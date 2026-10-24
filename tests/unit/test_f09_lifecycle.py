from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from cloud_network_benchmark.orchestration import (
    LifecycleContext,
    ProviderHooks,
    execute_provider_workflows,
    run_lifecycle,
)
from tests.fixtures.f09.helpers import FakeClock, Trace


def hooks(provider, trace, *, protect=None, fail=None, start_barrier=None):
    def run(stage):
        trace.add(provider, stage)
        if stage == "create" and start_barrier is not None:
            start_barrier.wait(timeout=1)
        if stage == fail:
            raise RuntimeError(f"{provider} {stage}")
        return {"provider": provider, "stage": stage, "synthetic": True}

    return ProviderHooks(
        provider=provider,
        protect=lambda: protect() if protect else run("protect"),
        claim=lambda: run("claim"),
        create=lambda: run("create"),
        prepare=lambda: run("prepare"),
        stabilize=lambda: run("stabilize"),
        measure=lambda: run("measure"),
        close=lambda: run("close"),
        retrieve=lambda: run("retrieve"),
        cleanup=lambda: run("cleanup"),
    )


def test_all_protection_is_verified_before_any_claim_or_create():
    trace = Trace()
    result = execute_provider_workflows(
        {name: hooks(name, trace) for name in ("aws", "azure", "gcp")},
        context=LifecycleContext("session-1", 100, 1000, 1200),
        clock=FakeClock(),
    )
    first_claim = min(i for i, (_, stage) in enumerate(trace.events) if stage == "claim")
    assert all(stage == "protect" for _, stage in trace.events[:first_claim])
    assert len([1 for _, stage in trace.events if stage == "create"]) == 3
    assert result.completed


def test_failed_protection_blocks_all_creation_and_cleans_safety_liabilities():
    trace = Trace()
    def bad_protection():
        trace.add("azure", "protect")
        raise RuntimeError("synthetic registration uncertainty")
    result = execute_provider_workflows(
        {"aws": hooks("aws", trace), "azure": hooks("azure", trace, protect=bad_protection)},
        context=LifecycleContext("session-1", 100, 1000, 1200),
        clock=FakeClock(),
    )
    assert not any(stage in {"claim", "create"} for _, stage in trace.events)
    assert {p for p, stage in trace.events if stage == "cleanup"} == {"aws", "azure"}
    assert result.primary_failure is not None


def test_healthy_providers_overlap_and_failed_peer_does_not_skip_cleanup():
    trace = Trace()
    barrier = Barrier(2)
    result = execute_provider_workflows(
        {"aws": hooks("aws", trace, fail="prepare", start_barrier=barrier),
         "gcp": hooks("gcp", trace, start_barrier=barrier)},
        context=LifecycleContext("session-1", 100, 1000, 1200),
        clock=FakeClock(),
        max_workers=2,
    )
    assert result.provider_results["gcp"].execution_state == "succeeded"
    assert result.provider_results["aws"].execution_state == "failed"
    assert {p for p, stage in trace.events if stage == "cleanup"} == {"aws", "gcp"}


def test_claim_failure_is_provider_local_after_complete_protection():
    trace = Trace()
    rows = {name: hooks(name, trace, fail="claim" if name == "aws" else None)
            for name in ("aws", "gcp")}
    result = execute_provider_workflows(rows,
        context=LifecycleContext("session-1", 100, 1000, 1200), clock=FakeClock())
    assert not any(provider == "aws" and stage == "create" for provider, stage in trace.events)
    assert any(provider == "gcp" and stage == "create" for provider, stage in trace.events)
    assert {provider for provider, stage in trace.events if stage == "cleanup"} == {"aws", "gcp"}
    assert not result.completed


def test_worker_interrupt_requests_scoped_cancel_then_closes_and_cleans_created_pair():
    trace = Trace()
    base = hooks("aws", trace)
    interrupted = ProviderHooks(**{**base.__dict__,
        "measure":lambda:(_ for _ in ()).throw(KeyboardInterrupt()),
        "cancel":lambda:trace.add("aws", "cancel")})
    result = execute_provider_workflows({"aws":interrupted},
        context=LifecycleContext("session-1", 100, 1000, 1200), clock=FakeClock())
    stages = [stage for _, stage in trace.events]
    assert "cancel" in stages and "close" in stages and "retrieve" in stages and "cleanup" in stages
    assert not result.completed and result.provider_results["aws"].primary_failure


def test_controller_session_and_cap_are_immutable_for_stage_checks():
    context = LifecycleContext("session-1", 100.0, 1000.0, 1200, {"measurement": 900.0})
    with pytest.raises(ValueError, match="cap"):
        context.require_fit("measurement", 901.0, now_monotonic=100.0)
    with pytest.raises(ValueError, match="session"):
        context.assert_session("session-2")


def test_admission_is_reserved_before_provider_factory_and_offline_scope_never_dispatches():
    class Ledger:
        def __init__(self, status): self.status, self.calls = status, []
        def reserve(self, request, *, plan):
            self.calls.append("reserve")
            from types import SimpleNamespace
            return SimpleNamespace(status=self.status, evidence=None, reasons=("prerequisite_pending",))
    class Plan:
        execution_scope = "paid_request"
    ledger = Ledger("rejected")
    factory_called = False
    def factory(_evidence):
        nonlocal factory_called
        factory_called = True
        return {}
    result = run_lifecycle(ledger=ledger, reservation_request=object(), admission_plan=Plan(),
        allowance=object(), workflow_factory=factory,
        context=LifecycleContext("s", 1, 100, 100), clock=FakeClock())
    assert result.admission_status == "rejected"
    assert ledger.calls == ["reserve"] and not factory_called
    class OfflinePlan: execution_scope = "offline_simulation"
    with pytest.raises(ValueError, match="offline_simulation"):
        run_lifecycle(ledger=ledger, reservation_request=object(), admission_plan=OfflinePlan(),
            allowance=object(), workflow_factory=factory,
            context=LifecycleContext("s", 1, 100, 100), clock=FakeClock())
    assert ledger.calls == ["reserve"]
