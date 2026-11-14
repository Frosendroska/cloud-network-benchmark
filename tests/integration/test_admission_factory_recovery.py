"""Source-shaped admission/factory regressions with real financial guards; no cloud I/O."""
import copy
import json
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from cloud_network_benchmark.admission import BudgetLedger, WorkflowAllowance, load_cost_inputs
from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.financial_controls import financial_binding
from cloud_network_benchmark.lifecycle_runtime import build_admission_request, build_runtime, load_initialized_campaign
from cloud_network_benchmark.manifests import initialize_campaign, ChildRunManifest
from cloud_network_benchmark.orchestration import run_lifecycle, SavedLifecycleRecovery
from tests.financial_helpers import synthetic_finances, save
from tests.integration.test_f09_multi_provider_factory import _costs

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE9F4huSqss0wx3n6f5+WJiEpePM3oXTbuOzBLRR5NCc synthetic"


class SequentialClock:
    """Every UTC/monotonic read advances the same underlying clock."""
    def __init__(self): self.elapsed = 0.0
    def utc_now(self):
        self.elapsed += .0001
        return NOW + timedelta(seconds=self.elapsed)
    def monotonic(self):
        self.elapsed += .0001
        return 100.0 + self.elapsed


class NoProviderCalls:
    def __init__(self): self.requests = []
    def run(self, request):
        self.requests.append(request)
        raise AssertionError("factory must not contact providers")
    def run_bounded_copy(self, *_args): raise AssertionError("no result transfer")


