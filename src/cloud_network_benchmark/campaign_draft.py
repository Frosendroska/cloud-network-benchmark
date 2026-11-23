"""Hash-bound, non-executable wrappers for production-built campaign drafts.

The production campaign preparer owns entry construction. This module only
records and verifies its output; it never creates identities or approval files.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from .paths import atomic_write_json


SCHEMA_VERSION = 1
DRAFT_STATUS = "DRAFT_UNAPPROVED"
ACTIVATED_STATUS = "READY_FOR_ADMISSION"


def validate_fixed_batch_scope(stages: list[dict[str, Any]]) -> None:
    """Enforce the one authorized pre-DAY batch shape before writing a card draft."""
    by_stage = {stage.get("stage"): stage for stage in stages}
    if len(stages) != 2 or set(by_stage) != {"acceptance", "calibration"}:
        raise ValueError("fixed pre-DAY draft requires exactly acceptance and calibration stages")
    acceptance = by_stage["acceptance"].get("entries", [])
    ids = [row.get("entry_id") for row in acceptance]
    reduced = ids == ["V-S1", "V-S3"]
    if not reduced and ids != ["V-S1", "V-S3", "V-A-FAIL", "V-Z-FAIL", "V-G-FAIL"]:
        raise ValueError("fixed pre-DAY acceptance requires the strict five entries or an explicitly waived normal pair")
    if reduced:
        from .failure_waiver import validate_prepared_binding
        validate_prepared_binding(by_stage["acceptance"].get("failure_waiver"), acceptance)
        if any(row.get("requires_supervision") for row in acceptance):
            raise ValueError("waived acceptance entries must contain only the normal non-supervised pair")
    if any(set(row.get("run_ids", {})) != {"aws", "azure", "gcp"} for row in acceptance[:2]):
        raise ValueError("V-S1 and V-S3 must each freeze all three provider children")
    if ([row.get("scenario") for row in acceptance[:2]] != ["S1", "S3"]
            or any(row.get("requires_supervision") for row in acceptance[:2])):
        raise ValueError("normal acceptance must preserve the simultaneous S1/S3 pair")
    if not reduced:
        for entry_id, provider in (("V-A-FAIL", "aws"), ("V-Z-FAIL", "azure"), ("V-G-FAIL", "gcp")):
            row = acceptance[[item.get("entry_id") for item in acceptance].index(entry_id)]
            if (set(row.get("run_ids", {})) != {provider} or row.get("requires_supervision") is not True):
                raise ValueError(f"{entry_id} must remain one supervised single-provider failure check")
    calibration = by_stage["calibration"].get("entries", [])
    if (len(calibration) != 1 or calibration[0].get("entry_id") != "CAL-S1"
            or set(calibration[0].get("run_ids", {})) != {"aws", "azure", "gcp"}):
        raise ValueError("fixed pre-DAY calibration requires one retained all-cloud CAL-S1 parent")
    run_ids = [run_id for stage in stages for entry in stage["entries"]
               for run_id in entry.get("run_ids", {}).values()]
    expected_run_count = 9 if reduced else 12
    if len(run_ids) != expected_run_count or len(set(run_ids)) != expected_run_count:
        raise ValueError(f"fixed pre-DAY batch must freeze exactly {expected_run_count} unique provider child run IDs")


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_file(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be an absolute file path")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} must be an absolute file path without parent traversal")
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError(f"{label} cannot traverse symlinks")
    if not path.is_file():
        raise ValueError(f"{label} is missing: {path}")
    return path


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _artifact_paths(stages: list[dict[str, Any]]) -> dict[str, str]:
    """Collect existing source-bound stage artifacts without inventing inputs."""
    paths: set[str] = set()
    for stage in stages:
        if not isinstance(stage, dict) or not isinstance(stage.get("entries"), list):
            raise ValueError("prepared draft stage must contain an entries list")
        for entry in stage["entries"]:
            for key in ("config_path", "runtime_config_path", "schedule_path",
                        "cost_inputs_path", "parent_manifest_path"):
                value = entry.get(key)
                if value is not None:
                    paths.add(str(_safe_file(value, f"{entry.get('entry_id')}.{key}")))
            manifest_path = entry.get("parent_manifest_path")
            if manifest_path:
                manifest = json.loads(_safe_file(manifest_path, "parent_manifest_path").read_text(encoding="utf-8"))
                provenance = manifest.get("config_provenance", {})
                repository = Path(stage.get("repository_root", ""))
                for key in ("source_path", "snapshot_path", "resolved_snapshot_path"):
                    value = provenance.get(key)
                    if not value or value in {"", "."}:
                        continue
                    path = Path(value)
                    if not path.is_absolute():
                        path = repository / path
                    paths.add(str(_safe_file(str(path), f"manifest.config_provenance.{key}")))
                for child in manifest.get("child_runs", []):
                    for key in ("manifest_path", "result_path"):
                        value = child.get(key)
                        if not value:
                            continue
                        path = Path(value)
                        if not path.is_absolute():
                            path = repository / path
                        if key == "manifest_path":
                            paths.add(str(_safe_file(str(path), f"child.{key}")))
                        elif path.is_symlink() or not path.is_absolute() or ".." in path.parts:
                            raise ValueError("campaign result snapshot path is unsafe")
                        elif path.is_dir():
                            for snapshot in path.rglob("*"):
                                if snapshot.is_symlink():
                                    raise ValueError("campaign result snapshots cannot traverse symlinks")
                                if snapshot.is_file():
                                    paths.add(str(_safe_file(str(snapshot), "child result snapshot")))
            runtime_path = entry.get("runtime_config_path")
            if runtime_path:
                runtime = json.loads(_safe_file(runtime_path, "runtime_config_path").read_text(encoding="utf-8"))
                for provider in runtime.get("providers", {}).values():
                    for key in ("primary_review_path", "expiry_cost_path", "workflow_cost_handoff_path", "support_cost_path"):
                        value = provider.get("sources", {}).get(key)
                        if value:
                            paths.add(str(_safe_file(value, f"runtime.providers.sources.{key}")))
                archive = runtime.get("archive_sink", {})
                quota_path = archive.get("controller_quota_evidence_path")
                if quota_path:
                    paths.add(str(_safe_file(quota_path, "runtime.archive_sink.controller_quota_evidence_path")))
            cost_path = entry.get("cost_inputs_path")
            if cost_path:
                costs = json.loads(_safe_file(cost_path, "cost_inputs_path").read_text(encoding="utf-8"))
                source_path = costs.get("source_path")
                if source_path:
                    paths.add(str(_safe_file(source_path, "cost_inputs.source_path")))
        for key in ("prepared_stage_path",):
            value = stage.get(key)
            if value:
                paths.add(str(_safe_file(value, f"stage.{key}")))
        waiver = stage.get("failure_waiver")
        if waiver is not None:
            source = waiver.get("source", {}).get("path") if isinstance(waiver, dict) else None
            if source:
                paths.add(str(_safe_file(source, "stage.failure_waiver.source")))
    return {path: _file_digest(Path(path)) for path in sorted(paths)}


def _repository_sources(stages: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Bind the executable source/config/Terraform tree, including local edits."""
    root_paths = []
    for stage in stages:
        candidate = Path(stage.get("repository_root", ""))
        if not candidate.is_absolute() or any(part.is_symlink() for part in (candidate, *candidate.parents)):
            raise ValueError("repository root must be absolute and cannot traverse symlinks")
        root_paths.append(candidate.resolve(strict=True))
    roots = {str(path) for path in root_paths}
    result: dict[str, dict[str, str]] = {}
    for root_text in sorted(roots):
        root = Path(root_text)
        if any(part.is_symlink() for part in (root, *root.parents)):
            raise ValueError("repository root cannot traverse symlinks")
        files: dict[str, str] = {}
        candidates: list[Path] = []
        for dirname in ("src", "configs", "terraform", "tool_patches"):
            base = root / dirname
            if not base.exists():
                continue
            if base.is_symlink():
                raise ValueError(f"source tree cannot contain a symlink: {base}")
            for path in base.rglob("*"):
                if path.is_dir():
                    if path.name in {"__pycache__", "campaign-generated"}:
                        continue
                    if path.is_symlink():
                        raise ValueError(f"source tree cannot contain a symlink: {path}")
                    continue
                if path.is_symlink():
                    raise ValueError(f"source tree cannot contain a symlink: {path}")
                if any(part in {"__pycache__", "campaign-generated"} for part in path.relative_to(root).parts):
                    continue
                candidates.append(path)
        for filename in ("pyproject.toml",):
            candidate = root / filename
            if candidate.is_file():
                candidates.append(candidate)
        for path in sorted(set(candidates)):
            relative = str(path.relative_to(root))
            files[relative] = _file_digest(_safe_file(str(path.resolve(strict=True)), relative))
        result[root_text] = files
    return result


