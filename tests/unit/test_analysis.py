from __future__ import annotations

import json
from pathlib import Path

import pytest

from cloud_network_benchmark.analysis import _bin_width, _deployment_summary, _sample_bins
from cloud_network_benchmark.contracts.common import Evidence
from cloud_network_benchmark.native import ReceiverInterval, SourceRef, receiver_statistics, rtt_statistics


def test_receiver_rate_uses_sum_of_bytes_over_sum_of_elapsed_seconds():
    source = SourceRef("receiver", "receiver.jsonl", "a" * 64)
    intervals = (
        ReceiverInterval("1", 0, 1, 1, 100_000_000, False, False, source, 1),
        ReceiverInterval("1", 1, 4, 3, 200_000_000, False, False, source, 2),
    )
    stats = receiver_statistics(intervals)
    assert stats["received_bytes"] == Evidence[int](value=300_000_000)
    assert stats["measured_seconds"] == Evidence[float](value=4)
    assert stats["throughput_gbps"].value == pytest.approx(.6, rel=1e-12)


def test_receiver_rate_excludes_omitted_and_sender_interval_scopes():
    source = SourceRef("receiver", "receiver.jsonl", "c" * 64)
    intervals = (
        ReceiverInterval("1", 0, 1, 1, 100, False, False, source, 1),
        ReceiverInterval("1", 1, 2, 1, 500, True, False, source, 2),
        ReceiverInterval("1", 2, 3, 1, 700, False, True, source, 3),
    )
    stats = receiver_statistics(intervals)
    assert stats["received_bytes"].value == 100
    assert stats["measured_seconds"].value == 1


def test_rtt_statistics_keep_raw_samples_and_native_thresholds():
    stats = rtt_statistics([1, 2, 3, 4])
    assert stats["median_us"].value == 2.5
    assert stats["p95_us"].value is None
    assert "1000" in stats["p95_us"].unavailable_reason
    assert stats["sample_stddev_us"].value == pytest.approx(1.2909944487358056)
    many = rtt_statistics([float(index) for index in range(1000)])
    assert many["p95_us"].value is not None
    assert many["p99_us"].value is None


def test_deployment_quantiles_are_linear_and_count_deployments_only():
    assert _deployment_summary([1, 2, 8, 9]) == {
        "n": 4, "points": [1, 2, 8, 9], "median": 5,
        "q1": 1.75, "q3": 8.25, "iqr": 6.5,
    }
    with pytest.raises(ValueError):
        _deployment_summary([])


@pytest.mark.parametrize("width", [1, 60])
def test_rtt_bins_are_half_open_and_use_transmit_time_only(width):
    source = SourceRef("sockperf", "rtt.csv", "b" * 64)
    from cloud_network_benchmark.native import RttSample
    samples = (RttSample(0, 0, .001, 1000, source, 2),
               RttSample(1, width - .000001, width + .000999, 1000, source, 3),
               RttSample(2, width, width + .001, 1000, source, 4))
    buckets = _sample_bins([(sample.tx_seconds, sample) for sample in samples], width)
    assert [index for index, _ in buckets] == [0, 1]
    assert [sample.packet_index for _, sample in buckets[0][1]] == [0, 1]
    assert [sample.packet_index for _, sample in buckets[1][1]] == [2]


def test_fixture_oracle_is_independent():
    expected = json.loads((Path(__file__).parents[1] / "fixtures/f12/expected.json").read_text())
    assert expected["receiver"]["throughput_gbps"] == .6
    assert expected["deployments"]["iqr"] == 6.5


@pytest.mark.parametrize("mode,width", [("diagnostic_day", 60), ("short_study", 1),
    ("acceptance", 1), ("calibration", 1)])
def test_bin_width_follows_existing_mode(mode, width):
    assert _bin_width(mode) == width