def prepared(tmp_path, provider, *, support="0", clock=None, command_timeout_seconds=60):
    """Normal initialized IDs, source-bound runtime/price/FX/card and real ledger."""
    clock = clock or SequentialClock()
    config = ROOT / "configs/tests" / ("exp-902-gcp-single-provider-north.yaml" if provider == "gcp"
        else "exp-901-azure-single-provider.yaml")
    campaign, _ = resolve_campaign(config, ROOT)
    init = initialize_campaign(config, ROOT, tmp_path / "results", clock=lambda: NOW,
        token_source=lambda: "42ad621d")
    parent_path = Path(init.parent_manifest_path)
    parent, children = load_initialized_campaign(parent_path, repository_root=ROOT, config_path=config)
    cost_path = tmp_path / "SYNTHETIC-prices.json"
    _costs(cost_path, campaign, children)
    payload = json.loads(cost_path.read_text())
    row = payload["provider_run_envelopes"][provider]
    row["components"]["expiry_cleanup_support"].update(amount_usd=support,
        inclusion_rule="native STOP instance templates; no separate support charge" if provider == "gcp"
        else "bounded Azure Automation job")
    row["priced_on"] = payload["prices_checked_on"] = payload["acquired_on"] = "2026-11-14"
    save(cost_path, payload)
    costs = load_cost_inputs(cost_path)
    pub = tmp_path / "SYNTHETIC.pub"; pub.write_text(KEY)
    expiry = tmp_path / "SYNTHETIC-azure-expiry.json"
    save(expiry, {"source_id":"SYNTHETIC", "date":"2026-11-14", "sha256":"a"*64,
        "automation_rate_per_job_minute":.002, "job_minutes_bound":20})
    from cloud_network_benchmark.access import TimeoutPolicy
    from cloud_network_benchmark.readiness import ReadinessConfig
    runtime = {"initialized_campaign_manifest":parent_path,
        "runtime_source":save(tmp_path / "SYNTHETIC-runtime-source.json", {"fixture":"never authorize"}),
        "ledger":{"path":tmp_path / "SYNTHETIC-ledger.json", "scope_id":"SYNTHETIC-OFFLINE", "initialize_if_missing":True},
        "allowance":WorkflowAllowance(10,30,30,60,80,5,10,10,"SYNTHETIC",{provider:30 if provider == "gcp" else 360}),
        "copy_retry_seconds":1, "approval_evidence":{},
        "access":{"timeouts":TimeoutPolicy(ssh_seconds=5,command_seconds=10,cloud_init_seconds=10,
            private_path_seconds=5,server_seconds=5,transfer_seconds=10),
            "readiness":ReadinessConfig(poll_interval_seconds=.01,attempt_ceiling=1),
            "guest_remote_root":"/var/lib/cnb/runtime", "diagnostic_interfaces":{provider:{"vm_a":"eth0","vm_b":"eth0"}}},
        "providers":{provider:{"options":{}, "sources":{},
            "bootstrap_template":ROOT / f"terraform/{provider}/pair/main.tf",
            "terraform_directory":ROOT / f"terraform/{provider}"}}}
    options = runtime["providers"][provider]
    if provider == "azure":
        options["options"] = {"subscription_id":"00000000-0000-0000-0000-000000000001",
            "tenant_id":"00000000-0000-0000-0000-000000000002", "controller_cidr":"203.0.113.4/32",
            "selected_image":"Canonical:0001-com-ubuntu-server-noble:24_04-lts:2026.10.01",
            "ssh_public_key_path":pub, "authentication_reference":"SYNTHETIC-no-credential",
            "poll_seconds":15,"command_timeout_seconds":command_timeout_seconds}
        options["sources"] = {"expiry_cost_path":expiry,"primary_review_path":None}
    else:
        options["sources"] = {"workflow_cost_handoff_path":tmp_path / "SYNTHETIC-gcp-handoff.json",
            "support_cost_path":ROOT / "tests/fixtures/gcp/zero-support-cost-2026-10-23.json", "primary_review_path":None}
        save(options["sources"]["workflow_cost_handoff_path"], {"synthetic":"not used before provider factory"})
    req, plan = build_admission_request(campaign=campaign,parent=parent,children=children,costs=costs,
        runtime=runtime,controller_session_id="SYNTHETIC")
    donor=tmp_path / "donor"; donor.mkdir()
    _, _, card, cardpath = synthetic_finances(donor,paid=True)
    until = (NOW + timedelta(days=1)).isoformat()
    checks = {"checked_at":NOW.isoformat(),"valid_until":until,
        "official_urls":["https://example.invalid/SYNTHETIC-NONAUTHORIZING"]}
    fx = {**checks,"currency":"USD","budget_currency":"EUR","conservative_eur_per_unit":"1.01"}
    card.update(approved_at=NOW.isoformat(),valid_from=NOW.isoformat(),valid_until=until,
        approved_run_count=1,entries=[financial_binding(plan,req)])
    card["caps"]["providers_eur"] = {provider:"75"}
    card["price"] = {**save(cost_path,payload),**checks}
    card["fx"] = {**save(donor / "SYNTHETIC-fx.json",fx),**fx}
    card["external_liabilities"].update(checks)
    ref=save(cardpath,card)
    runtime["approval_evidence"]={k:{"evidence_id":"SYNTHETIC-NONAUTHORIZING",
        "source_path":ref["path"],"source_sha256":ref["sha256"]}
        for k in ("paid_authorization","f10_offline","quota_review")}
    return SimpleNamespace(config=config,parent=parent,children=children,campaign=campaign,costs=costs,
        runtime=runtime,clock=clock,private=tmp_path / "private",results=tmp_path / "results",card=card,cardpath=cardpath)


def build(fixture, runner=None):
    return build_runtime(campaign=fixture.campaign,parent=fixture.parent,children=fixture.children,
        costs=fixture.costs,runtime=fixture.runtime,controller_session_id="SYNTHETIC",runner=runner or NoProviderCalls(),
        clock=fixture.clock,private_root=fixture.private,repository_root=ROOT)


def execute(built, clock, factory=None):
    return run_lifecycle(ledger=built["ledger"],reservation_request=built["request"],admission_plan=built["plan"],
        allowance=built["allowance"],workflow_factory=factory or built["workflow_factory"],clock=clock,
        recovery_path=built["recovery_path"],recovery_factory=built["recovery_factory"])


def test_gcp_priced_zero_native_support_reserves_with_real_source_and_financial_guards(tmp_path):
    fixture=prepared(tmp_path,"gcp")
    built=build(fixture)
    assert built["plan"].provider_support["gcp"]["included_usd"] == "0"
    result=built["ledger"].reserve(built["request"],plan=built["plan"])
    assert result.status == "reserved", result.reasons
    row=built["ledger"].inspect()["reservations"][result.evidence.reservation_id.value]
    assert row["reserved_cost_usd"] == "0.6000" and row["cleanup_open"]
    assert row["plan"]["support"]["gcp"]["included_usd"] == "0"
    assert float(row["financial"]["gross_eur"]) > 0 and row["financial"]["cash_eur"] == row["financial"]["gross_eur"]


