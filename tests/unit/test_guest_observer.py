from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

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
    return {"vm_id": "vm-real-id-" + role, "ssh_target": "bench@192.0.2.10",
            "workspace": WORKSPACE,
            "processes": [{"kind": kind, "pid": i + 300, "boot_id": "boot-1", "start_ticks": i + 900}
                          for i, kind in enumerate(process_kinds)], "files": files}


def fresh_probe(_role, payload):
    return {"processes": {item["kind"]: {"alive": True, "pid": item["pid"],
                "boot_id": item["boot_id"], "start_ticks": item["start_ticks"]}
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
    path = tmp_path / "bindings.json"
    raw = json.dumps(bindings, sort_keys=True).encode()
    path.write_bytes(raw)
    stage["observer_bindings_sha256"] = hashlib.sha256(raw).hexdigest()
    return stage, path, bindings


def test_admitted_fresh_guest_bindings_are_healthy_without_network(tmp_path):
    stage, path, _ = pinned_stage(tmp_path)
    result = observe_bindings(stage, path, now=NOW, probe=fresh_probe,
                              run=lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    assert result["health"] == "healthy"
    assert result["entries"]["entry-1"]["roles"]["vm_a"]["health"] == "healthy"


def test_guest_probe_detects_stale_unreachable_reused_pid_and_missing_outputs():
    role = role_binding("vm_a")
    stale = probe_role(role, WORKSPACE, "vm_a", now=NOW, probe=lambda *_: {
        **fresh_probe(role, {"processes": role["processes"], "files": role["files"]}),
        "files": {k: {**v, "mtime": 1} for k, v in fresh_probe(role, {"processes": role["processes"], "files": role["files"]})["files"].items()}})
    assert stale["health"] == "fault"
    assert any("stale" in error for error in stale["errors"])
    unreachable = probe_role(role, WORKSPACE, "vm_a", now=NOW,
        probe=lambda *_: (_ for _ in ()).throw(RuntimeError("offline")))
    assert unreachable["health"] == "fault"
    reused = fresh_probe(role, {"processes": role["processes"], "files": role["files"]})
    reused["processes"]["producer"]["start_ticks"] += 1
    assert "process_identity_mismatch:producer" in probe_role(role, WORKSPACE, "vm_a", now=NOW,
        probe=lambda *_: reused)["errors"]
    missing = fresh_probe(role, {"processes": role["processes"], "files": role["files"]})
    del missing["files"]["measurements/probe-0001.csv"]
    assert "output_missing_or_empty:sockperf_chunk" in probe_role(role, WORKSPACE, "vm_a", now=NOW,
        probe=lambda *_: missing)["errors"]


def test_receiver_and_diagnostic_staleness_are_faults_and_incomplete_bindings_unknown():
    role = role_binding("vm_b")
    saved = fresh_probe(role, {"processes": role["processes"], "files": role["files"]})
    saved["files"]["iperf3/server.jsonl"]["mtime"] = 1
    result = probe_role(role, WORKSPACE, "vm_b", now=NOW, probe=lambda *_: saved)
    assert "output_stale:iperf3_receiver" in result["errors"]
    diagnostic_stale = fresh_probe(role, {"processes": role["processes"], "files": role["files"]})
    diagnostic_stale["files"]["diagnostics/vm_b.jsonl"]["mtime"] = 1
    assert "output_stale:diagnostics" in probe_role(role, WORKSPACE, "vm_b", now=NOW,
        probe=lambda *_: diagnostic_stale)["errors"]
    role["processes"] = [item for item in role["processes"] if item["kind"] != "receiver"]
    assert probe_role(role, WORKSPACE, "vm_b", now=NOW, probe=fresh_probe)["health"] == "unknown"


def test_binding_digest_and_admitted_run_identity_are_enforced(tmp_path):
    stage, path, bindings = pinned_stage(tmp_path)
    bindings["entries"][0]["run_id"] = "other-run"
    path.write_text(json.dumps(bindings, sort_keys=True))
    assert "bindings_hash_mismatch" in observe_bindings(stage, path, now=NOW, probe=fresh_probe)["errors"]
    stage["observer_bindings_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    assert "admitted_run_mismatch" in observe_bindings(stage, path, now=NOW, probe=fresh_probe)["entries"]["entry-1"]["errors"]


def test_ssh_probe_is_bounded_read_only_and_requires_pinned_host_keys(monkeypatch):
    from cloud_network_benchmark.guest_observer import _ssh_probe
    role = role_binding("vm_a")
    seen = []
    monkeypatch.setattr("cloud_network_benchmark.guest_observer.subprocess.run", lambda argv, **kwargs: (
        seen.append((argv, kwargs)) or SimpleNamespace(returncode=1, stdout="", stderr="")))
    try:
        _ssh_probe(role, {"workspace": WORKSPACE, "processes": role["processes"], "files": role["files"]},
                   run=lambda argv, **kwargs: (seen.append((argv, kwargs)) or SimpleNamespace(returncode=1, stdout="", stderr="")))
    except RuntimeError:
        pass
    assert seen[0][0][0] == "ssh"
    assert "-oStrictHostKeyChecking=yes" in seen[0][0]
    assert seen[0][1]["timeout"] == 12
    assert seen[0][1]["check"] is False
