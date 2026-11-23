from __future__ import annotations

import fcntl
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cloud_network_benchmark.precreation_cleanup import _assert_terminal_factory_and_idle_stage


def _fixture(tmp_path: Path):
    stage_id, entry_id = "acceptance-stage", "V-S1"
    state_root = tmp_path / "state"
    stage_dir = state_root / stage_id
    stage_dir.mkdir(parents=True)
    (state_root / ".campaign-driver.lock").touch()
    (stage_dir / "campaign-journal.lock").touch()
    (stage_dir / "campaign-journal.json").write_text(json.dumps({
        "entries": {entry_id: {"state": "failed", "result": {"cleanup_open": True}}}}))
    draft = {"stages": [{"stage_id": stage_id, "stage": "acceptance",
        "state_root": str(state_root)}]}
    recovery = SimpleNamespace(initialization_failure="factory failed")
    return draft, recovery, state_root, stage_id, entry_id


def test_precreation_guard_rejects_live_campaign_factory_owner(tmp_path):
    draft, recovery, root, stage_id, entry_id = _fixture(tmp_path)
    with (root / ".campaign-driver.lock").open("r") as owner:
        fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="still active"):
            _assert_terminal_factory_and_idle_stage(recovery=recovery, draft=draft,
                stage_id=stage_id, entry_id=entry_id)


def test_precreation_guard_holds_stage_locks_for_the_full_recovery(tmp_path):
    draft, recovery, _, stage_id, entry_id = _fixture(tmp_path)
    descriptors = _assert_terminal_factory_and_idle_stage(recovery=recovery, draft=draft,
        stage_id=stage_id, entry_id=entry_id, hold=True)
    try:
        assert len(descriptors) == 2
        with pytest.raises(BlockingIOError):
            fd = __import__("os").open(draft["stages"][0]["state_root"] + "/.campaign-driver.lock", __import__("os").O_RDONLY)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                __import__("os").close(fd)
    finally:
        for fd in descriptors:
            fcntl.flock(fd, fcntl.LOCK_UN)
            __import__("os").close(fd)
