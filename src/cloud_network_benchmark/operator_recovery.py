"""Bounded Linux operator recovery for one registered failure attempt.

This is deliberately separate from campaign dispatch.  It can only freeze and
retire a process tree it captured with pidfds, then invoke the existing
``lifecycle cleanup`` command from the exact saved recovery record.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time
from typing import Any


FAILURE_PROVIDER = {"V-A-FAIL": "aws", "V-Z-FAIL": "azure", "V-G-FAIL": "gcp"}


def _private_file(path: Path) -> bytes:
    path = Path(path)
    if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError(f"expected a private regular file: {path}")
    return path.read_bytes()


def _bound_file(path: Path) -> bytes:
    path = Path(path)
    if path.is_symlink() or not path.is_file() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError(f"expected a regular non-symlink source file: {path}")
    return path.read_bytes()


def _sha(path: Path) -> str:
    return hashlib.sha256(_bound_file(path)).hexdigest()


def _immutable_stage_sha256(stage: dict[str, Any]) -> str:
    # Observer publication adds read-only bindings/receipt fields to this file
    # while a campaign is live. Those fields are progress, not run authority.
    snapshot = {key: value for key, value in stage.items() if key not in {
        "observer_launch_receipt_path", "observer_launch_receipt_sha256",
        "observer_bindings_sha256"}}
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _recovery_identity(recovery: Any) -> dict[str, Any]:
    return {"attempt_id": recovery.attempt_id, "controller_session_id": recovery.controller_session_id,
        "reservation_id": recovery.reservation_id, "ledger_path": recovery.ledger_path,
        "ledger_scope_id": recovery.ledger_scope_id,
        "providers": [{key: getattr(row, key) for key in (
            "provider", "deployment_id", "journal_reference", "expiry_registered",
            "creation_may_have_started", "cleanup_open", "cleanup_settings_reference")}
            for row in recovery.providers]}


@dataclass
class ProcIdentity:
    pid: int
    ppid: int
    uid: int
    boot_id: str
    start_ticks: int
    cwd: str
    cmdline: bytes
    fd: int
    initially_stopped: bool = False

    def same(self, other: "ProcIdentity") -> bool:
        return (self.pid, self.ppid, self.uid, self.boot_id, self.start_ticks,
                self.cwd, self.cmdline) == (other.pid, other.ppid, other.uid,
                other.boot_id, other.start_ticks, other.cwd, other.cmdline)


def _proc_identity(pid: int) -> ProcIdentity:
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise RuntimeError("Linux pidfd support is required")
    fd = os.pidfd_open(pid, 0)
    try:
        signal.pidfd_send_signal(fd, 0)
        proc = Path("/proc") / str(pid)
        raw = (proc / "stat").read_text()
        # comm is parenthesized and may itself contain spaces or ')'.
        fields = raw[raw.rfind(")") + 2:].split()
        ppid = int(fields[1])
        start = int(fields[19])  # proc stat field 22
        initially_stopped = fields[0] in {"T", "t"}
        uid = proc.stat().st_uid
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        cwd = os.readlink(proc / "cwd")
        cmdline = (proc / "cmdline").read_bytes()
        signal.pidfd_send_signal(fd, 0)
        return ProcIdentity(pid, ppid, uid, boot, start, cwd, cmdline, fd, initially_stopped)
    except BaseException:
        os.close(fd)
        raise


def capture_tree(root_pid: int, *, expected_uid: int | None = None) -> dict[int, ProcIdentity]:
    """Capture a finite ancestry tree; session and process-group IDs are ignored."""
    identities: dict[int, ProcIdentity] = {}
    try:
        root = _proc_identity(root_pid)
        if expected_uid is not None and root.uid != expected_uid:
            raise ValueError("root process UID mismatch")
        identities[root_pid] = root
        while True:
            parents = set(identities)
            new: list[int] = []
            for entry in Path("/proc").iterdir():
                if not entry.name.isdecimal():
                    continue
                pid = int(entry.name)
                if pid in identities:
                    continue
                try:
                    fields = (entry / "stat").read_text()
                    parent = int(fields[fields.rfind(")") + 2:].split()[1])
                except (OSError, ValueError, IndexError):
                    continue
                if parent in parents:
                    new.append(pid)
            if not new:
                break
            for pid in new:
                try:
                    item = _proc_identity(pid)
                except (OSError, ProcessLookupError, PermissionError):
                    continue
                if item.ppid in parents and expected_uid is not None and item.uid != expected_uid:
                    os.close(item.fd)
                    raise ValueError("owned child process UID mismatch")
                if item.ppid in parents:
                    identities[pid] = item
                else:
                    os.close(item.fd)
            # Repeated rounds discover grandchildren and detached sessions.
        # Re-read every process through the held pidfd identity before returning.
        for pid, saved in identities.items():
            current = _proc_identity(pid)
            try:
                if not saved.same(current):
                    raise ValueError("process identity changed during capture")
            finally:
                os.close(current.fd)
        return identities
    except BaseException:
        close_tree(identities)
        raise


def close_tree(tree: dict[int, ProcIdentity]) -> None:
    for item in tree.values():
        try:
            os.close(item.fd)
        except OSError:
            pass


def _is_alive(item: ProcIdentity) -> bool:
    try:
        raw = (Path("/proc") / str(item.pid) / "stat").read_text()
        if raw[raw.rfind(")") + 2:].split()[0] in {"Z", "X"}:
            return False
        signal.pidfd_send_signal(item.fd, 0)
        return True
    except ProcessLookupError:
        return False
    except FileNotFoundError:
        # A successful pidfd SIGKILL can reap `/proc/<pid>` before the next
        # state poll. The held pidfd still identifies the original process.
        return False
    except OSError as exc:
        if exc.errno in {2, 3}:  # ENOENT / ESRCH
            return False
        raise


def _depth_first(tree: dict[int, ProcIdentity]) -> list[ProcIdentity]:
    children: dict[int, list[int]] = {pid: [] for pid in tree}
    for pid, item in tree.items():
        if item.ppid in children:
            children[item.ppid].append(pid)
    result: list[ProcIdentity] = []
    def visit(pid: int) -> None:
        for child in children[pid]:
            visit(child)
        result.append(tree[pid])
    for pid, item in tree.items():
        if item.ppid not in tree:
            visit(pid)
    return result


def freeze_tree(tree: dict[int, ProcIdentity]) -> list[ProcIdentity]:
    """Freeze child-first; thaw exactly the already-stopped identities on error."""
    frozen: list[ProcIdentity] = []
    try:
        for item in _depth_first(tree):
            if not _is_alive(item):
                continue
            if item.initially_stopped:
                continue
            # Treat delivery as uncertain until the kernel readback proves
            # STOP. A pidfd send can take effect just before raising.
            frozen.append(item)
            signal.pidfd_send_signal(item.fd, signal.SIGSTOP)
            deadline = time.monotonic() + 3.0
            while _is_alive(item) and not _stopped(item) and time.monotonic() < deadline:
                time.sleep(0.01)
            if _is_alive(item) and not _stopped(item):
                raise RuntimeError("process did not reach stopped state")
        for item in frozen:
            if _is_alive(item) and not _stopped(item):
                raise RuntimeError("process did not reach stopped state")
        return frozen
    except BaseException:
        thaw_tree(frozen)
        raise


def freeze_fixed_point(root_pid: int, *, expected_uid: int | None = None,
                       rounds: int = 8,
                       expected_root_identity: ProcIdentity | None = None
                       ) -> tuple[dict[int, ProcIdentity], list[ProcIdentity]]:
    """Freeze all descendants, including new-session children, to a stable set."""
    held: dict[int, ProcIdentity] = {}
    frozen_pids: set[int] = set()
    try:
        for _ in range(rounds):
            fresh = capture_tree(root_pid, expected_uid=expected_uid)
            if expected_root_identity is not None:
                fresh_root = fresh.get(root_pid)
                if fresh_root is None or not expected_root_identity.same(fresh_root):
                    close_tree(fresh)
                    raise ValueError("root process identity changed before fixed-point freeze")
            duplicates: list[int] = []
            for pid, identity in fresh.items():
                previous = held.get(pid)
                if previous is not None:
                    if not previous.same(identity):
                        raise RuntimeError("PID identity changed during fixed-point freeze")
                    duplicates.append(pid)
                else:
                    held[pid] = identity
            frozen_pids.update(item.pid for item in freeze_tree(fresh))
            live_held = {pid for pid, item in held.items() if _is_alive(item)}
            for pid in duplicates:
                os.close(fresh[pid].fd)
            stable = {pid for pid in fresh if _is_alive(held[pid])}
            if stable == live_held and all(not _is_alive(x) or _stopped(x) for x in held.values()):
                # Root is now stopped, so a stable second inventory proves no
                # controller-owned process escaped the capture boundary.
                check = capture_tree(root_pid, expected_uid=expected_uid)
                try:
                    if set(check) == set(held) and all(held[p].same(check[p]) for p in held):
                        return held, [held[pid] for pid in sorted(frozen_pids) if pid in held]
                finally:
                    close_tree(check)
        raise RuntimeError("owned process tree did not reach a finite fixed point")
    except BaseException:
        thaw_tree([held[pid] for pid in sorted(frozen_pids) if pid in held])
        close_tree(held)
        raise


def _stopped(item: ProcIdentity) -> bool:
    try:
        raw = (Path("/proc") / str(item.pid) / "stat").read_text()
        return raw[raw.rfind(")") + 2:].split()[0] in {"T", "t"}
    except OSError:
        return not _is_alive(item)


def thaw_tree(frozen: list[ProcIdentity]) -> None:
    for item in reversed(frozen):
        if not item.initially_stopped and _is_alive(item):
            try:
                signal.pidfd_send_signal(item.fd, signal.SIGCONT)
            except ProcessLookupError:
                continue
            deadline = time.monotonic() + 3.0
            while _is_alive(item) and _stopped(item) and time.monotonic() < deadline:
                time.sleep(0.01)
            if _is_alive(item) and _stopped(item):
                raise RuntimeError("helper-stopped process did not resume during rollback")


def terminate_tree(tree: dict[int, ProcIdentity], *, timeout: float = 5.0) -> None:
    """Abruptly retire only captured owners after the server fault is recorded."""
    ordered = _depth_first(tree)
    for item in ordered:
        if _is_alive(item):
            try:
                signal.pidfd_send_signal(item.fd, signal.SIGKILL)
            except ProcessLookupError:
                continue
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and any(_is_alive(x) for x in ordered):
        time.sleep(0.02)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and any(_is_alive(x) for x in ordered):
        time.sleep(0.02)
    if any(_is_alive(x) for x in ordered):
        raise RuntimeError("captured controller owners remain alive")


def bind_failure_attempt(*, prepared_stage: Path, campaign_journal: Path,
                         recovery: Path, runtime_config: Path,
                         entry_id: str) -> dict[str, Any]:
    """Validate one registered failure entry and bind the durable checkpoint."""
    provider = FAILURE_PROVIDER.get(entry_id)
    if provider is None:
        raise ValueError("only registered V-A/Z/G-FAIL entries are supported")
    stage = json.loads(_bound_file(prepared_stage))
    journal = json.loads(_bound_file(campaign_journal))
    from .orchestration import SavedLifecycleRecovery
    rec = SavedLifecycleRecovery.validate_unique(json.loads(_private_file(recovery)))
    entries = [x for x in stage.get("entries", []) if x.get("entry_id") == entry_id]
    record = journal.get("entries", {}).get(entry_id)
    if len(entries) != 1 or not isinstance(record, dict):
        raise ValueError("registered failure entry is missing or ambiguous")
    entry = entries[0]
    stage_dir = Path(stage.get("state_root", "")) / str(stage.get("stage_id", ""))
    if (Path(prepared_stage).resolve() != (stage_dir / "prepared-stage.json").resolve()
            or Path(campaign_journal).resolve() != (stage_dir / "campaign-journal.json").resolve()
            or Path(recovery).resolve() != (Path(entry.get("private_root", "")) / "lifecycle-recovery.json").resolve()
            or Path(runtime_config).resolve() != Path(entry.get("runtime_config_path", "")).resolve()):
        raise ValueError("prepared stage, state, runtime, or recovery path differs from its registered entry")
    if not entry.get("requires_supervision") or entry.get("provider", provider) != provider:
        raise ValueError("entry is not the matching supervised provider failure attempt")
    if record.get("state") not in {"starting", "running", "failed", "awaiting_operator"}:
        raise ValueError("failure entry is not an active/recoverable original attempt")
    other_live = [key for key, value in journal.get("entries", {}).items()
                  if key != entry_id and isinstance(value, dict)
                  and value.get("state") in {"starting", "running"}]
    if other_live:
        raise ValueError("another campaign entry is live; failure recovery requires one provider at a time")
    if len(rec.providers) != 1 or rec.providers[0].provider != provider:
        raise ValueError("recovery must contain exactly the selected provider")
    provider_record = rec.providers[0]
    if not (provider_record.expiry_registered and provider_record.cleanup_open
            and provider_record.cleanup_settings_reference):
        raise ValueError("original registered expiry and cleanup liability are required")
    run_ids = entry.get("run_ids", {})
    if set(run_ids) != {provider}:
        raise ValueError("failure entry must identify exactly one selected child provider")
    from .lifecycle_cleanup import load_cleanup_settings
    saved = load_cleanup_settings(Path(provider_record.cleanup_settings_reference))
    if saved.deployment.deployment_id != provider_record.deployment_id:
        raise ValueError("saved cleanup settings differ from the recovery deployment")
    parent_manifest = json.loads(_bound_file(Path(entry["parent_manifest_path"])))
    _validate_campaign_identity(entry=entry, recovery=rec,
        provider_record=provider_record, parent_manifest=parent_manifest,
        saved_deployment=saved.deployment)
    source_files = [Path(entry["config_path"]), Path(entry["source_config_path"]),
                    Path(entry["runtime_config_path"]), Path(entry["parent_manifest_path"])]
    ledger_path = Path(rec.ledger_path or "")
    provider_journal = Path(provider_record.journal_reference)
    cleanup_settings = Path(provider_record.cleanup_settings_reference)
    files = [*source_files, provider_journal, cleanup_settings]
    hashes = {}
    for value in files:
        path = Path(value)
        if path.is_symlink() or not path.is_file():
            raise ValueError("source-bound implementation file must be regular and non-symlink")
        hashes[str(path.resolve())] = hashlib.sha256(path.read_bytes()).hexdigest()
    repository_root = Path(stage["repository_root"]).resolve(strict=True)
    return {
        "schema_version": 1, "entry_id": entry_id, "provider": provider,
        "stage_id": stage.get("stage_id"), "attempt_id": rec.attempt_id,
        "controller_session_id": rec.controller_session_id,
        "prepared_stage": str(Path(prepared_stage).resolve()),
        "campaign_journal": str(Path(campaign_journal).resolve()),
        "recovery": str(Path(recovery).resolve()),
        "runtime_config": str(Path(runtime_config).resolve()),
        "repository_root": str(repository_root),
        "implementation_git_commit": stage.get("implementation_git_commit"),
        "prepared_stage_immutable_sha256": _immutable_stage_sha256(stage),
        "recovery_identity": _recovery_identity(rec),
        "server_fault_unit": f"cnb-{provider_record.deployment_id}-iperf3.service",
        "provider_journal": str(provider_journal.resolve()),
        "cleanup_settings": str(cleanup_settings.resolve()),
        "ledger_path": str(ledger_path.resolve()),
        "source_files": [str(Path(p).resolve()) for p in source_files],
        "bindings": hashes,
        "original_expiry_request": original_expiry_window(recovery),
    }


def _validate_campaign_identity(*, entry: dict[str, Any],
                                recovery: Any, provider_record: Any,
                                parent_manifest: dict[str, Any],
                                saved_deployment: Any) -> None:
    """Bind recovery to the entry's unique parent campaign and registered child."""
    parent_campaign_id = parent_manifest.get("campaign_id")
    run_ids = entry.get("run_ids", {})
    provider = provider_record.provider
    if (not isinstance(parent_campaign_id, str) or not parent_campaign_id
            or recovery.attempt_id != parent_campaign_id
            or saved_deployment.campaign_id != parent_campaign_id):
        raise ValueError("recovery and saved deployment must match the entry parent manifest campaign")
    deployment_provider = getattr(saved_deployment.provider, "value", saved_deployment.provider)
    if (set(run_ids) != {provider} or run_ids.get(provider) != saved_deployment.run_id
            or deployment_provider != provider
            or saved_deployment.deployment_id != provider_record.deployment_id):
        raise ValueError("saved deployment must match the selected entry child run and provider")


