"""Small parsers for the supported Sockperf and iperf3 native dialects."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

from .contracts.common import Evidence
from .contracts.validation import ValidationFinding


@dataclass(frozen=True)
class SourceRef:
    artifact_id: str
    path: str
    sha256: str
    unit_id: Optional[str] = None
    deployment_id: Optional[str] = None
    producer: Optional[str] = None
    binary_version: Optional[str] = None
    source_identity: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.artifact_id or not self.path or not re.fullmatch(r"[a-f0-9]{64}", self.sha256):
            raise ValueError("native source requires identity, path and observed lowercase SHA-256")


@dataclass(frozen=True)
class RttSample:
    packet_index: int
    tx_seconds: float
    rx_seconds: float
    rtt_us: float
    source: SourceRef
    line: int
    session_id: str = "1"

    def __post_init__(self) -> None:
        if isinstance(self.packet_index, bool) or not isinstance(self.packet_index, int) or self.packet_index < 0:
            raise ValueError("packet index must be a nonnegative integer")
        if not self.session_id or isinstance(self.line, bool) or self.line < 1:
            raise ValueError("sample requires a session and one-based source line")
        if any(not math.isfinite(value) or value < 0 for value in (self.tx_seconds, self.rx_seconds, self.rtt_us)):
            raise ValueError("RTT sample values must be finite and nonnegative")
        if self.rx_seconds < self.tx_seconds or abs((self.rx_seconds - self.tx_seconds) * 1_000_000 - self.rtt_us) > .003:
            raise ValueError("RTT sample timing is reversed or inconsistent")


@dataclass(frozen=True)
class ProbeCounts:
    sent: Evidence[int]
    received: Evidence[int]
    dropped: Evidence[int]
    duplicated: Evidence[int]
    out_of_order: Evidence[int]
    skipped: Evidence[int]
    runtime_seconds: Evidence[float]
    reporting_scope: str
    source: SourceRef

    def __post_init__(self) -> None:
        for name in ("sent", "received", "dropped", "duplicated", "out_of_order", "skipped"):
            item = getattr(self, name)
            if item.value is not None and (isinstance(item.value, bool) or not isinstance(item.value, int) or item.value < 0):
                raise ValueError(f"{name} must be a nonnegative integer")
        if self.runtime_seconds.value is not None and (
            not math.isfinite(self.runtime_seconds.value) or self.runtime_seconds.value <= 0
        ):
            raise ValueError("runtime_seconds must be finite and positive")
        if not self.reporting_scope.strip():
            raise ValueError("count reporting scope is required")


@dataclass(frozen=True)
class ReceiverInterval:
    session_id: str
    start_seconds: float
    end_seconds: float
    seconds: float
    bytes: int
    omitted: bool
    sender: bool
    source: SourceRef
    line: int

    def __post_init__(self) -> None:
        if not self.session_id or isinstance(self.line, bool) or self.line < 1:
            raise ValueError("receiver interval requires a session and one-based source line")
        if any(not math.isfinite(value) or value < 0 for value in (self.start_seconds, self.end_seconds, self.seconds)):
            raise ValueError("receiver interval times must be finite and nonnegative")
        if self.end_seconds <= self.start_seconds or self.seconds <= 0:
            raise ValueError("receiver interval must have positive elapsed bounds")
        if isinstance(self.bytes, bool) or not isinstance(self.bytes, int) or self.bytes < 0:
            raise ValueError("receiver bytes must be a nonnegative integer")
        if not math.isclose(self.end_seconds - self.start_seconds, self.seconds, rel_tol=0, abs_tol=.002):
            raise ValueError("receiver interval bounds disagree with native elapsed seconds")


@dataclass(frozen=True)
class NativeParse:
    samples: tuple[RttSample, ...] = ()
    receiver_intervals: tuple[ReceiverInterval, ...] = ()
    probe_counts: tuple[ProbeCounts, ...] = ()
    findings: tuple[ValidationFinding, ...] = ()
    source: Optional[SourceRef] = None
    sessions: tuple[str, ...] = ()
    receiver_totals: tuple[dict[str, Any], ...] = ()
    session_details: tuple[dict[str, Any], ...] = ()


def _finding(code: str, message: str, source: Optional[SourceRef] = None, severity: str = "error") -> ValidationFinding:
    return ValidationFinding(code=code, severity=severity, message=message,
                             unit_id=source.unit_id if source else None,
                             artifact_id=source.artifact_id if source else None)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _evidence(value: Any, reason: str) -> Evidence:
    return Evidence(value=value) if value is not None else Evidence(unavailable_reason=reason)


def _finite_number(value: Any, *, integer: bool = False) -> Optional[float | int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0 or (integer and not isinstance(value, int)):
        return None
    return value


_COUNTER_PATTERNS = {
    "runtime_seconds": re.compile(r"\bRunTime=([0-9.eE+-]+)\s*sec\b", re.I),
    "sent": re.compile(r"\bSentMessages=(\d+)\b", re.I),
    "received": re.compile(r"\bReceivedMessages=(\d+)\b", re.I),
    "skipped": re.compile(r"\bSkippedMessages=(\d+)\b", re.I),
    "dropped": re.compile(r"#\s*dropped messages\s*=\s*(\d+)", re.I),
    "duplicated": re.compile(r"#\s*duplicated messages\s*=\s*(\d+)", re.I),
    "out_of_order": re.compile(r"#\s*out-of-order messages\s*=\s*(\d+)", re.I),
}


def parse_sockperf(csv_path: Path, log_path: Optional[Path], *, source: SourceRef,
                   log_source: Optional[SourceRef] = None) -> NativeParse:
    """Parse Sockperf 3.10's preamble plus full-RTT four-column export."""
    findings: list[ValidationFinding] = []
    samples: list[RttSample] = []
    counters: dict[str, Any] = {}
    try:
        csv_source = SourceRef(**{**source.__dict__, "sha256": _sha(csv_path)})
        lines = csv_path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        return NativeParse(findings=(_finding("native.sockperf.read", f"cannot read CSV: {exc}", source),), source=source)
    except UnicodeError as exc:
        return NativeParse(findings=(_finding("native.sockperf.encoding", f"CSV is not UTF-8: {exc}", source),), source=source)

    header_index = None
    for i, line in enumerate(lines):
        normalized = line.strip().lower()
        if normalized.lstrip().startswith("packet,") and "txtime" in normalized and "rtt" in normalized:
            header_index = i
            if "usec" not in normalized or "rtt" not in normalized:
                findings.append(_finding("native.sockperf.header", f"line {i + 1}: RTT unit/header is unsupported", csv_source))
            if "full" not in " ".join(lines[:i]).lower() and "rtt(usec)" not in normalized:
                # The canonical native column is explicitly RTT, rather than one-way latency.
                pass
            break
    if header_index is None:
        findings.append(_finding("native.sockperf.header", "full-RTT CSV header was not found", csv_source))
    else:
        for offset, line in enumerate(lines[header_index + 1:], header_index + 2):
            if line.strip().startswith("[") or line.strip().lower().startswith("sockperf:"):
                break
            row = next(csv.reader([line]), [])
            if not row or all(not cell.strip() for cell in row):
                continue
            if len(row) == 1 and row[0].strip() and set(row[0].strip()) == {"-"}:
                continue
            if len(row) != 4:
                findings.append(_finding("native.sockperf.row", f"line {offset}: expected four fields", csv_source, "warning"))
                continue
            try:
                packet = int(row[0].strip())
                tx, rx, rtt = map(float, (cell.strip() for cell in row[1:]))
                if packet < 0 or not all(math.isfinite(n) and n >= 0 for n in (tx, rx, rtt)) or rx < tx:
                    raise ValueError("negative, nonfinite or reversed sample")
                if abs((rx - tx) * 1_000_000 - rtt) > 0.003:
                    raise ValueError("exported RTT disagrees with monotonic timestamps")
                samples.append(RttSample(packet, tx, rx, rtt, csv_source, offset))
            except (ValueError, OverflowError) as exc:
                findings.append(_finding("native.sockperf.row", f"line {offset}: {exc}", csv_source, "warning"))

    scoped_counts, conflicts = _sockperf_counters(lines)
    for scope, key in conflicts:
        findings.append(_finding("native.sockperf.counter_conflict", f"CSV {scope} reports conflicting {key} values", csv_source))
    if log_path is not None:
        if log_source is None:
            findings.append(_finding("native.sockperf.log_source", "log counters unavailable because log_source provenance was not supplied", source))
            counter_source = csv_source
        else:
            try:
                log_lines = log_path.read_text(encoding="utf-8-sig").splitlines()
                log_counts, log_conflicts = _sockperf_counters(log_lines)
                for scope, key in log_conflicts:
                    findings.append(_finding("native.sockperf.counter_conflict", f"log {scope} reports conflicting {key} values", log_source))
                for scope, values in log_counts.items():
                    target = scoped_counts.setdefault(scope, {})
                    for key, val in values.items():
                        if key in target and target[key] != val:
                            findings.append(_finding("native.sockperf.counter_conflict", f"CSV and log {scope} disagree for {key}", log_source))
                        else:
                            target[key] = val
                csv_source = SourceRef(**{**csv_source.__dict__, "sha256": csv_source.sha256})
                counter_source = SourceRef(**{**log_source.__dict__, "sha256": _sha(log_path)})
            except (OSError, UnicodeError) as exc:
                findings.append(_finding("native.sockperf.log_read", f"cannot read log: {exc}", log_source))
                counter_source = csv_source
    else:
        counter_source = csv_source
    if scoped_counts:
        count_tuple = tuple(ProbeCounts(
            sent=_evidence(values.get("sent"), f"native sent count was not reported for {scope}"),
            received=_evidence(values.get("received"), f"native received count was not reported for {scope}"),
            dropped=_evidence(values.get("dropped"), f"native dropped count was not reported for {scope}"),
            duplicated=_evidence(values.get("duplicated"), f"native duplicate count was not reported for {scope}"),
            out_of_order=_evidence(values.get("out_of_order"), f"native out-of-order count was not reported for {scope}"),
            skipped=_evidence(values.get("skipped"), f"native skipped count was not reported for {scope}"),
            runtime_seconds=_evidence(values.get("runtime_seconds"), f"native runtime was not reported for {scope}"),
            reporting_scope=scope, source=counter_source)
            for scope, values in sorted(scoped_counts.items(), key=lambda item: (item[0] != "[Valid Duration]", item[0])))
    else:
        count_tuple = ()
        findings.append(_finding("native.sockperf.counts_unavailable", "Sockperf invocation counters were not reported", source, "warning"))
    if not samples:
        findings.append(_finding("native.sockperf.samples_unavailable", "no valid full-RTT samples were parsed", csv_source, "warning"))
    return NativeParse(samples=tuple(samples), probe_counts=count_tuple,
                       findings=tuple(findings), source=csv_source, sessions=("1",))


