"""Atomic, hash-pinned publication primitives for actual guest observer evidence.

This module deliberately accepts captured process and file records. It never
infers a process, guest path, VM, or connection from a name or timestamp.
"""
from __future__ import annotations

import hashlib
import contextlib
import json
import math
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
    if kind in {"sockperf_chunk", "iperf3_client"}:
        state = record.get("state")
        if state not in {"current", "completed"}:
            raise ValueError("measurement output must be an observed current/completed output")
        result["state"] = state
        if "output_state" in record:
            if record["output_state"] not in {"finalization_pending", "streaming", "complete", "failed"}:
                raise ValueError("measurement output finalization state is invalid")
            result["output_state"] = record["output_state"]
        for key in ("unit_id", "phase", "streams", "trial_index", "parent_unit_id"):
            if key in record:
                result[key] = record[key]
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
            by_kind = {p.get("kind"): ((_progress_process(p, p.get("kind"))
                if p.get("kind") == "tcp_client" or "action_id" in p else _process(p, p.get("kind")))
                if p.get("kind") in {"producer", "tcp_client"} else _process(p, p.get("kind")))
                for p in input_processes if isinstance(p, Mapping)}
            valid_kinds = (set(by_kind) in (expected_kinds, expected_kinds | {"tcp_client"})
                           if role == "vm_a" else set(by_kind) == expected_kinds)
            if not valid_kinds or len(by_kind) != len(input_processes):
                raise ValueError("process receipts are incomplete or duplicated")
            identities = {(item["pid"], item["boot_id"], item["start_ticks"]) for item in by_kind.values()}
            if len(identities) != len(by_kind) or len({item["pid"] for item in by_kind.values()}) != len(by_kind):
                raise ValueError("process receipts reuse a guest process identity")
            files_input = value.get("files")
            if not isinstance(files_input, Sequence) or isinstance(files_input, (str, bytes)):
                raise ValueError("actual file receipts are required")
            required = {"sockperf_chunk", "diagnostics"} if role == "vm_a" else {"iperf3_receiver", "diagnostics"}
            if role == "vm_a" and "tcp_client" in by_kind:
                required.add("iperf3_client")
            files = [_file(f, f.get("kind")) for f in files_input if isinstance(f, Mapping)]
            if len(files) != len(files_input) or not required <= {f["kind"] for f in files}:
                raise ValueError("actual output file receipts are incomplete")
            if role == "vm_a" and not any(f.get("state") == "current"
                    for f in files if f["kind"] == "sockperf_chunk"):
                raise ValueError("current Sockperf chunk receipt is required")
            if len({f["path"] for f in files}) != len(files):
                raise ValueError("duplicate output file receipt")
            roles[role] = {"vm_id": owned_role["vm_id"], "connection": owned_role["connection"],
                "workspace": workspace, "processes": [by_kind[k] for k in sorted(by_kind)],
                "files": files}
        workload = participant.get("active_workload")
        if workload is not None:
            if (not isinstance(workload, Mapping) or workload.get("unit_id") not in {
                    item.get("unit_id") for item in roles["vm_a"]["files"] if item["kind"] == "sockperf_chunk"}
                    or workload.get("phase") not in {"idle_latency", "single_flow", "multi_flow"}
                    or workload.get("sockperf_output", {}).get("state") != "finalization_pending"):
                raise ValueError("active workload phase/output mapping is inconsistent with current Sockperf process")
            receiver_files = [item for item in roles["vm_b"]["files"] if item["kind"] == "iperf3_receiver"]
            receiver_procs = [item for item in roles["vm_b"]["processes"] if item["kind"] == "receiver"]
            if (len(receiver_files) != 1 or len(receiver_procs) != 1
                    or workload.get("iperf3_receiver", {}).get("pid") != receiver_procs[0]["pid"]
                    or workload.get("iperf3_receiver", {}).get("path") != receiver_files[0]["path"]):
                raise ValueError("active workload does not bind its actual VM-B receiver and output")
        entries.append({"entry_id": entry_id, "provider": provider, "run_id": run_id,
            "deployment_id": deployment_id, "roles": roles,
            **({"active_workload": dict(workload)} if workload is not None else {})})
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
    if (not isinstance(value, Mapping)
            or set(value) not in ({"deployment_id", "producer", "sockperf_chunks"},
                                  {"deployment_id", "producer", "sockperf_chunks", "tcp_client", "iperf3_outputs"})
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
        allowed = {"path", "state"} if "unit_id" not in chunk else {
            "path", "state", "output_state", "unit_id", "phase", "streams", "trial_index", "parent_unit_id"}
        if set(chunk) != allowed or row["path"] in seen:
            raise ValueError("guest observer Sockperf chunk is duplicated or malformed")
        seen.add(row["path"])
        normalized.append({key: chunk[key] for key in allowed if key in chunk})
        if row["state"] == "current":
            current.append(row["path"])
    producer = value.get("producer")
    if producer is not None:
        producer = (_progress_process(producer, "producer") if "action_id" in producer
                    else _process({"kind": "producer", **producer}, "producer"))
    if ((producer is None and current) or (producer is not None and len(current) != 1)
            or (producer is not None and normalized[-1]["path"] != current[0])):
        raise ValueError("Sockperf producer and current chunk state are inconsistent")
    if producer is not None and "unit_id" in normalized[-1]:
        current_row = normalized[-1]
        if (producer["unit_id"] != current_row["unit_id"] or producer["path"] != current_row["path"]
                or current_row.get("output_state") != "finalization_pending"):
            raise ValueError("Sockperf process and pending-output mapping differ")
    tcp_client = value.get("tcp_client")
    outputs = value.get("iperf3_outputs", [])
    if "tcp_client" in value:
        tcp_client = ((_progress_process(tcp_client, "tcp_client") if "action_id" in tcp_client
                       else _process({"kind": "tcp_client", **tcp_client}, "tcp_client"))
                      if tcp_client is not None else None)
        if not isinstance(outputs, list) or len(outputs) > 288:
            raise ValueError("guest observer iperf3 output list is invalid")
        normalized_outputs = []
        output_paths = set()
        for output in outputs:
            if not isinstance(output, Mapping):
                raise ValueError("guest observer iperf3 output receipt is malformed")
            row = _file({"kind": "iperf3_client", **output}, "iperf3_client")
            allowed = {"path", "state", "output_state", "unit_id", "phase", "streams", "trial_index", "parent_unit_id"}
            if set(output) != allowed or row["path"] in output_paths:
                raise ValueError("guest observer iperf3 output receipt is duplicated or malformed")
            output_paths.add(row["path"])
            normalized_outputs.append({key: output[key] for key in allowed})
        if tcp_client is not None and not any(
                item["path"] == tcp_client["path"] and item["state"] == "current" for item in normalized_outputs):
            raise ValueError("active iperf3 client is missing its current output mapping")
    return {"deployment_id": deployment_id, "producer": producer, "sockperf_chunks": normalized,
            **({"tcp_client": tcp_client, "iperf3_outputs": normalized_outputs} if "tcp_client" in value else {})}


def _progress_process(value: Mapping[str, Any], kind: str) -> dict[str, Any]:
    process = _process({"kind": kind, **value}, kind)
    required = {"kind", "pid", "boot_id", "start_ticks", "path", "action_id", "unit_id", "timeout_seconds",
                "requested_seconds", "finalization_grace_seconds", "phase", "streams", "trial_index",
                "parent_unit_id", "streaming_output"}
    if set(value) != required:
        raise ValueError("observer process workload identity is incomplete")
    path = _file({"kind": "sockperf_chunk" if kind == "producer" else "iperf3_client",
                  "path": value["path"], "state": "current"},
                 "sockperf_chunk" if kind == "producer" else "iperf3_client")["path"]
    if (path != value["path"] or not isinstance(value["action_id"], str) or not value["action_id"]
            or not isinstance(value["unit_id"], str) or not value["unit_id"]
            or not isinstance(value["phase"], str) or not value["phase"]
            or type(value["requested_seconds"]) is not int or value["requested_seconds"] <= 0
            or isinstance(value["timeout_seconds"], bool) or not isinstance(value["timeout_seconds"], (int, float))
            or not math.isfinite(value["timeout_seconds"]) or value["timeout_seconds"] <= 0
            or isinstance(value["finalization_grace_seconds"], bool)
            or not isinstance(value["finalization_grace_seconds"], (int, float))
            or not math.isfinite(value["finalization_grace_seconds"])
            or value["finalization_grace_seconds"] <= 0
            or value["streams"] is not None and (type(value["streams"]) is not int or value["streams"] <= 0)
            or value["trial_index"] is not None and (type(value["trial_index"]) is not int or value["trial_index"] <= 0)
            or value["parent_unit_id"] is not None and not isinstance(value["parent_unit_id"], str)
            or type(value["streaming_output"]) is not bool):
        raise ValueError("observer process workload identity is invalid")
    return {**process, **{key: value[key] for key in required - {"pid", "boot_id", "start_ticks", "kind"}}}


class GuestObserverProgress:
    """Durably record actual phase-aware measurement processes and outputs on VM A."""

    def __init__(self, path: Path, *, deployment_id: str, observation: Any | None = None) -> None:
        self.path = Path(path)
        self.deployment_id = _required_text(deployment_id, "deployment_id")
        self._lock = threading.Lock()
        self._current_process: dict[str, Any] | None = None
        self._current_path: str | None = None
        self._completed: list[str] = []
        self._chunks: list[dict[str, Any]] = []
        self._tcp_client: dict[str, Any] | None = None
        self._iperf3_outputs: list[dict[str, Any]] = []
        self._units = {}
        self._day_context = None
        if observation is not None:
            for unit in observation.measurement_units:
                metadata = {"unit_id": unit.unit_id, "phase": str(getattr(unit.phase, "value", unit.phase)),
                    "streams": unit.streams, "trial_index": unit.trial_index,
                    "parent_unit_id": unit.parent_unit_id}
                self._units[unit.unit_id] = metadata
                if unit.kind == "continuous_day":
                    self._day_context = metadata

    def __call__(self, event: Mapping[str, Any]) -> None:
        if not isinstance(event, Mapping) or event.get("event") not in {"started", "completed"}:
            raise ValueError("unknown guest observer process event")
        kind = event.get("kind")
        if kind not in {"producer", "tcp_client"}:
            raise ValueError("unknown guest observer measurement process")
        prefix = "sockperf-" if kind == "producer" else "iperf3-"
        action_id = event.get("action_id")
        unit_id = event.get("unit_id")
        if not isinstance(action_id, str) or not action_id.startswith(prefix) or action_id[len(prefix):] != unit_id:
            raise ValueError("observer process action does not identify its actual measurement unit")
        unit = self._units.get(unit_id)
        if unit is None and self._day_context is not None and unit_id.startswith("probe-chunk-"):
            unit = {**self._day_context, "unit_id": unit_id,
                    "parent_unit_id": self._day_context["unit_id"]}
        if unit is None:
            raise ValueError("observer process action does not match a resolved measurement unit")
        exit_code = event.get("exit_code")
        if event["event"] == "completed" and (type(exit_code) is not int):
            raise ValueError("measurement completion has no actual process exit code")
        enriched = {key: value for key, value in event.items() if key not in {"event", "exit_code"}}
        enriched.update({key: unit[key] for key in ("phase", "streams", "trial_index", "parent_unit_id")})
        process = _progress_process(enriched, kind)
        path = process["path"]
        with self._lock:
            if kind == "producer":
                if event["event"] == "started":
                    if self._current_process is not None:
                        raise ValueError("Sockperf rotation began before the previous process completed")
                    self._current_process, self._current_path = process, path
                else:
                    if self._current_process != process or self._current_path != path:
                        raise ValueError("Sockperf completion does not match its captured launch")
                    self._completed.append(path)
                    self._current_process, self._current_path = None, None
            else:
                if event["event"] == "started":
                    if self._tcp_client is not None:
                        raise ValueError("iperf3 client began before the previous client completed")
                    self._tcp_client = process
                else:
                    if self._tcp_client != process:
                        raise ValueError("iperf3 completion does not match its captured launch")
                    self._tcp_client = None
                    for row in self._iperf3_outputs:
                        if row["path"] == path:
                            row["state"], row["output_state"] = "completed", "complete" if exit_code == 0 else "failed"
            if kind == "producer" and event["event"] == "started":
                output = {**{key: process[key] for key in ("unit_id", "phase", "streams", "trial_index", "parent_unit_id")},
                    "path": path, "state": "current", "output_state": "finalization_pending"}
                self._completed = [old for old in self._completed if old != path]
                self._chunks = [item for item in getattr(self, "_chunks", []) if item["path"] != path]
                self._chunks.append(output)
            elif kind == "producer" and event["event"] == "completed":
                for row in self._chunks:
                    if row["path"] == path:
                        row["state"], row["output_state"] = "completed", "complete" if exit_code == 0 else "failed"
            elif kind == "tcp_client" and event["event"] == "started":
                output = {**{key: process[key] for key in ("unit_id", "phase", "streams", "trial_index", "parent_unit_id")},
                    "path": path, "state": "current",
                    "output_state": "streaming" if process["streaming_output"] else "finalization_pending"}
                self._iperf3_outputs = [row for row in self._iperf3_outputs if row["path"] != path]
                self._iperf3_outputs.append(output)
            snapshot = {"deployment_id": self.deployment_id,
                "producer": self._current_process,
                "sockperf_chunks": getattr(self, "_chunks", []),
                "tcp_client": self._tcp_client, "iperf3_outputs": self._iperf3_outputs}
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
        raise ValueError("actual VM-A Sockperf producer is not active; measurement is between invocations")
    chunks = progress.get("sockperf_chunks")
    if not isinstance(chunks, list) or not any(item.get("state") == "current" for item in chunks):
        raise ValueError("actual current VM-A Sockperf output chunk is unavailable")
    if any(item.get("output_state") == "failed" for item in chunks):
        raise ValueError("a Sockperf invocation ended unsuccessfully")
    if set(diagnostics) != {"vm_a", "vm_b"}:
        raise ValueError("both actual diagnostics process receipts are required")
    tcp_client = progress.get("tcp_client")
    tcp_outputs = progress.get("iperf3_outputs", [])
    if tcp_client is not None and not isinstance(tcp_outputs, list):
        raise ValueError("actual VM-A iperf3 output mappings are malformed")
    if any(item.get("output_state") == "failed" for item in tcp_outputs):
        raise ValueError("an iperf3 client invocation ended unsuccessfully")
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
        processes = ([dict(producer),
                      {"kind": "diagnostics", "pid": diagnostic.pid,
                       "boot_id": diagnostic.boot_id, "start_ticks": diagnostic.start_ticks}]
                     if role == "vm_a" else
                     [{"kind": "receiver", "pid": listener.pid,
                       "boot_id": listener.boot_id, "start_ticks": listener.start_ticks},
                      {"kind": "diagnostics", "pid": diagnostic.pid,
                       "boot_id": diagnostic.boot_id, "start_ticks": diagnostic.start_ticks}])
        if role == "vm_a" and tcp_client is not None:
            processes.append(dict(tcp_client))
        files = ([{"kind": "sockperf_chunk", **dict(chunk)} for chunk in chunks]
                 if role == "vm_a" else [])
        if role == "vm_a":
            files.extend({"kind": "iperf3_client", **dict(item)} for item in tcp_outputs)
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
        "deployment_id": deployment_id, "roles": roles,
        "active_workload": {"unit_id": producer["unit_id"], "phase": producer["phase"],
            "streams": producer["streams"], "trial_index": producer["trial_index"],
            "parent_unit_id": producer["parent_unit_id"],
            "sockperf_output": {"path": producer["path"], "state": "finalization_pending"},
            "iperf3_client": (None if tcp_client is None else {"unit_id": tcp_client["unit_id"],
                "path": tcp_client["path"], "state": tcp_client["streaming_output"] and "streaming"
                    or "finalization_pending"}),
            "iperf3_receiver": {"pid": listener.pid, "path": PurePosixPath(listener.stdout_path).relative_to(
                PurePosixPath(workspace)).as_posix()}}}
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


