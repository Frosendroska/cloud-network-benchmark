from datetime import datetime, timedelta, timezone
import pytest

pytestmark = pytest.mark.usefixtures("isolated_legacy_saved_plan_boundary")

from cloud_network_benchmark.providers.gcp import CONTROLLER_SESSION_ID, GcpProviderDriver, GcpProviderError, SAFETY_LEAD_SECONDS
from cloud_network_benchmark.contracts.admission import ReservationEvidence
from cloud_network_benchmark.contracts.common import Evidence, StringEvidence


def _template_response(driver, deployment, role, template_id, trigger, created_at):
    import hashlib, json
    namespace = driver._namespace(deployment)
    intent = deployment.vm_a if role == "vm_a" else deployment.vm_b
    suffix = "a" if role == "vm_a" else "b"
    scenario = getattr(deployment.scenario, "value", deployment.scenario)
    address = ("10.80.1.10", "10.80.1.11") if scenario != "inter_region" else ("10.80.1.10", "10.81.1.11")
    ip = address[0 if role == "vm_a" else 1]
    metadata = {
        "ssh-keys": f"benchmark:{driver.settings.public_key}", "enable-guest-attributes": "TRUE",
        "enable-oslogin": "FALSE", "block-project-ssh-keys": "TRUE",
        "cnb-bootstrap-template": deployment.bootstrap_template,
        "cnb-bootstrap-inputs": json.dumps(deployment.bootstrap_inputs, sort_keys=True, separators=(",", ":")),
        "cnb-network-intent": json.dumps(deployment.vm_a.network.model_dump(mode="json"), sort_keys=True, separators=(",", ":")),
    }
    return {
        "id": str(template_id), "name": f"{namespace}-{suffix}", "selfLink": f"https://www.googleapis.com/compute/v1/projects/{driver.settings.project_id}/global/instanceTemplates/{namespace}-{suffix}", "creationTimestamp": created_at.isoformat().replace("+00:00", "Z"),
        "properties": {
            "machineType": "n2-standard-4", "labels": {"cnb-run": hashlib.sha256(deployment.run_id.encode()).hexdigest()[:20], "cnb-deploy": hashlib.sha256(deployment.deployment_id.encode()).hexdigest()[:20]},
            "scheduling": {"provisioningModel": "STANDARD", "instanceTerminationAction": "STOP", "terminationTime": trigger.isoformat().replace("+00:00", "Z"), "automaticRestart": False},
            "networkInterfaces": [{"network": f"https://www.googleapis.com/compute/v1/projects/{driver.settings.project_id}/global/networks/{namespace}-net", "subnetwork": f"https://www.googleapis.com/compute/v1/projects/{driver.settings.project_id}/regions/{intent.region}/subnetworks/{namespace}-" + ("b" if role == "vm_b" and scenario == "inter_region" else "a"), "networkIP": ip, "nicType": "GVNIC", "accessConfigs": [{}]}],
            "disks": [{"boot": True, "autoDelete": True, "initializeParams": {"sourceImage": driver.settings.images[intent.region], "diskType": "pd-balanced", "diskSizeGb": "32", "diskName": f"{namespace}-{suffix}-boot", "labels": {"cnb-run": hashlib.sha256(deployment.run_id.encode()).hexdigest()[:20], "cnb-deploy": hashlib.sha256(deployment.deployment_id.encode()).hexdigest()[:20]}}}],
            "metadata": {"items": [{"key": key, "value": value} for key, value in sorted(metadata.items())]},
            "reservationAffinity": {"consumeReservationType": "NO_RESERVATION"}, "resourcePolicies": [],
        },
    }


def _instance_response(driver, deployment, role):
    intent = deployment.vm_a if role == "vm_a" else deployment.vm_b
    suffix = "a" if role == "vm_a" else "b"
    name = f"{driver._namespace(deployment)}-{suffix}"
    addresses = {"same_zone": ("10.80.1.10", "10.80.1.11"), "cross_zone": ("10.80.1.10", "10.80.1.11"), "placement_optimization": ("10.80.1.10", "10.80.1.11"), "inter_region": ("10.80.1.10", "10.81.1.11")}
    ip = addresses[deployment.scenario][0 if role == "vm_a" else 1]
    subnet_role = "b" if role == "vm_b" and deployment.scenario == "inter_region" else "a"
    base = f"https://www.googleapis.com/compute/v1/projects/{driver.settings.project_id}"
    return {"id": "73101" if role == "vm_a" else "73102", "name": name, "selfLink": f"{base}/zones/{intent.zone}/instances/{name}", "zone": f"{base}/zones/{intent.zone}", "machineType": "n2-standard-4", "reservationAffinity": {"consumeReservationType": "NO_RESERVATION"}, "scheduling": {"automaticRestart": False}, "networkInterfaces": [{"network": f"{base}/global/networks/{driver._namespace(deployment)}-net", "networkIP": ip, "subnetwork": f"{base}/regions/{intent.region}/subnetworks/{driver._namespace(deployment)}-{subnet_role}", "nicType": "GVNIC", "accessConfigs": [{"natIP": "8.8.8.8"}]}], "disks": [{"boot": True, "source": f"{base}/zones/{intent.zone}/disks/{driver._namespace(deployment)}-{suffix}-boot"}]}
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


def test_provider_rejects_noncanonical_scenario_zones(tmp_path):
    from cloud_network_benchmark.providers.gcp import GcpProviderError
    deployment, _driver = _cleanup_fixture(tmp_path)
    for scenario, zone_a, zone_b in (("same_zone", "europe-west3-c", "europe-west3-c"), ("cross_zone", "europe-west3-c", "europe-west3-b"), ("placement_optimization", "europe-west3-a", "europe-west3-c"), ("inter_region", "europe-west3-a", "europe-west2-b")):
        a=deployment.vm_a.model_copy(update={"zone":zone_a})
        b=deployment.vm_b.model_copy(update={"zone":zone_b, "region":"europe-west2" if scenario=="inter_region" else "europe-west3"})
        case=deployment.model_copy(update={"scenario":scenario,"vm_a":a,"vm_b":b})
        with pytest.raises(GcpProviderError, match="selected V2 topology"):
            GcpProviderDriver._validate_deployment(case)


def test_finland_provider_scope_rejects_non_acceptance_identity_before_commands(tmp_path):
    from cloud_network_benchmark.providers.gcp import GcpProviderError
    deployment, _ = _cleanup_fixture(tmp_path)
    deployment = deployment.model_copy(update={
        "vm_a": deployment.vm_a.model_copy(update={"region": "europe-north1", "zone": "europe-north1-a"}),
        "vm_b": deployment.vm_b.model_copy(update={"region": "europe-north1", "zone": "europe-north1-a"}),
    })
    GcpProviderDriver._validate_deployment(deployment)
    for changed in (deployment.model_copy(update={"experiment_id": "EXP-999"}),
                    deployment.model_copy(update={"context": "standard"}),
                    deployment.model_copy(update={"mode": "short_study"})):
        with pytest.raises(GcpProviderError, match="Finland is limited"):
            GcpProviderDriver._validate_deployment(changed)
    for zone_a, zone_b in (("europe-north1-a", "europe-north1-b"),
                            ("europe-north1-b", "europe-north1-c"),
                            ("europe-north1-c", "europe-north1-a"),
                            ("europe-north1-d", "europe-north1-d")):
        changed = deployment.model_copy(update={
            "vm_a": deployment.vm_a.model_copy(update={"zone": zone_a}),
            "vm_b": deployment.vm_b.model_copy(update={"zone": zone_b}),
        })
        with pytest.raises(GcpProviderError, match="Finland is limited|selected Frankfurt/London"):
            GcpProviderDriver._validate_deployment(changed)
    wrong_region = deployment.model_copy(update={
        "vm_a": deployment.vm_a.model_copy(update={"region": "europe-west3", "zone": "europe-west3-a"}),
    })
    with pytest.raises(GcpProviderError, match="selected Frankfurt/London"):
        GcpProviderDriver._validate_deployment(wrong_region)


def test_incomplete_gcloud_inventory_warning_and_malformed_response_fail_closed(tmp_path):
    import json
    from cloud_network_benchmark.contracts.execution import CommandResult
    from cloud_network_benchmark.providers.gcp import GcpProviderError
    deployment, base = _cleanup_fixture(tmp_path)
    class Runner:
        def __init__(self, stdout, stderr=""): self.stdout,self.stderr=stdout,stderr
        def run(self, request):
            now=datetime.now(timezone.utc)
            return CommandResult(action_id=request.action_id,outcome="succeeded",started_at=now,finished_at=now,duration_seconds=0,exit_code=0,stdout=self.stdout,stderr=self.stderr)
    good=GcpProviderDriver(Runner("[]"),base.settings)
    assert good._gcloud_json(["compute","instances","list","--project",base.settings.project_id,"--format=json"],"gcp-inventory-all-zones")==[]
    warned=GcpProviderDriver(Runner("[]","WARNING: list incomplete because zone scope was denied"),base.settings)
    with pytest.raises(GcpProviderError,match="completeness warning"):
        warned._gcloud_json(["compute","instances","list","--project",base.settings.project_id,"--format=json"],"gcp-inventory-all-zones")
    malformed=GcpProviderDriver(Runner("{bad json"),base.settings)
    with pytest.raises(GcpProviderError,match="malformed"):
        malformed._gcloud_json(["compute","instances","list","--project",base.settings.project_id,"--format=json"],"gcp-inventory-all-zones")


def test_interrupted_registration_blocks_until_cleanup_is_proven(tmp_path):
    import json
    from cloud_network_benchmark.providers.gcp import GcpProviderError
    deployment,driver=_cleanup_fixture(tmp_path)
    other=driver._attempt_dir(deployment.model_copy(update={"run_id":"other-gcp-run","deployment_id":"other-gcp-deployment"}))
    driver._save(other,{"run_id":"other-gcp-run","deployment_id":"other-gcp-deployment","registration_may_have_started":True,"registered":False,"controller_session_id":"previous-controller"})
    with pytest.raises(GcpProviderError,match="interrupted registration"):
        driver._assert_no_open_cleanup(deployment)
    state=json.loads((other/"attempt.json").read_text())
    state["protection_cleanup_state"]="succeeded"
    driver._save(other,state)
    assert driver._assert_no_open_cleanup(deployment) is None


def test_unlabeled_canonical_orphan_boot_disk_keeps_cleanup_inventory_open(tmp_path,monkeypatch):
    deployment,driver=_cleanup_fixture(tmp_path)
    namespace=driver._namespace(deployment)
    def listing(args,action):
        if args[:3]==["compute","disks","list"]:
            return [{"id":"901","name":f"{namespace}-a-boot","zone":"europe-west3-a","labels":{}}]
        return []
    monkeypatch.setattr(driver,"_gcloud_json",listing)
    result=driver._inventory(deployment)
    assert result["pages_complete"] is True
    assert [disk["name"] for disk in result["disks"]]==[f"{namespace}-a-boot"]
    assert result["resources"]


