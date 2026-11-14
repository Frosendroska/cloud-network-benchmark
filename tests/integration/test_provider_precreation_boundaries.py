"""Source-shaped provider rejection/absence regressions; all commands injected."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cloud_network_benchmark import azure_provider as az
from cloud_network_benchmark import terraform_plan as tp
from cloud_network_benchmark.contracts.execution import CommandResult
from cloud_network_benchmark.providers.aws import AwsProviderDriver, AwsProviderError
from cloud_network_benchmark.providers.gcp import GcpProviderError, SAFETY_LEAD_SECONDS
from tests.unit.test_aws_provider import NOW, ROOT
from tests.integration.test_gcp_provider_sequences import _cleanup_fixture, _template_response
from tests.contract.test_azure_provider_contract import deployment, options
from tests.unit.test_azure_saved_plan import azure_scope, azure_document


class Commands:
    def __init__(self, responses): self.responses, self.requests = list(responses), []
    def run(self, q):
        self.requests.append(q)
        code, out, err = self.responses.pop(0)
        return CommandResult(action_id=q.action_id, outcome='failed' if code else 'succeeded',
            started_at=NOW, finished_at=NOW, duration_seconds=0, exit_code=code, stdout=out, stderr=err)


SAFETY = {'document_name':'cnb-expiry','document_version':'1'}
PARAMS = {'RunId':['original-run'], 'DeploymentId':['original-deployment'],
    'AutomationAssumeRole':['arn:aws:iam::123456789012:role/cnb-expiry'],
    'ExpiryTriggerUtc':['2026-11-14T20:00:00Z'], 'Region':['eu-north-1']}
REJECT = 'An error occurred (InvalidAutomationExecutionParametersException) when calling the StartAutomationExecution operation: The defined assume role is unable to be assumed.'


def aws_driver(runner):
    return AwsProviderDriver(runner, SimpleNamespace(aws_cli='aws',registration_timeout_seconds=90,
        authentication_reference='secret-reference',public_key='public-key-sentinel'),clock=lambda:NOW)


@pytest.mark.parametrize('mismatch', [None,'document','version','role','deadline','run','deployment','region'])
def test_aws_prefix_query_recovers_only_original_document_version_and_parameters(mismatch):
    execution={'AutomationExecutionId':'exec-original', 'DocumentName':SAFETY['document_name'],
        'DocumentVersion':'1','Parameters':deepcopy(PARAMS)}
    if mismatch=='document': execution['DocumentName']='neighbor'
    if mismatch=='version': execution['DocumentVersion']='2'
    keys={'role':'AutomationAssumeRole','deadline':'ExpiryTriggerUtc','run':'RunId','deployment':'DeploymentId','region':'Region'}
    if mismatch in keys: execution['Parameters'][keys[mismatch]]=['foreign']
    rows=[{'DocumentName':'cnb-expiry-neighbor','AutomationExecutionId':'never-read'},
        {'DocumentName':'cnb-expiry','AutomationExecutionId':'exec-original'}]
    runner=Commands([(1,'','response lost'),(0,json.dumps({'AutomationExecutionMetadataList':rows}),''),
        (0,json.dumps({'AutomationExecution':execution}),'')])
    result=aws_driver(runner)._start_or_recover_automation('eu-north-1',SAFETY,PARAMS,'original-token')
    assert result == ('exec-original' if mismatch is None else None)
    assert 'Key=DocumentNamePrefix,Values=cnb-expiry' in runner.requests[1].argv
    assert len(runner.requests)==3 and 'never-read' not in runner.requests[2].argv
    assert sum('start-automation-execution' in q.argv for q in runner.requests)==1


@pytest.mark.parametrize('response', [
    (0,'{}',''), (0,'[]',''), (0,'{"AutomationExecutionMetadataList":[],"NextToken":"more"}',''),
    (0,'{"AutomationExecutionMetadataList":[{}]}',''),
    (0,'{"AutomationExecutionMetadataList":[]}','WARNING: partial'), (254,'','AccessDenied')])
def test_aws_typed_rejection_cannot_close_with_unknown_lookup(response):
    runner=Commands([(254,'',REJECT),response])
    assert aws_driver(runner)._start_or_recover_automation('eu-north-1',SAFETY,PARAMS,'token') is None
    assert len(runner.requests)==2


def test_aws_rejection_requires_server_error_and_complete_exact_absence(tmp_path):
    runner=Commands([(254,'',REJECT),(0,'{"AutomationExecutionMetadataList":[]}','')])
    driver=aws_driver(runner); driver._active_attempt=tmp_path
    with pytest.raises(AwsProviderError,match='rejected expiry start') as raised:
        driver._start_or_recover_automation('eu-north-1',SAFETY,PARAMS,'original-token')
    evidence=raised.value.rejection_evidence
    assert evidence['lookup_complete'] and evidence['matching_document_execution_count']==0
    assert evidence['parameters_sha256']==hashlib.sha256(json.dumps(PARAMS,sort_keys=True).encode()).hexdigest()
    assert evidence['client_token']=='original-token' and raised.value.category=='expiry_start_rejected'
    assert json.loads((tmp_path/'commands.jsonl').read_text().splitlines()[0])['diagnostic']['stderr']==REJECT
    # An empty successful lookup cannot resolve a lost local Start response.
    lost=Commands([(1,'','connection lost'),(0,'{"AutomationExecutionMetadataList":[]}','')])
    assert aws_driver(lost)._start_or_recover_automation('eu-north-1',SAFETY,PARAMS,'original-token') is None


def test_aws_private_diagnostic_is_redacted_bounded_and_hashes_original(tmp_path):
    raw='ASIA'+'A'*16+' secret-reference public-key-sentinel aws_session_token=token-secret\n'+'x'*9000
    runner=Commands([(254,'',raw)])
    driver=aws_driver(runner);driver._active_attempt=tmp_path
    driver._run('aws-expiry-confirm-eu-north-1',['aws','ssm','get-automation-execution'],90)
    row=json.loads((tmp_path/'commands.jsonl').read_text())['diagnostic']
    assert len(row['stderr'])<=8192 and row['stderr_truncated']
    assert row['stderr_sha256']==hashlib.sha256(raw.encode()).hexdigest()
    assert all(secret not in row['stderr'] for secret in ('ASIA'+'A'*16,'secret-reference','public-key-sentinel','token-secret'))


def template_case(tmp_path):
    d,driver=_cleanup_fixture(tmp_path)
    trigger=d.reservation.value.expires_at.value-timedelta(seconds=SAFETY_LEAD_SECONDS)
    described=_template_response(driver,d,'vm_a','111',trigger,NOW)
    name=described['name']
    item={'id':'111','name':name,'zone':d.vm_a.zone,'expected_policy':None,
        'self_link_unique':f'projects/{driver.settings.project_id}/global/instanceTemplates/{name}?uniqueId=111'}
    return d,driver,trigger,described,item


@pytest.mark.parametrize('canonical', ['Z','offset'])
def test_gcp_provider_label_and_exact_deadline_canonical_forms_are_accepted(tmp_path,canonical):
    d,driver,trigger,described,item=template_case(tmp_path)
    described['properties']['labels']['goog-terraform-provisioned']='true'
    if canonical=='offset': described['properties']['scheduling']['terminationTime']=trigger.isoformat()
    driver._validate_template(described,item,'vm_a',d,trigger)


@pytest.mark.parametrize('field', ['labels','scheduling.terminationTime','scheduling.instanceTerminationAction',
    'identity','disks','networkInterfaces','metadata'])
def test_gcp_named_private_mismatch_survives_stale_outer_failure_save(tmp_path,field):
    d,driver,trigger,described,item=template_case(tmp_path)
    attempt=driver._attempt_dir(d);driver._active_attempt=attempt
    outer={'run_id':d.run_id,'deployment_id':d.deployment_id,'registered':False}
    driver._save(attempt,deepcopy(outer))
    props=described['properties']
    if field=='labels': props['labels']['goog-terraform-provisioned']='false'
    elif field=='identity': described['selfLink']=described['selfLink'].replace(driver.settings.project_id,'foreign-project')
    elif field=='disks': props['disks'][0]['initializeParams']['diskSizeGb']=64
    elif field=='networkInterfaces': props['networkInterfaces'][0]['networkIP']='10.9.9.9'
    elif field=='metadata': props['metadata']['items'].append({'key':'ssh-keys','value':'secret-bootstrap-value'})
    elif field=='scheduling.terminationTime': props['scheduling']['terminationTime']=(trigger+timedelta(microseconds=1)).isoformat()
    else: props['scheduling']['instanceTerminationAction']='DELETE'
    if field=='metadata':
        # A validly shaped changed value exercises the named mismatch, not a duplicate-key response.
        props['metadata']['items']=[x for x in props['metadata']['items'] if x['key']!='ssh-keys']+[{'key':'ssh-keys','value':'secret-bootstrap-value'}]
    with pytest.raises(GcpProviderError,match=field): driver._validate_template(described,item,'vm_a',d,trigger)
    outer['registration_failure']={'category':'expiry_mismatch'}
    driver._save(attempt,outer)
    diagnostic=json.loads((attempt/'attempt.json').read_text())['template_validation_diagnostics'][0]
    assert field in diagnostic['mismatched_fields']
    assert len(diagnostic['observed_field_sha256'][field])==64
    assert driver.settings.public_key not in json.dumps(diagnostic) and 'secret-bootstrap-value' not in json.dumps(diagnostic)


@pytest.mark.parametrize('invalid', ['extra-label','wrong-id','wrong-zone','naive-time'])
def test_gcp_other_identity_and_property_checks_remain_strict(tmp_path,invalid):
    d,driver,trigger,described,item=template_case(tmp_path)
    if invalid=='extra-label': described['properties']['labels']['arbitrary']='true'
    elif invalid=='wrong-id': item['id']='112'
    elif invalid=='wrong-zone': item['zone']='europe-west3-c'
    else: described['properties']['scheduling']['terminationTime']=trigger.replace(tzinfo=None).isoformat()
    with pytest.raises(GcpProviderError): driver._validate_template(described,item,'vm_a',d,trigger)


@pytest.mark.parametrize('case', ['default','owned','neighbor','warning','malformed','foreign','truncated'])
def test_gcp_all_scope_inventory_never_suppresses_warning_or_default_resources(tmp_path,case):
    d,driver=_cleanup_fixture(tmp_path);namespace=driver._namespace(d)
    attempt=driver._attempt_dir(d);driver._save(attempt,{'run_id':d.run_id,'deployment_id':d.deployment_id})
    replies=[]
    for collection in ('instances','disks','addresses','subnetworks','instance-templates','networks','firewall-rules','routes','resource-policies'):
        value=[];err=''
        if collection=='addresses':
            value=[{'name':'default-address'}]
            if case=='owned': value=[{'name':namespace+'-address'}]
            if case=='neighbor': value=[{'name':namespace+'neighbor-address'}]
            if case=='warning': err='WARNING: invalid filter, incomplete result'
            if case=='malformed': value=[{}]
            if case=='foreign': value=[{'name':'default','selfLink':'projects/foreign-project/global/addresses/default'}]
            if case=='truncated': value={'items':[],'nextPageToken':'next'}
        replies.append((0,json.dumps(value),err))
    driver.runner=runner=Commands(replies)
    if case in {'warning','malformed','foreign','truncated'}:
        with pytest.raises(GcpProviderError): driver._inventory(d)
    else:
        result=driver._inventory(d)
        assert bool(result['resources']) == (case=='owned')
        assert result['pages_complete']
    assert all(not any(a.startswith(('--regions','--global')) for a in q.argv) for q in runner.requests)


@pytest.mark.parametrize('root', ['expiry','pair'])
@pytest.mark.parametrize('mutation', ['missing','automatic','explicit-list'])
def test_azure_saved_plan_cannot_reenable_subscription_registration(root,mutation):
    v,r=azure_scope(root);document=azure_document(v,r)
    expr=document['configuration']['provider_config']['azurerm']['expressions']
    if mutation=='missing': expr.pop('resource_provider_registrations')
    elif mutation=='automatic': expr['resource_provider_registrations']={'constant_value':'core'}
    else: expr['resource_providers_to_register']={'constant_value':['Microsoft.Storage']}
    with pytest.raises(tp.PlanReviewError): tp.validate_scope(document,'azure',ROOT/'terraform/azure'/root,v)


@pytest.mark.parametrize('case', ['registered','wrong-subscription','wrong-tenant','disabled','unregistered','missing','warning'])
def test_azure_preflight_reads_only_expected_already_registered_namespaces(tmp_path,case):
    o=options(tmp_path)
    account={'id':o.subscription_id,'tenantId':o.tenant_id,'state':'Enabled'}
    if case=='wrong-subscription': account['id']='foreign'
    if case=='wrong-tenant': account['tenantId']='foreign'
    if case=='disabled': account['state']='Disabled'
    replies=[(0,json.dumps(account),'')]
    for namespace in ('Microsoft.Compute','Microsoft.Network','Microsoft.Automation','Microsoft.Authorization'):
        value={'id':f'/subscriptions/{o.subscription_id}/providers/{namespace}','namespace':namespace,
            'registrationState':'NotRegistered' if case=='unregistered' else 'Registered'}
        replies.append((0,json.dumps({} if case=='missing' else value),'partial' if case=='warning' else ''))
    runner=Commands(replies)
    if case=='registered': az._validate_provider_registrations(runner,o,tmp_path,lambda:1.)
    else:
        with pytest.raises(RuntimeError): az._validate_provider_registrations(runner,o,tmp_path,lambda:1.)
    assert all('--subscription' in q.argv and q.argv[q.argv.index('--subscription')+1]==o.subscription_id for q in runner.requests)
    assert all(q.argv[1:3] in (['account','show'],['provider','show']) for q in runner.requests)
    assert len(runner.requests)==(5 if case=='registered' else 1 if case in {'wrong-subscription','wrong-tenant','disabled'} else 2)


@pytest.mark.parametrize('case', ['typed','json','no-parent-proof','wrong-parent','wrong-subscription','authorization','unrelated','blank'])
def test_azure_typed_parent_absence_is_bound_to_independently_proven_group(tmp_path,case):
    o=options(tmp_path);group='original-group'
    resource=f'/subscriptions/{o.subscription_id}/resourceGroups/{group}/providers/Microsoft.Compute/virtualMachines/vm-a'
    message=f"Resource group '{group}' could not be found."
    err='(ResourceGroupNotFound) '+message
    if case=='json': err=json.dumps({'error':{'code':'ResourceGroupNotFound','message':message}})
    if case=='authorization': err='(AuthorizationFailed) denied; (ResourceGroupNotFound) '+message
    if case=='unrelated': err="(ResourceGroupNotFound) Resource group 'other' could not be found."
    if case=='blank': err=''
    if case=='wrong-subscription': resource=resource.replace(o.subscription_id,'foreign')
    parent=None if case=='no-parent-proof' else 'other' if case=='wrong-parent' else group
    runner=Commands([(254,'',err)])
    if case in {'typed','json'}: az._prove_vm_absent(runner,o,tmp_path,resource,lambda:1.,absent_parent_group=parent)
    else:
        with pytest.raises((RuntimeError,ValueError)): az._prove_vm_absent(runner,o,tmp_path,resource,lambda:1.,absent_parent_group=parent)


@pytest.mark.parametrize('case', ['foreign-id','foreign-next','blank','warning','truncated'])
def test_azure_subscription_inventory_must_be_complete_and_scoped(tmp_path,case):
    o=options(tmp_path);value={'value':[]};err=''
    if case=='foreign-id': value['value']=[{'id':'/subscriptions/foreign/resourceGroups/g/providers/Microsoft.Network/networkInterfaces/x','type':'Microsoft.Network/networkInterfaces'}]
    if case=='foreign-next': value['nextLink']='https://management.azure.com/subscriptions/'+o.subscription_id+'/resourcesForeign?api-version=x'
    if case=='warning': err='partial result'
    if case=='truncated': value.pop('value')
    runner=Commands([(0,'' if case=='blank' else json.dumps(value),err)])
    with pytest.raises(RuntimeError): az._read_arm_collection(runner,o,tmp_path,
        f'/subscriptions/{o.subscription_id}/resources?api-version=2021-04-01','pair_inventory',lambda:1.,('id','type'))


@pytest.mark.parametrize('cleanup_case', ['absent','default-other-group','wrong-subscription','authorization','wrong-parent','parent-present','blank-inventory','partial-inventory','owned-resource','residual-role'])
def test_azure_real_factory_plan_timeout_then_typed_precreation_cleanup_retains_liability(tmp_path,cleanup_case):
    from tests.integration.test_admission_factory_recovery import prepared, build, execute
    fixture=prepared(tmp_path,'azure',support='0.04',command_timeout_seconds=180)
    sub=fixture.runtime['providers']['azure']['options']['subscription_id']
    tenant=fixture.runtime['providers']['azure']['options']['tenant_id']
    class Precreation:
        def __init__(self): self.requests=[];self.plan_failed=False;self.group=None
        def run_bounded_copy(self,*args): raise AssertionError('no benchmark outputs before VM creation')
        def run(self,q):
            self.requests.append(q);code=0;out='';err='';outcome='succeeded';duration=0
            if q.action_type=='subscription_preflight': out=json.dumps({'id':'foreign' if self.plan_failed and cleanup_case=='wrong-subscription' else sub,'tenantId':tenant,'state':'Enabled'})
            elif q.action_type=='provider_registration_preflight':
                ns=q.argv[q.argv.index('--namespace')+1]
                out=json.dumps({'id':f'/subscriptions/{sub}/providers/{ns}','namespace':ns,'registrationState':'Registered'})
            elif 'plan' in q.argv:
                assert 179<q.timeout_seconds<=180
                outcome='timed_out';code=None;err='synthetic saved-plan timeout';duration=180;self.plan_failed=True
            elif q.action_type in {'parent_group_absence','expiry_inventory'}: out='true' if cleanup_case=='parent-present' and q.action_type=='parent_group_absence' else 'false'
            elif q.action_type=='vm_absence_by_id':
                group=q.argv[q.argv.index('--ids')+1].split('/resourceGroups/')[1].split('/')[0]
                self.group=group
                if cleanup_case=='wrong-parent': group='foreign-group'
                code=254;outcome='failed';err=f"(ResourceGroupNotFound) Resource group '{group}' could not be found."
                if cleanup_case=='authorization': err='(AuthorizationFailed) denied; '+err
            elif q.action_type.startswith('pair_inventory'):
                value={'value':[]}
                if cleanup_case in {'owned-resource','default-other-group'}:
                    group=self.group if cleanup_case=='owned-resource' else 'unrelated-default-group'
                    value['value']=[{'id':f'/subscriptions/{sub}/resourceGroups/{group}/providers/Microsoft.Network/networkInterfaces/leftover','type':'Microsoft.Network/networkInterfaces'}]
                if cleanup_case=='partial-inventory': value['nextLink']='https://management.azure.com/foreign/resources?api-version=x'
                out='' if cleanup_case=='blank-inventory' else json.dumps(value)
            elif q.action_type in {'role_cleanup_inventory','role_definition_cleanup_inventory'}: out='[{"id":"residual-role"}]' if cleanup_case=='residual-role' else '[]'
            return CommandResult(action_id=q.action_id,outcome=outcome,started_at=NOW,finished_at=NOW+timedelta(seconds=duration),
                duration_seconds=duration,exit_code=code,stdout=out,stderr=err)
    runner=Precreation();built=build(fixture,runner)
    result=execute(built,fixture.clock)
    assert result.admission_status=='reserved' and 'Terraform saved-plan creation failed' in result.primary_failure
    success=cleanup_case in {'absent','default-other-group'}
    assert (result.provider_results['azure'].cleanup_state=='succeeded') == success,str(result.provider_results['azure'])
    assert sum('plan' in q.argv for q in runner.requests)==1
    assert not any('apply' in q.argv or 'register' in q.argv for q in runner.requests)
    state=built['ledger'].inspect()['reservations'][result.reservation_evidence.reservation_id.value]
    assert state['cleanup_open'] == (not success) and float(state['reserved_cost_usd'])>0
    assert float(state.get('released_usd',0))==0 and float(state['financial']['gross_eur'])>0
    if success: assert any(q.action_type=='pair_inventory' and '/resourceGroups/' not in q.argv[q.argv.index('--url')+1] for q in runner.requests)


@pytest.mark.parametrize('case', ['rejected','old-uncertainty','tampered-parameters','incomplete-lookup','unrecorded-exception'])
def test_aws_rejected_start_cleanup_after_expiry_rechecks_bound_absence(tmp_path,case):
    from tests.integration.test_pre_vm_failure_recovery import direct_attempt
    d,settings,driver,original,attempt=direct_attempt(tmp_path)
    journal=json.loads((attempt/'attempt.json').read_text())
    region='eu-central-1'
    safety={**driver._prospective_safety(d,region,attempt,journal),'document_version':'1'}
    trigger=d.reservation.value.expires_at.value-timedelta(seconds=240)
    params=driver._automation_parameters(d,region,trigger,safety['role_arn'])
    runner=Commands([(254,'',REJECT),(0,'{"AutomationExecutionMetadataList":[]}','')])
    driver.runner=runner
    with pytest.raises(AwsProviderError) as raised:
        driver._start_or_recover_automation(region,safety,params,'original-token')
    journal.update(registration_attempted_regions=[region],safety={region:safety},
        expiry_trigger=trigger.isoformat(),tokens={region:'original-token'})
    if case!='old-uncertainty': journal['registration_rejections']={region:raised.value.rejection_evidence}
    if case=='tampered-parameters': journal['registration_rejections'][region]['parameters_sha256']='f'*64
    if case=='unrecorded-exception': journal.pop('registration_rejections')
    driver._save_journal(attempt,journal)
    originals={p:p.read_bytes() for p in attempt.glob('*.tfvars.json')}
    class Cleanup:
        def __init__(self): self.requests=[]
        def run(self,q):
            self.requests.append(q)
            if 'describe-automation-executions' in q.argv:
                if case=='incomplete-lookup': code,out,err=254,'','AccessDenied'
                else: code,out,err=0,'{"AutomationExecutionMetadataList":[]}',''
                return CommandResult(action_id=q.action_id,outcome='failed' if code else 'succeeded',
                    started_at=NOW,finished_at=NOW,duration_seconds=0,exit_code=code,stdout=out,stderr=err)
            return original.run(q)
    driver.runner=cleanup=Cleanup()
    # Generic exception before an execution identity is saved must not roll back
    # support on the assumption that no Start reached AWS.
    if case=='unrecorded-exception':
        driver._cleanup_unused_protection(d,attempt,journal)
        assert cleanup.requests==[]
    result=driver.cleanup(d)
    assert (result.cleanup_state=='succeeded') == (case=='rejected'),result
    assert all(p.read_bytes()==value for p,value in originals.items())
    assert not any('start-automation-execution' in q.argv or 'apply' in q.argv for q in cleanup.requests)
    if case!='rejected': assert not any('destroy' in q.argv for q in cleanup.requests)
