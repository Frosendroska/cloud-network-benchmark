from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from cloud_network_benchmark.contracts.admission import AdmissionRequirements, ExpiryRequest, ReservationEvidence, ScheduleEntry
from cloud_network_benchmark.contracts.artifacts import ArtifactInput, ArtifactLayout, ArtifactRecord, resolve_artifact_path
from cloud_network_benchmark.contracts.common import Evidence


def test_schedule_entry_keeps_exact_row_and_hash_identity() -> None:
    entry = ScheduleEntry(
        entry_id="row-1", config_id="exp_001", experiment_id="EXP-001",
        scheduled_start=datetime(2026, 10, 21, tzinfo=timezone.utc), window_label="morning",
        source_path="tracking/runs.csv", source_sha256="a" * 64, row_sha256="b" * 64,
        original_header="id,config", original_row="row-1,exp_001", columns=["id", "config"], priority="P0",
    )
    assert entry.original_row == "row-1,exp_001"
    with pytest.raises(ValidationError):
        ScheduleEntry(**{**entry.model_dump(), "config_id": "EXP-001"})
    with pytest.raises(ValidationError):
        ScheduleEntry(**{**entry.model_dump(), "scheduled_start": datetime(2026, 10, 21)})


def test_admission_requirements_use_only_declared_caps_and_source_evidence() -> None:
    expected = AdmissionRequirements.expected_for("diagnostic_day", "same_zone")
    assert expected == {
        "priority": "P0", "lifetime_cap_seconds": 87600, "requires_common_n": False,
        "requires_schedule": True, "requires_primary_review": True, "requires_expiry": True,
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
        ExpiryRequest(
            run_id="campaign-aws", deployment_id="deploy", provider="aws", action="deallocate",
            expires_at=Evidence[datetime](unavailable_reason="pending"),
            reservation_id=Evidence[str](unavailable_reason="pending"), resource_namespace="namespace",
            target_selector="resource-tag",
        )


def test_reserved_status_needs_actual_evidence() -> None:
    with pytest.raises(ValidationError):
        ReservationEvidence(
            campaign_id="campaign", reservation_id=Evidence[str](unavailable_reason="pending"),
            state="reserved", reserved_cost_usd=Evidence[float](unavailable_reason="pending"),
            cap_started_at=Evidence[datetime](unavailable_reason="pending"),
            expires_at=Evidence[datetime](unavailable_reason="pending"),
            cap_start_policy_id=Evidence[str](unavailable_reason="pending"), source_path="prices.csv",
            source_sha256="a" * 64, reasons=[],
        )
