from datetime import datetime, timezone

import pytest

from cloud_network_benchmark.contracts import (AcquisitionSource, ArtifactLayout, ClockMapping, Evidence,
    ExperimentMode, MeasurementTiming, PhaseConfiguration, BenchmarkPhase, VmRole)


def timing(**updates):
    values = dict(
        unit_id="unit-1", deployment_id="deployment-1",
        probe_test_started_at=Evidence[datetime](unavailable_reason="not captured"),
        tcp_started_at=Evidence[datetime](unavailable_reason="not captured"),
        measured_started_at=Evidence[datetime](unavailable_reason="not captured"),
        measured_finished_at=Evidence[datetime](unavailable_reason="not captured"),
        monotonic_started=Evidence[float](unavailable_reason="not captured"),
        monotonic_finished=Evidence[float](unavailable_reason="not captured"),
        omitted_seconds=5, overlap_seconds=Evidence[float](unavailable_reason="not established"),
    )
    values.update(updates)
    return MeasurementTiming(**values)


def source(**updates):
    values = dict(
        tool="sockperf", semantics_id="sockperf-3.10-enabled-v1", producer=VmRole.VM_A,
        command_action_id="unit-1-sockperf", invocation_id="unit-1-sockperf", session_id=None,
        boot_id="12345678-1234-1234-1234-123456789abc", clock_domain="CLOCK_MONOTONIC",
        log_artifact_id="unit-1-sockperf-stderr", log_path="measurements/unit-1/sockperf.stderr",
        native_artifact_ids=["unit-1-sockperf-csv", "unit-1-sockperf-counts"],
        build_artifact_id="vm-a-build-sockperf", mapping_indexes=[],
    )
    values.update(updates)
    return AcquisitionSource(**values)


def test_legacy_timing_defaults_to_unavailable_acquisition_sources():
    actual = timing()
    assert actual.acquisition_sources.value is None
    assert actual.acquisition_sources.unavailable_reason == "acquisition observations not captured"


def test_references_do_not_create_coverage_and_populated_list_is_nonempty():
    actual = timing(acquisition_sources=Evidence[list[AcquisitionSource]](value=[source()]))
    assert actual.overlap_seconds.value is None
    with pytest.raises(ValueError, match="must be nonempty"):
        timing(acquisition_sources=Evidence[list[AcquisitionSource]](value=[]))


@pytest.mark.parametrize("changes", [
    {"command_action_id": "other"},
    {"tool": "iperf3"},
    {"log_path": "../escape"},
    {"session_id": "unsupported-sockperf-cookie"},
    {"native_artifact_ids": ["same", "same"]},
])
def test_source_identity_and_path_constraints(changes):
    with pytest.raises(ValueError):
        source(**changes)


def test_mapping_index_must_exist_and_belong_to_producer():
    mapping = ClockMapping(vm_role=VmRole.VM_B, monotonic_seconds=1,
        utc_timestamp=datetime.now(timezone.utc), estimated_error_ms=Evidence[float](value=1),
        synchronization_source=Evidence[str](value="fixture"))
    with pytest.raises(ValueError, match="role must match"):
        timing(clock_mappings=[mapping], acquisition_sources=Evidence[list[AcquisitionSource]](
            value=[source(mapping_indexes=[0])]))
    with pytest.raises(ValueError, match="existing clock mapping"):
        timing(acquisition_sources=Evidence[list[AcquisitionSource]](value=[source(mapping_indexes=[0])]))


@pytest.mark.parametrize("mode,units", [
    (ExperimentMode.SHORT_STUDY, [PhaseConfiguration(unit_id="idle-1", kind="phase", phase=BenchmarkPhase.IDLE_LATENCY, measured_seconds=60),
                                  PhaseConfiguration(unit_id="single-1", kind="phase", phase=BenchmarkPhase.SINGLE_FLOW, measured_seconds=60, streams=1),
                                  PhaseConfiguration(unit_id="multi-1", kind="phase", phase=BenchmarkPhase.MULTI_FLOW, measured_seconds=60, streams=8)]),
    (ExperimentMode.DIAGNOSTIC_DAY, [PhaseConfiguration(unit_id="day-1", kind="continuous_day", phase=BenchmarkPhase.MULTI_FLOW, measured_seconds=86400, streams=8)]),
])
def test_observation_artifact_declarations_preserve_tool_roles(mode, units):
    records = ArtifactLayout.for_mode(mode, units).declarations_for_mode(mode, units)
    by_id = {item.artifact_id: item for item in records}
    if mode == ExperimentMode.SHORT_STUDY:
        assert by_id["idle-1-sockperf-observation"].kind == "sockperf-observation-stderr"
        assert "idle-1-iperf3-observation" not in by_id
        assert by_id["multi-1-iperf3-observation"].producer == VmRole.VM_A
    else:
        assert by_id["day-iperf3-observation"].producer == VmRole.VM_A
        assert by_id["day-probe-observation"].path_template.endswith("{chunk_id}.acquisition.stderr")
