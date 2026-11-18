from __future__ import annotations

import time

import pytest

from cloud_network_benchmark.lifecycle_remote import _wait_for_stabilization


class Deadline:
    def __init__(self, expires_at: float):
        self.expires_at = expires_at

    def remaining(self, clock):
        return self.expires_at - clock()


def test_legacy_two_read_boundary_race_reproduces_negative_sleep() -> None:
    samples = iter((59.9999, 60.0001))
    with pytest.raises(ValueError, match="sleep length must be non-negative"):
        while next(samples) < 60.0:
            remaining = 60.0 - next(samples)
            time.sleep(min(1.0, remaining))


def test_stabilization_wait_handles_clock_crossing_boundary_without_negative_sleep() -> None:
    samples = iter((59.9999, 60.0001))
    sleeps: list[float] = []

    _wait_for_stabilization(wait_until=60.0, deadline=Deadline(120.0),
        clock=lambda: next(samples), cancelled=lambda: False, sleep=sleeps.append)

    assert sleeps == [pytest.approx(0.0001)]
    assert all(duration >= 0 for duration in sleeps)
    assert 60.0001 >= 60.0


def test_stabilization_wait_still_fails_closed_on_cancellation() -> None:
    with pytest.raises(InterruptedError, match="cancellation"):
        _wait_for_stabilization(wait_until=60.0, deadline=Deadline(120.0),
            clock=lambda: 0.0, cancelled=lambda: True, sleep=lambda _duration: None)


def test_stabilization_wait_still_fails_closed_on_expired_cap() -> None:
    with pytest.raises(TimeoutError, match="original provider cap expired"):
        _wait_for_stabilization(wait_until=60.0, deadline=Deadline(60.0),
            clock=lambda: 60.0, cancelled=lambda: False, sleep=lambda _duration: None)