def revalidate_before_freeze(binding: dict[str, Any]) -> None:
    """Recheck immutable run authority after freeze, while allowing observer progress."""
    stage_path = Path(binding["prepared_stage"])
    stage = json.loads(_bound_file(stage_path))
    if _immutable_stage_sha256(stage) != binding["prepared_stage_immutable_sha256"]:
        raise ValueError("prepared stage immutable authority changed before freeze")
    if (stage.get("stage_id") != binding["stage_id"]
            or stage.get("controller_session_id") != binding["controller_session_id"]):
        raise ValueError("prepared stage identity changed before freeze")
    entries = [row for row in stage.get("entries", []) if row.get("entry_id") == binding["entry_id"]]
    if len(entries) != 1:
        raise ValueError("registered entry changed before freeze")
    journal_path = Path(binding["campaign_journal"])
    journal = json.loads(_bound_file(journal_path))
    records = journal.get("entries", {})
    record = records.get(binding["entry_id"])
    if not isinstance(record, dict) or record.get("state") not in {
            "starting", "running", "failed", "awaiting_operator"}:
        raise ValueError("registered failure entry is no longer recoverable before freeze")
    if any(key != binding["entry_id"] and isinstance(row, dict)
           and row.get("state") in {"starting", "running"} for key, row in records.items()):
        raise ValueError("another campaign entry became live before freeze")
    rec = json.loads(_private_file(Path(binding["recovery"])))
    from .orchestration import SavedLifecycleRecovery
    parsed = SavedLifecycleRecovery.validate_unique(rec)
    if _recovery_identity(parsed) != binding["recovery_identity"]:
        raise ValueError("original recovery liabilities changed before freeze")
    for raw_path, expected_hash in binding["bindings"].items():
        if hashlib.sha256(_bound_file(Path(raw_path))).hexdigest() != expected_hash:
            raise ValueError("immutable source-bound input changed before freeze")
    if original_expiry_window(Path(binding["recovery"])) != binding["original_expiry_request"]:
        raise ValueError("original provider expiry or cap changed before freeze")


