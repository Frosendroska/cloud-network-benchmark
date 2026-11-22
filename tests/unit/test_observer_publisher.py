from __future__ import annotations

import hashlib
from io import BytesIO
import json
from pathlib import Path
import subprocess
import threading
import time

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
        "files": {item["path"]: {"size": 10, "mtime": time.time() - 1, "kind": item["kind"]}
                  for item in payload["files"]}}


def _actual_sockperf_progress(tmp_path, monkeypatch):
    from cloud_network_benchmark.contracts.execution import CommandRequest
    from cloud_network_benchmark.measurement_process import LocalMeasurementProcesses, SystemMeasurementClock
    from cloud_network_benchmark.observer_publisher import GuestObserverProgress

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executable = tmp_path / "sockperf"
    executable.write_text("source-shaped sockperf executable")
    proc_root = tmp_path / "proc"
    (proc_root / "3501").mkdir(parents=True)
    (proc_root / "3501/stat").write_text("3501 (sockperf fake) S " + "1 " * 18 + "7654321\n")
    (proc_root / "sys/kernel/random").mkdir(parents=True)
    (proc_root / "sys/kernel/random/boot_id").write_text("01234567-89ab-cdef-0123-456789abcdef\n")
    class FakePopen:
        def __init__(self, argv, **_kwargs):
            self.pid, self.returncode = 3501, None
            self.stdout, self.stderr = BytesIO(), BytesIO()
            self.stdin = None
            self._done = threading.Event()
            Path(argv[argv.index("--full-log") + 1]).write_text("native-shaped output")
        def poll(self):
            return self.returncode
        def wait(self, timeout=None):
            if not self._done.wait(timeout):
                raise subprocess.TimeoutExpired("sockperf", timeout)
            return self.returncode
        def terminate(self):
            self.returncode = -15
            self._done.set()
        def kill(self):
            self.terminate()
    monkeypatch.setattr("cloud_network_benchmark.measurement_process.subprocess.Popen", FakePopen)
    progress = GuestObserverProgress(workspace / ".observer-progress.json", deployment_id="run-aws-deployment")
    runner = LocalMeasurementProcesses(SystemMeasurementClock(), observer_callback=progress, proc_root=proc_root)
    csv = workspace / "measurements" / "probe.csv"
    csv.parent.mkdir()
    request = CommandRequest(action_id="f03-sockperf-probe", action_type="measurement",
        argv=[str(executable), "--full-log", str(csv)], cwd=str(workspace), timeout_seconds=10)
    handle = runner.start(request, stdout_path=workspace / "out", stderr_path=workspace / "err")
    deadline = time.monotonic() + 2
    while not progress.path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    snapshot = json.loads(progress.path.read_text())
    assert snapshot["producer"]["pid"] == 3501
    assert snapshot["sockperf_chunks"] == [{"path": "measurements/probe.csv", "state": "current"}]
    assert csv.stat().st_size > 0
    return runner, handle, snapshot, progress, proc_root, executable, workspace


def test_actual_receipt_and_runtime_records_are_hash_pinned_and_consumable(tmp_path, monkeypatch):
    entries = [_participant("e1", "aws", "run-aws"), _participant("e1", "gcp", "run-gcp"),
               _participant("e2", "azure", "run-azure")]
    process_runner, process_handle, progress, _progress_writer, _proc_root, _executable, _workspace = _actual_sockperf_progress(tmp_path, monkeypatch)
    entries[0]["roles"]["vm_a"]["processes"] = [progress["producer"],
        entries[0]["roles"]["vm_a"]["processes"][1]]
    entries[0]["roles"]["vm_a"]["files"] = [
        {"kind": "sockperf_chunk", **progress["sockperf_chunks"][0]},
        entries[0]["roles"]["vm_a"]["files"][1]]
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
    result = observe_bindings(stage, binding_path, now=time.time(), probe=_probe,
                              run=lambda *_a, **_k: pytest.fail("unexpected SSH"))
    assert result["health"] == "healthy"
    assert len(result["entries"]) == 3
    process_runner.stop(process_handle, time.monotonic() + 2)


def test_production_process_hook_publishes_atomic_observed_rotation(tmp_path, monkeypatch):
    from cloud_network_benchmark.contracts.execution import CommandRequest

    runner, handle, first, progress, proc_root, executable, workspace = _actual_sockperf_progress(tmp_path, monkeypatch)
    runner.stop(handle, time.monotonic() + 2)
    closed = json.loads(progress.path.read_text())
    assert closed["producer"] is None
    assert closed["sockperf_chunks"] == [{"path": "measurements/probe.csv", "state": "completed"}]
    stat_path = proc_root / "3501/stat"
    stat_path.write_text("3501 (sockperf fake) S " + "1 " * 18 + "7654322\n")
    second_csv = workspace / "measurements" / "probe-002.csv"
    second = CommandRequest(action_id="f03-sockperf-probe-2", action_type="measurement",
        argv=[str(executable), "--full-log", str(second_csv)], cwd=str(workspace), timeout_seconds=10)
    next_handle = runner.start(second, stdout_path=workspace / "out-2", stderr_path=workspace / "err-2")
    rotated = json.loads(progress.path.read_text())
    assert rotated["producer"]["start_ticks"] == 7654322
    assert rotated["sockperf_chunks"] == [
        {"path": "measurements/probe.csv", "state": "completed"},
        {"path": "measurements/probe-002.csv", "state": "current"}]
    runner.stop(next_handle, time.monotonic() + 2)


