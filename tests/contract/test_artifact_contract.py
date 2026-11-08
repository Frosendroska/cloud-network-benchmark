import pytest

from cloud_network_benchmark.contracts import (
    ARTIFACTS,
    ArtifactExpectation,
    BenchmarkPhase,
    CampaignState,
    CleanupState,
    CommandOutcome,
    ExecutionState,
    MeasurementDirection,
    Provider,
    Scenario,
    VmRole,
    ArtifactLayout,
)


def values(enum: object) -> set[str]:
    return {item.value for item in enum}


def test_stable_enum_values() -> None:
    assert values(Provider) == {"aws", "azure", "gcp"}
    assert values(Scenario) == {"same_zone", "cross_zone", "placement_optimization", "inter_region"}
    assert values(VmRole) == {"vm_a", "vm_b"}
    assert values(MeasurementDirection) == {"vm_a_to_vm_b"}
    assert values(BenchmarkPhase) == {"idle_latency", "single_flow", "multi_flow"}
    assert values(CommandOutcome) == {"succeeded", "failed", "timed_out", "interrupted"}
    assert values(ArtifactExpectation) == {"required", "optional", "not_expected"}
    assert values(ExecutionState) == {"initialized", "provision_ready", "running", "collecting", "validating", "succeeded", "failed", "interrupted"}
    assert values(CleanupState) == {"not_started", "not_required", "attempted", "succeeded", "failed"}
    assert values(CampaignState) == {"initialized", "active", "partially_complete", "succeeded", "failed", "interrupted"}


def test_canonical_artifact_paths() -> None:
    assert ARTIFACTS.config_snapshot == "config.yaml"
    assert ARTIFACTS.resolved_snapshot == "config.resolved.json"
    assert ARTIFACTS.provider_outputs == "terraform-outputs.redacted.json"
    assert ARTIFACTS.day_probe_csv_template == "measurements/day/probes/{chunk_id}.csv"
    assert ARTIFACTS.vm_a_metadata == "metadata/vm_a.json"
    assert ARTIFACTS.validation_summary == "validation/summary.txt"
    assert not any("flent" in key.lower() for key in ARTIFACTS.model_dump())


def test_artifact_paths_cannot_be_overridden() -> None:
    with pytest.raises(ValueError):
        ArtifactLayout(config_snapshot="../../outside")


@pytest.mark.parametrize("filesystem_buffer", [8192, 1024 * 1024], ids=["local", "nfs-sized"])
def test_materialized_integrity_streams_with_one_buffer_and_checks_partial_tail(tmp_path, monkeypatch, filesystem_buffer):
    import hashlib
    import tracemalloc
    from pathlib import Path
    from cloud_network_benchmark.contracts.artifacts import ArtifactRecord
    from cloud_network_benchmark.contracts.common import Evidence, StringEvidence

    path = tmp_path / "native.jsonl"
    payload = b"native bytes\n" * 300_000 + b"partial tail"
    path.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest()
    # Model the filesystem-dependent default buffer, including large NFS blocks.
    original_open = Path.open
    resolved_path = path.resolve()

    def filesystem_open(self, mode="r", buffering=-1, *args, **kwargs):
        if self == resolved_path and mode == "rb" and buffering == -1:
            buffering = filesystem_buffer
        return original_open(self, mode, buffering, *args, **kwargs)

    monkeypatch.setattr(Path, "open", filesystem_open)
    record = ArtifactRecord(artifact_id="native-test", path=path.name, kind="iperf3-server-jsonl",
        producer="vm_b", unit_id=None, deployment_id="synthetic-deployment", closed=True,
        size_bytes=Evidence[int](value=len(payload)), sha256=StringEvidence(value=expected),
        retrieval_state="verified")
    checkpoints = []
    tracemalloc.start()
    try:
        record.verify_materialized_file(tmp_path, budget_check=lambda size, digest: checkpoints.append((size, digest)))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1536 * 1024
    assert checkpoints[-1] == (len(payload), expected)
    assert [size for size, _ in checkpoints] == sorted(size for size, _ in checkpoints)
    # A changed byte in the short final buffer must still invalidate the hash.
    with path.open("r+b") as stream:
        stream.seek(-1, 2); stream.write(b"X")
    with pytest.raises(ValueError, match="SHA-256"):
        record.verify_materialized_file(tmp_path)
