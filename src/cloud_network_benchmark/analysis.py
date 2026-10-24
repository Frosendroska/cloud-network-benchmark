"""Read-only deterministic summaries of already materialized F01R evidence."""
from __future__ import annotations

import hashlib
import json
import importlib.metadata
import math
from bisect import bisect_right
from decimal import Decimal, InvalidOperation
from enum import Enum
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts.common import Evidence, GitSnapshot
from .contracts.validation import ValidationResult, ValidatorInput
from .native import receiver_statistics, rtt_statistics
from .validation import (_acquisition_windows, _artifact_path, _measured_samples, _union_intervals,
                         _parse_unit, _source_supported_windows, _validate_artifacts, evaluate_comparison,
                         validate_bundle)


@dataclass(frozen=True)
class AnalysisReport:
    payload: dict[str, Any]
    protected_paths: tuple[Path, ...]


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON evidence {path}: {exc}") from exc


def _cost_ledger(path: Path) -> dict[str, Any]:
    data = _json_file(path)
    required = {"schema_version", "scope_id", "revision", "funding_remaining", "reservations",
                "claims", "events", "cleanup_open", "cleanup_blocked", "source_history"}
    if not isinstance(data, dict) or not required <= set(data) or data["schema_version"] != 1:
        raise ValueError("cost snapshot is not a saved F11 schema_version 1 ledger")
    events = data.get("events")
    if not isinstance(events, dict):
        raise ValueError("cost snapshot events are malformed")
    for event_id, event in events.items():
        if not isinstance(event, dict) or set(event) != {"digest", "payload"} or not isinstance(event["payload"], dict):
            raise ValueError(f"cost snapshot event {event_id!r} is malformed")
        digest = hashlib.sha256(json.dumps(event["payload"], sort_keys=True).encode()).hexdigest()
        if digest != event["digest"]:
            raise ValueError(f"cost snapshot event digest mismatch: {event_id}")
    return data


def _verified_provider_bill(ledger: dict[str, Any], ledger_path: Path,
                            reservation_id: str, provider: str,
                            expected_campaign_id: str | None = None) -> dict[str, Any] | None:
    record = ledger.get("reservations", {}).get(reservation_id)
    if not isinstance(record, dict) or provider not in record.get("plan", {}).get("providers", []):
        return None
    reservation = record.get("evidence", {})
    if expected_campaign_id is not None and reservation.get("campaign_id") != expected_campaign_id:
        return {"reserved": None, "estimated": None, "actual": None, "final": False,
                "reservation_id": reservation_id, "source_verified": False, "source_refs": [],
                "unavailable_reason": "ledger reservation campaign does not match selected run"}
    bill = record.get("provider_billing", {}).get(provider)
    if not isinstance(bill, dict):
        return None
    history_items = bill.get("history", [])
    if not isinstance(history_items, list) or not history_items:
        return {"reserved": record.get("reserved_cost_usd"), "estimated": None, "actual": None,
                "final": False, "reservation_id": reservation_id, "source_verified": False,
                "source_refs": [], "unavailable_reason": "billing history is missing"}
    source_refs = []
    calculated = {"actual": Decimal("0"), "estimate": Decimal("0")}
    seen_amount = {"actual": False, "estimate": False}
    calculated_final = False
    final_event_id = None
    verified = True
    for history in history_items:
        event_id = history.get("event_id") if isinstance(history, dict) else None
        event = ledger.get("events", {}).get(event_id)
        if not event:
            verified = False
            break
        payload = event["payload"]
        source = Path(payload.get("source_path", ""))
        if not source.is_absolute():
            source = ledger_path.parent / source
        try:
            source_hash = _sha(source.resolve(strict=True))
        except (OSError, ValueError):
            verified = False
            break
        if not (payload.get("reservation_id") == reservation_id and payload.get("provider") == provider
                and payload.get("source_sha256") == history.get("source_sha256") == source_hash
                and payload.get("incurred_usd") == history.get("amount_usd")
                and payload.get("actual") == history.get("actual")
                and payload.get("billing_final") == history.get("billing_final")
                and payload.get("billing_amount_kind") == history.get("amount_kind")
                and payload.get("occurred_at") == history.get("occurred_at")):
            verified = False
            break
        try:
            amount = Decimal(str(payload["incurred_usd"]))
        except (KeyError, InvalidOperation, ValueError):
            verified = False
            break
        if not amount.is_finite() or amount < 0 or payload.get("billing_amount_kind") not in {"cumulative", "delta"}:
            verified = False
            break
        actual = payload.get("actual") is True
        is_final = payload.get("billing_final") is True
        if not isinstance(payload.get("actual"), bool) or not isinstance(payload.get("billing_final"), bool):
            verified = False
            break
        if calculated_final:
            # F11 freezes billing after a final event; later history cannot alter it.
            verified = False
            break
        bucket = "actual" if actual else "estimate"
        seen_amount[bucket] = True
        kind = payload["billing_amount_kind"]
        if is_final:
            if not actual:
                verified = False
                break
            calculated["actual"] = (amount if kind == "cumulative" else calculated["actual"] + amount)
            calculated_final = True
            final_event_id = event_id
        elif kind == "delta":
            calculated[bucket] += amount
        else:
            calculated[bucket] = max(calculated[bucket], amount)
        source_refs.append({"event_id": event_id, "path": str(source.resolve()), "sha256": source_hash})
    if bill.get("final") is not calculated_final or (calculated_final and bill.get("event_id") != final_event_id):
        verified = False
    if verified:
        try:
            aggregate_actual = Decimal(str(bill.get("actual_usd") or "0"))
            aggregate_estimate = Decimal(str(bill.get("estimate_usd") or "0"))
            verified = (aggregate_actual == calculated["actual"] and aggregate_estimate == calculated["estimate"])
        except (InvalidOperation, ValueError):
            verified = False
    return {"reserved": record.get("reserved_cost_usd"),
            "estimated": float(calculated["estimate"]) if verified and seen_amount["estimate"] else None,
            "actual": float(calculated["actual"]) if verified and (seen_amount["actual"] or calculated_final) else None,
            "final": calculated_final and verified,
            "reservation_id": reservation_id, "source_verified": verified,
            "source_refs": sorted(source_refs, key=lambda item: item["event_id"]),
            "unavailable_reason": None if verified else "billing aggregate, event history, campaign, or source bytes disagree"}


def _utc_window(raw: Any) -> dict[str, str] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != {"start", "end"}:
        raise ValueError("utc_window must contain exactly start and end")
    try:
        start = datetime.fromisoformat(raw["start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(raw["end"].replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("utc_window timestamps must be ISO-8601") from exc
    if start.tzinfo is None or start.utcoffset() is None or end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("utc_window timestamps must include a UTC offset")
    start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    if start >= end:
        raise ValueError("utc_window must be increasing")
    return {"start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z")}


def _read_pair(item: Any, base: Path) -> tuple[ValidatorInput, ValidationResult, list[dict[str, str]], list[Path]]:
    if not isinstance(item, dict) or set(item) != {"validator_input", "validation_result"}:
        raise ValueError("each runs item must contain validator_input and validation_result")
    source_rows: list[dict[str, str]] = []
    paths: list[Path] = []
    parsed: list[Any] = []
    for key, model in (("validator_input", ValidatorInput), ("validation_result", ValidationResult)):
        raw = item[key]
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"{key} must be a local path")
        path = Path(raw)
        if not path.is_absolute():
            path = base / path
        path = path.resolve(strict=True)
        if not path.is_file():
            raise ValueError(f"{key} is not a regular file: {path}")
        try:
            value = model.model_validate(_json_file(path))
        except Exception as exc:
            raise ValueError(f"invalid {key} at {path}: {exc}") from exc
        parsed.append(value)
        digest = _sha(path)
        source_rows.append({"kind": key, "path": str(path), "sha256": digest})
        paths.append(path)
    value, recorded = parsed
    if recorded.run_id != value.identity.run_id or recorded.mode != value.mode:
        raise ValueError("saved result run_id/mode must match ValidatorInput")
    metrics = recorded.metrics.value
    if metrics is not None:
        expected_identity = value.identity.model_dump(mode="json") | {
            "provider": str(value.provider), "scenario": str(value.scenario),
            "mode": str(value.mode), "context": value.context,
        }
        if metrics.get("identity") is not None and metrics.get("identity") != expected_identity:
            raise ValueError("saved result metrics identity/configuration does not match ValidatorInput")
    return value, recorded, source_rows, paths


