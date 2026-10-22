from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path
import pytest

from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.contracts.admission import ExpiryEvidence, ExpiryRequest, ReservationEvidence
from cloud_network_benchmark.contracts.common import CommandOutcome, Evidence, StringEvidence, StructuredEvidence
from cloud_network_benchmark.contracts.deployment import ProviderDeploymentInput
from cloud_network_benchmark.contracts.execution import CommandResult
from cloud_network_benchmark.providers.aws import AwsProviderDriver, AwsProviderError, AwsRuntimeSettings


NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).parents[2]


class QueueRunner:
    def __init__(self, replies):
        self.replies = list(replies)
        self.actions = []

    def run(self, request):
        self.actions.append(request.action_id)
        exit_code, stdout, stderr = self.replies.pop(0)
        return CommandResult(
            action_id=request.action_id,
            outcome="succeeded" if exit_code == 0 else "failed",
            started_at=NOW,
            finished_at=NOW,
            duration_seconds=0,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )


class RegistrationRunner:
    def __init__(self, *, fail_london_start=False):
        self.actions = []
        self.fail_london_start = fail_london_start

    def run(self, request):
        action = request.action_id
        self.actions.append(action)
        code, stdout = 0, "{}"
        if action.startswith("aws-safety-apply-"):
            state_arg = next((item for item in request.argv if item.startswith("-state=")), None)
            if state_arg:
                Path(state_arg.removeprefix("-state=")).write_text("fake-terraform-state")
        if action.startswith("aws-safety-output-"):
            region = action.removeprefix("aws-safety-output-")
            suffix = "fr" if region == "eu-central-1" else "uk"
            stdout = json.dumps({name: {"value": value} for name, value in {
                "document_name": f"expiry-{suffix}", "document_version": "1",
                "document_hash": "a" * 64,
                "role_arn": f"arn:aws:iam::123456789012:role/expiry-{suffix}",
            }.items()})
        elif action.startswith("aws-expiry-start-"):
            region = action.removeprefix("aws-expiry-start-")
            if self.fail_london_start and region == "eu-west-2":
                code, stdout = 1, ""
            else:
                stdout = json.dumps({"AutomationExecutionId": f"exec-{region}"})
        elif action.startswith("aws-expiry-confirm-") and "-cancel-" not in action:
            region = action.removeprefix("aws-expiry-confirm-")
            role_suffix = "fr" if region == "eu-central-1" else "uk"
            parameters = {
                "AutomationAssumeRole": [f"arn:aws:iam::123456789012:role/expiry-{role_suffix}"],
                "ExpiryTriggerUtc": ["2026-10-22T12:14:00Z"],
                "RunId": ["campaign-aws"], "DeploymentId": ["campaign-aws-deployment"], "Region": [region],
            }
            document_name = f"expiry-{role_suffix}"
            stdout = json.dumps({"AutomationExecution": {
                "AutomationExecutionId": f"exec-{region}",
                "DocumentName": document_name, "DocumentVersion": "1",
                "AutomationExecutionStatus": "InProgress", "CurrentAction": "aws:sleep",
                "CurrentStepName": "waitUntilExpiry", "Parameters": parameters,
                "StepExecutionsTruncated": False,
                "StepExecutions": [{"StepName": "waitUntilExpiry", "Action": "aws:sleep",
                    "StepStatus": "InProgress", "Inputs": {"Timestamp": parameters["ExpiryTriggerUtc"][0]}}],
            }})
        elif action.startswith(("aws-expiry-read-terminal-", "aws-expiry-confirm-cancel-")):
            region = action.removeprefix("aws-expiry-read-terminal-").removeprefix("aws-expiry-confirm-cancel-")
            role_suffix = "fr" if region == "eu-central-1" else "uk"
            parameters = {"AutomationAssumeRole": [f"arn:aws:iam::123456789012:role/expiry-{role_suffix}"],
                "ExpiryTriggerUtc": ["2026-10-22T12:14:00Z"], "RunId": ["campaign-aws"],
                "DeploymentId": ["campaign-aws-deployment"], "Region": [region]}
            stdout = json.dumps({"AutomationExecution": {"AutomationExecutionId": f"exec-{region}",
                "DocumentName": f"expiry-{role_suffix}", "DocumentVersion": "1", "Parameters": parameters,
                "StepExecutionsTruncated": False, "StepExecutions": [{"StepName": "waitUntilExpiry",
                    "Action": "aws:sleep", "StepStatus": "Cancelled",
                    "Inputs": {"Timestamp": parameters["ExpiryTriggerUtc"][0]}}],
                "AutomationExecutionStatus": "Cancelled"}})
        return CommandResult(
            action_id=action, outcome="succeeded" if code == 0 else "failed",
            started_at=NOW, finished_at=NOW, duration_seconds=0,
            exit_code=code, stdout=stdout, stderr="registration failed" if code else "",
        )


