from __future__ import annotations

from typing import Dict, List, Optional
from typing import Literal
from datetime import datetime
import re
from pathlib import PurePosixPath

from pydantic import Field, model_validator

from .artifacts import ArtifactInput, ArtifactLayout
from .common import (
    ArtifactExpectation,
    BenchmarkPhase,
    CleanupState,
    ExecutionState,
    FailureEvidence,
    IDENTIFIER_PATTERN,
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
    utc_datetime,
)
from .campaign import PhaseConfiguration
from .measurements import CommandEvidence, DiagnosticSample, MeasurementTiming, VmMetadata
from .admission import ExpiryEvidence, ReservationEvidence, ScheduleEntry
from .artifacts import ArtifactRecord


class ValidatorInput(StrictModel):
    schema_version: Literal[2]
    identity: IdentityContext
    mode: ExperimentMode
    context: Literal["standard", "failure_check", "replacement"]
    campaign_manifest_path: str
    child_manifest_path: str
    child_result_root: str
    config_snapshot_path: str
    config_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    resolved_snapshot_path: str
    resolved_sha256: Evidence[str] = Field(default_factory=lambda: Evidence[str](unavailable_reason="not supplied"))
    schedule_sha256: Evidence[str] = Field(default_factory=lambda: Evidence[str](unavailable_reason="no schedule row"))
    schedule_snapshot_path: Evidence[str] = Field(default_factory=lambda: Evidence[str](unavailable_reason="no schedule row"))
    schedule_entry: Evidence[ScheduleEntry] = Field(default_factory=lambda: Evidence[ScheduleEntry](unavailable_reason="no schedule row"))
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
    vm_metadata_records: List[VmMetadata] = Field(default_factory=list)
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
        if not self.identity.run_id.endswith(f"-{Provider(self.provider).value}") or self.identity.deployment_id != f"{self.identity.run_id}-deployment":
            raise ValueError("ValidatorInput run and deployment identity must match provider")
        unit_ids = [item.unit_id for item in self.measurement_units]
        if len(unit_ids) != len(set(unit_ids)):
            raise ValueError("measurement unit IDs must be unique")
        if ExperimentMode(self.mode) in {ExperimentMode.ACCEPTANCE, ExperimentMode.SHORT_STUDY}:
            if [item.phase for item in self.measurement_units] != [BenchmarkPhase.IDLE_LATENCY, BenchmarkPhase.SINGLE_FLOW, BenchmarkPhase.MULTI_FLOW] or any(item.kind != "phase" for item in self.measurement_units):
                raise ValueError("short and acceptance units must contain the ordered three phases")
            expected_duration = 10 if ExperimentMode(self.mode) == ExperimentMode.ACCEPTANCE else 60
            if any(item.measured_seconds != expected_duration for item in self.measurement_units):
                raise ValueError("short and acceptance unit durations must match mode")
        elif ExperimentMode(self.mode) == ExperimentMode.CALIBRATION:
            expected_streams = [1, 4, 2, 8, 8, 2, 4, 1, 2, 8, 1, 4]
            if len(self.measurement_units) != 12 or any(item.kind != "calibration_trial" or item.phase != BenchmarkPhase.MULTI_FLOW or item.trial_index != index or item.streams != expected_streams[index-1] or item.measured_seconds != 60 for index, item in enumerate(self.measurement_units, 1)):
                raise ValueError("calibration units must contain twelve ordered aggregate trials")
        else:
            day_units = [item for item in self.measurement_units if item.kind == "continuous_day"]
            chunks = [item for item in self.measurement_units if item.kind == "probe_chunk"]
            if (len(day_units) != 1 or day_units[0].phase != BenchmarkPhase.MULTI_FLOW or day_units[0].measured_seconds != 86400
                    or any(item.parent_unit_id != day_units[0].unit_id for item in chunks)
                    or len(chunks) + 1 != len(self.measurement_units)):
                raise ValueError("diagnostic_day requires one retained 86400-second multi_flow unit and only its declared probe chunks")
        unit_set = set(unit_ids)
        if list(dict.fromkeys(item.phase for item in self.measurement_units)) != self.enabled_phases:
            raise ValueError("enabled_phases must match measurement unit phases")
        if any(item.deployment_id != self.identity.deployment_id or item.unit_id not in unit_set for item in self.commands):
            raise ValueError("command evidence must match deployment and declared units")
        if any(item.deployment_id != self.identity.deployment_id or item.unit_id not in unit_set for item in self.timings):
            raise ValueError("measurement timing must match deployment and declared units")
        if any(item.deployment_id != self.identity.deployment_id for item in self.diagnostics):
            raise ValueError("diagnostic evidence must match deployment")
        if any(item.deployment_id != self.identity.deployment_id for item in self.vm_metadata_records):
            raise ValueError("VM metadata must match deployment")
        if len({item.vm_role for item in self.vm_metadata_records}) != len(self.vm_metadata_records):
            raise ValueError("VM metadata may contain at most one record per available role")
        if self.vm_metadata_records and any(item.provider != self.provider for item in self.vm_metadata_records):
            raise ValueError("VM metadata must match the selected provider")
        record_ids = [(item.artifact_id, item.path, item.unit_id) for item in self.artifact_records]
        if len(record_ids) != len(set(record_ids)):
            raise ValueError("artifact records must have unique concrete identity")
        for record in self.artifact_records:
            if record.deployment_id != self.identity.deployment_id:
                raise ValueError("artifact evidence must match deployment")
            declaration = self.artifacts.get(record.artifact_id)
            if declaration is None:
                raise ValueError("artifact evidence must match its declared input")
            expected_path = declaration.path
            if expected_path is None and declaration.path_template is not None:
                pattern = "^" + re.escape(declaration.path_template).replace(r"\{chunk_id\}", r"[A-Za-z0-9][A-Za-z0-9._-]{0,191}") + "$"
                path_matches = re.fullmatch(pattern, record.path) is not None
            else:
                path_matches = record.path == expected_path
            dynamic_day_record = (record.artifact_id in {"day-probe-csv", "day-probe-log", "day-probe-observation"}
                                  and declaration.path_template is not None
                                  and record.unit_id == next((unit.unit_id for unit in self.measurement_units if unit.kind == "continuous_day"), None))
            compatible_legacy_kind = (record.artifact_id == "iperf3_server_error"
                                       and record.kind == "iperf3-server-error"
                                       and declaration.kind == "iperf3-observation-stderr")
            if not path_matches or (record.kind != declaration.kind and not compatible_legacy_kind) or record.producer != declaration.producer or (
                record.unit_id != declaration.unit_id and not dynamic_day_record
            ):
                raise ValueError("artifact evidence must match its declared input")
            expected_hash = {
                "config_snapshot": self.config_sha256,
                "resolved_snapshot": self.resolved_sha256.value,
                "schedule_snapshot": self.schedule_sha256.value,
            }.get(record.artifact_id)
            if expected_hash is not None and record.sha256.value != expected_hash:
                raise ValueError("snapshot artifact hash must match supplied source evidence")
        if self.reservation.value is not None and self.reservation.value.campaign_id != self.identity.campaign_id:
            raise ValueError("reservation evidence must match identity campaign")
        if self.expiry.value is not None and (
            self.expiry.value.run_id != self.identity.run_id or self.expiry.value.deployment_id != self.identity.deployment_id
            or self.expiry.value.provider != self.provider
        ):
            raise ValueError("expiry evidence must match identity run, deployment and provider")
        if self.schedule_entry.value is None and self.schedule_snapshot_path.value is not None:
            raise ValueError("schedule snapshot path requires a supplied schedule entry")
        if self.schedule_entry.value is not None and (
            self.schedule_entry.value.config_id != self.identity.config_id
            or self.schedule_entry.value.experiment_id != self.identity.experiment_id
        ):
            raise ValueError("schedule row identity must match ValidatorInput")
        for field_name, actual in (("config_snapshot", self.config_snapshot_path),
                                   ("resolved_snapshot", self.resolved_snapshot_path)):
            declaration = self.artifacts.get(field_name)
            expected_paths = {declaration.path, str(PurePosixPath(self.child_result_root) / declaration.path)} if declaration is not None and declaration.path else set()
            if actual not in expected_paths:
                raise ValueError(f"{field_name} path must match artifact declaration")
        if self.schedule_snapshot_path.value is not None:
            declaration = self.artifacts.get("schedule_snapshot")
            expected_paths = {declaration.path, str(PurePosixPath(self.child_result_root) / declaration.path)} if declaration is not None and declaration.path else set()
            if self.schedule_snapshot_path.value not in expected_paths:
                raise ValueError("schedule snapshot path must match artifact declaration")
        for evidence in (self.resolved_sha256.value, self.schedule_sha256.value):
            if evidence is not None and (len(evidence) != 64 or any(ch not in "0123456789abcdef" for ch in evidence)):
                raise ValueError("configuration and schedule hashes must be lowercase SHA-256")
        return self


