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
    driver = object.__new__(GcpProviderDriver)
    driver.clock = lambda: started + timedelta(seconds=1)
    assert driver._fit(reservation, first, 60) is None


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
            value={"id":"111" if role=="vm_a" else "222","creationTimestamp":(now-timedelta(seconds=1)).isoformat().replace("+00:00","Z"),"properties":{"scheduling":{"provisioningModel":"STANDARD","instanceTerminationAction":"STOP","terminationTime":end.isoformat().replace("+00:00","Z")}}}
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
    retry=driver.register_expiry(deployment,request)
    assert retry.registered_at == result.registered_at
    assert events == [("protection","apply")]
    restarted=GcpProviderDriver(Runner(),settings,clock=lambda:now)
    assert restarted.register_expiry(deployment,request).registered_at == result.registered_at
    assert events == [("protection","apply")]

    attempt=driver._attempt_dir(deployment)
    driver._save(attempt,{"run_id":deployment.run_id,"deployment_id":deployment.deployment_id,"commands":[]})
    partial=[]
    def partial_protection(_attempt, root_name, action, _deployment, _extra):
        partial.append((root_name,action))
        return {"templates":{"value":{"vm_a":{"id":"111","name":"fake-vm_a","self_link_unique":"projects/p/global/instanceTemplates/111"}}}} if action == "apply" else {}
    monkeypatch.setattr(driver,"_terraform",partial_protection)
    monkeypatch.setattr(driver,"_inventory",lambda _deployment:{"pages_complete":True,"resources":[],"instances":[],"disks":[],"addresses":[],"support_resources":[]})
    failed=driver.register_expiry(deployment,request)
    assert failed.state == "failed"
    assert partial == [("protection","apply"),("protection","destroy")]


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


def test_cleanup_does_not_claim_cancellation_or_trigger_after_native_deadline(tmp_path, monkeypatch):
    from cloud_network_benchmark.contracts.admission import ExpiryEvidence
    from cloud_network_benchmark.contracts.common import Evidence, StructuredEvidence
    deployment, driver = _cleanup_fixture(tmp_path)
    now=datetime.now(timezone.utc)
    expiry=ExpiryEvidence(schema_version=2,run_id=deployment.run_id,deployment_id=deployment.deployment_id,provider="gcp",action="stop",state="registered",registration_id=Evidence[str](value="111:222"),registered_at=Evidence[datetime](value=now-timedelta(seconds=30)),expires_at=Evidence[datetime](value=now-timedelta(seconds=5)),target_selector="project=cnb-test-12345;roles=vm_a,vm_b",provider_metadata=StructuredEvidence(value={"native_absolute_stop":True}))
    attempt=driver._attempt_dir(deployment)
    driver._save(attempt,{"run_id":deployment.run_id,"deployment_id":deployment.deployment_id,"registered":True,"expiry":expiry.model_dump(mode="json")})
    monkeypatch.setattr(driver,"_terraform",lambda _attempt,_root,_action,_deployment,_extra:{})
    monkeypatch.setattr(driver,"_gcloud_json",lambda _args,_action: [])
    result=driver.cleanup(deployment)
    assert result.cleanup_state == "succeeded"
    assert result.expiry.value.state == "registered"
    assert result.expiry.value.provider_metadata.value["trigger_due_at_cleanup"] is True
    assert result.expiry.value.provider_metadata.value["trigger_outcome"] == "unverified"


def test_protection_destroy_failure_does_not_claim_cancelled_expiry(tmp_path,monkeypatch):
    from cloud_network_benchmark.contracts.admission import ExpiryEvidence
    from cloud_network_benchmark.contracts.common import Evidence,StructuredEvidence
    from cloud_network_benchmark.providers.gcp import GcpProviderError
    deployment,driver=_cleanup_fixture(tmp_path)
    now=datetime.now(timezone.utc)
    expiry=ExpiryEvidence(schema_version=2,run_id=deployment.run_id,deployment_id=deployment.deployment_id,provider="gcp",action="stop",state="registered",registration_id=Evidence[str](value="111:222"),registered_at=Evidence[datetime](value=now-timedelta(seconds=10)),expires_at=Evidence[datetime](value=now+timedelta(seconds=900)),target_selector="project=cnb-test-12345;roles=vm_a,vm_b",provider_metadata=StructuredEvidence(value={"native_absolute_stop":True}))
    attempt=driver._attempt_dir(deployment)
    driver._save(attempt,{"run_id":deployment.run_id,"deployment_id":deployment.deployment_id,"registered":True,"expiry":expiry.model_dump(mode="json")})
    def terraform(_attempt,root,action,_deployment,_extra):
        if root=="protection" and action=="destroy":raise GcpProviderError("protection deletion failed",category="terraform_failed")
        return {}
    monkeypatch.setattr(driver,"_terraform",terraform)
    monkeypatch.setattr(driver,"_gcloud_json",lambda _args,_action:[])
    result=driver.cleanup(deployment)
    assert result.cleanup_state=="failed"
    assert result.expiry.value.state=="registered"
    assert result.cleanup_failure.category=="terraform_failed"


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
    labels={"cnb-run":hashlib.sha256(deployment.run_id.encode()).hexdigest()[:20],"cnb-deploy":hashlib.sha256(deployment.deployment_id.encode()).hexdigest()[:20]}
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


