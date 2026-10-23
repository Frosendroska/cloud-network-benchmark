"""Small, Azure-specific V2 deployment and independent expiry driver.

All external operations are argv-based and injected. Importing this module is offline.
"""
from __future__ import annotations

import hashlib
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
    admission_evidence: dict[str, Any]
    retry_end_at: datetime
    poll_seconds: int = Field(ge=1, le=300)
    command_timeout_seconds: float = Field(gt=0)
    expiry_cost_input: dict[str, Any]

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
        if not self.admission_evidence.get("priority_review_reference") and self.admission_evidence.get("priority") in {"P2", "P3"}:
            raise ValueError("P2/P3 requires explicit priority review evidence")
        if self.admission_evidence.get("outstanding_cleanup") is not False:
            raise ValueError("outstanding cleanup must be explicitly false")
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


def _slug(d: ProviderDeploymentInput) -> str:
    stem = re.sub(r"[^a-z0-9-]", "-", f"cnb-{d.campaign_id}-{d.deployment_id}".lower()).strip("-")
    suffix = hashlib.sha256(f"{d.campaign_id}\0{d.deployment_id}".encode()).hexdigest()[:8]
    return stem[:36].rstrip("-") + "-" + suffix


def _validate_request(d: ProviderDeploymentInput, o: AzureRuntimeOptions, now: datetime,
                     mono: float, *, require_registered: bool = False) -> tuple[str, list[str]]:
    if Provider(d.provider) != Provider.AZURE:
        raise ValueError("Azure driver requires provider=azure")
    if d.vm_a.vm_shape != "Standard_D4s_v5" or d.vm_b.vm_shape != "Standard_D4s_v5":
        raise ValueError("Azure shape must be Standard_D4s_v5")
    if d.vm_a.disk.kind != "StandardSSD_LRS_E4" or d.vm_b.disk.kind != "StandardSSD_LRS_E4":
        raise ValueError("Azure disk must be StandardSSD_LRS_E4")
    if d.vm_a.acceleration.kind != "accelerated_networking" or d.vm_b.acceleration.kind != "accelerated_networking":
        raise ValueError("both Azure NICs require Accelerated Networking")
    if d.vm_a.region != "germanywestcentral" or d.vm_b.region not in {"germanywestcentral", "uksouth"}:
        raise ValueError("Azure locality must use Frankfurt and optionally London")
    expected = {
        Scenario.SAME_ZONE: ("germanywestcentral", "germanywestcentral", "1", "1", "none"),
        Scenario.CROSS_ZONE: ("germanywestcentral", "germanywestcentral", "1", "2", "none"),
        Scenario.PLACEMENT_OPTIMIZATION: ("germanywestcentral", "germanywestcentral", "1", "1", "proximity_placement_group"),
        Scenario.INTER_REGION: ("germanywestcentral", "uksouth", "1", "1", "none"),
    }[Scenario(d.scenario)]
    got = (d.vm_a.region, d.vm_b.region, d.vm_a.zone, d.vm_b.zone, d.placement.kind)
    if got != expected:
        raise ValueError(f"scenario locality/placement mismatch: expected {expected}, got {got}")
    if d.scenario == Scenario.PLACEMENT_OPTIMIZATION and not d.placement.name:
        raise ValueError("placement optimization requires a named PPG")
    if d.scenario != Scenario.PLACEMENT_OPTIMIZATION and d.placement.name is not None:
        raise ValueError("PPG is only valid for placement_optimization")
    reservation = d.reservation.value
    if reservation is None or reservation.state != "reserved" or reservation.reservation_id.value is None:
        raise ValueError("matching active reservation evidence is required")
    if reservation.campaign_id != d.campaign_id or reservation.expires_at.value is None or reservation.cap_started_at.value is None:
        raise ValueError("reservation identity and cap evidence must be complete")
    mode_cap=int(AdmissionRequirements.expected_for(d.mode,d.scenario)["lifetime_cap_seconds"])
    if d.admission.lifetime_cap_seconds != mode_cap:
        raise ValueError("admitted lifetime cap does not match the selected mode")
    mode_measurement_seconds={"acceptance":30,"short_study":180,"calibration":720,"diagnostic_day":86400}[ExperimentMode(d.mode).value]
    if (o.complete_workflow_seconds < mode_measurement_seconds or o.post_registration_workflow_seconds < mode_measurement_seconds
            or o.complete_workflow_seconds > mode_cap or o.post_registration_workflow_seconds > mode_cap):
        raise ValueError("workflow bounds do not cover the selected mode or exceed its lifetime ceiling")
    if now < reservation.cap_started_at.value or reservation.expires_at.value-reservation.cap_started_at.value > timedelta(seconds=mode_cap):
        raise ValueError("reservation chronology or original mode lifetime ceiling is invalid")
    if o.admission_evidence.get("reservation_id") != reservation.reservation_id.value or o.admission_evidence.get("source_sha256") != reservation.source_sha256:
        raise ValueError("admission snapshot must bind this reservation and its source hash")
    expiry_cost_bound = float(o.expiry_cost_input["automation_rate_per_job_minute"]) * float(o.expiry_cost_input["job_minutes_bound"])
    if expiry_cost_bound > reservation.reserved_cost_usd.value:
        raise ValueError("bounded Azure expiry job cost exceeds the explicitly reserved deployment cost")
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
    if payload!=expected: raise RuntimeError(f"private {name} Terraform input no longer matches this deployment")
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
    mono = 0.0
    _validate_request(deployment, options, now, mono)
    scenario = Scenario(deployment.scenario)
    return {"resource_group_name": _slug(deployment), "location": "germanywestcentral",
        "subscription_id": options.subscription_id, "deployment_id": deployment.deployment_id,
        "config_sha256": deployment.config_sha256, "controller_cidr": options.controller_cidr,
        "ssh_public_key": options.ssh_public_key, "selected_image": options.selected_image,
        "placement_group_name": deployment.placement.name or "", "scenario": scenario.value,
        "region_a": "germanywestcentral", "region_b": "uksouth" if scenario == Scenario.INTER_REGION else "germanywestcentral",
        "zone_a": "1", "zone_b": "2" if scenario == Scenario.CROSS_ZONE else "1",
        "private_a": "10.70.0.4", "private_b": "10.71.0.4" if scenario == Scenario.INTER_REGION else "10.70.0.5",
        "create_peering": scenario == Scenario.INTER_REGION, "create_ppg": scenario == Scenario.PLACEMENT_OPTIMIZATION,
        "active_priority": "Regular", "network_limit_mbps": 12500}


