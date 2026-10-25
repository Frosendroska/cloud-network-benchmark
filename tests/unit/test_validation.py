from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import pytest

from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.contracts import IdentityContext, ValidatorInput, artifact_inputs
from cloud_network_benchmark.contracts.campaign import PhaseConfiguration, derive_measurement_units
from cloud_network_benchmark.contracts.common import ComparisonEligibility, DataValidity, Evidence, ExperimentMode, FailureEvidence, StringEvidence, ValidationStatus
from cloud_network_benchmark.contracts.measurements import ClockMapping, CommandEvidence, DiagnosticSample, MeasurementTiming, TimingGap
from cloud_network_benchmark.contracts.admission import ScheduleEntry
from cloud_network_benchmark.contracts.artifacts import ArtifactLayout, ArtifactRecord
from cloud_network_benchmark.contracts.validation import ValidationResult
from cloud_network_benchmark.native import NativeParse, ProbeCounts, ReceiverInterval, RttSample, SourceRef, rtt_statistics
from cloud_network_benchmark.validation import _command_findings, _coverage, _measured_samples, _receiver_session_for_unit, _resolved_snapshot_findings, _sequence_timing_findings, _validate_artifacts, evaluate_comparison, validate_bundle

NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)


def make_input(repository_root, provider="aws", campaign="camp", *, config_name="exp-001-multi-provider.yaml", mode_override=None, config_path=None):
    resolved, _ = resolve_campaign(config_path or repository_root / "configs/experiments" / config_name, repository_root)
    obs = next(item for item in resolved.observations if item.provider == provider)
    mode = ExperimentMode(mode_override) if mode_override else resolved.mode
    units = derive_measurement_units(mode, resolved.benchmark) if mode_override else obs.measurement_units
    if mode == ExperimentMode.ACCEPTANCE:
        units = [unit.model_copy(update={"measured_seconds": 10}) for unit in units]
    units = [unit.model_copy(update={"streams": 4}) if mode == ExperimentMode.SHORT_STUDY
             and unit.kind == "phase" and unit.phase == "multi_flow" and unit.streams is None else unit for unit in units]
    run_id = f"{campaign}-{provider}"
    deployment = f"{run_id}-deployment"
    identity = IdentityContext(experiment_id=resolved.experiment_id, config_id=resolved.config_id,
                               campaign_id=campaign, observation_id=f"{campaign}-{provider}-obs",
                               run_id=run_id, deployment_id=deployment, provider=provider)
    start = NOW
    timings = []
    elapsed = 0
    for unit in units:
        probe_start = start + timedelta(seconds=elapsed)
        measured_offset = 5 if unit.phase == "idle_latency" else 10
        measured_start = probe_start + timedelta(seconds=measured_offset)
        timings.append(MeasurementTiming(unit_id=unit.unit_id, deployment_id=deployment,
            probe_test_started_at={"value":probe_start},
            tcp_started_at=(Evidence[datetime](unavailable_reason="no TCP client in idle phase") if unit.phase == "idle_latency"
                            else Evidence[datetime](value=probe_start + timedelta(seconds=7))),
            measured_started_at={"value":measured_start}, measured_finished_at={"value":measured_start + timedelta(seconds=unit.measured_seconds)},
            monotonic_started={"value":float(elapsed + 1)}, monotonic_finished={"value":float(elapsed + unit.measured_seconds + 1)},
            omitted_seconds=5, overlap_seconds={"value":unit.measured_seconds}))
        elapsed += unit.measured_seconds + (5 if unit.phase == "idle_latency" else 10) + 10
    row = "slot-one,EXP-001,P0"
    schedule = ScheduleEntry(schema_version=2, entry_id="slot-one", config_id=resolved.config_id,
        experiment_id=resolved.experiment_id, scheduled_start=NOW, window_label="morning",
        source_path="schedule.csv", source_sha256="c" * 64,
        row_sha256=hashlib.sha256(row.encode()).hexdigest(), original_header="entry_id,experiment_id,priority",
        original_row=row, columns=["entry_id", "experiment_id", "priority"], priority="P0")
    return ValidatorInput(schema_version=2, identity=identity, mode=mode, context=resolved.context,
        campaign_manifest_path="results/manifests/camp.json", child_manifest_path=f"results/manifests/{run_id}.json",
        child_result_root="results/raw/"+run_id, config_snapshot_path="results/raw/"+run_id+"/config.yaml",
        config_sha256=resolved.config_source.sha256, resolved_snapshot_path="results/raw/"+run_id+"/config.resolved.json",
        provider=provider, scenario=resolved.scenario, enabled_phases=list(dict.fromkeys(item.phase for item in units)),
        expected_vm_roles=["vm_a", "vm_b"], measurement_direction="vm_a_to_vm_b",
        artifacts=artifact_inputs(ArtifactLayout.for_mode(mode, units), mode=mode, measurement_units=units),
        measurement_units=units, timings=timings,
        schedule_entry={"value": schedule},
        execution_state="succeeded", cleanup_state="succeeded", failure=None, cleanup_failure=None,
        vm_metadata={"unavailable_reason":"not supplied"}, provider_metadata={"unavailable_reason":"not supplied"},
        tool_versions={"unavailable_reason":"not supplied"}, implementation_git_commit={"value":"a"*40},
        design_git_commit={"value":"b"*40})


def good_result(value, source_campaign=None):
    identity = value.identity.model_dump(mode="json") | {"provider":str(value.provider), "scenario":str(value.scenario),
        "mode":str(value.mode), "context":value.context}
    provenance = {"config_sha256":value.config_sha256, "source_refs":[]}
    units = [{"unit_id":unit.unit_id, "kind":unit.kind, "phase":str(getattr(unit.phase,"value",unit.phase)),
              "streams":unit.streams, "coverage":{"requested_seconds":unit.measured_seconds}}
             for unit in value.measurement_units]
    return ValidationResult(schema_version=2, run_id=value.identity.run_id, mode=value.mode,
        validation_status=ValidationStatus(data_validity=DataValidity.VALID, data_reasons=[],
            comparison_eligibility=ComparisonEligibility.UNAVAILABLE, comparison_reasons=["not matched yet"]),
        metrics={"value":{"identity":identity, "provenance":provenance, "units":units,
                          "common_n":{"state":"frozen","common_n":4,"source_campaign_id":source_campaign or value.identity.campaign_id,
                                      "source_sha256":"f"*64,"plateau_found":True},
                          "commands":[item.model_dump(mode="json") for item in value.commands],
                          "timings":[item.model_dump(mode="json") for item in value.timings],
                          "execution":{"execution_state":"succeeded","bundle_complete":True}}}, evaluated_at=NOW)


def test_empty_materialized_bundle_is_unavailable_and_explains_missing_evidence(repository_root):
    value = make_input(repository_root)
    result = validate_bundle(value, evaluated_at=NOW)
    assert result.validation_status.data_validity == "unavailable"
    assert result.validation_status.comparison_eligibility == "unavailable"
    assert any(f.code == "artifact.missing" for f in result.findings)
    assert any(f.code == "identity.campaign_manifest_missing" for f in result.findings)
    assert any(f.code == "identity.child_manifest_missing" for f in result.findings)
    assert result.metrics.value["execution"]["bundle_complete"] is False
    assert len(result.metrics.value["units"]) == 3


