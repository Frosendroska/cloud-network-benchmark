from __future__ import annotations

from datetime import datetime
from math import isfinite
from typing import Literal, Optional

from pydantic import Field, field_validator, model_validator

from .common import Evidence, Provider, StrictModel, StringEvidence, VmRole, utc_datetime


class CommandEvidence(StrictModel):
    schema_version: Literal[2] = 2
    request: dict[str, object]
    result: dict[str, object]
    producer: VmRole | Literal["controller"]
    deployment_id: str = Field(min_length=1)
    unit_id: Optional[str] = None
    binary_version: StringEvidence
    source_identity: StringEvidence
    monotonic_started: Evidence[float]
    monotonic_finished: Evidence[float]

    @model_validator(mode="after")
    def ordered_monotonic(self) -> "CommandEvidence":
        start, finish = self.monotonic_started.value, self.monotonic_finished.value
        if start is not None and (not isfinite(start) or start < 0):
            raise ValueError("monotonic start must be finite and nonnegative")
        if finish is not None and (not isfinite(finish) or finish < 0):
            raise ValueError("monotonic finish must be finite and nonnegative")
        if start is not None and finish is not None and finish < start:
            raise ValueError("monotonic finish must not precede start")
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
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid_gap(self) -> "TimingGap":
        if self.started_at.tzinfo is None or self.finished_at.tzinfo is None:
            raise ValueError("gap timestamps must include a timezone")
        if self.finished_at < self.started_at:
            raise ValueError("gap finish must not precede start")
        return self


class MeasurementTiming(StrictModel):
    unit_id: str = Field(min_length=1)
    deployment_id: str = Field(min_length=1)
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
        return self


class DiagnosticSample(StrictModel):
    schema_version: Literal[2] = 2
    deployment_id: str = Field(min_length=1)
    vm_role: VmRole
    sampled_at: datetime
    monotonic_seconds: float = Field(ge=0)
    interval_seconds: float = Field(gt=0)
    cpu_total_percent: Evidence[float]
    cpu_max_core_percent: Evidence[float]
    cpu_steal_percent: Evidence[float]
    tcp_retransmissions: Evidence[int]
    interface_rx_drops: Evidence[int]
    interface_tx_drops: Evidence[int]
    interface_rx_errors: Evidence[int]
    interface_tx_errors: Evidence[int]
    rx_bytes: Evidence[int]
    tx_bytes: Evidence[int]
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
    schema_version: Literal[2] = 2
    deployment_id: str = Field(min_length=1)
    vm_role: VmRole
    provider: Provider
    cpu_model: StringEvidence
    nic_driver: StringEvidence
    acceleration: StringEvidence
    image: StringEvidence
    kernel: StringEvidence
    congestion_control: StringEvidence
    tool_versions: StringEvidence
    source_builds: StringEvidence
    documented_network_limit: StringEvidence
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
