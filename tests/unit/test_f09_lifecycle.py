from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from threading import Barrier

import pytest

from cloud_network_benchmark.orchestration import (
    LifecycleContext,
    cleanup_lifecycle,
    ProviderHooks,
    ProviderStageOutcome,
    SavedLifecycleRecovery,
    execute_provider_workflows,
    run_lifecycle,
    consume_retry_attempt,
    write_recovery_journal,
    _remaining_f11_allowance,
    _stage_budget_name,
    update_provider_recovery,
    _typed_outcome,
)
from cloud_network_benchmark.admission import WorkflowAllowance
from tests.fixtures.f09.helpers import FakeClock, Trace


def hooks(provider, trace, *, protect=None, fail=None, start_barrier=None):
    def run(stage):
        trace.add(provider, stage)
        if stage == "create" and start_barrier is not None:
            start_barrier.wait(timeout=1)
        if stage == fail:
            raise RuntimeError(f"{provider} {stage}")
        return ProviderStageOutcome(stage, True, {"provider": provider, "stage": stage, "synthetic": True})

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


@pytest.mark.parametrize("archive_ok", [True, False])
def test_archive_verification_controls_retrieval_success_then_cleanup_without_remeasurement(archive_ok):
    from cloud_network_benchmark.retrieval import RetrievalResult
    trace = Trace()
    base = hooks("aws", trace)
    retrieval = RetrievalResult(complete=archive_ok,
        failures=[] if archive_ok else [{"reason": "remote verification failed"}],
        archive_status={"status": "verified" if archive_ok else "failed", "verified": archive_ok})
    result = ProviderHooks(**{**base.__dict__,
        "retrieve": lambda: (trace.add("aws", "retrieve") or
            ProviderStageOutcome("retrieve", archive_ok, retrieval))})
    outcome = execute_provider_workflows({"aws": result},
        context=LifecycleContext("session-archive", 100, 1000, 1200), clock=FakeClock())
    stages = [stage for provider, stage in trace.events if provider == "aws"]
    assert stages.count("measure") == 1
    assert stages.count("cleanup") == 1 and stages[-1] == "cleanup"
    assert (outcome.provider_results["aws"].execution_state == "succeeded") is archive_ok
    assert outcome.provider_results["aws"].cleanup_state == "succeeded"
    if not archive_ok:
        assert outcome.provider_results["aws"].primary_failure


def test_lifecycle_cli_projects_only_nonsecret_archive_status():
    from types import SimpleNamespace
    from cloud_network_benchmark.lifecycle_cli import _archive_status_projection
    evidence = SimpleNamespace(archive_status={"status": "failed", "verified": False,
        "bucket": "research-archive", "prefix": "cnb/v1/aws/exp/run/",
        "partial_objects": [{"key": "manifest.json", "generation": "4"}],
        "session_uri": "must-not-escape"})
    result = SimpleNamespace(values={"retrieve": SimpleNamespace(evidence=evidence)})
    projected = _archive_status_projection(result)
    assert projected["status"] == "failed" and projected["verified"] is False
    assert "partial_objects" in projected and "session_uri" not in projected


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


def test_protection_interrupt_is_caught_and_attempts_cleanup_for_uncertain_registration():
    trace = Trace()
    base = hooks("aws", trace)
    interrupted = ProviderHooks(**{**base.__dict__,
        "protect":lambda:(_ for _ in ()).throw(KeyboardInterrupt()),
        "cancel":lambda:trace.add("aws", "cancel")})
    result = execute_provider_workflows({"aws":interrupted},
        context=LifecycleContext("session-1", 100, 1000, 1200), clock=FakeClock())
    stages = [stage for _, stage in trace.events]
    assert "cleanup" in stages and "cancel" in stages
    assert not any(stage in {"claim", "create"} for stage in stages)
    assert not result.completed and result.provider_results["aws"].interrupted


def test_returned_provider_failure_and_unregistered_expiry_are_not_success():
    trace = Trace()
    base = hooks("aws", trace)
    bad_expiry = ProviderHooks(**{**base.__dict__, "protect":lambda:{"state":"failed"}})
    outcome = execute_provider_workflows({"aws":bad_expiry},
        context=LifecycleContext("session-1", 100, 1000, 1200), clock=FakeClock())
    assert not outcome.completed
    assert "registered expiry" in outcome.provider_results["aws"].primary_failure
    assert "claim" not in [stage for _, stage in trace.events]