def test_f08_intersects_materialized_loaded_sockperf_and_receiver_sources(repository_root, tmp_path):
    from cloud_network_benchmark.acquisition import bind_acquisition_sources
    from cloud_network_benchmark.bootstrap import materialize_build_identity_artifacts
    from cloud_network_benchmark.contracts import ArtifactInput, ArtifactExpectation, VmRole
    from cloud_network_benchmark.validation import _acquisition_windows, _coverage, _measured_samples, _source_supported_windows
    from cloud_network_benchmark.native import parse_iperf3, parse_sockperf
    from tests.integration.test_acquisition_producer import _build, _native_iperf

    value = make_input(repository_root)
    unit = value.measurement_units[1]
    root = tmp_path
    build_sock = _build("sockperf", VmRole.VM_A, "f08-loaded-sockperf-build")
    build_iperf = _build("iperf3", VmRole.VM_B, "f08-loaded-iperf-build")
    builds = [build_sock, build_iperf]
    build_records = materialize_build_identity_artifacts(root, value.identity.deployment_id, builds)
    sock_id, sock_log_id = f"{unit.unit_id}-sockperf", f"{unit.unit_id}-sockperf-observation"
    client_id, server_log_id = f"{unit.unit_id}-iperf3", "iperf3_server_error"
    sock_path = f"measurements/{unit.unit_id}/sockperf.csv"
    sock_log_path = f"measurements/{unit.unit_id}/sockperf-acquisition.stderr"
    client_path = f"measurements/{unit.unit_id}/iperf3.json"
    server_log_path = "server-logs/iperf3-server.err"
    cookie = "materialized-loaded-session"
    sock_action = f"sockperf-{unit.unit_id}"
    receiver_action = "f02r-vm_b-listener-iperf3"
    boot_a, boot_b = "12345678-1234-1234-1234-123456789abc", "12345678-1234-1234-1234-123456789def"

    def write(relpath, payload):
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return payload

    def record(artifact_id, relpath, kind, producer, unit_id, payload):
        return ArtifactRecord(artifact_id=artifact_id, path=relpath, kind=kind, producer=producer,
            unit_id=unit_id, deployment_id=value.identity.deployment_id, closed=True,
            size_bytes={"value":len(payload)}, sha256={"value":hashlib.sha256(payload).hexdigest()},
            retrieval_state="verified")

    sock_csv = write(sock_path, ("sockperf: test output\n------------------------------\n"
        "packet, txTime(sec), rxTime(sec), rtt(usec)\n"
        "0, 10.001000, 10.001001, 1.000\n1, 64.999000, 64.999001, 1.000\n").encode())
    sock_rows = [
        {"format":1,"tool":"sockperf","semantics_id":"sockperf-3.10-enabled-v1",
         "invocation_id":sock_action,"pid":42,"boot_id":boot_a,"clock":"CLOCK_MONOTONIC",
         "record_kind":"clock_anchor","mono_before_ns":0,"realtime_ns":int(NOW.timestamp()*1e9),"mono_after_ns":1000},
        {"format":1,"tool":"sockperf","semantics_id":"sockperf-3.10-enabled-v1",
         "invocation_id":sock_action,"pid":42,"boot_id":boot_a,"clock":"CLOCK_MONOTONIC",
         "record_kind":"probe_window","sender_enabled_ns":1_000_000_000,"receiver_enabled_ns":1_000_000_000,
         "termination_ns":70_000_000_000,"native_filter_start_ns":10_000_000_000,
         "native_filter_end_ns":70_000_000_000,"termination_reason":"timer"},
    ]
    sock_log = write(sock_log_path, b"".join(b"CNB_ACQ_V1 " + json.dumps(row,separators=(",",":")).encode()+b"\n" for row in sock_rows))
    native_client = json.loads(_native_iperf(cookie, duration=60, start=5, end=65,
        byte_count=125_000_000, include_omitted=False))
    native_client["intervals"] = native_client["server_output_json"]["intervals"]
    native_client["end"] = {"sum_received":{"bytes":125_000_000,"seconds":60}}
    client_json = write(client_path, (json.dumps(native_client)+"\n").encode())
    receiver_rows = [
        {"format":1,"tool":"iperf3","semantics_id":"iperf-3.17.1-stats-v1",
         "invocation_id":receiver_action,"pid":51,"boot_id":boot_b,"clock":"CLOCK_MONOTONIC",
         "record_kind":"clock_anchor","cookie":cookie,"mono_before_ns":0,
         "realtime_ns":int(NOW.timestamp()*1e9),"mono_after_ns":1000},
        {"format":1,"tool":"iperf3","semantics_id":"iperf-3.17.1-stats-v1",
         "invocation_id":receiver_action,"pid":51,"boot_id":boot_b,"clock":"CLOCK_MONOTONIC",
         "record_kind":"session_settings","cookie":cookie,"protocol":"TCP","num_streams":1,
         "omit":5,"duration":60,"interval_us":1_000_000,"block_size":131072,"reverse":False},
        {"format":1,"tool":"iperf3","semantics_id":"iperf-3.17.1-stats-v1",
         "invocation_id":receiver_action,"pid":51,"boot_id":boot_b,"clock":"CLOCK_MONOTONIC",
         "record_kind":"stats_origin","cookie":cookie,"stream_id":"17","phase":"initial",
         "origin_us":0,"start_time_fixed_us":0},
        {"format":1,"tool":"iperf3","semantics_id":"iperf-3.17.1-stats-v1",
         "invocation_id":receiver_action,"pid":51,"boot_id":boot_b,"clock":"CLOCK_MONOTONIC",
         "record_kind":"stats_origin","cookie":cookie,"stream_id":"17","phase":"post_omit",
         "origin_us":5_000_000,"start_time_fixed_us":0},
        {"format":1,"tool":"iperf3","semantics_id":"iperf-3.17.1-stats-v1",
         "invocation_id":receiver_action,"pid":51,"boot_id":boot_b,"clock":"CLOCK_MONOTONIC",
         "record_kind":"receiver_interval","cookie":cookie,"stream_id":"17","role":"receiver",
         "protocol":"TCP","local_host":"10.0.0.2","local_port":"5201","remote_host":"10.0.0.1",
         "remote_port":"50100","interval_start_us":10_000_000,"interval_end_us":70_000_000,
         "bytes":125_000_000,"omitted":False},
    ]
    receiver_log = write(server_log_path, b"".join(b"CNB_ACQ_V1 "+json.dumps(row,separators=(",",":")).encode()+b"\n" for row in receiver_rows))
    sock_record = record(sock_id,sock_path,"sockperf-csv",VmRole.VM_A,unit.unit_id,sock_csv)
    sock_log_record = record(sock_log_id,sock_log_path,"sockperf-observation-stderr",VmRole.VM_A,unit.unit_id,sock_log)
    client_record = record(client_id,client_path,"iperf3-json",VmRole.VM_A,unit.unit_id,client_json)
    receiver_log_record = record(server_log_id,server_log_path,"iperf3-observation-stderr",VmRole.VM_B,None,receiver_log)
    clock_a = ClockMapping(vm_role=VmRole.VM_A,monotonic_seconds=0,utc_timestamp=NOW,
        estimated_error_ms={"value":0},synchronization_source={"value":"materialized fixture"})
    clock_b = ClockMapping(vm_role=VmRole.VM_B,monotonic_seconds=0,utc_timestamp=NOW,
        estimated_error_ms={"value":0},synchronization_source={"value":"materialized fixture"})
    sock_command = CommandEvidence(schema_version=2,request={"action_id":sock_action,
        "acquisition_invocation_id":sock_action,"argv":[build_sock.binary_path,"under-load","-t","70"]},
        result={"action_id":sock_action,"outcome":"succeeded"},producer=VmRole.VM_A,
        deployment_id=value.identity.deployment_id,unit_id=unit.unit_id,
        binary_version={"value":build_sock.binary_version},source_identity={"value":build_sock.source_reference},
        started_at={"value":NOW},finished_at={"value":NOW+timedelta(seconds=70)},
        monotonic_started={"value":0},monotonic_finished={"value":70})
    client_command = CommandEvidence(schema_version=2,request={"action_id":f"iperf3-{unit.unit_id}",
        "argv":[build_iperf.binary_path,"-c","10.0.0.2","-P","1","-t","60"]},
        result={"action_id":f"iperf3-{unit.unit_id}","outcome":"succeeded"},producer=VmRole.VM_A,
        deployment_id=value.identity.deployment_id,unit_id=unit.unit_id,
        binary_version={"value":build_iperf.binary_version},source_identity={"value":build_iperf.source_reference},
        monotonic_started={"value":5},monotonic_finished={"value":65})
    receiver_command = CommandEvidence(schema_version=2,request={"action_id":receiver_action,
        "argv":[build_iperf.binary_path,"-s","--json-stream","--forceflush",
                f"--property=Environment=CNB_ACQUISITION_ID={receiver_action}"]},
        result={"action_id":receiver_action,"outcome":"succeeded"},producer=VmRole.VM_B,
        deployment_id=value.identity.deployment_id,unit_id=value.measurement_units[0].unit_id,
        binary_version={"value":build_iperf.binary_version},source_identity={"value":build_iperf.source_reference},
        monotonic_started={"value":0},monotonic_finished={"value":86400})
    records = [sock_record,sock_log_record,client_record,receiver_log_record,*build_records]
    sources = bind_acquisition_sources(child_root=root,run_identity=value.identity,
        commands=[sock_command,client_command],receiver_command_evidence=[receiver_command],
        build_evidence=builds,source_artifact_records=records,clock_mappings=[clock_a,clock_b])
    assert {source.tool for source in sources} == {"sockperf","iperf3"}
    declarations = dict(value.artifacts)
    declarations[server_log_id] = ArtifactInput(artifact_id=server_log_id,path=server_log_path,
        kind=receiver_log_record.kind,producer=VmRole.VM_B,unit_id=None,expectation=ArtifactExpectation.REQUIRED)
    declarations.update({item.artifact_id:ArtifactInput(artifact_id=item.artifact_id,path=item.path,
        kind=item.kind,producer=item.producer,unit_id=item.unit_id,expectation=ArtifactExpectation.REQUIRED)
        for item in build_records})
    old_timing = next(item for item in value.timings if item.unit_id == unit.unit_id)
    timing = old_timing.model_copy(update={"probe_test_started_at":Evidence[datetime](value=NOW),
        "measured_started_at":Evidence[datetime](value=NOW+timedelta(seconds=10)),
        "measured_finished_at":Evidence[datetime](value=NOW+timedelta(seconds=70)),
        "clock_mappings":[clock_a,clock_b],"acquisition_sources":Evidence(value=list(sources))})
    prepared = value.model_copy(update={"child_result_root":str(root),"artifacts":declarations,
        "artifact_records":records,"commands":[sock_command,client_command,receiver_command],"timings":[timing]})
    windows, errors = _acquisition_windows(prepared,timing)
    assert not errors
    assert {tool for tool,_,_ in windows} == {"sockperf","iperf3"}
    by_tool = {tool:(start,end) for tool,start,end in windows}
    assert by_tool["sockperf"] == (NOW.timestamp()+10,NOW.timestamp()+70)
    assert by_tool["iperf3"] == pytest.approx((NOW.timestamp()+10,NOW.timestamp()+70),abs=1e-6)
    supported = _source_supported_windows(unit,{unit.unit_id:(windows,errors)})
    assert len(supported) == 1
    assert supported[0][0] == pytest.approx(NOW.timestamp()+10,abs=1e-6)
    assert supported[0][1] == pytest.approx(NOW.timestamp()+70,abs=1e-6)
    sock_source = SourceRef(sock_id,sock_path,sock_record.sha256.value,unit.unit_id,
        value.identity.deployment_id,"vm_a","3.10")
    sock = parse_sockperf(root/sock_path,None,source=sock_source)
    tcp = parse_iperf3(root/client_path,source=SourceRef(client_id,client_path,client_record.sha256.value,
        unit.unit_id,value.identity.deployment_id,"vm_a","3.17.1"),format="json")
    coverage = _coverage(prepared,unit,sock,tcp,{unit.unit_id:(windows,errors)})
    assert coverage["active_probe_seconds"]["value"] == pytest.approx(60,abs=1e-6)
    assert coverage["complete"] is True
    assert [sample.packet_index for sample in _measured_samples(prepared,unit,sock,{unit.unit_id:(windows,errors)})] == [0,1]


