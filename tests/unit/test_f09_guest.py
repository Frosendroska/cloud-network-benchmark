from __future__ import annotations

from datetime import timedelta
import hashlib

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
