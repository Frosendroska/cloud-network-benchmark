from __future__ import annotations

from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.contracts.admission import ExpiryRequest, ReservationEvidence
from cloud_network_benchmark.contracts.common import Evidence, FailureEvidence, StringEvidence
from cloud_network_benchmark.contracts.common import StructuredEvidence
from cloud_network_benchmark.contracts.deployment import ProviderDeploymentInput
from cloud_network_benchmark.contracts.execution import CommandResult
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


def test_unresolved_prior_aws_attempt_blocks_expiry_and_pair_setup(tmp_path):
    deployment = make_deployment()
    settings = make_settings(tmp_path, deployment)
    residual = settings.private_state_root / "cnb-campaign-aws-previous-deployment" / deployment.run_id / "campaign-aws-previous-deployment"
    residual.mkdir(parents=True)
    (residual / "attempt.json").write_text(json.dumps({
        "schema_version": 1,
        "run_id": deployment.run_id,
        "deployment_id": "campaign-aws-previous-deployment",
        "creation_may_have_started": True,
        "cleanup_state": "failed",
    }))
    driver = AwsProviderDriver(object(), settings, clock=lambda: NOW)
    with pytest.raises(AwsProviderError, match="unresolved cleanup"):
        driver._validate_registration(deployment, make_request(driver, deployment))


def test_future_cap_start_and_expired_trigger_are_rejected(tmp_path):
    deployment = make_deployment()
    future = deployment.reservation.value.model_copy(update={"cap_started_at": Evidence[datetime](value=NOW + timedelta(seconds=1))})
    future_deployment = deployment.model_copy(update={"reservation": Evidence[ReservationEvidence](value=future)})
    driver = AwsProviderDriver(object(), make_settings(tmp_path, deployment), clock=lambda: NOW)
    with pytest.raises(AwsProviderError, match="cap start has not occurred"):
        driver._validate_registration(future_deployment, make_request(driver, future_deployment))

    expired = deployment.reservation.value.model_copy(update={"expires_at": Evidence[datetime](value=NOW + timedelta(seconds=300))})
    expired_deployment = deployment.model_copy(update={"reservation": Evidence[ReservationEvidence](value=expired)})
    with pytest.raises(AwsProviderError, match="trigger has passed"):
        driver._validate_registration(expired_deployment, make_request(driver, expired_deployment))


def test_scenario_review_evidence_is_required_and_time_bounded(tmp_path):
    deployment = make_deployment(config_path="configs/experiments/exp-004-inter-region.yaml")
    missing = AwsProviderDriver(object(), make_settings(tmp_path, deployment, primary_review_decision=None), clock=lambda: NOW)
    with pytest.raises(AwsProviderError, match="sourced primary-review"):
        missing._validate_registration(deployment, make_request(missing, deployment))

    stale = make_settings(tmp_path, deployment, primary_review_decision=StructuredEvidence(value={
        "decision": "approved", "scenario": "inter_region", "source_path": "review.md",
        "source_sha256": "e" * 64, "reviewed_at": (NOW + timedelta(seconds=1)).isoformat(),
    }))
    rejected = AwsProviderDriver(object(), stale, clock=lambda: NOW)
    with pytest.raises(AwsProviderError, match="sourced primary-review"):
        rejected._validate_registration(deployment, make_request(rejected, deployment))


def test_missing_reservation_or_wrong_cap_policy_is_rejected(tmp_path):
    deployment = make_deployment()
    driver = AwsProviderDriver(object(), make_settings(tmp_path, deployment), clock=lambda: NOW)
    request = make_request(driver, deployment)
    missing = deployment.model_copy(update={"reservation": Evidence[ReservationEvidence](unavailable_reason="not reserved")})
    with pytest.raises(AwsProviderError, match="complete reservation"):
        driver._validate_registration(missing, request)

    wrong = deployment.reservation.value.model_copy(update={"cap_start_policy_id": StringEvidence(value="unknown")})
    invalid = deployment.model_copy(update={"reservation": Evidence[ReservationEvidence](value=wrong)})
    with pytest.raises(AwsProviderError, match="cap-start policy"):
        driver._validate_registration(invalid, make_request(driver, invalid))