def _sockperf_counters(lines: Sequence[str]) -> tuple[dict[str, dict[str, Any]], set[tuple[str, str]]]:
    values: dict[str, dict[str, list[Any]]] = {}
    scope = "[Unscoped]"
    for line in lines:
        clean = re.sub(r"\x1b\[[0-9;]*m", "", line)
        scope_match = re.search(r"\[(Total Run|Valid Duration)\]", clean, re.I)
        if scope_match:
            scope = "[" + scope_match.group(1).title() + "]"
        for key, pattern in _COUNTER_PATTERNS.items():
            match = pattern.search(clean)
            if match:
                try:
                    val: Any = float(match.group(1)) if key == "runtime_seconds" else int(match.group(1))
                    if math.isfinite(val) and val >= 0:
                        if key == "runtime_seconds" and val == 0:
                            continue
                        values.setdefault(scope, {}).setdefault(key, []).append(val)
                except ValueError:
                    continue
        if ("RunTime=" in clean and "SentMessages=" in clean and "ReceivedMessages=" in clean
                and "SkippedMessages=" not in clean):
            # Sockperf 3.10 emits SkippedMessages only on the branch where a skip occurred.
            values.setdefault(scope, {}).setdefault("skipped", []).append(0)
    return ({scope: {key: entries[0] for key, entries in counters.items()}
             for scope, counters in values.items()},
            {(scope, key) for scope, counters in values.items() for key, entries in counters.items()
             if len(set(entries)) > 1})


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _json_load(text: str) -> Any:
    return json.loads(text, object_pairs_hook=_pairs_no_duplicates,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"nonfinite JSON number {value}")))