def _is_exact_not_found(result: CommandResult) -> bool:
    for text in (result.stdout, result.stderr):
        try:
            payload=json.loads(text)
            code=payload.get("error",{}).get("code") if isinstance(payload,dict) else None
            if code in {"ResourceNotFound","NotFound"}: return True
        except (json.JSONDecodeError,AttributeError):
            pass
        if re.search(r"(?:^|\n)\s*(?:ERROR:\s*)?\(ResourceNotFound\)(?:\s|$)",text): return True
    return False


def _command(runner: CommandRunner, options: AzureRuntimeOptions, root: Path, argv: list[str], action: str,
             clock: Callable[[], float], *, allow_not_found: bool = False) -> CommandResult:
    remaining = options.cap_deadline_monotonic-clock()
    if remaining <= 0: raise TimeoutError("original reservation deadline elapsed")
    environment = dict(os.environ)
    tf_root = next((arg.split("=", 1)[1] for arg in argv if arg.startswith("-chdir=")), "terraform")
    environment["TF_DATA_DIR"] = str(root / ("terraform-data-" + Path(tf_root).name))
    req = CommandRequest(action_id=f"azure-{uuid.uuid4()}", action_type=action, argv=argv,
        cwd=str(Path(__file__).resolve().parents[2]), timeout_seconds=min(options.command_timeout_seconds, remaining),
        environment=environment, environment_classification="contains_secret_references", redactions=[options.authentication_reference, options.ssh_public_key, str(options.attempt_root)])
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
    journal = root / "recovery.json"
    if journal.is_file() and not journal.is_symlink():
        state=json.loads(journal.read_text(encoding="utf-8"))
        state.setdefault("actions", []).append({"request":req.durable_view(),"result":_safe_result_view(options,result)})
        _atomic_private(journal,state)
    if result.outcome != "succeeded" and not (allow_not_found and _is_exact_not_found(result)):
        raise RuntimeError(_redact(options,f"{action} failed with {result.outcome}: {result.stderr[:1000]}"))
    if len(result.stdout.encode("utf-8"))>1_000_000 or len(result.stderr.encode("utf-8"))>1_000_000:
        raise RuntimeError(f"{action} produced output above the 1 MiB bounded-response limit")
    return result


