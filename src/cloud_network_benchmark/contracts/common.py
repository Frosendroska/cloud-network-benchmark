from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timezone
from math import isfinite
from enum import Enum
from typing import Any, Dict, Generic, Optional, Sequence, TypeVar, get_args, get_origin, Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StringEnum(str, Enum):
    pass


class Provider(StringEnum):
    AWS = "aws"
    AZURE = "azure"
    GCP = "gcp"


class Scenario(StringEnum):
    SAME_ZONE = "same_zone"
    CROSS_ZONE = "cross_zone"
    PLACEMENT_OPTIMIZATION = "placement_optimization"
    INTER_REGION = "inter_region"


class VmRole(StringEnum):
    VM_A = "vm_a"
    VM_B = "vm_b"


class MeasurementDirection(StringEnum):
    VM_A_TO_VM_B = "vm_a_to_vm_b"


class BenchmarkPhase(StringEnum):
    IDLE_LATENCY = "idle_latency"
    SINGLE_FLOW = "single_flow"
    MULTI_FLOW = "multi_flow"


class ExperimentMode(StringEnum):
    ACCEPTANCE = "acceptance"
    CALIBRATION = "calibration"
    SHORT_STUDY = "short_study"
    DIAGNOSTIC_DAY = "diagnostic_day"


class DataValidity(StringEnum):
    NOT_VALIDATED = "not_validated"
    VALID = "valid"
    PARTIAL = "partial"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"


class ComparisonEligibility(StringEnum):
    NOT_EVALUATED = "not_evaluated"
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    UNAVAILABLE = "unavailable"


class EvidenceState(StringEnum):
    PENDING = "pending"
    OBSERVED = "observed"
    UNAVAILABLE = "unavailable"


def utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(timezone.utc)


IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,191}$"
CONFIG_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"
SHA256_PATTERN = r"^[a-f0-9]{64}$"


class CommandOutcome(StringEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"


class ArtifactExpectation(StringEnum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    NOT_EXPECTED = "not_expected"


class ExecutionState(StringEnum):
    INITIALIZED = "initialized"
    PROVISION_READY = "provision_ready"
    RUNNING = "running"
    COLLECTING = "collecting"
    VALIDATING = "validating"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class CleanupState(StringEnum):
    NOT_STARTED = "not_started"
    NOT_REQUIRED = "not_required"
    ATTEMPTED = "attempted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class CampaignState(StringEnum):
    INITIALIZED = "initialized"
    ACTIVE = "active"
    PARTIALLY_COMPLETE = "partially_complete"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class LifecycleDomain(StringEnum):
    CAMPAIGN = "campaign"
    EXECUTION = "execution"
    CLEANUP = "cleanup"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)


T = TypeVar("T")


class Evidence(StrictModel, Generic[T]):
    value: Optional[T] = None
    unavailable_reason: Optional[str] = None

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema: Any, handler: Any) -> Dict[str, Any]:
        schema = handler(core_schema)
        value_schema = schema.get("properties", {}).get("value", {})
        if "anyOf" in value_schema:
            nonnull_value = deepcopy(value_schema)
            nonnull_value["anyOf"] = [item for item in nonnull_value["anyOf"] if item.get("type") != "null"]
        else:
            nonnull_value = deepcopy(value_schema)
        for item in nonnull_value.get("anyOf", []):
            if item.get("type") == "string":
                item.update({"minLength": 1, "pattern": r"\S"})
            elif item.get("type") == "object":
                item["minProperties"] = 1
        reason_schema = schema.get("properties", {}).get("unavailable_reason", {})
        nonnull_reason = deepcopy(reason_schema)
        if "anyOf" in reason_schema:
            nonnull_reason["anyOf"] = [item for item in nonnull_reason["anyOf"] if item.get("type") != "null"]
            if nonnull_reason["anyOf"]:
                nonnull_reason["anyOf"][0].update({"minLength": 1, "pattern": r"\S"})
        schema["oneOf"] = [
            {"properties": {"value": nonnull_value, "unavailable_reason": {"type":"null"}}, "required": ["value"]},
            {"properties": {"value": {"type":"null"}, "unavailable_reason": nonnull_reason}, "required": ["unavailable_reason"]},
        ]
        return schema

    @model_validator(mode="before")
    @classmethod
    def strict_numeric_value(cls, value: object) -> object:
        if isinstance(value, dict) and isinstance(value.get("value"), bool):
            args = cls.__pydantic_generic_metadata__.get("args", ())
            target = args[0] if args else None
            if get_origin(target) is Annotated:
                target = get_args(target)[0]
            if target in (int, float):
                raise ValueError("boolean is not a numeric evidence value")
        return value

    @model_validator(mode="after")
    def exactly_one(self) -> "Evidence[T]":
        if (self.value is None) == (self.unavailable_reason is None):
            raise ValueError("exactly one of value or unavailable_reason is required")
        if self.unavailable_reason is not None and not self.unavailable_reason.strip():
            raise ValueError("unavailable_reason must not be empty")
        if isinstance(self.value, str) and not self.value.strip():
            raise ValueError("observed string evidence must not be empty")
        if isinstance(self.value, dict) and not self.value:
            raise ValueError("observed structured evidence must not be empty")
        args = type(self).__pydantic_generic_metadata__.get("args", ())
        target = args[0] if args else None
        if get_origin(target) is Annotated:
            target = get_args(target)[0]
        if target is float and self.value is not None and not isfinite(self.value):
            raise ValueError("numeric evidence must be finite")
        if isinstance(self.value, datetime):
            if self.value.tzinfo is None or self.value.utcoffset() is None:
                raise ValueError("datetime evidence must include a timezone")
            object.__setattr__(self, "value", utc_datetime(self.value))
        return self


