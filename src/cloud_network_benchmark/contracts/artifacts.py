from __future__ import annotations

from pathlib import PurePosixPath
from pathlib import Path
import re
from typing import Dict, Literal, Optional

from pydantic import Field, model_validator

from .common import ArtifactExpectation, FailureEvidence, StrictModel, StringEvidence, Evidence, VmRole


def _normalized_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or "\\" in value or path.is_absolute() or path.as_posix() != value:
        raise ValueError("artifact destination must be a normalized relative POSIX path")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("artifact destination must not contain traversal components")
    return value


def resolve_artifact_path(child_root: Path, relative_path: str) -> Path:
    """Resolve a concrete artifact path and reject symlink escape from its child root."""
    _normalized_relative_path(relative_path)
    root = child_root.resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("artifact destination escapes the child result root") from exc
    return candidate


class ArtifactInput(StrictModel):
    artifact_id: str = Field(min_length=1)
    path: Optional[str] = None
    path_template: Optional[str] = None
    kind: str = Field(min_length=1)
    producer: VmRole | Literal["controller"]
    unit_id: Optional[str] = None
    expectation: ArtifactExpectation

    @model_validator(mode="after")
    def one_destination(self) -> "ArtifactInput":
        if (self.path is None) == (self.path_template is None):
            raise ValueError("exactly one of path or path_template is required")
        if self.path is not None:
            _normalized_relative_path(self.path)
        if self.path_template is not None:
            _normalized_relative_path(self.path_template)
            if "{" not in self.path_template or "}" not in self.path_template:
                raise ValueError("path_template must declare a dynamic component")
        return self


class ArtifactRecord(StrictModel):
    artifact_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    producer: VmRole | Literal["controller"]
    unit_id: Optional[str]
    deployment_id: str = Field(min_length=1)
    closed: bool
    size_bytes: Evidence[int]
    sha256: StringEvidence
    retrieval_state: Literal["not_retrieved", "verified", "failed"]
    failure: Optional[FailureEvidence] = None

    @model_validator(mode="after")
    def integrity_matches_state(self) -> "ArtifactRecord":
        _normalized_relative_path(self.path)
        if self.size_bytes.value is not None and (isinstance(self.size_bytes.value, bool) or self.size_bytes.value < 0):
            raise ValueError("size_bytes must be a nonnegative integer")
        if self.sha256.value is not None and not re.fullmatch(r"[a-f0-9]{64}", self.sha256.value):
            raise ValueError("sha256 must be lowercase SHA-256")
        if self.retrieval_state == "verified" and (not self.closed or self.size_bytes.value is None or self.sha256.value is None):
            raise ValueError("verified artifact requires a closed file and observed size and hash")
        if self.retrieval_state == "failed" and self.failure is None:
            raise ValueError("failed retrieval requires failure evidence")
        return self