def _stage_identity(stage: Mapping[str, Any]) -> dict[str, Any]:
    """Require every execution input to survive; only gate/control state may change."""
    value = copy.deepcopy(dict(stage))
    for key in ("status", "pending_prerequisites", "approval_files_created",
                "activated_at_utc", "authorization_binding_sha256",
                "authorization_source_sha256"):
        value.pop(key, None)
    value.pop("dispatch_policy", None)
    return value


def _expected_acceptance_policy(stage: Mapping[str, Any]) -> dict[str, Any] | None:
    if stage.get("stage") != "acceptance":
        return None
    policy = stage.get("dispatch_policy")
    if not isinstance(policy, dict):
        return None
    return {key: value for key, value in policy.items() if key != "evidence_sha256"}


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return _digest_bytes(payload)


def _plan_files(plan: Mapping[str, Any]) -> dict[str, str]:
    result = {}
    for row in plan.get("plans", []):
        path = _safe_file(row.get("path"), "plan source")
        result[str(path)] = _file_digest(path)
    return result


def create_draft_bundle(*, plan: Mapping[str, Any], stages: list[dict[str, Any]],
                        output_path: Path, missing_prerequisites: Mapping[str, list[str]],
                        preparer: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Freeze already-built production stages as a clearly non-executable draft.

    A ``preparer`` is required so callers cannot accidentally use a parallel
    builder. It must be the production ``prepare_stage`` function invoked in
    its draft mode and return its real stage objects.
    """
    if preparer is None:
        raise ValueError("draft creation requires the production prepare_stage builder")
    if not stages or any(stage.get("status") != DRAFT_STATUS for stage in stages):
        raise ValueError("production preparer must mark every stage DRAFT_UNAPPROVED")
    if any(not isinstance(missing_prerequisites.get(str(stage.get("stage_id"))), list)
           for stage in stages):
        raise ValueError("list pending prerequisites for every frozen stage")
    pending_evidence: dict[str, list[str]] = {}
    for stage in stages:
        stage_id = str(stage["stage_id"])
        paths = stage.get("draft_pending_evidence_paths")
        if paths is None:
            refs = stage.get("approval_refs", {})
            paths = list(refs.values()) if isinstance(refs, dict) else []
        if not isinstance(paths, list):
            raise ValueError("production preparer approval references must be a path list/map")
        normalized = []
        for value in paths:
            if not isinstance(value, str) or not Path(value).is_absolute() or ".." in Path(value).parts:
                raise ValueError("pending approval evidence paths must be absolute and traversal-free")
            path = Path(value)
            if any(part.is_symlink() for part in (path, *path.parents)):
                raise ValueError("pending approval evidence paths cannot traverse symlinks")
            if path.exists() or path.is_symlink():
                raise ValueError("draft mode must not create approval evidence")
            normalized.append(str(path))
        pending_evidence[stage_id] = sorted(set(normalized))
    identities = [_stage_identity(stage) for stage in stages]
    bundle = {
        "schema_version": SCHEMA_VERSION,
        "status": DRAFT_STATUS,
        "created_by": "production-prepare_stage",
        "plan": dict(plan),
        "stages": stages,
        "stage_identities": identities,
        "expected_dispatch_policies": {str(stage["stage_id"]): policy
            for stage in stages if (policy := _expected_acceptance_policy(stage)) is not None},
        "missing_prerequisites": {str(stage["stage_id"]): list(missing_prerequisites[str(stage["stage_id"])])
                                   for stage in stages},
        "pending_evidence_paths": pending_evidence,
        "plan_files": _plan_files(plan),
        "artifacts": _artifact_paths(stages),
        "repository_sources": _repository_sources(stages),
    }
    bundle["draft_sha256"] = _canonical_digest(bundle)
    output = Path(output_path)
    if not output.is_absolute() or ".." in output.parts or any(part.is_symlink() for part in (output, *output.parents)):
        raise ValueError("draft output must be an absolute private path without symlink traversal")
    private_roots = {Path(stage["state_root"]).resolve(strict=True) for stage in stages}
    if len(private_roots) != 1 or output.parent.resolve() not in private_roots:
        raise ValueError("draft bundle must be stored directly in the shared private state root")
    private_root = next(iter(private_roots))
    private_root.chmod(0o700)
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"refusing to replace an existing draft bundle: {output}")
    atomic_write_json(output, bundle)
    output.chmod(0o600)
    return bundle


def load_and_verify_draft(path: Path, *, allow_evidence_for: str | None = None) -> dict[str, Any]:
    """Reject modified, symlinked, malformed, or already activated bundles."""
    source = _safe_file(str(Path(path).absolute()), "draft bundle")
    bundle = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(bundle, dict) or bundle.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported campaign draft schema")
    if bundle.get("status") != DRAFT_STATUS:
        raise ValueError("campaign draft is not awaiting activation")
    expected = bundle.get("draft_sha256")
    unhashed = {key: value for key, value in bundle.items() if key != "draft_sha256"}
    if not isinstance(expected, str) or _canonical_digest(unhashed) != expected:
        raise ValueError("campaign draft bundle digest changed")
    stages = bundle.get("stages")
    if not isinstance(stages, list) or not stages or bundle.get("stage_identities") != [
            _stage_identity(stage) for stage in stages]:
        raise ValueError("campaign draft frozen stage identity changed")
    if bundle.get("expected_dispatch_policies") != {str(stage["stage_id"]): policy
            for stage in stages if (policy := _expected_acceptance_policy(stage)) is not None}:
        raise ValueError("campaign draft dispatch-policy binding changed")
    if bundle.get("artifacts") != _artifact_paths(stages):
        raise ValueError("campaign draft source/config/runtime/cost/manifest artifacts changed")
    if bundle.get("repository_sources") != _repository_sources(stages):
        raise ValueError("campaign draft implementation/config/Terraform source tree changed")
    if bundle.get("plan_files") != _plan_files(bundle.get("plan", {})):
        raise ValueError("campaign draft plan source changed")
    for stage_id, paths in bundle.get("pending_evidence_paths", {}).items():
        marker = source.with_suffix(source.suffix + f".{stage_id}.consumed.json")
        consumed = None
        if marker.exists() or marker.is_symlink():
            consumed = json.loads(_safe_file(str(marker), "consumed-stage marker").read_text(encoding="utf-8"))
            if (consumed.get("status") != "CONSUMED" or consumed.get("draft_sha256") != expected
                    or consumed.get("stage_id") != stage_id):
                raise ValueError("campaign draft consumed-stage marker is invalid")
        for value in paths:
            path = Path(value)
            if consumed is None:
                if stage_id == allow_evidence_for:
                    _safe_file(value, "activated approval evidence")
                elif path.exists() or path.is_symlink():
                    raise ValueError(f"draft approval evidence appeared before activation: {path}")
            else:
                expected_evidence = consumed.get("approval_evidence_sha256", {}).get(value)
                if expected_evidence is None or _file_digest(_safe_file(value, "activated approval evidence")) != expected_evidence:
                    raise ValueError(f"activated approval evidence changed or disappeared: {path}")
    return bundle


def verify_activation(draft: Mapping[str, Any], stage_id: str,
                      activated_stage: dict[str, Any]) -> None:
    """Ensure genuine activation changes gate/control state only."""
    index = next((index for index, row in enumerate(draft.get("stages", []))
                  if row.get("stage_id") == stage_id), None)
    if index is None:
        raise ValueError("activated stage is not in this frozen draft")
    if _stage_identity(activated_stage) != draft.get("stage_identities", [])[index]:
        raise ValueError("activation changed frozen identities, schedules, paths or policy")
    if (activated_stage.get("status") != ACTIVATED_STATUS
            or activated_stage.get("approval_files_created") is not True
            or activated_stage.get("pending_prerequisites")):
        raise ValueError("production activation hook did not return a fully checked gate state")
    expected_policy = draft.get("expected_dispatch_policies", {}).get(stage_id)
    if expected_policy is not None:
        policy = activated_stage.get("dispatch_policy")
        if not isinstance(policy, dict) or any(policy.get(key) != value for key, value in expected_policy.items()):
            raise ValueError("activated acceptance dispatch policy changed its frozen pair/window/evidence path")
        evidence = _safe_file(policy.get("evidence_path"), "paired acceptance evidence")
        if policy.get("evidence_sha256") != _file_digest(evidence):
            raise ValueError("activated acceptance policy evidence hash does not match its bytes")


def mark_consumed(path: Path, draft: dict[str, Any], *, stage_id: str,
                  activated_stage_digest: str) -> None:
    """Atomically consume a draft once dispatch has been durably staged."""
    current = load_and_verify_draft(path, allow_evidence_for=stage_id)
    if stage_id not in current.get("pending_evidence_paths", {}):
        raise ValueError("stage_id is not part of this campaign draft")
    if current["draft_sha256"] != draft.get("draft_sha256"):
        raise ValueError("draft changed before activation could be consumed")
    consumed = {"schema_version": SCHEMA_VERSION, "status": "CONSUMED",
                "draft_sha256": draft["draft_sha256"], "stage_id": stage_id,
                "activated_stage_sha256": activated_stage_digest}
    evidence_hashes = {}
    for value in draft.get("pending_evidence_paths", {}).get(stage_id, []):
        evidence_path = _safe_file(value, "activated approval evidence")
        evidence_hashes[value] = _file_digest(evidence_path)
    consumed["approval_evidence_sha256"] = evidence_hashes
    marker = Path(path).with_suffix(Path(path).suffix + f".{stage_id}.consumed.json")
    if marker.exists() or marker.is_symlink():
        raise ValueError(f"campaign draft stage was already activated or dispatched: {stage_id}")
    atomic_write_json(marker, consumed)

