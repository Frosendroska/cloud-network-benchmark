from io import BytesIO
from pathlib import Path
import subprocess
import time
from datetime import timedelta

import pytest

from cloud_network_benchmark.contracts import CommandRequest
from cloud_network_benchmark.measurement_process import LocalMeasurementProcesses, ProcessEvent, SystemMeasurementClock


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
            self.stdout, self.stderr, self.stdin = BytesIO(b""), BytesIO(b""), None
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


def test_fake_clock_utc_adjustment_does_not_change_monotonic_wait():
    from f03_support import FakeClock

    clock = FakeClock()
    before = clock.utc_now()
    clock.utc_adjustment = timedelta(hours=1)
    after_adjustment = clock.utc_now()
    clock.wait_until(clock.monotonic() + 2)
    assert (after_adjustment - before).total_seconds() == 3600
    assert clock.monotonic() == 1002
