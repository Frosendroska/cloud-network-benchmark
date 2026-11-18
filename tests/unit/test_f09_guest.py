from __future__ import annotations

from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path

import pytest

from cloud_network_benchmark.contracts import IdentityContext
from cloud_network_benchmark.lifecycle_guest import GuestRequest, execute_request, package_tree_sha256
from cloud_network_benchmark.lifecycle_adapters import guest_duration
from f03_support import FakeClock, FakeProcesses, observation


def _request(repository_root, tmp_path):
    resolved = observation(repository_root, "acceptance")
    identity = IdentityContext(experiment_id=resolved.experiment_id, config_id=resolved.config_id,
        campaign_id="f09-guest", observation_id="f09-guest-aws-observation",
        run_id="f09-guest-aws", deployment_id="f09-guest-aws-deployment", provider="aws")
    package = tmp_path / "package"; package.mkdir(); (package / "sample.py").write_text("# staged\n")
    root = tmp_path / "result"; root.mkdir()
    clock = FakeClock()
    return GuestRequest(schema_version=2, identity=identity, observation=resolved,
        b_private_ipv4="10.20.30.40", output_root=root,
        effective_deadline_utc=clock.utc_now()+timedelta(seconds=400),
        safe_remaining_seconds=400, clock_uncertainty_seconds=1,
        source_revision="a"*40, expected_source_revision="a"*40,
        package_root=package, package_sha256=package_tree_sha256(package),
        shutdown_grace_seconds=1), clock


def test_guest_calls_existing_f03_runner_with_guest_clock_and_keeps_identity(repository_root, tmp_path):
    request, clock = _request(repository_root, tmp_path)
    processes = FakeProcesses(clock)
    result = execute_request(request, processes=processes, clock=clock)
    assert result.identity == request.identity
    assert result.outcome == "succeeded"
    assert result.executed_unit_ids == [unit.unit_id for unit in request.observation.measurement_units]
    assert all(process.request.action_type == "measurement" for process in processes.handles)
    assert result.package_sha256 == request.package_sha256


def test_guest_fails_closed_on_package_digest_or_revision_mismatch(repository_root, tmp_path):
    request, clock = _request(repository_root, tmp_path)
    processes = FakeProcesses(clock)
    bad = request.model_dump(mode="python") | {"package_sha256": "0"*64}
    with pytest.raises(ValueError, match="digest"):
        execute_request(GuestRequest.model_validate(bad), processes=processes, clock=clock)
    assert processes.handles == []
    with pytest.raises(ValueError, match="revision"):
        GuestRequest.model_validate(request.model_dump(mode="python") | {"source_revision": "b"*40})


def test_package_tree_digest_is_stable_and_rejects_symlinks(tmp_path):
    root = tmp_path / "pkg"; root.mkdir(); (root / "module.py").write_bytes(b"actual source")
    first = package_tree_sha256(root)
    assert first == hashlib.sha256((root / "module.py").read_bytes()).hexdigest() or len(first) == 64
    cache = root / "__pycache__"; cache.mkdir(); (cache / "module.cpython-311.pyc").write_bytes(b"runtime bytecode")
    assert package_tree_sha256(root) == first
    target = tmp_path / "target.py"; target.write_text("not package content")
    (root / "alias.py").symlink_to(target)
    with pytest.raises(ValueError, match="symlinks"):
        package_tree_sha256(root)


def test_guest_duration_uses_utc_and_subtracts_transport_and_clock_uncertainty():
    from datetime import datetime, timezone
    now = datetime(2026, 10, 23, tzinfo=timezone.utc)
    assert guest_duration(effective_deadline_utc=now+timedelta(seconds=100), guest_now_utc=now,
        controller_observed_remaining=120, transport_elapsed_seconds=10,
        clock_uncertainty_seconds=2) == 98
    with pytest.raises(TimeoutError):
        guest_duration(effective_deadline_utc=now+timedelta(seconds=5), guest_now_utc=now,
            controller_observed_remaining=5, transport_elapsed_seconds=4,
            clock_uncertainty_seconds=2)


def test_remote_measurement_deadline_enforces_stage_allowance_and_later_stage_reserves():
    from types import SimpleNamespace
    from cloud_network_benchmark.lifecycle_remote import _measurement_stage_window

    allowance = SimpleNamespace(measurement_seconds=100, measurement_shutdown_seconds=15,
        retrieval_seconds=45, cleanup_seconds=120)
    # The stage window is tighter than the remaining whole-workflow cap.
    window = _measurement_stage_window(overall_deadline=1000, measurement_started=50,
        allowance=allowance)
    assert window.execution_deadline_monotonic == 150
    assert window.shutdown_deadline_monotonic == 165
    # A near cap still reserves retrieval and cleanup even when it is tighter.
    window = _measurement_stage_window(overall_deadline=220, measurement_started=50,
        allowance=allowance)
    assert window.execution_deadline_monotonic == 40
    assert window.shutdown_deadline_monotonic == 55


