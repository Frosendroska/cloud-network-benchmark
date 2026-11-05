"""Focused source/settings contract tests, without provider calls."""
from datetime import datetime, timedelta, timezone
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
    monkeypatch.setattr(inputs, "_selected", lambda _: [])
    monkeypatch.setattr(inputs, "_gate_rows", lambda *_: {})
    monkeypatch.setattr(inputs, "_cost_payload", lambda *_: {})
    evidence = tmp_path / "refresh-review.txt"
    evidence.write_text("offline test-only stale credential review")
    settings = {"state_root": str(tmp_path / "private"), "results_root": str(tmp_path / "results"),
                "credential_refresh": {"confirmed": True, "source": str(evidence),
                   "valid_through_utc": (datetime.now(timezone.utc)-timedelta(days=1)).isoformat()}}
    blockers = validate_stage_inputs({"stage": "acceptance"}, settings, tmp_path)
    assert blockers and any("credential" in value.lower() or "refresh" in value.lower() for value in blockers)


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
