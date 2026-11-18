"""Small local process and clock boundary for the V2 measurement runner."""
from __future__ import annotations

import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Optional, Protocol, Sequence

from .contracts import CommandOutcome, CommandRequest, CommandResult


class MeasurementClock(Protocol):
    def monotonic(self) -> float: ...
    def utc_now(self) -> datetime: ...
    def wait_until(self, deadline_monotonic: float) -> None: ...


class SystemMeasurementClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def utc_now(self) -> datetime:
        return datetime.now(timezone.utc)

    def wait_until(self, deadline_monotonic: float) -> None:
        remaining = deadline_monotonic - self.monotonic()
        if remaining <= 0:
            raise TimeoutError("measurement deadline exhausted")
        time.sleep(remaining)


@dataclass(frozen=True)
class ProcessEvent:
    handle: "LocalProcess"
    channel: str
    text: str
    monotonic: float
    utc: datetime


@dataclass(eq=False)
class LocalProcess:
    request: CommandRequest
    process: subprocess.Popen[bytes]
    started_monotonic: float
    started_at: datetime
    stdout_file: BinaryIO
    stderr_file: BinaryIO
    events: "queue.Queue[ProcessEvent]"
    preview_limit: int
    preview: dict[str, bytearray] = field(default_factory=lambda: {"stdout": bytearray(), "stderr": bytearray()})
    stream_errors: list[str] = field(default_factory=list)
    threads: list[threading.Thread] = field(default_factory=list)
    exit_observed: threading.Event = field(default_factory=threading.Event)
    drains_done: set[str] = field(default_factory=set)
    state_lock: threading.Lock = field(default_factory=threading.Lock)
    result: Optional[CommandResult] = None
    process_exit_monotonic: Optional[float] = None
    finished_at: Optional[datetime] = None
    finished_monotonic: Optional[float] = None


class MeasurementProcesses(Protocol):
    def start(self, request: CommandRequest, *, stdout_path: Path, stderr_path: Path) -> object: ...
    def next_event(self, handles: Sequence[object], deadline_monotonic: float) -> Optional[ProcessEvent]: ...
    def poll(self, handle: object) -> Optional[CommandResult]: ...
    def stop(self, handle: object, deadline_monotonic: float) -> CommandResult: ...


