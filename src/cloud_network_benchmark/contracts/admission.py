from __future__ import annotations

from datetime import date, datetime
import hashlib
import csv
import io
from math import isfinite
import re
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
    utc_datetime,
)
from ..validation_profiles import PROFILE_SPECS


class ScheduleEntry(StrictModel):
    schema_version: Literal[2]
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
        return utc_datetime(value) if value is not None else None

    @model_validator(mode="after")
    def row_evidence_is_lossless(self) -> "ScheduleEntry":
        supplied_text = [self.original_header, self.original_row, self.source_path, *self.columns]
        header_names = list(self.columns)
        try:
            header_names.extend(next(csv.reader(io.StringIO(self.original_header))))
        except (csv.Error, StopIteration):
            pass
        if any(_contains_secret_text(value) for value in supplied_text) or any(_is_secret_field_name(value) for value in header_names):
            raise ValueError("schedule evidence contains a secret-bearing field; remove it before supplying the row")
        if len(self.columns) != len(set(self.columns)) or any(not name for name in self.columns):
            raise ValueError("columns must be unique nonblank header names")
        if not self.original_header and not self.original_row:
            raise ValueError("original row and header evidence cannot both be empty")
        if not self.source_path.strip() or any(not value.strip() for value in (self.original_header, self.original_row)):
            raise ValueError("schedule source/header/row values must be nonblank")
        if hashlib.sha256(self.original_row.encode("utf-8")).hexdigest() != self.row_sha256:
            raise ValueError("row_sha256 must hash the exact UTF-8 original_row bytes")
        return self


_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?:password|passwd|token|secret|credential|private[_-]?key|access[_-]?key|secret[_-]?key)\s*[:=]"
)
_SECRET_FIELD_NAME = re.compile(
    r"(?i)(?:^|[^a-z0-9])(?:password|passwd|token|secret|credential|credentials|private[_-]?key|access[_-]?key|secret[_-]?key)(?:$|[^a-z0-9])"
)


def _contains_secret_text(value: str) -> bool:
    return ("-----BEGIN " in value and "PRIVATE KEY-----" in value) or bool(_SECRET_ASSIGNMENT.search(value))


def _is_secret_field_name(value: str) -> bool:
    return bool(_SECRET_FIELD_NAME.search(value.strip()))


