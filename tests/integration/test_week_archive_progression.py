"""Native replay checks for WEEK archive progression; all stores are fake."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.integration.week_archive_fixture import build_single_provider_week_archive_fixture
from tests.integration.test_campaign_driver import driver, journal


def _quota(tmp_path: Path, *, free: int, metadata: int = 4096,
           growth: int = 8192, reserve: int = 16384) -> tuple[Path, str]:
    path = tmp_path / f"replay-quota-{free}.json"
    payload = {"measured_free_bytes": free, "measured_at": datetime.now(timezone.utc).isoformat(),
        "metadata_headroom_bytes": metadata, "projected_other_growth_bytes": growth,
        "reserve_bytes": reserve}
    encoded = (json.dumps(payload, sort_keys=True) + "\n").encode()
    path.write_bytes(encoded)
    return path, hashlib.sha256(encoded).hexdigest()


def _admitted_args(fixture, quota_path: Path, quota_sha: str, free: int) -> dict:
    return {"admit_replay_storage": True, "controller_quota_free_bytes": free,
        "controller_quota_evidence_path": quota_path,
        "controller_quota_evidence_sha256": quota_sha,
        "metadata_headroom_bytes": 4096, "projected_other_growth_bytes": 8192,
        "minimum_free_bytes": 16384}


def test_replay_admission_precedes_backend_and_sealed_export_stays_verified(tmp_path, monkeypatch):
    fixture = build_single_provider_week_archive_fixture(tmp_path=tmp_path,
        monkeypatch=monkeypatch, repository_root=Path.cwd())
    try:
        quota_path, quota_sha = _quota(tmp_path, free=10**9)
        result = fixture.restore(**{**fixture.restore_kwargs,
            **_admitted_args(fixture, quota_path, quota_sha, 10**9),
            "prepare_replay_roots": False})
        assert result["status"] == "replayed"
        assert result["validation"]["aws"]["status"]["data_validity"] == "valid"
        selection = Path(result["analysis"]["selection_path"])
        from cloud_network_benchmark.campaign_export import verify_export
        assert verify_export(selection.parent)
        report = Path(result["analysis"]["report_path"])
        assert report.is_file() and report.parent == selection.parent.parent
        assert result["storage_projection"]["raw_copy_count_at_peak"] == 3
    finally:
        fixture.cleanup()


def test_under_quota_replay_fails_before_backend_or_result_writes(tmp_path, monkeypatch):
    fixture = build_single_provider_week_archive_fixture(tmp_path=tmp_path,
        monkeypatch=monkeypatch, repository_root=Path.cwd())
    try:
        quota_path, quota_sha = _quota(tmp_path, free=1)
        calls: list[str] = []
        before = sorted(path.relative_to(tmp_path).as_posix()
            for path in tmp_path.rglob("*") if path.is_file())
        args = {**fixture.restore_kwargs, **_admitted_args(fixture, quota_path, quota_sha, 1),
            "backend": None, "backend_factory": lambda: calls.append("backend")}
        with pytest.raises(OSError, match="user quota cannot cover bounded replay peak"):
            fixture.restore(**args)
        after = sorted(path.relative_to(tmp_path).as_posix()
            for path in tmp_path.rglob("*") if path.is_file())
        assert calls == []
        assert after == before
    finally:
        fixture.cleanup()


def test_second_parallel_replay_counts_retained_first_replay_payload(tmp_path, monkeypatch):
    fixture = build_single_provider_week_archive_fixture(tmp_path=tmp_path,
        monkeypatch=monkeypatch, repository_root=Path.cwd())
    try:
        closed = fixture.receipt["manifest"]["closed_inventory"]
        raw_bytes = sum(row["expected_size_bytes"] for row in closed["items"])
        compressed_bytes = sum(row["archive_bytes"] for row in fixture.receipt["manifest"]["roles"])
        one_payload = compressed_bytes + 4 * raw_bytes
        # The quota fits one replay exactly with all fixed bounds, but not a
        # second replay while retaining the first replay's native copies.
        free = one_payload + 4096 + 8192 + 16384
        quota_path, quota_sha = _quota(tmp_path, free=free)
        calls: list[str] = []
        args = {**fixture.restore_kwargs,
            **_admitted_args(fixture, quota_path, quota_sha, free),
            "prior_replay_payload_bytes": one_payload,
            "backend": None, "backend_factory": lambda: calls.append("backend")}
        with pytest.raises(OSError, match="user quota cannot cover bounded replay peak"):
            fixture.restore(**args)
        assert calls == []
    finally:
        fixture.cleanup()


def test_campaign_driver_replays_and_reconciles_genuine_pending_capsule_under_owned_lock(
        tmp_path, monkeypatch):
    from cloud_network_benchmark import archive_replay, gcs_archive
    from cloud_network_benchmark.campaign_inputs import prepare_stage
    from tests.integration.test_campaign_admission import operator_settings
    fixture = build_single_provider_week_archive_fixture(tmp_path=tmp_path,
        monkeypatch=monkeypatch, repository_root=Path.cwd())
    try:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        class Clock:
            def __init__(self): self.current = now
            def utc_now(self): return self.current
            def monotonic(self): return (self.current - now).total_seconds()
            def sleep(self, seconds):
                from datetime import timedelta
                self.current += timedelta(seconds=seconds)
        clock = Clock()
        settings_root = tmp_path / "stage-settings"
        settings_root.mkdir()
        settings = operator_settings(settings_root, Path.cwd(), monkeypatch)
        settings["state_root"] = str(tmp_path / "driver-state")
        settings["minimum_free_bytes"] = 5_000_000
        settings["results_root"] = str(fixture.result_roots["aws"].parent.parent)
        settings["ledger"] = {"path": str(fixture.built["ledger"].path),
            "scope_id": fixture.built["ledger"].scope_id}
        archive_root, staging_root, restore_root = (tmp_path / "driver-archive-root",
            tmp_path / "driver-archive-staging", tmp_path / "driver-archive-restore")
        for root in (archive_root, staging_root, restore_root): root.mkdir()
        quota_doc = {"measured_free_bytes": 10**12, "measured_at": now.isoformat(),
            "metadata_headroom_bytes": 4096, "projected_other_growth_bytes": 8192,
            "reserve_bytes": 16384}
        quota_path = tmp_path / "source-quota.json"
        quota_bytes = (json.dumps(quota_doc, sort_keys=True) + "\n").encode()
        quota_path.write_bytes(quota_bytes)
        review_fields = settings["approvals"]["size_export_review"]["fields"]
        review_fields.update(capsule_state_manifest_bytes=65_536,
            controller_reserve_bytes=16_384,
            expected_concurrent_archive_part_count=2,
            maximum_archive_part_bytes=2_000_000,
            maximum_guest_archive_bytes=2_000_000,
            max_guest_raw_bytes=2_000_000, measured_guest_free_bytes=5_000_000,
            guest_headroom_bytes=100_000,
            controller_quota_free_bytes=10**12,
            controller_quota_evidence_path=str(quota_path),
            controller_quota_evidence_sha256=hashlib.sha256(quota_bytes).hexdigest())
        settings["archive_sink"] = {"bucket": fixture.bucket, "maximum_staging_bytes": 2_000_000,
            "maximum_archive_bytes": 16_000_000, "minimum_controller_free_bytes": 1024,
            "minimum_guest_free_bytes": 0, "maximum_capsule_bytes": 65_536,
            "controller_quota_free_bytes": 10**12,
            "controller_quota_evidence_path": str(quota_path),
            "controller_quota_evidence_sha256": hashlib.sha256(quota_bytes).hexdigest(),
            "source_credentials_file": str(tmp_path / "fake-adc.json"),
            "expected_source_principal": "katyabraun0@gmail.com",
            "expected_project_id": "project-a70f8497-3463-4ef2-9cd",
            "expected_service_account_email": "cnb-archive-halle@project-a70f8497-3463-4ef2-9cd.iam.gserviceaccount.com",
            "restore_path": str(restore_root), "archive_root": str(archive_root),
            "staging_directory": str(staging_root)}
        stage = prepare_stage({"stage": "week"}, settings, Path.cwd(),
            Path(settings["state_root"]), Path(settings["results_root"]), clock)
        # Keep two original scheduled pairs: the first carries genuine pending
        # archive evidence and the second proves dispatch resumes after replay.
        launch_times = list(dict.fromkeys(item["launch_at"] for item in stage["entries"]))[:2]
        stage["entries"] = [item for item in stage["entries"]
            if item["launch_at"] in launch_times]
        stage["controller_session_id"] = json.loads(
            Path(fixture.built["recovery_path"]).read_text(encoding="utf-8"))["controller_session_id"]
        stage["results_root"] = str(fixture.result_roots["aws"].parent.parent)
        stage["ledger_path"] = str(fixture.built["ledger"].path)
        stage["scope_id"] = fixture.built["ledger"].scope_id
        recovery_path = Path(fixture.built["recovery_path"])
        recovery = json.loads(recovery_path.read_text(encoding="utf-8"))
        stage["controller_session_id"] = recovery["controller_session_id"]
        entry = stage["entries"][0]
        run_id = fixture.capsule["child_manifests"]["aws"]["run_id"]
        entry.update(run_ids={"aws": run_id}, parent_manifest_path=str(fixture.parent_path),
            result_path=str(fixture.result_roots["aws"] / "validation" / "result.json"),
            private_root=str(recovery_path.parent))
        runtime_path = Path(entry["runtime_config_path"])
        runtime_document = json.loads(runtime_path.read_text(encoding="utf-8"))
        runtime_document["initialized_campaign_manifest"] = str(fixture.parent_path)
        runtime_document["providers"] = {"aws": runtime_document["providers"]["aws"]}
        runtime_document["access"]["diagnostic_interfaces"] = {
            "aws": runtime_document["access"]["diagnostic_interfaces"]["aws"]}
        runtime_document["allowance"]["provider_leads_seconds"] = {
            "aws": runtime_document["allowance"]["provider_leads_seconds"]["aws"]}
        runtime_document["archive_sink"]["bucket"] = fixture.bucket
        runtime_path.write_text(json.dumps(runtime_document, sort_keys=True) + "\n", encoding="utf-8")
        monkeypatch.setattr(gcs_archive, "google_cloud_storage_backend", lambda _sink: fixture.backend)
        monkeypatch.setattr(archive_replay, "restore_and_finalize_batch_capsule", fixture.restore)
        capsule_ref = {"status": "archive_pending_replay", "path": str(fixture.capsule_path),
            "sha256": fixture.capsule_sha256}

        def launch(item, _stage):
            if item["entry_id"] == entry["entry_id"]:
                return {"status": "archive_pending_validation", "cleanup_open": False,
                    "run_ids": {"aws": run_id}, "reservation_id": fixture.lifecycle.reservation_evidence.reservation_id.value,
                    "replay_capsule": capsule_ref}
            return {"status": "complete", "cleanup_open": False}

        engine = driver(clock, launch)
        outcome = engine.run(stage)
        saved = journal(stage)["entries"][entry["entry_id"]]
        assert saved["state"] == "complete", saved.get("result")
        assert saved["result"]["archive_validation"] == "valid"
        assert saved["result"].get("ledger_reconciliation_required") is None
        assert saved.get("blocked_reason") is None
        assert saved["export_status"] == "verified"
        from cloud_network_benchmark.campaign_export import verify_export
        assert verify_export(Path(stage["results_root"]) / "closed" / entry["entry_id"] / "export")
        assert fixture.backend.deleted == []
        assert outcome["counts"]["complete"] == 4
        assert set(outcome["entries"]) == {item["entry_id"] for item in stage["entries"]}

        # Emulate controller loss after the immutable successful receipt was
        # committed but before the journal marked this entry complete. Restart
        # must validate and consume that receipt without a second restore.
        journal_path = Path(stage["state_root"]) / stage["stage_id"] / "campaign-journal.json"
        saved_journal = journal(stage)
        saved_entry = saved_journal["entries"][entry["entry_id"]]
        receipt_path = Path(saved_entry["result"]["archive_replay_receipt"])
        receipt_bytes = receipt_path.read_bytes()
        pending_result = dict(saved_entry["result"])
        for key in ("archive_validation", "archive_replay_receipt", "archive_replay_receipt_sha256"):
            pending_result.pop(key, None)
        saved_entry.update(state="archive_pending_validation", result=pending_result)
        journal_path.write_text(json.dumps(saved_journal, sort_keys=True) + "\n", encoding="utf-8")
        receipt_path.write_bytes(b"corrupt receipt\n")
        monkeypatch.setattr(archive_replay, "restore_and_finalize_batch_capsule",
            lambda **_kwargs: pytest.fail("restart retried archive restore"))
        monkeypatch.setattr(gcs_archive, "google_cloud_storage_backend",
            lambda _sink: pytest.fail("restart opened archive backend"))
        driver(clock, lambda *_: pytest.fail("restart relaunched a WEEK entry")).run(stage)
        assert journal(stage)["entries"][entry["entry_id"]]["state"] == "archive_pending_validation"
        receipt_path.write_bytes(receipt_bytes)
        driver(clock, lambda *_: pytest.fail("receipt recovery relaunched a WEEK entry")).run(stage)
        recovered = journal(stage)["entries"][entry["entry_id"]]
        assert recovered["state"] == "complete"
        assert recovered["result"]["archive_validation"] == "valid"
        assert verify_export(Path(stage["results_root"]) / "closed" / entry["entry_id"] / "export")
    finally:
        fixture.cleanup()
