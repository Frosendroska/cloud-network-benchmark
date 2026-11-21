from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import gzip
import math
from pathlib import Path, PurePosixPath
import re
import shlex
import os
import shutil
import subprocess
import time
from typing import Callable, Dict, List, Optional
from uuid import uuid4

from pydantic import Field, model_validator

from .access import AccessFailureEvidence, ConnectionOutput, RemoteActionEvidence, RemoteDeadline, _action, _execute as _run_command, _failure, _identity_references_available, _request as _build_request, _request_failure
from .contracts.artifacts import ArtifactInput, ArtifactRecord, resolve_artifact_path
from .contracts.common import ArtifactExpectation, CommandOutcome, Evidence, FailureEvidence, StrictModel, StringEvidence, VmRole
from .contracts.execution import BOUNDED_COPY_EOF_MARKER, CommandRequest, CommandResult, CommandRunner
from .closed_archive import FAT32_FILE_LIMIT


def _request(action_id, connection, argv, timeout, action_type="ssh") -> CommandRequest:
    """Represent local authenticated-request failures without invoking a runner."""
    try:
        return _build_request(action_id, connection, argv, timeout, action_type)
    except Exception:
        safe_timeout = timeout if math.isfinite(timeout) and timeout > 0 else 0.001
        return CommandRequest(action_id=action_id, action_type=action_type,
            argv=["scp" if action_type == "scp" else "ssh", "[request-construction-failed]"], cwd=".",
            environment_classification="contains_secret_references", timeout_seconds=safe_timeout,
            redactions=[connection.authentication_reference, connection.host_key_reference])


def _execute(runner: CommandRunner, request: CommandRequest):
    if request.argv and request.argv[-1] == "[request-construction-failed]":
        now = datetime.now(timezone.utc)
        return CommandResult(action_id=request.action_id, outcome=CommandOutcome.FAILED,
            started_at=now, finished_at=now, duration_seconds=0, exit_code=1, stdout="",
            stderr="request construction failed: authenticated transport preflight", redactions=request.redactions)
    return _run_command(runner, request)


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
    transport_encoding: str = "identity"
    archive_bytes: Optional[int] = None
    archive_sha256: Optional[str] = None


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


def _bounded_stream_script(source_path: str, maximum_bytes: int) -> str:
    """Send at most the charged payload bytes, probing for excess remotely."""
    path = shlex.quote(source_path)
    ancestors = " && ".join(f"[ ! -L {shlex.quote(str(parent))} ]"
        for parent in reversed(PurePosixPath(source_path).parents) if str(parent) != "/")
    return (f"if {ancestors} && [ -f {path} ] && [ ! -L {path} ]; then "
            f"exec 3< {path} || exit 1; "
            f"dd bs=65536 count={maximum_bytes} iflag=count_bytes status=none <&3 || exit 1; "
            "python3 -c 'import os,sys; sys.exit(0 if os.read(3, 1) == b\"\" else 1)' || exit 1; "
            f"printf '{BOUNDED_COPY_EOF_MARKER}\\n' >&2; "
            "else exit 1; fi")


def _bounded_gzip_script(item: RetrievalItem) -> str:
    """Create a deterministic per-file gzip archive on the guest, then stream it.

    Source bytes and digest are rechecked before compression. The temporary
    archive is on the same guest workspace, bounded by the original size, and
    removed by the shell trap. Native source files are never modified/deleted.
    """
    path = shlex.quote(item.source_path)
    folder = shlex.quote(str(PurePosixPath(item.source_path).parent))
    ancestors = " && ".join(f"[ ! -L {shlex.quote(str(parent))} ]"
        for parent in reversed(PurePosixPath(item.source_path).parents) if str(parent) != "/")
    digest = shlex.quote(item.expected_sha256)
    return ("set -eu; "
        f"if {ancestors} && [ -f {path} ] && [ ! -L {path} ]; then :; else exit 1; fi; "
        f"actual_size=$(wc -c < {path}); [ \"$actual_size\" -eq {item.expected_size_bytes} ] || exit 1; "
        f"actual_hash=$(sha256sum {path}); set -- $actual_hash; [ \"$1\" = {digest} ] || exit 1; "
        f"free_kib=$(df -Pk {folder} | awk 'END {{print $4}}'); "
        f"[ \"$free_kib\" -ge {max(1, (item.expected_size_bytes + 1048576 + 1023) // 1024)} ] || exit 1; "
        f"tmp=$(mktemp {folder}/.cnb-archive.XXXXXX); "
        "trap 'rm -f \"$tmp\"' EXIT HUP INT TERM; "
        f"gzip -n -c {path} > \"$tmp\"; size=$(wc -c < \"$tmp\"); "
        f"[ \"$size\" -le {item.expected_size_bytes} ] || exit 1; "
        "archive_hash=$(sha256sum \"$tmp\"); set -- $archive_hash; "
        "printf 'CNB_ARCHIVE %s %s\\n' \"$size\" \"$1\" >&2; "
        f"dd bs=65536 count={item.expected_size_bytes} iflag=count_bytes status=none < \"$tmp\" || exit 1; "
        f"printf '{BOUNDED_COPY_EOF_MARKER}\\n' >&2")


