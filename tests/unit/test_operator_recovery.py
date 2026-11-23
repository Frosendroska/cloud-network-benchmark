from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from cloud_network_benchmark import operator_recovery as recovery


def _wait_stopped(pid: int) -> bool:
    end = time.monotonic() + 3
    while time.monotonic() < end:
        try:
            raw = Path(f"/proc/{pid}/stat").read_text()
            if raw[raw.rfind(")") + 2:].split()[0] in {"T", "t"}:
                return True
        except OSError:
            return True
        time.sleep(0.02)
    return False


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux /proc and pidfds")
def test_fixed_point_freezes_and_retires_owned_child_in_different_session():
    ready = Path(f"/tmp/cnb-op-recovery-{os.getpid()}.json")
    ready.unlink(missing_ok=True)
    script = ("import subprocess,sys,time,json,pathlib; "
              "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(90)'],start_new_session=True); "
              f"pathlib.Path({str(ready)!r}).write_text(json.dumps({{'child':p.pid}})); "
              "time.sleep(90)")
    root = subprocess.Popen([sys.executable, "-c", script], start_new_session=True)
    tree = {}
    child_fd = None
    try:
        end = time.monotonic() + 4
        while time.monotonic() < end and not ready.exists():
            time.sleep(0.02)
        child_pid = json.loads(ready.read_text())["child"]
        child_fd = os.pidfd_open(child_pid, 0)
        tree, frozen = recovery.freeze_fixed_point(root.pid, expected_uid=os.getuid())
        assert {root.pid, child_pid} <= set(tree)
        assert tree[root.pid].boot_id == tree[child_pid].boot_id
        assert all(_wait_stopped(pid) for pid in (root.pid, child_pid))
        recovery.terminate_tree(tree)
        assert all(not recovery._is_alive(item) for item in tree.values())
    finally:
        ready.unlink(missing_ok=True)
        recovery.thaw_tree(locals().get("frozen", []))
        recovery.close_tree(tree)
        if child_fd is not None:
            try:
                signal.pidfd_send_signal(child_fd, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.close(child_fd)
        for proc in (root,):
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=3)


def test_checkpoint_is_create_only_and_rejects_symlink(tmp_path):
    target = tmp_path / "checkpoint.json"
    recovery.write_checkpoint(target, {"schema_version": 1, "attempt": "registered"})
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        recovery.write_checkpoint(target, {})
    linked = tmp_path / "linked.json"
    linked.symlink_to(target)
    with pytest.raises(FileExistsError):
        recovery.write_checkpoint(linked, {})


def test_expiry_proof_is_target_registration_and_time_bound():
    expiry = datetime.now(timezone.utc)
    readback_path = Path(f"/tmp/cnb-expiry-readback-{os.getpid()}.json")
    readback_path.write_text('{"state":"deallocated"}')
    readback_path.chmod(0o600)
    import hashlib
    window = {"expiry_at": expiry.isoformat(),
        "latest_safe_cleanup_start": (expiry + timedelta(seconds=60)).isoformat(),
        "registration_id": "original-registration", "target_selector": "exact-target",
        "binding_sha256": "a" * 64, "provider": "azure", "expected_state": "deallocated"}
    proof = {"schema_version": 1, "binding_sha256": "a" * 64, "provider": "azure",
        "registration_id": "original-registration", "target_selector": "exact-target",
        "observed_state": "deallocated", "read_only": True, "captured_by": "operator-readback",
        "read_only_command": "provider-native-read-only-command", "readback_path": str(readback_path),
        "readback_sha256": hashlib.sha256(readback_path.read_bytes()).hexdigest(),
        "observed_at": (expiry + timedelta(seconds=2)).isoformat()}
    assert recovery.cleanup_decision(window, now=expiry + timedelta(seconds=2), proof=proof) == (
        True, "provider_expiry_proved")
    assert recovery.cleanup_decision(window, now=expiry + timedelta(seconds=59), proof=None)[0] is False
    assert recovery.cleanup_decision(window, now=expiry + timedelta(seconds=60), proof=None) == (
        True, "expiry_unproved_cleanup_priority")
    wrong = {**proof, "target_selector": "foreign-target"}
    assert recovery.cleanup_decision(window, now=expiry + timedelta(seconds=5), proof=wrong)[0] is False
    future = {**proof, "observed_at": (expiry + timedelta(seconds=70)).isoformat()}
    assert recovery.cleanup_decision(window, now=expiry + timedelta(seconds=5), proof=future)[0] is False
    wrong_unit = {**proof, "target_selector": "exact-target"}
    wrong_unit["readback_sha256"] = "0" * 64
    assert recovery.cleanup_decision(window, now=expiry + timedelta(seconds=5), proof=wrong_unit)[0] is False
    readback_path.unlink()


