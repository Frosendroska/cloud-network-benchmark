"""Read-only deterministic summaries of already materialized F01R evidence."""
from __future__ import annotations

import hashlib
import json
import importlib.metadata
import math
from enum import Enum
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts.common import GitSnapshot
from .contracts.validation import ValidationResult, ValidatorInput
from .native import receiver_statistics, rtt_statistics
from .validation import (_acquisition_windows, _artifact_path, _measured_samples,
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
                            reservation_id: str, provider: str) -> dict[str, Any] | None:
    record = ledger.get("reservations", {}).get(reservation_id)
    if not isinstance(record, dict) or provider not in record.get("plan", {}).get("providers", []):
        return None
    bill = record.get("provider_billing", {}).get(provider)
    if not isinstance(bill, dict):
        return None
    verified_event_ids = set()
    source_refs = []
    for history in bill.get("history", []):
        event_id = history.get("event_id") if isinstance(history, dict) else None
        event = ledger.get("events", {}).get(event_id)
        if not event:
            continue
        payload = event["payload"]
        source = Path(payload.get("source_path", ""))
        if not source.is_absolute():
            source = ledger_path.parent / source
        try:
            source_hash = _sha(source.resolve(strict=True))
        except (OSError, ValueError):
            continue
        if (payload.get("reservation_id") == reservation_id and payload.get("provider") == provider
                and payload.get("source_sha256") == history.get("source_sha256") == source_hash):
            verified_event_ids.add(event_id)
            source_refs.append({"event_id": event_id, "path": str(source.resolve()), "sha256": source_hash})
    verified = bool(bill.get("history")) and all(
        isinstance(item, dict) and item.get("event_id") in verified_event_ids for item in bill.get("history", []))
    return {"reserved": record.get("reserved_cost_usd"),
            "estimated": bill.get("estimate_usd") if verified else None,
            "actual": bill.get("actual_usd") if verified else None,
            "final": bool(bill.get("final")) and verified,
            "reservation_id": reservation_id, "source_verified": verified,
            "source_refs": sorted(source_refs, key=lambda item: item["event_id"]),
            "unavailable_reason": None if verified else "billing event source bytes were unavailable or did not match recorded hashes"}


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
            left = [metric_value(row) for row in s1]; right = [metric_value(row) for row in s3]
            left = [value for value in left if value is not None]; right = [value for value in right if value is not None]
            left_med = _quantile(left, .5) if left else None; right_med = _quantile(right, .5) if right else None
            difference = (right_med-left_med) if left_med is not None and right_med is not None and metric == "median_rtt_us" else None
            relative = (100*(right_med/left_med-1)) if left_med and right_med is not None and metric == "throughput_gbps" else None
            dates = {"S1": sorted({row.get("study_date", "unavailable") for row in s1}),
                     "S3": sorted({row.get("study_date", "unavailable") for row in s3})}
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
                "n_s1": len({row["deployment_id"] for row in s1}), "n_s3": len({row["deployment_id"] for row in s3}),
                "s1_run_ids": s1_runs, "s3_run_ids": s3_runs, "study_dates": dates,
                "source_refs": sorted({path for row in (*s1, *s3) for path in row["source_refs"]}),
                "same_utc_week": same_week, "same_week_deviation": None if same_week else "selected scenario dates are not all in the same UTC week",
                "eligible": bool(left and right and not (metric == "throughput_gbps" and left_med == 0)),
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
    units_metrics = result.metrics.value.get("units", []) if result.metrics.value is not None else []
    metrics_by_unit = {item.get("unit_id"): item for item in units_metrics if isinstance(item, dict)}
    for unit in value.measurement_units:
        sock, tcp = _parse_unit(value, unit, records)
        samples = _measured_samples(value, unit, sock, acquisition)
        timing = next((item for item in value.timings if item.unit_id == unit.unit_id), None)
        mapping = next((item for item in timing.clock_mappings if str(getattr(item.vm_role, "value", item.vm_role)) == "vm_a"
                        and item.estimated_error_ms.value is not None), None) if timing else None
        measured_start = timing.measured_started_at.value.timestamp() if timing and timing.measured_started_at.value else None
        window_start = datetime.fromisoformat(utc_window["start"].replace("Z", "+00:00")).timestamp() if utc_window else None
        window_end = datetime.fromisoformat(utc_window["end"].replace("Z", "+00:00")).timestamp() if utc_window else None
        timed_samples = []
        for sample in samples:
            if mapping is None or measured_start is None:
                continue
            sample_utc = mapping.utc_timestamp.timestamp() + sample.tx_seconds - mapping.monotonic_seconds
            if (window_start is None or sample_utc >= window_start) and (window_end is None or sample_utc < window_end):
                timed_samples.append((sample_utc - measured_start, sample))
        selected_samples = tuple(sample for _, sample in timed_samples)
        tcp_intervals = tuple(tcp.receiver_intervals) if tcp else ()
        tcp_summary = receiver_statistics(tcp_intervals)
        unit_id = unit.unit_id
        run_rows.append({
            "run_id": value.identity.run_id, "deployment_id": value.identity.deployment_id,
            "provider": str(value.provider), "scenario": str(value.scenario), "mode": str(value.mode),
            "context": value.context, "unit_id": unit_id, "kind": str(unit.kind),
            "phase": str(unit.phase), "streams": unit.streams,
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
                                   {interval.source.path for interval in tcp_intervals}),
        })
        width = _bin_width(value.mode)
        support = _source_supported_windows(unit, acquisition)
        if mapping and measured_start is not None:
            support = [(max(left, measured_start, window_start or left),
                        min(right, window_end or right)) for left, right in support
                       if min(right, window_end or right) > max(left, measured_start, window_start or left)]
        indices: set[int] = set()
        for left, right in support:
            first = max(0, math.floor((left - measured_start) / width)) if measured_start is not None else 0
            last = math.ceil((right - measured_start) / width) if measured_start is not None else first
            indices.update(range(first, max(first, last)))
        sample_bins = dict(_sample_bins(timed_samples, width))
        for index in sorted(indices):
            start, end = index * width, (index + 1) * width
            bucket_samples = sample_bins.get(index, [])
            rtt = rtt_statistics([sample.rtt_us for _, sample in bucket_samples])
            overlaps = [(max(left, measured_start + start), min(right, measured_start + end))
                        for left, right in support if measured_start is not None
                        and min(right, measured_start + end) > max(left, measured_start + start)]
            supported_seconds = sum(right - left for left, right in overlaps)
            source_paths = {sample.source.path for _, sample in bucket_samples}
            bins.append({"run_id": value.identity.run_id, "deployment_id": value.identity.deployment_id,
                "unit_id": unit_id, "mode": str(value.mode), "kind": str(unit.kind),
                "axis": "utc-mapped elapsed seconds from measured start", "start_seconds": start,
                "end_seconds": end, "nominal_seconds": width, "supported_seconds": supported_seconds,
                "coverage": "supported" if supported_seconds >= width else "partial or acquisition gap",
                "rtt": rtt, "receiver_bytes": {"unavailable_reason": "receiver intervals are not mapped to per-bin UTC bounds"},
                "source_refs": sorted(source_paths)})
        for parsed in (sock, tcp):
            if parsed:
                for ref in (parsed.source, *(sample.source for sample in parsed.samples),
                            *(interval.source for interval in parsed.receiver_intervals)):
                    if ref:
                        sources.append({"run_id": value.identity.run_id, "artifact_id": ref.artifact_id,
                            "path": ref.path, "resolved_path": str((Path(value.child_result_root) / ref.path).resolve()),
                            "sha256": ref.sha256, "unit_id": ref.unit_id})
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
    deployments: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
    deployment_points_seen: set[tuple[str, str, str, str, str, str]] = set()
    deployment_point_values: dict[tuple[str, str, str, str, str, str], float] = {}
    deployment_conflicts: list[dict[str, str]] = []
    costs = []
    reliability = []
    diagnostics = []
    current_results = []
    current_inputs = []
    for value, recorded, descriptor_sources, _key in loaded:
        current = validate_bundle(value, evaluated_at=recorded.evaluated_at)
        current_inputs.append(value)
        current_results.append(current)
        run_rows, bins, native_sources = _unit_rows(value, current, recorded, window)
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
                point_key = (*group, row["deployment_id"])
                if point_key not in deployment_points_seen:
                    deployment_points_seen.add(point_key)
                    deployment_point_values[point_key] = metric_value
                    deployments.setdefault(group, []).append({"run_id": row["run_id"],
                        "deployment_id": row["deployment_id"], "value": metric_value,
                        "study_date": date_label, "window_label": window_label,
                        "source_refs": row["source_refs"]})
                elif deployment_point_values[point_key] != metric_value:
                    deployment_conflicts.append({"deployment_id": row["deployment_id"], "metric": metric_name,
                        "reason": "selected attempts for one deployment disagree; counted once as an independent deployment"})
        reliability.append({"run_id": value.identity.run_id, "provider": str(value.provider),
            "scenario": str(value.scenario), "mode": str(value.mode), "attempts": 1,
            "recorded_data_validity": str(recorded.validation_status.data_validity),
            "current_data_validity": str(current.validation_status.data_validity),
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
        alignment_eligible = any(timing_alignments)
        metric_fields = ("cpu_total_percent", "cpu_max_core_percent", "cpu_steal_percent",
            "tcp_retransmissions", "interface_rx_drops", "interface_tx_drops",
            "interface_rx_errors", "interface_tx_errors", "rx_bytes", "tx_bytes")
        for sample in value.diagnostics:
            role = str(getattr(sample.vm_role, "value", sample.vm_role))
            source_key = "vm_a_diagnostics" if role == "vm_a" else "vm_b_diagnostics"
            diagnostics.append({"run_id": value.identity.run_id, "vm_role": role,
                "sampled_at": sample.sampled_at.isoformat().replace("+00:00", "Z"),
                "monotonic_seconds": sample.monotonic_seconds, "interval_seconds": sample.interval_seconds,
                "metrics": {name: getattr(sample, name).model_dump(mode="json") for name in metric_fields},
                "reset_fields": list(sample.reset_fields), "gap_reason": sample.gap_reason,
                "clock_mappings_within_100ms": alignment_eligible,
                "source": diagnostic_source.get(source_key, {"unavailable_reason": "diagnostic source artifact is not recorded"})})
        provider_name = str(getattr(value.provider, "value", value.provider))
        reservation = value.reservation.value
        reservation_id = reservation.reservation_id.value if reservation else None
        bill = (_verified_provider_bill(ledger, cost_path, reservation_id, provider_name)
                if ledger is not None and reservation_id else None)
        if bill:
            costs.append({"run_id": value.identity.run_id, "provider": provider_name,
                "reservation_id": reservation_id, "reservation": {"value": bill["reserved"], "scope": "shared reservation; not allocated"},
                "actual": {"value": bill["actual"], "unavailable_reason": bill["unavailable_reason"]} if bill["actual"] is not None else {"unavailable_reason": bill["unavailable_reason"]},
                "estimated": {"value": bill["estimated"]} if bill["estimated"] is not None else {"unavailable_reason": bill["unavailable_reason"]},
                "final": bill["final"], "source_verified": bill["source_verified"],
                "source_refs": bill["source_refs"],
                "runtime": {"unavailable_reason": "runtime not sourced"}, "transfer": {"unavailable_reason": "transfer not sourced"}})
        else:
            costs.append({"run_id": value.identity.run_id, "provider": provider_name,
                "reservation": {"unavailable_reason": "no matching verified reservation evidence"},
                "actual": {"unavailable_reason": "no verified cost snapshot supplied"},
                "estimated": {"unavailable_reason": "no verified cost snapshot supplied"},
                "final": False, "runtime": {"unavailable_reason": "runtime not sourced"},
                "transfer": {"unavailable_reason": "transfer not sourced"},
                "source_refs": []})
    comparison_rows, comparison_results = _comparison_rows(current_inputs, current_results) if current_inputs else ([], {})
    for row in all_runs:
        current = comparison_results.get(row["run_id"])
        if current:
            row["comparison_eligibility"] = str(current.validation_status.comparison_eligibility)
    summary_rows = []
    for (provider, scenario, phase, window_label, metric), values in sorted(deployments.items()):
        points = [item["value"] for item in values]
        summary_rows.append({"provider": provider, "scenario": scenario, "phase": phase,
            "window_label": window_label, "metric": metric,
            "deployment_ids": sorted({item["deployment_id"] for item in values}),
            "run_ids": sorted({item["run_id"] for item in values}),
            "source_refs": sorted({path for item in values for path in item["source_refs"]}),
            "study_dates": sorted({item["study_date"] for item in values}),
            "points": points, **_deployment_summary(points)})
    placement_rows = _placement_rows(current_inputs, all_runs)
    day_context = []
    for run_id in sorted({row["run_id"] for row in all_runs if row["mode"] == "diagnostic_day"}):
        run_bins = [row for row in all_bins if row["run_id"] == run_id and row.get("mode") == "diagnostic_day"
            and row.get("kind") == "continuous_day"]
        initial = [row["rtt"]["median_us"]["value"] for row in run_bins if row["start_seconds"] < 300
            and row["rtt"].get("median_us", {}).get("value") is not None]
        later = [row["rtt"]["median_us"]["value"] for row in run_bins if row["start_seconds"] >= 300
            and row["rtt"].get("median_us", {}).get("value") is not None]
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
        "reliability": sorted(reliability, key=lambda row: row["run_id"]), "costs": sorted(costs, key=lambda row: row["run_id"]),
        "findings": sorted([{"run_id": result.run_id, "data_validity": str(result.validation_status.data_validity),
            "data_reasons": list(result.validation_status.data_reasons),
            "comparison_eligibility": str(result.validation_status.comparison_eligibility),
            "comparison_reasons": list(result.validation_status.comparison_reasons),
            "finding_codes": sorted({finding.code for finding in result.findings})} for result in current_results], key=lambda row: row["run_id"]),
        "cohort_findings": sorted(deployment_conflicts, key=lambda row: (row["deployment_id"], row["metric"]))}
    return AnalysisReport(payload=_jsonable(payload), protected_paths=tuple(dict.fromkeys(protected)))
