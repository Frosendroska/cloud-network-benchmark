import hashlib
from pathlib import Path

from datetime import datetime, timedelta, timezone

from cloud_network_benchmark.admission import aws_expiry_cost_handoff, bounded_copy_handoff, load_cost_inputs


ROOT = Path(__file__).parents[2]


def test_aws_support_handoff_binds_fixture_and_all_selected_regions():
    cost = load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json")
    fixture = ROOT / "tests/fixtures/aws/cost-inputs/aws-ssm-automation-public-2026-10-22.json"
    payload = aws_expiry_cost_handoff(cost, fixture, ["eu-west-2", "eu-central-1"], reservation_sha256=cost.payload_sha256)
    assert payload["source_sha256"] == cost.payload_sha256
    assert payload["expiry_input_sha256"] == hashlib.sha256(fixture.read_bytes()).hexdigest()
    assert payload["expiry_input_sha256"] == "315d3e49755396597f97cac3512b9843fcf1c95083fadc11fae667c770215a61"
    assert payload["expiry_cost_regions"] == ["eu-central-1", "eu-west-2"]
    assert payload["expiry_cost_included_usd"] == 0.0278


def test_copy_handoff_keeps_cleanup_inside_original_trigger_and_one_retry():
    now = datetime(2026, 10, 22, tzinfo=timezone.utc)
    cap = now + timedelta(minutes=30)
    handoff = bounded_copy_handoff(reservation_id="reservation-1", cap_end=cap, provider_trigger=cap,
        now=now, cleanup_seconds=120, retry_seconds=60, failed_artifact_ids=["sockperf.csv", "iperf3.json"],
        already_retried=["iperf3.json"])
    assert handoff.work_deadline == cap - timedelta(seconds=120)
    assert handoff.retry_artifact_ids == ("sockperf.csv",)
    assert handoff.refused_artifact_ids == ("iperf3.json",)
    assert handoff.preserve_partial_evidence and handoff.request_normal_cleanup
