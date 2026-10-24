from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

from cloud_network_benchmark.analysis import (
    _containing_window,
    _diagnostic_alignment_scope,
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