class LocalMeasurementProcesses:
    """Shell-free Popen runner which saves full streams while retaining bounded previews."""

    def __init__(self, clock: MeasurementClock, *, preview_bytes: int = 8192) -> None:
        self.clock = clock
        self.preview_bytes = preview_bytes
        self._events: "queue.Queue[ProcessEvent]" = queue.Queue(maxsize=32)

    def start(self, request: CommandRequest, *, stdout_path: Path, stderr_path: Path) -> LocalProcess:
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        out = stdout_path.open("xb")
        try:
            err = stderr_path.open("xb")
        except BaseException:
            out.close()
            raise
        started_at = self.clock.utc_now()
        started_mono = self.clock.monotonic()
        try:
            proc = subprocess.Popen(
                request.argv, cwd=request.cwd, env=request.environment or None,
                stdin=subprocess.PIPE if request.stdin is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, bufsize=0,
            )
        except BaseException:
            out.close()
            err.close()
            raise
        handle = LocalProcess(request, proc, started_mono, started_at, out, err, self._events, self.preview_bytes)
        if request.stdin is not None and proc.stdin is not None:
            try:
                proc.stdin.write(request.stdin.encode())
                proc.stdin.close()
            except OSError:
                pass
        for name, pipe, target in (("stdout", proc.stdout, out), ("stderr", proc.stderr, err)):
            thread = threading.Thread(target=self._drain, args=(handle, name, pipe, target), daemon=True)
            handle.threads.append(thread)
            thread.start()
        waiter = threading.Thread(target=self._watch_exit, args=(handle,), daemon=True)
        handle.threads.append(waiter)
        waiter.start()
        return handle

    def _watch_exit(self, handle: LocalProcess) -> None:
        handle.process.wait()
        with handle.state_lock:
            exited_monotonic, exited_at = self.clock.monotonic(), self.clock.utc_now()
            handle.process_exit_monotonic = exited_monotonic
            handle.exit_observed.set()
        event = ProcessEvent(handle, "exit", "", exited_monotonic, exited_at)
        self._publish_completion(handle, event)

    def _publish_completion(self, handle: LocalProcess, event: ProcessEvent) -> None:
        """Publish a bounded wake-up; poll also observes the durable completion state."""
        try:
            handle.events.put(event, timeout=0.1)
        except queue.Full:
            # A full queue already contains wake-ups. next_event periodically
            # returns to poll(), so dropping this notification cannot hide the
            # completed state or strand the consumer until its whole deadline.
            return

    def _drain(self, handle: LocalProcess, channel: str, pipe: object, target: BinaryIO) -> None:
        try:
            while True:
                chunk = pipe.read(4096)  # type: ignore[attr-defined]
                if not chunk:
                    break
                target.write(chunk)
                target.flush()
                preview = handle.preview[channel]
                if len(preview) < handle.preview_limit:
                    preview.extend(chunk[:handle.preview_limit - len(preview)])
                event = ProcessEvent(handle, channel, chunk.decode("utf-8", errors="replace"), self.clock.monotonic(), self.clock.utc_now())
                while True:
                    try:
                        handle.events.put(event, timeout=0.1)
                        break
                    except queue.Full:
                        if handle.process.poll() is not None:
                            # Keep the durable file complete; event pressure must not block reaping.
                            break
        except OSError as exc:
            handle.stream_errors.append(f"{channel} stream write/read failed: {type(exc).__name__}: {exc}")
        finally:
            try:
                pipe.close()  # type: ignore[attr-defined]
            except BaseException as exc:
                handle.stream_errors.append(f"{channel} stream close failed: {type(exc).__name__}: {exc}")
            try:
                target.flush()
            except BaseException as exc:
                handle.stream_errors.append(f"{channel} stream flush failed: {type(exc).__name__}: {exc}")
            with handle.state_lock:
                handle.drains_done.add(channel)
            now_mono, now_utc = self.clock.monotonic(), self.clock.utc_now()
            self._publish_completion(handle, ProcessEvent(handle, f"{channel}_closed", "", now_mono, now_utc))

    def next_event(self, handles: Sequence[object], deadline_monotonic: float) -> Optional[ProcessEvent]:
        del handles  # events are tagged by their owning handle
        timeout = deadline_monotonic - self.clock.monotonic()
        if timeout <= 0:
            return None
        try:
            # Completion flags are authoritative, including when a bounded
            # output queue was full and a terminal wake-up could not be queued.
            return self._events.get(timeout=min(timeout, 0.05))
        except queue.Empty:
            return None

    def poll(self, handle: object) -> Optional[CommandResult]:
        item = self._as_local(handle)
        if item.result is not None:
            return item.result
        code = item.process.poll()
        with item.state_lock:
            exit_observed = item.exit_observed.is_set()
            drains_done = item.drains_done == {"stdout", "stderr"}
        if code is None or not exit_observed or not drains_done:
            return None
        return self._finish(item, CommandOutcome.SUCCEEDED if code == 0 else CommandOutcome.FAILED, code)

    def stop(self, handle: object, deadline_monotonic: float) -> CommandResult:
        item = self._as_local(handle)
        if item.result is not None:
            return item.result
        proc = item.process
        shutdown_errors: list[str] = []
        try:
            if proc.poll() is None:
                try:
                    proc.terminate()
                except BaseException as exc:
                    shutdown_errors.append(f"terminate failed: {type(exc).__name__}: {exc}")
            remaining = max(0.0, deadline_monotonic - self.clock.monotonic())
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                pass
            except BaseException as exc:
                shutdown_errors.append(f"wait failed: {type(exc).__name__}: {exc}")
            kill_attempted = proc.poll() is None
            if kill_attempted:
                try:
                    proc.kill()
                except BaseException as exc:
                    shutdown_errors.append(f"kill failed: {type(exc).__name__}: {exc}")
            if kill_attempted:
                remaining = max(0.0, deadline_monotonic - self.clock.monotonic())
                try:
                    proc.wait(timeout=remaining)
                except subprocess.TimeoutExpired as exc:
                    shutdown_errors.append(f"reap failed: {type(exc).__name__}: {exc}")
                except BaseException as exc:
                    shutdown_errors.append(f"reap failed: {type(exc).__name__}: {exc}")
        finally:
            for thread in item.threads:
                try:
                    thread.join(timeout=max(0.0, deadline_monotonic - self.clock.monotonic()))
                except BaseException as exc:
                    shutdown_errors.append(f"drain join failed: {type(exc).__name__}: {exc}")
            item.stream_errors.extend(shutdown_errors)
            # Every bounded shutdown path owns these streams. Drain workers have
            # already had their remaining deadline to flush and close their pipes.
            for name, stream in (("stdout", item.stdout_file), ("stderr", item.stderr_file)):
                if not getattr(stream, "closed", False):
                    try:
                        stream.flush()
                    except BaseException as exc:
                        item.stream_errors.append(f"{name} stream flush failed: {type(exc).__name__}: {exc}")
                    try:
                        stream.close()
                    except BaseException as exc:
                        item.stream_errors.append(f"{name} stream close failed: {type(exc).__name__}: {exc}")
        if proc.poll() is None or any(thread.is_alive() for thread in item.threads):
            details = "; ".join(dict.fromkeys(item.stream_errors))
            error = "measurement process could not be reaped before deadline"
            raise TimeoutError(f"{error}: {details}" if details else error)
        return self._finish(item, CommandOutcome.INTERRUPTED, None)

    def _finish(self, item: LocalProcess, outcome: CommandOutcome, code: Optional[int]) -> CommandResult:
        if item.result is not None:
            return item.result
        # Normal completion owns the same files as stop(). A broken close on one
        # stream must not skip the other stream or escape poll(), since callers
        # use that result to enter their bounded cleanup/failure path.
        for name, stream in (("stdout", item.stdout_file), ("stderr", item.stderr_file)):
            if getattr(stream, "closed", False):
                continue
            try:
                stream.flush()
            except BaseException as exc:
                item.stream_errors.append(f"{name} stream flush failed: {type(exc).__name__}: {exc}")
            if not getattr(stream, "closed", False):
                try:
                    stream.close()
                except BaseException as exc:
                    item.stream_errors.append(f"{name} stream close failed: {type(exc).__name__}: {exc}")
            # A close implementation may fail transiently. Retry once, without
            # looping, while preserving an explicit unresolved state if it fails.
            if not getattr(stream, "closed", False):
                try:
                    stream.close()
                except BaseException as exc:
                    item.stream_errors.append(f"{name} stream close retry failed: {type(exc).__name__}: {exc}")
                if not getattr(stream, "closed", False):
                    item.stream_errors.append(f"{name} stream remains open after finalization")
        finished_at = self.clock.utc_now()
        finished_monotonic = self.clock.monotonic()
        duration = finished_monotonic - item.started_monotonic
        process_duration = ((item.process_exit_monotonic - item.started_monotonic)
                            if item.process_exit_monotonic is not None else duration)
        timed_out_exit_code = None
        if outcome == CommandOutcome.SUCCEEDED and process_duration > item.request.timeout_seconds:
            timed_out_exit_code = code
            outcome = CommandOutcome.TIMED_OUT
        utc_elapsed = (finished_at - item.started_at).total_seconds()
        stdout = bytes(item.preview["stdout"]).decode("utf-8", errors="replace")
        stderr = bytes(item.preview["stderr"]).decode("utf-8", errors="replace")
        if timed_out_exit_code is not None:
            stderr = (stderr + f"\nprocess exited with code {timed_out_exit_code} after request timeout")[-self.preview_bytes:]
        item.finished_at = finished_at
        item.finished_monotonic = finished_monotonic
        if item.stream_errors:
            stderr = (stderr + "\n" + "\n".join(item.stream_errors))[-self.preview_bytes:]
            if outcome != CommandOutcome.FAILED:
                outcome, code = CommandOutcome.FAILED, 125
        for secret in sorted((s for s in item.request.redactions if s), key=len, reverse=True):
            stdout = stdout.replace(secret, "[REDACTED]")
            stderr = stderr.replace(secret, "[REDACTED]")
        item.result = CommandResult(
            action_id=item.request.action_id, outcome=outcome, started_at=item.started_at,
            finished_at=finished_at, duration_seconds=duration, duration_basis="monotonic",
            monotonic_started=item.started_monotonic, monotonic_finished=finished_monotonic,
            utc_elapsed_discrepancy_seconds=utc_elapsed - duration,
            exit_code=None if outcome == CommandOutcome.TIMED_OUT else code,
            stdout=stdout, stderr=stderr, redactions=item.request.redactions,
        )
        return item.result

    @staticmethod
    def _as_local(handle: object) -> LocalProcess:
        if not isinstance(handle, LocalProcess):
            raise TypeError("handle was not created by LocalMeasurementProcesses")
        return handle
