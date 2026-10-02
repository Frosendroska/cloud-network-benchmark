import json
import shutil
from datetime import timedelta
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker

from cloud_network_benchmark.errors import CollisionError, PersistenceError, ValidationError
from cloud_network_benchmark.contracts import FailureEvidence, StringEvidence
from cloud_network_benchmark.manifests import (
    RepositoryGitProvenance,
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


class CaptureProvenance:
    def __init__(self, commit: str) -> None:
        self.commit_value = commit
        self.repositories: list[Path] = []

    def commit(self, repository: Path) -> StringEvidence:
        self.repositories.append(repository)
        return StringEvidence(value=self.commit_value)


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
    ("path", "placement_scenario"),
    [
        (("provider_configs", "aws", "regions", "vm_a"), False),
        (("provider_configs", "aws", "zones", "vm_b"), False),
        (("provider_configs", "aws", "vm_shape"), False),
        (("provider_configs", "aws", "image"), False),
        (("provider_configs", "aws", "connection_user"), False),
        (("provider_configs", "aws", "placement", "name"), True),
    ],
)
def test_whitespace_provider_values_fail_before_id_or_result_allocation(
    repository_root: Path, tmp_path: Path, path: tuple[str, ...], placement_scenario: bool
) -> None:
    data = yaml.safe_load((repository_root / "configs/tests/exp-900-single-provider.yaml").read_text())
    if placement_scenario:
        data["scenario"] = "placement_optimization"
        data["provider_configs"]["aws"]["placement"] = {
            "kind": "cluster_placement_group",
            "name": " \t ",
        }
    else:
        target = data
        for component in path[:-1]:
            target = target[component]
        target[path[-1]] = " \t "

    root = tmp_path / "repo"
    config_path = root / "configs/tests/whitespace.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    results_root = tmp_path / "results"

    def unexpected_allocation() -> object:
        raise AssertionError("invalid config reached ID allocation")

    with pytest.raises(ValidationError):
        initialize_campaign(
            config_path,
            root,
            results_root,
            clock=unexpected_allocation,
            token_source=unexpected_allocation,
        )
    assert not results_root.exists()


