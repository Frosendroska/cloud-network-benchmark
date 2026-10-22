"""Credential-free validation of materialized F01R result bundles."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import math
from dataclasses import replace
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
import csv
import io
from itertools import zip_longest
import yaml

from .contracts.artifacts import resolve_artifact_path
from .contracts.common import (
    CleanupState, ComparisonEligibility, DataValidity, Evidence, ExecutionState,
    ExperimentMode, StructuredEvidence, ValidationStatus,
)
from .contracts.campaign import ResolvedCampaign, ResolvedObservation
from .contracts.validation import ValidationFinding, ValidationResult, ValidatorInput
from .contracts.measurements import DiagnosticSample, VmMetadata, CommandEvidence, MeasurementTiming
from .manifests import CampaignManifest, ChildRunManifest
from .native import NativeParse, SourceRef, parse_iperf3, parse_sockperf, receiver_statistics, rtt_statistics


def _finding(code: str, severity: str, message: str, *, unit: str | None = None,
             artifact: str | None = None) -> ValidationFinding:
    return ValidationFinding(code=code, severity=severity, message=message,
                             unit_id=unit, artifact_id=artifact)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _evidence(value: Any, reason: str) -> dict[str, Any]:
    return Evidence(value=value).model_dump(mode="json") if value is not None else Evidence(unavailable_reason=reason).model_dump(mode="json")


def _source(record: Any, value: ValidatorInput) -> SourceRef:
    versions = value.tool_versions.value if isinstance(value.tool_versions.value, dict) else {}
    role = str(getattr(record.producer, "value", record.producer))
    metadata = next((item for item in value.vm_metadata_records
                     if str(getattr(item.vm_role, "value", item.vm_role)) == role), None)
    builds = metadata.source_builds.value if metadata and isinstance(metadata.source_builds.value, dict) else {}
    tool_versions = metadata.tool_versions.value if metadata and isinstance(metadata.tool_versions.value, dict) else {}
    program = "iperf3" if "iperf3" in record.kind else "sockperf"
    return SourceRef(record.artifact_id, record.path, record.sha256.value or "0" * 64,
                     record.unit_id, value.identity.deployment_id, role,
                     versions.get(program) or tool_versions.get(program),
                     str(builds.get(program)) if builds.get(program) is not None else None)


def _artifact_path(value: ValidatorInput, record: Any) -> Path:
    return resolve_artifact_path(Path(value.child_result_root), record.path)


def _validate_artifacts(value: ValidatorInput) -> tuple[list[ValidationFinding], dict[tuple[str, str | None], Any], dict[str, Any]]:
    findings: list[ValidationFinding] = []
    records: dict[tuple[str, str | None], Any] = {}
    declarations = value.artifacts
    for record in value.artifact_records:
        records[(record.artifact_id, record.unit_id)] = record
    for artifact_id, declaration in declarations.items():
        relevant = [r for r in value.artifact_records if r.artifact_id == artifact_id]
        if declaration.expectation == "not_expected":
            if relevant:
                findings.append(_finding("artifact.unexpected", "error", "artifact was recorded although not_expected", artifact=artifact_id))
            continue
        if not relevant:
            if declaration.expectation == "required":
                findings.append(_finding("artifact.missing", "warning", "required artifact record is missing", unit=declaration.unit_id, artifact=artifact_id))
            continue
        for record in relevant:
            if record.retrieval_state != "verified" or not record.closed:
                findings.append(_finding("artifact.unverified", "error", "artifact is not closed and verified", unit=record.unit_id, artifact=artifact_id))
                continue
            try:
                path = _artifact_path(value, record)
                payload_hash = _sha(path)
                size = path.stat().st_size
            except (OSError, ValueError) as exc:
                findings.append(_finding("artifact.path_or_read", "error", f"artifact cannot be safely read: {exc}", unit=record.unit_id, artifact=artifact_id))
                continue
            if record.size_bytes.value != size or record.sha256.value != payload_hash:
                findings.append(_finding("artifact.integrity", "error", "materialized artifact size or SHA-256 differs from its record", unit=record.unit_id, artifact=artifact_id))
    snapshot_checks = (("config_snapshot", value.config_snapshot_path, value.config_sha256),
                       ("resolved_snapshot", value.resolved_snapshot_path, value.resolved_sha256.value),
                       ("schedule_snapshot", value.schedule_snapshot_path.value, value.schedule_sha256.value))
    for artifact_id, supplied_path, supplied_hash in snapshot_checks:
        if supplied_path is None:
            continue
        matches = [r for r in value.artifact_records if r.artifact_id == artifact_id]
        if not matches:
            findings.append(_finding("provenance.snapshot_missing", "warning", "snapshot has no verified artifact record", artifact=artifact_id))
            continue
        for record in matches:
            if supplied_hash and record.sha256.value != supplied_hash:
                findings.append(_finding("provenance.snapshot_hash", "error", "snapshot hash contradicts supplied provenance", artifact=artifact_id))
    provenance = {
        "config_sha256": value.config_sha256,
        "resolved_sha256": _evidence(value.resolved_sha256.value, value.resolved_sha256.unavailable_reason or "not supplied"),
        "schedule_sha256": _evidence(value.schedule_sha256.value, value.schedule_sha256.unavailable_reason or "no schedule row"),
        "implementation_git": value.implementation_git.model_dump(mode="json"),
        "design_git": value.design_git.model_dump(mode="json"),
        "tool_versions": value.tool_versions.model_dump(mode="json"),
        "source_refs": [
            {"artifact_id": r.artifact_id, "path": r.path, "sha256": r.sha256.value,
             "unit_id": r.unit_id, "producer": str(r.producer)}
            for r in sorted(value.artifact_records, key=lambda x: (x.unit_id or "", x.artifact_id, x.path))
        ],
    }
    return findings, records, provenance


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _json_records(path: Path):
    """Yield JSON values, streaming JSONL records one line at a time."""
    if path.suffix.lower() in {".jsonl", ".ndjson"}:
        with path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, 1):
                if line.strip():
                    try:
                        yield json.loads(line)
                    except (ValueError, json.JSONDecodeError) as exc:
                        raise ValueError(f"invalid JSONL line {line_no}: {exc}") from exc
        return
    with path.open(encoding="utf-8") as handle:
        first = next((char for char in iter(lambda: handle.read(1), "") if not char.isspace()), "")
    if not first:
        return
    if first in "[{":
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            value = None
        else:
            yield from value if isinstance(value, list) else (value,)
            return
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if line.strip():
                try:
                    yield json.loads(line)
                except (ValueError, json.JSONDecodeError) as exc:
                    raise ValueError(f"invalid JSONL line {line_no}: {exc}") from exc


def _iter_jsonl(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if line.strip():
                try:
                    yield line_no, json.loads(line)
                except (ValueError, json.JSONDecodeError) as exc:
                    raise ValueError(f"invalid JSONL line {line_no}: {exc}") from exc


def _records_equal(saved, expected) -> bool:
    sentinel = object()
    for left, right in zip_longest(saved, expected, fillvalue=sentinel):
        if left != right:
            return False
    return True


def _validate_materialized_records(value: ValidatorInput, records: dict[tuple[str, str | None], Any]) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for record in value.artifact_records:
        try:
            path = _artifact_path(value, record)
            if record.artifact_id == "config_snapshot":
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != value.config_sha256:
                    findings.append(_finding("provenance.config_content", "error", "saved configuration bytes contradict config_sha256", artifact=record.artifact_id))
                parsed = yaml.safe_load(raw)
                if isinstance(parsed, dict) and (parsed.get("config_id") not in {None, value.identity.config_id}
                        or parsed.get("experiment_id") not in {None, value.identity.experiment_id}):
                    findings.append(_finding("provenance.config_identity", "error", "saved configuration identity contradicts ValidatorInput", artifact=record.artifact_id))
            elif record.artifact_id == "schedule_snapshot" and value.schedule_entry.value is not None:
                schedule_text = path.read_text(encoding="utf-8")
                rows = list(csv.DictReader(io.StringIO(schedule_text)))
                expected = value.schedule_entry.value
                if not rows or expected.original_row not in schedule_text.splitlines():
                    findings.append(_finding("provenance.schedule_content", "error", "saved schedule does not contain the supplied original row", artifact=record.artifact_id))
            elif record.kind == "command_evidence":
                saved = _json_records(path)
                actual = [item.model_dump(mode="json") for item in value.commands if item.unit_id == record.unit_id]
                if not _records_equal(saved, actual):
                    findings.append(_finding("provenance.command_content", "error", "saved command evidence contradicts supplied command records", unit=record.unit_id, artifact=record.artifact_id))
            elif record.kind == "measurement_timing":
                saved = _json_records(path)
                actual = [item.model_dump(mode="json") for item in value.timings if item.unit_id == record.unit_id]
                if not _records_equal(saved, actual) and not (
                        len(actual) == 1 and _records_equal(_json_records(path), (actual[0],))):
                    findings.append(_finding("provenance.timing_content", "error", "saved timing evidence contradicts supplied timing records", unit=record.unit_id, artifact=record.artifact_id))
            elif record.artifact_id in {"vm_a_metadata", "vm_b_metadata"}:
                parsed = VmMetadata.model_validate_json(path.read_text(encoding="utf-8"))
                role = "vm_a" if record.artifact_id == "vm_a_metadata" else "vm_b"
                if parsed.deployment_id != value.identity.deployment_id or str(parsed.vm_role) != role or str(parsed.provider) != str(value.provider):
                    findings.append(_finding("provenance.metadata_identity", "error", "saved VM metadata identity contradicts ValidatorInput", artifact=record.artifact_id))
                supplied = next((item for item in value.vm_metadata_records if str(item.vm_role) == role), None)
                if supplied is not None and parsed.model_dump(mode="json") != supplied.model_dump(mode="json"):
                    findings.append(_finding("provenance.metadata_content", "error", "saved VM metadata contradicts supplied metadata record", artifact=record.artifact_id))
            elif record.artifact_id in {"vm_a_diagnostics", "vm_b_diagnostics"}:
                role = "vm_a" if record.artifact_id == "vm_a_diagnostics" else "vm_b"
                actual = [item.model_dump(mode="json") for item in value.diagnostics if str(item.vm_role) == role]
                matched = True
                count = 0
                for _, raw in _iter_jsonl(path):
                    saved = DiagnosticSample.model_validate(raw).model_dump(mode="json")
                    if actual and (count >= len(actual) or saved != actual[count]):
                        matched = False
                    count += 1
                if actual and (not matched or count != len(actual)):
                    findings.append(_finding("provenance.diagnostics_content", "error", "saved diagnostic samples contradict supplied diagnostic records", artifact=record.artifact_id))
            elif record.artifact_id == "resolved_snapshot":
                if hashlib.sha256(path.read_bytes()).hexdigest() != value.resolved_sha256.value:
                    findings.append(_finding("provenance.resolved_content", "error", "saved resolved configuration bytes contradict resolved_sha256", artifact=record.artifact_id))
            elif record.artifact_id == "schedule_snapshot" and value.schedule_sha256.value:
                if hashlib.sha256(path.read_bytes()).hexdigest() != value.schedule_sha256.value:
                    findings.append(_finding("provenance.schedule_hash", "error", "saved schedule bytes contradict schedule_sha256", artifact=record.artifact_id))
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            findings.append(_finding("provenance.materialized_invalid", "error", f"saved evidence cannot be parsed or checked: {exc}", unit=record.unit_id, artifact=record.artifact_id))
    return findings


def _validate_manifests(value: ValidatorInput) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for manifest_type, raw_path in (("campaign", value.campaign_manifest_path), ("child", value.child_manifest_path)):
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            findings.append(_finding(f"identity.{manifest_type}_manifest_missing", "warning", f"{manifest_type} manifest is unavailable"))
            continue
        except (OSError, ValueError) as exc:
            findings.append(_finding(f"identity.{manifest_type}_manifest_invalid", "error", f"{manifest_type} manifest cannot be read: {exc}"))
            continue
        try:
            manifest = CampaignManifest.model_validate(payload) if manifest_type == "campaign" else ChildRunManifest.model_validate(payload)
        except Exception as exc:
            findings.append(_finding(f"identity.{manifest_type}_manifest_invalid", "error", f"{manifest_type} manifest violates its F01R contract: {exc}"))
            continue
        if manifest_type == "campaign":
            consistent = (manifest.campaign_id == value.identity.campaign_id
                          and manifest.config_id == value.identity.config_id
                          and manifest.experiment_id == value.identity.experiment_id
                          and manifest.mode == value.mode and manifest.scenario == value.scenario
                          and manifest.context == value.context
                          and manifest.schedule_entry.value == value.schedule_entry.value
                          and manifest.config_provenance.sha256 == value.config_sha256
                          and manifest.config_provenance.resolved_sha256 == value.resolved_sha256.value
                          and manifest.config_provenance.schedule_sha256.value == value.schedule_sha256.value
                          and manifest.config_provenance.implementation_git_commit.value == value.implementation_git_commit.value
                          and manifest.config_provenance.design_git_commit.value == value.design_git_commit.value
                          and manifest.tool_versions == value.tool_versions)
        else:
            consistent = (manifest.run_id == value.identity.run_id
                          and manifest.campaign_id == value.identity.campaign_id
                          and manifest.deployment_id == value.identity.deployment_id
                          and manifest.provider == value.provider and manifest.mode == value.mode
                          and manifest.config_id == value.identity.config_id
                          and manifest.experiment_id == value.identity.experiment_id
                          and manifest.context == value.context and manifest.scenario == value.scenario
                          and manifest.observation_id == value.identity.observation_id
                          and manifest.replaces_run_id == value.identity.replaces_run_id
                          and manifest.schedule_entry.value == value.schedule_entry.value
                          and manifest.measurement_units == value.measurement_units
                          and manifest.artifact_records == value.artifact_records
                          and manifest.commands == value.commands and manifest.timings == value.timings
                          and manifest.config_provenance.sha256 == value.config_sha256
                          and manifest.config_provenance.resolved_sha256 == value.resolved_sha256.value
                          and manifest.config_provenance.schedule_sha256.value == value.schedule_sha256.value
                          and manifest.config_provenance.implementation_git_commit.value == value.implementation_git_commit.value
                          and manifest.config_provenance.design_git_commit.value == value.design_git_commit.value
                          and manifest.execution_state == value.execution_state
                          and manifest.cleanup_state == value.cleanup_state
                          and manifest.failure == value.failure
                          and manifest.retrieval_failure == value.retrieval_failure
                          and manifest.cleanup_failure == value.cleanup_failure
                          and manifest.vm_metadata == value.vm_metadata
                          and manifest.provider_metadata == value.provider_metadata
                          and manifest.tool_versions == value.tool_versions)
        if not consistent:
            findings.append(_finding(f"identity.{manifest_type}_manifest_contradiction", "error", f"{manifest_type} manifest contradicts supplied identity/evidence"))
    return findings


def _resolved_snapshot_findings(value: ValidatorInput) -> tuple[list[ValidationFinding], dict[str, Any]]:
    findings: list[ValidationFinding] = []
    record = next((r for r in value.artifact_records if r.artifact_id == "resolved_snapshot"), None)
    if not record:
        return findings, {}
    try:
        raw = json.loads(_artifact_path(value, record).read_text(encoding="utf-8"))
        if "selected_providers" in raw:
            resolved: Any = ResolvedCampaign.model_validate(raw)
            observations = [item for item in resolved.observations if item.provider == value.provider]
            selected = {str(item) for item in resolved.selected_providers}
        else:
            resolved = ResolvedObservation.model_validate(raw)
            observations = [resolved]
            selected = {str(resolved.provider)}
        if (resolved.config_id != value.identity.config_id or resolved.experiment_id != value.identity.experiment_id
                or resolved.mode != value.mode or resolved.scenario != value.scenario or str(value.provider) not in selected):
            findings.append(_finding("identity.resolved_snapshot_contradiction", "error", "resolved snapshot identity/provider contradicts ValidatorInput", artifact="resolved_snapshot"))
        if not observations:
            findings.append(_finding("identity.resolved_provider_missing", "error", "resolved snapshot has no observation for this provider", artifact="resolved_snapshot"))
        else:
            supplied = value.measurement_units
            declared_chunks = [unit for unit in supplied if unit.kind == "probe_chunk"]
            base_units = [unit for unit in supplied if unit.kind != "probe_chunk"]
            if observations[0].measurement_units != base_units:
                findings.append(_finding("identity.resolved_units", "error", "resolved snapshot measurement units contradict supplied units", artifact="resolved_snapshot"))
            day_units = [unit for unit in base_units if unit.kind == "continuous_day"]
            if declared_chunks and (value.mode != "diagnostic_day" or len(day_units) != 1
                    or any(chunk.parent_unit_id != day_units[0].unit_id or chunk.measured_seconds > 300
                           for chunk in declared_chunks)):
                findings.append(_finding("identity.resolved_chunks", "error", "materialized probe chunks must belong to the retained diagnostic-day unit and be at most 300 seconds", artifact="resolved_snapshot"))
        return findings, raw
    except (OSError, ValueError, TypeError) as exc:
        findings.append(_finding("provenance.resolved_snapshot_invalid", "error", f"resolved snapshot is not a valid F01R JSON record: {exc}", artifact="resolved_snapshot"))
        return findings, {}


def _option(argv: list[str], name: str) -> str | None:
    aliases = {"-c": ("--client",), "-B": ("--bind",), "-p": ("--port",),
               "-P": ("--parallel",), "-t": ("--time",), "-O": ("--omit",),
               "-l": ("--length",), "-i": ("--interval", "--ip"), "-m": ("--message-size",)}
    names = (name, *aliases.get(name, ()))
    for index, token in enumerate(argv):
        for candidate in names:
            if token == candidate and index + 1 < len(argv):
                return argv[index + 1]
            if token.startswith(candidate + "="):
                return token[len(candidate) + 1:]
            if token.startswith(candidate) and token != candidate and len(candidate) == 2:
                return token[len(candidate):]
    return None


def _option_values(argv: list[str], name: str) -> list[str]:
    aliases = {"-c": ("--client",), "-p": ("--port",), "-P": ("--parallel",),
               "-t": ("--time",), "-O": ("--omit",), "-l": ("--length",),
               "-i": ("--interval",), "-m": ("--message-size",), "--mps": ("--mps",)}
    values: list[str] = []
    for index, token in enumerate(argv):
        for candidate in dict.fromkeys((name, *aliases.get(name, ()))):
            if token == candidate and index + 1 < len(argv):
                values.append(argv[index + 1])
                break
            if token.startswith(candidate + "="):
                values.append(token[len(candidate) + 1:])
                break
            if len(candidate) == 2 and token.startswith(candidate) and token != candidate:
                values.append(token[len(candidate):])
                break
    return values


def _parse_unit(value: ValidatorInput, unit: Any, records: dict[tuple[str, str | None], Any]) -> tuple[NativeParse, NativeParse | None]:
    csv_record = next((r for r in value.artifact_records if r.unit_id == unit.unit_id and r.kind == "sockperf-csv"), None)
    log_record = next((r for r in value.artifact_records if r.unit_id == unit.unit_id and r.kind == "sockperf-log"), None)
    tcp_record = next((r for r in value.artifact_records if r.unit_id == unit.unit_id and r.kind == "iperf3-json"), None)
    if unit.kind == "probe_chunk":
        day_id = next((u.unit_id for u in value.measurement_units if u.kind == "continuous_day"), None)
        chunks = sorted((r for r in value.artifact_records if r.artifact_id == "day-probe-csv" and r.unit_id == day_id), key=lambda r: r.path)
        logs = sorted((r for r in value.artifact_records if r.artifact_id == "day-probe-log" and r.unit_id == day_id), key=lambda r: r.path)
        declared_chunks = [u for u in value.measurement_units if u.kind == "probe_chunk"]
        try:
            index = declared_chunks.index(unit)
            csv_record = next((r for r in chunks if Path(r.path).stem == unit.unit_id), chunks[index])
            log_record = next((r for r in logs if Path(r.path).stem == unit.unit_id), logs[index] if index < len(logs) else None)
        except (ValueError, IndexError):
            csv_record = log_record = None
    empty = NativeParse()
    sock = empty
    if csv_record:
        try:
            sock = parse_sockperf(_artifact_path(value, csv_record), _artifact_path(value, log_record) if log_record else None,
                                 source=_source(csv_record, value), log_source=_source(log_record, value) if log_record else None)
        except (OSError, ValueError) as exc:
            sock = NativeParse(findings=(_finding("native.sockperf.read", "error", str(exc), unit=unit.unit_id,
                                                   artifact=csv_record.artifact_id),))
    if unit.kind == "continuous_day":
        csv_record = log_record = None
        tcp_record = next((r for r in value.artifact_records if r.artifact_id == "day-iperf3-client"), None)
    server_record = next((r for r in value.artifact_records if r.artifact_id == "iperf3_server_log"), None)
    tcp = None
    if tcp_record:
        try:
            tcp = parse_iperf3(_artifact_path(value, tcp_record), source=_source(tcp_record, value),
                               format="jsonl" if tcp_record.kind.endswith("jsonl") else "json")
        except (OSError, ValueError) as exc:
            tcp = NativeParse(findings=(_finding("native.iperf3.read", "error", str(exc), unit=unit.unit_id,
                                                  artifact=tcp_record.artifact_id),))
    if server_record:
        try:
            receiver = parse_iperf3(_artifact_path(value, server_record), source=_source(server_record, value), format="jsonl")
            receiver = _receiver_session_for_unit(value, unit, receiver)
            if tcp is not None:
                client_stats, receiver_stats = _tcp_statistics(tcp), _tcp_statistics(receiver)
                client_bytes = client_stats["received_bytes"].value
                receiver_bytes = receiver_stats["received_bytes"].value
                client_seconds = client_stats["measured_seconds"].value
                receiver_seconds = receiver_stats["measured_seconds"].value
                if client_bytes is not None and receiver_bytes is not None and client_bytes != receiver_bytes:
                    receiver = replace(receiver, findings=(*receiver.findings, _finding(
                        "native.iperf3.client_receiver_mismatch", "error",
                        "client-embedded receiver total contradicts VM B receiver evidence", unit=unit.unit_id,
                        artifact=server_record.artifact_id)))
                if client_seconds is not None and receiver_seconds is not None and not math.isclose(client_seconds, receiver_seconds, rel_tol=0, abs_tol=.002):
                    receiver = replace(receiver, findings=(*receiver.findings, _finding(
                        "native.iperf3.client_receiver_mismatch", "error",
                        "client-embedded receiver duration contradicts VM B receiver evidence", unit=unit.unit_id,
                        artifact=server_record.artifact_id)))
            tcp = receiver
        except (OSError, ValueError) as exc:
            tcp = NativeParse(findings=(_finding("native.iperf3.server_read", "error", str(exc), unit=unit.unit_id,
                                                 artifact=server_record.artifact_id),))
    return sock, tcp


def _receiver_session_for_unit(value: ValidatorInput, unit: Any, parsed: NativeParse) -> NativeParse:
    """Associate B's persistent listener session by resolved stream setting and recorded time."""
    if not parsed.sessions:
        return parsed
    expected_streams = unit.streams or 1
    timing = next((item for item in value.timings if item.unit_id == unit.unit_id), None)
    command_start = next((command.started_at.value for command in value.commands
                          if command.unit_id == unit.unit_id and command.producer == "vm_a"
                          and command.started_at.value is not None), None)
    command_start = command_start or (timing.tcp_started_at.value if timing else None)

    def test_config(detail: dict[str, Any]) -> tuple[Any, Any, Any]:
        start = detail.get("start") or {}
        if not isinstance(start, dict):
            return None, None, None
        test = start.get("test_start", start)
        streams = test.get("num_streams") if isinstance(test, dict) else None
        protocol = test.get("protocol") if isinstance(test, dict) else None
        stamp = start.get("timestamp")
        if isinstance(stamp, dict):
            stamp = stamp.get("timesecs") or stamp.get("time")
        try:
            stamp = float(stamp) if stamp is not None else None
        except (TypeError, ValueError):
            stamp = None
        return streams, stamp, protocol

    details = {item.get("session_id"): item for item in parsed.session_details}
    candidates = []
    for session in parsed.sessions:
        streams, stamp, protocol = test_config(details.get(session, {}))
        if streams is not None and streams != expected_streams:
            continue
        if protocol is not None and str(protocol).upper() != "TCP":
            continue
        if command_start is not None and stamp is not None and abs(stamp - command_start.timestamp()) > 300:
            continue
        candidates.append(session)
    if len(candidates) != 1:
        return NativeParse(findings=(*parsed.findings, _finding(
            "native.iperf3.session_ambiguous", "error",
            "VM B persistent receiver session cannot be uniquely associated by stream settings and recorded start time",
            unit=unit.unit_id, artifact=parsed.source.artifact_id if parsed.source else None)), source=parsed.source,
            sessions=parsed.sessions, session_details=parsed.session_details)
    selected = candidates[0]
    detail = details.get(selected, {})
    streams, _, protocol = test_config(detail)
    additions = []
    if streams is None or protocol is None:
        additions.append(_finding("native.iperf3.session_settings_unavailable", "warning",
            "VM B receiver start does not report protocol and num_streams for the associated session", unit=unit.unit_id,
            artifact=parsed.source.artifact_id if parsed.source else None))
    return replace(parsed,
        receiver_intervals=tuple(item for item in parsed.receiver_intervals if item.session_id == selected),
        receiver_totals=tuple(item for item in parsed.receiver_totals if item["session_id"] == selected),
        findings=(*parsed.findings, *additions), sessions=(selected,),
        session_details=tuple(item for item in parsed.session_details if item.get("session_id") == selected))


