from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from cloud_network_benchmark.contracts.artifacts import ArtifactInput, ArtifactLayout
from cloud_network_benchmark.contracts.common import FailureEvidence
from cloud_network_benchmark.lifecycle_bundle import lifecycle_declarations
from cloud_network_benchmark.manifests import update_child_runtime_evidence


def test_existing_artifact_layout_is_extended_without_changing_day_identity():
    from cloud_network_benchmark.contracts.campaign import PhaseConfiguration
    units = [PhaseConfiguration(unit_id="day", kind="continuous_day", phase="multi_flow", measured_seconds=86400),
             PhaseConfiguration(unit_id="chunk-a", kind="probe_chunk", phase="multi_flow", measured_seconds=300, parent_unit_id="day")]
    declarations = lifecycle_declarations(ArtifactLayout.for_mode("diagnostic_day", units),
                                          mode="diagnostic_day", measurement_units=units)
    indexed = {item.artifact_id: item for item in declarations}
    assert indexed["day-probe-csv"].path_template == "measurements/day/probes/{chunk_id}.csv"
    assert indexed["day-probe-csv"].unit_id is None
    assert indexed["f02r-vm_a-sockperf-build"].path == "metadata/build/vm_a-sockperf.identity"


def test_runtime_manifest_updates_revalidate_and_preserve_first_retrieval_failure(tmp_path):
    fixture = Path(__file__).parents[1] / "fixtures/manifests/initialized-child.json"
    manifest_path = tmp_path / "child.json"
    manifest_path.write_bytes(fixture.read_bytes())
    from cloud_network_benchmark.manifests import ChildRunManifest
    original = ChildRunManifest.model_validate_json(manifest_path.read_text())
    first_time = original.updated_at + timedelta(seconds=1)
    first = FailureEvidence(category="copy", message="first copy failure", occurred_at=first_time)
    updated = update_child_runtime_evidence(manifest_path, first_time, retrieval_failure=first)
    later_time = first_time + timedelta(seconds=1)
    second = FailureEvidence(category="hash", message="later integrity failure", occurred_at=later_time)
    again = update_child_runtime_evidence(manifest_path, later_time, retrieval_failure=second)
    assert again.run_id == original.run_id
    assert again.retrieval_failure == first
    assert ChildRunManifest.model_validate_json(manifest_path.read_text()) == again


def test_runtime_manifest_update_rejects_backdated_and_empty_updates(tmp_path):
    fixture = Path(__file__).parents[1] / "fixtures/manifests/initialized-child.json"
    manifest_path = tmp_path / "child.json"; manifest_path.write_bytes(fixture.read_bytes())
    from cloud_network_benchmark.manifests import ChildRunManifest
    original = ChildRunManifest.model_validate_json(manifest_path.read_text())
    with pytest.raises(ValueError, match="at least one"):
        update_child_runtime_evidence(manifest_path, original.updated_at)
    with pytest.raises(ValueError, match="occurred_at"):
        update_child_runtime_evidence(manifest_path, original.updated_at - timedelta(seconds=1),
            retrieval_failure=FailureEvidence(category="copy", message="bad", occurred_at=original.updated_at))
