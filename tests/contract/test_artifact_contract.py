import pytest

from cloud_network_benchmark.contracts import (
    ARTIFACTS,
    ArtifactExpectation,
    BenchmarkPhase,
    CampaignState,
    CleanupState,
    CommandOutcome,
    ExecutionState,
    MeasurementDirection,
    Provider,
    Scenario,
    VmRole,
    ArtifactLayout,
)


def values(enum: object) -> set[str]:
    return {item.value for item in enum}


def test_stable_enum_values() -> None:
    assert values(Provider) == {"aws", "azure", "gcp"}
    assert values(Scenario) == {"same_zone", "cross_zone", "placement_optimization", "inter_region"}
    assert values(VmRole) == {"vm_a", "vm_b"}
    assert values(MeasurementDirection) == {"vm_a_to_vm_b"}
    assert values(BenchmarkPhase) == {"idle_latency", "single_flow", "multi_flow"}
    assert values(CommandOutcome) == {"succeeded", "failed", "timed_out", "interrupted"}
    assert values(ArtifactExpectation) == {"required", "optional", "not_expected"}
    assert values(ExecutionState) == {"initialized", "provision_ready", "running", "collecting", "validating", "succeeded", "failed", "interrupted"}
    assert values(CleanupState) == {"not_started", "not_required", "attempted", "succeeded", "failed"}
    assert values(CampaignState) == {"initialized", "active", "partially_complete", "succeeded", "failed", "interrupted"}


def test_canonical_artifact_paths() -> None:
    assert ARTIFACTS.config_snapshot == "config.yaml"
    assert ARTIFACTS.provider_outputs == "terraform-outputs.json"
    assert ARTIFACTS.idle_flent == "flent/idle.flent.gz"
    assert ARTIFACTS.vm_a_metadata == "metadata/vm_a.json"
    assert ARTIFACTS.validation_summary == "validation/summary.txt"


def test_artifact_paths_cannot_be_overridden() -> None:
    with pytest.raises(ValueError):
        ArtifactLayout(config_snapshot="../../outside")
