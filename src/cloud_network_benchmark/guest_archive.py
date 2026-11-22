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
import sys
from typing import Any, Mapping, Sequence

from .closed_archive import create_closed_archive, projected_archive_upper_bound, _safe_files


def create_guest_archive(source: Path, destination: Path, *, maximum_part_bytes: int,
        maximum_archive_bytes: int, minimum_free_bytes: int) -> dict:
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
    if projected_archive_upper_bound(sum(row[2] for row in rows), len(rows)) > maximum_archive_bytes:
        raise ValueError("guest archive upper bound exceeds its configured maximum byte bound")
    manifest = create_closed_archive(source, destination,
        max_part_bytes=maximum_part_bytes, minimum_headroom_bytes=minimum_free_bytes)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--maximum-part-bytes", required=True, type=int)
    parser.add_argument("--maximum-archive-bytes", required=True, type=int)
    parser.add_argument("--minimum-free-bytes", required=True, type=int)
    args = parser.parse_args(argv)
    manifest = create_guest_archive(args.source, args.destination,
        maximum_part_bytes=args.maximum_part_bytes,
        maximum_archive_bytes=args.maximum_archive_bytes,
        minimum_free_bytes=args.minimum_free_bytes)
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
    remaining = deadline.remaining(clock, cleanup=True)
    if remaining <= 0:
        raise TimeoutError("archive creation reached original deadline or cleanup reserve")
    vm = connection.vm_a if role == "vm_a" else connection.vm_b
    argv = ["python3", "-m", "cloud_network_benchmark.guest_archive",
        "--source", source_directory, "--destination", archive_directory,
        "--maximum-part-bytes", str(maximum_part_bytes),
        "--maximum-archive-bytes", str(maximum_archive_bytes),
        "--minimum-free-bytes", str(minimum_free_bytes)]
    request = _request(f"f09-guest-archive-{connection.deployment_id}-{role}", vm,
        ["sh", "-c", " ".join(shlex.quote(value) for value in argv)],
        min(connection.timeouts.transfer_seconds, remaining), "ssh")
    result = _execute(runner, request)
    action = _action(request.action_id, request.action_type, vm, request, result)
    if result.outcome != CommandOutcome.SUCCEEDED:
        raise RuntimeError(f"guest archive creation failed for {role}")
    try:
        manifest = json.loads(result.stdout.strip().splitlines()[-1])
        files = manifest["files"]
        parts = manifest["parts"]
        if files != list(expected_inventory) or not isinstance(parts, list) or not parts:
            raise ValueError("guest archive manifest differs from the closed source inventory")
        if any(type(row.get("size_bytes")) is not int or not 0 < row["size_bytes"] <= maximum_part_bytes
               for row in parts):
            raise ValueError("guest archive contains a part above the transfer bound")
        return {"role": role, "source_directory": source_directory,
            "archive_directory": archive_directory, "manifest": manifest,
            "action": action.model_dump(mode="json")}
    except (IndexError, KeyError, TypeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"guest archive result is invalid for {role}: {type(exc).__name__}") from exc


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(f"guest archive failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(2)
