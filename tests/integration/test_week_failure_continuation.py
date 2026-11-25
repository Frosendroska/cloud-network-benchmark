"""Real WEEK scheduler/journal with synthetic lifecycle outcomes, never providers."""
import copy
import json
from datetime import timedelta
from pathlib import Path

import pytest

from tests.integration.test_campaign_driver import START, FakeClock, driver, journal, stage_fixture


def seed(stage, clock, records):
    path = Path(stage["state_root"]) / stage["stage_id"] / "campaign-journal.json"
    saved = driver(clock, lambda *_: pytest.fail("seed dispatched"))._load_or_create_journal(stage, path)
    for entry_id, fields in records.items():
        saved["entries"][entry_id].update(fields)
    path.write_text(json.dumps(saved))
    return copy.deepcopy(saved)


@pytest.mark.parametrize("prior", ["failed", "missed"])
def test_settled_restart_preserves_terminal_evidence_and_original_schedule(tmp_path, prior):
    stage = stage_fixture(tmp_path, slots=3)
    frozen = copy.deepcopy(stage)
    clock = FakeClock(START + timedelta(hours=1))
    old = seed(stage, clock, {eid: {
        "state": prior, "error": "permanent original failure",
        "finished_at": START.isoformat(),
        "result": {"status": prior, "cleanup_open": False},
    } for eid in ("P0-00", "P1-00")})
    calls = []

    def launch(entry, prepared):
        assert prepared == frozen
        assert clock.utc_now().isoformat() == entry["launch_at"]
        calls.append(entry["entry_id"])
        return {"status": "complete", "cleanup_open": False}

    result = driver(clock, launch).run(stage)
    # Synthetic lifecycles deliberately create no native export; final export
    # failure must not be confused with the scheduler's preserved entry outcomes.
    assert result["status"] != "complete"
    assert result["counts"][prior] == 2
    assert result["counts"]["complete"] == 4
    assert set(calls) == {"P0-01", "P1-01", "P0-02", "P1-02"}
    for eid in ("P0-00", "P1-00"):
        assert result["entries"][eid] == old["entries"][eid]
    assert stage == frozen
    driver(clock, lambda *_: pytest.fail("terminal entry retried")).run(stage)


@pytest.mark.parametrize("prior", [
    {"state": "failed", "result": {"cleanup_open": True}},
    {"state": "failed", "result": {}},
    {"state": "complete", "result": {}},
    {"state": "complete", "result": {"cleanup_open": None}},
    {"state": "complete", "result": {"cleanup_open": 0}},
    {"state": "failed", "result": {"cleanup_open": False, "launch_uncertain": True}},
    {"state": "archive_pending_validation", "result": {"cleanup_open": False}},
    {"state": "missed", "started_at": START.isoformat(), "result": {}},
])
def test_unsettled_prior_entry_preserves_future_pair_without_dispatch(tmp_path, prior):
    stage = stage_fixture(tmp_path, slots=2)
    clock = FakeClock(START + timedelta(hours=1))
    seed(stage, clock, {"P0-00": prior, "P1-00": {
        "state": "complete", "result": {"cleanup_open": False}}})
    result = driver(clock, lambda *_: pytest.fail("unsettled cleanup launched"),
                    reconcile=lambda *_: None).run(stage)
    assert result["status"] == "awaiting_operator"
    assert all(result["entries"][eid]["state"] == "scheduled" for eid in ("P0-01", "P1-01"))


def test_open_cleanup_then_restart_settles_without_retry_or_backfill(tmp_path):
    stage = stage_fixture(tmp_path, slots=3)
    clock = FakeClock()
    calls = []

    def fail(entry, _):
        calls.append(entry["entry_id"])
        if entry["entry_id"] == "P0-00":
            raise OSError("synthetic interrupted creation")
        return {"status": "complete", "cleanup_open": False}

    result = driver(clock, fail).run(stage)
    assert result["status"] == "awaiting_operator"
    assert set(calls) == {"P0-00", "P1-00"}
    original = copy.deepcopy(result["entries"]["P0-00"])
    # Cleanup is still open after slot 1 passes. An unproved readback cannot dispatch.
    clock.sleep(7 * 3600)
    driver(clock, lambda *_: pytest.fail("open cleanup launched"),
           reconcile=lambda *_: {"status": "failed", "cleanup_open": True}).run(stage)
    recovered = []

    def reconcile(entry, _):
        recovered.append(entry["entry_id"])
        return {"status": "failed", "cleanup_open": False, "reconciliation": "cleanup_complete"}

    def later(entry, _):
        assert recovered == ["P0-00"]
        assert clock.utc_now() == START + timedelta(hours=12)
        saved = journal(stage)
        assert saved["entries"]["P0-00"]["result"]["cleanup_open"] is False
        assert saved["entries"]["P0-00"]["state"] == "failed"
        calls.append(entry["entry_id"])
        return {"status": "complete", "cleanup_open": False}

    result = driver(clock, later, reconcile=reconcile).run(stage)
    assert set(calls) == {"P0-00", "P1-00", "P0-02", "P1-02"}
    assert len(calls) == len(set(calls))
    assert result["status"] == "failed"
    for field in ("state", "error", "started_at", "finished_at"):
        assert result["entries"]["P0-00"][field] == original[field]
    assert all(result["entries"][eid]["state"] == "missed" for eid in ("P0-01", "P1-01"))
    assert all(result["entries"][eid]["state"] == "complete" for eid in ("P0-02", "P1-02"))


def test_oversleep_misses_only_expired_pair(tmp_path):
    stage = stage_fixture(tmp_path, slots=2)
    clock = FakeClock(START - timedelta(seconds=60))
    calls = []
    first = True

    def sleep(seconds):
        nonlocal first
        clock.sleep(seconds + (3600 if first else 0))
        first = False

    engine = driver(clock, lambda entry, _: calls.append(entry["entry_id"]) or
                    {"status": "complete", "cleanup_open": False})
    engine.sleep = sleep
    result = engine.run(stage)
    assert set(calls) == {"P0-01", "P1-01"}
    assert all(result["entries"][eid]["state"] == "missed" for eid in ("P0-00", "P1-00"))


def test_previously_cancelled_future_slot_is_never_restored(tmp_path):
    stage = stage_fixture(tmp_path, slots=3)
    clock = FakeClock()
    old = seed(stage, clock, {eid: {"state": "missed", "error": "original fail-stop cancellation"}
                             for eid in ("P0-00", "P1-00", "P0-01", "P1-01")})
    calls = []
    result = driver(clock, lambda entry, _: calls.append(entry["entry_id"]) or
                    {"status": "complete", "cleanup_open": False}).run(stage)
    assert set(calls) == {"P0-02", "P1-02"}
    for eid in ("P0-00", "P1-00", "P0-01", "P1-01"):
        assert result["entries"][eid] == old["entries"][eid]