def test_f08_replays_sockperf_acquisition_window_from_verified_materialized_source(repository_root, tmp_path):
    from cloud_network_benchmark.acquisition import bind_acquisition_sources
    from cloud_network_benchmark.bootstrap import materialize_build_identity_artifacts
    from cloud_network_benchmark.contracts import ArtifactInput, ArtifactExpectation
    from cloud_network_benchmark.validation import _acquisition_windows
    from tests.integration.test_acquisition_producer import _build

    value = make_input(repository_root)
    unit = value.measurement_units[0]
    root = tmp_path
    csv_bytes = b"sockperf native CSV\n"
    csv_path = root / "measurements" / unit.unit_id / "sockperf.csv"
    csv_path.parent.mkdir(parents=True)
    csv_path.write_bytes(csv_bytes)
    log_id, csv_id = f"{unit.unit_id}-sockperf-observation", f"{unit.unit_id}-sockperf"
    action_id = f"sockperf-{unit.unit_id}"
    boot = "12345678-1234-1234-1234-123456789abc"
    records_raw = [
        {"format": 1, "tool": "sockperf", "semantics_id": "sockperf-3.10-enabled-v1",
         "invocation_id": action_id, "pid": 42, "boot_id": boot, "clock": "CLOCK_MONOTONIC",
         "record_kind": "clock_anchor", "mono_before_ns": 0, "realtime_ns": int(NOW.timestamp()*1e9),
         "mono_after_ns": 1000},
        {"format": 1, "tool": "sockperf", "semantics_id": "sockperf-3.10-enabled-v1",
         "invocation_id": action_id, "pid": 42, "boot_id": boot, "clock": "CLOCK_MONOTONIC",
         "record_kind": "probe_window", "sender_enabled_ns": 1_000_000_000,
         "receiver_enabled_ns": 1_000_000_000, "termination_ns": 65_000_000_000,
         "native_filter_start_ns": 5_000_000_000, "native_filter_end_ns": 65_000_000_000,
         "termination_reason": "timer"},
    ]
    log_bytes = b"".join(b"CNB_ACQ_V1 " + json.dumps(row, separators=(",", ":")).encode() + b"\n"
                         for row in records_raw)
    log_path = root / "measurements" / unit.unit_id / "sockperf-acquisition.stderr"
    log_path.write_bytes(log_bytes)

    def record(artifact_id, path, kind, producer, unit_id, payload):
        return ArtifactRecord(artifact_id=artifact_id, path=str(path.relative_to(root)), kind=kind,
            producer=producer, unit_id=unit_id, deployment_id=value.identity.deployment_id,
            closed=True, size_bytes={"value":len(payload)}, sha256={"value":hashlib.sha256(payload).hexdigest()},
            retrieval_state="verified")

    native_record = record(csv_id, csv_path, "sockperf-csv", "vm_a", unit.unit_id, csv_bytes)
    log_record = record(log_id, log_path, "sockperf-observation-stderr", "vm_a", unit.unit_id, log_bytes)
    build = _build("sockperf", __import__("cloud_network_benchmark.contracts", fromlist=["VmRole"]).VmRole.VM_A,
                   "f08-vm-a-sockperf-build")
    build_records = materialize_build_identity_artifacts(root, value.identity.deployment_id, [build])
    build_record = build_records[0]
    declarations = dict(value.artifacts)
    declarations[log_id] = ArtifactInput(artifact_id=log_id, path=log_record.path,
        kind=log_record.kind, producer=log_record.producer, unit_id=unit.unit_id,
        expectation=ArtifactExpectation.REQUIRED)
    declarations[csv_id] = ArtifactInput(artifact_id=csv_id, path=native_record.path,
        kind=native_record.kind, producer=native_record.producer, unit_id=unit.unit_id,
        expectation=ArtifactExpectation.REQUIRED)
    declarations[build_record.artifact_id] = ArtifactInput(artifact_id=build_record.artifact_id,
        path=build_record.path, kind=build_record.kind, producer=build_record.producer, unit_id=None,
        expectation=ArtifactExpectation.REQUIRED)
    mapping = ClockMapping(vm_role="vm_a", monotonic_seconds=0, utc_timestamp=NOW,
        estimated_error_ms={"value":0}, synchronization_source={"value":"materialized source fixture"})
    command = CommandEvidence(schema_version=2,
        request={"action_id":action_id, "argv":[build.binary_path, "under-load", "-t", "65"],
                 "acquisition_invocation_id":action_id},
        result={"action_id":action_id, "outcome":"succeeded"}, producer="vm_a",
        deployment_id=value.identity.deployment_id, unit_id=unit.unit_id,
        binary_version={"value":build.binary_version}, source_identity={"value":build.source_reference},
        monotonic_started={"value":0}, monotonic_finished={"value":65},
        started_at={"value":NOW}, finished_at={"value":NOW+timedelta(seconds=65)})
    base_records = [log_record, native_record, *build_records]
    sources = bind_acquisition_sources(child_root=root, run_identity=value.identity, commands=[command],
        build_evidence=[build], source_artifact_records=base_records, clock_mappings=[mapping])
    assert len(sources) == 1
    source = sources[0].model_copy(update={"mapping_indexes":[0]})
    timing = value.timings[0].model_copy(update={"clock_mappings":[mapping],
        "acquisition_sources":Evidence(value=[source])})
    prepared = value.model_copy(update={"child_result_root":str(root), "artifacts":declarations,
        "artifact_records":base_records, "commands":[command], "timings":[timing]})
    windows, errors = _acquisition_windows(prepared, timing)
    assert not errors
    assert windows == [("sockperf", NOW.timestamp()+5, NOW.timestamp()+65)]
    source_ref = SourceRef(csv_id, native_record.path, native_record.sha256.value, unit.unit_id,
                           value.identity.deployment_id, "vm_a", "3.10")
    sock = NativeParse(samples=(RttSample(0, 5.001, 5.001001, 1.0, source_ref, 1),
                                RttSample(1, 64.999, 64.999001, 1.0, source_ref, 2)))
    coverage = _coverage(prepared, unit, sock, None, {unit.unit_id:(windows, errors)})
    assert coverage["active_probe_seconds"]["value"] == pytest.approx(60,abs=1e-6)
    assert coverage["complete"] is True
    assert [sample.packet_index for sample in _measured_samples(prepared, unit, sock, {unit.unit_id:(windows, errors)})] == [0, 1]


@pytest.mark.parametrize(("config_name", "mode", "units"), [
    ("exp-001-multi-provider.yaml", "acceptance", 3),
    ("exp-001-multi-provider.yaml", "short_study", 3),
    ("exp-001-calibration.yaml", "calibration", 12),
    ("exp-001-diagnostic-day.yaml", "diagnostic_day", 1),
])
def test_all_modes_keep_their_declared_unit_shape(repository_root, config_name, mode, units):
    value = make_input(repository_root, config_name=config_name,
                       mode_override="acceptance" if mode == "acceptance" else None)
    result = validate_bundle(value, evaluated_at=NOW)
    observed = result.metrics.value["units"]
    assert len(observed) == units
    assert result.metrics.value["research_eligible_mode"] is (mode == "short_study")


def test_validation_replay_is_deterministic_and_rejects_naive_evaluation_time(repository_root):
    value = make_input(repository_root)
    first = validate_bundle(value, evaluated_at=NOW)
    second = validate_bundle(value, evaluated_at=NOW)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    import pytest
    with pytest.raises(ValueError, match="timezone-aware"):
        validate_bundle(value, evaluated_at=datetime(2026, 10, 21))


def test_comparison_uses_actual_start_skew_and_returns_copied_results(repository_root):
    values = [make_input(repository_root, p) for p in ("aws", "azure", "gcp")]
    adjusted = []
    for value, seconds in zip(values, (0, 60.001, 0)):
        timing = value.timings[1].model_copy(update={
            "measured_started_at": Evidence[datetime](value=NOW + timedelta(seconds=seconds))})
        adjusted.append(value.model_copy(update={"timings": [*value.timings[:1], timing, *value.timings[2:]]}))
    originals = [good_result(value) for value in adjusted]
    output = evaluate_comparison(adjusted, originals)
    assert all(item.validation_status.comparison_eligibility == "eligible" for item in output.values())
    assert any(f.code == "comparison.start_target_degraded" for result in output.values() for f in result.findings)
    assert all(item.validation_status.comparison_eligibility == "unavailable" for item in originals)


def test_comparison_accepts_prior_calibration_campaign_and_binds_timing(repository_root):
    values = [make_input(repository_root, provider) for provider in ("aws", "azure", "gcp")]
    results = [good_result(value, "prior-calibration-campaign") for value in values]
    output = evaluate_comparison(values, results)
    assert all(item.validation_status.comparison_eligibility == "eligible" for item in output.values())
    changed = values[0].model_copy(update={"timings":[values[0].timings[0].model_copy(update={
        "measured_started_at":Evidence[datetime](value=NOW+timedelta(seconds=1))}), *values[0].timings[1:]]})
    with pytest.raises(ValueError, match="timing evidence"):
        evaluate_comparison([changed, *values[1:]], results)


def test_comparison_rejects_over_300_second_skew(repository_root):
    values = [make_input(repository_root, p) for p in ("aws", "azure", "gcp")]
    adjusted = [value.model_copy(update={"timings":[*value.timings[:1], value.timings[1].model_copy(update={
        "measured_started_at": Evidence[datetime](value=NOW + timedelta(seconds=skew))}), *value.timings[2:]]})
        for value, skew in zip(values, (0, 300.001, 0))]
    output = evaluate_comparison(adjusted, [good_result(value) for value in adjusted])
    assert all(item.validation_status.comparison_eligibility == "ineligible" for item in output.values())


@pytest.mark.parametrize(("skew", "eligibility", "degraded"), [
    (60, "eligible", False), (60.001, "eligible", True), (300, "eligible", True),
    (300.001, "ineligible", False),
])
def test_comparison_skew_boundaries(repository_root, skew, eligibility, degraded):
    values = [make_input(repository_root, p) for p in ("aws", "azure", "gcp")]
    adjusted = []
    for value, seconds in zip(values, (0, skew, 0)):
        timing = value.timings[1].model_copy(update={
            "measured_started_at": Evidence[datetime](value=NOW + timedelta(seconds=seconds))})
        adjusted.append(value.model_copy(update={"timings": [*value.timings[:1], timing, *value.timings[2:]]}))
    output = evaluate_comparison(adjusted, [good_result(value) for value in adjusted])
    assert all(item.validation_status.comparison_eligibility == eligibility for item in output.values())
    assert all(any(f.code == "comparison.start_target_degraded" for f in item.findings) == degraded
               for item in output.values())


