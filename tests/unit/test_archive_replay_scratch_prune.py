from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pytest

from cloud_network_benchmark.archive_replay import (
    _archive_scratch_inventory,
    _preflight_scratch,
    _remove_verified_scratch,
    _check_fresh_replay_quota,
)


def _row(path, data):
    return {"path": path, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def test_prune_removes_only_exact_generated_inventory_and_preserves_neighbor(tmp_path):
    scratch = tmp_path / "owned-scratch"
    scratch.mkdir()
    (scratch / "native.csv").write_bytes(b"native")
    neighbor = tmp_path / "preexisting.csv"
    neighbor.write_bytes(b"keep")
    removed = _remove_verified_scratch(scratch, [_row("native.csv", b"native")])
    assert removed == 6
    assert not scratch.exists()
    assert neighbor.read_bytes() == b"keep"


@pytest.mark.parametrize("mutation", ["extra", "tampered", "symlink"])
def test_prune_preflight_rejects_unknown_or_tampered_tree_before_unlink(tmp_path, mutation):
    scratch = tmp_path / "owned-scratch"
    scratch.mkdir()
    (scratch / "native.csv").write_bytes(b"native")
    if mutation == "extra":
        (scratch / "unexpected.txt").write_bytes(b"do not erase")
    elif mutation == "tampered":
        (scratch / "native.csv").write_bytes(b"changed")
    else:
        (scratch / "link").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ValueError):
        _preflight_scratch(scratch, [_row("native.csv", b"native")])
    assert scratch.exists()


def test_archive_scratch_inventory_is_manifest_bound():
    part = b"compressed-part"
    files = [{"path": "vm_a/native.csv", "size_bytes": 4, "sha256": "a" * 64}]
    parts = [{"path": "archive.tar.gz.part-00001", "size_bytes": len(part),
        "sha256": hashlib.sha256(part).hexdigest()}]
    manifest = {"provider": "aws", "experiment_id": "exp", "run_id": "run",
        "roles": [{"role": "vm_a", "files": files, "parts": parts,
            "archive_bytes": len(part), "archive_sha256": "b" * 64}]}
    rows = _archive_scratch_inventory(manifest)
    assert [row["path"] for row in rows] == [
        "manifest.json", "vm_a/archive.tar.gz.part-00001", "vm_a/manifest.json"]
    assert all(len(row["sha256"]) == 64 for row in rows)


def test_opt_in_quota_requires_hash_bound_fresh_measurement(tmp_path):
    path = tmp_path / "quota.json"
    bounds = {"metadata_headroom_bytes": 10, "projected_other_growth_bytes": 20, "reserve_bytes": 30}
    payload = {"measured_free_bytes": 1000, **bounds,
        "measured_at": datetime.now(timezone.utc).isoformat()}
    path.write_text(json.dumps(payload), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    _check_fresh_replay_quota(free_bytes=1000, evidence_path=path,
        evidence_sha256=digest, **bounds, now=datetime.now(timezone.utc))
    payload["measured_at"] = "2020-01-01T00:00:00+00:00"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="15 minutes"):
        _check_fresh_replay_quota(free_bytes=1000, evidence_path=path,
            evidence_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), **bounds,
            now=datetime.now(timezone.utc))