def campaign_supervisor_pid(binding: dict[str, Any]) -> ProcIdentity:
    """Resolve and retain the original campaign supervisor's verified pidfd."""
    stage = json.loads(_bound_file(Path(binding["prepared_stage"])))
    pid_path = Path(stage["state_root"]) / "campaign-supervisor.pid"
    raw = _bound_file(pid_path).decode("ascii").strip()
    if not raw.isdecimal() or str(int(raw)) != raw:
        raise ValueError("campaign supervisor PID file is malformed")
    pid = int(raw)
    identity = _proc_identity(pid)
    valid = False
    try:
        expected_root = str(Path(binding["repository_root"]).resolve(strict=True))
        args = identity.cmdline.decode("utf-8", errors="replace").split("\0")
        if (identity.uid != os.getuid() or identity.cwd != expected_root
                or "cloud_network_benchmark.campaign_supervisor" not in args
                or "--state-root" not in args
                or str(Path(stage["state_root"]).resolve(strict=True)) not in args):
            raise ValueError("PID file does not identify the source-bound campaign supervisor process")
        valid = True
        return identity
    finally:
        if not valid:
            os.close(identity.fd)


def write_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    """Create-only durable private checkpoint; caller must hold helper's lock."""
    path = Path(path)
    if path.is_symlink() or path.exists():
        raise FileExistsError("operator recovery checkpoint already exists")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    raw = json.dumps(payload, sort_keys=True, indent=2).encode() + b"\n"
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def validate_expiry_proof(proof: dict[str, Any], *, binding: dict[str, Any],
                          target_selector: str, registration_id: str,
                          expiry_at: datetime, now: datetime) -> bool:
    """Accept only captured provider readback of the original target/action."""
    try:
        observed = datetime.fromisoformat(str(proof.get("observed_at", "")).replace("Z", "+00:00"))
    except ValueError:
        return False
    try:
        readback_path = Path(str(proof.get("readback_path", "")))
        readback = _private_file(readback_path)
    except (OSError, ValueError):
        return False
    return bool(
        proof.get("schema_version") == 1 and proof.get("binding_sha256") == binding.get("sha256")
        and proof.get("target_selector") == target_selector
        and proof.get("registration_id") == registration_id
        and proof.get("provider") == binding.get("provider")
        and proof.get("observed_state") == binding.get("expected_state")
        and proof.get("read_only") is True and proof.get("captured_by")
        and proof.get("read_only_command") and proof.get("readback_sha256") == hashlib.sha256(readback).hexdigest()
        and observed.tzinfo is not None and expiry_at <= observed <= now
    )


