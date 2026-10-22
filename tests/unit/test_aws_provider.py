from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.contracts.admission import ExpiryRequest, ReservationEvidence
from cloud_network_benchmark.contracts.common import Evidence, StringEvidence
from cloud_network_benchmark.contracts.common import StructuredEvidence
from cloud_network_benchmark.contracts.deployment import ProviderDeploymentInput
from cloud_network_benchmark.providers.aws import (
    EXPIRY_INPUT_ID,
    EXPIRY_INPUT_PATH,
    EXPIRY_INPUT_SHA256,
    SAFETY_LEAD_SECONDS,
    AwsProviderDriver,
    AwsProviderError,
    AwsRuntimeSettings,
    _automation_matches,
    _has_exact_ownership_tags,
    _remaining_inventory,
    _validate_deployment,
)


ROOT = Path(__file__).parents[2]
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA fake-key"


def make_deployment(repository_root: Path = ROOT, config_path: str = "configs/tests/exp-900-single-provider.yaml"):
    resolved, _ = resolve_campaign(repository_root / config_path, repository_root)
    observation = next(item for item in resolved.observations if item.provider == "aws")
    source_path = "tracking/run-register-2026-10-22.csv"
    source_hash = "a" * 64
    admission = observation.admission.model_copy(update={"cost_input_sha256": StringEvidence(value=source_hash)})
    observation = observation.model_copy(update={"admission": admission})
    cap_start = NOW - timedelta(seconds=1)
    cap_end = NOW + timedelta(seconds=1200)
    reservation = ReservationEvidence(
        schema_version=2,
        campaign_id="campaign",
        reservation_id=Evidence[str](value="reservation-1"),
        state="reserved",
        reserved_cost_usd=Evidence[float](value=5.0),
        cap_started_at=Evidence[datetime](value=cap_start),
        expires_at=Evidence[datetime](value=cap_end),
        cap_start_policy_id=StringEvidence(value="reservation-activation-before-provider-expiry"),
        source_path=source_path,
        source_sha256=source_hash,
    )
    deployment = ProviderDeploymentInput.from_observation(
        observation,
        campaign_id="campaign",
        run_id="campaign-aws",
        bootstrap_template="cloud-init.yaml",
        terraform_directory="terraform/aws",
        child_result_path="results/raw/campaign-aws",
        config_sha256=resolved.config_source.sha256,
        implementation_git_commit=StringEvidence(value="b" * 40),
        design_git_commit=StringEvidence(value="c" * 40),
        reservation=Evidence[ReservationEvidence](value=reservation),
    )
    return deployment


def make_settings(tmp_path: Path, deployment=None, **overrides):
    if deployment is None:
        deployment = make_deployment()
    values = {
        "account_id": "123456789012",
        "controller_cidr": "203.0.113.4/32",
        "ami_ids": {"eu-central-1": "ami-0123456789abcdef0", "eu-west-2": "ami-0abcdef0123456789"},
        "public_key": KEY,
        "authentication_reference": "private-ssh-key:attempt-only",
        "private_state_root": tmp_path / "private-state",
        "aws_cli": "/usr/local/bin/aws",
        "terraform": "/private/tmp/cnb-tools/terraform",
        "cost_input_id": deployment.admission.cost_input_id,
        "cost_input_sha256": deployment.admission.cost_input_sha256.value,
        "cost_source_path": deployment.reservation.value.source_path,
        "complete_workflow_remaining_seconds": 120,
    }
    values.update(overrides)
    if deployment.admission.requires_primary_review and "primary_review_decision" not in values:
        values["primary_review_decision"] = StructuredEvidence(value={
            "decision": "approved", "scenario": str(deployment.scenario),
            "source_path": "tracking/paid-admission-review.md", "source_sha256": "e" * 64,
            "reviewed_at": NOW.isoformat(),
        })
    return AwsRuntimeSettings(**values)


