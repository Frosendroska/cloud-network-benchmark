import json
from dataclasses import replace
from pathlib import Path

import pytest

from cloud_network_benchmark.contracts import (
    ClockMapping, CommandEvidence, Evidence, MeasurementTiming, StringEvidence, VmRole,
)
from cloud_network_benchmark.measurement import run_measurement
from f03_support import FakeClock, FakeProcesses, context, observation


def test_materialized_short_run_preserves_native_and_projects_shared_contract_shapes(repository_root, tmp_path):
    item = observation(repository_root, "acceptance")
    clock = FakeClock()
    processes = FakeProcesses(clock)
    result = run_measurement(item, context(item, tmp_path), processes=processes, clock=clock)
    assert result.outcome == "succeeded"
    assert len(result.command_evidence) == 5
    assert all(isinstance(value, CommandEvidence) for value in result.command_evidence)
    assert all(isinstance(value, MeasurementTiming) for value in result.timings)
    sockperf = tmp_path / item.artifact_layout.unit_paths(item.measurement_units[0].unit_id)["sockperf"]
    assert sockperf.read_bytes() == (
        b"------------------------------\n"
        b"packet, txTime(sec), rxTime(sec), Round trip(usec)\n"
        b"0, 1000.000000000, 1000.000010000, 10.000\n"
        b"------------------------------\n"
        b"[Total Run] RunTime=15.000 sec; Warm up time=400 msec; SentMessages=100; ReceivedMessages=99\n"
        b"[Valid Duration] RunTime=14.550 sec; SentMessages=100; ReceivedMessages=99\n")
    iperf = tmp_path / item.artifact_layout.unit_paths(item.measurement_units[1].unit_id)["iperf3"]
    native = json.loads(iperf.read_text())
    assert native["server_output_json"]["end"]["sum_received"]["seconds"] == 10
    loaded_note = next(note for note in result.timing_notes if note["unit_id"] == item.measurement_units[1].unit_id)
    assert loaded_note["conservative_tcp_start_bounds_monotonic"] is not None
    assert loaded_note["iperf_source_semantics_verified"] is True
    assert loaded_note["probe_active_coverage"] == "unavailable"
    assert loaded_note["sockperf_source_semantics_verified"] is True
    assert loaded_note["sockperf_native_total_run_seconds"] == 20
    assert loaded_note["sockperf_native_valid_duration_seconds"] == 19.55
    assert loaded_note["sockperf_test_start_bounds_monotonic"] is not None
    assert loaded_note["probe_active_coverage"] == "unavailable"
    # F09 owns these canonical controller-produced paths and can serialize typed evidence unchanged.
    projection = json.loads(json.dumps([row.model_dump(mode="json") for row in result.command_evidence]))
    assert len([CommandEvidence.model_validate(row) for row in projection]) == 5
    timing_projection = json.loads(json.dumps([row.model_dump(mode="json") for row in result.timings]))
    assert len([MeasurementTiming.model_validate(row) for row in timing_projection]) == 3
    for unit in item.measurement_units:
        paths = item.artifact_layout.unit_paths(unit.unit_id)
        folder = tmp_path / f"measurements/{unit.unit_id}"
        folder.mkdir(parents=True, exist_ok=True)
        unit_commands = [row.model_dump(mode="json") for row in result.command_evidence if row.unit_id == unit.unit_id]
        unit_timings = [row.model_dump(mode="json") for row in result.timings if row.unit_id == unit.unit_id]
        (tmp_path / paths["commands"]).write_text(json.dumps(unit_commands))
        (tmp_path / paths["timing"]).write_text(json.dumps(unit_timings))
        assert all(CommandEvidence.model_validate(row) for row in json.loads((tmp_path / paths["commands"]).read_text()))
        assert all(MeasurementTiming.model_validate(row) for row in json.loads((tmp_path / paths["timing"]).read_text()))


def test_unknown_iperf_source_keeps_native_receiver_duration_but_withholds_bounds(repository_root, tmp_path):
    item = observation(repository_root, "acceptance")
    clock = FakeClock()
    processes = FakeProcesses(clock)
    runtime = replace(context(item, tmp_path), tool_identity={})
    result = run_measurement(item, runtime, processes=processes, clock=clock)
    note = next(note for note in result.timing_notes if note["unit_id"] == item.measurement_units[1].unit_id)
    assert note["observed_receiver_seconds"] == 10
    assert note["observed_omit_seconds"] == 5
    assert note["iperf_source_semantics_verified"] is False
    assert note["conservative_tcp_start_bounds_monotonic"] is None
    assert note["sockperf_native_total_run_seconds"] == 20
    assert note["sockperf_test_start_bounds_monotonic"] is None


