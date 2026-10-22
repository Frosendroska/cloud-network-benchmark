from __future__ import annotations

from datetime import datetime
from math import isfinite
from typing import Annotated, Literal, Optional

from pydantic import ConfigDict, Field, field_validator, model_validator

from .common import Evidence, IDENTIFIER_PATTERN, Provider, StrictModel, StringEvidence, StructuredEvidence, VmRole, utc_datetime
from .execution import CommandResult


class CommandEvidence(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True, json_schema_extra={
        "allOf": [
            {"if": {"not": {"properties": {"duration_basis": {"const": "monotonic"}}, "required": ["duration_basis"]}},
             "then": {"properties": {
                 "utc_elapsed_discrepancy_seconds": {"type": "null"},
                 "result": {"not": {"properties": {"duration_basis": {"const": "monotonic"}},
                                     "required": ["duration_basis"]}},
             }}},
            {"if": {"properties": {"duration_basis": {"const": "monotonic"}}, "required": ["duration_basis"]},
             "then": {
                 "required": ["started_at", "finished_at", "utc_elapsed_discrepancy_seconds"],
                 "properties": {
                     "monotonic_started": {"required": ["value"], "properties": {"value": {"type": "number", "minimum": 0}}},
                     "monotonic_finished": {"required": ["value"], "properties": {"value": {"type": "number", "minimum": 0}}},
                     "started_at": {"required": ["value"], "properties": {"value": {"type": "string", "format": "date-time"}}},
                     "finished_at": {"required": ["value"], "properties": {"value": {"type": "string", "format": "date-time"}}},
                     "utc_elapsed_discrepancy_seconds": {"type": "number"},
                     "result": {
                         "required": ["action_id", "outcome", "started_at", "finished_at", "duration_seconds",
                                      "exit_code", "stdout", "stderr", "duration_basis", "monotonic_started",
                                      "monotonic_finished", "utc_elapsed_discrepancy_seconds"],
                         "properties": {"action_id": {"type": "string", "minLength": 1},
                                        "outcome": {"enum": ["succeeded", "failed", "timed_out", "interrupted"]},
                                        "started_at": {"type": "string", "format": "date-time"},
                                        "finished_at": {"type": "string", "format": "date-time"},
                                        "duration_seconds": {"type": "number", "minimum": 0},
                                        "exit_code": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                                        "stdout": {"type": "string"},
                                        "stderr": {"type": "string"},
                                        "duration_basis": {"const": "monotonic"},
                                               "monotonic_started": {"type": "number", "minimum": 0},
                                               "monotonic_finished": {"type": "number", "minimum": 0},
                                               "utc_elapsed_discrepancy_seconds": {"type": "number"}},
                     },
                 },
             }},
        ]
    })
    schema_version: Literal[2]
    request: dict[str, object]
    result: dict[str, object]
    producer: VmRole | Literal["controller"]
    deployment_id: str = Field(pattern=IDENTIFIER_PATTERN)
    unit_id: Optional[str] = Field(default=None, pattern=IDENTIFIER_PATTERN)
    binary_version: StringEvidence
    source_identity: StringEvidence
    monotonic_started: Evidence[float]
    monotonic_finished: Evidence[float]
    started_at: Evidence[datetime] = Field(default_factory=lambda: Evidence[datetime](unavailable_reason="not captured"))
    finished_at: Evidence[datetime] = Field(default_factory=lambda: Evidence[datetime](unavailable_reason="not captured"))
    duration_basis: Literal["utc", "monotonic"] = "utc"
    utc_elapsed_discrepancy_seconds: Optional[float] = Field(default=None, strict=True)

    @model_validator(mode="before")
    @classmethod
    def reject_boolean_discrepancy(cls, value: object) -> object:
        if isinstance(value, dict) and isinstance(value.get("utc_elapsed_discrepancy_seconds"), bool):
            raise ValueError("utc_elapsed_discrepancy_seconds must be numeric, not boolean")
        return value

    @model_validator(mode="after")
    def durable_only_views(self) -> "CommandEvidence":
        if "environment" in self.request or "stdin" in self.request or "redactions" in self.request:
            raise ValueError("command request must use the redacted durable view")
        if "redactions" in self.result:
            raise ValueError("command result must use the redacted durable view")
        return self

    @model_validator(mode="after")
    def ordered_monotonic(self) -> "CommandEvidence":
        start, finish = self.monotonic_started.value, self.monotonic_finished.value
        if start is not None and (not isfinite(start) or start < 0):
            raise ValueError("monotonic start must be finite and nonnegative")
        if finish is not None and (not isfinite(finish) or finish < 0):
            raise ValueError("monotonic finish must be finite and nonnegative")
        if start is not None and finish is not None and finish < start:
            raise ValueError("monotonic finish must not precede start")
        utc_start, utc_finish = self.started_at.value, self.finished_at.value
        if utc_start is not None and (utc_start.tzinfo is None or utc_start.utcoffset() is None):
            raise ValueError("command UTC start must be timezone-aware")
        if utc_finish is not None and (utc_finish.tzinfo is None or utc_finish.utcoffset() is None):
            raise ValueError("command UTC finish must be timezone-aware")
        if utc_start is not None:
            object.__setattr__(self, "started_at", Evidence[datetime](value=utc_datetime(utc_start)))
            utc_start = self.started_at.value
        if utc_finish is not None:
            object.__setattr__(self, "finished_at", Evidence[datetime](value=utc_datetime(utc_finish)))
            utc_finish = self.finished_at.value
        if self.duration_basis == "utc":
            if self.utc_elapsed_discrepancy_seconds is not None:
                raise ValueError("UTC duration basis cannot include clock discrepancy")
            if self.result.get("duration_basis") == "monotonic":
                raise ValueError("monotonic command result cannot be wrapped as UTC evidence")
            if utc_start is not None and utc_finish is not None and utc_finish < utc_start:
                raise ValueError("command UTC finish must not precede start")
            return self

        if self.utc_elapsed_discrepancy_seconds is None or not isfinite(self.utc_elapsed_discrepancy_seconds):
            raise ValueError("monotonic duration basis requires a finite clock discrepancy")
        if start is None or finish is None or utc_start is None or utc_finish is None:
            raise ValueError("monotonic duration basis requires complete observed clock bounds")
        result_basis = self.result.get("duration_basis", "utc")
        if result_basis != "monotonic":
            raise ValueError("monotonic evidence must match a monotonic command result")
        try:
            projected_values = {
                name: self.result[name]
                for name in CommandResult.model_fields
                if name in self.result
            }
            projected = CommandResult.model_validate(projected_values)
        except Exception as exc:
            raise ValueError("monotonic command result must be a complete valid durable projection") from exc
        if projected.duration_basis != self.duration_basis:
            raise ValueError("command result timing basis must match native evidence")
        if projected.started_at != utc_datetime(utc_start) or projected.finished_at != utc_datetime(utc_finish):
            raise ValueError("command result UTC bounds must match native evidence")
        if abs(projected.monotonic_started - start) > 0.001 or abs(projected.monotonic_finished - finish) > 0.001:
            raise ValueError("command result monotonic bounds must match native evidence")
        if abs(projected.utc_elapsed_discrepancy_seconds - self.utc_elapsed_discrepancy_seconds) > 0.001:
            raise ValueError("command result discrepancy must match native evidence")
        object.__setattr__(self, "started_at", Evidence[datetime](value=utc_datetime(utc_start)))
        object.__setattr__(self, "finished_at", Evidence[datetime](value=utc_datetime(utc_finish)))
        return self


