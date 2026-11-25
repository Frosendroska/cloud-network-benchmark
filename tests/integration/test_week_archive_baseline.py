"""Characterize WEEK scheduler and archive-reconcile locking before the fix."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cloud_network_benchmark import campaign as campaign_module
from tests.integration.test_campaign_driver import FakeClock, driver, journal, stage_fixture


def test_archive_pending_week_stops_after_pair_and_preserves_future_slot_bindings(tmp_path):
    stage = stage_fixture(tmp_path)
    original = {
        entry["entry_id"]: {
            key: entry[key]
            for key in ("entry_id", "launch_at", "scheduled_start", "row_id", "run_ids", "private_root")
        }
        for entry in stage["entries"]
    }
    launched: list[str] = []

    def launch(entry, _prepared):
        launched.append(entry["entry_id"])
        status = "archive_pending_validation" if entry["entry_id"] == "P0-00" else "complete"
        return {"status": status, "cleanup_open": False,
                "replay_capsule": {"status": "archive_pending_replay"} if status.startswith("archive_") else None}

    result = driver(FakeClock(), launch).run(stage)

    assert set(launched) == {"P0-00", "P1-00"}
    assert result["counts"] == {
        "scheduled": 54,
        "starting": 0,
        "running": 0,
        "archive_pending_validation": 1,
        "complete": 1,
        "failed": 0,
        "missed": 0,
        "expected": 56,
    }
    saved = journal(stage)
    prepared = json.loads((Path(stage["state_root"]) / stage["stage_id"] / "prepared-stage.json").read_text())
    prepared_by_id = {entry["entry_id"]: entry for entry in prepared["entries"]}
    for entry in stage["entries"]:
        record = saved["entries"][entry["entry_id"]]
        if entry["entry_id"] in launched:
            continue
        assert record["state"] == "scheduled"
        assert record["blocked_reason"] == "cleanup_pending"
        assert record["launch_at"] == original[entry["entry_id"]]["launch_at"]
        assert record["scheduled_start"] == original[entry["entry_id"]]["scheduled_start"]
        assert {key: prepared_by_id[entry["entry_id"]][key] for key in original[entry["entry_id"]]} == original[entry["entry_id"]]


def test_external_archive_reconciliation_rejects_driver_lock_without_mutation(tmp_path):
    stage = stage_fixture(tmp_path, slots=1)
    clock = FakeClock()
    engine = driver(clock, lambda *_: pytest.fail("archive reconciliation must not launch"))
    journal_path = Path(stage["state_root"]) / stage["stage_id"] / "campaign-journal.json"
    saved = engine._load_or_create_journal(stage, journal_path)
    entry_id = stage["entries"][0]["entry_id"]
    saved["entries"][entry_id].update(
        state="archive_pending_validation",
        finished_at=clock.utc_now().isoformat(),
        result={"cleanup_open": False, "replay_capsule": {"status": "archive_pending_replay"}},
    )
    journal_path.write_text(json.dumps(saved), encoding="utf-8")
    before = journal_path.read_bytes()
    driver_lock = Path(stage["state_root"]) / ".campaign-driver.lock"

    with campaign_module._file_lock(driver_lock, blocking=False):
        with pytest.raises(RuntimeError, match="campaign driver is already active"):
            engine.reconcile_archive(
                Path(stage["state_root"]), stage["stage_id"], entry_id, tmp_path / "unused-receipt.json"
            )

    assert journal_path.read_bytes() == before
