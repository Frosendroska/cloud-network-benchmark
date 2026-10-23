from datetime import datetime, timedelta, timezone
import pytest

from cloud_network_benchmark.providers.gcp import GcpProviderDriver, SAFETY_LEAD_SECONDS
from cloud_network_benchmark.contracts.admission import ReservationEvidence
from cloud_network_benchmark.contracts.common import Evidence, StringEvidence


def test_original_reservation_lead_is_immutable_across_retry_and_controller_delay():
    started = datetime.now(timezone.utc) - timedelta(seconds=5)
    original_end = datetime.now(timezone.utc) + timedelta(seconds=1800)
    reservation = ReservationEvidence(schema_version=2, campaign_id="c", reservation_id=Evidence[str](value="r-1"), state="reserved", reserved_cost_usd=Evidence[float](value=10), cap_started_at=Evidence[datetime](value=started), expires_at=Evidence[datetime](value=original_end), cap_start_policy_id=StringEvidence(value="reserved-before-provider"), source_path="register.csv", source_sha256="a" * 64)
    first = original_end - timedelta(seconds=SAFETY_LEAD_SECONDS)
    delayed = datetime.now(timezone.utc) + timedelta(seconds=300)
    assert first == original_end - timedelta(seconds=30)
    assert delayed < first
    assert reservation.expires_at.value == original_end
    assert GcpProviderDriver._fit(reservation, first, 60) is None


