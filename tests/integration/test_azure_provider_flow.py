"""Offline protocol tests for Azure's registered-expiry gate."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from cloud_network_benchmark.azure_provider import AzureRuntimeOptions, _slug, register_expiry, provision, destroy
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
    def __init__(self, deployment_input, options_input, now, fail_at=None, publish_delay=0, bad_key_for=None, wrong_vm_id_for=None):
        self.d=deployment_input; self.o=options_input; self.now=now; self.fail_at=fail_at; self.publish_delay=publish_delay; self.bad_key_for=bad_key_for; self.wrong_vm_id_for=wrong_vm_id_for; self.publish_reads=0; self.requests=[]; self.script=""; self.schedule={}; self.binding={}; self.tamper_recheck=None
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
            vms={"a":{"id":f"/subscriptions/{self.o.subscription_id}/resourceGroups/{self.name}/providers/Microsoft.Compute/virtualMachines/{self.name}-a","private_ipv4":"10.70.0.4","public_ipv4":"8.8.8.8","image":self.o.selected_image,"size":"Standard_D4s_v5","region":"germanywestcentral","zone":"1","ppg":None,"accelerated_networking":True},
                 "b":{"id":f"/subscriptions/{self.o.subscription_id}/resourceGroups/{self.name}/providers/Microsoft.Compute/virtualMachines/{self.name}-b","private_ipv4":"10.70.0.5","public_ipv4":"1.1.1.1","image":self.o.selected_image,"size":"Standard_D4s_v5","region":"germanywestcentral","zone":"1","ppg":None,"accelerated_networking":True}}
            if self.wrong_vm_id_for: vms[self.wrong_vm_id_for]["id"] += "-foreign"
            out=json.dumps({"vm_"+role:{"value":data} for role,data in vms.items()})
        elif request.action_type=="host_key_identity":
            target=request.argv[request.argv.index("--ids")+1]
            key="malformed-key" if target.endswith("-"+str(self.bad_key_for)) else "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE9F4huSqss0wx3n6f5+WJiEpePM3oXTbuOzBLRR5NCc guest"
            out=json.dumps({"resourceId":target,"value":[{"code":"ComponentStatus/StdOut/succeeded","message":key+"\n"}]})
        elif request.action_type in {"runbook_readback","expiry_runbook_recheck"}:
            self.publish_reads+=1
            state="Published" if self.publish_reads>self.publish_delay else "Draft"
            payload=RESPONSE_FIXTURES["runbook_published"] if state=="Published" else RESPONSE_FIXTURES["runbook_draft"]
            out=json.dumps(payload)
        elif request.action_type in {"published_content_readback","expiry_content_recheck"}: out=self.script
        elif request.action_type in {"schedule_readback","expiry_schedule_recheck"}:
            p=dict(self.schedule["properties"]); p["isEnabled"]=self.tamper_recheck!="schedule" if request.action_type=="expiry_schedule_recheck" else True; out=json.dumps({"properties":p})
        elif request.action_type in {"job_binding_readback","expiry_binding_recheck"}:
            self.job_id=request.argv[request.argv.index("--url")+1].split("/jobSchedules/")[1].split("?")[0]
            binding=json.loads(json.dumps(self.binding["properties"]))
            if request.action_type=="expiry_binding_recheck" and self.tamper_recheck=="binding": binding["parameters"]["resourcegroupname"]="foreign"
            out=json.dumps({"id":f"{self.account}/jobSchedules/{self.job_id}","properties":binding})
        elif request.action_type in {"role_assignment_readback","expiry_role_recheck"}:
            scope=self.group_id+"/foreign" if request.action_type=="expiry_role_recheck" and self.tamper_recheck=="permission" else self.group_id
            out=json.dumps([{"id":self.assignment_id,"principalId":"00000000-0000-0000-0000-000000000003","scope":scope}])
        elif request.action_type=="role_definition_readback":
            out=json.dumps([{"id":self.role_id,"assignableScopes":[self.group_id],"permissions":[{"actions":["Microsoft.Compute/virtualMachines/read","Microsoft.Compute/virtualMachines/instanceView/read","Microsoft.Compute/virtualMachines/deallocate/action"],"notActions":[],"dataActions":[],"notDataActions":[]}]}])
        elif request.action_type=="expiry_role_definition_recheck":
            out=json.dumps([{"id":self.role_id,"assignableScopes":[self.group_id],"permissions":[{"actions":["Microsoft.Compute/virtualMachines/read","Microsoft.Compute/virtualMachines/instanceView/read","Microsoft.Compute/virtualMachines/deallocate/action"],"notActions":[],"dataActions":[],"notDataActions":[]}]}])
        elif request.action_type=="automation_module_readback": out=json.dumps({"properties":{"provisioningState":"Succeeded","version":"3.0.0"}})
        outcome=CommandOutcome.FAILED if request.action_type==self.fail_at else CommandOutcome.SUCCEEDED
        return CommandResult(action_id=request.action_id,outcome=outcome,started_at=self.now,finished_at=self.now,duration_seconds=0,
            exit_code=1 if outcome==CommandOutcome.FAILED else 0,stdout=out,stderr="injected failure" if outcome==CommandOutcome.FAILED else "")


class CleanupRunner(AutomationRunner):
    def __init__(self, *args, residual=False, **kwargs):
        super().__init__(*args, **kwargs); self.residual=residual
    def run(self, request):
        if request.action_type=="vm_absence_by_id":
            self.requests.append(request)
            return CommandResult(action_id=request.action_id,outcome=CommandOutcome.FAILED,started_at=self.now,finished_at=self.now,duration_seconds=0,exit_code=1,stdout="",stderr="(ResourceNotFound) VM not found")
        if request.action_type=="pair_inventory":
            self.requests.append(request)
            resources=[{"id":self.account,"type":"Microsoft.Automation/automationAccounts"}]
            if self.residual: resources.append({"id":self.group_id+"/providers/Microsoft.Network/networkInterfaces/leftover","type":"Microsoft.Network/networkInterfaces"})
            out=json.dumps(resources)
        elif request.action_type=="expiry_inventory": self.requests.append(request); out="false"
        elif request.action_type=="role_cleanup_inventory": self.requests.append(request); out="[]"
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


@pytest.mark.parametrize("invalid",["reservation_snapshot","expiry_cost","future_price"])
def test_registration_preflight_rejects_unadmitted_or_unpriced_requests_before_any_external_action(repository_root: Path, tmp_path: Path, invalid):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    request=expiry_request(d,o,now+timedelta(minutes=20))
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
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=30)})
    runner=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=20)),o,runner,clock=lambda:1.0,utc_now=lambda:now)
    assert expiry.state=="registered" and expiry.action=="deallocate"
    kinds=[x.action_type for x in runner.requests]
    assert kinds.index("expiry_prerequisites") < kinds.index("job_binding_create")
    assert "pair_apply" not in kinds
    assert expiry.provider_metadata.value["effective_rbac_unproved"] is True
    assert expiry.provider_metadata.value["binding_readback"]["resourcegroupname"]==_slug(d)
    journal=json.loads((o.attempt_root/"recovery.json").read_text())
    assert journal["expiry_state"]=="registered"


def test_registration_polls_asynchronous_publication_within_bound(repository_root: Path, tmp_path: Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=30)})
    runner=AutomationRunner(d,o,now,publish_delay=1)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=20)),o,runner,clock=lambda:1.0,utc_now=lambda:now,sleep=lambda seconds:None)
    assert expiry.state=="registered"
    assert [r.action_type for r in runner.requests].count("runbook_readback")==2


def test_provision_rechecks_published_content_before_pair_apply(repository_root: Path, tmp_path: Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=30)})
    runner=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=20)),o,runner,clock=lambda:1.0,utc_now=lambda:now)
    changed=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    runner.script += "# changed after confirmation\n"
    result=provision(changed,o,runner,clock=lambda:2.0,utc_now=lambda:now+timedelta(seconds=1))
    assert result.cleanup_required and result.deployment is None
    assert any("content changed" in f.message for f in result.failures), result.failures
    assert "pair_apply" not in [r.action_type for r in runner.requests]


@pytest.mark.parametrize("evidence", ["schedule", "binding", "permission"])
def test_provision_rechecks_schedule_binding_and_role_before_pair_apply(repository_root: Path, tmp_path: Path, evidence):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=30)})
    runner=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=20)),o,runner,clock=lambda:1.0,utc_now=lambda:now)
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    runner.tamper_recheck=evidence
    result=provision(registered,o,runner,clock=lambda:2.0,utc_now=lambda:now+timedelta(seconds=1))
    assert result.cleanup_required and result.deployment is None and result.failures
    assert "pair_apply" not in [r.action_type for r in runner.requests]


def test_registered_pair_provision_normalizes_both_vms_and_materializes_authenticated_known_hosts(repository_root: Path, tmp_path: Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=30)})
    runner=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=20)),o,runner,clock=lambda:1.0,utc_now=lambda:now)
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    result=provision(registered,o,runner,clock=lambda:2.0,utc_now=lambda:now+timedelta(seconds=1))
    assert result.deployment is not None and result.cleanup_required, result.failures
    assert result.deployment.vm_a.connection and result.deployment.vm_b.connection, result.failures
    assert result.deployment.vm_a.connection.host=="8.8.8.8"
    assert result.deployment.vm_b.connection.host=="1.1.1.1"
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
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=30)})
    runner=AutomationRunner(d,o,now,bad_key_for="b")
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=20)),o,runner,clock=lambda:1.0,utc_now=lambda:now)
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    result=provision(registered,o,runner,clock=lambda:2.0,utc_now=lambda:now+timedelta(seconds=1))
    assert result.cleanup_required and result.deployment is not None
    assert result.deployment.vm_a.resource_id and result.deployment.vm_b.resource_id
    assert result.deployment.vm_a.connection is None and result.deployment.vm_b.connection is None
    assert result.deployment.vm_a.unavailable.value["reason"]
    assert any("malformed" in failure.message for failure in result.failures)


@pytest.mark.parametrize("fail_at",["pair_apply","pair_output"])
def test_pair_apply_and_output_failures_keep_cleanup_required_journal(repository_root: Path, tmp_path: Path, fail_at):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=30)})
    runner=AutomationRunner(d,o,now,fail_at=fail_at)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=20)),o,runner,clock=lambda:1.0,utc_now=lambda:now)
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    result=provision(registered,o,runner,clock=lambda:2.0,utc_now=lambda:now+timedelta(seconds=1))
    journal=json.loads((o.attempt_root/"recovery.json").read_text())
    assert result.cleanup_required and result.deployment is None and result.failures
    assert journal["creation_may_have_started"] is True and journal["pair_state"]=="creating"
    assert not (o.attempt_root/"terraform-outputs.redacted.json").exists()


def test_foreign_vm_output_identity_preserves_only_the_valid_role(repository_root: Path, tmp_path: Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=30)})
    runner=AutomationRunner(d,o,now,wrong_vm_id_for="b")
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=20)),o,runner,clock=lambda:1.0,utc_now=lambda:now)
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    result=provision(registered,o,runner,clock=lambda:2.0,utc_now=lambda:now+timedelta(seconds=1))
    assert result.cleanup_required and result.deployment is not None
    assert result.deployment.vm_a.resource_id and result.deployment.vm_a.connection is None
    assert result.deployment.vm_b.resource_id is None and result.deployment.vm_b.connection is None


@pytest.mark.parametrize("residual,expected", [(False,"succeeded"),(True,"failed")])
def test_cleanup_proves_vm_absence_and_blocks_expiry_teardown_for_residual_pair_resource(repository_root: Path, tmp_path: Path, residual, expected):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=30)})
    registration_runner=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=20)),o,registration_runner,clock=lambda:1.0,utc_now=lambda:now)
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    runner=CleanupRunner(d,o,now,residual=residual)
    result=destroy(registered,o,runner,clock=lambda:2.0,utc_now=lambda:now+timedelta(seconds=1))
    assert result.cleanup_state==expected
    assert result.launch_blocked==residual
    cleanup_actions=[r.action_type for r in runner.requests]
    assert cleanup_actions.index("vm_absence_by_id") < cleanup_actions.index("pair_inventory")
    if residual:
        assert "expiry_unbind" not in cleanup_actions
    else:
        assert all(kind in cleanup_actions for kind in ("expiry_unbind","expiry_schedule_delete","expiry_runbook_delete"))


def test_missing_recovery_journal_blocks_launch_and_never_calls_cloud(repository_root: Path,tmp_path:Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    class Runner:
        def run(self,request): raise AssertionError("missing recovery evidence forbids cleanup API calls")
    result=destroy(d,o,Runner(),clock=lambda:1.0,utc_now=lambda:now)
    assert result.cleanup_state=="failed" and result.launch_blocked


def test_registration_failure_after_mutation_returns_failed_evidence_and_keeps_journal(repository_root: Path, tmp_path: Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=30)})
    runner=AutomationRunner(d,o,now,fail_at="runbook_publish")
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=20)),o,runner,clock=lambda:1.0,utc_now=lambda:now)
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