def test_runtime_callback_aggregates_only_complete_owned_day_participants(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from cloud_network_benchmark.contracts.execution import CommandRequest
    runner, handle, progress, progress_writer, proc_root, executable, workspace_path = _actual_sockperf_progress(tmp_path, monkeypatch)
    stage_id, entry_id = "day-stage", "DAY"
    root = tmp_path / "stage"
    root.mkdir()
    bindings_path = root / "observer-bindings.json"
    launch_root = root / "launch-receipts"
    runtime_paths = {}
    run_ids = {provider: f"day-{provider}" for provider in ("aws", "azure", "gcp")}
    for provider in run_ids:
        runtime_path = root / f"{provider}.runtime.json"
        runtime_path.write_text(json.dumps({"access": {"bootstrap": {"workspace": WORKSPACE}}}))
        runtime_paths[provider] = runtime_path
    prepared_path = root / "prepared-stage.json"
    prepared_document = {"stage_id": stage_id, "campaign_id": stage_id,
        "stage": "day", "state_root": str(tmp_path), "entries": [{"entry_id": entry_id,
            "run_ids": run_ids, "runtime_config_path": str(runtime_paths["aws"])}],
        "observer_bindings_path": str(bindings_path),
        "observer_launch_receipt_root": str(launch_root)}
    from cloud_network_benchmark.campaign import _stage_digest
    frozen_stage_digest = _stage_digest(prepared_document)
    prepared_path.write_text(json.dumps(prepared_document))
    journal_path = root / "campaign-journal.json"
    journal_path.write_text(json.dumps({"stage_id": stage_id, "entries": {entry_id: {"state": "running"}}}))
    workspace = WORKSPACE
    reports = []
    callbacks = {}
    from cloud_network_benchmark.lifecycle_runtime import _observer_progress_callback
    for index, provider in enumerate(run_ids):
        run_id = run_ids[provider]
        deployment_id = run_id + "-deployment"
        def remote(role, suffix):
            return SimpleNamespace(host=f"192.0.2.{10 + index * 2 + suffix}", user="bench", port=22,
                authentication_reference=str(tmp_path / f"{provider}-identity"),
                host_key_reference=str(tmp_path / f"{provider}-known-hosts"))
        connection = SimpleNamespace(deployment_id=deployment_id, vm_a=remote("vm_a", 0),
            vm_b=remote("vm_b", 1))
        pair = SimpleNamespace(deployment_id=deployment_id,
            vm_a=SimpleNamespace(resource_id=f"{provider}-vm-a"),
            vm_b=SimpleNamespace(resource_id=f"{provider}-vm-b"))
        deployment = SimpleNamespace(provider=provider, run_id=run_id, deployment_id=deployment_id)
        listener = SimpleNamespace(tool="iperf3", role="vm_b", state="active", pid=4100 + index,
            boot_id=BOOT, start_ticks=5100 + index,
            stdout_path=f"{WORKSPACE}/iperf3/server.jsonl")
        bootstrap = SimpleNamespace(listeners=[listener])
        diagnostics = {role: SimpleNamespace(deployment_id=deployment_id, role=role,
            pid=4200 + index * 2 + (role == "vm_b"), boot_id=BOOT,
            start_ticks=5200 + index * 2 + (role == "vm_b")) for role in ("vm_a", "vm_b")}
        config = {"stage_id": stage_id, "campaign_id": stage_id, "entry_id": entry_id,
            "workspace": workspace, "state_path": str(root / "observer-state.json"),
            "journal_path": str(journal_path), "prepared_stage_path": str(prepared_path),
            "bindings_path": str(bindings_path), "launch_receipt_root": str(launch_root)}
        callback = _observer_progress_callback(config)
        assert callable(callback)
        reports.append(callback(deployment, pair, connection, bootstrap, diagnostics, progress))
        callbacks[provider] = (callback, deployment, pair, connection, bootstrap, diagnostics)
    assert [item["status"] for item in reports] == ["incomplete", "incomplete", "published"], reports
    stage = json.loads(prepared_path.read_text())
    assert _stage_digest(stage) == frozen_stage_digest
    def probe(_role, payload):
        return {"processes": {item["kind"]: {"pid": item["pid"], "boot_id": item["boot_id"],
            "start_ticks": item["start_ticks"], "alive": True, "state": "S"}
            for item in payload["processes"]},
            "files": {item["path"]: {"size": 16, "mtime": time.time() - 1}
                for item in payload["files"]}}
    observed = observe_bindings(stage, bindings_path, now=time.time(), probe=probe)
    assert observed["health"] == "healthy"
    assert len(observed["entries"]) == 3

    runner.stop(handle, time.monotonic() + 2)
    (proc_root / "3501/stat").write_text("3501 (sockperf fake) S " + "1 " * 18 + "7654322\n")
    rotated_command = CommandRequest(action_id="f03-sockperf-rotate", action_type="measurement",
        argv=[str(executable), "--full-log", str(workspace_path / "measurements/probe-002.csv")],
        cwd=str(workspace_path), timeout_seconds=10)
    rotated_handle = runner.start(rotated_command, stdout_path=workspace_path / "out-rotate",
        stderr_path=workspace_path / "err-rotate")
    rotated_progress = json.loads(progress_writer.path.read_text())
    callback, deployment, pair, connection, bootstrap, diagnostics = callbacks["aws"]
    second = callback(deployment, pair, connection, bootstrap, diagnostics, rotated_progress)
    assert second["status"] == "published"
    refreshed_stage = json.loads(prepared_path.read_text())
    refreshed = observe_bindings(refreshed_stage, bindings_path, now=time.time(), probe=probe)
    assert refreshed["health"] == "healthy"
    aws_binding = next(item for item in json.loads(bindings_path.read_text())["entries"]
        if item["provider"] == "aws")
    aws_producer = next(item for item in aws_binding["roles"]["vm_a"]["processes"]
        if item["kind"] == "producer")
    assert aws_producer["start_ticks"] == 7654322

    receipt_path = Path(refreshed_stage["observer_launch_receipt_path"])
    tampered = json.loads(receipt_path.read_text())
    first_owned = next(iter(tampered["participants"].values()))
    first_owned["vm_a"]["vm_id"] = "foreign-vm"
    receipt_path.write_text(json.dumps(tampered, sort_keys=True, separators=(",", ":")) + "\n")
    runner.stop(rotated_handle, time.monotonic() + 2)
    (proc_root / "3501/stat").write_text("3501 (sockperf fake) S " + "1 " * 18 + "7654323\n")
    third_command = CommandRequest(action_id="f03-sockperf-rotate-3", action_type="measurement",
        argv=[str(executable), "--full-log", str(workspace_path / "measurements/probe-003.csv")],
        cwd=str(workspace_path), timeout_seconds=10)
    third_handle = runner.start(third_command, stdout_path=workspace_path / "out-rotate-3",
        stderr_path=workspace_path / "err-rotate-3")
    third_progress = json.loads(progress_writer.path.read_text())
    rejected = callback(deployment, pair, connection, bootstrap, diagnostics, third_progress)
    assert rejected["status"] == "incomplete" and rejected["error_type"] == "ValueError"
    from concurrent.futures import ThreadPoolExecutor
    from cloud_network_benchmark.observer_publisher import invalidate_runtime_observer_snapshot
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _index: invalidate_runtime_observer_snapshot(config), range(3)))
    invalidated_stage = json.loads(prepared_path.read_text())
    assert hashlib.sha256(bindings_path.read_bytes()).hexdigest() == invalidated_stage["observer_bindings_sha256"]
    invalidated = observe_bindings(invalidated_stage, bindings_path, now=time.time(), probe=probe)
    assert invalidated == {"health": "unknown", "errors": ["active_guest_binding_set_mismatch"]}
    runner.stop(third_handle, time.monotonic() + 2)

