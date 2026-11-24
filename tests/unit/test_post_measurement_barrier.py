from __future__ import annotations

import json
from pathlib import Path
import threading
from datetime import datetime, timedelta, timezone

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


def _ready_spec(root: Path, count=6):
    spec = _spec(root, count)
    spec.pop("barrier_id")
    spec["barrier_id"] = "validation-ready-001"
    spec["start_policy"] = "all_ready"
    spec["execution_window"] = {
        "not_before_utc": "2026-11-22T10:00:00+00:00",
        "not_after_utc": "2026-11-22T11:00:00+00:00"}
    spec["ready_window_seconds"] = 600
    if count == 3:
        for index, row in enumerate(spec["participants"]):
            row["entry_id"] = "CAL-S1"
            row["provider"] = ("aws", "azure", "gcp")[index]
    else:
        for index, row in enumerate(spec["participants"]):
            row["entry_id"] = "V-S1" if index < 3 else "V-S3"
            row["provider"] = ("aws", "azure", "gcp")[index % 3]
    return spec


@pytest.mark.parametrize("count", [3, 6])
def test_ready_triggered_release_persists_one_common_utc_and_exact_identity_set(tmp_path, count):
    now = datetime(2026, 11, 22, 10, 1, tzinfo=timezone.utc)
    spec = _ready_spec(tmp_path, count)
    barrier = PostMeasurementBarrier(spec)
    latest = (now + timedelta(minutes=5)).isoformat()
    for participant in barrier.spec["participants"]:
        barrier.signal_ready(participant, latest, deadline=Deadline(), clock=lambda: 0,
            utc_now=lambda: now)
    state = barrier.wait_released(deadline=Deadline(), clock=lambda: 0, utc_now=lambda: now)
    assert state["state"] == "released"
    assert state["release_utc"] == now.isoformat()
    assert len(state["ready"]) == count
    resumed = PostMeasurementBarrier(spec)
    assert resumed.wait_released(deadline=Deadline(), clock=lambda: 0,
        utc_now=lambda: now)["release_utc"] == now.isoformat()


def test_ready_triggered_barrier_aborts_when_original_cap_cannot_fit_and_cannot_reset(tmp_path):
    now = datetime(2026, 11, 22, 10, 1, tzinfo=timezone.utc)
    spec = _ready_spec(tmp_path, 3)
    barrier = PostMeasurementBarrier(spec)
    participants = barrier.spec["participants"]
    latest = now - timedelta(seconds=1)
    for participant in participants:
        result = barrier.signal_ready(participant, latest.isoformat(), deadline=Deadline(),
            clock=lambda: 0, utc_now=lambda: now)
    assert result["state"] == "aborted"
    assert result["abort_reason"] == "original_deadline_cannot_fit_measurement"
    with pytest.raises(RuntimeError, match="aborted"):
        barrier.wait_released(deadline=Deadline(), utc_now=lambda: now)


def test_ready_triggered_barrier_allows_readiness_spread_within_original_bound(tmp_path):
    first = datetime(2026, 11, 22, 10, 0, tzinfo=timezone.utc)
    last = first + timedelta(minutes=5)
    barrier = PostMeasurementBarrier(_ready_spec(tmp_path, 6))
    participants = barrier.spec["participants"]
    latest = (last + timedelta(minutes=10)).isoformat()
    for participant in participants[:3]:
        barrier.signal_ready(participant, latest, deadline=Deadline(), clock=lambda: 0,
            utc_now=lambda: first)
    for participant in participants[3:]:
        state = barrier.signal_ready(participant, latest, deadline=Deadline(), clock=lambda: 0,
            utc_now=lambda: last)
    assert state["state"] == "released"
    assert state["first_ready_utc"] == first.isoformat()
    assert state["release_utc"] == last.isoformat()


def test_ready_triggered_barrier_freezes_window_and_policy_digest(tmp_path):
    spec = _ready_spec(tmp_path, 3)
    PostMeasurementBarrier(spec)
    changed = _ready_spec(tmp_path, 3)
    changed["execution_window"]["not_after_utc"] = "2026-11-22T11:30:00+00:00"
    with pytest.raises(ValueError, match="conflicts"):
        PostMeasurementBarrier(changed)


def test_ready_triggered_barrier_accepts_finite_window_over_six_hours(tmp_path):
    spec = _ready_spec(tmp_path / "wide", 3)
    spec["barrier_root"] = str(tmp_path / "wide" / "barrier")
    spec["barrier_id"] = "validation-ready-wide-001"
    spec["execution_window"]["not_after_utc"] = "2026-11-23T10:00:00+00:00"
    barrier = PostMeasurementBarrier(spec)
    assert barrier.spec["execution_window"]["not_after_utc"] == "2026-11-23T10:00:00+00:00"
