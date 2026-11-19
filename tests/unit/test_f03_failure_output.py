from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

from cloud_network_benchmark.access import RemoteActionEvidence
from cloud_network_benchmark.lifecycle_adapters import (
    _F03_FAILURE_PREVIEW_BYTES,
    _f03_best_effort_diagnostic,
    _f03_failure_output_launch_guard,
    _f03_failure_output_reader_script,
    _f03_failure_output_timeout,
    _f03_guest_supervisor_script,
    _f03_python_exec_argument,
    _f03_serialize_failure_output,
)
from cloud_network_benchmark.lifecycle_projection import _remote_action_diagnostic


def _run_supervisor(tmp_path: Path, output: str, *, stderr: str = "", status: int = 7,
                    split_at: int | None = None, precreate_record: bool = False):
    package = tmp_path / "cloud_network_benchmark"
    package.mkdir()
    (package / "__init__.py").write_text("")
    chunks = [output.encode()] if split_at is None else [output.encode()[:split_at], output.encode()[split_at:]]
    code = "import sys; " + "; ".join("sys.stdout.buffer.write(" + repr(part) + "); sys.stdout.flush()" for part in chunks)
    code += "; sys.stderr.write(" + repr(stderr) + "); sys.stderr.flush()"
    (package / "lifecycle_guest.py").write_text(code + "; sys.exit(" + str(status) + ")\n")
    failure_dir = tmp_path / "failure-output"
    failure_dir.mkdir(mode=0o700)
    if precreate_record:
        (failure_dir / "failure-output.json").write_text("do not overwrite")
    request, outcome, status_path = (tmp_path / "request.json", tmp_path / "outcome.json", tmp_path / "status")
    request.write_text("{}")
    proc = subprocess.run([sys.executable, "-c", _f03_guest_supervisor_script(), str(failure_dir),
        "a" * 32, str(status_path), sys.executable, str(tmp_path), str(request), str(outcome)],
        capture_output=True, timeout=10)
    return proc, failure_dir, status_path, outcome


def test_supervisor_drains_and_redacts_before_bounded_tail_and_preserves_guest_status(tmp_path):
    secret = '"password": "quoted multi word secret"'
    key = "AKIA1234567890123456"
    pem = "-----BEGIN RSA PRIVATE KEY-----\n" + "PRIVATE" * 1000 + "\n-----END RSA PRIVATE KEY-----"
    output = secret + "\n" + "x" * 9000 + key + "\n" + pem + "\nBearer abc.def.ghi\n" + "tail" * 300
    proc, directory, status, outcome = _run_supervisor(tmp_path, output, stderr="Bearer abc.def.ghi\n")
    assert proc.returncode == 7
    assert status.read_text().strip() == "7"
    assert not outcome.exists()
    assert (directory / "failure-output.json").stat().st_mode & 0o777 == 0o600
    record = json.loads((directory / "failure-output.json").read_text())
    encoded = json.dumps(record)
    for forbidden in ("quoted multi word secret", key, "BEGIN RSA PRIVATE KEY", "PRIVATEPRIVATE", "abc.def.ghi"):
        assert forbidden not in encoded
    for stream in record["streams"].values():
        assert len(stream["preview"].encode()) <= _F03_FAILURE_PREVIEW_BYTES
    assert record["streams"]["stdout"]["redactions_applied"] is True
    assert record["streams"]["stderr"]["redactions_applied"] is True
    stderr_record = record["streams"]["stderr"]
    assert stderr_record["source_bytes"] == len(b"Bearer abc.def.ghi\n")
    assert stderr_record["sha256"] == hashlib.sha256(b"Bearer abc.def.ghi\n").hexdigest()
    assert record["streams"]["stdout"]["source_bytes"] == len(output.encode())
    action = RemoteActionEvidence(action_id="f09-guest-failure-output-" + "a" * 32,
        action_type="ssh", vm_id="vm-a", role="vm_a",
        request={"action_id": "safe", "action_type": "ssh"},
        result={"outcome": "succeeded", "exit_code": 0, "stdout": json.dumps(record), "stderr": ""})
    durable = _remote_action_diagnostic(action)
    serialized = json.dumps(durable, separators=(",", ":"))
    assert len(serialized) < 4096
    assert json.loads(serialized)["result"]["stdout"] == json.dumps(record)


def test_supervisor_sanitizes_chunk_boundary_and_fails_closed_on_unterminated_pem(tmp_path):
    # The key is split by pipe writes, while the sanitizer consumes a complete bounded line.
    text = "AKIA12345678" + "90123456" + "\n-----BEGIN OPENSSH PRIVATE KEY-----\n" + "PRIVATE" * 900
    split = text.index("90123456")
    proc, directory, status, outcome = _run_supervisor(tmp_path, text, split_at=split)
    assert proc.returncode == 7
    row = json.loads((directory / "failure-output.json").read_text())["streams"]["stdout"]
    assert "AKIA1234567890123456" not in row["preview"]
    assert "PRIVATEPRIVATE" not in row["preview"]
    assert row["preview_truncated"] is True


