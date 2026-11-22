from __future__ import annotations

import pytest
import json

from cloud_network_benchmark.admission import WorkflowAllowance
from cloud_network_benchmark.campaign_inputs import _allowance, _profile_execution_window
from cloud_network_benchmark.validation_profiles import (
    ACCEPTANCE_PROFILE, CALIBRATION_PROFILE, FAILURE_PROFILE,
    PROFILE_SPECS, profile_allowance, validate_profile_allowance,
)
from cloud_network_benchmark.admission import load_cost_inputs


@pytest.mark.parametrize("profile,providers", [
    (ACCEPTANCE_PROFILE, {"aws", "azure", "gcp"}),
    (CALIBRATION_PROFILE, {"aws", "azure", "gcp"}),
    (FAILURE_PROFILE, {"aws"}),
    (FAILURE_PROFILE, {"azure"}),
    (FAILURE_PROFILE, {"gcp"}),
])
def test_profile_fixed_vector_fits_registered_cap(profile, providers):
    spec = PROFILE_SPECS[profile]
    vector = profile_allowance(profile, providers)
    allowance = WorkflowAllowance(**vector)
    validate_profile_allowance(profile, allowance, providers)
    assert allowance.stabilization_seconds == 60
    assert allowance.total_seconds + spec["barrier_seconds"] + max(allowance.provider_leads_seconds.values()) <= spec["cap_seconds"]


def test_paired_acceptance_and_registered_failure_select_distinct_profiles():
    settings = {"stage_inputs": {"acceptance": {
        "workflow_profile": ACCEPTANCE_PROFILE,
        "failure_workflow_profile": FAILURE_PROFILE,
    }}}
    paired = _allowance(settings, "acceptance", {"aws", "azure", "gcp"}, entry_id="V-S1")
    failure = _allowance(settings, "acceptance", {"aws"}, entry_id="V-A-FAIL")
    assert paired["provider_leads_seconds"] == {"aws": 360, "azure": 120, "gcp": 30}
    assert failure["provider_leads_seconds"] == {"aws": 720}
    assert paired["source_id"] == f"{ACCEPTANCE_PROFILE}:fixed-profile-v1"
    assert failure["source_id"] == f"{FAILURE_PROFILE}:fixed-profile-v1"


def test_profile_rejects_contradictory_operator_vector_and_unknown_profile():
    settings = {"stage_inputs": {"acceptance": {
        "workflow_profile": ACCEPTANCE_PROFILE,
        "allowance": {"cleanup_seconds": 10},
    }}}
    with pytest.raises(ValueError, match="complete fixed stage vector"):
        _allowance(settings, "acceptance", {"aws", "azure", "gcp"}, entry_id="V-S1")
    settings["stage_inputs"]["acceptance"]["workflow_profile"] = "unregistered"
    with pytest.raises(ValueError, match="unsupported workflow_profile"):
        _allowance(settings, "acceptance", {"aws", "azure", "gcp"}, entry_id="V-S1")


def test_ready_trigger_window_requires_exact_positive_utc_interval():
    window = _profile_execution_window({"execution_window_utc": {
        "not_before_utc": "2026-11-22T10:00:00Z",
        "not_after_utc": "2026-11-22T11:00:00Z"}}, "acceptance")
    assert window["not_before_utc"] == "2026-11-22T10:00:00+00:00"
    wider = _profile_execution_window({"execution_window_utc": {
        "not_before_utc": "2026-11-22T10:00:00Z",
        "not_after_utc": "2026-11-23T10:00:00Z"}}, "acceptance")
    assert wider["not_after_utc"] == "2026-11-23T10:00:00+00:00"
    with pytest.raises(ValueError, match="must be positive"):
        _profile_execution_window({"execution_window_utc": {
            "not_before_utc": "2026-11-22T10:00:00Z",
            "not_after_utc": "2026-11-22T10:00:00Z"}}, "acceptance")


def test_cost_input_accepts_only_exact_fixed_profile_binding(tmp_path):
    row = {"profile_id": FAILURE_PROFILE, "stage": "acceptance",
        "lifetime_cap_seconds": PROFILE_SPECS[FAILURE_PROFILE]["cap_seconds"],
        "allowance": PROFILE_SPECS[FAILURE_PROFILE]["allowance"]}
    payload = {"schema_version": 1, "cost_input_id": "reviewed-dated-input",
        "prices_checked_on": "2026-11-22", "source_scope": ["operator-prices.json"],
        "source_sha256": "a" * 64, "source_identity_kind": "operator-reviewed-dated-price-source",
        "currency": "USD", "funding": {"primary": "1", "replacements": "0", "tax": "0", "uncertainty": "0"},
        "workflow_profiles": [row], "refresh_required_before_paid_execution": False}
    path = tmp_path / "cost-inputs.json"
    path.write_text(json.dumps(payload))
    costs = load_cost_inputs(path)
    assert not costs.refresh_required
    changed = json.loads(path.read_text())
    changed["workflow_profiles"][0]["lifetime_cap_seconds"] = 1200
    path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="fixed registered profile"):
        load_cost_inputs(path)