def test_comparison_missing_actual_start_is_unavailable(repository_root):
    values = [make_input(repository_root, p) for p in ("aws", "azure", "gcp")]
    missing = values[1].timings[1].model_copy(update={"measured_started_at": Evidence[datetime](unavailable_reason="not captured")})
    values[1] = values[1].model_copy(update={"timings":[*values[1].timings[:1], missing, *values[1].timings[2:]]})
    output = evaluate_comparison(values, [good_result(value) for value in values])
    assert all(item.validation_status.comparison_eligibility == "unavailable" for item in output.values())


def _coverage_inputs(repository_root, *, duration=60.0, gap_seconds=0.0):
    value = make_input(repository_root)
    unit = value.measurement_units[1]
    start = NOW
    gaps = [TimingGap(started_at=start + timedelta(seconds=30),
                      finished_at=start + timedelta(seconds=30 + gap_seconds), reason="recorded gap")] if gap_seconds else []
    mappings = [
        ClockMapping(vm_role="vm_a", monotonic_seconds=100, utc_timestamp=start,
                     estimated_error_ms={"value": 0}, synchronization_source={"value": "offline fixture"}),
        ClockMapping(vm_role="vm_b", monotonic_seconds=200, utc_timestamp=start,
                     estimated_error_ms={"value": 0}, synchronization_source={"value": "offline fixture"}),
    ]
    timing = MeasurementTiming(unit_id=unit.unit_id, deployment_id=value.identity.deployment_id,
        probe_test_started_at={"value": start}, tcp_started_at={"value": start + timedelta(seconds=5)},
        measured_started_at={"value": start + timedelta(seconds=5)},
        measured_finished_at={"value": start + timedelta(seconds=5 + duration)},
        monotonic_started={"value": 100}, monotonic_finished={"value": 100 + duration},
        omitted_seconds=5, overlap_seconds={"value": min(60, duration)}, gaps=gaps, clock_mappings=mappings)
    probe_command = CommandEvidence(schema_version=2,
        request={"argv":["sockperf","under-load","-i","10.0.0.2","-t","65"]},
        result={"outcome":"succeeded"},producer="vm_a",deployment_id=value.identity.deployment_id,
        unit_id=unit.unit_id,binary_version={"value":"3.10"},source_identity={"value":"fixture"},
        started_at={"value":start},finished_at={"value":start+timedelta(seconds=65)},
        monotonic_started={"value":100},monotonic_finished={"value":165})
    value = value.model_copy(update={"timings": [timing], "commands":[probe_command]})
    count = ProbeCounts(sent=Evidence[int](value=65000), received=Evidence[int](value=60000),
        dropped=Evidence[int](value=0), duplicated=Evidence[int](value=0), out_of_order=Evidence[int](value=0),
        skipped=Evidence[int](value=0), runtime_seconds=Evidence[float](value=65),
        reporting_scope="one Sockperf invocation", source=SourceRef("sockperf-log", "sockperf.log", "d" * 64))
    sample_source = SourceRef("sockperf-csv", "sockperf.csv", "f" * 64, unit.unit_id, value.identity.deployment_id, "vm_a", "3.10")
    samples = (RttSample(0, 105, 105.000001, 1, sample_source, 1),
               RttSample(1, 164.999999, 165.0, 1, sample_source, 2))
    sock = NativeParse(samples=samples, probe_counts=(count,))
    tcp = NativeParse(receiver_intervals=(ReceiverInterval("1", 5, 5 + duration, duration, 1000,
        False, False, SourceRef("iperf3_receiver", "iperf3.json", "e" * 64), 1),))
    return value, unit, sock, tcp


@pytest.mark.parametrize("duration", [59.999, 60.0, 60.001])
def test_runtime_and_point_samples_cannot_prove_continuous_probe_coverage(repository_root, duration):
    value, unit, sock, tcp = _coverage_inputs(repository_root, duration=duration)
    result = _coverage(value, unit, sock, tcp)
    assert result["complete"] is False
    assert result["active_probe_ratio"]["value"] is None
    assert "source-bound active-probe acquisition intervals" in result["tcp_start_bracket"]["unavailable_reason"]


def test_loaded_source_support_intersects_probe_and_receiver_and_unions_streams(repository_root):
    from cloud_network_benchmark.validation import _source_supported_windows

    value, unit, _, _ = _coverage_inputs(repository_root)
    acquisition = {unit.unit_id: ([
        ("sockperf", NOW.timestamp() + 5, NOW.timestamp() + 70),
        ("iperf3", NOW.timestamp() + 10, NOW.timestamp() + 65),
        ("iperf3", NOW.timestamp() + 10, NOW.timestamp() + 65),
    ], [])}
    assert _source_supported_windows(unit, acquisition) == [(NOW.timestamp() + 10, NOW.timestamp() + 65)]


def test_rtt_selection_excludes_warmup_and_samples_outside_half_open_measured_window(repository_root):
    value, unit, sock, _ = _coverage_inputs(repository_root)
    ref = SourceRef("sockperf-csv", "samples.csv", "f"*64, unit.unit_id, value.identity.deployment_id, "vm_a", "3.10")
    rows = [RttSample(i, tx, rx, (rx-tx)*1_000_000, ref, i+1) for i, (tx, rx) in enumerate([
        (104.999999, 105.0), (105.0, 105.000001), (164.999999, 164.999999), (165.0, 165.000001)])]
    native = NativeParse(samples=tuple(rows))
    assert _measured_samples(value, unit, native) == ()
    acquisition = {unit.unit_id: ([("sockperf", NOW.timestamp() + 5, NOW.timestamp() + 65),
                            ("iperf3", NOW.timestamp() + 5, NOW.timestamp() + 65)], [])}
    selected = _measured_samples(value, unit, native, acquisition)
    assert [item.packet_index for item in selected] == [1, 2]


def test_process_runtime_without_native_sample_bounds_cannot_prove_coverage(repository_root):
    value, unit, sock, tcp = _coverage_inputs(repository_root)
    uncorroborated = _coverage(value, unit, NativeParse(probe_counts=sock.probe_counts), tcp)
    assert uncorroborated["complete"] is False
    assert uncorroborated["active_probe_seconds"]["value"] is None


def test_day_requires_one_continuous_receiver_span_and_unions_chunk_tails(repository_root, monkeypatch):
    value = make_input(repository_root, config_name="exp-001-diagnostic-day.yaml")
    day = value.measurement_units[0]
    day_timing = value.timings[0].model_copy(update={
        "probe_test_started_at":Evidence[datetime](value=NOW),
        "tcp_started_at":Evidence[datetime](value=NOW),
        "measured_started_at":Evidence[datetime](value=NOW),
        "measured_finished_at":Evidence[datetime](value=NOW+timedelta(seconds=86400)),
        "clock_mappings":[ClockMapping(vm_role="vm_b", monotonic_seconds=0, utc_timestamp=NOW,
            estimated_error_ms={"value":0}, synchronization_source={"value":"fixture"})]})
    chunks=[]
    timings=[day_timing]
    chunk_parses={}
    source=SourceRef("day-probe-csv", "day.csv", "f"*64, None, value.identity.deployment_id, "vm_a", "3.10")
    for index in range(288):
        unit_id=f"chunk-{index+1:03d}"
        chunks.append(day.model_copy(update={"unit_id":unit_id, "kind":"probe_chunk", "measured_seconds":300,
            "parent_unit_id":day.unit_id, "trial_index":None, "streams":None}))
        offset=index*300
        gap=([TimingGap(started_at=NOW+timedelta(seconds=offset+100),
                        finished_at=NOW+timedelta(seconds=offset+101), reason="one-second probe gap")]
             if index==1 else [])
        timings.append(day_timing.model_copy(update={"unit_id":unit_id,
            "probe_test_started_at":Evidence[datetime](value=NOW+timedelta(seconds=offset)),
            "tcp_started_at":Evidence[datetime](unavailable_reason="no new TCP start during probe rotation"),
            "measured_started_at":Evidence[datetime](unavailable_reason="probe timing only"),
            "measured_finished_at":Evidence[datetime](unavailable_reason="probe timing only"),
            "monotonic_started":Evidence[float](value=offset),
            "monotonic_finished":Evidence[float](value=offset+(305 if index==0 else 300)),
            "omitted_seconds":5 if index==0 else 0, "gaps":gap,
            "clock_mappings":[ClockMapping(vm_role="vm_a", monotonic_seconds=0, utc_timestamp=NOW,
                estimated_error_ms={"value":0}, synchronization_source={"value":"fixture"})]}))
        first=offset+(5 if index==0 else 0)
        last=offset+(305 if index==0 else 300)
        samples=(RttSample(0, first, first+.000001, 1, source, 1),
                 RttSample(1, last-.000001, last, 1, source, 2))
        count=ProbeCounts(sent=Evidence[int](value=300000), received=Evidence[int](value=300000),
            dropped=Evidence[int](value=0), duplicated=Evidence[int](value=0), out_of_order=Evidence[int](value=0),
            skipped=Evidence[int](value=0), runtime_seconds=Evidence[float](value=300),
            reporting_scope="[Valid Duration]", source=SourceRef("day-probe-log", "day.log", "e"*64))
        chunk_parses[unit_id]=NativeParse(samples=samples, probe_counts=(count,))
    full=value.model_copy(update={"measurement_units":[day,*chunks], "timings":timings})
    import cloud_network_benchmark.validation as module
    monkeypatch.setattr(module, "_parse_unit", lambda _value, unit, _records:
                        (chunk_parses.get(unit.unit_id, NativeParse()), None))
    receiver=ReceiverInterval("1", 0, 86400, 86400, 1_000_000, False, False,
                              SourceRef("iperf3_server_log", "server.jsonl", "a"*64), 1)
    sock, tcp=NativeParse(), NativeParse(receiver_intervals=(receiver,), sessions=("1",))
    day_coverage=module._coverage(full, day, sock, tcp)
    assert day_coverage["complete"] is False
    assert day_coverage["active_probe_ratio"]["value"] is None
    assert day_coverage["active_probe_seconds"]["value"] is None
    assert "source-bound active-probe acquisition intervals" in day_coverage["tcp_start_bracket"]["unavailable_reason"]
    gapped_tcp=NativeParse(receiver_intervals=(
        ReceiverInterval("1", 0, 85400, 85400, 900_000, False, False, tcp.receiver_intervals[0].source, 1),
        ReceiverInterval("1", 86400, 87400, 1000, 100_000, False, False, tcp.receiver_intervals[0].source, 2)),
        sessions=("1",))
    rejected=module._coverage(full, day, sock, gapped_tcp)
    assert rejected["observed_receiver_seconds"]["value"] == 86400
    assert rejected["complete"] is False


