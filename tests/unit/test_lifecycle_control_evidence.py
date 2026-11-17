from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cloud_network_benchmark.lifecycle_projection import _controller_record, _verified_iperf3_control_evidence
from cloud_network_benchmark.contracts.artifacts import ArtifactInput
from cloud_network_benchmark.contracts.artifacts import ArtifactLayout


RUN = "run-control-aws"
DEPLOYMENT = f"{RUN}-deployment"
DECLARATION = ArtifactInput(artifact_id="iperf3_control_evidence",
    path="metadata/iperf3-control-evidence.json", kind="iperf3-control-evidence",
    producer="controller", unit_id=None, expectation="required")
CHILD = SimpleNamespace(run_id=RUN, deployment_id=DEPLOYMENT)


def _payload(*, run=RUN, deployment=DEPLOYMENT, role="vm_b", ready=True, closed=True):
    return {"schema_version": 1, "run_id": run, "deployment_id": deployment, "vm_role": role,
        "vm_a_host_id": "203.0.113.1", "vm_b_host_id": "203.0.113.2",
        "private_ipv4_b": "10.0.0.2", "iperf3_stdout_path": "/var/lib/cnb/results/server-logs/iperf3-server.jsonl",
        "iperf3_stderr_path": "/var/lib/cnb/results/server-logs/iperf3-server.err",
        "readiness": {"ready": ready, "deployment_id": DEPLOYMENT, "actions": [{"action_id": "f02r-ready-private-tcp-1",
            "role": "vm_a", "vm_id": "203.0.113.1", "outcome": "succeeded",
            "exit_code": 0,
            "action_type": "ssh", "started_at": "2026-11-17T11:59:59+00:00",
            "finished_at": "2026-11-17T12:00:00+00:00",
            "remote_command": "nc -z -w 1 10.0.0.2 5201"}]},
        "listener": {"closed": closed, "failures": [], "closed_at": "2026-11-17T12:00:00+00:00",
            "measurement_ended_at": "2026-11-17T11:59:59+00:00",
            "iperf3_owner_and_log_identity": {"pid": 7, "start_ticks": 9, "boot_id": "12345678-1234-1234-1234-123456789abc",
            "cgroup": f"/system.slice/cnb-{DEPLOYMENT}-iperf3.service", "log_ids": [[1, 2], [1, 3]],
                "closed_at_utc": "2026-11-17T12:00:00+00:00"},
            "actions": [{"action_id": f"f09-listener-{action}-{DEPLOYMENT}-iperf3",
                "role": "vm_b", "vm_id": "203.0.113.2", "action_type": "ssh", "outcome": "succeeded",
                "exit_code": 0, "started_at": "2026-11-17T11:59:59+00:00", "finished_at": "2026-11-17T12:00:00+00:00",
                "remote_command": "/var/lib/cnb/results/server-logs/iperf3-server.jsonl /var/lib/cnb/results/server-logs/iperf3-server.err"
                    if action != "stop" else f"sudo systemctl stop cnb-{DEPLOYMENT}-iperf3"} for action in
                ("stop", "owner-process", "closed-files")]}}


def _record(tmp_path, payload):
    return _controller_record(tmp_path, DEPLOYMENT, DECLARATION,
        (json.dumps(payload, sort_keys=True) + "\n").encode())


def _read(tmp_path, records):
    return _verified_iperf3_control_evidence(tmp_path, CHILD, records,
        vm_a_host_id="203.0.113.1", vm_b_host_id="203.0.113.2",
        private_ipv4_b="10.0.0.2", stdout_path="/var/lib/cnb/results/server-logs/iperf3-server.jsonl",
        stderr_path="/var/lib/cnb/results/server-logs/iperf3-server.err")


def test_verified_control_evidence_requires_bound_identity_readiness_and_owned_closure(tmp_path):
    payload = _payload()
    record = _record(tmp_path, payload)
    assert _read(tmp_path, [record]) == payload
    assert _read(tmp_path, []) is None
    assert _read(tmp_path,
        [record.model_copy(update={"deployment_id": "other-deployment"})]) is None
    assert _read(tmp_path,
        [record.model_copy(update={"producer": "vm_a"})]) is None
    assert _read(tmp_path,
        [record.model_copy(update={"path": "metadata/other.json"})]) is None


