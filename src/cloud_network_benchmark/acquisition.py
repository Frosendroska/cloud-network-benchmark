"""Conservative replay of source-emitted acquisition evidence.

The module intentionally treats observation logs as the authority.  Runtime timing
summaries and caller-constructed windows are not accepted by these functions.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence

from .contracts.artifacts import ArtifactRecord, resolve_artifact_path
from .contracts.measurements import AcquisitionSource, ClockMapping, CommandEvidence
from .contracts.common import VmRole

MAX_RECORD_BYTES = 4096
PREFIX = b"CNB_ACQ_V1 "


@dataclass(frozen=True)
class ParsedRecord:
    value: Mapping[str, object]
    byte_offset: int
    byte_length: int


@dataclass(frozen=True)
class SourceWindow:
    kind: str
    tool: str
    producer: str
    boot_id: str
    clock_domain: str
    session_id: str | None
    stream_id: str | None
    start_earliest_ns: int
    start_latest_ns: int
    end_earliest_ns: int
    end_latest_ns: int
    omitted: bool | None
    source_byte_offset: int
    source_byte_length: int

    def __post_init__(self) -> None:
        vals = (self.start_earliest_ns, self.start_latest_ns, self.end_earliest_ns, self.end_latest_ns)
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in vals):
            raise ValueError("source window bounds must be nonnegative integer nanoseconds")
        if self.start_earliest_ns > self.start_latest_ns or self.end_earliest_ns > self.end_latest_ns:
            raise ValueError("source window bounds must be ordered")

    @property
    def has_positive_inner_support(self) -> bool:
        return self.omitted is not True and self.start_latest_ns < self.end_earliest_ns


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def parse_observation_stream(stream) -> Iterator[ParsedRecord]:
    """Yield strict bounded CNB_ACQ_V1 records with exact original byte offsets."""
    offset = 0
    while True:
        line = stream.readline(MAX_RECORD_BYTES + 1)
        if not line:
            return
        start = offset
        offset += len(line)
        if len(line) > MAX_RECORD_BYTES:
            if line.startswith(PREFIX):
                raise ValueError(f"acquisition record exceeds {MAX_RECORD_BYTES} bytes at byte {start}")
            while line and not line.endswith(b"\n"):
                line = stream.readline(MAX_RECORD_BYTES + 1)
                offset += len(line)
            continue
        if not line.startswith(PREFIX):
            continue
        if not line.endswith(b"\n"):
            raise ValueError(f"truncated acquisition record at byte {start}")
        try:
            value = json.loads(line[len(PREFIX):-1].decode("utf-8"), object_pairs_hook=_unique_json_object,
                               parse_constant=lambda item: (_ for _ in ()).throw(ValueError(f"invalid number {item}")))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"invalid acquisition record at byte {start}: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"acquisition record at byte {start} must be a JSON object")
        if value.get("format") != 1 or isinstance(value.get("format"), bool):
            raise ValueError(f"unsupported acquisition record format at byte {start}")
        yield ParsedRecord(value, start, len(line))


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and nonnegative")
    return float(value)


def _native_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative native integer")
    return value


def _same_common(record: Mapping[str, object], source: AcquisitionSource) -> None:
    expected = {"tool": source.tool, "semantics_id": source.semantics_id,
                "invocation_id": source.invocation_id, "boot_id": source.boot_id,
                "clock": source.clock_domain}
    if any(record.get(key) != value for key, value in expected.items()):
        raise ValueError("observation tool, semantics, invocation, boot, or clock identity mismatch")
    _native_int(record.get("pid"), "pid")


def _sockperf_window(record: ParsedRecord, source: AcquisitionSource) -> SourceWindow:
    value = record.value
    _same_common(value, source)
    if value.get("record_kind") != "probe_window" or value.get("termination_reason") != "timer":
        raise ValueError("Sockperf lifecycle is incomplete or not a normal time-based termination")
    sender = _native_int(value.get("sender_enabled_ns"), "sender_enabled_ns")
    receiver = _native_int(value.get("receiver_enabled_ns"), "receiver_enabled_ns")
    termination = _native_int(value.get("termination_ns"), "termination_ns")
    native_start = _native_int(value.get("native_filter_start_ns"), "native_filter_start_ns")
    native_end = _native_int(value.get("native_filter_end_ns"), "native_filter_end_ns")
    start, end = max(sender, receiver, native_start), min(termination, native_end)
    if end < start:
        raise ValueError("Sockperf native lifecycle and filter bounds do not overlap")
    return SourceWindow("probe_enabled", "sockperf", str(source.producer), source.boot_id,
        source.clock_domain, None, None, start, start, end, end, None, record.byte_offset, record.byte_length)


def _iperf_windows(records: Iterator[ParsedRecord], source: AcquisitionSource) -> Iterator[SourceWindow]:
    origins: dict[tuple[str, str], int] = {}
    last_interval: dict[str, tuple[int, int]] = {}
    interval_count = 0
    for record in records:
        value = record.value
        _same_common(value, source)
        kind = value.get("record_kind")
        cookie = value.get("cookie")
        if not isinstance(cookie, str) or cookie != source.session_id:
            raise ValueError("iperf native session cookie does not match source reference")
        if kind == "stats_origin":
            phase = value.get("phase")
            if phase not in {"initial", "post_omit"}:
                raise ValueError("unsupported iperf stats origin phase")
            stream = value.get("stream_id")
            if not isinstance(stream, str) or not stream:
                raise ValueError("iperf stats origin lacks stream identity")
            origin = _native_int(value.get("origin_us"), "origin_us")
            identity = (str(phase), stream)
            if identity in origins and origins[identity] != origin:
                raise ValueError("conflicting duplicate iperf stats origin")
            origins[identity] = origin
            continue
        if kind != "receiver_interval":
            continue
        stream = value.get("stream_id")
        if not isinstance(stream, str) or not stream:
            raise ValueError("iperf receiver interval lacks stream identity")
        post_omit_origin = origins.get(("post_omit", stream))
        if post_omit_origin is None:
            raise ValueError("iperf post-omit statistic origin is missing before receiver interval")
        start_us = _native_int(value.get("interval_start_us"), "interval_start_us")
        end_us = _native_int(value.get("interval_end_us"), "interval_end_us")
        if end_us < start_us:
            raise ValueError("iperf receiver interval is non-monotonic")
        previous = last_interval.get(stream)
        if previous is not None and (start_us, end_us) == previous:
            raise ValueError("duplicate iperf receiver interval record")
        if previous is not None and (start_us < previous[0] or end_us < previous[1]):
            raise ValueError("conflicting or non-monotonic iperf receiver interval sequence")
        last_interval[stream] = (start_us, end_us)
        if start_us < post_omit_origin:
            raise ValueError("iperf receiver interval precedes the native post-omit origin")
        _native_int(value.get("bytes"), "bytes")
        if value.get("role") != "receiver" or value.get("protocol") != "tcp":
            raise ValueError("iperf interval is not native TCP receiver evidence")
        for endpoint in ("local_host", "local_port", "remote_host", "remote_port"):
            if not isinstance(value.get(endpoint), str) or not value[endpoint]:
                raise ValueError("iperf receiver interval lacks native endpoint identity")
        omitted = value.get("omitted")
        if not isinstance(omitted, bool):
            raise ValueError("iperf omitted marker must be boolean")
        start_ns, end_ns = start_us * 1000, end_us * 1000
        interval_count += 1
        yield SourceWindow("receiver_measured", "iperf3", str(source.producer), source.boot_id,
            source.clock_domain, source.session_id, stream, start_ns, start_ns + 1000,
            end_ns, end_ns + 1000, omitted, record.byte_offset, record.byte_length)
    if not any(phase == "post_omit" for phase, _stream in origins):
        raise ValueError("iperf post-omit statistic origin is missing")
    if interval_count == 0:
        raise ValueError("iperf receiver interval evidence is missing")


def _validate_anchor(record: ParsedRecord, source: AcquisitionSource) -> None:
    value = record.value
    _same_common(value, source)
    before = _native_int(value.get("mono_before_ns"), "mono_before_ns")
    realtime = _native_int(value.get("realtime_ns"), "realtime_ns")
    after = _native_int(value.get("mono_after_ns"), "mono_after_ns")
    if before > after or realtime == 0:
        raise ValueError("clock anchor bounds are invalid")


def _expected_build_identity(tool: str) -> tuple[str, str, str, str]:
    if tool == "sockperf":
        return ("5ebd327da983225321818c0355db922515e026bd",
                "tool_patches/sockperf-3.10-acquisition.patch",
                "3f0bb59a3c70b247e5ebde77254e1427c35dc6ec39e2a78a3c779cff7015a285",
                "a35a1daf310551b9f675af4fb7d95a6a10389fda4c7605d70a21554be0773f8e")
    return ("2acfcfe94e928e74542c9f107e02aa6dd4748a79",
            "tool_patches/iperf-3.17.1-acquisition.patch",
            "2619b1070eabbef72dd648372f7eecec2140d08f87e5a237685d98c5802f49df",
            "d6b4d2c7b20157c5b7fbd2de66f22bd3428958b5f2e28899151b6e22745886fe")


def iter_source_windows(child_root: Path, source: AcquisitionSource,
                        artifact_records: Sequence[ArtifactRecord], commands: Sequence[CommandEvidence],
                        build_evidence: Sequence[object], clock_mappings: Sequence[ClockMapping] = ()) -> Iterator[SourceWindow]:
    """Verify source references and stream raw-derived windows; never trust declared totals."""
    by_id: dict[str, list[ArtifactRecord]] = {}
    for item in artifact_records:
        by_id.setdefault(item.artifact_id, []).append(item)

    def one(artifact_id: str) -> ArtifactRecord:
        matches = by_id.get(artifact_id, [])
        if len(matches) != 1:
            raise ValueError(f"artifact reference {artifact_id!r} does not resolve uniquely")
        return matches[0]

    log = one(source.log_artifact_id)
    if log.path != source.log_path or log.producer != source.producer or log.deployment_id == "":
        raise ValueError("acquisition log artifact identity does not match source")
    native = [one(item) for item in source.native_artifact_ids]
    if log.kind != f"{source.tool}-observation-stderr":
        raise ValueError("acquisition log has an unexpected artifact kind")
    log.verify_materialized_file(child_root)
    command = [item for item in commands if item.result.get("action_id") == source.command_action_id
               or item.request.get("action_id") == source.command_action_id]
    if len(command) != 1 or command[0].deployment_id != log.deployment_id:
        raise ValueError("acquisition source must match one command in its deployment")
    outcome = (command[0].result.get("outcome") if isinstance(command[0].result, Mapping)
               else getattr(command[0].result, "outcome", None))
    outcome = getattr(outcome, "value", outcome)
    if outcome is not None and outcome != "succeeded":
        raise ValueError("acquisition source command did not complete successfully")
    if command[0].producer != source.producer:
        raise ValueError("acquisition command producer does not match source")
    if log.unit_id not in {None, command[0].unit_id}:
        raise ValueError("acquisition log unit does not match its producing command")
    for item in native:
        if item.deployment_id != log.deployment_id:
            raise ValueError("native acquisition artifacts must share the source deployment")
        if source.tool == "sockperf" and (item.producer != source.producer or item.kind != "sockperf-csv"):
            raise ValueError("Sockperf source must reference its native client CSV")
        if source.tool == "iperf3" and (item.producer != VmRole.VM_A or item.kind not in {"iperf3-json", "iperf3-client-jsonl"}):
            raise ValueError("iperf3 receiver source must reference a native client artifact")
        if source.tool == "sockperf" and item.unit_id not in {None, command[0].unit_id}:
            raise ValueError("Sockperf native artifact must match its producing measurement unit")
        if source.tool == "iperf3":
            client_command = [row for row in commands if row.deployment_id == log.deployment_id
                and row.producer == VmRole.VM_A and row.unit_id == item.unit_id
                and (row.result.get("action_id") or row.request.get("action_id") or "").startswith("iperf3-")]
            if len(client_command) != 1:
                raise ValueError("iperf3 native artifact must match one client command in its unit")
        item.verify_materialized_file(child_root)
        if source.tool == "iperf3" and not _file_contains(
            resolve_artifact_path(child_root, item.path), source.session_id.encode("ascii")
        ):
            raise ValueError("native iperf client output does not corroborate the referenced session cookie")
    builds = [item for item in build_evidence if getattr(item, "artifact_id", None) == source.build_artifact_id
              and getattr(item, "role", None) == source.producer]
    if len(builds) != 1:
        raise ValueError("acquisition source must match one observed build artifact")
    observed = builds[0]
    expected_semantics = "sockperf-3.10-enabled-v1" if source.tool == "sockperf" else "iperf-3.17.1-stats-v1"
    expected_commit, expected_patch_path, expected_patch_sha, expected_source_sha = _expected_build_identity(source.tool)
    if (getattr(observed, "tool", None) != source.tool
            or getattr(observed, "observation_capability", None) != "CNB_ACQ_V1"
            or getattr(observed, "patch_path", None) != expected_patch_path
            or getattr(observed, "patch_sha256", None) != expected_patch_sha
            or getattr(observed, "source_sha256", None) != expected_source_sha
            or getattr(observed, "source_reference", None) != f"{('sockperf-' if source.tool == 'sockperf' else 'iperf-')}{expected_commit}"
            or getattr(observed, "build_configuration", None) is None
            or not getattr(observed, "compiler_version", "").strip()
            or getattr(observed, "binary_sha256", None) is None
            or getattr(observed, "upstream_commit", None) != expected_commit
            or getattr(observed, "observation_semantics", None) != expected_semantics):
        raise ValueError("installed build does not prove the requested observation capability")
    for index in source.mapping_indexes:
        if index >= len(clock_mappings) or clock_mappings[index].vm_role != source.producer:
            raise ValueError("acquisition clock mapping does not match producer domain")

    def records():
        with resolve_artifact_path(child_root, log.path).open("rb") as stream:
            yield from parse_observation_stream(stream)

    if source.tool == "sockperf":
        found = 0
        anchor_pid: int | None = None
        for record in records():
            if record.value.get("record_kind") == "clock_anchor":
                _validate_anchor(record, source)
                pid = _native_int(record.value.get("pid"), "pid")
                if anchor_pid is not None and anchor_pid != pid:
                    raise ValueError("conflicting Sockperf source process identity")
                anchor_pid = pid
                continue
            if record.value.get("record_kind") != "probe_window":
                continue
            found += 1
            if found > 1:
                raise ValueError("conflicting duplicate Sockperf probe window records")
            if anchor_pid is None or _native_int(record.value.get("pid"), "pid") != anchor_pid:
                raise ValueError("Sockperf window is not linked to its clock-anchor process")
            yield _sockperf_window(record, source)
        if not found:
            raise ValueError("Sockperf probe window observation is missing")
        return
    def iperf_records():
        anchors: dict[str | None, Mapping[str, object]] = {}
        anchor_pids: dict[str | None, int] = {}
        found_session_anchor = False
        for record in records():
            if record.value.get("record_kind") == "clock_anchor":
                cookie = record.value.get("cookie")
                if cookie != source.session_id:
                    continue
                _validate_anchor(record, source)
                previous = anchors.get(cookie)
                if previous is not None and previous != record.value:
                    raise ValueError("conflicting duplicate iperf clock anchor")
                anchors[cookie] = record.value
                anchor_pids[cookie] = _native_int(record.value.get("pid"), "pid")
                found_session_anchor = True
                continue
            if record.value.get("cookie") != source.session_id:
                continue
            if source.session_id not in anchor_pids or _native_int(record.value.get("pid"), "pid") != anchor_pids[source.session_id]:
                raise ValueError("iperf session record is not linked to its clock-anchor process")
            yield record
        if not found_session_anchor:
            raise ValueError("iperf session clock anchor is missing")
    yield from _iperf_windows(iperf_records(), source)


def bind_acquisition_sources(*, child_root: Path, run_identity: object, commands: Sequence[CommandEvidence],
                             build_evidence: Sequence[object], source_artifact_records: Sequence[ArtifactRecord],
                             receiver_command_evidence: Sequence[CommandEvidence] = (),
                             clock_mappings: Sequence[ClockMapping] = ()) -> tuple[AcquisitionSource, ...]:
    """Construct references only from materialized records already present in the run context."""
    deployment_id = getattr(run_identity, "deployment_id", None)
    if not isinstance(deployment_id, str):
        raise ValueError("run identity lacks deployment_id")
    candidates = [record for record in source_artifact_records if record.deployment_id == deployment_id
                  and record.kind in {"sockperf-observation-stderr", "iperf3-observation-stderr"}
                  and record.closed and record.retrieval_state == "verified"]
    result: list[AcquisitionSource] = []
    all_commands = (*commands, *receiver_command_evidence)
    for record in candidates:
        tool = "sockperf" if record.kind.startswith("sockperf") else "iperf3"
        build = next((item for item in build_evidence if getattr(item, "role", None) == record.producer
                      and getattr(item, "tool", None) == tool), None)
        # Existing evidence cannot be promoted unless it contains the observed patched build fields.
        if not build or not getattr(build, "observation_capability", None):
            continue
        record.verify_materialized_file(child_root)
        anchors: dict[str | None, ParsedRecord] = {}
        observed_sessions: set[str] = set()
        with resolve_artifact_path(child_root, record.path).open("rb") as stream:
            for raw in parse_observation_stream(stream):
                kind = raw.value.get("record_kind")
                if kind == "clock_anchor":
                    try:
                        value = raw.value
                        expected_semantics = "sockperf-3.10-enabled-v1" if tool == "sockperf" else "iperf-3.17.1-stats-v1"
                        if value.get("tool") != tool or value.get("semantics_id") != expected_semantics:
                            continue
                        if value.get("clock") != "CLOCK_MONOTONIC" or not isinstance(value.get("invocation_id"), str):
                            continue
                        boot_id = value.get("boot_id")
                        if not isinstance(boot_id, str) or len(boot_id) != 36 or not isinstance(value.get("pid"), int):
                            continue
                        _native_int(value.get("pid"), "pid")
                        _native_int(value.get("mono_before_ns"), "mono_before_ns")
                        _native_int(value.get("realtime_ns"), "realtime_ns")
                        _native_int(value.get("mono_after_ns"), "mono_after_ns")
                        cookie = value.get("cookie") if tool == "iperf3" else None
                        if tool == "iperf3" and (not isinstance(cookie, str) or not cookie):
                            continue
                        anchors[cookie] = raw
                    except (KeyError, ValueError):
                        continue
                elif kind in {"receiver_interval", "probe_window"}:
                    value = raw.value
                    if isinstance(value.get("cookie"), str) and value.get("cookie"):
                        observed_sessions.add(str(value["cookie"]))
        if tool == "sockperf":
            if None not in anchors:
                continue
            anchor = anchors[None]
            invocation = str(anchor.value.get("invocation_id", ""))
            matching = [item for item in all_commands if item.deployment_id == deployment_id
                        and item.producer == record.producer and item.unit_id == record.unit_id
                        and (item.result.get("action_id") == invocation or item.request.get("action_id") == invocation)]
            native = [item for item in source_artifact_records if item.deployment_id == deployment_id
                      and item.unit_id == record.unit_id and item.producer == record.producer and item.kind == "sockperf-csv"]
            if len(matching) != 1 or len(native) != 1:
                continue
            native[0].verify_materialized_file(child_root)
            result.append(AcquisitionSource(tool=tool, semantics_id="sockperf-3.10-enabled-v1", producer=record.producer,
                command_action_id=invocation, invocation_id=invocation, boot_id=str(anchor.value["boot_id"]),
                clock_domain=str(anchor.value["clock"]), log_artifact_id=record.artifact_id, log_path=record.path,
                native_artifact_ids=[native[0].artifact_id], build_artifact_id=str(getattr(build, "artifact_id")), mapping_indexes=[]))
            continue
        for cookie in sorted(observed_sessions):
            anchor = anchors.get(cookie)
            if anchor is None:
                continue
            invocation = str(anchor.value.get("invocation_id", ""))
            matching_receiver = [item for item in receiver_command_evidence if item.deployment_id == deployment_id
                                 and item.producer == record.producer
                                 and (item.result.get("action_id") == invocation or item.request.get("action_id") == invocation)]
            native = [item for item in source_artifact_records if item.deployment_id == deployment_id
                      and item.producer == VmRole.VM_A and item.kind in {"iperf3-json", "iperf3-client-jsonl"}]
            for item in native:
                item.verify_materialized_file(child_root)
            native = [item for item in native if _file_contains(resolve_artifact_path(child_root, item.path), cookie.encode("utf-8"))]
            if len(matching_receiver) != 1 or len(native) != 1:
                continue
            client_commands = [item for item in commands if item.deployment_id == deployment_id
                               and item.producer == VmRole.VM_A and item.unit_id == native[0].unit_id
                               and (item.result.get("action_id") or "").startswith("iperf3-")]
            if len(client_commands) != 1:
                continue
            native[0].verify_materialized_file(child_root)
            result.append(AcquisitionSource(tool=tool, semantics_id="iperf-3.17.1-stats-v1", producer=record.producer,
                command_action_id=invocation, invocation_id=invocation, session_id=cookie,
                boot_id=str(anchor.value["boot_id"]), clock_domain=str(anchor.value["clock"]),
                log_artifact_id=record.artifact_id, log_path=record.path, native_artifact_ids=[native[0].artifact_id],
                build_artifact_id=str(getattr(build, "artifact_id")), mapping_indexes=[]))
    return tuple(result)


def _file_contains(path: Path, needle: bytes) -> bool:
    overlap = b""
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                return False
            data = overlap + chunk
            if needle in data:
                return True
            overlap = data[-max(0, len(needle) - 1):]


def _union(intervals: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted((start, end) for start, end in intervals if end > start):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def overlap_coverage_lower_bound(probes: Iterable[SourceWindow], receivers: Iterable[SourceWindow]) -> float | None:
    """Return conservative inner acquisition/outer receiver coverage in one proven domain.

    None means identities or boundaries do not support a finite ratio. Gaps stay
    absent from both unions, and omitted receiver intervals never enter the denominator.
    """
    domain: tuple[str, str, str] | None = None
    receiver_start: int | None = None
    receiver_end: int | None = None
    included = False
    for item in receivers:
        item_domain = (item.producer, item.boot_id, item.clock_domain)
        if domain is None:
            domain = item_domain
        elif domain != item_domain:
            return None
        if item.omitted is None:
            return None
        if item.omitted:
            continue
        included = True
        receiver_start = item.start_earliest_ns if receiver_start is None else min(receiver_start, item.start_earliest_ns)
        receiver_end = item.end_latest_ns if receiver_end is None else max(receiver_end, item.end_latest_ns)
    if not included or domain is None or receiver_start is None or receiver_end is None:
        return None
    # Keep the continuous receiver-session envelope in the denominator. Using
    # the union here would silently forgive missing intervals and inflate coverage.
    receiver_duration = receiver_end - receiver_start
    if receiver_duration <= 0:
        return None
    numerator = 0
    pending: tuple[int, int] | None = None
    previous_start: int | None = None
    has_probe_evidence = False
    for item in probes:
        if (item.producer, item.boot_id, item.clock_domain) != domain:
            return None
        if not item.has_positive_inner_support:
            continue
        has_probe_evidence = True
        start = max(receiver_start, item.start_latest_ns)
        end = min(receiver_end, item.end_earliest_ns)
        if end <= start:
            continue
        if previous_start is not None and start < previous_start:
            return None
        previous_start = start
        if pending is not None and start <= pending[1]:
            pending = (pending[0], max(pending[1], end))
        else:
            if pending is not None:
                numerator += pending[1] - pending[0]
            pending = (start, end)
    if pending is not None:
        numerator += pending[1] - pending[0]
    if not has_probe_evidence:
        return None
    denominator = receiver_duration
    return min(1.0, numerator / denominator)
