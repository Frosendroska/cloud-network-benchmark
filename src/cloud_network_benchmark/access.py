from __future__ import annotations

from datetime import datetime, timezone
from ipaddress import IPv4Address, ip_address
from pathlib import Path
import math
import re
import shlex
from typing import Any, Callable, Dict, List, Optional

from pydantic import Field, model_validator

from .contracts.common import CommandOutcome, FailureEvidence, IdentityContext, Scenario, StrictModel, VmRole
from .contracts.deployment import ProviderDeploymentOutput, RemoteConnectionData
from .contracts.execution import CommandRequest, CommandResult, CommandRunner


class TimeoutPolicy(StrictModel):
    ssh_seconds: float = Field(gt=0, le=3600)
    command_seconds: float = Field(gt=0, le=3600)
    cloud_init_seconds: float = Field(gt=0, le=3600)
    private_path_seconds: float = Field(gt=0, le=3600)
    server_seconds: float = Field(gt=0, le=3600)
    transfer_seconds: float = Field(gt=0, le=3600)


class ConnectionOutput(StrictModel):
    identity: IdentityContext
    deployment_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    scenario: Scenario
    vm_a: RemoteConnectionData
    vm_b: RemoteConnectionData
    private_ipv4_a: str
    private_ipv4_b: str
    timeouts: TimeoutPolicy

    @model_validator(mode="after")
    def validate_pair(self) -> "ConnectionOutput":
        if self.vm_a.role != VmRole.VM_A.value or self.vm_b.role != VmRole.VM_B.value:
            raise ValueError("connection output requires VM A and VM B roles")
        for name in ("private_ipv4_a", "private_ipv4_b"):
            address = IPv4Address(getattr(self, name))
            if not address.is_private or address.is_unspecified or address.is_multicast or address.is_loopback:
                raise ValueError(f"{name} must be a usable private IPv4 address")
        if self.private_ipv4_a == self.private_ipv4_b or self.vm_a.host == self.vm_b.host:
            raise ValueError("VM A and VM B addresses must be distinct")
        if (self.identity.run_id, self.identity.deployment_id, self.identity.provider) != (self.run_id, self.deployment_id, self.provider):
            raise ValueError("connection identity must match deployment, run and provider")
        return self

    @classmethod
    def from_deployment(cls, deployment: ProviderDeploymentOutput, timeouts: TimeoutPolicy) -> "ConnectionOutput":
        if deployment.vm_a.connection is None or deployment.vm_b.connection is None:
            raise ValueError("deployment output is missing SSH connection data")
        if deployment.vm_a.private_ipv4 is None or deployment.vm_b.private_ipv4 is None:
            raise ValueError("deployment output is missing private IPv4 data")
        return cls(identity=deployment.identity, deployment_id=deployment.deployment_id, run_id=deployment.run_id,
                   provider=deployment.provider, scenario=deployment.scenario,
                   vm_a=deployment.vm_a.connection, vm_b=deployment.vm_b.connection,
                   private_ipv4_a=deployment.vm_a.private_ipv4, private_ipv4_b=deployment.vm_b.private_ipv4,
                   timeouts=timeouts)


class RemoteDeadline(StrictModel):
    readiness_deadline_monotonic: float
    overall_deadline_monotonic: float
    cleanup_reserve_seconds: float = Field(gt=0)
    cap_started_at: datetime
    cap_expires_at: datetime

    @model_validator(mode="after")
    def valid_deadline(self) -> "RemoteDeadline":
        for value in (self.readiness_deadline_monotonic, self.overall_deadline_monotonic, self.cleanup_reserve_seconds):
            if not math.isfinite(value):
                raise ValueError("deadline values must be finite")
        if self.readiness_deadline_monotonic > self.overall_deadline_monotonic:
            raise ValueError("readiness deadline cannot exceed overall deadline")
        for name in ("cap_started_at", "cap_expires_at"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must include timezone")
        if self.cap_expires_at <= self.cap_started_at:
            raise ValueError("cap expiry must follow cap start")
        return self

    def remaining(self, clock: Callable[[], float], *, cleanup: bool = False, readiness: bool = False) -> float:
        end = self.readiness_deadline_monotonic if readiness else self.overall_deadline_monotonic
        reserve = 0 if readiness else self.cleanup_reserve_seconds if cleanup else 0
        return max(0.0, end - clock() - reserve)


class RemoteActionEvidence(StrictModel):
    action_id: str
    action_type: str
    vm_id: str
    role: str
    result: Dict[str, Any]
    request: Dict[str, Any] = Field(default_factory=dict)


class AccessFailureEvidence(StrictModel):
    stage: str
    vm_id: Optional[str] = None
    role: Optional[str] = None
    category: str
    message: str
    action_id: Optional[str] = None
    source_path: Optional[str] = None
    occurred_at: datetime
    details: Dict[str, Any] = Field(default_factory=dict)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _connection_from_deployment(deployment: ProviderDeploymentOutput | ConnectionOutput, timeouts: Optional[TimeoutPolicy]) -> ConnectionOutput:
    if isinstance(deployment, ConnectionOutput):
        return deployment
    if timeouts is None:
        raise ValueError("timeouts are required for deployment output")
    return ConnectionOutput.from_deployment(deployment, timeouts)


def _safe_token(value: str, label: str) -> str:
    if not value or value.startswith("-") or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"unsafe {label}")
    return value


