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
import subprocess
import sys


ROOT = Path(__file__).parents[2]


def ledger(path):
    return BudgetLedger(path, scope_id="offline-scope", utc_now=lambda: datetime(2026, 10, 22, tzinfo=timezone.utc),
        monotonic_now=lambda: 1.0, controller_session_id="session-1")


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
        plan = AdmissionPlan(campaign, {p: f"{campaign_id}-{p}" for p in ("aws", "azure", "gcp")},
            {p: f"{campaign_id}-{p}-ns" for p in ("aws", "azure", "gcp")}, costs, allowance,
            funding={"primary": Decimal("0.5")})
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
    grant = book.claim_creation(rid, provider=Provider.AWS, expiry_requests=expiry_requests, expiry_evidence=expiry_evidence)
    duplicate_grant = book.claim_creation(rid, provider=Provider.AWS, expiry_requests=expiry_requests, expiry_evidence=expiry_evidence)
    assert grant.eligible and grant.creation_granted
    assert duplicate_grant.eligible and not duplicate_grant.creation_granted
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
        occurred_at=datetime(2026,10,22,0,1,tzinfo=timezone.utc))
    book.reconcile(unknown)
    with pytest.raises(ValueError, match="duplicate_conflict"):
        book.reconcile(Reconciliation("event-1", rid, Provider.AWS, costs.payload_sha256,
            incurred_usd=Decimal("0.11"), billable_resources_present=None,
            occurred_at=datetime(2026,10,22,0,1,tzinfo=timezone.utc)))
    req_c, plan_c = reserve("F11-primary-third")
    assert book.reserve(req_c, plan=plan_c).reasons == ("cleanup_open",)
    for idx, provider in enumerate((Provider.AWS, Provider.AZURE, Provider.GCP), 2):
        book.reconcile(Reconciliation(f"event-{idx}", rid, provider, costs.payload_sha256,
            cleanup_verified_absent=True, billable_resources_present=False,
            occurred_at=datetime(2026,10,22,0,2+idx,tzinfo=timezone.utc)))
    assert book.reserve(req_c, plan=plan_c).status == "reserved"
