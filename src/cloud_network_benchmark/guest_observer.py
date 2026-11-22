"""Bounded read-only guest health probes for an admitted binding snapshot.

The lifecycle publisher must bind this JSON to a prepared stage by SHA-256.
No host or process identities are guessed here.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping


_REMOTE = r'''import json, os, pathlib, sys, time
p=json.loads(sys.argv[1]); root=pathlib.Path(p["workspace"]); root.is_dir() or sys.exit(20)
proc=pathlib.Path(p.get("proc_root", "/proc"))
boot=(proc/"sys/kernel/random/boot_id").read_text().strip(); out={"boot_id":boot,"processes":{},"files":{}}
valid={"R","S","D","W","X","x","K","P","I"}
for x in p["processes"]:
 s=(proc/str(x["pid"])/"stat").read_text(); f=s[s.rfind(")")+2:].split(); state=f[0]; ticks=int(f[19])
 hz=os.sysconf("SC_CLK_TCK"); age=max(0.0,float((proc/"uptime").read_text().split()[0])-ticks/hz)
 out["processes"][x["kind"]]={"pid":x["pid"],"boot_id":boot,"start_ticks":ticks,"state":state,"age_seconds":age,"age_resolution_seconds":1.0/hz,"alive":state in valid and state not in {"Z","X","x"}}
for x in p["files"]:
 q=(root/x["path"]).resolve(strict=False); q.is_relative_to(root.resolve(strict=True)) or sys.exit(21)
 if not q.exists() and x.get("output_state")=="finalization_pending": out["files"][x["path"]]={"exists":False,"kind":x["kind"]}; continue
 st=q.stat(); out["files"][x["path"]]={"exists":True,"size":st.st_size,"mtime":st.st_mtime,"kind":x["kind"]}
print(json.dumps(out,sort_keys=True))'''

_LIVE_PROC_STATES = {"R", "S", "D", "W", "K", "P", "I"}


def _safe_rel(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and path.as_posix() == value and all(part not in {"", ".", ".."} for part in path.parts)


def _validate_role(role: Mapping[str, Any], workspace: str, role_name: str,
                   owned_role: Mapping[str, Any] | None) -> list[str]:
    errors = []
    if role.get("workspace") != workspace:
        errors.append("workspace_mismatch")
    connection = role.get("connection")
    if not isinstance(role.get("vm_id"), str) or not role["vm_id"] or owned_role is None or role.get("vm_id") != owned_role.get("vm_id"):
        errors.append("vm_identity_unowned")
    if not isinstance(connection, dict) or owned_role is None or connection != owned_role.get("connection"):
        errors.append("connection_receipt_mismatch")
    else:
        host, user = connection.get("host"), connection.get("user")
        port = connection.get("port")
        known_hosts, identity_file = connection.get("known_hosts"), connection.get("identity_file")
        if (not isinstance(host, str) or not host or host.startswith("-")
                or not re.fullmatch(r"[A-Za-z0-9.-]+", host)
                or not isinstance(user, str) or user.startswith("-") or not re.fullmatch(r"[A-Za-z0-9._-]+", user)
                or type(port) is not int or not 1 <= port <= 65535
                or not isinstance(known_hosts, str) or not Path(known_hosts).is_absolute()
                or not isinstance(identity_file, str) or not Path(identity_file).is_absolute()
                or any(c.isspace() for c in (known_hosts or "") + (identity_file or ""))):
            errors.append("connection_binding_invalid")
    procs = role.get("processes")
    expected = {"producer", "diagnostics"} if role_name == "vm_a" else {"receiver", "diagnostics"}
    kinds = {x.get("kind") for x in procs if isinstance(x, dict)} if isinstance(procs, list) else set()
    allowed = (kinds in (expected, expected | {"tcp_client"}) if role_name == "vm_a" else kinds == expected)
    if not isinstance(procs, list) or not allowed or len(procs) != len(kinds):
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
    if role_name == "vm_a" and "tcp_client" in kinds:
        required_file_kinds.add("iperf3_client")
    if not isinstance(files, list) or not required_file_kinds <= {
            x.get("kind") for x in files if isinstance(x, dict)}:
        errors.append("output_bindings_incomplete")
    else:
        if (any(not isinstance(x, dict) or not _safe_rel(x.get("path")) for x in files)
                or len({x.get("path") for x in files}) != len(files)):
            errors.append("output_path_invalid")
        if role_name == "vm_a":
            chunks = [x for x in files if x.get("kind") == "sockperf_chunk"]
            if not chunks or any(x.get("state") not in {"current", "completed"} for x in chunks):
                errors.append("sockperf_chunk_state_invalid")
            if not any(x.get("state") == "current" for x in chunks):
                errors.append("current_sockperf_chunk_missing")
    return errors


def _ssh_probe(role: Mapping[str, Any], payload: dict, *, timeout: float,
               run: Callable[..., Any] = subprocess.run) -> dict:
    connection = role["connection"]
    remote = " ".join(("python3", "-c", shlex.quote(_REMOTE), shlex.quote(json.dumps(payload, separators=(",", ":")))))
    target = f"{connection['user']}@{connection['host']}"
    result = run(["ssh", "-i", connection["identity_file"], "-p", str(connection["port"]),
                  "-oBatchMode=yes", "-oStrictHostKeyChecking=yes", "-oIdentitiesOnly=yes",
                  "-oUserKnownHostsFile=" + connection["known_hosts"], "-oConnectTimeout=5",
                  target, remote], check=False, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("guest_probe_unreachable")
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise ValueError("guest_probe_malformed")
    return value


def probe_role(role: Mapping[str, Any], workspace: str, role_name: str, *, now: float,
               run: Callable[..., Any] = subprocess.run, probe: Callable[..., dict] | None = None,
               freshness_seconds: float = 120, owned_role: Mapping[str, Any] | None = None,
               timeout: float = 12, clock: Callable[[], float] | None = None) -> dict:
    if not all(math.isfinite(value) for value in (now, freshness_seconds, timeout)) or freshness_seconds <= 0 or timeout <= 0:
        return {"health": "unknown", "errors": ["observer_clock_or_probe_budget_invalid"]}
    errors = _validate_role(role, workspace, role_name, owned_role)
    if errors:
        return {"health": "unknown", "errors": sorted(set(errors))}
    payload = {"workspace": workspace, "processes": role["processes"], "files": role["files"]}
    try:
        result = (probe or (lambda r, p: _ssh_probe(r, p, timeout=timeout, run=run)))(role, payload)
    except Exception as exc:
        return {"health": "fault", "errors": ["guest_probe_unreachable" if isinstance(exc, (OSError, subprocess.SubprocessError, RuntimeError)) else "guest_probe_invalid"]}
    if not isinstance(result, Mapping):
        return {"health": "fault", "errors": ["guest_probe_malformed"]}
    check_now = clock() if clock is not None else now
    if not math.isfinite(check_now):
        return {"health": "unknown", "errors": ["observer_clock_invalid"]}
    problems = []
    for expected in role["processes"]:
        actual = result.get("processes", {}).get(expected["kind"], {})
        if (not isinstance(actual, Mapping) or actual.get("alive") is not True or actual.get("pid") != expected["pid"]
                or actual.get("state") not in _LIVE_PROC_STATES
                or actual.get("boot_id") != expected["boot_id"]
                or actual.get("start_ticks") != expected["start_ticks"]):
            problems.append("process_identity_mismatch:" + expected["kind"])
        if expected.get("kind") in {"producer", "tcp_client"} and "timeout_seconds" in expected:
            age = actual.get("age_seconds") if isinstance(actual, Mapping) else None
            resolution = actual.get("age_resolution_seconds") if isinstance(actual, Mapping) else None
            requested = expected.get("requested_seconds")
            grace = expected.get("finalization_grace_seconds")
            maximum_age = min(expected["timeout_seconds"], requested + grace
                if type(requested) is int and isinstance(grace, (int, float)) and not isinstance(grace, bool)
                and math.isfinite(grace) and grace > 0 else -1)
            if (isinstance(age, bool) or not isinstance(age, (int, float)) or not math.isfinite(age)
                    or isinstance(resolution, bool) or not isinstance(resolution, (int, float))
                    or not math.isfinite(resolution) or resolution < 0 or resolution > 0.1
                    or age < 0 or maximum_age <= 0 or age > maximum_age + resolution):
                problems.append("measurement_process_exceeded_original_timeout:" + expected["kind"])
    observed_files = result.get("files", {})
    for item in role["files"]:
        value = observed_files.get(item["path"])
        pending = item.get("state") == "current" and item.get("output_state") == "finalization_pending"
        if pending:
            process_kind = "producer" if item["kind"] == "sockperf_chunk" else "tcp_client"
            process = next((row for row in role["processes"] if row["kind"] == process_kind), None)
            if process is None or process.get("path") != item["path"]:
                problems.append("pending_output_has_no_matching_live_process:" + item["kind"])
            if not isinstance(value, Mapping) or value.get("exists") not in {True, False}:
                problems.append("pending_output_probe_invalid:" + item["kind"])
            continue
        if not isinstance(value, dict) or type(value.get("size")) is not int or value["size"] <= 0:
            problems.append("output_missing_or_empty:" + item["kind"])
            continue
        try:
            mtime = float(value["mtime"])
            if not math.isfinite(mtime):
                raise ValueError
            if mtime > check_now + 5:
                problems.append("output_time_in_future:" + item["kind"])
            elif item.get("state") != "completed" and check_now - mtime > freshness_seconds:
                problems.append("output_stale:" + item["kind"])
        except (KeyError, TypeError, ValueError, OverflowError):
            problems.append("output_timestamp_invalid:" + item["kind"])
    if role_name == "vm_a":
        current_chunks = [x for x in role["files"] if x["kind"] == "sockperf_chunk" and x["state"] == "current"]
        if not current_chunks:
            problems.append("current_sockperf_chunk_missing")
    pending_outputs = [item["kind"] for item in role["files"]
        if item.get("state") == "current" and item.get("output_state") == "finalization_pending"]
    return {"health": "fault" if problems else "healthy", "errors": sorted(set(problems)),
            "outputs_pending_finalization": pending_outputs}


def observe_bindings(stage: Mapping[str, Any], bindings_path: Path, *,
                     run: Callable[..., Any] = subprocess.run, now: float | None = None,
                     probe: Callable[..., dict] | None = None,
                     active_entry_ids: set[str] | None = None,
                     probe_budget_seconds: float = 20,
                     monotonic: Callable[[], float] = time.monotonic,
                     clock: Callable[[], float] | None = None) -> dict:
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
        observed_clock = clock or (time.time if now is None else lambda: now)
        at = observed_clock()
        if not math.isfinite(at) or not math.isfinite(probe_budget_seconds) or probe_budget_seconds <= 0:
            return {"health": "unknown", "errors": ["observer_clock_or_budget_invalid"]}
        prepared_rows = stage.get("entries", [])
        prepared = {item["entry_id"]: item for item in prepared_rows}
        if len(prepared) != len(prepared_rows):
            return {"health": "unknown", "errors": ["duplicate_prepared_entry"]}
        results = {}
        binding_entries = bindings.get("entries", [])
        if not isinstance(binding_entries, list):
            return {"health": "fault", "errors": ["guest_bindings_invalid"]}
        expected = set()
        active_sources = [source for entry_id, source in prepared.items()
                          if active_entry_ids is None or entry_id in active_entry_ids]
        for source in active_sources:
            for provider, run_id in source.get("run_ids", {}).items():
                expected.add((source["entry_id"], provider, run_id))
        actual = []
        for item in binding_entries:
            if not isinstance(item, dict):
                return {"health": "fault", "errors": ["guest_bindings_invalid"]}
            actual.append((item.get("entry_id"), item.get("provider"), item.get("run_id")))
        if len(actual) != len(set(actual)):
            return {"health": "fault", "errors": ["duplicate_guest_participant"]}
        if set(actual) != expected:
            return {"health": "unknown", "errors": ["active_guest_binding_set_mismatch"]}
        launch_path = Path(stage["observer_launch_receipt_path"])
        launch_raw = launch_path.read_bytes()
        if hashlib.sha256(launch_raw).hexdigest() != stage["observer_launch_receipt_sha256"]:
            return {"health": "unknown", "errors": ["launch_receipt_hash_mismatch"]}
        launch_receipt = json.loads(launch_raw)
        if launch_receipt.get("stage_id") != stage.get("stage_id"):
            return {"health": "unknown", "errors": ["launch_receipt_stage_mismatch"]}
        owned = launch_receipt.get("participants", {})
        expected_owned = {json.dumps(item, separators=(",", ":")) for item in expected}
        if not isinstance(owned, dict) or set(owned) != expected_owned:
            return {"health": "unknown", "errors": ["owned_launch_participant_set_mismatch"]}
        deadline = monotonic() + probe_budget_seconds
        for entry in binding_entries:
            entry_id = entry.get("entry_id")
            source = prepared.get(entry_id)
            provider, run_id = entry["provider"], source["run_ids"][entry["provider"]]
            result_key = json.dumps([entry_id, provider, run_id], separators=(",", ":"))
            runtime = json.loads(Path(source["runtime_config_path"]).read_text(encoding="utf-8"))
            workspace = runtime["access"]["bootstrap"]["workspace"]
            if (entry.get("run_id") != run_id or entry.get("deployment_id") != run_id + "-deployment"
                    or set(entry.get("roles", {})) != {"vm_a", "vm_b"}):
                results[result_key] = {"health": "fault", "errors": ["admitted_run_mismatch"]}
                continue
            participant_key = json.dumps([entry_id, provider, run_id], separators=(",", ":"))
            owned_participant = owned.get(participant_key, {})
            role_results = {}
            for role, binding in entry["roles"].items():
                remaining = deadline - monotonic()
                if remaining <= 0:
                    role_results[role] = {"health": "fault", "errors": ["guest_probe_budget_exhausted"]}
                else:
                    role_results[role] = probe_role(binding, workspace, role, now=at, run=run, probe=probe,
                        owned_role=owned_participant.get(role), timeout=min(12.0, remaining), clock=observed_clock)
            results[result_key] = {"provider": provider, "run_id": run_id, "deployment_id": entry["deployment_id"],
                                      "roles": role_results,
                                      "health": "healthy" if all(x["health"] == "healthy" for x in role_results.values()) else "fault"}
        return {"health": "healthy" if results and all(x["health"] == "healthy" for x in results.values()) else "fault",
                "entries": results, "observed_at": datetime.now(timezone.utc).isoformat()}
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return {"health": "unknown", "errors": ["guest_bindings_unavailable_or_invalid"]}