def test_registration_observes_both_absolute_stop_templates_before_any_pair(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    from cloud_network_benchmark.config import resolve_campaign
    from cloud_network_benchmark.contracts.admission import ExpiryRequest
    from cloud_network_benchmark.contracts.common import Evidence, StringEvidence, StructuredEvidence
    from cloud_network_benchmark.contracts.deployment import ProviderDeploymentInput
    from cloud_network_benchmark.contracts.execution import CommandResult
    from cloud_network_benchmark.providers.gcp import GcpProviderDriver, GcpRuntimeSettings

    root = Path(__file__).parents[2]
    resolved, _ = resolve_campaign(root / "configs/tests/exp-902-gcp-single-provider.yaml", root)
    observation = next(item for item in resolved.observations if item.provider == "gcp")
    now = datetime.now(timezone.utc)
    source_hash = "a" * 64
    observation = observation.model_copy(update={"admission": observation.admission.model_copy(update={"cost_input_sha256": StringEvidence(value=source_hash)})})
    reservation = ReservationEvidence(schema_version=2, campaign_id="gcp-test", reservation_id=Evidence[str](value="reserve-gcp-1"), state="reserved", reserved_cost_usd=Evidence[float](value=10), cap_started_at=Evidence[datetime](value=now-timedelta(seconds=2)), expires_at=Evidence[datetime](value=now+timedelta(seconds=1200)), cap_start_policy_id=StringEvidence(value="reservation-activation-before-provision"), source_path="dated-register.csv", source_sha256=source_hash)
    deployment = ProviderDeploymentInput.from_observation(observation, campaign_id="gcp-test", run_id="gcp-test-gcp", bootstrap_template="bootstrap/cloud-init.yaml", terraform_directory="terraform/gcp", child_result_path="results/gcp-test", config_sha256=resolved.config_source.sha256, implementation_git_commit=StringEvidence(value="b"*40), design_git_commit=StringEvidence(value="c"*40), reservation=Evidence(value=reservation))
    end = reservation.expires_at.value-timedelta(seconds=SAFETY_LEAD_SECONDS)
    request = ExpiryRequest(schema_version=2, run_id=deployment.run_id, deployment_id=deployment.deployment_id, provider="gcp", action="stop", expires_at=Evidence[datetime](value=end), reservation_id=reservation.reservation_id, resource_namespace=deployment.run_id, target_selector="project=cnb-test-12345;roles=vm_a,vm_b")
    key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA test"
    settings = GcpRuntimeSettings(project_id="cnb-test-12345", controller_cidr="203.0.113.4/32", images={"europe-west3":"projects/ubuntu-os-cloud/global/images/ubuntu-2404-lts-amd64-v20261001","europe-west2":"projects/ubuntu-os-cloud/global/images/ubuntu-2404-lts-amd64-v20261001"}, public_key=key, authentication_reference="attempt-identity", private_state_root=tmp_path/"private", cost_input_id=observation.admission.cost_input_id, cost_input_sha256=source_hash, cost_source_path="dated-register.csv", complete_workflow_cost_handoff=StructuredEvidence(value={"source_path":"dated-register.csv","source_sha256":source_hash,"prices_checked_on":"2026-10-23","estimated_total_usd":10,"units":{"vm":"hour","disk":"GiB-hour","address":"hour","transfer":"GiB"}}), complete_workflow_remaining_seconds=300)
    class Runner:
        def run(self, req):
            started=datetime.now(timezone.utc)
            role="vm_a" if "vm_a" in req.action_id else "vm_b"
            value={"id":"111" if role=="vm_a" else "222","creationTimestamp":"2026-10-23T12:00:00Z","properties":{"scheduling":{"provisioningModel":"STANDARD","instanceTerminationAction":"STOP","terminationTime":end.isoformat().replace("+00:00","Z")}}}
            return CommandResult(action_id=req.action_id,outcome="succeeded",started_at=started,finished_at=started,duration_seconds=0.0,exit_code=0,stdout=json.dumps(value),stderr="")
    driver=GcpProviderDriver(Runner(),settings,clock=lambda:now)
    events=[]
    def terraform(_attempt, root_name, action, _deployment, _extra):
        events.append((root_name,action))
        if action == "destroy": return {}
        return {"templates":{"value":{"vm_a":{"id":"111","name":"fake-vm_a","self_link_unique":"projects/p/global/instanceTemplates/111"},"vm_b":{"id":"222","name":"fake-vm_b","self_link_unique":"projects/p/global/instanceTemplates/222"}}}}
    monkeypatch.setattr(driver,"_terraform",terraform)
    result=driver.register_expiry(deployment,request)
    assert result.state == "registered"
    assert set(result.provider_metadata.value["templates"]) == {"vm_a","vm_b"}
    assert events == [("protection","apply")]


def _cleanup_fixture(tmp_path):
    from pathlib import Path
    from cloud_network_benchmark.config import resolve_campaign
    from cloud_network_benchmark.contracts.common import Evidence, StringEvidence, StructuredEvidence
    from cloud_network_benchmark.contracts.deployment import ProviderDeploymentInput
    from cloud_network_benchmark.providers.gcp import GcpProviderDriver, GcpRuntimeSettings
    root = Path(__file__).parents[2]
    resolved, _ = resolve_campaign(root / "configs/tests/exp-902-gcp-single-provider.yaml", root)
    observation = next(item for item in resolved.observations if item.provider == "gcp")
    now = datetime.now(timezone.utc)
    source_hash = "a" * 64
    observation = observation.model_copy(update={"admission": observation.admission.model_copy(update={"cost_input_sha256": StringEvidence(value=source_hash)})})
    reservation = ReservationEvidence(schema_version=2, campaign_id="cleanup-test", reservation_id=Evidence[str](value="reserve-cleanup-1"), state="reserved", reserved_cost_usd=Evidence[float](value=5), cap_started_at=Evidence[datetime](value=now-timedelta(seconds=1)), expires_at=Evidence[datetime](value=now+timedelta(seconds=1200)), cap_start_policy_id=StringEvidence(value="reserve-first"), source_path="dated-register.csv", source_sha256=source_hash)
    deployment = ProviderDeploymentInput.from_observation(observation, campaign_id="cleanup-test", run_id="cleanup-test-gcp", bootstrap_template="bootstrap/cloud-init.yaml", terraform_directory="terraform/gcp", child_result_path="results/cleanup-test", config_sha256=resolved.config_source.sha256, implementation_git_commit=StringEvidence(value="b"*40), design_git_commit=StringEvidence(value="c"*40), reservation=Evidence(value=reservation))
    settings = GcpRuntimeSettings(project_id="cnb-test-12345", controller_cidr="203.0.113.4/32", images={"europe-west3":"projects/ubuntu-os-cloud/global/images/ubuntu-2404-lts-amd64-v20261001","europe-west2":"projects/ubuntu-os-cloud/global/images/ubuntu-2404-lts-amd64-v20261001"}, public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA test", authentication_reference="attempt-key-ref", private_state_root=tmp_path/"state", cost_input_id=observation.admission.cost_input_id, cost_input_sha256=source_hash, cost_source_path="dated-register.csv", complete_workflow_cost_handoff=StructuredEvidence(value={"source_path":"dated-register.csv","source_sha256":source_hash,"prices_checked_on":"2026-10-23","estimated_total_usd":5,"units":{"vm":"hour","disk":"GiB-hour","address":"hour","transfer":"GiB"}}), complete_workflow_remaining_seconds=300)
    return deployment, GcpProviderDriver(object(), settings)


def test_cleanup_verifies_compute_absence_before_removing_protection_and_is_idempotent(tmp_path, monkeypatch):
    from cloud_network_benchmark.contracts.admission import ExpiryEvidence
    from cloud_network_benchmark.contracts.common import Evidence, StructuredEvidence
    deployment, driver = _cleanup_fixture(tmp_path)
    now = datetime.now(timezone.utc)
    expiry = ExpiryEvidence(schema_version=2, run_id=deployment.run_id, deployment_id=deployment.deployment_id, provider="gcp", action="stop", state="registered", registration_id=Evidence[str](value="111:222"), registered_at=Evidence[datetime](value=now-timedelta(seconds=10)), expires_at=Evidence[datetime](value=now+timedelta(seconds=900)), target_selector="project=cnb-test-12345;roles=vm_a,vm_b", provider_metadata=StructuredEvidence(value={"templates":{"vm_a":{"id":"111"},"vm_b":{"id":"222"}}}))
    attempt=driver._attempt_dir(deployment)
    driver._save(attempt,{"run_id":deployment.run_id,"deployment_id":deployment.deployment_id,"creation_may_have_started":True,"registered":True,"expiry":expiry.model_dump(mode="json"),"templates":{"vm_a":{"id":"111"},"vm_b":{"id":"222"}}})
    commands=[]
    def terraform(_attempt, root, action, _deployment, _extra): commands.append((root,action)); return {}
    monkeypatch.setattr(driver,"_terraform",terraform)
    monkeypatch.setattr(driver,"_gcloud_json",lambda _args, _action: [])
    result=driver.cleanup(deployment)
    assert result.cleanup_state == "succeeded"
    assert result.expiry.value.state == "cancelled"
    assert commands == [("pair","destroy"),("protection","destroy")]
    assert result.inventory.value["pages_complete"] is True
    driver.cleanup(deployment)
    assert commands == [("pair","destroy"),("protection","destroy")]


def test_cleanup_blocks_protection_removal_when_compute_remains_or_inventory_is_unknown(tmp_path, monkeypatch):
    from cloud_network_benchmark.contracts.admission import ExpiryEvidence
    from cloud_network_benchmark.contracts.common import Evidence, StructuredEvidence
    from cloud_network_benchmark.providers.gcp import GcpProviderError
    deployment, driver = _cleanup_fixture(tmp_path)
    now=datetime.now(timezone.utc)
    expiry=ExpiryEvidence(schema_version=2,run_id=deployment.run_id,deployment_id=deployment.deployment_id,provider="gcp",action="stop",state="registered",registration_id=Evidence[str](value="111:222"),registered_at=Evidence[datetime](value=now-timedelta(seconds=10)),expires_at=Evidence[datetime](value=now+timedelta(seconds=900)),target_selector="project=cnb-test-12345;roles=vm_a,vm_b",provider_metadata=StructuredEvidence(value={"templates":{"vm_a":{"id":"111"},"vm_b":{"id":"222"}}}))
    attempt=driver._attempt_dir(deployment)
    driver._save(attempt,{"run_id":deployment.run_id,"deployment_id":deployment.deployment_id,"creation_may_have_started":True,"registered":True,"expiry":expiry.model_dump(mode="json")})
    commands=[]
    monkeypatch.setattr(driver,"_terraform",lambda _attempt,root,action,_deployment,_extra: commands.append((root,action)) or {})
    import hashlib
    labels={"cnb-deploy":hashlib.sha256(deployment.deployment_id.encode()).hexdigest()[:20]}
    monkeypatch.setattr(driver,"_gcloud_json",lambda args,_action: [{"id":"42","status":"TERMINATED","labels":labels}] if "instances" in args and "list" in args else [])
    result=driver.cleanup(deployment)
    assert result.cleanup_state == "failed"
    assert result.cleanup_failure.category == "residual_compute"
    assert commands == [("pair","destroy")]


def test_unknown_inventory_shape_fails_closed_and_keeps_native_protection(tmp_path, monkeypatch):
    from cloud_network_benchmark.contracts.admission import ExpiryEvidence
    from cloud_network_benchmark.contracts.common import Evidence, StructuredEvidence
    deployment, driver = _cleanup_fixture(tmp_path)
    now=datetime.now(timezone.utc)
    expiry=ExpiryEvidence(schema_version=2,run_id=deployment.run_id,deployment_id=deployment.deployment_id,provider="gcp",action="stop",state="registered",registration_id=Evidence[str](value="111:222"),registered_at=Evidence[datetime](value=now-timedelta(seconds=10)),expires_at=Evidence[datetime](value=now+timedelta(seconds=900)),target_selector="project=cnb-test-12345;roles=vm_a,vm_b",provider_metadata=StructuredEvidence(value={"templates":{"vm_a":{"id":"111"},"vm_b":{"id":"222"}}}))
    attempt=driver._attempt_dir(deployment)
    driver._save(attempt,{"run_id":deployment.run_id,"deployment_id":deployment.deployment_id,"creation_may_have_started":True,"registered":True,"expiry":expiry.model_dump(mode="json")})
    commands=[]
    monkeypatch.setattr(driver,"_terraform",lambda _attempt,root,action,_deployment,_extra: commands.append((root,action)) or {})
    monkeypatch.setattr(driver,"_gcloud_json",lambda _args,_action: {"items":[],"nextPageToken":"opaque"})
    result=driver.cleanup(deployment)
    assert result.cleanup_state == "failed"
    assert result.cleanup_failure.category == "inventory_unknown"
    assert commands == [("pair","destroy")]


def test_open_registered_attempt_blocks_a_new_launch(tmp_path):
    from cloud_network_benchmark.providers.gcp import GcpProviderError
    deployment, driver = _cleanup_fixture(tmp_path)
    root=driver.settings.private_state_root
    path=root/"other"/"campaign-gcp"/"campaign-gcp-deployment"
    path.mkdir(parents=True)
    (path/"attempt.json").write_text('{"run_id":"campaign-gcp","deployment_id":"campaign-gcp-deployment","registered":true}')
    with pytest.raises(GcpProviderError, match="open cleanup"):
        driver._assert_no_open_cleanup(deployment)
