from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import tempfile
import time
from datetime import datetime, timedelta, timezone
from math import isfinite
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import Field, field_validator, model_validator

from ..contracts.admission import ExpiryEvidence, ExpiryRequest, ReservationEvidence
from ..contracts.common import CleanupState, Evidence, FailureEvidence, StrictModel, StructuredEvidence, StringEvidence
from ..contracts.deployment import ProviderDeploymentInput, ProviderDeploymentOutput, ProviderVmOutput, RemoteConnectionData, IdentityContext
from ..contracts.execution import CommandRequest, CommandResult, CommandRunner

SAFETY_LEAD_SECONDS = 30
NETWORK_LIMIT = {"maximum_gbit_per_second": 10, "is_ceiling_not_observation": True, "source": "https://cloud.google.com/compute/docs/network-bandwidth", "checked_on": "2026-10-23"}
REGION_ZONES = {"eu-central2": ("eu-central2-a", "eu-central2-b"), "europe-west2": ("europe-west2-a",)}


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
        handoff = self.complete_workflow_cost_handoff.value or {}
        for field in ("source_path", "source_sha256", "prices_checked_on", "estimated_total_usd", "units"):
            if field not in handoff:
                raise ValueError(f"complete-workflow cost evidence must include {field}")
        if handoff["source_path"] != self.cost_source_path or handoff["source_sha256"] != self.cost_input_sha256:
            raise ValueError("cost source identity must match the supplied dated source")
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
        self._deadline: float | None = None

    def register_expiry(self, deployment: ProviderDeploymentInput, request: ExpiryRequest) -> ExpiryEvidence:
        reservation = self._validate(deployment, request)
        trigger = reservation.expires_at.value - timedelta(seconds=SAFETY_LEAD_SECONDS)
        attempt = self._attempt_dir(deployment)
        journal = self._load(attempt, deployment)
        self._fit(reservation, trigger, self.settings.complete_workflow_remaining_seconds)
        if journal.get("registered"):
            return ExpiryEvidence.model_validate(journal["expiry"])
        self._deadline = time.monotonic() + self.settings.registration_timeout_seconds
        journal.update({"cap_started_at": reservation.cap_started_at.value.isoformat(), "cap_expires_at": reservation.expires_at.value.isoformat(), "trigger": trigger.isoformat(), "registration_may_have_started": True})
        self._save(attempt, journal)
        try:
            out = self._terraform(attempt, "protection", "apply", deployment, {"expiry_time": trigger.isoformat().replace("+00:00", "Z")})
            templates = self._outputs(out).get("templates", {})
            if set(templates) != {"vm_a", "vm_b"}:
                raise GcpProviderError("both template identities were not observed", category="expiry_uncertain")
            journal["templates"] = templates
            self._save(attempt, journal)
            observed = {}
            for role, template in templates.items():
                described = self._gcloud_json(["compute", "instance-templates", "describe", template["name"], "--project", self.settings.project_id, "--format=json"], f"gcp-template-read-{role}")
                scheduling = described.get("properties", {}).get("scheduling", {})
                if scheduling.get("provisioningModel") != "STANDARD" or scheduling.get("instanceTerminationAction") != "STOP" or scheduling.get("terminationTime") != trigger.isoformat().replace("+00:00", "Z"):
                    raise GcpProviderError("template readback does not match immutable STOP policy", category="expiry_mismatch")
                if not template.get("self_link_unique") or not template.get("id"):
                    raise GcpProviderError("unique template binding was not observed", category="expiry_uncertain")
                observed[role] = {"id": str(template["id"]), "name": template["name"], "self_link_unique": template["self_link_unique"], "sha256": hashlib.sha256(json.dumps(described, sort_keys=True).encode()).hexdigest(), "creationTimestamp": described.get("creationTimestamp"), "scheduling": scheduling}
            now = self.clock().astimezone(timezone.utc)
            if not reservation.cap_started_at.value <= now <= trigger:
                raise GcpProviderError("expiry registration is outside the original reservation window", category="cap_order")
            evidence = ExpiryEvidence(schema_version=2, run_id=deployment.run_id, deployment_id=deployment.deployment_id, provider="gcp", action="stop", state="registered", registration_id=Evidence[str](value=f"{observed['vm_a']['id']}:{observed['vm_b']['id']}"), registered_at=Evidence[datetime](value=now), expires_at=Evidence[datetime](value=trigger), target_selector=request.target_selector, provider_metadata=StructuredEvidence(value={"templates": observed, "reservation_id": reservation.reservation_id.value, "cost_source_id": self.settings.cost_input_id, "cost_source_sha256": self.settings.cost_input_sha256, "native_absolute_stop": True}))
            journal.update({"registered": True, "expiry": evidence.model_dump(mode="json"), "templates": observed})
            self._save(attempt, journal)
            return evidence
        except Exception as exc:
            journal["registration_failure"] = self._failure(exc).model_dump(mode="json")
            try:
                self._terraform(attempt, "protection", "destroy", deployment, {})
                journal["unused_protection_destroy_attempted"] = True
            except Exception as cleanup_error:
                journal["cleanup_failure"] = self._failure(cleanup_error).model_dump(mode="json")
                journal["launch_blocked"] = True
            self._save(attempt, journal)
            return ExpiryEvidence(schema_version=2, run_id=deployment.run_id, deployment_id=deployment.deployment_id, provider="gcp", action="stop", state="failed", registration_id=Evidence[str](unavailable_reason="both immutable templates were not confirmed"), registered_at=Evidence[datetime](unavailable_reason="registration incomplete"), expires_at=Evidence[datetime](value=trigger), target_selector=request.target_selector, provider_metadata=StructuredEvidence(value={"partial_protection": journal.get("templates", {}) or {}}), failure=self._failure(exc))

    def provision(self, deployment: ProviderDeploymentInput) -> ProviderDeploymentOutput:
        self._validate_deployment(deployment)
        attempt = self._attempt_dir(deployment)
        journal = self._load(attempt, deployment)
        if not journal.get("registered"):
            raise GcpProviderError("verified STOP templates are required before pair creation", category="expiry_required")
        supplied_expiry = deployment.expiry.value
        recorded_expiry = ExpiryEvidence.model_validate(journal["expiry"])
        if supplied_expiry is None or supplied_expiry.state != "registered" or supplied_expiry.registration_id != recorded_expiry.registration_id or supplied_expiry.expires_at != recorded_expiry.expires_at:
            raise GcpProviderError("deployment handoff must carry the matching registered STOP evidence", category="expiry_mismatch")
        reservation = deployment.reservation.value
        trigger = reservation.expires_at.value - timedelta(seconds=SAFETY_LEAD_SECONDS)
        self._fit(reservation, trigger, self.settings.complete_workflow_remaining_seconds)
        self._verify_registered_templates(journal, trigger)
        if deployment.scenario in {"cross_zone", "inter_region"}:
            self._validate_review(deployment)
        journal["creation_may_have_started"] = True
        self._save(attempt, journal)
        started = self.clock().astimezone(timezone.utc)
        try:
            templates = journal["templates"]
            pair = self._terraform(attempt, "pair", "apply", deployment, {"template_a": templates["vm_a"]["self_link_unique"], "template_b": templates["vm_b"]["self_link_unique"]})
            values = self._outputs(pair).get("instances", {})
            output = {}
            for role in ("vm_a", "vm_b"):
                resource = values[role]
                instance = self._gcloud_json(["compute", "instances", "describe", resource["name"], "--zone", resource["zone"], "--project", self.settings.project_id, "--format=json"], f"gcp-instance-read-{role}")
                scheduling = instance.get("scheduling", {})
                if scheduling.get("provisioningModel") != "STANDARD" or scheduling.get("instanceTerminationAction") != "STOP" or scheduling.get("terminationTime") != trigger.isoformat().replace("+00:00", "Z"):
                    raise GcpProviderError("observed VM does not inherit absolute STOP scheduling", category="instance_policy_mismatch")
                self._validate_instance(instance, deployment, role)
                host_key = self._trusted_hostkey(resource["name"], resource["zone"], instance)
                resource["_trusted_host_key"] = host_key
                output[role] = instance
            vms = [self._vm_output(role, output[role], deployment) for role in ("vm_a", "vm_b")]
            finished = self.clock().astimezone(timezone.utc)
            result = ProviderDeploymentOutput(schema_version=2, identity=IdentityContext(experiment_id=deployment.experiment_id, config_id=deployment.config_id, campaign_id=deployment.campaign_id, observation_id=deployment.observation_id, run_id=deployment.run_id, deployment_id=deployment.deployment_id, provider="gcp"), run_id=deployment.run_id, deployment_id=deployment.deployment_id, mode=deployment.mode, provider="gcp", scenario=deployment.scenario, apply_action_id="gcp-pair-apply", started_at=started, finished_at=finished, vm_a=vms[0], vm_b=vms[1], provider_metadata=StructuredEvidence(value={"template_bindings": {role: data["self_link_unique"] for role, data in templates.items()}, "readback": "instances.get", "no_source_template_response_claim": True}), documented_network_limit=StructuredEvidence(value=NETWORK_LIMIT), expiry=Evidence[ExpiryEvidence](value=ExpiryEvidence.model_validate(journal["expiry"])))
            self._write_private(attempt / "terraform-outputs.redacted.json", json.dumps(result.model_dump(mode="json"), sort_keys=True, indent=2).encode())
            journal["deployment_output"] = result.model_dump(mode="json")
            journal["observed_resources"] = {role: {"id": output[role].get("id"), "zone": output[role].get("zone"), "disk_sources": [disk.get("source") for disk in output[role].get("disks", [])], "private_ip": output[role].get("networkInterfaces", [{}])[0].get("networkIP")} for role in ("vm_a", "vm_b")}
            self._save(attempt, journal)
            return result
        except Exception as exc:
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
        attempt = self._attempt_dir(deployment)
        journal = self._load(attempt, deployment)
        if journal.get("cleanup_state") == "succeeded":
            return GcpCleanupResult(cleanup_state=CleanupState.SUCCEEDED, inventory=StructuredEvidence(value=journal["inventory"]), expiry=Evidence[ExpiryEvidence](value=ExpiryEvidence.model_validate(journal["expiry"])) if journal.get("expiry") else Evidence[ExpiryEvidence](unavailable_reason="no native expiry was registered"))
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
            inventory = self._inventory(deployment)
            if not inventory["pages_complete"] or inventory["resources"]:
                raise GcpProviderError("owned or unknown resources remain after cleanup", category="cleanup_incomplete")
            expiry = journal.get("expiry")
            if expiry and expiry.get("state") == "registered":
                expiry = {**expiry, "state": "cancelled", "provider_metadata": {"value": {**expiry.get("provider_metadata", {}).get("value", {}), "cancelled_after_full_deletion": True}}}
                journal["expiry"] = expiry
            result = GcpCleanupResult(cleanup_state=CleanupState.SUCCEEDED, inventory=StructuredEvidence(value=inventory), expiry=Evidence[ExpiryEvidence](value=ExpiryEvidence.model_validate(expiry)) if expiry else Evidence[ExpiryEvidence](unavailable_reason="no native expiry was registered"))
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
        if request.resource_namespace != deployment.run_id or request.target_selector.find(self.settings.project_id) < 0:
            raise GcpProviderError("expiry target must bind the selected project and attempt namespace", category="target_mismatch")
        if reservation.source_sha256 != self.settings.cost_input_sha256 or reservation.source_path != self.settings.cost_source_path:
            raise GcpProviderError("reservation and complete-workflow cost sources differ", category="cost_source_mismatch")
        if deployment.admission.cost_input_id != self.settings.cost_input_id or deployment.admission.cost_input_sha256.value != self.settings.cost_input_sha256:
            raise GcpProviderError("admission and reserved cost inputs must use the identified dated source", category="cost_source_mismatch")
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
        if deployment.scenario == "same_zone" and deployment.vm_a.zone != deployment.vm_b.zone:
            raise GcpProviderError("same_zone requires one common zone", category="scenario_unsupported")
        if deployment.scenario == "cross_zone" and (deployment.vm_a.region != deployment.vm_b.region or deployment.vm_a.zone == deployment.vm_b.zone):
            raise GcpProviderError("cross_zone requires distinct zones in one region", category="scenario_unsupported")
        if deployment.scenario == "placement_optimization" and (deployment.vm_a.zone != deployment.vm_b.zone or deployment.placement.kind != "compact_placement_policy"):
            raise GcpProviderError("S3 requires same-zone compact placement", category="scenario_unsupported")
        if deployment.scenario == "inter_region" and deployment.vm_b.region != "europe-west2":
            raise GcpProviderError("S4 receiver must be in London", category="scenario_unsupported")

    def _validate_review(self, deployment: ProviderDeploymentInput) -> None:
        evidence = self.settings.primary_review_decision
        value = evidence.value if evidence else None
        if not value or value.get("decision") != "approved" or value.get("scenario") != str(deployment.scenario):
            raise GcpProviderError("S2/S4 launch requires a sourced approved review for this scenario", category="review_required")
        if not value.get("source_path") or not re.fullmatch(r"[a-f0-9]{64}", str(value.get("source_sha256", ""))) or not value.get("reviewed_at"):
            raise GcpProviderError("review evidence requires a source path, SHA-256, and timestamp", category="review_required")

    def _verify_registered_templates(self, journal: dict[str, Any], trigger: datetime) -> None:
        templates = journal.get("templates", {})
        if set(templates) != {"vm_a", "vm_b"}:
            raise GcpProviderError("both registered template identities are required before pair creation", category="expiry_uncertain")
        expected_time = trigger.isoformat().replace("+00:00", "Z")
        for role, item in templates.items():
            described = self._gcloud_json(["compute", "instance-templates", "describe", item["name"], "--project", self.settings.project_id, "--format=json"], f"gcp-template-precreate-{role}")
            scheduling = described.get("properties", {}).get("scheduling", {})
            digest = hashlib.sha256(json.dumps(described, sort_keys=True).encode()).hexdigest()
            if str(described.get("id")) != str(item["id"]) or digest != item["sha256"] or scheduling.get("provisioningModel") != "STANDARD" or scheduling.get("instanceTerminationAction") != "STOP" or scheduling.get("terminationTime") != expected_time:
                raise GcpProviderError("registered template identity or absolute STOP policy changed before creation", category="expiry_mismatch")

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
            if value.get("deployment_id") == deployment.deployment_id: continue
            if value.get("launch_blocked") or value.get("cleanup_state") not in {None, "succeeded"} or value.get("registered"):
                raise GcpProviderError("another GCP attempt has open cleanup or native expiry", category="cleanup_open")

    @staticmethod
    def _fit(reservation: ReservationEvidence, trigger: datetime, seconds: float) -> None:
        now = datetime.now(timezone.utc)
        if reservation.cap_started_at.value > now or now + timedelta(seconds=seconds) > trigger:
            raise GcpProviderError("whole remaining workflow must fit before original native STOP initiation lead", category="cap_insufficient")

    def _attempt_dir(self, deployment: ProviderDeploymentInput) -> Path:
        root = self.settings.private_state_root.expanduser().absolute()
        if root.is_symlink():
            raise GcpProviderError("private state root cannot be a symlink", category="state_path_unsafe")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = root / hashlib.sha256(deployment.run_id.encode()).hexdigest()[:12] / deployment.run_id / deployment.deployment_id
        if any(part.is_symlink() for part in (path.parent.parent, path.parent, path)):
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
        return j

    def _save(self, attempt: Path, journal: dict[str, Any]) -> None:
        target = attempt / "attempt.json"
        if target.is_symlink(): raise GcpProviderError("journal cannot be a symlink", category="state_path_unsafe")
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
        limit = timeout or self.settings.command_timeout_seconds
        if self._deadline is not None:
            remaining = self._deadline - time.monotonic()
            if remaining <= 0: raise GcpProviderError("absolute GCP command deadline elapsed", category="command_timeout", action_id=action)
            limit = min(limit, max(1, int(remaining)))
        return self.runner.run(CommandRequest(action_id=action, action_type="gcp_provider_control", argv=argv, cwd=str(cwd), timeout_seconds=limit, environment_classification="contains_secret_references"))

    def _terraform(self, attempt: Path, root: str, action: str, deployment: ProviderDeploymentInput, extra: dict[str, Any]) -> dict[str, Any]:
        work = attempt / root
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
            template = {**common, "namespace": self._namespace(deployment), "scenario": deployment.scenario.value if hasattr(deployment.scenario, "value") else deployment.scenario, "expiry_time": "", "image_a": self.settings.images[deployment.vm_a.region], "image_b": self.settings.images[deployment.vm_b.region], "zone_a": deployment.vm_a.zone, "zone_b": deployment.vm_b.zone, "controller_cidr": self.settings.controller_cidr, "public_key": self.settings.public_key, "labels": {"cnb-run": hashlib.sha256(deployment.run_id.encode()).hexdigest()[:20], "cnb-deploy": hashlib.sha256(deployment.deployment_id.encode()).hexdigest()[:20]}}
        else:
            template = {**common, "name_a": self._namespace(deployment) + "-a", "name_b": self._namespace(deployment) + "-b", "zone_a": deployment.vm_a.zone, "zone_b": deployment.vm_b.zone}
        template.update(extra)
        self._write_private(vars_path, json.dumps(template, sort_keys=True).encode("utf-8"))
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
        return json.loads(result.stdout)

    @staticmethod
    def _outputs(value: dict[str, Any]) -> dict[str, Any]:
        return {key: item.get("value", item) for key, item in value.items()}

    @staticmethod
    def _namespace(deployment: ProviderDeploymentInput) -> str:
        stem = re.sub(r"[^a-z0-9-]", "-", deployment.run_id.lower()).strip("-")[:25]
        return f"{stem}-{hashlib.sha256(deployment.deployment_id.encode()).hexdigest()[:8]}"

    def _inventory(self, deployment: ProviderDeploymentInput) -> dict[str, Any]:
        zones = sorted({deployment.vm_a.zone, deployment.vm_b.zone})
        pages, instances, disks, addresses, support = [], [], [], [], []
        run_hash = hashlib.sha256(deployment.run_id.encode()).hexdigest()[:20]
        dep_hash = hashlib.sha256(deployment.deployment_id.encode()).hexdigest()[:20]
        namespace = self._namespace(deployment)
        for zone in zones:
            response = self._gcloud_json(["compute", "instances", "list", f"--zones={zone}", "--project", self.settings.project_id, "--filter", f"labels.cnb-run={run_hash}", "--format=json"], f"gcp-inventory-{zone}")
            if not isinstance(response, list): raise GcpProviderError("inventory pagination shape unknown", category="inventory_unknown")
            pages.append(zone); instances.extend(item for item in response if item.get("labels", {}).get("cnb-deploy") == dep_hash)
            disk_response = self._gcloud_json(["compute", "disks", "list", f"--zones={zone}", "--project", self.settings.project_id, "--filter", f"labels.cnb-run={run_hash}", "--format=json"], f"gcp-disk-inventory-{zone}")
            if not isinstance(disk_response, list): raise GcpProviderError("disk inventory pagination shape unknown", category="inventory_unknown")
            disks.extend(item for item in disk_response if item.get("labels", {}).get("cnb-deploy") == dep_hash)
        for region in sorted({deployment.vm_a.region, deployment.vm_b.region}):
            result = self._gcloud_json(["compute", "addresses", "list", f"--regions={region}", "--project", self.settings.project_id, "--format=json"], f"gcp-address-inventory-{region}")
            if not isinstance(result, list): raise GcpProviderError("regional address inventory pagination shape unknown", category="inventory_unknown")
            addresses.extend(item for item in result if str(item.get("name", "")).startswith(namespace))
            result = self._gcloud_json(["compute", "subnetworks", "list", f"--regions={region}", "--project", self.settings.project_id, "--format=json"], f"gcp-subnet-inventory-{region}")
            if not isinstance(result, list): raise GcpProviderError("subnetwork inventory pagination shape unknown", category="inventory_unknown")
            support.extend(item for item in result if str(item.get("name", "")).startswith(namespace))
        for collection, command in (("instance_templates", ["compute", "instance-templates", "list"]), ("networks", ["compute", "networks", "list"]), ("firewalls", ["compute", "firewall-rules", "list"])):
            result = self._gcloud_json([*command, "--project", self.settings.project_id, "--format=json"], f"gcp-{collection}-inventory")
            if not isinstance(result, list): raise GcpProviderError(f"{collection} inventory pagination shape unknown", category="inventory_unknown")
            support.extend(item for item in result if str(item.get("name", "")).startswith(namespace))
        for region in sorted({deployment.vm_a.region, deployment.vm_b.region}):
            result = self._gcloud_json(["compute", "resource-policies", "list", f"--regions={region}", "--project", self.settings.project_id, "--format=json"], f"gcp-policy-inventory-{region}")
            if not isinstance(result, list): raise GcpProviderError("resource policy inventory pagination shape unknown", category="inventory_unknown")
            support.extend(item for item in result if str(item.get("name", "")).startswith(namespace))
        global_addresses = self._gcloud_json(["compute", "addresses", "list", "--global", "--project", self.settings.project_id, "--format=json"], "gcp-global-address-inventory")
        if not isinstance(global_addresses, list): raise GcpProviderError("global address inventory pagination shape unknown", category="inventory_unknown")
        addresses.extend(item for item in global_addresses if str(item.get("name", "")).startswith(namespace))
        resources = [*instances, *disks, *addresses, *support]
        return {"pages_complete": True, "zones_scanned": pages, "regions_scanned": sorted({deployment.vm_a.region, deployment.vm_b.region}), "instances": instances, "disks": disks, "addresses": addresses, "support_resources": support, "resources": resources}

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
        return ProviderVmOutput(role=vm_role, resource_id=str(raw.get("id")), private_ipv4=internal, image_identity=self.settings.images[intent.region], actual_shape=raw.get("machineType", "").rsplit("/", 1)[-1], region=region, zone=zone, placement_metadata=StructuredEvidence(value={"resource_policies": raw.get("resourcePolicies", []), "physical_zone_id": "unavailable"}), connection=RemoteConnectionData(role=vm_role, host=internal, user="benchmark", authentication_reference=self.settings.authentication_reference, host_key_reference=str(known_hosts)), public_ipv4=Evidence[str](value=public) if public else Evidence[str](unavailable_reason="no public management IPv4 observed"), physical_zone_id=StringEvidence(unavailable_reason="GCP physical zone identifier unavailable"))

    def _validate_instance(self, instance: dict[str, Any], deployment: ProviderDeploymentInput, role: str) -> None:
        intent = deployment.vm_a if role == "vm_a" else deployment.vm_b
        interfaces = instance.get("networkInterfaces", [])
        if len(interfaces) != 1 or interfaces[0].get("nicType") != "GVNIC":
            raise GcpProviderError("observed VM must have exactly one gVNIC interface", category="instance_shape_mismatch")
        if instance.get("machineType", "").rsplit("/", 1)[-1] != "n2-standard-4":
            raise GcpProviderError("observed VM machine type differs from selected shape", category="instance_shape_mismatch")
        boot = next((disk for disk in instance.get("disks", []) if disk.get("boot")), None)
        if boot is None or not boot.get("source"):
            raise GcpProviderError("observed boot disk is missing", category="instance_shape_mismatch")
        disk = self._gcloud_json(["compute", "disks", "describe", boot["source"].rsplit("/", 1)[-1], "--zone", intent.zone, "--project", self.settings.project_id, "--format=json"], f"gcp-boot-disk-{role}")
        if int(disk.get("sizeGb", 0)) != 32 or disk.get("type", "").rsplit("/", 1)[-1] != "pd-balanced":
            raise GcpProviderError("observed boot disk differs from selected 32-GiB pd-balanced disk", category="instance_shape_mismatch")
        image = self._gcloud_json(["compute", "images", "describe", self.settings.images[intent.region].rsplit("/", 1)[-1], "--project", self.settings.images[intent.region].split("/")[1], "--format=json"], f"gcp-image-read-{role}")
        if image.get("architecture", "X86_64") != "X86_64":
            raise GcpProviderError("selected Ubuntu image is not amd64", category="image_mismatch")

    def _trusted_hostkey(self, name: str, zone: str, initial: dict[str, Any]) -> str:
        before = self._gcloud_json(["compute", "instances", "describe", name, "--zone", zone, "--project", self.settings.project_id, "--format=json"], "gcp-hostkey-instance-before")
        if str(before.get("id")) != str(initial.get("id")) or before.get("zone", "").endswith(zone) is False:
            raise GcpProviderError("instance identity changed before host-key read", category="hostkey_identity_mismatch")
        response = self._gcloud_json(["compute", "instances", "get-guest-attributes", name, "--zone", zone, "--project", self.settings.project_id, "--query-path=hostkeys/", "--format=json"], "gcp-guest-hostkeys")
        after = self._gcloud_json(["compute", "instances", "describe", name, "--zone", zone, "--project", self.settings.project_id, "--format=json"], "gcp-hostkey-instance-after")
        if str(after.get("id")) != str(initial.get("id")) or after.get("zone", "").endswith(zone) is False:
            raise GcpProviderError("instance identity changed after host-key read", category="hostkey_identity_mismatch")
        items = response.get("queryValue", {}).get("items", [])
        candidates = [item.get("value", "") for item in items if isinstance(item, dict) and item.get("namespace") == "hostkeys" and item.get("key")]
        key = next((line.strip() for value in candidates for line in value.splitlines() if re.match(r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp\d+) [A-Za-z0-9+/]+={0,2}(?:\s|$)", line.strip())), None)
        if key is None:
            raise GcpProviderError("guest attributes contain no valid OpenSSH host public key", category="hostkey_unavailable")
        hosts_file = self.settings.private_state_root / self._namespace_from_name(name) / "known_hosts"
        hosts_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(hosts_file.parent, 0o700)
        nic = after.get("networkInterfaces", [{}])[0]
        host = nic.get("networkIP")
        if not host:
            raise GcpProviderError("private VM address unavailable for host-key trust", category="hostkey_unavailable")
        existing = hosts_file.read_text(encoding="utf-8") if hosts_file.exists() and not hosts_file.is_symlink() else ""
        known = [parts[1] for line in existing.splitlines() if len(parts := line.split(maxsplit=1)) == 2 and parts[0] == host]
        if known and key not in known:
            raise GcpProviderError("host-key changed for an already trusted VM address", category="hostkey_mismatch")
        fd, tmp = tempfile.mkstemp(dir=hosts_file.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(existing + (f"{host} {key}\n" if not known else "")); stream.flush(); os.fsync(stream.fileno())
            os.chmod(tmp, 0o600); os.replace(tmp, hosts_file)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
        return key

    @staticmethod
    def _namespace_from_name(name: str) -> str:
        return name.rsplit("-", 1)[0]
