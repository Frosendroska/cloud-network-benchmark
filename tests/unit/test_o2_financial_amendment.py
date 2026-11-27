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


def _private_o3_sources():
    import os
    from pathlib import Path
    base = Path("/private/tmp/cnb-o3-authority-inputs-20261126")
    continuation = Path(os.environ.get("CNB_O3_CONTINUATION_SOURCE_PATH",
        base / "genuine-human-fixed-S1-S3-continuation-01.json"))
    amendment = Path(os.environ.get("CNB_O2_PRIVATE_POLICY_PATH",
        base / "genuine-human-budget-amendment-O2-02-EUR400.json"))
    guard = Path(os.environ.get("CNB_O3_LEGACY_USD_GUARD_SOURCE_PATH",
        base / "genuine-human-legacy-usd-guard-correction-01.json"))
    if not continuation.is_file() or not amendment.is_file() or not guard.is_file():
        pytest.skip("private, byte-verified O3 authority sources are not available")
    return continuation, amendment, guard


def _private_o4_retry_cap_removal_source():
    import os
    from pathlib import Path
    source = Path(os.environ.get("CNB_O4_RETRY_CAP_REMOVAL_SOURCE_PATH",
        "/private/tmp/cnb-o4-authority-inputs-20261126/genuine-human-retry-cap-removal-01.json"))
    if not source.is_file():
        pytest.skip("private, byte-verified O4 authority source is not available")
    return source


def _private_o9_sources():
    import os
    from pathlib import Path
    base = Path("/private/tmp/cnb-o9-authority-inputs-20261127")
    resume = Path(os.environ.get("CNB_O9_RESUME_SOURCE_PATH",
        base / "genuine-human-resume-instruction-01.json"))
    budget = Path(os.environ.get("CNB_O9_BUDGET_AMENDMENT_SOURCE_PATH",
        base / "genuine-human-budget-amendment-O9-EUR500-01.json"))
    timing = Path(os.environ.get("CNB_O9_TIMING_PROPOSAL_SOURCE_PATH",
        base / "derived-bounded-timing-proposal-O9-01.json"))
    if not resume.is_file() or not budget.is_file() or not timing.is_file():
        pytest.skip("private, byte-verified O9 objective, budget and timing sources are not available")
    expected = {
        resume: fc._O9_RESUME_SOURCE_SHA256,
        budget: fc._O9_BUDGET_AMENDMENT_SHA256,
        timing: fc._O9_TIMING_PROPOSAL_SHA256,
    }
    for path, digest in expected.items():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            pytest.fail("private O9 source bytes differ from production pins")
    return resume, budget, timing