class ClockMapping(StrictModel):
    vm_role: VmRole
    monotonic_seconds: float = Field(ge=0)
    utc_timestamp: datetime
    estimated_error_ms: Evidence[float]
    synchronization_source: StringEvidence

    @field_validator("monotonic_seconds")
    @classmethod
    def finite_monotonic(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("monotonic_seconds must be finite")
        return value

    @field_validator("utc_timestamp")
    @classmethod
    def aware_utc(cls, value: datetime) -> datetime:
        return utc_datetime(value)

    @field_validator("estimated_error_ms")
    @classmethod
    def valid_error(cls, value: Evidence[float]) -> Evidence[float]:
        if value.value is not None and (value.value < 0 or not isfinite(value.value)):
            raise ValueError("estimated_error_ms must be finite and nonnegative")
        return value


class TimingGap(StrictModel):
    started_at: datetime
    finished_at: datetime
    reason: str = Field(min_length=1, pattern=r"\S")

    @model_validator(mode="after")
    def valid_gap(self) -> "TimingGap":
        if self.started_at.tzinfo is None or self.finished_at.tzinfo is None:
            raise ValueError("gap timestamps must include a timezone")
        if self.finished_at < self.started_at:
            raise ValueError("gap finish must not precede start")
        object.__setattr__(self, "started_at", utc_datetime(self.started_at))
        object.__setattr__(self, "finished_at", utc_datetime(self.finished_at))
        return self


class MeasurementTiming(StrictModel):
    unit_id: str = Field(pattern=IDENTIFIER_PATTERN)
    deployment_id: str = Field(pattern=IDENTIFIER_PATTERN)
    probe_test_started_at: Evidence[datetime]
    tcp_started_at: Evidence[datetime]
    measured_started_at: Evidence[datetime]
    measured_finished_at: Evidence[datetime]
    monotonic_started: Evidence[float]
    monotonic_finished: Evidence[float]
    omitted_seconds: float = Field(ge=0)
    overlap_seconds: Evidence[float]
    gaps: list[TimingGap] = Field(default_factory=list)
    clock_mappings: list[ClockMapping] = Field(default_factory=list)

    @field_validator("omitted_seconds")
    @classmethod
    def finite_omitted(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("omitted_seconds must be finite")
        return value

    @model_validator(mode="after")
    def ordered_interval(self) -> "MeasurementTiming":
        start, end = self.measured_started_at.value, self.measured_finished_at.value
        if start is not None and end is not None and end < start:
            raise ValueError("measured finish must not precede start")
        if start is not None and (start.tzinfo is None or start.utcoffset() is None):
            raise ValueError("measured start must be timezone-aware")
        if end is not None and (end.tzinfo is None or end.utcoffset() is None):
            raise ValueError("measured finish must be timezone-aware")
        if self.overlap_seconds.value is not None and (self.overlap_seconds.value < 0 or not isfinite(self.overlap_seconds.value)):
            raise ValueError("overlap_seconds must be finite and nonnegative")
        for name in ("monotonic_started", "monotonic_finished"):
            value = getattr(self, name).value
            if value is not None and (not isfinite(value) or value < 0):
                raise ValueError(f"{name} must be finite and nonnegative")
        monotonic_start, monotonic_end = self.monotonic_started.value, self.monotonic_finished.value
        if monotonic_start is not None and monotonic_end is not None and monotonic_end < monotonic_start:
            raise ValueError("monotonic finish must not precede start")
        chronology = [self.probe_test_started_at.value, self.tcp_started_at.value, start, end]
        available = [item for item in chronology if item is not None]
        if any(item.tzinfo is None or item.utcoffset() is None for item in available):
            raise ValueError("observed measurement timestamps must include a timezone")
        if any(current < previous for previous, current in zip(available, available[1:])):
            raise ValueError("observed measurement chronology is invalid")
        for name in ("probe_test_started_at", "tcp_started_at", "measured_started_at", "measured_finished_at"):
            evidence = getattr(self, name)
            if evidence.value is not None:
                object.__setattr__(evidence, "value", utc_datetime(evidence.value))
        return self


class DiagnosticSample(StrictModel):
    schema_version: Literal[2]
    deployment_id: str = Field(pattern=IDENTIFIER_PATTERN)
    vm_role: VmRole
    sampled_at: datetime
    monotonic_seconds: float = Field(ge=0)
    interval_seconds: float = Field(gt=0)
    cpu_total_percent: Evidence[Annotated[float, Field(ge=0, le=100)]]
    cpu_max_core_percent: Evidence[Annotated[float, Field(ge=0, le=100)]]
    cpu_steal_percent: Evidence[Annotated[float, Field(ge=0, le=100)]]
    tcp_retransmissions: Evidence[Annotated[int, Field(ge=0)]]
    interface_rx_drops: Evidence[Annotated[int, Field(ge=0)]]
    interface_tx_drops: Evidence[Annotated[int, Field(ge=0)]]
    interface_rx_errors: Evidence[Annotated[int, Field(ge=0)]]
    interface_tx_errors: Evidence[Annotated[int, Field(ge=0)]]
    rx_bytes: Evidence[Annotated[int, Field(ge=0)]]
    tx_bytes: Evidence[Annotated[int, Field(ge=0)]]
    reset_fields: list[str] = Field(default_factory=list)
    gap_reason: Optional[str] = None

    @field_validator("sampled_at")
    @classmethod
    def sample_time_aware(cls, value: datetime) -> datetime:
        return utc_datetime(value)

    @field_validator("monotonic_seconds", "interval_seconds")
    @classmethod
    def finite_timing(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("timing values must be finite")
        return value

    @field_validator("cpu_total_percent", "cpu_max_core_percent", "cpu_steal_percent")
    @classmethod
    def cpu_bounds(cls, value: Evidence[float]) -> Evidence[float]:
        if value.value is not None and (not isfinite(value.value) or not 0 <= value.value <= 100):
            raise ValueError("CPU percentages must be finite and between 0 and 100")
        return value

    @field_validator("tcp_retransmissions", "interface_rx_drops", "interface_tx_drops", "interface_rx_errors", "interface_tx_errors", "rx_bytes", "tx_bytes")
    @classmethod
    def counters_nonnegative(cls, value: Evidence[int]) -> Evidence[int]:
        if value.value is not None and (isinstance(value.value, bool) or value.value < 0):
            raise ValueError("counter deltas must be nonnegative integers")
        return value

    @model_validator(mode="after")
    def reset_is_unavailable(self) -> "DiagnosticSample":
        for name in self.reset_fields:
            if name not in type(self).model_fields:
                raise ValueError(f"unknown reset field {name!r}")
            item = getattr(self, name)
            if not isinstance(item, Evidence) or item.value is not None:
                raise ValueError(f"reset field {name!r} must have unavailable evidence")
        return self


class VmMetadata(StrictModel):
    schema_version: Literal[2]
    deployment_id: str = Field(pattern=IDENTIFIER_PATTERN)
    vm_role: VmRole
    provider: Provider
    cpu_model: StringEvidence
    nic_driver: StringEvidence
    acceleration: StringEvidence
    image: StringEvidence
    kernel: StringEvidence
    congestion_control: StringEvidence
    tool_versions: StructuredEvidence
    source_builds: StructuredEvidence
    documented_network_limit: StructuredEvidence
    mtu: Evidence[int]
    observed_region: StringEvidence
    observed_zone: StringEvidence
    physical_zone_id: StringEvidence
    actual_shape: StringEvidence

    @field_validator("mtu")
    @classmethod
    def positive_mtu(cls, value: Evidence[int]) -> Evidence[int]:
        if value.value is not None and (isinstance(value.value, bool) or value.value <= 0):
            raise ValueError("MTU must be a positive integer")
        return value
