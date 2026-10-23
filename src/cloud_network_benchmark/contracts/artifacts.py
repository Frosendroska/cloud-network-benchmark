from __future__ import annotations

from pathlib import PurePosixPath
from pathlib import Path
import hashlib
import re
from typing import Callable, Dict, Literal, Optional

from pydantic import Field, model_validator

from .common import ArtifactExpectation, ExperimentMode, FailureEvidence, IDENTIFIER_PATTERN, StrictModel, StringEvidence, Evidence, VmRole


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
    artifact_id: str = Field(pattern=IDENTIFIER_PATTERN)
    path: Optional[str] = None
    path_template: Optional[str] = None
    kind: str = Field(pattern=IDENTIFIER_PATTERN)
    producer: VmRole | Literal["controller"]
    unit_id: Optional[str] = Field(default=None, pattern=IDENTIFIER_PATTERN)
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
    artifact_id: str = Field(pattern=IDENTIFIER_PATTERN)
    path: str = Field(min_length=1)
    kind: str = Field(pattern=IDENTIFIER_PATTERN)
    producer: VmRole | Literal["controller"]
    unit_id: Optional[str] = Field(pattern=IDENTIFIER_PATTERN)
    deployment_id: str = Field(pattern=IDENTIFIER_PATTERN)
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

    def verify_materialized_file(
        self, child_root: Path, *, budget_check: Callable[[int, str], None] | None = None,
    ) -> None:
        """Verify a claimed closed artifact against bytes present under the child root."""
        if self.retrieval_state != "verified" or not self.closed:
            raise ValueError("only closed verified artifacts can be checked for integrity")
        path = resolve_artifact_path(child_root, self.path)
        size = 0
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while True:
                if budget_check is not None:
                    budget_check(size, digest.hexdigest())
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
                if budget_check is not None:
                    budget_check(size, digest.hexdigest())
        if self.size_bytes.value != size:
            raise ValueError("materialized artifact size does not match recorded size")
        if self.sha256.value != digest.hexdigest():
            raise ValueError("materialized artifact hash does not match recorded SHA-256")


