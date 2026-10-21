from __future__ import annotations

from typing import Dict, List, Optional
from typing import Literal

from pydantic import Field, model_validator

from .artifacts import ArtifactInput, ArtifactLayout
from .common import (
    ArtifactExpectation,
    BenchmarkPhase,
    CleanupState,
    ExecutionState,
    FailureEvidence,
    MeasurementDirection,
    Provider,
    ExperimentMode,
    ComparisonEligibility,
    DataValidity,
    Evidence,
    IdentityContext,
    GitSnapshot,
    ValidationStatus,
    Scenario,
    StringEvidence,
    StrictModel,
    StructuredEvidence,
    VmRole,
)
from .campaign import PhaseConfiguration
from .measurements import CommandEvidence, DiagnosticSample, MeasurementTiming
from .admission import ExpiryEvidence, ReservationEvidence
from .artifacts import ArtifactRecord


class ValidatorInput(StrictModel):
    schema_version: Literal[2] = 2
    identity: IdentityContext
    mode: ExperimentMode
    context: Literal["standard", "failure_check", "replacement"]
    campaign_manifest_path: str
    child_manifest_path: str
    child_result_root: str
    config_snapshot_path: str
    config_sha256: str
    resolved_sha256: Evidence[str] = Field(default_factory=lambda: Evidence[str](unavailable_reason="not supplied"))
    schedule_sha256: Evidence[str] = Field(default_factory=lambda: Evidence[str](unavailable_reason="no schedule row"))
    provider: Provider
    scenario: Scenario
    enabled_phases: List[BenchmarkPhase]
    expected_vm_roles: List[VmRole]
    measurement_direction: MeasurementDirection
    artifacts: Dict[str, ArtifactInput]
    measurement_units: List[PhaseConfiguration] = Field(default_factory=list)
    artifact_records: List[ArtifactRecord] = Field(default_factory=list)
    commands: List[CommandEvidence] = Field(default_factory=list)
    timings: List[MeasurementTiming] = Field(default_factory=list)
    diagnostics: List[DiagnosticSample] = Field(default_factory=list)
    execution_state: ExecutionState
    cleanup_state: CleanupState
    failure: Optional[FailureEvidence]
    cleanup_failure: Optional[FailureEvidence]
    retrieval_failure: Optional[FailureEvidence] = None
    vm_metadata: StructuredEvidence
    provider_metadata: StructuredEvidence
    tool_versions: StructuredEvidence
    implementation_git_commit: StringEvidence
    design_git_commit: StringEvidence
    implementation_git: GitSnapshot = Field(default_factory=lambda: GitSnapshot(commit=StringEvidence(unavailable_reason="not supplied"), dirty=Evidence[bool](unavailable_reason="not supplied"), diff_sha256=StringEvidence(unavailable_reason="not supplied")))
    design_git: GitSnapshot = Field(default_factory=lambda: GitSnapshot(commit=StringEvidence(unavailable_reason="not supplied"), dirty=Evidence[bool](unavailable_reason="not supplied"), diff_sha256=StringEvidence(unavailable_reason="not supplied")))
    validation_status: ValidationStatus = Field(default_factory=ValidationStatus)
    reservation: Evidence[ReservationEvidence] = Field(default_factory=lambda: Evidence[ReservationEvidence](unavailable_reason="not evaluated"))
    expiry: Evidence[ExpiryEvidence] = Field(default_factory=lambda: Evidence[ExpiryEvidence](unavailable_reason="not registered"))

    @model_validator(mode="after")
    def canonical_roles(self) -> "ValidatorInput":
        if self.expected_vm_roles != [VmRole.VM_A.value, VmRole.VM_B.value]:
            raise ValueError("expected_vm_roles must be [vm_a, vm_b]")
        if len(set(self.enabled_phases)) != len(self.enabled_phases):
            raise ValueError("enabled_phases must be unique")
        if self.identity.provider != self.provider:
            raise ValueError("identity provider must match ValidatorInput provider")
        record_ids = [(item.artifact_id, item.path, item.unit_id) for item in self.artifact_records]
        if len(record_ids) != len(set(record_ids)):
            raise ValueError("artifact records must have unique concrete identity")
        return self


class ValidationFinding(StrictModel):
    code: str = Field(min_length=1)
    severity: Literal["info", "warning", "error"]
    message: str = Field(min_length=1)
    unit_id: Optional[str] = None
    artifact_id: Optional[str] = None


class ValidationResult(StrictModel):
    schema_version: Literal[2] = 2
    identity: IdentityContext
    mode: ExperimentMode
    status: DataValidity
    comparison_eligibility: ComparisonEligibility
    data_reasons: List[str] = Field(default_factory=list)
    comparison_reasons: List[str] = Field(default_factory=list)
    findings: List[ValidationFinding] = Field(default_factory=list)
    source_sha256: Evidence[str]
    resolved_sha256: Evidence[str]
    artifact_records: List[dict[str, object]] = Field(default_factory=list)

    @model_validator(mode="after")
    def reasons_for_unresolved(self) -> "ValidationResult":
        if self.status != DataValidity.VALID and not self.data_reasons:
            raise ValueError("non-valid result requires data_reasons")
        if self.comparison_eligibility != ComparisonEligibility.ELIGIBLE and not self.comparison_reasons:
            raise ValueError("non-eligible result requires comparison_reasons")
        for evidence in (self.source_sha256, self.resolved_sha256):
            if evidence.value is not None and (len(evidence.value) != 64 or any(ch not in "0123456789abcdef" for ch in evidence.value)):
                raise ValueError("source and resolved hashes must be lowercase SHA-256")
        if self.comparison_eligibility == ComparisonEligibility.ELIGIBLE and (
            self.source_sha256.value is None or self.resolved_sha256.value is None
        ):
            raise ValueError("eligible results require observed source and resolved hashes")
        return self

def artifact_inputs(
    layout: ArtifactLayout,
    multi_flow_enabled: bool = True,
    *,
    mode: Optional[ExperimentMode] = None,
    measurement_units: Optional[list[object]] = None,
    schedule_supplied: bool = False,
) -> Dict[str, ArtifactInput]:
    result: Dict[str, ArtifactInput] = {}
    declarations = layout.for_mode(mode, measurement_units or [], schedule_supplied=schedule_supplied) if mode is not None else [
        ArtifactInput(artifact_id=name, path=path, kind=name, producer="controller", unit_id=None,
                      expectation=ArtifactExpectation.REQUIRED)
        for name, path in layout.as_mapping().items()
    ]
    for declaration in declarations:
        name = declaration.artifact_id
        path = declaration.path or declaration.path_template
        expectation = ArtifactExpectation(declaration.expectation)
        if not multi_flow_enabled and ("multi_flow" in name):
            expectation = ArtifactExpectation.NOT_EXPECTED
        if name in {"validation_result", "validation_summary", "checksums"}:
            expectation = ArtifactExpectation.OPTIONAL
        result[name] = ArtifactInput(
            artifact_id=declaration.artifact_id, path=declaration.path,
            path_template=declaration.path_template, kind=declaration.kind,
            producer=declaration.producer, unit_id=declaration.unit_id, expectation=expectation,
        )
    return result