def test_claim_denial_reports_only_bounded_stage_decision_reason_codes():
    from types import SimpleNamespace

    denied = SimpleNamespace(eligible=False, creation_granted=False,
        reason_codes=("financial_outstanding_liability", "controller_session_changed"))
    ok, message = _typed_outcome("aws", "claim", denied)
    assert not ok
    assert message == ("F11 creation claim was not granted "
        "(financial_outstanding_liability, controller_session_changed)")

    malformed = SimpleNamespace(eligible=False, creation_granted=False,
        reason_codes=("safe_code", "secret value", "x" * 65, 7))
    ok, message = _typed_outcome("aws", "claim", malformed)
    assert not ok
    assert message == "F11 creation claim was not granted (safe_code)"

    granted = SimpleNamespace(eligible=True, creation_granted=True,
        reason_codes=("creation_granted",))
    assert _typed_outcome("aws", "claim", granted) == (True, None)

    trace = Trace()
    base = hooks("aws", trace)
    bad_cleanup = ProviderHooks(**{**base.__dict__,
        "cleanup":lambda:{"cleanup_state":"failed", "launch_blocked":True}})
    outcome = execute_provider_workflows({"aws":bad_cleanup},
        context=LifecycleContext("session-1", 100, 1000, 1200), clock=FakeClock())
    assert not outcome.completed
    assert outcome.provider_results["aws"].cleanup_state == "failed"
    assert "cleanup is failed" in outcome.provider_results["aws"].primary_failure


@pytest.mark.parametrize("provider,inventory", [
    ("aws", {"complete": True, "safety_absent": {"eu-central-1": True},
        "regions": {"eu-central-1": {"complete": True, "resources": {"instances": [], "known_ids": {}}}}}),
    ("gcp", {"pages_complete": True, "zone_scope": "all_project_zones",
        "completed_scopes": ["all_project_zones", "global"], "resources": []}),
    ("azure", []),
])
def test_cleanup_requires_provider_inventory_proving_absence(provider, inventory):
    from types import SimpleNamespace
    valid = SimpleNamespace(cleanup_state="succeeded", launch_blocked=False,
        failures=[], inventory=SimpleNamespace(value=inventory), resources=inventory if provider == "azure" else None)
    assert _typed_outcome(provider, "cleanup", valid)[0]
    if provider == "azure":
        valid.resources = [{"id": "still-present"}]
    else:
        valid.inventory = SimpleNamespace(value={**inventory,
            ("safety_absent" if provider == "aws" else "resources"): {"zone": False} if provider == "aws" else [{"id": "still-present"}]})
    assert not _typed_outcome(provider, "cleanup", valid)[0]


@pytest.mark.parametrize("now,deadline,trigger", [(999.0, 1000.0, 200.0), (201.0, 200.0, None)])
def test_original_cap_or_earlier_provider_trigger_blocks_stages_but_still_cleans(now, deadline, trigger):
    trace = Trace()
    context = LifecycleContext("session", 1, deadline, 1200,
        provider_trigger_monotonic=trigger)
    clock = FakeClock(monotonic=now)
    result = execute_provider_workflows({"aws":hooks("aws", trace)}, context=context, clock=clock)
    stages = [stage for _, stage in trace.events]
    assert not result.completed
    assert "protect" not in stages and "claim" not in stages and "create" not in stages
    assert "cleanup" in stages


def test_controller_session_and_cap_are_immutable_for_stage_checks():
    context = LifecycleContext("session-1", 100.0, 1000.0, 1200, {"measurement": 900.0})
    with pytest.raises(ValueError, match="cap"):
        context.require_fit("measurement", 901.0, now_monotonic=100.0)
    with pytest.raises(ValueError, match="session"):
        context.assert_session("session-2")


