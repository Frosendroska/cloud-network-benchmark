from __future__ import annotations

import copy
from pathlib import Path

import jsonschema
import pytest
import yaml

from cloud_network_benchmark.contracts.admission import AdmissionRequirements, ReservationRequest
from cloud_network_benchmark.contracts.campaign import CampaignConfiguration
from cloud_network_benchmark.contracts.common import Evidence, ExperimentMode, Provider, Scenario, StringEvidence
from cloud_network_benchmark.contracts.deployment import ProviderDeploymentInput

ROOT = Path(__file__).parents[2]
PROFILE = "azure_exp901_sweden_acceptance_v1"


def _profile_campaign():
    data = yaml.safe_load((ROOT / "configs/tests/exp-901-azure-single-provider.yaml").read_text())
    azure = data["provider_configs"]["azure"]
    azure["regions"] = {"vm_a": "swedencentral", "vm_b": "swedencentral"}
    azure["network"]["address_spaces"] = {"swedencentral": "10.70.0.0/16"}
    azure["network"]["subnets"] = {"swedencentral": ["10.70.0.0/24"]}
    data["admission"]["timing_profile"] = PROFILE
    data["admission"]["lifetime_cap_seconds"] = 1800
    return data


def test_profile_campaign_scope_and_generated_schema_match():
    data = _profile_campaign()
    CampaignConfiguration.model_validate(data)
    jsonschema.Draft202012Validator(CampaignConfiguration.model_json_schema()).validate(data)

    schema_data = CampaignConfiguration.model_validate(data).model_dump(mode="json")
    validator = jsonschema.Draft202012Validator(CampaignConfiguration.model_json_schema())
    for mutate in (
        lambda d: d.update(experiment_id="EXP-900"),
        lambda d: d.update(context="standard"),
        lambda d: d.update(selected_providers=["azure", "aws"]),
        lambda d: d.update(scenario="cross_zone"),
        lambda d: d["provider_configs"]["azure"].update(vm_shape="Standard_D2s_v5"),
        lambda d: d["provider_configs"]["azure"]["regions"].update(vm_a="SWEDENCENTRAL", vm_b="SWEDENCENTRAL"),
        lambda d: d["provider_configs"]["azure"]["zones"].update(vm_a=1),
        lambda d: d["admission"].update(lifetime_cap_seconds=1200),
    ):
        bad = copy.deepcopy(data)
        mutate(bad)
        with pytest.raises((ValueError, jsonschema.ValidationError)):
            CampaignConfiguration.model_validate(bad)
        bad_schema = copy.deepcopy(schema_data)
        mutate(bad_schema)
        with pytest.raises(jsonschema.ValidationError):
            validator.validate(bad_schema)


def test_unprofiled_acceptance_retains_1200_default_and_serialization():
    data = yaml.safe_load((ROOT / "configs/tests/exp-901-azure-single-provider.yaml").read_text())
    campaign = CampaignConfiguration.model_validate(data)
    assert campaign.admission.lifetime_cap_seconds == 1200
    assert "timing_profile" not in campaign.admission.model_dump(mode="json")
    assert AdmissionRequirements.expected_for(ExperimentMode.ACCEPTANCE, Scenario.SAME_ZONE)["lifetime_cap_seconds"] == 1200


def _reservation_request(**updates):
    data = dict(schema_version=2, campaign_id="profile-campaign-azure", config_id="exp-901-acceptance",
        schedule_entry_id=Evidence[str](unavailable_reason="acceptance has no schedule"),
        selected_providers=[Provider.AZURE], mode=ExperimentMode.ACCEPTANCE, priority="P0",
        lifetime_cap_seconds=1800, timing_profile=PROFILE, experiment_id="EXP-901",
        context="failure_check", scenario=Scenario.SAME_ZONE, config_sha256="a" * 64,
        resolved_sha256="b" * 64, estimated_cost_usd=Evidence[float](unavailable_reason="fixture"),
        source_path="fixture-cost-input.json", source_sha256="c" * 64,
        incurred_cost_usd=Evidence[float](unavailable_reason="fixture"),
        active_reserved_usd=Evidence[float](unavailable_reason="fixture"), outstanding_cleanup=False)
    data.update(updates)
    return data


