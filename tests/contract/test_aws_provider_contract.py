from datetime import datetime, timezone

from cloud_network_benchmark.contracts.common import Evidence, IdentityContext, StructuredEvidence
from cloud_network_benchmark.contracts.deployment import ProviderDeploymentOutput, ProviderVmOutput, RemoteConnectionData


def test_aws_vm_output_round_trip_preserves_public_private_and_physical_zone_evidence():
    connection = RemoteConnectionData(
        role="vm_a",
        host="8.8.8.8",
        user="ubuntu",
        authentication_reference="private-ssh-key:attempt-only",
        host_key_reference="attempt/vm-a.known_hosts",
    )
    output = ProviderVmOutput(
        role="vm_a",
        resource_id="i-0123456789abcdef0",
        private_ipv4="10.60.1.10",
        public_ipv4=Evidence[str](value="8.8.8.8"),
        physical_zone_id=Evidence[str](value="euc1-az2"),
        image_identity="ami-0123456789abcdef0:099720109477:ubuntu-noble-24.04-amd64-hvm",
        actual_shape="m7i.xlarge",
        region="eu-central-1",
        zone="eu-central-1a",
        placement_metadata=StructuredEvidence(value={"placement_group": None}),
        connection=connection,
    )
    restored = ProviderVmOutput.model_validate(output.model_dump(mode="json"))
    assert restored.private_ipv4 == "10.60.1.10"
    assert restored.public_ipv4.value == "8.8.8.8"
    assert restored.physical_zone_id.value == "euc1-az2"
    assert restored.connection.host_key_reference == "attempt/vm-a.known_hosts"


def test_partial_vm_output_requires_explicit_unavailable_evidence():
    partial = ProviderVmOutput(
        role="vm_b",
        resource_id=None,
        private_ipv4=None,
        image_identity=None,
        actual_shape=None,
        region=None,
        zone=None,
        placement_metadata=StructuredEvidence(value={"state": "unknown"}),
        connection=None,
        unavailable=StructuredEvidence(value={"reason": "provider observation unavailable"}),
    )
    assert ProviderVmOutput.model_validate(partial.model_dump(mode="json")).unavailable is not None


def test_complete_frozen_deployment_output_round_trip_keeps_both_roles_and_identity():
    now = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)

    def vm(role, private_ip, public_ip, zone_id):
        return ProviderVmOutput(
            role=role, resource_id=f"i-{role.replace('_', '-')}", private_ipv4=private_ip,
            public_ipv4=Evidence[str](value=public_ip), physical_zone_id=Evidence[str](value=zone_id),
            image_identity="ami-0123456789abcdef0:099720109477:ubuntu-noble-24.04-amd64-hvm",
            actual_shape="m7i.xlarge", region="eu-central-1", zone="eu-central-1a",
            placement_metadata=StructuredEvidence(value={"placement_group": None}),
            connection=RemoteConnectionData(role=role, host=public_ip, user="ubuntu",
                authentication_reference="private-ssh-key:attempt-only", host_key_reference=f"attempt/{role}.known_hosts"),
        )

    output = ProviderDeploymentOutput(
        schema_version=2,
        identity=IdentityContext(experiment_id="EXP-001", config_id="exp-900-single-provider",
            campaign_id="campaign", observation_id="campaign-aws-observation", run_id="campaign-aws",
            deployment_id="campaign-aws-deployment", provider="aws"),
        run_id="campaign-aws", deployment_id="campaign-aws-deployment", mode="short_study",
        provider="aws", scenario="same_zone", apply_action_id="aws-pair-apply", started_at=now, finished_at=now,
        vm_a=vm("vm_a", "10.60.1.10", "8.8.8.8", "euc1-az2"),
        vm_b=vm("vm_b", "10.60.1.11", "1.1.1.1", "euc1-az2"),
        provider_metadata=StructuredEvidence(value={"source": "aws-provider-observation"}),
        documented_network_limit=StructuredEvidence(value={"baseline_gbit_per_second": 1.562}),
    )
    restored = ProviderDeploymentOutput.model_validate(output.model_dump(mode="json"))
    assert restored.identity == output.identity
    assert restored.vm_a.role == "vm_a" and restored.vm_b.role == "vm_b"
    assert restored.vm_a.physical_zone_id.value == restored.vm_b.physical_zone_id.value == "euc1-az2"
    assert restored.vm_a.connection.host_key_reference != restored.vm_b.connection.host_key_reference
