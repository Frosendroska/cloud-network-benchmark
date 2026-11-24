"""Offline protocol tests for Azure's registered-expiry gate."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import subprocess

import pytest

import cloud_network_benchmark.azure_provider as azure_provider
from cloud_network_benchmark.azure_provider import AzureRuntimeOptions, _slug, register_expiry, provision, destroy, build_pair_inputs
from cloud_network_benchmark.contracts.admission import ExpiryRequest
from cloud_network_benchmark.contracts.common import CommandOutcome
from cloud_network_benchmark.contracts.execution import CommandResult
from cloud_network_benchmark.terraform_plan import reviewed_apply as _real_reviewed_apply
pytestmark = pytest.mark.usefixtures("isolated_legacy_saved_plan_boundary")

from tests.fixtures.f06 import RESPONSE_FIXTURES
from tests.contract.test_azure_provider_contract import deployment, options


@pytest.mark.parametrize("changes", [
    {"timeZone":"Europe/London"},
    {"timeZone":"GMT"},
    {"timeZone":[]},
    {"timeZone":{}},
    {"startTime":None},
    {"startTime":17},
    {"startTime":"2026-11-24T14:29:00"},
    {"startTime":"not-a-time"},
    {"startTime":"2026-11-24T15:29:00+01:00"},
    {"startTime":"2026-11-24T14:29:01+00:00"},
    {"nextRun":"2026-11-24T14:30:00+00:00"},
    {"expiryTime":"2026-11-24T14:30:00+00:00"},
    {"isEnabled":False},
    {"frequency":"Daily"},
])
def test_azure_schedule_validator_rejects_non_exact_readbacks(changes):
    props={"isEnabled":True,"frequency":"OneTime","timeZone":"Etc/UTC",
        "startTime":"2026-11-24T14:29:00+00:00"}
    props.update(changes)
    assert not azure_provider._azure_schedule_matches(props,datetime(2026,11,24,14,29,tzinfo=timezone.utc))


def test_missing_parent_wrapper_requires_exact_resource_group_and_unique_json_keys():
    from cloud_network_benchmark.azure_provider import _is_exact_missing_attempt_resource_group
    now=datetime.now(timezone.utc); group="cnb-attempt"
    payload={"error":{"code":"ResourceGroupNotFound","message":f"Resource group '{group}' could not be found."}}
    expected="ERROR: Not Found("+json.dumps(payload,separators=(",",":"))+")"
    accepted=CommandResult(action_id="absence",outcome=CommandOutcome.FAILED,started_at=now,
        finished_at=now,duration_seconds=0,exit_code=1,stdout="",stderr=expected)
    assert _is_exact_missing_attempt_resource_group(accepted,group)
    assert not _is_exact_missing_attempt_resource_group(accepted,"foreign-group")
    duplicate=accepted.model_copy(update={"stderr":'ERROR: Not Found({"error":{"code":"ResourceGroupNotFound","code":"AuthorizationFailed","message":"Resource group \'cnb-attempt\' could not be found."}})'})
    assert not _is_exact_missing_attempt_resource_group(duplicate,group)
    duplicate_outer=accepted.model_copy(update={"stderr":'ERROR: Not Found({"error":{"code":"ResourceGroupNotFound","message":"Resource group \'cnb-attempt\' could not be found."},"error":{"code":"ResourceGroupNotFound","message":"Resource group \'cnb-attempt\' could not be found."}})'})
    assert not _is_exact_missing_attempt_resource_group(duplicate_outer,group)
    duplicate_message=accepted.model_copy(update={"stderr":'ERROR: Not Found({"error":{"code":"ResourceGroupNotFound","message":"foreign","message":"Resource group \'cnb-attempt\' could not be found."}})'})
    assert not _is_exact_missing_attempt_resource_group(duplicate_message,group)
    assert not _is_exact_missing_attempt_resource_group(accepted.model_copy(update={"stdout":"unexpected"}),group)
    cli=(f"ERROR: (ResourceGroupNotFound) Resource group '{group}' could not be found.\n"
        f"Code: ResourceGroupNotFound\nMessage: Resource group '{group}' could not be found.\n")
    cli_result=accepted.model_copy(update={"stderr":cli})
    assert _is_exact_missing_attempt_resource_group(cli_result,group)
    assert not _is_exact_missing_attempt_resource_group(cli_result.model_copy(update={"stderr":cli.replace(group,"foreign-group")}),group)
    assert not _is_exact_missing_attempt_resource_group(cli_result.model_copy(update={"stderr":cli+"AuthorizationFailed"}),group)
    assert not _is_exact_missing_attempt_resource_group(cli_result.model_copy(update={"stdout":"unexpected"}),group)


def test_missing_automation_child_wrapper_is_typed_and_unique():
    from cloud_network_benchmark.azure_provider import _is_exact_missing_automation_child
    now=datetime.now(timezone.utc)
    expected_url=("/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/cnb-attempt"
        "/providers/Microsoft.Automation/automationAccounts/cnb-attempt-auto/jobSchedules/"
        "00000000-0000-0000-0000-000000000123?api-version="+azure_provider.AUTOMATION_API)
    stderr='ERROR: Not Found({"code":"NotFound","message":"Job Schedule not found."})'
    accepted=CommandResult(action_id="job-absence",outcome=CommandOutcome.FAILED,started_at=now,
        finished_at=now,duration_seconds=0,exit_code=1,stdout="",stderr=stderr)
    assert _is_exact_missing_automation_child(accepted,expected_url)
    assert _is_exact_missing_automation_child(accepted.model_copy(update={"stderr":'ERROR: Not Found({"code":"NotFound","message":""})'}),expected_url)
    assert not _is_exact_missing_automation_child(accepted.model_copy(update={"stdout":"unexpected"}),expected_url)
    assert not _is_exact_missing_automation_child(accepted.model_copy(update={"stderr":stderr.replace("NotFound", "AuthorizationFailed", 1)}),expected_url)
    assert not _is_exact_missing_automation_child(accepted.model_copy(update={"stderr":'ERROR: Not Found({"code":"NotFound","message":null})'}),expected_url)
    duplicate=accepted.model_copy(update={"stderr":'ERROR: Not Found({"code":"NotFound","code":"AuthorizationFailed","message":"Job Schedule not found."})'})
    assert not _is_exact_missing_automation_child(duplicate,expected_url)


def test_nested_resource_not_found_requires_exact_automation_child_message():
    from cloud_network_benchmark.azure_provider import _is_exact_missing_automation_child
    now=datetime.now(timezone.utc)
    url=("/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/cnb-attempt"
        "/providers/Microsoft.Automation/automationAccounts/cnb-attempt-auto/runbooks/"
        "cnb-attempt-deallocate?api-version="+azure_provider.AUTOMATION_API)
    msg=("The Resource 'Microsoft.Automation/automationAccounts/cnb-attempt-auto/runbooks/"
        "cnb-attempt-deallocate' under resource group 'cnb-attempt' was not found. "
        "For more details please go to https://aka.ms/ARMResourceNotFoundFix")
    stderr="ERROR: Not Found("+json.dumps({"error":{"code":"ResourceNotFound","message":msg}})+")\n"
    accepted=CommandResult(action_id="runbook-absence",outcome=CommandOutcome.FAILED,started_at=now,
        finished_at=now,duration_seconds=0,exit_code=1,stdout="",stderr=stderr)
    assert _is_exact_missing_automation_child(accepted,url)
    assert not _is_exact_missing_automation_child(accepted.model_copy(update={"stderr":stderr.replace("cnb-attempt-deallocate", "foreign-runbook")}),url)
    assert not _is_exact_missing_automation_child(accepted.model_copy(update={"stderr":stderr.replace("cnb-attempt' was", "foreign-group' was")}),url)
    assert not _is_exact_missing_automation_child(accepted.model_copy(update={"stderr":stderr.replace("ResourceNotFound", "AuthorizationFailed", 1)}),url)
    assert not _is_exact_missing_automation_child(accepted.model_copy(update={"stdout":"ambiguous"}),url)
    duplicate=accepted.model_copy(update={"stderr":'ERROR: Not Found({"error":{"code":"ResourceNotFound","code":"AuthorizationFailed","message":"'+msg+'"}})'})
    assert not _is_exact_missing_automation_child(duplicate,url)
    parent_message=("The Resource 'Microsoft.Automation/automationAccounts/cnb-attempt-auto' "
        "under resource group 'cnb-attempt' was not found. "
        "For more details please go to https://aka.ms/ARMResourceNotFoundFix")
    parent_result=accepted.model_copy(update={"stderr":"ERROR: Not Found("+json.dumps(
        {"error":{"code":"ResourceNotFound","message":parent_message}})+")"})
    assert _is_exact_missing_automation_child(parent_result,url)
    assert not _is_exact_missing_automation_child(parent_result.model_copy(update={"stderr":parent_result.stderr.replace("cnb-attempt-auto", "foreign-auto")}),url)
    group_result=accepted.model_copy(update={"stderr":"ERROR: Not Found("+json.dumps(
        {"error":{"code":"ResourceGroupNotFound","message":"Resource group 'cnb-attempt' could not be found."}})+")"})
    assert _is_exact_missing_automation_child(group_result,url)
    assert not _is_exact_missing_automation_child(group_result.model_copy(update={"stderr":group_result.stderr.replace("cnb-attempt'", "foreign'",1)}),url)


def test_missing_automation_account_wrapper_requires_exact_account_identity():
    from cloud_network_benchmark.azure_provider import _is_exact_missing_automation_account
    now=datetime.now(timezone.utc)
    account=("/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/cnb-attempt"
        "/providers/Microsoft.Automation/automationAccounts/cnb-attempt-auto")
    msg=("The Resource 'Microsoft.Automation/automationAccounts/cnb-attempt-auto' "
        "under resource group 'cnb-attempt' was not found. "
        "For more details please go to https://aka.ms/ARMResourceNotFoundFix")
    stderr="ERROR: Not Found("+json.dumps({"error":{"code":"ResourceNotFound","message":msg}})+")\n"
    accepted=CommandResult(action_id="account-absence",outcome=CommandOutcome.FAILED,started_at=now,
        finished_at=now,duration_seconds=0,exit_code=1,stdout="",stderr=stderr)
    assert _is_exact_missing_automation_account(accepted,account)
    assert not _is_exact_missing_automation_account(accepted,account.replace("cnb-attempt-auto","foreign-auto"))
    assert not _is_exact_missing_automation_account(accepted.model_copy(update={"stderr":stderr.replace("cnb-attempt-auto", "foreign-auto")}),account)
    assert not _is_exact_missing_automation_account(accepted.model_copy(update={"stderr":stderr.replace("ResourceNotFound", "AuthorizationFailed",1)}),account)
    assert not _is_exact_missing_automation_account(accepted.model_copy(update={"stdout":"ambiguous"}),account)
    duplicate=accepted.model_copy(update={"stderr":'ERROR: Not Found({"error":{"code":"ResourceNotFound","code":"AuthorizationFailed","message":"'+msg+'"}})'})
    assert not _is_exact_missing_automation_account(duplicate,account)


def test_actual_job_schedule_absence_passes_production_cleanup_command(repository_root, tmp_path):
    now=datetime.now(timezone.utc); d=deployment(repository_root); o=options(tmp_path,d.admission.priority)
    account=(f"/subscriptions/{o.subscription_id}/resourceGroups/{_slug(d)}"
        f"/providers/Microsoft.Automation/automationAccounts/{_slug(d)}-auto")
    result=CommandResult(action_id="job-absence",outcome=CommandOutcome.FAILED,started_at=now,
        finished_at=now,duration_seconds=0,exit_code=1,stdout="",
        stderr='ERROR: Not Found({"code":"NotFound","message":"Job Schedule not found."})\n')
    class Runner:
        def run(self, request):
            assert request.action_type=="expiry_jobSchedules_before_delete"
            assert request.argv[request.argv.index("--method")+1]=="GET"
            assert "/jobSchedules/" in request.argv[request.argv.index("--url")+1]
            return result.model_copy(update={"action_id":request.action_id})
    job_id="00000000-0000-0000-0000-000000000123"
    url=account+"/jobSchedules/"+job_id+"?api-version="+azure_provider.AUTOMATION_API
    with pytest.raises(RuntimeError,match="expiry_jobSchedules_before_delete failed with failed"):
        azure_provider._command(Runner(),o,tmp_path,azure_provider._azure_rest("GET",url),
            "expiry_jobSchedules_before_delete",lambda:1.0)
    actions=[]
    azure_provider._delete_owned_arm_child(Runner(),o,tmp_path,
        account,
        "jobSchedules",job_id,lambda:1.0,actions)
    assert len(actions)==1 and actions[0]["outcome"]=="failed"


def test_actual_job_schedule_absence_confirms_delete_through_production_command(repository_root,tmp_path):
    now=datetime.now(timezone.utc); d=deployment(repository_root); o=options(tmp_path,d.admission.priority)
    account=(f"/subscriptions/{o.subscription_id}/resourceGroups/{_slug(d)}"
        f"/providers/Microsoft.Automation/automationAccounts/{_slug(d)}-auto")
    job_id="00000000-0000-0000-0000-000000000123"
    resource_id=account+"/jobSchedules/"+job_id
    absent=CommandResult(action_id="absent",outcome=CommandOutcome.FAILED,started_at=now,
        finished_at=now,duration_seconds=0,exit_code=1,stdout="",
        stderr='ERROR: Not Found({"code":"NotFound","message":"Job Schedule not found."})\n')
    def success(request,stdout=""):
        return CommandResult(action_id=request.action_id,outcome=CommandOutcome.SUCCEEDED,
            started_at=now,finished_at=now,duration_seconds=0,exit_code=0,stdout=stdout,stderr="")
    class Runner:
        def __init__(self): self.calls=0
        def run(self,request):
            self.calls+=1
            if request.argv[request.argv.index("--method")+1]=="DELETE": return success(request)
            if self.calls==1: return success(request,json.dumps({"id":resource_id}))
            return absent.model_copy(update={"action_id":request.action_id})
    runner=Runner(); actions=[]
    azure_provider._delete_owned_arm_child(runner,o,tmp_path,account,
        "jobSchedules",job_id,lambda:1.0,actions)
    assert runner.calls==3 and len(actions)==3


def test_actual_blank_schedule_absence_passes_production_cleanup_command(repository_root,tmp_path):
    now=datetime.now(timezone.utc); d=deployment(repository_root); o=options(tmp_path,d.admission.priority)
    account=(f"/subscriptions/{o.subscription_id}/resourceGroups/{_slug(d)}"
        f"/providers/Microsoft.Automation/automationAccounts/{_slug(d)}-auto")
    result=CommandResult(action_id="schedule-absence",outcome=CommandOutcome.FAILED,started_at=now,
        finished_at=now,duration_seconds=0,exit_code=1,stdout="",
        stderr='ERROR: Not Found({"code":"NotFound","message":""})\n')
    class Runner:
        def run(self,request):
            assert request.action_type=="expiry_schedules_before_delete"
            assert request.argv[request.argv.index("--method")+1]=="GET"
            return result.model_copy(update={"action_id":request.action_id})
    actions=[]
    azure_provider._delete_owned_arm_child(Runner(),o,tmp_path,account,"schedules",_slug(d)+"-once",lambda:1.0,actions)
    assert len(actions)==1 and actions[0]["outcome"]=="failed"


def test_actual_nested_runbook_absence_passes_production_cleanup_command(repository_root,tmp_path):
    now=datetime.now(timezone.utc); d=deployment(repository_root); o=options(tmp_path,d.admission.priority)
    account=(f"/subscriptions/{o.subscription_id}/resourceGroups/{_slug(d)}"
        f"/providers/Microsoft.Automation/automationAccounts/{_slug(d)}-auto")
    runbook=_slug(d)+"-deallocate"
    message=(f"The Resource 'Microsoft.Automation/automationAccounts/{_slug(d)}-auto/runbooks/{runbook}' "
        f"under resource group '{_slug(d)}' was not found. For more details please go to "
        "https://aka.ms/ARMResourceNotFoundFix")
    stderr="ERROR: Not Found("+json.dumps({"error":{"code":"ResourceNotFound","message":message}})+")\n"
    result=CommandResult(action_id="runbook-absence",outcome=CommandOutcome.FAILED,started_at=now,
        finished_at=now,duration_seconds=0,exit_code=1,stdout="",stderr=stderr)
    class Runner:
        def run(self,request):
            assert request.action_type=="expiry_runbooks_before_delete"
            return result.model_copy(update={"action_id":request.action_id})
    actions=[]
    azure_provider._delete_owned_arm_child(Runner(),o,tmp_path,account,"runbooks",runbook,lambda:1.0,actions)
    assert len(actions)==1 and actions[0]["outcome"]=="failed"


@pytest.mark.parametrize("stderr,stdout,collection", [
    ('ERROR: Not Found({"code":"AuthorizationFailed","message":"Job Schedule not found."})\n', "", "jobSchedules"),
    ('ERROR: Not Found({"code":"NotFound","message":"Schedule not found."})\n', "", "jobSchedules"),
    ('ERROR: Not Found({"code":"NotFound","code":"AuthorizationFailed","message":"Job Schedule not found."})\n', "", "jobSchedules"),
    ('ERROR: Not Found({"code":"NotFound","message":"Job Schedule not found."})\n', "unexpected", "jobSchedules"),
    ('ERROR: Not Found({"code":"NotFound","message":""})\n', "", "unknownCollection"),
])
def test_job_schedule_absence_production_path_rejects_unrelated_errors(repository_root,tmp_path,stderr,stdout,collection):
    now=datetime.now(timezone.utc); d=deployment(repository_root); o=options(tmp_path,d.admission.priority)
    result=CommandResult(action_id="job-absence",outcome=CommandOutcome.FAILED,started_at=now,
        finished_at=now,duration_seconds=0,exit_code=1,stdout=stdout,stderr=stderr)
    class Runner:
        def run(self, request): return result.model_copy(update={"action_id":request.action_id})
    with pytest.raises(RuntimeError,match="failed with failed"):
        azure_provider._delete_owned_arm_child(Runner(),o,tmp_path,
            "/subscriptions/test/resourceGroups/rg/providers/Microsoft.Automation/automationAccounts/acct",
            collection,"owned-once",lambda:1.0,[])


@pytest.mark.parametrize("url_suffix,argv_suffix", [
    ("jobSchedules/00000000-0000-0000-0000-000000000124?api-version=2024-10-23", []),
    ("jobSchedules/00000000-0000-0000-0000-000000000123?api-version=2024-10-23", ["--method","GET"]),
    ("jobSchedules/00000000-0000-0000-0000-000000000123?api-version=2024-10-23", ["--url","/subscriptions/foreign/resourceGroups/rg/providers/Microsoft.Automation/automationAccounts/acct/jobSchedules/00000000-0000-0000-0000-000000000123?api-version=2024-10-23"]),
    ("schedules/00000000-0000-0000-0000-000000000123?api-version=2024-10-23", []),
])
def test_job_schedule_absence_command_requires_one_exact_owned_url(repository_root,tmp_path,url_suffix,argv_suffix):
    now=datetime.now(timezone.utc); d=deployment(repository_root); o=options(tmp_path,d.admission.priority)
    account=(f"/subscriptions/{o.subscription_id}/resourceGroups/{_slug(d)}"
        f"/providers/Microsoft.Automation/automationAccounts/{_slug(d)}-auto")
    request_url=account+"/"+url_suffix
    expected_url=account+"/jobSchedules/00000000-0000-0000-0000-000000000123?api-version=2024-10-23"
    result=CommandResult(action_id="job-absence",outcome=CommandOutcome.FAILED,started_at=now,
        finished_at=now,duration_seconds=0,exit_code=1,stdout="",
        stderr='ERROR: Not Found({"code":"NotFound","message":"Job Schedule not found."})')
    class Runner:
        def run(self,request): return result.model_copy(update={"action_id":request.action_id})
    argv=["az","rest","--method","GET","--url",request_url,"--output","json",*argv_suffix]
    with pytest.raises(RuntimeError,match="failed with failed"):
        azure_provider._command(Runner(),o,tmp_path,argv,"expiry_jobSchedules_before_delete",lambda:1.0,
            expected_missing_automation_child_url=expected_url)


@pytest.mark.parametrize("case", ["actual", "actual_schedule", "actual_runbook", "nested_runbook",
    "missing_parent_account", "missing_parent_group", "missing_account", "wrong_account",
    "mixed_account", "duplicate_account", "authorization", "mixed", "duplicate", "wrong_target"])
def test_final_profile_absence_accepts_only_exact_owned_automation_child_not_found(repository_root,tmp_path,case):
    from cloud_network_benchmark.azure_provider import _verify_profile_absence
    import hashlib
    now=datetime.now(timezone.utc); d=deployment(repository_root); o=options(tmp_path,d.admission.priority)
    account=(f"/subscriptions/{o.subscription_id}/resourceGroups/{_slug(d)}"
        f"/providers/Microsoft.Automation/automationAccounts/{_slug(d)}-auto")
    job_id="00000000-0000-0000-0000-000000000123"
    collection=("schedules" if case=="actual_schedule" else
        "runbooks" if case in {"actual_runbook","nested_runbook"} else "jobSchedules")
    child_name=(_slug(d)+"-once" if collection=="schedules" else
        _slug(d)+"-deallocate" if collection=="runbooks" else job_id)
    child_url=f"{account}/{collection}/{child_name}?api-version={azure_provider.AUTOMATION_API}"
    baseline={"groups":[],"watchers":[]}
    baseline["sha256"]=hashlib.sha256(json.dumps(baseline,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    expiry_child=({"schedule":child_name} if collection=="schedules" else
        {"runbook":child_name} if collection=="runbooks" else {"job_schedule_id":job_id})
    state={"resource_group":_slug(d),"expiry":{"automation_account_id":account,**expiry_child},
        "profile_external_baseline":baseline}
    actual='ERROR: Not Found({"code":"NotFound","message":"Job Schedule not found."})'
    if case=="authorization": error='ERROR: Not Found({"code":"AuthorizationFailed","message":"Job Schedule not found."})'
    elif case=="mixed": error=actual
    elif case=="duplicate": error='ERROR: Not Found({"code":"NotFound","code":"AuthorizationFailed","message":"Job Schedule not found."})'
    elif case in {"actual_schedule","actual_runbook"}: error='ERROR: Not Found({"code":"NotFound","message":""})'
    elif case=="nested_runbook":
        message=(f"The Resource 'Microsoft.Automation/automationAccounts/{_slug(d)}-auto/runbooks/{child_name}' "
            f"under resource group '{_slug(d)}' was not found. For more details please go to "
            "https://aka.ms/ARMResourceNotFoundFix")
        error="ERROR: Not Found("+json.dumps({"error":{"code":"ResourceNotFound","message":message}})+")"
    else: error=actual
    class Runner:
        def __init__(self): self.requests=[]
        def run(self,request):
            self.requests.append(request)
            now_result=now
            if request.action_type=="profile_absence_group":
                return CommandResult(action_id=request.action_id,outcome=CommandOutcome.SUCCEEDED,
                    started_at=now_result,finished_at=now_result,duration_seconds=0,exit_code=0,stdout="false",stderr="")
            if request.action_type in {"profile_absence_resources","profile_absence_groups",
                    "profile_absence_role_assignments","profile_absence_role_definitions"}:
                return CommandResult(action_id=request.action_id,outcome=CommandOutcome.SUCCEEDED,
                    started_at=now_result,finished_at=now_result,duration_seconds=0,exit_code=0,stdout='{"value":[]}',stderr="")
            url=request.argv[request.argv.index("--url")+1]
            if case=="wrong_target" and url==account+"?api-version="+azure_provider.AUTOMATION_API:
                stderr=actual; stdout=""
            elif case in {"missing_account","wrong_account","mixed_account","duplicate_account"} and url==account+"?api-version="+azure_provider.AUTOMATION_API:
                account_name="foreign-auto" if case=="wrong_account" else _slug(d)+"-auto"
                message=(f"The Resource 'Microsoft.Automation/automationAccounts/{account_name}' "
                    f"under resource group '{_slug(d)}' was not found. For more details please go to "
                    "https://aka.ms/ARMResourceNotFoundFix")
                if case=="duplicate_account":
                    stderr='ERROR: Not Found({"error":{"code":"ResourceNotFound","code":"AuthorizationFailed","message":"'+message+'"}})'; stdout=""
                elif case=="mixed_account":
                    stderr="ERROR: Not Found("+json.dumps({"error":{"code":"ResourceNotFound","message":message}})+")"; stdout="ambiguous"
                else:
                    stderr="ERROR: Not Found("+json.dumps({"error":{"code":"ResourceNotFound","message":message}})+")"; stdout=""
            elif case=="missing_parent_account" and url.startswith(account+"/"):
                message=(f"The Resource 'Microsoft.Automation/automationAccounts/{_slug(d)}-auto' "
                    f"under resource group '{_slug(d)}' was not found. For more details please go to "
                    "https://aka.ms/ARMResourceNotFoundFix")
                stderr="ERROR: Not Found("+json.dumps({"error":{"code":"ResourceNotFound","message":message}})+")"; stdout=""
            elif case=="missing_parent_group" and url.startswith(account+"/"):
                stderr="ERROR: Not Found("+json.dumps({"error":{"code":"ResourceGroupNotFound",
                    "message":f"Resource group '{_slug(d)}' could not be found."}})+")"; stdout=""
            elif url==child_url:
                stderr=error; stdout="noise" if case=="mixed" else ""
            else:
                stderr='{"error":{"code":"ResourceNotFound"}}'; stdout=""
            return CommandResult(action_id=request.action_id,outcome=CommandOutcome.FAILED,
                started_at=now_result,finished_at=now_result,duration_seconds=0,exit_code=1,stdout=stdout,stderr=stderr)
    runner=Runner(); actions=[]
    if case in {"actual","actual_schedule","actual_runbook","nested_runbook","missing_account",
            "missing_parent_account","missing_parent_group"}:
        _verify_profile_absence(d,o,runner,tmp_path,lambda:1.0,state,actions)
        request=next(r for r in runner.requests if "--url" in r.argv
            and f"/{collection}/" in r.argv[r.argv.index("--url")+1])
        assert request.action_type=="profile_absence_automation"
        assert request.argv[request.argv.index("--url")+1]==child_url
    else:
        with pytest.raises(RuntimeError):
            _verify_profile_absence(d,o,runner,tmp_path,lambda:1.0,state,actions)



def test_missing_expiry_is_rejected_before_workspace_mutation(repository_root: Path, tmp_path: Path):
    d=deployment(repository_root); o=options(tmp_path, d.admission.priority)
    class Runner:
        def run(self, request): raise AssertionError("runner must not be called")
    with pytest.raises(ValueError, match="confirmed Azure deallocation"):
        provision(d,o,Runner(),clock=lambda:1.0,utc_now=lambda:datetime.now(timezone.utc))
    assert not o.attempt_root.exists()


@pytest.mark.parametrize("started,should_apply", [(800.0, True), (840.0, False)])
def test_azure_saved_review_uses_stage_budget_and_clamps_each_command(
        repository_root: Path, tmp_path: Path, monkeypatch, started: float, should_apply: bool):
    """Queue and manual review share the stage budget; Terraform calls stay <=120s."""
    import cloud_network_benchmark.terraform_plan as plan_review
    monkeypatch.setattr(plan_review, "reviewed_apply", _real_reviewed_apply)

    d = deployment(repository_root)
    o = options(tmp_path / "attempt", d.admission.priority)
    o = o.model_copy(update={"command_timeout_seconds": 120,
        "plan_review_timeout_seconds": 120,
        "plan_review_admission_path": tmp_path / "review-queue",
        "workflow_stage_deadline_monotonic": 1000.0})
    root = repository_root / "terraform" / "azure" / "pair"
    vars_file = tmp_path / "vars.json"
    vars_file.write_text("{}")
    state = tmp_path / "state.tfstate"
    elapsed = [started]
    monkeypatch.setattr(plan_review.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(plan_review.time, "time", lambda: 1_800_000_000 + elapsed[0])
    monkeypatch.setattr(plan_review, "validate_scope", lambda *args: {"resource_changes": []})
    monkeypatch.setattr(plan_review, "original_review_cutoff", lambda *args: 9_999_999_999.0)
    monkeypatch.setattr(plan_review, "_review_admission_lock",
        lambda *args: elapsed.__setitem__(0, elapsed[0] + 110) or None)

    def wait_for_review(directory, binding, deadline):
        elapsed[0] += 55
        if elapsed[0] >= deadline:
            raise TimeoutError("review exceeded original stage budget")
        return {"decision": "approved", "binding": binding}

    monkeypatch.setattr(plan_review, "await_review", wait_for_review)
    class Runner:
        def __init__(self): self.requests = []
        def run(self, request):
            self.requests.append(request)
            argv = request.argv
            if "plan" in argv:
                Path(next(arg.split("=", 1)[1] for arg in argv if arg.startswith("-out="))).write_bytes(b"plan")
            elapsed[0] += 5
            out = "{}" if "show" in argv else "{}"
            now = datetime.now(timezone.utc)
            return CommandResult(action_id=request.action_id, outcome=CommandOutcome.SUCCEEDED,
                started_at=now, finished_at=now, duration_seconds=0, exit_code=0, stdout=out, stderr="")
    runner = Runner()
    command = ["terraform", f"-chdir={root}", "apply", "-input=false",
        f"-state={state}", f"-var-file={vars_file}"]
    if should_apply:
        result = azure_provider._command(runner, o, tmp_path, command, "pair_apply", lambda: elapsed[0])
        assert result.outcome == CommandOutcome.SUCCEEDED
        assert ["apply" in request.argv for request in runner.requests].count(True) == 1
    else:
        with pytest.raises((TimeoutError, ValueError)):
            azure_provider._command(runner, o, tmp_path, command, "pair_apply", lambda: elapsed[0])
        assert not any("apply" in request.argv for request in runner.requests)
    assert runner.requests
    assert all(request.timeout_seconds <= 120 for request in runner.requests)


class AutomationRunner:
    def __init__(self, deployment_input, options_input, now, fail_at=None, publish_delay=0, bad_key_for=None, wrong_vm_id_for=None,
                 stale_nonce_for=None, image_architecture="x64", wrong_role_definition=False,
                 image_publisher="Canonical", image_offer=None, image_sku=None, vm_image_mismatch=False,
                 image_os="Linux", module_recheck=None, marketplace_response=None, schedule_timezone=None):
        self.d=deployment_input; self.o=options_input; self.now=now; self.fail_at=fail_at; self.publish_delay=publish_delay; self.bad_key_for=bad_key_for; self.wrong_vm_id_for=wrong_vm_id_for
        self.stale_nonce_for=stale_nonce_for; self.image_architecture=image_architecture; self.wrong_role_definition=wrong_role_definition
        self.image_publisher=image_publisher; self.image_offer=image_offer; self.image_sku=image_sku; self.vm_image_mismatch=vm_image_mismatch
        self.image_os=image_os; self.module_recheck=module_recheck; self.marketplace_response=marketplace_response
        self.schedule_timezone=schedule_timezone
        self.publish_reads=0; self.requests=[]; self.script=""; self.schedule={}; self.binding={}; self.tamper_recheck=None
        self.name=_slug(deployment_input)
        self.account=f"/subscriptions/{options_input.subscription_id}/resourceGroups/{self.name}/providers/Microsoft.Automation/automationAccounts/{self.name}-auto"
        self.assignment_id=(f"/subscriptions/{options_input.subscription_id}/resourceGroups/{self.name}"
            "/providers/Microsoft.Authorization/roleAssignments/00000000-0000-0000-0000-000000000001")
        self.role_id=(f"/subscriptions/{options_input.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/"
            "00000000-0000-0000-0000-000000000002")
        self.group_id=f"/subscriptions/{options_input.subscription_id}/resourceGroups/{self.name}"
    def run(self, request):
        self.requests.append(request)
        if request.action_type in {"profile_watcher_baseline_resources", "profile_watcher_baseline_groups"}:
            return CommandResult(action_id=request.action_id,outcome=CommandOutcome.SUCCEEDED,
                started_at=self.now,finished_at=self.now,duration_seconds=0,exit_code=0,
                stdout=json.dumps({"value":[]}),stderr="")
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
        elif request.action_type in {"published_content_readback","expiry_content_recheck"}: out=self.script+"\n"
        elif request.action_type in {"schedule_readback","expiry_schedule_recheck"}:
            p=dict(self.schedule["properties"]); p["isEnabled"]=self.tamper_recheck!="schedule" if request.action_type=="expiry_schedule_recheck" else True
            if self.schedule_timezone is not None: p["timeZone"]=self.schedule_timezone
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
            out=json.dumps([{"id":self.assignment_id,"principalId":"00000000-0000-0000-0000-000000000003","scope":scope,"roleDefinitionId":role_definition}])
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
        return CommandResult(action_id=request.action_id,outcome=outcome,started_at=self.now,finished_at=self.now,duration_seconds=0,
            exit_code=1 if outcome==CommandOutcome.FAILED else 0,stdout=out,stderr="injected failure" if outcome==CommandOutcome.FAILED else "")


class CleanupRunner(AutomationRunner):
    def __init__(self, *args, residual=False, secret_output="", blank_inventory=False, fail_delete_once=None,
                 account_present=True, account_resource_id=None, account_absence="absent",
                 present_children=None, role_definition_inventory="[]", role_assignment_inventory="[]",
                 missing_automation_account=False, **kwargs):
        super().__init__(*args, **kwargs); self.residual=residual; self.secret_output=secret_output; self.blank_inventory=blank_inventory
        self.fail_delete_once=fail_delete_once; self.failed_delete=False; self.deleted_children=set(); self.absent_children=set()
        self.account_present=account_present
        self.account_resource_id=account_resource_id
        self.account_absence=account_absence
        self.missing_automation_account=missing_automation_account
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
        if request.action_type=="parent_group_absence":
            self.requests.append(request); out=json.dumps(self.account_present)
        elif request.action_type=="pair_inventory_page_2":
            self.requests.append(request)
            resources=[] if not self.residual else [{"id":self.group_id+"/providers/Microsoft.Network/networkInterfaces/leftover","type":"Microsoft.Network/networkInterfaces"}]
            out=json.dumps({"value":resources})
        elif request.action_type=="pair_inventory":
            self.requests.append(request)
            url=request.argv[request.argv.index("--url")+1].split("?",1)[0]
            resources=[{"id":self.account_resource_id or self.account,"type":"Microsoft.Automation/automationAccounts"}] if self.account_present else []
            out="" if self.blank_inventory else json.dumps({"value":resources,"nextLink":url+"?api-version=2021-04-01&$skiptoken=two"})
        elif request.action_type=="expiry_inventory": self.requests.append(request); out="false"
        elif request.action_type=="role_cleanup_inventory": self.requests.append(request); out=self.role_assignment_inventory
        elif request.action_type=="role_definition_cleanup_inventory": self.requests.append(request); out=self.role_definition_inventory
        elif request.action_type=="automation_account_absence":
            self.requests.append(request)
            if self.account_absence == "resource_group_missing_cli":
                group=_slug(self.d); message=f"Resource group '{group}' could not be found."
                return CommandResult(action_id=request.action_id,outcome=CommandOutcome.FAILED,
                    started_at=self.now,finished_at=self.now,duration_seconds=0,exit_code=1,stdout="",
                    stderr=f"ERROR: (ResourceGroupNotFound) {message}\nCode: ResourceGroupNotFound\nMessage: {message}\n")
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
            if self.missing_automation_account:
                message=(f"The Resource 'Microsoft.Automation/automationAccounts/{_slug(self.d)}-auto' "
                    f"under resource group '{_slug(self.d)}' was not found. For more details please go to "
                    "https://aka.ms/ARMResourceNotFoundFix")
                stderr="ERROR: Not Found("+json.dumps({"error":{"code":"ResourceNotFound","message":message}})+")\n"
                return CommandResult(action_id=request.action_id,outcome=CommandOutcome.FAILED,
                    started_at=self.now,finished_at=self.now,duration_seconds=0,exit_code=1,stdout="",stderr=stderr)
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


def profile_attempt(repository_root: Path,attempt_root: Path,now: datetime):
    from cloud_network_benchmark.contracts.common import Evidence
    profile="azure_exp901_sweden_acceptance_v1"
    base=deployment(repository_root)
    start=now-timedelta(seconds=100)
    reservation=base.reservation.value.model_copy(update={"timing_profile":profile,
        "cap_started_at":Evidence(value=start),"expires_at":Evidence(value=start+timedelta(seconds=1800))})
    admission=base.admission.model_copy(update={"timing_profile":profile,"lifetime_cap_seconds":1800})
    d=base.model_copy(update={"admission":admission,"reservation":Evidence(value=reservation),
        "vm_a":base.vm_a.model_copy(update={"region":"swedencentral"}),
        "vm_b":base.vm_b.model_copy(update={"region":"swedencentral"})})
    allowance={"registration_seconds":180,"provision_seconds":240,"readiness_seconds":180,
        "stabilization_seconds":60,"measurement_seconds":100,"measurement_shutdown_seconds":15,
        "retrieval_seconds":45,"cleanup_seconds":660,"azure_provider_lead_seconds":120}
    value=options(attempt_root,d.admission.priority).model_dump(mode="python")
    value.update(complete_workflow_seconds=1480,post_registration_workflow_seconds=1300,
        cap_deadline_monotonic=2700,retry_end_at=start+timedelta(seconds=1800),
        timing_profile=profile,cleanup_timing_path=attempt_root/"f09-private"/"azure-cleanup-timing"/"timer.json",
        profile_allowance=allowance,command_timeout_seconds=180)
    return d,AzureRuntimeOptions.model_validate(value),start


def register_profile_expiry(d,o,start,now,runner,*,clock=1000.0):
    # Match the source-bound cap origin established by default factory preparation.
    origin_mono=clock-(now-start).total_seconds()
    azure_provider._initialize_profile_timing(d,o,clock=lambda:origin_mono,utc_now=lambda:start)
    return register_expiry(d,expiry_request(d,o,start+timedelta(seconds=1680)),o,runner,
        clock=lambda:clock,utc_now=lambda:now)


def test_saved_s3_clock_mapping_registers_against_original_cap_sample(repository_root,tmp_path,monkeypatch):
    """Offline replay of the preserved S3 timing values with a fake runner."""
    from cloud_network_benchmark.contracts.common import Evidence

    origin_utc=datetime.fromisoformat("2026-11-23T15:53:48.107353+00:00")
    origin_mono=1059650.89673908
    cap_started_at=datetime.fromisoformat("2026-11-23T15:53:46.267707+00:00")
    cap_expires_at=datetime.fromisoformat("2026-11-23T17:03:46.267707+00:00")
    cap_deadline=1063849.057103963
    second_sample_cap=origin_mono+(cap_expires_at-origin_utc).total_seconds()
    factory_cap=min(cap_deadline,second_sample_cap)
    now=origin_utc+timedelta(seconds=1)
    d=deployment(repository_root,"exp-903-three-provider-smoke.yaml")
    reservation=d.reservation.value.model_copy(update={
        "workflow_profile":"all_cloud_acceptance_v1",
        "cap_started_at":Evidence(value=cap_started_at),"expires_at":Evidence(value=cap_expires_at)})
    admission=d.admission.model_copy(update={"workflow_profile":"all_cloud_acceptance_v1",
        "lifetime_cap_seconds":4200})
    d=d.model_copy(update={"admission":admission,"reservation":Evidence(value=reservation)})
    value=options(tmp_path,d.admission.priority).model_dump(mode="python")
    value.update(complete_workflow_seconds=3535,post_registration_workflow_seconds=2635,
        cap_deadline_monotonic=factory_cap,cap_started_monotonic=cap_deadline-4200.0,
        retry_end_at=cap_expires_at,workflow_profile="all_cloud_acceptance_v1",
        cleanup_timing_path=tmp_path/"private"/"azure-clock-map.json",
        profile_allowance={"registration_seconds":900,"provision_seconds":900,
            "readiness_seconds":600,"stabilization_seconds":60,"measurement_seconds":100,
            "measurement_shutdown_seconds":15,"retrieval_seconds":300,"cleanup_seconds":660,
            "azure_provider_lead_seconds":120})
    o=AzureRuntimeOptions.model_validate(value)

    # The old second-sample comparison rejects these original numbers by 10.88us.
    assert cap_deadline > second_sample_cap+1e-6
    assert o.cap_deadline_monotonic == second_sample_cap
    assert o.cap_deadline_monotonic < cap_deadline
    assert o.cap_deadline_monotonic <= azure_provider._source_cap_deadline(o,reservation)+1e-6

    monkeypatch.setattr(azure_provider,"_clock_identity",lambda:
        "9b1cc84e4aaa6905066a2c10c49aaf2d277d8eb394364da05ec60206638994fd")
    azure_provider._initialize_profile_timing(d,o,clock=lambda:origin_mono,utc_now=lambda:origin_utc,
        origin_monotonic=origin_mono,origin_utc=origin_utc)
    runner=AutomationRunner(d,o,now)
    requested=cap_expires_at-timedelta(seconds=120)
    result=register_expiry(d,expiry_request(d,o,requested),o,runner,
        clock=lambda:origin_mono+1.0,utc_now=lambda:now)
    assert result.state=="registered",result.failure
    assert result.provider_metadata.value["requested_trigger"] == requested.isoformat()
    assert result.provider_metadata.value["trigger"] == requested.replace(second=0,microsecond=0).isoformat()
    assert runner.requests


def test_original_cap_monotonic_sample_rejects_overlong_runtime_cap(repository_root,tmp_path):
    from cloud_network_benchmark.contracts.common import Evidence

    now=datetime(2026,11,23,15,53,49,tzinfo=timezone.utc)
    d,o,_=profile_attempt(repository_root,tmp_path,now)
    cap_started_at=datetime.fromisoformat("2026-11-23T15:53:46.267707+00:00")
    cap_expires_at=datetime.fromisoformat("2026-11-23T17:03:46.267707+00:00")
    reservation=d.reservation.value.model_copy(update={
        "cap_started_at":Evidence(value=cap_started_at),"expires_at":Evidence(value=cap_expires_at)})
    d=d.model_copy(update={"reservation":Evidence(value=reservation)})
    original_start_mono=1059649.057103963
    o=o.model_copy(update={"cap_started_monotonic":original_start_mono,
        "cap_deadline_monotonic":original_start_mono+4200.000002})
    with pytest.raises(ValueError,match="exceeds the original admission monotonic cap"):
        azure_provider._initialize_profile_timing(d,o,clock=lambda:1059650.0,
            utc_now=lambda:datetime.fromisoformat("2026-11-23T15:53:47.0+00:00"))
    assert not o.cleanup_timing_path.exists()


@pytest.mark.parametrize("anchor_state", ["missing","corrupt","foreign"])
def test_profile_registration_requires_original_timer_before_any_provider_call(
        repository_root: Path,tmp_path: Path,anchor_state):
    now=datetime(2026,11,20,12,1,40,tzinfo=timezone.utc)
    d,o,start=profile_attempt(repository_root,tmp_path,now)
    if anchor_state=="corrupt":
        o.cleanup_timing_path.parent.mkdir(parents=True,exist_ok=True)
        o.cleanup_timing_path.write_text('{"schema_version":1,"sha256":"broken"}')
        o.cleanup_timing_path.chmod(0o600)
    elif anchor_state=="foreign":
        foreign=d.model_copy(update={"run_id":"foreign-profile-run"})
        origin_mono=1000.0-(now-start).total_seconds()
        azure_provider._initialize_profile_timing(foreign,o,clock=lambda:origin_mono,utc_now=lambda:start)
    runner=AutomationRunner(d,o,now)
    evidence=register_expiry(d,expiry_request(d,o,start+timedelta(seconds=1680)),o,runner,
        clock=lambda:1000.0,utc_now=lambda:now)
    assert evidence.state=="failed"
    assert runner.requests==[]
    assert not (o.attempt_root/"recovery.json").exists()


class ProfileCleanupRunner(CleanupRunner):
    def __init__(self,*args,fail_first_absence=False,advance_clock_on_expiry=None,unowned_watcher=False,
                 advance_clock_on_action=None,corrupt_timer_on_final=False,residual_attempt_group=False,
                 residual_role=None,role_pages=False,capture_timing=False,missing_group_error=None,**kwargs):
        super().__init__(*args,**kwargs); self.fail_first_absence=fail_first_absence; self.failed_absence=False
        self.advance_clock_on_expiry=advance_clock_on_expiry
        self.advance_clock_on_action=advance_clock_on_action
        self.unowned_watcher=unowned_watcher
        self.corrupt_timer_on_final=corrupt_timer_on_final
        self.timer_corruptions=0
        self.residual_attempt_group=residual_attempt_group
        self.residual_role=residual_role
        self.role_pages=role_pages or residual_role is not None
        self.capture_timing=capture_timing
        self.timing_snapshot=None
        self.missing_group_error=missing_group_error
    def run(self, request):
        if request.action_type == "profile_absence_group":
            self.requests.append(request)
            if self.fail_first_absence and not self.failed_absence:
                self.failed_absence=True; out="true"
            else: out="false"
        elif request.action_type in {"profile_absence_resources","profile_absence_role_assignments"}:
            self.requests.append(request)
            values=([{"id":f"/subscriptions/{self.o.subscription_id}/resourceGroups/NetworkWatcherRG/providers/Microsoft.Network/networkWatchers/NetworkWatcher_swedencentral",
                "type":"Microsoft.Network/networkWatchers"}] if self.unowned_watcher and request.action_type=="profile_absence_resources" else [])
            out=json.dumps({"value":values})
        elif request.action_type in {"profile_absence_role_definitions","profile_absence_role_definitions_page_2"}:
            self.requests.append(request)
            url=request.argv[request.argv.index("--url") + 1]
            assert "%24filter=type%20eq%20%27CustomRole%27" in url
            role_id=f"/subscriptions/{self.o.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/owned-custom-role"
            role_name=_slug(self.d)+"-deallocate-only"
            if request.action_type.endswith("page_2"):
                values=([{"id":role_id,"properties":{"roleName":role_name}}] if self.residual_role else
                    [{"id":f"/subscriptions/{self.o.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/other-custom-role",
                      "properties":{"roleName":"Unrelated custom role"}}])
                out=json.dumps({"value":values})
            else:
                values=[{"id":f"/subscriptions/{self.o.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/page-one-custom-role",
                    "properties":{"roleName":"Unrelated first-page custom role"}}]
                payload={"value":values}
                if self.role_pages or self.residual_role:
                    payload["nextLink"]=url.split("?",1)[0]+"?%24filter=type%20eq%20%27CustomRole%27&api-version=2022-04-01&$skiptoken=page-two"
                out=json.dumps(payload)
            if self.capture_timing and self.o.cleanup_timing_path.is_file():
                self.timing_snapshot=json.loads(self.o.cleanup_timing_path.read_text(encoding="utf-8"))
        elif request.action_type=="profile_absence_groups":
            self.requests.append(request)
            values=([{"id":f"/subscriptions/{self.o.subscription_id}/resourceGroups/NetworkWatcherRG",
                "name":"NetworkWatcherRG"}] if self.unowned_watcher else [])
            if self.residual_attempt_group:
                values.append({"id":f"/subscriptions/{self.o.subscription_id}/resourceGroups/{_slug(self.d)}",
                    "name":_slug(self.d)})
            out=json.dumps({"value":values})
        elif request.action_type == "profile_absence_automation":
            self.requests.append(request)
            url=request.argv[request.argv.index("--url")+1]
            if self.missing_group_error is not None:
                group=("foreign-group" if self.missing_group_error=="foreign" else _slug(self.d))
                message=f"Resource group '{group}' could not be found."
                stderr="ERROR: Not Found("+json.dumps({"error":{"code":"ResourceGroupNotFound",
                    "message":message}},separators=(",",":"))+")"
                return CommandResult(action_id=request.action_id,outcome=CommandOutcome.FAILED,
                    started_at=self.now,finished_at=self.now,duration_seconds=0,exit_code=1,
                    stdout="",stderr=stderr)
            if self.missing_automation_account and any(path in url for path in ("/jobSchedules/","/schedules/","/runbooks/")):
                message=(f"The Resource 'Microsoft.Automation/automationAccounts/{_slug(self.d)}-auto' "
                    f"under resource group '{_slug(self.d)}' was not found. For more details please go to "
                    "https://aka.ms/ARMResourceNotFoundFix")
                return CommandResult(action_id=request.action_id,outcome=CommandOutcome.FAILED,
                    started_at=self.now,finished_at=self.now,duration_seconds=0,exit_code=1,stdout="",
                    stderr="ERROR: Not Found("+json.dumps({"error":{"code":"ResourceNotFound","message":message}})+")\n")
            if self.corrupt_timer_on_final and any("/runbooks/" in arg for arg in request.argv):
                self.o.cleanup_timing_path.write_text('{"schema_version":1,"sha256":"corrupt"}')
                self.timer_corruptions+=1
            return CommandResult(action_id=request.action_id,outcome=CommandOutcome.FAILED,
                started_at=self.now,finished_at=self.now,duration_seconds=0,exit_code=1,
                stdout="",stderr='{"error":{"code":"ResourceNotFound"}}')
        if request.action_type not in {
            "profile_absence_group","profile_absence_resources","profile_absence_role_assignments",
            "profile_absence_role_definitions","profile_absence_role_definitions_page_2",
            "profile_absence_groups","profile_absence_automation"}:
            result=super().run(request)
            if request.action_type=="expiry_destroy" and self.advance_clock_on_expiry:
                self.advance_clock_on_expiry()
            if self.advance_clock_on_action:
                elapsed=self.advance_clock_on_action(request.action_type)
                if elapsed:
                    self.now += timedelta(seconds=elapsed)
                    result=result.model_copy(update={"finished_at":self.now,"duration_seconds":elapsed})
            return result
        return CommandResult(
                action_id=request.action_id,outcome=CommandOutcome.SUCCEEDED,started_at=self.now,
                finished_at=self.now,duration_seconds=0,exit_code=0,stdout=out,stderr="")


@pytest.mark.parametrize("cleanup_mono,expected_cleanup,expected_timing", [
    (1100.0,"succeeded","within_deadline"),(2520.0,"succeeded","late")])
def test_profile_public_cleanup_uses_fresh_absence_and_sticky_timing_failure(
        repository_root: Path,tmp_path: Path,cleanup_mono,expected_cleanup,expected_timing):
    now=datetime(2026,11,20,12,1,40,tzinfo=timezone.utc)
    d,o,start=profile_attempt(repository_root,tmp_path,now)
    registered_clock=1000.0
    registration=AutomationRunner(d,o,now)
    evidence=register_profile_expiry(d,o,start,now,registration,clock=registered_clock)
    assert evidence.state=="registered"
    assert o.cleanup_timing_path.exists()
    runner=ProfileCleanupRunner(d,o,now,role_pages=True)
    result=destroy(d.model_copy(update={"expiry":type(d.expiry)(value=evidence)}),o,runner,
        clock=lambda:cleanup_mono,utc_now=lambda:now+timedelta(seconds=cleanup_mono-registered_clock))
    assert result.cleanup_state==expected_cleanup and not result.launch_blocked,result.failures
    assert result.timing_status==expected_timing
    if expected_timing=="within_deadline":
        assert result.timing_failure is None
    else:
        assert result.timing_failure is not None
    assert any(r.action_type=="profile_absence_role_definitions" for r in runner.requests)
    assert any(r.action_type=="profile_absence_role_definitions_page_2" for r in runner.requests)
    assert any(r.action_type=="profile_absence_automation" for r in runner.requests)


def test_cleanup_resume_accepts_exact_missing_automation_account_for_all_owned_children(
        repository_root:Path,tmp_path:Path):
    now=datetime(2026,11,20,12,1,40,tzinfo=timezone.utc)
    d,o,start=profile_attempt(repository_root,tmp_path,now)
    registration=AutomationRunner(d,o,now)
    expiry=register_profile_expiry(d,o,start,now,registration)
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    runner=ProfileCleanupRunner(d,o,now,role_pages=True,missing_automation_account=True,
        account_absence="resource_group_missing_cli")
    result=destroy(registered,o,runner,clock=lambda:1100.0,
        utc_now=lambda:now+timedelta(seconds=100))
    assert result.cleanup_state=="succeeded" and not result.launch_blocked,result.failures
    actions=[request.action_type for request in runner.requests]
    assert all(actions.count(f"expiry_{collection}_before_delete")==1
        for collection in ("jobSchedules","schedules","runbooks"))
    assert not any(action in actions for action in
        ("expiry_jobSchedules_delete","expiry_schedules_delete","expiry_runbooks_delete"))
    assert "expiry_destroy" in actions and "automation_account_absence" in actions
    assert actions.count("profile_absence_automation")==4
    assert {"role_cleanup_inventory","role_definition_cleanup_inventory"}.issubset(actions)


@pytest.mark.parametrize(("missing_group_error","expected_cleanup"), [
    ("exact","succeeded"),("foreign","failed")])
def test_profile_absence_cli_missing_group_requires_exact_owned_parent(
        repository_root: Path,tmp_path: Path,missing_group_error,expected_cleanup):
    now=datetime(2026,11,20,12,1,40,tzinfo=timezone.utc)
    d,o,start=profile_attempt(repository_root,tmp_path,now)
    registration=AutomationRunner(d,o,now)
    evidence=register_profile_expiry(d,o,start,now,registration)
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=evidence)})
    runner=ProfileCleanupRunner(d,o,now,missing_group_error=missing_group_error)
    result=destroy(registered,o,runner,clock=lambda:1100.0,utc_now=lambda:now+timedelta(seconds=100))
    assert result.cleanup_state==expected_cleanup
    group_check=next(i for i,r in enumerate(runner.requests) if r.action_type=="profile_absence_group")
    account_checks=[(i,r) for i,r in enumerate(runner.requests) if r.action_type=="profile_absence_automation"]
    assert account_checks and group_check < account_checks[0][0]
    expected_prefix=(f"/subscriptions/{o.subscription_id}/resourceGroups/{_slug(d)}/providers/Microsoft.Automation/").lower()
    account_urls=[r.argv[r.argv.index("--url") + 1].lower() for _,r in account_checks]
    assert all(url.startswith(expected_prefix) for url in account_urls)
    if missing_group_error=="exact":
        assert not result.launch_blocked and result.timing_status=="within_deadline"
    else:
        assert result.launch_blocked
        assert any("profile_absence_automation failed" in failure.message for failure in result.failures)


def test_missing_stop_full_cleanup_closes_copied_ledger_but_keeps_acceptance_unknown(
        repository_root: Path,tmp_path: Path):
    from types import SimpleNamespace
    from cloud_network_benchmark.lifecycle_cleanup import save_cleanup_settings, reconcile_saved_cleanup_result
    from cloud_network_benchmark.orchestration import cleanup_lifecycle

    now=datetime(2026,11,20,12,1,40,tzinfo=timezone.utc)
    d,o,start=profile_attempt(repository_root,tmp_path,now)
    cap=d.reservation.value.expires_at.value
    # Match the incident's pre-apply journal shape: timer exists, no registered
    # STOP, no prior cleanup entry, no expiry apply, and no resource creation.
    azure_provider._initialize_profile_timing(d,o,clock=lambda:1000.0,utc_now=lambda:start)
    root,journal=azure_provider._workspace(o,d)
    pre_state=json.loads(journal.read_text(encoding="utf-8"))
    assert pre_state["creation_may_have_started"] is False
    assert pre_state.get("expiry") is None
    timer=json.loads(o.cleanup_timing_path.read_text(encoding="utf-8"))
    assert timer["provider_stop_at"] is None and timer["first_cleanup_monotonic"] is None

    request=expiry_request(d,o,start+timedelta(seconds=1680))
    settings_path=save_cleanup_settings(provider="azure",deployment=d,request=request,
        reservation=d.reservation.value,provider_lead_seconds=120.0,settings=o,
        private_root=tmp_path/"saved-cleanup")
    runner=ProfileCleanupRunner(d,o,now,account_present=False,role_pages=True,capture_timing=True)
    physical=destroy(d,o,runner,clock=lambda:1000.0,utc_now=lambda:now)

    assert physical.cleanup_state=="succeeded" and physical.launch_blocked is False, physical.failures
    assert physical.timing_status=="unknown" and physical.timing_failure is not None
    assert runner.timing_snapshot is not None
    assert runner.timing_snapshot["provider_stop_at"] is None
    assert runner.timing_snapshot["first_cleanup_monotonic"] is None
    assert runner.timing_snapshot["first_cleanup_at"] is None
    assert runner.timing_snapshot["cap_deadline_monotonic"]==o.cap_deadline_monotonic
    assert runner.timing_snapshot["cap_expires_at"]==cap.isoformat()
    assert all(not ("apply" in item.argv and any(arg.startswith("-chdir=") for arg in item.argv))
        for item in runner.requests)

    reservation_id=d.reservation.value.reservation_id.value
    ledger_path=tmp_path/"isolated-copied-ledger.json"
    ledger_path.write_text("isolated fake ledger; never connected to the original ledger\n")
    class CopiedLedger:
        path=ledger_path
        scope_id="isolated-e901d168-copy"
        def __init__(self):
            self.state={"reservations":{reservation_id:{
                "evidence":d.reservation.value.model_dump(mode="json"),"provider_cleanup":{}}}}
            self.events=[]
        def inspect(self):
            return json.loads(json.dumps(self.state))
        def reconcile(self,event):
            self.events.append(event)
            row=self.state["reservations"][event.reservation_id]
            row.setdefault("provider_cleanup",{})[event.provider]=event.cleanup_verified_absent
    copied_ledger=CopiedLedger()

    recovery_path=tmp_path/"outer-recovery"/"lifecycle-recovery.json"
    recovery_path.parent.mkdir(mode=0o700)
    recovery_path.write_text(json.dumps({"schema_version":1,"attempt_id":d.run_id,
        "controller_session_id":"offline-copied-ledger-test","reservation_id":reservation_id,
        "ledger_path":str(ledger_path),"ledger_scope_id":copied_ledger.scope_id,
        "providers":[{"provider":"azure","deployment_id":d.deployment_id,
            "journal_reference":str(journal),"expiry_registered":False,
            "creation_may_have_started":False,"cleanup_open":True,
            "cleanup_settings_reference":str(settings_path),
            "cleanup_timing_status":"unknown",
            "cleanup_timing_failure":"actual registered STOP timing anchor is missing"}]}))
    recovery_path.chmod(0o600)
    saved_recovery=SimpleNamespace(reservation_id=reservation_id,ledger_path=str(ledger_path),
        ledger_scope_id=copied_ledger.scope_id)

    def reconcile(record,result):
        return reconcile_saved_cleanup_result(record,result,recovery=saved_recovery,ledger=copied_ledger,
            utc_now=lambda:now+timedelta(seconds=1))

    outcome=cleanup_lifecycle(recovery_path,current_session_id="offline-recovery-session",
        cleanup_actions={"azure":lambda _record:physical},reconcile=reconcile,ledger=copied_ledger)
    row=outcome.providers["azure"]
    assert row["reconciled"] is True and row["cleanup"]=="succeeded", row
    assert row["cleanup_open"] is False and row["launch_blocked"] is False
    assert row["cleanup_timing_status"]=="unknown" and row["acceptance_failed"] is True
    assert copied_ledger.state["reservations"][reservation_id]["provider_cleanup"]["azure"] is True
    assert copied_ledger.events and copied_ledger.events[0].cleanup_verified_absent is True
    assert outcome.complete is False
    persisted=json.loads(recovery_path.read_text(encoding="utf-8"))["providers"][0]
    assert persisted["cleanup_open"] is False
    assert persisted["cleanup_timing_status"]=="unknown"


@pytest.mark.parametrize("residual_role", ["id", "name"])
def test_profile_absence_rejects_residual_attempt_custom_role(repository_root: Path,tmp_path: Path,residual_role):
    now=datetime(2026,11,20,12,1,40,tzinfo=timezone.utc)
    d,o,start=profile_attempt(repository_root,tmp_path,now)
    registration=AutomationRunner(d,o,now)
    evidence=register_profile_expiry(d,o,start,now,registration)
    expiry=type(d.expiry)(value=evidence)
    if residual_role=="id":
        expiry=expiry.model_copy(update={"role_definition_id":
            f"/subscriptions/{o.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/owned-custom-role"})
    runner=ProfileCleanupRunner(d,o,now,residual_role=residual_role)
    result=destroy(d.model_copy(update={"expiry":expiry}),o,runner,
        clock=lambda:1100.0,utc_now=lambda:now+timedelta(seconds=100))
    assert result.cleanup_state=="failed"
    assert any("role-definition inventory found the attempt custom role" in failure.message
        for failure in result.failures)


@pytest.mark.parametrize("bad_page", ["malformed", "foreign", "cyclic"])
def test_filtered_custom_role_inventory_still_rejects_untrustworthy_pages(
        repository_root: Path,tmp_path: Path,bad_page):
    now=datetime(2026,11,20,12,1,40,tzinfo=timezone.utc)
    d,o,_=profile_attempt(repository_root,tmp_path,now)
    class BadInventory:
        def __init__(self): self.requests=[]
        def run(self,request):
            self.requests.append(request)
            url=request.argv[request.argv.index("--url") + 1]
            if bad_page=="malformed": out="{"
            elif bad_page=="foreign":
                out=json.dumps({"value":[],"nextLink":"https://management.azure.com/subscriptions/foreign/providers/Microsoft.Authorization/roleDefinitions?api-version=2022-04-01"})
            else:
                out=json.dumps({"value":[],"nextLink":url})
            return CommandResult(action_id=request.action_id,outcome=CommandOutcome.SUCCEEDED,
                started_at=now,finished_at=now,duration_seconds=0,exit_code=0,stdout=out,stderr="")
    runner=BadInventory()
    endpoint=f"/subscriptions/{o.subscription_id}/providers/Microsoft.Authorization/roleDefinitions?%24filter=type%20eq%20%27CustomRole%27&api-version=2022-04-01"
    with pytest.raises(RuntimeError):
        azure_provider._read_arm_collection(runner,o,repository_root,endpoint,"profile_absence_role_definitions",
            lambda:1100.0,("id",))
    assert len(runner.requests)==1


def test_unchanged_unfiltered_builtin_catalog_reproduces_size_limit(repository_root: Path,tmp_path: Path):
    now=datetime(2026,11,20,12,1,40,tzinfo=timezone.utc)
    d,o,_=profile_attempt(repository_root,tmp_path,now)
    old_endpoint=(f"/subscriptions/{o.subscription_id}/providers/Microsoft.Authorization/roleDefinitions"
        "?api-version=2022-04-01")
    large_builtin=json.dumps({"value":[{"id":f"/subscriptions/{o.subscription_id}/providers/"
        "Microsoft.Authorization/roleDefinitions/builtin-role","properties":{"roleName":"Built-in role "
        +"x"*1_000_100}}]})
    assert len(large_builtin.encode())>1_000_000
    class UnfilteredCatalog:
        def run(self,request):
            assert request.argv[request.argv.index("--url") + 1].endswith("roleDefinitions?api-version=2022-04-01")
            return CommandResult(action_id=request.action_id,outcome=CommandOutcome.SUCCEEDED,
                started_at=now,finished_at=now,duration_seconds=0,exit_code=0,stdout=large_builtin,stderr="")
    with pytest.raises(RuntimeError,match="above the 1 MiB bounded-response limit"):
        azure_provider._read_arm_collection(UnfilteredCatalog(),o,o.attempt_root,old_endpoint,
            "profile_absence_role_definitions",lambda:1100.0,("id",))


def test_workflow_cleanup_after_original_stop_uses_bounded_emergency_window(
        repository_root: Path, tmp_path: Path):
    from cloud_network_benchmark.contracts.common import Evidence
    from cloud_network_benchmark import azure_provider
    from cloud_network_benchmark.validation_profiles import FAILURE_PROFILE
    start = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    d, o, _ = profile_attempt(repository_root, tmp_path, start + timedelta(seconds=100))
    cap_end = start + timedelta(seconds=3900)
    reservation = d.reservation.value.model_copy(update={"timing_profile": None,
        "expires_at": Evidence(value=cap_end)})
    admission = d.admission.model_copy(update={"timing_profile": None,
        "workflow_profile": FAILURE_PROFILE, "lifetime_cap_seconds": 3900})
    d = d.model_copy(update={"admission": admission, "reservation": Evidence(value=reservation)})
    stop = cap_end - timedelta(seconds=720)
    o = o.model_copy(update={"timing_profile": None, "workflow_profile": FAILURE_PROFILE,
        "cap_deadline_monotonic": 4900.0, "retry_end_at": cap_end})
    azure_provider._initialize_profile_timing(d, o, clock=lambda: 1000.0, utc_now=lambda: start)
    azure_provider._bind_profile_stop(d, o, stop)
    late_now = start + timedelta(seconds=3181)
    deadline, state, note = azure_provider._begin_profile_cleanup(d, o,
        clock=lambda: 4181.0, utc_now=lambda: late_now, invocation_seconds=660)
    assert note and state["timing_status"] == "late"
    assert deadline == 4841.0
    assert deadline < o.cap_deadline_monotonic + 1
    assert state["cap_expires_at"] == cap_end.isoformat()
    assert state["provider_stop_at"] == stop.isoformat()

    class EmergencyRunner:
        def __init__(self): self.requests = []
        def run(self, request):
            from cloud_network_benchmark.contracts.execution import CommandResult
            self.requests.append(request)
            return CommandResult(action_id=request.action_id, outcome="succeeded",
                started_at=late_now, finished_at=late_now, duration_seconds=0,
                exit_code=0, stdout="ok", stderr="")

    runner = EmergencyRunner()
    emergency_options = o.model_copy(update={"cap_deadline_monotonic": deadline,
        "cleanup_timing_active": True, "cleanup_timing_phase": "emergency"})
    azure_provider._command(runner, emergency_options, tmp_path, ["az", "group", "exists"],
        "cleanup-recovery-probe", lambda: 4181.0)
    assert len(runner.requests) == 1
    assert runner.requests[0].timeout_seconds <= 660


def test_profile_absence_recovery_uses_separate_window_without_replaying_destroy(
        repository_root: Path,tmp_path: Path):
    now=datetime(2026,11,20,12,1,40,tzinfo=timezone.utc)
    d,o,start=profile_attempt(repository_root,tmp_path,now)
    registration=AutomationRunner(d,o,now)
    evidence=register_profile_expiry(d,o,start,now,registration)
    clock_state={"mono":1000.0}
    advances={"pair_destroy":[150.0,150.0],"expiry_destroy":[150.0,145.0]}
    def advance_action(action):
        values=advances.get(action)
        if not values: return 0.0
        elapsed=values.pop(0); clock_state["mono"]+=elapsed
        return elapsed
    runner=ProfileCleanupRunner(d,o,now,fail_first_absence=True,
        advance_clock_on_action=advance_action)
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=evidence)})
    clock=lambda:clock_state["mono"]
    utc=lambda:now+timedelta(seconds=clock_state["mono"]-1000.0)
    first=destroy(registered,o,runner,clock=clock,utc_now=utc)
    assert first.cleanup_state=="failed" and first.timing_status=="within_deadline"
    assert clock_state["mono"]==1300.0
    clock_state["mono"]=1300.0
    second=destroy(registered,o,runner,clock=clock,utc_now=utc)
    assert second.cleanup_state=="failed" and second.timing_status=="within_deadline"
    assert clock_state["mono"]==1595.0
    prior_destroy_count=sum(r.action_type=="pair_destroy" for r in runner.requests)
    clock_state["mono"]=1601.0
    third=destroy(registered,o,runner,clock=clock,utc_now=utc)
    assert third.cleanup_state=="succeeded" and third.timing_status=="within_deadline"
    assert sum(r.action_type=="pair_destroy" for r in runner.requests)==prior_destroy_count


def test_profile_cleanup_never_claims_absence_for_new_unowned_network_watcher(
        repository_root: Path,tmp_path: Path):
    now=datetime(2026,11,20,12,1,40,tzinfo=timezone.utc)
    d,o,start=profile_attempt(repository_root,tmp_path,now)
    registration=AutomationRunner(d,o,now)
    evidence=register_profile_expiry(d,o,start,now,registration)
    runner=ProfileCleanupRunner(d,o,now,unowned_watcher=True)
    result=destroy(d.model_copy(update={"expiry":type(d.expiry)(value=evidence)}),o,runner,
        clock=lambda:1100.0,utc_now=lambda:now+timedelta(seconds=100))
    assert result.cleanup_state=="failed" and result.launch_blocked
    assert any("Network Watcher" in item.message for item in result.failures)
    assert not any(item.action_type.endswith("_delete") and "NetworkWatcherRG" in " ".join(item.argv)
        for item in runner.requests)


def test_profile_final_timer_corruption_keeps_physical_absence_but_marks_timing_unknown(
        repository_root: Path,tmp_path: Path):
    now=datetime(2026,11,20,12,1,40,tzinfo=timezone.utc)
    d,o,start=profile_attempt(repository_root,tmp_path,now)
    registration=AutomationRunner(d,o,now)
    evidence=register_profile_expiry(d,o,start,now,registration)
    runner=ProfileCleanupRunner(d,o,now,corrupt_timer_on_final=True)
    result=destroy(d.model_copy(update={"expiry":type(d.expiry)(value=evidence)}),o,runner,
        clock=lambda:1100.0,utc_now=lambda:now+timedelta(seconds=100))
    assert result.cleanup_state=="succeeded" and not result.launch_blocked
    assert result.timing_status=="unknown" and result.timing_failure is not None
    assert runner.timer_corruptions==1


def test_missing_watcher_baseline_allows_empty_fresh_inventory_to_close_with_unknown_status(
        repository_root: Path,tmp_path: Path):
    now=datetime(2026,11,20,12,1,40,tzinfo=timezone.utc)
    d,o,start=profile_attempt(repository_root,tmp_path,now)
    registration=AutomationRunner(d,o,now)
    evidence=register_profile_expiry(d,o,start,now,registration)
    journal=o.attempt_root/"recovery.json"
    state=json.loads(journal.read_text())
    state.pop("profile_external_baseline")
    journal.write_text(json.dumps(state))
    runner=ProfileCleanupRunner(d,o,now)
    result=destroy(d.model_copy(update={"expiry":type(d.expiry)(value=evidence)}),o,runner,
        clock=lambda:1100.0,utc_now=lambda:now+timedelta(seconds=100))
    assert result.cleanup_state=="succeeded" and not result.launch_blocked
    assert result.timing_status=="unknown" and result.timing_failure is not None
    assert any(r.action_type=="profile_absence_resources" for r in runner.requests)
    assert any(r.action_type=="profile_absence_groups" for r in runner.requests)
    assert not any(item.action_type.endswith("_delete") and "NetworkWatcher" in " ".join(item.argv)
        for item in runner.requests)


def test_profile_cleanup_rejects_attempt_group_listed_after_exists_false(
        repository_root: Path,tmp_path: Path):
    now=datetime(2026,11,20,12,1,40,tzinfo=timezone.utc)
    d,o,start=profile_attempt(repository_root,tmp_path,now)
    registration=AutomationRunner(d,o,now)
    evidence=register_profile_expiry(d,o,start,now,registration)
    runner=ProfileCleanupRunner(d,o,now,residual_attempt_group=True)
    result=destroy(d.model_copy(update={"expiry":type(d.expiry)(value=evidence)}),o,runner,
        clock=lambda:1100.0,utc_now=lambda:now+timedelta(seconds=100))
    assert result.cleanup_state=="failed" and result.launch_blocked
    assert any("attempt resource group" in item.message for item in result.failures)


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
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=17)})
    runner=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,runner,clock=lambda:1.0,utc_now=lambda:now)
    assert expiry.state=="registered" and expiry.action=="deallocate"
    kinds=[x.action_type for x in runner.requests]
    assert kinds.index("expiry_prerequisites") < kinds.index("job_binding_create")
    assert "pair_apply" not in kinds
    assert expiry.provider_metadata.value["effective_rbac_unproved"] is True
    assert expiry.provider_metadata.value["binding_readback"]["resourcegroupname"]==_slug(d)
    journal=json.loads((o.attempt_root/"recovery.json").read_text())
    assert journal["expiry_state"]=="registered"
    assert journal["expiry"]["automation_account_id"] and journal["expiry"]["job_schedule_id"]


@pytest.mark.parametrize("timezone_name", ["UTC", "Etc/UTC"])
def test_registration_accepts_only_supported_utc_schedule_names(repository_root: Path,tmp_path: Path,timezone_name):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=17)})
    runner=AutomationRunner(d,o,now,schedule_timezone=timezone_name)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,runner,clock=lambda:1.0,utc_now=lambda:now)
    assert expiry.state=="registered"


def test_pre_vm_recheck_accepts_etc_utc_schedule_name(repository_root: Path,tmp_path: Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=17)})
    runner=AutomationRunner(d,o,now,schedule_timezone="Etc/UTC")
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,runner,clock=lambda:1.0,utc_now=lambda:now)
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    result=provision(registered,o,runner,clock=lambda:2.0,utc_now=lambda:now+timedelta(seconds=1))
    assert not any("schedule" in failure.message for failure in result.failures)
    assert "pair_apply" in [request.action_type for request in runner.requests]


@pytest.mark.parametrize("case,expected_error", [
    ("expired", "reservation/deadline has expired"),
    ("changed_identity", "registered expiry metadata is not bound to this reservation source"),
    ("missing_stop", "actual registered STOP is absent from the original cleanup timing anchor"),
    ("changed_stop", "actual registered STOP is absent from the original cleanup timing anchor"),
    ("backwards_clock", "reservation chronology or original mode lifetime ceiling is invalid"),
    ("changed_clock", "original monotonic deadline extends beyond the reserved UTC cap"),
    ("changed_clock_identity", "Azure cleanup timing anchor clock identity changed"),
    ("valid", None),
])
def test_v15_original_clock_mapping_preserves_deadlines_and_fails_closed(
        repository_root: Path, tmp_path: Path, monkeypatch, case, expected_error):
    """Exercise V15 UTC/monotonic bounds through Azure hooks and provision."""
    from cloud_network_benchmark.contracts.admission import ExpiryEvidence
    from cloud_network_benchmark.contracts.common import Evidence, StructuredEvidence
    from cloud_network_benchmark.lifecycle_adapters import RemoteStages, azure_hooks

    cap_start_utc = datetime.fromisoformat("2026-11-24T18:49:42.963086+00:00")
    cap_start_mono = 1156605.752490595
    origin_utc = datetime.fromisoformat("2026-11-24T18:49:45.827666+00:00")
    origin_mono = 1156608.617074353
    stop = datetime.fromisoformat("2026-11-24T19:57:00+00:00")
    requested_stop = datetime.fromisoformat("2026-11-24T19:57:42.963086+00:00")
    assert requested_stop.replace(second=0, microsecond=0) == stop
    cap_end_utc = cap_start_utc + timedelta(seconds=4200)
    cap_end_mono = cap_start_mono + 4200

    d = deployment(repository_root, "exp-903-three-provider-smoke.yaml")
    reservation = d.reservation.value.model_copy(update={
        "reservation_id": Evidence(value="42be6b78059261493b9ce80fd058af55"),
        "campaign_id": "20261124T174251Z-exp-903-4f979c09",
        "cap_started_at": Evidence(value=cap_start_utc),
        "expires_at": Evidence(value=cap_end_utc),
        "source_sha256": "364813b5d08c0e16a286e196d9528085ce0e0941eee2c1dba232e91e19b019d4",
        "workflow_profile": "all_cloud_acceptance_v1",
    })
    admission = d.admission.model_copy(update={
        "workflow_profile": "all_cloud_acceptance_v1", "lifetime_cap_seconds": 4200,
        "priority": "P0",
    })
    d = d.model_copy(update={
        "campaign_id": "20261124T174251Z-exp-903-4f979c09",
        "run_id": "20261124T174251Z-exp-903-4f979c09-azure",
        "deployment_id": "20261124T174251Z-exp-903-4f979c09-azure-deployment",
        "admission": admission, "reservation": Evidence(value=reservation),
    })
    value = options(tmp_path, "P0").model_dump(mode="python")
    value.update(
        complete_workflow_seconds=3535, post_registration_workflow_seconds=2635,
        cap_deadline_monotonic=cap_end_mono, cap_started_monotonic=cap_start_mono,
        retry_end_at=cap_end_utc, workflow_profile="all_cloud_acceptance_v1",
        admission_evidence={**value["admission_evidence"],
            "reservation_id": reservation.reservation_id.value,
            "source_sha256": reservation.source_sha256},
        cleanup_timing_path=tmp_path / "private" / "azure-clock.json",
        profile_allowance={
            "registration_seconds": 900, "provision_seconds": 900,
            "readiness_seconds": 600, "stabilization_seconds": 60,
            "measurement_seconds": 100, "measurement_shutdown_seconds": 15,
            "retrieval_seconds": 300, "cleanup_seconds": 660,
            "azure_provider_lead_seconds": 120,
        },
    )
    o = AzureRuntimeOptions.model_validate(value)
    clock_identity = {"value": "9b1cc84e4aaa6905061a6c10c49af2d277d8eb394364da05ec60206638994fd"}
    monkeypatch.setattr(azure_provider, "_clock_identity", lambda: clock_identity["value"])
    azure_provider._initialize_profile_timing(
        d, o, clock=lambda: origin_mono, utc_now=lambda: origin_utc,
        origin_monotonic=origin_mono, origin_utc=origin_utc)
    name = _slug(d)
    target_ids = [f"/subscriptions/{o.subscription_id}/resourceGroups/{name}/providers/Microsoft.Compute/virtualMachines/{name}-{role}"
                  for role in ("a", "b")]
    evidence = ExpiryEvidence(
        schema_version=2, run_id=d.run_id, deployment_id=d.deployment_id,
        provider="azure", action="deallocate", state="registered",
        registration_id=Evidence(value="fake-v15-expiry-registration"),
        registered_at=Evidence(value=cap_start_utc + timedelta(seconds=100)),
        expires_at=Evidence(value=stop + timedelta(minutes=1) if case == "changed_stop" else stop),
        target_selector=";".join(target_ids),
        provider_metadata=StructuredEvidence(value={
            "reservation_id": "foreign-reservation" if case == "changed_identity" else reservation.reservation_id.value,
            "source_sha256": reservation.source_sha256,
        }),
    )

    # The captured V15 mapping leaves 3.758 microseconds before the UTC cap.
    # A controlled 100-microsecond interval between the production samples makes
    # the old monotonic-then-UTC order project the deadline beyond that cap.
    provision_utc = datetime.fromisoformat("2026-11-24T19:13:00+00:00")
    if case == "expired":
        provision_utc = cap_end_utc + timedelta(seconds=1)
    elif case == "backwards_clock":
        provision_utc = cap_start_utc - timedelta(seconds=1)
    provision_elapsed = (provision_utc - origin_utc).total_seconds()
    provision_mono = origin_mono + provision_elapsed
    state = {"provision": False, "mono_calls": 0, "utc_calls": 0}

    def fake_clock():
        if not state["provision"]:
            return origin_mono + 100
        state["mono_calls"] += 1
        return provision_mono + (0.0001 if state["utc_calls"] else 0.0)

    def fake_utc_now():
        if not state["provision"]:
            return cap_start_utc + timedelta(seconds=100)
        state["utc_calls"] += 1
        offset = timedelta(seconds=5) if case == "changed_clock" else timedelta()
        return provision_utc + offset + (timedelta(microseconds=100) if state["mono_calls"] else timedelta())

    def fake_register(*args, **kwargs):
        if case != "missing_stop":
            azure_provider._bind_profile_stop(d, o, stop)
        return evidence

    monkeypatch.setattr(azure_provider, "register_expiry", fake_register)

    def protected(_provider, _evidence):
        state["provision"] = True
        if case == "changed_clock_identity":
            clock_identity["value"] = "f" * 64

    stages = RemoteStages(*([lambda *_: None] * 6))
    hooks = azure_hooks(d, expiry_request(d, o, requested_stop), options=o,
        runner=AutomationRunner(d, o, cap_start_utc), stages=stages,
        clock=fake_clock, utc_now=fake_utc_now, on_protected=protected)
    assert hooks.protect() == evidence

    def stop_before_external_terraform(*args, action, **kwargs):
        assert action == "pair_init"
        runtime_options = args[1]
        assert runtime_options.cap_started_monotonic == cap_start_mono
        assert runtime_options.cap_deadline_monotonic == cap_end_mono
        assert runtime_options.retry_end_at == cap_end_utc
        assert runtime_options.workflow_stage_deadline_monotonic == cap_start_mono + 1800
        assert evidence.expires_at.value == stop
        anchor = json.loads(o.cleanup_timing_path.read_text(encoding="utf-8"))
        assert anchor["provider_stop_at"] == stop.isoformat()
        raise RuntimeError("offline test boundary before external command execution")

    monkeypatch.setattr(azure_provider, "_tf", stop_before_external_terraform)
    if expected_error is not None:
        with pytest.raises(ValueError, match=expected_error):
            hooks.create()
    else:
        result = hooks.create()
        assert any("offline test boundary before external command execution" in failure.message
                   for failure in result.failures)
        assert not any("original monotonic deadline extends beyond" in failure.message
                       for failure in result.failures)


@pytest.mark.parametrize("timezone_name", ["Europe/London", "GMT", "Etc/GMT", "etc/utc"])
def test_registration_rejects_unexpected_schedule_timezone_aliases(repository_root: Path,tmp_path: Path,timezone_name):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=17)})
    runner=AutomationRunner(d,o,now,schedule_timezone=timezone_name)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,runner,clock=lambda:1.0,utc_now=lambda:now)
    assert expiry.state=="failed" and expiry.failure is not None
    assert not any(request.action_type=="pair_apply" for request in runner.requests)


def test_registration_rejects_foreign_assigned_role_definition(repository_root: Path,tmp_path: Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=17)})
    runner=AutomationRunner(d,o,now,wrong_role_definition=True)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,runner,clock=lambda:1.,utc_now=lambda:now)
    assert expiry.state=="failed"
    assert "pair_apply" not in [r.action_type for r in runner.requests]


def test_registration_polls_asynchronous_publication_within_bound(repository_root: Path, tmp_path: Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=17)})
    runner=AutomationRunner(d,o,now,publish_delay=1)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,runner,clock=lambda:1.0,utc_now=lambda:now,sleep=lambda seconds:None)
    assert expiry.state=="registered"
    assert [r.action_type for r in runner.requests].count("runbook_readback")==2

def test_azure_tsv_content_readback_removes_only_cli_record_terminator(repository_root):
    from cloud_network_benchmark.azure_provider import _azure_tsv_content_matches
    script=(repository_root/"terraform/azure/expiry/deallocate.ps1").read_bytes().decode("utf-8")
    import hashlib
    digest=hashlib.sha256(script.encode()).hexdigest()
    # `az rest --output tsv` adds one final LF after the content's own final LF.
    assert _azure_tsv_content_matches(script,digest)  # exact source response
    assert _azure_tsv_content_matches(script+"\n",digest)  # one CLI terminator
    assert not _azure_tsv_content_matches(script+"\n\n",digest)
    assert not _azure_tsv_content_matches(script.replace("Assert-Target", "Assert-Tampered")+"\n",digest)



def test_provision_rechecks_published_content_before_pair_apply(repository_root: Path, tmp_path: Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=17)})
    runner=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,runner,clock=lambda:1.0,utc_now=lambda:now)
    changed=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    runner.script += "# changed after confirmation\n"
    result=provision(changed,o,runner,clock=lambda:2.0,utc_now=lambda:now+timedelta(seconds=1))
    assert result.cleanup_required and result.deployment is None
    assert any("content changed" in f.message for f in result.failures), result.failures
    assert "pair_apply" not in [r.action_type for r in runner.requests]


@pytest.mark.parametrize("evidence", ["schedule", "binding", "permission", "runOn"])
def test_provision_rechecks_schedule_binding_and_role_before_pair_apply(repository_root: Path, tmp_path: Path, evidence):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=17)})
    runner=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,runner,clock=lambda:1.0,utc_now=lambda:now)
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
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=17)})
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


def test_registered_pair_provision_normalizes_both_vms_and_materializes_authenticated_known_hosts(repository_root: Path, tmp_path: Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=17)})
    runner=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,runner,clock=lambda:1.0,utc_now=lambda:now)
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
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=17)})
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
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=17)})
    runner=AutomationRunner(d,o,now,stale_nonce_for="b")
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,runner,clock=lambda:1.,utc_now=lambda:now)
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    result=provision(registered,o,runner,clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    assert result.failures and "nonce" in result.failures[0].message
    assert not (o.attempt_root/"known_hosts").exists()
    command_requests=[r for r in runner.requests if r.action_type=="host_key_identity"]
    assert [r.argv[r.argv.index("--ids")+1] for r in command_requests]==json.loads((o.attempt_root/"recovery.json").read_text())["vm_ids"]
    assert len({r.argv[r.argv.index("--scripts")+1].split("CNB-AZ-HOSTKEY:",1)[1].split("\\n",1)[0] for r in command_requests})==2


@pytest.mark.parametrize("mode", ["consolidated", "legacy"])
def test_run_command_accepts_authenticated_consolidated_and_legacy_success(repository_root: Path, tmp_path: Path, mode):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=17)})
    runner=AutomationRunner(d,o,now); original=runner.run
    def run(request):
        result=original(request)
        if request.action_type!="host_key_identity": return result
        payload=json.loads(result.stdout); status=payload["value"][0]
        if mode=="consolidated":
            status["code"]="ProvisioningState/succeeded"
            status["message"]="Enable succeeded:\n[stdout]\n"+status["message"]+"[stderr]\n"
        payload["value"]=[status]
        return result.model_copy(update={"stdout":json.dumps(payload)})
    runner.run=run
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,runner,clock=lambda:1.,utc_now=lambda:now)
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    result=provision(registered,o,runner,clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    assert result.deployment is not None and not result.failures, result.failures
    assert len((o.attempt_root/"known_hosts").read_text().splitlines())==2


@pytest.mark.parametrize("mutation", ["failure_code", "duplicate_nonce", "nonempty_stderr"])
def test_run_command_rejects_untrusted_consolidated_host_response(repository_root: Path, tmp_path: Path, mutation):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=17)})
    runner=AutomationRunner(d,o,now); original=runner.run
    def run(request):
        result=original(request)
        if request.action_type!="host_key_identity": return result
        payload=json.loads(result.stdout); status=payload["value"][0]
        status["code"]="ProvisioningState/failed" if mutation=="failure_code" else "ProvisioningState/succeeded"
        message=status["message"]
        status["message"]=("Enable succeeded:\n[stdout]\n"+message+message+"[stderr]\n" if mutation=="duplicate_nonce"
            else "Enable succeeded:\n[stdout]\n"+message+"[stderr]\nunexpected")
        return result.model_copy(update={"stdout":json.dumps(payload)})
    runner.run=run
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,runner,clock=lambda:1.,utc_now=lambda:now)
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    result=provision(registered,o,runner,clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    assert result.failures and result.deployment is not None
    assert not (o.attempt_root/"known_hosts").exists()


@pytest.mark.parametrize("architecture,publisher,offer", [("Arm64","Canonical","ubuntu2404"),("x64","Contoso","ubuntu2404"),("x64","Canonical","debian"),(None,"Canonical","ubuntu2404")])
def test_incompatible_image_metadata_rejects_before_pair_apply(repository_root: Path,tmp_path: Path,architecture,publisher,offer):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=17)})
    runner=AutomationRunner(d,o,now,image_architecture=architecture,image_publisher=publisher,image_offer=offer,image_sku=offer)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,runner,clock=lambda:1.,utc_now=lambda:now)
    result=provision(d.model_copy(update={"expiry":type(d.expiry)(value=expiry)}),o,runner,clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    assert result.failures and "image metadata" in result.failures[0].message
    assert "pair_apply" not in [r.action_type for r in runner.requests]


def test_created_vm_image_identity_is_observed_not_copied_from_request(repository_root:Path,tmp_path:Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=17)})
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
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=17),"cap_deadline_monotonic":501001})
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
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=17),"complete_workflow_seconds":600})
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
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=17)})
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
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=17)})
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
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=17)})
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
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=17)})
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
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=17)})
    failed=AutomationRunner(d,o,now,fail_at=fail_at)
    evidence=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,failed,clock=lambda:1.,utc_now=lambda:now)
    assert evidence.state=="failed"
    cleanup_runner=CleanupRunner(d,o,now,account_present=account_present,present_children=children)
    result=destroy(d,o,cleanup_runner,clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    assert result.cleanup_state=="succeeded"
    deleted={r.action_type.removeprefix("expiry_").removesuffix("_delete") for r in cleanup_runner.requests if r.action_type.endswith("_delete") and not r.action_type.endswith("_before_delete")}
    assert deleted==children


def test_cleanup_result_export_redacts_bounded_private_pem_sentinel(repository_root:Path,tmp_path:Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=17)})
    registration=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,registration,clock=lambda:1.,utc_now=lambda:now)
    registered=d.model_copy(update={"expiry":type(d.expiry)(value=expiry)})
    sentinel="-----BEGIN RSA PRIVATE KEY-----cleanup-secret-----END RSA PRIVATE KEY-----"
    result=destroy(registered,o,CleanupRunner(d,o,now,secret_output=sentinel),clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    serialized=result.model_dump_json()
    assert "cleanup-secret" not in serialized and sentinel not in serialized and "[REDACTED]" in serialized


def test_cleanup_rejects_modified_private_terraform_inputs_before_runner_call(repository_root:Path,tmp_path:Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=17)})
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
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=17)})
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
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=17)})
    registration=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,registration,clock=lambda:1.,utc_now=lambda:now)
    runner=CleanupRunner(d,o,now,role_definition_inventory=inventory)
    result=destroy(d.model_copy(update={"expiry":type(d.expiry)(value=expiry)}),o,runner,clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    assert result.cleanup_state=="failed" and result.launch_blocked
    assert "role definition inventory" in result.failures[0].message
    assert (o.attempt_root/"recovery.json").exists()


@pytest.mark.parametrize("inventory", ["[null]", "[{\"id\":\"\"}]", "{\"value\":[]} ",
    "[{\"id\":\"/subscriptions/foreign/providers/Microsoft.Authorization/roleAssignments/x\",\"scope\":\"/subscriptions/foreign\",\"principalId\":\"p\",\"roleDefinitionId\":\"r\"}]"])
def test_malformed_role_assignment_inventory_also_blocks_cleanup(repository_root:Path,tmp_path:Path,inventory):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=17)})
    registration=AutomationRunner(d,o,now)
    expiry=register_expiry(d,expiry_request(d,o,now+timedelta(minutes=16)),o,registration,clock=lambda:1.,utc_now=lambda:now)
    runner=CleanupRunner(d,o,now,role_assignment_inventory=inventory)
    result=destroy(d.model_copy(update={"expiry":type(d.expiry)(value=expiry)}),o,runner,clock=lambda:2.,utc_now=lambda:now+timedelta(seconds=1))
    assert result.cleanup_state=="failed" and result.launch_blocked
    assert "role assignment inventory" in result.failures[0].message
    request=next(q for q in runner.requests if q.action_type=="role_cleanup_inventory")
    assert request.argv == ["az","role","assignment","list","--subscription",
        o.subscription_id,"--scope",runner.group_id,"--output","json"]
    assert (o.attempt_root/"recovery.json").exists()


def test_interrupted_registration_reuses_observed_job_identity(repository_root:Path,tmp_path:Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=17)})
    runner=AutomationRunner(d,o,now,fail_at="runbook_publish")
    request=expiry_request(d,o,now+timedelta(minutes=16))
    first=register_expiry(d,request,o,runner,clock=lambda:1.,utc_now=lambda:now)
    first_id=json.loads((o.attempt_root/"recovery.json").read_text())["expiry"]["job_schedule_id"]
    second=register_expiry(d,request,o,runner,clock=lambda:1.,utc_now=lambda:now)
    second_id=json.loads((o.attempt_root/"recovery.json").read_text())["expiry"]["job_schedule_id"]
    assert first.state==second.state=="failed" and first_id==second_id


def test_repeated_cleanup_recovers_after_child_delete_interruption(repository_root:Path,tmp_path:Path):
    d=deployment(repository_root); now=datetime.now(timezone.utc); o=options(tmp_path,d.admission.priority)
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=17)})
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
    o=AzureRuntimeOptions.model_validate({**o.model_dump(),"retry_end_at":now+timedelta(minutes=17)})
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
