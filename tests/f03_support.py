from __future__ import annotations

import json
import queue
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from cloud_network_benchmark.contracts import CommandOutcome, CommandResult, IdentityContext, StringEvidence
from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.measurement import MeasurementContext
from cloud_network_benchmark.measurement_process import ProcessEvent


class FakeClock:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value
        self.origin = datetime(2026, 10, 21, tzinfo=timezone.utc)
        self.waits: list[float] = []
        self.utc_adjustment = timedelta(0)

    def monotonic(self) -> float:
        return self.value

    def utc_now(self) -> datetime:
        return self.origin + timedelta(seconds=self.value - 1000) + self.utc_adjustment

    def wait_until(self, deadline_monotonic: float) -> None:
        if deadline_monotonic < self.value:
            raise TimeoutError("deadline passed")
        self.waits.append(deadline_monotonic - self.value)
        self.value = deadline_monotonic


@dataclass(eq=False)
class FakeHandle:
    request: object
    started_monotonic: float
    started_at: datetime
    finishes: float
    result: Optional[CommandResult] = None
    finished_monotonic: Optional[float] = None
    stdout_path: Optional[Path] = None
    stderr_path: Optional[Path] = None


class FakeProcesses:
    def __init__(self, clock: FakeClock, *, failed_action: Optional[str] = None,
                 notification: str = "Starting test...", receiver_short: bool = False,
                 notification_delay: float = 1.0, split_notification: bool = False,
                 start_failure: Optional[str] = None, early_exit_action: Optional[str] = None,
                 hang_action: Optional[str] = None, stop_failure_action: Optional[str] = None,
                 write_failure_action: Optional[str] = None, start_delay: float = 0.0,
                 corrupt_iperf_action: Optional[str] = None) -> None:
        self.clock = clock
        self.failed_action = failed_action
        self.notification = notification
        self.receiver_short = receiver_short
        self.notification_delay = notification_delay
        self.split_notification = split_notification
        self.start_failure = start_failure
        self.early_exit_action = early_exit_action
        self.hang_action = hang_action
        self.stop_failure_action = stop_failure_action
        self.write_failure_action = write_failure_action
        self.start_delay = start_delay
        self.corrupt_iperf_action = corrupt_iperf_action
        self.handles: list[FakeHandle] = []
        self.events: "queue.Queue[ProcessEvent]" = queue.Queue()
        self.stop_calls: list[object] = []

    def start(self, request, *, stdout_path: Path, stderr_path: Path):
        executable = Path(request.argv[0]).name
        if request.action_id == self.start_failure:
            raise OSError("scripted process start failure")
        self.clock.value += self.start_delay
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.open("xb").write((self.notification + "\n" if executable == "sockperf" else "").encode())
        stderr_path.open("xb").write(b"")
        if request.action_id == self.write_failure_action:
            raise OSError("scripted disk full while writing native evidence")
        seconds = int(request.argv[request.argv.index("-t") + 1])
        if executable == "sockperf":
            native_csv = Path(request.argv[request.argv.index("--full-log") + 1])
            native_csv.parent.mkdir(parents=True, exist_ok=True)
            native_csv.open("xb").write(
                b"------------------------------\n"
                b"packet, txTime(sec), rxTime(sec), Round trip(usec)\n"
                b"0, 1000.000000000, 1000.000010000, 10.000\n"
                b"------------------------------\n"
                b"[Total Run] RunTime=%.3f sec; Warm up time=400 msec; SentMessages=100; ReceivedMessages=99\n" % seconds
                + b"[Valid Duration] RunTime=%.3f sec; SentMessages=100; ReceivedMessages=99\n" % max(0, seconds - 0.45))
        receiver_seconds = seconds - 0.01 if self.receiver_short and executable == "iperf3" and seconds > 10 else seconds
        duration = seconds if executable == "sockperf" else receiver_seconds + 5
        if request.action_id == self.early_exit_action:
            duration = min(duration, 2.0)
            if executable == "iperf3":
                receiver_seconds = 0.0
        if request.action_id == self.hang_action:
            duration = float("inf")
        handle = FakeHandle(request, self.clock.monotonic(), self.clock.utc_now(), self.clock.monotonic() + duration,
                            stdout_path=stdout_path, stderr_path=stderr_path)
        self.handles.append(handle)
        if executable == "sockperf":
            chunks = [self.notification]
            if self.split_notification:
                chunks = [self.notification[:8], self.notification[8:]]
            at = handle.started_monotonic + self.notification_delay
            for index, chunk in enumerate(chunks):
                self.events.put(ProcessEvent(handle, "stdout", chunk + ("\n" if index == len(chunks) - 1 else ""), at,
                                             handle.started_at + timedelta(seconds=self.notification_delay)))
        else:
            output_path = stdout_path
            if request.action_id == self.corrupt_iperf_action:
                output_path.write_bytes(b'{"start":{"test_start":{"omit":5}},')
            else:
                start = {"start": {"test_start": {"omit": 5, "duration": seconds}}}
                server = {"end": {"sum_received": {"seconds": receiver_seconds,
                                                    "bytes": int(receiver_seconds * 1_000_000)}}}
                if "--json-stream" in request.argv:
                    output_path.write_text("\n".join(json.dumps(row) for row in (
                        {"event": "start", "data": start},
                        {"event": "server_output_json", "data": server},
                        {"event": "end", "data": {}},
                    )) + "\n", encoding="utf-8")
                else:
                    output_path.write_text(json.dumps({**start, "server_output_json": server}), encoding="utf-8")
        return handle

    def next_event(self, handles, deadline_monotonic):
        try:
            event = self.events.get_nowait()
            self.clock.value = max(self.clock.value, event.monotonic)
            return event
        except queue.Empty:
            finishes = [h.finishes for h in handles if self.poll(h) is None]
            next_time = min(finishes, default=deadline_monotonic)
            self.clock.value = max(self.clock.value, min(deadline_monotonic, next_time))
            return None

    def poll(self, handle):
        if handle.result is not None:
            return handle.result
        if self.clock.monotonic() < handle.finishes:
            return None
        action = handle.request.action_id
        failed = action == self.failed_action
        # Capture output now; iperf native JSON was materialized at start.
        stdout = handle.stdout_path.read_text(encoding="utf-8") if handle.stdout_path else ""
        stderr = handle.stderr_path.read_text(encoding="utf-8") if handle.stderr_path else ""
        finish = self.clock.utc_now()
        handle.finished_monotonic = self.clock.monotonic()
        handle.result = CommandResult(action_id=action,
            outcome=CommandOutcome.FAILED if failed else CommandOutcome.SUCCEEDED,
            started_at=handle.started_at, finished_at=finish,
            duration_seconds=max(0.0, (finish - handle.started_at).total_seconds()),
            exit_code=2 if failed else 0, stdout=stdout[:8192], stderr=stderr)
        return handle.result

    def stop(self, handle, deadline_monotonic):
        self.stop_calls.append(handle)
        if handle.request.action_id == self.stop_failure_action:
            raise OSError("scripted termination failure")
        self.clock.value = min(deadline_monotonic, self.clock.value)
        result = self.poll(handle)
        if result is not None:
            return result
        now = self.clock.utc_now()
        handle.finished_monotonic = self.clock.monotonic()
        handle.result = CommandResult(action_id=handle.request.action_id, outcome=CommandOutcome.INTERRUPTED,
            started_at=handle.started_at, finished_at=now, duration_seconds=(now - handle.started_at).total_seconds(),
            exit_code=None, stdout="", stderr="interrupted")
        return handle.result