@pytest.mark.parametrize("secret", [
    '"password": "quoted multi word secret"',
    "'aws_secret_access_key': 'quoted value with spaces'",
    '"authorization": "Bearer quoted bearer secret"',
    "token = value with whitespace",
])
def test_secrets_are_redacted_before_the_retained_tail(secret, tmp_path):
    proc, directory, _, _ = _run_supervisor(tmp_path, "ordinary preface\n" + secret + "\n")
    assert proc.returncode == 7
    record = json.loads((directory / "failure-output.json").read_text())
    preview = record["streams"]["stdout"]["preview"]
    assert "[REDACTED]" in preview
    assert secret.split(":")[-1].strip(" '\"") not in preview


def test_multiline_pem_with_short_lines_is_redacted_when_header_leaves_preview(tmp_path):
    text = "-----BEGIN RSA PRIVATE KEY-----\n" + "private-body-fragment\n" * 40 + "-----END RSA PRIVATE KEY-----\n"
    proc, directory, _, _ = _run_supervisor(tmp_path, text)
    record = json.loads((directory / "failure-output.json").read_text())
    preview = record["streams"]["stdout"]["preview"]
    assert proc.returncode == 7
    assert "private-body-fragment" not in preview
    assert "[REDACTED_PRIVATE_KEY]" in preview


def test_final_unicode_control_and_no_newline_preview_stays_byte_bounded(tmp_path):
    text = ("earlier\n" + "ordinary\n" * 80 + "☃\x01" * 100 + "final-no-newline-☃")
    proc, directory, _, _ = _run_supervisor(tmp_path, text)
    record = json.loads((directory / "failure-output.json").read_text())
    preview = record["streams"]["stdout"]["preview"]
    assert proc.returncode == 7
    assert len(preview.encode("utf-8")) <= _F03_FAILURE_PREVIEW_BYTES
    assert "\x01" not in preview


def test_diagnostic_write_fault_keeps_original_guest_status(tmp_path):
    proc, directory, status, _ = _run_supervisor(tmp_path, "password=should redact\n", precreate_record=True)
    assert proc.returncode == 7
    assert status.read_text().strip() == "7"
    assert (directory / "failure-output.json").read_text() == "do not overwrite"


def test_launch_guard_rejects_preexisting_process_record(tmp_path):
    process = tmp_path / "process.json"; process.write_text("owned by another invocation")
    failure_dir = tmp_path / "failure-output"
    result = subprocess.run(["sh", "-c", "set -eu; " + _f03_failure_output_launch_guard(str(process), str(failure_dir))],
        capture_output=True, timeout=5)
    assert result.returncode != 0
    assert not failure_dir.exists()


def test_exhausted_existing_cleanup_budget_skips_transfer_and_controller_errors_are_best_effort():
    class Deadline:
        def remaining(self, clock, *, cleanup=False):
            return 0.0

    assert _f03_failure_output_timeout(Deadline(), lambda: 10.0, 20.0, 45.0) == 0.0
    called = []
    def fail():
        raise RecursionError("diagnostic collection fault")
    _f03_best_effort_diagnostic(fail, lambda: called.append("unavailable"))
    assert called == ["unavailable"]


def test_supervisor_success_discards_failure_output_and_writes_zero_status(tmp_path):
    proc, directory, status, outcome = _run_supervisor(tmp_path, "ordinary output", status=0)
    assert proc.returncode == 0
    assert status.read_text().strip() == "0"
    assert not (directory / "failure-output.json").exists()
    assert not outcome.exists()


def test_remote_reader_is_size_and_symlink_bounded(tmp_path):
    directory = tmp_path / "failure-output"
    directory.mkdir(mode=0o700)
    payload = directory / "failure-output.json"
    payload.write_bytes(b"small"); payload.chmod(0o600)
    command = [sys.executable, "-c", _f03_failure_output_reader_script(str(payload)), str(payload)]
    good = subprocess.run(command, capture_output=True, check=True, timeout=5)
    import base64
    assert base64.b64decode(good.stdout.strip()) == b"small"
    target = tmp_path / "target"; target.write_bytes(b"secret"); target.chmod(0o600)
    payload.unlink(); payload.symlink_to(target)
    bad_link = subprocess.run(command, capture_output=True, timeout=5)
    assert bad_link.returncode != 0
    payload.unlink(); payload.write_bytes(b"x" * 32769); payload.chmod(0o600)
    bad_size = subprocess.run(command, capture_output=True, timeout=5)
    assert bad_size.returncode != 0


def test_generated_remote_helpers_are_printable_transport_arguments():
    for script in (_f03_guest_supervisor_script(), _f03_failure_output_reader_script("/var/lib/cnb/control/a.json")):
        argument = _f03_python_exec_argument(script)
        assert argument.startswith("exec(bytes.fromhex('")
        assert all(32 <= ord(char) < 127 for char in argument)
        assert "\n" not in argument and "\r" not in argument


