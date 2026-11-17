#!/usr/bin/env python3
"""Run the production pinned Sockperf/iperf3 source recipes in a temporary prefix.

This is a native-host diagnostic, not proof of a clean Ubuntu VM build. It deliberately
skips package installation and requires an explicit --prepared-host acknowledgement.
All command output and a hash manifest stay in the selected evidence directory.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import shutil
import subprocess
import sys
import tempfile
import time

from cloud_network_benchmark.bootstrap import (
    BootstrapConfig, SourceBuild, _identity_preflight_script, _parse_build_identity,
    _recipe, _required_capabilities, _sha256sum_output, _capabilities_observed,
)

SOURCES = (
    dict(tool="sockperf", source_url="https://codeload.github.com/Mellanox/sockperf/tar.gz/5ebd327da983225321818c0355db922515e026bd",
         source_reference="sockperf-5ebd327da983225321818c0355db922515e026bd", archive_name="sockperf.tar.gz",
         source_sha256="a35a1daf310551b9f675af4fb7d95a6a10389fda4c7605d70a21554be0773f8e",
         upstream_commit="5ebd327da983225321818c0355db922515e026bd",
         observation_patch_path="tool_patches/sockperf-3.10-acquisition.patch",
         observation_patch_sha256="bfee91183037577a0861c24c27dd9c644134a644ac2edead0ab460f4875ac5fc"),
    dict(tool="iperf3", source_url="https://codeload.github.com/esnet/iperf/tar.gz/2acfcfe94e928e74542c9f107e02aa6dd4748a79",
         source_reference="iperf-2acfcfe94e928e74542c9f107e02aa6dd4748a79", archive_name="iperf3.tar.gz",
         source_sha256="d6b4d2c7b20157c5b7fbd2de66f22bd3428958b5f2e28899151b6e22745886fe",
         upstream_commit="2acfcfe94e928e74542c9f107e02aa6dd4748a79",
         observation_patch_path="tool_patches/iperf-3.17.1-acquisition.patch",
         observation_patch_sha256="11b96daadb4a1f17dc24376bb3d0d7feec330efeedc550b1979b1566e9b81661"),
)
REQUIRED = ("cc", "make", "autoconf", "automake", "libtoolize", "pkg-config", "curl", "sha256sum",
            "tar", "patch", "base64", "grep", "cmp")
COMMAND_TIMEOUT_SECONDS = 300


def build_config(root: Path) -> BootstrapConfig:
    workspace, prefix = root / "work", root / "install"
    return BootstrapConfig(sources=[SourceBuild(**item) for item in SOURCES], remote_root=str(root),
        workspace=str(workspace), results_workspace=str(root / "results"), install_prefix=str(prefix))


def workspace_context(requested: Path | None, parser: argparse.ArgumentParser):
    if requested is None:
        return tempfile.TemporaryDirectory(prefix="cnb-source-build-")
    temporary_root = Path(tempfile.gettempdir()).resolve()
    if not requested.is_absolute() or requested.exists() or requested.is_symlink():
        parser.error("--workspace must be an absolute path to a new directory")
    try:
        parent = requested.parent.resolve(strict=True)
        if not parent.is_relative_to(temporary_root) or requested != parent / requested.name:
            raise ValueError
    except (OSError, ValueError):
        parser.error("--workspace must be a normalized path beneath the system temporary directory")
    retained = parent / requested.name
    retained.mkdir(mode=0o700)
    return nullcontext(str(retained))


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run_logged(argv: list[str], cwd: Path, env: dict[str, str], stdout_path: Path, stderr_path: Path):
    started = time.monotonic()
    timed_out = False
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            return_code = proc.wait(timeout=COMMAND_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                return_code = proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                return_code = proc.wait()
    return return_code, timed_out, time.monotonic() - started


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared-host", action="store_true", help="acknowledge dependency installation is intentionally skipped")
    parser.add_argument("--evidence-dir", type=Path, required=True, help="new or empty local directory for full logs and evidence")
    parser.add_argument("--source-cache", type=Path, help="optional directory containing previously hash-verified pinned archives")
    parser.add_argument("--workspace", type=Path,
        help="new private directory under the system temporary directory; retain exact source and binaries for bounded loopback checks")
    args = parser.parse_args()
    if not args.prepared_host:
        parser.error("pass --prepared-host only after confirming this host already has the build dependencies")
    evidence = args.evidence_dir.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    if any(evidence.iterdir()):
        parser.error("evidence directory must be empty")
    missing = [name for name in REQUIRED if shutil.which(name) is None]
    release = Path("/etc/os-release").read_text(errors="replace") if Path("/etc/os-release").is_file() else "unavailable"
    summary = {"schema_version": 1, "host": {"system": platform.platform(), "machine": platform.machine(),
        "os_release": release,
        "python": sys.version, "compiler": subprocess.run(["cc", "--version"], text=True, capture_output=True).stdout.splitlines()[0]
        if shutil.which("cc") else None}, "required_commands": {name: shutil.which(name) for name in REQUIRED},
        "missing_commands": missing, "tools": []}
    if missing:
        (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(f"missing required commands: {', '.join(missing)}", file=sys.stderr)
        return 2
    with workspace_context(args.workspace, parser) as temp:
        root = Path(temp)
        config = build_config(root)
        Path(config.workspace).mkdir(); Path(config.install_prefix).mkdir()
        env = {"PATH": "/usr/bin:/bin", "HOME": str(root), "LC_ALL": "C", "LANG": "C"}
        for source in config.sources:
            row = {"tool": source.tool, "source_url": source.source_url, "source_reference": source.source_reference,
                "source_sha256_expected": source.source_sha256, "patch_path": source.observation_patch_path,
                "patch_sha256_expected": source.observation_patch_sha256, "commands": []}
            commands = _recipe(source, config)
            cached_archive = (args.source_cache / source.archive_name) if args.source_cache else None
            use_cache = cached_archive is not None and cached_archive.is_file() and not cached_archive.is_symlink()
            if use_cache:
                observed_cache_sha = digest(cached_archive)
                if observed_cache_sha != source.source_sha256:
                    row.update(status="failed", failed_command="source_cache_digest_mismatch",
                        source_cache_sha256_observed=observed_cache_sha)
                    summary["tools"].append(row)
                    continue
                shutil.copyfile(cached_archive, workspace / source.archive_name)
                row["source_cache_used"] = True
            # Recipe[0] is the production apt install action, explicitly omitted on this host.
            for index, argv in enumerate(commands[1:], start=1):
                label = {1: "download", 2: "archive_sha256", 3: "extract", 4: "production_source_build_recipe",
                         5: "compiler_version", 6: "tool_version", 7: "binary_sha256", 8: "tool_help"}.get(index, f"production_check_{index}")
                out_path, err_path = evidence / f"{source.tool}-{index:02d}-{label}.stdout", evidence / f"{source.tool}-{index:02d}-{label}.stderr"
                if use_cache and index == 1:
                    out_path.write_text("verified pinned source archive copied from local cache\n")
                    err_path.write_text("")
                    return_code, timed_out, elapsed = 0, False, 0.0
                else:
                    return_code, timed_out, elapsed = run_logged(argv, root, env, out_path, err_path)
                out, err = out_path.read_text(errors="replace"), err_path.read_text(errors="replace")
                record = {"label": label, "operation": "verified_cache_copy" if use_cache and index == 1 else "command",
                    "argv": None if use_cache and index == 1 else argv, "exit_code": return_code,
                    "timed_out": timed_out, "timeout_seconds": COMMAND_TIMEOUT_SECONDS,
                    "duration_seconds": round(elapsed, 3),
                    "stdout_path": out_path.name, "stdout_sha256": digest(out_path), "stdout_bytes": out_path.stat().st_size,
                    "stderr_path": err_path.name, "stderr_sha256": digest(err_path), "stderr_bytes": err_path.stat().st_size}
                row["commands"].append(record)
                if return_code:
                    row.update(status="failed", failed_command=label,
                        failure="command_timed_out" if timed_out else "command_failed")
                    break
                if index == 2:
                    observed = _sha256sum_output(out, str(workspace / source.archive_name))
                    if observed != source.source_sha256:
                        row.update(status="failed", failed_command="archive_digest_mismatch", observed_source_sha256=observed)
                        break
                    row["source_sha256_observed"] = observed
                if index == 5:
                    row["compiler_version"] = out.strip()
                if index == 6:
                    row["binary_version"] = out.strip()
                if index == 7:
                    row["binary_sha256"] = _sha256sum_output(out, str(prefix / "bin" / source.tool))
                    if not row["binary_sha256"]:
                        row.update(status="failed", failed_command="binary_digest_missing")
                        break
                if argv[:2] == ["python3", "-c"] and out.strip() != "CNB_TCP_CONGESTION=cubic":
                    row.update(status="failed", failed_command="tcp_congestion_control_unavailable")
                    break
                help_required = None
                if source.tool == "sockperf" and index == 9:
                    help_required = ["server", "-i", "-p"]
                elif source.tool == "sockperf" and index == 10:
                    help_required = ["under-load", "-i", "-p", "-m", "--mps", "--reply-every",
                        "--full-rtt", "--no-rdtsc", "--full-log", "-t"]
                elif source.tool == "iperf3" and index == 8:
                    help_required = _required_capabilities(source.tool)
                elif source.tool == "sockperf" and index == 8:
                    help_required = []
                if help_required is not None and not _capabilities_observed(out, source.tool, required=help_required):
                    row.update(status="failed", failed_command=f"capability_check_{index}")
                    break
            if row.get("status") == "failed":
                summary["tools"].append(row)
                continue
            try:
                identity_action = next(item for item in row["commands"]
                    if item["label"].startswith("production_check_") and
                    "CNB_BUILD_V1" in (evidence / item["stdout_path"]).read_text(errors="replace"))
                identity = _parse_build_identity((evidence / identity_action["stdout_path"]).read_text(errors="replace"), source)
                row["build_identity"] = identity
                argv = ["sh", "-c", _identity_preflight_script(source, config)]
                out_path, err_path = evidence / f"{source.tool}-identity-preflight.stdout", evidence / f"{source.tool}-identity-preflight.stderr"
                return_code, timed_out, elapsed = run_logged(argv, root, env, out_path, err_path)
                out, err = out_path.read_text(errors="replace"), err_path.read_text(errors="replace")
                row["commands"].append({"label": "identity_preflight", "argv": argv, "exit_code": return_code,
                    "timed_out": timed_out, "timeout_seconds": COMMAND_TIMEOUT_SECONDS,
                    "duration_seconds": round(elapsed, 3), "stdout_path": out_path.name,
                    "stdout_sha256": digest(out_path), "stdout_bytes": out_path.stat().st_size,
                    "stderr_path": err_path.name, "stderr_sha256": digest(err_path),
                    "stderr_bytes": err_path.stat().st_size})
                if return_code or out.strip() != "matching":
                    raise ValueError("production identity preflight failed" + (" by timeout" if timed_out else ""))
                row["identity_preflight"] = out.strip()
                row["status"] = "passed"
            except Exception as exc:
                row.update(status="failed", failed_command="identity_validation", error=str(exc))
            summary["tools"].append(row)
    summary["status"] = "passed" if len(summary["tools"]) == 2 and all(row["status"] == "passed" for row in summary["tools"]) else "failed"
    if args.workspace is not None:
        summary["retained_workspace"] = str(root)
    (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"status": summary["status"], "evidence_dir": str(evidence),
        "tools": [{"tool": row["tool"], "status": row["status"], "failed_command": row.get("failed_command")} for row in summary["tools"]]}, indent=2))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
