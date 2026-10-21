from datetime import datetime, timezone
import pytest
from pydantic import ValidationError

from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.contracts import (
    ARTIFACTS, ArtifactExpectation, ArtifactRecord, IdentityContext, PhaseConfiguration, ValidationResult, VmMetadata,
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
    assert resolved.observations[0].artifact_layout.mode == "short_study"
    assert resolved.observations[0].artifact_layout.measurement_unit_ids == [item.unit_id for item in resolved.measurement_units]


def test_documented_artifact_layout_entrypoint_returns_mode_derived_layout(repository_root) -> None:
    from cloud_network_benchmark.contracts.artifacts import ArtifactLayout

    for config_name, expected_mode in (
        ("exp-900-single-provider.yaml", "acceptance"),
        ("exp-001-multi-provider.yaml", "short_study"),
        ("exp-001-calibration.yaml", "calibration"),
        ("exp-001-diagnostic-day.yaml", "diagnostic_day"),
    ):
        path = (repository_root / "configs/tests" / config_name) if expected_mode == "acceptance" else (repository_root / "configs/experiments" / config_name)
        resolved, _ = resolve_campaign(path, repository_root)
        layout = ArtifactLayout.for_mode(resolved.mode, resolved.measurement_units)
        assert isinstance(layout, ArtifactLayout)
        assert layout.mode == expected_mode
        assert layout.measurement_unit_ids == [item.unit_id for item in resolved.measurement_units]
        assert resolved.observations[0].artifact_layout == layout
        native = artifact_inputs(layout, mode=resolved.mode, measurement_units=resolved.measurement_units)
        if expected_mode == "calibration":
            assert len([name for name in native if name.endswith("-iperf3")]) == 12
            assert not any(name.startswith("day-") for name in native)
        elif expected_mode == "diagnostic_day":
            assert "day-iperf3-client" in native and "day-probe-csv" in native
            assert not any(name.endswith("-sockperf") or name.endswith("-iperf3") for name in native)
        else:
            assert all(f"{unit.unit_id}-sockperf" in native for unit in resolved.measurement_units)
            assert "idle_latency-iperf3" not in native


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

    partial_diagnostics = {
        **value.model_dump(mode="python"),
        "diagnostics":[{
            "schema_version":2, "deployment_id":identity.deployment_id, "vm_role":"vm_a",
            "sampled_at":datetime(2026, 10, 21, tzinfo=timezone.utc), "monotonic_seconds":1,
            "interval_seconds":1, **{name:{"unavailable_reason":"not collected"} for name in (
                "cpu_total_percent", "cpu_max_core_percent", "cpu_steal_percent", "tcp_retransmissions",
                "interface_rx_drops", "interface_tx_drops", "interface_rx_errors", "interface_tx_errors",
                "rx_bytes", "tx_bytes")},
        }],
    }
    partial_diagnostics["vm_metadata_records"] = [VmMetadata(
        schema_version=2, deployment_id=identity.deployment_id, vm_role="vm_a", provider="aws",
        cpu_model={"unavailable_reason":"not observed"}, nic_driver={"unavailable_reason":"not observed"},
        acceleration={"unavailable_reason":"not observed"}, image={"unavailable_reason":"not observed"},
        kernel={"unavailable_reason":"not observed"}, congestion_control={"unavailable_reason":"not observed"},
        tool_versions={"unavailable_reason":"not observed"}, source_builds={"unavailable_reason":"not observed"},
        documented_network_limit={"unavailable_reason":"not observed"}, mtu={"unavailable_reason":"not observed"},
        observed_region={"unavailable_reason":"not observed"}, observed_zone={"unavailable_reason":"not observed"},
        physical_zone_id={"unavailable_reason":"not observed"}, actual_shape={"unavailable_reason":"not observed"},
    )]
    partially_observed = ValidatorInput.model_validate(partial_diagnostics)
    assert len(partially_observed.diagnostics) == 1 and len(partially_observed.vm_metadata_records) == 1


def test_validator_input_accepts_parent_linked_day_probe_chunks_and_rejects_wrong_phase(repository_root) -> None:
    day, _ = resolve_campaign(repository_root / "configs/experiments/exp-001-diagnostic-day.yaml", repository_root)
    obs = day.observations[0]
    parent = obs.measurement_units[0]
    chunk = PhaseConfiguration(unit_id="probe-chunk-001", kind="probe_chunk", phase="multi_flow",
                               measured_seconds=300, streams=None, parent_unit_id=parent.unit_id)
    units = [parent, chunk]
    identity = IdentityContext(
        experiment_id=day.experiment_id, config_id=day.config_id, campaign_id="campaign-day",
        observation_id="campaign-day-aws-observation", run_id="campaign-day-aws",
        deployment_id="campaign-day-aws-deployment", provider="aws",
    )
    artifacts = artifact_inputs(obs.artifact_layout, mode=day.mode, measurement_units=units)
    record = ArtifactRecord(
        artifact_id="day-probe-csv", path="measurements/day/probes/probe-chunk-001.csv", kind="sockperf-csv",
        producer="vm_a", unit_id=parent.unit_id, deployment_id=identity.deployment_id, closed=True,
        size_bytes={"value":4}, sha256={"value":"a"*64}, retrieval_state="verified",
    )
    candidate = ValidatorInput(
        schema_version=2, identity=identity, mode=day.mode, context=day.context,
        campaign_manifest_path="results/manifests/campaign.json", child_manifest_path="results/manifests/run.json",
        child_result_root="results/raw/run", config_snapshot_path="results/raw/run/config.yaml",
        config_sha256=day.config_source.sha256, resolved_snapshot_path="results/raw/run/config.resolved.json",
        provider="aws", scenario=day.scenario, enabled_phases=["multi_flow"],
        expected_vm_roles=["vm_a", "vm_b"], measurement_direction="vm_a_to_vm_b", artifacts=artifacts,
        measurement_units=units, artifact_records=[record], execution_state="running", cleanup_state="not_started",
        failure=None, cleanup_failure=None, vm_metadata={"unavailable_reason":"pending"},
        provider_metadata={"unavailable_reason":"pending"}, tool_versions={"unavailable_reason":"pending"},
        implementation_git_commit={"unavailable_reason":"pending"}, design_git_commit={"unavailable_reason":"pending"},
    )
    assert candidate.measurement_units[1].parent_unit_id == parent.unit_id
    malformed_parent = {**parent.model_dump(mode="python"), "phase":"idle_latency"}
    bad_parent = PhaseConfiguration.model_construct(**malformed_parent)
    with pytest.raises(ValidationError, match="continuous_day"):
        ValidatorInput.model_validate({**candidate.model_dump(mode="python"),
                                       "measurement_units":[bad_parent], "enabled_phases":["idle_latency"],
                                       "artifact_records":[]})
    with pytest.raises(ValidationError):
        PhaseConfiguration.model_validate(malformed_parent)
    with pytest.raises(ValidationError):
        PhaseConfiguration(unit_id="bad-single", kind="phase", phase="single_flow", measured_seconds=60, streams=4)


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