@pytest.mark.parametrize("change", [
    {"run": "other-run"}, {"deployment": "other-deployment"}, {"role": "vm_a"},
    {"ready": False}, {"closed": False},
])
def test_control_evidence_identity_or_missing_boundary_remains_unavailable(tmp_path, change):
    assert _read(tmp_path, [_record(tmp_path, _payload(**change))]) is None


@pytest.mark.parametrize("where", ["readiness", "listener"])
def test_control_evidence_missing_boundary_action_remains_unavailable(tmp_path, where):
    payload = _payload()
    payload[where]["actions"] = []
    assert _read(tmp_path, [_record(tmp_path, payload)]) is None


def test_control_evidence_wrong_readiness_action_identity_remains_unavailable(tmp_path):
    payload = _payload()
    payload["readiness"]["actions"][0]["action_id"] = "f02r-ready-private-icmp-1"
    assert _read(tmp_path, [_record(tmp_path, payload)]) is None


@pytest.mark.parametrize("location", ["stop_started", "stop_finished", "owner_closed"])
def test_control_evidence_non_string_close_timestamps_remain_unavailable(tmp_path, location):
    payload = _payload()
    if location == "owner_closed":
        payload["listener"]["iperf3_owner_and_log_identity"]["closed_at_utc"] = None
    else:
        payload["listener"]["actions"][0]["started_at" if location == "stop_started" else "finished_at"] = None
    assert _read(tmp_path, [_record(tmp_path, payload)]) is None


def test_control_evidence_is_declared_as_controller_materialization_not_remote_inventory():
    from cloud_network_benchmark.lifecycle_bundle import lifecycle_declarations, remote_inventory_paths_by_role
    declarations = lifecycle_declarations(ArtifactLayout(), mode="acceptance", measurement_units=[])
    declaration = next(item for item in declarations if item.artifact_id == "iperf3_control_evidence")
    assert declaration.producer == "controller"
    assert declaration.path == "metadata/iperf3-control-evidence.json"
    assert declaration in declarations
    assert declaration.path not in remote_inventory_paths_by_role(declarations)["vm_a"]
    assert declaration.path not in remote_inventory_paths_by_role(declarations)["vm_b"]


def test_tampered_control_evidence_bytes_remain_unavailable(tmp_path):
    record = _record(tmp_path, _payload())
    (tmp_path / record.path).write_bytes(b"{}\n")
    assert _read(tmp_path, [record]) is None


@pytest.mark.parametrize("field,value", [
    ("vm_a_host_id", "203.0.113.9"), ("vm_b_host_id", "203.0.113.9"),
    ("private_ipv4_b", "10.0.0.9"),
    ("iperf3_stdout_path", "/tmp/foreign.jsonl"),
    ("iperf3_stderr_path", "/tmp/foreign.err"),
])
def test_control_evidence_must_match_created_vm_and_owned_log_identity(tmp_path, field, value):
    payload = _payload()
    payload[field] = value
    assert _read(tmp_path, [_record(tmp_path, payload)]) is None


@pytest.mark.parametrize("which,field,value", [
    ("readiness", "vm_id", "203.0.113.9"),
    ("readiness", "remote_command", "nc -z -w 1 10.0.0.9 5201"),
    ("listener", "vm_id", "203.0.113.9"),
    ("listener", "remote_command", "/tmp/foreign.jsonl /tmp/foreign.err"),
])
def test_control_boundary_actions_must_match_expected_hosts_and_target(tmp_path, which, field, value):
    payload = _payload()
    if which == "readiness":
        payload["readiness"]["actions"][0][field] = value
    else:
        payload["listener"]["actions"][-1][field] = value
    assert _read(tmp_path, [_record(tmp_path, payload)]) is None
