import json
from pathlib import Path

import pytest

from cloud_network_benchmark.measurement import (
    conservative_coverage, conservative_measurement_bounds, conservative_sockperf_run_start_bounds,
    covered_seconds, merge_intervals, receiver_duration, receiver_timing, _sockperf_full_log_timing,
)


def test_acquisition_coverage_uses_inner_probe_support_and_outer_receiver_bounds():
    from cloud_network_benchmark.acquisition import SourceWindow, overlap_coverage_lower_bound
    receiver = SourceWindow("receiver_measured", "iperf3", "vm_a", "12345678-1234-1234-1234-123456789abc",
        "CLOCK_MONOTONIC", "cookie", "stream", 0, 0, 1000, 1000, False, 0, 1)
    exact = SourceWindow("probe_enabled", "sockperf", "vm_a", receiver.boot_id,
        "CLOCK_MONOTONIC", None, None, 0, 0, 990, 990, None, 0, 1)
    straddling = SourceWindow("probe_enabled", "sockperf", "vm_a", receiver.boot_id,
        "CLOCK_MONOTONIC", None, None, 0, 0, 989, 989, None, 0, 1)
    assert overlap_coverage_lower_bound([exact], [receiver]) == 0.99
    assert overlap_coverage_lower_bound([straddling], [receiver]) < 0.99
    different = SourceWindow("receiver_measured", "iperf3", "vm_a", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "CLOCK_MONOTONIC", "cookie", "stream", 0, 0, 1000, 1000, False, 0, 1)
    assert overlap_coverage_lower_bound([exact], [different]) is None


def test_source_window_rejects_boolean_or_reversed_bounds_and_omitted_is_not_coverage():
    from cloud_network_benchmark.acquisition import SourceWindow
    values = dict(kind="probe_enabled", tool="sockperf", producer="vm_a", boot_id="12345678-1234-1234-1234-123456789abc",
        clock_domain="CLOCK_MONOTONIC", session_id=None, stream_id=None, start_earliest_ns=1,
        start_latest_ns=1, end_earliest_ns=2, end_latest_ns=2, omitted=None, source_byte_offset=0, source_byte_length=1)
    with pytest.raises(ValueError, match="integer"):
        SourceWindow(**{**values, "start_earliest_ns": True})
    with pytest.raises(ValueError, match="ordered"):
        SourceWindow(**{**values, "start_earliest_ns": 3})
    omitted = SourceWindow(**{**values, "kind": "receiver_measured", "tool": "iperf3", "session_id": "cookie", "stream_id": "s", "omitted": True})
    assert not omitted.has_positive_inner_support


def test_receiver_duration_uses_only_receiver_side_sum_received():
    assert receiver_duration(json.dumps({"end": {"sum_sent": {"seconds": 60}}})) is None
    assert receiver_duration(json.dumps({"server_output": {"end": {"sum_received": {"seconds": 60.0}}}})) == 60.0


def test_pinned_iperf_json_and_json_stream_native_evidence_fields():
    # Shape emitted by iperf3 3.17.1: test_start carries omit; the remote
    # receiver result is included as server_output_json. These are synthetic
    # values in the producer's native format, not a real benchmark result.
    normal = json.dumps({
        "start": {"test_start": {"omit": 5, "duration": 60}},
        "server_output_json": {"end": {"sum_received": {"seconds": 60.0}}},
        "end": {"sum_sent": {"seconds": 60.0}},
    })
    assert receiver_timing(normal) == (60.0, 5.0)
    stream = "\n".join((
        json.dumps({"event": "start", "data": {"start": {"test_start": {"omit": 5}}}}),
        json.dumps({"event": "server_output_json", "data": {"end": {"sum_received": {"seconds": 60.0}}}}),
        json.dumps({"event": "end", "data": {"sum_sent": {"seconds": 60.0}}}),
        "{truncated final record",
    ))
    assert receiver_timing(stream) == (60.0, 5.0)
    assert receiver_timing(json.dumps({"start": {"test_start": {"omit": 0}},
        "server_output_json": {"end": {"sum_received": {"seconds": 60}}}})) == (60.0, 0.0)


def test_process_and_receiver_bounds_require_verified_omission_and_full_duration():
    assert conservative_measurement_bounds(10, 80, 60, 60, 5, omission_verified=True) == (15, 20)
    assert conservative_measurement_bounds(10, 80, 59.9, 60, 5, omission_verified=True) is None
    assert conservative_measurement_bounds(10, 80, 60, 60, 5, omission_verified=False) is None
    assert conservative_measurement_bounds(10, 80, 60, 60, 5, omission_verified=True) == (15, 20)
    assert conservative_measurement_bounds(20, 19, 60, 60, 5, omission_verified=True) is None


def test_sockperf_source_native_runtime_bounds_run_start_but_not_sample_coverage(tmp_path):
    log = tmp_path / "sockperf.csv"
    log.write_text("""------------------------------
packet, txTime(sec), rxTime(sec), Round trip(usec)
0, 1000.000000000, 1000.000010000, 10.000
------------------------------
[Total Run] RunTime=70.000 sec; Warm up time=400 msec; SentMessages=100; ReceivedMessages=99
[Valid Duration] RunTime=69.550 sec; SentMessages=100; ReceivedMessages=99
""", encoding="utf-8")
    total, valid = _sockperf_full_log_timing(log)
    assert (total, valid) == (70.0, 69.55)
    # Popen lifetime encloses s_start/s_end. Three-decimal rounding is expanded
    # conservatively; this bounds test-run start only, not filtered probe windows.
    bounds = conservative_sockperf_run_start_bounds(10, 90, total, semantics_verified=True)
    assert bounds is not None
    assert bounds == pytest.approx((10, 20.0005))
    assert conservative_sockperf_run_start_bounds(10, 90, total, semantics_verified=False) is None
    assert conservative_sockperf_run_start_bounds(30, 90, total, semantics_verified=True) is None
    assert _sockperf_full_log_timing(tmp_path / "missing.csv") == (None, None)


def test_interval_union_and_gaps_do_not_double_count():
    assert merge_intervals([(0, 6), (4, 10), (10, 12)]) == [(0, 12)]
    assert covered_seconds(0, 10, [(0, 6), (4, 10)], [(4, 5)]) == 9


def test_synthetic_timing_cases(tmp_path):
    cases = json.loads((Path(__file__).parents[1] / "fixtures/f03/timing-cases.json").read_text())
    for name, case in cases.items():
        if case.get("expected") is None and "expected_minimum_coverage_seconds" not in case:
            assert conservative_coverage(case["duration_seconds"], tuple(case["start_bounds"]), []) is None
            continue
        observed = conservative_coverage(case["duration_seconds"], tuple(case["start_bounds"]),
            case.get("probe_windows", []), case.get("gaps", []))
        assert observed == pytest.approx(case["expected_minimum_coverage_seconds"], abs=1e-9), name
        if name in {"boundary_99_percent", "below_99_percent"}:
            assert (observed / case["duration_seconds"] >= 0.99) == (name == "boundary_99_percent")


def test_coverage_uses_worst_possible_start_and_refuses_unknown_anchor():
    assert conservative_coverage(10, (0, 5), [(0, 10)]) == 5
    assert conservative_coverage(10, (0, float("inf")), [(0, 100)]) is None


@pytest.mark.parametrize("bounds", [(0, float("inf")), (2, 1)])
def test_invalid_or_unbounded_envelope_is_unavailable(bounds):
    assert conservative_coverage(60, bounds, [(0, 100)]) is None
