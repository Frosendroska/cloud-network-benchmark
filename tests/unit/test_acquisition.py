from pathlib import Path

import pytest

from cloud_network_benchmark.acquisition import parse_observation_stream, _sockperf_window, _iperf_windows
from cloud_network_benchmark.contracts import AcquisitionSource, VmRole


def source():
    return AcquisitionSource(tool="sockperf", semantics_id="sockperf-3.10-enabled-v1", producer=VmRole.VM_A,
        command_action_id="sockperf-short-multiflow", invocation_id="sockperf-short-multiflow",
        boot_id="12345678-1234-1234-1234-123456789abc", clock_domain="CLOCK_MONOTONIC",
        log_artifact_id="sockperf-log", log_path="measurements/sockperf.stderr",
        native_artifact_ids=["sockperf-csv"], build_artifact_id="build-sockperf")


def test_sockperf_fixture_derives_inner_enabled_and_native_filter_bounds():
    root = Path(__file__).parents[1] / "fixtures" / "acquisition"
    with (root / "sockperf-window.log").open("rb") as stream:
        rows = list(parse_observation_stream(stream))
    record = next(row for row in rows if row.value["record_kind"] == "probe_window")
    window = _sockperf_window(record, source())
    assert (window.start_earliest_ns, window.end_earliest_ns) == (6_000_000_000, 56_000_000_000)
    assert window.has_positive_inner_support


@pytest.mark.parametrize("payload,reason", [
    (b'CNB_ACQ_V1 {"format":1,"format":1}\n', "duplicate JSON key"),
    (b'CNB_ACQ_V1 {"format":1}', "truncated"),
    (b'CNB_ACQ_V1 {"format":true}\n', "unsupported"),
    (b'CNB_ACQ_V1 {"format":NaN}\n', "invalid number"),
])
def test_recognized_malformed_and_truncated_lines_are_not_ignored(payload, reason):
    import io
    with pytest.raises(ValueError, match=reason):
        list(parse_observation_stream(io.BytesIO(payload)))


def test_unrelated_stderr_is_ignored_and_offsets_refer_to_original_bytes():
    import io
    raw = b"ordinary notice\nCNB_ACQ_V1 {\"format\":1,\"ok\":true}\n"
    rows = list(parse_observation_stream(io.BytesIO(raw)))
    row = rows[0]
    assert raw[row.byte_offset:row.byte_offset + row.byte_length].endswith(b"\n")
    assert row.value["ok"] is True


def test_iperf_receiver_intervals_are_session_bound_microsecond_intervals_and_zero_bytes_survive():
    from cloud_network_benchmark.contracts import AcquisitionSource
    from cloud_network_benchmark.acquisition import SourceWindow
    root = Path(__file__).parents[1] / "fixtures" / "acquisition"
    iperf = AcquisitionSource(tool="iperf3", semantics_id="iperf-3.17.1-stats-v1", producer=VmRole.VM_B,
        command_action_id="f02r-vm_b-listener-iperf3", invocation_id="f02r-vm_b-listener-iperf3",
        session_id="native-cookie-1", boot_id="12345678-1234-1234-1234-123456789def",
        clock_domain="CLOCK_MONOTONIC", log_artifact_id="iperf-observation", log_path="server-logs/iperf.stderr",
        native_artifact_ids=["iperf-client"], build_artifact_id="iperf-build")
    with (root / "iperf-receiver.log").open("rb") as stream:
        rows = parse_observation_stream(stream)
        receiver = list(_iperf_windows((r for r in rows if r.value.get("cookie") == iperf.session_id
                                        and r.value["record_kind"] != "clock_anchor"), iperf))
    assert len(receiver) == 2
    assert receiver[0].omitted is True
    window = receiver[1]
    assert (window.start_earliest_ns, window.start_latest_ns) == (5_000_000_000, 5_000_001_000)
    assert (window.end_earliest_ns, window.end_latest_ns) == (6_000_000_000, 6_000_001_000)
    assert window.omitted is False and window.has_positive_inner_support
    assert window.stream_id == "17"


