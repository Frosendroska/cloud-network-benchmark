from __future__ import annotations

import json
import queue
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from cloud_network_benchmark.contracts import CommandOutcome, CommandResult
from cloud_network_benchmark.contracts import IdentityContext
from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.measurement import MeasurementContext
from cloud_network_benchmark.measurement_process import ProcessEvent


class FakeClock:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value
        self.origin = datetime(2026, 10, 21, tzinfo=timezone.utc)

    def monotonic(self) -> float:
        return self.value

    def utc_now(self) -> datetime:
        return self.origin + timedelta(seconds=self.value - 1000)

    def wait_until(self, deadline_monotonic: float) -> None:
        if deadline_monotonic < self.value:
            raise TimeoutError("deadline passed")
        self.value = deadline_monotonic


@dataclass(eq=False)
class FakeHandle:
    request: object
    started_monotonic: float
    started_at: datetime
    finishes: float
    result: Optional[CommandResult] = None
    stdout_path: Optional[Path] = None
    stderr_path: Optional[Path] = None


class FakeProcesses:
    def __init__(self, clock: FakeClock, *, failed_action: Optional[str] = None,
                 notification: str = "Starting test...", receiver_short: bool = False) -> None:
        self.clock = clock
        self.failed_action = failed_action
        self.notification = notification
        self.receiver_short = receiver_short
        self.handles: list[FakeHandle] = []
        self.events: "queue.Queue[ProcessEvent]" = queue.Queue()
        self.stop_calls: list[object] = []

    def start(self, request, *, stdout_path: Path, stderr_path: Path):
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.open("xb").write((self.notification + "\n" if request.argv[0] == "sockperf" else "").encode())
        stderr_path.open("xb").write(b"")
        if request.argv[0] == "sockperf":
            native_csv = Path(request.argv[request.argv.index("--full-log") + 1])
            native_csv.parent.mkdir(parents=True, exist_ok=True)
            native_csv.open("xb").write(b"time,latency_us\n0.0,10\n")
        seconds = int(request.argv[request.argv.index("-t") + 1])
        duration = seconds if request.argv[0] == "sockperf" else seconds + 5
        handle = FakeHandle(request, self.clock.monotonic(), self.clock.utc_now(), self.clock.monotonic() + duration,
                            stdout_path=stdout_path, stderr_path=stderr_path)
        self.handles.append(handle)
        if request.argv[0] == "sockperf":
            self.events.put(ProcessEvent(handle, "stdout", self.notification + "\n", handle.started_monotonic + 1,
                                         handle.started_at + timedelta(seconds=1)))
        else:
            output_path = stdout_path
            receiver_seconds = seconds - 0.01 if self.receiver_short and seconds > 10 else seconds
            payload = {"start": {"timestamp": {"time": "2026-10-21T00:00:00Z"}},
                       "server_output": {"end": {"sum_received": {"seconds": receiver_seconds}}}}
            output_path.write_text(json.dumps(payload), encoding="utf-8")
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
        handle.result = CommandResult(action_id=action,
            outcome=CommandOutcome.FAILED if failed else CommandOutcome.SUCCEEDED,
            started_at=handle.started_at, finished_at=finish,
            duration_seconds=max(0.0, (finish - handle.started_at).total_seconds()),
            exit_code=2 if failed else 0, stdout=stdout[:8192], stderr=stderr)
        return handle.result

    def stop(self, handle, deadline_monotonic):
        self.stop_calls.append(handle)
        self.clock.value = min(deadline_monotonic, self.clock.value)
        result = self.poll(handle)
        if result is not None:
            return result
        now = self.clock.utc_now()
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
    )
