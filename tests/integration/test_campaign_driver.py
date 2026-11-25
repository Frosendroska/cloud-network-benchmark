"""Credential-free campaign scheduling and failure tests.

The launch boundary is fake; budget ownership, durable journal and scheduler are real.
These cases do not establish provider acceptance or Linux service readiness.
"""
from __future__ import annotations

import json
import hashlib
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cloud_network_benchmark.campaign import CampaignDriver


ROOT = Path(__file__).parents[2]
START = datetime(2026, 11, 1, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self, current=START):
        self.current = current
        self.sleeps = []
        self.lock = threading.Lock()

    def utc_now(self):
        with self.lock:
            return self.current

    def monotonic(self):
        return (self.utc_now() - START).total_seconds()

    def sleep(self, seconds):
        assert seconds >= 0
        with self.lock:
            self.sleeps.append(seconds)
            self.current += timedelta(seconds=seconds)


def stage_fixture(tmp_path, *, slots=28):
    (tmp_path / "results").mkdir(exist_ok=True)
    entries = []
    approval_source = tmp_path / "weekly-concurrency-source.json"
    approval_source.write_text('{"fixture":"SYNTHETIC OFFLINE TEST ONLY"}\n')
    evidence = tmp_path / "weekly-concurrency-evidence.json"
    evidence.write_text(json.dumps({"mode": "paired_s1_s3", "max_parallel_entries": 2,
        "source_path": str(approval_source), "source_sha256": hashlib.sha256(approval_source.read_bytes()).hexdigest()}) + "\n")
    for index in range(slots):
        slot = START + timedelta(hours=6 * index)
        for priority, scenario in (("P0", "S1"), ("P1", "S3")):
            eid = f"{priority}-{index:02}"
            entries.append({
                "entry_id": eid, "scenario": scenario, "priority": priority,
                "config_path": str(ROOT / "configs/tests/exp-903-three-provider-smoke.yaml"),
                "schedule_path": str(tmp_path / "schedule.csv"), "row_id": eid,
                "scheduled_start": (slot + timedelta(seconds=120)).isoformat(),
                "launch_at": slot.isoformat(),
                "runtime_config_path": str(tmp_path / "private" / eid / "runtime.json"),
                "cost_inputs_path": str(ROOT / "tests/fixtures/f11/cost-inputs.json"),
                "parent_manifest_path": str(tmp_path / "results" / "manifests" / f"{eid}.json"),
                "run_ids": {p: f"{eid}-{p}" for p in ("aws", "azure", "gcp")},
                "private_root": str(tmp_path / "private" / eid),
            })
    return {"stage_id": "offline-week", "stage": "week", "controller_session_id": "stable-session",
            "dispatch_policy": {"policy_id": "weekly-paired-s1-s3-v1", "group_by": "launch_at",
                "scenarios": ["S1", "S3"], "priorities": ["P0", "P1"], "max_parallel_entries": 2,
                "evidence_path": str(evidence), "evidence_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()},
            "ledger_path": str(tmp_path / "ledger.json"), "scope_id": "offline-week-scope",
            "state_root": str(tmp_path / "state"), "results_root": str(tmp_path / "results"),
            "entries": entries, "minimum_free_bytes": 1024}


def driver(clock, launch, **kwargs):
    return CampaignDriver(prepare=lambda *a, **k: None,
                          validate=lambda *a, **k: [],
                          launch=launch, clock=clock, sleep=clock.sleep, **kwargs)


def journal(stage):
    return json.loads((Path(stage["state_root"]) / stage["stage_id"] / "campaign-journal.json").read_text())


@pytest.mark.parametrize("first_failure", [False, True])
def test_full_week_runs_56_observations_with_two_calls_per_slot_and_shared_budget(tmp_path, first_failure):
    from cloud_network_benchmark.admission import BudgetLedger, load_cost_inputs
    from cloud_network_benchmark.config import resolve_campaign
    from cloud_network_benchmark.contracts.common import Evidence, Provider
    from tests.integration.test_f11_ledger import scheduled_request_and_plan, record_absence

    clock = FakeClock()
    stage = stage_fixture(tmp_path)
    book = BudgetLedger(Path(stage["ledger_path"]), scope_id=stage["scope_id"],
                        utc_now=clock.utc_now, monotonic_now=clock.monotonic,
                        controller_session_id=stage["controller_session_id"])
    costs = load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json")
    book.initialize(costs)
    campaign, _ = resolve_campaign(ROOT / "configs/tests/exp-903-three-provider-smoke.yaml", ROOT)
    # A barrier times out if the driver accidentally serializes P0 and P1.
    together = threading.Barrier(2, timeout=5)
    launched = []
    active_peaks = []
    lock = threading.Lock()

    def launch(entry, prepared):
        assert prepared["controller_session_id"] == book.controller_session_id
        request, plan = scheduled_request_and_plan(
            campaign, costs, campaign_id=entry["entry_id"],
            providers=[Provider.AWS, Provider.AZURE, Provider.GCP],
            child_prefix=entry["entry_id"], namespace_prefix=entry["entry_id"], funding={"primary": ".5"},
            measurement_seconds=76,
        )
        # Each admission reads the authoritative shared ledger inside its lock.
        # Caller evidence may explicitly be unavailable while another call reserves.
        request = request.model_copy(update={
            "incurred_cost_usd": Evidence[float](unavailable_reason="concurrent admission uses ledger"),
            "active_reserved_usd": Evidence[float](unavailable_reason="concurrent admission uses ledger"),
        })
        decision = book.reserve(request, plan=plan)
        assert decision.status == "reserved", decision.reasons
        together.wait()
        active = sum(bool(rec.get("cleanup_open")) for rec in book.inspect()["reservations"].values())
        with lock:
            active_peaks.append(active)
            launched.append(entry["entry_id"])
        together.wait()
        rid = decision.evidence.reservation_id.value
        for provider in (Provider.AWS, Provider.AZURE, Provider.GCP):
            record_absence(book, f"{entry['entry_id']}-{provider.value}", rid, provider, clock.utc_now())
        return {"status": "failed" if first_failure and entry["entry_id"] == "P0-00" else "complete",
                "cleanup_open": False}

    driver(clock, launch).run(stage)
    saved = journal(stage)
    expected = {"complete": 55, "failed": 1} if first_failure else {"complete": 56}
    assert Counter(item["state"] for item in saved["entries"].values()) == expected
    assert Counter(e.split("-")[0] for e in launched) == {"P0": 28, "P1": 28}
    assert len(launched) == len(set(launched)) == 56
    assert max(active_peaks) == 2
    assert len(book.inspect()["reservations"]) == 56
    assert not book.inspect()["cleanup_blocked"]
    assert len({e["private_root"] for e in stage["entries"]}) == 56
    assert len({run for e in stage["entries"] for run in e["run_ids"].values()}) == 168
    assert clock.utc_now() == START + timedelta(hours=6 * 27)
    # Replay after the service restarts cannot launch a second paid observation.
    driver(clock, lambda *_: pytest.fail("completed observation relaunched")).run(stage)


def acceptance_stage_fixture(tmp_path):
    stage = stage_fixture(tmp_path, slots=1)
    stage["stage"] = "acceptance"
    pair = stage["entries"]
    pair[0].update(entry_id="V-S1", scenario="S1", requires_supervision=False)
    pair[1].update(entry_id="V-S3", scenario="S3", requires_supervision=False)
    pair[0]["priority"], pair[1]["priority"] = "P0", "P1"
    for entry_id in ("V-A-FAIL", "V-Z-FAIL", "V-G-FAIL"):
        pair.append({**pair[0], "entry_id": entry_id, "scenario": None,
                     "requires_supervision": True, "private_root": str(tmp_path / entry_id),
                     "run_ids": {p: f"{entry_id}-{p}" for p in ("aws", "azure", "gcp")}})
    approval_source = tmp_path / "acceptance-concurrency-source.json"
    approval_source.write_text('{"fixture":"SYNTHETIC OFFLINE TEST ONLY"}\n')
    evidence = tmp_path / "acceptance-concurrency-evidence.json"
    fixed_start = pair[0]["scheduled_start"]
    evidence.write_text(json.dumps({"mode": "paired_acceptance_s1_s3", "max_parallel_entries": 2,
        "start_utc": fixed_start,
        "source_path": str(approval_source),
        "source_sha256": hashlib.sha256(approval_source.read_bytes()).hexdigest()}) + "\n")
    stage["dispatch_policy"] = {"policy_id": "acceptance-paired-s1-s3-v1", "group_by": "acceptance_slot",
        "scenarios": ["S1", "S3"], "max_parallel_entries": 2, "start_utc": fixed_start,
        "evidence_path": str(evidence), "evidence_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}
    stage["post_measurement_barrier"] = {"schema_version": 1,
        "barrier_id": f"{stage['stage_id']}-normal-measurement-terminal",
        "barrier_root": str(Path(stage["state_root"]) / stage["stage_id"] / "measurement-barriers" / "normal-terminal"),
        "expected_participants": [
            {"entry_id": entry["entry_id"], "provider": provider, "run_id": run_id}
            for entry in pair[:2] for provider, run_id in entry["run_ids"].items()]}
    stage["measurement_start_barrier"] = {"schema_version": 1,
        "barrier_id": f"{stage['stage_id']}-normal-measurement-start",
        "barrier_root": str(Path(stage["state_root"]) / stage["stage_id"] / "measurement-barriers" / "normal-start"),
        "start_utc": pair[0]["scheduled_start"],
        "expected_participants": stage["post_measurement_barrier"]["expected_participants"]}
    return stage


def ready_acceptance_stage_fixture(tmp_path):
    stage = acceptance_stage_fixture(tmp_path)
    pair = stage["entries"][:2]
    lower = START
    dispatch = {"not_before_utc": lower.isoformat(),
        "not_after_utc": (lower + timedelta(minutes=15)).isoformat()}
    execution = {"not_before_utc": lower.isoformat(),
        "not_after_utc": (lower + timedelta(hours=1)).isoformat()}
    source = tmp_path / "ready-admission-source.json"
    source.write_text('{"fixture":"SYNTHETIC OFFLINE TEST ONLY"}\n')
    evidence = Path(stage["dispatch_policy"]["evidence_path"])
    evidence.write_text(json.dumps({"mode": "paired_acceptance_s1_s3", "max_parallel_entries": 2,
        "execution_window_utc": execution, "dispatch_window_utc": dispatch,
        "source_path": str(source), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}) + "\n")
    stage["dispatch_policy"] = {"policy_id": "acceptance-paired-s1-s3-ready-v1",
        "group_by": "acceptance_slot", "scenarios": ["S1", "S3"], "max_parallel_entries": 2,
        "start_policy": "all_ready", "execution_window": execution, "dispatch_window_utc": dispatch,
        "ready_window_seconds": 600, "evidence_path": str(evidence),
        "evidence_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}
    for entry in pair:
        entry["scheduled_start"] = None
        entry["launch_at"] = lower.isoformat()
    participants = stage["post_measurement_barrier"]["expected_participants"]
    stage["dispatch_window_utc"] = dispatch
    stage["measurement_start_barrier"] = {"schema_version": 1,
        "barrier_id": f"{stage['stage_id']}-normal-measurement-start",
        "barrier_root": str(Path(stage["state_root"]) / stage["stage_id"] / "measurement-barriers" / "normal-start"),
        "expected_participants": participants, "start_policy": "all_ready",
        "execution_window": execution, "dispatch_window_utc": dispatch, "ready_window_seconds": 600}
    return stage


def test_acceptance_s1_s3_entries_run_as_one_bounded_parallel_slot(tmp_path):
    stage = acceptance_stage_fixture(tmp_path)
    active = 0
    peak = 0
    lock = threading.Lock()

    def launch(entry, _):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        threading.Event().wait(0.01)
        with lock:
            active -= 1
        return {"status": "complete", "cleanup_open": False}

    driver(FakeClock(), launch).run(stage)
    assert peak == 2
    saved = journal(stage)
    assert saved["entries"]["V-A-FAIL"]["state"] == "scheduled"
    assert saved["entries"]["V-Z-FAIL"]["state"] == "scheduled"
    assert saved["entries"]["V-G-FAIL"]["state"] == "scheduled"


def test_acceptance_rejects_incomplete_pair_before_launch(tmp_path):
    stage = acceptance_stage_fixture(tmp_path)
    stage["entries"].pop(1)
    launched = []
    with pytest.raises(ValueError, match="five registered"):
        driver(FakeClock(), lambda entry, _: launched.append(entry["entry_id"]) or {"status": "complete"}).run(stage)
    assert launched == []


def test_acceptance_rejects_changed_pair_admission_before_launch(tmp_path):
    stage = acceptance_stage_fixture(tmp_path)
    Path(stage["dispatch_policy"]["evidence_path"]).write_text("{}\n")
    launched = []
    with pytest.raises(ValueError, match="evidence is missing or changed"):
        driver(FakeClock(), lambda entry, _: launched.append(entry["entry_id"]) or {"status": "complete"}).run(stage)
    assert launched == []


def test_expired_ready_dispatch_window_blocks_admission_and_provider_launch(tmp_path):
    stage = ready_acceptance_stage_fixture(tmp_path)
    dispatch_calls = []
    admission_calls = []
    clock = FakeClock(START + timedelta(minutes=15, seconds=1))
    result = CampaignDriver(prepare=lambda *a, **k: None, validate=lambda *a, **k: [],
        launch=lambda entry, _: dispatch_calls.append(entry["entry_id"]) or {"status": "complete"},
        pre_admit=lambda *_: admission_calls.append("admit"), clock=clock, sleep=clock.sleep).run(stage)
    assert dispatch_calls == [] and admission_calls == []
    assert all(result["entries"][entry_id]["state"] == "missed" for entry_id in ("V-S1", "V-S3"))


def test_production_launch_rechecks_dispatch_after_log_open_before_popen(tmp_path, monkeypatch):
    import cloud_network_benchmark.campaign as campaign_module

    stage = ready_acceptance_stage_fixture(tmp_path)
    lower, upper = campaign_module._stage_dispatch_window(stage)
    now = [lower + timedelta(seconds=1)]
    popen_calls = []
    opened_logs = []
    real_open = Path.open

    class ControlledDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now[0].astimezone(tz) if tz else now[0].replace(tzinfo=None)

    def crossing_open(path, *args, **kwargs):
        result = real_open(path, *args, **kwargs)
        if path.name == "V-S1.log":
            opened_logs.append(result)
            now[0] = upper + timedelta(seconds=1)
        return result

    monkeypatch.setattr(campaign_module, "datetime", ControlledDateTime)
    monkeypatch.setattr(Path, "open", crossing_open)
    monkeypatch.setattr(campaign_module.subprocess, "Popen",
        lambda *args, **kwargs: popen_calls.append(args) or None)

    with pytest.raises(campaign_module._DispatchWindowExpired, match="immediately before lifecycle process start"):
        campaign_module.CampaignDriver._launch_lifecycle(stage["entries"][0], stage)

    assert popen_calls == []
    assert len(opened_logs) == 1 and opened_logs[0].closed
    assert Path(stage["state_root"], stage["stage_id"], "V-S1.log").stat().st_size == 0


def test_acceptance_does_not_shift_fixed_start_after_aggregate_admission(tmp_path):
    stage = acceptance_stage_fixture(tmp_path)
    launched = []
    clock = FakeClock()

    def pre_admit(_entries, _stage):
        clock.current = datetime.fromisoformat(stage["measurement_start_barrier"]["start_utc"].replace("Z", "+00:00"))

    result = CampaignDriver(prepare=lambda *a, **k: None, validate=lambda *a, **k: [],
        launch=lambda entry, _: launched.append(entry["entry_id"]) or {"status": "complete"},
        pre_admit=pre_admit, clock=clock, sleep=clock.sleep).run(stage)
    assert launched == []
    assert all(result["entries"][entry_id]["state"] == "missed" for entry_id in ("V-S1", "V-S3"))


def test_supervised_continue_reuses_exact_completed_failure_without_launch(tmp_path):
    import yaml
    from cloud_network_benchmark.acceptance_review import _runtime_scope_sha256

    stage = acceptance_stage_fixture(tmp_path)
    entry = next(row for row in stage["entries"] if row["entry_id"] == "V-A-FAIL")
    entry["config_path"] = str(ROOT / "configs/tests/exp-903-three-provider-smoke.yaml")
    entry["config_sha256"] = hashlib.sha256(Path(entry["config_path"]).read_bytes()).hexdigest()
    entry["source_config_sha256"] = entry["config_sha256"]
    runtime = tmp_path / "V-A-FAIL-runtime.json"
    runtime.write_text('{"schema_version": 1, "ledger": {"scope_id": "reuse"}}\n')
    entry["runtime_config_path"] = str(runtime)
    entry["runtime_config_sha256"] = hashlib.sha256(runtime.read_bytes()).hexdigest()
    parent = tmp_path / "V-A-FAIL-parent.json"
    attempt = "synthetic-reused-aws-attempt"
    commit = {"value": "a" * 40, "unavailable_reason": None}
    parent.write_text(json.dumps({"campaign_id": attempt, "implementation_git_commit": commit}))
    entry["parent_manifest_path"] = str(parent)
    evidence = {}
    for action in ("server_failure", "expiry", "cleanup", "ordinary_recovery"):
        source = tmp_path / f"reused-{action}.txt"
        source.write_text(f"SYNTHETIC OFFLINE TEST ONLY: {attempt}: {action}\n")
        evidence[action] = {"status": "passed", "source_path": str(source),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    waiver = tmp_path / "reused-waiver.txt"
    waiver.write_text("SYNTHETIC OFFLINE TEST ONLY: physical loss waived\n")
    evidence["controller_loss"] = {"status": "waived", "reason": "Explicitly waived.",
        "source_path": str(waiver), "source_sha256": hashlib.sha256(waiver.read_bytes()).hexdigest()}
    evidence["config_identity"] = {"config_path": entry["config_path"], "config_sha256": entry["config_sha256"],
        "source_config_sha256": entry["source_config_sha256"], "runtime_config_path": str(runtime),
        "runtime_config_sha256": entry["runtime_config_sha256"],
        "runtime_scope_sha256": _runtime_scope_sha256(runtime), "implementation_git_commit": commit}
    review = tmp_path / "reused-failure-review.yaml"
    review.write_text(yaml.safe_dump({"schema_version": 1,
        "checks": {"V-A-FAIL": {"provider": "aws", "attempt_id": attempt, **evidence}}}))
    entry["reused_failure_check"] = {"source_path": str(review),
        "source_sha256": hashlib.sha256(review.read_bytes()).hexdigest(), "attempt_id": attempt}
    folder = Path(stage["state_root"]) / stage["stage_id"]
    folder.mkdir(parents=True, exist_ok=True)
    prepared = folder / "prepared-stage.json"
    prepared.write_text(json.dumps(stage))
    engine = driver(FakeClock(), lambda *_: pytest.fail("reused failure attempted provider lifecycle"),
        dispatch=lambda *_: pytest.fail("reused failure dispatched supervisor"))
    journal_path = folder / "campaign-journal.json"
    journal = engine._load_or_create_journal(stage, journal_path)
    for entry_id in ("V-S1", "V-S3"):
        journal["entries"][entry_id].update(state="complete", result={"cleanup_open": False})
    journal_path.write_text(json.dumps(journal))
    result = engine.continue_supervised(Path(stage["state_root"]), stage["stage_id"], "V-A-FAIL", confirmed=True)
    assert result["status"] == "reused"
    saved = json.loads(journal_path.read_text())["entries"]["V-A-FAIL"]
    assert saved["state"] == "failed" and saved["result"]["measurement_reused"] is True
    assert saved["export_status"] == "verified"


def test_completed_acceptance_pair_restarts_without_remeasurement(tmp_path):
    stage = acceptance_stage_fixture(tmp_path)
    launched = []
    first = driver(FakeClock(), lambda entry, _: launched.append(entry["entry_id"]) or
        {"status": "complete", "cleanup_open": False})
    first.run(stage)
    assert set(launched) == {"V-S1", "V-S3"}
    restarted = driver(FakeClock(), lambda *_: pytest.fail("completed acceptance pair was measured twice"))
    restarted.run(stage)


@pytest.mark.parametrize("admission_case", ["within_window", "one_cent_under", "dispatch_expired_after_reserve"])
def test_acceptance_pre_admits_both_full_ledgers_before_any_launch(repository_root, tmp_path, monkeypatch, admission_case):
    import copy
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from cloud_network_benchmark.admission import BudgetLedger, load_cost_inputs
    from cloud_network_benchmark.config import resolve_campaign
    from cloud_network_benchmark.contracts.common import Evidence, Provider
    from tests.integration.test_f11_ledger import scheduled_request_and_plan

    stage = ready_acceptance_stage_fixture(tmp_path)
    stage["repository_root"] = str(repository_root)
    stage["scope_id"] = "offline-acceptance-pair"
    stage["controller_session_id"] = "offline-acceptance-controller"
    costs_path = ROOT / "tests/fixtures/f11/cost-inputs.json"
    clock = FakeClock()
    providers = [Provider.AWS, Provider.AZURE, Provider.GCP]
    for entry in stage["entries"][:2]:
        entry["runtime_config_path"] = str(tmp_path / f"{entry['entry_id']}-runtime.json")
        entry["parent_manifest_path"] = str(tmp_path / f"{entry['entry_id']}-parent.json")
        entry["cost_inputs_path"] = str(costs_path)

    costs = load_cost_inputs(costs_path)
    campaign, _ = resolve_campaign(ROOT / "configs/tests/exp-903-three-provider-smoke.yaml", ROOT)
    runtime = {"ledger": {"path": stage["ledger_path"], "scope_id": stage["scope_id"],
                          "initialize_if_missing": True}}

    def runtime_loader(_path, *, selected_providers=None):
        assert selected_providers == {"aws", "azure", "gcp"}
        return copy.deepcopy(runtime)

    initialized_ids = iter(("V-S1", "V-S3"))
    def initialized(_path, *, repository_root, config_path, schedule_path):
        assert schedule_path is None
        entry_id = next(initialized_ids)
        parent = SimpleNamespace(campaign_id=entry_id)
        return parent, {provider.value: SimpleNamespace(run_id=f"{entry_id}-{provider.value}") for provider in providers}

    def admission_inputs(*, campaign, parent, children, costs, runtime, ledger_state, controller_session_id):
        request, plan = scheduled_request_and_plan(campaign, costs, campaign_id=parent.campaign_id,
            providers=providers, child_prefix=parent.campaign_id, namespace_prefix=parent.campaign_id,
            funding={"primary": "50.20"}, measurement_seconds=76)
        request = request.model_copy(update={
            "incurred_cost_usd": Evidence[float](unavailable_reason="ledger derives pair spend"),
            "active_reserved_usd": Evidence[float](unavailable_reason="ledger derives pair holds")})
        return request, plan

    import cloud_network_benchmark.lifecycle_runtime as lifecycle_runtime
    import cloud_network_benchmark.config as config_module
    monkeypatch.setattr(lifecycle_runtime, "load_runtime_config", runtime_loader)
    monkeypatch.setattr(lifecycle_runtime, "load_initialized_campaign", initialized)
    monkeypatch.setattr(lifecycle_runtime, "build_admission_request", admission_inputs)
    monkeypatch.setattr(config_module, "resolve_campaign", lambda *_: (campaign, None))

    book = BudgetLedger(Path(stage["ledger_path"]), scope_id=stage["scope_id"],
        utc_now=clock.utc_now, monotonic_now=clock.monotonic, controller_session_id=stage["controller_session_id"])
    book.initialize(costs)
    if admission_case == "one_cent_under":
        request, plan = scheduled_request_and_plan(campaign, costs, campaign_id="prior-one-cent",
            providers=providers, child_prefix="prior-one-cent", namespace_prefix="prior-one-cent",
            funding={"primary": "0.01"}, measurement_seconds=76)
        request = request.model_copy(update={
            "incurred_cost_usd": Evidence[float](unavailable_reason="ledger derives pair spend"),
            "active_reserved_usd": Evidence[float](unavailable_reason="ledger derives pair holds")})
        assert book.reserve(request, plan=plan).status == "reserved"

    launched = []
    import cloud_network_benchmark.campaign as campaign_module
    popen_calls = []
    monkeypatch.setattr(campaign_module.subprocess, "Popen",
        lambda *args, **kwargs: popen_calls.append(args) or None)
    engine = CampaignDriver(prepare=lambda *a, **k: None, validate=lambda *a, **k: [],
        launch=(campaign_module.CampaignDriver._launch_lifecycle if admission_case == "dispatch_expired_after_reserve"
            else lambda entry, _: launched.append(entry["entry_id"]) or {"status": "complete"}), clock=clock,
        sleep=clock.sleep)
    engine.pre_admit = engine._pre_admit_acceptance_pair
    if admission_case == "dispatch_expired_after_reserve":
        actual_pre_admit = engine.pre_admit
        def cross_dispatch_after_reserve(entries, prepared_stage):
            reservation_ids = actual_pre_admit(entries, prepared_stage)
            clock.current = datetime.fromisoformat(prepared_stage["dispatch_window_utc"]["not_after_utc"]) + timedelta(seconds=1)
            return reservation_ids
        engine.pre_admit = cross_dispatch_after_reserve
    if admission_case == "one_cent_under":
        result = engine.run(stage)
        assert launched == []
        assert len(book.inspect()["reservations"]) == 2  # prior + first paired liability; no VM call occurred
        assert all(result["entries"][entry_id]["state"] == "missed" for entry_id in ("V-S1", "V-S3"))
    elif admission_case == "dispatch_expired_after_reserve":
        result = engine.run(stage)
        ledger = book.inspect()
        assert launched == [] and popen_calls == []
        assert len(ledger["reservations"]) == 2
        assert not any(event_id.startswith("cancel-unused:") for event_id in ledger["events"])
        assert result["status"] == "awaiting_operator"
        assert result["cleanup_liabilities"] == 2
        assert len(result["retained_reservation_reconciliations"]) == 2
        status = engine.status(Path(stage["state_root"]), stage["stage_id"])[0]
        assert status["status"] == "awaiting_operator"
        assert status["cleanup_liabilities"] == 2
        assert len(status["retained_reservation_reconciliations"]) == 2
        for entry_id in ("V-S1", "V-S3"):
            record = result["entries"][entry_id]
            assert record["state"] == "missed"
            assert record["result"]["provider_calls"] == 0
            assert record["result"]["cleanup_open"] is True
            assert record["result"]["provider_cleanup_required"] is False
            assert record["result"]["reservation_status"] == "retained_reserved_unreconciled"
            assert set(record["result"]["pre_admission_reservation_ids"]) == set(ledger["reservations"])
            assert "retain their holds" in record["result"]["next_action"]
    else:
        result = engine.run(stage)
        reservations = book.inspect()["reservations"]
        assert len(reservations) == 2
        assert all(set(row["plan"]["providers"]) == {"aws", "azure", "gcp"} for row in reservations.values())
        assert len({tuple(sorted(row["child_identities"].items())) for row in reservations.values()}) == 2
        assert len({tuple(sorted(row["plan"]["namespaces"].items())) for row in reservations.values()}) == 2
        assert set(launched) == {"V-S1", "V-S3"}
        assert all(result["entries"][entry_id]["aggregate_reservation_ids"] for entry_id in ("V-S1", "V-S3"))


def test_missed_slots_are_recorded_and_never_backfilled(tmp_path):
    stage = stage_fixture(tmp_path, slots=2)
    clock = FakeClock(START + timedelta(hours=1))
    launched = []
    driver(clock, lambda entry, _: launched.append(entry["entry_id"]) or {"status": "complete", "cleanup_open": False}).run(stage)
    states = {key: value["state"] for key, value in journal(stage)["entries"].items()}
    assert states == {"P0-00": "missed", "P1-00": "missed", "P0-01": "complete", "P1-01": "complete"}
    assert set(launched) == {"P0-01", "P1-01"}


@pytest.mark.parametrize("uncertain_state", ["starting", "running"])
def test_restart_never_relaunches_an_ambiguous_observation(tmp_path, uncertain_state):
    stage = stage_fixture(tmp_path, slots=1)
    clock = FakeClock()
    driver(clock, lambda *_: {"status": "complete"}).run(stage)
    path = Path(stage["state_root"]) / stage["stage_id"] / "campaign-journal.json"
    saved = json.loads(path.read_text())
    saved["entries"]["P0-00"]["state"] = uncertain_state
    saved["entries"]["P0-00"]["result"] = None
    path.write_text(json.dumps(saved))
    driver(clock, lambda *_: pytest.fail("uncertain paid observation relaunched")).run(stage)
    assert journal(stage)["entries"]["P0-00"]["state"] != "complete"


def test_failed_cleanup_prevents_later_launches(tmp_path):
    stage = stage_fixture(tmp_path, slots=2)
    clock = FakeClock()
    called = []

    def launch(entry, _):
        called.append(entry["entry_id"])
        return {"status": "failed", "cleanup_open": True, "reason_codes": ["cleanup_unverified"]}

    driver(clock, launch).run(stage)
    assert set(called) <= {"P0-00", "P1-00"}
    assert all(journal(stage)["entries"][eid]["state"] != "complete" for eid in ("P0-01", "P1-01"))


def test_launch_failure_preserves_primary_failure_and_does_not_report_complete(tmp_path):
    stage = stage_fixture(tmp_path, slots=1)
    clock = FakeClock()

    def launch(*_):
        raise OSError("transfer checksum mismatch: preserve the partial bundle")

    driver(clock, launch).run(stage)
    saved = journal(stage)
    assert all(item["state"] == "failed" for item in saved["entries"].values())
    assert all("checksum mismatch" in str(item) for item in saved["entries"].values())


def test_low_disk_refuses_launch_and_records_the_reason(tmp_path, monkeypatch):
    import cloud_network_benchmark.campaign as campaign_module
    from collections import namedtuple

    stage = stage_fixture(tmp_path, slots=1)
    clock = FakeClock()
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(campaign_module.shutil, "disk_usage", lambda _: usage(100, 99, 1))
    driver(clock, lambda *_: pytest.fail("launch with insufficient result disk")).run(stage)
    saved = journal(stage)
    assert all(item["state"] != "complete" for item in saved["entries"].values())
    assert "disk" in json.dumps(saved).lower()


def test_check_and_start_without_execute_do_not_prepare_or_launch(tmp_path):
    plan = tmp_path / "acceptance.yaml"
    plan.write_text("stage: acceptance\nsettings: operator.yaml\n")
    settings = {"state_root": str(tmp_path / "private"), "results_root": str(tmp_path / "results")}
    clock = FakeClock()
    engine = CampaignDriver(
        prepare=lambda *_: pytest.fail("read-only command prepared launch state"),
        validate=lambda *_: [], settings_loader=lambda _: settings,
        launch=lambda *_: pytest.fail("read-only command launched"), clock=clock, sleep=clock.sleep)
    assert engine.check(plan, root=ROOT)["status"] == "ready"
    assert engine.start(plan, root=ROOT)["execution_requested"] is False
    assert not Path(settings["state_root"]).exists()
    assert not Path(settings["results_root"]).exists()


def test_missing_credential_blockers_are_actionable_and_prevent_preparation(tmp_path):
    plan = tmp_path / "acceptance.yaml"
    plan.write_text("stage: acceptance\nsettings: operator.yaml\n")
    engine = CampaignDriver(
        prepare=lambda *_: pytest.fail("missing credential evidence bypassed"),
        validate=lambda *_: ["aws identity evidence is missing", "GCP ADC refresh evidence is missing"],
        settings_loader=lambda _: {}, launch=lambda *_: pytest.fail("missing credentials launched"))
    report = engine.start(plan, root=ROOT, execute=True)
    assert report["status"] == "blocked"
    assert len(report["blockers"]) == 2


def test_second_driver_cannot_take_over_active_stage(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    stage = stage_fixture(tmp_path, slots=1)
    clock = FakeClock()
    entered = threading.Event()
    release = threading.Event()

    def launch(*_):
        entered.set()
        assert release.wait(timeout=5)
        return {"status": "complete"}

    with ThreadPoolExecutor(max_workers=1) as pool:
        active = pool.submit(driver(clock, launch).run, stage)
        try:
            assert entered.wait(timeout=5)
            with pytest.raises(RuntimeError, match="already active"):
                driver(clock, lambda *_: pytest.fail("duplicate controller launched")).run(stage)
        finally:
            release.set()
        active.result(timeout=5)


def test_restart_refuses_modified_frozen_configuration(tmp_path):
    stage = stage_fixture(tmp_path, slots=1)
    config = tmp_path / "frozen-config.yaml"
    config.write_text("identity: originally-reviewed\n")
    for entry in stage["entries"]:
        entry["config_path"] = str(config)
    clock = FakeClock()
    driver(clock, lambda *_: {"status": "complete"}).run(stage)
    config.write_text("identity: changed-after-preparation\n")
    with pytest.raises(ValueError, match="changed"):
        driver(clock, lambda *_: pytest.fail("changed configuration launched")).run(stage)


def test_restart_uses_reconciliation_callback_before_any_new_launch(tmp_path):
    stage = stage_fixture(tmp_path, slots=1)
    clock = FakeClock()
    driver(clock, lambda *_: {"status": "complete"}).run(stage)
    path = Path(stage["state_root"]) / stage["stage_id"] / "campaign-journal.json"
    saved = json.loads(path.read_text())
    saved["entries"]["P0-00"]["state"] = "running"
    path.write_text(json.dumps(saved))
    reconciled = []

    def reconcile(entry, prepared):
        reconciled.append((entry["entry_id"], entry["private_root"]))
        assert prepared["controller_session_id"] == stage["controller_session_id"]
        return "failed"

    driver(clock, lambda *_: pytest.fail("restarted a reconciled observation"), reconcile=reconcile).run(stage)
    assert reconciled == [("P0-00", stage["entries"][0]["private_root"])]
    assert journal(stage)["entries"]["P0-00"]["state"] == "failed"


@pytest.mark.parametrize("changed", ["controller_session_id", "launch_at", "private_root"])
def test_restart_rejects_changed_frozen_ownership_or_schedule(tmp_path, changed):
    import copy

    stage = stage_fixture(tmp_path, slots=1)
    clock = FakeClock()
    driver(clock, lambda *_: {"status": "complete"}).run(stage)
    modified = copy.deepcopy(stage)
    if changed == "controller_session_id":
        modified[changed] = "another-session"
    elif changed == "launch_at":
        modified["entries"][0][changed] = (START + timedelta(hours=1)).isoformat()
    else:
        modified["entries"][0][changed] = str(tmp_path / "wrong-private-state")
    expected_error = (r"WEEK slot .* must contain exactly one P0/S1 and one P1/S3 entry"
                      if changed == "launch_at" else r"changed|match|frozen")
    with pytest.raises(ValueError, match=expected_error):
        driver(clock, lambda *_: pytest.fail("altered frozen observation launched")).run(modified)


def test_lifecycle_success_json_is_parsed_as_multiline_and_missing_files_still_fail(tmp_path, monkeypatch):
    import subprocess

    stage = stage_fixture(tmp_path, slots=1)
    stage["repository_root"] = str(ROOT)

    class ClosedProcess:
        pid = 999999
        returncode = 0

        def __init__(self, command, **kwargs):
            assert "--execution-scope" in command and "paid_request" in command
            kwargs["stdout"].write(b'{\n  "status": "complete"\n}\n')
            kwargs["stdout"].flush()

        def poll(self):
            return 0

    monkeypatch.setattr(subprocess, "Popen", ClosedProcess)
    result = CampaignDriver._launch_lifecycle(stage["entries"][0], stage)
    assert result["lifecycle_status_complete"] is True
    assert result["status"] == "failed"
    assert result["validated_outputs_present"] is False


def test_lifecycle_records_sampled_disk_highwater_even_when_space_recovers_before_exit(tmp_path, monkeypatch):
    import cloud_network_benchmark.campaign as campaign_module
    from types import SimpleNamespace

    stage = stage_fixture(tmp_path, slots=1)
    stage["repository_root"] = str(ROOT)
    free_samples = iter([1000, 900, 200, 800])
    rss_samples = iter([10, 50])
    polls = iter([None, None, 0])
    original_read = Path.read_text

    class SampledProcess:
        pid = 999999
        returncode = 0

        def __init__(self, command, **kwargs):
            kwargs["stdout"].write(b'{"status":"complete","providers":{"aws":{"cleanup_state":"succeeded"}}}\n')
            kwargs["stdout"].flush()

        def poll(self):
            return next(polls)

    def read_process_status(path, *args, **kwargs):
        if str(path) == "/proc/999999/status":
            return f"VmRSS: {next(rss_samples)} kB\n"
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(campaign_module.subprocess, "Popen", SampledProcess)
    monkeypatch.setattr(campaign_module.shutil, "disk_usage", lambda _: SimpleNamespace(free=next(free_samples)))
    monkeypatch.setattr(campaign_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(Path, "read_text", read_process_status)
    result = CampaignDriver._launch_lifecycle(stage["entries"][0], stage)
    assert result["free_disk_bytes_start"] == 1000
    assert result["free_disk_bytes_end"] == 800
    assert result["minimum_free_disk_bytes_during_run"] == 200
    assert result["peak_observed_shared_filesystem_usage_delta_bytes"] == 800
    assert result["disk_sample_scope"] == "shared_results_filesystem"
    assert result["peak_process_rss_bytes"] == 50 * 1024


def test_week_measurement_failure_with_verified_cleanup_allows_later_slots(tmp_path):
    stage = stage_fixture(tmp_path, slots=2)
    clock = FakeClock()
    launched = []

    def launch(entry, _):
        launched.append(entry["entry_id"])
        return {"status": "failed" if entry["entry_id"] == "P0-00" else "complete", "cleanup_open": False}

    driver(clock, launch).run(stage)
    saved = journal(stage)
    assert set(launched) == {"P0-00", "P1-00", "P0-01", "P1-01"}
    assert saved["entries"]["P0-00"]["state"] == "failed"
    assert saved["entries"]["P1-00"]["state"] == "complete"
    assert all(saved["entries"][name]["state"] == "complete" for name in ("P0-01", "P1-01"))


def test_week_rejects_incomplete_or_duplicate_pair_before_launch(tmp_path):
    stage = stage_fixture(tmp_path, slots=1)
    stage["entries"][1]["scenario"] = "S1"
    called = []
    with pytest.raises(ValueError, match="exactly one P0/S1 and one P1/S3"):
        driver(FakeClock(), lambda entry, _: called.append(entry["entry_id"]) or {"status": "complete"}).run(stage)
    assert called == []


def test_week_rejects_changed_concurrency_evidence_before_launch(tmp_path):
    stage = stage_fixture(tmp_path, slots=1)
    Path(stage["dispatch_policy"]["evidence_path"]).write_text("{}\n")
    called = []
    with pytest.raises(ValueError, match="evidence is missing or changed"):
        driver(FakeClock(), lambda entry, _: called.append(entry["entry_id"]) or {"status": "complete"}).run(stage)
    assert called == []


def test_week_restart_does_not_launch_a_single_remaining_pair_member(tmp_path):
    stage = stage_fixture(tmp_path, slots=2)
    clock = FakeClock()
    engine = driver(clock, lambda *_: {"status": "complete"})
    path = Path(stage["state_root"]) / stage["stage_id"] / "campaign-journal.json"
    saved = engine._load_or_create_journal(stage, path)
    saved["entries"]["P1-00"]["state"] = "complete"
    saved["entries"]["P1-00"]["result"] = {"status": "complete", "cleanup_open": False}
    saved["entries"]["P1-00"]["finished_at"] = clock.utc_now().isoformat()
    path.write_text(json.dumps(saved))
    called = []
    result = driver(clock, lambda entry, _: called.append(entry["entry_id"]) or {"status": "complete", "cleanup_open": False}).run(stage)
    assert set(called) == {"P0-01", "P1-01"}
    assert result["entries"]["P0-00"]["state"] == "missed"
    assert result["entries"]["P1-00"]["state"] == "complete"
    assert all(result["entries"][name]["state"] == "complete" for name in ("P0-01", "P1-01"))


def test_sleeping_controller_that_wakes_after_the_slot_never_launches_backfill(tmp_path):
    stage = stage_fixture(tmp_path, slots=1)
    clock = FakeClock(START - timedelta(seconds=60))

    def oversleep(seconds):
        # Simulate a suspended/descheduled runner waking long after its slot.
        clock.sleep(seconds + 3600)

    engine = CampaignDriver(prepare=lambda *a, **k: None, validate=lambda *a, **k: [],
        launch=lambda *_: pytest.fail("paid measurement backfilled after overdue wakeup"),
        clock=clock, sleep=oversleep)
    engine.run(stage)
    assert {row["state"] for row in journal(stage)["entries"].values()} == {"missed"}


@pytest.mark.parametrize("proof", ["all_absent", "missing_provider", "absence_unproved",
                                    "missing_reconciled", "missing_cleanup"])
def test_restart_cleanup_needs_absence_proof_for_every_original_provider(tmp_path, monkeypatch, proof):
    import subprocess
    from types import SimpleNamespace

    stage = stage_fixture(tmp_path, slots=1)
    entry = stage["entries"][0]
    private = Path(entry["private_root"])
    private.mkdir(parents=True)
    recovery = private / "lifecycle-recovery.json"
    recovery.write_text("{}")
    (Path(stage["state_root"]) / stage["stage_id"]).mkdir(parents=True)
    providers = {name: {"cleanup": "succeeded", "reconciled": True, "errors": [],
        "launch_blocked": False, "cleanup_open": False, "absence_proved": True,
        "billable_resources_present": False} for name in entry["run_ids"]}
    if proof == "missing_provider":
        providers.pop("gcp")
    elif proof == "absence_unproved":
        providers["gcp"]["absence_proved"] = False
    elif proof == "missing_reconciled":
        providers["gcp"].pop("reconciled")
    elif proof == "missing_cleanup":
        providers["gcp"].pop("cleanup")

    def cleanup(command, **kwargs):
        assert command[command.index("--recovery") + 1] == str(recovery)
        kwargs["stdout"].write(json.dumps({"status": "complete", "cleanup": "complete",
            "execution": "not_resumed", "providers": providers}).encode())
        kwargs["stdout"].flush()
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", cleanup)
    result = CampaignDriver._reconcile_lifecycle(entry, stage)
    assert result["status"] == "failed"  # Recovery never relaunches or invents measurement success.
    assert result["cleanup_open"] is (proof != "all_absent")


@pytest.mark.parametrize("damage", [None, "native_corruption", "recovery_open", "wrong_report_run"])
def test_restart_recognizes_completed_native_evidence_and_contradictions_fall_through_cleanup(repository_root,
        tmp_path, frozen_clock, deterministic_token, fake_git_commits, request, monkeypatch, damage):
    from tests.integration.test_campaign_acceptance import _materialize_review
    from cloud_network_benchmark.lifecycle_bundle import seal_bundle
    from cloud_network_benchmark.orchestration import SavedLifecycleRecovery, SavedProviderRecovery
    from cloud_network_benchmark.contracts.validation import ValidatorInput, ValidationResult
    from cloud_network_benchmark.validation import validate_bundle
    import subprocess
    from types import SimpleNamespace

    _, _, raw = _materialize_review(repository_root, tmp_path, frozen_clock, deterministic_token,
        fake_git_commits, request)
    value = ValidatorInput.model_validate_json((raw / "validation/input.json").read_bytes())
    persisted = ValidationResult.model_validate_json((raw / "validation/result.json").read_bytes())
    # Save current replay after the fixture's final child status projection,
    # exactly as lifecycle finalization does before its immutable seal.
    result = validate_bundle(value, evaluated_at=persisted.evaluated_at)
    (raw / "validation/result.json").write_text(result.model_dump_json())
    seal_bundle(raw)
    provider = str(value.provider)
    entry = {"entry_id": "V-S1", "run_ids": {provider: value.identity.run_id},
             "private_root": str(tmp_path / "private-attempt"), "parent_manifest_path": value.campaign_manifest_path}
    private = Path(entry["private_root"])
    private.mkdir()
    journal = private / "provider-recovery.json"
    journal.write_text(json.dumps({"run_id": value.identity.run_id, "cleanup_state": "succeeded"}))
    recovery = SavedLifecycleRecovery(schema_version=1, attempt_id=value.identity.campaign_id,
        controller_session_id="stable-session", providers=[SavedProviderRecovery(provider=provider,
            deployment_id=value.identity.deployment_id, journal_reference=str(journal), expiry_registered=True,
            creation_may_have_started=True, cleanup_open=damage == "recovery_open")])
    (private / "lifecycle-recovery.json").write_text(recovery.model_dump_json())
    stage = {"stage_id": "completed-recovery", "stage": "acceptance", "controller_session_id": "stable-session",
        "state_root": str(tmp_path / "state"), "results_root": str(raw.parent.parent), "repository_root": str(repository_root)}
    state = Path(stage["state_root"]) / stage["stage_id"]
    state.mkdir(parents=True)
    report = {"status": "complete", "run_ids": entry["run_ids"],
        "providers": {provider: {"cleanup_state": "succeeded", "validation_status": {"data_validity": "valid"}}}}
    if damage == "wrong_report_run":
        report["run_ids"] = {provider: "another-run"}
    (state / "V-S1.log").write_text(json.dumps(report, indent=2))
    if damage == "native_corruption":
        next(raw.rglob("sockperf.csv")).write_bytes(b"corrupted native data")
    cleanup_calls = []

    def cleanup(command, **kwargs):
        cleanup_calls.append(command)
        kwargs["stdout"].write(json.dumps({"status": "complete", "cleanup": "complete",
            "execution": "not_resumed", "providers": {provider: {
                "cleanup": "succeeded", "reconciled": True, "errors": [], "launch_blocked": False,
                "cleanup_open": False, "absence_proved": True, "billable_resources_present": False}}}).encode())
        kwargs["stdout"].flush()
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", cleanup)
    recovered = CampaignDriver._reconcile_lifecycle(entry, stage)
    assert recovered["cleanup_open"] is False
    if damage is None:
        assert recovered["status"] == "complete" and recovered["reconciliation"] == "completed_validated"
        assert not cleanup_calls
    else:
        assert recovered["status"] == "failed"
        assert len(cleanup_calls) == 1


def supervised_stage_fixture(tmp_path, clock):
    stage = stage_fixture(tmp_path, slots=1)
    stage["stage"] = "acceptance"
    for entry, registered in zip(stage["entries"], ("V-A-FAIL", "V-Z-FAIL")):
        entry.update(entry_id=registered, scheduled_start=None, requires_supervision=True)
        config = tmp_path / f"{registered}-config.yaml"
        config.write_text("offline: frozen-config\n")
        entry["config_path"] = str(config)
        runtime = Path(entry["runtime_config_path"])
        runtime.parent.mkdir(parents=True, exist_ok=True)
        runtime.write_text('{"offline":"frozen-runtime"}')
    folder = Path(stage["state_root"]) / stage["stage_id"]
    engine = driver(clock, lambda *_: pytest.fail("setup must not launch"))
    engine._load_or_create_journal(stage, folder / "campaign-journal.json")
    return stage, folder


@pytest.mark.parametrize("entry_point", ["continue", "service", "supervised"])
@pytest.mark.parametrize("tampered", ["runtime", "config", "stage"])
def test_supervised_entry_points_enforce_frozen_stage_and_input_bytes(tmp_path, entry_point, tampered):
    clock = FakeClock()
    stage, folder = supervised_stage_fixture(tmp_path, clock)
    entry = stage["entries"][0]
    if tampered in {"runtime", "config"}:
        Path(entry["runtime_config_path"] if tampered == "runtime" else entry["config_path"]).write_text("tampered after freezing")
    else:
        stage["entries"][0]["private_root"] = str(tmp_path / "different-recovery")
        (folder / "prepared-stage.json").write_text(json.dumps(stage))
    engine = driver(clock, lambda *_: pytest.fail("tampered supervised launch"),
                    dispatch=lambda *_: pytest.fail("tampered supervised dispatch"))
    root = Path(stage["state_root"])
    (root / "active-stage.json").write_text(json.dumps({"stage_id": stage["stage_id"],
        "stage_path": str(folder / "prepared-stage.json"), "supervised_entry_id": entry["entry_id"]}))
    with pytest.raises(ValueError, match="changed|frozen|match"):
        if entry_point == "continue":
            engine.continue_supervised(root, stage["stage_id"], entry["entry_id"], confirmed=True)
        elif entry_point == "service":
            engine.run_active(root)
        else:
            engine._run_supervised(stage, entry["entry_id"])


def test_cleanup_pending_supervised_checks_resume_after_explicit_cleanup_reconciliation(tmp_path, monkeypatch):
    clock = FakeClock()
    stage, _ = supervised_stage_fixture(tmp_path, clock)
    first, second = stage["entries"]
    launched, reconciled, dispatched = [], [], []

    def launch(entry, _):
        launched.append(entry["entry_id"])
        return {"status": "failed", "cleanup_open": entry["entry_id"] == first["entry_id"]}

    def reconcile(entry, _):
        reconciled.append(entry["entry_id"])
        return {"status": "failed", "cleanup_open": False, "reconciliation": "explicit_offline_absence_proof"}

    def archive(_stage, _entry, record):
        record["export_status"] = "deferred_cleanup_open" if record["result"].get("cleanup_open") else "verified"

    monkeypatch.setattr(CampaignDriver, "_archive_closed_entry", staticmethod(archive))
    engine = driver(clock, launch, reconcile=reconcile, dispatch=lambda _: dispatched.append(True) or {"started": True})
    engine._run_supervised(stage, first["entry_id"])
    saved = journal(stage)
    assert saved["entries"][second["entry_id"]]["state"] == "scheduled"
    assert saved["entries"][second["entry_id"]]["blocked_reason"] == "cleanup_pending"
    engine.continue_supervised(Path(stage["state_root"]), stage["stage_id"], second["entry_id"], confirmed=True)
    assert reconciled == [first["entry_id"]] and dispatched == [True]
    assert journal(stage)["entries"][first["entry_id"]]["result"]["cleanup_open"] is False
    engine.run_active(Path(stage["state_root"]))
    assert launched == [first["entry_id"], second["entry_id"]]
    assert all(record["state"] == "failed" for record in journal(stage)["entries"].values())


def test_following_controller_log_survives_rotation_without_discarding_raw_evidence(tmp_path):
    current = tmp_path / "campaign.log"
    current.write_text("first controller line\n")
    sleeps = []

    def rotate(seconds):
        sleeps.append(seconds)
        current.rename(tmp_path / "campaign.log.1")
        current.write_text("second controller line\n")

    following = CampaignDriver.logs(current, follow=True, sleep=rotate)
    assert next(following) == "first controller line\n"
    assert next(following) == "second controller line\n"
    following.close()
    assert sleeps == [1]
    assert (tmp_path / "campaign.log.1").read_text() == "first controller line\n"


@pytest.mark.parametrize("first_state", ["complete", "running"])
@pytest.mark.parametrize("matching_summary", [True, False])
def test_status_projects_next_slot_and_recorded_export_backup_notification_results(tmp_path, matching_summary, first_state):
    clock = FakeClock()
    stage = stage_fixture(tmp_path, slots=1)
    engine = driver(clock, lambda *_: pytest.fail("status must not launch"))
    folder = Path(stage["state_root"]) / stage["stage_id"]
    saved = engine._load_or_create_journal(stage, folder / "campaign-journal.json")
    first, second = stage["entries"]
    saved["entries"][first["entry_id"]].update(state=first_state, export_status="verified",
        backup_status="verified", notification_status="failed: offline notification test")
    (folder / "campaign-journal.json").write_text(json.dumps(saved))
    summary = {"stage_id": stage["stage_id"] if matching_summary else "unrelated-stage",
               "export_status": "verified", "notification_status": "sent"}
    (Path(stage["results_root"]) / "stage-summary.json").write_text(json.dumps(summary))
    status = engine.status(Path(stage["state_root"]), stage["stage_id"])[0]
    expected_entry = first if first_state == "running" else second
    assert status["current_or_next_entry"] == expected_entry["entry_id"]
    assert status["next_slot"] == expected_entry["scheduled_start"]
    assert status["counts"][first_state] == 1 and status["counts"]["expected"] == 2
    assert status["exports"][first["entry_id"]] == "verified"
    assert status["backups"][first["entry_id"]] == "verified"
    assert status["notifications"][first["entry_id"]].startswith("failed:")
    if matching_summary:
        assert status["stage_summary"] == summary and status["exports"]["stage"] == "verified"
    else:
        assert not status["stage_summary"] and "stage" not in status["exports"]


def test_restart_blocks_all_later_slots_while_archive_validation_is_pending(tmp_path):
    clock = FakeClock()
    stage = stage_fixture(tmp_path, slots=1)
    stage_dir = Path(stage["state_root"]) / stage["stage_id"]
    stage_dir.mkdir(parents=True)
    launched = []
    engine = driver(clock, lambda entry, _: launched.append(entry["entry_id"]) or {"status": "complete"})
    path = stage_dir / "campaign-journal.json"
    saved = engine._load_or_create_journal(stage, path)
    first = stage["entries"][0]["entry_id"]
    saved["entries"][first].update(state="archive_pending_validation",
        finished_at=clock.utc_now().isoformat(), result={"cleanup_open": False,
            "replay_capsule": {"status": "archive_pending_replay"}})
    path.write_text(json.dumps(saved), encoding="utf-8")

    result = engine.run(stage)

    assert launched == []
    assert result["counts"]["archive_pending_validation"] == 1
    assert result["counts"]["scheduled"] == 1
    assert result["status"] == "awaiting_operator"


def test_archive_reconciliation_rejects_open_cleanup_and_handwritten_receipt(tmp_path):
    clock = FakeClock()
    stage = stage_fixture(tmp_path, slots=1)
    stage["repository_root"] = str(ROOT)
    stage_dir = Path(stage["state_root"]) / stage["stage_id"]
    stage_dir.mkdir(parents=True)
    engine = driver(clock, lambda *_: pytest.fail("reconciliation must not launch"))
    journal_path = stage_dir / "campaign-journal.json"
    saved = engine._load_or_create_journal(stage, journal_path)
    entry = stage["entries"][0]
    capsule_path = tmp_path / "capsule.json"
    capsule_doc = {"schema_version": 2, "status": "archive_pending_replay"}
    capsule_path.write_text(json.dumps(capsule_doc), encoding="utf-8")
    capsule_sha = hashlib.sha256(capsule_path.read_bytes()).hexdigest()
    receipt_path = tmp_path / "handwritten-receipt.json"
    receipt_path.write_text(json.dumps({"status": "replayed", "capsule_sha256": capsule_sha,
        "archives": {}, "validation": {}, "analysis": {"status": "completed"}}), encoding="utf-8")
    record = saved["entries"][entry["entry_id"]]
    record.update(state="archive_pending_validation", result={"cleanup_open": True,
        "run_ids": entry["run_ids"], "replay_capsule": {"status": "archive_pending_replay",
            "path": str(capsule_path), "sha256": capsule_sha}})
    journal_path.write_text(json.dumps(saved), encoding="utf-8")

    with pytest.raises(ValueError, match="closed cleanup"):
        engine.reconcile_archive(Path(stage["state_root"]), stage["stage_id"],
            entry["entry_id"], receipt_path)
    assert journal(stage)["entries"][entry["entry_id"]]["state"] == "archive_pending_validation"

    record["result"]["cleanup_open"] = False
    journal_path.write_text(json.dumps(saved), encoding="utf-8")
    with pytest.raises((ValueError, OSError)):
        engine.reconcile_archive(Path(stage["state_root"]), stage["stage_id"],
            entry["entry_id"], receipt_path)
    assert journal(stage)["entries"][entry["entry_id"]]["state"] == "archive_pending_validation"


def test_active_supervised_pointer_does_not_dispatch_archive_pending_entry(tmp_path):
    clock = FakeClock()
    stage = stage_fixture(tmp_path, slots=1)
    stage["stage"] = "acceptance"
    stage["entries"][0]["requires_supervision"] = True
    stage_dir = Path(stage["state_root"]) / stage["stage_id"]
    stage_dir.mkdir(parents=True)
    dispatched = []
    engine = driver(clock, lambda *_: pytest.fail("pending archive must not launch"),
        dispatch=lambda *_: dispatched.append(True) or {"started": True})
    path = stage_dir / "campaign-journal.json"
    saved = engine._load_or_create_journal(stage, path)
    entry_id = stage["entries"][0]["entry_id"]
    saved["entries"][entry_id].update(state="archive_pending_validation",
        result={"cleanup_open": False})
    path.write_text(json.dumps(saved), encoding="utf-8")
    pointer = Path(stage["state_root"]) / "active-stage.json"
    pointer.write_text(json.dumps({"stage_id": stage["stage_id"],
        "stage_path": str(stage_dir / "prepared-stage.json"), "supervised_entry_id": entry_id}), encoding="utf-8")

    result = engine.run_active(Path(stage["state_root"]))

    assert result["counts"]["archive_pending_validation"] == 1
    assert dispatched == []