class AdmissionRequirements(StrictModel):
    priority: Literal["P0", "P1", "P2", "P3"]
    lifetime_cap_seconds: Literal[1200, 1800, 3900, 4200, 5100, 87600]
    workflow_profile: Optional[Literal["all_cloud_acceptance_v1", "all_cloud_calibration_v1", "all_cloud_failure_acceptance_v1"]] = Field(default=None, exclude_if=lambda value: value is None)
    timing_profile: Optional[Literal["azure_exp901_sweden_acceptance_v1"]] = Field(
        default=None, exclude_if=lambda value: value is None)
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
    def expected_for(mode: ExperimentMode, scenario: Scenario, timing_profile: str | None = None,
                     workflow_profile: str | None = None) -> dict[str, object]:
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
        if timing_profile == "azure_exp901_sweden_acceptance_v1":
            if mode != ExperimentMode.ACCEPTANCE or scenario != Scenario.SAME_ZONE:
                raise ValueError("Azure EXP-901 timing profile is only valid for same-zone acceptance")
            cap = 1800
        elif timing_profile is not None:
            raise ValueError("unsupported timing profile")
        if workflow_profile is not None:
            spec = PROFILE_SPECS.get(workflow_profile)
            if spec is None or spec["mode"] != mode.value or timing_profile is not None:
                raise ValueError("workflow profile is unsupported for this mode or timing profile")
            cap = spec["cap_seconds"]
        scheduled = mode in {ExperimentMode.SHORT_STUDY, ExperimentMode.CALIBRATION, ExperimentMode.DIAGNOSTIC_DAY}
        return {
            "priority": priority,
            "lifetime_cap_seconds": cap,
            "workflow_profile": workflow_profile,
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

    @model_validator(mode="after")
    def source_identity_nonblank(self) -> "AdmissionRequirements":
        if not self.cost_input_id.strip():
            raise ValueError("cost_input_id must be nonblank")
        if self.cost_input_sha256.value is not None and not re.fullmatch(SHA256_PATTERN, self.cost_input_sha256.value):
            raise ValueError("cost_input_sha256 must be lowercase SHA-256")
        return self


class ReservationRequest(StrictModel):
    schema_version: Literal[2]
    campaign_id: str = Field(pattern=IDENTIFIER_PATTERN)
    config_id: str = Field(pattern=CONFIG_ID_PATTERN)
    schedule_entry_id: Evidence[str]
    selected_providers: list[Provider] = Field(min_length=1)
    mode: ExperimentMode
    priority: Literal["P0", "P1", "P2", "P3"]
    lifetime_cap_seconds: Literal[1200, 1800, 3900, 4200, 5100, 87600]
    timing_profile: Optional[Literal["azure_exp901_sweden_acceptance_v1"]] = Field(default=None, exclude_if=lambda value: value is None)
    workflow_profile: Optional[Literal["all_cloud_acceptance_v1", "all_cloud_calibration_v1", "all_cloud_failure_acceptance_v1"]] = Field(default=None, exclude_if=lambda value: value is None)
    experiment_id: Optional[str] = Field(default=None, exclude_if=lambda value: value is None)
    context: Optional[Literal["standard", "failure_check", "replacement"]] = Field(default=None, exclude_if=lambda value: value is None)
    scenario: Optional[Scenario] = Field(default=None, exclude_if=lambda value: value is None)
    config_sha256: Optional[str] = Field(default=None, pattern=SHA256_PATTERN, exclude_if=lambda value: value is None)
    resolved_sha256: Optional[str] = Field(default=None, pattern=SHA256_PATTERN, exclude_if=lambda value: value is None)
    estimated_cost_usd: Evidence[float]
    source_path: str = Field(min_length=1)
    source_sha256: str = Field(pattern=SHA256_PATTERN)
    incurred_cost_usd: Evidence[float]
    active_reserved_usd: Evidence[float]
    outstanding_cleanup: bool

    @model_validator(mode="after")
    def unique_providers_and_costs(self) -> "ReservationRequest":
        if len(set(self.selected_providers)) != len(self.selected_providers):
            raise ValueError("selected_providers must be unique")
        for evidence in (self.estimated_cost_usd, self.incurred_cost_usd, self.active_reserved_usd):
            if evidence.value is not None and (not isfinite(evidence.value) or evidence.value < 0):
                raise ValueError("cost evidence must be finite and nonnegative")
        expected_cap = {ExperimentMode.ACCEPTANCE:1200, ExperimentMode.SHORT_STUDY:1200,
                        ExperimentMode.CALIBRATION:1800, ExperimentMode.DIAGNOSTIC_DAY:87600}[ExperimentMode(self.mode)]
        if self.timing_profile == "azure_exp901_sweden_acceptance_v1":
            if (self.mode != ExperimentMode.ACCEPTANCE or self.selected_providers != [Provider.AZURE]
                    or self.experiment_id != "EXP-901" or self.context != "failure_check"
                    or self.scenario != Scenario.SAME_ZONE or self.config_sha256 is None
                    or self.resolved_sha256 is None):
                raise ValueError("Azure EXP-901 timing profile requires the exact Azure failure-check acceptance scope")
            expected_cap = 1800
        elif self.timing_profile is not None:
            raise ValueError("unsupported timing profile")
        if self.workflow_profile is not None:
            spec = PROFILE_SPECS.get(self.workflow_profile)
            if spec is None or spec["mode"] != self.mode.value or self.timing_profile is not None:
                raise ValueError("workflow profile is unsupported for this mode or timing profile")
            expected_cap = spec["cap_seconds"]
        if self.lifetime_cap_seconds != expected_cap:
            raise ValueError("lifetime_cap_seconds must match reservation mode")
        if self.schedule_entry_id.value is not None and not re.fullmatch(IDENTIFIER_PATTERN, self.schedule_entry_id.value):
            raise ValueError("schedule_entry_id must be an identifier")
        if not self.source_path.strip():
            raise ValueError("source_path must be nonblank")
        if self.incurred_cost_usd.value is not None and (not isfinite(self.incurred_cost_usd.value) or self.incurred_cost_usd.value < 0):
            raise ValueError("incurred cost must be finite and nonnegative")
        if self.active_reserved_usd.value is not None and (not isfinite(self.active_reserved_usd.value) or self.active_reserved_usd.value < 0):
            raise ValueError("active reserved cost must be finite and nonnegative")
        return self

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        schema = handler(core_schema)
        schema.setdefault("allOf", []).append({
            "if": {"properties": {"timing_profile": {"const": "azure_exp901_sweden_acceptance_v1"}},
                   "required": ["timing_profile"]},
            "then": {"properties": {"mode": {"const": "acceptance"}, "selected_providers": {"const": ["azure"]},
                "experiment_id": {"const": "EXP-901"}, "context": {"const": "failure_check"},
                "scenario": {"const": "same_zone"}, "lifetime_cap_seconds": {"const": 1800},
                "config_sha256": {"type": "string", "pattern": SHA256_PATTERN},
                "resolved_sha256": {"type": "string", "pattern": SHA256_PATTERN}},
                "required": ["experiment_id", "context", "scenario", "config_sha256", "resolved_sha256"]}
        })
        for profile, spec in PROFILE_SPECS.items():
            schema["allOf"].append({"if": {"properties": {"workflow_profile": {"const": profile}}, "required": ["workflow_profile"]},
                "then": {"properties": {"mode": {"const": spec["mode"]}, "lifetime_cap_seconds": {"const": spec["cap_seconds"]}}}})
        for mode, cap in (("acceptance", 1200), ("short_study", 1200),
                          ("calibration", 1800), ("diagnostic_day", 87600)):
            condition = {"properties": {"mode": {"const": mode}}, "required": ["mode"]}
            if mode == "acceptance":
                condition["not"] = {"properties": {"timing_profile": {
                    "const": "azure_exp901_sweden_acceptance_v1"}}, "required": ["timing_profile"]}
            profiles = {"acceptance": ("all_cloud_acceptance_v1", "all_cloud_failure_acceptance_v1"),
                        "calibration": ("all_cloud_calibration_v1",)}.get(mode, ())
            if profiles:
                existing = condition.get("not")
                profile_guards = [{"properties": {"workflow_profile": {"const": profile}}, "required": ["workflow_profile"]}
                                  for profile in profiles]
                if existing:
                    profile_guards.append(existing)
                condition["not"] = {"anyOf": profile_guards}
            schema["allOf"].append({"if": condition,
                "then": {"properties": {"lifetime_cap_seconds": {"const": cap}}}})
        return schema


class ReservationEvidence(StrictModel):
    schema_version: Literal[2]
    campaign_id: str = Field(pattern=IDENTIFIER_PATTERN)
    timing_profile: Optional[Literal["azure_exp901_sweden_acceptance_v1"]] = Field(
        default=None, exclude_if=lambda value: value is None)
    workflow_profile: Optional[Literal["all_cloud_acceptance_v1", "all_cloud_calibration_v1", "all_cloud_failure_acceptance_v1"]] = Field(default=None, exclude_if=lambda value: value is None)
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
        if self.reserved_cost_usd.value is not None and (not isfinite(self.reserved_cost_usd.value) or self.reserved_cost_usd.value < 0):
            raise ValueError("reserved cost must be finite and nonnegative")
        for evidence in (self.cap_started_at, self.expires_at):
            if evidence.value is not None:
                if evidence.value.tzinfo is None or evidence.value.utcoffset() is None:
                    raise ValueError("reservation timestamps must include a timezone")
                object.__setattr__(evidence, "value", utc_datetime(evidence.value))
        if self.state == "reserved":
            if any(item.value is None for item in (self.reservation_id, self.reserved_cost_usd, self.cap_started_at, self.expires_at, self.cap_start_policy_id)):
                raise ValueError("reserved evidence requires observed reservation, cost, cap times and policy")
            if self.expires_at.value <= self.cap_started_at.value:
                raise ValueError("reservation expiry must follow cap start")
            if self.reservation_id.value is not None and not re.fullmatch(IDENTIFIER_PATTERN, self.reservation_id.value):
                raise ValueError("reservation_id must be an identifier")
            if self.cap_start_policy_id.value is not None and not self.cap_start_policy_id.value.strip():
                raise ValueError("cap_start_policy_id must be nonblank")
        elif not self.reasons or any(not item.strip() for item in self.reasons):
            raise ValueError("non-reserved reservation state requires reasons")
        return self


class ExpiryRequest(StrictModel):
    schema_version: Literal[2]
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
        if not self.run_id.endswith(f"-{Provider(self.provider).value}"):
            raise ValueError("run_id provider suffix must match expiry provider")
        if self.deployment_id != f"{self.run_id}-deployment":
            raise ValueError("deployment_id must match expiry attempt")
        if self.expires_at.value is not None:
            if self.expires_at.value.tzinfo is None or self.expires_at.value.utcoffset() is None:
                raise ValueError("expiry request time must include a timezone")
            object.__setattr__(self.expires_at, "value", utc_datetime(self.expires_at.value))
        if not self.resource_namespace.strip() or not self.target_selector.strip():
            raise ValueError("expiry namespace and selector must be nonblank")
        return self


class ExpiryEvidence(StrictModel):
    schema_version: Literal[2]
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
        for evidence in (self.registered_at, self.expires_at):
            if evidence.value is not None and (evidence.value.tzinfo is None or evidence.value.utcoffset() is None):
                raise ValueError("expiry evidence timestamps must include a timezone")
            if evidence.value is not None:
                object.__setattr__(evidence, "value", utc_datetime(evidence.value))
        if not self.run_id.endswith(f"-{Provider(self.provider).value}"):
            raise ValueError("run_id provider suffix must match expiry provider")
        if self.deployment_id != f"{self.run_id}-deployment":
            raise ValueError("deployment_id must match expiry attempt")
        if not self.target_selector.strip():
            raise ValueError("target_selector must be nonblank")
        if self.registered_at.value is not None and self.expires_at.value is not None and self.expires_at.value <= self.registered_at.value:
            raise ValueError("expiry must follow registration")
        if self.state == "failed" and self.failure is None:
            raise ValueError("failed expiry requires failure evidence")
        return self