@pytest.mark.parametrize(("case", "updates"), [
    ("multi_provider", {"selected_providers": ["azure", "aws"]}),
    ("wrong_experiment", {"experiment_id": "EXP-900"}),
    ("wrong_context", {"context": "standard"}),
    ("wrong_scenario", {"scenario": "cross_zone"}),
    ("underdeclared_cap", {"lifetime_cap_seconds": 1200}),
    ("missing_config_hash", {"config_sha256": None}),
    ("missing_resolved_hash", {"resolved_sha256": None}),
    ("unscoped_1800", {"timing_profile": None, "lifetime_cap_seconds": 1800}),
], ids=lambda value: value if isinstance(value, str) else None)
def test_profile_reservation_request_is_scoped_and_schema_aligned(case, updates):
    data = _reservation_request()
    ReservationRequest.model_validate(data)
    schema_data = ReservationRequest.model_validate(data).model_dump(mode="json")
    validator = jsonschema.Draft202012Validator(ReservationRequest.model_json_schema())
    validator.validate(schema_data)
    bad = {**data, **updates}
    with pytest.raises(ValueError):
        ReservationRequest.model_validate(bad)
    bad_schema = {**schema_data, **updates}
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(bad_schema)


def test_profile_cannot_be_asserted_against_unprofiled_campaign(monkeypatch, tmp_path):
    from cloud_network_benchmark.admission import BudgetLedger
    from types import SimpleNamespace
    from datetime import datetime, timezone

    data = yaml.safe_load((ROOT / "configs/tests/exp-901-azure-single-provider.yaml").read_text())
    campaign = SimpleNamespace(config_id=data["config_id"], mode="acceptance", experiment_id="EXP-901",
        context="failure_check", scenario="same_zone", selected_providers=["azure"],
        admission=SimpleNamespace(priority="P0", lifetime_cap_seconds=1200, timing_profile=None),
        config_source=SimpleNamespace(sha256="d" * 64), resolved_sha256="e" * 64)
    ledger = BudgetLedger(tmp_path / "ledger.json", scope_id="offline-test",
        utc_now=lambda: datetime(2026, 11, 20, tzinfo=timezone.utc), monotonic_now=lambda: 1.0,
        controller_session_id="offline-test")
    plan = SimpleNamespace(resolved_campaign=campaign)
    result = ledger.reserve(ReservationRequest.model_validate(_reservation_request(config_sha256="d"*64,
        resolved_sha256="e"*64)), plan=plan)
    assert result.status == "rejected"
    assert "identity_mismatch" in result.reasons


def test_profile_mismatch_admission_never_constructs_provider_workflows(tmp_path):
    from cloud_network_benchmark.admission import BudgetLedger
    from cloud_network_benchmark.orchestration import run_lifecycle
    from types import SimpleNamespace
    from datetime import datetime, timezone

    data = yaml.safe_load((ROOT / "configs/tests/exp-901-azure-single-provider.yaml").read_text())
    campaign = SimpleNamespace(config_id=data["config_id"], mode="acceptance", experiment_id="EXP-901",
        context="failure_check", scenario="same_zone", selected_providers=["azure"],
        admission=SimpleNamespace(priority="P0", lifetime_cap_seconds=1200, timing_profile=None),
        config_source=SimpleNamespace(sha256="d" * 64), resolved_sha256="e" * 64)
    plan = SimpleNamespace(execution_scope="paid_request", resolved_campaign=campaign, allowance=object())
    ledger = BudgetLedger(tmp_path / "ledger.json", scope_id="offline-test",
        utc_now=lambda: datetime(2026, 11, 20, tzinfo=timezone.utc), monotonic_now=lambda: 1.0,
        controller_session_id="offline-test")
    request = ReservationRequest.model_validate(_reservation_request(config_sha256="d"*64,
        resolved_sha256="e"*64))
    factory_calls = []
    result = run_lifecycle(ledger=ledger, reservation_request=request, admission_plan=plan,
        allowance=object(), workflow_factory=lambda _reservation: factory_calls.append("called"),
        clock=SimpleNamespace(monotonic=lambda: 1.0, utc_now=lambda: datetime(2026, 11, 20, tzinfo=timezone.utc)))
    assert result.admission_status == "rejected"
    assert "identity_mismatch" in result.admission_reasons
    assert factory_calls == []


