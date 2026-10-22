from dataclasses import replace

import pytest

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
    assert clock.waits == [10, 10]
    assert len(result.timings) == 3 and result.timings[0].omitted_seconds == 5
    assert result.timings[1].measured_started_at.value is None


def test_calibration_uses_supplied_canonical_twelve_units(repository_root, tmp_path):
    item = observation(repository_root, "calibration")
    clock = FakeClock()
    processes = FakeProcesses(clock)
    result = run_measurement(item, context(item, tmp_path), processes=processes, clock=clock)
    assert result.outcome == "succeeded"
    assert result.executed_unit_ids == tuple(unit.unit_id for unit in item.measurement_units)
    assert all(unit.kind == "calibration_trial" for unit in item.measurement_units)
    assert [h.request.argv[h.request.argv.index("-P") + 1] for h in processes.handles if h.request.argv[0] == "iperf3"] == ["1", "4", "2", "8", "8", "2", "4", "1", "2", "8", "1", "4"]
    assert len(result.executed_unit_ids) == 12
    assert len([h for h in processes.handles if h.request.argv[0] == "iperf3"]) == 12
    assert all(e.deployment_id == context(item, tmp_path).identity.deployment_id for e in result.command_evidence)
    assert clock.waits == [10] * 11


@pytest.mark.parametrize("mode", ["acceptance", "calibration"])
def test_queued_previous_probe_exit_does_not_abort_next_current_notification(repository_root, tmp_path, mode):
    from cloud_network_benchmark.measurement_process import ProcessEvent

    item = observation(repository_root, mode)
    clock = FakeClock()

    class PreviousExitProcesses(FakeProcesses):
        def start(self, request, **kwargs):
            previous = [h for h in self.handles if h.request.argv[0] == "sockperf"]
            if request.argv[0] == "sockperf" and previous:
                old = previous[-1]
                self.events.put(ProcessEvent(old, "exit", "", clock.monotonic(), clock.utc_now()))
            return super().start(request, **kwargs)

    processes = PreviousExitProcesses(clock)
    result = run_measurement(item, context(item, tmp_path), processes=processes, clock=clock)
    assert result.outcome == "succeeded"
    assert result.executed_unit_ids == tuple(unit.unit_id for unit in item.measurement_units)


def test_current_probe_early_exit_during_notification_lead_fails_unit(repository_root, tmp_path):
    item = observation(repository_root, "acceptance")
    current_probe = f"sockperf-{item.measurement_units[1].unit_id}"
    clock = FakeClock()
    processes = FakeProcesses(clock, early_exit_action=current_probe)
    result = run_measurement(item, context(item, tmp_path), processes=processes, clock=clock)
    assert result.outcome == "failed"
    assert result.executed_unit_ids == (item.measurement_units[0].unit_id,)
    assert any(f.category == "probe_exit" for f in result.failures)


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
    assert clock.waits == [10, 10]


def test_short_receiver_exit_remains_execution_success_but_has_no_full_duration_bound(repository_root, tmp_path):
    item = observation(repository_root, "acceptance")
    clock = FakeClock()
    unit_id = item.measurement_units[1].unit_id
    processes = FakeProcesses(clock, early_exit_action=f"iperf3-{unit_id}")
    result = run_measurement(item, context(item, tmp_path), processes=processes, clock=clock)
    note = next(note for note in result.timing_notes if note["unit_id"] == unit_id)
    assert result.outcome == "succeeded"
    assert note["observed_receiver_seconds"] == 0
    assert note["conservative_tcp_start_bounds_monotonic"] is None
    assert note["probe_active_coverage"] == "unavailable"


def test_insufficient_deadline_fails_before_any_client_starts(repository_root, tmp_path):
    item = observation(repository_root, "acceptance")
    clock = FakeClock()
    processes = FakeProcesses(clock)
    result = run_measurement(item, replace(context(item, tmp_path), deadline_monotonic=1010),
        processes=processes, clock=clock)
    assert result.outcome == "failed"
    assert not processes.handles
    assert result.unexecuted_unit_ids == tuple(unit.unit_id for unit in item.measurement_units)