def test_attempt_with_prior_creation_or_cleanup_cannot_be_relaunched(tmp_path):
    import json
    from cloud_network_benchmark.providers.gcp import GcpProviderError
    deployment, driver = _cleanup_fixture(tmp_path)
    attempt=driver._attempt_dir(deployment)
    for state in ({"creation_may_have_started":True},{"cleanup_state":"failed"},{"cleanup_state":"succeeded"},{"launch_blocked":True}):
        driver._save(attempt,{"run_id":deployment.run_id,"deployment_id":deployment.deployment_id,**state})
        with pytest.raises(GcpProviderError,match="this attempt already has creation or cleanup state"):
            driver._assert_no_open_cleanup(deployment)
    other=tmp_path/"state"/"closed-other"/"attempt.json"
    other.parent.mkdir(parents=True)
    other.write_text(json.dumps({"run_id":"older-gcp","deployment_id":"older-gcp-deployment","registered":True,"cleanup_state":"succeeded"}))
    driver._save(attempt,{"run_id":deployment.run_id,"deployment_id":deployment.deployment_id})
    assert driver._assert_no_open_cleanup(deployment) is None


def test_pair_apply_records_exact_template_binding_input_hash(tmp_path,monkeypatch):
    import hashlib,json
    from datetime import datetime,timezone
    from cloud_network_benchmark.contracts.execution import CommandResult
    deployment,driver=_cleanup_fixture(tmp_path)
    attempt=driver._attempt_dir(deployment)
    driver._active_attempt=attempt
    driver._save(attempt,{"run_id":deployment.run_id,"deployment_id":deployment.deployment_id,"commands":[]})
    def fake_run(action,_argv,_cwd,timeout=None):
        now=datetime.now(timezone.utc)
        stdout='{"instances":{}}' if action=="gcp-pair-outputs" else ""
        return CommandResult(action_id=action,outcome="succeeded",started_at=now,finished_at=now,duration_seconds=0.0,exit_code=0,stdout=stdout,stderr="")
    monkeypatch.setattr(driver,"_run",fake_run)
    driver._terraform(attempt,"pair","apply",deployment,{"template_a":"projects/test/global/instanceTemplates/unique-a","template_b":"projects/test/global/instanceTemplates/unique-b"})
    variables=(attempt/"pair"/"vars.json").read_bytes()
    binding=json.loads((attempt/"attempt.json").read_text())["pair_binding"]
    assert binding["config_sha256"]==hashlib.sha256(variables).hexdigest()
    assert binding["template_a"].endswith("unique-a") and binding["template_b"].endswith("unique-b")


