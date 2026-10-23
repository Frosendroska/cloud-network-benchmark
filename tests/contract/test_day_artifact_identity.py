"""DAY artifact identities at the shared ValidatorInput boundary."""
from __future__ import annotations

import hashlib

import pytest

from cloud_network_benchmark.contracts import ARTIFACTS, ArtifactRecord, Evidence, StringEvidence
from cloud_network_benchmark.contracts.common import IdentityContext
from cloud_network_benchmark.contracts.campaign import PhaseConfiguration
from cloud_network_benchmark.contracts.validation import ValidatorInput, artifact_inputs


DAY = PhaseConfiguration(unit_id="study-day-17", kind="continuous_day", phase="multi_flow", measured_seconds=86400)
CHUNK_A = PhaseConfiguration(unit_id="probe-chunk-000001", kind="probe_chunk", phase="multi_flow", measured_seconds=300, parent_unit_id=DAY.unit_id)
CHUNK_B = PhaseConfiguration(unit_id="probe-chunk-000002", kind="probe_chunk", phase="multi_flow", measured_seconds=300, parent_unit_id=DAY.unit_id)
UNITS = [DAY, CHUNK_A, CHUNK_B]
ROOT = "results/raw/run"


def _record(artifact_id: str, path: str, kind: str, unit_id: str | None, *, producer="vm_a", deployment="campaign-aws-deployment") -> ArtifactRecord:
    payload = b"native fixture bytes"
    return ArtifactRecord(artifact_id=artifact_id, path=path, kind=kind, producer=producer,
        unit_id=unit_id, deployment_id=deployment, closed=True, size_bytes=Evidence(value=len(payload)),
        sha256=StringEvidence(value=hashlib.sha256(payload).hexdigest()), retrieval_state="verified")


def _input(records: list[ArtifactRecord], units=UNITS):
    artifacts = artifact_inputs(ARTIFACTS, mode="diagnostic_day", measurement_units=units)
    return ValidatorInput(schema_version=2,
        identity=IdentityContext(experiment_id="EXP-900", config_id="exp-900-diagnostic-day", campaign_id="campaign",
            observation_id="campaign-aws-observation", run_id="campaign-aws", deployment_id="campaign-aws-deployment", provider="aws"),
        mode="diagnostic_day", context="standard", campaign_manifest_path="results/manifests/campaign.json",
        child_manifest_path="results/manifests/run.json", child_result_root=ROOT,
        config_snapshot_path=f"{ROOT}/config.yaml", config_sha256="a" * 64,
        resolved_snapshot_path=f"{ROOT}/config.resolved.json", provider="aws", scenario="same_zone",
        enabled_phases=["multi_flow"], expected_vm_roles=["vm_a", "vm_b"], measurement_direction="vm_a_to_vm_b",
        artifacts=artifacts, measurement_units=units, artifact_records=records,
        execution_state="initialized", cleanup_state="not_started", failure=None, cleanup_failure=None,
        vm_metadata={"unavailable_reason":"pending"}, provider_metadata={"unavailable_reason":"pending"},
        tool_versions={"unavailable_reason":"pending"}, implementation_git_commit={"unavailable_reason":"pending"},
        design_git_commit={"unavailable_reason":"pending"})


def test_layout_associates_fixed_day_artifacts_with_actual_parent_and_keeps_templates_scoped():
    artifacts = artifact_inputs(ARTIFACTS, mode="diagnostic_day", measurement_units=UNITS)
    assert artifacts["day-iperf3-client"].unit_id == DAY.unit_id
    assert artifacts["day-iperf3-observation"].unit_id == DAY.unit_id
    assert artifacts["iperf3_server_log"].unit_id is None
    assert artifacts["iperf3_server_error"].unit_id is None
    for name in ("day-probe-csv", "day-probe-log", "day-probe-observation"):
        assert artifacts[name].unit_id is None


def test_concrete_chunk_records_resolve_exactly_to_template_and_parent():
    records = [
        _record("day-iperf3-client", "measurements/day/iperf3-client.jsonl", "iperf3-client-jsonl", DAY.unit_id),
        _record("day-iperf3-observation", "measurements/day/iperf3-acquisition.stderr", "iperf3-observation-stderr", DAY.unit_id),
        _record("day-probe-csv-probe-chunk-000001", "measurements/day/probes/probe-chunk-000001.csv", "sockperf-csv", CHUNK_A.unit_id),
        _record("day-probe-log-probe-chunk-000002", "measurements/day/probes/probe-chunk-000002.log", "sockperf-log", CHUNK_B.unit_id),
    ]
    accepted = _input(records)
    assert accepted.artifact_records == records


@pytest.mark.parametrize("changes", [
    {"artifact_id": "day-probe-csv-probe-chunk-000002"},
    {"path": "measurements/day/probes/other.csv"},
    {"unit_id": DAY.unit_id},
    {"unit_id": "undeclared-chunk"},
    {"kind": "sockperf-log"},
    {"producer": "vm_b"},
    {"deployment_id": "other-deployment"},
])
def test_modern_chunk_mutations_are_rejected_without_legacy_fallback(changes):
    record = _record("day-probe-csv-probe-chunk-000001", "measurements/day/probes/probe-chunk-000001.csv", "sockperf-csv", CHUNK_A.unit_id)
    mutated = record.model_copy(update=changes)
    with pytest.raises(ValueError):
        _input([mutated])


def test_duplicate_concrete_identity_is_rejected():
    record = _record("day-probe-csv-probe-chunk-000001", "measurements/day/probes/probe-chunk-000001.csv", "sockperf-csv", CHUNK_A.unit_id)
    with pytest.raises(ValueError, match="unique concrete identity"):
        _input([record, record])


@pytest.mark.parametrize("record_unit", [None, DAY.unit_id])
def test_historical_base_family_record_remains_readable_without_renaming(record_unit):
    records = [
        _record("day-iperf3-client", "measurements/day/iperf3-client.jsonl", "iperf3-client-jsonl", None),
        _record("day-iperf3-observation", "measurements/day/iperf3-acquisition.stderr", "iperf3-observation-stderr", None),
        _record("day-probe-csv", "measurements/day/probes/old-a.csv", "sockperf-csv", record_unit),
        _record("day-probe-csv", "measurements/day/probes/old-b.csv", "sockperf-csv", record_unit),
    ]
    current = _input(records)
    assert [(row.artifact_id, row.path, row.unit_id) for row in current.artifact_records] == [
        (row.artifact_id, row.path, row.unit_id) for row in records]


def test_legacy_client_identity_is_admitted_against_original_declaration_too():
    record = _record("day-iperf3-client", "measurements/day/iperf3-client.jsonl", "iperf3-client-jsonl", None)
    value = _input([]).model_dump()
    value["artifacts"]["day-iperf3-client"]["unit_id"] = None
    value["artifact_records"] = [record.model_dump()]
    admitted = ValidatorInput.model_validate(value)
    assert admitted.artifact_records[0].unit_id is None