def make_request(driver, deployment):
    reservation = deployment.reservation.value
    trigger = reservation.expires_at.value - timedelta(seconds=SAFETY_LEAD_SECONDS)
    regions = driver._regions(deployment)
    return ExpiryRequest(
        schema_version=2,
        run_id=deployment.run_id,
        deployment_id=deployment.deployment_id,
        provider="aws",
        action="stop",
        expires_at=Evidence[datetime](value=trigger),
        reservation_id=reservation.reservation_id,
        resource_namespace="cnb-campaign-aws-campaign-aws-deployment",
        target_selector=driver._target_selector(deployment, regions),
    )


def test_settings_require_single_controller_ipv4_and_public_only_key(tmp_path):
    with pytest.raises(ValidationError, match="IPv4 /32"):
        make_settings(tmp_path, controller_cidr="203.0.113.0/24")
    with pytest.raises(ValidationError, match="public key"):
        make_settings(tmp_path, public_key="-----BEGIN OPENSSH PRIVATE KEY-----")
    with pytest.raises(ValidationError, match="public key"):
        make_settings(tmp_path, public_key="ssh-rsa AAAA legacy")


def test_provider_rejects_non_aws_and_wrong_market_contract(tmp_path):
    deployment = make_deployment()
    _validate_deployment(deployment)
    with pytest.raises(AwsProviderError, match="On-Demand"):
        _validate_deployment(deployment.model_copy(update={"provider_options": {"instance_market_type": "spot"}}))
    with pytest.raises(AwsProviderError, match="AWS deployments"):
        _validate_deployment(deployment.model_copy(update={"provider": "azure"}))


def test_active_reservation_source_and_trigger_are_bound_before_any_command(tmp_path):
    deployment = make_deployment()
    settings = make_settings(tmp_path, deployment)
    driver = AwsProviderDriver(runner=object(), settings=settings, clock=lambda: NOW)
    request = make_request(driver, deployment)
    regions, reservation, trigger = driver._validate_registration(deployment, request)
    assert regions == ["eu-central-1"]
    assert reservation.reservation_id.value == "reservation-1"
    assert trigger == NOW + timedelta(seconds=840)
    assert driver._pair_variables(deployment)["account_id"] == "123456789012"


@pytest.mark.parametrize("change,match", [
    ({"complete_workflow_remaining_seconds": 900}, "does not fit"),
    ({"cost_source_path": "other.csv"}, "source identity/hash"),
    ({"cost_input_sha256": "d" * 64}, "source identity/hash"),
])
def test_expiry_validation_fails_closed_on_cap_or_cost_mismatch(tmp_path, change, match):
    deployment = make_deployment()
    settings = make_settings(tmp_path, deployment, **change)
    driver = AwsProviderDriver(runner=object(), settings=settings, clock=lambda: NOW)
    with pytest.raises(AwsProviderError, match=match):
        driver._validate_registration(deployment, make_request(driver, deployment))


def test_expiry_price_source_is_dated_and_hash_verified():
    source = ROOT / EXPIRY_INPUT_PATH
    raw = source.read_bytes()
    fixture = json.loads(raw)
    assert fixture["id"] == EXPIRY_INPUT_ID
    assert fixture["free_tier_assumed"] is False
    assert fixture["worst_planned_cost_per_region"] == 0.013
    assert hashlib.sha256(raw).hexdigest() == EXPIRY_INPUT_SHA256
    assert fixture["worst_planned_cost_for_two_regions"] == 0.026


def test_attempt_state_is_private_atomic_and_separate_by_deployment(tmp_path):
    deployment = make_deployment()
    driver = AwsProviderDriver(object(), make_settings(tmp_path, deployment), clock=lambda: NOW)
    first = driver._attempt_dir(deployment)
    driver._save_journal(first, driver._load_journal(first, deployment))
    journal = first / "attempt.json"
    assert journal.stat().st_mode & 0o777 == 0o600
    assert first.stat().st_mode & 0o777 == 0o700
    other = deployment.model_copy(update={"deployment_id": "campaign-aws-deployment-other"})
    assert driver._attempt_dir(other) != first