def test_s1_fake_provision_normalizes_authenticated_vm_disk_image_and_hostkey_readbacks(tmp_path,monkeypatch):
    import json
    from cloud_network_benchmark.contracts.admission import ExpiryEvidence
    from cloud_network_benchmark.contracts.common import Evidence,StructuredEvidence
    deployment,driver=_cleanup_fixture(tmp_path)
    now=datetime.now(timezone.utc)
    trigger=deployment.reservation.value.expires_at.value-timedelta(seconds=SAFETY_LEAD_SECONDS)
    expiry=ExpiryEvidence(schema_version=2,run_id=deployment.run_id,deployment_id=deployment.deployment_id,provider="gcp",action="stop",state="registered",registration_id=Evidence[str](value="111:222"),registered_at=Evidence[datetime](value=now-timedelta(seconds=5)),expires_at=Evidence[datetime](value=trigger),target_selector="project=cnb-test-12345;roles=vm_a,vm_b",provider_metadata=StructuredEvidence(value={"native_absolute_stop":True}))
    deployment=deployment.model_copy(update={"expiry":Evidence(value=expiry)})
    attempt=driver._attempt_dir(deployment)
    templates={"vm_a":{"id":"111","name":"template-a","self_link_unique":"projects/test/global/instanceTemplates/unique-a"},"vm_b":{"id":"222","name":"template-b","self_link_unique":"projects/test/global/instanceTemplates/unique-b"}}
    driver._save(attempt,{"run_id":deployment.run_id,"deployment_id":deployment.deployment_id,"registered":True,"expiry":expiry.model_dump(mode="json"),"templates":templates})
    monkeypatch.setattr(driver,"_verify_registered_templates",lambda *_args:None)
    monkeypatch.setattr(driver,"_terraform",lambda *_args:{"instances":{"value":{"vm_a":{"name":"attempt-a","zone":deployment.vm_a.zone,"id":"101"},"vm_b":{"name":"attempt-b","zone":deployment.vm_b.zone,"id":"102"}}}})
    hostkey="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA synthetic"
    def readback(args,action):
        role="vm_a" if "attempt-a" in args or action.endswith("vm_a") else "vm_b"
        intent=deployment.vm_a if role=="vm_a" else deployment.vm_b
        address="10.80.1.10" if role=="vm_a" else "10.80.1.11"
        zone_path=f"https://compute.googleapis.com/compute/v1/projects/test/zones/{intent.zone}"
        if action.startswith("gcp-boot-disk"):
            return {"id":"201" if role=="vm_a" else "202","name":f"disk-{role}","zone":zone_path,"sizeGb":32,"type":"zones/europe-west3-a/diskTypes/pd-balanced","sourceImage":f"projects/ubuntu-os-cloud/global/images/{driver.settings.images[intent.region].rsplit('/',1)[-1]}"}
        if action.startswith("gcp-image-read"):
            return {"architecture":"X86_64","name":driver.settings.images[intent.region].rsplit("/",1)[-1]}
        if action=="gcp-guest-hostkeys":
            return {"queryValue":{"items":[{"namespace":"hostkeys","key":"0","value":hostkey}]}}
        if action.startswith("gcp-hostkey-instance"):
            return {"id":"101" if role=="vm_a" else "102","zone":zone_path,"networkInterfaces":[{"networkIP":address}]}
        return {"id":"101" if role=="vm_a" else "102","name":f"attempt-{role[-1]}","zone":zone_path,"machineType":"n2-standard-4","scheduling":{"provisioningModel":"STANDARD","instanceTerminationAction":"STOP","terminationTime":trigger.isoformat().replace("+00:00","Z")},"networkInterfaces":[{"networkIP":address,"nicType":"GVNIC","accessConfigs":[{"natIP":"8.8.8.8"}]}],"disks":[{"boot":True,"source":f"https://compute.googleapis.com/compute/v1/projects/test/zones/{intent.zone}/disks/disk-{role}"}],"resourcePolicies":[]}
    monkeypatch.setattr(driver,"_gcloud_json",readback)
    result=driver.provision(deployment)
    assert result.vm_a.private_ipv4=="10.80.1.10" and result.vm_b.private_ipv4=="10.80.1.11"
    assert result.vm_a.actual_shape==result.vm_b.actual_shape=="n2-standard-4"
    assert result.vm_a.physical_zone_id.unavailable_reason
    assert result.provider_metadata.value["no_source_template_response_claim"] is True
    journal=json.loads((attempt/"attempt.json").read_text())
    assert journal["observed_disks"][0]["id"] in {"201","202"}


