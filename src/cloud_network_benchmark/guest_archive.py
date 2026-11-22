"""Closed guest-side archive creation entry point for bounded retrieval.

This module intentionally has no provider or credential integration. The
controller invokes it over the already established SSH connection after both
measurement producers are closed; originals remain untouched.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import shutil
import sys
from typing import Any, Mapping, Sequence

from .closed_archive import create_closed_archive, projected_archive_upper_bound, _safe_files


def create_guest_archive(source: Path, destination: Path, *, maximum_part_bytes: int,
        maximum_archive_bytes: int, minimum_free_bytes: int,
        expected_inventory: Sequence[Mapping[str, Any]] | None = None) -> dict:
    source, destination = Path(source), Path(destination)
    if source.is_symlink() or not source.is_dir():
        raise ValueError("closed guest source must be a real directory")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("guest archive destination must be new")
    source_real = source.resolve(strict=True)
    destination_real = destination.absolute()
    if source_real == destination_real or source_real in destination_real.parents:
        raise ValueError("guest archive must not be created inside the native source tree")
    rows = _safe_files(source_real)
    source_by_name = {row[0]: row for row in rows}
    expected = ([{"path": row[0], "size_bytes": row[2], "sha256": row[3]} for row in rows]
        if expected_inventory is None else list(expected_inventory))
    if (not expected or any(not isinstance(row, Mapping)
            or set(row) != {"path", "size_bytes", "sha256"} for row in expected)
            or len({row["path"] for row in expected}) != len(expected)):
        raise ValueError("closed native inventory must be a nonempty unique file list")
    selected = []
    for row in sorted(expected, key=lambda value: value["path"]):
        actual = source_by_name.get(row["path"])
        if actual is None or {"path": actual[0], "size_bytes": actual[2], "sha256": actual[3]} != dict(row):
            raise ValueError("selected guest source file differs from the frozen closed inventory")
        selected.append(dict(row))
    if projected_archive_upper_bound(sum(row["size_bytes"] for row in selected), len(selected)) > maximum_archive_bytes:
        raise ValueError("guest archive upper bound exceeds its configured maximum byte bound")
    manifest = create_closed_archive(source, destination,
        max_part_bytes=maximum_part_bytes,
        free_bytes=lambda path: shutil.disk_usage(path).free,
        minimum_headroom_bytes=minimum_free_bytes,
        minimum_source_headroom_bytes=minimum_free_bytes,
        include_files=[row["path"] for row in selected])
    current = {row[0]: {"path": row[0], "size_bytes": row[2], "sha256": row[3]}
        for row in _safe_files(source_real)}
    if [current[name] for name in sorted(current) if name in {row["path"] for row in selected}] != selected:
        raise ValueError("selected guest source inventory changed during archive creation")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--maximum-part-bytes", required=True, type=int)
    parser.add_argument("--maximum-archive-bytes", required=True, type=int)
    parser.add_argument("--minimum-free-bytes", required=True, type=int)
    parser.add_argument("--inventory", required=True, type=Path)
    args = parser.parse_args(argv)
    manifest = create_guest_archive(args.source, args.destination,
        maximum_part_bytes=args.maximum_part_bytes,
        maximum_archive_bytes=args.maximum_archive_bytes,
        minimum_free_bytes=args.minimum_free_bytes,
        expected_inventory=json.loads(args.inventory.read_text(encoding="utf-8")))
    print(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    return 0


def create_remote_guest_archive(connection: Any, *, role: str, source_directory: str,
        archive_directory: str, expected_inventory: Sequence[Mapping[str, Any]],
        maximum_part_bytes: int, maximum_archive_bytes: int,
        minimum_free_bytes: int, runner: Any, deadline: Any,
        clock: Any = __import__("time").monotonic) -> dict:
    """Create and verify one deterministic archive on a closed measurement VM."""
    from .access import _action, _execute, _request
    from .contracts.common import CommandOutcome
    from pathlib import PurePosixPath

    if role not in {"vm_a", "vm_b"}:
        raise ValueError("guest archive role must identify VM A or VM B")
    for value in (source_directory, archive_directory):
        path = PurePosixPath(value)
        if not value.startswith("/") or path.as_posix() != value or ".." in path.parts:
            raise ValueError("guest archive paths must be normalized absolute POSIX paths")
    if (PurePosixPath(archive_directory) == PurePosixPath(source_directory)
            or PurePosixPath(source_directory) in PurePosixPath(archive_directory).parents):
        raise ValueError("guest archive output must be outside the native source tree")
    def transfer_timeout(command_seconds: float) -> float:
        remaining_now = deadline.remaining(clock, cleanup=True)
        if remaining_now <= 0:
            raise TimeoutError("archive work reached the original deadline or cleanup reserve")
        return min(command_seconds, remaining_now)
    vm = connection.vm_a if role == "vm_a" else connection.vm_b
    # Measurement guests do not have the application installed. Deliver only
    # the two stdlib-only source modules needed for compression, bound to this
    # executing checkout; never install a package or resolve dependencies.
    import tempfile
    from pathlib import Path
    package_root = Path(__file__).resolve().parent
    helper_root = f"/tmp/cnb-guest-archive-{connection.deployment_id}-{role}"
    helper_package = helper_root + "/cloud_network_benchmark"
    archive_parent = str(PurePosixPath(archive_directory).parent)
    mkdir = _request(f"f09-guest-archive-mkdir-{connection.deployment_id}-{role}", vm,
        ["sh", "-c", "test ! -e " + shlex.quote(helper_root) + " && test ! -e "
         + shlex.quote(archive_directory) + " && mkdir -p " + shlex.quote(helper_package)
         + " " + shlex.quote(archive_parent)],
        transfer_timeout(connection.timeouts.command_seconds))
    mkdir_result = _execute(runner, mkdir)
    if mkdir_result.outcome != CommandOutcome.SUCCEEDED:
        raise RuntimeError(f"guest archive helper directory unavailable for {role}")
    actions = [_action(mkdir.action_id, mkdir.action_type, vm, mkdir, mkdir_result)]
    with tempfile.TemporaryDirectory(prefix="cnb-guest-archive-manifest-") as temp:
        inventory_path = Path(temp) / "closed-inventory.json"
        inventory_path.write_text(json.dumps(list(expected_inventory), sort_keys=True,
            separators=(",", ":")) + "\n", encoding="utf-8")
        request = _request(f"f09-guest-archive-inventory-{connection.deployment_id}-{role}", vm,
            [str(inventory_path), f"{vm.user}@{vm.host}:{helper_root}/closed-inventory.json"],
            transfer_timeout(connection.timeouts.transfer_seconds), "scp")
        result = _execute(runner, request)
        actions.append(_action(request.action_id, request.action_type, vm, request, result))
        if result.outcome != CommandOutcome.SUCCEEDED:
            raise RuntimeError(f"closed inventory delivery failed for {role}")
    for name in ("closed_archive.py", "guest_archive.py"):
        source = package_root / name
        request = _request(f"f09-guest-archive-helper-{connection.deployment_id}-{role}-{name}", vm,
            [str(source), f"{vm.user}@{vm.host}:{helper_package}/{name}"],
            transfer_timeout(connection.timeouts.transfer_seconds), "scp")
        result = _execute(runner, request)
        actions.append(_action(request.action_id, request.action_type, vm, request, result))
        if result.outcome != CommandOutcome.SUCCEEDED:
            raise RuntimeError(f"guest archive helper delivery failed for {role}")
    argv = ["env", "PYTHONPATH=" + helper_root, "python3", "-m", "cloud_network_benchmark.guest_archive",
        "--source", source_directory, "--destination", archive_directory,
        "--maximum-part-bytes", str(maximum_part_bytes),
        "--maximum-archive-bytes", str(maximum_archive_bytes),
        "--minimum-free-bytes", str(minimum_free_bytes),
        "--inventory", helper_root + "/closed-inventory.json"]
    request = _request(f"f09-guest-archive-{connection.deployment_id}-{role}", vm,
        ["sh", "-c", " ".join(shlex.quote(value) for value in argv)],
        transfer_timeout(connection.timeouts.transfer_seconds), "ssh")
    result = _execute(runner, request)
    action = _action(request.action_id, request.action_type, vm, request, result)
    if result.outcome != CommandOutcome.SUCCEEDED:
        raise RuntimeError(f"guest archive creation failed for {role}")
    try:
        manifest = json.loads(result.stdout.strip().splitlines()[-1])
        files = manifest["files"]
        parts = manifest["parts"]
        normalized_inventory = sorted((dict(row) for row in expected_inventory), key=lambda row: row["path"])
        if files != normalized_inventory or not isinstance(parts, list) or not parts:
            raise ValueError("guest archive manifest differs from the closed source inventory")
        if any(type(row.get("size_bytes")) is not int or not 0 < row["size_bytes"] <= maximum_part_bytes
               for row in parts):
            raise ValueError("guest archive contains a part above the transfer bound")
        return {"role": role, "source_directory": source_directory,
            "archive_directory": archive_directory, "manifest": manifest,
            "actions": [*([item.model_dump(mode="json") for item in actions]), action.model_dump(mode="json")]}
    except (IndexError, KeyError, TypeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"guest archive result is invalid for {role}: {type(exc).__name__}") from exc


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(f"guest archive failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(2)
