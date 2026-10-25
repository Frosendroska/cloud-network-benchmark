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
import signal
import stat
import threading
import fcntl
import tempfile
from contextlib import contextmanager
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
    provider_trigger_monotonic: float | None = None

    def __post_init__(self) -> None:
        if not self.controller_session_id.strip():
            raise ValueError("controller session identity is required")
        if self.cap_started_monotonic < 0 or self.cap_deadline_monotonic <= self.cap_started_monotonic:
            raise ValueError("lifecycle cap deadline must follow its monotonic start")
        if self.lifetime_cap_seconds <= 0 or self.cap_deadline_monotonic - self.cap_started_monotonic > self.lifetime_cap_seconds + 1e-6:
            raise ValueError("lifecycle deadline exceeds its original cap")
        if any(not key.strip() or value < 0 for key, value in self.stage_remainders.items()):
            raise ValueError("stage remainder evidence must be named and nonnegative")
        if (self.provider_trigger_monotonic is not None
                and not self.cap_started_monotonic < self.provider_trigger_monotonic <= self.cap_deadline_monotonic):
            raise ValueError("provider expiry trigger must be within the original cap")

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
    cancel: Callable[[], Any] = lambda: None
    stage_admission: Callable[[str], Any] | None = None
    cleanup_reconcile: Callable[[Any], Any] | None = None
    cleanup_resource_ids: Callable[[], list[str]] | None = None


@dataclass(frozen=True)
class ProviderStageOutcome:
    """Explicit local adapter result; returning from a callback is not success."""
    stage: str
    succeeded: bool
    evidence: Any = None

    def __post_init__(self) -> None:
        if not self.stage or type(self.succeeded) is not bool:
            raise ValueError("provider stage outcome requires a stage and strict boolean status")


@dataclass
class ProviderLifecycleResult:
    provider: str
    execution_state: str = "initialized"
    cleanup_state: str = "not_started"
    values: dict[str, Any] = field(default_factory=dict)
    primary_failure: str | None = None
    secondary_failures: list[str] = field(default_factory=list)
    interrupted: bool = False
    creation_may_have_started: bool = False
    stage_times: dict[str, dict[str, datetime]] = field(default_factory=dict)


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
    cleanup_settings_reference: str | None = None


class SavedLifecycleRecovery(StrictModel):
    schema_version: int = Field(strict=True, ge=1, le=1)
    attempt_id: str = Field(min_length=1)
    controller_session_id: str = Field(min_length=1)
    reservation_id: str | None = None
    ledger_path: str | None = None
    ledger_scope_id: str | None = None
    providers: list[SavedProviderRecovery] = Field(min_length=1, max_length=3)
    retry_consumed_ids: list[str] = Field(default_factory=list)

    @classmethod
    def validate_unique(cls, value):
        parsed = cls.model_validate(value)
        if len({item.provider for item in parsed.providers}) != len(parsed.providers):
            raise ValueError("recovery provider identities must be unique")
        if any(item.provider not in {"aws", "azure", "gcp"} for item in parsed.providers):
            raise ValueError("recovery contains an unsupported provider")
        if (len(parsed.retry_consumed_ids) != len(set(parsed.retry_consumed_ids))
                or any(not isinstance(item, str) or not item.strip() for item in parsed.retry_consumed_ids)):
            raise ValueError("retry consumption identities must be unique nonblank strings")
        return parsed