def test_registered_template_replacement_or_weakened_stop_fails_precreate(tmp_path, monkeypatch):
    from cloud_network_benchmark.providers.gcp import GcpProviderError
    deployment, driver = _cleanup_fixture(tmp_path)
    trigger = deployment.reservation.value.expires_at.value - timedelta(seconds=SAFETY_LEAD_SECONDS)
    expected = trigger.isoformat().replace("+00:00", "Z")
    described = {"id": "template-1", "properties": {"scheduling": {"provisioningModel": "STANDARD", "instanceTerminationAction": "STOP", "terminationTime": expected}}}
    described_b = {**described, "id": "template-2"}
    template = {"id": "template-1", "name": "test-template", "self_link_unique": "projects/test/global/instanceTemplates/unique-1", "sha256": __import__("hashlib").sha256(__import__("json").dumps(described, sort_keys=True).encode()).hexdigest()}
    second = {"id": "template-2", "name": "test-template-b", "self_link_unique": "projects/test/global/instanceTemplates/unique-2", "sha256": __import__("hashlib").sha256(__import__("json").dumps(described_b, sort_keys=True).encode()).hexdigest()}
    monkeypatch.setattr(driver, "_gcloud_json", lambda args, _action: described_b if "test-template-b" in args else described)
    driver._verify_registered_templates({"templates": {"vm_a": template, "vm_b": second}}, trigger)
    replaced = {**described, "id": "template-2"}
    monkeypatch.setattr(driver, "_gcloud_json", lambda *_args: replaced)
    with pytest.raises(GcpProviderError, match="identity or absolute STOP policy changed"):
        driver._verify_registered_templates({"templates": {"vm_a": template, "vm_b": second}}, trigger)
    weakened = {**described, "properties": {"scheduling": {"provisioningModel": "STANDARD", "instanceTerminationAction": "DELETE", "terminationTime": expected}}}
    monkeypatch.setattr(driver, "_gcloud_json", lambda *_args: weakened)
    with pytest.raises(GcpProviderError, match="identity or absolute STOP policy changed"):
        driver._verify_registered_templates({"templates": {"vm_a": template, "vm_b": second}}, trigger)


def test_selected_topology_readback_checks_zone_addresses_disk_and_image(tmp_path, monkeypatch):
    from cloud_network_benchmark.contracts.common import Scenario
    from cloud_network_benchmark.providers.gcp import GcpProviderError
    deployment, driver = _cleanup_fixture(tmp_path)
    pinned = driver.settings.images[deployment.vm_a.region].rsplit("/", 1)[-1]
    monkeypatch.setattr(driver, "_gcloud_json", lambda _args, action: {"sizeGb": 32, "type": "zones/europe-west3-a/diskTypes/pd-balanced", "sourceImage": f"projects/ubuntu-os-cloud/global/images/{pinned}"} if action.startswith("gcp-boot-disk") else {"architecture": "X86_64", "name": pinned})

    scenarios = [
        (Scenario.SAME_ZONE, "europe-west3", "europe-west3-a", "10.80.1.11"),
        (Scenario.CROSS_ZONE, "europe-west3", "europe-west3-b", "10.80.2.10"),
        (Scenario.PLACEMENT_OPTIMIZATION, "europe-west3", "europe-west3-a", "10.80.1.11"),
        (Scenario.INTER_REGION, "europe-west2", "europe-west2-a", "10.81.1.11"),
    ]
    for scenario, region, zone, address in scenarios:
        vm_b = deployment.vm_b.model_copy(update={"region": region, "zone": zone})
        case = deployment.model_copy(update={"scenario": scenario, "vm_b": vm_b})
        instance = {"zone": f"https://compute.googleapis.com/compute/v1/projects/test/zones/{zone}", "machineType": "n2-standard-4", "networkInterfaces": [{"networkIP": address, "nicType": "GVNIC", "accessConfigs": [{"natIP": "203.0.113.10"}]}], "disks": [{"boot": True, "source": f"https://compute.googleapis.com/compute/v1/projects/test/zones/{zone}/disks/vm-b"}]}
        assert driver._validate_instance(instance, case, "vm_b")["sizeGb"] == 32
        wrong = {**instance, "networkInterfaces": [{**instance["networkInterfaces"][0], "networkIP": "10.99.0.1"}]}
        with pytest.raises(GcpProviderError, match="private or public management address"):
            driver._validate_instance(wrong, case, "vm_b")
        no_public = {**instance, "networkInterfaces": [{"networkIP": address, "nicType": "GVNIC", "accessConfigs": []}]}
        with pytest.raises(GcpProviderError, match="private or public management address"):
            driver._validate_instance(no_public, case, "vm_b")
    wrong_zone={**instance,"zone":"https://compute.googleapis.com/compute/v1/projects/test/zones/europe-west3-c"}
    with pytest.raises(GcpProviderError,match="differs from selected zone"):
        driver._validate_instance(wrong_zone,case,"vm_b")
    monkeypatch.setattr(driver,"_gcloud_json",lambda _args,action:{"sizeGb":32,"type":"pd-balanced","sourceImage":f"projects/other-project/global/images/{pinned}"} if action.startswith("gcp-boot-disk") else {"architecture":"X86_64","name":pinned})
    with pytest.raises(GcpProviderError,match="differs from selected pinned image"):
        driver._validate_instance(instance,case,"vm_b")
    monkeypatch.setattr(driver,"_gcloud_json",lambda _args,action:{"sizeGb":32,"type":"pd-balanced","sourceImage":f"projects/ubuntu-os-cloud/global/images/{pinned}"} if action.startswith("gcp-boot-disk") else {"name":pinned})
    with pytest.raises(GcpProviderError,match="not amd64"):
        driver._validate_instance(instance,case,"vm_b")