@pytest.mark.parametrize("case", ["missing","negative","mismatch","source","path","unpriced","wrong_provider","boolean","null"])
def test_zero_support_never_means_missing_or_unpriced(tmp_path,case):
    fixture=prepared(tmp_path,"azure" if case == "wrong_provider" else "gcp")
    built=build(fixture); plan=built["plan"]; provider=next(iter(plan.child_identities))
    support=copy.deepcopy(plan.provider_support)
    if case == "missing": support[provider].pop("included_usd")
    if case == "negative": support[provider]["included_usd"]="-1"
    if case == "mismatch": support[provider]["included_usd"]="0.01"
    if case == "source": support[provider]["source_sha256"]="e"*64
    if case == "path": support[provider]["source_path"]="missing.json"
    if case == "boolean": support[provider]["included_usd"]=False
    if case == "null": support[provider]["included_usd"]=None
    if case == "unpriced":
        envelopes=copy.deepcopy(plan.cost_envelopes); del envelopes[provider]["components"]["expiry_cleanup_support"]["amount_usd"]
        plan=replace(plan,cost_envelopes=envelopes)
    before=built["ledger"].path.read_bytes()
    decision=built["ledger"].reserve(built["request"],plan=replace(plan,provider_support=support))
    assert decision.status in {"rejected", "pending"}, decision
    assert set(decision.reasons) <= {"support_cost_missing","cost_envelope_invalid"}
    assert built["ledger"].path.read_bytes() == before


class NativeReadClock:
    def __init__(self): self.start=time.monotonic()
    def monotonic(self): return time.monotonic()
    def utc_now(self): return NOW+timedelta(seconds=time.monotonic()-self.start)


@pytest.mark.parametrize("clock_type", [SequentialClock,NativeReadClock])
def test_real_sequential_clock_anchor_never_extends_utc_cap(tmp_path,clock_type):
    fixture=prepared(tmp_path,"azure",support="0.04",clock=clock_type())
    built=build(fixture)
    decision=built["ledger"].reserve(built["request"],plan=built["plan"])
    assert decision.status == "reserved", decision.reasons
    row=built["ledger"].inspect()["reservations"][decision.evidence.reservation_id.value]
    expires=decision.evidence.expires_at.value
    now=fixture.clock.utc_now(); mono=fixture.clock.monotonic()
    assert now + timedelta(seconds=row["cap_start_monotonic"]+row["cap_seconds"]-mono) <= expires
    assert row["provider_trigger_monotonic"] == row["cap_start_monotonic"]+(row["cap_seconds"]-360)
    hooks=built["workflow_factory"](decision.evidence)
    assert set(hooks) == {"azure"}
    settings=json.loads(next((fixture.private / "f09-cleanup/azure").glob("*.json")).read_text())
    assert settings["settings"]["cap_deadline_monotonic"] <= row["cap_start_monotonic"]+1200
    assert fixture.clock.utc_now()+timedelta(seconds=settings["settings"]["cap_deadline_monotonic"]-fixture.clock.monotonic()) <= expires


