from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cloud_network_benchmark.admission import BudgetLedger, Reconciliation, load_cost_inputs
from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.contracts.admission import ExpiryEvidence, ExpiryRequest, ReservationRequest
from cloud_network_benchmark.contracts.common import Evidence, Provider, StructuredEvidence
from cloud_network_benchmark.admission import AdmissionPlan, WorkflowAllowance
from decimal import Decimal
import os
import json
import subprocess
import sys
import multiprocessing
import cloud_network_benchmark.admission as admission_module
import fcntl
import hashlib
from cloud_network_benchmark.contracts.admission import ScheduleEntry
from cloud_network_benchmark.contracts.campaign import CommonNState, ReplacementContext
from tests.fixtures.f11.envelope_factory import make_synthetic_envelopes


ROOT = Path(__file__).parents[2]


def ledger(path):
    return BudgetLedger(path, scope_id="offline-scope", utc_now=lambda: datetime(2026, 10, 22, tzinfo=timezone.utc),
        monotonic_now=lambda: 1.0, controller_session_id="session-1")


def _reserve_same_claim_in_process(args):
    path, campaign_id = args
    book = ledger(Path(path))
    costs = load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json")
    campaign, _ = resolve_campaign(ROOT / "configs/tests/exp-903-three-provider-smoke.yaml", ROOT)
    allowance = WorkflowAllowance(10, 20, 10, 60, 76, 1, 10, 10, "synthetic-test-policy",
        {"aws": 360, "azure": 60, "gcp": 60})
    request = ReservationRequest(schema_version=2, campaign_id=campaign_id, config_id=campaign.config_id,
        schedule_entry_id=Evidence[str](unavailable_reason="acceptance has no scheduled row"),
        selected_providers=[Provider.AWS, Provider.AZURE, Provider.GCP], mode=campaign.mode,
        priority=campaign.admission.priority, lifetime_cap_seconds=1200,
        estimated_cost_usd=Evidence[float](value=.5), source_path=costs.source_path, source_sha256=costs.payload_sha256,
        incurred_cost_usd=Evidence[float](value=0), active_reserved_usd=Evidence[float](value=0), outstanding_cleanup=False)
    children = {p: f"{campaign_id}-{p}" for p in ("aws", "azure", "gcp")}
    funding = {"primary": Decimal("0.5")}
    plan = AdmissionPlan(campaign, children,
        {p: f"{campaign_id}-{p}-ns" for p in ("aws", "azure", "gcp")}, costs, allowance,
        funding=funding,cost_envelopes=make_synthetic_envelopes(campaign,children,funding))
    result = book.reserve(request, plan=plan)
    return result.status, result.newly_reserved, result.revision


def scheduled_campaign(config_path, entry_id, start):
    base, _ = resolve_campaign(config_path, ROOT)
    header="entry_id,config_id,experiment_id,scheduled_start,window_label,priority"
    row=f"{entry_id},{base.config_id},{base.experiment_id},{start.isoformat()},night,{base.admission.priority}"
    source=(header+"\n"+row+"\n").encode()
    entry=ScheduleEntry(schema_version=2,entry_id=entry_id,config_id=base.config_id,experiment_id=base.experiment_id,
        scheduled_start=start,window_label="night",source_path="generated/f11-week.csv",source_sha256=hashlib.sha256(source).hexdigest(),
        row_sha256=hashlib.sha256(row.encode()).hexdigest(),original_header=header,original_row=row,columns=header.split(","),priority=base.admission.priority)
    campaign,_=resolve_campaign(config_path,ROOT,schedule_entry=entry)
    return campaign


def scheduled_request_and_plan(campaign, costs, *, campaign_id, providers, child_prefix, namespace_prefix, funding,
                               original_failure=None, observation_id=None, measurement_seconds=226, lead_seconds=60):
    names=[Provider(provider).value for provider in providers]
    allowance=WorkflowAllowance(10,20,10,60,measurement_seconds,1,10,10,"synthetic-sourced-policy",{name:lead_seconds for name in names})
    schedule_evidence = (Evidence[str](value=campaign.schedule_entry.value.entry_id) if campaign.schedule_entry.value
        else Evidence[str](unavailable_reason="acceptance has no scheduled row"))
    request=ReservationRequest(schema_version=2,campaign_id=campaign_id,config_id=campaign.config_id,
        schedule_entry_id=schedule_evidence,selected_providers=providers,
        mode=campaign.mode,priority=campaign.admission.priority,lifetime_cap_seconds=campaign.admission.lifetime_cap_seconds,
        estimated_cost_usd=Evidence[float](value=float(sum((Decimal(value) for value in funding.values()),Decimal("0")))),source_path=costs.source_path,
        source_sha256=costs.payload_sha256,incurred_cost_usd=Evidence[float](value=0),
        active_reserved_usd=Evidence[float](value=0),outstanding_cleanup=False)
    children={name:f"{child_prefix}-{name}" for name in names}
    funding_map={key:Decimal(value) for key,value in funding.items()}
    plan=AdmissionPlan(campaign,children,
        {name:f"{namespace_prefix}-{name}" for name in names},costs,allowance,
        funding=funding_map,original_failure=original_failure,observation_id=observation_id,
        cost_envelopes=make_synthetic_envelopes(campaign,children,funding_map))
    return request,plan


def record_absence(book, event_id, reservation_id, provider, when, *, failure=None):
    source_path=f"synthetic-evidence/cleanup-{event_id}.json"
    source_sha=hashlib.sha256(f"fixture-only:{event_id}".encode()).hexdigest()
    event=Reconciliation(event_id,reservation_id,provider,source_sha,cleanup_verified_absent=True,
        billable_resources_present=False,failure=failure,occurred_at=when,source_path=source_path,
        absence_evidence_id=f"absence-{event_id}",
        absent_resource_ids=(f"{provider.value}:vm-a",f"{provider.value}:vm-b",f"{provider.value}:expiry-support"))
    book.reconcile(event)
    return source_sha


def test_reconciliation_is_idempotent_keeps_primary_and_records_secondary_failure(tmp_path):
    costs = load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json")
    book = ledger(tmp_path / "ledger.json")
    book.initialize(costs)
    _reserve_same_claim_in_process((str(book.path), "F11-failures"))
    rid = next(iter(book.inspect()["reservations"]))
    now = datetime(2026, 10, 22, tzinfo=timezone.utc)
    first = {"run_id": "aws-run", "failed_provider": "aws", "reason": "probe_failed"}
    second = {"run_id": "azure-run", "failed_provider": "azure", "reason": "copy_failed"}
    first_event = Reconciliation("primary-failure", rid, Provider.AWS,
        hashlib.sha256(b"aws-failure").hexdigest(), failure=first, occurred_at=now,
        source_path="synthetic/aws-failure.json")
    book.reconcile(first_event)
    revision = book.inspect()["revision"]
    book.reconcile(first_event)
    assert book.inspect()["revision"] == revision
    secondary_event = Reconciliation("secondary-failure", rid, Provider.AZURE,
        hashlib.sha256(b"azure-failure").hexdigest(), failure=second, occurred_at=now + timedelta(seconds=1),
        source_path="synthetic/azure-failure.json")
    book.reconcile(secondary_event)
    rec = book.inspect()["reservations"][rid]
    assert rec["failure"] == first
    assert rec["failure_provider"] == "aws"
    assert rec["failures_by_provider"] == {"aws": first, "azure": second}
    assert len(rec["failures"]) == 2
    with pytest.raises(ValueError, match="duplicate_conflict"):
        book.reconcile(Reconciliation("primary-failure", rid, Provider.AWS,
            hashlib.sha256(b"aws-failure").hexdigest(), failure={**first, "reason": "changed"},
            occurred_at=now, source_path="synthetic/aws-failure.json"))
    assert set(book.inspect()["cleanup_blocked"]) == {rid}


