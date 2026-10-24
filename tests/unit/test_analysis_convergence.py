from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

from cloud_network_benchmark.analysis import (
    _containing_window,
    _combine_day_minute_parts,
    _deduplicate_cost_rows,
    _diagnostic_alignment_scope,
    _iperf_receiver_origin,
    _receiver_interval_bin,
    _receiver_bin_metrics,
    _unique_deployment_attempts,
    _verified_provider_bill,
)


def test_deployment_attempt_reduction_deduplicates_and_excludes_conflicts_canonically():
    rows = [
        {"deployment_id": "D2", "run_id": "later", "value": 100.0, "source_refs": ["b"]},
        {"deployment_id": "D1", "run_id": "replacement", "value": 7.0, "source_refs": ["z"]},
        {"deployment_id": "D1", "run_id": "original", "value": 7.0, "source_refs": ["a"]},
        {"deployment_id": "D3", "run_id": "conflict-a", "value": 10.0, "source_refs": []},
        {"deployment_id": "D3", "run_id": "conflict-b", "value": 11.0, "source_refs": []},
    ]

    selected, conflicts = _unique_deployment_attempts(rows, lambda row: row["value"])
    reversed_selected, reversed_conflicts = _unique_deployment_attempts(list(reversed(rows)), lambda row: row["value"])

    assert conflicts == reversed_conflicts == ["D3"]
    assert [(row["deployment_id"], row["value"], row["run_ids"], row["source_refs"])
            for row in selected] == [(row["deployment_id"], row["value"], row["run_ids"], row["source_refs"])
            for row in reversed_selected]
    assert [row["deployment_id"] for row in selected] == ["D1", "D2"]
    assert selected[0]["run_ids"] == ["original", "replacement"]
    assert selected[0]["source_refs"] == ["a", "z"]


def test_receiver_interval_lookup_preserves_gaps_and_requires_full_containment():
    windows = [(0.0, 2.0), (3.0, 5.0)]
    starts = [left for left, _right in windows]

    assert _containing_window(windows, starts, 0.2, 1.9)
    assert _containing_window(windows, starts, 3.0, 5.0)
    assert not _containing_window(windows, starts, 2.0, 3.0)
    assert not _containing_window(windows, starts, 1.9, 2.1)
    assert not _containing_window(windows, starts, 4.9, 5.1)


def test_native_receiver_origin_matches_f08_mode_specific_omit_reset():
    from cloud_network_benchmark.native import NativeParse

    tcp = NativeParse(session_details=({"start": {"timestamp": {"timesecs": 1792540907},
        "test_start": {"omit": 2}}},))
    assert _iperf_receiver_origin(tcp, post_omit_origin=False) == (1792540907.0, 2.0)
    assert _iperf_receiver_origin(tcp, post_omit_origin=True) == (1792540909.0, 2.0)


def test_receiver_interval_straddle_invalidates_every_touched_bin():
    whole_index, whole_bins = _receiver_interval_bin(0.0, .5, 0.0, 1.0, 0.0)
    straddle_index, straddle_bins = _receiver_interval_bin(.5, 1.5, 0.0, 1.0, 0.0)
    assert whole_index == 0 and whole_bins == [0]
    assert straddle_index is None and straddle_bins == [0, 1]
    assert _receiver_interval_bin(.2, .5, 0.0, 1.0, .01)[0] == 0
    assert _receiver_interval_bin(.2, .5, 0.0, 1.0, .9)[0] is None

    from cloud_network_benchmark.native import ReceiverInterval, SourceRef
    source = SourceRef("receiver", "receiver.jsonl", "a" * 64)
    whole_interval = ReceiverInterval("session", 0.0, .5, .5, 100, False, False, source, 1)
    primary, whole_evidence = _receiver_bin_metrics([whole_interval], straddled=True,
        unavailable_reason="straddled interval excluded")
    assert primary["throughput_gbps"].value is None
    assert primary["throughput_gbps"].unavailable_reason == "receiver interval straddles a bin boundary"
    assert whole_evidence["received_bytes"].value == 100
    assert whole_evidence["throughput_gbps"].value == .0000016


def test_day_minute_combines_raw_samples_and_unions_chunk_support():
    parts = [
        {"samples": [SimpleNamespace(rtt_us=1.0)], "support": [(0.0, 35.0)],
         "chunk_ids": ["chunk-a"], "chunk_windows": {"chunk-a": {"start_utc": "a"}},
         "source_refs": ["a.csv"]},
        {"samples": [SimpleNamespace(rtt_us=100.0)], "support": [(30.0, 60.0)],
         "chunk_ids": ["chunk-b"], "chunk_windows": {"chunk-b": {"start_utc": "b"}},
         "source_refs": ["b.csv"]},
    ]
    combined = _combine_day_minute_parts(parts)
    from cloud_network_benchmark.native import rtt_statistics
    assert rtt_statistics([sample.rtt_us for sample in combined["samples"]])["median_us"].value == 50.5
    assert sum(end - start for start, end in combined["support"]) == 60.0
    assert combined["chunk_ids"] == ["chunk-a", "chunk-b"]
    assert combined["source_refs"] == ["a.csv", "b.csv"]