def test_fit_is_rechecked_after_template_prereads_before_pair_apply(tmp_path,monkeypatch):
    import json
    from cloud_network_benchmark.contracts.admission import ExpiryEvidence
    from cloud_network_benchmark.contracts.common import Evidence,StructuredEvidence
    from cloud_network_benchmark.providers.gcp import GcpProviderError
    deployment,driver=_cleanup_fixture(tmp_path)
    start=datetime.now(timezone.utc)
    expiry_at=deployment.reservation.value.expires_at.value-timedelta(seconds=SAFETY_LEAD_SECONDS)
    expiry=ExpiryEvidence(schema_version=2,run_id=deployment.run_id,deployment_id=deployment.deployment_id,provider="gcp",action="stop",state="registered",registration_id=Evidence[str](value="111:222"),registered_at=Evidence[datetime](value=start-timedelta(seconds=2)),expires_at=Evidence[datetime](value=expiry_at),target_selector="project=cnb-test-12345;roles=vm_a,vm_b",provider_metadata=StructuredEvidence(value={"native_absolute_stop":True}))
    deployment=deployment.model_copy(update={"expiry":Evidence(value=expiry)})
    attempt=driver._attempt_dir(deployment)
    namespace=driver._namespace(deployment)
    templates={"vm_a":{"id":"111","name":"template-a","self_link_unique":"projects/test/global/instanceTemplates/unique-a"},"vm_b":{"id":"222","name":"template-b","self_link_unique":"projects/test/global/instanceTemplates/unique-b"}}
    driver._save(attempt,{"run_id":deployment.run_id,"deployment_id":deployment.deployment_id,"registered":True,"controller_session_id":CONTROLLER_SESSION_ID,"expiry":expiry.model_dump(mode="json"),"templates":templates,"binding_sha256":driver._binding_fingerprint(deployment),"reservation_id":deployment.reservation.value.reservation_id.value})
    clock=[start]
    driver.clock=lambda:clock[0]
    monkeypatch.setattr(driver,"_verify_registered_templates",lambda *_args:clock.__setitem__(0,expiry_at-timedelta(seconds=100)))
    calls=[]
    monkeypatch.setattr(driver,"_terraform",lambda _attempt,root,action,*_args:calls.append((root,action)) or {})
    with pytest.raises(GcpProviderError,match="whole remaining workflow"):
        driver.provision(deployment)
    journal=json.loads((attempt/"attempt.json").read_text())
    assert not journal.get("creation_may_have_started")
    assert calls==[]


@pytest.mark.parametrize("remaining_after_prereads", [1062.477, 879.99])
@pytest.mark.parametrize("provision_timeout", [180, None])
def test_pair_apply_deadline_debits_registration_once_and_keeps_downstream_reserve(
    tmp_path, monkeypatch, remaining_after_prereads, provision_timeout
):
    """Reproduce 107.523s elapsed: 99.772s registration + 7.708s template reads."""
    from cloud_network_benchmark.contracts.execution import CommandResult
    from cloud_network_benchmark.providers import gcp as gcp_module

    deployment, driver = _cleanup_fixture(tmp_path)
    deployment = deployment.model_copy(update={"provisioning_timeout_seconds": provision_timeout})
    reservation = deployment.reservation.value
    trigger = reservation.expires_at.value - timedelta(seconds=SAFETY_LEAD_SECONDS)
    # Match the packet: 1,150s complete allowance = 90s registration +
    # 1,060s after registration, including a 180s provision stage.
    settings = driver.settings.model_copy(update={
        "complete_workflow_remaining_seconds": 1150.0,
        "registration_workflow_seconds": 90.0,
        "post_registration_workflow_seconds": 1060.0,
    })
    driver = GcpProviderDriver(object(), settings, clock=lambda: utc_now[0])
    utc_now = [trigger - timedelta(seconds=remaining_after_prereads)]
    if remaining_after_prereads >= 1060.0:
        driver._fit(reservation, trigger, 1060.0)
    attempt = driver._attempt_dir(deployment)
    driver._active_attempt = attempt
    driver._save(attempt, {"run_id": deployment.run_id, "deployment_id": deployment.deployment_id})
    driver._deadline = 230.0  # absolute 180s provision deadline from monotonic 50
    monotonic_now = [50.0]
    monkeypatch.setattr(gcp_module.time, "monotonic", lambda: monotonic_now[0])
    settings = settings.model_copy(update={"command_timeout_seconds": 300})
    driver.settings = settings
    monkeypatch.setattr(
        "cloud_network_benchmark.terraform_work.initialize_terraform",
        lambda *_args: monotonic_now.__setitem__(0, monotonic_now[0] + 2.0),
    )
    review = []
    def reviewed(request, execute, *, review_seconds):
        review.append((request, review_seconds))
        monotonic_now[0] += 3.0
        assert driver._deadline - monotonic_now[0] == pytest.approx(175.0)
        return execute(request.model_copy(update={"timeout_seconds": request.timeout_seconds - 3.0}))
    monkeypatch.setattr("cloud_network_benchmark.terraform_plan.reviewed_apply", reviewed)

    class Runner:
        def run(self, request):
            now = datetime.now(timezone.utc)
            stdout = '{"instances":{}}' if request.action_id == "gcp-pair-outputs" else ""
            return CommandResult(
                action_id=request.action_id, outcome="succeeded", started_at=now,
                finished_at=now, duration_seconds=0, exit_code=0, stdout=stdout, stderr="",
            )

    driver.runner = Runner()
    if remaining_after_prereads < 880.0:
        with pytest.raises(GcpProviderError, match="absolute GCP command deadline elapsed"):
            driver._terraform(attempt, "pair", "apply", deployment,
                {"template_a": "template-a", "template_b": "template-b"})
        assert not review
        return

    # At the incident's 1,062.477s remaining, reserving 880s downstream
    # leaves 182.477s. The original provision deadline at monotonic 230 binds.
    driver._terraform(attempt, "pair", "apply", deployment,
        {"template_a": "template-a", "template_b": "template-b"})
    assert len(review) == 1
    assert review[0][0].timeout_seconds == pytest.approx(178.0)
    assert driver._deadline == pytest.approx(230.0)


def test_cost_undercoverage_and_changed_cap_are_rejected_before_provider_side_effects(tmp_path):
    from cloud_network_benchmark.contracts.admission import ExpiryEvidence, ExpiryRequest
    from cloud_network_benchmark.contracts.common import Evidence, StructuredEvidence
    from cloud_network_benchmark.providers.gcp import GcpProviderError
    deployment,driver=_cleanup_fixture(tmp_path)
    trigger=deployment.reservation.value.expires_at.value-timedelta(seconds=SAFETY_LEAD_SECONDS)
    request=ExpiryRequest(schema_version=2,run_id=deployment.run_id,deployment_id=deployment.deployment_id,provider="gcp",action="stop",expires_at=Evidence[datetime](value=trigger),reservation_id=deployment.reservation.value.reservation_id,resource_namespace=deployment.run_id,target_selector=f"project={driver.settings.project_id};run_id={deployment.run_id};deployment_id={deployment.deployment_id};roles=vm_a,vm_b")
    under=deployment.reservation.value.model_copy(update={"reserved_cost_usd":Evidence[float](value=4.99)})
    with pytest.raises(GcpProviderError,match="does not cover"):
        driver._validate(deployment.model_copy(update={"reservation":Evidence(value=under)}),request)
    wrong_selector=request.model_copy(update={"target_selector":f"project=x{driver.settings.project_id};run_id={deployment.run_id};deployment_id={deployment.deployment_id};roles=vm_a,vm_b"})
    with pytest.raises(GcpProviderError,match="target must bind"):
        driver._validate(deployment,wrong_selector)
    changed_cap=deployment.reservation.value.model_copy(update={"cap_started_at":Evidence[datetime](value=deployment.reservation.value.cap_started_at.value+timedelta(seconds=1))})
    with pytest.raises(GcpProviderError,match="cap duration"):
        driver._validate(deployment.model_copy(update={"reservation":Evidence(value=changed_cap)}),request)
    changed_campaign=deployment.reservation.value.model_copy(update={"campaign_id":"other-campaign"})
    with pytest.raises(GcpProviderError,match="campaign"):
        driver._validate(deployment.model_copy(update={"reservation":Evidence(value=changed_campaign)}),request)
    expiry=ExpiryEvidence(schema_version=2,run_id=deployment.run_id,deployment_id=deployment.deployment_id,provider="gcp",action="stop",state="registered",registration_id=Evidence[str](value="111:222"),registered_at=Evidence[datetime](value=datetime.now(timezone.utc)),expires_at=Evidence[datetime](value=trigger),target_selector=request.target_selector,provider_metadata=StructuredEvidence(value={"native_absolute_stop":True}))
    deployment=deployment.model_copy(update={"expiry":Evidence(value=expiry)})
    attempt=driver._attempt_dir(deployment)
    driver._save(attempt,{"run_id":deployment.run_id,"deployment_id":deployment.deployment_id,"registered":True,"controller_session_id":CONTROLLER_SESSION_ID,"expiry":expiry.model_dump(mode="json"),"templates":{"vm_a":{"id":"111","name":"template-a","self_link_unique":"projects/test/global/instanceTemplates/unique-a"},"vm_b":{"id":"222","name":"template-b","self_link_unique":"projects/test/global/instanceTemplates/unique-b"}},"binding_sha256":driver._binding_fingerprint(deployment),"reservation_id":deployment.reservation.value.reservation_id.value})
    changed=deployment.reservation.value.model_copy(update={"expires_at":Evidence[datetime](value=deployment.reservation.value.expires_at.value+timedelta(minutes=5))})
    changed_deployment=deployment.model_copy(update={"reservation":Evidence(value=changed)})
    with pytest.raises(GcpProviderError,match="immutable reserved registration"):
        driver.provision(changed_deployment)


@pytest.mark.parametrize("change", ["project", "cost_source"])
def test_changed_project_or_cost_source_cannot_recover_registered_attempt(tmp_path,change):
    from cloud_network_benchmark.contracts.admission import ExpiryEvidence
    from cloud_network_benchmark.contracts.common import Evidence,StructuredEvidence
    from cloud_network_benchmark.providers.gcp import GcpProviderError
    deployment,driver=_cleanup_fixture(tmp_path)
    trigger=deployment.reservation.value.expires_at.value-timedelta(seconds=SAFETY_LEAD_SECONDS)
    expiry=ExpiryEvidence(schema_version=2,run_id=deployment.run_id,deployment_id=deployment.deployment_id,provider="gcp",action="stop",state="registered",registration_id=Evidence[str](value="111:222"),registered_at=Evidence[datetime](value=datetime.now(timezone.utc)),expires_at=Evidence[datetime](value=trigger),target_selector="project=cnb-test-12345;roles=vm_a,vm_b",provider_metadata=StructuredEvidence(value={"native_absolute_stop":True}))
    deployment=deployment.model_copy(update={"expiry":Evidence(value=expiry)})
    attempt=driver._attempt_dir(deployment)
    driver._save(attempt,{"run_id":deployment.run_id,"deployment_id":deployment.deployment_id,"registered":True,"controller_session_id":CONTROLLER_SESSION_ID,"expiry":expiry.model_dump(mode="json"),"templates":{"vm_a":{"id":"111"},"vm_b":{"id":"222"}},"binding_sha256":driver._binding_fingerprint(deployment),"reservation_id":deployment.reservation.value.reservation_id.value})
    settings=driver.settings.model_copy(update={"project_id":"cnb-other-project-12345"} if change=="project" else {"cost_input_sha256":"b"*64,"cost_source_path":"different-register.csv"})
    recovered=GcpProviderDriver(object(),settings)
    with pytest.raises(GcpProviderError,match="immutable reserved registration"):
        recovered.provision(deployment)


def test_registration_keyboard_interrupt_records_cleanup_and_keeps_attempt_nonrepeatable(tmp_path,monkeypatch):
    import json
    from cloud_network_benchmark.contracts.admission import ExpiryRequest
    from cloud_network_benchmark.contracts.common import Evidence
    deployment,driver=_cleanup_fixture(tmp_path)
    trigger=deployment.reservation.value.expires_at.value-timedelta(seconds=SAFETY_LEAD_SECONDS)
    request=ExpiryRequest(schema_version=2,run_id=deployment.run_id,deployment_id=deployment.deployment_id,provider="gcp",action="stop",expires_at=Evidence[datetime](value=trigger),reservation_id=deployment.reservation.value.reservation_id,resource_namespace=deployment.run_id,target_selector=f"project={driver.settings.project_id};run_id={deployment.run_id};deployment_id={deployment.deployment_id};roles=vm_a,vm_b")
    actions=[]
    def terraform(_attempt,_root,action,*_args):
        actions.append(action)
        if action=="apply": raise KeyboardInterrupt("registration interrupted")
        return {}
    monkeypatch.setattr(driver,"_terraform",terraform)
    monkeypatch.setattr(driver,"_inventory",lambda *_args:{"pages_complete":True,"resources":[],"instances":[],"disks":[],"addresses":[],"support_resources":[]})
    with pytest.raises(KeyboardInterrupt):
        driver.register_expiry(deployment,request)
    journal=json.loads((driver._attempt_dir(deployment)/"attempt.json").read_text())
    assert journal["registration_may_have_started"] and journal["protection_cleanup_state"]=="succeeded"
    assert journal["registration_failure"] and actions==["apply","destroy"]
    with pytest.raises(Exception,match="cannot be restarted"):
        driver.register_expiry(deployment,request)


