"""Run all WEEK logical slots through the real driver, journal and F11 ledger.

Lifecycle calls are controlled fakes. The integrity-verified closed exports in
this test are synthetic transport fixtures, not archive replay or native
validation evidence. The separate F09 fixture smoke test covers the genuine
AWS-only archive-pending source boundary.
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections import Counter
from datetime import timedelta
from pathlib import Path

import pytest

from cloud_network_benchmark.admission import BudgetLedger, load_cost_inputs
from cloud_network_benchmark.campaign import CampaignDriver
from cloud_network_benchmark.campaign_export import verify_export
from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.contracts.common import Evidence, Provider
from tests.integration.test_campaign_driver import FakeClock, START, driver, journal, stage_fixture
from tests.integration.test_f11_ledger import record_absence, scheduled_request_and_plan


ROOT = Path(__file__).parents[2]


def _tree_hashes(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file()}


def _seed_integrity_export_inputs(stage: dict, entry: dict) -> None:
    repository = Path(stage["repository_root"])
    results = Path(stage["results_root"])
    child_refs = []
    for provider, run_id in entry["run_ids"].items():
        child_rel = Path("results/manifests") / f"{run_id}.json"
        child_path = repository / child_rel
        child_path.parent.mkdir(parents=True, exist_ok=True)
        child_path.write_text(json.dumps({"run_id": run_id, "provider": provider}) + "\n")
        child_refs.append({"run_id": run_id, "manifest_path": child_rel.as_posix()})
        raw = results / "raw" / run_id
        raw.mkdir(parents=True, exist_ok=True)
        (raw / "synthetic-transport.txt").write_text(f"fixture for {entry['entry_id']} {provider}\n")
    parent = Path(entry["parent_manifest_path"])
    parent.parent.mkdir(parents=True, exist_ok=True)
    parent.write_text(json.dumps({"campaign_id": entry["entry_id"], "child_runs": child_refs}) + "\n")


def test_full_week_logical_pairs_keep_terminal_outcomes_and_settled_exports(tmp_path, monkeypatch):
    clock = FakeClock()
    stage = stage_fixture(tmp_path)
    stage["repository_root"] = str(tmp_path)
    original_entries = {
        entry["entry_id"]: {
            key: entry[key]
            for key in ("entry_id", "priority", "scenario", "launch_at", "scheduled_start", "row_id",
                        "run_ids", "private_root")
        }
        for entry in stage["entries"]
    }
    stage_entries = {entry["entry_id"]: entry for entry in stage["entries"]}
    assert set(original_entries) == {f"{priority}-{index:02}" for index in range(28) for priority in ("P0", "P1")}
    assert len({run_id for entry in stage["entries"] for run_id in entry["run_ids"].values()}) == 168

    book = BudgetLedger(Path(stage["ledger_path"]), scope_id=stage["scope_id"],
        utc_now=clock.utc_now, monotonic_now=clock.monotonic,
        controller_session_id=stage["controller_session_id"])
    costs = load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json")
    book.initialize(costs)
    campaign, _ = resolve_campaign(ROOT / "configs/tests/exp-903-three-provider-smoke.yaml", ROOT)
    barriers = {entry["launch_at"]: threading.Barrier(2, timeout=5) for entry in stage["entries"]}
    launch_lock = threading.Lock()
    launched: list[str] = []
    active_reservations: list[int] = []

    export_snapshots: dict[str, dict[str, str]] = {}
    export_impl = CampaignDriver._archive_closed_entry

    def record_verified_export(stage_arg, entry, record):
        export_impl(stage_arg, entry, record)
        if entry["entry_id"] == "P0-00":
            export_path = Path(stage_arg["results_root"]) / "closed" / "P0-00" / "export"
            assert record.get("export_status") == "verified"
            assert verify_export(export_path)
            export_snapshots["P0-00"] = _tree_hashes(export_path)

    monkeypatch.setattr(CampaignDriver, "_archive_closed_entry", staticmethod(record_verified_export))

    def launch(entry, prepared):
        assert prepared["controller_session_id"] == book.controller_session_id
        _seed_integrity_export_inputs(prepared, entry)
        request, plan = scheduled_request_and_plan(
            campaign, costs, campaign_id=entry["entry_id"], providers=[Provider.AWS, Provider.AZURE, Provider.GCP],
            child_prefix=entry["entry_id"], namespace_prefix=entry["entry_id"], funding={"primary": ".5"},
            measurement_seconds=76)
        request = request.model_copy(update={
            "incurred_cost_usd": Evidence[float](unavailable_reason="concurrent admission reads the ledger"),
            "active_reserved_usd": Evidence[float](unavailable_reason="concurrent admission reads the ledger"),
        })
        decision = book.reserve(request, plan=plan)
        assert decision.status == "reserved", decision.reasons
        barriers[entry["launch_at"]].wait()
        active = sum(bool(item.get("cleanup_open")) for item in book.inspect()["reservations"].values())
        with launch_lock:
            active_reservations.append(active)
            launched.append(entry["entry_id"])
        for provider in (Provider.AWS, Provider.AZURE, Provider.GCP):
            record_absence(book, f"{entry['entry_id']}-{provider.value}",
                decision.evidence.reservation_id.value, provider, clock.utc_now())
        barriers[entry["launch_at"]].wait()
        if entry["entry_id"] == "P1-00":
            # Let exactly the next pair's frozen start expire; later slots remain
            # within their original schedule and must continue without backfill.
            with clock.lock:
                clock.current = START + timedelta(hours=6, seconds=121)
        outcome = "failed" if entry["entry_id"] == "P0-05" else "complete"
        return {"status": outcome, "cleanup_open": False}

    driver(clock, launch).run(stage)

    saved = journal(stage)
    counts = Counter(record["state"] for record in saved["entries"].values())
    assert counts == Counter({"complete": 53, "failed": 1, "missed": 2})
    assert len(launched) == 54 and len(set(launched)) == 54
    assert "P0-01" not in launched and "P1-01" not in launched
    assert {entry_id for entry_id in original_entries if entry_id not in launched} == {"P0-01", "P1-01"}
    assert Counter(entry_id.split("-")[0] for entry_id in launched) == {"P0": 27, "P1": 27}
    launched_per_slot = Counter(stage_entries[entry_id]["launch_at"] for entry_id in launched)
    assert len(launched_per_slot) == 27 and set(launched_per_slot.values()) == {2}
    assert max(active_reservations) == 2
    assert len(book.inspect()["reservations"]) == 54
    assert not book.inspect()["cleanup_blocked"]
    assert all(not item.get("cleanup_open", True) for item in book.inspect()["reservations"].values())
    assert saved["entries"]["P0-05"]["state"] == "failed"
    assert saved["entries"]["P0-05"]["result"]["cleanup_open"] is False
    assert all(saved["entries"][entry_id]["state"] == "missed" for entry_id in ("P0-01", "P1-01"))
    assert export_snapshots["P0-00"]

    for entry_id, frozen in original_entries.items():
        assert {key: stage_entries[entry_id][key] for key in frozen} == frozen

    terminal_before_restart = {entry_id: row["state"] for entry_id, row in saved["entries"].items()}
    calls_before_restart = list(launched)
    driver(clock, lambda *_: pytest.fail("terminal WEEK work must not be retried or backfilled")).run(stage)
    after_restart = journal(stage)
    assert {entry_id: row["state"] for entry_id, row in after_restart["entries"].items()} == terminal_before_restart
    assert launched == calls_before_restart
    preserved_export = Path(stage["results_root"]) / "closed" / "P0-00" / "export"
    assert verify_export(preserved_export)
    assert _tree_hashes(preserved_export) == export_snapshots["P0-00"]
