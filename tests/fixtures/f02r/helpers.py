"""Credential-free fake helpers for F02R tests."""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path

class FakeClock:
    def __init__(self, now: float = 100.0): self.now = now
    def __call__(self) -> float: return self.now
    def advance(self, seconds: float) -> None: self.now += seconds

def command_result(action_id: str, *, stdout: str = "", stderr: str = "", success: bool = True):
    from cloud_network_benchmark.contracts.execution import CommandResult
    now = datetime.now(timezone.utc)
    return CommandResult(action_id=action_id, outcome="succeeded" if success else "failed", started_at=now,
        finished_at=now, duration_seconds=0, exit_code=0 if success else 1, stdout=stdout, stderr=stderr)

class MaterializingRunner:
    """Fake runner that writes only bytes explicitly mapped to a request destination."""
    def __init__(self, responses=None, materialized=None):
        self.responses, self.materialized, self.requests = responses or {}, materialized or {}, []
    def run(self, request):
        self.requests.append(request)
        destination = request.argv[-1] if request.action_type == "scp" else None
        if destination in self.materialized: Path(destination).write_bytes(self.materialized[destination])
        return self.responses.get(request.action_id) or command_result(request.action_id)
