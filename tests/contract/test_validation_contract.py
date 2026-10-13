from cloud_network_benchmark.contracts import (
    ARTIFACTS, ArtifactExpectation, StringEvidence, StructuredEvidence,
    ValidatorInput, artifact_inputs,
)


def test_validator_inputs_cover_every_artifact() -> None:
    enabled = artifact_inputs(ARTIFACTS, True)
    assert set(enabled) == set(ARTIFACTS.model_dump())
    assert enabled["idle_flent"].expectation == ArtifactExpectation.REQUIRED.value
    assert enabled["validation_result"].expectation == ArtifactExpectation.OPTIONAL.value


def test_disabled_multi_flow_remains_named_not_expected() -> None:
    disabled = artifact_inputs(ARTIFACTS, False)
    assert disabled["multi_flow_flent"].expectation == ArtifactExpectation.NOT_EXPECTED.value
    assert disabled["multi_flow_vm_a_diagnostics"].path == "diagnostics/multi_flow/vm_a.json"


def test_validator_input_carries_phases_roles_and_direction() -> None:
    unavailable = StructuredEvidence(unavailable_reason="not collected")
    value = ValidatorInput(
        campaign_manifest_path="results/manifests/campaign.json",
        child_manifest_path="results/manifests/run.json",
        child_result_root="results/raw/run",
        config_snapshot_path="results/raw/run/config.yaml",
        config_sha256="a" * 64,
        provider="aws",
        scenario="inter_region",
        enabled_phases=["idle_latency", "single_flow"],
        expected_vm_roles=["vm_a", "vm_b"],
        measurement_direction="vm_a_to_vm_b",
        artifacts=artifact_inputs(ARTIFACTS, False),
        execution_state="collecting",
        cleanup_state="not_started",
        failure=None,
        cleanup_failure=None,
        vm_metadata=unavailable,
        provider_metadata=unavailable,
        tool_versions=unavailable,
        implementation_git_commit=StringEvidence(value="a" * 40),
        design_git_commit=StringEvidence(unavailable_reason="unavailable"),
    )
    assert value.enabled_phases == ["idle_latency", "single_flow"]
    assert value.expected_vm_roles == ["vm_a", "vm_b"]
    assert value.measurement_direction == "vm_a_to_vm_b"
