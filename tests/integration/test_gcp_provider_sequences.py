from datetime import datetime, timedelta, timezone

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
