"""Atomic, hash-pinned publication primitives for actual guest observer evidence.

This module deliberately accepts captured process and file records. It never
infers a process, guest path, VM, or connection from a name or timestamp.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


_ROLES = ("vm_a", "vm_b")
_CONNECTION_KEYS = ("host", "user", "port", "known_hosts", "identity_file")
_PROCESS_KINDS = {"vm_a": {"producer", "diagnostics"}, "vm_b": {"receiver", "diagnostics"}}


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                        ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def _atomic_bytes(path: Path, raw: bytes, *, replace: bool) -> None:
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("observer publication path must be absolute")
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError("observer publication path cannot traverse symlinks")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or (path.exists() and not replace):
        raise FileExistsError("observer launch receipt is immutable")
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if not replace and path.exists():
            raise FileExistsError("observer launch receipt is immutable")
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _participant_key(entry_id: str, provider: str, run_id: str) -> str:
    return json.dumps([entry_id, provider, run_id], separators=(",", ":"))


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"{name} must be a nonempty visible string")
    return value


def _process(record: Mapping[str, Any], kind: str) -> dict[str, Any]:
    if not isinstance(record, Mapping) or record.get("kind") != kind:
        raise ValueError(f"actual {kind} process receipt is missing")
    pid, ticks = record.get("pid"), record.get("start_ticks")
    if type(pid) is not int or pid <= 1 or type(ticks) is not int or ticks <= 0:
        raise ValueError(f"actual {kind} PID/start ticks are invalid")
    boot = record.get("boot_id")
    if not isinstance(boot, str) or not re.fullmatch(r"[0-9a-fA-F-]{36}", boot):
        raise ValueError(f"actual {kind} boot ID is invalid")
    return {"kind": kind, "pid": pid, "boot_id": boot, "start_ticks": ticks}


def _file(record: Mapping[str, Any], kind: str) -> dict[str, Any]:
    if not isinstance(record, Mapping) or record.get("kind") != kind:
        raise ValueError(f"actual {kind} file receipt is missing")
    path = record.get("path")
    parsed = PurePosixPath(path) if isinstance(path, str) else None
    if (parsed is None or parsed.is_absolute() or parsed.as_posix() != path
            or not path or any(part in {"", ".", ".."} for part in parsed.parts)):
        raise ValueError(f"actual {kind} path must be workspace relative")
    result = {"path": path, "kind": kind}
    if kind == "sockperf_chunk":
        state = record.get("state")
        if state not in {"current", "completed"}:
            raise ValueError("Sockperf chunk must be an observed current/completed chunk")
        result["state"] = state
    return result


def write_launch_receipt(path: Path, *, stage_id: str,
                         participants: Mapping[tuple[str, str, str], Mapping[str, Any]]) -> str:
    """Write one immutable receipt from owned deployment and ConnectionOutput records.

    A participant value must provide per-role ``vm_id`` (the provider resource
    ID), ``connection`` (the exact resolved SSH tuple), and ``workspace``.
    """
    _required_text(stage_id, "stage_id")
    if not participants:
        raise ValueError("launch receipt requires at least one active participant")
    owned: dict[str, Any] = {}
    for identity, participant in participants.items():
        if not isinstance(identity, tuple) or len(identity) != 3:
            raise ValueError("participant key must be (entry_id, provider, run_id)")
        key = _participant_key(*(_required_text(x, "participant identity") for x in identity))
        if key in owned:
            raise ValueError("duplicate active participant")
        role_data = {}
        if not isinstance(participant, Mapping) or set(participant) != set(_ROLES):
            raise ValueError("launch participant must map exactly vm_a and vm_b")
        for role in _ROLES:
            value = participant[role]
            vm_id = _required_text(value.get("vm_id"), "provider-owned VM ID")
            connection = value.get("connection")
            if not isinstance(connection, Mapping) or set(connection) != set(_CONNECTION_KEYS):
                raise ValueError("launch connection must match the exact SSH tuple")
            host, user = _required_text(connection["host"], "SSH host"), _required_text(connection["user"], "SSH user")
            port = connection["port"]
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError("SSH port is invalid")
            known_hosts = _required_text(connection["known_hosts"], "known-hosts path")
            identity_file = _required_text(connection["identity_file"], "identity path")
            if not Path(known_hosts).is_absolute() or not Path(identity_file).is_absolute():
                raise ValueError("SSH identity and known-hosts paths must be absolute")
            role_data[role] = {"vm_id": vm_id, "connection": {"host": host, "user": user,
                "port": port, "known_hosts": known_hosts, "identity_file": identity_file}}
        owned[key] = role_data
    raw = _json_bytes({"stage_id": stage_id, "participants": owned})
    _atomic_bytes(Path(path), raw, replace=False)
    return hashlib.sha256(raw).hexdigest()


def build_bindings(*, stage_id: str, campaign_id: str,
                   participants: Sequence[Mapping[str, Any]],
                   launch_receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Build a complete snapshot only when all actual runtime receipts agree."""
    _required_text(stage_id, "stage_id")
    _required_text(campaign_id, "campaign_id")
    if launch_receipt.get("stage_id") != stage_id:
        raise ValueError("launch receipt belongs to a different stage")
    owned = launch_receipt.get("participants")
    if not isinstance(owned, Mapping):
        raise ValueError("launch receipt participant set is invalid")
    entries = []
    seen = set()
    for participant in participants:
        entry_id = _required_text(participant.get("entry_id"), "entry_id")
        provider = _required_text(participant.get("provider"), "provider")
        run_id = _required_text(participant.get("run_id"), "run_id")
        deployment_id = _required_text(participant.get("deployment_id"), "deployment_id")
        key = _participant_key(entry_id, provider, run_id)
        if key in seen or key not in owned:
            raise ValueError("participant is duplicate or absent from owned launch receipt")
        seen.add(key)
        roles_input = participant.get("roles")
        if not isinstance(roles_input, Mapping) or set(roles_input) != set(_ROLES):
            raise ValueError("both actual guest roles are required")
        roles = {}
        for role in _ROLES:
            value = roles_input[role]
            owned_role = owned[key][role]
            if value.get("vm_id") != owned_role.get("vm_id") or value.get("connection") != owned_role.get("connection"):
                raise ValueError("runtime guest identity differs from provider-owned launch receipt")
            workspace = _required_text(value.get("workspace"), "guest workspace")
            input_processes = value.get("processes")
            if not isinstance(input_processes, Sequence) or isinstance(input_processes, (str, bytes)):
                raise ValueError("actual process receipts are required")
            expected_kinds = _PROCESS_KINDS[role]
            by_kind = {p.get("kind"): _process(p, p.get("kind")) for p in input_processes if isinstance(p, Mapping)}
            if set(by_kind) != expected_kinds or len(by_kind) != len(input_processes):
                raise ValueError("process receipts are incomplete or duplicated")
            identities = {(item["pid"], item["boot_id"], item["start_ticks"]) for item in by_kind.values()}
            if len(identities) != len(by_kind) or len({item["pid"] for item in by_kind.values()}) != len(by_kind):
                raise ValueError("process receipts reuse a guest process identity")
            files_input = value.get("files")
            if not isinstance(files_input, Sequence) or isinstance(files_input, (str, bytes)):
                raise ValueError("actual file receipts are required")
            required = {"sockperf_chunk", "diagnostics"} if role == "vm_a" else {"iperf3_receiver", "diagnostics"}
            files = [_file(f, f.get("kind")) for f in files_input if isinstance(f, Mapping)]
            if len(files) != len(files_input) or not required <= {f["kind"] for f in files}:
                raise ValueError("actual output file receipts are incomplete")
            if role == "vm_a" and not any(f.get("state") == "current" for f in files if f["kind"] == "sockperf_chunk"):
                raise ValueError("current Sockperf chunk receipt is required")
            if len({f["path"] for f in files}) != len(files):
                raise ValueError("duplicate output file receipt")
            roles[role] = {"vm_id": owned_role["vm_id"], "connection": owned_role["connection"],
                "workspace": workspace, "processes": [by_kind[k] for k in sorted(expected_kinds)],
                "files": files}
        entries.append({"entry_id": entry_id, "provider": provider, "run_id": run_id,
            "deployment_id": deployment_id, "roles": roles})
    if seen != set(owned):
        raise ValueError("snapshot must contain the complete active participant set")
    return {"stage_id": stage_id, "campaign_id": campaign_id, "entries": entries}


