import json
from pathlib import Path

import pytest

from cloud_network_benchmark.errors import CollisionError, PersistenceError
from cloud_network_benchmark.contracts import FailureEvidence
from cloud_network_benchmark.manifests import (
    StaticGitProvenance,
    initialize_campaign,
    update_child_cleanup,
    update_child_evidence,
    update_child_execution,
)
from cloud_network_benchmark.contracts import StructuredEvidence


class FailOnce:
    def __init__(self, point: str) -> None:
        self.point = point
        self.fired = False

    def __call__(self, point: str) -> None:
        if point == self.point and not self.fired:
            self.fired = True
            raise OSError(f"injected failure at {point}")


class FailEachOnce:
    def __init__(self, *points: str) -> None:
        self.remaining = set(points)

    def __call__(self, point: str) -> None:
        if point in self.remaining:
            self.remaining.remove(point)
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


@pytest.mark.parametrize(
    "point",
    [
        "before_parent_manifest_reservation",
        "after_parent_manifest_reservation",
        *[f"after_child_manifest_reservation:{provider}" for provider in ("aws", "azure", "gcp")],
        *[f"after_result_directory:{provider}" for provider in ("aws", "azure", "gcp")],
        *[f"after_config_snapshot:{provider}" for provider in ("aws", "azure", "gcp")],
    ],
)
def test_failure_injection_preserves_every_assigned_manifest(repository_root: Path, tmp_path: Path, frozen_clock: object, deterministic_token: object, fake_git_commits: dict[str, str], point: str) -> None:
    root = tmp_path / point
    with pytest.raises(PersistenceError):
        initialize(repository_root, root, frozen_clock, deterministic_token, fake_git_commits, FailOnce(point))
    manifest_paths = list((root / "manifests").glob("*.json")) if (root / "manifests").exists() else []
    child_manifests = [path for path in manifest_paths if json.loads(path.read_text())["manifest_type"] == "child_run"]
    if point == "before_parent_manifest_reservation":
        assert list((root / "manifests").glob("*.json")) == []
        return
    parent_paths = [path for path in manifest_paths if path not in child_manifests]
    assert len(parent_paths) == 1
    parent = json.loads(parent_paths[0].read_text())
    assert parent["aggregate_state"] == "failed"
    assert {item["run_id"] for item in parent["child_runs"]} == {
        json.loads(path.read_text())["run_id"] for path in child_manifests
    }
    for path in child_manifests:
        assert json.loads(path.read_text())["execution_state"] == "failed"


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
    parent = json.loads(Path(result.parent_manifest_path).read_text())
    assert parent["aggregate_state"] == "partially_complete"
    assert len(parent["lifecycle_events"]) == 4


def test_atomic_evidence_update_replaces_unavailable_values(repository_root: Path, tmp_path: Path, frozen_clock: object, deterministic_token: object, fake_git_commits: dict[str, str]) -> None:
    result = initialize(repository_root, tmp_path / "results", frozen_clock, deterministic_token, fake_git_commits)
    path = Path(result.children[0].manifest_path)
    observed = StructuredEvidence(value={"vm_a": {"mtu": 1500}, "vm_b": {"mtu": 1500}})
    updated = update_child_evidence(path, frozen_clock(), vm_metadata=observed)
    assert updated.vm_metadata.value["vm_a"]["mtu"] == 1500
    assert json.loads(path.read_text())["vm_metadata"]["unavailable_reason"] is None


def test_persistence_error_reports_preserved_manifests(repository_root: Path, tmp_path: Path, frozen_clock: object, deterministic_token: object, fake_git_commits: dict[str, str]) -> None:
    root = tmp_path / "recovery-details"
    with pytest.raises(PersistenceError) as caught:
        initialize(repository_root, root, frozen_clock, deterministic_token, fake_git_commits, FailOnce("after_child_manifest_reservation:azure"))
    details = caught.value.details
    assert details["parent_manifest_path"]
    assert len(details["preserved_child_manifest_paths"]) == 2
    assert all(Path(path).exists() for path in details["preserved_child_manifest_paths"])


def test_cli_renders_structured_recovery_details(monkeypatch: pytest.MonkeyPatch, capsys: object) -> None:
    from cloud_network_benchmark.cli import main

    error = PersistenceError(
        "injected",
        {
            "campaign_id": "campaign",
            "parent_manifest_path": "results/manifests/campaign.json",
            "preserved_child_manifest_paths": ["results/manifests/campaign-aws.json"],
            "recovery_failures": [],
        },
    )
    monkeypatch.setattr("cloud_network_benchmark.cli.initialize_campaign", lambda *_args, **_kwargs: (_ for _ in ()).throw(error))
    assert main(["init", "--config", "configs/tests/exp-900-single-provider.yaml", "--format", "json"]) == 5
    payload = json.loads(capsys.readouterr().err)
    assert payload["error"]["details"]["preserved_child_manifest_paths"] == ["results/manifests/campaign-aws.json"]
    monkeypatch.setattr("cloud_network_benchmark.cli.initialize_campaign", lambda *_args, **_kwargs: (_ for _ in ()).throw(error))
    assert main(["init", "--config", "configs/tests/exp-900-single-provider.yaml", "--format", "human"]) == 5
    human = capsys.readouterr().err
    assert "preserved_child_manifest_paths" in human
    assert "campaign-aws.json" in human


def test_recovery_fallback_preserves_valid_failed_manifest(repository_root: Path, tmp_path: Path, frozen_clock: object, deterministic_token: object, fake_git_commits: dict[str, str]) -> None:
    root = tmp_path / "recovery-fallback"
    fault = FailEachOnce("after_child_manifest_reservation:aws", "before_recovery_update:aws")
    with pytest.raises(PersistenceError) as caught:
        initialize(repository_root, root, frozen_clock, deterministic_token, fake_git_commits, fault)
    child_path = next((root / "manifests").glob("*-aws.json"))
    assert json.loads(child_path.read_text())["execution_state"] == "failed"
    assert caught.value.details["recovery_failures"]
