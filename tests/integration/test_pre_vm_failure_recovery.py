"""Real orchestration/projection with source-shaped injected commands; no cloud I/O."""
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json

import pytest

from cloud_network_benchmark.providers.aws import AwsProviderDriver, _namespace, _legacy_namespace
from cloud_network_benchmark.contracts.common import Evidence, FailureEvidence, Provider
from cloud_network_benchmark.contracts.admission import ExpiryRequest
from cloud_network_benchmark.contracts.execution import CommandResult
from cloud_network_benchmark.manifests import initialize_campaign, ChildRunManifest
from cloud_network_benchmark.lifecycle_runtime import build_runtime, load_initialized_campaign, build_admission_request
from cloud_network_benchmark.lifecycle_projection import finalize_production_batch
from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.admission import load_cost_inputs
from cloud_network_benchmark.orchestration import run_lifecycle
from tests.unit.test_aws_provider import make_deployment, make_settings, NOW, ROOT
from tests.integration.test_f09_aws_runtime_factory import _runtime, _cost_source, FakeClock

COLLECTIONS = dict(zip(('describe-instances','describe-volumes','describe-network-interfaces','describe-security-groups',
    'describe-subnets','describe-route-tables','describe-internet-gateways','describe-vpcs','describe-vpc-peering-connections',
    'describe-placement-groups','describe-key-pairs','describe-addresses'),('Reservations','Volumes','NetworkInterfaces',
    'SecurityGroups','Subnets','RouteTables','InternetGateways','Vpcs','VpcPeeringConnections','PlacementGroups','KeyPairs','Addresses')))
REAL_RUN = '20261114T125059Z-exp-900-91f75a46-aws'


class PreVmCommands:
    def __init__(self, issue=None):self.requests=[];self.issue=issue
    def run(self,q):
        self.requests.append(q);code=0;out='{}';err=''
        if 'plan' in q.argv:
            code=1;err='injected real-shaped Terraform plan rejection before any apply'
        elif q.argv[1:3]==['sts','get-caller-identity']:
            out=json.dumps({'Account':'999999999999' if self.issue=='wrong_account' else '123456789012'})
        elif len(q.argv)>2 and q.argv[1]=='ec2':
            operation=q.argv[2];out=json.dumps({COLLECTIONS[operation]:[]})
            if operation=='describe-volumes' and self.issue=='truncated':out=json.dumps({'Volumes':[],'NextToken':'not-complete'})
            if operation=='describe-volumes' and self.issue=='ec2_denied':code=254;err='An error occurred (AccessDenied) when calling DescribeVolumes'
        elif q.argv[1:3]==['ssm','describe-document']:
            code=254;err='An error occurred ('+('AccessDenied' if self.issue=='document_denied' else 'InvalidDocument')+') when calling DescribeDocument'
        elif q.argv[1:3]==['iam','get-role']:
            if self.issue=='role_present':out='{"Role":{"RoleName":"present"}}'
            else:code=254;err='An error occurred (NoSuchEntity) when calling GetRole'
        return CommandResult(action_id=q.action_id,outcome='failed' if code else 'succeeded',started_at=NOW,
            finished_at=NOW,duration_seconds=0,exit_code=code,stdout=out,stderr=err)
    def run_bounded_copy(self,*_):raise AssertionError('failure before expiry must never copy guest outputs')


