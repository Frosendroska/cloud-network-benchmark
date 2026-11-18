from __future__ import annotations

import json
import base64
import hashlib
from types import SimpleNamespace

import pytest

from cloud_network_benchmark.lifecycle_projection import (_controller_record,
    _iperf3_control_event_association, _verified_iperf3_control_evidence)
from cloud_network_benchmark.contracts.artifacts import ArtifactInput, ArtifactRecord, Evidence, StringEvidence
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


def _association_fixture(tmp_path):
    from cloud_network_benchmark.contracts.common import VmRole

    boot = "12345678-1234-1234-8234-123456789abc"
    unit = f"cnb-{DEPLOYMENT}-iperf3"
    log_path = "/var/lib/cnb/results/server-logs/iperf3-server.jsonl"
    err_path = "/var/lib/cnb/results/server-logs/iperf3-server.err"
    prefix = b'{"event":"error","data":"error - unable to receive cookie at server: "}\n{"event":"end","data":{}}\n'
    before_stop = prefix + b'{"event":"server_listening","data":{}}\n'
    raw_log = before_stop + b'{"event":"error","data":"interrupt - the server has terminated"}\n{"event":"end","data":{}}\n'
    ids = [[11,12],[11,13]]
    version = "iperf 3.17.1 (cJSON 1.7.15)\nLinux fixture 6.8.0\nOptional features: CPU affinity setting, IPv6 flow label, TCP congestion algorithm setting, sendfile / zerocopy, socket pacing, authentication"
    source = {"tool":"iperf3","binary_version":version,"source_reference":"iperf-2acfcfe94e928e74542c9f107e02aa6dd4748a79",
        "source_sha256":"d6b4d2c7b20157c5b7fbd2de66f22bd3428958b5f2e28899151b6e22745886fe",
        "binary_sha256":"a"*64,"upstream_commit":"2acfcfe94e928e74542c9f107e02aa6dd4748a79",
        "patch_path":"tool_patches/iperf-3.17.1-acquisition.patch",
        "patch_sha256":"11b96daadb4a1f17dc24376bb3d0d7feec330efeedc550b1979b1566e9b81661",
        "observation_capability":"CNB_ACQ_V1","observation_semantics":"iperf-3.17.1-stats-v1"}
    owner = {"pid":32000,"start_ticks":1234,"boot_id":boot,"cgroup":f"/system.slice/{unit}.service",
        "log_ids":ids,"closed_at_utc":"2026-11-17T12:00:11+00:00"}
    def action(action_id, role, host, kind, start, finish, command):
        return {"action_id":action_id,"role":role,"vm_id":host,"action_type":kind,"outcome":"succeeded",
            "exit_code":0,"started_at":start,"finished_at":finish,"remote_command":command}
    ready = action("f02r-ready-private-tcp-1","vm_a","203.0.113.1","ssh","2026-11-17T12:00:00+00:00",
        "2026-11-17T12:00:01+00:00","nc -z -w 1 10.0.0.2 5201")
    snap_common = {"deployment_id":DEPLOYMENT,"controller_session_id":"fake-session",
        "listener_invocation_id":"f02r-vm_b-listener-iperf3","unit":unit,"pid":32000,
        "start_ticks":1234,"boot_id":boot,"cgroup":owner["cgroup"],"log_path":log_path,"log_ids":ids}
    pre = {**snap_common,"boundary":"pre_measure","prefix_bytes":len(prefix),"prefix_sha256":hashlib.sha256(prefix).hexdigest()}
    post = {**snap_common,"boundary":"pre_stop","available":True,"prefix_bytes":len(before_stop),
        "prefix_sha256":hashlib.sha256(before_stop).hexdigest()}
    snap_action = action(f"f09-iperf3-control-prefix-measure-{DEPLOYMENT}","vm_b","203.0.113.2","ssh",
        "2026-11-17T12:00:01+00:00","2026-11-17T12:00:02+00:00",f"snapshot {log_path} {err_path} fake-session {unit}")
    dispatch = action("f09-guest-run-one","vm_a","203.0.113.1","ssh","2026-11-17T12:00:03+00:00",
        "2026-11-17T12:00:04+00:00","run f03")
    receipt = action("f09-guest-outcome-one","vm_a","203.0.113.1","scp","2026-11-17T12:00:05+00:00",
        "2026-11-17T12:00:06+00:00","scp result")
    owner_row = action(f"f09-listener-owner-process-{DEPLOYMENT}-iperf3","vm_b","203.0.113.2","ssh",
        "2026-11-17T12:00:07+00:00","2026-11-17T12:00:08+00:00",f"owner {log_path} {err_path}")
    stop_row = action(f"f09-listener-stop-{DEPLOYMENT}-iperf3","vm_b","203.0.113.2","ssh",
        "2026-11-17T12:00:08+00:00","2026-11-17T12:00:09+00:00",f"sudo systemctl stop {unit}")
    owner_tokens = ["32000","1234",boot,owner["cgroup"],"11","12","13",log_path,err_path]
    closed_row = action(f"f09-listener-closed-files-{DEPLOYMENT}-iperf3","vm_b","203.0.113.2","ssh",
        "2026-11-17T12:00:10+00:00","2026-11-17T12:00:11+00:00","closure " + " ".join(owner_tokens))
    payload = {"schema_version":1,"run_id":RUN,"deployment_id":DEPLOYMENT,"vm_role":"vm_b",
        "vm_a_host_id":"203.0.113.1","vm_b_host_id":"203.0.113.2","private_ipv4_b":"10.0.0.2",
        "iperf3_stdout_path":log_path,"iperf3_stderr_path":err_path,"controller_session_id":"fake-session",
        "source":source,"readiness":{"ready":True,"deployment_id":DEPLOYMENT,"actions":[ready]},
        "pre_measure":{"available":True,"snapshot":pre,"action":snap_action},"measure_actions":[dispatch,receipt],
        "listener":{"closed":True,"failures":[],"closed_at":"2026-11-17T12:00:11+00:00",
            "measurement_ended_at":"2026-11-17T12:00:07+00:00","iperf3_owner_and_log_identity":owner,
            "pre_stop_prefix":post,"actions":[owner_row,stop_row,closed_row]}}

    def record(artifact_id, path, kind, producer, data):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return ArtifactRecord(artifact_id=artifact_id,path=path,kind=kind,producer=producer,unit_id=None,
            deployment_id=DEPLOYMENT,closed=True,size_bytes=Evidence(value=len(data)),
            sha256=StringEvidence(value=hashlib.sha256(data).hexdigest()),retrieval_state="verified")
    build_bytes = "\n".join(("CNB_BUILD_V1", "tool=iperf3",f"source_reference={source['source_reference']}",
        f"source_sha256={source['source_sha256']}",f"upstream_commit={source['upstream_commit']}",
        f"patch_path={source['patch_path']}",f"patch_sha256={source['patch_sha256']}",
        "compiler_version_b64="+base64.b64encode(b"gcc 13.2.0").decode(),
        "build_configuration_b64="+base64.b64encode(b"configure && make").decode(),
        "binary_path=/var/lib/cnb/install/bin/iperf3",
        "binary_version_b64="+base64.b64encode(version.encode()).decode(),f"binary_sha256={source['binary_sha256']}",
        "observation_capability=CNB_ACQ_V1","observation_semantics=iperf-3.17.1-stats-v1",
        "capabilities=json-stream,receiver-stats","")).encode()
    acquisition = ("CNB_ACQ_V1 "+json.dumps({"tool":"iperf3","semantics_id":"iperf-3.17.1-stats-v1",
        "invocation_id":"f02r-vm_b-listener-iperf3","record_kind":"clock_anchor","pid":32000,
        "boot_id":boot,"clock":"CLOCK_MONOTONIC"},separators=(",", ":"))+"\n").encode()
    records = [record("iperf3_server_log","server-logs/iperf3-server.jsonl","iperf3-server-jsonl",VmRole.VM_B,raw_log),
        record("iperf3_server_error","server-logs/iperf3-server.err","iperf3-observation-stderr",VmRole.VM_B,acquisition),
        record("f02r-vm_b-iperf3-build","builds/vm_b/iperf3.identity","build-identity",VmRole.VM_B,build_bytes)]
    association, _ = _iperf3_control_event_association(tmp_path, records, payload,
        vm_a_host_id="203.0.113.1",vm_b_host_id="203.0.113.2",private_ipv4_b="10.0.0.2",stdout_path=log_path)
    payload["readiness"]["event_association"] = association
    records.append(_record(tmp_path,payload))
    return payload, records, raw_log