def make_inter_region_attempt(tmp_path, scenario="inter_region"):
    config_paths = {
        "same_zone": "exp-001-multi-provider.yaml",
        "cross_zone": "exp-002-cross-zone.yaml",
        "placement_optimization": "exp-003-placement-optimization.yaml",
        "inter_region": "exp-004-inter-region.yaml",
    }
    config, _ = resolve_campaign(ROOT / "configs/experiments" / config_paths[scenario], ROOT)
    observation = next(item for item in config.observations if item.provider == "aws")
    source_hash = "a" * 64
    observation = observation.model_copy(update={"admission": observation.admission.model_copy(update={"cost_input_sha256": StringEvidence(value=source_hash)})})
    reservation = ReservationEvidence(
        schema_version=2, campaign_id="campaign", reservation_id=Evidence[str](value="reservation-1"),
        state="reserved", reserved_cost_usd=Evidence[float](value=5.0),
        cap_started_at=Evidence[datetime](value=NOW), expires_at=Evidence[datetime](value=NOW + timedelta(seconds=1200)),
        cap_start_policy_id=StringEvidence(value="reservation-activation-before-provider-expiry"),
        source_path="tracking/run-register.csv", source_sha256=source_hash,
    )
    deployment = ProviderDeploymentInput.from_observation(
        observation, campaign_id="campaign", run_id="campaign-aws", bootstrap_template="cloud-init.yaml",
        terraform_directory="terraform/aws", child_result_path="results/raw/campaign-aws",
        config_sha256=config.config_source.sha256, implementation_git_commit=StringEvidence(value="b" * 40),
        design_git_commit=StringEvidence(value="c" * 40), reservation=Evidence[ReservationEvidence](value=reservation),
    )
    settings_values = dict(
        account_id="123456789012", controller_cidr="203.0.113.4/32",
        ami_ids={"eu-central-1": "ami-0123456789abcdef0", "eu-west-2": "ami-0abcdef0123456789"},
        public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA fake-key",
        authentication_reference="private-ssh-key:attempt-only", private_state_root=tmp_path / "state",
        aws_cli="aws", terraform="terraform", cost_input_id=observation.admission.cost_input_id,
        cost_input_sha256=source_hash, cost_source_path=reservation.source_path,
        complete_workflow_remaining_seconds=120,
    )
    if observation.admission.requires_primary_review:
        settings_values["primary_review_decision"] = StructuredEvidence(value={"decision": "approved", "scenario": scenario,
            "source_path": "tracking/paid-admission-review.md", "source_sha256": "e" * 64, "reviewed_at": NOW.isoformat()})
    settings = AwsRuntimeSettings(**settings_values)
    return deployment, settings


def terminal_automation(execution_id, document_name, parameters, status="Cancelled", outputs=None):
    return {"AutomationExecution": {
        "AutomationExecutionId": execution_id, "DocumentName": document_name, "DocumentVersion": "1",
        "Parameters": parameters, "StepExecutionsTruncated": False,
        "StepExecutions": [{"StepName": "waitUntilExpiry", "Action": "aws:sleep", "StepStatus": status,
            "Inputs": {"Timestamp": parameters["ExpiryTriggerUtc"][0]}}],
        "AutomationExecutionStatus": status, "Outputs": outputs,
    }}


def test_expiry_start_uses_one_request_and_returns_execution_identity(tmp_path):
    runner = QueueRunner([(0, '{"AutomationExecutionId":"exec-1"}', "")])
    driver = AwsProviderDriver(runner, SimpleNamespace(aws_cli="aws", registration_timeout_seconds=90, authentication_reference="fake-ref"), clock=lambda: NOW)
    result = driver._start_or_recover_automation(
        "eu-central-1",
        {"document_name": "cnb-expiry", "document_version": "1"},
        {"RunId": ["campaign-aws"], "DeploymentId": ["campaign-aws-deployment"]},
        "stable-token",
    )
    assert result == "exec-1"
    assert runner.actions == ["aws-expiry-start-eu-central-1"]


def test_lost_expiry_start_recovers_only_one_exact_inprogress_match(tmp_path):
    params = {"RunId": ["campaign-aws"], "DeploymentId": ["campaign-aws-deployment"]}
    execution = {
        "DocumentName": "cnb-expiry",
        "DocumentVersion": "1",
        "AutomationExecutionStatus": "InProgress",
        "AutomationExecutionId": "exec-1",
        "CurrentAction": "aws:sleep",
        "CurrentStepName": "waitUntilExpiry",
        "Parameters": params,
        "StepExecutionsTruncated": False,
        "StepExecutions": [{"StepName": "waitUntilExpiry", "Action": "aws:sleep", "StepStatus": "InProgress", "Inputs": {"Timestamp": "2026-10-22T12:14:00Z"}}],
    }
    described = {"AutomationExecutionMetadataList": [{"AutomationExecutionId": "exec-1"}]}
    runner = QueueRunner([
        (1, "", "response lost"),
        (0, json.dumps(described), ""),
        (0, json.dumps({"AutomationExecution": {**execution, "AutomationExecutionId": "exec-1", "Parameters": {**params, "AutomationAssumeRole": ["arn:aws:iam::123456789012:role/expiry"], "ExpiryTriggerUtc": ["2026-10-22T12:14:00Z"], "Region": ["eu-central-1"]}}}), ""),
    ])
    driver = AwsProviderDriver(runner, SimpleNamespace(aws_cli="aws", registration_timeout_seconds=90, authentication_reference="fake-ref"), clock=lambda: NOW)
    recovered = driver._start_or_recover_automation(
        "eu-central-1",
        {"document_name": "cnb-expiry", "document_version": "1", "role_arn": "arn:aws:iam::123456789012:role/expiry"},
        {**params, "AutomationAssumeRole": ["arn:aws:iam::123456789012:role/expiry"], "ExpiryTriggerUtc": ["2026-10-22T12:14:00Z"], "Region": ["eu-central-1"]},
        "stable-token",
    )
    assert recovered == "exec-1"
    assert runner.actions == [
        "aws-expiry-start-eu-central-1",
        "aws-expiry-recover-eu-central-1",
        "aws-expiry-recover-read-eu-central-1",
    ]


def test_lost_expiry_start_with_duplicate_recovery_matches_fails_closed():
    params = {"RunId": ["campaign-aws"], "DeploymentId": ["campaign-aws-deployment"]}
    runner = QueueRunner([
        (1, "", "lost"),
        (0, json.dumps({"AutomationExecutionMetadataList": [{"AutomationExecutionId": "one"}, {"AutomationExecutionId": "two"}]}), ""),
        (0, "{}", ""),
        (0, "{}", ""),
    ])
    driver = AwsProviderDriver(runner, SimpleNamespace(aws_cli="aws", registration_timeout_seconds=90, authentication_reference="fake-ref"), clock=lambda: NOW)
    assert driver._start_or_recover_automation(
        "eu-central-1", {"document_name": "expiry", "document_version": "1"}, params, "token"
    ) is None


