from cloud_network_benchmark.contracts import ARTIFACTS, ArtifactExpectation, artifact_inputs


def test_validator_inputs_cover_every_artifact() -> None:
    enabled = artifact_inputs(ARTIFACTS, True)
    assert set(enabled) == set(ARTIFACTS.model_dump())
    assert enabled["idle_flent"].expectation == ArtifactExpectation.REQUIRED.value
    assert enabled["validation_result"].expectation == ArtifactExpectation.OPTIONAL.value


def test_disabled_multi_flow_remains_named_not_expected() -> None:
    disabled = artifact_inputs(ARTIFACTS, False)
    assert disabled["multi_flow_flent"].expectation == ArtifactExpectation.NOT_EXPECTED.value
    assert disabled["multi_flow_vm_a_diagnostics"].path == "diagnostics/multi_flow/vm_a.json"