def _tcp_statistics(tcp: NativeParse | None) -> dict[str, Any]:
    if tcp is None:
        return {"received_bytes": Evidence[int](unavailable_reason="receiver iperf3 evidence is unavailable"),
                "measured_seconds": Evidence[float](unavailable_reason="receiver iperf3 evidence is unavailable"),
                "throughput_gbps": Evidence[float](unavailable_reason="receiver iperf3 evidence is unavailable")}
    if tcp.receiver_intervals:
        return receiver_statistics(tcp.receiver_intervals)
    if len(tcp.receiver_totals) == 1:
        total = tcp.receiver_totals[0]
        return {"received_bytes": Evidence[int](value=total["bytes"]),
                "measured_seconds": Evidence[float](value=total["seconds"]),
                "throughput_gbps": Evidence[float](value=8 * total["bytes"] / total["seconds"] / 1e9)}
    return {"received_bytes": Evidence[int](unavailable_reason="no receiver-side measured intervals or unique receiver total"),
            "measured_seconds": Evidence[float](unavailable_reason="receiver measured duration unavailable"),
            "throughput_gbps": Evidence[float](unavailable_reason="receiver throughput unavailable")}


def _valid_probe_counts(sock: NativeParse) -> tuple[Any, ...]:
    scoped = tuple(item for item in sock.probe_counts if item.reporting_scope == "[Valid Duration]")
    if scoped:
        return scoped
    if len(sock.probe_counts) == 1 and sock.probe_counts[0].reporting_scope in {"[Unscoped]", "one Sockperf invocation"}:
        return sock.probe_counts
    return ()