def test_s4_registration_confirms_both_regions_before_reporting_registered(tmp_path):
    deployment, settings = make_inter_region_attempt(tmp_path)
    runner = RegistrationRunner()
    driver = AwsProviderDriver(runner, settings, clock=lambda: NOW)
    reservation = deployment.reservation.value
    regions = driver._regions(deployment)
    request = ExpiryRequest(
        schema_version=2, run_id=deployment.run_id, deployment_id=deployment.deployment_id,
        provider="aws", action="stop",
        expires_at=Evidence[datetime](value=reservation.expires_at.value - timedelta(seconds=360)),
        reservation_id=reservation.reservation_id,
        resource_namespace=f"cnb-{deployment.run_id}-{deployment.deployment_id}",
        target_selector=driver._target_selector(deployment, regions),
    )
    evidence = driver.register_expiry(deployment, request)
    assert evidence.state == "registered"
    assert {action for action in runner.actions if action.startswith("aws-expiry-confirm-")} == {
        "aws-expiry-confirm-eu-central-1", "aws-expiry-confirm-eu-west-2",
    }
    assert max(index for index, action in enumerate(runner.actions) if action.startswith("aws-safety-output-")) < min(
        index for index, action in enumerate(runner.actions) if action.startswith("aws-expiry-start-")
    )
    assert "aws-pair-apply" not in runner.actions


def test_s4_uncertain_second_region_registration_keeps_pair_launch_blocked(tmp_path):
    deployment, settings = make_inter_region_attempt(tmp_path)
    runner = RegistrationRunner(fail_london_start=True)
    driver = AwsProviderDriver(runner, settings, clock=lambda: NOW)
    reservation = deployment.reservation.value
    regions = driver._regions(deployment)
    request = ExpiryRequest(
        schema_version=2, run_id=deployment.run_id, deployment_id=deployment.deployment_id,
        provider="aws", action="stop",
        expires_at=Evidence[datetime](value=reservation.expires_at.value - timedelta(seconds=360)),
        reservation_id=reservation.reservation_id,
        resource_namespace=f"cnb-{deployment.run_id}-{deployment.deployment_id}",
        target_selector=driver._target_selector(deployment, regions),
    )
    failed = driver.register_expiry(deployment, request)
    assert failed.state == "failed"
    failed_deployment = deployment.model_copy(update={"expiry": Evidence[ExpiryEvidence](value=failed)})
    try:
        driver.provision(failed_deployment)
    except Exception as exc:
        assert "expiry evidence" in str(exc)
    else:
        raise AssertionError("failed S4 expiry evidence must block pair creation")
    assert "aws-expiry-confirm-eu-west-2" not in runner.actions
    assert "aws-pair-apply" not in runner.actions
    assert "aws-safety-rollback-eu-central-1" in runner.actions, driver._load_journal(driver._attempt_dir(deployment), deployment).get("unused_protection_cleanup_errors")
    assert "aws-safety-rollback-eu-west-2" not in runner.actions


