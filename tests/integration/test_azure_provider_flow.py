"""Offline protocol tests for Azure's registered-expiry gate."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

import cloud_network_benchmark.azure_provider as azure_provider
from cloud_network_benchmark.azure_provider import AzureRuntimeOptions, _slug, register_expiry, provision, destroy, build_pair_inputs
from cloud_network_benchmark.contracts.admission import ExpiryRequest
from cloud_network_benchmark.contracts.common import CommandOutcome
from cloud_network_benchmark.contracts.execution import CommandRequest, CommandResult
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
    now=datetime.now(timezone.utc); d=deployment(repository_root); o=options(tmp_path,d.admission.priority)
    account=(f"/subscriptions/{o.subscription_id}/resourceGroups/{_slug(d)}"
        f"/providers/Microsoft.Automation/automationAccounts/{_slug(d)}-auto")
    job_id="00000000-0000-0000-0000-000000000123"
    collection=("schedules" if case=="actual_schedule" else
        "runbooks" if case in {"actual_runbook","nested_runbook"} else "jobSchedules")
    child_name=(_slug(d)+"-once" if collection=="schedules" else
        _slug(d)+"-deallocate" if collection=="runbooks" else job_id)
    child_url=f"{account}/{collection}/{child_name}?api-version={azure_provider.AUTOMATION_API}"
    baseline=watcher_baseline(d,o)
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


def test_final_profile_absence_accepts_watcher_proven_in_precreation_baseline(repository_root,tmp_path):
    from cloud_network_benchmark.azure_provider import _verify_profile_absence
    now=datetime(2026,11,25,10,0,tzinfo=timezone.utc)
    d=deployment(repository_root); o=options(tmp_path,d.admission.priority)
    watcher_id=(f"/subscriptions/{o.subscription_id}/resourceGroups/NetworkWatcherRG"
        "/providers/Microsoft.Network/networkWatchers/NetworkWatcher_germanywestcentral")
    watcher_group=watcher_id.rsplit("/providers/",1)[0]
    resources=[{"id":watcher_id,"type":"Microsoft.Network/networkWatchers"}]
    groups=[{"id":watcher_group,"name":"NetworkWatcherRG"}]
    state={"resource_group":_slug(d),"expiry":{},"profile_external_baseline":watcher_baseline(d,o,resources,groups)}
    class Runner:
        def run(self,request):
            if request.action_type=="profile_absence_group": out="false"
            elif request.action_type=="profile_absence_resources": out=json.dumps({"value":resources})
            elif request.action_type=="profile_absence_groups": out=json.dumps({"value":groups})
            elif request.action_type in {"profile_absence_role_assignments","profile_absence_role_definitions"}:
                out=json.dumps({"value":[]})
            else: raise AssertionError(f"unexpected cleanup inventory request: {request.action_type}")
            return CommandResult(action_id=request.action_id,outcome=CommandOutcome.SUCCEEDED,
                started_at=now,finished_at=now,duration_seconds=0,exit_code=0,stdout=out,stderr="")
    actions=[]
    _verify_profile_absence(d,o,Runner(),tmp_path,lambda:1.0,state,actions)
    assert len(actions)>=5



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


def watcher_baseline(d,o,resources=(),groups=()):
    import hashlib
    from cloud_network_benchmark.azure_provider import _profile_timing_digest, _watcher_baseline
    resource_inventory=sorted(({"id":row["id"].lower(),"type":row["type"].lower()} for row in resources),
        key=lambda row:(row["id"],row["type"]))
    group_inventory=sorted(({"id":row["id"].lower(),"name":row["name"].lower()} for row in groups),
        key=lambda row:(row["id"],row["name"]))
    baseline=_watcher_baseline(list(groups),list(resources))
    baseline.update({"schema_version":2,"subscription_id":o.subscription_id,"run_id":d.run_id,
        "deployment_id":d.deployment_id,"config_sha256":d.config_sha256,
        "profile":azure_provider._profile_name(d),"capture_started_at_utc":"2026-11-20T12:00:00+00:00",
        "capture_started_monotonic":1000.0,"capture_finished_at_utc":"2026-11-20T12:00:01+00:00",
        "capture_finished_monotonic":1001.0,"resource_inventory":resource_inventory,
        "group_inventory":group_inventory,"source_action_ids":["baseline-resources","baseline-groups"],
        "inventory_sha256":hashlib.sha256(json.dumps({"resources":resource_inventory,"groups":group_inventory},
            sort_keys=True,separators=(",",":")).encode()).hexdigest()})
    baseline["sha256"]=_profile_timing_digest(baseline)
    return baseline


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


def test_workflow_profile_captures_complete_bound_watcher_baseline_before_provider_mutation(
        repository_root: Path,tmp_path: Path):
    now=datetime(2026,11,25,10,0,tzinfo=timezone.utc)
    d=deployment(repository_root,"exp-003-placement-optimization.yaml")
    d=d.model_copy(update={"admission":d.admission.model_copy(update={
        "workflow_profile":"all_cloud_acceptance_v1","timing_profile":None})})
    o=options(tmp_path,d.admission.priority).model_copy(update={"workflow_profile":"all_cloud_acceptance_v1",
        "cleanup_timing_path":tmp_path/"cleanup-timing.json"})
    root,journal=azure_provider._workspace(o,d)
    watcher_id=(f"/subscriptions/{o.subscription_id}/resourceGroups/NetworkWatcherRG"
        "/providers/Microsoft.Network/networkWatchers/NetworkWatcher_germanywestcentral")
    resource_id=(f"/subscriptions/{o.subscription_id}/resourceGroups/shared/providers/Microsoft.Network/virtualNetworks/shared")
    class Runner:
        def __init__(self): self.requests=[]
        def run(self,request):
            self.requests.append(request)
            if request.action_type=="profile_watcher_baseline_resources":
                values=[{"id":watcher_id,"type":"Microsoft.Network/networkWatchers"},
                    {"id":resource_id,"type":"Microsoft.Network/virtualNetworks"}]
            else:
                values=[{"id":watcher_id.rsplit("/providers/",1)[0],"name":"NetworkWatcherRG"},
                    {"id":resource_id.rsplit("/providers/",1)[0],"name":"shared"}]
            return CommandResult(action_id=request.action_id,outcome=CommandOutcome.SUCCEEDED,
                started_at=now,finished_at=now,duration_seconds=0,exit_code=0,
                stdout=json.dumps({"value":values}),stderr="")
    runner=Runner()
    azure_provider._capture_profile_watcher_baseline(d,o,runner,root,journal,lambda:1.0,lambda:now)
    state=json.loads(journal.read_text()); baseline=state["profile_external_baseline"]
    assert azure_provider._valid_watcher_baseline(baseline,d,o)
    assert baseline["subscription_id"]==o.subscription_id and baseline["run_id"]==d.run_id
    assert baseline["deployment_id"]==d.deployment_id and baseline["config_sha256"]==d.config_sha256
    assert baseline["resource_inventory"]==[
        {"id":watcher_id.lower(),"type":"microsoft.network/networkwatchers"},
        {"id":resource_id.lower(),"type":"microsoft.network/virtualnetworks"}]
    assert baseline["watchers"]==[watcher_id.lower()]
    assert baseline["capture_finished_monotonic"]>=baseline["capture_started_monotonic"]
    assert len(baseline["source_action_ids"])==2
    assert {request.action_type for request in runner.requests}=={
        "profile_watcher_baseline_resources","profile_watcher_baseline_groups"}
    class NoReplay:
        def run(self,request): raise AssertionError("saved complete baseline must be reused")
    azure_provider._capture_profile_watcher_baseline(d,o,NoReplay(),root,journal,lambda:1.0,lambda:now)


@pytest.mark.parametrize("clock_samples,utc_samples", [
    ((1.0, 1.0), ("2026-11-25T10:00:00+00:00", "2026-11-25T09:59:59+00:00")),
    ((2.0, 1.0), ("2026-11-25T10:00:00+00:00", "2026-11-25T10:00:01+00:00")),
    ((float("nan"), float("nan")), ("2026-11-25T10:00:00+00:00", "2026-11-25T10:00:01+00:00")),
    ((1.0, 1.0), ("2026-11-25T10:00:00", "2026-11-25T10:00:01+00:00")),
])
def test_workflow_profile_invalid_baseline_capture_blocks_before_expiry_mutation(
        repository_root: Path,tmp_path: Path,clock_samples,utc_samples):
    d=deployment(repository_root,"exp-003-placement-optimization.yaml")
    d=d.model_copy(update={"admission":d.admission.model_copy(update={"workflow_profile":"all_cloud_acceptance_v1"})})
    o=options(tmp_path,d.admission.priority).model_copy(update={"workflow_profile":"all_cloud_acceptance_v1",
        "cleanup_timing_path":tmp_path/"cleanup-timing.json"})
    root,journal=azure_provider._workspace(o,d)
    now=datetime(2026,11,25,10,0,tzinfo=timezone.utc)

    class Runner:
        def __init__(self): self.requests=[]
        def run(self,request):
            self.requests.append(request)
            assert request.action_type in {"profile_watcher_baseline_resources", "profile_watcher_baseline_groups"}
            return CommandResult(action_id=request.action_id,outcome=CommandOutcome.SUCCEEDED,
                started_at=now,finished_at=now,duration_seconds=0,exit_code=0,
                stdout=json.dumps({"value":[]}),stderr="")

    runner=Runner()
    clock_calls=0
    def clock_sample():
        nonlocal clock_calls
        clock_calls+=1
        if clock_calls==1: return clock_samples[0]
        # _command samples the clock while bounding each read. Keep those
        # intermediate values finite and let the final capture sample exercise
        # the requested invalid chronology/value.
        if clock_calls<6: return 1.0
        return clock_samples[1]
    utc_values=iter(datetime.fromisoformat(value) for value in utc_samples)
    with pytest.raises(RuntimeError,match="captured pre-creation Network Watcher baseline is invalid"):
        azure_provider._capture_profile_watcher_baseline(d,o,runner,root,journal,
            clock_sample,lambda:next(utc_values))

    state=json.loads(journal.read_text())
    assert "profile_external_baseline" not in state
    expected_queries=0 if datetime.fromisoformat(utc_samples[0]).tzinfo is None else 2
    assert len(state["actions"])==expected_queries
    if expected_queries:
        assert {item["request"]["action_type"] for item in state["actions"]}=={
            "profile_watcher_baseline_resources","profile_watcher_baseline_groups"}
    assert not any("expiry" in item["request"]["action_type"] for item in state["actions"])
    assert len(runner.requests)==expected_queries


def test_workflow_profile_baseline_rejects_foreign_inventory_and_interrupted_recovery(
        repository_root: Path,tmp_path: Path):
    now=datetime(2026,11,25,10,0,tzinfo=timezone.utc)
    d=deployment(repository_root,"exp-003-placement-optimization.yaml")
    d=d.model_copy(update={"admission":d.admission.model_copy(update={"workflow_profile":"all_cloud_acceptance_v1"})})
    o=options(tmp_path,d.admission.priority).model_copy(update={"workflow_profile":"all_cloud_acceptance_v1",
        "cleanup_timing_path":tmp_path/"cleanup-timing.json"})
    root,journal=azure_provider._workspace(o,d)
    class ForeignRunner:
        def __init__(self): self.requests=[]
        def run(self,request):
            self.requests.append(request)
            return CommandResult(action_id=request.action_id,outcome=CommandOutcome.SUCCEEDED,
                started_at=now,finished_at=now,duration_seconds=0,exit_code=0,
                stdout=json.dumps({"value":[{"id":"/subscriptions/foreign/resourceGroups/shared/providers/Microsoft.Network/networkWatchers/shared",
                    "type":"Microsoft.Network/networkWatchers"}]}),stderr="")
    foreign=ForeignRunner()
    with pytest.raises(RuntimeError,match="foreign subscription"):
        azure_provider._capture_profile_watcher_baseline(d,o,foreign,root,journal,lambda:1.0,lambda:now)
    assert not json.loads(journal.read_text()).get("profile_external_baseline")
    class NoReplay:
        def run(self,request): raise AssertionError("interrupted baseline must not be replaced")
    with pytest.raises(RuntimeError,match="interrupted; refusing a late replacement"):
        azure_provider._capture_profile_watcher_baseline(d,o,NoReplay(),root,journal,lambda:1.0,lambda:now)


def test_workflow_profile_never_retrofits_missing_baseline_after_expiry_registration(
        repository_root: Path,tmp_path: Path):
    d=deployment(repository_root,"exp-003-placement-optimization.yaml")
    d=d.model_copy(update={"admission":d.admission.model_copy(update={"workflow_profile":"all_cloud_acceptance_v1"})})
    o=options(tmp_path,d.admission.priority).model_copy(update={"workflow_profile":"all_cloud_acceptance_v1",
        "cleanup_timing_path":tmp_path/"cleanup-timing.json"})
    root,journal=azure_provider._workspace(o,d)
    state=json.loads(journal.read_text()); state["expiry_state"]="registered"
    journal.write_text(json.dumps(state))
    class NoProviderCall:
        def run(self,request): raise AssertionError("late baseline capture must not query after mutation")
    with pytest.raises(RuntimeError,match="after provider mutation may have started"):
        azure_provider._capture_profile_watcher_baseline(d,o,NoProviderCall(),root,journal,lambda:1.0,
            lambda:datetime.now(timezone.utc))


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


@pytest.mark.parametrize("cohort_profile", ["normal_pair", "calibration_self"])
@pytest.mark.parametrize("auxiliary_state", ["created", "never_created"])
@pytest.mark.parametrize("cleanup_late", [False, True])
def test_public_destroy_cleans_authorized_shared_watcher_cohort(
        repository_root: Path, tmp_path: Path, cohort_profile, auxiliary_state, cleanup_late):
    """Exercise the public cleanup lifecycle with provider I/O replaced only at CommandRunner.run."""
    from concurrent.futures import ThreadPoolExecutor
    from urllib.parse import unquote
    from cloud_network_benchmark.validation_profiles import PROFILE_SPECS
    from cloud_network_benchmark.contracts.admission import AdmissionRequirements
    from cloud_network_benchmark.contracts.common import Evidence
    from cloud_network_benchmark.azure_auxiliary_cleanup import AuxiliaryCleanupCohort

    now = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    subscription = "188a5848-76fb-4d57-8f2a-32c09284d486"
    workflow = ("all_cloud_acceptance_v1" if cohort_profile == "normal_pair"
        else "all_cloud_calibration_v1")
    fixture_name = ("exp-903-three-provider-smoke.yaml" if cohort_profile == "normal_pair"
        else "exp-001-calibration.yaml")
    profile_spec = PROFILE_SPECS[workflow]
    profile_allowance = profile_spec["allowance"]
    allowance = {key: float(profile_allowance[key]) for key in (
        "registration_seconds", "provision_seconds", "readiness_seconds", "stabilization_seconds",
        "measurement_seconds", "measurement_shutdown_seconds", "retrieval_seconds", "cleanup_seconds")}
    allowance["azure_provider_lead_seconds"] = float(profile_allowance["provider_leads_seconds"]["azure"])
    complete_seconds = sum(allowance.values()) - allowance["azure_provider_lead_seconds"]
    post_registration_seconds = complete_seconds - allowance["registration_seconds"]
    cap_seconds = int(profile_spec["cap_seconds"])
    cap_start = now - timedelta(seconds=120)
    cap_end = cap_start + timedelta(seconds=cap_seconds)
    trigger = cap_start + timedelta(seconds=cap_seconds - 600)
    owners = []
    attempts = []
    count = 2 if cohort_profile == "normal_pair" else 1
    for index in range(count):
        attempt_root = tmp_path / f"parent-{index}"
        d = deployment(repository_root, fixture_name)
        start = cap_start
        entry_id = ("V-S1" if index == 0 else "V-S3") if count == 2 else "CAL-S1"
        if index == 1:
            d = d.model_copy(update={"campaign_id": "peer-campaign", "run_id": "peer-campaign-azure",
                "deployment_id": "peer-campaign-azure-deployment"})
        admission_fields = AdmissionRequirements.expected_for(d.mode, d.scenario, None, workflow)
        admission = d.admission.model_copy(update=admission_fields)
        reservation = d.reservation.value.model_copy(update={"campaign_id": d.campaign_id,
            "reservation_id": Evidence(value=f"public-{cohort_profile}-reservation-{index}"),
            "timing_profile": None, "workflow_profile": workflow,
            "cap_started_at": Evidence(value=cap_start), "expires_at": Evidence(value=cap_end)})
        d = d.model_copy(update={"admission": admission, "reservation": Evidence(value=reservation)})
        d = type(d).model_validate(d.model_dump(mode="python"))
        value = options(attempt_root, d.admission.priority).model_dump(mode="python")
        value.update(subscription_id=subscription, complete_workflow_seconds=complete_seconds,
            post_registration_workflow_seconds=post_registration_seconds,
            cap_deadline_monotonic=880.0 + cap_seconds, retry_end_at=cap_end,
            cap_started_monotonic=880.0,
            workflow_profile=workflow, timing_profile=None,
            admission_evidence={**value["admission_evidence"],
                "reservation_id": reservation.reservation_id.value,
                "source_sha256": reservation.source_sha256},
            cleanup_timing_path=attempt_root / "private" / "azure-cleanup-timing.json",
            profile_allowance=allowance, command_timeout_seconds=60)
        o = AzureRuntimeOptions.model_validate(value)
        parent = f"/subscriptions/{subscription}/resourceGroups/{_slug(d)}"
        owners.append({"entry_id": entry_id, "run_id": d.run_id, "campaign_id": d.campaign_id,
            "deployment_id": d.deployment_id, "config_sha256": d.config_sha256,
            "resource_group_id": parent,
            "vm_ids": [f"{parent}/providers/Microsoft.Compute/virtualMachines/{_slug(d)}-{role}"
                for role in ("a", "b")],
            "nic_ids": [f"{parent}/providers/Microsoft.Network/networkInterfaces/{_slug(d)}-{role}-nic"
                for role in ("a", "b")]})
        attempts.append((d, o, start, entry_id))

    group_id = f"/subscriptions/{subscription}/resourceGroups/NetworkWatcherRG"
    watcher_id = group_id + "/providers/Microsoft.Network/networkWatchers/NetworkWatcher_swedencentral"
    spec = _provider_cleanup_spec(tmp_path, owners, subscription, group_id, watcher_id,
        f"public-{cohort_profile}", profile=cohort_profile)
    ready = []
    for (d, o, start, _entry), owner in zip(attempts, spec["owners"]):
        d = d.model_copy(update={"config_sha256": owner["config_sha256"]})
        o = o.model_copy(update={"azure_auxiliary_cleanup": spec})
        d = type(d).model_validate(d.model_dump(mode="python"))
        registration = AutomationRunner(d, o, now)
        origin_monotonic = 1000.0 - (now - start).total_seconds()
        azure_provider._initialize_profile_timing(d, o, clock=lambda: origin_monotonic,
            utc_now=lambda: start, origin_monotonic=origin_monotonic, origin_utc=start)
        expiry = register_expiry(d, expiry_request(d, o, trigger), o, registration,
            clock=lambda: 1000.0, utc_now=lambda: now)
        assert expiry.state == "registered", expiry.failure
        journal = o.attempt_root / "recovery.json"
        assert journal.is_file()
        registered_state = json.loads(journal.read_text(encoding="utf-8"))
        assert registered_state.get("profile_external_baseline") is not None
        d = d.model_copy(update={"expiry": Evidence(value=expiry)})
        ready.append((d, o))

    from cloud_network_benchmark.lifecycle_cleanup import save_cleanup_settings
    from cloud_network_benchmark.orchestration import SavedLifecycleRecovery, SavedProviderRecovery
    for (d, o), owner in zip(ready, spec["owners"]):
        private_entry = tmp_path / owner["entry_id"]
        saved = save_cleanup_settings(provider="azure", deployment=d,
            request=expiry_request(d, o, trigger), reservation=d.reservation.value,
            provider_lead_seconds=120.0, settings=o, private_root=private_entry)
        recovery = SavedLifecycleRecovery(schema_version=1, attempt_id=d.run_id,
            controller_session_id="fixture-cleanup-session",
            reservation_id=d.reservation.value.reservation_id.value,
            providers=[SavedProviderRecovery(provider="azure", deployment_id=d.deployment_id,
                journal_reference=str(o.attempt_root / "recovery.json"), expiry_registered=True,
                creation_may_have_started=True, cleanup_open=True,
                cleanup_settings_reference=str(saved))])
        recovery_path = private_entry / "lifecycle-recovery.json"
        recovery_path.parent.mkdir(parents=True, exist_ok=True)
        recovery_path.write_text(recovery.model_dump_json(indent=2) + "\n", encoding="utf-8")
        recovery_path.chmod(0o600)

    event_owner = spec["owners"][0]
    fixture = json.loads((Path(__file__).parents[1] /
        "fixtures/azure/shared-watcher-activity-retained-schema.json").read_text(encoding="utf-8"))

    class PublicCleanupRunner(ProfileCleanupRunner):
        def __init__(self, *args, timeout_expiry_once=False, timeout_clock=None,
                timeout_at=None, **kwargs):
            super().__init__(*args, role_pages=True, **kwargs)
            self.watcher_deleted = False
            self.group_deleted = False
            self.timeout_expiry_once = timeout_expiry_once
            self.timeout_clock = timeout_clock
            self.timeout_at = timeout_at

        def _result(self, request, *, outcome=CommandOutcome.SUCCEEDED, stdout="", stderr=""):
            return CommandResult(action_id=request.action_id, outcome=outcome, started_at=now,
                finished_at=now, duration_seconds=0,
                exit_code=(0 if outcome == CommandOutcome.SUCCEEDED else
                    None if outcome == CommandOutcome.TIMED_OUT else 1),
                stdout=stdout, stderr=stderr)

        def run(self, request):
            if not request.action_type.startswith("azure_auxiliary_"):
                if request.action_type == "expiry_destroy" and self.timeout_expiry_once:
                    self.timeout_expiry_once = False
                    self.timeout_clock["mono"] = self.timeout_at
                    self.requests.append(request)
                    return self._result(request, outcome=CommandOutcome.TIMED_OUT,
                        stderr="bounded offline timeout")
                return super().run(request)
            self.requests.append(request)
            action = request.action_type
            if action.startswith("azure_auxiliary_activity_"):
                url = request.argv[request.argv.index("--url") + 1]
                decoded = unquote(url)
                match = re.search(r"resourceUri eq '([^']+)'", decoded)
                target = match.group(1) if match else ""
                event = None
                if target.lower() == watcher_id.lower():
                    event = {"id": f"/subscriptions/{subscription}/providers/Microsoft.Insights/eventtypes/management/values/watcher-create",
                        "resourceId": watcher_id,
                        "caller": "synthetic-watcher-caller", "correlationId": "public-cohort-correlation",
                        "eventTimestamp": (now + timedelta(seconds=1)).isoformat(),
                        "status": {"value": "Succeeded"}, "subStatus": {"value": "Created",
                            "localizedValue": "Created (HTTP Status Code: 201)"},
                        "operationName": {"value": "Microsoft.Network/networkWatchers/write"},
                        "properties": {"statusCode": "Created"}}
                else:
                    for resource_id in event_owner["vm_ids"] + event_owner["nic_ids"]:
                        if target.lower() == resource_id.lower():
                            operation = ("Microsoft.Compute/virtualMachines/write" if "/virtualmachines/" in
                                resource_id.lower() else "Microsoft.Network/networkInterfaces/write")
                            event = {"id": f"/subscriptions/{subscription}/providers/Microsoft.Insights/eventtypes/management/values/owner-write-" +
                                hashlib.sha256(resource_id.encode()).hexdigest()[:8], "resourceId": resource_id,
                                "caller": "synthetic-owner-caller", "correlationId": "public-cohort-correlation",
                                "eventTimestamp": (now + timedelta(seconds=1)).isoformat(),
                                "status": {"value": "Succeeded"}, "subStatus": {"value": "OK"},
                                "operationName": {"value": operation}, "properties": {}}
                            break
                return self._result(request, stdout=json.dumps({"value": [event] if event else []}))
            if action in {"azure_auxiliary_inventory_resources", "azure_auxiliary_preleaf_resources"}:
                rows = ([{"id": watcher_id, "type": "Microsoft.Network/networkWatchers"}]
                    if auxiliary_state == "created" and not self.watcher_deleted else [])
            elif action in {"azure_auxiliary_inventory_groups", "azure_auxiliary_preleaf_groups",
                    "azure_auxiliary_group_final_groups"}:
                rows = ([{"id": group_id, "name": "NetworkWatcherRG"}]
                    if auxiliary_state == "created" and not self.group_deleted else [])
            elif action in {"azure_auxiliary_group_inventory", "azure_auxiliary_preleaf_group_children"}:
                rows = ([{"id": watcher_id, "type": "Microsoft.Network/networkWatchers"}]
                    if auxiliary_state == "created" and not self.watcher_deleted else [])
            elif action == "azure_auxiliary_watcher_etag":
                return self._result(request, stdout=json.dumps({"id": watcher_id, "etag": 'W/"fresh"'}))
            elif action == "azure_auxiliary_watcher_delete":
                assert any(f"If-Match=W/\"fresh\"" in arg for arg in request.argv)
                self.watcher_deleted = True
                return self._result(request)
            elif action == "azure_auxiliary_watcher_absence":
                error = {"error": {"code": "ResourceNotFound", "target": watcher_id}}
                return self._result(request, outcome=CommandOutcome.FAILED,
                    stderr="ERROR: Not Found(" + json.dumps(error) + ")")
            elif action == "azure_auxiliary_group_exact_readback":
                return self._result(request, stdout=json.dumps(fixture["group_get"]["body"]))
            elif action == "azure_auxiliary_group_delete":
                assert not any("If-Match=" in arg for arg in request.argv)
                self.group_deleted = True
                return self._result(request)
            elif action == "azure_auxiliary_group_absence":
                return self._result(request, stdout="false")
            else:
                # Watcher child collections and post-leaf group inventories must be empty.
                rows = []
            return self._result(request, stdout=json.dumps({"value": rows}))

    routine_elapsed = cap_seconds - 660.0
    original_cap_mono = 880.0 + cap_seconds
    late_mono = original_cap_mono - 580.0
    late_utc = start + timedelta(seconds=cap_seconds - 580.0)
    failed_clocks = [{"mono": 1100.0} for _ in ready]
    initial_runners = [PublicCleanupRunner(d, o, now, timeout_expiry_once=cleanup_late,
        timeout_clock=failed_clocks[i], timeout_at=880.0 + routine_elapsed + 1.0)
        for i, (d, o) in enumerate(ready)]
    if cleanup_late:
        for i, (d, o) in enumerate(ready):
            failed = destroy(d, o, initial_runners[i], clock=lambda i=i: failed_clocks[i]["mono"],
                utc_now=lambda i=i: start + timedelta(seconds=failed_clocks[i]["mono"] - 880.0))
            assert failed.cleanup_state == "failed" and failed.timing_status == "late"
            assert any(action.action_type == "expiry_destroy" for action in initial_runners[i].requests)
            assert not any(action.action_type == "azure_auxiliary_watcher_delete"
                for action in initial_runners[i].requests)
    runners = [PublicCleanupRunner(d, o, now) for d, o in ready]
    cleanup_utc = late_utc if cleanup_late else now + timedelta(seconds=100)
    cleanup_mono = late_mono if cleanup_late else 1100.0
    def invoke(index):
        d, o = ready[index]
        return destroy(d, o, runners[index], clock=lambda: cleanup_mono,
            utc_now=lambda: cleanup_utc)
    if count == 2:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(invoke, range(count)))
    else:
        results = [invoke(0)]

    for result, runner in zip(results, runners):
        assert result.cleanup_state == "succeeded" and not result.launch_blocked, result.failures
        assert result.timing_status == ("late" if cleanup_late else "within_deadline")
        assert (result.timing_failure is not None) == cleanup_late
        assert any(request.action_type == "profile_absence_resources" for request in runner.requests)
        assert any(request.action_type == "profile_absence_groups" for request in runner.requests)
        assert any(request.action_type == "azure_auxiliary_inventory_resources" for request in runner.requests)
    cohort = AuxiliaryCleanupCohort(spec)
    state = cohort.snapshot()
    if auxiliary_state == "never_created":
        assert state["claim"] is None and state["outcomes"] == [] and state["owners"] == {}
        assert not any(request.action_type in {"azure_auxiliary_watcher_delete", "azure_auxiliary_group_delete"}
            for runner in runners for request in runner.requests)
    else:
        assert state["claim"]["state"] == "complete"
        assert sum(sum(request.action_type == "azure_auxiliary_watcher_delete" for request in runner.requests)
            for runner in runners) == 1
        assert sum(sum(request.action_type == "azure_auxiliary_group_delete" for request in runner.requests)
            for runner in runners) == 1
    all_actions = [request.action_type for runner in runners for request in runner.requests]
    if auxiliary_state == "created":
        for required in ("azure_auxiliary_preleaf_group_children", "azure_auxiliary_watcher_absence",
                "azure_auxiliary_group_final_inventory", "azure_auxiliary_group_final_subscription_inventory",
                "azure_auxiliary_group_final_groups", "azure_auxiliary_group_immediate_children",
                "azure_auxiliary_group_exact_readback", "azure_auxiliary_group_absence"):
            assert required in all_actions
        assert sum(runner.watcher_deleted for runner in runners) == 1
        assert sum(runner.group_deleted for runner in runners) == 1
    else:
        assert not any(action.startswith("azure_auxiliary_") and action not in {
            "azure_auxiliary_inventory_resources", "azure_auxiliary_inventory_groups"} for action in all_actions)
        assert not any(runner.watcher_deleted or runner.group_deleted for runner in runners)
    for (_d, o) in ready:
        timer = json.loads(o.cleanup_timing_path.read_text(encoding="utf-8"))
        assert timer["absence_verified_at"] is not None


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


def _provider_cleanup_spec(tmp_path, owners, subscription, group_id, watcher_id, cohort_id,
        profile="normal_pair"):
    from cloud_network_benchmark.azure_auxiliary_cleanup import canonical
    owners = [dict(owner) for owner in owners]
    members = []
    for owner in owners:
        stem = owner["entry_id"]
        config = tmp_path / f"{cohort_id}-{stem}.yaml"; config.write_text(f"config: {stem}\n")
        source = tmp_path / f"{cohort_id}-{stem}-source.yaml"; source.write_text(f"source: {stem}\n")
        runtime = tmp_path / f"{cohort_id}-{stem}-runtime.json"; runtime.write_text(json.dumps({"entry_id": stem}) + "\n")
        parent = tmp_path / f"{cohort_id}-{stem}-parent.json"; parent.write_text(json.dumps({"run_id": owner["run_id"]}) + "\n")
        owner["config_sha256"] = hashlib.sha256(config.read_bytes()).hexdigest()
        members.append({"entry_id": owner["entry_id"], "run_id": owner["run_id"],
            "config_sha256": owner["config_sha256"], "runtime_path": str(runtime),
            "runtime_sha256": hashlib.sha256(runtime.read_bytes()).hexdigest(),
            "parent_manifest_path": str(parent), "parent_manifest_sha256": hashlib.sha256(parent.read_bytes()).hexdigest(),
            "config_path": str(config), "source_config_path": str(source),
            "source_config_sha256": hashlib.sha256(source.read_bytes()).hexdigest()})
    manifest_path = tmp_path / f"{cohort_id}-manifest.json"
    manifest = {"schema_version": 1, "cohort_id": cohort_id, "profile": profile,
        "subscription_id": subscription, "region": "swedencentral", "resource_group_id": group_id,
        "watcher_id": watcher_id, "owners": owners, "members": members}
    manifest_path.write_bytes(canonical(manifest) + b"\n")
    manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    decision = tmp_path / f"{cohort_id}-decision.md"; decision.write_text("Synthetic offline approval only.\n")
    card = {"schema_version": 1, "approval_status": "approved",
        "action": "delete_exact_auto_created_network_watcher_and_empty_parent", "profile": profile,
        "cohort_id": cohort_id, "subscription_id": subscription, "region": "swedencentral",
        "resource_group_id": group_id, "watcher_id": watcher_id,
        "cohort_manifest_path": str(manifest_path), "cohort_manifest_sha256": manifest_hash,
        "owners": owners, "maximum_leaf_deletes": 1, "maximum_group_deletes": 1,
        "source_path": str(manifest_path), "source_sha256": manifest_hash,
        "decision_source_path": str(decision), "decision_source_sha256": hashlib.sha256(decision.read_bytes()).hexdigest(),
        "approved_by": "synthetic-offline-test", "approved_at_utc": "2026-11-25T12:00:00Z",
        "not_before_utc": "2026-11-25T00:00:00Z", "not_after_utc": "2026-11-26T00:00:00Z",
        "expected_watcher_create_caller": "synthetic-watcher-caller",
        "expected_owned_write_caller": "synthetic-owner-caller"}
    card_path = tmp_path / f"{cohort_id}-approval.json"; card_path.write_text(json.dumps(card))
    spec = {"schema_version": 1, "cohort_id": cohort_id,
        "state_root": str(tmp_path / f"{cohort_id}-cohort-state"), "subscription_id": subscription,
        "region": "swedencentral", "resource_group_id": group_id, "watcher_id": watcher_id,
        "profile": profile, "owners": owners,
        "authorization": {"card_path": str(card_path),
            "card_sha256": hashlib.sha256(card_path.read_bytes()).hexdigest(),
            "cohort_manifest_path": str(manifest_path), "cohort_manifest_sha256": manifest_hash}}
    return spec


def _provider_cleanup_baseline(deployment, subscription, *, profile="all_cloud_acceptance_v1"):
    baseline = {"schema_version": 2, "subscription_id": subscription,
        "run_id": deployment.run_id, "deployment_id": deployment.deployment_id,
        "config_sha256": deployment.config_sha256, "profile": profile,
        "capture_started_at_utc": "2026-11-25T11:59:00+00:00",
        "capture_started_monotonic": 900.0, "capture_finished_at_utc": "2026-11-25T11:59:01+00:00",
        "capture_finished_monotonic": 901.0,
        "groups": [], "watchers": [], "source_action_ids": ["baseline-resources", "baseline-groups"],
        "resource_inventory": [], "group_inventory": [],
        "inventory_sha256": hashlib.sha256(b'{"groups":[],"resources":[]}').hexdigest()}
    baseline["sha256"] = hashlib.sha256(json.dumps(baseline, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return baseline


def test_authorized_shared_watcher_cleanup_deletes_one_leaf_then_one_empty_group(
        repository_root: Path, tmp_path: Path, monkeypatch):
    from cloud_network_benchmark.azure_auxiliary_cleanup import AuxiliaryCleanupCohort, canonical

    now = datetime(2026, 11, 25, 12, 1, tzinfo=timezone.utc)
    d, options, _ = profile_attempt(repository_root, tmp_path, now)
    subscription = "188a5848-76fb-4d57-8f2a-32c09284d486"
    options = options.model_copy(update={"subscription_id": subscription})
    group_id = f"/subscriptions/{subscription}/resourceGroups/NetworkWatcherRG"
    watcher_id = group_id + "/providers/Microsoft.Network/networkWatchers/NetworkWatcher_swedencentral"
    owner_group = f"/subscriptions/{subscription}/resourceGroups/{_slug(d)}"
    owner = {"entry_id": "V-S1", "run_id": d.run_id, "deployment_id": d.deployment_id,
        "campaign_id": d.campaign_id, "config_sha256": d.config_sha256, "resource_group_id": owner_group,
        "vm_ids": [f"{owner_group}/providers/Microsoft.Compute/virtualMachines/{_slug(d)}-{r}" for r in ("a", "b")],
        "nic_ids": [f"{owner_group}/providers/Microsoft.Network/networkInterfaces/{_slug(d)}-{r}-nic" for r in ("a", "b")]}
    peer_campaign, peer_deployment = "peer-campaign", "peer-campaign-azure-deployment"
    peer_run = "peer-campaign-azure"
    peer_slug = f"cnb-{peer_campaign}-{peer_deployment}"
    peer_suffix = hashlib.sha256(f"{peer_campaign}\0{peer_deployment}".encode()).hexdigest()[:8]
    peer_slug = peer_slug[:36].rstrip("-") + "-" + peer_suffix
    peer_group = f"/subscriptions/{subscription}/resourceGroups/{peer_slug}"
    peer = {"entry_id": "V-S3", "run_id": peer_run, "campaign_id": peer_campaign,
        "deployment_id": peer_deployment,
        "config_sha256": hashlib.sha256(b"peer config").hexdigest(),
        "resource_group_id": peer_group,
        "vm_ids": [f"{peer_group}/providers/Microsoft.Compute/virtualMachines/{peer_slug}-{r}" for r in ("a", "b")],
        "nic_ids": [f"{peer_group}/providers/Microsoft.Network/networkInterfaces/{peer_slug}-{r}-nic" for r in ("a", "b")]}
    spec = _provider_cleanup_spec(tmp_path, [owner, peer], subscription, group_id, watcher_id, "provider-path")
    owner, peer = spec["owners"]
    d = d.model_copy(update={"config_sha256": owner["config_sha256"]})
    options = options.model_copy(update={"azure_auxiliary_cleanup": spec})
    baseline = _provider_cleanup_baseline(d, subscription)
    monkeypatch.setattr(azure_provider, "_valid_watcher_baseline", lambda *args: True)
    monkeypatch.setattr(azure_provider, "_require_profile_timing_anchor",
        lambda *args, **kwargs: {"routine_deadline_monotonic": 1500.0})
    event_source = {"events": [{"id": "event-source"}], "correlation_id": "corr-owned"}
    monkeypatch.setattr(azure_provider, "_azure_watcher_activity",
        lambda *args, **kwargs: (event_source, []))
    cohort = AuxiliaryCleanupCohort(spec)
    cohort.record_precreation_baseline(owner, baseline)
    cohort.register_absent(peer, baseline_sha256=baseline["sha256"],
        activity_sha256=hashlib.sha256(canonical(event_source)).hexdigest(), deadline_monotonic=1500.0)

    class Runner:
        def __init__(self):
            self.calls = []
            self.watcher_deleted = False
            self.group_deleted = False
        def run(self, request):
            self.calls.append(request)
            action = request.action_type
            def result(outcome=CommandOutcome.SUCCEEDED, stdout="", stderr=""):
                return CommandResult(action_id=request.action_id, outcome=outcome,
                    started_at=now, finished_at=now, duration_seconds=0.0,
                    exit_code=0 if outcome == CommandOutcome.SUCCEEDED else 1,
                    stdout=stdout, stderr=stderr)
            if action == "azure_auxiliary_watcher_etag":
                output = json.dumps({"id": watcher_id, "etag": "W/\"fresh\""})
                return result(stdout=output)
            if action == "azure_auxiliary_watcher_delete":
                assert "If-Match=W/\"fresh\"" in request.argv
                self.watcher_deleted = True
                return result()
            if action == "azure_auxiliary_watcher_absence":
                assert self.watcher_deleted
                error = {"error": {"code": "ResourceNotFound", "target": watcher_id}}
                return result(CommandOutcome.FAILED, stderr="ERROR: Not Found(" + json.dumps(error) + ")")
            if action == "azure_auxiliary_group_delete":
                assert self.watcher_deleted
                assert not any("If-Match=" in arg for arg in request.argv)
                self.group_deleted = True
                return result()
            if action == "azure_auxiliary_group_exact_readback":
                retained = json.loads((Path(__file__).parents[1] / "fixtures/azure/shared-watcher-activity-retained-schema.json").read_text())
                return result(stdout=json.dumps(retained["group_get"]["body"]))
            if action == "azure_auxiliary_group_absence":
                return result(stdout="false")
            return result()

    runner = Runner()
    def inventory(_runner, _options, _root, endpoint, action, _clock, _fields):
        if action in {"azure_auxiliary_inventory_resources", "azure_auxiliary_preleaf_resources"}:
            rows = [{"id": watcher_id, "type": "Microsoft.Network/networkWatchers"}]
        elif action in {"azure_auxiliary_inventory_groups", "azure_auxiliary_preleaf_groups"}:
            rows = [{"id": group_id, "name": "NetworkWatcherRG"}]
        elif action in {"azure_auxiliary_group_inventory", "azure_auxiliary_preleaf_group_children"}:
            rows = [] if runner.watcher_deleted else [{"id": watcher_id, "type": "Microsoft.Network/networkWatchers"}]
        elif action.startswith("azure_auxiliary_") and action.endswith("_inventory") and any(
                name in action for name in ("flowLogs", "packetCaptures", "connectionMonitors")):
            rows = []
        elif action == "azure_auxiliary_group_final_inventory":
            rows = []
        else:
            rows = []
        return rows, []
    monkeypatch.setattr(azure_provider, "_read_arm_collection", inventory)
    monkeypatch.setattr(azure_provider, "_safe_cleanup_action", lambda _options, result: {
        "action_id": result.action_id, "outcome": result.outcome, "stdout": result.stdout, "stderr": result.stderr})
    state = {"profile_external_baseline": baseline, "actions": []}
    actions = []
    finish_group = azure_provider._finish_shared_watcher_group
    monkeypatch.setattr(azure_provider, "_finish_shared_watcher_group",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("injected interruption after exact leaf absence")))
    with pytest.raises(RuntimeError, match="injected interruption"):
        azure_provider._cleanup_shared_network_watcher(d, options, runner, tmp_path,
            lambda: 1100.0, lambda: now, state, actions)
    assert runner.watcher_deleted and not runner.group_deleted
    monkeypatch.setattr(azure_provider, "_finish_shared_watcher_group", finish_group)
    azure_provider._cleanup_shared_network_watcher(d, options, runner, tmp_path,
        lambda: 1100.0, lambda: now, state, actions)
    assert runner.watcher_deleted and runner.group_deleted
    assert [r.action_type for r in runner.calls].count("azure_auxiliary_watcher_delete") == 1
    assert [r.action_type for r in runner.calls].count("azure_auxiliary_group_delete") == 1
    assert cohort.snapshot()["claim"]["state"] == "complete"


@pytest.mark.parametrize("profile", ["normal_pair", "calibration_self"])
@pytest.mark.parametrize("auxiliary_state", ["created", "never_created", "watcher_only",
    "group_only", "foreign_child", "foreign_watcher", "unproved_parent", "wrong_type_target",
    "wrong_type_group_resource",
    "stale_baseline_watcher", "stale_baseline_group"])
def test_private_parent_cleanup_hooks_reach_shared_terminal_absence(
        repository_root: Path, tmp_path: Path, monkeypatch, profile, auxiliary_state):
    """Exercise each profile's shared cleanup decision with provider calls faked."""
    from concurrent.futures import ThreadPoolExecutor
    from cloud_network_benchmark.azure_auxiliary_cleanup import AuxiliaryCleanupCohort, owner_key

    now = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    first, options, _ = profile_attempt(repository_root, tmp_path, now)
    subscription = "188a5848-76fb-4d57-8f2a-32c09284d486"
    group_id = f"/subscriptions/{subscription}/resourceGroups/NetworkWatcherRG"
    watcher_id = group_id + "/providers/Microsoft.Network/networkWatchers/NetworkWatcher_swedencentral"
    if profile == "normal_pair":
        second = first.model_copy(update={"campaign_id": "peer-campaign", "run_id": "peer-campaign-azure",
            "deployment_id": "peer-campaign-azure-deployment", "config_sha256": hashlib.sha256(b"peer-config").hexdigest()})
        deployments = [first, second]
        entries = ["V-S1", "V-S3"]
    else:
        deployments = [first]
        entries = ["CAL-S1"]
    owners = []
    for deployment, entry_id in zip(deployments, entries):
        parent = f"/subscriptions/{subscription}/resourceGroups/{_slug(deployment)}"
        owners.append({"entry_id": entry_id, "run_id": deployment.run_id,
            "campaign_id": deployment.campaign_id, "deployment_id": deployment.deployment_id,
            "config_sha256": deployment.config_sha256, "resource_group_id": parent,
            "vm_ids": [f"{parent}/providers/Microsoft.Compute/virtualMachines/{_slug(deployment)}-{r}" for r in ("a", "b")],
            "nic_ids": [f"{parent}/providers/Microsoft.Network/networkInterfaces/{_slug(deployment)}-{r}-nic" for r in ("a", "b")]})
    spec = _provider_cleanup_spec(tmp_path, owners, subscription, group_id, watcher_id,
        f"hook-{profile}", profile=profile)
    deployments = [deployment.model_copy(update={"config_sha256": owner["config_sha256"]})
        for deployment, owner in zip(deployments, spec["owners"])]
    options = options.model_copy(update={"subscription_id": subscription, "azure_auxiliary_cleanup": spec})
    cohort = AuxiliaryCleanupCohort(spec)
    baselines = [_provider_cleanup_baseline(deployment, subscription,
        profile="all_cloud_acceptance_v1" if profile == "normal_pair" else "all_cloud_calibration_v1")
        for deployment in deployments]
    if auxiliary_state in {"stale_baseline_watcher", "stale_baseline_group"}:
        if auxiliary_state == "stale_baseline_watcher":
            baselines[0]["resource_inventory"] = [{"id": watcher_id.lower(),
                "type": "microsoft.network/networkinterfaces"}]
        else:
            baselines[0]["group_inventory"] = [{"id": group_id.lower(), "name": "foreign-name"}]
        inventory = {"resources": baselines[0]["resource_inventory"],
            "groups": baselines[0]["group_inventory"]}
        baselines[0]["inventory_sha256"] = hashlib.sha256(json.dumps(inventory,
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        baselines[0]["sha256"] = hashlib.sha256(json.dumps({key: value for key, value in baselines[0].items()
            if key != "sha256"}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    cohort.record_precreation_baseline(spec["owners"][0], baselines[0])
    monkeypatch.setattr(azure_provider, "_valid_watcher_baseline", lambda *_a, **_k: True)
    monkeypatch.setattr(azure_provider, "_require_profile_timing_anchor", lambda *_a, **_k: {
        "routine_deadline_monotonic": 1500.0})
    activity = {"events": [{"id": "synthetic-owned-event"}], "correlation_id": "synthetic-correlation"}
    monkeypatch.setattr(azure_provider, "_azure_watcher_activity", lambda *_a, **_k: (activity, []))
    deleted = {"watcher": False, "group": False}
    command_actions = []

    def fake_command(_runner, _options, _root, _argv, action, _clock, **_kwargs):
        command_actions.append(action)
        output = ""
        error = ""
        outcome = CommandOutcome.SUCCEEDED
        if action == "azure_auxiliary_watcher_etag":
            output = json.dumps({"id": watcher_id, "etag": 'W/"fresh"'})
        elif action == "azure_auxiliary_watcher_delete":
            deleted["watcher"] = True
        elif action == "azure_auxiliary_watcher_absence":
            outcome = CommandOutcome.FAILED
            error = "ERROR: Not Found(" + json.dumps({"error": {"code": "ResourceNotFound", "target": watcher_id}}) + ")"
        elif action == "azure_auxiliary_group_exact_readback":
            fixture = json.loads((Path(__file__).parents[1] / "fixtures/azure/shared-watcher-activity-retained-schema.json").read_text())
            output = json.dumps(fixture["group_get"]["body"])
        elif action == "azure_auxiliary_group_delete":
            deleted["group"] = True
        elif action == "azure_auxiliary_group_absence":
            output = "false"
        return CommandResult(action_id=f"synthetic-{action}", outcome=outcome,
            started_at=now, finished_at=now, duration_seconds=0.0,
            exit_code=0 if outcome == CommandOutcome.SUCCEEDED else 1,
            stdout=output, stderr=error)

    def inventory(_runner, _options, _root, _endpoint, action, _clock, _fields):
        watcher = {"id": watcher_id, "type": "Microsoft.Network/networkWatchers"}
        group = {"id": group_id, "name": "NetworkWatcherRG"}
        if action in {"azure_auxiliary_inventory_resources", "azure_auxiliary_preleaf_resources"}:
            if auxiliary_state in {"created", "watcher_only"}:
                rows = [watcher]
            elif auxiliary_state == "wrong_type_target":
                rows = [{"id": watcher_id, "type": "Microsoft.Network/networkInterfaces"}]
            elif auxiliary_state == "wrong_type_group_resource":
                rows = [{"id": group_id, "type": "Microsoft.Resources/resourceGroups"}]
            elif auxiliary_state == "foreign_child":
                rows = [{"id": group_id + "/providers/Microsoft.Network/routeTables/foreign",
                    "type": "Microsoft.Network/routeTables"}]
            elif auxiliary_state == "foreign_watcher":
                rows = [{"id": f"/subscriptions/{subscription}/resourceGroups/Foreign/providers/"
                    "Microsoft.Network/networkWatchers/foreign", "type": "Microsoft.Network/networkWatchers"}]
            elif auxiliary_state == "unproved_parent":
                rows = [{"id": spec["owners"][0]["vm_ids"][0],
                    "type": "Microsoft.Compute/virtualMachines"}]
            else:
                rows = []
        elif action in {"azure_auxiliary_inventory_groups", "azure_auxiliary_preleaf_groups"}:
            rows = [group] if auxiliary_state in {"created", "group_only"} else []
        elif action in {"azure_auxiliary_group_inventory", "azure_auxiliary_preleaf_group_children"}:
            rows = [] if deleted["watcher"] or auxiliary_state == "never_created" else [watcher]
        elif action == "azure_auxiliary_group_final_inventory":
            rows = []
        elif action == "azure_auxiliary_group_final_subscription_inventory":
            rows = []
        elif action == "azure_auxiliary_group_final_groups":
            rows = []
        elif action == "azure_auxiliary_group_immediate_children":
            rows = []
        elif action.startswith("azure_auxiliary_") and any(name in action for name in
                ("flowLogs", "packetCaptures", "connectionMonitors")):
            rows = []
        else:
            rows = []
        return rows, []

    monkeypatch.setattr(azure_provider, "_command", fake_command)
    monkeypatch.setattr(azure_provider, "_read_arm_collection", inventory)
    monkeypatch.setattr(azure_provider, "_safe_cleanup_action", lambda _options, result: {
        "action_id": result.action_id, "outcome": result.outcome,
        "stdout": result.stdout, "stderr": result.stderr})
    runner = object()
    def invoke(index):
        return azure_provider._cleanup_shared_network_watcher(deployments[index], options, runner,
            tmp_path / f"parent-{index}", lambda: 1100.0, lambda: now,
            {"profile_external_baseline": baselines[index], "actions": []}, [])
    rejected_states = {"watcher_only", "group_only", "foreign_child", "foreign_watcher",
        "unproved_parent", "wrong_type_target", "wrong_type_group_resource",
        "stale_baseline_watcher", "stale_baseline_group"}
    if auxiliary_state in rejected_states:
        with pytest.raises(RuntimeError):
            invoke(0)
        assert deleted == {"watcher": False, "group": False}
        assert command_actions == []
        return
    if profile == "normal_pair":
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(invoke, range(2)))
    else:
        results = [invoke(0)]
    assert results == [None] * len(deployments)
    if auxiliary_state == "never_created":
        assert deleted == {"watcher": False, "group": False}
        assert command_actions == []
        state = cohort.snapshot()
        assert state["claim"] is None and state["outcomes"] == [] and state["owners"] == {}
        return
    assert deleted == {"watcher": True, "group": True}
    state = cohort.snapshot()
    assert state["claim"]["state"] == "complete"
    assert set(state["owners"]) == {owner_key(owner) for owner in spec["owners"]}
    assert len(state["outcomes"]) == 2


def test_shared_watcher_cleanup_refuses_nonempty_child_collection_before_any_delete(
        repository_root: Path, tmp_path: Path, monkeypatch):
    from cloud_network_benchmark.azure_auxiliary_cleanup import AuxiliaryCleanupCohort, canonical
    now = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    d, options, _ = profile_attempt(repository_root, tmp_path, now)
    subscription = "188a5848-76fb-4d57-8f2a-32c09284d486"
    options = options.model_copy(update={"subscription_id": subscription})
    group_id = f"/subscriptions/{subscription}/resourceGroups/NetworkWatcherRG"
    watcher_id = group_id + "/providers/Microsoft.Network/networkWatchers/NetworkWatcher_swedencentral"
    owner_group = f"/subscriptions/{subscription}/resourceGroups/{_slug(d)}"
    owner = {"entry_id": "V-S1", "run_id": d.run_id, "deployment_id": d.deployment_id,
        "campaign_id": d.campaign_id, "config_sha256": d.config_sha256, "resource_group_id": owner_group,
        "vm_ids": [f"{owner_group}/providers/Microsoft.Compute/virtualMachines/{_slug(d)}-{r}" for r in ("a", "b")],
        "nic_ids": [f"{owner_group}/providers/Microsoft.Network/networkInterfaces/{_slug(d)}-{r}-nic" for r in ("a", "b")]}
    peer_campaign, peer_deployment = "peer-campaign", "peer-campaign-azure-deployment"
    peer_run = "peer-campaign-azure"
    peer_slug = f"cnb-{peer_campaign}-{peer_deployment}"
    peer_suffix = hashlib.sha256(f"{peer_campaign}\0{peer_deployment}".encode()).hexdigest()[:8]
    peer_slug = peer_slug[:36].rstrip("-") + "-" + peer_suffix
    peer_group = f"/subscriptions/{subscription}/resourceGroups/{peer_slug}"
    peer = {"entry_id": "V-S3", "run_id": peer_run, "campaign_id": peer_campaign,
        "deployment_id": peer_deployment,
        "config_sha256": hashlib.sha256(b"peer config").hexdigest(),
        "resource_group_id": peer_group,
        "vm_ids": [f"{peer_group}/providers/Microsoft.Compute/virtualMachines/{peer_slug}-{r}" for r in ("a", "b")],
        "nic_ids": [f"{peer_group}/providers/Microsoft.Network/networkInterfaces/{peer_slug}-{r}-nic" for r in ("a", "b")]}
    spec = _provider_cleanup_spec(tmp_path, [owner, peer], subscription, group_id, watcher_id, "nonempty-child")
    owner, peer = spec["owners"]
    d = d.model_copy(update={"config_sha256": owner["config_sha256"]})
    options = options.model_copy(update={"azure_auxiliary_cleanup": spec})
    baseline = _provider_cleanup_baseline(d, subscription)
    monkeypatch.setattr(azure_provider, "_valid_watcher_baseline", lambda *args: True)
    monkeypatch.setattr(azure_provider, "_require_profile_timing_anchor", lambda *args, **kwargs: {
        "routine_deadline_monotonic": 1500.0})
    event_source = {"events": [{"id": "event-source"}], "correlation_id": "corr-owned"}
    monkeypatch.setattr(azure_provider, "_azure_watcher_activity", lambda *args, **kwargs: (event_source, []))
    cohort = AuxiliaryCleanupCohort(spec)
    cohort.record_precreation_baseline(owner, baseline)
    cohort.register_absent(peer, baseline_sha256=baseline["sha256"],
        activity_sha256=hashlib.sha256(canonical(event_source)).hexdigest(), deadline_monotonic=1500.0)
    monkeypatch.setattr(azure_provider, "_read_arm_collection", lambda _r, _o, _root, _endpoint, action, _clock, _fields:
        ([{"id": watcher_id, "type": "Microsoft.Network/networkWatchers"}] if action in {
            "azure_auxiliary_inventory_resources", "azure_auxiliary_group_inventory"} else
         [{"id": group_id, "name": "NetworkWatcherRG"}] if action == "azure_auxiliary_inventory_groups" else
         [{"id": watcher_id + "/flowLogs/foreign", "name": "foreign"}] if action == "azure_auxiliary_flowLogs_inventory" else [], []))
    class NoDelete:
        def __init__(self): self.calls = []
        def run(self, request):
            self.calls.append(request)
            raise AssertionError("provider delete must not occur")
    runner = NoDelete()
    monkeypatch.setattr(azure_provider, "_safe_cleanup_action", lambda _o, result: {"action_id": result.action_id})
    with pytest.raises(RuntimeError, match="flowLogs collection is nonempty"):
        azure_provider._cleanup_shared_network_watcher(d, options, runner, tmp_path,
            lambda: 1100.0, lambda: now, {"profile_external_baseline": baseline, "actions": []}, [])
    assert not any("delete" in request.action_type for request in runner.calls)


@pytest.mark.parametrize(("collection", "error_code", "message"), [
    ("flowLogs", "ParentResourceNotFound",
        "Failed to perform 'read' on resource(s) of type 'networkWatchers/flowLogs', because the parent resource '{watcher}' could not be found."),
    ("packetCaptures", "ResourceNotFound",
        "The Resource 'Microsoft.Network/networkWatchers/NetworkWatcher_swedencentral' under resource group 'NetworkWatcherRG' was not found. For more details please go to https://aka.ms/ARMResourceNotFoundFix"),
    ("connectionMonitors", "ParentResourceNotFound",
        "Failed to perform 'read' on resource(s) of type 'networkWatchers/connectionMonitors', because the parent resource '{watcher}' could not be found."),
])
def test_group_only_child_inventory_accepts_only_saved_exact_missing_watcher_responses(
        repository_root, tmp_path, collection, error_code, message):
    now = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    _d, o, _ = profile_attempt(repository_root, tmp_path, now)
    subscription = "188a5848-76fb-4d57-8f2a-32c09284d486"
    watcher = (f"/subscriptions/{subscription}/resourceGroups/NetworkWatcherRG/providers/"
        "Microsoft.Network/networkWatchers/NetworkWatcher_swedencentral")
    message = message.format(watcher=watcher)
    stderr = "ERROR: Not Found(" + json.dumps({"error": {"code": error_code,
        "message": message}}, separators=(",", ":")) + ")\n"

    class Runner:
        def __init__(self, error): self.error = error; self.requests = []
        def run(self, request):
            self.requests.append(request)
            return CommandResult(action_id=request.action_id, outcome=CommandOutcome.FAILED,
                started_at=now, finished_at=now, duration_seconds=0.0, exit_code=1,
                stdout="", stderr=self.error)

    lookup_watcher = watcher.lower()
    endpoint = f"{lookup_watcher}/{collection}?api-version=2023-09-01"
    action = f"azure_auxiliary_group_only_{collection}_inventory"
    values, evidence = azure_provider._read_arm_collection(Runner(stderr), o.model_copy(update={
        "subscription_id": subscription}), tmp_path, endpoint, action,
        lambda: 100.0, ("id",), expected_missing_watcher_parent_id=lookup_watcher)
    assert values == [] and len(evidence) == 1

    wrong_parent = (stderr.replace(watcher, watcher + "-foreign") if collection != "packetCaptures"
        else stderr.replace("NetworkWatcher_swedencentral", "NetworkWatcher_foreign"))
    with pytest.raises(RuntimeError):
        azure_provider._read_arm_collection(Runner(wrong_parent), o.model_copy(update={
            "subscription_id": subscription}), tmp_path, endpoint, action,
            lambda: 100.0, ("id",), expected_missing_watcher_parent_id=lookup_watcher)
    forbidden = "ERROR: Not Found(" + json.dumps({"error": {
        "code": "AuthorizationFailed", "message": message}}) + ")\n"
    with pytest.raises(RuntimeError):
        azure_provider._read_arm_collection(Runner(forbidden), o.model_copy(update={
            "subscription_id": subscription}), tmp_path, endpoint, action,
            lambda: 100.0, ("id",), expected_missing_watcher_parent_id=lookup_watcher)


def test_preexisting_watcher_and_parent_group_are_never_deleted(
        repository_root: Path, tmp_path: Path, monkeypatch):
    from cloud_network_benchmark.azure_auxiliary_cleanup import AuxiliaryCleanupCohort
    now = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    d, options, _ = profile_attempt(repository_root, tmp_path, now)
    subscription = "188a5848-76fb-4d57-8f2a-32c09284d486"
    group_id = f"/subscriptions/{subscription}/resourceGroups/NetworkWatcherRG"
    watcher_id = group_id + "/providers/Microsoft.Network/networkWatchers/NetworkWatcher_swedencentral"
    owner_group = f"/subscriptions/{subscription}/resourceGroups/{_slug(d)}"
    owner = {"entry_id": "V-S1", "run_id": d.run_id, "deployment_id": d.deployment_id,
        "campaign_id": d.campaign_id, "config_sha256": d.config_sha256, "resource_group_id": owner_group,
        "vm_ids": [f"{owner_group}/providers/Microsoft.Compute/virtualMachines/{_slug(d)}-{r}" for r in ("a", "b")],
        "nic_ids": [f"{owner_group}/providers/Microsoft.Network/networkInterfaces/{_slug(d)}-{r}-nic" for r in ("a", "b")]}
    peer_campaign, peer_deployment = "peer-campaign", "peer-campaign-azure-deployment"
    peer_slug = f"cnb-{peer_campaign}-{peer_deployment}"
    peer_suffix = hashlib.sha256(f"{peer_campaign}\0{peer_deployment}".encode()).hexdigest()[:8]
    peer_slug = peer_slug[:36].rstrip("-") + "-" + peer_suffix
    peer_group = f"/subscriptions/{subscription}/resourceGroups/{peer_slug}"
    peer = {"entry_id": "V-S3", "run_id": "peer-campaign-azure", "campaign_id": peer_campaign,
        "deployment_id": peer_deployment, "config_sha256": hashlib.sha256(b"peer config").hexdigest(),
        "resource_group_id": peer_group,
        "vm_ids": [f"{peer_group}/providers/Microsoft.Compute/virtualMachines/{peer_slug}-{r}" for r in ("a", "b")],
        "nic_ids": [f"{peer_group}/providers/Microsoft.Network/networkInterfaces/{peer_slug}-{r}-nic" for r in ("a", "b")]}
    spec = _provider_cleanup_spec(tmp_path, [owner, peer], subscription, group_id, watcher_id, "preexisting")
    owner, _peer = spec["owners"]
    d = d.model_copy(update={"config_sha256": owner["config_sha256"]})
    options = options.model_copy(update={"subscription_id": subscription, "azure_auxiliary_cleanup": spec})
    monkeypatch.setattr(azure_provider, "_valid_watcher_baseline", lambda *args: True)
    monkeypatch.setattr(azure_provider, "_require_profile_timing_anchor", lambda *args, **kwargs: {
        "routine_deadline_monotonic": 1500.0})
    class NoProviderCall:
        def run(self, _request):
            raise AssertionError("pre-existing watcher must cause zero provider calls")
    baseline = _provider_cleanup_baseline(d, subscription)
    baseline["groups"] = [group_id.lower()]
    baseline["watchers"] = [watcher_id.lower()]
    baseline["resource_inventory"] = [{"id": watcher_id.lower(), "type": "microsoft.network/networkwatchers"}]
    baseline["group_inventory"] = [{"id": group_id.lower(), "name": "networkwatcherrg"}]
    baseline["inventory_sha256"] = hashlib.sha256(json.dumps({"resources": baseline["resource_inventory"],
        "groups": baseline["group_inventory"]}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    baseline["sha256"] = hashlib.sha256(json.dumps({key: value for key, value in baseline.items() if key != "sha256"},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    cohort = AuxiliaryCleanupCohort(spec)
    cohort.record_precreation_baseline(owner, baseline)
    azure_provider._cleanup_shared_network_watcher(d, options, NoProviderCall(), tmp_path,
        lambda: 1100.0, lambda: now, {"profile_external_baseline": baseline, "actions": []}, [])


def test_shared_watcher_cleanup_inventories_block_foreign_active_vm(repository_root, tmp_path):
    now = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    d, _options, _ = profile_attempt(repository_root, tmp_path, now)
    subscription = "188a5848-76fb-4d57-8f2a-32c09284d486"
    group_id = f"/subscriptions/{subscription}/resourceGroups/NetworkWatcherRG"
    watcher_id = group_id + "/providers/Microsoft.Network/networkWatchers/NetworkWatcher_swedencentral"
    owner_group = f"/subscriptions/{subscription}/resourceGroups/{_slug(d)}"
    owner = {"entry_id": "V-S1", "run_id": d.run_id, "deployment_id": d.deployment_id,
        "campaign_id": d.campaign_id, "config_sha256": d.config_sha256, "resource_group_id": owner_group,
        "vm_ids": [f"{owner_group}/providers/Microsoft.Compute/virtualMachines/{_slug(d)}-{r}" for r in ("a", "b")],
        "nic_ids": [f"{owner_group}/providers/Microsoft.Network/networkInterfaces/{_slug(d)}-{r}-nic" for r in ("a", "b")]}
    peer_campaign, peer_deployment = "peer-campaign", "peer-campaign-azure-deployment"
    peer_slug = f"cnb-{peer_campaign}-{peer_deployment}"
    peer_suffix = hashlib.sha256(f"{peer_campaign}\0{peer_deployment}".encode()).hexdigest()[:8]
    peer_slug = peer_slug[:36].rstrip("-") + "-" + peer_suffix
    peer_group = f"/subscriptions/{subscription}/resourceGroups/{peer_slug}"
    peer = {"entry_id": "V-S3", "run_id": "peer-campaign-azure", "campaign_id": peer_campaign,
        "deployment_id": peer_deployment, "config_sha256": hashlib.sha256(b"peer config").hexdigest(),
        "resource_group_id": peer_group,
        "vm_ids": [f"{peer_group}/providers/Microsoft.Compute/virtualMachines/{peer_slug}-{r}" for r in ("a", "b")],
        "nic_ids": [f"{peer_group}/providers/Microsoft.Network/networkInterfaces/{peer_slug}-{r}-nic" for r in ("a", "b")]}
    spec = _provider_cleanup_spec(tmp_path, [owner, peer], subscription, group_id, watcher_id, "foreign-vm")
    unrelated_vm = f"/subscriptions/{subscription}/resourceGroups/unregistered/providers/Microsoft.Compute/virtualMachines/live"
    with pytest.raises(RuntimeError, match="foreign VM"):
        azure_provider._require_cleanup_cohort_parents_absent(spec, [], [
            {"id": unrelated_vm, "type": "Microsoft.Compute/virtualMachines"}])


def test_cleanup_provider_commands_bind_the_earliest_registered_parent_deadline(repository_root, tmp_path):
    from cloud_network_benchmark.azure_auxiliary_cleanup import AuxiliaryCleanupCohort
    now = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    _deployment, options, _ = profile_attempt(repository_root, tmp_path, now)
    subscription = "188a5848-76fb-4d57-8f2a-32c09284d486"
    group_id = f"/subscriptions/{subscription}/resourceGroups/NetworkWatcherRG"
    watcher_id = group_id + "/providers/Microsoft.Network/networkWatchers/NetworkWatcher_swedencentral"
    peer_campaign, peer_deployment = "peer-campaign", "peer-campaign-azure-deployment"
    peer_slug = f"cnb-{peer_campaign}-{peer_deployment}"
    peer_suffix = hashlib.sha256(f"{peer_campaign}\0{peer_deployment}".encode()).hexdigest()[:8]
    peer_slug = peer_slug[:36].rstrip("-") + "-" + peer_suffix
    peer_group = f"/subscriptions/{subscription}/resourceGroups/{peer_slug}"
    owners = [{"entry_id": "V-S1", "run_id": "local-campaign-azure",
        "deployment_id": "local-campaign-azure-deployment",
        "campaign_id": "local-campaign", "config_sha256": hashlib.sha256(b"local").hexdigest(),
        "resource_group_id": f"/subscriptions/{subscription}/resourceGroups/cnb-local-campaign-local-deployment-00000000",
        "vm_ids": [], "nic_ids": []},
        {"entry_id": "V-S3", "run_id": "peer-campaign-azure", "deployment_id": peer_deployment,
        "campaign_id": peer_campaign, "config_sha256": hashlib.sha256(b"peer").hexdigest(),
        "resource_group_id": peer_group, "vm_ids": [], "nic_ids": []}]
    # Derive resource IDs from the same campaign/deployment slug contract.
    for owner in owners:
        suffix = hashlib.sha256(f"{owner['campaign_id']}\0{owner['deployment_id']}".encode()).hexdigest()[:8]
        stem = "-".join(("cnb", owner["campaign_id"], owner["deployment_id"]))
        import re
        stem = re.sub(r"[^a-z0-9-]", "-", stem.lower()).strip("-")[:36].rstrip("-")
        owner["resource_group_id"] = f"/subscriptions/{subscription}/resourceGroups/{stem}-{suffix}"
        owner["vm_ids"] = [owner["resource_group_id"] + f"/providers/Microsoft.Compute/virtualMachines/{stem}-{suffix}-{r}" for r in ("a", "b")]
        owner["nic_ids"] = [owner["resource_group_id"] + f"/providers/Microsoft.Network/networkInterfaces/{stem}-{suffix}-{r}-nic" for r in ("a", "b")]
    spec = _provider_cleanup_spec(tmp_path, owners, subscription, group_id, watcher_id, "deadline-binding")
    cohort = AuxiliaryCleanupCohort(spec)
    for owner, deadline in zip(spec["owners"], (900.0, 700.0)):
        cohort.register_absent(owner, baseline_sha256=hashlib.sha256(owner["run_id"].encode()).hexdigest(),
            activity_sha256=hashlib.sha256((owner["run_id"] + "activity").encode()).hexdigest(),
            deadline_monotonic=deadline)
    options = options.model_copy(update={"workflow_stage_deadline_monotonic": 950.0})
    bound = azure_provider._bind_azure_cleanup_cohort_deadline(options, cohort)
    assert bound.workflow_stage_deadline_monotonic == 700.0


@pytest.mark.parametrize("corruption", ["wrong_caller", "ambiguous_create", "missing_nic_write", "blank_only",
    "foreign_completion_caller", "wrong_completion_correlation", "before_completion",
    "duplicate_completion", "contradictory_completion"])
def test_watcher_activity_ownership_requires_exact_caller_and_unique_correlated_vm_nic_writes(
        repository_root: Path, tmp_path: Path, monkeypatch, corruption):
    now = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    d, options, _ = profile_attempt(repository_root, tmp_path, now)
    subscription = "188a5848-76fb-4d57-8f2a-32c09284d486"
    watcher_id = f"/subscriptions/{subscription}/resourceGroups/NetworkWatcherRG/providers/Microsoft.Network/networkWatchers/NetworkWatcher_swedencentral"
    owner_group = f"/subscriptions/{subscription}/resourceGroups/{_slug(d)}"
    owner = {"entry_id": "V-S1", "run_id": d.run_id, "campaign_id": d.campaign_id,
        "deployment_id": d.deployment_id, "config_sha256": d.config_sha256,
        "resource_group_id": owner_group,
        "vm_ids": [f"{owner_group}/providers/Microsoft.Compute/virtualMachines/{_slug(d)}-{r}" for r in ("a", "b")],
        "nic_ids": [f"{owner_group}/providers/Microsoft.Network/networkInterfaces/{_slug(d)}-{r}-nic" for r in ("a", "b")]}
    options = options.model_copy(update={"subscription_id": subscription,
        "azure_auxiliary_cleanup": {"owners": [owner]}})
    baseline = {"capture_started_at_utc": "2026-11-25T11:00:00+00:00",
        "capture_finished_at_utc": "2026-11-25T11:00:01+00:00"}
    caller = "00000000-0000-0000-0000-000000000001"
    corr = "exact-owned-correlation"
    def event(resource, operation, event_id, *, event_caller=caller, created=False):
        return {"id": f"/subscriptions/{subscription}/providers/Microsoft.Insights/eventtypes/management/values/{event_id}",
            "resourceId": resource,
            "caller": event_caller, "correlationId": corr,
            "eventTimestamp": "2026-11-25T11:30:00Z", "status": {"value": "Succeeded"},
            "subStatus": {"value": "Created" if created else "",
                "localizedValue": "Created (HTTP Status Code: 201)" if created else ""},
            "operationName": {"value": operation},
            "properties": {"statusCode": "Created"} if created else {}}
    watcher_event = event(watcher_id, "Microsoft.Network/networkWatchers/write", "watcher", created=True,
        event_caller="foreign-caller" if corruption == "wrong_caller" else caller)
    completion = event(watcher_id, "Microsoft.Network/networkWatchers/write", "watcher-completion")
    completion["eventTimestamp"] = "2026-11-25T11:31:00Z"
    if corruption == "foreign_completion_caller": completion["caller"] = "foreign-caller"
    if corruption == "wrong_completion_correlation": completion["correlationId"] = "other-correlation"
    if corruption == "before_completion": completion["eventTimestamp"] = "2026-11-25T11:29:00Z"
    if corruption == "contradictory_completion": completion["subStatus"]["value"] = "Updated"
    vm_event = event(owner["vm_ids"][0], "Microsoft.Compute/virtualMachines/write", "vm")
    nic_event = event(owner["nic_ids"][0], "Microsoft.Network/networkInterfaces/write", "nic")
    target_map = {watcher_id.lower(): [watcher_event, completion],
        owner["vm_ids"][0].lower(): [vm_event], owner["vm_ids"][1].lower(): [],
        owner["nic_ids"][0].lower(): [nic_event], owner["nic_ids"][1].lower(): []}
    if corruption == "ambiguous_create":
        target_map[watcher_id.lower()].append(event(watcher_id,
            "Microsoft.Network/networkWatchers/write", "watcher-two", created=True))
    if corruption == "duplicate_completion": target_map[watcher_id.lower()].append(dict(completion))
    if corruption == "blank_only": target_map[watcher_id.lower()] = [completion]
    if corruption == "missing_nic_write":
        target_map[owner["nic_ids"][0].lower()] = []
    targets = [watcher_id, *sorted(target_map.keys() - {watcher_id.lower()})]
    monkeypatch.setattr(azure_provider, "_read_arm_collection",
        lambda _runner, _options, _root, endpoint, action, _clock, _fields:
            (target_map.get(targets[int(action.rsplit("_", 1)[1]) - 1].lower(), []), []))
    with pytest.raises(RuntimeError):
        azure_provider._azure_watcher_activity(d, options, object(), tmp_path, baseline,
            watcher_id, caller, caller, lambda: 1100.0, lambda: now)


@pytest.mark.parametrize("with_completion", [False, True])
def test_watcher_activity_accepts_one_exact_successful_correlated_create(
        repository_root: Path, tmp_path: Path, monkeypatch, with_completion):
    now = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    d, options, _ = profile_attempt(repository_root, tmp_path, now)
    subscription = "188a5848-76fb-4d57-8f2a-32c09284d486"
    watcher_id = f"/subscriptions/{subscription}/resourceGroups/NetworkWatcherRG/providers/Microsoft.Network/networkWatchers/NetworkWatcher_swedencentral"
    owner_group = f"/subscriptions/{subscription}/resourceGroups/{_slug(d)}"
    owner = {"entry_id": "V-S1", "run_id": d.run_id, "campaign_id": d.campaign_id,
        "deployment_id": d.deployment_id, "config_sha256": d.config_sha256,
        "resource_group_id": owner_group,
        "vm_ids": [f"{owner_group}/providers/Microsoft.Compute/virtualMachines/{_slug(d)}-{r}" for r in ("a", "b")],
        "nic_ids": [f"{owner_group}/providers/Microsoft.Network/networkInterfaces/{_slug(d)}-{r}-nic" for r in ("a", "b")]}
    options = options.model_copy(update={"subscription_id": subscription,
        "azure_auxiliary_cleanup": {"owners": [owner]}})
    baseline = {"capture_started_at_utc": "2026-11-25T11:00:00+00:00",
        "capture_finished_at_utc": "2026-11-25T11:00:01+00:00"}
    caller, corr = "00000000-0000-0000-0000-000000000001", "exact-owned-correlation"
    def event(resource, operation, event_id, created=False):
        return {"id": f"/subscriptions/{subscription}/providers/Microsoft.Insights/eventtypes/management/values/{event_id}",
            "resourceId": resource, "caller": caller, "correlationId": corr,
            "eventTimestamp": "2026-11-25T11:30:00Z", "status": {"value": "Succeeded"},
            "subStatus": {"value": "Created" if created else "",
                "localizedValue": "Created (HTTP Status Code: 201)" if created else ""},
            "operationName": {"value": operation},
            "properties": {"statusCode": "Created"} if created else {}}
    watcher = event(watcher_id, "Microsoft.Network/networkWatchers/write", "watcher", created=True)
    completion = event(watcher_id, "Microsoft.Network/networkWatchers/write", "watcher-completion")
    completion["eventTimestamp"] = "2026-11-25T11:31:00Z"
    vm = event(owner["vm_ids"][0], "Microsoft.Compute/virtualMachines/write", "vm")
    nic = event(owner["nic_ids"][0], "Microsoft.Network/networkInterfaces/write", "nic")
    data = {watcher_id.lower(): [watcher, *([completion] if with_completion else [])], owner["vm_ids"][0].lower(): [vm],
        owner["vm_ids"][1].lower(): [], owner["nic_ids"][0].lower(): [nic],
        owner["nic_ids"][1].lower(): []}
    targets = [watcher_id, *sorted(data.keys() - {watcher_id.lower()})]
    monkeypatch.setattr(azure_provider, "_read_arm_collection",
        lambda _runner, _options, _root, endpoint, action, _clock, _fields:
            (data.get(targets[int(action.rsplit("_", 1)[1]) - 1].lower(), []), []))
    evidence, pages = azure_provider._azure_watcher_activity(d, options, object(), tmp_path,
        baseline, watcher_id, caller, caller, lambda: 1100.0, lambda: now)
    assert evidence["correlation_id"] == corr
    assert len(evidence["events"]) == (4 if with_completion else 3)
    assert pages == []


def test_retained_azure_activity_schema_allows_split_callers_and_nonwrite_null_caller(
        repository_root: Path, tmp_path: Path, monkeypatch):
    fixture = json.loads((Path(__file__).parents[1] / "fixtures/azure/shared-watcher-activity-retained-schema.json").read_text())
    now = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    d, options, _ = profile_attempt(repository_root, tmp_path, now)
    subscription = "188a5848-76fb-4d57-8f2a-32c09284d486"
    watcher_id = (f"/subscriptions/{subscription}/resourceGroups/NetworkWatcherRG/providers/"
        "Microsoft.Network/networkWatchers/NetworkWatcher_swedencentral")
    owner_group = f"/subscriptions/{subscription}/resourceGroups/{_slug(d)}"
    owner = {"entry_id": "V-S1", "run_id": d.run_id, "campaign_id": d.campaign_id,
        "deployment_id": d.deployment_id, "config_sha256": d.config_sha256,
        "resource_group_id": owner_group,
        "vm_ids": [f"{owner_group}/providers/Microsoft.Compute/virtualMachines/{_slug(d)}-{r}" for r in ("a", "b")],
        "nic_ids": [f"{owner_group}/providers/Microsoft.Network/networkInterfaces/{_slug(d)}-{r}-nic" for r in ("a", "b")]}
    options = options.model_copy(update={"subscription_id": subscription,
        "azure_auxiliary_cleanup": {"owners": [owner]}})
    mapping = {}
    for row in fixture["events"]:
        resource = row["resourceId"]
        if "/networkwatchers/" in resource.lower():
            row["resourceId"] = watcher_id
        elif "/virtualmachines/" in resource.lower():
            role = "a" if resource.lower().rsplit("/", 1)[-1].endswith("-a") else "b"
            row["resourceId"] = owner["vm_ids"][0 if role == "a" else 1]
        elif "/networkinterfaces/" in resource.lower():
            role = "a" if resource.lower().rsplit("/", 1)[-1].endswith("-a-nic") else "b"
            row["resourceId"] = owner["nic_ids"][0 if role == "a" else 1]
        mapping.setdefault(row["resourceId"].lower(), []).append(row)
    targets = [watcher_id, *sorted(set(mapping) - {watcher_id.lower()})]
    monkeypatch.setattr(azure_provider, "_read_arm_collection",
        lambda _runner, _options, _root, _endpoint, action, _clock, _fields:
            (mapping.get(targets[int(action.rsplit("_", 1)[1]) - 1].lower(), []), []))
    evidence, _ = azure_provider._azure_watcher_activity(d, options, object(), tmp_path,
        {"capture_started_at_utc": "2026-11-25T10:00:00+00:00",
            "capture_finished_at_utc": "2026-11-25T10:00:01+00:00"}, watcher_id,
        "11111111-1111-1111-1111-111111111111", "owner@example.invalid",
        lambda: 1100.0, lambda: now)
    assert evidence["correlation_id"] == "8ae6c03a-f4f9-f5e6-528b-15d81198e46b"
    assert len(evidence["events"]) == 7
    assert fixture["group_get"]["body"]["id"].lower() == (
        f"/subscriptions/{subscription}/resourcegroups/networkwatcherrg")
    assert "etag" not in fixture["group_get"]["body"]


@pytest.mark.parametrize("body,accepted", [
    ('ERROR: Not Found({"error":{"code":"ResourceNotFound","message":"Resource not found for exact /watcher"}})', True),
    ('ERROR: Not Found({"error":{"code":"ResourceNotFound","target":"/watcher","message":"absent"}})', True),
    ('ERROR: Not Found({"error":{"code":"ResourceNotFound"}})', False),
    ('ERROR: Not Found({"error":{"code":"ResourceNotFound","target":"/watcher","details":[{"target":"/foreign"}]}})', False),
    ('ERROR: Not Found({"error":{"code":"ResourceNotFound","target":"/watcher-foreign"}})', False),
    ('ERROR: Not Found({"error":{"code":"ResourceNotFound","target":"/foreign","target":"/watcher"}})', False),
    ('ERROR: Not Found({"error":{"code":"ResourceNotFound","message":"Resource not found for exact /watcher-foreign"}})', False),
    ('ERROR: Not Found({"error":{"code":"AuthorizationFailed","target":"/watcher"}})', False),
    ('ERROR: Not Found({"error":{"code":"ResourceNotFound","target":"/watcher"}}) extra', False),
])
def test_watcher_absence_requires_complete_exact_resource_404(body, accepted):
    now = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    result = CommandResult(action_id="watcher-404", outcome=CommandOutcome.FAILED,
        started_at=now, finished_at=now, duration_seconds=0.0, exit_code=1,
        stdout="", stderr=body)
    assert azure_provider._is_exact_resource_404(result, "/watcher") is accepted


@pytest.mark.parametrize("message,accepted", [
    ("Resource /subscriptions/188a5848-76fb-4d57-8f2a-32c09284d486/resourceGroups/networkwatcherrg/providers/Microsoft.Network/networkWatchers/networkwatcher_swedencentral not found.", True),
    ("resource /SUBSCRIPTIONS/188A5848-76FB-4D57-8F2A-32C09284D486/RESOURCEGROUPS/NETWORKWATCHERRG/PROVIDERS/MICROSOFT.NETWORK/NETWORKWATCHERS/NETWORKWATCHER_SWEDENCENTRAL NOT FOUND.", True),
    ("Resource /subscriptions/188a5848-76fb-4d57-8f2a-32c09284d486/resourceGroups/networkwatcherrg/providers/Microsoft.Network/networkWatchers/foreign not found.", False),
    ("Resource /subscriptions/188a5848-76fb-4d57-8f2a-32c09284d486/resourceGroups/networkwatcherrg/providers/Microsoft.Network/networkWatchers/networkwatcher_swedencentral not found. extra", False),
])
def test_watcher_absence_accepts_only_exact_arm_full_id_message(message, accepted):
    now = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    resource_id = "/subscriptions/188a5848-76fb-4d57-8f2a-32c09284d486/resourceGroups/NetworkWatcherRG/providers/Microsoft.Network/networkWatchers/NetworkWatcher_swedencentral"
    result = CommandResult(action_id="watcher-404", outcome=CommandOutcome.FAILED,
        started_at=now, finished_at=now, duration_seconds=0.0, exit_code=1,
        stdout="", stderr="ERROR: Not Found(" + json.dumps({"error": {
            "code": "NotFound", "message": message, "details": []}}) + ")")
    assert azure_provider._is_exact_resource_404(result, resource_id) is accepted


@pytest.mark.parametrize("mutation,accepted", [
    ("none", True), ("wrong_etag", False), ("wrong_id", False),
    ("wrong_action_order", False), ("duplicate_delete", False),
    ("newline", True), ("foreign_absence_id", False), ("wrapped_suffix", False),
])
def test_saved_watcher_receipt_replay_requires_exact_adjacent_etag_delete_404(
        tmp_path, mutation, accepted):
    now = datetime(2026, 11, 26, 5, 49, tzinfo=timezone.utc)
    subscription = "00000000-0000-0000-0000-000000000001"
    group_id = f"/subscriptions/{subscription}/resourceGroups/NetworkWatcherRG"
    watcher_id = group_id + "/providers/Microsoft.Network/networkWatchers/NetworkWatcher_swedencentral"
    etag = 'W/\"saved-etag\"'

    def row(action, argv, *, outcome=CommandOutcome.SUCCEEDED, stdout="", stderr=""):
        command = CommandRequest(action_id=f"azure-{action}", action_type=action, argv=argv,
            cwd=str(tmp_path), timeout_seconds=30, environment_classification="none")
        result = CommandResult(action_id=command.action_id, outcome=outcome, started_at=now,
            finished_at=now + timedelta(seconds=1), duration_seconds=1.0,
            exit_code=0 if outcome == CommandOutcome.SUCCEEDED else 1, stdout=stdout, stderr=stderr)
        return {"request": command.durable_view(), "result": result.durable_view()}

    etag_args = ["az", "rest", "--subscription", subscription, "--method", "GET",
        "--url", watcher_id + "?api-version=2023-09-01", "--output", "json"]
    delete_args = ["az", "rest", "--subscription", subscription, "--method", "DELETE",
        "--url", watcher_id + "?api-version=2023-09-01", "--output", "json",
        "--headers", f"If-Match={etag}"]
    if mutation == "wrong_etag":
        delete_args[-1] = "If-Match=W/\"other\""
    elif mutation == "wrong_id":
        etag_args[etag_args.index("--url") + 1] = etag_args[etag_args.index("--url") + 1].replace(
            "NetworkWatcher_swedencentral", "foreign")
    etag_row = row("azure_auxiliary_watcher_etag", etag_args,
        stdout=json.dumps({"id": watcher_id, "etag": etag}))
    delete_row = row("azure_auxiliary_watcher_delete", delete_args)
    absence_error = {"error": {"code": "NotFound",
        "message": f"Resource {watcher_id.lower()} not found.", "details": []}}
    absence_row = row("azure_auxiliary_watcher_absence",
        ["az", "rest", "--subscription", subscription, "--method", "GET",
            "--url", watcher_id + "?api-version=2023-09-01", "--output", "json"],
        outcome=CommandOutcome.FAILED,
        stderr="ERROR: Not Found(" + json.dumps(absence_error) + ")")
    if mutation == "newline":
        absence_row["result"]["stderr"] += "\n"
    elif mutation == "foreign_absence_id":
        absence_row["result"]["stderr"] = absence_row["result"]["stderr"].replace(
            watcher_id.lower(), watcher_id.lower() + "-foreign")
    elif mutation == "wrapped_suffix":
        absence_row["result"]["stderr"] += " extra\n"
    actions = [etag_row, delete_row, absence_row]
    if mutation == "wrong_action_order":
        actions = [delete_row, etag_row, absence_row]
    elif mutation == "duplicate_delete":
        actions = [etag_row, delete_row, absence_row, delete_row]
    state = {"actions": actions}
    o = options(tmp_path)
    spec = {"watcher_id": watcher_id}
    if accepted:
        evidence = azure_provider._saved_watcher_leaf_receipt(state, o, spec)
        assert evidence["watcher_id"] == watcher_id
        assert evidence["delete_action_id"] == delete_row["request"]["action_id"]
    else:
        with pytest.raises(ValueError):
            azure_provider._saved_watcher_leaf_receipt(state, o, spec)


def _group_only_recovery_fixture(repository_root, tmp_path, now):
    from cloud_network_benchmark.azure_auxiliary_cleanup import AuxiliaryCleanupCohort
    d = deployment(repository_root, "exp-903-three-provider-smoke.yaml")
    sub = "188a5848-76fb-4d57-8f2a-32c09284d486"
    group_id = f"/subscriptions/{sub}/resourceGroups/NetworkWatcherRG"
    watcher_id = group_id + "/providers/Microsoft.Network/networkWatchers/NetworkWatcher_swedencentral"
    parent = f"/subscriptions/{sub}/resourceGroups/{_slug(d)}"
    owners = [{"entry_id": "CAL-S1", "run_id": d.run_id, "campaign_id": d.campaign_id,
        "deployment_id": d.deployment_id, "config_sha256": d.config_sha256,
        "resource_group_id": parent,
        "vm_ids": [f"{parent}/providers/Microsoft.Compute/virtualMachines/{_slug(d)}-{r}"
            for r in ("a", "b")],
        "nic_ids": [f"{parent}/providers/Microsoft.Network/networkInterfaces/{_slug(d)}-{r}-nic"
            for r in ("a", "b")]}]
    spec = _provider_cleanup_spec(tmp_path, owners, sub, group_id, watcher_id,
        "group-only-recovery", profile="calibration_self")
    owner = spec["owners"][0]
    d = type(d).model_validate(d.model_copy(update={"config_sha256": owner["config_sha256"]})
        .model_dump(mode="python"))
    o = options(tmp_path / "attempt", d.admission.priority).model_copy(update={
        "subscription_id": sub, "azure_auxiliary_cleanup": spec})
    root = o.attempt_root
    root.mkdir(mode=0o700, parents=True)

    def saved_row(action, argv, *, outcome=CommandOutcome.SUCCEEDED, stdout="", stderr="", seconds=0,
            redactions=None):
        stamp = now - timedelta(seconds=3-seconds)
        command = CommandRequest(action_id=f"azure-saved-{action}", action_type=action, argv=argv,
            cwd=str(root), timeout_seconds=30, environment_classification="none")
        result = CommandResult(action_id=command.action_id, outcome=outcome, started_at=stamp,
            finished_at=stamp + timedelta(seconds=0.5), duration_seconds=0.5,
            exit_code=0 if outcome == CommandOutcome.SUCCEEDED else 1,
            stdout=stdout, stderr=stderr, redactions=redactions or [])
        return {"request": command.durable_view(), "result": result.durable_view()}
    etag = 'W/"saved-etag"'
    prefix = ["az", "rest", "--subscription", sub]
    leaf_rows = [
        saved_row("azure_auxiliary_watcher_etag",
            prefix + ["--method", "GET", "--url", watcher_id + "?api-version=2023-09-01",
                "--output", "json"],
            stdout=json.dumps({"id": watcher_id, "etag": etag}), seconds=0),
        saved_row("azure_auxiliary_watcher_delete",
            prefix + ["--method", "DELETE", "--url", watcher_id + "?api-version=2023-09-01",
                "--output", "json", "--headers", f"If-Match={etag}"], seconds=1),
        saved_row("azure_auxiliary_watcher_absence",
            prefix + ["--method", "GET", "--url", watcher_id + "?api-version=2023-09-01",
                "--output", "json"], outcome=CommandOutcome.FAILED,
            stderr="ERROR: Not Found(" + json.dumps({"error": {"code": "NotFound",
                "message": f"Resource {watcher_id.lower()} not found.", "details": []}}) + ")",
            seconds=2, redactions=["fixture-private-token"]),
    ]
    state = {"run_id": d.run_id, "deployment_id": d.deployment_id,
        "config_sha256": d.config_sha256,
        "vm_ids": owner["vm_ids"], "expiry": {}, "actions": leaf_rows}
    journal = root / "recovery.json"
    journal.write_text(json.dumps(state, indent=2) + "\n")
    journal.chmod(0o600)
    cohort = AuxiliaryCleanupCohort(spec)
    cohort.register_absent(owner, baseline_sha256=hashlib.sha256(b"baseline").hexdigest(),
        activity_sha256=hashlib.sha256(b"owner-activity").hexdigest(), deadline_monotonic=90.0)
    claim_id = cohort.claim_leaf(now_monotonic=40.0,
        bounded_recovery_deadline_monotonic=50.0)
    assert claim_id is not None
    return d, o, spec, cohort, journal, group_id, watcher_id, owner


def _stub_group_only_recovery_cap(monkeypatch, deployment, clock, utc_now):
    monkeypatch.setattr(azure_provider, "_require_profile_timing_anchor",
        lambda *_args, **_kwargs: {"clock_identity": azure_provider._clock_identity(),
            "run_id": deployment.run_id, "deployment_id": deployment.deployment_id,
            "config_sha256": deployment.config_sha256})
    monkeypatch.setattr(azure_provider, "_bounded_late_auxiliary_deadline",
        lambda *_args, **_kwargs: (clock() + 600.0, utc_now() + timedelta(seconds=600)))


def test_group_claimed_continuation_rejects_leaf_outcome_not_bound_to_saved_etag_receipt(
        repository_root, tmp_path, monkeypatch):
    now = datetime(2026, 11, 25, 12, 1, tzinfo=timezone.utc)
    d, o, spec, cohort, _journal, _group_id, watcher_id, _owner = _group_only_recovery_fixture(
        repository_root, tmp_path, now)
    claim = cohort.snapshot()["claim"]
    cohort.record_outcome(claim["claim_id"], action="leaf_delete", identity=watcher_id,
        outcome="succeeded", evidence_sha256=hashlib.sha256(b"wrong leaf evidence").hexdigest(),
        now_monotonic=41.0, typed_404_identity=watcher_id,
        bounded_recovery_deadline_monotonic=50.0)
    assert cohort.claim_group(claim["claim_id"], now_monotonic=42.0,
        bounded_recovery_deadline_monotonic=50.0)
    _stub_group_only_recovery_cap(monkeypatch, d, lambda: 100.0, lambda: now)
    with pytest.raises(ValueError, match="outcome differs from its exact ETag/404"):
        azure_provider.recover_shared_watcher_group_only(d, o, object(), o.attempt_root,
            clock=lambda: 100.0, utc_now=lambda: now)
    after = cohort.snapshot()
    assert after["claim"]["state"] == "group_claimed"
    assert after["outcomes"][0]["evidence_sha256"] == hashlib.sha256(b"wrong leaf evidence").hexdigest()
    assert after.get("group_only_recovery_intent") is None


def test_group_claimed_roundtrip_binds_raw_durable_result_and_rejects_tampering(
        repository_root, tmp_path, monkeypatch):
    from cloud_network_benchmark.azure_auxiliary_cleanup import canonical
    now = datetime(2026, 11, 25, 12, 1, tzinfo=timezone.utc)
    d, o, spec, cohort, journal, _group_id, watcher_id, _owner = _group_only_recovery_fixture(
        repository_root, tmp_path, now)
    state = json.loads(journal.read_text())
    state["actions"].pop()
    journal.write_text(json.dumps(state, indent=2) + "\n")
    class AbsenceRunner:
        def run(self, request):
            stamp = now
            return CommandResult(action_id=request.action_id, outcome=CommandOutcome.FAILED,
                started_at=stamp, finished_at=stamp + timedelta(seconds=0.5),
                duration_seconds=0.5, exit_code=1, stdout="",
                stderr="ERROR: Not Found(" + json.dumps({"error": {"code": "NotFound",
                    "message": f"Resource {watcher_id.lower()} not found.", "details": []}}) + ")")
    azure_provider._command(AbsenceRunner(), o, o.attempt_root,
        ["az", "rest", "--method", "GET", "--url", watcher_id + "?api-version=2023-09-01",
            "--output", "json"], "azure_auxiliary_watcher_absence", lambda: 100.0,
        allow_not_found=True, expected_missing_resource_id=watcher_id,
        expected_missing_resource_url=watcher_id + "?api-version=2023-09-01")
    state = json.loads(journal.read_text())
    raw_absence_result = state["actions"][2]["result"]
    assert raw_absence_result["redactions_applied"] is True
    raw_digest = hashlib.sha256(canonical(raw_absence_result)).hexdigest()
    claim = cohort.snapshot()["claim"]
    cohort.record_outcome(claim["claim_id"], action="leaf_delete", identity=watcher_id,
        outcome="succeeded", evidence_sha256=raw_digest, now_monotonic=41.0,
        typed_404_identity=watcher_id, bounded_recovery_deadline_monotonic=50.0)
    assert cohort.claim_group(claim["claim_id"], now_monotonic=42.0,
        bounded_recovery_deadline_monotonic=50.0)
    raw_absence_result["redactions_applied"] = False
    journal.write_text(json.dumps(state, indent=2) + "\n")
    _stub_group_only_recovery_cap(monkeypatch, d, lambda: 100.0, lambda: now)
    with pytest.raises(ValueError, match="differs from its exact ETag/404 action receipt"):
        azure_provider.recover_shared_watcher_group_only(d, o, object(), o.attempt_root,
            clock=lambda: 100.0, utc_now=lambda: now)
    assert cohort.snapshot().get("group_only_recovery_intent") is None


def test_group_only_rejects_disagreeing_expired_owner_cap_clocks(repository_root, tmp_path, monkeypatch):
    now = datetime(2026, 11, 25, 23, 59, tzinfo=timezone.utc)
    d, o, _spec, cohort, _journal, _group_id, _watcher_id, _owner = _group_only_recovery_fixture(
        repository_root, tmp_path, now - timedelta(minutes=2))
    _stub_group_only_recovery_cap(monkeypatch, d, lambda: 100.0, lambda: now)
    monkeypatch.setattr(azure_provider, "_bounded_late_auxiliary_deadline",
        lambda *_args, **_kwargs: (99.0, now + timedelta(seconds=60)))
    with pytest.raises(ValueError, match="monotonic and UTC cap anchors disagree"):
        azure_provider.recover_shared_watcher_group_only(d, o, object(), o.attempt_root,
            clock=lambda: 100.0, utc_now=lambda: now)
    assert cohort.snapshot().get("group_only_recovery_intent") is None


@pytest.mark.parametrize("continued", [False, True], ids=["original-claimed", "group-claimed-leaf-receipt"])
@pytest.mark.parametrize("stale_leaf_inventory", [False, True], ids=["fresh-list", "stale-list-once"])
@pytest.mark.parametrize("expired_owner_caps", [False, True], ids=["owner-cap-live", "owner-cap-expired"])
def test_group_only_recovery_uses_existing_cleanup_hook_and_source_receipt(
        repository_root, tmp_path, monkeypatch, continued, stale_leaf_inventory, expired_owner_caps):
    from types import SimpleNamespace
    from cloud_network_benchmark.azure_auxiliary_cleanup import canonical
    from cloud_network_benchmark.lifecycle_cleanup import (cleanup_saved_provider,
        reconcile_saved_cleanup_result, save_cleanup_settings)
    from cloud_network_benchmark.contracts.common import Evidence

    # The card expires in one minute, earlier than the live owner's 660-second
    # bound, so both owner-cap branches must clip the operation to the card.
    now = datetime(2026, 11, 25, 23, 59, tzinfo=timezone.utc)
    d, o, spec, cohort, journal, group_id, watcher_id, owner = _group_only_recovery_fixture(
        repository_root, tmp_path, now - timedelta(minutes=2))
    saved_state = json.loads(journal.read_text())
    saved_leaf = azure_provider._saved_watcher_leaf_receipt(saved_state, o, spec,
        persisted_result_binding=continued)
    claim = cohort.snapshot()["claim"]
    if continued:
        cohort.record_outcome(claim["claim_id"], action="leaf_delete", identity=watcher_id,
            outcome="succeeded", evidence_sha256=saved_leaf["absence_evidence_sha256"],
            now_monotonic=41.0, typed_404_identity=watcher_id,
            bounded_recovery_deadline_monotonic=50.0)
        assert cohort.claim_group(claim["claim_id"], now_monotonic=42.0,
            bounded_recovery_deadline_monotonic=50.0)
    clock_value = [100.0]
    def clock(): return clock_value[0]
    def utc_now(): return now + timedelta(seconds=clock_value[0]-100.0)
    _stub_group_only_recovery_cap(monkeypatch, d, clock, utc_now)
    if expired_owner_caps:
        monkeypatch.setattr(azure_provider, "_bounded_late_auxiliary_deadline",
            lambda *_args, **_kwargs: (clock() - 1.0, utc_now() - timedelta(seconds=1)))
    monkeypatch.setattr(azure_provider, "_observe_profile_cleanup_clock",
        lambda *args, **kwargs: {"timing_status": "late", "timing_failure": "original deadline elapsed"})
    inventory_reads = [0]
    def inventory(_runner, _options, _root, endpoint, action, _clock, _required, **_kwargs):
        if "resourcegroups?" in endpoint:
            rows = [{"id": group_id, "name": "NetworkWatcherRG"}]
        elif action == "azure_auxiliary_group_only_subscription_resources":
            inventory_reads[0] += 1
            rows = ([{"id": watcher_id, "type": "Microsoft.Network/networkWatchers"}]
                if stale_leaf_inventory and inventory_reads[0] == 1 else [])
        else:
            rows = []
        if action.endswith("immediate_children"):
            rows = []
        return rows, []
    monkeypatch.setattr(azure_provider, "_read_arm_collection", inventory)

    class Runner:
        def __init__(self): self.requests = []
        def run(self, request):
            self.requests.append(request)
            stamp = utc_now()
            if request.action_type == "azure_auxiliary_group_only_exact_readback":
                stdout = json.dumps({"id": group_id})
            elif request.action_type == "azure_auxiliary_group_absence":
                stdout = "false"
            elif request.action_type == "azure_auxiliary_group_only_leaf_absence":
                return CommandResult(action_id=request.action_id, outcome=CommandOutcome.FAILED,
                    started_at=stamp, finished_at=stamp, duration_seconds=0.0, exit_code=1,
                    stdout="", stderr="ERROR: Not Found(" + json.dumps({"error": {
                        "code": "NotFound", "message": f"Resource {watcher_id} not found."}}) + ")")
            else:
                stdout = ""
            outcome = CommandOutcome.TIMED_OUT if request.action_type == "azure_auxiliary_group_delete" and False else CommandOutcome.SUCCEEDED
            return CommandResult(action_id=request.action_id, outcome=outcome,
                started_at=stamp, finished_at=stamp, duration_seconds=0.0,
                exit_code=0 if outcome == CommandOutcome.SUCCEEDED else None,
                stdout=stdout, stderr="")
    runner = Runner()
    request = expiry_request(d, o, d.reservation.value.expires_at.value - timedelta(seconds=120))
    settings_path = save_cleanup_settings(provider="azure", deployment=d, request=request,
        reservation=d.reservation.value, provider_lead_seconds=120.0, settings=o,
        private_root=tmp_path / "saved")
    record = SimpleNamespace(provider="azure", deployment_id=d.deployment_id,
        cleanup_settings_reference=str(settings_path), journal_reference=str(journal))
    physical, _saved, _absent = cleanup_saved_provider(record, runner=runner,
        utc_now=utc_now, monotonic_now=clock)
    assert physical.cleanup_state == "succeeded" and physical.timing_status == "late"
    assert sum(row.action_type == "azure_auxiliary_group_delete" for row in runner.requests) == 1
    assert not any(row.action_type == "azure_auxiliary_watcher_delete" for row in runner.requests)
    assert inventory_reads[0] == (2 if stale_leaf_inventory else 1)
    after = cohort.snapshot()
    if continued:
        assert after["claim"]["state"] == "group_claimed" and len(after["outcomes"]) == 1
        assert after["outcomes"][0]["action"] == "leaf_delete"
        assert after["outcomes"][0]["evidence_sha256"] == saved_leaf["absence_evidence_sha256"]
    else:
        assert after["claim"]["state"] == "claimed" and after["outcomes"] == []
    assert after["bounded_recovery_deadline_monotonic"] == 50.0
    assert after["group_only_recovery_receipt"]["group_absent"] is True
    assert after["group_only_recovery_intent"]["operation_deadline_utc"] == "2026-11-26T00:00:00+00:00"

    reservation_id = d.reservation.value.reservation_id.value
    ledger_path = tmp_path / "original-ledger-copy.json"
    class LedgerCopy:
        path = ledger_path
        scope_id = "isolated-cleanup-reconciliation-copy"
        def __init__(self):
            self.state = {"reservations": {reservation_id: {
                "evidence": d.reservation.value.model_dump(mode="json"), "provider_cleanup": {}}}}
        def inspect(self): return self.state
        def reconcile(self, event):
            self.state["reservations"][reservation_id]["provider_cleanup"][event.provider] = event.cleanup_verified_absent
    ledger = LedgerCopy()
    recovery = SimpleNamespace(reservation_id=reservation_id, ledger_path=str(ledger_path),
        ledger_scope_id=ledger.scope_id)
    reconciled = reconcile_saved_cleanup_result(record, physical, recovery=recovery,
        ledger=ledger, utc_now=lambda: utc_now())
    assert reconciled["absence_proved"] is True and reconciled["cleanup_open"] is False
    assert reconciled["ledger_event"].cleanup_verified_absent is True


def test_group_only_unknown_intent_never_dispatches_a_second_delete(repository_root, tmp_path, monkeypatch):
    from cloud_network_benchmark.lifecycle_cleanup import cleanup_saved_provider, save_cleanup_settings
    from types import SimpleNamespace

    now = datetime(2026, 11, 25, 12, 1, tzinfo=timezone.utc)
    d, o, spec, cohort, journal, group_id, watcher_id, owner = _group_only_recovery_fixture(
        repository_root, tmp_path, now)
    clock_value = [100.0]
    def clock(): return clock_value[0]
    def utc_now(): return now + timedelta(seconds=clock_value[0]-100.0)
    _stub_group_only_recovery_cap(monkeypatch, d, clock, utc_now)
    monkeypatch.setattr(azure_provider, "_observe_profile_cleanup_clock",
        lambda *args, **kwargs: {"timing_status": "late", "timing_failure": "original deadline elapsed"})
    def inventory(_runner, _options, _root, endpoint, action, _clock, _required, **_kwargs):
        rows = [{"id": group_id, "name": "NetworkWatcherRG"}] if "resourcegroups?" in endpoint else []
        if action.endswith("immediate_children"):
            rows = []
        return rows, []
    monkeypatch.setattr(azure_provider, "_read_arm_collection", inventory)
    class Runner:
        def __init__(self): self.requests = []
        def run(self, request):
            self.requests.append(request)
            stamp = utc_now()
            if request.action_type == "azure_auxiliary_group_only_exact_readback":
                return CommandResult(action_id=request.action_id, outcome=CommandOutcome.SUCCEEDED,
                    started_at=stamp, finished_at=stamp, duration_seconds=0.0, exit_code=0,
                    stdout=json.dumps({"id": group_id}), stderr="")
            if request.action_type == "azure_auxiliary_group_only_leaf_absence":
                return CommandResult(action_id=request.action_id, outcome=CommandOutcome.FAILED,
                    started_at=stamp, finished_at=stamp, duration_seconds=0.0, exit_code=1,
                    stdout="", stderr="ERROR: Not Found(" + json.dumps({"error": {
                        "code": "NotFound", "message": f"Resource {watcher_id} not found."}}) + ")")
            if request.action_type == "azure_auxiliary_group_delete":
                return CommandResult(action_id=request.action_id, outcome=CommandOutcome.TIMED_OUT,
                    started_at=stamp, finished_at=stamp, duration_seconds=0.0, exit_code=None,
                    stdout="", stderr="ambiguous timeout")
            raise AssertionError("no further provider operation is expected after unknown delete")
    runner = Runner()
    request = expiry_request(d, o, d.reservation.value.expires_at.value - timedelta(seconds=120))
    settings_path = save_cleanup_settings(provider="azure", deployment=d, request=request,
        reservation=d.reservation.value, provider_lead_seconds=120.0, settings=o,
        private_root=tmp_path / "saved")
    record = SimpleNamespace(provider="azure", deployment_id=d.deployment_id,
        cleanup_settings_reference=str(settings_path), journal_reference=str(journal))
    with pytest.raises(RuntimeError, match="timed_out"):
        cleanup_saved_provider(record, runner=runner, utc_now=utc_now, monotonic_now=clock)
    assert cohort.snapshot().get("group_only_recovery_intent") is not None
    with pytest.raises(RuntimeError, match="successful exact DELETE"):
        cleanup_saved_provider(record, runner=runner, utc_now=utc_now, monotonic_now=clock)
    assert sum(row.action_type == "azure_auxiliary_group_delete" for row in runner.requests) == 1


def test_group_only_hook_delegates_legacy_leaf_absent_claim(repository_root, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from cloud_network_benchmark.lifecycle_cleanup import cleanup_saved_provider, save_cleanup_settings

    now = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    d, o, spec, cohort, journal, _group_id, watcher_id, _owner = _group_only_recovery_fixture(
        repository_root, tmp_path, now)
    claim_id = cohort.snapshot()["claim"]["claim_id"]
    cohort.record_outcome(claim_id, action="leaf_delete", identity=watcher_id,
        outcome="succeeded", evidence_sha256=hashlib.sha256(b"prior exact absence").hexdigest(),
        now_monotonic=41.0, typed_404_identity=watcher_id)
    now_utc = lambda: now
    clock = lambda: 100.0
    request = expiry_request(d, o, d.reservation.value.expires_at.value - timedelta(seconds=120))
    settings_path = save_cleanup_settings(provider="azure", deployment=d, request=request,
        reservation=d.reservation.value, provider_lead_seconds=120.0, settings=o,
        private_root=tmp_path / "saved")
    record = SimpleNamespace(provider="azure", deployment_id=d.deployment_id,
        cleanup_settings_reference=str(settings_path), journal_reference=str(journal))
    delegated = object()
    monkeypatch.setattr(azure_provider, "destroy", lambda *args, **kwargs: delegated)
    physical, _saved, _absent = cleanup_saved_provider(record, runner=object(),
        utc_now=now_utc, monotonic_now=clock)
    assert physical is delegated


@pytest.mark.parametrize("case", ["before_window", "crosses_window_end"])
def test_group_only_rejects_leaf_delete_outside_card_even_if_absence_is_inside(repository_root, tmp_path, case, monkeypatch):
    from types import SimpleNamespace
    from cloud_network_benchmark.lifecycle_cleanup import cleanup_saved_provider, save_cleanup_settings

    now = datetime(2026, 11, 25, 12, 1, tzinfo=timezone.utc)
    d, o, spec, _cohort, journal, _group_id, _watcher_id, _owner = _group_only_recovery_fixture(
        repository_root, tmp_path, datetime(2026, 11, 25, 0, 0, tzinfo=timezone.utc))
    state = json.loads(journal.read_text())
    etag_row, delete_row, absence_row = state["actions"]
    if case == "before_window":
        etag_time = datetime(2026, 11, 24, 23, 58, tzinfo=timezone.utc)
        delete_time = datetime(2026, 11, 24, 23, 59, tzinfo=timezone.utc)
        absence_time = datetime(2026, 11, 25, 0, 1, tzinfo=timezone.utc)
    else:
        etag_time = datetime(2026, 11, 25, 23, 59, tzinfo=timezone.utc)
        delete_time = datetime(2026, 11, 25, 23, 59, 59, 800000, tzinfo=timezone.utc)
        absence_time = datetime(2026, 11, 26, 0, 0, 10, tzinfo=timezone.utc)
    for row, start in ((etag_row, etag_time), (delete_row, delete_time),
            (absence_row, absence_time)):
        row["result"]["started_at"] = start.isoformat()
        row["result"]["finished_at"] = (start + timedelta(milliseconds=500)).isoformat()
    journal.write_text(json.dumps(state) + "\n")
    journal.chmod(0o600)
    request = expiry_request(d, o, d.reservation.value.expires_at.value - timedelta(seconds=120))
    settings_path = save_cleanup_settings(provider="azure", deployment=d, request=request,
        reservation=d.reservation.value, provider_lead_seconds=120.0, settings=o,
        private_root=tmp_path / "saved")
    record = SimpleNamespace(provider="azure", deployment_id=d.deployment_id,
        cleanup_settings_reference=str(settings_path), journal_reference=str(journal))
    _stub_group_only_recovery_cap(monkeypatch, d, lambda: 100.0, lambda: now)
    with pytest.raises(PermissionError, match="leaf DELETE was not authorized"):
        cleanup_saved_provider(record, runner=object(), utc_now=lambda: now,
            monotonic_now=lambda: 100.0)


def test_group_only_peer_replays_source_receipt_without_second_delete(repository_root, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from cloud_network_benchmark.azure_auxiliary_cleanup import AuxiliaryCleanupCohort
    from cloud_network_benchmark.lifecycle_cleanup import cleanup_saved_provider, save_cleanup_settings
    from cloud_network_benchmark.orchestration import SavedLifecycleRecovery, SavedProviderRecovery

    now = datetime(2026, 11, 25, 12, 11, tzinfo=timezone.utc)
    d1 = deployment(repository_root, "exp-903-three-provider-smoke.yaml")
    campaign2 = d1.campaign_id + "-peer"
    reservation2 = d1.reservation.value.model_copy(update={"campaign_id": campaign2})
    d2 = type(d1).model_validate(d1.model_copy(update={"campaign_id": campaign2,
        "run_id": campaign2 + "-azure", "deployment_id": campaign2 + "-azure-deployment",
        "reservation": d1.reservation.model_copy(update={"value": reservation2})})
        .model_dump(mode="python"))
    sub = "188a5848-76fb-4d57-8f2a-32c09284d486"
    group_id = f"/subscriptions/{sub}/resourceGroups/NetworkWatcherRG"
    watcher_id = group_id + "/providers/Microsoft.Network/networkWatchers/NetworkWatcher_swedencentral"
    owners = []
    for entry, d in (("V-S1", d1), ("V-S3", d2)):
        parent = f"/subscriptions/{sub}/resourceGroups/{_slug(d)}"
        owners.append({"entry_id": entry, "run_id": d.run_id, "campaign_id": d.campaign_id,
            "deployment_id": d.deployment_id, "config_sha256": d.config_sha256,
            "resource_group_id": parent,
            "vm_ids": [f"{parent}/providers/Microsoft.Compute/virtualMachines/{_slug(d)}-{role}"
                for role in ("a", "b")],
            "nic_ids": [f"{parent}/providers/Microsoft.Network/networkInterfaces/{_slug(d)}-{role}-nic"
                for role in ("a", "b")]})
    spec = _provider_cleanup_spec(tmp_path, owners, sub, group_id, watcher_id,
        "group-only-peer-replay")
    (tmp_path / "saved").mkdir(mode=0o700)
    ds, os = [], []
    for d, owner in zip((d1, d2), spec["owners"]):
        d = type(d).model_validate(d.model_copy(update={"config_sha256": owner["config_sha256"]})
            .model_dump(mode="python"))
        o = options(tmp_path / owner["entry_id"] / "attempt", d.admission.priority).model_copy(update={
            "subscription_id": sub, "azure_auxiliary_cleanup": spec})
        o.attempt_root.mkdir(mode=0o700, parents=True)
        journal = o.attempt_root / "recovery.json"
        actions = []
        if owner["entry_id"] == "V-S1":
            etag = 'W/"peer-etag"'; prefix = ["az", "rest", "--subscription", sub]
            def row(action, argv, result):
                command = CommandRequest(action_id="peer-" + action, action_type=action, argv=argv,
                    cwd=str(o.attempt_root), timeout_seconds=30, environment_classification="none")
                result = result.model_copy(update={"action_id": command.action_id})
                return {"request": command.durable_view(), "result": result.durable_view()}
            for action, method, headers, outcome, stdout, stderr, second in (
                ("azure_auxiliary_watcher_etag", "GET", [], CommandOutcome.SUCCEEDED,
                    json.dumps({"id": watcher_id, "etag": etag}), "", 1),
                ("azure_auxiliary_watcher_delete", "DELETE", ["--headers", "If-Match=" + etag],
                    CommandOutcome.SUCCEEDED, "", "", 2),
                ("azure_auxiliary_watcher_absence", "GET", [], CommandOutcome.FAILED, "",
                    "ERROR: Not Found(" + json.dumps({"error": {"code": "NotFound",
                        "message": f"Resource {watcher_id} not found.", "details": []}}) + ")", 3)):
                stamp = now - timedelta(seconds=10-second)
                result = CommandResult(action_id="pending", outcome=outcome, started_at=stamp,
                    finished_at=stamp + timedelta(milliseconds=100), duration_seconds=.1,
                    exit_code=0 if outcome == CommandOutcome.SUCCEEDED else 1, stdout=stdout, stderr=stderr)
                argv = prefix + ["--method", method, "--url", watcher_id + "?api-version=2023-09-01",
                    "--output", "json"] + headers
                actions.append(row("azure_auxiliary_watcher_" + ("absence" if action.endswith("absence")
                    else "delete" if action.endswith("delete") else "etag"), argv, result))
        journal.write_text(json.dumps({"run_id": d.run_id, "deployment_id": d.deployment_id,
            "config_sha256": d.config_sha256, "vm_ids": owner["vm_ids"], "expiry": {}, "actions": actions}) + "\n")
        journal.chmod(0o600)
        request = expiry_request(d, o, d.reservation.value.expires_at.value - timedelta(seconds=120))
        saved = save_cleanup_settings(provider="azure", deployment=d, request=request,
            reservation=d.reservation.value, provider_lead_seconds=120.0, settings=o,
            private_root=tmp_path / "saved" / owner["entry_id"])
        entry_root = Path(spec["state_root"]).parent / owner["entry_id"]
        entry_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        recovery = SavedLifecycleRecovery(schema_version=1, attempt_id=d.run_id,
            controller_session_id="peer-replay-fixture", reservation_id=d.reservation.value.reservation_id.value,
            providers=[SavedProviderRecovery(provider="azure", deployment_id=d.deployment_id,
                journal_reference=str(journal), expiry_registered=True, creation_may_have_started=True,
                cleanup_open=True, cleanup_settings_reference=str(saved))])
        recovery_path = entry_root / "lifecycle-recovery.json"
        recovery_path.write_text(recovery.model_dump_json(indent=2) + "\n")
        recovery_path.chmod(0o600)
        ds.append((d, o, journal, saved))
    cohort = AuxiliaryCleanupCohort(spec)
    for owner in spec["owners"]:
        cohort.register_absent(owner, baseline_sha256=hashlib.sha256(owner["entry_id"].encode()).hexdigest(),
            activity_sha256=hashlib.sha256((owner["entry_id"] + "activity").encode()).hexdigest(),
            deadline_monotonic=90.0)
    claim = cohort.claim_leaf(now_monotonic=40.0, bounded_recovery_deadline_monotonic=50.0)
    old_snapshot = cohort.snapshot()
    assert claim and old_snapshot["claim"]["state"] == "claimed"

    group_exists = [True]
    descendant_reads = [0]
    def inventory(_runner, _options, _root, endpoint, action, _clock, _required, **_kwargs):
        if action in {"azure_auxiliary_group_only_flowLogs_inventory",
                "azure_auxiliary_group_only_packetCaptures_inventory",
                "azure_auxiliary_group_only_connectionMonitors_inventory"}:
            descendant_reads[0] += 1
        if "resourcegroups?" in endpoint:
            rows = [{"id": group_id, "name": "NetworkWatcherRG"}] if group_exists[0] else []
        else:
            rows = []
        return rows, []
    monkeypatch.setattr(azure_provider, "_read_arm_collection", inventory)
    monkeypatch.setattr(azure_provider, "_observe_profile_cleanup_clock",
        lambda *args, **kwargs: {"timing_status": "late", "timing_failure": "original deadline elapsed"})
    _stub_group_only_recovery_cap(monkeypatch, d1, lambda: 100.0, lambda: now)
    class Runner:
        def __init__(self): self.requests = []
        def run(self, request):
            self.requests.append(request)
            stamp = now
            if request.action_type == "azure_auxiliary_group_only_exact_readback":
                stdout = json.dumps({"id": group_id})
            elif request.action_type == "azure_auxiliary_group_delete":
                stdout = ""; group_exists[0] = False
            elif request.action_type in {"azure_auxiliary_group_absence", "azure_auxiliary_group_only_receipt_absence"}:
                stdout = "false"
            elif request.action_type == "azure_auxiliary_group_only_leaf_absence":
                return CommandResult(action_id=request.action_id, outcome=CommandOutcome.FAILED,
                    started_at=stamp, finished_at=stamp, duration_seconds=0.0, exit_code=1,
                    stdout="", stderr="ERROR: Not Found(" + json.dumps({"error": {
                        "code": "NotFound", "message": f"Resource {watcher_id} not found."}}) + ")")
            else: raise AssertionError(request.action_type)
            return CommandResult(action_id=request.action_id, outcome=CommandOutcome.SUCCEEDED,
                started_at=stamp, finished_at=stamp, duration_seconds=0.0, exit_code=0, stdout=stdout, stderr="")
    runner = Runner()
    records = [SimpleNamespace(provider="azure", deployment_id=d.deployment_id,
        cleanup_settings_reference=str(saved), journal_reference=str(journal)) for d, _o, journal, saved in ds]
    retained_amounts = [d.reservation.value.reserved_cost_usd.value for d, _o, _j, _s in ds]
    first, *_ = cleanup_saved_provider(records[0], runner=runner, utc_now=lambda: now,
        monotonic_now=lambda: 100.0)
    assert first.cleanup_state == "succeeded" and first.timing_status == "late"
    first_descendant_reads = descendant_reads[0]
    first_descendant_requests = sum("group_only_leaf_absence" in row.action_type for row in runner.requests)
    second, *_ = cleanup_saved_provider(records[1], runner=runner, utc_now=lambda: now,
        monotonic_now=lambda: 101.0)
    assert second.cleanup_state == "succeeded" and second.timing_status == "late"
    assert descendant_reads[0] == first_descendant_reads
    assert sum("group_only_leaf_absence" in row.action_type for row in runner.requests) == first_descendant_requests
    assert sum(row.action_type == "azure_auxiliary_group_delete" for row in runner.requests) == 1
    assert not any(row.action_type == "azure_auxiliary_watcher_delete" for row in runner.requests)
    after = cohort.snapshot()
    assert after["claim"] == old_snapshot["claim"]
    assert after["owners"] == old_snapshot["owners"]
    assert after["bounded_recovery_deadline_monotonic"] == 50.0
    assert after["outcomes"] == []
    assert [d.reservation.value.reserved_cost_usd.value for d, _o, _j, _s in ds] == retained_amounts


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
    state = {"provision": False, "mono_calls": 0, "utc_calls": 0,
        "stage_started_monotonic": None, "validation_monotonic": None}

    def fake_clock():
        if not state["provision"]:
            return origin_mono + 100
        state["mono_calls"] += 1
        value = provision_mono + (0.0001 if state["utc_calls"] else 0.0)
        if state["mono_calls"] == 1:
            state["stage_started_monotonic"] = value
        elif state["mono_calls"] == 2:
            state["validation_monotonic"] = value
        return value

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
        provision_stage_seconds = float(d.provisioning_timeout_seconds or o.command_timeout_seconds)
        profile_stage_deadline = cap_start_mono + 900 + 900
        effective_stop_deadline = cap_start_mono + (stop - cap_start_utc).total_seconds()
        expected_stage_deadline = min(
            state["stage_started_monotonic"] + provision_stage_seconds,
            profile_stage_deadline, cap_end_mono, effective_stop_deadline)
        second_sample_deadline = min(
            state["validation_monotonic"] + provision_stage_seconds,
            profile_stage_deadline, cap_end_mono, effective_stop_deadline)
        assert runtime_options.workflow_stage_deadline_monotonic == expected_stage_deadline
        assert runtime_options.workflow_stage_deadline_monotonic != second_sample_deadline
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
