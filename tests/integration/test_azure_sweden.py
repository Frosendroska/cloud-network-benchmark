"""The bounded Swedish component and recovery; cloud responses are injected."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from cloud_network_benchmark import azure_provider as az
from tests.contract.test_azure_provider_contract import deployment, options
from tests.integration.test_azure_provider_flow import AutomationRunner, CleanupRunner, expiry_request

IMAGE = "Canonical:ubuntu-24_04-lts:server:24.04.202610250"


def sweden(root,tmp_path):
    d=deployment(root)
    d=d.model_copy(update={"vm_a":d.vm_a.model_copy(update={"region":"swedencentral"}),"vm_b":d.vm_b.model_copy(update={"region":"swedencentral"})})
    # Use one minute-aligned fake instant for the provider clock and reservation.
    # A real wall clock here made the +15 minute requested trigger floor up to
    # 59 seconds earlier while this fixture allowed only a 30 second workflow
    # margin, so unrelated Sweden success/recovery cases failed before reaching
    # their intended assertions.
    now=datetime.now(timezone.utc).replace(second=0,microsecond=0)
    reservation=d.reservation.value.model_copy(update={
        "cap_started_at":d.reservation.value.cap_started_at.model_copy(update={"value":now-timedelta(seconds=30)}),
        "expires_at":d.reservation.value.expires_at.model_copy(update={"value":now+timedelta(seconds=1170)}),
    })
    d=d.model_copy(update={"reservation":d.reservation.model_copy(update={"value":reservation})})
    o=options(tmp_path).model_copy(update={"selected_image":IMAGE,"retry_end_at":now+timedelta(minutes=16)})
    return d,o,now


class SwedenRunner(AutomationRunner):
    def run(self,req):
        response=super().run(req)
        if req.action_type=="image_preflight":
            d=json.loads(response.stdout)
            d["id"]=d["id"].replace("germanywestcentral","swedencentral");d["location"]="swedencentral"
            response=response.model_copy(update={"stdout":json.dumps(d)})
        elif req.action_type=="pair_output":
            d=json.loads(response.stdout)
            for row in d.values(): row["value"]["region"]="swedencentral"
            response=response.model_copy(update={"stdout":json.dumps(d)})
        elif req.action_type=="vm_image_observation":
            response=response.model_copy(update={"stdout":json.dumps({**json.loads(response.stdout),"storageProfile":{"imageReference":dict(zip(("publisher","offer","sku","exactVersion"),IMAGE.split(":")))}})})
        return response


@pytest.mark.parametrize("change",["experiment","context","mode","scenario","zone","mixed"])
def test_deployment_exception_is_bounded_even_for_direct_calls(repository_root,tmp_path,change):
    d,o,_=sweden(repository_root,tmp_path)
    if change=="experiment": d=d.model_copy(update={"experiment_id":"EXP-999"})
    elif change=="context": d=d.model_copy(update={"context":"standard"})
    elif change=="mode": d=d.model_copy(update={"mode":"short_study"})
    elif change=="scenario": d=d.model_copy(update={"scenario":"cross_zone"})
    elif change=="zone": d=d.model_copy(update={"vm_b":d.vm_b.model_copy(update={"zone":"2"})})
    else: d=d.model_copy(update={"vm_b":d.vm_b.model_copy(update={"region":"germanywestcentral"})})
    with pytest.raises(ValueError,match="scenario locality"):
        az.build_pair_inputs(d,o)
    assert not o.attempt_root.exists()


def test_minute_floor_that_breaks_complete_workflow_is_rejected_before_runner(repository_root,tmp_path):
    d,o,now=sweden(repository_root,tmp_path)
    runner=SwedenRunner(d,o,now)
    # Request is just before the next minute: flooring takes it from +14:59
    # to +14:00, while the original 900-second workflow bound ends at +14:30.
    request=expiry_request(d,o,now+timedelta(minutes=14,seconds=59))
    with pytest.raises(ValueError,match="complete workflow bound exceeds actual expiry trigger"):
        az.register_expiry(d,request,o,runner,clock=lambda:1.,utc_now=lambda:now)
    assert not runner.requests
    assert not o.attempt_root.exists()


def test_sweden_mapping_image_expiry_and_host_identity(repository_root,tmp_path,isolated_legacy_saved_plan_boundary):
    d,o,now=sweden(repository_root,tmp_path);runner=SwedenRunner(d,o,now)
    pair=az.build_pair_inputs(d,o)
    assert (pair["location"],pair["region_a"],pair["region_b"],pair["zone_a"],pair["zone_b"])==("swedencentral","swedencentral","swedencentral","1","1")
    assert not pair["create_ppg"] and not pair["create_peering"]
    assert pair["admin_username"]==d.vm_a.connection_user=="azureuser"
    expiry=az.register_expiry(d,expiry_request(d,o,now+timedelta(minutes=15)),o,runner,clock=lambda:1.,utc_now=lambda:now)
    assert expiry.state=="registered",expiry.failure
    original_expiry=(o.attempt_root/"expiry.auto.tfvars.json").read_bytes()
    assert json.loads(original_expiry)["location"]=="swedencentral"
    body=json.loads(next(q for q in runner.requests if q.action_type=="runbook_draft").argv[-1])
    assert body["location"]=="swedencentral"
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    result=az.provision(registered,o,runner,clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    assert result.deployment is not None,result.failures
    for vm in (result.deployment.vm_a,result.deployment.vm_b):
        assert vm.region=="swedencentral" and vm.zone=="1" and vm.image_identity==IMAGE
    image=next(q for q in runner.requests if q.action_type=="image_preflight")
    assert image.argv[image.argv.index("--location")+1]=="swedencentral"
    assert image.argv[image.argv.index("--urn")+1]==IMAGE
    assert [q.action_type for q in runner.requests].index("job_binding_readback") < [q.action_type for q in runner.requests].index("pair_apply")
    assert (o.attempt_root/"expiry.auto.tfvars.json").read_bytes()==original_expiry


@pytest.mark.parametrize("review_elapsed,anchor",[(46.0,None),(80.0,"trigger"),(21.0,"cap")])
def test_pair_apply_review_rechecks_full_budget_and_preserves_total_stage_deadline(
        repository_root,tmp_path,monkeypatch,isolated_legacy_saved_plan_boundary,review_elapsed,anchor):
    from cloud_network_benchmark.contracts.common import Evidence
    d,o,base=sweden(repository_root,tmp_path)
    # The usual pair-apply review starts with 839 seconds to trigger and a
    # complete post-registration allowance of 760 seconds. The successful
    # case retains only the original provision-stage remainder after 46s of
    # review. Cap- and trigger-exhaustion cases stop before dispatch.
    o=o.model_copy(update={"complete_workflow_seconds":810,
        "post_registration_workflow_seconds":760,"command_timeout_seconds":180,
        "plan_review_timeout_seconds":60,
        "cap_deadline_monotonic":782 if anchor=="cap" else 1000})
    d=d.model_copy(update={"provisioning_timeout_seconds":180})
    reservation=d.reservation.value.model_copy(update={
        "cap_started_at":d.reservation.value.cap_started_at.model_copy(update={"value":base-timedelta(seconds=30)}),
        "expires_at":d.reservation.value.expires_at.model_copy(update={"value":base+timedelta(seconds=1170)}),
    })
    d=d.model_copy(update={"reservation":d.reservation.model_copy(update={"value":reservation})})
    requested_trigger=base+timedelta(minutes=14,seconds=59)
    register_clock=[2.0]
    expiry=az.register_expiry(d,expiry_request(d,o,requested_trigger),o,
        SwedenRunner(d,o,base),clock=lambda:register_clock[0],utc_now=lambda:base)
    assert expiry.state=="registered",expiry.failure
    d=d.model_copy(update={"expiry":Evidence(value=expiry)})

    clock=[2.0]
    calls=[]
    budget_checks=[]
    def reviewed(request,execute,*,review_seconds,apply_timeout_seconds,before_apply,review_admission_path):
        assert review_admission_path is None
        calls.append((request.timeout_seconds,review_seconds,apply_timeout_seconds))
        clock[0]+=review_elapsed
        budget_checks.append("invoked")
        calls.append((before_apply(),))
        return execute(request.model_copy(update={"timeout_seconds":calls[-1][0]}))
    monkeypatch.setattr("cloud_network_benchmark.terraform_plan.reviewed_apply",reviewed)
    monkeypatch.setattr(az,"_verify_registered_expiry",lambda *_args:None)
    runner=SwedenRunner(d,o,base)
    result=az.provision(d,o,runner,clock=lambda:clock[0],
        utc_now=lambda:base+timedelta(seconds=clock[0]-1))
    expected_review_request=20.0 if anchor=="cap" else 79.0
    assert calls[0]==pytest.approx((expected_review_request,60,180))
    assert budget_checks==["invoked"]
    if anchor is None:
        assert result.deployment is not None,(result.failures,calls,[q.action_type for q in runner.requests])
        assert calls[1]==pytest.approx((134.0,))
        assert any(q.action_type=="pair_apply" for q in runner.requests)
    else:
        assert result.deployment is None and result.failures
        assert not any(q.action_type=="pair_apply" for q in runner.requests)


@pytest.mark.parametrize("failure",[None,"runbook_draft","pair_apply"])
def test_restarted_cleanup_uses_original_sweden_inputs_after_expiry(repository_root,tmp_path,monkeypatch,isolated_legacy_saved_plan_boundary,failure):
    d,o,now=sweden(repository_root,tmp_path);runner=SwedenRunner(d,o,now,fail_at=failure)
    expiry=az.register_expiry(d,expiry_request(d,o,now+timedelta(minutes=15)),o,runner,clock=lambda:1.,utc_now=lambda:now)
    if expiry.state=="registered":
        d=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
        result=az.provision(d,o,runner,clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
        if failure: assert result.failures
    else: assert failure=="runbook_draft"
    paths=[o.attempt_root/(name+".auto.tfvars.json") for name in ("pair","expiry")]
    original={p:p.read_bytes() for p in paths}
    # Simulate later source mappings, expired authority/quotes and changed defaults.
    monkeypatch.setattr(az,"build_pair_inputs",lambda *_: (_ for _ in ()).throw(AssertionError("cannot regenerate recovery inputs")))
    later=o.model_copy(update={"selected_image":"changed-source-image","post_registration_workflow_seconds":999999.,"cap_deadline_monotonic":1.})
    class Recover(CleanupRunner):
        def run(self,q):
            if "destroy" in q.argv:
                p=Path(next(a.split("=",1)[1] for a in q.argv if a.startswith("-var-file=")))
                assert p.read_bytes()==original[p] and json.loads(p.read_bytes())["location"]=="swedencentral"
            return super().run(q)
    cleanup_runner=Recover(d,later,now+timedelta(days=2))
    cleaned=az.destroy(d,later,cleanup_runner,clock=lambda:2000.,utc_now=lambda:now+timedelta(days=2))
    assert cleaned.cleanup_state=="succeeded",cleaned.failures
    destroys=[q for q in cleanup_runner.requests if "destroy" in q.argv]
    assert len(destroys)==2
    for q in destroys:
        assert "plan" not in q.argv and "apply" not in q.argv
        p=Path(next(a.split("=",1)[1] for a in q.argv if a.startswith("-var-file=")))
        assert p in original
        assert q.timeout_seconds<=later.command_timeout_seconds


@pytest.mark.parametrize("kind",["image","account","runbook"])
def test_wrong_region_authenticated_reads_block_pair(repository_root,tmp_path,isolated_legacy_saved_plan_boundary,kind):
    d,o,now=sweden(repository_root,tmp_path)
    class WrongRegion(SwedenRunner):
        def run(self,q):
            result=super().run(q)
            if q.action_type=={"image":"image_preflight","account":"expiry_account_recheck","runbook":"expiry_runbook_recheck"}[kind]:
                p=json.loads(result.stdout);p["location"]="germanywestcentral"
                result=result.model_copy(update={"stdout":json.dumps(p)})
            return result
    runner=WrongRegion(d,o,now)
    expiry=az.register_expiry(d,expiry_request(d,o,now+timedelta(minutes=15)),o,runner,clock=lambda:1.,utc_now=lambda:now)
    assert expiry.state=="registered"
    d=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    result=az.provision(d,o,runner,clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    assert result.failures and result.deployment is None
    assert not any(q.action_type=="pair_apply" for q in runner.requests)


@pytest.mark.parametrize("field,value",[("location","germanywestcentral"),("id","/subscriptions/foreign"),("zones",["2"]),("hardwareProfile",{"vmSize":"Standard_D8s_v5"}),("osProfile",{"adminUsername":"ubuntu"})])
def test_actual_vm_readback_must_match_sweden_identity_and_admin_user(repository_root,tmp_path,isolated_legacy_saved_plan_boundary,field,value):
    d,o,now=sweden(repository_root,tmp_path)
    class AlteredVm(SwedenRunner):
        def run(self,q):
            result=super().run(q)
            if q.action_type=="vm_image_observation":
                p=json.loads(result.stdout);p[field]=value
                result=result.model_copy(update={"stdout":json.dumps(p)})
            return result
    runner=AlteredVm(d,o,now)
    expiry=az.register_expiry(d,expiry_request(d,o,now+timedelta(minutes=15)),o,runner,clock=lambda:1.,utc_now=lambda:now)
    d=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    result=az.provision(d,o,runner,clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    assert result.deployment is None and result.failures and result.cleanup_required
    assert "authenticated Azure VM identity" in result.failures[0].message
