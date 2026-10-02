from __future__ import annotations

from typing import Dict, List
from pydantic import Field

from .access import AccessFailureEvidence, ConnectionOutput, RemoteActionEvidence, _failure, _request
from .contracts.common import CommandOutcome, StrictModel
from .contracts.execution import CommandRunner


class BootstrapConfig(StrictModel):
    flent_version: str = Field(min_length=1)
    netperf_version: str = Field(min_length=1)
    linux_tools: List[str] = Field(min_length=1)
    cloud_init_probe: str = Field(default="cloud-init status --wait")
    client_setup: List[List[str]] = Field(default_factory=list)
    server_setup: List[List[str]] = Field(default_factory=list)


class BootstrapResult(StrictModel):
    succeeded: bool
    actions: List[RemoteActionEvidence] = Field(default_factory=list)
    failure: AccessFailureEvidence | None = None
    tool_versions: Dict[str, str] = Field(default_factory=dict)


def render_commands(config: BootstrapConfig, role: str) -> List[List[str]]:
    commands = [["sudo", "apt-get", "update"]]
    commands.append(["sudo", "apt-get", "install", "-y", *config.linux_tools])
    commands.append(["python3", "-m", "pip", "install", f"flent=={config.flent_version}"])
    commands.append(["netperf", "--version", config.netperf_version])
    commands.extend(config.client_setup if role == "vm_a" else config.server_setup)
    commands.append(config.cloud_init_probe.split())
    return commands


def run_bootstrap(connection: ConnectionOutput, config: BootstrapConfig, runner: CommandRunner) -> BootstrapResult:
    actions: List[RemoteActionEvidence] = []
    for vm, role in ((connection.vm_a, "vm_a"), (connection.vm_b, "vm_b")):
        for index, argv in enumerate(render_commands(config, role)):
            action_id = f"bootstrap-{role}-{index}"
            timeout = connection.timeouts.cloud_init_seconds if index == len(render_commands(config, role)) - 1 else connection.timeouts.command_seconds
            result = runner.run(_request(action_id, vm, argv, timeout))
            actions.append(RemoteActionEvidence(action_id=action_id, action_type="ssh", vm_id=vm.host, role=role, result=result))
            if result.outcome != CommandOutcome.SUCCEEDED:
                category = "cloud_init_failed" if index == len(render_commands(config, role)) - 1 else "bootstrap_failed"
                if result.outcome == CommandOutcome.TIMED_OUT:
                    category = "readiness_timeout" if index == len(render_commands(config, role)) - 1 else "bootstrap_timeout"
                return BootstrapResult(succeeded=False, actions=actions, failure=_failure("bootstrap", vm, category, result, f"{role} bootstrap action failed"), tool_versions={"flent": config.flent_version, "netperf": config.netperf_version})
    return BootstrapResult(succeeded=True, actions=actions, tool_versions={"flent": config.flent_version, "netperf": config.netperf_version})