class ArtifactLayout(StrictModel):
    config_snapshot: str = "config.yaml"
    resolved_snapshot: str = "config.resolved.json"
    schedule_snapshot: str = "schedule.csv"
    provider_outputs: str = "terraform-outputs.redacted.json"
    checksums: str = "checksums.sha256"
    vm_a_metadata: str = "metadata/vm_a.json"
    vm_b_metadata: str = "metadata/vm_b.json"
    vm_a_diagnostics: str = "diagnostics/vm_a.jsonl"
    vm_b_diagnostics: str = "diagnostics/vm_b.jsonl"
    sockperf_server_log: str = "server-logs/sockperf.log"
    iperf3_server_log: str = "server-logs/iperf3-server.jsonl"
    iperf3_server_error: str = "server-logs/iperf3-server.err"
    validation_result: str = "validation/result.json"
    validation_summary: str = "validation/summary.txt"
    day_iperf3_client: str = "measurements/day/iperf3-client.jsonl"
    day_probe_csv_template: str = "measurements/day/probes/{chunk_id}.csv"
    day_probe_log_template: str = "measurements/day/probes/{chunk_id}.log"

    @model_validator(mode="after")
    def safe_paths(self) -> "ArtifactLayout":
        for value in self.model_dump().values():
            _normalized_relative_path(value)
        return self

    def as_mapping(self) -> Dict[str, str]:
        return self.model_dump()

    def unit_paths(self, unit_id: str) -> Dict[str, str]:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,191}", unit_id):
            raise ValueError("unit_id is not a valid identifier")
        _normalized_relative_path(f"measurements/{unit_id}/marker")
        root = f"measurements/{unit_id}"
        return {
            "commands": f"{root}/commands.json",
            "timing": f"{root}/timing.json",
            "sockperf": f"{root}/sockperf.csv",
            "stdout": f"{root}/stdout.log",
            "stderr": f"{root}/stderr.log",
            "iperf3": f"{root}/iperf3.json",
        }

    def for_mode(self, mode: str, measurement_units: list[object], *, schedule_supplied: bool = False) -> list[ArtifactInput]:
        from .common import ExperimentMode

        mode = ExperimentMode(mode)
        declarations: list[ArtifactInput] = []
        global_paths = {
            "config_snapshot": (self.config_snapshot, "configuration", ArtifactExpectation.REQUIRED),
            "resolved_snapshot": (self.resolved_snapshot, "resolved_configuration", ArtifactExpectation.REQUIRED),
            "schedule_snapshot": (self.schedule_snapshot, "schedule_row", ArtifactExpectation.REQUIRED if schedule_supplied else ArtifactExpectation.NOT_EXPECTED),
            "provider_outputs": (self.provider_outputs, "provider_outputs", ArtifactExpectation.OPTIONAL),
            "checksums": (self.checksums, "checksums", ArtifactExpectation.OPTIONAL),
            "vm_a_metadata": (self.vm_a_metadata, "vm_metadata", ArtifactExpectation.OPTIONAL),
            "vm_b_metadata": (self.vm_b_metadata, "vm_metadata", ArtifactExpectation.OPTIONAL),
            "vm_a_diagnostics": (self.vm_a_diagnostics, "diagnostics", ArtifactExpectation.OPTIONAL),
            "vm_b_diagnostics": (self.vm_b_diagnostics, "diagnostics", ArtifactExpectation.OPTIONAL),
            "validation_result": (self.validation_result, "validation_result", ArtifactExpectation.OPTIONAL),
            "validation_summary": (self.validation_summary, "validation_summary", ArtifactExpectation.OPTIONAL),
        }
        for name, (path, kind, expectation) in global_paths.items():
            declarations.append(ArtifactInput(artifact_id=name, path=path, kind=kind,
                                              producer="controller", unit_id=None, expectation=expectation))
        for unit in measurement_units:
            unit_id = str(getattr(unit, "unit_id"))
            phase_value = getattr(unit, "phase", "")
            phase = str(getattr(phase_value, "value", phase_value))
            for artifact, path in self.unit_paths(unit_id).items():
                if artifact == "iperf3" and phase == "idle_latency":
                    continue
                declarations.append(ArtifactInput(
                    artifact_id=f"{unit_id}-{artifact}", path=path, kind=artifact,
                    producer=VmRole.VM_A if artifact in {"sockperf", "iperf3", "stdout", "stderr"} else "controller",
                    unit_id=unit_id, expectation=ArtifactExpectation.REQUIRED,
                ))
        if mode == ExperimentMode.DIAGNOSTIC_DAY:
            declarations.extend([
                ArtifactInput(artifact_id="day-iperf3-client", path=self.day_iperf3_client, kind="iperf3-client-jsonl", producer=VmRole.VM_A, unit_id=None, expectation=ArtifactExpectation.REQUIRED),
                ArtifactInput(artifact_id="day-probe-csv", path_template=self.day_probe_csv_template, kind="sockperf-csv", producer=VmRole.VM_A, unit_id=None, expectation=ArtifactExpectation.REQUIRED),
                ArtifactInput(artifact_id="day-probe-log", path_template=self.day_probe_log_template, kind="sockperf-log", producer=VmRole.VM_A, unit_id=None, expectation=ArtifactExpectation.REQUIRED),
            ])
        return declarations


ARTIFACTS = ArtifactLayout()
