from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from types import GeneratorType
from datetime import datetime, timedelta
import yaml

from cloud_network_benchmark.native import SourceRef, parse_iperf3, parse_sockperf, receiver_statistics, rtt_statistics
from cloud_network_benchmark.validation import _json_records, _receiver_session_for_unit
from cloud_network_benchmark.manifests import (StaticGitProvenance, initialize_campaign,
    update_child_cleanup, update_child_execution)
from cloud_network_benchmark.contracts import (
    ArtifactLayout, ArtifactRecord, ClockMapping, CommandEvidence, DiagnosticSample,
    Evidence, GitSnapshot, IdentityContext, MeasurementTiming, StructuredEvidence, StringEvidence,
    VmMetadata, artifact_inputs,
)
from cloud_network_benchmark.contracts.admission import ScheduleEntry
from cloud_network_benchmark.contracts.common import CleanupState, ExecutionState
from cloud_network_benchmark.validation import evaluate_comparison, validate_bundle
from cloud_network_benchmark.summaries import summarize_validation


def test_native_fixture_replay_is_source_linked_and_does_not_mutate_inputs():
    root=Path(__file__).parents[1]/"fixtures"/"f08"/"native"
    files=[root/"sockperf.csv",root/"sockperf.log",root/"iperf3.json",root/"iperf3.jsonl"]
    before={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    def ref(path, artifact):
        return SourceRef(artifact,path.name,before[path.name],"unit-1","deployment-1","vm_a","3.10")
    sock=parse_sockperf(files[0],files[1],source=ref(files[0],"sockperf"),log_source=ref(files[1],"sockperf-log"))
    receiver=parse_iperf3(files[2],source=ref(files[2],"iperf3"),format="json")
    stream=parse_iperf3(files[3],source=ref(files[3],"iperf3-stream"),format="jsonl")
    output={"rtt":rtt_statistics([s.rtt_us for s in sock.samples]),
            "tcp":receiver_statistics(receiver.receiver_intervals),"stream":receiver_statistics(stream.receiver_intervals)}
    replay=json.dumps(output,sort_keys=True,default=lambda x:x.model_dump(mode="json"))
    sock_again=parse_sockperf(files[0],files[1],source=ref(files[0],"sockperf"),log_source=ref(files[1],"sockperf-log"))
    receiver_again=parse_iperf3(files[2],source=ref(files[2],"iperf3"),format="json")
    stream_again=parse_iperf3(files[3],source=ref(files[3],"iperf3-stream"),format="jsonl")
    replay_again=json.dumps({"rtt":rtt_statistics([s.rtt_us for s in sock_again.samples]),
        "tcp":receiver_statistics(receiver_again.receiver_intervals),
        "stream":receiver_statistics(stream_again.receiver_intervals)},sort_keys=True,
        default=lambda x:x.model_dump(mode="json"))
    assert replay==replay_again
    assert before=={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    assert all(s.source.sha256 for s in sock.samples)


def test_jsonl_malformed_tail_keeps_prior_receiver_intervals(tmp_path):
    fixture=Path(__file__).parents[1]/"fixtures"/"f08"/"native"/"iperf3.jsonl"
    content=fixture.read_text()+"{truncated event\n"
    path=tmp_path/"partial.jsonl"
    path.write_text(content)
    parsed=parse_iperf3(path,source=SourceRef("iperf3","partial.jsonl","0"*64),format="jsonl")
    assert len(parsed.receiver_intervals)==1
    assert parsed.receiver_intervals[0].bytes==125000000
    assert any(item.code=="native.iperf3.jsonl" for item in parsed.findings)


def test_materialized_jsonl_records_are_consumed_incrementally(tmp_path):
    path = tmp_path / "evidence.jsonl"
    path.write_text('{"event":1}\nnot-json\n', encoding="utf-8")
    records = _json_records(path)
    assert isinstance(records, GeneratorType)
    assert next(records) == {"event": 1}
    try:
        next(records)
    except ValueError as exc:
        assert "line 2" in str(exc)
    else:
        raise AssertionError("malformed second record must be reported when consumed")


def _materialize_complete_bundle(repository_root, tmp_path, frozen_clock, deterministic_token,
                                fake_git_commits, request, *, config_name="exp-001-multi-provider.yaml",
                                mode_override=None, provider="aws", frozen_n=False, shared_slot=False):
    from tests.unit.test_validation import NOW, make_input

    config_path=repository_root/"configs/experiments"/config_name
    config_data=yaml.safe_load(config_path.read_text())
    if mode_override == "acceptance":
        config_data["mode"]="acceptance"
        config_data["benchmark"]["measured_seconds"]=10
        config_data["admission"]["requires_common_n"]=False
        config_data["admission"]["requires_schedule"]=False
    if frozen_n:
        config_data["benchmark"]["common_n"]={"state":"frozen","common_n":4,
            "source_campaign_id":"prior-calibration-campaign","source_sha256":"d"*64,"plateau_found":True}
    config_data["scheduled_start"]="2026-10-21T00:00:00Z"
    config_path=repository_root/"configs/tests"/f"f08-{tmp_path.name}-{provider}.yaml"
    request.addfinalizer(lambda: config_path.unlink(missing_ok=True))
    config_path.write_text(yaml.safe_dump(config_data,sort_keys=False))
    entry_id="f08-shared-short-study" if shared_slot else f"f08-{tmp_path.name}-{provider}"
    row=f"{entry_id},{config_data['experiment_id']},{config_data['config_id']}"
    schedule=ScheduleEntry(schema_version=2,entry_id=entry_id,config_id=config_data["config_id"],
        experiment_id=config_data["experiment_id"],scheduled_start=datetime(2026,10,21,tzinfo=NOW.tzinfo),
        window_label="morning",source_path="tests/fixtures/f08/schedule.csv",
        source_sha256=hashlib.sha256(b"synthetic F08 schedule fixture").hexdigest(),
        row_sha256=hashlib.sha256(row.encode()).hexdigest(),original_header="entry_id,experiment_id,config_id",
        original_row=row,columns=["entry_id","experiment_id","config_id"],priority="P0")
    initialized = initialize_campaign(config_path,
        repository_root, tmp_path / "results", frozen_clock, deterministic_token,
        StaticGitProvenance(fake_git_commits["implementation"]), StaticGitProvenance(fake_git_commits["design"]),
        schedule_entry=schedule)
    child_ref = next(item for item in initialized.children if item.provider == provider)
    child_path, child_root = Path(child_ref.manifest_path), Path(child_ref.result_path)
    child = json.loads(child_path.read_text())
    run_id, campaign_id, deployment_id = child["run_id"], child["campaign_id"], child["deployment_id"]
    value = make_input(repository_root, provider, campaign_id, config_name=config_name, mode_override=mode_override, config_path=config_path)
    identity = IdentityContext(experiment_id=child["experiment_id"], config_id=child["config_id"],
        campaign_id=campaign_id, observation_id=child["observation_id"], run_id=run_id,
        deployment_id=deployment_id, provider=provider)
    from cloud_network_benchmark.contracts.campaign import PhaseConfiguration
    units = [PhaseConfiguration.model_validate(item) for item in child["measurement_units"]]
    from tests.integration.test_acquisition_producer import _build
    from cloud_network_benchmark.bootstrap import materialize_build_identity_artifacts
    from cloud_network_benchmark.contracts import VmRole
    builds = [_build("sockperf", VmRole.VM_A, "f08-vma-sockperf-build"),
              _build("iperf3", VmRole.VM_A, "f08-vma-iperf3-build"),
              _build("sockperf", VmRole.VM_B, "f08-vmb-sockperf-build"),
              _build("iperf3", VmRole.VM_B, "f08-vmb-iperf3-build")]
    if value.mode=="diagnostic_day":
        day_id=units[0].unit_id
        units.extend(PhaseConfiguration(unit_id=f"probe-chunk-{index:06d}",kind="probe_chunk",
            phase="multi_flow",measured_seconds=300,parent_unit_id=day_id)
            for index in range(1,289))
    declarations = artifact_inputs(ArtifactLayout.for_mode(value.mode, units), mode=value.mode,
        measurement_units=units,schedule_supplied=True)
    records, commands, timings, samples = [], [], [], []
    receiver_source_rows=[]

    def materialize(artifact_id, unit_id, content, *, concrete_path=None, record_unit_id=None):
        declaration = declarations[artifact_id]
        relpath = concrete_path or declaration.path or declaration.path_template.replace("{chunk_id}",unit_id)
        path = child_root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = content if isinstance(content, bytes) else content.encode()
        path.write_bytes(payload)
        records.append(ArtifactRecord(artifact_id=artifact_id, path=relpath, kind=declaration.kind,
            producer=declaration.producer, unit_id=unit_id if record_unit_id is None else record_unit_id,
            deployment_id=deployment_id, closed=True,
            size_bytes={"value":len(payload)}, sha256={"value":hashlib.sha256(payload).hexdigest()},
            retrieval_state="verified"))
        return payload

    def materialize_extra(artifact_id, unit_id, relpath, kind, producer, content):
        path = child_root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = content if isinstance(content, bytes) else content.encode()
        path.write_bytes(payload)
        records.append(ArtifactRecord(artifact_id=artifact_id, path=relpath, kind=kind,
            producer=producer, unit_id=unit_id, deployment_id=deployment_id, closed=True,
            size_bytes={"value":len(payload)}, sha256={"value":hashlib.sha256(payload).hexdigest()},
            retrieval_state="verified"))

    config_bytes = (child_root / "config.yaml").read_bytes()
    resolved_bytes = (child_root / "config.resolved.json").read_bytes()
    materialize("config_snapshot", None, config_bytes)
    materialize("resolved_snapshot", None, resolved_bytes)
    schedule_bytes=(schedule.original_header+"\n"+schedule.original_row+"\n").encode()
    materialize("schedule_snapshot",None,schedule_bytes)
    metadata = []
    diagnostics = []
    for role in ("vm_a", "vm_b"):
        item = VmMetadata(schema_version=2, deployment_id=deployment_id, vm_role=role, provider=provider,
            cpu_model={"value":"synthetic CPU"}, nic_driver={"value":"synthetic NIC"},
            acceleration={"value":"synthetic"}, image={"value":"ubuntu"}, kernel={"value":"6.8"},
            congestion_control={"value":"cubic"}, tool_versions={"value":{tool:next((b.binary_version for b in builds if b.tool==tool and b.role.value==role),"3.10" if tool=="sockperf" else "3.17.1") for tool in ("sockperf","iperf3")}},
            source_builds={"value":{tool:next((b.source_reference for b in builds if b.tool==tool and b.role.value==role),"synthetic fixture build") for tool in ("sockperf","iperf3")}},
            documented_network_limit={"unavailable_reason":"not specified in synthetic fixture"}, mtu={"value":1500},
            observed_region={"value":"us-east-1"}, observed_zone={"value":"us-east-1a"},
            physical_zone_id={"unavailable_reason":"not observed"}, actual_shape={"value":"synthetic"})
        metadata.append(item)
        materialize(f"{role}_metadata", None, item.model_dump_json().encode())
        diagnostic = DiagnosticSample(schema_version=2, deployment_id=deployment_id, vm_role=role,
            sampled_at=NOW, monotonic_seconds=0, interval_seconds=1,
            cpu_total_percent={"value":5}, cpu_max_core_percent={"value":10}, cpu_steal_percent={"value":0},
            tcp_retransmissions={"value":0}, interface_rx_drops={"value":0}, interface_tx_drops={"value":0},
            interface_rx_errors={"value":0}, interface_tx_errors={"value":0}, rx_bytes={"value":1000}, tx_bytes={"value":1000})
        diagnostics.append(diagnostic)
        materialize(f"{role}_diagnostics", None, (diagnostic.model_dump_json()+"\n").encode())
    materialize("sockperf_server_log", None, "")
    if not builds:
        materialize("iperf3_server_error", None, "")

    def command(unit_id, producer, argv, stamp):
        runtime=1
        if "-t" in argv:
            runtime=int(argv[argv.index("-t")+1])
            if argv and "iperf3" in argv[0] and "-c" in argv and "-O" in argv:
                runtime+=int(argv[argv.index("-O")+1])
        elif "-s" in argv or "server" in argv:
            runtime=86400
        return CommandEvidence(schema_version=2, request={"argv":argv}, result={"outcome":"succeeded"},
            producer=producer, deployment_id=deployment_id, unit_id=unit_id,
            binary_version={"value":"3.10" if "sockperf" in argv[0] else "3.17.1"},
            source_identity={"value":"synthetic fixture build"}, monotonic_started={"value":stamp},
            monotonic_finished={"value":stamp+runtime}, started_at={"value":NOW+timedelta(seconds=stamp)},
            finished_at={"value":NOW+timedelta(seconds=stamp+runtime)})

    for index, unit in enumerate(units):
        if value.mode=="calibration":
            start_offset=index*400
        elif value.mode=="diagnostic_day":
            start_offset=0 if unit.kind=="continuous_day" else (index-1)*300
        else:
            start_offset=(0, 100, 500)[index]
        probe_start = NOW + timedelta(seconds=start_offset)
        idle_phase=unit.phase=="idle_latency"
        day_unit=unit.kind=="continuous_day"
        chunk_unit=unit.kind=="probe_chunk"
        measured_offset = 5 if idle_phase else (7 if day_unit else 0 if chunk_unit else 10)
        measured_start = probe_start + timedelta(seconds=measured_offset)
        duration=unit.measured_seconds
        measured_finish = measured_start + timedelta(seconds=duration)
        tcp_start = Evidence[datetime](unavailable_reason="idle phase has no TCP client") if idle_phase or chunk_unit else Evidence[datetime](value=probe_start+timedelta(seconds=2 if day_unit else 7))
        mapping_a = ClockMapping(vm_role="vm_a", monotonic_seconds=0, utc_timestamp=NOW,
            estimated_error_ms={"value":0}, synchronization_source={"value":"synthetic clock fixture"})
        mapping_b = ClockMapping(vm_role="vm_b", monotonic_seconds=0, utc_timestamp=NOW,
            estimated_error_ms={"value":0}, synchronization_source={"value":"synthetic clock fixture"})
        timing = MeasurementTiming(unit_id=unit.unit_id, deployment_id=deployment_id,
            probe_test_started_at={"value":probe_start}, tcp_started_at=tcp_start,
            measured_started_at={"value":measured_start}, measured_finished_at={"value":measured_finish},
            monotonic_started={"value":float(start_offset+1)}, monotonic_finished={"value":float(start_offset+measured_offset+duration+1)},
            omitted_seconds=float(measured_offset), overlap_seconds={"value":duration},
            clock_mappings=[mapping_a, mapping_b] if not idle_phase else [mapping_a])
        timings.append(timing)
        if not day_unit and not chunk_unit:
            sockperf_argv=["sockperf","under-load","-i","10.0.0.2","--full-rtt","--no-rdtsc","-m","64","--mps","1000","--reply-every","1","-p","11111","-t",str(duration+5 if idle_phase else duration+10 if not chunk_unit else duration),"--full-log"]
            commands.append(command(unit.unit_id,"vm_a",sockperf_argv,start_offset))
            if builds:
                sock_build=next(b for b in builds if b.tool=="sockperf")
                sock_action=f"sockperf-{unit.unit_id}"
                commands[-1]=commands[-1].model_copy(update={"request":{**commands[-1].request,
                    "action_id":sock_action,"acquisition_invocation_id":sock_action,
                    "argv":[sock_build.binary_path,*sockperf_argv[1:]]},
                    "result":{**commands[-1].result,"action_id":sock_action},
                    "binary_version":Evidence[str](value=sock_build.binary_version),
                    "source_identity":Evidence[str](value=sock_build.source_reference)})
        if day_unit:
            day_argv=["iperf3","-c","10.0.0.2","-z","-P","1","-t","86400","-O","5","-l","131072","-i","1","-p","5201","-C","cubic","--json-stream","--forceflush"]
            day_build=next(b for b in builds if b.tool=="iperf3" and b.role==VmRole.VM_A)
            day_action=f"iperf3-{unit.unit_id}"
            commands.append(command(unit.unit_id,"vm_a",[day_build.binary_path,*day_argv[1:]],start_offset+2))
            commands[-1]=commands[-1].model_copy(update={"request":{**commands[-1].request,
                "action_id":day_action,"acquisition_invocation_id":day_action},
                "result":{**commands[-1].result,"action_id":day_action},
                "binary_version":Evidence[str](value=day_build.binary_version),
                "source_identity":Evidence[str](value=day_build.source_reference)})
        if chunk_unit:
            chunk_index=index-1
            start_offset=chunk_index*300
            measured_offset=0
            duration=300
            measured_start=NOW+timedelta(seconds=7+start_offset)
            timing=timing.model_copy(update={"probe_test_started_at":Evidence[datetime](value=NOW+timedelta(seconds=start_offset+7)),
                "tcp_started_at":Evidence[datetime](unavailable_reason="persistent TCP session is not restarted for probe rotation"),
                "measured_started_at":Evidence[datetime](value=measured_start),
                "measured_finished_at":Evidence[datetime](value=measured_start+timedelta(seconds=300)),
                "monotonic_started":Evidence[float](value=float(start_offset+7)),"monotonic_finished":Evidence[float](value=float(start_offset+307)),
                "omitted_seconds":0.0,"clock_mappings":[mapping_a]})
            timings[-1]=timing
            sockperf_argv=["sockperf","under-load","-i","10.0.0.2","--full-rtt","--no-rdtsc","-m","64","--mps","1000","--reply-every","1","-p","11111","-t","300","--full-log"]
            sock_build=next(b for b in builds if b.tool=="sockperf" and b.role==VmRole.VM_A)
            action=f"sockperf-{unit.unit_id}"
            commands.append(command(unit.unit_id,"vm_a",[sock_build.binary_path,*sockperf_argv[1:]],start_offset+7))
            commands[-1]=commands[-1].model_copy(update={"request":{**commands[-1].request,
                "action_id":action,"acquisition_invocation_id":action},
                "result":{**commands[-1].result,"action_id":action},
                "binary_version":Evidence[str](value=sock_build.binary_version),
                "source_identity":Evidence[str](value=sock_build.source_reference)})
        materialize(f"{unit.unit_id}-timing", unit.unit_id, timing.model_dump_json().encode())
        sample_at=float(start_offset+measured_offset+.001)
        sample_end=float(start_offset+measured_offset+duration-.001)
        csv=("sockperf: test output\n------------------------------\npacket, txTime(sec), rxTime(sec), rtt(usec)\n"
             f"0, {sample_at:.6f}, {sample_at+.000001:.6f}, 1.000\n"
             f"1, {sample_end:.6f}, {sample_end+.000001:.6f}, 1.000\n")
        valid_runtime=duration if idle_phase or chunk_unit or day_unit else duration+5
        valid_sent=valid_runtime*1000
        total_runtime=valid_runtime+(5 if index==0 else 5)
        total_sent=total_runtime*1000
        log=(f"[Total Run] RunTime={total_runtime:.3f} sec; Warm up time=5000 msec; SentMessages={total_sent}; ReceivedMessages=2\n"
             f"[Valid Duration] RunTime={valid_runtime:.3f} sec; SentMessages={valid_sent}; ReceivedMessages=2\n")
        if chunk_unit:
            chunk_index=index-1
            materialize_extra(f"day-probe-csv-{unit.unit_id}",unit.unit_id,
                f"measurements/day/probes/{unit.unit_id}.csv","sockperf-csv","vm_a",csv)
            materialize_extra(f"day-probe-log-{unit.unit_id}",unit.unit_id,
                f"measurements/day/probes/{unit.unit_id}.log","sockperf-log","vm_a",log)
            action=f"sockperf-{unit.unit_id}"
            boot="12345678-1234-1234-1234-123456789abc"
            left=int((7+chunk_index*300)*1_000_000_000)
            ns=int(NOW.timestamp()*1_000_000_000)
            source_rows=[
                {"format":1,"tool":"sockperf","semantics_id":"sockperf-3.10-enabled-v1","invocation_id":action,"pid":42,"boot_id":boot,"clock":"CLOCK_MONOTONIC","record_kind":"clock_anchor","mono_before_ns":left,"realtime_ns":ns+left,"mono_after_ns":left+1000},
                {"format":1,"tool":"sockperf","semantics_id":"sockperf-3.10-enabled-v1","invocation_id":action,"pid":42,"boot_id":boot,"clock":"CLOCK_MONOTONIC","record_kind":"probe_window","sender_enabled_ns":left,"receiver_enabled_ns":left,"termination_ns":left+300_000_000_000,"native_filter_start_ns":left,"native_filter_end_ns":left+300_000_000_000,"termination_reason":"timer"}]
            materialize_extra(f"day-probe-observation-{unit.unit_id}",unit.unit_id,
                f"measurements/day/probes/{unit.unit_id}.acquisition.stderr","sockperf-observation-stderr","vm_a",
                b"".join(b"CNB_ACQ_V1 "+json.dumps(row,separators=(",",":" )).encode()+b"\n" for row in source_rows))
        elif not day_unit:
            materialize(f"{unit.unit_id}-sockperf",unit.unit_id,csv)
            materialize_extra("sockperf-log",unit.unit_id,f"measurements/{unit.unit_id}/sockperf.log",
                "sockperf-log","vm_a",log)
            if builds:
                action=f"sockperf-{unit.unit_id}"
                boot="12345678-1234-1234-1234-123456789abc"
                ns=int(NOW.timestamp()*1_000_000_000)
                left=int((start_offset+measured_offset)*1_000_000_000)
                right=left+duration*1_000_000_000
                source_rows=[
                    {"format":1,"tool":"sockperf","semantics_id":"sockperf-3.10-enabled-v1","invocation_id":action,"pid":42,"boot_id":boot,"clock":"CLOCK_MONOTONIC","record_kind":"clock_anchor","mono_before_ns":left,"realtime_ns":ns+left,"mono_after_ns":left+1000},
                    {"format":1,"tool":"sockperf","semantics_id":"sockperf-3.10-enabled-v1","invocation_id":action,"pid":42,"boot_id":boot,"clock":"CLOCK_MONOTONIC","record_kind":"probe_window","sender_enabled_ns":left,"receiver_enabled_ns":left,"termination_ns":right,"native_filter_start_ns":left,"native_filter_end_ns":right,"termination_reason":"timer"}]
                materialize(f"{unit.unit_id}-sockperf-observation",unit.unit_id,
                    b"".join(b"CNB_ACQ_V1 "+json.dumps(row,separators=(",",":")).encode()+b"\n" for row in source_rows))
            materialize(f"{unit.unit_id}-stdout",unit.unit_id,"")
            materialize(f"{unit.unit_id}-stderr",unit.unit_id,"")
        samples.append({"unit_id":unit.unit_id,"sample":sample_at})
        if not idle_phase and not day_unit and not chunk_unit:
            n=unit.streams or 1
            client_argv=["iperf3","-c","10.0.0.2","-z","-P",str(n),"-t",str(duration),"-O","5","-l","131072","-i","1","-p","5201","-C","cubic","-J","--get-server-output"]
            iperf_build=next((b for b in builds if b.tool=="iperf3"),None)
            cookie=f"f08-{unit.unit_id}-session"
            action=f"iperf3-{unit.unit_id}"
            commands.append(command(unit.unit_id,"vm_a",client_argv,start_offset+7))
            if iperf_build:
                commands[-1]=commands[-1].model_copy(update={"request":{**commands[-1].request,
                    "action_id":action,"argv":[iperf_build.binary_path,*client_argv[1:]]},
                    "result":{**commands[-1].result,"action_id":action},
                    "binary_version":Evidence[str](value=iperf_build.binary_version),
                    "source_identity":Evidence[str](value=iperf_build.source_reference)})
            streams=[{"socket":17+i,"start":0,"end":duration,"bytes":125000000//n,
                "omitted":False,"sender":False} for i in range(n)]
            connections=[{"socket":17+i,"local_host":"10.0.0.2","local_port":5201+i,
                "remote_host":"10.0.0.1","remote_port":50100+i} for i in range(n)]
            settings={"protocol":"TCP","num_streams":n,"omit":5,"duration":duration,
                "interval":1,"blksize":131072,"reverse":False}
            receiver={"start":{"cookie":cookie,"test_start":settings,"connected":connections},
                "intervals":[{"streams":streams}]}
            client={"start":{"cookie":cookie,"test_start":settings},"server_output_json":receiver,
                "intervals":[{"streams":streams}],"end":{"sum_received":{"bytes":125000000,"seconds":duration}}}
            materialize(f"{unit.unit_id}-iperf3",unit.unit_id,json.dumps(client))
            materialize(f"{unit.unit_id}-iperf3-observation",unit.unit_id,
                "client acquisition stderr captured separately from receiver observations\n")
            if iperf_build:
                boot="12345678-1234-1234-1234-123456789def"
                left=int((start_offset+10)*1_000_000_000)
                right=left+duration*1_000_000_000
                rows=[{"format":1,"tool":"iperf3","semantics_id":"iperf-3.17.1-stats-v1","invocation_id":"f02r-vm_b-listener-iperf3","pid":51,"boot_id":boot,"clock":"CLOCK_MONOTONIC","record_kind":"clock_anchor","cookie":cookie,"mono_before_ns":left,"realtime_ns":int(NOW.timestamp()*1_000_000_000)+left,"mono_after_ns":left+1000},
                    {"format":1,"tool":"iperf3","semantics_id":"iperf-3.17.1-stats-v1","invocation_id":"f02r-vm_b-listener-iperf3","pid":51,"boot_id":boot,"clock":"CLOCK_MONOTONIC","record_kind":"session_settings","cookie":cookie,"protocol":"TCP","num_streams":n,"omit":5,"duration":duration,"interval_us":1000000,"block_size":131072,"reverse":False}]
                for i in range(n):
                    stream=str(17+i)
                    rows.extend([
                        {"format":1,"tool":"iperf3","semantics_id":"iperf-3.17.1-stats-v1","invocation_id":"f02r-vm_b-listener-iperf3","pid":51,"boot_id":boot,"clock":"CLOCK_MONOTONIC","record_kind":"stats_origin","cookie":cookie,"stream_id":stream,"phase":"initial","origin_us":left//1000,"start_time_fixed_us":left//1000},
                        {"format":1,"tool":"iperf3","semantics_id":"iperf-3.17.1-stats-v1","invocation_id":"f02r-vm_b-listener-iperf3","pid":51,"boot_id":boot,"clock":"CLOCK_MONOTONIC","record_kind":"stats_origin","cookie":cookie,"stream_id":stream,"phase":"post_omit","origin_us":left//1000,"start_time_fixed_us":left//1000},
                        {"format":1,"tool":"iperf3","semantics_id":"iperf-3.17.1-stats-v1","invocation_id":"f02r-vm_b-listener-iperf3","pid":51,"boot_id":boot,"clock":"CLOCK_MONOTONIC","record_kind":"receiver_interval","cookie":cookie,"stream_id":stream,"role":"receiver","protocol":"TCP","local_host":"10.0.0.2","local_port":str(5201+i),"remote_host":"10.0.0.1","remote_port":str(50100+i),"interval_start_us":left//1000,"interval_end_us":right//1000,"bytes":125000000//n,"omitted":False}])
                receiver_source_rows.extend(rows)
        if day_unit:
            cookie=f"f08-{unit.unit_id}-session"
            stamp=(NOW+timedelta(seconds=2)).timestamp()
            settings={"protocol":"TCP","num_streams":1,"omit":5,"duration":86400,
                "interval":1,"blksize":131072,"reverse":False}
            connected=[{"socket":17,"local_host":"10.0.0.2","local_port":5201,
                "remote_host":"10.0.0.1","remote_port":50100}]
            start_data={"cookie":cookie,"test_start":settings,"connected":connected,
                "timestamp":{"timesecs":stamp}}
            start_event={"event":"start","data":start_data}
            interval_events=({"event":"interval","data":{"streams":[{"socket":17,
                "start":second,"end":second+1,"seconds":1,"bytes":125000000,"omitted":False,"sender":False}]}}
                for second in range(86400))
            end_event={"event":"end","data":{"sum_received":{"bytes":10800000000000,"seconds":86400}}}
            client_payload="\n".join(json.dumps(item,separators=(",",":"))
                for item in (start_event,*interval_events,end_event))+"\n"
            materialize("day-iperf3-client",unit.unit_id,client_payload)
            materialize("day-iperf3-observation",unit.unit_id,
                "day client process stdout/stderr closure evidence is retained separately\n")
            boot="12345678-1234-1234-1234-123456789def"
            mono_initial=2_000_000_000
            mono_post=7_000_000_000
            listener="f02r-vm_b-listener-iperf3"
            source_rows=[
                {"format":1,"tool":"iperf3","semantics_id":"iperf-3.17.1-stats-v1","invocation_id":listener,"pid":51,"boot_id":boot,"clock":"CLOCK_MONOTONIC","record_kind":"clock_anchor","cookie":cookie,"mono_before_ns":mono_initial,"realtime_ns":int(NOW.timestamp()*1_000_000_000)+mono_initial,"mono_after_ns":mono_initial+1000},
                {"format":1,"tool":"iperf3","semantics_id":"iperf-3.17.1-stats-v1","invocation_id":listener,"pid":51,"boot_id":boot,"clock":"CLOCK_MONOTONIC","record_kind":"session_settings","cookie":cookie,"protocol":"TCP","num_streams":1,"omit":5,"duration":86400,"interval_us":1000000,"block_size":131072,"reverse":False},
                {"format":1,"tool":"iperf3","semantics_id":"iperf-3.17.1-stats-v1","invocation_id":listener,"pid":51,"boot_id":boot,"clock":"CLOCK_MONOTONIC","record_kind":"stats_origin","cookie":cookie,"stream_id":"17","phase":"initial","origin_us":mono_initial//1000,"start_time_fixed_us":mono_initial//1000},
                {"format":1,"tool":"iperf3","semantics_id":"iperf-3.17.1-stats-v1","invocation_id":listener,"pid":51,"boot_id":boot,"clock":"CLOCK_MONOTONIC","record_kind":"stats_origin","cookie":cookie,"stream_id":"17","phase":"post_omit","origin_us":mono_post//1000,"start_time_fixed_us":mono_initial//1000}]
            for second in range(86400):
                source_rows.append({"format":1,"tool":"iperf3","semantics_id":"iperf-3.17.1-stats-v1","invocation_id":listener,"pid":51,"boot_id":boot,"clock":"CLOCK_MONOTONIC","record_kind":"receiver_interval","cookie":cookie,"stream_id":"17","role":"receiver","protocol":"TCP","local_host":"10.0.0.2","local_port":"5201","remote_host":"10.0.0.1","remote_port":"50100","interval_start_us":mono_post//1000+second*1000000,"interval_end_us":mono_post//1000+(second+1)*1000000,"bytes":125000000,"omitted":False})
            materialize("iperf3_server_error",None,
                b"".join(b"CNB_ACQ_V1 "+json.dumps(row,separators=(",",":" )).encode()+b"\n" for row in source_rows))

    commands.extend([
        command(units[0].unit_id,"vm_b",["sockperf","server","-i","10.0.0.2","-p","11111"],0),
        command(units[0].unit_id,"vm_b",["iperf3","-s","-B","10.0.0.2","-p","5201","--json-stream","--forceflush"],0)])
    if builds:
        sockperf_server_build=next(b for b in builds if b.tool=="sockperf" and b.role==VmRole.VM_B)
        commands[-2]=commands[-2].model_copy(update={"request":{**commands[-2].request,
            "argv":[sockperf_server_build.binary_path,"server","-i","10.0.0.2","-p","11111"]},
            "binary_version":Evidence[str](value=sockperf_server_build.binary_version),
            "source_identity":Evidence[str](value=sockperf_server_build.source_reference)})
        iperf_build=next(b for b in builds if b.tool=="iperf3" and b.role==VmRole.VM_B)
        commands[-1]=commands[-1].model_copy(update={"request":{**commands[-1].request,
            "action_id":"f02r-vm_b-listener-iperf3","acquisition_invocation_id":"f02r-vm_b-listener-iperf3",
            "launcher_argv":["systemd-run","--property=Environment=CNB_ACQUISITION_ID=f02r-vm_b-listener-iperf3",iperf_build.binary_path,"-s","-B","10.0.0.2","-p","5201","--json-stream","--forceflush"],
            "argv":["systemd-run","--property=Environment=CNB_ACQUISITION_ID=f02r-vm_b-listener-iperf3",iperf_build.binary_path,"-s","-B","10.0.0.2","-p","5201","--json-stream","--forceflush"]},
            "result":{**commands[-1].result,"action_id":"f02r-vm_b-listener-iperf3"},
            "binary_version":Evidence[str](value=iperf_build.binary_version),
            "source_identity":Evidence[str](value=iperf_build.source_reference)})
        records.extend(materialize_build_identity_artifacts(child_root,deployment_id,builds))
        if receiver_source_rows:
            materialize("iperf3_server_error",None,
                b"".join(b"CNB_ACQ_V1 "+json.dumps(row,separators=(",",":")).encode()+b"\n" for row in receiver_source_rows))
    for unit in units:
        if unit.kind!="probe_chunk":
            materialize(f"{unit.unit_id}-commands",unit.unit_id,json.dumps(
                [item.model_dump(mode="json") for item in commands if item.unit_id==unit.unit_id]))
        else:
            materialize(f"{unit.unit_id}-commands",unit.unit_id,json.dumps(
                [item.model_dump(mode="json") for item in commands if item.unit_id==unit.unit_id]))
    receiver_events=[]
    loaded_units=([units[0]] if value.mode=="diagnostic_day" else
        [unit for unit in units if unit.phase!="idle_latency"])
    offset_by_unit={unit.unit_id:(0 if value.mode=="diagnostic_day" else
        (unit.trial_index-1)*400 if value.mode=="calibration"
        else (100 if unit.phase=="single_flow" else 500)) for unit in loaded_units}
    for unit in loaded_units:
        offset=offset_by_unit[unit.unit_id]
        stamp=(NOW+timedelta(seconds=offset+(2 if value.mode=="diagnostic_day" else 7))).timestamp()
        received_bytes=10800000000000 if value.mode=="diagnostic_day" else 125000000
        receiver_seconds=86400 if value.mode=="diagnostic_day" else unit.measured_seconds
        interval_start=0 if value.mode=="diagnostic_day" else 3
        day_cookie=f"f08-{unit.unit_id}-session"
        day_settings={"protocol":"TCP","num_streams":1,"omit":5,"duration":86400,
            "interval":1,"blksize":131072,"reverse":False}
        day_connected=[{"socket":17,"local_host":"10.0.0.2","local_port":5201,
            "remote_host":"10.0.0.1","remote_port":50100}]
        day_start={"cookie":day_cookie,"test_start":day_settings,"connected":day_connected,
            "timestamp":{"timesecs":stamp}}
        short_settings={"protocol":"TCP","num_streams":unit.streams or 1,"omit":5,
            "duration":unit.measured_seconds,"interval":1,"blksize":131072,"reverse":False}
        short_connected=[{"socket":17+i,"local_host":"10.0.0.2","local_port":5201,
            "remote_host":"10.0.0.1","remote_port":50100+i} for i in range(unit.streams or 1)]
        short_start={"cookie":day_cookie,"test_start":short_settings,"connected":short_connected,
            "timestamp":{"timesecs":stamp}}
        receiver_events.append({"event":"start","data":day_start if value.mode == "diagnostic_day" else short_start})
        if value.mode == "diagnostic_day":
            receiver_events.extend({"event":"interval","data":{"streams":[{"socket":17,
                "start":second,"end":second+1,"seconds":1,"bytes":125000000,"sender":False,"omitted":False}]}}
                for second in range(86_400))
        else:
            receiver_events.append({"event":"interval","data":{"sum":{"start":interval_start,"end":interval_start+receiver_seconds,
                "seconds":receiver_seconds,"bytes":received_bytes,"sender":False,"omitted":False}}})
        receiver_events.append({"event":"end","data":{"sum_received":{"bytes":received_bytes,"seconds":receiver_seconds}}})
    materialize("iperf3_server_log",None,"\n".join(json.dumps(event) for event in receiver_events)+"\n")
    from cloud_network_benchmark.acquisition import bind_acquisition_sources
    receiver_command=commands[-1]
    sources=bind_acquisition_sources(child_root=child_root,run_identity=identity,commands=commands,
        receiver_command_evidence=[receiver_command],build_evidence=builds,source_artifact_records=records)
    if not sources:
        raise AssertionError("materialized fixture acquisition sources did not bind")
    for index,timing in enumerate(timings):
        matching=[]
        for source in sources:
            if source.tool=="sockperf" and (source.log_path.endswith(f"{timing.unit_id}/sockperf-acquisition.stderr")
                    or source.log_path.endswith(f"{timing.unit_id}.acquisition.stderr")):
                matching.append(source)
            elif source.tool=="iperf3" and (source.native_artifact_ids[0]==f"{timing.unit_id}-iperf3"
                    or (timing.unit_id==units[0].unit_id and source.native_artifact_ids[0]=="day-iperf3-client")):
                matching.append(source)
        if matching:
            updated=timing.model_copy(update={"acquisition_sources":Evidence[list](value=matching)})
            timings[index]=updated
            timing_record=next(r for r in records if r.artifact_id==f"{timing.unit_id}-timing")
            raw=updated.model_dump_json().encode()
            target=child_root/timing_record.path
            target.write_bytes(raw)
            records[records.index(timing_record)]=timing_record.model_copy(update={
                "size_bytes":Evidence[int](value=len(raw)),"sha256":StringEvidence(value=hashlib.sha256(raw).hexdigest())})
    # The clock records above use the same monotonic epoch as the frozen UTC mappings.
    for offset, state in enumerate(("provision_ready","running","collecting","validating","succeeded")):
        update_child_execution(child_path,state,NOW+timedelta(seconds=offset+1))
    update_child_cleanup(child_path,"attempted",NOW+timedelta(seconds=7))
    update_child_cleanup(child_path,"succeeded",NOW+timedelta(seconds=8))
    child=json.loads(child_path.read_text())
    tool_versions=StructuredEvidence(value=child["tool_versions"]["value"])
    provider_metadata=StructuredEvidence(value={"region":"us-east-1","zone":"us-east-1a"})
    vm_metadata=StructuredEvidence(value={str(item.vm_role):item.model_dump(mode="json") for item in metadata})
    manifest_units=[item for item in units if item.kind!="probe_chunk"]
    manifest_unit_ids={item.unit_id for item in manifest_units}
    manifest_commands=[item for item in commands if item.unit_id in manifest_unit_ids]
    manifest_timings=[item for item in timings if item.unit_id in manifest_unit_ids]
    manifest_records=[item for item in records if item.unit_id is None or item.unit_id in manifest_unit_ids]
    child.update({"measurement_units":[item.model_dump(mode="json") for item in manifest_units],
        "commands":[item.model_dump(mode="json") for item in manifest_commands],
        "timings":[item.model_dump(mode="json") for item in manifest_timings],
        "artifact_records":[item.model_dump(mode="json") for item in manifest_records],
        "execution_state":"succeeded","cleanup_state":"succeeded",
        "vm_metadata":vm_metadata.model_dump(mode="json"),"provider_metadata":provider_metadata.model_dump(mode="json"),
        "tool_versions":tool_versions.model_dump(mode="json"),
        "actual_started_at":NOW.isoformat(),"actual_finished_at":(NOW+timedelta(seconds=1)).isoformat()})
    child_path.write_text(json.dumps(child,sort_keys=True))
    resolved_sha=hashlib.sha256(resolved_bytes).hexdigest()
    git=child["config_provenance"]
    complete=value.model_copy(update={"identity":identity,"campaign_manifest_path":str(initialized.parent_manifest_path),
        "child_manifest_path":str(child_path),"child_result_root":str(child_root),
        "config_snapshot_path":"config.yaml","resolved_snapshot_path":"config.resolved.json",
        "config_sha256":hashlib.sha256(config_bytes).hexdigest(),"resolved_sha256":Evidence[str](value=resolved_sha),
        "measurement_units":units,"artifacts":declarations,"artifact_records":records,"commands":commands,"timings":timings,
        "vm_metadata_records":metadata,"vm_metadata":vm_metadata,"provider_metadata":provider_metadata,
        "tool_versions":tool_versions,"implementation_git_commit":StringEvidence(value=git["implementation_git_commit"]["value"]),
        "design_git_commit":StringEvidence(value=git["design_git_commit"]["value"]),
        "implementation_git":GitSnapshot.model_validate(git["implementation_git"]),
        "design_git":GitSnapshot.model_validate(git["design_git"]),
        "schedule_entry":Evidence[object](value=schedule),
        "schedule_sha256":Evidence[str](value=hashlib.sha256(schedule_bytes).hexdigest()),
        "schedule_snapshot_path":Evidence[str](value="schedule.csv"),"diagnostics":diagnostics})
    before={record.path:hashlib.sha256((child_root/record.path).read_bytes()).hexdigest() for record in records}
    result=validate_bundle(complete,evaluated_at=NOW)
    expected_validity="partial" if value.mode=="short_study" and not frozen_n else "valid"
    assert result.validation_status.data_validity == expected_validity, [(item.code,item.unit_id,item.artifact_id,item.message) for item in result.findings]
    assert result.metrics.value["execution"]["bundle_complete"] is (expected_validity=="valid")
    summary=summarize_validation(result)
    assert "EXP-001" in summary
    assert "Valid interval bounds (iperf3_server_log;" in summary
    assert validate_bundle(complete,evaluated_at=NOW).model_dump(mode="json")==result.model_dump(mode="json")
    assert evaluate_comparison([complete],[result])[run_id].validation_status.comparison_eligibility=="ineligible"
    csv_record=next(item for item in records if item.kind=="sockperf-csv")
    native_path=child_root/csv_record.path
    original=native_path.read_bytes()
    native_path.write_bytes(original+b"\n")
    assert validate_bundle(complete,evaluated_at=NOW).validation_status.data_validity=="invalid"
    native_path.write_bytes(original)
    assert before=={record.path:hashlib.sha256((child_root/record.path).read_bytes()).hexdigest() for record in records}
    return complete,result


def test_acceptance_materialized_bundle_replays_from_f01r_bytes(repository_root, tmp_path,
        frozen_clock, deterministic_token, fake_git_commits, request):
    _materialize_complete_bundle(repository_root,tmp_path,frozen_clock,deterministic_token,
        fake_git_commits,request,mode_override="acceptance")


def test_three_provider_materialized_calibrations_recommend_from_saved_bundles(repository_root,
        tmp_path,frozen_clock,deterministic_token,fake_git_commits,request):
    from cloud_network_benchmark.calibration import select_common_n

    results=[]
    for provider in ("aws","azure","gcp"):
        bundle,result=_materialize_complete_bundle(repository_root,tmp_path/provider,frozen_clock,
            deterministic_token,fake_git_commits,request,config_name="exp-001-calibration.yaml",provider=provider)
        assert all(record.sha256.value==hashlib.sha256((Path(bundle.child_result_root)/record.path).read_bytes()).hexdigest()
            for record in bundle.artifact_records)
        results.append(result)
    recommendation=select_common_n(results)
    assert recommendation.common_n.value == 2
    assert recommendation.plateau_found.value is True
    assert recommendation.source_sha256.value


def test_three_provider_short_study_replay_keeps_pending_common_n_ineligible(repository_root,
        tmp_path,frozen_clock,deterministic_token,fake_git_commits,request):
    from cloud_network_benchmark.validation import evaluate_comparison

    bundles,results=[],[]
    for provider in ("aws","azure","gcp"):
        bundle,result=_materialize_complete_bundle(repository_root,tmp_path/provider,frozen_clock,
            deterministic_token,fake_git_commits,request,provider=provider)
        bundles.append(bundle)
        results.append(result)
    paired=evaluate_comparison(bundles,results)
    assert set(paired)=={item.identity.run_id for item in bundles}
    assert all(item.validation_status.comparison_eligibility=="ineligible" for item in paired.values())
    assert all(item.validation_status.data_validity=="partial" for item in results)
    reasons=[item.validation_status.comparison_reasons for item in paired.values()]
    assert all("all compared data must be valid" in item and "execution or cleanup is incomplete" in item
        for item in reasons)
    assert all(item.metrics.value["common_n"]["state"]=="pending" for item in results)


def test_three_provider_short_study_with_prior_calibration_is_valid_and_comparable(repository_root,
        tmp_path,frozen_clock,deterministic_token,fake_git_commits,request):
    bundles,results=[],[]
    for provider in ("aws","azure","gcp"):
        bundle,result=_materialize_complete_bundle(repository_root,tmp_path/provider,frozen_clock,
            deterministic_token,fake_git_commits,request,provider=provider,frozen_n=True,shared_slot=True)
        bundles.append(bundle)
        results.append(result)
    assert all(result.validation_status.data_validity=="valid" for result in results)
    assert all(result.metrics.value["common_n"]["source_campaign_id"]=="prior-calibration-campaign" for result in results)
    paired=evaluate_comparison(bundles,results)
    assert all(item.validation_status.comparison_eligibility=="eligible" for item in paired.values())

    def change_result_source(result, campaign, digest):
        metrics=deepcopy(result.metrics.value)
        metrics["common_n"]["source_campaign_id"]=campaign
        metrics["common_n"]["source_sha256"]=digest
        return result.model_copy(update={"metrics":result.metrics.model_copy(update={"value":metrics})})

    result_only=[change_result_source(result,"unrelated-calibration","e"*64) for result in results]
    result_only_pairs=evaluate_comparison(bundles,result_only)
    assert all(item.validation_status.comparison_eligibility!="eligible" for item in result_only_pairs.values())
    one_peer=[change_result_source(results[0],"unrelated-calibration","e"*64),*results[1:]]
    one_peer_pairs=evaluate_comparison(bundles,one_peer)
    assert all(item.validation_status.comparison_eligibility!="eligible" for item in one_peer_pairs.values())


def test_materialized_persistent_listener_and_native_connected_endpoints_are_reconciled(repository_root,
        tmp_path,frozen_clock,deterministic_token,fake_git_commits,request):
    bundle,result=_materialize_complete_bundle(repository_root,tmp_path,frozen_clock,deterministic_token,
        fake_git_commits,request,mode_override="acceptance")
    listener=next(command for command in bundle.commands
        if command.producer=="vm_b" and command.request.get("action_id")=="f02r-vm_b-listener-iperf3")
    argv=list(listener.request["argv"])
    argv[argv.index("-B")+1]="10.0.0.9"
    wrong_listener=listener.model_copy(update={"request":{**listener.request,"argv":argv}})
    wrong_commands=[wrong_listener if command is listener else command for command in bundle.commands]
    wrong_bind_result=validate_bundle(bundle.model_copy(update={"commands":wrong_commands}),evaluated_at=result.evaluated_at)
    assert any(finding.code=="command.target_bind_mismatch" and finding.severity=="error"
        for finding in wrong_bind_result.findings)

    record=next(item for item in bundle.artifact_records if item.artifact_id=="iperf3_server_log")
    native_path=Path(bundle.child_result_root)/record.path
    original=native_path.read_bytes()
    try:
        rows=[json.loads(line) for line in original.splitlines() if line.strip()]
        start=next(row for row in rows if row.get("event")=="start")
        start["data"]["connected"][0]["local_host"]="10.0.0.9"
        native_path.write_text("\n".join(json.dumps(row) for row in rows)+"\n")
        endpoint_result=validate_bundle(bundle,evaluated_at=result.evaluated_at)
        assert any(finding.code=="native.iperf3.endpoint_mismatch" and finding.severity=="error"
            for finding in endpoint_result.findings)
    finally:
        native_path.write_bytes(original)


def test_diagnostic_day_materialized_bundle_replays_all_native_chunks(repository_root, tmp_path,
        frozen_clock, deterministic_token, fake_git_commits, request):
    bundle,result=_materialize_complete_bundle(repository_root,tmp_path,frozen_clock,deterministic_token,
        fake_git_commits,request,config_name="exp-001-diagnostic-day.yaml")
    day=next(unit for unit in bundle.measurement_units if unit.kind=="continuous_day")
    chunks=[unit for unit in bundle.measurement_units if unit.kind=="probe_chunk"]
    coverage=result.metrics.value["units"][0]["coverage"]
    assert len(chunks)==288 and all(chunk.measured_seconds==300 for chunk in chunks)
    assert result.validation_status.data_validity=="valid"
    assert result.metrics.value["execution"]["bundle_complete"] is True
    assert coverage["requested_seconds"]==86400
    assert coverage["observed_receiver_seconds"]["value"]==86400
    assert coverage["active_probe_seconds"]["value"]>=86400*.99
    assert coverage["active_probe_ratio"]["value"]>=.99
    assert coverage["tcp_start_bracket"]["source"]=="F01R acquisition helper"
    assert result.metrics.value["units"][0]["receiver_interval_count"]==86400
    dynamic={record.artifact_id for record in bundle.artifact_records
        if record.artifact_id.startswith("day-probe-csv-")}
    assert dynamic=={f"day-probe-csv-{chunk.unit_id}" for chunk in chunks}
    assert all(record.unit_id==record.artifact_id.removeprefix("day-probe-csv-")
        for record in bundle.artifact_records if record.artifact_id.startswith("day-probe-csv-"))
    summary=summarize_validation(result)
    assert "day-probe-csv-probe-chunk-000288" in summary
    assert validate_bundle(bundle,evaluated_at=result.evaluated_at).model_dump(mode="json")==result.model_dump(mode="json")
    day_timing=next(timing for timing in bundle.timings if timing.unit_id==day.unit_id)
    day_sources=day_timing.acquisition_sources.value
    assert len(day_sources)==1 and day_sources[0].tool=="iperf3"
    assert day_sources[0].native_artifact_ids==["day-iperf3-client","iperf3_server_log"]
    chunk_timings=[timing for timing in bundle.timings if timing.unit_id in {chunk.unit_id for chunk in chunks}]
    assert len(chunk_timings)==288
    assert all(len(timing.acquisition_sources.value)==1 and timing.acquisition_sources.value[0].tool=="sockperf"
        and timing.acquisition_sources.value[0].native_artifact_ids==[f"day-probe-csv-{timing.unit_id}"]
        for timing in chunk_timings)
    assert not any(finding.severity=="error" for finding in result.findings)


def test_materialized_native_replay_keeps_mode_unit_deployment_and_status_inputs_separate():
    root=Path(__file__).parents[1]/"fixtures"/"f08"/"native"
    csv_path, log_path, tcp_path = root/"sockperf.csv", root/"sockperf.log", root/"iperf3.json"
    before={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in (csv_path,log_path,tcp_path)}
    replay_shape={"acceptance":["idle_latency","single_flow","multi_flow"],
        "short_study":["idle_latency","single_flow","multi_flow"],
        "calibration":[f"trial_{index:02d}" for index in range(1,13)],
        "diagnostic_day":["probe_chunk_001"]}
    for mode, unit_ids in replay_shape.items():
        for unit_id in unit_ids:
            ref=SourceRef("sockperf-csv",csv_path.name,before[csv_path.name],unit_id,
                          f"{mode}-deployment","vm_a","3.10")
            parsed=parse_sockperf(csv_path,log_path,source=ref,
                log_source=SourceRef("sockperf-log",log_path.name,before[log_path.name],unit_id,
                                     f"{mode}-deployment","vm_a","3.10"))
            assert parsed.samples and all(sample.source.unit_id==unit_id for sample in parsed.samples)
            assert all(sample.source.deployment_id==f"{mode}-deployment" for sample in parsed.samples)
        if mode in {"acceptance","short_study","calibration"}:
            ref=SourceRef("iperf3_receiver",tcp_path.name,before[tcp_path.name],unit_ids[-1],
                          f"{mode}-deployment","vm_b","3.17.1")
            tcp=parse_iperf3(tcp_path,source=ref,format="json")
            assert tcp.receiver_intervals and all(item.source.unit_id==unit_ids[-1] for item in tcp.receiver_intervals)
    assert before=={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in (csv_path,log_path,tcp_path)}
