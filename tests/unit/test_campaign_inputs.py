"""Focused source/settings contract tests, without provider calls."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from cloud_network_benchmark.campaign_inputs import load_operator_settings, validate_stage_inputs


def test_operator_yaml_rejects_duplicate_keys(tmp_path):
    path = tmp_path / "operator.yaml"
    path.write_text("schema_version: 1\nproviders: {}\nproviders: {}\n")
    with pytest.raises(ValueError, match="duplicate YAML key"):
        load_operator_settings(path)


def test_operator_yaml_rejects_missing_schema(tmp_path):
    path = tmp_path / "operator.yaml"
    path.write_text("providers: {}\n")
    with pytest.raises(ValueError, match="schema_version"):
        load_operator_settings(path)


def test_expired_credential_refresh_evidence_cannot_pass_preflight(tmp_path, monkeypatch):
    import cloud_network_benchmark.campaign_inputs as inputs
    # Isolate the credential-duration condition; unrelated price/approval/config
    # contracts are exercised separately by lifecycle/admission integration tests.
    monkeypatch.setattr(inputs, "_selected", lambda *_: [])
    monkeypatch.setattr(inputs, "_gate_rows", lambda *_: {})
    monkeypatch.setattr(inputs, "_cost_payload", lambda *_, **__: {})
    evidence = tmp_path / "refresh-review.txt"
    evidence.write_text("offline test-only stale credential review")
    settings = {"state_root": str(tmp_path / "private"), "results_root": str(tmp_path / "results"),
                "credential_refresh": {"confirmed": True, "source": str(evidence),
                   "valid_through_utc": (datetime.now(timezone.utc)-timedelta(days=1)).isoformat()}}
    blockers = validate_stage_inputs({"stage": "acceptance"}, settings, tmp_path)
    assert blockers and any("credential" in value.lower() or "refresh" in value.lower() for value in blockers)


def test_ready_profile_requires_finite_ordered_dispatch_window_and_uses_it_for_credential_horizon():
    from cloud_network_benchmark.campaign_inputs import _profile_dispatch_window, _ready_credential_horizon

    inputs = {"execution_window_utc": {"not_before_utc": "2026-11-24T15:25:00Z",
            "not_after_utc": "2026-11-24T17:00:00Z"},
        "dispatch_window_utc": {"not_before_utc": "2026-11-24T15:25:00Z",
            "not_after_utc": "2026-11-24T16:15:00Z"}}
    execution = inputs["execution_window_utc"]
    assert _profile_dispatch_window(inputs, "acceptance", execution) == {
        "not_before_utc": "2026-11-24T15:25:00+00:00", "not_after_utc": "2026-11-24T16:15:00+00:00"}
    assert _ready_credential_horizon(inputs, "acceptance", 4200) == datetime(2026, 11, 24, 17, 25, tzinfo=timezone.utc)
    for dispatch in (
            None,
            {"not_before_utc": "2026-11-24T16:15:00Z", "not_after_utc": "2026-11-24T15:25:00Z"},
            {"not_before_utc": "2026-11-24T15:25:00Z", "not_after_utc": "2026-11-24T17:01:00Z"},
            {"not_before_utc": "2026-11-24T15:25:00", "not_after_utc": "2026-11-24T16:15:00Z"},
    ):
        inputs["dispatch_window_utc"] = dispatch
        with pytest.raises(ValueError):
            _profile_dispatch_window(inputs, "acceptance", execution)


def test_archive_paths_must_not_overlap_results_or_private_state(tmp_path, monkeypatch):
    import hashlib, json
    import cloud_network_benchmark.campaign_inputs as inputs
    monkeypatch.setattr(inputs, "_selected", lambda *_: [])
    quota_path = tmp_path / "quota-evidence.json"
    quota_payload = json.dumps({"measured_free_bytes": 8192}, sort_keys=True).encode()
    quota_path.write_bytes(quota_payload)
    settings = {"state_root": str(tmp_path / "private"), "results_root": str(tmp_path / "results"),
        "archive_sink": {"bucket": "valid-bucket", "maximum_staging_bytes": 8192,
            "maximum_archive_bytes": 1_000_000, "minimum_controller_free_bytes": 1024,
            "minimum_guest_free_bytes": 1024, "maximum_capsule_bytes": 4096,
            "controller_quota_free_bytes": 8192,
            "controller_quota_evidence_path": str(quota_path),
            "controller_quota_evidence_sha256": hashlib.sha256(quota_payload).hexdigest(),
            "source_credentials_file": str(tmp_path / "synthetic-adc.json"),
            "expected_source_principal": "katyabraun0@gmail.com",
            "expected_project_id": "project-a70f8497-3463-4ef2-9cd",
            "expected_service_account_email": "cnb-archive-halle@project-a70f8497-3463-4ef2-9cd.iam.gserviceaccount.com",
            "restore_path": str(tmp_path / "restore"),
            "archive_root": str(tmp_path / "results" / "archive"),
            "staging_directory": str(tmp_path / "stage")}}
    blockers = validate_stage_inputs({"stage": "acceptance"}, settings, tmp_path)
    assert any("must not overlap results_root" in item for item in blockers)


def test_runner_uses_locally_frozen_common_n_without_running_measurement_analysis(tmp_path, monkeypatch):
    import json
    import cloud_network_benchmark.campaign_inputs as inputs
    import cloud_network_benchmark.lifecycle_bundle as lifecycle

    monkeypatch.setattr(lifecycle, "recommend_calibration_common_n", lambda *_: pytest.fail("runner selected N from outcomes"))
    saved = tmp_path / "local-common-n.json"
    saved.write_text(json.dumps({"status": "recommended", "common_n": {"value": 4},
        "source_campaign_id": {"value": "offline-calibration"}, "source_sha256": {"value": "a" * 64},
        "plateau_found": {"value": True}, "source_run_ids": ["cal-aws", "cal-azure", "cal-gcp"]}))
    frozen = inputs._common_n({"common_n_recommendation": str(saved)})
    assert frozen["state"] == "frozen" and frozen["common_n"] == 4


def test_raw_calibration_results_are_not_accepted_as_a_locally_reviewed_recommendation(tmp_path):
    import json
    import cloud_network_benchmark.campaign_inputs as inputs

    saved = tmp_path / "raw-calibration-results.json"
    saved.write_text(json.dumps([{}, {}, {}]))
    with pytest.raises(ValueError, match="locally saved"):
        inputs._common_n({"common_n_recommendation": str(saved)})


def _proposed_overrun_amounts():
    return {"primary": Decimal("189.10562207733502645502645501"),
        "uncertainty": Decimal("51.11454259936507936507936508"),
        "tax": Decimal("11.16189295488931216931216931"),
        "replacements": Decimal("0"), "total_limit": Decimal("300"),
        "working_limit": Decimal("250"), "protected_buffer": Decimal("50")}


def _pending_eur_release_settings(path, *, scope_id="SYNTHETIC-OFFLINE"):
    import hashlib
    from cloud_network_benchmark.campaign_inputs import _EUR_RESERVE_RELEASE_REQUEST
    return {"ledger": {"scope_id": scope_id},
        "pricing": {"pending_eur_reserve_release": dict(_EUR_RESERVE_RELEASE_REQUEST)},
        "approvals": {"paid_authorization": {"confirmed": True, "source": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}}


def test_over_250_usd_cost_draft_records_pending_eur_release_without_authority():
    from cloud_network_benchmark.campaign_inputs import (_EUR_RESERVE_RELEASE_REQUEST,
        _validate_funding_budget)
    from cloud_network_benchmark.financial_controls import legacy_usd_budget_exceeded

    now = datetime(2026, 11, 10, tzinfo=timezone.utc)
    amounts = _proposed_overrun_amounts()
    commitment = sum((amounts[key] for key in ("primary", "uncertainty", "tax", "replacements")), Decimal(0))
    assert commitment == Decimal("251.3820576315894179894179894")
    assert legacy_usd_budget_exceeded(commitment, None, now=now)
    _validate_funding_budget(amounts, dict(_EUR_RESERVE_RELEASE_REQUEST), None, draft=True, now=now)
    with pytest.raises(ValueError, match="without an explicit EUR 14"):
        _validate_funding_budget(amounts, None, None, draft=True, now=now)
    with pytest.raises(ValueError, match="without current source-bound EUR 14 approval"):
        _validate_funding_budget(amounts, dict(_EUR_RESERVE_RELEASE_REQUEST), None, draft=False, now=now)


def test_non_draft_overrun_requires_current_source_bound_typed_eur_14_caps(tmp_path):
    import hashlib
    import json
    from datetime import timedelta
    from cloud_network_benchmark.campaign_inputs import (_EUR_RESERVE_RELEASE_REQUEST,
        _reserve_release_policy, _validate_funding_budget)
    from tests.financial_helpers import NOW, synthetic_finances

    book, _plans, card, path = synthetic_finances(tmp_path, paid=True)
    card["caps"].update(project_eur="300", working_eur="264", protected_eur="36",
        personal_cash_eur="264", batch_eur="264", stage_eur="264")
    path.write_text(json.dumps(card, sort_keys=True))
    settings = _pending_eur_release_settings(path, scope_id=book.scope_id)
    request, snapshot = _reserve_release_policy(settings, "acceptance", draft=False, now=NOW)
    assert request == _EUR_RESERVE_RELEASE_REQUEST
    assert snapshot["scope_id"] == book.scope_id
    _validate_funding_budget(_proposed_overrun_amounts(), request, snapshot, draft=False, now=NOW)

    with pytest.raises(ValueError, match="current ledger scope"):
        _reserve_release_policy(_pending_eur_release_settings(path, scope_id="SYNTHETIC-OTHER"),
            "acceptance", draft=False, now=NOW)
    with pytest.raises(ValueError, match="exact typed EUR 14"):
        card["caps"].update(working_eur="266", protected_eur="34")
        path.write_text(json.dumps(card, sort_keys=True))
        settings["approvals"]["paid_authorization"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        _reserve_release_policy(settings, "acceptance", draft=False, now=NOW)

    card["caps"].update(working_eur="264", protected_eur="36")
    card["stage_id"] = "SYNTHETIC-TAMPERED-STAGE"
    path.write_text(json.dumps(card, sort_keys=True))
    with pytest.raises(ValueError, match="exact declared source sha256"):
        _reserve_release_policy(settings, "acceptance", draft=False, now=NOW)
    card["stage_id"] = "SYNTHETIC-STAGE"
    card["valid_until"] = (NOW - timedelta(seconds=1)).isoformat()
    path.write_text(json.dumps(card, sort_keys=True))
    settings["approvals"]["paid_authorization"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="stale"):
        _reserve_release_policy(settings, "acceptance", draft=False, now=NOW)

    settings["approvals"]["paid_authorization"]["source"] = str(tmp_path / "missing-card.json")
    with pytest.raises(ValueError, match="missing approvals.paid_authorization.source"):
        _reserve_release_policy(settings, "acceptance", draft=False, now=NOW)


def _v19_request():
    return {"status": "pending", "currency": "EUR", "prior_release_eur": "14",
        "additional_release_eur": "15", "cumulative_release_eur": "29"}


def _write_synthetic_authority(path, card):
    import hashlib
    import json
    path.write_text(json.dumps(card, sort_keys=True))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _synthetic_v19_authorities(tmp_path):
    import copy
    from tests.financial_helpers import synthetic_finances

    book, _plans, card, _path = synthetic_finances(tmp_path, paid=True)
    prior = copy.deepcopy(card)
    prior["caps"].update(project_eur="300", working_eur="264", protected_eur="36",
        personal_cash_eur="264", batch_eur="264", stage_eur="264")
    prior_path = tmp_path / "SYNTHETIC-prior-v18-card.json"
    prior_hash = _write_synthetic_authority(prior_path, prior)
    current = copy.deepcopy(card)
    current["caps"].update(project_eur="300", working_eur="279", protected_eur="21",
        personal_cash_eur="279", batch_eur="279", stage_eur="279")
    current_path = tmp_path / "SYNTHETIC-new-v19-card.json"
    current_hash = _write_synthetic_authority(current_path, current)
    settings = {"ledger": {"scope_id": book.scope_id},
        "pricing": {"pending_eur_reserve_release": _v19_request()},
        "approvals": {
            "prior_paid_authorization": {"confirmed": True, "source": str(prior_path), "sha256": prior_hash},
            "paid_authorization": {"confirmed": True, "source": str(current_path), "sha256": current_hash}}}
    return book, settings, prior, prior_path, current, current_path


def test_v19_release_request_is_pending_and_non_authorizing_in_draft(tmp_path):
    from cloud_network_benchmark.campaign_inputs import (_reserve_release_policy,
        _validate_funding_budget)
    from tests.financial_helpers import NOW

    request = _v19_request()
    settings = {"pricing": {"pending_eur_reserve_release": request}}
    amounts = {"primary": Decimal("200.73562207733502645502645501"),
        "uncertainty": Decimal("54.60454259936507936507936508"),
        "tax": Decimal("11.16189295488931216931216931"), "replacements": Decimal("0"),
        "total_limit": Decimal("300"), "working_limit": Decimal("250"),
        "protected_buffer": Decimal("50")}
    pending, snapshot = _reserve_release_policy(settings, "acceptance", draft=True, now=NOW)
    assert pending == request and snapshot is None
    _validate_funding_budget(amounts, pending, snapshot, draft=True, now=NOW)
    with pytest.raises(ValueError, match="without an explicit EUR 14 reserve-release request"):
        _validate_funding_budget(amounts, None, None, draft=True, now=NOW)


def test_v19_overrun_requires_prior_14_and_current_15_source_bound_authorities(tmp_path, monkeypatch):
    import hashlib
    import cloud_network_benchmark.campaign_inputs as inputs
    from tests.financial_helpers import NOW

    book, settings, _prior, prior_path, _current, _current_path = _synthetic_v19_authorities(tmp_path)
    prior_hash = hashlib.sha256(prior_path.read_bytes()).hexdigest()
    monkeypatch.setattr(inputs, "_EUR14_AUTHORITY_SHA256", prior_hash)
    request, snapshot = inputs._reserve_release_policy(settings, "acceptance", draft=False, now=NOW)
    assert request == _v19_request()
    assert snapshot["scope_id"] == book.scope_id
    assert snapshot["caps"]["project_eur"] == "300"
    assert snapshot["caps"]["working_eur"] == "279"
    assert snapshot["caps"]["protected_eur"] == "21"
    amounts = {"primary": Decimal("200.73562207733502645502645501"),
        "uncertainty": Decimal("54.60454259936507936507936508"),
        "tax": Decimal("11.16189295488931216931216931"), "replacements": Decimal("0"),
        "total_limit": Decimal("300"), "working_limit": Decimal("250"),
        "protected_buffer": Decimal("50")}
    inputs._validate_funding_budget(amounts, request, snapshot, draft=False, now=NOW)


@pytest.mark.parametrize("case", ["missing_prior", "wrong_prior_hash", "wrong_prior_scope",
    "wrong_prior_caps", "expired_prior", "missing_current", "wrong_current_hash",
    "wrong_current_scope", "wrong_current_caps", "expired_current", "changed_current_source"])
def test_v19_release_authority_failures_close_paid_path(tmp_path, monkeypatch, case):
    import hashlib
    import cloud_network_benchmark.campaign_inputs as inputs
    from datetime import timedelta
    from tests.financial_helpers import NOW

    _book, settings, prior, prior_path, current, current_path = _synthetic_v19_authorities(tmp_path)
    prior_hash = hashlib.sha256(prior_path.read_bytes()).hexdigest()
    monkeypatch.setattr(inputs, "_EUR14_AUTHORITY_SHA256", prior_hash)
    if case == "missing_prior":
        settings["approvals"].pop("prior_paid_authorization")
    elif case == "wrong_prior_hash":
        settings["approvals"]["prior_paid_authorization"]["sha256"] = "0" * 64
    elif case == "wrong_prior_scope":
        prior["ledger_scope_id"] = "SYNTHETIC-WRONG-SCOPE"
        prior_hash = _write_synthetic_authority(prior_path, prior)
        settings["approvals"]["prior_paid_authorization"]["sha256"] = prior_hash
        monkeypatch.setattr(inputs, "_EUR14_AUTHORITY_SHA256", prior_hash)
    elif case == "wrong_prior_caps":
        prior["caps"]["working_eur"] = "263"
        prior_hash = _write_synthetic_authority(prior_path, prior)
        settings["approvals"]["prior_paid_authorization"]["sha256"] = prior_hash
        monkeypatch.setattr(inputs, "_EUR14_AUTHORITY_SHA256", prior_hash)
    elif case == "expired_prior":
        prior["valid_until"] = (NOW - timedelta(seconds=1)).isoformat()
        prior_hash = _write_synthetic_authority(prior_path, prior)
        settings["approvals"]["prior_paid_authorization"]["sha256"] = prior_hash
        monkeypatch.setattr(inputs, "_EUR14_AUTHORITY_SHA256", prior_hash)
    elif case == "missing_current":
        settings["approvals"].pop("paid_authorization")
    elif case == "wrong_current_hash":
        settings["approvals"]["paid_authorization"]["sha256"] = "0" * 64
    elif case == "wrong_current_scope":
        current["ledger_scope_id"] = "SYNTHETIC-WRONG-SCOPE"
        current_hash = _write_synthetic_authority(current_path, current)
        settings["approvals"]["paid_authorization"]["sha256"] = current_hash
    elif case == "wrong_current_caps":
        current["caps"].update(working_eur="264", protected_eur="36")
        current_hash = _write_synthetic_authority(current_path, current)
        settings["approvals"]["paid_authorization"]["sha256"] = current_hash
    elif case == "expired_current":
        current["valid_until"] = (NOW - timedelta(seconds=1)).isoformat()
        current_hash = _write_synthetic_authority(current_path, current)
        settings["approvals"]["paid_authorization"]["sha256"] = current_hash
    elif case == "changed_current_source":
        current_path.write_text("changed after approval")
    with pytest.raises(ValueError):
        inputs._reserve_release_policy(settings, "acceptance", draft=False, now=NOW)
