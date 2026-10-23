from __future__ import annotations

import inspect

from cloud_network_benchmark import calibration, native, summaries, validation
from cloud_network_benchmark.contracts.validation import ValidationResult


def test_public_function_signatures_and_frozen_native_records_match_contract():
    assert list(inspect.signature(native.parse_sockperf).parameters) == ["csv_path", "log_path", "source", "log_source"]
    assert list(inspect.signature(native.parse_iperf3).parameters) == ["path", "source", "format"]
    assert list(inspect.signature(native.rtt_statistics).parameters) == ["samples_us"]
    assert list(inspect.signature(native.receiver_statistics).parameters) == ["intervals"]
    assert list(inspect.signature(validation.validate_bundle).parameters) == ["value", "evaluated_at"]
    assert list(inspect.signature(validation.evaluate_comparison).parameters) == ["inputs", "results"]
    assert list(inspect.signature(calibration.select_common_n).parameters) == ["results"]
    assert list(inspect.signature(summaries.summarize_validation).parameters) == ["value"]
    for record in (native.SourceRef, native.RttSample, native.ProbeCounts, native.ReceiverInterval, native.NativeParse):
        assert record.__dataclass_params__.frozen


def test_native_records_reject_invalid_source_and_measurement_values():
    import pytest
    with pytest.raises(ValueError, match="SHA-256"):
        native.SourceRef("iperf3", "input.json", "not-a-hash")
    source = native.SourceRef("iperf3", "input.json", "a" * 64)
    with pytest.raises(ValueError, match="bytes"):
        native.ReceiverInterval("1", 0, 1, 1, True, False, False, source, 1)
    with pytest.raises(ValueError, match="RTT sample timing"):
        native.RttSample(0, 1.0, 2.0, 2.0, source, 1)


def test_f08_reuses_existing_f01r_validation_result_schema():
    schema=ValidationResult.model_json_schema()
    assert set(schema["properties"]) == {"schema_version","run_id","mode","validation_status","findings","metrics","evaluated_at"}
    assert schema["properties"]["schema_version"]["const"] == 2
