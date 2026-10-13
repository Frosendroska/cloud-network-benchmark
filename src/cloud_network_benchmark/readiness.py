from __future__ import annotations

import time
from typing import List
from pydantic import Field

from .access import AccessFailureEvidence, ConnectionOutput, RemoteActionEvidence, _failure, _request
from .contracts.common import CommandOutcome, StrictModel
from .contracts.execution import CommandRunner


class ReadinessConfig(StrictModel):
    private_path_command: List[str] = Field(default_factory=lambda: ["ping", "-c", "1"])
    server_health_command: List[str] = Field(default_factory=lambda: ["systemctl", "is-active", "--quiet", "netserver"])
    success_output: str | None = "ready"
    poll_attempts: int = Field(default=1, ge=1)
    bootstrap_succeeded: bool = True


class ReadinessResult(StrictModel):
    ready: bool
    attempts: int
    actions: List[RemoteActionEvidence] = Field(default_factory=list)
    failure: AccessFailureEvidence | None = None


def run_readiness(connection: ConnectionOutput, config: ReadinessConfig, runner: CommandRunner) -> ReadinessResult:
    actions: List[RemoteActionEvidence] = []
    if not config.bootstrap_succeeded:
        from datetime import datetime, timezone
        return ReadinessResult(ready=False, attempts=0, failure=AccessFailureEvidence(stage="readiness", category="bootstrap_failed", message="readiness requires successful bootstrap", occurred_at=datetime.now(timezone.utc)))
    checks = [("ssh-vm-a", connection.vm_a, ["true"], connection.timeouts.ssh_seconds), ("ssh-vm-b", connection.vm_b, ["true"], connection.timeouts.ssh_seconds), ("private-path", connection.vm_a, [*config.private_path_command, connection.private_ipv4_b], connection.timeouts.private_path_seconds), ("server-health", connection.vm_b, config.server_health_command, connection.timeouts.server_seconds)]
    attempts = 0
    for name, vm, argv, timeout in checks:
        result = None
        deadline = time.monotonic() + timeout
        for attempt in range(config.poll_attempts):
            attempts += 1
            result = runner.run(_request(name, vm, argv, timeout))
            actions.append(RemoteActionEvidence(action_id=name, action_type="ssh", vm_id=vm.host, role=vm.role, result=result))
            healthy = config.success_output is None or result.stdout.strip() == config.success_output
            if result.outcome == CommandOutcome.SUCCEEDED and (name not in {"private-path", "server-health"} or healthy):
                break
            if time.monotonic() >= deadline:
                break
        if result is None:
            raise RuntimeError("readiness runner returned no result")
        healthy = config.success_output is None or result.stdout.strip() == config.success_output
        if result.outcome != CommandOutcome.SUCCEEDED or (name in {"private-path", "server-health"} and not healthy):
            category = "readiness_timeout" if result.outcome == CommandOutcome.TIMED_OUT else ("private_path_failed" if name == "private-path" else "server_not_ready" if name == "server-health" else "ssh_failed")
            failure = _failure("readiness", vm, category, result, f"{name} readiness failed")
            failure = failure.model_copy(update={"details": {**failure.details, "attempts": attempts, "deadline_seconds": timeout, "elapsed_seconds": max(0.0, timeout - max(0.0, deadline - time.monotonic()))}})
            return ReadinessResult(ready=False, attempts=attempts, actions=actions, failure=failure)
    return ReadinessResult(ready=True, attempts=attempts, actions=actions)
