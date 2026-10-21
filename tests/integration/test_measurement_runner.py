import json
from pathlib import Path

from cloud_network_benchmark.contracts import CommandEvidence, MeasurementTiming
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
    assert sockperf.read_bytes() == b"time,latency_us\n0.0,10\n"
    iperf = tmp_path / item.artifact_layout.unit_paths(item.measurement_units[1].unit_id)["iperf3"]
    native = json.loads(iperf.read_text())
    assert native["server_output"]["end"]["sum_received"]["seconds"] == 10
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


def test_day_keeps_one_tcp_invocation_and_rotates_local_bounded_chunks(repository_root, tmp_path):
    item = observation(repository_root, "diagnostic_day")
    clock = FakeClock()
    processes = FakeProcesses(clock)
    result = run_measurement(item, context(item, tmp_path), processes=processes, clock=clock)
    tcp = [handle for handle in processes.handles if handle.request.argv[0] == "iperf3"]
    probes = [handle for handle in processes.handles if handle.request.argv[0] == "sockperf"]
    assert result.outcome == "succeeded"
    assert len(tcp) == 1
    assert tcp[0].request.argv[tcp[0].request.argv.index("-t") + 1] == "86400"
    assert "--json-stream" in tcp[0].request.argv and "--forceflush" in tcp[0].request.argv
    assert len(probes) >= 288
    assert all(int(handle.request.argv[handle.request.argv.index("-t") + 1]) <= 300 for handle in probes)
    assert len({handle.request.argv[handle.request.argv.index("--full-log") + 1] for handle in probes}) == len(probes)


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
