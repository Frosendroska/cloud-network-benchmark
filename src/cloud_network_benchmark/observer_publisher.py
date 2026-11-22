"""Atomic, hash-pinned publication primitives for actual guest observer evidence.

This module deliberately accepts captured process and file records. It never
infers a process, guest path, VM, or connection from a name or timestamp.
"""
from __future__ import annotations

import hashlib
import contextlib
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


@contextlib.contextmanager
def _exclusive_file_lock(path: Path):
    import fcntl
    path = Path(path)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("observer lock path must be absolute and cannot be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _remote_role_binding(remote: Any, *, vm_id: str, workspace: str) -> dict[str, Any]:
    connection = {"host": remote.host, "user": remote.user, "port": remote.port,
        "known_hosts": str(Path(remote.host_key_reference)),
        "identity_file": str(Path(remote.authentication_reference))}
    return {"vm_id": vm_id, "connection": connection, "workspace": workspace}


def _observer_participant(config: Mapping[str, Any], deployment: Any, pair: Any,
                          connection: Any, bootstrap: Any, diagnostics: Mapping[str, Any],
                          progress: Mapping[str, Any]) -> tuple[tuple[str, str, str], dict[str, Any], dict[str, Any]]:
    """Project only identities and paths already captured by production lifecycle callbacks."""
    entry_id = _required_text(config.get("entry_id"), "observer entry_id")
    provider = _required_text(getattr(deployment.provider, "value", deployment.provider), "observer provider")
    run_id = _required_text(deployment.run_id, "observer run_id")
    deployment_id = _required_text(deployment.deployment_id, "observer deployment_id")
    if pair.deployment_id != deployment_id or connection.deployment_id != deployment_id:
        raise ValueError("observer callback deployment identity mismatch")
    workspace = _required_text(config.get("workspace"), "observer workspace")
    listener_rows = [item for item in bootstrap.listeners
        if item.tool == "iperf3" and item.role == "vm_b" and item.state == "active"]
    if len(listener_rows) != 1:
        raise ValueError("one actual active VM-B iperf3 listener identity is required")
    listener = listener_rows[0]
    if any(getattr(listener, key, None) is None for key in ("pid", "boot_id", "start_ticks")):
        raise ValueError("VM-B iperf3 listener has no captured process identity")
    producer = progress.get("producer")
    if producer is None:
        raise ValueError("actual VM-A Sockperf producer is no longer active")
    chunks = progress.get("sockperf_chunks")
    if not isinstance(chunks, list) or not any(item.get("state") == "current" for item in chunks):
        raise ValueError("actual current VM-A Sockperf output chunk is unavailable")
    if set(diagnostics) != {"vm_a", "vm_b"}:
        raise ValueError("both actual diagnostics process receipts are required")
    roles = {}
    owned = {}
    remote_by_role = {"vm_a": connection.vm_a, "vm_b": connection.vm_b}
    deployed_by_role = {"vm_a": pair.vm_a, "vm_b": pair.vm_b}
    for role in ("vm_a", "vm_b"):
        diagnostic = diagnostics[role]
        if (diagnostic.deployment_id != deployment_id or diagnostic.role != role
                or any(getattr(diagnostic, field, None) is None for field in ("pid", "boot_id", "start_ticks"))):
            raise ValueError(f"{role} diagnostics process receipt is incomplete or mismatched")
        owned[role] = _remote_role_binding(remote_by_role[role],
            vm_id=deployed_by_role[role].resource_id, workspace=workspace)
        processes = ([{"kind": "producer", **dict(producer)},
                      {"kind": "diagnostics", "pid": diagnostic.pid,
                       "boot_id": diagnostic.boot_id, "start_ticks": diagnostic.start_ticks}]
                     if role == "vm_a" else
                     [{"kind": "receiver", "pid": listener.pid,
                       "boot_id": listener.boot_id, "start_ticks": listener.start_ticks},
                      {"kind": "diagnostics", "pid": diagnostic.pid,
                       "boot_id": diagnostic.boot_id, "start_ticks": diagnostic.start_ticks}])
        files = ([{"kind": "sockperf_chunk", **dict(chunk)} for chunk in chunks]
                 if role == "vm_a" else [])
        if role == "vm_b":
            remote_output = PurePosixPath(listener.stdout_path)
            root = PurePosixPath(workspace)
            try:
                receiver_path = remote_output.relative_to(root).as_posix()
            except ValueError as exc:
                raise ValueError("VM-B receiver output escapes its declared workspace") from exc
            files.append({"kind": "iperf3_receiver", "path": receiver_path})
        files.append({"kind": "diagnostics", "path": f"diagnostics/{role}.jsonl"})
        roles[role] = {**owned[role], "processes": processes, "files": files}
    participant = {"entry_id": entry_id, "provider": provider, "run_id": run_id,
        "deployment_id": deployment_id, "roles": roles}
    return (entry_id, provider, run_id), owned, participant


def publish_runtime_observer_snapshot(config: Mapping[str, Any], *, deployment: Any,
        pair: Any, connection: Any, bootstrap: Any, diagnostics: Mapping[str, Any],
        progress: Mapping[str, Any]) -> dict[str, Any]:
    """Aggregate a complete active set from callbacks carrying native lifecycle receipts."""
    required = {"stage_id", "campaign_id", "entry_id", "workspace", "state_path", "journal_path",
        "prepared_stage_path", "bindings_path", "launch_receipt_root"}
    if not isinstance(config, Mapping) or not required <= config.keys():
        raise ValueError("observer runtime configuration is incomplete")
    key, owned, participant = _observer_participant(config, deployment, pair, connection,
        bootstrap, diagnostics, progress)
    state_path, journal_path = Path(config["state_path"]), Path(config["journal_path"])
    prepared_path, bindings_path = Path(config["prepared_stage_path"]), Path(config["bindings_path"])
    receipt_root = Path(config["launch_receipt_root"])
    lock_path = state_path.with_suffix(".lock")
    for path in (state_path, journal_path, prepared_path, bindings_path, receipt_root):
        if not path.is_absolute() or path.is_symlink():
            raise ValueError("observer runtime paths must be absolute and cannot be symlinks")
    with _exclusive_file_lock(lock_path):
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {
            "schema_version": 1, "owned": {}, "participants": {}}
        if state.get("schema_version") != 1 or not isinstance(state.get("owned"), dict) or not isinstance(state.get("participants"), dict):
            raise ValueError("observer aggregate state is malformed")
        key_text = _participant_key(*key)
        state["owned"][key_text] = owned
        state["participants"][key_text] = participant
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        prepared = json.loads(prepared_path.read_text(encoding="utf-8"))
        if journal.get("stage_id") != config["stage_id"] or prepared.get("stage_id") != config["stage_id"]:
            raise ValueError("observer aggregate stage identity mismatch")
        active_ids = {entry_id for entry_id, row in journal.get("entries", {}).items()
                      if row.get("state") in {"starting", "running"}}
        expected = {_participant_key(entry["entry_id"], provider, run_id)
            for entry in prepared.get("entries", []) if entry["entry_id"] in active_ids
            for provider, run_id in entry.get("run_ids", {}).items()}
        actual = set(state["participants"]) & expected
        bindings_path.parent.mkdir(parents=True, exist_ok=True)
        if actual == expected and expected:
            receipt_participants = {tuple(json.loads(row)): state["owned"][row] for row in sorted(expected)}
            receipt_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            receipt_value = {"stage_id": config["stage_id"], "participants": {
                _participant_key(*identity): {role: {"vm_id": value["vm_id"],
                    "connection": value["connection"]} for role, value in roles.items()}
                for identity, roles in receipt_participants.items()}}
            receipt_raw = _json_bytes(receipt_value)
            receipt_path = receipt_root / f"launch-{hashlib.sha256(receipt_raw).hexdigest()}.json"
            receipt_sha256 = hashlib.sha256(receipt_raw).hexdigest()
            if receipt_path.exists() or receipt_path.is_symlink():
                existing_receipt = receipt_path.read_bytes() if receipt_path.is_file() and not receipt_path.is_symlink() else b""
                if (not existing_receipt or existing_receipt != receipt_raw
                        or hashlib.sha256(existing_receipt).hexdigest() != receipt_sha256):
                    raise ValueError("occupied immutable launch receipt differs from the exact owned identities")
            else:
                saved_sha256 = write_launch_receipt(receipt_path, stage_id=config["stage_id"],
                    participants=receipt_participants)
                if saved_sha256 != receipt_sha256:
                    raise ValueError("immutable launch receipt hash differs from its owned identity projection")
            receipt = json.loads(receipt_raw)
            participants = [state["participants"][row] for row in sorted(expected)]
            bindings = build_bindings(stage_id=config["stage_id"], campaign_id=config["campaign_id"],
                participants=participants, launch_receipt=receipt)
            bindings_sha256 = publish_bindings(bindings_path, bindings)
            prepared["observer_launch_receipt_path"] = str(receipt_path)
            prepared["observer_launch_receipt_sha256"] = receipt_sha256
            prepared["observer_bindings_sha256"] = bindings_sha256
        else:
            prepared["observer_bindings_sha256"] = publish_bindings(bindings_path,
                {"stage_id": config["stage_id"], "campaign_id": config["campaign_id"], "entries": []})
        _atomic_bytes(state_path, _json_bytes(state), replace=True)
        _atomic_bytes(prepared_path, (json.dumps(prepared, sort_keys=True, indent=2) + "\n").encode(), replace=True)
        return {"status": "published" if actual == expected and expected else "incomplete",
            "active_participants": len(expected), "observed_participants": len(actual)}


def invalidate_runtime_observer_snapshot(config: Mapping[str, Any]) -> None:
    """Force a hash-consistent unknown snapshot after any incomplete live receipt."""
    bindings_path = Path(config["bindings_path"])
    prepared_path = Path(config["prepared_stage_path"])
    state_path = Path(config["state_path"])
    with _exclusive_file_lock(state_path.with_suffix(".lock")):
        prepared = json.loads(prepared_path.read_text(encoding="utf-8"))
        if prepared.get("stage_id") != config.get("stage_id"):
            raise ValueError("observer invalidation stage identity mismatch")
        digest = publish_bindings(bindings_path, {"stage_id": config["stage_id"],
            "campaign_id": config["campaign_id"], "entries": []})
        prepared["observer_bindings_sha256"] = digest
        _atomic_bytes(prepared_path,
            (json.dumps(prepared, sort_keys=True, indent=2) + "\n").encode(), replace=True)