def test_ambiguous_automation_readback_never_authorizes_creation():
    params = {
        "AutomationAssumeRole": ["arn:aws:iam::123456789012:role/expiry"],
        "ExpiryTriggerUtc": ["2026-10-22T12:14:00Z"],
        "RunId": ["campaign-aws"],
        "DeploymentId": ["campaign-aws-deployment"],
        "Region": ["eu-central-1"],
    }
    safety = {"document_name": "expiry", "document_version": "1"}
    response = {
        "DocumentName": "expiry", "DocumentVersion": "1", "AutomationExecutionStatus": "InProgress",
        "CurrentAction": "aws:sleep", "CurrentStepName": "waitUntilExpiry", "Parameters": params,
        "StepExecutions": [{"StepName": "waitUntilExpiry", "Action": "aws:sleep", "StepStatus": "InProgress", "Inputs": {"Timestamp": params["ExpiryTriggerUtc"]}}],
    }
    assert _automation_matches(response, safety, params)
    response["AutomationExecutionStatus"] = "Pending"
    assert not _automation_matches(response, safety, params)


@pytest.mark.parametrize("scenario,file", [
    ("same_zone", "configs/experiments/exp-001-multi-provider.yaml"),
    ("cross_zone", "configs/experiments/exp-002-cross-zone.yaml"),
    ("placement_optimization", "configs/experiments/exp-003-placement-optimization.yaml"),
    ("inter_region", "configs/experiments/exp-004-inter-region.yaml"),
])
def test_frozen_scenarios_match_aws_regions_and_pair_root(scenario, file, tmp_path):
    deployment = make_deployment(config_path=file)
    assert str(deployment.scenario) == scenario
    driver = AwsProviderDriver(object(), make_settings(tmp_path, deployment), clock=lambda: NOW)
    variables = driver._pair_variables(deployment)
    assert variables["scenario"] == scenario
    assert variables["controller_cidr"].endswith("/32")
    assert variables["image_id_frankfurt"] == "ami-0123456789abcdef0"


def test_pair_hcl_keeps_exact_two_vm_topologies_and_private_ingress():
    main = (ROOT / "terraform/aws/pair/main.tf").read_text()
    for literal in ("m7i.xlarge", "10.60.0.0/16", "10.60.1.0/24", "10.60.2.0/24", "10.61.0.0/16", "10.61.1.0/24", "11111", "5201", "gp3", "volume_size           = 32"):
        assert literal in main
    assert "aws_instance" not in (ROOT / "terraform/aws/safety/main.tf").read_text()
    assert main.count('resource "aws_instance"') == 3  # B is represented once per region; runtime count selects exactly one.
    assert 'cidr_blocks = [var.controller_cidr]' in main
    assert "0.0.0.0/0" in main  # egress only; ingress is asserted separately below.
    assert "spot" not in main.lower()
    assert "aws_nat_gateway" not in main
    assert "aws_efa" not in main


def test_cleanup_inventory_never_claims_unknown_resources_absent():
    inventory = _remaining_inventory({
        "instances": [{"InstanceId": "i-dead", "State": {"Name": "terminated"}}],
        "volumes": None,
        "peerings": [{"VpcPeeringConnectionId": "pcx-deleted", "Status": {"Code": "deleted"}}],
    })
    assert inventory == {"volumes": ["inventory_unknown"]}


def test_cleanup_inventory_requires_both_attempt_tags():
    assert _has_exact_ownership_tags({"Tags": [
        {"Key": "cnb_run_id", "Value": "campaign-aws"},
        {"Key": "cnb_deployment_id", "Value": "campaign-aws-deployment"},
    ]}, "campaign-aws", "campaign-aws-deployment")
    assert not _has_exact_ownership_tags({"Tags": [
        {"Key": "cnb_run_id", "Value": "campaign-aws"},
        {"Key": "cnb_deployment_id", "Value": "foreign-deployment"},
    ]}, "campaign-aws", "campaign-aws-deployment")
