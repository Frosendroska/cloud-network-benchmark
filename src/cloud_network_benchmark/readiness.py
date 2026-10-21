from __future__ import annotations

from datetime import datetime, timezone
import math
import re
import time
from typing import Callable, List

from pydantic import Field, model_validator

from .access import AccessFailureEvidence, ConnectionOutput, RemoteActionEvidence, RemoteDeadline, _action, _request
from .bootstrap import BootstrapResult
from .contracts.common import CommandOutcome, StrictModel
from .contracts.execution import CommandRunner


class ReadinessConfig(StrictModel):
    poll_interval_seconds: float = Field(gt=0, le=60)
    attempt_ceiling: int = Field(ge=1, le=1000)

    @model_validator(mode="after")
    def finite_poll(self):
        if not math.isfinite(self.poll_interval_seconds):
            raise ValueError("poll interval must be finite")
        return self


class ReadinessResult(StrictModel):
    ready: bool
    attempts: int
    actions: List[RemoteActionEvidence] = Field(default_factory=list)
    observations: List[dict] = Field(default_factory=list)
    deadline: RemoteDeadline
    elapsed_seconds: float
    remaining_seconds: float
    failure: AccessFailureEvidence | None = None


def _bootstrap_matches(connection: ConnectionOutput, prepared: BootstrapResult) -> bool:
    return bool(prepared.succeeded and prepared.deployment_id == connection.deployment_id
                and prepared.identity.run_id == connection.run_id
                and len(prepared.builds) == 4
                and {(x.role, x.tool) for x in prepared.builds} == {(r, t) for r in ("vm_a", "vm_b") for t in ("sockperf", "iperf3")}
                and {(x.tool, x.protocol, x.port, x.address, x.allowed_source) for x in prepared.listeners}
                    == {("sockperf", "udp", 11111, connection.private_ipv4_b, f"{connection.private_ipv4_a}/32"),
                        ("iperf3", "tcp", 5201, connection.private_ipv4_b, f"{connection.private_ipv4_a}/32")}
                and all(x.state == "active" for x in prepared.listeners))


def _systemd_healthy(stdout: str, connection: ConnectionOutput, prepared: BootstrapResult, tool: str) -> bool:
    fields = dict(line.split("=", 1) for line in stdout.splitlines() if "=" in line)
    listener = next((item for item in prepared.listeners if item.tool == tool), None)
    if listener is None:
        return False
    exec_start = fields.get("ExecStart", "")
    expected = [listener.binary_path, connection.private_ipv4_b,
                "11111" if tool == "sockperf" else "5201"]
    if tool == "iperf3":
        expected.extend(("--json-stream", "--forceflush"))
    else:
        expected.append("server")
    return (fields.get("LoadState") == "loaded" and fields.get("ActiveState") == "active"
            and fields.get("Restart") == "no" and all(token in exec_start for token in expected)
            and listener.stdout_path in fields.get("StandardOutput", "")
            and listener.stderr_path in fields.get("StandardError", ""))


def _predicate(name: str, stdout: str, connection: ConnectionOutput, prepared: BootstrapResult) -> bool:
    if name in {"ssh-vm-a", "ssh-vm-b"}:
        return True
    if name.startswith("cloud-init"):
        return stdout.strip().lower() == "status: done" or stdout.strip().lower() == "done"
    if name == "private-icmp":
        return connection.private_ipv4_b in stdout
    if name == "private-tcp":
        return True
    if name.startswith("service-"):
        return _systemd_healthy(stdout, connection, prepared, name.removeprefix("service-"))
    if name == "socket-sockperf":
        return f"{connection.private_ipv4_b}:11111" in stdout and "sockperf" in stdout.lower() and "0.0.0.0:11111" not in stdout and "*:11111" not in stdout
    if name == "socket-iperf3":
        return f"{connection.private_ipv4_b}:5201" in stdout and "iperf3" in stdout.lower() and "0.0.0.0:5201" not in stdout and "*:5201" not in stdout
    if name == "guest-acl":
        return (f"{connection.private_ipv4_a}/32" in stdout and connection.private_ipv4_b in stdout
                and "11111" in stdout and "5201" in stdout and "drop" in stdout.lower()
                and "hook input" in stdout.lower())
    return False