StringEvidence = Evidence[str]
StructuredEvidence = Evidence[Dict[str, Any]]


class IdentityContext(StrictModel):
    experiment_id: str = Field(pattern=r"^EXP-[0-9]{3,}$")
    config_id: str = Field(pattern=CONFIG_ID_PATTERN)
    campaign_id: str = Field(pattern=IDENTIFIER_PATTERN)
    observation_id: str = Field(pattern=IDENTIFIER_PATTERN)
    run_id: str = Field(pattern=IDENTIFIER_PATTERN)
    deployment_id: str = Field(pattern=IDENTIFIER_PATTERN)
    provider: Optional[Provider] = None
    replaces_run_id: Optional[str] = Field(default=None, pattern=IDENTIFIER_PATTERN)

    @model_validator(mode="after")
    def provider_suffix(self) -> "IdentityContext":
        if self.provider is not None and not self.run_id.endswith(f"-{Provider(self.provider).value}"):
            raise ValueError("run_id must end with the provider suffix")
        return self


class GitSnapshot(StrictModel):
    commit: StringEvidence
    dirty: Evidence[bool]
    diff_sha256: StringEvidence

    @field_validator("commit")
    @classmethod
    def valid_commit_hash(cls, value: StringEvidence) -> StringEvidence:
        if value.value is not None and len(value.value) not in {40, 64}:
            raise ValueError("commit must be a 40- or 64-character hash")
        if value.value is not None and any(ch not in "0123456789abcdefABCDEF" for ch in value.value):
            raise ValueError("commit must be hexadecimal")
        return value

    @field_validator("diff_sha256")
    @classmethod
    def valid_diff_hash(cls, value: StringEvidence) -> StringEvidence:
        if value.value is not None and not re.fullmatch(SHA256_PATTERN, value.value):
            raise ValueError("diff_sha256 must be lowercase SHA-256")
        return value


class ValidationStatus(StrictModel):
    data_validity: DataValidity = DataValidity.NOT_VALIDATED
    data_reasons: list[str] = Field(default_factory=lambda: ["not validated"])
    comparison_eligibility: ComparisonEligibility = ComparisonEligibility.NOT_EVALUATED
    comparison_reasons: list[str] = Field(default_factory=lambda: ["not evaluated"])

    @model_validator(mode="after")
    def statuses_have_reasons(self) -> "ValidationStatus":
        if DataValidity(self.data_validity) != DataValidity.VALID and not self.data_reasons:
            raise ValueError("non-valid data status requires data_reasons")
        if ComparisonEligibility(self.comparison_eligibility) != ComparisonEligibility.ELIGIBLE and not self.comparison_reasons:
            raise ValueError("non-eligible comparison status requires comparison_reasons")
        if ComparisonEligibility(self.comparison_eligibility) == ComparisonEligibility.ELIGIBLE and DataValidity(self.data_validity) != DataValidity.VALID:
            raise ValueError("eligible comparison requires valid data")
        if any(not item.strip() for item in (*self.data_reasons, *self.comparison_reasons)):
            raise ValueError("status reasons must be nonblank")
        return self

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema: Any, handler: Any) -> Dict[str, Any]:
        schema = handler(core_schema)
        schema["allOf"] = [
            {"if": {"properties": {"comparison_eligibility": {"const": "eligible"}}, "required": ["comparison_eligibility"]},
             "then": {"properties": {"data_validity": {"const": "valid"}}}},
            {"if": {"properties": {"data_validity": {"enum": ["not_validated", "partial", "invalid", "unavailable"]}}},
             "then": {"properties": {"data_reasons": {"minItems": 1}}}},
            {"if": {"properties": {"comparison_eligibility": {"enum": ["not_evaluated", "ineligible", "unavailable"]}}},
             "then": {"properties": {"comparison_reasons": {"minItems": 1}}}},
        ]
        return schema


class FailureEvidence(StrictModel):
    category: str = Field(min_length=1)
    message: str = Field(min_length=1)
    occurred_at: datetime
    action_id: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def timestamp_is_aware(self) -> "FailureEvidence":
        if not self.category.strip() or not self.message.strip():
            raise ValueError("failure category and message must be nonblank")
        if self.action_id is not None and not self.action_id.strip():
            raise ValueError("action_id must be nonblank when supplied")
        object.__setattr__(self, "occurred_at", utc_datetime(self.occurred_at))
        return self


class LifecycleEvent(StrictModel):
    state_domain: LifecycleDomain
    state: str
    occurred_at: datetime
    detail: Optional[str] = None

    @model_validator(mode="after")
    def state_matches_domain(self) -> "LifecycleEvent":
        object.__setattr__(self, "occurred_at", utc_datetime(self.occurred_at))
        if self.detail is not None and not self.detail.strip():
            raise ValueError("lifecycle detail must be nonblank when supplied")
        allowed = {
            LifecycleDomain.CAMPAIGN: {item.value for item in CampaignState},
            LifecycleDomain.EXECUTION: {item.value for item in ExecutionState},
            LifecycleDomain.CLEANUP: {item.value for item in CleanupState},
        }
        if self.state not in allowed[LifecycleDomain(self.state_domain)]:
            raise ValueError(f"state {self.state!r} is invalid for {self.state_domain} lifecycle events")
        return self


def validate_event_chronology(events: Sequence[LifecycleEvent]) -> None:
    for previous, current in zip(events, events[1:]):
        if current.occurred_at < previous.occurred_at:
            raise ValueError("lifecycle events must be chronological")
