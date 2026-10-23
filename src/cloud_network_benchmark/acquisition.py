"""Conservative replay of source-emitted acquisition evidence.

The module intentionally treats observation logs as the authority.  Runtime timing
summaries and caller-constructed windows are not accepted by these functions.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import itertools
import json
import math
import base64
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


def _iperf_windows(records: Iterator[ParsedRecord], source: AcquisitionSource,
                   native: Mapping[str, object] | None = None) -> Iterator[SourceWindow]:
    origins: dict[tuple[str, str], int] = {}
    fixed_origins: dict[str, int] = {}
    last_interval: dict[str, tuple[int, int]] = {}
    settings: Mapping[str, object] | None = None
    interval_count = 0
    native_streams = (native["stream_factory"]() if native is not None
                      and not native.get("_preflight_complete") else None)
    for record in records:
        value = record.value
        _same_common(value, source)
        kind = value.get("record_kind")
        cookie = value.get("cookie")
        if not isinstance(cookie, str) or cookie != source.session_id:
            raise ValueError("iperf native session cookie does not match source reference")
        if kind == "session_settings":
            if settings is not None:
                raise ValueError("conflicting duplicate iperf session settings")
            if (str(value.get("protocol", "")).lower() != "tcp"
                    or value.get("reverse") is not False):
                raise ValueError("iperf receiver settings are not the approved forward TCP mode")
            _native_int(value.get("num_streams"), "num_streams")
            _native_int(value.get("omit"), "omit")
            _native_int(value.get("duration"), "duration")
            _native_int(value.get("interval_us"), "interval_us")
            _native_int(value.get("block_size"), "block_size")
            settings = value
            continue
        if kind == "stats_origin":
            phase = value.get("phase")
            if phase not in {"initial", "post_omit"}:
                raise ValueError("unsupported iperf stats origin phase")
            stream = value.get("stream_id")
            if not isinstance(stream, str) or not stream:
                raise ValueError("iperf stats origin lacks stream identity")
            _native_int(int(stream) if stream.isdecimal() else None, "stream_id")
            origin = _native_int(value.get("origin_us"), "origin_us")
            fixed = _native_int(value.get("start_time_fixed_us"), "start_time_fixed_us")
            if stream not in fixed_origins:
                fixed_origins[stream] = fixed
            elif fixed_origins[stream] != fixed:
                raise ValueError("conflicting iperf stream start origin")
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
        _native_int(int(stream) if stream.isdecimal() else None, "stream_id")
        initial_origin = origins.get(("initial", stream))
        post_omit_origin = origins.get(("post_omit", stream))
        if initial_origin is None:
            raise ValueError("iperf initial statistic origin is missing before receiver interval")
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
        _native_int(value.get("bytes"), "bytes")
        if value.get("role") != "receiver" or str(value.get("protocol", "")).lower() != "tcp":
            raise ValueError("iperf interval is not native TCP receiver evidence")
        for endpoint in ("local_host", "local_port", "remote_host", "remote_port"):
            if not isinstance(value.get(endpoint), str) or not value[endpoint]:
                raise ValueError("iperf receiver interval lacks native endpoint identity")
        omitted = value.get("omitted")
        if not isinstance(omitted, bool):
            raise ValueError("iperf omitted marker must be boolean")
        if omitted:
            # Omitted records precede the post-omit reset and are retained as
            # excluded native intervals; they do not contribute coverage.
            origin_us = initial_origin
        else:
            if post_omit_origin is None:
                raise ValueError("iperf post-omit statistic origin is missing before measured interval")
            origin_us = post_omit_origin
        if (native is not None and native.get("_preflight_complete")
                and interval_count >= int(native.get("validated_interval_count", 0))):
            raise ValueError("native iperf receiver output has an unknown unmatched interval tail")
        if native_streams is not None:
            native_row = next(native_streams, None)
            if native_row is None:
                raise ValueError("native iperf receiver output ends before the observed interval")
            origin = initial_origin if omitted else post_omit_origin
            assert origin is not None
            _corroborate_iperf_interval(value, stream, origin, native_row, native["server_output"])
        # reset_stats resets the native origin but the first interval may retain
        # its pre-reset interval start. Clip only temporal support to the reset;
        # preserve the native interval end and byte count without prorating.
        start_ns, end_ns = max(start_us, origin_us) * 1000, end_us * 1000
        interval_count += 1
        yield SourceWindow("receiver_measured", "iperf3", str(source.producer), source.boot_id,
            source.clock_domain, source.session_id, stream, start_ns, start_ns + 1000,
            end_ns, end_ns + 1000, omitted, record.byte_offset, record.byte_length)
    if settings is None:
        raise ValueError("iperf native session settings are missing")
    if len({stream for phase, stream in origins if phase == "initial"}) != settings["num_streams"]:
        raise ValueError("iperf stream origins do not match native stream count")
    if not any(phase == "post_omit" for phase, _stream in origins):
        raise ValueError("iperf post-omit statistic origin is missing")
    if interval_count == 0:
        raise ValueError("iperf receiver interval evidence is missing")
    if native is not None and native.get("receiver_complete") is not True:
        raise ValueError("native iperf receiver JSONL has an unknown session tail")
    if native is not None and native.get("native_tail"):
        raise ValueError("native iperf receiver JSONL has an unknown truncated tail")


def _validate_anchor(record: ParsedRecord, source: AcquisitionSource) -> None:
    value = record.value
    _same_common(value, source)
    before = _native_int(value.get("mono_before_ns"), "mono_before_ns")
    realtime = _native_int(value.get("realtime_ns"), "realtime_ns")
    after = _native_int(value.get("mono_after_ns"), "mono_after_ns")
    if before > after or realtime == 0:
        raise ValueError("clock anchor bounds are invalid")


def _corroborate_iperf_interval(value: Mapping[str, object], stream: str, origin_us: int,
                               native_row: Mapping[str, object], server_output: Mapping[str, object]) -> None:
    if not isinstance(server_output, Mapping):
        raise ValueError("native iperf receiver stream evidence is incomplete")
    observed_start = _native_int(value.get("interval_start_us"), "interval_start_us")
    omitted = value.get("omitted")
    # iperf_time_diff returns a nonnegative magnitude. For the first interval
    # crossing reset, its prior end precedes result->start_time, so JSON reports
    # abs(start - reset) while end remains end - reset.
    start_us = (observed_start - origin_us if omitted is True
                else abs(observed_start - origin_us))
    end_us = _native_int(value.get("interval_end_us"), "interval_end_us") - origin_us
    byte_count = _native_int(value.get("bytes"), "bytes")
    socket_id = native_row.get("socket")
    raw_start, raw_end = native_row.get("start"), native_row.get("end")
    if (isinstance(socket_id, bool) or not isinstance(socket_id, int) or str(socket_id) != stream
            or isinstance(raw_start, bool) or not isinstance(raw_start, (int, float)) or not math.isfinite(raw_start)
            or isinstance(raw_end, bool) or not isinstance(raw_end, (int, float)) or not math.isfinite(raw_end)):
        raise ValueError("native iperf receiver stream identity or offsets are invalid")
    if (abs(round(raw_start * 1_000_000) - start_us) > 1
            or abs(round(raw_end * 1_000_000) - end_us) > 1
            or native_row.get("bytes") != byte_count or native_row.get("omitted") is not omitted
            or native_row.get("sender") is not False):
        raise ValueError("native iperf interval offsets, bytes, omission or stream do not match receiver observation")
    connected = server_output.get("start", {}).get("connected", []) if isinstance(server_output.get("start"), Mapping) else []
    connection = next((item for item in connected if isinstance(item, Mapping)
        and item.get("socket") == int(stream)), None) if isinstance(connected, list) else None
    if not isinstance(connection, Mapping):
        raise ValueError("native iperf receiver endpoint record is missing for stream")
    for observation_key, native_key in (("local_host", "local_host"), ("remote_host", "remote_host"),
                                         ("local_port", "local_port"), ("remote_port", "remote_port")):
        if str(value.get(observation_key)) != str(connection.get(native_key)):
            raise ValueError("native iperf receiver endpoints do not match the observation")


def _normalized_native_settings(value: object) -> dict[str, object]:
    result = dict(value) if isinstance(value, Mapping) else {}
    if isinstance(result.get("protocol"), str):
        result["protocol"] = result["protocol"].lower()
    return result


def _check_iperf_settings(settings: Mapping[str, object], native: Mapping[str, object]) -> None:
    expected = {"protocol": str(settings["protocol"]).lower(), "num_streams": settings["num_streams"],
        "omit": settings["omit"], "duration": settings["duration"],
        "blksize": settings["block_size"], "reverse": settings["reverse"],
        "interval": settings["interval_us"] / 1_000_000}
    receiver = _normalized_native_settings(native.get("receiver_settings"))
    client = _normalized_native_settings(native.get("client_settings"))
    if any(receiver.get(key) != value for key, value in expected.items()):
        raise ValueError("native iperf receiver settings do not corroborate the source session")
    if any(client.get(key) != value for key, value in expected.items()):
        raise ValueError("native iperf client settings do not corroborate the source session")


def _preflight_iperf_source(records_factory, source: AcquisitionSource,
                            native: Mapping[str, object], outcome: object) -> None:
    """Check shared session proof and interval/native correspondence before any yield."""
    settings: Mapping[str, object] | None = None
    origins: dict[tuple[str, str], int] = {}
    fixed: dict[str, int] = {}
    last: dict[str, tuple[int, int]] = {}
    measured_streams: set[str] = set()
    interval_count = 0
    observation_tail = False
    native_tail = False
    matched_count = 0
    stream_rows = native["stream_factory"]()
    settings_checked = False
    missing_native_row = False
    try:
        for record in records_factory():
            value = record.value
            _same_common(value, source)
            if value.get("cookie") != source.session_id:
                raise ValueError("iperf native session cookie does not match source reference")
            kind = value.get("record_kind")
            if kind == "session_settings":
                if settings is not None:
                    raise ValueError("conflicting duplicate iperf session settings")
                if str(value.get("protocol", "")).lower() != "tcp" or value.get("reverse") is not False:
                    raise ValueError("iperf receiver settings are not the approved forward TCP mode")
                for name in ("num_streams", "omit", "duration", "interval_us", "block_size"):
                    _native_int(value.get(name), name)
                settings = value
            elif kind == "stats_origin":
                phase, stream = value.get("phase"), value.get("stream_id")
                if phase not in {"initial", "post_omit"} or not isinstance(stream, str) or not stream.isdecimal():
                    raise ValueError("iperf stats origin identity is invalid")
                origin = _native_int(value.get("origin_us"), "origin_us")
                fixed_value = _native_int(value.get("start_time_fixed_us"), "start_time_fixed_us")
                if stream in fixed and fixed[stream] != fixed_value:
                    raise ValueError("conflicting iperf stream start origin")
                fixed[stream] = fixed_value
                identity = (str(phase), stream)
                if identity in origins and origins[identity] != origin:
                    raise ValueError("conflicting duplicate iperf stats origin")
                origins[identity] = origin
            elif kind == "receiver_interval":
                if settings is None:
                    raise ValueError("iperf session settings are missing before receiver intervals")
                stream = value.get("stream_id")
                if not isinstance(stream, str) or not stream.isdecimal():
                    raise ValueError("iperf receiver interval stream identity is invalid")
                start = _native_int(value.get("interval_start_us"), "interval_start_us")
                end = _native_int(value.get("interval_end_us"), "interval_end_us")
                if end < start:
                    raise ValueError("iperf receiver interval is non-monotonic")
                previous = last.get(stream)
                if previous is not None and (start < previous[0] or end < previous[1]):
                    raise ValueError("conflicting or non-monotonic iperf receiver interval sequence")
                last[stream] = (start, end)
                _native_int(value.get("bytes"), "bytes")
                if value.get("role") != "receiver" or str(value.get("protocol", "")).lower() != "tcp":
                    raise ValueError("iperf interval is not native TCP receiver evidence")
                if not isinstance(value.get("omitted"), bool):
                    raise ValueError("iperf omitted marker must be boolean")
                phase = "initial" if value["omitted"] is True else "post_omit"
                if (phase, stream) not in origins:
                    raise ValueError("iperf statistic origin is missing before receiver interval")
                if phase == "post_omit":
                    measured_streams.add(stream)
                for key in ("local_host", "local_port", "remote_host", "remote_port"):
                    if not isinstance(value.get(key), str) or not value[key]:
                        raise ValueError("iperf receiver interval lacks native endpoint identity")
                if not settings_checked:
                    _check_iperf_settings(settings, native)
                    settings_checked = True
                origin = origins[(phase, stream)]
                if not missing_native_row:
                    try:
                        native_row = next(stream_rows, None)
                    except ValueError as exc:
                        if str(exc).startswith("native iperf JSONL ends with a truncated event"):
                            native_tail = True
                            missing_native_row = True
                        else:
                            raise
                    else:
                        if native_row is None:
                            missing_native_row = True
                        else:
                            _corroborate_iperf_interval(value, stream, origin, native_row,
                                                        native["server_output"])
                            matched_count += 1
                interval_count += 1
    except ValueError as exc:
        if str(exc).startswith("truncated acquisition record at byte"):
            observation_tail = True
        else:
            raise
    if settings is None or not interval_count:
        raise ValueError("iperf source lacks session settings or closed receiver intervals")
    initial_streams = {stream for phase, stream in origins if phase == "initial"}
    post_streams = {stream for phase, stream in origins if phase == "post_omit"}
    if len(initial_streams) != settings["num_streams"]:
        raise ValueError("iperf stream origins do not match native stream count")
    if measured_streams and not measured_streams.issubset(post_streams):
        raise ValueError("iperf post-omit statistic origin is missing for a measured stream")
    _check_iperf_settings(settings, native)
    outcome_value = getattr(outcome, "value", outcome)
    partial_allowed = (observation_tail or native_tail
        or outcome_value not in {None, "succeeded"} or native.get("receiver_complete") is not True)
    if missing_native_row and not partial_allowed:
        raise ValueError("native iperf receiver output ends before the observed interval")
    try:
        extra = next(stream_rows, None)
    except ValueError as exc:
        if str(exc).startswith("native iperf JSONL ends with a truncated event"):
            extra = None
            native_tail = True
            partial_allowed = True
        else:
            raise
    partial_allowed = partial_allowed or native_tail
    if extra is not None and not partial_allowed:
        raise ValueError("native iperf receiver intervals do not match the source count")
    if matched_count == 0:
        raise ValueError("native iperf receiver has no independently corroborated interval")
    native["partial_allowed"] = partial_allowed
    native["validated_interval_count"] = matched_count
    native["native_tail"] = native_tail
    native["_preflight_complete"] = True


def _expected_build_identity(tool: str) -> tuple[str, str, str, str]:
    if tool == "sockperf":
        return ("5ebd327da983225321818c0355db922515e026bd",
                "tool_patches/sockperf-3.10-acquisition.patch",
                "3f0bb59a3c70b247e5ebde77254e1427c35dc6ec39e2a78a3c779cff7015a285",
                "a35a1daf310551b9f675af4fb7d95a6a10389fda4c7605d70a21554be0773f8e")
    return ("2acfcfe94e928e74542c9f107e02aa6dd4748a79",
            "tool_patches/iperf-3.17.1-acquisition.patch",
            "11b96daadb4a1f17dc24376bb3d0d7feec330efeedc550b1979b1566e9b81661",
            "d6b4d2c7b20157c5b7fbd2de66f22bd3428958b5f2e28899151b6e22745886fe")


def _parse_build_identity(raw: bytes) -> dict[str, object]:
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("build identity artifact is not UTF-8") from exc
    if not lines or lines[0] != "CNB_BUILD_V1":
        raise ValueError("build identity artifact format is unsupported")
    values: dict[str, str] = {}
    for line in lines[1:]:
        key, sep, value = line.partition("=")
        if not sep or key in values:
            raise ValueError("build identity artifact has malformed or duplicate fields")
        values[key] = value
    expected = {"tool", "source_reference", "source_sha256", "upstream_commit", "patch_path", "patch_sha256",
        "compiler_version_b64", "build_configuration_b64", "binary_path", "binary_version_b64", "binary_sha256",
        "observation_capability", "observation_semantics", "capabilities"}
    if values.keys() != expected:
        raise ValueError("build identity artifact fields are incomplete")
    result: dict[str, object] = dict(values)
    for name, field in (("compiler_version_b64", "compiler_version"),
                        ("build_configuration_b64", "build_configuration"),
                        ("binary_version_b64", "binary_version")):
        try:
            result[field] = base64.b64decode(values[name], validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError(f"build identity artifact {name} is invalid") from exc
    return result


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
    # Failed/interrupted commands may still have independently verifiable closed
    # receiver intervals in their stderr. Consumers retain those intervals while
    # the command outcome keeps the unobserved tail explicitly incomplete.
    if command[0].producer != source.producer:
        raise ValueError("acquisition command producer does not match source")
    if log.unit_id not in {None, command[0].unit_id}:
        raise ValueError("acquisition log unit does not match its producing command")
    native_client: ArtifactRecord | None = None
    native_receiver: ArtifactRecord | None = None
    for item in native:
        if item.deployment_id != log.deployment_id:
            raise ValueError("native acquisition artifacts must share the source deployment")
        if source.tool == "sockperf" and (item.producer != source.producer or item.kind != "sockperf-csv"):
            raise ValueError("Sockperf source must reference its native client CSV")
        if source.tool == "iperf3":
            if item.producer == VmRole.VM_A and item.kind in {"iperf3-json", "iperf3-client-jsonl"}:
                if native_client is not None:
                    raise ValueError("iperf3 source must reference exactly one native client artifact")
                native_client = item
            elif item.producer == VmRole.VM_B and item.kind == "iperf3-server-jsonl":
                if native_receiver is not None:
                    raise ValueError("iperf3 source must reference at most one native receiver artifact")
                native_receiver = item
            else:
                raise ValueError("iperf3 source references an unsupported native artifact")
        if source.tool == "sockperf" and item.unit_id not in {None, command[0].unit_id}:
            raise ValueError("Sockperf native artifact must match its producing measurement unit")
        item.verify_materialized_file(child_root)
    native_metadata: Mapping[str, object] | None = None
    if source.tool == "iperf3":
        if native_client is None:
            raise ValueError("iperf3 source must reference one native client artifact")
        client_commands = [row for row in commands if row.deployment_id == log.deployment_id
            and row.producer == VmRole.VM_A and row.unit_id == native_client.unit_id
            and (row.result.get("action_id") or row.request.get("action_id"))
                == f"iperf3-{native_client.unit_id}"]
        if len(client_commands) != 1:
            raise ValueError("iperf3 native client artifact must match one client command in its unit")
        receiver_path = None
        if native_receiver is not None:
            if native_receiver.unit_id is not None:
                raise ValueError("persistent iperf3 receiver artifact must be deployment-scoped")
            receiver_path = resolve_artifact_path(child_root, native_receiver.path)
        if not _native_iperf_cookie(resolve_artifact_path(child_root, native_client.path), source.session_id):
            raise ValueError("native iperf client JSON start.cookie does not match the referenced session")
        native_metadata = _native_iperf_metadata(resolve_artifact_path(child_root, native_client.path),
            source.session_id, receiver_path)
        if native_metadata is None:
            raise ValueError("native iperf client/receiver JSON lacks a unique session and receiver interval output")
    builds = [item for item in build_evidence if getattr(item, "artifact_id", None) == source.build_artifact_id
              and getattr(item, "role", None) == source.producer]
    if len(builds) != 1:
        raise ValueError("acquisition source must match one observed build artifact")
    observed = builds[0]
    build_record = one(source.build_artifact_id)
    if (build_record.kind != "build-identity" or build_record.deployment_id != log.deployment_id
            or build_record.producer != source.producer or build_record.unit_id is not None):
        raise ValueError("build identity artifact must be a closed same-deployment producer artifact")
    build_record.verify_materialized_file(child_root)
    identity_bytes = resolve_artifact_path(child_root, build_record.path).read_bytes()
    if getattr(observed, "identity_bytes", None) is None or identity_bytes != observed.identity_bytes.encode("utf-8"):
        raise ValueError("materialized build identity bytes do not match the captured bootstrap sidecar")
    identity = _parse_build_identity(identity_bytes)
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
    for field, expected in (("tool", source.tool), ("source_reference", observed.source_reference),
            ("source_sha256", observed.source_sha256), ("upstream_commit", observed.upstream_commit),
            ("patch_path", observed.patch_path), ("patch_sha256", observed.patch_sha256),
            ("compiler_version", observed.compiler_version), ("build_configuration", observed.build_configuration),
            ("binary_path", observed.binary_path), ("binary_version", observed.binary_version),
            ("binary_sha256", observed.binary_sha256), ("observation_capability", observed.observation_capability),
            ("observation_semantics", observed.observation_semantics),
            ("capabilities", ",".join(getattr(observed, "capabilities", ())))):
        if identity.get(field) != expected:
            raise ValueError(f"build sidecar {field} does not match the supplied BuildObservation")
    request = command[0].request if isinstance(command[0].request, Mapping) else getattr(command[0].request, "model_dump", lambda **_: {})()
    argv = request.get("argv", []) if isinstance(request, Mapping) else []
    if source.tool == "sockperf" and request.get("acquisition_invocation_id") != source.invocation_id:
        raise ValueError("acquisition-producing command environment identity does not match invocation")
    if source.tool == "sockperf" and (not argv or argv[0] != observed.binary_path):
        raise ValueError("acquisition-producing command executable does not match the installed build identity")
    if source.tool == "iperf3":
        if (not any(str(token) == observed.binary_path for token in argv)
                or f"--property=Environment=CNB_ACQUISITION_ID={source.command_action_id}" not in argv
                or "--json-stream" not in argv or "-s" not in argv):
            raise ValueError("iperf receiver command environment or native JSON-stream mode does not match source identity")
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
        if outcome not in {None, "succeeded"}:
            raise ValueError(f"acquisition producer command outcome {outcome!r} leaves an unknown tail")
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
    if native_metadata is not None:
        _preflight_iperf_source(iperf_records, source, native_metadata, outcome)
    yield from _iperf_windows(iperf_records(), source, native_metadata)
    if outcome not in {None, "succeeded"}:
        raise ValueError(f"acquisition producer command outcome {outcome!r} leaves an unknown tail")


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
        try:
            record.verify_materialized_file(child_root)
        except (OSError, ValueError):
            # A corrupt candidate cannot invalidate a different independently
            # materialized source from another unit or receiver session.
            continue
        anchors: dict[str | None, ParsedRecord] = {}
        observed_sessions: set[str] = set()
        try:
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
        except (OSError, ValueError):
            # Keep independently parsed closed prefixes; replay will surface the
            # malformed tail while retaining any windows yielded before it.
            pass
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
            try:
                native[0].verify_materialized_file(child_root)
            except (OSError, ValueError):
                # A corrupt unit-local native file only makes this candidate
                # unavailable. It must not erase independent units below.
                continue
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
            native_clients = [item for item in source_artifact_records if item.deployment_id == deployment_id
                      and item.producer == VmRole.VM_A and item.kind in {"iperf3-json", "iperf3-client-jsonl"}]
            matching_clients: list[ArtifactRecord] = []
            for item in native_clients:
                try:
                    item.verify_materialized_file(child_root)
                except (OSError, ValueError):
                    continue
                if _native_iperf_cookie(resolve_artifact_path(child_root, item.path), cookie):
                    matching_clients.append(item)
            if len(matching_receiver) != 1 or len(matching_clients) != 1:
                continue
            client = matching_clients[0]
            client_commands = [item for item in commands if item.deployment_id == deployment_id
                               and item.producer == VmRole.VM_A and item.unit_id == client.unit_id
                               and (item.result.get("action_id") or item.request.get("action_id"))
                                   == f"iperf3-{client.unit_id}"]
            if len(client_commands) != 1:
                continue
            client_path = resolve_artifact_path(child_root, client.path)
            client_metadata = _native_iperf_metadata(client_path, cookie)
            native_ids = [client.artifact_id]
            if client_metadata is None and _native_iperf_is_jsonl(client_path):
                server_candidates = [item for item in source_artifact_records
                    if item.deployment_id == deployment_id and item.producer == VmRole.VM_B
                    and item.unit_id is None and item.kind == "iperf3-server-jsonl"]
                matching_servers = []
                for item in server_candidates:
                    try:
                        item.verify_materialized_file(child_root)
                        metadata = _native_iperf_metadata(client_path, cookie,
                            resolve_artifact_path(child_root, item.path))
                    except (OSError, ValueError):
                        continue
                    if metadata is not None:
                        matching_servers.append(item)
                if len(matching_servers) != 1:
                    continue
                native_ids.append(matching_servers[0].artifact_id)
            result.append(AcquisitionSource(tool=tool, semantics_id="iperf-3.17.1-stats-v1", producer=record.producer,
                command_action_id=invocation, invocation_id=invocation, session_id=cookie,
                boot_id=str(anchor.value["boot_id"]), clock_domain=str(anchor.value["clock"]),
                log_artifact_id=record.artifact_id, log_path=record.path, native_artifact_ids=native_ids,
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


MAX_PRETTY_IPERF_JSON_BYTES = 16 * 1024 * 1024
MAX_IPERF_JSONL_RECORD_BYTES = 1024 * 1024


def _native_start(value: Mapping[str, object]) -> Mapping[str, object] | None:
    """The pinned JSONStream_Output puts json_start directly in event.data."""
    if value.get("event") == "start":
        data = value.get("data")
        return data if isinstance(data, Mapping) else None
    start = value.get("start")
    return start if isinstance(start, Mapping) else None


def _native_settings(start: Mapping[str, object]) -> Mapping[str, object] | None:
    value = start.get("test_start")
    return value if isinstance(value, Mapping) else None


def _decode_json_line(line: str) -> Mapping[str, object] | None:
    value = json.loads(line, object_pairs_hook=_unique_json_object,
        parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)))
    return value if isinstance(value, Mapping) else None


def _native_iperf_cookie(path: Path, expected_cookie: str) -> bool:
    """Find one exact native start cookie in pretty JSON or source JSONL."""
    try:
        with path.open("r", encoding="utf-8") as stream:
            first = next((line for line in stream if line.strip()), "")
        try:
            first_event = _decode_json_line(first)
        except (json.JSONDecodeError, ValueError):
            first_event = None
        if first_event is None or first_event.get("event") != "start":
            if path.stat().st_size > MAX_PRETTY_IPERF_JSON_BYTES:
                return False
            with path.open("r", encoding="utf-8") as stream:
                value = json.load(stream, object_pairs_hook=_unique_json_object,
                    parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)))
            if not isinstance(value, Mapping):
                return False
            start = _native_start(value)
            return start is not None and start.get("cookie") == expected_cookie
        starts = 0
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                if not line.endswith("\n"):
                    return False
                if len(line.encode("utf-8")) > MAX_IPERF_JSONL_RECORD_BYTES:
                    return False
                value = _decode_json_line(line)
                if value is None:
                    continue
                start = _native_start(value)
                if start is not None and isinstance(start.get("cookie"), str):
                    starts += 1
                    if start.get("cookie") != expected_cookie:
                        return False
        return starts == 1
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return False


def _native_iperf_is_jsonl(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8") as stream:
            first = next((line for line in stream if line.strip()), "")
        value = _decode_json_line(first)
        return value is not None and value.get("event") == "start"
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return False


def _native_server_metadata(path: Path, expected_cookie: str) -> dict[str, object] | None:
    """Validate one receiver session in the persistent server JSONL log."""
    active = False
    starts = 0
    settings = None
    server_start = None
    rows = 0
    ended = False
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                if not line.endswith("\n"):
                    break
                if len(line.encode("utf-8")) > MAX_IPERF_JSONL_RECORD_BYTES:
                    return None
                try:
                    value = _decode_json_line(line)
                except (json.JSONDecodeError, ValueError):
                    if not line.endswith("\n"):
                        break
                    return None
                if value is None:
                    continue
                event = value.get("event")
                if event == "start":
                    start = _native_start(value)
                    cookie = start.get("cookie") if start is not None else None
                    active = cookie == expected_cookie
                    if active:
                        starts += 1
                        if starts != 1:
                            return None
                        settings = _native_settings(start)
                        server_start = start
                elif event == "interval" and active:
                    data = value.get("data")
                    if isinstance(data, Mapping) and isinstance(data.get("streams"), list):
                        rows += sum(1 for row in data["streams"]
                                    if isinstance(row, Mapping) and row.get("sender") is False)
                elif event == "end" and active:
                    active = False
                    ended = True
        if starts != 1 or settings is None or server_start is None or rows == 0:
            return None
        return {"receiver_settings": settings, "server_output": {"start": server_start},
                "receiver_complete": ended}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None


def _iter_server_streams(path: Path, expected_cookie: str) -> Iterator[Mapping[str, object]]:
    active = False
    starts = 0
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            if not line.endswith("\n"):
                raise ValueError("native iperf JSONL ends with a truncated event")
            if len(line.encode("utf-8")) > MAX_IPERF_JSONL_RECORD_BYTES:
                raise ValueError("native iperf JSONL event exceeds its bounded record size")
            try:
                value = _decode_json_line(line)
            except (json.JSONDecodeError, ValueError) as exc:
                if not line.endswith("\n"):
                    raise ValueError("native iperf JSONL ends with a truncated event") from exc
                raise ValueError("native iperf JSONL contains an invalid event") from exc
            if value is None:
                continue
            event = value.get("event")
            if event == "start":
                start = _native_start(value)
                active = start is not None and start.get("cookie") == expected_cookie
                if active:
                    starts += 1
            elif event == "interval" and active:
                data = value.get("data")
                streams = data.get("streams") if isinstance(data, Mapping) else None
                if isinstance(streams, list):
                    yield from (row for row in streams if isinstance(row, Mapping)
                                and row.get("sender") is False)
            elif event == "end" and active:
                active = False
    if starts != 1:
        raise ValueError("native iperf receiver JSONL session is missing or ambiguous")


def _native_iperf_metadata(path: Path, expected_cookie: str,
                           receiver_path: Path | None = None) -> dict[str, object] | None:
    """Read bounded pretty -J or stream only the small JSONL session header."""
    try:
        with path.open("r", encoding="utf-8") as stream:
            first = next((line for line in stream if line.strip()), "")
        try:
            first_event = _decode_json_line(first)
        except (json.JSONDecodeError, ValueError):
            first_event = None
        if first_event is None or first_event.get("event") != "start":
            if path.stat().st_size > MAX_PRETTY_IPERF_JSON_BYTES:
                return None
            with path.open("r", encoding="utf-8") as stream:
                value = json.load(stream, object_pairs_hook=_unique_json_object,
                    parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)))
            if not isinstance(value, Mapping):
                return None
            start = _native_start(value)
            if start is None or start.get("cookie") != expected_cookie:
                return None
            settings = _native_settings(start)
            server_output = value.get("server_output_json")
            if settings is None or not isinstance(server_output, Mapping):
                return None
            receiver_start = server_output.get("start")
            receiver_settings = _native_settings(receiver_start) if isinstance(receiver_start, Mapping) else None
            if receiver_settings is None or not isinstance(server_output.get("intervals"), list):
                return None
            streams = tuple(row for interval in server_output["intervals"]
                if isinstance(interval, Mapping) and isinstance(interval.get("streams"), list)
                for row in interval["streams"] if isinstance(row, Mapping))
            if not streams:
                return None
            return {"cookie": expected_cookie, "client_settings": settings,
                "receiver_settings": receiver_settings, "server_output": server_output,
                "streams": streams, "stream_factory": lambda: iter(streams),
                "receiver_complete": True}
        starts = 0
        settings = None
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                if not line.endswith("\n"):
                    return None
                if len(line.encode("utf-8")) > MAX_IPERF_JSONL_RECORD_BYTES:
                    return None
                value = _decode_json_line(line)
                if value is None:
                    continue
                start = _native_start(value)
                if start is None:
                    continue
                cookie = start.get("cookie")
                if not isinstance(cookie, str):
                    continue
                starts += 1
                if cookie != expected_cookie:
                    return None
                settings = _native_settings(start)
        if starts != 1 or settings is None:
            return None
        if receiver_path is None:
            return None
        receiver = _native_server_metadata(receiver_path, expected_cookie)
        if receiver is None:
            return None
        return {"cookie": expected_cookie, "client_settings": settings,
                **receiver, "stream_factory": lambda: _iter_server_streams(receiver_path, expected_cookie)}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None


def _merged_support(items: Iterable[SourceWindow], domain: tuple[str, str, str], *,
                    receiver: bool, envelope: dict[str, int | None] | None = None
                    ) -> Iterator[tuple[int, int]]:
    """Yield chronological union support with one pending interval of state."""
    pending: tuple[int, int] | None = None
    previous_start: int | None = None
    for item in items:
        if (item.producer, item.boot_id, item.clock_domain) != domain:
            raise ValueError("acquisition sources do not share a proven clock domain")
        if receiver:
            if item.omitted is None:
                raise ValueError("receiver omission state is unknown")
            if item.omitted:
                continue
            assert envelope is not None
            envelope["start"] = item.start_earliest_ns if envelope["start"] is None else min(
                int(envelope["start"]), item.start_earliest_ns)
            envelope["end"] = item.end_latest_ns if envelope["end"] is None else max(
                int(envelope["end"]), item.end_latest_ns)
        if not item.has_positive_inner_support:
            continue
        start, end = item.start_latest_ns, item.end_earliest_ns
        if previous_start is not None and start < previous_start:
            raise ValueError("acquisition support is not in chronological order")
        previous_start = start
        if pending is None:
            pending = (start, end)
        elif start <= pending[1]:
            pending = (pending[0], max(pending[1], end))
        else:
            yield pending
            pending = (start, end)
    if pending is not None:
        yield pending


def overlap_coverage_lower_bound(probes: Iterable[SourceWindow], receivers: Iterable[SourceWindow]) -> float | None:
    """Stream inner-support intersections over the outer continuous receiver duration.

    Inputs must be emitted in chronological order by source replay. Only a single
    union interval from each stream is retained, so diagnostic-day coverage does
    not allocate storage proportional to interval count.
    """
    receiver_iter = iter(receivers)
    first = next(receiver_iter, None)
    if first is None:
        return None
    domain = (first.producer, first.boot_id, first.clock_domain)
    envelope: dict[str, int | None] = {"start": None, "end": None}
    receiver_support = _merged_support(itertools.chain((first,), receiver_iter), domain,
                                       receiver=True, envelope=envelope)
    probe_support = _merged_support(probes, domain, receiver=False)
    try:
        receiver_interval = next(receiver_support, None)
        probe_interval = next(probe_support, None)
        numerator = 0
        has_probe_support = False
        while receiver_interval is not None and probe_interval is not None:
            has_probe_support = True
            start = max(receiver_interval[0], probe_interval[0])
            end = min(receiver_interval[1], probe_interval[1])
            if end > start:
                numerator += end - start
            receiver_end, probe_end = receiver_interval[1], probe_interval[1]
            if receiver_end <= probe_end:
                receiver_interval = next(receiver_support, None)
            if probe_end <= receiver_end:
                probe_interval = next(probe_support, None)
        for _interval in receiver_support:
            pass
        for _interval in probe_support:
            has_probe_support = True
        if (not has_probe_support or envelope["start"] is None or envelope["end"] is None):
            return None
        denominator = int(envelope["end"]) - int(envelope["start"])
        if denominator <= 0:
            return None
        return min(1.0, numerator / denominator)
    except ValueError:
        return None
