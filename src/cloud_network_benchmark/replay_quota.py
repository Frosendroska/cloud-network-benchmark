"""Bounded, source-scoped Halle user-quota observations for WEEK replay."""
from __future__ import annotations

import hashlib
import json
import os
import pwd
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping


QUOTA_BINARY = Path("/usr/bin/quota")
QUOTA_TIMEOUT_SECONDS = 10
QUOTA_MAX_OUTPUT_BYTES = 16 * 1024
QUOTA_MAX_AGE_SECONDS = 900
MAX_BLOCKS_KIB = ((2 ** 63) - 1) // 1024
MAX_INODE_COUNT = (2 ** 63) - 1
_USER_LINE = re.compile(r"Disk quotas for user ([a-z_][a-z0-9_-]{0,31}) \(uid ([0-9]+)\):\Z")
_HEADERS = ["Filesystem", "blocks", "quota", "limit", "grace", "files", "quota", "limit", "grace"]
_ENTRY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


class ReplayQuotaError(ValueError):
    """Quota sampling or admission failed before replay allocation."""


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_replay_quota_scope(value: Any) -> dict[str, Any]:
    """Validate the exact, command-free WEEK replay identity and path scope."""
    expected = {"user", "uid", "device", "mountpoint", "filesystem_paths",
        "minimum_available_bytes"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("archive_sink.replay_quota_refresh must contain exactly the pinned user, uid, device, mountpoint, and filesystem_paths")
    user, uid, device, mountpoint, paths = (value["user"], value["uid"], value["device"],
        value["mountpoint"], value["filesystem_paths"])
    if (not isinstance(user, str) or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user)
            or type(uid) is not int or uid <= 0
            or type(value["minimum_available_bytes"]) is not int or value["minimum_available_bytes"] <= 0
            or not isinstance(device, str) or not device or any(ch in device for ch in "\r\n\x00")
            or not isinstance(mountpoint, str) or not Path(mountpoint).is_absolute()
            or ".." in Path(mountpoint).parts
            or not isinstance(paths, list) or not paths):
        raise ValueError("WEEK replay quota identity or filesystem path scope is invalid")
    normalized = []
    for raw in paths:
        if not isinstance(raw, str) or not Path(raw).is_absolute() or ".." in Path(raw).parts:
            raise ValueError("WEEK replay quota filesystem_paths must be absolute paths without traversal")
        if raw in normalized:
            raise ValueError("WEEK replay quota filesystem_paths must not contain duplicates")
        normalized.append(raw)
    if mountpoint not in normalized:
        raise ValueError("WEEK replay quota filesystem_paths must include the pinned mountpoint")
    return {"user": user, "uid": uid, "device": device, "mountpoint": mountpoint,
        "filesystem_paths": normalized, "minimum_available_bytes": value["minimum_available_bytes"]}


def replay_quota_scope_sha256(scope: Mapping[str, Any]) -> str:
    return _canonical_sha256(validate_replay_quota_scope(dict(scope)))


