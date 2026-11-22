from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from cloud_network_benchmark.guest_observer import observe_bindings, probe_role


NOW = 1000.0
WORKSPACE = "/var/lib/cnb/work"


def role_binding(role: str) -> dict:
    process_kinds = ["producer", "diagnostics"] if role == "vm_a" else ["receiver", "diagnostics"]
    files = ([{"path": "measurements/probe-0001.csv", "kind": "sockperf_chunk", "state": "current"},
              {"path": "diagnostics/vm_a.jsonl", "kind": "diagnostics"}]
             if role == "vm_a" else
             [{"path": "iperf3/server.jsonl", "kind": "iperf3_receiver"},
              {"path": "diagnostics/vm_b.jsonl", "kind": "diagnostics"}])
    return {"vm_id": "vm-real-id-" + role,
            "connection": {"host": "192.0.2.10", "user": "bench", "port": 22,
                           "known_hosts": "/private/tmp/cnb-known-hosts",
                           "identity_file": "/private/tmp/cnb-observer-key"},
            "workspace": WORKSPACE,
            "processes": [{"kind": kind, "pid": i + 300, "boot_id": "boot-1", "start_ticks": i + 900}
                          for i, kind in enumerate(process_kinds)], "files": files}


def fresh_probe(_role, payload):
    return {"processes": {item["kind"]: {"alive": True, "pid": item["pid"],
                "boot_id": item["boot_id"], "start_ticks": item["start_ticks"], "state": "S"}
                for item in payload["processes"]},
            "files": {item["path"]: {"size": 10, "mtime": 990, "kind": item["kind"]}
                      for item in payload["files"]}}


def pinned_stage(tmp_path: Path, *, provider: str = "aws", run_id: str = "run-1"):
    runtime = tmp_path / "runtime.json"
    runtime.write_text(json.dumps({"access": {"bootstrap": {"workspace": WORKSPACE}}}))
    stage = {"stage_id": "stage-1", "campaign_id": "campaign-1", "entries": [
        {"entry_id": "entry-1", "run_ids": {provider: run_id}, "runtime_config_path": str(runtime)}]}
    bindings = {"stage_id": "stage-1", "campaign_id": "campaign-1", "entries": [{
        "entry_id": "entry-1", "provider": provider, "run_id": run_id,
        "deployment_id": run_id + "-deployment", "roles": {"vm_a": role_binding("vm_a"),
                                                                  "vm_b": role_binding("vm_b")}}]}
    participant_key = json.dumps(["entry-1", provider, run_id], separators=(",", ":"))
    receipt = {"stage_id": "stage-1", "participants": {participant_key: {
        role: {"vm_id": role_binding(role)["vm_id"], "connection": role_binding(role)["connection"]}
        for role in ("vm_a", "vm_b")}}}
    path = tmp_path / "bindings.json"
    raw = json.dumps(bindings, sort_keys=True).encode()
    path.write_bytes(raw)
    stage["observer_bindings_sha256"] = hashlib.sha256(raw).hexdigest()
    receipt_path = tmp_path / "launch-receipt.json"
    receipt_raw = json.dumps(receipt, sort_keys=True).encode()
    receipt_path.write_bytes(receipt_raw)
    stage["observer_launch_receipt_path"] = str(receipt_path)
    stage["observer_launch_receipt_sha256"] = hashlib.sha256(receipt_raw).hexdigest()
    return stage, path, bindings, receipt


