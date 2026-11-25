"""Exercise WEEK replay retry allocation and durable cleanup-proof guards.

The callback interruption case mocks only runtime parsing and the already-settled
financial precondition so it isolates replay-attempt allocation. It creates a
task-temp payload but never a successful replay receipt or validation proof.
The direct ledger cases use the genuine AWS F09 fake-lifecycle fixture and real
typed BudgetLedger/recovery evidence; they do not claim a full native WEEK run.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cloud_network_benchmark import campaign as campaign_module
from cloud_network_benchmark.campaign import CampaignDriver
from cloud_network_benchmark.orchestration import SavedLifecycleRecovery
from tests.integration.test_campaign_driver import FakeClock, driver, journal, stage_fixture
from tests.integration.week_archive_fixture import build_single_provider_week_archive_fixture


ROOT = Path(__file__).parents[2]


def _synthetic_pending_ref(tmp_path: Path, entry_id: str) -> dict[str, str]:
    path = tmp_path / f"{entry_id}-synthetic-pending-capsule.json"
    path.write_text(json.dumps({"fixture": "projection-only", "status": "archive_pending_replay"}) + "\n")
    return {"status": "archive_pending_replay", "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def test_partial_replay_attempt_suppresses_peer_and_restart_even_when_filtered(tmp_path, monkeypatch):
    stage = stage_fixture(tmp_path, slots=1)
    stage["stage"] = "week"
    stage["repository_root"] = str(ROOT)
    stage["entries"] = list(reversed(stage["entries"]))
    peer_id = stage["entries"][1]["entry_id"]
    attempted_id = stage["entries"][0]["entry_id"]
    staging_root = tmp_path / "archive-staging"
    staging_root.mkdir()
    (tmp_path / "private").mkdir(exist_ok=True)

    # This narrow mock lets the test isolate replay-allocation recovery. It does
    # not bypass archive, native validation, cleanup or provider execution.
    import cloud_network_benchmark.lifecycle_runtime as lifecycle_runtime

    monkeypatch.setattr(lifecycle_runtime, "load_runtime_config", lambda _path: {
        "archive_sink": {"staging_directory": staging_root}})
    monkeypatch.setattr(campaign_module, "_week_ledger_settled", lambda *_args, **_kwargs: True)

    attempts: list[str] = []
    partial_payloads: list[Path] = []

    def launch(entry, _prepared):
        return {"status": "archive_pending_validation", "cleanup_open": False,
            "replay_capsule": _synthetic_pending_ref(tmp_path, entry["entry_id"])}

    def interrupted_restore(entry, held_stage, _record):
        attempts.append(entry["entry_id"])
        assert entry["entry_id"] == attempted_id
        lock_path = Path(held_stage["state_root"]) / ".campaign-driver.lock"
        with pytest.raises(RuntimeError, match="campaign driver is already active"):
            with campaign_module._file_lock(lock_path, blocking=False):
                pass
        task_temp = staging_root / "task-temp" / f"{entry['entry_id']}.partial"
        task_temp.parent.mkdir(parents=True, exist_ok=True)
        task_temp.write_bytes(b"synthetic partial restore payload; no receipt")
        partial_payloads.append(task_temp)
        raise RuntimeError("synthetic interrupted restore after task-temp write")

    clock = FakeClock()
    engine = driver(clock, launch, archive_replay=interrupted_restore)
    engine.run(stage)

    first_journal = journal(stage)
    assert attempts == [attempted_id]
    assert peer_id != attempted_id
    assert len(partial_payloads) == 1 and partial_payloads[0].read_bytes().startswith(b"synthetic partial")
    attempted = first_journal["entries"][attempted_id]
    peer = first_journal["entries"][peer_id]
    assert attempted["state"] == peer["state"] == "archive_pending_validation"
    assert attempted["result"]["archive_replay_attempted"] is True
    assert "archive_replay_error" in attempted["result"]
    assert peer["result"].get("archive_replay_attempted") is not True
    assert "prior replay quota allocation is unavailable" in peer["result"].get("archive_replay_error", "")
    for entry_id in (attempted_id, peer_id):
        digest = first_journal["entries"][entry_id]["result"]["replay_capsule"]["sha256"]
        receipt = staging_root / "week-replay" / f"{entry_id}-{digest[:16]}" / "replay-receipts" / f"{digest}.json"
        assert not receipt.exists()

    reconcile = CampaignDriver._reconcile_week_archives
    filtered_calls = []

    def exclude_already_attempted_peer(self, held_stage, held_journal, journal_path, *, entry_ids=None):
        selected = {peer_id}
        filtered_calls.append(selected)
        return reconcile(self, held_stage, held_journal, journal_path, entry_ids=selected)

    monkeypatch.setattr(CampaignDriver, "_reconcile_week_archives", exclude_already_attempted_peer)
    restarted = driver(clock, lambda *_: pytest.fail("archive-pending WEEK entry must not launch"),
        archive_replay=interrupted_restore)
    restarted.run(stage)

    assert filtered_calls == [{peer_id}]
    assert attempted_id not in filtered_calls[0]
    assert attempts == [attempted_id]
    assert len(list((staging_root / "task-temp").glob("*.partial"))) == 1
    second_journal = journal(stage)
    assert second_journal["entries"][attempted_id]["state"] == "archive_pending_validation"
    assert second_journal["entries"][peer_id]["state"] == "archive_pending_validation"
    assert second_journal["entries"][attempted_id]["result"]["archive_replay_attempted"] is True
    assert second_journal["entries"][peer_id]["result"].get("archive_replay_attempted") is not True


@pytest.fixture
def genuine_f09_pending(tmp_path, monkeypatch, repository_root):
    fixture = build_single_provider_week_archive_fixture(
        tmp_path=tmp_path, monkeypatch=monkeypatch, repository_root=repository_root)
    try:
        yield fixture
    finally:
        fixture.cleanup()


def _f09_ledger_args(fixture):
    provider = "aws"
    run_id = fixture.capsule["archives"][provider]["run_id"]
    entry = {"entry_id": "offline-aws-archive-entry", "run_ids": {provider: run_id},
        "parent_manifest_path": str(fixture.parent_path),
        "private_root": str(fixture.built["recovery_path"].parent)}
    stage = {"stage_id": "offline-aws-archive-stage", "stage": "week",
        "controller_session_id": "archive-sink-session",
        "ledger_path": str(fixture.built["ledger"].path),
        "scope_id": fixture.built["ledger"].scope_id}
    capsule_ref = {"path": str(fixture.capsule_path), "sha256": fixture.capsule_sha256}
    return fixture.runtime, capsule_ref, entry, stage


def test_genuine_f09_pending_fixture_has_typed_settled_ledger_proof(genuine_f09_pending):
    runtime, capsule_ref, entry, stage = _f09_ledger_args(genuine_f09_pending)

    assert campaign_module._week_ledger_settled(runtime, capsule_ref, entry, stage) is True


@pytest.mark.parametrize("damage", ["stage_ledger_path", "stage_scope", "missing_recovery",
    "provider_cleanup_open", "capsule_hash"])
def test_genuine_f09_pending_fixture_rejects_changed_or_unresolved_cleanup_proof(genuine_f09_pending, tmp_path, damage):
    runtime, capsule_ref, entry, stage = _f09_ledger_args(genuine_f09_pending)
    recovery_path = Path(entry["private_root"]) / "lifecycle-recovery.json"
    original_recovery = recovery_path.read_bytes()

    if damage == "stage_ledger_path":
        wrong_ledger = tmp_path / "other-ledger.json"
        wrong_ledger.write_text("{}\n", encoding="utf-8")
        stage["ledger_path"] = str(wrong_ledger)
    elif damage == "stage_scope":
        stage["scope_id"] = "different-scope"
    elif damage == "missing_recovery":
        recovery_path.unlink()
    elif damage == "provider_cleanup_open":
        recovery_doc = json.loads(original_recovery)
        recovery_doc["providers"][0]["cleanup_open"] = True
        typed_open_recovery = SavedLifecycleRecovery.validate_unique(recovery_doc)
        recovery_path.write_text(typed_open_recovery.model_dump_json(), encoding="utf-8")
    else:
        capsule_ref["sha256"] = "0" * 64

    assert campaign_module._week_ledger_settled(runtime, capsule_ref, entry, stage) is False