def parse_iperf3(path: Path, *, source: SourceRef, format: str) -> NativeParse:
    """Parse monolithic iperf3 JSON or newline event/data JSONL."""
    if format not in {"json", "jsonl"}:
        raise ValueError("format must be json or jsonl")
    findings: list[ValidationFinding] = []
    intervals: list[ReceiverInterval] = []
    totals: list[dict[str, Any]] = []
    sessions: list[str] = []
    session_details: list[dict[str, Any]] = []
    actual_source = source
    try:
        actual_source = SourceRef(**{**source.__dict__, "sha256": _sha(path)})
    except OSError as exc:
        return NativeParse(findings=(_finding("native.iperf3.read", f"cannot read input: {exc}", source),), source=source)
    objects: Any
    if format == "json":
        try:
            root = _json_load(path.read_text(encoding="utf-8"))
            if not isinstance(root, dict):
                raise ValueError("top-level JSON must be an object")
            objects = [(1, root)]
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            return NativeParse(findings=(_finding("native.iperf3.json", str(exc), actual_source),), source=actual_source)
    else:
        def jsonl_objects():
            try:
                with path.open(encoding="utf-8") as handle:
                    for line_no, line in enumerate(handle, 1):
                        if not line.strip():
                            continue
                        try:
                            value = _json_load(line)
                            if not isinstance(value, dict):
                                raise ValueError("event must be a JSON object")
                            yield line_no, value
                        except (ValueError, json.JSONDecodeError) as exc:
                            findings.append(_finding("native.iperf3.jsonl", f"line {line_no}: {exc}", actual_source, "warning"))
            except (OSError, UnicodeError) as exc:
                findings.append(_finding("native.iperf3.read", f"cannot read JSONL: {exc}", actual_source))
        objects = jsonl_objects()
    current = 0
    for line_no, obj in objects:
        if format == "jsonl":
            event = obj.get("event")
            if event == "start":
                current += 1
                sessions.append(str(current))
                data = obj.get("data", {})
                session_details.append({"session_id": str(current), "start": data})
                continue
            if event == "error":
                findings.append(_finding("native.iperf3.error", f"line {line_no}: iperf3 error event", actual_source))
                continue
            if event in {"end", "server_output", "server_output_json"}:
                payload = obj.get("data", obj)
                if event == "end":
                    totals.extend(_extract_totals(payload, actual_source, line_no, str(max(1, current))))
                    if session_details and session_details[-1]["session_id"] == str(max(1, current)):
                        session_details[-1]["end"] = payload
                elif event.startswith("server_output"):
                    embedded = payload
                    if isinstance(embedded, str):
                        try:
                            embedded = _json_load(embedded)
                        except ValueError as exc:
                            findings.append(_finding("native.iperf3.server_output", f"line {line_no}: {exc}", actual_source, "warning"))
                            continue
                    if isinstance(embedded, dict):
                        intervals.extend(_extract_intervals(embedded, actual_source, line_no, str(max(1, current)), findings))
                        totals.extend(_extract_totals(embedded, actual_source, line_no, str(max(1, current))))
                continue
            if event != "interval":
                findings.append(_finding("native.iperf3.event", f"line {line_no}: unsupported or missing event {event!r}", actual_source, "warning"))
                continue
            current = max(1, current)
            data = obj.get("data", obj)
            intervals.extend(_extract_interval_data(data, actual_source, line_no, str(current), findings))
            continue
        sessions.append("1")
        session_details.append({"session_id": "1", "start": obj.get("start", {}), "end": obj.get("end", {})})
        intervals.extend(_extract_intervals(obj, actual_source, line_no, "1", findings))
        server = obj.get("server_output_json")
        if isinstance(server, str):
            try:
                server = _json_load(server)
            except ValueError as exc:
                findings.append(_finding("native.iperf3.server_output", str(exc), actual_source, "warning"))
        if isinstance(server, dict):
            intervals.extend(_extract_intervals(server, actual_source, line_no, "1", findings))
            totals.extend(_extract_totals(server, actual_source, line_no, "1"))
        totals.extend(_extract_totals(obj, actual_source, line_no, "1"))
    intervals = _dedupe_intervals(intervals, findings)
    for total in totals:
        session_intervals = [item for item in intervals if item.session_id == total["session_id"] and not item.omitted]
        if session_intervals:
            interval_bytes = sum(item.bytes for item in session_intervals)
            if interval_bytes != total["bytes"]:
                findings.append(_finding("native.iperf3.total_mismatch",
                    f"session {total['session_id']}: receiver final bytes contradict interval sum", actual_source, "warning"))
    if len(set(sessions)) > 1:
        findings.append(_finding("native.iperf3.multiple_sessions", "multiple persistent-server sessions require external unit association", actual_source, "warning"))
    if not intervals:
        findings.append(_finding("native.iperf3.receiver_unavailable", "no receiver-side measured intervals were found", actual_source, "warning"))
    return NativeParse(receiver_intervals=tuple(intervals), findings=tuple(findings), source=actual_source,
                       sessions=tuple(dict.fromkeys(sessions)), receiver_totals=tuple(totals),
                       session_details=tuple(session_details))


