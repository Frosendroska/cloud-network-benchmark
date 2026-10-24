from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from cloud_network_benchmark.acquisition import iter_source_windows
from cloud_network_benchmark.contracts.artifacts import ArtifactInput
from cloud_network_benchmark.contracts.common import Evidence, VmRole
from cloud_network_benchmark.contracts.measurements import ClockMapping, CommandEvidence
from cloud_network_benchmark.contracts.common import StringEvidence
from cloud_network_benchmark.contracts.campaign import PhaseConfiguration
from cloud_network_benchmark.lifecycle_bundle import (bind_retrieved_acquisition_sources,
    build_closed_inventory, finalize_validation, retrieve_closed_bundle)
from cloud_network_benchmark.validation import validate_bundle
from tests.fixtures.f02r.support import connection, deadline, result as command_result
from tests.integration.test_acquisition_producer import _build, _build_records, _command
from tests.f03_support import observation
from tests.unit.test_validation import make_input


@pytest.mark.parametrize("mode", ["acceptance", "short_study", "calibration", "diagnostic_day"])
def test_closed_inventory_fake_transport_retrieves_and_replays_source_fixture(mode, repository_root, tmp_path):
    """Exercise F09 inventory/retrieval with the existing F03 raw source grammar."""
    value = make_input(repository_root, mode_override=None if mode == "diagnostic_day" else mode)
    c = connection(tmp_path).model_copy(update={"identity": value.identity,
        "deployment_id": value.identity.deployment_id, "run_id": value.identity.run_id})
    unit_id = ("diagnostic-day" if mode == "diagnostic_day"
               else observation(repository_root, mode).measurement_units[-1].unit_id)
    anchor = datetime.now(timezone.utc)
    anchor_ns = int(anchor.timestamp() * 1_000_000_000)
    sock_log = (Path(__file__).parents[1] / "fixtures/acquisition/sockperf-window.log").read_bytes()
    sock_log = sock_log.replace(b'"realtime_ns":20', f'"realtime_ns":{anchor_ns}'.encode())
    sock_csv = b"Synthetic native Sockperf CSV bytes; no traffic was run.\n"
    remote_roots = {"vm_a":tmp_path / "remote-a", "vm_b":tmp_path / "remote-b"}
    for root in remote_roots.values(): root.mkdir()
    (remote_roots["vm_a"] / "measurements").mkdir()
    (remote_roots["vm_a"] / "measurements/sockperf.stderr").write_bytes(sock_log)
    (remote_roots["vm_a"] / "measurements/sockperf.csv").write_bytes(sock_csv)
    declarations = [
        ArtifactInput(artifact_id="sock-log", path="measurements/sockperf.stderr", kind="sockperf-observation-stderr",
                producer="vm_a", unit_id=unit_id, expectation="required"),
        ArtifactInput(artifact_id="sock-native", path="measurements/sockperf.csv", kind="sockperf-csv",
                producer="vm_a", unit_id=unit_id, expectation="required"),
    ]
    ended = datetime.now(timezone.utc)
    inventory = build_closed_inventory(identity=c.identity.model_dump(), deployment_id=c.deployment_id,
        remote_workspace="/var/lib/cnb/work", declarations=declarations, source_roots=remote_roots,
        source_vm_ids={"vm_a":c.vm_a.host, "vm_b":c.vm_b.host}, measurement_ended_at=ended,
        closure={"vm_a":ended+timedelta(seconds=1), "vm_b":ended+timedelta(seconds=1)})
    source_by_path = {str(Path("/var/lib/cnb/work") / item.destination_path):
        (remote_roots[item.source_role] / item.destination_path).read_bytes() for item in inventory.items}
    class TransferRunner:
        def run(self, request):
            item = next(item for item in inventory.items if item.artifact_id in request.action_id)
            if request.action_type == "ssh":
                payload = source_by_path[item.source_path]
                return command_result(request.action_id, f"{len(payload)} {hashlib.sha256(payload).hexdigest()}")
            Path(request.argv[-1]).write_bytes(source_by_path[item.source_path])
            return command_result(request.action_id)
    bundle = tmp_path / "bundle"; bundle.mkdir()
    report = retrieve_closed_bundle(connection=c, manifest=inventory, runner=TransferRunner(), filesystem=bundle,
        deadline=deadline(), retry_admission=lambda _item:False, reservation_id="reservation-f09",
        provider_trigger=ended+timedelta(minutes=5), retry_seconds=1,
        now_utc=lambda:ended, clock=lambda:100)
    assert report.complete and len(report.records) == 2, (report.failures, report.attempts,
        [action.result for action in report.actions],
        [(record.artifact_id, record.retrieval_state, record.failure) for record in report.records])
    for record in report.records: record.verify_materialized_file(bundle)

    build = _build("sockperf", VmRole.VM_A, "sock-build")
    build_records = _build_records(bundle, c.deployment_id, [build])
    command = _command("sockperf-short-multiflow", VmRole.VM_A, unit_id, c.deployment_id)
    mapping = ClockMapping(vm_role=VmRole.VM_A, monotonic_seconds=1, utc_timestamp=anchor,
        estimated_error_ms=Evidence[float](value=1), synchronization_source={"value":"fixture"})
    command_evidence = CommandEvidence(schema_version=2, request=command.request,
        result={"action_id": command.request["action_id"], "outcome":"succeeded"},
        producer=command.producer, deployment_id=command.deployment_id, unit_id=command.unit_id,
        binary_version=StringEvidence(value="sockperf 3.10"), source_identity=StringEvidence(value="fixture"),
        monotonic_started=Evidence[float](value=1), monotonic_finished=Evidence[float](value=2))
    value_payload = {**value.model_dump(mode="python"),
        "identity": c.identity.model_dump(mode="python"),
        "commands": [command_evidence.model_dump(mode="python")],
        "artifacts": {**value.artifacts, **{item.artifact_id: item for item in declarations},
            build.artifact_id: ArtifactInput(artifact_id=build.artifact_id, path=build_records[0].path,
                kind="build-identity", producer=VmRole.VM_A, unit_id=None, expectation="required")},
        "timings": [{**timing.model_dump(mode="python"), "deployment_id": c.deployment_id}
                    for timing in value.timings]}
    if mode == "diagnostic_day":
        from cloud_network_benchmark.contracts.common import BenchmarkPhase
        day = PhaseConfiguration(unit_id=unit_id, kind="continuous_day", phase=BenchmarkPhase.MULTI_FLOW,
            measured_seconds=86400, streams=4)
        chunk = PhaseConfiguration(unit_id="probe-chunk-000001", kind="probe_chunk",
            phase=BenchmarkPhase.MULTI_FLOW, measured_seconds=300, parent_unit_id=unit_id)
        old_timing = value.timings[-1].model_dump(mode="python")
        value_payload.update(mode=mode, measurement_units=[day.model_dump(mode="python"), chunk.model_dump(mode="python")],
            enabled_phases=[BenchmarkPhase.MULTI_FLOW.value],
            timings=[{**old_timing, "unit_id": unit_id, "deployment_id": c.deployment_id}])
    value = type(value).model_validate(value_payload)
    for record in [*report.records, *build_records]:
        declaration = value.artifacts[record.artifact_id]
        assert (record.path, record.kind, record.producer, record.unit_id) == (
            declaration.path, declaration.kind, declaration.producer, declaration.unit_id), record
    bound, sources, unavailable = bind_retrieved_acquisition_sources(value=value,
        child_root=bundle, source_artifact_records=[*report.records, *build_records], build_evidence=[build],
        receiver_command_evidence=[], clock_mappings=[mapping])
    assert len(sources) == 1 and sources[0].tool == "sockperf"
    assert not unavailable
    timing = next(item for item in bound.timings if item.unit_id == unit_id)
    assert timing.acquisition_sources.value[0].mapping_indexes == [0]
    source_hashes = {record.path: hashlib.sha256((bundle / record.path).read_bytes()).hexdigest()
                     for record in [*report.records, *build_records]}
    result, summary, seal, sealed_files = finalize_validation(root=bundle,
        validator_input_path=bundle / "validation/input.json", value=bound,
        evaluated_at=datetime.now(timezone.utc))
    saved = type(bound).model_validate_json((bundle / "validation/input.json").read_text())
    assert saved.model_dump(mode="json") == bound.model_dump(mode="json")
    windows = list(iter_source_windows(bundle, sources[0], [*report.records, *build_records],
        [command_evidence], [build], timing.clock_mappings))
    assert windows and windows[0].has_positive_inner_support
    replay = validate_bundle(saved, evaluated_at=datetime.now(timezone.utc))
    assert replay.run_id == saved.identity.run_id == result.run_id
    assert summary and len(seal) == 64 and "checksums.sha256" not in sealed_files
    assert source_hashes == {record.path: hashlib.sha256((bundle / record.path).read_bytes()).hexdigest()
                             for record in [*report.records, *build_records]}
