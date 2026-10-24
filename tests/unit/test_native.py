from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cloud_network_benchmark.native import (
    ReceiverInterval, SourceRef, parse_iperf3, parse_sockperf, receiver_statistics, rtt_statistics,
)


FIXTURES = Path(__file__).parents[1] / "fixtures" / "f08" / "native"


def source(path: Path, artifact: str = "sample") -> SourceRef:
    return SourceRef(artifact, path.name, hashlib.sha256(path.read_bytes()).hexdigest(), "unit-1", "deployment-1", "vm_a", "3.10")


def test_sockperf_native_preamble_rows_and_separate_counts_are_source_linked():
    csv_path = FIXTURES / "sockperf.csv"
    log_path = FIXTURES / "sockperf.log"
    before = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    parsed = parse_sockperf(csv_path, log_path, source=source(csv_path, "sockperf"), log_source=source(log_path, "sockperf-log"))
    assert [sample.rtt_us for sample in parsed.samples] == [1.0, 2.0]
    assert [sample.line for sample in parsed.samples] == [4, 5]
    assert parsed.probe_counts[0].sent.value == 2000
    assert parsed.probe_counts[0].dropped.value == 1
    assert parsed.probe_counts[0].duplicated.value == 2
    assert parsed.probe_counts[0].out_of_order.value == 3
    assert parsed.probe_counts[0].skipped.value == 0
    by_scope = {item.reporting_scope: item for item in parsed.probe_counts}
    assert set(by_scope) == {"[Total Run]", "[Valid Duration]"}
    assert by_scope["[Total Run]"].runtime_seconds.value == 2.1
    assert by_scope["[Valid Duration]"].runtime_seconds.value == 2.0
    assert hashlib.sha256(csv_path.read_bytes()).hexdigest() == before
    assert parsed.source.sha256 == before


def test_sockperf_missing_log_source_does_not_trust_log_counters():
    csv_path, log_path = FIXTURES / "sockperf.csv", FIXTURES / "sockperf.log"
    parsed = parse_sockperf(csv_path, log_path, source=source(csv_path))
    assert parsed.probe_counts[0].sent.value == 2000
    assert any(f.code == "native.sockperf.log_source" for f in parsed.findings)


def test_sockperf_rejects_conflicting_duplicate_counters_and_skips_separator_lines(tmp_path):
    csv_path = tmp_path / "sockperf.csv"
    csv_path.write_text("[Total Run] RunTime=2 sec; SentMessages=4; ReceivedMessages=2\n"
                        "------------------------------\npacket, txTime(sec), rxTime(sec), rtt(usec)\n"
                        "0, 1.000000, 1.000001, 1.000\n------------------------------\n"
                        "[Total Run] RunTime=3 sec; SentMessages=5; ReceivedMessages=2\n")
    parsed = parse_sockperf(csv_path, None, source=source(FIXTURES / "sockperf.csv"))
    assert len(parsed.samples) == 1
    assert any(item.code == "native.sockperf.counter_conflict" for item in parsed.findings)


def test_iperf3_receiver_is_authoritative_and_final_total_is_not_added():
    path = FIXTURES / "iperf3.json"
    parsed = parse_iperf3(path, source=source(path, "iperf3"), format="json")
    assert len(parsed.receiver_intervals) == 1
    assert parsed.receiver_totals[0]["bytes"] == 125000000
    stats = receiver_statistics(parsed.receiver_intervals)
    assert stats["received_bytes"].value == 125000000
    assert stats["throughput_gbps"].value == 1.0


def test_iperf3_sender_only_is_not_receiver_evidence():
    path = FIXTURES / "iperf3-sender-only.json"
    parsed = parse_iperf3(path, source=source(path), format="json")
    assert parsed.receiver_intervals == ()
    assert "receiver" in parsed.findings[-1].message


@pytest.mark.parametrize("payload", [
    '{"intervals":[],"intervals":[]}',
    '{"intervals":[{"sum":{"start":0,"end":1,"seconds":1,"bytes":true,"sender":false}}]}',
    '{"intervals":[{"sum":{"start":0,"end":1,"seconds":1,"bytes":-1,"sender":false}}]}',
    '{"intervals":[{"sum":{"start":0,"end":1,"seconds":NaN,"bytes":1,"sender":false}}]}',
])
def test_iperf3_rejects_ambiguous_or_invalid_json_values(tmp_path, payload):
    path = tmp_path / "invalid.json"
    path.write_text(payload)
    parsed = parse_iperf3(path, source=SourceRef("iperf3", path.name, "0" * 64), format="json")
    assert parsed.receiver_intervals == ()
    assert parsed.findings


def test_iperf3_multiple_sessions_are_kept_separate_and_not_silently_combined(tmp_path):
    path = tmp_path / "sessions.jsonl"
    interval = {"sum":{"start":5,"end":6,"seconds":1,"bytes":125000000,"sender":False}}
    path.write_text("\n".join(json.dumps(item) for item in [
        {"event":"start","data":{}}, {"event":"interval","data":interval}, {"event":"end","data":{}},
        {"event":"start","data":{}}, {"event":"interval","data":interval}, {"event":"end","data":{}},
    ]))
    parsed = parse_iperf3(path, source=SourceRef("iperf3", path.name, "0" * 64), format="jsonl")
    assert [item.session_id for item in parsed.receiver_intervals] == ["1", "2"]
    assert any(item.code == "native.iperf3.multiple_sessions" for item in parsed.findings)


