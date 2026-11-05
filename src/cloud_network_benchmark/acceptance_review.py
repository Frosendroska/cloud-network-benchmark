"""Source-backed applicability of completed supervised acceptance to later modes."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

import yaml


def _regular(path: str | Path) -> Path:
    result = Path(path)
    if not result.is_absolute() or any(p.is_symlink() for p in (result, *result.parents)) or not result.is_file():
        raise ValueError("acceptance evidence must identify a regular absolute source file")
    return result


def _digest(path: Path, identity: str | None = None) -> str:
    sha = hashlib.sha256()
    found = identity is None
    tail = b""
    token = identity.encode() if identity else b""
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
            if token and token in tail + block:
                found = True
            tail = block[-len(token):] if token else b""
    if not found:
        raise ValueError("supervised failure evidence does not identify the reviewed attempt")
    return sha.hexdigest()


def _failure_review(reference: Mapping[str, Any]) -> None:
    path = _regular(reference["path"])
    if _digest(path) != reference.get("sha256"):
        raise ValueError("registered supervised failure review changed")
    review = yaml.safe_load(path.read_text())
    checks = review.get("checks") if isinstance(review, dict) else None
    expected = {"V-A-FAIL": "aws", "V-Z-FAIL": "azure", "V-G-FAIL": "gcp"}
    if not isinstance(checks, dict) or set(checks) != set(expected) or review.get("schema_version") != 1:
        raise ValueError("registered failure review must cover V-A-FAIL, V-Z-FAIL and V-G-FAIL")
    attempts = set()
    for registered, provider in expected.items():
        row = checks[registered]
        attempt = row.get("attempt_id")
        if row.get("provider") != provider or not isinstance(attempt, str) or not attempt or attempt in attempts:
            raise ValueError("registered failure review requires distinct actual provider attempt identities")
        attempts.add(attempt)
        for action in ("server_failure", "controller_loss", "expiry", "cleanup"):
            evidence = row.get(action)
            if not isinstance(evidence, dict) or evidence.get("status") != "passed":
                raise ValueError(f"supervised {registered}/{action} has not passed")
            source = _regular(evidence.get("source_path", ""))
            if _digest(source, attempt) != evidence.get("source_sha256"):
                raise ValueError(f"supervised {registered}/{action} source hash differs")


def validate_acceptance_scope(evidence: Mapping[str, Any], campaign: Any) -> None:
    """Read real source review, manifests and validated native acceptance descriptors.

    A scope review permits different config IDs, durations and calibrated N;
    machine/network/placement intent must still match what acceptance exercised.
    """
    from .manifests import CampaignManifest, ChildRunManifest
    from .contracts.validation import ValidatorInput, ValidationResult
    from .validation import validate_bundle

    source = _regular(evidence["source_path"])
    if _digest(source) != evidence["source_sha256"]:
        raise ValueError("acceptance review source hash differs")
    declared = yaml.safe_load(source.read_text())
    references = evidence.get("accepted_campaigns")
    if (not isinstance(declared, dict) or declared.get("schema_version") != 1
        or not isinstance(references, list) or not references
        or declared.get("accepted_campaigns") != [r.get("path") for r in references]
        or declared.get("repository_root") != evidence.get("repository_root")
        or declared.get("registered_failure_review") != evidence.get("registered_failure_review", {}).get("path")):
        raise ValueError("acceptance scope wrapper does not match its actual reviewed source")
    selected = {str(p) for p in campaign.selected_providers}
    repository_root = Path(evidence["repository_root"])
    if not repository_root.is_absolute():
        raise ValueError("acceptance repository root must be absolute")
    matched = False
    for reference in references:
        path = _regular(reference["path"])
        if _digest(path) != reference.get("sha256"):
            raise ValueError("acceptance manifest source changed")
        parent = CampaignManifest.model_validate_json(path.read_bytes())
        if parent.mode != "acceptance" or parent.aggregate_state != "succeeded":
            raise ValueError("acceptance campaign has not completed successfully")
        if str(parent.scenario) != str(campaign.scenario) or {str(p) for p in parent.selected_providers} != selected:
            continue
        if evidence.get("requested_provider_scope") is not None:
            from .campaign_inputs import provider_scope
            runtime_ref = declared.get("accepted_runtime_configs", {}).get(str(path))
            if not isinstance(runtime_ref, dict):
                raise ValueError("acceptance review must identify its original frozen runtime scope")
            runtime_path = _regular(runtime_ref.get("path", ""))
            if _digest(runtime_path) != runtime_ref.get("sha256"):
                raise ValueError("accepted runtime scope source changed")
            import json
            runtime = json.loads(runtime_path.read_text())
            if Path(runtime.get("initialized_campaign_manifest", "")) != path:
                raise ValueError("accepted runtime scope belongs to another campaign")
            accepted_scope = provider_scope({p: row["options"] for p, row in runtime["providers"].items()})
            requested = {p: evidence["requested_provider_scope"][p] for p in selected}
            if accepted_scope != requested:
                raise ValueError("provider account/project or concrete image changed since acceptance")
        for ref in parent.child_runs:
            child_path = Path(ref.manifest_path)
            child = ChildRunManifest.model_validate_json(_regular(child_path if child_path.is_absolute() else repository_root / child_path).read_bytes())
            if (child.campaign_id != parent.campaign_id or child.mode != "acceptance"
                or child.execution_state != "succeeded" or child.cleanup_state != "succeeded"
                or child.validation_status.data_validity != "valid"):
                raise ValueError("acceptance child is incomplete, invalid, or not cleaned")
            result_root = Path(ref.result_path)
            if not result_root.is_absolute():
                result_root = repository_root / result_root
            validation = ValidationResult.model_validate_json(_regular(result_root / "validation/result.json").read_bytes())
            descriptor = ValidatorInput.model_validate_json(_regular(result_root / "validation/input.json").read_bytes())
            replay = validate_bundle(descriptor, evaluated_at=validation.evaluated_at)
            if (validation.run_id != child.run_id or descriptor.identity.run_id != child.run_id
                or validation.validation_status.data_validity != "valid" or replay.validation_status.data_validity != "valid"):
                raise ValueError("saved native acceptance validation is absent or invalid")
            snapshot = _regular(result_root / "config.resolved.json")
            if _digest(snapshot) != child.config_provenance.resolved_snapshot_sha256.value:
                raise ValueError("accepted resolved config snapshot changed")
            target = next(item for item in campaign.observations if str(item.provider) == str(child.provider))
            accepted = child.resolved_observation
            if accepted.vm_a != target.vm_a or accepted.vm_b != target.vm_b:
                raise ValueError("acceptance VM image/network intent differs from requested study")
            tested_placement = accepted.placement.model_dump(exclude={"name"})
            target_placement = target.placement.model_dump(exclude={"name"})
            if tested_placement != target_placement:
                raise ValueError("acceptance placement intent differs from requested study")
        matched = True
    if not matched:
        raise ValueError("no completed acceptance covers this provider/scenario scope")
    _failure_review(evidence["registered_failure_review"])
