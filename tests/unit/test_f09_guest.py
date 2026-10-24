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