def test_diagnostic_alignment_requires_every_concrete_overlapping_unit():
    short = SimpleNamespace(mode="short_study", measurement_units=[])
    mixed = [(SimpleNamespace(unit_id="phase-a"), True), (SimpleNamespace(unit_id="phase-b"), False)]
    assert _diagnostic_alignment_scope(short, mixed) == mixed
    assert not all(aligned for _timing, aligned in _diagnostic_alignment_scope(short, mixed))

    day = SimpleNamespace(mode="diagnostic_day", measurement_units=[
        SimpleNamespace(unit_id="day", kind="continuous_day"),
        SimpleNamespace(unit_id="chunk-1", kind="probe_chunk"),
    ])
    parent_and_chunk = [(SimpleNamespace(unit_id="day"), False),
        (SimpleNamespace(unit_id="chunk-1"), True)]
    scoped = _diagnostic_alignment_scope(day, parent_and_chunk)
    assert [timing.unit_id for timing, _aligned in scoped] == ["chunk-1"]
    assert all(aligned for _timing, aligned in scoped)


def test_cost_aggregate_is_recomputed_from_digest_checked_event_and_source(tmp_path):
    source = tmp_path / "billing.json"
    source.write_text('{"incurred_usd":"1.00"}\n', encoding="utf-8")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    payload = {"reservation_id": "r1", "provider": "aws", "source_path": str(source),
        "source_sha256": source_hash, "incurred_usd": "1.00", "actual": True,
        "billing_final": True, "billing_amount_kind": "cumulative", "occurred_at": "2026-10-24T00:00:00Z"}
    event_id = "event-1"
    event = {"digest": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(), "payload": payload}
    history = {"event_id": event_id, "source_sha256": source_hash, "amount_usd": "1.00",
        "actual": True, "billing_final": True, "amount_kind": "cumulative",
        "occurred_at": "2026-10-24T00:00:00Z"}
    record = {"plan": {"providers": ["aws"]}, "evidence": {"campaign_id": "c1"},
        "reserved_cost_usd": "10", "provider_billing": {"aws": {"history": [history],
            "actual_usd": "1.00", "estimate_usd": "0", "final": True, "event_id": event_id}}}
    ledger = {"events": {event_id: event}, "reservations": {"r1": record}}

    verified = _verified_provider_bill(ledger, tmp_path / "ledger.json", "r1", "aws", "c1")
    assert verified["source_verified"] is True
    assert verified["actual"] == 1.0 and verified["final"] is True

    record["provider_billing"]["aws"]["actual_usd"] = "999"
    mismatched_aggregate = _verified_provider_bill(ledger, tmp_path / "ledger.json", "r1", "aws", "c1")
    assert mismatched_aggregate["source_verified"] is False
    assert mismatched_aggregate["actual"] is None

    mismatched_campaign = _verified_provider_bill(ledger, tmp_path / "ledger.json", "r1", "aws", "other")
    assert mismatched_campaign["source_verified"] is False
    assert mismatched_campaign["actual"] is None


def test_shared_reservation_cost_rows_are_order_independent_and_conflicts_unknown():
    one = {"run_id": "run-b", "provider": "aws", "reservation_id": "shared-r", "campaign_id": "c1",
        "reservation": {"value": 10, "scope": "shared"}, "actual": {"value": 1},
        "estimated": {"unavailable_reason": "no estimate"}, "final": True, "source_verified": True,
        "source_refs": [{"event_id": "e1"}]}
    two = {**one, "run_id": "run-a"}
    forward = _deduplicate_cost_rows([one, two])
    reverse = _deduplicate_cost_rows([two, one])
    assert forward == reverse
    assert len(forward) == 1 and forward[0]["run_id"] == "run-a"
    assert forward[0]["run_ids"] == ["run-a", "run-b"]

    contradictory = {**two, "campaign_id": "other", "actual": {"unavailable_reason": "campaign mismatch"},
        "final": False, "source_verified": False, "source_refs": []}
    conflicted = _deduplicate_cost_rows([one, contradictory])[0]
    assert conflicted["source_verified"] is False
    assert conflicted["actual"].get("value") is None
    assert "conflicts" in conflicted["unavailable_reason"]
