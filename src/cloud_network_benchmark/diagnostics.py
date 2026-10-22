"""Typed controller boundary for the standalone local diagnostics collector."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from .bootstrap import BootstrapResult
from .contracts.artifacts import ArtifactInput
from .contracts.common import ArtifactExpectation, Evidence, Provider, StringEvidence, StructuredEvidence, VmRole
from .contracts.deployment import ProviderDeploymentOutput
from .contracts.measurements import ClockMapping, DiagnosticSample, VmMetadata


@dataclass
class DiagnosticCollectionResult:
    deployment_id: str
    vm_role: str
    state: str
    sample_count: int
    mapping_count: int
    artifacts: list[ArtifactInput] = field(default_factory=list)
    closed_paths: list[str] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)
    metadata: VmMetadata | None = None


def _role(value):
    role = getattr(value, "value", value)
    if role not in ("vm_a", "vm_b"):
        raise ValueError("role must be vm_a or vm_b")
    return role


def _evidence(value, reason):
    return value if value is not None else {"value": None, "unavailable_reason": reason}


def build_collection_context(deployment: ProviderDeploymentOutput, *, role: str, interface: str,
                             output_root: str | Path, duration_seconds: float,
                             prepared: BootstrapResult | None = None) -> dict:
    role = _role(role)
    if deployment.schema_version != 2 or deployment.deployment_id != deployment.identity.deployment_id:
        raise ValueError("deployment identity or schema is inconsistent")
    identity_provider = getattr(deployment.identity, "provider", None)
    if identity_provider is not None and Provider(identity_provider) != Provider(deployment.provider):
        raise ValueError("deployment provider differs from identity provider")
    if prepared is not None and (prepared.deployment_id != deployment.deployment_id or prepared.identity != deployment.identity):
        raise ValueError("prepared build identity does not match deployment")
    if isinstance(duration_seconds, bool) or not isinstance(duration_seconds, (int, float)) or not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("duration_seconds must be finite and positive")
    vm = deployment.vm_a if role == "vm_a" else deployment.vm_b
    provider = Provider(deployment.provider).value
    builds = {}
    if prepared:
        selected = [item for item in prepared.builds if item.role == role]
        if len({item.tool for item in selected}) != len(selected):
            raise ValueError("duplicate prepared build observations for role")
        for item in selected:
            builds[item.tool] = {
                "source_reference": item.source_reference, "source_sha256": item.source_sha256,
                "compiler_version": item.compiler_version, "binary_path": item.binary_path,
                "binary_version": item.binary_version, "binary_sha256": item.binary_sha256,
            }
    metadata = VmMetadata(
        schema_version=2, deployment_id=deployment.deployment_id, vm_role=role, provider=provider,
        cpu_model=StringEvidence(unavailable_reason="guest observation pending"),
        nic_driver=StringEvidence(unavailable_reason="guest observation pending"),
        acceleration=StringEvidence(unavailable_reason="guest observation pending"),
        image=StringEvidence(value=vm.image_identity) if vm.image_identity else StringEvidence(unavailable_reason="provider image identity unavailable"),
        kernel=StringEvidence(unavailable_reason="guest observation pending"),
        congestion_control=StringEvidence(unavailable_reason="guest observation pending"),
        tool_versions=StructuredEvidence(unavailable_reason="guest tool observation pending"),
        source_builds=StructuredEvidence(value=builds) if builds else StructuredEvidence(unavailable_reason="bootstrap build evidence unavailable"),
        documented_network_limit=deployment.documented_network_limit,
        mtu=Evidence[int](unavailable_reason="guest observation pending"),
        observed_region=StringEvidence(value=vm.region) if vm.region else StringEvidence(unavailable_reason="provider region unavailable"),
        observed_zone=StringEvidence(value=vm.zone) if vm.zone else StringEvidence(unavailable_reason="provider zone unavailable"),
        physical_zone_id=vm.physical_zone_id,
        actual_shape=StringEvidence(value=vm.actual_shape) if vm.actual_shape else StringEvidence(unavailable_reason="provider shape unavailable"),
    ).model_dump(mode="json")
    context = {"deployment_id": deployment.deployment_id, "vm_role": role, "provider": provider,
               "interface": interface, "output_root": str(output_root), "duration_seconds": float(duration_seconds),
               "metadata_seed": metadata}
    # Validate using the exact standalone guest rules without importing anything into that file.
    from .diagnostics_guest import validate_context
    validate_context(context)
    return context


def _iter_models(path: str | Path, model) -> Iterator:
    path = Path(path)
    with path.open("r", encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.endswith("\n"):
                raise ValueError(f"{path}:{number}: incomplete JSONL line")
            try:
                yield model.model_validate_json(line)
            except Exception as exc:
                raise ValueError(f"{path}:{number}: invalid {model.__name__}: {exc}") from exc


def iter_samples(path: str | Path) -> Iterator[DiagnosticSample]:
    yield from _iter_models(path, DiagnosticSample)


def iter_clock_mappings(path: str | Path) -> Iterator[ClockMapping]:
    yield from _iter_models(path, ClockMapping)


def alignment_eligibility(mapping_a: ClockMapping, mapping_b: ClockMapping) -> tuple[bool, list[str]]:
    reasons = []
    for expected, mapping in (("vm_a", mapping_a), ("vm_b", mapping_b)):
        if mapping.vm_role != expected:
            reasons.append(f"expected {expected} clock mapping")
        source = mapping.synchronization_source.value
        if not source or not source.startswith("chronyc tracking synchronized;"):
            reasons.append(f"{expected} synchronization source is not observed synchronized chrony")
        error = mapping.estimated_error_ms.value
        if error is None:
            reasons.append(f"{expected} clock error is unavailable")
        elif not math.isfinite(error) or error < 0 or error > 100:
            reasons.append(f"{expected} clock error exceeds 100 ms or is invalid")
    return not reasons, reasons


def _declarations(role):
    return [
        ArtifactInput(artifact_id=f"{role}_metadata", path=f"metadata/{role}.json", kind="vm_metadata", producer=role, unit_id=None, expectation=ArtifactExpectation.REQUIRED),
        ArtifactInput(artifact_id=f"{role}_diagnostics", path=f"diagnostics/{role}.jsonl", kind="diagnostics", producer=role, unit_id=None, expectation=ArtifactExpectation.REQUIRED),
        ArtifactInput(artifact_id=f"{role}_clock", path=f"diagnostics/{role}-clock.jsonl", kind="clock_mapping", producer=role, unit_id=None, expectation=ArtifactExpectation.REQUIRED),
    ]


def load_collection_result(context: dict, outcome: dict) -> DiagnosticCollectionResult:
    deployment, role = context["deployment_id"], _role(context["vm_role"])
    failures = list(outcome.get("failures") or [])
    if outcome.get("deployment_id") != deployment or outcome.get("vm_role") != role:
        failures.append({"category":"identity_mismatch", "message":"collector outcome identity differs from context"})
    state = outcome.get("state")
    if state not in ("completed", "interrupted", "failed"):
        failures.append({"category":"invalid_outcome", "message":"unknown collector state"}); state = "failed"
    samples = mappings = 0
    root = Path(context["output_root"])
    metadata = None
    expected = {"metadata":root/f"metadata/{role}.json", "diagnostics":root/f"diagnostics/{role}.jsonl", "clock":root/f"diagnostics/{role}-clock.jsonl"}
    declared_paths = outcome.get("artifacts") or {}
    for key, path in expected.items():
        if declared_paths.get(key) != str(path):
            failures.append({"category":"artifact_declaration_mismatch", "message":f"outcome path differs for {key}"})
        try: path.resolve().relative_to(root.resolve())
        except ValueError:
            failures.append({"category":"artifact_escape", "message":f"{key} artifact escapes output root"}); continue
        if any(part.is_symlink() for part in (path, path.parent)):
            failures.append({"category":"artifact_symlink", "message":f"{key} artifact path contains a symlink"}); continue
        if not path.is_file():
            failures.append({"category":"missing_artifact", "message":f"missing {key} artifact"}); continue
        try:
            if key == "metadata":
                metadata = VmMetadata.model_validate_json(path.read_text(encoding="utf-8"))
                if metadata.deployment_id != deployment or metadata.vm_role != role:
                    raise ValueError("metadata identity differs from context")
            elif key == "diagnostics":
                for item in iter_samples(path):
                    samples += 1
                    if item.deployment_id != deployment or item.vm_role != role: raise ValueError(f"{path}: sample identity mismatch")
            else:
                for item in iter_clock_mappings(path):
                    mappings += 1
                    if item.vm_role != role: raise ValueError(f"{path}: clock role mismatch")
        except Exception as exc:
            failures.append({"category":"artifact_validation", "message":str(exc)[:500]})
    declared_samples, declared_mappings = outcome.get("sample_count"), outcome.get("mapping_count")
    if isinstance(declared_samples, bool) or not isinstance(declared_samples, int) or samples != declared_samples:
        failures.append({"category":"count_mismatch", "message":"diagnostic sample count differs from outcome"})
    if isinstance(declared_mappings, bool) or not isinstance(declared_mappings, int) or mappings != declared_mappings:
        failures.append({"category":"count_mismatch", "message":"clock mapping count differs from outcome"})
    closed = set(outcome.get("closed_paths") or [])
    for path in expected.values():
        if str(path) not in closed:
            failures.append({"category":"closure_mismatch", "message":f"artifact not reported closed: {path.name}"})
    if state == "completed" and failures: state = "failed"
    return DiagnosticCollectionResult(deployment, role, state, samples, mappings, _declarations(role), sorted(closed), failures, metadata)
