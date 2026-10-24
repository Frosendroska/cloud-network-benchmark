from __future__ import annotations

from datetime import timedelta
import hashlib
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
        def __init__(self): self.actions=[]; self.request_payload=None; self.outcome_payload=None
        def run(self, command):
            self.actions.append(command)
            if command.action_id.startswith("f09-guest-request-"):
                self.request_payload = json.loads(Path(command.argv[-2]).read_text())
            elif command.action_id.startswith("f09-guest-run-"):
                assert "cloud_network_benchmark.lifecycle_guest --request" in command.argv[-1]
                assert command.timeout_seconds == deadline().overall_deadline_monotonic - 100
                row = self.request_payload
                self.outcome_payload = GuestOutcome(identity=row["identity"], outcome="succeeded",
                    executed_unit_ids=[item["unit_id"] for item in row["observation"]["measurement_units"]],
                    unexecuted_unit_ids=[], commands=[], timings=[], artifact_paths=[],
                    source_revision=row["source_revision"], package_sha256=row["package_sha256"],
                    guest_started_at=datetime.now(timezone.utc), guest_finished_at=datetime.now(timezone.utc)).model_dump_json()
            elif command.action_id.startswith("f09-guest-outcome-"):
                Path(command.argv[-1]).write_text(self.outcome_payload)
            return result(command.action_id)
    runner=Runner()
    report=run_remote_f03(connected, request, runner=runner, runtime=runtime,
        deadline=deadline(), clock=lambda:100)
    assert report.failure is None and report.outcome.outcome == "succeeded"
    assert [item.action_type for item in report.actions] == ["ssh", "scp", "ssh", "scp"]
    assert report.outcome.identity == request.identity
    from cloud_network_benchmark.orchestration import _typed_outcome
    assert _typed_outcome("aws", "measure", report)[0] is True


def test_remote_f03_cancels_and_verifies_exact_guest_group_after_ssh_interruption(repository_root, tmp_path):
    from datetime import datetime, timezone
    from cloud_network_benchmark.contracts.execution import CommandResult
    from cloud_network_benchmark.lifecycle_adapters import GuestRuntimeEvidence, run_remote_f03
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

    class Runner:
        def __init__(self): self.requests = []
        def run(self, command):
            self.requests.append(command)
            if command.action_id.startswith("f09-guest-run-"):
                assert "setsid sh -c" in command.argv[-1]
                now = datetime.now(timezone.utc)
                return CommandResult(action_id=command.action_id, outcome="interrupted",
                    started_at=now, finished_at=now, duration_seconds=0, exit_code=None,
                    stdout="", stderr="interrupted")
            if command.action_id.startswith("f09-guest-cancel-"):
                assert "kill -TERM" in command.argv[-1]
                assert "process_group_stopped" in command.argv[-1]
                return result(command.action_id, "process_group_stopped")
            return result(command.action_id)

    runner = Runner()
    report = run_remote_f03(connected, request, runner=runner, runtime=runtime,
        deadline=deadline(), clock=lambda:100)
    assert report.outcome is None and "F03 remote handoff failed" in report.failure
    assert [item.action_id.split("-")[2] for item in report.actions if item.action_id.startswith("f09-guest-")] == [
        "control", "request", "run", "cancel"]
    cancel = next(item for item in report.actions if item.action_id.startswith("f09-guest-cancel-"))
    assert cancel.result["stdout"] == "process_group_stopped"


def test_remote_f04_collector_is_started_and_stopped_by_exact_guest_process_identity(tmp_path):
    import json
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
        def __init__(self): self.requests = []
        def run(self, request):
            self.requests.append(request)
            if request.action_id.startswith("f09-diagnostics-start-"):
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
                    "vm_role":"vm_b", "state":"interrupted", "closed_paths":[
                        "/var/lib/cnb/work/metadata/vm_b.json",
                        "/var/lib/cnb/work/diagnostics/vm_b.jsonl",
                        "/var/lib/cnb/work/diagnostics/vm_b-clock.jsonl"]}))
            return result(request.action_id)

    runner = Runner()
    process = start_remote_diagnostics(connected, context, runner=runner,
        remote_root=f"/var/lib/cnb/control/{connected.deployment_id}", deadline=deadline(), clock=lambda:100)
    assert process.role == "vm_b" and process.pid == process.process_group == 4321
    assert process.boot_id == boot_id and process.start_ticks == 7654
    closure = stop_remote_diagnostics(connected, process, runner=runner,
        deadline=deadline(), clock=lambda:100)
    assert closure.state == "closed" and closure.closed_at is not None
    assert closure.outcome["state"] == "interrupted" and len(closure.outcome["closed_paths"]) == 3
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
            if request.action_id.startswith("f09-listener-exit-"):
                return result(request.action_id,
                    "LoadState=loaded\nActiveState=inactive\nMainPID=0\n"
                    "InactiveEnterTimestamp=Fri 2026-10-23 10:11:00 UTC\n")
            return result(request.action_id)
    runner = Runner()
    closure = stop_vm_b_listeners(connected, runner=runner, deadline=deadline(), clock=lambda:100)
    assert closure.closed and closure.closed_at is not None
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
            if request.action_id.startswith("f09-listener-exit-"):
                return result(request.action_id,
                    "LoadState=loaded\nActiveState=active\nMainPID=4321\n"
                    "InactiveEnterTimestamp=n/a\n")
            return result(request.action_id)
    closure = stop_vm_b_listeners(connected, runner=Runner(), deadline=deadline(), clock=lambda:100)
    assert not closure.closed and closure.closed_at is None
    assert len(closure.failures) == 2


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
    inventory = inventory_remote_workspace(connected, role="vm_a", workspace="/var/lib/cnb/work",
        runner=runner, deadline=deadline(), clock=lambda:100)
    assert inventory.failures == () and inventory.files == tuple(rows)
    assert inventory.files[0]["sha256"] == hashlib.sha256(b"native\n").hexdigest()
    malformed = Runner([{**rows[0], "sha256":"bad"}])
    rejected = inventory_remote_workspace(connected, role="vm_a", workspace="/var/lib/cnb/work",
        runner=malformed, deadline=deadline(), clock=lambda:100)
    assert rejected.files == () and rejected.failures


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