def publish_bindings(path: Path, bindings: Mapping[str, Any]) -> str:
    """Atomically replace the binding snapshot and return its exact-byte digest."""
    raw = _json_bytes(bindings)
    _atomic_bytes(Path(path), raw, replace=True)
    return hashlib.sha256(raw).hexdigest()


def validate_guest_progress(value: Mapping[str, Any], *, deployment_id: str) -> dict[str, Any]:
    """Validate the bounded VM-A progress receipt before controller callbacks."""
    if (not isinstance(value, Mapping) or set(value) != {"deployment_id", "producer", "sockperf_chunks"}
            or value.get("deployment_id") != deployment_id):
        raise ValueError("guest observer progress deployment/schema mismatch")
    chunks = value.get("sockperf_chunks")
    if not isinstance(chunks, list) or len(chunks) > 288:
        raise ValueError("guest observer Sockperf chunk list is invalid")
    normalized = []
    seen = set()
    current = []
    for chunk in chunks:
        if not isinstance(chunk, Mapping):
            raise ValueError("guest observer Sockperf chunk receipt is malformed")
        row = _file({"kind": "sockperf_chunk", **chunk}, "sockperf_chunk")
        if set(chunk) != {"path", "state"} or row["path"] in seen:
            raise ValueError("guest observer Sockperf chunk is duplicated or malformed")
        seen.add(row["path"])
        normalized.append({"path": row["path"], "state": row["state"]})
        if row["state"] == "current":
            current.append(row["path"])
    producer = value.get("producer")
    if producer is not None:
        producer = _process({"kind": "producer", **producer}, "producer")
    if ((producer is None and current) or (producer is not None and len(current) != 1)
            or (producer is not None and normalized[-1]["path"] != current[0])):
        raise ValueError("Sockperf producer and current chunk state are inconsistent")
    return {"deployment_id": deployment_id, "producer": producer, "sockperf_chunks": normalized}


