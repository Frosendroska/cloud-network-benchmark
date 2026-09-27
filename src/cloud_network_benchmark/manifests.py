from __future__ import annotations

import hashlib
import platform
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

from pydantic import Field

from .config import resolve_campaign
from .contracts.artifacts import ArtifactLayout
from .contracts.campaign import ResolvedCampaign, ResolvedObservation
from .contracts.common import (
    CampaignState,
    CleanupState,
    ExecutionState,
    FailureEvidence,
    LifecycleEvent,
    Provider,
    StringEvidence,
    StrictModel,
    StructuredEvidence,
)
from .errors import BenchmarkError, PersistenceError
from .ids import Clock, TokenSource, candidate_campaign_id, candidate_run_id, random_token, utc_now
from .paths import (
    FaultInjector,
    allocate_paths,
    atomic_write_json,
    create_directory,
    no_fault,
    preflight,
    reserve_json,
    write_exclusive,
)
from .lifecycle import transition_cleanup, transition_execution


class GitProvenanceProvider(Protocol):
    def commit(self, repository: Path) -> StringEvidence:
        ...


class RepositoryGitProvenance:
    def commit(self, repository: Path) -> StringEvidence:
        try:
            git_dir = repository / ".git"
            if git_dir.is_file():
                pointer = git_dir.read_text(encoding="utf-8").strip()
                git_dir = (repository / pointer.split(":", 1)[1].strip()).resolve()
            head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
            if head.startswith("ref: "):
                ref = head[5:]
                ref_path = git_dir / ref
                if ref_path.exists():
                    head = ref_path.read_text(encoding="utf-8").strip()
                else:
                    packed = (git_dir / "packed-refs").read_text(encoding="utf-8")
                    head = next(line.split()[0] for line in packed.splitlines() if line.endswith(f" {ref}"))
            if not head:
                raise ValueError("empty Git HEAD")
            return StringEvidence(value=head)
        except Exception as exc:
            return StringEvidence(unavailable_reason=f"Git commit unavailable: {exc}")


class StaticGitProvenance:
    def __init__(self, commit: str) -> None:
        self._commit = commit

    def commit(self, repository: Path) -> StringEvidence:
        del repository
        return StringEvidence(value=self._commit)


class ManifestConfigProvenance(StrictModel):
    source_path: str
    sha256: str
    byte_length: int
    snapshot_path: Optional[str]
    implementation_git_commit: StringEvidence
    design_git_commit: StringEvidence


class ChildReference(StrictModel):
    provider: Provider
    run_id: str
    manifest_path: str
    result_path: str


class ChildRunManifest(StrictModel):
    manifest_type: str = "child_run"
    schema_version: int = 1
    run_id: str
    campaign_id: str
    experiment_id: str
    provider: Provider
    scenario: str
    scheduled_start: datetime
    actual_started_at: Optional[datetime] = None
    actual_finished_at: Optional[datetime] = None
    config_provenance: ManifestConfigProvenance
    resolved_observation: Dict[str, Any]
    execution_state: ExecutionState = ExecutionState.INITIALIZED
    cleanup_state: CleanupState = CleanupState.NOT_STARTED
    failure: Optional[FailureEvidence] = None
    cleanup_failure: Optional[FailureEvidence] = None
    vm_metadata: StructuredEvidence
    provider_metadata: StructuredEvidence
    tool_versions: StructuredEvidence
    artifacts: ArtifactLayout = Field(default_factory=ArtifactLayout)
    lifecycle_events: List[LifecycleEvent]
    created_at: datetime
    updated_at: datetime


class CampaignManifest(StrictModel):
    manifest_type: str = "campaign"
    schema_version: int = 1
    campaign_id: str
    experiment_id: str
    configuration_role: str
    scheduled_start: datetime
    selected_providers: List[Provider]
    scenario: str
    config_provenance: ManifestConfigProvenance
    tool_versions: StructuredEvidence
    aggregate_state: CampaignState = CampaignState.INITIALIZED
    child_runs: List[ChildReference]
    created_at: datetime
    updated_at: datetime
    lifecycle_events: List[LifecycleEvent]


class InitializedCampaign(StrictModel):
    campaign_id: str
    parent_manifest_path: str
    children: List[ChildReference]