def invalidate_runtime_observer_snapshot(config: Mapping[str, Any], *,
        participant: tuple[str, str, str] | None = None) -> None:
    """Invalidate one participant receipt, or the whole stage when none is named."""
    bindings_path = Path(config["bindings_path"])
    prepared_path = Path(config["prepared_stage_path"])
    state_path = Path(config["state_path"])
    with _exclusive_file_lock(state_path.with_suffix(".lock")):
        prepared = json.loads(prepared_path.read_text(encoding="utf-8"))
        if prepared.get("stage_id") != config.get("stage_id"):
            raise ValueError("observer invalidation stage identity mismatch")
        if participant is None:
            state = {"schema_version": 1, "owned": {}, "participants": {}}
        else:
            state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {
                "schema_version": 1, "owned": {}, "participants": {}}
            if (state.get("schema_version") != 1 or not isinstance(state.get("owned"), dict)
                    or not isinstance(state.get("participants"), dict)):
                raise ValueError("observer aggregate state is malformed")
            key = _participant_key(*participant)
            state["owned"].pop(key, None)
            state["participants"].pop(key, None)
        digest = publish_bindings(bindings_path, {"stage_id": config["stage_id"],
            "campaign_id": config["campaign_id"], "entries": []})
        _atomic_bytes(state_path, _json_bytes(state), replace=True)
        prepared["observer_bindings_sha256"] = digest
        _atomic_bytes(prepared_path,
            (json.dumps(prepared, sort_keys=True, indent=2) + "\n").encode(), replace=True)