def original_expiry_window(recovery: Path) -> dict[str, Any]:
    """Read deadlines and target from the exact typed saved cleanup source."""
    from .lifecycle_cleanup import _expected_journal, load_cleanup_settings, _validate_request
    from .contracts.admission import ExpiryEvidence
    from .orchestration import SavedLifecycleRecovery
    rec = SavedLifecycleRecovery.validate_unique(json.loads(_private_file(recovery)))
    if len(rec.providers) != 1:
        raise ValueError("expiry proof must bind exactly one provider")
    row = rec.providers[0]
    if not row.cleanup_settings_reference:
        raise ValueError("recovery lacks its saved cleanup settings reference")
    saved = load_cleanup_settings(Path(row.cleanup_settings_reference))
    _validate_request(saved)
    journal_path = Path(row.journal_reference)
    if (_expected_journal(saved).resolve() != journal_path.resolve()
            or journal_path.is_symlink() or not journal_path.is_file()
            or stat.S_IMODE(journal_path.stat().st_mode) & 0o077):
        raise ValueError("original provider journal is missing, unsafe, or mismatched")
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    reservation = saved.reservation
    cleanup_seconds = saved.deployment.cleanup_timeout_seconds
    provider = str(saved.provider.value)
    request = saved.expiry_request
    if (journal.get("run_id") != saved.deployment.run_id
            or journal.get("deployment_id") != saved.deployment.deployment_id
            or reservation.expires_at.value is None or not cleanup_seconds):
        raise ValueError("typed original registration, cap, or cleanup allowance is unavailable")
    registration_id: str
    if provider == "aws":
        evidence = ExpiryEvidence.model_validate(journal.get("expiry_evidence"))
        metadata = evidence.provider_metadata.value or {}
        expiry_at = evidence.expires_at.value
        registration_id = evidence.registration_id.value
        target_selector = evidence.target_selector
        expected_state = "stopped"
        if evidence.state != "registered" or metadata.get("stop_is_not_deletion") is not True:
            raise ValueError("AWS native journal does not contain its registered absolute STOP")
    elif provider == "gcp":
        evidence = ExpiryEvidence.model_validate(journal.get("expiry"))
        metadata = evidence.provider_metadata.value or {}
        expiry_at = evidence.expires_at.value
        registration_id = evidence.registration_id.value
        target_selector = evidence.target_selector
        expected_state = "terminated"
        if evidence.state != "registered" or metadata.get("native_absolute_stop") is not True:
            raise ValueError("GCP native journal does not contain its registered absolute STOP")
    elif provider == "azure":
        internal_expiry = journal.get("expiry")
        metadata = internal_expiry.get("metadata") if isinstance(internal_expiry, dict) else None
        if (not isinstance(metadata, dict) or internal_expiry.get("state") != "registered"
                or metadata.get("mechanism") != "azure_automation_once_deallocate"):
            raise ValueError("Azure native journal lacks its registered deallocation state and metadata")
        expiry_at = datetime.fromisoformat(str(metadata.get("trigger", "")).replace("Z", "+00:00"))
        if not isinstance(expiry_at, datetime) or expiry_at.tzinfo is None:
            raise ValueError("Azure native journal trigger is malformed")
        account_id = str(metadata.get("automation_account_id", "")).rstrip("/")
        schedule_id = str(metadata.get("job_schedule_id", ""))
        api_version = str(metadata.get("api_version", ""))
        registration_id = (f"{account_id}/jobSchedules/{schedule_id}?api-version={api_version}"
                           if account_id and schedule_id and api_version else "")
        target_ids = metadata.get("target_ids")
        target_selector = ";".join(target_ids) if isinstance(target_ids, list) else ""
        expected_state = "deallocated"
        from .azure_provider import _azure_schedule_trigger
        if _azure_schedule_trigger(request.expires_at.value) != expiry_at:
            raise ValueError("Azure registered minute-floor trigger differs from the original request")
    else:
        raise ValueError("unsupported provider for original expiry proof")
    if (expiry_at is None or not registration_id or not target_selector
            or request.action != ("deallocate" if provider == "azure" else "stop")
            or target_selector != request.target_selector):
        raise ValueError("native registered expiry differs from the original action or target")
    latest_safe = reservation.expires_at.value - timedelta(seconds=cleanup_seconds)
    binding = hashlib.sha256(json.dumps({
        "provider": provider, "run_id": saved.deployment.run_id,
        "deployment_id": saved.deployment.deployment_id,
        "registration_id": registration_id,
        "target_selector": target_selector,
        "action": request.action,
        "trigger": expiry_at.isoformat(),
        "cap": reservation.expires_at.value.isoformat(),
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"provider": provider, "registration_id": registration_id,
        "target_selector": target_selector, "action": request.action,
        "expected_state": expected_state, "expiry_at": expiry_at.isoformat(),
        "latest_safe_cleanup_start": latest_safe.isoformat(),
        "cleanup_seconds": cleanup_seconds, "cap_at": reservation.expires_at.value.isoformat(),
        "binding_sha256": binding}


def cleanup_decision(window: dict[str, Any], *, now: datetime,
                     proof: dict[str, Any] | None) -> tuple[bool, str]:
    """Never wait past original cleanup start; missing proof becomes inconclusive."""
    expiry_at = datetime.fromisoformat(window["expiry_at"])
    latest = datetime.fromisoformat(window["latest_safe_cleanup_start"])
    if proof is not None and now <= latest and validate_expiry_proof(proof,
            binding={"sha256": window["binding_sha256"], "expected_state": window["expected_state"],
                     "provider": window["provider"]},
            target_selector=window["target_selector"], registration_id=window["registration_id"],
            expiry_at=expiry_at, now=now):
        return True, "provider_expiry_proved"
    if now >= latest:
        return True, "expiry_unproved_cleanup_priority"
    return False, "wait_for_original_expiry_observation_window"


def cleanup_command(recovery: Path, session: str) -> list[str]:
    """The only subprocess command this module constructs."""
    return [sys.executable, "-m", "cloud_network_benchmark", "lifecycle", "cleanup",
            "--recovery", str(Path(recovery).resolve()), "--controller-session", session,
            "--format", "json"]


class RecoverySupervisor:
    """Single-process durable supervisor; keep this object in the armed helper.

    Pidfds are intentionally never serialized. The helper must be started as a
    detached service before interrupting the campaign controller and must stay
    alive through cleanup.
    """

    def __init__(self, *, checkpoint: Path, binding: dict[str, Any],
                 tree: dict[int, ProcIdentity]):
        self.checkpoint = Path(checkpoint)
        self.binding = binding
        self.tree = tree
        self.lock_path = self.checkpoint.with_suffix(self.checkpoint.suffix + ".lock")
        if self.lock_path.is_symlink():
            raise ValueError("operator lock cannot be a symlink")
        self.lock_fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.fchmod(self.lock_fd, 0o600)
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            write_checkpoint(self.checkpoint, {"phase": "armed", "binding": binding,
                "owner_pids": sorted(tree), "armed_at": datetime.now(timezone.utc).isoformat()})
        except BaseException:
            os.close(self.lock_fd)
            raise

    def _update(self, **values: Any) -> None:
        current = json.loads(_private_file(self.checkpoint))
        current.update(values)
        temp = self.checkpoint.with_name(self.checkpoint.name + ".tmp")
        if temp.exists() or temp.is_symlink():
            raise FileExistsError("operator checkpoint temporary path is occupied")
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(json.dumps(current, sort_keys=True, indent=2).encode() + b"\n")
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, self.checkpoint)
        dfd = os.open(self.checkpoint.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)

    def fault_captured(self, proof: dict[str, Any]) -> None:
        if json.loads(_private_file(self.checkpoint)).get("phase") != "controllers_frozen":
            raise RuntimeError("server fault can only be captured once after finite controller freeze")
        if not self._fault_validation_compat(proof):
            raise ValueError("server-fault readback is missing or does not match the registered attempt")
        self._update(phase="fault_captured", server_fault=proof)
        terminate_tree(self.tree)
        if any(_is_alive(item) for item in self.tree.values()):
            raise RuntimeError("old controller owners are not proven dead")
        self._update(phase="old_owners_dead", owners_dead_at=datetime.now(timezone.utc).isoformat())

    def retire_after_unproved_fault(self, reason: str) -> None:
        """Close a missing/invalid fault record at the original cleanup cutoff."""
        if json.loads(_private_file(self.checkpoint)).get("phase") != "controllers_frozen":
            raise RuntimeError("unproved-fault fallback requires the frozen original controller")
        self._update(phase="fault_unproved", server_fault_status="inconclusive", reason=reason)
        terminate_tree(self.tree)
        if any(_is_alive(item) for item in self.tree.values()):
            raise RuntimeError("old controller owners are not proven dead")
        self._update(phase="old_owners_dead", owners_dead_at=datetime.now(timezone.utc).isoformat())

    def _fault_validation_compat(self, proof: dict[str, Any]) -> bool:
        if (proof.get("entry_id") != self.binding.get("entry_id")
                or proof.get("provider") != self.binding.get("provider")
                or proof.get("target_unit") != self.binding.get("server_fault_unit")
                or proof.get("command") != f"sudo systemctl stop {self.binding.get('server_fault_unit')}"
                or proof.get("readback_command") != f"systemctl show --property=ActiveState --property=SubState --value {self.binding.get('server_fault_unit')}"
                or proof.get("observed_state") != "inactive" or proof.get("readback_stdout") != "inactive\ndead"
                or proof.get("server_was_active_tcp") is not True
                or proof.get("read_only") is not True or not proof.get("captured_at")):
            return False
        for path_key, hash_key in (("guest_receipt_path", "guest_receipt_sha256"),
                                   ("readback_path", "readback_sha256")):
            try:
                receipt = _private_file(Path(str(proof[path_key])))
            except (KeyError, OSError, ValueError) as exc:
                return False
            if hashlib.sha256(receipt).hexdigest() != proof.get(hash_key):
                return False
        return True

    def wait_for_expiry_and_cleanup(self, *, recovery: Path, proof_path: Path,
                                    poll_seconds: float = 1.0) -> dict[str, Any]:
        if json.loads(_private_file(self.checkpoint)).get("phase") != "old_owners_dead":
            raise RuntimeError("cleanup is blocked until server fault and old-owner death are recorded")
        if poll_seconds <= 0 or poll_seconds > 5:
            raise ValueError("poll interval must be between 0 and 5 seconds")
        if Path(recovery).resolve() != Path(self.binding["recovery"]).resolve():
            raise ValueError("cleanup recovery path differs from the armed source binding")
        window = self.binding["original_expiry_request"]
        drifted_sources = []
        for raw_path, expected_hash in self.binding["bindings"].items():
            try:
                unchanged = hashlib.sha256(_bound_file(Path(raw_path))).hexdigest() == expected_hash
            except (OSError, ValueError):
                unchanged = False
            if not unchanged:
                drifted_sources.append(raw_path)
        self._update(source_integrity_status="unchanged" if not drifted_sources else "changed_or_unreadable",
            source_integrity_paths=drifted_sources)
        self._update(expiry_window=window)
        while True:
            now = datetime.now(timezone.utc)
            proof = None
            try:
                proof = json.loads(_private_file(proof_path))
            except (OSError, ValueError, json.JSONDecodeError):
                pass
            ready, reason = cleanup_decision(window, now=now, proof=proof)
            if ready:
                timing = "late" if now > datetime.fromisoformat(window["latest_safe_cleanup_start"]) else "within_deadline"
                self._update(expiry_status=reason, expiry_proof=proof if reason == "provider_expiry_proved" else None,
                    cleanup_timing_status=timing, original_cap_at=window["cap_at"],
                    cleanup_started_at=now.isoformat())
                break
            time.sleep(min(poll_seconds, max(0.0,
                (datetime.fromisoformat(window["latest_safe_cleanup_start"]) - now).total_seconds())))
        claim = self.checkpoint.with_suffix(self.checkpoint.suffix + ".cleanup-claimed")
        fd = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(datetime.now(timezone.utc).isoformat() + "\n")
            stream.flush(); os.fsync(stream.fileno())
        if any(_is_alive(item) for item in self.tree.values()):
            raise RuntimeError("old controller owner reappeared before cleanup")
        self._update(phase="cleanup_claimed", cleanup_claim=str(claim))
        output = self.checkpoint.with_suffix(self.checkpoint.suffix + ".cleanup.json")
        command = cleanup_command(recovery, self.binding["controller_session_id"])
        if output.exists() or output.is_symlink():
            raise FileExistsError("cleanup report path is already occupied")
        with output.open("xb") as stream:
            os.chmod(output, 0o600)
            completed = subprocess.run(command, cwd=self.binding.get("repository_root"),
                stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                check=False, timeout=window["cleanup_seconds"])
            stream.flush(); os.fsync(stream.fileno())
        try:
            report = json.loads(_private_file(output))
        except (OSError, ValueError, json.JSONDecodeError):
            report = None
        provider_report = (report.get("providers", {}).get(window["provider"])
            if isinstance(report, dict) and isinstance(report.get("providers"), dict) else None)
        cleanup_complete = bool(completed.returncode == 0 and isinstance(report, dict)
            and report.get("execution") == "not_resumed" and report.get("status") == "complete"
            and isinstance(provider_report, dict) and provider_report.get("cleanup_open") is False
            and provider_report.get("absence_proved") is True
            and provider_report.get("billable_resources_present") is False)
        self._update(phase="cleanup_finished", cleanup_exit_code=completed.returncode,
            cleanup_report_path=str(output), cleanup_report_status=report.get("status") if isinstance(report, dict) else "unreadable",
            cleanup_complete=cleanup_complete, finished_at=datetime.now(timezone.utc).isoformat())
        return {"exit_code": completed.returncode, "report": str(output),
            "expiry_status": reason, "cleanup_complete": cleanup_complete}

    def close(self) -> None:
        close_tree(self.tree)
        os.close(self.lock_fd)


def supervise_failure_entry(prepared_stage: Path, entry_id: str) -> dict[str, Any]:
    """Standalone helper entry point; all attempt paths and commands are derived."""
    stage = json.loads(_bound_file(prepared_stage))
    stage_dir = Path(stage["state_root"]) / stage["stage_id"]
    entry_rows = [row for row in stage.get("entries", []) if row.get("entry_id") == entry_id]
    if len(entry_rows) != 1:
        raise ValueError("registered failure entry is missing or ambiguous")
    entry = entry_rows[0]
    binding = bind_failure_attempt(prepared_stage=prepared_stage,
        campaign_journal=stage_dir / "campaign-journal.json",
        recovery=Path(entry["private_root"]) / "lifecycle-recovery.json",
        runtime_config=Path(entry["runtime_config_path"]), entry_id=entry_id)
    verified_supervisor = campaign_supervisor_pid(binding)
    pid = verified_supervisor.pid
    attempt_dir = stage_dir / "operator-recovery" / entry_id
    try:
        attempt_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        os.chmod(attempt_dir, 0o700)
        checkpoint = attempt_dir / "checkpoint.json"
        fault_path = attempt_dir / "server-fault-proof.json"
        expiry_path = attempt_dir / "expiry-proof.json"
        initial_tree = capture_tree(pid, expected_uid=os.getuid())
        captured_supervisor = initial_tree.get(pid)
        if captured_supervisor is None or not verified_supervisor.same(captured_supervisor):
            close_tree(initial_tree)
            raise ValueError("campaign supervisor PID changed after PID-file verification")
    finally:
        os.close(verified_supervisor.fd)
    supervisor = RecoverySupervisor(checkpoint=checkpoint, binding=binding, tree=initial_tree)
    tree: dict[int, ProcIdentity] = initial_tree
    frozen: list[ProcIdentity] = []
    fault_recorded = False
    try:
        new_tree, frozen = freeze_fixed_point(pid, expected_uid=os.getuid(),
            expected_root_identity=tree[pid])
        for old_pid, old_identity in tree.items():
            newer = new_tree.get(old_pid)
            if newer is None or not old_identity.same(newer):
                raise RuntimeError("controller tree changed between durable arm and freeze")
        close_tree(tree)
        tree = new_tree
        supervisor.tree = tree
        revalidate_before_freeze(binding)
        supervisor._update(phase="controllers_frozen", owner_identities=[{
            "pid": item.pid, "uid": item.uid, "boot_id": item.boot_id,
            "start_ticks": item.start_ticks, "ppid": item.ppid,
            "cwd": item.cwd, "cmdline_sha256": hashlib.sha256(item.cmdline).hexdigest(),
        } for item in _depth_first(tree)])
        cutoff = datetime.fromisoformat(binding["original_expiry_request"]["latest_safe_cleanup_start"])
        invalid_fault = False
        while datetime.now(timezone.utc) < cutoff:
            if fault_path.is_file() and not fault_path.is_symlink():
                try:
                    fault_proof = json.loads(_private_file(fault_path))
                    if supervisor._fault_validation_compat(fault_proof):
                        supervisor.fault_captured(fault_proof)
                        fault_recorded = True
                        break
                    invalid_fault = True
                except (OSError, ValueError, json.JSONDecodeError):
                    invalid_fault = True
            time.sleep(min(0.25, max(0.0, (cutoff - datetime.now(timezone.utc)).total_seconds())))
        if not fault_recorded:
            supervisor.retire_after_unproved_fault(
                "server_fault_proof_invalid" if invalid_fault else "server_fault_proof_missing_at_original_cleanup_cutoff")
            fault_recorded = True
        return supervisor.wait_for_expiry_and_cleanup(recovery=Path(binding["recovery"]),
            proof_path=expiry_path)
    except BaseException:
        phase = json.loads(_private_file(checkpoint)).get("phase")
        fault_recorded = fault_recorded or phase in {"fault_captured", "old_owners_dead", "cleanup_claimed", "cleanup_finished"}
        if not fault_recorded:
            thaw_tree(frozen)
            supervisor._update(phase="arming_or_fault_capture_failed",
                failed_at=datetime.now(timezone.utc).isoformat())
        raise
    finally:
        supervisor.close()


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="cnb-operator-recovery")
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("supervise", help="arm one registered V-A/Z/G-FAIL cleanup recovery")
    run.add_argument("--prepared-stage", required=True, type=Path)
    run.add_argument("--entry-id", required=True, choices=tuple(FAILURE_PROVIDER))
    args = parser.parse_args(argv)
    try:
        result = supervise_failure_entry(args.prepared_stage, args.entry_id)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["exit_code"] == 0 else 1
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

