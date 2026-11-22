"""Bounded read-only guest health probes for an admitted binding snapshot.

The lifecycle publisher must bind this JSON to a prepared stage by SHA-256.
No host or process identities are guessed here.
"""
from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping


_REMOTE = r'''import json, os, pathlib, sys, time
p=json.loads(sys.argv[1]); root=pathlib.Path(p["workspace"]); root.is_dir() or sys.exit(20)
boot=pathlib.Path("/proc/sys/kernel/random/boot_id").read_text().strip(); out={"boot_id":boot,"processes":{},"files":{}}
for x in p["processes"]:
 s=pathlib.Path(f"/proc/{x['pid']}/stat").read_text(); f=s[s.rfind(")")+2:].split(); ticks=int(f[19])
 out["processes"][x["kind"]]={"pid":x["pid"],"boot_id":boot,"start_ticks":ticks,"alive":True}
for x in p["files"]:
 q=(root/x["path"]).resolve(strict=True); q.is_relative_to(root.resolve(strict=True)) or sys.exit(21)
 st=q.stat(); out["files"][x["path"]]={"size":st.st_size,"mtime":st.st_mtime,"kind":x["kind"]}
print(json.dumps(out,sort_keys=True))'''


def _safe_rel(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and path.as_posix() == value and all(part not in {"", ".", ".."} for part in path.parts)


def _validate_role(role: Mapping[str, Any], workspace: str, role_name: str) -> list[str]:
    errors = []
    if role.get("workspace") != workspace:
        errors.append("workspace_mismatch")
    if not isinstance(role.get("ssh_target"), str) or not role["ssh_target"] or any(c.isspace() for c in role["ssh_target"]):
        errors.append("ssh_target_missing")
    if not isinstance(role.get("vm_id"), str) or not role["vm_id"]:
        errors.append("vm_identity_missing")
    procs = role.get("processes")
    expected = {"producer", "diagnostics"} if role_name == "vm_a" else {"receiver", "diagnostics"}
    if not isinstance(procs, list) or {x.get("kind") for x in procs if isinstance(x, dict)} != expected:
        errors.append("process_bindings_incomplete")
    else:
        for item in procs:
            if (type(item.get("pid")) is not int or item["pid"] <= 1
                    or type(item.get("start_ticks")) is not int or item["start_ticks"] <= 0
                    or not isinstance(item.get("boot_id"), str) or not item["boot_id"]):
                errors.append("process_identity_incomplete")
    files = role.get("files")
    required_file_kinds = ({"sockperf_chunk", "diagnostics"} if role_name == "vm_a"
                           else {"iperf3_receiver", "diagnostics"})
    if not isinstance(files, list) or not required_file_kinds <= {
            x.get("kind") for x in files if isinstance(x, dict)}:
        errors.append("output_bindings_incomplete")
    else:
        if any(not _safe_rel(x.get("path")) for x in files):
            errors.append("output_path_invalid")
        if role_name == "vm_a":
            chunks = [x for x in files if x.get("kind") == "sockperf_chunk"]
            if not chunks or any(x.get("state") not in {"current", "completed"} for x in chunks):
                errors.append("sockperf_chunk_state_invalid")
            if not any(x.get("state") == "current" for x in chunks):
                errors.append("current_sockperf_chunk_missing")
    return errors


def _ssh_probe(role: Mapping[str, Any], payload: dict, *, run: Callable[..., Any] = subprocess.run) -> dict:
    remote = " ".join(("python3", "-c", shlex.quote(_REMOTE), shlex.quote(json.dumps(payload, separators=(",", ":")))))
    result = run(["ssh", "-oBatchMode=yes", "-oStrictHostKeyChecking=yes", "-oConnectTimeout=5",
                  role["ssh_target"], remote], check=False, capture_output=True, text=True, timeout=12)
    if result.returncode:
        raise RuntimeError("guest_probe_unreachable")
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise ValueError("guest_probe_malformed")
    return value


def probe_role(role: Mapping[str, Any], workspace: str, role_name: str, *, now: float,
               run: Callable[..., Any] = subprocess.run, probe: Callable[..., dict] | None = None,
               freshness_seconds: float = 120) -> dict:
    errors = _validate_role(role, workspace, role_name)
    if errors:
        return {"health": "unknown", "errors": sorted(set(errors))}
    payload = {"workspace": workspace, "processes": role["processes"], "files": role["files"]}
    try:
        result = (probe or (lambda r, p: _ssh_probe(r, p, run=run)))(role, payload)
    except Exception as exc:
        return {"health": "fault", "errors": ["guest_probe_unreachable" if isinstance(exc, (OSError, subprocess.SubprocessError, RuntimeError)) else "guest_probe_invalid"]}
    problems = []
    for expected in role["processes"]:
        actual = result.get("processes", {}).get(expected["kind"], {})
        if (actual.get("alive") is not True or actual.get("pid") != expected["pid"]
                or actual.get("boot_id") != expected["boot_id"]
                or actual.get("start_ticks") != expected["start_ticks"]):
            problems.append("process_identity_mismatch:" + expected["kind"])
    observed_files = result.get("files", {})
    for item in role["files"]:
        value = observed_files.get(item["path"])
        if not isinstance(value, dict) or type(value.get("size")) is not int or value["size"] <= 0:
            problems.append("output_missing_or_empty:" + item["kind"])
            continue
        if not (item["kind"] == "sockperf_chunk" and item.get("state") == "completed") and now - float(value.get("mtime", 0)) > freshness_seconds:
            problems.append("output_stale:" + item["kind"])
    if role_name == "vm_a":
        current_chunks = [x for x in role["files"] if x["kind"] == "sockperf_chunk" and x["state"] == "current"]
        if not any(isinstance(observed_files.get(x["path"]), dict)
                   and observed_files[x["path"]].get("size", 0) > 0
                   and now - float(observed_files[x["path"]].get("mtime", 0)) <= freshness_seconds
                   for x in current_chunks):
            problems.append("current_sockperf_chunk_not_fresh")
    return {"health": "fault" if problems else "healthy", "errors": sorted(set(problems))}


def observe_bindings(stage: Mapping[str, Any], bindings_path: Path, *,
                     run: Callable[..., Any] = subprocess.run, now: float | None = None,
                     probe: Callable[..., dict] | None = None,
                     active_entry_ids: set[str] | None = None) -> dict:
    """Verify the hash-pinned binding against prepared stage IDs, then probe all roles."""
    try:
        raw = Path(bindings_path).read_bytes()
        expected_sha = stage["observer_bindings_sha256"]
        if hashlib.sha256(raw).hexdigest() != expected_sha:
            return {"health": "fault", "errors": ["bindings_hash_mismatch"]}
        bindings = json.loads(raw)
        if (bindings.get("stage_id") != stage.get("stage_id")
                or bindings.get("campaign_id") != stage.get("campaign_id")):
            return {"health": "fault", "errors": ["stage_binding_mismatch"]}
        at = __import__("time").time() if now is None else now
        prepared = {item["entry_id"]: item for item in stage.get("entries", [])}
        results = {}
        binding_entries = bindings.get("entries", [])
        if not isinstance(binding_entries, list):
            return {"health": "fault", "errors": ["guest_bindings_invalid"]}
        if active_entry_ids is not None and {item.get("entry_id") for item in binding_entries} != active_entry_ids:
            return {"health": "unknown", "errors": ["active_guest_binding_set_mismatch"]}
        for entry in binding_entries:
            entry_id = entry.get("entry_id")
            source = prepared.get(entry_id)
            if source is None or entry.get("provider") not in source.get("run_ids", {}):
                results[str(entry_id)] = {"health": "fault", "errors": ["admitted_run_mismatch"]}
                continue
            provider, run_id = entry["provider"], source["run_ids"][entry["provider"]]
            runtime = json.loads(Path(source["runtime_config_path"]).read_text(encoding="utf-8"))
            workspace = runtime["access"]["bootstrap"]["workspace"]
            if (entry.get("run_id") != run_id or entry.get("deployment_id") != run_id + "-deployment"
                    or set(entry.get("roles", {})) != {"vm_a", "vm_b"}):
                results[str(entry_id)] = {"health": "fault", "errors": ["admitted_run_mismatch"]}
                continue
            role_results = {role: probe_role(binding, workspace, role, now=at, run=run, probe=probe)
                            for role, binding in entry["roles"].items()}
            results[str(entry_id)] = {"provider": provider, "run_id": run_id, "deployment_id": entry["deployment_id"],
                                      "roles": role_results,
                                      "health": "healthy" if all(x["health"] == "healthy" for x in role_results.values()) else "fault"}
        return {"health": "healthy" if results and all(x["health"] == "healthy" for x in results.values()) else "fault",
                "entries": results, "observed_at": datetime.now(timezone.utc).isoformat()}
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return {"health": "unknown", "errors": ["guest_bindings_unavailable_or_invalid"]}
