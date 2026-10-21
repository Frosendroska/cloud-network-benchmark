from __future__ import annotations

import hashlib
import json
from pathlib import Path

from cloud_network_benchmark.native import SourceRef, parse_iperf3, parse_sockperf, receiver_statistics, rtt_statistics


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
