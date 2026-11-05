"""Read-only provider identity checks never expose credential/error output."""
from types import SimpleNamespace
import json
import subprocess

import pytest

from cloud_network_benchmark.campaign_identity import check_provider_identities


@pytest.mark.parametrize("provider,expected,response", [
    ("aws", {"account_id": "123456789012"}, {"Account": "123456789012"}),
    ("azure", {"subscription_id": "sub-a", "tenant_id": "tenant-a"}, {"id": "SUB-A", "tenantId": "TENANT-A", "state": "Enabled"}),
])
@pytest.mark.parametrize("matching", [True, False])
def test_account_identity_must_match_expected_scope(provider, expected, response, matching):
    calls = []
    if not matching:
        response = {**response, "Account" if provider == "aws" else "id": "different-account"}

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(response), stderr="sensitive irrelevant output")

    blockers = check_provider_identities({provider: expected}, run=run)
    assert bool(blockers) is not matching
    assert len(calls) == 1 and calls[0][1]["timeout"] <= 30
    assert not any("sensitive" in value for value in blockers)
    assert calls[0][0][:3] == (["aws", "sts", "get-caller-identity"] if provider == "aws" else ["az", "account", "show"])


@pytest.mark.parametrize("failure", [None, "many_accounts", "wrong_project", "adc_failure"])
def test_gcp_checks_one_active_account_exact_project_and_usable_adc_with_discarded_token(failure):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        assert kwargs["timeout"] <= 30
        if command[:3] == ["gcloud", "auth", "list"]:
            return SimpleNamespace(returncode=0, stdout="reviewed@example.test\nother@example.test\n" if failure == "many_accounts" else "reviewed@example.test\n", stderr="")
        if command[:3] == ["gcloud", "config", "get-value"]:
            return SimpleNamespace(returncode=0, stdout="wrong-project" if failure == "wrong_project" else "reviewed-project", stderr="")
        assert command == ["gcloud", "auth", "application-default", "print-access-token"]
        assert kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
        assert "capture_output" not in kwargs
        return SimpleNamespace(returncode=1 if failure == "adc_failure" else 0, stdout="must never read token", stderr="must never read error")

    blockers = check_provider_identities({"gcp": {"project_id": "reviewed-project"}}, run=run)
    assert bool(blockers) is (failure is not None)
    assert not any("must never" in value or "@example.test" in value for value in blockers)
    assert len(calls) == (2 if failure in {"many_accounts", "wrong_project"} else 3)


@pytest.mark.parametrize("error", [subprocess.TimeoutExpired(["aws"], 30, output="secret-token"), OSError("secret-error")])
def test_identity_command_failure_reports_action_without_error_or_token_output(error):
    def run(*args, **kwargs):
        raise error

    blockers = check_provider_identities({"aws": {"account_id": "123456789012"}}, run=run)
    assert len(blockers) == 1
    assert "refresh credentials" in blockers[0]
    assert "secret" not in blockers[0]