def _extract_interval_data(data: Any, source: SourceRef, line: int, session: str,
                           findings: list[ValidationFinding]) -> list[ReceiverInterval]:
    if not isinstance(data, dict):
        findings.append(_finding("native.iperf3.interval", f"line {line}: interval data must be an object", source, "warning"))
        return []
    return _interval_items(data, source, line, session, findings)


def _extract_intervals(root: dict[str, Any], source: SourceRef, line: int, session: str,
                       findings: list[ValidationFinding]) -> list[ReceiverInterval]:
    output: list[ReceiverInterval] = []
    for item in root.get("intervals", []) if isinstance(root.get("intervals", []), list) else []:
        if isinstance(item, dict):
            output.extend(_interval_items(item, source, line, session, findings))
    if root.get("event") == "interval":
        output.extend(_interval_items(root.get("data", root), source, line, session, findings))
    return output


def _interval_items(obj: dict[str, Any], source: SourceRef, line: int, session: str,
                    findings: list[ValidationFinding]) -> list[ReceiverInterval]:
    interval = obj.get("sum")
    streams = obj.get("streams")
    candidates: list[dict[str, Any]] = []
    if isinstance(interval, dict) and interval.get("sender") is False:
        candidates = [interval]
    elif isinstance(streams, list):
        recv = []
        for stream in streams:
            if not isinstance(stream, dict):
                continue
            if isinstance(stream.get("receiver"), dict):
                recv.append(stream["receiver"])
            elif stream.get("sender") is False:
                recv.append(stream)
        if recv and all(_bounds(s) == _bounds(recv[0]) for s in recv):
            candidates = recv
    if not candidates:
        return []
    first = candidates[0]
    start, end = _number(first.get("start")), _number(first.get("end"))
    elapsed = _number(first.get("seconds"))
    if start is None or end is None or elapsed is None or end <= start or elapsed <= 0:
        findings.append(_finding("native.iperf3.interval_time", f"line {line}: invalid receiver interval bounds", source, "warning"))
        return []
    byte_values = [_nonnegative_integer(item.get("bytes")) for item in candidates]
    if any(value is None for value in byte_values):
        findings.append(_finding("native.iperf3.interval_bytes", f"line {line}: receiver bytes missing or invalid", source, "warning"))
        return []
    if len(candidates) > 1:
        amount = sum(byte_values)  # type: ignore[arg-type]
    else:
        amount = byte_values[0]
    if not math.isclose(end - start, elapsed, rel_tol=0, abs_tol=0.002):
        findings.append(_finding("native.iperf3.interval_duration", f"line {line}: end-start disagrees with native seconds", source, "warning"))
        return []
    omitted = bool(first.get("omitted", False))
    return [ReceiverInterval(session, start, end, elapsed, int(amount), omitted, False, source, line)]


