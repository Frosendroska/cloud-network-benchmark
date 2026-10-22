from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from cloud_network_benchmark.contracts.execution import CommandResult
from cloud_network_benchmark.providers.aws import AwsProviderDriver


NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


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
        "CurrentAction": "aws:sleep",
        "CurrentStepName": "waitUntilExpiry",
        "Parameters": params,
        "StepExecutions": [{"StepName": "waitUntilExpiry", "Action": "aws:sleep", "StepStatus": "InProgress", "Inputs": {"Timestamp": ["2026-10-22T12:14:00Z"]}}],
    }
    described = {"AutomationExecutionMetadataList": [{"AutomationExecutionId": "exec-1"}]}
    runner = QueueRunner([
        (1, "", "response lost"),
        (0, json.dumps(described), ""),
        (0, json.dumps({"AutomationExecution": {**execution, "Parameters": {**params, "AutomationAssumeRole": ["arn:aws:iam::123456789012:role/expiry"], "ExpiryTriggerUtc": ["2026-10-22T12:14:00Z"], "Region": ["eu-central-1"]}}}), ""),
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
