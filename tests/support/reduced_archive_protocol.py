"""Synthetic archive inputs for the reduced operator integration test.

This module supplies source-shaped settings only. The caller still runs the
production preparation and lifecycle path, and may inject the existing
``tests.unit.test_gcs_archive.FakeGCS`` backend at the normal runtime seam.
Nothing here creates archive receipts, manifests, or native descriptors.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path


def _pytest_tmp_path(path: Path, label: str) -> Path:
    resolved = Path(path).resolve(strict=False)
    temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
    try:
        resolved.relative_to(temporary_root)
    except ValueError as exc:
        raise ValueError(f"{label} must resolve beneath the system temporary root") from exc
    return resolved


def synthetic_archive_sink_settings(root: Path) -> dict:
    """Return bounded, validator-complete archive_sink settings under ``root``.

    The evidence and credentials-shaped file are synthetic local inputs. The
    credentials file contains no token and must never be used to construct the
    production GCS backend; inject the in-memory fake backend after runtime
    construction instead.
    """
    from cloud_network_benchmark.gcs_archive import (
        _ARCHIVE_PROJECT_ID,
        _ARCHIVE_SERVICE_ACCOUNT,
        _ARCHIVE_SOURCE_PRINCIPAL,
    )

    root = _pytest_tmp_path(Path(root), "synthetic archive settings root")
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    quota_path = root / "synthetic-controller-quota.json"
    quota_bytes = 64_000_000
    quota_payload = json.dumps({"measured_free_bytes": quota_bytes}, sort_keys=True) + "\n"
    quota_path.write_text(quota_payload, encoding="utf-8")
    quota_path.chmod(0o600)
    credentials_path = root / "synthetic-source-credentials.json"
    credentials_path.write_text("{\"synthetic_only\": true}\n", encoding="utf-8")
    credentials_path.chmod(0o600)
    archive_root = root / "archive-root"
    staging = root / "archive-staging"
    archive_root.mkdir(exist_ok=True)
    staging.mkdir(exist_ok=True)
    return {
        "bucket": "cnb-synthetic-archive",
        "maximum_staging_bytes": 2_000_000,
        "maximum_archive_bytes": 16_000_000,
        "minimum_controller_free_bytes": 0,
        "minimum_guest_free_bytes": 0,
        "maximum_capsule_bytes": 8_000_000,
        "controller_quota_free_bytes": quota_bytes,
        "controller_quota_evidence_path": str(quota_path),
        "controller_quota_evidence_sha256": hashlib.sha256(quota_path.read_bytes()).hexdigest(),
        "archive_root": str(archive_root),
        "staging_directory": str(staging),
        "source_credentials_file": str(credentials_path),
        "expected_source_principal": _ARCHIVE_SOURCE_PRINCIPAL,
        "expected_project_id": _ARCHIVE_PROJECT_ID,
        "expected_service_account_email": _ARCHIVE_SERVICE_ACCOUNT,
    }


def install_fake_gcs_backend(runtime: dict, backend) -> None:
    """Install an in-memory backend at the normal runtime archive_sink seam."""
    sink = runtime.get("archive_sink")
    if not isinstance(sink, dict):
        raise ValueError("source-shaped archive_sink settings must exist before backend injection")
    if not all(callable(getattr(backend, name, None)) for name in ("stat", "upload", "download", "delete")):
        raise TypeError("fake archive backend must implement the GCS object boundary")
    sink["backend"] = backend


def replay_and_reconcile_actual_capsule(*, capsule_path: Path, expected_capsule_sha256: str,
        backend, bucket: str, archive_staging_root: Path, materialization_root: Path,
        repository_root: Path, maximum_part_bytes: int, evaluated_at,
        campaign_driver, state_root: Path, stage_id: str, entry_id: str,
        assert_first_provider_identity_rejected: bool = False) -> dict:
    """Consume the exact CLI-written capsule, then reconcile its campaign entry.

    Capsule path and digest must come from the actual archive-pending journal
    result. Restore/finalization writes the production replay receipt, and the
    supplied real CampaignDriver instance validates that receipt against its
    persisted prepared stage, journal, recovery state, and manifests.
    """
    from cloud_network_benchmark.archive_replay import (
        restore_and_finalize_batch_capsule,
    )

    capsule_path = _pytest_tmp_path(Path(capsule_path), "actual replay capsule")
    archive_staging_root = _pytest_tmp_path(Path(archive_staging_root), "archive staging root")
    materialization_root = _pytest_tmp_path(Path(materialization_root), "materialization root")
    state_root = _pytest_tmp_path(Path(state_root), "campaign state root")
    replay = restore_and_finalize_batch_capsule(capsule_path=capsule_path,
        expected_capsule_sha256=expected_capsule_sha256, backend=backend, bucket=bucket,
        archive_staging_root=archive_staging_root,
        materialization_root=materialization_root,
        repository_root=Path(repository_root), maximum_part_bytes=maximum_part_bytes,
        evaluated_at=evaluated_at)
    if replay.get("status") not in {"replayed", "replayed_invalid"}:
        raise ValueError("production archive restore did not return a reconciled replay result")
    if not replay.get("analysis", {}).get("report_sha256"):
        raise ValueError("production replay did not complete its deterministic analysis export")
    receipt_path = Path(replay.get("replay_receipt", {}).get("path", ""))
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise ValueError("production batch restore did not persist a replay receipt")
    if assert_first_provider_identity_rejected:
        capsule = json.loads(capsule_path.read_text(encoding="utf-8"))
        providers = list(capsule.get("child_paths", {}))
        if len(providers) < 2 or providers[0] != "aws":
            raise ValueError("multi-provider fixture must put AWS first for the source-child negative")
        provider = providers[0]
        child_path = Path(repository_root) / capsule["child_paths"][provider]
        if child_path.is_symlink() or not child_path.is_file():
            raise ValueError("first provider source child manifest is unavailable")
        original_child = child_path.read_bytes()
        original_receipt = receipt_path.read_bytes()
        try:
            child_doc = json.loads(original_child)
            child_doc["run_id"] = "tampered-first-provider-run"
            child_path.write_text(json.dumps(child_doc, sort_keys=True) + "\n", encoding="utf-8")
            payload = json.loads(original_receipt)
            payload["replayed_manifests"]["children"][provider]["sha256"] = hashlib.sha256(
                child_path.read_bytes()).hexdigest()
            receipt_path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
            try:
                campaign_driver.reconcile_archive(state_root, stage_id, entry_id, receipt_path)
            except ValueError as exc:
                expected_identity_rejections = (
                    "capsule identity fields",
                    "run_id must match campaign_id and provider",
                )
                if not any(reason in str(exc) for reason in expected_identity_rejections):
                    raise
            else:
                raise ValueError("tampered first-provider child identity was accepted")
            journal_path = Path(state_root) / stage_id / "campaign-journal.json"
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            if journal["entries"][entry_id].get("state") != "archive_pending_validation":
                raise ValueError("tampered first-provider child changed the pending journal")
        finally:
            child_path.write_bytes(original_child)
            receipt_path.write_bytes(original_receipt)
    try:
        reconciled = campaign_driver.reconcile_archive(state_root, stage_id,
            entry_id, receipt_path)
    except BaseException as exc:
        import traceback
        validation = replay.get("validation", {})
        summary = {provider: {
            "status": item.get("status"),
            "data_validity": item.get("result", {}).get("validation_status", {}).get("data_validity"),
            "failed_checks": item.get("result", {}).get("validation_status", {}).get("failed_checks")}
            for provider, item in validation.items() if isinstance(item, dict)}
        raise RuntimeError("actual archive replay validation summary "
            + json.dumps(summary, sort_keys=True, default=str)
            + "\nreconcile error: " + repr(exc)
            + "\n" + traceback.format_exc()) from exc
    entry = reconciled.get("entries", {}).get(entry_id, {})
    if (reconciled.get("counts", {}).get("complete", 0) < 1
            or entry.get("state") != "complete"
            or entry.get("result", {}).get("archive_validation") != "valid"):
        raise ValueError("actual campaign journal reconciliation did not close the entry as valid")
    return {"replay": replay, "reconciliation": reconciled}
