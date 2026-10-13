from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import Field, model_validator

from .artifacts import ArtifactLayout
from .common import (
    ArtifactExpectation,
    BenchmarkPhase,
    CleanupState,
    ExecutionState,
    FailureEvidence,
    MeasurementDirection,
    Provider,
    Scenario,
    StringEvidence,
    StrictModel,
    StructuredEvidence,
    VmRole,
)


class ArtifactInput(StrictModel):
    path: str
    expectation: ArtifactExpectation


class ValidatorInput(StrictModel):
    campaign_manifest_path: str
    child_manifest_path: str
    child_result_root: str
    config_snapshot_path: str
    config_sha256: str
    provider: Provider
    scenario: Scenario
    enabled_phases: List[BenchmarkPhase]
    expected_vm_roles: List[VmRole]
    measurement_direction: MeasurementDirection
    artifacts: Dict[str, ArtifactInput]
    execution_state: ExecutionState
    cleanup_state: CleanupState
    failure: Optional[FailureEvidence]
    cleanup_failure: Optional[FailureEvidence]
    vm_metadata: StructuredEvidence
    provider_metadata: StructuredEvidence
    tool_versions: StructuredEvidence
    implementation_git_commit: StringEvidence
    design_git_commit: StringEvidence

    @model_validator(mode="after")
    def canonical_roles(self) -> "ValidatorInput":
        if self.expected_vm_roles != [VmRole.VM_A.value, VmRole.VM_B.value]:
            raise ValueError("expected_vm_roles must be [vm_a, vm_b]")
        if len(set(self.enabled_phases)) != len(self.enabled_phases):
            raise ValueError("enabled_phases must be unique")
        return self


def artifact_inputs(layout: ArtifactLayout, multi_flow_enabled: bool = True) -> Dict[str, ArtifactInput]:
    result: Dict[str, ArtifactInput] = {}
    for name, path in layout.as_mapping().items():
        expectation = ArtifactExpectation.REQUIRED
        if not multi_flow_enabled and ("multi_flow" in name):
            expectation = ArtifactExpectation.NOT_EXPECTED
        if name in {"validation_result", "validation_summary"}:
            expectation = ArtifactExpectation.OPTIONAL
        result[name] = ArtifactInput(path=path, expectation=expectation)
    return result
