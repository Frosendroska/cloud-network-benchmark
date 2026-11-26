from __future__ import annotations

import hashlib
import base64
import struct
import ipaddress
import json
import os
import re
import tempfile
import uuid
from threading import local
import time
from datetime import datetime, timedelta, timezone
from math import isfinite
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qsl, urlparse

from pydantic import Field, field_validator, model_validator

from ..contracts.admission import ExpiryEvidence, ExpiryRequest, ReservationEvidence
from ..contracts.common import CleanupState, Evidence, FailureEvidence, StrictModel, StructuredEvidence, StringEvidence
from ..contracts.deployment import ProviderDeploymentInput, ProviderDeploymentOutput, ProviderVmOutput, RemoteConnectionData, IdentityContext
from ..contracts.execution import CommandRequest, CommandResult, CommandRunner
from ..validation_profiles import provider_lead_seconds

SAFETY_LEAD_SECONDS = 30
CONTROLLER_SESSION_ID = str(uuid.uuid4())
NETWORK_LIMIT = {"maximum_gbit_per_second": 10, "is_ceiling_not_observation": True, "source": "https://cloud.google.com/compute/docs/network-bandwidth", "checked_on": "2026-10-23"}
REGION_ZONES = {
    "europe-west3": ("europe-west3-a", "europe-west3-b"),
    "europe-west2": ("europe-west2-a",),
    "europe-north1": ("europe-north1-c",),
}
BASELINE_IMAGE_REGIONS = frozenset({"europe-west3", "europe-west2"})
NORTHERN_IMAGE_REGIONS = frozenset({"europe-north1"})
GCP_CLEANUP_INVENTORY_COMMANDS = (
    ("gcp-inventory-all-zones", ("compute", "instances", "list")),
    ("gcp-disk-inventory-all-zones", ("compute", "disks", "list")),
    ("gcp-addresses-inventory-all-scopes", ("compute", "addresses", "list")),
    ("gcp-subnet-inventory-all-scopes", ("compute", "networks", "subnets", "list")),
    ("gcp-instance_templates-inventory-all-scopes", ("compute", "instance-templates", "list")),
    ("gcp-networks-inventory-all-scopes", ("compute", "networks", "list")),
    ("gcp-firewalls-inventory-all-scopes", ("compute", "firewall-rules", "list")),
    ("gcp-routes-inventory-all-scopes", ("compute", "routes", "list")),
    ("gcp-policy-inventory-all-scopes", ("compute", "resource-policies", "list")),
)
GCP_CLEANUP_COMMAND_IDS = frozenset({"gcp-pair-destroy", "gcp-protection-destroy",
    *(action for action, _ in GCP_CLEANUP_INVENTORY_COMMANDS)})


def validate_ubuntu_image_identities(images: dict[str, str], *, selected_regions: set[str] | None = None) -> None:
    """Require dated Ubuntu 24.04 amd64 image resources, never family aliases."""
    allowed_maps = (BASELINE_IMAGE_REGIONS, NORTHERN_IMAGE_REGIONS,
                    BASELINE_IMAGE_REGIONS | NORTHERN_IMAGE_REGIONS)
    if not isinstance(images, dict) or frozenset(images) not in allowed_maps:
        raise ValueError("images must pin the selected Frankfurt/London or Finland image identities")
    if selected_regions is not None and not selected_regions <= images.keys():
        raise ValueError("images must pin every selected GCP region")
    # Noble is the published image name; retain dated legacy configuration names.
    pattern = r"projects/ubuntu-os-cloud/global/images/ubuntu-2404-(?:noble-amd64-v|lts-amd64-v?)[0-9]{8}"
    if any(not isinstance(value, str) or not re.fullmatch(pattern, value) for value in images.values()):
        raise ValueError("images must be immutable Ubuntu 24.04 amd64 image identities, not aliases or families")