def test_remote_pair_preparation_composes_bootstrap_readiness_and_guest_staging(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from cloud_network_benchmark import access
    from cloud_network_benchmark.lifecycle_adapters import prepare_remote_pair
    called = []
    monkeypatch.setattr(access, "prepare", lambda *a, **k: called.append("bootstrap") or
                        SimpleNamespace(succeeded=True))
    monkeypatch.setattr(access, "check_readiness", lambda *a, **k: called.append("readiness") or
                        SimpleNamespace(ready=True))
    monkeypatch.setattr("cloud_network_benchmark.lifecycle_adapters.stage_guest_runtime",
                        lambda *a, **k: called.append("guest") or SimpleNamespace(package_sha256="a"*64))
    outcome = prepare_remote_pair(object(), bootstrap_config=object(), readiness_config=object(),
        runner=object(), deadline=object(), source_root=tmp_path,
        source_revision="a"*40, remote_root="/tmp/cnb")
    assert outcome.stage == "prepare" and outcome.succeeded is True
    assert called == ["bootstrap", "readiness", "guest"]
    called.clear()
    monkeypatch.setattr(access, "check_readiness", lambda *a, **k: called.append("readiness") or
                        SimpleNamespace(ready=False))
    outcome = prepare_remote_pair(object(), bootstrap_config=object(), readiness_config=object(),
        runner=object(), deadline=object(), source_root=tmp_path,
        source_revision="a"*40, remote_root="/tmp/cnb")
    assert outcome.succeeded is False and called == ["bootstrap", "readiness"]


def test_remote_f03_handoff_uses_staged_module_and_validates_downloaded_identity(repository_root, tmp_path):
    import json
    from datetime import datetime, timezone
    from cloud_network_benchmark.lifecycle_adapters import GuestRuntimeEvidence, run_remote_f03
    from cloud_network_benchmark.lifecycle_guest import GuestOutcome
    from tests.fixtures.f02r.support import connection, deadline, result

    connected = connection(tmp_path)
    base, _clock = _request(repository_root, tmp_path)
    runtime_root = f"/var/lib/cnb/runtime/{connected.deployment_id}/runtime"
    source = repository_root / "src/cloud_network_benchmark"
    digest = package_tree_sha256(source)
    package_root = Path(runtime_root) / "source/cloud_network_benchmark"
    request = type(base).model_validate({**base.model_dump(mode="python"),
        "identity": connected.identity.model_dump(mode="python"),
        "observation": {**base.observation.model_dump(mode="python"), "provider": "aws"},
        "output_root": f"{runtime_root}/results", "package_root": str(package_root),
        "source_revision": "a"*40, "expected_source_revision": "a"*40,
        "package_sha256": digest})
    runtime = GuestRuntimeEvidence("a"*40, digest, "b"*64, runtime_root,
        "3.12.3", "2.14.0", "6.0.2", ())
    class Runner:
        def __init__(self, cancel_event): self.actions=[]; self.request_payload=None; self.outcome_payload=None; self.cancel_event=cancel_event
        def run(self, command):
            self.actions.append(command)
            if command.action_id.startswith("f09-guest-request-"):
                self.request_payload = json.loads(Path(command.argv[-2]).read_text())
            elif command.action_id.startswith("f09-guest-run-"):
                assert "cloud_network_benchmark.lifecycle_guest --request" in command.argv[-1]
                assert command.timeout_seconds == min(command.timeout_seconds,
                    deadline().overall_deadline_monotonic - 100)
                row = self.request_payload
                invocation = command.action_id.removeprefix("f09-guest-run-")
                self.outcome_payload = GuestOutcome(identity=row["identity"], outcome="succeeded",
                    executed_unit_ids=[item["unit_id"] for item in row["observation"]["measurement_units"]],
                    unexecuted_unit_ids=[], commands=[], timings=[], artifact_paths=[],
                    source_revision=row["source_revision"], package_sha256=row["package_sha256"],
                    guest_started_at=datetime.now(timezone.utc), guest_finished_at=datetime.now(timezone.utc)).model_dump_json()
                return result(command.action_id, json.dumps({"deployment_id": connected.deployment_id,
                    "invocation_id": invocation, "pid": 25001, "process_group": 25001,
                    "session_id": 25001, "boot_id": "synthetic-boot", "start_ticks": 12345}))
            elif command.action_id.startswith("f09-guest-status-"):
                # Cancellation racing with actual guest completion preserves
                # the authenticated terminal status and retrieves its output.
                self.cancel_event.set()
                return result(command.action_id, "exited 0")
            elif command.action_id.startswith("f09-guest-outcome-"):
                Path(command.argv[-1]).write_text(self.outcome_payload)
            return result(command.action_id)
    import threading
    cancel_event = threading.Event()
    runner=Runner(cancel_event)
    report=run_remote_f03(connected, request, runner=runner, runtime=runtime,
        deadline=deadline(), clock=lambda:100, cancel_event=cancel_event)
    assert report.failure and "raced with natural guest exit" in report.failure
    assert report.outcome.outcome == "succeeded" and report.outcome_source_bytes
    assert [item.action_type for item in report.actions] == ["ssh", "scp", "ssh", "ssh", "scp"]
    assert not any(item.action_id.startswith("f09-guest-cancel-") for item in report.actions)
    assert report.outcome.identity == request.identity
    from cloud_network_benchmark.orchestration import _typed_outcome
    assert _typed_outcome("aws", "measure", report)[0] is False


def test_remote_f03_handoff_bounds_staging_polling_and_owned_stop_to_measurement_window(repository_root, tmp_path):
    import json
    from datetime import datetime, timedelta, timezone
    from cloud_network_benchmark.lifecycle_adapters import GuestRuntimeEvidence, run_remote_f03
    from cloud_network_benchmark.lifecycle_guest import package_tree_sha256
    from cloud_network_benchmark.lifecycle_remote import _measurement_stage_window
    from tests.fixtures.f02r.support import connection, deadline, result

    connected = connection(tmp_path)
    base, _ = _request(repository_root, tmp_path)
    runtime_root = f"/var/lib/cnb/runtime/{connected.deployment_id}/runtime"
    digest = package_tree_sha256(repository_root / "src/cloud_network_benchmark")
    now = datetime.now(timezone.utc)
    window = _measurement_stage_window(overall_deadline=300, measurement_started=100,
        allowance=type("Allowance", (), {"measurement_seconds": 1.5,
            "measurement_shutdown_seconds": 1.0, "retrieval_seconds": 45.0,
            "cleanup_seconds": 120.0})())
    request = type(base).model_validate({**base.model_dump(mode="python"),
        "identity": connected.identity.model_dump(mode="python"),
        "observation": {**base.observation.model_dump(mode="python"), "provider": "aws"},
        "output_root": f"{runtime_root}/results",
        "package_root": f"{runtime_root}/source/cloud_network_benchmark",
        "source_revision": "a"*40, "expected_source_revision": "a"*40,
        "package_sha256": digest,
        "effective_deadline_utc": now + timedelta(seconds=window.shutdown_deadline_monotonic - 100),
        "safe_remaining_seconds": window.shutdown_deadline_monotonic - 100,
        "clock_uncertainty_seconds": 0.0})
    runtime = GuestRuntimeEvidence("a"*40, digest, "b"*64, runtime_root,
        "3.12.3", "2.14.0", "6.0.2", ())
    current = [100.0]

    class Runner:
        def __init__(self): self.requests=[]; self.request_payload=None
        def run(self, command):
            self.requests.append(command)
            if command.action_id.startswith("f09-guest-request-"):
                self.request_payload = json.loads(Path(command.argv[-2]).read_text())
            if command.action_id.startswith("f09-guest-run-"):
                assert command.timeout_seconds <= 1.5
                invocation = command.action_id.removeprefix("f09-guest-run-")
                return result(command.action_id, json.dumps({"deployment_id":connected.deployment_id,
                    "invocation_id":invocation,"pid":25001,"process_group":25001,
                    "session_id":25001,"boot_id":"synthetic-boot","start_ticks":12345}))
            if command.action_id.startswith("f09-guest-status-"):
                assert command.timeout_seconds <= max(0.0, window.execution_deadline_monotonic-current[0])
                return result(command.action_id, "running")
            if command.action_id.startswith("f09-guest-cancel-"):
                assert command.timeout_seconds <= 1.0
                return result(command.action_id, "process_group_stopped")
            if command.action_id.startswith("f09-guest-outcome-"):
                return result(command.action_id, success=False, stderr="outcome not returned")
            return result(command.action_id)

    runner = Runner()
    report = run_remote_f03(connected, request, runner=runner, runtime=runtime,
        deadline=deadline(), clock=lambda:current[0],
        active_deadline_monotonic=window.execution_deadline_monotonic,
        shutdown_deadline_monotonic=window.shutdown_deadline_monotonic,
        sleep=lambda seconds: current.__setitem__(0, current[0] + seconds))
    assert runner.request_payload["safe_remaining_seconds"] == 2.5
    assert runner.request_payload["effective_deadline_utc"] == request.effective_deadline_utc.isoformat().replace("+00:00", "Z")
    assert any(item.action_id.startswith("f09-guest-cancel-") for item in runner.requests)
    assert "F03 active cap expired; process_group_stopped" in report.failure
    assert all(item.timeout_seconds <= 1.5 for item in runner.requests
        if item.action_id.startswith(("f09-guest-control-mkdir-", "f09-guest-request-", "f09-guest-run-", "f09-guest-status-")))


@pytest.mark.parametrize(("source_case", "verified_stop"), [
    ("partial", True), ("missing", True), ("malformed", True),
    ("wrong_identity", True), ("partial", False), ("nonzero_exit", True),
], ids=["partial-source", "missing-source", "malformed-source", "wrong-identity",
    "unverified-stop", "nonzero-terminal-status"])
def test_remote_f03_preserves_partial_outcome_only_after_verified_stop(
        repository_root, tmp_path, source_case, verified_stop):
    import json
    from datetime import datetime, timedelta, timezone
    from cloud_network_benchmark.contracts.execution import CommandResult
    from cloud_network_benchmark.contracts.measurements import CommandEvidence, MeasurementTiming
    from cloud_network_benchmark.lifecycle_adapters import GuestRuntimeEvidence, run_remote_f03
    from cloud_network_benchmark.lifecycle_guest import GuestOutcome, package_tree_sha256
    from tests.fixtures.f02r.support import connection, deadline, result

    connected = connection(tmp_path)
    base, _clock = _request(repository_root, tmp_path)
    runtime_root = f"/var/lib/cnb/runtime/{connected.deployment_id}/runtime"
    digest = package_tree_sha256(repository_root / "src/cloud_network_benchmark")
    request = type(base).model_validate({**base.model_dump(mode="python"),
        "identity": connected.identity.model_dump(mode="python"),
        "observation": {**base.observation.model_dump(mode="python"), "provider": "aws"},
        "output_root": f"{runtime_root}/results",
        "package_root": f"{runtime_root}/source/cloud_network_benchmark",
        "source_revision": "a"*40, "expected_source_revision": "a"*40,
        "package_sha256": digest})
    runtime = GuestRuntimeEvidence("a"*40, digest, "b"*64, runtime_root,
        "3.12.3", "2.14.0", "6.0.2", ())
    unit = request.observation.measurement_units[0]
    started = datetime.now(timezone.utc)
    action_id = f"sockperf-{unit.unit_id}"
    command = CommandEvidence(schema_version=2,
        request={"action_id": action_id, "argv": ["sockperf", "ping-pong", "-t", "10"]},
        result={"outcome": "succeeded"}, producer="vm_a", deployment_id=connected.deployment_id,
        unit_id=unit.unit_id, binary_version={"value": "sockperf-1"},
        source_identity={"value": "synthetic partial guest source"},
        monotonic_started={"value": 10.0}, monotonic_finished={"value": 11.0},
        started_at={"value": started}, finished_at={"value": started + timedelta(seconds=1)})
    timing = MeasurementTiming(unit_id=unit.unit_id, deployment_id=connected.deployment_id,
        probe_test_started_at={"value": started}, tcp_started_at={"unavailable_reason": "idle unit"},
        measured_started_at={"value": started}, measured_finished_at={"value": started + timedelta(seconds=1)},
        monotonic_started={"value": 10.0}, monotonic_finished={"value": 11.0}, omitted_seconds=0,
        overlap_seconds={"unavailable_reason": "partial interruption fixture"})
    partial = GuestOutcome(identity=request.identity, outcome="failed",
        executed_unit_ids=[unit.unit_id],
        unexecuted_unit_ids=[item.unit_id for item in request.observation.measurement_units[1:]],
        commands=[command.model_dump(mode="json")], timings=[timing.model_dump(mode="json")],
        artifact_paths=[], failures=[{"category": "controller_interruption",
            "message": "interrupted after first completed unit", "occurred_at": started}],
        source_revision=runtime.source_revision, package_sha256=runtime.package_sha256,
        guest_started_at=started, guest_finished_at=started + timedelta(seconds=1))
    raw_partial = partial.model_dump_json().encode("utf-8")
    if source_case == "wrong_identity":
        changed = request.identity.model_copy(update={"run_id": "different-run"})
        raw_partial = partial.model_copy(update={"identity": changed}).model_dump_json().encode("utf-8")

    class Runner:
        def __init__(self, cancellation): self.requests = []; self.cancellation = cancellation
        def run(self, command):
            self.requests.append(command)
            if command.action_id.startswith("f09-guest-run-"):
                assert "setsid sh -c" in command.argv[-1]
                invocation = command.action_id.removeprefix("f09-guest-run-")
                return result(command.action_id, json.dumps({"deployment_id": connected.deployment_id,
                    "invocation_id": invocation, "pid": 25001, "process_group": 25001,
                    "session_id": 25001, "boot_id": "synthetic-boot", "start_ticks": 12345}))
            if command.action_id.startswith("f09-guest-status-"):
                if source_case == "nonzero_exit":
                    return result(command.action_id, "exited 1")
                self.cancellation.set()
                return result(command.action_id, "running")
            if command.action_id.startswith("f09-guest-cancel-"):
                assert "b64decode" in command.argv[-1]
                return result(command.action_id,
                    "process_group_stopped" if verified_stop else "cancellation_unverified")
            if command.action_id.startswith("f09-guest-outcome-"):
                if source_case == "missing":
                    now = datetime.now(timezone.utc)
                    return CommandResult(action_id=command.action_id, outcome="failed",
                        started_at=now, finished_at=now, duration_seconds=0, exit_code=1,
                        stdout="", stderr="outcome absent")
                Path(command.argv[-1]).write_bytes(b"not-json\n" if source_case == "malformed" else raw_partial)
            return result(command.action_id)

    import threading
    cancellation = threading.Event()
    runner = Runner(cancellation)
    report = run_remote_f03(connected, request, runner=runner, runtime=runtime,
        deadline=deadline(), clock=lambda:100, cancel_event=cancellation, sleep=lambda _seconds: None)
    if source_case == "nonzero_exit":
        assert "guest exited with status 1" in report.failure
    else:
        assert "controller cancellation" in report.failure
    if verified_stop:
        assert any(item.action_id.startswith("f09-guest-outcome-") for item in report.actions)
        if source_case in {"partial", "nonzero_exit"}:
            assert report.outcome is not None and report.outcome.outcome == "failed"
            assert report.outcome.executed_unit_ids == [unit.unit_id]
            assert report.outcome.commands and report.outcome.timings
            assert report.outcome_source_bytes == raw_partial
            assert "partial guest outcome retained" in report.failure
        else:
            assert report.outcome is None
            assert "partial guest outcome unavailable" in report.failure
            if source_case == "malformed":
                assert report.outcome_source_bytes == b"not-json\n"
            if source_case == "wrong_identity":
                assert report.outcome_source_bytes == raw_partial
    else:
        assert report.outcome is None and report.outcome_source_bytes is None
        assert "verified closure was not established" in report.failure
        assert not any(item.action_id.startswith("f09-guest-outcome-") for item in report.actions)
    action_names = [item.action_id.split("-")[2] for item in report.actions if item.action_id.startswith("f09-guest-")]
    assert action_names[:5] == ["control", "request", "run", "status", "cancel"]
    if verified_stop:
        assert action_names[-1] == "outcome"
    cancel = next(item for item in report.actions if item.action_id.startswith("f09-guest-cancel-"))
    assert cancel.result["stdout"] == ("process_group_stopped" if verified_stop else "cancellation_unverified")


def test_f03_scoped_stop_reaps_owned_session_when_original_leader_has_exited(tmp_path):
    """Exercise the production stop script against a real local session, offline."""
    import json
    import os
    import shlex
    import subprocess
    import sys
    from datetime import datetime, timezone
    from cloud_network_benchmark.contracts.execution import CommandResult
    from cloud_network_benchmark.lifecycle_adapters import _stop_remote_f03
    from tests.fixtures.f02r.support import connection, deadline

    if not Path("/proc/self/stat").is_file():
        pytest.skip("the local test host has no Linux /proc process identity interface")
    connected = connection(tmp_path)
    # Hold the leader until its identity and a real child are observed. Avoid a
    # race between the original immediate exit and reading /proc/getpgid.
    leader = subprocess.Popen([sys.executable, "-c",
        "import os,sys,time\nchild=os.fork()\nif child:\n print(child,flush=True); sys.stdin.readline(); os._exit(0)\nelse: time.sleep(30)"],
        start_new_session=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    child_pid = int(leader.stdout.readline())
    assert Path(f"/proc/{child_pid}/stat").is_file()
    pid = leader.pid
    stat_path = Path(f"/proc/{pid}/stat")
    import time
    for _ in range(100):
        try:
            stat_raw = stat_path.read_text()
            break
        except FileNotFoundError:
            time.sleep(.01)
    tail = stat_raw[stat_raw.rfind(")") + 2:].split()
    record = {"deployment_id": connected.deployment_id, "invocation_id": "owned-child-session",
        "pid": pid, "process_group": os.getpgid(pid), "session_id": os.getsid(pid),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "start_ticks": int(tail[19])}
    process_path = tmp_path / "owned-child-session.process.json"
    process_path.write_text(json.dumps(record, sort_keys=True))
    leader.stdin.write("exit\n"); leader.stdin.flush(); leader.stdin.close()
    leader.wait(timeout=2)
    leader.stdout.close()
    assert not stat_path.exists(), "test precondition: session leader has exited"
    class LocalScopedRunner:
        def run(self, command):
            remote = shlex.split(command.argv[-1])
            assert remote[:2] == ["sh", "-c"]
            tokens = shlex.split(remote[2])
            assert tokens[:2] == ["python3", "-c"]
            completed = subprocess.run([sys.executable, "-c", tokens[2], *tokens[3:]],
                capture_output=True, text=True, timeout=8)
            now = datetime.now(timezone.utc)
            return CommandResult(action_id=command.action_id,
                outcome="succeeded" if completed.returncode == 0 else "failed",
                started_at=now, finished_at=now, duration_seconds=0,
                exit_code=completed.returncode, stdout=completed.stdout,
                stderr=completed.stderr)
    actions = []
    try:
        status = _stop_remote_f03(connected, record, record["invocation_id"],
            process_path=str(process_path), runner=LocalScopedRunner(), deadline=deadline(),
            clock=lambda:100, actions=actions)
        assert status == "process_group_stopped", actions[-1].result
        assert actions and "process_group_stopped" in actions[-1].result["stdout"]
        try:
            raw = Path(f"/proc/{child_pid}/stat").read_text()
        except FileNotFoundError:
            pass
        else:
            assert raw[raw.rfind(")") + 2:].split()[0] in {"Z", "X"}
    finally:
        try:
            os.killpg(pid, 9)
        except ProcessLookupError:
            pass


def test_f03_dash_status_probe_tracks_running_terminal_and_rejects_absent_or_malformed(
        tmp_path):
    """Run the production status probe through Ubuntu's /bin/sh (dash) semantics."""
    import os
    import shutil
    import subprocess
    from cloud_network_benchmark.lifecycle_adapters import (
        _f03_guest_status_command, _parse_f03_guest_status,
    )

    shell = shutil.which("dash")
    if shell is None:
        pytest.skip("dash is unavailable on the local test host")
    process = subprocess.Popen(["sleep", "30"], start_new_session=True)
    status_path = tmp_path / "f03.status"
    command = _f03_guest_status_command(str(status_path), process.pid)

    def poll():
        completed = subprocess.run([shell, "-c", command], capture_output=True,
            text=True, timeout=3)
        assert completed.returncode == 0, completed.stderr
        return completed.stdout.strip()

    try:
        assert os.getpgid(process.pid) == process.pid
        # Reproduce the former production spelling: dash hides its rejected
        # `--` operand inside the conditional and misreports this live group.
        former = subprocess.run([shell, "-c", command.replace("kill -0 -", "kill -0 -- -")],
            capture_output=True, text=True, timeout=3)
        assert former.returncode == 0 and former.stdout.strip() == "absent"
        assert _parse_f03_guest_status(poll()) is None

        # An explicit wrapper status remains terminal even after its process exits.
        status_path.write_text("0\n")
        os.killpg(process.pid, 15)
        process.wait(timeout=3)
        assert _parse_f03_guest_status(poll()) == 0

        # A dead group without the wrapper's numeric status must remain a failure.
        status_path.unlink()
        absent = poll()
        assert absent == "absent"
        with pytest.raises(RuntimeError, match="without an authenticated terminal status"):
            _parse_f03_guest_status(absent)

        status_path.write_text("not-a-number\n")
        malformed = poll()
        assert malformed == "exited not-a-number"
        with pytest.raises(ValueError, match="exit status is malformed"):
            _parse_f03_guest_status(malformed)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, 9)
            process.wait(timeout=3)


def test_remote_f03_runs_production_guest_wrapper_to_its_terminal_outcome(
        repository_root, tmp_path):
    """Run the generated wrapper and real guest module with local fake tools."""
    import json
    import os
    import shlex
    import shutil
    import subprocess
    import sys
    import time
    from datetime import datetime, timedelta, timezone
    from cloud_network_benchmark.lifecycle_adapters import (
        GuestRuntimeEvidence, _stop_remote_f03, run_remote_f03,
    )
    from cloud_network_benchmark.access import RemoteDeadline
    from cloud_network_benchmark.lifecycle_guest import GuestOutcome, package_tree_sha256
    from tests.fixtures.f02r.support import connection, result

    base, _clock = _request(repository_root, tmp_path)
    assert base.observation.benchmark.measured_seconds == 10
    assert base.observation.benchmark.warmup_seconds == 5
    assert [(unit.phase, unit.measured_seconds)
            for unit in base.observation.measurement_units] == [
                ("idle_latency", 10), ("single_flow", 10), ("multi_flow", 10)]
    now_utc = datetime.now(timezone.utc)
    command_shape = GuestOutcome(identity=base.identity, outcome="failed",
        executed_unit_ids=[],
        unexecuted_unit_ids=[unit.unit_id for unit in base.observation.measurement_units],
        commands=[{"request": {"argv": ["sockperf", "under-load", "-t", "16"]}}],
        timings=[], artifact_paths=[], source_revision=base.source_revision,
        package_sha256=base.package_sha256, guest_started_at=now_utc,
        guest_finished_at=now_utc)
    assert command_shape.commands[0]["request"]["argv"][-2:] == ["-t", "16"]

    if not Path("/proc/self/stat").is_file() or shutil.which("setsid") is None:
        pytest.skip("the generated F03 launcher requires Linux /proc and setsid")

    connected = connection(tmp_path)
    connected = connected.model_copy(update={
        "identity": base.identity, "run_id": base.identity.run_id,
        "deployment_id": base.identity.deployment_id,
    })
    remote_root = tmp_path / "guest-root"
    package_root = remote_root / "source" / "cloud_network_benchmark"
    package_root.parent.mkdir(parents=True)
    shutil.copytree(repository_root / "src/cloud_network_benchmark", package_root)
    python = remote_root / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} \"$@\"\n")
    python.chmod(0o755)
    digest = package_tree_sha256(package_root)
    request = GuestRequest.model_validate({**base.model_dump(mode="python"),
        "identity": connected.identity.model_dump(mode="python"),
        "observation": base.observation.model_dump(mode="python"),
        "output_root": str(remote_root / "results"), "package_root": str(package_root),
        "effective_deadline_utc": datetime.now(timezone.utc) + timedelta(seconds=45),
        "safe_remaining_seconds": 45,
        "source_revision": "a" * 40, "expected_source_revision": "a" * 40,
        "package_sha256": digest})
    runtime = GuestRuntimeEvidence("a" * 40, digest, "b" * 64,
        str(remote_root), "3.12.3", "2.14.0", "6.0.2", ())

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    # The valid acceptance fixture has 10 measured seconds, 5 seconds warmup,
    # and uses 15-second idle / 20-second loaded Sockperf probes. Fail its
    # first controlled probe so this transport test closes before later units.
    (fake_bin / "sockperf").write_text(
        "#!/usr/bin/env python3\nimport sys, time\n"
        "print('Starting test...', flush=True)\ntime.sleep(.5)\nsys.exit(7)\n")
    (fake_bin / "iperf3").write_text(
        "#!/usr/bin/env python3\nimport json\n"
        "print(json.dumps({'start': {'test_start': {'omit': 5}}, "
        "'server_output_json': {'end': {'sum_received': {'seconds': 15, 'bytes': 1}}}}))\n")
    for binary in (fake_bin / "sockperf", fake_bin / "iperf3"):
        binary.chmod(0o755)
    local_env = os.environ | {"PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"}
    request.output_root.mkdir(parents=True)

    class LocalGuestRunner:
        def __init__(self):
            self.actions = []

        def run(self, command):
            self.actions.append(command)
            action = command.action_id
            if action.startswith("f09-guest-control-mkdir-"):
                completed = subprocess.run(["/bin/sh", "-c", command.argv[-1]],
                    capture_output=True, text=True, env=local_env, timeout=5)
                return result(action, completed.stdout, completed.returncode == 0, completed.stderr)
            if action.startswith("f09-guest-request-"):
                source = Path(command.argv[-2])
                destination = Path(command.argv[-1].split(":", 1)[1])
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                return result(action)
            if (action.startswith("f09-guest-run-") or action.startswith("f09-guest-status-")
                    or action.startswith("f09-guest-cancel-")):
                completed = subprocess.run(["/bin/sh", "-c", command.argv[-1]],
                    capture_output=True, text=True, env=local_env, timeout=8)
                return result(action, completed.stdout, completed.returncode == 0, completed.stderr)
            if action.startswith("f09-guest-outcome-"):
                source = Path(command.argv[-2].split(":", 1)[1])
                shutil.copyfile(source, Path(command.argv[-1]))
                return result(action)
            raise AssertionError(f"unexpected local guest command: {action}")

    runner = LocalGuestRunner()
    start_monotonic = time.monotonic()
    start_utc = datetime.now(timezone.utc)
    controller_deadline = RemoteDeadline(
        readiness_deadline_monotonic=start_monotonic + 45,
        overall_deadline_monotonic=start_monotonic + 45,
        cleanup_reserve_seconds=5, cap_started_at=start_utc,
        cap_expires_at=start_utc + timedelta(seconds=45))
    try:
        report = run_remote_f03(connected, request, runner=runner, runtime=runtime,
            deadline=controller_deadline, clock=time.monotonic)

        assert report.failure is None, report.failure
        assert report.outcome is not None and report.outcome.outcome == "failed"
        assert report.outcome.identity == request.identity
        assert report.outcome_source_bytes
        assert [(unit.phase, unit.measured_seconds)
                for unit in request.observation.measurement_units] == [
                    ("idle_latency", 10), ("single_flow", 10), ("multi_flow", 10)]
        assert report.outcome.commands and report.outcome.failures
        assert report.outcome.commands[0]["request"]["argv"][-2:] == ["-t", "16"]
        status_actions = [item for item in report.actions if "f09-guest-status-" in item.action_id]
        assert any(item.result["stdout"].strip() == "running" for item in status_actions)
        assert status_actions[-1].result["stdout"].strip() == "exited 0"
        assert any("f09-guest-outcome-" in item.action_id for item in report.actions)
        status_file = next(remote_root.glob("control/*.status"))
        assert status_file.read_text().strip() == "0"
    finally:
        process_files = list(remote_root.glob("control/*.process.json"))
        if process_files:
            process_path = process_files[0]
            process_record = json.loads(process_path.read_text(encoding="utf-8"))
            cleanup_actions = []
            cleanup_started = time.monotonic()
            cleanup_utc = datetime.now(timezone.utc)
            cleanup_deadline = RemoteDeadline(
                readiness_deadline_monotonic=cleanup_started + 10,
                overall_deadline_monotonic=cleanup_started + 10,
                cleanup_reserve_seconds=1, cap_started_at=cleanup_utc,
                cap_expires_at=cleanup_utc + timedelta(seconds=10))
            stopped = _stop_remote_f03(connected, process_record,
                process_record["invocation_id"], process_path=str(process_path),
                runner=runner, deadline=cleanup_deadline,
                clock=time.monotonic, actions=cleanup_actions)
            assert stopped == "process_group_stopped", cleanup_actions[-1].result