class ValidationFinding(StrictModel):
    code: str = Field(min_length=1, pattern=r"\S")
    severity: Literal["info", "warning", "error"]
    message: str = Field(min_length=1, pattern=r"\S")
    unit_id: Optional[str] = None
    artifact_id: Optional[str] = None


class ValidationResult(StrictModel):
    schema_version: Literal[2]
    run_id: str = Field(pattern=IDENTIFIER_PATTERN)
    mode: ExperimentMode
    validation_status: ValidationStatus
    findings: List[ValidationFinding] = Field(default_factory=list)
    metrics: StructuredEvidence = Field(default_factory=lambda: StructuredEvidence(unavailable_reason="metrics not produced"))
    evaluated_at: datetime

    @model_validator(mode="after")
    def status_matches_data(self) -> "ValidationResult":
        if self.validation_status.comparison_eligibility == ComparisonEligibility.ELIGIBLE and self.validation_status.data_validity != DataValidity.VALID:
            raise ValueError("eligible comparison requires valid data")
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must include a timezone")
        object.__setattr__(self, "evaluated_at", utc_datetime(self.evaluated_at))
        return self

def artifact_inputs(
    layout: ArtifactLayout,
    *,
    mode: ExperimentMode,
    measurement_units: list[object],
    schedule_supplied: bool = False,
) -> Dict[str, ArtifactInput]:
    result: Dict[str, ArtifactInput] = {}
    declarations = layout.declarations_for_mode(mode, measurement_units, schedule_supplied=schedule_supplied)
    for declaration in declarations:
        name = declaration.artifact_id
        result[name] = ArtifactInput(
            artifact_id=declaration.artifact_id, path=declaration.path,
            path_template=declaration.path_template, kind=declaration.kind,
            producer=declaration.producer, unit_id=declaration.unit_id, expectation=declaration.expectation,
        )
    return result
