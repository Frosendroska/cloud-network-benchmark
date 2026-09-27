import json
from pathlib import Path

import pytest

from cloud_network_benchmark.errors import CollisionError, PersistenceError
from cloud_network_benchmark.contracts import FailureEvidence
from cloud_network_benchmark.manifests import StaticGitProvenance, initialize_campaign, update_child_cleanup, update_child_execution


class FailOnce:
    def __init__(self, point: str) -> None:
        self.point = point
        self.fired = False

    def __call__(self, point: str) -> None:
        if point == self.point and not self.fired:
            self.fired = True
            raise OSError(f"injected failure at {point}")


def initialize(repository_root: Path, root: Path, frozen_clock: object, deterministic_token: object, fake_git_commits: dict[str, str], fault: object = lambda _: None):
    return initialize_campaign(
        repository_root / "configs/experiments/exp-001-multi-provider.yaml",
        repository_root,
        root,
        frozen_clock,
        deterministic_token,
        StaticGitProvenance(fake_git_commits["implementation"]),
        StaticGitProvenance(fake_git_commits["design"]),
        fault,
    )


def test_initializes_linked_three_provider_evidence(repository_root: Path, tmp_path: Path, frozen_clock: object, deterministic_token: object, fake_git_commits: dict[str, str]) -> None:
    result = initialize(repository_root, tmp_path / "results", frozen_clock, deterministic_token, fake_git_commits)
    assert len(result.children) == 3
    parent = json.loads(Path(result.parent_manifest_path).read_text())
    assert {child["provider"] for child in parent["child_runs"]} == {"aws", "azure", "gcp"}
    source = (repository_root / "configs/experiments/exp-001-multi-provider.yaml").read_bytes()
    for child in result.children:
        assert (Path(child.result_path) / "config.yaml").read_bytes() == source


@pytest.mark.parametrize("point", ["before_manifest_reservation", "after_manifest_reservation", "before_result_directory", "after_result_directory", "before_config_snapshot", "after_config_snapshot"])
def test_failure_injection_preserves_every_assigned_manifest(repository_root: Path, tmp_path: Path, frozen_clock: object, deterministic_token: object, fake_git_commits: dict[str, str], point: str) -> None:
    root = tmp_path / point
    with pytest.raises(PersistenceError):
        initialize(repository_root, root, frozen_clock, deterministic_token, fake_git_commits, FailOnce(point))
    manifests = list((root / "manifests").glob("*-aws.json")) if (root / "manifests").exists() else []
    if point == "before_manifest_reservation":
        assert manifests == []
    else:
        assert len(manifests) == 1
        assert json.loads(manifests[0].read_text())["execution_state"] == "failed"


def test_collision_never_overwrites(repository_root: Path, tmp_path: Path, frozen_clock: object, deterministic_token: object, fake_git_commits: dict[str, str]) -> None:
    root = tmp_path / "results"
    initialize(repository_root, root, frozen_clock, deterministic_token, fake_git_commits)
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(CollisionError):
        initialize(repository_root, root, frozen_clock, deterministic_token, fake_git_commits)
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_atomic_updates_preserve_execution_and_cleanup_failures(repository_root: Path, tmp_path: Path, frozen_clock: object, deterministic_token: object, fake_git_commits: dict[str, str]) -> None:
    result = initialize(repository_root, tmp_path / "results", frozen_clock, deterministic_token, fake_git_commits)
    path = Path(result.children[0].manifest_path)
    now = frozen_clock()
    execution_failure = FailureEvidence(category="benchmark", message="phase failed", occurred_at=now)
    updated = update_child_execution(path, "failed", now, execution_failure)
    cleanup_failure = FailureEvidence(category="cleanup", message="destroy failed", occurred_at=now)
    update_child_cleanup(path, "attempted", now)
    final = update_child_cleanup(path, "failed", now, cleanup_failure)
    assert final.failure == updated.failure
    assert final.failure.message == "phase failed"
    assert final.cleanup_failure.message == "destroy failed"
