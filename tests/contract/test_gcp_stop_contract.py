from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.contracts import CampaignConfiguration, ProviderDeploymentInput, ResolvedCampaign, StringEvidence
from cloud_network_benchmark.contracts.schema import SCHEMAS


EXAMPLES = (
    "configs/tests/exp-903-three-provider-smoke.yaml",
    "configs/experiments/exp-001-multi-provider.yaml",
    "configs/experiments/exp-001-calibration.yaml",
    "configs/experiments/exp-001-diagnostic-day.yaml",
    "configs/experiments/exp-002-cross-zone.yaml",
    "configs/experiments/exp-003-placement-optimization.yaml",
    "configs/experiments/exp-004-inter-region.yaml",
    "configs/tests/exp-902-gcp-single-provider.yaml",
)


def mutations(options: dict[str, object]):
    for value in ("DELETE", None, "", "stop", "Stop", "unexpected", False, 1, [], {}):
        yield f"action={value!r}", {**options, "instance_termination_action": value}
    omitted_action = dict(options)
    del omitted_action["instance_termination_action"]
    yield "action omitted", omitted_action
    for value in ("SPOT", "unexpected", None, False, 1, [], {}):
        yield f"provisioning={value!r}", {**options, "provisioning_model": value}
    omitted_model = dict(options)
    del omitted_model["provisioning_model"]
    yield "provisioning omitted", omitted_model
    yield "preemptible true", {**options, "preemptible": True}
    yield "preemptible false", {**options, "preemptible": False}
    yield "unknown key", {**options, "unknown": "value"}


def set_options(document: dict, options: dict[str, object], kind: str) -> dict:
    value = deepcopy(document)
    if kind == "config":
        value["provider_configs"]["gcp"]["provider_options"] = options
    elif kind == "resolved":
        next(item for item in value["observations"] if item["provider"] == "gcp")["provider_config"]["provider_options"] = options
    else:
        value["provider_options"] = options
    return value


def envelopes(repository_root: Path) -> tuple[dict, dict, dict]:
    config = yaml.safe_load((repository_root / EXAMPLES[0]).read_text(encoding="utf-8"))
    options = config["provider_configs"]["gcp"]["provider_options"]
    resolved, _ = resolve_campaign(repository_root / EXAMPLES[0], repository_root)
    resolved_doc = resolved.model_dump(mode="json")
    next(item for item in resolved_doc["observations"] if item["provider"] == "gcp")["provider_config"]["provider_options"] = {
        "provisioning_model": "STANDARD", "instance_termination_action": "STOP"
    }
    config["provider_configs"]["gcp"]["provider_options"] = {
        "provisioning_model": "STANDARD", "instance_termination_action": "STOP"
    }
    observation = next(item for item in resolved.observations if item.provider == "gcp")
    deployment = ProviderDeploymentInput.from_observation(
        observation,
        campaign_id="stop-contract",
        run_id="stop-contract-gcp",
        bootstrap_template="bootstrap/cloud-init.yaml",
        terraform_directory="terraform/gcp/pair",
        child_result_path="results/raw/stop-contract-gcp",
        config_sha256=resolved.config_source.sha256,
        implementation_git_commit=StringEvidence(value="a" * 40),
        design_git_commit=StringEvidence(unavailable_reason="offline contract fixture"),
    ).model_dump(mode="json")
    deployment["provider_options"] = {"provisioning_model": "STANDARD", "instance_termination_action": "STOP"}
    # The original config option is deliberately read above to anchor the fixture to the existing shape.
    assert set(options) == {"provisioning_model", "instance_termination_action"}
    return config, resolved_doc, deployment


@pytest.mark.parametrize("example", EXAMPLES)
def test_active_gcp_examples_explicitly_declare_standard_stop(repository_root: Path, example: str) -> None:
    document = yaml.safe_load((repository_root / example).read_text(encoding="utf-8"))
    options = document["provider_configs"]["gcp"]["provider_options"]
    assert options == {"provisioning_model": "STANDARD", "instance_termination_action": "STOP"}
    CampaignConfiguration.model_validate(document)


@pytest.mark.parametrize("case,options", list(mutations({"provisioning_model": "STANDARD", "instance_termination_action": "STOP"})))
def test_runtime_rejects_every_gcp_option_mutation(repository_root: Path, case: str, options: dict[str, object]) -> None:
    config, resolved, deployment = envelopes(repository_root)
    with pytest.raises(ValidationError):
        CampaignConfiguration.model_validate(set_options(config, options, "config"))
    with pytest.raises(ValidationError):
        ResolvedCampaign.model_validate(set_options(resolved, options, "resolved"))
    with pytest.raises(ValidationError):
        ProviderDeploymentInput.model_validate(set_options(deployment, options, "deployment"))