def _prove_vm_absent(runner: CommandRunner, options: AzureRuntimeOptions, root: Path,
                     resource_id: str, clock: Callable[[], float]) -> CommandResult:
    """Accept only an explicit Azure ResourceNotFound response for the exact VM GET."""
    remaining = options.cap_deadline_monotonic-clock()
    if remaining <= 0:
        raise TimeoutError("original reservation deadline elapsed")
    request = CommandRequest(action_id=f"azure-{uuid.uuid4()}", action_type="vm_absence_by_id",
        argv=["az", "resource", "show", "--ids", resource_id, "--output", "json"],
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
        if not result.stdout.strip(): raise RuntimeError(f"{action} returned blank collection output")
        try: payload=json.loads(result.stdout)
        except json.JSONDecodeError as exc: raise RuntimeError(f"{action} returned malformed JSON") from exc
        if not isinstance(payload,dict) or not isinstance(payload.get("value"),list):
            raise RuntimeError(f"{action} response lacks a complete ARM value collection")
        for item in payload["value"]:
            if not isinstance(item,dict) or any(not isinstance(item.get(field),str) or not item[field].strip() for field in required_fields):
                raise RuntimeError(f"{action} contains an incomplete resource observation")
            identity=item[required_fields[0]].lower()
            if identity in seen_ids: raise RuntimeError(f"{action} contains a duplicate resource identity")
            seen_ids.add(identity); values.append(item)
        link=payload.get("nextLink")
        if link in (None,""): return values,evidence
        if not isinstance(link,str) or not link.startswith(scope_prefix):
            raise RuntimeError(f"{action} returned an invalid continuation URL")
        next_url=link
    raise RuntimeError(f"{action} exceeded the bounded 1000-page inventory limit")


def _delete_owned_arm_child(runner: CommandRunner, options: AzureRuntimeOptions, root: Path,
                            account: str, collection: str, child: str,
                            clock: Callable[[],float], actions: list[dict[str,Any]]) -> None:
    base=f"{account}/{collection}/{child}?api-version={AUTOMATION_API}"
    read=_command(runner,options,root,_azure_rest("GET",base),f"expiry_{collection}_before_delete",clock,allow_not_found=True)
    actions.append(_safe_cleanup_action(options,read))
    if read.outcome=="failed" and _is_exact_not_found(read): return
    try: observed=json.loads(read.stdout)
    except (json.JSONDecodeError,TypeError) as exc: raise RuntimeError(f"Azure {collection} readback is malformed") from exc
    expected=base.split("?",1)[0]
    if not isinstance(observed,dict) or not isinstance(observed.get("id"),str) or observed["id"].lower()!=expected.lower():
        raise RuntimeError(f"Azure {collection} readback identity mismatch")
    deleted=_command(runner,options,root,_azure_rest("DELETE",base),f"expiry_{collection}_delete",clock,allow_not_found=True)
    actions.append(_safe_cleanup_action(options,deleted))
    verify=_command(runner,options,root,_azure_rest("GET",base),f"expiry_{collection}_absence",clock,allow_not_found=True)
    actions.append(_safe_cleanup_action(options,verify))
    if verify.outcome!="failed" or not _is_exact_not_found(verify):
        raise RuntimeError(f"Azure {collection} deletion is not confirmed")


def _azure_rest(method: str, url: str, body: Optional[dict[str, Any]] = None) -> list[str]:
    args = ["az", "rest", "--method", method, "--url", url, "--output", "json"]
    if body is not None: args += ["--headers", "Content-Type=application/json", "--body", json.dumps(body, separators=(",", ":"))]
    return args


def _register_expiry_impl(deployment: ProviderDeploymentInput, request: ExpiryRequest, options: AzureRuntimeOptions,
                    runner: CommandRunner, *, clock: Callable[[], float], utc_now: Callable[[], datetime],
                    sleep: Callable[[float], None]) -> ExpiryEvidence:
    """Prepare/read back Automation and bind one UTC job before pair creation."""
    now = utc_now().astimezone(UTC); mono = clock()
    name, ids = _validate_request(deployment, options, now, mono)
    if request.provider != Provider.AZURE or request.action != "deallocate" or request.run_id != deployment.run_id or request.deployment_id != deployment.deployment_id:
        raise ValueError("expiry request identity/action mismatch")
    if request.resource_namespace != name:
        raise ValueError("expiry request namespace does not match deterministic attempt")
    reservation = deployment.reservation.value
    trigger = request.expires_at.value
    if trigger is None:
        raise ValueError("expiry trigger is required")
    if trigger >= reservation.expires_at.value or trigger >= options.retry_end_at:
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
    root, journal = _workspace(options, deployment)
    pair_vars = _persist_tfvars(root,journal,"pair",build_pair_inputs(deployment,options))
    expiry_values={"subscription_id":options.subscription_id,"tenant_id":options.tenant_id,
        "resource_group_name":name,"deployment_id":deployment.deployment_id,"location":"germanywestcentral"}
    expiry_vars = _persist_tfvars(root,journal,"expiry",expiry_values)
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
    _tf(root,options,runner,clock,"terraform/azure/expiry","apply","-auto-approve","-input=false",f"-var-file={expiry_vars}",action="expiry_prerequisites")
    expiry_outputs = json.loads(_tf(root,options,runner,clock,"terraform/azure/expiry","output","-json",action="expiry_outputs").stdout)
    expiry_ids = {key: item.get("value") for key, item in expiry_outputs.items()}
    required_outputs = ("resource_group_id", "automation_account_id", "principal_id", "role_assignment_id", "role_definition_id")
    if any(not expiry_ids.get(key) for key in required_outputs):
        raise RuntimeError("expiry prerequisite outputs are incomplete")
    account_read=json.loads(_command(runner,options,root,_azure_rest("GET",f"{account}?api-version={AUTOMATION_API}"),"automation_account_readback",clock).stdout)
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
    runbook = {"location":"germanywestcentral","properties":{"runbookType":"PowerShell","logVerbose":False,"logProgress":True,"description":"F06 deallocate-only expiry"}}
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
        if (published.get("properties", {}).get("state") == "Published"
                and published.get("properties", {}).get("runbookType") == "PowerShell"
                and published.get("properties", {}).get("runtimeEnvironment") in (None, "", "PowerShell-5.1")):
            break
        remaining=min(publish_mono_deadline-clock(),publish_wall_deadline-time.monotonic())
        if remaining <= 0:
            raise TimeoutError("runbook publication did not reach Published within the original command deadline")
        sleep(min(options.poll_seconds,remaining))
    content = _command(runner, options, root, ["az", "rest", "--method", "GET", "--url", f"{account}/runbooks/{rb}/content?api-version={AUTOMATION_API}", "--output", "tsv"], "published_content_readback", clock)
    digest = hashlib.sha256(content.stdout.encode()).hexdigest()
    if published.get("properties", {}).get("state") != "Published" or digest != hashlib.sha256(script.encode()).hexdigest():
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
    if (not props.get("isEnabled") or props.get("frequency") != "OneTime" or props.get("timeZone") != "UTC"
            or datetime.fromisoformat(props["startTime"].replace("Z", "+00:00")).astimezone(UTC) != trigger):
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
    assignments = json.loads(_command(runner, options, root, ["az", "role", "assignment", "list", "--scope", expiry_ids["resource_group_id"], "--all", "--output", "json"], "role_assignment_readback", clock).stdout or "[]")
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
    metadata = {"mechanism":"azure_automation_once_deallocate","api_version":AUTOMATION_API,"resource_group":name,
        "reservation_id":reservation.reservation_id.value,"source_sha256":reservation.source_sha256,
        "runbook":rb,"schedule":sched,"job_schedule_id":job_id,"trigger":trigger.isoformat(),"target_ids":ids,
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
    if runbook.get("properties", {}).get("state") != "Published":
        raise RuntimeError("registered runbook is no longer Published")
    if runbook.get("properties", {}).get("runbookType") != "PowerShell" or runbook.get("properties", {}).get("runtimeEnvironment") not in (None, "", "PowerShell-5.1"):
        raise RuntimeError("registered runbook runtime changed")
    account_read=json.loads(_command(runner,options,root,_azure_rest("GET",f"{account}?api-version={AUTOMATION_API}"),"expiry_account_recheck",clock).stdout)
    if account_read.get("identity",{}).get("type") != "SystemAssigned" or account_read.get("identity",{}).get("principalId","").lower() != str(m.get("managed_identity_principal_id","")).lower():
        raise RuntimeError("registered Automation system-assigned identity changed")
    content = _command(runner, options, root, ["az", "rest", "--method", "GET", "--url", f"{account}/runbooks/{m['runbook']}/content?api-version={AUTOMATION_API}", "--output", "tsv"], "expiry_content_recheck", clock)
    if hashlib.sha256(content.stdout.encode()).hexdigest() != m.get("published_content_sha256"):
        raise RuntimeError("registered runbook content changed after expiry registration")
    schedule_url = f"{account}/schedules/{m['schedule']}?api-version={AUTOMATION_API}"
    schedule = json.loads(_command(runner, options, root, _azure_rest("GET", schedule_url), "expiry_schedule_recheck", clock).stdout or "{}")
    props = schedule.get("properties", {})
    if (not props.get("isEnabled") or props.get("frequency") != "OneTime" or props.get("timeZone") != "UTC"
            or props.get("runOn") not in (None, "")
            or datetime.fromisoformat(props["startTime"].replace("Z", "+00:00")).astimezone(UTC) != expiry.expires_at.value):
        raise RuntimeError("registered expiry schedule is disabled or has changed")
    job_url = f"{account}/jobSchedules/{m['job_schedule_id']}?api-version={AUTOMATION_API}"
    job = json.loads(_command(runner, options, root, _azure_rest("GET", job_url), "expiry_binding_recheck", clock).stdout or "{}")
    if (job.get("id","").lower()!=job_url.split("?",1)[0].lower()
            or job.get("properties",{}).get("runOn") not in (None, "")
            or job.get("properties", {}).get("parameters", {}) != m.get("binding_readback")
            or job.get("properties", {}).get("runbook", {}).get("name") != m.get("runbook")
            or job.get("properties", {}).get("schedule", {}).get("name") != m.get("schedule")):
        raise RuntimeError("registered expiry job binding changed")
    assignments = json.loads(_command(runner, options, root, ["az", "role", "assignment", "list", "--scope", m["resource_group_id"], "--all", "--output", "json"], "expiry_role_recheck", clock).stdout or "[]")
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
    trigger = request.expires_at.value
    if request.provider != Provider.AZURE or request.action != "deallocate" or request.run_id != deployment.run_id or request.deployment_id != deployment.deployment_id:
        raise ValueError("expiry request identity/action mismatch")
    if trigger is None or trigger >= reservation.expires_at.value or trigger >= options.retry_end_at:
        raise ValueError("expiry trigger must precede retry end and reservation cap")
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
        return _register_expiry_impl(deployment, request, options, runner, clock=clock, utc_now=utc_now, sleep=sleep)
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
        workdir: str, *args: str, action: str) -> CommandResult:
    state = root / ("expiry.tfstate" if workdir.endswith("/expiry") else "pair.tfstate")
    argv = ["terraform", f"-chdir={workdir}", *args]
    if args and args[0] in {"apply", "destroy", "output"}:
        argv += ["-state", str(state)]
    if args and args[0] == "init":
        argv += [f"-backend-config=path={state}"]
    return _command(runner, options, root, argv, action, clock)


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
                             clock: Callable[[],float]) -> dict[str,Any]:
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
        result=json.loads(_command(runner,options,root,["az","vm","image","show","--location","germanywestcentral","--urn",image,"--output","json"],"image_preflight",clock).stdout)
        props=result.get("properties",{})
        actual={"id":result.get("id"),"architecture":result.get("architecture",props.get("architecture")),"publisher":result.get("publisher",props.get("publisher")),
                "offer":result.get("offer",props.get("offer")),"sku":result.get("sku",props.get("sku")),"version":result.get("version",props.get("version")),"source":"marketplace"}
    if str(actual.get("architecture") or "").lower() not in {"x64","amd64"} or str(actual.get("publisher") or "").lower()!="canonical" or not re.search(r"(?i)(ubuntu-24_04|ubuntu-24\.04|ubuntu2404|noble)",str(actual.get("offer",""))+" "+str(actual.get("sku",""))):
        raise ValueError("selected Azure image metadata does not prove x64 Canonical Ubuntu 24.04")
    if image.startswith("/") and str(actual.get("id","")).lower()!=image.lower():
        raise ValueError("selected shared gallery image version identity mismatch")
    if not image.startswith("/") and (actual.get("publisher","").lower(),actual.get("offer","").lower(),actual.get("sku","").lower(),str(actual.get("version","")))!=tuple(x.lower() for x in image.split(":")):
        raise ValueError("selected Marketplace image identity mismatch")
    return actual


