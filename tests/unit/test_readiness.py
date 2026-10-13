from cloud_network_benchmark import ConnectionOutput, ReadinessConfig, TimeoutPolicy, check_readiness
from cloud_network_benchmark.contracts.deployment import RemoteConnectionData
from cloud_network_benchmark.contracts.execution import CommandResult
from datetime import datetime, timezone


def test_ssh_alone_does_not_grant_readiness():
    t = TimeoutPolicy(ssh_seconds=1, command_seconds=1, cloud_init_seconds=1, private_path_seconds=1, server_seconds=1, transfer_seconds=1)
    c = ConnectionOutput(run_id="r", provider="aws", scenario="same_zone", vm_a=RemoteConnectionData(role="vm_a", host="10.0.0.1", user="u", authentication_reference="ref"), vm_b=RemoteConnectionData(role="vm_b", host="10.0.0.2", user="u", authentication_reference="ref"), private_ipv4_a="10.0.0.1", private_ipv4_b="10.0.0.2", timeouts=t)
    class Runner:
        def run(self, request):
            now = datetime.now(timezone.utc)
            stdout = "" if request.action_id.startswith("ssh-") else "not ready"
            return CommandResult(action_id=request.action_id, outcome="succeeded", started_at=now, finished_at=now, duration_seconds=0, exit_code=0, stdout=stdout, stderr="")
    result = check_readiness(c, ReadinessConfig(success_output="ready"), Runner())
    assert result.ready is False
    assert result.failure.category == "private_path_failed"