def _invocation_probe_counts(sock: NativeParse) -> tuple[Any, ...]:
    total = tuple(item for item in sock.probe_counts if item.reporting_scope == "[Total Run]")
    return total or _valid_probe_counts(sock)


def _measured_samples(value: ValidatorInput, unit: Any, sock: NativeParse) -> tuple[Any, ...]:
    """Keep only samples whose complete source-native RTT lies inside the measured window."""
    timing = next((item for item in value.timings if item.unit_id == unit.unit_id), None)
    if timing is None or timing.measured_started_at.value is None or timing.measured_finished_at.value is None:
        return ()
    mapping = next((item for item in timing.clock_mappings
                    if str(getattr(item.vm_role, "value", item.vm_role)) == "vm_a"
                    and item.estimated_error_ms.value is not None), None)
    if mapping is None:
        return ()
    start, end = timing.measured_started_at.value.timestamp(), timing.measured_finished_at.value.timestamp()
    error = mapping.estimated_error_ms.value / 1000
    result = []
    for sample in sock.samples:
        tx = mapping.utc_timestamp.timestamp() + sample.tx_seconds - mapping.monotonic_seconds
        rx = mapping.utc_timestamp.timestamp() + sample.rx_seconds - mapping.monotonic_seconds
        if tx - error >= start and rx + error < end:
            result.append(sample)
    return tuple(result)