def test_truncated_receiver_json_is_preserved_with_unavailable_timing(repository_root, tmp_path):
    item = observation(repository_root, "acceptance")
    clock = FakeClock()
    action = f"iperf3-{item.measurement_units[1].unit_id}"
    result = run_measurement(item, context(item, tmp_path),
        processes=FakeProcesses(clock, corrupt_iperf_action=action), clock=clock)
    path = tmp_path / item.artifact_layout.unit_paths(item.measurement_units[1].unit_id)["iperf3"]
    note = next(note for note in result.timing_notes if note["unit_id"] == item.measurement_units[1].unit_id)
    assert result.outcome == "succeeded"
    assert path.read_bytes() == b'{"start":{"test_start":{"omit":5}},'
    assert note["observed_receiver_seconds"] is None
    assert note["conservative_tcp_start_bounds_monotonic"] is None


@pytest.mark.parametrize("error", [Evidence(value=101.0), Evidence(unavailable_reason="not measured")])
def test_coarse_or_unknown_clock_mapping_is_preserved_without_exact_measurement_anchors(repository_root, tmp_path, error):
    item = observation(repository_root, "acceptance")
    clock = FakeClock()
    mapping = ClockMapping(vm_role=VmRole.VM_A, monotonic_seconds=clock.monotonic(),
        utc_timestamp=clock.utc_now(), estimated_error_ms=error,
        synchronization_source=StringEvidence(value="synthetic supplied mapping"))
    runtime = replace(context(item, tmp_path), clock_mappings=(mapping,))
    result = run_measurement(item, runtime, processes=FakeProcesses(clock), clock=clock)
    assert result.outcome == "succeeded"
    assert all(timing.measured_started_at.value is None and timing.measured_finished_at.value is None
               for timing in result.timings)
    assert result.timings[0].clock_mappings[0].estimated_error_ms == error


def test_day_keeps_one_tcp_invocation_and_rotates_local_bounded_chunks(repository_root, tmp_path):
    item = observation(repository_root, "diagnostic_day")
    clock = FakeClock()
    processes = FakeProcesses(clock, start_delay=0.25)
    result = run_measurement(item, context(item, tmp_path), processes=processes, clock=clock)
    day_events = json.loads((Path(__file__).parents[1] / "fixtures/f03/day-events.json").read_text())
    tcp = [handle for handle in processes.handles if handle.request.argv[0] == "iperf3"]
    probes = [handle for handle in processes.handles if handle.request.argv[0] == "sockperf"]
    assert result.outcome == "succeeded"
    assert len(tcp) == day_events["tcp_processes"] == 1
    assert tcp[0].request.argv[tcp[0].request.argv.index("-t") + 1] == "86400"
    assert "--json-stream" in tcp[0].request.argv and "--forceflush" in tcp[0].request.argv
    assert len(probes) >= 288
    assert all(int(handle.request.argv[handle.request.argv.index("-t") + 1]) <= day_events["probe_limit_seconds"] for handle in probes)
    assert len({handle.request.argv[handle.request.argv.index("--full-log") + 1] for handle in probes}) == len(probes)
    durations = [int(handle.request.argv[handle.request.argv.index("-t") + 1]) for handle in probes]
    assert durations[-1] < 300
    assert len({handle.request.action_id for handle in probes}) == len(probes)
    chunk_notes = [note for note in result.timing_notes if note.get("parent_unit_id")]
    assert all(note["parent_unit_id"] == item.measurement_units[0].unit_id for note in chunk_notes)
    assert len(chunk_notes) == len(probes)
    assert chunk_notes[0]["process_rotation_gap_seconds"] is None
    assert all(note["process_rotation_gap_seconds"] == 0.25 for note in chunk_notes[1:-1])
    assert chunk_notes[-1]["process_rotation_gap_seconds"] is None
    assert all(note["probe_process_monotonic"][0] <= note["probe_process_monotonic"][1]
               for note in chunk_notes)
    assert len([note for note in result.timing_notes if note.get("unit_id") == item.measurement_units[0].unit_id]) == 1
    day_jsonl = (tmp_path / item.artifact_layout.day_iperf3_client).read_text().splitlines()
    assert [json.loads(line)["event"] for line in day_jsonl] == ["start", "server_output_json", "end"]
    day_note = next(note for note in result.timing_notes if note.get("unit_id") == item.measurement_units[0].unit_id)
    assert day_note["observed_receiver_seconds"] == 86400
    assert day_note["observed_omit_seconds"] == 5
    assert day_note["probe_active_coverage"] == "unavailable"
    probe_note = next(note for note in result.timing_notes if note["unit_id"] == "probe-chunk-000001")
    assert probe_note["sockperf_native_total_run_seconds"] == 300
    assert probe_note["sockperf_test_start_bounds_monotonic"] is not None
    assert day_note["initial_notification_monotonic"] is not None
    assert day_note["tcp_process_monotonic"][0] >= day_note["initial_notification_monotonic"] + 2


