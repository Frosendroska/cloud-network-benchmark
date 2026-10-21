"""Credential-free V2 Sockperf/iperf3 measurement execution on VM A."""
from __future__ import annotations

import csv
import ipaddress
import json
import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

from .contracts import (
    BenchmarkPhase, ClockMapping, CommandEvidence, CommandOutcome, CommandRequest,
    CommandResult, Evidence, ExperimentMode, FailureEvidence, IdentityContext,
    MeasurementTiming, PhaseConfiguration, ResolvedObservation, StringEvidence,
    TimingGap, VmRole,
)
from .contracts.artifacts import resolve_artifact_path
from .measurement_process import MeasurementClock, MeasurementProcesses, ProcessEvent, SystemMeasurementClock

NOTIFICATION = "Starting test..."
CALIBRATION_ORDER = (1, 4, 2, 8, 8, 2, 4, 1, 2, 8, 1, 4)
_PREVIEW = 8192


@dataclass(frozen=True)
class MeasurementContext:
    identity: IdentityContext
    b_private_ipv4: str
    root: Path
    deadline_monotonic: float
    shutdown_grace_seconds: float
    clock_mappings: tuple[ClockMapping, ...] = ()
    tool_identity: Mapping[str, StringEvidence] = field(default_factory=dict)


@dataclass(frozen=True)
class MeasurementResult:
    identity: IdentityContext
    executed_unit_ids: tuple[str, ...]
    unexecuted_unit_ids: tuple[str, ...]
    command_evidence: tuple[CommandEvidence, ...]
    timings: tuple[MeasurementTiming, ...]
    artifact_paths: tuple[str, ...]
    outcome: str
    failures: tuple[FailureEvidence, ...] = ()
    timing_notes: tuple[Mapping[str, object], ...] = ()


class MeasurementError(RuntimeError):
    def __init__(self, message: str, category: str = "measurement") -> None:
        super().__init__(message)
        self.category = category


def sockperf_argv(address: str, csv_path: Path, seconds: int) -> list[str]:
    return ["sockperf", "under-load", "-i", address, "-p", "11111", "-m", "64",
            "--mps", "1000", "--reply-every", "1", "--full-rtt", "--no-rdtsc",
            "--full-log", str(csv_path), "-t", str(seconds)]


def iperf3_argv(address: str, streams: int, seconds: int, *, day: bool = False) -> list[str]:
    common = ["iperf3", "-c", address, "-p", "5201", "-P", str(streams), "-t", str(seconds),
              "-O", "5", "-i", "1", "-l", "131072", "-C", "cubic", "-Z"]
    if day:
        return common + ["--json-stream", "--forceflush"]
    return common + ["-J", "--get-server-output"]