def observation(repository_root: Path, mode: str, *, freeze_common_n: bool = True):
    names = {
        "acceptance": "exp-900-single-provider.yaml",
        "short_study": "exp-001-multi-provider.yaml",
        "calibration": "exp-001-calibration.yaml",
        "diagnostic_day": "exp-001-diagnostic-day.yaml",
    }
    path = repository_root / ("configs/tests" if mode == "acceptance" else "configs/experiments") / names[mode]
    resolved, _ = resolve_campaign(path, repository_root)
    item = resolved.observations[0]
    if freeze_common_n and mode in {"short_study", "diagnostic_day"}:
        common = item.benchmark.common_n.model_copy(update={
            "state": "frozen", "common_n": 4, "source_campaign_id": "CAL-S1",
            "source_sha256": "a" * 64, "plateau_found": True,
        })
        benchmark = item.benchmark.model_copy(update={"common_n": common})
        units = [u.model_copy(update={"streams": 4} if u.phase == "multi_flow" else {}) for u in item.measurement_units]
        item = item.model_copy(update={"benchmark": benchmark, "measurement_units": units})
    return item


def context(item, root: Path, *, deadline: float = 200000.0):
    return MeasurementContext(
        identity=IdentityContext(experiment_id=item.experiment_id, config_id=item.config_id,
            campaign_id="campaign-f03", observation_id="observation-f03", run_id=f"run-f03-{item.provider}",
            deployment_id="deployment-f03", provider=item.provider),
        b_private_ipv4="10.20.30.40", root=root, deadline_monotonic=deadline,
        shutdown_grace_seconds=1.0,
        tool_identity={
            "iperf3": StringEvidence(value="synthetic source-format fixture: iperf3 3.17.1"),
            "sockperf": StringEvidence(value="synthetic source-format fixture: Sockperf 3.10"),
        },
    )