@pytest.mark.parametrize("apply_outcome", [CommandOutcome.TIMED_OUT, CommandOutcome.FAILED, CommandOutcome.INTERRUPTED])
def test_pair_apply_failure_attempts_destroy_and_preserves_primary_failure(tmp_path, apply_outcome):
    deployment, settings = make_inter_region_attempt(tmp_path)
    # Timeout is represented distinctly from an ordinary non-zero exit.
    class TimeoutRunner:
        def __init__(self):
            self.actions = []

        def run(self, request):
            self.actions.append(request.action_id)
            outcome = apply_outcome if request.action_id == "aws-pair-apply" else CommandOutcome.SUCCEEDED
            if "expiry-read-terminal" in request.action_id or "expiry-confirm-cancel" in request.action_id:
                region = request.action_id.removeprefix("aws-expiry-read-terminal-").removeprefix("aws-expiry-confirm-cancel-")
                parameters = {"AutomationAssumeRole": [f"arn:aws:iam::123456789012:role/expiry-{region}"],
                    "ExpiryTriggerUtc": ["2026-10-22T12:14:00Z"], "RunId": [deployment.run_id],
                    "DeploymentId": [deployment.deployment_id], "Region": [region]}
                stdout = json.dumps(terminal_automation(f"exec-{region}", f"expiry-{region}", parameters))
            else:
                stdout = ""
            if "aws-expiry-document-absence" in request.action_id:
                outcome = CommandOutcome.FAILED
                stderr = "An error occurred (InvalidDocument)"
            elif "aws-expiry-role-absence" in request.action_id:
                outcome = CommandOutcome.FAILED
                stderr = "An error occurred (NoSuchEntity)"
            else:
                stderr = "apply failed" if outcome != CommandOutcome.SUCCEEDED else ""
            return CommandResult(action_id=request.action_id, outcome=outcome, started_at=NOW,
                finished_at=NOW, duration_seconds=0, exit_code=1 if outcome == CommandOutcome.FAILED else None if outcome != CommandOutcome.SUCCEEDED else 0,
                stdout=stdout, stderr=stderr)

    runner = TimeoutRunner()
    driver = AwsProviderDriver(runner, settings, clock=lambda: NOW)
    expiry = ExpiryEvidence(
        schema_version=2, run_id=deployment.run_id, deployment_id=deployment.deployment_id,
        provider="aws", action="stop", state="registered",
        registration_id=Evidence[str](value="aws-ssm:campaign-aws-deployment"),
        registered_at=Evidence[datetime](value=NOW - timedelta(seconds=1)),
        expires_at=Evidence[datetime](value=NOW + timedelta(seconds=840)),
        target_selector=driver._target_selector(deployment, ["eu-central-1", "eu-west-2"]),
        provider_metadata=StructuredEvidence(value={"regional_execution_ids": {"eu-central-1": "exec-eu-central-1", "eu-west-2": "exec-eu-west-2"}}),
    )
    deployment = deployment.model_copy(update={"expiry": Evidence[ExpiryEvidence](value=expiry)})
    attempt = driver._attempt_dir(deployment)
    driver._save_journal(attempt, {
        "schema_version": 1, "run_id": deployment.run_id, "deployment_id": deployment.deployment_id,
        "registered": True, "regions": ["eu-central-1", "eu-west-2"], "registered_regions": ["eu-central-1", "eu-west-2"],
        "executions": {"eu-central-1": "exec-eu-central-1", "eu-west-2": "exec-eu-west-2"}, "safety": {region: {"document_name": f"expiry-{region}", "document_version": "1", "role_arn": f"arn:aws:iam::123456789012:role/expiry-{region}"} for region in ("eu-central-1", "eu-west-2")}, "expiry_evidence": expiry.model_dump(mode="json"),
        "regional_parameters": {region: {"AutomationAssumeRole": [f"arn:aws:iam::123456789012:role/expiry-{region}"], "ExpiryTriggerUtc": ["2026-10-22T12:14:00Z"], "RunId": [deployment.run_id], "DeploymentId": [deployment.deployment_id], "Region": [region]} for region in ("eu-central-1", "eu-west-2")},
        "reservation_id": deployment.reservation.value.reservation_id.value,
        "cap_started_at": deployment.reservation.value.cap_started_at.value.isoformat(), "cap_expires_at": deployment.reservation.value.expires_at.value.isoformat(), "expiry_trigger": expiry.expires_at.value.isoformat(),
    })
    driver._confirm_automation = lambda *_args: {}
    driver._inventory_region = lambda _deployment, _region, _failures: {"complete": True, "remaining": {}, "resources": {}}
    try:
        driver.provision(deployment)
    except Exception as exc:
        assert "apply failed" in str(exc)
    else:
        raise AssertionError("timed-out apply must preserve its primary failure")
    assert runner.actions[:2] == ["aws-pair-apply", "aws-pair-destroy"]
    assert {action for action in runner.actions if action.startswith("aws-expiry-read-terminal-")} == {"aws-expiry-read-terminal-eu-central-1", "aws-expiry-read-terminal-eu-west-2"}
    journal = driver._load_journal(attempt, deployment)
    assert journal["primary_failure"]["category"] == "terraform_apply"
    assert journal["cleanup_state"] == "succeeded"


@pytest.mark.parametrize("output_outcome", [CommandOutcome.FAILED, CommandOutcome.TIMED_OUT, CommandOutcome.INTERRUPTED, CommandOutcome.SUCCEEDED])
def test_terraform_output_failure_attempts_destroy_and_retains_output_failure(tmp_path, output_outcome):
    deployment, settings = make_inter_region_attempt(tmp_path)

    class OutputFailureRunner:
        def __init__(self):
            self.actions = []

        def run(self, request):
            self.actions.append(request.action_id)
            if request.action_id == "aws-pair-apply":
                state = next(item.removeprefix("-state=") for item in request.argv if item.startswith("-state="))
                Path(state).write_text("fake-terraform-state")
            if request.action_id == "aws-pair-output":
                return CommandResult(action_id=request.action_id, outcome=output_outcome, started_at=NOW,
                    finished_at=NOW, duration_seconds=0,
                    exit_code=1 if output_outcome == CommandOutcome.FAILED else None if output_outcome != CommandOutcome.SUCCEEDED else 0,
                    stdout="{}", stderr="output failed")
            if "expiry-read-terminal" in request.action_id or "expiry-confirm-cancel" in request.action_id:
                region = request.action_id.removeprefix("aws-expiry-read-terminal-").removeprefix("aws-expiry-confirm-cancel-")
                parameters = {"AutomationAssumeRole": [f"arn:aws:iam::123456789012:role/expiry-{region}"],
                    "ExpiryTriggerUtc": ["2026-10-22T12:14:00Z"], "RunId": [deployment.run_id],
                    "DeploymentId": [deployment.deployment_id], "Region": [region]}
                stdout = json.dumps(terminal_automation(f"exec-{region}", f"expiry-{region}", parameters))
            else:
                stdout = ""
            if "aws-expiry-document-absence" in request.action_id:
                return CommandResult(action_id=request.action_id, outcome="failed", started_at=NOW, finished_at=NOW, duration_seconds=0, exit_code=254, stdout="", stderr="An error occurred (InvalidDocument)")
            if "aws-expiry-role-absence" in request.action_id:
                return CommandResult(action_id=request.action_id, outcome="failed", started_at=NOW, finished_at=NOW, duration_seconds=0, exit_code=254, stdout="", stderr="An error occurred (NoSuchEntity)")
            return CommandResult(action_id=request.action_id, outcome="succeeded", started_at=NOW,
                finished_at=NOW, duration_seconds=0, exit_code=0, stdout=stdout, stderr="")

    runner = OutputFailureRunner()
    driver = AwsProviderDriver(runner, settings, clock=lambda: NOW)
    expiry = ExpiryEvidence(
        schema_version=2, run_id=deployment.run_id, deployment_id=deployment.deployment_id,
        provider="aws", action="stop", state="registered",
        registration_id=Evidence[str](value="aws-ssm:campaign-aws-deployment"),
        registered_at=Evidence[datetime](value=NOW - timedelta(seconds=1)),
        expires_at=Evidence[datetime](value=NOW + timedelta(seconds=840)),
        target_selector=driver._target_selector(deployment, ["eu-central-1", "eu-west-2"]),
        provider_metadata=StructuredEvidence(value={"regional_execution_ids": {"eu-central-1": "exec-eu-central-1", "eu-west-2": "exec-eu-west-2"}}),
    )
    deployment = deployment.model_copy(update={"expiry": Evidence[ExpiryEvidence](value=expiry)})
    attempt = driver._attempt_dir(deployment)
    driver._save_journal(attempt, {
        "schema_version": 1, "run_id": deployment.run_id, "deployment_id": deployment.deployment_id,
        "registered": True, "regions": ["eu-central-1", "eu-west-2"],
        "registered_regions": ["eu-central-1", "eu-west-2"], "executions": {"eu-central-1": "exec-eu-central-1", "eu-west-2": "exec-eu-west-2"}, "safety": {region: {"document_name": f"expiry-{region}", "document_version": "1", "role_arn": f"arn:aws:iam::123456789012:role/expiry-{region}"} for region in ("eu-central-1", "eu-west-2")},
        "regional_parameters": {region: {"AutomationAssumeRole": [f"arn:aws:iam::123456789012:role/expiry-{region}"], "ExpiryTriggerUtc": ["2026-10-22T12:14:00Z"], "RunId": [deployment.run_id], "DeploymentId": [deployment.deployment_id], "Region": [region]} for region in ("eu-central-1", "eu-west-2")},
        "expiry_evidence": expiry.model_dump(mode="json"),
        "reservation_id": deployment.reservation.value.reservation_id.value,
        "cap_started_at": deployment.reservation.value.cap_started_at.value.isoformat(), "cap_expires_at": deployment.reservation.value.expires_at.value.isoformat(), "expiry_trigger": expiry.expires_at.value.isoformat(),
    })
    driver._confirm_automation = lambda *_args: {}
    driver._inventory_region = lambda _deployment, _region, _failures: {"complete": True, "remaining": {}, "resources": {}}
    try:
        driver.provision(deployment)
    except Exception as exc:
        if output_outcome == CommandOutcome.SUCCEEDED:
            assert "missing deployment.value" in str(exc)
        else:
            assert "AWS pair Terraform output failed" in str(exc)
    else:
        raise AssertionError("missing output must fail the attempt")
    assert runner.actions[:3] == ["aws-pair-apply", "aws-pair-output", "aws-pair-destroy"]
    assert {action for action in runner.actions if action.startswith("aws-expiry-read-terminal-")} == {"aws-expiry-read-terminal-eu-central-1", "aws-expiry-read-terminal-eu-west-2"}
    journal = driver._load_journal(attempt, deployment)
    assert journal["primary_failure"]["category"] == ("output_invalid" if output_outcome == CommandOutcome.SUCCEEDED else "terraform_output")
    assert journal["cleanup_state"] == "succeeded"