def test_initialization_resolves_thesis_root_from_linked_worktree(
    repository_root: Path, tmp_path: Path, frozen_clock: object, deterministic_token: object,
    fake_git_commits: dict[str, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("THESIS_ROOT", raising=False)
    main_root = tmp_path / "main" / "cloud-network-benchmark"
    common_git = main_root / ".git"
    worktree_root = tmp_path / "managed-worktree" / "cloud-network-benchmark"
    linked_git = common_git / "worktrees" / "feature"
    linked_git.mkdir(parents=True)
    worktree_root.mkdir(parents=True)
    (worktree_root / ".git").write_text(f"gitdir: {linked_git}\n", encoding="utf-8")
    (linked_git / "commondir").write_text("../..\n", encoding="utf-8")
    config_relative = Path("configs/experiments/exp-001-multi-provider.yaml")
    config_path = worktree_root / config_relative
    config_path.parent.mkdir(parents=True)
    shutil.copyfile(repository_root / config_relative, config_path)
    thesis_root = tmp_path / "main" / "Thesis"
    thesis_root.mkdir()
    design_git = CaptureProvenance(fake_git_commits["design"])

    result = initialize_campaign(
        config_path, worktree_root, tmp_path / "results", frozen_clock, deterministic_token,
        StaticGitProvenance(fake_git_commits["implementation"]), design_git,
    )

    assert design_git.repositories == [thesis_root]
    parent = json.loads(Path(result.parent_manifest_path).read_text())
    assert parent["config_provenance"]["design_git_commit"]["value"] == fake_git_commits["design"]


def test_thesis_root_environment_override_takes_precedence(
    repository_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cloud_network_benchmark.manifests import thesis_repository_root

    configured = tmp_path / "configured-thesis"
    monkeypatch.setenv("THESIS_ROOT", str(configured))
    assert thesis_repository_root(repository_root) == configured


@pytest.mark.parametrize("ref_storage", ["loose", "packed"])
def test_implementation_provenance_reads_shared_linked_worktree_ref(tmp_path: Path, ref_storage: str) -> None:
    repository = tmp_path / "worktree"
    common_git = tmp_path / "main" / ".git"
    linked_git = common_git / "worktrees" / "feature"
    repository.mkdir()
    linked_git.mkdir(parents=True)
    (repository / ".git").write_text(f"gitdir: {linked_git}\n", encoding="utf-8")
    (linked_git / "HEAD").write_text("ref: refs/heads/feature\n", encoding="utf-8")
    (linked_git / "commondir").write_text("../..\n", encoding="utf-8")
    expected_commit = "a" * 40
    if ref_storage == "loose":
        (common_git / "refs/heads").mkdir(parents=True)
        (common_git / "refs/heads/feature").write_text(f"{expected_commit}\n", encoding="utf-8")
    else:
        (common_git / "packed-refs").write_text(
            f"# pack-refs with: peeled fully-peeled\n{expected_commit} refs/heads/feature\n",
            encoding="utf-8",
        )

    evidence = RepositoryGitProvenance().commit(repository)

    assert evidence.value == expected_commit
    assert evidence.unavailable_reason is None


def test_implementation_provenance_reports_genuinely_unavailable_repository(tmp_path: Path) -> None:
    evidence = RepositoryGitProvenance().commit(tmp_path / "not-a-repository")

    assert evidence.value is None
    assert evidence.unavailable_reason.startswith("Git commit unavailable:")


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
    parent_schema = json.loads(
        (repository_root / "specs/001-core-offline-contracts/contracts/campaign-manifest.schema.json").read_text()
    )
    Draft202012Validator(parent_schema, format_checker=FormatChecker()).validate(parent)
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


@pytest.mark.parametrize("damage", ["missing", "corrupt", "wrong_parent"])
def test_parent_aggregation_fails_closed_on_invalid_child_evidence(
    repository_root: Path,
    tmp_path: Path,
    frozen_clock: object,
    deterministic_token: object,
    fake_git_commits: dict[str, str],
    damage: str,
) -> None:
    from cloud_network_benchmark.manifests import recompute_parent_manifest

    result = initialize(repository_root, tmp_path / damage, frozen_clock, deterministic_token, fake_git_commits)
    child_path = Path(result.children[0].manifest_path)
    if damage == "missing":
        child_path.unlink()
    elif damage == "corrupt":
        child_path.write_text("{not-json", encoding="utf-8")
    else:
        child = json.loads(child_path.read_text())
        child["campaign_id"] = "different-campaign"
        child_path.write_text(json.dumps(child), encoding="utf-8")
    with pytest.raises(PersistenceError):
        recompute_parent_manifest(Path(result.parent_manifest_path), frozen_clock(), "test invalid child")


def test_parent_aggregation_resolves_the_recorded_manifest_path(
    repository_root: Path,
    tmp_path: Path,
    frozen_clock: object,
    deterministic_token: object,
    fake_git_commits: dict[str, str],
) -> None:
    from cloud_network_benchmark.manifests import recompute_parent_manifest

    result = initialize(repository_root, tmp_path / "manifest-reference", frozen_clock, deterministic_token, fake_git_commits)
    parent_path = Path(result.parent_manifest_path)
    parent = json.loads(parent_path.read_text())
    child_ref = parent["child_runs"][0]
    child_ref["manifest_path"] = str(parent_path.parent / "missing" / Path(child_ref["manifest_path"]).name)
    parent_path.write_text(json.dumps(parent), encoding="utf-8")
    with pytest.raises(PersistenceError, match="missing"):
        recompute_parent_manifest(parent_path, frozen_clock(), "must follow stored path")


def test_parent_succeeds_only_after_every_selected_child_succeeds_and_cleans_up(
    repository_root: Path,
    tmp_path: Path,
    frozen_clock: object,
    deterministic_token: object,
    fake_git_commits: dict[str, str],
) -> None:
    result = initialize(repository_root, tmp_path / "complete", frozen_clock, deterministic_token, fake_git_commits)
    now = frozen_clock()
    for child in result.children:
        path = Path(child.manifest_path)
        for state in ("provision_ready", "running", "collecting", "validating", "succeeded"):
            update_child_execution(path, state, now)
        update_child_cleanup(path, "attempted", now)
        update_child_cleanup(path, "succeeded", now)
    parent = json.loads(Path(result.parent_manifest_path).read_text())
    assert parent["aggregate_state"] == "succeeded"


def test_manifest_update_apis_reject_backward_occurrence_times(
    repository_root: Path,
    tmp_path: Path,
    frozen_clock: object,
    deterministic_token: object,
    fake_git_commits: dict[str, str],
) -> None:
    from cloud_network_benchmark.manifests import recompute_parent_manifest

    result = initialize(repository_root, tmp_path / "chronology", frozen_clock, deterministic_token, fake_git_commits)
    child_path = Path(result.children[0].manifest_path)
    past = frozen_clock() - timedelta(seconds=1)
    with pytest.raises(ValueError, match="earlier"):
        update_child_execution(child_path, "provision_ready", past)
    with pytest.raises(ValueError, match="earlier"):
        update_child_evidence(child_path, past, vm_metadata=StructuredEvidence(unavailable_reason="still unavailable"))
    with pytest.raises(ValueError, match="earlier"):
        update_child_cleanup(child_path, "attempted", past)
    with pytest.raises(ValueError, match="earlier"):
        recompute_parent_manifest(Path(result.parent_manifest_path), past, "backward")
