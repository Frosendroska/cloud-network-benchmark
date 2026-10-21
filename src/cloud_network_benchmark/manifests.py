from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Literal, Optional, Protocol, Tuple

from pydantic import Field, model_validator

from .config import resolve_campaign
from .contracts.artifacts import ArtifactLayout, ArtifactRecord
from .contracts.admission import AdmissionRequirements, ExpiryEvidence, ReservationEvidence, ScheduleEntry
from .contracts.campaign import PhaseConfiguration, ResolvedCampaign, ResolvedObservation
from .contracts.common import (
    CampaignState,
    CleanupState,
    ExecutionState,
    ExperimentMode,
    Evidence,
    FailureEvidence,
    GitSnapshot,
    LifecycleEvent,
    Provider,
    Scenario,
    ValidationStatus,
    CommandOutcome,
    StringEvidence,
    StrictModel,
    StructuredEvidence,
    validate_event_chronology,
)
from .errors import BenchmarkError, CollisionError, PersistenceError
from .contracts.measurements import CommandEvidence, MeasurementTiming
from .ids import Clock, TokenSource, candidate_campaign_id, candidate_deployment_id, candidate_observation_id, candidate_run_id, random_token, utc_now
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
from .lifecycle import aggregate_campaign, transition_cleanup, transition_execution


class GitProvenanceProvider(Protocol):
    def commit(self, repository: Path) -> StringEvidence:
        ...

    def snapshot(self, repository: Path) -> GitSnapshot:
        ...