@pytest.mark.parametrize("issue", ["after_settings","before_settings"])
def test_factory_failure_is_durable_and_retains_cleanup_liability(tmp_path, issue):
    fixture=prepared(tmp_path,"azure",support="0.04"); runner=NoProviderCalls(); built=build(fixture,runner)
    def broken(reservation):
        saved=SavedLifecycleRecovery.model_validate_json(built["recovery_path"].read_text())
        assert saved.reservation_id == reservation.reservation_id.value and saved.providers[0].cleanup_open
        if issue == "after_settings": built["workflow_factory"](reservation)
        raise RuntimeError("injected pre-provider factory failure")
    result=execute(built,fixture.clock,broken)
    assert result.admission_status == "reserved" and not result.completed
    assert result.admission_reasons == ("workflow_factory_failed",)
    assert "injected pre-provider factory failure" in result.primary_failure
    provider=result.provider_results["azure"]
    assert provider.execution_state == "failed" and provider.cleanup_state == "unknown"
    assert not provider.creation_may_have_started and not provider.stage_times and not runner.requests
    saved=SavedLifecycleRecovery.model_validate_json(built["recovery_path"].read_text())
    assert saved.initialization_failure == result.primary_failure
    assert saved.providers[0].cleanup_open and not saved.providers[0].expiry_registered
    assert built["recovery_path"].stat().st_mode & 0o077 == 0
    row=built["ledger"].inspect()["reservations"][saved.reservation_id]
    assert row["cleanup_open"] and not row.get("creation_claims") and float(row["reserved_cost_usd"]) > 0
    if issue == "after_settings":
        from cloud_network_benchmark.lifecycle_cleanup import load_cleanup_settings
        settings=load_cleanup_settings(Path(saved.providers[0].cleanup_settings_reference))
        assert settings.deployment.run_id == fixture.children["azure"].run_id
        assert Path(saved.providers[0].journal_reference).is_file()
    else:
        assert not Path(saved.providers[0].cleanup_settings_reference).exists()
        from cloud_network_benchmark.lifecycle_cleanup import build_cleanup_runtime
        cleanup=build_cleanup_runtime(recovery_path=built["recovery_path"],recovery=saved,
            current_session_id="SYNTHETIC-restart",runner=runner,
            utc_now=lambda:NOW+timedelta(days=3),monotonic_now=lambda:1.)
        assert not cleanup.complete and not runner.requests
        assert built["ledger"].inspect()["reservations"][saved.reservation_id]["cleanup_open"]


@pytest.mark.parametrize('case', ['absent','blank_inventory','residual_role','missing_journal','changed_inputs'])
def test_factory_failure_cleanup_after_expiry_needs_independent_absence(tmp_path,case):
    from cloud_network_benchmark.lifecycle_cleanup import build_cleanup_runtime,load_cleanup_settings
    from cloud_network_benchmark.azure_provider import AzureRuntimeOptions
    from tests.integration.test_azure_provider_flow import CleanupRunner
    fixture=prepared(tmp_path,'azure',support='0.04'); built=build(fixture)
    def broken(reservation):
        built['workflow_factory'](reservation)
        raise RuntimeError('injected factory failure after original settings')
    result=execute(built,fixture.clock,broken)
    assert result.admission_reasons == ('workflow_factory_failed',)
    recovery=SavedLifecycleRecovery.model_validate_json(built['recovery_path'].read_text())
    record=recovery.providers[0]; saved=load_cleanup_settings(Path(record.cleanup_settings_reference))
    settings_bytes=Path(record.cleanup_settings_reference).read_bytes()
    options=AzureRuntimeOptions.model_validate(saved.settings)
    original={p:p.read_bytes() for p in options.attempt_root.glob('*.tfvars.json')}
    assert len(original)==2
    ledger_before=copy.deepcopy(built['ledger'].inspect()['reservations'][recovery.reservation_id])
    if case == 'missing_journal': Path(record.journal_reference).unlink()
    if case == 'changed_inputs':
        pair=options.attempt_root/'pair.auto.tfvars.json'
        value=json.loads(pair.read_text()); value['resource_group_name']='foreign-group'; save(pair,value)
    class ObservedCleanup(CleanupRunner):
        def run(self,request):
            for arg in request.argv:
                if arg.startswith('-var-file='):
                    path=Path(arg.split('=',1)[1]); assert path in original and path.read_bytes()==original[path]
            return super().run(request)
    late=NOW+timedelta(days=3)
    runner=ObservedCleanup(saved.deployment,options,late,account_present=False,present_children=(),
        blank_inventory=case=='blank_inventory',role_definition_inventory='[{"id":"foreign-role"}]' if case=='residual_role' else '[]')
    cleanup=build_cleanup_runtime(recovery_path=built['recovery_path'],recovery=recovery,
        current_session_id='SYNTHETIC-restart',runner=runner,utc_now=lambda:late,monotonic_now=lambda:1.)
    after=built['ledger'].inspect()['reservations'][recovery.reservation_id]
    assert cleanup.complete == (case=='absent'), cleanup.providers
    assert after['cleanup_open'] == (case!='absent')
    assert after['reserved_cost_usd']==ledger_before['reserved_cost_usd']
    assert after['financial']==ledger_before['financial']
    assert float(after.get('released_usd',0))==0
    assert Path(record.cleanup_settings_reference).read_bytes()==settings_bytes
    if case in {'missing_journal','changed_inputs'}: assert not runner.requests
    if case=='absent':
        actions={r.action_type for r in runner.requests}
        assert {'pair_inventory','expiry_inventory','vm_absence_by_id','role_cleanup_inventory','role_definition_cleanup_inventory'} <= actions
        assert not any('apply' in r.argv for r in runner.requests)
        assert after['provider_cleanup']['azure'] is True