@pytest.mark.parametrize("command_finish", [165, 225, 164])
def test_conservative_command_native_bracket_cannot_prove_continuous_coverage(repository_root, command_finish):
    value, unit, sock, tcp = _coverage_inputs(repository_root)
    ref = SourceRef("iperf3_receiver", "iperf3.json", "e" * 64)
    tcp = NativeParse(receiver_intervals=(
        ReceiverInterval("1", 0, 5, 5, 0, True, False, ref, 1),
        *tcp.receiver_intervals))
    timing = value.timings[0].model_copy(update={
        "unit_id": unit.unit_id,
        "probe_test_started_at": Evidence[datetime](value=NOW),
        "measured_started_at": Evidence[datetime](unavailable_reason="not captured"),
        "measured_finished_at": Evidence[datetime](unavailable_reason="not captured"),
        "clock_mappings": [ClockMapping(vm_role="vm_a", monotonic_seconds=100, utc_timestamp=NOW,
            estimated_error_ms={"value": 0}, synchronization_source={"value": "offline fixture"})],
    })
    command = CommandEvidence(schema_version=2,
        request={"argv":["iperf3", "-c", "10.0.0.2"]}, result={"outcome":"succeeded"},
        producer="vm_a", deployment_id=value.identity.deployment_id, unit_id=unit.unit_id,
        binary_version={"value":"3.17.1"}, source_identity={"value":"fixture build"},
        monotonic_started={"value":100}, monotonic_finished={"value":command_finish})
    probe_command = CommandEvidence(schema_version=2,
        request={"argv":["sockperf","under-load","-i","10.0.0.2","-t","65"]},
        result={"outcome":"succeeded"},producer="vm_a",deployment_id=value.identity.deployment_id,
        unit_id=unit.unit_id,binary_version={"value":"3.10"},source_identity={"value":"fixture"},
        started_at={"value":NOW},finished_at={"value":NOW+timedelta(seconds=65)},
        monotonic_started={"value":100},monotonic_finished={"value":165})
    value = value.model_copy(update={"timings":[timing], "commands":[probe_command,command]})
    result = _coverage(value, unit, sock, tcp)
    assert result["active_probe_seconds"]["value"] is None
    assert result["active_probe_ratio"]["value"] is None
    assert "source-bound active-probe acquisition intervals" in result["tcp_start_bracket"]["unavailable_reason"]


def test_frozen_common_n_consistency_and_inflation_rejects_incomplete_idle_baseline(repository_root, monkeypatch):
    value = make_input(repository_root)
    idle = value.measurement_units[0].unit_id
    loaded = value.measurement_units[1].unit_id
    parses = {}
    for unit_id, samples in ((idle, (10.0, 12.0)), (loaded, (14.0, 16.0))):
        ref = SourceRef("sockperf-csv", f"{unit_id}.csv", "f" * 64, unit_id,
                        value.identity.deployment_id, "vm_a", "3.10")
        from cloud_network_benchmark.native import RttSample
        sample_rows = tuple(RttSample(i, 0 if i == 0 else 60 - sample / 1_000_000 - .000001,
            (0 if i == 0 else 60 - sample / 1_000_000 - .000001) + sample / 1_000_000,
            sample, ref, i + 1) for i, sample in enumerate(samples))
        counts = ProbeCounts(sent=Evidence[int](value=65000), received=Evidence[int](value=60000),
            dropped=Evidence[int](value=0), duplicated=Evidence[int](value=0), out_of_order=Evidence[int](value=0),
            skipped=Evidence[int](value=0), runtime_seconds=Evidence[float](value=65),
            reporting_scope="one Sockperf invocation", source=SourceRef("sockperf-log", "sockperf.log", "d"*64))
        parses[unit_id] = NativeParse(samples=sample_rows, probe_counts=(counts,))
    from cloud_network_benchmark.contracts.measurements import ClockMapping
    timed = []
    for timing, offset in zip(value.timings, (5, 10, 10)):
        mappings = [ClockMapping(vm_role="vm_a", monotonic_seconds=0,
            utc_timestamp=NOW + timedelta(seconds=offset), estimated_error_ms={"value":0},
            synchronization_source={"value":"fixture"})]
        if timing.unit_id == loaded:
            mappings.append(ClockMapping(vm_role="vm_b", monotonic_seconds=0,
                utc_timestamp=NOW + timedelta(seconds=offset), estimated_error_ms={"value":0},
                synchronization_source={"value":"fixture"}))
        timed.append(timing.model_copy(update={"clock_mappings": mappings}))
    value = value.model_copy(update={"timings":timed})
    import cloud_network_benchmark.validation as module
    monkeypatch.setattr(module, "_parse_unit", lambda _value, unit, _records:
                        (parses.get(unit.unit_id, NativeParse()),
                         NativeParse(receiver_intervals=(ReceiverInterval("1", 10, 70, 60, 1000, False, False,
                             SourceRef("iperf3", "receiver.json", "e"*64), 1),)) if unit.unit_id == loaded else None))
    result = module.validate_bundle(value, evaluated_at=NOW)
    inflation = result.metrics.value["units"][1]["rtt"]["latency_inflation_us"]["value"]
    assert inflation is None
    assert result.metrics.value["units"][0]["data_validity"] != "valid"
    assert result.metrics.value["common_n"]["state"] == "pending"


def test_frozen_common_n_snapshot_contradiction_is_invalid_and_explicit(repository_root, tmp_path):
    value = make_input(repository_root)
    resolved, _ = resolve_campaign(repository_root / "configs/experiments/exp-001-multi-provider.yaml", repository_root)
    snapshot = resolved.model_dump(mode="json")
    snapshot["benchmark"]["common_n"] = {"state":"frozen", "common_n":4,
        "source_campaign_id":"calibration-campaign", "source_sha256":"d" * 64, "plateau_found":True}
    snapshot["launch_blockers"].remove("calibration_pending")
    for unit in snapshot["measurement_units"]:
        if unit["phase"] == "multi_flow":
            unit["streams"] = 4
    # The resolved snapshot freezes N=4; the supplied native attempt claims N=2.
    for observation in snapshot["observations"]:
        observation["benchmark"]["common_n"] = snapshot["benchmark"]["common_n"]
        for unit in observation["measurement_units"]:
            if unit["phase"] == "multi_flow":
                unit["streams"] = 4
    path = tmp_path / "resolved.json"
    payload = json.dumps(snapshot, sort_keys=True).encode()
    path.write_bytes(payload)
    record = ArtifactRecord(artifact_id="resolved_snapshot", path="resolved.json", kind="resolved_configuration",
        producer="controller", unit_id=None, deployment_id=value.identity.deployment_id, closed=True,
        size_bytes={"value":len(payload)}, sha256={"value":hashlib.sha256(payload).hexdigest()}, retrieval_state="verified")
    units = [unit.model_copy(update={"streams":2}) if unit.phase == "multi_flow" else unit
             for unit in value.measurement_units]
    changed = value.model_copy(update={"measurement_units":units, "child_result_root":str(tmp_path),
        "resolved_snapshot_path":"resolved.json", "resolved_sha256":Evidence[str](value=hashlib.sha256(payload).hexdigest()),
        "artifact_records":[record]})
    result = validate_bundle(changed, evaluated_at=NOW)
    codes = {finding.code for finding in result.findings}
    assert "identity.resolved_units" in codes, codes
    assert "calibration.frozen_n_mismatch" in codes, codes
    assert result.validation_status.data_validity == "invalid"


def test_materialized_diagnostic_chunks_extend_resolved_day_without_redefining_it(repository_root, tmp_path):
    value = make_input(repository_root, config_name="exp-001-diagnostic-day.yaml")
    day = value.measurement_units[0]
    chunk = PhaseConfiguration(unit_id="probe-chunk-001", kind="probe_chunk", phase="multi_flow",
        measured_seconds=300, streams=4, parent_unit_id=day.unit_id)
    resolved, _ = resolve_campaign(repository_root / "configs/experiments/exp-001-diagnostic-day.yaml", repository_root)
    payload = json.dumps(resolved.model_dump(mode="json"), sort_keys=True).encode()
    (tmp_path / "resolved.json").write_bytes(payload)
    record = ArtifactRecord(artifact_id="resolved_snapshot", path="resolved.json", kind="resolved_configuration",
        producer="controller", unit_id=None, deployment_id=value.identity.deployment_id, closed=True,
        size_bytes={"value":len(payload)}, sha256={"value":hashlib.sha256(payload).hexdigest()}, retrieval_state="verified")
    extended = value.model_copy(update={"measurement_units":[day, chunk], "child_result_root":str(tmp_path),
        "resolved_snapshot_path":"resolved.json", "artifact_records":[record]})
    findings, raw = _resolved_snapshot_findings(extended)
    assert raw
    assert not any(item.code in {"identity.resolved_units", "identity.resolved_chunks"} for item in findings)
    invalid_chunk = chunk.model_copy(update={"parent_unit_id":"other-day"})
    extended = extended.model_copy(update={"measurement_units":[day, invalid_chunk]})
    findings, _ = _resolved_snapshot_findings(extended)
    assert any(item.code == "identity.resolved_chunks" for item in findings)