def test_admitted_fresh_guest_bindings_are_healthy_without_network(tmp_path):
    stage, path, _, _ = pinned_stage(tmp_path)
    result = observe_bindings(stage, path, now=NOW, probe=fresh_probe,
                              run=lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    assert result["health"] == "healthy"
    key = json.dumps(["entry-1", "aws", "run-1"], separators=(",", ":"))
    assert result["entries"][key]["roles"]["vm_a"]["health"] == "healthy"


def test_guest_probe_detects_stale_unreachable_reused_pid_and_missing_outputs():
    role = role_binding("vm_a")
    stale = probe_role(role, WORKSPACE, "vm_a", now=NOW, owned_role=role, probe=lambda *_: {
        **fresh_probe(role, {"processes": role["processes"], "files": role["files"]}),
        "files": {k: {**v, "mtime": 1} for k, v in fresh_probe(role, {"processes": role["processes"], "files": role["files"]})["files"].items()}})
    assert stale["health"] == "fault"
    assert any("stale" in error for error in stale["errors"])
    unreachable = probe_role(role, WORKSPACE, "vm_a", now=NOW, owned_role=role,
        probe=lambda *_: (_ for _ in ()).throw(RuntimeError("offline")))
    assert unreachable["health"] == "fault"
    reused = fresh_probe(role, {"processes": role["processes"], "files": role["files"]})
    reused["processes"]["producer"]["start_ticks"] += 1
    assert "process_identity_mismatch:producer" in probe_role(role, WORKSPACE, "vm_a", now=NOW, owned_role=role,
        probe=lambda *_: reused)["errors"]
    missing = fresh_probe(role, {"processes": role["processes"], "files": role["files"]})
    del missing["files"]["measurements/probe-0001.csv"]
    assert "output_missing_or_empty:sockperf_chunk" in probe_role(role, WORKSPACE, "vm_a", now=NOW, owned_role=role,
        probe=lambda *_: missing)["errors"]


def test_live_sockperf_full_log_may_remain_empty_until_process_finalizes():
    role = role_binding("vm_a")
    path = "measurements/probe-0001.csv"
    role["processes"][0].update({"path": path, "action_id": "sockperf-probe-0001",
        "unit_id": "probe-0001", "timeout_seconds": 90, "requested_seconds": 70,
        "phase": "single_flow", "streams": 1, "trial_index": None, "parent_unit_id": None,
        "streaming_output": False})
    role["files"][0].update({"output_state": "finalization_pending", "unit_id": "probe-0001",
        "phase": "single_flow", "streams": 1, "trial_index": None, "parent_unit_id": None})
    def probe(_role, payload):
        return {"processes": {item["kind"]: {"pid": item["pid"], "boot_id": item["boot_id"],
            "start_ticks": item["start_ticks"], "state": "S", "alive": True, "age_seconds": 80,
            "age_resolution_seconds": 0.01}
            for item in payload["processes"]},
            "files": {item["path"]: {"exists": False} if item.get("output_state") == "finalization_pending"
                else {"exists": True, "size": 10, "mtime": NOW - 1} for item in payload["files"]}}
    result = probe_role(role, WORKSPACE, "vm_a", now=NOW, owned_role=role, probe=probe)
    assert result["health"] == "healthy", result
    assert result["outputs_pending_finalization"] == ["sockperf_chunk"]

    overrun = probe_role(role, WORKSPACE, "vm_a", now=NOW, owned_role=role,
        probe=lambda _role, payload: {**probe(_role, payload), "processes": {
            **probe(_role, payload)["processes"], "producer": {**probe(_role, payload)["processes"]["producer"],
                "age_seconds": 90.1}}})
    assert overrun["health"] == "fault"
    assert "measurement_process_exceeded_original_timeout:producer" in overrun["errors"]


def test_executed_remote_snippet_rejects_zombie_exited_and_invalid_proc_states(tmp_path):
    import subprocess
    import sys
    from cloud_network_benchmark.guest_observer import _REMOTE

    workspace = tmp_path / "workspace"
    (workspace / "measurements").mkdir(parents=True)
    (workspace / "measurements/current.csv").write_text("sample")
    proc_root = tmp_path / "proc"
    (proc_root / "sys/kernel/random").mkdir(parents=True)
    (proc_root / "sys/kernel/random/boot_id").write_text("boot-1\n")
    (proc_root / "uptime").write_text("1000.0 0.0\n")
    payload = {"workspace": str(workspace), "proc_root": str(proc_root),
        "processes": [{"kind": "producer", "pid": 321}],
        "files": [{"path": "measurements/current.csv", "kind": "sockperf_chunk"}]}
    for state, expected_alive in (("R", True), ("S", True), ("D", True),
                                  ("T", False), ("t", False), ("Z", False), ("X", False), ("?", False)):
        process_dir = proc_root / "321"
        process_dir.mkdir(exist_ok=True)
        fields = [state] + ["1"] * 18 + ["987"]
        (process_dir / "stat").write_text("321 (sockperf) " + " ".join(fields))
        result = subprocess.run([sys.executable, "-c", _REMOTE, json.dumps(payload)],
            capture_output=True, text=True, check=True, timeout=2)
        process = json.loads(result.stdout)["processes"]["producer"]
        assert process["alive"] is expected_alive
        assert process["state"] == state


@pytest.mark.parametrize("state", ["T", "t"])
def test_stopped_and_tracing_observer_processes_are_not_healthy(state):
    role = role_binding("vm_a")
    observed = fresh_probe(role, {"processes": role["processes"], "files": role["files"]})
    observed["processes"]["producer"]["state"] = state
    result = probe_role(role, WORKSPACE, "vm_a", now=NOW, owned_role=role, probe=lambda *_: observed)
    assert result["health"] == "fault"
    assert "process_identity_mismatch:producer" in result["errors"]


def test_receiver_and_diagnostic_staleness_are_faults_and_incomplete_bindings_unknown():
    role = role_binding("vm_b")
    saved = fresh_probe(role, {"processes": role["processes"], "files": role["files"]})
    saved["files"]["iperf3/server.jsonl"]["mtime"] = 1
    result = probe_role(role, WORKSPACE, "vm_b", now=NOW, owned_role=role, probe=lambda *_: saved)
    assert "output_stale:iperf3_receiver" in result["errors"]
    diagnostic_stale = fresh_probe(role, {"processes": role["processes"], "files": role["files"]})
    diagnostic_stale["files"]["diagnostics/vm_b.jsonl"]["mtime"] = 1
    assert "output_stale:diagnostics" in probe_role(role, WORKSPACE, "vm_b", now=NOW, owned_role=role,
        probe=lambda *_: diagnostic_stale)["errors"]
    role["processes"] = [item for item in role["processes"] if item["kind"] != "receiver"]
    assert probe_role(role, WORKSPACE, "vm_b", now=NOW, owned_role=role, probe=fresh_probe)["health"] == "unknown"


def test_binding_digest_and_admitted_run_identity_are_enforced(tmp_path):
    stage, path, bindings, _ = pinned_stage(tmp_path)
    bindings["entries"][0]["run_id"] = "other-run"
    path.write_text(json.dumps(bindings, sort_keys=True))
    assert "bindings_hash_mismatch" in observe_bindings(stage, path, now=NOW, probe=fresh_probe)["errors"]
    stage["observer_bindings_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    assert "active_guest_binding_set_mismatch" in observe_bindings(stage, path, now=NOW, probe=fresh_probe)["errors"]


def test_three_provider_results_use_composite_keys_and_require_exact_participants(tmp_path):
    stage, path, bindings, receipt = pinned_stage(tmp_path)
    source = stage["entries"][0]
    source["run_ids"] = {"aws": "run-aws", "azure": "run-azure", "gcp": "run-gcp"}
    template = bindings["entries"][0]
    entries = []
    participants = {}
    for provider, run_id in source["run_ids"].items():
        item = {**template, "provider": provider, "run_id": run_id, "deployment_id": run_id + "-deployment"}
        entries.append(item)
        participants[json.dumps(["entry-1", provider, run_id], separators=(",", ":"))] = {
            role: {"vm_id": role_binding(role)["vm_id"], "connection": role_binding(role)["connection"]}
            for role in ("vm_a", "vm_b")}
    bindings["entries"] = entries
    raw = json.dumps(bindings, sort_keys=True).encode()
    path.write_bytes(raw)
    stage["observer_bindings_sha256"] = hashlib.sha256(raw).hexdigest()
    receipt["participants"] = participants
    receipt_path = Path(stage["observer_launch_receipt_path"])
    receipt_raw = json.dumps(receipt, sort_keys=True).encode()
    receipt_path.write_bytes(receipt_raw)
    stage["observer_launch_receipt_sha256"] = hashlib.sha256(receipt_raw).hexdigest()

    result = observe_bindings(stage, path, now=NOW, probe=fresh_probe)
    assert result["health"] == "healthy"
    assert len(result["entries"]) == 3
    assert {json.loads(key)[1] for key in result["entries"]} == {"aws", "azure", "gcp"}

    bindings["entries"].append(bindings["entries"][0])
    raw = json.dumps(bindings, sort_keys=True).encode()
    path.write_bytes(raw)
    stage["observer_bindings_sha256"] = hashlib.sha256(raw).hexdigest()
    assert "duplicate_guest_participant" in observe_bindings(
        stage, path, now=NOW, probe=fresh_probe)["errors"]

    bindings["entries"].pop()
    bindings["entries"].pop()
    raw = json.dumps(bindings, sort_keys=True).encode()
    path.write_bytes(raw)
    stage["observer_bindings_sha256"] = hashlib.sha256(raw).hexdigest()
    assert "active_guest_binding_set_mismatch" in observe_bindings(
        stage, path, now=NOW, probe=fresh_probe)["errors"]


def test_unowned_vm_and_future_guest_mtime_never_report_healthy(tmp_path):
    stage, path, bindings, _ = pinned_stage(tmp_path)
    bindings["entries"][0]["roles"]["vm_a"]["vm_id"] = "foreign-unowned-vm"
    raw = json.dumps(bindings, sort_keys=True).encode()
    path.write_bytes(raw)
    stage["observer_bindings_sha256"] = hashlib.sha256(raw).hexdigest()
    result = observe_bindings(stage, path, now=NOW, probe=fresh_probe)
    assert result["health"] == "fault"
    assert "vm_identity_unowned" in result["entries"][json.dumps(["entry-1", "aws", "run-1"], separators=(",", ":"))]["roles"]["vm_a"]["errors"]

    role = role_binding("vm_a")
    future = fresh_probe(role, {"processes": role["processes"], "files": role["files"]})
    for item in role["files"]:
        future["files"][item["path"]]["mtime"] = NOW + 1e9
    result = probe_role(role, WORKSPACE, "vm_a", now=NOW, owned_role=role, probe=lambda *_: future)
    assert result["health"] == "fault"
    assert "output_time_in_future:sockperf_chunk" in result["errors"]


def test_unreceipted_ssh_connection_cannot_probe_guest(tmp_path):
    stage, path, bindings, _ = pinned_stage(tmp_path)
    bindings["entries"][0]["roles"]["vm_a"]["connection"]["port"] = 2222
    raw = json.dumps(bindings, sort_keys=True).encode()
    path.write_bytes(raw)
    stage["observer_bindings_sha256"] = hashlib.sha256(raw).hexdigest()
    called = []
    result = observe_bindings(stage, path, now=NOW,
        probe=lambda role, payload: called.append(role) or fresh_probe(role, payload))
    key = json.dumps(["entry-1", "aws", "run-1"], separators=(",", ":"))
    assert result["health"] == "fault"
    assert "connection_receipt_mismatch" in result["entries"][key]["roles"]["vm_a"]["errors"]
    assert len(called) == 1


def test_guest_fanout_has_one_total_probe_deadline(tmp_path):
    stage, path, bindings, receipt = pinned_stage(tmp_path)
    source = stage["entries"][0]
    source["run_ids"] = {"aws": "run-aws", "azure": "run-azure", "gcp": "run-gcp"}
    template = bindings["entries"][0]
    bindings["entries"] = [{**template, "provider": provider, "run_id": run_id,
                            "deployment_id": run_id + "-deployment"}
                           for provider, run_id in source["run_ids"].items()]
    receipt["participants"] = {json.dumps(["entry-1", provider, run_id], separators=(",", ":")):
        {role: {"vm_id": role_binding(role)["vm_id"], "connection": role_binding(role)["connection"]}
         for role in ("vm_a", "vm_b")} for provider, run_id in source["run_ids"].items()}
    raw = json.dumps(bindings, sort_keys=True).encode()
    path.write_bytes(raw)
    stage["observer_bindings_sha256"] = hashlib.sha256(raw).hexdigest()
    receipt_path = Path(stage["observer_launch_receipt_path"])
    receipt_raw = json.dumps(receipt, sort_keys=True).encode()
    receipt_path.write_bytes(receipt_raw)
    stage["observer_launch_receipt_sha256"] = hashlib.sha256(receipt_raw).hexdigest()
    elapsed = [0.0]
    calls = []

    def slow_probe(role, payload):
        calls.append(role)
        elapsed[0] += 5
        return fresh_probe(role, payload)

    result = observe_bindings(stage, path, now=NOW, probe=slow_probe,
        monotonic=lambda: elapsed[0], probe_budget_seconds=20)
    assert len(calls) == 4
    assert elapsed[0] == 20
    assert any("guest_probe_budget_exhausted" in role["errors"]
               for entry in result["entries"].values() for role in entry["roles"].values())


def test_output_freshness_uses_clock_after_each_probe_response(tmp_path):
    stage, path, _, _ = pinned_stage(tmp_path)
    wall = [NOW]

    def delayed_probe(role, payload):
        wall[0] += 6
        result = fresh_probe(role, payload)
        for value in result["files"].values():
            value["mtime"] = wall[0]
        return result

    result = observe_bindings(stage, path, probe=delayed_probe, now=NOW, clock=lambda: wall[0])
    assert result["health"] == "healthy"


def test_watcher_rejects_future_snapshot_timestamp():
    from cloud_network_benchmark.observer_watch import _snapshot
    future = "2099-01-01T00:00:00+00:00"
    # Use a minimal temporary file without relying on wall-clock sleeps.
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        snapshot = Path(directory) / "observer-status.json"
        snapshot.write_text(json.dumps({"observed_at": future, "health": "healthy"}))
        status, details = _snapshot(snapshot, datetime(2026, 11, 22, tzinfo=timezone.utc).timestamp(), 90)
    assert status == "observer_unavailable"
    assert details["health"] == "unknown"


def test_ssh_probe_is_bounded_read_only_and_requires_pinned_host_keys(monkeypatch):
    from cloud_network_benchmark.guest_observer import _ssh_probe
    role = role_binding("vm_a")
    seen = []
    monkeypatch.setattr("cloud_network_benchmark.guest_observer.subprocess.run", lambda argv, **kwargs: (
        seen.append((argv, kwargs)) or SimpleNamespace(returncode=1, stdout="", stderr="")))
    try:
        _ssh_probe(role, {"workspace": WORKSPACE, "processes": role["processes"], "files": role["files"]}, timeout=10,
                   run=lambda argv, **kwargs: (seen.append((argv, kwargs)) or SimpleNamespace(returncode=1, stdout="", stderr="")))
    except RuntimeError:
        pass
    assert seen[0][0][0] == "ssh"
    assert "-oStrictHostKeyChecking=yes" in seen[0][0]
    assert "-oUserKnownHostsFile=/private/tmp/cnb-known-hosts" in seen[0][0]
    assert "-i" in seen[0][0] and "22" in seen[0][0]
    assert seen[0][1]["timeout"] == 10
    assert seen[0][1]["check"] is False