def _validate_remote_identity(connection: RemoteConnectionData) -> None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,31}", connection.user):
        raise ValueError("unsafe remote username")
    try:
        host = ip_address(connection.host)
    except ValueError as exc:
        raise ValueError("remote host must be an IPv4 address") from exc
    if not isinstance(host, IPv4Address):
        raise ValueError("remote host must be an IPv4 address")
    _safe_token(connection.authentication_reference, "key reference")
    _safe_token(connection.host_key_reference, "known-host reference")


def _readable_reference(path: str, label: str) -> None:
    p = Path(path)
    if not p.is_file() or not p.stat().st_size or not (p.stat().st_mode & 0o444):
        raise ValueError(f"{label} reference must be a nonempty readable file")


def _remote_argv(argv: List[str]) -> str:
    if not argv or any(not isinstance(v, str) or any(ord(char) < 32 or ord(char) == 127 for char in v) for v in argv):
        raise ValueError("remote argv must contain safe string tokens")
    return shlex.join(argv)


def _transport_options(connection: RemoteConnectionData, *, scp: bool = False) -> List[str]:
    if not connection.host_key_reference or not connection.authentication_reference:
        raise ValueError("authenticated key and known-host references are required")
    for value, label in ((connection.user, "user"), (connection.host, "host"), (connection.authentication_reference, "key reference"), (connection.host_key_reference, "known-host reference")):
        _safe_token(value, label)
    _validate_remote_identity(connection)
    for path, label in ((connection.authentication_reference, "key"), (connection.host_key_reference, "known-host")):
        _readable_reference(path, label)
    port_flag = "-P" if scp else "-p"
    options = ["-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
               "-o", f"UserKnownHostsFile={connection.host_key_reference}", "-o", "PasswordAuthentication=no",
               "-o", "KbdInteractiveAuthentication=no"]
    return [port_flag, str(connection.port), "-i", connection.authentication_reference, *options]


def _request(action_id: str, connection: RemoteConnectionData, argv: List[str], timeout: float, action_type: str = "ssh") -> CommandRequest:
    if timeout <= 0 or not math.isfinite(timeout):
        raise ValueError("command timeout must be positive and finite")
    command = ["scp", *_transport_options(connection, scp=action_type == "scp")] if action_type == "scp" else ["ssh", *_transport_options(connection)]
    target = f"{connection.user}@{connection.host}"
    if action_type == "scp":
        command.extend(argv)
    else:
        command.extend([target, _remote_argv(argv)])
    return CommandRequest(action_id=action_id, action_type=action_type, argv=command, cwd=".",
                          environment_classification="contains_secret_references", timeout_seconds=timeout,
                          redactions=[connection.authentication_reference, connection.host_key_reference])


def _execute(runner: CommandRunner, request: CommandRequest) -> CommandResult:
    """Turn a runner-boundary exception into durable failed command evidence."""
    started = datetime.now(timezone.utc)
    try:
        return runner.run(request)
    except KeyboardInterrupt:
        finished = datetime.now(timezone.utc)
        return CommandResult(action_id=request.action_id, outcome=CommandOutcome.INTERRUPTED,
                             started_at=started, finished_at=finished,
                             duration_seconds=max(0.0, (finished - started).total_seconds()),
                             exit_code=None, stdout="", stderr="command interrupted",
                             redactions=request.redactions)
    except Exception as exc:
        finished = datetime.now(timezone.utc)
        return CommandResult(action_id=request.action_id, outcome=CommandOutcome.FAILED,
                             started_at=started, finished_at=finished,
                             duration_seconds=max(0.0, (finished - started).total_seconds()),
                             exit_code=1, stdout="", stderr=f"command runner raised {type(exc).__name__}",
                             redactions=request.redactions)