class GcpRuntimeSettings(StrictModel):
    project_id: str = Field(pattern=r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
    controller_cidr: str
    images: dict[str, str]
    public_key: str = Field(min_length=1)
    authentication_reference: str = Field(min_length=1)
    private_state_root: Path
    terraform: str = "terraform"
    gcloud: str = "gcloud"
    cost_input_id: str = Field(min_length=1)
    cost_input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    cost_source_path: str = Field(min_length=1)
    complete_workflow_cost_handoff: StructuredEvidence
    support_cost_evidence: StructuredEvidence
    complete_workflow_remaining_seconds: float = Field(gt=0)
    workflow_cap_started_monotonic: float | None = Field(default=None, gt=0)
    registration_allowance_seconds: float = Field(default=0, ge=0)
    provision_allowance_seconds: float = Field(default=0, ge=0)
    primary_review_decision: Optional[StructuredEvidence] = None
    plan_review_timeout_seconds: int = Field(default=0, ge=0, le=600)
    plan_review_admission_path: Optional[Path] = Field(default=None, exclude_if=lambda value: value is None)
    command_timeout_seconds: int = Field(default=120, gt=0, le=1800)
    registration_timeout_seconds: int = Field(default=180, gt=0, le=600)

    @field_validator("controller_cidr")
    @classmethod
    def controller_is_single_ipv4(cls, value: str) -> str:
        network = ipaddress.ip_network(value, strict=True)
        if network.version != 4 or network.prefixlen != 32:
            raise ValueError("GCP management access requires a controller IPv4 /32")
        return str(network)

    @field_validator("public_key")
    @classmethod
    def public_key_is_public(cls, value: str) -> str:
        if not re.fullmatch(r"ssh-(?:ed25519|rsa) [A-Za-z0-9+/]+={0,2}(?: [^\r\n]+)?", value.strip()):
            raise ValueError("public_key must be an OpenSSH public key")
        if "PRIVATE KEY" in value:
            raise ValueError("private key material is not accepted")
        return value.strip()

    @field_validator("complete_workflow_remaining_seconds")
    @classmethod
    def finite_workflow_time(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("complete workflow estimate must be finite")
        return value

    @model_validator(mode="after")
    def source_complete(self) -> "GcpRuntimeSettings":
        if self.workflow_cap_started_monotonic is not None and (
                not isfinite(self.workflow_cap_started_monotonic)
                or self.registration_allowance_seconds <= 0
                or self.provision_allowance_seconds <= 0):
            raise ValueError("profiled GCP review requires its original cap anchor and stage allowances")
        validate_ubuntu_image_identities(self.images)
        handoff = self.complete_workflow_cost_handoff.value or {}
        for field in ("source_path", "source_sha256", "prices_checked_on", "estimated_total_usd", "units"):
            if field not in handoff:
                raise ValueError(f"complete-workflow cost evidence must include {field}")
        estimate = handoff["estimated_total_usd"]
        if isinstance(estimate, bool) or not isinstance(estimate, (int, float)) or not isfinite(float(estimate)) or estimate <= 0:
            raise ValueError("complete-workflow estimated cost must be positive finite USD")
        units = handoff["units"]
        if not isinstance(units, dict) or not units or any(not isinstance(key, str) or not key.strip() or not isinstance(value, (str, int, float)) or isinstance(value, bool) or (isinstance(value, str) and not value.strip()) or (isinstance(value, (int, float)) and (not isfinite(float(value)) or value <= 0)) for key, value in units.items()):
            raise ValueError("complete-workflow resource units must be nonempty, explicit and positive where numeric")
        if handoff["source_path"] != self.cost_source_path or handoff["source_sha256"] != self.cost_input_sha256:
            raise ValueError("cost source identity must match the supplied dated source")
        try:
            source_date = datetime.strptime(str(handoff["prices_checked_on"]), "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError("complete-workflow cost source date must use YYYY-MM-DD") from exc
        if source_date > datetime.now(timezone.utc).date():
            raise ValueError("complete-workflow cost source cannot be future-dated")
        support = self.support_cost_evidence.value or {}
        if (support.get("template_support_cost_usd") != 0 or support.get("placement_policy_support_cost_usd") != 0
                or not support.get("rationale") or not support.get("source_path") or not support.get("id")
                or not re.fullmatch(r"[a-f0-9]{64}", str(support.get("source_sha256", "")))):
            raise ValueError("zero support-cost evidence requires a sourced rationale and exact zero support line items")
        try:
            support_date = datetime.strptime(str(support.get("prices_checked_on")), "%Y-%m-%d").date()
            support_path = Path(__file__).resolve().parents[3] / str(support["source_path"])
            support_hash = hashlib.sha256(support_path.read_bytes()).hexdigest()
        except (KeyError, OSError, ValueError) as exc:
            raise ValueError("zero support-cost source must be a dated local evidence artifact") from exc
        if support_date > datetime.now(timezone.utc).date() or support_hash != support["source_sha256"]:
            raise ValueError("zero support-cost evidence date or source hash is invalid")
        return self


class GcpCleanupResult(StrictModel):
    cleanup_state: CleanupState
    cleanup_failure: Optional[FailureEvidence] = None
    inventory: StructuredEvidence
    expiry: Evidence[ExpiryEvidence]
    primary_failure: Optional[FailureEvidence] = None

    @model_validator(mode="after")
    def failure_state_is_truthful(self) -> "GcpCleanupResult":
        if self.cleanup_state == CleanupState.FAILED and self.cleanup_failure is None:
            raise ValueError("failed cleanup requires independent cleanup evidence")
        if self.cleanup_state == CleanupState.SUCCEEDED and (self.cleanup_failure is not None or self.inventory.value is None):
            raise ValueError("successful cleanup requires observed empty inventory")
        return self


class GcpProviderError(RuntimeError):
    def __init__(self, message: str, *, category: str = "gcp_provider", action_id: str | None = None):
        super().__init__(message)
        self.category, self.action_id = category, action_id


class GcpProviderDriver:
    GUEST_ATTRIBUTE_POLL_INTERVAL_SECONDS = 1.0

    def __init__(self, runner: CommandRunner, settings: GcpRuntimeSettings,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc), *,
                 monotonic_clock: Callable[[], float] = time.monotonic) -> None:
        self.runner, self.settings, self.clock = runner, settings, clock
        self.monotonic_clock = monotonic_clock
        self._command_context = local()

    @property
    def _deadline(self) -> float | None:
        return getattr(self._command_context, "deadline", None)

    @_deadline.setter
    def _deadline(self, value: float | None) -> None:
        self._command_context.deadline = value

    @property
    def _cleanup_deadline_utc(self) -> datetime | None:
        return getattr(self._command_context, "cleanup_deadline_utc", None)

    @_cleanup_deadline_utc.setter
    def _cleanup_deadline_utc(self, value: datetime | None) -> None:
        self._command_context.cleanup_deadline_utc = value

    @property
    def _cleanup_clock_binding(self) -> float | None:
        return getattr(self._command_context, "cleanup_clock_binding", None)

    @_cleanup_clock_binding.setter
    def _cleanup_clock_binding(self, value: float | None) -> None:
        self._command_context.cleanup_clock_binding = value

    @property
    def _cleanup_absence_deadline_utc(self) -> datetime | None:
        return getattr(self._command_context, "cleanup_absence_deadline_utc", None)

    @_cleanup_absence_deadline_utc.setter
    def _cleanup_absence_deadline_utc(self, value: datetime | None) -> None:
        self._command_context.cleanup_absence_deadline_utc = value

    @property
    def _cleanup_routine_deadline_utc(self) -> datetime | None:
        return getattr(self._command_context, "cleanup_routine_deadline_utc", None)

    @_cleanup_routine_deadline_utc.setter
    def _cleanup_routine_deadline_utc(self, value: datetime | None) -> None:
        self._command_context.cleanup_routine_deadline_utc = value

    @property
    def _active_attempt(self) -> Path | None:
        return getattr(self._command_context, "attempt", None)

    @_active_attempt.setter
    def _active_attempt(self, value: Path | None) -> None:
        self._command_context.attempt = value

    def register_expiry(self, deployment: ProviderDeploymentInput, request: ExpiryRequest) -> ExpiryEvidence:
        self._deadline = None
        self._provider_stop_deadline = None
        reservation = self._validate(deployment, request)
        if deployment.scenario in {"cross_zone", "inter_region"}:
            self._validate_review(deployment)
        lead = provider_lead_seconds(deployment.admission.workflow_profile, "gcp", SAFETY_LEAD_SECONDS)
        trigger = reservation.expires_at.value - timedelta(seconds=lead)
        attempt = self._attempt_dir(deployment)
        self._active_attempt = attempt
        journal = self._load(attempt, deployment)
        self._fit(deployment, reservation, trigger, self._remaining_workflow_seconds(deployment, "registration"))
        if journal.get("registered"):
            if journal.get("controller_session_id") != CONTROLLER_SESSION_ID:
                raise GcpProviderError("an earlier controller session cannot reuse registered protection; resolve the attempt through cleanup", category="cleanup_open")
            if journal.get("binding_sha256") != self._binding_fingerprint(deployment):
                raise GcpProviderError("registered attempt no longer matches its immutable inputs", category="state_identity_mismatch")
            return ExpiryEvidence.model_validate(journal["expiry"])
        if journal.get("registration_may_have_started"):
            raise GcpProviderError("an earlier interrupted registration cannot be restarted under this attempt", category="cleanup_open")
        self._provider_stop_deadline = time.monotonic() + max(0.0,
            (trigger - self.clock().astimezone(timezone.utc)).total_seconds())
        if self.settings.workflow_cap_started_monotonic is not None:
            from ..terraform_plan import workflow_review_stage_deadline
            self._deadline = workflow_review_stage_deadline(
                cap_started_monotonic=self.settings.workflow_cap_started_monotonic,
                registration_allowance_seconds=self.settings.registration_allowance_seconds,
                provision_allowance_seconds=self.settings.provision_allowance_seconds,
                stage="registration", overall_deadline=self.settings.workflow_cap_started_monotonic
                    + self.settings.complete_workflow_remaining_seconds,
                stop_deadline=self._provider_stop_deadline)
        else:
            self._deadline = min(time.monotonic() + self.settings.registration_timeout_seconds,
                self._provider_stop_deadline)
        journal.update({"cap_started_at": reservation.cap_started_at.value.isoformat(), "cap_expires_at": reservation.expires_at.value.isoformat(), "trigger": trigger.isoformat(), "registration_may_have_started": True, "binding_sha256": self._binding_fingerprint(deployment), "reservation_id": reservation.reservation_id.value, "controller_session_id": CONTROLLER_SESSION_ID})
        self._save(attempt, journal)
        try:
            out = self._terraform(attempt, "protection", "apply", deployment, {"expiry_time": trigger.isoformat().replace("+00:00", "Z")})
            templates = self._outputs(out).get("templates", {})
            if set(templates) != {"vm_a", "vm_b"}:
                raise GcpProviderError("both template identities were not observed", category="expiry_uncertain")
            template_ids = [str(item.get("id", "")) for item in templates.values()]
            template_links = [item.get("self_link_unique") for item in templates.values()]
            if not all(template_ids) or len(set(template_ids)) != 2 or not all(template_links) or len(set(template_links)) != 2:
                raise GcpProviderError("role templates must have distinct immutable numeric and unique-link identities", category="expiry_uncertain")
            journal["templates"] = templates
            placement_policy_id = self._outputs(out).get("compact_policy_id")
            journal["compact_policy_id"] = placement_policy_id
            expected_policy_path = self._selected_policy_path(deployment)
            if (expected_policy_path and self._compute_path(placement_policy_id) != expected_policy_path) or (not expected_policy_path and placement_policy_id):
                raise GcpProviderError("compact placement policy identity differs from the selected scenario", category="placement_missing")
            self._save(attempt, journal)
            observed = {}
            for role, template in templates.items():
                template["zone"] = deployment.vm_a.zone if role == "vm_a" else deployment.vm_b.zone
                template["expected_policy"] = placement_policy_id if deployment.scenario == "placement_optimization" else None
                described = self._gcloud_json(["compute", "instance-templates", "describe", template["name"], "--project", self.settings.project_id, "--format=json"], f"gcp-template-read-{role}")
                self._validate_template(described, template, role, deployment, trigger)
                scheduling = described.get("properties", {}).get("scheduling", {})
                observed[role] = {"id": str(template["id"]), "name": template["name"], "zone": template["zone"], "expected_policy": template["expected_policy"], "self_link_unique": template["self_link_unique"], "sha256": hashlib.sha256(json.dumps(described, sort_keys=True).encode()).hexdigest(), "creationTimestamp": described.get("creationTimestamp"), "scheduling": scheduling}
            now = self.clock()
            if now.tzinfo is None or now.utcoffset() is None:
                raise GcpProviderError("registration clock must be timezone-aware", category="cap_order")
            now = now.astimezone(timezone.utc)
            self._fit(deployment, reservation, trigger, self._remaining_workflow_seconds(deployment, "registration"))
            if not reservation.cap_started_at.value <= now <= trigger:
                raise GcpProviderError("expiry registration is outside the original reservation window", category="cap_order")
            cap_start = reservation.cap_started_at.value.astimezone(timezone.utc)
            for item in observed.values():
                self._validate_template_created_at(item.get("creationTimestamp"), cap_start, now)
            evidence = ExpiryEvidence(schema_version=2, run_id=deployment.run_id, deployment_id=deployment.deployment_id, provider="gcp", action="stop", state="registered", registration_id=Evidence[str](value=f"{observed['vm_a']['id']}:{observed['vm_b']['id']}"), registered_at=Evidence[datetime](value=now), expires_at=Evidence[datetime](value=trigger), target_selector=request.target_selector, provider_metadata=StructuredEvidence(value={"templates": observed, "reservation_id": reservation.reservation_id.value, "cost_source_id": self.settings.cost_input_id, "cost_source_sha256": self.settings.cost_input_sha256, "native_absolute_stop": True}))
            journal.update({"registered": True, "expiry": evidence.model_dump(mode="json"), "templates": observed, "protection_cleanup_state": None})
            self._save(attempt, journal)
            return evidence
        except BaseException as exc:
            journal["registration_failure"] = self._failure(exc).model_dump(mode="json")
            try:
                self._terraform(attempt, "protection", "destroy", deployment, {})
                journal["unused_protection_destroy_attempted"] = True
                inventory = self._inventory(deployment)
                if not inventory.get("pages_complete") or inventory.get("resources"):
                    raise GcpProviderError("unused protection cleanup did not prove complete resource absence", category="cleanup_incomplete")
                journal["unused_protection_inventory"] = inventory
                journal["protection_cleanup_state"] = "succeeded"
            except BaseException as cleanup_error:
                journal["cleanup_failure"] = self._failure(cleanup_error).model_dump(mode="json")
                journal["protection_cleanup_state"] = "failed"
                journal["launch_blocked"] = True
            self._save(attempt, journal)
            if not isinstance(exc, Exception):
                raise
            return ExpiryEvidence(schema_version=2, run_id=deployment.run_id, deployment_id=deployment.deployment_id, provider="gcp", action="stop", state="failed", registration_id=Evidence[str](unavailable_reason="both immutable templates were not confirmed"), registered_at=Evidence[datetime](unavailable_reason="registration incomplete"), expires_at=Evidence[datetime](value=trigger), target_selector=request.target_selector, provider_metadata=StructuredEvidence(value={"partial_protection": journal.get("templates", {}) or {}}), failure=self._failure(exc))

    def provision(self, deployment: ProviderDeploymentInput) -> ProviderDeploymentOutput:
        self._deadline = None
        self._validate_deployment(deployment)
        self._validate_selected_images(deployment)
        attempt = self._attempt_dir(deployment)
        self._active_attempt = attempt
        journal = self._load(attempt, deployment)
        if journal.get("registered") and journal.get("controller_session_id") != CONTROLLER_SESSION_ID:
            raise GcpProviderError("an earlier controller session cannot provision this registered attempt; resolve it through cleanup", category="cleanup_open")
        if not journal.get("registered"):
            raise GcpProviderError("verified STOP templates are required before pair creation", category="expiry_required")
        if journal.get("creation_may_have_started") or journal.get("deployment_output") or journal.get("cleanup_state") is not None or journal.get("launch_blocked"):
            raise GcpProviderError("this attempt has already started creation or entered cleanup and cannot be relaunched", category="cleanup_open")
        supplied_expiry = deployment.expiry.value
        recorded_expiry = ExpiryEvidence.model_validate(journal["expiry"])
        if supplied_expiry is None or supplied_expiry.state != "registered" or supplied_expiry.registration_id != recorded_expiry.registration_id or supplied_expiry.expires_at != recorded_expiry.expires_at:
            raise GcpProviderError("deployment handoff must carry the matching registered STOP evidence", category="expiry_mismatch")
        reservation = deployment.reservation.value
        if reservation is None or reservation.state != "reserved" or journal.get("binding_sha256") != self._binding_fingerprint(deployment):
            raise GcpProviderError("provision inputs differ from the immutable reserved registration", category="state_identity_mismatch")
        if reservation.reservation_id.value != journal.get("reservation_id") or reservation.source_sha256 != self.settings.cost_input_sha256 or reservation.source_path != self.settings.cost_source_path:
            raise GcpProviderError("reservation, project or cost-source identity changed after registration", category="state_identity_mismatch")
        self._assert_no_open_cleanup(deployment)
        trigger = reservation.expires_at.value - timedelta(seconds=provider_lead_seconds(
            deployment.admission.workflow_profile, "gcp", SAFETY_LEAD_SECONDS))
        self._fit(deployment, reservation, trigger, self._remaining_workflow_seconds(deployment, "preparation"))
        self._verify_registered_templates(journal, trigger, deployment)
        if deployment.scenario in {"cross_zone", "inter_region"}:
            self._validate_review(deployment)
        self._fit(deployment, reservation, trigger, self._remaining_workflow_seconds(deployment, "preparation"))
        self._provider_stop_deadline = time.monotonic() + max(0.0,
            (trigger - self.clock().astimezone(timezone.utc)).total_seconds())
        if self.settings.workflow_cap_started_monotonic is not None:
            from ..terraform_plan import workflow_review_stage_deadline
            self._deadline = workflow_review_stage_deadline(
                cap_started_monotonic=self.settings.workflow_cap_started_monotonic,
                registration_allowance_seconds=self.settings.registration_allowance_seconds,
                provision_allowance_seconds=self.settings.provision_allowance_seconds,
                stage="provision", overall_deadline=self.settings.workflow_cap_started_monotonic
                    + self.settings.complete_workflow_remaining_seconds,
                stop_deadline=self._provider_stop_deadline)
        else:
            self._deadline = min(time.monotonic() + (deployment.provisioning_timeout_seconds
                or self.settings.registration_timeout_seconds), self._provider_stop_deadline)
        journal["creation_may_have_started"] = True
        self._save(attempt, journal)
        started = self.clock().astimezone(timezone.utc)
        try:
            templates = journal["templates"]
            self._fit(deployment, reservation, trigger, self._remaining_workflow_seconds(deployment, "preparation"))
            pair = self._terraform(attempt, "pair", "apply", deployment, {"template_a": templates["vm_a"]["self_link_unique"], "template_b": templates["vm_b"]["self_link_unique"]})
            values = self._outputs(pair).get("instances", {})
            if set(values) != {"vm_a", "vm_b"} or len({str(item.get("id")) for item in values.values()}) != 2 or len({item.get("name") for item in values.values()}) != 2:
                raise GcpProviderError("pair apply must return exactly two distinct role identities", category="instance_identity_mismatch")
            output = {}
            observed_disks = {}
            for role in ("vm_a", "vm_b"):
                resource = values[role]
                instance = self._gcloud_json(["compute", "instances", "describe", resource["name"], "--zone", resource["zone"], "--project", self.settings.project_id, "--format=json"], f"gcp-instance-read-{role}")
                current = self._load(attempt, deployment)
                current.setdefault("observed_resources", {})[role] = {"id": str(instance.get("id")), "name": instance.get("name"), "zone": instance.get("zone", "").rsplit("/", 1)[-1], "disk_sources": [disk.get("source") for disk in instance.get("disks", [])]}
                self._save(attempt, current)
                if str(instance.get("id")) != str(resource.get("id")) or instance.get("name") != resource.get("name") or instance.get("zone", "").rsplit("/", 1)[-1] != resource.get("zone"):
                    raise GcpProviderError("observed VM does not match Terraform's role-specific numeric identity", category="instance_identity_mismatch")
                expected_vm_path=f"projects/{self.settings.project_id}/zones/{resource['zone']}/instances/{resource['name']}"
                actual_vm_path=self._compute_path(instance.get("selfLink"))
                output_vm_path=self._compute_path(resource.get("self_link"))
                if not re.fullmatch(r"[1-9][0-9]*",str(instance.get("id",""))) or actual_vm_path != expected_vm_path or output_vm_path != expected_vm_path:
                    raise GcpProviderError("observed VM self link is outside the selected project, zone, or Terraform output", category="instance_identity_mismatch")
                scheduling = instance.get("scheduling", {})
                if scheduling.get("provisioningModel") != "STANDARD" or scheduling.get("instanceTerminationAction") != "STOP" or scheduling.get("terminationTime") != trigger.isoformat().replace("+00:00", "Z") or scheduling.get("automaticRestart") is not False or "maxRunDuration" in scheduling:
                    raise GcpProviderError("observed VM does not inherit absolute STOP scheduling", category="instance_policy_mismatch")
                observed_disks[role] = self._validate_instance(instance, deployment, role)
                policies = instance.get("resourcePolicies", [])
                expected_policy = self._selected_policy_path(deployment)
                policy_paths = [self._compute_path(policy) for policy in policies] if isinstance(policies, list) else None
                if policy_paths != ([expected_policy] if expected_policy else []):
                    raise GcpProviderError("observed placement policy does not match selected scenario", category="placement_mismatch")
                host_key = self._trusted_hostkey(resource["name"], resource["zone"], instance)
                resource["_trusted_host_key"] = host_key
                output[role] = instance
            vms = [self._vm_output(role, output[role], deployment) for role in ("vm_a", "vm_b")]
            finished = self.clock().astimezone(timezone.utc)
            result = ProviderDeploymentOutput(schema_version=2, identity=IdentityContext(experiment_id=deployment.experiment_id, config_id=deployment.config_id, campaign_id=deployment.campaign_id, observation_id=deployment.observation_id, run_id=deployment.run_id, deployment_id=deployment.deployment_id, provider="gcp"), run_id=deployment.run_id, deployment_id=deployment.deployment_id, mode=deployment.mode, provider="gcp", scenario=deployment.scenario, apply_action_id="gcp-pair-apply", started_at=started, finished_at=finished, vm_a=vms[0], vm_b=vms[1], provider_metadata=StructuredEvidence(value={"template_bindings": {role: data["self_link_unique"] for role, data in templates.items()}, "readback": "instances.get", "no_source_template_response_claim": True}), documented_network_limit=StructuredEvidence(value=NETWORK_LIMIT), expiry=Evidence[ExpiryEvidence](value=ExpiryEvidence.model_validate(journal["expiry"])))
            self._write_private(attempt / "terraform-outputs.redacted.json", json.dumps(result.model_dump(mode="json"), sort_keys=True, indent=2).encode())
            journal["deployment_output"] = result.model_dump(mode="json")
            journal["observed_resources"] = {role: {"id": output[role].get("id"), "name": output[role].get("name"), "zone": output[role].get("zone", "").rsplit("/", 1)[-1], "disk_sources": [disk.get("source") for disk in output[role].get("disks", [])], "private_ip": output[role].get("networkInterfaces", [{}])[0].get("networkIP")} for role in ("vm_a", "vm_b")}
            journal["observed_disks"] = [{"id": str(disk.get("id")), "name": disk.get("name"), "zone": disk.get("zone", "").rsplit("/", 1)[-1]} for disk in observed_disks.values()]
            self._save(attempt, journal)
            return result
        except BaseException as exc:
            journal["primary_failure"] = self._failure(exc).model_dump(mode="json")
            self._save(attempt, journal)
            try:
                self.cleanup(deployment)
            except Exception as cleanup_error:
                journal["cleanup_failure"] = self._failure(cleanup_error).model_dump(mode="json")
                journal["launch_blocked"] = True
                self._save(attempt, journal)
            raise

    def cleanup(self, deployment: ProviderDeploymentInput) -> GcpCleanupResult:
        self._deadline = None
        self._cleanup_deadline_utc = None
        self._cleanup_absence_deadline_utc = None
        self._cleanup_routine_deadline_utc = None
        self._cleanup_clock_binding = None
        self._cleanup_acceptance_deadlines = []
        attempt = self._attempt_dir(deployment)
        self._active_attempt = attempt
        journal = self._load(attempt, deployment)
        if journal.get("binding_sha256") and journal["binding_sha256"] != self._binding_fingerprint(deployment):
            failure = self._failure(GcpProviderError(
                "cleanup caller differs from the immutable registered project/source/cap/settings",
                category="state_identity_mismatch"))
            return GcpCleanupResult(cleanup_state=CleanupState.FAILED,
                cleanup_failure=failure,
                primary_failure=FailureEvidence.model_validate(journal["primary_failure"]) if journal.get("primary_failure") else None,
                inventory=StructuredEvidence(value={"known": [], "pages_complete": False}),
                expiry=Evidence[ExpiryEvidence](value=ExpiryEvidence.model_validate(journal["expiry"]))
                    if journal.get("expiry") else Evidence[ExpiryEvidence](unavailable_reason="no native expiry was registered"))
        if journal.get("cleanup_state") == "succeeded":
            return GcpCleanupResult(cleanup_state=CleanupState.SUCCEEDED, inventory=StructuredEvidence(value=journal["inventory"]), expiry=Evidence[ExpiryEvidence](value=ExpiryEvidence.model_validate(journal["expiry"])) if journal.get("expiry") else Evidence[ExpiryEvidence](unavailable_reason="no native expiry was registered"), primary_failure=FailureEvidence.model_validate(journal["primary_failure"]) if journal.get("primary_failure") else None)
        cleanup_now = self.clock()
        deadline_required = bool(journal.get("creation_may_have_started")
            or journal.get("registered") or journal.get("registration_may_have_started"))
        try:
            if cleanup_now.tzinfo is None or cleanup_now.utcoffset() is None:
                raise GcpProviderError("cleanup UTC clock must be timezone-aware", category="cleanup_deadline_unknown")
            cleanup_now = cleanup_now.astimezone(timezone.utc)
            cleanup_deadline = (self._prepare_cleanup_deadline(attempt, journal, deployment, cleanup_now)
                if deadline_required else None)
            absence_fields = ("cleanup_absence_started_at", "cleanup_absence_deadline_utc",
                "cleanup_absence_started_monotonic", "cleanup_absence_deadline_monotonic")
            if any(field in journal for field in absence_fields):
                if not deployment.admission.workflow_profile:
                    raise GcpProviderError("saved cleanup absence phase has no profile identity", category="cleanup_deadline_unknown")
                from ..validation_profiles import provider_lead_seconds
                lead = provider_lead_seconds(deployment.admission.workflow_profile, "gcp", SAFETY_LEAD_SECONDS)
                absence_end = self._prepare_absence_deadline(attempt, journal, deployment, lead)
                self._cleanup_absence_deadline_utc = absence_end
                absence_remaining = (absence_end-cleanup_now).total_seconds()
                overall_remaining = (cleanup_deadline-cleanup_now).total_seconds()
                self._deadline = min(float(journal["cleanup_deadline_monotonic"]),
                    time.monotonic()+overall_remaining,
                    float(journal["cleanup_absence_deadline_monotonic"]),
                    time.monotonic()+max(0.0, absence_remaining))
                self._save(attempt, journal)
        except Exception as exc:
            failure = self._failure(exc)
            inventory = {"known": [], "pages_complete": False}
            journal.update({"cleanup_state": "failed", "launch_blocked": True,
                "cleanup_failure": failure.model_dump(mode="json"), "inventory": inventory})
            self._save(attempt, journal)
            expiry = journal.get("expiry")
            return GcpCleanupResult(cleanup_state=CleanupState.FAILED, cleanup_failure=failure,
                primary_failure=FailureEvidence.model_validate(journal["primary_failure"]) if journal.get("primary_failure") else None,
                inventory=StructuredEvidence(value=inventory),
                expiry=Evidence[ExpiryEvidence](value=ExpiryEvidence.model_validate(expiry)) if expiry else Evidence[ExpiryEvidence](unavailable_reason="no native expiry was registered"))
        if deployment.admission.workflow_profile and cleanup_deadline is not None:
            started_utc = datetime.fromisoformat(journal["cleanup_started_at"].replace("Z", "+00:00")).astimezone(timezone.utc)
            reservation_end = deployment.reservation.value.expires_at.value.astimezone(timezone.utc)
            from ..validation_profiles import provider_lead_seconds
            lead = provider_lead_seconds(deployment.admission.workflow_profile, "gcp", SAFETY_LEAD_SECONDS)
            routine_acceptance_end = min(started_utc + timedelta(seconds=600), reservation_end-timedelta(seconds=lead+60))
            # The reservation/STOP boundary classifies cleanup timeliness. It
            # cannot consume the independent recovery allowance for deletion.
            routine_end = min(started_utc + timedelta(seconds=600), cleanup_deadline)
            routine_mono = min(float(journal["cleanup_started_monotonic"])+600.0,
                float(journal["cleanup_deadline_monotonic"]))
            saved_routine_utc = journal.get("cleanup_routine_deadline_utc")
            saved_routine_mono = journal.get("cleanup_routine_deadline_monotonic")
            if saved_routine_utc is not None or saved_routine_mono is not None:
                if saved_routine_utc is None or saved_routine_mono is None:
                    raise GcpProviderError("original cleanup routine deadline is incomplete", category="cleanup_deadline_unknown")
                try:
                    parsed_routine_utc = datetime.fromisoformat(str(saved_routine_utc).replace("Z", "+00:00"))
                except ValueError as exc:
                    raise GcpProviderError("original cleanup routine deadline is malformed", category="cleanup_deadline_unknown") from exc
                if (parsed_routine_utc.tzinfo is None or parsed_routine_utc.utcoffset() is None
                        or parsed_routine_utc.astimezone(timezone.utc) != routine_end
                        or isinstance(saved_routine_mono, bool) or not isinstance(saved_routine_mono, (int, float))
                        or not isfinite(float(saved_routine_mono)) or abs(float(saved_routine_mono)-routine_mono) > 0.01):
                    raise GcpProviderError("original cleanup routine deadline changed", category="cleanup_deadline_invalid")
            else:
                journal["cleanup_routine_deadline_utc"] = routine_end.isoformat()
                journal["cleanup_routine_deadline_monotonic"] = routine_mono
            if self._cleanup_absence_deadline_utc is None:
                self._cleanup_routine_deadline_utc = routine_end
                self._deadline = min(self._deadline, routine_mono)
            self._cleanup_acceptance_deadlines = [routine_acceptance_end]
            if self._cleanup_absence_deadline_utc is not None:
                absence_started_utc = datetime.fromisoformat(
                    journal["cleanup_absence_started_at"].replace("Z", "+00:00")).astimezone(timezone.utc)
                self._cleanup_acceptance_deadlines.append(min(
                    absence_started_utc+timedelta(seconds=60),
                    reservation_end-timedelta(seconds=lead)))
            previous_timing = journal.get("cleanup_acceptance_timing", {})
            timing_status = previous_timing.get("status")
            if timing_status not in {"late", "unknown"}:
                timing_status = "late" if cleanup_now >= routine_acceptance_end else "within_deadline"
            journal["cleanup_acceptance_timing"] = {
                "routine_deadline_utc": routine_acceptance_end.isoformat(),
                "status": timing_status,
            }
            self._save(attempt, journal)
        failure = None
        inventory: dict[str, Any] = {"known": [], "pages_complete": False}
        try:
            if journal.get("creation_may_have_started"):
                self._terraform(attempt, "pair", "destroy", deployment, {})
            inventory = self._inventory(deployment)
            if inventory["instances"]:
                raise GcpProviderError("owned compute remains; protection must stay installed", category="residual_compute")
            if journal.get("registered") or journal.get("registration_may_have_started"):
                self._terraform(attempt, "protection", "destroy", deployment, {})
            if deployment.admission.workflow_profile and (journal.get("registered") or journal.get("registration_may_have_started")):
                from ..validation_profiles import provider_lead_seconds
                lead = provider_lead_seconds(deployment.admission.workflow_profile, "gcp", SAFETY_LEAD_SECONDS)
                if self.clock().astimezone(timezone.utc) >= self._cleanup_acceptance_deadlines[0]:
                    journal["cleanup_acceptance_timing"]["status"] = "late"
                absence_end = self._prepare_absence_deadline(attempt, journal, deployment, lead)
                absence_started_utc = datetime.fromisoformat(
                    journal["cleanup_absence_started_at"].replace("Z", "+00:00")).astimezone(timezone.utc)
                absence_acceptance_end = min(absence_started_utc+timedelta(seconds=60),
                    deployment.reservation.value.expires_at.value.astimezone(timezone.utc)-timedelta(seconds=lead))
                self._cleanup_acceptance_deadlines.append(absence_acceptance_end)
                self._cleanup_absence_deadline_utc = absence_end
                if self.clock().astimezone(timezone.utc) >= absence_acceptance_end:
                    journal["cleanup_acceptance_timing"]["status"] = "late"
                remaining_utc = (absence_end-self.clock().astimezone(timezone.utc)).total_seconds()
                absence_mono = journal["cleanup_absence_deadline_monotonic"]
                overall_deadline_utc = datetime.fromisoformat(journal["cleanup_deadline_utc"].replace("Z", "+00:00")).astimezone(timezone.utc)
                overall_remaining = (overall_deadline_utc-self.clock().astimezone(timezone.utc)).total_seconds()
                self._deadline = min(float(journal["cleanup_deadline_monotonic"]),
                    time.monotonic()+overall_remaining, float(absence_mono),
                    time.monotonic()+max(0.0, remaining_utc))
                self._save(attempt, journal)
            inventory = self._inventory(deployment)
            if not inventory["pages_complete"] or inventory["resources"]:
                raise GcpProviderError("owned or unknown resources remain after cleanup", category="cleanup_incomplete")
            expiry = journal.get("expiry")
            if expiry and expiry.get("state") == "registered":
                expires_at = datetime.fromisoformat(expiry["expires_at"]["value"].replace("Z", "+00:00")).astimezone(timezone.utc)
                now = self.clock()
                if now.tzinfo is None or now.utcoffset() is None:
                    raise GcpProviderError("cleanup clock must be timezone-aware", category="expiry_uncertain")
                metadata = {**expiry.get("provider_metadata", {}).get("value", {})}
                if now.astimezone(timezone.utc) < expires_at:
                    expiry = {**expiry, "state": "cancelled", "provider_metadata": {"value": {**metadata, "cancelled_after_full_deletion": True}}}
                else:
                    expiry = {**expiry, "provider_metadata": {"value": {**metadata, "trigger_due_at_cleanup": True, "trigger_outcome": "unverified", "policy_removed_after_full_deletion": True}}}
                journal["expiry"] = expiry
            timing = journal.get("cleanup_acceptance_timing")
            if timing and self._cleanup_acceptance_deadlines and self.clock().astimezone(timezone.utc) >= self._cleanup_acceptance_deadlines[-1]:
                timing["status"] = "late"
            if timing:
                inventory["cleanup_timing"] = timing
            result = GcpCleanupResult(cleanup_state=CleanupState.SUCCEEDED, inventory=StructuredEvidence(value=inventory), expiry=Evidence[ExpiryEvidence](value=ExpiryEvidence.model_validate(expiry)) if expiry else Evidence[ExpiryEvidence](unavailable_reason="no native expiry was registered"), primary_failure=FailureEvidence.model_validate(journal["primary_failure"]) if journal.get("primary_failure") else None)
            journal.update({"cleanup_state": "succeeded", "launch_blocked": False, "inventory": inventory})
            self._save(attempt, journal)
            return result
        except Exception as exc:
            failure = self._failure(exc)
            timing = journal.get("cleanup_acceptance_timing")
            deadlines = getattr(self, "_cleanup_acceptance_deadlines", [])
            if timing and deadlines and self.clock().astimezone(timezone.utc) >= deadlines[-1]:
                timing["status"] = "late"
            if timing:
                inventory["cleanup_timing"] = timing
            journal.update({"cleanup_state": "failed", "launch_blocked": True, "cleanup_failure": failure.model_dump(mode="json"), "inventory": inventory})
            self._save(attempt, journal)
            expiry = journal.get("expiry")
            return GcpCleanupResult(cleanup_state=CleanupState.FAILED, cleanup_failure=failure, primary_failure=FailureEvidence.model_validate(journal["primary_failure"]) if journal.get("primary_failure") else None, inventory=StructuredEvidence(value=inventory), expiry=Evidence[ExpiryEvidence](value=ExpiryEvidence.model_validate(expiry)) if expiry else Evidence[ExpiryEvidence](unavailable_reason="no native expiry was registered"))

    def _prepare_cleanup_deadline(self, attempt: Path, journal: dict[str, Any],
                                  deployment: ProviderDeploymentInput, now_utc: datetime) -> datetime:
        """Bind cleanup commands to one durable UTC and monotonic deadline."""
        if now_utc.tzinfo is None or now_utc.utcoffset() is None:
            raise GcpProviderError("cleanup UTC clock must be timezone-aware", category="cleanup_deadline_unknown")
        now_utc = now_utc.astimezone(timezone.utc)
        now_mono = time.monotonic()
        if not isfinite(now_mono) or now_mono < 0:
            raise GcpProviderError("cleanup monotonic clock is unavailable", category="cleanup_deadline_unknown")
        clock_binding = round(now_utc.timestamp()-now_mono, 3)
        started_raw = journal.get("cleanup_started_at")
        if started_raw is None:
            started_utc = now_utc
            journal["cleanup_started_at"] = started_utc.isoformat()
            journal["cleanup_started_monotonic"] = now_mono
            journal["cleanup_clock_binding"] = clock_binding
        else:
            try:
                started_utc = datetime.fromisoformat(str(started_raw).replace("Z", "+00:00"))
            except ValueError as exc:
                raise GcpProviderError("original cleanup start time is malformed", category="cleanup_deadline_unknown") from exc
            if started_utc.tzinfo is None or started_utc.utcoffset() is None:
                raise GcpProviderError("original cleanup start time has no UTC offset", category="cleanup_deadline_unknown")
            started_utc = started_utc.astimezone(timezone.utc)
            if started_utc > now_utc:
                raise GcpProviderError("original cleanup start is in the future; UTC may have moved backward", category="cleanup_clock_changed")
            started_mono = journal.get("cleanup_started_monotonic")
            saved_binding = journal.get("cleanup_clock_binding")
            if (isinstance(started_mono, bool) or not isinstance(started_mono, (int, float))
                    or not isfinite(float(started_mono)) or not isinstance(saved_binding, (int, float))
                    or not isfinite(float(saved_binding))):
                raise GcpProviderError("original cleanup monotonic and clock identity anchors are unavailable", category="cleanup_deadline_unknown")
            if abs(clock_binding-float(saved_binding)) > 2.0:
                raise GcpProviderError("cleanup UTC/monotonic clock identity changed", category="cleanup_clock_changed")
            elapsed_utc = (now_utc-started_utc).total_seconds()
            elapsed_mono = now_mono-float(started_mono)
            if elapsed_utc < 0 or elapsed_mono < 0 or abs(elapsed_utc-elapsed_mono) > 2.0:
                raise GcpProviderError("cleanup UTC and monotonic elapsed time disagree", category="cleanup_clock_changed")
        timeout_seconds = deployment.cleanup_timeout_seconds or (
            660 if deployment.admission.workflow_profile else self.settings.registration_timeout_seconds)
        if not isfinite(float(timeout_seconds)) or timeout_seconds <= 0:
            raise GcpProviderError("original cleanup allowance is unavailable", category="cleanup_deadline_unknown")
        original_end = started_utc + timedelta(seconds=float(timeout_seconds))
        existing_end = journal.get("cleanup_deadline_utc")
        if existing_end is not None:
            try:
                deadline_utc = datetime.fromisoformat(str(existing_end).replace("Z", "+00:00"))
            except ValueError as exc:
                raise GcpProviderError("original cleanup deadline is malformed", category="cleanup_deadline_unknown") from exc
            if deadline_utc.tzinfo is None or deadline_utc.utcoffset() is None:
                raise GcpProviderError("original cleanup deadline has no UTC offset", category="cleanup_deadline_unknown")
            deadline_utc = deadline_utc.astimezone(timezone.utc)
            if deadline_utc > original_end:
                raise GcpProviderError("saved cleanup deadline exceeds its original cap", category="cleanup_deadline_invalid")
        else:
            deadline_utc = original_end
            journal["cleanup_deadline_utc"] = deadline_utc.isoformat()
        owner_binding = journal.get("binding_sha256")
        if "cleanup_deadline_binding_sha256" in journal and journal["cleanup_deadline_binding_sha256"] != owner_binding:
            raise GcpProviderError("cleanup deadline is bound to another attempt identity", category="state_identity_mismatch")
        journal["cleanup_deadline_binding_sha256"] = owner_binding
        remaining_utc = (deadline_utc-now_utc).total_seconds()
        if not isfinite(remaining_utc) or remaining_utc <= 0:
            raise GcpProviderError("original cumulative cleanup deadline elapsed", category="command_timeout")
        saved_mono = journal.get("cleanup_deadline_monotonic")
        if saved_mono is None:
            if started_raw is not None:
                raise GcpProviderError("original cleanup monotonic deadline was not persisted", category="cleanup_deadline_unknown")
            saved_mono = now_mono + remaining_utc
            journal["cleanup_deadline_monotonic"] = saved_mono
        if isinstance(saved_mono, bool) or not isinstance(saved_mono, (int, float)) or not isfinite(float(saved_mono)) or float(saved_mono) <= now_mono:
            raise GcpProviderError("original cumulative monotonic cleanup deadline elapsed or is unknown", category="command_timeout")
        # Recheck UTC on every resume and never extend the first monotonic cap.
        self._deadline = min(float(saved_mono), now_mono + remaining_utc)
        self._cleanup_deadline_utc = deadline_utc
        self._cleanup_clock_binding = float(journal["cleanup_clock_binding"])
        if self._deadline <= now_mono:
            raise GcpProviderError("original cumulative cleanup deadline elapsed", category="command_timeout")
        self._save(attempt, journal)
        return deadline_utc

    def _prepare_absence_deadline(self, attempt: Path, journal: dict[str, Any],
                                  deployment: ProviderDeploymentInput, lead: float) -> datetime:
        now_utc = self.clock()
        if now_utc.tzinfo is None or now_utc.utcoffset() is None:
            raise GcpProviderError("cleanup UTC clock must be timezone-aware", category="cleanup_deadline_unknown")
        now_utc = now_utc.astimezone(timezone.utc)
        now_mono = time.monotonic()
        if not isfinite(now_mono) or now_mono < 0:
            raise GcpProviderError("cleanup absence monotonic clock is unavailable", category="cleanup_deadline_unknown")
        clock_binding = round(now_utc.timestamp()-now_mono, 3)
        started_raw = journal.get("cleanup_absence_started_at")
        if started_raw is None:
            started_utc = now_utc
            journal["cleanup_absence_started_at"] = started_utc.isoformat()
            journal["cleanup_absence_started_monotonic"] = now_mono
            journal["cleanup_absence_clock_binding"] = clock_binding
        else:
            try:
                started_utc = datetime.fromisoformat(str(started_raw).replace("Z", "+00:00"))
            except ValueError as exc:
                raise GcpProviderError("original cleanup absence time is malformed", category="cleanup_deadline_unknown") from exc
            if started_utc.tzinfo is None or started_utc.utcoffset() is None:
                raise GcpProviderError("original cleanup absence time has no UTC offset", category="cleanup_deadline_unknown") from None
            started_utc = started_utc.astimezone(timezone.utc)
            if started_utc > now_utc:
                raise GcpProviderError("cleanup absence start is in the future; UTC may have moved backward", category="cleanup_clock_changed")
            started_mono = journal.get("cleanup_absence_started_monotonic")
            saved_binding = journal.get("cleanup_absence_clock_binding")
            if (isinstance(started_mono, bool) or not isinstance(started_mono, (int, float))
                    or not isfinite(float(started_mono)) or not isinstance(saved_binding, (int, float))
                    or not isfinite(float(saved_binding))):
                raise GcpProviderError("cleanup absence monotonic and clock identity anchors are unavailable", category="cleanup_deadline_unknown")
            if abs(clock_binding-float(saved_binding)) > 2.0:
                raise GcpProviderError("cleanup absence clock identity changed", category="cleanup_clock_changed")
            elapsed_utc = (now_utc-started_utc).total_seconds()
            elapsed_mono = now_mono-float(started_mono)
            if elapsed_utc < 0 or elapsed_mono < 0 or abs(elapsed_utc-elapsed_mono) > 2.0:
                raise GcpProviderError("cleanup absence UTC and monotonic elapsed time disagree", category="cleanup_clock_changed")
        raw_overall_end = journal.get("cleanup_deadline_utc")
        if raw_overall_end is None:
            raise GcpProviderError("original cumulative cleanup deadline is unavailable for absence verification", category="cleanup_deadline_unknown")
        try:
            overall_end = datetime.fromisoformat(str(raw_overall_end).replace("Z", "+00:00"))
        except ValueError as exc:
            raise GcpProviderError("original cumulative cleanup deadline is malformed", category="cleanup_deadline_unknown") from exc
        if overall_end.tzinfo is None or overall_end.utcoffset() is None:
            raise GcpProviderError("original cumulative cleanup deadline has no UTC offset", category="cleanup_deadline_unknown")
        candidate = min(started_utc + timedelta(seconds=60), overall_end.astimezone(timezone.utc))
        raw_end = journal.get("cleanup_absence_deadline_utc")
        if raw_end is not None:
            try:
                end = datetime.fromisoformat(str(raw_end).replace("Z", "+00:00"))
            except ValueError as exc:
                raise GcpProviderError("original cleanup absence deadline is malformed", category="cleanup_deadline_unknown") from exc
            if end.tzinfo is None or end.utcoffset() is None or end.astimezone(timezone.utc) > candidate:
                raise GcpProviderError("saved cleanup absence deadline exceeds its original cap", category="cleanup_deadline_invalid")
            end = end.astimezone(timezone.utc)
        else:
            end = candidate
            journal["cleanup_absence_deadline_utc"] = end.isoformat()
        remaining_utc = (end-now_utc).total_seconds()
        saved_mono = journal.get("cleanup_absence_deadline_monotonic")
        if saved_mono is None:
            if started_raw is not None:
                raise GcpProviderError("original cleanup absence monotonic deadline was not persisted", category="cleanup_deadline_unknown")
            saved_mono = now_mono + max(0.0, remaining_utc)
            journal["cleanup_absence_deadline_monotonic"] = saved_mono
        if isinstance(saved_mono, bool) or not isinstance(saved_mono, (int, float)) or not isfinite(float(saved_mono)):
            raise GcpProviderError("original cleanup absence monotonic deadline is unknown", category="cleanup_deadline_unknown")
        if remaining_utc <= 0 or float(saved_mono) <= now_mono:
            raise GcpProviderError("original cleanup absence deadline elapsed", category="command_timeout")
        return end

    def _check_cleanup_deadline(self, action: str) -> None:
        deadline_utc = self._cleanup_deadline_utc
        if action not in GCP_CLEANUP_COMMAND_IDS or deadline_utc is None:
            return
        now_utc = self.clock()
        now_mono = time.monotonic()
        if (now_utc.tzinfo is None or now_utc.utcoffset() is None or not isfinite(now_mono)
                or now_mono < 0 or self._deadline is None or not isfinite(self._deadline)):
            raise GcpProviderError("cleanup UTC/monotonic deadline anchor is unavailable",
                category="cleanup_deadline_unknown", action_id=action)
        now_utc = now_utc.astimezone(timezone.utc)
        binding = self._cleanup_clock_binding
        if binding is None or abs(round(now_utc.timestamp()-now_mono, 3)-binding) > 2.0:
            raise GcpProviderError("cleanup UTC/monotonic clock identity changed",
                category="cleanup_clock_changed", action_id=action)
        deadlines = [deadline_utc]
        if self._cleanup_absence_deadline_utc is not None:
            deadlines.append(self._cleanup_absence_deadline_utc)
        elif self._cleanup_routine_deadline_utc is not None:
            deadlines.append(self._cleanup_routine_deadline_utc)
        remaining = min((deadline-now_utc).total_seconds() for deadline in deadlines)
        if not isfinite(remaining) or remaining <= 0 or self._deadline <= now_mono:
            raise GcpProviderError("original cumulative cleanup deadline elapsed",
                category="command_timeout", action_id=action)
        self._deadline = min(self._deadline, now_mono+remaining)

    def verify_unstarted_absence(self, deployment: ProviderDeploymentInput) -> dict[str, Any]:
        """Read-only all-scope inventory for a source-proved pre-registration failure."""
        self._active_attempt = None
        original_settings = self.settings
        # Provider private state must remain untouched. Use the existing
        # controller directory as the subprocess cwd for read-only gcloud calls.
        self.settings = self.settings.model_copy(update={"private_state_root": Path.cwd()})
        self._deadline = time.monotonic() + max(1.0, float(self.settings.registration_timeout_seconds))
        try:
            inventory = self._inventory(deployment, journal={})
        finally:
            self.settings = original_settings
        complete = (inventory.get("pages_complete") is True and not inventory.get("resources")
            and all(not inventory.get(name) for name in ("instances", "disks", "addresses", "support_resources")))
        return {"provider": "gcp", "verified": complete,
            "target_selector": f"project={self.settings.project_id};run_id={deployment.run_id};deployment_id={deployment.deployment_id};roles=vm_a,vm_b",
            "inventory": inventory, "query_count": len(getattr(self, "_active_commands", ())),
            "timing_status": "late" if self.clock().astimezone(timezone.utc)
                >= deployment.reservation.value.expires_at.value else "unknown"}

    def _validate(self, deployment: ProviderDeploymentInput, request: ExpiryRequest) -> ReservationEvidence:
        self._validate_deployment(deployment)
        self._validate_selected_images(deployment)
        reservation = deployment.reservation.value
        if reservation is None or reservation.state != "reserved":
            raise GcpProviderError("active reservation evidence is required", category="reservation_required")
        trigger = reservation.expires_at.value - timedelta(seconds=provider_lead_seconds(
            deployment.admission.workflow_profile, "gcp", SAFETY_LEAD_SECONDS))
        if request.provider != "gcp" or request.action != "stop" or request.run_id != deployment.run_id or request.deployment_id != deployment.deployment_id:
            raise GcpProviderError("expiry request identity/action does not match GCP deployment", category="identity_mismatch")
        if request.reservation_id.value != reservation.reservation_id.value or request.expires_at.value != trigger:
            raise GcpProviderError("expiry must retain the original reservation cap minus the fixed lead", category="cap_mismatch")
        expected_selector = f"project={self.settings.project_id};run_id={deployment.run_id};deployment_id={deployment.deployment_id};roles=vm_a,vm_b"
        if request.resource_namespace != deployment.run_id or request.target_selector != expected_selector:
            raise GcpProviderError("expiry target must bind the selected project and attempt namespace", category="target_mismatch")
        if reservation.source_sha256 != self.settings.cost_input_sha256 or reservation.source_path != self.settings.cost_source_path:
            raise GcpProviderError("reservation and complete-workflow cost sources differ", category="cost_source_mismatch")
        if deployment.admission.cost_input_id != self.settings.cost_input_id or deployment.admission.cost_input_sha256.value != self.settings.cost_input_sha256:
            raise GcpProviderError("admission and reserved cost inputs must use the identified dated source", category="cost_source_mismatch")
        handoff = self.settings.complete_workflow_cost_handoff.value or {}
        try:
            reserved = float(reservation.reserved_cost_usd.value)
            estimate = float(handoff["estimated_total_usd"])
            checked = datetime.strptime(str(handoff["prices_checked_on"]), "%Y-%m-%d").date()
        except (KeyError, TypeError, ValueError) as exc:
            raise GcpProviderError("reservation or dated workflow cost evidence is incomplete", category="cost_source_mismatch") from exc
        if not isfinite(reserved) or not isfinite(estimate) or reserved < estimate:
            raise GcpProviderError("reservation does not cover the supplied complete workflow estimate", category="cost_undercovered")
        admission_estimate = deployment.admission.estimated_cost_usd.value
        if admission_estimate is not None and (not isfinite(admission_estimate) or reserved < admission_estimate):
            raise GcpProviderError("reservation does not cover the admitted complete workflow estimate", category="cost_undercovered")
        if reservation.campaign_id != deployment.campaign_id:
            raise GcpProviderError("reservation campaign does not match the deployment", category="reservation_mismatch")
        if (reservation.expires_at.value - reservation.cap_started_at.value).total_seconds() != deployment.admission.lifetime_cap_seconds:
            raise GcpProviderError("reservation cap duration does not match the admitted lifetime cap", category="cap_mismatch")
        if deployment.admission.prices_checked_on > self.clock().date():
            raise GcpProviderError("admission price-check date cannot be future-dated", category="cost_source_mismatch")
        if not reservation.cap_start_policy_id.value or not reservation.cap_start_policy_id.value.strip():
            raise GcpProviderError("source-identified cap-start policy is required", category="cap_mismatch")
        if checked > self.clock().date():
            raise GcpProviderError("dated cost source cannot be future-dated", category="cost_source_mismatch")
        self._assert_no_open_cleanup(deployment)
        return reservation

    @staticmethod
    def _validate_deployment(deployment: ProviderDeploymentInput) -> None:
        if deployment.provider != "gcp" or deployment.schema_version != 2 or deployment.provider_options != {"provisioning_model": "STANDARD", "instance_termination_action": "STOP"}:
            raise GcpProviderError("deployment must use schema v2 GCP STANDARD/STOP", category="deployment_invalid")
        northern = (deployment.vm_a.region == deployment.vm_b.region == "europe-north1"
                    and deployment.vm_a.zone == deployment.vm_b.zone == "europe-north1-c"
                    and deployment.scenario in {"same_zone", "placement_optimization"})
        northern_research = (northern and deployment.experiment_id in {"EXP-001", "EXP-003"}
                    and deployment.mode in {"short_study", "calibration", "diagnostic_day"} and deployment.context == "standard")
        northern_replacement = (northern and deployment.experiment_id in {"EXP-001", "EXP-003"}
                    and deployment.mode == "short_study" and deployment.context == "replacement"
                    and deployment.replaces_run_id is not None and deployment.replaces_run_id.endswith("-gcp"))
        northern_acceptance = (northern and deployment.mode == "acceptance"
                    and ((deployment.scenario == "same_zone" and deployment.experiment_id == "EXP-902" and deployment.context == "failure_check")
                         or (deployment.scenario == "same_zone" and deployment.experiment_id == "EXP-903" and deployment.context == "standard")
                         or (deployment.scenario == "placement_optimization" and deployment.experiment_id == "EXP-003" and deployment.context == "standard")))
        required_north = ((deployment.experiment_id in {"EXP-001", "EXP-003"}
                           and deployment.mode in {"short_study", "calibration", "diagnostic_day"})
                          or (deployment.mode == "acceptance" and
                              ((deployment.experiment_id == "EXP-903" and deployment.scenario == "same_zone")
                               or (deployment.experiment_id == "EXP-003" and deployment.scenario == "placement_optimization"))))
        if required_north and not (northern_research or northern_replacement or northern_acceptance):
            raise GcpProviderError("primary S1/S3 cannot fall back from Finland without a reviewed region-specific exception", category="scenario_unsupported")
        if northern and not (northern_research or northern_replacement or northern_acceptance):
            raise GcpProviderError("Finland locality is outside the registered S1/S3 research or acceptance scope", category="scenario_unsupported")
        if not northern and (deployment.vm_a.region != "europe-west3" or deployment.vm_b.region not in {"europe-west3", "europe-west2"}):
            raise GcpProviderError("only the selected baseline topology or Finland same-zone S1/S3 pair is supported", category="scenario_unsupported")
        if deployment.vm_a.vm_shape.lower() not in {"n2-standard-4", "n2_standard_4"} or deployment.vm_b.vm_shape.lower() not in {"n2-standard-4", "n2_standard_4"}:
            raise GcpProviderError("GCP roles must use selected n2-standard-4 shape", category="shape_unsupported")
        if deployment.vm_a.role != "vm_a" or deployment.vm_b.role != "vm_b" or deployment.vm_a.function != "client_traffic_generator" or deployment.vm_b.function != "server_receiver":
            raise GcpProviderError("GCP deployment requires exact client A and receiver B identities", category="identity_mismatch")
        expected_zones = {"same_zone": ("europe-west3-a", "europe-west3-a"), "cross_zone": ("europe-west3-a", "europe-west3-b"), "placement_optimization": ("europe-west3-a", "europe-west3-a"), "inter_region": ("europe-west3-a", "europe-west2-a")}
        if not northern and (deployment.scenario not in expected_zones or (deployment.vm_a.zone, deployment.vm_b.zone) != expected_zones[deployment.scenario]):
            raise GcpProviderError("scenario zones must match the selected V2 topology", category="scenario_unsupported")
        if deployment.scenario == "placement_optimization" and deployment.placement.kind != "compact_placement_policy":
            raise GcpProviderError("S3 requires same-zone compact placement", category="scenario_unsupported")
        if deployment.scenario == "placement_optimization" and (
                not deployment.placement.name or not re.fullmatch(r"[a-z]([-a-z0-9]{0,61}[a-z0-9])?", deployment.placement.name)):
            raise GcpProviderError("S3 requires a valid registered GCP compact placement policy name", category="placement_missing")
        if deployment.scenario == "inter_region" and deployment.vm_b.region != "europe-west2":
            raise GcpProviderError("S4 receiver must be in London", category="scenario_unsupported")

    def _validate_selected_images(self, deployment: ProviderDeploymentInput) -> None:
        selected = {deployment.vm_a.region, deployment.vm_b.region}
        if not selected <= self.settings.images.keys():
            raise GcpProviderError("selected GCP region has no pinned Ubuntu image", category="image_mismatch")

    def _validate_review(self, deployment: ProviderDeploymentInput) -> None:
        evidence = self.settings.primary_review_decision
        value = evidence.value if evidence else None
        scenario = getattr(deployment.scenario, "value", deployment.scenario)
        if scenario == "placement_optimization":
            return
        if not value or value.get("decision") != "approved" or value.get("scenario") != scenario:
            raise GcpProviderError("S2/S4 launch requires a sourced approved review for this scenario", category="review_required")
        if not value.get("source_path") or not re.fullmatch(r"[a-f0-9]{64}", str(value.get("source_sha256", ""))) or not value.get("reviewed_at"):
            raise GcpProviderError("review evidence requires a source path, SHA-256, and timestamp", category="review_required")

    def _verify_registered_templates(self, journal: dict[str, Any], trigger: datetime, deployment: ProviderDeploymentInput) -> None:
        templates = journal.get("templates", {})
        if set(templates) != {"vm_a", "vm_b"}:
            raise GcpProviderError("both registered template identities are required before pair creation", category="expiry_uncertain")
        identities = {(str(item.get("id")), item.get("self_link_unique")) for item in templates.values()}
        if len(identities) != 2 or any(not item[0] or not item[1] for item in identities):
            raise GcpProviderError("role templates must retain distinct immutable identities", category="expiry_uncertain")
        expected_time = trigger.isoformat().replace("+00:00", "Z")
        for role, item in templates.items():
            described = self._gcloud_json(["compute", "instance-templates", "describe", item["name"], "--project", self.settings.project_id, "--format=json"], f"gcp-template-precreate-{role}")
            digest = hashlib.sha256(json.dumps(described, sort_keys=True).encode()).hexdigest()
            self._validate_template(described, item, role, deployment, trigger)
            if digest != item["sha256"]:
                raise GcpProviderError("registered template identity or absolute STOP policy changed before creation", category="expiry_mismatch")

    def _validate_template(self, described: dict[str, Any], item: dict[str, Any], role: str, deployment: ProviderDeploymentInput | None, trigger: datetime) -> None:
        props = described.get("properties", {}) if isinstance(described, dict) else None
        if not isinstance(props, dict):
            self._template_mismatch(deployment, role, ["properties"], {"properties": props})
        if not isinstance(props.get("scheduling"), dict):
            self._template_mismatch(deployment, role, ["scheduling"], {"scheduling": props.get("scheduling")})
        scheduling = props["scheduling"]
        expected_name = f"{self._namespace(deployment)}-{role[-1]}"
        expected_region = deployment.vm_a.region if role == "vm_a" or deployment is None else deployment.vm_b.region
        expected_zone = deployment.vm_a.zone if role == "vm_a" or deployment is None else deployment.vm_b.zone
        expected_ip = {"same_zone": ("10.80.1.10", "10.80.1.11"), "cross_zone": ("10.80.1.10", "10.80.1.11"), "placement_optimization": ("10.80.1.10", "10.80.1.11"), "inter_region": ("10.80.1.10", "10.81.1.11")}
        role_index = 0 if role == "vm_a" else 1
        exp_image = self.settings.images[expected_region]
        exp_zone = expected_zone
        exp_ip = expected_ip[deployment.scenario][role_index]
        expected_network = f"projects/{self.settings.project_id}/global/networks/{self._namespace(deployment)}-net"
        expected_subnet = f"projects/{self.settings.project_id}/regions/{expected_region}/subnetworks/{self._namespace(deployment)}-" + ("b" if role == "vm_b" and deployment.scenario == "inter_region" else "a")
        if item.get("zone") != exp_zone:
            self._template_mismatch(deployment, role, ["identity.zone"], {"identity.zone": item.get("zone")})
        link = str(item.get("self_link_unique", ""))
        described_id = self._int64(described.get("id"))
        item_id = self._int64(item.get("id"))
        rest_path = self._compute_path(described.get("selfLink"))
        unique_path, unique_id = self._unique_compute_link(link)
        expected_link = f"projects/{self.settings.project_id}/global/instanceTemplates/{expected_name}"
        disk = props.get("disks", [])
        nics = props.get("networkInterfaces", [])
        raw_metadata = props.get("metadata", {})
        metadata_items = raw_metadata.get("items", []) if isinstance(raw_metadata, dict) else None
        shape_checks = {
            "disks": isinstance(disk, list) and all(isinstance(x, dict) and isinstance(x.get("initializeParams"), dict) for x in disk),
            "networkInterfaces": isinstance(nics, list) and all(isinstance(x, dict) for x in nics),
            "metadata": (isinstance(metadata_items, list)
                and all(isinstance(x, dict) and isinstance(x.get("key"), str) for x in metadata_items)
                and len({x["key"] for x in metadata_items}) == len(metadata_items)),
            "reservationAffinity": isinstance(props.get("reservationAffinity", {}), dict),
        }
        malformed = [key for key, matches in shape_checks.items() if not matches]
        if malformed:
            self._template_mismatch(deployment, role, malformed, {key: props.get(key) for key in malformed})
        metadata = {x["key"]: x.get("value") for x in metadata_items}
        labels = props.get("labels", {})
        expected_labels = {"cnb-run": hashlib.sha256(deployment.run_id.encode()).hexdigest()[:20], "cnb-deploy": hashlib.sha256(deployment.deployment_id.encode()).hexdigest()[:20]}
        expected_policy = self._selected_policy_path(deployment)
        raw_policies = props.get("resourcePolicies", [])
        item_policy_matches = (
            self._compute_path(item.get("expected_policy")) == expected_policy
            if expected_policy else not item.get("expected_policy")
        )
        if expected_policy:
            # Compute's InstanceProperties contract returns policy names for
            # templates, while Terraform's selected policy output is a full
            # project/region path. Bind that one short name to the already
            # validated selected path; retain exact path checks for URL forms.
            policy_matches = (
                isinstance(raw_policies, list) and len(raw_policies) == 1
                and (
                    self._compute_path(raw_policies[0]) == expected_policy
                    or (raw_policies[0] == deployment.placement.name and item_policy_matches)
                )
            )
        else:
            policy_matches = raw_policies == []
        # Named predicates keep mismatch evidence precise without persisting raw
        # metadata (which includes SSH keys and bootstrap input contents).
        checks = {
            "identity": (described_id is not None and item_id == described_id
                and described.get("name") == item.get("name") == expected_name
                and rest_path == unique_path == expected_link and unique_id == str(described_id)),
            "scheduling.provisioningModel": scheduling.get("provisioningModel") == "STANDARD",
            "scheduling.instanceTerminationAction": scheduling.get("instanceTerminationAction") == "STOP",
            "scheduling.terminationTime": self._same_utc_instant(scheduling.get("terminationTime"), trigger),
            "scheduling.automaticRestart": scheduling.get("automaticRestart") is False,
            "scheduling.maxRunDuration": "maxRunDuration" not in scheduling,
            "machineType": str(props.get("machineType", "")).rsplit("/", 1)[-1] == "n2-standard-4",
            "disks": (len(disk) == 1 and disk[0].get("boot") is True and disk[0].get("autoDelete") is True
                and str(disk[0].get("initializeParams", {}).get("sourceImage", "")).split("projects/", 1)[-1] == exp_image.split("projects/", 1)[-1]
                and str(disk[0].get("initializeParams", {}).get("diskType", "")).rsplit("/", 1)[-1] == "pd-balanced"
                and self._matches_int(disk[0].get("initializeParams", {}).get("diskSizeGb"), 32)
                and disk[0].get("initializeParams", {}).get("diskName") == f"{self._namespace(deployment)}-{role[-1]}-boot"
                and disk[0].get("initializeParams", {}).get("labels") == expected_labels),
            "networkInterfaces": (len(nics) == 1 and nics[0].get("nicType") == "GVNIC"
                and nics[0].get("networkIP") == exp_ip and self._compute_path(nics[0].get("network")) == expected_network
                and self._compute_path(nics[0].get("subnetwork")) == expected_subnet
                and isinstance(nics[0].get("accessConfigs"), list) and len(nics[0]["accessConfigs"]) == 1),
            "networkPerformanceConfig": not self._unsupported_network_tier(props.get("networkPerformanceConfig")),
            "minCpuPlatform": not props.get("minCpuPlatform"),
            "scheduling.nodeAffinities": not scheduling.get("nodeAffinities"),
            "metadata": (metadata.get("enable-guest-attributes") == "TRUE"
                and metadata.get("enable-oslogin") == "FALSE" and metadata.get("block-project-ssh-keys") == "TRUE"
                and metadata.get("cnb-bootstrap-template") == deployment.bootstrap_template
                and metadata.get("cnb-bootstrap-inputs") == json.dumps(deployment.bootstrap_inputs, sort_keys=True, separators=(",", ":"))
                and metadata.get("cnb-network-intent") == json.dumps(deployment.vm_a.network.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
                and metadata.get("ssh-keys") == f"benchmark:{self.settings.public_key}"),
            "reservationAffinity": props.get("reservationAffinity", {}).get("consumeReservationType") == "NO_RESERVATION",
            "resourcePolicies": bool(policy_matches and item_policy_matches),
            "labels": labels in (expected_labels, {**expected_labels, "goog-terraform-provisioned": "true"}),
            "maxRunDuration": "maxRunDuration" not in props,
            "guestAccelerators": not props.get("guestAccelerators"),
        }
        mismatches = [key for key, matches in checks.items() if not matches]
        if mismatches:
            fields = {"identity": (described.get("id"), described.get("name"), described.get("selfLink")),
                **{key: scheduling.get(key.split(".", 1)[1]) if key.startswith("scheduling.") else props.get(key)
                   for key in checks if key != "identity"}}
            self._template_mismatch(deployment, role, mismatches, fields)

    def _template_mismatch(self, deployment: ProviderDeploymentInput, role: str,
                           mismatches: list[str], fields: dict[str, Any]) -> None:
        diagnostic = {"role": role, "mismatched_fields": mismatches,
            "observed_field_sha256": {key: hashlib.sha256(json.dumps(fields[key], sort_keys=True).encode()).hexdigest() for key in mismatches}}
        if "resourcePolicies" in mismatches:
            policies = fields.get("resourcePolicies")
            policy_readback = {
                "observed_type": type(policies).__name__,
                "observed_sha256": diagnostic["observed_field_sha256"]["resourcePolicies"],
            }
            # Provider policy identifiers are safe to retain in the private
            # attempt journal. Bound the diagnostic so malformed responses
            # cannot turn it into an unbounded provider payload archive.
            if (isinstance(policies, list) and len(policies) <= 16
                    and all(isinstance(value, str) and len(value) <= 512 for value in policies)):
                policy_readback["observed_values"] = policies
            diagnostic["resourcePolicies_readback"] = policy_readback
        if self._active_attempt is not None:
            journal = self._load(self._active_attempt, deployment)
            journal.setdefault("template_validation_diagnostics", []).append(diagnostic)
            self._save(self._active_attempt, journal)
        raise GcpProviderError("immutable template properties or project-scoped unique identity differ from the selected role: " + ", ".join(mismatches), category="expiry_mismatch")

    @staticmethod
    def _same_utc_instant(value: Any, expected: datetime) -> bool:
        if not isinstance(value, str): return False
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.tzinfo is not None and parsed.utcoffset() is not None and parsed == expected
        except ValueError:
            return False

    def _assert_no_open_cleanup(self, deployment: ProviderDeploymentInput) -> None:
        root = self.settings.private_state_root.expanduser().absolute()
        if root.is_symlink():
            raise GcpProviderError("private state root cannot be a symlink", category="state_path_unsafe")
        if not root.exists(): return
        for path in root.rglob("attempt.json"):
            if path.is_symlink():
                raise GcpProviderError("attempt journal cannot be a symlink", category="state_path_unsafe")
            try: value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc: raise GcpProviderError("an attempt journal is unreadable; new launches are blocked", category="cleanup_unknown") from exc
            if value.get("deployment_id") == deployment.deployment_id:
                if (value.get("registered") or value.get("registration_may_have_started")) and value.get("controller_session_id") != CONTROLLER_SESSION_ID:
                    raise GcpProviderError("an earlier controller session has uncertain protection for this attempt", category="cleanup_open")
                if value.get("launch_blocked") or value.get("cleanup_state") is not None or value.get("creation_may_have_started"):
                    raise GcpProviderError("this attempt already has creation or cleanup state and cannot be launched again", category="cleanup_open")
                continue
            if value.get("cleanup_state") == "succeeded" or value.get("protection_cleanup_state") == "succeeded":
                continue
            session_is_live = value.get("controller_session_id") == CONTROLLER_SESSION_ID
            if value.get("registration_may_have_started") and not value.get("registered"):
                raise GcpProviderError("interrupted registration or unfinished protection blocks launches", category="cleanup_open")
            if value.get("registered") and not session_is_live:
                raise GcpProviderError("an earlier controller session has uncertain protection and blocks launches", category="cleanup_open")
            if value.get("creation_may_have_started"):
                if not session_is_live or not value.get("registered") or value.get("launch_blocked") or value.get("cleanup_state") is not None or value.get("primary_failure"):
                    raise GcpProviderError("another GCP attempt has unresolved creation or cleanup state", category="cleanup_open")
            if value.get("launch_blocked") or value.get("cleanup_state") not in {None, "succeeded"}:
                raise GcpProviderError("another GCP attempt has open cleanup or native expiry", category="cleanup_open")

    @staticmethod
    def _validate_template_created_at(value: Any, cap_start: datetime, registered_at: datetime) -> datetime:
        try:
            created = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise GcpProviderError("template creation time is missing or invalid", category="cap_order") from exc
        if created.tzinfo is None or created.utcoffset() is None or cap_start.tzinfo is None or registered_at.tzinfo is None:
            raise GcpProviderError("template creation and reservation timestamps must be timezone-aware", category="cap_order")
        created = created.astimezone(timezone.utc)
        if not cap_start.astimezone(timezone.utc) <= created <= registered_at.astimezone(timezone.utc):
            raise GcpProviderError("template creation must fall after reservation activation and before registration", category="cap_order")
        return created

    def _fit(self, deployment: ProviderDeploymentInput, reservation: ReservationEvidence, trigger: datetime, seconds: float) -> None:
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None or trigger.tzinfo is None or trigger.utcoffset() is None:
            raise GcpProviderError("reservation and trigger times must be timezone-aware", category="cap_order")
        now = now.astimezone(timezone.utc)
        trigger = trigger.astimezone(timezone.utc)
        cap_start = reservation.cap_started_at.value.astimezone(timezone.utc)
        cap_end = reservation.expires_at.value.astimezone(timezone.utc)
        if not isfinite(seconds) or seconds <= 0:
            raise GcpProviderError("complete remaining workflow estimate must be positive and finite", category="cap_insufficient")
        if trigger != cap_end - timedelta(seconds=provider_lead_seconds(
                deployment.admission.workflow_profile, "gcp", SAFETY_LEAD_SECONDS)):
            raise GcpProviderError("native STOP trigger must remain tied to the original reservation cap", category="cap_mismatch")
        if cap_start > now or now > trigger or now + timedelta(seconds=seconds) > trigger:
            raise GcpProviderError("whole remaining workflow must fit before original native STOP initiation lead", category="cap_insufficient")

    def _elapsed_workflow_seconds(self, deployment: ProviderDeploymentInput) -> float:
        """Elapsed monotonic time within the original reservation epoch."""
        started = self.settings.workflow_cap_started_monotonic
        if deployment.admission.workflow_profile is None or started is None:
            return 0.0
        elapsed = self.monotonic_clock() - started
        if not isfinite(elapsed) or elapsed < 0:
            raise GcpProviderError("original monotonic workflow anchor is invalid", category="cap_order")
        return elapsed

    def _remaining_workflow_seconds(self, deployment: ProviderDeploymentInput, stage: str = "registration") -> float:
        """Return the stage view of the fixed workflow vector after completed work."""
        total = self.settings.complete_workflow_remaining_seconds
        if deployment.admission.workflow_profile is None or self.settings.workflow_cap_started_monotonic is None:
            return total
        elapsed = self._elapsed_workflow_seconds(deployment)
        registration = self.settings.registration_allowance_seconds
        if stage == "registration":
            completed = elapsed
        elif stage == "preparation":
            completed = max(registration, elapsed)
        elif stage == "post_provision":
            completed = max(registration+self.settings.provision_allowance_seconds, elapsed)
        else:
            raise ValueError("unknown GCP workflow fit stage")
        return max(0.0, total-completed)

    def _remaining_after_provision_seconds(self, deployment: ProviderDeploymentInput) -> float:
        total = self.settings.complete_workflow_remaining_seconds
        if deployment.admission.workflow_profile is None or self.settings.workflow_cap_started_monotonic is None:
            return total
        completed = max(self.settings.registration_allowance_seconds+self.settings.provision_allowance_seconds,
            self._elapsed_workflow_seconds(deployment))
        return max(0.0, total-completed)

    def _attempt_dir(self, deployment: ProviderDeploymentInput) -> Path:
        root = self.settings.private_state_root.expanduser().absolute()
        if any(parent.is_symlink() for parent in (root, *root.parents) if parent.exists()):
            raise GcpProviderError("private state root cannot be a symlink", category="state_path_unsafe")
        for value, label in ((deployment.run_id, "run"), (deployment.deployment_id, "deployment")):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value) or value in {".", ".."}:
                raise GcpProviderError(f"{label} identity is not a safe single path component", category="state_path_unsafe")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = root / hashlib.sha256(deployment.run_id.encode()).hexdigest()[:12] / deployment.run_id / deployment.deployment_id
        if os.path.commonpath((str(root), str(path.absolute()))) != str(root) or any(part.is_symlink() for part in (path.parent.parent, path.parent, path)):
            raise GcpProviderError("attempt path cannot contain symlinks", category="state_path_unsafe")
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)
        return path

    def _load(self, attempt: Path, deployment: ProviderDeploymentInput) -> dict[str, Any]:
        p = attempt / "attempt.json"
        if not p.exists():
            return {"run_id": deployment.run_id, "deployment_id": deployment.deployment_id, "commands": []}
        if p.is_symlink():
            raise GcpProviderError("journal cannot be a symlink", category="state_path_unsafe")
        j = json.loads(p.read_text())
        if j.get("run_id") != deployment.run_id or j.get("deployment_id") != deployment.deployment_id:
            raise GcpProviderError("journal identity mismatch", category="state_identity_mismatch")
        if not isinstance(j.get("commands", []), list):
            raise GcpProviderError("command journal is malformed", category="cleanup_unknown")
        return j

    def _save(self, attempt: Path, journal: dict[str, Any]) -> None:
        target = attempt / "attempt.json"
        if target.is_symlink(): raise GcpProviderError("journal cannot be a symlink", category="state_path_unsafe")
        if target.exists():
            current = json.loads(target.read_text(encoding="utf-8"))
            if (current.get("run_id"), current.get("deployment_id")) != (journal.get("run_id"), journal.get("deployment_id")):
                raise GcpProviderError("journal identity changed during update", category="state_identity_mismatch")
            immutable = ("run_id", "deployment_id", "binding_sha256", "reservation_id", "cap_started_at", "cap_expires_at", "trigger", "controller_session_id", "cleanup_started_at", "cleanup_started_monotonic", "cleanup_clock_binding", "cleanup_deadline_utc", "cleanup_deadline_monotonic", "cleanup_deadline_binding_sha256", "cleanup_routine_deadline_utc", "cleanup_routine_deadline_monotonic", "cleanup_absence_started_at", "cleanup_absence_started_monotonic", "cleanup_absence_clock_binding", "cleanup_absence_deadline_utc", "cleanup_absence_deadline_monotonic")
            for key in immutable:
                if key in current and key in journal and current[key] != journal[key]:
                    raise GcpProviderError(f"journal immutable ownership field {key} changed", category="state_identity_mismatch")
                if key in current:
                    journal[key] = current[key]
            if "expiry" in current and "expiry" not in journal:
                journal["expiry"] = current["expiry"]
            elif (current.get("expiry") or {}).get("state") in {"cancelled", "failed"} and (journal.get("expiry") or {}).get("state") == "registered":
                journal["expiry"] = current["expiry"]
            old_events = current.get("commands", [])
            supplied_events = journal.get("commands", [])
            if not isinstance(old_events, list) or not isinstance(supplied_events, list):
                raise GcpProviderError("command journal is malformed", category="cleanup_unknown")
            events, event_hashes = [], set()
            for event in (*old_events, *supplied_events):
                fingerprint = hashlib.sha256(json.dumps(event, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
                if fingerprint not in event_hashes:
                    event_hashes.add(fingerprint); events.append(event)
            journal["commands"] = events
            diagnostics = []
            for entry in (*current.get("template_validation_diagnostics", []), *journal.get("template_validation_diagnostics", [])):
                if entry not in diagnostics:
                    diagnostics.append(entry)
            if diagnostics:
                journal["template_validation_diagnostics"] = diagnostics[-16:]
            prior_disks = current.get("observed_disks", [])
            incoming_disks = journal.get("observed_disks", [])
            if not isinstance(prior_disks, list) or not isinstance(incoming_disks, list):
                raise GcpProviderError("observed disk journal is malformed", category="cleanup_unknown")
            disks, disk_keys = [], set()
            for disk in (*prior_disks, *incoming_disks):
                if not isinstance(disk, dict):
                    raise GcpProviderError("observed disk identity is malformed", category="cleanup_unknown")
                key = (str(disk.get("id")), disk.get("name"), disk.get("zone"))
                if key not in disk_keys:
                    disk_keys.add(key); disks.append(disk)
            if disks or "observed_disks" in current or "observed_disks" in journal:
                journal["observed_disks"] = disks
            prior_resources = current.get("observed_resources", {})
            incoming_resources = journal.get("observed_resources", {})
            if not isinstance(prior_resources, dict) or not isinstance(incoming_resources, dict):
                raise GcpProviderError("observed VM journal is malformed", category="cleanup_unknown")
            resources = {role: dict(value) for role, value in prior_resources.items()}
            for role, value in incoming_resources.items():
                if role in resources and resources[role].get("id") and value.get("id") and str(resources[role]["id"]) != str(value["id"]):
                    raise GcpProviderError("observed role VM identity changed in the journal", category="state_identity_mismatch")
                resources[role] = {**resources.get(role, {}), **value}
            if resources or "observed_resources" in current or "observed_resources" in journal:
                journal["observed_resources"] = resources
            prior_hosts = current.get("known_management_hosts", {})
            incoming_hosts = journal.get("known_management_hosts", {})
            if not isinstance(prior_hosts, dict) or not isinstance(incoming_hosts, dict):
                raise GcpProviderError("management host binding journal is malformed", category="cleanup_unknown")
            hosts = dict(prior_hosts)
            for identity, address in incoming_hosts.items():
                if identity in hosts and hosts[identity] != address:
                    raise GcpProviderError("management host address changed for a bound VM identity", category="hostkey_identity_mismatch")
                hosts[identity] = address
            if hosts or "known_management_hosts" in current or "known_management_hosts" in journal:
                journal["known_management_hosts"] = hosts
            prior_templates = current.get("templates", {})
            incoming_templates = journal.get("templates", {})
            if not isinstance(prior_templates, dict) or not isinstance(incoming_templates, dict):
                raise GcpProviderError("registered template journal is malformed", category="cleanup_unknown")
            templates = {role: dict(value) for role, value in prior_templates.items()}
            for role, value in incoming_templates.items():
                if role in templates:
                    for identity_field in ("id", "name", "self_link_unique"):
                        if templates[role].get(identity_field) and value.get(identity_field) and templates[role][identity_field] != value[identity_field]:
                            raise GcpProviderError("registered template identity changed in the journal", category="state_identity_mismatch")
                templates[role] = {**templates.get(role, {}), **value}
            if templates or "templates" in current or "templates" in journal:
                journal["templates"] = templates
            for key in ("registration_may_have_started", "registered", "creation_may_have_started"):
                if current.get(key):
                    journal[key] = True
            if current.get("cleanup_state") is not None and journal.get("cleanup_state") is None:
                journal["cleanup_state"] = current["cleanup_state"]
            if current.get("protection_cleanup_state") is not None and journal.get("protection_cleanup_state") is None:
                journal["protection_cleanup_state"] = current["protection_cleanup_state"]
            if current.get("launch_blocked") and journal.get("cleanup_state") != "succeeded":
                journal["launch_blocked"] = True
            failures = list(current.get("failure_history", []))
            for source in (current, journal):
                for kind in ("primary_failure", "registration_failure", "cleanup_failure"):
                    record = source.get(kind)
                    if record and record not in failures:
                        failures.append(record)
            if failures:
                journal["failure_history"] = failures
            for key in ("primary_failure", "registration_failure", "cleanup_failure"):
                if key in current and key not in journal:
                    journal[key] = current[key]
        fd, name = tempfile.mkstemp(dir=attempt)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(journal, stream, sort_keys=True, indent=2, default=str)
                stream.flush(); os.fsync(stream.fileno())
            os.chmod(name, 0o600); os.replace(name, target)
        finally:
            if os.path.exists(name): os.unlink(name)

    @staticmethod
    def _write_private(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.is_symlink(): raise GcpProviderError("private artifact cannot be a symlink", category="state_path_unsafe")
        fd, name = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content); stream.flush(); os.fsync(stream.fileno())
            os.chmod(name, 0o600); os.replace(name, path)
        finally:
            if os.path.exists(name): os.unlink(name)

    def _run(self, action: str, argv: list[str], cwd: Path, timeout: int | None = None) -> CommandResult:
        terraform_root = next((Path(arg.split("=", 1)[1]).name for arg in argv if arg.startswith("-chdir=")), None)
        cleanup_destroy = (action in {"gcp-pair-destroy", "gcp-protection-destroy"}
            and terraform_root in {"pair", "protection"}
            and len(argv) > 2 and argv[2] == "destroy")
        requested_limit = self.settings.command_timeout_seconds if timeout is None else timeout
        command_limit = min(requested_limit, 1800 if cleanup_destroy else self.settings.command_timeout_seconds)
        reviewed_apply = "apply" in argv and any(a.startswith("-chdir=") for a in argv)
        started = time.monotonic()
        self._check_cleanup_deadline(action)
        hard_deadline = self._deadline if self._deadline is not None else float("inf")
        if cleanup_destroy and self._deadline is None:
            raise GcpProviderError("original cumulative cleanup deadline is unavailable", category="cleanup_deadline_unknown", action_id=action)
        if reviewed_apply and self.settings.workflow_cap_started_monotonic is not None:
            cap_start = self.settings.workflow_cap_started_monotonic
            elapsed = started - cap_start
            if not isfinite(elapsed) or elapsed < 0:
                raise GcpProviderError("original monotonic workflow anchor is invalid", category="cap_order", action_id=action)
            root_arg = next(a.split("=", 1)[1] for a in argv if a.startswith("-chdir="))
            terraform_root = Path(root_arg).name
            if terraform_root == "protection":
                stage = "registration"
            elif terraform_root == "pair":
                stage = "provision"
            else:
                raise GcpProviderError("profiled reviewed apply has an unknown workflow stage", category="allowance_invalid", action_id=action)
            from ..terraform_plan import workflow_review_stage_deadline
            # Review queue time is charged to the original registered workflow
            # stage. Provider API commands retain their own registration limits;
            # every Terraform command remains capped below by command_limit.
            if self._provider_stop_deadline is None:
                raise GcpProviderError("registered provider STOP deadline is unavailable", category="stop_deadline_invalid", action_id=action)
            hard_deadline = workflow_review_stage_deadline(
                cap_started_monotonic=cap_start,
                registration_allowance_seconds=self.settings.registration_allowance_seconds,
                provision_allowance_seconds=self.settings.provision_allowance_seconds,
                stage=stage, overall_deadline=cap_start + self.settings.complete_workflow_remaining_seconds,
                stop_deadline=self._provider_stop_deadline)
        hard_remaining = hard_deadline - started
        if hard_remaining <= 0:
            raise GcpProviderError("absolute GCP command deadline elapsed", category="command_timeout", action_id=action)
        if not (reviewed_apply and self.settings.workflow_cap_started_monotonic is not None):
            hard_remaining = min(hard_remaining, command_limit + (self.settings.plan_review_timeout_seconds
                if reviewed_apply else 0))
        total_deadline = started + hard_remaining
        init_limit = min(command_limit, hard_remaining)
        from ..terraform_work import private_terraform_environment, initialize_terraform
        command_deadline = started + init_limit
        request = CommandRequest(action_id=action, action_type="gcp_provider_control", argv=argv, cwd=str(cwd), timeout_seconds=init_limit, environment=private_terraform_environment(argv, cwd), environment_classification="contains_secret_references")
        if not hasattr(self, "_initialized_terraform"):
            self._initialized_terraform = set()
        initialize_terraform(self.runner, request, self._initialized_terraform)
        self._check_cleanup_deadline(action)
        now_after_init = time.monotonic()
        remaining_after_init = command_deadline - now_after_init
        if self._deadline is not None:
            remaining_after_init = min(remaining_after_init, self._deadline-now_after_init)
        if remaining_after_init <= 0:
            raise GcpProviderError("original command deadline elapsed during private Terraform initialization", category="command_timeout", action_id=action)
        request_budget = (total_deadline - time.monotonic()) if reviewed_apply else min(
            request.timeout_seconds, remaining_after_init)
        if request_budget <= 0:
            raise GcpProviderError("original workflow request budget elapsed during private Terraform initialization",
                category="command_timeout", action_id=action)
        request = request.model_copy(update={"timeout_seconds": request_budget})
        def execute(request):
            result = self.runner.run(request)
            self._record_command(request, result)
            return result
        if reviewed_apply:
            from ..terraform_plan import reviewed_apply
            return reviewed_apply(request, execute, review_seconds=self.settings.plan_review_timeout_seconds,
                command_timeout_seconds=command_limit,
                review_admission_path=self.settings.plan_review_admission_path)
        return execute(request)

    def _record_command(self, request: CommandRequest, result: CommandResult) -> None:
        attempt = self._active_attempt
        if attempt is None:
            return
        target = attempt / "attempt.json"
        if not target.exists():
            return
        if target.is_symlink():
            raise GcpProviderError("journal cannot be a symlink", category="state_path_unsafe")
        journal = json.loads(target.read_text(encoding="utf-8"))
        request_view = request.durable_view()
        result_view = result.durable_view()
        # Keep outcome/timing and content hashes while excluding provider response bodies
        # that may contain host keys, addresses, or other attempt-specific material.
        for field in ("stdout", "stderr"):
            raw = getattr(result, field).encode("utf-8")
            result_view[field] = f"[omitted sha256={hashlib.sha256(raw).hexdigest()} bytes={len(raw)}]"
        journal.setdefault("commands", []).append({"request": request_view, "result": result_view})
        self._save(attempt, journal)

    def _terraform(self, attempt: Path, root: str, action: str, deployment: ProviderDeploymentInput, extra: dict[str, Any]) -> dict[str, Any]:
        work = attempt / root
        if work.is_symlink() or os.path.commonpath((str(attempt.absolute()), str(work.absolute()))) != str(attempt.absolute()):
            raise GcpProviderError("Terraform work directory must remain inside its private attempt", category="state_path_unsafe")
        work.mkdir(mode=0o700, exist_ok=True)
        vars_path = work / "vars.json"
        legacy_pair_cleanup = False
        if root == "pair" and action == "destroy" and not vars_path.exists():
            raise GcpProviderError("original pair Terraform inputs are unavailable for scoped cleanup", category="state_identity_mismatch")
        if action == "destroy" and vars_path.exists():
            if vars_path.is_symlink(): raise GcpProviderError("Terraform input cannot be a symlink", category="state_path_unsafe")
            variables = json.loads(vars_path.read_text(encoding="utf-8"))
            if variables.get("project_id") != self.settings.project_id:
                raise GcpProviderError("Terraform inputs belong to another project", category="state_identity_mismatch")
            if (variables.get("zone_a"), variables.get("zone_b")) != (deployment.vm_a.zone, deployment.vm_b.zone):
                raise GcpProviderError("Terraform inputs changed the original role zones", category="state_identity_mismatch")
            if root == "protection":
                expected = {
                    "namespace": self._namespace(deployment),
                    "scenario": deployment.scenario.value if hasattr(deployment.scenario, "value") else deployment.scenario,
                    "placement_policy_name": deployment.placement.name or "",
                    "image_a": self.settings.images.get(deployment.vm_a.region),
                    "image_b": self.settings.images.get(deployment.vm_b.region),
                }
            else:
                expected = {"name_a": self._namespace(deployment) + "-a", "name_b": self._namespace(deployment) + "-b"}
            if any(variables.get(key) != value for key, value in expected.items()):
                raise GcpProviderError("Terraform inputs changed the original attempt scope", category="state_identity_mismatch")
            if root == "pair":
                expected_pair = self._pair_variables(deployment, {
                    "template_a": variables.get("template_a"),
                    "template_b": variables.get("template_b"),
                })
                pair_fields = set(expected_pair) - {"project_id", "name_a", "name_b", "zone_a", "zone_b", "template_a", "template_b"}
                present_pair_fields = pair_fields.intersection(variables)
                if present_pair_fields and present_pair_fields != pair_fields:
                    raise GcpProviderError("pair Terraform NIC inputs are incomplete", category="state_identity_mismatch")
                if present_pair_fields:
                    if any(variables.get(key) != value for key, value in expected_pair.items()):
                        raise GcpProviderError("pair Terraform NIC inputs changed the selected topology", category="state_identity_mismatch")
                else:
                    legacy_pair_cleanup = True
                journal = self._load(attempt, deployment)
                pair_binding = journal.get("pair_binding", {})
                if pair_binding and any(pair_binding.get(key) != variables.get(key)
                        for key in ("template_a", "template_b", "project_id", "zone_a", "zone_b")):
                    raise GcpProviderError("saved pair template binding differs from cleanup inputs", category="state_identity_mismatch")
                registered_templates = journal.get("templates", {})
                if set(registered_templates) != {"vm_a", "vm_b"}:
                    raise GcpProviderError("registered role template identities are unavailable for cleanup", category="state_identity_mismatch")
                for role, key in (("vm_a", "template_a"), ("vm_b", "template_b")):
                    registered_link = registered_templates[role].get("self_link_unique")
                    registered_id = str(registered_templates[role].get("id", ""))
                    if (not registered_id or not registered_link or variables.get(key) != registered_link
                            or f"uniqueId={registered_id}" not in str(registered_link)):
                        raise GcpProviderError("cleanup template link differs from its registered immutable identity", category="state_identity_mismatch")
        else:
            variables = None
        common = {"project_id": self.settings.project_id}
        if variables is not None:
            template = variables
        elif root == "protection":
            template = {**common, "namespace": self._namespace(deployment), "scenario": deployment.scenario.value if hasattr(deployment.scenario, "value") else deployment.scenario, "placement_policy_name": deployment.placement.name or "", "expiry_time": "", "image_a": self.settings.images[deployment.vm_a.region], "image_b": self.settings.images[deployment.vm_b.region], "zone_a": deployment.vm_a.zone, "zone_b": deployment.vm_b.zone, "controller_cidr": self.settings.controller_cidr, "public_key": self.settings.public_key, "labels": {"cnb-run": hashlib.sha256(deployment.run_id.encode()).hexdigest()[:20], "cnb-deploy": hashlib.sha256(deployment.deployment_id.encode()).hexdigest()[:20]}, "bootstrap_template": deployment.bootstrap_template, "bootstrap_inputs": deployment.bootstrap_inputs, "network_intent": deployment.vm_a.network.model_dump(mode="json")}
        elif root == "pair" and action == "apply":
            template = self._pair_variables(deployment, extra)
        else:
            template = {**common, "name_a": self._namespace(deployment) + "-a", "name_b": self._namespace(deployment) + "-b", "zone_a": deployment.vm_a.zone, "zone_b": deployment.vm_b.zone}
        if not (root == "pair" and action == "apply"):
            template.update(extra)
        serialized_vars = json.dumps(template, sort_keys=True).encode("utf-8")
        self._write_private(vars_path, serialized_vars)
        if root == "pair" and action == "apply":
            journal = self._load(attempt, deployment)
            journal["pair_binding"] = {"config_sha256": hashlib.sha256(serialized_vars).hexdigest(), **{key: template[key] for key in ("template_a", "template_b", "project_id", "zone_a", "zone_b", "network_a", "network_b", "subnetwork_a", "subnetwork_b", "network_ip_a", "network_ip_b", "nic_type_a", "nic_type_b", "public_management_a", "public_management_b")}}
            self._save(attempt, journal)
        root_path = Path(__file__).resolve().parents[3] / "terraform" / "gcp" / root
        if root == "pair" and action == "apply":
            trigger = deployment.reservation.value.expires_at.value - timedelta(seconds=provider_lead_seconds(
                deployment.admission.workflow_profile, "gcp", SAFETY_LEAD_SECONDS))
            slack = (trigger - self.clock()).total_seconds() - self._remaining_after_provision_seconds(deployment)
            self._deadline = min(self._deadline or float("inf"), time.monotonic() + max(0, slack))
        argv = [self.settings.terraform, f"-chdir={root_path}", action, "-input=false", "-auto-approve", f"-state={work / 'terraform.tfstate'}", f"-var-file={vars_path}"]
        if legacy_pair_cleanup:
            argv.append("-var=cleanup_mode=true")
        rc = self._run(f"gcp-{root}-{action}", argv, work, timeout=deployment.provisioning_timeout_seconds if action == "apply" else deployment.cleanup_timeout_seconds)
        if rc.outcome != "succeeded" or rc.exit_code != 0:
            raise GcpProviderError(f"GCP Terraform {root} {action} failed", category="terraform_failed", action_id=rc.action_id)
        if action == "destroy": return {}
        result = self._run(f"gcp-{root}-outputs", [self.settings.terraform, f"-chdir={root_path}", "output", "-json", f"-state={work / 'terraform.tfstate'}"], work)
        if result.outcome != "succeeded": raise GcpProviderError("Terraform output read failed", category="terraform_output")
        return json.loads(result.stdout)

    def _pair_variables(self, deployment: ProviderDeploymentInput, templates: dict[str, Any]) -> dict[str, Any]:
        scenario = deployment.scenario.value if hasattr(deployment.scenario, "value") else str(deployment.scenario)
        addresses = {
            "same_zone": ("10.80.1.10", "10.80.1.11"),
            "cross_zone": ("10.80.1.10", "10.80.1.11"),
            "placement_optimization": ("10.80.1.10", "10.80.1.11"),
            "inter_region": ("10.80.1.10", "10.81.1.11"),
        }
        if scenario not in addresses or set(templates) != {"template_a", "template_b"}:
            raise GcpProviderError("pair input factory received an unknown topology or template binding", category="state_identity_mismatch")
        namespace = self._namespace(deployment)
        network = f"projects/{self.settings.project_id}/global/networks/{namespace}-net"
        subnet_b = "b" if scenario == "inter_region" else "a"
        return {
            "project_id": self.settings.project_id,
            "name_a": namespace + "-a", "name_b": namespace + "-b",
            "zone_a": deployment.vm_a.zone, "zone_b": deployment.vm_b.zone,
            "template_a": templates["template_a"], "template_b": templates["template_b"],
            "scenario": scenario,
            "region_a": deployment.vm_a.region, "region_b": deployment.vm_b.region,
            "network_a": network, "network_b": network,
            "subnetwork_a": f"projects/{self.settings.project_id}/regions/{deployment.vm_a.region}/subnetworks/{namespace}-a",
            "subnetwork_b": f"projects/{self.settings.project_id}/regions/{deployment.vm_b.region}/subnetworks/{namespace}-{subnet_b}",
            "network_ip_a": addresses[scenario][0], "network_ip_b": addresses[scenario][1],
            "nic_type_a": "GVNIC", "nic_type_b": "GVNIC",
            "public_management_a": True, "public_management_b": True,
            # Terraform includes declared defaults in plan JSON variables.
            # Supply the production value so strict review compares the same
            # complete input map that produced the saved plan.
            "cleanup_mode": False,
        }

    def _gcloud_json(self, args: list[str], action: str) -> dict[str, Any] | list[Any]:
        result = self._run(action, [self.settings.gcloud, *args], self.settings.private_state_root)
        if result.outcome != "succeeded" or result.exit_code != 0:
            if (action == "gcp-guest-hostkeys"
                    and "get-guest-attributes" in args
                    and "--query-path=hostkeys/" in args
                    and result.outcome == "failed"
                    and result.exit_code == 1
                    and self._is_hostkeys_guest_attribute_not_found(result.stderr)):
                raise GcpProviderError("requested GCP hostkeys guest attribute is not yet available",
                    category="guest_attribute_not_found", action_id=action)
            raise GcpProviderError("authenticated GCP readback failed", category="gcp_api_read", action_id=action)
        if args[:1] == ["compute"] and "list" in args and "--format=json" in args:
            if result.stderr.strip():
                raise GcpProviderError(f"inventory scope {action} returned a completeness warning", category="inventory_unknown", action_id=action)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise GcpProviderError(f"authenticated GCP response for {action} was malformed", category="gcp_api_read", action_id=action) from exc

    @staticmethod
    def _is_hostkeys_guest_attribute_not_found(stderr: str) -> bool:
        """Recognize only the canonical one-line gcloud diagnostic for hostkeys/."""
        return bool(re.fullmatch(
            r"ERROR:\s+\(gcloud\.compute\.instances\.get-guest-attributes\) "
            r"HTTPError 404: The resource 'hostkeys/' of type 'Guest Attribute' "
            r"was not found\."
            r"(?: This command is authenticated as [^\s]+ which is the active "
            r"account specified by the \[core/account\] property\.)?",
            stderr.strip()))

    @staticmethod
    def _outputs(value: dict[str, Any]) -> dict[str, Any]:
        return {key: item.get("value", item) for key, item in value.items()}

    @staticmethod
    def _compute_path(value: Any) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        parsed = urlparse(value)
        if parsed.scheme:
            try:
                unsafe_authority = parsed.username or parsed.password or parsed.port
            except ValueError:
                return None
            if parsed.scheme != "https" or parsed.hostname not in {"www.googleapis.com", "compute.googleapis.com"} or unsafe_authority:
                return None
            path = parsed.path
        else:
            if value.startswith("//") or parsed.query or parsed.fragment:
                return None
            path = parsed.path
        if parsed.scheme and (parsed.query or parsed.fragment):
            return None
        marker = "/compute/v1/"
        if marker in path:
            path = path.split(marker, 1)[1]
        path = path.lstrip("/")
        if not path or any(part in {".", ".."} for part in path.split("/")) or "%2f" in path.lower():
            return None
        return path

    @classmethod
    def _unique_compute_link(cls, value: Any) -> tuple[str | None, str | None]:
        if not isinstance(value, str):
            return None, None
        parsed = urlparse(value)
        if parsed.scheme:
            try:
                unsafe_authority = parsed.username or parsed.password or parsed.port
            except ValueError:
                return None, None
            if parsed.scheme != "https" or parsed.hostname not in {"www.googleapis.com", "compute.googleapis.com"} or unsafe_authority or parsed.fragment:
                return None, None
        elif value.startswith("//") or parsed.fragment:
            return None, None
        path = cls._compute_path(parsed.path)
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        if len(pairs) != 1 or pairs[0][0] != "uniqueId" or not re.fullmatch(r"[1-9][0-9]*", pairs[0][1]):
            return None, None
        return path, pairs[0][1]

    @staticmethod
    def _int64(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if value > 0 else None
        if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value):
            return int(value)
        return None

    @classmethod
    def _matches_int(cls, value: Any, expected: int) -> bool:
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            return value == expected
        return isinstance(value, str) and re.fullmatch(r"[0-9]+", value) is not None and int(value) == expected

    @staticmethod
    def _unsupported_network_tier(value: Any) -> bool:
        if not isinstance(value, dict):
            return bool(value)
        tier = value.get("totalEgressBandwidthTier")
        return tier not in (None, "", "TIER_UNSPECIFIED")

    def _selected_policy_path(self, deployment: ProviderDeploymentInput) -> str | None:
        if deployment.scenario != "placement_optimization":
            return None
        name = deployment.placement.name
        if not name:
            raise GcpProviderError("S3 requires the registered compact placement policy name", category="placement_missing")
        region = deployment.vm_a.region
        return f"projects/{self.settings.project_id}/regions/{region}/resourcePolicies/{name}"

    @staticmethod
    def _namespace(deployment: ProviderDeploymentInput) -> str:
        stem = re.sub(r"[^a-z0-9-]", "-", deployment.run_id.lower()).strip("-")[:25]
        stem = re.sub(r"-+", "-", stem).strip("-") or "run"
        return f"gcp-{stem}-{hashlib.sha256(deployment.deployment_id.encode()).hexdigest()[:8]}"

    def _binding_fingerprint(self, deployment: ProviderDeploymentInput) -> str:
        handoff = self.settings.complete_workflow_cost_handoff.value or {}
        deployment_value = deployment.model_dump(mode="json")
        deployment_value.pop("expiry", None)
        value = {"deployment": deployment_value, "project": self.settings.project_id,
                 "images": self.settings.images, "controller_cidr": self.settings.controller_cidr,
                 "public_key_sha256": hashlib.sha256(self.settings.public_key.encode()).hexdigest(),
                 "authentication_reference": self.settings.authentication_reference,
                 "cost_input_id": self.settings.cost_input_id, "cost_input_sha256": self.settings.cost_input_sha256,
                 "cost_source_path": self.settings.cost_source_path, "workflow_handoff": handoff,
                 "support_cost_evidence": self.settings.support_cost_evidence.value,
                 "primary_review_decision": self.settings.primary_review_decision.value if self.settings.primary_review_decision else None,
                 "workflow_remaining_seconds": self.settings.complete_workflow_remaining_seconds}
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()

    def _inventory(self, deployment: ProviderDeploymentInput, *,
                   journal: dict[str, Any] | None = None) -> dict[str, Any]:
        instances, disks, addresses, support = [], [], [], []
        dep_hash = hashlib.sha256(deployment.deployment_id.encode()).hexdigest()[:20]
        namespace = self._namespace(deployment)
        journal = self._load(self._attempt_dir(deployment), deployment) if journal is None else journal
        known_vms = {(str(item.get("id")), item.get("name"), item.get("zone")) for item in journal.get("observed_resources", {}).values() if item.get("id") and item.get("name") and item.get("zone")}
        known_disks = {(str(item.get("id")), item.get("name"), item.get("zone")) for item in journal.get("observed_disks", []) if item.get("id") and item.get("name") and item.get("zone")}
        known_templates = {(str(item.get("id")), item.get("name")) for item in journal.get("templates", {}).values() if item.get("id") and item.get("name")}
        owned_labels = {"cnb-run": hashlib.sha256(deployment.run_id.encode()).hexdigest()[:20], "cnb-deploy": dep_hash}
        expected_instance_names = {f"{namespace}-a", f"{namespace}-b"}
        command_args = dict(GCP_CLEANUP_INVENTORY_COMMANDS)
        response = self._gcloud_json([*command_args["gcp-inventory-all-zones"], "--project", self.settings.project_id, "--format=json"], "gcp-inventory-all-zones")
        if not isinstance(response, list): raise GcpProviderError("all-zone instance inventory pagination shape unknown", category="inventory_unknown")
        self._validate_inventory_items(response, "instances")
        instances.extend(item for item in response if (str(item.get("id")), item.get("name"), item.get("zone", "").rsplit("/", 1)[-1]) in known_vms or all(item.get("labels", {}).get(key) == value for key, value in owned_labels.items()) or item.get("name") in expected_instance_names)
        disk_response = self._gcloud_json([*command_args["gcp-disk-inventory-all-zones"], "--project", self.settings.project_id, "--format=json"], "gcp-disk-inventory-all-zones")
        if not isinstance(disk_response, list): raise GcpProviderError("all-zone disk inventory pagination shape unknown", category="inventory_unknown")
        self._validate_inventory_items(disk_response, "disks")
        expected_disk_names = {f"{namespace}-a-boot", f"{namespace}-b-boot"}
        disks.extend(item for item in disk_response if (str(item.get("id")), item.get("name"), item.get("zone", "").rsplit("/", 1)[-1]) in known_disks or all(item.get("labels", {}).get(key) == value for key, value in owned_labels.items()) or item.get("name") in expected_disk_names)
        # Aggregated project-wide lists include every region and global scope.
        # --regions on addresses is not a supported complete-list query and can
        # produce warnings even for empty output. Never suppress such warnings.
        collections = (
            ("addresses", "gcp-addresses-inventory-all-scopes", addresses),
            ("subnetworks", "gcp-subnet-inventory-all-scopes", support),
            ("instance-templates", "gcp-instance_templates-inventory-all-scopes", support),
            ("networks", "gcp-networks-inventory-all-scopes", support),
            ("firewall-rules", "gcp-firewalls-inventory-all-scopes", support),
            ("routes", "gcp-routes-inventory-all-scopes", support),
            ("resource-policies", "gcp-policy-inventory-all-scopes", support))
        for command, action_id, target in collections:
            result = self._gcloud_json([*command_args[action_id], "--project", self.settings.project_id, "--format=json"],
                action_id)
            if not isinstance(result, list):
                raise GcpProviderError(f"{command} inventory pagination shape unknown", category="inventory_unknown")
            self._validate_inventory_items(result, command)
            for item in result:
                if (item["name"].startswith(namespace + "-")
                        or (command == "instance-templates" and (str(item.get("id")), item["name"]) in known_templates)):
                    target.append(item)
        resources = [*instances, *disks, *addresses, *support]
        return {"pages_complete": True, "zone_scope": "all_project_zones", "regions_scanned": sorted({deployment.vm_a.region, deployment.vm_b.region}), "completed_scopes": ["all_project_zones", *sorted({deployment.vm_a.region, deployment.vm_b.region}), "global"], "instances": instances, "disks": disks, "addresses": addresses, "support_resources": support, "resources": resources}

    def _validate_inventory_items(self, items: list[Any], collection: str) -> None:
        for item in items:
            if (not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip()
                    or not isinstance(item.get("labels", {}), dict)):
                raise GcpProviderError(f"{collection} inventory has an incomplete observation", category="inventory_unknown")
            link = item.get("selfLink")
            if link is not None and not (self._compute_path(link) or "").startswith(f"projects/{self.settings.project_id}/"):
                raise GcpProviderError(f"{collection} inventory has a foreign project identity", category="inventory_unknown")

    @staticmethod
    def _failure(exc: Exception) -> FailureEvidence:
        return FailureEvidence(category=getattr(exc, "category", "gcp_provider"), message=str(exc)[:500], occurred_at=datetime.now(timezone.utc), action_id=getattr(exc, "action_id", None))

    def _vm_output(self, role: str, raw: dict[str, Any], deployment: ProviderDeploymentInput) -> ProviderVmOutput:
        nics = raw.get("networkInterfaces", [])
        if len(nics) != 1: raise GcpProviderError("VM must have exactly one observed network interface", category="instance_shape_mismatch")
        nic = nics[0]; internal = nic.get("networkIP")
        public = next((x.get("natIP") for x in nic.get("accessConfigs", []) if x.get("natIP")), None)
        zone = raw.get("zone", "").rsplit("/", 1)[-1]
        region = "-".join(zone.split("-")[:-1])
        intent = deployment.vm_a if role == "vm_a" else deployment.vm_b
        vm_role = "vm_a" if role == "vm_a" else "vm_b"
        known_hosts = self.settings.private_state_root / self._namespace(deployment) / "known_hosts"
        return ProviderVmOutput(role=vm_role, resource_id=str(raw.get("id")), private_ipv4=internal, image_identity=self.settings.images[intent.region], actual_shape=raw.get("machineType", "").rsplit("/", 1)[-1], region=region, zone=zone, placement_metadata=StructuredEvidence(value={"resource_policies": raw.get("resourcePolicies", []), "physical_zone_id": "unavailable", "hardware_platform": raw.get("cpuPlatform") or "unavailable"}), connection=RemoteConnectionData(role=vm_role, host=public, user="benchmark", authentication_reference=self.settings.authentication_reference, host_key_reference=str(known_hosts)), public_ipv4=Evidence[str](value=public) if public else Evidence[str](unavailable_reason="no public management IPv4 observed"), physical_zone_id=StringEvidence(unavailable_reason="GCP physical zone identifier unavailable"))

    def _validate_instance(self, instance: dict[str, Any], deployment: ProviderDeploymentInput, role: str) -> dict[str, Any]:
        intent = deployment.vm_a if role == "vm_a" else deployment.vm_b
        expected_ips = {
            "same_zone": ("10.80.1.10", "10.80.1.11"),
            "cross_zone": ("10.80.1.10", "10.80.1.11"),
            "placement_optimization": ("10.80.1.10", "10.80.1.11"),
            "inter_region": ("10.80.1.10", "10.81.1.11"),
        }
        expected_ip = expected_ips[deployment.scenario][0 if role == "vm_a" else 1]
        actual_zone = instance.get("zone", "").rsplit("/", 1)[-1]
        if actual_zone != intent.zone:
            raise GcpProviderError("observed VM zone differs from selected zone", category="instance_shape_mismatch")
        suffix = "a" if role == "vm_a" else "b"
        expected_name = f"{self._namespace(deployment)}-{suffix}"
        expected_instance_path = f"projects/{self.settings.project_id}/zones/{intent.zone}/instances/{expected_name}"
        if instance.get("name") != expected_name or self._int64(instance.get("id")) is None or self._compute_path(instance.get("selfLink")) != expected_instance_path:
            raise GcpProviderError("observed VM selfLink/name/ID differs from the exact project and role resource", category="instance_identity_mismatch")
        interfaces = instance.get("networkInterfaces", [])
        if len(interfaces) != 1 or interfaces[0].get("nicType") != "GVNIC":
            raise GcpProviderError("observed VM must have exactly one gVNIC interface", category="instance_shape_mismatch")
        accesses = interfaces[0].get("accessConfigs", [])
        expected_subnet = f"projects/{self.settings.project_id}/regions/{intent.region}/subnetworks/{self._namespace(deployment)}-" + ("b" if role == "vm_b" and deployment.scenario == "inter_region" else "a")
        expected_network = f"projects/{self.settings.project_id}/global/networks/{self._namespace(deployment)}-net"
        subnet = interfaces[0].get("subnetwork", "")
        if (interfaces[0].get("networkIP") != expected_ip or len(accesses) != 1 or not accesses[0].get("natIP")
                or self._compute_path(subnet) != expected_subnet or self._compute_path(interfaces[0].get("network")) != expected_network):
            raise GcpProviderError("observed VM private or public management address differs from selected topology", category="instance_shape_mismatch")
        if self._unsupported_network_tier(instance.get("networkPerformanceConfig")):
            raise GcpProviderError("observed VM uses unsupported TIER_1 network performance", category="instance_shape_mismatch")
        if instance.get("machineType", "").rsplit("/", 1)[-1] != "n2-standard-4":
            raise GcpProviderError("observed VM machine type differs from selected shape", category="instance_shape_mismatch")
        scheduling = instance.get("scheduling", {})
        if (instance.get("reservationAffinity", {}).get("consumeReservationType") != "NO_RESERVATION" or instance.get("minCpuPlatform")
                or instance.get("guestAccelerators") or scheduling.get("automaticRestart") is not False or "maxRunDuration" in scheduling
                or scheduling.get("nodeAffinities")):
            raise GcpProviderError("observed VM reservation, CPU platform, or accelerator policy differs", category="instance_shape_mismatch")
        observed_disks = instance.get("disks", [])
        boot = next((disk for disk in observed_disks if disk.get("boot")), None)
        if len(observed_disks) != 1 or boot is None or not boot.get("source"):
            raise GcpProviderError("observed boot disk is missing", category="instance_shape_mismatch")
        role_suffix = "a" if role == "vm_a" else "b"
        expected_disk_name = f"{self._namespace(deployment)}-{role_suffix}-boot"
        if boot.get("source", "").rsplit("/", 1)[-1] != expected_disk_name:
            raise GcpProviderError("observed boot disk source differs from the selected role", category="instance_shape_mismatch")
        disk = self._gcloud_json(["compute", "disks", "describe", boot["source"].rsplit("/", 1)[-1], "--zone", intent.zone, "--project", self.settings.project_id, "--format=json"], f"gcp-boot-disk-{role}")
        if self._active_attempt is not None:
            journal = json.loads((self._active_attempt / "attempt.json").read_text(encoding="utf-8"))
            journal.setdefault("observed_disks", []).append({"id": str(disk.get("id")), "name": disk.get("name"), "zone": disk.get("zone", "").rsplit("/", 1)[-1]})
            self._save(self._active_attempt, journal)
        if not self._matches_int(disk.get("sizeGb"), 32) or disk.get("type", "").rsplit("/", 1)[-1] != "pd-balanced":
            raise GcpProviderError("observed boot disk differs from selected 32-GiB pd-balanced disk", category="instance_shape_mismatch")
        disk["sizeGb"] = 32
        expected_labels = {"cnb-run": hashlib.sha256(deployment.run_id.encode()).hexdigest()[:20], "cnb-deploy": hashlib.sha256(deployment.deployment_id.encode()).hexdigest()[:20]}
        if disk.get("name") != expected_disk_name or disk.get("labels") != expected_labels:
            raise GcpProviderError("observed boot disk name or attempt ownership labels differ", category="instance_shape_mismatch")
        expected_image = self.settings.images[intent.region]
        observed_image = str(disk.get("sourceImage", ""))
        if not observed_image or observed_image.split("projects/", 1)[-1] != expected_image.split("projects/", 1)[-1]:
            raise GcpProviderError("observed boot disk image differs from selected pinned image", category="image_mismatch")
        image = self._gcloud_json(["compute", "images", "describe", self.settings.images[intent.region].rsplit("/", 1)[-1], "--project", self.settings.images[intent.region].split("/")[1], "--format=json"], f"gcp-image-read-{role}")
        if image.get("architecture") != "X86_64":
            raise GcpProviderError("selected Ubuntu image is not amd64", category="image_mismatch")
        if image.get("name") != expected_image.rsplit("/", 1)[-1]:
            raise GcpProviderError("observed image identity differs from selected pinned image", category="image_mismatch")
        features = {entry.get("type") for entry in image.get("guestOsFeatures", []) if isinstance(entry, dict)}
        if "GVNIC" not in features:
            raise GcpProviderError("selected image does not advertise the GvNIC guest OS feature", category="image_gvnic_incompatible")
        return disk

    def _trusted_hostkey(self, name: str, zone: str, initial: dict[str, Any]) -> str:
        namespace = self._namespace_from_name(name)
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", namespace):
            raise GcpProviderError("provider resource name does not map to a safe attempt namespace", category="state_path_unsafe")
        hosts_file = self.settings.private_state_root / namespace / "known_hosts"
        root = self.settings.private_state_root.expanduser().absolute()
        if any(parent.is_symlink() for parent in (root, *root.parents) if parent.exists()) or hosts_file.parent.is_symlink() or hosts_file.is_symlink() or os.path.commonpath((str(root), str(hosts_file.absolute()))) != str(root):
            raise GcpProviderError("known_hosts path must remain private and cannot contain symlinks", category="state_path_unsafe")
        before = self._gcloud_json(["compute", "instances", "describe", name, "--zone", zone, "--project", self.settings.project_id, "--format=json"], "gcp-hostkey-instance-before")
        if not self._same_vm_identity(before, initial, name, zone) or self._public_ip(before) != self._public_ip(initial):
            raise GcpProviderError("instance identity changed before host-key read", category="hostkey_identity_mismatch")
        guest_args = ["compute", "instances", "get-guest-attributes", name, "--zone", zone, "--project", self.settings.project_id, "--query-path=hostkeys/", "--format=json"]
        while True:
            try:
                response = self._gcloud_json(guest_args, "gcp-guest-hostkeys")
                break
            except GcpProviderError as exc:
                if exc.category != "guest_attribute_not_found" or self._deadline is None:
                    raise
                remaining = self._deadline - time.monotonic()
                if remaining <= 0:
                    raise GcpProviderError("GCP host-key readiness exceeded the original provision deadline",
                        category="command_timeout", action_id="gcp-guest-hostkeys") from exc
                time.sleep(min(self.GUEST_ATTRIBUTE_POLL_INTERVAL_SECONDS, remaining))
        after = self._gcloud_json(["compute", "instances", "describe", name, "--zone", zone, "--project", self.settings.project_id, "--format=json"], "gcp-hostkey-instance-after")
        if not self._same_vm_identity(after, initial, name, zone) or self._public_ip(after) != self._public_ip(initial):
            raise GcpProviderError("instance identity changed after host-key read", category="hostkey_identity_mismatch")
        # The installed gcloud command's YAML spec selects `queryValue.items`
        # as its result attribute. With --format=json, stdout is therefore the
        # item list itself, not the Compute API response envelope.
        if not isinstance(response, list):
            raise GcpProviderError("authenticated GCP guest-attribute response was malformed", category="gcp_api_read", action_id="gcp-guest-hostkeys")
        items = response
        key = None
        for entry in items:
            if not isinstance(entry, dict) or entry.get("namespace") != "hostkeys":
                continue
            algorithm, blob = entry.get("key", ""), entry.get("value", "")
            if not re.fullmatch(r"(?:ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp\d+)", algorithm) or not isinstance(blob, str):
                continue
            try:
                decoded = base64.b64decode(blob, validate=True)
                if len(decoded) > 16384 or len(decoded) < 4:
                    continue
                embedded, offset = self._ssh_string(decoded, 0)
                if embedded.decode("ascii") != algorithm:
                    continue
                if algorithm == "ssh-ed25519":
                    key_data, offset = self._ssh_string(decoded, offset)
                    if len(key_data) != 32 or offset != len(decoded): continue
                elif algorithm == "ssh-rsa":
                    exponent, offset = self._ssh_string(decoded, offset)
                    modulus, offset = self._ssh_string(decoded, offset)
                    if not exponent or not modulus or offset != len(decoded): continue
                else:
                    curve, offset = self._ssh_string(decoded, offset)
                    point, offset = self._ssh_string(decoded, offset)
                    if curve.decode("ascii") != algorithm.removeprefix("ecdsa-sha2-") or not point or offset != len(decoded): continue
                key = f"{algorithm} {blob}"
                break
            except (ValueError, UnicodeDecodeError, struct.error, IndexError):
                continue
        if key is None:
            raise GcpProviderError("guest attributes contain no valid OpenSSH host public key", category="hostkey_unavailable")
        hosts_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(hosts_file.parent, 0o700)
        nic = after.get("networkInterfaces", [{}])[0]
        host = next((item.get("natIP") for item in nic.get("accessConfigs", []) if item.get("natIP")), None)
        if not host:
            raise GcpProviderError("public management IPv4 unavailable for host-key trust", category="hostkey_unavailable")
        existing = hosts_file.read_text(encoding="utf-8") if hosts_file.exists() and not hosts_file.is_symlink() else ""
        known = [parts[1] for line in existing.splitlines() if len(parts := line.split(maxsplit=1)) == 2 and parts[0] == host]
        if known and key not in known:
            raise GcpProviderError("host-key changed for an already trusted VM address", category="hostkey_mismatch")
        if self._active_attempt is not None:
            journal_path = self._active_attempt / "attempt.json"
            journal = json.loads(journal_path.read_text(encoding="utf-8")) if journal_path.exists() else {}
            bindings = journal.setdefault("known_management_hosts", {})
            vm_id = str(initial.get("id"))
            if vm_id in bindings and bindings[vm_id] != host:
                raise GcpProviderError("public management IPv4 changed for an already bound VM identity", category="hostkey_identity_mismatch")
            bindings[vm_id] = host
            self._save(self._active_attempt, journal)
        fd, tmp = tempfile.mkstemp(dir=hosts_file.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(existing + (f"{host} {key}\n" if not known else "")); stream.flush(); os.fsync(stream.fileno())
            os.chmod(tmp, 0o600); os.replace(tmp, hosts_file)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
        return key

    def _same_vm_identity(self, observed: dict[str, Any], initial: dict[str, Any], name: str, zone: str) -> bool:
        identity = (str(observed.get("id")), observed.get("name"), observed.get("zone", "").rsplit("/", 1)[-1])
        expected = (str(initial.get("id")), name, zone)
        if identity != expected or str(initial.get("name")) != name or initial.get("zone", "").rsplit("/", 1)[-1] != zone:
            return False
        project_path = f"/projects/{self.settings.project_id}/"
        return all(project_path in str(item.get("selfLink", "")) for item in (observed, initial))

    @staticmethod
    def _public_ip(instance: dict[str, Any]) -> str | None:
        interfaces = instance.get("networkInterfaces", [])
        if len(interfaces) != 1:
            return None
        value = next((str(item.get("natIP")) for item in interfaces[0].get("accessConfigs", []) if item.get("natIP")), None)
        try:
            address = ipaddress.ip_address(value) if value else None
        except ValueError:
            return None
        return value if address and address.version == 4 and address.is_global else None

    @staticmethod
    def _ssh_string(payload: bytes, offset: int) -> tuple[bytes, int]:
        if offset + 4 > len(payload):
            raise ValueError("truncated SSH key field")
        length = struct.unpack(">I", payload[offset:offset+4])[0]
        offset += 4
        end = offset + length
        if length == 0 or end > len(payload):
            raise ValueError("invalid SSH key field length")
        return payload[offset:end], end

    @staticmethod
    def _namespace_from_name(name: str) -> str:
        return name.rsplit("-", 1)[0]
