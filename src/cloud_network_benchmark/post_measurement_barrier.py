"""Identity-scoped, durable gate before any post-measurement archive work."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Callable, Mapping


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


class PostMeasurementBarrier:
    """Collect actual close-stage terminal events and release only as a full set.

    State is bound to the preparer's exact barrier and participant list. A failed
    participant, cancellation, or original-deadline expiry atomically aborts the
    group; there is no reset/retry path.
    """

    def __init__(self, spec: Mapping[str, Any]):
        if not isinstance(spec, Mapping) or spec.get("schema_version") != 1:
            raise ValueError("post-measurement barrier requires schema version 1")
        barrier_id = spec.get("barrier_id")
        root_value = spec.get("barrier_root", spec.get("absolute_barrier_root"))
        participants = spec.get("participants", spec.get("expected_participants"))
        if not isinstance(barrier_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", barrier_id):
            raise ValueError("barrier identity is malformed")
        if not isinstance(root_value, str) or not Path(root_value).is_absolute():
            raise ValueError("barrier root must be an absolute path")
        if not isinstance(participants, list) or len(participants) < 1:
            raise ValueError("barrier must declare participants")
        normalized = []
        seen = set()
        for row in participants:
            if (not isinstance(row, Mapping) or set(row) != {"entry_id", "provider", "run_id"}
                    or not all(isinstance(row.get(key), str) and row[key] for key in row)):
                raise ValueError("barrier participant identity is malformed")
            identity = (row["entry_id"], row["provider"], row["run_id"])
            if identity in seen:
                raise ValueError("barrier participant identities must be unique")
            seen.add(identity)
            normalized.append(dict(row))
        root = Path(root_value)
        if root.is_symlink():
            raise ValueError("barrier root cannot be a symlink")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root.resolve(strict=True) != root.absolute():
            raise ValueError("barrier root cannot traverse symlink components")
        self.spec = {"schema_version": 1, "barrier_id": barrier_id,
            "barrier_root": str(root), "participants": normalized}
        if spec.get("start_utc") is not None:
            from datetime import datetime
            start = datetime.fromisoformat(spec["start_utc"].replace("Z", "+00:00"))
            if start.tzinfo is None or start.utcoffset() is None:
                raise ValueError("barrier frozen start UTC must include timezone")
            self.spec["start_utc"] = start.astimezone(__import__("datetime").timezone.utc).isoformat()
        self.digest = hashlib.sha256(_canonical(self.spec)).hexdigest()
        self.path = root / f"{barrier_id}.state.json"
        self.lock_path = root / f"{barrier_id}.lock"
        self._initialize()

    def _locked(self, callback: Callable[[dict[str, Any]], Any], *, deadline: Any = None,
            clock: Callable[[], float] = time.monotonic, cancelled: Callable[[], bool] = lambda: False,
            maximum_wait: float = 1.0) -> Any:
        if self.lock_path.is_symlink() or self.path.is_symlink():
            raise ValueError("barrier state and lock must not be symlinks")
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.lock_path, flags, 0o600)
        try:
            end = clock() + maximum_wait
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if cancelled():
                        raise InterruptedError("barrier lock acquisition was cancelled")
                    remaining = min(maximum_wait, end - clock())
                    if deadline is not None:
                        remaining = min(remaining, deadline.remaining(clock, cleanup=True))
                    if remaining <= 0:
                        raise TimeoutError("barrier lock acquisition reached its bounded deadline")
                    time.sleep(min(0.025, remaining))
            state = json.loads(self.path.read_text(encoding="utf-8"))
            if state.get("spec_sha256") != self.digest or state.get("spec") != self.spec:
                raise ValueError("barrier state belongs to a different or replaced participant set")
            return callback(state)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _write(self, state: dict[str, Any]) -> None:
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".partial", dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(_canonical(state) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.path)
            dirfd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    def _initialize(self) -> None:
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.lock_path, flags, 0o600)
        try:
            end = time.monotonic() + 1.0
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    remaining = end - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("barrier initialization lock is already held") from exc
                    time.sleep(min(0.025, remaining))
            if self.path.exists():
                state = json.loads(self.path.read_text(encoding="utf-8"))
                if state.get("spec_sha256") != self.digest or state.get("spec") != self.spec:
                    raise ValueError("existing barrier state conflicts with prepared identity")
            else:
                self._write({"schema_version": 1, "barrier_id": self.spec["barrier_id"],
                    "spec_sha256": self.digest, "spec": self.spec, "state": "waiting",
                    "terminal": {}, "abort_reason": None})
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def signal_terminal(self, participant: Mapping[str, str], *, succeeded: bool,
            deadline: Any = None, clock: Callable[[], float] = time.monotonic) -> dict:
        if type(succeeded) is not bool:
            raise ValueError("measurement terminal outcome must be explicit")
        key = _canonical(dict(participant)).decode()
        identities = {_canonical(row).decode() for row in self.spec["participants"]}
        if key not in identities:
            raise ValueError("terminal signal is outside the prepared participant set")
        def apply(state: dict[str, Any]) -> dict:
            if state["state"] == "aborted":
                return dict(state)
            previous = state["terminal"].get(key)
            if previous is not None and previous != succeeded:
                raise ValueError("participant terminal signal conflicts with its prior result")
            state["terminal"][key] = succeeded
            if not succeeded:
                state["state"] = "aborted"
                state["abort_reason"] = "participant_measurement_or_close_failed"
            elif len(state["terminal"]) == len(self.spec["participants"]):
                state["state"] = "released"
            self._write(state)
            return dict(state)
        return self._locked(apply, deadline=deadline, clock=clock)

    def abort(self, reason: str, *, deadline: Any = None,
            clock: Callable[[], float] = time.monotonic, nonblocking: bool = False) -> dict:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("barrier abort requires a reason")
        def apply(state: dict[str, Any]) -> dict:
            if state["state"] != "released":
                state["state"] = "aborted"
                state["abort_reason"] = reason[:200]
                self._write(state)
            return dict(state)
        return self._locked(apply, deadline=deadline, clock=clock,
            maximum_wait=0.0 if nonblocking else 1.0)

    def wait_released(self, *, deadline: Any, clock: Callable[[], float] = time.monotonic,
            cancelled: Callable[[], bool] = lambda: False,
            sleep: Callable[[float], None] = time.sleep) -> dict:
        while True:
            if cancelled():
                try:
                    self.abort("participant_cancelled", deadline=deadline, clock=clock, nonblocking=True)
                except (TimeoutError, InterruptedError):
                    pass
                raise InterruptedError("post-measurement barrier was cancelled")
            remaining = deadline.remaining(clock, cleanup=True)
            if remaining <= 0:
                try:
                    self.abort("original_deadline_expired_before_barrier_release",
                        deadline=deadline, clock=clock, nonblocking=True)
                except (TimeoutError, InterruptedError):
                    pass
                raise TimeoutError("post-measurement barrier reached the original deadline")
            snapshot = self._locked(lambda state: dict(state), clock=clock,
                cancelled=cancelled, maximum_wait=remaining)
            if snapshot["state"] == "released":
                return snapshot
            if snapshot["state"] == "aborted":
                raise RuntimeError("post-measurement barrier aborted: " + str(snapshot.get("abort_reason")))
            sleep(min(0.25, remaining))
