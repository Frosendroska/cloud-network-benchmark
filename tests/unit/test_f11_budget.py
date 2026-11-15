import hashlib
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from cloud_network_benchmark.admission import AdmissionPlan, BudgetLedger, WorkflowAllowance, load_cost_inputs, minimum_protocol_seconds, _decimal, _validate_paid_acceptance_evidence
from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.contracts.admission import ReservationRequest
from cloud_network_benchmark.contracts.common import Evidence, Provider
from cloud_network_benchmark.orchestration import _remaining_f11_allowance, _stage_budget_name
from tests.fixtures.f11.envelope_factory import make_synthetic_envelopes


ROOT = Path(__file__).parents[2]


def test_scoped_cost_input_hashes_saved_payload_and_keeps_forecast_dated():
    source = ROOT / "tests/fixtures/f11/cost-inputs.json"
    costs = load_cost_inputs(source)
    assert costs.payload_sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert costs.payload["funding"]["primary_forecast"] == "147.82"
    assert costs.prices_checked_on == "2026-10-14"
    assert costs.refresh_required
    assert costs.payload["live_quote"] is False


@pytest.mark.parametrize("field", ["prices_checked_on", "acquired_on"])
def test_cost_input_dates_require_exact_calendar_date_format(tmp_path, field):
    payload=json.loads((ROOT/"tests/fixtures/f11/cost-inputs.json").read_text())
    payload[field]="20261014"
    source=tmp_path/"compact-date.json"
    source.write_text(json.dumps(payload),encoding="utf-8")
    with pytest.raises(ValueError,match="dates must use ISO"):
        load_cost_inputs(source)


@pytest.mark.parametrize("value", [True, -1, "NaN", "Infinity"])
def test_money_rejects_boolean_negative_and_nonfinite(value):
    with pytest.raises(ValueError):
        _decimal(value)


@pytest.mark.parametrize("mutation", ["failed", "wrong_config", "wrong_mode", "wrong_providers"])
def test_paid_acceptance_evidence_requires_completed_matching_scope(mutation):
    campaign, _ = resolve_campaign(ROOT / "configs/experiments/exp-001-multi-provider.yaml", ROOT)
    evidence = {"evidence_id":"acceptance-result", "source_path":"evidence/acceptance.json",
        "source_sha256":"a"*64, "status":"completed", "config_id":campaign.config_id,
        "mode":"acceptance", "providers":["aws", "azure", "gcp"]}
    if mutation == "failed": evidence["status"] = "failed"
    if mutation == "wrong_config": evidence["config_id"] = "other-config"
    if mutation == "wrong_mode": evidence["mode"] = "calibration"
    if mutation == "wrong_providers": evidence["providers"] = ["aws"]
    with pytest.raises(ValueError, match="paid acceptance evidence"):
        _validate_paid_acceptance_evidence(evidence, campaign)
    good = {**evidence, "status":"completed", "config_id":campaign.config_id, "mode":"acceptance",
        "providers":["aws", "azure", "gcp"]}
    _validate_paid_acceptance_evidence(good, campaign)


@pytest.mark.parametrize(("working", "accepted"), [("250", True), ("250.0001", False), ("300", False)])
def test_full_remaining_and_reserved_liabilities_obey_working_and_total_caps(tmp_path, working, accepted):
    base = json.loads((ROOT / "tests/fixtures/f11/cost-inputs.json").read_text())
    base["funding"] = {"primary": working, "replacements": "0", "tax": "0", "uncertainty": "0"}
    source = tmp_path / "costs.json"
    source.write_text(json.dumps(base, sort_keys=True), encoding="utf-8")
    costs = load_cost_inputs(source)
    book = BudgetLedger(tmp_path / "ledger.json", scope_id="budget-test", utc_now=lambda: datetime(2026,10,22,tzinfo=timezone.utc),
        monotonic_now=lambda: 1.0, controller_session_id="session")
    book.initialize(costs)
    campaign, _ = resolve_campaign(ROOT / "configs/tests/exp-903-three-provider-smoke.yaml", ROOT)
    providers = [Provider.AWS, Provider.AZURE, Provider.GCP]
    allowance = WorkflowAllowance(10,20,10,60,76,1,10,10,"sourced-test-policy",{"aws":60,"azure":60,"gcp":60})
    request = ReservationRequest(schema_version=2,campaign_id="F11-budget-edge",config_id=campaign.config_id,
        schedule_entry_id=Evidence[str](unavailable_reason="acceptance has no scheduled row"),selected_providers=providers,
        mode=campaign.mode,priority=campaign.admission.priority,lifetime_cap_seconds=1200,
        estimated_cost_usd=Evidence[float](value=.5),source_path=costs.source_path,source_sha256=costs.payload_sha256,
        incurred_cost_usd=Evidence[float](value=0),active_reserved_usd=Evidence[float](value=0),outstanding_cleanup=False)
    children={p.value:f"{p.value}-child" for p in providers}; funding={"primary":Decimal("0.5")}
    plan = AdmissionPlan(campaign,children,{p.value:f"{p.value}-namespace" for p in providers},
        costs,allowance,funding=funding,cost_envelopes=make_synthetic_envelopes(campaign,children,funding))
    decision = book.reserve(request,plan=plan)
    assert (decision.status == "reserved") is accepted
    if accepted:
        remaining = sum(Decimal(v) for v in book.inspect()["funding_remaining"].values())
        active = sum(Decimal(v["reserved_cost_usd"]) for v in book.inspect()["reservations"].values())
        assert remaining + active == Decimal(working)
    else:
        assert decision.reasons == ("budget_exhausted",)
        assert book.inspect()["revision"] == 1