def test_legacy_control_evidence_without_prefix_proof_remains_unavailable(tmp_path):
    payload = _payload()
    record = _record(tmp_path, payload)
    # Older controller snapshots describe readiness and closure but cannot
    # associate native error/end pairs with those boundaries.
    assert _read(tmp_path, [record]) is None
    assert _read(tmp_path, []) is None


def test_native_shape_control_association_is_replayed_from_lf_bytes_and_pinned_banner(tmp_path):
    payload, records, raw = _association_fixture(tmp_path)
    association, suppressions = _iperf3_control_event_association(tmp_path,
        [item for item in records if item.artifact_id != "iperf3_control_evidence"], payload,
        vm_a_host_id="203.0.113.1",vm_b_host_id="203.0.113.2",private_ipv4_b="10.0.0.2",
        stdout_path="/var/lib/cnb/results/server-logs/iperf3-server.jsonl")
    assert association["status"] == "verified"
    assert association["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert association["readiness_cookie"]["byte_end"] == payload["pre_measure"]["snapshot"]["prefix_bytes"]
    assert len(suppressions) == 4
    assert _read(tmp_path, records) == payload
    control = next(item for item in records if item.artifact_id == "iperf3_control_evidence")
    for changed in (control.model_copy(update={"deployment_id":"other-deployment"}),
            control.model_copy(update={"producer":"vm_a"}),
            control.model_copy(update={"path":"metadata/other.json"})):
        changed_records = [changed if item.artifact_id == "iperf3_control_evidence" else item for item in records]
        assert _read(tmp_path, changed_records) is None


@pytest.mark.parametrize("mutation", [
    "source_reference", "source_patch", "version", "vm_binding", "owner_pid", "owner_boot",
    "foreign_fd", "action_order", "prefix_offset", "prefix_hash", "missing_snapshot",
])
def test_native_control_association_rejects_independent_identity_and_boundary_failures(tmp_path, mutation):
    payload, records, _raw = _association_fixture(tmp_path)
    if mutation == "source_reference":
        payload["source"]["source_reference"] = "iperf-unpinned"
    elif mutation == "source_patch":
        payload["source"]["patch_sha256"] = "f" * 64
    elif mutation == "version":
        payload["source"]["binary_version"] = "3.17.1"
    elif mutation == "vm_binding":
        payload["listener"]["actions"][0]["vm_id"] = "203.0.113.9"
    elif mutation == "owner_pid":
        payload["listener"]["iperf3_owner_and_log_identity"]["pid"] += 1
    elif mutation == "owner_boot":
        payload["listener"]["pre_stop_prefix"]["boot_id"] = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    elif mutation == "foreign_fd":
        payload["listener"]["pre_stop_prefix"]["log_ids"][0][1] += 99
    elif mutation == "action_order":
        payload["measure_actions"][0]["started_at"] = "2026-11-17T12:00:00+00:00"
    elif mutation == "prefix_offset":
        payload["pre_measure"]["snapshot"]["prefix_bytes"] -= 1
    elif mutation == "prefix_hash":
        payload["listener"]["pre_stop_prefix"]["prefix_sha256"] = "f" * 64
    else:
        payload["pre_measure"]["snapshot"] = None
    association, suppressions = _iperf3_control_event_association(tmp_path,
        [item for item in records if item.artifact_id != "iperf3_control_evidence"], payload,
        vm_a_host_id="203.0.113.1",vm_b_host_id="203.0.113.2",private_ipv4_b="10.0.0.2",
        stdout_path="/var/lib/cnb/results/server-logs/iperf3-server.jsonl")
    assert association["status"] == "unavailable"
    assert not suppressions


def test_native_control_association_rejects_raw_tamper_and_truncation(tmp_path):
    payload, records, raw = _association_fixture(tmp_path)
    target = tmp_path / "server-logs/iperf3-server.jsonl"
    target.write_bytes(raw[:-8])
    association, suppressions = _iperf3_control_event_association(tmp_path,
        [item for item in records if item.artifact_id != "iperf3_control_evidence"], payload,
        vm_a_host_id="203.0.113.1",vm_b_host_id="203.0.113.2",private_ipv4_b="10.0.0.2",
        stdout_path="/var/lib/cnb/results/server-logs/iperf3-server.jsonl")
    assert association["status"] == "unavailable"
    assert not suppressions


def _rewrite_artifact_record(root, records, artifact_id, data):
    target_record = next(item for item in records if item.artifact_id == artifact_id)
    (root / target_record.path).write_bytes(data)
    updated = target_record.model_copy(update={
        "size_bytes": Evidence(value=len(data)),
        "sha256": StringEvidence(value=hashlib.sha256(data).hexdigest())})
    return [updated if item.artifact_id == artifact_id else item for item in records]


@pytest.mark.parametrize("case", ["repeated", "nonadjacent", "in_session"])
def test_native_control_events_must_be_unique_adjacent_and_outside_measurement(tmp_path, case):
    payload, records, raw = _association_fixture(tmp_path)
    records = [item for item in records if item.artifact_id != "iperf3_control_evidence"]
    if case == "repeated":
        altered = raw + b'{"event":"error","data":"interrupt - the server has terminated"}\n{"event":"end","data":{}}\n'
    elif case == "nonadjacent":
        altered = raw.replace(b'\n{"event":"end","data":{}}\n',
            b'\n{"event":"server_stats","data":{}}\n{"event":"end","data":{}}\n', 1)
        pre_size = payload["pre_measure"]["snapshot"]["prefix_bytes"]
        pre_bytes = altered[:pre_size + len(b'{"event":"server_stats","data":{}}\n')]
        payload["pre_measure"]["snapshot"]["prefix_bytes"] = len(pre_bytes)
        payload["pre_measure"]["snapshot"]["prefix_sha256"] = hashlib.sha256(pre_bytes).hexdigest()
        post_size = payload["listener"]["pre_stop_prefix"]["prefix_bytes"]
        post_bytes = altered[:post_size + len(b'{"event":"server_stats","data":{}}\n')]
        payload["listener"]["pre_stop_prefix"]["prefix_bytes"] = len(post_bytes)
        payload["listener"]["pre_stop_prefix"]["prefix_sha256"] = hashlib.sha256(post_bytes).hexdigest()
    else:
        post = payload["listener"]["pre_stop_prefix"]
        post["prefix_bytes"] = len(raw)
        post["prefix_sha256"] = hashlib.sha256(raw).hexdigest()
        altered = raw
    records = _rewrite_artifact_record(tmp_path, records, "iperf3_server_log", altered)
    association, suppressions = _iperf3_control_event_association(tmp_path, records, payload,
        vm_a_host_id="203.0.113.1",vm_b_host_id="203.0.113.2",private_ipv4_b="10.0.0.2",
        stdout_path="/var/lib/cnb/results/server-logs/iperf3-server.jsonl")
    assert association["status"] == "unavailable"
    assert not suppressions


def test_unknown_native_error_is_never_in_the_control_suppression_set(tmp_path):
    payload, records, raw = _association_fixture(tmp_path)
    records = [item for item in records if item.artifact_id != "iperf3_control_evidence"]
    unknown_line = raw.count(b"\n") + 1
    altered = raw + b'{"event":"error","data":"unrecognized server diagnostic"}\n'
    records = _rewrite_artifact_record(tmp_path, records, "iperf3_server_log", altered)
    association, suppressions = _iperf3_control_event_association(tmp_path, records, payload,
        vm_a_host_id="203.0.113.1",vm_b_host_id="203.0.113.2",private_ipv4_b="10.0.0.2",
        stdout_path="/var/lib/cnb/results/server-logs/iperf3-server.jsonl")
    assert association["status"] == "verified"
    assert all(line != unknown_line for line, _code in suppressions)


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