def test_failed_short_unit_returns_primary_failure_and_no_later_units(repository_root, tmp_path):
    item = observation(repository_root, "acceptance")
    clock = FakeClock()
    processes = FakeProcesses(clock, failed_action=f"iperf3-{item.measurement_units[1].unit_id}")
    result = run_measurement(item, context(item, tmp_path), processes=processes, clock=clock)
    assert result.outcome == "failed"
    assert result.executed_unit_ids == (item.measurement_units[0].unit_id,)
    assert result.unexecuted_unit_ids == tuple(unit.unit_id for unit in item.measurement_units[1:])
    assert result.failures and result.failures[0].category == "command_failure"
    assert (tmp_path / item.artifact_layout.unit_paths(item.measurement_units[1].unit_id)["sockperf"]).exists()


def test_missing_current_notification_stops_before_tcp_and_keeps_probe_evidence(repository_root, tmp_path):
    item = observation(repository_root, "acceptance")
    clock = FakeClock()
    processes = FakeProcesses(clock, notification="startup complete")
    result = run_measurement(item, context(item, tmp_path), processes=processes, clock=clock)
    assert result.outcome == "failed"
    assert not [handle for handle in processes.handles if handle.request.argv[0] == "iperf3"]
    assert result.command_evidence and result.command_evidence[0].request["argv"][0] == "sockperf"
    assert result.timings[0].probe_test_started_at.value is None


def test_failed_day_probe_rotates_without_restarting_tcp(repository_root, tmp_path):
    item = observation(repository_root, "diagnostic_day")
    clock = FakeClock()
    processes = FakeProcesses(clock, failed_action="sockperf-probe-chunk-000002")
    result = run_measurement(item, context(item, tmp_path), processes=processes, clock=clock)
    assert result.outcome == "succeeded"
    assert len([handle for handle in processes.handles if handle.request.argv[0] == "iperf3"]) == 1
    assert any(f.category == "probe_chunk_failure" for f in result.failures)
    assert len([handle for handle in processes.handles if handle.request.argv[0] == "sockperf"]) >= 288


def test_failed_day_tcp_preserves_partial_day_and_does_not_restart(repository_root, tmp_path):
    item = observation(repository_root, "diagnostic_day")
    clock = FakeClock()
    parent = item.measurement_units[0]
    processes = FakeProcesses(clock, failed_action=f"iperf3-{parent.unit_id}")
    result = run_measurement(item, context(item, tmp_path), processes=processes, clock=clock)
    assert result.outcome == "failed"
    assert len([handle for handle in processes.handles if handle.request.argv[0] == "iperf3"]) == 1
    assert result.failures and result.failures[0].category == "tcp_failure"
    assert (tmp_path / item.artifact_layout.day_iperf3_client).is_file()
    assert any("probe-chunk-000001" in path for path in result.artifact_paths)
    assert parent.unit_id in result.unexecuted_unit_ids