def direct_attempt(tmp_path, *, legacy=False, issue=None):
    base=make_deployment();campaign_id=REAL_RUN.removesuffix('-aws')
    reservation=base.reservation.value.model_copy(update={'campaign_id':campaign_id})
    d=base.model_copy(update={'campaign_id':campaign_id,'run_id':REAL_RUN,'deployment_id':REAL_RUN+'-deployment',
        'observation_id':REAL_RUN+'-observation','reservation':type(base.reservation)(value=reservation)})
    settings=make_settings(tmp_path,deployment=d);runner=PreVmCommands(issue)
    driver=AwsProviderDriver(runner,settings,clock=lambda:NOW+timedelta(days=2))
    namespace=_legacy_namespace(d) if legacy else _namespace(d)
    attempt=settings.private_state_root/namespace/d.run_id/d.deployment_id;attempt.mkdir(parents=True,mode=0o700)
    values=dict(region='eu-central-1',account_id=settings.account_id,resource_namespace=namespace,run_id=d.run_id,deployment_id=d.deployment_id)
    driver._write_private(attempt/'safety-eu-central-1.tfvars.json',json.dumps(values).encode())
    (attempt/'safety-eu-central-1.tfstate').write_text('{"serial":1,"resources":[]}')
    scope=driver._deployment_scope(d);scope['resource_namespace']=namespace
    cause=FailureEvidence(category='terraform_plan_review',message='original plan failure',occurred_at=NOW)
    state=dict(schema_version=1,run_id=d.run_id,deployment_id=d.deployment_id,regions=['eu-central-1'],
        deployment_scope=scope,creation_may_have_started=False,executions={},safety={},registration_failure=cause.model_dump(mode='json'))
    if issue=='uncertain_execution':state.update(registration_attempted_regions=['eu-central-1'],registration_uncertain_regions=['eu-central-1'])
    driver._save_journal(attempt,state)
    return d,settings,driver,runner,attempt


def test_generated_init_ids_use_one_bounded_namespace_in_admission_and_provider(tmp_path):
    config=ROOT/'configs/tests/exp-900-single-provider-north.yaml'
    init=initialize_campaign(config,ROOT,tmp_path/'results',clock=lambda:NOW,token_source=lambda:'91f75a46')
    parent,children=load_initialized_campaign(Path(init.parent_manifest_path),repository_root=ROOT,config_path=config)
    child=children['aws'];assert len(_legacy_namespace(child))==90
    assert len('cnb-'+REAL_RUN+'-'+REAL_RUN+'-deployment')==90
    assert len(_namespace(child))<=80 and _namespace(child)==_namespace(child)
    assert _namespace(child)!=_namespace(SimpleNamespace(run_id=child.run_id,deployment_id=child.deployment_id+'-different'))
    cost=tmp_path/'cost.json';_cost_source(cost,child.run_id,child.resolved_observation.admission.cost_input_id,region='eu-north-1')
    costs=load_cost_inputs(cost);runtime=_runtime(tmp_path,ROOT,cost,Path(init.parent_manifest_path),costs.payload_sha256,region='eu-north-1')
    campaign,_=resolve_campaign(config,ROOT)
    _request,plan=build_admission_request(campaign=campaign,parent=parent,children=children,costs=costs,runtime=runtime,
        controller_session_id='synthetic')
    assert plan.namespaces['aws']==_namespace(child)


