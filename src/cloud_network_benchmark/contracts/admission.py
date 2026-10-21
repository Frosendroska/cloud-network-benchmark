from __future__ import annotations

from datetime import date, datetime
import hashlib
from math import isfinite
from typing import Literal, Optional

from pydantic import Field, field_validator, model_validator

from .common import (
    CONFIG_ID_PATTERN,
    IDENTIFIER_PATTERN,
    SHA256_PATTERN,
    Evidence,
    ExperimentMode,
    FailureEvidence,
    Provider,
    Scenario,
    StrictModel,
    StringEvidence,
    StructuredEvidence,
)


class ScheduleEntry(StrictModel):
    schema_version: Literal[2] = 2
    entry_id: str = Field(pattern=IDENTIFIER_PATTERN)
    config_id: str = Field(pattern=CONFIG_ID_PATTERN)
    experiment_id: str = Field(pattern=r"^EXP-[0-9]{3,}$")
    scheduled_start: Optional[datetime]
    window_label: Optional[Literal["night", "morning", "afternoon", "evening"]] = None
    source_path: str = Field(min_length=1)
    source_sha256: str = Field(pattern=SHA256_PATTERN)
    row_sha256: str = Field(pattern=SHA256_PATTERN)
    original_header: str
    original_row: str
    columns: list[str] = Field(min_length=1)
    priority: Literal["P0", "P1", "P2", "P3"]
    replaces_run_id: Optional[str] = Field(default=None, pattern=IDENTIFIER_PATTERN)

    @field_validator("scheduled_start")
    @classmethod
    def aware_schedule(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("scheduled_start must include a timezone")
        return value

    @model_validator(mode="after")
    def row_evidence_is_lossless(self) -> "ScheduleEntry":
        if len(self.columns) != len(set(self.columns)) or any(not name for name in self.columns):
            raise ValueError("columns must be unique nonblank header names")
        if not self.original_header and not self.original_row:
            raise ValueError("original row and header evidence cannot both be empty")
        if hashlib.sha256(self.original_row.encode("utf-8")).hexdigest() != self.row_sha256:
            raise ValueError("row_sha256 must hash the exact UTF-8 original_row bytes")
        return self


class AdmissionRequirements(StrictModel):
    priority: Literal["P0", "P1", "P2", "P3"]
    lifetime_cap_seconds: Literal[1200, 1800, 87600]
    hard_budget_usd: Literal[300] = 300
    protected_buffer_usd: Literal[50] = 50
    cost_input_id: str = Field(min_length=1)
    prices_checked_on: date
    cost_input_sha256: StringEvidence
    estimated_cost_usd: Evidence[float]
    requires_common_n: bool
    requires_schedule: bool
    requires_primary_review: bool
    requires_expiry: bool

    @staticmethod
    def expected_for(mode: ExperimentMode, scenario: Scenario) -> dict[str, object]:
        mode, scenario = ExperimentMode(mode), Scenario(scenario)
        priority = {
            Scenario.SAME_ZONE: "P0",
            Scenario.CROSS_ZONE: "P2",
            Scenario.PLACEMENT_OPTIMIZATION: "P1",
            Scenario.INTER_REGION: "P3",
        }[scenario]
        cap = {
            ExperimentMode.ACCEPTANCE: 1200,
            ExperimentMode.SHORT_STUDY: 1200,
            ExperimentMode.CALIBRATION: 1800,
            ExperimentMode.DIAGNOSTIC_DAY: 87600,
        }[mode]
        scheduled = mode in {ExperimentMode.SHORT_STUDY, ExperimentMode.CALIBRATION, ExperimentMode.DIAGNOSTIC_DAY}
        return {
            "priority": priority,
            "lifetime_cap_seconds": cap,
            "requires_common_n": mode in {ExperimentMode.SHORT_STUDY, ExperimentMode.DIAGNOSTIC_DAY},
            "requires_schedule": scheduled,
            "requires_primary_review": scheduled and priority in {"P2", "P3"},
            "requires_expiry": True,
        }

    @field_validator("estimated_cost_usd")
    @classmethod
    def finite_cost(cls, value: Evidence[float]) -> Evidence[float]:
        if value.value is not None and (value.value < 0 or not isfinite(value.value)):
            raise ValueError("estimated_cost_usd must be finite and nonnegative")
        return value


class ReservationRequest(StrictModel):
    schema_version: Literal[2] = 2
    campaign_id: str = Field(pattern=IDENTIFIER_PATTERN)
    config_id: str = Field(pattern=CONFIG_ID_PATTERN)
    schedule_entry_id: Evidence[str]
    selected_providers: list[Provider] = Field(min_length=1)
    mode: ExperimentMode
    priority: Literal["P0", "P1", "P2", "P3"]
    lifetime_cap_seconds: Literal[1200, 1800, 87600]
    estimated_cost_usd: Evidence[float]
    source_path: str = Field(min_length=1)
    source_sha256: str = Field(pattern=SHA256_PATTERN)
    incurred_cost_usd: Evidence[float]
    active_reserved_usd: Evidence[float]
    outstanding_cleanup: bool


class ReservationEvidence(StrictModel):
    schema_version: Literal[2] = 2
    campaign_id: str = Field(pattern=IDENTIFIER_PATTERN)
    reservation_id: Evidence[str]
    state: Literal["not_evaluated", "reserved", "rejected", "released"]
    reserved_cost_usd: Evidence[float]
    cap_started_at: Evidence[datetime]
    expires_at: Evidence[datetime]
    cap_start_policy_id: StringEvidence
    source_path: str = Field(min_length=1)
    source_sha256: str = Field(pattern=SHA256_PATTERN)
    reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def reserved_is_complete(self) -> "ReservationEvidence":
        if self.state == "reserved":
            if any(item.value is None for item in (self.reservation_id, self.reserved_cost_usd, self.cap_started_at, self.expires_at, self.cap_start_policy_id)):
                raise ValueError("reserved evidence requires observed reservation, cost, cap times and policy")
            if self.expires_at.value <= self.cap_started_at.value:
                raise ValueError("reservation expiry must follow cap start")
        elif not self.reasons:
            raise ValueError("non-reserved reservation state requires reasons")
        return self


class ExpiryRequest(StrictModel):
    schema_version: Literal[2] = 2
    run_id: str = Field(pattern=IDENTIFIER_PATTERN)
    deployment_id: str = Field(pattern=IDENTIFIER_PATTERN)
    provider: Provider
    action: Literal["stop", "deallocate"]
    expires_at: Evidence[datetime]
    reservation_id: Evidence[str]
    resource_namespace: str = Field(min_length=1)
    target_selector: str = Field(min_length=1)

    @model_validator(mode="after")
    def provider_action(self) -> "ExpiryRequest":
        expected = "deallocate" if Provider(self.provider) == Provider.AZURE else "stop"
        if self.action != expected:
            raise ValueError(f"{self.provider} expiry action must be {expected}")
        return self


class ExpiryEvidence(StrictModel):
    schema_version: Literal[2] = 2
    run_id: str = Field(pattern=IDENTIFIER_PATTERN)
    deployment_id: str = Field(pattern=IDENTIFIER_PATTERN)
    provider: Provider
    action: Literal["stop", "deallocate"]
    state: Literal["not_registered", "registered", "failed", "cancelled", "triggered"]
    registration_id: Evidence[str]
    registered_at: Evidence[datetime]
    expires_at: Evidence[datetime]
    target_selector: str = Field(min_length=1)
    provider_metadata: StructuredEvidence
    failure: Optional[FailureEvidence] = None

    @model_validator(mode="after")
    def registration_is_complete(self) -> "ExpiryEvidence":
        expected = "deallocate" if Provider(self.provider) == Provider.AZURE else "stop"
        if self.action != expected:
            raise ValueError(f"{self.provider} expiry action must be {expected}")
        if self.state == "registered" and any(item.value is None for item in (self.registration_id, self.registered_at, self.expires_at)):
            raise ValueError("registered expiry requires observed ID and timestamps")
        if self.state == "failed" and self.failure is None:
            raise ValueError("failed expiry requires failure evidence")
        return self
