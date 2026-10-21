from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.contracts import (
    ARTIFACTS, ArtifactExpectation, IdentityContext, ValidationResult,
    ValidatorInput, artifact_inputs,
)


def test_mode_aware_artifacts_cover_exact_units_and_schedule_expectation(repository_root) -> None:
    resolved, _ = resolve_campaign(repository_root / "configs/experiments/exp-001-multi-provider.yaml", repository_root)
    selected = artifact_inputs(ARTIFACTS, mode=resolved.mode, measurement_units=resolved.measurement_units)
    assert selected["config_snapshot"].expectation == ArtifactExpectation.REQUIRED.value
    assert selected["resolved_snapshot"].expectation == ArtifactExpectation.REQUIRED.value
    assert selected["schedule_snapshot"].expectation == ArtifactExpectation.NOT_EXPECTED.value
    assert not any("flent" in key for key in selected)
    assert any(value.unit_id == "idle_latency" for value in selected.values())
    assert selected["validation_result"].expectation == ArtifactExpectation.OPTIONAL.value


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
        provider="aws", scenario=resolved.scenario,
        enabled_phases=[item.phase for item in resolved.measurement_units],
        expected_vm_roles=["vm_a", "vm_b"], measurement_direction="vm_a_to_vm_b",
        artifacts=artifacts, execution_state="initialized", cleanup_state="not_started",
        failure=None, cleanup_failure=None, vm_metadata={"unavailable_reason":"pending"},
        provider_metadata={"unavailable_reason":"pending"}, tool_versions={"unavailable_reason":"pending"},
        implementation_git_commit={"unavailable_reason":"pending"}, design_git_commit={"unavailable_reason":"pending"},
    )
    assert value.identity.run_id == "campaign-aws"
    assert value.enabled_phases == ["idle_latency", "single_flow", "multi_flow"]


def test_validation_result_keeps_data_and_comparison_status_separate() -> None:
    identity = IdentityContext(
        experiment_id="EXP-900", config_id="exp_900_aws_validation", campaign_id="campaign",
        observation_id="campaign-aws-observation", run_id="campaign-aws",
        deployment_id="campaign-aws-deployment", provider="aws",
    )
    result = ValidationResult(identity=identity, mode="acceptance", status="not_validated",
                              comparison_eligibility="not_evaluated", data_reasons=["not validated"],
                              comparison_reasons=["not evaluated"], source_sha256={"unavailable_reason":"pending"},
                              resolved_sha256={"unavailable_reason":"pending"})
    assert result.status == "not_validated"