def _mutate_recovery_journal(recovery_path: Path, update: Callable[[SavedLifecycleRecovery], tuple[SavedLifecycleRecovery, Any]]) -> Any:
    path = Path(recovery_path)
    if path.is_symlink() or not path.is_file() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("recovery update requires a regular journal without symlink parents")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("recovery journal permissions must be private")
    lock_path = path.with_name(path.name + ".lock")
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchmod(lock_fd, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        recovery = SavedLifecycleRecovery.validate_unique(json.loads(path.read_text(encoding="utf-8")))
        updated, result = update(recovery)
        updated = SavedLifecycleRecovery.validate_unique(updated.model_dump(mode="python"))
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(updated.model_dump_json(indent=2).encode("utf-8") + b"\n")
                stream.flush(); os.fsync(stream.fileno())
            os.replace(temp_path, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            if stat.S_IMODE(path.stat().st_mode) & 0o077:
                os.chmod(path, 0o600)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
        return result
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def consume_retry_attempt(recovery_path: Path, artifact_id: str) -> bool:
    """Persist sole retry consumption before invoking the second transfer."""
    if not artifact_id.strip():
        raise ValueError("retry consumption requires a concrete artifact ID")
    def update(recovery: SavedLifecycleRecovery):
        if artifact_id in recovery.retry_consumed_ids:
            return recovery, False
        return SavedLifecycleRecovery.model_validate({**recovery.model_dump(),
            "retry_consumed_ids": [*recovery.retry_consumed_ids, artifact_id]}), True
    return _mutate_recovery_journal(recovery_path, update)


def update_provider_recovery(recovery_path: Path, provider: str, stage: str, status: str) -> None:
    """Fsync current provider liabilities around resource-creating stages."""
    if provider not in {"aws", "azure", "gcp"} or status not in {"started", "succeeded", "failed"}:
        raise ValueError("provider recovery update identity/status is invalid")
    if stage not in {"protect", "claim", "create", "cleanup"}:
        return
    def update(recovery: SavedLifecycleRecovery):
        matches = [item for item in recovery.providers if item.provider == provider]
        if len(matches) != 1:
            raise ValueError("provider recovery record is missing or ambiguous")
        rows = []
        for item in recovery.providers:
            payload = item.model_dump()
            if item.provider == provider:
                if stage in {"protect", "claim", "create"}:
                    payload["cleanup_open"] = True
                if stage == "create" and status == "started":
                    payload["creation_may_have_started"] = True
                if stage == "protect" and status == "succeeded":
                    payload["expiry_registered"] = True
                if stage == "cleanup" and status == "succeeded":
                    payload.update(expiry_registered=False, creation_may_have_started=False, cleanup_open=False)
            rows.append(payload)
        return SavedLifecycleRecovery.model_validate({**recovery.model_dump(), "providers": rows}), None
    _mutate_recovery_journal(recovery_path, update)


def write_recovery_journal(path: Path, recovery: SavedLifecycleRecovery) -> None:
    """Create an exclusive private journal before lifecycle side effects."""
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise FileExistsError("private recovery journal destination must be absent")
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("private recovery journal path cannot contain symlinks")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("private recovery journal path cannot contain symlinks")
    if stat.S_IMODE(path.parent.stat().st_mode) & 0o077:
        raise PermissionError("private recovery journal directory permissions must be restrictive")
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
                      reconcile: Callable[[SavedProviderRecovery, Any], Any],
                      ledger: Any = None) -> CleanupLifecycleResult:
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
        ledger_reservation = None
        if ledger is not None and callable(getattr(ledger, "inspect", None)) and recovery.reservation_id is not None:
            ledger_reservation = ledger.inspect().get("reservations", {}).get(recovery.reservation_id)
            from .lifecycle_cleanup import replay_saved_cleanup_receipt
            try:
                receipt_reconciled = replay_saved_cleanup_receipt(record, recovery=recovery, ledger=ledger)
            except RuntimeError as exc:
                outcomes[record.provider] = {"cleanup": "failed", "reconciled": False,
                    "errors": [str(exc)], "launch_blocked": True}
                continue
            if receipt_reconciled:
                try:
                    update_provider_recovery(recovery_path, record.provider, "cleanup", "succeeded")
                    outcomes[record.provider] = {"cleanup": "not_required", "reconciled": True,
                        "absence_proved": True, "billable_resources_present": False,
                        "reason": "saved source receipt acknowledged by the original F11 ledger",
                        "launch_blocked": False}
                    continue
                except BaseException as exc:
                    outcomes[record.provider] = {"cleanup": "failed", "reconciled": False,
                        "errors": [f"recovery journal update failed ({type(exc).__name__})"],
                        "launch_blocked": True}
                    continue
            if isinstance(ledger_reservation, Mapping):
                # A local journal flag or stale receipt cannot override an open
                # original F11 provider cleanup liability.
                liability = True
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
            if ledger is not None:
                from .admission import Reconciliation
                event_payload = (reconciliation.get("ledger_event")
                    if isinstance(reconciliation, Mapping) else getattr(reconciliation, "ledger_event", None))
                if isinstance(event_payload, Reconciliation):
                    event = event_payload
                elif isinstance(event_payload, Mapping):
                    event = Reconciliation(**event_payload)
                else:
                    raise ValueError("provider reconciliation lacks a typed F11 ledger event")
                if (recovery.reservation_id is None or event.reservation_id != recovery.reservation_id
                        or event.provider != record.provider):
                    raise ValueError("F11 reconciliation identity does not match the saved attempt")
                ledger.reconcile(event)
                if callable(getattr(ledger, "inspect", None)):
                    acknowledged = ledger.inspect().get("reservations", {}).get(
                        recovery.reservation_id, {}).get("provider_cleanup", {}).get(record.provider)
                    if event.cleanup_verified_absent and acknowledged is not True:
                        raise ValueError("F11 ledger did not durably acknowledge provider absence")
        except BaseException as exc:
            reconciliation = None
            errors.append(f"reconciliation failed ({type(exc).__name__})")
        absence_proved = bool(getattr(reconciliation, "absence_proved", False) if not isinstance(reconciliation, Mapping)
                              else reconciliation.get("absence_proved") is True)
        billable_resources_present = (getattr(reconciliation, "billable_resources_present", None)
            if not isinstance(reconciliation, Mapping) else reconciliation.get("billable_resources_present"))
        ledger_cleanup_open = (getattr(reconciliation, "cleanup_open", None)
            if not isinstance(reconciliation, Mapping) else reconciliation.get("cleanup_open"))
        reconciled = absence_proved and billable_resources_present is False and ledger_cleanup_open is False
        if reconciled:
            # Clear durable liability only after explicit absence and ledger
            # reconciliation evidence; callback return alone is insufficient.
            try:
                update_provider_recovery(recovery_path, record.provider, "cleanup", "succeeded")
            except BaseException as exc:
                errors.append(f"recovery journal update failed ({type(exc).__name__})")
                reconciled = False
        outcomes[record.provider] = {"cleanup": "attempted" if not errors else "failed",
                                     "reconciled": reconciled,
                                     "absence_proved": absence_proved,
                                     "billable_resources_present": billable_resources_present,
                                     "cleanup_open": ledger_cleanup_open,
                                     "billing_final": (getattr(reconciliation, "billing_final", None)
                                         if not isinstance(reconciliation, Mapping) else reconciliation.get("billing_final")),
                                     "incurred_usd": (getattr(reconciliation, "incurred_usd", None)
                                         if not isinstance(reconciliation, Mapping) else reconciliation.get("incurred_usd")),
                                     "cleanup_result": cleanup_result,
                                     "reconciliation": reconciliation, "errors": errors,
                                     "launch_blocked": not reconciled}
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


def _typed_outcome(provider: str, stage: str, value: Any) -> tuple[bool, str | None]:
    """Interpret provider contract results and explicit adapter outcomes."""
    if stage == "cancel":
        return True, None
    if isinstance(value, ProviderStageOutcome):
        if value.stage != stage:
            return False, f"adapter reported {value.stage} for {stage}"
        return value.succeeded, None if value.succeeded else f"adapter reported {stage} unsuccessful"

    if stage == "protect":
        from .contracts.admission import ExpiryEvidence
        actual_provider = getattr(getattr(value, "provider", None), "value", getattr(value, "provider", None))
        return (isinstance(value, ExpiryEvidence) and value.state == "registered"
                and actual_provider == provider, "registered expiry evidence is missing or mismatched")
    if stage == "claim":
        return (getattr(value, "eligible", None) is True and getattr(value, "creation_granted", None) is True,
                "F11 creation claim was not granted")
    if stage == "create":
        from .contracts.deployment import ProviderDeploymentOutput
        deployment = value if isinstance(value, ProviderDeploymentOutput) else getattr(value, "deployment", None)
        failures = getattr(value, "failures", ())
        expiry = getattr(value, "expiry", None)
        if deployment is None or not isinstance(deployment, ProviderDeploymentOutput):
            return False, "provider did not return a typed deployment output"
        if getattr(deployment.provider, "value", deployment.provider) != provider:
            return False, "provider deployment identity is mismatched"
        if failures:
            return False, "provider provision result contains failures"
        evidence = getattr(deployment, "expiry", None)
        expiry = getattr(evidence, "value", None)
        if expiry is not None and (expiry.state != "registered"
                                   or getattr(expiry.provider, "value", expiry.provider) != provider):
            return False, "provision output contains invalid expiry evidence"
        return True, None
    if stage == "prepare":
        return (getattr(value, "succeeded", None) is True and getattr(value, "failure", None) is None,
                "guest preparation did not return successful typed evidence")
    if stage == "stabilize":
        return (getattr(value, "ready", None) is True and getattr(value, "failure", None) is None,
                "readiness evidence is not ready")
    if stage == "measure":
        outcome = getattr(value, "outcome", None)
        if not isinstance(outcome, str):
            outcome = getattr(outcome, "outcome", None)
        failures = getattr(value, "failures", None)
        if failures is None:
            failures = getattr(getattr(value, "outcome", None), "failures", ())
        return (outcome == "succeeded" and not failures and getattr(value, "failure", None) is None,
                "guest measurement did not complete successfully")
    if stage == "close":
        return (getattr(value, "closed", None) is True and getattr(value, "failures", None) == (),
                "producer closure evidence is incomplete")
    if stage == "retrieve":
        return (getattr(value, "complete", None) is True and not getattr(value, "failures", ()),
                "both-role retrieval is incomplete")
    if stage == "cleanup":
        state = getattr(value, "cleanup_state", None)
        state = getattr(state, "value", state)
        blocked = getattr(value, "launch_blocked", False)
        failures = getattr(value, "failures", ()) or getattr(value, "secondary_failures", ())
        if isinstance(value, Mapping):
            state = value.get("cleanup_state", state)
            blocked = value.get("launch_blocked", blocked)
            failures = value.get("failures", failures)
        inventory = getattr(getattr(value, "inventory", None), "value", None)
        if isinstance(value, Mapping):
            inventory = value.get("inventory", inventory)
        inventory_proves_absence = False
        def empty_inventory(value: Any) -> bool:
            if isinstance(value, Mapping):
                return all(empty_inventory(item) for item in value.values())
            if isinstance(value, (list, tuple, set)):
                return len(value) == 0
            return value in (None, False, {}, [])
        if provider == "aws" and isinstance(inventory, Mapping):
            safety = inventory.get("safety_absent")
            regions = inventory.get("regions")
            inventory_proves_absence = (inventory.get("complete") is True
                and isinstance(safety, Mapping) and bool(safety) and all(item is True for item in safety.values())
                and isinstance(regions, Mapping) and bool(regions)
                and all(isinstance(item, Mapping) and item.get("complete") is True
                        and empty_inventory(item.get("resources")) for item in regions.values()))
        elif provider == "gcp" and isinstance(inventory, Mapping):
            inventory_proves_absence = (inventory.get("pages_complete") is True
                and inventory.get("zone_scope") == "all_project_zones"
                and inventory.get("completed_scopes") and inventory.get("resources") == [])
        elif provider == "azure":
            resources = getattr(value, "resources", None)
            if isinstance(value, Mapping):
                resources = value.get("resources", resources)
            inventory_proves_absence = (blocked is False and state == "succeeded"
                                        and isinstance(resources, list) and not resources)
        good = state == "succeeded" and blocked is False and not failures and inventory_proves_absence
        return good, "provider cleanup is failed, blocked, unknown or lacks typed success evidence"
    return False, f"no typed result contract exists for stage {stage}"


def _call(provider: str, stage: str, action: Callable[[], Any], result: ProviderLifecycleResult,
          *, before: Callable[[], None] | None = None,
          after: Callable[[Any, bool], None] | None = None,
          verify: Callable[[Any], None] | None = None) -> bool:
    try:
        if before is not None:
            try:
                before()
            except BaseException:
                if stage != "cleanup":
                    raise
        if stage == "create":
            result.creation_may_have_started = True
        value = action()
        result.values[stage] = value
        succeeded, reason = _typed_outcome(provider, stage, value)
        if succeeded and verify is not None:
            verify(value)
        if succeeded and stage == "create" and not isinstance(value, ProviderStageOutcome):
            protected = result.values.get("protect")
            from .contracts.deployment import ProviderDeploymentOutput
            deployment = value if isinstance(value, ProviderDeploymentOutput) else getattr(value, "deployment", None)
            registered = getattr(getattr(deployment, "expiry", None), "value", None)
            if (protected is None or registered is None or registered != protected
                    or registered.state != "registered" or registered.provider != provider):
                succeeded, reason = False, "created deployment does not retain the exact registered expiry evidence"
        if after is not None:
            after(value, succeeded)
        if succeeded:
            return True
        failure = f"{provider} {stage} failed ({reason})"
    except BaseException as exc:
        result.interrupted = isinstance(exc, (KeyboardInterrupt, SystemExit))
        if stage == "create":
            result.creation_may_have_started = True
        if after is not None:
            try:
                after(None, False)
            except BaseException:
                pass
        failure = f"{provider} {stage} failed ({type(exc).__name__})"
    if result.primary_failure is None:
        result.primary_failure = failure
    else:
        result.secondary_failures.append(failure)
    return False


def _stage_budget_name(stage: str) -> str:
    return {"protect":"registration", "claim":"registration", "create":"preparation",
        "prepare":"preparation", "stabilize":"preparation", "measure":"measurement",
        "close":"retrieval", "retrieve":"retrieval", "cleanup":"cleanup"}[stage]


def _remaining_f11_allowance(allowance: Any, stage: str) -> Any:
    """Represent the reserved allowance after completed lifecycle phases."""
    from dataclasses import replace
    completed = {
        "registration": (),
        "preparation": ("registration",),
        "measurement": ("registration", "preparation"),
        "retrieval": ("registration", "preparation", "measurement"),
    }
    if stage not in completed:
        raise ValueError(f"unsupported F11 stage allowance {stage}")
    fields = {
        "registration": (),
        "preparation": ("registration_seconds",),
        "measurement": ("registration_seconds", "provision_seconds", "readiness_seconds", "stabilization_seconds"),
        "retrieval": ("registration_seconds", "provision_seconds", "readiness_seconds", "stabilization_seconds",
                      "measurement_seconds", "measurement_shutdown_seconds"),
    }[stage]
    return replace(allowance, **{name: 0.0 for name in fields}, completed_stages=completed[stage])


def _before_stage(context: LifecycleContext, clock: Any, provider: str, stage: str,
                  hooks: ProviderHooks) -> None:
    if stage == "cleanup":
        return  # cleanup remains best effort after the measurement/copy deadline
    observed = getattr(clock, "monotonic", None)
    now = observed() if callable(observed) else observed
    if isinstance(now, bool) or not isinstance(now, (int, float)):
        import time
        now = time.monotonic()
    limit = context.provider_trigger_monotonic or context.cap_deadline_monotonic
    if now < context.cap_started_monotonic or now >= limit:
        raise TimeoutError(f"{provider} {stage} no longer fits the original cap/provider trigger")
    phase = _stage_budget_name(stage)
    if phase in context.stage_remainders:
        context.require_fit(phase, 0, now_monotonic=now)
    if hooks.stage_admission is not None:
        decision = hooks.stage_admission(phase)
        if getattr(decision, "eligible", None) is not True:
            reasons = tuple(getattr(decision, "reason_codes", ()))
            raise TimeoutError(f"{provider} {stage} denied by F11 stage fit: {','.join(reasons) or 'unknown'}")


@contextmanager
def _reachable_interrupts():
    """Turn SIGTERM into a catchable interruption only on the controller thread."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = {}
    def interrupt(signum, _frame):
        raise KeyboardInterrupt(f"received signal {signum}")
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, interrupt)
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def execute_provider_workflows(
    workflows: Mapping[str, ProviderHooks], *, context: LifecycleContext,
    clock: Any, max_workers: int = 3, recovery_path: Path | None = None,
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
    protection_attempted: set[str] = set()
    claim_ok: set[str] = set()
    protection_failed = False
    interrupted = False

    def utc_now() -> datetime:
        source = getattr(clock, "utc_now", None) or getattr(clock, "now", None)
        return source() if callable(source) else datetime.now(timezone.utc)

    def record_interruption(provider: str, exc: BaseException) -> None:
        nonlocal interrupted
        interrupted = True
        item = results[provider]
        item.interrupted = True
        message = f"{provider} controller interrupted ({type(exc).__name__})"
        if item.primary_failure is None:
            item.primary_failure = message
        else:
            item.secondary_failures.append(message)
        item.execution_state = "failed"

    def call(provider: str, stage: str, action: Callable[[], Any], *, verify=None) -> bool:
        nonlocal interrupted
        started_at = utc_now()
        try:
            before = None if stage == "cancel" else lambda: _before_stage(context, clock, provider, stage, workflows[provider])
            if recovery_path is not None and stage != "cancel":
                stage_before = before
                def before():
                    if stage_before is not None:
                        stage_before()
                    update_provider_recovery(recovery_path, provider, stage, "started")
                def after(_value, success):
                    update_provider_recovery(recovery_path, provider, stage,
                                             "succeeded" if success else "failed")
            else:
                after = None
            succeeded = _call(provider, stage, action, results[provider], before=before,
                              after=after, verify=verify)
        except BaseException as exc:
            results[provider].stage_times[stage] = {
                "started_at": started_at, "finished_at": utc_now()}
            record_interruption(provider, exc)
            return False
        results[provider].stage_times[stage] = {
            "started_at": started_at, "finished_at": utc_now()}
        if results[provider].interrupted:
            interrupted = True
        return succeeded

    try:
        with _reachable_interrupts():
            # Mark each provider liable before registration: a transport failure
            # may happen after the remote side has accepted the registration.
            for provider, hooks in workflows.items():
                protection_attempted.add(provider)
                if not call(provider, "protect", hooks.protect):
                    protection_failed = True
                    if results[provider].interrupted:
                        break
            if not protection_failed and not interrupted:
                for provider, hooks in workflows.items():
                    if call(provider, "claim", hooks.claim):
                        claim_ok.add(provider)
                    elif results[provider].interrupted:
                        break
            if claim_ok and not interrupted:
                def run_one(provider: str, hooks: ProviderHooks) -> None:
                    result = results[provider]
                    created = call(provider, "create", hooks.create)
                    if created:
                        result.execution_state = "provision_ready"
                        for stage in ("prepare", "stabilize", "measure"):
                            if not call(provider, stage, getattr(hooks, stage)):
                                result.execution_state = "failed"
                                if result.interrupted:
                                    call(provider, "cancel", hooks.cancel)
                                break
                            if stage == "measure":
                                result.execution_state = "running"
                    else:
                        result.execution_state = "failed"
                        if result.interrupted:
                            call(provider, "cancel", hooks.cancel)
                    # A create callback may fail after the provider accepted a
                    # request. Close and retrieve whenever creation was attempted.
                    if result.creation_may_have_started:
                        for stage in ("close", "retrieve"):
                            if not call(provider, stage, getattr(hooks, stage)):
                                result.execution_state = "failed"
                    if result.primary_failure is None:
                        result.execution_state = "succeeded"
                    else:
                        result.execution_state = "failed"

                with ThreadPoolExecutor(max_workers=min(max_workers, len(workflows))) as pool:
                    futures = {pool.submit(run_one, provider, workflows[provider]): provider for provider in claim_ok}
                    try:
                        for future in as_completed(futures):
                            try:
                                future.result()
                            except BaseException as exc:
                                provider = futures[future]
                                record_interruption(provider, exc)
                                for active_provider in claim_ok:
                                    if not results[active_provider].values.get("cancel"):
                                        call(active_provider, "cancel", workflows[active_provider].cancel)
                    except BaseException as exc:
                        interrupted = True
                        for provider in claim_ok:
                            record_interruption(provider, exc)
                            call(provider, "cancel", workflows[provider].cancel)
                    for provider in claim_ok:
                        result = results[provider]
                        if result.creation_may_have_started:
                            for stage in ("close", "retrieve"):
                                if stage not in result.values:
                                    call(provider, stage, getattr(workflows[provider], stage))
                        if result.interrupted:
                            result.execution_state = "failed"
            elif interrupted:
                for provider in protection_attempted:
                    call(provider, "cancel", workflows[provider].cancel)
    except BaseException as exc:
        interrupted = True
        provider = next(iter(protection_attempted), next(iter(workflows)))
        record_interruption(provider, exc)
        for active in protection_attempted:
            call(active, "cancel", workflows[active].cancel)
    finally:
        # Always join cleanup attempts for every possibly registered provider.
        def cleanup_one(provider: str) -> bool:
            # Route through call() so a successful terminal provider response is
            # durably reconciled before the attempt can report completion.
            hooks = workflows[provider]
            if hooks.cleanup_reconcile is None:
                return call(provider, "cleanup", hooks.cleanup)
            return call(provider, "cleanup", hooks.cleanup,
                verify=lambda result: hooks.cleanup_reconcile(result))
        with ThreadPoolExecutor(max_workers=min(max_workers, len(workflows))) as pool:
            futures = {pool.submit(cleanup_one, provider): provider
                       for provider in protection_attempted}
            for future in as_completed(futures):
                provider = futures[future]
                try:
                    succeeded = future.result()
                except BaseException as exc:
                    succeeded = False
                    item = results[provider]
                    item.interrupted = item.interrupted or isinstance(exc, (KeyboardInterrupt, SystemExit))
                    failure = f"{provider} cleanup failed ({type(exc).__name__})"
                    if item.primary_failure is None:
                        item.primary_failure = failure
                    else:
                        item.secondary_failures.append(failure)
                results[provider].cleanup_state = "succeeded" if succeeded else "failed"
    failures = [item.primary_failure for item in results.values() if item.primary_failure]
    failures.extend(f"{item.provider} cleanup failed" for item in results.values() if item.cleanup_state == "failed")
    completed = (not protection_failed and not interrupted and not failures
                 and all(item.execution_state == "succeeded" for item in results.values()))
    return LifecycleResult(results, completed, failures[0] if failures else None, started, datetime.now(timezone.utc))


def run_lifecycle(*, ledger: Any, reservation_request: Any, admission_plan: Any,
                  allowance: Any, workflow_factory: Callable[[Any], Mapping[str, ProviderHooks]],
                  context: LifecycleContext | None = None, clock: Any, max_workers: int = 3,
                  context_factory: Callable[[Mapping[str, Any]], LifecycleContext] | None = None,
                  recovery_path: Path | None = None,
                  recovery_factory: Callable[[Any, Mapping[str, ProviderHooks]], SavedLifecycleRecovery] | None = None) -> LifecycleResult:
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
    ledger_session = getattr(ledger, "controller_session_id", None)
    if ledger_session is not None and context is not None:
        context.assert_session(ledger_session)
    # The caller's monotonic window is only accepted when it is the exact
    # window that F11 reserved. Never let an unrelated caller context extend it.
    ledger_state = ledger.inspect()
    reservation = ledger_state.get("reservations", {}).get(reservation_id)
    if not isinstance(reservation, Mapping):
        return LifecycleResult({}, False, "reserved attempt is absent from the F11 ledger",
                               admission_status="reserved_but_unavailable",
                               reservation_evidence=decision.evidence)
    try:
        cap_start = float(reservation["cap_start_monotonic"])
        cap_seconds = float(reservation["cap_seconds"])
        provider_trigger = float(reservation.get("provider_trigger_monotonic", cap_start + cap_seconds))
        if context_factory is not None:
            if context is not None:
                raise ValueError("supply either lifecycle context or context_factory")
            context = context_factory(reservation)
        elif context is None:
            context = LifecycleContext(
                controller_session_id=ledger.controller_session_id,
                cap_started_monotonic=cap_start,
                cap_deadline_monotonic=cap_start+cap_seconds,
                lifetime_cap_seconds=cap_seconds,
                stage_remainders={
                    "registration": allowance.registration_seconds,
                    "preparation": allowance.preparation_seconds,
                    "measurement": allowance.measurement_seconds+allowance.measurement_shutdown_seconds,
                    "retrieval": allowance.retrieval_seconds,
                    "cleanup": allowance.cleanup_seconds,
                },
                provider_trigger_monotonic=provider_trigger)
        if (context.controller_session_id != ledger.controller_session_id
                or abs(context.cap_started_monotonic-cap_start) > 1e-6
                or abs(context.cap_deadline_monotonic-(cap_start+cap_seconds)) > 1e-6
                or abs(context.lifetime_cap_seconds-cap_seconds) > 1e-6):
            raise ValueError("caller lifecycle context does not match the original F11 reservation")
        context = __import__("dataclasses").replace(context, provider_trigger_monotonic=provider_trigger)
    except (KeyError, TypeError, ValueError) as exc:
        return LifecycleResult({}, False, f"reserved attempt timing is unavailable or inconsistent ({type(exc).__name__})",
                               admission_status="reserved_but_unavailable",
                               reservation_evidence=decision.evidence)
    fit = ledger.check_stage_fit(reservation_id, stage="registration", remaining=allowance)
    if not fit.eligible:
        return LifecycleResult({}, False, "complete workflow does not fit", admission_status="reserved_but_not_fit",
                               admission_reasons=tuple(fit.reason_codes), reservation_evidence=decision.evidence)
    from dataclasses import replace
    workflows = workflow_factory(decision.evidence)
    if not workflows:
        return LifecycleResult({}, False, "selected provider workflow factory returned no provider hooks",
                               admission_status="reserved_but_not_dispatched",
                               reservation_evidence=decision.evidence)
    # A production attempt cannot enter provider code until a private crash
    # journal for the exact selected deployment set is durably established.
    if recovery_path is None or recovery_factory is None:
        return LifecycleResult({}, False, "private lifecycle recovery was not initialized",
                               admission_status="reserved_but_not_dispatched",
                               reservation_evidence=decision.evidence)
    try:
        recovery = SavedLifecycleRecovery.validate_unique(
            recovery_factory(decision.evidence, workflows).model_dump(mode="python"))
        if (recovery.controller_session_id != context.controller_session_id
                or recovery.reservation_id != reservation_id
                or {row.provider for row in recovery.providers} != set(workflows)):
            raise ValueError("recovery identity does not match the reserved selected providers")
        write_recovery_journal(recovery_path, recovery)
    except BaseException as exc:
        return LifecycleResult({}, False, f"private lifecycle recovery initialization failed ({type(exc).__name__})",
                               admission_status="reserved_but_not_dispatched",
                               reservation_evidence=decision.evidence)
    def f11_stage_fit(stage: str):
        if stage == "cleanup":
            return None
        return ledger.check_stage_fit(reservation_id, stage=stage,
            remaining=_remaining_f11_allowance(allowance, stage))
    workflows = {provider: replace(hooks, stage_admission=f11_stage_fit)
                 for provider, hooks in workflows.items()}
    result = execute_provider_workflows(workflows, context=context, clock=clock, max_workers=max_workers,
                                        recovery_path=recovery_path)
    result.admission_status = decision.status
    result.reservation_evidence = decision.evidence
    return result
