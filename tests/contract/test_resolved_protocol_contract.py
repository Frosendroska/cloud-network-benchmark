import json

import pytest
import yaml
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.contracts.admission import AdmissionRequirements
from cloud_network_benchmark.contracts.campaign import (
    CampaignConfiguration, PhaseConfiguration, ResolvedCampaign, ResolvedObservation,
    derive_measurement_units,
)


MODE_CONFIGS = (
    ("exp-903-three-provider-smoke.yaml", "acceptance", "tests"),
    ("exp-001-multi-provider.yaml", "short_study", "experiments"),
    ("exp-001-calibration.yaml", "calibration", "experiments"),
    ("exp-001-diagnostic-day.yaml", "diagnostic_day", "experiments"),
)
SCENARIOS = (
    ("exp-001-multi-provider.yaml", "same_zone"),
    ("exp-002-cross-zone.yaml", "cross_zone"),
    ("exp-003-placement-optimization.yaml", "placement_optimization"),
    ("exp-004-inter-region.yaml", "inter_region"),
)


def _source_case(repository_root, filename, mode, context, scenario):
    data = yaml.safe_load((repository_root / "configs/experiments" / filename).read_text())
    data["scenario"] = scenario
    data["mode"] = mode
    data["benchmark"]["measured_seconds"] = {"acceptance": 10, "short_study": 60,
                                                "calibration": 60, "diagnostic_day": 86400}[mode]
    data["benchmark"]["pause_seconds"] = 0 if mode == "diagnostic_day" else 10
    data["benchmark"]["probe_chunk_seconds"] = 300 if mode == "diagnostic_day" else None
    data["context"] = context
    if context != "standard":
        data["selected_providers"] = ["aws"]
        data["provider_configs"] = {"aws": data["provider_configs"]["aws"]}
    if context == "replacement":
        data["replacement_context"] = {"observation_id": "stable-observation", "replaces_run_id": "prior-aws"}
    else:
        data["replacement_context"] = None
    data["admission"].update(AdmissionRequirements.expected_for(mode, scenario))
    return data


def _resolved_case(repository_root, tmp_path, filename, mode, scenario):
    data = _source_case(repository_root, filename, mode, "standard", scenario)
    root = tmp_path / f"{mode}-{scenario}"
    folder = "tests" if mode == "acceptance" else "experiments"
    path = root / "configs" / folder / filename
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return resolve_campaign(path, root)[0]


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


@pytest.mark.parametrize("mode", ["acceptance", "short_study", "calibration", "diagnostic_day"])
@pytest.mark.parametrize("scenario", ["same_zone", "cross_zone", "placement_optimization", "inter_region"])
@pytest.mark.parametrize("context", ["standard", "failure_check", "replacement"])
def test_source_mode_scenario_context_matrix(repository_root, mode, scenario, context):
    filename = dict((scenario_name, name) for name, scenario_name in SCENARIOS)[scenario]
    data = _source_case(repository_root, filename, mode, context, scenario)
    valid = ((mode not in {"calibration", "diagnostic_day"} or scenario == "same_zone")
             and (context == "standard" or context == "failure_check" and mode == "acceptance"
                  or context == "replacement" and mode == "short_study"))
    schema = Draft202012Validator(CampaignConfiguration.model_json_schema())
    if valid:
        CampaignConfiguration.model_validate(data)
        assert schema.is_valid(data)
    else:
        with pytest.raises(ValidationError):
            CampaignConfiguration.model_validate(data)
        assert not schema.is_valid(data)


def test_source_replacement_link_must_name_the_selected_provider(repository_root):
    data = _source_case(repository_root, "exp-001-multi-provider.yaml", "short_study", "replacement", "same_zone")
    data["replacement_context"]["replaces_run_id"] = "prior-gcp"
    with pytest.raises(ValidationError, match="replacement provider"):
        CampaignConfiguration.model_validate(data)
    assert not Draft202012Validator(CampaignConfiguration.model_json_schema()).is_valid(data)