@pytest.mark.parametrize('issue',[None,'partial_state','wrong_account','truncated','ec2_denied','document_denied','role_present','uncertain_execution','vars_tamper'])
def test_pre_vm_cleanup_requires_independent_original_scope_absence(tmp_path,issue):
    d,settings,driver,runner,attempt=direct_attempt(tmp_path,issue=issue)
    if issue=='partial_state':(attempt/'safety-eu-central-1.tfstate').write_text('{"resources":[{"type":"aws_iam_role"}]}')
    if issue=='vars_tamper':
        path=attempt/'safety-eu-central-1.tfvars.json';value=json.loads(path.read_text());value['account_id']='999999999999';path.write_text(json.dumps(value))
    original=(attempt/'safety-eu-central-1.tfvars.json').read_bytes()
    result=driver.cleanup(d)
    assert result.primary_failure.category=='terraform_plan_review'
    assert (attempt/'safety-eu-central-1.tfvars.json').read_bytes()==original
    assert not any('apply' in q.argv or 'plan' in q.argv for q in runner.requests)
    if issue in (None,'partial_state'):
        assert result.cleanup_state=='succeeded',result.model_dump(mode='json')
        inventory=result.inventory.value
        assert inventory['regions']['eu-central-1']['complete'] and not inventory['regions']['eu-central-1']['remaining']
        assert len([q for q in runner.requests if len(q.argv)>1 and q.argv[1]=='ec2'])==12
        assert inventory['safety_absent']=={'eu-central-1':True}
        targets=inventory['safety_targets']['eu-central-1'];assert targets['role']['arn'].startswith('arn:aws:iam::123456789012:role/cnb-expiry-')
        values=json.loads(original);digest=hashlib.sha256((d.run_id+':'+d.deployment_id+':eu-central-1').encode()).hexdigest()[:24]
        name='cnb-expiry-'+values['resource_namespace'].replace('.','-')[:24]+'-'+digest
        assert targets['role']['arn']=='arn:aws:iam::123456789012:role/'+name
        assert targets['document']['arn']=='arn:aws:ssm:eu-central-1:123456789012:document/'+name
        for q in runner.requests:
            if len(q.argv)>1 and q.argv[1]=='ec2':
                assert 'Name=tag:cnb_run_id,Values='+d.run_id in q.argv
                assert 'Name=tag:cnb_deployment_id,Values='+d.deployment_id in q.argv
                assert q.argv[q.argv.index('--region')+1]=='eu-central-1'
        assert any('--role-name' in q.argv and q.argv[q.argv.index('--role-name')+1]==name for q in runner.requests)
        assert any('--name' in q.argv and q.argv[q.argv.index('--name')+1]==name for q in runner.requests)
        assert any('destroy' in q.argv for q in runner.requests)
        assert json.loads((attempt/'attempt.json').read_text())['last_cleanup_inventory']
    else:
        assert result.cleanup_state=='failed' and json.loads((attempt/'attempt.json').read_text())['launch_blocked']
        assert (attempt/'attempt.json').exists()
        if issue in ('wrong_account','uncertain_execution','vars_tamper'):assert not any('destroy' in q.argv for q in runner.requests)


def test_legacy_saved_cleanup_keeps_original_namespace_inputs_state_and_request(tmp_path):
    from cloud_network_benchmark.lifecycle_cleanup import save_cleanup_settings,cleanup_saved_provider
    from cloud_network_benchmark.providers.aws import SAFETY_LEAD_SECONDS
    d,settings,driver,runner,attempt=direct_attempt(tmp_path,legacy=True)
    original={p:p.read_bytes() for p in (attempt/'safety-eu-central-1.tfvars.json',attempt/'safety-eu-central-1.tfstate')}
    request=ExpiryRequest(schema_version=2,run_id=d.run_id,deployment_id=d.deployment_id,provider='aws',action='stop',
        resource_namespace=_legacy_namespace(d),target_selector=driver._target_selector(d,['eu-central-1']),
        expires_at=Evidence(value=d.reservation.value.expires_at.value-timedelta(seconds=SAFETY_LEAD_SECONDS)),reservation_id=d.reservation.value.reservation_id)
    saved=save_cleanup_settings(provider='aws',deployment=d,request=request,reservation=d.reservation.value,
        provider_lead_seconds=float(SAFETY_LEAD_SECONDS),settings=settings,private_root=tmp_path/'recovery')
    record=SimpleNamespace(provider='aws',deployment_id=d.deployment_id,journal_reference=str(attempt/'attempt.json'),cleanup_settings_reference=str(saved))
    result,retained,_=cleanup_saved_provider(record,runner=runner,utc_now=lambda:NOW+timedelta(days=2))
    assert result.cleanup_state=='succeeded',result.model_dump(mode='json')
    assert retained.expiry_request==request
    assert not (settings.private_state_root/_namespace(d)).exists()
    for path,raw in original.items():assert path.read_bytes()==raw
    assert all(str(attempt) in str(q.cwd) for q in runner.requests if 'destroy' in q.argv)
    assert any('-state='+str(attempt/'safety-eu-central-1.tfstate') in q.argv for q in runner.requests)


def prepared_runtime(tmp_path):
    config=ROOT/'configs/tests/exp-900-single-provider.yaml'
    init=initialize_campaign(config,ROOT,tmp_path/'results',clock=lambda:NOW,token_source=lambda:'91f75a46')
    parent_path=Path(init.parent_manifest_path)
    parent,children=load_initialized_campaign(parent_path,repository_root=ROOT,config_path=config)
    campaign,_=resolve_campaign(config,ROOT)
    cost=tmp_path/'cost.json';_cost_source(cost,children['aws'].run_id,children['aws'].resolved_observation.admission.cost_input_id)
    costs=load_cost_inputs(cost);runtime=_runtime(tmp_path,ROOT,cost,parent_path,costs.payload_sha256)
    return config,parent_path,parent,children,campaign,cost,costs,runtime


