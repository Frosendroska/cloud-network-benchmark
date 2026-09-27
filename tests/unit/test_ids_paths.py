import json
from pathlib import Path

import pytest

from cloud_network_benchmark.contracts import Provider
from cloud_network_benchmark.errors import CollisionError
from cloud_network_benchmark.ids import candidate_campaign_id, candidate_run_id
from cloud_network_benchmark.paths import allocate_paths, preflight, reserve_json


def test_deterministic_candidate_ids(frozen_clock: object, deterministic_token: object) -> None:
    campaign_id = candidate_campaign_id("EXP-001", frozen_clock, deterministic_token)
    assert campaign_id == "20260927T120000Z-exp-001-abc123"
    assert candidate_run_id(campaign_id, Provider.AWS) == f"{campaign_id}-aws"


def test_manifest_reservation_is_exclusive(results_root: Path) -> None:
    paths = allocate_paths(results_root, "campaign", {Provider.AWS: "campaign-aws"})
    preflight(paths)
    reserve_json(paths.child_manifests[Provider.AWS], {"run_id": "campaign-aws"})
    original = paths.child_manifests[Provider.AWS].read_bytes()
    with pytest.raises(CollisionError):
        reserve_json(paths.child_manifests[Provider.AWS], {"run_id": "replacement"})
    assert paths.child_manifests[Provider.AWS].read_bytes() == original


def test_preflight_detects_any_occupied_destination(results_root: Path) -> None:
    paths = allocate_paths(results_root, "campaign", {Provider.AWS: "campaign-aws"})
    paths.child_results[Provider.AWS].mkdir()
    with pytest.raises(CollisionError):
        preflight(paths)
