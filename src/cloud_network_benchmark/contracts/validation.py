from __future__ import annotations

from typing import Dict, Optional

from pydantic import Field

from .artifacts import ArtifactLayout
from .common import (
    ArtifactExpectation,
    CleanupState,
    ExecutionState,
    FailureEvidence,
    Provider,
    Scenario,
    StringEvidence,
    StrictModel,
    StructuredEvidence,
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
