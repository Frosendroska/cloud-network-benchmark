"""Capsule and truthful restore/finalize path for closed native archives.

The capsule stores only lifecycle/provenance context. Native bytes stay in the
closed archive and are revalidated after a generation-bound restore. A capsule
cannot set validation state; the production projection and F08 validator do.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
from types import SimpleNamespace
from typing import Any, Mapping


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, bytes):
        return {"$bytes_base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, SimpleNamespace):
        return {str(key): _jsonable(item) for key, item in vars(value).items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _replay_provider_result(result: Any) -> dict[str, Any]:
    """Keep only F08 replay inputs; exclude SSH requests, keys and setup secrets."""
    from .retrieval import RetrievalResult
    values = {}
    for name, outcome in result.values.items():
        evidence = getattr(outcome, "evidence", None)
        if evidence is None:
            if name == "protect":
                evidence = SimpleNamespace(expiry=outcome)
            elif name in {"create", "prepare", "measure"}:
                evidence = outcome
        saved = None
        if name == "measure" and evidence is not None:
            saved = {"outcome": _jsonable(getattr(evidence, "outcome", None)),
                "receiver_command_evidence": _jsonable(getattr(evidence, "receiver_command_evidence", ())),
                "outcome_source_bytes": _jsonable(getattr(evidence, "outcome_source_bytes", None))}
        elif name == "retrieve" and isinstance(evidence, RetrievalResult):
            saved = evidence.model_copy(update={"actions": [], "attempts": [], "records": []}).model_dump(mode="json")
            saved["archive_status"].pop("actions", None)
        elif name == "protect":
            expiry = getattr(evidence, "expiry", getattr(evidence, "expires_at", None))
            saved = {"expiry": _jsonable(expiry)} if expiry is not None else {}
        elif name == "create":
            deployment = evidence if hasattr(evidence, "provider_metadata") else getattr(evidence, "deployment", None)
            saved = {"provider_metadata": _jsonable(getattr(deployment, "provider_metadata", None))}
        elif name == "prepare":
            bootstrap = evidence.get("bootstrap") if isinstance(evidence, Mapping) else None
            saved = {"bootstrap": {"builds": _jsonable(getattr(bootstrap, "builds", ()))}}
        values[name] = {"stage": getattr(outcome, "stage", name),
            "succeeded": bool(getattr(outcome, "succeeded", True)), "evidence": saved}
    return {"provider": result.provider, "execution_state": result.execution_state,
        "cleanup_state": result.cleanup_state, "values": values,
        "stage_times": _jsonable(result.stage_times), "primary_failure": result.primary_failure,
        "secondary_failures": [], "interrupted": bool(result.interrupted),
        "creation_may_have_started": bool(result.creation_may_have_started)}


def _safe_relative(value: str, label: str) -> Path:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise ValueError(f"{label} must be a normalized repository-relative path")
    return Path(*path.parts)


def _inventory(root: Path) -> list[dict[str, Any]]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("native result root must be a real directory")
    rows = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("native result tree cannot contain symlinks")
        if path.is_file():
            data = path.read_bytes()
            rows.append({"path": path.relative_to(root).as_posix(), "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest()})
    if not rows:
        raise ValueError("native result tree is empty")
    return rows


def write_batch_replay_capsule(path: Path, *, archives: Mapping[str, Mapping[str, Any]],
        parent_manifest_path: Path, child_manifest_paths: Mapping[str, Path],
        result_roots: Mapping[str, Path], provider_results: Mapping[str, Any],
        reservation: Any, repository_root: Path, maximum_capsule_bytes: int) -> dict[str, Any]:
    """Persist bounded immutable provenance for a fully verified provider batch."""
    from .retrieval import RetrievalManifest
    if (not archives or set(archives) != set(child_manifest_paths) or set(archives) != set(result_roots)
            or set(archives) != set(provider_results)):
        raise ValueError("capsule archive, manifest, result and provider sets must match exactly")
    if type(maximum_capsule_bytes) is not int or maximum_capsule_bytes <= 0:
        raise ValueError("capsule state requires a positive measured maximum byte bound")
    root = Path(repository_root).resolve(strict=True)
    parent_path = Path(parent_manifest_path).resolve(strict=True)
    try:
        parent_relative = parent_path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("parent manifest must remain inside the owning repository") from exc
    parent_manifest = json.loads(parent_path.read_text(encoding="utf-8"))
    normalized_archives, child_manifests, path_rows, context_rows = {}, {}, {}, {}
    for provider in sorted(archives):
        receipt = _jsonable(dict(archives[provider]))
        if receipt.get("status") != "verified" or receipt.get("verified") is not True:
            raise ValueError(f"{provider} has no completely verified archive")
        if not receipt.get("completion_generation") or not receipt.get("manifest_sha256"):
            raise ValueError("archive receipt lacks its immutable manifest/completion generation")
        manifest = receipt.get("manifest")
        if not isinstance(manifest, dict):
            raise ValueError("verified archive receipt is missing its immutable manifest")
        manifest_bytes = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
        if hashlib.sha256(manifest_bytes).hexdigest() != receipt["manifest_sha256"]:
            raise ValueError("archive manifest bytes do not match their generation-bound digest")
        if manifest.get("provider") != provider or manifest.get("run_id") != receipt.get("run_id"):
            raise ValueError("archive manifest provider/run identity differs from its receipt")
        closed = RetrievalManifest.model_validate(manifest.get("closed_inventory"))
        expected = {role: [] for role in ("vm_a", "vm_b")}
        for item in closed.items:
            relative = str(PurePosixPath(item.source_path).relative_to(PurePosixPath(closed.remote_workspace)))
            expected[item.source_role].append({"path": relative, "size_bytes": item.expected_size_bytes,
                "sha256": item.expected_sha256})
        for row in manifest.get("roles", []):
            if row.get("files") != sorted(expected[row.get("role")], key=lambda item: item["path"]):
                raise ValueError("archive role inventory differs from the frozen closed retrieval manifest")
        normalized_archives[provider] = receipt
        child_path = Path(child_manifest_paths[provider]).resolve(strict=True)
        result_root = Path(result_roots[provider]).resolve(strict=True)
        for label, source in (("child", child_path), ("result", result_root)):
            try:
                path_rows[f"{provider}:{label}"] = source.relative_to(root).as_posix()
            except ValueError as exc:
                raise ValueError("child result and manifest paths must remain in the owning repository") from exc
        child_manifests[provider] = json.loads(child_path.read_text(encoding="utf-8"))
        context_rows[provider] = _inventory(result_root)
        # No remote artifact bytes may already have been staged on the controller.
        if set(row["path"] for row in context_rows[provider]) & {
                item.destination_path for item in closed.items}:
            raise ValueError("native guest artifact bytes are already present in the controller result tree")
    document = {"schema_version": 2, "status": "archive_pending_replay",
        "parent_path": parent_relative, "parent_manifest": parent_manifest,
        "child_paths": {provider: path_rows[f"{provider}:child"] for provider in sorted(archives)},
        "result_paths": {provider: path_rows[f"{provider}:result"] for provider in sorted(archives)},
        "child_manifests": child_manifests, "controller_context_inventory": context_rows,
        "archives": normalized_archives,
        "provider_results": {provider: _replay_provider_result(provider_results[provider])
            for provider in sorted(archives)},
        "reservation": _jsonable(reservation)}
    encoded = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(encoded) > maximum_capsule_bytes:
        raise ValueError("immutable replay capsule exceeds its measured controller-state bound")
    destination = Path(path)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("replay capsule path must be new and exclusive")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.parent.is_symlink():
        raise ValueError("replay capsule directory cannot be a symlink")
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
    return {"status": document["status"], "path": str(destination),
        "sha256": hashlib.sha256(encoded).hexdigest(), "bytes": len(encoded),
        "providers": sorted(archives), "manifest_sha256": {
            provider: normalized_archives[provider]["manifest_sha256"] for provider in sorted(archives)}}


def write_replay_capsule(path: Path, *, archive_receipt: Mapping[str, Any],
        archive_manifest_path: Path,
        parent_manifest_path: Path, child_manifest_path: Path, child_result_root: Path,
        repository_root: Path, provider_result: Any, reservation: Any) -> dict[str, Any]:
    """Persist immutable source-bound lifecycle context next to a verified archive."""
    if archive_receipt.get("status") != "verified" or archive_receipt.get("verified") is not True:
        raise ValueError("only a closed, remotely verified archive can receive a replay capsule")
    objects = archive_receipt.get("objects")
    if (not isinstance(objects, list) or not objects
            or any(not isinstance(row, dict) or not row.get("key")
                   or not row.get("generation") or type(row.get("size_bytes")) is not int
                   or row["size_bytes"] < 0 for row in objects)
            or not archive_receipt.get("completion_generation")):
        raise ValueError("archive receipt must bind every object and completion generation")
    root = Path(repository_root).resolve(strict=True)
    paths = {}
    for label, source in (("parent", parent_manifest_path), ("child", child_manifest_path),
                          ("result", child_result_root)):
        source = Path(source).resolve(strict=True)
        try:
            relative = source.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError(f"{label} source must remain inside the owning repository") from exc
        paths[label] = relative
    archive_manifest_path = Path(archive_manifest_path)
    if archive_manifest_path.is_symlink() or not archive_manifest_path.is_file():
        raise ValueError("closed archive manifest must be a regular file")
    manifest_bytes = archive_manifest_path.read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != archive_receipt.get("manifest_sha256"):
        raise ValueError("archive manifest bytes differ from the verified receipt digest")
    archive_manifest = json.loads(manifest_bytes)
    inventory = _inventory(Path(child_result_root))
    if archive_manifest.get("files") != inventory:
        raise ValueError("closed archive manifest inventory differs from the lifecycle result tree")
    parent = json.loads(Path(parent_manifest_path).read_text(encoding="utf-8"))
    child = json.loads(Path(child_manifest_path).read_text(encoding="utf-8"))
    if child.get("run_id") != archive_receipt.get("run_id", child.get("run_id")):
        raise ValueError("child run identity differs from the verified archive")
    payload = {"schema_version": 1, "status": "archive_pending_replay",
        "archive": _jsonable(dict(archive_receipt)),
        "closed_inventory": inventory,
        "paths": paths, "parent_manifest": parent, "child_manifest": child,
        "provider_result": _jsonable(provider_result),
        "reservation": _jsonable(reservation)}
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    destination = Path(path)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("replay capsule path must be new and exclusive")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
    return {"status": payload["status"], "path": str(destination),
        "sha256": hashlib.sha256(encoded).hexdigest(), "bytes": len(encoded),
        "run_id": child["run_id"], "manifest_sha256": archive_receipt.get("manifest_sha256")}


def _decode_bytes(value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"$bytes_base64"}:
        return base64.b64decode(value["$bytes_base64"], validate=True)
    if isinstance(value, dict):
        return {key: _decode_bytes(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode_bytes(item) for item in value]
    return value


def _restore_lifecycle(provider: str, document: Mapping[str, Any], reservation: Any) -> Any:
    from .contracts.admission import ExpiryEvidence
    from .contracts.artifacts import ArtifactRecord
    from .contracts.validation import ValidationResult
    from .lifecycle_adapters import RemoteGuestExecution
    from .lifecycle_guest import GuestOutcome
    from .orchestration import LifecycleResult, ProviderLifecycleResult, ProviderStageOutcome
    from .retrieval import RetrievalResult

    values = {}
    for name, row in document.get("values", {}).items():
        evidence = _decode_bytes(row.get("evidence"))
        if name == "measure" and isinstance(evidence, dict):
            outcome = GuestOutcome.model_validate(evidence["outcome"]) if evidence.get("outcome") else None
            from .contracts.measurements import CommandEvidence
            evidence = RemoteGuestExecution(outcome=outcome,
                actions=tuple(evidence.get("actions", ())), failure=evidence.get("failure"),
                receiver_command_evidence=tuple(CommandEvidence.model_validate(item)
                    for item in evidence.get("receiver_command_evidence", ())),
                outcome_source_bytes=evidence.get("outcome_source_bytes"))
        elif name == "retrieve" and isinstance(evidence, dict):
            evidence = RetrievalResult.model_validate(evidence)
        elif name == "prepare" and isinstance(evidence, dict):
            bootstrap = evidence.get("bootstrap")
            if isinstance(bootstrap, dict):
                from .bootstrap import BuildObservation
                evidence["bootstrap"] = SimpleNamespace(builds=[BuildObservation.model_validate(item)
                    for item in bootstrap.get("builds", ())])
        elif name == "create" and isinstance(evidence, dict):
            if evidence.get("provider_metadata") is not None:
                from .contracts.common import StructuredEvidence
                evidence = SimpleNamespace(provider_metadata=StructuredEvidence.model_validate(
                    evidence["provider_metadata"]))
            else:
                evidence = SimpleNamespace()
        elif name == "protect" and isinstance(evidence, dict) and isinstance(evidence.get("expiry"), dict):
            evidence = SimpleNamespace(expiry=ExpiryEvidence.model_validate(evidence["expiry"]))
        values[name] = ProviderStageOutcome(stage=row["stage"], succeeded=row["succeeded"], evidence=evidence)
    stage_times = {}
    for stage, pair in document.get("stage_times", {}).items():
        stage_times[stage] = {key: datetime.fromisoformat(value) for key, value in pair.items()}
    provider_result = ProviderLifecycleResult(provider=provider,
        execution_state=document.get("execution_state", "initialized"),
        cleanup_state=document.get("cleanup_state", "not_started"), values=values,
        primary_failure=document.get("primary_failure"),
        secondary_failures=list(document.get("secondary_failures", ())),
        interrupted=bool(document.get("interrupted", False)),
        creation_may_have_started=bool(document.get("creation_may_have_started", False)),
        stage_times=stage_times)
    return LifecycleResult(provider_results={provider: provider_result}, completed=True,
        reservation_evidence=reservation)


def finalize_restored_capsule(*, capsule_path: Path, native_root: Path,
        repository_root: Path, evaluated_at: datetime | None = None) -> dict[str, Any]:
    """Restore lifecycle context and use the production projection/F08/analysis path."""
    from .contracts.admission import ReservationEvidence
    from .manifests import CampaignManifest, ChildRunManifest

    capsule_path, native_root = Path(capsule_path), Path(native_root)
    if capsule_path.is_symlink() or not capsule_path.is_file():
        raise ValueError("replay capsule must be a regular non-symlink file")
    capsule_bytes = capsule_path.read_bytes()
    capsule = json.loads(capsule_bytes)
    if capsule.get("schema_version") != 1 or capsule.get("status") != "archive_pending_replay":
        raise ValueError("capsule is not an archive-pending replay capsule")
    archive = capsule.get("archive", {})
    if (archive.get("status") != "verified" or archive.get("verified") is not True
            or not archive.get("completion_generation") or not archive.get("manifest_sha256")):
        raise ValueError("capsule is not bound to a verified generation-bound closed archive")
    expected_inventory = capsule.get("closed_inventory")
    if not isinstance(expected_inventory, list) or _inventory(native_root) != expected_inventory:
        raise ValueError("restored native inventory differs from the immutable capsule inventory")
    repository_root = Path(repository_root).resolve(strict=True)
    paths = capsule.get("paths", {})
    manifest_targets = {key: repository_root / _safe_relative(paths[key], f"{key} manifest path")
        for key in ("parent", "child")}
    result_target = repository_root / _safe_relative(paths["result"], "result path")
    if any(path.exists() or path.is_symlink() for path in (*manifest_targets.values(), result_target)):
        raise FileExistsError("restore targets must all be new; no existing result or manifest will be overwritten")
    if any(parent.is_symlink() for path in (*manifest_targets.values(), result_target)
           for parent in (path, *path.parents) if parent != repository_root.parent):
        raise ValueError("restore targets cannot traverse symlink components")
    child = ChildRunManifest.model_validate(capsule["child_manifest"])
    parent = CampaignManifest.model_validate(capsule["parent_manifest"])
    provider = str(child.provider)
    if child.run_id != archive.get("run_id", child.run_id):
        raise ValueError("capsule run identity differs from the archive identity")
    if result_target.as_posix() != str(Path(paths["result"])):
        raise ValueError("result path is not normalized")
    result_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(native_root, result_target, symlinks=False)
    for label, target in manifest_targets.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        doc = capsule[f"{label}_manifest"]
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write((json.dumps(doc, sort_keys=True, indent=2) + "\n").encode())
            stream.flush(); os.fsync(stream.fileno())
    reservation = ReservationEvidence.model_validate(capsule["reservation"])
    lifecycle = _restore_lifecycle(provider, capsule["provider_result"], reservation)
    from .lifecycle_projection import finalize_production_batch
    children = {provider: child}
    values = finalize_production_batch(parent_path=manifest_targets["parent"], children=children,
        parent=parent, lifecycle=lifecycle, repository_root=repository_root,
        evaluated_at=evaluated_at)
    validation, summary = values[provider]
    if validation.run_id != child.run_id:
        raise ValueError("production validator returned another run identity")
    result = {"status": "replayed", "run_id": child.run_id,
        "validation_status": validation.validation_status.model_dump(mode="json"),
        "validation_result": validation.model_dump(mode="json"), "summary": summary,
        "result_root": str(result_target), "capsule_sha256": hashlib.sha256(capsule_bytes).hexdigest(),
        "archive_manifest_sha256": archive["manifest_sha256"]}
    if validation.validation_status.data_validity != "valid":
        result["status"] = "replayed_invalid"
    # Reuse the existing relocatable export descriptor and deterministic native
    # analysis reader. Invalid inputs remain in the report with their actual state.
    from .campaign_export import export_entry
    export_path = repository_root / "results" / "analysis" / "archive-replays" / child.run_id
    stage = {"results_root": str(repository_root / "results"),
        "repository_root": str(repository_root), "stage_id": "archive-replay",
        "entries": []}
    entry = {"entry_id": child.run_id, "run_ids": {provider: child.run_id},
        "parent_manifest_path": str(manifest_targets["parent"])}
    selection = export_entry(stage, entry, export_path)
    if selection is None:
        raise ValueError("validated restored run did not produce a relocatable analysis descriptor")
    from .analysis import analyze_selection
    report = analyze_selection(selection, analysis_git=child.implementation_git)
    report_path = export_path / "analysis-report.json"
    fd = os.open(report_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write((json.dumps(report.payload, sort_keys=True, indent=2) + "\n").encode())
        stream.flush(); os.fsync(stream.fileno())
    result["analysis"] = {"status": "completed", "selection_path": str(selection),
        "selection_sha256": hashlib.sha256(selection.read_bytes()).hexdigest(),
        "report_path": str(report_path), "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest()}
    return result


def restore_and_finalize_batch_capsule(*, capsule_path: Path, expected_capsule_sha256: str,
        backend: Any, bucket: str, archive_staging_root: Path, materialization_root: Path,
        repository_root: Path, maximum_part_bytes: int, minimum_free_bytes: int = 0,
        evaluated_at: datetime | None = None) -> dict[str, Any]:
    """Restore every provider archive, attach verified records, run F08, then F12."""
    from .contracts.admission import ReservationEvidence
    from .contracts.artifacts import ArtifactRecord
    from .contracts.common import Evidence, StringEvidence
    from .contracts.validation import ValidationResult
    from .manifests import CampaignManifest, ChildRunManifest
    from .orchestration import LifecycleResult, ProviderStageOutcome
    from .retrieval import RetrievalManifest, RetrievalResult
    from .gcs_archive import materialize_verified_guest_archives

    capsule_path = Path(capsule_path)
    if capsule_path.is_symlink() or not capsule_path.is_file():
        raise ValueError("replay capsule must be a regular non-symlink file")
    capsule_bytes = capsule_path.read_bytes()
    if hashlib.sha256(capsule_bytes).hexdigest() != expected_capsule_sha256:
        raise ValueError("replay capsule bytes differ from their separately bound digest")
    capsule = json.loads(capsule_bytes)
    if capsule.get("schema_version") != 2 or capsule.get("status") != "archive_pending_replay":
        raise ValueError("capsule is not a batch archive-pending replay capsule")
    archives = capsule.get("archives")
    if not isinstance(archives, dict) or not archives:
        raise ValueError("capsule has no provider archive receipts")
    repository_root = Path(repository_root).resolve(strict=True)
    archive_staging_root, materialization_root = Path(archive_staging_root), Path(materialization_root)
    for path, label in ((archive_staging_root, "archive staging root"),
                        (materialization_root, "materialization root")):
        if path.is_symlink() or not path.is_dir() or path.resolve(strict=True) != path.absolute():
            raise ValueError(f"{label} must be an existing real directory without symlink components")
    native_roots = {}
    for provider, receipt in sorted(archives.items()):
        if receipt.get("bucket") != bucket:
            raise ValueError("requested archive bucket differs from the pinned capsule receipt")
        staging = archive_staging_root / provider
        if staging.exists() or staging.is_symlink():
            raise FileExistsError("provider archive restore staging must be new")
        staging.mkdir(mode=0o700)
        restored = materialize_verified_guest_archives(backend=backend, bucket=bucket,
            provider=provider, experiment_id=receipt["manifest"]["experiment_id"],
            run_id=receipt["run_id"], expected_manifest_sha256=receipt["manifest_sha256"],
            archive_directory=staging, materialization_root=materialization_root,
            maximum_part_bytes=maximum_part_bytes, minimum_free_bytes=minimum_free_bytes)
        if restored.get("completion_generation") != receipt.get("completion_generation"):
            raise ValueError("restored completion generation differs from the capsule receipt")
        native_roots[provider] = Path(restored["native_root"])
    if set(native_roots) != set(capsule.get("child_manifests", {})):
        raise ValueError("restored provider set differs from capsule child set")

    parent_path = repository_root / _safe_relative(capsule["parent_path"], "parent manifest path")
    if parent_path.is_symlink() or not parent_path.is_file():
        raise ValueError("source parent manifest is unavailable")
    parent_doc = json.loads(parent_path.read_text(encoding="utf-8"))
    if parent_doc != capsule["parent_manifest"]:
        raise ValueError("source parent manifest changed after capsule creation")
    parent = CampaignManifest.model_validate(parent_doc)
    children = {}
    child_paths = {}
    result_roots = {}
    for provider in sorted(native_roots):
        child_path = repository_root / _safe_relative(capsule["child_paths"][provider], "child manifest path")
        result_root = repository_root / _safe_relative(capsule["result_paths"][provider], "result root path")
        if child_path.is_symlink() or not child_path.is_file() or result_root.is_symlink() or not result_root.is_dir():
            raise ValueError("source child manifest or context result root is unavailable")
        child_doc = json.loads(child_path.read_text(encoding="utf-8"))
        if child_doc != capsule["child_manifests"][provider]:
            raise ValueError("source child manifest changed after capsule creation")
        if _inventory(result_root) != capsule["controller_context_inventory"][provider]:
            raise ValueError("controller context inventory changed after capsule creation")
        child = ChildRunManifest.model_validate(child_doc)
        archive = archives[provider]
        if child.run_id != archive.get("run_id") or str(child.provider) != provider:
            raise ValueError("restored archive identity differs from the source child manifest")
        closed = RetrievalManifest.model_validate(archive["manifest"]["closed_inventory"])
        expected_inventory = sorted(({"path": item.destination_path,
            "size_bytes": item.expected_size_bytes, "sha256": item.expected_sha256} for item in closed.items),
            key=lambda row: row["path"])
        if _inventory(native_roots[provider]) != expected_inventory:
            raise ValueError("restored native tree differs from the immutable role/archive inventory")
        for row in expected_inventory:
            source, target = native_roots[provider] / row["path"], result_root / row["path"]
            if target.is_symlink():
                raise ValueError("native result destination cannot be a symlink")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if _inventory(target.parent)[0].get("sha256") != row["sha256"]:
                    raise ValueError("existing native result bytes differ from the immutable archive")
            else:
                with source.open("rb") as incoming, target.open("xb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
                    outgoing.flush(); os.fsync(outgoing.fileno())
            observed = _inventory(target.parent)
            matches = [entry for entry in observed if entry["path"] == target.name]
            if len(matches) != 1 or matches[0]["sha256"] != row["sha256"] or matches[0]["size_bytes"] != row["size_bytes"]:
                raise ValueError("native result publication failed its immutable size/hash verification")
        children[provider] = child
        child_paths[provider] = child_path
        result_roots[provider] = result_root

    reservation = ReservationEvidence.model_validate(capsule["reservation"])
    provider_results = {}
    for provider, document in capsule["provider_results"].items():
        restored_lifecycle = _restore_lifecycle(provider, document, reservation)
        provider_result = restored_lifecycle.provider_results[provider]
        stage_outcome = provider_result.values.get("retrieve")
        if stage_outcome is None or stage_outcome.succeeded is not True:
            raise ValueError("capsule provider retrieval stage was not successful")
        archive = archives[provider]
        closed = RetrievalManifest.model_validate(archive["manifest"]["closed_inventory"])
        records = []
        for item in closed.items:
            target = result_roots[provider] / item.destination_path
            data_hash = hashlib.sha256()
            size = 0
            with target.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    data_hash.update(block); size += len(block)
            if (size, data_hash.hexdigest()) != (item.expected_size_bytes, item.expected_sha256):
                raise ValueError("native artifact failed verification before record creation")
            record = ArtifactRecord(artifact_id=item.artifact_id, path=item.destination_path,
                kind=item.kind, producer=item.source_role, unit_id=item.unit_id,
                deployment_id=item.deployment_id, closed=True,
                size_bytes=Evidence[int](value=size), sha256=StringEvidence(value=data_hash.hexdigest()),
                retrieval_state="verified")
            record.verify_materialized_file(result_roots[provider])
            records.append(record)
        retrieval = RetrievalResult.model_validate(stage_outcome.evidence)
        retrieval = retrieval.model_copy(update={"records": records, "complete": True,
            "archive_status": {**retrieval.archive_status, "status": "materialized_pending_replay",
                "verified": True, "validation_state": "native_restored_pending_validation"}})
        provider_result.values["retrieve"] = ProviderStageOutcome(stage="retrieve",
            succeeded=True, evidence=retrieval)
        provider_results[provider] = provider_result
    lifecycle = LifecycleResult(provider_results=provider_results, completed=True,
        reservation_evidence=reservation)
    from .lifecycle_projection import finalize_production_batch
    validations = finalize_production_batch(parent_path=parent_path, children=children,
        parent=parent, lifecycle=lifecycle, repository_root=repository_root, evaluated_at=evaluated_at)
    output = {"status": "replayed", "capsule_sha256": expected_capsule_sha256,
        "archives": {provider: {"manifest_sha256": archive["manifest_sha256"],
            "completion_generation": archive["completion_generation"]} for provider, archive in archives.items()},
        "validation": {}, "native_roots": {provider: str(path) for provider, path in native_roots.items()}}
    for provider, (validation, summary) in validations.items():
        output["validation"][provider] = {"result": validation.model_dump(mode="json"),
            "summary": summary, "status": validation.validation_status.model_dump(mode="json")}
        if validation.validation_status.data_validity != "valid":
            output["status"] = "replayed_invalid"
    from .campaign_export import export_entry
    campaign_id = parent.campaign_id
    export_root = repository_root / "results" / "analysis" / "archive-replays" / str(campaign_id)
    entry = {"entry_id": str(campaign_id),
        "run_ids": {provider: child.run_id for provider, child in children.items()},
        "parent_manifest_path": str(parent_path)}
    selection = export_entry({"results_root": str(repository_root / "results"),
        "repository_root": str(repository_root), "stage_id": "archive-replay", "entries": []},
        entry, export_root)
    if selection is None:
        raise ValueError("validated restored batch did not produce a deterministic export selection")
    from .analysis import analyze_selection
    first_child = next(iter(children.values()))
    report = analyze_selection(selection,
        analysis_git=first_child.config_provenance.implementation_git)
    report_path = export_root / "analysis-report.json"
    fd = os.open(report_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write((json.dumps(report.payload, sort_keys=True, indent=2) + "\n").encode())
        stream.flush(); os.fsync(stream.fileno())
    output["analysis"] = {"status": "completed", "selection_path": str(selection),
        "selection_sha256": hashlib.sha256(selection.read_bytes()).hexdigest(),
        "report_path": str(report_path), "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest()}
    output["replayed_manifests"] = {
        "parent": {"path": capsule["parent_path"],
            "sha256": hashlib.sha256(parent_path.read_bytes()).hexdigest()},
        "children": {provider: {"path": capsule["child_paths"][provider],
            "sha256": hashlib.sha256(child_paths[provider].read_bytes()).hexdigest()}
            for provider in sorted(child_paths)}}
    receipt_root = archive_staging_root / "replay-receipts"
    if receipt_root.exists() and (receipt_root.is_symlink() or not receipt_root.is_dir()):
        raise ValueError("replay receipt destination is not a real directory")
    receipt_root.mkdir(mode=0o700, exist_ok=True)
    receipt_path = receipt_root / f"{expected_capsule_sha256}.json"
    receipt_bytes = (json.dumps(output, sort_keys=True, separators=(",", ":")) + "\n").encode()
    fd = os.open(receipt_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(receipt_bytes); stream.flush(); os.fsync(stream.fileno())
    output["replay_receipt"] = {"path": str(receipt_path),
        "sha256": hashlib.sha256(receipt_bytes).hexdigest(), "bytes": len(receipt_bytes)}
    return output