def test_both_role_observations_match_each_supported_scenario(tmp_path, monkeypatch):
    from cloud_network_benchmark.contracts.common import Scenario
    deployment, driver = _cleanup_fixture(tmp_path)
    cases = [
        (Scenario.SAME_ZONE, "europe-west3", "europe-west3-a", "europe-west3", "europe-west3-a", "10.80.1.10", "10.80.1.11"),
        (Scenario.CROSS_ZONE, "europe-west3", "europe-west3-a", "europe-west3", "europe-west3-b", "10.80.1.10", "10.80.2.10"),
        (Scenario.PLACEMENT_OPTIMIZATION, "europe-west3", "europe-west3-a", "europe-west3", "europe-west3-a", "10.80.1.10", "10.80.1.11"),
        (Scenario.INTER_REGION, "europe-west3", "europe-west3-a", "europe-west2", "europe-west2-a", "10.80.1.10", "10.81.1.11"),
    ]
    for scenario, region_a, zone_a, region_b, zone_b, ip_a, ip_b in cases:
        vm_a = deployment.vm_a.model_copy(update={"region": region_a, "zone": zone_a})
        vm_b = deployment.vm_b.model_copy(update={"region": region_b, "zone": zone_b})
        case = deployment.model_copy(update={"scenario": scenario, "vm_a": vm_a, "vm_b": vm_b})
        def readback(args, action):
            if action.startswith("gcp-boot-disk"):
                region = region_a if action.endswith("vm_a") else region_b
                image = driver.settings.images[region]
                zone = zone_a if action.endswith("vm_a") else zone_b
                return {"sizeGb": 32, "type": f"zones/{zone}/diskTypes/pd-balanced", "sourceImage": image}
            region = region_a if action.endswith("vm_a") else region_b
            return {"architecture": "X86_64", "name": driver.settings.images[region].rsplit("/", 1)[-1]}
        monkeypatch.setattr(driver, "_gcloud_json", readback)
        for role, zone, address in (("vm_a", zone_a, ip_a), ("vm_b", zone_b, ip_b)):
            instance = {
                "zone": f"https://compute.googleapis.com/compute/v1/projects/test/zones/{zone}",
                "machineType": "n2-standard-4",
                "networkInterfaces": [{"networkIP": address, "nicType": "GVNIC", "accessConfigs": [{"natIP": "8.8.8.8"}]}],
                "disks": [{"boot": True, "source": f"https://compute.googleapis.com/compute/v1/projects/test/zones/{zone}/disks/{role}"}],
            }
            observed = driver._validate_instance(instance, case, role)
            assert observed["sizeGb"] == 32
            assert observed["sourceImage"].endswith("ubuntu-2404-lts-amd64-v20261001")


def test_cleanup_preserves_primary_failure_when_pair_destroy_times_out(tmp_path, monkeypatch):
    from cloud_network_benchmark.contracts.common import FailureEvidence
    from cloud_network_benchmark.providers.gcp import GcpProviderError
    deployment, driver = _cleanup_fixture(tmp_path)
    attempt = driver._attempt_dir(deployment)
    primary = FailureEvidence(category="provision_failed", message="primary failure", occurred_at=datetime.now(timezone.utc))
    driver._save(attempt, {"run_id": deployment.run_id, "deployment_id": deployment.deployment_id, "creation_may_have_started": True, "primary_failure": primary.model_dump(mode="json")})
    def fail_pair(_attempt, root, action, _deployment, _extra):
        if root == "pair" and action == "destroy":
            raise GcpProviderError("pair destroy timed out", category="command_timeout")
        raise AssertionError("protection must remain while pair cleanup is unresolved")
    monkeypatch.setattr(driver, "_terraform", fail_pair)
    result = driver.cleanup(deployment)
    assert result.cleanup_state == "failed"
    assert result.primary_failure.message == "primary failure"
    assert result.cleanup_failure.category == "command_timeout"


