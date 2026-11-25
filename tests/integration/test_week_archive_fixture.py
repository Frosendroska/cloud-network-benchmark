"""Smoke-test the generated F09 archive-pending fixture before campaign replay."""
from __future__ import annotations

import hashlib
import json

from cloud_network_benchmark.archive_replay import _inventory
from tests.integration.week_archive_fixture import build_single_provider_week_archive_fixture


def test_f09_fixture_preserves_real_pending_source_before_restore(tmp_path, monkeypatch, repository_root):
    fixture = build_single_provider_week_archive_fixture(
        tmp_path=tmp_path, monkeypatch=monkeypatch, repository_root=repository_root)
    try:
        assert fixture.capsule["schema_version"] == 2
        assert fixture.capsule["status"] == "archive_pending_replay"
        assert fixture.receipt["status"] == "verified"
        assert fixture.receipt["verified"] is True
        assert set(fixture.capsule["archives"]) == {"aws"}
        assert fixture.restore_kwargs["capsule_path"] == fixture.capsule_path
        assert fixture.capsule_sha256 == hashlib.sha256(fixture.capsule_path.read_bytes()).hexdigest()
        assert fixture.backend.objects
        assert fixture.backend.deleted == []

        parent = json.loads(fixture.parent_path.read_text(encoding="utf-8"))
        child_path = fixture.child_manifest_paths["aws"]
        child = json.loads(child_path.read_text(encoding="utf-8"))
        assert parent == fixture.capsule["parent_manifest"]
        assert child == fixture.capsule["child_manifests"]["aws"]
        assert _inventory(fixture.result_roots["aws"]) == fixture.capsule[
            "controller_context_inventory"]["aws"]
        validation = fixture.result_roots["aws"] / "validation"
        assert not (validation / "input.json").exists()
        assert not (validation / "result.json").exists()

        assert fixture.lifecycle.provider_results["aws"].cleanup_state == "succeeded"
        ledger_state = fixture.built["ledger"].inspect()
        reservation_id = fixture.lifecycle.reservation_evidence.reservation_id.value
        assert reservation_id in ledger_state["reservations"]
    finally:
        fixture.cleanup()