@pytest.mark.parametrize("context", ["standard", "failure_check", "replacement"])
@pytest.mark.parametrize("provider", ["aws", "azure", "gcp"])
@pytest.mark.parametrize("metadata_case", ["omitted", "null", "empty", "incomplete", "valid"])
def test_replacement_metadata_presence_matches_source_and_resolved_schemas(
    repository_root, tmp_path, context, provider, metadata_case
):
    mode = "acceptance" if context == "failure_check" else "short_study"
    scenario_file = "exp-001-multi-provider.yaml"
    source_data = _source_case(repository_root, scenario_file, mode, "standard", "same_zone")
    source_data["context"] = context
    if context != "standard":
        source_data["selected_providers"] = [provider]
        source_data["provider_configs"] = {provider: source_data["provider_configs"][provider]}
    replacement = {"observation_id": "stable-observation", "replaces_run_id": f"prior-{provider}"}
    supplied = {
        "null": None,
        "empty": {},
        "incomplete": {"observation_id": "stable-observation"},
        "valid": replacement,
    }
    if metadata_case == "omitted":
        source_data.pop("replacement_context", None)
    else:
        source_data["replacement_context"] = supplied[metadata_case]

    expected_valid = (context != "replacement" and metadata_case in {"omitted", "null"}) or (
        context == "replacement" and metadata_case == "valid")
    source_schema = Draft202012Validator(CampaignConfiguration.model_json_schema())
    if expected_valid:
        CampaignConfiguration.model_validate(source_data)
        assert source_schema.is_valid(source_data)
    else:
        with pytest.raises(ValidationError):
            CampaignConfiguration.model_validate(source_data)
        assert not source_schema.is_valid(source_data)

    resolved = _resolved_case(repository_root, tmp_path, scenario_file, mode, "same_zone")
    target = next(item for item in resolved.observations if item.provider == provider)
    observation_data = json.loads(target.model_dump_json())
    observation_data["context"] = context
    campaign_data = json.loads(resolved.model_dump_json())
    campaign_data["context"] = context
    if context != "standard":
        campaign_data["selected_providers"] = [provider]
        campaign_data["observations"] = [observation_data]
    else:
        campaign_data["observations"] = [
            json.loads(item.model_dump_json()) for item in resolved.observations
        ]
        for child in campaign_data["observations"]:
            child["context"] = context
    if metadata_case == "omitted":
        campaign_data.pop("replacement_context", None)
        observation_data.pop("replacement_context", None)
        for child in campaign_data["observations"]:
            child.pop("replacement_context", None)
    else:
        value = supplied[metadata_case]
        campaign_data["replacement_context"] = value
        observation_data["replacement_context"] = value
        for child in campaign_data["observations"]:
            child["replacement_context"] = value

    for payload, model in ((campaign_data, ResolvedCampaign), (observation_data, ResolvedObservation)):
        schema_valid = Draft202012Validator(model.model_json_schema()).is_valid(payload)
        if expected_valid:
            model.model_validate(payload)
            assert schema_valid
        else:
            with pytest.raises(ValidationError):
                model.model_validate(payload)
            assert not schema_valid


@pytest.mark.parametrize("mode", ["calibration", "diagnostic_day"])
def test_resolved_entry_points_reject_non_same_zone_with_consistent_admission(repository_root, mode):
    filename = "exp-001-calibration.yaml" if mode == "calibration" else "exp-001-diagnostic-day.yaml"
    resolved, _ = resolve_campaign(repository_root / "configs/experiments" / filename, repository_root)
    for dto, model in ((resolved, ResolvedCampaign), (resolved.observations[0], ResolvedObservation)):
        payload = json.loads(dto.model_dump_json())
        payload["scenario"] = "cross_zone"
        payload["admission"].update(AdmissionRequirements.expected_for(mode, "cross_zone"))
        with pytest.raises(ValidationError, match="same_zone"):
            model.model_validate(payload)
        assert not Draft202012Validator(model.model_json_schema()).is_valid(payload)


