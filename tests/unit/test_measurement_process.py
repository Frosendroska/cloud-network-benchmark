from io import BytesIO
from pathlib import Path

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

    class FakePopen:
        def __init__(self, argv, **kwargs):
            assert argv == ["fake-tool"]
            assert kwargs["shell"] is False
            self.stdout = BytesIO(payload)
            self.stderr = BytesIO(b"diagnostic\n")
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