def test_explicit_initialization_persists_scoped_private_ledger(tmp_path):
    path = tmp_path / "ledger.json"
    book = ledger(path)
    costs = load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json")
    state = book.initialize(costs)
    assert state["revision"] == 1
    assert book.inspect()["scope_id"] == "offline-scope"
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="exclusive"):
        book.initialize(costs)


def test_initialization_refuses_dangling_ledger_symlink(tmp_path):
    path = tmp_path / "ledger.json"
    path.symlink_to(tmp_path / "not-created.json")
    book = ledger(path)
    costs = load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json")
    with pytest.raises(ValueError, match="exclusive"):
        book.initialize(costs)
    assert path.is_symlink()
    assert not path.with_name("ledger.json.lock").exists()


def test_preview_is_pending_read_only_and_invalid_input_exits_two(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    base = [sys.executable, "-m", "cloud_network_benchmark.admission", "preview",
        "--schedule", "tests/fixtures/f11/schedule-pending.csv", "--config-map", "tests/fixtures/f11/config-map.json",
        "--cost-inputs", "tests/fixtures/f11/cost-inputs.json", "--now", "2026-10-22T00:00:00Z"]
    good = subprocess.run(base, cwd=ROOT, env=env, text=True, capture_output=True, check=False)
    assert good.returncode == 0
    assert '"paid_eligible":false' in good.stdout
    assert '"schedule_pending"' in good.stdout
    assert not list(tmp_path.glob("*.json"))
    bad = subprocess.run(base[:-2] + ["missing.json", "--now", "2026-10-22T00:00:00Z"], cwd=ROOT,
        env=env, text=True, capture_output=True, check=False)
    assert bad.returncode == 2


def test_preview_reports_duplicate_retained_schedule_as_invalid(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    duplicate = tmp_path / "duplicate-calibration.csv"
    duplicate.write_text("entry_id,config_id,experiment_id,scheduled_start,window_label,priority\n"
        "CAL-A,exp-001-calibration,EXP-001,,night,P0\n"
        "CAL-B,exp-001-calibration,EXP-001,,night,P0\n", encoding="utf-8")
    command = [sys.executable, "-m", "cloud_network_benchmark.admission", "preview", "--schedule", str(duplicate),
        "--config-map", "tests/fixtures/f11/config-map.json", "--cost-inputs", "tests/fixtures/f11/cost-inputs.json",
        "--now", "2026-10-22T00:00:00Z"]
    result = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, check=False)
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["status"] == "invalid"
    assert any(code.startswith("schedule_error:identity_mismatch:CAL-A:retained_mode_requires_one_entry")
        for code in report["reason_codes"])


def test_concurrent_processes_commit_one_identical_claim(tmp_path):
    path = tmp_path / "ledger.json"
    book = ledger(path)
    book.initialize(load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json"))
    with multiprocessing.get_context("fork").Pool(2) as pool:
        results = pool.map(_reserve_same_claim_in_process, [(str(path), "F11-concurrent-identical")] * 2)
    assert sorted(result[1] for result in results) == [False, True]
    assert {result[2] for result in results} == {2}
    assert len(book.inspect()["reservations"]) == 1


def test_atomic_commit_failure_before_replace_and_ambiguous_after_replace(tmp_path, monkeypatch):
    path = tmp_path / "ledger.json"
    book = ledger(path)
    costs = load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json")
    book.initialize(costs)
    args = (str(path), "F11-atomic")
    monkeypatch.setattr(admission_module.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("before replace")))
    with pytest.raises(OSError, match="before replace"):
        _reserve_same_claim_in_process(args)
    assert book.inspect()["revision"] == 1
    assert not book.inspect()["reservations"]
    monkeypatch.undo()
    original_fsync = admission_module.os.fsync
    def fail_parent_directory(fd):
        import stat
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("after replace")
        return original_fsync(fd)
    monkeypatch.setattr(admission_module.os, "fsync", fail_parent_directory)
    with pytest.raises(OSError, match="after replace"):
        _reserve_same_claim_in_process(args)
    monkeypatch.undo()
    state = book.inspect()
    assert state["revision"] == 2
    assert len(state["reservations"]) == 1
    retry = _reserve_same_claim_in_process(args)
    assert retry == ("reserved", False, 2)


def test_stage_fit_preserves_original_monotonic_cap_and_effective_trigger(tmp_path):
    utc = [datetime(2026,10,22,tzinfo=timezone.utc)]
    monotonic = [100.0]
    costs = load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json")
    book = BudgetLedger(tmp_path / "ledger.json", scope_id="time-scope", utc_now=lambda:utc[0],
        monotonic_now=lambda:monotonic[0], controller_session_id="controller")
    book.initialize(costs)
    campaign, _ = resolve_campaign(ROOT / "configs/tests/exp-903-three-provider-smoke.yaml", ROOT)
    providers = [Provider.AWS,Provider.AZURE,Provider.GCP]
    allowance = WorkflowAllowance(10,20,10,60,76,1,10,10,"time-policy",{"aws":360,"azure":60,"gcp":60})
    request = ReservationRequest(schema_version=2,campaign_id="F11-time",config_id=campaign.config_id,
        schedule_entry_id=Evidence[str](unavailable_reason="acceptance has no scheduled row"),selected_providers=providers,
        mode=campaign.mode,priority=campaign.admission.priority,lifetime_cap_seconds=1200,
        estimated_cost_usd=Evidence[float](value=.5),source_path=costs.source_path,source_sha256=costs.payload_sha256,
        incurred_cost_usd=Evidence[float](value=0),active_reserved_usd=Evidence[float](value=0),outstanding_cleanup=False)
    children={p.value:f"{p.value}-child" for p in providers}; funding={"primary":Decimal("0.5")}
    plan=AdmissionPlan(campaign,children,{p.value:f"{p.value}-namespace" for p in providers},
        costs,allowance,funding=funding,cost_envelopes=make_synthetic_envelopes(campaign,children,funding))
    decision=book.reserve(request,plan=plan)
    assert decision.effective_deadline == utc[0] + timedelta(seconds=840)
    rid=decision.evidence.reservation_id.value
    omitted_future = WorkflowAllowance(10,0,10,60,76,1,10,10,"time-policy",{"aws":360,"azure":60,"gcp":60})
    assert not book.check_stage_fit(rid,stage="registration",remaining=omitted_future).eligible
    assert book.check_stage_fit(rid,stage="registration",remaining=allowance).eligible
    # Wall-clock steps are evidence only; fit uses the original monotonic cap mapping.
    utc[0] += timedelta(days=2)
    monotonic[0] += 1
    assert book.check_stage_fit(rid,stage="registration",remaining=allowance).eligible
    utc[0] -= timedelta(days=4)
    assert book.check_stage_fit(rid,stage="registration",remaining=allowance).eligible
    monotonic[0] += 150
    utc[0] += timedelta(seconds=150)
    measurement=WorkflowAllowance(0,0,0,0,76,1,10,10,"time-policy",{"aws":360,"azure":60,"gcp":60},("registration","preparation"))
    blocked=book.check_stage_fit(rid,stage="measurement",remaining=measurement)
    assert blocked.eligible
    # The cap and provider trigger, rather than the initial fit estimate, bound work.
    monotonic[0] = 940.0
    expired = book.check_stage_fit(rid,stage="measurement",remaining=measurement)
    assert not expired.eligible and "workflow_time_insufficient" in expired.reason_codes
    monotonic[0]=99.0
    regressed=book.check_stage_fit(rid,stage="registration",remaining=allowance)
    assert "controller_session_changed" in regressed.reason_codes


def test_final_billing_and_all_provider_absence_release_only_proved_unused_funding(tmp_path):
    path=tmp_path / "ledger.json"
    book=ledger(path)
    costs=load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json")
    book.initialize(costs)
    _reserve_same_claim_in_process((str(path),"F11-release"))
    rid=next(iter(book.inspect()["reservations"]))
    with pytest.raises(ValueError,match="final sourced billing"):
        book.reconcile(Reconciliation("release-early",rid,Provider.AWS,costs.payload_sha256,
            released_funding_by_bucket={"primary":Decimal("0.4")},release_evidence_id="billing-final",
            release_source_path="billing.json",source_path=costs.source_path,
            occurred_at=datetime(2026,10,22,0,1,tzinfo=timezone.utc)))
    for provider in (Provider.AWS,Provider.AZURE):
        record_absence(book,f"absence-{provider.value}",rid,provider,datetime(2026,10,22,0,2,tzinfo=timezone.utc))
    for provider, amount in ((Provider.AWS, Decimal("0.02")), (Provider.AZURE, Decimal("0.03"))):
        book.reconcile(Reconciliation(f"final-billing-{provider.value}",rid,provider,
            hashlib.sha256(f"billing-{provider.value}".encode()).hexdigest(), incurred_usd=amount, actual=True,
            occurred_at=datetime(2026,10,22,0,2,30,tzinfo=timezone.utc),billing_final=True,
            source_path=f"local/{provider.value}-billing.json"))
    billing_sha=hashlib.sha256(b"synthetic-final-billing-evidence").hexdigest()
    book.reconcile(Reconciliation("final-billing",rid,Provider.GCP,billing_sha,
        incurred_usd=Decimal("0.05"),actual=True,cleanup_verified_absent=True,billable_resources_present=False,
        occurred_at=datetime(2026,10,22,0,3,tzinfo=timezone.utc),billing_final=True,
        released_funding_by_bucket={"primary":Decimal("0.4")},release_evidence_id="billing-final-v1",
        release_source_path="local/billing-final.json",source_path="local/billing-final.json",
        absence_evidence_id="absence-final-billing",absent_resource_ids=("gcp:vm-a","gcp:vm-b","gcp:expiry-support")))
    state=book.inspect()
    reservation=state["reservations"][rid]
    assert Decimal(reservation["incurred_usd"]) == Decimal("0.1")
    assert reservation["released_usd"] == "0.4"
    assert reservation["evidence"]["state"] == "released"
    assert reservation["evidence"]["reasons"]
    assert Decimal(state["funding_remaining"]["primary"]) == Decimal("100.3")
    assert not state["cleanup_blocked"]
    terminal = book.claim_creation(rid, provider=Provider.AWS, expiry_requests=[], expiry_evidence=[])
    assert not terminal.eligible and terminal.reason_codes == ("reservation_terminal",)
    with pytest.raises(ValueError, match="conflicting_final_billing"):
        book.reconcile(Reconciliation("conflicting-aws-final",rid,Provider.AWS,
            hashlib.sha256(b"conflicting-final").hexdigest(),incurred_usd=Decimal("0.09"),actual=True,
            billing_final=True,occurred_at=datetime(2026,10,22,0,4,tzinfo=timezone.utc),source_path="local/conflict.json"))


def test_replacement_is_bound_to_one_failed_short_observation_and_original_provider(tmp_path):
    now=datetime(2026,10,22,tzinfo=timezone.utc)
    costs=load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json")
    book=ledger(tmp_path / "ledger.json")
    book.initialize(costs)
    config=ROOT / "configs/experiments/exp-001-multi-provider.yaml"
    original=scheduled_campaign(config,"ORIGINAL-S1",now+timedelta(seconds=60))
    frozen=CommonNState(state="frozen",common_n=4,source_campaign_id="CAL-OBS",source_sha256="a"*64,plateau_found=True)
    original=original.model_copy(update={"benchmark":original.benchmark.model_copy(update={"common_n":frozen})})
    providers=[Provider.AWS,Provider.AZURE,Provider.GCP]
    req,plan=scheduled_request_and_plan(original,costs,campaign_id="original-campaign",providers=providers,
        child_prefix="original-run",namespace_prefix="original-resources",funding={"primary":"0.5"})
    initial=book.reserve(req,plan=plan)
    assert initial.status=="reserved"
    rid=initial.evidence.reservation_id.value
    failure={"run_id":"original-run-aws","failed_run_id":"original-run-aws","failed_provider":"aws","observation_id":"OBS-001"}
    failure_source=None
    for provider in providers:
        observed=record_absence(book,f"cleanup-{provider.value}",rid,provider,now+timedelta(minutes=2),
            failure=failure if provider==Provider.AWS else None)
        if provider==Provider.AWS: failure_source=observed
    replacement_row=scheduled_campaign(config,"REPLACEMENT-S1-A",now+timedelta(minutes=5))
    replacement_context=ReplacementContext(observation_id="OBS-001",replaces_run_id="original-run-aws")
    replacement_campaign=replacement_row.model_copy(update={"benchmark":replacement_row.benchmark.model_copy(update={"common_n":frozen}),
        "context":"replacement","replacement_context":replacement_context,"selected_providers":[Provider.AWS]})
    original_failure={"original_reservation_id":rid,"failed_run_id":"original-run-aws","failed_provider":"aws",
        "observation_id":"OBS-001","failure_event_id":"cleanup-aws","failure_source_sha256":failure_source}
    mismatched_failure={**original_failure, "failed_provider":"azure"}
    mismatch_req,mismatch_plan=scheduled_request_and_plan(replacement_campaign,costs,campaign_id="replacement-wrong-provider",
        providers=[Provider.AWS],child_prefix="replacement-wrong-provider-run",namespace_prefix="replacement-wrong-provider-resources",
        funding={"replacements":"0.5"},original_failure=mismatched_failure,observation_id="OBS-001")
    mismatch_req=mismatch_req.model_copy(update={"active_reserved_usd":Evidence[float](value=.5)})
    mismatch=book.reserve(mismatch_req,plan=mismatch_plan)
    assert mismatch.status=="rejected" and mismatch.reasons==("prerequisite_pending",)
    rreq,rplan=scheduled_request_and_plan(replacement_campaign,costs,campaign_id="replacement-campaign-a",
        providers=[Provider.AWS],child_prefix="replacement-a",namespace_prefix="replacement-a-resources",
        funding={"replacements":"0.5"},original_failure=original_failure,observation_id="OBS-001")
    rreq=rreq.model_copy(update={"active_reserved_usd":Evidence[float](value=.5)})
    admitted=book.reserve(rreq,plan=rplan)
    assert admitted.status=="reserved", admitted.reasons
    second_row=scheduled_campaign(config,"REPLACEMENT-S1-B",now+timedelta(minutes=10))
    second_campaign=second_row.model_copy(update={"benchmark":second_row.benchmark.model_copy(update={"common_n":frozen}),
        "context":"replacement","replacement_context":replacement_context,"selected_providers":[Provider.AWS]})
    second_req,second_plan=scheduled_request_and_plan(second_campaign,costs,campaign_id="replacement-campaign-b",
        providers=[Provider.AWS],child_prefix="replacement-b",namespace_prefix="replacement-b-resources",
        funding={"replacements":"0.5"},original_failure=original_failure,observation_id="OBS-001")
    second_req=second_req.model_copy(update={"active_reserved_usd":Evidence[float](value=1.0)})
    rejected=book.reserve(second_req,plan=second_plan)
    assert rejected.status=="rejected"
    assert rejected.reasons==("duplicate_conflict",)


def test_successful_original_cannot_qualify_for_replacement(tmp_path):
    now = datetime(2026, 10, 22, tzinfo=timezone.utc)
    costs = load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json")
    book = ledger(tmp_path / "ledger.json")
    book.initialize(costs)
    config = ROOT / "configs/experiments/exp-001-multi-provider.yaml"
    frozen = CommonNState(state="frozen", common_n=4, source_campaign_id="CAL-OBS",
        source_sha256="f" * 64, plateau_found=True)
    original = scheduled_campaign(config, "SUCCESSFUL-ORIGINAL", now + timedelta(seconds=60))
    original = original.model_copy(update={"benchmark": original.benchmark.model_copy(update={"common_n": frozen})})
    req, plan = scheduled_request_and_plan(original, costs, campaign_id="successful-original",
        providers=[Provider.AWS, Provider.AZURE, Provider.GCP], child_prefix="successful-run", namespace_prefix="successful-resource",
        funding={"primary": "0.5"})
    admitted = book.reserve(req, plan=plan)
    assert admitted.status == "reserved"
    rid = admitted.evidence.reservation_id.value
    for provider in (Provider.AWS, Provider.AZURE, Provider.GCP):
        record_absence(book, f"successful-cleanup-{provider.value}", rid, provider, now + timedelta(minutes=2))

    row = scheduled_campaign(config, "FALSE-REPLACEMENT", now + timedelta(minutes=5))
    context = ReplacementContext(observation_id="NO-FAILURE", replaces_run_id="successful-run-aws")
    replacement = row.model_copy(update={"benchmark": row.benchmark.model_copy(update={"common_n": frozen}),
        "context": "replacement", "replacement_context": context, "selected_providers": [Provider.AWS]})
    link = {"original_reservation_id": rid, "failed_run_id": "successful-run-aws", "failed_provider": "aws",
        "observation_id": "NO-FAILURE", "failure_event_id": "no-failure-event", "failure_source_sha256": costs.payload_sha256}
    replacement_req, replacement_plan = scheduled_request_and_plan(replacement, costs,
        campaign_id="replacement-after-success", providers=[Provider.AWS], child_prefix="false-replacement-run",
        namespace_prefix="false-replacement-resource", funding={"replacements": "0.5"},
        original_failure=link, observation_id="NO-FAILURE")
    replacement_req = replacement_req.model_copy(update={"active_reserved_usd": Evidence[float](value=0.5)})
    result = book.reserve(replacement_req, plan=replacement_plan)
    assert result.status == "rejected"
    assert result.reasons == ("prerequisite_pending",)


@pytest.mark.parametrize(("filename", "entry", "measurement"), [
    ("exp-001-calibration.yaml", "NO-CAL-REPEAT", 951),
    ("exp-001-diagnostic-day.yaml", "NO-DAY-REPEAT", 86408),
    ("exp-002-cross-zone.yaml", "NO-S2-REPEAT", 226),
    ("exp-004-inter-region.yaml", "NO-S4-REPEAT", 226),
])
def test_replacement_scope_refuses_cal_day_and_deferred_scenarios(tmp_path, filename, entry, measurement):
    now = datetime(2026, 10, 22, tzinfo=timezone.utc)
    costs = load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json")
    book = ledger(tmp_path / f"{entry}.json")
    book.initialize(costs)
    frozen = CommonNState(state="frozen", common_n=4, source_campaign_id="CAL-OBS",
        source_sha256="9" * 64, plateau_found=True)
    original = scheduled_campaign(ROOT / "configs/experiments/exp-001-multi-provider.yaml",
        f"ORIGINAL-{entry}", now + timedelta(minutes=1))
    original = original.model_copy(update={"benchmark": original.benchmark.model_copy(update={"common_n": frozen})})
    request, plan = scheduled_request_and_plan(original, costs, campaign_id=f"original-{entry}",
        providers=[Provider.AWS, Provider.AZURE, Provider.GCP], child_prefix=f"original-{entry}-run",
        namespace_prefix=f"original-{entry}-resources", funding={"primary": "0.5"})
    original_admission = book.reserve(request, plan=plan)
    assert original_admission.status == "reserved"
    rid = original_admission.evidence.reservation_id.value
    failed_run = f"original-{entry}-run-aws"
    failure = {"run_id": failed_run, "failed_run_id": failed_run, "failed_provider": "aws", "observation_id": entry}
    for provider in (Provider.AWS, Provider.AZURE, Provider.GCP):
        record_absence(book, f"cleanup-{entry}-{provider.value}", rid, provider, now + timedelta(minutes=2),
            failure=failure if provider == Provider.AWS else None)
    failure_event_id = f"cleanup-{entry}-aws"
    failure_link = {"original_reservation_id": rid, "failed_run_id": failed_run, "failed_provider": "aws",
        "observation_id": entry, "failure_event_id": failure_event_id,
        "failure_source_sha256": book.inspect()["events"][failure_event_id]["payload"]["source_sha256"]}

    config = ROOT / "configs/experiments" / filename
    row = scheduled_campaign(config, entry, now + timedelta(minutes=5))
    context = ReplacementContext(observation_id=entry, replaces_run_id=failed_run)
    selected = [Provider.AWS]
    replacement = row.model_copy(update={"benchmark": row.benchmark.model_copy(update={"common_n": frozen}),
        "context": "replacement", "replacement_context": context, "selected_providers": selected})
    replacement_request, replacement_plan = scheduled_request_and_plan(replacement, costs,
        campaign_id=f"replacement-{entry}", providers=selected, child_prefix=f"replacement-{entry}-run",
        namespace_prefix=f"replacement-{entry}-resources", funding={"replacements": "0.5"},
        original_failure=failure_link, observation_id=entry, measurement_seconds=measurement)
    replacement_request = replacement_request.model_copy(update={"active_reserved_usd": Evidence[float](value=0.5)})
    result = book.reserve(replacement_request, plan=replacement_plan)
    assert result.status in {"pending", "rejected"}
    assert not result.newly_reserved


def test_three_replacements_per_provider_across_s1_s3_then_refuses_fourth_and_chain(tmp_path):
    now=datetime(2026,10,22,tzinfo=timezone.utc)
    costs=load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json")
    book=ledger(tmp_path / "ledger.json")
    book.initialize(costs)
    configs=[ROOT/"configs/experiments/exp-001-multi-provider.yaml",ROOT/"configs/experiments/exp-003-placement-optimization.yaml"]
    providers=[Provider.AWS,Provider.AZURE,Provider.GCP]
    frozen=CommonNState(state="frozen",common_n=4,source_campaign_id="CAL-OBS",source_sha256="b"*64,plateau_found=True)
    originals=[]
    for index in range(4):
        config=configs[index%2]
        campaign=scheduled_campaign(config,f"ORIGINAL-{index}",now+timedelta(seconds=30))
        campaign=campaign.model_copy(update={"benchmark":campaign.benchmark.model_copy(update={"common_n":frozen})})
        request,plan=scheduled_request_and_plan(campaign,costs,campaign_id=f"original-{index}",providers=providers,
            child_prefix=f"original-{index}-run",namespace_prefix=f"original-{index}-resources",funding={"primary":"0.5"})
        request=request.model_copy(update={"active_reserved_usd":Evidence[float](value=index*1.0)})
        admitted=book.reserve(request,plan=plan)
        assert admitted.status=="reserved",admitted.reasons
        rid=admitted.evidence.reservation_id.value
        run_id=f"original-{index}-run-aws"
        failure={"run_id":run_id,"failed_run_id":run_id,"failed_provider":"aws","observation_id":f"OBS-{index}"}
        failure_source=None
        for provider in providers:
            observed=record_absence(book,f"cleanup-{index}-{provider.value}",rid,provider,now+timedelta(minutes=2),
                failure=failure if provider==Provider.AWS else None)
            if provider==Provider.AWS: failure_source=observed
        originals.append((rid,run_id,f"OBS-{index}",config))
        if index < 3:
            row=scheduled_campaign(config,f"REPLACEMENT-{index}-A",now+timedelta(minutes=5))
            context=ReplacementContext(observation_id=f"OBS-{index}",replaces_run_id=run_id)
            replacement=row.model_copy(update={"benchmark":row.benchmark.model_copy(update={"common_n":frozen}),
                "context":"replacement","replacement_context":context,"selected_providers":[Provider.AWS]})
            failure_link={"original_reservation_id":rid,"failed_run_id":run_id,"failed_provider":"aws",
                "observation_id":f"OBS-{index}","failure_event_id":f"cleanup-{index}-aws","failure_source_sha256":failure_source}
            rep_req,rep_plan=scheduled_request_and_plan(replacement,costs,campaign_id=f"replacement-{index}",
                providers=[Provider.AWS],child_prefix=f"replacement-{index}-run",namespace_prefix=f"replacement-{index}-resources",
                funding={"replacements":"0.5"},original_failure=failure_link,observation_id=f"OBS-{index}")
            rep_req=rep_req.model_copy(update={"active_reserved_usd":Evidence[float](value=(2*index+1)*.5)})
            replaced=book.reserve(rep_req,plan=rep_plan)
            assert replaced.status=="reserved",replaced.reasons
            if index==0:
                chain_row=scheduled_campaign(config,"REPLACEMENT-CHAIN",now+timedelta(minutes=7))
                chain_context=ReplacementContext(observation_id="OBS-CHAIN",replaces_run_id="replacement-0-run-aws")
                chain=chain_row.model_copy(update={"benchmark":chain_row.benchmark.model_copy(update={"common_n":frozen}),
                    "context":"replacement","replacement_context":chain_context,"selected_providers":[Provider.AWS]})
                chain_failure={"original_reservation_id":replaced.evidence.reservation_id.value,
                    "failed_run_id":"replacement-0-run-aws","failed_provider":"aws","observation_id":"OBS-CHAIN",
                    "failure_event_id":"does-not-exist","failure_source_sha256":costs.payload_sha256}
                chain_req,chain_plan=scheduled_request_and_plan(chain,costs,campaign_id="replacement-chain",
                    providers=[Provider.AWS],child_prefix="replacement-chain-run",namespace_prefix="replacement-chain-resources",
                    funding={"replacements":"0.5"},original_failure=chain_failure,observation_id="OBS-CHAIN")
                chain_req=chain_req.model_copy(update={"active_reserved_usd":Evidence[float](value=1.0)})
                chain_decision=book.reserve(chain_req,plan=chain_plan)
                assert chain_decision.status=="rejected"
                assert chain_decision.reasons==("identity_mismatch",)
    rid,run_id,observation,config=originals[3]
    row=scheduled_campaign(config,"REPLACEMENT-3-FOURTH",now+timedelta(minutes=10))
    context=ReplacementContext(observation_id=observation,replaces_run_id=run_id)
    replacement=row.model_copy(update={"benchmark":row.benchmark.model_copy(update={"common_n":frozen}),
        "context":"replacement","replacement_context":context,"selected_providers":[Provider.AWS]})
    failure_link={"original_reservation_id":rid,"failed_run_id":run_id,"failed_provider":"aws",
        "observation_id":observation,"failure_event_id":"cleanup-3-aws",
        "failure_source_sha256":book.inspect()["events"]["cleanup-3-aws"]["payload"]["source_sha256"]}
    req,plan=scheduled_request_and_plan(replacement,costs,campaign_id="replacement-fourth",
        providers=[Provider.AWS],child_prefix="replacement-fourth-run",namespace_prefix="replacement-fourth-resources",
        funding={"replacements":"0.5"},original_failure=failure_link,observation_id=observation)
    req=req.model_copy(update={"active_reserved_usd":Evidence[float](value=3.5)})
    rejected=book.reserve(req,plan=plan)
    assert rejected.status=="rejected"
    assert rejected.reasons==("optional_review_required",)


def test_past_short_slot_is_missed_without_catchup_or_ledger_mutation(tmp_path):
    now=datetime(2026,10,22,tzinfo=timezone.utc)
    costs=load_cost_inputs(ROOT/"tests/fixtures/f11/cost-inputs.json")
    book=ledger(tmp_path/"ledger.json")
    book.initialize(costs)
    campaign=scheduled_campaign(ROOT/"configs/experiments/exp-001-multi-provider.yaml","MISSED-S1",now-timedelta(seconds=1))
    frozen=CommonNState(state="frozen",common_n=4,source_campaign_id="CAL-OBS",source_sha256="c"*64,plateau_found=True)
    campaign=campaign.model_copy(update={"benchmark":campaign.benchmark.model_copy(update={"common_n":frozen})})
    request,plan=scheduled_request_and_plan(campaign,costs,campaign_id="missed-slot",providers=[Provider.AWS,Provider.AZURE,Provider.GCP],
        child_prefix="missed-run",namespace_prefix="missed-resource",funding={"primary":"0.5"})
    result=book.reserve(request,plan=plan)
    assert result.status=="missed" and result.reasons==("slot_missed",)
    assert book.inspect()["revision"]==1
    assert not book.inspect()["reservations"]


def test_exact_claim_retry_after_target_returns_original_reservation(tmp_path):
    now = [datetime(2026, 10, 22, tzinfo=timezone.utc)]
    costs = load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json")
    book = BudgetLedger(tmp_path / "ledger.json", scope_id="retry-scope", utc_now=lambda:now[0],
        monotonic_now=lambda:1.0, controller_session_id="retry-session")
    book.initialize(costs)
    campaign = scheduled_campaign(ROOT / "configs/experiments/exp-001-multi-provider.yaml", "RETRY-AFTER-TARGET", now[0]+timedelta(seconds=30))
    frozen = CommonNState(state="frozen", common_n=4, source_campaign_id="CAL-OBS", source_sha256="c"*64, plateau_found=True)
    campaign = campaign.model_copy(update={"benchmark":campaign.benchmark.model_copy(update={"common_n":frozen})})
    request, plan = scheduled_request_and_plan(campaign, costs, campaign_id="retry-target",
        providers=[Provider.AWS, Provider.AZURE, Provider.GCP], child_prefix="retry-run", namespace_prefix="retry-ns", funding={"primary":"0.5"})
    first = book.reserve(request, plan=plan)
    assert first.newly_reserved
    now[0] += timedelta(seconds=60)
    retry = book.reserve(request, plan=plan)
    assert retry.status == "reserved" and not retry.newly_reserved
    assert retry.evidence.reservation_id.value == first.evidence.reservation_id.value
    changed = plan.__class__(**{**plan.__dict__, "observation_id":"changed-prerequisite-context"})
    conflict = book.reserve(request, plan=changed)
    assert conflict.status == "rejected" and conflict.reasons == ("duplicate_conflict",)


def test_retained_calibration_admits_one_row_while_day_waits_for_common_n(tmp_path):
    now=datetime(2026,10,22,tzinfo=timezone.utc)
    costs=load_cost_inputs(ROOT/"tests/fixtures/f11/cost-inputs.json")
    providers=[Provider.AWS,Provider.AZURE,Provider.GCP]
    calibration_book=ledger(tmp_path/"calibration.json")
    calibration_book.initialize(costs)
    cal=scheduled_campaign(ROOT/"configs/experiments/exp-001-calibration.yaml","CAL-ONE-ROW",now+timedelta(seconds=30))
    cal_request,cal_plan=scheduled_request_and_plan(cal,costs,campaign_id="cal-one",providers=providers,
        child_prefix="cal-run",namespace_prefix="cal-resource",funding={"primary":"0.5"},measurement_seconds=951)
    cal_decision=calibration_book.reserve(cal_request,plan=cal_plan)
    assert cal_decision.status=="reserved"
    assert len(calibration_book.inspect()["reservations"])==1
    day_book=ledger(tmp_path/"day.json")
    day_book.initialize(costs)
    day=scheduled_campaign(ROOT/"configs/experiments/exp-001-diagnostic-day.yaml","DAY-ONE-ROW",now+timedelta(seconds=30))
    day_request,day_plan=scheduled_request_and_plan(day,costs,campaign_id="day-one",providers=providers,
        child_prefix="day-run",namespace_prefix="day-resource",funding={"primary":"0.5"},measurement_seconds=86408)
    day_decision=day_book.reserve(day_request,plan=day_plan)
    assert day_decision.status=="pending"
    assert "calibration_pending" in day_decision.reasons
    assert not day_book.inspect()["reservations"]


@pytest.mark.parametrize("config_name,mode,minimum", [
    ("tests/exp-903-three-provider-smoke.yaml", "acceptance", 76),
    ("experiments/exp-001-multi-provider.yaml", "short_study", 226),
    ("experiments/exp-001-calibration.yaml", "calibration", 951),
    ("experiments/exp-001-diagnostic-day.yaml", "diagnostic_day", 86408),
])
def test_claim_creation_uses_durable_mode_allowance(tmp_path, config_name, mode, minimum):
    now = datetime(2026, 10, 22, tzinfo=timezone.utc)
    costs = load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json")
    book = ledger(tmp_path / f"{mode}.json")
    book.initialize(costs)
    config_path = ROOT / ("configs/" + config_name if config_name.startswith("tests/") else "configs/" + config_name)
    if mode == "acceptance":
        campaign, _ = resolve_campaign(config_path, ROOT)
    else:
        campaign = scheduled_campaign(config_path, f"{mode}-row", now + timedelta(seconds=30))
    if mode in {"short_study", "diagnostic_day"}:
        frozen = CommonNState(state="frozen", common_n=4, source_campaign_id="CAL-OBS", source_sha256="c"*64, plateau_found=True)
        campaign = campaign.model_copy(update={"benchmark": campaign.benchmark.model_copy(update={"common_n": frozen})})
    providers = [Provider(p) for p in campaign.selected_providers]
    request, plan = scheduled_request_and_plan(campaign, costs, campaign_id=f"create-{mode}", providers=providers,
        child_prefix=f"create-{mode}-run", namespace_prefix=f"create-{mode}-ns", funding={"primary":"0.5"}, measurement_seconds=minimum)
    reserved = book.reserve(request, plan=plan)
    assert reserved.status == "reserved", reserved.reasons
    rid = reserved.evidence.reservation_id.value
    assert book.check_stage_fit(rid, stage="registration", remaining=plan.allowance).eligible
    omitted = WorkflowAllowance(10,0,plan.allowance.readiness_seconds,60,minimum,1,
        plan.allowance.retrieval_seconds,plan.allowance.cleanup_seconds,"mode-stage-test",plan.allowance.provider_leads_seconds)
    assert not book.check_stage_fit(rid, stage="registration", remaining=omitted).eligible
    measurement_remainder = WorkflowAllowance(0,0,0,0,minimum,1,plan.allowance.retrieval_seconds,
        plan.allowance.cleanup_seconds,"mode-stage-test",plan.allowance.provider_leads_seconds,("registration","preparation"))
    assert book.check_stage_fit(rid, stage="measurement", remaining=measurement_remainder).eligible
    expiry_requests, expiry_evidence = [], []
    for provider in providers:
        name = provider.value
        run_id = plan.child_identities[name]
        trigger = reserved.evidence.expires_at.value - timedelta(seconds=plan.allowance.provider_leads_seconds[name])
        action = "deallocate" if provider == Provider.AZURE else "stop"
        req = ExpiryRequest(schema_version=2, run_id=run_id, deployment_id=f"{run_id}-deployment", provider=provider,
            action=action, expires_at=Evidence[datetime](value=trigger), reservation_id=Evidence[str](value=rid),
            resource_namespace=plan.namespaces[name], target_selector=f"target-{name}")
        expiry_requests.append(req)
        expiry_evidence.append(ExpiryEvidence(schema_version=2, run_id=run_id, deployment_id=req.deployment_id,
            provider=provider, action=action, state="registered", registration_id=Evidence[str](value=f"registration-{name}"),
            registered_at=Evidence[datetime](value=now), expires_at=Evidence[datetime](value=trigger),
            target_selector=req.target_selector, provider_metadata=StructuredEvidence(value={"fixture":True})))
    decision = book.claim_creation(rid, provider=providers[0], expiry_requests=expiry_requests, expiry_evidence=expiry_evidence)
    assert decision.eligible and decision.creation_granted
    late = BudgetLedger(tmp_path / f"{mode}.json", scope_id="offline-scope", utc_now=lambda:now,
        monotonic_now=lambda:1.0 + request.lifetime_cap_seconds + 1, controller_session_id="session-1")
    expired = late.claim_creation(rid, provider=providers[0], expiry_requests=expiry_requests, expiry_evidence=expiry_evidence)
    assert not expired.eligible and "workflow_time_insufficient" in expired.reason_codes
    replay = book.claim_creation(rid, provider=providers[0], expiry_requests=expiry_requests, expiry_evidence=expiry_evidence)
    assert replay.eligible and not replay.creation_granted and "creation_already_claimed" in replay.reason_codes
    resumed = BudgetLedger(tmp_path / f"{mode}.json", scope_id="offline-scope", utc_now=lambda:now,
        monotonic_now=lambda:2.0, controller_session_id="new-controller-session")
    campaign2 = (resolve_campaign(config_path, ROOT)[0] if mode == "acceptance"
        else scheduled_campaign(config_path, f"{mode}-after-registration", now + timedelta(seconds=60)))
    if mode in {"short_study", "diagnostic_day"}:
        campaign2 = campaign2.model_copy(update={"benchmark":campaign2.benchmark.model_copy(update={"common_n":frozen})})
    request2, plan2 = scheduled_request_and_plan(campaign2, costs, campaign_id=f"create-{mode}-after-registration",
        providers=providers, child_prefix=f"after-registration-{mode}", namespace_prefix=f"after-registration-{mode}-ns",
        funding={"primary":"0.5"}, measurement_seconds=minimum)
    request2 = request2.model_copy(update={"outstanding_cleanup":True,"active_reserved_usd":Evidence[float](value=0.5)})
    blocked = resumed.reserve(request2, plan=plan2)
    assert blocked.status == "rejected" and blocked.reasons == ("controller_session_changed",)


@pytest.mark.parametrize("mutation", ["missing_component", "wrong_region", "underfunded_component"])
def test_cost_envelopes_reject_incomplete_or_mismatched_workflow_cost(mutation):
    costs = load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json")
    campaign, _ = resolve_campaign(ROOT / "configs/tests/exp-903-three-provider-smoke.yaml", ROOT)
    request, plan = scheduled_request_and_plan(campaign, costs, campaign_id=f"cost-{mutation}",
        providers=[Provider.AWS, Provider.AZURE, Provider.GCP], child_prefix=f"cost-{mutation}-run",
        namespace_prefix=f"cost-{mutation}-ns", funding={"primary":"0.5"})
    assert sum(admission_module._validate_cost_envelopes(plan, request)[1].values()) == Decimal("0.5")
    envelopes = {name: dict(row) for name, row in plan.cost_envelopes.items()}
    row = dict(envelopes["aws"])
    if mutation == "missing_component":
        row["components"] = dict(row["components"])
        del row["components"]["tax"]
    elif mutation == "wrong_region":
        row["regions"] = ["invented-region"]
    else:
        underfunded = plan.__class__(**{**plan.__dict__, "funding":{"primary":Decimal("0.49")}})
        with pytest.raises(ValueError, match="cost_envelope_invalid"):
            admission_module._validate_cost_envelopes(underfunded, request)
        return
    envelopes["aws"] = row
    with pytest.raises(ValueError, match="cost_envelope_invalid"):
        admission_module._validate_cost_envelopes(plan.__class__(**{**plan.__dict__, "cost_envelopes":envelopes}), request)


def test_saved_provider_price_envelopes_validate_without_launching_paid_work(tmp_path):
    costs = load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json")
    campaign, _ = resolve_campaign(ROOT / "configs/tests/exp-903-three-provider-smoke.yaml", ROOT)
    request, plan = scheduled_request_and_plan(campaign, costs, campaign_id="priced-boundary",
        providers=[Provider.AWS, Provider.AZURE, Provider.GCP], child_prefix="priced-run",
        namespace_prefix="priced-ns", funding={"primary":"0.5"})
    rows = make_synthetic_envelopes(campaign, plan.child_identities, plan.funding)
    public_rows = {name:{**{k:v for k,v in row.items() if k not in {"source_path","source_sha256"}},
        "source_kind":"provider-price-input"} for name,row in rows.items()}
    payload = dict(costs.payload)
    payload["provider_run_envelopes"] = public_rows
    priced_path = tmp_path / "dated-prices.json"
    priced_path.write_text(json.dumps(payload,sort_keys=True),encoding="utf-8")
    priced = load_cost_inputs(priced_path)
    envelopes = {name:{**row,"source_kind":"provider-price-input", "source_path":priced.source_path,
        "source_sha256":priced.payload_sha256} for name,row in rows.items()}
    request = request.model_copy(update={"source_path":priced.source_path,"source_sha256":priced.payload_sha256})
    paid_plan = plan.__class__(**{**plan.__dict__,"costs":priced,"execution_scope":"paid_request","cost_envelopes":envelopes})
    assert admission_module._validate_cost_envelopes(paid_plan,request)[0] == Decimal("0.5")


def test_saved_cost_bytes_must_match_at_initialize(tmp_path):
    source = tmp_path / "costs.json"
    source.write_bytes((ROOT / "tests/fixtures/f11/cost-inputs.json").read_bytes())
    costs = load_cost_inputs(source)
    source.write_text(source.read_text().replace('"primary_forecast": "147.82"', '"primary_forecast": "147.83"'))
    book = ledger(tmp_path / "ledger.json")
    with pytest.raises(ValueError, match="source_mismatch"):
        book.initialize(costs)
    source.unlink()
    with pytest.raises((ValueError, OSError)):
        book.initialize(costs)


def test_earlier_controller_session_blocks_from_reservation_activation(tmp_path):
    now = datetime(2026, 10, 22, tzinfo=timezone.utc)
    costs = load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json")
    original = ledger(tmp_path / "ledger.json")
    original.initialize(costs)
    campaign, _ = resolve_campaign(ROOT / "configs/tests/exp-903-three-provider-smoke.yaml", ROOT)
    request, plan = scheduled_request_and_plan(campaign, costs, campaign_id="pre-registration",
        providers=[Provider.AWS, Provider.AZURE, Provider.GCP], child_prefix="pre-registration-run",
        namespace_prefix="pre-registration-ns", funding={"primary":"0.5"})
    assert original.reserve(request, plan=plan).status == "reserved"
    resumed = BudgetLedger(tmp_path / "ledger.json", scope_id="offline-scope", utc_now=lambda:now,
        monotonic_now=lambda:2.0, controller_session_id="session-after-restart")
    campaign_b, _ = resolve_campaign(ROOT / "configs/tests/exp-903-three-provider-smoke.yaml", ROOT)
    request_b, plan_b = scheduled_request_and_plan(campaign_b, costs, campaign_id="after-restart",
        providers=[Provider.AWS, Provider.AZURE, Provider.GCP], child_prefix="after-restart-run",
        namespace_prefix="after-restart-ns", funding={"primary":"0.5"})
    request_b = request_b.model_copy(update={"outstanding_cleanup":True,
        "active_reserved_usd":Evidence[float](value=0.5)})
    blocked = resumed.reserve(request_b, plan=plan_b)
    assert blocked.status == "rejected" and blocked.reasons == ("controller_session_changed",)
    rid = next(iter(original.inspect()["reservations"]))
    for index, provider in enumerate((Provider.AWS, Provider.AZURE, Provider.GCP)):
        record_absence(original, f"restart-cleanup-{index}", rid, provider, now + timedelta(seconds=index))
    assert not original.inspect()["cleanup_open"]


def test_cost_source_switch_requires_review_and_preserves_old_allocations(tmp_path):
    source = tmp_path / "costs-v1.json"
    source.write_bytes((ROOT / "tests/fixtures/f11/cost-inputs.json").read_bytes())
    old = load_cost_inputs(source)
    book = ledger(tmp_path / "ledger.json")
    book.initialize(old)
    campaign, _ = resolve_campaign(ROOT / "configs/tests/exp-903-three-provider-smoke.yaml", ROOT)
    old_request, old_plan = scheduled_request_and_plan(campaign, old, campaign_id="old-cost-source",
        providers=[Provider.AWS,Provider.AZURE,Provider.GCP], child_prefix="old-source-run", namespace_prefix="old-source-ns",
        funding={"primary":"0.5"})
    assert book.reserve(old_request, plan=old_plan).status == "reserved"
    payload = dict(old.payload)
    payload["cost_input_id"] = "reviewed-next-source"
    payload["funding"] = dict(payload["funding"])
    payload["funding"]["primary"] = "200"
    next_path = tmp_path / "costs-v2.json"
    next_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    fresh = load_cost_inputs(next_path)
    changed_request = old_request.model_copy(update={"campaign_id":"changed-source-attempt", "source_path":fresh.source_path,
        "source_sha256":fresh.payload_sha256})
    changed_plan = old_plan.__class__(**{**old_plan.__dict__,"costs":fresh})
    assert book.reserve(changed_request, plan=changed_plan).reasons == ("source_mismatch",)
    evidence = {"evidence_id":"cost-source-review-2", "source_path":"review/source-change.json",
        "source_sha256":"f"*64, "reviewed_payload_sha256":fresh.payload_sha256,
        "scope_id":"offline-scope", "status":"reviewed"}
    with pytest.raises(ValueError, match="source_mismatch"):
        book.reconcile_cost_source(fresh, {**evidence, "reviewed_payload_sha256":"0"*64})
    updated = book.reconcile_cost_source(fresh, evidence)
    assert Decimal(updated["funding_remaining"]["primary"]) == Decimal("199.5")
    assert len(updated["source_history"]) == 3


def test_optional_rows_remain_offline_and_paid_request_needs_current_review_and_support(tmp_path):
    now=datetime(2026,10,22,tzinfo=timezone.utc)
    costs=load_cost_inputs(ROOT/"tests/fixtures/f11/cost-inputs.json")
    campaign=scheduled_campaign(ROOT/"configs/experiments/exp-002-cross-zone.yaml","OPTIONAL-P2",now+timedelta(seconds=30))
    providers=[Provider.AWS,Provider.AZURE,Provider.GCP]
    book=ledger(tmp_path/"optional.json")
    book.initialize(costs)
    request,plan=scheduled_request_and_plan(campaign,costs,campaign_id="optional-offline",providers=providers,
        child_prefix="optional-run",namespace_prefix="optional-resources",funding={"primary":"0.5"})
    blocked=book.reserve(request,plan=plan)
    assert blocked.status=="pending"
    assert "optional_review_required" in blocked.reasons
    frozen=CommonNState(state="frozen",common_n=4,source_campaign_id="CAL-OBS",source_sha256="d"*64,plateau_found=True)
    campaign=campaign.model_copy(update={"benchmark":campaign.benchmark.model_copy(update={"common_n":frozen})})
    request,plan=scheduled_request_and_plan(campaign,costs,campaign_id="optional-paid",providers=providers,
        child_prefix="optional-paid-run",namespace_prefix="optional-paid-resources",funding={"optional":"0.5"})
    evidence={key:{"evidence_id":key,"source_path":f"review/{key}.json","source_sha256":"e"*64}
        for key in ("execution_review","analysis_review","budget_review","primary_protection")}
    plan=plan.__class__(**{**plan.__dict__,"execution_scope":"paid_request","optional_reviewed":True,
        "optional_review_evidence":evidence})
    paid=book.reserve(request,plan=plan)
    assert paid.status=="rejected"
    assert paid.reasons == ("cost_envelope_invalid",)
    assert not book.inspect()["reservations"]


@pytest.mark.parametrize("damage", ["truncate", "remove_lock", "symlink"])
def test_initialized_scope_loss_or_corruption_fails_closed(tmp_path, damage):
    path = tmp_path / "ledger.json"
    book = ledger(path)
    book.initialize(load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json"))
    if damage == "truncate": path.write_text("{", encoding="utf-8")
    elif damage == "remove_lock": path.with_name("ledger.json.lock").unlink()
    else:
        path.unlink(); path.symlink_to(tmp_path / "other.json")
    with pytest.raises(ValueError):
        book.inspect()


def test_lock_timeout_is_bounded_and_never_resets_lock_marker(tmp_path):
    path=tmp_path/"ledger.json"
    costs=load_cost_inputs(ROOT/"tests/fixtures/f11/cost-inputs.json")
    book=ledger(path)
    book.initialize(costs)
    short=BudgetLedger(path,scope_id="offline-scope",utc_now=lambda:datetime(2026,10,22,tzinfo=timezone.utc),
        monotonic_now=lambda:1.0,controller_session_id="session-1",lock_timeout_seconds=.02)
    fd=os.open(path.with_name("ledger.json.lock"),os.O_RDWR)
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    try:
        with pytest.raises(TimeoutError,match="lock timeout"):
            short.inspect()
    finally:
        fcntl.flock(fd,fcntl.LOCK_UN)
        os.close(fd)
    assert book.inspect()["revision"]==1


def test_same_session_can_hold_two_independent_primary_reservations(tmp_path):
    path = tmp_path / "ledger.json"
    book = ledger(path)
    costs = load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json")
    book.initialize(costs)
    campaign, _ = resolve_campaign(ROOT / "configs/tests/exp-903-three-provider-smoke.yaml", ROOT)
    allowance = WorkflowAllowance(10, 20, 10, 60, 76, 1, 10, 10, "synthetic-test-policy",
        {"aws": 360, "azure": 60, "gcp": 60})
    def reserve(campaign_id):
        req = ReservationRequest(schema_version=2, campaign_id=campaign_id, config_id=campaign.config_id,
            schedule_entry_id=Evidence[str](unavailable_reason="acceptance has no scheduled row"),
            selected_providers=[Provider.AWS, Provider.AZURE, Provider.GCP], mode=campaign.mode,
            priority=campaign.admission.priority, lifetime_cap_seconds=1200,
            estimated_cost_usd=Evidence[float](value=.5), source_path=costs.source_path, source_sha256=costs.payload_sha256,
            incurred_cost_usd=Evidence[float](value=0), active_reserved_usd=Evidence[float](value=0), outstanding_cleanup=False)
        children = {p: f"{campaign_id}-{p}" for p in ("aws", "azure", "gcp")}; funding={"primary": Decimal("0.5")}
        plan = AdmissionPlan(campaign, children,
            {p: f"{campaign_id}-{p}-ns" for p in ("aws", "azure", "gcp")}, costs, allowance,
            funding=funding,cost_envelopes=make_synthetic_envelopes(campaign,children,funding))
        return req, plan
    req_a, plan_a = reserve("F11-primary-s1")
    req_b, plan_b = reserve("F11-primary-s3")
    first = book.reserve(req_a, plan=plan_a)
    assert first.evidence.cap_start_policy_id.value == "reservation-activation-before-provider-expiry"
    rid = first.evidence.reservation_id.value
    expiry_requests = []
    expiry_evidence = []
    for provider in (Provider.AWS, Provider.AZURE, Provider.GCP):
        name = provider.value
        run_id = plan_a.child_identities[name]
        trigger = first.evidence.expires_at.value - timedelta(seconds=allowance.provider_leads_seconds[name])
        action = "deallocate" if provider == Provider.AZURE else "stop"
        request = ExpiryRequest(schema_version=2, run_id=run_id, deployment_id=f"{run_id}-deployment", provider=provider,
            action=action, expires_at=Evidence[datetime](value=trigger), reservation_id=Evidence[str](value=rid),
            resource_namespace=plan_a.namespaces[name], target_selector=f"target-{name}")
        expiry_requests.append(request)
        expiry_evidence.append(ExpiryEvidence(schema_version=2, run_id=run_id, deployment_id=request.deployment_id,
            provider=provider, action=action, state="registered", registration_id=Evidence[str](value=f"registration-{name}"),
            registered_at=Evidence[datetime](value=datetime(2026,10,22,0,0,0,tzinfo=timezone.utc)),
            expires_at=Evidence[datetime](value=trigger), target_selector=request.target_selector,
            provider_metadata=StructuredEvidence(value={"synthetic": True})))
    missing = book.claim_creation(rid, provider=Provider.AWS, expiry_requests=expiry_requests[:-1], expiry_evidence=expiry_evidence[:-1])
    assert not missing.eligible and missing.reason_codes == ("expiry_required",)
    early_evidence=list(expiry_evidence)
    early_evidence[0]=early_evidence[0].model_copy(update={"registered_at":Evidence[datetime](value=datetime(2026,10,22,0,0,tzinfo=timezone.utc)-timedelta(seconds=1))})
    early=book.claim_creation(rid,provider=Provider.AWS,expiry_requests=expiry_requests,expiry_evidence=early_evidence)
    assert not early.eligible and early.reason_codes == ("expiry_mismatch",)
    wrong_requests=list(expiry_requests)
    wrong_requests[0]=wrong_requests[0].model_copy(update={"action":"deallocate"})
    wrong=book.claim_creation(rid,provider=Provider.AWS,expiry_requests=wrong_requests,expiry_evidence=expiry_evidence)
    assert not wrong.eligible and wrong.reason_codes == ("expiry_mismatch",)
    grant = book.claim_creation(rid, provider=Provider.AWS, expiry_requests=expiry_requests, expiry_evidence=expiry_evidence)
    duplicate_grant = book.claim_creation(rid, provider=Provider.AWS, expiry_requests=expiry_requests, expiry_evidence=expiry_evidence)
    assert grant.eligible and grant.creation_granted
    assert duplicate_grant.eligible and not duplicate_grant.creation_granted
    req_b = req_b.model_copy(update={"active_reserved_usd": Evidence[float](value=.5)})
    second = book.reserve(req_b, plan=plan_b)
    retry = book.reserve(req_a, plan=plan_a)
    assert first.status == second.status == "reserved"
    assert first.newly_reserved and second.newly_reserved
    assert not retry.newly_reserved
    state = book.inspect()
    assert len(state["reservations"]) == 2
    assert state["funding_remaining"]["primary"] == "99.40"
    unknown = Reconciliation("event-1", rid, Provider.AWS, costs.payload_sha256,
        incurred_usd=Decimal("0.10"), actual=False, billable_resources_present=None,
        occurred_at=datetime(2026,10,22,0,1,tzinfo=timezone.utc),source_path=costs.source_path)
    book.reconcile(unknown)
    with pytest.raises(ValueError, match="duplicate_conflict"):
        book.reconcile(Reconciliation("event-1", rid, Provider.AWS, costs.payload_sha256,
            incurred_usd=Decimal("0.11"), billable_resources_present=None,
            occurred_at=datetime(2026,10,22,0,1,tzinfo=timezone.utc),source_path=costs.source_path))
    book.reconcile(Reconciliation("event-1-final", rid, Provider.AWS, costs.payload_sha256,
        incurred_usd=Decimal("0.10"), actual=True, billing_final=True, billable_resources_present=None,
        occurred_at=datetime(2026,10,22,0,1,30,tzinfo=timezone.utc),source_path=costs.source_path))
    req_c, plan_c = reserve("F11-primary-third")
    assert book.reserve(req_c, plan=plan_c).reasons == ("cleanup_open",)
    for idx, provider in enumerate((Provider.AWS, Provider.AZURE, Provider.GCP), 2):
        record_absence(book,f"event-{idx}",rid,provider,datetime(2026,10,22,0,2+idx,tzinfo=timezone.utc))
    req_c = req_c.model_copy(update={"incurred_cost_usd": Evidence[float](value=.1),
        "active_reserved_usd": Evidence[float](value=.9)})
    assert book.reserve(req_c, plan=plan_c).status == "reserved"
