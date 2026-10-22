import pytest

from cloud_network_benchmark.measurement import MeasurementContext, run_measurement
from f03_support import FakeClock, FakeProcesses, context, observation


def test_context_identity_and_private_address_are_checked_before_process_start(repository_root, tmp_path):
    item = observation(repository_root, "acceptance")
    clock, processes = FakeClock(), FakeProcesses(FakeClock())
    bad = context(item, tmp_path).identity.model_copy(update={"config_id": "other"})
    with pytest.raises(ValueError, match="experiment/config"):
        run_measurement(item, MeasurementContext(identity=bad, b_private_ipv4="10.1.2.3", root=tmp_path,
            deadline_monotonic=200000, shutdown_grace_seconds=1), processes=processes, clock=clock)
    with pytest.raises(ValueError, match="private IPv4"):
        run_measurement(item, MeasurementContext(identity=context(item, tmp_path).identity, b_private_ipv4="8.8.8.8",
            root=tmp_path, deadline_monotonic=200000, shutdown_grace_seconds=1), processes=processes, clock=clock)
    assert not processes.handles


def test_existing_destinations_and_symlink_escape_are_rejected(repository_root, tmp_path):
    item = observation(repository_root, "acceptance")
    target = tmp_path / "outside"
    target.mkdir()
    (tmp_path / "measurements").mkdir()
    (tmp_path / "measurements" / item.measurement_units[0].unit_id).symlink_to(target, target_is_directory=True)
    clock = FakeClock()
    with pytest.raises(ValueError):
        run_measurement(item, context(item, tmp_path), processes=FakeProcesses(clock), clock=clock)


def test_pending_research_common_n_is_rejected(repository_root, tmp_path):
    item = observation(repository_root, "short_study", freeze_common_n=False)
    clock = FakeClock()
    processes = FakeProcesses(clock)
    with pytest.raises(ValueError, match="frozen common N"):
        run_measurement(item, context(item, tmp_path), processes=processes, clock=clock)
    assert not processes.handles


def test_short_deadline_and_provider_mismatch_reject_before_launch(repository_root, tmp_path):
    item = observation(repository_root, "acceptance")
    clock = FakeClock()
    processes = FakeProcesses(clock)
    short = context(item, tmp_path, deadline=1000.0)
    with pytest.raises(ValueError, match="finite future|deadline"):
        run_measurement(item, short, processes=processes, clock=clock)
    wrong_provider = context(item, tmp_path).identity.model_copy(update={"provider": "gcp", "run_id": "run-f03-gcp"})
    wrong = MeasurementContext(identity=wrong_provider, b_private_ipv4="10.20.30.40", root=tmp_path,
        deadline_monotonic=200000, shutdown_grace_seconds=1)
    with pytest.raises(ValueError, match="provider"):
        run_measurement(item, wrong, processes=processes, clock=clock)
    assert not processes.handles
