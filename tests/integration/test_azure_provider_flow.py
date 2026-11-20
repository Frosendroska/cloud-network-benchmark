"""Offline protocol tests for Azure's registered-expiry gate."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from cloud_network_benchmark.azure_provider import AzureRuntimeOptions, ROLE_ACTIONS, _slug, _is_exact_not_found, _utc_schedule_matches, _azure_schedule_trigger, register_expiry, provision, destroy, build_pair_inputs
from cloud_network_benchmark.contracts.admission import ExpiryRequest
from cloud_network_benchmark.contracts.common import CommandOutcome
from cloud_network_benchmark.contracts.execution import CommandRequest, CommandResult
pytestmark = pytest.mark.usefixtures("isolated_legacy_saved_plan_boundary")

from tests.fixtures.f06 import RESPONSE_FIXTURES
from tests.contract.test_azure_provider_contract import deployment, options as _base_options

def options(tmp_path: Path, priority: str = "P0"):
    value=_base_options(tmp_path,priority)
    return AzureRuntimeOptions.model_validate({**value.model_dump(),"retry_end_at":datetime.now(timezone.utc)+timedelta(minutes=18)})


@pytest.mark.parametrize("stderr", [
    'ERROR: Not Found({"code":"NotFound", malformed})',
    'ERROR: Not Found({"code":"AuthorizationFailed","message":"denied"})',
    'ERROR: Not Found({"code":"Conflict","message":"conflict"})',
    'ERROR: Not Found({"error":{"code":"AuthorizationFailed","message":"denied"}})',
    'ERROR: Not Found({"error":{"code":"Conflict","message":"conflict"}})',
    'ERROR: not found',
    'not found',
])
def test_exact_not_found_parser_rejects_malformed_or_non_notfound_cli_diagnostics(stderr):
    now=datetime.now(timezone.utc)
    result=CommandResult(action_id="azure-cleanup-test",outcome=CommandOutcome.FAILED,
        started_at=now,finished_at=now,duration_seconds=0,exit_code=1,stdout="",stderr=stderr)
    assert not _is_exact_not_found(result)


@pytest.mark.parametrize("stderr", [
    'ERROR: Not Found({"error":{"code":"ResourceNotFound","message":"The resource was not found."}})',
    'ERROR: Not Found({"error":{"code":"NotFound","message":"The resource was not found."}})',
])
def test_exact_nested_azure_arm_not_found_is_recognized(stderr):
    now=datetime.now(timezone.utc)
    result=CommandResult(action_id="azure-cleanup-test",outcome=CommandOutcome.FAILED,
        started_at=now,finished_at=now,duration_seconds=0,exit_code=1,stdout="",stderr=stderr)
    assert _is_exact_not_found(result)


def test_azure_schedule_minute_floor_matches_recorded_arm_readback():
    requested=datetime.fromisoformat("2026-11-15T16:27:42.069196+00:00")
    effective=datetime.fromisoformat("2026-11-15T16:27:00+00:00")
    assert _azure_schedule_trigger(requested)==effective
    recorded={"timeZone":"Etc/UTC","startTime":"2026-11-15T16:27:00+00:00",
        "expiryTime":"2026-11-15T16:27:00+00:00","nextRun":"2026-11-15T16:27:00+00:00"}
    assert _utc_schedule_matches(recorded,effective)
    assert not _utc_schedule_matches({**recorded,"nextRun":"2026-11-15T16:28:00+00:00"},effective)
    assert not _utc_schedule_matches({**recorded,"expiryTime":"2026-11-15T16:26:00+00:00"},effective)
    assert not _utc_schedule_matches({**recorded,"startTime":"2026-11-15T16:27:01+00:00"},effective)
    assert not _utc_schedule_matches({**recorded,"timeZone":"Europe/Berlin"},effective)


@pytest.mark.parametrize("start_time", [None, "not-a-timestamp", "2026-11-15T16:27:00"])
def test_schedule_readback_requires_valid_timezone_aware_start_time(start_time):
    effective=datetime.fromisoformat("2026-11-15T16:27:00+00:00")
    props={"timeZone":"Etc/UTC","expiryTime":"2026-11-15T16:27:00+00:00",
        "nextRun":"2026-11-15T16:27:00+00:00"}
    props["startTime"]=start_time
    assert not _utc_schedule_matches(props,effective)


def test_schedule_readback_requires_start_time_field():
    effective=datetime.fromisoformat("2026-11-15T16:27:00+00:00")
    assert not _utc_schedule_matches({"timeZone":"UTC"},effective)


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
                 image_os="Linux", module_recheck=None, marketplace_response=None, content_corrupt=False,
                 expiry_state_overrides=None, schedule_timezone=None, schedule_overrides=None,
                 wrong_assignment_principal=False, wrong_assignment_id=False, extra_role_assignment=False):
        self.d=deployment_input; self.o=options_input; self.now=now; self.fail_at=fail_at; self.publish_delay=publish_delay; self.bad_key_for=bad_key_for; self.wrong_vm_id_for=wrong_vm_id_for
        self.stale_nonce_for=stale_nonce_for; self.image_architecture=image_architecture; self.wrong_role_definition=wrong_role_definition
        self.image_publisher=image_publisher; self.image_offer=image_offer; self.image_sku=image_sku; self.vm_image_mismatch=vm_image_mismatch
        self.image_os=image_os; self.module_recheck=module_recheck; self.marketplace_response=marketplace_response
        self.content_corrupt=content_corrupt
        self.schedule_timezone=schedule_timezone
        self.schedule_overrides=schedule_overrides or {}
        self.expiry_state_overrides=expiry_state_overrides or {}
        self.wrong_assignment_principal=wrong_assignment_principal; self.wrong_assignment_id=wrong_assignment_id
        self.extra_role_assignment=extra_role_assignment
        self.publish_reads=0; self.requests=[]; self.script=""; self.schedule={}; self.binding={}; self.tamper_recheck=None
        self.name=_slug(deployment_input)
        self.account=f"/subscriptions/{options_input.subscription_id}/resourceGroups/{self.name}/providers/Microsoft.Automation/automationAccounts/{self.name}-auto"
        self.group_id=f"/subscriptions/{options_input.subscription_id}/resourceGroups/{self.name}"
        self.assignment_id=f"{self.group_id}/providers/Microsoft.Authorization/roleAssignments/00000000-0000-0000-0000-000000000005"
        self.role_id=f"/subscriptions/{options_input.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/00000000-0000-0000-0000-000000000004"
    def run(self, request):
        self.requests.append(request)
        if (request.action_type in {"role_assignment_readback", "expiry_role_recheck", "role_cleanup_inventory"}
                and "--scope" in request.argv and "--all" in request.argv):
            return CommandResult(action_id=request.action_id,outcome=CommandOutcome.FAILED,started_at=self.now,
                finished_at=self.now,duration_seconds=0,exit_code=2,stdout="",
                stderr="ERROR: group or scope are not required when --all is used")
        if request.action_type=="runbook_content": self.script=request.argv[request.argv.index("--body")+1]
        if request.action_type=="schedule_create": self.schedule=json.loads(request.argv[request.argv.index("--body")+1])
        if request.action_type=="job_binding_create": self.binding=json.loads(request.argv[request.argv.index("--body")+1])
        out=""
        if request.action_type=="subscription_preflight":
            out=json.dumps({"id":self.o.subscription_id,"tenantId":self.o.tenant_id,"state":"Enabled"})
        elif request.action_type=="provider_registration_preflight":
            namespace=request.argv[request.argv.index("--namespace")+1]
            out=json.dumps({"id":f"/subscriptions/{self.o.subscription_id}/providers/{namespace}","namespace":namespace,"registrationState":"Registered"})
        elif request.action_type=="expiry_outputs":
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
            payload["location"]=self.d.vm_a.region
            payload.setdefault("properties",{}).update({"runbookType":"PowerShell","runtimeEnvironment":"PowerShell-5.1"})
            out=json.dumps(payload)
        elif request.action_type in {"published_content_readback","expiry_content_recheck"}:
            output=Path(request.argv[request.argv.index("--output-file")+1])
            content=self.script.encode()
            output.write_bytes(content[:-1]+b"!" if self.content_corrupt else content)
        elif request.action_type in {"schedule_readback","expiry_schedule_recheck"}:
            p=dict(self.schedule["properties"]); p["isEnabled"]=self.tamper_recheck!="schedule" if request.action_type=="expiry_schedule_recheck" else True
            if isinstance(p.get("startTime"),str): p["startTime"]=p["startTime"].replace("Z","+00:00")
            p["expiryTime"]=p.get("startTime")
            p["nextRun"]=p.get("startTime")
            if self.schedule_timezone is not None: p["timeZone"]=self.schedule_timezone
            p.update(self.schedule_overrides)
            if request.action_type=="expiry_schedule_recheck" and self.tamper_recheck=="timezone": p["timeZone"]="America/New_York"
            out=json.dumps({"properties":p})
        elif request.action_type in {"job_binding_readback","expiry_binding_recheck"}:
            self.job_id=request.argv[request.argv.index("--url")+1].split("/jobSchedules/")[1].split("?")[0]
            binding=json.loads(json.dumps(self.binding["properties"]))
            if request.action_type=="expiry_binding_recheck" and self.tamper_recheck=="binding": binding["parameters"]["resourcegroupname"]="foreign"
            if request.action_type=="expiry_binding_recheck" and self.tamper_recheck=="runOn": binding["runOn"]="unexpected-hybrid-worker"
            out=json.dumps({"id":f"{self.account}/jobSchedules/{self.job_id}","properties":binding})
        elif request.action_type in {"role_assignment_readback","expiry_role_recheck"}:
            scope=self.group_id+"/foreign" if request.action_type=="expiry_role_recheck" and self.tamper_recheck=="permission" else self.group_id
            role_definition="foreign-role" if self.wrong_role_definition else self.role_id
            principal="00000000-0000-0000-0000-000000000099" if (self.wrong_assignment_principal or (request.action_type=="expiry_role_recheck" and self.tamper_recheck=="principal")) else "00000000-0000-0000-0000-000000000003"
            assignment_id=self.assignment_id+"-unexpected" if self.wrong_assignment_id else self.assignment_id
            payload=[{"id":assignment_id,"principalId":principal,"scope":scope,"roleDefinitionId":role_definition}]
            if self.extra_role_assignment: payload.append({**payload[0],"id":self.assignment_id+"-unexpected"})
            out=json.dumps(payload)
        elif request.action_type=="role_definition_readback":
            out=json.dumps([{"id":self.role_id,"assignableScopes":[self.group_id],"permissions":[{"actions":["Microsoft.Compute/virtualMachines/read","Microsoft.Compute/virtualMachines/instanceView/read","Microsoft.Compute/virtualMachines/deallocate/action"],"notActions":[],"dataActions":[],"notDataActions":[]}]}])
        elif request.action_type=="expiry_role_definition_recheck":
            out=json.dumps([{"id":self.role_id,"assignableScopes":[self.group_id],"permissions":[{"actions":["Microsoft.Compute/virtualMachines/read","Microsoft.Compute/virtualMachines/instanceView/read","Microsoft.Compute/virtualMachines/deallocate/action"],"notActions":[],"dataActions":[],"notDataActions":[]}]}])
        elif request.action_type=="automation_module_readback": out=json.dumps({"properties":{"provisioningState":"Succeeded","version":"3.0.0"}})
        elif request.action_type=="expiry_module_recheck": out=json.dumps(self.module_recheck if self.module_recheck is not None else {"properties":{"provisioningState":"Succeeded","version":"3.0.0"}})
        elif request.action_type in {"automation_account_readback","expiry_account_recheck"}: out=json.dumps({"location":self.d.vm_a.region,"identity":{"type":"SystemAssigned","principalId":"00000000-0000-0000-0000-000000000003"}})
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
            vm=self.d.vm_a if request.argv[request.argv.index("--ids")+1].endswith("-a") else self.d.vm_b
            out=json.dumps({"id":request.argv[request.argv.index("--ids")+1],"location":vm.region,"zones":[vm.zone],"hardwareProfile":{"vmSize":vm.vm_shape},"osProfile":{"adminUsername":vm.connection_user},"storageProfile":{"imageReference":{"id":actual}}})
        outcome=CommandOutcome.FAILED if request.action_type==self.fail_at else CommandOutcome.SUCCEEDED
        if request.action_type=="expiry_prerequisites" and outcome==CommandOutcome.SUCCEEDED:
            self._write_expiry_state()
        if request.action_type=="expiry_destroy" and outcome==CommandOutcome.SUCCEEDED and (self.o.attempt_root/"expiry.tfstate").is_file():
            path=self.o.attempt_root/"expiry.tfstate"
            saved=json.loads(path.read_text())
            saved["resources"]=[]
            path.write_text(json.dumps(saved))
        return CommandResult(action_id=request.action_id,outcome=outcome,started_at=self.now,finished_at=self.now,duration_seconds=0,
            exit_code=1 if outcome==CommandOutcome.FAILED else 0,stdout=out,stderr="injected failure" if outcome==CommandOutcome.FAILED else "")

    def _write_expiry_state(self):
        definitions={"id":self.role_id,"role_definition_resource_id":self.role_id,
            "name":self.name+"-deallocate-only","scope":self.group_id,
            "assignable_scopes":[self.group_id],"permissions":[{"actions":[
                "Microsoft.Compute/virtualMachines/read",
                "Microsoft.Compute/virtualMachines/instanceView/read",
                "Microsoft.Compute/virtualMachines/deallocate/action"],
                "not_actions":[],"data_actions":None,"not_data_actions":None}]}
        assignment={"id":self.assignment_id,"scope":self.group_id,"principal_id":"00000000-0000-0000-0000-000000000003",
            "role_definition_id":self.role_id}
        group={"id":self.group_id,"name":self.name,"location":self.d.vm_a.region,
            "tags":{"deployment_id":self.d.deployment_id}}
        account={"id":self.account,"identity":[{"principal_id":"00000000-0000-0000-0000-000000000003"}]}
        resources=[]
        for kind,name,attrs in (("azurerm_resource_group","attempt",group),
                ("azurerm_automation_account","expiry",account),
                ("azurerm_role_definition","deallocate",definitions),
                ("azurerm_role_assignment","deallocate",assignment)):
            resources.append({"mode":"managed","type":kind,"name":name,"instances":[{"attributes":
                {**attrs,**self.expiry_state_overrides.get(kind+"."+name,{})}}]})
        (self.o.attempt_root/"expiry.tfstate").write_text(json.dumps({"version":4,"resources":resources}))


class CleanupRunner(AutomationRunner):
    def __init__(self, *args, residual=False, secret_output="", blank_inventory=False, fail_delete_once=None,
                 account_present=True, account_resource_id=None, account_absence="absent", parent_exists=True,
                 present_children=None, role_definition_inventory="[]", role_assignment_inventory="[]",
                 azure_cli_not_found_for=None, **kwargs):
        super().__init__(*args, **kwargs); self.residual=residual; self.secret_output=secret_output; self.blank_inventory=blank_inventory
        self.fail_delete_once=fail_delete_once; self.failed_delete=False; self.deleted_children=set(); self.absent_children=set()
        self.account_present=account_present
        self.parent_exists=parent_exists
        self.account_resource_id=account_resource_id
        self.account_absence=account_absence
        self.role_definition_inventory=role_definition_inventory
        self.role_assignment_inventory=role_assignment_inventory
        self.present_children=set(present_children if present_children is not None else {"jobSchedules","schedules","runbooks"})
        self.azure_cli_not_found_for=azure_cli_not_found_for
    def run(self, request):
        if request.action_type=="pair_destroy" and self.secret_output:
            self.requests.append(request)
            return CommandResult(action_id=request.action_id,outcome=CommandOutcome.SUCCEEDED,started_at=self.now,finished_at=self.now,duration_seconds=0,exit_code=0,stdout=self.secret_output,stderr="")
        if request.action_type=="vm_absence_by_id":
            self.requests.append(request)
            return CommandResult(action_id=request.action_id,outcome=CommandOutcome.FAILED,started_at=self.now,finished_at=self.now,duration_seconds=0,exit_code=1,stdout="",stderr="(ResourceNotFound) VM not found")
        if request.action_type=="parent_group_absence":
            self.requests.append(request); out=json.dumps(self.parent_exists)
        elif request.action_type=="pair_inventory_page_2":
            self.requests.append(request)
            resources=[] if not self.residual else [{"id":self.group_id+"/providers/Microsoft.Network/networkInterfaces/leftover","type":"Microsoft.Network/networkInterfaces"}]
            out=json.dumps({"value":resources})
        elif request.action_type=="pair_inventory":
            self.requests.append(request)
            url=request.argv[request.argv.index("--url")+1].split("?",1)[0]
            resources=[{"id":self.account_resource_id or self.account,"type":"Microsoft.Automation/automationAccounts"}] if self.account_present else []
            if not self.parent_exists:
                resources.append({"id":f"/subscriptions/{self.o.subscription_id}/resourceGroups/unrelated/providers/Microsoft.Network/virtualNetworks/keep","type":"Microsoft.Network/virtualNetworks"})
            out="" if self.blank_inventory else json.dumps({"value":resources,"nextLink":url+"?api-version=2021-04-01&$skiptoken=two"})
        elif request.action_type=="expiry_inventory": self.requests.append(request); out="false"
        elif request.action_type=="role_cleanup_inventory": self.requests.append(request); out=self.role_assignment_inventory
        elif request.action_type=="role_definition_cleanup_inventory": self.requests.append(request); out=self.role_definition_inventory
        elif request.action_type=="automation_account_absence":
            self.requests.append(request)
            if self.account_absence == "present":
                return CommandResult(action_id=request.action_id,outcome=CommandOutcome.SUCCEEDED,
                    started_at=self.now,finished_at=self.now,duration_seconds=0,exit_code=0,
                    stdout=json.dumps({"id":request.argv[request.argv.index("--ids")+1]}),stderr="")
            if self.account_absence == "unknown":
                return CommandResult(action_id=request.action_id,outcome=CommandOutcome.FAILED,
                    started_at=self.now,finished_at=self.now,duration_seconds=0,exit_code=1,
                    stdout="",stderr="injected inconclusive readback")
            return CommandResult(action_id=request.action_id,outcome=CommandOutcome.FAILED,started_at=self.now,finished_at=self.now,duration_seconds=0,exit_code=1,stdout="",stderr='{"error":{"code":"ResourceNotFound"}}')
        elif request.action_type.endswith("_before_delete"):
            self.requests.append(request)
            child_url=request.argv[request.argv.index("--url")+1]
            child=child_url.split("?",1)[0].rsplit("/",2)[-2]
            if child in self.deleted_children or child not in self.present_children:
                if child==self.azure_cli_not_found_for:
                    return CommandResult(action_id=request.action_id,outcome=CommandOutcome.FAILED,started_at=self.now,finished_at=self.now,duration_seconds=0,exit_code=1,stdout="",stderr='ERROR: Not Found({"code":"NotFound","message":"Job Schedule not found."})')
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


def test_minute_floor_that_breaks_300_second_lead_is_rejected_before_mutation(repository_root:Path,tmp_path:Path):
    d=deployment(repository_root); o=options(tmp_path,d.admission.priority)
    cap_start=d.reservation.value.cap_started_at.value
    now=cap_start+timedelta(seconds=30)
    effective=(now+timedelta(seconds=300)).replace(second=0,microsecond=0)
    requested=effective+timedelta(seconds=42,microseconds=69196)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    request=expiry_request(d,o,requested)
    class Runner:
        def run(self, request): raise AssertionError("minute-floored trigger has less than 300 seconds lead")
    with pytest.raises(ValueError,match="300 seconds"):
        register_expiry(d,request,o,Runner(),clock=lambda:1.,utc_now=lambda:now)
    assert not o.attempt_root.exists()


@pytest.mark.parametrize("poll", [0, 0.1, 301])
def test_runbook_poll_interval_must_match_bounded_integer_contract(repository_root: Path,tmp_path: Path,poll):
    with pytest.raises(ValueError):
        AzureRuntimeOptions.model_validate({**options(tmp_path).model_dump(),"poll_seconds":poll})


@pytest.mark.parametrize("invalid",["reservation_snapshot","expiry_cost","future_price"])
def test_registration_preflight_rejects_unadmitted_or_unpriced_requests_before_any_external_action(repository_root: Path, tmp_path: Path, invalid):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    request=expiry_request(d,o,now+timedelta(minutes=16))
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
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    runner=AutomationRunner(d,o,now)
    requested=now+timedelta(minutes=16)
    expiry=register_expiry(d,expiry_request(d,o,requested),o,runner,clock=lambda:1.0,utc_now=lambda:now)
    assert expiry.state=="registered" and expiry.action=="deallocate"
    kinds=[x.action_type for x in runner.requests]
    assert kinds.index("expiry_prerequisites") < kinds.index("job_binding_create")
    assert "pair_apply" not in kinds
    assert expiry.provider_metadata.value["effective_rbac_unproved"] is True
    effective=requested.replace(second=0,microsecond=0)
    assert expiry.expires_at.value==effective
    assert expiry.provider_metadata.value["requested_trigger"]==requested.isoformat()
    schedule=json.loads(runner.requests[[x.action_type for x in runner.requests].index("schedule_create")].argv[-1])
    assert schedule["properties"]["startTime"]==effective.isoformat().replace("+00:00","Z")
    assert expiry.provider_metadata.value["binding_readback"]["resourcegroupname"]==_slug(d)
    journal=json.loads((o.attempt_root/"recovery.json").read_text())
    assert journal["expiry_state"]=="registered"
    assert journal["expiry"]["automation_account_id"] and journal["expiry"]["job_schedule_id"]
    role_read=next(r for r in runner.requests if r.action_type=="role_assignment_readback")
    assert role_read.argv[role_read.argv.index("--scope")+1]==runner.group_id
    assert role_read.argv[role_read.argv.index("--assignee-object-id")+1]=="00000000-0000-0000-0000-000000000003"
    assert role_read.argv[role_read.argv.index("--fill-principal-name")+1]=="false"
    assert "--all" not in role_read.argv


def test_azure_role_assignment_cli_rejects_scope_with_all(repository_root: Path,tmp_path: Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    runner=AutomationRunner(d,o,now)
    request=CommandRequest(action_id="role-cli-grammar",action_type="role_assignment_readback",
        argv=["az","role","assignment","list","--scope",runner.group_id,"--all","--output","json"],
        cwd=str(repository_root),timeout_seconds=30)
    result=runner.run(request)
    assert result.outcome==CommandOutcome.FAILED
    assert result.stderr=="ERROR: group or scope are not required when --all is used"


@pytest.mark.parametrize("tamper", ["principal", "assignment_id", "extra"])
def test_registration_rejects_mismatched_or_ambiguous_role_assignment(repository_root:Path,tmp_path:Path,tamper):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    runner=AutomationRunner(d,o,now,wrong_assignment_principal=tamper=="principal",
        wrong_assignment_id=tamper=="assignment_id",extra_role_assignment=tamper=="extra")
    result=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,runner,clock=lambda:1.,utc_now=lambda:now)
    assert result.state=="failed"
    assert "role assignment readback mismatch" in result.failure.message


@pytest.mark.parametrize("timezone_name", ["UTC", "Etc/UTC"])
def test_registration_accepts_only_explicit_utc_schedule_labels(repository_root: Path, tmp_path: Path, timezone_name):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    runner=AutomationRunner(d,o,now,schedule_timezone=timezone_name)
    evidence=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,runner,clock=lambda:1.,utc_now=lambda:now)
    assert evidence.state=="registered", evidence.failure
    assert evidence.provider_metadata.value["schedule_readback"]["timeZone"]==timezone_name


@pytest.mark.parametrize("timezone_name,overrides", [
    ("Europe/Berlin", {}),
    ("America/New_York", {}),
    ("GMT", {}),
    ("UTC+00:00", {}),
    ("UTC", {"isEnabled":False}),
    ("UTC", {"frequency":"Daily"}),
])
def test_registration_rejects_non_utc_or_unsafe_schedule_readbacks(repository_root: Path,tmp_path: Path,timezone_name,overrides):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    runner=AutomationRunner(d,o,now,schedule_timezone=timezone_name,schedule_overrides=overrides)
    evidence=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,runner,clock=lambda:1.,utc_now=lambda:now)
    assert evidence.state=="failed"
    assert "schedule readback mismatch" in evidence.failure.message
    assert "job_binding_create" not in [request.action_type for request in runner.requests]


def test_registration_rejects_nonzero_start_time_offset_even_when_instant_matches(repository_root:Path,tmp_path:Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); trigger=now+timedelta(minutes=16); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    offset_start=trigger.astimezone(timezone(timedelta(hours=1))).isoformat()
    runner=AutomationRunner(d,o,now,schedule_timezone="UTC",schedule_overrides={"startTime":offset_start})
    evidence=register_expiry(d,expiry_request(d,o,trigger),o,runner,clock=lambda:1.,utc_now=lambda:now)
    assert evidence.state=="failed"
    assert "schedule readback mismatch" in evidence.failure.message


def test_registration_rejects_changed_schedule_instant(repository_root:Path,tmp_path:Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); trigger=now+timedelta(minutes=16); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    runner=AutomationRunner(d,o,now,schedule_timezone="UTC",schedule_overrides={"startTime":(trigger+timedelta(seconds=1)).isoformat()})
    evidence=register_expiry(d,expiry_request(d,o,trigger),o,runner,clock=lambda:1.,utc_now=lambda:now)
    assert evidence.state=="failed"
    assert "schedule readback mismatch" in evidence.failure.message


def test_registration_rejects_foreign_assigned_role_definition(repository_root: Path,tmp_path: Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    runner=AutomationRunner(d,o,now,wrong_role_definition=True)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,runner,clock=lambda:1.,utc_now=lambda:now)
    assert expiry.state=="failed"
    assert "pair_apply" not in [r.action_type for r in runner.requests]


def test_registration_polls_asynchronous_publication_within_bound(repository_root: Path, tmp_path: Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    runner=AutomationRunner(d,o,now,publish_delay=1)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,runner,clock=lambda:1.0,utc_now=lambda:now,sleep=lambda seconds:None)
    assert expiry.state=="registered"
    assert [r.action_type for r in runner.requests].count("runbook_readback")==2


def test_published_content_readback_uses_exact_file_bytes_and_rejects_corruption(repository_root:Path,tmp_path:Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    runner=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,runner,clock=lambda:1.,utc_now=lambda:now)
    assert expiry.state=="registered"
    readback=next(r for r in runner.requests if r.action_type=="published_content_readback")
    assert readback.argv[readback.argv.index("--output-file")+1]
    assert "--output" not in readback.argv
    assert not list(o.attempt_root.glob(".*.content"))
    journal=json.loads((o.attempt_root/"recovery.json").read_text())
    assert journal["expiry"]["metadata"]["published_content_sha256"]==hashlib.sha256(runner.script.encode()).hexdigest()

    corrupt_options=options(tmp_path/"corrupt",d.admission.priority)
    corrupt_options=AzureRuntimeOptions.model_validate({**corrupt_options.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    corrupt=AutomationRunner(d,corrupt_options,now,content_corrupt=True)
    failed=register_expiry(d,expiry_request(d,corrupt_options,now+timedelta(minutes=16)),corrupt_options,corrupt,
        clock=lambda:1.,utc_now=lambda:now)
    assert failed.state=="failed"
    assert "publication/content readback did not match" in failed.failure.message
    assert not any(r.action_type=="schedule_create" for r in corrupt.requests)


def test_provision_rechecks_published_content_before_pair_apply(repository_root: Path, tmp_path: Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    runner=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,runner,clock=lambda:1.0,utc_now=lambda:now)
    changed=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    runner.script += "# changed after confirmation\n"
    result=provision(changed,o,runner,clock=lambda:2.0,utc_now=lambda:now+timedelta(seconds=1))
    assert result.cleanup_required and result.deployment is None
    assert any("content changed" in f.message for f in result.failures), result.failures
    assert "pair_apply" not in [r.action_type for r in runner.requests]


@pytest.mark.parametrize("evidence", ["schedule", "binding", "permission", "principal", "runOn", "timezone", "requested_trigger"])
def test_provision_rechecks_schedule_binding_and_role_before_pair_apply(repository_root: Path, tmp_path: Path, evidence):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    runner=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,runner,clock=lambda:1.0,utc_now=lambda:now)
    if evidence=="requested_trigger":
        metadata={**expiry.provider_metadata.value,"requested_trigger":(datetime.fromisoformat(
            expiry.provider_metadata.value["requested_trigger"])+timedelta(minutes=1)).isoformat()}
        expiry=expiry.model_copy(update={"provider_metadata":type(expiry.provider_metadata)(value=metadata)})
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    runner.tamper_recheck=evidence if evidence!="requested_trigger" else None
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
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    registration=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,registration,clock=lambda:1.,utc_now=lambda:now)
    runner=AutomationRunner(d,o,now,module_recheck=module)
    runner.script=registration.script; runner.schedule=registration.schedule; runner.binding=registration.binding
    runner.publish_reads=registration.publish_reads
    result=provision(d.model_copy(update={"expiry":type(d.expiry)(value=expiry)}),o,runner,clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    actions=[request.action_type for request in runner.requests]
    assert result.cleanup_required and result.deployment is None
    assert "expiry_module_recheck" in actions
    assert "pair_apply" not in actions


@pytest.mark.parametrize("timezone_name", ["UTC", "Etc/UTC"])
def test_registered_pair_provision_normalizes_both_vms_and_materializes_authenticated_known_hosts(repository_root: Path, tmp_path: Path, timezone_name):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    runner=AutomationRunner(d,o,now,schedule_timezone=timezone_name)
    requested=now+timedelta(minutes=16,seconds=42,microseconds=69196)
    expiry=register_expiry(d,expiry_request(d,o,requested),o,runner,clock=lambda:1.0,utc_now=lambda:now)
    effective=_azure_schedule_trigger(requested)
    assert expiry.expires_at.value==effective
    assert expiry.provider_metadata.value["requested_trigger"]==requested.isoformat()
    assert _utc_schedule_matches(expiry.provider_metadata.value["schedule_readback"],effective)
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


def _use_consolidated_run_command_response(runner, *, code="ProvisioningState/succeeded", level="Info", stderr="", duplicate=False, nonce_mode="valid", key_mode="valid", preamble="Enable succeeded:\n", legacy_two_status=False, frame_mode="valid"):
    original=runner.run
    def run(request):
        result=original(request)
        if request.action_type=="host_key_identity":
            payload=json.loads(result.stdout)
            status=payload["value"][0]
            status["code"]=code; status["level"]=level
            message=status["message"]
            if nonce_mode=="missing": message=message.replace("CNB-AZ-HOSTKEY:","CNB-AZ-OTHER:")
            elif nonce_mode=="duplicate": message += message
            elif nonce_mode=="stale": message=message.replace("CNB-AZ-HOSTKEY:","CNB-AZ-HOSTKEY:stale-")
            if key_mode=="malformed": message=message.rsplit("\n",2)[0]+"\nnot-a-key\n"
            if legacy_two_status:
                status["code"]="ComponentStatus/StdOut/succeeded"
                status["message"]=message
                payload["value"].append({"code":"ComponentStatus/StdErr/succeeded","level":"Info","message":""})
            else:
                status["message"]=preamble+"[stdout]\n"+message+"[stderr]\n"+stderr
                if frame_mode=="duplicate": status["message"]=status["message"].replace("[stderr]","[stdout]\n[stderr]")
                elif frame_mode=="missing": status["message"]=status["message"].replace("[stdout]","")
            if duplicate: payload["value"].append(dict(status))
            return result.model_copy(update={"stdout":json.dumps(payload)})
        return result
    runner.run=run


def _registered_pair_result(repository_root, tmp_path, runner_factory=AutomationRunner, *, response=None):
    d=deployment(repository_root).model_copy(update={"provisioning_timeout_seconds":180}); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    runner=runner_factory(d,o,now)
    if response is not None: _use_consolidated_run_command_response(runner,**response)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16,seconds=42)),o,runner,clock=lambda:1.,utc_now=lambda:now)
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    clock=(lambda:runner.mono) if hasattr(runner,"mono") else (lambda:2.)
    result=provision(registered,o,runner,clock=clock,utc_now=lambda:now+timedelta(seconds=1))
    return result,runner,o


def test_consolidated_success_response_authenticates_both_hosts_on_create_pair_path(repository_root:Path,tmp_path:Path):
    # Structural synthetic response: the original post-cleanup response body was not retained.
    result,runner,o=_registered_pair_result(repository_root,tmp_path,response={})
    assert result.deployment is not None and not result.failures
    assert len([r for r in runner.requests if r.action_type=="host_key_identity"])==2
    assert len((o.attempt_root/"known_hosts").read_text().splitlines())==2


def test_legacy_stdout_and_empty_stderr_success_statuses_remain_supported(repository_root:Path,tmp_path:Path):
    result,runner,o=_registered_pair_result(repository_root,tmp_path,response={"legacy_two_status":True})
    assert result.deployment is not None and not result.failures
    assert len([r for r in runner.requests if r.action_type=="host_key_identity"])==2
    assert len((o.attempt_root/"known_hosts").read_text().splitlines())==2


@pytest.mark.parametrize("response",[
    {"code":"ProvisioningState/failed"},
    {"code":"ProvisioningState/unknown"},
    {"level":"Error"},
    {"stderr":"unexpected stderr"},
    {"duplicate":True},
    {"nonce_mode":"missing"},
    {"nonce_mode":"duplicate"},
    {"nonce_mode":"stale"},
    {"key_mode":"malformed"},
    {"frame_mode":"duplicate"},
    {"frame_mode":"missing"},
])
def test_consolidated_run_command_rejects_failure_unknown_and_mixed_responses(repository_root:Path,tmp_path:Path,response):
    result,_,o=_registered_pair_result(repository_root,tmp_path,response=response)
    assert result.failures and result.deployment is not None
    assert not (o.attempt_root/"known_hosts").exists()
    assert not (o.attempt_root/"terraform-outputs.redacted.json").exists()


def test_late_host_trust_completion_cannot_establish_pair_identity(repository_root:Path,tmp_path:Path):
    class LateRunner(AutomationRunner):
        mono=2.
        image_observations=0
        host_commands=0
        def run(self,request):
            result=super().run(request)
            if request.action_type=="vm_image_observation":
                self.image_observations+=1
                self.mono=143.524726 if self.image_observations==1 else self.mono+2.691137
            if request.action_type=="host_key_identity":
                self.host_commands+=1
                # The second command duration is synthetic, set equal to the first observed 33.6501576s.
                self.mono+=33.6501576
            return result
    result,runner,o=_registered_pair_result(repository_root,tmp_path,LateRunner,response={})
    assert result.failures and "provision-stage deadline" in result.failures[0].message
    assert runner.host_commands==2 and runner.mono>180
    assert not (o.attempt_root/"known_hosts").exists()
    assert not (o.attempt_root/"terraform-outputs.redacted.json").exists()


def test_host_identity_failure_preserves_partial_normalized_output_without_connection(repository_root: Path, tmp_path: Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    runner=AutomationRunner(d,o,now,bad_key_for="b")
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,runner,clock=lambda:1.0,utc_now=lambda:now)
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    result=provision(registered,o,runner,clock=lambda:2.0,utc_now=lambda:now+timedelta(seconds=1))
    assert result.cleanup_required and result.deployment is not None
    assert result.deployment.vm_a.resource_id and result.deployment.vm_b.resource_id
    assert result.deployment.vm_a.connection is None and result.deployment.vm_b.connection is None
    assert result.deployment.vm_a.unavailable.value["reason"]
    assert any("malformed" in failure.message for failure in result.failures)


def test_stale_run_command_nonce_cannot_materialize_known_hosts(repository_root: Path,tmp_path: Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    runner=AutomationRunner(d,o,now,stale_nonce_for="b")
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,runner,clock=lambda:1.,utc_now=lambda:now)
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
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    runner=AutomationRunner(d,o,now,image_architecture=architecture,image_publisher=publisher,image_offer=offer,image_sku=offer)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,runner,clock=lambda:1.,utc_now=lambda:now)
    result=provision(d.model_copy(update={"expiry":type(d.expiry)(value=expiry)}),o,runner,clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    assert result.failures and "image metadata" in result.failures[0].message
    assert "pair_apply" not in [r.action_type for r in runner.requests]


def test_created_vm_image_identity_is_observed_not_copied_from_request(repository_root:Path,tmp_path:Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    runner=AutomationRunner(d,o,now,vm_image_mismatch=True)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,runner,clock=lambda:1.,utc_now=lambda:now)
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
    image=_validate_image_metadata(o,runner,_workspace(o,d)[0],lambda:1.,d.vm_a.region)
    assert image=={
        "id":f"/subscriptions/{o.subscription_id}/providers/Microsoft.Compute/locations/germanywestcentral/publishers/Canonical/artifacttypes/vmimage/offers/ubuntu-24_04-lts/skus/server/versions/24.04.20261001",
        "architecture":"x64","operating_system":"Linux","publisher":"Canonical","offer":"ubuntu-24_04-lts",
        "sku":"server","version":"24.04.20261001","source":"marketplace",
    }
    request=next(request for request in runner.requests if request.action_type=="image_preflight")
    assert request.argv==["az","vm","image","show","--subscription",o.subscription_id,"--location","germanywestcentral","--urn",selected,"--output","json"]


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
        _validate_image_metadata(o,runner,_workspace(o,d)[0],lambda:1.,d.vm_a.region)


def test_absolute_nonzero_monotonic_origin_survives_mapping_registration_provision_and_cleanup(repository_root:Path,tmp_path:Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18),"cap_deadline_monotonic":501001})
    assert build_pair_inputs(d,o)["scenario"]=="same_zone"
    registration=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,registration,clock=lambda:500001.,utc_now=lambda:now)
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
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18),"complete_workflow_seconds":600})
    registration=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,registration,clock=lambda:1.,utc_now=lambda:now)
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
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    runner=AutomationRunner(d,o,now,fail_at=fail_at)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,runner,clock=lambda:1.0,utc_now=lambda:now)
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    result=provision(registered,o,runner,clock=lambda:2.0,utc_now=lambda:now+timedelta(seconds=1))
    journal=json.loads((o.attempt_root/"recovery.json").read_text())
    assert result.cleanup_required and result.deployment is None and result.failures
    assert journal["creation_may_have_started"] is True and journal["pair_state"]=="creating"
    assert not (o.attempt_root/"terraform-outputs.redacted.json").exists()


def test_foreign_vm_output_identity_preserves_only_the_valid_role(repository_root: Path, tmp_path: Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    runner=AutomationRunner(d,o,now,wrong_vm_id_for="b")
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,runner,clock=lambda:1.0,utc_now=lambda:now)
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    result=provision(registered,o,runner,clock=lambda:2.0,utc_now=lambda:now+timedelta(seconds=1))
    assert result.cleanup_required and result.deployment is not None
    assert result.deployment.vm_a.resource_id and result.deployment.vm_a.connection is None
    assert result.deployment.vm_b.resource_id is None and result.deployment.vm_b.connection is None


@pytest.mark.parametrize("residual,expected", [(False,"succeeded"),(True,"failed")])
def test_cleanup_proves_vm_absence_and_blocks_expiry_teardown_for_residual_pair_resource(repository_root: Path, tmp_path: Path, residual, expected):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    registration_runner=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,registration_runner,clock=lambda:1.0,utc_now=lambda:now)
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
        assert any(arg.endswith(f"/terraform/azure/{name}") for arg in init.argv)
        assert any(arg.endswith(f"/terraform/azure/{name}") for arg in destroy_request.argv)
        assert any(arg.endswith(f"{name}.auto.tfvars.json") for arg in destroy_request.argv)
    if residual:
            assert not any(kind.endswith("_delete") for kind in cleanup_actions)
    else:
            assert all(kind in cleanup_actions for kind in ("expiry_jobSchedules_delete","expiry_schedules_delete","expiry_runbooks_delete"))


@pytest.mark.parametrize("case", ["foreign_predelete_account", "account_still_present", "unknown_account_readback"])
def test_cleanup_never_proves_absence_without_exact_account_removal(
        repository_root: Path, tmp_path: Path, case):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    registration_runner=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,
        registration_runner,clock=lambda:1.0,utc_now=lambda:now)
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    kwargs={}
    if case == "foreign_predelete_account":
        kwargs["account_resource_id"] = registration_runner.group_id + "/providers/Microsoft.Automation/automationAccounts/foreign"
    elif case == "account_still_present":
        kwargs["account_absence"] = "present"
    else:
        kwargs["account_absence"] = "unknown"
    runner=CleanupRunner(d,o,now,**kwargs)
    result=destroy(registered,o,runner,clock=lambda:2.0,utc_now=lambda:now+timedelta(seconds=1))
    assert result.cleanup_state == "failed"
    assert result.launch_blocked is True
    if case == "foreign_predelete_account":
        assert not any(request.action_type == "automation_account_absence" for request in runner.requests)
        assert any("pair resources remain" in failure.message for failure in result.failures)
    else:
        assert any(request.action_type == "automation_account_absence" for request in runner.requests)
        expected_detail = ("Automation account absence is not proven" if case == "account_still_present"
            else "automation_account_absence failed")
        assert any(expected_detail in failure.message for failure in result.failures)


def test_cleanup_with_absent_parent_uses_original_subscription_inventory_and_vm_ids(repository_root:Path,tmp_path:Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    registration=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,registration,clock=lambda:1.,utc_now=lambda:now)
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    runner=CleanupRunner(d,o,now,parent_exists=False,account_present=False)
    result=destroy(registered,o,runner,clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    assert result.cleanup_state=="succeeded" and not result.launch_blocked, result.failures
    inventory=next(r for r in runner.requests if r.action_type=="pair_inventory")
    url=inventory.argv[inventory.argv.index("--url")+1]
    assert url==f"https://management.azure.com/subscriptions/{o.subscription_id}/resources?api-version=2021-04-01"
    queried=[r.argv[r.argv.index("--ids")+1] for r in runner.requests if r.action_type=="vm_absence_by_id"]
    assert queried==[f"/subscriptions/{o.subscription_id}/resourceGroups/{_slug(d)}/providers/Microsoft.Compute/virtualMachines/{_slug(d)}-{role}" for role in ("a","b")]
    assert not any(r.action_type.endswith("_delete") for r in runner.requests)


def test_cleanup_accepts_subscription_scoped_custom_role_from_original_terraform_output(repository_root:Path,tmp_path:Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"complete_workflow_seconds":600,"post_registration_workflow_seconds":600})
    from cloud_network_benchmark.lifecycle_cleanup import cleanup_saved_provider, save_cleanup_settings
    request=expiry_request(d,o,d.reservation.value.expires_at.value-timedelta(seconds=300))
    registration=AutomationRunner(d,o,now,fail_at="runbook_publish")
    evidence=register_expiry(d,request,o,registration,clock=lambda:1.,utc_now=lambda:now)
    assert evidence.state=="failed"
    journal=json.loads((o.attempt_root/"recovery.json").read_text())
    expected_role=f"/subscriptions/{o.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/00000000-0000-0000-0000-000000000004"
    assert journal["expiry"]["role_definition_id"]==expected_role
    saved=save_cleanup_settings(provider="azure",deployment=d,request=request,
        reservation=d.reservation.value,provider_lead_seconds=300.,settings=o,private_root=tmp_path/"f09")
    record=SimpleNamespace(provider="azure",deployment_id=d.deployment_id,
        journal_reference=str(o.attempt_root/"recovery.json"),cleanup_settings_reference=str(saved))
    cleanup=CleanupRunner(d,o,now,account_present=True,present_children={"runbooks"})
    result,retained,absent_ids=cleanup_saved_provider(record,runner=cleanup,
        utc_now=lambda:now+timedelta(seconds=1),monotonic_now=lambda:2.)
    assert result.cleanup_state=="succeeded" and not result.launch_blocked, result.failures
    assert retained.deployment.deployment_id==d.deployment_id and expected_role in absent_ids
    assert any(r.action_type=="expiry_destroy" for r in cleanup.requests)


def test_cleanup_rejects_subscription_scoped_role_from_another_subscription_before_commands(repository_root:Path,tmp_path:Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    registration=AutomationRunner(d,o,now,fail_at="runbook_publish")
    register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,registration,
        clock=lambda:1.,utc_now=lambda:now)
    journal_path=o.attempt_root/"recovery.json"
    journal=json.loads(journal_path.read_text())
    journal["expiry"]["role_definition_id"]="/subscriptions/foreign/providers/Microsoft.Authorization/roleDefinitions/00000000-0000-0000-0000-000000000004"
    journal_path.write_text(json.dumps(journal))
    class NoCloud:
        def __init__(self): self.requests=[]
        def run(self,request): self.requests.append(request); raise AssertionError("foreign role identity must stop cleanup")
    runner=NoCloud()
    with pytest.raises(ValueError,match="foreign expiry identities"):
        destroy(d,o,runner,clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    assert not runner.requests


@pytest.mark.parametrize("tamper", ["same_subscription_role", "same_group_assignment",
    "principal", "assignable_scope", "permissions"])
def test_saved_f09_cleanup_rejects_role_state_mismatch_before_expiry_destroy(repository_root:Path,tmp_path:Path,tamper):
    from cloud_network_benchmark.lifecycle_cleanup import cleanup_saved_provider, save_cleanup_settings
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"complete_workflow_seconds":600,"post_registration_workflow_seconds":600})
    request=expiry_request(d,o,d.reservation.value.expires_at.value-timedelta(seconds=300))
    overrides={}
    if tamper=="principal":
        overrides["azurerm_automation_account.expiry"]={"identity":[{"principal_id":"00000000-0000-0000-0000-000000000099"}]}
    elif tamper=="assignable_scope":
        overrides["azurerm_role_definition.deallocate"]={"assignable_scopes":["/subscriptions/foreign/resourceGroups/other"]}
    else:
        overrides["azurerm_role_definition.deallocate"]={"permissions":[{"actions":ROLE_ACTIONS+
            ["Microsoft.Compute/virtualMachines/start/action"],"not_actions":[],"data_actions":None,
            "not_data_actions":[]}]}
    registration=AutomationRunner(d,o,now,fail_at="runbook_publish",expiry_state_overrides=overrides)
    assert register_expiry(d,request,o,registration,clock=lambda:1.,utc_now=lambda:now).state=="failed"
    journal_path=o.attempt_root/"recovery.json"
    journal=json.loads(journal_path.read_text())
    if tamper=="same_subscription_role":
        journal["expiry"]["role_definition_id"]=f"/subscriptions/{o.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/00000000-0000-0000-0000-000000000099"
        journal_path.write_text(json.dumps(journal))
    elif tamper=="same_group_assignment":
        journal["expiry"]["role_assignment_id"]=(f"/subscriptions/{o.subscription_id}/resourceGroups/{_slug(d)}"
            "/providers/Microsoft.Authorization/roleAssignments/00000000-0000-0000-0000-000000000099")
        journal_path.write_text(json.dumps(journal))
    saved=save_cleanup_settings(provider="azure",deployment=d,request=request,
        reservation=d.reservation.value,provider_lead_seconds=300.,settings=o,private_root=tmp_path/"f09")
    record=SimpleNamespace(provider="azure",deployment_id=d.deployment_id,
        journal_reference=str(journal_path),cleanup_settings_reference=str(saved))
    cleanup=CleanupRunner(d,o,now,account_present=True,present_children={"runbooks"})
    result,_,_=cleanup_saved_provider(record,runner=cleanup,utc_now=lambda:now+timedelta(seconds=1),
        monotonic_now=lambda:2.)
    assert result.cleanup_state=="failed" and result.launch_blocked, result.failures
    assert not any(request.action_type=="expiry_destroy" for request in cleanup.requests)
    assert any(request.action_type=="pair_destroy" for request in cleanup.requests)


def test_saved_f09_cleanup_retries_after_successful_expiry_destroy_with_empty_state(repository_root:Path,tmp_path:Path):
    from cloud_network_benchmark.lifecycle_cleanup import cleanup_saved_provider, save_cleanup_settings
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"complete_workflow_seconds":600,"post_registration_workflow_seconds":600})
    request=expiry_request(d,o,d.reservation.value.expires_at.value-timedelta(seconds=300))
    registration=AutomationRunner(d,o,now,fail_at="runbook_publish")
    assert register_expiry(d,request,o,registration,clock=lambda:1.,utc_now=lambda:now).state=="failed"
    saved=save_cleanup_settings(provider="azure",deployment=d,request=request,
        reservation=d.reservation.value,provider_lead_seconds=300.,settings=o,private_root=tmp_path/"f09")
    record=SimpleNamespace(provider="azure",deployment_id=d.deployment_id,
        journal_reference=str(o.attempt_root/"recovery.json"),cleanup_settings_reference=str(saved))
    first=CleanupRunner(d,o,now,account_present=True,present_children={"runbooks"},
        role_definition_inventory='[{"id":"/subscriptions/foreign/providers/Microsoft.Authorization/roleDefinitions/foreign"}]')
    failed,_,_=cleanup_saved_provider(record,runner=first,utc_now=lambda:now+timedelta(seconds=1),
        monotonic_now=lambda:2.)
    assert failed.cleanup_state=="failed" and failed.launch_blocked
    assert json.loads((o.attempt_root/"expiry.tfstate").read_text())["resources"]==[]
    second=CleanupRunner(d,o,now,account_present=False)
    recovered,_,_=cleanup_saved_provider(record,runner=second,utc_now=lambda:now+timedelta(seconds=2),
        monotonic_now=lambda:3.)
    assert recovered.cleanup_state=="succeeded" and not recovered.launch_blocked, recovered.failures
    assert any(request.action_type=="expiry_destroy" for request in second.requests)


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
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    failed=AutomationRunner(d,o,now,fail_at=fail_at)
    evidence=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,failed,clock=lambda:1.,utc_now=lambda:now)
    assert evidence.state=="failed"
    cleanup_runner=CleanupRunner(d,o,now,account_present=account_present,present_children=children)
    result=destroy(d,o,cleanup_runner,clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    assert result.cleanup_state=="succeeded"
    deleted={r.action_type.removeprefix("expiry_").removesuffix("_delete") for r in cleanup_runner.requests if r.action_type.endswith("_delete") and not r.action_type.endswith("_before_delete")}
    assert deleted==children


def test_saved_partial_registration_cleanup_accepts_exact_azure_cli_missing_job_schedule(repository_root:Path,tmp_path:Path):
    from cloud_network_benchmark.lifecycle_cleanup import cleanup_saved_provider, save_cleanup_settings
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"complete_workflow_seconds":600,"post_registration_workflow_seconds":600})
    request=expiry_request(d,o,d.reservation.value.expires_at.value-timedelta(seconds=300))
    # This reproduces the recorded partial state: Terraform expiry support,
    # uploaded/published runbook, and schedule exist; no jobSchedule was made.
    registration=AutomationRunner(d,o,now,fail_at="job_binding_create",schedule_timezone="Etc/UTC")
    failed=register_expiry(d,request,o,registration,clock=lambda:1.,utc_now=lambda:now)
    assert failed.state=="failed"
    journal=json.loads((o.attempt_root/"recovery.json").read_text())
    assert journal["expiry"]["children"]["schedule"]=="created"
    assert journal["expiry"]["children"]["job_schedule"]=="creating"
    saved=save_cleanup_settings(provider="azure",deployment=d,request=request,
        reservation=d.reservation.value,provider_lead_seconds=300.,settings=o,private_root=tmp_path/"f09")
    record=SimpleNamespace(provider="azure",deployment_id=d.deployment_id,
        journal_reference=str(o.attempt_root/"recovery.json"),cleanup_settings_reference=str(saved))
    cleanup=CleanupRunner(d,o,now,present_children={"schedules","runbooks"},
        azure_cli_not_found_for="jobSchedules")
    result,_,_=cleanup_saved_provider(record,runner=cleanup,
        utc_now=lambda:now+timedelta(seconds=1),monotonic_now=lambda:2.)
    assert result.cleanup_state=="succeeded" and not result.launch_blocked, result.failures
    assert cleanup.deleted_children=={"schedules","runbooks"}
    assert any(item.action_type=="expiry_jobSchedules_before_delete" for item in cleanup.requests)


def test_cleanup_result_export_redacts_bounded_private_pem_sentinel(repository_root:Path,tmp_path:Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    registration=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,registration,clock=lambda:1.,utc_now=lambda:now)
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    sentinel="-----BEGIN RSA PRIVATE KEY-----cleanup-secret-----END RSA PRIVATE KEY-----"
    result=destroy(registered,o,CleanupRunner(d,o,now,secret_output=sentinel),clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    serialized=result.model_dump_json()
    assert "cleanup-secret" not in serialized and sentinel not in serialized and "[REDACTED]" in serialized


def test_cleanup_rejects_modified_private_terraform_inputs_before_runner_call(repository_root:Path,tmp_path:Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    registration=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,registration,clock=lambda:1.,utc_now=lambda:now)
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
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    registration=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,registration,clock=lambda:1.,utc_now=lambda:now)
    runner=CleanupRunner(d,o,now,blank_inventory=True)
    result=destroy(d.model_copy(update={"expiry":type(d.expiry)(value=expiry)}),o,runner,clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    assert result.cleanup_state=="failed" and result.launch_blocked
    assert "pair_inventory_page_2" not in [r.action_type for r in runner.requests]
    assert not any(r.action_type.endswith("_delete") for r in runner.requests)


@pytest.mark.parametrize("inventory", ["[{\"malformed\":\"no id\"}]", "[null]", "[{\"id\":\"foreign-role\"}]"])
def test_malformed_or_unexpected_custom_role_inventory_retains_recovery_and_blocks_launch(repository_root:Path,tmp_path:Path,inventory):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    registration=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,registration,clock=lambda:1.,utc_now=lambda:now)
    runner=CleanupRunner(d,o,now,role_definition_inventory=inventory)
    result=destroy(d.model_copy(update={"expiry":type(d.expiry)(value=expiry)}),o,runner,clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    assert result.cleanup_state=="failed" and result.launch_blocked
    assert "role definition inventory" in result.failures[0].message
    assert (o.attempt_root/"recovery.json").exists()


@pytest.mark.parametrize("inventory", ["[null]", "[{\"id\":\"\"}]", "{\"value\":[]}"])
def test_malformed_role_assignment_inventory_also_blocks_cleanup(repository_root:Path,tmp_path:Path,inventory):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    registration=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,registration,clock=lambda:1.,utc_now=lambda:now)
    runner=CleanupRunner(d,o,now,role_assignment_inventory=inventory)
    result=destroy(d.model_copy(update={"expiry":type(d.expiry)(value=expiry)}),o,runner,clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    assert result.cleanup_state=="failed" and result.launch_blocked
    assert "role assignment inventory" in result.failures[0].message
    assert (o.attempt_root/"recovery.json").exists()


def test_cleanup_uses_global_all_inventory_and_ignores_other_resource_groups(repository_root:Path,tmp_path:Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    registration=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,registration,clock=lambda:1.,utc_now=lambda:now)
    other_assignment=f"/subscriptions/{o.subscription_id}/resourceGroups/other/providers/Microsoft.Authorization/roleAssignments/00000000-0000-0000-0000-000000000099"
    runner=CleanupRunner(d,o,now,role_assignment_inventory=json.dumps([{"id":other_assignment}]))
    result=destroy(d.model_copy(update={"expiry":type(d.expiry)(value=expiry)}),o,runner,clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    assert result.cleanup_state=="succeeded" and not result.launch_blocked
    request=next(r for r in runner.requests if r.action_type=="role_cleanup_inventory")
    assert "--all" in request.argv and "--scope" not in request.argv


def test_cleanup_detects_remaining_assignment_in_attempt_resource_group(repository_root:Path,tmp_path:Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    registration=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,registration,clock=lambda:1.,utc_now=lambda:now)
    assignment=f"/subscriptions/{o.subscription_id}/resourceGroups/{_slug(d)}/providers/Microsoft.Authorization/roleAssignments/00000000-0000-0000-0000-000000000099"
    runner=CleanupRunner(d,o,now,role_assignment_inventory=json.dumps([{"id":assignment}]))
    result=destroy(d.model_copy(update={"expiry":type(d.expiry)(value=expiry)}),o,runner,clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    assert result.cleanup_state=="failed" and result.launch_blocked
    assert "role assignment inventory still contains" in result.failures[0].message
    assert (o.attempt_root/"recovery.json").exists()


def test_interrupted_registration_reuses_observed_job_identity(repository_root:Path,tmp_path:Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    runner=AutomationRunner(d,o,now,fail_at="runbook_publish")
    request=expiry_request(d,o,now+timedelta(minutes=16))
    first=register_expiry(d,request,o,runner,clock=lambda:1.,utc_now=lambda:now)
    first_id=json.loads((o.attempt_root/"recovery.json").read_text())["expiry"]["job_schedule_id"]
    second=register_expiry(d,request,o,runner,clock=lambda:1.,utc_now=lambda:now)
    second_id=json.loads((o.attempt_root/"recovery.json").read_text())["expiry"]["job_schedule_id"]
    assert first.state==second.state=="failed" and first_id==second_id


def test_repeated_cleanup_recovers_after_child_delete_interruption(repository_root:Path,tmp_path:Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    registration=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,registration,clock=lambda:1.,utc_now=lambda:now)
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
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=18)})
    runner=AutomationRunner(d,o,now,fail_at="runbook_publish")
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,runner,clock=lambda:1.0,utc_now=lambda:now)
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
