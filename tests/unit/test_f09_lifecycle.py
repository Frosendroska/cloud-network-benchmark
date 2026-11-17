from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from cloud_network_benchmark.orchestration import (
    LifecycleContext,
    ProviderHooks,
    ProviderStageOutcome,
    SavedLifecycleRecovery,
    execute_provider_workflows,
    run_lifecycle,
    consume_retry_attempt,
    write_recovery_journal,
    _stage_budget_name,
    _remaining_f11_allowance,
    _typed_outcome,
)
from cloud_network_benchmark.admission import WorkflowAllowance
from cloud_network_benchmark.lifecycle_projection import _failed_stage
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
        "regions": {"eu-central-1": {"complete": True, "remaining": {},
            "resources": {"instances": [], "known_ids": {}}}}}),
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


def test_aws_cleanup_accepts_terminated_inventory_when_remaining_is_empty():
    from types import SimpleNamespace
    terminated_instance = {"InstanceId": "i-terminated", "State": {"Name": "terminated"}}
    valid = SimpleNamespace(cleanup_state="succeeded", launch_blocked=False,
        failures=[], inventory=SimpleNamespace(value={"complete": True,
            "safety_absent": {"eu-north-1": True},
            "regions": {"eu-north-1": {"complete": True, "remaining": {},
                "resources": {"instances": [terminated_instance]}}}}))
    assert _typed_outcome("aws", "cleanup", valid)[0]

    valid.inventory.value["regions"]["eu-north-1"]["remaining"] = {"volume": ["vol-still-present"]}
    assert not _typed_outcome("aws", "cleanup", valid)[0]
    del valid.inventory.value["regions"]["eu-north-1"]["remaining"]
    assert not _typed_outcome("aws", "cleanup", valid)[0]


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
    claim_phase = _stage_budget_name("claim")
    claim_remainder = _remaining_f11_allowance(full, claim_phase)
    preparation = _remaining_f11_allowance(full, "preparation")
    post_provision = _remaining_f11_allowance(full, "post_provision")
    measurement = _remaining_f11_allowance(full, "measurement")
    retrieval = _remaining_f11_allowance(full, "retrieval")
    stabilization = _remaining_f11_allowance(full, "stabilization")
    assert registration == full
    assert claim_phase == "preparation"
    assert claim_remainder == preparation
    assert preparation.completed_stages == ("registration",)
    assert (preparation.registration_seconds, preparation.provision_seconds,
            preparation.readiness_seconds, preparation.stabilization_seconds) == (0, 20, 30, 40)
    assert post_provision.completed_stages == ("registration", "provision")
    assert (post_provision.registration_seconds, post_provision.provision_seconds,
            post_provision.readiness_seconds, post_provision.stabilization_seconds) == (0, 0, 30, 40)
    assert _stage_budget_name("stabilize") == "stabilization"
    assert stabilization.completed_stages == ("registration", "provision", "readiness")
    assert (stabilization.registration_seconds, stabilization.provision_seconds,
            stabilization.readiness_seconds, stabilization.stabilization_seconds) == (0, 0, 0, 40)
    assert stabilization.total_seconds == 40 + 60 + 5 + 12 + 8
    assert measurement.completed_stages == ("registration", "preparation")
    assert (measurement.registration_seconds, measurement.provision_seconds,
            measurement.readiness_seconds, measurement.stabilization_seconds) == (0, 0, 0, 0)
    assert measurement.measurement_seconds == 60 and measurement.measurement_shutdown_seconds == 5
    assert retrieval.completed_stages == ("registration", "preparation", "measurement")
    assert retrieval.measurement_seconds == 0 and retrieval.measurement_shutdown_seconds == 0
    assert retrieval.retrieval_seconds == 12 and retrieval.cleanup_seconds == 8