def _snapshot_or_unavailable(provider: GitProvenanceProvider, repository: Path, commit: StringEvidence) -> GitSnapshot:
    snapshotter = getattr(provider, "snapshot", None)
    if callable(snapshotter):
        return snapshotter(repository)
    reason = "provider implements commit() only; working-tree evidence unavailable"
    return GitSnapshot(commit=commit, dirty=Evidence[bool](unavailable_reason=reason),
                       diff_sha256=StringEvidence(unavailable_reason=reason))


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
                git_dirs = [git_dir]
                common_dir_file = git_dir / "commondir"
                if common_dir_file.is_file():
                    common_dir = Path(common_dir_file.read_text(encoding="utf-8").strip())
                    if not common_dir.is_absolute():
                        common_dir = git_dir / common_dir
                    resolved_common_dir = common_dir.resolve()
                    if resolved_common_dir != git_dir:
                        git_dirs.append(resolved_common_dir)

                for refs_dir in git_dirs:
                    ref_path = refs_dir / ref
                    if ref_path.is_file():
                        head = ref_path.read_text(encoding="utf-8").strip()
                        break
                else:
                    for refs_dir in git_dirs:
                        packed_refs = refs_dir / "packed-refs"
                        if not packed_refs.is_file():
                            continue
                        matching_ref = next(
                            (
                                line.split()[0]
                                for line in packed_refs.read_text(encoding="utf-8").splitlines()
                                if line and not line.startswith(("#", "^")) and line.split()[-1] == ref
                            ),
                            None,
                        )
                        if matching_ref is not None:
                            head = matching_ref
                            break
                    else:
                        raise ValueError(f"Git ref not found: {ref}")
            if not head:
                raise ValueError("empty Git HEAD")
            return StringEvidence(value=head)
        except Exception as exc:
            return StringEvidence(unavailable_reason=f"Git commit unavailable: {exc}")

    def snapshot(self, repository: Path) -> GitSnapshot:
        commit = self.commit(repository)
        if commit.value is None:
            unavailable = commit.unavailable_reason or "Git commit unavailable"
            return GitSnapshot(commit=commit, dirty=Evidence[bool](unavailable_reason=unavailable),
                               diff_sha256=StringEvidence(unavailable_reason=unavailable))
        try:
            def git(*args: str) -> bytes:
                return subprocess.run(["git", "-C", str(repository), *args], check=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
            tracked = git("diff", "HEAD", "--binary")
            names = git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
            pieces = [b"tracked\0", len(tracked).to_bytes(8, "big"), tracked]
            untracked = []
            for name in sorted(item for item in names if item):
                path = repository / os.fsdecode(name)
                try:
                    content = path.read_bytes()
                except OSError as exc:
                    raise OSError(f"untracked file changed or unreadable: {os.fsdecode(name)}") from exc
                untracked.append((name, content))
            for name, content in untracked:
                pieces.extend((b"untracked\0", len(name).to_bytes(8, "big"), name,
                               len(content).to_bytes(8, "big"), content))
            digest = hashlib.sha256(b"".join(pieces)).hexdigest()
            dirty = bool(tracked or untracked)
            return GitSnapshot(commit=commit, dirty=Evidence[bool](value=dirty), diff_sha256=StringEvidence(value=digest))
        except Exception as exc:
            reason = f"Git working-tree snapshot unavailable: {type(exc).__name__}"
            return GitSnapshot(commit=commit, dirty=Evidence[bool](unavailable_reason=reason),
                               diff_sha256=StringEvidence(unavailable_reason=reason))


class StaticGitProvenance:
    def __init__(self, commit: str) -> None:
        self._commit = commit

    def commit(self, repository: Path) -> StringEvidence:
        del repository
        return StringEvidence(value=self._commit)

    def snapshot(self, repository: Path) -> GitSnapshot:
        del repository
        return GitSnapshot(commit=StringEvidence(value=self._commit), dirty=Evidence[bool](value=False),
                           diff_sha256=StringEvidence(value=hashlib.sha256(b"tracked\0" + (0).to_bytes(8, "big")).hexdigest()))


class ManifestConfigProvenance(StrictModel):
    source_path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    byte_length: int = Field(gt=0)
    snapshot_path: Optional[str] = Field(min_length=1)
    resolved_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    resolved_snapshot_sha256: StringEvidence
    resolved_snapshot_path: Optional[str] = Field(min_length=1)
    schedule_sha256: StringEvidence = Field(default_factory=lambda: StringEvidence(unavailable_reason="no schedule row supplied"))
    implementation_git_commit: StringEvidence
    design_git_commit: StringEvidence
    implementation_git: GitSnapshot = Field(default_factory=lambda: GitSnapshot(
        commit=StringEvidence(unavailable_reason="not captured"),
        dirty=Evidence[bool](unavailable_reason="not captured"),
        diff_sha256=StringEvidence(unavailable_reason="not captured"),
    ))
    design_git: GitSnapshot = Field(default_factory=lambda: GitSnapshot(
        commit=StringEvidence(unavailable_reason="not captured"),
        dirty=Evidence[bool](unavailable_reason="not captured"),
        diff_sha256=StringEvidence(unavailable_reason="not captured"),
    ))


class ChildReference(StrictModel):
    provider: Provider
    run_id: str = Field(min_length=1, pattern=r"^\S+$")
    manifest_path: str = Field(min_length=1)
    result_path: str = Field(min_length=1)

    @model_validator(mode="after")
    def normalized_paths(self) -> "ChildReference":
        for name, value in (("manifest_path", self.manifest_path), ("result_path", self.result_path)):
            if "\\" in value:
                raise ValueError(f"{name} must use POSIX separators")
            parsed = PurePosixPath(value)
            if parsed.as_posix() != value or any(part in {".", ".."} for part in parsed.parts):
                raise ValueError(f"{name} must be normalized and must not traverse directories")
        if PurePosixPath(self.manifest_path).name != f"{self.run_id}.json":
            raise ValueError("manifest_path must identify the referenced run_id")
        if PurePosixPath(self.result_path).name.rstrip("/") != self.run_id:
            raise ValueError("result_path must identify the referenced run_id")
        return self


class ChildRunManifest(StrictModel):
    manifest_type: Literal["child_run"] = "child_run"
    schema_version: Literal[2] = 2
    config_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    mode: ExperimentMode
    context: Literal["standard", "failure_check", "replacement"]
    run_id: str = Field(min_length=1, pattern=r"^\S+$")
    campaign_id: str = Field(min_length=1, pattern=r"^\S+$")
    experiment_id: str = Field(pattern=r"^EXP-[0-9]{3,}$")
    provider: Provider
    scenario: Scenario
    observation_id: str = Field(min_length=1)
    deployment_id: str = Field(min_length=1)
    replaces_run_id: Optional[str] = None
    scheduled_start: Optional[datetime]
    schedule_entry: Evidence[ScheduleEntry]
    measurement_units: List[PhaseConfiguration] = Field(default_factory=list)
    validation_status: ValidationStatus = Field(default_factory=ValidationStatus)
    commands: List[CommandEvidence] = Field(default_factory=list)
    timings: List[MeasurementTiming] = Field(default_factory=list)
    artifact_records: List[ArtifactRecord] = Field(default_factory=list)
    reservation: Evidence[ReservationEvidence] = Field(default_factory=lambda: Evidence[ReservationEvidence](unavailable_reason="not evaluated during local initialization"))
    expiry: Evidence[ExpiryEvidence] = Field(default_factory=lambda: Evidence[ExpiryEvidence](unavailable_reason="not registered during local initialization"))
    actual_started_at: Optional[datetime] = None
    actual_finished_at: Optional[datetime] = None
    config_provenance: ManifestConfigProvenance
    resolved_observation: ResolvedObservation
    execution_state: ExecutionState = ExecutionState.INITIALIZED
    cleanup_state: CleanupState = CleanupState.NOT_STARTED
    failure: Optional[FailureEvidence] = None
    retrieval_failure: Optional[FailureEvidence] = None
    cleanup_failure: Optional[FailureEvidence] = None
    vm_metadata: StructuredEvidence
    provider_metadata: StructuredEvidence
    tool_versions: StructuredEvidence
    artifacts: ArtifactLayout = Field(default_factory=ArtifactLayout)
    lifecycle_events: List[LifecycleEvent]
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def lifecycle_matches_current_state(self) -> "ChildRunManifest":
        timestamps = [self.created_at, self.updated_at]
        if self.scheduled_start is not None:
            timestamps.append(self.scheduled_start)
        timestamps.extend(value for value in (self.actual_started_at, self.actual_finished_at) if value is not None)
        if any(value.tzinfo is None or value.utcoffset() is None for value in timestamps):
            raise ValueError("manifest timestamps must include a timezone")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        validate_event_chronology(self.lifecycle_events)
        if any(
            event.occurred_at < self.created_at or event.occurred_at > self.updated_at
            for event in self.lifecycle_events
        ):
            raise ValueError("lifecycle event time must be between created_at and updated_at")
        if self.actual_started_at is not None and not (
            self.created_at <= self.actual_started_at <= self.updated_at
        ):
            raise ValueError("actual_started_at must be between created_at and updated_at")
        if self.actual_finished_at is not None and not (
            self.created_at <= self.actual_finished_at <= self.updated_at
        ):
            raise ValueError("actual_finished_at must be between created_at and updated_at")
        if (
            self.actual_started_at is not None
            and self.actual_finished_at is not None
            and self.actual_finished_at < self.actual_started_at
        ):
            raise ValueError("actual_finished_at must not precede actual_started_at")
        execution_events = [event for event in self.lifecycle_events if event.state_domain == "execution"]
        cleanup_events = [event for event in self.lifecycle_events if event.state_domain == "cleanup"]
        if not execution_events or execution_events[-1].state != self.execution_state:
            raise ValueError("latest execution lifecycle event must match execution_state")
        if cleanup_events:
            if cleanup_events[-1].state != self.cleanup_state:
                raise ValueError("latest cleanup lifecycle event must match cleanup_state")
        elif self.cleanup_state != CleanupState.NOT_STARTED.value:
            raise ValueError("non-initial cleanup state requires a cleanup lifecycle event")
        if self.execution_state in {ExecutionState.FAILED.value, ExecutionState.INTERRUPTED.value} and self.failure is None:
            raise ValueError("failed or interrupted execution state requires failure evidence")
        if self.execution_state not in {
            ExecutionState.FAILED.value,
            ExecutionState.INTERRUPTED.value,
        } and self.failure is not None:
            raise ValueError("failure evidence is only valid for failed or interrupted execution")
        if self.cleanup_state == CleanupState.FAILED.value and self.cleanup_failure is None:
            raise ValueError("failed cleanup state requires cleanup failure evidence")
        if self.cleanup_state != CleanupState.FAILED.value and self.cleanup_failure is not None:
            raise ValueError("cleanup failure evidence requires failed cleanup state")
        for evidence in (self.failure, self.cleanup_failure):
            if evidence is not None and not (self.created_at <= evidence.occurred_at <= self.updated_at):
                raise ValueError("failure evidence time must be between created_at and updated_at")
        observation = self.resolved_observation
        if (
            observation.config_id != self.config_id
            or observation.experiment_id != self.experiment_id
            or observation.mode != self.mode
            or observation.context != self.context
            or observation.provider != self.provider
            or observation.scenario != self.scenario
            or observation.scheduled_start != self.scheduled_start
        ):
            raise ValueError("resolved_observation identity must match the child manifest")
        if self.run_id != candidate_run_id(self.campaign_id, Provider(self.provider)):
            raise ValueError("run_id must match campaign_id and provider")
        if self.observation_id == self.run_id or not self.observation_id.endswith("-observation"):
            raise ValueError("observation_id must identify a stable observation unit")
        if self.deployment_id != f"{self.run_id}-deployment":
            raise ValueError("deployment_id must be unique to this attempt")
        if self.replaces_run_id is not None and self.context != "replacement":
            raise ValueError("replaces_run_id requires replacement context")
        identities = [(item.artifact_id, item.path, item.unit_id) for item in self.artifact_records]
        if len(identities) != len(set(identities)):
            raise ValueError("artifact records must have unique concrete identity")
        if any(item.deployment_id != self.deployment_id for item in self.artifact_records):
            raise ValueError("artifact records must match child deployment_id")
        if any(item.deployment_id != self.deployment_id for item in self.timings):
            raise ValueError("timings must match child deployment_id")
        return self


class CampaignManifest(StrictModel):
    manifest_type: Literal["campaign"] = "campaign"
    schema_version: Literal[2] = 2
    config_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    mode: ExperimentMode
    context: Literal["standard", "failure_check", "replacement"]
    campaign_id: str = Field(min_length=1, pattern=r"^\S+$")
    experiment_id: str = Field(pattern=r"^EXP-[0-9]{3,}$")
    configuration_role: Literal["experiment", "test"]
    scheduled_start: Optional[datetime]
    schedule_entry: Evidence[ScheduleEntry]
    admission: AdmissionRequirements
    reservation: Evidence[ReservationEvidence] = Field(default_factory=lambda: Evidence[ReservationEvidence](unavailable_reason="not evaluated during local initialization"))
    selected_providers: List[Provider] = Field(min_length=1, max_length=3)
    scenario: Scenario
    config_provenance: ManifestConfigProvenance
    tool_versions: StructuredEvidence
    aggregate_state: CampaignState = CampaignState.INITIALIZED
    child_runs: List[ChildReference] = Field(max_length=3)
    created_at: datetime
    updated_at: datetime
    lifecycle_events: List[LifecycleEvent]

    @model_validator(mode="after")
    def lifecycle_matches_aggregate(self) -> "CampaignManifest":
        timestamps = [self.created_at, self.updated_at]
        if self.scheduled_start is not None:
            timestamps.append(self.scheduled_start)
        if any(value.tzinfo is None or value.utcoffset() is None for value in timestamps):
            raise ValueError("manifest timestamps must include a timezone")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        validate_event_chronology(self.lifecycle_events)
        if any(
            event.occurred_at < self.created_at or event.occurred_at > self.updated_at
            for event in self.lifecycle_events
        ):
            raise ValueError("lifecycle event time must be between created_at and updated_at")
        if not self.lifecycle_events or self.lifecycle_events[-1].state != self.aggregate_state:
            raise ValueError("latest campaign lifecycle event must match aggregate_state")
        if len(set(self.selected_providers)) != len(self.selected_providers):
            raise ValueError("selected_providers must be unique")
        child_providers = [reference.provider for reference in self.child_runs]
        if len(set(child_providers)) != len(child_providers):
            raise ValueError("child run providers must be unique")
        selected = set(self.selected_providers)
        for reference in self.child_runs:
            if reference.provider not in selected:
                raise ValueError("child run provider must be selected by the campaign")
            if reference.run_id != candidate_run_id(self.campaign_id, Provider(reference.provider)):
                raise ValueError("child run_id must match campaign_id and provider")
        if len({item.manifest_path for item in self.child_runs}) != len(self.child_runs):
            raise ValueError("child manifest paths must be unique")
        if len({item.result_path for item in self.child_runs}) != len(self.child_runs):
            raise ValueError("child result paths must be unique")
        if self.aggregate_state == CampaignState.SUCCEEDED.value and set(child_providers) != selected:
            raise ValueError("succeeded campaign must reference every selected provider exactly once")
        return self


class InitializedCampaign(StrictModel):
    campaign_id: str
    parent_manifest_path: str
    children: List[ChildReference]


class InitializationRecovery(StrictModel):
    campaign_id: str
    parent_manifest_path: Optional[str]
    preserved_child_manifest_paths: List[str]
    recovery_failures: List[str]


def thesis_repository_root(repository_root: Path) -> Path:
    configured = os.environ.get("THESIS_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()

    git_marker = repository_root / ".git"
    if git_marker.is_file():
        pointer = git_marker.read_text(encoding="utf-8").strip()
        if pointer.startswith("gitdir:"):
            git_dir = Path(pointer.split(":", 1)[1].strip())
            if not git_dir.is_absolute():
                git_dir = repository_root / git_dir
            git_dir = git_dir.resolve()
            common_dir_file = git_dir / "commondir"
            if common_dir_file.is_file():
                common_dir = Path(common_dir_file.read_text(encoding="utf-8").strip())
                if not common_dir.is_absolute():
                    common_dir = git_dir / common_dir
                main_git_dir = common_dir.resolve()
                return main_git_dir.parent.parent / "Thesis"

    return repository_root.parent / "Thesis"


def _relative(path: Path, repository_root: Path) -> str:
    try:
        return path.resolve().relative_to(repository_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _manifest_payload(model: StrictModel) -> Dict[str, Any]:
    return model.model_dump(mode="json")


def _validated_copy(model: StrictModel, updates: Dict[str, Any]) -> Any:
    candidate = model.model_copy(update=updates)
    return type(model).model_validate(candidate.model_dump())


def _failed_manifest(manifest: ChildRunManifest, now: datetime, message: str) -> ChildRunManifest:
    failure = FailureEvidence(category="initialization", message=message, occurred_at=now, details={"partial_initialization": True})
    event = LifecycleEvent(state_domain="execution", state=ExecutionState.FAILED.value, occurred_at=now, detail=message)
    return _validated_copy(
        manifest,
        {
            "execution_state": ExecutionState.FAILED,
            "failure": failure,
            "updated_at": now,
            "lifecycle_events": [*manifest.lifecycle_events, event],
        },
    )


def _parent_manifest_path(child_manifest_path: Path, campaign_id: str) -> Path:
    return child_manifest_path.parent / f"{campaign_id}.json"


def _resolve_reference_path(parent_manifest_path: Path, reference_path: str) -> Path:
    path = Path(reference_path)
    if path.is_absolute():
        return path
    if path.parts[:2] != ("results", "manifests"):
        raise PersistenceError("child manifest reference must be absolute or rooted at results/manifests")
    results_root = parent_manifest_path.parent.parent
    if results_root.name == "results":
        repository_root = results_root.parent
    else:
        repository_root = results_root
    return repository_root.joinpath(*path.parts)


def recompute_parent_manifest(
    parent_manifest_path: Path,
    occurred_at: datetime,
    detail: str,
    fault: FaultInjector = no_fault,
) -> CampaignManifest:
    try:
        parent = CampaignManifest.model_validate_json(parent_manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PersistenceError("parent manifest is unavailable or invalid") from exc
    if occurred_at < parent.updated_at:
        raise ValueError("occurred_at cannot be earlier than the manifest updated_at")
    references = {Provider(reference.provider): reference for reference in parent.child_runs}
    selected = {Provider(provider) for provider in parent.selected_providers}
    if set(references) != selected:
        raise PersistenceError("parent manifest does not reference every selected provider")
    children = []
    for provider in parent.selected_providers:
        reference = references[Provider(provider)]
        child_path = _resolve_reference_path(parent_manifest_path, reference.manifest_path)
        if child_path.name != f"{reference.run_id}.json":
            raise PersistenceError("referenced child manifest path does not match run_id")
        if not child_path.exists():
            raise PersistenceError("referenced child manifest is missing", {"run_id": reference.run_id})
        try:
            child = ChildRunManifest.model_validate_json(child_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise PersistenceError(
                "referenced child manifest is invalid",
                {"run_id": reference.run_id},
            ) from exc
        if (
            child.run_id != reference.run_id
            or child.campaign_id != parent.campaign_id
            or child.provider != reference.provider
            or child.experiment_id != parent.experiment_id
            or child.scenario != parent.scenario
            or child.config_id != parent.config_id
            or child.mode != parent.mode
            or child.context != parent.context
            or child.scheduled_start != parent.scheduled_start
        ):
            raise PersistenceError(
                "referenced child manifest identity does not match its campaign",
                {"run_id": reference.run_id},
            )
        expected_result_path = child_path.parent.parent / "raw" / reference.run_id
        stored_result_path = Path(reference.result_path)
        if stored_result_path.is_absolute():
            matches_result = stored_result_path == expected_result_path
        else:
            matches_result = stored_result_path == Path("results/raw") / reference.run_id
        if not matches_result:
            raise PersistenceError("referenced child result path does not match its run_id", {"run_id": reference.run_id})
        children.append((child.execution_state, child.cleanup_state))
    aggregate = aggregate_campaign(children)
    event = LifecycleEvent(
        state_domain="campaign",
        state=aggregate.value,
        occurred_at=occurred_at,
        detail=detail,
    )
    updated = _validated_copy(
        parent,
        {
            "aggregate_state": aggregate,
            "updated_at": occurred_at,
            "lifecycle_events": [*parent.lifecycle_events, event],
        },
    )
    atomic_write_json(parent_manifest_path, _manifest_payload(updated), fault)
    return updated


def update_child_evidence(
    manifest_path: Path,
    occurred_at: datetime,
    *,
    vm_metadata: Optional[StructuredEvidence] = None,
    provider_metadata: Optional[StructuredEvidence] = None,
    tool_versions: Optional[StructuredEvidence] = None,
    fault: FaultInjector = no_fault,
) -> ChildRunManifest:
    if vm_metadata is None and provider_metadata is None and tool_versions is None:
        raise ValueError("at least one evidence field must be supplied")
    manifest = ChildRunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    if occurred_at < manifest.updated_at:
        raise ValueError("occurred_at cannot be earlier than the manifest updated_at")
    updates: Dict[str, Any] = {"updated_at": occurred_at}
    for name, value in (
        ("vm_metadata", vm_metadata),
        ("provider_metadata", provider_metadata),
        ("tool_versions", tool_versions),
    ):
        if value is not None:
            updates[name] = value
    updated = _validated_copy(manifest, updates)
    atomic_write_json(manifest_path, _manifest_payload(updated), fault)
    return updated


def update_child_execution(
    manifest_path: Path,
    target: ExecutionState,
    occurred_at: datetime,
    failure: Optional[FailureEvidence] = None,
    parent_manifest_path: Optional[Path] = None,
    fault: FaultInjector = no_fault,
) -> ChildRunManifest:
    manifest = ChildRunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    if occurred_at < manifest.updated_at:
        raise ValueError("occurred_at cannot be earlier than the manifest updated_at")
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
    updated = _validated_copy(manifest, updates)
    atomic_write_json(manifest_path, _manifest_payload(updated), fault)
    parent_path = parent_manifest_path or _parent_manifest_path(manifest_path, manifest.campaign_id)
    if parent_path.exists():
        recompute_parent_manifest(parent_path, occurred_at, f"child {manifest.run_id} execution -> {target.value}", fault)
    return updated


def update_child_cleanup(
    manifest_path: Path,
    target: CleanupState,
    occurred_at: datetime,
    cleanup_failure: Optional[FailureEvidence] = None,
    parent_manifest_path: Optional[Path] = None,
    fault: FaultInjector = no_fault,
) -> ChildRunManifest:
    manifest = ChildRunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    if occurred_at < manifest.updated_at:
        raise ValueError("occurred_at cannot be earlier than the manifest updated_at")
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
    updated = _validated_copy(manifest, updates)
    atomic_write_json(manifest_path, _manifest_payload(updated), fault)
    parent_path = parent_manifest_path or _parent_manifest_path(manifest_path, manifest.campaign_id)
    if parent_path.exists():
        recompute_parent_manifest(parent_path, occurred_at, f"child {manifest.run_id} cleanup -> {target.value}", fault)
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
    schedule_entry: Optional[ScheduleEntry] = None,
) -> InitializedCampaign:
    resolved, source = resolve_campaign(config_path, repository_root, schedule_entry)
    if schedule_entry is not None and (
        schedule_entry.config_id != resolved.config_id
        or schedule_entry.experiment_id != resolved.experiment_id
        or schedule_entry.scheduled_start != resolved.scheduled_start
    ):
        raise ValueError("supplied schedule entry must match resolved config identity and time")
    if resolved.schedule_entry.value is not None and schedule_entry != resolved.schedule_entry.value:
        raise ValueError("supplied schedule entry must equal the config schedule entry")
    schedule_entry = schedule_entry or resolved.schedule_entry.value
    resolved_bytes = json.dumps(resolved.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    schedule_bytes = ((schedule_entry.original_header + "\n" + schedule_entry.original_row + "\n").encode("utf-8")
                      if schedule_entry else None)
    now = clock()
    campaign_id = candidate_campaign_id(resolved.experiment_id, clock, token_source)
    run_ids = {Provider(provider): candidate_run_id(campaign_id, Provider(provider)) for provider in resolved.selected_providers}
    paths = allocate_paths(results_root, campaign_id, run_ids)
    preflight(paths)
    implementation_provider = implementation_git or RepositoryGitProvenance()
    implementation = implementation_provider.commit(repository_root)
    implementation_snapshot = _snapshot_or_unavailable(implementation_provider, repository_root, implementation)
    design_root = thesis_repository_root(repository_root)
    design_provider = design_git or RepositoryGitProvenance()
    design = design_provider.commit(design_root)
    design_snapshot = _snapshot_or_unavailable(design_provider, design_root, design)
    unavailable = StructuredEvidence(unavailable_reason="not observed during F01 initialization")
    tool_versions = StructuredEvidence(value={"cloud_network_benchmark": "0.1.0", "python": platform.python_version()})
    assigned: List[Tuple[Provider, ChildRunManifest]] = []
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
        snapshot_path=_relative(paths.child_results[Provider(resolved.selected_providers[0])] / "config.yaml", repository_root),
        resolved_sha256=resolved.resolved_sha256,
        resolved_snapshot_sha256=StringEvidence(value=hashlib.sha256(resolved_bytes).hexdigest()),
        resolved_snapshot_path=_relative(paths.child_results[Provider(resolved.selected_providers[0])] / "config.resolved.json", repository_root),
        schedule_sha256=StringEvidence(value=hashlib.sha256(schedule_bytes).hexdigest()) if schedule_bytes else StringEvidence(unavailable_reason="no schedule row supplied"),
        implementation_git_commit=implementation,
        design_git_commit=design,
        implementation_git=implementation_snapshot,
        design_git=design_snapshot,
    )
    parent = CampaignManifest(
        config_id=resolved.config_id,
        mode=resolved.mode,
        context=resolved.context,
        campaign_id=campaign_id,
        experiment_id=resolved.experiment_id,
        configuration_role=resolved.configuration_role,
        scheduled_start=resolved.scheduled_start,
        schedule_entry=Evidence[ScheduleEntry](value=schedule_entry) if schedule_entry else Evidence[ScheduleEntry](unavailable_reason="no schedule row supplied"),
        admission=resolved.admission,
        reservation=Evidence[ReservationEvidence](unavailable_reason="local initialization does not reserve budget"),
        selected_providers=resolved.selected_providers,
        scenario=resolved.scenario,
        config_provenance=parent_provenance,
        tool_versions=tool_versions,
        child_runs=[],
        lifecycle_events=[LifecycleEvent(state_domain="campaign", state=CampaignState.INITIALIZED.value, occurred_at=now)],
        created_at=now,
        updated_at=now,
    )

    try:
        fault("before_parent_manifest_reservation")
        reserve_json(paths.campaign_manifest, _manifest_payload(parent), fault)
        fault("after_parent_manifest_reservation")
        for observation in resolved.observations:
            provider = Provider(observation.provider)
            run_id = run_ids[provider]
            result_path = paths.child_results[provider]
            snapshot = result_path / "config.yaml"
            resolved_snapshot = result_path / "config.resolved.json"
            schedule_snapshot = result_path / "schedule.csv"
            provenance = ManifestConfigProvenance(
                source_path=resolved.config_source.source_path,
                sha256=resolved.config_source.sha256,
                byte_length=resolved.config_source.byte_length,
                snapshot_path=_relative(snapshot, repository_root),
                resolved_sha256=resolved.resolved_sha256,
                resolved_snapshot_sha256=StringEvidence(value=hashlib.sha256(resolved_bytes).hexdigest()),
                resolved_snapshot_path=_relative(resolved_snapshot, repository_root),
                schedule_sha256=StringEvidence(value=hashlib.sha256(schedule_bytes).hexdigest()) if schedule_bytes else StringEvidence(unavailable_reason="no schedule row supplied"),
                implementation_git_commit=implementation,
                design_git_commit=design,
                implementation_git=implementation_snapshot,
                design_git=design_snapshot,
            )
            initial_event = LifecycleEvent(
                state_domain="execution",
                state=ExecutionState.INITIALIZED.value,
                occurred_at=now,
                detail="child manifest reserved",
            )
            manifest = ChildRunManifest(
                config_id=resolved.config_id,
                mode=resolved.mode,
                context=resolved.context,
                run_id=run_id,
                campaign_id=campaign_id,
                experiment_id=resolved.experiment_id,
                provider=provider,
                scenario=resolved.scenario,
                scheduled_start=resolved.scheduled_start,
                observation_id=resolved.replacement_context.observation_id if resolved.replacement_context else candidate_observation_id(run_id),
                deployment_id=candidate_deployment_id(run_id),
                replaces_run_id=resolved.replacement_context.replaces_run_id if resolved.replacement_context else (schedule_entry.replaces_run_id if schedule_entry else None),
                schedule_entry=Evidence[ScheduleEntry](value=schedule_entry) if schedule_entry else Evidence[ScheduleEntry](unavailable_reason="no schedule row supplied"),
                measurement_units=resolved.measurement_units,
                config_provenance=provenance,
                resolved_observation=observation.model_dump(mode="json"),
                vm_metadata=unavailable,
                provider_metadata=unavailable,
                tool_versions=tool_versions,
                lifecycle_events=[initial_event],
                created_at=now,
                updated_at=now,
            )
            fault(f"before_child_manifest_reservation:{provider.value}")
            reserve_json(paths.child_manifests[provider], _manifest_payload(manifest), fault)
            fault(f"after_child_manifest_reservation:{provider.value}")
            assigned.append((provider, manifest))
            current_parent = CampaignManifest.model_validate_json(paths.campaign_manifest.read_text(encoding="utf-8"))
            assigned_providers = {item[0] for item in assigned}
            linked_parent = _validated_copy(
                current_parent,
                {
                    "child_runs": [reference for reference in children if Provider(reference.provider) in assigned_providers],
                    "updated_at": now,
                },
            )
            fault(f"before_parent_child_link_update:{provider.value}")
            atomic_write_json(paths.campaign_manifest, _manifest_payload(linked_parent), fault)
            fault(f"after_parent_child_link_update:{provider.value}")
            fault(f"before_result_directory:{provider.value}")
            create_directory(result_path, fault)
            fault(f"after_result_directory:{provider.value}")
            fault(f"before_config_snapshot:{provider.value}")
            write_exclusive(snapshot, source, fault)
            fault(f"after_config_snapshot:{provider.value}")
            if hashlib.sha256(snapshot.read_bytes()).hexdigest() != resolved.config_source.sha256:
                raise PersistenceError(f"snapshot hash mismatch for {run_id}")
            write_exclusive(resolved_snapshot, resolved_bytes, fault)
            if hashlib.sha256(resolved_snapshot.read_bytes()).hexdigest() != provenance.resolved_snapshot_sha256.value:
                raise PersistenceError(f"resolved snapshot hash mismatch for {run_id}")
            if schedule_bytes:
                write_exclusive(schedule_snapshot, schedule_bytes, fault)
                if hashlib.sha256(schedule_snapshot.read_bytes()).hexdigest() != provenance.schedule_sha256.value:
                    raise PersistenceError(f"schedule snapshot hash mismatch for {run_id}")

        return InitializedCampaign(
            campaign_id=campaign_id,
            parent_manifest_path=_relative(paths.campaign_manifest, repository_root),
            children=children,
        )
    except Exception as exc:
        recovery_time = clock()
        recovery_failures: List[str] = []
        assigned_by_provider = {provider: manifest for provider, manifest in assigned}
        recovered_providers = set(assigned_by_provider)
        for observation in resolved.observations:
            provider = Provider(observation.provider)
            manifest_path = paths.child_manifests[provider]
            if not manifest_path.exists():
                continue
            manifest = assigned_by_provider.get(provider)
            if manifest is None:
                if isinstance(exc, CollisionError):
                    continue
                try:
                    manifest = ChildRunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if manifest.run_id != run_ids[provider] or manifest.campaign_id != campaign_id:
                    continue
                recovered_providers.add(provider)
            failed = _failed_manifest(manifest, recovery_time, str(exc))
            try:
                fault(f"before_recovery_update:{provider.value}")
                atomic_write_json(manifest_path, _manifest_payload(failed), fault)
                fault(f"after_recovery_update:{provider.value}")
            except Exception as recovery_exc:
                recovery_failures.append(f"{manifest_path}: {recovery_exc}")
                try:
                    atomic_write_json(manifest_path, _manifest_payload(failed))
                except BenchmarkError as fallback_exc:
                    recovery_failures.append(f"{manifest_path} fallback: {fallback_exc}")
        if paths.campaign_manifest.exists():
            try:
                parent = CampaignManifest.model_validate_json(paths.campaign_manifest.read_text(encoding="utf-8"))
                parent_event = LifecycleEvent(
                    state_domain="campaign",
                    state=CampaignState.FAILED.value,
                    occurred_at=recovery_time,
                    detail=f"partial initialization failed: {exc}",
                )
                failed_parent = _validated_copy(
                    parent,
                    {
                        "aggregate_state": CampaignState.FAILED,
                        "child_runs": [
                            reference for reference in children
                            if Provider(reference.provider) in recovered_providers
                        ],
                        "updated_at": recovery_time,
                        "lifecycle_events": [*parent.lifecycle_events, parent_event],
                    },
                )
                fault("before_parent_recovery_update")
                atomic_write_json(paths.campaign_manifest, _manifest_payload(failed_parent), fault)
                fault("after_parent_recovery_update")
            except Exception as recovery_exc:
                recovery_failures.append(f"{paths.campaign_manifest}: {recovery_exc}")
                try:
                    atomic_write_json(paths.campaign_manifest, _manifest_payload(failed_parent))
                except Exception as fallback_exc:
                    recovery_failures.append(f"{paths.campaign_manifest} fallback: {fallback_exc}")
        preserved = []
        for provider, path in paths.child_manifests.items():
            if provider in recovered_providers and path.exists():
                try:
                    ChildRunManifest.model_validate_json(path.read_text(encoding="utf-8"))
                    preserved.append(_relative(path, repository_root))
                except Exception as invalid_exc:
                    recovery_failures.append(f"{path} invalid after recovery: {invalid_exc}")
        recovery = InitializationRecovery(
            campaign_id=campaign_id,
            parent_manifest_path=_relative(paths.campaign_manifest, repository_root) if paths.campaign_manifest.exists() else None,
            preserved_child_manifest_paths=preserved,
            recovery_failures=recovery_failures,
        )
        details = recovery.model_dump(mode="json")
        if isinstance(exc, CollisionError):
            raise exc
        raise PersistenceError(f"campaign initialization failed: {exc}", details) from exc