def test_f03_scoped_stop_refuses_reused_pid_start_ticks(tmp_path):
    import json
    import os
    import shlex
    import subprocess
    import sys
    from datetime import datetime, timezone
    from cloud_network_benchmark.contracts.execution import CommandResult
    from cloud_network_benchmark.lifecycle_adapters import _stop_remote_f03
    from tests.fixtures.f02r.support import connection, deadline

    if not Path("/proc/self/stat").is_file():
        pytest.skip("the local test host has no Linux /proc process identity interface")
    connected = connection(tmp_path)
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"], start_new_session=True)
    raw = Path(f"/proc/{process.pid}/stat").read_text()
    tail = raw[raw.rfind(")") + 2:].split()
    record = {"deployment_id": connected.deployment_id, "invocation_id": "wrong-start-ticks",
        "pid": process.pid, "process_group": os.getpgid(process.pid), "session_id": os.getsid(process.pid),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "start_ticks": int(tail[19]) + 1}
    process_path = tmp_path / "wrong-start-ticks.process.json"
    process_path.write_text(json.dumps(record, sort_keys=True))
    class LocalScopedRunner:
        def run(self, command):
            remote = shlex.split(command.argv[-1])
            assert remote[:2] == ["sh", "-c"]
            tokens = shlex.split(remote[2])
            completed = subprocess.run([sys.executable, "-c", tokens[2], *tokens[3:]],
                capture_output=True, text=True, timeout=3)
            now = datetime.now(timezone.utc)
            return CommandResult(action_id=command.action_id,
                outcome="succeeded" if completed.returncode == 0 else "failed",
                started_at=now, finished_at=now, duration_seconds=0,
                exit_code=completed.returncode, stdout=completed.stdout,
                stderr=completed.stderr)
    actions = []
    try:
        status = _stop_remote_f03(connected, record, record["invocation_id"],
            process_path=str(process_path), runner=LocalScopedRunner(), deadline=deadline(),
            clock=lambda:100, actions=actions)
        assert status == "cancellation_unverified"
        assert process.poll() is None, "mismatched start ticks must not signal a reused PID"
    finally:
        os.killpg(process.pid, 9)
        process.wait(timeout=3)


