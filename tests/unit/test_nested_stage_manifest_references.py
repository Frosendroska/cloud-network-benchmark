from pathlib import Path

import pytest

from cloud_network_benchmark.errors import PersistenceError
from cloud_network_benchmark.manifests import (
    _expected_relative_result_path,
    _resolve_reference_path,
)


def test_child_manifest_reference_accepts_nested_stage_and_legacy_flat_layout(tmp_path):
    root = tmp_path / "source"
    nested_parent = root / "results" / "acceptance-20261124T120000Z-a1b2c3d4" / "manifests" / "parent.json"
    nested_ref = "results/acceptance-20261124T120000Z-a1b2c3d4/manifests/run.json"
    assert _resolve_reference_path(nested_parent, nested_ref) == root / nested_ref

    flat_parent = root / "results" / "manifests" / "parent.json"
    flat_ref = "results/manifests/run.json"
    assert _resolve_reference_path(flat_parent, flat_ref) == root / flat_ref
    assert _expected_relative_result_path(flat_parent, "run") == Path("results/raw/run")
    assert _expected_relative_result_path(nested_parent, "run") == Path(
        "results/acceptance-20261124T120000Z-a1b2c3d4/raw/run")


@pytest.mark.parametrize("reference", [
    "results/../outside/manifests/run.json",
    "results/stage/raw/run",
    "other/stage/manifests/run.json",
])
def test_child_manifest_reference_rejects_noncanonical_or_traversing_layout(tmp_path, reference):
    parent = tmp_path / "source" / "results" / "stage" / "manifests" / "parent.json"
    with pytest.raises(PersistenceError):
        _resolve_reference_path(parent, reference)


def test_child_manifest_reference_rejects_symlinked_stage_directory(tmp_path):
    root = tmp_path / "source"
    (root / "results").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "results" / "stage").symlink_to(outside, target_is_directory=True)
    parent = root / "results" / "stage" / "manifests" / "parent.json"
    with pytest.raises(PersistenceError, match="symlink"):
        _resolve_reference_path(parent, "results/stage/manifests/run.json")


@pytest.mark.parametrize("reference", [
    "results/other-stage/manifests/run.json",
    "results/manifests/run.json",
])
def test_child_manifest_reference_rejects_wrong_or_mixed_stage_root(tmp_path, reference):
    parent = tmp_path / "source" / "results" / "stage" / "manifests" / "parent.json"
    with pytest.raises(PersistenceError):
        _resolve_reference_path(parent, reference)
