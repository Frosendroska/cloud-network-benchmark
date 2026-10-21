from pathlib import Path

from cloud_network_benchmark.measurement import run_measurement
from f03_support import FakeClock, FakeProcesses, context, observation


def test_acceptance_uses_idle_single_aggregate_order_and_two_pauses(repository_root, tmp_path):
    item = observation(repository_root, "acceptance")
    clock, processes = FakeClock(), None
    processes = FakeProcesses(clock)
    result = run_measurement(item, context(item, tmp_path), processes=processes, clock=clock)
    assert result.outcome == "succeeded"
    assert result.executed_unit_ids == tuple(unit.unit_id for unit in item.measurement_units)
    assert [h.request.argv[0] for h in processes.handles] == ["sockperf", "sockperf", "iperf3", "sockperf", "iperf3"]
    iperf = [h.request.argv for h in processes.handles if h.request.argv[0] == "iperf3"]
    assert all(argv[argv.index("-t") + 1] == "10" for argv in iperf)
    assert [argv[argv.index("-P") + 1] for argv in iperf] == ["1", "4"]
    assert len(result.timings) == 3 and result.timings[0].omitted_seconds == 5
    assert result.timings[1].measured_started_at.value is None


def test_calibration_uses_supplied_canonical_twelve_units(repository_root, tmp_path):
    item = observation(repository_root, "calibration")
    clock = FakeClock()
    processes = FakeProcesses(clock)
    result = run_measurement(item, context(item, tmp_path), processes=processes, clock=clock)
    assert result.outcome == "succeeded"
    assert [h.request.argv[h.request.argv.index("-P") + 1] for h in processes.handles if h.request.argv[0] == "iperf3"] == ["1", "4", "2", "8", "8", "2", "4", "1", "2", "8", "1", "4"]
    assert len(result.executed_unit_ids) == 12
    assert len([h for h in processes.handles if h.request.argv[0] == "iperf3"]) == 12


def test_failed_calibration_trial_preserves_evidence_and_stops_later_units(repository_root, tmp_path):
    item = observation(repository_root, "calibration")
    clock = FakeClock()
    processes = FakeProcesses(clock, failed_action=f"iperf3-{item.measurement_units[2].unit_id}")
    result = run_measurement(item, context(item, tmp_path), processes=processes, clock=clock)
    assert result.outcome == "failed"
    assert result.executed_unit_ids == item.measurement_units[:2] or len(result.executed_unit_ids) == 2
    assert len([h for h in processes.handles if h.request.argv[0] == "iperf3"]) == 3
    assert result.failures


def test_short_study_uses_sixty_second_units_and_fixed_order(repository_root, tmp_path):
    item = observation(repository_root, "short_study")
    clock = FakeClock()
    processes = FakeProcesses(clock)
    result = run_measurement(item, context(item, tmp_path), processes=processes, clock=clock)
    assert result.outcome == "succeeded"
    assert [handle.request.argv[handle.request.argv.index("-t") + 1]
            for handle in processes.handles if handle.request.argv[0] == "iperf3"] == ["60", "60"]
    probes = [handle.request.argv for handle in processes.handles if handle.request.argv[0] == "sockperf"]
    assert [argv[argv.index("-t") + 1] for argv in probes] == ["65", "70", "70"]
    assert len(result.executed_unit_ids) == 3