def parse_quota_output(output: str, *, user: str, uid: int, device: str,
                       mountpoint: str) -> dict[str, int]:
    """Parse the observed --verbose --no-wrap --raw-grace quota row only."""
    if not isinstance(output, str) or not output or len(output.encode("utf-8")) > QUOTA_MAX_OUTPUT_BYTES:
        raise ReplayQuotaError("quota output is empty or exceeds the fixed output bound")
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if len(lines) != 3:
        raise ReplayQuotaError("quota output must contain exactly one identity, header, and filesystem row")
    identity = _USER_LINE.fullmatch(lines[0])
    if identity is None or identity.group(1) != user or int(identity.group(2)) != uid:
        raise ReplayQuotaError("quota output user or uid differs from the pinned identity")
    if lines[1].split() != _HEADERS:
        raise ReplayQuotaError("quota output column header is missing or ambiguous")
    fields = lines[2].split()
    if len(fields) != 10 or fields[0] != device or fields[1] != mountpoint:
        raise ReplayQuotaError("quota output filesystem device or mountpoint is missing or ambiguous")
    try:
        values = [int(value, 10) for value in fields[2:]]
    except ValueError as exc:
        raise ReplayQuotaError("quota output contains malformed numeric fields") from exc
    if any(value < 0 for value in values):
        raise ReplayQuotaError("quota output contains negative numeric fields")
    blocks_used, blocks_soft, blocks_hard, blocks_grace, files_used, files_soft, files_hard, files_grace = values
    if any(value > MAX_BLOCKS_KIB for value in values[:3]) or any(value > MAX_INODE_COUNT for value in values[3:]):
        raise ReplayQuotaError("quota output numeric fields exceed the fixed integer bound")
    if blocks_soft == 0 or blocks_hard == 0:
        raise ReplayQuotaError("block quota limits are zero or unlimited; available user quota is ambiguous")
    if blocks_used >= blocks_soft or blocks_used >= blocks_hard or blocks_grace != 0:
        raise ReplayQuotaError("block quota is at or beyond its soft/hard limit or in grace")
    if (files_soft == 0) != (files_hard == 0):
        raise ReplayQuotaError("inode quota has only one finite limit and is ambiguous")
    if files_soft and (files_used >= files_soft or files_used >= files_hard or files_grace != 0):
        raise ReplayQuotaError("finite inode quota is at or beyond its limit or in grace")
    available_bytes = (min(blocks_soft, blocks_hard) - blocks_used) * 1024
    if available_bytes <= 0:
        raise ReplayQuotaError("quota output does not provide positive available block quota")
    return {"blocks_used_kib": blocks_used, "blocks_soft_limit_kib": blocks_soft,
        "blocks_hard_limit_kib": blocks_hard, "blocks_grace_raw": blocks_grace,
        "inodes_used": files_used, "inodes_soft_limit": files_soft,
        "inodes_hard_limit": files_hard, "inodes_grace_raw": files_grace,
        "available_bytes": available_bytes}


def _current_identity() -> tuple[int, int, str]:
    uid, euid = os.getuid(), os.geteuid()
    try:
        username = pwd.getpwuid(uid).pw_name
    except KeyError as exc:
        raise ReplayQuotaError("controller uid has no local username mapping") from exc
    return uid, euid, username


def _has_symlink_component(path: Path) -> bool:
    return any(part.is_symlink() for part in (path, *path.parents))


def _filesystem_device(path: Path) -> int:
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts or _has_symlink_component(path):
        raise ReplayQuotaError(f"quota-scoped path is unsafe: {path}")
    if not path.exists() or path.is_symlink() or not path.is_dir():
        raise ReplayQuotaError(f"quota-scoped path must be an existing real directory: {path}")
    return path.stat().st_dev


def validate_filesystem_devices(scope: Mapping[str, Any]) -> tuple[int, list[dict[str, Any]]]:
    """Prove every frozen output root and the pinned mount share one device."""
    mount_device = _filesystem_device(Path(scope["mountpoint"]))
    rows = []
    for raw_path in sorted(scope["filesystem_paths"]):
        device_number = _filesystem_device(Path(raw_path))
        if device_number != mount_device:
            raise ReplayQuotaError(f"quota-scoped output path is on a different filesystem device: {raw_path}")
        rows.append({"path": raw_path, "st_dev": device_number})
    return mount_device, rows


def _run_quota(argv: list[str], *, timeout: int, env: dict[str, str]):
    return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, timeout=timeout, check=False, env=env)


