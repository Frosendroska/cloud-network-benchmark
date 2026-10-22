from __future__ import annotations

from datetime import datetime, timezone
import math
import re
import time
from typing import Callable, List

from pydantic import Field, model_validator

from .access import AccessFailureEvidence, ConnectionOutput, RemoteActionEvidence, RemoteDeadline, _action, _execute, _request, _scrub
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
                and prepared.identity == connection.identity
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
    match = re.search(r"argv\[\]=(.*?);", exec_start)
    if not match:
        return False
    try:
        observed = __import__("shlex").split(match.group(1))
    except ValueError:
        return False
    expected = [listener.binary_path]
    expected += ["server", "-i", connection.private_ipv4_b, "-p", "11111"] if tool == "sockperf" else ["-s", "-B", connection.private_ipv4_b, "-p", "5201", "--json-stream", "--forceflush"]
    return (fields.get("LoadState") == "loaded" and fields.get("ActiveState") == "active"
            and fields.get("Restart") == "no" and observed == expected
            and fields.get("MainPID", "0").isdigit() and int(fields.get("MainPID", "0")) > 0
            and fields.get("StandardOutput") == "append:" + listener.stdout_path
            and fields.get("StandardError") == "append:" + listener.stderr_path)


def _listener_pid(stdout: str, tool: str, address: str, port: int) -> int | None:
    rows = [line for line in stdout.splitlines() if re.search(rf"(?:^|\s){re.escape(address)}:{port}(?:\s|$)", line)]
    if len(rows) != 1 or re.search(rf"(?:0\.0\.0\.0|\*):{port}(?:\s|$)", rows[0]):
        return None
    match = re.search(rf'users:\(\("{re.escape(tool)}",pid=(\d+),', rows[0])
    return int(match.group(1)) if match else None


def _acl_healthy(stdout: str, connection: ConnectionOutput) -> bool:
    rules = [re.sub(r"\bcounter packets \d+ bytes \d+\b", "", " ".join(line.strip().split())).strip() for line in stdout.splitlines() if line.strip()]
    wanted = [
        f"ip saddr {connection.private_ipv4_a}/32 ip daddr {connection.private_ipv4_b} udp dport 11111 accept",
        f"ip daddr {connection.private_ipv4_b} udp dport 11111 drop",
        f"ip saddr {connection.private_ipv4_a}/32 ip daddr {connection.private_ipv4_b} tcp dport 5201 accept",
        f"ip daddr {connection.private_ipv4_b} tcp dport 5201 drop",
    ]
    positions = []
    for expected in wanted:
        hits = [i for i, line in enumerate(rules) if line == expected]
        if len(hits) != 1:
            return False
        positions.append(hits[0])
    if positions != sorted(positions):
        return False
    if not any("hook input" in line and "policy accept" in line for line in rules):
        return False
    return True


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
        return _listener_pid(stdout, "sockperf", connection.private_ipv4_b, 11111) is not None
    if name == "socket-iperf3":
        return _listener_pid(stdout, "iperf3", connection.private_ipv4_b, 5201) is not None
    if name == "guest-acl":
        return _acl_healthy(stdout, connection)
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
            result = _execute(runner, request)
            actions.append(_action(request.action_id, "ssh", vm, request, result))
            healthy = result.outcome == CommandOutcome.SUCCEEDED and _predicate(name, result.stdout, connection, prepared)
            remaining_after = deadline.remaining(clock, readiness=True)
            if name.startswith("socket-") and healthy:
                tool = name.removeprefix("socket-")
                service = next((o.get("observation", "") for o in reversed(observations) if o.get("predicate") == f"service-{tool}"), "")
                fields = dict(line.split("=", 1) for line in service.splitlines() if "=" in line)
                pid = _listener_pid(result.stdout, tool, connection.private_ipv4_b, 11111 if tool == "sockperf" else 5201)
                healthy = pid is not None and str(pid) == fields.get("MainPID")
            if healthy and remaining_after <= 0:
                healthy = False
            safe_stdout = result.stdout
            for secret in (vm.authentication_reference, vm.host_key_reference):
                safe_stdout = _scrub(safe_stdout, secret)
            last = {"predicate": name, "outcome": str(result.outcome), "healthy": healthy,
                    "observation": safe_stdout, "remaining_seconds": remaining_after}
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
    remaining = deadline.remaining(clock, readiness=True)
    if remaining <= 0:
        failure = AccessFailureEvidence(stage="readiness", category="readiness_deadline_exhausted", message="readiness observations completed after the original deadline", occurred_at=datetime.now(timezone.utc), details={"last_observation": observations[-1] if observations else {}, "elapsed_seconds": max(0, clock()-start), "remaining_seconds": remaining})
        return ReadinessResult(ready=False, attempts=attempts, actions=actions, observations=observations, deadline=deadline, elapsed_seconds=max(0, clock()-start), remaining_seconds=remaining, failure=failure)
    return ReadinessResult(ready=True, attempts=attempts, actions=actions, observations=observations,
        deadline=deadline, elapsed_seconds=max(0, clock()-start), remaining_seconds=remaining)