def test_receiver_stream_replay_keeps_memory_bounded_for_large_interval_logs():
    import io, json, tracemalloc
    from cloud_network_benchmark.acquisition import _iperf_windows
    source_ref = AcquisitionSource(tool="iperf3", semantics_id="iperf-3.17.1-stats-v1", producer=VmRole.VM_B,
        command_action_id="f02r-vm_b-listener-iperf3", invocation_id="f02r-vm_b-listener-iperf3",
        session_id="native-cookie-1", boot_id="12345678-1234-1234-1234-123456789def",
        clock_domain="CLOCK_MONOTONIC", log_artifact_id="iperf-observation", log_path="server-logs/iperf.stderr",
        native_artifact_ids=["iperf-client"], build_artifact_id="iperf-build")
    common = {"format": 1, "tool": "iperf3", "semantics_id": source_ref.semantics_id,
        "invocation_id": source_ref.invocation_id, "pid": 51, "boot_id": source_ref.boot_id,
        "clock": source_ref.clock_domain, "cookie": source_ref.session_id}
    settings = {**common, "record_kind": "session_settings", "protocol": "TCP", "num_streams": 1,
        "omit": 5, "duration": 60, "interval_us": 1000000, "block_size": 131072, "reverse": False}
    payload = [b"ordinary server notice\n", b"CNB_ACQ_V1 " + json.dumps(settings).encode() + b"\n", *[(b"CNB_ACQ_V1 " + json.dumps({**common,
        "record_kind": "stats_origin", "stream_id": "1", "phase": phase,
        "origin_us": 0, "start_time_fixed_us": 0}).encode() + b"\n") for phase in ("initial", "post_omit")]]
    count = 50_000
    for index in range(count):
        value = {**common, "record_kind": "receiver_interval", "stream_id": "1",
            "role": "receiver", "protocol": "tcp", "local_host": "10.0.0.2", "local_port": "5201",
            "remote_host": "10.0.0.1", "remote_port": "50100", "interval_start_us": index,
            "interval_end_us": index + 1, "bytes": 0, "omitted": False}
        payload.append(b"CNB_ACQ_V1 " + json.dumps(value, separators=(",", ":")).encode() + b"\n")
    data = b"".join(payload)
    del payload
    tracemalloc.start()
    parsed = parse_observation_stream(io.BytesIO(data))
    windows = _iperf_windows((item for item in parsed if item.value.get("record_kind") != "clock_anchor"), source_ref)
    observed = sum(1 for _ in windows)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert observed == count
    assert peak < 1_000_000


@pytest.mark.parametrize("patch,reason", [
    (lambda value: {**value, "cookie": "wrong-session"}, "session cookie"),
    (lambda value: {**value, "stream_id": "999"}, "initial statistic origin"),
    (lambda value: {**value, "role": "sender"}, "receiver evidence"),
])
def test_iperf_receiver_record_mutations_are_rejected(patch, reason):
    import io, json
    from cloud_network_benchmark.contracts import AcquisitionSource
    from cloud_network_benchmark.acquisition import _iperf_windows, ParsedRecord
    source_ref = AcquisitionSource(tool="iperf3", semantics_id="iperf-3.17.1-stats-v1", producer=VmRole.VM_B,
        command_action_id="f02r-vm_b-listener-iperf3", invocation_id="f02r-vm_b-listener-iperf3", session_id="native-cookie-1",
        boot_id="12345678-1234-1234-1234-123456789def", clock_domain="CLOCK_MONOTONIC",
        log_artifact_id="iperf-observation", log_path="server-logs/iperf.stderr",
        native_artifact_ids=["iperf-client"], build_artifact_id="iperf-build")
    root = Path(__file__).parents[1] / "fixtures" / "acquisition"
    with (root / "iperf-receiver.log").open("rb") as stream:
        parsed = [row for row in parse_observation_stream(stream) if row.value.get("cookie") == source_ref.session_id]
    origins = [r for r in parsed if r.value["record_kind"] == "stats_origin"]
    interval = next(r for r in parsed if r.value["record_kind"] == "receiver_interval")
    changed = ParsedRecord(patch(dict(interval.value)), interval.byte_offset, interval.byte_length)
    with pytest.raises(ValueError, match=reason):
        list(_iperf_windows(iter(origins + [changed]), source_ref))
