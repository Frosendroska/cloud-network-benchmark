"""Offline protocol tests for Azure's registered-expiry gate."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from cloud_network_benchmark.azure_provider import AzureRuntimeOptions, _slug, register_expiry, provision, destroy, build_pair_inputs
from cloud_network_benchmark.contracts.admission import ExpiryRequest
from cloud_network_benchmark.contracts.common import CommandOutcome
from cloud_network_benchmark.contracts.execution import CommandResult
from tests.fixtures.f06 import RESPONSE_FIXTURES
from tests.contract.test_azure_provider_contract import deployment, options


def test_missing_expiry_is_rejected_before_workspace_mutation(repository_root: Path, tmp_path: Path):
    d=deployment(repository_root); o=options(tmp_path, d.admission.priority)
    class Runner:
        def run(self, request): raise AssertionError("runner must not be called")
    with pytest.raises(ValueError, match="confirmed Azure deallocation"):
        provision(d,o,Runner(),clock=lambda:1.0,utc_now=lambda:datetime.now(timezone.utc))
    assert not o.attempt_root.exists()


class AutomationRunner:
    def __init__(self, deployment_input, options_input, now, fail_at=None, publish_delay=0, bad_key_for=None, wrong_vm_id_for=None,
                 stale_nonce_for=None, image_architecture="x64", wrong_role_definition=False,
                 image_publisher="Canonical", image_offer=None, image_sku=None, vm_image_mismatch=False,
                 image_os="Linux", module_recheck=None, marketplace_response=None):
        self.d=deployment_input; self.o=options_input; self.now=now; self.fail_at=fail_at; self.publish_delay=publish_delay; self.bad_key_for=bad_key_for; self.wrong_vm_id_for=wrong_vm_id_for
        self.stale_nonce_for=stale_nonce_for; self.image_architecture=image_architecture; self.wrong_role_definition=wrong_role_definition
        self.image_publisher=image_publisher; self.image_offer=image_offer; self.image_sku=image_sku; self.vm_image_mismatch=vm_image_mismatch
        self.image_os=image_os; self.module_recheck=module_recheck; self.marketplace_response=marketplace_response
        self.publish_reads=0; self.requests=[]; self.script=""; self.schedule={}; self.binding={}; self.tamper_recheck=None
        self.name=_slug(deployment_input)
        self.account=f"/subscriptions/{options_input.subscription_id}/resourceGroups/{self.name}/providers/Microsoft.Automation/automationAccounts/{self.name}-auto"
        self.assignment_id=f"{self.account}/providers/Microsoft.Authorization/roleAssignments/assignment"
        self.role_id=f"/subscriptions/{options_input.subscription_id}/resourceGroups/{self.name}/providers/Microsoft.Authorization/roleDefinitions/role"
        self.group_id=f"/subscriptions/{options_input.subscription_id}/resourceGroups/{self.name}"
    def run(self, request):
        self.requests.append(request)
        if request.action_type=="runbook_content": self.script=request.argv[request.argv.index("--body")+1]
        if request.action_type=="schedule_create": self.schedule=json.loads(request.argv[request.argv.index("--body")+1])
        if request.action_type=="job_binding_create": self.binding=json.loads(request.argv[request.argv.index("--body")+1])
        out=""
        if request.action_type=="expiry_outputs":
            values={"resource_group_id":self.group_id,"automation_account_id":self.account,"principal_id":"00000000-0000-0000-0000-000000000003","role_assignment_id":self.assignment_id,"role_definition_id":self.role_id}
            out=json.dumps({k:{"value":v} for k,v in values.items()})
        elif request.action_type=="pair_output":
            vms={"a":{"id":f"/subscriptions/{self.o.subscription_id}/resourceGroups/{self.name}/providers/Microsoft.Compute/virtualMachines/{self.name}-a","private_ipv4":"10.70.0.4","public_ipv4":"8.8.8.8","requested_image":self.o.selected_image,"size":"Standard_D4s_v5","region":"germanywestcentral","zone":"1","ppg":None,"accelerated_networking":True},
                 "b":{"id":f"/subscriptions/{self.o.subscription_id}/resourceGroups/{self.name}/providers/Microsoft.Compute/virtualMachines/{self.name}-b","private_ipv4":"10.70.0.5","public_ipv4":"1.1.1.1","requested_image":self.o.selected_image,"size":"Standard_D4s_v5","region":"germanywestcentral","zone":"1","ppg":None,"accelerated_networking":True}}
            if self.wrong_vm_id_for: vms[self.wrong_vm_id_for]["id"] += "-foreign"
            out=json.dumps({"vm_"+role:{"value":data} for role,data in vms.items()})
        elif request.action_type=="host_key_identity":
            target=request.argv[request.argv.index("--ids")+1]
            key="malformed-key" if target.endswith("-"+str(self.bad_key_for)) else "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE9F4huSqss0wx3n6f5+WJiEpePM3oXTbuOzBLRR5NCc guest"
            script=request.argv[request.argv.index("--scripts")+1]
            marker=script.split("CNB-AZ-HOSTKEY:",1)[1].split("\\n",1)[0]
            if target.endswith("-"+str(self.stale_nonce_for)): marker="stale-"+marker
            out=json.dumps({"value":[{"code":"ComponentStatus/StdOut/succeeded","level":"Info","message":"CNB-AZ-HOSTKEY:"+marker+"\n"+key+"\n"}]})
        elif request.action_type in {"runbook_readback","expiry_runbook_recheck"}:
            self.publish_reads+=1
            state="Published" if self.publish_reads>self.publish_delay else "Draft"
            payload=json.loads(json.dumps(RESPONSE_FIXTURES["runbook_published"] if state=="Published" else RESPONSE_FIXTURES["runbook_draft"]))
            payload.setdefault("properties",{}).update({"runbookType":"PowerShell","runtimeEnvironment":"PowerShell-5.1"})
            out=json.dumps(payload)
        elif request.action_type in {"published_content_readback","expiry_content_recheck"}: out=self.script
        elif request.action_type in {"schedule_readback","expiry_schedule_recheck"}:
            p=dict(self.schedule["properties"]); p["isEnabled"]=self.tamper_recheck!="schedule" if request.action_type=="expiry_schedule_recheck" else True; out=json.dumps({"properties":p})
        elif request.action_type in {"job_binding_readback","expiry_binding_recheck"}:
            self.job_id=request.argv[request.argv.index("--url")+1].split("/jobSchedules/")[1].split("?")[0]
            binding=json.loads(json.dumps(self.binding["properties"]))
            if request.action_type=="expiry_binding_recheck" and self.tamper_recheck=="binding": binding["parameters"]["resourcegroupname"]="foreign"
            if request.action_type=="expiry_binding_recheck" and self.tamper_recheck=="runOn": binding["runOn"]="unexpected-hybrid-worker"
            out=json.dumps({"id":f"{self.account}/jobSchedules/{self.job_id}","properties":binding})
        elif request.action_type in {"role_assignment_readback","expiry_role_recheck"}:
            scope=self.group_id+"/foreign" if request.action_type=="expiry_role_recheck" and self.tamper_recheck=="permission" else self.group_id
            role_definition="foreign-role" if self.wrong_role_definition else self.role_id
            out=json.dumps([{"id":self.assignment_id,"principalId":"00000000-0000-0000-0000-000000000003","scope":scope,"roleDefinitionId":role_definition}])
        elif request.action_type=="role_definition_readback":
            out=json.dumps([{"id":self.role_id,"assignableScopes":[self.group_id],"permissions":[{"actions":["Microsoft.Compute/virtualMachines/read","Microsoft.Compute/virtualMachines/instanceView/read","Microsoft.Compute/virtualMachines/deallocate/action"],"notActions":[],"dataActions":[],"notDataActions":[]}]}])
        elif request.action_type=="expiry_role_definition_recheck":
            out=json.dumps([{"id":self.role_id,"assignableScopes":[self.group_id],"permissions":[{"actions":["Microsoft.Compute/virtualMachines/read","Microsoft.Compute/virtualMachines/instanceView/read","Microsoft.Compute/virtualMachines/deallocate/action"],"notActions":[],"dataActions":[],"notDataActions":[]}]}])
        elif request.action_type=="automation_module_readback": out=json.dumps({"properties":{"provisioningState":"Succeeded","version":"3.0.0"}})
        elif request.action_type=="expiry_module_recheck": out=json.dumps(self.module_recheck if self.module_recheck is not None else {"properties":{"provisioningState":"Succeeded","version":"3.0.0"}})
        elif request.action_type in {"automation_account_readback","expiry_account_recheck"}: out=json.dumps({"identity":{"type":"SystemAssigned","principalId":"00000000-0000-0000-0000-000000000003"}})
        elif request.action_type=="image_preflight":
            if "--urn" in request.argv:
                _,offer,sku,version=request.argv[request.argv.index("--urn")+1].split(":")
                image_id=(f"/subscriptions/{self.o.subscription_id}/providers/Microsoft.Compute/locations/germanywestcentral/publishers/{self.image_publisher}/artifacttypes/vmimage/offers/{self.image_offer or offer}/skus/{self.image_sku or sku}/versions/{version}")
                payload={"id":image_id,"name":version,"location":"germanywestcentral","architecture":self.image_architecture,"osDiskImage":{"operatingSystem":self.image_os}}
                out=json.dumps(self.marketplace_response if self.marketplace_response is not None else payload)
            else:
                out=json.dumps({"architecture":self.image_architecture,"identifier":{"publisher":self.image_publisher,"offer":self.image_offer or "ubuntu2404","sku":self.image_sku or "ubuntu2404"}})
        elif request.action_type=="image_version_preflight":
            out=json.dumps({"id":request.argv[request.argv.index("--ids")+1],"name":"24.04.20261001"})
        elif request.action_type=="vm_image_observation":
            actual=self.o.selected_image+"-foreign" if self.vm_image_mismatch else self.o.selected_image
            out=json.dumps({"storageProfile":{"imageReference":{"id":actual}}})
        outcome=CommandOutcome.FAILED if request.action_type==self.fail_at else CommandOutcome.SUCCEEDED
        return CommandResult(action_id=request.action_id,outcome=outcome,started_at=self.now,finished_at=self.now,duration_seconds=0,
            exit_code=1 if outcome==CommandOutcome.FAILED else 0,stdout=out,stderr="injected failure" if outcome==CommandOutcome.FAILED else "")


class CleanupRunner(AutomationRunner):
    def __init__(self, *args, residual=False, secret_output="", blank_inventory=False, fail_delete_once=None,
                 account_present=True, present_children=None, role_definition_inventory="[]", role_assignment_inventory="[]", **kwargs):
        super().__init__(*args, **kwargs); self.residual=residual; self.secret_output=secret_output; self.blank_inventory=blank_inventory
        self.fail_delete_once=fail_delete_once; self.failed_delete=False; self.deleted_children=set(); self.absent_children=set()
        self.account_present=account_present
        self.role_definition_inventory=role_definition_inventory
        self.role_assignment_inventory=role_assignment_inventory
        self.present_children=set(present_children if present_children is not None else {"jobSchedules","schedules","runbooks"})
    def run(self, request):
        if request.action_type=="pair_destroy" and self.secret_output:
            self.requests.append(request)
            return CommandResult(action_id=request.action_id,outcome=CommandOutcome.SUCCEEDED,started_at=self.now,finished_at=self.now,duration_seconds=0,exit_code=0,stdout=self.secret_output,stderr="")
        if request.action_type=="vm_absence_by_id":
            self.requests.append(request)
            return CommandResult(action_id=request.action_id,outcome=CommandOutcome.FAILED,started_at=self.now,finished_at=self.now,duration_seconds=0,exit_code=1,stdout="",stderr="(ResourceNotFound) VM not found")
        if request.action_type=="pair_inventory_page_2":
            self.requests.append(request)
            resources=[] if not self.residual else [{"id":self.group_id+"/providers/Microsoft.Network/networkInterfaces/leftover","type":"Microsoft.Network/networkInterfaces"}]
            out=json.dumps({"value":resources})
        elif request.action_type=="pair_inventory":
            self.requests.append(request)
            url=request.argv[request.argv.index("--url")+1].split("?",1)[0]
            resources=[{"id":self.account,"type":"Microsoft.Automation/automationAccounts"}] if self.account_present else []
            out="" if self.blank_inventory else json.dumps({"value":resources,"nextLink":url+"?api-version=2021-04-01&$skiptoken=two"})
        elif request.action_type=="expiry_inventory": self.requests.append(request); out="false"
        elif request.action_type=="role_cleanup_inventory": self.requests.append(request); out=self.role_assignment_inventory
        elif request.action_type=="role_definition_cleanup_inventory": self.requests.append(request); out=self.role_definition_inventory
        elif request.action_type=="automation_account_absence":
            self.requests.append(request)
            return CommandResult(action_id=request.action_id,outcome=CommandOutcome.FAILED,started_at=self.now,finished_at=self.now,duration_seconds=0,exit_code=1,stdout="",stderr='{"error":{"code":"ResourceNotFound"}}')
        elif request.action_type.endswith("_before_delete"):
            self.requests.append(request)
            child_url=request.argv[request.argv.index("--url")+1]
            child=child_url.split("?",1)[0].rsplit("/",2)[-2]
            if child in self.deleted_children or child not in self.present_children:
                return CommandResult(action_id=request.action_id,outcome=CommandOutcome.FAILED,started_at=self.now,finished_at=self.now,duration_seconds=0,exit_code=1,stdout="",stderr='{"error":{"code":"ResourceNotFound"}}')
            out=json.dumps({"id":child_url.split("?",1)[0]})
        elif request.action_type.endswith("_delete"):
            self.requests.append(request)
            if request.action_type==self.fail_delete_once and not self.failed_delete:
                self.failed_delete=True
                return CommandResult(action_id=request.action_id,outcome=CommandOutcome.FAILED,started_at=self.now,finished_at=self.now,duration_seconds=0,exit_code=1,stdout="",stderr="injected unknown delete outcome")
            child=request.argv[request.argv.index("--url")+1].split("?",1)[0].rsplit("/",2)[-2]
            self.deleted_children.add(child); out="{}"
        elif request.action_type.endswith("_absence"):
            self.requests.append(request)
            collection=request.action_type.removeprefix("expiry_").removesuffix("_absence")
            child={"jobSchedules":"jobSchedules","schedules":"schedules","runbooks":"runbooks"}[collection]
            self.absent_children.add(child)
            return CommandResult(action_id=request.action_id,outcome=CommandOutcome.FAILED,started_at=self.now,finished_at=self.now,duration_seconds=0,exit_code=1,stdout="",stderr="(ResourceNotFound) not found")
        else: return super().run(request)
        return CommandResult(action_id=request.action_id,outcome=CommandOutcome.SUCCEEDED,started_at=self.now,finished_at=self.now,duration_seconds=0,exit_code=0,stdout=out,stderr="")


def expiry_request(d, o, trigger):
    slug=_slug(d); ids=[f"/subscriptions/{o.subscription_id}/resourceGroups/{slug}/providers/Microsoft.Compute/virtualMachines/{slug}-{role}" for role in ("a","b")]
    return ExpiryRequest(schema_version=2,run_id=d.run_id,deployment_id=d.deployment_id,provider="azure",action="deallocate",
        expires_at={"value":trigger},reservation_id={"value":d.reservation.value.reservation_id.value},resource_namespace=slug,target_selector=";".join(ids))


def test_registration_requires_lead_and_exact_target_ids(repository_root: Path, tmp_path: Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    request=ExpiryRequest(schema_version=2,run_id=d.run_id,deployment_id=d.deployment_id,provider="azure",action="deallocate",
        expires_at={"value":now+timedelta(seconds=120)},reservation_id={"value":"res-f06"},resource_namespace="attempt",target_selector="wrong")
    class Runner:
        def run(self, request): raise AssertionError("preflight must fail before external mutation")
    with pytest.raises(ValueError, match="300 seconds"):
        register_expiry(d,request,o,Runner(),clock=lambda:1.0,utc_now=lambda:now)
    assert not o.attempt_root.exists()


@pytest.mark.parametrize("poll", [0, 0.1, 301])
def test_runbook_poll_interval_must_match_bounded_integer_contract(repository_root: Path,tmp_path: Path,poll):
    with pytest.raises(ValueError):
        AzureRuntimeOptions.model_validate({**options(tmp_path).model_dump(),"poll_seconds":poll})


@pytest.mark.parametrize("invalid",["reservation_snapshot","expiry_cost","future_price"])
def test_registration_preflight_rejects_unadmitted_or_unpriced_requests_before_any_external_action(repository_root: Path, tmp_path: Path, invalid):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    request=expiry_request(d,o,now+timedelta(minutes=15))
    if invalid=="reservation_snapshot":
        o=AzureRuntimeOptions.model_validate({**o.model_dump(),"admission_evidence":{**o.admission_evidence,"source_sha256":"e"*64}})
    elif invalid=="expiry_cost":
        o=AzureRuntimeOptions.model_validate({**o.model_dump(),"expiry_cost_input":{**o.expiry_cost_input,"automation_rate_per_job_minute":0.2}})
    else:
        o=AzureRuntimeOptions.model_validate({**o.model_dump(),"expiry_cost_input":{**o.expiry_cost_input,"date":(now+timedelta(days=1)).date().isoformat()}})
    class Runner:
        def run(self, request): raise AssertionError("invalid request must be rejected before external actions")
    with pytest.raises(ValueError):
        register_expiry(d,request,o,Runner(),clock=lambda:1.0,utc_now=lambda:now)
    assert not o.attempt_root.exists()


def test_registration_records_readbacks_and_narrow_rbac(repository_root: Path, tmp_path: Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=16)})
    runner=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=15)),o,runner,clock=lambda:1.0,utc_now=lambda:now)
    assert expiry.state=="registered" and expiry.action=="deallocate"
    kinds=[x.action_type for x in runner.requests]
    assert kinds.index("expiry_prerequisites") < kinds.index("job_binding_create")
    assert "pair_apply" not in kinds
    assert expiry.provider_metadata.value["effective_rbac_unproved"] is True
    assert expiry.provider_metadata.value["binding_readback"]["resourcegroupname"]==_slug(d)
    journal=json.loads((o.attempt_root/"recovery.json").read_text())
    assert journal["expiry_state"]=="registered"
    assert journal["expiry"]["automation_account_id"] and journal["expiry"]["job_schedule_id"]


def test_registration_rejects_foreign_assigned_role_definition(repository_root: Path,tmp_path: Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=16)})
    runner=AutomationRunner(d,o,now,wrong_role_definition=True)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=15)),o,runner,clock=lambda:1.,utc_now=lambda:now)
    assert expiry.state=="failed"
    assert "pair_apply" not in [r.action_type for r in runner.requests]


def test_registration_polls_asynchronous_publication_within_bound(repository_root: Path, tmp_path: Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=16)})
    runner=AutomationRunner(d,o,now,publish_delay=1)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=15)),o,runner,clock=lambda:1.0,utc_now=lambda:now,sleep=lambda seconds:None)
    assert expiry.state=="registered"
    assert [r.action_type for r in runner.requests].count("runbook_readback")==2


def test_provision_rechecks_published_content_before_pair_apply(repository_root: Path, tmp_path: Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=16)})
    runner=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=15)),o,runner,clock=lambda:1.0,utc_now=lambda:now)
    changed=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    runner.script += "# changed after confirmation\n"
    result=provision(changed,o,runner,clock=lambda:2.0,utc_now=lambda:now+timedelta(seconds=1))
    assert result.cleanup_required and result.deployment is None
    assert any("content changed" in f.message for f in result.failures), result.failures
    assert "pair_apply" not in [r.action_type for r in runner.requests]


@pytest.mark.parametrize("evidence", ["schedule", "binding", "permission", "runOn"])
def test_provision_rechecks_schedule_binding_and_role_before_pair_apply(repository_root: Path, tmp_path: Path, evidence):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=16)})
    runner=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=15)),o,runner,clock=lambda:1.0,utc_now=lambda:now)
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    runner.tamper_recheck=evidence
    result=provision(registered,o,runner,clock=lambda:2.0,utc_now=lambda:now+timedelta(seconds=1))
    assert result.cleanup_required and result.deployment is None and result.failures
    assert "pair_apply" not in [r.action_type for r in runner.requests]


@pytest.mark.parametrize("module", [
    {},
    {"properties":{"provisioningState":"Failed","version":"3.0.0"}},
    {"properties":{"provisioningState":"Succeeded"}},
    {"properties":{"provisioningState":"Succeeded","version":"3.1.0"}},
])
def test_provision_rechecks_current_az_accounts_module_before_pair_apply(repository_root: Path,tmp_path: Path,module):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=16)})
    registration=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=15)),o,registration,clock=lambda:1.,utc_now=lambda:now)
    runner=AutomationRunner(d,o,now,module_recheck=module)
    runner.script=registration.script; runner.schedule=registration.schedule; runner.binding=registration.binding
    runner.publish_reads=registration.publish_reads
    result=provision(d.model_copy(update={"expiry":type(d.expiry)(value=expiry)}),o,runner,clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    actions=[request.action_type for request in runner.requests]
    assert result.cleanup_required and result.deployment is None
    assert "expiry_module_recheck" in actions
    assert "pair_apply" not in actions


def test_registered_pair_provision_normalizes_both_vms_and_materializes_authenticated_known_hosts(repository_root: Path, tmp_path: Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=16)})
    runner=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=15)),o,runner,clock=lambda:1.0,utc_now=lambda:now)
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    result=provision(registered,o,runner,clock=lambda:2.0,utc_now=lambda:now+timedelta(seconds=1))
    assert result.deployment is not None and result.cleanup_required, result.failures
    assert result.deployment.vm_a.connection and result.deployment.vm_b.connection, result.failures
    assert result.deployment.vm_a.connection.host=="8.8.8.8"
    assert result.deployment.vm_b.connection.host=="1.1.1.1"
    assert [r.action_type for r in runner.requests].index("pair_init") < [r.action_type for r in runner.requests].index("expiry_module_recheck")
    known_hosts=o.attempt_root/"known_hosts"
    assert known_hosts.read_text().splitlines()==[
        "8.8.8.8 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE9F4huSqss0wx3n6f5+WJiEpePM3oXTbuOzBLRR5NCc",
        "1.1.1.1 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE9F4huSqss0wx3n6f5+WJiEpePM3oXTbuOzBLRR5NCc"]
    assert known_hosts.stat().st_mode & 0o777 == 0o600
    artifact=o.attempt_root/"terraform-outputs.redacted.json"
    text=artifact.read_text()
    assert "authentication_reference" not in text and o.authentication_reference not in text and o.ssh_public_key not in text
    assert "12500" in text and "not_measured" in text
    assert [r.action_type for r in runner.requests].index("job_binding_readback") < [r.action_type for r in runner.requests].index("pair_apply")


def test_host_identity_failure_preserves_partial_normalized_output_without_connection(repository_root: Path, tmp_path: Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=16)})
    runner=AutomationRunner(d,o,now,bad_key_for="b")
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=15)),o,runner,clock=lambda:1.0,utc_now=lambda:now)
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    result=provision(registered,o,runner,clock=lambda:2.0,utc_now=lambda:now+timedelta(seconds=1))
    assert result.cleanup_required and result.deployment is not None
    assert result.deployment.vm_a.resource_id and result.deployment.vm_b.resource_id
    assert result.deployment.vm_a.connection is None and result.deployment.vm_b.connection is None
    assert result.deployment.vm_a.unavailable.value["reason"]
    assert any("malformed" in failure.message for failure in result.failures)


def test_stale_run_command_nonce_cannot_materialize_known_hosts(repository_root: Path,tmp_path: Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=16)})
    runner=AutomationRunner(d,o,now,stale_nonce_for="b")
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=15)),o,runner,clock=lambda:1.,utc_now=lambda:now)
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    result=provision(registered,o,runner,clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    assert result.failures and "nonce" in result.failures[0].message
    assert not (o.attempt_root/"known_hosts").exists()
    command_requests=[r for r in runner.requests if r.action_type=="host_key_identity"]
    assert [r.argv[r.argv.index("--ids")+1] for r in command_requests]==json.loads((o.attempt_root/"recovery.json").read_text())["vm_ids"]
    assert len({r.argv[r.argv.index("--scripts")+1].split("CNB-AZ-HOSTKEY:",1)[1].split("\\n",1)[0] for r in command_requests})==2


@pytest.mark.parametrize("architecture,publisher,offer", [("Arm64","Canonical","ubuntu2404"),("x64","Contoso","ubuntu2404"),("x64","Canonical","debian"),(None,"Canonical","ubuntu2404")])
def test_incompatible_image_metadata_rejects_before_pair_apply(repository_root: Path,tmp_path: Path,architecture,publisher,offer):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=16)})
    runner=AutomationRunner(d,o,now,image_architecture=architecture,image_publisher=publisher,image_offer=offer,image_sku=offer)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=15)),o,runner,clock=lambda:1.,utc_now=lambda:now)
    result=provision(d.model_copy(update={"expiry":type(d.expiry)(value=expiry)}),o,runner,clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    assert result.failures and "image metadata" in result.failures[0].message
    assert "pair_apply" not in [r.action_type for r in runner.requests]


def test_created_vm_image_identity_is_observed_not_copied_from_request(repository_root:Path,tmp_path:Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=16)})
    runner=AutomationRunner(d,o,now,vm_image_mismatch=True)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=15)),o,runner,clock=lambda:1.,utc_now=lambda:now)
    result=provision(d.model_copy(update={"expiry":type(d.expiry)(value=expiry)}),o,runner,clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    assert result.failures and "differs from the requested" in result.failures[0].message
    journal=json.loads((o.attempt_root/"recovery.json").read_text())
    output=json.loads(next(item["result"]["stdout"] for item in journal["actions"] if item["request"]["action_type"]=="pair_output"))
    assert "requested_image" in output["vm_a"]["value"]


def test_marketplace_image_metadata_uses_exact_x64_canonical_urn(repository_root:Path,tmp_path:Path):
    from cloud_network_benchmark.azure_provider import _validate_image_metadata, _workspace
    d=deployment(repository_root); o=options(tmp_path,d.admission.priority)
    selected="Canonical:ubuntu-24_04-lts:server:24.04.20261001"
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"selected_image":selected})
    runner=AutomationRunner(d,o,datetime.now(timezone.utc))
    image=_validate_image_metadata(o,runner,_workspace(o,d)[0],lambda:1.)
    assert image=={
        "id":f"/subscriptions/{o.subscription_id}/providers/Microsoft.Compute/locations/germanywestcentral/publishers/Canonical/artifacttypes/vmimage/offers/ubuntu-24_04-lts/skus/server/versions/24.04.20261001",
        "architecture":"x64","operating_system":"Linux","publisher":"Canonical","offer":"ubuntu-24_04-lts",
        "sku":"server","version":"24.04.20261001","source":"marketplace",
    }
    request=next(request for request in runner.requests if request.action_type=="image_preflight")
    assert request.argv==["az","vm","image","show","--location","germanywestcentral","--urn",selected,"--output","json"]


@pytest.mark.parametrize("response", [
    {"id":"/subscriptions/x/providers/Microsoft.Compute/locations/germanywestcentral/publishers/Canonical/artifacttypes/vmimage/offers/ubuntu-24_04-lts/skus/server/versions/24.04.20261001",
     "name":"24.04.20261001","osDiskImage":{"operatingSystem":"Linux"}},
    {"id":"/subscriptions/x/providers/Microsoft.Compute/locations/germanywestcentral/publishers/Canonical/artifacttypes/vmimage/offers/ubuntu-24_04-lts/skus/server/versions/24.04.20261001",
     "name":"24.04.20261002","architecture":"x64","osDiskImage":{"operatingSystem":"Linux"}},
    {"id":"/subscriptions/x/providers/Microsoft.Compute/locations/germanywestcentral/publishers/Canonical/artifacttypes/vmimage/offers/debian/skus/server/versions/24.04.20261001",
     "name":"24.04.20261001","architecture":"x64","osDiskImage":{"operatingSystem":"Linux"}},
    {"id":"/subscriptions/x/providers/Microsoft.Compute/locations/germanywestcentral/publishers/Canonical/artifacttypes/vmimage/offers/ubuntu-24_04-lts/skus/server/versions/24.04.20261001",
     "name":"24.04.20261001","architecture":"Arm64","osDiskImage":{"operatingSystem":"Linux"}},
    {"id":"/subscriptions/x/providers/Microsoft.Compute/locations/germanywestcentral/publishers/Canonical/artifacttypes/vmimage/offers/ubuntu-24_04-lts/skus/server/versions/24.04.20261001",
     "name":"24.04.20261001","architecture":"x64","osDiskImage":{"operatingSystem":"Windows"}},
])
def test_marketplace_compute_readback_requires_complete_matching_x64_linux_identity(repository_root:Path,tmp_path:Path,response):
    from cloud_network_benchmark.azure_provider import _validate_image_metadata, _workspace
    d=deployment(repository_root); o=options(tmp_path,d.admission.priority)
    selected="Canonical:ubuntu-24_04-lts:server:24.04.20261001"
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"selected_image":selected})
    runner=AutomationRunner(d,o,datetime.now(timezone.utc),marketplace_response=response)
    with pytest.raises(ValueError):
        _validate_image_metadata(o,runner,_workspace(o,d)[0],lambda:1.)


def test_absolute_nonzero_monotonic_origin_survives_mapping_registration_provision_and_cleanup(repository_root:Path,tmp_path:Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=16),"cap_deadline_monotonic":501001})
    assert build_pair_inputs(d,o)["scenario"]=="same_zone"
    registration=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=15)),o,registration,clock=lambda:500001.,utc_now=lambda:now)
    assert expiry.state=="registered"
    runner=AutomationRunner(d,o,now)
    runner.script=registration.script; runner.schedule=registration.schedule; runner.binding=registration.binding
    runner.publish_reads=registration.publish_reads
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    result=provision(registered,o,runner,clock=lambda:500002.,utc_now=lambda:now+timedelta(seconds=1))
    assert result.deployment is not None and "pair_apply" in [r.action_type for r in runner.requests], result.failures
    cleanup=destroy(registered,o,CleanupRunner(d,o,now),clock=lambda:500003.,utc_now=lambda:now+timedelta(seconds=2))
    assert cleanup.cleanup_state=="succeeded" and not cleanup.launch_blocked, cleanup.failures


def test_fresh_cap_is_rechecked_after_last_expiry_read_and_before_apply(repository_root: Path,tmp_path: Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=16),"complete_workflow_seconds":600})
    registration=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=15)),o,registration,clock=lambda:1.,utc_now=lambda:now)
    class AdvancingRunner(AutomationRunner):
        mono=2.
        def run(self,request):
            result=super().run(request)
            if request.action_type=="expiry_role_definition_recheck": self.mono=451.
            return result
    runner=AdvancingRunner(d,o,now)
    runner.script=registration.script; runner.schedule=registration.schedule; runner.binding=registration.binding
    runner.publish_reads=registration.publish_reads
    result=provision(d.model_copy(update={"expiry":type(d.expiry)(value=expiry)}),o,runner,clock=lambda:runner.mono,utc_now=lambda:now+timedelta(seconds=1))
    assert result.failures and "workflow" in result.failures[0].message
    assert "pair_apply" not in [r.action_type for r in runner.requests]


@pytest.mark.parametrize("fail_at",["pair_apply","pair_output"])
def test_pair_apply_and_output_failures_keep_cleanup_required_journal(repository_root: Path, tmp_path: Path, fail_at):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=16)})
    runner=AutomationRunner(d,o,now,fail_at=fail_at)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=15)),o,runner,clock=lambda:1.0,utc_now=lambda:now)
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    result=provision(registered,o,runner,clock=lambda:2.0,utc_now=lambda:now+timedelta(seconds=1))
    journal=json.loads((o.attempt_root/"recovery.json").read_text())
    assert result.cleanup_required and result.deployment is None and result.failures
    assert journal["creation_may_have_started"] is True and journal["pair_state"]=="creating"
    assert not (o.attempt_root/"terraform-outputs.redacted.json").exists()


def test_foreign_vm_output_identity_preserves_only_the_valid_role(repository_root: Path, tmp_path: Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=16)})
    runner=AutomationRunner(d,o,now,wrong_vm_id_for="b")
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=15)),o,runner,clock=lambda:1.0,utc_now=lambda:now)
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    result=provision(registered,o,runner,clock=lambda:2.0,utc_now=lambda:now+timedelta(seconds=1))
    assert result.cleanup_required and result.deployment is not None
    assert result.deployment.vm_a.resource_id and result.deployment.vm_a.connection is None
    assert result.deployment.vm_b.resource_id is None and result.deployment.vm_b.connection is None


@pytest.mark.parametrize("residual,expected", [(False,"succeeded"),(True,"failed")])
def test_cleanup_proves_vm_absence_and_blocks_expiry_teardown_for_residual_pair_resource(repository_root: Path, tmp_path: Path, residual, expected):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=16)})
    registration_runner=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=15)),o,registration_runner,clock=lambda:1.0,utc_now=lambda:now)
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    runner=CleanupRunner(d,o,now,residual=residual)
    result=destroy(registered,o,runner,clock=lambda:2.0,utc_now=lambda:now+timedelta(seconds=1))
    assert result.cleanup_state==expected
    assert result.launch_blocked==residual
    cleanup_actions=[r.action_type for r in runner.requests]
    assert cleanup_actions.index("vm_absence_by_id") < cleanup_actions.index("pair_inventory")
    for name in (("pair",) if residual else ("pair","expiry")):
        init=next(request for request in runner.requests if request.action_type==name+"_recovery_init")
        destroy_request=next(request for request in runner.requests if request.action_type==name+"_destroy")
        assert f"-chdir=terraform/azure/{name}" in init.argv
        assert f"-chdir=terraform/azure/{name}" in destroy_request.argv
        assert any(arg.endswith(f"{name}.auto.tfvars.json") for arg in destroy_request.argv)
    if residual:
            assert not any(kind.endswith("_delete") for kind in cleanup_actions)
    else:
            assert all(kind in cleanup_actions for kind in ("expiry_jobSchedules_delete","expiry_schedules_delete","expiry_runbooks_delete"))


@pytest.mark.parametrize("fail_at,account_present,children", [
    ("expiry_prerequisites",False,set()),
    ("runbook_draft",True,set()),
    ("runbook_content",True,{"runbooks"}),
    ("runbook_publish",True,{"runbooks"}),
    ("schedule_create",True,{"runbooks"}),
    ("job_binding_create",True,{"runbooks","schedules"}),
])
def test_partial_registration_is_cleaned_from_journal_without_registered_expiry_dto(repository_root:Path,tmp_path:Path,fail_at,account_present,children):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=16)})
    failed=AutomationRunner(d,o,now,fail_at=fail_at)
    evidence=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=15)),o,failed,clock=lambda:1.,utc_now=lambda:now)
    assert evidence.state=="failed"
    cleanup_runner=CleanupRunner(d,o,now,account_present=account_present,present_children=children)
    result=destroy(d,o,cleanup_runner,clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    assert result.cleanup_state=="succeeded"
    deleted={r.action_type.removeprefix("expiry_").removesuffix("_delete") for r in cleanup_runner.requests if r.action_type.endswith("_delete") and not r.action_type.endswith("_before_delete")}
    assert deleted==children


def test_cleanup_result_export_redacts_bounded_private_pem_sentinel(repository_root:Path,tmp_path:Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=16)})
    registration=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=15)),o,registration,clock=lambda:1.,utc_now=lambda:now)
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    sentinel="-----BEGIN RSA PRIVATE KEY-----cleanup-secret-----END RSA PRIVATE KEY-----"
    result=destroy(registered,o,CleanupRunner(d,o,now,secret_output=sentinel),clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    serialized=result.model_dump_json()
    assert "cleanup-secret" not in serialized and sentinel not in serialized and "[REDACTED]" in serialized


def test_cleanup_rejects_modified_private_terraform_inputs_before_runner_call(repository_root:Path,tmp_path:Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=16)})
    registration=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=15)),o,registration,clock=lambda:1.,utc_now=lambda:now)
    pair_vars=o.attempt_root/"pair.auto.tfvars.json"
    pair=json.loads(pair_vars.read_text()); pair["resource_group_name"]="foreign-group"; pair_vars.write_text(json.dumps(pair))
    assert pair_vars.stat().st_mode & 0o777==0o600
    class NoCloud:
        def run(self,request): raise AssertionError("altered recovery inputs must block all commands")
    result=destroy(d.model_copy(update={"expiry":type(d.expiry)(value=expiry)}),o,NoCloud(),clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    assert result.cleanup_state=="failed" and result.launch_blocked
    assert "no longer matches" in result.failures[0].message


def test_blank_complete_inventory_fails_closed_and_preserves_launch_block(repository_root:Path,tmp_path:Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=16)})
    registration=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=15)),o,registration,clock=lambda:1.,utc_now=lambda:now)
    runner=CleanupRunner(d,o,now,blank_inventory=True)
    result=destroy(d.model_copy(update={"expiry":type(d.expiry)(value=expiry)}),o,runner,clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    assert result.cleanup_state=="failed" and result.launch_blocked
    assert "pair_inventory_page_2" not in [r.action_type for r in runner.requests]
    assert not any(r.action_type.endswith("_delete") for r in runner.requests)


@pytest.mark.parametrize("inventory", ["[{\"malformed\":\"no id\"}]", "[null]", "[{\"id\":\"foreign-role\"}]"])
def test_malformed_or_unexpected_custom_role_inventory_retains_recovery_and_blocks_launch(repository_root:Path,tmp_path:Path,inventory):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=16)})
    registration=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=15)),o,registration,clock=lambda:1.,utc_now=lambda:now)
    runner=CleanupRunner(d,o,now,role_definition_inventory=inventory)
    result=destroy(d.model_copy(update={"expiry":type(d.expiry)(value=expiry)}),o,runner,clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    assert result.cleanup_state=="failed" and result.launch_blocked
    assert "role definition inventory" in result.failures[0].message
    assert (o.attempt_root/"recovery.json").exists()


@pytest.mark.parametrize("inventory", ["[null]", "[{\"id\":\"\"}]", "{\"value\":[]}"])
def test_malformed_role_assignment_inventory_also_blocks_cleanup(repository_root:Path,tmp_path:Path,inventory):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=16)})
    registration=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=15)),o,registration,clock=lambda:1.,utc_now=lambda:now)
    runner=CleanupRunner(d,o,now,role_assignment_inventory=inventory)
    result=destroy(d.model_copy(update={"expiry":type(d.expiry)(value=expiry)}),o,runner,clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    assert result.cleanup_state=="failed" and result.launch_blocked
    assert "role assignment inventory" in result.failures[0].message
    assert (o.attempt_root/"recovery.json").exists()


def test_interrupted_registration_reuses_observed_job_identity(repository_root:Path,tmp_path:Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=16)})
    runner=AutomationRunner(d,o,now,fail_at="runbook_publish")
    request=expiry_request(d,o,now+timedelta(minutes=15))
    first=register_expiry(d,request,o,runner,clock=lambda:1.,utc_now=lambda:now)
    first_id=json.loads((o.attempt_root/"recovery.json").read_text())["expiry"]["job_schedule_id"]
    second=register_expiry(d,request,o,runner,clock=lambda:1.,utc_now=lambda:now)
    second_id=json.loads((o.attempt_root/"recovery.json").read_text())["expiry"]["job_schedule_id"]
    assert first.state==second.state=="failed" and first_id==second_id


def test_repeated_cleanup_recovers_after_child_delete_interruption(repository_root:Path,tmp_path:Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=16)})
    registration=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=15)),o,registration,clock=lambda:1.,utc_now=lambda:now)
    runner=CleanupRunner(d,o,now,fail_delete_once="expiry_schedules_delete")
    first=destroy(d.model_copy(update={"expiry":type(d.expiry)(value=expiry)}),o,runner,clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    assert first.cleanup_state=="failed" and first.launch_blocked
    second=destroy(d,o,runner,clock=lambda:3.,utc_now=lambda:now+timedelta(seconds=2))
    assert second.cleanup_state=="succeeded" and not second.launch_blocked
    actions=[item.action_type for item in runner.requests]
    assert actions.count("expiry_jobSchedules_delete")==1
    assert actions.count("expiry_schedules_delete")==2


def test_missing_recovery_journal_blocks_launch_and_never_calls_cloud(repository_root: Path,tmp_path:Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    class Runner:
        def run(self,request): raise AssertionError("missing recovery evidence forbids cleanup API calls")
    result=destroy(d,o,Runner(),clock=lambda:1.0,utc_now=lambda:now)
    assert result.cleanup_state=="failed" and result.launch_blocked


def test_registration_failure_after_mutation_returns_failed_evidence_and_keeps_journal(repository_root: Path, tmp_path: Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=16)})
    runner=AutomationRunner(d,o,now,fail_at="runbook_publish")
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=15)),o,runner,clock=lambda:1.0,utc_now=lambda:now)
    assert expiry.state=="failed" and expiry.failure is not None
    journal=json.loads((o.attempt_root/"recovery.json").read_text())
    assert journal["expiry_state"]=="failed" and journal["failures"]


def test_deallocation_runbook_has_two_target_retries_and_no_start_create_delete():
    script=Path(__file__).resolve().parents[2]/"terraform/azure/expiry/deallocate.ps1"
    text=script.read_text()
    assert "foreach ($target in $targets)" in text
    assert "while ([DateTime]::UtcNow -lt $RetryEndAt" in text
    assert "/deallocate?api-version=2025-04-01" in text
    for forbidden in ("/start?", "/delete?", "New-AzVM", "Start-AzVM", "Remove-AzVM"):
        assert forbidden not in text


@pytest.mark.parametrize("mode", ["late","fail-a-once","deny-a-once"])
def test_runbook_fake_process_retries_late_and_creating_targets_without_controller(repository_root: Path, mode):
    pwsh=shutil.which("pwsh")
    if not pwsh: pytest.skip("PowerShell runtime is not installed")
    harness=repository_root/"tests/fixtures/f06/runbook-fake-harness.ps1"
    script=repository_root/"terraform/azure/expiry/deallocate.ps1"
    result=subprocess.run([pwsh,"-NoLogo","-NoProfile","-File",str(harness),"-RunbookPath",str(script),"-Mode",mode],capture_output=True,text=True,timeout=20,check=False)
    assert result.returncode==0, result.stderr
    evidence=json.loads(result.stdout)
    assert evidence["dispatch_and_completion_guaranteed"] is False
    states={item["resource_id"].rsplit("-",1)[-1]:item for item in evidence["targets"]}
    assert states["a"]["state"]==states["b"]["state"]=="deallocated"
    assert states["a"]["attempts"] >= 2
    assert states["b"]["attempts"] >= 2 # B is attempted even when A's first read throws