def test_preparation_can_overlap_slot_wait_but_wait_consumes_original_cap():
    start=datetime(2026,10,22,tzinfo=timezone.utc)
    allowance=WorkflowAllowance(10,20,10,60,76,1,10,10,"slot-policy",{"aws":60})
    assert allowance.fit_seconds(now=start,scheduled_start=start+timedelta(seconds=50)) == 197
    assert allowance.fit_seconds(now=start,scheduled_start=start+timedelta(seconds=200)) == 297
    assert allowance.fit_seconds(now=start,scheduled_start=start+timedelta(seconds=200),preparation_can_overlap=False) == 397


def test_mode_minimums_match_explicit_test_only_policy_cases():
    scenarios=json.loads((ROOT/"tests/fixtures/f11/scenarios.json").read_text())
    assert scenarios["fixture_scope"]=="synthetic-test-only"
    for mode,seconds in scenarios["minimum_protocol_seconds"].items():
        assert minimum_protocol_seconds(mode)==seconds


@pytest.mark.parametrize(("workflow_seconds","accepted"),[(1140,True),(1141,False)])
def test_complete_workflow_exact_earliest_trigger_boundary(tmp_path,workflow_seconds,accepted):
    costs=load_cost_inputs(ROOT/"tests/fixtures/f11/cost-inputs.json")
    book=BudgetLedger(tmp_path/f"ledger-{workflow_seconds}.json",scope_id="trigger-test",
        utc_now=lambda:datetime(2026,10,22,tzinfo=timezone.utc),monotonic_now=lambda:1.0,controller_session_id="s")
    book.initialize(costs)
    campaign,_=resolve_campaign(ROOT/"configs/tests/exp-903-three-provider-smoke.yaml",ROOT)
    providers=[Provider.AWS,Provider.AZURE,Provider.GCP]
    # Shared required work includes one second of shutdown grace; provision absorbs the remainder.
    fixed=10+10+60+76+1+10+10
    allowance=WorkflowAllowance(10,workflow_seconds-fixed,10,60,76,1,10,10,"trigger-policy",
        {provider.value:60 for provider in providers})
    request=ReservationRequest(schema_version=2,campaign_id=f"F11-trigger-{workflow_seconds}",config_id=campaign.config_id,
        schedule_entry_id=Evidence[str](unavailable_reason="acceptance has no scheduled row"),selected_providers=providers,
        mode=campaign.mode,priority=campaign.admission.priority,lifetime_cap_seconds=1200,
        estimated_cost_usd=Evidence[float](value=.5),source_path=costs.source_path,source_sha256=costs.payload_sha256,
        incurred_cost_usd=Evidence[float](value=0),active_reserved_usd=Evidence[float](value=0),outstanding_cleanup=False)
    children={p.value:f"{workflow_seconds}-{p.value}" for p in providers}; funding={"primary":Decimal("0.5")}
    plan=AdmissionPlan(campaign,children,{p.value:f"trigger-{workflow_seconds}-{p.value}" for p in providers},
        costs,allowance,funding=funding,cost_envelopes=make_synthetic_envelopes(campaign,children,funding))
    result=book.reserve(request,plan=plan)
    assert (result.status=="reserved")==accepted
    if not accepted:
        assert result.reasons==("workflow_time_insufficient",)
        assert book.inspect()["revision"]==1


