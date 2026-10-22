from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path, PurePosixPath
import re
import shlex
import os
import subprocess
import time
from typing import Callable, Dict, List, Optional
from uuid import uuid4

from pydantic import Field, model_validator

from .access import AccessFailureEvidence, ConnectionOutput, RemoteActionEvidence, RemoteDeadline, _action, _execute, _failure, _request
from .contracts.artifacts import ArtifactInput, ArtifactRecord, resolve_artifact_path
from .contracts.common import ArtifactExpectation, CommandOutcome, Evidence, FailureEvidence, StrictModel, StringEvidence, VmRole
from .contracts.execution import CommandRequest, CommandRunner


class RetrievalItem(StrictModel):
    artifact_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,191}$")
    deployment_id: str = Field(min_length=1)
    source_vm: str = Field(min_length=1)
    source_role: str
    source_path: str = Field(min_length=1)
    destination_path: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    unit_id: Optional[str] = None
    expectation: ArtifactExpectation = ArtifactExpectation.REQUIRED
    closed: bool = True
    expected_size_bytes: int = Field(ge=0)
    expected_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    timeout_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def safe_paths(self):
        if not math.isfinite(self.timeout_seconds):
            raise ValueError("item timeout must be finite")
        source = PurePosixPath(self.source_path)
        if (not self.source_path.startswith("/") or source.as_posix() != self.source_path or ".." in source.parts
                or not re.fullmatch(r"/[A-Za-z0-9._/-]+", self.source_path)):
            raise ValueError("remote source path must be normalized absolute POSIX path")
        destination = PurePosixPath(self.destination_path)
        if destination.is_absolute() or destination.as_posix() != self.destination_path or ".." in destination.parts:
            raise ValueError("destination path must be normalized relative POSIX path")
        if self.source_role not in {"vm_a", "vm_b"}:
            raise ValueError("source_role must be VM A or VM B")
        return self


