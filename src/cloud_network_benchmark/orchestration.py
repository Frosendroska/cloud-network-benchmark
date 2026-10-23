"""Small, explicit F09 controller composition over existing contracts.

The module deliberately owns no provider implementation or shared DTO. Callers
adapt the merged AWS, Azure and GCP drivers to ``ProviderHooks``.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
import stat
from pathlib import Path
from typing import Any, Callable, Mapping
from pydantic import Field

from .contracts.common import ExperimentMode
from .contracts.common import StrictModel


@dataclass(frozen=True)
class LifecycleContext:
    controller_session_id: str
    cap_started_monotonic: float
    cap_deadline_monotonic: float
    lifetime_cap_seconds: float
    stage_remainders: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.controller_session_id.strip():
            raise ValueError("controller session identity is required")
        if self.cap_started_monotonic < 0 or self.cap_deadline_monotonic <= self.cap_started_monotonic:
            raise ValueError("lifecycle cap deadline must follow its monotonic start")
        if self.lifetime_cap_seconds <= 0 or self.cap_deadline_monotonic - self.cap_started_monotonic > self.lifetime_cap_seconds + 1e-6:
            raise ValueError("lifecycle deadline exceeds its original cap")
        if any(not key.strip() or value < 0 for key, value in self.stage_remainders.items()):
            raise ValueError("stage remainder evidence must be named and nonnegative")

    def assert_session(self, session_id: str) -> None:
        if session_id != self.controller_session_id:
            raise ValueError("controller session changed; lifecycle attempt cannot resume")

    def require_fit(self, stage: str, required_seconds: float, *, now_monotonic: float) -> None:
        if stage not in self.stage_remainders:
            raise ValueError(f"{stage} remainder has no sourced allowance")
        if required_seconds < 0 or now_monotonic < self.cap_started_monotonic:
            raise ValueError("stage timing evidence is invalid")
        if now_monotonic + max(required_seconds, self.stage_remainders[stage]) > self.cap_deadline_monotonic:
            raise ValueError(f"{stage} does not fit inside the original cap")


@dataclass(frozen=True)
class ProviderHooks:
    provider: str
    protect: Callable[[], Any]
    claim: Callable[[], Any]
    create: Callable[[], Any]
    prepare: Callable[[], Any]
    stabilize: Callable[[], Any]
    measure: Callable[[], Any]
    close: Callable[[], Any]
    retrieve: Callable[[], Any]
    cleanup: Callable[[], Any]


@dataclass
class ProviderLifecycleResult:
    provider: str
    execution_state: str = "initialized"
    cleanup_state: str = "not_started"
    values: dict[str, Any] = field(default_factory=dict)
    primary_failure: str | None = None
    secondary_failures: list[str] = field(default_factory=list)


@dataclass
class LifecycleResult:
    provider_results: dict[str, ProviderLifecycleResult]
    completed: bool
    primary_failure: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    admission_status: str = "not_evaluated"
    admission_reasons: tuple[str, ...] = ()
    reservation_evidence: Any = None


class SavedProviderRecovery(StrictModel):
    provider: str
    deployment_id: str = Field(min_length=1)
    journal_reference: str = Field(min_length=1)
    expiry_registered: bool
    creation_may_have_started: bool
    cleanup_open: bool


class SavedLifecycleRecovery(StrictModel):
    schema_version: int = Field(strict=True, ge=1, le=1)
    attempt_id: str = Field(min_length=1)
    controller_session_id: str = Field(min_length=1)
    providers: list[SavedProviderRecovery] = Field(min_length=1, max_length=3)

    @classmethod
    def validate_unique(cls, value):
        parsed = cls.model_validate(value)
        if len({item.provider for item in parsed.providers}) != len(parsed.providers):
            raise ValueError("recovery provider identities must be unique")
        if any(item.provider not in {"aws", "azure", "gcp"} for item in parsed.providers):
            raise ValueError("recovery contains an unsupported provider")
        return parsed


def write_recovery_journal(path: Path, recovery: SavedLifecycleRecovery) -> None:
    """Create an exclusive private journal before lifecycle side effects."""
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise FileExistsError("private recovery journal destination must be absent")
    path.parent.mkdir(parents=True, exist_ok=True)
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("private recovery journal path cannot contain symlinks")
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise FileExistsError("private recovery journal temporary path must be absent")
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(recovery.model_dump_json(indent=2).encode("utf-8") + b"\n")
            stream.flush(); os.fsync(stream.fileno())
        os.link(temporary, path)
        os.unlink(temporary)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        finally:
            raise
    written = path.stat()
    if stat.S_IMODE(written.st_mode) & 0o077:
        raise PermissionError("private recovery journal permissions are not restrictive")
    SavedLifecycleRecovery.validate_unique(json.loads(path.read_text(encoding="utf-8")))


@dataclass
class CleanupLifecycleResult:
    attempt_id: str
    original_session_id: str
    current_session_id: str
    providers: dict[str, dict[str, Any]]
    complete: bool


def validate_mode_units(mode: str, units: list[Any] | tuple[Any, ...]) -> None:
    """Check the unchanged mode protocol before lifecycle work can be admitted."""
    mode = ExperimentMode(mode)
    phases = [str(getattr(getattr(unit, "phase", ""), "value", getattr(unit, "phase", ""))) for unit in units]
    kinds = [getattr(unit, "kind", None) for unit in units]
    durations = [getattr(unit, "measured_seconds", None) for unit in units]
    if mode == ExperimentMode.ACCEPTANCE:
        if phases != ["idle_latency", "single_flow", "multi_flow"] or any(kind != "phase" for kind in kinds) or durations != [10, 10, 10]:
            raise ValueError("acceptance must retain the ordered three ten-second phases")
    elif mode == ExperimentMode.SHORT_STUDY:
        if phases != ["idle_latency", "single_flow", "multi_flow"] or any(kind != "phase" for kind in kinds) or durations != [60, 60, 60]:
            raise ValueError("short_study must retain the ordered three sixty-second phases")
    elif mode == ExperimentMode.CALIBRATION:
        expected = [1, 4, 2, 8, 8, 2, 4, 1, 2, 8, 1, 4]
        if len(units) != 12 or phases != ["multi_flow"] * 12 or any(kind != "calibration_trial" for kind in kinds):
            raise ValueError("calibration must retain twelve aggregate trials")
        if [getattr(unit, "trial_index", None) for unit in units] != list(range(1, 13)) or [getattr(unit, "streams", None) for unit in units] != expected or durations != [60] * 12:
            raise ValueError("calibration trial order, stream count or duration changed")
    else:
        day = [unit for unit in units if getattr(unit, "kind", None) == "continuous_day"]
        chunks = [unit for unit in units if getattr(unit, "kind", None) == "probe_chunk"]
        if len(day) != 1 or phases.count("multi_flow") != len(units) or day[0].measured_seconds != 86400:
            raise ValueError("diagnostic_day requires one continuous 86400-second multi_flow unit")
        if len(chunks) + 1 != len(units) or any(chunk.parent_unit_id != day[0].unit_id or chunk.measured_seconds > 300 for chunk in chunks):
            raise ValueError("diagnostic_day probe chunks must be bounded children of the retained TCP unit")


def cleanup_lifecycle(recovery_path: Path, *, current_session_id: str,
                      cleanup_actions: Mapping[str, Callable[[SavedProviderRecovery], Any]],
                      reconcile: Callable[[SavedProviderRecovery, Any], Any]) -> CleanupLifecycleResult:
    """Perform only explicit cleanup/reconciliation from a saved attempt.

    This function has no provision, protection-registration or measurement call
    path. Provider cleanup callbacks are invoked only for durable liabilities;
    each peer is attempted even when another provider raises.
    """
    if recovery_path.is_symlink() or not recovery_path.is_file():
        raise ValueError("recovery journal must be a regular non-symlink file")
    mode = stat.S_IMODE(recovery_path.stat().st_mode)
    if mode & 0o077:
        raise ValueError("recovery journal permissions must be private")
    recovery = SavedLifecycleRecovery.validate_unique(json.loads(recovery_path.read_text(encoding="utf-8")))
    if not current_session_id.strip():
        raise ValueError("current controller session is required for cleanup")
    outcomes: dict[str, dict[str, Any]] = {}
    for record in recovery.providers:
        liability = record.cleanup_open or record.expiry_registered or record.creation_may_have_started
        if not liability:
            outcomes[record.provider] = {"cleanup": "not_required", "reconciled": True,
                                         "reason": "journal proves no safety or compute allocation began"}
            continue
        cleanup_result: Any = None
        errors: list[str] = []
        action = cleanup_actions.get(record.provider)
        if action is None:
            errors.append("provider cleanup adapter unavailable")
        else:
            try:
                cleanup_result = action(record)
            except BaseException as exc:
                errors.append(f"cleanup failed ({type(exc).__name__})")
        try:
            reconciliation = reconcile(record, cleanup_result)
        except BaseException as exc:
            reconciliation = None
            errors.append(f"reconciliation failed ({type(exc).__name__})")
        absence_proved = bool(getattr(reconciliation, "absence_proved", False) if not isinstance(reconciliation, Mapping)
                              else reconciliation.get("absence_proved") is True)
        outcomes[record.provider] = {"cleanup": "attempted" if not errors else "failed",
                                     "reconciled": absence_proved, "cleanup_result": cleanup_result,
                                     "reconciliation": reconciliation, "errors": errors,
                                     "launch_blocked": not absence_proved}
    complete = all(item.get("cleanup") in {"succeeded", "not_required"} and item.get("reconciled", True)
                   for item in outcomes.values())
    # Cleanup callbacks do not get to claim success from a requested delete;
    # the reconciliation adapter must independently prove absence.
    for row in outcomes.values():
        if row.get("cleanup") == "attempted" and row.get("reconciled"):
            row["cleanup"] = "succeeded"
    complete = all(item.get("cleanup") in {"succeeded", "not_required"} and item.get("reconciled", True)
                   for item in outcomes.values())
    return CleanupLifecycleResult(recovery.attempt_id, recovery.controller_session_id,
                                  current_session_id, outcomes, complete)


def _call(provider: str, stage: str, action: Callable[[], Any], result: ProviderLifecycleResult) -> bool:
    try:
        result.values[stage] = action()
        return True
    except BaseException as exc:
        failure = f"{provider} {stage} failed ({type(exc).__name__})"
        if result.primary_failure is None:
            result.primary_failure = failure
        else:
            result.secondary_failures.append(failure)
        return False


def execute_provider_workflows(
    workflows: Mapping[str, ProviderHooks], *, context: LifecycleContext,
    clock: Any, max_workers: int = 3,
) -> LifecycleResult:
    """Execute protected provider workflows and always collect cleanup outcomes.

    Protection is a global barrier: no single-use creation claim or VM creation
    happens until every selected provider has returned protection evidence. After
    that barrier provider failures remain local, and all selected cleanup hooks
    are joined even if a sibling raises.
    """
    if not workflows or set(workflows) != {hooks.provider for hooks in workflows.values()}:
        raise ValueError("provider workflow keys must uniquely match selected provider identities")
    if len(workflows) > 3 or max_workers < 1:
        raise ValueError("lifecycle accepts one to three providers and a positive worker bound")
    started = datetime.now(timezone.utc)
    results = {provider: ProviderLifecycleResult(provider) for provider in workflows}
    protected: set[str] = set()
    protection_attempted: set[str] = set()
    protection_failed = False
    # Registration is intentionally complete before any claim/create. Serial
    # calls avoid concurrent mutation of provider-local recovery journals.
    for provider, hooks in workflows.items():
        protection_attempted.add(provider)
        if _call(provider, "protect", hooks.protect, results[provider]):
            protected.add(provider)
        else:
            protection_failed = True
    claim_ok: set[str] = set()
    if not protection_failed:
        for provider, hooks in workflows.items():
            if _call(provider, "claim", hooks.claim, results[provider]):
                claim_ok.add(provider)
        if claim_ok:
            def run_one(provider: str, hooks: ProviderHooks) -> None:
                result = results[provider]
                if not _call(provider, "create", hooks.create, result):
                    result.execution_state = "failed"
                    return
                result.execution_state = "provision_ready"
                for stage in ("prepare", "stabilize", "measure"):
                    if not _call(provider, stage, getattr(hooks, stage), result):
                        result.execution_state = "failed"
                        break
                    if stage == "measure":
                        result.execution_state = "running"
                # Once a pair exists, failure in preparation or measurement
                # still requires best-effort close/flush and retrieval of any
                # partial native evidence before normal resource cleanup.
                for stage in ("close", "retrieve"):
                    if not _call(provider, stage, getattr(hooks, stage), result):
                        result.execution_state = "failed"
                if result.primary_failure is None:
                    result.execution_state = "succeeded"
                else:
                    result.execution_state = "failed"

            with ThreadPoolExecutor(max_workers=min(max_workers, len(workflows))) as pool:
                futures = [pool.submit(run_one, provider, workflows[provider]) for provider in claim_ok]
                for future in as_completed(futures):
                    try:
                        future.result()
                    except BaseException as exc:  # defensive: worker implementation must not strand peers
                        results[next(iter(workflows))].secondary_failures.append(f"worker failed ({type(exc).__name__})")

    # Cleanup is attempted for every selected provider: protection may have
    # allocated safety resources even when VM creation was never reached.
    with ThreadPoolExecutor(max_workers=min(max_workers, len(workflows))) as pool:
        futures = {pool.submit(_call, provider, "cleanup", workflows[provider].cleanup, results[provider]): provider
                   for provider in protection_attempted}
        for future in as_completed(futures):
            provider = futures[future]
            if future.result():
                results[provider].cleanup_state = "succeeded"
            else:
                results[provider].cleanup_state = "failed"
    failures = [r.primary_failure for r in results.values() if r.primary_failure]
    failures.extend(f"{r.provider} cleanup failed" for r in results.values() if r.cleanup_state == "failed")
    completed = not protection_failed and not failures and all(r.execution_state == "succeeded" for r in results.values())
    return LifecycleResult(results, completed, failures[0] if failures else None, started, datetime.now(timezone.utc))


def run_lifecycle(*, ledger: Any, reservation_request: Any, admission_plan: Any,
                  allowance: Any, workflow_factory: Callable[[Any], Mapping[str, ProviderHooks]],
                  context: LifecycleContext, clock: Any, max_workers: int = 3) -> LifecycleResult:
    """Reserve the complete F11 workflow before constructing external stages.

    This entry accepts only the explicitly selected ``paid_request`` scope;
    offline simulation stays in test fixtures and never dispatches a provider.
    """
    if admission_plan.execution_scope != "paid_request":
        raise ValueError("offline_simulation cannot dispatch lifecycle provider commands")
    for observation in getattr(getattr(admission_plan, "resolved_campaign", None), "observations", ()):
        validate_mode_units(observation.mode, observation.measurement_units)
    decision = ledger.reserve(reservation_request, plan=admission_plan)
    if decision.status != "reserved" or decision.evidence is None:
        return LifecycleResult({}, False, "admission rejected or pending", admission_status=decision.status,
                               admission_reasons=tuple(decision.reasons))
    reservation_id = decision.evidence.reservation_id.value
    fit = ledger.check_stage_fit(reservation_id, stage="registration", remaining=allowance)
    if not fit.eligible:
        return LifecycleResult({}, False, "complete workflow does not fit", admission_status="reserved_but_not_fit",
                               admission_reasons=tuple(fit.reason_codes), reservation_evidence=decision.evidence)
    workflows = workflow_factory(decision.evidence)
    result = execute_provider_workflows(workflows, context=context, clock=clock, max_workers=max_workers)
    result.admission_status = decision.status
    result.reservation_evidence = decision.evidence
    return result
