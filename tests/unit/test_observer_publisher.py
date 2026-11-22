from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cloud_network_benchmark.guest_observer import observe_bindings
from cloud_network_benchmark.observer_publisher import build_bindings, publish_bindings, write_launch_receipt


CONNECTION = {"host": "192.0.2.10", "user": "bench", "port": 22,
              "known_hosts": "/private/tmp/known_hosts", "identity_file": "/private/tmp/identity"}
WORKSPACE = "/var/lib/cnb/work"
BOOT = "01234567-89ab-cdef-0123-456789abcdef"


def _process(kind, pid):
    return {"kind": kind, "pid": pid, "boot_id": BOOT, "start_ticks": pid * 10}


def _participant(entry, provider, run):
    roles = {}
    for index, role in enumerate(("vm_a", "vm_b")):
        kinds = ("producer", "diagnostics") if role == "vm_a" else ("receiver", "diagnostics")
        files = ([{"kind": "sockperf_chunk", "path": "measurements/chunk-001.csv", "state": "current"},
                 {"kind": "diagnostics", "path": "diagnostics/vm_a.jsonl"}]
                if role == "vm_a" else
                [{"kind": "iperf3_receiver", "path": "iperf3/server.jsonl"},
                 {"kind": "diagnostics", "path": "diagnostics/vm_b.jsonl"}])
        roles[role] = {"vm_id": f"{provider}-vm-{index}", "connection": dict(CONNECTION),
            "workspace": WORKSPACE,
            "processes": [_process(kind, 300 + index * 10 + i) for i, kind in enumerate(kinds)],
            "files": files}
    return {"entry_id": entry, "provider": provider, "run_id": run,
            "deployment_id": run + "-deployment", "roles": roles}


def _probe(_role, payload):
    return {"processes": {item["kind"]: {"pid": item["pid"], "boot_id": item["boot_id"],
        "start_ticks": item["start_ticks"], "state": "S", "alive": True}
        for item in payload["processes"]},
        "files": {item["path"]: {"size": 10, "mtime": 990, "kind": item["kind"]}
                  for item in payload["files"]}}


def test_actual_receipt_and_runtime_records_are_hash_pinned_and_consumable(tmp_path):
    entries = [_participant("e1", "aws", "run-aws"), _participant("e1", "gcp", "run-gcp"),
               _participant("e2", "azure", "run-azure")]
    owned = {(e["entry_id"], e["provider"], e["run_id"]): {
        role: {"vm_id": e["roles"][role]["vm_id"], "connection": e["roles"][role]["connection"]}
        for role in ("vm_a", "vm_b")} for e in entries}
    receipt_path = tmp_path / "launch.json"
    receipt_sha = write_launch_receipt(receipt_path, stage_id="stage", participants=owned)
    receipt_raw = receipt_path.read_bytes()
    assert hashlib.sha256(receipt_raw).hexdigest() == receipt_sha
    with pytest.raises(FileExistsError):
        write_launch_receipt(receipt_path, stage_id="stage", participants=owned)

    bindings = build_bindings(stage_id="stage", campaign_id="campaign", participants=entries,
                              launch_receipt=json.loads(receipt_raw))
    binding_path = tmp_path / "bindings.json"
    digest = publish_bindings(binding_path, bindings)
    assert hashlib.sha256(binding_path.read_bytes()).hexdigest() == digest
    runtimes = []
    for entry_id in ("e1", "e2"):
        runtime = tmp_path / f"{entry_id}.json"
        runtime.write_text(json.dumps({"access": {"bootstrap": {"workspace": WORKSPACE}}}))
        run_ids = {entry["provider"]: entry["run_id"] for entry in entries if entry["entry_id"] == entry_id}
        runtimes.append({"entry_id": entry_id, "run_ids": run_ids,
                         "runtime_config_path": str(runtime)})
    stage = {"stage_id": "stage", "campaign_id": "campaign", "entries": runtimes,
        "observer_bindings_sha256": digest, "observer_launch_receipt_path": str(receipt_path),
        "observer_launch_receipt_sha256": receipt_sha}
    result = observe_bindings(stage, binding_path, now=1000, probe=_probe,
                              run=lambda *_a, **_k: pytest.fail("unexpected SSH"))
    assert result["health"] == "healthy"
    assert len(result["entries"]) == 3


def test_missing_actual_roles_foreign_vm_and_bad_process_identity_fail_closed(tmp_path):
    participant = _participant("entry", "aws", "run-aws")
    identity = ("entry", "aws", "run-aws")
    owned = {identity: {role: {"vm_id": participant["roles"][role]["vm_id"],
        "connection": participant["roles"][role]["connection"]} for role in ("vm_a", "vm_b")}}
    path = tmp_path / "launch.json"
    write_launch_receipt(path, stage_id="stage", participants=owned)
    receipt = json.loads(path.read_text())
    bad = json.loads(json.dumps(participant))
    bad["roles"]["vm_a"]["vm_id"] = "foreign-vm"
    with pytest.raises(ValueError, match="provider-owned"):
        build_bindings(stage_id="stage", campaign_id="campaign", participants=[bad], launch_receipt=receipt)
    bad = json.loads(json.dumps(participant))
    bad["roles"]["vm_b"]["processes"] = [_process("diagnostics", 312)]
    with pytest.raises(ValueError, match="incomplete"):
        build_bindings(stage_id="stage", campaign_id="campaign", participants=[bad], launch_receipt=receipt)
    bad = json.loads(json.dumps(participant))
    bad["roles"]["vm_a"]["processes"][0]["start_ticks"] = 0
    with pytest.raises(ValueError, match="PID/start ticks"):
        build_bindings(stage_id="stage", campaign_id="campaign", participants=[bad], launch_receipt=receipt)


def test_rotation_snapshot_contains_observed_current_and_completed_chunks(tmp_path):
    participant = _participant("entry", "aws", "run-aws")
    identity = ("entry", "aws", "run-aws")
    owned = {identity: {role: {"vm_id": participant["roles"][role]["vm_id"],
        "connection": participant["roles"][role]["connection"]} for role in ("vm_a", "vm_b")}}
    receipt_path = tmp_path / "launch.json"
    write_launch_receipt(receipt_path, stage_id="stage", participants=owned)
    participant["roles"]["vm_a"]["files"].insert(0,
        {"kind": "sockperf_chunk", "path": "measurements/chunk-000.csv", "state": "completed"})
    binding = build_bindings(stage_id="stage", campaign_id="campaign", participants=[participant],
                             launch_receipt=json.loads(receipt_path.read_text()))
    assert [f["state"] for f in binding["entries"][0]["roles"]["vm_a"]["files"]
            if f["kind"] == "sockperf_chunk"] == ["completed", "current"]