def _observe_vm_image(options: AzureRuntimeOptions, runner: CommandRunner, root: Path,
                      vm_id: str, clock: Callable[[],float], requested: str) -> str:
    payload=json.loads(_command(runner,options,root,["az","vm","show","--ids",vm_id,"--output","json"],"vm_image_observation",clock).stdout)
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
    now=utc_now().astimezone(UTC); mono=clock(); name,ids=_validate_request(deployment,options,now,mono,require_registered=True)
    if date.fromisoformat(str(options.expiry_cost_input["date"])) > now.date():
        raise ValueError("Azure expiry price input date cannot be in the future")
    root,journal=_workspace(options,deployment); state=json.loads(journal.read_text()); failures=[]; results=[]; apply=None; outputs=None; valid_outputs={}
    variables=build_pair_inputs(deployment,options)
    pair_vars=_persist_tfvars(root,journal,"pair",variables)
    try:
        _verify_registered_expiry(deployment,options,root,runner,clock,now)
        _tf(root,options,runner,clock,"terraform/azure/pair","init","-input=false",action="pair_init")
        # Expiry readbacks and pair initialization may consume meaningful cap time.
        fresh_utc=utc_now().astimezone(UTC); fresh_mono=clock()
        _validate_request(deployment,options,fresh_utc,fresh_mono,require_registered=True)
        if deployment.expiry.value.expires_at.value - fresh_utc < timedelta(seconds=options.post_registration_workflow_seconds):
            raise TimeoutError("remaining complete workflow no longer fits before the registered trigger")
        if options.cap_deadline_monotonic-fresh_mono < options.post_registration_workflow_seconds:
            raise TimeoutError("remaining complete workflow no longer fits within the original monotonic cap")
        image_probe=_validate_image_metadata(options,runner,root,clock)
        state=json.loads(journal.read_text(encoding="utf-8")); state["creation_may_have_started"]=True; state["pair_state"]="creating"; _atomic_private(journal,state)
        args=["apply","-auto-approve","-input=false",f"-var-file={pair_vars}"]
        preapply_utc=utc_now().astimezone(UTC); preapply_mono=clock()
        if deployment.expiry.value.expires_at.value-preapply_utc < timedelta(seconds=options.post_registration_workflow_seconds) or options.cap_deadline_monotonic-preapply_mono < options.post_registration_workflow_seconds:
            state=json.loads(journal.read_text(encoding="utf-8")); state["creation_may_have_started"]=False; state["pair_state"]="not_started"; _atomic_private(journal,state)
            raise TimeoutError("complete workflow no longer fits immediately before pair apply")
        apply=_tf(root,options,runner,clock,"terraform/azure/pair",*args,action="pair_apply"); results.append(apply.durable_view())
        out=_tf(root,options,runner,clock,"terraform/azure/pair","output","-json",action="pair_output")
        outputs=json.loads(out.stdout)
        observed_images={}
        vms=[]; key_lines=[]
        for role in ("a","b"):
            data=_validate_vm_output_data(deployment,options,role,outputs["vm_"+role]["value"],ids[0 if role=="a" else 1])
            data["image"]=_observe_vm_image(options,runner,root,ids[0 if role=="a" else 1],clock,options.selected_image)
            observed_images[role]=data["image"]
            valid_outputs[role]=data
            nonce=secrets.token_urlsafe(24)
            script=f"printf 'CNB-AZ-HOSTKEY:{nonce}\\n'; cat /etc/ssh/ssh_host_ed25519_key.pub"
            command=_command(runner,options,root,["az","vm","run-command","invoke","--ids",ids[0 if role=="a" else 1],"--command-id","RunShellScript","--scripts",script,"--output","json"],"host_key_identity",clock)
            action=json.loads(command.stdout)
            statuses=action.get("value",[])
            success=[x for x in statuses if isinstance(x,dict) and str(x.get("code","")).lower()=="componentstatus/stdout/succeeded"]
            if len(success)!=1 or any(str(x.get("level","")).lower()=="error" or str(x.get("code","")).lower().endswith("/failed") for x in statuses):
                raise ValueError("authenticated Azure Run Command did not report successful completion")
            script_out=success[0].get("message","")
            marker=f"CNB-AZ-HOSTKEY:{nonce}"
            if not isinstance(script_out,str) or script_out.count(marker)!=1:
                raise ValueError("authenticated Azure Run Command response nonce is missing, stale, or ambiguous")
            suffix=script_out.split(marker,1)[1]
            parsed_keys=[line.strip() for line in suffix.splitlines() if _KEY.fullmatch(line.strip())]
            if len(parsed_keys)!=1:
                raise ValueError("authenticated Azure Run Command stdout has malformed or ambiguous host key material")
            key=_key_material(parsed_keys[0])
            key_lines.append(f"{data['public_ipv4']} {key}")
            vms.append(_vm_output(deployment,role,data,key,options))
        known=root/"known_hosts"
        if known.exists(): raise FileExistsError("known_hosts already exists; refusing overwrite")
        with known.open("x",encoding="utf-8") as f: f.write("\n".join(key_lines)+"\n")
        os.chmod(known,0o600)
        state=json.loads(journal.read_text(encoding="utf-8"))
        state["pair_state"]="created"; state["known_hosts_reference"]=str(known); _atomic_private(journal,state)
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