@pytest.mark.parametrize("fingerprint,expected_error", [
    ("6vY5WQn8o6vthzV3rnmaHhNHw0pYp9aF9Vw5c9y1QwA", None),
    ("differentFingerprint123456789", "does not match authenticated"),
])
def test_console_host_fingerprint_is_bound_to_current_instance_and_scan(tmp_path, fingerprint, expected_error):
    class Runner:
        def __init__(self):
            self.commands = []

        def run(self, request):
            self.commands.append(request.argv)
            if request.argv[0] == "ssh-keyscan":
                stdout = "8.8.8.8 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeHostKey\n"
            elif request.argv[0] == "ssh-keygen":
                stdout = f"256 SHA256:{fingerprint} host (ED25519)\n"
            else:
                console = f"CNB_HOSTKEY_BOOT_ID={fixture['boot_id']}\nCNB_ED25519_HOST_FINGERPRINT={fixture['console_fingerprint']}\n"
                stdout = json.dumps({"InstanceId": fixture["instance_id"], "Output": base64.b64encode(console.encode()).decode()})
            return CommandResult(action_id=request.action_id, outcome="succeeded", started_at=NOW,
                finished_at=NOW, duration_seconds=0, exit_code=0, stdout=stdout, stderr="")

    fixture = json.loads((ROOT / "tests/fixtures/aws/console/verified-host.json").read_text())
    assert fixture["public_key_fingerprint"] != fixture["console_fingerprint"]
    runner = Runner()
    deployment = make_deployment()
    driver = AwsProviderDriver(runner, make_settings(tmp_path, deployment), clock=lambda: NOW)
    driver._active_attempt = driver._attempt_dir(deployment)
    detail = {"region": fixture["region"], "instance_id": fixture["instance_id"], "public_ipv4": "8.8.8.8"}
    if expected_error:
        with pytest.raises(AwsProviderError, match=expected_error):
            driver._verify_host_key(detail)
        assert not list(driver._active_attempt.glob("*.known_hosts"))
    else:
        observed = driver._verify_host_key(detail)
        assert observed["boot_id"] == fixture["boot_id"]
        assert Path(observed["known_hosts_path"]).is_file()
    assert all(KEY not in " ".join(argv) for argv in runner.commands)