@pytest.mark.parametrize(("sent", "below"), [(949, True), (950, False), (951, False)])
def test_generation_ratio_uses_native_sent_and_runtime(repository_root, monkeypatch, sent, below):
    value = make_input(repository_root)
    counts = ProbeCounts(sent=Evidence[int](value=sent), received=Evidence[int](value=sent),
        dropped=Evidence[int](value=0), duplicated=Evidence[int](value=0), out_of_order=Evidence[int](value=0),
        skipped=Evidence[int](value=0), runtime_seconds=Evidence[float](value=1),
        reporting_scope="one Sockperf invocation", source=SourceRef("sockperf-log", "sockperf.log", "f" * 64))
    import cloud_network_benchmark.validation as module
    monkeypatch.setattr(module, "_parse_unit", lambda *_: (NativeParse(probe_counts=(counts,)), None))
    result = module.validate_bundle(value, evaluated_at=NOW)
    generation = result.metrics.value["units"][0]["probe_counts"][0]["generation_ratio"]["value"]
    assert generation == pytest.approx(sent / 1000)
    assert any(item.code == "generation.below_target" for item in result.findings) is below


def test_clock_alignment_boundary_is_separate_from_a_local_rtt(repository_root):
    value = make_input(repository_root)
    mappings = [ClockMapping(vm_role=role, monotonic_seconds=index, utc_timestamp=NOW,
                estimated_error_ms={"value": error}, synchronization_source={"value": "offline fixture"})
                for role, index, error in (("vm_a", 1, 100), ("vm_b", 2, 100))]
    timings = [item.model_copy(update={"clock_mappings": mappings}) for item in value.timings]
    result = validate_bundle(value.model_copy(update={"timings": timings}), evaluated_at=NOW)
    assert result.metrics.value["units"][0]["alignment"]["one_second_eligible"]["value"] is True
    assert rtt_statistics([2.5])["median_us"].value == 2.5
    excessive = [mappings[0], mappings[1].model_copy(update={"estimated_error_ms": Evidence[float](value=100.001)})]
    failed = validate_bundle(value.model_copy(update={"timings":[item.model_copy(update={"clock_mappings": excessive}) for item in value.timings]}), evaluated_at=NOW)
    assert failed.metrics.value["units"][0]["alignment"]["one_second_eligible"]["value"] is False


def test_coverage_applies_clock_uncertainty_once_to_source_support(repository_root):
    from cloud_network_benchmark.contracts.measurements import ClockMapping

    value = make_input(repository_root, mode_override="short_study")
    unit = next(item for item in value.measurement_units if item.phase == "idle_latency")
    timing = next(item for item in value.timings if item.unit_id == unit.unit_id)
    mapping = ClockMapping(vm_role="vm_a", monotonic_seconds=0, utc_timestamp=NOW,
        estimated_error_ms={"value": 1}, synchronization_source={"value": "materialized fixture"})
    timing = timing.model_copy(update={"clock_mappings": [mapping]})
    value = value.model_copy(update={"timings": [timing if item.unit_id == unit.unit_id else item
        for item in value.timings]})
    start = timing.measured_started_at.value.timestamp()
    finish = timing.measured_finished_at.value.timestamp()

    def coverage_for(window):
        return _coverage(value, unit, NativeParse(), None,
            {unit.unit_id: ([ ("sockperf", *window) ], [])})

    # Acquisition replay has already removed the actual 1 ms uncertainty from
    # these supports. Extra source margin therefore proves the whole selected
    # interval without a second erosion at the selection step.
    enclosed = coverage_for((start - 2, finish + 2))
    assert enclosed["active_probe_seconds"]["value"] == pytest.approx(unit.measured_seconds)
    assert enclosed["complete"] is True
    assert enclosed["tcp_start_bracket"]["coverage_bound"] == \
        "source-inner support within declared measured window"

    # When already-inner support reaches only to the selected endpoints, its
    # genuine 1 ms losses remain visible and do not satisfy idle full support.
    edge_clipped = coverage_for((start + .001, finish - .001))
    assert edge_clipped["active_probe_seconds"]["value"] == pytest.approx(unit.measured_seconds - .002)
    assert edge_clipped["complete"] is False

    unbounded = timing.model_copy(update={"clock_mappings": []})
    value = value.model_copy(update={"timings": [unbounded if item.unit_id == unit.unit_id else item
        for item in value.timings]})
    unknown = _coverage(value, unit, NativeParse(), None,
        {unit.unit_id: ([ ("sockperf", start - 2, finish + 2) ], [])})
    assert unknown["active_probe_seconds"]["value"] is None


def test_materialized_artifact_integrity_and_symlink_escape_are_reported(repository_root, tmp_path):
    value = make_input(repository_root)
    unit = value.measurement_units[0]
    declaration = value.artifacts[f"{unit.unit_id}-sockperf"]
    target = tmp_path / declaration.path
    target.parent.mkdir(parents=True)
    data = (repository_root / "tests/fixtures/f08/native/sockperf.csv").read_bytes()
    target.write_bytes(data)
    record = ArtifactRecord(artifact_id=declaration.artifact_id, path=declaration.path, kind=declaration.kind,
        producer=declaration.producer, unit_id=declaration.unit_id, deployment_id=value.identity.deployment_id,
        closed=True, size_bytes={"value": len(data)}, sha256={"value": hashlib.sha256(data).hexdigest()},
        retrieval_state="verified")
    materialized = value.model_copy(update={"child_result_root": str(tmp_path), "artifact_records": [record]})
    findings, _, _ = _validate_artifacts(materialized)
    assert not any(item.code == "artifact.integrity" for item in findings)
    target.write_bytes(data + b"tampered")
    findings, _, _ = _validate_artifacts(materialized)
    assert any(item.code == "artifact.integrity" for item in findings)
    outside = tmp_path.parent / f"{tmp_path.name}-outside.csv"
    outside.write_bytes(data)
    target.unlink()
    target.symlink_to(outside)
    findings, _, _ = _validate_artifacts(materialized)
    assert any(item.code == "artifact.path_or_read" for item in findings)


def test_undeclared_consumed_native_log_integrity_is_verified(repository_root, tmp_path):
    value = make_input(repository_root)
    data = b"[Valid Duration] RunTime=60 sec; SentMessages=60000; ReceivedMessages=60000\n"
    path = tmp_path / "sockperf.log"
    path.write_bytes(data)
    extra = ArtifactRecord(artifact_id="sockperf-log", path="sockperf.log", kind="sockperf-log",
        producer="vm_a", unit_id=value.measurement_units[1].unit_id,
        deployment_id=value.identity.deployment_id, closed=True, size_bytes={"value":len(data)},
        sha256={"value":hashlib.sha256(data).hexdigest()}, retrieval_state="verified")
    materialized = value.model_copy(update={"child_result_root":str(tmp_path), "artifact_records":[extra]})
    assert not any(item.code == "artifact.path_or_read" for item in _validate_artifacts(materialized)[0])
    path.unlink()
    findings = _validate_artifacts(materialized)[0]
    assert any(item.code == "artifact.path_or_read" for item in findings)


def test_diagnostic_sources_must_be_nonempty_and_role_bound(repository_root, tmp_path):
    from cloud_network_benchmark.validation import _validate_materialized_records
    value = make_input(repository_root)
    record = ArtifactRecord(artifact_id="vm_a_diagnostics", path="vm-a.jsonl", kind="diagnostic_samples",
        producer="vm_a", unit_id=None, deployment_id=value.identity.deployment_id, closed=True,
        size_bytes={"value":0}, sha256={"value":hashlib.sha256(b"").hexdigest()}, retrieval_state="verified")
    path = tmp_path / record.path
    path.write_bytes(b"")
    materialized = value.model_copy(update={"child_result_root":str(tmp_path),"artifact_records":[record]})
    findings = _validate_materialized_records(materialized,{})
    assert any(item.code == "provenance.diagnostics_empty" for item in findings)
    wrong = DiagnosticSample(schema_version=2,deployment_id=value.identity.deployment_id,vm_role="vm_b",
        sampled_at=NOW,monotonic_seconds=0,interval_seconds=1,cpu_total_percent={"value":1},
        cpu_max_core_percent={"value":1},cpu_steal_percent={"value":0},tcp_retransmissions={"value":0},
        interface_rx_drops={"value":0},interface_tx_drops={"value":0},interface_rx_errors={"value":0},
        interface_tx_errors={"value":0},rx_bytes={"value":0},tx_bytes={"value":0})
    payload=(wrong.model_dump_json()+"\n").encode()
    path.write_bytes(payload)
    updated=record.model_copy(update={"size_bytes":Evidence[int](value=len(payload)),
        "sha256":StringEvidence(value=hashlib.sha256(payload).hexdigest())})
    findings=_validate_materialized_records(materialized.model_copy(update={"artifact_records":[updated]}),{})
    assert any(item.code == "provenance.diagnostics_identity" for item in findings)


def test_tcp_window_must_intersect_native_sample_support(repository_root):
    value, unit, sock, tcp = _coverage_inputs(repository_root)
    timing = value.timings[0].model_copy(update={
        "unit_id":unit.unit_id,
        "measured_started_at":Evidence[datetime](value=NOW+timedelta(seconds=200)),
        "measured_finished_at":Evidence[datetime](value=NOW+timedelta(seconds=260)),
        "tcp_started_at":Evidence[datetime](value=NOW+timedelta(seconds=200)),
    })
    result = _coverage(value.model_copy(update={"timings":[timing]}), unit, sock, tcp)
    assert result["complete"] is False
    assert result["active_probe_seconds"]["value"] is None
    assert "source-bound active-probe acquisition intervals" in result["tcp_start_bracket"]["unavailable_reason"]


