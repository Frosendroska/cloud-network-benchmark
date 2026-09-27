from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional, Tuple

from .contracts.common import (
    CampaignState,
    CleanupState,
    ExecutionState,
    FailureEvidence,
    LifecycleEvent,
)


EXECUTION_TRANSITIONS = {
    ExecutionState.INITIALIZED: {ExecutionState.PROVISION_READY, ExecutionState.FAILED, ExecutionState.INTERRUPTED},
    ExecutionState.PROVISION_READY: {ExecutionState.RUNNING, ExecutionState.FAILED, ExecutionState.INTERRUPTED},
    ExecutionState.RUNNING: {ExecutionState.COLLECTING, ExecutionState.FAILED, ExecutionState.INTERRUPTED},
    ExecutionState.COLLECTING: {ExecutionState.VALIDATING, ExecutionState.FAILED, ExecutionState.INTERRUPTED},
    ExecutionState.VALIDATING: {ExecutionState.SUCCEEDED, ExecutionState.FAILED, ExecutionState.INTERRUPTED},
    ExecutionState.SUCCEEDED: set(),
    ExecutionState.FAILED: set(),
    ExecutionState.INTERRUPTED: set(),
}

CLEANUP_TRANSITIONS = {
    CleanupState.NOT_STARTED: {CleanupState.NOT_REQUIRED, CleanupState.ATTEMPTED},
    CleanupState.ATTEMPTED: {CleanupState.SUCCEEDED, CleanupState.FAILED},
    CleanupState.NOT_REQUIRED: set(),
    CleanupState.SUCCEEDED: set(),
    CleanupState.FAILED: set(),
}


def transition_execution(
    current: ExecutionState,
    target: ExecutionState,
    occurred_at: datetime,
    failure: Optional[FailureEvidence] = None,
) -> LifecycleEvent:
    current, target = ExecutionState(current), ExecutionState(target)
    if target not in EXECUTION_TRANSITIONS[current]:
        raise ValueError(f"illegal execution transition: {current.value} -> {target.value}")
    if target == ExecutionState.FAILED and failure is None:
        raise ValueError("failed transition requires failure evidence")
    return LifecycleEvent(state_domain="execution", state=target.value, occurred_at=occurred_at, detail=failure.message if failure else None)


def transition_cleanup(current: CleanupState, target: CleanupState, occurred_at: datetime) -> LifecycleEvent:
    current, target = CleanupState(current), CleanupState(target)
    if target not in CLEANUP_TRANSITIONS[current]:
        raise ValueError(f"illegal cleanup transition: {current.value} -> {target.value}")
    return LifecycleEvent(state_domain="cleanup", state=target.value, occurred_at=occurred_at)


def aggregate_campaign(children: Iterable[Tuple[ExecutionState, CleanupState]]) -> CampaignState:
    values = [(ExecutionState(execution), CleanupState(cleanup)) for execution, cleanup in children]
    if not values:
        raise ValueError("campaign requires at least one child")
    if all(execution == ExecutionState.INITIALIZED for execution, _ in values):
        return CampaignState.INITIALIZED
    terminal = {ExecutionState.SUCCEEDED, ExecutionState.FAILED, ExecutionState.INTERRUPTED}
    if any(execution not in terminal for execution, _ in values):
        return CampaignState.PARTIALLY_COMPLETE if any(execution in terminal for execution, _ in values) else CampaignState.ACTIVE
    if all(execution == ExecutionState.SUCCEEDED and cleanup in {CleanupState.SUCCEEDED, CleanupState.NOT_REQUIRED} for execution, cleanup in values):
        return CampaignState.SUCCEEDED
    if any(execution == ExecutionState.FAILED or cleanup == CleanupState.FAILED for execution, cleanup in values):
        return CampaignState.FAILED
    return CampaignState.INTERRUPTED
