"""Replay a real native validation fixture after moving the campaign export."""
from __future__ import annotations

import json
import hashlib
import shutil

import pytest
from pathlib import Path


@pytest.mark.parametrize("aggregate", [False, True])
@pytest.mark.parametrize("absolute_paths", [False, True])
def test_export_can_move_and_analyze_without_original_runner_files(repository_root, tmp_path,
        frozen_clock, deterministic_token, fake_git_commits, request, absolute_paths, aggregate):
    from tests.f12_support import materialize_f08_pair
    from cloud_network_benchmark.campaign_export import export_selection
    from cloud_network_benchmark.cli import main

    runner = tmp_path / "runner"
    runner.mkdir()
    value, result, _ = materialize_f08_pair(repository_root, runner, frozen_clock,
        deterministic_token, fake_git_commits, request, mode="acceptance")
    raw = Path(value.child_result_root)
    if absolute_paths:
        from cloud_network_benchmark.contracts.common import Evidence
        value = value.model_copy(update={
            "config_snapshot_path": str(raw / value.config_snapshot_path),
            "resolved_snapshot_path": str(raw / value.resolved_snapshot_path),
            "schedule_snapshot_path": Evidence[str](value=str(raw / value.schedule_snapshot_path.value)),
        })
    validation = raw / "validation"
    validation.mkdir(exist_ok=True)
    (validation / "input.json").write_text(value.model_dump_json())
    (validation / "result.json").write_text(result.model_dump_json())
    from cloud_network_benchmark.lifecycle_bundle import seal_bundle
    seal_bundle(raw)
    stage = {"stage_id": "offline-acceptance", "stage": "acceptance", "repository_root": str(repository_root), "results_root": str(runner / "results"),
             "state_root": str(runner / "private"), "entries": [{"entry_id": "V-S1",
                 "parent_manifest_path": value.campaign_manifest_path,
                 "run_ids": {str(value.provider): value.identity.run_id}}]}
    destination = runner / "results/closed/V-S1/export" if aggregate else tmp_path / "export"
    selection = export_selection(stage, destination)
    assert Path(selection).is_file()
    if aggregate:
        from cloud_network_benchmark.campaign_export import aggregate_closed_exports
        selection = aggregate_closed_exports(stage, {"entries": {"V-S1": {"state": "complete", "result": {"cleanup_open": False}}}},
                                             runner / "results/export")
        assert Path(selection).is_file()
        destination = runner / "results"
    copied = tmp_path / "laptop" / "copied"
    copied.parent.mkdir()
    shutil.move(str(destination), copied)
    # Hide every absolute original path. Replay must use only copied bytes.
    shutil.move(str(runner), tmp_path / "unavailable-original")
    output = tmp_path / "analysis"
    selection_root = copied / "export" if aggregate else copied
    assert main(["analyze", "--selection", str(selection_root / "selection.json"), "--output", str(output)]) == 0
    report = json.loads((output / "report.json").read_text())
    assert report["runs"]
    for index in copied.rglob("checksums.sha256"):
        for row in index.read_text().splitlines():
            digest, relative_path = row.split("  ", 1)
            assert hashlib.sha256((index.parent / relative_path).read_bytes()).hexdigest() == digest
    assert {row["current_data_validity"] for row in report["runs"]} == {"valid"}
    assert not list(copied.rglob("recovery.json"))
    assert not list(copied.rglob("runtime.json"))
    from cloud_network_benchmark.campaign_export import verify_export
    assert verify_export(selection_root)
    closed_root = copied / "closed/V-S1/export" if aggregate else copied
    native_path = next((closed_root / "runs" / value.identity.run_id).rglob("sockperf.csv"))
    native_path.write_bytes(b"corrupted after copy")
    assert verify_export(selection_root) is False


def test_corrupted_export_copy_is_rejected_and_original_bytes_remain(tmp_path, monkeypatch):
    import cloud_network_benchmark.campaign_export as exporting

    source = tmp_path / "native"
    source.mkdir()
    native = source / "sockperf.csv"
    native.write_bytes(b"immutable original native output\n")
    original_copytree = exporting.shutil.copytree

    def corrupt_copy(source, destination, **kwargs):
        result = original_copytree(source, destination, **kwargs)
        (Path(destination) / "sockperf.csv").write_bytes(b"truncated")
        return result

    monkeypatch.setattr(exporting.shutil, "copytree", corrupt_copy)
    with pytest.raises(OSError, match="checksum"):
        exporting._copy_verified(source, tmp_path / "copied")
    assert native.read_bytes() == b"immutable original native output\n"