def test_guest_progress_rejects_partial_or_inconsistent_rotation():
    from cloud_network_benchmark.observer_publisher import validate_guest_progress
    valid = {"deployment_id": "deploy", "producer": {"pid": 301, "boot_id": BOOT,
        "start_ticks": 990}, "sockperf_chunks": [{"path": "measurements/current.csv", "state": "current"}]}
    normalized = validate_guest_progress(valid, deployment_id="deploy")
    assert normalized["producer"] == {"kind": "producer", **valid["producer"]}
    with pytest.raises(ValueError, match="inconsistent"):
        validate_guest_progress({**valid, "producer": None}, deployment_id="deploy")
    with pytest.raises(ValueError, match="inconsistent"):
        validate_guest_progress({**valid, "sockperf_chunks": [
            {"path": "measurements/one.csv", "state": "current"},
            {"path": "measurements/two.csv", "state": "current"}]}, deployment_id="deploy")
    with pytest.raises(ValueError, match="deployment/schema"):
        validate_guest_progress(valid, deployment_id="foreign")


def test_f02_listener_process_receipt_parses_real_identity_and_rejects_reuse():
    from cloud_network_benchmark.bootstrap import _process_identity_receipt
    output = ("/opt/cnb/bin/iperf3\n" + "a" * 64 + " /proc/501/exe\n" +
              json.dumps({"pid": 501, "boot_id": BOOT, "start_ticks": 12001}))
    assert _process_identity_receipt(output, "/opt/cnb/bin/iperf3", "a" * 64) == {
        "pid": 501, "boot_id": BOOT, "start_ticks": 12001}
    assert _process_identity_receipt(output.replace('"pid": 501', '"pid": 502'),
                                     "/opt/cnb/bin/iperf3", "a" * 64) == {
        "pid": 502, "boot_id": BOOT, "start_ticks": 12001}
    assert _process_identity_receipt(output.replace('"start_ticks": 12001', '"start_ticks": 0'),
                                     "/opt/cnb/bin/iperf3", "a" * 64) is None


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