def test_cleanup_command_is_fixed_to_saved_recovery_and_session():
    command = recovery.cleanup_command(Path("/private/recovery.json"), "original-session")
    assert command[1:4] == ["-m", "cloud_network_benchmark", "lifecycle"]
    assert command[-4:] == ["--controller-session", "original-session", "--format", "json"]
    assert "run" not in command and "campaign" not in command and "--cap" not in command


@pytest.mark.parametrize("provider", ["aws", "azure", "gcp"])
def test_original_expiry_window_reads_provider_native_journal_shape(tmp_path, monkeypatch, provider):
    from cloud_network_benchmark.contracts.admission import ExpiryEvidence
    from cloud_network_benchmark import lifecycle_cleanup

    now = datetime.now(timezone.utc).replace(microsecond=0)
    expires = now + timedelta(minutes=20)
    cap = expires + timedelta(minutes=25)
    run_id = f"operator-test-{provider}"
    deployment_id = f"{run_id}-deployment"
    request_time = now + timedelta(minutes=10, seconds=23)
    target = "vm-one;vm-two"
    reg_id = "original-native-registration"
    journal_path = tmp_path / f"{provider}-journal.json"
    settings_path = tmp_path / f"{provider}-cleanup-settings.json"
    settings_path.write_text("synthetic typed settings source is supplied by the loader\n")
    settings_path.chmod(0o600)
    metadata = ({"stop_is_not_deletion": True} if provider == "aws" else
        {"native_absolute_stop": True} if provider == "gcp" else {
            "mechanism": "azure_automation_once_deallocate", "trigger": request_time.replace(second=0).isoformat(),
            "automation_account_id": "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Automation/automationAccounts/acct",
            "job_schedule_id": "schedule-one", "api_version": "2023-11-01",
            "target_ids": ["vm-one", "vm-two"]})
    expiry_row = {"schema_version": 2, "run_id": run_id, "deployment_id": deployment_id,
        "provider": provider, "action": "deallocate" if provider == "azure" else "stop",
        "state": "registered", "registration_id": {"value": reg_id},
        "registered_at": {"value": now.isoformat()}, "expires_at": {"value": request_time.isoformat()},
        "target_selector": target, "provider_metadata": {"value": metadata}}
    journal = ({"run_id": run_id, "deployment_id": deployment_id,
        **({"expiry_evidence": expiry_row} if provider == "aws" else
           {"expiry": ({"state": "registered", "metadata": metadata} if provider == "azure" else expiry_row)})})
    journal_path.write_text(json.dumps(journal) + "\n")
    journal_path.chmod(0o600)
    private_recovery = tmp_path / "lifecycle-recovery.json"
    private_recovery.write_text(json.dumps({"schema_version": 1, "attempt_id": "parent-campaign",
        "controller_session_id": "session-one", "providers": [{"provider": provider,
            "deployment_id": deployment_id, "journal_reference": str(journal_path),
            "expiry_registered": True, "creation_may_have_started": True, "cleanup_open": True,
            "cleanup_settings_reference": str(settings_path)}]}) + "\n")
    private_recovery.chmod(0o600)
    request = SimpleNamespace(provider=provider, action="deallocate" if provider == "azure" else "stop",
        expires_at=SimpleNamespace(value=request_time), target_selector=target)
    saved = SimpleNamespace(provider=SimpleNamespace(value=provider),
        deployment=SimpleNamespace(run_id=run_id, deployment_id=deployment_id,
            cleanup_timeout_seconds=660), reservation=SimpleNamespace(
                expires_at=SimpleNamespace(value=cap)), expiry_request=request)
    monkeypatch.setattr(lifecycle_cleanup, "load_cleanup_settings", lambda _: saved)
    monkeypatch.setattr(lifecycle_cleanup, "_validate_request", lambda _: None)
    monkeypatch.setattr(lifecycle_cleanup, "_expected_journal", lambda _: journal_path)

    window = recovery.original_expiry_window(private_recovery)
    assert window["provider"] == provider
    assert window["target_selector"] == target
    assert window["action"] == request.action
    assert window["cleanup_seconds"] == 660
    assert datetime.fromisoformat(window["latest_safe_cleanup_start"]) == cap - timedelta(seconds=660)
    if provider == "azure":
        assert window["registration_id"].endswith("/jobSchedules/schedule-one?api-version=2023-11-01")
        assert window["expected_state"] == "deallocated"
    else:
        assert window["registration_id"] == reg_id
        assert window["expected_state"] == ("stopped" if provider == "aws" else "terminated")