@pytest.mark.parametrize("tool", ["sockperf", "iperf3"])
def test_f02r_persistent_listener_without_process_times_is_retained(repository_root, tool):
    value = make_input(repository_root)
    unit = next(item for item in value.measurement_units if item.phase == "multi_flow")
    action = f"f02r-vm_b-listener-{tool}"
    root = "/var/lib/cnb/work/server-logs"
    argv = ["sudo", "systemd-run", "--unit", f"cnb-{value.identity.deployment_id}-{tool}",
        "--property=Restart=no", "--property=UMask=0022",
        f"--property=StandardOutput=append:{root}/{tool}{'.log' if tool == 'sockperf' else '-server.jsonl'}",
        f"--property=StandardError=append:{root}/{tool}{'.err' if tool == 'sockperf' else '-server.err'}"]
    if tool == "iperf3":
        argv.append("--property=Environment=CNB_ACQUISITION_ID=f02r-vm_b-listener-iperf3")
    argv.extend([f"/var/lib/cnb/install/bin/{tool}"])
    argv.extend(["server", "-i", "10.60.1.11", "-p", "11111"] if tool == "sockperf" else
        ["-s", "-B", "10.60.1.11", "-p", "5201", "--json-stream", "--forceflush"])
    listener = CommandEvidence(schema_version=2, request={"action_id": action, "argv": argv},
        result={"outcome": "succeeded", "action_id": action}, producer="vm_b",
        deployment_id=value.identity.deployment_id, unit_id=unit.unit_id,
        binary_version={"value": "3.17.1"}, source_identity={"value": "fixture"},
        monotonic_started={"unavailable_reason": "persistent listener start is not measured"},
        monotonic_finished={"unavailable_reason": "persistent listener has no finite finish"})
    findings = _command_findings(value.model_copy(update={"commands": [listener]}))
    assert not [item for item in findings if item.code == "command.timing_unavailable"]


def test_receiver_session_settings_endpoint_and_window_are_corroborated(repository_root):
    value = make_input(repository_root)
    unit = value.measurement_units[1]
    command = CommandEvidence(schema_version=2,
        request={"argv":["iperf3","-c","10.0.0.2","-P","1","-t","60","-O","5","-l","131072"]},
        result={"outcome":"succeeded"}, producer="vm_a", deployment_id=value.identity.deployment_id,
        unit_id=unit.unit_id, binary_version={"value":"3.17.1"}, source_identity={"value":"fixture"},
        started_at={"value":NOW+timedelta(seconds=7)}, finished_at={"value":NOW+timedelta(seconds=70)},
        monotonic_started={"value":7}, monotonic_finished={"value":70})
    listener = CommandEvidence(schema_version=2,
        request={"argv":["iperf3","-s","-B","10.0.0.2","-p","5201","--json-stream","--forceflush"]},
        result={"outcome":"succeeded"}, producer="vm_b", deployment_id=value.identity.deployment_id,
        unit_id=value.measurement_units[0].unit_id, binary_version={"value":"3.17.1"}, source_identity={"value":"fixture"},
        started_at={"value":NOW}, finished_at={"value":NOW+timedelta(seconds=1)},
        monotonic_started={"value":0}, monotonic_finished={"value":1})
    timing = value.timings[1].model_copy(update={
        "tcp_started_at":Evidence[datetime](value=NOW+timedelta(seconds=7)),
        "measured_started_at":Evidence[datetime](value=NOW+timedelta(seconds=10)),
        "measured_finished_at":Evidence[datetime](value=NOW+timedelta(seconds=70))})
    value = value.model_copy(update={"commands":[command,listener],"timings":[value.timings[0],timing,*value.timings[2:]]})
    source = SourceRef("iperf3_server_log","receiver.jsonl","a"*64)
    details = ({"session_id":"1","start":{"test_start":{"protocol":"TCP","num_streams":1,
        "omit":5,"duration":60,"blksize":131072},"timestamp":{"timesecs":(NOW+timedelta(seconds=7)).timestamp()},
        "cookie":"captured-session", "connected":[{"socket":17,"local_host":"10.0.0.2","local_port":5201,
            "remote_host":"10.0.0.1","remote_port":50100}]}},)
    good = ReceiverInterval("1",0,60,60,1000,False,False,source,1)
    omitted_warmup = ReceiverInterval("1",0,4,4,250,True,False,source,2)
    outside = ReceiverInterval("1",61,64,3,100,False,False,source,3)
    parsed = NativeParse(source=source,sessions=("1",),session_details=details,
        receiver_intervals=(good,omitted_warmup,outside))
    selected = _receiver_session_for_unit(value,unit,parsed)
    assert selected.receiver_intervals == (good, omitted_warmup, outside)
    assert any(item.code == "native.iperf3.window_unavailable" for item in selected.findings)
    assert not any(item.code == "native.iperf3.interval_outside_window" for item in selected.findings)
    verified = {"captured-session": {"origin_utc": (NOW+timedelta(seconds=10)).timestamp(),
        "uncertainty_seconds": .000001, "crossing_reset": False, "stream_count": 1}}
    selected = _receiver_session_for_unit(value,unit,parsed,verified)
    assert selected.receiver_intervals == (good, omitted_warmup)
    assert any(item.code == "native.iperf3.interval_outside_window" for item in selected.findings)
    partial = ReceiverInterval("1",3,60,57,900,False,False,source,4)
    partial_result = _receiver_session_for_unit(value,unit,NativeParse(source=source,sessions=("1",),
        session_details=details,receiver_intervals=(partial,)),verified)
    assert partial_result.receiver_intervals == (partial,)
    assert not any(item.code == "native.iperf3.interval_outside_window" for item in partial_result.findings)
    shifted_timing = timing.model_copy(update={
        "measured_started_at":Evidence[datetime](value=NOW+timedelta(seconds=20)),
        "measured_finished_at":Evidence[datetime](value=NOW+timedelta(seconds=80))})
    shifted_value = value.model_copy(update={"timings":[value.timings[0],shifted_timing,*value.timings[2:]]})
    shifted_result = _receiver_session_for_unit(shifted_value,unit,NativeParse(source=source,sessions=("1",),
        session_details=details,receiver_intervals=(good,)),verified)
    assert any(item.code == "native.iperf3.interval_outside_window" for item in shifted_result.findings)
    wrong_start = {**details[0]["start"],"connected":[{**details[0]["start"]["connected"][0],
        "local_host":"10.0.0.9"}]}
    mismatch = _receiver_session_for_unit(value,unit,NativeParse(**{**parsed.__dict__,
        "session_details":({**details[0],"start":wrong_start},)}))
    assert any(item.code == "native.iperf3.endpoint_mismatch" for item in mismatch.findings)
    shifted = {**details[0],"start":{**details[0]["start"],"timestamp":{"timesecs":(NOW+timedelta(seconds=299)).timestamp()}}}
    ambiguous = _receiver_session_for_unit(value,unit,NativeParse(**{**parsed.__dict__,"session_details":(shifted,)}))
    assert any(item.code == "native.iperf3.session_ambiguous" for item in ambiguous.findings)


def test_schedule_snapshot_content_is_checked_after_self_consistent_hash_update(repository_root, tmp_path):
    from cloud_network_benchmark.validation import _validate_materialized_records

    value = make_input(repository_root)
    expected = value.schedule_entry.value
    path = tmp_path / "schedule.csv"
    valid = (expected.original_header + "\n" + expected.original_row + "\n").encode()
    path.write_bytes(valid)
    digest = hashlib.sha256(valid).hexdigest()
    record = ArtifactRecord(artifact_id="schedule_snapshot", path="schedule.csv", kind="schedule_row",
        producer="controller", unit_id=None, deployment_id=value.identity.deployment_id, closed=True,
        size_bytes={"value":len(valid)}, sha256={"value":digest}, retrieval_state="verified")
    materialized = value.model_copy(update={"child_result_root":str(tmp_path),
        "schedule_snapshot_path":Evidence[str](value="schedule.csv"),
        "schedule_sha256":Evidence[str](value=digest), "artifact_records":[record]})
    assert not _validate_materialized_records(materialized, {})

    contradictory = b"entry_id,experiment_id,priority\nother,EXP-001,P0\n"
    path.write_bytes(contradictory)
    changed_record = record.model_copy(update={"size_bytes":Evidence[int](value=len(contradictory)),
        "sha256":Evidence[str](value=hashlib.sha256(contradictory).hexdigest())})
    changed = materialized.model_copy(update={"schedule_sha256":Evidence[str](value=changed_record.sha256.value),
        "artifact_records":[changed_record]})
    findings = _validate_materialized_records(changed, {})
    assert any(item.code == "provenance.schedule_content" for item in findings)


def test_not_expected_artifact_and_execution_cleanup_failures_remain_distinct(repository_root):
    value = make_input(repository_root)
    declaration = value.artifacts["schedule_snapshot"]
    unexpected = ArtifactRecord(artifact_id="schedule_snapshot", path=declaration.path, kind=declaration.kind,
        producer=declaration.producer, unit_id=None, deployment_id=value.identity.deployment_id,
        closed=True, size_bytes={"value": 0}, sha256={"value":"a" * 64}, retrieval_state="verified")
    findings, _, _ = _validate_artifacts(value.model_copy(update={"artifact_records":[unexpected]}))
    assert any(item.code == "artifact.unexpected" for item in findings)
    failed = value.model_copy(update={"execution_state":"failed", "cleanup_state":"failed",
        "failure":FailureEvidence(category="measurement", message="offline fixture failure", occurred_at=NOW),
        "cleanup_failure":FailureEvidence(category="cleanup", message="offline cleanup failure", occurred_at=NOW)})
    result = validate_bundle(failed, evaluated_at=NOW)
    assert result.metrics.value["execution"]["execution_state"] == "failed"
    assert result.metrics.value["execution"]["cleanup_state"] == "failed"
    assert result.metrics.value["execution"]["primary_failure"]["message"] == "offline fixture failure"
    assert result.metrics.value["execution"]["cleanup_failure"]["message"] == "offline cleanup failure"
    assert result.metrics.value["execution"]["bundle_complete"] is False