def _scalar(evidence: Any) -> dict[str, Any]:
    return evidence.model_dump(mode="json") if hasattr(evidence, "model_dump") else {"value": evidence}


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower, upper = math.floor(position), math.ceil(position)
    return ordered[lower] + (position - lower) * (ordered[upper] - ordered[lower])


def _deployment_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        raise ValueError("deployment summary requires at least one point")
    ordered = sorted(values)
    q1, q3 = _quantile(ordered, .25), _quantile(ordered, .75)
    return {"n": len(ordered), "points": ordered, "median": _quantile(ordered, .5),
            "q1": q1, "q3": q3, "iqr": q3 - q1}


def _unique_deployment_attempts(rows: list[dict[str, Any]], value_of: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """Keep one canonical value per deployment and reject conflicting attempts."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        value = value_of(row)
        if value is not None:
            grouped.setdefault(str(row["deployment_id"]), []).append(row)
    kept, conflicts = [], []
    for deployment_id, attempts in sorted(grouped.items()):
        values = {value_of(item) for item in attempts}
        if len(values) != 1:
            conflicts.append(deployment_id)
            continue
        first = sorted(attempts, key=lambda item: item.get("run_id", ""))[0]
        kept.append({**first, "run_ids": sorted({item.get("run_id") for item in attempts if item.get("run_id")}),
            "source_refs": sorted({path for item in attempts for path in item.get("source_refs", [])}),
            "study_dates": sorted({item.get("study_date") for item in attempts if item.get("study_date")}),
            "study_weekdays": sorted({item.get("study_weekday") for item in attempts if item.get("study_weekday")})})
    return kept, conflicts


def _deduplicate_cost_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Represent a shared provider reservation once, independent of selection order."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row.get("reservation_id") or f"run:{row.get('run_id', '')}"),
               str(row.get("provider", "")))
        groups.setdefault(key, []).append(row)
    output = []
    compared = ("campaign_id", "reservation", "actual", "estimated", "final", "source_verified")
    for _key, members in sorted(groups.items()):
        members = sorted(members, key=lambda item: (str(item.get("run_id", "")),
            json.dumps(item, sort_keys=True, separators=(",", ":"))))
        first = members[0]
        run_ids = sorted({str(item["run_id"]) for item in members if item.get("run_id")})
        signatures = {tuple(json.dumps(item.get(field), sort_keys=True, separators=(",", ":"))
                            for field in compared) for item in members}
        refs = sorted({json.dumps(ref, sort_keys=True, separators=(",", ":"))
                       for item in members for ref in item.get("source_refs", [])})
        row = {**first, "run_id": run_ids[0] if run_ids else "", "run_ids": run_ids,
               "source_refs": [json.loads(ref) for ref in refs]}
        if len(signatures) > 1 and len(members) > 1:
            reason = "shared reservation evidence conflicts across selected runs"
            row.update({"reservation": {"unavailable_reason": reason},
                "actual": {"unavailable_reason": reason}, "estimated": {"unavailable_reason": reason},
                "final": False, "source_verified": False, "unavailable_reason": reason})
        output.append(row)
    return sorted(output, key=lambda row: (row.get("reservation_id") or "",
        row.get("provider", ""), row.get("run_id", "")))


def _containing_window(windows: list[tuple[float, float]], starts: list[float],
                       interval_start: float, interval_end: float) -> bool:
    """Return whether one preserved acquisition window fully contains an interval."""
    index = bisect_right(starts, interval_start) - 1
    return index >= 0 and interval_end <= windows[index][1]


def _diagnostic_alignment_scope(value: ValidatorInput,
                                overlapping: list[tuple[Any, bool]]) -> list[tuple[Any, bool]]:
    """Use the concrete probe chunk for DAY samples, not the enclosing day declaration."""
    if str(value.mode) != "diagnostic_day":
        return overlapping
    chunks = {unit.unit_id for unit in value.measurement_units if unit.kind == "probe_chunk"}
    return [item for item in overlapping if item[0].unit_id in chunks]


def _bin_width(mode: Any) -> int:
    return 60 if str(getattr(mode, "value", mode)) == "diagnostic_day" else 1


def _sample_bins(samples: list[tuple[float, Any]], width: int) -> list[tuple[int, list[tuple[float, Any]]]]:
    if width <= 0:
        raise ValueError("bin width must be positive")
    result: dict[int, list[tuple[float, Any]]] = {}
    for elapsed, sample in samples:
        result.setdefault(math.floor(elapsed / width), []).append((elapsed, sample))
    return sorted(result.items())


def _histogram_data(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"edges_us": [], "counts": [], "unavailable_reason": "no supported raw RTT samples"}
    low, high = min(values), max(values)
    if low == high:
        width = max(abs(low) * .02, 1.0)
        low, high = low - width / 2, high + width / 2
    edges = [low + (high - low) * index / 50 for index in range(51)]
    counts = [0] * 50
    for value in values:
        index = min(49, max(0, int((value - low) / (high - low) * 50)))
        counts[index] += 1
    return {"edges_us": edges, "counts": counts}


def _iperf_receiver_origin(tcp: Any, *, post_omit_origin: bool) -> tuple[float, float] | None:
    """Return the session interval UTC origin using F08's unit-specific reset rule."""
    if tcp is None or len(tcp.session_details) != 1:
        return None
    detail = tcp.session_details[0]
    start = detail.get("start") if isinstance(detail, dict) else None
    if not isinstance(start, dict):
        return None
    test = start.get("test_start", start)
    stamp = start.get("timestamp")
    if isinstance(stamp, dict):
        stamp = stamp.get("timesecs", stamp.get("time"))
    try:
        origin = float(stamp)
        omit = float(test.get("omit", 0)) if isinstance(test, dict) else 0.0
    except (TypeError, ValueError):
        return None
    if not math.isfinite(origin) or not math.isfinite(omit) or omit < 0:
        return None
    return origin + (omit if post_omit_origin else 0.0), omit


def _receiver_interval_bin(start: float, end: float, origin: float,
                           width: float, uncertainty: float) -> tuple[int | None, list[int]]:
    """Map a whole receiver interval to one bin, or identify every touched bin."""
    if end + uncertainty <= origin:
        return None, []
    first = max(0, math.floor((start - uncertainty - origin) / width))
    last = max(0, math.ceil((end + uncertainty - origin) / width) - 1)
    touched = list(range(first, last + 1))
    if len(touched) == 1:
        bin_start = origin + touched[0] * width
        bin_end = bin_start + width
        if start - uncertainty >= bin_start and end + uncertainty <= bin_end:
            return touched[0], touched
    return None, touched


def _receiver_bin_metrics(intervals: list[Any], *, straddled: bool,
                          unavailable_reason: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return primary metrics and independently visible whole-interval evidence."""
    whole = receiver_statistics(intervals)
    if straddled:
        reason = "receiver interval straddles a bin boundary"
        primary = {"received_bytes": Evidence[int](unavailable_reason=reason),
            "measured_seconds": Evidence[float](unavailable_reason=reason),
            "throughput_gbps": Evidence[float](unavailable_reason=reason)}
    elif not intervals:
        primary = {"received_bytes": Evidence[int](unavailable_reason=unavailable_reason),
            "measured_seconds": Evidence[float](unavailable_reason=unavailable_reason),
            "throughput_gbps": Evidence[float](unavailable_reason=unavailable_reason)}
    else:
        primary = whole
    return primary, whole


def _combine_day_minute_parts(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine chunk-native observations before calculating a canonical DAY minute."""
    chunk_windows = {}
    for part in parts:
        chunk_windows.update(part.get("chunk_windows", {}))
    return {"samples": [sample for part in parts for sample in part.get("samples", [])],
        "support": _union_intervals([interval for part in parts for interval in part.get("support", [])]),
        "receiver_intervals": [interval for part in parts for interval in part.get("receiver_intervals", [])],
        "receiver_straddles": any(part.get("receiver_straddles", False) for part in parts),
        "exclusions": [item for part in parts for item in part.get("exclusions", [])],
        "source_refs": sorted({path for part in parts for path in part.get("source_refs", [])}),
        "chunk_ids": sorted({chunk_id for part in parts for chunk_id in part.get("chunk_ids", [])}),
        "chunk_windows": chunk_windows}


def _timing_clock_error(timing: Any, tool: str) -> float | None:
    sources = timing.acquisition_sources.value or []
    roles = {str(getattr(source.producer, "value", source.producer)) for source in sources
             if source.tool == tool}
    if not roles:
        return None
    errors = []
    for role in roles:
        matching = [mapping for mapping in timing.clock_mappings
                    if str(getattr(mapping.vm_role, "value", mapping.vm_role)) == role
                    and mapping.estimated_error_ms.value is not None]
        if len(matching) != 1:
            return None
        errors.append(matching[0].estimated_error_ms.value / 1000)
    return max(errors) if errors else None


def _nonplacement_fingerprint(value: ValidatorInput) -> str | None:
    record = next((item for item in value.artifact_records if item.artifact_id == "resolved_snapshot"), None)
    if record is None:
        return None
    try:
        raw = _json_file(_artifact_path(value, record))
        if isinstance(raw, dict) and isinstance(raw.get("observations"), list):
            provider = str(getattr(value.provider, "value", value.provider))
            raw = next(item for item in raw["observations"] if item.get("provider") == provider)
        if not isinstance(raw, dict):
            return None
        provider_config = dict(raw.get("provider_config", {}))
        provider_config.pop("placement", None)
        selected = {key: raw.get(key) for key in ("mode", "context", "provider",
            "measurement_units", "measurement_direction", "benchmark", "options")}
        options = dict(selected.get("options") or {})
        labels = dict(options.get("labels") or {})
        labels.pop("scenario_id", None)
        options["labels"] = labels
        selected["options"] = options
        selected["provider_config"] = provider_config
        return hashlib.sha256(json.dumps(selected, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    except (OSError, ValueError, TypeError, StopIteration):
        return None


def _comparison_group_key(value: ValidatorInput) -> tuple[Any, ...]:
    schedule = value.schedule_entry.value
    return (value.identity.campaign_id, value.identity.config_id, value.identity.experiment_id,
        str(value.scenario), value.context, tuple(sorted(map(str, value.enabled_phases))),
        schedule.entry_id if schedule else None, schedule.source_sha256 if schedule else None,
        schedule.row_sha256 if schedule else None)


def _comparison_rows(inputs: list[ValidatorInput], results: list[ValidationResult]) -> tuple[list[dict[str, Any]], dict[str, ValidationResult]]:
    groups: dict[tuple[Any, ...], list[tuple[ValidatorInput, ValidationResult]]] = {}
    for value, result in zip(inputs, results):
        groups.setdefault(_comparison_group_key(value), []).append((value, result))
    rows: list[dict[str, Any]] = []
    updated: dict[str, ValidationResult] = {}
    expected = ["aws", "azure", "gcp"]
    for group in groups.values():
        group_inputs, group_results = [item[0] for item in group], [item[1] for item in group]
        evaluated = evaluate_comparison(group_inputs, group_results)
        updated.update(evaluated)
        first = group_inputs[0]
        entry = first.schedule_entry.value
        present = sorted(str(item.provider) for item in group_inputs)
        row_result = evaluated[first.identity.run_id]
        rows.append({"comparison_id": hashlib.sha256(json.dumps(_comparison_group_key(first), default=str,
            separators=(",", ":")).encode()).hexdigest(), "scenario": str(first.scenario),
            "slot_id": entry.entry_id if entry else None, "window_label": entry.window_label if entry else "unresolved",
            "expected_providers": expected, "present_providers": present,
            "missing_providers": sorted(set(expected) - set(present)),
            "eligibility": str(row_result.validation_status.comparison_eligibility),
            "reasons": list(row_result.validation_status.comparison_reasons),
            "run_ids": sorted(item.identity.run_id for item in group_inputs),
            "phase_ids": sorted({str(unit.phase) for item in group_inputs for unit in item.measurement_units})})
    return rows, updated


def _placement_rows(inputs: list[ValidatorInput], run_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_run = {item.identity.run_id: item for item in inputs}
    def placement(value: ValidatorInput) -> Any:
        record = next((item for item in value.artifact_records if item.artifact_id == "resolved_snapshot"), None)
        if record is None:
            return None
        try:
            raw = _json_file(_artifact_path(value, record))
            if isinstance(raw, dict) and isinstance(raw.get("observations"), list):
                provider = str(getattr(value.provider, "value", value.provider))
                raw = next(item for item in raw["observations"] if item.get("provider") == provider)
            return raw.get("provider_config", {}).get("placement") if isinstance(raw, dict) else None
        except (OSError, ValueError, TypeError, StopIteration):
            return None
    eligible = [row for row in run_rows if row["data_validity"] == "valid" and row["mode"] == "short_study"
        and row["context"] == "standard" and row["execution_state"] == "succeeded"
        and row["cleanup_state"] in {"succeeded", "not_required"}
        and row["original_unit"].get("coverage", {}).get("complete") is True]
    groups: dict[tuple[str, str, str, str], dict[str, list[dict[str, Any]]]] = {}
    for row in eligible:
        if row["scenario"] not in {"same_zone", "placement_optimization"}:
            continue
        value = by_run[row["run_id"]]
        schedule = value.schedule_entry.value
        label = schedule.window_label if schedule else "unresolved"
        fingerprint = _nonplacement_fingerprint(value)
        if fingerprint is None:
            continue
        groups.setdefault((row["provider"], row["phase"], label, fingerprint), {}).setdefault(row["scenario"], []).append(row)
    output = []
    for (provider, phase, label, _fingerprint), scenarios in sorted(groups.items()):
        s1, s3 = scenarios.get("same_zone", []), scenarios.get("placement_optimization", [])
        s1_runs = sorted({row["run_id"] for row in s1}); s3_runs = sorted({row["run_id"] for row in s3})
        for metric in ("median_rtt_us", "throughput_gbps"):
            def metric_value(row: dict[str, Any]) -> float | None:
                raw = row["rtt"].get("median_us", {}) if metric == "median_rtt_us" else row["receiver"].get("throughput_gbps", {})
                value = raw.value if hasattr(raw, "value") else raw.get("value") if isinstance(raw, dict) else None
                return float(value) if value is not None else None
            s1_unique, s1_conflicts = _unique_deployment_attempts(s1, metric_value)
            s3_unique, s3_conflicts = _unique_deployment_attempts(s3, metric_value)
            left = [metric_value(row) for row in s1_unique]
            right = [metric_value(row) for row in s3_unique]
            left_med = _quantile(left, .5) if left else None; right_med = _quantile(right, .5) if right else None
            difference = (right_med-left_med) if left_med is not None and right_med is not None and metric == "median_rtt_us" else None
            relative = (100*(right_med/left_med-1)) if left_med and right_med is not None and metric == "throughput_gbps" else None
            dates = {"S1": sorted({row.get("study_date", "unavailable") for row in s1_unique}),
                     "S3": sorted({row.get("study_date", "unavailable") for row in s3_unique})}
            same_week = bool(dates["S1"] and dates["S3"] and dates["S1"][0] != "unavailable"
                and dates["S3"][0] != "unavailable" and len({datetime.fromisoformat(day).isocalendar()[:2]
                for day in dates["S1"] + dates["S3"]}) == 1)
            unavailable = ("both independent scenario distributions are required" if not left or not right else
                "S1 throughput baseline is zero" if metric == "throughput_gbps" and left_med == 0 else None)
            output.append({"provider": provider, "phase": phase, "window_label": label,
                "comparison_id": hashlib.sha256(json.dumps([provider, phase, label, _fingerprint, metric],
                    separators=(",", ":")).encode()).hexdigest(),
                "metric": metric, "direction": "S3 minus S1" if metric == "median_rtt_us" else "100*(S3/S1-1)",
                "config_ids": {"S1": sorted({by_run[run_id].identity.config_id for run_id in s1_runs}),
                    "S3": sorted({by_run[run_id].identity.config_id for run_id in s3_runs})},
                "placement_settings": {"S1": sorted({json.dumps(placement(by_run[run_id]), sort_keys=True) for run_id in s1_runs}),
                    "S3": sorted({json.dumps(placement(by_run[run_id]), sort_keys=True) for run_id in s3_runs})},
                "baseline": {"scenario": "same_zone", "value": left_med}, "comparison": {"scenario": "placement_optimization", "value": right_med},
                "rtt_difference_us": {"value": difference} if difference is not None else {"unavailable_reason": unavailable or "not an RTT metric"},
                "throughput_change_percent": {"value": relative} if relative is not None else {"unavailable_reason": unavailable or "not a throughput metric"},
                "n_s1": len(s1_unique), "n_s3": len(s3_unique),
                "s1_run_ids": s1_runs, "s3_run_ids": s3_runs, "study_dates": dates,
                "source_refs": sorted({path for row in (*s1, *s3) for path in row["source_refs"]}),
                "same_utc_week": same_week, "same_week_deviation": None if same_week else "selected scenario dates are not all in the same UTC week",
                "eligible": bool(left and right and not (metric == "throughput_gbps" and left_med == 0)),
                "excluded_conflicting_deployment_ids": {"S1": s1_conflicts, "S3": s3_conflicts},
                "unavailable_reason": unavailable})
    return output


def _unit_rows(value: ValidatorInput, result: ValidationResult, recorded: ValidationResult,
               utc_window: dict[str, str] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    artifact_findings, records, _provenance = _validate_artifacts(value)
    acquisition = {}
    for timing in value.timings:
        windows, errors = _acquisition_windows(value, timing)
        acquisition[timing.unit_id] = (windows, errors)
    run_rows: list[dict[str, Any]] = []
    bins: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    day_bin_accumulators: dict[int, list[dict[str, Any]]] = {}
    for record in value.artifact_records:
        try:
            resolved = _artifact_path(value, record).resolve(strict=True)
        except (OSError, ValueError):
            resolved = None
        sources.append({"run_id": value.identity.run_id, "artifact_id": record.artifact_id,
            "kind": str(record.kind), "path": record.path,
            "resolved_path": str(resolved) if resolved else None,
            "unavailable_reason": None if resolved else "recorded artifact cannot be resolved to a local file",
            "sha256": record.sha256.value, "unit_id": record.unit_id,
            "producer": str(record.producer)})
    for artifact_id, path, digest in (
        ("config_snapshot", value.config_snapshot_path, value.config_sha256),
        ("resolved_snapshot", value.resolved_snapshot_path, value.resolved_sha256.value),
        ("schedule_snapshot", value.schedule_snapshot_path.value, value.schedule_sha256.value),
    ):
        if path and digest:
            resolved = Path(path)
            if not resolved.is_absolute():
                resolved = Path(value.child_result_root) / resolved
            sources.append({"run_id": value.identity.run_id, "artifact_id": artifact_id,
                "kind": "provenance_snapshot", "path": str(path),
                "resolved_path": str(resolved.resolve()), "sha256": digest, "unit_id": None})
    units_metrics = result.metrics.value.get("units", []) if result.metrics.value is not None else []
    metrics_by_unit = {item.get("unit_id"): item for item in units_metrics if isinstance(item, dict)}
    window_start = datetime.fromisoformat(utc_window["start"].replace("Z", "+00:00")).timestamp() if utc_window else None
    window_end = datetime.fromisoformat(utc_window["end"].replace("Z", "+00:00")).timestamp() if utc_window else None
    day_origin_timing = next((item for item in value.timings
        if item.unit_id in {unit.unit_id for unit in value.measurement_units if unit.kind == "continuous_day"}), None)
    for unit in value.measurement_units:
        sock, tcp = _parse_unit(value, unit, records)
        samples = _measured_samples(value, unit, sock, acquisition)
        timing = next((item for item in value.timings if item.unit_id == unit.unit_id), None)
        mapping = next((item for item in timing.clock_mappings if str(getattr(item.vm_role, "value", item.vm_role)) == "vm_a"
                        and item.estimated_error_ms.value is not None), None) if timing else None
        measured_start = timing.measured_started_at.value.timestamp() if timing and timing.measured_started_at.value else None
        origin_timing = day_origin_timing if str(value.mode) == "diagnostic_day" else timing
        axis_origin = origin_timing.measured_started_at.value.timestamp() if origin_timing and origin_timing.measured_started_at.value else None
        timed_samples = []
        for sample in samples:
            if mapping is None or axis_origin is None:
                continue
            sample_utc = mapping.utc_timestamp.timestamp() + sample.tx_seconds - mapping.monotonic_seconds
            if (window_start is None or sample_utc >= window_start) and (window_end is None or sample_utc < window_end):
                timed_samples.append((sample_utc - axis_origin, sample, sample_utc))
        selected_samples = tuple(sample for _, sample, _ in timed_samples)
        unit_id = unit.unit_id
        windows = acquisition.get(unit_id, ([], []))[0]
        receiver_windows = _union_intervals((left, right) for tool, left, right in windows if tool == "iperf3")
        receiver_window_starts = [left for left, _right in receiver_windows]
        receiver_origin = _iperf_receiver_origin(tcp, post_omit_origin=unit.kind == "continuous_day")
        receiver_error = _timing_clock_error(timing, "iperf3") if timing else None
        receiver_intervals = tuple(tcp.receiver_intervals) if tcp else ()
        selected_receiver = [] if utc_window is not None else list(receiver_intervals)
        mapped_receiver: list[tuple[float, float, Any]] = []
        receiver_reason = "native receiver session timestamp or bounded acquisition clock mapping is unavailable"
        if receiver_origin is not None and receiver_error is not None and receiver_windows:
            session_origin, _omit = receiver_origin
            uncertainty = receiver_error
            selected_receiver = []
            for interval in receiver_intervals:
                absolute_start = session_origin + interval.start_seconds
                absolute_end = session_origin + interval.end_seconds
                supported = _containing_window(receiver_windows, receiver_window_starts,
                    absolute_start - uncertainty, absolute_end + uncertainty)
                selected = ((window_start is None or absolute_start - uncertainty >= window_start)
                            and (window_end is None or absolute_end + uncertainty <= window_end))
                if supported and selected:
                    selected_receiver.append(interval)
                    if axis_origin is not None:
                        mapped_receiver.append((absolute_start, absolute_end, interval))
            receiver_reason = "no whole receiver intervals are supported inside the selected UTC acquisition window"
        if utc_window is not None and receiver_origin is None:
            tcp_summary = {"received_bytes": Evidence[int](unavailable_reason=receiver_reason),
                "measured_seconds": Evidence[float](unavailable_reason=receiver_reason),
                "throughput_gbps": Evidence[float](unavailable_reason=receiver_reason)}
        elif utc_window is not None and (receiver_error is None or not receiver_windows):
            tcp_summary = {"received_bytes": Evidence[int](unavailable_reason=receiver_reason),
                "measured_seconds": Evidence[float](unavailable_reason=receiver_reason),
                "throughput_gbps": Evidence[float](unavailable_reason=receiver_reason)}
        elif utc_window is not None:
            tcp_summary = receiver_statistics(selected_receiver)
        else:
            tcp_summary = receiver_statistics(receiver_intervals)
        schedule = value.schedule_entry.value
        study_start = timing.measured_started_at.value if timing else None
        study_date = study_start.date().isoformat() if study_start else None
        run_rows.append({
            "run_id": value.identity.run_id, "attempt_id": value.identity.run_id,
            "campaign_id": value.identity.campaign_id, "experiment_id": value.identity.experiment_id,
            "config_id": value.identity.config_id, "deployment_id": value.identity.deployment_id,
            "provider": str(value.provider), "scenario": str(value.scenario), "mode": str(value.mode),
            "context": value.context, "unit_id": unit_id, "kind": str(unit.kind),
            "phase": str(unit.phase), "streams": unit.streams, "study_start_utc": study_start.isoformat() if study_start else None,
            "study_date": study_date, "study_weekday": study_start.strftime("%A") if study_start else None,
            "schedule_entry_id": schedule.entry_id if schedule else None,
            "schedule_window_label": schedule.window_label if schedule else None,
            "schedule_source_path": schedule.source_path if schedule else None,
            "schedule_source_sha256": schedule.source_sha256 if schedule else None,
            "acquisition_chunk_start_utc": timing.measured_started_at.value.isoformat() if timing and timing.measured_started_at.value else None,
            "acquisition_chunk_end_utc": timing.measured_finished_at.value.isoformat() if timing and timing.measured_finished_at.value else None,
            "data_validity": str(result.validation_status.data_validity),
            "current_data_validity": str(result.validation_status.data_validity),
            "recorded_data_validity": str(recorded.validation_status.data_validity),
            "comparison_eligibility": str(result.validation_status.comparison_eligibility),
            "execution_state": str(value.execution_state), "cleanup_state": str(value.cleanup_state),
            "rtt": rtt_statistics([sample.rtt_us for sample in selected_samples]),
            "rtt_histogram": _histogram_data([sample.rtt_us for sample in selected_samples]),
            "receiver": tcp_summary,
            "metadata": [item.model_dump(mode="json") for item in value.vm_metadata_records],
            "original_unit": metrics_by_unit.get(unit_id, {}),
            "source_refs": sorted({sample.source.path for sample in selected_samples} |
                                   {interval.source.path for interval in (selected_receiver if utc_window else receiver_intervals)}),
        })
        width = _bin_width(value.mode)
        support = _source_supported_windows(unit, acquisition)
        if axis_origin is not None:
            support = [(max(left, axis_origin, window_start if window_start is not None else left),
                        min(right, window_end if window_end is not None else right)) for left, right in support
                       if min(right, window_end if window_end is not None else right) >
                       max(left, axis_origin, window_start if window_start is not None else left)]
        else:
            support = []
        indices: set[int] = set()
        for left, right in support:
            first = max(0, math.floor((left - axis_origin) / width))
            last = math.ceil((right - axis_origin) / width)
            indices.update(range(first, max(first, last)))
        sample_bins = dict(_sample_bins([(elapsed, sample) for elapsed, sample, _ in timed_samples], width))
        receiver_bins: dict[int, list[Any]] = {}
        receiver_bin_reasons: dict[int, set[str]] = {}
        receiver_bin_exclusions: dict[int, list[dict[str, Any]]] = {}
        receiver_bin_straddles: set[int] = set()
        if receiver_origin is not None and receiver_error is not None and receiver_windows and axis_origin is not None:
            for interval_start, interval_end, interval in mapped_receiver:
                index, touched = _receiver_interval_bin(interval_start, interval_end, axis_origin,
                    width, receiver_error)
                exclusion = {"artifact_id": interval.source.artifact_id, "path": interval.source.path,
                    "sha256": interval.source.sha256, "start_seconds": interval.start_seconds,
                    "end_seconds": interval.end_seconds, "bytes": interval.bytes,
                    "reason": "omitted warm-up interval" if interval.omitted else
                        "sender interval excluded" if interval.sender else
                        "receiver interval straddles a bin boundary"}
                if interval.omitted or interval.sender:
                    for affected in touched:
                        indices.add(affected)
                        receiver_bin_reasons.setdefault(affected, set()).add(exclusion["reason"])
                        receiver_bin_exclusions.setdefault(affected, []).append(exclusion)
                elif index is not None:
                    receiver_bins.setdefault(index, []).append(interval)
                    indices.add(index)
                else:
                    for affected in touched:
                        indices.add(affected)
                        receiver_bin_straddles.add(affected)
                        receiver_bin_reasons.setdefault(affected, set()).add(exclusion["reason"])
                        receiver_bin_exclusions.setdefault(affected, []).append(exclusion)
        for index in sorted(indices):
            start, end = index * width, (index + 1) * width
            bucket_samples = sample_bins.get(index, [])
            rtt = rtt_statistics([sample.rtt_us for _, sample in bucket_samples])
            overlaps = [(max(left, axis_origin + start), min(right, axis_origin + end))
                        for left, right in support
                        if min(right, axis_origin + end) > max(left, axis_origin + start)]
            supported_seconds = sum(right - left for left, right in overlaps)
            bucket_receiver = receiver_bins.get(index, [])
            receiver_metrics, whole_interval_metrics = _receiver_bin_metrics(bucket_receiver,
                straddled=index in receiver_bin_straddles,
                unavailable_reason="; ".join(sorted(receiver_bin_reasons.get(index, set()))) or receiver_reason)
            exclusions = sorted(receiver_bin_exclusions.get(index, []),
                key=lambda item: (item["path"], item["start_seconds"], item["end_seconds"], item["reason"]))
            source_paths = ({sample.source.path for _, sample in bucket_samples}
                | {item.source.path for item in bucket_receiver}
                | {item["path"] for item in exclusions})
            bin_row = {"run_id": value.identity.run_id, "deployment_id": value.identity.deployment_id,
                "campaign_id": value.identity.campaign_id, "experiment_id": value.identity.experiment_id,
                "config_id": value.identity.config_id, "attempt_id": value.identity.run_id,
                "unit_id": unit_id, "chunk_id": unit_id if unit.kind == "probe_chunk" else None,
                "chunk_ids": [unit_id] if unit.kind == "probe_chunk" else [],
                "mode": str(value.mode), "kind": str(unit.kind), "phase": str(unit.phase),
                "study_date": study_date, "study_weekday": study_start.strftime("%A") if study_start else None,
                "schedule_entry_id": schedule.entry_id if schedule else None,
                "schedule_window_label": schedule.window_label if schedule else None,
                "acquisition_chunk_start_utc": timing.measured_started_at.value.isoformat() if timing and timing.measured_started_at.value else None,
                "acquisition_chunk_end_utc": timing.measured_finished_at.value.isoformat() if timing and timing.measured_finished_at.value else None,
                "axis": "UTC-mapped seconds from continuous-day origin" if str(value.mode) == "diagnostic_day" else "UTC-mapped elapsed seconds from measured start",
                "start_seconds": start, "end_seconds": end, "nominal_seconds": width,
                "supported_seconds": supported_seconds,
                "coverage": "supported" if supported_seconds >= width else "partial or acquisition gap",
                "rtt": rtt, "receiver_bytes": receiver_metrics, "receiver": receiver_metrics,
                "receiver_whole_intervals": whole_interval_metrics, "receiver_exclusions": exclusions,
                "receiver_supported_seconds": receiver_metrics["measured_seconds"].value or 0,
                "receiver_whole_interval_seconds": whole_interval_metrics["measured_seconds"].value or 0,
                "source_refs": sorted(source_paths)}
            if str(value.mode) == "diagnostic_day":
                part = {"samples": [sample for _, sample in bucket_samples], "support": overlaps,
                    "receiver_intervals": bucket_receiver, "receiver_straddles": index in receiver_bin_straddles,
                    "exclusions": exclusions, "source_refs": sorted(source_paths),
                    "chunk_ids": [], "chunk_windows": {}}
                if unit.kind == "probe_chunk":
                    part["chunk_ids"].append(unit_id)
                    if timing and timing.measured_started_at.value and timing.measured_finished_at.value:
                        part["chunk_windows"][unit_id] = {
                            "start_utc": timing.measured_started_at.value.isoformat(),
                            "end_utc": timing.measured_finished_at.value.isoformat()}
                day_bin_accumulators.setdefault(index, []).append(part)
            else:
                bins.append(bin_row)
        for parsed in (sock, tcp):
            if parsed:
                for ref in (parsed.source, *(sample.source for sample in parsed.samples),
                            *(interval.source for interval in parsed.receiver_intervals)):
                    if ref:
                        sources.append({"run_id": value.identity.run_id, "artifact_id": ref.artifact_id,
                            "path": ref.path, "resolved_path": str((Path(value.child_result_root) / ref.path).resolve()),
                            "sha256": ref.sha256, "unit_id": ref.unit_id})
    if str(value.mode) == "diagnostic_day" and day_origin_timing is not None:
        day_start = day_origin_timing.measured_started_at.value
        day_finish = day_origin_timing.measured_finished_at.value
        if day_start is not None and day_finish is not None:
            duration = max(0.0, (day_finish - day_start).total_seconds())
            width = 60
            day_start_timestamp = day_start.timestamp()
            selected_start = max(day_start_timestamp, window_start if window_start is not None else day_start_timestamp)
            selected_end = min(day_start_timestamp + duration, window_end if window_end is not None else day_start_timestamp + duration)
            if selected_end > selected_start:
                first_index = max(0, math.floor((selected_start - day_start_timestamp) / width))
                end_index = min(math.ceil(duration / width), math.ceil((selected_end - day_start_timestamp) / width))
            else:
                first_index = end_index = 0
            parent_unit = next((unit for unit in value.measurement_units if unit.kind == "continuous_day"), None)
            schedule = value.schedule_entry.value
            for index in range(first_index, end_index):
                start = index * width
                end = min(start + width, duration)
                selected_start_seconds = max(start, selected_start - day_start_timestamp)
                selected_end_seconds = min(end, selected_end - day_start_timestamp)
                selected_seconds = max(0.0, selected_end_seconds - selected_start_seconds)
                parts = day_bin_accumulators.get(index, [])
                aggregate = _combine_day_minute_parts(parts)
                support = _union_intervals([(max(left, day_start_timestamp + start),
                    min(right, day_start_timestamp + end)) for left, right in aggregate["support"]
                    if min(right, day_start_timestamp + end) > max(left, day_start_timestamp + start)])
                supported_seconds = sum(right - left for left, right in support)
                receiver_intervals = aggregate["receiver_intervals"]
                receiver_metrics, whole_interval_metrics = _receiver_bin_metrics(receiver_intervals,
                    straddled=bool(parts and aggregate["receiver_straddles"]),
                    unavailable_reason="no whole source-supported receiver interval in this selected minute")
                exclusions = sorted({json.dumps(item, sort_keys=True, separators=(",", ":"))
                    for item in aggregate["exclusions"]})
                exclusions = [json.loads(item) for item in exclusions]
                source_paths = aggregate["source_refs"]
                chunk_ids = aggregate["chunk_ids"]
                if not parts:
                    kind = "acquisition_gap"
                    rtt = rtt_statistics([])
                    coverage = "unsupported selected acquisition interval"
                    supported_seconds = 0.0
                else:
                    kind = "continuous_day_minute"
                    rtt = rtt_statistics([sample.rtt_us for sample in aggregate["samples"]])
                    if selected_seconds < width:
                        coverage = "partial UTC selection or measured-day tail"
                    else:
                        coverage = "supported" if supported_seconds >= selected_seconds else "partial or acquisition gap"
                chunk_windows = [(chunk_id, aggregate["chunk_windows"].get(chunk_id))
                    for chunk_id in chunk_ids]
                bins.append({"run_id": value.identity.run_id, "attempt_id": value.identity.run_id,
                    "campaign_id": value.identity.campaign_id, "experiment_id": value.identity.experiment_id,
                    "config_id": value.identity.config_id, "deployment_id": value.identity.deployment_id,
                    "unit_id": parent_unit.unit_id if parent_unit else "continuous-day",
                    "chunk_id": chunk_ids[0] if len(chunk_ids) == 1 else None, "chunk_ids": chunk_ids,
                    "chunk_windows": chunk_windows, "mode": "diagnostic_day", "kind": kind,
                    "phase": str(parent_unit.phase) if parent_unit else "multi_flow",
                    "study_date": day_start.date().isoformat(), "study_weekday": day_start.strftime("%A"),
                    "schedule_entry_id": schedule.entry_id if schedule else None,
                    "schedule_window_label": schedule.window_label if schedule else None,
                    "acquisition_chunk_start_utc": None, "acquisition_chunk_end_utc": None,
                    "axis": "UTC-mapped seconds from continuous-day origin", "start_seconds": start,
                    "end_seconds": end, "nominal_seconds": width, "selected_seconds": selected_seconds,
                    "selection_start_seconds": selected_start_seconds, "selection_end_seconds": selected_end_seconds,
                    "supported_seconds": supported_seconds, "coverage": coverage,
                    "rtt": rtt, "receiver_bytes": receiver_metrics, "receiver": receiver_metrics,
                    "receiver_whole_intervals": whole_interval_metrics, "receiver_exclusions": exclusions,
                    "receiver_supported_seconds": receiver_metrics["measured_seconds"].value or 0,
                    "receiver_whole_interval_seconds": whole_interval_metrics["measured_seconds"].value or 0,
                    "source_refs": source_paths})
    return run_rows, bins, sources


def analyze_selection(selection_path: Path, *, analysis_git: GitSnapshot) -> AnalysisReport:
    """Parse the selected saved DTOs and revalidate them using F08 at saved time."""
    selection_path = Path(selection_path).resolve(strict=True)
    selection = _json_file(selection_path)
    if not isinstance(selection, dict) or set(selection) - {"format_version", "runs", "utc_window", "cost_snapshot"}:
        raise ValueError("selection contains unknown fields or is not an object")
    if selection.get("format_version") != 1 or isinstance(selection.get("format_version"), bool):
        raise ValueError("format_version must be 1")
    raw_runs = selection.get("runs")
    if not isinstance(raw_runs, list) or not raw_runs:
        raise ValueError("runs must be a nonempty list")
    window = _utc_window(selection.get("utc_window"))
    cost = selection.get("cost_snapshot")
    ledger = None
    if cost is not None:
        if not isinstance(cost, dict) or set(cost) != {"path", "sha256"}:
            raise ValueError("cost_snapshot must contain exactly path and sha256")
        if not isinstance(cost["sha256"], str) or len(cost["sha256"]) != 64 or any(c not in "0123456789abcdef" for c in cost["sha256"]):
            raise ValueError("cost_snapshot sha256 must be lowercase SHA-256")
        if not isinstance(cost["path"], str) or not cost["path"].strip():
            raise ValueError("cost_snapshot path must be a local file path")
        cost_path = Path(cost["path"])
        if not cost_path.is_absolute():
            cost_path = selection_path.parent / cost_path
        cost_path = cost_path.resolve(strict=True)
        if _sha(cost_path) != cost["sha256"]:
            raise ValueError("cost_snapshot digest does not match source bytes")
        ledger = _cost_ledger(cost_path)
    else:
        cost_path = None
    loaded = []
    protected = [selection_path]
    semantic = []
    for item in raw_runs:
        value, recorded, descriptor_sources, descriptor_paths = _read_pair(item, selection_path.parent)
        protected.extend(descriptor_paths)
        protected.append(Path(value.child_result_root).resolve())
        if cost_path:
            protected.append(cost_path)
        key = (value.identity.run_id, descriptor_sources)
        identity = {"run_id": value.identity.run_id, "mode": str(value.mode),
                    "sources": descriptor_sources}
        if any(existing[0].identity.run_id == value.identity.run_id for existing in loaded):
            previous = next(existing for existing in loaded if existing[0].identity.run_id == value.identity.run_id)
            if previous[3] != descriptor_sources:
                raise ValueError(f"conflicting duplicate run evidence: {value.identity.run_id}")
            continue
        loaded.append((value, recorded, descriptor_sources, descriptor_sources))
        semantic.append(identity)
    semantic.sort(key=lambda item: (item["run_id"], json.dumps(item["sources"], sort_keys=True)))
    fingerprint = hashlib.sha256(json.dumps({"format_version": 1, "runs": semantic, "utc_window": window,
        "cost_sha256": cost["sha256"] if cost else None}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    all_runs: list[dict[str, Any]] = []
    all_bins: list[dict[str, Any]] = []
    all_sources: list[dict[str, Any]] = []
    deployment_candidates: dict[tuple[str, str, str, str, str], dict[str, list[dict[str, Any]]]] = {}
    deployment_conflicts: list[dict[str, str]] = []
    costs = []
    reliability = []
    diagnostics = []
    current_results = []
    current_inputs = []
    saved_metric_checks: dict[str, dict[str, Any]] = {}
    if cost_path is not None:
        all_sources.append({"run_id": "", "artifact_id": "cost_snapshot", "kind": "cost_snapshot",
            "path": str(cost_path), "resolved_path": str(cost_path), "sha256": cost["sha256"], "unit_id": None})
    for value, recorded, descriptor_sources, _key in loaded:
        current = validate_bundle(value, evaluated_at=recorded.evaluated_at)
        recorded_metrics = recorded.metrics.value or {}
        current_metrics = current.metrics.value or {}
        differing_metric_fields = sorted(key for key in set(recorded_metrics) | set(current_metrics)
            if recorded_metrics.get(key) != current_metrics.get(key))
        saved_metric_checks[value.identity.run_id] = {"consistent": not differing_metric_fields,
            "differing_fields": differing_metric_fields,
            "unavailable_reason": None if not differing_metric_fields else
                "saved result metric projection differs from F08 replay at its recorded evaluation time"}
        current_inputs.append(value)
        current_results.append(current)
        run_rows, bins, native_sources = _unit_rows(value, current, recorded, window)
        for row in run_rows:
            row["saved_result_metrics_consistent"] = saved_metric_checks[value.identity.run_id]["consistent"]
            row["saved_result_differing_fields"] = saved_metric_checks[value.identity.run_id]["differing_fields"]
        all_runs.extend(run_rows)
        all_bins.extend(bins)
        all_sources.extend(descriptor_sources + native_sources)
        for row in run_rows:
            unit_metrics = row.get("original_unit", {})
            coverage = unit_metrics.get("coverage", {}) if isinstance(unit_metrics, dict) else {}
            eligible = (row["data_validity"] == "valid" and row["mode"] == "short_study"
                        and row["context"] == "standard" and coverage.get("complete") is True
                        and value.execution_state == "succeeded" and value.cleanup_state in {"succeeded", "not_required"})
            if not eligible:
                continue
            schedule = value.schedule_entry.value
            window_label = schedule.window_label if schedule else "unresolved"
            start_at = next((timing.measured_started_at.value for timing in value.timings
                             if timing.unit_id == row["unit_id"] and timing.measured_started_at.value), None)
            date_label = start_at.date().isoformat() if start_at else "unavailable"
            metrics = []
            rtt_median = row["rtt"].get("median_us", {})
            rtt_value = rtt_median.value if hasattr(rtt_median, "value") else rtt_median.get("value")
            if rtt_value is not None:
                metrics.append(("median_rtt_us", float(rtt_value)))
            throughput = row["receiver"].get("throughput_gbps")
            throughput_value = throughput.value if hasattr(throughput, "value") else (throughput or {}).get("value")
            if throughput_value is not None:
                metrics.append(("throughput_gbps", float(throughput_value)))
            for metric_name, metric_value in metrics:
                group = (row["provider"], row["scenario"], row["phase"], window_label, metric_name)
                deployment_candidates.setdefault(group, {}).setdefault(row["deployment_id"], []).append({
                    "run_id": row["run_id"], "deployment_id": row["deployment_id"], "value": metric_value,
                    "study_date": date_label, "study_weekday": row.get("study_weekday"),
                    "window_label": window_label, "source_refs": row["source_refs"]})
        reliability.append({"run_id": value.identity.run_id, "attempt_id": value.identity.run_id,
            "campaign_id": value.identity.campaign_id, "experiment_id": value.identity.experiment_id,
            "config_id": value.identity.config_id, "deployment_id": value.identity.deployment_id,
            "provider": str(value.provider),
            "scenario": str(value.scenario), "mode": str(value.mode), "attempts": 1,
            "recorded_data_validity": str(recorded.validation_status.data_validity),
            "current_data_validity": str(current.validation_status.data_validity),
            "saved_result_metrics_consistent": saved_metric_checks[value.identity.run_id]["consistent"],
            "saved_result_differing_fields": saved_metric_checks[value.identity.run_id]["differing_fields"],
            "failure_reason": list(current.validation_status.data_reasons),
            "missing_phases": sorted({str(phase) for phase in value.enabled_phases} -
                {str(row.get("phase")) for row in (current.metrics.value or {}).get("units", [])}),
            "replacement_of": value.identity.replaces_run_id,
            "attempt_status": ("success" if current.validation_status.data_validity == "valid"
                and value.execution_state == "succeeded" and value.cleanup_state in {"succeeded", "not_required"}
                else "partial" if current.validation_status.data_validity == "partial" else "failed_or_unavailable")})
        diagnostic_source = {record.artifact_id: {"path": record.path, "sha256": record.sha256.value}
            for record in value.artifact_records if record.artifact_id in {"vm_a_diagnostics", "vm_b_diagnostics"}}
        timing_alignments = []
        for timing in value.timings:
            errors = [mapping.estimated_error_ms.value for mapping in timing.clock_mappings
                      if mapping.estimated_error_ms.value is not None]
            roles = {str(getattr(mapping.vm_role, "value", mapping.vm_role)) for mapping in timing.clock_mappings
                     if mapping.estimated_error_ms.value is not None}
            timing_alignments.append(len(roles) == 2 and len(errors) == 2 and max(errors) <= 100)
        metric_fields = ("cpu_total_percent", "cpu_max_core_percent", "cpu_steal_percent",
            "tcp_retransmissions", "interface_rx_drops", "interface_tx_drops",
            "interface_rx_errors", "interface_tx_errors", "rx_bytes", "tx_bytes")
        for sample in value.diagnostics:
            role = str(getattr(sample.vm_role, "value", sample.vm_role))
            source_key = "vm_a_diagnostics" if role == "vm_a" else "vm_b_diagnostics"
            sample_start = sample.sampled_at.timestamp() - sample.interval_seconds
            sample_end = sample.sampled_at.timestamp()
            overlapping = []
            for timing, aligned in zip(value.timings, timing_alignments):
                left, right = timing.measured_started_at.value, timing.measured_finished_at.value
                if left is not None and right is not None and sample_start < right.timestamp() and sample_end > left.timestamp():
                    overlapping.append((timing, aligned))
            scoped = _diagnostic_alignment_scope(value, overlapping)
            alignment_eligible = bool(scoped) and all(item[1] for item in scoped)
            alignment_reason = (None if alignment_eligible else
                "no overlapping measurement unit has both VM clock mappings within 100 ms")
            diagnostics.append({"run_id": value.identity.run_id, "vm_role": role,
                "sampled_at": sample.sampled_at.isoformat().replace("+00:00", "Z"),
                "monotonic_seconds": sample.monotonic_seconds, "interval_seconds": sample.interval_seconds,
                "metrics": {name: getattr(sample, name).model_dump(mode="json") for name in metric_fields},
                "reset_fields": list(sample.reset_fields), "gap_reason": sample.gap_reason,
                "measurement_unit_ids": sorted({timing.unit_id for timing, _ in scoped}),
                "clock_mappings_within_100ms": alignment_eligible,
                "alignment_unavailable_reason": alignment_reason,
                "source": diagnostic_source.get(source_key, {"unavailable_reason": "diagnostic source artifact is not recorded"})})
        provider_name = str(getattr(value.provider, "value", value.provider))
        reservation = value.reservation.value
        reservation_id = reservation.reservation_id.value if reservation else None
        bill = (_verified_provider_bill(ledger, cost_path, reservation_id, provider_name,
                reservation.campaign_id if reservation else value.identity.campaign_id)
                if ledger is not None and reservation_id else None)
        if bill:
            costs.append({"run_id": value.identity.run_id, "provider": provider_name,
                "run_ids": [value.identity.run_id], "campaign_id": reservation.campaign_id if reservation else value.identity.campaign_id,
                "reservation_id": reservation_id, "reservation": {"value": bill["reserved"], "scope": "shared reservation; not allocated"},
                "actual": {"value": bill["actual"], "unavailable_reason": bill["unavailable_reason"]} if bill["actual"] is not None else {"unavailable_reason": bill["unavailable_reason"]},
                "estimated": {"value": bill["estimated"]} if bill["estimated"] is not None else {"unavailable_reason": bill["unavailable_reason"]},
                "final": bill["final"], "source_verified": bill["source_verified"],
                "source_refs": bill["source_refs"] + ([{"event_id": "cost_snapshot", "path": str(cost_path), "sha256": cost["sha256"]}] if cost_path else []),
                "runtime": {"unavailable_reason": "runtime not sourced"}, "transfer": {"unavailable_reason": "transfer not sourced"}})
        else:
            costs.append({"run_id": value.identity.run_id, "provider": provider_name,
                "run_ids": [value.identity.run_id], "campaign_id": reservation.campaign_id if reservation else value.identity.campaign_id,
                "reservation_id": reservation_id,
                "reservation": {"unavailable_reason": "no matching verified reservation evidence"},
                "actual": {"unavailable_reason": "no verified cost snapshot supplied"},
                "estimated": {"unavailable_reason": "no verified cost snapshot supplied"},
                "final": False, "runtime": {"unavailable_reason": "runtime not sourced"},
                "transfer": {"unavailable_reason": "transfer not sourced"},
                "source_refs": []})
    costs = _deduplicate_cost_rows(costs)
    comparison_rows, comparison_results = _comparison_rows(current_inputs, current_results) if current_inputs else ([], {})
    for row in all_runs:
        current = comparison_results.get(row["run_id"])
        if current:
            row["comparison_eligibility"] = str(current.validation_status.comparison_eligibility)
    deployments: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
    for group, by_deployment in sorted(deployment_candidates.items()):
        metric_name = group[-1]
        flat = [item for attempts in by_deployment.values() for item in attempts]
        selected, conflicts = _unique_deployment_attempts(flat, lambda row: row["value"])
        for deployment_id in conflicts:
            attempts = by_deployment[deployment_id]
            deployment_conflicts.append({"deployment_id": deployment_id, "metric": metric_name,
                "run_ids": json.dumps(sorted({item["run_id"] for item in attempts})),
                "reason": "conflicting accepted attempt values; deployment excluded from this metric summary"})
        for item in selected:
            deployments.setdefault(group, []).append({**item,
                "study_date": item.get("study_dates", [None])[0] if item.get("study_dates") else None,
                "study_weekday": item.get("study_weekdays", [None])[0] if item.get("study_weekdays") else None})
    summary_rows = []
    for (provider, scenario, phase, window_label, metric), values in sorted(deployments.items()):
        values = sorted(values, key=lambda item: (item["deployment_id"], item["value"]))
        points = [item["value"] for item in values]
        summary_rows.append({"provider": provider, "scenario": scenario, "phase": phase,
            "window_label": window_label, "metric": metric,
            "deployment_ids": sorted({item["deployment_id"] for item in values}),
            "run_ids": sorted({run_id for item in values for run_id in item["run_ids"]}),
            "source_refs": sorted({path for item in values for path in item["source_refs"]}),
            "study_dates": sorted({day for item in values for day in item["study_dates"]}),
            "study_weekdays": sorted({item["study_weekday"] for item in values if item["study_weekday"]}),
            "points": points, **_deployment_summary(points)})
    deployment_time_groups = []
    for (provider, scenario, phase, window_label, metric), values in sorted(deployments.items()):
        for basis, key_name in (("date", "study_date"), ("weekday", "study_weekday")):
            groups_by_time: dict[str, dict[str, list[dict[str, Any]]]] = {}
            for item in values:
                time_key = item.get(key_name)
                if time_key:
                    groups_by_time.setdefault(time_key, {}).setdefault(item["deployment_id"], []).append(item)
            for time_key, deployments_on_date in sorted(groups_by_time.items()):
                points = []
                for deployment_id, attempts in sorted(deployments_on_date.items()):
                    vals = {attempt["value"] for attempt in attempts}
                    if len(vals) == 1:
                        points.append(next(iter(vals)))
                if points:
                    deployment_time_groups.append({"provider": provider, "scenario": scenario,
                        "phase": phase, "window_label": window_label, "metric": metric,
                        "date_basis": basis, "date_or_weekday": time_key, **_deployment_summary(points)})
    placement_rows = _placement_rows(current_inputs, all_runs)
    day_context = []
    for run_id in sorted({row["run_id"] for row in all_runs if row["mode"] == "diagnostic_day"}):
        run_bins = [row for row in all_bins if row["run_id"] == run_id and row.get("mode") == "diagnostic_day"
            and row.get("rtt", {}).get("valid_replies", 0)]
        initial = [row["rtt"]["median_us"].value for row in run_bins if row["start_seconds"] < 300
            and row["rtt"]["median_us"].value is not None]
        later = [row["rtt"]["median_us"].value for row in run_bins if row["start_seconds"] >= 300
            and row["rtt"]["median_us"].value is not None]
        day_context.append({"run_id": run_id, "scope": "descriptive RTT medians during sustained TCP load",
            "initial_300_seconds": _deployment_summary(initial) if initial else {"unavailable_reason": "no supported RTT bins in initial 300 seconds"},
            "subsequent_supported_bins": _deployment_summary(later) if later else {"unavailable_reason": "no later supported RTT bins"},
            "idle_baseline": {"unavailable_reason": "diagnostic-day mode has no idle baseline"},
            "causal_interpretation": {"unavailable_reason": "descriptive association only"}})
    try:
        mpl_version = importlib.metadata.version("matplotlib")
    except importlib.metadata.PackageNotFoundError:
        mpl_version = None
    try:
        numpy_version = importlib.metadata.version("numpy")
    except importlib.metadata.PackageNotFoundError:
        numpy_version = None
    try:
        from matplotlib import get_backend
        backend = get_backend()
    except ImportError:
        backend = None
    metadata = {"format_version": 1, "selection_fingerprint": fingerprint,
        "analysis_git": analysis_git.model_dump(mode="json"),
        "renderer": {"name": "matplotlib", "version": mpl_version, "backend": backend,
            "unavailable_reason": None if mpl_version else "optional Matplotlib dependency is not installed"},
        "numeric_runtime": {"python": __import__("platform").python_version(), "numpy": numpy_version}, "utc_window": window}
    payload = {**metadata, "sources": sorted(all_sources, key=lambda x: (x.get("run_id", ""), x.get("path", ""), x.get("artifact_id", ""))),
        "runs": sorted(all_runs, key=lambda x: (x["run_id"], x["unit_id"])),
        "bins": sorted(all_bins, key=lambda x: (x["run_id"], x["unit_id"], x["start_seconds"])),
        "deployments": summary_rows, "comparisons": sorted(comparison_rows, key=lambda row: row["comparison_id"]),
        "placement_comparisons": sorted(placement_rows, key=lambda row: (row["provider"], row["phase"], row["window_label"], row["metric"])),
        "diagnostics": sorted(diagnostics, key=lambda row: (row["run_id"], row["vm_role"], row["sampled_at"])),
        "day_load_context": day_context,
        "reliability_summary": {"selected_unique_attempts": len(reliability),
            "successes": sum(row["attempt_status"] == "success" for row in reliability),
            "partial": sum(row["attempt_status"] == "partial" for row in reliability),
            "failed_or_unavailable": sum(row["attempt_status"] == "failed_or_unavailable" for row in reliability),
            "replacement_links": sum(row.get("replacement_of") is not None for row in reliability)},
        "reliability": sorted(reliability, key=lambda row: row["run_id"]), "costs": sorted(costs, key=lambda row: row.get("reservation_id") or row.get("run_id", "")),
        "deployment_time_groups": deployment_time_groups,
        "findings": sorted([{"run_id": result.run_id, "data_validity": str(result.validation_status.data_validity),
            "data_reasons": list(result.validation_status.data_reasons),
            "comparison_eligibility": str(result.validation_status.comparison_eligibility),
            "comparison_reasons": list(result.validation_status.comparison_reasons),
            "saved_result_metrics_consistent": saved_metric_checks[result.run_id]["consistent"],
            "saved_result_differing_fields": saved_metric_checks[result.run_id]["differing_fields"],
            "finding_codes": sorted({finding.code for finding in result.findings})} for result in current_results], key=lambda row: row["run_id"]),
        "cohort_findings": sorted(deployment_conflicts, key=lambda row: (row["deployment_id"], row["metric"]))}
    return AnalysisReport(payload=_jsonable(payload), protected_paths=tuple(dict.fromkeys(protected)))
