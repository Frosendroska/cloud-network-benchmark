from cloud_network_benchmark.contracts.common import Evidence, StructuredEvidence
from cloud_network_benchmark.contracts.deployment import ProviderVmOutput, RemoteConnectionData


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