def _command_findings(value: ValidatorInput) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for command in value.commands:
        argv = command.request.get("argv")
        if not isinstance(argv, list) or not all(isinstance(token, str) for token in argv):
            findings.append(_finding("command.argv_missing", "warning", "durable argv is unavailable", unit=command.unit_id))
            continue
        lower = [token.lower() for token in argv]
        joined = " ".join(lower)
        forbidden = {"--reverse", "-r", "--bidir", "--bidirectional", "--rate", "--bitrate", "-b", "--udp", "-u", "--window", "-w"}
        for token in argv:
            normalized = token.lower().split("=", 1)[0] if token.startswith("--") else token
            attached_short = any(token.startswith(flag) and token != flag for flag in ("-b", "-w", "-r", "-u"))
            if normalized in forbidden or token == "-R" or attached_short:
                findings.append(_finding("command.protocol_extra", "error", f"unsupported protocol/rate option {token}", unit=command.unit_id))
        if lower and "iperf3" in lower[0] and command.producer == "vm_a":
            if "-c" not in lower:
                findings.append(_finding("command.direction", "error", "iperf3 client command must target VM B", unit=command.unit_id))
            target = _option(argv, "-c")
            try:
                address = ipaddress.ip_address(target or "")
                private_target = address.version == 4 and address.is_private and not (address.is_loopback or address.is_link_local)
            except ValueError:
                private_target = False
            if not private_target:
                findings.append(_finding("command.target", "error", "iperf3 must target VM B's private IPv4 address", unit=command.unit_id))
            if "-z" not in lower and "--zerocopy" not in lower:
                findings.append(_finding("command.zerocopy", "error", "iperf3 sender zerocopy flag is missing", unit=command.unit_id))
            if "-cubic" not in joined and not ("-c" in lower and "cubic" in lower):
                findings.append(_finding("command.congestion", "warning", "CUBIC command setting is not explicit", unit=command.unit_id))
            if _option(argv, "-p") != "5201":
                findings.append(_finding("command.port", "error", "iperf3 receiver port 5201 is missing", unit=command.unit_id))
            if len(_option_values(argv, "-c")) != 1 or len(_option_values(argv, "-p")) != 1:
                findings.append(_finding("command.duplicate_option", "error", "iperf3 client and port options must each occur once", unit=command.unit_id))
            if any(len(_option_values(argv, flag)) > 1 for flag in ("-c", "-p", "-P", "-t", "-O", "-l", "-i")):
                findings.append(_finding("command.duplicate_option", "error", "iperf3 resolved options must not be repeated", unit=command.unit_id))
            if "-r" in lower or "-R".lower() in lower or "-d" in lower or "--bidir" in lower:
                findings.append(_finding("command.direction", "error", "bidirectional/reverse iperf3 is unsupported", unit=command.unit_id))
            unit = next((u for u in value.measurement_units if u.unit_id == command.unit_id), None)
            if unit:
                expected_streams = unit.streams or 1
                for flag, expected in (("-P", str(expected_streams)), ("-t", str(unit.measured_seconds)), ("-O", "5"), ("-l", "131072"), ("-i", "1")):
                    observed_values = _option_values(argv, flag)
                    if observed_values != [expected]:
                        observed = observed_values[0] if observed_values else None
                        findings.append(_finding("command.setting", "error", f"iperf3 {flag} must be {expected}, observed {observed!r}", unit=unit.unit_id))
            if not ("-cubic" in joined or ("-c" in lower and "cubic" in lower)):
                findings.append(_finding("command.congestion", "error", "iperf3 CUBIC setting is missing", unit=command.unit_id))
            if unit and unit.kind == "continuous_day":
                if "--json-stream" not in lower or "--forceflush" not in lower:
                    findings.append(_finding("command.streaming", "error", "diagnostic-day iperf3 must stream flushed JSON Lines", unit=unit.unit_id))
            elif "-j" not in lower and "--json" not in lower:
                findings.append(_finding("command.output", "error", "short iperf3 command must preserve native JSON output", unit=command.unit_id))
            elif "--get-server-output" not in lower:
                findings.append(_finding("command.server_output", "error", "short iperf3 output must include the receiver server result", unit=command.unit_id))
        elif command.producer == "vm_a" and any("sockperf" in token for token in lower) and "under-load" in lower:
            if not any("full-rtt" in token for token in lower):
                findings.append(_finding("command.full_rtt", "error", "Sockperf full-RTT setting is missing", unit=command.unit_id))
            if not any("no-rdtsc" in token for token in lower):
                findings.append(_finding("command.monotonic", "error", "Sockperf monotonic timing setting is missing", unit=command.unit_id))
            if _option(argv, "-m") != "64":
                findings.append(_finding("command.probe_size", "error", "Sockperf probe size must be 64 bytes", unit=command.unit_id))
            if _option(argv, "--mps") != "1000":
                findings.append(_finding("command.probe_generation", "error", "Sockperf probe rate must be 1000 messages per second", unit=command.unit_id))
            if _option(argv, "--reply-every") != "1":
                findings.append(_finding("command.reply_every", "error", "Sockperf reply-every must be 1", unit=command.unit_id))
            if _option(argv, "-p") != "11111":
                findings.append(_finding("command.port", "error", "Sockperf probe port must be UDP 11111", unit=command.unit_id))
            if any(len(_option_values(argv, flag)) != 1 for flag in ("-p", "--mps", "--reply-every", "-m", "-t")):
                findings.append(_finding("command.duplicate_option", "error", "Sockperf resolved options must each occur once", unit=command.unit_id))
            unit = next((u for u in value.measurement_units if u.unit_id == command.unit_id), None)
            expected_duration = unit.measured_seconds + (5 if unit and unit.phase == "idle_latency" else 10) if unit else None
            if expected_duration is not None and _option(argv, "-t") != str(expected_duration) and unit.kind not in {"probe_chunk", "continuous_day"}:
                findings.append(_finding("command.duration", "error", f"Sockperf duration must be {expected_duration} seconds including warm-up/coordination", unit=unit.unit_id))
            if "--full-log" not in lower:
                findings.append(_finding("command.output", "error", "Sockperf native full-log output is required", unit=command.unit_id))
            target = _option(argv, "-i")
            try:
                address = ipaddress.ip_address(target or "")
                private_target = address.version == 4 and address.is_private and not (address.is_loopback or address.is_link_local)
            except ValueError:
                private_target = False
            if not private_target:
                findings.append(_finding("command.target", "error", "Sockperf must target VM B's private IPv4 address", unit=command.unit_id))
        elif command.producer == "vm_b":
            if lower and "sockperf" in lower[0] and "server" in lower:
                target = _option(argv, "-i")
                try:
                    address = ipaddress.ip_address(target or "")
                    private_bind = address.version == 4 and address.is_private and not (address.is_loopback or address.is_link_local)
                except ValueError:
                    private_bind = False
                if not private_bind:
                    findings.append(_finding("command.listener_bind", "error", "Sockperf listener must bind VM B's private IPv4 address", unit=command.unit_id))
                if _option(argv, "-p") != "11111":
                    findings.append(_finding("command.port", "error", "Sockperf server must listen on UDP 11111", unit=command.unit_id))
            if lower and "iperf3" in lower[0] and "-s" in lower:
                target = _option(argv, "-B")
                try:
                    address = ipaddress.ip_address(target or "")
                    private_bind = address.version == 4 and address.is_private and not (address.is_loopback or address.is_link_local)
                except ValueError:
                    private_bind = False
                if not private_bind:
                    findings.append(_finding("command.listener_bind", "error", "iperf3 listener must bind VM B's private IPv4 address", unit=command.unit_id))
                if _option(argv, "-p") != "5201":
                    findings.append(_finding("command.port", "error", "iperf3 server must listen on TCP 5201", unit=command.unit_id))
                if "--json-stream" not in lower or "--forceflush" not in lower:
                    findings.append(_finding("command.streaming", "error", "iperf3 server must preserve incremental JSON Lines output", unit=command.unit_id))
    for unit in value.measurement_units:
        relevant = [command for command in value.commands if command.unit_id == unit.unit_id]
        if not relevant:
            findings.append(_finding("command.evidence_missing", "warning", "actual command evidence is unavailable", unit=unit.unit_id))
        for command in relevant:
            outcome = command.result.get("outcome")
            if outcome not in {None, "succeeded"}:
                findings.append(_finding("command.failed", "warning", f"command outcome is {outcome}", unit=unit.unit_id))
            argv = command.request.get("argv", [])
            if isinstance(argv, list) and argv and any(program in str(argv[0]).lower() for program in ("sockperf", "iperf3")):
                if command.binary_version.value is None or command.source_identity.value is None:
                    findings.append(_finding("provenance.command_build_unavailable", "warning",
                        "actual benchmark command lacks binary version or source/build identity", unit=unit.unit_id))
    for role, program in (("vm_b", "sockperf"), ("vm_b", "iperf3")):
        present = any(command.producer == role and isinstance(command.request.get("argv"), list)
                      and command.request["argv"] and program in str(command.request["argv"][0]).lower()
                      and ("server" in command.request["argv"] if program == "sockperf"
                           else "-s" in command.request["argv"])
                      for command in value.commands)
        if not present:
            findings.append(_finding("command.listener_evidence_missing", "warning",
                                     f"VM B {program} server command evidence is unavailable"))
    for unit in value.measurement_units:
        if unit.phase not in {"single_flow", "multi_flow"} or unit.kind == "probe_chunk":
            continue
        timing = next((item for item in value.timings if item.unit_id == unit.unit_id), None)
        if timing and timing.probe_test_started_at.value and timing.tcp_started_at.value:
            delay = (timing.tcp_started_at.value - timing.probe_test_started_at.value).total_seconds()
            if delay < 2:
                findings.append(_finding("command.probe_notification_delay", "error",
                                         "TCP must start at least two seconds after Sockperf test-start notification", unit=unit.unit_id))
        else:
            findings.append(_finding("command.probe_notification_delay_unavailable", "warning",
                                     "test-start notification to TCP launch delay is unavailable", unit=unit.unit_id))
        if unit.kind == "continuous_day":
            continue
    for unit in value.measurement_units:
        needed = set() if unit.kind == "continuous_day" else {"sockperf"}
        if unit.phase != "idle_latency":
            needed.add("iperf3")
        for program in needed:
            present = any(command.unit_id == unit.unit_id and command.producer == "vm_a"
                          and isinstance(command.request.get("argv"), list)
                          and command.request["argv"] and program in str(command.request["argv"][0]).lower()
                          for command in value.commands)
            if not present:
                findings.append(_finding("command.unit_evidence_missing", "warning",
                                         f"VM A {program} command evidence is unavailable for this unit", unit=unit.unit_id))
        if unit.phase != "idle_latency":
            clients = [command for command in value.commands if command.unit_id == unit.unit_id
                       and command.producer == "vm_a" and command.request.get("argv")
                       and "iperf3" in str(command.request["argv"][0]).lower()
                       and (_option(command.request["argv"], "-c") is not None)]
            listeners = [command for command in value.commands if command.unit_id == unit.unit_id
                         and command.producer == "vm_b" and command.request.get("argv")
                         and "iperf3" in str(command.request["argv"][0]).lower()
                         and "-s" in command.request["argv"]]
            if clients and listeners:
                target = _option(clients[0].request["argv"], "-c")
                bind = _option(listeners[0].request["argv"], "-B")
                if target != bind:
                    findings.append(_finding("command.target_bind_mismatch", "error",
                                             "iperf3 client target does not match VM B's recorded private bind", unit=unit.unit_id))
            sock_clients = [command for command in value.commands if command.unit_id == unit.unit_id
                            and command.producer == "vm_a" and command.request.get("argv")
                            and "sockperf" in str(command.request["argv"][0]).lower()]
            sock_listeners = [command for command in value.commands if command.unit_id == unit.unit_id
                              and command.producer == "vm_b" and command.request.get("argv")
                              and "sockperf" in str(command.request["argv"][0]).lower()
                              and "server" in command.request["argv"]]
            if sock_clients and sock_listeners and _option(sock_clients[0].request["argv"], "-i") != _option(sock_listeners[0].request["argv"], "-i"):
                findings.append(_finding("command.target_bind_mismatch", "error",
                                         "Sockperf client target does not match VM B's recorded private bind", unit=unit.unit_id))
    return findings


