from __future__ import annotations

from pathlib import Path

import pytest

from cloud_network_benchmark.closed_archive import FAT32_FILE_LIMIT
from cloud_network_benchmark.storage_admission import validate_storage_review


def review(**changes):
    value = {"measured_run_raw_bytes": 100_000, "measured_run_seconds": 180,
        "expected_raw_bytes": 200_000_000, "expected_archive_bytes": 100_000_000,
        "expected_archive_part_count": 1, "max_guest_raw_bytes": 150_000_000,
        "measured_guest_free_bytes": 200_000_000, "guest_headroom_bytes": 50_000_000,
        "destination_headroom_bytes": 50_000_000, "archive_manifest_sha256": "a" * 64,
        "archive_checksum_verified": True}
    value.update(changes)
    return value


@pytest.mark.parametrize(("stage", "expected_runs"), [("day", 3), ("week", 168)])
def test_day_week_review_projects_native_growth_guest_and_destination_headroom(tmp_path, stage, expected_runs):
    result = validate_storage_review(stage, review(), minimum_free_bytes=350_000_000,
        results_path=tmp_path / "results", disk_free=lambda _: 350_000_000)
    assert result["expected_run_count"] == expected_runs
    assert result["destination_required_free_bytes"] == 350_000_000


def test_measured_growth_undercount_stops_admission(tmp_path):
    with pytest.raises(ValueError, match="undercounts"):
        validate_storage_review("day", review(expected_raw_bytes=1), minimum_free_bytes=500_000_000,
            results_path=tmp_path, disk_free=lambda _: 900_000_000)


def test_guest_headroom_and_actual_controller_free_space_are_both_enforced(tmp_path):
    with pytest.raises(ValueError, match="guest free space"):
        validate_storage_review("week", review(measured_guest_free_bytes=10), minimum_free_bytes=500_000_000,
            results_path=tmp_path, disk_free=lambda _: 900_000_000)
    with pytest.raises(OSError, match="current destination free space"):
        validate_storage_review("day", review(), minimum_free_bytes=500_000_000,
            results_path=tmp_path, disk_free=lambda _: 1)


def test_minimum_free_must_cover_native_plus_archive_plus_headroom(tmp_path):
    with pytest.raises(ValueError, match="native results, archive staging"):
        validate_storage_review("day", review(), minimum_free_bytes=349_999_999,
            results_path=tmp_path, disk_free=lambda _: 1_000_000_000)


def test_archive_must_fit_declared_fat32_bounded_part_count(tmp_path):
    with pytest.raises(ValueError, match="FAT32 limits"):
        validate_storage_review("week", review(expected_archive_bytes=FAT32_FILE_LIMIT + 1),
            minimum_free_bytes=1_000_000_000_000, results_path=tmp_path,
            disk_free=lambda _: 1_000_000_000_000)
    passed = validate_storage_review("week", review(expected_archive_bytes=FAT32_FILE_LIMIT + 1,
        expected_archive_part_count=2), minimum_free_bytes=1_000_000_000_000,
        results_path=tmp_path, disk_free=lambda _: 1_000_000_000_000)
    assert passed["expected_archive_bytes"] == FAT32_FILE_LIMIT + 1


def test_unverified_or_malformed_archive_manifest_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="SHA-256 verification"):
        validate_storage_review("week", review(archive_checksum_verified=False), minimum_free_bytes=500_000_000,
            results_path=tmp_path, disk_free=lambda _: 900_000_000)
    with pytest.raises(ValueError, match="lowercase SHA"):
        validate_storage_review("week", review(archive_manifest_sha256="not-a-hash"), minimum_free_bytes=500_000_000,
            results_path=tmp_path, disk_free=lambda _: 900_000_000)