@pytest.mark.parametrize("provider", ["aws", "azure", "gcp"])
def test_replacement_provider_must_match_selection_and_observation(repository_root, provider):
    resolved, _ = resolve_campaign(repository_root / "configs/experiments/exp-001-multi-provider.yaml", repository_root)
    campaign_data = json.loads(resolved.model_dump_json())
    target = next(item for item in resolved.observations if item.provider == provider)
    observation_data = json.loads(target.model_dump_json())
    selected = [provider]
    replacement = {"observation_id": "stable-observation", "replaces_run_id": f"prior-{provider}"}
    campaign_data.update(context="replacement", replacement_context=replacement, selected_providers=selected,
                         observations=[json.loads(target.model_dump_json())])
    observation_data.update(context="replacement", replacement_context=replacement)
    campaign_data["observations"][0].update(context="replacement", replacement_context=replacement)
    assert ResolvedCampaign.model_validate(campaign_data).context == "replacement"
    assert ResolvedObservation.model_validate(observation_data).context == "replacement"
    assert Draft202012Validator(ResolvedCampaign.model_json_schema()).is_valid(campaign_data)
    assert Draft202012Validator(ResolvedObservation.model_json_schema()).is_valid(observation_data)

    wrong = "gcp" if provider != "gcp" else "aws"
    campaign_data["replacement_context"]["replaces_run_id"] = f"prior-{wrong}"
    campaign_data["observations"][0]["replacement_context"]["replaces_run_id"] = f"prior-{wrong}"
    observation_data["replacement_context"]["replaces_run_id"] = f"prior-{wrong}"
    with pytest.raises(ValidationError, match="replacement provider"):
        ResolvedCampaign.model_validate(campaign_data)
    with pytest.raises(ValidationError, match="replacement provider"):
        ResolvedObservation.model_validate(observation_data)
    assert not Draft202012Validator(ResolvedCampaign.model_json_schema()).is_valid(campaign_data)
    assert not Draft202012Validator(ResolvedObservation.model_json_schema()).is_valid(observation_data)


@pytest.mark.parametrize(("filename", "scenario"), SCENARIOS)
@pytest.mark.parametrize("mode", ["acceptance", "short_study"])
@pytest.mark.parametrize("context", ["failure_check", "replacement"])
def test_resolved_mode_context_matrix(repository_root, tmp_path, filename, scenario, mode, context):
    resolved = _resolved_case(repository_root, tmp_path, filename, mode, scenario)
    aws = next(item for item in resolved.observations if item.provider == "aws")
    expected_valid = (context == "failure_check" and mode == "acceptance") or (
        context == "replacement" and mode == "short_study")
    observation_data = json.loads(aws.model_dump_json())
    observation_data["context"] = context
    observation_data["replacement_context"] = (
        {"observation_id": "stable-observation", "replaces_run_id": "prior-aws"}
        if context == "replacement" else None
    )
    campaign_data = json.loads(resolved.model_dump_json())
    campaign_data.update(context=context, selected_providers=["aws"],
                         observations=[observation_data],
                         replacement_context=observation_data["replacement_context"])
    for payload, model in ((campaign_data, ResolvedCampaign), (observation_data, ResolvedObservation)):
        schema_valid = Draft202012Validator(model.model_json_schema()).is_valid(payload)
        if expected_valid:
            model.model_validate(payload)
            assert schema_valid
        else:
            with pytest.raises(ValidationError):
                model.model_validate(payload)
            assert not schema_valid


@pytest.mark.parametrize(("filename", "scenario"), SCENARIOS)
@pytest.mark.parametrize("mode", ["acceptance", "short_study"])
def test_resolved_entry_points_accept_each_retained_scenario(repository_root, tmp_path, filename, scenario, mode):
    resolved = _resolved_case(repository_root, tmp_path, filename, mode, scenario)
    campaign_payload = json.loads(resolved.model_dump_json())
    observation_payload = json.loads(resolved.observations[0].model_dump_json())
    assert ResolvedCampaign.model_validate(campaign_payload).scenario == scenario
    assert ResolvedObservation.model_validate(observation_payload).scenario == scenario
    assert Draft202012Validator(ResolvedCampaign.model_json_schema()).is_valid(campaign_payload)
    assert Draft202012Validator(ResolvedObservation.model_json_schema()).is_valid(observation_payload)