@pytest.mark.parametrize("mutation,match", [
    (None, None),
    ("instance", "instance identity"),
    ("account", "account differs"),
    ("zone", "physical AWS zone ID"),
    ("tags", "ownership tags differ"),
    ("image_owner", "Canonical Ubuntu"),
    ("disk", "32 GiB gp3"),
])
def test_provider_observation_authenticates_account_topology_image_and_disk(tmp_path, mutation, match):
    deployment = make_deployment()
    instance = {
        "InstanceId": "i-0123456789abcdef0", "State": {"Name": "running"},
        "InstanceType": "m7i.xlarge", "ImageId": "ami-0123456789abcdef0",
        "PrivateIpAddress": "10.60.1.10", "PublicIpAddress": "8.8.8.8",
        "Placement": {"AvailabilityZone": "eu-central-1a"},
        "Tags": [{"Key": "cnb_run_id", "Value": deployment.run_id},
                 {"Key": "cnb_deployment_id", "Value": deployment.deployment_id},
                 {"Key": "cnb_role", "Value": "vm_a"}],
        "NetworkInterfaces": [{"InterfaceType": "interface"}],
        "BlockDeviceMappings": [{"Ebs": {"VolumeId": "vol-0123456789abcdef0", "DeleteOnTermination": True}}],
    }
    responses = {
        "get-caller-identity": {"Account": "123456789012"},
        "describe-instances": {"Reservations": [{"Instances": [instance]}]},
        "describe-instance-topology": {"Instances": [{"ZoneId": "euc1-az2"}]},
        "describe-images": {"Images": [{"ImageId": instance["ImageId"], "OwnerId": "099720109477",
            "Architecture": "x86_64", "VirtualizationType": "hvm", "RootDeviceType": "ebs",
            "State": "available", "Name": "ubuntu-noble-24.04-amd64-server-20261001", "EnaSupport": True}]},
        "describe-volumes": {"Volumes": [{"VolumeId": "vol-0123456789abcdef0", "Size": 32, "VolumeType": "gp3"}]},
        "describe-instance-types": {"InstanceTypes": [{"InstanceType": "m7i.xlarge",
            "ProcessorInfo": {"SupportedArchitectures": ["x86_64"]}, "VCpuInfo": {"DefaultVCpus": 4},
            "MemoryInfo": {"SizeInMiB": 16384}}]},
    }
    if mutation == "account":
        responses["get-caller-identity"]["Account"] = "000000000000"
    elif mutation == "instance":
        instance["InstanceId"] = "i-foreign"
    elif mutation == "zone":
        responses["describe-instance-topology"]["Instances"][0]["ZoneId"] = "wrong-zone"
    elif mutation == "tags":
        instance["Tags"][1]["Value"] = "foreign-deployment"
    elif mutation == "image_owner":
        responses["describe-images"]["Images"][0]["OwnerId"] = "foreign-owner"
    elif mutation == "disk":
        responses["describe-volumes"]["Volumes"][0]["Size"] = 64

    class Runner:
        def run(self, request):
            operation = request.argv[2]
            return CommandResult(action_id=request.action_id, outcome="succeeded", started_at=NOW,
                finished_at=NOW, duration_seconds=0, exit_code=0,
                stdout=json.dumps(responses.get(operation, {})), stderr="")

    driver = AwsProviderDriver(Runner(), make_settings(tmp_path, deployment), clock=lambda: NOW)
    expected = {"region": "eu-central-1", "instance_id": "i-0123456789abcdef0",
        "private_ipv4": "10.60.1.10", "public_ipv4": "8.8.8.8", "zone": "eu-central-1a",
        "zone_id": "euc1-az2", "image_id": instance["ImageId"], "boot_volume_id": "vol-0123456789abcdef0",
        "placement_group": None}
    if match:
        with pytest.raises(AwsProviderError, match=match):
            driver._verify_observed_instance(expected, "same_zone", "vm_a", deployment.run_id, deployment.deployment_id)
    else:
        actual = driver._verify_observed_instance(expected, "same_zone", "vm_a", deployment.run_id, deployment.deployment_id)
        assert actual["zone_id"] == "euc1-az2"
        assert actual["boot_volume_type"] == "gp3"


def test_terraform_output_from_wrong_region_is_rejected_before_host_trust(tmp_path):
    deployment = make_deployment()
    driver = AwsProviderDriver(object(), make_settings(tmp_path, deployment), clock=lambda: NOW)
    def detail(instance_id, private_ip, public_ip, region, zone):
        return {"instance_id": instance_id, "private_ipv4": private_ip, "public_ipv4": public_ip,
            "region": region, "zone": zone, "zone_id": "test-zone", "image_id": "ami-test",
            "image_owner_id": "099720109477", "architecture": "x86_64", "shape": "m7i.xlarge",
            "boot_volume_id": "vol-test"}
    with pytest.raises(AwsProviderError, match="region/zone"):
        driver._normalize_output(deployment, {"vms": {
            "vm_a": detail("i-vm-a", "10.60.1.10", "8.8.8.8", "eu-west-2", "eu-west-2a"),
            "vm_b": detail("i-vm-b", "10.60.1.11", "1.1.1.1", "eu-central-1", "eu-central-1a"),
        }}, NOW, NOW)


@pytest.mark.parametrize("response,match", [
    ({"InstanceId": "i-foreign", "Output": ""}, "not bound to the expected instance"),
    ({"InstanceId": "i-0123456789abcdef0", "Output": base64.b64encode(b"CNB_ED25519_HOST_FINGERPRINT=SHA256:abc123\n").decode()}, "lacks one current boot"),
])
def test_console_rejects_wrong_instance_or_missing_current_boot(response, match, tmp_path):
    deployment = make_deployment()
    driver = AwsProviderDriver(object(), make_settings(tmp_path, deployment), clock=lambda: NOW)
    driver._aws_json = lambda *_args: response
    with pytest.raises(AwsProviderError, match=match):
        driver._verify_host_key({"region": "eu-central-1", "instance_id": "i-0123456789abcdef0", "public_ipv4": "8.8.8.8"})


