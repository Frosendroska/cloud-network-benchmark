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
from urllib.parse import parse_qsl, urlencode, urlparse

from pydantic import Field, field_validator, model_validator

from ..contracts.admission import ExpiryEvidence, ExpiryRequest, ReservationEvidence
from ..contracts.common import CleanupState, Evidence, FailureEvidence, StrictModel, StructuredEvidence, StringEvidence
from ..contracts.deployment import ProviderDeploymentInput, ProviderDeploymentOutput, ProviderVmOutput, RemoteConnectionData, IdentityContext
from ..contracts.execution import CommandRequest, CommandResult, CommandRunner

SAFETY_LEAD_SECONDS = 30
NETWORK_LIMIT = {"maximum_gbit_per_second": 10, "is_ceiling_not_observation": True, "source": "https://cloud.google.com/compute/docs/network-bandwidth", "checked_on": "2026-10-23"}
REGION_ZONES = {"europe-west3": ("europe-west3-a", "europe-west3-b"), "europe-west2": ("europe-west2-a",)}


class GcpRuntimeSettings(StrictModel):
    project_id: str = Field(pattern=r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
    controller_cidr: str
    images: dict[str, str]
    public_key: str = Field(min_length=1)
    authentication_reference: str = Field(min_length=1)
    private_state_root: Path
    terraform: str = "/private/tmp/cnb-tools/terraform"
    gcloud: str = "/opt/homebrew/bin/gcloud"
    cost_input_id: str = Field(min_length=1)
    cost_input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    cost_source_path: str = Field(min_length=1)
    complete_workflow_cost_handoff: StructuredEvidence
    support_cost_evidence: StructuredEvidence
    complete_workflow_remaining_seconds: float = Field(gt=0)
    primary_review_decision: Optional[StructuredEvidence] = None
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
        if set(self.images) != {"europe-west3", "europe-west2"}:
            raise ValueError("images must pin the selected Frankfurt and London image identities")
        if any(not re.fullmatch(r"projects/ubuntu-os-cloud/global/images/ubuntu-2404-lts-amd64-[a-z0-9-]+", value) for value in self.images.values()):
            raise ValueError("images must be immutable Ubuntu 24.04 amd64 image identities, not aliases or families")
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
    def __init__(self, runner: CommandRunner, settings: GcpRuntimeSettings, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.runner, self.settings, self.clock = runner, settings, clock
        self._command_context = local()

    @property
    def _deadline(self) -> float | None:
        return getattr(self._command_context, "deadline", None)

    @_deadline.setter
    def _deadline(self, value: float | None) -> None:
        self._command_context.deadline = value

    @property
    def _active_attempt(self) -> Path | None:
        return getattr(self._command_context, "attempt", None)

    @_active_attempt.setter
    def _active_attempt(self, value: Path | None) -> None:
        self._command_context.attempt = value

    def register_expiry(self, deployment: ProviderDeploymentInput, request: ExpiryRequest) -> ExpiryEvidence:
        self._deadline = None
        reservation = self._validate(deployment, request)
        if deployment.scenario in {"cross_zone", "inter_region"}:
            self._validate_review(deployment)
        trigger = reservation.expires_at.value - timedelta(seconds=SAFETY_LEAD_SECONDS)
        attempt = self._attempt_dir(deployment)
        self._active_attempt = attempt
        journal = self._load(attempt, deployment)
        self._fit(reservation, trigger, self.settings.complete_workflow_remaining_seconds)
        if journal.get("registered"):
            if journal.get("binding_sha256") != self._binding_fingerprint(deployment):
                raise GcpProviderError("registered attempt no longer matches its immutable inputs", category="state_identity_mismatch")
            return ExpiryEvidence.model_validate(journal["expiry"])
        if journal.get("registration_may_have_started"):
            raise GcpProviderError("an earlier interrupted registration cannot be restarted under this attempt", category="cleanup_open")
        self._deadline = min(time.monotonic() + self.settings.registration_timeout_seconds, time.monotonic() + max(0.0, (trigger - self.clock()).total_seconds()))
        journal.update({"cap_started_at": reservation.cap_started_at.value.isoformat(), "cap_expires_at": reservation.expires_at.value.isoformat(), "trigger": trigger.isoformat(), "registration_may_have_started": True, "binding_sha256": self._binding_fingerprint(deployment), "reservation_id": reservation.reservation_id.value, "controller_session_id": str(uuid.uuid4())})
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
            if deployment.scenario == "placement_optimization" and not placement_policy_id:
                raise GcpProviderError("S3 compact placement policy was not observed", category="placement_missing")
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
            self._fit(reservation, trigger, self.settings.complete_workflow_remaining_seconds)
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
        attempt = self._attempt_dir(deployment)
        self._active_attempt = attempt
        journal = self._load(attempt, deployment)
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
        trigger = reservation.expires_at.value - timedelta(seconds=SAFETY_LEAD_SECONDS)
        self._fit(reservation, trigger, self.settings.complete_workflow_remaining_seconds)
        self._verify_registered_templates(journal, trigger, deployment)
        if deployment.scenario in {"cross_zone", "inter_region"}:
            self._validate_review(deployment)
        self._fit(reservation, trigger, self.settings.complete_workflow_remaining_seconds)
        self._deadline = min(time.monotonic() + (deployment.provisioning_timeout_seconds or self.settings.registration_timeout_seconds), time.monotonic() + max(0.0, (trigger-self.clock()).total_seconds()))
        journal["creation_may_have_started"] = True
        self._save(attempt, journal)
        started = self.clock().astimezone(timezone.utc)
        try:
            templates = journal["templates"]
            self._fit(reservation, trigger, self.settings.complete_workflow_remaining_seconds)
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
                actual_vm_path=urlparse(str(instance.get("selfLink", ""))).path.split("/compute/v1/",1)[-1]
                output_vm_path=urlparse(str(resource.get("self_link", ""))).path.split("/compute/v1/",1)[-1]
                if not re.fullmatch(r"[0-9]+",str(instance.get("id",""))) or actual_vm_path.lstrip("/") != expected_vm_path or actual_vm_path.lstrip("/") != output_vm_path.lstrip("/"):
                    raise GcpProviderError("observed VM self link is outside the selected project, zone, or Terraform output", category="instance_identity_mismatch")
                scheduling = instance.get("scheduling", {})
                if scheduling.get("provisioningModel") != "STANDARD" or scheduling.get("instanceTerminationAction") != "STOP" or scheduling.get("terminationTime") != trigger.isoformat().replace("+00:00", "Z") or scheduling.get("automaticRestart") is not False or "maxRunDuration" in scheduling:
                    raise GcpProviderError("observed VM does not inherit absolute STOP scheduling", category="instance_policy_mismatch")
                observed_disks[role] = self._validate_instance(instance, deployment, role)
                policies = instance.get("resourcePolicies", [])
                expected_policy = journal.get("compact_policy_id")
                if (deployment.scenario == "placement_optimization" and expected_policy not in policies) or (deployment.scenario != "placement_optimization" and policies):
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
        attempt = self._attempt_dir(deployment)
        self._active_attempt = attempt
        self._deadline = time.monotonic() + (deployment.cleanup_timeout_seconds or self.settings.registration_timeout_seconds)
        journal = self._load(attempt, deployment)
        if journal.get("cleanup_state") == "succeeded":
            return GcpCleanupResult(cleanup_state=CleanupState.SUCCEEDED, inventory=StructuredEvidence(value=journal["inventory"]), expiry=Evidence[ExpiryEvidence](value=ExpiryEvidence.model_validate(journal["expiry"])) if journal.get("expiry") else Evidence[ExpiryEvidence](unavailable_reason="no native expiry was registered"), primary_failure=FailureEvidence.model_validate(journal["primary_failure"]) if journal.get("primary_failure") else None)
        failure = None
        inventory: dict[str, Any] = {"known": [], "pages_complete": False}
        try:
            if journal.get("binding_sha256") and journal["binding_sha256"] != self._binding_fingerprint(deployment):
                raise GcpProviderError("cleanup caller differs from the immutable registered project/source/cap/settings", category="state_identity_mismatch")
            if journal.get("creation_may_have_started"):
                self._terraform(attempt, "pair", "destroy", deployment, {})
            inventory = self._inventory(deployment)
            if inventory["instances"]:
                raise GcpProviderError("owned compute remains; protection must stay installed", category="residual_compute")
            if journal.get("registered") or journal.get("registration_may_have_started"):
                self._terraform(attempt, "protection", "destroy", deployment, {})
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
            result = GcpCleanupResult(cleanup_state=CleanupState.SUCCEEDED, inventory=StructuredEvidence(value=inventory), expiry=Evidence[ExpiryEvidence](value=ExpiryEvidence.model_validate(expiry)) if expiry else Evidence[ExpiryEvidence](unavailable_reason="no native expiry was registered"), primary_failure=FailureEvidence.model_validate(journal["primary_failure"]) if journal.get("primary_failure") else None)
            journal.update({"cleanup_state": "succeeded", "launch_blocked": False, "inventory": inventory})
            self._save(attempt, journal)
            return result
        except Exception as exc:
            failure = self._failure(exc)
            journal.update({"cleanup_state": "failed", "launch_blocked": True, "cleanup_failure": failure.model_dump(mode="json"), "inventory": inventory})
            self._save(attempt, journal)
            expiry = journal.get("expiry")
            return GcpCleanupResult(cleanup_state=CleanupState.FAILED, cleanup_failure=failure, primary_failure=FailureEvidence.model_validate(journal["primary_failure"]) if journal.get("primary_failure") else None, inventory=StructuredEvidence(value=inventory), expiry=Evidence[ExpiryEvidence](value=ExpiryEvidence.model_validate(expiry)) if expiry else Evidence[ExpiryEvidence](unavailable_reason="no native expiry was registered"))

    def _validate(self, deployment: ProviderDeploymentInput, request: ExpiryRequest) -> ReservationEvidence:
        self._validate_deployment(deployment)
        reservation = deployment.reservation.value
        if reservation is None or reservation.state != "reserved":
            raise GcpProviderError("active reservation evidence is required", category="reservation_required")
        trigger = reservation.expires_at.value - timedelta(seconds=SAFETY_LEAD_SECONDS)
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
        if deployment.vm_a.region != "europe-west3" or deployment.vm_b.region not in {"europe-west3", "europe-west2"}:
            raise GcpProviderError("F07 supports only Frankfurt roles and London receiver", category="scenario_unsupported")
        if deployment.vm_a.vm_shape.lower() not in {"n2-standard-4", "n2_standard_4"} or deployment.vm_b.vm_shape.lower() not in {"n2-standard-4", "n2_standard_4"}:
            raise GcpProviderError("GCP roles must use selected n2-standard-4 shape", category="shape_unsupported")
        if deployment.vm_a.role != "vm_a" or deployment.vm_b.role != "vm_b" or deployment.vm_a.function != "client_traffic_generator" or deployment.vm_b.function != "server_receiver":
            raise GcpProviderError("GCP deployment requires exact client A and receiver B identities", category="identity_mismatch")
        expected_zones = {"same_zone": ("europe-west3-a", "europe-west3-a"), "cross_zone": ("europe-west3-a", "europe-west3-b"), "placement_optimization": ("europe-west3-a", "europe-west3-a"), "inter_region": ("europe-west3-a", "europe-west2-a")}
        if deployment.scenario not in expected_zones or (deployment.vm_a.zone, deployment.vm_b.zone) != expected_zones[deployment.scenario]:
            raise GcpProviderError("scenario zones must match the selected V2 topology", category="scenario_unsupported")
        if deployment.scenario == "placement_optimization" and deployment.placement.kind != "compact_placement_policy":
            raise GcpProviderError("S3 requires same-zone compact placement", category="scenario_unsupported")
        if deployment.scenario == "inter_region" and deployment.vm_b.region != "europe-west2":
            raise GcpProviderError("S4 receiver must be in London", category="scenario_unsupported")

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
        props = described.get("properties", {})
        scheduling = props.get("scheduling", {})
        expected_name = f"{self._namespace(deployment)}-{role[-1]}"
        expected_region = deployment.vm_a.region if role == "vm_a" or deployment is None else deployment.vm_b.region
        expected_zone = deployment.vm_a.zone if role == "vm_a" or deployment is None else deployment.vm_b.zone
        expected_ip = {"same_zone": ("10.80.1.10", "10.80.1.11"), "cross_zone": ("10.80.1.10", "10.80.1.11"), "placement_optimization": ("10.80.1.10", "10.80.1.11"), "inter_region": ("10.80.1.10", "10.81.1.11")}
        role_index = 0 if role == "vm_a" else 1
        exp_image = self.settings.images[expected_region]
        exp_zone = expected_zone
        exp_ip = expected_ip[deployment.scenario][role_index]
        expected_subnet = f"/projects/{self.settings.project_id}/regions/{expected_region}/subnetworks/{self._namespace(deployment)}-" + ("b" if role == "vm_b" and deployment.scenario == "inter_region" else "a")
        if item.get("zone") != exp_zone:
            raise GcpProviderError("template role is bound to a different selected zone", category="expiry_mismatch")
        link = str(item.get("self_link_unique", ""))
        canonical_link = f"projects/{self.settings.project_id}/global/instanceTemplates/{item.get('name')}?uniqueId={item.get('id')}"
        parsed_link = urlparse(str(described.get("selfLinkWithId", "")))
        actual_path = parsed_link.path.split("/compute/v1/", 1)[-1].lstrip("/")
        actual_unique_link = actual_path + ("?" + urlencode(sorted(parse_qsl(parsed_link.query))) if parsed_link.query else "")
        disk = props.get("disks", [])
        nics = props.get("networkInterfaces", [])
        metadata = {x.get("key"): x.get("value") for x in props.get("metadata", {}).get("items", [])}
        labels = props.get("labels", {})
        expected_labels = {"cnb-run": hashlib.sha256(deployment.run_id.encode()).hexdigest()[:20], "cnb-deploy": hashlib.sha256(deployment.deployment_id.encode()).hexdigest()[:20]}
        if (not re.fullmatch(r"[0-9]+", str(item.get("id", ""))) or str(described.get("id")) != str(item.get("id")) or described.get("name") != item.get("name") or item.get("name") != expected_name
            or f"projects/{self.settings.project_id}/" not in link or link != canonical_link or actual_unique_link != canonical_link
            or scheduling.get("provisioningModel") != "STANDARD" or scheduling.get("instanceTerminationAction") != "STOP"
            or scheduling.get("terminationTime") != trigger.isoformat().replace("+00:00", "Z")
            or scheduling.get("automaticRestart") is not False or "maxRunDuration" in scheduling
            or props.get("machineType", "").rsplit("/", 1)[-1] != "n2-standard-4"
            or len(disk) != 1 or not disk[0].get("boot") or disk[0].get("autoDelete") is not True
            or disk[0].get("initializeParams", {}).get("sourceImage", "").split("projects/", 1)[-1] != exp_image.split("projects/", 1)[-1]
            or disk[0].get("initializeParams", {}).get("diskType", "").rsplit("/", 1)[-1] != "pd-balanced"
            or disk[0].get("initializeParams", {}).get("diskSizeGb") != 32
            or disk[0].get("initializeParams", {}).get("diskName") != f"{self._namespace(deployment)}-{role[-1]}-boot" or disk[0].get("initializeParams", {}).get("labels") != expected_labels
            or len(nics) != 1 or nics[0].get("nicType") != "GVNIC" or nics[0].get("networkIP") != exp_ip or expected_subnet not in nics[0].get("subnetwork", "")
            or nics[0].get("networkPerformanceConfig", {}).get("totalEgressBandwidthTier") == "TIER_1"
            or len(nics[0].get("accessConfigs", [])) != 1
            or metadata.get("enable-guest-attributes") != "TRUE" or metadata.get("enable-oslogin") != "FALSE" or metadata.get("block-project-ssh-keys") != "TRUE"
            or metadata.get("cnb-bootstrap-template") != deployment.bootstrap_template
            or metadata.get("cnb-bootstrap-inputs") != json.dumps(deployment.bootstrap_inputs, sort_keys=True, separators=(",", ":"))
            or metadata.get("cnb-network-intent") != json.dumps(deployment.vm_a.network.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            or metadata.get("ssh-keys") != f"benchmark:{self.settings.public_key}"
            or props.get("reservationAffinity", {}).get("consumeReservationType") != "NO_RESERVATION"
            or props.get("resourcePolicies", []) != ([item.get("expected_policy")] if item.get("expected_policy") else [])
            or labels != expected_labels or "maxRunDuration" in props or props.get("guestAccelerators")):
            raise GcpProviderError("immutable template properties or project-scoped unique identity differ from the selected role", category="expiry_mismatch")

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
                if value.get("launch_blocked") or value.get("cleanup_state") is not None or value.get("creation_may_have_started"):
                    raise GcpProviderError("this attempt already has creation or cleanup state and cannot be launched again", category="cleanup_open")
                continue
            if value.get("registration_may_have_started") and not value.get("registered") and value.get("protection_cleanup_state") != "succeeded":
                raise GcpProviderError("an interrupted registration has uncertain protection and blocks launches", category="cleanup_open")
            if value.get("creation_may_have_started") and value.get("cleanup_state") != "succeeded":
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

    def _fit(self, reservation: ReservationEvidence, trigger: datetime, seconds: float) -> None:
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None or trigger.tzinfo is None or trigger.utcoffset() is None:
            raise GcpProviderError("reservation and trigger times must be timezone-aware", category="cap_order")
        now = now.astimezone(timezone.utc)
        trigger = trigger.astimezone(timezone.utc)
        cap_start = reservation.cap_started_at.value.astimezone(timezone.utc)
        cap_end = reservation.expires_at.value.astimezone(timezone.utc)
        if not isfinite(seconds) or seconds <= 0:
            raise GcpProviderError("complete remaining workflow estimate must be positive and finite", category="cap_insufficient")
        if trigger != cap_end - timedelta(seconds=SAFETY_LEAD_SECONDS):
            raise GcpProviderError("native STOP trigger must remain tied to the original reservation cap", category="cap_mismatch")
        if cap_start > now or now > trigger or now + timedelta(seconds=seconds) > trigger:
            raise GcpProviderError("whole remaining workflow must fit before original native STOP initiation lead", category="cap_insufficient")

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
            old_events = current.get("commands", [])
            supplied_events = journal.get("commands", [])
            if not isinstance(old_events, list) or not isinstance(supplied_events, list):
                raise GcpProviderError("command journal is malformed", category="cleanup_unknown")
            if len(old_events) > len(supplied_events) and old_events[:len(supplied_events)] == supplied_events:
                journal["commands"] = old_events
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
        limit = min(timeout or self.settings.command_timeout_seconds, self.settings.command_timeout_seconds)
        if self._deadline is not None:
            remaining = self._deadline - time.monotonic()
            if remaining <= 0: raise GcpProviderError("absolute GCP command deadline elapsed", category="command_timeout", action_id=action)
            limit = min(limit, max(1, int(remaining)))
        request = CommandRequest(action_id=action, action_type="gcp_provider_control", argv=argv, cwd=str(cwd), timeout_seconds=limit, environment_classification="contains_secret_references")
        result = self.runner.run(request)
        self._record_command(request, result)
        return result

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
        if action == "destroy" and vars_path.exists():
            if vars_path.is_symlink(): raise GcpProviderError("Terraform input cannot be a symlink", category="state_path_unsafe")
            variables = json.loads(vars_path.read_text(encoding="utf-8"))
            if variables.get("project_id") != self.settings.project_id:
                raise GcpProviderError("Terraform inputs belong to another project", category="state_identity_mismatch")
        else:
            variables = None
        common = {"project_id": self.settings.project_id}
        if variables is not None:
            template = variables
        elif root == "protection":
            template = {**common, "namespace": self._namespace(deployment), "scenario": deployment.scenario.value if hasattr(deployment.scenario, "value") else deployment.scenario, "expiry_time": "", "image_a": self.settings.images[deployment.vm_a.region], "image_b": self.settings.images[deployment.vm_b.region], "zone_a": deployment.vm_a.zone, "zone_b": deployment.vm_b.zone, "controller_cidr": self.settings.controller_cidr, "public_key": self.settings.public_key, "labels": {"cnb-run": hashlib.sha256(deployment.run_id.encode()).hexdigest()[:20], "cnb-deploy": hashlib.sha256(deployment.deployment_id.encode()).hexdigest()[:20]}, "bootstrap_template": deployment.bootstrap_template, "bootstrap_inputs": deployment.bootstrap_inputs, "network_intent": deployment.vm_a.network.model_dump(mode="json")}
        else:
            template = {**common, "name_a": self._namespace(deployment) + "-a", "name_b": self._namespace(deployment) + "-b", "zone_a": deployment.vm_a.zone, "zone_b": deployment.vm_b.zone}
        template.update(extra)
        serialized_vars = json.dumps(template, sort_keys=True).encode("utf-8")
        self._write_private(vars_path, serialized_vars)
        if root == "pair" and action == "apply":
            journal = self._load(attempt, deployment)
            journal["pair_binding"] = {"config_sha256": hashlib.sha256(serialized_vars).hexdigest(), "template_a": template["template_a"], "template_b": template["template_b"], "project_id": self.settings.project_id, "zone_a": deployment.vm_a.zone, "zone_b": deployment.vm_b.zone}
            self._save(attempt, journal)
        root_path = Path(__file__).resolve().parents[3] / "terraform" / "gcp" / root
        rc = self._run(f"gcp-{root}-{action}", [self.settings.terraform, f"-chdir={root_path}", action, "-input=false", "-auto-approve", f"-state={work / 'terraform.tfstate'}", f"-var-file={vars_path}"], work, timeout=deployment.provisioning_timeout_seconds if action == "apply" else deployment.cleanup_timeout_seconds)
        if rc.outcome != "succeeded" or rc.exit_code != 0:
            raise GcpProviderError(f"GCP Terraform {root} {action} failed", category="terraform_failed", action_id=rc.action_id)
        if action == "destroy": return {}
        result = self._run(f"gcp-{root}-outputs", [self.settings.terraform, f"-chdir={root_path}", "output", "-json", f"-state={work / 'terraform.tfstate'}"], work)
        if result.outcome != "succeeded": raise GcpProviderError("Terraform output read failed", category="terraform_output")
        return json.loads(result.stdout)

    def _gcloud_json(self, args: list[str], action: str) -> dict[str, Any]:
        result = self._run(action, [self.settings.gcloud, *args], self.settings.private_state_root)
        if result.outcome != "succeeded" or result.exit_code != 0: raise GcpProviderError("authenticated GCP readback failed", category="gcp_api_read", action_id=action)
        if args and args[0] == "compute" and len(args) > 1 and args[1] == "instances" and args[2:3] == ["list"] or args and args[0] == "compute" and len(args) > 2 and args[2] == "list":
            if re.search(r"(?i)(warning|incomplete|denied|permission|partial|some zones|not all|failed to list)", result.stderr or ""):
                raise GcpProviderError(f"inventory scope {action} returned a completeness warning", category="inventory_unknown", action_id=action)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise GcpProviderError(f"authenticated GCP response for {action} was malformed", category="gcp_api_read", action_id=action) from exc

    @staticmethod
    def _outputs(value: dict[str, Any]) -> dict[str, Any]:
        return {key: item.get("value", item) for key, item in value.items()}

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

    def _inventory(self, deployment: ProviderDeploymentInput) -> dict[str, Any]:
        instances, disks, addresses, support = [], [], [], []
        dep_hash = hashlib.sha256(deployment.deployment_id.encode()).hexdigest()[:20]
        namespace = self._namespace(deployment)
        journal = self._load(self._attempt_dir(deployment), deployment)
        known_vms = {(str(item.get("id")), item.get("name"), item.get("zone")) for item in journal.get("observed_resources", {}).values() if item.get("id") and item.get("name") and item.get("zone")}
        known_disks = {(str(item.get("id")), item.get("name"), item.get("zone")) for item in journal.get("observed_disks", []) if item.get("id") and item.get("name") and item.get("zone")}
        known_templates = {(str(item.get("id")), item.get("name")) for item in journal.get("templates", {}).values() if item.get("id") and item.get("name")}
        owned_labels = {"cnb-run": hashlib.sha256(deployment.run_id.encode()).hexdigest()[:20], "cnb-deploy": dep_hash}
        expected_instance_names = {f"{namespace}-a", f"{namespace}-b"}
        response = self._gcloud_json(["compute", "instances", "list", "--project", self.settings.project_id, "--format=json"], "gcp-inventory-all-zones")
        if not isinstance(response, list): raise GcpProviderError("all-zone instance inventory pagination shape unknown", category="inventory_unknown")
        instances.extend(item for item in response if (str(item.get("id")), item.get("name"), item.get("zone", "").rsplit("/", 1)[-1]) in known_vms or all(item.get("labels", {}).get(key) == value for key, value in owned_labels.items()) or item.get("name") in expected_instance_names)
        disk_response = self._gcloud_json(["compute", "disks", "list", "--project", self.settings.project_id, "--format=json"], "gcp-disk-inventory-all-zones")
        if not isinstance(disk_response, list): raise GcpProviderError("all-zone disk inventory pagination shape unknown", category="inventory_unknown")
        expected_disk_names = {f"{namespace}-a-boot", f"{namespace}-b-boot"}
        disks.extend(item for item in disk_response if (str(item.get("id")), item.get("name"), item.get("zone", "").rsplit("/", 1)[-1]) in known_disks or all(item.get("labels", {}).get(key) == value for key, value in owned_labels.items()) or item.get("name") in expected_disk_names)
        for region in sorted({deployment.vm_a.region, deployment.vm_b.region}):
            result = self._gcloud_json(["compute", "addresses", "list", f"--regions={region}", "--project", self.settings.project_id, "--format=json"], f"gcp-address-inventory-{region}")
            if not isinstance(result, list): raise GcpProviderError("regional address inventory pagination shape unknown", category="inventory_unknown")
            addresses.extend(item for item in result if str(item.get("name", "")).startswith(namespace))
            result = self._gcloud_json(["compute", "subnetworks", "list", f"--regions={region}", "--project", self.settings.project_id, "--format=json"], f"gcp-subnet-inventory-{region}")
            if not isinstance(result, list): raise GcpProviderError("subnetwork inventory pagination shape unknown", category="inventory_unknown")
            support.extend(item for item in result if str(item.get("name", "")).startswith(namespace))
        for collection, command in (("instance_templates", ["compute", "instance-templates", "list"]), ("networks", ["compute", "networks", "list"]), ("firewalls", ["compute", "firewall-rules", "list"]), ("routes", ["compute", "routes", "list"])):
            result = self._gcloud_json([*command, "--project", self.settings.project_id, "--format=json"], f"gcp-{collection}-inventory")
            if not isinstance(result, list): raise GcpProviderError(f"{collection} inventory pagination shape unknown", category="inventory_unknown")
            support.extend(item for item in result if str(item.get("name", "")).startswith(namespace) or (collection == "instance_templates" and (str(item.get("id")), item.get("name")) in known_templates))
        for region in sorted({deployment.vm_a.region, deployment.vm_b.region}):
            result = self._gcloud_json(["compute", "resource-policies", "list", f"--regions={region}", "--project", self.settings.project_id, "--format=json"], f"gcp-policy-inventory-{region}")
            if not isinstance(result, list): raise GcpProviderError("resource policy inventory pagination shape unknown", category="inventory_unknown")
            support.extend(item for item in result if str(item.get("name", "")).startswith(namespace))
        global_addresses = self._gcloud_json(["compute", "addresses", "list", "--global", "--project", self.settings.project_id, "--format=json"], "gcp-global-address-inventory")
        if not isinstance(global_addresses, list): raise GcpProviderError("global address inventory pagination shape unknown", category="inventory_unknown")
        addresses.extend(item for item in global_addresses if str(item.get("name", "")).startswith(namespace))
        resources = [*instances, *disks, *addresses, *support]
        return {"pages_complete": True, "zone_scope": "all_project_zones", "regions_scanned": sorted({deployment.vm_a.region, deployment.vm_b.region}), "completed_scopes": ["all_project_zones", *sorted({deployment.vm_a.region, deployment.vm_b.region}), "global"], "instances": instances, "disks": disks, "addresses": addresses, "support_resources": support, "resources": resources}

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
        interfaces = instance.get("networkInterfaces", [])
        if len(interfaces) != 1 or interfaces[0].get("nicType") != "GVNIC":
            raise GcpProviderError("observed VM must have exactly one gVNIC interface", category="instance_shape_mismatch")
        accesses = interfaces[0].get("accessConfigs", [])
        expected_subnet_suffix = f"/regions/{intent.region}/subnetworks/{self._namespace(deployment)}-" + ("b" if role == "vm_b" and deployment.scenario == "inter_region" else "a")
        subnet = interfaces[0].get("subnetwork", "")
        if interfaces[0].get("networkIP") != expected_ip or len(accesses) != 1 or not accesses[0].get("natIP") or expected_subnet_suffix not in subnet:
            raise GcpProviderError("observed VM private or public management address differs from selected topology", category="instance_shape_mismatch")
        if interfaces[0].get("networkPerformanceConfig", {}).get("totalEgressBandwidthTier") == "TIER_1":
            raise GcpProviderError("observed VM uses unsupported TIER_1 network performance", category="instance_shape_mismatch")
        if instance.get("machineType", "").rsplit("/", 1)[-1] != "n2-standard-4":
            raise GcpProviderError("observed VM machine type differs from selected shape", category="instance_shape_mismatch")
        if instance.get("reservationAffinity", {}).get("consumeReservationType") != "NO_RESERVATION" or instance.get("minCpuPlatform") or instance.get("guestAccelerators") or instance.get("scheduling", {}).get("automaticRestart") is not False or "maxRunDuration" in instance.get("scheduling", {}):
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
        if int(disk.get("sizeGb", 0)) != 32 or disk.get("type", "").rsplit("/", 1)[-1] != "pd-balanced":
            raise GcpProviderError("observed boot disk differs from selected 32-GiB pd-balanced disk", category="instance_shape_mismatch")
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
        response = self._gcloud_json(["compute", "instances", "get-guest-attributes", name, "--zone", zone, "--project", self.settings.project_id, "--query-path=hostkeys/", "--format=json"], "gcp-guest-hostkeys")
        after = self._gcloud_json(["compute", "instances", "describe", name, "--zone", zone, "--project", self.settings.project_id, "--format=json"], "gcp-hostkey-instance-after")
        if not self._same_vm_identity(after, initial, name, zone) or self._public_ip(after) != self._public_ip(initial):
            raise GcpProviderError("instance identity changed after host-key read", category="hostkey_identity_mismatch")
        items = response.get("queryValue", {}).get("items", [])
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
