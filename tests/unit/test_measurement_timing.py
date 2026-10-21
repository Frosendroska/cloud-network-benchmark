import json
from pathlib import Path

import pytest

from cloud_network_benchmark.measurement import conservative_coverage, conservative_measurement_bounds, covered_seconds, merge_intervals, receiver_duration


def test_receiver_duration_uses_only_receiver_side_sum_received():
    assert receiver_duration(json.dumps({"end": {"sum_sent": {"seconds": 60}}})) is None
    assert receiver_duration(json.dumps({"server_output": {"end": {"sum_received": {"seconds": 60.0}}}})) == 60.0


def test_process_and_receiver_bounds_require_verified_omission_and_full_duration():
    assert conservative_measurement_bounds(10, 80, 60, 60, 5, omission_verified=True) == (15, 20)
    assert conservative_measurement_bounds(10, 80, 59.9, 60, 5, omission_verified=True) is None
    assert conservative_measurement_bounds(10, 80, 60, 60, 5, omission_verified=False) is None


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


@pytest.mark.parametrize("bounds", [(0, float("inf")), (2, 1)])
def test_invalid_or_unbounded_envelope_is_unavailable(bounds):
    assert conservative_coverage(60, bounds, [(0, 100)]) is None
