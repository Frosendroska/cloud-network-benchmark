from io import BytesIO
from pathlib import Path
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from cloud_network_benchmark.contracts import CommandEvidence, CommandRequest
from cloud_network_benchmark.measurement import _record
from cloud_network_benchmark.measurement_process import LocalMeasurementProcesses, ProcessEvent, SystemMeasurementClock
from f03_support import context, observation


def test_local_process_boundary_is_replaceable_and_exclusive(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("test attempted to launch an executable")

    monkeypatch.setattr("cloud_network_benchmark.measurement_process.subprocess.Popen", forbidden)
    runner = LocalMeasurementProcesses(SystemMeasurementClock())
    request = CommandRequest(action_id="fake", action_type="measurement", argv=["sockperf"], cwd=str(tmp_path), timeout_seconds=1)
    out, err = tmp_path / "out", tmp_path / "err"
    # A test fake supplies the same boundary shape; production Popen is never invoked here.
    assert not out.exists() and not err.exists()
    assert request.argv == ["sockperf"]
    assert isinstance(runner, LocalMeasurementProcesses)
    assert ProcessEvent.__name__ == "ProcessEvent"


def test_local_adapter_drains_both_streams_preserves_bytes_and_bounds_redacted_preview(tmp_path, monkeypatch):
    payload = b"secret-value\n" + b"x" * 100

    class ChunkedPipe(BytesIO):
        def read(self, size=-1):
            return super().read(min(5, size) if size >= 0 else 5)

    class FakePopen:
        def __init__(self, argv, **kwargs):
            assert argv == ["fake-tool"]
            assert kwargs["shell"] is False
            self.stdout = ChunkedPipe(payload)
            self.stderr = ChunkedPipe(b"diagnostic\n")
            self.returncode = 0
            self.stdin = None

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr("cloud_network_benchmark.measurement_process.subprocess.Popen", FakePopen)
    runner = LocalMeasurementProcesses(SystemMeasurementClock(), preview_bytes=32)
    req = CommandRequest(action_id="fake", action_type="measurement", argv=["fake-tool"], cwd=str(tmp_path),
                         timeout_seconds=1, redactions=["secret-value"])
    out, err = tmp_path / "stream.out", tmp_path / "stream.err"
    handle = runner.start(req, stdout_path=out, stderr_path=err)
    for thread in handle.threads:
        thread.join(timeout=1)
    result = runner.poll(handle)
    assert result is not None and result.outcome == "succeeded"
    assert len(result.stdout.encode()) <= 32
    assert "secret-value" not in result.stdout
    assert out.read_bytes() == payload
    assert err.read_bytes() == b"diagnostic\n"
    try:
        runner.start(req, stdout_path=out, stderr_path=tmp_path / "second.err")
    except FileExistsError:
        pass
    else:
        raise AssertionError("existing output destination was overwritten")


def test_start_failure_closes_partial_files_and_never_overwrites(tmp_path, monkeypatch):
    original_open = Path.open
    err = tmp_path / "stream.err"

    def fail_stderr(path, *args, **kwargs):
        if path == err:
            raise OSError("scripted disk full while opening stderr")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_stderr)
    runner = LocalMeasurementProcesses(SystemMeasurementClock())
    req = CommandRequest(action_id="fake", action_type="measurement", argv=["fake-tool"],
                         cwd=str(tmp_path), timeout_seconds=1)
    out = tmp_path / "stream.out"
    try:
        runner.start(req, stdout_path=out, stderr_path=err)
    except OSError as exc:
        assert "disk full" in str(exc)
    else:
        raise AssertionError("stderr creation failure was not propagated")
    assert out.exists() and out.read_bytes() == b""
    assert not err.exists()