class RetrievalManifest(StrictModel):
    identity: dict
    deployment_id: str = Field(min_length=1)
    remote_workspace: str = Field(min_length=1)
    declarations: List[ArtifactInput] = Field(min_length=1)
    items: List[RetrievalItem] = Field(default_factory=list)
    unavailable_items: Dict[str, dict] = Field(default_factory=dict)
    measurement_ended_at: datetime
    producers_closed_at: datetime
    closure_evidence: str = Field(min_length=1)

    @model_validator(mode="after")
    def inventory_is_closed_and_resolved(self):
        workspace = PurePosixPath(self.remote_workspace)
        if (not self.remote_workspace.startswith("/") or workspace.as_posix() != self.remote_workspace
                or ".." in workspace.parts or not re.fullmatch(r"/[A-Za-z0-9._/-]+", self.remote_workspace)):
            raise ValueError("remote_workspace must be a normalized absolute POSIX path")
        for name in ("measurement_ended_at", "producers_closed_at"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must include timezone")
        if self.producers_closed_at < self.measurement_ended_at:
            raise ValueError("producers must close at or after measurement end")
        if any(not item.closed for item in self.items):
            raise ValueError("all concrete remote artifacts must be closed before retrieval")
        ids = [i.artifact_id for i in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate artifact IDs")
        paths = [i.destination_path for i in self.items]
        if len(paths) != len(set(paths)):
            raise ValueError("duplicate artifact destinations")
        remote_declarations = [d for d in self.declarations if d.producer != "controller"]
        declaration_ids = {d.artifact_id for d in remote_declarations}
        if len(declaration_ids) != len(remote_declarations):
            raise ValueError("duplicate artifact declarations")
        resolved_item_ids: set[str] = set()
        for declaration in remote_declarations:
            matching = [item for item in self.items if item.artifact_id == declaration.artifact_id]
            if declaration.path_template is not None:
                if declaration.path_template.count("{chunk_id}") != 1 or re.sub(r"\{chunk_id\}", "", declaration.path_template).find("{") >= 0 or "}" in re.sub(r"\{chunk_id\}", "", declaration.path_template):
                    raise ValueError("dynamic artifact path must use exactly one {chunk_id} component")
                matching = [item for item in self.items if item.artifact_id.startswith(declaration.artifact_id + "-")]
                for item in matching:
                    if item.unit_id is None or item.artifact_id != f"{declaration.artifact_id}-{item.unit_id}" or declaration.path_template.replace("{chunk_id}", item.unit_id) != item.destination_path:
                        raise ValueError("dynamic artifact path does not resolve to the declared chunk path")
            for item in matching:
                if (item.kind != declaration.kind or item.source_role != declaration.producer
                        or item.unit_id != declaration.unit_id and declaration.path_template is None):
                    raise ValueError("concrete artifact identity does not match its declaration")
                try:
                    PurePosixPath(item.source_path).relative_to(workspace)
                except ValueError as exc:
                    raise ValueError("remote source path must be under the deployment workspace") from exc
                if declaration.path is not None and item.destination_path != declaration.path:
                    raise ValueError("concrete artifact destination does not match its declaration")
            if declaration.artifact_id in self.unavailable_items:
                if matching:
                    raise ValueError("artifact cannot be both present and unavailable")
            elif not matching:
                raise ValueError("every remote declaration must resolve to a concrete item or unavailable entry")
            resolved_item_ids.update(item.artifact_id for item in matching)
        if set(ids) - resolved_item_ids:
            raise ValueError("concrete item does not match a remote artifact declaration")
        if set(self.unavailable_items) - declaration_ids:
            raise ValueError("unavailable item does not match a remote declaration")
        if set(ids) & set(self.unavailable_items):
            raise ValueError("artifact cannot be both present and unavailable")
        for key, value in self.unavailable_items.items():
            if not all(value.get(field) for field in ("role", "path", "reason")):
                raise ValueError(f"unavailable item {key} requires role, path and reason")
        return self


class TransferAttempt(StrictModel):
    artifact_id: str
    attempt: int = Field(ge=1, le=2)
    staging_path: str
    outcome: str
    size_bytes: Optional[int] = None
    sha256: Optional[str] = None
    reason: Optional[str] = None


class RetrievalResult(StrictModel):
    complete: bool
    cleanup_required: bool = True
    actions: List[RemoteActionEvidence] = Field(default_factory=list)
    retrieved: List[str] = Field(default_factory=list)
    integrity: dict[str, dict[str, int | str]] = Field(default_factory=dict)
    records: List[ArtifactRecord] = Field(default_factory=list)
    failures: List[dict] = Field(default_factory=list)
    attempts: List[TransferAttempt] = Field(default_factory=list)
    failure: AccessFailureEvidence | None = None


def _inventory_script(source_path: str) -> str:
    """Return a POSIX-shell observation for a closed regular file."""
    path = shlex.quote(source_path)
    ancestors = " && ".join(f"[ ! -L {shlex.quote(str(parent))} ]" for parent in reversed(PurePosixPath(source_path).parents) if str(parent) != "/")
    return (f"if {ancestors} && [ -f {path} ] && [ ! -L {path} ]; then "
            f"size=$(wc -c < {path}) && hash=$(sha256sum {path}) && "
            "set -- $hash && printf '%s %s\\n' \"$size\" \"$1\"; else exit 1; fi")


def _failure_record(item: RetrievalItem, category: str, message: str, action_id: str | None = None) -> FailureEvidence:
    return _evidence_failure(item, category, message, action_id)


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _evidence_failure(item: RetrievalItem, category: str, message: str, action_id: str | None = None) -> FailureEvidence:
    return FailureEvidence(category=category, message=message, action_id=action_id, occurred_at=datetime.now(timezone.utc), details={"artifact_id": item.artifact_id})


def run_retrieval(connection: ConnectionOutput, manifest: RetrievalManifest, runner: CommandRunner, filesystem: Path, *, deadline: RemoteDeadline, clock: Callable[[], float] = time.monotonic) -> RetrievalResult:
    if manifest.deployment_id != connection.deployment_id or manifest.identity != connection.identity.model_dump():
        raise ValueError("retrieval manifest identity does not match connection")
    child_root = filesystem.resolve()
    actions: List[RemoteActionEvidence] = []
    retrieved: List[str] = []
    integrity: dict[str, dict[str, int | str]] = {}
    records: List[ArtifactRecord] = []
    failures: List[dict] = []
    attempts: List[TransferAttempt] = []
    primary_failure: AccessFailureEvidence | None = None
    stop = False
    required_remote = {d.artifact_id for d in manifest.declarations if d.producer != "controller" and d.expectation == ArtifactExpectation.REQUIRED}
    declarations = {d.artifact_id: d for d in manifest.declarations}
    # Validate every source identity and every destination before the first command/copy.
    preflight_errors: dict[str, str] = {}
    destination_paths: dict[str, Path] = {}
    for item in manifest.items:
        vm = connection.vm_a if item.source_role == "vm_a" else connection.vm_b
        try:
            if vm.host != item.source_vm or item.deployment_id != connection.deployment_id:
                raise ValueError("retrieval source identity does not match VM role/deployment")
            resolved = resolve_artifact_path(child_root, item.destination_path)
            target = child_root / item.destination_path
            if any(part.is_symlink() for part in [target, *target.parents] if part != child_root.parent):
                raise ValueError("destination path contains a symlink")
            if resolved.exists() or resolved.is_symlink():
                raise ValueError("destination collision")
            destination_paths[item.artifact_id] = resolved
        except (ValueError, OSError) as exc:
            preflight_errors[item.artifact_id] = str(exc)
    entries = list(destination_paths.items())
    for index, (artifact_id, path) in enumerate(entries):
        for other_id, other in entries[index + 1:]:
            if path in other.parents or other in path.parents:
                preflight_errors.setdefault(artifact_id, "conflicting ancestor destination")
                preflight_errors.setdefault(other_id, "conflicting ancestor destination")
    staging_root = child_root / ".f02r-attempts"
    if staging_root.is_symlink():
        for item in manifest.items:
            preflight_errors.setdefault(item.artifact_id, "attempt staging root must not be a symlink")
    for artifact_id, missing in manifest.unavailable_items.items():
        failures.append({"artifact_id": artifact_id, "reason": missing.get("reason", "unavailable")})
        if artifact_id in required_remote:
            primary_failure = primary_failure or AccessFailureEvidence(stage="retrieval", category="required_artifact_unavailable", message=f"required artifact {artifact_id} is unavailable", occurred_at=datetime.now(timezone.utc), details=missing)
        declaration = next(d for d in manifest.declarations if d.artifact_id == artifact_id)
        state = "failed" if artifact_id in required_remote else "not_retrieved"
        failure_record = FailureEvidence(category="artifact_unavailable", message=missing["reason"],
            occurred_at=datetime.now(timezone.utc), details={"artifact_id": artifact_id,
            "role": missing["role"], "path": missing["path"]}) if state == "failed" else None
        records.append(ArtifactRecord(artifact_id=artifact_id, path=declaration.path or declaration.path_template,
            kind=declaration.kind, producer=declaration.producer, unit_id=declaration.unit_id,
            deployment_id=connection.deployment_id, closed=False,
            size_bytes=Evidence[int](unavailable_reason="artifact unavailable"),
            sha256=StringEvidence(unavailable_reason="artifact unavailable"), retrieval_state=state,
            failure=failure_record))
    if preflight_errors:
        for item in manifest.items:
            vm = connection.vm_a if item.source_role == "vm_a" else connection.vm_b
            message = preflight_errors.get(item.artifact_id, "another inventory item failed whole-inventory preflight")
            category = "destination_invalid" if item.artifact_id in preflight_errors else "inventory_preflight_failed"
            failure = _evidence_failure(item, category, message)
            failures.append(failure.model_dump(mode="json"))
            primary_failure = primary_failure or AccessFailureEvidence(stage="retrieval", vm_id=vm.host, role=item.source_role, category=category, message=message, source_path=item.source_path, occurred_at=datetime.now(timezone.utc))
            records.append(ArtifactRecord(artifact_id=item.artifact_id, path=item.destination_path, kind=item.kind, producer=item.source_role, unit_id=item.unit_id, deployment_id=item.deployment_id, closed=True, size_bytes=Evidence[int](unavailable_reason="whole inventory preflight failed"), sha256=StringEvidence(unavailable_reason="whole inventory preflight failed"), retrieval_state="failed", failure=failure))
        return RetrievalResult(complete=False, cleanup_required=True, actions=actions, retrieved=retrieved, integrity=integrity, records=records, failures=failures, attempts=attempts, failure=primary_failure)
    for item in manifest.items:
        vm = connection.vm_a if item.source_role == "vm_a" else connection.vm_b
        if item.artifact_id in preflight_errors:
            message = preflight_errors[item.artifact_id]
            failure = _evidence_failure(item, "destination_invalid", message)
            failures.append(failure.model_dump(mode="json"))
            primary_failure = primary_failure or AccessFailureEvidence(stage="retrieval", vm_id=vm.host, role=item.source_role, category="destination_invalid", message=message, source_path=item.source_path, occurred_at=datetime.now(timezone.utc))
            records.append(ArtifactRecord(artifact_id=item.artifact_id, path=item.destination_path, kind=item.kind, producer=item.source_role, unit_id=item.unit_id, deployment_id=item.deployment_id, closed=item.closed, size_bytes=Evidence[int](unavailable_reason="transfer not verified"), sha256=StringEvidence(unavailable_reason="transfer not verified"), retrieval_state="failed", failure=failure))
            continue
        final_path = child_root / item.destination_path
        if stop:
            reason = "not_attempted_after_stop"
            failure = _evidence_failure(item, reason, "item was not attempted after interruption or budget exhaustion")
            failures.append(failure.model_dump(mode="json"))
            records.append(ArtifactRecord(artifact_id=item.artifact_id, path=item.destination_path, kind=item.kind, producer=item.source_role, unit_id=item.unit_id, deployment_id=item.deployment_id, closed=True, size_bytes=Evidence[int](unavailable_reason=reason), sha256=StringEvidence(unavailable_reason=reason), retrieval_state="failed", failure=failure))
            continue
        remaining = deadline.remaining(clock, cleanup=True)
        if remaining <= 0:
            failure = _evidence_failure(item, "cleanup_allowance_reached", "copy stopped to preserve cleanup allowance")
            failures.append(failure.model_dump(mode="json"))
            primary_failure = primary_failure or AccessFailureEvidence(stage="retrieval", vm_id=vm.host, role=item.source_role, category="cleanup_allowance_reached", message="copy stopped to preserve cleanup allowance", source_path=item.source_path, occurred_at=datetime.now(timezone.utc))
            failure_record = _evidence_failure(item, "cleanup_allowance_reached", "copy stopped to preserve cleanup allowance")
            records.append(ArtifactRecord(artifact_id=item.artifact_id, path=item.destination_path, kind=item.kind, producer=item.source_role, unit_id=item.unit_id, deployment_id=item.deployment_id, closed=True, size_bytes=Evidence[int](unavailable_reason="not copied"), sha256=StringEvidence(unavailable_reason="not copied"), retrieval_state="failed", failure=failure_record))
            stop = True
            continue
        # Verify remote closed-file observations at the source before SCP.
        observe_script = _inventory_script(item.source_path)
        obs_request = _request(f"f02r-retrieve-observe-{item.artifact_id}", vm, ["sh", "-c", observe_script], min(item.timeout_seconds, connection.timeouts.transfer_seconds, remaining), "ssh")
        try:
            obs_result = _execute(runner, obs_request)
        except Exception as exc:
            failure = _evidence_failure(item, "inventory_command_error", "remote inventory command failed at the runner boundary", obs_request.action_id)
            failures.append(failure.model_dump(mode="json")); primary_failure = primary_failure or AccessFailureEvidence(stage="retrieval", vm_id=vm.host, role=item.source_role, category="inventory_command_error", message=type(exc).__name__, action_id=obs_request.action_id, source_path=item.source_path, occurred_at=datetime.now(timezone.utc))
            records.append(ArtifactRecord(artifact_id=item.artifact_id, path=item.destination_path, kind=item.kind, producer=item.source_role, unit_id=item.unit_id, deployment_id=item.deployment_id, closed=True, size_bytes=Evidence[int](unavailable_reason="inventory command failed"), sha256=StringEvidence(unavailable_reason="inventory command failed"), retrieval_state="failed", failure=failure))
            continue
        actions.append(_action(obs_request.action_id, "ssh", vm, obs_request, obs_result))
        observation = obs_result.stdout.split() if obs_result.outcome == CommandOutcome.SUCCEEDED else []
        try:
            if len(observation) != 2 or not re.fullmatch(r"[a-f0-9]{64}", observation[1]):
                raise ValueError("malformed inventory output")
            observed_size, observed_hash = int(observation[0]), observation[1]
        except (ValueError, IndexError):
            observed_size, observed_hash = None, None
        if observed_size != item.expected_size_bytes or observed_hash != item.expected_sha256:
            inventory_error = "command runner raised " in obs_result.stderr
            failure = _evidence_failure(item, "inventory_command_error" if inventory_error else "remote_inventory_mismatch", "remote closed-file observation did not match supplied inventory", obs_request.action_id)
            failures.append(failure.model_dump(mode="json"))
            primary_failure = primary_failure or AccessFailureEvidence(stage="retrieval", vm_id=vm.host, role=item.source_role, category="inventory_command_error" if inventory_error else "remote_inventory_mismatch", message="remote closed-file observation did not match inventory", source_path=item.source_path, occurred_at=obs_result.finished_at)
            records.append(ArtifactRecord(artifact_id=item.artifact_id, path=item.destination_path, kind=item.kind, producer=item.source_role, unit_id=item.unit_id, deployment_id=item.deployment_id, closed=True, size_bytes=Evidence[int](unavailable_reason="remote observation mismatch"), sha256=StringEvidence(unavailable_reason="remote observation mismatch"), retrieval_state="failed", failure=failure))
            if obs_result.outcome == CommandOutcome.INTERRUPTED:
                stop = True
            continue
        item_verified = False
        for attempt in (1, 2):
            remaining = deadline.remaining(clock, cleanup=True)
            if remaining <= 0:
                break
            staging = child_root / ".f02r-attempts" / f"{item.artifact_id}-{attempt}-{uuid4().hex}.part"
            try:
                staging.parent.mkdir(parents=True, exist_ok=True)
                if staging.parent.is_symlink() or staging.exists() or staging.is_symlink():
                    raise OSError("unsafe or colliding staging path")
                remote = f"{vm.user}@{vm.host}:{item.source_path}"
                req = _request(f"f02r-retrieve-{item.artifact_id}-{attempt}", vm, [remote, str(staging)], min(item.timeout_seconds, connection.timeouts.transfer_seconds, remaining), "scp")
                result = _execute(runner, req)
            except Exception as exc:
                reason = "staging_or_runner_error"
                attempts.append(TransferAttempt(artifact_id=item.artifact_id, attempt=attempt, staging_path=str(staging.relative_to(child_root)), outcome="failed", reason=reason))
                failure = _evidence_failure(item, reason, "transfer could not be staged or invoked", None)
                failures.append(failure.model_dump(mode="json")); primary_failure = primary_failure or AccessFailureEvidence(stage="retrieval", vm_id=vm.host, role=item.source_role, category=reason, message=type(exc).__name__, source_path=item.source_path, occurred_at=datetime.now(timezone.utc))
                continue
            actions.append(_action(req.action_id, "scp", vm, req, result))
            size = digest = None
            reason = None
            if result.outcome != CommandOutcome.SUCCEEDED:
                reason = f"copy_{result.outcome}"
            elif not staging.is_file() or staging.is_symlink():
                reason = "copy_not_materialized"
            else:
                try:
                    size, digest = _hash_file(staging)
                    if size != item.expected_size_bytes:
                        reason = "size_mismatch"
                    elif digest != item.expected_sha256:
                        reason = "sha256_mismatch"
                except OSError:
                    reason = "staging_read_failed"
            attempts.append(TransferAttempt(artifact_id=item.artifact_id, attempt=attempt, staging_path=str(staging.relative_to(child_root)), outcome="verified" if reason is None else "failed", size_bytes=size, sha256=digest, reason=reason))
            if reason is None:
                try:
                    final_path.parent.mkdir(parents=True, exist_ok=True)
                    if final_path.exists() or final_path.is_symlink() or any(p.is_symlink() for p in final_path.parents if p != child_root.parent):
                        reason = "destination_collision"
                    else:
                        os.link(staging, final_path)
                        staging.unlink()
                except OSError:
                    reason = "publication_failed"
                if reason is None:
                    rec = ArtifactRecord(artifact_id=item.artifact_id, path=item.destination_path, kind=item.kind, producer=item.source_role, unit_id=item.unit_id, deployment_id=item.deployment_id, closed=True, size_bytes=Evidence[int](value=size), sha256=StringEvidence(value=digest), retrieval_state="verified")
                    try:
                        rec.verify_materialized_file(child_root)
                    except (OSError, ValueError):
                        records.append(ArtifactRecord(artifact_id=item.artifact_id, path=item.destination_path, kind=item.kind, producer=item.source_role, unit_id=item.unit_id, deployment_id=item.deployment_id, closed=True, size_bytes=Evidence[int](unavailable_reason="materialized verification failed"), sha256=StringEvidence(unavailable_reason="materialized verification failed"), retrieval_state="failed", failure=_evidence_failure(item, "publication_verification_failed", "published file failed materialized verification")))
                        primary_failure = primary_failure or AccessFailureEvidence(stage="retrieval", vm_id=vm.host, role=item.source_role, category="publication_verification_failed", message="published file failed materialized verification", source_path=item.source_path, occurred_at=datetime.now(timezone.utc))
                        item_verified = True
                        break
                    records.append(rec)
                    retrieved.append(item.artifact_id)
                    integrity[item.artifact_id] = {"bytes": size, "sha256": digest}
                    item_verified = True
                    break
            if reason:
                failure = _evidence_failure(item, reason, f"artifact transfer attempt {attempt} failed", req.action_id)
                failures.append(failure.model_dump(mode="json"))
                primary_failure = primary_failure or AccessFailureEvidence(stage="retrieval", vm_id=vm.host, role=item.source_role, category=reason, message=f"artifact {item.artifact_id} failed transfer verification", source_path=item.source_path, occurred_at=result.finished_at)
            if result.outcome == CommandOutcome.INTERRUPTED:
                stop = True
                break
        if not item_verified:
            failure = _evidence_failure(item, "transfer_incomplete", "artifact was not verified after available attempts")
            records.append(ArtifactRecord(artifact_id=item.artifact_id, path=item.destination_path, kind=item.kind, producer=item.source_role, unit_id=item.unit_id, deployment_id=item.deployment_id, closed=True, size_bytes=Evidence[int](unavailable_reason="no verified transfer"), sha256=StringEvidence(unavailable_reason="no verified transfer"), retrieval_state="failed", failure=failure))
        if not item_verified and deadline.remaining(clock, cleanup=True) <= 0:
            stop = True
    verified_ids = set(retrieved)
    complete = True
    for declaration in manifest.declarations:
        if declaration.artifact_id not in required_remote:
            continue
        concrete = [item.artifact_id for item in manifest.items if item.artifact_id == declaration.artifact_id or (declaration.path_template is not None and item.artifact_id.startswith(declaration.artifact_id + "-"))]
        if declaration.artifact_id in manifest.unavailable_items or not concrete or any(artifact_id not in verified_ids for artifact_id in concrete):
            complete = False
    if any(item.artifact_id in required_remote and item.artifact_id not in verified_ids for item in manifest.items):
        complete = False
    if deadline.remaining(clock, cleanup=True) <= 0:
        complete = False
    return RetrievalResult(complete=complete, cleanup_required=True, actions=actions, retrieved=retrieved, integrity=integrity, records=records, failures=failures, attempts=attempts, failure=primary_failure)
