from datetime import datetime, timezone

import pytest
import json
from pathlib import Path

from cloud_network_benchmark import ConnectionOutput, TimeoutPolicy
from cloud_network_benchmark.contracts import ProviderDeploymentOutput, ProviderVmOutput, StructuredEvidence
from cloud_network_benchmark.contracts.deployment import RemoteConnectionData
from cloud_network_benchmark.access import _request


def connection() -> ConnectionOutput:
    timeouts = TimeoutPolicy(ssh_seconds=1, command_seconds=2, cloud_init_seconds=3, private_path_seconds=4, server_seconds=5, transfer_seconds=6)
    return ConnectionOutput(run_id="run-1", provider="aws", scenario="same_zone", vm_a=RemoteConnectionData(role="vm_a", host="10.0.0.1", user="ubuntu", authentication_reference="runtime:ssh"), vm_b=RemoteConnectionData(role="vm_b", host="10.0.0.2", user="ubuntu", authentication_reference="runtime:ssh"), private_ipv4_a="10.0.0.1", private_ipv4_b="10.0.0.2", timeouts=timeouts)


def test_connection_contract_requires_two_private_roles() -> None:
    assert connection().vm_a.role == "vm_a"
    with pytest.raises(ValueError):
        ConnectionOutput.model_validate({**connection().model_dump(), "private_ipv4_b": "8.8.8.8"})


def test_ssh_request_contains_runtime_identity_and_host_key_options() -> None:
    vm = connection().vm_a
    request = _request("ssh-check", vm, ["true"], 5)
    assert request.argv == ["ssh", "-p", "22", "-i", "runtime:ssh", "ubuntu@10.0.0.1", "true"]


def test_deployment_handoff_requires_scenario_metadata():
    missing = StructuredEvidence(unavailable_reason="not observed")
    output = ProviderDeploymentOutput(run_id="r", provider="aws", apply_action_id="a", started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc), vm_a=ProviderVmOutput(role="vm_a", resource_id=None, private_ipv4=None, image_identity=None, actual_shape=None, region=None, zone=None, placement_metadata=missing, connection=None, unavailable=missing), vm_b=ProviderVmOutput(role="vm_b", resource_id=None, private_ipv4=None, image_identity=None, actual_shape=None, region=None, zone=None, placement_metadata=missing, connection=None, unavailable=missing), provider_metadata=missing, documented_network_limit=missing)
    with pytest.raises(ValueError, match="scenario"):
        ConnectionOutput.from_deployment(output, connection().timeouts)


def test_evidence_review_fixture_contains_success_and_failure():
    fixture = json.loads((Path(__file__).parents[1] / "fixtures" / "f02" / "evidence-review.json").read_text())
    assert fixture["successful"]["status"] == "ready"
    assert fixture["failed"]["stdout"] == "password=[REDACTED]"
