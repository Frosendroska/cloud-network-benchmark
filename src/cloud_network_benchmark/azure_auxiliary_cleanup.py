"""Fail-closed filesystem coordination for explicitly authorized Azure auxiliaries.

This module only arbitrates an already source-bound cleanup authorization.  It
never discovers provider ownership, grants authority, retries provider calls,
or widens a cleanup deadline.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
import time
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
        os.chmod(root, 0o700)
        self.root = root.resolve(strict=True)
        self.digest = hashlib.sha256(canonical({k: v for k, v in self.spec.items() if k != "state_root"})).hexdigest()
        self.path = self.root / (self.spec["cohort_id"] + ".azure-cleanup.json")
        self.lock = self.root / (self.spec["cohort_id"] + ".azure-cleanup.lock")

    def _mutate(self, callback: Callable[[dict[str, Any]], Any]) -> Any:
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.lock, flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
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
                if self.path.stat().st_mode & 0o077:
                    raise PermissionError("Azure auxiliary cleanup state must remain private")
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

    def register_absent(self, owner: dict[str, str], *, baseline_sha256: str,
                        activity_sha256: str, deadline_monotonic: float) -> None:
        key = owner_key(owner)
        expected = {owner_key(item) for item in self.spec["owners"]}
        if (key not in expected or not is_sha256(baseline_sha256) or not is_sha256(activity_sha256)
                or deadline_monotonic <= 0):
            raise ValueError("Azure auxiliary cleanup parent identity or deadline is invalid")
        def apply(state: dict[str, Any]) -> None:
            prior = state["owners"].get(key)
            value = {"state": "parent_absent", "baseline_sha256": baseline_sha256,
                "activity_sha256": activity_sha256, "deadline_monotonic": float(deadline_monotonic)}
            if prior is not None and prior != value:
                raise ValueError("Azure auxiliary cleanup owner evidence conflicts with its first observation")
            state["owners"][key] = value
        self._mutate(apply)

    def snapshot(self) -> dict[str, Any]:
        def read(state: dict[str, Any]) -> dict[str, Any]:
            return json.loads(json.dumps(state))
        return self._mutate(read)

    def claim_leaf(self, *, now_monotonic: float) -> str | None:
        expected = {owner_key(item) for item in self.spec["owners"]}
        def apply(state: dict[str, Any]) -> str | None:
            if state["claim"] is not None:
                claim = state["claim"]
                return claim.get("claim_id") if claim.get("state") == "leaf_absent" else None
            if set(state["owners"]) != expected:
                raise RuntimeError("Azure auxiliary cleanup cohort still has an active or unproven peer parent")
            deadlines = [float(state["owners"][key]["deadline_monotonic"]) for key in expected]
            if now_monotonic >= min(deadlines):
                raise TimeoutError("Azure auxiliary cleanup reached the original peer cleanup deadline")
            claim = hashlib.sha256(canonical([self.digest, sorted(expected)])).hexdigest()
            state["claim"] = {"action": "leaf_delete", "claim_id": claim,
                "state": "claimed", "claimed_at_monotonic": now_monotonic}
            return claim
        return self._mutate(apply)

    def claim_group(self, claim_id: str, *, now_monotonic: float) -> bool:
        def apply(state: dict[str, Any]) -> bool:
            claim = state.get("claim")
            if not isinstance(claim, dict) or claim.get("claim_id") != claim_id:
                raise ValueError("Azure auxiliary cleanup group action does not own the durable claim")
            if claim.get("state") == "leaf_absent":
                if now_monotonic >= min(float(row["deadline_monotonic"]) for row in state["owners"].values()):
                    raise TimeoutError("Azure auxiliary cleanup reached the original peer cleanup deadline")
                claim["state"] = "group_claimed"
                claim["group_claimed_at_monotonic"] = now_monotonic
                return True
            return False
        return self._mutate(apply)

    def wait_all_absent(self, *, owner: dict[str, str], baseline_sha256: str,
                        activity_sha256: str,
                        deadline_monotonic: float, clock: Callable[[], float] = time.monotonic,
                        sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
        self.register_absent(owner, baseline_sha256=baseline_sha256,
            activity_sha256=activity_sha256, deadline_monotonic=deadline_monotonic)
        expected = {owner_key(item) for item in self.spec["owners"]}
        while True:
            snapshot = self.snapshot()
            if snapshot.get("claim") is not None:
                return snapshot
            if set(snapshot["owners"]) == expected:
                return snapshot
            remaining = min(deadline_monotonic - clock(),
                min((float(row["deadline_monotonic"]) - clock()
                     for row in snapshot["owners"].values()), default=deadline_monotonic-clock()))
            if remaining <= 0:
                raise TimeoutError("Azure auxiliary cleanup peer absence barrier reached the original cleanup deadline")
            sleep(min(0.1, remaining))

    def wait_complete(self, *, deadline_monotonic: float, clock: Callable[[], float] = time.monotonic,
                      sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
        while True:
            snapshot = self.snapshot()
            claim = snapshot.get("claim")
            if isinstance(claim, dict) and claim.get("state") == "complete":
                return snapshot
            if isinstance(claim, dict) and claim.get("state") in {"failed", "unknown"}:
                raise RuntimeError("Azure auxiliary cleanup action claim is terminal: " + claim["state"])
            remaining = deadline_monotonic - clock()
            if remaining <= 0:
                raise TimeoutError("Azure auxiliary cleanup action did not complete within the original deadline")
            sleep(min(0.1, remaining))

    def record_outcome(self, claim_id: str, *, action: str, identity: str,
                       outcome: str, evidence_sha256: str,
                       typed_404_identity: str | None = None) -> None:
        if action not in {"leaf_delete", "group_delete"} or outcome not in {"succeeded", "failed", "unknown"}:
            raise ValueError("Azure auxiliary cleanup action outcome is invalid")
        if not is_sha256(claim_id) or not is_sha256(evidence_sha256) or not isinstance(identity, str) or not identity:
            raise ValueError("Azure auxiliary cleanup action evidence is malformed")
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
            expected_action = "leaf_delete" if claim.get("state") == "claimed" else "group_delete"
            if action != expected_action:
                raise ValueError("Azure auxiliary cleanup action is out of order")
            row = {"action": action, "identity": identity, "outcome": outcome,
                "evidence_sha256": evidence_sha256}
            if typed_404_identity is not None:
                row["typed_404_identity"] = typed_404_identity
            state["outcomes"].append(row)
            if outcome != "succeeded":
                claim["state"] = outcome
            elif action == "leaf_delete":
                claim["state"] = "leaf_absent"
            else:
                claim["state"] = "complete"
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
    if not isinstance(spec["cohort_id"], str) or not spec["cohort_id"]:
        raise ValueError("Azure auxiliary cleanup cohort identity is required")
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
    if not isinstance(authorization, dict) or set(authorization) != {"card_path", "card_sha256", "runtime_path", "runtime_sha256"}:
        raise ValueError("Azure auxiliary cleanup authorization binding is malformed")
    if (not isinstance(authorization["card_path"], str)
            or not Path(authorization["card_path"]).is_absolute()
            or not isinstance(authorization["runtime_path"], str) or not Path(authorization["runtime_path"]).is_absolute()
            or not is_sha256(authorization["card_sha256"]) or not is_sha256(authorization["runtime_sha256"])):
        raise PermissionError("Azure auxiliary cleanup requires a valid explicit source-bound authorization")
    card = Path(authorization["card_path"])
    if not _regular_source(card) or hashlib.sha256(card.read_bytes()).hexdigest() != authorization["card_sha256"]:
        raise PermissionError("Azure auxiliary cleanup authorization card is missing, changed, or unsafe")
    payload = json.loads(card.read_text(encoding="utf-8"))
    runtime_path = Path(authorization["runtime_path"])
    if (not _regular_source(runtime_path)
            or hashlib.sha256(runtime_path.read_bytes()).hexdigest() != authorization["runtime_sha256"]):
        raise PermissionError("Azure auxiliary cleanup runtime source is missing or changed")
    if (not isinstance(payload, dict) or payload.get("schema_version") != 1
            or payload.get("approval_status") != "approved"
            or payload.get("action") != "delete_exact_auto_created_network_watcher_and_empty_parent"
            or payload.get("profile") != spec["profile"]
            or payload.get("runtime_sha256") != authorization["runtime_sha256"]
            or payload.get("subscription_id") != subscription or payload.get("region") != spec["region"]
            or payload.get("resource_group_id", "").lower() != group.lower()
            or payload.get("watcher_id", "").lower() != watcher.lower()
            or payload.get("maximum_leaf_deletes") != 1 or payload.get("maximum_group_deletes") != 1):
        raise PermissionError("Azure auxiliary cleanup approval card does not bind the exact target, source, and delete cap")
    if not is_sha256(payload.get("source_sha256")) or not isinstance(payload.get("source_path"), str):
        raise PermissionError("Azure auxiliary cleanup approval card lacks source identity")
    source = Path(payload["source_path"])
    if not _regular_source(source) or hashlib.sha256(source.read_bytes()).hexdigest() != payload["source_sha256"]:
        raise PermissionError("Azure auxiliary cleanup approval source is missing or changed")
    if not isinstance(payload.get("expected_caller"), str) or not payload["expected_caller"].strip():
        raise PermissionError("Azure auxiliary cleanup approval does not identify the expected Activity Log caller")
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
    if ends <= starts:
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
    if payload.get("owners") != owners:
        raise PermissionError("Azure auxiliary cleanup approval card does not bind the exact parent identities")
    return dict(spec)


def authorization_payload(spec: dict[str, Any], now_utc: Any) -> dict[str, Any]:
    validate_spec(spec)
    card_path = Path(spec["authorization"]["card_path"])
    card = json.loads(card_path.read_text(encoding="utf-8"))
    from datetime import datetime, timezone
    start = datetime.fromisoformat(card["not_before_utc"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(card["not_after_utc"].replace("Z", "+00:00"))
    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise PermissionError("Azure auxiliary cleanup authorization check needs an aware UTC clock")
    now = now_utc.astimezone(timezone.utc)
    if now < start.astimezone(timezone.utc) or now > end.astimezone(timezone.utc):
        raise PermissionError("Azure auxiliary cleanup authorization is outside its finite validity window")
    return card