def test_failed_notification_hook_is_visible_without_hiding_stage_failure(tmp_path):
    from cloud_network_benchmark.campaign import _finalize_exports

    results = tmp_path / "results"
    results.mkdir()
    hook = tmp_path / "failing-notification"
    hook.write_text("#!/bin/sh\nexit 7\n")
    hook.chmod(0o700)
    stage = {"stage_id": "hook-failure", "stage": "week", "entries": [],
             "results_root": str(results), "notification_hook": str(hook)}
    result = {"status": "failed", "counts": {"failed": 1}, "journal": "private-journal"}
    _finalize_exports(stage, {"entries": {}}, result)
    assert result["status"] == "failed"
    assert result["notification_status"].startswith("failed:")
    assert (results / "stage-summary.json").is_file()


def test_existing_unverifiable_export_is_rejected_and_local_bytes_remain(tmp_path, monkeypatch):
    import cloud_network_benchmark.campaign as campaign

    results = tmp_path / "results"
    exported = results / "export"
    exported.mkdir(parents=True)
    (exported / "selection.json").write_text('{"format_version":1,"runs":[]}')
    payload = exported / "native.csv"
    payload.write_bytes(b"native evidence\n")
    stage = {"stage_id": "backup-failure", "stage": "week", "entries": [{"entry_id": "one"}],
             "results_root": str(results), "backup_destination": str(tmp_path / "backup")}
    result = {"status": "complete", "counts": {"complete": 1}, "journal": "private-journal"}
    monkeypatch.setattr(campaign.shutil, "copytree", lambda *a, **k: pytest.fail("unverified export copied to backup"))
    campaign._finalize_exports(stage, {"entries": {"one": {"state": "complete"}}}, result)
    assert result["status"] == "failed"
    assert "integrity check" in result["export_status"]
    assert payload.read_bytes() == b"native evidence\n"


def test_closed_bundle_backup_is_verified_and_later_corruption_is_reported(repository_root, tmp_path,
        frozen_clock, deterministic_token, fake_git_commits, request):
    from tests.f12_support import materialize_f08_pair
    from cloud_network_benchmark.campaign import CampaignDriver
    from cloud_network_benchmark.lifecycle_bundle import seal_bundle

    runner = tmp_path / "runner"
    runner.mkdir()
    value, result, _ = materialize_f08_pair(repository_root, runner, frozen_clock,
        deterministic_token, fake_git_commits, request, mode="acceptance")
    raw = Path(value.child_result_root)
    (raw / "validation").mkdir(exist_ok=True)
    (raw / "validation/input.json").write_text(value.model_dump_json())
    (raw / "validation/result.json").write_text(result.model_dump_json())
    seal_bundle(raw)
    entry = {"entry_id": "V-S1", "parent_manifest_path": value.campaign_manifest_path,
             "run_ids": {str(value.provider): value.identity.run_id}}
    stage = {"stage_id": "backup-test", "stage": "acceptance", "repository_root": str(repository_root),
        "results_root": str(runner / "results"), "state_root": str(runner / "private"),
        "backup_destination": str(tmp_path / "backup"), "entries": [entry]}
    record = {"state": "complete", "result": {"cleanup_open": False}}
    CampaignDriver._archive_closed_entry(stage, entry, record)
    assert record["export_status"] == record["backup_status"] == "verified"
    backup = tmp_path / "backup/backup-test/closed/V-S1/export"
    corrupted = next(backup.rglob("sockperf.csv"))
    corrupted.write_bytes(b"corrupt backup after initial verification")
    CampaignDriver._archive_closed_entry(stage, entry, record)
    assert record["export_status"] == "verified"
    assert record["backup_status"].startswith("failed:")
    assert "existing backup failed integrity" in record["backup_status"]
    assert record["state"] == "complete"
    assert next(raw.rglob("sockperf.csv")).read_bytes() != corrupted.read_bytes()
    journal_root = Path(stage["state_root"]) / stage["stage_id"]
    journal_root.mkdir(parents=True, exist_ok=True)
    (journal_root / "prepared-stage.json").write_text(json.dumps(stage))
    (journal_root / "campaign-journal.json").write_text(json.dumps({
        "stage_id": stage["stage_id"], "entries": {entry["entry_id"]: record}}))
    status = CampaignDriver().status(Path(stage["state_root"]), stage["stage_id"])[0]
    assert status["exports"][entry["entry_id"]] == "verified"
    assert status["backups"][entry["entry_id"]] == record["backup_status"]
