from __future__ import annotations

import pytest

from cloud_network_benchmark.contracts.campaign import PhaseConfiguration
from cloud_network_benchmark.orchestration import LifecycleContext, ProviderHooks, execute_provider_workflows, validate_mode_units
from tests.fixtures.f09.helpers import FakeClock


def units_for(mode):
    if mode == "acceptance":
        return [PhaseConfiguration(unit_id=f"accept-{phase}", kind="phase", phase=phase,
            measured_seconds=10, streams=(None if phase == "idle_latency" else 1 if phase == "single_flow" else 4))
            for phase in ("idle_latency", "single_flow", "multi_flow")]
    if mode == "short_study":
        return [PhaseConfiguration(unit_id=f"study-{phase}", kind="phase", phase=phase,
            measured_seconds=60, streams=(None if phase == "idle_latency" else 1 if phase == "single_flow" else 4))
            for phase in ("idle_latency", "single_flow", "multi_flow")]
    if mode == "calibration":
        return [PhaseConfiguration(unit_id=f"cal-{index:02}", kind="calibration_trial", phase="multi_flow",
            measured_seconds=60, streams=streams, trial_index=index)
            for index, streams in enumerate((1, 4, 2, 8, 8, 2, 4, 1, 2, 8, 1, 4), 1)]
    day = [PhaseConfiguration(unit_id="day", kind="continuous_day", phase="multi_flow", measured_seconds=86400, streams=4)]
    day.extend(PhaseConfiguration(unit_id=f"probe-{index:03}", kind="probe_chunk", phase="multi_flow",
        measured_seconds=300, parent_unit_id="day") for index in (1, 2))
    return day


@pytest.mark.parametrize("provider", ["aws", "azure", "gcp"])
@pytest.mark.parametrize("mode", ["acceptance", "short_study", "calibration", "diagnostic_day"])
def test_one_retained_pair_per_provider_and_mode_uses_exact_resolved_units(provider, mode):
    units = units_for(mode)
    validate_mode_units(mode, units)
    trace = []
    def hook(stage):
        def action():
            trace.append(stage)
            return {"mode": mode, "units": [unit.unit_id for unit in units], "synthetic": True}
        return action
    workflow = ProviderHooks(provider, *(hook(stage) for stage in
        ("protect", "claim", "create", "prepare", "stabilize", "measure", "close", "retrieve", "cleanup")))
    result = execute_provider_workflows({provider: workflow},
        context=LifecycleContext("session", 1, 100000, 100000), clock=FakeClock())
    assert result.completed
    assert trace.count("create") == trace.count("measure") == trace.count("cleanup") == 1
    assert result.provider_results[provider].values["measure"]["units"] == [unit.unit_id for unit in units]
    if mode in {"calibration", "diagnostic_day"}:
        assert trace.count("create") == 1  # no pair or TCP process per trial/chunk


def test_invalid_protocol_units_are_rejected_before_admission():
    with pytest.raises(ValueError, match="acceptance"):
        validate_mode_units("acceptance", [PhaseConfiguration(unit_id="bad", kind="phase", phase="multi_flow", measured_seconds=60, streams=4)])