@pytest.mark.parametrize("scenario", ["same_zone", "cross_zone", "placement_optimization", "inter_region"])
def test_scenario_apply_output_materializes_exact_roles_and_private_state(tmp_path, scenario):
    deployment, settings = make_inter_region_attempt(tmp_path, scenario)
    sequence = []

    class SuccessRunner:
        def __init__(self):
            self.actions = []

        def run(self, request):
            self.actions.append(request.action_id)
            sequence.append(request.action_id)
            if request.action_id == "aws-pair-apply":
                state = next(item.removeprefix("-state=") for item in request.argv if item.startswith("-state="))
                Path(state).write_text("fake-terraform-state")
                stdout = ""
            elif request.action_id == "aws-pair-output":
                if scenario == "cross_zone":
                    b_region, b_zone, b_zone_id, b_ip = "eu-central-1", "eu-central-1b", "test-euc1-az1", "10.60.2.10"
                elif scenario == "inter_region":
                    b_region, b_zone, b_zone_id, b_ip = "eu-west-2", "eu-west-2a", "test-euw2-az1", "10.61.1.10"
                else:
                    b_region, b_zone, b_zone_id, b_ip = "eu-central-1", "eu-central-1a", "test-euc1-az2", "10.60.1.11"
                placement_group = f"cnb-{deployment.run_id}-{deployment.deployment_id}-cluster" if scenario == "placement_optimization" else None
                roles = {
                    "vm_a": {"instance_id": "i-vm-a", "private_ipv4": "10.60.1.10", "public_ipv4": "8.8.8.8",
                        "region": "eu-central-1", "zone": "eu-central-1a", "zone_id": "test-euc1-az2",
                        "image_id": "ami-0123456789abcdef0", "image_owner_id": "099720109477",
                        "architecture": "x86_64", "shape": "m7i.xlarge", "boot_volume_id": "vol-a",
                        "placement_group": placement_group,
                        "placement_group_strategy": "cluster" if placement_group else None},
                    "vm_b": {"instance_id": "i-vm-b", "private_ipv4": b_ip, "public_ipv4": "1.1.1.1",
                        "region": b_region, "zone": b_zone, "zone_id": b_zone_id,
                        "image_id": settings.ami_ids[b_region], "image_owner_id": "099720109477",
                        "architecture": "x86_64", "shape": "m7i.xlarge", "boot_volume_id": "vol-b",
                        "placement_group": placement_group,
                        "placement_group_strategy": "cluster" if placement_group else None},
                }
                stdout = json.dumps({"deployment": {"value": {"vms": roles, "network": {"peering_id": "pcx-fake"}, "placement": {"strategy": None}}}})
            else:
                stdout = ""
            return CommandResult(action_id=request.action_id, outcome="succeeded", started_at=NOW,
                finished_at=NOW + timedelta(seconds=1), duration_seconds=1, exit_code=0, stdout=stdout, stderr="")

    runner = SuccessRunner()
    driver = AwsProviderDriver(runner, settings, clock=lambda: NOW)
    regions = driver._regions(deployment)
    expiry = ExpiryEvidence(
        schema_version=2, run_id=deployment.run_id, deployment_id=deployment.deployment_id,
        provider="aws", action="stop", state="registered",
        registration_id=Evidence[str](value="aws-ssm:campaign-aws-deployment"),
        registered_at=Evidence[datetime](value=NOW - timedelta(seconds=1)),
        expires_at=Evidence[datetime](value=NOW + timedelta(seconds=840)),
        target_selector=driver._target_selector(deployment, regions),
        provider_metadata=StructuredEvidence(value={"regional_execution_ids": {region: f"exec-{region}" for region in regions}}),
    )
    deployment = deployment.model_copy(update={"expiry": Evidence[ExpiryEvidence](value=expiry)})
    attempt = driver._attempt_dir(deployment)
    driver._save_journal(attempt, {
        "schema_version": 1, "run_id": deployment.run_id, "deployment_id": deployment.deployment_id,
        "registered": True, "regions": regions,
        "registered_regions": regions,
        "executions": {region: f"exec-{region}" for region in regions},
        "safety": {region: {"document_name": f"expiry-{region}", "document_version": "1",
            "role_arn": f"arn:aws:iam::123456789012:role/expiry-{region}"} for region in regions},
        "regional_parameters": {region: {"AutomationAssumeRole": [f"arn:aws:iam::123456789012:role/expiry-{region}"], "ExpiryTriggerUtc": ["2026-10-22T12:14:00Z"], "RunId": [deployment.run_id], "DeploymentId": [deployment.deployment_id], "Region": [region]} for region in regions},
        "expiry_evidence": expiry.model_dump(mode="json"),
        "reservation_id": deployment.reservation.value.reservation_id.value,
        "cap_started_at": deployment.reservation.value.cap_started_at.value.isoformat(), "cap_expires_at": deployment.reservation.value.expires_at.value.isoformat(), "expiry_trigger": expiry.expires_at.value.isoformat(),
    })
    driver._confirm_automation = lambda region, *_args: sequence.append(f"expiry-confirm-{region}")
    driver._verify_observed_instance = lambda expected, *_args: expected
    driver._verify_host_key = lambda detail: {"known_hosts_path": str(attempt / f"{detail['instance_id']}.known_hosts")}
    result = driver.provision(deployment)
    assert runner.actions == ["aws-pair-apply", "aws-pair-output"]
    assert sequence[:len(regions)] == [f"expiry-confirm-{region}" for region in regions]
    assert sequence.index("aws-pair-apply") > len(regions) - 1
    assert str(result.scenario) == scenario
    assert result.vm_a.region == "eu-central-1"
    assert result.vm_b.region == ("eu-west-2" if scenario == "inter_region" else "eu-central-1")
    assert result.vm_a.private_ipv4 == "10.60.1.10"
    assert result.vm_b.private_ipv4 == ("10.60.2.10" if scenario == "cross_zone" else "10.61.1.10" if scenario == "inter_region" else "10.60.1.11")
    assert result.vm_a.physical_zone_id.value == "test-euc1-az2"
    assert result.vm_b.physical_zone_id.value in {"test-euc1-az1", "test-euc1-az2", "test-euw2-az1"}
    assert result.documented_network_limit.value["baseline_gbit_per_second"] == 1.562
    assert (attempt / "pair.tfstate").stat().st_mode & 0o777 == 0o600
    assert (attempt / "pair.tfvars.json").stat().st_mode & 0o777 == 0o600
    tfvars = (attempt / "pair.tfvars.json").read_text()
    assert settings.public_key in tfvars
    assert "PRIVATE KEY" not in tfvars


