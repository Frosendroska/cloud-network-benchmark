from cloud_network_benchmark import ConnectionOutput, RetrievalItem, RetrievalManifest, TimeoutPolicy, retrieve
from cloud_network_benchmark.contracts.deployment import RemoteConnectionData
from cloud_network_benchmark.contracts.execution import CommandResult
from datetime import datetime, timezone


def test_retrieval_rejects_existing_destination(tmp_path):
    t = TimeoutPolicy(ssh_seconds=1, command_seconds=1, cloud_init_seconds=1, private_path_seconds=1, server_seconds=1, transfer_seconds=1)
    c = ConnectionOutput(run_id="r", provider="aws", scenario="same_zone", vm_a=RemoteConnectionData(role="vm_a", host="10.0.0.1", user="u", authentication_reference="ref"), vm_b=RemoteConnectionData(role="vm_b", host="10.0.0.2", user="u", authentication_reference="ref"), private_ipv4_a="10.0.0.1", private_ipv4_b="10.0.0.2", timeouts=t)
    (tmp_path / "a.txt").write_text("old")
    manifest = RetrievalManifest(items=[RetrievalItem(artifact_name="a", source_vm="10.0.0.1", source_role="vm_a", source_path="/tmp/a", destination_path="a.txt", timeout_seconds=1)])
    result = retrieve(c, manifest, lambda request: None, tmp_path)
    assert result.complete is False
    assert result.failure.category == "destination_collision"


def test_retrieval_materializes_fake_transfer(tmp_path):
    t = TimeoutPolicy(ssh_seconds=1, command_seconds=1, cloud_init_seconds=1, private_path_seconds=1, server_seconds=1, transfer_seconds=1)
    c = ConnectionOutput(run_id="r", provider="aws", scenario="same_zone", vm_a=RemoteConnectionData(role="vm_a", host="10.0.0.1", user="u", authentication_reference="ref"), vm_b=RemoteConnectionData(role="vm_b", host="10.0.0.2", user="u", authentication_reference="ref"), private_ipv4_a="10.0.0.1", private_ipv4_b="10.0.0.2", timeouts=t)
    manifest = RetrievalManifest(items=[RetrievalItem(artifact_name="a", source_vm="10.0.0.1", source_role="vm_a", source_path="/tmp/a", destination_path="nested/a.txt", timeout_seconds=1)])
    class Runner:
        def run(self, request):
            now = datetime.now(timezone.utc)
            return CommandResult(action_id=request.action_id, outcome="succeeded", started_at=now, finished_at=now, duration_seconds=0, exit_code=0, stdout="payload", stderr="")
    result = retrieve(c, manifest, Runner(), tmp_path)
    assert result.complete is True
    assert (tmp_path / "nested/a.txt").read_text() == "payload"