def _write_exclusive(path: Path, payload: bytes) -> None:
    if path.parent.is_symlink() or not path.parent.is_dir() or path.is_symlink():
        raise ReplayQuotaError("quota observation directory or receipt path is unsafe")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def sample_replay_quota(*, scope_value: Mapping[str, Any], entry_id: str,
        runtime_config_sha256: str, frozen_policy: Mapping[str, int],
        filesystem_paths: list[str], receipt_directory: Path,
        now: Callable[[], datetime] | None = None,
        identity_reader: Callable[[], tuple[int, int, str]] | None = None,
        command_runner: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Take one bounded live quota observation and retain an immutable receipt.

    Only the code-owned /usr/bin/quota command and fixed arguments are run.
    The function creates only the final exclusive receipt file in an existing
    journal directory; it never creates replay roots or contacts an archive.
    """
    scope = validate_replay_quota_scope(dict(scope_value))
    if not _ENTRY_ID.fullmatch(entry_id):
        raise ReplayQuotaError("WEEK replay entry id is invalid")
    if not re.fullmatch(r"[a-f0-9]{64}", runtime_config_sha256):
        raise ReplayQuotaError("frozen runtime source hash is invalid")
    if set(filesystem_paths) != set(scope["filesystem_paths"]):
        raise ReplayQuotaError("actual WEEK output roots differ from the pinned quota filesystem_paths")
    expected_policy = {"metadata_headroom_bytes", "projected_other_growth_bytes", "reserve_bytes"}
    if (set(frozen_policy) != expected_policy
            or any(type(frozen_policy[key]) is not int or frozen_policy[key] <= 0 for key in expected_policy)):
        raise ReplayQuotaError("frozen replay metadata, growth, and reserve bounds must be positive integers")

    uid, euid, username = (identity_reader or _current_identity)()
    if uid != scope["uid"] or euid != scope["uid"] or username != scope["user"]:
        raise ReplayQuotaError("controller uid/euid/user differs from the pinned quota identity")
    mount_device, root_devices = validate_filesystem_devices(scope)

    if QUOTA_BINARY.is_symlink() or not QUOTA_BINARY.is_file() or not os.access(QUOTA_BINARY, os.X_OK):
        raise ReplayQuotaError("trusted /usr/bin/quota executable is unavailable or unsafe")
    argv = [str(QUOTA_BINARY), "--user", "--verbose", "--no-wrap", "--raw-grace",
        "--show-mntpoint", f"--filesystem={scope['mountpoint']}", scope["user"]]
    started = (now or (lambda: datetime.now(timezone.utc)))().astimezone(timezone.utc)
    started_monotonic = time.monotonic()
    try:
        completed = (command_runner or _run_quota)(argv, timeout=QUOTA_TIMEOUT_SECONDS,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"})
    except subprocess.TimeoutExpired as exc:
        raise ReplayQuotaError("trusted quota command exceeded its 10-second deadline") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReplayQuotaError(f"trusted quota command failed: {type(exc).__name__}") from exc
    completed_at = (now or (lambda: datetime.now(timezone.utc)))().astimezone(timezone.utc)
    if completed.returncode != 0:
        raise ReplayQuotaError(f"trusted quota command exited with status {completed.returncode}")
    raw_stdout = completed.stdout
    if isinstance(raw_stdout, bytes):
        try:
            stdout = raw_stdout.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ReplayQuotaError("trusted quota command output is not UTF-8") from exc
    elif isinstance(raw_stdout, str):
        stdout = raw_stdout
    else:
        raise ReplayQuotaError("trusted quota command returned an unsupported output type")
    output_bytes = stdout.encode("utf-8")
    if len(output_bytes) > QUOTA_MAX_OUTPUT_BYTES:
        raise ReplayQuotaError("trusted quota command output exceeds the fixed output bound")
    parsed = parse_quota_output(stdout, user=scope["user"], uid=scope["uid"],
        device=scope["device"], mountpoint=scope["mountpoint"])
    receipt = {"schema_version": 1, "purpose": "week_archive_replay_user_quota",
        "entry_id": entry_id, "runtime_config_sha256": runtime_config_sha256,
        "scope_sha256": replay_quota_scope_sha256(scope), "user": scope["user"],
        "uid": uid, "euid": euid, "filesystem_device": scope["device"],
        "mountpoint": scope["mountpoint"], "mount_st_dev": mount_device,
        "filesystem_roots": root_devices, "measured_at": started.isoformat(),
        "completed_at": completed_at.isoformat(), "duration_seconds": max(0.0,
            time.monotonic() - started_monotonic), "command_argv": argv,
        "return_code": completed.returncode, "stdout": stdout,
        "stdout_sha256": hashlib.sha256(output_bytes).hexdigest(),
        "parsed_quota": parsed, "measured_free_bytes": parsed["available_bytes"],
        "minimum_available_bytes": scope["minimum_available_bytes"],
        **dict(frozen_policy)}
    encoded = (json.dumps(receipt, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    if not receipt_directory.is_absolute() or receipt_directory.is_symlink() or not receipt_directory.is_dir():
        raise ReplayQuotaError("campaign journal directory for quota receipts must already exist")
    receipt_path = receipt_directory / f"week-quota-{entry_id}-{uuid.uuid4().hex}.json"
    _write_exclusive(receipt_path, encoded)
    return {"path": str(receipt_path), "sha256": hashlib.sha256(encoded).hexdigest(),
        "payload": receipt}


def load_replay_quota_receipt(*, path: Path, expected_sha256: str,
        scope_value: Mapping[str, Any], entry_id: str, runtime_config_sha256: str,
        frozen_policy: Mapping[str, int], filesystem_paths: list[str],
        now: datetime | None = None) -> dict[str, Any]:
    """Read and revalidate the immutable journal-bound observation."""
    path = Path(path)
    if (path.is_symlink() or not path.is_file()
            or not re.fullmatch(r"[a-f0-9]{64}", str(expected_sha256))
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256):
        raise ReplayQuotaError("append-only replay quota receipt is missing or changed")
    payload = json.loads(path.read_text(encoding="utf-8"))
    scope = validate_replay_quota_scope(dict(scope_value))
    mount_device, expected_roots = validate_filesystem_devices(scope)
    rows = payload.get("filesystem_roots")
    if not isinstance(rows, list) or len(rows) != len(expected_roots):
        raise ReplayQuotaError("replay quota receipt filesystem-root inventory is malformed")
    if rows != expected_roots or payload.get("mount_st_dev") != mount_device:
        raise ReplayQuotaError("replay quota receipt filesystem device mapping changed")
    if (payload.get("schema_version") != 1
            or payload.get("purpose") != "week_archive_replay_user_quota"
            or payload.get("entry_id") != entry_id
            or payload.get("runtime_config_sha256") != runtime_config_sha256
            or payload.get("scope_sha256") != replay_quota_scope_sha256(scope)
            or payload.get("user") != scope["user"] or payload.get("uid") != scope["uid"]
            or payload.get("euid") != scope["uid"]
            or payload.get("filesystem_device") != scope["device"]
            or payload.get("mountpoint") != scope["mountpoint"]
            or set(filesystem_paths) != set(scope["filesystem_paths"])):
        raise ReplayQuotaError("replay quota receipt identity or frozen scope differs")
    if any(payload.get(key) != value for key, value in frozen_policy.items()):
        raise ReplayQuotaError("replay quota receipt changed frozen metadata, growth, or reserve policy")
    if payload.get("minimum_available_bytes") != scope["minimum_available_bytes"]:
        raise ReplayQuotaError("replay quota receipt changed the frozen WEEK storage minimum")
    measured = datetime.fromisoformat(str(payload.get("measured_at", "")).replace("Z", "+00:00"))
    current = now or datetime.now(timezone.utc)
    if measured.tzinfo is None or current.tzinfo is None:
        raise ReplayQuotaError("replay quota receipt timestamps must include UTC offsets")
    age = (current.astimezone(timezone.utc) - measured.astimezone(timezone.utc)).total_seconds()
    if age < 0 or age > QUOTA_MAX_AGE_SECONDS:
        raise ReplayQuotaError("replay quota receipt is future-dated or more than 15 minutes old")
    parsed = payload.get("parsed_quota")
    if (not isinstance(parsed, dict) or type(payload.get("measured_free_bytes")) is not int
            or payload.get("measured_free_bytes") != parsed.get("available_bytes")
            or type(payload.get("measured_free_bytes")) is not int or payload["measured_free_bytes"] <= 0):
        raise ReplayQuotaError("replay quota receipt has invalid available bytes")
    stdout = payload.get("stdout")
    if not isinstance(stdout, str):
        raise ReplayQuotaError("replay quota receipt does not retain textual command output")
    parsed_again = parse_quota_output(stdout, user=scope["user"], uid=scope["uid"],
        device=scope["device"], mountpoint=scope["mountpoint"])
    stdout_bytes = stdout.encode("utf-8")
    if (hashlib.sha256(stdout_bytes).hexdigest() != payload.get("stdout_sha256")
            or parsed_again != parsed or payload.get("command_argv") != [str(QUOTA_BINARY),
                "--user", "--verbose", "--no-wrap", "--raw-grace", "--show-mntpoint",
                f"--filesystem={scope['mountpoint']}", scope["user"]]
            or payload.get("return_code") != 0):
        raise ReplayQuotaError("replay quota receipt command, output digest, or parsed fields are invalid")
    return payload