def test_cleanup_destroy_is_attempted_without_state_and_primary_failure_is_preserved(tmp_path):
    class Runner:
        def __init__(self):
            self.actions = []

        def run(self, request):
            self.actions.append(request.action_id)
            return CommandResult(action_id=request.action_id, outcome="succeeded", started_at=NOW,
                finished_at=NOW, duration_seconds=0, exit_code=0, stdout="{}", stderr="")

    deployment = make_deployment()
    settings = make_settings(tmp_path, deployment)
    runner = Runner()
    driver = AwsProviderDriver(runner, settings, clock=lambda: NOW)
    attempt = driver._attempt_dir(deployment)
    (attempt / "pair.tfvars.json").write_text("{}")
    primary = FailureEvidence(category="terraform_apply", message="apply timed out", occurred_at=NOW, action_id="aws-pair-apply")
    driver._save_journal(attempt, {
        "schema_version": 1, "run_id": deployment.run_id, "deployment_id": deployment.deployment_id,
        "creation_may_have_started": True, "pair_state_path": str(attempt / "pair.tfstate"),
        "primary_failure": primary.model_dump(mode="json"), "regions": ["eu-central-1"], "executions": {},
    })
    driver._inventory_region = lambda _deployment, _region, failures: failures.append(primary) or {"complete": False, "remaining": {"volumes": ["inventory_unknown"]}}
    result = driver.cleanup(deployment)
    assert runner.actions == ["aws-pair-destroy"]
    assert result.cleanup_state == "failed"
    assert result.primary_failure.category == "terraform_apply"
    assert result.cleanup_failure.category == "inventory_incomplete"


def test_safety_support_absence_requires_not_found_from_both_apis(tmp_path):
    class Runner:
        def __init__(self, role_error="NoSuchEntity"):
            self.role_error = role_error

        def run(self, request):
            operation = request.argv[2]
            code = "InvalidDocument" if operation == "describe-document" else self.role_error
            return CommandResult(action_id=request.action_id, outcome="failed", started_at=NOW,
                finished_at=NOW, duration_seconds=0, exit_code=254, stdout="",
                stderr=f"An error occurred ({code}) when calling the API: absent")

    deployment = make_deployment()
    settings = make_settings(tmp_path, deployment)
    safety = {"document_name": "expiry", "role_arn": "arn:aws:iam::123456789012:role/expiry"}
    assert AwsProviderDriver(Runner(), settings, clock=lambda: NOW)._verify_safety_absent("eu-central-1", safety)
    assert not AwsProviderDriver(Runner("AccessDenied"), settings, clock=lambda: NOW)._verify_safety_absent("eu-central-1", safety)


@pytest.mark.parametrize("failure_operation,token_operation", [
    ("describe-volumes", None),
    (None, "describe-instances"),
])
def test_cleanup_inventory_blocks_on_denied_or_truncated_aws_cli_results(tmp_path, failure_operation, token_operation):
    class Runner:
        def run(self, request):
            operation = request.argv[2]
            if operation == failure_operation:
                return CommandResult(action_id=request.action_id, outcome="failed", started_at=NOW,
                    finished_at=NOW, duration_seconds=0, exit_code=1, stdout="", stderr="AccessDenied")
            stdout = json.dumps({"Reservations": [], "NextToken": "next"}) if operation == token_operation else "{}"
            return CommandResult(action_id=request.action_id, outcome="succeeded", started_at=NOW,
                finished_at=NOW, duration_seconds=0, exit_code=0, stdout=stdout, stderr="")

    deployment = make_deployment()
    driver = AwsProviderDriver(Runner(), make_settings(tmp_path, deployment), clock=lambda: NOW)
    failures = []
    inventory = driver._inventory_region(deployment, "eu-central-1", failures)
    assert inventory["complete"] is False
    assert failures
    if failure_operation:
        assert inventory["remaining"]["volumes"] == ["inventory_unknown"]
    else:
        assert failures[0].category == "inventory_truncated"


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