def test_cleanup_keeps_safety_support_when_execution_cancellation_is_unverified(tmp_path):
    deployment, settings = make_inter_region_attempt(tmp_path)

    class CleanupRunner:
        def __init__(self):
            self.actions = []

        def run(self, request):
            self.actions.append(request.action_id)
            return CommandResult(action_id=request.action_id, outcome="succeeded", started_at=NOW,
                finished_at=NOW, duration_seconds=0, exit_code=0, stdout="", stderr="")

    runner = CleanupRunner()
    driver = AwsProviderDriver(runner, settings, clock=lambda: NOW)
    attempt = driver._attempt_dir(deployment)
    (attempt / "pair.tfvars.json").write_text("{}")
    expiry = ExpiryEvidence(
        schema_version=2, run_id=deployment.run_id, deployment_id=deployment.deployment_id,
        provider="aws", action="stop", state="registered",
        registration_id=Evidence[str](value="aws-ssm:campaign-aws-deployment"),
        registered_at=Evidence[datetime](value=NOW - timedelta(seconds=1)),
        expires_at=Evidence[datetime](value=NOW + timedelta(seconds=840)),
        target_selector="account=123456789012;regions=eu-central-1,eu-west-2;tags=attempt",
        provider_metadata=StructuredEvidence(value={"regional_execution_ids": {"eu-central-1": "exec-fr"}}),
    )
    driver._save_journal(attempt, {
        "schema_version": 1, "run_id": deployment.run_id, "deployment_id": deployment.deployment_id,
        "creation_may_have_started": True, "pair_state_path": str(attempt / "pair.tfstate"),
        "regions": ["eu-central-1", "eu-west-2"], "executions": {"eu-central-1": "exec-fr"},
        "safety": {"eu-central-1": {"document_name": "expiry", "role_arn": "arn:aws:iam::123456789012:role/expiry"}},
        "expiry_evidence": expiry.model_dump(mode="json"),
    })
    driver._inventory_region = lambda _deployment, _region, _failures: {"complete": True, "remaining": {}, "resources": {}}
    driver._cancel_or_confirm_terminal = lambda *_args: (_ for _ in ()).throw(RuntimeError("cancel readback denied"))
    result = driver.cleanup(deployment)
    assert result.cleanup_state == "failed"
    assert result.cleanup_failure.category == "aws_provider"
    assert "aws-pair-destroy" in runner.actions
    assert not any(action.startswith("aws-safety-destroy-") for action in runner.actions)