def test_f11_stage_fit_receives_exact_remaining_lifecycle_remainder():
    full = WorkflowAllowance(10, 20, 30, 40, 60, 5, 12, 8, "sourced", {"aws": 50})
    registration = _remaining_f11_allowance(full, "registration")
    preparation = _remaining_f11_allowance(full, "preparation")
    measurement = _remaining_f11_allowance(full, "measurement")
    retrieval = _remaining_f11_allowance(full, "retrieval")
    assert registration == full
    consumed = _remaining_f11_allowance(full, "registration", registration_elapsed=7)
    assert consumed.registration_seconds == 3
    assert consumed.provision_seconds == full.provision_seconds
    shared = WorkflowAllowance(900, 900, 600, 60, 100, 15, 300, 660,
        "sourced", {"aws": 360, "azure": 180, "gcp": 60})
    assert _stage_budget_name("protect") == "registration"
    assert _remaining_f11_allowance(shared, "registration", registration_elapsed=200 + 180).registration_seconds == 520
    assert _remaining_f11_allowance(shared, "registration", registration_elapsed=900).registration_seconds == 0
    remaining_after_serial = _remaining_f11_allowance(shared, "registration", registration_elapsed=380)
    assert shared.fit_seconds(now=datetime.now(timezone.utc)) == 3535
    assert shared.fit_seconds(now=datetime.now(timezone.utc)) > 3460
    assert remaining_after_serial.fit_seconds(now=datetime.now(timezone.utc)) == 3155
    assert remaining_after_serial.fit_seconds(now=datetime.now(timezone.utc)) <= 3460
    assert preparation.completed_stages == ("registration",)
    assert (preparation.registration_seconds, preparation.provision_seconds,
            preparation.readiness_seconds, preparation.stabilization_seconds) == (0, 20, 30, 40)
    assert measurement.completed_stages == ("registration", "preparation")
    assert (measurement.registration_seconds, measurement.provision_seconds,
            measurement.readiness_seconds, measurement.stabilization_seconds) == (0, 0, 0, 0)
    assert measurement.measurement_seconds == 60 and measurement.measurement_shutdown_seconds == 5
    assert retrieval.completed_stages == ("registration", "preparation", "measurement")
    assert retrieval.measurement_seconds == 0 and retrieval.measurement_shutdown_seconds == 0
    assert retrieval.retrieval_seconds == 12 and retrieval.cleanup_seconds == 8


@pytest.mark.parametrize(("provider_elapsed", "gcp_eligible", "remaining_seconds", "expected_vector"), [
    ((200, 180), True, 520, [900, 700, 520]),
    ((500, 400), False, 0, [900, 400, 0])])
def test_run_lifecycle_registration_admission_debits_shared_budget(
        tmp_path, monkeypatch, provider_elapsed, gcp_eligible, remaining_seconds, expected_vector):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    import cloud_network_benchmark.orchestration as orchestration
    from cloud_network_benchmark.orchestration import LifecycleResult
    from cloud_network_benchmark.admission import StageDecision

    allowance = WorkflowAllowance(900, 900, 600, 60, 100, 15, 300, 660,
        "sourced", {"aws": 360, "azure": 180, "gcp": 60})
    clock = FakeClock(monotonic=100.0)
    evidence = SimpleNamespace(reservation_id=SimpleNamespace(value="reservation-1"))
    class Ledger:
        controller_session_id = "session"
        def __init__(self): self.fits = []
        def reserve(self, *_args, **_kwargs):
            return SimpleNamespace(status="reserved", evidence=evidence, reasons=())
        def inspect(self):
            return {"reservations": {"reservation-1": {"cap_start_monotonic": 100.0,
                "cap_seconds": 4200.0, "provider_trigger_monotonic": 3940.0}}}
        def check_stage_fit(self, _reservation_id, *, stage, remaining):
            live = 3840.0 - (clock.monotonic - 100.0)
            fits = (remaining.registration_seconds > 0 and remaining.fit_seconds(
                now=datetime(2026, 11, 22, tzinfo=timezone.utc)) <= live)
            self.fits.append((stage, remaining.registration_seconds, fits))
            return StageDecision(fits, () if fits else ("workflow_time_insufficient",), None)

    ledger = Ledger()
    plan = SimpleNamespace(execution_scope="paid_request", resolved_campaign=None)
    request = SimpleNamespace(selected_providers=["aws", "azure", "gcp"])
    context = LifecycleContext("session", 100.0, 4300.0, 4200.0)
    def recovery_factory(_evidence, _workflows):
        return SavedLifecycleRecovery(schema_version=1, attempt_id="attempt",
            controller_session_id="session", reservation_id="reservation-1",
            providers=[{"provider": name, "deployment_id": f"{name}-deployment",
                "journal_reference": f"{name}.json", "expiry_registered": False,
                "creation_may_have_started": False, "cleanup_open": False}
                for name in ("aws", "azure", "gcp")])

    def execute(workflows, **_kwargs):
        for provider, spent in zip(("aws", "azure", "gcp"), (*provider_elapsed, 0)):
            decision = workflows[provider].stage_admission("registration")
            if provider == "gcp":
                assert decision.eligible is gcp_eligible
                assert ledger.fits[-1][1] == remaining_seconds
            clock.monotonic += spent
        return LifecycleResult({}, False, "fake registration admission")

    monkeypatch.setattr(orchestration, "execute_provider_workflows", execute)
    workflows = {name: hooks(name, Trace()) for name in ("aws", "azure", "gcp")}
    result = run_lifecycle(ledger=ledger, reservation_request=request, admission_plan=plan,
        allowance=allowance, workflow_factory=lambda _evidence: workflows, context=context,
        clock=clock, recovery_path=tmp_path/"recovery.json", recovery_factory=recovery_factory)
    assert [item[1] for item in ledger.fits[1:]] == expected_vector, (
        result.primary_failure, result.admission_reasons, result.provider_results)
    assert result.admission_status == "reserved"