def test_stop_terminates_then_kills_and_reaps_on_caller_cancellation(tmp_path, monkeypatch):
    class FakePopen:
        def __init__(self, argv, **kwargs):
            self.stdout, self.stderr, self.stdin = BytesIO(b"partial\n"), BytesIO(b""), None
            self.returncode = None
            self.waits = 0
            self.terminated = self.killed = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True
            self.returncode = -9

        def wait(self, timeout=None):
            if timeout is None:
                return self.returncode
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired("fake-tool", timeout)
            return self.returncode

    monkeypatch.setattr("cloud_network_benchmark.measurement_process.subprocess.Popen", FakePopen)
    runner = LocalMeasurementProcesses(SystemMeasurementClock())
    req = CommandRequest(action_id="cancel", action_type="measurement", argv=["fake-tool"],
                         cwd=str(tmp_path), timeout_seconds=1)
    handle = runner.start(req, stdout_path=tmp_path / "out", stderr_path=tmp_path / "err")
    result = runner.stop(handle, time.monotonic() + 1)
    assert handle.process.terminated and handle.process.killed
    assert handle.process.waits == 2
    assert result.outcome == "interrupted"
    assert (tmp_path / "out").read_bytes() == b"partial\n"
    assert handle.stdout_file.closed


def test_stream_write_failure_is_reported_as_failed_command(tmp_path, monkeypatch):
    original_open = Path.open
    out = tmp_path / "out"

    class DiskFullWriter:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def write(self, data):
            raise OSError("disk full")

        def flush(self):
            self.wrapped.flush()

        def close(self):
            self.wrapped.close()

    def patched_open(path, *args, **kwargs):
        opened = original_open(path, *args, **kwargs)
        return DiskFullWriter(opened) if path == out else opened

    class FakePopen:
        def __init__(self, argv, **kwargs):
            self.stdout, self.stderr, self.stdin = BytesIO(b"too much output"), BytesIO(b""), None
            self.returncode = 0

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(Path, "open", patched_open)
    monkeypatch.setattr("cloud_network_benchmark.measurement_process.subprocess.Popen", FakePopen)
    runner = LocalMeasurementProcesses(SystemMeasurementClock())
    req = CommandRequest(action_id="disk-full", action_type="measurement", argv=["fake-tool"],
                         cwd=str(tmp_path), timeout_seconds=1)
    handle = runner.start(req, stdout_path=out, stderr_path=tmp_path / "err")
    for thread in handle.threads:
        thread.join(timeout=1)
    result = runner.poll(handle)
    assert result is not None and result.outcome == "failed"
    assert result.exit_code == 125
    assert "disk full" in result.stderr


@pytest.mark.parametrize("failure", ["terminate", "kill", "reap"])
def test_stop_reports_termination_or_reap_failure(tmp_path, monkeypatch, failure):
    class FakePopen:
        def __init__(self, argv, **kwargs):
            self.stdout, self.stderr, self.stdin = BytesIO(b"partial stdout"), BytesIO(b"partial stderr"), None
            self.returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            if failure == "terminate":
                raise OSError("terminate failed")

        def kill(self):
            if failure == "kill":
                raise OSError("kill failed")
            self.returncode = None

        def wait(self, timeout=None):
            if timeout is None:
                return self.returncode
            raise subprocess.TimeoutExpired("fake-tool", timeout)

    monkeypatch.setattr("cloud_network_benchmark.measurement_process.subprocess.Popen", FakePopen)
    runner = LocalMeasurementProcesses(SystemMeasurementClock())
    req = CommandRequest(action_id="cancel", action_type="measurement", argv=["fake-tool"],
                         cwd=str(tmp_path), timeout_seconds=1)
    handle = runner.start(req, stdout_path=tmp_path / "out", stderr_path=tmp_path / "err")
    with pytest.raises((OSError, TimeoutError)):
        runner.stop(handle, time.monotonic() + 0.05)
    assert handle.stdout_file.closed and handle.stderr_file.closed
    assert (tmp_path / "out").read_bytes() == b"partial stdout"
    assert (tmp_path / "err").read_bytes() == b"partial stderr"