def test_cleanup_reports_destroy_failure_and_retries_idempotently(tmp_path):
    deployment, settings = make_inter_region_attempt(tmp_path)

    class RetryCleanupRunner:
        def __init__(self):
            self.actions = []
            self.destroy_attempts = 0

        def run(self, request):
            self.actions.append(request.action_id)
            if request.action_id == "aws-pair-destroy":
                self.destroy_attempts += 1
                if self.destroy_attempts == 1:
                    return CommandResult(action_id=request.action_id, outcome="failed", started_at=NOW,
                        finished_at=NOW, duration_seconds=0, exit_code=1, stdout="", stderr="destroy failed")
            return CommandResult(action_id=request.action_id, outcome="succeeded", started_at=NOW,
                finished_at=NOW, duration_seconds=0, exit_code=0, stdout="", stderr="")

    runner = RetryCleanupRunner()
    driver = AwsProviderDriver(runner, settings, clock=lambda: NOW)
    attempt = driver._attempt_dir(deployment)
    (attempt / "pair.tfvars.json").write_text("{}")
    driver._save_journal(attempt, {
        "schema_version": 1, "run_id": deployment.run_id, "deployment_id": deployment.deployment_id,
        "creation_may_have_started": True, "pair_state_path": str(attempt / "pair.tfstate"),
        "regions": ["eu-central-1", "eu-west-2"], "executions": {}, "safety": {},
    })
    driver._inventory_region = lambda _deployment, _region, _failures: {"complete": True, "remaining": {}, "resources": {}}
    failed = driver.cleanup(deployment)
    assert failed.cleanup_state == "failed"
    assert failed.cleanup_failure.category == "terraform_destroy"
    retried = driver.cleanup(deployment)
    assert retried.cleanup_state == "succeeded"
    assert runner.destroy_attempts == 2


@pytest.mark.parametrize("terminal,expiry_state", [("Cancelled", "cancelled"), ("Success", "triggered"), ("Failed", "failed"), ("TimedOut", "failed")])
def test_cleanup_records_terminal_state_and_action_outputs(tmp_path, terminal, expiry_state):
    deployment, settings = make_inter_region_attempt(tmp_path, scenario="same_zone")

    regions = ["eu-central-1"]
    parameters = {"AutomationAssumeRole": ["arn:aws:iam::123456789012:role/expiry"], "ExpiryTriggerUtc": ["2026-10-22T12:10:00Z"],
        "RunId": [deployment.run_id], "DeploymentId": [deployment.deployment_id], "Region": [regions[0]]}
    class TerminalRunner:
        def run(self, request):
            if request.action_id.startswith("aws-expiry-read-terminal-"):
                output = {"AutomationExecution": {"AutomationExecutionId": "exec-one", "DocumentName": "expiry",
                    "DocumentVersion": "1", "Parameters": parameters, "StepExecutionsTruncated": False,
                    "StepExecutions": [{"StepName": "waitUntilExpiry", "Action": "aws:sleep",
                        "StepStatus": terminal, "Inputs": {"Timestamp": parameters["ExpiryTriggerUtc"][0]}}],
                    "AutomationExecutionStatus": terminal, "Outputs": {"stop_requests": ["i-one"]}}}
            else:
                output = {}
            return CommandResult(action_id=request.action_id, outcome="succeeded", started_at=NOW,
                finished_at=NOW, duration_seconds=0, exit_code=0, stdout=json.dumps(output), stderr="")

    driver = AwsProviderDriver(TerminalRunner(), settings, clock=lambda: NOW)
    attempt = driver._attempt_dir(deployment)
    (attempt / "pair.tfvars.json").write_text("{}")
    regions = driver._regions(deployment)
    expiry = ExpiryEvidence(schema_version=2, run_id=deployment.run_id, deployment_id=deployment.deployment_id,
        provider="aws", action="stop", state="registered",
        registration_id=Evidence[str](value=f"aws-ssm:{deployment.deployment_id}"),
        registered_at=Evidence[datetime](value=NOW - timedelta(seconds=1)),
        expires_at=Evidence[datetime](value=NOW + timedelta(seconds=600)),
        target_selector=driver._target_selector(deployment, regions),
        provider_metadata=StructuredEvidence(value={"regional_execution_ids": {regions[0]: "exec-one"}}))
    driver._save_journal(attempt, {"schema_version": 1, "run_id": deployment.run_id,
        "deployment_id": deployment.deployment_id, "creation_may_have_started": True,
        "pair_state_path": str(attempt / "pair.tfstate"), "regions": regions,
        "executions": {regions[0]: "exec-one"}, "safety": {regions[0]: {"document_name": "expiry", "document_version": "1"}},
        "regional_parameters": {regions[0]: parameters},
        "expiry_evidence": expiry.model_dump(mode="json")})
    driver._inventory_region = lambda *_args: {"complete": True, "remaining": {}, "resources": {}}
    driver._verify_safety_absent = lambda *_args: True
    result = driver.cleanup(deployment)
    assert result.cleanup_state == "succeeded"
    assert result.expiry.value.state == expiry_state
    assert result.expiry.value.provider_metadata.value["regional_outcomes"][regions[0]]["state"] == terminal
    assert result.expiry.value.provider_metadata.value["regional_outcomes"][regions[0]]["outputs"] == {"stop_requests": ["i-one"]}