def test_scenario_matrix_requires_review_for_cross_zone_compact_and_inter_region(tmp_path):
    from cloud_network_benchmark.contracts.common import Scenario, StructuredEvidence
    from cloud_network_benchmark.providers.gcp import GcpProviderError, GcpProviderDriver
    deployment, driver = _cleanup_fixture(tmp_path)
    cases = [deployment]
    cross_b = deployment.vm_b.model_copy(update={"zone": "europe-west3-b"})
    cases.append(deployment.model_copy(update={"scenario": Scenario.CROSS_ZONE, "vm_b": cross_b}))
    compact = deployment.model_copy(update={"scenario": Scenario.PLACEMENT_OPTIMIZATION, "placement": deployment.placement.model_copy(update={"kind": "compact_placement_policy", "collocated": True})})
    cases.append(compact)
    london_b = deployment.vm_b.model_copy(update={"region": "europe-west2", "zone": "europe-west2-a"})
    cases.append(deployment.model_copy(update={"scenario": Scenario.INTER_REGION, "vm_b": london_b}))
    for case in cases:
        assert GcpProviderDriver._validate_deployment(case) is None
        assert case.vm_a.network.tcp_congestion_control == "cubic"
        assert case.vm_a.network.dscp == 0 and case.vm_a.network.ecn is False and case.vm_a.network.default_offloads is True
    for case in cases[1:]:
        with pytest.raises(GcpProviderError, match="S2/S3/S4"):
            driver._validate_review(case)
    evidence = StructuredEvidence(value={"decision": "approved", "scenario": "placement_optimization", "source_path": "review.md", "source_sha256": "a" * 64, "reviewed_at": "2026-10-23T12:00:00Z"})
    reviewed_driver = GcpProviderDriver(object(), driver.settings.model_copy(update={"primary_review_decision": evidence}))
    assert reviewed_driver._validate_review(compact) is None


def test_s4_inventory_scans_both_regions_global_addresses_and_exact_known_ids(tmp_path, monkeypatch):
    import hashlib
    from cloud_network_benchmark.providers.gcp import GcpProviderDriver
    deployment, driver = _cleanup_fixture(tmp_path)
    london_b = deployment.vm_b.model_copy(update={"region": "europe-west2", "zone": "europe-west2-a"})
    deployment = deployment.model_copy(update={"scenario": "inter_region", "vm_b": london_b})
    namespace = driver._namespace(deployment)
    attempt = driver._attempt_dir(deployment)
    driver._save(attempt, {"run_id": deployment.run_id, "deployment_id": deployment.deployment_id, "observed_resources": {"vm_a": {"id": "101", "name": "known-a", "zone": "europe-west3-a"}}, "observed_disks": [{"id": "201", "name": "disk-a", "zone": "europe-west3-a"}], "templates": {"vm_a": {"id": "301", "name": "known-template"}}})
    foreign_label = {"cnb-deploy": hashlib.sha256(deployment.deployment_id.encode()).hexdigest()[:20]}
    responses = {
        "instances": [{"id": "101", "name": "known-a", "zone": "europe-west3-a"}, {"id": "101", "name": "reused-id-different-resource", "zone": "europe-west3-a"}, {"id": "999", "name": "foreign", "zone": "europe-west3-a", "labels": foreign_label}],
        "disks": [{"id": "201", "name": "disk-a", "zone": "europe-west3-a"}],
        "instance-templates": [{"id": "301", "name": "known-template"}],
        "networks": [{"id": "401", "name": f"{namespace}-net"}],
        "addresses": [{"id": "501", "name": f"{namespace}-address"}],
        "subnetworks": [], "firewall-rules": [], "routes": [], "resource-policies": [],
    }
    actions=[]
    def fake(args, action):
        actions.append(action)
        collection = next((name for name in responses if name in args), None)
        if collection in {"instances", "disks"}:
            zone = next((part.split("=", 1)[1] for part in args if part.startswith("--zones=")), None)
            return responses[collection] if zone is None else [item for item in responses[collection] if item.get("zone") == zone]
        return responses[collection] if collection else []
    monkeypatch.setattr(driver, "_gcloud_json", fake)
    inventory=driver._inventory(deployment)
    assert inventory["pages_complete"] is True
    assert inventory["zone_scope"] == "all_project_zones"
    assert "gcp-inventory-all-zones" in actions and "gcp-disk-inventory-all-zones" in actions
    assert "gcp-global-address-inventory" in actions
    assert "gcp-routes-inventory" in actions
    assert [item["id"] for item in inventory["instances"]] == ["101"]
    assert [item["id"] for item in inventory["disks"]] == ["201"]
    assert {item["name"] for item in inventory["support_resources"]} == {"known-template", f"{namespace}-net"}


