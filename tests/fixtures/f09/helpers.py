from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Callable


@dataclass
class FakeClock:
    """Independent deterministic UTC and monotonic clocks for offline tests."""

    utc: datetime = field(default_factory=lambda: datetime(2026, 10, 23, tzinfo=timezone.utc))
    monotonic: float = 100.0

    def now(self) -> datetime:
        return self.utc

    def tick(self, seconds: float) -> None:
        self.monotonic += seconds
        self.utc += timedelta(seconds=seconds)


@dataclass
class Trace:
    events: list[tuple[str, str]] = field(default_factory=list)
    lock: Lock = field(default_factory=Lock)

    def add(self, provider: str, stage: str) -> None:
        with self.lock:
            self.events.append((provider, stage))


class FakeStage:
    def __init__(self, provider: str, trace: Trace, *, fail_at: str | None = None):
        self.provider, self.trace, self.fail_at = provider, trace, fail_at

    def __call__(self, stage: str) -> dict[str, Any]:
        self.trace.add(self.provider, stage)
        if stage == self.fail_at:
            raise RuntimeError(f"synthetic {self.provider} {stage} failure")
        return {"provider": self.provider, "stage": stage, "synthetic": True}


def fake_provider_workflow(provider: str, trace: Trace, *, fail_at: str | None = None) -> dict[str, Callable[[], Any]]:
    run = FakeStage(provider, trace, fail_at=fail_at)
    return {stage: (lambda stage=stage: run(stage)) for stage in ("protect", "create", "prepare", "measure", "close", "copy", "cleanup")}
