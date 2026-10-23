from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.azure_provider import AzureRuntimeOptions, build_pair_inputs, _command, _workspace, _slug
from cloud_network_benchmark.contracts import Evidence, ProviderDeploymentInput, StringEvidence
from cloud_network_benchmark.contracts.admission import AdmissionRequirements, ReservationEvidence

UTC = timezone.utc


def deployment(root: Path, fixture: str = "exp-901-azure-single-provider.yaml"):
    path = root / "configs/tests" / fixture
    if not path.exists(): path = root / "configs/experiments" / fixture
    resolved, _ = resolve_campaign(path, root)
    observation = next(o for o in resolved.observations if o.provider == "azure")
    now = datetime.now(UTC)
    mode_cap=int(AdmissionRequirements.expected_for(observation.mode,observation.scenario)["lifetime_cap_seconds"])
    reservation = ReservationEvidence(schema_version=2, campaign_id="f06-campaign", reservation_id={"value":"res-f06"},
        state="reserved", reserved_cost_usd={"value":2.0}, cap_started_at={"value":now-timedelta(seconds=30)},
        expires_at={"value":now+timedelta(seconds=mode_cap-30)}, cap_start_policy_id={"value":"f11-policy"},
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
        complete_workflow_seconds=900, post_registration_workflow_seconds=600, cap_deadline_monotonic=1000,
        admission_evidence={"priority":priority,"priority_review_reference":"offline-review","outstanding_cleanup":False,"reservation_id":"res-f06","source_sha256":"a"*64}, retry_end_at=datetime.now(UTC)+timedelta(minutes=16),
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


@pytest.mark.parametrize("image", [
    "Canonical:ubuntu-24_04-lts:server:latest",
    "Contoso:ubuntu-24_04-lts:server:24.04.20261001",
    "Canonical:debian:bookworm:12.0",
    "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/images/providers/Microsoft.Compute/galleries/ubuntu/images/ubuntu2404/versions/latest",
])
def test_only_concrete_canonical_ubuntu_2404_image_inputs_are_accepted(repository_root: Path, tmp_path: Path, image):
    value=options(tmp_path).model_dump(); value["selected_image"]=image
    with pytest.raises(ValueError,match="selected_image"):
        AzureRuntimeOptions.model_validate(value)


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


def test_two_same_cidr_attempts_have_private_nonoverwriting_workspaces(repository_root: Path, tmp_path: Path):
    first=deployment(repository_root); second=first.model_copy(update={"run_id":"f06-campaign-next-azure","deployment_id":"f06-campaign-next-azure-deployment"})
    first_options=options(tmp_path/"one",first.admission.priority); second_options=options(tmp_path/"two",second.admission.priority)
    first_root,first_journal=_workspace(first_options,first); second_root,second_journal=_workspace(second_options,second)
    assert first_root!=second_root and first_journal!=second_journal
    assert _slug(first)!=_slug(second)
    assert first_journal.read_text()!=second_journal.read_text()


def test_command_requests_clip_deadline_and_separate_root_data_dirs(repository_root: Path,tmp_path:Path):
    d=deployment(repository_root); o=options(tmp_path)
    from cloud_network_benchmark.contracts.common import CommandOutcome
    from cloud_network_benchmark.contracts.execution import CommandResult
    root,_=_workspace(o,d); requests=[]
    class Runner:
        def run(self,request):
            requests.append(request); now=datetime.now(UTC)
            return CommandResult(action_id=request.action_id,outcome=CommandOutcome.SUCCEEDED,started_at=now,finished_at=now,duration_seconds=0,exit_code=0,stdout="",stderr="")
    runner=Runner()
    _command(runner,o,root,["terraform","-chdir=terraform/azure/pair","validate"],"pair_validate",lambda:1)
    _command(runner,o,root,["terraform","-chdir=terraform/azure/expiry","validate"],"expiry_validate",lambda:1)
    _command(runner,o,root,["terraform","-chdir=terraform/azure/pair","validate"],"near_deadline",lambda:999)
    assert all(request.timeout_seconds==60 for request in requests[:2])
    assert requests[2].timeout_seconds==1
    assert requests[0].environment["TF_DATA_DIR"] != requests[1].environment["TF_DATA_DIR"]
    assert all(str(root) in request.environment["TF_DATA_DIR"] for request in requests)


def test_foreign_or_symlinked_attempt_workspaces_are_never_adopted(repository_root: Path,tmp_path:Path):
    d=deployment(repository_root); o=options(tmp_path); root,journal=_workspace(o,d)
    state=json.loads(journal.read_text()); state["deployment_id"]="foreign-deployment"; journal.write_text(json.dumps(state))
    with pytest.raises(FileExistsError,match="another deployment"):
        _workspace(o,d)
    linked=options(tmp_path/"link"); linked.attempt_root.parent.mkdir(parents=True,exist_ok=True)
    linked.attempt_root.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(FileExistsError,match="symlink"):
        _workspace(linked,d)


def test_interrupted_command_is_journaled_unknown_with_redacted_error(repository_root:Path,tmp_path:Path):
    d=deployment(repository_root); o=options(tmp_path); root,journal=_workspace(o,d)
    class InterruptedRunner:
        def run(self,request): raise RuntimeError(f"controller interruption {o.authentication_reference} {o.ssh_public_key}")
    with pytest.raises(RuntimeError):
        _command(InterruptedRunner(),o,root,["az","test",o.authentication_reference,o.ssh_public_key],"interrupt",lambda:1)
    contents=journal.read_text()
    state=json.loads(contents)
    assert state["actions"][-1]["outcome"]=="unknown"
    assert o.authentication_reference not in contents and o.ssh_public_key not in contents
