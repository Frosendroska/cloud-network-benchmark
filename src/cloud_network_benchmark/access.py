from __future__ import annotations

from datetime import datetime, timezone
from ipaddress import IPv4Address
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional

from pydantic import Field, model_validator

from .contracts.common import CommandOutcome, FailureEvidence, Scenario, StrictModel, VmRole
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
            if not address.is_private:
                raise ValueError(f"{name} must be a private IPv4 address")
        if self.private_ipv4_a == self.private_ipv4_b or self.vm_a.host == self.vm_b.host:
            raise ValueError("VM A and VM B addresses must be distinct")
        return self

    @classmethod
    def from_deployment(cls, deployment: ProviderDeploymentOutput, timeouts: TimeoutPolicy) -> "ConnectionOutput":
        if deployment.scenario is None:
            raise ValueError("deployment output is missing scenario metadata")
        if deployment.vm_a.connection is None or deployment.vm_b.connection is None:
            raise ValueError("deployment output is missing SSH connection data")
        if deployment.vm_a.private_ipv4 is None or deployment.vm_b.private_ipv4 is None:
            raise ValueError("deployment output is missing private IPv4 data")
        return cls(run_id=deployment.run_id, provider=deployment.provider, scenario=deployment.scenario, vm_a=deployment.vm_a.connection, vm_b=deployment.vm_b.connection, private_ipv4_a=deployment.vm_a.private_ipv4, private_ipv4_b=deployment.vm_b.private_ipv4, timeouts=timeouts)


class RemoteActionEvidence(StrictModel):
    action_id: str
    action_type: str
    vm_id: str
    role: str
    result: CommandResult


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


def _request(action_id: str, connection: RemoteConnectionData, argv: List[str], timeout: float, action_type: str = "ssh") -> CommandRequest:
    command = ["ssh", "-p", str(connection.port)]
    if connection.host_key_reference:
        command.extend(["-o", f"UserKnownHostsFile={connection.host_key_reference}"])
    command.extend([f"{connection.user}@{connection.host}", *argv])
    return CommandRequest(action_id=action_id, action_type=action_type, argv=command, cwd=".", environment_classification="contains_secret_references", timeout_seconds=timeout)


def _failure(stage: str, vm: RemoteConnectionData, category: str, result: CommandResult, message: str) -> AccessFailureEvidence:
    def redact(value: str) -> str:
        value = re.sub(r"-----BEGIN .*?-----.*?-----END .*?-----", "[REDACTED_KEY]", value, flags=re.DOTALL)
        return re.sub(r"(?i)(password|token|secret|private[_ -]?key)\s*[=:]\s*\S+", r"\1=[REDACTED]", value)
    return AccessFailureEvidence(stage=stage, vm_id=vm.host, role=vm.role, category=category, message=message, action_id=result.action_id, occurred_at=result.finished_at, details={"outcome": result.outcome, "exit_code": result.exit_code, "stdout": redact(result.stdout), "stderr": redact(result.stderr)})


def validate_connection(value: ConnectionOutput) -> ConnectionOutput:
    return ConnectionOutput.model_validate(value)


def prepare(connection: ConnectionOutput, bootstrap_config: Any, command_runner: CommandRunner):
    from .bootstrap import run_bootstrap
    return run_bootstrap(connection, bootstrap_config, command_runner)


def check_readiness(connection: ConnectionOutput, readiness_config: Any, command_runner: CommandRunner):
    from .readiness import run_readiness
    return run_readiness(connection, readiness_config, command_runner)


def retrieve(connection: ConnectionOutput, retrieval_manifest: Any, command_runner: CommandRunner, filesystem: Path):
    from .retrieval import run_retrieval
    return run_retrieval(connection, retrieval_manifest, command_runner, filesystem)