def _extract_totals(root: Any, source: SourceRef, line: int, session: str) -> list[dict[str, Any]]:
    if not isinstance(root, dict):
        return []
    end = root.get("end", root)
    if not isinstance(end, dict):
        return []
    for key in ("sum_received", "sum_received_bidir_reverse", "sum"):
        item = end.get(key)
        receiver_marked = item.get("sender") is False if isinstance(item, dict) else False
        if isinstance(item, dict) and (receiver_marked or key in {"sum_received", "sum_received_bidir_reverse"}):
            amount, seconds = _nonnegative_integer(item.get("bytes")), _number(item.get("seconds"))
            if amount is not None and seconds is not None and seconds > 0:
                return [{"session_id": session, "bytes": amount, "seconds": seconds,
                         "source": source, "line": line}]
    streams = end.get("streams")
    if isinstance(streams, list):
        receivers = [item.get("receiver") for item in streams
                     if isinstance(item, dict) and isinstance(item.get("receiver"), dict)]
        if receivers and all(_number(item.get("seconds")) == _number(receivers[0].get("seconds")) for item in receivers):
            amounts = [_nonnegative_integer(item.get("bytes")) for item in receivers]
            seconds = _number(receivers[0].get("seconds"))
            if seconds and all(value is not None for value in amounts):
                return [{"session_id": session, "bytes": sum(amounts), "seconds": seconds,
                         "source": source, "line": line}]
    return []


