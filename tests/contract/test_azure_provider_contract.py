from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.azure_provider import AzureRuntimeOptions, build_pair_inputs, _command, _workspace
from cloud_network_benchmark.contracts import Evidence, ProviderDeploymentInput, StringEvidence
from cloud_network_benchmark.contracts.admission import ReservationEvidence

UTC = timezone.utc


def deployment(root: Path, fixture: str = "exp-901-azure-single-provider.yaml"):
    path = root / "configs/tests" / fixture
    if not path.exists(): path = root / "configs/experiments" / fixture
    resolved, _ = resolve_campaign(path, root)
    observation = next(o for o in resolved.observations if o.provider == "azure")
    now = datetime.now(UTC)
    reservation = ReservationEvidence(schema_version=2, campaign_id="f06-campaign", reservation_id={"value":"res-f06"},
        state="reserved", reserved_cost_usd={"value":2.0}, cap_started_at={"value":now-timedelta(seconds=30)},
        expires_at={"value":now+timedelta(hours=2)}, cap_start_policy_id={"value":"f11-policy"},
        source_path="prices.csv", source_sha256="a"*64)
    return ProviderDeploymentInput.from_observation(observation, campaign_id="f06-campaign", run_id="f06-campaign-azure",
        bootstrap_template="cloud-init.yaml", terraform_directory="terraform/azure/pair", child_result_path="results/raw/f06-campaign-azure",
        config_sha256=resolved.config_source.sha256, resolved_sha256=StringEvidence(value=resolved.resolved_sha256),
        reservation=Evidence[ReservationEvidence](value=reservation), implementation_git_commit=StringEvidence(value="b"*40),
        design_git_commit=StringEvidence(value="c"*40))


def options(tmp_path: Path, priority: str = "P0"):
    return AzureRuntimeOptions(subscription_id="00000000-0000-0000-0000-000000000001", tenant_id="00000000-0000-0000-0000-000000000002",
        controller_cidr="192.0.2.8/32", attempt_root=tmp_path/"attempt", authentication_reference="private:ssh-key",
        ssh_public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE9F4huSqss0wx3n6f5+WJiEpePM3oXTbuOzBLRR5NCc f06", selected_image="/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/images/providers/Microsoft.Compute/galleries/ubuntu/images/ubuntu2404/versions/24.04.20261001",
        complete_workflow_seconds=900, post_registration_workflow_seconds=600, cap_deadline_monotonic=3600,
        admission_evidence={"priority":priority,"priority_review_reference":"offline-review","outstanding_cleanup":False,"reservation_id":"res-f06","source_sha256":"a"*64}, retry_end_at=datetime.now(UTC)+timedelta(hours=1),
        poll_seconds=15, command_timeout_seconds=60, expiry_cost_input={"source_id":"automation-prices-2026-10-22","date":"2026-10-22","sha256":"d"*64,"automation_rate_per_job_minute":0.002,"job_minutes_bound":20})


@pytest.mark.parametrize("filename,scenario,region_b,zone_b,ppg,peering", [
    ("exp-901-azure-single-provider.yaml", "same_zone", "germanywestcentral", "1", False, False),
    ("exp-002-cross-zone.yaml", "cross_zone", "germanywestcentral", "2", False, False),
    ("exp-003-placement-optimization.yaml", "placement_optimization", "germanywestcentral", "1", True, False),
    ("exp-004-inter-region.yaml", "inter_region", "uksouth", "1", False, True),
])
def test_scenario_mapping_is_explicit(repository_root: Path, tmp_path: Path, filename, scenario, region_b, zone_b, ppg, peering):
    deployment_input = deployment(repository_root, filename)
    result = build_pair_inputs(deployment_input, options(tmp_path, deployment_input.admission.priority))
    assert result["scenario"] == scenario
    assert result["region_b"] == region_b and result["zone_b"] == zone_b
    assert result["create_ppg"] is ppg and result["create_peering"] is peering
    assert result["active_priority"] == "Regular" and result["network_limit_mbps"] == 12500


def test_invalid_azure_shape_and_explicit_host_identity_are_rejected(repository_root: Path, tmp_path: Path):
    d = deployment(repository_root)
    bad = d.model_copy(update={"vm_a":d.vm_a.model_copy(update={"vm_shape":"Standard_D2s_v5"})})
    with pytest.raises(ValueError, match="shape"):
        build_pair_inputs(bad, options(tmp_path, d.admission.priority))
    with pytest.raises(ValueError, match="/32"):
        AzureRuntimeOptions.model_validate({**options(tmp_path).model_dump(), "controller_cidr":"192.0.2.0/24"})


def test_private_action_journal_redacts_auth_reference_public_key_and_private_pem(repository_root: Path, tmp_path: Path):
    d=deployment(repository_root); o=options(tmp_path); root,journal=_workspace(o,d)
    from cloud_network_benchmark.contracts.common import CommandOutcome
    from cloud_network_benchmark.contracts.execution import CommandResult
    class SecretEchoRunner:
        def run(self, request):
            now=datetime.now(UTC)
            return CommandResult(action_id=request.action_id,outcome=CommandOutcome.SUCCEEDED,started_at=now,finished_at=now,duration_seconds=0,exit_code=0,
                stdout=f"{o.authentication_reference} {o.ssh_public_key} -----BEGIN RSA PRIVATE KEY-----sentinel-----END RSA PRIVATE KEY-----",stderr="")
    _command(SecretEchoRunner(),o,root,["echo",o.authentication_reference,o.ssh_public_key],"secret_sentinel",lambda:1)
    raw=journal.read_text()
    assert o.authentication_reference not in raw and o.ssh_public_key not in raw
    assert "sentinel-----END RSA PRIVATE KEY" not in raw and "[REDACTED]" in raw
    assert root.stat().st_mode & 0o777==0o700 and journal.stat().st_mode & 0o777==0o600
