from datetime import datetime, timezone

from cloud_network_benchmark import BootstrapConfig, ConnectionOutput, TimeoutPolicy, prepare
from cloud_network_benchmark.bootstrap import render_commands
from cloud_network_benchmark.contracts.deployment import RemoteConnectionData
from cloud_network_benchmark.contracts.execution import CommandResult, ScriptedCommandRunner


def conn() -> ConnectionOutput:
    t = TimeoutPolicy(ssh_seconds=1, command_seconds=1, cloud_init_seconds=1, private_path_seconds=1, server_seconds=1, transfer_seconds=1)
    return ConnectionOutput(run_id="r", provider="aws", scenario="same_zone", vm_a=RemoteConnectionData(role="vm_a", host="10.0.0.1", user="u", authentication_reference="ref"), vm_b=RemoteConnectionData(role="vm_b", host="10.0.0.2", user="u", authentication_reference="ref"), private_ipv4_a="10.0.0.1", private_ipv4_b="10.0.0.2", timeouts=t)


def result(action: str) -> CommandResult:
    now = datetime.now(timezone.utc)
    return CommandResult(action_id=action, outcome="succeeded", started_at=now, finished_at=now, duration_seconds=0, exit_code=0, stdout="cloud-init status: done", stderr="")


def test_bootstrap_runs_both_roles() -> None:
    config = BootstrapConfig(flent_version="2.2.0", netperf_version="2.7.0", linux_tools=["iproute2"])
    commands = []
    for role in ("vm_a", "vm_b"):
        commands.extend([(None, result(f"bootstrap-{role}-{i}")) for i in range(5)])
    # Replace expected requests after constructing a permissive fake runner.
    class Runner:
        def run(self, request):
            commands.append(request)
            return result(request.action_id)
    outcome = prepare(conn(), config, Runner())
    assert outcome.succeeded is True
    assert len(outcome.actions) == 10


def test_bootstrap_commands_are_shell_free():
    commands = render_commands(BootstrapConfig(flent_version="2.2.0", netperf_version="2.7.0", linux_tools=["iproute2"]), "vm_a")
    assert all(command[0] != "bash" for command in commands)
    assert ["python3", "-m", "pip", "install", "flent==2.2.0"] in commands
