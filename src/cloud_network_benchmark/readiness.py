from __future__ import annotations

from typing import List
from pydantic import Field

from .access import AccessFailureEvidence, ConnectionOutput, RemoteActionEvidence, _failure, _request
from .contracts.common import CommandOutcome, StrictModel
from .contracts.execution import CommandRunner


class ReadinessConfig(StrictModel):
    private_path_command: List[str] = Field(default_factory=lambda: ["ping", "-c", "1"])
    server_health_command: List[str] = Field(default_factory=lambda: ["netserver", "-D"])
    success_output: str = "ready"
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
        for attempt in range(config.poll_attempts):
            attempts += 1
            result = runner.run(_request(name, vm, argv, timeout))
            actions.append(RemoteActionEvidence(action_id=name, action_type="ssh", vm_id=vm.host, role=vm.role, result=result))
            if result.outcome == CommandOutcome.SUCCEEDED and (name not in {"private-path", "server-health"} or result.stdout.strip() == config.success_output):
                break
        assert result is not None
        if result.outcome != CommandOutcome.SUCCEEDED or (name in {"private-path", "server-health"} and result.stdout.strip() != config.success_output):
            category = "readiness_timeout" if result.outcome == CommandOutcome.TIMED_OUT else ("private_path_failed" if name == "private-path" else "server_not_ready" if name == "server-health" else "ssh_failed")
            return ReadinessResult(ready=False, attempts=attempts, actions=actions, failure=_failure("readiness", vm, category, result, f"{name} readiness failed"))
    return ReadinessResult(ready=True, attempts=attempts, actions=actions)
