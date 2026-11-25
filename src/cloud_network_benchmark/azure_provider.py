"""Small, Azure-specific V2 deployment and independent expiry driver.

All external operations are argv-based and injected. Importing this module is offline.
"""
from __future__ import annotations

import hashlib
import fcntl
import ipaddress
import json
import os
import re
import base64
import secrets
import struct
import shutil
import tempfile
import time
import uuid
from urllib.parse import quote
from contextvars import ContextVar
from datetime import date
from datetime import datetime, timedelta, timezone
from math import isfinite
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from pydantic import Field, field_validator, model_validator

from .contracts.admission import AdmissionRequirements, ExpiryEvidence, ExpiryRequest
from .contracts.common import (CleanupState, Evidence, ExperimentMode, FailureEvidence, IdentityContext,
    Provider, Scenario, StrictModel, StringEvidence, StructuredEvidence, VmRole)
from .contracts.deployment import (ProviderDeploymentInput, ProviderDeploymentOutput,
    ProviderVmOutput, RemoteConnectionData)
from .contracts.execution import CommandRequest, CommandResult, CommandRunner

UTC = timezone.utc


def _is_uuid_segment(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value.lower()
    except (ValueError, AttributeError):
        return False


def _azure_schedule_matches(props: Any, expected_trigger: datetime) -> bool:
    """Validate Azure's exact one-time UTC schedule readback."""
    if (not isinstance(props, dict) or props.get("isEnabled") is not True
            or props.get("frequency") != "OneTime"
            or not isinstance(props.get("timeZone"), str)
            or props.get("timeZone") not in {"UTC", "Etc/UTC"}):
        return False
    if expected_trigger.tzinfo is None or expected_trigger.utcoffset() != timedelta(0):
        return False
    for key in ("startTime", "nextRun", "expiryTime"):
        if key not in props or props[key] is None:
            if key == "startTime":
                return False
            continue
        value = props[key]
        if not isinstance(value, str):
            return False
        try:
            actual = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        if actual.tzinfo is None or actual.utcoffset() != timedelta(0) or actual != expected_trigger:
            return False
    return True


_EXPLICIT_UTC_NOW: ContextVar[Callable[[], datetime] | None] = ContextVar("azure_explicit_utc_now", default=None)
_EXPLICIT_TIMING_IDENTITY: ContextVar[dict[str, str] | None] = ContextVar("azure_explicit_timing_identity", default=None)
_TIMING_OBSERVATION_FAILURE: ContextVar[str | None] = ContextVar("azure_timing_observation_failure", default=None)
AUTOMATION_API = "2024-10-23"
COMPUTE_API = "2025-04-01"
ROLE_ACTIONS = ["Microsoft.Compute/virtualMachines/read",
    "Microsoft.Compute/virtualMachines/instanceView/read",
    "Microsoft.Compute/virtualMachines/deallocate/action"]
_KEY = re.compile(r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256) [A-Za-z0-9+/]+={0,3}(?: .*)?$")


class AzureRuntimeOptions(StrictModel):
    subscription_id: str
    tenant_id: str
    controller_cidr: str
    attempt_root: Path
    authentication_reference: str
    ssh_public_key: str
    selected_image: str
    complete_workflow_seconds: float = Field(gt=0)
    post_registration_workflow_seconds: float = Field(gt=0)
    cap_deadline_monotonic: float = Field(gt=0)
    # Original admission sample. Keeping this basis avoids remapping a fixed
    # cap through a later UTC/monotonic pair during Azure expiry registration.
    cap_started_monotonic: Optional[float] = Field(default=None, gt=0, exclude_if=lambda value: value is None)
    # Per-operation stage ceiling, derived from the immutable cap start. It is
    # transient and never replaces the original cap binding above.
    workflow_stage_deadline_monotonic: Optional[float] = Field(default=None, exclude=True)
    admission_evidence: dict[str, Any]
    retry_end_at: datetime
    poll_seconds: int = Field(ge=1, le=300)
    command_timeout_seconds: float = Field(gt=0)
    expiry_cost_input: dict[str, Any]
    plan_review_timeout_seconds: int = Field(default=0, ge=0, le=600)
    plan_review_admission_path: Optional[Path] = Field(default=None, exclude_if=lambda value: value is None)
    timing_profile: Optional[Literal["azure_exp901_sweden_acceptance_v1"]] = Field(
        default=None, exclude_if=lambda value: value is None)
    workflow_profile: Optional[Literal["all_cloud_acceptance_v1", "all_cloud_calibration_v1", "all_cloud_failure_acceptance_v1"]] = Field(
        default=None, exclude_if=lambda value: value is None)
    cleanup_timing_path: Optional[Path] = Field(default=None, exclude_if=lambda value: value is None)
    cleanup_timing_active: bool = Field(default=False, exclude_if=lambda value: value is False)
    cleanup_timing_phase: Optional[Literal["routine", "absence", "emergency"]] = Field(default=None, exclude_if=lambda value: value is None)
    profile_allowance: Optional[dict[str, float]] = Field(default=None, exclude_if=lambda value: value is None)
    azure_auxiliary_cleanup: Optional[dict[str, Any]] = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator("subscription_id", "tenant_id")
    @classmethod
    def uuid_text(cls, v: str) -> str:
        if str(uuid.UUID(v)) != v.lower():
            raise ValueError("subscription and tenant must be explicit UUIDs")
        return v.lower()

    @field_validator("controller_cidr")
    @classmethod
    def controller_host(cls, v: str) -> str:
        n = ipaddress.ip_network(v, strict=True)
        if n.version != 4 or n.prefixlen != 32:
            raise ValueError("controller_cidr must be an IPv4 /32")
        return v

    @field_validator("attempt_root")
    @classmethod
    def absolute_path(cls, v: Path) -> Path:
        if not v.is_absolute():
            raise ValueError("attempt_root must be absolute")
        return v

    @field_validator("retry_end_at")
    @classmethod
    def aware_retry(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("retry_end_at must be timezone-aware")
        return v.astimezone(UTC)

    @field_validator("selected_image")
    @classmethod
    def concrete_ubuntu_image(cls, v: str) -> str:
        if not v.strip() or "latest" in v.lower() or v == "ubuntu-24_04-lts":
            raise ValueError("selected_image must be a concrete Ubuntu 24.04 image version or ARM image ID")
        if not (v.startswith("/") or len(v.split(":")) == 4):
            raise ValueError("selected_image must be an ARM image ID or publisher:offer:sku:version")
        if not re.search(r"(?i)(ubuntu-24_04|ubuntu-24\.04|ubuntu2404|noble)",v):
            raise ValueError("selected_image must identify Ubuntu 24.04")
        if v.startswith("/") and ("/versions/" not in v.lower() or v.rstrip("/").split("/")[-1].lower()=="latest"):
            raise ValueError("gallery ARM image ID must identify a concrete image version")
        if not v.startswith("/") and v.split(":")[0].lower()!="canonical":
            raise ValueError("Ubuntu 24.04 marketplace images must use the Canonical publisher")
        return v

    @model_validator(mode="after")
    def complete_private_inputs(self) -> "AzureRuntimeOptions":
        if not self.authentication_reference.strip() or not self.ssh_public_key.strip():
            raise ValueError("private authentication reference and public key are required")
        _key_material(self.ssh_public_key)
        if self.timing_profile and self.workflow_profile:
            raise ValueError("Azure workflow and EXP-901 timing profiles cannot be combined")
        if (self.timing_profile or self.workflow_profile) and (self.cleanup_timing_path is None or not self.cleanup_timing_path.is_absolute()):
            raise ValueError("profile cleanup requires an absolute external timing-state path")
        if (self.timing_profile or self.workflow_profile) and self.cleanup_timing_path.is_relative_to(self.attempt_root):
            raise ValueError("profile cleanup timing state must survive provider attempt-root deletion")
        if self.workflow_profile and self.command_timeout_seconds > 120:
            raise ValueError("profile Azure commands are capped at 120 seconds")
        if self.timing_profile and self.command_timeout_seconds > 180:
            raise ValueError("EXP-901 Azure commands are capped at 180 seconds")
        if not self.admission_evidence.get("priority_review_reference") and self.admission_evidence.get("priority") in {"P2", "P3"}:
            raise ValueError("P2/P3 requires explicit priority review evidence")
        if self.admission_evidence.get("outstanding_cleanup") is not False:
            raise ValueError("outstanding cleanup must be explicitly false")
        if self.azure_auxiliary_cleanup is not None:
            from .azure_auxiliary_cleanup import validate_spec
            validate_spec(self.azure_auxiliary_cleanup)
        price = self.expiry_cost_input
        if not all(price.get(k) is not None for k in ("source_id", "date", "sha256", "automation_rate_per_job_minute", "job_minutes_bound")):
            raise ValueError("dated expiry cost input and bounded job minutes are required")
        if not re.fullmatch(r"[a-f0-9]{64}", str(price["sha256"])):
            raise ValueError("expiry price source hash must be SHA-256")
        try:
            date.fromisoformat(str(price["date"]))
        except ValueError as exc:
            raise ValueError("expiry price date must be an ISO calendar date") from exc
        if float(price["automation_rate_per_job_minute"]) < 0 or float(price["job_minutes_bound"]) <= 0:
            raise ValueError("expiry price and job bound must be nonnegative/positive")
        if not all(isfinite(x) for x in (self.complete_workflow_seconds, self.post_registration_workflow_seconds,
                self.cap_deadline_monotonic, self.poll_seconds, self.command_timeout_seconds,
                float(price["automation_rate_per_job_minute"]), float(price["job_minutes_bound"]))):
            raise ValueError("time and price bounds must be finite")
        if (self.workflow_stage_deadline_monotonic is not None
                and (not isfinite(self.workflow_stage_deadline_monotonic)
                    or self.workflow_stage_deadline_monotonic <= 0
                    or self.workflow_stage_deadline_monotonic > self.cap_deadline_monotonic)):
            raise ValueError("Azure workflow stage deadline must be finite and within the original cap")
        return self


class AzureRecovery(StrictModel):
    identity: IdentityContext
    reservation_id: str
    config_sha256: str
    namespace: str
    resource_group: str
    vm_ids: list[str] = Field(min_length=2, max_length=2)
    expiry: dict[str, Any]
    pair_state: str
    expiry_state: str
    creation_may_have_started: bool = False
    actions: list[dict[str, Any]] = Field(default_factory=list)
    resources: list[dict[str, Any]] = Field(default_factory=list)
    failures: list[dict[str, Any]] = Field(default_factory=list)
    original_deadline_monotonic: float
    cap_started_at: datetime
    known_hosts_reference: Optional[str] = None


class AzureProviderResult(StrictModel):
    deployment: Optional[ProviderDeploymentOutput] = None
    expiry: ExpiryEvidence
    actions: list[dict[str, Any]] = Field(default_factory=list)
    failures: list[FailureEvidence] = Field(default_factory=list)
    recovery_reference: str
    cleanup_required: bool


class AzureCleanupResult(StrictModel):
    identity: IdentityContext
    cleanup_state: CleanupState
    attempted_at: datetime
    finished_at: datetime
    actions: list[dict[str, Any]] = Field(default_factory=list)
    failures: list[FailureEvidence] = Field(default_factory=list)
    resources: list[dict[str, Any]] = Field(default_factory=list)
    launch_blocked: bool
    timing_status: Optional[Literal["within_deadline", "late", "unknown"]] = Field(
        default=None, exclude_if=lambda value: value is None)
    timing_failure: Optional[FailureEvidence] = Field(default=None, exclude_if=lambda value: value is None)


def _slug(d: ProviderDeploymentInput) -> str:
    stem = re.sub(r"[^a-z0-9-]", "-", f"cnb-{d.campaign_id}-{d.deployment_id}".lower()).strip("-")
    suffix = hashlib.sha256(f"{d.campaign_id}\0{d.deployment_id}".encode()).hexdigest()[:8]
    return stem[:36].rstrip("-") + "-" + suffix


def _validate_request(d: ProviderDeploymentInput, o: AzureRuntimeOptions, now: datetime,
                     mono: float | None, *, require_registered: bool = False,
                     check_live_clock: bool = True) -> tuple[str, list[str]]:
    if Provider(d.provider) != Provider.AZURE:
        raise ValueError("Azure driver requires provider=azure")
    if d.vm_a.vm_shape != "Standard_D4s_v5" or d.vm_b.vm_shape != "Standard_D4s_v5":
        raise ValueError("Azure shape must be Standard_D4s_v5")
    if d.vm_a.disk.kind != "StandardSSD_LRS_E4" or d.vm_b.disk.kind != "StandardSSD_LRS_E4":
        raise ValueError("Azure disk must be StandardSSD_LRS_E4")
    if d.vm_a.acceleration.kind != "accelerated_networking" or d.vm_b.acceleration.kind != "accelerated_networking":
        raise ValueError("both Azure NICs require Accelerated Networking")
    expected = {
        Scenario.SAME_ZONE: ("germanywestcentral", "germanywestcentral", "1", "1", "none"),
        Scenario.CROSS_ZONE: ("germanywestcentral", "germanywestcentral", "1", "2", "none"),
        Scenario.PLACEMENT_OPTIMIZATION: ("germanywestcentral", "germanywestcentral", "1", "1", "proximity_placement_group"),
        Scenario.INTER_REGION: ("germanywestcentral", "uksouth", "1", "1", "none"),
    }[Scenario(d.scenario)]
    got = (d.vm_a.region, d.vm_b.region, d.vm_a.zone, d.vm_b.zone, d.placement.kind)
    sweden_locality = ("swedencentral", "swedencentral", "1", "1", "none")
    sweden_placement = ("swedencentral", "swedencentral", "1", "1", "proximity_placement_group")
    sweden_research = (d.experiment_id in {"EXP-001", "EXP-003"}
        and d.mode in {ExperimentMode.SHORT_STUDY, ExperimentMode.CALIBRATION, ExperimentMode.DIAGNOSTIC_DAY}
        and d.context == "standard"
        and got == (sweden_placement if d.scenario == Scenario.PLACEMENT_OPTIMIZATION else sweden_locality))
    sweden_replacement = (d.experiment_id in {"EXP-001", "EXP-003"}
        and d.mode == ExperimentMode.SHORT_STUDY and d.context == "replacement"
        and d.replaces_run_id is not None and d.replaces_run_id.endswith("-azure")
        and got == (sweden_placement if d.scenario == Scenario.PLACEMENT_OPTIMIZATION else sweden_locality))
    sweden_acceptance = (d.mode == ExperimentMode.ACCEPTANCE
        and ((d.experiment_id == "EXP-901" and d.context == "failure_check" and d.scenario == Scenario.SAME_ZONE and got == sweden_locality)
             or (d.experiment_id == "EXP-903" and d.context == "standard" and d.scenario == Scenario.SAME_ZONE and got == sweden_locality)
             or (d.experiment_id == "EXP-003" and d.context == "standard" and d.scenario == Scenario.PLACEMENT_OPTIMIZATION and got == sweden_placement)))
    required_north = ((d.experiment_id in {"EXP-001", "EXP-003"}
                       and d.mode in {ExperimentMode.SHORT_STUDY, ExperimentMode.CALIBRATION, ExperimentMode.DIAGNOSTIC_DAY})
                      or (d.mode == ExperimentMode.ACCEPTANCE and
                          ((d.experiment_id == "EXP-903" and d.scenario == Scenario.SAME_ZONE)
                           or (d.experiment_id == "EXP-003" and d.scenario == Scenario.PLACEMENT_OPTIMIZATION))))
    if required_north and not (sweden_research or sweden_replacement or sweden_acceptance):
        raise ValueError("primary S1/S3 cannot fall back from Sweden Central without a reviewed region-specific exception")
    if got != expected and not (sweden_research or sweden_replacement or sweden_acceptance):
        raise ValueError(f"scenario locality/placement mismatch: expected {expected}, got {got}")
    if d.scenario == Scenario.PLACEMENT_OPTIMIZATION and not d.placement.name:
        raise ValueError("placement optimization requires a named PPG")
    if d.scenario != Scenario.PLACEMENT_OPTIMIZATION and d.placement.name is not None:
        raise ValueError("PPG is only valid for placement_optimization")
    if d.vm_a.connection_user != d.vm_b.connection_user:
        raise ValueError("Azure pair requires the same configured admin user on both VMs")
    reservation = d.reservation.value
    if reservation is None or reservation.state != "reserved" or reservation.reservation_id.value is None:
        raise ValueError("matching active reservation evidence is required")
    if reservation.campaign_id != d.campaign_id or reservation.expires_at.value is None or reservation.cap_started_at.value is None:
        raise ValueError("reservation identity and cap evidence must be complete")
    profile=d.admission.timing_profile
    if profile != o.timing_profile:
        raise ValueError("Azure runtime timing profile differs from source-bound deployment")
    if d.reservation.value.timing_profile != profile:
        raise ValueError("Azure reservation timing profile differs from deployment")
    workflow_profile=d.admission.workflow_profile
    mode_cap=int(AdmissionRequirements.expected_for(d.mode,d.scenario,profile,workflow_profile)["lifetime_cap_seconds"])
    if d.admission.lifetime_cap_seconds != mode_cap:
        raise ValueError("admitted lifetime cap does not match the selected mode")
    if profile:
        from .admission import AZURE_EXP901_ALLOWANCE, AZURE_EXP901_PROVIDER_LEAD_SECONDS
        expected_allowance = {**AZURE_EXP901_ALLOWANCE,
            "azure_provider_lead_seconds": AZURE_EXP901_PROVIDER_LEAD_SECONDS}
        if o.profile_allowance != expected_allowance:
            raise ValueError("Azure EXP-901 profile requires the exact registered workflow allowance vector")
        if (o.complete_workflow_seconds != 1480 or o.post_registration_workflow_seconds != 1300
                or o.command_timeout_seconds > 180
                or (d.cleanup_timeout_seconds or 300) > 300):
            raise ValueError("Azure EXP-901 profile workflow or command allowance differs from the reviewed envelope")
    elif workflow_profile is not None:
        from .validation_profiles import PROFILE_SPECS
        source_allowance = PROFILE_SPECS[workflow_profile]["allowance"]
        expected_allowance = {key: float(source_allowance[key]) for key in (
            "registration_seconds", "provision_seconds", "readiness_seconds",
            "stabilization_seconds", "measurement_seconds", "measurement_shutdown_seconds",
            "retrieval_seconds", "cleanup_seconds")}
        expected_allowance["azure_provider_lead_seconds"] = float(
            source_allowance["provider_leads_seconds"]["azure"])
        if o.profile_allowance != expected_allowance:
            raise ValueError("Azure workflow profile requires the exact registered allowance vector")
        if o.command_timeout_seconds > 120:
            raise ValueError("profile Azure commands are capped at 120 seconds")
    mode_measurement_seconds={"acceptance":30,"short_study":180,"calibration":720,"diagnostic_day":86400}[ExperimentMode(d.mode).value]
    if (o.complete_workflow_seconds < mode_measurement_seconds or o.post_registration_workflow_seconds < mode_measurement_seconds
            or o.complete_workflow_seconds > mode_cap or o.post_registration_workflow_seconds > mode_cap):
        raise ValueError("workflow bounds do not cover the selected mode or exceed its lifetime ceiling")
    if now < reservation.cap_started_at.value or reservation.expires_at.value-reservation.cap_started_at.value > timedelta(seconds=mode_cap):
        raise ValueError("reservation chronology or original mode lifetime ceiling is invalid")
    if profile and reservation.expires_at.value-reservation.cap_started_at.value != timedelta(seconds=1800):
        raise ValueError("Azure EXP-901 profile requires the exact original 1800-second reservation cap")
    if o.admission_evidence.get("reservation_id") != reservation.reservation_id.value or o.admission_evidence.get("source_sha256") != reservation.source_sha256:
        raise ValueError("admission snapshot must bind this reservation and its source hash")
    expiry_cost_bound = float(o.expiry_cost_input["automation_rate_per_job_minute"]) * float(o.expiry_cost_input["job_minutes_bound"])
    if expiry_cost_bound > reservation.reserved_cost_usd.value:
        raise ValueError("bounded Azure expiry job cost exceeds the explicitly reserved deployment cost")
    if check_live_clock:
        if mono is None:
            raise ValueError("live Azure request validation requires the injected monotonic clock")
        if reservation.expires_at.value <= now or mono >= o.cap_deadline_monotonic:
            raise ValueError("reservation/deadline has expired")
        if now + timedelta(seconds=o.cap_deadline_monotonic-mono) > reservation.expires_at.value:
            raise ValueError("original monotonic deadline extends beyond the reserved UTC cap")
        if o.retry_end_at <= now or o.retry_end_at > reservation.expires_at.value:
            raise ValueError("retry end must follow trigger and remain within original reservation")
        if reservation.cap_started_at.value + timedelta(seconds=o.complete_workflow_seconds) > now + timedelta(seconds=(o.cap_deadline_monotonic-mono)):
            raise ValueError("complete workflow bound exceeds original cap")
        if (reservation.expires_at.value - now).total_seconds() < o.post_registration_workflow_seconds:
            raise ValueError("post-registration workflow does not fit before trigger")
    if reservation.cap_started_at.value + timedelta(seconds=o.complete_workflow_seconds) > reservation.expires_at.value:
        raise ValueError("complete workflow exceeds reserved cap")
    if d.admission.priority in {"P2", "P3"} and not o.admission_evidence.get("priority_review_reference"):
        raise ValueError("priority review is required for P2/P3")
    if d.admission.priority != o.admission_evidence.get("priority"):
        raise ValueError("admission evidence priority mismatch")
    expiry = d.expiry.value
    if require_registered:
        if expiry is None or expiry.state != "registered" or expiry.provider != Provider.AZURE or expiry.action != "deallocate":
            raise ValueError("confirmed Azure deallocation registration required before pair apply")
        if expiry.run_id != d.run_id or expiry.deployment_id != d.deployment_id or expiry.expires_at.value > reservation.expires_at.value:
            raise ValueError("expiry evidence does not match reservation and deployment")
        if expiry.registered_at.value is None or expiry.registered_at.value > now or expiry.registered_at.value < reservation.cap_started_at.value:
            raise ValueError("expiry registration chronology is invalid")
        metadata=expiry.provider_metadata.value or {}
        if (metadata.get("reservation_id") != reservation.reservation_id.value
                or metadata.get("source_sha256") != reservation.source_sha256):
            raise ValueError("registered expiry metadata is not bound to this reservation source")
        if expiry.expires_at.value >= o.retry_end_at or (o.retry_end_at-expiry.expires_at.value).total_seconds() > float(o.expiry_cost_input["job_minutes_bound"])*60:
            raise ValueError("registered expiry trigger or retry window no longer matches the bound price")
        if (expiry.expires_at.value-now).total_seconds() < o.post_registration_workflow_seconds:
            raise ValueError("post-registration workflow no longer fits before expiry")
    return _slug(d), [f"/subscriptions/{o.subscription_id}/resourceGroups/{_slug(d)}/providers/Microsoft.Compute/virtualMachines/{name}" for name in (_slug(d)+"-a", _slug(d)+"-b")]


def _atomic_private(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, name = tempfile.mkstemp(prefix=".recovery-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, sort_keys=True, separators=(",", ":"))
            f.flush(); os.fsync(f.fileno())
        os.replace(name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(name): os.unlink(name)


def _clock_identity() -> str:
    """Identify the OS monotonic-clock epoch so a reboot cannot renew cleanup time."""
    import platform
    boot_id = Path("/proc/sys/kernel/random/boot_id")
    if boot_id.is_file():
        epoch = boot_id.read_text(encoding="ascii").strip()
    else:
        # On platforms without a kernel boot UUID, bind to the host and its
        # wall-minus-monotonic boot epoch. A clock step is handled separately
        # as a sticky timing failure.
        epoch = str(round(time.time() - time.monotonic()))
    return hashlib.sha256(f"{platform.system()}\0{platform.node()}\0{epoch}".encode()).hexdigest()


def _profile_timing_digest(value: dict[str, Any]) -> str:
    payload = {key: item for key, item in value.items() if key != "sha256"}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _profile_name(deployment: ProviderDeploymentInput) -> str | None:
    return deployment.admission.timing_profile or deployment.admission.workflow_profile


def _profile_timing_identity(deployment: ProviderDeploymentInput) -> dict[str,str]:
    return {"profile":_profile_name(deployment) or "",
        "run_id":deployment.run_id,"deployment_id":deployment.deployment_id,
        "reservation_id":deployment.reservation.value.reservation_id.value or "",
        "config_sha256":deployment.config_sha256}


def _source_cap_deadline(options: AzureRuntimeOptions, reservation: Any) -> float | None:
    """Map the reservation cap from its original admission monotonic sample."""
    if options.cap_started_monotonic is None:
        return None
    duration = (reservation.expires_at.value - reservation.cap_started_at.value).total_seconds()
    if duration <= 0:
        raise ValueError("original Azure reservation cap interval is invalid")
    return float(options.cap_started_monotonic) + duration


def _utc_from_clock(clock: Callable[[], float]) -> Callable[[], datetime]:
    owner = getattr(clock, "__self__", None)
    candidate = getattr(owner, "utc_now", None)
    explicit = _EXPLICIT_UTC_NOW.get()
    return candidate if callable(candidate) else explicit if explicit is not None else lambda: datetime.now(UTC)


def _profile_timing_mutate(options: AzureRuntimeOptions, update: Callable[[dict[str, Any]], dict[str, Any]], *,
                           create: bool = False, expected_identity: dict[str,str] | None = None) -> dict[str, Any]:
    path = options.cleanup_timing_path
    if path is None or not path.is_absolute() or path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("external Azure cleanup timing state path is missing or unsafe")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    lock_path = path.with_name(path.name + ".lock")
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchmod(lock_fd, 0o600); fcntl.flock(lock_fd, fcntl.LOCK_EX)
        if path.is_symlink():
            raise ValueError("external Azure cleanup timing state cannot be a symlink")
        if path.exists():
            if path.stat().st_mode & 0o077:
                raise PermissionError("external Azure cleanup timing state must remain private")
            current = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(current, dict) or current.get("schema_version") != 1
                    or current.get("sha256") != _profile_timing_digest(current)):
                raise ValueError("external Azure cleanup timing state is corrupt")
        elif create:
            current = {}
        else:
            raise FileNotFoundError("original Azure cleanup timing anchor is unavailable")
        if expected_identity and any(current.get(key)!=value for key,value in expected_identity.items()):
            raise ValueError("external Azure cleanup timing state belongs to another attempt")
        updated = update(dict(current))
        updated["sha256"] = _profile_timing_digest(updated)
        _atomic_private(path, updated)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return updated
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN); os.close(lock_fd)


def _initialize_profile_timing(deployment: ProviderDeploymentInput, options: AzureRuntimeOptions,
                               *, clock: Callable[[], float], utc_now: Callable[[], datetime],
                               origin_monotonic: float | None = None,
                               origin_utc: datetime | None = None) -> None:
    profile_name = _profile_name(deployment)
    if profile_name is None:
        return
    mono = float(clock()) if origin_monotonic is None else float(origin_monotonic)
    wall = (utc_now() if origin_utc is None else origin_utc).astimezone(UTC)
    if (options.cleanup_timing_path is not None and not options.cleanup_timing_path.exists()
            and (options.attempt_root / "recovery.json").exists()):
        raise ValueError("missing profile timing anchor cannot be recreated after provider recovery state exists")
    reservation = deployment.reservation.value
    source_cap = _source_cap_deadline(options, reservation)
    if (source_cap is not None
            and options.cap_deadline_monotonic > source_cap + 1e-6):
        raise ValueError("Azure runtime cap exceeds the original admission monotonic cap")
    body = {"schema_version": 1, "profile": profile_name,
        "run_id": deployment.run_id, "deployment_id": deployment.deployment_id,
        "reservation_id": reservation.reservation_id.value,
        "config_sha256": deployment.config_sha256,
        "clock_identity": _clock_identity(), "origin_monotonic": mono,
        "origin_utc": wall.isoformat(), "last_monotonic": mono, "last_utc": wall.isoformat(),
        "cap_deadline_monotonic": options.cap_deadline_monotonic,
        "cap_started_monotonic": options.cap_started_monotonic,
        "source_cap_deadline_monotonic": _source_cap_deadline(options, reservation),
        "cap_started_at": reservation.cap_started_at.value.isoformat(),
        "cap_expires_at": reservation.expires_at.value.isoformat(),
        "provider_stop_at": None, "first_cleanup_monotonic": None,
        "first_cleanup_at": None, "routine_deadline_monotonic": None,
        "absence_deadline_monotonic": None, "routine_deadline_utc": None,
        "absence_deadline_utc": None, "fresh_absence_deadline_utc": None,
        "cleanup_invocations": 0,
        "fresh_absence_deadline_monotonic": None,
        "timing_status": "within_deadline", "timing_failure": None,
        "absence_verified_at": None}
    def initialize(current):
        if not current:
            return body
        if any(current.get(key) != body[key] for key in ("profile", "run_id", "deployment_id", "reservation_id", "config_sha256",
                "clock_identity", "cap_deadline_monotonic", "cap_started_monotonic",
                "source_cap_deadline_monotonic", "cap_started_at", "cap_expires_at")):
            raise ValueError("existing Azure cleanup clock anchor differs from the original source-bound attempt")
        return current
    _profile_timing_mutate(options, initialize, create=True)


def _bind_profile_stop(deployment: ProviderDeploymentInput, options: AzureRuntimeOptions,
                       stop_at: datetime) -> None:
    if _profile_name(deployment) is None:
        return
    stop = stop_at.astimezone(UTC)
    def bind(state):
        if (state.get("run_id") != deployment.run_id or state.get("deployment_id") != deployment.deployment_id
                or state.get("reservation_id") != deployment.reservation.value.reservation_id.value
                or state.get("config_sha256") != deployment.config_sha256):
            raise ValueError("Azure cleanup timing anchor identity differs from registered deployment")
        existing = state.get("provider_stop_at")
        if existing is not None and existing != stop.isoformat():
            raise ValueError("Azure registered STOP changed after its timing anchor was saved")
        if stop <= datetime.fromisoformat(state["origin_utc"]) or stop > datetime.fromisoformat(state["cap_expires_at"]):
            raise ValueError("actual registered STOP is outside the original cap chronology")
        state["provider_stop_at"] = stop.isoformat()
        return state
    _profile_timing_mutate(options, bind,expected_identity=_profile_timing_identity(deployment))


def _require_profile_timing_anchor(deployment: ProviderDeploymentInput, options: AzureRuntimeOptions,
                                   *, require_stop: bool) -> dict[str, Any] | None:
    profile_name = _profile_name(deployment)
    if profile_name is None:
        return None
    def check(state):
        if (state.get("profile") != profile_name
                or state.get("run_id") != deployment.run_id
                or state.get("deployment_id") != deployment.deployment_id
                or state.get("reservation_id") != deployment.reservation.value.reservation_id.value
                or state.get("config_sha256") != deployment.config_sha256):
            raise ValueError("Azure cleanup timing anchor identity mismatch")
        if state.get("clock_identity") != _clock_identity():
            raise ValueError("Azure cleanup timing anchor clock identity changed")
        if require_stop and state.get("provider_stop_at") != deployment.expiry.value.expires_at.value.isoformat():
            raise ValueError("actual registered STOP is absent from the original cleanup timing anchor")
        return state
    return _profile_timing_mutate(options, check,expected_identity=_profile_timing_identity(deployment))


def _observe_profile_cleanup_clock(options: AzureRuntimeOptions, *, clock: Callable[[], float],
                                   utc_now: Callable[[], datetime], phase: str = "routine") -> dict[str, Any] | None:
    if options.timing_profile is None and options.workflow_profile is None:
        return None
    mono = float(clock()); wall = utc_now().astimezone(UTC)
    def observe(state):
        if state.get("first_cleanup_monotonic") is None:
            return state
        if state.get("clock_identity") != _clock_identity():
            state["timing_status"] = "unknown"
            state["timing_failure"] = state.get("timing_failure") or "cleanup resumed on a different monotonic clock epoch"
        previous_mono = float(state.get("last_monotonic", mono))
        previous_wall = datetime.fromisoformat(state.get("last_utc", wall.isoformat()))
        if mono < previous_mono or wall < previous_wall:
            state["timing_status"] = "unknown"
            state["timing_failure"] = state.get("timing_failure") or "cleanup clock moved backwards"
        bound = state.get("fresh_absence_deadline_monotonic") if phase == "absence" else state.get("routine_deadline_monotonic")
        utc_bound = (state.get("fresh_absence_deadline_utc") or state.get("absence_deadline_utc")
            if phase == "absence" else state.get("routine_deadline_utc"))
        utc_elapsed = utc_bound is None or wall >= datetime.fromisoformat(utc_bound)
        if ((bound is not None and mono >= float(bound)) or utc_elapsed) and state.get("timing_status") == "within_deadline":
            state["timing_status"] = "late"
            state["timing_failure"] = state.get("timing_failure") or f"profile {phase} UTC or monotonic deadline elapsed"
        state["last_monotonic"] = max(previous_mono, mono)
        if wall >= previous_wall:
            state["last_utc"] = wall.isoformat()
        return state
    try:
        return _profile_timing_mutate(options, observe,expected_identity=_EXPLICIT_TIMING_IDENTITY.get())
    except (OSError, ValueError, PermissionError, json.JSONDecodeError) as exc:
        _TIMING_OBSERVATION_FAILURE.set(f"profile timing observation failed ({type(exc).__name__})")
        return None


def _begin_profile_cleanup(deployment: ProviderDeploymentInput, options: AzureRuntimeOptions, *,
                           clock: Callable[[], float], utc_now: Callable[[], datetime],
                           invocation_seconds: float) -> tuple[float, dict[str, Any] | None, str | None]:
    now_mono = float(clock()); now_utc = utc_now().astimezone(UTC)
    profile_name = _profile_name(deployment)
    if profile_name is None:
        return now_mono + max(1.0, invocation_seconds), None, None
    if deployment.admission.timing_profile:
        fallback = now_mono + min(300.0, max(1.0, invocation_seconds))
    elif deployment.admission.workflow_profile:
        fallback = now_mono + min(660.0, max(1.0, invocation_seconds))
    else:
        fallback_end = min(options.cap_deadline_monotonic, now_mono + min(660.0, max(1.0, invocation_seconds)))
        if deployment.expiry.value is not None and deployment.expiry.value.expires_at.value is not None:
            stop_remaining = (deployment.expiry.value.expires_at.value.astimezone(UTC)-now_utc).total_seconds()
            fallback_end = min(fallback_end, now_mono + max(0.0, stop_remaining))
        fallback = fallback_end
    try:
        def begin(state):
            if (state.get("profile") != profile_name
                    or state.get("run_id") != deployment.run_id
                    or state.get("deployment_id") != deployment.deployment_id
                    or state.get("reservation_id") != deployment.reservation.value.reservation_id.value
                    or state.get("config_sha256") != deployment.config_sha256):
                raise ValueError("profile cleanup timing anchor identity mismatch")
            failure = state.get("timing_failure")
            status = state.get("timing_status", "unknown")
            if state.get("clock_identity") != _clock_identity():
                status, failure = "unknown", failure or "cleanup clock identity is unrecognized"
            missing_stop = state.get("provider_stop_at") is None
            if missing_stop:
                status, failure = "unknown", failure or "actual registered STOP timing anchor is missing"
            if (state.get("first_cleanup_monotonic") is None
                    and state.get("missing_stop_cleanup_observed_monotonic") is None):
                cap_mono = float(state["cap_deadline_monotonic"])
                cap_utc = datetime.fromisoformat(state["cap_expires_at"])
                if missing_stop:
                    # Continue bounded owned cleanup using only the original cap. This does not
                    # invent a STOP or restore acceptance timing status.
                    routine_mono = min(now_mono + 600.0, cap_mono - 60.0)
                    absence_mono = min(now_mono + 660.0, cap_mono)
                    routine_utc = min(now_utc + timedelta(seconds=600), cap_utc - timedelta(seconds=60))
                    absence_utc = min(now_utc + timedelta(seconds=660), cap_utc)
                else:
                    stop = datetime.fromisoformat(state["provider_stop_at"])
                    origin = datetime.fromisoformat(state["origin_utc"])
                    stop_mono = float(state["origin_monotonic"]) + (stop - origin).total_seconds()
                    routine_mono = min(now_mono + 600.0, stop_mono - 60.0, cap_mono - 60.0)
                    absence_mono = min(now_mono + 660.0, stop_mono, cap_mono)
                    routine_utc = min(now_utc + timedelta(seconds=600), stop - timedelta(seconds=60),
                        cap_utc - timedelta(seconds=60))
                    absence_utc = min(now_utc + timedelta(seconds=660), stop, cap_utc)
                if missing_stop:
                    state.update(missing_stop_cleanup_observed_monotonic=now_mono,
                        missing_stop_cleanup_observed_at=now_utc.isoformat())
                else:
                    state.update(first_cleanup_monotonic=now_mono, first_cleanup_at=now_utc.isoformat())
                state.update(routine_deadline_monotonic=routine_mono, absence_deadline_monotonic=absence_mono,
                    routine_deadline_utc=routine_utc.isoformat(), absence_deadline_utc=absence_utc.isoformat())
            prior_mono = float(state.get("last_monotonic", now_mono))
            prior_utc = datetime.fromisoformat(state.get("last_utc", now_utc.isoformat()))
            if now_mono < prior_mono or now_utc < prior_utc:
                status, failure = "unknown", failure or "cleanup clock moved backwards"
            if state["cleanup_invocations"] >= 3:
                status, failure = "late", failure or "supported cleanup and two recoveries were exhausted"
            routine = state.get("routine_deadline_monotonic")
            routine_utc = state.get("routine_deadline_utc")
            absence = state.get("fresh_absence_deadline_monotonic")
            absence_utc = state.get("fresh_absence_deadline_utc") or state.get("absence_deadline_utc")
            in_absence_recovery = (state.get("absence_started_at") is not None and absence is not None
                and absence_utc is not None and now_mono < float(absence)
                and now_utc < datetime.fromisoformat(absence_utc))
            if ((routine is None or now_mono >= float(routine) or routine_utc is None
                    or now_utc >= datetime.fromisoformat(routine_utc)) and not in_absence_recovery):
                status, failure = "late", failure or "shared routine cleanup deadline elapsed"
            if missing_stop:
                # Expiry of the bounded cleanup window cannot turn a missing
                # registered STOP into a measured late/within-deadline result.
                status = "unknown"
                failure = failure or "actual registered STOP timing anchor is missing"
            state["cleanup_invocations"] += 1
            state["timing_status"] = status
            state["timing_failure"] = failure
            state["last_monotonic"] = max(prior_mono, now_mono)
            if now_utc >= prior_utc:
                state["last_utc"] = now_utc.isoformat()
            return state
        state = _profile_timing_mutate(options, begin,expected_identity=_profile_timing_identity(deployment))
        routine = state.get("routine_deadline_monotonic")
        absence = state.get("fresh_absence_deadline_monotonic")
        absence_recovery = (state.get("absence_started_at") is not None and absence is not None
            and now_mono < float(absence) and (state.get("fresh_absence_deadline_utc") is not None
                and now_utc < datetime.fromisoformat(state["fresh_absence_deadline_utc"])))
        within = state.get("timing_status") == "within_deadline" and (routine is not None or absence_recovery)
        utc_bound = (state.get("fresh_absence_deadline_utc") or state.get("absence_deadline_utc")
            if absence_recovery else state.get("routine_deadline_utc"))
        utc_mapped = now_mono + max(0.0, (datetime.fromisoformat(utc_bound) - now_utc).total_seconds()) if utc_bound else now_mono
        deadline = min(fallback, float(absence if absence_recovery else routine), utc_mapped) if within else fallback
        return deadline, state, None if within else str(state.get("timing_failure") or "cleanup timing is unknown")
    except (OSError, ValueError, PermissionError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return fallback, None, f"cleanup timing anchor unavailable ({type(exc).__name__})"


def _finish_profile_timing(options: AzureRuntimeOptions, *, clock: Callable[[], float],
                           utc_now: Callable[[], datetime], absence_verified: bool) -> dict[str, Any] | None:
    if options.timing_profile is None and options.workflow_profile is None:
        return None
    mono = float(clock()); wall = utc_now().astimezone(UTC)
    def finish(state):
        in_absence_phase = absence_verified or state.get("absence_started_at") is not None
        bound = (state.get("fresh_absence_deadline_monotonic") or state.get("absence_deadline_monotonic")
            if in_absence_phase else state.get("routine_deadline_monotonic"))
        utc_bound = (state.get("fresh_absence_deadline_utc") or state.get("absence_deadline_utc")
            if in_absence_phase else state.get("routine_deadline_utc"))
        if ((bound is not None and mono >= float(bound)) or utc_bound is None
                or wall >= datetime.fromisoformat(utc_bound)) and state.get("timing_status") == "within_deadline":
            state["timing_status"] = "late"
            state["timing_failure"] = f"profile {'absence' if in_absence_phase else 'routine cleanup'} deadline elapsed"
        previous_mono = float(state.get("last_monotonic", mono))
        previous_wall = datetime.fromisoformat(state.get("last_utc", wall.isoformat()))
        if mono < previous_mono or wall < previous_wall:
            state["timing_status"] = "unknown"
            state["timing_failure"] = state.get("timing_failure") or "cleanup clock moved backwards"
        state["last_monotonic"] = max(previous_mono, mono)
        if wall >= previous_wall:
            state["last_utc"] = wall.isoformat()
        if absence_verified:
            state["absence_verified_at"] = wall.isoformat()
        return state
    try:
        state = _profile_timing_mutate(options, finish,expected_identity=_EXPLICIT_TIMING_IDENTITY.get())
        # Include the time spent writing and syncing the durable observation.
        # Otherwise a slow fsync could persist a false within-deadline result.
        persisted_mono = float(clock()); persisted_wall = utc_now().astimezone(UTC)
        phase = "absence" if (absence_verified or state.get("absence_started_at") is not None) else "routine"
        bound_key = ("fresh_absence_deadline_monotonic" if phase == "absence"
            and state.get("fresh_absence_deadline_monotonic") is not None else
            "absence_deadline_monotonic" if phase == "absence" else "routine_deadline_monotonic")
        persisted_bound = state.get(bound_key)
        previous_mono = float(state.get("last_monotonic", persisted_mono))
        previous_wall = datetime.fromisoformat(state.get("last_utc", persisted_wall.isoformat()))
        persisted_utc_bound = (state.get("fresh_absence_deadline_utc") or state.get("absence_deadline_utc")
            if phase == "absence" else state.get("routine_deadline_utc"))
        late = ((persisted_bound is not None and persisted_mono >= float(persisted_bound))
            or persisted_utc_bound is None or persisted_wall >= datetime.fromisoformat(persisted_utc_bound))
        backwards = persisted_mono < previous_mono or persisted_wall < previous_wall
        if (late and state.get("timing_status") == "within_deadline") or backwards:
            def mark_persistence_result(current):
                if backwards:
                    current["timing_status"] = "unknown"
                    current["timing_failure"] = current.get("timing_failure") or "cleanup clock moved backwards during durable timing write"
                elif current.get("timing_status") == "within_deadline":
                    current["timing_status"] = "late"
                    current["timing_failure"] = f"profile {phase} cleanup deadline elapsed during durable timing write"
                current["last_monotonic"] = max(float(current.get("last_monotonic", persisted_mono)), persisted_mono)
                current["last_utc"] = max(datetime.fromisoformat(current.get("last_utc", persisted_wall.isoformat())),
                    persisted_wall).isoformat()
                return current
            state = _profile_timing_mutate(options, mark_persistence_result,
                expected_identity=_EXPLICIT_TIMING_IDENTITY.get())
        return state
    except (OSError, ValueError, PermissionError, json.JSONDecodeError) as exc:
        _TIMING_OBSERVATION_FAILURE.set(f"profile timing finalization failed ({type(exc).__name__})")
        return None


def _mark_profile_absence_started(options: AzureRuntimeOptions, *, clock: Callable[[], float],
                                  utc_now: Callable[[], datetime]) -> dict[str, Any] | None:
    if options.timing_profile is None and options.workflow_profile is None:
        return None
    mono = float(clock()); wall = utc_now().astimezone(UTC)
    try:
        def mark(state):
            if state.get("absence_started_at") is None:
                state["absence_started_at"]=wall.isoformat()
                state["absence_started_monotonic"]=mono
                state["fresh_absence_deadline_monotonic"]=min(
                    mono+60.0,float(state.get("absence_deadline_monotonic",mono+60.0)))
                absence_end=datetime.fromisoformat(state.get("absence_deadline_utc",wall.isoformat()))
                state["fresh_absence_deadline_utc"]=min(wall+timedelta(seconds=60),absence_end).isoformat()
            return state
        return _profile_timing_mutate(options, mark,expected_identity=_EXPLICIT_TIMING_IDENTITY.get())
    except (OSError, ValueError, PermissionError, json.JSONDecodeError) as exc:
        _TIMING_OBSERVATION_FAILURE.set(f"profile absence timing anchor could not be saved ({type(exc).__name__})")
        return None


def _timing_failure(status: str | None, message: str | None, occurred_at: datetime) -> FailureEvidence | None:
    if status in (None, "within_deadline") and not message:
        return None
    return FailureEvidence(category="azure_cleanup_timing", message=(message or f"cleanup timing status is {status}")[:1000],
        occurred_at=occurred_at, details={"timing_status": status or "unknown", "acceptance_failed": True})


def _persist_tfvars(root: Path, journal: Path, name: str, values: dict[str, Any]) -> Path:
    """Persist the exact root inputs once, privately, so destroy can reproduce state identity."""
    path = root / f"{name}.auto.tfvars.json"
    if path.is_symlink():
        raise FileExistsError(f"{name} Terraform input file is a symlink")
    if path.exists():
        current = json.loads(path.read_text(encoding="utf-8"))
        if current != values:
            raise FileExistsError(f"{name} Terraform inputs differ from the owned attempt")
    else:
        _atomic_private(path, values)
    os.chmod(path, 0o600)
    state = json.loads(journal.read_text(encoding="utf-8"))
    state.setdefault("terraform_inputs", {})[name] = path.name
    state.setdefault("terraform_input_sha256", {})[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    _atomic_private(journal, state)
    return path


def _recovery_tfvars(root: Path, journal: Path, name: str, expected: dict[str,Any]) -> Path:
    """Return only the original private Terraform inputs recorded for this attempt."""
    state=json.loads(journal.read_text(encoding="utf-8"))
    if state.get("terraform_inputs",{}).get(name) != f"{name}.auto.tfvars.json":
        raise RuntimeError(f"recovery journal lacks the original {name} Terraform inputs")
    path=root/f"{name}.auto.tfvars.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise RuntimeError(f"private {name} Terraform input is missing or has unsafe permissions")
    try: payload=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as exc: raise RuntimeError(f"private {name} Terraform input is malformed") from exc
    digest = state.get("terraform_input_sha256", {}).get(name)
    if digest is not None and digest != hashlib.sha256(path.read_bytes()).hexdigest():
        raise RuntimeError(f"private {name} Terraform input no longer matches its original hash")
    if digest is None and name == "pair" and "admin_username" not in payload:
        expected = {key:value for key,value in expected.items() if key != "admin_username"}
    if (digest is None and payload != expected) or any(payload.get(key) != value for key,value in expected.items()):
        raise RuntimeError(f"private {name} Terraform input no longer matches this deployment")
    return path


def _redact(options: AzureRuntimeOptions, value: object) -> str:
    text = str(value)
    for secret in (options.authentication_reference, options.ssh_public_key, str(options.attempt_root)):
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", "[REDACTED]", text, flags=re.DOTALL)


def _safe_result_view(options: AzureRuntimeOptions, result: CommandResult) -> dict[str, Any]:
    view=result.durable_view()
    view["stdout"]=_redact(options,view["stdout"])
    view["stderr"]=_redact(options,view["stderr"])
    for field in ("stdout","stderr"):
        if len(view[field])>65536:
            view[field]=view[field][:65536]+"[TRUNCATED]"
    return view


def _safe_cleanup_action(options: AzureRuntimeOptions, result: CommandResult) -> dict[str, Any]:
    return _safe_result_view(options, result)


def _journal_actions(journal: Path) -> list[dict[str, Any]]:
    if not journal.is_file() or journal.is_symlink():
        return []
    return json.loads(journal.read_text(encoding="utf-8")).get("actions",[])


def _write_output_artifact(root: Path, output: ProviderDeploymentOutput) -> None:
    path=root/"terraform-outputs.redacted.json"
    if path.exists() or path.is_symlink():
        raise FileExistsError("redacted output artifact already exists; refusing overwrite")
    vms={}
    for role,vm in (("a",output.vm_a),("b",output.vm_b)):
        vms[role]={key:getattr(vm,key) for key in ("resource_id","private_ipv4","image_identity","actual_shape","region","zone")}
        vms[role]["public_ipv4"]=vm.public_ipv4.model_dump(mode="json")
        vms[role]["placement_metadata"]=vm.placement_metadata.model_dump(mode="json")
        vms[role]["physical_zone_id"]=vm.physical_zone_id.model_dump(mode="json")
    expiry=output.expiry.value
    payload={"provider":"azure","run_id":output.run_id,"deployment_id":output.deployment_id,"scenario":str(output.scenario),
        "vms":vms,"documented_network_limit":output.documented_network_limit.model_dump(mode="json"),
        "expiry":{"action":expiry.action,"state":expiry.state,"registration_id":expiry.registration_id.model_dump(mode="json"),"expires_at":expiry.expires_at.model_dump(mode="json")} if expiry else None}
    _atomic_private(path,payload)


def _workspace(o: AzureRuntimeOptions, d: ProviderDeploymentInput) -> tuple[Path, Path]:
    root = o.attempt_root
    if root.is_symlink() or root.exists() and not root.is_dir():
        raise FileExistsError("attempt workspace exists as a symlink or non-directory")
    if root.exists():
        old = root / "recovery.json"
        if not old.is_file() or old.is_symlink():
            if any(root.iterdir()):
                raise FileExistsError("nonempty workspace has no owned recovery journal")
        else:
            state = json.loads(old.read_text(encoding="utf-8"))
            if state.get("run_id") != d.run_id or state.get("deployment_id") != d.deployment_id or state.get("config_sha256") != d.config_sha256:
                raise FileExistsError("attempt workspace belongs to another deployment")
            return root, old
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    journal = root / "recovery.json"
    state = {"run_id": d.run_id, "deployment_id": d.deployment_id, "config_sha256": d.config_sha256,
        "provider": "azure", "resource_group": _slug(d), "namespace": _slug(d), "creation_may_have_started": False,
        "reservation_id": d.reservation.value.reservation_id.value,
        "cap_started_at": d.reservation.value.cap_started_at.value.isoformat(),
        "original_deadline_monotonic": o.cap_deadline_monotonic,
        "vm_ids": [f"/subscriptions/{o.subscription_id}/resourceGroups/{_slug(d)}/providers/Microsoft.Compute/virtualMachines/{_slug(d)}-{r}" for r in ("a", "b")],
        "actions": [], "failures": [], "resources": []}
    _atomic_private(journal, state)
    return root, journal


def build_pair_inputs(deployment: ProviderDeploymentInput, options: AzureRuntimeOptions) -> dict[str, Any]:
    """Pure, strict scenario mapping for the Azure pair root."""
    reservation = deployment.reservation.value
    if reservation is None or reservation.cap_started_at.value is None:
        raise ValueError("matching active reservation evidence is required")
    now = reservation.cap_started_at.value
    _validate_request(deployment, options, now, None, check_live_clock=False)
    scenario = Scenario(deployment.scenario)
    return {"resource_group_name": _slug(deployment), "location": deployment.vm_a.region,
        "subscription_id": options.subscription_id, "deployment_id": deployment.deployment_id,
        "config_sha256": deployment.config_sha256, "controller_cidr": options.controller_cidr,
        "ssh_public_key": options.ssh_public_key, "selected_image": options.selected_image,
        "admin_username": deployment.vm_a.connection_user,
        "placement_group_name": deployment.placement.name or "", "scenario": scenario.value,
        "region_a": deployment.vm_a.region, "region_b": deployment.vm_b.region,
        "zone_a": "1", "zone_b": "2" if scenario == Scenario.CROSS_ZONE else "1",
        "private_a": "10.70.0.4", "private_b": "10.71.0.4" if scenario == Scenario.INTER_REGION else "10.70.0.5",
        "create_peering": scenario == Scenario.INTER_REGION, "create_ppg": scenario == Scenario.PLACEMENT_OPTIMIZATION,
        "active_priority": "Regular", "network_limit_mbps": 12500}


def prepare_recovery(deployment: ProviderDeploymentInput, options: AzureRuntimeOptions, *,
                     clock: Callable[[], float] = time.monotonic,
                     utc_now: Callable[[], datetime] = lambda: datetime.now(UTC),
                     timing_origin: tuple[float, datetime] | None = None,
                     initialize_timing: bool = True) -> tuple[Path, Path, Path, Path]:
    """Save the original local cleanup identity/inputs before live preflight.

    This records prospective targets only. It makes no provider call or absence
    claim, and does not require an unexpired approval, quote or launch cap.
    """
    if initialize_timing:
        origin_kwargs = ({"origin_monotonic": timing_origin[0], "origin_utc": timing_origin[1]}
            if timing_origin is not None else {})
        _initialize_profile_timing(deployment, options, clock=clock, utc_now=utc_now,
            **origin_kwargs)
    pair_values = build_pair_inputs(deployment, options)
    root, journal = _workspace(options, deployment)
    pair_vars = _persist_tfvars(root, journal, "pair", pair_values)
    expiry_vars = _persist_tfvars(root, journal, "expiry", {
        "subscription_id": options.subscription_id, "tenant_id": options.tenant_id,
        "resource_group_name": _slug(deployment), "deployment_id": deployment.deployment_id,
        "location": deployment.vm_a.region})
    return root, journal, pair_vars, expiry_vars


def _is_exact_not_found(result: CommandResult) -> bool:
    texts = [text for text in (result.stdout, result.stderr) if text.strip()]
    if len(texts) != 1:
        return False
    text = texts[0]
    try:
        payload = json.loads(text)
        code = payload.get("error", {}).get("code") if isinstance(payload, dict) else None
        return code in {"ResourceNotFound", "NotFound"}
    except (json.JSONDecodeError, AttributeError):
        return re.match(r"\s*(?:ERROR:\s*)?\(ResourceNotFound\)(?:\s|$)", text) is not None


def _is_exact_missing_attempt_resource_group(result: CommandResult, resource_group: str) -> bool:
    """Accept only Azure CLI's exact structured or canonical CLI absence for this parent."""
    if result.outcome != "failed" or result.stdout.strip() or not result.stderr.strip():
        return False
    expected_message = f"Resource group '{resource_group}' could not be found."
    wrapped = re.fullmatch(r"\s*ERROR:\s*Not Found\((\{.*\})\)\s*", result.stderr, re.DOTALL)
    if wrapped is None:
        cli_message=(r"\s*ERROR:\s*\(ResourceGroupNotFound\)\s+"+re.escape(expected_message)
            +r"\r?\nCode:\s*ResourceGroupNotFound\r?\nMessage:\s*"
            +re.escape(expected_message)+r"\s*")
        return re.fullmatch(cli_message,result.stderr,re.DOTALL) is not None
    def unique_object(pairs):
        output = {}
        for key, value in pairs:
            if key in output:
                raise ValueError("duplicate JSON key")
            output[key] = value
        return output
    try:
        payload = json.loads(wrapped.group(1), object_pairs_hook=unique_object)
    except (json.JSONDecodeError, ValueError):
        return False
    if not isinstance(payload, dict) or set(payload) != {"error"}:
        return False
    error = payload["error"]
    return (isinstance(error, dict) and set(error) == {"code", "message"}
        and error.get("code") == "ResourceGroupNotFound" and error.get("message") == expected_message)


def _automation_account_parts(account_id: str) -> dict[str,str] | None:
    match=re.fullmatch(r"/subscriptions/(?P<subscription>[^/]+)/resourceGroups/(?P<group>[^/]+)"
        r"/providers/Microsoft\.Automation/automationAccounts/(?P<account>[^/]+)",account_id)
    if match is None:
        return None
    parts=match.groupdict()
    return parts if parts["account"]==parts["group"]+"-auto" else None


def _is_exact_missing_automation_account(result: CommandResult, expected_account_id: str) -> bool:
    """Match only the canonical nested ResourceNotFound for the exact owned account."""
    parts=_automation_account_parts(expected_account_id)
    if parts is None or result.outcome!="failed" or result.stdout.strip() or not result.stderr.strip():
        return False
    wrapped=re.fullmatch(r"\s*ERROR:\s*Not Found\((\{.*\})\)\s*",result.stderr,re.DOTALL)
    if wrapped is None:
        return False
    def unique_object(pairs):
        output={}
        for key,value in pairs:
            if key in output:
                raise ValueError("duplicate JSON key")
            output[key]=value
        return output
    try:
        payload=json.loads(wrapped.group(1),object_pairs_hook=unique_object)
    except (json.JSONDecodeError,ValueError):
        return False
    if not isinstance(payload,dict) or set(payload)!={"error"}:
        return False
    expected_message=(f"The Resource 'Microsoft.Automation/automationAccounts/{parts['account']}' "
        f"under resource group '{parts['group']}' was not found. "
        "For more details please go to https://aka.ms/ARMResourceNotFoundFix")
    error=payload["error"]
    return (isinstance(error,dict) and set(error)=={"code","message"}
        and error.get("code")=="ResourceNotFound" and error.get("message")==expected_message)


def _is_exact_missing_automation_child(result: CommandResult, expected_url: str) -> bool:
    """Accept typed CLI absence wrappers only with the exact owned child URL."""
    if result.outcome != "failed" or result.stdout.strip() or not result.stderr.strip():
        return False
    wrapped = re.fullmatch(r"\s*ERROR:\s*Not Found\((\{.*\})\)\s*", result.stderr, re.DOTALL)
    if wrapped is None:
        return False
    def unique_object(pairs):
        output = {}
        for key, value in pairs:
            if key in output:
                raise ValueError("duplicate JSON key")
            output[key] = value
        return output
    try:
        payload = json.loads(wrapped.group(1), object_pairs_hook=unique_object)
    except (json.JSONDecodeError, ValueError):
        return False
    if (isinstance(payload, dict) and set(payload) == {"code", "message"}
            and payload.get("code") == "NotFound" and isinstance(payload.get("message"), str)):
        return True
    nested_url = re.fullmatch(
        r"/subscriptions/[^/?]+/resourceGroups/(?P<group>[^/?]+)/providers/Microsoft\.Automation/"
        r"automationAccounts/(?P<account>[^/?]+)/(?P<collection>jobSchedules|schedules|runbooks)/"
        r"(?P<child>[^/?]+)\?api-version="+re.escape(AUTOMATION_API), expected_url)
    if nested_url is None or not isinstance(payload, dict) or set(payload) != {"error"}:
        return False
    parts=nested_url.groupdict()
    missing_resource_message=(f"The Resource 'Microsoft.Automation/automationAccounts/{parts['account']}/"
        f"{parts['collection']}/{parts['child']}' under resource group '{parts['group']}' was not found. "
        "For more details please go to https://aka.ms/ARMResourceNotFoundFix")
    missing_account_message=(f"The Resource 'Microsoft.Automation/automationAccounts/{parts['account']}' "
        f"under resource group '{parts['group']}' was not found. "
        "For more details please go to https://aka.ms/ARMResourceNotFoundFix")
    error=payload["error"]
    if not isinstance(error,dict) or set(error)!={"code","message"}:
        return False
    return ((error.get("code")=="ResourceNotFound"
            and error.get("message") in {missing_resource_message,missing_account_message})
        or (error.get("code")=="ResourceGroupNotFound"
            and error.get("message")==f"Resource group '{parts['group']}' could not be found."))


def _azure_tsv_content_matches(stdout: str, expected_sha256: str) -> bool:
    observed = stdout.encode()
    if hashlib.sha256(observed).hexdigest() == expected_sha256:
        return True
    # `az rest --output tsv` appends one LF record terminator. Remove only that
    # single framing byte; any additional bytes remain part of the content.
    return (observed.endswith(b"\n")
        and hashlib.sha256(observed[:-1]).hexdigest() == expected_sha256)


def _azure_schedule_trigger(requested: datetime) -> datetime:
    """Floor Azure Automation OneTime schedules to their exact UTC minute."""
    if requested.tzinfo is None or requested.utcoffset() is None:
        raise ValueError("Azure expiry request must include a timezone")
    return requested.astimezone(UTC).replace(second=0, microsecond=0)


def _assert_subscription(runner: CommandRunner, options: AzureRuntimeOptions, root: Path,
                         clock: Callable[[], float]) -> CommandResult:
    result = _command(runner, options, root, ["az", "account", "show", "--subscription", options.subscription_id,
        "--query", "{id:id,tenantId:tenantId,state:state}", "--output", "json"], "subscription_preflight", clock)
    value = json.loads(result.stdout)
    if (result.stderr.strip() or not isinstance(value, dict)
            or str(value.get("id", "")).lower() != options.subscription_id.lower()
            or str(value.get("tenantId", "")).lower() != options.tenant_id.lower()
            or value.get("state") != "Enabled"):
        raise RuntimeError("expected Enabled Azure subscription/tenant identity is unproved")
    return result


def _validate_provider_registrations(runner: CommandRunner, options: AzureRuntimeOptions, root: Path,
                                    clock: Callable[[], float]) -> None:
    _assert_subscription(runner, options, root, clock)
    for namespace in ("Microsoft.Compute", "Microsoft.Network", "Microsoft.Automation", "Microsoft.Authorization"):
        result = _command(runner, options, root, ["az", "provider", "show", "--namespace", namespace,
            "--subscription", options.subscription_id, "--query", "{id:id,namespace:namespace,registrationState:registrationState}",
            "--output", "json"], "provider_registration_preflight", clock)
        value = json.loads(result.stdout)
        if (result.stderr.strip() or not isinstance(value, dict)
                or value.get("namespace") != namespace or value.get("registrationState") != "Registered"
                or str(value.get("id", "")).lower() != f"/subscriptions/{options.subscription_id}/providers/{namespace}".lower()):
            raise RuntimeError("required preexisting Azure provider registration is unproved: " + namespace)


def _command(runner: CommandRunner, options: AzureRuntimeOptions, root: Path, argv: list[str], action: str,
             clock: Callable[[], float], *, allow_not_found: bool = False,
             allow_missing_resource_group: str | None = None,
             expected_missing_automation_child_url: str | None = None,
             expected_missing_automation_account_id: str | None = None,
             apply_timeout_seconds: float | None = None,
             before_apply: Callable[[], float] | None = None) -> CommandResult:
    if options.cleanup_timing_active:
        _observe_profile_cleanup_clock(options, clock=clock, utc_now=_utc_from_clock(clock),
            phase=options.cleanup_timing_phase or "routine")
    operation_deadline = min(options.cap_deadline_monotonic,
        options.workflow_stage_deadline_monotonic
        if options.workflow_stage_deadline_monotonic is not None else options.cap_deadline_monotonic)
    remaining = operation_deadline-clock()
    if options.cleanup_timing_active and options.cleanup_timing_phase == "routine":
        try:
            timing = json.loads(options.cleanup_timing_path.read_text(encoding="utf-8"))
            deadline = timing.get("routine_deadline_monotonic")
            if isinstance(deadline, (int, float)):
                remaining = min(remaining, float(deadline) - clock())
            utc_deadline = timing.get("routine_deadline_utc")
            if isinstance(utc_deadline, str):
                remaining = min(remaining, (datetime.fromisoformat(utc_deadline)
                    - _utc_from_clock(clock)().astimezone(UTC)).total_seconds())
            else:
                remaining = min(remaining, 0.0)
        except (OSError, ValueError, TypeError):
            remaining = min(remaining, 0.0)
    if options.cleanup_timing_active and options.cleanup_timing_phase == "absence":
        try:
            timing = json.loads(options.cleanup_timing_path.read_text(encoding="utf-8"))
            deadline = timing.get("fresh_absence_deadline_monotonic")
            if isinstance(deadline, (int, float)) and deadline > clock():
                remaining = min(remaining, float(deadline) - clock())
            else:
                remaining = min(remaining, 0.0)
            utc_deadline = timing.get("fresh_absence_deadline_utc")
            if isinstance(utc_deadline, str):
                remaining = min(remaining, (datetime.fromisoformat(utc_deadline)
                    - _utc_from_clock(clock)().astimezone(UTC)).total_seconds())
            else:
                remaining = min(remaining, 0.0)
        except (OSError, ValueError, TypeError):
            remaining = min(remaining, 0.0)
    if remaining <= 0: raise TimeoutError("original reservation deadline elapsed")
    if argv[:1] == ["az"] and "--subscription" not in argv:
        index = next((i for i,value in enumerate(argv) if value.startswith("--")), len(argv))
        argv = [*argv[:index], "--subscription", options.subscription_id, *argv[index:]]
    environment = dict(os.environ)
    tf_root = next((arg.split("=", 1)[1] for arg in argv if arg.startswith("-chdir=")), "terraform")
    environment["TF_DATA_DIR"] = str(root / ("terraform-data-" + Path(tf_root).name))
    reviewed_apply = ("apply" in argv and any(arg.startswith("-chdir=") for arg in argv)
        and options.plan_review_timeout_seconds > 0)
    bounded_terraform_destroy = ("terraform" in argv[:1] and "destroy" in argv
        and action.endswith("_destroy") and options.cleanup_timing_active
        and options.cleanup_timing_phase in {"routine", "emergency"})
    req = CommandRequest(action_id=f"azure-{uuid.uuid4()}", action_type=action, argv=argv,
        cwd=str(Path(__file__).resolve().parents[2]), timeout_seconds=(remaining if reviewed_apply or bounded_terraform_destroy else min(
            options.command_timeout_seconds, 180.0 if options.timing_profile else options.command_timeout_seconds, remaining)),
        environment=environment, environment_classification="contains_secret_references", redactions=[options.authentication_reference, options.ssh_public_key, str(options.attempt_root)])
    def execute(req: CommandRequest) -> CommandResult:
        try:
            result = runner.run(req)
        except Exception as exc:
            journal = root / "recovery.json"
            if journal.is_file() and not journal.is_symlink():
                state=json.loads(journal.read_text(encoding="utf-8"))
                state.setdefault("actions", []).append({"action_id":req.action_id,"action_type":action,"request":req.durable_view(),"outcome":"unknown","error":_redact(options,exc)[:500]})
                _atomic_private(journal,state)
            raise RuntimeError(_redact(options, exc)) from exc
        result = result.model_copy(update={"redactions":list(set(result.redactions + req.redactions))})
        if options.cleanup_timing_active:
            _observe_profile_cleanup_clock(options, clock=clock, utc_now=_utc_from_clock(clock),
                phase=options.cleanup_timing_phase or "routine")
        journal = root / "recovery.json"
        if journal.is_file() and not journal.is_symlink():
            state=json.loads(journal.read_text(encoding="utf-8"))
            state.setdefault("actions", []).append({"request":req.durable_view(),"result":_safe_result_view(options,result)})
            _atomic_private(journal,state)
        return result
    if "apply" in argv and any(arg.startswith("-chdir=") for arg in argv):
        from .terraform_plan import reviewed_apply
        req = req.model_copy(update={"cwd": str(root)})
        result = reviewed_apply(req, execute, review_seconds=options.plan_review_timeout_seconds,
            command_timeout_seconds=int(options.command_timeout_seconds),
            apply_timeout_seconds=apply_timeout_seconds, before_apply=before_apply,
            review_admission_path=options.plan_review_admission_path)
    else:
        result = execute(req)
    method_values=[argv[i+1] for i,value in enumerate(argv[:-1]) if value=="--method"]
    url_values=[argv[i+1] for i,value in enumerate(argv[:-1]) if value=="--url"]
    subscription_values=[argv[i+1] for i,value in enumerate(argv[:-1]) if value=="--subscription"]
    output_values=[argv[i+1] for i,value in enumerate(argv[:-1]) if value=="--output"]
    expected_job_url_pattern=(r"/subscriptions/(?P<subscription>[^/?]+)/resourceGroups/(?P<group>[^/?]+)"
        +r"/providers/Microsoft\.Automation/automationAccounts/(?P<account>[^/?]+)"
        +r"/(?P<collection>jobSchedules|schedules|runbooks)/(?P<child>[^/?]+)\?api-version="
        +re.escape(AUTOMATION_API))
    child_url_match=(re.fullmatch(expected_job_url_pattern,expected_missing_automation_child_url)
        if expected_missing_automation_child_url is not None else None)
    child_identity_valid=False
    if child_url_match is not None:
        groups=child_url_match.groupdict()
        child_identity_valid=(groups["subscription"]==options.subscription_id
            and groups["account"]==groups["group"]+"-auto"
            and ((groups["collection"]=="jobSchedules" and _is_uuid_segment(groups["child"]))
                or (groups["collection"]=="schedules" and groups["child"]==groups["group"]+"-once")
                or (groups["collection"]=="runbooks" and groups["child"]==groups["group"]+"-deallocate")))
    exact_automation_child_absence = (expected_missing_automation_child_url is not None
        and action in {"expiry_jobSchedules_before_delete", "expiry_jobSchedules_absence",
            "expiry_schedules_before_delete", "expiry_schedules_absence",
            "expiry_runbooks_before_delete", "expiry_runbooks_absence", "profile_absence_automation"}
        and argv[:2]==["az","rest"] and method_values == ["GET"]
        and url_values == [expected_missing_automation_child_url] and output_values == ["json"]
        and not any(flag in argv for flag in ("--body","--headers","--query","--resource"))
        and subscription_values == [options.subscription_id]
        and child_identity_valid
        and _is_exact_missing_automation_child(result,expected_missing_automation_child_url))
    account_parts=(_automation_account_parts(expected_missing_automation_account_id)
        if expected_missing_automation_account_id is not None else None)
    account_identity_valid=(account_parts is not None and account_parts["subscription"]==options.subscription_id)
    account_url=(expected_missing_automation_account_id+"?api-version="+AUTOMATION_API
        if expected_missing_automation_account_id is not None else None)
    account_profile_request=(action=="profile_absence_automation" and argv[:2]==["az","rest"]
        and method_values==["GET"] and url_values==[account_url] and output_values==["json"]
        and subscription_values==[options.subscription_id]
        and not any(flag in argv for flag in ("--body","--headers","--query","--resource")))
    account_cli_request=(action=="automation_account_absence" and argv[:3]==["az","resource","show"]
        and [argv[i+1] for i,value in enumerate(argv[:-1]) if value=="--ids"]==[expected_missing_automation_account_id]
        and output_values==["json"] and subscription_values==[options.subscription_id]
        and not any(flag in argv for flag in ("--method","--url","--body","--headers","--query")))
    exact_automation_account_absence=(account_identity_valid
        and (account_profile_request or account_cli_request)
        and (_is_exact_missing_automation_account(result,expected_missing_automation_account_id)
            or (account_cli_request and account_parts is not None
                and _is_exact_missing_attempt_resource_group(result,account_parts["group"]))))
    accepted_absence = (allow_not_found and _is_exact_not_found(result)) or exact_automation_child_absence or exact_automation_account_absence or (
        allow_missing_resource_group is not None
        and _is_exact_missing_attempt_resource_group(result, allow_missing_resource_group))
    if result.outcome != "succeeded" and not accepted_absence:
        raise RuntimeError(_redact(options,f"{action} failed with {result.outcome}: {result.stderr[:1000]}"))
    if len(result.stdout.encode("utf-8"))>1_000_000 or len(result.stderr.encode("utf-8"))>1_000_000:
        raise RuntimeError(f"{action} produced output above the 1 MiB bounded-response limit")
    return result


def _role_assignments_at_scope(runner: CommandRunner, options: AzureRuntimeOptions,
                               root: Path, scope: str, action: str,
                               clock: Callable[[], float]) -> tuple[Any, list[dict[str, Any]]]:
    """Read direct role assignments at one exact scope, rejecting ambiguous output."""
    result = _command(runner, options, root,
        ["az", "role", "assignment", "list", "--scope", scope, "--output", "json"],
        action, clock)
    if result.stderr.strip() or not result.stdout.strip():
        raise RuntimeError("role assignment inventory is blank or incomplete")
    try:
        assignments = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("role assignment inventory is malformed") from exc
    if not isinstance(assignments, list):
        raise RuntimeError("role assignment inventory is malformed")
    seen: set[str] = set()
    for item in assignments:
        if not isinstance(item, dict) or any(not isinstance(item.get(key), str) or not item[key].strip()
                for key in ("id", "scope", "principalId", "roleDefinitionId")):
            raise RuntimeError("role assignment inventory contains an unknown or malformed entry")
        identity = item["id"].lower()
        if identity in seen:
            raise RuntimeError("role assignment inventory contains a duplicate identity")
        seen.add(identity)
        if item["scope"].rstrip("/").lower() != scope.rstrip("/").lower():
            raise RuntimeError("role assignment inventory contains an inherited or foreign scope")
    return result, assignments


def _prove_vm_absent(runner: CommandRunner, options: AzureRuntimeOptions, root: Path,
                     resource_id: str, clock: Callable[[], float], *, absent_parent_group: str | None = None) -> CommandResult:
    """Accept exact VM absence, or typed parent absence already independently proved."""
    match = re.fullmatch(r"/subscriptions/([^/]+)/resourceGroups/([^/]+)/providers/Microsoft.Compute/virtualMachines/([^/]+)", resource_id, re.I)
    if (not match or match.group(1).lower() != options.subscription_id.lower()
            or (absent_parent_group is not None and match.group(2) != absent_parent_group)):
        raise ValueError("VM absence query is outside the original subscription/group")
    remaining = options.cap_deadline_monotonic-clock()
    if remaining <= 0:
        raise TimeoutError("original reservation deadline elapsed")
    request = CommandRequest(action_id=f"azure-{uuid.uuid4()}", action_type="vm_absence_by_id",
        argv=["az", "resource", "show", "--subscription", options.subscription_id, "--ids", resource_id, "--output", "json"],
        cwd=str(Path(__file__).resolve().parents[2]), timeout_seconds=min(options.command_timeout_seconds, remaining),
        environment=dict(os.environ), environment_classification="contains_secret_references", redactions=[options.authentication_reference, options.ssh_public_key, str(options.attempt_root)])
    try:
        result = runner.run(request)
    except Exception as exc:
        result = None
        detail = _redact(options,exc)[:500]
    else:
        result = result.model_copy(update={"redactions":list(set(result.redactions + request.redactions))})
        detail = result.stderr
    journal = root / "recovery.json"
    if journal.is_file() and not journal.is_symlink():
        state=json.loads(journal.read_text(encoding="utf-8"))
        state.setdefault("actions", []).append({"request":request.durable_view(),"result":_safe_result_view(options,result) if result else {"outcome":"unknown","error":detail}})
        _atomic_private(journal,state)
    if result is None:
        raise RuntimeError("direct VM absence query outcome is unknown: " + detail)
    result = result.model_copy(update={"redactions":list(set(result.redactions + request.redactions))})
    if result.outcome == "succeeded":
        raise RuntimeError("VM remains present by direct resource ID: " + resource_id)
    if result.outcome == "failed" and _is_exact_not_found(result):
        return result
    if result.outcome == "failed" and absent_parent_group is not None:
        # Only the primary typed CLI/ARM error, bound to this exact parent.
        # An authorization error merely mentioning ResourceGroupNotFound is not proof.
        group_message = f"Resource group '{absent_parent_group}' could not be found."
        primary = re.fullmatch(r"\s*(?:ERROR:\s*)?\(ResourceGroupNotFound\)\s+" + re.escape(group_message)
            + r"(?:\s*Code: ResourceGroupNotFound\s*Message: " + re.escape(group_message) + r")?\s*", result.stderr)
        if primary and not result.stdout.strip():
            return result
        for text, other in ((result.stdout, result.stderr), (result.stderr, result.stdout)):
            if other.strip():
                continue
            try:
                payload = json.loads(text)
                error = payload.get("error", {}) if isinstance(payload, dict) else {}
                if error.get("code") == "ResourceGroupNotFound" and error.get("message") == group_message:
                    return result
            except (ValueError, AttributeError):
                pass
    raise RuntimeError(f"direct VM absence query is not proof: {result.outcome}: {result.stderr[:500]}")


def _read_arm_collection(runner: CommandRunner, options: AzureRuntimeOptions, root: Path,
                         endpoint: str, action: str, clock: Callable[[],float],
                         required_fields: tuple[str,...]) -> tuple[list[dict[str,Any]],list[dict[str,Any]]]:
    """Read all ARM continuation pages; malformed, repeated, or foreign links fail closed."""
    next_url="https://management.azure.com"+endpoint
    scope_prefix=next_url.split("?",1)[0]
    seen_urls=set(); seen_ids=set(); values=[]; evidence=[]
    for page in range(1000):
        if next_url in seen_urls or not next_url.startswith("https://management.azure.com/"):
            raise RuntimeError(f"{action} returned a repeated or non-ARM continuation URL")
        seen_urls.add(next_url)
        result=_command(runner,options,root,["az","rest","--method","GET","--url",next_url,"--output","json"],
            action if page==0 else f"{action}_page_{page+1}",clock)
        evidence.append(_safe_cleanup_action(options,result))
        if result.stderr.strip(): raise RuntimeError(f"{action} returned an inventory diagnostic; completeness is unproved")
        if not result.stdout.strip(): raise RuntimeError(f"{action} returned blank collection output")
        try: payload=json.loads(result.stdout)
        except json.JSONDecodeError as exc: raise RuntimeError(f"{action} returned malformed JSON") from exc
        if not isinstance(payload,dict) or not isinstance(payload.get("value"),list):
            raise RuntimeError(f"{action} response lacks a complete ARM value collection")
        for item in payload["value"]:
            if not isinstance(item,dict) or any(not isinstance(item.get(field),str) or not item[field].strip() for field in required_fields):
                raise RuntimeError(f"{action} contains an incomplete resource observation")
            identity=item[required_fields[0]].lower()
            if not identity.startswith(f"/subscriptions/{options.subscription_id}/".lower()):
                raise RuntimeError(f"{action} returned a foreign subscription resource identity")
            if identity in seen_ids: raise RuntimeError(f"{action} contains a duplicate resource identity")
            seen_ids.add(identity); values.append(item)
        link=payload.get("nextLink")
        if link in (None,""): return values,evidence
        if not isinstance(link,str) or not (link == scope_prefix or link.startswith(scope_prefix + "?")):
            raise RuntimeError(f"{action} returned an invalid continuation URL")
        next_url=link
    raise RuntimeError(f"{action} exceeded the bounded 1000-page inventory limit")


def _watcher_baseline(groups: list[dict[str,Any]], resources: list[dict[str,Any]]) -> dict[str,Any]:
    group_ids=sorted(item["id"].lower() for item in groups
        if item["name"].lower().startswith(("networkwatcher", "newnetworkwatcher")))
    watcher_ids=sorted(item["id"].lower() for item in resources
        if item["type"].lower()=="microsoft.network/networkwatchers")
    snapshot={"groups":group_ids,"watchers":watcher_ids}
    snapshot["sha256"]=hashlib.sha256(json.dumps(snapshot,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    return snapshot


def _capture_profile_watcher_baseline(deployment: ProviderDeploymentInput, options: AzureRuntimeOptions,
                                      runner: CommandRunner, root: Path, journal: Path,
                                      clock: Callable[[],float],
                                      utc_now: Callable[[],datetime]) -> None:
    if _profile_name(deployment) is None:
        return
    state=json.loads(journal.read_text(encoding="utf-8"))
    prior=state.get("profile_external_baseline")
    if prior is not None:
        if not _valid_watcher_baseline(prior, deployment, options, state.get("actions")):
            raise RuntimeError("saved pre-creation Network Watcher baseline is invalid or belongs to another attempt")
        if options.azure_auxiliary_cleanup is not None:
            from .azure_auxiliary_cleanup import AuxiliaryCleanupCohort
            owner = next((row for row in options.azure_auxiliary_cleanup["owners"]
                if row["run_id"] == deployment.run_id and row["deployment_id"] == deployment.deployment_id
                and row["config_sha256"] == deployment.config_sha256), None)
            if owner is None:
                raise PermissionError("Azure pre-creation cohort baseline does not bind this exact parent")
            AuxiliaryCleanupCohort(options.azure_auxiliary_cleanup).record_precreation_baseline(owner, prior)
        return
    if (state.get("creation_may_have_started")
            or state.get("pair_state") in {"creating","created"}
            or state.get("expiry_state") in {"preparing","registered"}
            or any(action.get("request",{}).get("action_type") in {
                "expiry_prerequisites","pair_apply","pair_destroy","expiry_destroy"}
                for action in state.get("actions",[]) if isinstance(action,dict))):
        raise RuntimeError("pre-creation Network Watcher baseline is missing after provider mutation may have started")
    if any(action.get("request",{}).get("action_type") in {
            "profile_watcher_baseline_resources", "profile_watcher_baseline_groups"}
            for action in state.get("actions", []) if isinstance(action,dict)):
        raise RuntimeError("pre-creation Network Watcher baseline capture was interrupted; refusing a late replacement")
    def capture_utc_sample() -> datetime:
        value=utc_now()
        if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise RuntimeError("captured pre-creation Network Watcher baseline is invalid; provider mutation is blocked")
        return value.astimezone(UTC)
    started_utc=capture_utc_sample()
    started_mono=clock()
    resources,resource_evidence=_read_arm_collection(runner,options,root,
        f"/subscriptions/{options.subscription_id}/resources?api-version=2021-04-01",
        "profile_watcher_baseline_resources",clock,("id","type"))
    groups,group_evidence=_read_arm_collection(runner,options,root,
        f"/subscriptions/{options.subscription_id}/resourcegroups?api-version=2021-04-01",
        "profile_watcher_baseline_groups",clock,("id","name"))
    finished_utc=capture_utc_sample()
    finished_mono=clock()
    resource_inventory=sorted(({"id":row["id"].lower(),"type":row["type"].lower()} for row in resources),
        key=lambda row:(row["id"],row["type"]))
    group_inventory=sorted(({"id":row["id"].lower(),"name":row["name"].lower()} for row in groups),
        key=lambda row:(row["id"],row["name"]))
    baseline=_watcher_baseline(groups,resources)
    baseline.update({"schema_version":2,"subscription_id":options.subscription_id,
        "run_id":deployment.run_id,"deployment_id":deployment.deployment_id,
        "config_sha256":deployment.config_sha256,"profile":_profile_name(deployment),
        "capture_started_at_utc":started_utc.isoformat(),"capture_started_monotonic":started_mono,
        "capture_finished_at_utc":finished_utc.isoformat(),"capture_finished_monotonic":finished_mono,
        "resource_inventory":resource_inventory,"group_inventory":group_inventory,
        "source_action_ids":[item["action_id"] for item in (*resource_evidence,*group_evidence)
            if isinstance(item.get("action_id"),str) and item["action_id"]],
        "inventory_sha256":hashlib.sha256(json.dumps({"resources":resource_inventory,"groups":group_inventory},
            sort_keys=True,separators=(",",":")).encode()).hexdigest()})
    baseline["sha256"]=_profile_timing_digest(baseline)
    state=json.loads(journal.read_text(encoding="utf-8"))
    if (state.get("run_id")!=deployment.run_id or state.get("deployment_id")!=deployment.deployment_id
            or state.get("config_sha256")!=deployment.config_sha256):
        raise RuntimeError("attempt journal identity changed during Network Watcher baseline capture")
    prior=state.get("profile_external_baseline")
    if prior is not None and prior!=baseline:
        raise RuntimeError("pre-creation Network Watcher baseline changed during concurrent registration")
    # Validate chronology, full identity, inventory digest, and the just-recorded
    # source actions before publishing this baseline or allowing the caller to
    # proceed to expiry/provider mutations. Keep the completed query actions in
    # the journal when validation fails so recovery retains the evidence.
    if not _valid_watcher_baseline(baseline, deployment, options, state.get("actions")):
        raise RuntimeError("captured pre-creation Network Watcher baseline is invalid; provider mutation is blocked")
    state["profile_external_baseline"]=baseline
    _atomic_private(journal,state)
    if options.azure_auxiliary_cleanup is not None:
        from .azure_auxiliary_cleanup import AuxiliaryCleanupCohort
        owner = next((row for row in options.azure_auxiliary_cleanup["owners"]
            if row["run_id"] == deployment.run_id and row["deployment_id"] == deployment.deployment_id
            and row["config_sha256"] == deployment.config_sha256), None)
        if owner is None:
            raise PermissionError("Azure pre-creation cohort baseline does not bind this exact parent")
        AuxiliaryCleanupCohort(options.azure_auxiliary_cleanup).record_precreation_baseline(owner, baseline)


def _valid_watcher_baseline(baseline: Any, deployment: ProviderDeploymentInput,
                            options: AzureRuntimeOptions,
                            journal_actions: Any = None) -> bool:
    if not isinstance(baseline,dict) or baseline.get("schema_version")!=2:
        return False
    if (baseline.get("subscription_id")!=options.subscription_id
            or baseline.get("run_id")!=deployment.run_id
            or baseline.get("deployment_id")!=deployment.deployment_id
            or baseline.get("config_sha256")!=deployment.config_sha256
            or baseline.get("profile")!=_profile_name(deployment)):
        return False
    resources,groups=baseline.get("resource_inventory"),baseline.get("group_inventory")
    source_ids=baseline.get("source_action_ids")
    if (not isinstance(resources,list) or not isinstance(groups,list)
            or not isinstance(source_ids,list) or len(source_ids)<2
            or any(not isinstance(value,str) or not value for value in source_ids)
            or len(set(source_ids))!=len(source_ids)):
        return False
    if journal_actions is not None:
        if not isinstance(journal_actions,list):
            return False
        action_types={}
        for action in journal_actions:
            if not isinstance(action,dict):
                continue
            request=action.get("request")
            if not isinstance(request,dict):
                continue
            action_id,action_type=request.get("action_id"),request.get("action_type")
            if isinstance(action_id,str) and isinstance(action_type,str):
                action_types[action_id]=action_type
        bound_types=[action_types.get(action_id) for action_id in source_ids]
        if (any(action_type is None for action_type in bound_types)
                or not any(action_type.startswith("profile_watcher_baseline_resources") for action_type in bound_types)
                or not any(action_type.startswith("profile_watcher_baseline_groups") for action_type in bound_types)
                or any(not re.fullmatch(r"profile_watcher_baseline_(?:resources|groups)(?:_page_[1-9][0-9]*)?",action_type)
                    for action_type in bound_types)):
            return False
    if any(not isinstance(row,dict) or set(row)!={"id","type"}
            or not all(isinstance(row.get(key),str) and row[key] for key in ("id","type"))
            or not row["id"].startswith(f"/subscriptions/{options.subscription_id}/".lower())
            for row in resources):
        return False
    if any(not isinstance(row,dict) or set(row)!={"id","name"}
            or not all(isinstance(row.get(key),str) and row[key] for key in ("id","name"))
            or not row["id"].startswith(f"/subscriptions/{options.subscription_id}/".lower())
            for row in groups):
        return False
    try:
        start_utc=datetime.fromisoformat(baseline["capture_started_at_utc"])
        finish_utc=datetime.fromisoformat(baseline["capture_finished_at_utc"])
        start_mono=float(baseline["capture_started_monotonic"])
        finish_mono=float(baseline["capture_finished_monotonic"])
    except (KeyError,TypeError,ValueError):
        return False
    if (start_utc.tzinfo is None or finish_utc.tzinfo is None
            or start_utc.utcoffset()!=timedelta(0) or finish_utc.utcoffset()!=timedelta(0)
            or finish_utc<start_utc
            or not isfinite(start_mono) or not isfinite(finish_mono) or finish_mono<start_mono):
        return False
    inventory_hash=hashlib.sha256(json.dumps({"resources":resources,"groups":groups},
        sort_keys=True,separators=(",",":")).encode()).hexdigest()
    if baseline.get("inventory_sha256")!=inventory_hash:
        return False
    # The complete inventory is persisted above; these two projections are the
    # only external resources whose ownership the cleanup verifier needs.
    projected=_watcher_baseline(
        [{"id":row["id"],"name":row["name"]} for row in groups],
        [{"id":row["id"],"type":row["type"]} for row in resources])
    if baseline.get("groups")!=projected["groups"] or baseline.get("watchers")!=projected["watchers"]:
        return False
    return baseline.get("sha256")==_profile_timing_digest(baseline)


def _delete_owned_arm_child(runner: CommandRunner, options: AzureRuntimeOptions, root: Path,
                            account: str, collection: str, child: str,
                            clock: Callable[[],float], actions: list[dict[str,Any]]) -> None:
    base=f"{account}/{collection}/{child}?api-version={AUTOMATION_API}"
    expected_missing_automation_child_url=base if collection in {"jobSchedules","schedules","runbooks"} else None
    read=_command(runner,options,root,_azure_rest("GET",base),f"expiry_{collection}_before_delete",clock,
        allow_not_found=True,expected_missing_automation_child_url=expected_missing_automation_child_url)
    actions.append(_safe_cleanup_action(options,read))
    if read.outcome=="failed" and (_is_exact_not_found(read)
            or (expected_missing_automation_child_url is not None
                and _is_exact_missing_automation_child(read,expected_missing_automation_child_url))): return
    try: observed=json.loads(read.stdout)
    except (json.JSONDecodeError,TypeError) as exc: raise RuntimeError(f"Azure {collection} readback is malformed") from exc
    expected=base.split("?",1)[0]
    if not isinstance(observed,dict) or not isinstance(observed.get("id"),str) or observed["id"].lower()!=expected.lower():
        raise RuntimeError(f"Azure {collection} readback identity mismatch")
    deleted=_command(runner,options,root,_azure_rest("DELETE",base),f"expiry_{collection}_delete",clock,allow_not_found=True)
    actions.append(_safe_cleanup_action(options,deleted))
    verify=_command(runner,options,root,_azure_rest("GET",base),f"expiry_{collection}_absence",clock,
        allow_not_found=True,expected_missing_automation_child_url=expected_missing_automation_child_url)
    actions.append(_safe_cleanup_action(options,verify))
    if verify.outcome!="failed" or not (_is_exact_not_found(verify)
            or (expected_missing_automation_child_url is not None
                and _is_exact_missing_automation_child(verify,expected_missing_automation_child_url))):
        raise RuntimeError(f"Azure {collection} deletion is not confirmed")


def _verify_profile_absence(deployment: ProviderDeploymentInput, options: AzureRuntimeOptions,
                            runner: CommandRunner, root: Path, clock: Callable[[], float],
                            state: dict[str, Any], actions: list[dict[str, Any]]) -> None:
    """Fresh, independent Azure inventory after all requested deletes completed."""
    name = _slug(deployment)
    group_prefix = f"/subscriptions/{options.subscription_id}/resourceGroups/{name}/".lower()
    group = _command(runner, options, root, ["az", "group", "exists", "--name", name, "--output", "json"],
        "profile_absence_group", clock)
    actions.append(_safe_cleanup_action(options, group))
    present = json.loads(group.stdout)
    if group.stderr.strip() or type(present) is not bool or present:
        raise RuntimeError("independent Azure absence inventory found or could not exclude the attempt resource group")
    resources, evidence = _read_arm_collection(runner, options, root,
        f"/subscriptions/{options.subscription_id}/resources?api-version=2021-04-01",
        "profile_absence_resources", clock, ("id", "type"))
    actions.extend(evidence)
    groups, evidence = _read_arm_collection(runner, options, root,
        f"/subscriptions/{options.subscription_id}/resourcegroups?api-version=2021-04-01",
        "profile_absence_groups", clock, ("id","name"))
    actions.extend(evidence)
    expected_group_id=f"/subscriptions/{options.subscription_id}/resourceGroups/{state['resource_group']}".lower()
    residual_attempt_groups=[row["id"] for row in groups if row["id"].lower()==expected_group_id]
    if residual_attempt_groups:
        raise RuntimeError("independent Azure group inventory found the attempt resource group: "
            + ", ".join(residual_attempt_groups))
    baseline=state.get("profile_external_baseline")
    baseline_valid=_valid_watcher_baseline(baseline,deployment,options,state.get("actions"))
    current=_watcher_baseline(groups,resources)
    if not baseline_valid:
        if current["groups"] or current["watchers"]:
            raise RuntimeError("Network Watcher baseline is unavailable and fresh inventory contains resources with unproven ownership")
        def mark_unknown(timer):
            timer["timing_status"]="unknown"
            timer["timing_failure"]=timer.get("timing_failure") or "pre-creation Network Watcher ownership baseline is unavailable"
            return timer
        _profile_timing_mutate(options,mark_unknown,expected_identity=_EXPLICIT_TIMING_IDENTITY.get())
    else:
        new_groups=sorted(set(current["groups"])-set(baseline["groups"]))
        new_watchers=sorted(set(current["watchers"])-set(baseline["watchers"]))
        if new_groups or new_watchers:
            raise RuntimeError("independent inventory found Network Watcher resources or parent groups with unproven attempt ownership")
    remaining = [row["id"] for row in resources if row["id"].lower().startswith(group_prefix)]
    if remaining:
        raise RuntimeError("independent Azure resource inventory found attempt-owned resources: " + ", ".join(remaining))
    expiry = state.get("expiry", {})
    principal = expiry.get("principal_id")
    assignment_id = expiry.get("role_assignment_id")
    assignments, evidence = _read_arm_collection(runner, options, root,
        f"/subscriptions/{options.subscription_id}/providers/Microsoft.Authorization/roleAssignments?api-version=2022-04-01",
        "profile_absence_role_assignments", clock, ("id",))
    actions.extend(evidence)
    for row in assignments:
        properties=row.get("properties")
        if not isinstance(properties,dict) or not isinstance(properties.get("principalId"),str) or not properties["principalId"].strip():
            raise RuntimeError("independent Azure role-assignment inventory lacks principal ownership fields")
    owned_assignments = [row["id"] for row in assignments if row["id"].lower().startswith(group_prefix)
        or (assignment_id and row["id"].lower() == str(assignment_id).lower())
        or (principal and str(row.get("properties", {}).get("principalId", "")).lower() == str(principal).lower())]
    if owned_assignments:
        raise RuntimeError("independent Azure role-assignment inventory found attempt-owned assignments")
    definitions, evidence = _read_arm_collection(runner, options, root,
        f"/subscriptions/{options.subscription_id}/providers/Microsoft.Authorization/roleDefinitions?%24filter=type%20eq%20%27CustomRole%27&api-version=2022-04-01",
        "profile_absence_role_definitions", clock, ("id",))
    actions.extend(evidence)
    for row in definitions:
        properties=row.get("properties")
        if not isinstance(properties,dict) or not isinstance(properties.get("roleName"),str) or not properties["roleName"].strip():
            raise RuntimeError("independent Azure role-definition inventory lacks role ownership fields")
    definition_id = expiry.get("role_definition_id")
    definition_name = name + "-deallocate-only"
    if any((definition_id and row["id"].lower() == str(definition_id).lower())
            or str(row.get("properties", {}).get("roleName", "")) == definition_name for row in definitions):
        raise RuntimeError("independent Azure role-definition inventory found the attempt custom role")
    account = expiry.get("automation_account_id")
    if account:
        expected_account=(f"/subscriptions/{options.subscription_id}/resourceGroups/{name}"
            f"/providers/Microsoft.Automation/automationAccounts/{name}-auto")
        if account != expected_account:
            raise RuntimeError("independent Azure Automation absence target is outside the owned account")
        targets = [(account, "automation account", AUTOMATION_API, None, None)]
        for collection, key in (("jobSchedules", "job_schedule_id"), ("schedules", "schedule"), ("runbooks", "runbook")):
            child = expiry.get(key) or expiry.get("metadata", {}).get(key)
            if child:
                expected_child=(None if collection=="jobSchedules" else
                    name+("-once" if collection=="schedules" else "-deallocate"))
                if expected_child is not None and child!=expected_child:
                    raise RuntimeError(f"independent Azure {collection} absence target is not the registered owned identity")
                targets.append((f"{account}/{collection}/{child}", f"Automation {collection} child",
                    AUTOMATION_API, collection, child))
        for target, label, api, collection, child in targets:
            url=f"{target}?api-version={api}"
            expected_missing_automation_child_url=(url if collection in {"jobSchedules","schedules","runbooks"} else None)
            expected_missing_automation_account_id=(account if label=="automation account" else None)
            if collection=="jobSchedules" and not _is_uuid_segment(str(child)):
                raise RuntimeError("independent Azure job schedule absence target is not the registered owned identity")
            result = _command(runner, options, root, _azure_rest("GET", url),
                "profile_absence_automation", clock, allow_not_found=True,
                allow_missing_resource_group=state["resource_group"],
                expected_missing_automation_child_url=expected_missing_automation_child_url,
                expected_missing_automation_account_id=expected_missing_automation_account_id)
            actions.append(_safe_cleanup_action(options, result))
            if result.outcome != "failed" or not (_is_exact_not_found(result)
                    or _is_exact_missing_attempt_resource_group(result, state["resource_group"])
                    or (expected_missing_automation_account_id is not None
                        and _is_exact_missing_automation_account(result,expected_missing_automation_account_id))
                    or (expected_missing_automation_child_url is not None
                        and _is_exact_missing_automation_child(result,expected_missing_automation_child_url))):
                raise RuntimeError(f"independent Azure {label} absence is not proved by typed NotFound")


def _cleanup_shared_network_watcher(deployment: ProviderDeploymentInput, options: AzureRuntimeOptions,
        runner: CommandRunner, root: Path, clock: Callable[[], float], utc_now: Callable[[], datetime],
        state: dict[str, Any], actions: list[dict[str, Any]]) -> None:
    """Delete one auto-created watcher only after exact parent and activity proof."""
    spec = options.azure_auxiliary_cleanup
    if spec is None:
        return
    from .azure_auxiliary_cleanup import AuxiliaryCleanupCohort, authorization_payload, canonical, owner_key
    card = authorization_payload(spec, utc_now().astimezone(UTC))
    if (options.subscription_id != spec["subscription_id"] or deployment.vm_a.region != spec["region"]
            or deployment.vm_b.region != spec["region"]):
        raise PermissionError("Azure auxiliary cleanup binding differs from the exact subscription or region")
    baseline = state.get("profile_external_baseline")
    if not _valid_watcher_baseline(baseline, deployment, options, state.get("actions")):
        raise RuntimeError("Azure auxiliary cleanup lacks the complete pre-mutation watcher baseline")
    group_id, watcher_id = spec["resource_group_id"].lower(), spec["watcher_id"].lower()
    owner = next((row for row in spec["owners"] if row["run_id"] == deployment.run_id
        and row["deployment_id"] == deployment.deployment_id
        and row["config_sha256"] == deployment.config_sha256), None)
    if owner is None:
        raise PermissionError("Azure auxiliary cleanup does not bind this exact parent deployment")
    expected_group = f"/subscriptions/{options.subscription_id}/resourceGroups/{_slug(deployment)}".lower()
    if owner["resource_group_id"].lower() != expected_group:
        raise PermissionError("Azure auxiliary cleanup owner resource group differs from deployment")
    vm_ids = [f"{expected_group}/providers/Microsoft.Compute/virtualMachines/{_slug(deployment)}-{r}".lower()
        for r in ("a", "b")]
    nic_ids = [f"{expected_group}/providers/Microsoft.Network/networkInterfaces/{_slug(deployment)}-{r}-nic".lower()
        for r in ("a", "b")]
    if sorted(x.lower() for x in owner["vm_ids"]) != sorted(vm_ids) or sorted(
            x.lower() for x in owner["nic_ids"]) != sorted(nic_ids):
        raise PermissionError("Azure auxiliary cleanup VM/NIC identities differ from deployment")
    timing = _require_profile_timing_anchor(deployment, options, require_stop=False)
    peer_deadline = timing.get("routine_deadline_monotonic") if timing else None
    if isinstance(peer_deadline, bool) or not isinstance(peer_deadline, (int, float)) or not isfinite(float(peer_deadline)):
        raise TimeoutError("original Azure cleanup deadline is unavailable")
    cohort = AuxiliaryCleanupCohort(spec)
    cohort_state = cohort.snapshot()
    initial = cohort_state.get("precreation_baseline")
    if not isinstance(initial, dict) or not isinstance(initial.get("baseline"), dict):
        raise RuntimeError("shared Azure cleanup cohort lacks its earliest pre-creation baseline")
    baseline = initial["baseline"]
    if group_id in baseline["groups"] or watcher_id in baseline["watchers"]:
        # The earliest complete cohort baseline proves the shared resource was
        # pre-existing. Preserve it and let ordinary per-parent absence checks run.
        return
    existing = cohort_state.get("claim")
    if isinstance(existing, dict):
        if existing.get("state") == "complete":
            cohort.wait_complete(deadline_monotonic=float(peer_deadline), clock=clock)
            return
        if existing.get("state") in {"claimed", "group_claimed"}:
            cohort.wait_complete(deadline_monotonic=float(peer_deadline), clock=clock)
            return
        if existing.get("state") == "leaf_absent":
            outcomes = cohort.snapshot().get("outcomes", [])
            leaf = [row for row in outcomes if row.get("action") == "leaf_delete"]
            if (not leaf or leaf[-1].get("outcome") != "succeeded"
                    or leaf[-1].get("identity", "").lower() != watcher_id
                    or leaf[-1].get("typed_404_identity", "").lower() != watcher_id):
                raise RuntimeError("saved leaf completion lacks exact typed absence identity")
            owner_state = cohort.snapshot().get("owners", {}).get(owner_key(owner))
            if not isinstance(owner_state, dict):
                raise RuntimeError("saved shared cleanup owner evidence is missing")
            cohort.wait_all_absent(owner=owner, baseline_sha256=baseline["sha256"],
                activity_sha256=owner_state["activity_sha256"], deadline_monotonic=float(peer_deadline), clock=clock)
            _finish_shared_watcher_group(spec, options, runner, root, clock, utc_now,
                cohort, existing["claim_id"], float(peer_deadline), actions)
            return
        raise RuntimeError("Azure auxiliary cleanup has a terminal or uncertain prior action")
    resources, ev = _read_arm_collection(runner, options, root,
        f"/subscriptions/{options.subscription_id}/resources?api-version=2021-04-01",
        "azure_auxiliary_inventory_resources", clock, ("id", "type"))
    actions.extend(ev)
    groups, ev = _read_arm_collection(runner, options, root,
        f"/subscriptions/{options.subscription_id}/resourcegroups?api-version=2021-04-01",
        "azure_auxiliary_inventory_groups", clock, ("id", "name"))
    actions.extend(ev)
    current = _watcher_baseline(groups, resources)
    if (sorted(set(current["watchers"]) - set(baseline["watchers"])) != [watcher_id]
            or sorted(set(current["groups"]) - set(baseline["groups"])) != [group_id]):
        raise RuntimeError("fresh inventory does not prove the exact newly created watcher and parent group")
    grouped, ev = _read_arm_collection(runner, options, root,
        f"{group_id}/resources?api-version=2021-04-01", "azure_auxiliary_group_inventory", clock, ("id", "type"))
    actions.extend(ev)
    if len(grouped) != 1 or grouped[0]["id"].lower() != watcher_id:
        raise RuntimeError("NetworkWatcherRG has foreign or additional child resources")
    activity, ev = _azure_watcher_activity(deployment, options, runner, root, baseline, watcher_id,
        card["expected_watcher_create_caller"], card["expected_owned_write_caller"], clock, utc_now)
    actions.extend(ev)
    activity_sha = hashlib.sha256(canonical(activity)).hexdigest()
    cohort.wait_all_absent(owner=owner, baseline_sha256=baseline["sha256"],
        activity_sha256=activity_sha, deadline_monotonic=float(peer_deadline), clock=clock)
    claim_id = cohort.claim_leaf(now_monotonic=clock())
    if claim_id is None:
        cohort.wait_complete(deadline_monotonic=float(peer_deadline), clock=clock)
        return
    for collection in ("flowLogs", "packetCaptures", "connectionMonitors"):
        children, ev = _read_arm_collection(runner, options, root,
            f"{watcher_id}/{collection}?api-version=2023-09-01",
            f"azure_auxiliary_{collection}_inventory", clock, ("id",))
        actions.extend(ev)
        if children:
            raise RuntimeError(f"Network Watcher {collection} collection is nonempty")
    fresh_resources, ev = _read_arm_collection(runner, options, root,
        f"/subscriptions/{options.subscription_id}/resources?api-version=2021-04-01",
        "azure_auxiliary_preleaf_resources", clock, ("id", "type"))
    actions.extend(ev)
    fresh_groups, ev = _read_arm_collection(runner, options, root,
        f"/subscriptions/{options.subscription_id}/resourcegroups?api-version=2021-04-01",
        "azure_auxiliary_preleaf_groups", clock, ("id", "name"))
    actions.extend(ev)
    _require_cleanup_cohort_parents_absent(spec, fresh_groups, fresh_resources)
    fresh_grouped, ev = _read_arm_collection(runner, options, root,
        f"{group_id}/resources?api-version=2021-04-01", "azure_auxiliary_preleaf_group_children", clock, ("id", "type"))
    actions.extend(ev)
    if len(fresh_grouped) != 1 or fresh_grouped[0].get("id", "").lower() != watcher_id:
        raise RuntimeError("immediate pre-delete NetworkWatcherRG inventory is not exact")
    leaf_url = watcher_id + "?api-version=2023-09-01"
    claim = cohort.snapshot().get("claim")
    if not isinstance(claim, dict) or claim.get("claim_id") != claim_id:
        raise RuntimeError("Network Watcher delete claim is no longer owned")
    if claim.get("state") == "claimed":
        authorization_payload(spec, utc_now().astimezone(UTC))
        before = _command(runner, options, root, _azure_rest("GET", leaf_url),
            "azure_auxiliary_watcher_etag", clock)
        actions.append(_safe_cleanup_action(options, before))
        observed = json.loads(before.stdout)
        if str(observed.get("id", "")).lower() != watcher_id or not isinstance(observed.get("etag"), str):
            raise RuntimeError("Network Watcher exact ETag identity is unavailable")
        delete = _command(runner, options, root,
            _azure_rest("DELETE", leaf_url, headers={"If-Match": observed["etag"]}),
            "azure_auxiliary_watcher_delete", clock)
        actions.append(_safe_cleanup_action(options, delete))
        absence = _command(runner, options, root, _azure_rest("GET", leaf_url),
            "azure_auxiliary_watcher_absence", clock, allow_not_found=True)
        actions.append(_safe_cleanup_action(options, absence))
        if not _is_exact_resource_404(absence, watcher_id):
            cohort.record_outcome(claim_id, action="leaf_delete", identity=watcher_id,
                outcome="unknown", evidence_sha256=hashlib.sha256(canonical(_safe_cleanup_action(options, absence))).hexdigest(),
                now_monotonic=clock())
            raise RuntimeError("Network Watcher deletion lacks an exact typed 404 identity")
        cohort.record_outcome(claim_id, action="leaf_delete", identity=watcher_id,
            outcome="succeeded", evidence_sha256=hashlib.sha256(canonical(_safe_cleanup_action(options, absence))).hexdigest(),
            now_monotonic=clock(),
            typed_404_identity=watcher_id)
    _finish_shared_watcher_group(spec, options, runner, root, clock, utc_now,
        cohort, claim_id, float(peer_deadline), actions)


def _finish_shared_watcher_group(spec: dict[str, Any], options: AzureRuntimeOptions,
        runner: CommandRunner, root: Path, clock: Callable[[], float], utc_now: Callable[[], datetime],
        cohort: Any, claim_id: str, peer_deadline: float, actions: list[dict[str, Any]]) -> None:
    from .azure_auxiliary_cleanup import authorization_payload, canonical
    group_id = spec["resource_group_id"].lower()
    if not cohort.claim_group(claim_id, now_monotonic=clock()):
        cohort.wait_complete(deadline_monotonic=peer_deadline, clock=clock)
        return
    authorization_payload(spec, utc_now().astimezone(UTC))
    grouped, ev = _read_arm_collection(runner, options, root,
        f"{group_id}/resources?api-version=2021-04-01", "azure_auxiliary_group_final_inventory", clock, ("id", "type"))
    actions.extend(ev)
    if grouped:
        raise RuntimeError("NetworkWatcherRG is not empty after exact watcher absence")
    global_resources, ev = _read_arm_collection(runner, options, root,
        f"/subscriptions/{options.subscription_id}/resources?api-version=2021-04-01",
        "azure_auxiliary_group_final_subscription_inventory", clock, ("id", "type"))
    actions.extend(ev)
    global_groups, ev = _read_arm_collection(runner, options, root,
        f"/subscriptions/{options.subscription_id}/resourcegroups?api-version=2021-04-01",
        "azure_auxiliary_group_final_groups", clock, ("id", "name"))
    actions.extend(ev)
    _require_cleanup_cohort_parents_absent(spec, global_groups, global_resources)
    if any(str(row.get("id", "")).lower().startswith(group_id + "/") for row in global_resources):
        raise RuntimeError("fresh full inventory found a child under NetworkWatcherRG")
    for collection in ("flowLogs", "packetCaptures", "connectionMonitors"):
        children, ev = _read_arm_collection(runner, options, root,
            f"{spec['watcher_id']}/{collection}?api-version=2023-09-01",
            f"azure_auxiliary_group_final_{collection}", clock, ("id",))
        actions.extend(ev)
        if children:
            raise RuntimeError(f"Network Watcher {collection} collection became nonempty before parent deletion")
    grouped, ev = _read_arm_collection(runner, options, root,
        f"{group_id}/resources?api-version=2021-04-01",
        "azure_auxiliary_group_immediate_children", clock, ("id", "type"))
    actions.extend(ev)
    if grouped:
        raise RuntimeError("NetworkWatcherRG acquired a child before parent deletion")
    group_url = f"/subscriptions/{options.subscription_id}/resourcegroups/NetworkWatcherRG?api-version=2021-04-01"
    read = _command(runner, options, root, _azure_rest("GET", group_url),
        "azure_auxiliary_group_exact_readback", clock)
    actions.append(_safe_cleanup_action(options, read))
    observed = json.loads(read.stdout)
    if str(observed.get("id", "")).lower() != group_id:
        raise RuntimeError("NetworkWatcherRG exact identity is not confirmed before deletion")
    authorization_payload(spec, utc_now().astimezone(UTC))
    deleted = _command(runner, options, root,
        _azure_rest("DELETE", group_url),
        "azure_auxiliary_group_delete", clock)
    actions.append(_safe_cleanup_action(options, deleted))
    absent = _command(runner, options, root,
        ["az", "group", "exists", "--name", "NetworkWatcherRG", "--output", "json"],
        "azure_auxiliary_group_absence", clock)
    actions.append(_safe_cleanup_action(options, absent))
    if absent.stderr.strip() or absent.stdout.strip().lower() != "false":
        cohort.record_outcome(claim_id, action="group_delete", identity=group_id, outcome="unknown",
            evidence_sha256=hashlib.sha256(canonical(_safe_cleanup_action(options, absent))).hexdigest(),
            now_monotonic=clock())
        raise RuntimeError("NetworkWatcherRG deletion is not confirmed absent")
    cohort.record_outcome(claim_id, action="group_delete", identity=group_id, outcome="succeeded",
        evidence_sha256=hashlib.sha256(canonical(_safe_cleanup_action(options, absent))).hexdigest(),
        now_monotonic=clock())


def _require_cleanup_cohort_parents_absent(spec: dict[str, Any], groups: list[dict[str, Any]],
        resources: list[dict[str, Any]]) -> None:
    group_ids = {str(row.get("id", "")).lower() for row in groups}
    resource_ids = {str(row.get("id", "")).lower() for row in resources}
    for owner in spec["owners"]:
        parent = owner["resource_group_id"].lower().rstrip("/")
        if parent in group_ids or any(item == parent or item.startswith(parent + "/") for item in resource_ids):
            raise RuntimeError("fresh all-subscription inventory found a live or unknown owned parent resource")
        if any(item.lower() in resource_ids for item in owner["vm_ids"] + owner["nic_ids"]):
            raise RuntimeError("fresh all-subscription inventory found an owned VM or NIC before shared deletion")


def _is_exact_resource_404(result: CommandResult, resource_id: str) -> bool:
    if result.outcome != "failed":
        return False
    texts = [value for value in (result.stdout, result.stderr) if value.strip()]
    if len(texts) != 1:
        return False
    text = texts[0]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        wrapped = re.fullmatch(r"ERROR: Not Found\((\{.*\})\)", text, flags=re.DOTALL)
        if wrapped is None:
            return False
        try:
            payload = json.loads(wrapped.group(1))
        except json.JSONDecodeError:
            return False
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict) or error.get("code") not in {"ResourceNotFound", "NotFound"}:
        return False
    targets = []
    for key in ("target", "resourceId", "resourceUri"):
        if key in error:
            value = error[key]
            if not isinstance(value, str) or not value:
                return False
            targets.append(value)
    details = error.get("details", [])
    if not isinstance(details, list):
        return False
    for item in details:
        if not isinstance(item, dict):
            return False
        for key in ("target", "resourceId", "resourceUri"):
            if key in item:
                value = item[key]
                if not isinstance(value, str) or not value:
                    return False
                targets.append(value)
    if targets:
        return all(value.lower() == resource_id.lower() for value in targets)
    message = error.get("message")
    # A code-only 404 is ambiguous. Accept the known exact-message form only
    # when it names the exact resource requested by the caller.
    return (isinstance(message, str) and bool(message.strip())
        and resource_id.lower() in message.lower())


def _azure_watcher_activity(deployment: ProviderDeploymentInput, options: AzureRuntimeOptions,
        runner: CommandRunner, root: Path, baseline: dict[str, Any], watcher_id: str,
        expected_create_caller: str, expected_owned_caller: str,
        clock: Callable[[], float], utc_now: Callable[[], datetime]):
    start_text = baseline.get("capture_finished_at_utc")
    if not isinstance(start_text, str):
        raise RuntimeError("Azure Activity Log proof requires the complete cohort baseline end time")
    start = datetime.fromisoformat(start_text).astimezone(UTC)
    end = utc_now().astimezone(UTC)
    if end < start:
        raise RuntimeError("Azure Activity Log query window precedes the watcher baseline")
    owners = options.azure_auxiliary_cleanup["owners"]
    resource_owner = {resource_id.lower(): owner for owner in owners
        for resource_id in owner["vm_ids"] + owner["nic_ids"]}
    events, evidence = [], []
    targets = [watcher_id, *sorted(resource_owner)]
    for index, target in enumerate(targets):
        filt = f"eventTimestamp ge '{start.isoformat()}' and eventTimestamp le '{end.isoformat()}' and resourceUri eq '{target}'"
        encoded_filter = quote(filt, safe="'() ")
        endpoint = (f"/subscriptions/{options.subscription_id}/providers/Microsoft.Insights/eventtypes/management/values"
            f"?api-version=2015-04-01&$filter={encoded_filter}")
        rows, page_evidence = _read_arm_collection(runner, options, root, endpoint,
            f"azure_auxiliary_activity_{index + 1}", clock, ("id", "resourceId"))
        events.extend(rows)
        evidence.extend(page_evidence)
    watcher_create = []
    peer_writes = {}
    for row in events:
        details = row.get("properties", {})
        if not isinstance(details, dict):
            raise RuntimeError("Azure Activity Log event properties are malformed")
        resource = row.get("resourceId")
        caller, corr = row.get("caller"), row.get("correlationId")
        status, operation = row.get("status", {}), row.get("operationName", {})
        status = status.get("value") if isinstance(status, dict) else status
        operation = operation.get("value") if isinstance(operation, dict) else operation
        if not isinstance(resource, str) or not resource:
            raise RuntimeError("Azure Activity Log event lacks a resource identity")
        if not isinstance(operation, str) or not operation:
            if resource.lower() == watcher_id.lower() or resource.lower() in resource_owner:
                raise RuntimeError("Azure Activity Log event for an owned target lacks its operation identity")
            continue
        owner_target = resource.lower() in resource_owner
        expected_owned_operation = None
        if owner_target and "/providers/microsoft.network/networkinterfaces/" in resource.lower():
            expected_owned_operation = "microsoft.network/networkinterfaces/write"
        elif owner_target and "/providers/microsoft.compute/virtualmachines/" in resource.lower():
            expected_owned_operation = "microsoft.compute/virtualmachines/write"
        watcher_write = (resource.lower() == watcher_id.lower()
            and operation.lower() == "microsoft.network/networkwatchers/write")
        is_owned_write = owner_target and operation.lower() == expected_owned_operation
        # Actual Activity Log exports include non-write health events for VM
        # targets, sometimes without a caller. They do not prove ownership.
        if not watcher_write and not is_owned_write:
            continue
        if not all(isinstance(value, str) and value for value in (caller, corr, status)):
            raise RuntimeError("Azure Activity Log target write lacks caller, correlation, or status")
        if status.lower() != "succeeded":
            continue
        raw_substatus = row.get("subStatus", {})
        if not isinstance(raw_substatus, dict):
            raise RuntimeError("Azure Activity Log subStatus field is malformed")
        substatus = raw_substatus.get("value")
        is_watcher_create = (watcher_write and status.lower() == "succeeded"
            and isinstance(substatus, str) and substatus.lower() == "created"
            and str(details.get("statusCode", "")).lower() == "created"
            and str(raw_substatus.get("localizedValue", "")).lower() == "created (http status code: 201)")
        is_owned_write = is_owned_write and status.lower() == "succeeded"
        if watcher_write and not is_watcher_create:
            raise RuntimeError("successful Network Watcher write is not an exact HTTP 201 creation")
        if (is_watcher_create and caller != expected_create_caller) or (is_owned_write and caller != expected_owned_caller):
            raise RuntimeError("Azure Activity Log caller differs from the exact authorized principal")
        if is_watcher_create:
            timestamp = row.get("eventTimestamp")
            try:
                observed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except (AttributeError, ValueError) as exc:
                raise RuntimeError("Network Watcher Activity Log timestamp is malformed") from exc
            if observed.tzinfo is None or not start < observed.astimezone(UTC) <= end:
                raise RuntimeError("Network Watcher creation event is outside the cohort baseline and cleanup window")
            watcher_create.append(corr)
        if is_owned_write:
            timestamp = row.get("eventTimestamp")
            try:
                observed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except (AttributeError, ValueError) as exc:
                raise RuntimeError("owned parent Activity Log timestamp is malformed") from exc
            if observed.tzinfo is None or not start < observed.astimezone(UTC) <= end:
                raise RuntimeError("owned parent write is outside the original cohort mutation window")
            owner = resource_owner[resource.lower()]
            peer_writes.setdefault((owner["run_id"], corr), set()).add(resource.lower())
    if len(watcher_create) != 1:
        raise RuntimeError("Activity Log does not prove one exact successful Network Watcher creation")
    correlation = watcher_create[0]
    matches = [(run_id, writes) for (run_id, corr), writes in peer_writes.items() if corr == correlation]
    if len(matches) != 1:
        raise RuntimeError("Network Watcher creation correlation does not uniquely match owned NIC and VM writes")
    owner = next(item for item in owners if item["run_id"] == matches[0][0])
    if not (set(x.lower() for x in owner["nic_ids"]) & matches[0][1]
            and set(x.lower() for x in owner["vm_ids"]) & matches[0][1]):
        raise RuntimeError("Network Watcher creation correlation lacks both an owned NIC and VM write")
    return {"events": events, "correlation_id": correlation}, evidence


def _azure_rest(method: str, url: str, body: Optional[dict[str, Any]] = None,
                headers: Optional[dict[str, str]] = None) -> list[str]:
    args = ["az", "rest", "--method", method, "--url", url, "--output", "json"]
    header_values = dict(headers or {})
    if body is not None:
        header_values.setdefault("Content-Type", "application/json")
        args += ["--body", json.dumps(body, separators=(",", ":"))]
    if header_values:
        args += ["--headers", *[f"{key}={value}" for key, value in sorted(header_values.items())]]
    return args


def _register_expiry_impl(deployment: ProviderDeploymentInput, request: ExpiryRequest, options: AzureRuntimeOptions,
                    runner: CommandRunner, *, clock: Callable[[], float], utc_now: Callable[[], datetime],
                    sleep: Callable[[float], None]) -> ExpiryEvidence:
    """Prepare/read back Automation and bind one UTC job before pair creation."""
    now = utc_now().astimezone(UTC); mono = clock()
    if _profile_name(deployment):
        # Factory preparation owns the original anchor. Registration must never
        # recreate it after the attempt has begun or make provider calls first.
        anchor = _require_profile_timing_anchor(deployment, options, require_stop=False)
        reservation = deployment.reservation.value
        anchor_origin_utc=datetime.fromisoformat(anchor["origin_utc"])
        anchor_origin_mono=float(anchor["origin_monotonic"])
        mapped_cap=anchor_origin_mono+(reservation.expires_at.value-anchor_origin_utc).total_seconds()
        source_cap = _source_cap_deadline(options, reservation)
        # Factory-created settings retain the admission cap-start monotonic
        # sample. Compare against that immutable basis; a later UTC/monotonic
        # sample can differ by clock-read latency even when the cap is valid.
        # Keep the original one-microsecond guard and never raise the runtime cap.
        cap_mapping_valid = float(anchor["cap_deadline_monotonic"]) <= mapped_cap + 1e-6
        if source_cap is not None:
            cap_mapping_valid = (cap_mapping_valid
                and anchor.get("cap_started_monotonic") == options.cap_started_monotonic
                and anchor.get("source_cap_deadline_monotonic") == source_cap
                and float(anchor["cap_deadline_monotonic"]) <= source_cap + 1e-6)
        if (anchor.get("clock_identity") != _clock_identity()
                or datetime.fromisoformat(anchor["cap_started_at"]) != reservation.cap_started_at.value
                or datetime.fromisoformat(anchor["cap_expires_at"]) != reservation.expires_at.value
                or anchor_origin_utc < reservation.cap_started_at.value or anchor_origin_utc > now
                or anchor_origin_mono > mono
                or float(anchor["cap_deadline_monotonic"]) != float(options.cap_deadline_monotonic)
                or not cap_mapping_valid
                or anchor.get("provider_stop_at") is not None):
            raise ValueError("original Azure profile timing anchor is foreign, expired, or already bound")
    name, ids = _validate_request(deployment, options, now, mono)
    if request.provider != Provider.AZURE or request.action != "deallocate" or request.run_id != deployment.run_id or request.deployment_id != deployment.deployment_id:
        raise ValueError("expiry request identity/action mismatch")
    if request.resource_namespace != name:
        raise ValueError("expiry request namespace does not match deterministic attempt")
    reservation = deployment.reservation.value
    requested_trigger = request.expires_at.value
    if requested_trigger is None:
        raise ValueError("expiry trigger is required")
    trigger = _azure_schedule_trigger(requested_trigger)
    if trigger <= now:
        raise ValueError("minute-aligned Azure expiry trigger is no longer in the future")
    if (requested_trigger >= reservation.expires_at.value or requested_trigger >= options.retry_end_at
            or trigger >= reservation.expires_at.value or trigger >= options.retry_end_at):
        raise ValueError("expiry trigger must precede retry end and reservation cap")
    if (options.retry_end_at-trigger).total_seconds() > float(options.expiry_cost_input["job_minutes_bound"])*60:
        raise ValueError("bounded Automation job minutes do not cover the configured retry window")
    if (trigger-now).total_seconds() < 300:
        raise ValueError("Automation schedule requires at least 300 seconds lead")
    if reservation.cap_started_at.value + timedelta(seconds=options.complete_workflow_seconds) > trigger:
        raise ValueError("complete workflow bound exceeds actual expiry trigger")
    if trigger <= now + timedelta(seconds=options.post_registration_workflow_seconds):
        raise ValueError("complete post-registration workflow must fit before trigger")
    if request.reservation_id.value != reservation.reservation_id.value:
        raise ValueError("expiry reservation ID mismatch")
    if request.resource_namespace != _slug(deployment):
        raise ValueError("expiry request namespace does not match deterministic attempt")
    if request.target_selector != ";".join(ids):
        raise ValueError("expiry target selector must name exactly A and B")
    root, journal, pair_vars, expiry_vars = prepare_recovery(deployment, options, clock=clock,
        utc_now=utc_now, initialize_timing=False)
    _capture_profile_watcher_baseline(deployment,options,runner,root,journal,clock,utc_now)
    _validate_provider_registrations(runner, options, root, clock)
    account = f"/subscriptions/{options.subscription_id}/resourceGroups/{name}/providers/Microsoft.Automation/automationAccounts/{name}-auto"
    rb = name + "-deallocate"
    sched = name + "-once"
    state = json.loads(journal.read_text())
    prior=state.setdefault("expiry",{})
    job_id=str(prior.get("job_schedule_id") or uuid.uuid4())
    prior.update({"state":"preparing","targets":ids,"automation_account_id":account,
        "runbook":rb,"schedule":sched,"job_schedule_id":job_id})
    prior.setdefault("children",{})
    state["expiry_state"]="preparing"
    _atomic_private(journal, state)
    # Terraform creates only the attempt group, Basic Automation identity, and narrow role.
    _tf(root,options,runner,clock,"terraform/azure/expiry","init","-input=false",action="expiry_init")
    review_now, review_mono = utc_now().astimezone(UTC), clock()
    review_slack = min(options.cap_deadline_monotonic-review_mono,
        (trigger-review_now).total_seconds()) - options.post_registration_workflow_seconds
    if review_slack <= 0: raise TimeoutError("expiry plan review no longer fits before the workflow allowance")
    review_options = options.model_copy(update={"cap_deadline_monotonic":review_mono+review_slack})
    _tf(root,review_options,runner,clock,"terraform/azure/expiry","apply","-input=false",f"-var-file={expiry_vars}",action="expiry_prerequisites")
    expiry_outputs = json.loads(_tf(root,options,runner,clock,"terraform/azure/expiry","output","-json",action="expiry_outputs").stdout)
    expiry_ids = {key: item.get("value") for key, item in expiry_outputs.items()}
    required_outputs = ("resource_group_id", "automation_account_id", "principal_id", "role_assignment_id", "role_definition_id")
    if any(not expiry_ids.get(key) for key in required_outputs):
        raise RuntimeError("expiry prerequisite outputs are incomplete")
    account_read=json.loads(_command(runner,options,root,_azure_rest("GET",f"{account}?api-version={AUTOMATION_API}"),"automation_account_readback",clock).stdout)
    if account_read.get("location") != deployment.vm_a.region:
        raise RuntimeError("Automation account region mismatch")
    account_identity=account_read.get("identity",{})
    if account_identity.get("type")!="SystemAssigned" or account_identity.get("principalId","").lower()!=str(expiry_ids["principal_id"]).lower():
        raise RuntimeError("Automation account system-assigned identity readback mismatch")
    state=json.loads(journal.read_text(encoding="utf-8"))
    state["expiry"].update({"resource_group_id":expiry_ids["resource_group_id"],"role_assignment_id":expiry_ids["role_assignment_id"],
        "role_definition_id":expiry_ids["role_definition_id"],"principal_id":expiry_ids["principal_id"]})
    _atomic_private(journal,state)
    base = f"{account}/runbooks/{rb}?api-version={AUTOMATION_API}"
    script_path = Path(__file__).resolve().parents[2] / "terraform/azure/expiry/deallocate.ps1"
    script = script_path.read_text(encoding="utf-8")
    runbook = {"location":deployment.vm_a.region,"properties":{"runbookType":"PowerShell","logVerbose":False,"logProgress":True,"description":"F06 deallocate-only expiry"}}
    state=json.loads(journal.read_text(encoding="utf-8")); state["expiry"]["children"]["runbook"]="creating"; _atomic_private(journal,state)
    _command(runner, options, root, _azure_rest("PUT", base, runbook), "runbook_draft", clock)
    state=json.loads(journal.read_text(encoding="utf-8")); state["expiry"]["children"]["runbook"]="draft"; _atomic_private(journal,state)
    state=json.loads(journal.read_text(encoding="utf-8")); state["expiry"]["children"]["content"]="creating"; _atomic_private(journal,state)
    _command(runner, options, root, ["az", "rest", "--method", "PUT", "--url", f"{account}/runbooks/{rb}/draft/content?api-version={AUTOMATION_API}", "--headers", "Content-Type=text/powershell", "--body", script], "runbook_content", clock)
    state=json.loads(journal.read_text(encoding="utf-8")); state["expiry"]["children"]["content"]="uploaded"; _atomic_private(journal,state)
    state=json.loads(journal.read_text(encoding="utf-8")); state["expiry"]["children"]["publish"]="creating"; _atomic_private(journal,state)
    _command(runner, options, root, _azure_rest("POST", f"{account}/runbooks/{rb}/publish?api-version={AUTOMATION_API}"), "runbook_publish", clock)
    state=json.loads(journal.read_text(encoding="utf-8")); state["expiry"]["children"]["publish"]="submitted"; _atomic_private(journal,state)
    publish_wall_deadline=time.monotonic()+options.command_timeout_seconds
    publish_mono_deadline=min(options.cap_deadline_monotonic,mono+options.command_timeout_seconds)
    published={}
    while True:
        published = json.loads(_command(runner, options, root, _azure_rest("GET", base), "runbook_readback", clock).stdout or "{}")
        if published.get("location") != deployment.vm_a.region:
            raise RuntimeError("Automation runbook region mismatch")
        if (published.get("properties", {}).get("state") == "Published"
                and published.get("properties", {}).get("runbookType") == "PowerShell"
                and published.get("properties", {}).get("runtimeEnvironment") in (None, "", "PowerShell-5.1")):
            break
        remaining=min(publish_mono_deadline-clock(),publish_wall_deadline-time.monotonic())
        if remaining <= 0:
            raise TimeoutError("runbook publication did not reach Published within the original command deadline")
        sleep(min(options.poll_seconds,remaining))
    content = _command(runner, options, root, ["az", "rest", "--method", "GET", "--url", f"{account}/runbooks/{rb}/content?api-version={AUTOMATION_API}", "--output", "tsv"], "published_content_readback", clock)
    digest = hashlib.sha256(script.encode()).hexdigest()
    if published.get("properties", {}).get("state") != "Published" or not _azure_tsv_content_matches(content.stdout,digest):
        raise RuntimeError("runbook publication/content readback did not match uploaded script")
    sched_url = f"{account}/schedules/{sched}?api-version={AUTOMATION_API}"
    schedule_body = {"name":sched,"properties":{"frequency":"OneTime","startTime":trigger.isoformat().replace("+00:00","Z"),"timeZone":"UTC","description":deployment.deployment_id}}
    if (trigger - utc_now().astimezone(UTC)).total_seconds() < 300:
        raise ValueError("Automation schedule requires at least 300 seconds lead before schedule mutation")
    state=json.loads(journal.read_text(encoding="utf-8")); state["expiry"]["children"]["schedule"]="creating"; _atomic_private(journal,state)
    _command(runner, options, root, _azure_rest("PUT", sched_url, schedule_body), "schedule_create", clock)
    state=json.loads(journal.read_text(encoding="utf-8")); state["expiry"]["children"]["schedule"]="created"; _atomic_private(journal,state)
    sched_read = json.loads(_command(runner, options, root, _azure_rest("GET", sched_url), "schedule_readback", clock).stdout or "{}")
    props = sched_read.get("properties", {})
    if not _azure_schedule_matches(props, trigger):
        raise RuntimeError("schedule readback mismatch")
    if (trigger - utc_now().astimezone(UTC)).total_seconds() < 300:
        raise ValueError("Automation schedule no longer has 300 seconds lead")
    binding = {"properties":{"runbook":{"name":rb},"schedule":{"name":sched},"parameters":{
        "vmaresourceid":ids[0],"vmbresourceid":ids[1],"subscriptionid":options.subscription_id,
        "resourcegroupname":name,
        "deploymentid":deployment.deployment_id,"retryendat":options.retry_end_at.isoformat().replace("+00:00","Z"),
        "pollseconds":str(options.poll_seconds)}}}
    job_url = f"{account}/jobSchedules/{job_id}?api-version={AUTOMATION_API}"
    state=json.loads(journal.read_text(encoding="utf-8")); state["expiry"]["children"]["job_schedule"]="creating"; _atomic_private(journal,state)
    _command(runner, options, root, _azure_rest("PUT", job_url, binding), "job_binding_create", clock)
    state=json.loads(journal.read_text(encoding="utf-8")); state["expiry"]["children"]["job_schedule"]="created"; _atomic_private(journal,state)
    job_read = json.loads(_command(runner, options, root, _azure_rest("GET", job_url), "job_binding_readback", clock).stdout or "{}")
    actual = job_read.get("properties", {}).get("parameters", {})
    job_props=job_read.get("properties",{})
    if (job_read.get("id", "").lower() != job_url.split("?", 1)[0].lower()
            or job_props.get("runOn") not in (None,"")
            or job_props.get("runbook",{}).get("name")!=rb
            or job_props.get("schedule",{}).get("name")!=sched):
        raise RuntimeError("job binding identity readback mismatch")
    if not isinstance(actual,dict) or {k.lower():v for k,v in actual.items()} != binding["properties"]["parameters"]:
        raise RuntimeError("job binding readback mismatch")
    _, assignments = _role_assignments_at_scope(runner, options, root,
        expiry_ids["resource_group_id"], "role_assignment_readback", clock)
    assignment = next((x for x in assignments if x.get("id", "").lower() == expiry_ids["role_assignment_id"].lower()), None)
    if assignment is None or assignment.get("principalId", "").lower() != expiry_ids["principal_id"].lower() or assignment.get("scope", "").lower() != expiry_ids["resource_group_id"].lower() or assignment.get("roleDefinitionId", "").lower()!=expiry_ids["role_definition_id"].lower():
        raise RuntimeError("Automation managed identity role assignment readback mismatch")
    roles = json.loads(_command(runner, options, root, ["az", "role", "definition", "list", "--name", name+"-deallocate-only", "--output", "json"], "role_definition_readback", clock).stdout or "[]")
    role = next((x for x in roles if x.get("id", "").lower() == expiry_ids["role_definition_id"].lower()), None)
    permissions = role.get("permissions", []) if role else []
    if (role is None or [scope.lower() for scope in role.get("assignableScopes",[])] != [expiry_ids["resource_group_id"].lower()]
            or len(permissions) != 1 or sorted(permissions[0].get("actions", [])) != sorted(ROLE_ACTIONS)
            or permissions[0].get("notActions", []) or permissions[0].get("dataActions", [])
            or permissions[0].get("notDataActions", [])):
        raise RuntimeError("deallocate-only custom role readback mismatch")
    module_url = f"{account}/modules/Az.Accounts?api-version={AUTOMATION_API}"
    module = json.loads(_command(runner, options, root, _azure_rest("GET", module_url), "automation_module_readback", clock).stdout or "{}")
    module_props = module.get("properties", {})
    if module_props.get("provisioningState") != "Succeeded" or not module_props.get("version"):
        raise RuntimeError("Az.Accounts Automation module is not provisioned with a version")
    registered_at = utc_now().astimezone(UTC)
    if trigger <= registered_at + timedelta(seconds=options.post_registration_workflow_seconds):
        raise ValueError("post-registration workflow no longer fits actual trigger")
    metadata = {"mechanism":"azure_automation_once_deallocate","api_version":AUTOMATION_API,"resource_group":name,"location":deployment.vm_a.region,
        "reservation_id":reservation.reservation_id.value,"source_sha256":reservation.source_sha256,
        "runbook":rb,"schedule":sched,"job_schedule_id":job_id,
        "requested_trigger":requested_trigger.isoformat(),"trigger":trigger.isoformat(),"target_ids":ids,
        "published_content_sha256":digest,"runtime":"PowerShell 5.1","module":{"name":"Az.Accounts","version":module_props["version"]},
        "managed_identity_principal_id":expiry_ids["principal_id"],"role_assignment_id":expiry_ids["role_assignment_id"],
        "role_definition_id":expiry_ids["role_definition_id"],"resource_group_id":expiry_ids["resource_group_id"],
        "automation_account_id":expiry_ids["automation_account_id"],"role_actions":ROLE_ACTIONS,"role_scope":expiry_ids["resource_group_id"],
        "price_input":options.expiry_cost_input,"complete_workflow_seconds":options.complete_workflow_seconds,
        "post_registration_workflow_seconds":options.post_registration_workflow_seconds,"retry_end_at":options.retry_end_at.isoformat(),
        "schedule_readback":props,"binding_readback":actual,"effective_rbac_unproved":True}
    state=json.loads(journal.read_text(encoding="utf-8"))
    state.setdefault("expiry",{}).update({"state":"registered","metadata":metadata})
    state["expiry_state"]="registered"
    _atomic_private(journal,state)
    return ExpiryEvidence(schema_version=2,run_id=deployment.run_id,deployment_id=deployment.deployment_id,
        provider=Provider.AZURE,action="deallocate",state="registered",registration_id=Evidence(value=job_url),
        registered_at=Evidence(value=registered_at),expires_at=Evidence(value=trigger),target_selector=";".join(ids),
        provider_metadata=StructuredEvidence(value=metadata))


def _verify_registered_expiry(deployment: ProviderDeploymentInput, options: AzureRuntimeOptions, root: Path,
                              runner: CommandRunner, clock: Callable[[], float], now: datetime) -> None:
    expiry = deployment.expiry.value
    if expiry is None or not expiry.provider_metadata.value:
        raise ValueError("registered expiry metadata is missing")
    m = expiry.provider_metadata.value
    account = m.get("automation_account_id")
    expected_account = f"/subscriptions/{options.subscription_id}/resourceGroups/{_slug(deployment)}/providers/Microsoft.Automation/automationAccounts/{_slug(deployment)}-auto"
    if (account != expected_account or m.get("resource_group") != _slug(deployment)
            or m.get("target_ids") is None or m.get("role_actions") != ROLE_ACTIONS
            or m.get("role_scope") != m.get("resource_group_id")
            or m.get("reservation_id") != deployment.reservation.value.reservation_id.value
            or m.get("source_sha256") != deployment.reservation.value.source_sha256
            or m.get("retry_end_at") != options.retry_end_at.isoformat()
            or m.get("price_input") != options.expiry_cost_input
            or datetime.fromisoformat(m.get("trigger","").replace("Z","+00:00")).astimezone(UTC) != expiry.expires_at.value
            or expiry.registration_id.value != f"{account}/jobSchedules/{m.get('job_schedule_id')}?api-version={AUTOMATION_API}"):
        raise ValueError("expiry account, exact targets or narrow permission evidence is missing")
    expected_ids = [f"/subscriptions/{options.subscription_id}/resourceGroups/{m['resource_group']}/providers/Microsoft.Compute/virtualMachines/{m['resource_group']}-{role}" for role in ("a", "b")]
    if m.get("target_ids") != expected_ids or expiry.target_selector != ";".join(expected_ids):
        raise ValueError("registered expiry target identities do not match the deterministic pair")
    runbook_url = f"{account}/runbooks/{m['runbook']}?api-version={AUTOMATION_API}"
    runbook = json.loads(_command(runner, options, root, _azure_rest("GET", runbook_url), "expiry_runbook_recheck", clock).stdout or "{}")
    if runbook.get("location") != deployment.vm_a.region:
        raise RuntimeError("registered runbook region changed")
    if runbook.get("properties", {}).get("state") != "Published":
        raise RuntimeError("registered runbook is no longer Published")
    if runbook.get("properties", {}).get("runbookType") != "PowerShell" or runbook.get("properties", {}).get("runtimeEnvironment") not in (None, "", "PowerShell-5.1"):
        raise RuntimeError("registered runbook runtime changed")
    account_read=json.loads(_command(runner,options,root,_azure_rest("GET",f"{account}?api-version={AUTOMATION_API}"),"expiry_account_recheck",clock).stdout)
    if account_read.get("location") != deployment.vm_a.region:
        raise RuntimeError("registered Automation account region changed")
    if account_read.get("identity",{}).get("type") != "SystemAssigned" or account_read.get("identity",{}).get("principalId","").lower() != str(m.get("managed_identity_principal_id","")).lower():
        raise RuntimeError("registered Automation system-assigned identity changed")
    content = _command(runner, options, root, ["az", "rest", "--method", "GET", "--url", f"{account}/runbooks/{m['runbook']}/content?api-version={AUTOMATION_API}", "--output", "tsv"], "expiry_content_recheck", clock)
    if not _azure_tsv_content_matches(content.stdout,m.get("published_content_sha256","")):
        raise RuntimeError("registered runbook content changed after expiry registration")
    schedule_url = f"{account}/schedules/{m['schedule']}?api-version={AUTOMATION_API}"
    schedule = json.loads(_command(runner, options, root, _azure_rest("GET", schedule_url), "expiry_schedule_recheck", clock).stdout or "{}")
    props = schedule.get("properties", {})
    if (not _azure_schedule_matches(props, expiry.expires_at.value)
            or props.get("runOn") not in (None, "")):
        raise RuntimeError("registered expiry schedule is disabled or has changed")
    job_url = f"{account}/jobSchedules/{m['job_schedule_id']}?api-version={AUTOMATION_API}"
    job = json.loads(_command(runner, options, root, _azure_rest("GET", job_url), "expiry_binding_recheck", clock).stdout or "{}")
    if (job.get("id","").lower()!=job_url.split("?",1)[0].lower()
            or job.get("properties",{}).get("runOn") not in (None, "")
            or job.get("properties", {}).get("parameters", {}) != m.get("binding_readback")
            or job.get("properties", {}).get("runbook", {}).get("name") != m.get("runbook")
            or job.get("properties", {}).get("schedule", {}).get("name") != m.get("schedule")):
        raise RuntimeError("registered expiry job binding changed")
    _, assignments = _role_assignments_at_scope(runner, options, root,
        m["resource_group_id"], "expiry_role_recheck", clock)
    assignment = next((x for x in assignments if x.get("id", "").lower() == m.get("role_assignment_id", "").lower()), None)
    if (assignment is None or assignment.get("principalId", "").lower() != m.get("managed_identity_principal_id", "").lower()
            or assignment.get("scope","").lower()!=m.get("resource_group_id","").lower()
            or assignment.get("roleDefinitionId","").lower()!=m.get("role_definition_id","").lower()):
        raise RuntimeError("registered expiry role assignment is absent or changed")
    roles = json.loads(_command(runner, options, root, ["az", "role", "definition", "list", "--name", m["resource_group"]+"-deallocate-only", "--output", "json"], "expiry_role_definition_recheck", clock).stdout or "[]")
    role = next((x for x in roles if x.get("id", "").lower() == m.get("role_definition_id", "").lower()), None)
    permissions = role.get("permissions", []) if role else []
    if (role is None or [scope.lower() for scope in role.get("assignableScopes",[])] != [m["resource_group_id"].lower()] or len(permissions) != 1
            or sorted(permissions[0].get("actions", [])) != sorted(ROLE_ACTIONS)
            or permissions[0].get("notActions", []) or permissions[0].get("dataActions", [])
            or permissions[0].get("notDataActions", [])):
        raise RuntimeError("registered expiry custom role is absent or broader than deallocate-only")
    module_url = f"{account}/modules/Az.Accounts?api-version={AUTOMATION_API}"
    module = json.loads(_command(runner, options, root, _azure_rest("GET", module_url), "expiry_module_recheck", clock).stdout or "{}")
    module_props = module.get("properties") if isinstance(module, dict) else None
    expected_module = m.get("module") if isinstance(m.get("module"), dict) else {}
    if (not isinstance(module_props, dict)
            or module_props.get("provisioningState") != "Succeeded"
            or not isinstance(module_props.get("version"), str)
            or not module_props["version"].strip()
            or module_props["version"] != expected_module.get("version")
            or expected_module.get("name") != "Az.Accounts"):
        raise RuntimeError("registered Az.Accounts Automation module is absent, not ready, or changed")
    if expiry.expires_at.value <= now or (expiry.expires_at.value - now).total_seconds() < options.post_registration_workflow_seconds:
        raise ValueError("registered expiry no longer covers the complete remaining workflow")


def register_expiry(deployment: ProviderDeploymentInput, request: ExpiryRequest, options: AzureRuntimeOptions,
                    runner: CommandRunner, *, clock: Callable[[], float], utc_now: Callable[[], datetime],
                    sleep: Callable[[float], None] = time.sleep) -> ExpiryEvidence:
    """Register expiry and convert post-mutation failures into durable failed evidence."""
    now = utc_now().astimezone(UTC)
    mono = clock()
    _validate_request(deployment, options, now, mono)
    if date.fromisoformat(str(options.expiry_cost_input["date"])) > now.date():
        raise ValueError("Azure expiry price input date cannot be in the future")
    reservation = deployment.reservation.value
    requested_trigger = request.expires_at.value
    trigger = _azure_schedule_trigger(requested_trigger) if requested_trigger is not None else None
    if request.provider != Provider.AZURE or request.action != "deallocate" or request.run_id != deployment.run_id or request.deployment_id != deployment.deployment_id:
        raise ValueError("expiry request identity/action mismatch")
    if (requested_trigger is None or trigger is None or requested_trigger >= reservation.expires_at.value
            or requested_trigger >= options.retry_end_at or trigger >= reservation.expires_at.value
            or trigger >= options.retry_end_at):
        raise ValueError("expiry trigger must precede retry end and reservation cap")
    if deployment.admission.timing_profile:
        expected_requested = reservation.cap_started_at.value + timedelta(seconds=1680)
        if (reservation.expires_at.value - reservation.cap_started_at.value != timedelta(seconds=1800)
                or options.retry_end_at != reservation.expires_at.value
                or requested_trigger != expected_requested
                or trigger != _azure_schedule_trigger(expected_requested)):
            raise ValueError("Azure EXP-901 profile requires its exact 1680-second requested STOP and floored trigger")
    if trigger <= now + timedelta(seconds=300):
        raise ValueError("Automation schedule requires at least 300 seconds lead")
    if (options.retry_end_at-trigger).total_seconds() > float(options.expiry_cost_input["job_minutes_bound"])*60:
        raise ValueError("bounded Automation job minutes do not cover the configured retry window")
    if reservation.cap_started_at.value + timedelta(seconds=options.complete_workflow_seconds) > trigger:
        raise ValueError("complete workflow bound exceeds actual expiry trigger")
    if trigger <= now + timedelta(seconds=options.post_registration_workflow_seconds):
        raise ValueError("complete post-registration workflow must fit before trigger")
    if request.reservation_id.value != reservation.reservation_id.value:
        raise ValueError("expiry reservation ID mismatch")
    _, ids = _validate_request(deployment, options, now, mono)
    if request.target_selector != ";".join(ids):
        raise ValueError("expiry target selector must name exactly A and B")
    try:
        registration_options = options
        if deployment.admission.workflow_profile:
            allowance = options.profile_allowance
            if allowance is None:
                raise ValueError("profile Azure registration requires the source-bound allowance vector")
            cap_start = options.cap_started_monotonic
            if cap_start is None:
                raise ValueError("profile Azure registration requires the original monotonic cap start")
            deadline = cap_start + float(allowance.get("registration_seconds", 0.0))
            deadline = min(deadline, options.cap_deadline_monotonic)
            if trigger is not None:
                stop_deadline = cap_start + (trigger - reservation.cap_started_at.value).total_seconds()
                deadline = min(deadline, stop_deadline)
            registration_options = options.model_copy(update={"workflow_stage_deadline_monotonic": deadline})
        evidence = _register_expiry_impl(deployment, request, registration_options, runner, clock=clock, utc_now=utc_now, sleep=sleep)
        if evidence.state == "registered" and evidence.expires_at.value is not None:
            _bind_profile_stop(deployment, options, evidence.expires_at.value)
        return evidence
    except Exception as exc:
        root = options.attempt_root
        journal = root / "recovery.json"
        if journal.is_file() and not journal.is_symlink():
            state = json.loads(journal.read_text(encoding="utf-8"))
            state["expiry_state"] = "failed"
            state.setdefault("failures", []).append({"category":"expiry_registration","message":_redact(options,exc)[:1000],"occurred_at":utc_now().astimezone(UTC).isoformat()})
            state.setdefault("expiry", {}).update({"state":"failed","failure":_redact(options,exc)[:1000]})
            _atomic_private(journal, state)
        failure = FailureEvidence(category="expiry_registration",message=_redact(options,exc)[:1000],occurred_at=utc_now().astimezone(UTC),details={"cleanup_required":journal.is_file()})
        return ExpiryEvidence(schema_version=2,run_id=deployment.run_id,deployment_id=deployment.deployment_id,provider=Provider.AZURE,
            action="deallocate",state="failed",registration_id=Evidence(unavailable_reason="registration was not fully confirmed"),
            registered_at=Evidence(unavailable_reason="registration was not fully confirmed"),expires_at=Evidence(value=trigger),
            target_selector=request.target_selector,provider_metadata=StructuredEvidence(value={"mechanism":"azure_automation_once_deallocate","cleanup_required":journal.is_file()}),failure=failure)


def _tf(root: Path, options: AzureRuntimeOptions, runner: CommandRunner, clock: Callable[[], float],
        workdir: str, *args: str, action: str,
        apply_timeout_seconds: float | None = None,
        before_apply: Callable[[], float] | None = None) -> CommandResult:
    state = root / ("expiry.tfstate" if workdir.endswith("/expiry") else "pair.tfstate")
    source_root = Path(__file__).resolve().parents[2] / workdir
    argv = ["terraform", f"-chdir={source_root}", *args]
    if args and args[0] in {"apply", "destroy", "output"}:
        argv += [f"-state={state}"]
    if args and args[0] == "init":
        argv += [f"-backend-config=path={state}", "-lockfile=readonly"]
    return _command(runner, options, root, argv, action, clock,
        apply_timeout_seconds=apply_timeout_seconds, before_apply=before_apply)


def _key_material(text: str) -> str:
    value = next((line.strip() for line in text.splitlines() if _KEY.fullmatch(line.strip())), "")
    if not value: raise ValueError("authenticated guest returned malformed SSH public host key")
    algorithm, encoded = value.split()[:2]
    try:
        blob = base64.b64decode(encoded, validate=True)
        size = struct.unpack(">I", blob[:4])[0]
        if len(blob) < 4 + size or blob[4:4 + size].decode("ascii") != algorithm:
            raise ValueError
        if algorithm == "ssh-ed25519" and len(blob) != 4 + size + 4 + 32:
            raise ValueError
        if algorithm == "ecdsa-sha2-nistp256" and len(blob) <= 4 + size + 8:
            raise ValueError
        if algorithm == "ssh-rsa" and len(blob) <= 4 + size + 8:
            raise ValueError
    except Exception as exc:
        raise ValueError("authenticated guest returned malformed SSH public host key") from exc
    return algorithm + " " + encoded


def _validate_image_metadata(options: AzureRuntimeOptions, runner: CommandRunner, root: Path,
                             clock: Callable[[],float], region: str) -> dict[str,Any]:
    image=options.selected_image
    if image.startswith("/"):
        version_id=image
        definition_id=image.split("/versions/")[0]
        definition=json.loads(_command(runner,options,root,["az","sig","image-definition","show","--ids",definition_id,"--output","json"],"image_preflight",clock).stdout)
        version=json.loads(_command(runner,options,root,["az","sig","image-version","show","--ids",version_id,"--output","json"],"image_version_preflight",clock).stdout)
        definition_props=definition.get("properties",{})
        identifier=definition.get("identifier",definition_props.get("identifier",{}))
        architecture=definition.get("architecture",definition_props.get("architecture"))
        publisher=identifier.get("publisher")
        offer=identifier.get("offer","")
        sku=identifier.get("sku","")
        actual={"id":version.get("id"),"architecture":architecture,"publisher":publisher,"offer":offer,"sku":sku,
                "version":version.get("name") or version_id.rstrip("/").split("/")[-1],"source":"shared_gallery"}
    else:
        result=json.loads(_command(runner,options,root,["az","vm","image","show","--location",region,"--urn",image,"--output","json"],"image_preflight",clock).stdout)
        image_id=result.get("id") if isinstance(result,dict) else None
        image_name=result.get("name") if isinstance(result,dict) else None
        if not isinstance(image_id,str) or not isinstance(image_name,str):
            raise ValueError("Marketplace image response lacks Compute image identity")
        match=re.search(r"/publishers/([^/]+)/artifacttypes/vmimage/offers/([^/]+)/skus/([^/]+)/versions/([^/]+)$",image_id,re.IGNORECASE)
        if match is None:
            raise ValueError("Marketplace image resource ID does not contain the requested Compute identity")
        publisher,offer,sku,version=match.groups()
        os_metadata=result.get("osDiskImage")
        actual={"id":image_id,"architecture":result.get("architecture"),"operating_system":os_metadata.get("operatingSystem") if isinstance(os_metadata,dict) else None,
                "publisher":publisher,"offer":offer,"sku":sku,"version":image_name,"source":"marketplace"}
        if (str(result.get("location", "")).lower() != region.lower()
                or f"/locations/{region.lower()}/" not in image_id.lower()):
            raise ValueError("Marketplace image response is for a different region")
        if image_name.lower()!=version.lower():
            raise ValueError("Marketplace image name and resource version identity disagree")
    if str(actual.get("architecture") or "").lower() not in {"x64","amd64"} or str(actual.get("publisher") or "").lower()!="canonical" or not re.search(r"(?i)(ubuntu-24_04|ubuntu-24\.04|ubuntu2404|noble)",str(actual.get("offer",""))+" "+str(actual.get("sku",""))):
        raise ValueError("selected Azure image metadata does not prove x64 Canonical Ubuntu 24.04")
    if ((not image.startswith("/") and actual.get("operating_system") != "Linux")
            or (image.startswith("/") and actual.get("operating_system") not in (None, "Linux"))):
        raise ValueError("selected Azure image metadata does not prove a Linux operating system")
    if image.startswith("/") and str(actual.get("id","")).lower()!=image.lower():
        raise ValueError("selected shared gallery image version identity mismatch")
    if not image.startswith("/") and (actual.get("publisher","").lower(),actual.get("offer","").lower(),actual.get("sku","").lower(),str(actual.get("version","")))!=tuple(x.lower() for x in image.split(":")):
        raise ValueError("selected Marketplace image identity mismatch")
    return actual


def _observe_vm_image(options: AzureRuntimeOptions, runner: CommandRunner, root: Path,
                      vm_id: str, clock: Callable[[],float], requested: str, region: str, zone: str, admin_user: str) -> str:
    payload=json.loads(_command(runner,options,root,["az","vm","show","--ids",vm_id,"--output","json"],"vm_image_observation",clock).stdout)
    if (str(payload.get("id", "")).lower() != vm_id.lower() or payload.get("location") != region
            or payload.get("zones") != [zone]
            or payload.get("hardwareProfile", {}).get("vmSize") != "Standard_D4s_v5"
            or payload.get("osProfile", {}).get("adminUsername") != admin_user):
        raise ValueError("authenticated Azure VM identity, locality or shape differs from the request")
    storage=payload.get("storageProfile",payload.get("properties",{}).get("storageProfile",{}))
    ref=storage.get("imageReference",{})
    if requested.startswith("/"):
        observed=ref.get("id")
        if not isinstance(observed,str) or observed.lower()!=requested.lower():
            raise ValueError("created VM shared image version differs from the requested image")
        return observed
    parts=[ref.get(k) for k in ("publisher","offer","sku","exactVersion")]
    if not all(isinstance(x,str) and x for x in parts):
        raise ValueError("created VM image reference lacks observed Marketplace identity and exact version")
    wanted=requested.split(":")
    if [x.lower() for x in parts[:3]] != [x.lower() for x in wanted[:3]] or parts[3].lower()!=wanted[3].lower():
        raise ValueError("created VM Marketplace image identity or version differs from the request")
    return ":".join(parts)


def provision(deployment: ProviderDeploymentInput, options: AzureRuntimeOptions, runner: CommandRunner, *,
              clock: Callable[[],float], utc_now: Callable[[],datetime]) -> AzureProviderResult:
    provision_stage_seconds = float(deployment.provisioning_timeout_seconds or options.command_timeout_seconds)
    stage_started_monotonic = clock()
    provision_deadline_monotonic = stage_started_monotonic + provision_stage_seconds
    if deployment.admission.workflow_profile:
        allowance = options.profile_allowance
        if allowance is None:
            raise ValueError("profile Azure provisioning requires the source-bound allowance vector")
        cap_start = options.cap_started_monotonic
        if cap_start is None:
            raise ValueError("profile Azure provisioning requires the original monotonic cap start")
        stage_start = cap_start + float(allowance.get("registration_seconds", 0.0))
        provision_deadline_monotonic = min(provision_deadline_monotonic,
            stage_start + float(allowance.get("provision_seconds", 0.0)), options.cap_deadline_monotonic)
        stop = deployment.expiry.value.expires_at.value
        reservation_start = deployment.reservation.value.cap_started_at.value
        provision_deadline_monotonic = min(provision_deadline_monotonic,
            cap_start + (stop - reservation_start).total_seconds())
        # All Azure operations in provisioning inherit the original cumulative
        # stage cutoff. Keep the source overall cap untouched for its bindings.
        options = options.model_copy(update={
            "workflow_stage_deadline_monotonic": provision_deadline_monotonic})
    # Keep the stage cutoff anchored to its original first monotonic sample,
    # then validate one conservative UTC/monotonic pair. Sampling UTC first
    # and monotonic second prevents elapsed Python work from moving the
    # projected UTC cap later than the reserved cap.
    now=utc_now().astimezone(UTC)
    mono=clock()
    _require_profile_timing_anchor(deployment, options, require_stop=True)
    name,ids=_validate_request(deployment,options,now,mono,require_registered=True)
    if date.fromisoformat(str(options.expiry_cost_input["date"])) > now.date():
        raise ValueError("Azure expiry price input date cannot be in the future")
    root,journal=_workspace(options,deployment); state=json.loads(journal.read_text()); failures=[]; results=[]; apply=None; outputs=None; valid_outputs={}
    variables=build_pair_inputs(deployment,options)
    pair_vars=_persist_tfvars(root,journal,"pair",variables)
    try:
        _tf(root,options,runner,clock,"terraform/azure/pair","init","-input=false",action="pair_init")
        # Expiry readbacks and pair initialization may consume meaningful cap time.
        _verify_registered_expiry(deployment,options,root,runner,clock,now)
        fresh_utc=utc_now().astimezone(UTC); fresh_mono=clock()
        _validate_request(deployment,options,fresh_utc,fresh_mono,require_registered=True)
        if provision_deadline_monotonic-fresh_mono <= 0:
            raise TimeoutError("original Azure provision-stage deadline elapsed before pair creation")
        if deployment.expiry.value.expires_at.value - fresh_utc < timedelta(seconds=options.post_registration_workflow_seconds):
            raise TimeoutError("remaining complete workflow no longer fits before the registered trigger")
        if options.cap_deadline_monotonic-fresh_mono < options.post_registration_workflow_seconds:
            raise TimeoutError("remaining complete workflow no longer fits within the original monotonic cap")
        image_probe=_validate_image_metadata(options,runner,root,clock,deployment.vm_a.region)
        state=json.loads(journal.read_text(encoding="utf-8")); state["creation_may_have_started"]=True; state["pair_state"]="creating"; _atomic_private(journal,state)
        args=["apply","-input=false",f"-var-file={pair_vars}"]
        preapply_utc=utc_now().astimezone(UTC); preapply_mono=clock()
        if (provision_deadline_monotonic-preapply_mono <= 0
                or deployment.expiry.value.expires_at.value-preapply_utc < timedelta(seconds=options.post_registration_workflow_seconds)
                or options.cap_deadline_monotonic-preapply_mono < options.post_registration_workflow_seconds):
            state=json.loads(journal.read_text(encoding="utf-8")); state["creation_may_have_started"]=False; state["pair_state"]="not_started"; _atomic_private(journal,state)
            raise TimeoutError("complete workflow no longer fits immediately before pair apply")
        review_slack = min(options.cap_deadline_monotonic-preapply_mono,
            (deployment.expiry.value.expires_at.value-preapply_utc).total_seconds()) - options.post_registration_workflow_seconds
        if review_slack <= 0: raise TimeoutError("pair plan review no longer fits before the workflow allowance")
        review_deadline = min(preapply_mono+review_slack, provision_deadline_monotonic)
        if review_deadline <= preapply_mono:
            raise TimeoutError("pair plan review no longer fits within the original provision-stage allowance")
        review_options = options.model_copy(update={"workflow_stage_deadline_monotonic":review_deadline})
        if provision_stage_seconds > options.post_registration_workflow_seconds:
            raise TimeoutError("pair provision stage exceeds the post-registration workflow allowance")
        # The F11 allowance reserves the provision component inside the full
        # post-registration remainder. Runtime construction clamps the
        # deployment timeout to that component; preserve any stricter config.
        downstream_reserve = options.post_registration_workflow_seconds - provision_stage_seconds

        def apply_budget_after_review() -> float:
            reviewed_utc = utc_now().astimezone(UTC)
            reviewed_mono = clock()
            trigger_remaining = (deployment.expiry.value.expires_at.value - reviewed_utc).total_seconds()
            cap_remaining = options.cap_deadline_monotonic - reviewed_mono
            stage_remaining = provision_deadline_monotonic - reviewed_mono
            if trigger_remaining < options.post_registration_workflow_seconds or cap_remaining < options.post_registration_workflow_seconds:
                raise TimeoutError("complete workflow no longer fits immediately after pair plan review")
            if stage_remaining <= 0:
                raise TimeoutError("original Azure provision-stage deadline elapsed during pair plan review")
            remaining = min(options.command_timeout_seconds, stage_remaining,
                cap_remaining - downstream_reserve, trigger_remaining - downstream_reserve)
            if remaining <= 0:
                raise TimeoutError("pair apply has no remaining stage budget after saved-plan review")
            return remaining

        apply=_tf(root,review_options,runner,clock,"terraform/azure/pair",*args,action="pair_apply",
            apply_timeout_seconds=min(options.command_timeout_seconds, provision_deadline_monotonic-preapply_mono),
            before_apply=apply_budget_after_review); results.append(apply.durable_view())
        post_apply_options=options.model_copy(update={"workflow_stage_deadline_monotonic":provision_deadline_monotonic})
        if clock() >= provision_deadline_monotonic:
            raise TimeoutError("original Azure provision-stage deadline elapsed before pair output and host trust")
        out=_tf(root,post_apply_options,runner,clock,"terraform/azure/pair","output","-json",action="pair_output")
        outputs=json.loads(out.stdout)
        observed_images={}
        vms=[]; key_lines=[]
        for role in ("a","b"):
            data=_validate_vm_output_data(deployment,options,role,outputs["vm_"+role]["value"],ids[0 if role=="a" else 1])
            data["image"]=_observe_vm_image(post_apply_options,runner,root,ids[0 if role=="a" else 1],clock,options.selected_image,deployment.vm_a.region if role=="a" else deployment.vm_b.region,
                deployment.vm_a.zone if role=="a" else deployment.vm_b.zone,
                deployment.vm_a.connection_user if role=="a" else deployment.vm_b.connection_user)
            observed_images[role]=data["image"]
            valid_outputs[role]=data
            nonce=secrets.token_urlsafe(24)
            script=f"printf 'CNB-AZ-HOSTKEY:{nonce}\\n'; cat /etc/ssh/ssh_host_ed25519_key.pub"
            command=_command(runner,post_apply_options,root,["az","vm","run-command","invoke","--ids",ids[0 if role=="a" else 1],"--command-id","RunShellScript","--scripts",script,"--output","json"],"host_key_identity",clock)
            if clock() >= provision_deadline_monotonic:
                raise TimeoutError("original Azure provision-stage deadline elapsed during host trust")
            action=json.loads(command.stdout)
            statuses=action.get("value",[])
            if not isinstance(statuses,list) or not statuses or any(not isinstance(item,dict) for item in statuses):
                raise ValueError("authenticated Azure Run Command did not report successful completion")
            codes=[str(item.get("code","")).lower() for item in statuses]
            legacy_stdout="componentstatus/stdout/succeeded"
            legacy_stderr="componentstatus/stderr/succeeded"
            consolidated_code="provisioningstate/succeeded"
            marker=f"CNB-AZ-HOSTKEY:{nonce}"
            if sum(str(item.get("message","")).count(marker) for item in statuses)!=1:
                raise ValueError("authenticated Azure Run Command response nonce is missing, stale, or ambiguous")
            if any(str(item.get("level","")).lower()=="error" or code.endswith("/failed")
                    for item,code in zip(statuses,codes)):
                raise ValueError("authenticated Azure Run Command did not report successful completion")
            if consolidated_code in codes:
                if len(statuses)!=1 or codes[0]!=consolidated_code:
                    raise ValueError("authenticated Azure Run Command returned mixed or ambiguous statuses")
                status=statuses[0]
                consolidated=True
            else:
                if (codes.count(legacy_stdout)!=1 or any(code not in {legacy_stdout,legacy_stderr} for code in codes)
                        or codes.count(legacy_stderr)>1):
                    raise ValueError("authenticated Azure Run Command did not report successful completion")
                status=statuses[codes.index(legacy_stdout)]
                stderr_status=[item for item,code in zip(statuses,codes) if code==legacy_stderr]
                if stderr_status and str(stderr_status[0].get("message","" )).strip():
                    raise ValueError("authenticated Azure Run Command stderr is not empty")
                consolidated=False
            script_out=status.get("message","")
            if not isinstance(script_out,str):
                raise ValueError("authenticated Azure Run Command stdout is malformed")
            if script_out.count(marker)!=1:
                raise ValueError("authenticated Azure Run Command response nonce is missing, stale, or ambiguous")
            if "[stdout]" in script_out or "[stderr]" in script_out:
                if (script_out.count("[stdout]")!=1 or script_out.count("[stderr]")!=1
                        or script_out.index("[stdout]")>script_out.index("[stderr]")):
                    raise ValueError("authenticated Azure Run Command output framing is malformed or ambiguous")
                stdout_start=script_out.index("[stdout]")+len("[stdout]")
                stderr_start=script_out.index("[stderr]")
                if script_out[stderr_start+len("[stderr]"):].strip():
                    raise ValueError("authenticated Azure Run Command stderr or mixed output is not empty")
                script_out=script_out[stdout_start:stderr_start]
            elif consolidated:
                raise ValueError("authenticated Azure Run Command consolidated output framing is missing")
            if script_out.count(marker)!=1:
                raise ValueError("authenticated Azure Run Command response nonce is missing, stale, or ambiguous")
            suffix=script_out.split(marker,1)[1]
            parsed_keys=[line.strip() for line in suffix.splitlines() if _KEY.fullmatch(line.strip())]
            if len(parsed_keys)!=1:
                raise ValueError("authenticated Azure Run Command stdout has malformed or ambiguous host key material")
            key=_key_material(parsed_keys[0])
            key_lines.append(f"{data['public_ipv4']} {key}")
            vms.append(_vm_output(deployment,role,data,key,options))
        if clock() >= provision_deadline_monotonic:
            raise TimeoutError("original Azure provision-stage deadline elapsed before pair trust materialization")
        known=root/"known_hosts"
        if known.exists(): raise FileExistsError("known_hosts already exists; refusing overwrite")
        with known.open("x",encoding="utf-8") as f: f.write("\n".join(key_lines)+"\n")
        os.chmod(known,0o600)
        state=json.loads(journal.read_text(encoding="utf-8"))
        state["pair_state"]="created"; state["known_hosts_reference"]=str(known); _atomic_private(journal,state)
        if clock() >= provision_deadline_monotonic:
            raise TimeoutError("original Azure provision-stage deadline elapsed before pair output materialization")
        started=apply.started_at
        output=ProviderDeploymentOutput(schema_version=2,identity=_identity(deployment),run_id=deployment.run_id,deployment_id=deployment.deployment_id,
            mode=deployment.mode,provider=Provider.AZURE,scenario=Scenario(deployment.scenario),apply_action_id=apply.action_id,
            started_at=started,finished_at=out.finished_at,vm_a=vms[0],vm_b=vms[1],
            provider_metadata=StructuredEvidence(value={"purchase":"Regular","resource_group":name,"scenario":Scenario(deployment.scenario).value,"image_preflight":image_probe,"observed_vm_images":observed_images}),
            documented_network_limit=StructuredEvidence(value={"value":12500,"unit":"Mbps","kind":"maximum_aggregate","source":"Azure Dsv5 documentation","not_measured":True}),
            expiry=deployment.expiry)
        _write_output_artifact(root,output)
        return AzureProviderResult(deployment=output,expiry=deployment.expiry.value,actions=_journal_actions(journal),failures=[],recovery_reference=str(journal),cleanup_required=True)
    except Exception as exc:
        failures.append(FailureEvidence(category="azure_provision",message=_redact(options,exc)[:1000],occurred_at=utc_now(),details={"primary":True}))
        state=json.loads(journal.read_text()); state["failures"].append({"category":"azure_provision","message":_redact(options,exc)[:1000]}); _atomic_private(journal,state)
        expiry=deployment.expiry.value
        partial=None
        if apply is not None and valid_outputs:
            reason=_redact(options,exc)
            vm_a=_partial_vm_output("a",valid_outputs["a"],reason) if "a" in valid_outputs else _partial_empty_vm("a",reason)
            vm_b=_partial_vm_output("b",valid_outputs["b"],reason) if "b" in valid_outputs else _partial_empty_vm("b",reason)
            partial=ProviderDeploymentOutput(schema_version=2,identity=_identity(deployment),run_id=deployment.run_id,deployment_id=deployment.deployment_id,
                mode=deployment.mode,provider=Provider.AZURE,scenario=Scenario(deployment.scenario),apply_action_id=apply.action_id,
                started_at=apply.started_at,finished_at=max(apply.finished_at,utc_now().astimezone(UTC)),vm_a=vm_a,vm_b=vm_b,
                provider_metadata=StructuredEvidence(value={"purchase":"Regular","resource_group":name,"partial_failure":True}),
                documented_network_limit=StructuredEvidence(value={"value":12500,"unit":"Mbps","kind":"maximum_aggregate","source":"Azure Dsv5 documentation","not_measured":True}),expiry=deployment.expiry)
        return AzureProviderResult(deployment=partial,expiry=expiry,actions=_journal_actions(journal),failures=failures,recovery_reference=str(journal),cleanup_required=True)


def _identity(d: ProviderDeploymentInput) -> IdentityContext:
    return IdentityContext(experiment_id=d.experiment_id,config_id=d.config_id,campaign_id=d.campaign_id,
        observation_id=d.observation_id,run_id=d.run_id,deployment_id=d.deployment_id,provider=Provider.AZURE,replaces_run_id=d.replaces_run_id)


def _vm_output(d: ProviderDeploymentInput, role: str, data: dict[str,Any], key: str, o: AzureRuntimeOptions) -> ProviderVmOutput:
    vmrole=VmRole.VM_A if role=="a" else VmRole.VM_B
    conn=RemoteConnectionData(role=vmrole,host=data["public_ipv4"],user=d.vm_a.connection_user if role=="a" else d.vm_b.connection_user,
        authentication_reference=o.authentication_reference,host_key_reference=str(o.attempt_root/"known_hosts"),readiness_expectations=["ssh_strict_host_key_checking"])
    return ProviderVmOutput(role=vmrole,resource_id=data["id"],private_ipv4=data["private_ipv4"],image_identity=data["image"],actual_shape=data["size"],
        region=data["region"],zone=str(data["zone"]),placement_metadata=StructuredEvidence(value={"ppg":data.get("ppg") or "none","accelerated_networking":data["accelerated_networking"]}),
        connection=conn,public_ipv4=Evidence(value=data["public_ipv4"]),physical_zone_id=StringEvidence(unavailable_reason="Azure logical zones do not establish cross-subscription physical identity"))


def _validate_vm_output_data(deployment: ProviderDeploymentInput, options: AzureRuntimeOptions, role: str,
                             data: object, expected_id: str) -> dict[str, Any]:
    if not isinstance(data,dict): raise ValueError(f"Terraform output for VM {role.upper()} is missing or malformed")
    intent=deployment.vm_a if role=="a" else deployment.vm_b
    expected_private="10.71.0.4" if role=="b" and intent.region=="uksouth" else ("10.70.0.4" if role=="a" else "10.70.0.5")
    if not isinstance(data.get("id"),str) or data["id"].lower()!=expected_id.lower():
        raise ValueError(f"Terraform output VM {role.upper()} resource identity mismatch")
    if data.get("size")!="Standard_D4s_v5" or data.get("region")!=intent.region or str(data.get("zone"))!=intent.zone:
        raise ValueError(f"Terraform output VM {role.upper()} shape or locality mismatch")
    if data.get("requested_image")!=options.selected_image or data.get("accelerated_networking") is not True:
        raise ValueError(f"Terraform output VM {role.upper()} image or Accelerated Networking evidence mismatch")
    expected_ppg=deployment.placement.name if deployment.scenario==Scenario.PLACEMENT_OPTIMIZATION else None
    if data.get("ppg")!=expected_ppg:
        raise ValueError(f"Terraform output VM {role.upper()} placement evidence mismatch")
    private=ipaddress.ip_address(data.get("private_ipv4", ""))
    public=ipaddress.ip_address(data.get("public_ipv4", ""))
    if private.version!=4 or not private.is_private or str(private)!=expected_private:
        raise ValueError(f"Terraform output VM {role.upper()} private address mismatch")
    if public.version!=4 or public.is_private or public.is_reserved or public.is_loopback or public.is_multicast:
        raise ValueError(f"Terraform output VM {role.upper()} public address is invalid")
    return data


def _partial_vm_output(role: str, data: dict[str,Any], reason: str) -> ProviderVmOutput:
    vmrole=VmRole.VM_A if role=="a" else VmRole.VM_B
    resource_id=data["id"]
    public=data.get("public_ipv4")
    return ProviderVmOutput(role=vmrole,resource_id=resource_id,private_ipv4=data.get("private_ipv4"),image_identity=data.get("image"),
        actual_shape=data.get("size"),region=data.get("region"),zone=str(data["zone"]) if data.get("zone") is not None else None,
        placement_metadata=StructuredEvidence(value={"ppg":data.get("ppg"),"accelerated_networking":data.get("accelerated_networking")}),
        connection=None,unavailable=StructuredEvidence(value={"reason":reason}),
        public_ipv4=Evidence(value=public) if public else Evidence(unavailable_reason="public IPv4 was not returned"))


def _partial_empty_vm(role: str, reason: str) -> ProviderVmOutput:
    vmrole=VmRole.VM_A if role=="a" else VmRole.VM_B
    return ProviderVmOutput(role=vmrole,resource_id=None,private_ipv4=None,image_identity=None,actual_shape=None,region=None,zone=None,
        placement_metadata=StructuredEvidence(unavailable_reason="provider output unavailable"),connection=None,
        unavailable=StructuredEvidence(value={"reason":reason}),public_ipv4=Evidence(unavailable_reason="provider output unavailable"))


def _destroy_impl(deployment: ProviderDeploymentInput, options: AzureRuntimeOptions, runner: CommandRunner, *,
            clock: Callable[[],float], utc_now: Callable[[],datetime]) -> AzureCleanupResult:
    start=utc_now().astimezone(UTC)
    configured_timeout = float(deployment.cleanup_timeout_seconds or options.command_timeout_seconds)
    invocation_seconds=(min(300.0, configured_timeout) if deployment.admission.timing_profile
        else min(660.0, configured_timeout) if deployment.admission.workflow_profile else configured_timeout)
    invocation_start=float(clock())
    invocation_deadline, timing_state, timing_note = _begin_profile_cleanup(deployment, options,
        clock=clock, utc_now=utc_now, invocation_seconds=invocation_seconds)
    if _profile_name(deployment) and timing_state is not None and timing_note is None:
        absence_bound=(timing_state.get("fresh_absence_deadline_monotonic")
            if timing_state.get("absence_started_at") is not None
            else timing_state.get("absence_deadline_monotonic"))
        if isinstance(absence_bound,(int,float)):
            invocation_deadline=min(invocation_start+invocation_seconds,float(absence_bound))
    options = options.model_copy(update={"cap_deadline_monotonic": invocation_deadline,
        "cleanup_timing_active": _profile_name(deployment) is not None and timing_state is not None,
        "cleanup_timing_phase": ("emergency" if timing_note else "routine")
            if _profile_name(deployment) else None})
    root=options.attempt_root; journal=root/"recovery.json"; actions=[]; failures=[]; resources=[]
    if not journal.is_file() or journal.is_symlink():
        failures.append(FailureEvidence(category="recovery_missing",message="private recovery journal unavailable",occurred_at=start))
        if timing_state is not None:
            timing_state = _finish_profile_timing(options, clock=clock, utc_now=utc_now, absence_verified=False) or timing_state
        status = timing_state.get("timing_status") if timing_state else ("unknown" if _profile_name(deployment) else None)
        timing_failure = _timing_failure(status, timing_note or (timing_state or {}).get("timing_failure"), utc_now())
        return AzureCleanupResult(identity=_identity(deployment),cleanup_state=CleanupState.FAILED,attempted_at=start,
            finished_at=utc_now(),failures=failures,launch_blocked=True,timing_status=status,timing_failure=timing_failure)
    state=json.loads(journal.read_text())
    if state.get("run_id")!=deployment.run_id or state.get("deployment_id")!=deployment.deployment_id:
        raise ValueError("recovery journal identity mismatch; refusing cross-attempt cleanup")
    if state.get("config_sha256") != deployment.config_sha256 or state.get("resource_group") != _slug(deployment):
        raise ValueError("recovery journal configuration or namespace mismatch; refusing cleanup")
    expected_vm_ids=[f"/subscriptions/{options.subscription_id}/resourceGroups/{_slug(deployment)}/providers/Microsoft.Compute/virtualMachines/{_slug(deployment)}-{role}" for role in ("a","b")]
    if state.get("vm_ids") != expected_vm_ids:
        raise ValueError("recovery journal contains foreign target identities; refusing cleanup")
    if (_profile_name(deployment) and timing_state
            and timing_state.get("absence_started_at") is not None
            and timing_note is None):
        expected_group=f"/subscriptions/{options.subscription_id}/resourceGroups/{_slug(deployment)}"
        expected_account=expected_group+f"/providers/Microsoft.Automation/automationAccounts/{_slug(deployment)}-auto"
        prereq=state.get("expiry",{})
        assignment=prereq.get("role_assignment_id")
        definition=prereq.get("role_definition_id")
        assignment_prefix=(expected_group+"/providers/Microsoft.Authorization/roleAssignments/").lower()
        definition_prefix=(f"/subscriptions/{options.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/").lower()
        if (prereq.get("automation_account_id") not in (None,expected_account)
                or prereq.get("resource_group_id") not in (None,expected_group)
                or (assignment and (not str(assignment).lower().startswith(assignment_prefix)
                    or not _is_uuid_segment(str(assignment)[len(assignment_prefix):])))
                or (definition and (not str(definition).lower().startswith(definition_prefix)
                    or not _is_uuid_segment(str(definition)[len(definition_prefix):])))):
            raise ValueError("recovery journal contains foreign expiry identities; refusing absence recovery")
        # Routine deletion and its pre-inventory checks completed before the
        # prior process entered the separately budgeted fresh absence window.
        # Resume that read-only proof directly; replaying teardown here would
        # consume the independent 60-second allowance.
        absence_options=options.model_copy(update={"cleanup_timing_phase":"absence"})
        try:
            _verify_profile_absence(deployment,absence_options,runner,root,clock,state,actions)
            resources=[]
            timing_state=_finish_profile_timing(options,clock=clock,utc_now=utc_now,absence_verified=True)
            shutil.rmtree(root)
            status=timing_state.get("timing_status") if timing_state else "unknown"
            failure=_timing_failure(status,(timing_state or {}).get("timing_failure"),utc_now())
            return AzureCleanupResult(identity=_identity(deployment),cleanup_state=CleanupState.SUCCEEDED,
                attempted_at=start,finished_at=utc_now(),actions=actions,failures=[],resources=[],
                launch_blocked=False,timing_status=status,timing_failure=failure)
        except Exception as exc:
            failures.append(FailureEvidence(category="azure_cleanup_absence",message=_redact(options,exc)[:1000],occurred_at=utc_now()))
            state=json.loads(journal.read_text(encoding="utf-8"))
            state["failures"].append({"category":"azure_cleanup_absence","message":_redact(options,exc)[:1000]})
            _atomic_private(journal,state)
            finalized=_finish_profile_timing(options,clock=clock,utc_now=utc_now,absence_verified=False)
            timing_state=finalized
            status=timing_state.get("timing_status") if timing_state else "unknown"
            failure=_timing_failure(status,(timing_state or {}).get("timing_failure"),utc_now())
            return AzureCleanupResult(identity=_identity(deployment),cleanup_state=CleanupState.FAILED,
                attempted_at=start,finished_at=utc_now(),actions=actions,failures=failures,
                resources=[],launch_blocked=True,timing_status=status,timing_failure=failure)
    expected_group = f"/subscriptions/{options.subscription_id}/resourceGroups/{_slug(deployment)}"
    expected_account = expected_group + f"/providers/Microsoft.Automation/automationAccounts/{_slug(deployment)}-auto"
    prereq = state.get("expiry", {})
    role_assignment_id = prereq.get("role_assignment_id")
    role_definition_id = prereq.get("role_definition_id")
    assignment_prefix = (expected_group + "/providers/Microsoft.Authorization/roleAssignments/").lower()
    definition_prefix = (f"/subscriptions/{options.subscription_id}"
        "/providers/Microsoft.Authorization/roleDefinitions/").lower()
    valid_assignment = (isinstance(role_assignment_id, str)
        and role_assignment_id.lower().startswith(assignment_prefix)
        and _is_uuid_segment(role_assignment_id[len(assignment_prefix):]))
    valid_definition = (isinstance(role_definition_id, str)
        and role_definition_id.lower().startswith(definition_prefix)
        and _is_uuid_segment(role_definition_id[len(definition_prefix):]))
    if (prereq.get("automation_account_id") not in (None, expected_account)
            or prereq.get("resource_group_id") not in (None, expected_group)
            or (role_assignment_id and not valid_assignment)
            or (role_definition_id and not valid_definition)
            or (prereq.get("job_schedule_id") is not None
                and not _is_uuid_segment(str(prereq.get("job_schedule_id"))))):
        raise ValueError("recovery journal contains foreign expiry identities; refusing cleanup")
    try:
        # Pair destroy precedes inventory. Expiry remains until direct inventory proves both VMs absent.
        input_names=state.get("terraform_inputs",{})
        if "pair" not in input_names:
            if state.get("creation_may_have_started"): raise RuntimeError("pair Terraform inputs are missing after resource creation may have started")
            pair_vars=_persist_tfvars(root,journal,"pair",build_pair_inputs(deployment,options))
        else:
            # New journals bind original bytes, so future mappings/prices/images cannot
            # regenerate recovery inputs. Legacy journals retain their strict comparison.
            expected_pair = ({"subscription_id":options.subscription_id,"resource_group_name":_slug(deployment),
                "deployment_id":deployment.deployment_id,"config_sha256":deployment.config_sha256,
                "location":deployment.vm_a.region,"region_a":deployment.vm_a.region,"region_b":deployment.vm_b.region}
                if state.get("terraform_input_sha256",{}).get("pair") else build_pair_inputs(deployment,options))
            pair_vars=_recovery_tfvars(root,journal,"pair",expected_pair)
        expected_expiry={"subscription_id":options.subscription_id,"tenant_id":options.tenant_id,
            "resource_group_name":_slug(deployment),"deployment_id":deployment.deployment_id,"location":deployment.vm_a.region}
        if "expiry" not in input_names:
            if state.get("expiry_state") not in (None,"preparing","failed"): raise RuntimeError("expiry Terraform inputs are missing after expiry preparation")
            expiry_vars=_persist_tfvars(root,journal,"expiry",expected_expiry)
        else: expiry_vars=_recovery_tfvars(root,journal,"expiry",expected_expiry)
        actions.append(_safe_cleanup_action(options, _assert_subscription(runner, options, root, clock)))
        _tf(root,options,runner,clock,"terraform/azure/pair","init","-input=false",action="pair_recovery_init")
        result=_tf(root,options,runner,clock,"terraform/azure/pair","destroy","-auto-approve","-input=false",f"-var-file={pair_vars}",action="pair_destroy")
        actions.append(_safe_cleanup_action(options,result))
        parent = _command(runner, options, root, ["az", "group", "exists", "--name", state["resource_group"],
            "--output", "json"], "parent_group_absence", clock)
        parent_exists = json.loads(parent.stdout)
        if parent.stderr.strip() or type(parent_exists) is not bool:
            raise RuntimeError("exact parent group absence/presence is unproved")
        actions.append(_safe_cleanup_action(options, parent))
        for vm_id in expected_vm_ids:
            actions.append(_safe_cleanup_action(options,_prove_vm_absent(runner,options,root,vm_id,clock,
                absent_parent_group=state["resource_group"] if not parent_exists else None)))
        prereq=state.get("expiry",{})
        applied=any(item.get("request",{}).get("action_type")=="expiry_prerequisites"
                    and "apply" in item.get("request",{}).get("argv",[])
                    and item.get("result",{}).get("outcome")=="succeeded" for item in state.get("actions",[]))
        if applied and not prereq.get("role_assignment_id"):
            _tf(root,options,runner,clock,"terraform/azure/expiry","init","-input=false",action="expiry_recovery_identity_init")
            raw=_tf(root,options,runner,clock,"terraform/azure/expiry","output","-json",action="expiry_recovery_identity_output").stdout
            outputs=json.loads(raw)
            recovered={k:outputs.get(k,{}).get("value") for k in ("resource_group_id","automation_account_id","principal_id","role_assignment_id","role_definition_id")}
            if not all(isinstance(value,str) and value for value in recovered.values()):
                raise RuntimeError("interrupted expiry prerequisite identities cannot be recovered completely")
            prereq.update(recovered); state["expiry"]=prereq; _atomic_private(journal,state)
        endpoint = (f"/subscriptions/{options.subscription_id}/resourceGroups/{state['resource_group']}/resources?api-version=2021-04-01"
            if parent_exists else f"/subscriptions/{options.subscription_id}/resources?api-version=2021-04-01")
        found,inventory_evidence=_read_arm_collection(runner,options,root,endpoint,"pair_inventory",clock,("id","type"))
        if not parent_exists:
            prefix = f"/subscriptions/{options.subscription_id}/resourceGroups/{state['resource_group']}/".lower()
            found = [item for item in found if item["id"].lower().startswith(prefix)]
            if found:
                raise RuntimeError("parent absence contradicts subscription resource inventory")
        actions.extend(inventory_evidence)
        metadata=state.get("expiry",{}).get("metadata",{})
        prereq=state.get("expiry",{})
        allowed_expiry_ids={str(prereq.get(k,"")).lower() for k in ("automation_account_id","role_assignment_id","role_definition_id")}
        prereq_account=str(prereq.get("automation_account_id",""))
        for collection,key in (("runbooks","runbook"),("schedules","schedule"),("jobSchedules","job_schedule_id")):
            if prereq_account and prereq.get(key): allowed_expiry_ids.add(f"{prereq_account}/{collection}/{prereq[key]}".lower())
        if metadata:
            account=str(metadata.get("automation_account_id",""))
            for collection,key in (("runbooks","runbook"),("schedules","schedule"),("jobSchedules","job_schedule_id")):
                if account and metadata.get(key): allowed_expiry_ids.add(f"{account}/{collection}/{metadata[key]}".lower())
        residual=[]
        for item in found:
            resource={"id":item.get("id"),"type":item.get("type"),"state":"present","source":inventory_evidence[-1]["action_id"],
                "observed_at":inventory_evidence[-1]["finished_at"]}
            resources.append(resource)
            if str(item.get("id","")).lower() not in allowed_expiry_ids:
                residual.append(item.get("id", "unknown"))
        if residual:
            raise RuntimeError("VMs or pair resources remain after pair destroy: " + ", ".join(str(x) for x in residual))
        account=prereq.get("automation_account_id")
        if account and parent_exists:
            child_info=prereq
            for collection,key in (("jobSchedules","job_schedule_id"),("schedules","schedule"),("runbooks","runbook")):
                child=child_info.get(key)
                if child: _delete_owned_arm_child(runner,options,root,account,collection,str(child),clock,actions)
        _tf(root,options,runner,clock,"terraform/azure/expiry","init","-input=false",action="expiry_recovery_init")
        exp=_tf(root,options,runner,clock,"terraform/azure/expiry","destroy","-auto-approve","-input=false",f"-var-file={expiry_vars}",action="expiry_destroy")
        actions.append(_safe_cleanup_action(options,exp))
        if account and parent_exists:
            account_absence=_command(runner,options,root,["az","resource","show","--ids",account,"--output","json"],
                "automation_account_absence",clock,allow_not_found=True,
                expected_missing_automation_account_id=account)
            actions.append(_safe_cleanup_action(options,account_absence))
            if account_absence.outcome!="failed" or not (_is_exact_not_found(account_absence)
                    or _is_exact_missing_automation_account(account_absence,account)
                    or _is_exact_missing_attempt_resource_group(account_absence,state["resource_group"])):
                raise RuntimeError("Automation account absence is not proven by exact resource identity")
        verify=_command(runner,options,root,["az","group","exists","--name",state["resource_group"],"--output","tsv"],"expiry_inventory",clock)
        actions.append(_safe_cleanup_action(options,verify))
        if verify.stderr.strip() or verify.stdout.strip().lower()!="false": raise RuntimeError("attempt resource group removal is not verified")
        residual_roles,assignments=_role_assignments_at_scope(runner,options,root,
            f"/subscriptions/{options.subscription_id}/resourceGroups/{state['resource_group']}",
            "role_cleanup_inventory",clock)
        if assignments:
            raise RuntimeError("role assignment inventory still contains resources after expiry destroy")
        actions.append(_safe_cleanup_action(options,residual_roles))
        residual_defs=_command(runner,options,root,["az","role","definition","list","--name",_slug(deployment)+"-deallocate-only","--output","json"],"role_definition_cleanup_inventory",clock)
        if residual_defs.stderr.strip() or not residual_defs.stdout.strip(): raise RuntimeError("custom role definition inventory is blank or incomplete")
        definitions=json.loads(residual_defs.stdout)
        if not isinstance(definitions,list): raise RuntimeError("custom role definition inventory is malformed")
        expected_role=str(prereq.get("role_definition_id","")).lower()
        if not expected_role.strip() and definitions:
            raise RuntimeError("custom role definition identity is missing while inventory contains entries")
        for item in definitions:
            if not isinstance(item,dict) or not isinstance(item.get("id"),str) or not item["id"].strip():
                raise RuntimeError("custom role definition inventory contains an unknown or malformed entry")
        if any(item["id"].lower()==expected_role for item in definitions):
            raise RuntimeError("attempt custom role definition remains after expiry destroy")
        if definitions:
            raise RuntimeError("custom role definition inventory contains an unexpected identity")
        actions.append(_safe_cleanup_action(options,residual_defs))
        if _profile_name(deployment):
            routine_options = options.model_copy(update={"cleanup_timing_phase": "routine"})
            if timing_state is not None and timing_state.get("timing_status") == "within_deadline":
                _cleanup_shared_network_watcher(deployment, routine_options, runner, root, clock, utc_now,
                    state, actions)
            phase="emergency"
            if timing_state is not None:
                try:
                    _mark_profile_absence_started(options, clock=clock, utc_now=utc_now)
                    current_timing=_require_profile_timing_anchor(deployment,options,require_stop=False)
                    absence_deadline=(current_timing or {}).get("fresh_absence_deadline_monotonic")
                    if ((current_timing or {}).get("timing_status")=="within_deadline"
                            and isinstance(absence_deadline,(int,float)) and clock()<float(absence_deadline)):
                        phase="absence"
                except (OSError,ValueError,PermissionError,KeyError,TypeError) as exc:
                    timing_state=None
                    _TIMING_OBSERVATION_FAILURE.set(f"profile timing state became unavailable during cleanup ({type(exc).__name__})")
            absence_options = options.model_copy(update={"cleanup_timing_phase": phase})
            _verify_profile_absence(deployment, absence_options, runner, root, clock, state, actions)
            resources = []
            if timing_state is not None:
                timing_state = _finish_profile_timing(options, clock=clock, utc_now=utc_now, absence_verified=True)
        shutil.rmtree(root)
        cleanup=CleanupState.SUCCEEDED; blocked=False
    except Exception as exc:
        failures.append(FailureEvidence(category="azure_cleanup",message=_redact(options,exc)[:1000],occurred_at=utc_now()))
        state=json.loads(journal.read_text(encoding="utf-8"))
        state["failures"].append({"category":"azure_cleanup","message":_redact(options,exc)[:1000]}); _atomic_private(journal,state)
        cleanup=CleanupState.FAILED; blocked=True
    if _profile_name(deployment) and timing_state is not None:
        timing_state = _finish_profile_timing(options, clock=clock, utc_now=utc_now,
            absence_verified=cleanup == CleanupState.SUCCEEDED)
    timing_status = timing_state.get("timing_status") if timing_state else ("unknown" if _profile_name(deployment) else None)
    timing_failure = _timing_failure(timing_status,
        timing_note or (timing_state or {}).get("timing_failure"), utc_now())
    return AzureCleanupResult(identity=_identity(deployment),cleanup_state=cleanup,attempted_at=start,finished_at=utc_now(),
        actions=actions,failures=failures,resources=resources,launch_blocked=blocked,
        timing_status=timing_status,timing_failure=timing_failure)


def destroy(deployment: ProviderDeploymentInput, options: AzureRuntimeOptions, runner: CommandRunner, *,
            clock: Callable[[], float], utc_now: Callable[[], datetime]) -> AzureCleanupResult:
    """Run Azure cleanup with the caller's explicit UTC clock in command tracing."""
    token = _EXPLICIT_UTC_NOW.set(utc_now)
    identity_token = _EXPLICIT_TIMING_IDENTITY.set(_profile_timing_identity(deployment))
    observation_token = _TIMING_OBSERVATION_FAILURE.set(None)
    try:
        result=_destroy_impl(deployment, options, runner, clock=clock, utc_now=utc_now)
        timing_error=_TIMING_OBSERVATION_FAILURE.get()
        if timing_error:
            result=result.model_copy(update={"timing_status":"unknown",
                "timing_failure":_timing_failure("unknown",timing_error,utc_now())})
        return result
    finally:
        _TIMING_OBSERVATION_FAILURE.reset(observation_token)
        _EXPLICIT_TIMING_IDENTITY.reset(identity_token)
        _EXPLICIT_UTC_NOW.reset(token)


def verify_unstarted_absence(deployment: ProviderDeploymentInput, options: AzureRuntimeOptions,
                             runner: CommandRunner, *, clock: Callable[[], float],
                             utc_now: Callable[[], datetime]) -> dict[str, Any]:
    """Prove exact Azure target absence without creating provider recovery state."""
    original_root = options.attempt_root
    if original_root.exists() or original_root.is_symlink():
        raise ValueError("pre-provider Azure absence requires the original attempt root to remain absent")
    now_mono = float(clock())
    now_utc = utc_now().astimezone(UTC)
    allowance = min(660.0, max(1.0, float(options.profile_allowance.get("cleanup_seconds", 60)
        if options.profile_allowance else options.command_timeout_seconds * 4)))
    invocation_options = options.model_copy(update={"attempt_root": Path.cwd(),
        "cap_deadline_monotonic": now_mono + allowance,
        "cleanup_timing_active": False, "cleanup_timing_phase": None})
    root = Path.cwd()
    actions: list[dict[str, Any]] = [_safe_cleanup_action(invocation_options,
        _assert_subscription(runner, invocation_options, root, clock))]
    group = _slug(deployment)
    group_result = _command(runner, invocation_options, root,
        ["az", "group", "exists", "--subscription", options.subscription_id,
         "--name", group, "--output", "json"], "precreation_group_absence", clock)
    if group_result.stderr.strip():
        raise RuntimeError("Azure target resource-group absence returned a diagnostic")
    exists = json.loads(group_result.stdout)
    if type(exists) is not bool:
        raise RuntimeError("Azure target resource-group inventory is not a boolean")
    actions.append(_safe_cleanup_action(invocation_options, group_result))
    resources: list[dict[str, Any]] = []
    if exists:
        resources, group_evidence = _read_arm_collection(runner, invocation_options, root,
            f"/subscriptions/{options.subscription_id}/resourceGroups/{group}/resources?api-version=2021-04-01",
            "precreation_group_resources", clock, ("id", "type"))
        actions.extend(group_evidence)
        for collection in ("roleAssignments", "roleDefinitions"):
            rows, evidence = _read_arm_collection(runner, invocation_options, root,
                f"/subscriptions/{options.subscription_id}/resourceGroups/{group}/providers/Microsoft.Authorization/{collection}?api-version=2022-04-01",
                f"precreation_{collection}", clock, ("id",))
            resources.extend(rows)
            actions.extend(evidence)
    vm_ids = [f"/subscriptions/{options.subscription_id}/resourceGroups/{group}/providers/Microsoft.Compute/virtualMachines/{group}-{role}"
        for role in ("a", "b")]
    for vm_id in vm_ids:
        evidence = _prove_vm_absent(runner, invocation_options, root, vm_id, clock,
            absent_parent_group=group if not exists else None)
        actions.append(_safe_cleanup_action(invocation_options, evidence))
    verified = not exists or not resources
    # We do not have the original cleanup routine stop timestamp. An unexpired
    # cap cannot establish a timing pass; keep it unknown until sourced.
    status = "late" if now_utc >= deployment.reservation.value.expires_at.value else "unknown"
    return {"provider": "azure", "verified": verified, "target_selector": ";".join(vm_ids),
        "resource_group": group, "resource_group_exists": exists,
        "remaining_resource_count": len(resources), "actions": actions,
        "query_count": len(actions), "timing_status": status}