def test_cli_factory_exception_retains_reservation_and_failed_manifest(tmp_path,monkeypatch,capsys):
    from cloud_network_benchmark import lifecycle_cli as cli,azure_provider as azure
    from cloud_network_benchmark.contracts import execution
    fixture=prepared(tmp_path,'azure',support='0.04'); commands=NoProviderCalls()
    original=azure._validate_request
    def broken(deployment,options,now,mono,**kwargs):
        if kwargs.get('check_live_clock',True):
            raise ValueError('source-shaped factory exception')
        return original(deployment,options,now,mono,**kwargs)
    monkeypatch.setattr(azure,'_validate_request',broken)
    monkeypatch.setattr(cli,'load_runtime_config',lambda *_a,**_k:fixture.runtime)
    monkeypatch.setattr(execution.SubprocessCommandRunner,'run',lambda _self,request:commands.run(request))
    runtimepath=Path(fixture.runtime['runtime_source']['path'])
    code=cli.main(['run','--config',str(fixture.config),'--cost-inputs',fixture.costs.source_path,
        '--runtime-config',str(runtimepath),'--results-root',str(fixture.results),
        '--private-root',str(fixture.private),'--controller-session','SYNTHETIC',
        '--execution-scope','paid_request','--format','json'],run_clock=fixture.clock)
    report=json.loads(capsys.readouterr().err)
    assert code==1 and report['status']=='failed',report
    assert report['admission_status']=='reserved' and report['reservation_id']
    assert report['external_calls']==0 and not commands.requests
    assert 'workflow_factory_failed' in report['reason_codes']
    assert 'invalid_lifecycle_request' not in report['reason_codes']
    assert 'source-shaped factory exception' in report['providers']['azure']['primary_failure']
    assert report['providers']['azure']['execution_state']=='failed'
    child=ChildRunManifest.model_validate_json(Path(fixture.parent.child_runs[0].manifest_path).read_text())
    assert child.execution_state=='failed' and child.actual_started_at is None and not child.commands
    assert child.validation_status.data_validity!='valid'
    recovery=SavedLifecycleRecovery.model_validate_json((fixture.private/'lifecycle-recovery.json').read_text())
    assert recovery.reservation_id==report['reservation_id'] and recovery.providers[0].cleanup_open


def test_factory_conservatively_clamps_legacy_sequential_clock_mapping(tmp_path):
    fixture=prepared(tmp_path,'azure',support='0.04'); built=build(fixture)
    decision=built['ledger'].reserve(built['request'],plan=built['plan'])
    assert decision.status=='reserved',decision.reasons
    # Synthetic old ledger only: emulate the old UTC-before-monotonic gap.
    state=json.loads(built['ledger'].path.read_text()); row=state['reservations'][decision.evidence.reservation_id.value]
    row['cap_start_monotonic']+=.002; row['provider_trigger_monotonic']+=.002
    save(built['ledger'].path,state)
    original_deadline=row['cap_start_monotonic']+row['cap_seconds']
    hooks=built['workflow_factory'](decision.evidence)
    assert set(hooks)=={'azure'}
    saved=json.loads(next((fixture.private/'f09-cleanup/azure').glob('*.json')).read_text())
    deadline=saved['settings']['cap_deadline_monotonic']
    assert deadline<original_deadline
    assert fixture.clock.utc_now()+timedelta(seconds=deadline-fixture.clock.monotonic())<=decision.evidence.expires_at.value
    assert built['ledger'].inspect()['reservations'][decision.evidence.reservation_id.value]['cap_start_monotonic']==row['cap_start_monotonic']