def _coverage(value: ValidatorInput, unit: Any, sock: NativeParse, tcp: NativeParse | None) -> dict[str, Any]:
    requested = unit.measured_seconds
    unit_timings = [t for t in value.timings if t.unit_id == unit.unit_id]
    timing = unit_timings[0] if len(unit_timings) == 1 else None
    clock_errors = ({str(getattr(m.vm_role, "value", m.vm_role)): m.estimated_error_ms.value / 1000
                     for m in timing.clock_mappings if m.estimated_error_ms.value is not None} if timing else {})
    tcp_stats = _tcp_statistics(tcp) if tcp else None
    observed = tcp_stats["measured_seconds"].value if tcp_stats else None
    full_duration = observed is not None and observed >= requested
    if unit.kind == "continuous_day":
        measured = sorted((item for item in (tcp.receiver_intervals if tcp else ()) if not item.omitted),
                          key=lambda item: (item.session_id, item.start_seconds, item.line))
        contiguous = bool(measured) and len({item.session_id for item in measured}) == 1
        if contiguous:
            contiguous = all(math.isclose(right.start_seconds, left.end_seconds, rel_tol=0, abs_tol=.002)
                             for left, right in zip(measured, measured[1:]))
            contiguous = contiguous and math.isclose(measured[-1].end_seconds - measured[0].start_seconds,
                                                    requested, rel_tol=0, abs_tol=.002)
        if timing and timing.measured_started_at.value and timing.measured_finished_at.value:
            timing_seconds = (timing.measured_finished_at.value - timing.measured_started_at.value).total_seconds()
            contiguous = contiguous and math.isclose(timing_seconds, requested, rel_tol=0, abs_tol=.002)
        else:
            contiguous = False
        if tcp is not None and len(tcp.sessions) != 1:
            contiguous = False
        full_duration = full_duration and contiguous
    active = None
    bracket = None
    phase = str(getattr(unit.phase, "value", unit.phase))
    probe_start = timing.probe_test_started_at.value if timing else None
    invocation_counts = _invocation_probe_counts(sock)
    runtime_values = [item.runtime_seconds.value for item in invocation_counts if item.runtime_seconds.value is not None]
    runtime = runtime_values[0] if len(runtime_values) == 1 else None
    chunk_warmup = 5 if unit.kind == "probe_chunk" and next((u for u in value.measurement_units if u.kind == "probe_chunk"), None) == unit else 0
    warmup = (5 if unit.kind != "probe_chunk" else chunk_warmup)
    valid_duration_scope = len(invocation_counts) == 1 and invocation_counts[0].reporting_scope == "[Valid Duration]"
    runtime_offset = warmup if valid_duration_scope else 0
    probe_windows: list[tuple[float, float]] = []
    probe_epoch = probe_start.timestamp() if probe_start else None
    if probe_epoch is not None and runtime is not None and runtime > 0 and "vm_a" in clock_errors:
        error = clock_errors["vm_a"]
        probe_windows.append((probe_epoch + warmup + error, probe_epoch + runtime_offset + runtime - error))
    if timing:
        for gap in timing.gaps:
            gap_start, gap_end = gap.started_at.timestamp(), gap.finished_at.timestamp()
            split: list[tuple[float, float]] = []
            for start, end in probe_windows:
                if gap_end <= start or gap_start >= end:
                    split.append((start, end))
                else:
                    if start < gap_start:
                        split.append((start, gap_start))
                    if gap_end < end:
                        split.append((gap_end, end))
            probe_windows = split
    sample_support = None
    sample_bounds_utc = None
    if unit.kind not in {"continuous_day"} and sock.samples and timing:
        mapping = next((m for m in timing.clock_mappings if str(getattr(m.vm_role, "value", m.vm_role)) == "vm_a"
                        and m.estimated_error_ms.value is not None), None)
        if mapping:
            error = mapping.estimated_error_ms.value / 1000
            sample_start = mapping.utc_timestamp.timestamp() + min(s.tx_seconds for s in sock.samples) - mapping.monotonic_seconds + error
            sample_end = mapping.utc_timestamp.timestamp() + max(s.rx_seconds for s in sock.samples) - mapping.monotonic_seconds - error
            sample_bounds_utc = (sample_start, sample_end)
            sample_support = round(sum(max(0.0, min(end, sample_end) - max(start, sample_start))
                                       for start, end in probe_windows), 6)

    def overlap(start: float, duration: float) -> float:
        end = start + duration
        # Input wall-clock bounds are microsecond precision; round off binary timestamp
        # representation noise at that source precision, never at a policy threshold.
        return round(sum(max(0.0, min(end, p_end) - max(start, p_start))
                         for p_start, p_end in probe_windows), 6)

    tcp_start = timing.measured_started_at.value.timestamp() if timing and timing.measured_started_at.value else None
    tcp_finish = timing.measured_finished_at.value.timestamp() if timing and timing.measured_finished_at.value else None
    if phase == "idle_latency":
        if probe_windows:
            active = sum(end - start for start, end in probe_windows)
            if timing and timing.measured_started_at.value and timing.measured_finished_at.value:
                actual_start = timing.measured_started_at.value.timestamp()
                actual_end = timing.measured_finished_at.value.timestamp()
                active = min(active, max(0.0, min(actual_end, probe_windows[-1][1]) - max(actual_start, probe_windows[0][0])))
    elif observed is not None and probe_windows:
        if tcp_start is not None and tcp_finish is not None:
            timing_duration = tcp_finish - tcp_start
            if math.isclose(timing_duration, observed, rel_tol=0, abs_tol=1e-6) and "vm_b" in clock_errors:
                error = clock_errors["vm_b"]
                lower, upper = tcp_start - error, tcp_start + error
                candidates = [lower, upper]
                for p_start, p_end in probe_windows:
                    candidates.extend((p_start - observed, p_end - observed, p_start, p_end))
                possible = [candidate for candidate in candidates if lower <= candidate <= upper]
                active = min((overlap(candidate, observed) for candidate in possible), default=0.0)
            else:
                bracket = {"unavailable_reason": "timing window duration contradicts native receiver duration"}
        else:
            command = next((c for c in value.commands if c.unit_id == unit.unit_id and c.producer == "vm_a"
                            and isinstance(c.request.get("argv"), list)
                            and any("iperf3" in str(token) for token in c.request.get("argv", []))), None)
            mapping = next((m for m in timing.clock_mappings if str(getattr(m.vm_role, "value", m.vm_role)) == "vm_a"
                            and m.estimated_error_ms.value is not None), None) if timing else None
            omitted = sum(i.seconds for i in (tcp.receiver_intervals if tcp else ()) if i.omitted)
            duration = observed
            if command and mapping and omitted > 0 and duration > 0 and command.monotonic_started.value is not None and command.monotonic_finished.value is not None:
                c0 = command.monotonic_started.value + omitted
                c1 = command.monotonic_finished.value - duration
                if c0 <= c1:
                    error = mapping.estimated_error_ms.value / 1000
                    utc0 = mapping.utc_timestamp.timestamp() + c0 - mapping.monotonic_seconds - error
                    utc1 = mapping.utc_timestamp.timestamp() + c1 - mapping.monotonic_seconds + error
                    candidates = [utc0, utc1]
                    for p_start, p_end in probe_windows:
                        candidates.extend((p_start - duration, p_end - duration, p_start, p_end))
                    starts = [candidate for candidate in candidates if utc0 <= candidate <= utc1]
                    minimum = min((overlap(candidate, duration) for candidate in starts), default=0.0)
                    active = minimum
                    bracket = {"start_utc_lower": utc0, "start_utc_upper": utc1,
                               "coverage_lower_bound_seconds": minimum,
                               "exact_utc_start_available": False}
                else:
                    bracket = {"unavailable_reason": "conservative TCP measured-start bracket is empty"}
    if unit.kind not in {"continuous_day"}:
        if sample_support is None:
            active = None
        elif phase == "idle_latency":
            if sample_support < requested * .99:
                active = None
        elif active is not None:
            active = min(active, sample_support)
    if unit.kind == "probe_chunk":
        if probe_windows and sock.samples and timing:
            mapping = next((m for m in timing.clock_mappings if str(getattr(m.vm_role, "value", m.vm_role)) == "vm_a"
                            and m.estimated_error_ms.value is not None), None)
            if mapping:
                error = mapping.estimated_error_ms.value / 1000
                first = min(sample.tx_seconds for sample in sock.samples)
                last = max(sample.rx_seconds for sample in sock.samples)
                native_start = mapping.utc_timestamp.timestamp() + first - mapping.monotonic_seconds - error
                native_end = mapping.utc_timestamp.timestamp() + last - mapping.monotonic_seconds + error
                probe_windows = [(max(start, native_start), min(end, native_end))
                                 for start, end in probe_windows if min(end, native_end) > max(start, native_start)]
        active = sum(end - start for start, end in probe_windows) if probe_windows else None
    if unit.kind == "continuous_day":
        day_start = timing.measured_started_at.value.timestamp() if timing and timing.measured_started_at.value and "vm_b" in clock_errors else None
        day_end = timing.measured_finished_at.value.timestamp() if timing and timing.measured_finished_at.value and "vm_b" in clock_errors else None
        if day_start is not None and day_end is not None:
            day_start += clock_errors["vm_b"]
            day_end -= clock_errors["vm_b"]
        chunk_windows: list[tuple[float, float]] = []
        chunks = [item for item in value.measurement_units if item.kind == "probe_chunk"]
        for chunk in chunks:
            chunk_sock, _ = _parse_unit(value, chunk, {})
            chunk_timing = next((t for t in value.timings if t.unit_id == chunk.unit_id), None)
            starts_at = chunk_timing.probe_test_started_at.value if chunk_timing else None
            invocation_counts = _invocation_probe_counts(chunk_sock)
            runtimes = [count.runtime_seconds.value for count in invocation_counts if count.runtime_seconds.value is not None]
            chunk_error = next((m.estimated_error_ms.value / 1000 for m in chunk_timing.clock_mappings
                                if str(getattr(m.vm_role, "value", m.vm_role)) == "vm_a"
                                and m.estimated_error_ms.value is not None), None) if chunk_timing else None
            if starts_at is None or len(runtimes) != 1 or chunk_error is None or day_start is None or day_end is None:
                continue
            warmup_for_chunk = 5 if chunk == chunks[0] else 0
            valid_scope = len(invocation_counts) == 1 and invocation_counts[0].reporting_scope == "[Valid Duration]"
            start = starts_at.timestamp() + warmup_for_chunk + chunk_error
            end = starts_at.timestamp() + warmup_for_chunk * (1 if valid_scope else 0) + runtimes[0] - chunk_error
            if day_start is not None and day_end is not None:
                start, end = max(start, day_start), min(end, day_end)
            if chunk_sock.samples:
                mapping = next((m for m in chunk_timing.clock_mappings if str(getattr(m.vm_role, "value", m.vm_role)) == "vm_a"
                                and m.estimated_error_ms.value is not None), None)
                if mapping:
                    error = mapping.estimated_error_ms.value / 1000
                    native_start = mapping.utc_timestamp.timestamp() + min(s.tx_seconds for s in chunk_sock.samples) - mapping.monotonic_seconds - error
                    native_end = mapping.utc_timestamp.timestamp() + max(s.rx_seconds for s in chunk_sock.samples) - mapping.monotonic_seconds + error
                    start, end = max(start, native_start), min(end, native_end)
            segments = [(start, end)] if end > start else []
            for gap in chunk_timing.gaps if chunk_timing else []:
                left, right = gap.started_at.timestamp(), gap.finished_at.timestamp()
                remainder = []
                for seg_start, seg_end in segments:
                    if right <= seg_start or left >= seg_end:
                        remainder.append((seg_start, seg_end))
                    else:
                        if seg_start < left:
                            remainder.append((seg_start, left))
                        if right < seg_end:
                            remainder.append((right, seg_end))
                segments = remainder
            chunk_windows.extend(segments)
        merged: list[list[float]] = []
        for start, end in sorted(chunk_windows):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        active = sum(end - start for start, end in merged) if merged else None
    elif unit.kind == "probe_chunk":
        ratio = active / requested if active is not None and requested else None
    elif phase == "idle_latency":
        ratio = active / requested if active is not None and requested else None
    else:
        ratio = active / observed if active is not None and observed and observed > 0 else None
    if unit.kind == "continuous_day":
        ratio = active / requested if active is not None and requested else None
        complete = full_duration and ratio is not None and ratio >= .99 and tcp is not None and len(tcp.sessions) == 1
    elif phase == "idle_latency":
        complete = active is not None and active >= requested and sample_support is not None and sample_support >= requested * .99
    elif unit.kind == "probe_chunk":
        complete = active is not None
    else:
        complete = full_duration and ratio is not None and ratio >= .99
    reason = None if complete else "required measured duration or active-probe coverage is unproven"
    return {"requested_seconds": requested,
            "observed_receiver_seconds": _evidence(observed, "receiver measured duration is unavailable") if tcp_stats else _evidence(None, "receiver evidence unavailable"),
            "active_probe_seconds": _evidence(active, "active probe overlap is unavailable"),
            "active_probe_ratio": _evidence(ratio, "active probe coverage is unavailable"),
            "native_sample_support_seconds": _evidence(sample_support, "native sample bounds do not corroborate the acquisition window"),
            "complete": complete,
            "tcp_start_bracket": bracket,
            "gaps": [gap.model_dump(mode="json") for gap in timing.gaps] if timing else [],
            "unavailable_reason": reason}


