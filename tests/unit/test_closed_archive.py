from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cloud_network_benchmark.closed_archive import (
    FAT32_FILE_LIMIT, copy_archive_parts, create_closed_archive,
    projected_archive_upper_bound, verify_and_extract_archive,
)


def _tree(root: Path):
    root.mkdir()
    (root / "measurements").mkdir()
    (root / "measurements" / "sockperf.csv").write_bytes(b"timestamp,value\n" + b"1,42\n" * 2000)
    (root / "measurements" / "iperf.jsonl").write_bytes(b'{"interval":1}\n' * 1000)
    return root


def _manifest_hash(path):
    return hashlib.sha256((path / "manifest.json").read_bytes()).hexdigest()


def test_archive_is_deterministic_lossless_and_fat32_part_bounded(tmp_path):
    source = _tree(tmp_path / "closed")
    original = {p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    first = create_closed_archive(source, tmp_path / "archive-a", max_part_bytes=2048)
    second = create_closed_archive(source, tmp_path / "archive-b", max_part_bytes=2048)
    assert first == second
    assert _manifest_hash(tmp_path / "archive-a") == _manifest_hash(tmp_path / "archive-b")
    assert all(row["size_bytes"] <= FAT32_FILE_LIMIT for row in first["parts"])
    verified = verify_and_extract_archive(tmp_path / "archive-a", tmp_path / "restored",
        expected_manifest_sha256=_manifest_hash(tmp_path / "archive-a"))
    assert verified["archive_sha256"] == first["archive_sha256"]
    assert {p.relative_to(tmp_path / "restored").as_posix(): p.read_bytes()
            for p in (tmp_path / "restored").rglob("*") if p.is_file()} == original
    assert {p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob("*") if p.is_file()} == original


def test_copy_verifies_parts_and_is_retry_safe_after_interruption(tmp_path):
    source = _tree(tmp_path / "closed")
    archive = tmp_path / "archive"
    create_closed_archive(source, archive, max_part_bytes=2048)
    expected = _manifest_hash(archive)
    def interrupt(index):
        if index == 1:
            raise InterruptedError("synthetic partial copy")
    with pytest.raises(InterruptedError):
        copy_archive_parts(archive, tmp_path / "copied", expected_manifest_sha256=expected,
                           before_part=interrupt)
    assert not (tmp_path / "copied").exists()
    assert list(tmp_path.glob(".copied.*.partial"))
    copy_archive_parts(archive, tmp_path / "copied", expected_manifest_sha256=expected)
    assert _manifest_hash(tmp_path / "copied") == expected
    # Retrying a complete exact destination is idempotent; different content fails closed.
    assert copy_archive_parts(archive, tmp_path / "copied", expected_manifest_sha256=expected) == tmp_path / "copied"
    with pytest.raises(ValueError, match="bound SHA-256"):
        copy_archive_parts(archive, tmp_path / "other", expected_manifest_sha256="0" * 64)


def test_corrupt_part_or_manifest_hash_is_rejected_without_publishing(tmp_path):
    source = _tree(tmp_path / "closed")
    archive = tmp_path / "archive"
    create_closed_archive(source, archive, max_part_bytes=2048)
    expected = _manifest_hash(archive)
    part = archive / json.loads((archive / "manifest.json").read_text())["parts"][0]["path"]
    part.write_bytes(part.read_bytes()[:-1] + b"X")
    with pytest.raises(ValueError, match="part size or SHA-256"):
        verify_and_extract_archive(archive, tmp_path / "bad", expected_manifest_sha256=expected)
    assert not (tmp_path / "bad").exists()


@pytest.mark.parametrize("destination_free,source_free", [(1, 100000), (100000, 0)])
def test_low_source_or_destination_disk_fails_before_archive_write(tmp_path, destination_free, source_free):
    source = _tree(tmp_path / "closed")
    destination = tmp_path / "archive"
    def free(path):
        return source_free if Path(path) == source else destination_free
    with pytest.raises(OSError, match="free space"):
        create_closed_archive(source, destination, max_part_bytes=2048, free_bytes=free,
                              minimum_headroom_bytes=10, minimum_source_headroom_bytes=10)
    assert not destination.exists()


def test_interrupted_archive_build_has_no_manifest_or_publishable_destination(tmp_path):
    source = _tree(tmp_path / "closed")
    calls = []
    def stop(_size):
        calls.append(True)
        raise InterruptedError("synthetic source interruption")
    with pytest.raises(InterruptedError):
        create_closed_archive(source, tmp_path / "archive", max_part_bytes=2048, before_write=stop)
    assert calls and not (tmp_path / "archive").exists()
    assert not list(tmp_path.glob(".archive.*.partial/manifest.json"))


def test_archive_projection_is_conservative_and_rejects_invalid_inputs():
    assert projected_archive_upper_bound(1000, 1) > 1000
    with pytest.raises(ValueError):
        projected_archive_upper_bound(-1, 1)