def _request_failure(action_id: str, connection: RemoteConnectionData, timeout: float, error: BaseException) -> tuple[CommandRequest, CommandResult]:
    """Represent a failed local transport preflight without attempting a remote command."""
    now = datetime.now(timezone.utc)
    request = CommandRequest(action_id=action_id, action_type="ssh", argv=["ssh", "[preflight-failed]"],
        cwd=".", environment_classification="contains_secret_references", timeout_seconds=max(timeout, 0.001),
        redactions=[connection.authentication_reference, connection.host_key_reference])
    result = CommandResult(action_id=action_id, outcome=CommandOutcome.FAILED, started_at=now,
        finished_at=now, duration_seconds=0, exit_code=1, stdout="",
        stderr=f"transport preflight failed: {type(error).__name__}", redactions=request.redactions)
    return request, result


def _identity_references_available(connection: RemoteConnectionData) -> bool:
    try:
        for path in (connection.authentication_reference, connection.host_key_reference):
            _readable_reference(path, "identity")
        return True
    except (OSError, ValueError):
        return False


def _action(action_id: str, action_type: str, vm: RemoteConnectionData, request: CommandRequest, result: CommandResult) -> RemoteActionEvidence:
    req = request.durable_view()
    res = result.durable_view()
    secrets = [connection_path for connection_path in (vm.authentication_reference, vm.host_key_reference) if connection_path]
    for secret in secrets:
        req = _scrub(req, secret)
        res = _scrub(res, secret)
    return RemoteActionEvidence(action_id=action_id, action_type=action_type, vm_id=vm.host, role=str(vm.role), request=req, result=res)


def _scrub(value: Any, secret: str) -> Any:
    if isinstance(value, str):
        value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"-----BEGIN [^-]+-----.*?-----END [^-]+-----", "[REDACTED_KEY]", value, flags=re.DOTALL)
        value = re.sub(r"(?i)(password|token|secret|private[_ -]?key)\s*[=:]\s*\S+", r"\1=[REDACTED]", value)
        return value
    if isinstance(value, dict):
        return {k: _scrub(v, secret) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v, secret) for v in value]
    return value


def _failure(stage: str, vm: RemoteConnectionData, category: str, result: CommandResult, message: str, request: CommandRequest | None = None) -> AccessFailureEvidence:
    details = {"outcome": result.outcome, "exit_code": result.exit_code, "stdout": result.stdout, "stderr": result.stderr,
               "request": request.durable_view() if request is not None else {}}
    for secret in (vm.authentication_reference, vm.host_key_reference):
        details = _scrub(details, secret)
    return AccessFailureEvidence(stage=stage, vm_id=vm.host, role=str(vm.role), category=category, message=message,
                                 action_id=result.action_id, occurred_at=result.finished_at, details=details)


def validate_connection(value: ConnectionOutput) -> ConnectionOutput:
    return ConnectionOutput.model_validate(value)


def prepare(connection: ConnectionOutput, bootstrap_config: Any, command_runner: CommandRunner, *, deadline: RemoteDeadline, clock: Callable[[], float] = __import__("time").monotonic):
    from .bootstrap import run_bootstrap
    return run_bootstrap(connection, bootstrap_config, command_runner, deadline=deadline, clock=clock)


def check_readiness(connection: ConnectionOutput, readiness_config: Any, command_runner: CommandRunner, *, prepared: Any, deadline: RemoteDeadline, clock: Callable[[], float] = __import__("time").monotonic):
    from .readiness import run_readiness
    return run_readiness(connection, readiness_config, command_runner, prepared=prepared, deadline=deadline, clock=clock)


def retrieve(connection: ConnectionOutput, retrieval_manifest: Any, command_runner: CommandRunner, filesystem: Path, *, deadline: RemoteDeadline, clock: Callable[[], float] = __import__("time").monotonic):
    from .retrieval import run_retrieval
    return run_retrieval(connection, retrieval_manifest, command_runner, filesystem, deadline=deadline, clock=clock)