def _dedupe_intervals(items: list[ReceiverInterval], findings: list[ValidationFinding]) -> list[ReceiverInterval]:
    result: list[ReceiverInterval] = []
    last_by_session: dict[str, ReceiverInterval] = {}
    for item in sorted(items, key=lambda i: (i.session_id, i.line)):
        prev = last_by_session.get(item.session_id)
        if prev and item.start_seconds < prev.start_seconds:
            findings.append(_finding("native.iperf3.session_reset", f"line {item.line}: receiver interval clock moved backwards", item.source, "warning"))
            continue
        if prev and item.start_seconds < prev.end_seconds:
            code = "native.iperf3.interval_duplicate" if (prev.start_seconds, prev.end_seconds, prev.bytes) == (item.start_seconds, item.end_seconds, item.bytes) else "native.iperf3.interval_overlap"
            findings.append(_finding(code, f"line {item.line}: duplicate/overlapping receiver interval was not added", item.source, "warning"))
            continue
        result.append(item)
        last_by_session[item.session_id] = item
    return result


def _bounds(item: dict[str, Any]) -> tuple[Any, Any]:
    return item.get("start"), item.get("end")


def _number(value: Any) -> Optional[float]:
    result = _finite_number(value)
    return float(result) if result is not None else None


def _nonnegative_integer(value: Any) -> Optional[int]:
    result = _finite_number(value, integer=True)
    return int(result) if result is not None else None


def _linear_quantile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    h = (len(ordered) - 1) * q
    lower = math.floor(h)
    upper = math.ceil(h)
    return ordered[lower] + (h - lower) * (ordered[upper] - ordered[lower])


def rtt_statistics(samples_us: Sequence[float]) -> dict[str, object]:
    vals = [float(v) for v in samples_us]
    if any(isinstance(v, bool) or not math.isfinite(v) or v < 0 for v in samples_us):
        raise ValueError("RTT samples must be finite nonnegative numbers")
    n = len(vals)
    return {
        "valid_replies": n,
        "median_us": _evidence(_linear_quantile(vals, .5), "at least one valid measured RTT sample is required") if n else Evidence[float](unavailable_reason="at least one valid measured RTT sample is required"),
        "p95_us": _evidence(_linear_quantile(vals, .95), "P95 requires at least 1000 valid measured replies") if n >= 1000 else Evidence[float](unavailable_reason="P95 requires at least 1000 valid measured replies"),
        "p99_us": _evidence(_linear_quantile(vals, .99), "P99 requires at least 10000 valid measured replies") if n >= 10000 else Evidence[float](unavailable_reason="P99 requires at least 10000 valid measured replies"),
        "sample_stddev_us": Evidence[float](value=statistics.stdev(vals)) if n >= 2 else Evidence[float](unavailable_reason="sample standard deviation requires at least two valid measured replies"),
        "p95_minus_median_us": Evidence[float](value=_linear_quantile(vals, .95) - _linear_quantile(vals, .5)) if n >= 1000 else Evidence[float](unavailable_reason="P95-minus-median requires P95 (at least 1000 replies)"),
    }


def receiver_statistics(intervals: Sequence[ReceiverInterval]) -> dict[str, object]:
    measured = [i for i in intervals if not i.omitted and not i.sender]
    ordered = sorted(measured, key=lambda i: (i.session_id, i.line))
    accepted: list[ReceiverInterval] = []
    for item in ordered:
        if item.seconds <= 0 or item.bytes < 0 or isinstance(item.bytes, bool):
            continue
        if accepted and accepted[-1].session_id == item.session_id and item.start_seconds < accepted[-1].end_seconds:
            continue
        accepted.append(item)
    byte_count = sum(i.bytes for i in accepted)
    seconds = sum(i.seconds for i in accepted)
    if not accepted or seconds <= 0:
        return {"received_bytes": Evidence[int](unavailable_reason="no valid receiver-side measured intervals"),
                "measured_seconds": Evidence[float](unavailable_reason="no valid receiver-side measured intervals"),
                "throughput_gbps": Evidence[float](unavailable_reason="no valid receiver-side measured intervals")}
    return {"received_bytes": Evidence[int](value=byte_count),
            "measured_seconds": Evidence[float](value=seconds),
            "throughput_gbps": Evidence[float](value=8 * byte_count / seconds / 1_000_000_000)}
