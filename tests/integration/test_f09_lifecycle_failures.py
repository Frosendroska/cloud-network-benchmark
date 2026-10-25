from __future__ import annotations

import pytest

from cloud_network_benchmark.orchestration import LifecycleContext, ProviderHooks, ProviderStageOutcome, execute_provider_workflows
from tests.fixtures.f09.helpers import FakeClock


STAGES = ("protect", "claim", "create", "prepare", "stabilize", "measure", "close", "retrieve", "cleanup")


def make_hooks(provider, fail_at=None, *, failures=()):
    def action(stage):
        def run():
            if stage == fail_at or stage in failures:
                raise RuntimeError(f"synthetic {provider} {stage}")
            return ProviderStageOutcome(stage, True, {"synthetic": True, "stage": stage})
        return run
    return ProviderHooks(provider, *(action(stage) for stage in STAGES))


@pytest.mark.parametrize("provider", ["aws", "azure", "gcp"])
@pytest.mark.parametrize("stage", STAGES)
def test_stage_failure_is_recorded_and_all_protected_peers_are_finalized(provider, stage):
    peers = {name: make_hooks(name, fail_at=stage if name == provider else None)
             for name in ("aws", "azure", "gcp")}
    outcome = execute_provider_workflows(peers,
        context=LifecycleContext("session", 1, 100000, 100000), clock=FakeClock())
    assert not outcome.completed
    assert outcome.primary_failure is not None
    for name, item in outcome.provider_results.items():
        assert item.cleanup_state == ("failed" if stage == "cleanup" and name == provider else "succeeded")
    if stage == "protect":
        assert not any(item.values.get("create") for item in outcome.provider_results.values())
    assert sum("create" in item.values for item in outcome.provider_results.values()) <= 3


def test_primary_failure_survives_cleanup_and_records_secondary_failure():
    peers = {"aws": make_hooks("aws", fail_at="measure", failures=("cleanup",)),
             "gcp": make_hooks("gcp")}
    outcome = execute_provider_workflows(peers,
        context=LifecycleContext("session", 1, 100000, 100000), clock=FakeClock())
    aws = outcome.provider_results["aws"]
    assert aws.primary_failure == "aws measure failed (RuntimeError)"
    assert "aws cleanup failed (RuntimeError)" in aws.secondary_failures
    assert aws.cleanup_state == "failed"
    assert outcome.provider_results["gcp"].cleanup_state == "succeeded"


def test_changed_controller_session_prevents_attempt_reuse():
    context = LifecycleContext("original-session", 1, 100, 120)
    with pytest.raises(ValueError, match="cannot resume"):
        context.assert_session("new-session")