def test_later_inventory_failure_keeps_protection_and_launch_blocked(tmp_path, monkeypatch):
    import json
    from cloud_network_benchmark.contracts.common import Evidence
    from cloud_network_benchmark.contracts.common import StructuredEvidence
    from cloud_network_benchmark.contracts.admission import ExpiryEvidence
    from cloud_network_benchmark.providers.gcp import GcpProviderError
    deployment, driver = _cleanup_fixture(tmp_path)
    now=datetime.now(timezone.utc)
    expiry=ExpiryEvidence(schema_version=2,run_id=deployment.run_id,deployment_id=deployment.deployment_id,provider="gcp",action="stop",state="registered",registration_id=Evidence[str](value="111:222"),registered_at=Evidence[datetime](value=now-timedelta(seconds=10)),expires_at=Evidence[datetime](value=now+timedelta(seconds=900)),target_selector="project=cnb-test-12345;roles=vm_a,vm_b",provider_metadata=StructuredEvidence(value={"native":True}))
    attempt=driver._attempt_dir(deployment)
    driver._save(attempt,{"run_id":deployment.run_id,"deployment_id":deployment.deployment_id,"creation_may_have_started":True,"registered":True,"expiry":expiry.model_dump(mode="json")})
    commands=[]
    monkeypatch.setattr(driver,"_terraform",lambda _attempt,root,action,_deployment,_extra: commands.append((root,action)) or {})
    def inventory(args, action):
        if action == "gcp-disk-inventory-all-zones":
            raise GcpProviderError("access denied while listing a later inventory scope", category="gcp_api_read")
        return []
    monkeypatch.setattr(driver,"_gcloud_json",inventory)
    result=driver.cleanup(deployment)
    assert result.cleanup_state == "failed"
    assert result.cleanup_failure.category == "gcp_api_read"
    assert commands == [("pair","destroy")]
    assert json.loads((attempt/"attempt.json").read_text())["launch_blocked"] is True


@pytest.mark.parametrize("failure_mode", ["apply_timeout", "output_missing", "interrupted"])
def test_post_create_failures_preserve_primary_and_attempt_pair_cleanup(tmp_path, monkeypatch, failure_mode):
    import json
    from cloud_network_benchmark.contracts.admission import ExpiryEvidence
    from cloud_network_benchmark.contracts.common import Evidence, StructuredEvidence
    from cloud_network_benchmark.providers.gcp import GcpProviderError
    deployment, driver = _cleanup_fixture(tmp_path)
    now=datetime.now(timezone.utc)
    expiry=ExpiryEvidence(schema_version=2,run_id=deployment.run_id,deployment_id=deployment.deployment_id,provider="gcp",action="stop",state="registered",registration_id=Evidence[str](value="111:222"),registered_at=Evidence[datetime](value=now-timedelta(seconds=10)),expires_at=Evidence[datetime](value=now+timedelta(seconds=900)),target_selector="project=cnb-test-12345;roles=vm_a,vm_b",provider_metadata=StructuredEvidence(value={"native_absolute_stop":True}))
    deployment=deployment.model_copy(update={"expiry":Evidence(value=expiry)})
    attempt=driver._attempt_dir(deployment)
    templates={"vm_a":{"id":"111","name":"template-a","self_link_unique":"projects/test/global/instanceTemplates/111"},"vm_b":{"id":"222","name":"template-b","self_link_unique":"projects/test/global/instanceTemplates/222"}}
    driver._save(attempt,{"run_id":deployment.run_id,"deployment_id":deployment.deployment_id,"registered":True,"expiry":expiry.model_dump(mode="json"),"templates":templates})
    monkeypatch.setattr(driver,"_verify_registered_templates",lambda *_args:None)
    events=[]
    def terraform(_attempt,root,action,_deployment,_extra):
        events.append((root,action))
        if root=="pair" and action=="apply":
            if failure_mode=="apply_timeout": raise GcpProviderError("pair apply timed out",category="command_timeout")
            if failure_mode=="interrupted": raise KeyboardInterrupt("controller interrupted during pair apply")
            return {}
        return {}
    monkeypatch.setattr(driver,"_terraform",terraform)
    monkeypatch.setattr(driver,"_gcloud_json",lambda _args,_action:[])
    with pytest.raises((GcpProviderError,KeyError,KeyboardInterrupt)):
        driver.provision(deployment)
    journal=json.loads((attempt/"attempt.json").read_text())
    assert journal["creation_may_have_started"] is True
    assert journal["primary_failure"]
    assert events==[("pair","apply"),("pair","destroy"),("protection","destroy")]


