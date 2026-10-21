from __future__ import annotations

from datetime import datetime, timezone

from cloud_network_benchmark.contracts.validation import ValidationResult
from cloud_network_benchmark.summaries import summarize_validation


def test_summary_separates_status_cleanup_and_preserves_sources_and_failures():
    result=ValidationResult(schema_version=2, run_id="run-aws", mode="short_study",
        validation_status={"data_validity":"partial","data_reasons":["partial receiver interval"],
                           "comparison_eligibility":"unavailable","comparison_reasons":["start missing"]},
        findings=[{"code":"timing.gap","severity":"warning","message":"one interval gap"}],
        metrics={"value":{"identity":{"provider":"aws"},"execution":{"execution_state":"failed","cleanup_state":"failed","bundle_complete":False},
          "provenance":{"config_sha256":"a"*64,"resolved_sha256":{"unavailable_reason":"missing"},
                        "schedule_sha256":{"value":"b"*64}},
          "commands":[{"unit_id":"single_flow","producer":"vm_a","request":{"argv":["iperf3","-c","host","-p","5201"]}}],
          "diagnostics":[{"vm_role":"vm_a","cpu_total_percent":{"value":12.5},"reset_fields":["rx_bytes"],"gap_reason":"sample gap"}],
          "units":[{"unit_id":"single_flow","phase":"single_flow","rtt":{"median_us":{"value":2.5},"p95_us":{"unavailable_reason":"few replies"}},
                    "tcp":{"throughput_gbps":{"value":1.2}},"probe_counts":[{"sent":{"value":5},"received":{"value":4},"dropped":{"value":1},
                    "duplicated":{"value":0},"out_of_order":{"value":0},"skipped":{"value":0}}],"coverage":{"active_probe_ratio":{"value":.98},"gaps":[{"reason":"gap"}]}}]}},
        evaluated_at=datetime(2026,10,21,tzinfo=timezone.utc))
    summary=summarize_validation(result)
    assert "Data: partial" in summary
    assert "Comparison: unavailable" in summary
    assert "cleanup: failed" in summary
    assert "dropped=1" in summary
    assert "unavailable: few replies" in summary
    assert "Schedule SHA-256: " + "b" * 64 in summary
    assert "iperf3 -c host -p 5201" in summary
    assert "CPU total range 12.5–12.5%" in summary
    assert summarize_validation(result) == summary