def test_day_finalizes_probe_that_exits_with_tcp(repository_root, tmp_path):
    item = observation(repository_root, "diagnostic_day")
    clock = FakeClock()

    class SimultaneousExitProcesses(FakeProcesses):
        def poll(self, handle):
            if handle.request.argv[0] == "iperf3" and clock.monotonic() >= handle.finishes:
                for other in self.handles:
                    if other.request.argv[0] == "sockperf" and other.result is None:
                        other.finishes = clock.monotonic()
            return super().poll(handle)

    processes = SimultaneousExitProcesses(clock)
    result = run_measurement(item, context(item, tmp_path), processes=processes, clock=clock)
    probes = [handle for handle in processes.handles if handle.request.argv[0] == "sockperf"]
    probe_rows = [row for row in result.command_evidence if row.request["argv"][0] == "sockperf"]
    probe_ids = [row.unit_id for row in probe_rows]
    assert result.outcome == "succeeded"
    assert len([handle for handle in processes.handles if handle.request.argv[0] == "iperf3"]) == 1
    assert len(probe_rows) == len(probes)
    assert len(probe_ids) == len(set(probe_ids))
    assert all(unit_id in result.executed_unit_ids for unit_id in probe_ids)
    assert all(f"{unit_id}" in " ".join(result.artifact_paths) for unit_id in probe_ids)


def test_day_finalization_error_is_secondary_to_interruption(repository_root, tmp_path):
    item = observation(repository_root, "diagnostic_day")
    clock = FakeClock()

    class InterruptedDayProcesses(FakeProcesses):
        def next_event(self, handles, deadline_monotonic):
            if any(handle.request.argv[0] == "iperf3" for handle in handles):
                raise KeyboardInterrupt("scripted diagnostic-day interruption")
            return super().next_event(handles, deadline_monotonic)

    processes = InterruptedDayProcesses(clock, stop_failure_action="sockperf-probe-chunk-000001")
    result = run_measurement(item, context(item, tmp_path), processes=processes, clock=clock)
    assert result.outcome == "failed"
    assert result.failures[0].message.startswith("KeyboardInterrupt:")
    assert any(f.category == "shutdown" and "termination failure" in f.message for f in result.failures[1:])
    assert len([handle for handle in processes.handles if handle.request.argv[0] == "iperf3"]) == 1
    assert len(processes.stop_calls) == 2


def test_split_delayed_notification_gates_tcp_on_notification_plus_monotonic_lead(repository_root, tmp_path):
    item = observation(repository_root, "acceptance")
    clock = FakeClock()
    processes = FakeProcesses(clock, notification_delay=1.5, split_notification=True)
    result = run_measurement(item, context(item, tmp_path), processes=processes, clock=clock)
    assert result.outcome == "succeeded"
    probe = next(handle for handle in processes.handles if handle.request.action_id == f"sockperf-{item.measurement_units[1].unit_id}")
    tcp = next(handle for handle in processes.handles if handle.request.action_id == f"iperf3-{item.measurement_units[1].unit_id}")
    assert tcp.started_monotonic == probe.started_monotonic + 3.5
    assert next(note for note in result.timing_notes if note["unit_id"] == item.measurement_units[1].unit_id)["notification_monotonic"] == probe.started_monotonic + 1.5
    assert tcp.started_monotonic >= probe.started_monotonic + 3.5


def test_late_notification_never_starts_tcp_and_keeps_partial_outputs(repository_root, tmp_path):
    item = observation(repository_root, "acceptance")
    clock = FakeClock()
    processes = FakeProcesses(clock, notification_delay=20)
    result = run_measurement(item, context(item, tmp_path), processes=processes, clock=clock)
    assert result.outcome == "failed"
    assert not [handle for handle in processes.handles if handle.request.argv[0] == "iperf3"]
    assert result.executed_unit_ids == (item.measurement_units[0].unit_id,)
    assert result.unexecuted_unit_ids == tuple(unit.unit_id for unit in item.measurement_units[1:])
    assert result.command_evidence


def test_process_start_failure_preserves_primary_and_partial_artifacts(repository_root, tmp_path):
    item = observation(repository_root, "acceptance")
    clock = FakeClock()
    action = f"iperf3-{item.measurement_units[1].unit_id}"
    processes = FakeProcesses(clock, start_failure=action)
    result = run_measurement(item, context(item, tmp_path), processes=processes, clock=clock)
    unit_paths = item.artifact_layout.unit_paths(item.measurement_units[1].unit_id)
    assert result.outcome == "failed"
    assert result.failures[0].message.startswith("OSError: scripted process start failure")
    assert result.executed_unit_ids == (item.measurement_units[0].unit_id,)
    assert (tmp_path / unit_paths["sockperf"]).exists()
    assert len([handle for handle in processes.handles if handle.request.argv[0] == "iperf3"]) == 0


