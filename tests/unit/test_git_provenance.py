import subprocess
from pathlib import Path

from cloud_network_benchmark.manifests import RepositoryGitProvenance


def git(path: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(path), *args], check=True, text=True, capture_output=True).stdout.strip()


def test_snapshot_hashes_staged_unstaged_and_untracked_without_export(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    (repo / "tracked.txt").write_text("initial\n")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-qm", "initial")
    provenance = RepositoryGitProvenance()
    clean = provenance.snapshot(repo)
    assert clean.commit.value and clean.dirty.value is False
    (repo / "tracked.txt").write_text("staged secret bytes\n")
    git(repo, "add", "tracked.txt")
    (repo / "tracked.txt").write_text("unstaged secret bytes\n")
    (repo / "new.txt").write_text("untracked secret bytes\n")
    first = provenance.snapshot(repo)
    second = provenance.snapshot(repo)
    assert first.dirty.value is True
    assert first.diff_sha256.value == second.diff_sha256.value
    assert first.diff_sha256.value != clean.diff_sha256.value
    assert "secret bytes" not in first.model_dump_json()


def test_staged_change_cancelled_by_worktree_revert_is_still_dirty(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    tracked = repo / "tracked.txt"
    tracked.write_text("base\n")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-qm", "base")
    clean_hash = RepositoryGitProvenance().snapshot(repo).diff_sha256.value
    tracked.write_text("staged change\n")
    git(repo, "add", "tracked.txt")
    tracked.write_text("base\n")
    assert git(repo, "diff", "HEAD") == ""
    snapshot = RepositoryGitProvenance().snapshot(repo)
    assert snapshot.dirty.value is True
    assert snapshot.diff_sha256.value != clean_hash


def test_snapshot_supports_detached_and_packed_refs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    (repo / "file").write_text("v\n")
    git(repo, "add", "file")
    git(repo, "commit", "-qm", "initial")
    expected = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", "refs/heads/packed", expected)
    git(repo, "pack-refs", "--all")
    assert RepositoryGitProvenance().snapshot(repo).commit.value == expected
    git(repo, "checkout", "--detach", "-q", expected)
    assert RepositoryGitProvenance().snapshot(repo).commit.value == expected


def test_snapshot_supports_real_linked_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    (repo / "file").write_text("v\n")
    git(repo, "add", "file")
    git(repo, "commit", "-qm", "initial")
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "-qb", "feature", str(linked))
    snapshot = RepositoryGitProvenance().snapshot(linked)
    assert snapshot.commit.value == git(repo, "rev-parse", "HEAD")
    assert snapshot.dirty.value is False
def test_unavailable_repository_is_explicit(tmp_path: Path) -> None:
    snapshot = RepositoryGitProvenance().snapshot(tmp_path)
    assert snapshot.commit.value is None
    assert snapshot.dirty.value is None
    assert snapshot.diff_sha256.value is None


def test_raced_snapshot_becomes_explicitly_unavailable(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    tracked = repo / "tracked.txt"
    tracked.write_text("base\n")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-qm", "base")

    class RacedSnapshot(RepositoryGitProvenance):
        calls = 0

        def _capture_working_state(self, repository: Path):
            captured = super()._capture_working_state(repository)
            self.calls += 1
            if self.calls == 1:
                tracked.write_text("changed during snapshot\n")
            return captured

    snapshot = RacedSnapshot().snapshot(repo)
    assert snapshot.dirty.value is None
    assert "RuntimeError" in (snapshot.dirty.unavailable_reason or "")


def test_malformed_observed_commit_becomes_unavailable_evidence(tmp_path: Path) -> None:
    class MalformedCommit(RepositoryGitProvenance):
        def commit(self, repository: Path):
            del repository
            from cloud_network_benchmark.contracts import StringEvidence
            return StringEvidence(value="malformed")

    snapshot = MalformedCommit().snapshot(tmp_path)
    assert snapshot.commit.value is None
    assert "malformed" in (snapshot.commit.unavailable_reason or "")
    assert snapshot.dirty.value is None and snapshot.diff_sha256.value is None
