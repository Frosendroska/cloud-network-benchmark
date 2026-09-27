import cloud_network_benchmark.contracts as contracts


def test_reviewed_public_surface_is_importable() -> None:
    required = {
        "ProviderDeploymentInput", "ProviderDeploymentOutput", "RemoteConnectionData", "CommandRequest",
        "CommandResult", "CommandRunner", "ScriptedCommandRunner", "ValidatorInput", "ArtifactLayout",
        "ExecutionState", "CleanupState", "FailureEvidence", "StructuredEvidence",
    }
    assert required <= set(contracts.__all__)