def test_cleanup_preserves_mixed_s4_expiry_outcomes_as_failed_evidence(tmp_path):
    deployment, settings = make_inter_region_attempt(tmp_path)
    driver = AwsProviderDriver(object(), settings, clock=lambda: NOW)
    regions = driver._regions(deployment)
    trigger = "2026-10-22T12:14:00Z"
    statuses = {"eu-central-1": "Success", "eu-west-2": "Failed"}
    parameters = {
        region: {"AutomationAssumeRole": [f"arn:aws:iam::123456789012:role/expiry-{region}"],
            "ExpiryTriggerUtc": [trigger], "RunId": [deployment.run_id],
            "DeploymentId": [deployment.deployment_id], "Region": [region]}
        for region in regions
    }

    class MixedRunner:
        def run(self, request):
            region = next((item for item in regions if request.action_id.endswith(item)), regions[0])
            if request.action_id.startswith("aws-expiry-read-terminal-"):
                return CommandResult(action_id=request.action_id, outcome="succeeded", started_at=NOW,
                    finished_at=NOW, duration_seconds=0, exit_code=0,
                    stdout=json.dumps(terminal_automation(f"exec-{region}", f"expiry-{region}",
                        parameters[region], status=statuses[region])), stderr="")
            return CommandResult(action_id=request.action_id, outcome="succeeded", started_at=NOW,
                finished_at=NOW, duration_seconds=0, exit_code=0, stdout="{}", stderr="")

    driver.runner = MixedRunner()
    attempt = driver._attempt_dir(deployment)
    (attempt / "pair.tfvars.json").write_text("{}")
    expiry = ExpiryEvidence(schema_version=2, run_id=deployment.run_id, deployment_id=deployment.deployment_id,
        provider="aws", action="stop", state="registered",
        registration_id=Evidence[str](value=f"aws-ssm:{deployment.deployment_id}"),
        registered_at=Evidence[datetime](value=NOW - timedelta(seconds=1)),
        expires_at=Evidence[datetime](value=NOW + timedelta(seconds=840)),
        target_selector=driver._target_selector(deployment, regions),
        provider_metadata=StructuredEvidence(value={"regional_execution_ids": {region: f"exec-{region}" for region in regions}}))
    safety = {region: {"document_name": f"expiry-{region}", "document_version": "1"} for region in regions}
    driver._save_journal(attempt, {"schema_version": 1, "run_id": deployment.run_id,
        "deployment_id": deployment.deployment_id, "creation_may_have_started": True,
        "pair_state_path": str(attempt / "pair.tfstate"), "regions": regions,
        "executions": {region: f"exec-{region}" for region in regions}, "safety": safety,
        "regional_parameters": parameters, "expiry_evidence": expiry.model_dump(mode="json")})
    driver._inventory_region = lambda *_args: {"complete": True, "remaining": {}, "resources": {}}
    driver._verify_safety_absent = lambda *_args: True
    result = driver.cleanup(deployment)
    assert result.cleanup_state == "succeeded"
    assert result.expiry.value.state == "failed"
    assert result.expiry.value.failure is not None
    observed = result.expiry.value.provider_metadata.value["regional_outcomes"]
    assert observed["eu-central-1"]["state"] == "Success"
    assert observed["eu-west-2"]["state"] == "Failed"


@pytest.mark.parametrize("tamper", [False, True])
def test_provision_revalidates_registered_evidence_and_time_after_readback(tmp_path, tamper):
    deployment, settings = make_inter_region_attempt(tmp_path, scenario="same_zone")
    region = "eu-central-1"
    driver_clock = [NOW]
    runner = RegistrationRunner()
    driver = AwsProviderDriver(runner, settings, clock=lambda: driver_clock[0])
    expires = NOW + timedelta(seconds=840)
    expiry = ExpiryEvidence(schema_version=2, run_id=deployment.run_id, deployment_id=deployment.deployment_id,
        provider="aws", action="stop", state="registered",
        registration_id=Evidence[str](value=f"aws-ssm:{deployment.deployment_id}"),
        registered_at=Evidence[datetime](value=NOW - timedelta(seconds=1)),
        expires_at=Evidence[datetime](value=expires),
        target_selector=driver._target_selector(deployment, [region]),
        provider_metadata=StructuredEvidence(value={"regional_execution_ids": {region: f"exec-{region}"}}))
    deployment = deployment.model_copy(update={"expiry": Evidence[ExpiryEvidence](value=expiry)})
    attempt = driver._attempt_dir(deployment)
    safety = {region: {"document_name": f"expiry-{region}", "document_version": "1", "role_arn": f"arn:aws:iam::123456789012:role/expiry-{region}"}}
    driver._save_journal(attempt, {"schema_version": 1, "run_id": deployment.run_id, "deployment_id": deployment.deployment_id,
        "registered": True, "regions": [region], "registered_regions": [region],
        "executions": {region: f"exec-{region}"}, "safety": safety,
        "reservation_id": deployment.reservation.value.reservation_id.value,
        "cap_started_at": deployment.reservation.value.cap_started_at.value.isoformat(),
        "cap_expires_at": deployment.reservation.value.expires_at.value.isoformat(), "expiry_trigger": expires.isoformat(),
        "expiry_evidence": expiry.model_dump(mode="json")})
    driver._confirm_automation = lambda *_args: (driver_clock.__setitem__(0, expires) if not tamper else None) or {}
    if tamper:
        deployment = deployment.model_copy(update={"expiry": Evidence[ExpiryEvidence](value=expiry.model_copy(update={"target_selector": "other-attempt"}))})
    with pytest.raises(AwsProviderError, match="differs|no longer fits"):
        driver.provision(deployment)
    assert not any(action == "aws-pair-apply" for action in runner.actions)
