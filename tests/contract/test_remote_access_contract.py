from datetime import datetime, timezone

import pytest

from cloud_network_benchmark import ConnectionOutput, TimeoutPolicy
from cloud_network_benchmark.contracts import ProviderDeploymentOutput, ProviderVmOutput, StructuredEvidence
from cloud_network_benchmark.contracts.deployment import RemoteConnectionData


def connection() -> ConnectionOutput:
    timeouts = TimeoutPolicy(ssh_seconds=1, command_seconds=2, cloud_init_seconds=3, private_path_seconds=4, server_seconds=5, transfer_seconds=6)
    return ConnectionOutput(run_id="run-1", provider="aws", scenario="same_zone", vm_a=RemoteConnectionData(role="vm_a", host="10.0.0.1", user="ubuntu", authentication_reference="runtime:ssh"), vm_b=RemoteConnectionData(role="vm_b", host="10.0.0.2", user="ubuntu", authentication_reference="runtime:ssh"), private_ipv4_a="10.0.0.1", private_ipv4_b="10.0.0.2", timeouts=timeouts)


def test_connection_contract_requires_two_private_roles() -> None:
    assert connection().vm_a.role == "vm_a"
    with pytest.raises(ValueError):
        ConnectionOutput.model_validate({**connection().model_dump(), "private_ipv4_b": "8.8.8.8"})


def test_deployment_handoff_requires_scenario_metadata():
    missing = StructuredEvidence(unavailable_reason="not observed")
    output = ProviderDeploymentOutput(run_id="r", provider="aws", apply_action_id="a", started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc), vm_a=ProviderVmOutput(role="vm_a", resource_id=None, private_ipv4=None, image_identity=None, actual_shape=None, region=None, zone=None, placement_metadata=missing, connection=None, unavailable=missing), vm_b=ProviderVmOutput(role="vm_b", resource_id=None, private_ipv4=None, image_identity=None, actual_shape=None, region=None, zone=None, placement_metadata=missing, connection=None, unavailable=missing), provider_metadata=missing, documented_network_limit=missing)
    with pytest.raises(ValueError, match="scenario"):
        ConnectionOutput.from_deployment(output, connection().timeouts)