def test_recovery_journal_durably_consumes_each_retry_once(tmp_path):
    path = tmp_path / "private" / "recovery.json"
    recovery = SavedLifecycleRecovery(schema_version=1, attempt_id="attempt-retry", controller_session_id="session",
        providers=[{"provider":"aws", "deployment_id":"run-aws-deployment",
            "journal_reference":"private/aws.json", "expiry_registered":True,
            "creation_may_have_started":True, "cleanup_open":True}])
    write_recovery_journal(path, recovery)
    assert consume_retry_attempt(path, "native-artifact") is True
    assert consume_retry_attempt(path, "native-artifact") is False
    saved = SavedLifecycleRecovery.validate_unique(__import__("json").loads(path.read_text()))
    assert saved.retry_consumed_ids == ["native-artifact"]


def test_late_cleanup_timing_is_durable_without_reopening_absence_liability(tmp_path):
    path = tmp_path / "private" / "recovery.json"
    recovery = SavedLifecycleRecovery(schema_version=1, attempt_id="attempt-timing",
        controller_session_id="session", reservation_id="reservation-timing",
        providers=[{"provider": "aws", "deployment_id": "aws-deployment",
            "journal_reference": "private/aws.json", "expiry_registered": True,
            "creation_may_have_started": True, "cleanup_open": True}])
    write_recovery_journal(path, recovery)
    update_provider_recovery(path, "aws", "cleanup", "succeeded",
        cleanup_timing_status="late")
    saved = SavedLifecycleRecovery.validate_unique(__import__("json").loads(path.read_text()))
    row = saved.providers[0]
    assert row.cleanup_open is False and row.expiry_registered is False
    assert row.creation_may_have_started is False and row.cleanup_timing_status == "late"
    result = cleanup_lifecycle(path, current_session_id="session", cleanup_actions={},
        reconcile=lambda *_args: None)
    assert result.providers["aws"]["acceptance_failed"] is True
    assert result.providers["aws"]["cleanup"] == "not_required"
    assert result.complete is False


@pytest.mark.parametrize(("previous_status", "timing_status", "expected_status", "acceptance_failed"), [
    (None, "late", "late", True),
    (None, "within_deadline", "within_deadline", False),
    ("late", "within_deadline", "late", True),
    ("unknown", "within_deadline", "unknown", True)])
def test_cleanup_recovery_projects_new_receipt_timing_after_absence_proof(
        tmp_path, previous_status, timing_status, expected_status, acceptance_failed):
    path = tmp_path / "private" / "recovery.json"
    recovery = SavedLifecycleRecovery(schema_version=1, attempt_id="attempt-recovered-timing",
        controller_session_id="session", reservation_id="reservation-recovered-timing",
        providers=[{"provider": "aws", "deployment_id": "aws-deployment",
            "journal_reference": "private/aws.json", "expiry_registered": True,
            "creation_may_have_started": True, "cleanup_open": True,
            "cleanup_timing_status": previous_status,
            "cleanup_timing_failure": "earlier acceptance timing was unresolved"
                if previous_status else None}])
    write_recovery_journal(path, recovery)
    from types import SimpleNamespace
    cleanup = SimpleNamespace(cleanup_state="succeeded",
        inventory=SimpleNamespace(value={"cleanup_timing": {"status": timing_status}}))
    reconciliation = {"absence_proved": True, "billable_resources_present": False,
        "cleanup_open": False, "billing_final": False, "incurred_usd": 0.0}
    result = cleanup_lifecycle(path, current_session_id="recovery-session",
        cleanup_actions={"aws": lambda _record: cleanup}, reconcile=lambda *_args: reconciliation)
    row = SavedLifecycleRecovery.validate_unique(json.loads(path.read_text())).providers[0]
    assert row.cleanup_open is False
    assert row.cleanup_timing_status == expected_status
    assert result.providers["aws"]["absence_proved"] is True
    assert result.providers["aws"]["acceptance_failed"] is acceptance_failed
    assert result.complete is (not acceptance_failed)


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