def receiver_duration(payload: bytes | str) -> Optional[float]:
    """Read only receiver-side iperf duration; sender summaries are never a substitute."""
    try:
        data = json.loads(payload)
    except (TypeError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    server = data.get("server_output")
    if isinstance(server, str):
        try:
            server = json.loads(server)
        except ValueError:
            server = None
    candidates = [server, data]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        end = candidate.get("end")
        if not isinstance(end, dict):
            continue
        summary = end.get("sum_received")
        if isinstance(summary, dict):
            seconds = summary.get("seconds")
            if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and math.isfinite(seconds) and seconds >= 0:
                return float(seconds)
    return None


def merge_intervals(intervals: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    ordered = sorted((start, end) for start, end in intervals if end > start)
    merged: list[tuple[float, float]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def covered_seconds(start: float, end: float, intervals: Iterable[tuple[float, float]], gaps: Iterable[tuple[float, float]] = ()) -> float:
    if end <= start:
        return 0.0
    active = merge_intervals((max(start, a), min(end, b)) for a, b in intervals)
    missing = merge_intervals((max(start, a), min(end, b)) for a, b in gaps)
    total = sum(b - a for a, b in active)
    return max(0.0, total - sum(b - a for a, b in missing))


def conservative_coverage(duration: float, start_bounds: tuple[float, float],
                          probe_windows: Iterable[tuple[float, float]],
                          gaps: Iterable[tuple[float, float]] = ()) -> Optional[float]:
    """Minimum coverage across every possible duration-long interval in a bounded start range."""
    lo, hi = start_bounds
    if lo is None or hi is None or not all(math.isfinite(v) for v in (duration, lo, hi)) or duration <= 0 or hi < lo:
        return None
    # Coverage can only change at window/gap boundaries. Evaluate each extreme and every
    # boundary shifted by the interval duration, which captures the piecewise-linear minima.
    probe_windows, gaps = tuple(probe_windows), tuple(gaps)
    candidates = {lo, hi}
    for a, b in (*probe_windows, *gaps):
        candidates.update((min(hi, max(lo, a)), min(hi, max(lo, b)),
                           min(hi, max(lo, a - duration)), min(hi, max(lo, b - duration))))
    return min(covered_seconds(s, s + duration, probe_windows, gaps) for s in candidates)


def conservative_measurement_bounds(process_started: float, process_finished: float,
                                    receiver_duration_seconds: Optional[float],
                                    requested_seconds: float, omitted_seconds: float,
                                    *, omission_verified: bool) -> Optional[tuple[float, float]]:
    """Bound the native measured interval on one monotonic clock when semantics are verified."""
    values = (process_started, process_finished, requested_seconds, omitted_seconds)
    if not all(math.isfinite(value) for value in values) or receiver_duration_seconds is None:
        return None
    if not math.isfinite(receiver_duration_seconds) or receiver_duration_seconds < requested_seconds:
        return None
    if not omission_verified or requested_seconds <= 0 or omitted_seconds < 0 or process_finished < process_started:
        return None
    bounds = process_started + omitted_seconds, process_finished - receiver_duration_seconds
    return bounds if bounds[1] >= bounds[0] else None


def _validate(observation: ResolvedObservation, context: MeasurementContext, clock: MeasurementClock) -> Path:
    IdentityContext.model_validate(context.identity.model_dump(mode="python"))
    if context.identity.experiment_id != observation.experiment_id or context.identity.config_id != observation.config_id:
        raise ValueError("identity experiment/config must match resolved observation")
    if context.identity.provider is None or str(context.identity.provider) != str(observation.provider):
        raise ValueError("identity provider must match resolved observation")
    if any(unit.kind == "phase" and unit.parent_unit_id is not None for unit in observation.measurement_units):
        raise ValueError("resolved units contain inconsistent parent identity")
    if any(context.identity.deployment_id != getattr(unit, "deployment_id", context.identity.deployment_id)
           for unit in observation.measurement_units):
        raise ValueError("all units must share the supplied deployment identity")
    address = ipaddress.ip_address(context.b_private_ipv4)
    if address.version != 4 or not address.is_private or address.is_loopback or address.is_multicast or address.is_unspecified:
        raise ValueError("B traffic destination must be a private IPv4 address")
    if not math.isfinite(context.deadline_monotonic) or context.deadline_monotonic <= clock.monotonic():
        raise ValueError("deadline_monotonic must be a finite future absolute deadline")
    if not math.isfinite(context.shutdown_grace_seconds) or context.shutdown_grace_seconds <= 0:
        raise ValueError("shutdown_grace_seconds must be finite and positive")
    mode = ExperimentMode(observation.mode)
    if mode in {ExperimentMode.SHORT_STUDY, ExperimentMode.DIAGNOSTIC_DAY} and observation.benchmark.common_n.state != "frozen":
        raise ValueError("research/day measurement requires frozen common N")
    if mode in {ExperimentMode.SHORT_STUDY, ExperimentMode.DIAGNOSTIC_DAY}:
        common_n = observation.benchmark.common_n.common_n
        if any(unit.phase == BenchmarkPhase.MULTI_FLOW and unit.streams != common_n for unit in observation.measurement_units):
            raise ValueError("research/day aggregate streams must equal frozen common N")
    root = Path(context.root).resolve()
    if not root.is_dir():
        raise ValueError("measurement artifact root must already exist")
    units = observation.measurement_units
    if mode != ExperimentMode.DIAGNOSTIC_DAY:
        paths = [p for unit in units for p in observation.artifact_layout.unit_paths(unit.unit_id).values()]
    else:
        paths = [observation.artifact_layout.day_iperf3_client]
    for rel in paths:
        dest = resolve_artifact_path(root, rel)
        if dest.exists():
            raise ValueError(f"measurement artifact already exists: {rel}")
        # Reject any existing symlink along the path, including one that currently resolves inward.
        cursor = root
        for part in Path(rel).parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ValueError(f"measurement artifact path contains symlink: {rel}")
    return root


def _evidence(value: object, reason: str) -> Evidence:
    return Evidence(value=value) if value is not None else Evidence(unavailable_reason=reason)


def _request(action: str, argv: list[str], root: Path, deadline: float, clock: MeasurementClock) -> CommandRequest:
    remaining = deadline - clock.monotonic()
    if remaining <= 0:
        raise MeasurementError("measurement deadline exhausted", "deadline")
    return CommandRequest(action_id=action, action_type="measurement", argv=argv, cwd=str(root), timeout_seconds=remaining)


def _paths(observation: ResolvedObservation, root: Path, unit: PhaseConfiguration) -> dict[str, Path]:
    return {name: resolve_artifact_path(root, rel) for name, rel in observation.artifact_layout.unit_paths(unit.unit_id).items()}


def _record(request: CommandRequest, result: CommandResult, context: MeasurementContext,
            unit_id: Optional[str], role: VmRole = VmRole.VM_A,
            started_monotonic: Optional[float] = None, finished_monotonic: Optional[float] = None) -> CommandEvidence:
    return CommandEvidence(
        schema_version=2, request=request.durable_view(), result=result.durable_view(), producer=role,
        deployment_id=context.identity.deployment_id, unit_id=unit_id,
        binary_version=context.tool_identity.get(request.argv[0], StringEvidence(unavailable_reason="tool identity not supplied")),
        source_identity=context.tool_identity.get(request.argv[0], StringEvidence(unavailable_reason="source identity not supplied")),
        monotonic_started=_evidence(started_monotonic, "process start not captured"),
        monotonic_finished=_evidence(finished_monotonic, "process finish not captured"),
        started_at=Evidence(value=result.started_at), finished_at=Evidence(value=result.finished_at),
    )


def _timing(unit_id: str, context: MeasurementContext, *, notification: Optional[datetime],
            tcp_started: Optional[datetime], omitted: float, idle: bool = False,
            clocks: tuple[ClockMapping, ...] = ()) -> MeasurementTiming:
    why = "native evidence does not identify exact measured boundaries"
    return MeasurementTiming(
        unit_id=unit_id, deployment_id=context.identity.deployment_id,
        probe_test_started_at=_evidence(notification, "Sockperf test-start notification not observed"),
        tcp_started_at=_evidence(tcp_started, "precise TCP measurement start unavailable"),
        measured_started_at=Evidence(unavailable_reason=why), measured_finished_at=Evidence(unavailable_reason=why),
        monotonic_started=Evidence(unavailable_reason=why), monotonic_finished=Evidence(unavailable_reason=why),
        omitted_seconds=omitted,
        overlap_seconds=Evidence(unavailable_reason="idle unit has no TCP workload" if idle else why),
        clock_mappings=list(clocks),
    )


class _Runner:
    def __init__(self, observation: ResolvedObservation, context: MeasurementContext,
                 processes: MeasurementProcesses, clock: MeasurementClock) -> None:
        self.observation, self.context, self.processes, self.clock = observation, context, processes, clock
        self.root = _validate(observation, context, clock)
        self.commands: list[CommandEvidence] = []
        self.timings: list[MeasurementTiming] = []
        self.artifacts: list[str] = []
        self.executed: list[str] = []
        self.failures: list[FailureEvidence] = []
        self.notes: list[Mapping[str, object]] = []

    def _failure(self, exc: BaseException, action: Optional[str] = None, category: Optional[str] = None) -> None:
        self.failures.append(FailureEvidence(category=category or getattr(exc, "category", "measurement"),
            message=f"{type(exc).__name__}: {exc}", occurred_at=self.clock.utc_now(), action_id=action))

    def _start(self, req: CommandRequest, stdout: Path, stderr: Path) -> object:
        return self.processes.start(req, stdout_path=stdout, stderr_path=stderr)

    @staticmethod
    def _start_times(handle: object) -> tuple[Optional[float], Optional[datetime]]:
        mono = getattr(handle, "started_monotonic", getattr(handle, "monotonic_started", None))
        utc = getattr(handle, "started_at", None)
        return mono, utc

    @staticmethod
    def _finish_times(handle: object, result: CommandResult) -> tuple[Optional[float], datetime]:
        mono = getattr(handle, "finished_monotonic", getattr(handle, "monotonic_finished", None))
        return mono, result.finished_at

    def _poll(self, handles: Sequence[object]) -> dict[object, Optional[CommandResult]]:
        return {handle: self.processes.poll(handle) for handle in handles}

    def _event_until(self, handles: Sequence[object], until: float) -> Optional[ProcessEvent]:
        event = self.processes.next_event(handles, min(until, self.context.deadline_monotonic - self.context.shutdown_grace_seconds))
        return event if isinstance(event, ProcessEvent) else event

    def _wait_notification(self, probe: object, until: float) -> Optional[ProcessEvent]:
        tail = ""
        while self.clock.monotonic() < until:
            result = self.processes.poll(probe)
            if result is not None:
                raise MeasurementError("Sockperf exited before its current test-start notification", "probe_exit")
            event = self._event_until([probe], until)
            if event is None:
                continue
            if event.channel == "exit":
                raise MeasurementError("Sockperf exited before its current test-start notification", "probe_exit")
            if event.monotonic > until or event.monotonic > self.context.deadline_monotonic - self.context.shutdown_grace_seconds:
                raise MeasurementError("Sockperf test-start notification arrived after the usable deadline", "deadline")
            if event.handle is not probe or event.channel != "stdout":
                continue
            text = tail + event.text
            if NOTIFICATION in text:
                return ProcessEvent(probe, "stdout", NOTIFICATION, event.monotonic, event.utc)
            tail = text[-(len(NOTIFICATION) - 1):]
        raise MeasurementError("deadline waiting for Sockperf test-start notification", "deadline")

    def _lead(self, probe: object, notification: ProcessEvent) -> None:
        until = notification.monotonic + self.observation.benchmark.probe_lead_seconds
        while self.clock.monotonic() < until:
            result = self.processes.poll(probe)
            if result is not None:
                raise MeasurementError("Sockperf exited during the two-second probe lead", "probe_exit")
            deadline = min(until, self.context.deadline_monotonic - self.context.shutdown_grace_seconds)
            if self.clock.monotonic() >= deadline:
                raise MeasurementError("deadline during the probe lead", "deadline")
            self._event_until([probe], min(until, deadline))
        if self.clock.monotonic() < until:
            raise MeasurementError("measurement clock did not reach probe lead deadline", "deadline")
        usable = self.context.deadline_monotonic - self.context.shutdown_grace_seconds
        if self.clock.monotonic() >= usable:
            raise MeasurementError("usable measurement deadline expired during the probe lead", "deadline")

    def _finish_process(self, handle: object, request: CommandRequest, unit_id: str,
                        start_mono: Optional[float], start_utc: Optional[datetime]) -> CommandResult:
        while True:
            result = self.processes.poll(handle)
            if result is not None:
                finish_mono, _ = self._finish_times(handle, result)
                self.commands.append(_record(request, result, self.context, unit_id, started_monotonic=start_mono, finished_monotonic=finish_mono))
                return result
            usable_deadline = self.context.deadline_monotonic - self.context.shutdown_grace_seconds
            if self.clock.monotonic() >= usable_deadline:
                raise MeasurementError("measurement deadline exhausted while process remained active", "deadline")
            self._event_until([handle], usable_deadline)

    def _run_unit(self, unit: PhaseConfiguration, *, seconds: int, probe_seconds: int,
                  tcp: bool = True, streams: Optional[int] = None, day: bool = False) -> None:
        usable_deadline = self.context.deadline_monotonic - self.context.shutdown_grace_seconds
        minimum_runtime = probe_seconds if not tcp else max(probe_seconds, 2 + seconds + 5)
        if self.clock.monotonic() + minimum_runtime > usable_deadline:
            raise MeasurementError("unit protocol and reserved shutdown grace do not fit the remaining deadline", "deadline")
        p = _paths(self.observation, self.root, unit)
        # F09 owns the canonical aggregate stdout/stderr projection. These VM-local logs
        # preserve tool attribution until that caller materializes the controller files.
        probe_stdout = p["sockperf"].with_suffix(".stdout.log")
        p_err = p["sockperf"].with_suffix(".sockperf.stderr.log")
        tcp_err = p["iperf3"].with_suffix(".iperf3.stderr.log")
        for path in (p["sockperf"], probe_stdout, p_err, tcp_err, *( [p["iperf3"]] if tcp and not day else [])):
            if path.exists():
                raise ValueError(f"measurement artifact already exists: {path.relative_to(self.root)}")
        self.artifacts.extend(path.relative_to(self.root).as_posix() for path in (p["sockperf"], probe_stdout, p_err))
        probe_req = _request(f"sockperf-{unit.unit_id}", sockperf_argv(self.context.b_private_ipv4, p["sockperf"], probe_seconds), self.root, self.context.deadline_monotonic - self.context.shutdown_grace_seconds, self.clock)
        probe = self._start(probe_req, probe_stdout, p_err)
        pmono, putc = self._start_times(probe)
        handles = [probe]
        tcp_req = None
        tcp_handle = None
        tcp_mono = None
        tcp_utc = None
        notification: Optional[ProcessEvent] = None
        primary: Optional[BaseException] = None
        timing_recorded = False
        recorded_handles: set[object] = set()
        try:
            if tcp:
                notification = self._wait_notification(probe, self.context.deadline_monotonic - self.context.shutdown_grace_seconds)
                self._lead(probe, notification)
                if self.clock.monotonic() + seconds + 5 > self.context.deadline_monotonic - self.context.shutdown_grace_seconds:
                    raise MeasurementError("TCP measured duration and reserved shutdown grace no longer fit the deadline", "deadline")
                tcp_dest = self.root / self.observation.artifact_layout.day_iperf3_client if day else p["iperf3"]
                tcp_err_dest = tcp_dest.with_suffix(".stderr.log") if day else tcp_err
                self.artifacts.extend((tcp_dest.relative_to(self.root).as_posix(), tcp_err_dest.relative_to(self.root).as_posix()))
                tcp_req = _request(f"iperf3-{unit.unit_id}", iperf3_argv(self.context.b_private_ipv4, streams or unit.streams or 1, seconds, day=day), self.root, self.context.deadline_monotonic - self.context.shutdown_grace_seconds, self.clock)
                tcp_handle = self._start(tcp_req, tcp_dest, tcp_err_dest)
                tcp_mono, tcp_utc = self._start_times(tcp_handle)
                handles.append(tcp_handle)
            results: dict[object, Optional[CommandResult]] = {handle: None for handle in handles}
            requests = {probe: probe_req}
            if tcp_handle is not None and tcp_req is not None:
                requests[tcp_handle] = tcp_req
            recorded = recorded_handles
            while any(value is None for value in results.values()):
                for handle in handles:
                    if results[handle] is None:
                        results[handle] = self.processes.poll(handle)
                    result = results[handle]
                    if result is not None and handle not in recorded:
                        req = requests[handle]
                        finish_mono, _ = self._finish_times(handle, result)
                        self.commands.append(_record(req, result, self.context, unit.unit_id,
                            started_monotonic=pmono if handle is probe else tcp_mono, finished_monotonic=finish_mono))
                        recorded.add(handle)
                if any(result is not None and result.outcome != CommandOutcome.SUCCEEDED for result in results.values()):
                    raise MeasurementError("measurement client exited unsuccessfully", "command_failure")
                if all(result is not None for result in results.values()):
                    break
                usable_deadline = self.context.deadline_monotonic - self.context.shutdown_grace_seconds
                if self.clock.monotonic() >= usable_deadline:
                    raise MeasurementError("measurement deadline exhausted while clients remained active", "deadline")
                event = self._event_until(handles, usable_deadline)
                # Drain events until both processes are reaped; no output is interpreted as exact timing.
                del event
            for handle, req in requests.items():
                result = results[handle]
                assert result is not None
                if result.outcome != CommandOutcome.SUCCEEDED:
                    raise MeasurementError(f"{req.argv[0]} exited {result.exit_code}", "command_failure")
            self.executed.append(unit.unit_id)
            self.timings.append(_timing(unit.unit_id, self.context,
                notification=notification.utc if notification else None,
                tcp_started=None, omitted=5, idle=not tcp,
                clocks=self.context.clock_mappings))
            observed_receiver_seconds = None
            if tcp and p["iperf3"].is_file():
                try:
                    observed_receiver_seconds = receiver_duration(p["iperf3"].read_bytes())
                except OSError as exc:
                    self.notes.append({"unit_id": unit.unit_id, "receiver_duration": "unavailable",
                                       "reason": f"cannot read closed receiver output: {type(exc).__name__}"})
            self.notes.append({"unit_id": unit.unit_id, "measured_window": "unavailable",
                "requested_receiver_seconds": seconds if tcp else None,
                "observed_receiver_seconds": observed_receiver_seconds,
                "conservative_bounds": "unavailable",
                "reason": "native Sockperf acquisition windows and verified omission semantics are required to bound coverage"})
            timing_recorded = True
        except BaseException as exc:
            primary = exc
            raise
        finally:
            if primary is not None:
                self._failure(primary, category=getattr(primary, "category", "measurement"))
            if not timing_recorded and (probe is not None):
                self.timings.append(_timing(unit.unit_id, self.context,
                    notification=notification.utc if notification else None, tcp_started=None,
                    omitted=5, idle=not tcp, clocks=self.context.clock_mappings))
                self.notes.append({"unit_id": unit.unit_id, "measured_window": "unavailable",
                                   "reason": "unit ended before defensible native measurement bounds were available"})
            for handle in reversed(handles):
                try:
                    current = self.processes.poll(handle)
                    if current is None:
                        stopped = self.processes.stop(handle, self.context.deadline_monotonic)
                        current = stopped
                    if current is not None and handle not in recorded_handles:
                        req = probe_req if handle is probe else tcp_req
                        if req is not None:
                            finish_mono, _ = self._finish_times(handle, current)
                            self.commands.append(_record(req, current, self.context, unit.unit_id,
                                started_monotonic=pmono if handle is probe else tcp_mono, finished_monotonic=finish_mono))
                            recorded_handles.add(handle)
                except BaseException as shutdown_exc:
                    self._failure(shutdown_exc, category="shutdown")

    def _remaining_units(self) -> list[str]:
        return [unit.unit_id for unit in self.observation.measurement_units if unit.unit_id not in self.executed]

    def _streams(self, unit: PhaseConfiguration) -> Optional[int]:
        if unit.phase == BenchmarkPhase.IDLE_LATENCY:
            return None
        if unit.streams is not None:
            return unit.streams
        common = self.observation.benchmark.common_n
        if ExperimentMode(self.observation.mode) == ExperimentMode.ACCEPTANCE and common.state == "pending":
            return 4
        return common.common_n

    def run(self) -> MeasurementResult:
        mode = ExperimentMode(self.observation.mode)
        units = self.observation.measurement_units
        failure: Optional[BaseException] = None
        try:
            if mode == ExperimentMode.DIAGNOSTIC_DAY:
                self._run_day(units[0])
            elif mode == ExperimentMode.CALIBRATION:
                for index, unit in enumerate(units):
                    self._run_unit(unit, seconds=unit.measured_seconds, probe_seconds=70, streams=unit.streams)
                    if index < len(units) - 1:
                        self._pause(10)
            else:
                measure = 60 if mode == ExperimentMode.SHORT_STUDY else 10
                for index, unit in enumerate(units):
                    probe = (65 if mode == ExperimentMode.SHORT_STUDY else 15) if unit.phase == BenchmarkPhase.IDLE_LATENCY else (70 if mode == ExperimentMode.SHORT_STUDY else 20)
                    self._run_unit(unit, seconds=measure, probe_seconds=probe, tcp=unit.phase != BenchmarkPhase.IDLE_LATENCY,
                                   streams=self._streams(unit))
                    if index < len(units) - 1:
                        self._pause(10)
        except BaseException as exc:
            failure = exc
            if not self.failures:
                self._failure(exc)
        return MeasurementResult(
            identity=self.context.identity, executed_unit_ids=tuple(self.executed),
            unexecuted_unit_ids=tuple(self._remaining_units()), command_evidence=tuple(self.commands),
            timings=tuple(self.timings), artifact_paths=tuple(dict.fromkeys(self.artifacts)),
            outcome="failed" if failure else "succeeded", failures=tuple(self.failures), timing_notes=tuple(self.notes),
        )

    def _pause(self, seconds: int) -> None:
        usable = self.context.deadline_monotonic - self.context.shutdown_grace_seconds
        until = self.clock.monotonic() + seconds
        if until > usable:
            raise MeasurementError("insufficient remaining budget for protocol pause and shutdown", "deadline")
        self.clock.wait_until(until)

    def _run_day(self, parent: PhaseConfiguration) -> None:
        # The continuous day uses one iperf process; chunks are bounded probe invocations.
        # A fake clock can advance this state machine without waiting in real time.
        # Start the persistent TCP with the first probe, then rotate probes while it remains active.
        tcp_path = resolve_artifact_path(self.root, self.observation.artifact_layout.day_iperf3_client)
        if tcp_path.exists():
            raise ValueError("diagnostic-day client output already exists")
        tcp_err = tcp_path.with_suffix(".stderr.log")
        if tcp_err.exists():
            raise ValueError("diagnostic-day client stderr already exists")
        self.artifacts.extend((tcp_path.relative_to(self.root).as_posix(), tcp_err.relative_to(self.root).as_posix()))
        deadline = self.context.deadline_monotonic - self.context.shutdown_grace_seconds
        if self.clock.monotonic() + 86400 + 7 > deadline:
            raise MeasurementError("diagnostic-day duration, initial gate and shutdown grace do not fit the deadline", "deadline")
        self._day_continuous(parent, tcp_path, tcp_err, deadline)

    def _day_continuous(self, parent: PhaseConfiguration, tcp_path: Path, tcp_err: Path, deadline: float) -> None:
        layout = self.observation.artifact_layout
        tcp_req = _request(f"iperf3-{parent.unit_id}", iperf3_argv(
            self.context.b_private_ipv4, self._streams(parent) or 1,
            86400, day=True), self.root, deadline, self.clock)
        tcp_handle: Optional[object] = None
        tcp_result: Optional[CommandResult] = None
        tcp_mono: Optional[float] = None
        chunk_no = 0
        probe_handle: Optional[object] = None
        probe_req: Optional[CommandRequest] = None
        probe_id: Optional[str] = None
        probe_start: Optional[float] = None
        notification: Optional[ProcessEvent] = None
        first = True
        try:
            while True:
                if probe_handle is None:
                    if tcp_handle is not None and self.processes.poll(tcp_handle) is not None:
                        break
                    chunk_no += 1
                    probe_id = f"probe-chunk-{chunk_no:06d}"
                    elapsed = 0.0 if tcp_mono is None else max(0.0, self.clock.monotonic() - tcp_mono - 5.0)
                    remaining = max(1, int(math.ceil(86400.0 - elapsed)))
                    chunk_seconds = min(300, remaining)
                    chunk = PhaseConfiguration(unit_id=probe_id, kind="probe_chunk", phase=BenchmarkPhase.MULTI_FLOW,
                        measured_seconds=chunk_seconds, streams=self._streams(parent) or 1,
                        parent_unit_id=parent.unit_id)
                    paths = layout.unit_paths(probe_id)
                    csv_path = resolve_artifact_path(self.root, layout.day_probe_csv_template.format(chunk_id=probe_id))
                    log_path = resolve_artifact_path(self.root, layout.day_probe_log_template.format(chunk_id=probe_id))
                    stderr_path = log_path.with_suffix(".stderr.log")
                    if any(path.exists() for path in (csv_path, log_path, stderr_path)):
                        raise ValueError(f"diagnostic chunk artifact already exists: {probe_id}")
                    probe_req = _request(f"sockperf-{probe_id}", sockperf_argv(self.context.b_private_ipv4, csv_path, chunk_seconds),
                                         self.root, deadline, self.clock)
                    probe_handle = self._start(probe_req, log_path, stderr_path)
                    probe_start, _ = self._start_times(probe_handle)
                    self.artifacts.extend((csv_path.relative_to(self.root).as_posix(), log_path.relative_to(self.root).as_posix(), stderr_path.relative_to(self.root).as_posix()))
                    if first:
                        notification = self._wait_notification(probe_handle, deadline)
                        self._lead(probe_handle, notification)
                        if self.clock.monotonic() + 86405 > deadline:
                            raise MeasurementError("diagnostic TCP duration and shutdown grace no longer fit the deadline", "deadline")
                        tcp_handle = self._start(tcp_req, tcp_path, tcp_err)
                        tcp_mono, _ = self._start_times(tcp_handle)
                        first = False
                    else:
                        # The same TCP handle stays active; later probe chunks do not gate or delay it.
                        first = False

                assert probe_handle is not None and probe_req is not None and probe_id is not None
                assert tcp_handle is not None
                probe_done = self.processes.poll(probe_handle)
                tcp_done = self.processes.poll(tcp_handle)
                if tcp_done is not None:
                    tcp_result = tcp_done
                    break
                if probe_done is not None:
                    fm, _ = self._finish_times(probe_handle, probe_done)
                    self.commands.append(_record(probe_req, probe_done, self.context, probe_id,
                        started_monotonic=probe_start, finished_monotonic=fm))
                    self.timings.append(_timing(probe_id, self.context, notification=notification.utc if notification else None,
                        tcp_started=None, omitted=0, clocks=self.context.clock_mappings))
                    self.artifacts.append(f"{layout.unit_paths(probe_id)['timing']}")
                    self.executed.append(probe_id)
                    if probe_done.outcome != CommandOutcome.SUCCEEDED:
                        self._failure(MeasurementError(f"Sockperf chunk exited {probe_done.exit_code}", "probe_chunk_failure"), probe_req.action_id)
                    self.notes.append({"unit_id": probe_id, "probe_gap_endpoints": "unavailable",
                                       "reason": "native probe timing does not establish exact acquisition endpoints"})
                    probe_handle = None
                    probe_req = None
                    probe_id = None
                    notification = None
                    continue
                usable = min(deadline, self.context.deadline_monotonic - self.context.shutdown_grace_seconds)
                if self.clock.monotonic() >= usable:
                    raise MeasurementError("diagnostic-day deadline exhausted", "deadline")
                self._event_until([probe_handle, tcp_handle], usable)

            if tcp_result is None:
                tcp_result = self.processes.poll(tcp_handle)
            if tcp_result is None:
                raise MeasurementError("diagnostic TCP exited without a completed result", "tcp_failure")
            fm, _ = self._finish_times(tcp_handle, tcp_result)
            self.commands.append(_record(tcp_req, tcp_result, self.context, parent.unit_id,
                started_monotonic=tcp_mono, finished_monotonic=fm))
            if tcp_result.outcome != CommandOutcome.SUCCEEDED:
                raise MeasurementError(f"diagnostic TCP exited {tcp_result.exit_code}", "tcp_failure")
            self.executed.append(parent.unit_id)
            self.timings.append(_timing(parent.unit_id, self.context, notification=None, tcp_started=None,
                omitted=5, clocks=self.context.clock_mappings))
        except BaseException as exc:
            self._failure(exc, category=getattr(exc, "category", "measurement"))
            raise
        finally:
            for handle, req, unit_id, start_mono in (
                (probe_handle, probe_req, probe_id, probe_start),
                (tcp_handle, tcp_req, parent.unit_id, tcp_mono),
            ):
                if handle is None or self.processes.poll(handle) is not None:
                    continue
                try:
                    result = self.processes.stop(handle, self.context.deadline_monotonic)
                    if req is not None and unit_id is not None:
                        fm, _ = self._finish_times(handle, result)
                        self.commands.append(_record(req, result, self.context, unit_id,
                            started_monotonic=start_mono, finished_monotonic=fm))
                except BaseException as shutdown_exc:
                    self._failure(shutdown_exc, category="shutdown")
                    continue
                if handle is probe_handle and unit_id is not None and not any(t.unit_id == unit_id for t in self.timings):
                    self.timings.append(_timing(unit_id, self.context, notification=None, tcp_started=None,
                        omitted=0, clocks=self.context.clock_mappings))
                    self.notes.append({"unit_id": unit_id, "probe_gap_endpoints": "unavailable",
                                       "reason": "probe chunk was interrupted before native timing could bound its acquisition"})
            if not any(t.unit_id == parent.unit_id for t in self.timings):
                self.timings.append(_timing(parent.unit_id, self.context, notification=None, tcp_started=None,
                    omitted=5, clocks=self.context.clock_mappings))


def run_measurement(observation: ResolvedObservation, context: MeasurementContext, *,
                    processes: MeasurementProcesses, clock: MeasurementClock) -> MeasurementResult:
    """Execute the supplied resolved units on A, returning unvalidated source evidence."""
    validated = ResolvedObservation.model_validate(observation.model_dump(mode="python"))
    return _Runner(validated, context, processes, clock).run()