@pytest.mark.parametrize(("filename", "mode", "directory"), MODE_CONFIGS)
@pytest.mark.parametrize("common_n_case", ["omitted", "state_omitted", "pending", "frozen"])
def test_resolved_common_n_defaults_match_schema_and_runtime(repository_root, filename, mode, directory, common_n_case):
    resolved, _ = resolve_campaign(repository_root / "configs" / directory / filename, repository_root)
    for dto, model in ((resolved, ResolvedCampaign), (resolved.observations[0], ResolvedObservation)):
        payload = json.loads(dto.model_dump_json())
        if common_n_case == "omitted":
            payload["benchmark"].pop("common_n")
        elif common_n_case == "state_omitted":
            payload["benchmark"]["common_n"].pop("state")
        elif common_n_case == "pending":
            payload["benchmark"]["common_n"] = {"state": "pending", "common_n": None,
                                                  "source_campaign_id": None, "source_sha256": None,
                                                  "plateau_found": None}
        else:
            payload["benchmark"]["common_n"] = {"state": "frozen", "common_n": 8,
                                                  "source_campaign_id": "calibration-run",
                                                  "source_sha256": "a" * 64, "plateau_found": False}
        parsed_benchmark = dto.benchmark.model_validate(payload["benchmark"])
        payload["measurement_units"] = [unit.model_dump(mode="json") for unit in
                                         derive_measurement_units(dto.mode, parsed_benchmark)]
        if isinstance(dto, ResolvedCampaign):
            for child in payload["observations"]:
                child["benchmark"] = payload["benchmark"]
                child["measurement_units"] = payload["measurement_units"]
            if common_n_case == "frozen":
                payload["launch_blockers"] = [item for item in payload["launch_blockers"]
                                               if item != "calibration_pending"]
        assert model.model_validate(payload)
        assert Draft202012Validator(model.model_json_schema()).is_valid(payload)


@pytest.mark.parametrize(("filename", "mode", "directory"), MODE_CONFIGS)
def test_resolved_frozen_n_requires_complete_evidence(repository_root, filename, mode, directory):
    resolved, _ = resolve_campaign(repository_root / "configs" / directory / filename, repository_root)
    for dto, model in ((resolved, ResolvedCampaign), (resolved.observations[0], ResolvedObservation)):
        payload = json.loads(dto.model_dump_json())
        payload["benchmark"]["common_n"] = {"state": "frozen", "common_n": 8}
        payload["measurement_units"] = [unit.model_dump(mode="json") for unit in
                                         derive_measurement_units(dto.mode, dto.benchmark.model_copy(update={
                                             "common_n": dto.benchmark.common_n.model_copy(update={
                                                 "state": "frozen", "common_n": 8, "source_campaign_id": "calibration-run",
                                                 "source_sha256": "a" * 64, "plateau_found": False,
                                             })
                                         }))]
        if isinstance(dto, ResolvedCampaign):
            for child in payload["observations"]:
                child["benchmark"] = payload["benchmark"]
                child["measurement_units"] = payload["measurement_units"]
            payload["launch_blockers"] = [item for item in payload["launch_blockers"]
                                           if item != "calibration_pending"]
        with pytest.raises(ValidationError):
            model.model_validate(payload)
        assert not Draft202012Validator(model.model_json_schema()).is_valid(payload)


@pytest.mark.parametrize(("filename", "mode", "directory"), [case for case in MODE_CONFIGS if case[1] != "calibration"])
def test_omitted_common_n_defaults_pending_stream_derivation(repository_root, filename, mode, directory):
    resolved, _ = resolve_campaign(repository_root / "configs" / directory / filename, repository_root)
    for dto, model in ((resolved, ResolvedCampaign), (resolved.observations[0], ResolvedObservation)):
        payload = json.loads(dto.model_dump_json())
        payload["benchmark"].pop("common_n")
        unit_index = 2 if mode in {"acceptance", "short_study"} else 0
        payload["measurement_units"][unit_index]["streams"] = 2
        if isinstance(dto, ResolvedCampaign):
            for child in payload["observations"]:
                child["benchmark"] = payload["benchmark"]
                child["measurement_units"] = payload["measurement_units"]
        with pytest.raises(ValidationError):
            model.model_validate(payload)
        assert not Draft202012Validator(model.model_json_schema()).is_valid(payload)