@pytest.mark.parametrize("collection,resource_key", [("disks","disks"),("addresses","addresses"),("instance_templates","support_resources")])
def test_residual_billable_or_protection_resource_keeps_cleanup_open(tmp_path,monkeypatch,collection,resource_key):
    import hashlib
    from cloud_network_benchmark.contracts.admission import ExpiryEvidence
    from cloud_network_benchmark.contracts.common import Evidence, StructuredEvidence
    deployment,driver=_cleanup_fixture(tmp_path)
    now=datetime.now(timezone.utc)
    expiry=ExpiryEvidence(schema_version=2,run_id=deployment.run_id,deployment_id=deployment.deployment_id,provider="gcp",action="stop",state="registered",registration_id=Evidence[str](value="111:222"),registered_at=Evidence[datetime](value=now-timedelta(seconds=10)),expires_at=Evidence[datetime](value=now+timedelta(seconds=900)),target_selector="project=cnb-test-12345;roles=vm_a,vm_b",provider_metadata=StructuredEvidence(value={"native_absolute_stop":True}))
    attempt=driver._attempt_dir(deployment)
    driver._save(attempt,{"run_id":deployment.run_id,"deployment_id":deployment.deployment_id,"registered":True,"expiry":expiry.model_dump(mode="json"),"templates":{"vm_a":{"id":"111","name":"template-a"}}})
    namespace=driver._namespace(deployment)
    owned={"id":"77","name":f"{namespace}-leftover","zone":"europe-west3-a","labels":{"cnb-run":hashlib.sha256(deployment.run_id.encode()).hexdigest()[:20],"cnb-deploy":hashlib.sha256(deployment.deployment_id.encode()).hexdigest()[:20]}}
    commands=[]
    monkeypatch.setattr(driver,"_terraform",lambda _attempt,root,action,_deployment,_extra:commands.append((root,action)) or {})
    def fake(args,action):
        if action=="gcp-instance_templates-inventory" and collection=="instance_templates": return [owned]
        if action=="gcp-disk-inventory-all-zones" and collection=="disks": return [owned]
        if action=="gcp-address-inventory-europe-west3" and collection=="addresses": return [owned]
        return []
    monkeypatch.setattr(driver,"_gcloud_json",fake)
    result=driver.cleanup(deployment)
    assert result.cleanup_state=="failed"
    assert result.cleanup_failure.category=="cleanup_incomplete"
    assert result.inventory.value[resource_key]
    assert commands==[("protection","destroy")]


def test_failed_cleanup_can_retry_from_original_state_without_relaunch(tmp_path,monkeypatch):
    import json
    from cloud_network_benchmark.contracts.admission import ExpiryEvidence
    from cloud_network_benchmark.contracts.common import Evidence,StructuredEvidence
    deployment,driver=_cleanup_fixture(tmp_path)
    now=datetime.now(timezone.utc)
    expiry=ExpiryEvidence(schema_version=2,run_id=deployment.run_id,deployment_id=deployment.deployment_id,provider="gcp",action="stop",state="registered",registration_id=Evidence[str](value="111:222"),registered_at=Evidence[datetime](value=now-timedelta(seconds=10)),expires_at=Evidence[datetime](value=now+timedelta(seconds=900)),target_selector="project=cnb-test-12345;roles=vm_a,vm_b",provider_metadata=StructuredEvidence(value={"native_absolute_stop":True}))
    attempt=driver._attempt_dir(deployment)
    driver._save(attempt,{"run_id":deployment.run_id,"deployment_id":deployment.deployment_id,"registered":True,"expiry":expiry.model_dump(mode="json")})
    state={"fail":True,"count":0};calls=[]
    monkeypatch.setattr(driver,"_terraform",lambda _attempt,root,action,_deployment,_extra:calls.append((root,action)) or {})
    def inventory(_deployment):
        state["count"]+=1
        if state["fail"] and state["count"]==2:raise GcpProviderError("inventory access denied",category="gcp_api_read")
        return {"pages_complete":True,"zone_scope":"all_project_zones","instances":[],"disks":[],"addresses":[],"support_resources":[],"resources":[],"regions_scanned":[]}
    from cloud_network_benchmark.providers.gcp import GcpProviderError
    monkeypatch.setattr(driver,"_inventory",inventory)
    first=driver.cleanup(deployment)
    assert first.cleanup_state=="failed"
    assert json.loads((attempt/"attempt.json").read_text())["launch_blocked"] is True
    state["fail"]=False
    second=driver.cleanup(deployment)
    assert second.cleanup_state=="succeeded"
    assert calls==[("protection","destroy"),("protection","destroy")]