class ArtifactLayout(StrictModel):
    mode: Optional[ExperimentMode] = None
    measurement_unit_ids: list[str] = Field(default_factory=list)
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
    day_iperf3_observation: str = "measurements/day/iperf3-acquisition.stderr"
    day_probe_csv_template: str = "measurements/day/probes/{chunk_id}.csv"
    day_probe_log_template: str = "measurements/day/probes/{chunk_id}.log"
    day_probe_observation_template: str = "measurements/day/probes/{chunk_id}.acquisition.stderr"

    @model_validator(mode="after")
    def safe_paths(self) -> "ArtifactLayout":
        for name, value in self.model_dump().items():
            if name in {"mode", "measurement_unit_ids"}:
                continue
            _normalized_relative_path(value)
        return self

    def as_mapping(self) -> Dict[str, str]:
        return {name: value for name, value in self.model_dump().items()
                if name not in {"mode", "measurement_unit_ids"}}

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
            "sockperf-observation": f"{root}/sockperf-acquisition.stderr",
            "iperf3-observation": f"{root}/iperf3-acquisition.stderr",
        }

    @classmethod
    def for_mode(cls, mode: str, measurement_units: list[object]) -> "ArtifactLayout":
        normalized_mode = ExperimentMode(mode)
        unit_ids = [str(getattr(unit, "unit_id")) for unit in measurement_units]
        if len(unit_ids) != len(set(unit_ids)):
            raise ValueError("mode-derived artifact layout requires unique measurement unit IDs")
        return cls(mode=normalized_mode, measurement_unit_ids=unit_ids)

    def declarations_for_mode(self, mode: str, measurement_units: list[object], *, schedule_supplied: bool = False) -> list[ArtifactInput]:
        from .common import ExperimentMode

        mode = ExperimentMode(mode)
        if self.mode is not None and ExperimentMode(self.mode) != mode:
            raise ValueError("artifact layout mode must match requested mode")
        unit_ids = [str(getattr(unit, "unit_id")) for unit in measurement_units]
        if self.measurement_unit_ids and self.measurement_unit_ids != unit_ids:
            day_chunks_extend_layout = (
                mode == ExperimentMode.DIAGNOSTIC_DAY
                and unit_ids[:len(self.measurement_unit_ids)] == self.measurement_unit_ids
                and all(getattr(unit, "kind", None) == "probe_chunk"
                        and getattr(unit, "parent_unit_id", None) in self.measurement_unit_ids
                        for unit in measurement_units[len(self.measurement_unit_ids):])
            )
            if not day_chunks_extend_layout:
                raise ValueError("artifact layout unit IDs must match requested measurement units")
        declarations: list[ArtifactInput] = []
        global_paths = [
            ("config_snapshot", self.config_snapshot, "configuration", "controller", ArtifactExpectation.REQUIRED),
            ("resolved_snapshot", self.resolved_snapshot, "resolved_configuration", "controller", ArtifactExpectation.REQUIRED),
            ("schedule_snapshot", self.schedule_snapshot, "schedule_row", "controller", ArtifactExpectation.REQUIRED if schedule_supplied else ArtifactExpectation.NOT_EXPECTED),
            ("provider_outputs", self.provider_outputs, "provider_outputs", "controller", ArtifactExpectation.OPTIONAL),
            ("checksums", self.checksums, "checksums", "controller", ArtifactExpectation.OPTIONAL),
            ("vm_a_metadata", self.vm_a_metadata, "vm_metadata", VmRole.VM_A, ArtifactExpectation.REQUIRED),
            ("vm_b_metadata", self.vm_b_metadata, "vm_metadata", VmRole.VM_B, ArtifactExpectation.REQUIRED),
            ("vm_a_diagnostics", self.vm_a_diagnostics, "diagnostics", VmRole.VM_A, ArtifactExpectation.REQUIRED),
            ("vm_b_diagnostics", self.vm_b_diagnostics, "diagnostics", VmRole.VM_B, ArtifactExpectation.REQUIRED),
            ("sockperf_server_log", self.sockperf_server_log, "sockperf-server-log", VmRole.VM_B, ArtifactExpectation.REQUIRED),
            ("iperf3_server_log", self.iperf3_server_log, "iperf3-server-jsonl", VmRole.VM_B, ArtifactExpectation.REQUIRED),
            ("iperf3_server_error", self.iperf3_server_error, "iperf3-observation-stderr", VmRole.VM_B, ArtifactExpectation.REQUIRED),
            ("validation_result", self.validation_result, "validation_result", "controller", ArtifactExpectation.OPTIONAL),
            ("validation_summary", self.validation_summary, "validation_summary", "controller", ArtifactExpectation.OPTIONAL),
        ]
        for name, path, kind, producer, expectation in global_paths:
            declarations.append(ArtifactInput(artifact_id=name, path=path, kind=kind,
                                              producer=producer, unit_id=None, expectation=expectation))
        for unit in measurement_units:
            unit_id = str(getattr(unit, "unit_id"))
            phase_value = getattr(unit, "phase", "")
            phase = str(getattr(phase_value, "value", phase_value))
            paths = self.unit_paths(unit_id)
            unit_files = {"commands": (paths["commands"], "command_evidence", "controller"),
                          "timing": (paths["timing"], "measurement_timing", "controller")}
            if mode != ExperimentMode.DIAGNOSTIC_DAY:
                unit_files.update({
                    "sockperf": (paths["sockperf"], "sockperf-csv", VmRole.VM_A),
                    "sockperf-observation": (paths["sockperf-observation"], "sockperf-observation-stderr", VmRole.VM_A),
                    "stdout": (paths["stdout"], "stdout", "controller"),
                    "stderr": (paths["stderr"], "stderr", "controller"),
                })
                if phase != "idle_latency":
                    unit_files["iperf3"] = (paths["iperf3"], "iperf3-json", VmRole.VM_A)
                    unit_files["iperf3-observation"] = (paths["iperf3-observation"], "iperf3-observation-stderr", VmRole.VM_A)
            for artifact, (path, kind, producer) in unit_files.items():
                declarations.append(ArtifactInput(artifact_id=f"{unit_id}-{artifact}", path=path, kind=kind,
                                                  producer=producer, unit_id=unit_id,
                                                  expectation=ArtifactExpectation.REQUIRED))
        if mode == ExperimentMode.DIAGNOSTIC_DAY:
            declarations.extend([
                ArtifactInput(artifact_id="day-iperf3-client", path=self.day_iperf3_client, kind="iperf3-client-jsonl", producer=VmRole.VM_A, unit_id=None, expectation=ArtifactExpectation.REQUIRED),
                ArtifactInput(artifact_id="day-iperf3-observation", path=self.day_iperf3_observation, kind="iperf3-observation-stderr", producer=VmRole.VM_A, unit_id=None, expectation=ArtifactExpectation.REQUIRED),
                ArtifactInput(artifact_id="day-probe-csv", path_template=self.day_probe_csv_template, kind="sockperf-csv", producer=VmRole.VM_A, unit_id=None, expectation=ArtifactExpectation.REQUIRED),
                ArtifactInput(artifact_id="day-probe-log", path_template=self.day_probe_log_template, kind="sockperf-log", producer=VmRole.VM_A, unit_id=None, expectation=ArtifactExpectation.REQUIRED),
                ArtifactInput(artifact_id="day-probe-observation", path_template=self.day_probe_observation_template, kind="sockperf-observation-stderr", producer=VmRole.VM_A, unit_id=None, expectation=ArtifactExpectation.REQUIRED),
            ])
        return declarations


ARTIFACTS = ArtifactLayout()
