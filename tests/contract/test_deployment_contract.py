from datetime import datetime, timezone

import pytest

from cloud_network_benchmark.contracts import (
    Placement,
    ProviderDeploymentInput,
    ProviderDeploymentOutput,
    ProviderVmOutput,
    RemoteConnectionData,
    StructuredEvidence,
    VmIntent,
)


NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


def vm(role: str, function: str) -> VmIntent:
    return VmIntent(role=role, function=function, region="eu-central-1", zone="euc1-az1", vm_shape="m7i.xlarge", image="ubuntu", connection_user="ubuntu")


def test_provider_deployment_input_keeps_provider_semantics() -> None:
    value = ProviderDeploymentInput(
        campaign_id="campaign", run_id="campaign-aws", experiment_id="EXP-001", provider="aws", scenario="same_zone",
        scheduled_start=NOW, vm_a=vm("vm_a", "client"), vm_b=vm("vm_b", "server"),
        placement=Placement(kind="none", name=None), bootstrap_template="cloud-init.yaml", terraform_directory="terraform/aws",
        child_result_path="results/raw/campaign-aws", provisioning_timeout_seconds=60, cleanup_timeout_seconds=60,
        config_sha256="a" * 64, implementation_git_commit="b" * 40,
    )
    assert value.provider == "aws"
    assert value.vm_a.role == "vm_a"


def test_remote_connection_requires_private_ipv4() -> None:
    connection = RemoteConnectionData(role="vm_a", host="10.0.0.4", user="ubuntu", authentication_reference="runtime:ssh")
    assert connection.port == 22
    with pytest.raises(ValueError):
        RemoteConnectionData(role="vm_a", host="8.8.8.8", user="ubuntu", authentication_reference="runtime:ssh")


def test_partial_provider_output_represents_unavailable_data() -> None:
    missing = StructuredEvidence(unavailable_reason="apply failed")
    output = ProviderDeploymentOutput(
        run_id="campaign-aws", provider="aws", apply_action_id="apply", started_at=NOW, finished_at=NOW,
        vm_a=ProviderVmOutput(role="vm_a", resource_id=None, private_ipv4=None, image_identity=None, actual_shape=None, region=None, zone=None, placement_metadata=missing, connection=None, unavailable=missing),
        vm_b=ProviderVmOutput(role="vm_b", resource_id=None, private_ipv4=None, image_identity=None, actual_shape=None, region=None, zone=None, placement_metadata=missing, connection=None, unavailable=missing),
        provider_metadata=missing, documented_network_limit=missing,
    )
    assert output.vm_a.unavailable.unavailable_reason == "apply failed"