def test_guest_sigterm_bridge_raises_interrupt_and_restores_original_handler(
        repository_root, tmp_path, monkeypatch, capsys):
    import signal
    from cloud_network_benchmark import lifecycle_guest

    request, _clock = _request(repository_root, tmp_path)
    request_path = tmp_path / "request.json"
    request_path.write_text(request.model_dump_json())
    prior = signal.getsignal(signal.SIGTERM)
    def send_sigterm(_request):
        signal.raise_signal(signal.SIGTERM)
    monkeypatch.setattr(lifecycle_guest, "execute_request", send_sigterm)
    status = lifecycle_guest.main(["--request", str(request_path),
        "--outcome", str(tmp_path / "outcome.json")])
    assert status == 1
    assert "measurement interrupted" in capsys.readouterr().err
    assert signal.getsignal(signal.SIGTERM) == prior


@pytest.mark.parametrize(("collector_state", "collector_failures", "paths_complete", "expected_state"), [
    ("interrupted", [], True, "closed"),
    ("completed", [], True, "closed"),
    ("failed", [], True, "failed"),
    ("completed", [{"category": "snapshot", "message": "failed"}], True, "failed"),
    ("completed", [], False, "failed"),
])
def test_remote_f04_collector_is_started_and_stopped_by_exact_guest_process_identity(
        tmp_path, collector_state, collector_failures, paths_complete, expected_state):
    import json
    import os
    import shlex
    import signal
    import subprocess
    import sys
    from datetime import datetime, timezone
    from cloud_network_benchmark.lifecycle_adapters import start_remote_diagnostics, stop_remote_diagnostics
    from tests.fixtures.f02r.support import connection, deadline, result
    from tests.integration.test_diagnostics_collection import seed

    connected = connection(tmp_path)
    metadata = seed("vm_b")
    metadata["deployment_id"] = connected.deployment_id
    context = {"deployment_id": connected.deployment_id, "vm_role": "vm_b", "provider": "aws",
        "interface": "eth0", "output_root": "/var/lib/cnb/work", "duration_seconds": 60,
        "metadata_seed": metadata}
    boot_id = "12345678-1234-1234-1234-123456789abc"

    class Runner:
        def __init__(self): self.requests = []; self.start_request = None
        def run(self, request):
            self.requests.append(request)
            if request.action_id.startswith("f09-diagnostics-start-"):
                self.start_request = request
                assert "setsid python3" in request.argv[-1]
                assert "process_group" in request.argv[-1] and "/proc/sys/kernel/random/boot_id" in request.argv[-1]
                return result(request.action_id, json.dumps({"pid": 4321, "process_group": 4321,
                    "boot_id": boot_id, "start_ticks": 7654}))
            if request.action_id.startswith("f09-diagnostics-stop-"):
                assert "killpg" in request.argv[-1] and "SIGTERM" in request.argv[-1]
                assert "4321" in request.argv[-1] and boot_id in request.argv[-1]
                assert "pkill" not in request.argv[-1]
                import shlex
                import ast
                remote = shlex.split(request.argv[-1])
                python = shlex.split(remote[2])
                outer = ast.parse(python[2])
                compile(ast.literal_eval(outer.body[0].value.args[0]), "remote-diagnostics-stop", "exec")
                return result(request.action_id, json.dumps({"state":"stopped",
                    "closed_at_utc":datetime.now(timezone.utc).isoformat().replace("+00:00","Z")}))
            if request.action_id.startswith("f09-diagnostics-outcome-"):
                return result(request.action_id, json.dumps({"deployment_id":connected.deployment_id,
                    "vm_role":"vm_b", "state":collector_state, "failures":collector_failures,
                    "sample_count":114, "mapping_count":115,
                    "artifacts":{"metadata":"/var/lib/cnb/work/metadata/vm_b.json",
                        "diagnostics":"/var/lib/cnb/work/diagnostics/vm_b.jsonl",
                        "clock":"/var/lib/cnb/work/diagnostics/vm_b-clock.jsonl"},
                    "closed_paths":[
                        "/var/lib/cnb/work/metadata/vm_b.json",
                        "/var/lib/cnb/work/diagnostics/vm_b.jsonl",
                        "/var/lib/cnb/work/diagnostics/vm_b-clock.jsonl"] if paths_complete else
                        ["/var/lib/cnb/work/metadata/vm_b.json"]}))
            return result(request.action_id)

    runner = Runner()
    process = start_remote_diagnostics(connected, context, runner=runner,
        remote_root=f"/var/lib/cnb/control/{connected.deployment_id}", deadline=deadline(), clock=lambda:100)
    assert process.role == "vm_b" and process.pid == process.process_group == 4321
    assert process.boot_id == boot_id and process.start_ticks == 7654

    # Compile the exact identity reporter placed in the generated remote argv.
    generated_argv = shlex.split(shlex.split(runner.start_request.argv[-1])[2])
    reporter_indexes = [index for index in range(len(generated_argv) - 1)
        if generated_argv[index:index + 2] == ["python3", "-c"]]
    assert len(reporter_indexes) == 1
    reporter_index = reporter_indexes[0]
    identity_reporter = generated_argv[reporter_index + 2]
    assert "process_group" in identity_reporter
    assert "/proc/sys/kernel/random/boot_id" in identity_reporter
    assert "start_ticks" in identity_reporter
    compile(identity_reporter, "generated-f04-process-identity", "exec")
    if sys.platform == "linux":
        # Exercise that exact generated reporter against a short-lived local process.
        local = subprocess.Popen(["/bin/sleep", "30"], start_new_session=True)
        try:
            identity_path = tmp_path / "local-process-identity.json"
            observed = subprocess.run([sys.executable, "-c", identity_reporter,
                str(local.pid), str(identity_path)], capture_output=True, text=True,
                check=True, timeout=3)
            actual = json.loads(observed.stdout)
            written_text = identity_path.read_text(encoding="utf-8")
            written = json.loads(written_text)
            stat_tail = Path(f"/proc/{local.pid}/stat").read_text().split(")", 1)[1].split()
            assert actual == written
            assert written_text.endswith("\n")
            assert actual == {"pid": local.pid, "process_group": local.pid,
                "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                "start_ticks": int(stat_tail[19])}
        finally:
            try:
                os.killpg(local.pid, signal.SIGTERM)
                local.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(local.pid, signal.SIGKILL)
                local.wait(timeout=2)
    closure = stop_remote_diagnostics(connected, process, runner=runner,
        deadline=deadline(), clock=lambda:100)
    assert closure.state == expected_state
    assert closure.closed_at is not None
    assert closure.outcome["state"] == collector_state and len(closure.outcome["closed_paths"]) == (3 if paths_complete else 1)
    assert bool(closure.failures) == (expected_state == "failed")
    assert [item.action_id.split("-")[2] for item in runner.requests if item.action_id.startswith("f09-diagnostics-")] == [
        "mkdir", "helper", "context", "start", "stop", "outcome"]


def test_vm_b_listener_closure_stops_selected_systemd_units_and_observes_closed_logs(tmp_path):
    from cloud_network_benchmark.lifecycle_adapters import stop_vm_b_listeners
    from tests.fixtures.f02r.support import connection, deadline, result

    connected = connection(tmp_path)
    class Runner:
        def __init__(self): self.requests = []
        def run(self, request):
            self.requests.append(request)
            if request.action_id.startswith("f09-listener-owner-process-"):
                tool = "sockperf" if request.action_id.endswith("-sockperf") else "iperf3"
                return result(request.action_id, json.dumps({"pid":4242,"start_ticks":12345,
                        "boot_id":"12345678-1234-1234-1234-123456789abc",
                        "cgroup":f"/system.slice/cnb-{connected.deployment_id}-{tool}.service",
                        "log_ids":[[1,2],[1,3]]}))
            if request.action_id.startswith("f09-listener-closed-files-"):
                return result(request.action_id, json.dumps({"closed_at_utc":"2026-11-17T15:00:00+00:00"}))
            if request.action_id.startswith("f09-listener-owner-"):
                tool = "sockperf" if request.action_id.endswith("-sockperf") else "iperf3"
                unit = f"cnb-{connected.deployment_id}-{tool}"
                stdout = "sockperf.log" if tool == "sockperf" else "iperf3-server.jsonl"
                stderr = "sockperf.err" if tool == "sockperf" else "iperf3-server.err"
                return result(request.action_id, "LoadState=loaded\nActiveState=active\nMainPID=4242\n"
                    f"ControlGroup=/system.slice/{unit}.service\n"
                    "StandardOutput=append\nStandardError=append\n")
            if request.action_id.startswith("f09-listener-config-"):
                tool = "sockperf" if request.action_id.endswith("-sockperf") else "iperf3"
                stdout = "sockperf.log" if tool == "sockperf" else "iperf3-server.jsonl"
                stderr = "sockperf.err" if tool == "sockperf" else "iperf3-server.err"
                return result(request.action_id,
                    "# /run/systemd/transient/cnb.service\n[Service]\n"
                    f"StandardOutput=append:/var/lib/cnb/results/server-logs/{stdout}\n"
                    f"StandardError=append:/var/lib/cnb/results/server-logs/{stderr}\n")
            return result(request.action_id)
    runner = Runner()
    closure = stop_vm_b_listeners(connected, runner=runner, deadline=deadline(), clock=lambda:100)
    assert closure.closed and closure.closed_at is not None, (closure.observations, closure.failures,
        [(x.action_id, x.result.get("outcome"), x.result.get("stdout")) for x in closure.actions])
    assert set(closure.observations) == {"sockperf", "iperf3"}
    stops = [item.argv[-1] for item in runner.requests if "systemctl stop" in item.argv[-1]]
    assert len(stops) == 2
    assert all(connected.deployment_id in item for item in stops)
    assert all("pkill" not in item for item in stops)


def test_vm_b_listener_closure_fails_closed_when_unit_is_still_active(tmp_path):
    from cloud_network_benchmark.lifecycle_adapters import stop_vm_b_listeners
    from tests.fixtures.f02r.support import connection, deadline, result
    connected = connection(tmp_path)
    class Runner:
        def run(self, request):
            if request.action_id.startswith("f09-listener-owner-process-"):
                return result(request.action_id, json.dumps({"pid":4242,"start_ticks":12345,
                    "boot_id":"12345678-1234-1234-1234-123456789abc"}))
            if request.action_id.startswith("f09-listener-owner-"):
                tool = "sockperf" if request.action_id.endswith("-sockperf") else "iperf3"
                unit = f"cnb-{connected.deployment_id}-{tool}"
                stdout = "sockperf.log" if tool == "sockperf" else "iperf3-server.jsonl"
                stderr = "sockperf.err" if tool == "sockperf" else "iperf3-server.err"
                return result(request.action_id, "LoadState=loaded\nActiveState=active\nMainPID=4242\n"
                    f"ControlGroup=/system.slice/{unit}.service\n"
                    f"StandardOutput=append:/var/lib/cnb/results/server-logs/{stdout}\n"
                    f"StandardError=append:/var/lib/cnb/results/server-logs/{stderr}\n")
            return result(request.action_id)
    closure = stop_vm_b_listeners(connected, runner=Runner(), deadline=deadline(), clock=lambda:100)
    assert not closure.closed and closure.closed_at is None
    assert len(closure.failures) == 2


@pytest.mark.parametrize("case", ["wrong-path", "cat-denied", "override"])
def test_vm_b_listener_closure_requires_effective_exact_cat_log_paths(tmp_path, case):
    from cloud_network_benchmark.lifecycle_adapters import stop_vm_b_listeners
    from tests.fixtures.f02r.support import connection, deadline, result
    connected = connection(tmp_path)
    class Runner:
        def __init__(self): self.requests = []
        def run(self, request):
            self.requests.append(request)
            if request.action_id.startswith("f09-listener-owner-"):
                tool = "sockperf" if request.action_id.endswith("-sockperf") else "iperf3"
                unit = f"cnb-{connected.deployment_id}-{tool}"
                return result(request.action_id, "LoadState=loaded\nActiveState=active\nMainPID=4242\n"
                    f"ControlGroup=/system.slice/{unit}.service\n"
                    "StandardOutput=append\nStandardError=append\n")
            if request.action_id.startswith("f09-listener-config-"):
                if case == "cat-denied":
                    return result(request.action_id, "", success=False, stderr="permission denied")
                tool = "sockperf" if request.action_id.endswith("-sockperf") else "iperf3"
                stdout = "sockperf.log" if tool == "sockperf" else "iperf3-server.jsonl"
                stderr = "sockperf.err" if tool == "sockperf" else "iperf3-server.err"
                config = ("# /run/systemd/transient/cnb.service\n[Service]\n"
                    f"StandardOutput=append:/var/lib/cnb/results/server-logs/{stdout}\n"
                    f"StandardError=append:/var/lib/cnb/results/server-logs/{stderr}\n")
                if case == "wrong-path":
                    config = config.replace(f"{stdout}", "foreign.log")
                if case == "override":
                    config += ("# /etc/systemd/system/cnb.service.d/override.conf\n[Service]\n"
                        "StandardError=append:/var/lib/cnb/results/server-logs/foreign.err\n")
                return result(request.action_id, config)
            raise AssertionError("closure must reject before process inspection or stop")
    runner = Runner()
    closure = stop_vm_b_listeners(connected, runner=runner, deadline=deadline(), clock=lambda:100)
    assert not closure.closed and closure.closed_at is None
    assert len([request for request in runner.requests if "systemctl stop" in request.argv[-1]]) == 0
    assert not any(request.action_id.startswith("f09-listener-owner-process-") for request in runner.requests)


@pytest.mark.skipif(not Path("/proc/self/stat").exists() or os.geteuid() != 0,
    reason="emitted listener closure proof requires Linux /proc and privileged descriptor visibility")
def test_emitted_listener_closure_script_checks_live_writer_and_closed_process(tmp_path):
    import shlex
    import subprocess
    import sys
    from cloud_network_benchmark.lifecycle_adapters import stop_vm_b_listeners
    from tests.fixtures.f02r.support import connection, deadline, result

    connected = connection(tmp_path)
    result_root = tmp_path / "results"
    logs = result_root / "server-logs"
    logs.mkdir(parents=True)
    paths = [logs / "sockperf.log", logs / "sockperf.err"]
    for path in paths: path.write_bytes(b"")

    def run_case(*, leave_child):
        source = "import os,sys,time; [open(p,'ab') for p in sys.argv[1:3]]; "
        source += ("pid=os.fork(); "
            "(time.sleep(30),os._exit(0)) if pid==0 else (print(pid,flush=True),time.sleep(30))"
            if leave_child else "time.sleep(30)")
        process = subprocess.Popen([sys.executable, "-c", source, *(str(path) for path in paths)],
            stdout=subprocess.PIPE, text=True)
        child_pid = int(process.stdout.readline()) if leave_child else None
        stat = Path(f"/proc/{process.pid}/stat").read_text(); ticks = int(stat[stat.rfind(")")+2:].split()[19])
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        log_ids = [[path.stat().st_dev, path.stat().st_ino] for path in paths]
        unit = f"cnb-{connected.deployment_id}-sockperf"
        cgroup = f"/sys/fs/cgroup/cnb-closure-test-{process.pid}.service"

        class Runner:
            def __init__(self): self.requests=[]
            def run(self, request):
                self.requests.append(request)
                aid = request.action_id
                if aid.startswith("f09-listener-owner-"):
                    return result(aid, "LoadState=loaded\nActiveState=active\nMainPID="+str(process.pid)+"\n"
                        f"ControlGroup=/system.slice/{unit}.service\n"
                        "StandardOutput=append\nStandardError=append\n")
                if aid.startswith("f09-listener-config-"):
                    return result(aid, "# /run/systemd/transient/cnb.service\n[Service]\n"
                        f"StandardOutput=append:{paths[0]}\nStandardError=append:{paths[1]}\n")
                if aid.startswith("f09-listener-owner-process-"):
                    return result(aid, json.dumps({"pid":process.pid,"start_ticks":ticks,
                        "boot_id":boot,"cgroup":f"/system.slice/{unit}.service","log_ids":log_ids}))
                if aid.startswith("f09-listener-stop-"):
                    process.terminate(); process.wait(timeout=3)
                    if leave_child: return result(aid)
                    return result(aid)
                return result(aid)

        # Run the generated guest closure program locally against controlled processes.
        class ExecutingRunner(Runner):
            def run(self, request):
                aid=request.action_id
                if aid.startswith("f09-listener-closed-files-"):
                    remote=shlex.split(request.argv[-1])
                    completed=subprocess.run([sys.executable,*remote[2:]],capture_output=True,text=True,check=False)
                    return result(aid,completed.stdout,success=completed.returncode==0,stderr=completed.stderr)
                return super().run(request)
        closure = stop_vm_b_listeners(connected, runner=ExecutingRunner(), results_workspace=str(result_root),
            deadline=deadline(), clock=lambda:100)
        if child_pid is not None:
            try: os.kill(child_pid, 9)
            except ProcessLookupError: pass
        return closure

    rejected = run_case(leave_child=True)
    assert not rejected.closed and any("descriptors remain open" in item for item in rejected.failures)
    accepted = run_case(leave_child=False)
    assert accepted.closed and accepted.closed_at is not None


def test_remote_workspace_inventory_is_hash_bound_bounded_and_symlink_failing(tmp_path):
    import ast
    import hashlib
    import json
    import shlex
    from cloud_network_benchmark.lifecycle_adapters import inventory_remote_workspace
    from tests.fixtures.f02r.support import connection, deadline, result
    connected = connection(tmp_path)
    rows = [{"path":"measurements/unit/native.log", "size_bytes":7,
        "sha256":hashlib.sha256(b"native\n").hexdigest()}]
    class Runner:
        def __init__(self, payload): self.payload=payload; self.requests=[]
        def run(self, request):
            self.requests.append(request)
            python = shlex.split(shlex.split(request.argv[-1])[2])
            outer = ast.parse(python[2])
            compile(ast.literal_eval(outer.body[0].value.args[0]), "remote-inventory", "exec")
            return result(request.action_id, json.dumps({"files":self.payload}))
    runner = Runner(rows)
    inventory = inventory_remote_workspace(connected, role="vm_a", workspace="/var/lib/cnb/results",
        runner=runner, deadline=deadline(), clock=lambda:100, expected_paths=["measurements/unit/native.log"])
    assert inventory.failures == () and inventory.files == tuple(rows)
    assert inventory.files[0]["sha256"] == hashlib.sha256(b"native\n").hexdigest()
    malformed = Runner([{**rows[0], "sha256":"bad"}])
    rejected = inventory_remote_workspace(connected, role="vm_a", workspace="/var/lib/cnb/results",
        runner=malformed, deadline=deadline(), clock=lambda:100, expected_paths=["measurements/unit/native.log"])
    assert rejected.files == () and rejected.failures


def test_actual_staged_workspace_is_separate_from_strict_declared_native_inventory(tmp_path):
    import subprocess
    import sys
    from cloud_network_benchmark.lifecycle_adapters import inventory_remote_workspace
    from tests.fixtures.f02r.support import connection, deadline, result

    connected = connection(tmp_path)
    from cloud_network_benchmark.contracts.artifacts import ArtifactInput
    from cloud_network_benchmark.lifecycle_bundle import remote_inventory_paths_by_role
    from cloud_network_benchmark.contracts.common import ArtifactExpectation
    guest = tmp_path / "guest"
    work = guest / "work"
    results = guest / "results"
    (work / "runtime").mkdir(parents=True)
    subprocess.run([sys.executable, "-m", "venv", "--system-site-packages",
        str(work / "runtime" / "venv")], check=True)
    bootstrap_source = work / "sockperf-5ebd327da983225321818c0355db922515e026bd"
    bootstrap_source.mkdir(parents=True)
    (bootstrap_source / "generated-target").write_text("build artifact")
    (bootstrap_source / "generated-link").symlink_to("generated-target")
    (results / "measurements/unit").mkdir(parents=True)
    native = results / "measurements/unit/sockperf.csv"
    native.write_bytes(b"native closed result\n")
    assert any(path.is_symlink() for path in work.rglob("*")), "fixture must reproduce staged runtime/build symlinks"
    role_paths = remote_inventory_paths_by_role([
        ArtifactInput(artifact_id="vm-a-native", path="measurements/unit/sockperf.csv",
            kind="sockperf-csv", producer="vm_a", expectation=ArtifactExpectation.REQUIRED),
        ArtifactInput(artifact_id="vm-b-server-stderr", path="server-logs/sockperf.err",
            kind="sockperf-server-stderr", producer="vm_b", expectation=ArtifactExpectation.REQUIRED),
    ])

    class LocalGuestRunner:
        def __init__(self): self.requests=[]
        def run(self, request):
            self.requests.append(request)
            command = request.argv[-1]
            completed = subprocess.run(["sh", "-c", command], capture_output=True, text=True)
            return result(request.action_id, completed.stdout, success=completed.returncode == 0,
                stderr=completed.stderr)

    runner = LocalGuestRunner()
    inventory = inventory_remote_workspace(connected, role="vm_a", workspace=str(results),
        runner=runner, deadline=deadline(), clock=lambda:100,
        expected_paths=role_paths["vm_a"])
    assert inventory.failures == (), (runner.requests[0].argv, inventory.actions[0].result.get("stderr"))
    assert [row["path"] for row in inventory.files] == ["measurements/unit/sockperf.csv"]
    assert inventory.files[0]["size_bytes"] == len(native.read_bytes())
    wrong_role = results / "server-logs/sockperf.err"
    wrong_role.parent.mkdir(parents=True); wrong_role.write_bytes(b"wrong VM's declaration")
    rejected_role_file = inventory_remote_workspace(connected, role="vm_a", workspace=str(results),
        runner=LocalGuestRunner(), deadline=deadline(), clock=lambda:100,
        expected_paths=role_paths["vm_a"])
    assert rejected_role_file.files == () and any("undeclared" in item for item in rejected_role_file.failures), (rejected_role_file.failures,rejected_role_file.actions[0].result.get("stderr"))
    wrong_role.unlink()
    oversized = inventory_remote_workspace(connected, role="vm_a", workspace=str(results),
        runner=LocalGuestRunner(), deadline=deadline(), clock=lambda:100, maximum_bytes=4,
        expected_paths=role_paths["vm_a"])
    assert oversized.files == () and oversized.failures

    (results / "measurements/unit/foreign.txt").write_text("not declared")
    unknown = inventory_remote_workspace(connected, role="vm_a", workspace=str(results),
        runner=LocalGuestRunner(), deadline=deadline(), clock=lambda:100,
        expected_paths=role_paths["vm_a"])
    assert unknown.files == () and unknown.failures
    (results / "measurements/unit/foreign.txt").unlink()
    (results / "measurements/unit/escape").symlink_to(native)
    symlink = inventory_remote_workspace(connected, role="vm_a", workspace=str(results),
        runner=LocalGuestRunner(), deadline=deadline(), clock=lambda:100,
        expected_paths=role_paths["vm_a"])
    assert symlink.files == () and symlink.failures


def test_stage_guest_runtime_uses_trusted_scp_and_verifies_digest_revision_and_dependencies(
        repository_root, tmp_path):
    import json
    import tarfile
    from cloud_network_benchmark.lifecycle_adapters import stage_guest_runtime
    from tests.fixtures.f02r.support import connection, deadline, result

    connected = connection(tmp_path)
    class Runner:
        def __init__(self): self.requests = []
        def run(self, request):
            self.requests.append(request)
            if request.action_id.startswith("f09-runtime-mkdir-"):
                return result(request.action_id)
            if request.action_type == "scp":
                archive = Path(request.argv[-2])
                with tarfile.open(archive, "r:gz") as tar:
                    names = tar.getnames()
                    assert "runtime-source-revision.txt" in names
                    assert "source/cloud_network_benchmark/lifecycle_guest.py" in names
                    assert tar.extractfile("runtime-source-revision.txt").read() == b"a" * 40 + b"\n"
                assert request.argv[-1].endswith("/runtime-source.tar.gz")
                return result(request.action_id)
            assert request.action_type == "ssh"
            assert "sha256sum" in request.argv[-1] and "python3 -m venv" in request.argv[-1]
            assert "-m pip install" in request.argv[-1]
            assert "pydantic>=2.0,<3" in request.argv[-1] and "PyYAML>=6.0,<7" in request.argv[-1]
            return result(request.action_id, json.dumps({"python":"3.11.13", "pydantic":"2.14.0", "yaml":"6.0.2"}))
    runner = Runner()
    evidence = stage_guest_runtime(connected, source_root=repository_root, source_revision="a" * 40,
        remote_root=f"/var/lib/cnb/runtime/{connected.deployment_id}", runner=runner,
        deadline=deadline(), clock=lambda:100)
    assert evidence.source_revision == "a" * 40
    assert evidence.python_version == "3.11.13" and evidence.pydantic_version == "2.14.0"
    assert evidence.yaml_version == "6.0.2" and len(evidence.archive_sha256) == 64
    assert [request.action_type for request in runner.requests] == ["ssh", "scp", "ssh"]
    assert "mkdir -p" in runner.requests[0].argv[-1]
    assert all(request.redactions for request in runner.requests)


def test_stage_guest_runtime_fails_closed_when_transfer_or_readiness_budget_fails(repository_root, tmp_path):
    from cloud_network_benchmark.lifecycle_adapters import stage_guest_runtime
    from tests.fixtures.f02r.support import connection, deadline, result

    connected = connection(tmp_path)
    class Runner:
        def __init__(self): self.requests = []
        def run(self, request):
            self.requests.append(request)
            if request.action_id.startswith("f09-runtime-mkdir-"):
                return result(request.action_id)
            return result(request.action_id, success=False, stderr="synthetic failure")
    runner = Runner()
    with pytest.raises(RuntimeError, match="archive transfer"):
        stage_guest_runtime(connected, source_root=repository_root, source_revision="a" * 40,
            remote_root=f"/var/lib/cnb/runtime/{connected.deployment_id}", runner=runner,
            deadline=deadline(), clock=lambda:100)
    assert len(runner.requests) == 2
    runner = Runner()
    with pytest.raises(TimeoutError, match="readiness remainder"):
        stage_guest_runtime(connected, source_root=repository_root, source_revision="a" * 40,
            remote_root=f"/var/lib/cnb/runtime/{connected.deployment_id}", runner=runner,
            deadline=deadline(), clock=lambda:200)
    assert runner.requests == []


def test_staged_runtime_import_execution_does_not_mutate_immutable_package_digest(tmp_path):
    import importlib.util
    import json
    import tarfile
    from cloud_network_benchmark.lifecycle_adapters import stage_guest_runtime
    from tests.fixtures.f02r.support import connection, deadline, result

    source_root = tmp_path / "source"
    package = source_root / "src" / "cloud_network_benchmark"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("def answer():\n    return 42\n")
    source_digest = package_tree_sha256(package)
    remote = tmp_path / "remote"
    connection_root = tmp_path / "connection"
    connection_root.mkdir()
    connected = connection(connection_root)

    class Runner:
        def __init__(self): self.requests = []
        def run(self, request):
            self.requests.append(request)
            if request.action_id.startswith("f09-runtime-mkdir-"):
                remote.mkdir(parents=True, exist_ok=True)
                return result(request.action_id)
            if request.action_type == "scp":
                archive_path = Path(request.argv[-2])
                runtime_root = remote / "runtime"
                runtime_root.mkdir(parents=True, exist_ok=True)
                with tarfile.open(archive_path, "r:gz") as archive:
                    archive.extractall(runtime_root)
                return result(request.action_id)
            source = remote / "runtime" / "source"
            spec = importlib.util.spec_from_file_location("f09_staged_package", source / "cloud_network_benchmark" / "__init__.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            assert module.answer() == 42
            return result(request.action_id, json.dumps({"python":"3.11.13", "pydantic":"2.14.0", "yaml":"6.0.2"}))

    stage_guest_runtime(connected, source_root=source_root, source_revision="a" * 40,
        remote_root="/var/lib/cnb/runtime/test", runner=Runner(), deadline=deadline(), clock=lambda:100)
    assert package_tree_sha256(package) == source_digest


@pytest.mark.parametrize("role", ["vm_a", "vm_b"])
def test_standalone_diagnostics_run_only_for_explicit_guest_role(tmp_path, role):
    from tests.integration.test_diagnostics_collection import FakeClock, seed, snapshot
    from cloud_network_benchmark.lifecycle_guest import run_local_diagnostics
    output = tmp_path / role; output.mkdir()
    context = {"deployment_id":"RUN-deployment", "vm_role":role, "provider":"aws", "interface":"eth0",
        "output_root":str(output), "duration_seconds":1, "metadata_seed":seed(role)}
    outcome = run_local_diagnostics(context, expected_deployment_id="RUN-deployment", expected_role=role,
        root=Path(__file__).parents[1] / "fixtures/f04", clock=FakeClock(),
        run_command=lambda *_:{"returncode":1,"stdout":"","stderr":"fixture"},
        snapshot_reader=lambda *_:snapshot(0), stop_requested=lambda:False)
    assert outcome["vm_role"] == role and outcome["state"] == "completed"
    assert (output / f"diagnostics/{role}.jsonl").is_file()
    with pytest.raises(ValueError, match="does not match"):
        run_local_diagnostics(context, expected_deployment_id="another-run", expected_role=role,
            root=Path(__file__).parents[1] / "fixtures/f04", clock=FakeClock(),
            run_command=lambda *_:{"returncode":1,"stdout":"","stderr":"fixture"},
            snapshot_reader=lambda *_:snapshot(0), stop_requested=lambda:False)


@pytest.mark.parametrize("case,expected,signals", [
    ("term", "process_group_stopped", [15]),
    ("kill", "process_group_stopped", [15, 9]),
    ("orphan", "process_group_stopped", [15]),
    ("zombie", "process_group_stopped", []),
    ("reused", "cancellation_unverified", []),
    ("boot", "cancellation_unverified", []),
    ("record", "cancellation_unverified", []),
    ("session", "cancellation_unverified", []),
    ("older_member", "cancellation_unverified", []),
    ("reused_during_term", "cancellation_unverified", [15]),
    ("never_exits", "cancellation_unverified", [15, 9]),
])
def test_f03_scoped_stop_payload_identity_zombies_and_bounded_escalation(
        tmp_path, monkeypatch, case, expected, signals):
    """Execute the exact shipped Python/argv with synthetic /proc on every OS."""
    import builtins
    import contextlib
    import io
    import json
    import os
    import shlex
    import sys
    import time
    from datetime import datetime, timezone
    from cloud_network_benchmark.contracts.execution import CommandResult
    from cloud_network_benchmark.lifecycle_adapters import _stop_remote_f03
    from tests.fixtures.f02r.support import connection, deadline

    connected = connection(tmp_path)
    record = {"deployment_id": connected.deployment_id, "invocation_id": "payload-test",
              "pid": 100, "process_group": 100, "session_id": 100,
              "boot_id": "test-boot", "start_ticks": 200}
    process_path = tmp_path / "process.json"
    process_path.write_text(json.dumps(record | ({"invocation_id": "other"} if case == "record" else {})))
    # PID, PGID, SID, start ticks, state. A child may remain after its leader exits.
    members = {100: [100, 100, 200, "S"], 101: [100, 100, 201, "S"]}
    if case == "orphan":
        del members[100]
    if case == "zombie":
        for row in members.values(): row[3] = "Z"
    if case == "reused": members[100][2] += 1
    if case == "session": members[101][1] = 99
    if case == "older_member": members[101][2] = 199
    observed_signals = []
    elapsed = [0.0]
    original_open, original_listdir = builtins.open, os.listdir

    def proc_open(path, *args, **kwargs):
        path = str(path)
        if path == "/proc/sys/kernel/random/boot_id":
            return io.StringIO("other-boot" if case == "boot" else "test-boot")
        if path.startswith("/proc/") and path.endswith("/stat"):
            pid = int(path.split("/")[2])
            pgid, sid, ticks, state = members[pid]
            tail = [state, "1", str(pgid), str(sid), *(["0"] * 15), str(ticks)]
            return io.StringIO(f"{pid} (synthetic name) " + " ".join(tail))
        return original_open(path, *args, **kwargs)

    def killpg(group, sig):
        assert group == 100
        observed_signals.append(int(sig))
        if case == "reused_during_term":
            members[100][2] += 1
        elif case != "never_exits" and (int(sig) == 9 or case != "kill"):
            for row in members.values(): row[3] = "Z"

    class PayloadRunner:
        def run(self, command):
            remote = shlex.split(command.argv[-1])
            assert remote[:2] == ["sh", "-c"]
            tokens = shlex.split(remote[2])
            assert tokens[:2] == ["python3", "-c"]
            stdout = io.StringIO()
            with monkeypatch.context() as patch, contextlib.redirect_stdout(stdout):
                patch.setattr(sys, "argv", ["-c", *tokens[3:]])
                patch.setattr(builtins, "open", proc_open)
                patch.setattr(os, "listdir", lambda path: list(map(str, members)) if path == "/proc" else original_listdir(path))
                patch.setattr(os, "killpg", killpg)
                patch.setattr(time, "monotonic", lambda: elapsed[0])
                patch.setattr(time, "sleep", lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds))
                error = ""
                try:
                    exec(tokens[2], {})
                    code = 0
                except SystemExit as exc:
                    code = exc.code if isinstance(exc.code, int) else 1
                    error = str(exc.code)
            now = datetime.now(timezone.utc)
            return CommandResult(action_id=command.action_id,
                outcome="succeeded" if code == 0 else "failed", started_at=now,
                finished_at=now + timedelta(seconds=elapsed[0]), duration_seconds=elapsed[0], exit_code=code,
                stdout=stdout.getvalue(), stderr=error)

    actions = []
    assert _stop_remote_f03(connected, record, record["invocation_id"],
        process_path=str(process_path), runner=PayloadRunner(), deadline=deadline(),
        clock=lambda: 100, actions=actions) == expected, json.dumps(actions[-1].result)
    assert observed_signals == signals, json.dumps(actions[-1].result)
    assert elapsed[0] <= 5.0
