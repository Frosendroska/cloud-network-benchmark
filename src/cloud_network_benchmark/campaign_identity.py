"""Read-only checks that provider CLIs select the reviewed cloud identities."""
from __future__ import annotations

import json
import subprocess
from typing import Any, Callable, Mapping


_TIMEOUT_SECONDS = 30


def check_provider_identities(
    providers: dict[str, Any], *, run: Callable[..., Any] | None = None,
) -> list[str]:
    """Check each configured CLI identity without exposing credential output.

    Commands inherit the controller environment and never change active account,
    subscription, tenant, project, or credential configuration. GCP ADC is
    checked by discarding token output and inspecting only the process status.
    """
    runner = run or subprocess.run
    if not isinstance(providers, Mapping):
        return ["provider identity settings must be a mapping"]
    blockers: list[str] = []
    for provider, expected in providers.items():
        if not isinstance(expected, Mapping):
            blockers.append(f"{provider} identity settings are invalid")
            continue
        try:
            if provider == "aws":
                _check_aws(expected, runner)
            elif provider == "azure":
                _check_azure(expected, runner)
            elif provider == "gcp":
                _check_gcp(expected, runner)
            else:
                blockers.append(f"unsupported provider identity check: {provider}")
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
            blockers.append(_provider_error(provider))
    return blockers


def _check_aws(expected: Mapping[str, Any], runner: Callable[..., Any]) -> None:
    account_id = str(expected.get("account_id") or "")
    if not account_id:
        raise ValueError("missing expected AWS account")
    completed = runner(["aws", "sts", "get-caller-identity", "--output", "json", "--no-cli-pager"],
                       check=False, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS)
    if completed.returncode != 0:
        raise ValueError("AWS identity command failed")
    identity = json.loads(completed.stdout)
    if not isinstance(identity, dict) or identity.get("Account") != account_id:
        raise ValueError("AWS identity differs")


def _check_azure(expected: Mapping[str, Any], runner: Callable[..., Any]) -> None:
    subscription_id = str(expected.get("subscription_id") or "")
    tenant_id = str(expected.get("tenant_id") or "")
    if not subscription_id or not tenant_id:
        raise ValueError("missing expected Azure subscription/tenant")
    completed = runner(["az", "account", "show", "--output", "json", "--only-show-errors"],
                       check=False, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS)
    if completed.returncode != 0:
        raise ValueError("Azure identity command failed")
    identity = json.loads(completed.stdout)
    if (not isinstance(identity, dict) or str(identity.get("id", "")).casefold() != subscription_id.casefold()
            or str(identity.get("tenantId", "")).casefold() != tenant_id.casefold()
            or identity.get("state", "Enabled") != "Enabled"):
        raise ValueError("Azure identity differs")


def _check_gcp(expected: Mapping[str, Any], runner: Callable[..., Any]) -> None:
    project_id = str(expected.get("project_id") or "")
    if not project_id:
        raise ValueError("missing expected GCP project")
    active = runner(["gcloud", "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"],
                    check=False, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS)
    configured_project = runner(["gcloud", "config", "get-value", "project", "--quiet"],
                                check=False, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS)
    if active.returncode != 0 or configured_project.returncode != 0:
        raise ValueError("GCP CLI identity command failed")
    accounts = [line.strip() for line in active.stdout.splitlines() if line.strip()]
    if len(accounts) != 1 or configured_project.stdout.strip() != project_id:
        raise ValueError("GCP active identity/project differs")

    # stdout is the short-lived access token. Discard both streams and retain
    # only the return code; never put a credential in output, an exception, or
    # the operator log. This tests ADC usability without persisting a token.
    adc = runner(["gcloud", "auth", "application-default", "print-access-token"],
                 check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                 timeout=_TIMEOUT_SECONDS)
    if adc.returncode != 0:
        raise ValueError("GCP application-default credentials are unavailable")


def _provider_error(provider: str) -> str:
    if provider == "aws":
        return ("AWS credentials are unavailable or the caller account differs from providers.aws.account_id; "
                "refresh credentials for the reviewed account")
    if provider == "azure":
        return ("Azure credentials are unavailable or the active subscription/tenant differs from configured "
                "providers.azure identifiers; select the reviewed identity")
    if provider == "gcp":
        return ("GCP credentials are unavailable or active account/project differs from providers.gcp.project_id; "
                "select the reviewed identity and refresh application-default credentials")
    return f"{provider} credentials or configured identity could not be verified"