def destroy(deployment: ProviderDeploymentInput, options: AzureRuntimeOptions, runner: CommandRunner, *,
            clock: Callable[[],float], utc_now: Callable[[],datetime]) -> AzureCleanupResult:
    start=utc_now().astimezone(UTC); root=options.attempt_root; journal=root/"recovery.json"; actions=[]; failures=[]; resources=[]
    if not journal.is_file() or journal.is_symlink():
        failures.append(FailureEvidence(category="recovery_missing",message="private recovery journal unavailable",occurred_at=start))
        return AzureCleanupResult(identity=_identity(deployment),cleanup_state=CleanupState.FAILED,attempted_at=start,finished_at=utc_now(),failures=failures,launch_blocked=True)
    state=json.loads(journal.read_text())
    if state.get("run_id")!=deployment.run_id or state.get("deployment_id")!=deployment.deployment_id:
        raise ValueError("recovery journal identity mismatch; refusing cross-attempt cleanup")
    if state.get("config_sha256") != deployment.config_sha256 or state.get("resource_group") != _slug(deployment):
        raise ValueError("recovery journal configuration or namespace mismatch; refusing cleanup")
    expected_vm_ids=[f"/subscriptions/{options.subscription_id}/resourceGroups/{_slug(deployment)}/providers/Microsoft.Compute/virtualMachines/{_slug(deployment)}-{role}" for role in ("a","b")]
    if state.get("vm_ids") != expected_vm_ids:
        raise ValueError("recovery journal contains foreign target identities; refusing cleanup")
    try:
        # Pair destroy precedes inventory. Expiry remains until direct inventory proves both VMs absent.
        input_names=state.get("terraform_inputs",{})
        if "pair" not in input_names:
            if state.get("creation_may_have_started"): raise RuntimeError("pair Terraform inputs are missing after resource creation may have started")
            pair_vars=_persist_tfvars(root,journal,"pair",build_pair_inputs(deployment,options))
        else: pair_vars=_recovery_tfvars(root,journal,"pair",build_pair_inputs(deployment,options))
        expected_expiry={"subscription_id":options.subscription_id,"tenant_id":options.tenant_id,
            "resource_group_name":_slug(deployment),"deployment_id":deployment.deployment_id,"location":"germanywestcentral"}
        if "expiry" not in input_names:
            if state.get("expiry_state") not in (None,"preparing","failed"): raise RuntimeError("expiry Terraform inputs are missing after expiry preparation")
            expiry_vars=_persist_tfvars(root,journal,"expiry",expected_expiry)
        else: expiry_vars=_recovery_tfvars(root,journal,"expiry",expected_expiry)
        _tf(root,options,runner,clock,"terraform/azure/pair","init","-input=false",action="pair_recovery_init")
        result=_tf(root,options,runner,clock,"terraform/azure/pair","destroy","-auto-approve","-input=false",f"-var-file={pair_vars}",action="pair_destroy")
        actions.append(_safe_cleanup_action(options,result))
        for vm_id in expected_vm_ids:
            actions.append(_safe_cleanup_action(options,_prove_vm_absent(runner,options,root,vm_id,clock)))
        prereq=state.get("expiry",{})
        applied=any(item.get("request",{}).get("action_type")=="expiry_prerequisites"
                    and item.get("result",{}).get("outcome")=="succeeded" for item in state.get("actions",[]))
        if applied and not prereq.get("role_assignment_id"):
            _tf(root,options,runner,clock,"terraform/azure/expiry","init","-input=false",action="expiry_recovery_identity_init")
            raw=_tf(root,options,runner,clock,"terraform/azure/expiry","output","-json",action="expiry_recovery_identity_output").stdout
            outputs=json.loads(raw)
            recovered={k:outputs.get(k,{}).get("value") for k in ("resource_group_id","automation_account_id","principal_id","role_assignment_id","role_definition_id")}
            if not all(isinstance(value,str) and value for value in recovered.values()):
                raise RuntimeError("interrupted expiry prerequisite identities cannot be recovered completely")
            prereq.update(recovered); state["expiry"]=prereq; _atomic_private(journal,state)
        found,inventory_evidence=_read_arm_collection(runner,options,root,
            f"/subscriptions/{options.subscription_id}/resourceGroups/{state['resource_group']}/resources?api-version=2021-04-01",
            "pair_inventory",clock,("id","type"))
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
        if account:
            child_info=prereq
            for collection,key in (("jobSchedules","job_schedule_id"),("schedules","schedule"),("runbooks","runbook")):
                child=child_info.get(key)
                if child: _delete_owned_arm_child(runner,options,root,account,collection,str(child),clock,actions)
        _tf(root,options,runner,clock,"terraform/azure/expiry","init","-input=false",action="expiry_recovery_init")
        exp=_tf(root,options,runner,clock,"terraform/azure/expiry","destroy","-auto-approve","-input=false",f"-var-file={expiry_vars}",action="expiry_destroy")
        actions.append(_safe_cleanup_action(options,exp))
        if account:
            account_absence=_command(runner,options,root,["az","resource","show","--ids",account,"--output","json"],"automation_account_absence",clock,allow_not_found=True)
            actions.append(_safe_cleanup_action(options,account_absence))
            if account_absence.outcome!="failed" or not _is_exact_not_found(account_absence):
                raise RuntimeError("Automation account absence is not proven by exact resource identity")
        verify=_command(runner,options,root,["az","group","exists","--name",state["resource_group"],"--output","tsv"],"expiry_inventory",clock)
        actions.append(_safe_cleanup_action(options,verify))
        if verify.stdout.strip().lower()!="false": raise RuntimeError("attempt resource group removal is not verified")
        residual_roles=_command(runner,options,root,["az","role","assignment","list","--scope",f"/subscriptions/{options.subscription_id}/resourceGroups/{state['resource_group']}","--all","--output","json"],"role_cleanup_inventory",clock)
        if not residual_roles.stdout.strip(): raise RuntimeError("role assignment inventory is blank")
        assignments=json.loads(residual_roles.stdout)
        if not isinstance(assignments,list): raise RuntimeError("role assignment inventory is malformed")
        if assignments:
            raise RuntimeError("role assignment inventory still contains resources after expiry destroy")
        actions.append(_safe_cleanup_action(options,residual_roles))
        residual_defs=_command(runner,options,root,["az","role","definition","list","--name",_slug(deployment)+"-deallocate-only","--output","json"],"role_definition_cleanup_inventory",clock)
        if not residual_defs.stdout.strip(): raise RuntimeError("custom role definition inventory is blank")
        definitions=json.loads(residual_defs.stdout)
        if not isinstance(definitions,list): raise RuntimeError("custom role definition inventory is malformed")
        expected_role=str(prereq.get("role_definition_id","")).lower()
        if any(isinstance(item,dict) and str(item.get("id","")).lower()==expected_role for item in definitions):
            raise RuntimeError("attempt custom role definition remains after expiry destroy")
        actions.append(_safe_cleanup_action(options,residual_defs))
        shutil.rmtree(root)
        cleanup=CleanupState.SUCCEEDED; blocked=False
    except Exception as exc:
        failures.append(FailureEvidence(category="azure_cleanup",message=_redact(options,exc)[:1000],occurred_at=utc_now()))
        state=json.loads(journal.read_text(encoding="utf-8"))
        state["failures"].append({"category":"azure_cleanup","message":_redact(options,exc)[:1000]}); _atomic_private(journal,state)
        cleanup=CleanupState.FAILED; blocked=True
    return AzureCleanupResult(identity=_identity(deployment),cleanup_state=cleanup,attempted_at=start,finished_at=utc_now(),actions=actions,failures=failures,resources=resources,launch_blocked=blocked)
