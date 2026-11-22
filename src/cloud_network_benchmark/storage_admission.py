"""Evidence-based DAY/WEEK raw, archive and filesystem headroom admission."""
from __future__ import annotations

from pathlib import Path
import shutil
from typing import Callable, Mapping

from .closed_archive import FAT32_FILE_LIMIT


def validate_storage_review(stage: str, review: Mapping, *, minimum_free_bytes: int,
                            results_path: Path, disk_free: Callable[[Path], int] | None = None,
                            storage_mode: str = "local", restore_path: Path | None = None) -> dict:
    """Fail closed unless measured source and destination projections fit.

    WEEK projects 168 closed child-run directories; DAY projects one 24-hour
    result set per provider. Controller peak includes native outputs plus the
    compressed archives being staged before their integrity gate completes.
    Guest admission uses the worst measured guest free-space sample and the
    projected per-guest raw growth, keeping the source copies intact.
    """
    if stage not in {"day", "week"}:
        raise ValueError("storage review applies only to DAY and WEEK")
    if storage_mode not in {"local", "remote_archive"}:
        raise ValueError("storage_mode must be local or remote_archive")
    required_fields = {
        "measured_run_raw_bytes", "measured_run_seconds", "expected_raw_bytes",
        "expected_archive_bytes", "max_guest_raw_bytes", "measured_guest_free_bytes",
        "guest_headroom_bytes", "destination_headroom_bytes", "archive_manifest_sha256",
        "archive_checksum_verified", "expected_archive_part_count",
    }
    if storage_mode == "remote_archive":
        required_fields |= {"expected_concurrent_archive_part_count",
            "maximum_archive_part_bytes", "capsule_state_manifest_bytes",
            "controller_reserve_bytes", "restore_destination_headroom_bytes"}
    missing = sorted(required_fields - set(review))
    if missing:
        raise ValueError("storage review missing measured fields: " + ", ".join(missing))
    integers = required_fields - {"archive_manifest_sha256", "archive_checksum_verified"}
    for field in integers:
        if type(review[field]) is not int or review[field] < 0:
            raise ValueError(f"storage review {field} must be a nonnegative integer")
    if review["measured_run_raw_bytes"] <= 0 or review["measured_run_seconds"] <= 0:
        raise ValueError("measured run bytes and seconds must be positive")
    if review["archive_checksum_verified"] is not True:
        raise ValueError("archive SHA-256 verification evidence is required")
    manifest_hash = review["archive_manifest_sha256"]
    if not isinstance(manifest_hash, str) or len(manifest_hash) != 64 or any(c not in "0123456789abcdef" for c in manifest_hash):
        raise ValueError("archive_manifest_sha256 must be a lowercase SHA-256")
    expected_runs, projected_seconds = (3, 86400) if stage == "day" else (168, 180)
    sample_rate = review["measured_run_raw_bytes"] / review["measured_run_seconds"]
    projected_minimum = int(sample_rate * projected_seconds * expected_runs)
    if review["expected_raw_bytes"] < projected_minimum:
        raise ValueError(f"expected_raw_bytes undercounts measured {stage.upper()} output growth")
    count = review["expected_archive_part_count"]
    if count < 1 or review["expected_archive_bytes"] <= 0 or review["expected_archive_bytes"] > count * FAT32_FILE_LIMIT:
        raise ValueError("expected_archive_part_count does not cover archive bytes under FAT32 limits")
    guest_required = review["max_guest_raw_bytes"] + review["guest_headroom_bytes"]
    if review["measured_guest_free_bytes"] < guest_required:
        raise ValueError("measured guest free space does not cover projected per-guest output and headroom")
    if storage_mode == "local":
        destination_required = (review["expected_raw_bytes"] + review["expected_archive_bytes"]
                                + review["destination_headroom_bytes"])
        restore_required = None
        controller_peak = destination_required
    else:
        concurrent_parts = review["expected_concurrent_archive_part_count"]
        part_bytes = review["maximum_archive_part_bytes"]
        overhead = review["capsule_state_manifest_bytes"]
        reserve = review["controller_reserve_bytes"]
        restore_headroom = review["restore_destination_headroom_bytes"]
        if concurrent_parts < 1 or part_bytes < 1 or part_bytes > FAT32_FILE_LIMIT:
            raise ValueError("remote archive concurrent part projection is invalid")
        if review["expected_archive_bytes"] > count * part_bytes:
            raise ValueError("aggregate archive part inventory undercounts expected archives")
        if overhead <= 0 or reserve < 0 or restore_headroom < 0:
            raise ValueError("remote archive overhead, reserve, and restore headroom are invalid")
        # Halle holds only the concurrently active bounded parts plus small state.
        controller_peak = concurrent_parts * part_bytes + overhead + reserve
        destination_required = controller_peak
        # The roomy Mac restore must fit native bytes and the complete compressed archive peak.
        restore_required = review["expected_raw_bytes"] + review["expected_archive_bytes"] + restore_headroom
        if review["destination_headroom_bytes"] < restore_headroom:
            raise ValueError("destination headroom must cover the roomy restore destination headroom")
        if restore_path is None:
            raise ValueError("remote archive mode requires a separately measured roomy restore_path")
        restore_path = Path(restore_path)
        restore_probe = restore_path
        while not restore_probe.exists() and restore_probe != restore_probe.parent:
            restore_probe = restore_probe.parent
        observed_restore_free = (disk_free or (lambda target: shutil.disk_usage(target).free))(restore_probe)
        if observed_restore_free < restore_required:
            raise OSError("roomy restore destination free space is below native/archive peak plus headroom")
    if storage_mode == "local":
        observed_restore_free = None
    if type(minimum_free_bytes) is not int or minimum_free_bytes < destination_required:
        raise ValueError("minimum_free_bytes must cover native results, archive staging and destination headroom")
    results_path = Path(results_path)
    path = results_path
    while not path.exists() and path != path.parent:
        path = path.parent
    observed_free = (disk_free or (lambda target: shutil.disk_usage(target).free))(path)
    if observed_free < destination_required:
        raise OSError("current destination free space is below projected native/archive headroom")
    return {"stage": stage, "storage_mode": storage_mode, "expected_run_count": expected_runs,
            "projected_raw_minimum_bytes": projected_minimum,
            "expected_raw_bytes": review["expected_raw_bytes"],
            "expected_archive_bytes": review["expected_archive_bytes"],
            "expected_concurrent_archive_part_count": review.get("expected_concurrent_archive_part_count"),
            "controller_peak_required_bytes": controller_peak,
            "restore_destination_required_bytes": restore_required,
            "observed_restore_free_bytes": observed_restore_free,
            "guest_required_free_bytes": guest_required,
            "destination_required_free_bytes": destination_required,
            "observed_destination_free_bytes": observed_free}