def execute_failure(tmp_path):
    config,parent_path,parent,children,campaign,cost,costs,runtime=prepared_runtime(tmp_path)
    runner=PreVmCommands();clock=FakeClock()
    built=build_runtime(campaign=campaign,parent=parent,children=children,costs=costs,runtime=runtime,
        controller_session_id='offline-failure',runner=runner,clock=clock,private_root=tmp_path/'private',repository_root=ROOT)
    lifecycle=run_lifecycle(ledger=built['ledger'],reservation_request=built['request'],admission_plan=built['plan'],
        allowance=built['allowance'],workflow_factory=built['workflow_factory'],clock=clock,
        recovery_path=built['recovery_path'],recovery_factory=built['recovery_factory'])
    return parent_path,parent,children,runner,built,lifecycle


def test_real_orchestration_finalizes_failure_before_expiry_and_reconciles_absence(tmp_path,isolated_legacy_provider_financial_boundary):
    parent_path,parent,children,runner,built,lifecycle=execute_failure(tmp_path)
    assert not lifecycle.completed and lifecycle.admission_status=='reserved',lifecycle.admission_reasons
    result=lifecycle.provider_results['aws'];assert result.execution_state=='failed' and result.cleanup_state=='succeeded',result
    assert result.values['protect'].failure.category=='terraform_plan_review'
    recovery=json.loads(built['recovery_path'].read_text())
    assert Path(recovery['providers'][0]['journal_reference']).parts[-4]==_namespace(children['aws'])
    assert built['plan'].namespaces['aws']==_namespace(children['aws'])
    assert 'create' not in result.stage_times and not result.creation_may_have_started
    projected=finalize_production_batch(parent_path=parent_path,parent=parent,children=children,lifecycle=lifecycle,
        repository_root=ROOT,evaluated_at=NOW+timedelta(minutes=2))
    childpath=ROOT/parent.child_runs[0].manifest_path
    child=ChildRunManifest.model_validate_json(childpath.read_text())
    assert child.execution_state=='failed' and child.failure.category=='terraform_plan_review'
    assert child.actual_started_at is None and not child.commands
    assert all(t.actual_start_utc.value is None and t.actual_end_utc.value is None for t in child.timings)
    assert projected['aws'][0].validation_status.data_validity!='valid'
    state=built['ledger'].inspect();record=state['reservations'][lifecycle.reservation_evidence.reservation_id.value]
    assert record['cleanup_open'] is False and record['provider_cleanup']['aws'] is True
    assert record['evidence']['reserved_cost_usd']['value']>0
    assert float(record.get('released_usd',0))==0 and record.get('billing_final',False) is False
    plan_logs=list((tmp_path/'private').rglob('plan-result.json'))
    assert len(plan_logs)==1 and plan_logs[0].stat().st_mode & 0o077==0
    assert json.loads(plan_logs[0].read_text())['exit_code']==1
    assert 'injected real-shaped Terraform plan rejection' in json.loads(plan_logs[0].read_text())['stderr']
    assert not any('apply' in q.argv for q in runner.requests)