def _sequence_timing_findings(value: ValidatorInput, parsed: dict[str, NativeParse]) -> list[ValidationFinding]:
    if value.mode not in {ExperimentMode.ACCEPTANCE, ExperimentMode.SHORT_STUDY, ExperimentMode.CALIBRATION}:
        return []
    findings: list[ValidationFinding] = []
    pause_seconds = 10
    ordered = value.measurement_units
    for previous, current in zip(ordered, ordered[1:]):
        previous_timing = next((item for item in value.timings if item.unit_id == previous.unit_id), None)
        current_timing = next((item for item in value.timings if item.unit_id == current.unit_id), None)
        previous_start = previous_timing.probe_test_started_at.value if previous_timing else None
        current_start = current_timing.probe_test_started_at.value if current_timing else None
        if previous_start is None or current_start is None:
            findings.append(_finding("timing.phase_order_unavailable", "warning",
                                     "ordered phase/trial starts are unavailable", unit=current.unit_id))
            continue
        if current_start <= previous_start:
            findings.append(_finding("timing.phase_order", "error", "phase/trial start order contradicts the declared protocol", unit=current.unit_id))
            continue
        previous_counts = _invocation_probe_counts(parsed.get(previous.unit_id, NativeParse()))
        runtimes = [item.runtime_seconds.value for item in previous_counts
                    if item.runtime_seconds.value is not None]
        if len(runtimes) != 1:
            findings.append(_finding("timing.phase_pause_unavailable", "warning",
                                     "preceding Sockperf runtime is unavailable to corroborate the inter-phase pause", unit=previous.unit_id))
            continue
        extra_warmup = 5 if len(previous_counts) == 1 and previous_counts[0].reporting_scope == "[Valid Duration]" else 0
        minimum_next_start = previous_start.timestamp() + runtimes[0] + extra_warmup + pause_seconds
        if current_start.timestamp() < minimum_next_start:
            findings.append(_finding("timing.phase_pause", "error",
                                     "next phase/trial starts before the measured Sockperf runtime plus the required 10-second pause",
                                     unit=current.unit_id))
    return findings


