import json

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.contracts.campaign import PhaseConfiguration, ResolvedCampaign, ResolvedObservation


MODE_CONFIGS = (
    ("exp-903-three-provider-smoke.yaml", "acceptance", "tests"),
    ("exp-001-multi-provider.yaml", "short_study", "experiments"),
    ("exp-001-calibration.yaml", "calibration", "experiments"),
    ("exp-001-diagnostic-day.yaml", "diagnostic_day", "experiments"),
)


@pytest.mark.parametrize(("filename", "mode", "directory"), MODE_CONFIGS)
def test_resolved_dtos_validate_protocol_on_json_round_trip(repository_root, filename, mode, directory):
    resolved, _ = resolve_campaign(repository_root / "configs" / directory / filename, repository_root)
    campaign_data = json.loads(resolved.model_dump_json())
    observation_data = json.loads(resolved.observations[0].model_dump_json())
    assert ResolvedCampaign.model_validate_json(json.dumps(campaign_data)).mode == mode
    assert ResolvedObservation.model_validate_json(json.dumps(observation_data)).mode == mode
    assert Draft202012Validator(ResolvedCampaign.model_json_schema()).is_valid(campaign_data)
    assert Draft202012Validator(ResolvedObservation.model_json_schema()).is_valid(observation_data)


@pytest.mark.parametrize(("filename", "mode", "directory"), MODE_CONFIGS)
def test_resolved_dtos_reject_agreeing_but_wrong_duration_and_derived_units(repository_root, filename, mode, directory):
    resolved, _ = resolve_campaign(repository_root / "configs" / directory / filename, repository_root)
    for dto, model in ((resolved, ResolvedCampaign), (resolved.observations[0], ResolvedObservation)):
        payload = json.loads(dto.model_dump_json())
        payload["benchmark"]["measured_seconds"] = 999
        for unit in payload["measurement_units"]:
            if unit["kind"] != "probe_chunk":
                unit["measured_seconds"] = 999
        with pytest.raises(ValidationError):
            model.model_validate(payload)
        assert not Draft202012Validator(model.model_json_schema()).is_valid(payload)


@pytest.mark.parametrize(("filename", "mode", "directory"), MODE_CONFIGS)
def test_resolved_dtos_reject_admission_that_disagrees_with_mode_and_scenario(repository_root, filename, mode, directory):
    resolved, _ = resolve_campaign(repository_root / "configs" / directory / filename, repository_root)
    for dto, model in ((resolved, ResolvedCampaign), (resolved.observations[0], ResolvedObservation)):
        payload = json.loads(dto.model_dump_json())
        payload["admission"]["lifetime_cap_seconds"] = 1800 if mode != "calibration" else 1200
        with pytest.raises(ValidationError):
            model.model_validate(payload)
        assert not Draft202012Validator(model.model_json_schema()).is_valid(payload)


@pytest.mark.parametrize("duration", [1, 300])
def test_probe_chunk_accepts_documented_duration_boundaries(duration):
    unit = PhaseConfiguration(unit_id="probe-1", kind="probe_chunk", phase="multi_flow", measured_seconds=duration,
                              parent_unit_id="diagnostic-day")
    assert unit.measured_seconds == duration
    assert Draft202012Validator(PhaseConfiguration.model_json_schema()).is_valid(unit.model_dump(mode="json"))


def test_probe_chunk_rejects_duration_above_documented_maximum():
    value = {"unit_id": "probe-1", "kind": "probe_chunk", "phase": "multi_flow", "measured_seconds": 301,
             "parent_unit_id": "diagnostic-day"}
    with pytest.raises(ValidationError, match="300 seconds"):
        PhaseConfiguration.model_validate(value)
    assert not Draft202012Validator(PhaseConfiguration.model_json_schema()).is_valid(value)