def test_missing_generation_and_end_only_receiver_totals_remain_partial_not_unavailable(repository_root, monkeypatch):
    value = make_input(repository_root)
    missing = ProbeCounts(sent=Evidence[int](unavailable_reason="sent omitted"),
        received=Evidence[int](unavailable_reason="not reported"), dropped=Evidence[int](unavailable_reason="not reported"),
        duplicated=Evidence[int](unavailable_reason="not reported"), out_of_order=Evidence[int](unavailable_reason="not reported"),
        skipped=Evidence[int](unavailable_reason="not reported"), runtime_seconds=Evidence[float](unavailable_reason="runtime omitted"),
        reporting_scope="[Valid Duration]", source=SourceRef("sockperf-log", "sockperf.log", "d"*64))
    total = {"session_id":"1", "bytes":1000, "seconds":60, "source":SourceRef("iperf3", "receiver.json", "e"*64), "line":1}
    import cloud_network_benchmark.validation as module
    monkeypatch.setattr(module, "_parse_unit", lambda _value, unit, _records:
                        (NativeParse(probe_counts=(missing,)) if unit.phase == "idle_latency" else NativeParse(),
                         NativeParse(receiver_totals=(total,)) if unit.phase == "single_flow" else None))
    failed = value.model_copy(update={"retrieval_failure":FailureEvidence(category="retrieval", message="copy failed", occurred_at=NOW)})
    result = validate_bundle(failed, evaluated_at=NOW)
    assert result.validation_status.data_validity == "partial"
    assert any(item.code == "generation.unavailable" for item in result.findings)
    assert any(item.code == "retrieval.failed" for item in result.findings)
    assert result.metrics.value["units"][1]["tcp"]["throughput_gbps"]["value"] is not None
    assert result.metrics.value["execution"]["bundle_complete"] is False


def test_actual_command_options_accept_equivalent_spelling_and_reject_protocol_extras(repository_root):
    value = make_input(repository_root)
    unit = value.measurement_units[1]
    valid = CommandEvidence(schema_version=2,
        request={"argv":["iperf3", "-c", "10.0.0.2", "-p=5201", "-Z", "-C", "cubic", "-P", "1", "-t", "60", "-O", "5", "-l", "131072", "-i", "1", "--json", "--get-server-output"]},
        result={"outcome":"succeeded"}, producer="vm_a", deployment_id=value.identity.deployment_id,
        unit_id=unit.unit_id, binary_version={"value":"3.17.1"}, source_identity={"value":"fixture build"},
        monotonic_started={"value":1}, monotonic_finished={"value":61})
    findings = _command_findings(value.model_copy(update={"commands":[valid]}))
    assert not any(item.severity == "error" for item in findings)
    invalid = valid.model_copy(update={"request":{"argv":[*valid.request["argv"], "--reverse"]}})
    findings = _command_findings(value.model_copy(update={"commands":[invalid]}))
    assert any(item.code == "command.protocol_extra" for item in findings)
    udp = valid.model_copy(update={"request":{"argv":[*valid.request["argv"], "-u"]}})
    assert any(item.code == "command.protocol_extra" for item in
               _command_findings(value.model_copy(update={"commands":[udp]})))
    for override in ("--bitrate=100M", "--window=128K", "-b100M", "-w128K"):
        altered = valid.model_copy(update={"request":{"argv":[*valid.request["argv"], override]}})
        assert any(item.code == "command.protocol_extra" for item in
                   _command_findings(value.model_copy(update={"commands":[altered]})))
    long_options = ["iperf3", "--client=10.0.0.2", "--port=5201", "-Z", "-C", "cubic",
                    "--parallel=1", "--time=60", "--omit=5", "--length=131072", "--interval=1",
                    "--json", "--get-server-output"]
    equivalent = valid.model_copy(update={"request":{"argv":long_options}})
    assert not any(item.severity == "error" for item in
                   _command_findings(value.model_copy(update={"commands":[equivalent]})))
    duplicate = valid.model_copy(update={"request":{"argv":[*valid.request["argv"], "--time=60"]}})
    assert any(item.code == "command.duplicate_option" for item in
               _command_findings(value.model_copy(update={"commands":[duplicate]})))
    for extra in ("--fq-rate=100M",):
        altered = valid.model_copy(update={"request":{"argv":[*valid.request["argv"], extra]}})
        assert any(item.code == "command.protocol_extra" for item in
                   _command_findings(value.model_copy(update={"commands":[altered]})))
    wrong_argv = list(valid.request["argv"])
    wrong_argv[wrong_argv.index("-C") + 1] = "reno"
    wrong_congestion = valid.model_copy(update={"request":{"argv":wrong_argv}})
    assert any(item.code == "command.congestion" and item.severity == "error" for item in
               _command_findings(value.model_copy(update={"commands":[wrong_congestion]})))
    day_value = make_input(repository_root, config_name="exp-001-diagnostic-day.yaml")
    day = day_value.measurement_units[0]
    chunk = PhaseConfiguration(unit_id="probe-chunk-001", kind="probe_chunk", phase="multi_flow",
        measured_seconds=300, streams=4, parent_unit_id=day.unit_id)
    probe = CommandEvidence(schema_version=2,
        request={"argv":["sockperf","under-load","-i","10.0.0.2","--full-rtt","--no-rdtsc","-m","64","--mps","1000","--reply-every","1","-p","11111","-t","900","--full-log"]},
        result={"outcome":"succeeded"}, producer="vm_a", deployment_id=day_value.identity.deployment_id,
        unit_id=chunk.unit_id, binary_version={"value":"3.10"}, source_identity={"value":"fixture"},
        monotonic_started={"value":1}, monotonic_finished={"value":901})
    chunk_findings = _command_findings(day_value.model_copy(update={"measurement_units":[day,chunk], "commands":[probe]}))
    assert any(item.code == "command.duration" and item.unit_id == chunk.unit_id for item in chunk_findings)


def test_commands_require_private_targets_and_persistent_private_bound_listeners(repository_root):
    value = make_input(repository_root)
    unit = value.measurement_units[1]
    base = dict(schema_version=2, result={"outcome":"succeeded"}, deployment_id=value.identity.deployment_id,
        unit_id=unit.unit_id, binary_version={"value":"3.17.1"}, source_identity={"value":"fixture"},
        monotonic_started={"value":1}, monotonic_finished={"value":61})
    commands = [
        CommandEvidence(request={"argv":["sockperf", "server", "-i", "10.0.0.2", "-p", "11111"]},
            producer="vm_b", **{**base,"unit_id":value.measurement_units[0].unit_id}),
        CommandEvidence(request={"argv":["iperf3", "-s", "-B", "10.0.0.2", "-p", "5201", "--json-stream", "--forceflush"]},
            producer="vm_b", **{**base,"unit_id":value.measurement_units[0].unit_id}),
        CommandEvidence(request={"argv":["iperf3", "-c", "10.0.0.2", "-p", "5201", "-Z", "-C", "cubic", "-P", "1", "-t", "60", "-O", "5", "-l", "131072", "-i", "1", "--json", "--get-server-output"]},
            producer="vm_a", **base),
    ]
    findings = _command_findings(value.model_copy(update={"commands":commands}))
    assert not any(item.severity == "error" for item in findings)
    action="f02r-vm_b-listener-iperf3"
    launcher=["systemd-run",f"--property=Environment=CNB_ACQUISITION_ID={action}","/usr/local/bin/iperf3",
        "-s","-B","10.0.0.9","-p","5201","--json-stream","--forceflush"]
    strict_listener=commands[1].model_copy(update={
        "request":{"argv":launcher,"launcher_argv":launcher,"action_id":action,
            "acquisition_invocation_id":action},
        "result":{"outcome":"succeeded","action_id":action}})
    findings = _command_findings(value.model_copy(update={"commands":[commands[0],strict_listener,commands[2]]}))
    assert "command.target_bind_mismatch" in {item.code for item in findings}
    bad = commands[1].model_copy(update={"request":{"argv":["iperf3", "-s", "-B", "8.8.8.8", "-p", "5201"]}})
    findings = _command_findings(value.model_copy(update={"commands":[commands[0], bad]}))
    assert {item.code for item in findings} >= {"command.listener_bind", "command.streaming"}



def test_f02r_iperf_listener_accepts_exact_sudo_systemd_run_and_rejects_mutations(repository_root):
    from cloud_network_benchmark.validation import _f02r_iperf_listener

    value = make_input(repository_root)
    action = "f02r-vm_b-listener-iperf3"
    argv = ["sudo", "systemd-run", "--unit=cnb-listener-iperf3",
        "--property=Restart=no", "--property=Environment=CNB_ACQUISITION_ID=" + action,
        "/var/lib/cnb/install/bin/iperf3", "-s", "-B", "10.0.0.2", "-p", "5201",
        "--json-stream", "--forceflush"]
    command = CommandEvidence(schema_version=2,
        request={"argv": argv, "action_id": action, "acquisition_invocation_id": action},
        result={"outcome": "succeeded", "action_id": action}, producer="vm_b",
        deployment_id=value.identity.deployment_id, unit_id=value.measurement_units[0].unit_id,
        binary_version={"value": "3.17.1"}, source_identity={"value": "fixture"},
        monotonic_started={"value": 0}, monotonic_finished={"value": 1})
    assert _f02r_iperf_listener(command, argv)
    wrong_env = list(argv)
    wrong_env[4] = "--property=Environment=CNB_ACQUISITION_ID=wrong"
    wrong_bind = list(argv)
    wrong_bind[8] = "8.8.8.8"
    wrong_action = command.model_copy(deep=True)
    wrong_action.request["action_id"] = "other-action"
    assert not _f02r_iperf_listener(command, wrong_env)
    assert not _f02r_iperf_listener(wrong_action, argv)
    assert not _f02r_iperf_listener(command, wrong_bind)


def test_sequence_requires_actual_sockperf_runtime_and_ten_second_pause(repository_root):
    value = make_input(repository_root)
    first, second = value.measurement_units[:2]
    counts = ProbeCounts(sent=Evidence[int](value=65000), received=Evidence[int](value=65000),
        dropped=Evidence[int](value=0), duplicated=Evidence[int](value=0), out_of_order=Evidence[int](value=0),
        skipped=Evidence[int](value=0), runtime_seconds=Evidence[float](value=65),
        reporting_scope="one Sockperf invocation", source=SourceRef("sockperf-log", "sockperf.log", "d" * 64))
    too_early = value.timings[1].model_copy(update={"probe_test_started_at":Evidence[datetime](
        value=value.timings[0].probe_test_started_at.value + timedelta(seconds=74))})
    value = value.model_copy(update={"timings":[value.timings[0], too_early, *value.timings[2:]]})
    findings = _sequence_timing_findings(value, {first.unit_id:NativeParse(probe_counts=(counts,))})
    assert any(item.code == "timing.phase_pause" and item.unit_id == second.unit_id for item in findings)