@pytest.mark.parametrize('projection_error',[False,True])
def test_cli_counts_real_dispatched_commands_and_retains_provider_failure(tmp_path,monkeypatch,capsys,isolated_legacy_provider_financial_boundary,projection_error):
    from cloud_network_benchmark import lifecycle_cli as cli, lifecycle_runtime as rt, lifecycle_projection as projection
    from cloud_network_benchmark.contracts import execution
    config,parent_path,parent,children,campaign,cost,costs,runtime=prepared_runtime(tmp_path)
    runtime_file=tmp_path/'runtime.json';runtime_file.write_text('{}')
    commands=PreVmCommands()
    monkeypatch.setattr(cli,'load_runtime_config',lambda *_a,**_k:runtime)
    monkeypatch.setattr(execution,'SubprocessCommandRunner',lambda:commands)
    if projection_error:
        monkeypatch.setattr(projection,'finalize_production_batch',lambda **_:(_ for _ in ()).throw(ValueError('injected post-cleanup persist failure')))
    code=cli.main(['run','--config',str(config),'--cost-inputs',str(cost),'--runtime-config',str(runtime_file),
        '--results-root',str(tmp_path/'results'),'--private-root',str(tmp_path/'private'),'--controller-session','offline-cli',
        '--execution-scope','paid_request','--format','json'],run_clock=FakeClock())
    captured=capsys.readouterr();report=json.loads(captured.err)
    assert code==1 and report['status']=='failed',report
    assert report['external_calls']==len(commands.requests)>0
    assert report['run_ids']['aws']==children['aws'].run_id
    assert 'terraform_plan_review' in report['providers']['aws']['primary_failure']
    assert 'invalid_lifecycle_request' not in report['reason_codes']
    assert any('plan' in q.argv for q in commands.requests) and any('destroy' in q.argv for q in commands.requests)
    if projection_error:
        assert report['finalization_failure']['category']=='post_cleanup_validation_failed'
        assert 'post_cleanup_validation_failed' in report['reason_codes']


def test_missing_original_journal_cannot_switch_to_a_new_namespace(tmp_path):
    from cloud_network_benchmark.lifecycle_cleanup import save_cleanup_settings,cleanup_saved_provider
    from cloud_network_benchmark.providers.aws import SAFETY_LEAD_SECONDS
    d,settings,driver,runner,attempt=direct_attempt(tmp_path,legacy=True)
    request=ExpiryRequest(schema_version=2,run_id=d.run_id,deployment_id=d.deployment_id,provider='aws',action='stop',
        resource_namespace=_legacy_namespace(d),target_selector=driver._target_selector(d,['eu-central-1']),
        expires_at=Evidence(value=d.reservation.value.expires_at.value-timedelta(seconds=SAFETY_LEAD_SECONDS)),reservation_id=d.reservation.value.reservation_id)
    saved=save_cleanup_settings(provider='aws',deployment=d,request=request,reservation=d.reservation.value,
        provider_lead_seconds=float(SAFETY_LEAD_SECONDS),settings=settings,private_root=tmp_path/'recovery')
    journal=attempt/'attempt.json';journal.unlink()
    record=SimpleNamespace(provider='aws',deployment_id=d.deployment_id,journal_reference=str(journal),cleanup_settings_reference=str(saved))
    with pytest.raises(ValueError,match='original AWS recovery journal'):
        cleanup_saved_provider(record,runner=runner)
    assert not runner.requests and not (settings.private_state_root/_namespace(d)).exists()


def test_ambiguous_old_and_new_journals_fail_before_commands(tmp_path):
    from cloud_network_benchmark.providers.aws import AwsProviderError
    d,settings,driver,runner,old=direct_attempt(tmp_path,legacy=True)
    new=settings.private_state_root/_namespace(d)/d.run_id/d.deployment_id;new.mkdir(parents=True)
    (new/'attempt.json').write_bytes((old/'attempt.json').read_bytes())
    with pytest.raises(AwsProviderError,match='ambiguous'):driver.cleanup(d)
    assert not runner.requests


def test_maximum_legal_identity_components_fit_new_state_path(tmp_path):
    d=make_deployment();run='r'*176+'-aws'
    d=d.model_copy(update={'run_id':run,'deployment_id':run+'-deployment'})
    driver=AwsProviderDriver(PreVmCommands(),make_settings(tmp_path,deployment=d),clock=lambda:NOW)
    assert len(_legacy_namespace(d))>255
    path=driver._attempt_dir(d)
    assert len(path.parent.parent.name)<=80 and path.name==d.deployment_id