def test_success_and_repeated_cleanup_keep_saved_primary_failure(tmp_path,monkeypatch):
    import json
    from cloud_network_benchmark.contracts.common import FailureEvidence
    deployment,driver=_cleanup_fixture(tmp_path)
    attempt=driver._attempt_dir(deployment)
    primary=FailureEvidence(category="pair_failed",message="original pair failure",occurred_at=datetime.now(timezone.utc))
    driver._save(attempt,{"run_id":deployment.run_id,"deployment_id":deployment.deployment_id,"primary_failure":primary.model_dump(mode="json")})
    monkeypatch.setattr(driver,"_gcloud_json",lambda *_args:[])
    first=driver.cleanup(deployment)
    second=driver.cleanup(deployment)
    assert first.cleanup_state==second.cleanup_state=="succeeded"
    assert first.primary_failure.message==second.primary_failure.message=="original pair failure"
    assert json.loads((attempt/"attempt.json").read_text())["primary_failure"]["message"]=="original pair failure"


@pytest.mark.parametrize(("scenario", "policy_mutation", "north_zone"), [
    ("same_zone", None, None), ("cross_zone", None, None),
    ("placement_optimization", None, None), ("inter_region", None, None),
    ("placement_optimization", "extra", None), ("placement_optimization", "duplicate", None),
    ("placement_optimization", "missing", None), ("placement_optimization", "foreign", None),
    ("placement_optimization", "mismatched", None),
    *(('same_zone', None, zone) for zone in ("europe-north1-a", "europe-north1-b", "europe-north1-c")),
])
def test_full_fake_register_provision_trust_cleanup_trace_for_each_scenario(tmp_path, scenario, policy_mutation, north_zone):
    import base64, hashlib, json, struct
    from cloud_network_benchmark.contracts.common import Evidence, StructuredEvidence
    from cloud_network_benchmark.contracts.execution import CommandResult
    from cloud_network_benchmark.providers.gcp import GcpProviderDriver

    base, fixture_driver = _cleanup_fixture(tmp_path)
    north = north_zone is not None
    if north:
        north_network = base.vm_a.network.model_copy(update={
            "address_spaces": {"europe-north1": "10.80.1.0/24"},
            "subnets": {"europe-north1": ["10.80.1.0/24"]},
        })
        base = base.model_copy(update={
            "vm_a": base.vm_a.model_copy(update={"region": "europe-north1", "zone": north_zone, "network": north_network}),
            "vm_b": base.vm_b.model_copy(update={"region": "europe-north1", "zone": north_zone, "network": north_network}),
        })
    vm_b = base.vm_b
    placement = base.placement
    if scenario == "cross_zone":
        vm_b = vm_b.model_copy(update={"zone": "europe-west3-b"})
    elif scenario == "placement_optimization":
        placement = placement.model_copy(update={"kind": "compact_placement_policy", "collocated": True})
    elif scenario == "inter_region":
        vm_b = vm_b.model_copy(update={"region": "europe-west2", "zone": "europe-west2-a"})
    deployment = base.model_copy(update={"scenario": scenario, "vm_b": vm_b, "placement": placement})
    settings = fixture_driver.settings
    if north:
        settings = settings.model_copy(update={"images": {**settings.images,
            "europe-north1": "projects/ubuntu-os-cloud/global/images/ubuntu-2404-noble-amd64-v20261105"}})
    if scenario in {"cross_zone", "inter_region"}:
        settings = settings.model_copy(update={"primary_review_decision": StructuredEvidence(value={"decision":"approved", "scenario":scenario, "source_path":"review.md", "source_sha256":"a"*64, "reviewed_at":"2026-10-23T12:00:00Z"})})
    now = datetime.now(timezone.utc)
    trigger = deployment.reservation.value.expires_at.value-timedelta(seconds=SAFETY_LEAD_SECONDS)
    namespace = f"gcp-{deployment.run_id[:25]}-{hashlib.sha256(deployment.deployment_id.encode()).hexdigest()[:8]}"
    policy_id = f"projects/{settings.project_id}/regions/europe-west3/resourcePolicies/{namespace}-compact" if scenario == "placement_optimization" else None
    host_blob = base64.b64encode(struct.pack(">I", 11)+b"ssh-ed25519"+struct.pack(">I", 32)+b"h"*32).decode()
    class FakeRunner:
        def __init__(self): self.requests=[]
        def run(self, request):
            self.requests.append(request)
            action=request.action_id
            started=datetime.now(timezone.utc)
            def result(value="", code=0):
                return CommandResult(action_id=action,outcome="succeeded",started_at=started,finished_at=started,duration_seconds=0.0,exit_code=code,stdout=value if isinstance(value,str) else json.dumps(value),stderr="")
            if action.startswith("terraform-init-"):
                assert "init" in request.argv and "-backend=false" in request.argv
                assert request.environment["TF_DATA_DIR"]
                return result()
            if action == "gcp-protection-apply": return result()
            if action == "gcp-protection-outputs":
                templates={role:{"id":str(111 if role=="vm_a" else 222),"name":f"{namespace}-{'a' if role=='vm_a' else 'b'}","self_link_unique":f"https://www.googleapis.com/compute/v1/projects/{settings.project_id}/global/instanceTemplates/{namespace}-{'a' if role=='vm_a' else 'b'}?uniqueId={111 if role=='vm_a' else 222}"} for role in ("vm_a","vm_b")}
                return result({"templates":{"value":templates},"compact_policy_id":{"value":policy_id}})
            if action == "gcp-pair-apply": return result()
            if action == "gcp-pair-outputs":
                return result({"instances":{"value":{role:{"id":str(1001 if role=="vm_a" else 1002),"name":f"{namespace}-{'a' if role=='vm_a' else 'b'}","zone":deployment.vm_a.zone if role=="vm_a" else deployment.vm_b.zone,"self_link":f"projects/{settings.project_id}/zones/{deployment.vm_a.zone if role=='vm_a' else deployment.vm_b.zone}/instances/{namespace}-{'a' if role=='vm_a' else 'b'}"} for role in ("vm_a","vm_b")}}})
            if action in {"gcp-pair-destroy", "gcp-protection-destroy"}: return result()
            if "inventory" in action: return result([])
            role = "vm_a" if action.endswith("vm_a") or f"{namespace}-a" in request.argv else "vm_b"
            intent = deployment.vm_a if role=="vm_a" else deployment.vm_b
            suffix = "a" if role=="vm_a" else "b"
            vm_name = f"{namespace}-{suffix}"
            zone_path=f"https://www.googleapis.com/compute/v1/projects/{settings.project_id}/zones/{intent.zone}"
            address=("10.80.1.10" if role=="vm_a" else "10.80.1.11") if scenario!="inter_region" else ("10.80.1.10" if role=="vm_a" else "10.81.1.11")
            public="8.8.8.8" if role=="vm_a" else "1.1.1.1"
            identity={"id":str(1001 if role=="vm_a" else 1002),"name":vm_name,"zone":zone_path,"selfLink":f"https://www.googleapis.com/compute/v1/projects/{settings.project_id}/zones/{intent.zone}/instances/{vm_name}","networkInterfaces":[{"networkIP":address,"accessConfigs":[{"natIP":public}]}]}
            if action.startswith("gcp-template-read") or action.startswith("gcp-template-precreate"):
                template_id="111" if role=="vm_a" else "222"
                template=_template_response(GcpProviderDriver(self,settings,clock=lambda:now),deployment,role,template_id,trigger,now-timedelta(seconds=1))
                if policy_id: template["properties"]["resourcePolicies"]=[policy_id]
                return result(template)
            if action.startswith("gcp-instance-read") or action.startswith("gcp-hostkey-instance"):
                instance={**identity,"machineType":"n2-standard-4","scheduling":{"provisioningModel":"STANDARD","instanceTerminationAction":"STOP","terminationTime":trigger.isoformat().replace("+00:00","Z"),"automaticRestart":False},"reservationAffinity":{"consumeReservationType":"NO_RESERVATION"},"networkInterfaces":[{"network":f"https://www.googleapis.com/compute/v1/projects/{settings.project_id}/global/networks/{namespace}-net","networkIP":address,"subnetwork":f"https://www.googleapis.com/compute/v1/projects/{settings.project_id}/regions/{intent.region}/subnetworks/{namespace}-"+("b" if role=="vm_b" and scenario=="inter_region" else "a"),"nicType":"GVNIC","accessConfigs":[{"natIP":public}]}],"disks":[{"boot":True,"source":f"https://www.googleapis.com/compute/v1/projects/{settings.project_id}/zones/{intent.zone}/disks/{namespace}-{suffix}-boot"}],"resourcePolicies":[policy_id] if policy_id else []}
                if policy_mutation:
                    extra = f"projects/{settings.project_id}/regions/europe-west3/resourcePolicies/{namespace}-extra"
                    instance["resourcePolicies"] = {
                        "extra": [policy_id, extra],
                        "duplicate": [policy_id, policy_id],
                        "missing": [],
                        "foreign": [f"projects/foreign-project/regions/europe-west3/resourcePolicies/{namespace}-compact"],
                        "mismatched": [f"projects/{settings.project_id}/regions/europe-west2/resourcePolicies/{namespace}-compact"],
                    }[policy_mutation]
                return result(instance)
            if action == "gcp-guest-hostkeys": return result([{"namespace":"hostkeys","key":"ssh-ed25519","value":host_blob}])
            if action.startswith("gcp-boot-disk"):
                return result({"id":str(2001 if role=="vm_a" else 2002),"name":f"{namespace}-{suffix}-boot","zone":zone_path,"sizeGb":"32","type":f"zones/{intent.zone}/diskTypes/pd-balanced","sourceImage":settings.images[intent.region],"labels":{"cnb-run":hashlib.sha256(deployment.run_id.encode()).hexdigest()[:20],"cnb-deploy":hashlib.sha256(deployment.deployment_id.encode()).hexdigest()[:20]}})
            if action.startswith("gcp-image-read"): return result({"architecture":"X86_64","name":settings.images[intent.region].rsplit("/",1)[-1],"guestOsFeatures":[{"type":"GVNIC"}]})
            raise AssertionError(f"unexpected CommandRequest action {action}: {request.argv}")
    runner=FakeRunner()
    driver=GcpProviderDriver(runner,settings,clock=lambda:now)
    from cloud_network_benchmark.contracts.admission import ExpiryRequest
    request=ExpiryRequest(schema_version=2,run_id=deployment.run_id,deployment_id=deployment.deployment_id,provider="gcp",action="stop",expires_at=Evidence[datetime](value=trigger),reservation_id=deployment.reservation.value.reservation_id,resource_namespace=deployment.run_id,target_selector=f"project={settings.project_id};run_id={deployment.run_id};deployment_id={deployment.deployment_id};roles=vm_a,vm_b")
    expiry=driver.register_expiry(deployment,request)
    assert expiry.state == "registered"
    deployment=deployment.model_copy(update={"expiry":Evidence(value=expiry)})
    if policy_mutation:
        from cloud_network_benchmark.providers.gcp import GcpProviderError
        with pytest.raises(GcpProviderError, match="placement policy"):
            driver.provision(deployment)
        actions=[item.action_id for item in runner.requests]
        assert actions.count("gcp-pair-apply")==1
        assert actions.count("gcp-instance-read-vm_a")==1
        assert "gcp-pair-destroy" in actions and "gcp-protection-destroy" in actions
        journal=json.loads((driver._attempt_dir(deployment)/"attempt.json").read_text())
        assert journal["primary_failure"]["category"] == "placement_mismatch"
        assert driver.cleanup(deployment).cleanup_state == "succeeded"
        return
    output=driver.provision(deployment)
    if north:
        assert (output.vm_a.region, output.vm_b.region) == ("europe-north1", "europe-north1")
        assert (output.vm_a.zone, output.vm_b.zone) == (north_zone, north_zone)
        protection_vars = json.loads((driver._attempt_dir(deployment)/"protection"/"vars.json").read_text())
        assert (protection_vars["zone_a"], protection_vars["zone_b"]) == (north_zone, north_zone)
        pair_vars = json.loads((driver._attempt_dir(deployment)/"pair"/"vars.json").read_text())
        assert (pair_vars["zone_a"], pair_vars["zone_b"]) == (north_zone, north_zone)
        assert protection_vars["image_a"] == protection_vars["image_b"] == settings.images["europe-north1"]
    assert (output.vm_a.private_ipv4,output.vm_b.private_ipv4)==(("10.80.1.10","10.80.1.11") if scenario!="inter_region" else ("10.80.1.10","10.81.1.11"))
    assert output.vm_a.connection.host == "8.8.8.8" and output.vm_b.connection.host == "1.1.1.1"
    pair_vars=json.loads((driver._attempt_dir(deployment)/"pair"/"vars.json").read_text())
    assert pair_vars["template_a"] == expiry.provider_metadata.value["templates"]["vm_a"]["self_link_unique"]
    assert pair_vars["scenario"] == scenario
    assert len([x for x in runner.requests if x.action_id=="gcp-pair-apply"])==1
    cleaned = driver.cleanup(deployment)
    assert cleaned.cleanup_state == "succeeded"
    if north:
        assert cleaned.inventory.value["regions_scanned"] == ["europe-north1"]
        regional_requests = [request for request in runner.requests if request.action_id.startswith((
            "gcp-addresses-inventory-", "gcp-subnet-inventory-", "gcp-policy-inventory-"))]
        assert regional_requests and all(not any(arg.startswith(("--regions", "--global")) for arg in request.argv) for request in regional_requests)


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
    reservation = ReservationEvidence(schema_version=2, campaign_id="gcp-test", reservation_id=Evidence[str](value="reserve-gcp-1"), state="reserved", reserved_cost_usd=Evidence[float](value=10), cap_started_at=Evidence[datetime](value=now), expires_at=Evidence[datetime](value=now+timedelta(seconds=1200)), cap_start_policy_id=StringEvidence(value="reservation-activation-before-provision"), source_path="dated-register.csv", source_sha256=source_hash)
    deployment = ProviderDeploymentInput.from_observation(observation, campaign_id="gcp-test", run_id="gcp-test-gcp", bootstrap_template="bootstrap/cloud-init.yaml", terraform_directory="terraform/gcp", child_result_path="results/gcp-test", config_sha256=resolved.config_source.sha256, implementation_git_commit=StringEvidence(value="b"*40), design_git_commit=StringEvidence(value="c"*40), reservation=Evidence(value=reservation))
    end = reservation.expires_at.value-timedelta(seconds=SAFETY_LEAD_SECONDS)
    request = ExpiryRequest(schema_version=2, run_id=deployment.run_id, deployment_id=deployment.deployment_id, provider="gcp", action="stop", expires_at=Evidence[datetime](value=end), reservation_id=reservation.reservation_id, resource_namespace=deployment.run_id, target_selector=f"project=cnb-test-12345;run_id={deployment.run_id};deployment_id={deployment.deployment_id};roles=vm_a,vm_b")
    key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA test"
    settings = GcpRuntimeSettings(project_id="cnb-test-12345", controller_cidr="203.0.113.4/32", images={"europe-west3":"projects/ubuntu-os-cloud/global/images/ubuntu-2404-lts-amd64-v20261001","europe-west2":"projects/ubuntu-os-cloud/global/images/ubuntu-2404-lts-amd64-v20261001"}, public_key=key, authentication_reference="attempt-identity", private_state_root=tmp_path/"private", cost_input_id=observation.admission.cost_input_id, cost_input_sha256=source_hash, cost_source_path="dated-register.csv", complete_workflow_cost_handoff=StructuredEvidence(value={"source_path":"dated-register.csv","source_sha256":source_hash,"prices_checked_on":"2026-10-23","estimated_total_usd":10,"units":{"vm":"hour","disk":"GiB-hour","address":"hour","transfer":"GiB"}}), support_cost_evidence=StructuredEvidence(value={"id":"gcp-template-placement-support-2026-10-23","prices_checked_on":"2026-10-23","source_path":"tests/fixtures/gcp/zero-support-cost-source.md","source_sha256":"ec0caa3e3b553d14e1e4d60bdd0bd325e6cf8eaf583da3cd9c3884ce310bbcaf","template_support_cost_usd":0,"placement_policy_support_cost_usd":0,"rationale":"No separate per-template or placement-policy support fee is charged."}), complete_workflow_remaining_seconds=1150, registration_workflow_seconds=90, post_registration_workflow_seconds=1060)
    clock_now = [now]
    class Runner:
        def run(self, req):
            started=datetime.now(timezone.utc)
            role="vm_a" if "vm_a" in req.action_id else "vm_b"
            value=_template_response(driver, deployment, role, "111" if role=="vm_a" else "222", end, now+timedelta(seconds=1))
            return CommandResult(action_id=req.action_id,outcome="succeeded",started_at=started,finished_at=started,duration_seconds=0.0,exit_code=0,stdout=json.dumps(value),stderr="")
    driver=GcpProviderDriver(Runner(),settings,clock=lambda:clock_now[0])
    events=[]
    def terraform(_attempt, root_name, action, _deployment, _extra):
        events.append((root_name,action))
        if action == "destroy": return {}
        namespace=driver._namespace(deployment)
        clock_now[0] = now + timedelta(seconds=102.47)
        return {"templates":{"value":{"vm_a":{"id":"111","name":f"{namespace}-a","self_link_unique":f"projects/cnb-test-12345/global/instanceTemplates/{namespace}-a?uniqueId=111"},"vm_b":{"id":"222","name":f"{namespace}-b","self_link_unique":f"projects/cnb-test-12345/global/instanceTemplates/{namespace}-b?uniqueId=222"}}}}
    monkeypatch.setattr(driver,"_terraform",terraform)
    result=driver.register_expiry(deployment,request)
    assert result.state == "registered", result.failure
    assert set(result.provider_metadata.value["templates"]) == {"vm_a","vm_b"}
    assert result.registered_at.value == now + timedelta(seconds=102.47)
    assert result.expires_at.value == now + timedelta(seconds=1170)
    assert settings.complete_workflow_remaining_seconds == 1150
    assert settings.registration_workflow_seconds == 90
    assert settings.post_registration_workflow_seconds == 1060
    assert reservation.cap_started_at.value == now
    assert reservation.expires_at.value == now + timedelta(seconds=1200)
    assert settings.cost_source_path == "dated-register.csv"
    assert settings.cost_input_sha256 == source_hash
    assert (result.expires_at.value - clock_now[0]).total_seconds() == pytest.approx(1067.53)
    with pytest.raises(GcpProviderError, match="whole remaining workflow"):
        driver._fit(reservation, end, 1068)
    retry=driver.register_expiry(deployment,request)
    assert retry.registered_at == result.registered_at
    assert events == [("protection","apply")]
    registered_journal = driver._load(driver._attempt_dir(deployment), deployment)
    assert registered_journal["registered"] is True
    assert not registered_journal.get("creation_may_have_started")
    restarted=GcpProviderDriver(Runner(),settings,clock=lambda:now)
    assert restarted.register_expiry(deployment,request).registered_at == result.registered_at
    assert events == [("protection","apply")]

    clock_now[0] = now
    partial_deployment=deployment.model_copy(update={"run_id":"gcp-test-partial-gcp","deployment_id":"gcp-test-partial-gcp-deployment"})
    partial_request=request.model_copy(update={"run_id":partial_deployment.run_id,"deployment_id":partial_deployment.deployment_id,"resource_namespace":partial_deployment.run_id,"target_selector":f"project={settings.project_id};run_id={partial_deployment.run_id};deployment_id={partial_deployment.deployment_id};roles=vm_a,vm_b"})
    attempt=driver._attempt_dir(partial_deployment)
    driver._save(attempt,{"run_id":partial_deployment.run_id,"deployment_id":partial_deployment.deployment_id,"commands":[]})
    partial=[]
    def partial_protection(_attempt, root_name, action, _deployment, _extra):
        partial.append((root_name,action))
        return {"templates":{"value":{"vm_a":{"id":"111","name":"fake-vm_a","self_link_unique":"projects/p/global/instanceTemplates/111"}}}} if action == "apply" else {}
    monkeypatch.setattr(driver,"_terraform",partial_protection)
    monkeypatch.setattr(driver,"_inventory",lambda _deployment:{"pages_complete":True,"resources":[],"instances":[],"disks":[],"addresses":[],"support_resources":[]})
    failed=driver.register_expiry(partial_deployment,partial_request)
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
    reservation = ReservationEvidence(schema_version=2, campaign_id="cleanup-test", reservation_id=Evidence[str](value="reserve-cleanup-1"), state="reserved", reserved_cost_usd=Evidence[float](value=5), cap_started_at=Evidence[datetime](value=now-timedelta(seconds=1)), expires_at=Evidence[datetime](value=now+timedelta(seconds=1199)), cap_start_policy_id=StringEvidence(value="reserve-first"), source_path="dated-register.csv", source_sha256=source_hash)
    deployment = ProviderDeploymentInput.from_observation(observation, campaign_id="cleanup-test", run_id="cleanup-test-gcp", bootstrap_template="bootstrap/cloud-init.yaml", terraform_directory="terraform/gcp", child_result_path="results/cleanup-test", config_sha256=resolved.config_source.sha256, implementation_git_commit=StringEvidence(value="b"*40), design_git_commit=StringEvidence(value="c"*40), reservation=Evidence(value=reservation))
    settings = GcpRuntimeSettings(project_id="cnb-test-12345", controller_cidr="203.0.113.4/32", images={"europe-west3":"projects/ubuntu-os-cloud/global/images/ubuntu-2404-lts-amd64-v20261001","europe-west2":"projects/ubuntu-os-cloud/global/images/ubuntu-2404-lts-amd64-v20261001"}, public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA test", authentication_reference="attempt-key-ref", private_state_root=tmp_path/"state", cost_input_id=observation.admission.cost_input_id, cost_input_sha256=source_hash, cost_source_path="dated-register.csv", complete_workflow_cost_handoff=StructuredEvidence(value={"source_path":"dated-register.csv","source_sha256":source_hash,"prices_checked_on":"2026-10-23","estimated_total_usd":5,"units":{"vm":"hour","disk":"GiB-hour","address":"hour","transfer":"GiB"}}), support_cost_evidence=StructuredEvidence(value={"id":"gcp-template-placement-support-2026-10-23","prices_checked_on":"2026-10-23","source_path":"tests/fixtures/gcp/zero-support-cost-source.md","source_sha256":"ec0caa3e3b553d14e1e4d60bdd0bd325e6cf8eaf583da3cd9c3884ce310bbcaf","template_support_cost_usd":0,"placement_policy_support_cost_usd":0,"rationale":"No separate per-template or placement-policy support fee is charged."}), complete_workflow_remaining_seconds=500, registration_workflow_seconds=100, post_registration_workflow_seconds=400)
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


def test_interrupted_registration_recovers_only_fully_proven_orphan_templates(tmp_path, monkeypatch):
    from cloud_network_benchmark.contracts.execution import CommandResult
    deployment, driver = _cleanup_fixture(tmp_path)
    attempt = driver._attempt_dir(deployment)
    reservation = deployment.reservation.value
    trigger = reservation.expires_at.value - timedelta(seconds=SAFETY_LEAD_SECONDS)
    namespace = driver._namespace(deployment)
    cap_start = reservation.cap_started_at.value
    driver._save(attempt, {"run_id": deployment.run_id, "deployment_id": deployment.deployment_id,
        "registration_may_have_started": True, "registered": False, "binding_sha256": driver._binding_fingerprint(deployment),
        "reservation_id": reservation.reservation_id.value, "cap_started_at": cap_start.isoformat(),
        "cap_expires_at": reservation.expires_at.value.isoformat(), "trigger": trigger.isoformat()})
    descriptions = {
        f"{namespace}-a": _template_response(driver, deployment, "vm_a", 7183431637078019760, trigger, cap_start + timedelta(milliseconds=1)),
        f"{namespace}-b": _template_response(driver, deployment, "vm_b", 7829080923790806671, trigger, cap_start + timedelta(milliseconds=2)),
    }
    live = set(descriptions)
    deleted = []
    terraform = []
    monkeypatch.setattr(driver, "_terraform", lambda _attempt, root, action, *_args: terraform.append((root, action)) or {})
    def read(args, action):
        if action == "gcp-instance_templates-inventory-all-scopes":
            return [{"id": descriptions[name]["id"], "name": name, "selfLink": descriptions[name]["selfLink"], "labels": {}}
                    for name in sorted(live)]
        if action.startswith("gcp-orphan-template-read-"):
            return descriptions[args[3]]
        return []
    monkeypatch.setattr(driver, "_gcloud_json", read)
    def command(action, argv, cwd, timeout=None):
        assert argv[:4] == [driver.settings.gcloud, "compute", "instance-templates", "delete"]
        assert argv[-3:] == ["--project", driver.settings.project_id, "--quiet"]
        name = argv[4]
        assert name in live
        import json
        saved = json.loads((attempt / "attempt.json").read_text())["orphan_template_recovery"]
        assert saved["absence_verified"] is False
        assert {item["id"] for item in saved["deleted"]} == {"7183431637078019760", "7829080923790806671"}
        deleted.append((name, argv))
        live.remove(name)
        now = datetime.now(timezone.utc)
        return CommandResult(action_id=action, outcome="succeeded", started_at=now, finished_at=now,
                             duration_seconds=0, exit_code=0, stdout="", stderr="")
    monkeypatch.setattr(driver, "_run", command)
    result = driver.cleanup(deployment)
    assert result.cleanup_state == "succeeded"
    assert result.inventory.value["pages_complete"] is True and not result.inventory.value["resources"]
    assert terraform == [("protection", "destroy")]
    assert [name for name, _argv in deleted] == [f"{namespace}-a", f"{namespace}-b"]
    import json
    from cloud_network_benchmark.lifecycle_cleanup import _verified_orphan_template_ids
    recovery = json.loads((attempt / "attempt.json").read_text())
    assert recovery["orphan_template_recovery"]["absence_verified"] is True
    assert set(_verified_orphan_template_ids(recovery, {f"{namespace}-a", f"{namespace}-b"})) == {"7183431637078019760", "7829080923790806671"}


@pytest.mark.parametrize("failure", ["partial_delete", "partial_then_wrong_id", "partial_then_wrong_config", "incomplete_readback", "incomplete_initial"])
def test_orphan_template_recovery_keeps_cleanup_blocked_without_complete_absence(tmp_path, monkeypatch, failure):
    import json
    from cloud_network_benchmark.contracts.execution import CommandResult
    deployment, driver = _cleanup_fixture(tmp_path)
    attempt = driver._attempt_dir(deployment)
    reservation = deployment.reservation.value
    trigger = reservation.expires_at.value - timedelta(seconds=SAFETY_LEAD_SECONDS)
    namespace = driver._namespace(deployment)
    start = reservation.cap_started_at.value
    driver._save(attempt, {"run_id": deployment.run_id, "deployment_id": deployment.deployment_id,
        "registration_may_have_started": True, "registered": False, "binding_sha256": driver._binding_fingerprint(deployment),
        "reservation_id": reservation.reservation_id.value, "cap_started_at": start.isoformat(),
        "cap_expires_at": reservation.expires_at.value.isoformat(), "trigger": trigger.isoformat()})
    descriptions = {
        f"{namespace}-a": _template_response(driver, deployment, "vm_a", 201, trigger, start + timedelta(milliseconds=1)),
        f"{namespace}-b": _template_response(driver, deployment, "vm_b", 202, trigger, start + timedelta(milliseconds=2)),
    }
    live = set(descriptions)
    monkeypatch.setattr(driver, "_terraform", lambda *_args: {})
    def read(args, action):
        if action == "gcp-instance_templates-inventory-all-scopes":
            return [{"id": descriptions[name]["id"], "name": name, "selfLink": descriptions[name]["selfLink"], "labels": {}}
                    for name in sorted(live)]
        if action.startswith("gcp-orphan-template-read-"): return descriptions[args[3]]
        return []
    monkeypatch.setattr(driver, "_gcloud_json", read)
    deleted = []
    failed_once = False
    def command(action, argv, cwd, timeout=None):
        nonlocal failed_once
        name = argv[4]
        deleted.append(name)
        if failure.startswith("partial") and name.endswith("-b") and not failed_once:
            failed_once = True
            now = datetime.now(timezone.utc)
            return CommandResult(action_id=action, outcome="failed", started_at=now, finished_at=now,
                                 duration_seconds=0, exit_code=1, stdout="", stderr="failed")
        live.remove(name)
        now = datetime.now(timezone.utc)
        return CommandResult(action_id=action, outcome="succeeded", started_at=now, finished_at=now,
                             duration_seconds=0, exit_code=0, stdout="", stderr="")
    monkeypatch.setattr(driver, "_run", command)
    if failure in {"incomplete_readback", "incomplete_initial"}:
        original_inventory = driver._inventory
        calls = 0
        def inventory(_deployment):
            nonlocal calls
            calls += 1
            result = original_inventory(_deployment)
            failure_call = 2 if failure == "incomplete_initial" else 3
            return {**result, "pages_complete": False} if calls == failure_call else result
        monkeypatch.setattr(driver, "_inventory", inventory)
    result = driver.cleanup(deployment)
    assert result.cleanup_state == "failed"
    assert result.cleanup_failure.category == "cleanup_incomplete"
    saved_state = json.loads((attempt / "attempt.json").read_text())
    if failure == "incomplete_initial":
        assert "orphan_template_recovery" not in saved_state
        assert deleted == []
    else:
        saved = saved_state["orphan_template_recovery"]
        assert saved["absence_verified"] is False
        assert len(saved["deleted"]) == 2 and len(deleted) == 2
        expected_remaining = 1 if failure.startswith("partial") else 0
        assert len(result.inventory.value["support_resources"]) == expected_remaining
        if failure == "partial_delete":
            retry = driver.cleanup(deployment)
            assert retry.cleanup_state == "succeeded"
            assert json.loads((attempt / "attempt.json").read_text())["orphan_template_recovery"]["absence_verified"] is True
            assert len(deleted) == 3
        elif failure in {"partial_then_wrong_id", "partial_then_wrong_config"}:
            remaining_name = f"{namespace}-b"
            if failure == "partial_then_wrong_id": descriptions[remaining_name]["id"] = "999"
            else: descriptions[remaining_name]["properties"]["machineType"] = "n2-standard-8"
            retry = driver.cleanup(deployment)
            assert retry.cleanup_state == "failed"
            assert len(deleted) == 2
        elif failure == "incomplete_readback":
            retry = driver.cleanup(deployment)
            assert retry.cleanup_state == "succeeded"
            assert json.loads((attempt / "attempt.json").read_text())["orphan_template_recovery"]["absence_verified"] is True


def test_verified_orphan_template_ids_are_source_bound_for_cleanup_receipt_replay(tmp_path, monkeypatch):
    import hashlib
    import json
    from types import SimpleNamespace
    from cloud_network_benchmark.contracts.common import Evidence, Provider, StructuredEvidence
    from cloud_network_benchmark.lifecycle_cleanup import _result_is_absent, _verified_orphan_template_ids, replay_saved_cleanup_receipt
    from cloud_network_benchmark.providers.gcp import GcpCleanupResult, GcpProviderDriver
    ids = ["7183431637078019760", "7829080923790806671"]
    journal = tmp_path / "attempt.json"
    state = {"run_id": "gcp-replay-run", "deployment_id": "gcp-replay-deployment",
             "orphan_template_recovery": {"deleted": [], "absence_verified": True}}
    namespace = GcpProviderDriver._namespace(SimpleNamespace(run_id=state["run_id"], deployment_id=state["deployment_id"]))
    state["orphan_template_recovery"]["deleted"] = [
        {"name": f"{namespace}-a", "id": ids[0]}, {"name": f"{namespace}-b", "id": ids[1]}]
    journal.write_text(json.dumps(state))
    settings_ref = tmp_path / "settings.json"
    settings_ref.write_text("saved")
    settings_ref.chmod(0o600)
    saved = SimpleNamespace(provider=Provider.GCP, deployment=SimpleNamespace(run_id=state["run_id"], deployment_id=state["deployment_id"]),
        reservation=SimpleNamespace(reservation_id=SimpleNamespace(value="reservation-1")))
    monkeypatch.setattr("cloud_network_benchmark.lifecycle_cleanup.load_cleanup_settings", lambda _path: saved)
    monkeypatch.setattr("cloud_network_benchmark.lifecycle_cleanup._expected_journal", lambda _saved: journal)
    record = SimpleNamespace(cleanup_settings_reference=str(settings_ref), provider=str(Provider.GCP),
        deployment_id=state["deployment_id"], journal_reference=str(journal))
    recovery = SimpleNamespace(reservation_id="reservation-1", ledger_path=str(tmp_path / "ledger.json"), ledger_scope_id="scope-1")
    result = GcpCleanupResult(cleanup_state="succeeded", inventory=StructuredEvidence(value={
        "pages_complete": True, "resources": [], "instances": [], "disks": [], "addresses": [], "support_resources": []}),
        expiry=Evidence(unavailable_reason="no native expiry was registered"))
    receipt = {"provider": str(Provider.GCP), "reservation_id": "reservation-1", "deployment_id": state["deployment_id"],
        "absence_proved": True, "absent_resource_ids": ids, "cleanup_result": result.model_dump(mode="json"),
        "occurred_at": datetime.now(timezone.utc).isoformat()}
    raw = (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode()
    digest = hashlib.sha256(raw).hexdigest()
    receipt_path = tmp_path / f"reservation-1-{Provider.GCP}-cleanup-{digest[:16]}.json"
    receipt_path.write_bytes(raw)
    receipt_path.chmod(0o600)
    assert set(_verified_orphan_template_ids(state, {f"{namespace}-a", f"{namespace}-b"})) == set(ids)
    assert _result_is_absent(Provider.GCP, GcpCleanupResult.model_validate(receipt["cleanup_result"]), ids)
    class Ledger:
        path = recovery.ledger_path
        scope_id = recovery.ledger_scope_id
        events = {}
        def reconcile(self, event): self.events[event.event_id] = event
        def inspect(self): return {"reservations": {"reservation-1": {"provider_cleanup": {str(Provider.GCP): True}}}}
    ledger = Ledger()
    assert replay_saved_cleanup_receipt(record, recovery=recovery, ledger=ledger) is True
    assert replay_saved_cleanup_receipt(record, recovery=recovery, ledger=ledger) is True
    assert len(ledger.events) == 1
    assert tuple(sorted(ledger.events.values().__iter__().__next__().absent_resource_ids)) == tuple(ids)
    state["orphan_template_recovery"]["absence_verified"] = False
    journal.write_text(json.dumps(state))
    assert replay_saved_cleanup_receipt(record, recovery=recovery, ledger=ledger) is False


@pytest.mark.parametrize("mutation", ["wrong_label", "wrong_project", "wrong_name", "wrong_id", "wrong_shape", "wrong_expiry", "created_before_cap", "created_after_trigger"])
def test_interrupted_registration_rejects_unproven_templates_without_deletion(tmp_path, monkeypatch, mutation):
    import json
    from cloud_network_benchmark.contracts.execution import CommandResult
    deployment, driver = _cleanup_fixture(tmp_path)
    attempt = driver._attempt_dir(deployment)
    reservation = deployment.reservation.value
    trigger = reservation.expires_at.value - timedelta(seconds=SAFETY_LEAD_SECONDS)
    namespace = driver._namespace(deployment)
    start = reservation.cap_started_at.value
    driver._save(attempt, {"run_id": deployment.run_id, "deployment_id": deployment.deployment_id,
        "registration_may_have_started": True, "registered": False, "binding_sha256": driver._binding_fingerprint(deployment),
        "reservation_id": reservation.reservation_id.value, "cap_started_at": start.isoformat(),
        "cap_expires_at": reservation.expires_at.value.isoformat(), "trigger": trigger.isoformat()})
    descriptions = {
        f"{namespace}-a": _template_response(driver, deployment, "vm_a", 101, trigger, start + timedelta(milliseconds=1)),
        f"{namespace}-b": _template_response(driver, deployment, "vm_b", 102, trigger, start + timedelta(milliseconds=2)),
    }
    listed_ids = {f"{namespace}-a": "101", f"{namespace}-b": "102"}
    if mutation == "wrong_label": descriptions[f"{namespace}-a"]["properties"]["labels"]["cnb-run"] = "foreign"
    elif mutation == "wrong_project": descriptions[f"{namespace}-a"]["selfLink"] = descriptions[f"{namespace}-a"]["selfLink"].replace(driver.settings.project_id, "other-project")
    elif mutation == "wrong_name": descriptions[f"{namespace}-a"]["name"] = "other-template"
    elif mutation == "wrong_id": descriptions[f"{namespace}-a"]["id"] = "999"
    elif mutation == "wrong_shape": descriptions[f"{namespace}-a"]["properties"]["machineType"] = "n2-standard-8"
    elif mutation == "wrong_expiry": descriptions[f"{namespace}-a"]["properties"]["scheduling"]["terminationTime"] = (trigger + timedelta(seconds=1)).isoformat()
    elif mutation == "created_before_cap": descriptions[f"{namespace}-a"]["creationTimestamp"] = (start - timedelta(seconds=1)).isoformat()
    elif mutation == "created_after_trigger": descriptions[f"{namespace}-a"]["creationTimestamp"] = (trigger + timedelta(seconds=1)).isoformat()
    live = set(descriptions)
    deletes = []
    monkeypatch.setattr(driver, "_terraform", lambda *_args: {})
    def read(args, action):
        if action == "gcp-instance_templates-inventory-all-scopes":
            return [{"id": listed_ids[name], "name": name,
                     "selfLink": f"https://www.googleapis.com/compute/v1/projects/{driver.settings.project_id}/global/instanceTemplates/{name}", "labels": {}}
                    for name in sorted(live)]
        if action.startswith("gcp-orphan-template-read-"): return descriptions[args[3]]
        return []
    monkeypatch.setattr(driver, "_gcloud_json", read)
    def command(action, argv, cwd, timeout=None):
        deletes.append(argv)
        now = datetime.now(timezone.utc)
        return CommandResult(action_id=action, outcome="succeeded", started_at=now, finished_at=now,
                             duration_seconds=0, exit_code=0, stdout="", stderr="")
    monkeypatch.setattr(driver, "_run", command)
    result = driver.cleanup(deployment)
    assert result.cleanup_state == "failed"
    assert result.cleanup_failure.category in {"expiry_mismatch", "cap_order", "cleanup_incomplete"}
    assert deletes == []
    assert result.inventory.value["pages_complete"] is True
    assert "orphan_template_recovery" not in json.loads((attempt / "attempt.json").read_text())


def test_same_attempt_prior_controller_session_blocks_reuse_and_provision_but_allows_cleanup(tmp_path, monkeypatch):
    import json
    import cloud_network_benchmark.providers.gcp as gcp_provider
    from cloud_network_benchmark.contracts.admission import ExpiryEvidence, ExpiryRequest
    from cloud_network_benchmark.contracts.common import Evidence, StructuredEvidence
    from cloud_network_benchmark.providers.gcp import GcpProviderError
    deployment, driver = _cleanup_fixture(tmp_path)
    now = datetime.now(timezone.utc)
    trigger = deployment.reservation.value.expires_at.value - timedelta(seconds=SAFETY_LEAD_SECONDS)
    expiry = ExpiryEvidence(schema_version=2, run_id=deployment.run_id, deployment_id=deployment.deployment_id, provider="gcp", action="stop", state="registered", registration_id=Evidence[str](value="111:222"), registered_at=Evidence[datetime](value=now-timedelta(seconds=5)), expires_at=Evidence[datetime](value=trigger), target_selector=f"project={driver.settings.project_id};run_id={deployment.run_id};deployment_id={deployment.deployment_id};roles=vm_a,vm_b", provider_metadata=StructuredEvidence(value={"native_absolute_stop": True}))
    attempt = driver._attempt_dir(deployment)
    original_cap = {"cap_started_at": deployment.reservation.value.cap_started_at.value.isoformat(), "cap_expires_at": deployment.reservation.value.expires_at.value.isoformat(), "trigger": trigger.isoformat()}
    driver._save(attempt, {"run_id": deployment.run_id, "deployment_id": deployment.deployment_id, **original_cap, "reservation_id": deployment.reservation.value.reservation_id.value, "binding_sha256": driver._binding_fingerprint(deployment), "controller_session_id": "prior-session", "registration_may_have_started": True, "registered": True, "expiry": expiry.model_dump(mode="json"), "templates": {"vm_a": {"id": "111", "name": "template-a"}, "vm_b": {"id": "222", "name": "template-b"}}})
    request = ExpiryRequest(schema_version=2, run_id=deployment.run_id, deployment_id=deployment.deployment_id, provider="gcp", action="stop", expires_at=Evidence[datetime](value=trigger), reservation_id=deployment.reservation.value.reservation_id, resource_namespace=deployment.run_id, target_selector=expiry.target_selector)
    monkeypatch.setattr(gcp_provider, "CONTROLLER_SESSION_ID", "replacement-session")
    terraform_actions = []
    monkeypatch.setattr(driver, "_terraform", lambda _attempt, root, action, _deployment, _extra: terraform_actions.append((root, action)) or {})
    monkeypatch.setattr(driver, "_inventory", lambda _deployment: {"pages_complete": True, "instances": [], "resources": [], "known": []})
    with pytest.raises(GcpProviderError, match="earlier controller session"):
        driver.register_expiry(deployment, request)
    with pytest.raises(GcpProviderError, match="earlier controller session"):
        driver.provision(deployment.model_copy(update={"expiry": Evidence(value=expiry)}))
    assert not any(action == "apply" for _root, action in terraform_actions)
    before_cleanup = json.loads((attempt / "attempt.json").read_text())
    assert before_cleanup["controller_session_id"] == "prior-session"
    assert {key: before_cleanup[key] for key in original_cap} == original_cap
    result = driver.cleanup(deployment)
    assert result.cleanup_state == "succeeded"
    assert terraform_actions == [("protection", "destroy")]
    after_cleanup = json.loads((attempt / "attempt.json").read_text())
    assert after_cleanup["controller_session_id"] == "prior-session"
    assert {key: after_cleanup[key] for key in original_cap} == original_cap
    assert after_cleanup["cleanup_state"] == "succeeded"


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
    monkeypatch.setattr(driver,"_gcloud_json",lambda args,_action: [{"id":"42","name":"owned-terminated","status":"TERMINATED","labels":labels}] if "instances" in args and "list" in args else [])
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


def test_healthy_registered_attempt_allows_independent_primary_launch(tmp_path):
    import json
    from cloud_network_benchmark.providers.gcp import CONTROLLER_SESSION_ID
    deployment, driver = _cleanup_fixture(tmp_path)
    root=driver.settings.private_state_root
    path=root/"other"/"campaign-gcp"/"campaign-gcp-deployment"
    path.mkdir(parents=True)
    (path/"attempt.json").write_text(json.dumps({"run_id":"campaign-gcp","deployment_id":"campaign-gcp-deployment","registration_may_have_started":True,"registered":True,"creation_may_have_started":True,"controller_session_id":CONTROLLER_SESSION_ID,"cleanup_state":None,"launch_blocked":False}))
    assert driver._assert_no_open_cleanup(deployment) is None


def test_registered_only_prior_session_blocks_until_cleanup_is_verified(tmp_path):
    import json
    from cloud_network_benchmark.providers.gcp import GcpProviderError
    deployment, driver = _cleanup_fixture(tmp_path)
    path=driver.settings.private_state_root/"old"/"campaign-gcp"/"campaign-gcp-deployment"
    path.mkdir(parents=True)
    state={"run_id":"campaign-gcp","deployment_id":"campaign-gcp-deployment","registration_may_have_started":True,"registered":True,"controller_session_id":"dead-controller-session"}
    (path/"attempt.json").write_text(json.dumps(state))
    with pytest.raises(GcpProviderError,match="earlier controller session"):
        driver._assert_no_open_cleanup(deployment)
    state["protection_cleanup_state"]="succeeded"
    (path/"attempt.json").write_text(json.dumps(state))
    assert driver._assert_no_open_cleanup(deployment) is None


def test_stale_provision_save_preserves_disk_and_management_host_checkpoints(tmp_path):
    import hashlib
    import json
    from cloud_network_benchmark.contracts.common import FailureEvidence
    from cloud_network_benchmark.providers.gcp import GcpProviderError
    deployment, driver = _cleanup_fixture(tmp_path)
    attempt = driver._attempt_dir(deployment)
    driver._active_attempt = attempt
    driver._save(attempt, {"run_id": deployment.run_id, "deployment_id": deployment.deployment_id, "commands": []})
    stale = driver._load(attempt, deployment)
    namespace = driver._namespace(deployment)
    instance = _instance_response(driver, deployment, "vm_a")
    image = driver.settings.images[deployment.vm_a.region].rsplit("/", 1)[-1]
    labels = {"cnb-run": hashlib.sha256(deployment.run_id.encode()).hexdigest()[:20], "cnb-deploy": hashlib.sha256(deployment.deployment_id.encode()).hexdigest()[:20]}
    def fail_after_disk(args, action):
        if action.startswith("gcp-boot-disk"):
            return {"id": "201", "name": f"{namespace}-a-boot", "zone": "europe-west3-a", "sizeGb": "32", "type": "zones/europe-west3-a/diskTypes/pd-balanced", "sourceImage": driver.settings.images[deployment.vm_a.region], "labels": labels}
        return {"architecture": "X86_64", "name": image, "guestOsFeatures": []}
    driver._gcloud_json = fail_after_disk
    with pytest.raises(GcpProviderError, match="does not advertise the GvNIC"):
        driver._validate_instance(instance, deployment, "vm_a")
    checkpoint = driver._load(attempt, deployment)
    assert checkpoint["observed_disks"] == [{"id": "201", "name": f"{namespace}-a-boot", "zone": "europe-west3-a"}]
    checkpoint["known_management_hosts"] = {"101": "8.8.8.8"}
    checkpoint["commands"] = [{"action_id": "disk-read", "outcome": "succeeded"}]
    driver._save(attempt, checkpoint)
    # The failure handler still holds this pre-read local copy when it saves its primary failure.
    stale["primary_failure"] = FailureEvidence(category="image_mismatch", message="image readback failed", occurred_at=datetime.now(timezone.utc)).model_dump(mode="json")
    driver._save(attempt, stale)
    recovered = GcpProviderDriver(object(), driver.settings)._load(attempt, deployment)
    assert recovered["observed_disks"] == [{"id": "201", "name": f"{namespace}-a-boot", "zone": "europe-west3-a"}]
    assert recovered["known_management_hosts"] == {"101": "8.8.8.8"}
    assert any(event.get("action_id") == "disk-read" for event in recovered["commands"])
    assert recovered["primary_failure"]["category"] == "image_mismatch"


def test_restarted_controller_rejects_changed_address_for_saved_vm_identity(tmp_path, monkeypatch):
    import base64
    import struct
    from cloud_network_benchmark.providers.gcp import GcpProviderError
    deployment, first = _cleanup_fixture(tmp_path)
    attempt = first._attempt_dir(deployment)
    name = f"{first._namespace(deployment)}-a"
    zone = deployment.vm_a.zone
    first._save(attempt, {"run_id": deployment.run_id, "deployment_id": deployment.deployment_id, "known_management_hosts": {"101": "8.8.8.8"}})
    restarted = GcpProviderDriver(object(), first.settings)
    restarted._active_attempt = attempt
    blob = base64.b64encode(struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + b"k" * 32).decode()
    identity = {"id": "101", "name": name, "zone": f"https://compute.googleapis.com/compute/v1/projects/{first.settings.project_id}/zones/{zone}", "selfLink": f"https://compute.googleapis.com/compute/v1/projects/{first.settings.project_id}/zones/{zone}/instances/{name}", "networkInterfaces": [{"networkIP": "10.80.1.10", "accessConfigs": [{"natIP": "1.1.1.1"}]}]}
    def read(args, action):
        if action == "gcp-guest-hostkeys":
            return [{"namespace": "hostkeys", "key": "ssh-ed25519", "value": blob}]
        return identity
    monkeypatch.setattr(restarted, "_gcloud_json", read)
    with pytest.raises(GcpProviderError, match="management IPv4 changed for an already bound VM identity"):
        restarted._trusted_hostkey(name, zone, identity)


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
    with pytest.raises(GcpProviderError,match="this attempt already has creation or cleanup state"):
        driver._assert_no_open_cleanup(deployment)


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
    driver._terraform(attempt,"pair","apply",deployment,{"template_a":"projects/test/global/instanceTemplates/unique-a","template_b":"projects/test/global/instanceTemplates/unique-b","scenario":"same_zone"})
    variables=(attempt/"pair"/"vars.json").read_bytes()
    binding=json.loads((attempt/"attempt.json").read_text())["pair_binding"]
    assert binding["config_sha256"]==hashlib.sha256(variables).hexdigest()
    assert binding["template_a"].endswith("unique-a") and binding["template_b"].endswith("unique-b")


def test_s1_fake_provision_normalizes_authenticated_vm_disk_image_and_hostkey_readbacks(tmp_path,monkeypatch):
    import json, hashlib
    from cloud_network_benchmark.contracts.admission import ExpiryEvidence
    from cloud_network_benchmark.contracts.common import Evidence,StructuredEvidence
    deployment,driver=_cleanup_fixture(tmp_path)
    now=datetime.now(timezone.utc)
    trigger=deployment.reservation.value.expires_at.value-timedelta(seconds=SAFETY_LEAD_SECONDS)
    expiry=ExpiryEvidence(schema_version=2,run_id=deployment.run_id,deployment_id=deployment.deployment_id,provider="gcp",action="stop",state="registered",registration_id=Evidence[str](value="111:222"),registered_at=Evidence[datetime](value=now-timedelta(seconds=5)),expires_at=Evidence[datetime](value=trigger),target_selector="project=cnb-test-12345;roles=vm_a,vm_b",provider_metadata=StructuredEvidence(value={"native_absolute_stop":True}))
    deployment=deployment.model_copy(update={"expiry":Evidence(value=expiry)})
    attempt=driver._attempt_dir(deployment)
    namespace=driver._namespace(deployment)
    templates={"vm_a":{"id":"111","name":"template-a","self_link_unique":"projects/test/global/instanceTemplates/unique-a"},"vm_b":{"id":"222","name":"template-b","self_link_unique":"projects/test/global/instanceTemplates/unique-b"}}
    driver._save(attempt,{"run_id":deployment.run_id,"deployment_id":deployment.deployment_id,"registered":True,"controller_session_id":CONTROLLER_SESSION_ID,"expiry":expiry.model_dump(mode="json"),"templates":templates,"binding_sha256":driver._binding_fingerprint(deployment),"reservation_id":deployment.reservation.value.reservation_id.value})
    monkeypatch.setattr(driver,"_verify_registered_templates",lambda *_args:None)
    monkeypatch.setattr(driver,"_terraform",lambda *_args:{"instances":{"value":{"vm_a":{"name":f"{namespace}-a","zone":deployment.vm_a.zone,"id":"101","self_link":f"projects/cnb-test-12345/zones/{deployment.vm_a.zone}/instances/{namespace}-a"},"vm_b":{"name":f"{namespace}-b","zone":deployment.vm_b.zone,"id":"102","self_link":f"projects/cnb-test-12345/zones/{deployment.vm_b.zone}/instances/{namespace}-b"}}}})
    import base64, struct
    hostkey=base64.b64encode(struct.pack(">I",11)+b"ssh-ed25519"+struct.pack(">I",32)+b"x"*32).decode()
    def readback(args,action):
        role="vm_a" if f"{namespace}-a" in args or action.endswith("vm_a") else "vm_b"
        intent=deployment.vm_a if role=="vm_a" else deployment.vm_b
        address="10.80.1.10" if role=="vm_a" else "10.80.1.11"
        zone_path=f"https://compute.googleapis.com/compute/v1/projects/test/zones/{intent.zone}"
        if action.startswith("gcp-boot-disk"):
            return {"id":"201" if role=="vm_a" else "202","name":f"{driver._namespace(deployment)}-{'a' if role=='vm_a' else 'b'}-boot","zone":zone_path,"sizeGb":"32","type":"zones/europe-west3-a/diskTypes/pd-balanced","sourceImage":f"projects/ubuntu-os-cloud/global/images/{driver.settings.images[intent.region].rsplit('/',1)[-1]}","labels":{"cnb-run":hashlib.sha256(deployment.run_id.encode()).hexdigest()[:20],"cnb-deploy":hashlib.sha256(deployment.deployment_id.encode()).hexdigest()[:20]}}
        if action.startswith("gcp-image-read"):
            return {"architecture":"X86_64","name":driver.settings.images[intent.region].rsplit("/",1)[-1],"guestOsFeatures":[{"type":"GVNIC"}]}
        if action=="gcp-guest-hostkeys":
            return [{"namespace":"hostkeys","key":"ssh-ed25519","value":hostkey}]
        if action.startswith("gcp-hostkey-instance"):
            return {"id":"101" if role=="vm_a" else "102","name":f"{namespace}-{'a' if role=='vm_a' else 'b'}","zone":zone_path,"selfLink":f"https://compute.googleapis.com/compute/v1/projects/cnb-test-12345/zones/{intent.zone}/instances/{namespace}-{'a' if role=='vm_a' else 'b'}","networkInterfaces":[{"networkIP":address,"accessConfigs":[{"natIP":"8.8.8.8"}]}]}
        return {"id":"101" if role=="vm_a" else "102","name":f"{namespace}-{'a' if role=='vm_a' else 'b'}","zone":zone_path,"selfLink":f"https://compute.googleapis.com/compute/v1/projects/cnb-test-12345/zones/{intent.zone}/instances/{namespace}-{'a' if role=='vm_a' else 'b'}","machineType":"n2-standard-4","scheduling":{"provisioningModel":"STANDARD","instanceTerminationAction":"STOP","terminationTime":trigger.isoformat().replace("+00:00","Z"),"automaticRestart":False},"reservationAffinity":{"consumeReservationType":"NO_RESERVATION"},"networkInterfaces":[{"network":f"https://www.googleapis.com/compute/v1/projects/cnb-test-12345/global/networks/{namespace}-net","networkIP":address,"subnetwork":f"https://www.googleapis.com/compute/v1/projects/cnb-test-12345/regions/europe-west3/subnetworks/{namespace}-a","nicType":"GVNIC","accessConfigs":[{"natIP":"8.8.8.8"}]}],"disks":[{"boot":True,"source":f"https://compute.googleapis.com/compute/v1/projects/cnb-test-12345/zones/{intent.zone}/disks/{namespace}-{'a' if role=='vm_a' else 'b'}-boot"}],"resourcePolicies":[]}
    monkeypatch.setattr(driver,"_gcloud_json",readback)
    result=driver.provision(deployment)
    assert result.vm_a.private_ipv4=="10.80.1.10" and result.vm_b.private_ipv4=="10.80.1.11"
    assert result.vm_a.actual_shape==result.vm_b.actual_shape=="n2-standard-4"
    assert result.vm_a.physical_zone_id.unavailable_reason
    assert result.provider_metadata.value["no_source_template_response_claim"] is True
    journal=json.loads((attempt/"attempt.json").read_text())
    assert journal["observed_disks"][0]["id"] in {"201","202"}
    assert journal["known_management_hosts"] == {"101": "8.8.8.8", "102": "8.8.8.8"}


def test_registered_template_replacement_or_weakened_stop_fails_precreate(tmp_path, monkeypatch):
    from cloud_network_benchmark.providers.gcp import GcpProviderError
    deployment, driver = _cleanup_fixture(tmp_path)
    trigger = deployment.reservation.value.expires_at.value - timedelta(seconds=SAFETY_LEAD_SECONDS)
    expected = trigger.isoformat().replace("+00:00", "Z")
    import hashlib, json
    created = datetime.now(timezone.utc)-timedelta(seconds=1)
    desc_a = _template_response(driver, deployment, "vm_a", 111, trigger, created)
    desc_b = _template_response(driver, deployment, "vm_b", 222, trigger, created)
    namespace = driver._namespace(deployment)
    template = {"id": "111", "name": f"{namespace}-a", "zone": deployment.vm_a.zone, "expected_policy": None, "self_link_unique": f"projects/cnb-test-12345/global/instanceTemplates/{namespace}-a?uniqueId=111", "sha256": hashlib.sha256(json.dumps(desc_a, sort_keys=True).encode()).hexdigest()}
    second = {"id": "222", "name": f"{namespace}-b", "zone": deployment.vm_b.zone, "expected_policy": None, "self_link_unique": f"projects/cnb-test-12345/global/instanceTemplates/{namespace}-b?uniqueId=222", "sha256": hashlib.sha256(json.dumps(desc_b, sort_keys=True).encode()).hexdigest()}
    monkeypatch.setattr(driver, "_gcloud_json", lambda args, _action: desc_b if second["name"] in args else desc_a)
    driver._verify_registered_templates({"templates": {"vm_a": template, "vm_b": second}}, trigger, deployment)
    replaced = {**desc_a, "id": "222"}
    monkeypatch.setattr(driver, "_gcloud_json", lambda *_args: replaced)
    with pytest.raises(GcpProviderError, match="immutable template properties"):
        driver._verify_registered_templates({"templates": {"vm_a": template, "vm_b": second}}, trigger, deployment)


@pytest.mark.parametrize("change", ["swapped_role", "foreign_link", "wrong_shape", "wrong_disk", "wrong_subnet", "weak_metadata", "relative_runtime", "foreign_rest_link", "wrong_rest_id", "wrong_unique_id", "wrong_unique_name", "bad_disk_string", "bool_disk_size", "tier_one", "fixed_cpu", "node_affinity"])
def test_template_role_binding_rejects_swapped_foreign_or_weakened_properties(tmp_path,change):
    from cloud_network_benchmark.providers.gcp import GcpProviderError
    deployment,driver=_cleanup_fixture(tmp_path)
    now=datetime.now(timezone.utc)
    trigger=deployment.reservation.value.expires_at.value-timedelta(seconds=SAFETY_LEAD_SECONDS)
    name=f"{driver._namespace(deployment)}-a"
    described=_template_response(driver,deployment,"vm_a","111",trigger,now-timedelta(seconds=1))
    item={"id":"111","name":name,"zone":deployment.vm_a.zone,"expected_policy":None,"self_link_unique":f"projects/{driver.settings.project_id}/global/instanceTemplates/{name}?uniqueId=111"}
    if change=="swapped_role":
        item["name"]=f"{driver._namespace(deployment)}-b"
    elif change=="foreign_link":
        item["self_link_unique"]="projects/foreign-project/global/instanceTemplates/forged?uniqueId=111"
    elif change=="wrong_shape":
        described["properties"]["machineType"]="n2-standard-8"
    elif change=="wrong_disk":
        described["properties"]["disks"][0]["initializeParams"]["diskSizeGb"]=64
    elif change=="wrong_subnet":
        described["properties"]["networkInterfaces"][0]["subnetwork"]="projects/foreign-project/regions/europe-west3/subnetworks/other"
    elif change=="weak_metadata":
        next(x for x in described["properties"]["metadata"]["items"] if x["key"]=="block-project-ssh-keys")["value"]="FALSE"
    elif change=="relative_runtime":
        described["properties"]["scheduling"]["maxRunDuration"]={"seconds":"3600"}
    elif change=="foreign_rest_link":
        described["selfLink"] = described["selfLink"].replace(driver.settings.project_id, "foreign-project")
    elif change=="wrong_rest_id":
        described["id"] = True
    elif change=="wrong_unique_id":
        item["self_link_unique"] = item["self_link_unique"].replace("uniqueId=111", "uniqueId=112")
    elif change=="wrong_unique_name":
        item["self_link_unique"] = item["self_link_unique"].replace(f"/{name}?", "/forged?")
    elif change=="bad_disk_string":
        described["properties"]["disks"][0]["initializeParams"]["diskSizeGb"] = "32GB"
    elif change=="bool_disk_size":
        described["properties"]["disks"][0]["initializeParams"]["diskSizeGb"] = True
    elif change=="tier_one":
        described["properties"]["networkPerformanceConfig"] = {"totalEgressBandwidthTier": "TIER_1"}
    elif change=="fixed_cpu":
        described["properties"]["minCpuPlatform"] = "Intel Cascade Lake"
    elif change=="node_affinity":
        described["properties"]["scheduling"]["nodeAffinities"] = [{"key": "compute.googleapis.com/node-group-name", "operator": "IN", "values": ["fixed"]}]
    with pytest.raises(GcpProviderError,match="template role|immutable template"):
        driver._validate_template(described,item,"vm_a",deployment,trigger)


def test_selected_topology_readback_checks_zone_addresses_disk_and_image(tmp_path, monkeypatch):
    import hashlib
    from cloud_network_benchmark.contracts.common import Scenario
    from cloud_network_benchmark.providers.gcp import GcpProviderError
    deployment, driver = _cleanup_fixture(tmp_path)
    pinned = driver.settings.images[deployment.vm_a.region].rsplit("/", 1)[-1]
    def fake_disk(_args,action):
        if action.startswith("gcp-boot-disk"):
            role="vm_a" if action.endswith("vm_a") else "vm_b"
            suffix="a" if role=="vm_a" else "b"
            return {"id":"201","name":f"{driver._namespace(deployment)}-{suffix}-boot","zone":"europe-west3-a","sizeGb":"32","type":"zones/europe-west3-a/diskTypes/pd-balanced","sourceImage":f"projects/ubuntu-os-cloud/global/images/{pinned}","labels":{"cnb-run":hashlib.sha256(deployment.run_id.encode()).hexdigest()[:20],"cnb-deploy":hashlib.sha256(deployment.deployment_id.encode()).hexdigest()[:20]}}
        return {"architecture":"X86_64","name":pinned,"guestOsFeatures":[{"type":"GVNIC"}]}
    monkeypatch.setattr(driver,"_gcloud_json",fake_disk)

    scenarios = [
        (Scenario.SAME_ZONE, "europe-west3", "europe-west3-a", "10.80.1.11"),
        (Scenario.CROSS_ZONE, "europe-west3", "europe-west3-b", "10.80.1.11"),
        (Scenario.PLACEMENT_OPTIMIZATION, "europe-west3", "europe-west3-a", "10.80.1.11"),
        (Scenario.INTER_REGION, "europe-west2", "europe-west2-a", "10.81.1.11"),
    ]
    for scenario, region, zone, address in scenarios:
        vm_b = deployment.vm_b.model_copy(update={"region": region, "zone": zone})
        case = deployment.model_copy(update={"scenario": scenario, "vm_b": vm_b})
        instance = _instance_response(driver, case, "vm_b")
        assert driver._validate_instance(instance, case, "vm_b")["sizeGb"] == 32
        with pytest.raises(GcpProviderError,match="boot disk is missing"):
            driver._validate_instance({**instance,"disks":[*instance["disks"],{"boot":False,"source":"foreign-disk"}]},case,"vm_b")
        wrong = {**instance, "networkInterfaces": [{**instance["networkInterfaces"][0], "networkIP": "10.99.0.1"}]}
        with pytest.raises(GcpProviderError, match="private or public management address"):
            driver._validate_instance(wrong, case, "vm_b")
        no_public = {**instance, "networkInterfaces": [{**instance["networkInterfaces"][0], "accessConfigs": []}]}
        with pytest.raises(GcpProviderError, match="private or public management address"):
            driver._validate_instance(no_public, case, "vm_b")
    wrong_zone={**instance,"zone":"https://compute.googleapis.com/compute/v1/projects/test/zones/europe-west3-c"}
    with pytest.raises(GcpProviderError,match="differs from selected zone"):
        driver._validate_instance(wrong_zone,case,"vm_b")
    for unsupported in (
        {**instance, "networkPerformanceConfig": {"totalEgressBandwidthTier": "TIER_1"}},
        {**instance, "minCpuPlatform": "Intel Cascade Lake"},
        {**instance, "scheduling": {**instance["scheduling"], "nodeAffinities": [{"key": "compute.googleapis.com/node-group-name", "operator": "IN", "values": ["fixed"]}]}},
        {**instance, "networkInterfaces": [{**instance["networkInterfaces"][0], "network": f"https://www.googleapis.com/compute/v1/projects/foreign-project/global/networks/{driver._namespace(case)}-net"}]},
    ):
        with pytest.raises(GcpProviderError):
            driver._validate_instance(unsupported, case, "vm_b")
    def other_project_disk(_args,action):
        if action.startswith("gcp-boot-disk"):
            return {"id":"201","name":f"{driver._namespace(case)}-b-boot","zone":zone,"sizeGb":32,"type":"pd-balanced","sourceImage":f"projects/other-project/global/images/{pinned}","labels":{"cnb-run":hashlib.sha256(case.run_id.encode()).hexdigest()[:20],"cnb-deploy":hashlib.sha256(case.deployment_id.encode()).hexdigest()[:20]}}
        return {"architecture":"X86_64","name":pinned,"guestOsFeatures":[{"type":"GVNIC"}]}
    monkeypatch.setattr(driver,"_gcloud_json",other_project_disk)
    with pytest.raises(GcpProviderError,match="differs from selected pinned image"):
        driver._validate_instance(instance,case,"vm_b")
    def wrong_arch_disk(_args,action):
        if action.startswith("gcp-boot-disk"):
            return {"id":"201","name":f"{driver._namespace(case)}-b-boot","zone":zone,"sizeGb":32,"type":"pd-balanced","sourceImage":f"projects/ubuntu-os-cloud/global/images/{pinned}","labels":{"cnb-run":hashlib.sha256(case.run_id.encode()).hexdigest()[:20],"cnb-deploy":hashlib.sha256(case.deployment_id.encode()).hexdigest()[:20]}}
        return {"name":pinned}
    monkeypatch.setattr(driver,"_gcloud_json",wrong_arch_disk)
    with pytest.raises(GcpProviderError,match="not amd64"):
        driver._validate_instance(instance,case,"vm_b")
    def no_gvnic_disk(_args,action):
        if action.startswith("gcp-boot-disk"):
            return {"id":"201","name":f"{driver._namespace(case)}-b-boot","zone":zone,"sizeGb":32,"type":"pd-balanced","sourceImage":f"projects/ubuntu-os-cloud/global/images/{pinned}","labels":{"cnb-run":hashlib.sha256(case.run_id.encode()).hexdigest()[:20],"cnb-deploy":hashlib.sha256(case.deployment_id.encode()).hexdigest()[:20]}}
        return {"architecture":"X86_64","name":pinned,"guestOsFeatures":[]}
    monkeypatch.setattr(driver,"_gcloud_json",no_gvnic_disk)
    with pytest.raises(GcpProviderError,match="does not advertise the GvNIC"):
        driver._validate_instance(instance,case,"vm_b")


def test_both_role_observations_match_each_supported_scenario(tmp_path, monkeypatch):
    import hashlib
    from cloud_network_benchmark.contracts.common import Scenario
    deployment, driver = _cleanup_fixture(tmp_path)
    cases = [
        (Scenario.SAME_ZONE, "europe-west3", "europe-west3-a", "europe-west3", "europe-west3-a", "10.80.1.10", "10.80.1.11"),
        (Scenario.CROSS_ZONE, "europe-west3", "europe-west3-a", "europe-west3", "europe-west3-b", "10.80.1.10", "10.80.1.11"),
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
                suffix="a" if action.endswith("vm_a") else "b"
                return {"id":"201","name":f"{driver._namespace(case)}-{suffix}-boot","zone":f"zones/{zone}","sizeGb": "32", "type": f"zones/{zone}/diskTypes/pd-balanced", "sourceImage": image,"labels":{"cnb-run":hashlib.sha256(case.run_id.encode()).hexdigest()[:20],"cnb-deploy":hashlib.sha256(case.deployment_id.encode()).hexdigest()[:20]}}
            region = region_a if action.endswith("vm_a") else region_b
            return {"architecture": "X86_64", "name": driver.settings.images[region].rsplit("/", 1)[-1],"guestOsFeatures":[{"type":"GVNIC"}]}
        monkeypatch.setattr(driver, "_gcloud_json", readback)
        for role, zone, address in (("vm_a", zone_a, ip_a), ("vm_b", zone_b, ip_b)):
            instance = _instance_response(driver, case, role)
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
        if case.scenario in {Scenario.CROSS_ZONE, Scenario.INTER_REGION}:
            with pytest.raises(GcpProviderError, match="S2/S4"):
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
    assert "gcp-addresses-inventory-all-scopes" in actions
    assert "gcp-routes-inventory-all-scopes" in actions
    assert [item["id"] for item in inventory["instances"]] == ["101"]
    assert [item["id"] for item in inventory["disks"]] == ["201"]
    assert {item["name"] for item in inventory["support_resources"]} == {"known-template", f"{namespace}-net"}


def test_cleanup_inventory_uses_valid_gcloud_subnet_list_grammar(tmp_path):
    from cloud_network_benchmark.contracts.execution import CommandResult
    from cloud_network_benchmark.providers.gcp import GcpProviderDriver
    deployment, base = _cleanup_fixture(tmp_path)

    class GcloudGrammarRunner:
        # Simulate gcloud's command parser: invalid resource paths fail instead
        # of returning a plausible empty inventory.
        valid_lists = {
            ("compute", "instances", "list"), ("compute", "disks", "list"),
            ("compute", "addresses", "list"), ("compute", "networks", "subnets", "list"),
            ("compute", "instance-templates", "list"), ("compute", "networks", "list"),
            ("compute", "firewall-rules", "list"), ("compute", "routes", "list"),
            ("compute", "resource-policies", "list"),
        }

        def __init__(self):
            self.commands = []

        def run(self, request):
            now = datetime.now(timezone.utc)
            args = tuple(request.argv[1:])
            command = args[:args.index("--project")]
            self.commands.append(command)
            if command not in self.valid_lists:
                return CommandResult(action_id=request.action_id, outcome="failed", started_at=now,
                    finished_at=now, duration_seconds=0, exit_code=2, stdout="",
                    stderr="Invalid choice: 'subnetworks'. Did you mean 'networks'? ")
            return CommandResult(action_id=request.action_id, outcome="succeeded", started_at=now,
                finished_at=now, duration_seconds=0, exit_code=0, stdout="[]", stderr="")

    runner = GcloudGrammarRunner()
    driver = GcpProviderDriver(runner, base.settings)
    inventory = driver._inventory(deployment)
    assert inventory["pages_complete"] is True
    assert ("compute", "networks", "subnets", "list") in runner.commands
    assert ("compute", "subnetworks", "list") not in runner.commands


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
    driver._save(attempt,{"run_id":deployment.run_id,"deployment_id":deployment.deployment_id,"registered":True,"controller_session_id":CONTROLLER_SESSION_ID,"expiry":expiry.model_dump(mode="json"),"templates":templates,"binding_sha256":driver._binding_fingerprint(deployment),"reservation_id":deployment.reservation.value.reservation_id.value})
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
        if action=="gcp-instance_templates-inventory-all-scopes" and collection=="instance_templates": return [owned]
        if action=="gcp-disk-inventory-all-zones" and collection=="disks": return [owned]
        if action=="gcp-addresses-inventory-all-scopes" and collection=="addresses": return [owned]
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
