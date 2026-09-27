from datetime import datetime, timezone

import pytest

from cloud_network_benchmark.contracts import CampaignState, CleanupState, ExecutionState, FailureEvidence
from cloud_network_benchmark.lifecycle import aggregate_campaign, transition_cleanup, transition_execution


NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


def test_canonical_execution_path() -> None:
    states = [ExecutionState.INITIALIZED, ExecutionState.PROVISION_READY, ExecutionState.RUNNING, ExecutionState.COLLECTING, ExecutionState.VALIDATING, ExecutionState.SUCCEEDED]
    for current, target in zip(states, states[1:]):
        assert transition_execution(current, target, NOW).state == target.value


def test_failure_requires_evidence_and_terminal_is_immutable() -> None:
    with pytest.raises(ValueError):
        transition_execution(ExecutionState.RUNNING, ExecutionState.FAILED, NOW)
    failure = FailureEvidence(category="command", message="failed", occurred_at=NOW)
    assert transition_execution(ExecutionState.RUNNING, ExecutionState.FAILED, NOW, failure).detail == "failed"
    with pytest.raises(ValueError):
        transition_execution(ExecutionState.FAILED, ExecutionState.RUNNING, NOW)


def test_cleanup_and_parent_aggregation() -> None:
    assert transition_cleanup(CleanupState.NOT_STARTED, CleanupState.ATTEMPTED, NOW).state == "attempted"
    assert aggregate_campaign([(ExecutionState.INITIALIZED, CleanupState.NOT_STARTED)]) == CampaignState.INITIALIZED
    assert aggregate_campaign([(ExecutionState.SUCCEEDED, CleanupState.SUCCEEDED)]) == CampaignState.SUCCEEDED
    assert aggregate_campaign([(ExecutionState.SUCCEEDED, CleanupState.FAILED)]) == CampaignState.FAILED
    assert aggregate_campaign([(ExecutionState.INTERRUPTED, CleanupState.SUCCEEDED)]) == CampaignState.INTERRUPTED


@pytest.mark.parametrize("alias", ["resolved", "pending", "provision-ready"])
def test_lifecycle_aliases_are_rejected(alias: str) -> None:
    with pytest.raises(ValueError):
        ExecutionState(alias)