def test_profile_resolves_to_source_bound_deployment_and_generated_schema(tmp_path):
    from cloud_network_benchmark.config import resolve_campaign
    from cloud_network_benchmark.contracts.admission import ReservationEvidence
    from datetime import datetime, timedelta, timezone

    config_path = tmp_path / "configs/tests/exp-901-profile.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(yaml.safe_dump(_profile_campaign(), sort_keys=False))
    campaign, _ = resolve_campaign(config_path, tmp_path)
    assert campaign.admission.timing_profile == PROFILE
    jsonschema.Draft202012Validator(type(campaign).model_json_schema()).validate(campaign.model_dump(mode="json"))
    observation = campaign.observations[0]
    jsonschema.Draft202012Validator(type(observation).model_json_schema()).validate(observation.model_dump(mode="json"))
    altered_observation = observation.model_dump(mode="json")
    altered_observation["provider_config"]["vm_shape"] = "Standard_D2s_v5"
    altered_observation["vm_a"]["vm_shape"] = "Standard_D2s_v5"
    altered_observation["vm_b"]["vm_shape"] = "Standard_D2s_v5"
    with pytest.raises(ValueError, match="reviewed Azure pair"):
        type(observation).model_validate(altered_observation)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(type(observation).model_json_schema()).validate(altered_observation)
    altered_campaign = campaign.model_dump(mode="json")
    altered_campaign["observations"][0]["admission"]["timing_profile"] = None
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(type(campaign).model_json_schema()).validate(altered_campaign)
    started = datetime(2026, 11, 20, tzinfo=timezone.utc)
    campaign_id = "20261120T150000Z-exp-901-profile"
    reservation = ReservationEvidence(schema_version=2, campaign_id=campaign_id,
        timing_profile=PROFILE, reservation_id=Evidence[str](value="reservation-profile"), state="reserved",
        reserved_cost_usd=Evidence[float](value=2.0), cap_started_at=Evidence[datetime](value=started),
        expires_at=Evidence[datetime](value=started + timedelta(seconds=1800)),
        cap_start_policy_id=StringEvidence(value="offline-stage1-fixture"), source_path="fixture-costs.json",
        source_sha256="c" * 64, reasons=[])
    deployment = ProviderDeploymentInput.from_observation(observation, campaign_id=campaign_id,
        run_id=f"{campaign_id}-azure", bootstrap_template="bootstrap.yaml",
        terraform_directory="terraform/azure/pair", child_result_path="results/raw/fixture",
        config_sha256=campaign.config_source.sha256,
        resolved_sha256=StringEvidence(value=campaign.resolved_sha256),
        implementation_git_commit=StringEvidence(unavailable_reason="offline stage1 fixture"),
        design_git_commit=StringEvidence(unavailable_reason="offline stage1 fixture"),
        reservation=Evidence[ReservationEvidence](value=reservation))
    assert deployment.admission.timing_profile == PROFILE
    assert deployment.reservation.value.timing_profile == PROFILE
    jsonschema.Draft202012Validator(ProviderDeploymentInput.model_json_schema()).validate(
        deployment.model_dump(mode="json"))
    mismatched = deployment.model_dump(mode="python")
    mismatched["reservation"]["value"]["timing_profile"] = None
    with pytest.raises(ValueError):
        ProviderDeploymentInput.model_validate(mismatched)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(ProviderDeploymentInput.model_json_schema()).validate(
            ProviderDeploymentInput.model_validate(deployment.model_dump(mode="python")).model_dump(mode="json")
            | {"reservation": {"value": {**deployment.model_dump(mode="json")["reservation"]["value"], "timing_profile": None}}})
