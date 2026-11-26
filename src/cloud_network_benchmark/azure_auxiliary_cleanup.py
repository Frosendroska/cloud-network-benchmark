"""Fail-closed filesystem coordination for explicitly authorized Azure auxiliaries.

This module only arbitrates an already source-bound cleanup authorization.  It
never discovers provider ownership, grants authority, retries provider calls,
or widens a cleanup deadline.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import stat
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _write(path: Path, value: dict[str, Any]) -> None:
    fd, name = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".partial", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


class AuxiliaryCleanupCohort:
    """Durable two-owner barrier and one-shot claim for normal S1/S3 cleanup."""

    def __init__(self, spec: dict[str, Any]):
        self.spec = validate_spec(spec)
        root = Path(self.spec["state_root"])
        if root.is_symlink() or any(parent.is_symlink() for parent in root.parents):
            raise ValueError("Azure auxiliary cleanup state root cannot traverse symlinks")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = root.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PermissionError("Azure auxiliary cleanup state root must be privately owned by the current user")
        self.root = root.resolve(strict=True)
        self.configured_root = root.absolute()
        self.digest = hashlib.sha256(canonical({k: v for k, v in self.spec.items() if k != "state_root"})).hexdigest()
        self.path = self.root / (self.spec["cohort_id"] + ".azure-cleanup.json")
        self.lock = self.root / (self.spec["cohort_id"] + ".azure-cleanup.lock")

    def _mutate(self, callback: Callable[[dict[str, Any]], Any]) -> Any:
        if (self.configured_root.is_symlink()
                or any(parent.is_symlink() for parent in self.configured_root.parents)
                or self.configured_root.resolve(strict=True) != self.root):
            raise ValueError("Azure auxiliary cleanup state root changed or traverses a symlink")
        root_info = self.root.stat()
        if root_info.st_uid != os.getuid() or root_info.st_mode & 0o077:
            raise PermissionError("Azure auxiliary cleanup state root must remain private and current-user owned")
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        fd = os.open(self.lock, flags, 0o600)
        try:
            lock_info = os.fstat(fd)
            if (not stat.S_ISREG(lock_info.st_mode) or lock_info.st_uid != os.getuid()
                    or lock_info.st_mode & 0o077):
                raise PermissionError("Azure auxiliary cleanup lock must be a private regular file owned by the current user")
            lock_deadline = time.monotonic() + 1.0
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    remaining = lock_deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Azure auxiliary cleanup lock exceeded its bounded acquisition window")
                    time.sleep(min(0.025, remaining))
            if self.path.is_symlink():
                raise ValueError("Azure auxiliary cleanup state cannot be a symlink")
            if self.path.exists():
                state_info = self.path.stat()
                if (not self.path.is_file() or state_info.st_uid != os.getuid()
                        or state_info.st_mode & 0o077):
                    raise PermissionError("Azure auxiliary cleanup state must be private and owned by the current user")
                state = json.loads(self.path.read_text(encoding="utf-8"))
                if (state.get("schema_version") != 1 or state.get("spec_sha256") != self.digest
                        or state.get("spec") != {k: v for k, v in self.spec.items() if k != "state_root"}):
                    raise ValueError("Azure auxiliary cleanup state is foreign or corrupt")
            else:
                state = {"schema_version": 1, "spec_sha256": self.digest,
                    "spec": {k: v for k, v in self.spec.items() if k != "state_root"},
                    "owners": {}, "claim": None, "outcomes": []}
            result = callback(state)
            _write(self.path, state)
            return result
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _pin_bounded_recovery_deadline(self, candidate: float | None) -> float | None:
        if candidate is not None:
            _finite_monotonic(candidate, "bounded auxiliary recovery deadline")
        def pin(state: dict[str, Any]) -> float | None:
            prior = state.get("bounded_recovery_deadline_monotonic")
            if prior is not None:
                prior = _finite_monotonic(prior, "saved bounded auxiliary recovery deadline")
            if candidate is not None:
                state["bounded_recovery_deadline_monotonic"] = (candidate if prior is None
                    else min(prior, candidate))
            return state.get("bounded_recovery_deadline_monotonic")
        return self._mutate(pin)

    def register_absent(self, owner: dict[str, str], *, baseline_sha256: str,
                        activity_sha256: str, deadline_monotonic: float) -> None:
        key = owner_key(owner)
        expected = {owner_key(item) for item in self.spec["owners"]}
        if (key not in expected or not is_sha256(baseline_sha256) or not is_sha256(activity_sha256)
                or isinstance(deadline_monotonic, bool) or not isinstance(deadline_monotonic, (int, float))
                or not math.isfinite(deadline_monotonic) or deadline_monotonic <= 0):
            raise ValueError("Azure auxiliary cleanup parent identity or deadline is invalid")
        def apply(state: dict[str, Any]) -> None:
            prior = state["owners"].get(key)
            value = {"state": "parent_absent", "baseline_sha256": baseline_sha256,
                "activity_sha256": activity_sha256, "deadline_monotonic": float(deadline_monotonic)}
            if prior is not None and prior != value:
                raise ValueError("Azure auxiliary cleanup owner evidence conflicts with its first observation")
            state["owners"][key] = value
        self._mutate(apply)

    def record_precreation_baseline(self, owner: dict[str, str], baseline: dict[str, Any]) -> None:
        key = owner_key(owner)
        expected = {owner_key(item) for item in self.spec["owners"]}
        if key not in expected or not isinstance(baseline, dict):
            raise ValueError("Azure auxiliary cleanup baseline owner identity is invalid")
        _validate_precreation_baseline(baseline, owner, self.spec)
        def apply(state: dict[str, Any]) -> None:
            prior = state.get("precreation_baseline")
            observed = {"owner_key": key, "captured_at_utc": baseline["capture_started_at_utc"],
                "baseline": baseline, "baseline_sha256": baseline["sha256"]}
            if prior is None:
                state["precreation_baseline"] = observed
        self._mutate(apply)

    def precreation_baseline(self) -> dict[str, Any] | None:
        state = self.snapshot()
        row = state.get("precreation_baseline")
        if row is None:
            return None
        if not isinstance(row, dict) or set(row) != {"owner_key", "captured_at_utc", "baseline", "baseline_sha256"}:
            raise ValueError("Azure auxiliary cleanup stored cohort baseline is malformed")
        if not isinstance(row.get("baseline"), dict):
            raise ValueError("Azure auxiliary cleanup stored cohort baseline payload is malformed")
        owner = next((item for item in self.spec["owners"] if owner_key(item) == row["owner_key"]), None)
        if owner is None or row["baseline_sha256"] != row["baseline"].get("sha256"):
            raise ValueError("Azure auxiliary cleanup stored cohort baseline owner or digest differs")
        _validate_precreation_baseline(row["baseline"], owner, self.spec)
        return row["baseline"]

    def earliest_deadline(self) -> float:
        state = self.snapshot()
        expected = {owner_key(item) for item in self.spec["owners"]}
        if set(state.get("owners", {})) != expected:
            raise RuntimeError("Azure auxiliary cleanup cohort has not registered every original owner deadline")
        return min(_finite_monotonic(state["owners"][key].get("deadline_monotonic"),
            "original owner cleanup deadline") for key in expected)

    def snapshot(self) -> dict[str, Any]:
        def read(state: dict[str, Any]) -> dict[str, Any]:
            return json.loads(json.dumps(state))
        return self._mutate(read)

    def record_group_only_recovery_receipt(self, receipt: dict[str, Any]) -> None:
        """Persist group-only completion without rewriting the old leaf claim or deadline."""
        required = {"schema_version", "spec_sha256", "claim_id", "source_entry_id", "leaf_receipt_sha256",
            "group_id", "group_delete_action_id", "group_absence_action_id", "group_absent",
            "occurred_at_utc", "operation_deadline_utc", "receipt_sha256"}
        if (not isinstance(receipt, dict) or set(receipt) != required
                or receipt.get("schema_version") != 1 or receipt.get("spec_sha256") != self.digest
                or not is_sha256(receipt.get("claim_id"))
                or not is_sha256(receipt.get("leaf_receipt_sha256"))
                or not isinstance(receipt.get("source_entry_id"), str) or not receipt["source_entry_id"]
                or not isinstance(receipt.get("group_id"), str)
                or receipt["group_id"].lower() != self.spec["resource_group_id"].lower()
                or not all(isinstance(receipt.get(key), str) and receipt[key]
                    for key in ("group_delete_action_id", "group_absence_action_id"))
                or receipt.get("group_absent") is not True
                or receipt.get("source_entry_id") not in {owner["entry_id"] for owner in self.spec["owners"]}
                or not isinstance(receipt.get("occurred_at_utc"), str)
                or not isinstance(receipt.get("operation_deadline_utc"), str)
                or receipt.get("receipt_sha256") != hashlib.sha256(canonical(
                    {key: value for key, value in receipt.items() if key != "receipt_sha256"})).hexdigest()):
            raise ValueError("Azure auxiliary group-only receipt is malformed or foreign")
        occurred = datetime.fromisoformat(receipt["occurred_at_utc"].replace("Z", "+00:00"))
        deadline = datetime.fromisoformat(receipt["operation_deadline_utc"].replace("Z", "+00:00"))
        if (occurred.tzinfo is None or occurred.utcoffset() is None or deadline.tzinfo is None
                or deadline.utcoffset() is None or occurred > deadline):
            raise ValueError("Azure auxiliary group-only receipt timestamps are invalid or late")
        def apply(state: dict[str, Any]) -> None:
            claim = state.get("claim")
            if (not isinstance(claim, dict) or claim.get("claim_id") != receipt["claim_id"]
                    or claim.get("state") != "claimed" or state.get("outcomes") != []):
                raise ValueError("Azure auxiliary group-only receipt does not match the untouched leaf claim")
            intent = state.get("group_only_recovery_intent")
            if (not isinstance(intent, dict) or intent.get("group_delete_action_id") != receipt["group_delete_action_id"]
                    or intent.get("source_entry_id") != receipt["source_entry_id"]
                    or intent.get("leaf_receipt_sha256") != receipt["leaf_receipt_sha256"]
                    or intent.get("group_id", "").lower() != receipt["group_id"].lower()
                    or intent.get("operation_deadline_utc") != receipt["operation_deadline_utc"]):
                raise ValueError("Azure auxiliary group-only receipt lacks its durable delete intent")
            prior = state.get("group_only_recovery_receipt")
            if prior is not None and prior != receipt:
                raise ValueError("Azure auxiliary group-only receipt conflicts with its first completion")
            state["group_only_recovery_receipt"] = receipt
        self._mutate(apply)

    def record_group_only_recovery_intent(self, intent: dict[str, Any]) -> None:
        required = {"schema_version", "spec_sha256", "claim_id", "source_entry_id",
            "leaf_receipt_sha256", "group_id", "group_delete_action_id", "started_at_utc",
            "operation_deadline_utc", "intent_sha256"}
        if (not isinstance(intent, dict) or set(intent) != required
                or intent.get("schema_version") != 1 or intent.get("spec_sha256") != self.digest
                or not is_sha256(intent.get("claim_id")) or not is_sha256(intent.get("leaf_receipt_sha256"))
                or not isinstance(intent.get("source_entry_id"), str) or not intent["source_entry_id"]
                or intent["source_entry_id"] not in {owner["entry_id"] for owner in self.spec["owners"]}
                or not isinstance(intent.get("group_id"), str)
                or intent["group_id"].lower() != self.spec["resource_group_id"].lower()
                or not isinstance(intent.get("group_delete_action_id"), str)
                or not intent["group_delete_action_id"].startswith("azure-")
                or not isinstance(intent.get("started_at_utc"), str)
                or not isinstance(intent.get("operation_deadline_utc"), str)
                or intent.get("intent_sha256") != hashlib.sha256(canonical(
                    {key: value for key, value in intent.items() if key != "intent_sha256"})).hexdigest()):
            raise ValueError("Azure auxiliary group-only intent is malformed or foreign")
        started = datetime.fromisoformat(intent["started_at_utc"].replace("Z", "+00:00"))
        deadline = datetime.fromisoformat(intent["operation_deadline_utc"].replace("Z", "+00:00"))
        if (started.tzinfo is None or started.utcoffset() is None or deadline.tzinfo is None
                or deadline.utcoffset() is None or started >= deadline):
            raise ValueError("Azure auxiliary group-only intent timestamps are invalid")
        def apply(state: dict[str, Any]) -> None:
            claim = state.get("claim")
            if (not isinstance(claim, dict) or claim.get("claim_id") != intent["claim_id"]
                    or claim.get("state") != "claimed" or state.get("outcomes") != []):
                raise ValueError("Azure auxiliary group-only intent does not match the untouched leaf claim")
            prior = state.get("group_only_recovery_intent")
            if prior is not None:
                raise ValueError("Azure auxiliary group-only delete intent already exists; DELETE cannot be retried")
            state["group_only_recovery_intent"] = intent
        self._mutate(apply)

    def claim_leaf(self, *, now_monotonic: float,
                   bounded_recovery_deadline_monotonic: float | None = None) -> str | None:
        _finite_monotonic(now_monotonic, "cleanup claim clock")
        recovery_deadline = self._pin_bounded_recovery_deadline(bounded_recovery_deadline_monotonic)
        expected = {owner_key(item) for item in self.spec["owners"]}
        def apply(state: dict[str, Any]) -> str | None:
            saved_deadline = state.get("bounded_recovery_deadline_monotonic")
            effective_deadline = (min(recovery_deadline, float(saved_deadline))
                if recovery_deadline is not None and saved_deadline is not None
                else recovery_deadline if recovery_deadline is not None else saved_deadline)
            if state["claim"] is not None:
                claim = state["claim"]
                return claim.get("claim_id") if claim.get("state") == "leaf_absent" else None
            if set(state["owners"]) != expected:
                raise RuntimeError("Azure auxiliary cleanup cohort still has an active or unproven peer parent")
            deadlines = [float(state["owners"][key]["deadline_monotonic"]) for key in expected]
            original_deadline = min(deadlines)
            if (now_monotonic >= effective_deadline if effective_deadline is not None
                    else now_monotonic >= original_deadline):
                raise TimeoutError("Azure auxiliary cleanup reached the original peer cleanup deadline")
            claim = hashlib.sha256(canonical([self.digest, sorted(expected)])).hexdigest()
            state["claim"] = {"action": "leaf_delete", "claim_id": claim,
                "state": "claimed", "claimed_at_monotonic": now_monotonic,
                "late_recovery": now_monotonic >= original_deadline}
            if effective_deadline is not None:
                state["claim"]["bounded_recovery_deadline_monotonic"] = effective_deadline
            return claim
        return self._mutate(apply)

    def claim_group(self, claim_id: str, *, now_monotonic: float,
                    bounded_recovery_deadline_monotonic: float | None = None) -> bool:
        _finite_monotonic(now_monotonic, "cleanup claim clock")
        recovery_deadline = self._pin_bounded_recovery_deadline(bounded_recovery_deadline_monotonic)
        def apply(state: dict[str, Any]) -> bool:
            claim = state.get("claim")
            if not isinstance(claim, dict) or claim.get("claim_id") != claim_id:
                raise ValueError("Azure auxiliary cleanup group action does not own the durable claim")
            if claim.get("state") == "leaf_absent":
                original_deadline = min(float(row["deadline_monotonic"]) for row in state["owners"].values())
                saved_values = [value for value in (recovery_deadline,
                    claim.get("bounded_recovery_deadline_monotonic"),
                    state.get("bounded_recovery_deadline_monotonic")) if value is not None]
                saved_deadline = min(float(value) for value in saved_values) if saved_values else None
                if (now_monotonic >= saved_deadline if saved_deadline is not None
                        else now_monotonic >= original_deadline):
                    raise TimeoutError("Azure auxiliary cleanup reached the original peer cleanup deadline")
                if now_monotonic < state["outcomes"][-1].get("recorded_at_monotonic", 0):
                    raise ValueError("Azure auxiliary cleanup group claim clock moved backwards")
                claim["state"] = "group_claimed"
                claim["group_claimed_at_monotonic"] = now_monotonic
                return True
            return False
        return self._mutate(apply)

    def wait_all_absent(self, *, owner: dict[str, str], baseline_sha256: str,
                        activity_sha256: str,
                        deadline_monotonic: float,
                        bounded_recovery_deadline_monotonic: float | None = None,
                        clock: Callable[[], float] = time.monotonic,
                        sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
        _finite_monotonic(deadline_monotonic, "cleanup deadline")
        recovery_deadline = self._pin_bounded_recovery_deadline(bounded_recovery_deadline_monotonic)
        self.register_absent(owner, baseline_sha256=baseline_sha256,
            activity_sha256=activity_sha256, deadline_monotonic=deadline_monotonic)
        expected = {owner_key(item) for item in self.spec["owners"]}
        while True:
            snapshot = self.snapshot()
            now = _finite_monotonic(clock(), "cleanup barrier clock")
            if snapshot.get("claim") is not None:
                return snapshot
            if set(snapshot["owners"]) == expected:
                return snapshot
            saved_deadline = snapshot.get("bounded_recovery_deadline_monotonic")
            wait_deadline = (min(float(recovery_deadline), float(saved_deadline))
                if recovery_deadline is not None and saved_deadline is not None else
                float(recovery_deadline) if recovery_deadline is not None else deadline_monotonic)
            remaining = min(wait_deadline - now,
                min((float(row["deadline_monotonic"]) - now
                     for row in snapshot["owners"].values()), default=wait_deadline-now)
                if recovery_deadline is None else wait_deadline-now)
            if remaining <= 0:
                raise TimeoutError("Azure auxiliary cleanup peer absence barrier reached the original cleanup deadline")
            sleep(min(0.1, remaining))

    def wait_complete(self, *, deadline_monotonic: float,
                      bounded_recovery_deadline_monotonic: float | None = None,
                      clock: Callable[[], float] = time.monotonic,
                      sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
        _finite_monotonic(deadline_monotonic, "cleanup deadline")
        recovery_deadline = self._pin_bounded_recovery_deadline(bounded_recovery_deadline_monotonic)
        while True:
            snapshot = self.snapshot()
            claim = snapshot.get("claim")
            now = _finite_monotonic(clock(), "cleanup completion clock")
            owner_deadline = min((float(row["deadline_monotonic"])
                for row in snapshot.get("owners", {}).values()), default=deadline_monotonic)
            saved_values = [value for value in (recovery_deadline,
                snapshot.get("bounded_recovery_deadline_monotonic"),
                claim.get("bounded_recovery_deadline_monotonic") if isinstance(claim, dict) else None)
                if value is not None]
            wait_deadline = min(float(value) for value in saved_values) if saved_values else min(deadline_monotonic, owner_deadline)
            if now >= wait_deadline:
                raise TimeoutError("Azure auxiliary cleanup completion exceeded the original peer cleanup deadline")
            if isinstance(claim, dict) and claim.get("state") == "complete":
                completed = _finite_monotonic(claim.get("completed_at_monotonic"),
                    "durable cleanup completion clock")
                if now < completed:
                    raise ValueError("Azure auxiliary cleanup observation clock moved backwards after completion")
                return snapshot
            if isinstance(claim, dict) and claim.get("state") in {"failed", "unknown"}:
                raise RuntimeError("Azure auxiliary cleanup action claim is terminal: " + claim["state"])
            remaining = wait_deadline - now
            if remaining <= 0:
                raise TimeoutError("Azure auxiliary cleanup action did not complete within the original deadline")
            sleep(min(0.1, remaining))

    def record_outcome(self, claim_id: str, *, action: str, identity: str,
                       outcome: str, evidence_sha256: str,
                       now_monotonic: float,
                       typed_404_identity: str | None = None,
                       bounded_recovery_deadline_monotonic: float | None = None) -> None:
        now_monotonic = _finite_monotonic(now_monotonic, "cleanup outcome clock")
        recovery_deadline = self._pin_bounded_recovery_deadline(bounded_recovery_deadline_monotonic)
        if action not in {"leaf_delete", "group_delete"} or outcome not in {"succeeded", "failed", "unknown"}:
            raise ValueError("Azure auxiliary cleanup action outcome is invalid")
        if not is_sha256(claim_id) or not is_sha256(evidence_sha256) or not isinstance(identity, str) or not identity:
            raise ValueError("Azure auxiliary cleanup action evidence is malformed")
        expected_identity = self.spec["watcher_id"] if action == "leaf_delete" else self.spec["resource_group_id"]
        if identity.lower() != expected_identity.lower():
            raise ValueError("Azure auxiliary cleanup outcome identity differs from its exact target")
        if action == "leaf_delete" and outcome == "succeeded" and (
                not isinstance(typed_404_identity, str)
                or typed_404_identity.lower() != self.spec["watcher_id"].lower()):
            raise ValueError("successful watcher deletion requires the exact typed 404 resource identity")
        def apply(state: dict[str, Any]) -> None:
            claim = state.get("claim")
            if not isinstance(claim, dict) or claim.get("claim_id") != claim_id:
                raise ValueError("Azure auxiliary cleanup action does not own the durable claim")
            if claim.get("state") in {"failed", "unknown", "complete"}:
                raise RuntimeError("Azure auxiliary cleanup claim is terminal and cannot be retried")
            expected_action = {"claimed": "leaf_delete", "group_claimed": "group_delete"}.get(claim.get("state"))
            if action != expected_action:
                raise ValueError("Azure auxiliary cleanup action is out of order")
            earliest_action_clock = (claim.get("claimed_at_monotonic") if action == "leaf_delete"
                else max(claim.get("group_claimed_at_monotonic", 0),
                    state["outcomes"][-1].get("recorded_at_monotonic", 0)) if state["outcomes"] else None)
            if (isinstance(earliest_action_clock, bool) or not isinstance(earliest_action_clock, (int, float))
                    or now_monotonic < earliest_action_clock):
                raise ValueError("Azure auxiliary cleanup outcome clock moved backwards")
            earliest_deadline = min(float(row["deadline_monotonic"]) for row in state["owners"].values())
            saved_values = [value for value in (recovery_deadline,
                claim.get("bounded_recovery_deadline_monotonic"),
                state.get("bounded_recovery_deadline_monotonic")) if value is not None]
            saved_deadline = min(float(value) for value in saved_values) if saved_values else None
            if (now_monotonic >= saved_deadline if saved_deadline is not None
                    else now_monotonic >= earliest_deadline):
                raise TimeoutError("Azure auxiliary cleanup outcome exceeded the original peer cleanup deadline")
            row = {"action": action, "identity": identity, "outcome": outcome,
                "evidence_sha256": evidence_sha256, "recorded_at_monotonic": now_monotonic}
            if typed_404_identity is not None:
                row["typed_404_identity"] = typed_404_identity
            state["outcomes"].append(row)
            if outcome != "succeeded":
                claim["state"] = outcome
            elif action == "leaf_delete":
                claim["state"] = "leaf_absent"
            else:
                claim["state"] = "complete"
                claim["completed_at_monotonic"] = now_monotonic
        self._mutate(apply)


def owner_key(owner: dict[str, str]) -> str:
    required = {"entry_id", "run_id", "campaign_id", "deployment_id", "config_sha256", "resource_group_id", "vm_ids", "nic_ids"}
    if not isinstance(owner, dict) or set(owner) != required:
        raise ValueError("Azure auxiliary cleanup owner fields are malformed")
    if (any(not isinstance(owner[key], str) or not owner[key] for key in required - {"vm_ids", "nic_ids"})
            or not is_sha256(owner["config_sha256"])
            or not isinstance(owner["vm_ids"], list) or len(owner["vm_ids"]) != 2
            or not isinstance(owner["nic_ids"], list) or len(owner["nic_ids"]) != 2
            or any(not isinstance(value, str) or not value for value in owner["vm_ids"] + owner["nic_ids"])):
        raise ValueError("Azure auxiliary cleanup owner identity is invalid")
    if len(set(owner["vm_ids"])) != 2 or len(set(owner["nic_ids"])) != 2:
        raise ValueError("Azure auxiliary cleanup VM/NIC identities must be unique")
    suffix = hashlib.sha256(f"{owner['campaign_id']}\0{owner['deployment_id']}".encode()).hexdigest()[:8]
    stem = "-".join(("cnb", owner["campaign_id"], owner["deployment_id"]))
    import re
    stem = re.sub(r"[^a-z0-9-]", "-", stem.lower()).strip("-")[:36].rstrip("-")
    if not owner["resource_group_id"].lower().endswith(f"/resourcegroups/{stem}-{suffix}".lower()):
        raise ValueError("Azure auxiliary cleanup owner group is not derivable from its exact campaign/deployment identity")
    group = owner["resource_group_id"].rstrip("/")
    expected_vms = [f"{group}/providers/Microsoft.Compute/virtualMachines/{stem}-{suffix}-{role}" for role in ("a", "b")]
    expected_nics = [f"{group}/providers/Microsoft.Network/networkInterfaces/{stem}-{suffix}-{role}-nic" for role in ("a", "b")]
    if sorted(item.lower() for item in owner["vm_ids"]) != sorted(item.lower() for item in expected_vms) \
            or sorted(item.lower() for item in owner["nic_ids"]) != sorted(item.lower() for item in expected_nics):
        raise ValueError("Azure auxiliary cleanup VM/NIC IDs do not match their exact owned parent")
    return hashlib.sha256(canonical(owner)).hexdigest()


def is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _finite_monotonic(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{label} must be nonnegative finite monotonic seconds")
    return float(value)


def _validate_precreation_baseline(baseline: dict[str, Any], owner: dict[str, Any], spec: dict[str, Any]) -> None:
    from datetime import datetime, timezone
    expected_profile = "all_cloud_acceptance_v1" if spec["profile"] == "normal_pair" else "all_cloud_calibration_v1"
    try:
        started = datetime.fromisoformat(baseline["capture_started_at_utc"].replace("Z", "+00:00"))
        finished = datetime.fromisoformat(baseline["capture_finished_at_utc"].replace("Z", "+00:00"))
        start_mono = _finite_monotonic(baseline["capture_started_monotonic"], "baseline start clock")
        finish_mono = _finite_monotonic(baseline["capture_finished_monotonic"], "baseline finish clock")
    except (KeyError, AttributeError, TypeError, ValueError) as exc:
        raise ValueError("Azure auxiliary cleanup needs complete valid baseline clock samples") from exc
    if (type(baseline.get("schema_version")) is not int or baseline["schema_version"] != 2
            or baseline.get("subscription_id") != spec["subscription_id"]
            or baseline.get("run_id") != owner["run_id"]
            or baseline.get("deployment_id") != owner["deployment_id"]
            or baseline.get("config_sha256") != owner["config_sha256"]
            or baseline.get("profile") != expected_profile
            or started.tzinfo is None or finished.tzinfo is None
            or started.utcoffset() != timezone.utc.utcoffset(started)
            or finished.utcoffset() != timezone.utc.utcoffset(finished)
            or finished < started or finish_mono < start_mono
            or not isinstance(baseline.get("groups"), list)
            or not isinstance(baseline.get("watchers"), list)
            or not all(isinstance(item, str) for item in baseline["groups"] + baseline["watchers"])
            or not isinstance(baseline.get("resource_inventory"), list)
            or not isinstance(baseline.get("group_inventory"), list)
            or not isinstance(baseline.get("source_action_ids"), list)
            or len(baseline["source_action_ids"]) < 2
            or not all(isinstance(item, str) and item for item in baseline["source_action_ids"])):
        raise ValueError("Azure auxiliary cleanup cohort baseline is incomplete or foreign")
    if len(set(baseline["source_action_ids"])) != len(baseline["source_action_ids"]):
        raise ValueError("Azure auxiliary cleanup baseline source actions are duplicated")
    if (any(not isinstance(item, dict) or set(item) != {"id", "type"}
            or not isinstance(item.get("id"), str) or not item["id"]
            or not isinstance(item.get("type"), str) or not item["type"]
            for item in baseline["resource_inventory"])
            or any(not isinstance(item, dict) or set(item) != {"id", "name"}
                or not isinstance(item.get("id"), str) or not item["id"]
                or not isinstance(item.get("name"), str) or not item["name"]
                for item in baseline["group_inventory"])):
        raise ValueError("Azure auxiliary cleanup baseline full inventories are malformed")
    subscription_root = f"/subscriptions/{spec['subscription_id']}/".lower()
    if any(not item["id"].lower().startswith(subscription_root)
            for item in baseline["resource_inventory"] + baseline["group_inventory"]):
        raise ValueError("Azure auxiliary cleanup baseline contains a foreign subscription resource")
    expected_groups = sorted(item["id"].lower() for item in baseline["group_inventory"]
        if item["name"].lower().startswith(("networkwatcher", "newnetworkwatcher")))
    expected_watchers = sorted(item["id"].lower() for item in baseline["resource_inventory"]
        if item["type"].lower() == "microsoft.network/networkwatchers")
    if (baseline["groups"] != expected_groups or baseline["watchers"] != expected_watchers):
        raise ValueError("Azure auxiliary cleanup baseline summary differs from its full inventories")
    inventory = {"resources": baseline["resource_inventory"], "groups": baseline["group_inventory"]}
    inventory_hash = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    baseline_payload = {key: value for key, value in baseline.items() if key != "sha256"}
    baseline_hash = hashlib.sha256(json.dumps(baseline_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if (not is_sha256(baseline.get("inventory_sha256")) or baseline["inventory_sha256"] != inventory_hash
            or not is_sha256(baseline.get("sha256")) or baseline["sha256"] != baseline_hash):
        raise ValueError("Azure auxiliary cleanup cohort baseline inventory or source digest is invalid")


def _regular_source(path: Path) -> bool:
    return (path.is_absolute() and path.is_file()
        and not any(item.is_symlink() for item in (path, *path.parents)))


def validate_spec(spec: dict[str, Any]) -> dict[str, Any]:
    required = {"schema_version", "cohort_id", "state_root", "subscription_id", "region",
        "resource_group_id", "watcher_id", "authorization", "owners", "profile"}
    if not isinstance(spec, dict) or set(spec) != required:
        raise ValueError("Azure auxiliary cleanup runtime binding is malformed")
    if type(spec["schema_version"]) is not int or spec["schema_version"] != 1:
        raise ValueError("Azure auxiliary cleanup schema is unsupported")
    if spec["profile"] not in {"normal_pair", "calibration_self"}:
        raise ValueError("Azure auxiliary cleanup profile is unsupported")
    if (not isinstance(spec["cohort_id"], str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", spec["cohort_id"])
            or spec["cohort_id"] in {".", ".."}):
        raise ValueError("Azure auxiliary cleanup cohort identity must be one safe filename component")
    if not isinstance(spec["state_root"], str) or not Path(spec["state_root"]).is_absolute():
        raise ValueError("Azure auxiliary cleanup state root must be absolute")
    subscription = spec["subscription_id"]
    group = f"/subscriptions/{subscription}/resourceGroups/NetworkWatcherRG"
    watcher = group + "/providers/Microsoft.Network/networkWatchers/NetworkWatcher_swedencentral"
    if (subscription != "188a5848-76fb-4d57-8f2a-32c09284d486" or spec["region"] != "swedencentral"
            or spec["resource_group_id"].lower() != group.lower()
            or spec["watcher_id"].lower() != watcher.lower()):
        raise ValueError("Azure auxiliary cleanup target differs from the exact reviewed Sweden Central identity")
    authorization = spec["authorization"]
    if not isinstance(authorization, dict) or set(authorization) != {
            "card_path", "card_sha256", "cohort_manifest_path", "cohort_manifest_sha256"}:
        raise ValueError("Azure auxiliary cleanup authorization binding is malformed")
    if (any(not isinstance(authorization[key], str) or not Path(authorization[key]).is_absolute()
            for key in ("card_path", "cohort_manifest_path"))
            or not is_sha256(authorization["card_sha256"])
            or not is_sha256(authorization["cohort_manifest_sha256"])):
        raise PermissionError("Azure auxiliary cleanup requires a valid explicit source-bound authorization")
    card = Path(authorization["card_path"])
    if not _regular_source(card) or hashlib.sha256(card.read_bytes()).hexdigest() != authorization["card_sha256"]:
        raise PermissionError("Azure auxiliary cleanup authorization card is missing, changed, or unsafe")
    payload = json.loads(card.read_text(encoding="utf-8"))
    manifest_path = Path(authorization["cohort_manifest_path"])
    manifest_hash = authorization["cohort_manifest_sha256"]
    if not _regular_source(manifest_path) or hashlib.sha256(manifest_path.read_bytes()).hexdigest() != manifest_hash:
        raise PermissionError("Azure auxiliary cleanup cohort manifest is missing, changed, or unsafe")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or set(manifest) != {
            "schema_version", "cohort_id", "profile", "subscription_id", "region",
            "resource_group_id", "watcher_id", "owners", "members"}:
        raise ValueError("Azure auxiliary cleanup cohort manifest is malformed")
    if (manifest["schema_version"] != 1 or manifest["cohort_id"] != spec["cohort_id"]
            or manifest["profile"] != spec["profile"] or manifest["subscription_id"] != subscription
            or manifest["region"] != spec["region"]
            or manifest["resource_group_id"].lower() != group.lower()
            or manifest["watcher_id"].lower() != watcher.lower() or manifest["owners"] != spec["owners"]):
        raise PermissionError("Azure auxiliary cleanup cohort manifest differs from the exact authorized scope")
    members = manifest["members"]
    if not isinstance(members, list) or len(members) != len(spec["owners"]):
        raise ValueError("Azure auxiliary cleanup cohort manifest must bind every owner runtime")
    member_entries = set()
    for member in members:
        required_member = {"entry_id", "run_id", "config_sha256", "runtime_path", "runtime_sha256", "parent_manifest_path",
            "parent_manifest_sha256", "config_path", "source_config_path", "source_config_sha256"}
        if not isinstance(member, dict) or set(member) != required_member:
            raise ValueError("Azure auxiliary cleanup cohort member is malformed")
        owner = next((item for item in spec["owners"] if item["entry_id"] == member["entry_id"]
            and item["run_id"] == member["run_id"]), None)
        if owner is None or member["entry_id"] in member_entries:
            raise ValueError("Azure auxiliary cleanup cohort member is foreign or duplicated")
        if member["config_sha256"] != owner["config_sha256"]:
            raise PermissionError("Azure auxiliary cleanup cohort config hash differs from its owner")
        member_entries.add(member["entry_id"])
        for path_key, hash_key in (("runtime_path", "runtime_sha256"),
                ("parent_manifest_path", "parent_manifest_sha256"),
                ("config_path", "config_sha256"), ("source_config_path", "source_config_sha256")):
            path = Path(member[path_key])
            if not _regular_source(path):
                raise PermissionError("Azure auxiliary cleanup cohort member source is unavailable")
            if hash_key is not None and (not is_sha256(member[hash_key])
                    or hashlib.sha256(path.read_bytes()).hexdigest() != member[hash_key]):
                raise PermissionError("Azure auxiliary cleanup cohort member source hash differs")
    if not isinstance(payload, dict) or set(payload) != {
            "schema_version", "approval_status", "action", "profile", "cohort_id", "subscription_id", "region",
            "resource_group_id", "watcher_id", "cohort_manifest_path", "cohort_manifest_sha256", "owners",
            "expected_watcher_create_caller", "expected_owned_write_caller", "maximum_leaf_deletes",
            "maximum_group_deletes", "source_path", "source_sha256", "decision_source_path",
            "decision_source_sha256", "approved_by", "approved_at_utc", "not_before_utc", "not_after_utc"}:
        raise PermissionError("Azure auxiliary cleanup approval card has an unknown or missing field")
    if (type(payload.get("schema_version")) is not int or payload.get("schema_version") != 1
            or payload.get("approval_status") != "approved"
            or payload.get("action") != "delete_exact_auto_created_network_watcher_and_empty_parent"
            or payload.get("profile") != spec["profile"] or payload.get("cohort_id") != spec["cohort_id"]
            or payload.get("subscription_id") != subscription or payload.get("region") != spec["region"]
            or not isinstance(payload.get("resource_group_id"), str)
            or payload["resource_group_id"].lower() != group.lower()
            or not isinstance(payload.get("watcher_id"), str)
            or payload["watcher_id"].lower() != watcher.lower()
            or payload.get("cohort_manifest_path") != str(manifest_path)
            or payload.get("cohort_manifest_sha256") != manifest_hash
            or type(payload.get("maximum_leaf_deletes")) is not int or payload.get("maximum_leaf_deletes") != 1
            or type(payload.get("maximum_group_deletes")) is not int or payload.get("maximum_group_deletes") != 1):
        raise PermissionError("Azure auxiliary cleanup approval card does not bind the exact target, source, and delete cap")
    if (payload.get("owners") != spec["owners"] or payload.get("source_path") != str(manifest_path)
            or payload.get("source_sha256") != manifest_hash or not is_sha256(payload.get("decision_source_sha256"))
            or not isinstance(payload.get("decision_source_path"), str)
            or not isinstance(payload.get("approved_by"), str) or not payload["approved_by"].strip()):
        raise PermissionError("Azure auxiliary cleanup approval card does not bind exact owner and decision sources")
    source = Path(payload["source_path"])
    if not _regular_source(source) or hashlib.sha256(source.read_bytes()).hexdigest() != payload["source_sha256"]:
        raise PermissionError("Azure auxiliary cleanup approval source is missing or changed")
    decision_source = Path(payload["decision_source_path"])
    if (not _regular_source(decision_source)
            or hashlib.sha256(decision_source.read_bytes()).hexdigest() != payload["decision_source_sha256"]):
        raise PermissionError("Azure auxiliary cleanup human decision source is missing or changed")
    if (not isinstance(payload.get("expected_watcher_create_caller"), str)
            or not payload["expected_watcher_create_caller"].strip()
            or not isinstance(payload.get("expected_owned_write_caller"), str)
            or not payload["expected_owned_write_caller"].strip()):
        raise PermissionError("Azure auxiliary cleanup approval must identify exact watcher and owned-write callers")
    for key in ("not_before_utc", "not_after_utc"):
        if not isinstance(payload.get(key), str):
            raise PermissionError("Azure auxiliary cleanup approval validity window is missing")
        from datetime import datetime, timezone
        try:
            parsed = datetime.fromisoformat(payload[key].replace("Z", "+00:00"))
        except ValueError as exc:
            raise PermissionError("Azure auxiliary cleanup approval validity window is malformed") from exc
        if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise PermissionError("Azure auxiliary cleanup approval validity must use UTC")
    starts = datetime.fromisoformat(payload["not_before_utc"].replace("Z", "+00:00"))
    ends = datetime.fromisoformat(payload["not_after_utc"].replace("Z", "+00:00"))
    approved = datetime.fromisoformat(payload["approved_at_utc"].replace("Z", "+00:00"))
    if (approved.tzinfo is None or approved.utcoffset() != timezone.utc.utcoffset(approved)
            or starts.tzinfo is None or ends.tzinfo is None or ends <= starts):
        raise PermissionError("Azure auxiliary cleanup approval validity window must be positive and finite")
    owners = spec["owners"]
    expected_count = 2 if spec["profile"] == "normal_pair" else 1
    if not isinstance(owners, list) or len(owners) != expected_count:
        raise ValueError("Azure auxiliary cleanup owner set has the wrong cardinality")
    for owner in owners:
        owner_key(owner)
        if (owner["run_id"] != owner["campaign_id"] + "-azure"
                or owner["deployment_id"] != owner["run_id"] + "-deployment"
                or not owner["resource_group_id"].lower().startswith(
                    f"/subscriptions/{subscription}/resourcegroups/".lower())
                or any(not item.lower().startswith(owner["resource_group_id"].lower() + "/providers/")
                       for item in owner["vm_ids"] + owner["nic_ids"])):
            raise ValueError("Azure auxiliary cleanup owner IDs differ from the exact bound subscription/deployment")
    if len({owner_key(owner) for owner in owners}) != expected_count:
        raise ValueError("Azure auxiliary cleanup owners are not unique")
    expected_entries = {"V-S1", "V-S3"} if spec["profile"] == "normal_pair" else {"CAL-S1"}
    if {owner["entry_id"] for owner in owners} != expected_entries:
        raise ValueError("Azure auxiliary cleanup owner set is outside the exact normal or CAL scope")
    if member_entries != expected_entries:
        raise PermissionError("Azure auxiliary cleanup cohort manifest omits a frozen owner runtime")
    return dict(spec)


def authorization_payload(spec: dict[str, Any], now_utc: Any) -> dict[str, Any]:
    validate_spec(spec)
    card_path = Path(spec["authorization"]["card_path"])
    card = json.loads(card_path.read_text(encoding="utf-8"))
    from datetime import datetime, timezone
    start = datetime.fromisoformat(card["not_before_utc"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(card["not_after_utc"].replace("Z", "+00:00"))
    approved_at = datetime.fromisoformat(card["approved_at_utc"].replace("Z", "+00:00"))
    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise PermissionError("Azure auxiliary cleanup authorization check needs an aware UTC clock")
    now = now_utc.astimezone(timezone.utc)
    if approved_at.tzinfo is None or approved_at.utcoffset() != timezone.utc.utcoffset(approved_at):
        raise PermissionError("Azure auxiliary cleanup decision timestamp must use UTC")
    if approved_at.astimezone(timezone.utc) > now:
        raise PermissionError("Azure auxiliary cleanup card approval timestamp is in the future")
    if now < start.astimezone(timezone.utc) or now > end.astimezone(timezone.utc):
        raise PermissionError("Azure auxiliary cleanup authorization is outside its finite validity window")
    return card
