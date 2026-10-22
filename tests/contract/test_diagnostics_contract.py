from datetime import datetime, timezone
from cloud_network_benchmark import diagnostics_guest as guest
from cloud_network_benchmark.diagnostics import _declarations, iter_clock_mappings, iter_samples
from cloud_network_benchmark.contracts.common import Evidence, StringEvidence
from cloud_network_benchmark.contracts.measurements import ClockMapping, DiagnosticSample, VmMetadata
import json
from jsonschema import validate
from pathlib import Path


def evidence(value=None, reason="fixture unavailable"):
    return Evidence(value=value) if value is not None else Evidence(unavailable_reason=reason)


def test_wire_rows_validate_against_unchanged_models(tmp_path):
    sample=guest.derive_sample({"cpu":[10,0,0,90,0,0,0,0],"tcp":0,"nic":dict(rx_dropped=0,tx_dropped=0,rx_errors=0,tx_errors=0,rx_bytes=0,tx_bytes=0)},
        {"cpu":[11,0,0,99,0,0,0,0],"tcp":0,"nic":dict(rx_dropped=0,tx_dropped=0,rx_errors=0,tx_errors=0,rx_bytes=0,tx_bytes=0)},
        deployment_id="RUN-deployment",vm_role="vm_a",sampled_at=datetime.now(timezone.utc),monotonic_seconds=1,interval_seconds=1)
    # Fixture snapshots here use aggregate CPU shape; it remains a valid strict shared sample.
    parsed=DiagnosticSample.model_validate(sample)
    path=tmp_path/"samples.jsonl"; path.write_text(parsed.model_dump_json()+"\n")
    assert len(list(iter_samples(path))) == 1
    schema_root=Path(__file__).parents[2]/"specs/003-v2-shared-contracts/contracts"
    validate(parsed.model_dump(mode="json"),json.loads((schema_root/"diagnostic-sample.schema.json").read_text()))
    clock=ClockMapping(vm_role="vm_a",monotonic_seconds=1,utc_timestamp=datetime.now(timezone.utc),
        estimated_error_ms=evidence(2.0),synchronization_source=StringEvidence(value="chronyc tracking synchronized; reference=test"))
    cp=tmp_path/"clock.jsonl"; cp.write_text(clock.model_dump_json()+"\n")
    assert len(list(iter_clock_mappings(cp))) == 1


def test_artifact_declarations_are_role_specific_and_unverified():
    items=_declarations("vm_b")
    assert [(x.artifact_id,x.path,x.kind,x.producer,x.unit_id) for x in items] == [
        ("vm_b_metadata","metadata/vm_b.json","vm_metadata","vm_b",None),
        ("vm_b_diagnostics","diagnostics/vm_b.jsonl","diagnostics","vm_b",None),
        ("vm_b_clock","diagnostics/vm_b-clock.jsonl","clock_mapping","vm_b",None)]


def test_metadata_materializes_to_existing_schema():
    metadata=VmMetadata(schema_version=2,deployment_id="RUN-deployment",vm_role="vm_b",provider="gcp",
        cpu_model=StringEvidence(value="fixture cpu"),nic_driver=StringEvidence(unavailable_reason="not observed"),
        acceleration=StringEvidence(unavailable_reason="not verified"),image=StringEvidence(value="image-fixture"),
        kernel=StringEvidence(value="kernel-fixture"),congestion_control=StringEvidence(value="cubic"),
        tool_versions=Evidence[dict](value={"sockperf":{"version":{"value":"fixture"}}}),
        source_builds=Evidence[dict](unavailable_reason="not supplied"),
        documented_network_limit=Evidence[dict](value={"source":"dated fixture","limit":"GCP-specific"}),
        mtu=Evidence[int](value=9000),observed_region=StringEvidence(value="region-fixture"),
        observed_zone=StringEvidence(value="zone-fixture"),physical_zone_id=StringEvidence(unavailable_reason="not exposed"),
        actual_shape=StringEvidence(value="shape-fixture"))
    schema=Path(__file__).parents[2]/"specs/003-v2-shared-contracts/contracts/vm-metadata.schema.json"
    validate(metadata.model_dump(mode="json"),json.loads(schema.read_text()))


def test_truncated_last_line_is_reported_without_rewriting(tmp_path):
    path=tmp_path/"broken.jsonl"; original=b'{"schema_version":2'
    path.write_bytes(original)
    try: list(iter_samples(path))
    except ValueError as exc: assert "incomplete JSONL" in str(exc)
    else: raise AssertionError("truncated line accepted")
    assert path.read_bytes() == original