class GuestObserverProgress:
    """Durably record the real DAY Sockperf producer and chunk rotations on VM A."""

    def __init__(self, path: Path, *, deployment_id: str) -> None:
        self.path = Path(path)
        self.deployment_id = _required_text(deployment_id, "deployment_id")
        self._lock = threading.Lock()
        self._current_process: dict[str, Any] | None = None
        self._current_path: str | None = None
        self._completed: list[str] = []

    def __call__(self, event: Mapping[str, Any]) -> None:
        if not isinstance(event, Mapping) or event.get("event") not in {"started", "completed"}:
            raise ValueError("unknown guest observer process event")
        process = _process(event, "producer")
        path = _file({"kind": "sockperf_chunk", "path": event.get("path"),
                      "state": "current"}, "sockperf_chunk")["path"]
        with self._lock:
            if event["event"] == "started":
                if self._current_process is not None:
                    raise ValueError("Sockperf rotation began before the previous process completed")
                self._current_process, self._current_path = process, path
            else:
                if self._current_process != process or self._current_path != path:
                    raise ValueError("Sockperf completion does not match its captured launch")
                self._completed.append(path)
                self._current_process, self._current_path = None, None
            snapshot = {"deployment_id": self.deployment_id,
                "producer": self._current_process,
                "sockperf_chunks": ([{"path": item, "state": "completed"} for item in self._completed]
                    + ([{"path": self._current_path, "state": "current"}] if self._current_path else []))}
            _atomic_bytes(self.path, _json_bytes(snapshot), replace=True)
