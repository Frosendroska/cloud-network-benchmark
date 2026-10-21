from datetime import datetime, timezone
import pytest

from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.contracts import (
    ARTIFACTS, ArtifactExpectation, IdentityContext, ValidationResult,
    ValidatorInput, artifact_inputs,
)
from cloud_network_benchmark.contracts.common import ValidationStatus


def test_mode_aware_artifacts_cover_exact_units_and_schedule_expectation(repository_root) -> None:
    resolved, _ = resolve_campaign(repository_root / "configs/experiments/exp-001-multi-provider.yaml", repository_root)
    selected = artifact_inputs(ARTIFACTS, mode=resolved.mode, measurement_units=resolved.measurement_units)
    assert selected["config_snapshot"].expectation == ArtifactExpectation.REQUIRED.value
    assert selected["resolved_snapshot"].expectation == ArtifactExpectation.REQUIRED.value
    assert selected["schedule_snapshot"].expectation == ArtifactExpectation.NOT_EXPECTED.value
    assert not any("flent" in key for key in selected)
    assert any(value.unit_id == "idle_latency" for value in selected.values())
    assert selected["validation_result"].expectation == ArtifactExpectation.OPTIONAL.value
    assert selected["sockperf_server_log"].producer == "vm_b"
    assert selected["iperf3_server_log"].producer == "vm_b"
    assert selected["vm_a_diagnostics"].producer == "vm_a"
    assert selected["vm_b_metadata"].producer == "vm_b"
    assert selected["single_flow-iperf3"].kind == "iperf3-json"


def test_day_artifact_layout_uses_streaming_native_files_only(repository_root) -> None:
    resolved, _ = resolve_campaign(repository_root / "configs/experiments/exp-001-diagnostic-day.yaml", repository_root)
    selected = artifact_inputs(ARTIFACTS, mode=resolved.mode, measurement_units=resolved.measurement_units)
    assert "day-iperf3-client" in selected and "day-probe-csv" in selected
    assert not any(key.endswith("-sockperf") or key.endswith("-iperf3") for key in selected)
    assert selected["day-iperf3-client"].kind == "iperf3-client-jsonl"


def test_legacy_multi_flow_toggle_is_rejected(repository_root) -> None:
    import pytest
    resolved, _ = resolve_campaign(repository_root / "configs/experiments/exp-001-multi-provider.yaml", repository_root)
    with pytest.raises(TypeError):
        artifact_inputs(ARTIFACTS, multi_flow_enabled=False, mode=resolved.mode,
                       measurement_units=resolved.measurement_units)


def test_validator_input_carries_native_identity_and_pending_status(repository_root) -> None:
    resolved, _ = resolve_campaign(repository_root / "configs/tests/exp-900-single-provider.yaml", repository_root)
    obs = resolved.observations[0]
    identity = IdentityContext(
        experiment_id=resolved.experiment_id, config_id=resolved.config_id,
        campaign_id="campaign", observation_id="campaign-aws-observation",
        run_id="campaign-aws", deployment_id="campaign-aws-deployment", provider="aws",
    )
    artifacts = artifact_inputs(obs.artifact_layout, mode=resolved.mode,
                                measurement_units=resolved.measurement_units)
    value = ValidatorInput(
        schema_version=2, identity=identity, mode=resolved.mode, context=resolved.context,
        campaign_manifest_path="results/manifests/campaign.json",
        child_manifest_path="results/manifests/run.json", child_result_root="results/raw/run",
        config_snapshot_path="results/raw/run/config.yaml", config_sha256=resolved.config_source.sha256,
        resolved_snapshot_path="results/raw/run/config.resolved.json",
        provider="aws", scenario=resolved.scenario,
        enabled_phases=[item.phase for item in resolved.measurement_units],
        expected_vm_roles=["vm_a", "vm_b"], measurement_direction="vm_a_to_vm_b",
        artifacts=artifacts, measurement_units=resolved.measurement_units,
        execution_state="initialized", cleanup_state="not_started",
        failure=None, cleanup_failure=None, vm_metadata={"unavailable_reason":"pending"},
        provider_metadata={"unavailable_reason":"pending"}, tool_versions={"unavailable_reason":"pending"},
        implementation_git_commit={"unavailable_reason":"pending"}, design_git_commit={"unavailable_reason":"pending"},
    )
    assert value.identity.run_id == "campaign-aws"
    assert value.enabled_phases == ["idle_latency", "single_flow", "multi_flow"]
    with pytest.raises(ValueError, match="calibration units"):
        ValidatorInput.model_validate({**value.model_dump(mode="python"), "mode":"calibration"})
    declared = artifacts["config_snapshot"]
    wrong_deployment = {
        **value.model_dump(mode="python"),
        "artifact_records":[{
            "artifact_id":declared.artifact_id, "path":declared.path, "kind":declared.kind,
            "producer":declared.producer, "unit_id":declared.unit_id,
            "deployment_id":"another-aws-deployment", "closed":True,
            "size_bytes":{"value":1}, "sha256":{"value":"a"*64}, "retrieval_state":"verified",
        }],
    }
    with pytest.raises(ValueError, match="artifact evidence must match deployment"):
        ValidatorInput.model_validate(wrong_deployment)


def test_validation_result_keeps_data_and_comparison_status_separate() -> None:
    result = ValidationResult(schema_version=2, run_id="campaign-aws", mode="acceptance",
                              validation_status=ValidationStatus(),
                              evaluated_at=datetime(2026, 10, 21, tzinfo=timezone.utc))
    assert result.validation_status.data_validity == "not_validated"
    assert result.metrics.unavailable_reason == "metrics not produced"


@pytest.mark.parametrize("validity,eligibility", [
    ("valid", "eligible"), ("valid", "ineligible"), ("partial", "ineligible"),
    ("invalid", "unavailable"), ("not_validated", "not_evaluated"),
])
def test_validation_result_accepts_normative_status_pairs(validity, eligibility) -> None:
    status = ValidationStatus(data_validity=validity, data_reasons=[] if validity == "valid" else ["reason"],
                              comparison_eligibility=eligibility,
                              comparison_reasons=[] if eligibility == "eligible" else ["reason"])
    result = ValidationResult(schema_version=2, run_id="run-aws", mode="acceptance", validation_status=status,
                              evaluated_at=datetime.now(timezone.utc))
    assert result.validation_status.comparison_eligibility == eligibility


def test_validation_result_rejects_invalid_data_marked_eligible_and_naive_time() -> None:
    with pytest.raises(ValueError, match="eligible comparison requires valid data"):
        ValidationResult(schema_version=2, run_id="run-aws", mode="acceptance",
                         validation_status={"data_validity":"invalid", "data_reasons":["bad"],
                                            "comparison_eligibility":"eligible", "comparison_reasons":[]},
                         evaluated_at=datetime.now(timezone.utc))
    with pytest.raises(ValueError, match="evaluated_at must include a timezone"):
        ValidationResult(schema_version=2, run_id="run-aws", mode="acceptance", validation_status=ValidationStatus(),
                         evaluated_at=datetime(2026, 10, 21))