def validate_bundle(value: ValidatorInput, *, evaluated_at: datetime) -> ValidationResult:
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("evaluated_at must be timezone-aware")
    findings, records, provenance = _validate_artifacts(value)
    findings.extend(_validate_materialized_records(value, records))
    findings.extend(_validate_manifests(value))
    resolved_findings, resolved_obj = _resolved_snapshot_findings(value)
    findings.extend(resolved_findings)
    findings.extend(_command_findings(value))
    if value.retrieval_failure is not None:
        findings.append(_finding("retrieval.failed", "warning", "artifact retrieval failure is recorded; bundle cannot be complete"))
    units_payload: list[dict[str, Any]] = []
    any_measurement = False
    all_complete = True
    parsed_sock: dict[str, NativeParse] = {}
    for unit in value.measurement_units:
        sock, tcp = _parse_unit(value, unit, records)
        if value.mode in {ExperimentMode.ACCEPTANCE, ExperimentMode.SHORT_STUDY, ExperimentMode.CALIBRATION}:
            parsed_sock[unit.unit_id] = NativeParse(probe_counts=sock.probe_counts, findings=sock.findings,
                source=sock.source, sessions=sock.sessions)
        findings.extend(sock.findings)
        if tcp:
            findings.extend(tcp.findings)
        measured_samples = _measured_samples(value, unit, sock)
        rtt = rtt_statistics([s.rtt_us for s in measured_samples])
        tcp_stats = _tcp_statistics(tcp)
        counts = []
        for item in sock.probe_counts:
            count = {name: getattr(item, name).model_dump(mode="json") for name in
                     ("sent", "received", "dropped", "duplicated", "out_of_order", "skipped", "runtime_seconds")}
            runtime = item.runtime_seconds.value
            sent = item.sent.value
            count["reporting_scope"] = item.reporting_scope
            generation_scope = item.reporting_scope == "[Valid Duration]" or (
                len(sock.probe_counts) == 1 and item.reporting_scope in {"[Unscoped]", "one Sockperf invocation"}
                and not any(c.reporting_scope == "[Valid Duration]" for c in sock.probe_counts))
            count["generation_ratio"] = _evidence(sent / (1000 * runtime), "sent count/runtime unavailable") if generation_scope and sent is not None and runtime else _evidence(None, "not the measured valid-duration reporting scope")
            counts.append(count)
            if generation_scope and (sent is None or not runtime):
                findings.append(_finding("generation.unavailable", "warning",
                                         "native valid-duration sent count or runtime is unavailable", unit=unit.unit_id))
            if generation_scope and sent is not None and runtime and sent / (1000 * runtime) < .95:
                findings.append(_finding("generation.below_target", "warning", "probe generation is below 95% of target", unit=unit.unit_id))
        if not sock.probe_counts and unit.kind != "continuous_day":
            findings.append(_finding("generation.unavailable", "warning", "actual Sockperf sent/runtime evidence is unavailable", unit=unit.unit_id))
        elif sock.probe_counts and unit.kind != "continuous_day" and not any(
            item.reporting_scope in {"[Valid Duration]", "[Unscoped]"}
            and item.sent.value is not None and item.runtime_seconds.value is not None
            for item in sock.probe_counts
        ):
            findings.append(_finding("generation.unavailable", "warning", "valid-duration sent/runtime evidence is unavailable", unit=unit.unit_id))
        coverage = _coverage(value, unit, sock, tcp)
        if sock.samples or (tcp and (tcp.receiver_intervals or tcp.receiver_totals)):
            any_measurement = True
        if not coverage["complete"]:
            all_complete = False
            findings.append(_finding("timing.coverage_unavailable", "warning", coverage["unavailable_reason"] or "measurement coverage incomplete", unit=unit.unit_id))
        timing = next((t for t in value.timings if t.unit_id == unit.unit_id), None)
        alignment_errors = [m.estimated_error_ms.value for m in timing.clock_mappings] if timing else []
        alignment = (all(error is not None and error <= 100 for error in alignment_errors)
                     and {str(getattr(m.vm_role, "value", m.vm_role)) for m in timing.clock_mappings} >= {"vm_a", "vm_b"}) if timing else False
        units_payload.append({
            "unit_id": unit.unit_id, "kind": unit.kind, "phase": str(unit.phase), "streams": unit.streams,
            "trial_index": unit.trial_index, "deployment_id": value.identity.deployment_id,
            "rtt": rtt,
            "tcp": {key: val.model_dump(mode="json") if hasattr(val, "model_dump") else val for key, val in tcp_stats.items()},
            "probe_counts": counts,
            "sources": [
                {"artifact_id": record.artifact_id, "path": record.path, "sha256": record.sha256.value,
                 "unit_id": record.unit_id, "producer": str(record.producer),
                 "binary_version": _source(record, value).binary_version,
                 "source_identity": _source(record, value).source_identity}
                for record in value.artifact_records if record.unit_id == unit.unit_id
                or (unit.kind == "probe_chunk" and record.artifact_id in {"day-probe-csv", "day-probe-log"})
            ],
            "receiver_interval_count": len(tcp.receiver_intervals) if tcp else 0,
            "sample_count": len(measured_samples),
            "native_sample_source": sock.source.__dict__ if sock.source else None,
            "coverage": coverage,
            "alignment": {"one_second_eligible": _evidence(alignment, "both role clock mappings with error <=100 ms are required"),
                          "clock_error_ms": alignment_errors},
            "findings": [],
            "inflation_baseline_valid": bool(coverage["complete"] and not any(
                f.severity == "error" for f in (*sock.findings, *(tcp.findings if tcp else ())))),
        })
    findings.extend(_sequence_timing_findings(value, parsed_sock))
    if not value.resolved_sha256.value or not value.implementation_git.commit.value or not value.design_git.commit.value:
        findings.append(_finding("provenance.incomplete", "warning", "configuration/design/implementation provenance is incomplete"))
        all_complete = False
    benchmark = resolved_obj.get("benchmark", {}) if isinstance(resolved_obj, dict) else {}
    common_n = benchmark.get("common_n", {}) if isinstance(benchmark, dict) else {}
    if common_n.get("state") == "frozen":
        expected_n = common_n.get("common_n")
        for unit in value.measurement_units:
            if unit.phase == "multi_flow" and unit.streams != expected_n:
                findings.append(_finding("calibration.frozen_n_mismatch", "error", "multi-flow stream count contradicts frozen CommonNState", unit=unit.unit_id))
                all_complete = False
    elif value.mode == ExperimentMode.SHORT_STUDY and any(
        unit.phase == "multi_flow" and unit.streams is None for unit in value.measurement_units
    ):
        findings.append(_finding("calibration.common_n_pending", "warning",
                                 "multi-flow N remains unresolved; no stream count is inferred"))
    by_unit = {unit["unit_id"]: unit for unit in units_payload}
    idle_unit = next((unit for unit in units_payload if unit["phase"] == "idle_latency"), None)
    idle_id = idle_unit["unit_id"] if idle_unit else None
    idle_findings = [finding for finding in findings if finding.unit_id in {None, idle_id}]
    idle_valid = bool(idle_unit and idle_unit["coverage"]["complete"] and not idle_findings)
    idle_median = idle_unit["rtt"]["median_us"].value if idle_valid else None
    for unit in units_payload:
        unit_findings = [finding for finding in findings if finding.unit_id in {None, unit["unit_id"]}]
        unit["data_validity"] = ("invalid" if any(f.severity == "error" for f in unit_findings)
                                 else "partial" if unit_findings or not unit["coverage"]["complete"]
                                 else "valid" if unit["sample_count"] or unit["receiver_interval_count"]
                                 else "unavailable")
        phase = unit["phase"]
        if value.mode == ExperimentMode.SHORT_STUDY and phase in {"single_flow", "multi_flow"}:
            loaded = unit["rtt"]["median_us"].value
            reason = "valid same-deployment idle and loaded median RTT are required"
            loaded_valid = unit["data_validity"] == "valid"
            unit["rtt"]["latency_inflation_us"] = _evidence(loaded - idle_median, reason) if loaded is not None and idle_median is not None and loaded_valid else _evidence(None, reason)
        else:
            unit["rtt"]["latency_inflation_us"] = _evidence(None, "inflation is unavailable outside valid short-study phases")
    structural = any(f.severity == "error" for f in findings)
    incomplete = any(f.severity == "warning" for f in findings) or not all_complete
    if structural:
        data_validity = DataValidity.INVALID
        data_reasons = ["one or more trust, integrity, command or native evidence checks failed"]
    elif not any_measurement:
        data_validity = DataValidity.UNAVAILABLE
        data_reasons = ["no assessable native measurement evidence"]
    elif incomplete:
        data_validity = DataValidity.PARTIAL
        data_reasons = ["trusted measurement evidence is incomplete"]
    else:
        data_validity = DataValidity.VALID
        data_reasons = []
    if value.mode != ExperimentMode.SHORT_STUDY:
        comparison, comparison_reasons = ComparisonEligibility.INELIGIBLE, ["mode is not a fresh short-study comparison unit"]
    else:
        comparison, comparison_reasons = ComparisonEligibility.UNAVAILABLE, ["matched provider evidence has not been evaluated"]
    status = ValidationStatus(data_validity=data_validity, data_reasons=data_reasons,
                              comparison_eligibility=comparison, comparison_reasons=comparison_reasons)
    for unit in units_payload:
        unit_id = unit["unit_id"]
        unit["findings"] = [finding.model_dump(mode="json") for finding in findings
                            if finding.unit_id in {None, unit_id}]
    metrics = {
        "format_version": 1,
        "identity": value.identity.model_dump(mode="json") | {"provider": str(value.provider), "scenario": str(value.scenario), "mode": str(value.mode), "context": value.context},
        "common_n": common_n if common_n else {"state": "pending", "common_n": None},
        "commands": [command.model_dump(mode="json") for command in value.commands],
        "execution": {"execution_state": str(value.execution_state), "primary_failure": value.failure.model_dump(mode="json") if value.failure else None,
                      "retrieval_failure": value.retrieval_failure.model_dump(mode="json") if value.retrieval_failure else None,
                      "cleanup_state": str(value.cleanup_state), "cleanup_failure": value.cleanup_failure.model_dump(mode="json") if value.cleanup_failure else None,
                      "bundle_complete": value.cleanup_state in {CleanupState.SUCCEEDED, CleanupState.NOT_REQUIRED}
                      and value.execution_state == ExecutionState.SUCCEEDED
                      and value.retrieval_failure is None and data_validity == DataValidity.VALID},
        "provenance": provenance, "units": units_payload,
        "diagnostics": [item.model_dump(mode="json") for item in value.diagnostics],
        "research_eligible_mode": value.mode == ExperimentMode.SHORT_STUDY,
    }
    return ValidationResult(schema_version=2, run_id=value.identity.run_id, mode=value.mode,
                           validation_status=status, findings=sorted(findings, key=lambda f: (f.unit_id or "", f.artifact_id or "", f.code, f.message)),
                           metrics=StructuredEvidence(value=metrics), evaluated_at=evaluated_at.astimezone(timezone.utc))