def test_claim_stage_fit_uses_post_registration_remainder_at_recorded_aws_timing(tmp_path):
    costs = load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json")
    now = {"utc": datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc), "monotonic": 100.0}
    book = BudgetLedger(tmp_path / "claim-stage-fit.json", scope_id="claim-fit",
        utc_now=lambda: now["utc"], monotonic_now=lambda: now["monotonic"],
        controller_session_id="session")
    book.initialize(costs)
    campaign, _ = resolve_campaign(ROOT / "configs/tests/exp-900-single-provider.yaml", ROOT)
    allowance = WorkflowAllowance(135, 180, 180, 60, 100, 15, 45, 120,
        "recorded-aws-allowance", {"aws": 360})
    request = ReservationRequest(schema_version=2, campaign_id="claim-fit", config_id=campaign.config_id,
        schedule_entry_id=Evidence[str](unavailable_reason="unscheduled acceptance fixture"),
        selected_providers=[Provider.AWS], mode=campaign.mode,
        priority=campaign.admission.priority, lifetime_cap_seconds=1200,
        estimated_cost_usd=Evidence[float](value=.5), source_path=costs.source_path,
        source_sha256=costs.payload_sha256, incurred_cost_usd=Evidence[float](value=0),
        active_reserved_usd=Evidence[float](value=0), outstanding_cleanup=False)
    children = {"aws": "aws-child"}
    funding = {"primary": Decimal("0.5")}
    plan = AdmissionPlan(campaign, children, {"aws": "claim-fit-aws"}, costs, allowance,
        funding=funding, cost_envelopes=make_synthetic_envelopes(campaign, children, funding))
    decision = book.reserve(request, plan=plan)
    assert decision.status == "reserved"

    # Archive timing: registration readback ended 121.4 seconds into the cap;
    # 718.6 seconds then remained until the 840-second provider trigger.
    now["monotonic"] += 121.4
    reservation_id = decision.evidence.reservation_id.value
    old_full_registration_check = book.check_stage_fit(reservation_id,
        stage="registration", remaining=allowance)
    claim_phase = _stage_budget_name("claim")
    claim_remainder = _remaining_f11_allowance(allowance, claim_phase)
    corrected_claim_check = book.check_stage_fit(reservation_id,
        stage=claim_phase, remaining=claim_remainder)
    assert not old_full_registration_check.eligible
    assert old_full_registration_check.reason_codes == ("workflow_time_insufficient",)
    assert claim_phase == "preparation"
    assert claim_remainder.fit_seconds(now=now["utc"]) == 700
    assert corrected_claim_check.eligible


def test_post_provision_stage_fit_uses_exact_550_second_remainder_and_rejects_bad_markers(tmp_path):
    costs = load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json")
    now = {"utc": datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc), "monotonic": 100.0}
    book = BudgetLedger(tmp_path / "post-provision-fit.json", scope_id="post-provision-fit",
        utc_now=lambda: now["utc"], monotonic_now=lambda: now["monotonic"],
        controller_session_id="session")
    book.initialize(costs)
    campaign, _ = resolve_campaign(ROOT / "configs/tests/exp-900-single-provider.yaml", ROOT)
    allowance = WorkflowAllowance(105, 180, 210, 60, 100, 15, 45, 120,
        "post-provision-policy", {"aws": 360})
    request = ReservationRequest(schema_version=2, campaign_id="post-provision-fit",
        config_id=campaign.config_id,
        schedule_entry_id=Evidence[str](unavailable_reason="unscheduled acceptance fixture"),
        selected_providers=[Provider.AWS], mode=campaign.mode, priority=campaign.admission.priority,
        lifetime_cap_seconds=1200, estimated_cost_usd=Evidence[float](value=.5),
        source_path=costs.source_path, source_sha256=costs.payload_sha256,
        incurred_cost_usd=Evidence[float](value=0), active_reserved_usd=Evidence[float](value=0),
        outstanding_cleanup=False)
    children = {"aws": "aws-post-provision-child"}
    funding = {"primary": Decimal("0.5")}
    plan = AdmissionPlan(campaign, children, {"aws": "post-provision-aws"}, costs, allowance,
        funding=funding, cost_envelopes=make_synthetic_envelopes(campaign, children, funding))
    decision = book.reserve(request, plan=plan)
    assert decision.status == "reserved", decision.reasons
    reservation_id = decision.evidence.reservation_id.value
    now["monotonic"] = 390.0  # 550s remain to the sourced 840s provider trigger.

    post_provision = _remaining_f11_allowance(allowance, "post_provision")
    assert post_provision.total_seconds == 550
    assert not book.check_stage_fit(reservation_id, stage="preparation",
        remaining=_remaining_f11_allowance(allowance, "preparation")).eligible
    assert book.check_stage_fit(reservation_id, stage="post_provision", remaining=post_provision).eligible

    now["monotonic"] += 4.0  # 546s remain; the 550s remainder must not fit.
    insufficient = book.check_stage_fit(reservation_id, stage="post_provision", remaining=post_provision)
    assert not insufficient.eligible
    assert "workflow_time_insufficient" in insufficient.reason_codes

    now["monotonic"] = 390.0
    wrong_marker = WorkflowAllowance(0, 0, 210, 60, 100, 15, 45, 120,
        "post-provision-policy", {"aws": 360}, ("registration",))
    wrong_completed_fields = WorkflowAllowance(1, 0, 210, 60, 100, 15, 45, 120,
        "post-provision-policy", {"aws": 360}, ("registration", "provision"))
    missing_readiness = WorkflowAllowance(0, 0, 0, 60, 100, 15, 45, 120,
        "post-provision-policy", {"aws": 360}, ("registration", "provision"))
    reduced_readiness = WorkflowAllowance(0, 0, 209, 60, 100, 15, 45, 120,
        "post-provision-policy", {"aws": 360}, ("registration", "provision"))
    reduced_stabilization = WorkflowAllowance(0, 0, 210, 59, 100, 15, 45, 120,
        "post-provision-policy", {"aws": 360}, ("registration", "provision"))
    for invalid in (wrong_marker, wrong_completed_fields, missing_readiness,
                    reduced_readiness, reduced_stabilization):
        result = book.check_stage_fit(reservation_id, stage="post_provision", remaining=invalid)
        assert not result.eligible
        assert "workflow_time_insufficient" in result.reason_codes
