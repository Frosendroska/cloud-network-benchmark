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


def test_archive_paths_must_not_overlap_results_or_private_state(tmp_path, monkeypatch):
    import cloud_network_benchmark.campaign_inputs as inputs
    import json
    monkeypatch.setattr(inputs, "_selected", lambda _: [])
    source_adc = tmp_path / "human-adc.json"
    source_adc.write_text(json.dumps({"type": "authorized_user", "client_id": "client",
        "client_secret": "secret", "refresh_token": "refresh",
        "quota_project_id": "project-a70f8497-3463-4ef2-9cd", "universe_domain": "googleapis.com"}))
    source_adc.chmod(0o600)
    settings = {"state_root": str(tmp_path / "private"), "results_root": str(tmp_path / "results"),
        "archive_sink": {"mode": "impersonated_adc", "bucket": "valid-bucket", "maximum_staging_bytes": 8192,
            "maximum_archive_bytes": 1_000_000, "minimum_controller_free_bytes": 1024,
            "archive_root": str(tmp_path / "results" / "archive"),
            "source_credentials_file": str(source_adc),
            "expected_source_principal": "katyabraun0@gmail.com",
            "expected_project_id": "project-a70f8497-3463-4ef2-9cd",
            "expected_service_account_email": "cnb-archive-halle@project-a70f8497-3463-4ef2-9cd.iam.gserviceaccount.com",
            "staging_directory": str(tmp_path / "stage")}}
    blockers = validate_stage_inputs({"stage": "acceptance"}, settings, tmp_path)
    assert any("must not overlap results_root" in item for item in blockers)


def test_archive_readiness_rejects_absent_human_adc_and_key_mode_before_preparation(tmp_path, monkeypatch):
    import cloud_network_benchmark.campaign_inputs as inputs
    monkeypatch.setattr(inputs, "_selected", lambda _: [])
    roots = {"state_root": str(tmp_path / "private"), "results_root": str(tmp_path / "results")}
    sink = {"mode": "impersonated_adc", "bucket": "valid-bucket", "maximum_staging_bytes": 8192,
        "maximum_archive_bytes": 1_000_000, "minimum_controller_free_bytes": 1024,
        "archive_root": str(tmp_path / "archive"), "staging_directory": str(tmp_path / "stage"),
        "source_credentials_file": str(tmp_path / "absent-adc.json"),
        "expected_source_principal": "katyabraun0@gmail.com",
        "expected_project_id": "project-a70f8497-3463-4ef2-9cd",
        "expected_service_account_email": "cnb-archive-halle@project-a70f8497-3463-4ef2-9cd.iam.gserviceaccount.com"}
    blockers = validate_stage_inputs({"stage": "acceptance"}, {**roots, "archive_sink": sink}, tmp_path)
    assert any("archive source ADC" in item for item in blockers)
    key_mode = {**sink, "mode": "service_account_key", "credentials_file": str(tmp_path / "key.json")}
    blockers = validate_stage_inputs({"stage": "acceptance"}, {**roots, "archive_sink": key_mode}, tmp_path)
    assert any("archive_sink" in item for item in blockers)


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