def evaluate_comparison(inputs: Sequence[ValidatorInput], results: Sequence[ValidationResult]) -> dict[str, ValidationResult]:
    if len(inputs) != len(results) or not inputs:
        raise ValueError("comparison requires matching nonempty input and result collections")
    by_run: dict[str, ValidationResult] = {}
    for item, result in zip(inputs, results):
        run_id = item.identity.run_id
        if run_id in by_run or result.run_id != run_id:
            raise ValueError("comparison has duplicate or mismatched run identity")
        if result.mode != item.mode:
            raise ValueError("comparison result mode contradicts its ValidatorInput")
        by_run[run_id] = result
        metrics = result.metrics.value or {}
        result_identity = metrics.get("identity", {})
        expected_identity = item.identity.model_dump(mode="json") | {
            "provider": str(item.provider), "scenario": str(item.scenario),
            "mode": str(item.mode), "context": item.context,
        }
        for key in ("experiment_id", "config_id", "campaign_id", "observation_id", "run_id", "deployment_id",
                    "provider", "scenario", "mode", "context"):
            if result_identity.get(key) != expected_identity.get(key):
                raise ValueError(f"comparison result {key} identity contradicts its ValidatorInput")
        result_sources = (metrics.get("provenance", {}) or {}).get("source_refs", [])
        input_sources = [{"artifact_id": record.artifact_id, "path": record.path,
                          "sha256": record.sha256.value, "unit_id": record.unit_id,
                          "producer": str(record.producer)} for record in sorted(
                              item.artifact_records, key=lambda record: (record.unit_id or "", record.artifact_id, record.path))]
        if result_sources != input_sources or (metrics.get("provenance", {}) or {}).get("config_sha256") != item.config_sha256:
            raise ValueError("comparison result source/config evidence contradicts its ValidatorInput")
        expected_commands = [command.model_dump(mode="json") for command in item.commands]
        if metrics.get("commands", []) != expected_commands:
            raise ValueError("comparison result command/build evidence contradicts its ValidatorInput")
        result_units = metrics.get("units", [])
        input_units = [(unit.unit_id, str(getattr(unit.phase, "value", unit.phase)), unit.kind, unit.streams, unit.measured_seconds)
                       for unit in item.measurement_units]
        output_units = [(unit.get("unit_id"), unit.get("phase"), unit.get("kind"),
                         unit.get("streams"), (unit.get("coverage", {}) or {}).get("requested_seconds")) for unit in result_units]
        if output_units != input_units:
            raise ValueError("comparison result unit evidence contradicts its ValidatorInput")
    first = inputs[0]
    ineligible: list[str] = []
    unavailable: list[str] = []
    if any(item.mode != ExperimentMode.SHORT_STUDY or item.context != "standard" for item in inputs):
        ineligible.append("only standard short_study results are comparable")
    provider_set = {str(item.provider) for item in inputs}
    if provider_set != {"aws", "azure", "gcp"}:
        ineligible.append("matched comparison requires the full selected AWS/Azure/GCP cohort")
    for item in inputs[1:]:
        if (item.identity.campaign_id, item.identity.config_id, item.identity.experiment_id, item.scenario,
            item.context, item.enabled_phases, item.schedule_entry.value,
            item.config_sha256, item.measurement_direction, item.expected_vm_roles,
            [(u.unit_id, u.phase, u.kind, u.streams, u.measured_seconds) for u in item.measurement_units]) != (
            first.identity.campaign_id, first.identity.config_id, first.identity.experiment_id, first.scenario,
            first.context, first.enabled_phases, first.schedule_entry.value,
            first.config_sha256, first.measurement_direction, first.expected_vm_roles,
            [(u.unit_id, u.phase, u.kind, u.streams, u.measured_seconds) for u in first.measurement_units]):
            ineligible.append("provider inputs do not share campaign/configuration/scenario/slot/phase/settings")
            break
    if len(provider_set) != len(inputs):
        ineligible.append("comparison providers must be unique")
    for result in results:
        if result.validation_status.data_validity != DataValidity.VALID:
            ineligible.append("all compared data must be valid")
        execution = (result.metrics.value or {}).get("execution")
        if not isinstance(execution, dict):
            unavailable.append(f"{result.run_id} lacks execution/cleanup evidence")
        elif execution.get("execution_state") != "succeeded" or execution.get("bundle_complete") is not True:
            ineligible.append("execution or cleanup is incomplete")
    schedule_values = [item.schedule_entry.value for item in inputs]
    if any(entry is None for entry in schedule_values):
        unavailable.append("a shared scheduled slot is required")
    elif len({(entry.entry_id, entry.source_sha256, entry.row_sha256) for entry in schedule_values}) != 1:
        ineligible.append("provider inputs do not share the same scheduled row")
    n_values: list[int] = []
    n_evidence: list[tuple[Any, ...]] = []
    for result in results:
        common_n = (result.metrics.value or {}).get("common_n", {})
        if not isinstance(common_n, dict) or common_n.get("state") != "frozen" or common_n.get("common_n") not in {2, 4, 8}:
            unavailable.append(f"{result.run_id} has no frozen CommonNState")
        else:
            n_values.append(common_n["common_n"])
            required = (common_n.get("source_campaign_id"), common_n.get("source_sha256"), common_n.get("plateau_found"))
            if (required[0] != (result.metrics.value.get("identity", {}) or {}).get("campaign_id")
                    or not isinstance(required[1], str) or len(required[1]) != 64
                    or any(char not in "0123456789abcdef" for char in required[1])
                    or required[2] not in {True, False}):
                unavailable.append(f"{result.run_id} CommonNState lacks source campaign/hash/plateau evidence")
            n_evidence.append(required)
    if len(n_values) == len(results) and (len(set(n_values)) != 1 or any(
        next((unit.streams for unit in item.measurement_units if unit.phase == "multi_flow"), None) != n_values[index]
        for index, item in enumerate(inputs)
    )):
        ineligible.append("provider inputs contradict the shared frozen CommonNState")
    if n_evidence and len(set(n_evidence)) != 1:
        ineligible.append("provider results cite incompatible CommonNState source campaign/hash/plateau evidence")
    starts_by_unit: dict[str, dict[str, datetime]] = {}
    for item in inputs:
        timing_by_unit = {timing.unit_id: timing for timing in item.timings}
        for unit in item.measurement_units:
            timing = timing_by_unit.get(unit.unit_id)
            if timing is None:
                unavailable.append(f"{item.provider}/{unit.unit_id} timing is missing")
                continue
            exact_start = timing.measured_started_at.value
            if exact_start is None:
                unavailable.append(f"{item.provider}/{unit.unit_id} actual start timestamp is unavailable")
                continue
            starts_by_unit.setdefault(unit.unit_id, {})[str(item.provider)] = exact_start
    skews: dict[str, float] = {}
    for unit_id, starts in starts_by_unit.items():
        if set(starts) != provider_set:
            unavailable.append(f"{unit_id} does not have starts for every provider")
            continue
        skews[unit_id] = (max(starts.values()) - min(starts.values())).total_seconds()
    if any(skew > 300 for skew in skews.values()):
        ineligible.append(f"measurement start skew exceeds 300 seconds ({max(skews.values()):g}s)")
    degraded = any(60 < skew <= 300 for skew in skews.values())
    if ineligible:
        eligibility = ComparisonEligibility.INELIGIBLE
        reasons = sorted(set(ineligible))
    elif unavailable or not skews:
        eligibility = ComparisonEligibility.UNAVAILABLE
        reasons = sorted(set(unavailable or ["matched measurement starts are unavailable"]))
    else:
        eligibility = ComparisonEligibility.ELIGIBLE
        reasons = ["measurement start skew is timing-eligible" if degraded else "all matched phase starts meet the 60-second target"]
    output: dict[str, ValidationResult] = {}
    for item in inputs:
        result = by_run[item.identity.run_id]
        additions = []
        if eligibility == ComparisonEligibility.ELIGIBLE and degraded:
            additions.append(_finding("comparison.start_target_degraded", "warning", f"matched phase skew exceeds 60 seconds: {max(skews.values()):g}s"))
        elif eligibility == ComparisonEligibility.INELIGIBLE:
            additions.extend(_finding("comparison.ineligible", "error", reason) for reason in reasons)
        elif eligibility == ComparisonEligibility.UNAVAILABLE:
            additions.extend(_finding("comparison.start_unavailable", "warning", reason) for reason in reasons)
        updated_status = ValidationStatus(data_validity=result.validation_status.data_validity,
                                          data_reasons=result.validation_status.data_reasons,
                                          comparison_eligibility=eligibility,
                                          comparison_reasons=reasons)
        output[result.run_id] = result.model_copy(update={"validation_status": updated_status,
                                                         "findings": [*result.findings, *additions]})
    return output