def _o3_test_card(tmp_path, monkeypatch):
    import hashlib
    import json

    continuation_path, amendment_path, _guard_path = _private_o3_sources()
    continuation_sha = hashlib.sha256(continuation_path.read_bytes()).hexdigest()
    amendment_sha = hashlib.sha256(amendment_path.read_bytes()).hexdigest()
    assert continuation_sha == fc._O3_CONTINUATION_SHA256
    assert amendment_sha == fc._O2_AMENDMENT_SHA256
    card, _reference, binding, _policy = _authority(tmp_path, monkeypatch)
    for entry in card['entries']:
        entry['config_id'] = ('exp-903-acceptance' if entry['priority'] == 'P0' else 'exp-003-acceptance')
    binding = next(entry for entry in card['entries'] if entry['priority'] == 'P0')
    monkeypatch.setattr(fc, "_O2_AMENDMENT_SHA256", amendment_sha)
    card["financial_policy_amendment"] = {"path": str(amendment_path), "sha256": amendment_sha}
    card["continuation_authorization"] = {"path": str(continuation_path), "sha256": continuation_sha}
    card["caps"].update(project_eur="400", working_eur="400", protected_eur="0",
        personal_cash_eur="400", run_eur="8", batch_eur="400", stage_eur="16")
    path = tmp_path / "SYNTHETIC-O3-financial-card.json"
    path.write_text(json.dumps(card, sort_keys=True) + "\n")
    ref = {"source_path": str(path), "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return card, ref, binding


def test_o3_source_bound_card_uses_new_branch_without_changing_o2_limits(tmp_path, monkeypatch):
    from decimal import Decimal

    card, reference, binding = _o3_test_card(tmp_path, monkeypatch)
    source, parsed = _validate(reference, binding)
    assert source.sha256 == reference["source_sha256"]
    assert parsed.continuation_authorization.sha256 == fc._O3_CONTINUATION_SHA256
    assert parsed.caps.working_eur == "400" and parsed.caps.batch_eur == "400"
    snapshot = {"authority": {"path": reference["source_path"],
        "sha256": reference["source_sha256"]},
        "continuation_authorization": card["continuation_authorization"],
        "financial_policy_amendment": card["financial_policy_amendment"],
        "caps": card["caps"], "batch_id": card["batch_id"],
        "stage_id": card["stage_id"], "scope_id": card["ledger_scope_id"]}
    assert fc.has_explicit_eur_reserve_release(snapshot, now=NOW) is False
    assert fc.legacy_usd_budget_exceeded(Decimal("320"), snapshot, now=NOW) is False

    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    legacy, legacy_ref, legacy_binding, _ = _authority(legacy_root, monkeypatch)
    # `_authority` is deliberately synthetic; verify its original O2-shaped card
    # still uses the strict EUR302/EUR233 branch and one-batch identity.
    _, legacy_parsed = _validate(legacy_ref, legacy_binding)
    assert legacy_parsed.caps.working_eur == "302"
    assert legacy_parsed.caps.batch_eur == "233"


def test_o3_legacy_usd_guard_correction_is_source_bound_and_zero_reserve():
    from decimal import Decimal
    import cloud_network_benchmark.campaign_inputs as inputs

    continuation_path, _amendment_path, guard_path = _private_o3_sources()
    continuation = {"path": str(continuation_path),
        "sha256": hashlib.sha256(continuation_path.read_bytes()).hexdigest()}
    guard = {"path": str(guard_path),
        "sha256": hashlib.sha256(guard_path.read_bytes()).hexdigest()}
    settings = {"pricing": {"continuation_authorization": continuation,
            "legacy_usd_guard_correction": guard},
        "ledger": {"scope_id": "thesis-primary"}}
    pending, planning = inputs._reserve_release_policy(settings, "acceptance",
        draft=True, now=NOW)
    assert pending is None
    assert fc.has_o3_continuation_cost_source(planning, now=NOW)
    assert fc.legacy_usd_budget_exceeded(Decimal("304.2879978288326933322751497"),
        planning, now=NOW) is True
    assert fc.has_explicit_eur_reserve_release(planning, now=NOW) is False

    broken = {**settings, "pricing": {**settings["pricing"],
        "legacy_usd_guard_correction": {**guard, "sha256": "0" * 64}}}
    with pytest.raises(ValueError, match="exact approved continuation and correction"):
        inputs._reserve_release_policy(broken, "acceptance", draft=True, now=NOW)
    with pytest.raises(ValueError, match="limited to normal acceptance pricing"):
        inputs._reserve_release_policy(settings, "calibration", draft=True, now=NOW)
    with pytest.raises(ValueError, match="cannot be combined with a reserve-release request"):
        inputs._reserve_release_policy({**settings, "pricing": {**settings["pricing"],
            "pending_eur_reserve_release": {"status": "pending"}}},
            "acceptance", draft=True, now=NOW)


def test_o3_usd_overage_requires_exact_typed_card_for_non_draft(tmp_path, monkeypatch):
    from decimal import Decimal
    import cloud_network_benchmark.campaign_inputs as inputs

    card, card_ref, _binding = _o3_test_card(tmp_path, monkeypatch)
    continuation_path, _amendment_path, guard_path = _private_o3_sources()
    continuation = {"path": str(continuation_path),
        "sha256": hashlib.sha256(continuation_path.read_bytes()).hexdigest()}
    guard = {"path": str(guard_path),
        "sha256": hashlib.sha256(guard_path.read_bytes()).hexdigest()}
    settings = {"pricing": {"continuation_authorization": continuation,
            "legacy_usd_guard_correction": guard},
        "ledger": {"scope_id": card["ledger_scope_id"]},
        "approvals": {"paid_authorization": {"confirmed": True,
            "source": card_ref["source_path"], "sha256": card_ref["source_sha256"]}}}
    pending, snapshot = inputs._reserve_release_policy(settings, "acceptance",
        draft=False, now=NOW)
    assert pending is None
    assert snapshot["authority"]["sha256"] == card_ref["source_sha256"]
    assert snapshot["continuation_authorization"] == card["continuation_authorization"]
    assert fc.has_explicit_eur_reserve_release(snapshot, now=NOW) is False
    inputs._validate_funding_budget({"primary": Decimal("218.1738760752144690264550353"),
        "replacements": Decimal("0"), "tax": Decimal("11.16189295488931216931216931"),
        "uncertainty": Decimal("74.95222879872891213650794506"),
        "total_limit": Decimal("300"), "working_limit": Decimal("250"),
        "protected_buffer": Decimal("50")}, pending, snapshot, draft=False, now=NOW)

    source_only = {"pricing": settings["pricing"], "ledger": settings["ledger"]}
    with pytest.raises(ValueError, match="requires its exact typed paid_authorization"):
        inputs._reserve_release_policy(source_only, "acceptance", draft=False, now=NOW)

    legacy_root = tmp_path / "legacy-above-300"
    legacy_root.mkdir()
    legacy, legacy_ref, _legacy_binding, _policy = _authority(legacy_root, monkeypatch)
    legacy_snapshot = {"authority": {"path": legacy_ref["source_path"],
        "sha256": legacy_ref["source_sha256"]}, "caps": legacy["caps"],
        "batch_id": legacy["batch_id"], "stage_id": legacy["stage_id"],
        "scope_id": legacy["ledger_scope_id"]}
    with pytest.raises(ValueError, match="without source-bound O3 continuation"):
        inputs._validate_funding_budget({"primary": Decimal("250"), "replacements": Decimal("0"),
            "tax": Decimal("25"), "uncertainty": Decimal("30"), "total_limit": Decimal("300"),
            "working_limit": Decimal("250"), "protected_buffer": Decimal("50")},
            None, legacy_snapshot, draft=False, now=NOW)


@pytest.mark.parametrize("mutation,expected", [
    ("wrong_source", "financial_amendment_invalid"),
    ("new_reserve", "financial_ceiling_exceeded"),
    ("calibration", "financial_scope_mismatch"),
    ("over_fresh", "financial_ceiling_exceeded"),
])
def test_o3_continuation_rejects_wrong_source_reserve_scope_and_caps(
        tmp_path, monkeypatch, mutation, expected):
    import copy
    import hashlib
    import json

    card, _reference, binding = _o3_test_card(tmp_path, monkeypatch)
    mutated = copy.deepcopy(card)
    if mutation == "wrong_source":
        mutated["continuation_authorization"]["sha256"] = "0" * 64
    elif mutation == "new_reserve":
        mutated["caps"]["protected_eur"] = "1"
    elif mutation == "calibration":
        mutated["entries"][0]["mode"] = "calibration"
        binding = mutated["entries"][0]
    elif mutation == "over_fresh":
        mutated["caps"]["stage_eur"] = "17"
    path = tmp_path / f"SYNTHETIC-O3-{mutation}.json"
    path.write_text(json.dumps(mutated, sort_keys=True) + "\n")
    reference = {"source_path": str(path), "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    with pytest.raises(fc.FinancialError, match=expected):
        _validate(reference, binding)


def test_o3_source_without_original_o2_amendment_is_rejected(tmp_path, monkeypatch):
    import hashlib
    import json

    card, _reference, binding = _o3_test_card(tmp_path, monkeypatch)
    card["financial_policy_amendment"] = None
    path = tmp_path / "SYNTHETIC-O3-missing-base-amendment.json"
    path.write_text(json.dumps(card, sort_keys=True) + "\n")
    reference = {"source_path": str(path), "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    with pytest.raises(fc.FinancialError, match="financial_amendment_invalid"):
        _validate(reference, binding)


def test_o3_shared_finance_guard_fails_closed_without_retained_o1_o2_history():
    snapshot = {
        "authority": {"sha256": "a" * 64},
        "continuation_authorization": {"path": "/private/policy.json",
            "sha256": fc._O3_CONTINUATION_SHA256},
        "batch_id": "fixed-pre-day-v1", "stage_id": "O3-stage",
        "scope_id": "SYNTHETIC-SCOPE", "caps": {"stage_eur": "16"},
        "binding": {"campaign_id": "SYNTHETIC-O3"},
    }
    with pytest.raises(fc.FinancialError, match="financial_continuation_history_invalid"):
        fc._validate_o3_continuation_history({"reservations": {}}, snapshot)


def test_o3_epoch_selection_ignores_older_fixed_family_cards_with_larger_stages():
    o1, o2 = fc._O3_EPOCH_ANCHORS
    candidate = ("candidate-authority", "candidate-stage")
    historical = ("older-authority", "older-stage")
    groups = {
        o1: [("o1-a", {"financial": {"caps": {"stage_eur": "16"}}}),
             ("o1-b", {"financial": {"caps": {"stage_eur": "16"}}})],
        o2: [("o2-a", {"financial": {"caps": {"stage_eur": "16"}}}),
             ("o2-b", {"financial": {"caps": {"stage_eur": "16"}}})],
        historical: [(f"old-{index}", {"financial": {
            "caps": {"stage_eur": "32"}, "entries": ["one", "two", "three"]}})
            for index in range(3)],
        candidate: [("candidate", {"financial": {"continuation_authorization": {
            "path": "/private/o3.json", "sha256": fc._O3_CONTINUATION_SHA256}}})],
    }
    assert fc._o3_epoch_group_keys(groups, candidate) == {o1, o2}


def test_o3_reissued_exact_card_may_share_amendment_only_with_pinned_continuation():
    amendment = {"path": "/private/o2.json", "sha256": fc._O2_AMENDMENT_SHA256}
    prior = {"authority": {"sha256": "b" * 64}, "batch_id": "fixed-pre-day-v1",
        "stage_id": "O2-stage", "financial_policy_amendment": amendment}
    successor = {"authority": {"sha256": "c" * 64}, "batch_id": "fixed-pre-day-v1",
        "stage_id": "O3-stage", "financial_policy_amendment": amendment,
        "continuation_authorization": {"path": "/private/o3.json",
            "sha256": fc._O3_CONTINUATION_SHA256}}
    fc._validate_o2_batch_identity([prior], successor)
    with pytest.raises(fc.FinancialError, match="financial_amendment_batch_consumed"):
        fc._validate_o2_batch_identity([prior], {**successor,
            "continuation_authorization": {"sha256": "0" * 64}})
    with pytest.raises(fc.FinancialError, match="financial_amendment_batch_consumed"):
        fc._validate_o2_batch_identity([prior], {**successor,
            "batch_id": "SYNTHETIC-NEW-FAMILY"})


def test_o4_exact_source_removes_only_the_separate_48_epoch_cap(tmp_path):
    import hashlib
    import json
    from decimal import Decimal

    source_path = _private_o4_retry_cap_removal_source()
    source = fc.Source(path=str(source_path), sha256=hashlib.sha256(source_path.read_bytes()).hexdigest())
    assert fc.validate_o4_retry_cap_removal_source(source).sha256 == fc._O4_RETRY_CAP_REMOVAL_SHA256

    # Legacy O3 remains at 48. The genuine amendment removes only that separate
    # allocation epoch; the EUR400 inclusive ledger ceiling still governs admission.
    fc._validate_o3_epoch_allocation(Decimal("48"), Decimal("16"), None)
    with pytest.raises(fc.FinancialError, match="financial_continuation_exposure_exceeded"):
        fc._validate_o3_epoch_allocation(Decimal("49"), Decimal("16"), None)
    fc._validate_o3_epoch_allocation(Decimal("64"), Decimal("16"), source)
    with pytest.raises(fc.FinancialError, match="financial_continuation_exposure_exceeded"):
        fc._validate_o3_epoch_allocation(Decimal("65"), Decimal("17"), source)

    changed = json.loads(source_path.read_text())
    changed["authority_change"]["new_reserve_release_eur"] = "1"
    changed_path = tmp_path / "mutated-o4-source.json"
    changed_path.write_text(json.dumps(changed, sort_keys=True) + "\n")
    changed_ref = fc.Source(path=str(changed_path), sha256=hashlib.sha256(changed_path.read_bytes()).hexdigest())
    with pytest.raises(fc.FinancialError, match="financial_amendment_invalid"):
        fc._validate_o3_epoch_allocation(Decimal("64"), Decimal("16"), changed_ref)


def test_o4_source_must_be_bound_to_the_typed_continuation_card(tmp_path, monkeypatch):
    import hashlib
    import json

    card, _reference, binding = _o3_test_card(tmp_path, monkeypatch)
    source_path = _private_o4_retry_cap_removal_source()
    source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    card["continuation_exposure_amendment"] = {"path": str(source_path), "sha256": source_sha}
    path = tmp_path / "SYNTHETIC-O3-O4-financial-card.json"
    path.write_text(json.dumps(card, sort_keys=True) + "\n")
    reference = {"source_path": str(path), "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    _source, parsed = _validate(reference, binding)
    assert parsed.continuation_exposure_amendment.sha256 == fc._O4_RETRY_CAP_REMOVAL_SHA256

    card["continuation_exposure_amendment"]["sha256"] = "0" * 64
    path.write_text(json.dumps(card, sort_keys=True) + "\n")
    reference["source_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(fc.FinancialError, match="financial_amendment_invalid"):
        _validate(reference, binding)


def test_o4_pricing_requires_the_exact_typed_paid_card_source(tmp_path, monkeypatch):
    import hashlib
    import json
    import cloud_network_benchmark.campaign_inputs as inputs

    card, _card_ref, _binding = _o3_test_card(tmp_path, monkeypatch)
    continuation_path, _amendment_path, guard_path = _private_o3_sources()
    exposure_path = _private_o4_retry_cap_removal_source()
    continuation = {"path": str(continuation_path),
        "sha256": hashlib.sha256(continuation_path.read_bytes()).hexdigest()}
    guard = {"path": str(guard_path),
        "sha256": hashlib.sha256(guard_path.read_bytes()).hexdigest()}
    exposure = {"path": str(exposure_path),
        "sha256": hashlib.sha256(exposure_path.read_bytes()).hexdigest()}
    pricing = {"continuation_authorization": continuation,
        "legacy_usd_guard_correction": guard,
        "continuation_exposure_amendment": exposure}
    settings = {"pricing": pricing, "ledger": {"scope_id": card["ledger_scope_id"]}}
    _pending, draft_snapshot = inputs._reserve_release_policy(settings, "acceptance",
        draft=True, now=NOW)
    assert draft_snapshot["continuation_exposure_amendment"] == exposure

    card["continuation_exposure_amendment"] = exposure
    card_path = tmp_path / "O4-typed-card.json"
    card_path.write_text(json.dumps(card, sort_keys=True) + "\n")
    paid_settings = {**settings, "approvals": {"paid_authorization": {
        "confirmed": True, "source": str(card_path),
        "sha256": hashlib.sha256(card_path.read_bytes()).hexdigest()}}}
    _pending, paid_snapshot = inputs._reserve_release_policy(paid_settings,
        "acceptance", draft=False, now=NOW)
    assert paid_snapshot["continuation_exposure_amendment"] == exposure

    card.pop("continuation_exposure_amendment")
    card_path.write_text(json.dumps(card, sort_keys=True) + "\n")
    paid_settings["approvals"]["paid_authorization"]["sha256"] = hashlib.sha256(card_path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="exact current EUR400 typed card"):
        inputs._reserve_release_policy(paid_settings, "acceptance", draft=False, now=NOW)


@pytest.mark.parametrize("mutation,expected", [
    ("wrong_exposure_source", "financial_amendment_invalid"),
    ("wrong_ceiling_amendment", "financial_amendment_invalid"),
    ("over_project", "financial_ceiling_exceeded"),
    ("over_stage", "financial_ceiling_exceeded"),
    ("over_run", "financial_ceiling_exceeded"),
    ("new_reserve", "financial_ceiling_exceeded"),
    ("changed_case", "financial_scope_mismatch"),
])
def test_o4_source_does_not_expand_existing_card_limits(
        tmp_path, monkeypatch, mutation, expected):
    import copy
    import hashlib
    import json

    card, _reference, binding = _o3_test_card(tmp_path, monkeypatch)
    exposure_path = _private_o4_retry_cap_removal_source()
    card["continuation_exposure_amendment"] = {
        "path": str(exposure_path), "sha256": hashlib.sha256(exposure_path.read_bytes()).hexdigest()}
    changed = copy.deepcopy(card)
    if mutation == "wrong_exposure_source":
        changed["continuation_exposure_amendment"]["sha256"] = "0" * 64
    elif mutation == "wrong_ceiling_amendment":
        changed["financial_policy_amendment"]["sha256"] = "0" * 64
    elif mutation == "over_project":
        changed["caps"]["project_eur"] = "401"
        changed["caps"]["personal_cash_eur"] = "401"
    elif mutation == "over_stage":
        changed["caps"]["stage_eur"] = "17"
    elif mutation == "over_run":
        changed["caps"]["run_eur"] = "9"
    elif mutation == "new_reserve":
        changed["caps"]["protected_eur"] = "1"
    elif mutation == "changed_case":
        changed["entries"][0]["config_id"] = "exp-004-acceptance"
        binding = changed["entries"][0]
    path = tmp_path / f"SYNTHETIC-O4-{mutation}.json"
    path.write_text(json.dumps(changed, sort_keys=True) + "\n")
    reference = {"source_path": str(path), "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    with pytest.raises(fc.FinancialError, match=expected):
        _validate(reference, binding)


def _o9_test_card(tmp_path, monkeypatch):
    import copy
    from pathlib import Path

    end = datetime(2026, 11, 28, 7, 0, tzinfo=timezone.utc)
    dispatch_start = datetime(2026, 11, 27, 8, 42, 48, tzinfo=timezone.utc)
    monkeypatch.setattr(__import__(__name__, fromlist=["POLICY_END"]), "POLICY_END", end)
    card, _reference, binding = _o3_test_card(tmp_path, monkeypatch)
    resume_path, budget_path, timing_path = _private_o9_sources()
    exposure_path = _private_o4_retry_cap_removal_source()
    card["continuation_resume_source"] = {"path": str(resume_path),
        "sha256": hashlib.sha256(resume_path.read_bytes()).hexdigest()}
    card["continuation_budget_amendment"] = {"path": str(budget_path),
        "sha256": hashlib.sha256(budget_path.read_bytes()).hexdigest()}
    card["continuation_timing_proposal"] = {"path": str(timing_path),
        "sha256": hashlib.sha256(timing_path.read_bytes()).hexdigest()}
    card["continuation_exposure_amendment"] = {"path": str(exposure_path),
        "sha256": hashlib.sha256(exposure_path.read_bytes()).hexdigest()}
    card["approved_at"] = "2026-11-27T08:42:49Z"
    card["valid_from"] = dispatch_start.isoformat()
    card["valid_until"] = end.isoformat()
    card["caps"].update(project_eur="500", working_eur="500", protected_eur="0",
        personal_cash_eur="500", batch_eur="500", stage_eur="16", run_eur="8",
        providers_eur={provider: "500" for provider in ("aws", "azure", "gcp")})
    path = tmp_path / "SYNTHETIC-O9-financial-card.json"
    path.write_text(json.dumps(card, sort_keys=True) + "\n")
    reference = {"source_path": str(path),
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return copy.deepcopy(card), reference, binding, dispatch_start


def test_o9_exact_sources_bind_new_ceiling_and_bounded_window(tmp_path, monkeypatch):
    from decimal import Decimal

    resume_path, budget_path, timing_path = _private_o9_sources()
    resume = fc.Source(path=str(resume_path), sha256=fc._O9_RESUME_SOURCE_SHA256)
    budget = fc.Source(path=str(budget_path), sha256=fc._O9_BUDGET_AMENDMENT_SHA256)
    timing = fc.Source(path=str(timing_path), sha256=fc._O9_TIMING_PROPOSAL_SHA256)
    assert fc.validate_o9_resume_source(resume) == resume
    assert fc.validate_o9_budget_amendment_source(budget) == budget
    start, dispatch_end, execution_end = fc.validate_o9_timing_proposal_source(
        timing, budget_amendment=budget, resume_source=resume)
    assert start == datetime(2026, 11, 27, 8, 42, 48, tzinfo=timezone.utc)
    assert dispatch_end == datetime(2026, 11, 28, 5, 45, tzinfo=timezone.utc)
    assert execution_end == datetime(2026, 11, 28, 7, 0, tzinfo=timezone.utc)

    card, reference, binding, _start = _o9_test_card(tmp_path, monkeypatch)
    now = datetime(2026, 11, 27, 8, 43, tzinfo=timezone.utc)
    _source, parsed = fc.validate_authority(reference, binding=binding, scope_id=SCOPE,
        now=now, end=now + timedelta(seconds=4200))
    assert parsed.caps.project_eur == "500"
    assert parsed.caps.personal_cash_eur == "500"
    assert parsed.caps.stage_eur == "16" and parsed.caps.run_eur == "8"
    assert fc._validate_o3_epoch_allocation(Decimal("64"), Decimal("16"),
        fc.Source(path=str(_private_o4_retry_cap_removal_source()),
            sha256=fc._O4_RETRY_CAP_REMOVAL_SHA256)) is None

    import cloud_network_benchmark.campaign_inputs as inputs
    _resume_path, _budget_path, _timing_path = _private_o9_sources()
    _continuation_path, _amendment_path, guard_path = _private_o3_sources()
    settings = {"pricing": {
        "continuation_authorization": card["continuation_authorization"],
        "continuation_exposure_amendment": card["continuation_exposure_amendment"],
        "continuation_resume_source": card["continuation_resume_source"],
        "continuation_budget_amendment": card["continuation_budget_amendment"],
        "continuation_timing_proposal": card["continuation_timing_proposal"],
        "legacy_usd_guard_correction": {"path": str(guard_path),
            "sha256": hashlib.sha256(guard_path.read_bytes()).hexdigest()}},
        "ledger": {"scope_id": SCOPE}}
    _pending, planning = inputs._reserve_release_policy(settings, "acceptance",
        draft=True, now=now)
    assert _pending is None and fc.has_o3_continuation_cost_source(planning, now=now)
    paid_settings = {**settings, "approvals": {"paid_authorization": {
        "confirmed": True, "source": reference["source_path"],
        "sha256": reference["source_sha256"]}}}
    _pending, paid_snapshot = inputs._reserve_release_policy(paid_settings,
        "acceptance", draft=False, now=now)
    assert _pending is None and fc._has_source_bound_o3_continuation(paid_snapshot, now=now)
    assert fc.legacy_usd_budget_exceeded(Decimal("304.2879978288326933322751497"),
        paid_snapshot, now=now) is False
    assert fc.has_explicit_eur_reserve_release(paid_snapshot, now=now) is False


@pytest.mark.parametrize("mutation,expected", [
    ("missing_timing", "financial_amendment_invalid"),
    ("wrong_budget_source", "financial_amendment_invalid"),
    ("over_500", "financial_ceiling_exceeded"),
    ("over_stage", "financial_ceiling_exceeded"),
    ("over_run", "financial_ceiling_exceeded"),
    ("new_reserve", "financial_ceiling_exceeded"),
    ("changed_case", "financial_scope_mismatch"),
    ("expired_dispatch", "financial_authorization_expired"),
])
def test_o9_amendment_does_not_expand_caps_or_change_scope(
        tmp_path, monkeypatch, mutation, expected):
    import copy

    card, _reference, binding, _start = _o9_test_card(tmp_path, monkeypatch)
    changed = copy.deepcopy(card)
    now = datetime(2026, 11, 27, 8, 43, tzinfo=timezone.utc)
    if mutation == "missing_timing":
        changed.pop("continuation_timing_proposal")
    elif mutation == "wrong_budget_source":
        changed["continuation_budget_amendment"]["sha256"] = "0" * 64
    elif mutation == "over_500":
        changed["caps"].update(project_eur="501", working_eur="501",
            personal_cash_eur="501")
    elif mutation == "over_stage":
        changed["caps"]["stage_eur"] = "17"
    elif mutation == "over_run":
        changed["caps"]["run_eur"] = "9"
    elif mutation == "new_reserve":
        changed["caps"]["protected_eur"] = "1"
    elif mutation == "changed_case":
        changed["entries"][0]["config_id"] = "exp-004-acceptance"
        binding = changed["entries"][0]
    elif mutation == "expired_dispatch":
        now = datetime(2026, 11, 28, 5, 46, tzinfo=timezone.utc)
    path = tmp_path / f"SYNTHETIC-O9-{mutation}.json"
    path.write_text(json.dumps(changed, sort_keys=True) + "\n")
    reference = {"source_path": str(path),
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    with pytest.raises(fc.FinancialError, match=expected):
        _validate(reference, binding, now=now)


def test_legacy_o3_card_keeps_eur400_ceiling_without_o9_amendment(tmp_path, monkeypatch):
    card, _reference, binding = _o3_test_card(tmp_path, monkeypatch)
    card["caps"].update(project_eur="401", working_eur="401", personal_cash_eur="401")
    card["valid_until"] = POLICY_END.isoformat()
    path = tmp_path / "SYNTHETIC-O3-legacy-400-limit.json"
    path.write_text(json.dumps(card, sort_keys=True) + "\n")
    reference = {"source_path": str(path),
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    with pytest.raises(fc.FinancialError, match="financial_ceiling_exceeded"):
        _validate(reference, binding)