def run_readiness(connection: ConnectionOutput, config: ReadinessConfig, runner: CommandRunner, *, prepared: BootstrapResult, deadline: RemoteDeadline, clock: Callable[[], float] = time.monotonic) -> ReadinessResult:
    start = clock()
    actions: List[RemoteActionEvidence] = []
    observations: List[dict] = []
    attempts = 0
    if not _bootstrap_matches(connection, prepared):
        failure = AccessFailureEvidence(stage="readiness", category="bootstrap_evidence_mismatch", message="readiness requires matching successful preparation evidence", occurred_at=datetime.now(timezone.utc))
        return ReadinessResult(ready=False, attempts=0, actions=[], observations=[], deadline=deadline,
            elapsed_seconds=0, remaining_seconds=deadline.remaining(clock, readiness=True), failure=failure)
    chain = "cnb_" + re.sub(r"[^A-Za-z0-9_]", "_", connection.deployment_id)[:32]
    checks = [
        ("ssh-vm-a", connection.vm_a, ["true"], connection.timeouts.ssh_seconds),
        ("ssh-vm-b", connection.vm_b, ["true"], connection.timeouts.ssh_seconds),
        ("cloud-init-vm-a", connection.vm_a, ["cloud-init", "status", "--wait"], connection.timeouts.cloud_init_seconds),
        ("cloud-init-vm-b", connection.vm_b, ["cloud-init", "status", "--wait"], connection.timeouts.cloud_init_seconds),
        ("private-icmp", connection.vm_a, ["ping", "-c", "1", "-W", "1", connection.private_ipv4_b], connection.timeouts.private_path_seconds),
        ("private-tcp", connection.vm_a, ["nc", "-z", "-w", "1", connection.private_ipv4_b, "5201"], connection.timeouts.private_path_seconds),
        ("service-sockperf", connection.vm_b, ["systemctl", "show", f"cnb-{connection.deployment_id}-sockperf", "--property=LoadState,ActiveState,ExecStart,StandardOutput,StandardError,Restart"], connection.timeouts.server_seconds),
        ("service-iperf3", connection.vm_b, ["systemctl", "show", f"cnb-{connection.deployment_id}-iperf3", "--property=LoadState,ActiveState,ExecStart,StandardOutput,StandardError,Restart"], connection.timeouts.server_seconds),
        ("socket-sockperf", connection.vm_b, ["ss", "-H", "-lunp"], connection.timeouts.server_seconds),
        ("socket-iperf3", connection.vm_b, ["ss", "-H", "-ltnp"], connection.timeouts.server_seconds),
        ("guest-acl", connection.vm_b, ["sudo", "nft", "list", "chain", "inet", "cnb_f02r", chain], connection.timeouts.server_seconds),
    ]
    for name, vm, argv, ceiling in checks:
        last = None
        for attempt in range(1, config.attempt_ceiling + 1):
            remaining = deadline.remaining(clock, readiness=True)
            if remaining <= 0:
                break
            attempts += 1
            request = _request(f"f02r-ready-{name}-{attempt}", vm, argv, min(ceiling, remaining))
            result = runner.run(request)
            actions.append(_action(request.action_id, "ssh", vm, request, result))
            healthy = result.outcome == CommandOutcome.SUCCEEDED and _predicate(name, result.stdout, connection, prepared)
            last = {"predicate": name, "outcome": str(result.outcome), "healthy": healthy,
                    "observation": result.stdout, "remaining_seconds": deadline.remaining(clock, readiness=True)}
            observations.append(last)
            if healthy:
                break
            if result.outcome == CommandOutcome.INTERRUPTED:
                failure = AccessFailureEvidence(stage="readiness", vm_id=vm.host, role=str(vm.role), category="readiness_interrupted",
                    message=f"readiness predicate {name} was interrupted", action_id=request.action_id,
                    occurred_at=result.finished_at, details={"last_observation": last})
                return ReadinessResult(ready=False, attempts=attempts, actions=actions, observations=observations,
                    deadline=deadline, elapsed_seconds=max(0, clock()-start),
                    remaining_seconds=deadline.remaining(clock, readiness=True), failure=failure)
            remaining = deadline.remaining(clock, readiness=True)
            if remaining <= 0 or attempt == config.attempt_ceiling:
                break
            wait = min(config.poll_interval_seconds, remaining)
            clock_wait = getattr(clock, "wait", None)
            if clock_wait is not None:
                clock_wait(wait)
            else:
                time.sleep(wait)
        if last is None or not last["healthy"]:
            now_remaining = deadline.remaining(clock, readiness=True)
            category = "readiness_deadline_exhausted" if now_remaining <= 0 else "readiness_predicate_failed"
            failure = AccessFailureEvidence(stage="readiness", vm_id=vm.host, role=str(vm.role), category=category,
                message=f"readiness predicate {name} was not observed", action_id=actions[-1].action_id if actions else None,
                occurred_at=datetime.now(timezone.utc), details={"last_observation": last or {}, "elapsed_seconds": max(0, clock()-start), "remaining_seconds": now_remaining})
            return ReadinessResult(ready=False, attempts=attempts, actions=actions, observations=observations,
                deadline=deadline, elapsed_seconds=max(0, clock()-start), remaining_seconds=now_remaining, failure=failure)
    return ReadinessResult(ready=True, attempts=attempts, actions=actions, observations=observations,
        deadline=deadline, elapsed_seconds=max(0, clock()-start), remaining_seconds=deadline.remaining(clock, readiness=True))