def _decode_gzip_verified(source: Path, destination: Path, item: RetrievalItem) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    try:
        with gzip.open(source, "rb") as compressed, destination.open("xb") as output:
            while block := compressed.read(1024 * 1024):
                size += len(block)
                if size > item.expected_size_bytes:
                    raise ValueError("decompressed archive exceeds the inventoried source size")
                digest.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
    except (OSError, EOFError) as exc:
        destination.unlink(missing_ok=True)
        raise ValueError("compressed archive is corrupt or incomplete") from exc
    if size != item.expected_size_bytes or digest.hexdigest() != item.expected_sha256:
        destination.unlink(missing_ok=True)
        raise ValueError("decompressed payload size or SHA-256 differs from closed source manifest")
    return size, digest.hexdigest()


def _failure_record(item: RetrievalItem, category: str, message: str, action_id: str | None = None) -> FailureEvidence:
    return _evidence_failure(item, category, message, action_id)


class _VerificationStopped(Exception):
    def __init__(self, size: int, sha256: str):
        super().__init__("cleanup allowance reached during streaming local verification")
        self.size = size
        self.sha256 = sha256


def _hash_file(path: Path, *, deadline: RemoteDeadline | None = None, clock: Callable[[], float] | None = None) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
            if deadline is not None and clock is not None and deadline.remaining(clock, cleanup=True) <= 0:
                raise _VerificationStopped(size, digest.hexdigest())
    return size, digest.hexdigest()


def _verification_budget_check(deadline: RemoteDeadline, clock: Callable[[], float]):
    def check(size: int, sha256: str) -> None:
        if deadline.remaining(clock, cleanup=True) <= 0:
            raise _VerificationStopped(size, sha256)
    return check


def _evidence_failure(item: RetrievalItem, category: str, message: str, action_id: str | None = None) -> FailureEvidence:
    return FailureEvidence(category=category, message=message, action_id=action_id, occurred_at=datetime.now(timezone.utc), details={"artifact_id": item.artifact_id})


