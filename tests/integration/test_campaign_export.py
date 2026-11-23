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


def _config_only_stage(tmp_path, *, add_native=False):
    import json

    repository = tmp_path / "repo"
    results = repository / "results"
    manifests = repository / "manifests"
    manifests.mkdir(parents=True)
    entries = []
    providers = {"aws": "AWS", "azure": "AZURE", "gcp": "GCP"}
    for suffix, provider in providers.items():
        run_id = f"failed-{suffix}"
        raw = results / "raw" / run_id
        raw.mkdir(parents=True)
        (raw / "config.yaml").write_text(f"experiment: {suffix}\n")
        (raw / "config.resolved.json").write_text(json.dumps({"provider": provider}))
        if add_native:
            (raw / "sockperf.csv").write_text("timestamp,rtt\n")
        child = manifests / f"{run_id}.json"
        child.write_text(json.dumps({"run_id": run_id, "provider": provider}))
        parent = manifests / f"{suffix}-parent.json"
        parent.write_text(json.dumps({"child_runs": [{"run_id": run_id,
            "manifest_path": str(child.relative_to(repository))}]}))
        entries.append({"entry_id": f"V-{suffix}", "parent_manifest_path": str(parent),
                        "run_ids": {provider: run_id}})
    return {"stage_id": "failed-acceptance", "repository_root": str(repository),
            "results_root": str(results), "entries": entries}


def test_unmeasured_config_only_exports_are_transport_verified_but_not_selected(tmp_path):
    from cloud_network_benchmark.campaign_export import export_selection, verify_export

    stage = _config_only_stage(tmp_path)
    destination = tmp_path / "export"
    assert export_selection(stage, destination) is None
    provenance = json.loads((destination / "export-provenance.json").read_text())
    assert provenance["evidence_status"] == "not_validated"
    assert provenance["selection_sha256"] is None
    assert provenance["transport_status"] == "integrity_only"
    assert len(provenance["transport_inventory"]) >= 10
    assert verify_export(destination) is True


@pytest.mark.parametrize("mutation", ["missing", "extra", "native", "symlink", "no_inventory", "fifo"])
def test_partial_export_integrity_fails_closed_on_changes_and_unsealed_native_files(tmp_path, mutation):
    from cloud_network_benchmark.campaign_export import export_selection, verify_export
    import os

    stage = _config_only_stage(tmp_path, add_native=(mutation == "native"))
    destination = tmp_path / "export"
    assert export_selection(stage, destination) is None
    if mutation == "native":
        assert verify_export(destination) is True  # Transport is verified; it is never a replay selection.
        assert not (destination / "selection.json").exists()
        assert json.loads((destination / "export-provenance.json").read_text())["evidence_status"] == "not_validated"
        return
    assert verify_export(destination) is True
    if mutation == "missing":
        next((destination / "runs").rglob("config.yaml")).unlink()
    elif mutation == "extra":
        (destination / "runs" / "unexpected.txt").write_text("untracked")
    elif mutation == "symlink":
        (destination / "runs" / "linked-config").symlink_to("config.yaml")
    elif mutation == "no_inventory":
        provenance_path = destination / "export-provenance.json"
        provenance = json.loads(provenance_path.read_text())
        provenance.pop("transport_inventory")
        provenance_path.write_text(json.dumps(provenance))
    elif mutation == "fifo" and hasattr(os, "mkfifo"):
        os.mkfifo(destination / "runs" / "unexpected-pipe")
    assert verify_export(destination) is False


def test_failed_cleanup_closed_export_reconciliation_preserves_old_copy_and_does_not_dispatch(tmp_path):
    from cloud_network_benchmark.campaign import CampaignDriver

    stage = _config_only_stage(tmp_path)
    stage["state_root"] = str(tmp_path / "state")
    stage_id, entry_id = stage["stage_id"], stage["entries"][0]["entry_id"]
    state = Path(stage["state_root"]) / stage_id
    state.mkdir(parents=True)
    (state / "prepared-stage.json").write_text(json.dumps(stage))
    (state / "campaign-journal.json").write_text(json.dumps({"stage_id": stage_id, "entries": {
        entry_id: {"state": "failed", "result": {"cleanup_open": False},
                   "export_status": "failed: integrity check"}}}))
    export_root = Path(stage["results_root"]) / "closed" / entry_id / "export"
    export_root.mkdir(parents=True)
    (export_root / "old-copy.txt").write_text("preserve these exact old bytes")
    preserved = export_root.with_name("export.previous-invalid")
    result = CampaignDriver().reconcile_export(Path(stage["state_root"]), stage_id, entry_id, preserved)
    assert result["dispatch_started"] is False
    assert result["evidence_status"] == "not_validated"
    assert (preserved / "old-copy.txt").read_text() == "preserve these exact old bytes"
    from cloud_network_benchmark.campaign_export import verify_export
    assert verify_export(export_root)


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