@pytest.mark.parametrize(("filename", "finding", "intervals"), [
    ("iperf3-malformed-tail.jsonl", "native.iperf3.jsonl", 1),
    ("iperf3-duplicate.jsonl", "native.iperf3.interval_duplicate", 1),
    ("iperf3-reset.jsonl", "native.iperf3.session_reset", 1),
])
def test_native_stream_variants_preserve_only_trustworthy_intervals(filename, finding, intervals):
    path = FIXTURES / filename
    parsed = parse_iperf3(path, source=source(path), format="jsonl")
    assert len(parsed.receiver_intervals) == intervals
    assert any(item.code == finding for item in parsed.findings)



def test_iperf_omitted_and_postomit_reset_offsets_are_distinct_phases(tmp_path):
    path = tmp_path / "reset-phases.jsonl"
    rows = [
        {"event": "start", "data": {"cookie": "session"}},
        {"event": "interval", "data": {"streams": [
            {"socket": 17, "start": 0, "end": 4, "seconds": 4, "bytes": 250,
             "omitted": True, "sender": False}] }},
        {"event": "interval", "data": {"streams": [
            {"socket": 17, "start": 0, "end": 10, "seconds": 10, "bytes": 1000,
             "omitted": False, "sender": False}] }},
        {"event": "interval", "data": {"streams": [
            {"socket": 17, "start": 5, "end": 7, "seconds": 2, "bytes": 200,
             "omitted": False, "sender": False}] }},
        {"event": "end", "data": {}},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    parsed = parse_iperf3(path, source=source(path), format="jsonl")
    assert [(item.start_seconds, item.end_seconds, item.omitted) for item in parsed.receiver_intervals] == [
        (0, 4, True), (0, 10, False)]
    assert any(item.code == "native.iperf3.interval_overlap" for item in parsed.findings)


def test_sockperf_truncated_row_keeps_valid_rows():
    path = FIXTURES / "sockperf-corrupt.csv"
    parsed = parse_sockperf(path, None, source=source(path))
    assert [item.rtt_us for item in parsed.samples] == [1.0, 2.0]
    assert any(item.code == "native.sockperf.row" for item in parsed.findings)


def test_sockperf_missing_file_and_invalid_encoding_have_explicit_findings(tmp_path):
    missing = parse_sockperf(tmp_path / "missing.csv", None,
                             source=SourceRef("sockperf", "missing.csv", "0" * 64))
    assert any(item.code == "native.sockperf.read" for item in missing.findings)
    invalid = tmp_path / "invalid.csv"
    invalid.write_bytes(b"\xff\xfe")
    parsed = parse_sockperf(invalid, None, source=SourceRef("sockperf", "invalid.csv", "0" * 64))
    assert any(item.code == "native.sockperf.encoding" for item in parsed.findings)


def test_iperf3_jsonl_parses_events_and_keeps_receiver_interval():
    path = FIXTURES / "iperf3.jsonl"
    parsed = parse_iperf3(path, source=source(path), format="jsonl")
    assert [i.bytes for i in parsed.receiver_intervals] == [125000000]
    assert parsed.sessions == ("1",)


def test_quantiles_match_numpy_linear_and_sample_standard_deviation():
    pair = rtt_statistics([1, 2])
    assert pair["median_us"].value == 1.5
    assert pair["sample_stddev_us"].value == pytest.approx(2 ** -0.5)
    result = rtt_statistics([1, 2, 3, 4])
    assert result["median_us"].value == 2.5
    assert result["p95_us"].unavailable_reason
    assert result["sample_stddev_us"].value == pytest.approx((5 / 3) ** .5)
    assert rtt_statistics([])["median_us"].unavailable_reason
    assert rtt_statistics([1])["sample_stddev_us"].unavailable_reason


@pytest.mark.parametrize(("n", "p95", "p99"), [(999, False, False), (1000, True, False), (9999, True, False), (10000, True, True)])
def test_quantile_sample_count_boundaries(n: int, p95: bool, p99: bool):
    result = rtt_statistics(list(map(float, range(1, n + 1))))
    assert (result["p95_us"].value is not None) is p95
    assert (result["p99_us"].value is not None) is p99
    if n == 1000:
        assert result["p95_us"].value == pytest.approx(950.05)
    if n == 10000:
        assert result["p99_us"].value == pytest.approx(9900.01)


def test_receiver_skips_omitted_and_overlap_without_double_counting():
    path = FIXTURES / "iperf3.json"
    ref = source(path)
    intervals = [
        ReceiverInterval("1", 0, 1, 1, 125000000, False, False, ref, 1),
        ReceiverInterval("1", 1, 2, 1, 125000000, True, False, ref, 2),
        ReceiverInterval("1", .5, 1.5, 1, 500000000, False, False, ref, 3),
    ]
    stats = receiver_statistics(intervals)
    assert stats["received_bytes"].value == 125000000
    assert stats["measured_seconds"].value == 1