def run_retrieval(connection: ConnectionOutput, manifest: RetrievalManifest, runner: CommandRunner, filesystem: Path, *, deadline: RemoteDeadline, clock: Callable[[], float] = time.monotonic, max_attempts: int = 2, attempt_offset: int = 0, retry_admission: Callable[[RetrievalItem], bool] | None = None, transfer_admission: Callable[[RetrievalItem, int], bool] | None = None, compressed_transfer: bool = False) -> RetrievalResult:
    # Keep the historical two-attempt default for existing F02 callers. F09
    # supplies an explicit one-retry ceiling and a fresh F11 check immediately
    # before that retry. Validate before path creation or command execution.
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts not in (1, 2):
        raise ValueError("max_attempts must be the strict integer 1 or 2")
    if isinstance(attempt_offset, bool) or not isinstance(attempt_offset, int) or attempt_offset not in (0, 1) or attempt_offset + max_attempts > 2:
        raise ValueError("attempt_offset and max_attempts must keep the total attempt number at or below 2")
    if type(compressed_transfer) is not bool:
        raise ValueError("compressed_transfer must be a strict boolean")
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
        if compressed_transfer and item.expected_size_bytes > FAT32_FILE_LIMIT:
            preflight_errors[item.artifact_id] = "single-file compressed transfer exceeds FAT32-safe bound; archive must be split before copying"
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
        if not _identity_references_available(vm):
            request, result = _request_failure(f"f02r-retrieve-{item.artifact_id}-transport-preflight", vm, item.timeout_seconds, ValueError("credential reference unavailable between artifacts"))
            actions.append(_action(request.action_id, "ssh", vm, request, result))
            failure = _evidence_failure(item, "transport_preflight_failed", "SSH identity reference became unavailable before inventory", request.action_id)
            failures.append(failure.model_dump(mode="json"))
            primary_failure = primary_failure or AccessFailureEvidence(stage="retrieval", vm_id=vm.host, role=item.source_role, category="transport_preflight_failed", message="SSH identity reference became unavailable between artifact transfers", action_id=request.action_id, source_path=item.source_path, occurred_at=result.finished_at)
            records.append(ArtifactRecord(artifact_id=item.artifact_id, path=item.destination_path, kind=item.kind, producer=item.source_role, unit_id=item.unit_id, deployment_id=item.deployment_id, closed=True, size_bytes=Evidence[int](unavailable_reason="inventory not attempted"), sha256=StringEvidence(unavailable_reason="inventory not attempted"), retrieval_state="failed", failure=failure))
            stop = True
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
        if compressed_transfer:
            try:
                # At peak this item has its compressed transfer stage plus the
                # verified raw staging file. Previously published items are
                # already reflected in the current free-space observation.
                required_free = item.expected_size_bytes * 2 + 1024 * 1024
                if shutil.disk_usage(child_root).free < required_free:
                    raise OSError("destination free space is below raw-plus-archive peak headroom")
            except OSError as exc:
                reason = "destination_low_disk"
                failure = _evidence_failure(item, reason, str(exc))
                failures.append(failure.model_dump(mode="json"))
                primary_failure = primary_failure or AccessFailureEvidence(stage="retrieval", vm_id=vm.host,
                    role=item.source_role, category=reason, message="compressed transfer stopped before remote copy",
                    source_path=item.source_path, occurred_at=datetime.now(timezone.utc))
                records.append(ArtifactRecord(artifact_id=item.artifact_id, path=item.destination_path,
                    kind=item.kind, producer=item.source_role, unit_id=item.unit_id,
                    deployment_id=item.deployment_id, closed=True,
                    size_bytes=Evidence[int](unavailable_reason=reason),
                    sha256=StringEvidence(unavailable_reason=reason), retrieval_state="failed", failure=failure))
                stop = True
                continue
        # Reserve the first attempt before even observing this artifact. This
        # also stops inventory/control work once the shared payload cap is gone.
        first_attempt = attempt_offset + 1
        if transfer_admission is not None:
            try:
                charged = transfer_admission(item, first_attempt)
            except Exception:
                charged = False
            if not charged:
                reason = "result_transfer_allowance_exhausted"
                failure = _evidence_failure(item, reason, "durable payload allowance refused this attempt")
                failures.append(failure.model_dump(mode="json"))
                primary_failure = primary_failure or AccessFailureEvidence(stage="retrieval", vm_id=vm.host,
                    role=item.source_role, category=reason, message="result transfer stopped before remote inventory",
                    source_path=item.source_path, occurred_at=datetime.now(timezone.utc))
                records.append(ArtifactRecord(artifact_id=item.artifact_id, path=item.destination_path,
                    kind=item.kind, producer=item.source_role, unit_id=item.unit_id,
                    deployment_id=item.deployment_id, closed=True,
                    size_bytes=Evidence[int](unavailable_reason=reason),
                    sha256=StringEvidence(unavailable_reason=reason), retrieval_state="failed", failure=failure))
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
            inventory_error = ("command runner raised " in obs_result.stderr
                               or "request construction failed" in obs_result.stderr)
            failure = _evidence_failure(item, "inventory_command_error" if inventory_error else "remote_inventory_mismatch", "remote closed-file observation did not match supplied inventory", obs_request.action_id)
            failures.append(failure.model_dump(mode="json"))
            primary_failure = primary_failure or AccessFailureEvidence(stage="retrieval", vm_id=vm.host, role=item.source_role, category="inventory_command_error" if inventory_error else "remote_inventory_mismatch", message="remote closed-file observation did not match inventory", source_path=item.source_path, occurred_at=obs_result.finished_at)
            records.append(ArtifactRecord(artifact_id=item.artifact_id, path=item.destination_path, kind=item.kind, producer=item.source_role, unit_id=item.unit_id, deployment_id=item.deployment_id, closed=True, size_bytes=Evidence[int](unavailable_reason="remote observation mismatch"), sha256=StringEvidence(unavailable_reason="remote observation mismatch"), retrieval_state="failed", failure=failure))
            if obs_result.outcome == CommandOutcome.INTERRUPTED:
                stop = True
            continue
        item_verified = False
        reason = "cleanup_allowance_reached_before_transfer"
        last_attempt = None
        for attempt in range(attempt_offset + 1, attempt_offset + max_attempts + 1):
            if attempt > 1 and retry_admission is not None:
                try:
                    admitted = retry_admission(item)
                except Exception as exc:
                    admitted = False
                    reason = f"retry_admission_error:{type(exc).__name__}"
                if not admitted:
                    reason = reason or "retry_not_admitted"
                    failures.append(_evidence_failure(item, reason, "failed artifact retry was refused by the fresh admission check").model_dump(mode="json"))
                    break
            remaining = deadline.remaining(clock, cleanup=True)
            if remaining <= 0:
                reason = "cleanup_allowance_reached_before_transfer"
                stop = True
                break
            if transfer_admission is not None and attempt != first_attempt:
                try:
                    charged = transfer_admission(item, attempt)
                except Exception:
                    charged = False
                if not charged:
                    reason = "result_transfer_allowance_exhausted"
                    stop = True
                    failure = _evidence_failure(item, reason, "durable payload allowance refused this attempt")
                    failures.append(failure.model_dump(mode="json"))
                    primary_failure = primary_failure or AccessFailureEvidence(stage="retrieval", vm_id=vm.host,
                        role=item.source_role, category=reason, message="result transfer stopped before payload",
                        source_path=item.source_path, occurred_at=datetime.now(timezone.utc))
                    break
            staging = child_root / ".f02r-attempts" / f"{item.artifact_id}-{attempt}-{uuid4().hex}.part"
            try:
                staging.parent.mkdir(parents=True, exist_ok=True)
                if staging.parent.is_symlink() or staging.exists() or staging.is_symlink():
                    raise OSError("unsafe or colliding staging path")
                # Local staging can consume the original copy allowance. Re-read
                # it immediately before constructing the SCP command.
                remaining = deadline.remaining(clock, cleanup=True)
                if remaining <= 0:
                    reason = "cleanup_allowance_reached_before_transfer"
                    stop = True
                    break
                if compressed_transfer:
                    req = _request(f"f02r-retrieve-{item.artifact_id}-{attempt}", vm,
                        ["sh", "-c", _bounded_gzip_script(item)],
                        min(item.timeout_seconds, connection.timeouts.transfer_seconds, remaining), "ssh")
                    if req.argv[-1] != "[request-construction-failed]":
                        req = req.model_copy(update={"argv": [req.argv[0], "-T", *req.argv[1:]]})
                    result = (_execute(runner, req) if req.argv[-1] == "[request-construction-failed]"
                        else runner.run_bounded_copy(req, staging, item.expected_size_bytes))
                elif transfer_admission is None:
                    remote = f"{vm.user}@{vm.host}:{item.source_path}"
                    req = _request(f"f02r-retrieve-{item.artifact_id}-{attempt}", vm,
                        [remote, str(staging)], min(item.timeout_seconds, connection.timeouts.transfer_seconds, remaining), "scp")
                    result = _execute(runner, req)
                else:
                    req = _request(f"f02r-retrieve-{item.artifact_id}-{attempt}", vm,
                        ["sh", "-c", _bounded_stream_script(item.source_path, item.expected_size_bytes)],
                        min(item.timeout_seconds, connection.timeouts.transfer_seconds, remaining), "ssh")
                    if req.argv[-1] != "[request-construction-failed]":
                        req = req.model_copy(update={"argv": [req.argv[0], "-T", *req.argv[1:]]})
                    result = (_execute(runner, req) if req.argv[-1] == "[request-construction-failed]"
                        else runner.run_bounded_copy(req, staging, item.expected_size_bytes))
            except Exception as exc:
                reason = "staging_or_runner_error"
                attempts.append(TransferAttempt(artifact_id=item.artifact_id, attempt=attempt, staging_path=str(staging.relative_to(child_root)), outcome="failed", reason=reason))
                failure = _evidence_failure(item, reason, "transfer could not be staged or invoked", None)
                failures.append(failure.model_dump(mode="json")); primary_failure = primary_failure or AccessFailureEvidence(stage="retrieval", vm_id=vm.host, role=item.source_role, category=reason, message=type(exc).__name__, source_path=item.source_path, occurred_at=datetime.now(timezone.utc))
                continue
            actions.append(_action(req.action_id, req.action_type, vm, req, result))
            size = digest = None
            archive_bytes = archive_digest = None
            reason = None
            if req.argv and req.argv[-1] == "[request-construction-failed]":
                reason = "transport_preflight_failed"
            elif not staging.is_file() or staging.is_symlink():
                reason = f"copy_{result.outcome}" if result.outcome != CommandOutcome.SUCCEEDED else "copy_not_materialized"
            elif compressed_transfer and result.outcome != CommandOutcome.SUCCEEDED:
                reason = f"copy_{result.outcome}"
            else:
                try:
                    if compressed_transfer:
                        archive_bytes, archive_digest = _hash_file(staging, deadline=deadline, clock=clock)
                        proofs = [line.split() for line in result.stderr.splitlines()
                                  if line.startswith("CNB_ARCHIVE ")]
                        if (len(proofs) != 1 or len(proofs[0]) != 3
                                or not proofs[0][1].isdigit()
                                or int(proofs[0][1]) != archive_bytes
                                or proofs[0][2] != archive_digest):
                            raise ValueError("remote archive byte count or SHA-256 proof mismatch")
                        decoded = staging.with_name(staging.name + ".decoded")
                        try:
                            size, digest = _decode_gzip_verified(staging, decoded, item)
                        except ValueError as exc:
                            raise ValueError(f"compressed archive verification failed: {exc}") from exc
                        staging.unlink()
                        os.replace(decoded, staging)
                    size, digest = _hash_file(staging, deadline=deadline, clock=clock)
                    if result.outcome != CommandOutcome.SUCCEEDED:
                        reason = f"copy_{result.outcome}"
                    elif size != item.expected_size_bytes:
                        reason = "size_mismatch"
                    elif digest != item.expected_sha256:
                        reason = "sha256_mismatch"
                    if deadline.remaining(clock, cleanup=True) <= 0:
                        reason = "cleanup_allowance_reached_during_local_verification"
                        stop = True
                except _VerificationStopped as exc:
                    size, digest = exc.size, exc.sha256
                    reason = "cleanup_allowance_reached_during_local_verification"
                    stop = True
                except ValueError:
                    reason = "compressed_archive_integrity_mismatch"
                except OSError:
                    reason = "staging_read_failed"
            attempts.append(TransferAttempt(artifact_id=item.artifact_id, attempt=attempt,
                staging_path=str(staging.relative_to(child_root)), outcome="verified" if reason is None else "failed",
                size_bytes=size, sha256=digest, reason=reason,
                transport_encoding="gzip" if compressed_transfer else "identity",
                archive_bytes=archive_bytes if compressed_transfer else None,
                archive_sha256=archive_digest if compressed_transfer else None))
            if reason == "transport_preflight_failed":
                stop = True
            if reason is None:
                try:
                    final_path.parent.mkdir(parents=True, exist_ok=True)
                    if deadline.remaining(clock, cleanup=True) <= 0:
                        reason = "cleanup_allowance_reached_before_publication"
                        stop = True
                    elif final_path.exists() or final_path.is_symlink() or any(p.is_symlink() for p in final_path.parents if p != child_root.parent):
                        reason = "destination_collision"
                    else:
                        os.link(staging, final_path)
                        staging.unlink()
                        if deadline.remaining(clock, cleanup=True) <= 0:
                            reason = "cleanup_allowance_reached_after_publication"
                            stop = True
                except OSError:
                    reason = "publication_failed"
                if reason is None:
                    rec = ArtifactRecord(artifact_id=item.artifact_id, path=item.destination_path, kind=item.kind, producer=item.source_role, unit_id=item.unit_id, deployment_id=item.deployment_id, closed=True, size_bytes=Evidence[int](value=size), sha256=StringEvidence(value=digest), retrieval_state="verified")
                    if deadline.remaining(clock, cleanup=True) <= 0:
                        reason = "cleanup_allowance_reached_before_publication_verification"
                        failure = _evidence_failure(item, reason, "materialized verification skipped after the caller cleanup allowance")
                        records.append(ArtifactRecord(artifact_id=item.artifact_id, path=item.destination_path, kind=item.kind, producer=item.source_role, unit_id=item.unit_id, deployment_id=item.deployment_id, closed=True,
                            size_bytes=Evidence[int](value=size), sha256=StringEvidence(value=digest), retrieval_state="failed", failure=failure))
                        primary_failure = primary_failure or AccessFailureEvidence(stage="retrieval", vm_id=vm.host, role=item.source_role, category=reason, message="materialized verification skipped after cleanup allowance exhaustion", source_path=item.source_path, occurred_at=datetime.now(timezone.utc))
                        item_verified = True
                        stop = True
                        break
                    try:
                        rec.verify_materialized_file(
                            child_root,
                            budget_check=_verification_budget_check(deadline, clock),
                        )
                    except _VerificationStopped as exc:
                        reason = "cleanup_allowance_reached_during_publication_verification"
                        failure = _evidence_failure(item, reason, "streaming materialized verification stopped at the caller cleanup allowance")
                        failure.details.update({"verified_prefix_bytes": exc.size, "verified_prefix_sha256": exc.sha256})
                        records.append(ArtifactRecord(artifact_id=item.artifact_id, path=item.destination_path, kind=item.kind, producer=item.source_role, unit_id=item.unit_id, deployment_id=item.deployment_id, closed=True,
                            size_bytes=Evidence[int](value=size), sha256=StringEvidence(value=digest), retrieval_state="failed", failure=failure))
                        primary_failure = primary_failure or AccessFailureEvidence(stage="retrieval", vm_id=vm.host, role=item.source_role, category=reason, message="streaming materialized verification reached cleanup allowance", source_path=item.source_path, occurred_at=datetime.now(timezone.utc))
                        item_verified = True
                        stop = True
                        break
                    except (OSError, ValueError):
                        records.append(ArtifactRecord(artifact_id=item.artifact_id, path=item.destination_path, kind=item.kind, producer=item.source_role, unit_id=item.unit_id, deployment_id=item.deployment_id, closed=True, size_bytes=Evidence[int](unavailable_reason="materialized verification failed"), sha256=StringEvidence(unavailable_reason="materialized verification failed"), retrieval_state="failed", failure=_evidence_failure(item, "publication_verification_failed", "published file failed materialized verification")))
                        primary_failure = primary_failure or AccessFailureEvidence(stage="retrieval", vm_id=vm.host, role=item.source_role, category="publication_verification_failed", message="published file failed materialized verification", source_path=item.source_path, occurred_at=datetime.now(timezone.utc))
                        item_verified = True
                        break
                    if reason is None and deadline.remaining(clock, cleanup=True) <= 0:
                        reason = "cleanup_allowance_reached_during_publication_verification"
                        failure = _evidence_failure(item, reason, "materialized verification completed after the caller cleanup allowance")
                        records.append(ArtifactRecord(artifact_id=item.artifact_id, path=item.destination_path, kind=item.kind, producer=item.source_role, unit_id=item.unit_id, deployment_id=item.deployment_id, closed=True,
                            size_bytes=Evidence[int](value=size), sha256=StringEvidence(value=digest), retrieval_state="failed", failure=failure))
                        primary_failure = primary_failure or AccessFailureEvidence(stage="retrieval", vm_id=vm.host, role=item.source_role, category=reason, message="retrieval verification exceeded the caller cleanup allowance", source_path=item.source_path, occurred_at=datetime.now(timezone.utc))
                        item_verified = True
                        stop = True
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
            if reason == "transport_preflight_failed":
                break
        if not item_verified:
            last_attempt = next((attempt for attempt in reversed(attempts) if attempt.artifact_id == item.artifact_id), None)
            if reason not in {"cleanup_allowance_reached_before_transfer", "result_transfer_allowance_exhausted"}:
                reason = last_attempt.reason if last_attempt and last_attempt.reason else reason or "transfer_incomplete"
            failure = _evidence_failure(item, reason, "artifact was not verified and published within the available cleanup allowance")
            if reason == "cleanup_allowance_reached_before_transfer":
                failures.append(failure.model_dump(mode="json"))
                primary_failure = primary_failure or AccessFailureEvidence(stage="retrieval", vm_id=vm.host, role=item.source_role, category=reason, message="staging work exhausted the original copy allowance before SCP", source_path=item.source_path, occurred_at=datetime.now(timezone.utc))
            records.append(ArtifactRecord(artifact_id=item.artifact_id, path=item.destination_path, kind=item.kind, producer=item.source_role, unit_id=item.unit_id, deployment_id=item.deployment_id, closed=True,
                size_bytes=Evidence[int](value=last_attempt.size_bytes) if last_attempt and last_attempt.size_bytes is not None else Evidence[int](unavailable_reason="no observed local size"),
                sha256=StringEvidence(value=last_attempt.sha256) if last_attempt and last_attempt.sha256 is not None else StringEvidence(unavailable_reason="no observed local hash"),
                retrieval_state="failed", failure=failure))
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