def test_precreation_aws_absence_rejects_wrong_sts_account_before_inventory(tmp_path):
    from cloud_network_benchmark.providers.aws import AwsProviderError
    deployment, settings, driver, runner, _ = direct_attempt(tmp_path, legacy=True)
    driver._aws_json = lambda *_args: {"Account": "000000000000"}
    with pytest.raises(AwsProviderError, match="caller account differs"):
        driver.verify_unstarted_absence(deployment)
    assert not runner.requests


def test_precreation_receipt_producer_rejects_wrong_frozen_selector(tmp_path):
    from types import SimpleNamespace
    from cloud_network_benchmark.precreation_cleanup import _persist_absence_receipt

    record = SimpleNamespace(provider="aws", cleanup_settings_reference=str(tmp_path / "f09-cleanup" / "aws" / "run-deployment.json"))
    recovery = SimpleNamespace(reservation_id="reservation-1")
    binding = {"target_selectors": {"aws": "tag:deployment=expected"}}
    with pytest.raises(ValueError, match="selector differs from frozen target"):
        _persist_absence_receipt(record, recovery=recovery, binding=binding,
            proof={"provider": "aws", "verified": True,
                   "target_selector": "tag:deployment=foreign"}, utc_now=lambda: NOW)
    assert not tmp_path.joinpath("f09-cleanup").exists()


def test_precreation_source_config_uses_verified_repository_source_map(tmp_path):
    import hashlib
    from cloud_network_benchmark.precreation_cleanup import _verify_entry_paths

    root = tmp_path / "repo"
    source = root / "configs" / "source.yaml"
    source.parent.mkdir(parents=True)
    source.write_text("source: frozen\n")
    generated = {}
    for name, content in (("config", "generated config\n"), ("runtime", "runtime\n"),
                          ("parent", "manifest\n"), ("cost", "cost\n")):
        path = tmp_path / f"{name}.json"
        path.write_text(content)
        generated[name] = (path, hashlib.sha256(path.read_bytes()).hexdigest())
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    entry = {"config_path": str(generated["config"][0]), "config_sha256": generated["config"][1],
        "runtime_config_path": str(generated["runtime"][0]), "runtime_config_sha256": generated["runtime"][1],
        "parent_manifest_path": str(generated["parent"][0]),
        "source_config_path": str(source), "source_config_sha256": source_hash,
        "cost_inputs_path": str(generated["cost"][0])}
    artifacts = {str(path): digest for path, digest in generated.values()}
    stage = {"repository_root": str(root), "entries": [entry]}
    draft = {"artifacts": artifacts,
        "repository_sources": {str(root): {"configs/source.yaml": source_hash}}, "stages": [stage]}
    paths = {"config_path": generated["config"][0], "runtime_path": generated["runtime"][0],
        "parent_manifest_path": generated["parent"][0]}
    assert _verify_entry_paths(draft=draft, entry=entry, **paths)["source_config"] == source_hash
    draft["repository_sources"][str(root)]["configs/source.yaml"] = "0" * 64
    with pytest.raises(ValueError, match="repository source map omits"):
        _verify_entry_paths(draft=draft, entry=entry, **paths)


@pytest.mark.parametrize('tamper',[False,True])
def test_pre_vm_cleanup_receipt_replays_only_original_support_targets(tmp_path,isolated_legacy_provider_financial_boundary,tamper):
    from cloud_network_benchmark.lifecycle_cleanup import replay_saved_cleanup_receipt
    from cloud_network_benchmark.orchestration import SavedLifecycleRecovery
    _,_,_,_,built,result=execute_failure(tmp_path)
    recovery=SavedLifecycleRecovery.model_validate_json(built['recovery_path'].read_text())
    record=recovery.providers[0]
    journal=Path(record.journal_reference)
    original=journal.read_bytes();state=json.loads(original)
    assert state['cleanup_safety_targets']
    if tamper:
        region=next(iter(state['cleanup_safety_targets']))
        state['cleanup_safety_targets'][region]['role']['arn']+='-foreign'
        journal.write_text(json.dumps(state))
    before=built['ledger'].path.read_bytes()
    assert replay_saved_cleanup_receipt(record,recovery=recovery,ledger=built['ledger']) is (not tamper)
    assert built['ledger'].path.read_bytes()==before
