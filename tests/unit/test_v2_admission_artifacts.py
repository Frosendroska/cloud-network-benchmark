from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib

import pytest
from pydantic import ValidationError

from cloud_network_benchmark.contracts.admission import AdmissionRequirements, ExpiryEvidence, ExpiryRequest, ReservationEvidence, ReservationRequest, ScheduleEntry
from cloud_network_benchmark.contracts.artifacts import ArtifactInput, ArtifactLayout, ArtifactRecord, resolve_artifact_path
from cloud_network_benchmark.contracts.common import Evidence


def test_schedule_entry_keeps_exact_row_and_hash_identity() -> None:
    entry = ScheduleEntry(schema_version=2,
        entry_id="row-1", config_id="exp_001", experiment_id="EXP-001",
        scheduled_start=datetime(2026, 10, 21, tzinfo=timezone.utc), window_label="morning",
        source_path="tracking/runs.csv", source_sha256="a" * 64, row_sha256=hashlib.sha256(b"row-1,exp_001").hexdigest(),
        original_header="id,config", original_row="row-1,exp_001", columns=["id", "config"], priority="P0",
    )
    assert entry.original_row == "row-1,exp_001"
    with pytest.raises(ValidationError):
        ScheduleEntry(**{**entry.model_dump(), "config_id": "EXP-001"})
    with pytest.raises(ValidationError):
        ScheduleEntry(**{**entry.model_dump(), "row_sha256": "b" * 64})
    with pytest.raises(ValidationError):
        ScheduleEntry(**{**entry.model_dump(), "scheduled_start": datetime(2026, 10, 21)})


def test_admission_requirements_use_only_declared_caps_and_source_evidence() -> None:
    expected = AdmissionRequirements.expected_for("diagnostic_day", "same_zone")
    assert expected == {
        "priority": "P0", "lifetime_cap_seconds": 87600, "requires_common_n": True,
        "requires_schedule": True, "requires_primary_review": False, "requires_expiry": True,
    }
    requirements = AdmissionRequirements(
        **expected, hard_budget_usd=300, protected_buffer_usd=50, cost_input_id="input-2026-10-20",
        prices_checked_on=date(2026, 10, 20), cost_input_sha256=Evidence[str](unavailable_reason="not loaded"),
        estimated_cost_usd=Evidence[float](unavailable_reason="not estimated"),
    )
    assert requirements.estimated_cost_usd.value is None
    with pytest.raises(ValidationError):
        AdmissionRequirements(**{**requirements.model_dump(), "lifetime_cap_seconds": 3600})


def test_artifact_paths_and_materialized_success_are_strict() -> None:
    layout = ArtifactLayout()
    assert layout.unit_paths("unit-1")["sockperf"] == "measurements/unit-1/sockperf.csv"
    with pytest.raises(ValidationError):
        ArtifactInput(artifact_id="escape", path="../x", kind="sockperf", producer="vm_a", unit_id="u", expectation="required")
    with pytest.raises(ValidationError):
        ArtifactInput(artifact_id="bad", path="ok", path_template="chunks/{id}", kind="x", producer="controller", unit_id=None, expectation="required")
    with pytest.raises(ValidationError):
        ArtifactRecord(
            artifact_id="missing", path="data.csv", kind="sockperf", producer="vm_a", unit_id="unit-1",
            deployment_id="deploy", closed=False, size_bytes=Evidence[int](value=0),
            sha256=Evidence[str](value="a" * 64), retrieval_state="verified", failure=None,
        )


def test_artifact_destination_resolves_inside_root_and_rejects_symlinks(tmp_path) -> None:
    root = tmp_path / "child"
    root.mkdir()
    assert resolve_artifact_path(root, "measurements/unit/sockperf.csv") == root / "measurements/unit/sockperf.csv"
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        resolve_artifact_path(root, "escape/secret.txt")


def test_provider_expiry_semantics_are_explicit() -> None:
    with pytest.raises(ValidationError):
        ExpiryRequest(schema_version=2,
            run_id="campaign-aws", deployment_id="deploy", provider="aws", action="deallocate",
            expires_at=Evidence[datetime](unavailable_reason="pending"),
            reservation_id=Evidence[str](unavailable_reason="pending"), resource_namespace="namespace",
            target_selector="resource-tag",
        )


def test_reserved_status_needs_actual_evidence() -> None:
    with pytest.raises(ValidationError):
        ReservationEvidence(schema_version=2,
            campaign_id="campaign", reservation_id=Evidence[str](unavailable_reason="pending"),
            state="reserved", reserved_cost_usd=Evidence[float](unavailable_reason="pending"),
            cap_started_at=Evidence[datetime](unavailable_reason="pending"),
            expires_at=Evidence[datetime](unavailable_reason="pending"),
            cap_start_policy_id=Evidence[str](unavailable_reason="pending"), source_path="prices.csv",
            source_sha256="a" * 64, reasons=[],
        )


def test_safety_records_reject_naive_times_and_cross_attempt_expiry() -> None:
    with pytest.raises(ValidationError, match="datetime evidence must include a timezone"):
        ReservationEvidence(schema_version=2,
            campaign_id="campaign", reservation_id={"value":"reservation"}, state="reserved",
            reserved_cost_usd={"value":10}, cap_started_at={"value":datetime(2026, 10, 21)},
            expires_at={"value":datetime(2026, 10, 22, tzinfo=timezone.utc)},
            cap_start_policy_id={"value":"policy"}, source_path="prices.csv", source_sha256="a"*64,
        )
    with pytest.raises(ValidationError, match="provider suffix"):
        ExpiryEvidence(schema_version=2,
            run_id="campaign-aws", deployment_id="campaign-aws-deployment", provider="azure", action="deallocate",
            state="registered", registration_id={"value":"registration"},
            registered_at={"value":datetime(2026, 10, 21, tzinfo=timezone.utc)},
            expires_at={"value":datetime(2026, 10, 22, tzinfo=timezone.utc)},
            target_selector="selector", provider_metadata={"unavailable_reason":"not captured"},
        )


def test_reservation_request_cap_is_derived_from_mode() -> None:
    with pytest.raises(ValidationError, match="match reservation mode"):
        ReservationRequest(schema_version=2,
            campaign_id="campaign", config_id="exp-001-short", schedule_entry_id={"unavailable_reason":"none"},
            selected_providers=["aws"], mode="calibration", priority="P0", lifetime_cap_seconds=1200,
            estimated_cost_usd={"unavailable_reason":"pending"}, source_path="prices.csv", source_sha256="a"*64,
            incurred_cost_usd={"unavailable_reason":"pending"}, active_reserved_usd={"unavailable_reason":"pending"},
            outstanding_cleanup=False,
        )


def test_generic_numeric_evidence_rejects_bool_and_nonfinite_values() -> None:
    from cloud_network_benchmark.contracts.common import Evidence
    with pytest.raises(ValidationError, match="boolean"):
        Evidence[int](value=True)
    with pytest.raises(ValidationError, match="finite"):
        Evidence[float](value=float("nan"))