@pytest.mark.parametrize("case,options", list(mutations({"provisioning_model": "STANDARD", "instance_termination_action": "STOP"})))
def test_canonical_schemas_reject_every_gcp_option_mutation(repository_root: Path, case: str, options: dict[str, object]) -> None:
    config, resolved, deployment = envelopes(repository_root)
    values = (
        ("campaign-config.schema.json", config, "config"),
        ("resolved-campaign.schema.json", resolved, "resolved"),
        ("provider-deployment-input.schema.json", deployment, "deployment"),
    )
    for schema_name, envelope, kind in values:
        schema = SCHEMAS[schema_name].model_json_schema(mode="validation")
        validator = Draft202012Validator(schema)
        candidate = set_options(envelope, options, kind)
        assert not validator.is_valid(candidate), f"{schema_name} accepted {case}"


def test_stop_schemas_accept_valid_envelopes_and_preserve_aws_azure_controls(repository_root: Path) -> None:
    config, resolved, deployment = envelopes(repository_root)
    assert CampaignConfiguration.model_validate(config)
    assert ResolvedCampaign.model_validate(resolved)
    assert ProviderDeploymentInput.model_validate(deployment)
    for schema_name, envelope in (
        ("campaign-config.schema.json", config),
        ("resolved-campaign.schema.json", resolved),
        ("provider-deployment-input.schema.json", deployment),
    ):
        assert Draft202012Validator(SCHEMAS[schema_name].model_json_schema()).is_valid(envelope)
    for example in EXAMPLES:
        document = yaml.safe_load((repository_root / example).read_text(encoding="utf-8"))
        schema = SCHEMAS["campaign-config.schema.json"].model_json_schema()
        assert Draft202012Validator(schema).is_valid(document)
        parsed = CampaignConfiguration.model_validate(document)
        resolved_value, _ = resolve_campaign(repository_root / example, repository_root)
        gcp = next(item for item in resolved_value.observations if item.provider == "gcp")
        assert gcp.provider_config.provider_options == {"provisioning_model": "STANDARD", "instance_termination_action": "STOP"}
        assert Draft202012Validator(SCHEMAS["resolved-campaign.schema.json"].model_json_schema()).is_valid(
            resolved_value.model_dump(mode="json")
        )
        handoff = ProviderDeploymentInput.from_observation(
            gcp, campaign_id="stop-contract", run_id="stop-contract-gcp",
            bootstrap_template="bootstrap/cloud-init.yaml", terraform_directory="terraform/gcp/pair",
            child_result_path="results/raw/stop-contract-gcp", config_sha256=resolved_value.config_source.sha256,
            implementation_git_commit=StringEvidence(value="a" * 40),
            design_git_commit=StringEvidence(unavailable_reason="offline contract fixture"),
        )
        assert handoff.provider_options == gcp.provider_config.provider_options
        assert Draft202012Validator(SCHEMAS["provider-deployment-input.schema.json"].model_json_schema()).is_valid(
            handoff.model_dump(mode="json")
        )
        assert resolved_value.mode == parsed.mode


def test_cli_previews_retain_stop_and_pending_gates(repository_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from cloud_network_benchmark.cli import main

    expected_modes = {"acceptance", "short_study", "calibration", "diagnostic_day"}
    seen_modes = set()
    seen_scenarios = set()
    seen_scenario_ids = set()
    for example in EXAMPLES:
        assert main(["dry-run", "--config", example, "--format", "json"]) == 0
        preview = json.loads(capsys.readouterr().out)
        seen_modes.add(preview["mode"])
        seen_scenarios.add(preview["scenario"])
        scenario_id = preview["campaign_options"]["labels"].get("scenario_id")
        if scenario_id is not None:
            seen_scenario_ids.add(scenario_id)
        child = next(item for item in preview["children"] if item["provider"] == "gcp")
        assert child["provider_options"] == {"provisioning_model": "STANDARD", "instance_termination_action": "STOP"}
        assert preview["launch_blockers"]
        assert preview["admission"]["requires_expiry"] is True
        assert preview["config_sha256"] == hashlib.sha256((repository_root / example).read_bytes()).hexdigest()
    assert seen_modes == expected_modes
    assert seen_scenarios == {"same_zone", "cross_zone", "placement_optimization", "inter_region"}
    assert seen_scenario_ids == {"S1", "S2", "S3", "S4"}
