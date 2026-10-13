from cloud_network_benchmark import BootstrapConfig, ConnectionOutput, ReadinessConfig, TimeoutPolicy, check_readiness, prepare
from cloud_network_benchmark.contracts.deployment import RemoteConnectionData
from cloud_network_benchmark.contracts.execution import CommandResult
from datetime import datetime, timezone


def test_failed_bootstrap_stops_before_next_vm():
    t = TimeoutPolicy(ssh_seconds=1, command_seconds=1, cloud_init_seconds=1, private_path_seconds=1, server_seconds=1, transfer_seconds=1)
    c = ConnectionOutput(run_id="r", provider="aws", scenario="same_zone", vm_a=RemoteConnectionData(role="vm_a", host="10.0.0.1", user="u", authentication_reference="ref"), vm_b=RemoteConnectionData(role="vm_b", host="10.0.0.2", user="u", authentication_reference="ref"), private_ipv4_a="10.0.0.1", private_ipv4_b="10.0.0.2", timeouts=t)
    calls = []
    class Runner:
        def run(self, request):
            calls.append(request.action_id)
            now = datetime.now(timezone.utc)
            return CommandResult(action_id=request.action_id, outcome="failed", started_at=now, finished_at=now, duration_seconds=0, exit_code=1, stdout="", stderr="install failed")
    result = prepare(c, BootstrapConfig(flent_version="1", netperf_version="1", linux_tools=["iproute2"]), Runner())
    assert result.succeeded is False
    assert not any("vm_b" in action for action in calls)