def test_write_and_shutdown_failures_remain_secondary_to_primary(repository_root, tmp_path):
    item = observation(repository_root, "acceptance")
    clock = FakeClock()
    unit_id = item.measurement_units[1].unit_id
    processes = FakeProcesses(clock, failed_action=f"iperf3-{unit_id}",
        stop_failure_action=f"sockperf-{unit_id}")
    result = run_measurement(item, context(item, tmp_path), processes=processes, clock=clock)
    assert result.outcome == "failed"
    assert result.failures[0].category == "command_failure"
    assert any(f.category == "shutdown" and "termination failure" in f.message for f in result.failures[1:])

    other_root = tmp_path / "disk-full"
    other_root.mkdir()
    other_clock = FakeClock()
    failing = FakeProcesses(other_clock, write_failure_action=f"sockperf-{item.measurement_units[0].unit_id}")
    failed = run_measurement(item, context(item, other_root), processes=failing, clock=other_clock)
    assert failed.outcome == "failed"
    assert "disk full" in failed.failures[0].message
    assert not failing.handles


def test_deadline_cancellation_stops_owned_clients_and_retains_unexecuted_units(repository_root, tmp_path):
    item = observation(repository_root, "acceptance")
    clock = FakeClock()
    unit_id = item.measurement_units[1].unit_id
    processes = FakeProcesses(clock, hang_action=f"iperf3-{unit_id}")
    result = run_measurement(item, context(item, tmp_path, deadline=1100), processes=processes, clock=clock)
    assert result.outcome == "failed"
    assert result.failures[0].category == "deadline"
    assert result.unexecuted_unit_ids == tuple(unit.unit_id for unit in item.measurement_units[1:])
    assert len(processes.stop_calls) >= 1, result.failures
    assert all((tmp_path / path).exists() for path in result.artifact_paths)


def test_interruption_stops_active_pair_and_preserves_partial_command_evidence(repository_root, tmp_path):
    item = observation(repository_root, "acceptance")
    clock = FakeClock()
    unit_id = item.measurement_units[1].unit_id

    class InterruptedProcesses(FakeProcesses):
        def next_event(self, handles, deadline_monotonic):
            if any(handle.request.action_id == f"iperf3-{unit_id}" for handle in handles):
                raise KeyboardInterrupt("scripted caller interruption")
            return super().next_event(handles, deadline_monotonic)

    processes = InterruptedProcesses(clock)
    result = run_measurement(item, context(item, tmp_path), processes=processes, clock=clock)
    assert result.outcome == "failed"
    assert "KeyboardInterrupt" in result.failures[0].message
    assert result.executed_unit_ids == (item.measurement_units[0].unit_id,)
    assert result.unexecuted_unit_ids == tuple(unit.unit_id for unit in item.measurement_units[1:])
    assert {row.request["argv"][0] for row in result.command_evidence} == {"sockperf", "iperf3"}
    assert len(processes.stop_calls) >= 1
    assert (tmp_path / item.artifact_layout.unit_paths(unit_id)["sockperf"]).is_file()
    assert (tmp_path / item.artifact_layout.unit_paths(unit_id)["iperf3"]).is_file()


def test_calibration_materializes_original_native_and_shared_evidence_for_each_trial(repository_root, tmp_path):
    item = observation(repository_root, "calibration")
    clock = FakeClock()
    result = run_measurement(item, context(item, tmp_path), processes=FakeProcesses(clock), clock=clock)
    assert result.outcome == "succeeded" and len(result.executed_unit_ids) == 12
    command_rows = [row.model_dump(mode="json") for row in result.command_evidence]
    timing_rows = [row.model_dump(mode="json") for row in result.timings]
    assert len([CommandEvidence.model_validate(row) for row in json.loads(json.dumps(command_rows))]) == 24
    assert len([MeasurementTiming.model_validate(row) for row in json.loads(json.dumps(timing_rows))]) == 12
    assert len({row["unit_id"] for row in command_rows}) == 12
    for unit in item.measurement_units:
        paths = item.artifact_layout.unit_paths(unit.unit_id)
        native = tmp_path / paths["iperf3"]
        assert native.is_file() and "server_output_json" in json.loads(native.read_text())
        assert (tmp_path / paths["sockperf"]).is_file()
