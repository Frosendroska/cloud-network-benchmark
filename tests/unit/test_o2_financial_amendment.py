"""Synthetic O2 policy fixtures; never human authority or provider approval."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from cloud_network_benchmark import financial_controls as fc
from tests.financial_helpers import synthetic_o2_amendment


NOW = datetime(2026, 11, 26, 10, 9, tzinfo=timezone.utc)
POLICY_END = datetime(2026, 11, 27, 7, 0, tzinfo=timezone.utc)
SCOPE = "SYNTHETIC-O2-SCOPE"


def _save(path, value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(raw)
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}


def _authority(tmp_path, monkeypatch, *, project="400", priority_values=("P0", "P1")):
    checked_at = NOW.isoformat().replace("+00:00", "Z")
    valid_until = POLICY_END.isoformat().replace("+00:00", "Z")
    checks = {"checked_at": checked_at, "valid_until": valid_until,
              "official_urls": ["https://example.invalid/synthetic-o2"]}

    runtime_ref = _save(tmp_path / "SYNTHETIC-runtime.json", {"fixture": "offline-only"})
    price_payload = {"currency": "USD", "refresh_required_before_paid_execution": False,
                     "prices_checked_on": "2026-11-26"}
    price_ref = _save(tmp_path / "SYNTHETIC-price.json", price_payload)
    fx_payload = {**checks, "currency": "USD", "budget_currency": "EUR",
                  "conservative_eur_per_unit": "1.01"}
    fx_ref = _save(tmp_path / "SYNTHETIC-fx.json", fx_payload)
    other_payload = {**checks, "ledger_scope_id": SCOPE, "currency": "EUR",
                     "gross_eur": "0", "personal_cash_eur": "0", "excludes_this_ledger": True}
    other_ref = _save(tmp_path / "SYNTHETIC-liabilities.json", other_payload)
    policy_ref = synthetic_o2_amendment(tmp_path / "SYNTHETIC-o2-policy.json")
    monkeypatch.setattr(fc, "_O2_AMENDMENT_SHA256", policy_ref["sha256"])

    runs = []
    entries = []
    for index, priority in enumerate(priority_values):
        run_ids = {provider: f"SYNTHETIC-{index}-{provider}" for provider in ("aws", "azure", "gcp")}
        runs.extend(run_ids.values())
        entries.append({
            "campaign_id": f"SYNTHETIC-O2-{priority}",
            "config_id": f"synthetic-{priority.lower()}",
            "config_sha256": "a" * 64,
            "resolved_sha256": "b" * 64,
            "mode": "acceptance",
            "priority": priority,
            "run_ids": run_ids,
            "namespaces": {provider: f"synthetic-{index}-{provider}" for provider in run_ids},
            "lifetime_cap_seconds": 4200,
            "cost_sha256": price_ref["sha256"],
            "cost_envelopes_sha256": "c" * 64,
            "allowance_sha256": "d" * 64,
            "runtime": {
                "implementation_revision": "e" * 40,
                "initialized_revisions": ["e" * 40],
                "runtime_source": runtime_ref,
                "effective_runtime_sha256": "f" * 64,
                "sources": [runtime_ref],
            },
            "resources": {provider: {"vms": 2, "disks": 2, "public_addresses": 2}
                          for provider in run_ids},
            # The approved readiness profile has no scheduled_start; paired
            # S1/S3 dispatch is bound by the frozen operator batch.
            "scheduled_start": None,
        })

    card = {
        "schema_version": 1, "kind": "explicit-human-paid-approval",
        "evidence_id": "SYNTHETIC-NONAUTHORIZING", "approved_by": "SYNTHETIC-OFFLINE",
        "approved_at": "2026-11-26T10:08:34Z", "valid_from": "2026-11-26T10:08:34Z",
        "valid_until": valid_until, "currency": "EUR", "ledger_scope_id": SCOPE,
        "batch_id": "fixed-pre-day-v1", "stage_id": "SYNTHETIC-O2-STAGE",
        "approved_run_count": len(runs), "entries": entries,
        "caps": {"project_eur": project, "working_eur": "302", "protected_eur": "0",
                 "personal_cash_eur": project, "run_eur": "8", "batch_eur": "233",
                 "stage_eur": "16", "providers_eur": {p: "16" for p in ("aws", "azure", "gcp")}},
        "price": {**price_ref, **checks}, "fx": {**fx_ref, **fx_payload},
        "external_liabilities": {**other_ref, **checks},
        "financial_policy_amendment": policy_ref,
        "other_gross_eur": "0", "other_personal_cash_eur": "0",
        "fx_card_fee_rate": "0.01", "fixed_fees_eur": "0.01",
        "funding_treatment": "full-personal-cash-no-offsets",
    }
    card_path = tmp_path / "SYNTHETIC-financial-card.json"
    card_ref = _save(card_path, card)
    source_reference = {"source_path": card_ref["path"], "source_sha256": card_ref["sha256"]}
    return card, source_reference, entries[0], policy_ref


def _validate(card_ref, binding, *, scope=SCOPE, now=NOW):
    if "source_path" not in card_ref:
        card_ref = {"source_path": card_ref["path"], "source_sha256": card_ref["sha256"]}
    return fc.validate_authority(card_ref, binding=binding, scope_id=scope,
        now=now, end=now + timedelta(seconds=4200))


def test_o2_policy_accepts_only_one_bound_six_run_normal_batch(tmp_path, monkeypatch):
    _card, reference, binding, _policy = _authority(tmp_path, monkeypatch)
    source, card = _validate(reference, binding)
    assert source.sha256 == reference["source_sha256"]
    assert card.caps.project_eur == "400"


@pytest.mark.parametrize("case, expected", [
    ("missing", "financial_ceiling_exceeded"),
    ("changed", "financial_source_changed"),
    ("wrong_scope", "financial_scope_mismatch"),
    ("expired", "financial_authorization_expired"),
    ("over_400", "financial_ceiling_exceeded"),
    ("over_working", "financial_ceiling_exceeded"),
    ("wrong_batch", "financial_scope_mismatch"),
    ("wrong_family_batch", "financial_scope_mismatch"),
])
def test_o2_amendment_rejects_invalid_or_out_of_scope_authority(
        tmp_path, monkeypatch, case, expected):
    card, reference, binding, policy_ref = _authority(tmp_path, monkeypatch)
    if case == "missing":
        card["financial_policy_amendment"] = None
    elif case == "changed":
        (tmp_path / "SYNTHETIC-o2-policy.json").write_text("{}\n")
    elif case == "wrong_scope":
        binding = {**binding, "run_ids": {"aws": "OTHER"}}
    elif case == "expired":
        now = datetime(2026, 11, 27, 5, 46, tzinfo=timezone.utc)
    elif case == "over_400":
        card["caps"]["project_eur"] = "401"
        card["caps"]["personal_cash_eur"] = "401"
    elif case == "over_working":
        card["caps"]["working_eur"] = "303"
    elif case == "wrong_batch":
        binding = {**binding, "priority": "P2"}
        card["entries"][0] = binding
    elif case == "wrong_family_batch":
        card["batch_id"] = "SYNTHETIC-SECOND-BATCH"
    card_ref = _save(tmp_path / "SYNTHETIC-financial-card.json", card)
    if case == "expired":
        with pytest.raises(fc.FinancialError, match=expected):
            _validate(card_ref, binding, now=now)
    else:
        with pytest.raises(fc.FinancialError, match=expected):
            _validate(card_ref, binding)


def test_o2_one_batch_identity_allows_second_parent_but_rejects_new_card():
    amendment = {"path": "/synthetic/o2.json", "sha256": "a" * 64}
    prior = {"authority": {"sha256": "b" * 64}, "batch_id": "fixed-pre-day-v1",
             "stage_id": "o2-stage", "financial_policy_amendment": amendment}
    same_card = {"authority": {"sha256": "b" * 64}, "batch_id": "fixed-pre-day-v1",
                 "stage_id": "o2-stage", "financial_policy_amendment": amendment}
    fc._validate_o2_batch_identity([prior], same_card)
    second_batch = {**same_card, "batch_id": "another-batch"}
    with pytest.raises(fc.FinancialError, match="financial_amendment_batch_consumed"):
        fc._validate_o2_batch_identity([prior], second_batch)
    reissued_card = {**same_card, "authority": {"sha256": "c" * 64}}
    with pytest.raises(fc.FinancialError, match="financial_amendment_batch_consumed"):
        fc._validate_o2_batch_identity([prior], reissued_card)


def test_o2_existing_lifecycle_uses_original_scope_and_never_extends_card_expiry(
        tmp_path, monkeypatch):
    _card, reference, binding, _policy = _authority(tmp_path, monkeypatch)
    later = datetime(2026, 11, 27, 6, 0, tzinfo=timezone.utc)
    fc.validate_authority(reference, binding=binding, scope_id=SCOPE, now=later,
        end=datetime(2026, 11, 27, 6, 50, tzinfo=timezone.utc),
        quote_freshness_at=datetime(2026, 11, 27, 5, 30, tzinfo=timezone.utc),
        existing_authorization=True)
    expired = datetime(2026, 11, 27, 7, 1, tzinfo=timezone.utc)
    with pytest.raises(fc.FinancialError, match="financial_authorization_expired"):
        fc.validate_authority(reference, binding=binding, scope_id=SCOPE, now=expired,
            end=expired + timedelta(minutes=1),
            quote_freshness_at=datetime(2026, 11, 27, 5, 30, tzinfo=timezone.utc),
            existing_authorization=True)


def test_o2_reserve_request_is_typed_acceptance_only_and_non_mutating(
        tmp_path, monkeypatch):
    import cloud_network_benchmark.campaign_inputs as inputs

    _card, source_ref, _binding, _policy = _authority(tmp_path, monkeypatch)
    request = {"status": "pending", "currency": "EUR", "prior_release_eur": "37",
               "additional_release_eur": "13", "cumulative_release_eur": "50"}
    settings = {
        "pricing": {"pending_eur_reserve_release": request},
        "approvals": {"paid_authorization": {"confirmed": True,
            "source": source_ref["source_path"], "sha256": source_ref["source_sha256"]}},
        "ledger": {"scope_id": SCOPE},
        "stage_inputs": {},
    }
    assert inputs._reserve_release_policy(settings, "acceptance", draft=True, now=NOW) == (request, None)
    release, snapshot = inputs._reserve_release_policy(settings, "acceptance", draft=False, now=NOW)
    assert release == request
    assert snapshot["caps"]["project_eur"] == "400"
    assert fc.has_explicit_eur_reserve_release(snapshot, now=NOW) is True
    with pytest.raises(ValueError, match="acceptance batch"):
        inputs._reserve_release_policy(settings, "calibration", draft=False, now=NOW)


def test_legacy_three_hundred_card_does_not_need_amendment(tmp_path, monkeypatch):
    card, _reference, binding, _policy = _authority(tmp_path, monkeypatch, project="300")
    card["financial_policy_amendment"] = None
    card["caps"].update(working_eur="240", protected_eur="60", personal_cash_eur="240")
    card_ref = _save(tmp_path / "SYNTHETIC-financial-card.json", card)
    reference = {"source_path": card_ref["path"], "source_sha256": card_ref["sha256"]}
    _source, validated = _validate(reference, binding)
    assert validated.caps.project_eur == "300"
