from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Generic, Optional, Sequence, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
        return self


StringEvidence = Evidence[str]
StructuredEvidence = Evidence[Dict[str, Any]]


class FailureEvidence(StrictModel):
    category: str = Field(min_length=1)
    message: str = Field(min_length=1)
    occurred_at: datetime
    action_id: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def timestamp_is_aware(self) -> "FailureEvidence":
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return self


class LifecycleEvent(StrictModel):
    state_domain: LifecycleDomain
    state: str
    occurred_at: datetime
    detail: Optional[str] = None

    @model_validator(mode="after")
    def state_matches_domain(self) -> "LifecycleEvent":
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
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