def _relative(path: Path, repository_root: Path) -> str:
    try:
        return path.resolve().relative_to(repository_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _manifest_payload(model: StrictModel) -> Dict[str, Any]:
    return model.model_dump(mode="json")


def _failed_manifest(manifest: ChildRunManifest, now: datetime, message: str) -> ChildRunManifest:
    failure = FailureEvidence(category="initialization", message=message, occurred_at=now, details={"partial_initialization": True})
    event = LifecycleEvent(state_domain="execution", state=ExecutionState.FAILED.value, occurred_at=now, detail=message)
    return manifest.model_copy(
        update={
            "execution_state": ExecutionState.FAILED,
            "failure": failure,
            "updated_at": now,
            "lifecycle_events": [*manifest.lifecycle_events, event],
        }
    )


def update_child_execution(
    manifest_path: Path,
    target: ExecutionState,
    occurred_at: datetime,
    failure: Optional[FailureEvidence] = None,
) -> ChildRunManifest:
    manifest = ChildRunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    target = ExecutionState(target)
    event = transition_execution(ExecutionState(manifest.execution_state), target, occurred_at, failure)
    updates: Dict[str, Any] = {
        "execution_state": target,
        "updated_at": occurred_at,
        "lifecycle_events": [*manifest.lifecycle_events, event],
    }
    if target == ExecutionState.RUNNING and manifest.actual_started_at is None:
        updates["actual_started_at"] = occurred_at
    if target in {ExecutionState.SUCCEEDED, ExecutionState.FAILED, ExecutionState.INTERRUPTED}:
        updates["actual_finished_at"] = occurred_at
    if failure is not None and manifest.failure is None:
        updates["failure"] = failure
    updated = manifest.model_copy(update=updates)
    atomic_write_json(manifest_path, _manifest_payload(updated))
    return updated


def update_child_cleanup(
    manifest_path: Path,
    target: CleanupState,
    occurred_at: datetime,
    cleanup_failure: Optional[FailureEvidence] = None,
) -> ChildRunManifest:
    manifest = ChildRunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    target = CleanupState(target)
    if target == CleanupState.FAILED and cleanup_failure is None:
        raise ValueError("failed cleanup requires cleanup failure evidence")
    event = transition_cleanup(CleanupState(manifest.cleanup_state), target, occurred_at)
    updates: Dict[str, Any] = {
        "cleanup_state": target,
        "updated_at": occurred_at,
        "lifecycle_events": [*manifest.lifecycle_events, event],
    }
    if cleanup_failure is not None:
        updates["cleanup_failure"] = cleanup_failure
    updated = manifest.model_copy(update=updates)
    atomic_write_json(manifest_path, _manifest_payload(updated))
    return updated


def initialize_campaign(
    config_path: Path,
    repository_root: Path,
    results_root: Path,
    clock: Clock = utc_now,
    token_source: TokenSource = random_token,
    implementation_git: Optional[GitProvenanceProvider] = None,
    design_git: Optional[GitProvenanceProvider] = None,
    fault: FaultInjector = no_fault,
) -> InitializedCampaign:
    resolved, source = resolve_campaign(config_path, repository_root)
    now = clock()
    campaign_id = candidate_campaign_id(resolved.experiment_id, clock, token_source)
    run_ids = {Provider(provider): candidate_run_id(campaign_id, Provider(provider)) for provider in resolved.selected_providers}
    paths = allocate_paths(results_root, campaign_id, run_ids)
    preflight(paths)
    implementation = (implementation_git or RepositoryGitProvenance()).commit(repository_root)
    design_root = repository_root.parent / "Thesis"
    design = (design_git or RepositoryGitProvenance()).commit(design_root)
    unavailable = StructuredEvidence(unavailable_reason="not observed during F01 initialization")
    tool_versions = StructuredEvidence(value={"cloud_network_benchmark": "0.1.0", "python": platform.python_version()})
    assigned: List[Tuple[Provider, ChildRunManifest]] = []

    try:
        for observation in resolved.observations:
            provider = Provider(observation.provider)
            run_id = run_ids[provider]
            result_path = paths.child_results[provider]
            snapshot = result_path / "config.yaml"
            provenance = ManifestConfigProvenance(
                source_path=resolved.config_source.source_path,
                sha256=resolved.config_source.sha256,
                byte_length=resolved.config_source.byte_length,
                snapshot_path=_relative(snapshot, repository_root),
                implementation_git_commit=implementation,
                design_git_commit=design,
            )
            initial_event = LifecycleEvent(
                state_domain="execution",
                state=ExecutionState.INITIALIZED.value,
                occurred_at=now,
                detail="child manifest reserved",
            )
            manifest = ChildRunManifest(
                run_id=run_id,
                campaign_id=campaign_id,
                experiment_id=resolved.experiment_id,
                provider=provider,
                scenario=resolved.scenario,
                scheduled_start=resolved.scheduled_start,
                config_provenance=provenance,
                resolved_observation=observation.model_dump(mode="json"),
                vm_metadata=unavailable,
                provider_metadata=unavailable,
                tool_versions=tool_versions,
                lifecycle_events=[initial_event],
                created_at=now,
                updated_at=now,
            )
            reserve_json(paths.child_manifests[provider], _manifest_payload(manifest), fault)
            assigned.append((provider, manifest))
            create_directory(result_path, fault)
            write_exclusive(snapshot, source, fault)
            if hashlib.sha256(snapshot.read_bytes()).hexdigest() != resolved.config_source.sha256:
                raise PersistenceError(f"snapshot hash mismatch for {run_id}")

        children = [
            ChildReference(
                provider=provider,
                run_id=run_ids[provider],
                manifest_path=_relative(paths.child_manifests[provider], repository_root),
                result_path=_relative(paths.child_results[provider], repository_root),
            )
            for provider in run_ids
        ]
        parent_provenance = ManifestConfigProvenance(
            source_path=resolved.config_source.source_path,
            sha256=resolved.config_source.sha256,
            byte_length=resolved.config_source.byte_length,
            snapshot_path=None,
            implementation_git_commit=implementation,
            design_git_commit=design,
        )
        parent = CampaignManifest(
            campaign_id=campaign_id,
            experiment_id=resolved.experiment_id,
            configuration_role=resolved.configuration_role,
            scheduled_start=resolved.scheduled_start,
            selected_providers=resolved.selected_providers,
            scenario=resolved.scenario,
            config_provenance=parent_provenance,
            tool_versions=tool_versions,
            child_runs=children,
            lifecycle_events=[LifecycleEvent(state_domain="campaign", state=CampaignState.INITIALIZED.value, occurred_at=now)],
            created_at=now,
            updated_at=now,
        )
        reserve_json(paths.campaign_manifest, _manifest_payload(parent), fault)
        return InitializedCampaign(
            campaign_id=campaign_id,
            parent_manifest_path=_relative(paths.campaign_manifest, repository_root),
            children=children,
        )
    except Exception as exc:
        recovery_time = clock()
        assigned_by_provider = {provider: manifest for provider, manifest in assigned}
        for observation in resolved.observations:
            provider = Provider(observation.provider)
            manifest_path = paths.child_manifests[provider]
            if not manifest_path.exists():
                continue
            manifest = assigned_by_provider.get(provider)
            if manifest is None:
                try:
                    manifest = ChildRunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
            failed = _failed_manifest(manifest, recovery_time, str(exc))
            try:
                atomic_write_json(manifest_path, _manifest_payload(failed))
            except BenchmarkError:
                pass
        if paths.campaign_manifest.exists():
            try:
                parent = CampaignManifest.model_validate_json(paths.campaign_manifest.read_text(encoding="utf-8"))
                parent_event = LifecycleEvent(
                    state_domain="campaign",
                    state=CampaignState.FAILED.value,
                    occurred_at=recovery_time,
                    detail=f"partial initialization failed: {exc}",
                )
                failed_parent = parent.model_copy(
                    update={
                        "aggregate_state": CampaignState.FAILED,
                        "updated_at": recovery_time,
                        "lifecycle_events": [*parent.lifecycle_events, parent_event],
                    }
                )
                atomic_write_json(paths.campaign_manifest, _manifest_payload(failed_parent))
            except Exception:
                pass
        if isinstance(exc, BenchmarkError):
            raise
        raise PersistenceError(f"campaign initialization failed: {exc}") from exc