@pytest.mark.parametrize("collection", ["success", "exhausted", "controller-error",
    "schema-bool", "counter-bool"])
def test_production_run_remote_f03_preserves_failure_and_collects_only_with_budget(
        repository_root, tmp_path, collection):
    import base64
    from datetime import datetime, timezone
    from cloud_network_benchmark.lifecycle_adapters import GuestRuntimeEvidence, run_remote_f03
    from cloud_network_benchmark.lifecycle_guest import package_tree_sha256
    from tests.fixtures.f02r.support import connection, deadline, result
    from test_f09_guest import _request

    connected = connection(tmp_path)
    base, _ = _request(repository_root, tmp_path)
    runtime_root = f"/var/lib/cnb/runtime/{connected.deployment_id}/runtime"
    digest = package_tree_sha256(repository_root / "src/cloud_network_benchmark")
    request = type(base).model_validate({**base.model_dump(mode="python"),
        "identity": connected.identity.model_dump(mode="python"),
        "observation": {**base.observation.model_dump(mode="python"), "provider": "aws"},
        "output_root": f"{runtime_root}/results",
        "package_root": f"{runtime_root}/source/cloud_network_benchmark",
        "source_revision": "a" * 40, "expected_source_revision": "a" * 40,
        "package_sha256": digest})
    runtime = GuestRuntimeEvidence("a" * 40, digest, "b" * 64,
        runtime_root, "3.12.3", "2.14.0", "6.0.2", ())
    invocation_holder = {}
    sidecar = {"schema_version": 1, "invocation_id": "", "streams": {}}
    for name in ("stdout", "stderr"):
        sidecar["streams"][name] = {"preview": "[REDACTED] tail", "sha256": "a" * 64,
            "source_bytes": 500, "preview_truncated": True, "redactions_applied": True,
            "redaction_count": 1,
            "hash_basis": "complete guest stream sha256; preview is bounded tail after redaction"}

    class Runner:
        def __init__(self): self.requests = []
        def run(self, command):
            self.requests.append(command)
            aid = command.action_id
            if aid.startswith("f09-guest-run-"):
                invocation = aid.removeprefix("f09-guest-run-")
                invocation_holder["id"] = invocation
                sidecar["invocation_id"] = invocation
                assert all(32 <= ord(ch) < 127 for arg in command.argv for ch in arg)
                return result(aid, json.dumps({"deployment_id": connected.deployment_id,
                    "invocation_id": invocation, "pid": 25001, "process_group": 25001,
                    "session_id": 25001, "boot_id": "synthetic-boot", "start_ticks": 12345}))
            if aid.startswith("f09-guest-status-"):
                return result(aid, "exited 7")
            if aid.startswith("f09-guest-cancel-"):
                return result(aid, "process_group_stopped")
            if aid.startswith("f09-guest-outcome-"):
                return result(aid, success=False, stderr="missing outcome")
            if aid.startswith("f09-guest-failure-output-"):
                if collection == "controller-error": raise RecursionError("synthetic diagnostic failure")
                assert command.action_type == "ssh"
                assert command.timeout_seconds <= connected.timeouts.transfer_seconds
                payload = json.loads(json.dumps(sidecar))
                if collection == "schema-bool": payload["schema_version"] = True
                if collection == "counter-bool": payload["streams"]["stdout"]["redaction_count"] = True
                return result(aid, base64.b64encode(json.dumps(payload).encode()).decode())
            return result(aid)

    runner = Runner()
    report = run_remote_f03(connected, request, runner=runner, runtime=runtime,
        deadline=deadline(), clock=lambda: 100.0,
        shutdown_deadline_monotonic=(100.0 if collection == "exhausted" else 200.0),
        retain_failure_output=True)
    assert "exited with status 7" in report.failure
    diagnostic_actions = [a for a in report.actions if "f09-guest-failure-output-" in a.action_id]
    assert len(diagnostic_actions) == 1
    if collection == "success":
        assert json.loads(diagnostic_actions[0].result["stdout"])["invocation_id"] == invocation_holder["id"]
        assert diagnostic_actions[0].result["outcome"] == "succeeded"
    elif collection == "exhausted":
        assert diagnostic_actions[0].result["outcome"] == "unavailable"
        assert not any(a.action_id.startswith("f09-guest-failure-output-") and a.request.get("argv")
            for a in report.actions)
    elif collection == "controller-error":
        assert diagnostic_actions[0].result["outcome"] == "failed"
        assert "failure output unavailable: bounded failure-output transfer failed" in diagnostic_actions[0].result["stderr"]
    else:
        assert diagnostic_actions[0].result["outcome"] == "succeeded"
        assert "bounded failure-output file was invalid" in diagnostic_actions[0].result["stderr"]


def test_failure_metadata_rejects_oversize_durable_envelope():
    with pytest.raises(ValueError, match="4096"):
        _f03_serialize_failure_output({"preview": "x" * 4097})
