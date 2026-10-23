"""Closed source inventory, retrieval handoff and immutable bundle sealing."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

from .contracts.artifacts import ArtifactInput, ArtifactRecord, resolve_artifact_path
from .contracts.common import Evidence, FailureEvidence, StringEvidence, VmRole
from .contracts.validation import ValidatorInput
from .retrieval import RetrievalItem, RetrievalManifest, RetrievalResult, run_retrieval
from .admission import bounded_copy_handoff


def lifecycle_declarations(layout, *, mode, measurement_units, schedule_supplied: bool = False) -> list[ArtifactInput]:
    """Return existing layout declarations plus exact F09-produced local logs/builds."""
    declarations = layout.declarations_for_mode(mode, measurement_units, schedule_supplied=schedule_supplied)
    seen = {item.artifact_id for item in declarations}
    additions: list[ArtifactInput] = []
    for role in ("vm_a", "vm_b"):
        for tool in ("sockperf", "iperf3"):
            artifact_id = f"f02r-{role}-{tool}-build"
            if artifact_id not in seen:
                additions.append(ArtifactInput(artifact_id=artifact_id,
                    path=f"metadata/build/{role}-{tool}.identity", kind="build-identity",
                    producer=role, unit_id=None, expectation="required"))
                seen.add(artifact_id)
    if str(getattr(mode, "value", mode)) != "diagnostic_day":
        for unit in measurement_units:
            unit_id = unit.unit_id
            artifact_id = f"{unit_id}-sockperf-stdout"
            if artifact_id not in seen:
                additions.append(ArtifactInput(artifact_id=artifact_id,
                    path=f"measurements/{unit_id}/sockperf.stdout.log", kind="sockperf-stdout",
                    producer="vm_a", unit_id=unit_id, expectation="required"))
                seen.add(artifact_id)
    return [*declarations, *additions]


def _sha256(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def build_closed_inventory(*, identity: Mapping[str, Any], deployment_id: str,
                           remote_workspace: str, declarations: Sequence[ArtifactInput],
                           source_roots: Mapping[str, Path], source_vm_ids: Mapping[str, str],
                           measurement_ended_at: datetime, closure: Mapping[str, datetime],
                           timeout_seconds: float = 30.0,
                           controller_materialized_ids: Sequence[str] = ()) -> RetrievalManifest:
    """Inventory only actual closed files from both producer roots.

    Missing declarations are preserved as unavailable evidence. The caller must
    supply an observed stop/flush/exit closure time for each remote role.
    """
    workspace = PurePosixPath(remote_workspace)
    if not remote_workspace.startswith("/") or workspace.as_posix() != remote_workspace or ".." in workspace.parts:
        raise ValueError("remote workspace must be normalized absolute POSIX")
    if measurement_ended_at.tzinfo is None or measurement_ended_at.utcoffset() is None:
        raise ValueError("measurement end must be timezone-aware")
    if set(closure) != {"vm_a", "vm_b"}:
        raise ValueError("closure evidence must cover VM A and VM B")
    if set(source_roots) != {"vm_a", "vm_b"} or set(source_vm_ids) != {"vm_a", "vm_b"}:
        raise ValueError("source roots and VM identities must cover both roles")
    if any(t.tzinfo is None or t.utcoffset() is None or t < measurement_ended_at for t in closure.values()):
        raise ValueError("producer closure must be observed at or after measurement end")
    closed_at = max(closure.values())
    items: list[RetrievalItem] = []
    unavailable: dict[str, dict[str, str]] = {}
    controller_ids = set(controller_materialized_ids)
    for declaration in declarations:
        role = str(getattr(declaration.producer, "value", declaration.producer))
        if role == "controller" or declaration.artifact_id in controller_ids:
            continue
        if role not in source_roots:
            raise ValueError(f"unsupported producer role {role}")
        root = source_roots[role].resolve(strict=True)
        if root.is_symlink() or not root.is_dir():
            raise ValueError("producer root must be a real directory")
        if closure[role] < measurement_ended_at:
            unavailable[declaration.artifact_id] = {"role": role, "path": declaration.path or declaration.path_template or "unknown", "reason": "producer closure was not observed"}
            continue
        concrete: list[tuple[str, str, Path]] = []
        if declaration.path is not None:
            path = root / declaration.path
            if path.is_file() and not path.is_symlink():
                concrete.append((declaration.artifact_id, declaration.path, path))
            else:
                unavailable[declaration.artifact_id] = {"role": role, "path": declaration.path, "reason": "declared source file is absent or not a regular file"}
        else:
            template = declaration.path_template
            if template is None or template.count("{chunk_id}") != 1:
                raise ValueError("dynamic declaration requires one chunk_id template")
            parent = PurePosixPath(template).parent
            name = PurePosixPath(template).name
            prefix, suffix = name.split("{chunk_id}")
            folder = root / str(parent)
            if folder.is_dir():
                for candidate in sorted(folder.iterdir()):
                    item_name = candidate.name
                    if candidate.is_file() and not candidate.is_symlink() and item_name.startswith(prefix) and item_name.endswith(suffix):
                        chunk = item_name[len(prefix):len(item_name)-len(suffix) if suffix else None]
                        if chunk and all(c.isalnum() or c in "._-" for c in chunk):
                            concrete.append((f"{declaration.artifact_id}-{chunk}", template.replace("{chunk_id}", chunk), candidate))
            if not concrete:
                unavailable[declaration.artifact_id] = {"role": role, "path": template, "reason": "no closed concrete chunk files were observed"}
        for artifact_id, rel, path in concrete:
            size, digest = _sha256(path)
            remote_path = str(workspace / rel)
            items.append(RetrievalItem(
                artifact_id=artifact_id, deployment_id=deployment_id,
                source_vm=source_vm_ids[role], source_role=role,
                source_path=remote_path, destination_path=rel, kind=declaration.kind,
                unit_id=(artifact_id[len(declaration.artifact_id)+1:] if declaration.path_template else declaration.unit_id),
                expectation=declaration.expectation, closed=True, expected_size_bytes=size,
                expected_sha256=digest, timeout_seconds=timeout_seconds,
            ))
    return RetrievalManifest(
        identity=dict(identity), deployment_id=deployment_id, remote_workspace=remote_workspace,
        declarations=list(declarations), items=items, unavailable_items=unavailable,
        measurement_ended_at=measurement_ended_at, producers_closed_at=closed_at,
        closure_evidence="observed producer stop/flush/exit for both VM roles",
    )


def retry_failed_items(result: RetrievalResult, *, already_retried: Sequence[str]) -> tuple[str, ...]:
    """Return only failed concrete IDs not already attempted twice."""
    attempted_twice = {item.artifact_id for item in result.attempts if item.attempt >= 2}
    already = set(already_retried) | attempted_twice
    failed = {record.artifact_id for record in result.records if record.retrieval_state == "failed"}
    failed.update(str(item.get("artifact_id")) for item in result.failures if item.get("artifact_id"))
    return tuple(sorted(item for item in failed if item not in already))


def retry_manifest(manifest: RetrievalManifest, artifact_ids: Sequence[str]) -> RetrievalManifest:
    ids = set(artifact_ids)
    if len(ids) != len(artifact_ids):
        raise ValueError("retry artifact identities must be unique")
    selected = [item for item in manifest.items if item.artifact_id in ids]
    if {item.artifact_id for item in selected} != ids:
        raise ValueError("retry may select only concrete declared retrieval items")
    if not selected:
        raise ValueError("retry must contain at least one failed concrete item")
    selected_bases = set()
    for item in selected:
        base = next((d.artifact_id for d in manifest.declarations
                     if d.producer != "controller" and
                     (item.artifact_id == d.artifact_id or item.artifact_id.startswith(d.artifact_id + "-"))), None)
        if base is None:
            raise ValueError("retry item has no matching remote declaration")
        selected_bases.add(base)
    declarations = [d for d in manifest.declarations
                    if d.producer == "controller" or d.artifact_id in selected_bases]
    unavailable = {key: value for key, value in manifest.unavailable_items.items()
                   if key in selected_bases and key not in {item.artifact_id for item in selected}}
    return RetrievalManifest.model_validate({
        **manifest.model_dump(mode="json"),
        "declarations": [item.model_dump(mode="json") for item in declarations],
        "items": [item.model_dump(mode="json") for item in selected],
        "unavailable_items": unavailable,
    })


def retrieve_closed_bundle(*, connection: Any, manifest: RetrievalManifest, runner: Any,
                           filesystem: Path, deadline: Any,
                           retry_admission: Callable[[RetrievalItem], bool],
                           reservation_id: str, provider_trigger: datetime,
                           retry_seconds: float, now_utc: Callable[[], datetime],
                           clock: Callable[[], float],
                           already_retried: Sequence[str] = ()) -> RetrievalResult:
    """Retrieve once, then re-admit and retry each failed concrete item once.

    Each F02 call is explicitly limited to one attempt. F09 makes a fresh
    admission decision immediately before each retry and passes only that
    failed item to the second call. The same original RemoteDeadline is used
    throughout, so the cleanup reserve is not reset between rounds.
    """
    first = run_retrieval(connection, manifest, runner, filesystem, deadline=deadline,
                          clock=clock, max_attempts=1)
    merged = first
    retry_ids = retry_failed_items(first, already_retried=already_retried)
    if any(action.result.get("outcome") == "interrupted" for action in first.actions):
        retry_ids = ()
    for artifact_id in retry_ids:
        item = next((candidate for candidate in manifest.items if candidate.artifact_id == artifact_id), None)
        if item is None:
            continue
        handoff = bounded_copy_handoff(reservation_id=reservation_id,
            cap_end=deadline.cap_expires_at, provider_trigger=provider_trigger,
            now=now_utc(), cleanup_seconds=deadline.cleanup_reserve_seconds,
            retry_seconds=retry_seconds, failed_artifact_ids=(artifact_id,),
            already_retried=already_retried)
        if artifact_id not in handoff.retry_artifact_ids:
            continue
        try:
            admitted = retry_admission(item)
        except Exception:
            admitted = False
        if not admitted:
            continue
        one_item = retry_manifest(manifest, (artifact_id,))
        second = run_retrieval(connection, one_item, runner, filesystem, deadline=deadline,
                               clock=clock, max_attempts=1, attempt_offset=1)
        attempts = [*merged.attempts, *second.attempts]
        by_id = {record.artifact_id: record for record in merged.records}
        by_id.update({record.artifact_id: record for record in second.records})
        merged = RetrievalResult(
            complete=False,
            cleanup_required=True,
            actions=[*merged.actions, *second.actions],
            retrieved=list(dict.fromkeys([*merged.retrieved, *second.retrieved])),
            integrity={**merged.integrity, **second.integrity},
            records=list(by_id.values()), failures=[*merged.failures, *second.failures],
            attempts=attempts, failure=merged.failure or second.failure,
        )
    verified = {record.artifact_id for record in merged.records
                if record.retrieval_state == "verified" and record.closed}
    required = [declaration for declaration in manifest.declarations
                if declaration.producer != "controller" and declaration.expectation == "required"]
    complete = bool(required) and all(
        declaration.artifact_id not in manifest.unavailable_items
        and (concrete := [candidate.artifact_id for candidate in manifest.items
                          if candidate.artifact_id == declaration.artifact_id
                          or (declaration.path_template is not None
                              and candidate.artifact_id.startswith(declaration.artifact_id + "-"))])
        and all(artifact_id in verified for artifact_id in concrete)
        for declaration in required)
    return RetrievalResult.model_validate({**merged.model_dump(mode="python"),
        "complete": complete and deadline.remaining(clock, cleanup=True) > 0})


def save_validator_input(path: Path, value: ValidatorInput) -> ValidatorInput:
    """Persist and strictly reparse the exact input used for F08 validation."""
    if path.exists() or path.is_symlink():
        raise FileExistsError("validator input destination must be exclusive")
    validated = ValidatorInput.model_validate(value.model_dump(mode="python"))
    payload = validated.model_dump_json(indent=2).encode("utf-8") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload); stream.flush(); os.fsync(stream.fileno())
    return ValidatorInput.model_validate_json(path.read_text(encoding="utf-8"))


def finalize_validation(*, root: Path, validator_input_path: Path, value: ValidatorInput,
                        evaluated_at: datetime, peers: Sequence[ValidatorInput] = (),
                        peer_results: Sequence[Any] = ()) -> tuple[Any, str, str, dict[str, str]]:
    """Validate the persisted strict input, persist result/summary, then seal."""
    from .summaries import summarize_validation
    from .validation import evaluate_comparison, validate_bundle
    saved = save_validator_input(validator_input_path, value)
    result = validate_bundle(saved, evaluated_at=evaluated_at)
    if peers or peer_results:
        if len(peers) != len(peer_results):
            raise ValueError("peer inputs and validation results must match")
        compared = evaluate_comparison([*peers, saved], [*peer_results, result])
        result = compared[saved.identity.run_id]
    result_path = resolve_artifact_path(root, "validation/result.json")
    summary_path = resolve_artifact_path(root, "validation/summary.txt")
    if result_path.exists() or summary_path.exists():
        raise FileExistsError("validation outputs must be written before sealing and cannot be overwritten")
    result_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    _write_exclusive(result_path, result.model_dump_json(indent=2).encode("utf-8") + b"\n")
    summary = summarize_validation(result)
    _write_exclusive(summary_path, summary.encode("utf-8"))
    digest, hashes = seal_bundle(root)
    return result, summary, digest, hashes


def _write_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload); stream.flush(); os.fsync(stream.fileno())


def seal_bundle(root: Path, *, index_name: str = "checksums.sha256") -> tuple[str, dict[str, str]]:
    """Write the final public checksum index last, excluding itself/private files."""
    root = root.resolve(strict=True)
    index = resolve_artifact_path(root, index_name)
    if index.exists() or index.is_symlink():
        raise FileExistsError("checksum index is immutable and must be absent before sealing")
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("bundle sealing refuses symlinks")
        if not path.is_file() or path == index or "private" in path.relative_to(root).parts:
            continue
        rel = path.relative_to(root).as_posix()
        hashes[rel] = _sha256(path)[1]
    payload = "".join(f"{digest}  {name}\n" for name, digest in sorted(hashes.items())).encode("utf-8")
    index.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(index, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o444)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload); stream.flush(); os.fsync(stream.fileno())
    return hashlib.sha256(payload).hexdigest(), hashes