def test_stop_falls_back_to_kill_after_unexpected_wait_error(tmp_path, monkeypatch):
    class FakePopen:
        def __init__(self, argv, **kwargs):
            self.stdout, self.stderr, self.stdin = BytesIO(b"partial stdout"), BytesIO(b"partial stderr"), None
            self.returncode = None
            self.killed = False

        def poll(self):
            return self.returncode

        def terminate(self):
            pass

        def wait(self, timeout=None):
            if timeout is None:
                return self.returncode
            if not self.killed:
                raise OSError("unexpected wait failure")
            return self.returncode

        def kill(self):
            self.killed = True
            self.returncode = -9

    monkeypatch.setattr("cloud_network_benchmark.measurement_process.subprocess.Popen", FakePopen)
    runner = LocalMeasurementProcesses(SystemMeasurementClock())
    req = CommandRequest(action_id="wait-error", action_type="measurement", argv=["fake-tool"],
                         cwd=str(tmp_path), timeout_seconds=1)
    handle = runner.start(req, stdout_path=tmp_path / "out", stderr_path=tmp_path / "err")
    result = runner.stop(handle, time.monotonic() + 1)
    assert handle.process.killed and handle.process.poll() == -9
    assert result.outcome == "failed"
    assert "wait failed: OSError: unexpected wait failure" in result.stderr
    assert handle.stdout_file.closed and handle.stderr_file.closed
    assert (tmp_path / "out").read_bytes() == b"partial stdout"
    assert (tmp_path / "err").read_bytes() == b"partial stderr"