def test_p0_p1_runs_have_distinct_state_tag_and_public_key_namespaces(tmp_path):
    deployment = make_deployment()
    p0 = deployment
    p1 = deployment.model_copy(update={
        "campaign_id": "campaign-p1", "run_id": "campaign-p1-aws",
        "deployment_id": "campaign-p1-aws-deployment", "observation_id": "campaign-p1-aws-observation",
    })
    settings_p0 = make_settings(tmp_path / "p0", p0)
    key_p1 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC p1-key"
    settings_p1 = make_settings(tmp_path / "p1", p1, public_key=key_p1)
    driver_p0 = AwsProviderDriver(object(), settings_p0, clock=lambda: NOW)
    driver_p1 = AwsProviderDriver(object(), settings_p1, clock=lambda: NOW)
    path_p0, path_p1 = driver_p0._attempt_dir(p0), driver_p1._attempt_dir(p1)
    assert path_p0 != path_p1
    assert driver_p0._pair_variables(p0)["run_id"] != driver_p1._pair_variables(p1)["run_id"]
    assert settings_p0.public_key in driver_p0._pair_variables(p0)["host_key_bootstrap"]
    assert settings_p1.public_key in driver_p1._pair_variables(p1)["host_key_bootstrap"]
    assert settings_p1.public_key not in driver_p0._pair_variables(p0)["host_key_bootstrap"]


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


@pytest.mark.parametrize("change", [
    {"DocumentVersion": "2"},
    {"AutomationExecutionStatus": "Failed"},
    {"Parameters": {"RunId": ["foreign-run"]}},
    {"CurrentStepName": "discoverAndStopOwnedInstances"},
])
def test_automation_readback_rejects_truncated_or_mismatched_execution(change):
    params = {
        "AutomationAssumeRole": ["arn:aws:iam::123456789012:role/expiry"],
        "ExpiryTriggerUtc": ["2026-10-22T12:14:00Z"],
        "RunId": ["campaign-aws"], "DeploymentId": ["campaign-aws-deployment"], "Region": ["eu-central-1"],
    }
    execution = {
        "DocumentName": "expiry", "DocumentVersion": "1", "AutomationExecutionStatus": "InProgress",
        "CurrentAction": "aws:sleep", "CurrentStepName": "waitUntilExpiry", "Parameters": params,
        "StepExecutions": [{"StepName": "waitUntilExpiry", "Action": "aws:sleep", "StepStatus": "InProgress", "Inputs": {"Timestamp": params["ExpiryTriggerUtc"]}}],
    }
    safety = {"document_name": "expiry", "document_version": "1"}
    assert _automation_matches(execution, safety, params)
    execution.update(change)
    assert not _automation_matches(execution, safety, params)


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
    variables = (ROOT / "terraform/aws/pair/variables.tf").read_text()
    outputs = (ROOT / "terraform/aws/pair/outputs.tf").read_text()
    for literal in (
        "m7i.xlarge", "10.60.0.0/16", "10.60.1.0/24", "10.60.2.0/24", "10.61.0.0/16", "10.61.1.0/24",
        "eu-central-1a", "eu-central-1b", "eu-west-2a", "11111", "5201", "gp3",
        "volume_size           = 32", "associate_public_ip_address = true", "http_tokens   = \"required\"",
        "delete_on_termination = true", "strategy = \"cluster\"", "peer_region = \"eu-west-2\"",
    ):
        assert literal in main
    assert "cidrhost(var.controller_cidr, 0)" in variables and 'endswith(var.controller_cidr, "/32")' in variables
    assert '"10.60.1.10"' in (ROOT / "src/cloud_network_benchmark/providers/aws.py").read_text()
    assert '"10.60.1.11"' in outputs or '10.60.1.11' in (ROOT / "src/cloud_network_benchmark/providers/aws.py").read_text()
    assert "10.60.2.10" in (ROOT / "src/cloud_network_benchmark/providers/aws.py").read_text()
    assert "10.61.1.10" in (ROOT / "src/cloud_network_benchmark/providers/aws.py").read_text()
    assert 'cidr_blocks = [var.controller_cidr]' in main
    assert 'cidr_blocks = ["10.60.1.10/32"]' in main
    assert 'cidr_blocks = ["0.0.0.0/0"]' in main  # egress only; ingress is asserted separately.
    assert "EnaSrd" not in main and "efa" not in main.lower()
    assert "aws_nat_gateway" not in main and "aws_lb" not in main and "aws_instance" not in (ROOT / "terraform/aws/safety/main.tf").read_text()
    assert "aws_instance" not in (ROOT / "terraform/aws/safety/main.tf").read_text()
    assert main.count('resource "aws_instance"') == 3  # B is represented once per region; runtime count selects exactly one.
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