def test_missing_expiry_proof_claims_exact_cleanup_once(monkeypatch, tmp_path):
    recovery_path = tmp_path / "lifecycle-recovery.json"
    recovery_path.write_text("{}\n")
    recovery_path.chmod(0o600)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    window = {"provider": "aws", "expiry_at": (now - timedelta(minutes=1)).isoformat(),
        "latest_safe_cleanup_start": (now - timedelta(seconds=1)).isoformat(),
        "cleanup_seconds": 660, "cap_at": (now + timedelta(minutes=10)).isoformat(),
        "binding_sha256": "a" * 64, "expected_state": "stopped",
        "target_selector": "exact-target", "registration_id": "original-registration"}
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        kwargs["stdout"].write(json.dumps({"execution": "not_resumed", "status": "complete",
            "providers": {"aws": {"cleanup_open": False, "absence_proved": True,
                "billable_resources_present": False}}}).encode())
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(recovery.subprocess, "run", fake_run)
    checkpoint = tmp_path / "checkpoint.json"
    supervisor = recovery.RecoverySupervisor(checkpoint=checkpoint,
        binding={"provider": "aws", "recovery": str(recovery_path),
            "controller_session_id": "original-session", "repository_root": str(tmp_path),
            "bindings": {}, "original_expiry_request": window}, tree={})
    supervisor._update(phase="old_owners_dead")
    try:
        result = supervisor.wait_for_expiry_and_cleanup(recovery=recovery_path,
            proof_path=tmp_path / "missing-expiry-proof.json")
        assert result["expiry_status"] == "expiry_unproved_cleanup_priority"
        assert result["cleanup_complete"] is True
        assert len(calls) == 1
        assert calls[0][0] == recovery.cleanup_command(recovery_path, "original-session")
        assert calls[0][1]["timeout"] == 660
        assert calls[0][1]["cwd"] == str(tmp_path)
        assert Path(str(checkpoint) + ".cleanup-claimed").is_file()
    finally:
        supervisor.close()


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux /proc and pidfds")
def test_process_identity_comparison_detects_pid_reuse_or_mutation():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    tree = {}
    try:
        item = recovery._proc_identity(proc.pid)
        tree[proc.pid] = item
        changed = recovery.ProcIdentity(item.pid, item.ppid + 1, item.uid, item.boot_id,
            item.start_ticks, item.cwd, item.cmdline, item.fd, item.initially_stopped)
        assert not item.same(changed)
    finally:
        recovery.close_tree(tree)
        proc.terminate()
        proc.wait(timeout=3)


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux /proc and pidfds")
def test_uid_mismatch_refuses_capture():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        with pytest.raises(ValueError, match="UID mismatch"):
            recovery.capture_tree(proc.pid, expected_uid=os.getuid() + 1)
    finally:
        proc.terminate()
        proc.wait(timeout=3)


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux /proc and pidfds")
def test_partial_freeze_rolls_back_only_its_stops(monkeypatch):
    ready = Path(f"/tmp/cnb-op-rollback-{os.getpid()}.json")
    ready.unlink(missing_ok=True)
    script = ("import subprocess,sys,time,json,pathlib; "
              "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(90)'],start_new_session=True); "
              f"pathlib.Path({str(ready)!r}).write_text(json.dumps({{'child':p.pid}})); time.sleep(90)")
    root = subprocess.Popen([sys.executable, "-c", script], start_new_session=True)
    tree = {}
    child_fd = None
    try:
        end = time.monotonic() + 4
        while time.monotonic() < end and not ready.exists():
            time.sleep(0.02)
        tree = recovery.capture_tree(root.pid, expected_uid=os.getuid())
        child_pid = json.loads(ready.read_text())["child"]
        child_fd = os.pidfd_open(child_pid, 0)
        send = signal.pidfd_send_signal
        failed = False

        def once(pidfd, sig, *args, **kwargs):
            nonlocal failed
            if sig == signal.SIGSTOP and not failed:
                failed = True
                send(pidfd, sig, *args, **kwargs)
                raise RuntimeError("injected stop failure")
            return send(pidfd, sig, *args, **kwargs)

        monkeypatch.setattr(signal, "pidfd_send_signal", once)
        with pytest.raises(RuntimeError, match="injected stop failure"):
            recovery.freeze_tree(tree)
        assert all(not _wait_stopped(pid) for pid in tree)
    finally:
        ready.unlink(missing_ok=True)
        recovery.close_tree(tree)
        if child_fd is not None:
            try:
                signal.pidfd_send_signal(child_fd, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.close(child_fd)
        if root.poll() is None:
            root.kill()
        root.wait(timeout=3)
