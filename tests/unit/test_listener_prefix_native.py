"""Execute the emitted owner program against real Linux process/file evidence."""
from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="requires Linux /proc")
@pytest.mark.parametrize("case", ["complete", "partial_line", "oversized", "wrong_descriptor"])
def test_emitted_owner_program_captures_only_bounded_complete_live_prefix(tmp_path, monkeypatch, case):
    # Extract the actual guest program, rather than a fake runner's invented receipt.
    module = Path(__file__).parents[2] / "src/cloud_network_benchmark/lifecycle_adapters.py"
    tree = ast.parse(module.read_text())
    function = next(node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "stop_vm_b_listeners")
    assignment = next(node for node in ast.walk(function)
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
            and target.id == "owner_source" for target in node.targets))
    source = "\n".join(ast.literal_eval(item) for item in assignment.value.args[0].elts)
    prefix = b'{"event":"end","data":{}}\n'
    if case == "partial_line":
        prefix = prefix[:-1]
    elif case == "oversized":
        prefix = b"x" * 65536 + b"\n"
    stdout_path, stderr_path = tmp_path / "server.jsonl", tmp_path / "server.err"
    stdout_path.write_bytes(prefix)
    stderr_path.write_bytes(b"")
    other_path = tmp_path / "other.jsonl"
    other_path.write_bytes(prefix)
    with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=stdout, stderr=stderr)
    unit = "cnb-native-prefix-iperf3"
    cgroup = f"/system.slice/{unit}.service"
    original_read = Path.read_text

    def read_text(path, *args, **kwargs):
        # The test has no systemd service: substitute only its cgroup membership.
        # PID start ticks, boot ID, file descriptors, mode, bytes and hashes are native.
        if str(path) == f"/proc/{process.pid}/cgroup":
            return f"0::{cgroup}\n"
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(sys, "argv", ["owner", str(process.pid), unit,
        str(other_path if case == "wrong_descriptor" else stdout_path), str(stderr_path),
        "controller-session", "native-prefix"])
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            if case == "wrong_descriptor":
                with pytest.raises(SystemExit) as failure:
                    exec(compile(source, "emitted-listener-owner", "exec"), {})
                assert failure.value.code == 4
                return
            exec(compile(source, "emitted-listener-owner", "exec"), {})
        receipt = json.loads(output.getvalue())
        snapshot = receipt["pre_stop_prefix"]
        if case != "complete":
            assert snapshot["available"] is False
            return
        assert snapshot["available"] is True
        assert snapshot["prefix_bytes"] == len(prefix)
        assert snapshot["prefix_sha256"] == hashlib.sha256(prefix).hexdigest()
        assert snapshot["pid"] == process.pid
        assert snapshot["start_ticks"] == receipt["start_ticks"]
        assert snapshot["log_ids"] == receipt["log_ids"]
        assert snapshot["controller_session_id"] == "controller-session"
        assert snapshot["deployment_id"] == "native-prefix"
        assert snapshot["boundary"] == "pre_stop"
    finally:
        process.terminate()
        process.wait(timeout=3)
