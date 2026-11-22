from __future__ import annotations

import json
from pathlib import Path
import threading

import pytest

from cloud_network_benchmark.post_measurement_barrier import PostMeasurementBarrier


def _spec(root: Path, count: int = 6):
    participants = [{"entry_id": f"entry-{i}", "provider": ("aws", "azure", "gcp")[i % 3],
        "run_id": f"run-{i}"} for i in range(count)]
    return {"schema_version": 1, "barrier_id": "week-slot-001",
        "barrier_root": str(root), "participants": participants}


class Deadline:
    def __init__(self, values=(100,)):
        self.values = iter(values)

    def remaining(self, _clock, *, cleanup=False):
        return next(self.values, 0)


def test_six_actual_terminal_signals_release_barrier_and_bind_state(tmp_path):
    barrier = PostMeasurementBarrier(_spec(tmp_path))
    participants = barrier.spec["participants"]
    threads = [threading.Thread(target=barrier.signal_terminal,
        args=(participant,), kwargs={"succeeded": True}) for participant in participants]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    released = barrier.wait_released(deadline=Deadline(), clock=lambda: 0)
    assert released["state"] == "released"
    assert len(released["terminal"]) == 6
    assert barrier.signal_terminal(participants[0], succeeded=True)["state"] == "released"


def test_failed_participant_aborts_group_without_release(tmp_path):
    barrier = PostMeasurementBarrier(_spec(tmp_path))
    barrier.signal_terminal(barrier.spec["participants"][0], succeeded=False)
    with pytest.raises(RuntimeError, match="aborted"):
        barrier.wait_released(deadline=Deadline(), clock=lambda: 0)
    assert json.loads(barrier.path.read_text())["state"] == "aborted"


def test_barrier_cancellation_and_original_deadline_abort_without_extension(tmp_path):
    barrier = PostMeasurementBarrier(_spec(tmp_path))
    with pytest.raises(InterruptedError, match="cancelled"):
        barrier.wait_released(deadline=Deadline(), cancelled=lambda: True)
    assert json.loads(barrier.path.read_text())["abort_reason"] == "participant_cancelled"

    other = PostMeasurementBarrier(_spec(tmp_path / "second"))
    with pytest.raises(TimeoutError, match="original deadline"):
        other.wait_released(deadline=Deadline((0,)))
    assert json.loads(other.path.read_text())["state"] == "aborted"


def test_foreign_replaced_or_symlinked_barrier_state_fails_closed(tmp_path):
    spec = _spec(tmp_path)
    barrier = PostMeasurementBarrier(spec)
    changed = _spec(tmp_path)
    changed["participants"][0]["run_id"] = "foreign-run"
    with pytest.raises(ValueError, match="conflicts"):
        PostMeasurementBarrier(changed)
    barrier.path.unlink()
    barrier.path.symlink_to(tmp_path / "foreign.json")
    with pytest.raises(ValueError, match="symlinks"):
        barrier.signal_terminal(barrier.spec["participants"][0], succeeded=True)


def test_barrier_rejects_foreign_participant_and_conflicting_duplicate(tmp_path):
    barrier = PostMeasurementBarrier(_spec(tmp_path, count=1))
    foreign = {"entry_id": "other", "provider": "aws", "run_id": "run-other"}
    with pytest.raises(ValueError, match="outside"):
        barrier.signal_terminal(foreign, succeeded=True)
    participant = barrier.spec["participants"][0]
    barrier.signal_terminal(participant, succeeded=True)
    with pytest.raises(ValueError, match="conflicts"):
        barrier.signal_terminal(participant, succeeded=False)