@pytest.mark.parametrize(("protect_seconds", "create_seconds", "create_succeeds", "prepare_expected"), [
    (105, 180, True, True),   # 555s remain: post-provision 550s fits, preparation 730s no longer fits.
    (105, 189, True, False),  # 546s remain: post-provision 550s does not fit.
    (111, 0, True, False),    # Before create, 729s cannot cover the full 730s preparation remainder.
    (105, 180, False, False), # Failed create never earns completed-provision credit.
], ids=["post-provision-fits", "546-of-550-insufficient", "pre-create-full-fit", "failed-create-no-credit"])
def test_run_lifecycle_uses_real_f11_post_provision_stage_fit(
        tmp_path, protect_seconds, create_seconds, create_succeeds, prepare_expected,
        isolated_legacy_provider_financial_boundary):
    import hashlib
    import json
    from decimal import Decimal
    from cloud_network_benchmark.admission import (AdmissionPlan, BudgetLedger, aws_expiry_cost_handoff,
        load_cost_inputs)
    from cloud_network_benchmark.config import resolve_campaign
    from cloud_network_benchmark.contracts.admission import ReservationRequest
    from cloud_network_benchmark.contracts.common import Evidence, Provider

    root = __import__("pathlib").Path(__file__).parents[2]
    campaign, _ = resolve_campaign(root / "configs/tests/exp-900-single-provider.yaml", root)
    allowance = WorkflowAllowance(105, 180, 210, 60, 100, 15, 45, 120,
        "post-create-fit-policy", {"aws": 360})
    cost_payload = json.loads((root / "tests/fixtures/f11/cost-inputs.json").read_text())
    regions = sorted({item.vm_a.region for item in campaign.observations} |
        {item.vm_b.region for item in campaign.observations})
    components = {}
    for name in ("compute", "disks", "addresses", "traffic_export", "tax", "uncertainty"):
        components[name] = {"amount_usd": "0.1", "quantity": "1", "unit": "unit",
            "inclusion_rule": "synthetic test input", "funding_bucket": "primary"}
    components["traffic_export"].update(quantity="1", unit="GB-egress")
    support_input = root / "tests/fixtures/aws/cost-inputs/aws-ssm-automation-public-2026-10-22.json"
    support_fixture = json.loads(support_input.read_text())
    support_amount = support_fixture["worst_planned_cost_per_region"]
    components["expiry_cleanup_support"] = {"amount_usd": str(support_amount), "quantity": "1",
        "unit": "region-session", "inclusion_rule": "synthetic test input", "funding_bucket": "primary"}
    envelope = {"envelope_id": "post-create-fit-envelope", "provider": "aws",
        "run_id": "post-create-fit-aws", "priced_on": "2026-10-22",
        "source_kind": "provider-price-input", "regions": regions,
        "unit_basis": "one-two-vm-provider-run", "components": components,
        "result_transfer": {"payload_limit_bytes": 900_000_000,
            "protocol_overhead_allowance_bytes": 100_000_000,
            "protocol_overhead_priced_usd": "0.001"}}
    cost_payload.update(cost_input_id=campaign.admission.cost_input_id,
        prices_checked_on="2026-10-22", acquired_on="2026-10-22",
        refresh_required_before_paid_execution=False,
        funding={"primary": "0.6139", "replacements": "0", "tax": "0", "uncertainty": "0"},
        provider_run_envelopes={"aws": envelope})
    cost_path = tmp_path / "cost-inputs.json"
    cost_path.write_text(json.dumps(cost_payload, sort_keys=True))
    costs = load_cost_inputs(cost_path)

    class AdvancingClock:
        def __init__(self):
            from datetime import datetime, timezone
            self.utc = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
            self.elapsed = 100.0
        def utc_now(self): return self.utc
        def monotonic(self): return self.elapsed
        def tick(self, seconds):
            from datetime import timedelta
            self.elapsed += seconds
            self.utc += timedelta(seconds=seconds)

    clock = AdvancingClock()

    class RecordingLedger(BudgetLedger):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.stage_checks = []
        def check_stage_fit(self, reservation_id, *, stage, remaining):
            self.stage_checks.append((stage, remaining))
            return super().check_stage_fit(reservation_id, stage=stage, remaining=remaining)

    ledger = RecordingLedger(tmp_path / "ledger.json", scope_id="post-create-fit",
        utc_now=clock.utc_now, monotonic_now=clock.monotonic, controller_session_id="session")
    ledger.initialize(costs)
    children = {"aws": "post-create-fit-aws"}
    funding = {"primary": Decimal("0.6139")}
    envelope_binding = {"aws": {**envelope, "source_path": costs.source_path,
        "source_sha256": costs.payload_sha256}}
    provider_support = {"aws": aws_expiry_cost_handoff(costs, support_input, regions,
        reservation_sha256=costs.payload_sha256, config_id=campaign.config_id)}
    gates = {}
    for name in ("f10_offline", "paid_authorization", "quota_review"):
        source = tmp_path / f"{name}.txt"
        source.write_text(f"synthetic {name}\n")
        gates[name] = {"evidence_id": f"synthetic-{name}", "source_path": str(source),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    plan = AdmissionPlan(campaign, children, {"aws": "post-create-fit-namespace"}, costs,
        allowance, execution_scope="paid_request", funding=funding,
        prerequisites=gates, provider_support=provider_support, cost_envelopes=envelope_binding)
    request = ReservationRequest(schema_version=2, campaign_id="post-create-fit",
        config_id=campaign.config_id,
        schedule_entry_id=Evidence[str](unavailable_reason="unscheduled acceptance fixture"),
        selected_providers=[Provider.AWS], mode=campaign.mode, priority=campaign.admission.priority,
        lifetime_cap_seconds=1200, estimated_cost_usd=Evidence[float](value=.6139),
        source_path=costs.source_path, source_sha256=costs.payload_sha256,
        incurred_cost_usd=Evidence[float](value=0), active_reserved_usd=Evidence[float](value=0),
        outstanding_cleanup=False)
    trace = Trace()

    def outcome(stage, *, advance=0, succeeded=True):
        def call():
            trace.add("aws", stage)
            clock.tick(advance)
            return ProviderStageOutcome(stage, succeeded, {"synthetic": True})
        return call

    provider_hooks = ProviderHooks("aws",
        protect=outcome("protect", advance=protect_seconds),
        claim=outcome("claim"),
        create=outcome("create", advance=create_seconds, succeeded=create_succeeds),
        prepare=outcome("prepare"), stabilize=outcome("stabilize", advance=60), measure=outcome("measure"),
        close=outcome("close"), retrieve=outcome("retrieve"), cleanup=outcome("cleanup"))

    def recovery_factory(evidence, _workflows):
        return SavedLifecycleRecovery(schema_version=1, attempt_id="post-create-fit-attempt",
            controller_session_id="session", reservation_id=evidence.reservation_id.value,
            providers=[{"provider": "aws", "deployment_id": "post-create-fit-deployment",
                "journal_reference": str(tmp_path / "aws-journal.json"), "expiry_registered": True,
                "creation_may_have_started": False, "cleanup_open": True}])

    result = run_lifecycle(ledger=ledger, reservation_request=request, admission_plan=plan,
        allowance=allowance, workflow_factory=lambda _evidence: {"aws": provider_hooks},
        clock=clock, recovery_path=tmp_path / "private" / "recovery.json",
        recovery_factory=recovery_factory)
    provider = result.provider_results["aws"]
    stages = [stage for _, stage in trace.events]
    admitted = [stage for stage, _remaining in ledger.stage_checks]

    assert "cleanup" in stages
    if protect_seconds > 105:
        assert "create" not in stages and "post_provision" not in admitted
        assert provider.cleanup_state == "succeeded"
    elif not create_succeeds:
        assert "create" in stages and "prepare" not in stages
        assert "post_provision" not in admitted
        assert provider.creation_may_have_started
    elif create_seconds == 189:
        assert "create" in stages and "prepare" not in stages
        assert "post_provision" in admitted
        assert not result.completed
    else:
        assert result.completed
        assert "prepare" in stages
        assert [remaining.total_seconds for stage, remaining in ledger.stage_checks
            if stage == "post_provision"] == [550]
        assert [remaining.total_seconds for stage, remaining in ledger.stage_checks
            if stage == "stabilization"] == [340]
        assert [remaining.total_seconds for stage, remaining in ledger.stage_checks
            if stage == "preparation"] == [730, 730]
    assert ("prepare" in stages) is prepare_expected


def test_projection_locates_stage_rejected_before_typed_outcome_and_keeps_cleanup_order():
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    failure_finished = datetime(2026, 11, 15, 12, 23, 37, tzinfo=timezone.utc)
    cleanup_started = failure_finished + timedelta(microseconds=1)
    cleanup_finished = cleanup_started + timedelta(minutes=2)
    result = SimpleNamespace(primary_failure="aws claim failed (TimeoutError)", values={}, stage_times={
        "claim": {"started_at": failure_finished - timedelta(seconds=1), "finished_at": failure_finished},
        "cleanup": {"started_at": cleanup_started, "finished_at": cleanup_finished},
    })

    stage = _failed_stage(result, result.stage_times)
    assert stage == "claim"
    assert result.stage_times[stage]["finished_at"] < result.stage_times["cleanup"]["started_at"]


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