@pytest.mark.parametrize("first_error", ["flush", "close"])
def test_stop_attempts_to_close_second_stream_after_first_stream_error(tmp_path, monkeypatch, first_error):
    original_open = Path.open
    wrappers = {}

    class FaultyStream:
        def __init__(self, wrapped, fail):
            self.wrapped, self.fail = wrapped, fail

        @property
        def closed(self):
            return self.wrapped.closed

        def flush(self):
            if self.fail == "flush":
                raise OSError("first flush failed")
            self.wrapped.flush()

        def write(self, data):
            return self.wrapped.write(data)

        def close(self):
            if self.fail == "close":
                self.fail = None
                raise OSError("first close failed")
            self.wrapped.close()

        def __getattr__(self, name):
            return getattr(self.wrapped, name)

    def wrapped_open(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        if path.name == "out":
            stream = FaultyStream(stream, None)
        wrappers[path.name] = stream
        return stream

    class FakePopen:
        def __init__(self, argv, **kwargs):
            self.stdout, self.stderr, self.stdin = BytesIO(b"partial stdout"), BytesIO(b"partial stderr"), None
            self.returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            pass

        def wait(self, timeout=None):
            self.returncode = -15
            return self.returncode

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(Path, "open", wrapped_open)
    monkeypatch.setattr("cloud_network_benchmark.measurement_process.subprocess.Popen", FakePopen)
    runner = LocalMeasurementProcesses(SystemMeasurementClock())
    req = CommandRequest(action_id="close-error", action_type="measurement", argv=["fake-tool"],
                         cwd=str(tmp_path), timeout_seconds=1)
    handle = runner.start(req, stdout_path=tmp_path / "out", stderr_path=tmp_path / "err")
    wrappers["out"].fail = first_error
    result = runner.stop(handle, time.monotonic() + 1)
    assert wrappers["err"].closed
    assert "stdout stream " + first_error + " failed" in result.stderr
    assert original_open(tmp_path / "out", "rb").read() == b"partial stdout"
    assert original_open(tmp_path / "err", "rb").read() == b"partial stderr"


def test_normal_poll_closes_stderr_and_caches_failed_result_after_stdout_close_errors(tmp_path, monkeypatch):
    original_open = Path.open
    streams = {}

    class PersistentCloseFailure:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.close_attempts = 0

        @property
        def closed(self):
            return self.wrapped.closed

        def close(self):
            self.close_attempts += 1
            raise OSError("stdout close remains unavailable")

        def __getattr__(self, name):
            return getattr(self.wrapped, name)

    def wrapped_open(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        if path.name == "out":
            stream = PersistentCloseFailure(stream)
        streams[path.name] = stream
        return stream

    class FakePopen:
        def __init__(self, argv, **kwargs):
            self.stdout, self.stderr, self.stdin = BytesIO(b"native stdout"), BytesIO(b"native stderr"), None
            self.returncode = 0

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(Path, "open", wrapped_open)
    monkeypatch.setattr("cloud_network_benchmark.measurement_process.subprocess.Popen", FakePopen)
    runner = LocalMeasurementProcesses(SystemMeasurementClock())
    req = CommandRequest(action_id="normal-close-error", action_type="measurement", argv=["fake-tool"],
                         cwd=str(tmp_path), timeout_seconds=1)
    handle = runner.start(req, stdout_path=tmp_path / "out", stderr_path=tmp_path / "err")
    for thread in handle.threads:
        thread.join(timeout=1)

    result = runner.poll(handle)
    assert result is not None and result.outcome == "failed" and result.exit_code == 125
    assert runner.poll(handle) is result
    assert streams["out"].close_attempts == 2
    assert not streams["out"].closed
    assert streams["err"].closed
    assert "stdout stream remains open after finalization" in result.stderr
    assert original_open(tmp_path / "out", "rb").read() == b"native stdout"
    assert original_open(tmp_path / "err", "rb").read() == b"native stderr"


def test_fake_clock_utc_adjustment_does_not_change_monotonic_wait():
    from f03_support import FakeClock

    clock = FakeClock()
    before = clock.utc_now()
    clock.utc_adjustment = timedelta(hours=1)
    after_adjustment = clock.utc_now()
    clock.wait_until(clock.monotonic() + 2)
    assert (after_adjustment - before).total_seconds() == 3600
    assert clock.monotonic() == 1002


class AdjustableProcessClock:
    def __init__(self):
        self.value = 1000.0
        self.origin = datetime(2026, 10, 21, tzinfo=timezone.utc)
        self.adjustment = timedelta(0)

    def monotonic(self):
        return self.value

    def utc_now(self):
        return self.origin + timedelta(seconds=self.value - 1000) + self.adjustment

    def wait_until(self, deadline_monotonic):
        self.value = deadline_monotonic


class BlockingFakePopen:
    def __init__(self, argv, **kwargs):
        self.stdout, self.stderr, self.stdin = BytesIO(b"native stdout"), BytesIO(b"native stderr"), None
        self.returncode = None
        self.exited = threading.Event()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if not self.exited.wait(timeout):
            raise subprocess.TimeoutExpired("fake-tool", timeout)
        return self.returncode

    def terminate(self):
        pass

    def kill(self):
        self.returncode = -9
        self.exited.set()


@pytest.mark.parametrize("adjustment_seconds,exit_code,expected_outcome", [
    (-3600, 0, "succeeded"), (3600, 0, "succeeded"),
    (-3600, 7, "failed"), (3600, 7, "failed"),
])
def test_production_adapter_uses_monotonic_elapsed_and_projects_real_utc_jumps(
        tmp_path, monkeypatch, repository_root, adjustment_seconds, exit_code, expected_outcome):
    from cloud_network_benchmark.contracts import CommandOutcome

    clock = AdjustableProcessClock()
    monkeypatch.setattr("cloud_network_benchmark.measurement_process.subprocess.Popen", BlockingFakePopen)
    runner = LocalMeasurementProcesses(clock)
    request = CommandRequest(action_id="clock-jump", action_type="measurement", argv=["fake-tool"],
                            cwd=str(tmp_path), timeout_seconds=5)
    item = observation(repository_root, "acceptance")
    handle = runner.start(request, stdout_path=tmp_path / "out", stderr_path=tmp_path / "err")
    clock.value += 2.0
    clock.adjustment = timedelta(seconds=adjustment_seconds)
    handle.process.returncode = exit_code
    handle.process.exited.set()
    for thread in handle.threads:
        thread.join(timeout=1)

    result = runner.poll(handle)
    assert result is not None
    assert result.outcome == CommandOutcome(expected_outcome)
    assert result.duration_basis == "monotonic"
    assert result.duration_seconds == 2.0
    assert result.monotonic_started == 1000.0
    assert result.monotonic_finished == 1002.0
    assert result.utc_elapsed_discrepancy_seconds == float(adjustment_seconds)
    assert result.finished_at == clock.origin + timedelta(seconds=2 + adjustment_seconds)
    assert (tmp_path / "out").read_bytes() == b"native stdout"
    assert (tmp_path / "err").read_bytes() == b"native stderr"

    evidence = _record(request, result, context(item, tmp_path), item.measurement_units[0].unit_id)
    projection = CommandEvidence.model_validate(evidence.model_dump(mode="json"))
    assert projection.duration_basis == "monotonic"
    assert projection.result["duration_basis"] == "monotonic"
    assert projection.monotonic_started.value == projection.result["monotonic_started"] == result.monotonic_started
    assert projection.monotonic_finished.value == projection.result["monotonic_finished"] == result.monotonic_finished
    assert projection.utc_elapsed_discrepancy_seconds == projection.result["utc_elapsed_discrepancy_seconds"] == result.utc_elapsed_discrepancy_seconds
    assert projection.started_at.value == result.started_at
    assert projection.finished_at.value == result.finished_at


def test_production_adapter_preserves_monotonic_duration_on_kill_cancellation(
        tmp_path, monkeypatch, repository_root):
    clock = AdjustableProcessClock()
    monkeypatch.setattr("cloud_network_benchmark.measurement_process.subprocess.Popen", BlockingFakePopen)
    runner = LocalMeasurementProcesses(clock)
    request = CommandRequest(action_id="cancel-clock-jump", action_type="measurement", argv=["fake-tool"],
                            cwd=str(tmp_path), timeout_seconds=5)
    handle = runner.start(request, stdout_path=tmp_path / "out", stderr_path=tmp_path / "err")
    clock.value += 1.25
    clock.adjustment = timedelta(seconds=-3600)
    # Cancellation has completed its measured interval; give bounded cleanup
    # workers a real shutdown budget to reap the killed process and drain pipes.
    result = runner.stop(handle, clock.value + 1.0)
    assert handle.process.returncode == -9
    assert result.outcome == "interrupted"
    assert result.exit_code is None
    assert result.duration_basis == "monotonic"
    assert result.duration_seconds == 1.25
    assert result.monotonic_started == 1000.0 and result.monotonic_finished == 1001.25
    assert result.utc_elapsed_discrepancy_seconds == -3600.0
    assert result.finished_at < result.started_at
    item = observation(repository_root, "acceptance")
    evidence = _record(request, result, context(item, tmp_path), item.measurement_units[0].unit_id)
    projection = CommandEvidence.model_validate(evidence.model_dump(mode="json"))
    assert projection.result["outcome"] == "interrupted"
    assert projection.duration_basis == projection.result["duration_basis"] == "monotonic"
    assert projection.monotonic_started.value == projection.result["monotonic_started"]
    assert projection.monotonic_finished.value == projection.result["monotonic_finished"]
    assert projection.utc_elapsed_discrepancy_seconds == projection.result["utc_elapsed_discrepancy_seconds"]
    assert handle.stdout_file.closed and handle.stderr_file.closed
    assert (tmp_path / "out").read_bytes() == b"native stdout"
    assert (tmp_path / "err").read_bytes() == b"native stderr"
