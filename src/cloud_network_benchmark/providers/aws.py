from __future__ import annotations

import hashlib
import ipaddress
import json
from decimal import Decimal, ROUND_CEILING
from math import isfinite
import os
import re
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import Field, field_validator, model_validator
import yaml

from ..contracts.admission import ExpiryEvidence, ExpiryRequest, ReservationEvidence
from ..contracts.common import (
    CleanupState,
    CommandOutcome,
    Evidence,
    FailureEvidence,
    Provider,
    Scenario,
    StrictModel,
    StructuredEvidence,
    VmRole,
)
from ..contracts.deployment import (
    IdentityContext,
    ProviderDeploymentInput,
    ProviderDeploymentOutput,
    ProviderVmOutput,
    RemoteConnectionData,
)
from ..contracts.execution import CommandRequest, CommandResult, CommandRunner


SAFETY_LEAD_SECONDS = 360
EXPIRY_SCRIPT_SECONDS = 300
EXPIRY_ACTION_TIMEOUT_SECONDS = 330
PLANNED_STOP_MARGIN_SECONDS = 60
EXPIRY_INPUT_ID = "aws-ssm-automation-public-2026-10-22"
EXPIRY_INPUT_PATH = "tests/fixtures/aws/cost-inputs/aws-ssm-automation-public-2026-10-22.json"
EXPIRY_INPUT_SHA256 = "315d3e49755396597f97cac3512b9843fcf1c95083fadc11fae667c770215a61"
EXPIRY_COST_PER_REGION = 0.0139
NETWORK_LIMIT = {
    "baseline_gbit_per_second": 1.562,
    "burst_gbit_per_second": 12.5,
    "burst_is_ceiling_not_observation": True,
    "source": "https://docs.aws.amazon.com/ec2/latest/instancetypes/gp.html",
    "checked_on": "2026-10-22",
    "caveat": "Instance family network documentation; credits and achieved throughput are not inferred.",
}
REGION_FOR_ZONE = {"eu-central-1a": "eu-central-1", "eu-central-1b": "eu-central-1", "eu-west-2a": "eu-west-2", "eu-north-1a": "eu-north-1"}
ROLE_PRIVATE_IPS = {
    "same_zone": ("10.60.1.10", "10.60.1.11"),
    "cross_zone": ("10.60.1.10", "10.60.2.10"),
    "placement_optimization": ("10.60.1.10", "10.60.1.11"),
    "inter_region": ("10.60.1.10", "10.61.1.10"),
}
ALLOWED_SCENARIO_LOCALITIES = {
    "same_zone": {
        (("eu-central-1", "eu-central-1"), ("eu-central-1a", "eu-central-1a")),
        (("eu-north-1", "eu-north-1"), ("eu-north-1a", "eu-north-1a")),
    },
    "cross_zone": {(("eu-central-1", "eu-central-1"), ("eu-central-1a", "eu-central-1b"))},
    "placement_optimization": {(("eu-central-1", "eu-central-1"), ("eu-central-1a", "eu-central-1a")), (("eu-north-1", "eu-north-1"), ("eu-north-1a", "eu-north-1a"))},
    "inter_region": {(("eu-central-1", "eu-west-2"), ("eu-central-1a", "eu-west-2a"))},
}
TERMINAL_AUTOMATION_STATES = {"Success", "Cancelled", "TimedOut", "Failed"}


def _pair_apply_timeout_seconds(provision_seconds: int, command_timeout_seconds: int,
        plan_review_seconds: int, trigger_remaining_seconds: float) -> int:
    """Bound saved-plan review and apply by the sourced provisioning phase."""
    if min(provision_seconds, command_timeout_seconds) <= 0 or plan_review_seconds < 0:
        raise ValueError("AWS provisioning phase timeout inputs are invalid")
    return max(1, min(provision_seconds,
        command_timeout_seconds + plan_review_seconds, int(trigger_remaining_seconds)))


class AwsRuntimeSettings(StrictModel):
    account_id: str = Field(pattern=r"^[0-9]{12}$")
    controller_cidr: str
    ami_ids: dict[str, str]
    public_key: str = Field(min_length=1)
    authentication_reference: str = Field(min_length=1)
    private_state_root: Path
    aws_cli: str = "aws"
    terraform: str = "terraform"
    cost_input_id: str = Field(min_length=1)
    cost_input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    cost_source_path: str = Field(min_length=1)
    complete_workflow_cost_handoff: StructuredEvidence
    complete_workflow_remaining_seconds: int = Field(gt=0)
    post_registration_workflow_seconds: int = Field(gt=0)
    post_provision_workflow_seconds: int = Field(gt=0)
    primary_review_decision: Optional[StructuredEvidence] = None
    plan_review_timeout_seconds: int = Field(default=0, ge=0, le=600)
    command_timeout_seconds: int = Field(default=120, gt=0, le=1800)
    registration_timeout_seconds: int = Field(default=90, gt=0, le=600)
    iam_settle_seconds: int = Field(default=0, ge=0, le=60)

    @field_validator("controller_cidr")
    @classmethod
    def controller_is_single_ipv4(cls, value: str) -> str:
        network = ipaddress.ip_network(value, strict=True)
        if network.version != 4 or network.prefixlen != 32:
            raise ValueError("AWS management access requires one validated controller IPv4 /32")
        return str(network)

    @field_validator("public_key")
    @classmethod
    def public_key_only(cls, value: str) -> str:
        if not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/]+={0,2}(?: [^\r\n]+)?", value.strip()):
            raise ValueError("public_key must be one OpenSSH ED25519 public key, never private key material")
        if "PRIVATE KEY" in value:
            raise ValueError("private key material is not accepted")
        return value.strip()

    @model_validator(mode="after")
    def source_paths(self) -> "AwsRuntimeSettings":
        if set(self.ami_ids) - {"eu-central-1", "eu-west-2", "eu-north-1"} or not self.ami_ids:
            raise ValueError("ami_ids may identify only the selected AWS regions")
        if any(not re.fullmatch(r"ami-[a-f0-9]{8,17}", image) for image in self.ami_ids.values()):
            raise ValueError("ami_ids must contain pinned AWS AMI IDs")
        if not self.authentication_reference.strip():
            raise ValueError("authentication_reference must be a non-secret runtime reference")
        return self


class AwsCleanupResult(StrictModel):
    cleanup_state: CleanupState
    cleanup_failure: Optional[FailureEvidence] = None
    inventory: StructuredEvidence
    expiry: Evidence[ExpiryEvidence]
    primary_failure: Optional[FailureEvidence] = None
    secondary_failures: list[FailureEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def truthful_failure_state(self) -> "AwsCleanupResult":
        if self.cleanup_state == CleanupState.FAILED and self.cleanup_failure is None:
            raise ValueError("failed AWS cleanup requires failure evidence")
        if self.cleanup_state == CleanupState.SUCCEEDED and (self.cleanup_failure is not None or self.inventory.value is None):
            raise ValueError("successful AWS cleanup requires observed inventory and no cleanup failure")
        if self.cleanup_state != CleanupState.FAILED and self.cleanup_failure is not None:
            raise ValueError("cleanup_failure is present only for failed AWS cleanup")
        return self


class AwsProviderError(RuntimeError):
    def __init__(self, message: str, *, category: str = "aws_provider", action_id: str | None = None):
        super().__init__(message)
        self.category = category
        self.action_id = action_id


class AwsCliServiceError(AwsProviderError):
    def __init__(self, code: str, region: str, resource_id: str, action_id: str):
        super().__init__(f"AWS API returned {code} for recorded resource {resource_id} in {region}", category="aws_api", action_id=action_id)
        self.code = code
        self.region = region
        self.resource_id = resource_id


class AwsProviderDriver:
    def __init__(
        self,
        runner: CommandRunner,
        settings: AwsRuntimeSettings,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.runner = runner
        self.settings = settings
        self.clock = clock
        self.sleeper = sleeper
        self._active_attempt: Path | None = None
        self._active_commands: list[dict[str, Any]] = []
        self._registration_deadline: float | None = None
        self._cleanup_deadline: float | None = None
        self._precreate_deadline: float | None = None
        self._active_primary_failure: FailureEvidence | None = None
        self._initialized_terraform: set[str] = set()

    def register_expiry(self, deployment: ProviderDeploymentInput, request: ExpiryRequest) -> ExpiryEvidence:
        """Create and read back protection in all regions before any pair command."""
        _validate_deployment(deployment)
        try:
            self._registration_deadline = time.monotonic() + self.settings.registration_timeout_seconds
            regions, reservation, trigger = self._validate_registration(deployment, request)
            actual_price = _expiry_price(self._root(), self.settings)
            attempt = self._attempt_dir(deployment)
            self._active_attempt = attempt
            self._active_commands = []
            journal = self._load_journal(attempt, deployment)
            scope = self._deployment_scope(deployment)
            if journal.get("deployment_scope") and journal["deployment_scope"] != scope:
                raise AwsProviderError("existing expiry journal has a different source-bound AWS scope", category="scope_mismatch")
            if journal.get("registered"):
                evidence = ExpiryEvidence.model_validate(journal["expiry_evidence"])
                if evidence.state == "registered" and evidence.expires_at.value == trigger:
                    return evidence
                raise AwsProviderError("existing expiry journal does not match this cap", category="expiry_recovery")

            # Reservation cap start is supplied by the caller and must already be active.
            journal.update({
                "deployment_scope": scope,
                "cap_started_at": reservation.cap_started_at.value.isoformat(),
                "cap_expires_at": reservation.expires_at.value.isoformat(),
                "reservation_id": reservation.reservation_id.value,
                "expiry_trigger": trigger.isoformat(),
                "regions": regions,
                "registered_regions": [],
                "tokens": {},
                "executions": {},
                "creation_may_have_started": False,
            })
            self._save_journal(attempt, journal)
            outputs: dict[str, dict[str, str]] = {}
            for region in regions:
                outputs[region] = self._apply_safety_root(deployment, region, attempt)
                # Save each role/document identity before external registration calls.
                journal.setdefault("safety", {})[region] = outputs[region]
                self._save_journal(attempt, journal)

            settle_seconds = self.settings.iam_settle_seconds
            if settle_seconds:
                remaining = (self._registration_deadline or time.monotonic()) - time.monotonic()
                if remaining < settle_seconds:
                    journal["iam_settle"] = {"seconds": settle_seconds, "status": "insufficient_registration_time"}
                    self._save_journal(attempt, journal)
                    raise AwsProviderError("AWS IAM settle delay does not fit the original registration deadline",
                        category="registration_deadline")
                journal["iam_settle"] = {"seconds": settle_seconds, "status": "started"}
                self._save_journal(attempt, journal)
                self.sleeper(settle_seconds)
                journal["iam_settle"]["status"] = "completed"
                self._save_journal(attempt, journal)

            for region in regions:
                token = journal["tokens"].setdefault(region, str(uuid.uuid5(uuid.NAMESPACE_URL, f"{deployment.deployment_id}:{region}:expiry")))
                self._save_journal(attempt, journal)
                expected = self._automation_parameters(deployment, region, trigger, outputs[region]["role_arn"])
                execution_id = journal["executions"].get(region)
                if execution_id is None:
                    if region not in journal.setdefault("registration_attempted_regions", []):
                        journal["registration_attempted_regions"].append(region)
                        self._save_journal(attempt, journal)
                    try:
                        execution_id = self._start_or_recover_automation(region, outputs[region], expected, token)
                    except AwsProviderError as exc:
                        rejection = getattr(exc, "rejection_evidence", None)
                        if exc.category == "expiry_start_rejected" and rejection:
                            journal.setdefault("registration_rejections", {})[region] = rejection
                            self._save_journal(attempt, journal)
                        raise
                    if not execution_id:
                        journal.setdefault("registration_uncertain_regions", []).append(region)
                        self._save_journal(attempt, journal)
                        raise AwsProviderError(
                            f"regional expiry start is uncertain in {region}; pair creation is blocked",
                            category="expiry_uncertain",
                            action_id=f"aws-expiry-start-{region}",
                        )
                    journal["executions"][region] = execution_id
                    self._save_journal(attempt, journal)
                execution = self._confirm_automation(region, execution_id, outputs[region], expected)
                journal.setdefault("regional_parameters", {})[region] = expected
                journal.setdefault("regional_expiry_outcomes", {})[region] = {
                    "execution_id": execution_id,
                    "state": execution.get("AutomationExecutionStatus"),
                    "current_action": execution.get("CurrentAction"),
                    "current_step": execution.get("CurrentStepName"),
                }
                if region not in journal["registered_regions"]:
                    journal["registered_regions"].append(region)
                self._save_journal(attempt, journal)

            if set(journal["registered_regions"]) != set(regions):
                raise AwsProviderError("not every regional expiry was confirmed", category="expiry_uncertain")
            registered_at = self.clock().astimezone(timezone.utc)
            evidence = ExpiryEvidence(
                schema_version=2,
                run_id=deployment.run_id,
                deployment_id=deployment.deployment_id,
                provider="aws",
                action="stop",
                state="registered",
                registration_id=Evidence[str](value=f"aws-ssm:{deployment.deployment_id}"),
                registered_at=Evidence[datetime](value=registered_at),
                expires_at=Evidence[datetime](value=trigger),
                target_selector=self._target_selector(deployment, regions),
                provider_metadata=StructuredEvidence(value={
                    "regional_execution_ids": dict(journal["executions"]),
                    "regional_document_names": {region: outputs[region]["document_name"] for region in regions},
                    "regional_document_versions": {region: outputs[region]["document_version"] for region in regions},
                    "trigger_utc": trigger.isoformat(),
                    "script_deadline_seconds": EXPIRY_SCRIPT_SECONDS,
                    "discovery_horizon_seconds": EXPIRY_SCRIPT_SECONDS,
                    "planned_action_seconds": EXPIRY_ACTION_TIMEOUT_SECONDS,
                    "planned_stop_margin_seconds": PLANNED_STOP_MARGIN_SECONDS,
                    "reserved_cap_end_utc": reservation.expires_at.value.isoformat(),
                    "cost_input_id": actual_price["id"],
                    "cost_input_sha256": actual_price["observed_sha256"],
                    "action_timeout_seconds": EXPIRY_ACTION_TIMEOUT_SECONDS,
                    "planned_expiry_cost_per_region_usd": actual_price["worst_planned_cost_per_region"],
                    "planned_expiry_cost_usd": actual_price["worst_planned_cost_per_region"] * len(regions),
                    "expiry_cost_handoff_source_path": self.settings.complete_workflow_cost_handoff.value["source_path"],
                    "expiry_cost_handoff_source_sha256": self.settings.complete_workflow_cost_handoff.value["source_sha256"],
                    "expiry_cost_included_usd": self.settings.complete_workflow_cost_handoff.value["expiry_cost_included_usd"],
                    "stop_is_not_deletion": True,
                }),
            )
            journal.update({"registered": True, "expiry_evidence": evidence.model_dump(mode="json")})
            journal["commands"] = list(self._active_commands)
            self._save_journal(attempt, journal)
            return evidence
        except Exception as exc:
            try:
                attempt = self._attempt_dir(deployment)
                journal = self._load_journal(attempt, deployment)
                journal["registration_failure"] = _failure(exc, self.clock()).model_dump(mode="json")
                journal["commands"] = list(self._active_commands)
                self._save_journal(attempt, journal)
                self._cleanup_unused_protection(deployment, attempt, journal)
            except Exception:
                pass
            return self._failed_expiry(deployment, request, exc)

    def _cleanup_unused_protection(self, deployment: ProviderDeploymentInput, attempt: Path, journal: dict[str, Any]) -> None:
        """Remove regional protection only after known executions are terminal.

        A lost or malformed start/readback is ambiguous. Preserve that region's
        support resources, while cleaning independently safe regions.
        """
        uncertain = set(journal.get("registration_uncertain_regions", [])) | (
            set(journal.get("registration_attempted_regions", [])) - set(journal.get("executions", {})))
        for region in journal.get("regions", []):
            if region in uncertain:
                continue
            execution_id = journal.get("executions", {}).get(region)
            state_path = attempt / f"safety-{region}.tfstate"
            vars_path = attempt / f"safety-{region}.tfvars.json"
            if not vars_path.exists():
                continue
            try:
                if execution_id:
                    self._cancel_or_confirm_terminal(region, execution_id, journal.get("safety", {}).get(region), journal.get("regional_parameters", {}).get(region))
                result = self._run(
                    f"aws-safety-rollback-{region}",
                    [self.settings.terraform, f"-chdir={self._root() / 'terraform/aws/safety'}", "destroy", "-input=false", "-auto-approve", f"-state={state_path}", f"-var-file={vars_path}"],
                    self.settings.command_timeout_seconds,
                    cwd=attempt,
                )
                if not _succeeded(result):
                    journal.setdefault("unused_protection_cleanup_errors", []).append({"region": region, "action_id": result.action_id})
            except Exception as cleanup_exc:
                journal.setdefault("unused_protection_cleanup_errors", []).append({"region": region, "message": str(cleanup_exc)})
        journal["commands"] = list(self._active_commands)
        self._save_journal(attempt, journal)

    def provision(self, deployment: ProviderDeploymentInput) -> ProviderDeploymentOutput:
        _validate_deployment(deployment)
        if deployment.expiry.value is None or deployment.expiry.value.state != "registered":
            raise AwsProviderError("all-region expiry evidence must be registered before pair apply", category="expiry_required")
        attempt = self._attempt_dir(deployment)
        self._active_attempt = attempt
        self._active_commands = []
        self._active_primary_failure = None
        journal = self._load_journal(attempt, deployment)
        self._block_on_residual_cleanup(deployment)
        if journal.get("deployment_scope") and journal["deployment_scope"] != self._deployment_scope(deployment):
            raise AwsProviderError("deployment differs from the registered AWS scope", category="scope_mismatch")
        if not journal.get("registered") or set(journal.get("registered_regions", [])) != set(self._regions(deployment)):
            raise AwsProviderError("private expiry journal does not confirm every involved region", category="expiry_required")
        registered = self._expiry_from_journal(deployment, journal)
        if registered is None or deployment.expiry.value is None or registered.model_dump(mode="json") != deployment.expiry.value.model_dump(mode="json"):
            raise AwsProviderError("supplied expiry evidence differs from the private registered evidence", category="expiry_identity_mismatch")
        reservation = deployment.reservation.value
        regions = self._regions(deployment)
        if reservation is None or journal.get("reservation_id") != reservation.reservation_id.value or journal.get("cap_started_at") != reservation.cap_started_at.value.isoformat() or journal.get("cap_expires_at") != reservation.expires_at.value.isoformat() or set(journal.get("executions", {})) != set(regions):
            raise AwsProviderError("reservation or exact regional execution identity changed after registration", category="expiry_identity_mismatch")
        metadata = registered.provider_metadata.value or {}
        if registered.registration_id.value != f"aws-ssm:{deployment.deployment_id}" or metadata.get("regional_execution_ids") != journal.get("executions"):
            raise AwsProviderError("registration ID or regional execution IDs differ from the protected journal", category="expiry_identity_mismatch")
        if registered.target_selector != self._target_selector(deployment, regions) or registered.expires_at.value is None or registered.expires_at.value.isoformat() != journal.get("expiry_trigger"):
            raise AwsProviderError("expiry selector or trigger differs from the registered journal", category="expiry_identity_mismatch")
        now = self.clock().astimezone(timezone.utc)
        trigger = deployment.expiry.value.expires_at.value
        if trigger is None or now >= trigger:
            raise AwsProviderError("AWS expiry trigger has passed", category="cap_expired")
        if self.settings.post_registration_workflow_seconds > self.settings.complete_workflow_remaining_seconds:
            raise AwsProviderError("post-registration workflow allowance exceeds the complete workflow allowance", category="allowance_invalid")
        if (self.settings.post_provision_workflow_seconds > self.settings.post_registration_workflow_seconds
                or self.settings.post_provision_workflow_seconds > self.settings.complete_workflow_remaining_seconds):
            raise AwsProviderError("post-provision workflow allowance exceeds an earlier workflow allowance", category="allowance_invalid")
        provision_phase_seconds = (self.settings.post_registration_workflow_seconds
            - self.settings.post_provision_workflow_seconds)
        if provision_phase_seconds <= 0:
            raise AwsProviderError("source-bound provision phase allowance is empty", category="allowance_invalid")
        if self.settings.post_registration_workflow_seconds > (trigger - now).total_seconds():
            raise AwsProviderError("complete remaining workflow does not fit before the earlier expiry trigger", category="cap_insufficient")
        slack = (trigger - now).total_seconds() - self.settings.post_registration_workflow_seconds
        if slack <= 0:
            raise AwsProviderError("no command budget remains before reserving the complete workflow", category="cap_insufficient")
        self._registration_deadline = time.monotonic() + self.settings.registration_timeout_seconds
        for region, execution_id in journal["executions"].items():
            self._confirm_automation(region, execution_id, journal["safety"][region], self._automation_parameters(deployment, region, trigger, journal["safety"][region]["role_arn"]))
        now = self.clock().astimezone(timezone.utc)
        if now >= trigger or self.settings.post_registration_workflow_seconds > (trigger - now).total_seconds():
            raise AwsProviderError("complete remaining workflow no longer fits after expiry readback", category="cap_insufficient")
        # Bound pair apply by reserving all source-declared work after provisioning.
        # This leaves the full provision allowance available to apply itself.
        self._precreate_deadline = time.monotonic() + max(0, (trigger - now).total_seconds() - self.settings.post_provision_workflow_seconds)

        variables = self._pair_variables(deployment)
        vars_path = attempt / "pair.tfvars.json"
        self._write_private(vars_path, json.dumps(variables, sort_keys=True, indent=2).encode())
        state_path = attempt / "pair.tfstate"
        journal["creation_may_have_started"] = True
        journal["pair_state_path"] = str(state_path)
        journal["apply_started_at"] = now.isoformat()
        self._save_journal(attempt, journal)
        apply_result: CommandResult | None = None
        try:
            apply_result = self._run(
                "aws-pair-apply",
                [self.settings.terraform, f"-chdir={self._root() / 'terraform/aws/pair'}", "apply", "-input=false", "-auto-approve", f"-state={state_path}", f"-var-file={vars_path}"],
                _pair_apply_timeout_seconds(provision_phase_seconds,
                    self.settings.command_timeout_seconds, self.settings.plan_review_timeout_seconds,
                    (trigger - self.clock().astimezone(timezone.utc)).total_seconds()),
                cwd=attempt,
            )
            _restrict_private_file(Path(state_path))
            if not _succeeded(apply_result):
                raise AwsProviderError("AWS pair Terraform apply failed", category="terraform_apply", action_id="aws-pair-apply")
            output_result = self._run(
                "aws-pair-output",
                [self.settings.terraform, f"-chdir={self._root() / 'terraform/aws/pair'}", "output", "-json", f"-state={state_path}"],
                self.settings.command_timeout_seconds,
                cwd=attempt,
            )
            if not _succeeded(output_result):
                raise AwsProviderError("AWS pair Terraform output failed", category="terraform_output", action_id="aws-pair-output")
            raw = json.loads(output_result.stdout)
            output = raw.get("deployment", {}).get("value")
            if not isinstance(output, dict):
                raise AwsProviderError("Terraform output is missing deployment.value", category="output_invalid")
            journal["known_resource_ids"] = _known_resource_ids(output)
            self._save_journal(attempt, journal)
            normalized = self._normalize_output(deployment, output, apply_result.started_at, apply_result.finished_at)
            self._write_private(attempt / "terraform-outputs.redacted.json", json.dumps(_redacted_output(output), indent=2, sort_keys=True).encode())
            journal["provisioned"] = True
            journal["deployment_output"] = normalized.model_dump(mode="json")
            journal["commands"] = list(self._active_commands)
            self._save_journal(attempt, journal)
            return normalized
        except Exception as exc:
            try:
                _restrict_private_file(Path(state_path))
            except (OSError, AwsProviderError):
                pass
            self._active_primary_failure = _failure(exc, self.clock())
            journal["primary_failure"] = self._active_primary_failure.model_dump(mode="json")
            try:
                self._save_journal(attempt, journal)
            except Exception as journal_exc:
                journal.setdefault("secondary_failures", []).append(_failure(journal_exc, self.clock()).model_dump(mode="json"))
                journal["cleanup_state"] = "failed"
                journal["launch_blocked"] = True
            # Once an apply could have started, every failure path invokes normal cleanup.
            try:
                self.cleanup(deployment)
            except Exception as cleanup_exc:
                try:
                    journal.setdefault("secondary_failures", []).append(_failure(cleanup_exc, self.clock()).model_dump(mode="json"))
                    journal["cleanup_state"] = "failed"
                    journal["launch_blocked"] = True
                    self._save_journal(attempt, journal)
                except Exception:
                    pass
            raise

    def cleanup(self, deployment: ProviderDeploymentInput) -> AwsCleanupResult:
        attempt = self._attempt_dir(deployment)
        self._active_attempt = attempt
        self._active_commands = []
        journal = self._load_journal(attempt, deployment)
        failures: list[FailureEvidence] = []
        cause = journal.get("primary_failure") or journal.get("registration_failure")
        primary = FailureEvidence.model_validate(cause) if cause else self._active_primary_failure
        inventory_by_region: dict[str, Any] = {}
        regions = journal.get("regions") or self._regions(deployment)
        scope = journal.get("deployment_scope")
        if scope and (scope.get("account_id") != self.settings.account_id or regions != list(dict.fromkeys(scope.get("regions", [])))):
            failure = _failure(AwsProviderError("cleanup account or regions differ from registered AWS scope", category="scope_mismatch"), self.clock())
            return self._failed_cleanup(inventory_by_region, [failure], primary, failure, deployment)
        self._registration_deadline = None
        self._cleanup_deadline = time.monotonic() + self.settings.registration_timeout_seconds * max(3, len(regions) * 3)

        try:
            identity = self._aws_json("sts", "get-caller-identity", "--output", "json")
            if identity.get("Account") != self.settings.account_id:
                raise AwsProviderError("cleanup caller account differs from the original scope", category="scope_mismatch")
        except Exception as exc:
            failure = _failure(exc,self.clock())
            return self._failed_cleanup(inventory_by_region,[failure],primary,failure,deployment)

        if journal.get("creation_may_have_started"):
            state_path = Path(journal.get("pair_state_path", attempt / "pair.tfstate"))
            vars_path = attempt / "pair.tfvars.json"
            if vars_path.exists():
                try:
                    result = self._run(
                        "aws-pair-destroy",
                        [self.settings.terraform, f"-chdir={self._root() / 'terraform/aws/pair'}", "destroy", "-input=false", "-auto-approve", f"-state={state_path}", f"-var-file={vars_path}"],
                        self.settings.command_timeout_seconds,
                        cwd=attempt,
                    )
                    if not _succeeded(result):
                        failures.append(_failure(AwsProviderError("AWS pair Terraform destroy failed", category="terraform_destroy", action_id="aws-pair-destroy"), self.clock()))
                except Exception as exc:
                    failures.append(_failure(exc, self.clock()))
        for region in regions:
            try:
                inventory_by_region[region] = self._inventory_region(deployment, region, failures)
            except Exception as exc:
                failures.append(_failure(exc, self.clock()))
                inventory_by_region[region] = {"complete": False, "remaining": {"inventory": ["inventory_unknown"]}, "resources": {}}
        if any(not item.get("complete") or item.get("remaining") for item in inventory_by_region.values()):
            failure = _failure(AwsProviderError("AWS resource inventory is unknown or contains leftovers", category="inventory_incomplete"), self.clock())
            return self._failed_cleanup(inventory_by_region, failures, primary, failure, deployment)

        uncertain = set(journal.get("registration_uncertain_regions", [])) | (
            set(journal.get("registration_attempted_regions", [])) - set(journal.get("executions", {})))
        for region in list(uncertain):
            rejection = journal.get("registration_rejections", {}).get(region, {})
            safety = journal.get("safety", {}).get(region)
            if not safety or not rejection:
                continue
            try:
                trigger = datetime.fromisoformat(journal["expiry_trigger"])
                params = self._automation_parameters(deployment, region, trigger, safety["role_arn"])
                digest = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
                if (rejection.get("lookup_complete") is not True
                        or rejection.get("matching_document_execution_count") != 0
                        or rejection.get("error_code") != "InvalidAutomationExecutionParametersException"
                        or rejection.get("parameters_sha256") != digest
                        or rejection.get("document_name") != safety["document_name"]
                        or rejection.get("document_version") != safety["document_version"]
                        or rejection.get("client_token") != journal.get("tokens", {}).get(region)):
                    continue
                lookup = self._lookup_automation(region, safety, params)
                if lookup is not None and lookup[1] == 0:
                    uncertain.discard(region)
            except (KeyError, ValueError, AwsProviderError):
                pass
        if uncertain:
            failure = _failure(AwsProviderError("expiry execution identity remains uncertain; preserve support resources", category="expiry_uncertain"),self.clock())
            return self._failed_cleanup(inventory_by_region,[*failures,failure],primary,failure,deployment)
        # Expiry remains until pair absence is confirmed in every involved region.
        for region in regions:
            execution_id = journal.get("executions", {}).get(region)
            if execution_id:
                try:
                    outcome = self._cancel_or_confirm_terminal(region, execution_id, journal.get("safety", {}).get(region), journal.get("regional_parameters", {}).get(region))
                    state = outcome["state"]
                    journal.setdefault("regional_expiry_outcomes", {})[region] = {"execution_id": execution_id, **outcome}
                    if state == "Cancelled":
                        journal["expiry_cancelled_regions"] = sorted(set(journal.get("expiry_cancelled_regions", [])) | {region})
                except Exception as exc:
                    failures.append(_failure(exc, self.clock()))
        journal["commands"] = list(self._active_commands)
        try:
            self._save_journal(attempt, journal)
        except Exception as exc:
            failures.append(_failure(exc, self.clock()))
        if failures:
            return self._failed_cleanup(inventory_by_region, failures, primary, failures[0], deployment)

        safety_absent: dict[str, bool] = {}
        safety_targets: dict[str, Any] = {}
        for region in regions:
            try:
                safety = journal.get("safety", {}).get(region)
                if safety is None:
                    safety = self._prospective_safety(deployment,region,attempt,journal)
                role_arn = safety["role_arn"]
                document_arn = f"arn:aws:ssm:{region}:{self.settings.account_id}:document/{safety['document_name']}"
                safety_targets[region] = {"role":{"arn":role_arn},"document":{"arn":document_arn}}
                # State and vars can exist even when apply/output failed before
                # the journal acquired a safety output. Always use original bytes.
                variables = attempt/f"safety-{region}.tfvars.json"
                terraform_destroyed = True
                if variables.exists():
                    if variables.is_symlink(): raise AwsProviderError("unsafe original safety inputs",category="state_path_unsafe")
                    result = self._run(f"aws-safety-destroy-{region}",
                        [self.settings.terraform,f"-chdir={self._root() / 'terraform/aws/safety'}","destroy","-input=false","-auto-approve",f"-state={attempt / f'safety-{region}.tfstate'}",f"-var-file={variables}"],
                        self.settings.command_timeout_seconds,cwd=attempt)
                    terraform_destroyed = _succeeded(result)
                safety_absent[region] = self._verify_safety_absent(region,safety)
                if not terraform_destroyed or not safety_absent[region]:
                    raise AwsProviderError(f"AWS safety cleanup or independent absence failed in {region}",category="safety_destroy")
            except Exception as exc:
                failures.append(_failure(exc,self.clock()))
                safety_absent[region] = False
        journal["cleanup_safety_targets"] = safety_targets
        journal["last_cleanup_inventory"] = inventory_by_region
        journal["commands"] = list(self._active_commands)
        journal["cleanup_attempted_at"] = self.clock().astimezone(timezone.utc).isoformat()
        try:
            self._save_journal(attempt, journal)
        except Exception as exc:
            failures.append(_failure(exc, self.clock()))
        if failures:
            return self._failed_cleanup(inventory_by_region, failures, primary, failures[0], deployment)

        expiry = self._expiry_from_journal(deployment, journal)
        if expiry and journal.get("regional_expiry_outcomes"):
            outcomes = journal["regional_expiry_outcomes"]
            states = {item.get("state") for item in outcomes.values()}
            failed_states = states & {"Failed", "TimedOut"}
            state = "failed" if failed_states else "triggered" if "Success" in states else "cancelled"
            failure = _failure(AwsProviderError("one or more regional expiry executions failed or timed out", category="expiry_execution_failed"), self.clock()) if state == "failed" else None
            expiry = expiry.model_copy(update={"state": state, "failure": failure, "provider_metadata": StructuredEvidence(value={**(expiry.provider_metadata.value or {}), "regional_outcomes": outcomes, "launch_authorization": "cleared_after_protection_removal"})})
            journal["expiry_evidence"] = expiry.model_dump(mode="json")
        result = AwsCleanupResult(
            cleanup_state=CleanupState.SUCCEEDED,
            inventory=StructuredEvidence(value={"regions": inventory_by_region, "safety_absent": safety_absent, "safety_targets": safety_targets, "complete": True}),
            expiry=Evidence[ExpiryEvidence](value=expiry) if expiry else Evidence[ExpiryEvidence](unavailable_reason="expiry not registered"),
            primary_failure=primary,
        )
        journal["cleanup_state"] = result.cleanup_state.value if hasattr(result.cleanup_state, "value") else result.cleanup_state
        journal["launch_blocked"] = False
        try:
            self._save_journal(attempt, journal)
        except Exception as exc:
            failure = _failure(exc, self.clock())
            return self._failed_cleanup(inventory_by_region, [*failures, failure], primary, failure, deployment)
        return result

    def _prospective_safety(self, deployment: ProviderDeploymentInput, region: str, attempt: Path, journal: dict) -> dict[str,str]:
        """Expected lookup targets, not evidence that resources were ever created."""
        namespace = attempt.parent.parent.name
        variables = attempt/f"safety-{region}.tfvars.json"
        if variables.exists():
            if variables.is_symlink(): raise AwsProviderError("unsafe original safety inputs",category="state_path_unsafe")
            original = json.loads(variables.read_text())
            expected = {"account_id":self.settings.account_id,"region":region,
                "run_id":deployment.run_id,"deployment_id":deployment.deployment_id,"resource_namespace":namespace}
            if original != expected: raise AwsProviderError("original safety inputs differ from owned recovery scope",category="scope_mismatch")
        elif journal.get("deployment_scope",{}).get("resource_namespace",namespace) != namespace:
            raise AwsProviderError("original safety namespace is missing or mismatched",category="scope_mismatch")
        digest = hashlib.sha256(f"{deployment.run_id}:{deployment.deployment_id}:{region}".encode()).hexdigest()[:24]
        name = "cnb-expiry-" + namespace.replace(".","-")[:24] + "-" + digest
        return {"role_arn":f"arn:aws:iam::{self.settings.account_id}:role/{name}","document_name":name}

    def _verify_safety_absent(self, region: str, safety: dict[str, str]) -> bool:
        role_name = safety["role_arn"].rsplit("/", 1)[-1]
        document = self._run(
            f"aws-expiry-document-absence-{region}",
            [self.settings.aws_cli, "ssm", "describe-document", "--name", safety["document_name"], "--region", region, "--output", "json"],
            self.settings.registration_timeout_seconds,
        )
        role = self._run(
            f"aws-expiry-role-absence-{region}",
            [self.settings.aws_cli, "iam", "get-role", "--role-name", role_name, "--output", "json"],
            self.settings.registration_timeout_seconds,
        )
        document_absent = not _succeeded(document) and _aws_cli_error_code(document.stderr) == "InvalidDocument"
        role_absent = not _succeeded(role) and _aws_cli_error_code(role.stderr) == "NoSuchEntity"
        return document_absent and role_absent

    def _validate_registration(self, deployment: ProviderDeploymentInput, request: ExpiryRequest) -> tuple[list[str], ReservationEvidence, datetime]:
        _validate_deployment(deployment)
        if (self.settings.post_registration_workflow_seconds > self.settings.complete_workflow_remaining_seconds
                or self.settings.post_provision_workflow_seconds > self.settings.post_registration_workflow_seconds
                or self.settings.post_registration_workflow_seconds <= self.settings.post_provision_workflow_seconds):
            raise AwsProviderError("AWS source-bound phase allowances are inconsistent", category="allowance_invalid")
        if request.provider != Provider.AWS or request.action != "stop":
            raise AwsProviderError("AWS expiry request must be stop", category="request_invalid")
        if (request.run_id, request.deployment_id) != (deployment.run_id, deployment.deployment_id):
            raise AwsProviderError("expiry request identity does not match deployment", category="identity_mismatch")
        reservation = deployment.reservation.value
        if reservation is None or reservation.state != "reserved":
            raise AwsProviderError("active complete reservation evidence is required", category="reservation_required")
        if reservation.campaign_id != deployment.campaign_id or reservation.expires_at.value is None or reservation.cap_started_at.value is None:
            raise AwsProviderError("reservation evidence is incomplete or mismatched", category="reservation_invalid")
        if reservation.cap_start_policy_id.value != "reservation-activation-before-provider-expiry":
            raise AwsProviderError("reservation cap-start policy must confirm activation before provider expiry registration", category="reservation_policy_mismatch")
        admission_hash = deployment.admission.cost_input_sha256.value
        if (
            deployment.admission.cost_input_id != self.settings.cost_input_id
            or admission_hash != self.settings.cost_input_sha256
            or reservation.source_path != self.settings.cost_source_path
            or reservation.source_sha256 != self.settings.cost_input_sha256
        ):
            raise AwsProviderError("dated complete-workflow cost source identity/hash does not match", category="cost_source_mismatch")
        _validate_expiry_price_fixture(self._root(), self.settings)
        regions = self._regions(deployment)
        self._validate_expiry_cost_coverage(reservation, regions)
        if request.reservation_id.value != reservation.reservation_id.value:
            raise AwsProviderError("expiry reservation identity does not match active reservation", category="reservation_mismatch")
        trigger = reservation.expires_at.value - timedelta(seconds=SAFETY_LEAD_SECONDS)
        selector = self._target_selector(deployment, regions)
        if request.target_selector != selector or request.resource_namespace != _namespace(deployment):
            raise AwsProviderError("expiry target selector or isolated namespace is not exact", category="selector_mismatch")
        if request.expires_at.value != trigger:
            raise AwsProviderError("expiry trigger must equal reserved cap end minus the 360-second safety lead", category="trigger_mismatch")
        now = self.clock().astimezone(timezone.utc)
        if reservation.cap_started_at.value > now:
            raise AwsProviderError("reservation cap start has not occurred", category="cap_not_started")
        if trigger <= now:
            raise AwsProviderError("expiry trigger has passed", category="cap_expired")
        if self.settings.complete_workflow_remaining_seconds > (trigger - now).total_seconds():
            raise AwsProviderError("complete workflow allowance does not fit before expiry trigger", category="cap_insufficient")
        if deployment.admission.requires_primary_review:
            if not _primary_review_valid(self.settings.primary_review_decision, str(deployment.scenario), now):
                raise AwsProviderError("scenario requires sourced primary-review admission evidence", category="review_required")
        self._block_on_residual_cleanup(deployment)
        return regions, reservation, trigger

    def _validate_expiry_cost_coverage(self, reservation: ReservationEvidence, regions: list[str]) -> None:
        price = _expiry_price(self._root(), self.settings)
        per_region = price["worst_planned_cost_per_region"]
        required = round(per_region * len(regions), 4)
        reserved = reservation.reserved_cost_usd.value
        if reserved is None or reserved + 1e-9 < required:
            raise AwsProviderError(f"reserved cost does not cover the AWS expiry allowance of {required:.4f} USD", category="expiry_cost_underreserved")
        handoff = self.settings.complete_workflow_cost_handoff.value
        if not isinstance(handoff, dict):
            raise AwsProviderError("complete-workflow cost handoff is missing expiry coverage evidence", category="expiry_cost_handoff_missing")
        included = handoff.get("expiry_cost_included_usd")
        if (
            handoff.get("source_path") != reservation.source_path
            or handoff.get("source_sha256") != reservation.source_sha256
            or handoff.get("expiry_input_id") != price["id"]
            or handoff.get("expiry_input_sha256") != price["observed_sha256"]
            or handoff.get("expiry_cost_allowance_per_region_usd") != per_region
            or handoff.get("expiry_cost_regions") != regions
            or isinstance(included, bool)
            or not isinstance(included, (int, float))
            or not isfinite(included)
            or included + 1e-9 < required
        ):
            raise AwsProviderError("complete-workflow cost handoff does not identify the matching sourced AWS expiry allowance", category="expiry_cost_handoff_mismatch")
        if reserved + 1e-9 < included:
            raise AwsProviderError("reserved cost is below the expiry amount claimed by the complete-workflow cost handoff", category="expiry_cost_underreserved")

    def _block_on_residual_cleanup(self, deployment: ProviderDeploymentInput) -> None:
        root = self.settings.private_state_root.expanduser().absolute()
        if root.is_symlink():
            raise AwsProviderError("private state root is a symlink; AWS launches remain blocked", category="residual_cleanup_unknown")
        if not root.exists():
            return
        prefix = "cnb-"
        for namespace in root.iterdir():
            if not namespace.name.startswith(prefix):
                continue
            if namespace.is_symlink():
                raise AwsProviderError("an earlier attempt namespace is a symlink; AWS launches remain blocked", category="residual_cleanup_unknown")
            if not namespace.is_dir():
                continue
            run_dir = namespace / deployment.run_id
            if run_dir.is_symlink():
                raise AwsProviderError("an earlier attempt run directory is a symlink; AWS launches remain blocked", category="residual_cleanup_unknown")
            if not run_dir.exists():
                continue
            for attempt_dir in run_dir.iterdir():
                if attempt_dir.is_symlink():
                    raise AwsProviderError("an earlier attempt directory is a symlink; AWS launches remain blocked", category="residual_cleanup_unknown")
                if not attempt_dir.is_dir():
                    continue
                journal_path = attempt_dir / "attempt.json"
                if not journal_path.exists():
                    continue
                if journal_path.is_symlink():
                    raise AwsProviderError("an attempt journal is a symlink; AWS launches remain blocked", category="residual_cleanup_unknown")
                try:
                    journal = json.loads(journal_path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise AwsProviderError("an earlier AWS attempt journal is unreadable; launches remain blocked", category="residual_cleanup_unknown") from exc
                if journal.get("run_id") != deployment.run_id:
                    continue
                if (journal.get("creation_may_have_started") or journal.get("registration_failure") or journal.get("registration_uncertain_regions") or journal.get("cleanup_state") == "failed") and journal.get("cleanup_state") != CleanupState.SUCCEEDED.value:
                    raise AwsProviderError("an earlier AWS attempt has unresolved cleanup; new launches are blocked", category="residual_cleanup_required")

    def _apply_safety_root(self, deployment: ProviderDeploymentInput, region: str, attempt: Path) -> dict[str, str]:
        vars_path = attempt / f"safety-{region}.tfvars.json"
        values = {
            "region": region,
            "account_id": self.settings.account_id,
            "resource_namespace": _namespace(deployment),
            "run_id": deployment.run_id,
            "deployment_id": deployment.deployment_id,
        }
        self._write_private(vars_path, json.dumps(values, sort_keys=True).encode())
        state_path = attempt / f"safety-{region}.tfstate"
        result = self._run(
            f"aws-safety-apply-{region}",
            [self.settings.terraform, f"-chdir={self._root() / 'terraform/aws/safety'}", "apply", "-input=false", "-auto-approve", f"-state={state_path}", f"-var-file={vars_path}"],
            self.settings.command_timeout_seconds,
            cwd=attempt,
        )
        _restrict_private_file(state_path)
        if not _succeeded(result):
            raise AwsProviderError(f"AWS safety Terraform failed in {region}", category="safety_apply", action_id=result.action_id)
        outputs = self._run(
            f"aws-safety-output-{region}",
            [self.settings.terraform, f"-chdir={self._root() / 'terraform/aws/safety'}", "output", "-json", f"-state={state_path}"],
            self.settings.command_timeout_seconds,
            cwd=attempt,
        )
        if not _succeeded(outputs):
            raise AwsProviderError(f"AWS safety Terraform outputs unavailable in {region}", category="safety_output", action_id=outputs.action_id)
        try:
            raw = json.loads(outputs.stdout)
            values = {name: raw[name]["value"] for name in ("document_name", "document_version", "document_hash", "role_arn")}
        except (ValueError, KeyError, TypeError) as exc:
            raise AwsProviderError(f"AWS safety Terraform outputs malformed in {region}", category="safety_output_invalid") from exc
        if not all(isinstance(value, str) and value for value in values.values()):
            raise AwsProviderError(f"AWS safety output identity missing in {region}", category="safety_output_invalid")
        return values

    def _automation_parameters(self, deployment: ProviderDeploymentInput, region: str, trigger: datetime, role_arn: str) -> dict[str, list[str]]:
        return {
            "AutomationAssumeRole": [role_arn],
            "ExpiryTriggerUtc": [_timestamp(trigger)],
            "RunId": [deployment.run_id],
            "DeploymentId": [deployment.deployment_id],
            "Region": [region],
        }

    def _start_or_recover_automation(self, region: str, safety: dict[str, str], params: dict[str, list[str]], token: str) -> Optional[str]:
        parameter_json = json.dumps(params, separators=(",", ":"))
        start = self._run(
            f"aws-expiry-start-{region}",
            [self.settings.aws_cli, "ssm", "start-automation-execution", "--document-name", safety["document_name"], "--document-version", safety["document_version"], "--parameters", parameter_json, "--client-token", token, "--region", region, "--output", "json"],
            self.settings.registration_timeout_seconds,
        )
        if _succeeded(start):
            try:
                execution_id = json.loads(start.stdout)["AutomationExecutionId"]
            except (ValueError, KeyError, TypeError):
                execution_id = None
            if isinstance(execution_id, str) and execution_id.strip():
                return execution_id
        # One start only. A lost response is uncertain even when a lookup is
        # empty. A typed server rejection plus complete exact-document absence
        # can establish a rejected pre-VM attempt, never successful protection.
        lookup = self._lookup_automation(region, safety, params)
        if lookup is not None and len(lookup[0]) == 1:
            return lookup[0][0]
        rejected = (start.outcome == "failed" and start.exit_code not in (None, 0) and not start.stdout.strip()
            and re.match(r"\s*(?:aws: \[ERROR\]:\s*)?An error occurred \(InvalidAutomationExecutionParametersException\)\s+when calling the StartAutomationExecution operation:", start.stderr))
        if rejected and lookup is not None and lookup[1] == 0:
            exc = AwsProviderError("AWS rejected expiry start; complete lookup found no exact-document execution",
                category="expiry_start_rejected", action_id=start.action_id)
            exc.rejection_evidence = {"error_code": "InvalidAutomationExecutionParametersException",
                "document_name": safety["document_name"], "document_version": safety["document_version"],
                "parameters_sha256": hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest(),
                "client_token": token, "lookup_complete": True, "matching_document_execution_count": 0}
            raise exc
        return None

    def _lookup_automation(self, region: str, safety: dict[str, str], params: dict[str, list[str]]) -> tuple[list[str], int] | None:
        describe = self._run(f"aws-expiry-recover-{region}",
            [self.settings.aws_cli, "ssm", "describe-automation-executions", "--filters",
             f"Key=DocumentNamePrefix,Values={safety['document_name']}", "--region", region, "--output", "json"],
            self.settings.registration_timeout_seconds)
        if not _succeeded(describe) or describe.stderr.strip():
            return None
        try:
            payload = json.loads(describe.stdout)
            executions = payload["AutomationExecutionMetadataList"]
            if not isinstance(executions, list) or payload.get("NextToken"):
                return None
        except (ValueError, KeyError, TypeError):
            return None
        matches, exact_ids = [], set()
        for item in executions:
            if (not isinstance(item, dict) or not isinstance(item.get("DocumentName"), str)
                    or not item["DocumentName"].startswith(safety["document_name"])):
                return None
            # AWS supports a prefix filter only; a neighboring document is not
            # evidence about this attempt and must never be read/cancelled.
            if item["DocumentName"] != safety["document_name"]:
                continue
            execution_id = item.get("AutomationExecutionId")
            if not isinstance(execution_id, str) or not execution_id or execution_id in exact_ids:
                return None
            exact_ids.add(execution_id)
            result = self._run(f"aws-expiry-recover-read-{region}",
                [self.settings.aws_cli, "ssm", "get-automation-execution", "--automation-execution-id", execution_id,
                 "--region", region, "--output", "json"], self.settings.registration_timeout_seconds)
            if not _succeeded(result) or result.stderr.strip():
                return None
            try:
                execution = _json_execution(result.stdout)
                if (execution.get("AutomationExecutionId") != execution_id
                        or execution.get("DocumentName") != safety["document_name"]
                        or not isinstance(execution.get("Parameters"), dict)):
                    return None
                if (execution.get("DocumentVersion") == safety["document_version"]
                        and execution["Parameters"] == params):
                    matches.append(execution_id)
            except (ValueError, AwsProviderError):
                return None
        return matches, len(exact_ids)

    def _confirm_automation(self, region: str, execution_id: str, safety: dict[str, str], params: dict[str, list[str]]) -> dict[str, Any]:
        for _ in range(6):
            result = self._run(
            f"aws-expiry-confirm-{region}",
            [self.settings.aws_cli, "ssm", "get-automation-execution", "--automation-execution-id", execution_id, "--region", region, "--output", "json"],
            self.settings.registration_timeout_seconds,
        )
            if not _succeeded(result):
                raise AwsProviderError(f"AWS expiry readback failed in {region}", category="expiry_readback", action_id=result.action_id)
            execution = _json_execution(result.stdout)
            if execution.get("AutomationExecutionId") != execution_id:
                raise AwsProviderError(f"AWS expiry readback execution ID differs in {region}", category="expiry_readback_mismatch")
            if _automation_matches(execution, safety, params, execution_id):
                return execution
            if execution.get("AutomationExecutionStatus") != "Pending" or self._registration_deadline is not None and time.monotonic() >= self._registration_deadline:
                raise AwsProviderError(f"AWS expiry readback is not confirmed running the expected sleep in {region}", category="expiry_readback_mismatch")
            time.sleep(min(1, max(0, (self._registration_deadline or time.monotonic() + 1) - time.monotonic())))
        raise AwsProviderError(f"AWS expiry remained Pending in {region}", category="expiry_readback_timeout")

    def _pair_variables(self, deployment: ProviderDeploymentInput) -> dict[str, Any]:
        scenario = str(deployment.scenario)
        regions = self._regions(deployment)
        if any(region not in self.settings.ami_ids for region in set(regions)):
            raise AwsProviderError("a regional Canonical AMI ID is missing", category="ami_missing")
        if deployment.provider_options.get("instance_market_type") != "on_demand":
            raise AwsProviderError("AWS spot instances are not permitted", category="shape_mismatch")
        if scenario == "inter_region" and not _primary_review_valid(self.settings.primary_review_decision, scenario, self.clock().astimezone(timezone.utc)):
            raise AwsProviderError("S4 requires sourced priority review and admission", category="review_required")
        if scenario == "cross_zone" and deployment.vm_a.zone == deployment.vm_b.zone:
            raise AwsProviderError("cross_zone requires distinct selected zones", category="scenario_mismatch")
        if scenario in {"same_zone", "placement_optimization"} and deployment.vm_a.zone != deployment.vm_b.zone:
            raise AwsProviderError("selected AWS scenario requires a shared zone", category="scenario_mismatch")
        expected_placement = "cluster_placement_group" if scenario == "placement_optimization" else "none"
        if deployment.placement.kind != expected_placement:
            raise AwsProviderError("AWS placement kind does not match selected scenario", category="scenario_mismatch")
        # The Terraform roots own the fixed network/topology values; caller CIDR values cannot override them.
        return {
            "account_id": self.settings.account_id,
            "resource_namespace": _namespace(deployment),
            "run_id": deployment.run_id,
            "deployment_id": deployment.deployment_id,
            "scenario": scenario,
            "controller_cidr": self.settings.controller_cidr,
            "primary_region": deployment.vm_a.region,
            "primary_zone": deployment.vm_a.zone,
            "image_id_primary": self.settings.ami_ids[deployment.vm_a.region],
            "image_id_london": self.settings.ami_ids.get("eu-west-2", "ami-00000000000000000"),
            "host_key_bootstrap": _host_key_cloud_init(self.settings.public_key),
            "placement_group_name": deployment.placement.name or f"{_namespace(deployment)}-cluster",
        }

    def _normalize_output(self, deployment: ProviderDeploymentInput, raw: dict[str, Any], started: datetime, finished: datetime) -> ProviderDeploymentOutput:
        vm_values = raw.get("vms")
        if not isinstance(vm_values, dict) or set(vm_values) != {"vm_a", "vm_b"}:
            raise AwsProviderError("Terraform output must contain exactly vm_a and vm_b", category="output_invalid")
        outputs: dict[str, ProviderVmOutput] = {}
        for role in ("vm_a", "vm_b"):
            details = vm_values[role]
            required = ("instance_id", "private_ipv4", "public_ipv4", "region", "zone", "zone_id", "image_id", "image_owner_id", "architecture", "shape", "boot_volume_id")
            missing = [name for name in required if not details.get(name)]
            if missing:
                raise AwsProviderError(f"Terraform output {role} missing {', '.join(missing)}", category="output_incomplete")
            expected_region = deployment.vm_a.region if role == "vm_a" else deployment.vm_b.region
            expected_zone = deployment.vm_a.zone if role == "vm_a" else deployment.vm_b.zone
            if details["region"] != expected_region or details["zone"] != expected_zone:
                raise AwsProviderError(f"observed {role} region/zone does not match the selected scenario", category="locality_mismatch")
            if details["image_id"] != self.settings.ami_ids.get(expected_region):
                raise AwsProviderError(f"observed {role} AMI differs from the pinned regional image", category="image_shape_mismatch")
            if details["shape"] != "m7i.xlarge" or details["architecture"] != "x86_64" or details["image_owner_id"] != "099720109477":
                raise AwsProviderError(f"observed {role} shape/image identity does not match Ubuntu 24.04 amd64 requirements", category="image_shape_mismatch")
            identity = f"{details['image_id']}:{details['image_owner_id']}:ubuntu-noble-24.04-amd64-hvm"
            outputs[role] = ProviderVmOutput(
                role=role,
                resource_id=details["instance_id"],
                private_ipv4=details["private_ipv4"],
                public_ipv4=Evidence[str](value=details["public_ipv4"]),
                physical_zone_id=Evidence[str](value=details["zone_id"]),
                image_identity=identity,
                actual_shape=details["shape"],
                region=details["region"],
                zone=details["zone"],
                placement_metadata=StructuredEvidence(value={"placement_group": details.get("placement_group"), "placement_group_strategy": details.get("placement_group_strategy")}),
                connection=None,
                unavailable=StructuredEvidence(value={"connection": "authenticated host trust not yet established"}),
            )

        # Authenticate provider observation and install strict known_hosts only after keyscan match.
        updated: dict[str, ProviderVmOutput] = {}
        for role, item in outputs.items():
            detail = vm_values[role]
            detail.update(self._verify_observed_instance(detail, deployment.scenario, role, deployment.run_id, deployment.deployment_id))
            evidence = self._verify_host_key(detail)
            host_key_path = evidence["known_hosts_path"]
            connection = RemoteConnectionData(
                role=role,
                host=item.public_ipv4.value,
                user="ubuntu",
                authentication_reference=self.settings.authentication_reference,
                host_key_reference=host_key_path,
            )
            updated[role] = item.model_copy(update={"connection": connection, "unavailable": None})

        provider_metadata = {
            "account_id": self.settings.account_id,
            "scenario": str(deployment.scenario),
            "selected_regions": list(self._regions(deployment)),
            "security": {"ssh_source": self.settings.controller_cidr, "benchmark_listener_source": "vm_a private IPv4 only", "nat": False, "bastion": False},
            "network_preparation_intent": {"mtu": 1460, "tcp_congestion_control": "cubic", "dscp": 0, "ecn": False, "default_offloads": True},
            "expiry_registration_id": deployment.expiry.value.registration_id.value,
        }
        return ProviderDeploymentOutput(
            schema_version=2,
            identity=IdentityContext(
                experiment_id=deployment.experiment_id,
                config_id=deployment.config_id,
                campaign_id=deployment.campaign_id,
                observation_id=deployment.observation_id,
                run_id=deployment.run_id,
                deployment_id=deployment.deployment_id,
                provider="aws",
                replaces_run_id=deployment.replaces_run_id,
            ),
            run_id=deployment.run_id,
            deployment_id=deployment.deployment_id,
            mode=deployment.mode,
            provider="aws",
            scenario=deployment.scenario,
            apply_action_id="aws-pair-apply",
            started_at=started,
            finished_at=finished,
            vm_a=updated["vm_a"],
            vm_b=updated["vm_b"],
            provider_metadata=StructuredEvidence(value=provider_metadata),
            documented_network_limit=StructuredEvidence(value=NETWORK_LIMIT),
            expiry=Evidence[ExpiryEvidence](value=deployment.expiry.value),
        )

    def _verify_host_key(self, detail: dict[str, Any]) -> dict[str, str]:
        region, instance_id = detail["region"], detail["instance_id"]
        response = self._aws_json("ec2", "get-console-output", "--instance-id", instance_id, "--latest", "--region", region, "--output", "json")
        if response.get("InstanceId") != instance_id:
            raise AwsProviderError("console response is not bound to the expected instance", category="host_identity_mismatch")
        console = response.get("Output")
        if not isinstance(console, str):
            raise AwsProviderError("console output is malformed", category="host_identity_unavailable")
        boots = re.findall(r"CNB_HOSTKEY_BOOT_ID=([0-9a-f-]{36})", console)
        fingerprints = re.findall(r"CNB_ED25519_HOST_FINGERPRINT=(SHA256:[A-Za-z0-9+/]+)", console)
        timestamp = response.get("Timestamp")
        launch_time = detail.get("launch_time")
        try:
            timestamp_value = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            launch_value = launch_time if isinstance(launch_time, datetime) else datetime.fromisoformat(str(launch_time).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            timestamp_value = launch_value = None
        if timestamp_value is None or launch_value is None or timestamp_value < launch_value or len(boots) != 1 or len(fingerprints) != 1:
            raise AwsProviderError("authenticated console lacks one current boot and ED25519 host fingerprint", category="host_identity_unavailable")
        keyscan = self._run(
            f"aws-host-keyscan-{instance_id}",
            ["ssh-keyscan", "-T", "10", "-t", "ed25519", detail["public_ipv4"]],
            15,
        )
        if not _succeeded(keyscan):
            raise AwsProviderError("host keyscan failed", category="host_keyscan", action_id=keyscan.action_id)
        parsed = self._run(
            f"aws-host-key-fingerprint-{instance_id}",
            ["ssh-keygen", "-E", "sha256", "-lf", "-"],
            10,
            stdin=keyscan.stdout,
        )
        if not _succeeded(parsed):
            raise AwsProviderError("candidate host key fingerprint could not be parsed", category="host_keyscan", action_id=parsed.action_id)
        candidates = re.findall(r"SHA256:[A-Za-z0-9+/]+", parsed.stdout)
        if len(candidates) != 1 or candidates[0] != fingerprints[0]:
            raise AwsProviderError("candidate host key does not match authenticated EC2 console fingerprint", category="host_key_mismatch")
        private_dir = self._attempt_dir_for_details(detail)
        known_hosts = private_dir / f"{instance_id}.known_hosts"
        host_lines = [line.strip() for line in keyscan.stdout.splitlines() if line.strip() and not line.startswith("#")]
        if not host_lines:
            raise AwsProviderError("keyscan produced no host key", category="host_keyscan")
        self._write_private(known_hosts, ("\n".join(host_lines) + "\n").encode())
        trust = {"known_hosts_path": str(known_hosts), "boot_id": boots[0], "fingerprint": fingerprints[0], "account_id": self.settings.account_id, "region": region, "instance_id": instance_id, "console_timestamp": str(timestamp)}
        self._write_private(private_dir / f"{instance_id}.host-trust.json", json.dumps(trust, sort_keys=True).encode())
        return trust

    def _verify_observed_instance(self, expected: dict[str, Any], scenario: Any, role: str, run_id: str, deployment_id: str) -> dict[str, Any]:
        region = expected["region"]
        caller = self._aws_json("sts", "get-caller-identity", "--region", region, "--output", "json")
        if caller.get("Account") != self.settings.account_id:
            raise AwsProviderError("authenticated AWS account differs from the admitted account", category="account_mismatch")
        response = self._aws_json("ec2", "describe-instances", "--instance-ids", expected["instance_id"], "--region", region, "--output", "json")
        instances = [instance for reservation in response.get("Reservations", []) for instance in reservation.get("Instances", [])]
        if len(instances) != 1:
            raise AwsProviderError("authenticated DescribeInstances must return exactly one instance", category="instance_identity_unavailable")
        instance = instances[0]
        placement = instance.get("Placement") or {}
        networks = instance.get("NetworkInterfaces") or []
        mappings = instance.get("BlockDeviceMappings") or []
        tags = {item.get("Key"): item.get("Value") for item in instance.get("Tags", []) if isinstance(item, dict)}
        if instance.get("InstanceId") != expected["instance_id"] or instance.get("State", {}).get("Name") != "running":
            raise AwsProviderError("observed AWS instance identity or running state differs", category="instance_identity_mismatch")
        if tags.get("cnb_run_id") != run_id or tags.get("cnb_deployment_id") != deployment_id or tags.get("cnb_role") != role:
            raise AwsProviderError("observed AWS instance ownership tags differ from the active attempt", category="instance_identity_mismatch")
        if instance.get("InstanceType") != "m7i.xlarge" or instance.get("ImageId") != expected["image_id"]:
            raise AwsProviderError("observed AWS image or shape differs from the selected pair", category="image_shape_mismatch")
        if instance.get("PrivateIpAddress") != expected["private_ipv4"] or instance.get("PublicIpAddress") != expected["public_ipv4"]:
            raise AwsProviderError("observed AWS address differs from Terraform output", category="address_mismatch")
        if placement.get("AvailabilityZone") != expected["zone"] or len(networks) != 1:
            raise AwsProviderError("observed AWS zone or one-NIC shape differs", category="topology_mismatch")
        expected_group = expected.get("placement_group")
        if placement.get("GroupName") != expected_group:
            raise AwsProviderError("observed placement group differs from the selected scenario", category="placement_mismatch")
        if any(network.get("InterfaceType") == "efa" for network in networks):
            raise AwsProviderError("EFA network interfaces are not part of the selected protocol", category="acceleration_mismatch")
        zone = self._aws_json("ec2", "describe-instance-topology", "--instance-ids", expected["instance_id"], "--region", region, "--output", "json")
        topology = zone.get("Instances") or []
        if len(topology) != 1 or topology[0].get("ZoneId") != expected["zone_id"]:
            raise AwsProviderError("physical AWS zone ID differs from observed instance topology", category="zone_identity_mismatch")

        images = self._aws_json("ec2", "describe-images", "--image-ids", instance["ImageId"], "--region", region, "--output", "json").get("Images", [])
        if len(images) != 1:
            raise AwsProviderError("authenticated DescribeImages did not return exactly one selected image", category="image_identity_unavailable")
        image = images[0]
        if (
            image.get("ImageId") != instance["ImageId"]
            or image.get("OwnerId") != "099720109477"
            or image.get("Architecture") != "x86_64"
            or image.get("VirtualizationType") != "hvm"
            or image.get("RootDeviceType") != "ebs"
            or image.get("State") != "available"
            or not re.search(r"ubuntu-noble-24\.04-amd64-server-", str(image.get("Name", "")))
        ):
            raise AwsProviderError("AMI is not an available Canonical Ubuntu 24.04 amd64 HVM image", category="image_identity_mismatch")
        if image.get("EnaSupport") is not True:
            raise AwsProviderError("selected Ubuntu AMI does not report ENA support", category="acceleration_mismatch")
        if any((network.get("EnaSrdSpecification") or {}).get("EnaSrdEnabled") is True for network in networks):
            raise AwsProviderError("ENA Express is outside the selected measurement configuration", category="acceleration_mismatch")

        if len(mappings) != 1 or not mappings[0].get("Ebs", {}).get("VolumeId") or mappings[0].get("Ebs", {}).get("DeleteOnTermination") is not True:
            raise AwsProviderError("instance must have one EBS root volume with delete-on-termination", category="disk_mismatch")
        volume_id = mappings[0]["Ebs"]["VolumeId"]
        if volume_id != expected["boot_volume_id"]:
            raise AwsProviderError("observed root volume differs from Terraform output", category="disk_mismatch")
        volumes = self._aws_json("ec2", "describe-volumes", "--volume-ids", volume_id, "--region", region, "--output", "json").get("Volumes", [])
        if len(volumes) != 1 or volumes[0].get("Size") != 32 or volumes[0].get("VolumeType") != "gp3":
            raise AwsProviderError("root disk must be one 32 GiB gp3 volume", category="disk_mismatch")

        types = self._aws_json("ec2", "describe-instance-types", "--instance-types", "m7i.xlarge", "--region", region, "--output", "json").get("InstanceTypes", [])
        if len(types) != 1:
            raise AwsProviderError("DescribeInstanceTypes did not return m7i.xlarge", category="shape_metadata_unavailable")
        type_data = types[0]
        if type_data.get("InstanceType") != "m7i.xlarge" or type_data.get("ProcessorInfo", {}).get("SupportedArchitectures") is not None and "x86_64" not in type_data.get("ProcessorInfo", {}).get("SupportedArchitectures", []):
            raise AwsProviderError("authenticated instance-type metadata does not match amd64 m7i.xlarge", category="shape_metadata_mismatch")
        if type_data.get("VCpuInfo", {}).get("DefaultVCpus") != 4 or type_data.get("MemoryInfo", {}).get("SizeInMiB") != 16384:
            raise AwsProviderError("authenticated instance-type vCPU/memory metadata differs from selected shape", category="shape_metadata_mismatch")

        actual_zone_id = topology[0]["ZoneId"]
        return {
            "instance_id": instance["InstanceId"],
            "private_ipv4": instance["PrivateIpAddress"],
            "public_ipv4": instance["PublicIpAddress"],
            "region": region,
            "zone": placement["AvailabilityZone"],
            "zone_id": actual_zone_id,
            "image_id": image["ImageId"],
            "image_owner_id": image["OwnerId"],
            "architecture": image["Architecture"],
            "shape": type_data["InstanceType"],
            "launch_time": instance.get("LaunchTime"),
            "network_interface_count": len(networks),
            "root_volume_ids": [mapping.get("Ebs", {}).get("VolumeId") for mapping in mappings if mapping.get("Ebs", {}).get("VolumeId")],
            "ena_support": image.get("EnaSupport"),
            "efa_attached": any(network.get("InterfaceType") == "efa" for network in networks),
            "ena_express_enabled": any((network.get("EnaSrdSpecification") or {}).get("EnaSrdEnabled") is True for network in networks),
            "boot_volume_size_gib": volumes[0]["Size"],
            "boot_volume_type": volumes[0]["VolumeType"],
            "instance_tags": tags,
            "placement_group": placement.get("GroupName"),
            "scenario": str(scenario),
        }

    def _attempt_dir_for_details(self, _details: dict[str, Any]) -> Path:
        # _normalize_output has already created the deployment attempt directory. Resolve the
        # currently active attempt from the driver's single-call context set by provision().
        path = getattr(self, "_active_attempt", None)
        if path is None:
            raise AwsProviderError("host trust has no active attempt context", category="host_identity_unavailable")
        return path

    def _inventory_region(self, deployment: ProviderDeploymentInput, region: str, failures: list[FailureEvidence]) -> dict[str, Any]:
        filters = ["--filters", f"Name=tag:cnb_run_id,Values={deployment.run_id}", f"Name=tag:cnb_deployment_id,Values={deployment.deployment_id}", "--region", region, "--output", "json"]
        queries = {
            "instances": ["ec2", "describe-instances", *filters],
            "volumes": ["ec2", "describe-volumes", *filters],
            "network_interfaces": ["ec2", "describe-network-interfaces", *filters],
            "security_groups": ["ec2", "describe-security-groups", *filters],
            "subnets": ["ec2", "describe-subnets", *filters],
            "route_tables": ["ec2", "describe-route-tables", *filters],
            "internet_gateways": ["ec2", "describe-internet-gateways", *filters],
            "vpcs": ["ec2", "describe-vpcs", *filters],
            "peerings": ["ec2", "describe-vpc-peering-connections", *filters],
            "placement_groups": ["ec2", "describe-placement-groups", *filters],
            "key_pairs": ["ec2", "describe-key-pairs", *filters],
            "addresses": ["ec2", "describe-addresses", *filters],
        }
        response_fields = {
            "instances": "Reservations", "volumes": "Volumes", "network_interfaces": "NetworkInterfaces",
            "security_groups": "SecurityGroups", "subnets": "Subnets", "route_tables": "RouteTables",
            "internet_gateways": "InternetGateways", "vpcs": "Vpcs", "peerings": "VpcPeeringConnections",
            "placement_groups": "PlacementGroups", "key_pairs": "KeyPairs",
            "addresses": "Addresses",
        }
        entries: dict[str, Any] = {}
        complete = True
        known = self._load_journal(self._attempt_dir(deployment), deployment).get("known_resource_ids", {})
        for name, argv in queries.items():
            try:
                data = self._aws_json(*argv)
                if not isinstance(data, dict) or response_fields[name] not in data or not isinstance(data[response_fields[name]], list):
                    raise AwsProviderError(f"{name} inventory response omitted a valid collection", category="inventory_incomplete")
                values = data[response_fields[name]]
                if data.get("NextToken") or data.get("nextToken"):
                    complete = False
                    failures.append(_failure(AwsProviderError(f"{name} inventory response is truncated", category="inventory_truncated"), self.clock()))
                # EC2 instance response is one extra nesting level.
                if name == "instances":
                    if any(not isinstance(reservation, dict) or not isinstance(reservation.get("Instances"), list) for reservation in values):
                        raise AwsProviderError("instances inventory has malformed reservation collection", category="inventory_incomplete")
                    values = [instance for reservation in values for instance in reservation["Instances"]]
                if any(not _has_exact_ownership_tags(item, deployment.run_id, deployment.deployment_id) for item in values):
                    complete = False
                    failures.append(_failure(AwsProviderError(f"{name} inventory returned a resource without the exact run/deployment tags", category="inventory_identity_mismatch"), self.clock()))
                entries[name] = values
            except Exception as exc:
                complete = False
                failures.append(_failure(exc, self.clock()))
                entries[name] = None
        known_remaining = self._verify_known_resource_ids(known, region, failures)
        if known_remaining:
            complete = False
            entries["known_ids"] = known_remaining
        return {"complete": complete, "remaining": _remaining_inventory(entries), "resources": entries}

    def _verify_known_resource_ids(self, known: dict[str, Any], region: str, failures: list[FailureEvidence]) -> dict[str, Any]:
        remaining: dict[str, Any] = {}
        for resource_type, ids in known.items():
            regional_ids = ids.get(region, []) if isinstance(ids, dict) else []
            for resource_id in regional_ids:
                operation = {"instance": "describe-instances", "volume": "describe-volumes", "network_interface": "describe-network-interfaces"}.get(resource_type)
                if operation is None:
                    continue
                try:
                    response = self._aws_json("ec2", operation, "--instance-ids" if resource_type == "instance" else "--volume-ids" if resource_type == "volume" else "--network-interface-ids", str(resource_id), "--region", region, "--output", "json")
                    field = {"instance": "Reservations", "volume": "Volumes", "network_interface": "NetworkInterfaces"}[resource_type]
                    if not isinstance(response, dict) or not isinstance(response.get(field), list):
                        raise AwsProviderError("known-ID inventory omitted its response collection", category="inventory_incomplete")
                    found = response[field]
                    if resource_type == "instance":
                        if any(not isinstance(res, dict) or not isinstance(res.get("Instances"), list) for res in found):
                            raise AwsProviderError("known instance-ID inventory is malformed", category="inventory_incomplete")
                        found = [inst for res in found for inst in res["Instances"] if inst.get("State", {}).get("Name") != "terminated"]
                    if found:
                        remaining.setdefault(resource_type, []).extend(found)
                except AwsCliServiceError as exc:
                    expected_codes = {
                        "instance": {"InvalidInstanceID.NotFound"},
                        "volume": {"InvalidVolume.NotFound"},
                        "network_interface": {"InvalidNetworkInterfaceID.NotFound"},
                    }.get(resource_type, set())
                    if exc.code in expected_codes and exc.region == region and exc.resource_id == str(resource_id):
                        identity = self._aws_json("sts", "get-caller-identity", "--output", "json")
                        if isinstance(identity, dict) and identity.get("Account") == self.settings.account_id:
                            continue
                        failures.append(_failure(AwsProviderError("authenticated AWS account does not match recorded resource scope", category="inventory_account_mismatch"), self.clock()))
                        remaining.setdefault(resource_type, []).append("inventory_unknown")
                        continue
                    failures.append(_failure(exc, self.clock()))
                    remaining.setdefault(resource_type, []).append("inventory_unknown")
                except Exception as exc:
                    failures.append(_failure(exc, self.clock()))
                    remaining.setdefault(resource_type, []).append("inventory_unknown")
        return remaining

    def _cancel_or_confirm_terminal(self, region: str, execution_id: str, safety: dict[str, str] | None = None, parameters: dict[str, list[str]] | None = None) -> dict[str, Any]:
        current = self._run(
            f"aws-expiry-read-terminal-{region}",
            [self.settings.aws_cli, "ssm", "get-automation-execution", "--automation-execution-id", execution_id, "--region", region, "--output", "json"],
            self.settings.registration_timeout_seconds,
        )
        if not _succeeded(current):
            raise AwsProviderError(f"cannot read expiry state in {region}", category="expiry_cancel_unverified", action_id=current.action_id)
        execution = _json_execution(current.stdout)
        _validate_terminal_execution(execution, execution_id, region, safety, parameters)
        state = execution.get("AutomationExecutionStatus")
        if state not in TERMINAL_AUTOMATION_STATES:
            stop = self._run(
                f"aws-expiry-cancel-{region}",
                [self.settings.aws_cli, "ssm", "stop-automation-execution", "--automation-execution-id", execution_id, "--type", "Cancel", "--region", region, "--output", "json"],
                self.settings.registration_timeout_seconds,
            )
            if not _succeeded(stop):
                raise AwsProviderError(f"expiry cancellation request failed in {region}", category="expiry_cancel_failed", action_id=stop.action_id)
            current = self._run(
                f"aws-expiry-confirm-cancel-{region}",
                [self.settings.aws_cli, "ssm", "get-automation-execution", "--automation-execution-id", execution_id, "--region", region, "--output", "json"],
                self.settings.registration_timeout_seconds,
            )
            if not _succeeded(current):
                raise AwsProviderError(f"expiry cancellation readback failed in {region}", category="expiry_cancel_unverified", action_id=current.action_id)
            execution = _json_execution(current.stdout)
            _validate_terminal_execution(execution, execution_id, region, safety, parameters)
            state = execution.get("AutomationExecutionStatus")
        if state not in TERMINAL_AUTOMATION_STATES:
            raise AwsProviderError(f"expiry execution remains nonterminal in {region}: {state}", category="expiry_cancel_unverified")
        stop_step = next((step for step in execution["StepExecutions"] if step.get("StepName") == "discoverAndStopOwnedInstances"), None)
        stop_payload = _expiry_stop_payload(stop_step, parameters) if stop_step and stop_step.get("StepStatus") == "Success" else None
        return {"state": str(state), "steps": execution.get("StepExecutions"), "step_executions_truncated": execution.get("StepExecutionsTruncated"), "outputs": execution.get("Outputs"), "stop_payload": stop_payload, "failure_message": execution.get("FailureMessage")}

    def _expiry_from_journal(self, deployment: ProviderDeploymentInput, journal: dict[str, Any]) -> ExpiryEvidence | None:
        value = journal.get("expiry_evidence")
        if value:
            return ExpiryEvidence.model_validate(value)
        return deployment.expiry.value

    def _failed_expiry(self, deployment: ProviderDeploymentInput, request: ExpiryRequest, exc: Exception) -> ExpiryEvidence:
        failure = _failure(exc, self.clock())
        return ExpiryEvidence(
            schema_version=2,
            run_id=deployment.run_id,
            deployment_id=deployment.deployment_id,
            provider="aws",
            action="stop",
            state="failed",
            registration_id=Evidence[str](unavailable_reason="all-region expiry registration was not confirmed"),
            registered_at=Evidence[datetime](unavailable_reason="registration did not complete"),
            expires_at=request.expires_at,
            target_selector=request.target_selector,
            provider_metadata=StructuredEvidence(value={"failure_category": failure.category, "failure_action_id": failure.action_id or "unknown", "partial_registration": True}),
            failure=failure,
        )

    def _failed_cleanup(self, inventory: dict[str, Any], secondary: list[FailureEvidence], primary: FailureEvidence | None, failure: FailureEvidence, deployment: ProviderDeploymentInput) -> AwsCleanupResult:
        journal = self._load_journal(self._attempt_dir(deployment), deployment)
        expiry = self._expiry_from_journal(deployment, journal)
        journal["cleanup_state"] = "failed"
        journal["last_cleanup_inventory"] = inventory
        journal["cleanup_attempted_at"] = self.clock().astimezone(timezone.utc).isoformat()
        journal["launch_blocked"] = True
        journal["cleanup_failure"] = failure.model_dump(mode="json")
        journal["secondary_failures"] = [item.model_dump(mode="json") for item in secondary]
        try:
            self._save_journal(self._attempt_dir(deployment), journal)
        except Exception as journal_exc:
            secondary = [*secondary, _failure(journal_exc, self.clock())]
        return AwsCleanupResult(
            cleanup_state=CleanupState.FAILED,
            cleanup_failure=failure,
            inventory=StructuredEvidence(value={"regions": inventory, "complete": False}),
            expiry=Evidence[ExpiryEvidence](value=expiry) if expiry else Evidence[ExpiryEvidence](unavailable_reason="expiry registration not available"),
            primary_failure=primary,
            secondary_failures=secondary,
        )

    def _regions(self, deployment: ProviderDeploymentInput) -> list[str]:
        scenario = str(deployment.scenario)
        actual = ((deployment.vm_a.region, deployment.vm_b.region), (deployment.vm_a.zone, deployment.vm_b.zone))
        if actual not in ALLOWED_SCENARIO_LOCALITIES.get(scenario, set()) or any(REGION_FOR_ZONE.get(zone) != region for region, zone in zip(*actual)):
            raise AwsProviderError(f"AWS scenario region/zone combination is unsupported: {actual}", category="scenario_mismatch")
        if "eu-north-1" in actual[0]:
            research = (deployment.mode in {"short_study", "calibration", "diagnostic_day"}
                        and deployment.context == "standard"
                        and ((scenario == "same_zone" and deployment.experiment_id == "EXP-001")
                             or (scenario == "placement_optimization" and deployment.experiment_id == "EXP-003")))
            acceptance = (deployment.mode == "acceptance"
                          and ((scenario == "same_zone" and deployment.experiment_id == "EXP-900" and deployment.context == "failure_check")
                               or (scenario == "same_zone" and deployment.experiment_id == "EXP-903" and deployment.context == "standard")
                               or (scenario == "placement_optimization" and deployment.experiment_id == "EXP-003" and deployment.context == "standard")))
            if not (research or acceptance):
                raise AwsProviderError("Stockholm locality is outside the registered S1/S3 research or acceptance scope", category="scenario_mismatch")
        return list(dict.fromkeys(actual[0]))

    def _deployment_scope(self, deployment: ProviderDeploymentInput) -> dict[str, Any]:
        regions = self._regions(deployment)
        if any(region not in self.settings.ami_ids for region in regions):
            raise AwsProviderError("a regional Canonical AMI ID is missing", category="ami_missing")
        return {
            "account_id": self.settings.account_id,
            "scenario": str(deployment.scenario),
            "regions": [deployment.vm_a.region, deployment.vm_b.region],
            "zones": [deployment.vm_a.zone, deployment.vm_b.zone],
            "ami_ids": {region: self.settings.ami_ids[region] for region in regions},
            "config_sha256": deployment.config_sha256,
        }

    def _target_selector(self, deployment: ProviderDeploymentInput, regions: list[str]) -> str:
        return f"account={self.settings.account_id};regions={','.join(regions)};tags=cnb_run_id:{deployment.run_id}&cnb_deployment_id:{deployment.deployment_id}"

    def _attempt_dir(self, deployment: ProviderDeploymentInput) -> Path:
        root = self.settings.private_state_root.expanduser().absolute()
        if root.is_symlink():
            raise AwsProviderError("private state root must not be a symlink", category="state_path_unsafe")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(root, 0o700)
        # Existing long namespaces are recovery identities, never migration inputs.
        owned = []
        for namespace in dict.fromkeys((_namespace(deployment), _legacy_namespace(deployment))):
            if len(namespace.encode()) > 255:  # Such a legacy component could never exist on these hosts.
                continue
            candidate = root / namespace / deployment.run_id / deployment.deployment_id
            if any(part.is_symlink() for part in (root/namespace, candidate.parent, candidate, candidate/"attempt.json")):
                raise AwsProviderError("attempt state path must not contain symlinks", category="state_path_unsafe")
            if (candidate/"attempt.json").is_file():
                self._load_journal(candidate,deployment)
                owned.append(candidate)
        if len(owned) > 1:
            raise AwsProviderError("multiple namespace journals claim this attempt; recovery is ambiguous", category="state_identity_mismatch")
        if owned:
            return owned[0]
        path = root
        for component in (_namespace(deployment), deployment.run_id, deployment.deployment_id):
            path = path / component
            if path.is_symlink():
                raise AwsProviderError("attempt state path must not contain symlink components", category="state_path_unsafe")
            path.mkdir(exist_ok=True, mode=0o700)
            os.chmod(path, 0o700)
        if root.resolve() not in path.resolve().parents:
            raise AwsProviderError("attempt state path escapes private state root", category="state_path_unsafe")
        return path

    def _load_journal(self, attempt: Path, deployment: ProviderDeploymentInput) -> dict[str, Any]:
        path = attempt / "attempt.json"
        if not path.exists():
            return {"schema_version": 1, "run_id": deployment.run_id, "deployment_id": deployment.deployment_id, "commands": []}
        if path.is_symlink():
            raise AwsProviderError("attempt journal must not be a symlink", category="state_path_unsafe")
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("run_id") != deployment.run_id or value.get("deployment_id") != deployment.deployment_id:
            raise AwsProviderError("attempt journal belongs to another deployment", category="state_identity_mismatch")
        return value

    def _save_journal(self, attempt: Path, payload: dict[str, Any]) -> None:
        self._write_private(attempt / "attempt.json", (json.dumps(payload, sort_keys=True, indent=2, default=str) + "\n").encode())

    def _write_private(self, path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path.parent, 0o700)
        if path.is_symlink():
            raise AwsProviderError(f"refusing to replace symlink at {path}", category="state_path_unsafe")
        temp = None
        try:
            with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
                temp = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp, 0o600)
            os.replace(temp, path)
        finally:
            if temp is not None and temp.exists():
                temp.unlink()

    def _run(self, action_id: str, argv: list[str], timeout: int, *, cwd: Path | None = None, stdin: str | None = None) -> CommandResult:
        registration_action = action_id.startswith(("aws-expiry-start-", "aws-expiry-recover-", "aws-expiry-confirm-")) and "-cancel-" not in action_id
        if self._registration_deadline is not None and (registration_action or action_id.startswith("aws-safety-apply-")):
            remaining = int(self._registration_deadline - time.monotonic())
            if remaining <= 0:
                raise AwsProviderError("AWS expiry registration exceeded its absolute deadline", category="expiry_registration_timeout", action_id=action_id)
            timeout = min(timeout, remaining)
        if self._cleanup_deadline is not None and action_id.startswith("aws-expiry-"):
            remaining = int(self._cleanup_deadline - time.monotonic())
            if remaining <= 0:
                raise AwsProviderError("AWS expiry cleanup exceeded its bounded deadline", category="expiry_cleanup_timeout", action_id=action_id)
            timeout = min(timeout, remaining)
        if self._precreate_deadline is not None and action_id == "aws-pair-apply":
            remaining = int(self._precreate_deadline - time.monotonic())
            if remaining <= 0:
                raise AwsProviderError("pair apply admission budget expired immediately before creation", category="cap_insufficient", action_id=action_id)
            timeout = min(timeout, remaining)
        from ..terraform_work import private_terraform_environment, initialize_terraform
        command_deadline = time.monotonic() + timeout
        request = CommandRequest(
            action_id=action_id,
            action_type="aws_provider_control",
            argv=[str(value) for value in argv],
            cwd=str(cwd or self._root()),
            environment=private_terraform_environment(argv, cwd or self._root()),
            timeout_seconds=timeout,
            stdin=stdin,
            environment_classification="contains_secret_references" if self.settings.authentication_reference else "none",
        )
        initialize_terraform(self.runner, request, self._initialized_terraform)
        remaining_after_init = command_deadline - time.monotonic()
        if remaining_after_init <= 0:
            raise AwsProviderError("original command deadline elapsed during private Terraform initialization", category="cap_insufficient", action_id=action_id)
        request = request.model_copy(update={"timeout_seconds": min(request.timeout_seconds, remaining_after_init)})
        def execute(request):
            result = self.runner.run(request)
            # Persist only redacted command identity/outcome/timing, never provider stdout.
            record = {
                "action_id": result.action_id,
                "argv": request.durable_view()["argv"],
                "outcome": str(result.outcome),
                "started_at": result.started_at.isoformat(),
                "finished_at": result.finished_at.isoformat(),
                "duration_seconds": result.duration_seconds,
                "duration_basis": result.duration_basis,
                "exit_code": result.exit_code,
                "redactions_applied": bool(result.redactions),
            }
            if action_id.startswith(("aws-expiry-start-", "aws-expiry-recover-", "aws-expiry-confirm-")):
                diagnostic = {}
                for field in ("stdout", "stderr"):
                    raw = getattr(result, field)
                    redacted = result.model_copy(update={"redactions": [*result.redactions,
                        getattr(self.settings, "authentication_reference", ""), getattr(self.settings, "public_key", "")]}).durable_view()[field]
                    redacted = re.sub(r"(?:AKIA|ASIA)[A-Z0-9]{16}", "[REDACTED_ACCESS_KEY_ID]", redacted)
                    redacted = re.sub(r"(?i)((?:aws_secret_access_key|aws_session_token|authorization)\s*[=:]\s*)[^\s,;]+", r"\1[REDACTED]", redacted)
                    redacted = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", "[REDACTED_PRIVATE_KEY]", redacted, flags=re.S)
                    diagnostic[field] = redacted[:8192]
                    diagnostic[field+"_sha256"] = hashlib.sha256(raw.encode()).hexdigest()
                    diagnostic[field+"_truncated"] = len(redacted) > 8192
                record["diagnostic"] = diagnostic
            self._active_commands.append(record)
            if self._active_attempt is not None:
                command_log = self._active_attempt / "commands.jsonl"
                with command_log.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, sort_keys=True) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(command_log, 0o600)
            return result
        if "apply" in request.argv and any(a.startswith("-chdir=") for a in request.argv):
            from ..terraform_plan import reviewed_apply
            return reviewed_apply(request, execute, review_seconds=self.settings.plan_review_timeout_seconds)
        return execute(request)

    def _aws_json(self, *argv: str) -> dict[str, Any]:
        action_id = "aws-" + "-".join(argv[1:3]).replace("_", "-")
        result = self._run(action_id, [self.settings.aws_cli, *argv], self.settings.command_timeout_seconds)
        if not _succeeded(result):
            error_match = re.search(r"\(?(Invalid(?:InstanceID|Volume|NetworkInterfaceID)\.NotFound)\)?", result.stderr)
            region_match = re.search(r"--region\s+([a-z0-9-]+)", " ".join(argv))
            resource_id = argv[argv.index("--instance-ids") + 1] if "--instance-ids" in argv and argv.index("--instance-ids") + 1 < len(argv) else argv[argv.index("--volume-ids") + 1] if "--volume-ids" in argv and argv.index("--volume-ids") + 1 < len(argv) else argv[argv.index("--network-interface-ids") + 1] if "--network-interface-ids" in argv and argv.index("--network-interface-ids") + 1 < len(argv) else ""
            if error_match and region_match and resource_id:
                raise AwsCliServiceError(error_match.group(1), region_match.group(1), resource_id, action_id)
            raise AwsProviderError(f"AWS read-only API command failed: {action_id}", category="aws_api", action_id=action_id)
        try:
            value = json.loads(result.stdout)
        except (ValueError, TypeError) as exc:
            raise AwsProviderError(f"AWS API response is malformed: {action_id}", category="aws_api_response", action_id=action_id) from exc
        if not isinstance(value, dict):
            raise AwsProviderError(f"AWS API response has unexpected shape: {action_id}", category="aws_api_response", action_id=action_id)
        return value

    def _root(self) -> Path:
        return Path(__file__).resolve().parents[3]


def _validate_deployment(deployment: ProviderDeploymentInput) -> None:
    if deployment.provider != Provider.AWS:
        raise AwsProviderError("AWS driver accepts only AWS deployments", category="provider_mismatch")
    if deployment.run_id != f"{deployment.campaign_id}-aws" or deployment.deployment_id != f"{deployment.run_id}-deployment":
        raise AwsProviderError("AWS run/deployment identity mismatch", category="identity_mismatch")
    if deployment.vm_a.role != VmRole.VM_A.value or deployment.vm_b.role != VmRole.VM_B.value:
        raise AwsProviderError("AWS attempt requires exactly vm_a and vm_b roles", category="role_mismatch")
    if deployment.provider_options != {"instance_market_type": "on_demand"}:
        raise AwsProviderError("AWS attempt must use the frozen On-Demand provider option", category="shape_mismatch")


def _validate_expiry_price_fixture(root: Path, settings: AwsRuntimeSettings) -> None:
    _expiry_price(root, settings)


def _expiry_price(root: Path, settings: AwsRuntimeSettings) -> dict[str, Any]:
    handoff = settings.complete_workflow_cost_handoff.value or {}
    path = Path(handoff.get("expiry_input_path", root / EXPIRY_INPUT_PATH))
    if not path.is_file() or path.is_symlink():
        raise AwsProviderError("dated AWS expiry cost input fixture is unavailable", category="cost_fixture_missing")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != handoff.get("expiry_input_sha256", EXPIRY_INPUT_SHA256):
        # The reservation's complete-workflow source is independently validated against its admission hash.
        raise AwsProviderError("dated AWS expiry price fixture ID/hash differs", category="cost_fixture_mismatch")
    payload = json.loads(raw)
    steps = payload.get("planned_steps_per_region")
    action_seconds = payload.get("planned_action_seconds_per_region")
    rates = (payload.get("automation_step_price"), payload.get("script_second_price"))
    if any(isinstance(rate, bool) or not isinstance(rate, (int, float)) or not isfinite(rate) or rate < 0 for rate in rates):
        raise AwsProviderError("expiry price rates must be finite nonnegative USD", category="cost_fixture_invalid")
    per_region = float((Decimal(str(rates[0])) * 2 + Decimal(str(rates[1])) * 330).quantize(Decimal("0.0001"), rounding=ROUND_CEILING))
    if (payload.get("free_tier_assumed") is not False or payload.get("discovery_horizon_seconds_per_region") != EXPIRY_SCRIPT_SECONDS
        or steps != 2 or action_seconds != EXPIRY_ACTION_TIMEOUT_SECONDS
        or payload.get("currency") != "USD"
        or payload.get("worst_planned_cost_per_region") != round(per_region, 4)
        or payload.get("worst_planned_cost_for_two_regions") != round(per_region * 2, 4)
        or payload.get("id") != handoff.get("expiry_input_id", EXPIRY_INPUT_ID)):
        raise AwsProviderError("expiry price fixture omits the documented two-step/330-second action allowance", category="cost_fixture_invalid")
    return {**payload, "observed_sha256": digest}


def _legacy_namespace(deployment: ProviderDeploymentInput) -> str:
    return f"cnb-{deployment.run_id}-{deployment.deployment_id}"


def _namespace(deployment: ProviderDeploymentInput) -> str:
    original = _legacy_namespace(deployment)
    if len(original) <= 80:
        return original
    return original[:55].rstrip("-._") + "-" + hashlib.sha256(original.encode()).hexdigest()[:24]


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _succeeded(result: CommandResult) -> bool:
    return result.outcome == CommandOutcome.SUCCEEDED and result.exit_code == 0


def _aws_cli_error_code(stderr: str) -> str | None:
    match = re.search(r"\bAn error occurred \(([A-Za-z0-9]+)\)", stderr)
    return match.group(1) if match else None


def _restrict_private_file(path: Path) -> None:
    if path.is_symlink():
        raise AwsProviderError("refusing to trust symlinked Terraform state", category="state_path_unsafe")
    if path.is_file():
        os.chmod(path, 0o600)


def _json_execution(stdout: str) -> dict[str, Any]:
    value = json.loads(stdout)
    execution = value.get("AutomationExecution") if isinstance(value, dict) else None
    if not isinstance(execution, dict):
        raise AwsProviderError("GetAutomationExecution response omitted AutomationExecution", category="expiry_readback_invalid")
    return execution


def _automation_matches(execution: dict[str, Any], safety: dict[str, str], parameters: dict[str, list[str]], execution_id: str | None = None) -> bool:
    steps = execution.get("StepExecutions")
    if execution.get("StepExecutionsTruncated") is not False or not isinstance(steps, list):
        return False
    matching = [step for step in steps if isinstance(step, dict) and step.get("StepName") == "waitUntilExpiry"]
    if len(matching) != 1:
        return False
    sleep_step = matching[0]
    inputs = (sleep_step or {}).get("Inputs") or {}
    return (
        execution.get("DocumentName") == safety["document_name"]
        and execution.get("DocumentVersion") == safety["document_version"]
        and execution.get("AutomationExecutionStatus") == "InProgress"
        and execution.get("CurrentAction") == "aws:sleep"
        and execution.get("CurrentStepName") == "waitUntilExpiry"
        and execution.get("Parameters") == parameters
        and (sleep_step or {}).get("Action") == "aws:sleep"
        and (sleep_step or {}).get("StepStatus") == "InProgress"
        and _ssm_scalar(inputs.get("Timestamp")) == parameters["ExpiryTriggerUtc"][0]
        and (execution_id is None or execution.get("AutomationExecutionId") == execution_id)
    )


def _validate_terminal_execution(execution: dict[str, Any], execution_id: str, region: str, safety: dict[str, str] | None, parameters: dict[str, list[str]] | None) -> None:
    if not isinstance(safety, dict) or not isinstance(parameters, dict):
        raise AwsProviderError(f"recorded expiry identity is incomplete in {region}", category="expiry_terminal_identity")
    steps = execution.get("StepExecutions")
    if execution.get("AutomationExecutionId") != execution_id or execution.get("DocumentName") != safety.get("document_name") or execution.get("DocumentVersion") != safety.get("document_version") or execution.get("Parameters") != parameters:
        raise AwsProviderError(f"terminal expiry readback identity differs in {region}", category="expiry_terminal_identity")
    if parameters.get("Region") != [region] or execution.get("StepExecutionsTruncated") is not False or not isinstance(steps, list):
        raise AwsProviderError(f"terminal expiry readback is incomplete in {region}", category="expiry_terminal_incomplete")
    status = execution.get("AutomationExecutionStatus")
    if not steps and status in {"Pending", "Cancelled"}:
        return
    if len(steps) not in {1, 2} or any(not isinstance(step, dict) for step in steps):
        raise AwsProviderError(f"terminal expiry step evidence is ambiguous in {region}", category="expiry_terminal_step")
    sleep = [step for step in steps if step.get("StepName") == "waitUntilExpiry"]
    stop = [step for step in steps if step.get("StepName") == "discoverAndStopOwnedInstances"]
    if len(sleep) != 1 or len(stop) > 1 or len(sleep) + len(stop) != len(steps) or steps[0] is not sleep[0] or (stop and steps[1] is not stop[0]) or sleep[0].get("Action") != "aws:sleep" or _ssm_scalar((sleep[0].get("Inputs") or {}).get("Timestamp")) != parameters.get("ExpiryTriggerUtc", [None])[0]:
        raise AwsProviderError(f"terminal expiry sleep evidence differs in {region}", category="expiry_terminal_step")
    step_status = sleep[0].get("StepStatus")
    if status not in TERMINAL_AUTOMATION_STATES | {"Pending", "InProgress"} or step_status not in {"Pending", "InProgress", "Success", "Cancelled", "TimedOut", "Failed"}:
        raise AwsProviderError(f"terminal expiry status evidence is malformed in {region}", category="expiry_terminal_step")
    stop_status = stop[0].get("StepStatus") if stop else None
    if stop and (stop[0].get("Action") != "aws:executeScript" or not _expiry_stop_inputs_match(stop[0], parameters, region)):
        raise AwsProviderError(f"terminal expiry stop step identity differs in {region}", category="expiry_terminal_step")
    if stop and stop_status not in {"Pending", "InProgress", "Success", "Cancelled", "TimedOut", "Failed"}:
        raise AwsProviderError(f"terminal expiry stop status is malformed in {region}", category="expiry_terminal_step")
    if status == "Success":
        if step_status != "Success" or not stop or stop_status != "Success":
            raise AwsProviderError(f"successful expiry lacks completed stop-step evidence in {region}", category="expiry_terminal_step")
        _expiry_stop_payload(stop[0], parameters)
    elif status == "InProgress":
        if step_status == "Success":
            if not stop or stop_status not in {"Pending", "InProgress"} or execution.get("CurrentStepName") != "discoverAndStopOwnedInstances" or execution.get("CurrentAction") != "aws:executeScript":
                raise AwsProviderError(f"active expiry stop-step evidence conflicts in {region}", category="expiry_terminal_step")
        elif step_status != "InProgress" or stop or execution.get("CurrentStepName") != "waitUntilExpiry" or execution.get("CurrentAction") != "aws:sleep":
            raise AwsProviderError(f"active expiry sleep-step evidence conflicts in {region}", category="expiry_terminal_step")
    elif status in {"Failed", "TimedOut"}:
        if not (step_status in {"Failed", "TimedOut"} or stop_status in {"Failed", "TimedOut"}):
            raise AwsProviderError(f"failed expiry lacks a failed or timed-out step in {region}", category="expiry_terminal_step")
    elif status == "Cancelled":
        if step_status == "Success" and (not stop or stop_status not in {"Pending", "Cancelled"}):
            raise AwsProviderError(f"cancelled expiry has conflicting stop-step evidence in {region}", category="expiry_terminal_step")
        if step_status in {"Failed", "TimedOut"} or stop_status in {"Failed", "TimedOut", "Success", "InProgress"}:
            raise AwsProviderError(f"cancelled expiry has conflicting terminal step evidence in {region}", category="expiry_terminal_step")


def _expiry_stop_inputs_match(step: dict[str, Any], parameters: dict[str, list[str]], region: str) -> bool:
    inputs = step.get("Inputs")
    if not isinstance(inputs, dict):
        return False
    raw = inputs.get("InputPayload")
    if not isinstance(raw, str):
        return False
    try:
        payload = json.loads(raw)
        if isinstance(payload, str):
            payload = json.loads(payload)
    except (TypeError, ValueError):
        return False
    return payload == {"run_id": parameters.get("RunId", [None])[0], "deployment_id": parameters.get("DeploymentId", [None])[0], "region": region}


def _expiry_stop_payload(step: dict[str, Any], parameters: dict[str, list[str]]) -> dict[str, Any]:
    outputs = step.get("Outputs")
    values = outputs.get("OutputPayload") if isinstance(outputs, dict) else None
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], str):
        raise AwsProviderError("successful expiry stop step has absent or ambiguous payload", category="expiry_terminal_payload")
    try:
        payload = json.loads(values[0])
    except (TypeError, ValueError) as exc:
        raise AwsProviderError("successful expiry stop payload is malformed", category="expiry_terminal_payload") from exc
    required = {"run_id", "deployment_id", "discovery_seconds", "action_timeout_seconds", "instances_seen", "stop_requests", "errors", "owned_instance_count_anomaly", "unresolved_instance_ids", "complete_deletion"}
    errors = payload.get("errors") if isinstance(payload, dict) else None
    valid_errors = isinstance(errors, list) and all(isinstance(item, dict) and isinstance(item.get("instance_id"), str) and isinstance(item.get("code"), str) for item in errors)
    if not isinstance(payload, dict) or not required <= payload.keys() or payload.get("run_id") != parameters.get("RunId", [None])[0] or payload.get("deployment_id") != parameters.get("DeploymentId", [None])[0] or payload.get("discovery_seconds") != EXPIRY_SCRIPT_SECONDS or payload.get("action_timeout_seconds") != EXPIRY_ACTION_TIMEOUT_SECONDS or not isinstance(payload.get("instances_seen"), dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in payload["instances_seen"].items()) or not isinstance(payload.get("stop_requests"), list) or not all(isinstance(item, str) for item in payload["stop_requests"]) or not valid_errors or not isinstance(payload.get("owned_instance_count_anomaly"), bool) or not isinstance(payload.get("unresolved_instance_ids"), list) or not all(isinstance(item, str) for item in payload["unresolved_instance_ids"]) or payload.get("complete_deletion") is not False:
        raise AwsProviderError("successful expiry stop payload is incomplete or mismatched", category="expiry_terminal_payload")
    return payload


def _ssm_scalar(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return value
    return decoded if isinstance(decoded, str) else None


def _known_resource_ids(output: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    result: dict[str, dict[str, list[str]]] = {kind: {} for kind in ("instance", "volume", "network_interface")}
    for vm in output.get("vms", {}).values() if isinstance(output.get("vms"), dict) else []:
        if not isinstance(vm, dict):
            continue
        region = vm.get("region")
        if not isinstance(region, str):
            continue
        for kind, key in (("instance", "instance_id"), ("volume", "boot_volume_id"), ("network_interface", "network_interface_id")):
            value = vm.get(key)
            if isinstance(value, str) and value:
                result[kind].setdefault(region, []).append(value)
    return result


def _failure(exc: Exception, occurred_at: datetime) -> FailureEvidence:
    return FailureEvidence(
        category=getattr(exc, "category", "aws_provider"),
        message=str(exc) or type(exc).__name__,
        occurred_at=occurred_at,
        action_id=getattr(exc, "action_id", None),
        details={"exception_type": type(exc).__name__},
    )


def _primary_review_valid(evidence: Optional[StructuredEvidence], scenario: str, now: datetime) -> bool:
    if evidence is None or evidence.value is None:
        return False
    value = evidence.value
    if value.get("decision") != "approved" or value.get("scenario") != scenario:
        return False
    source = value.get("source_path")
    digest = value.get("source_sha256")
    reviewed = value.get("reviewed_at")
    if not isinstance(source, str) or not source.strip() or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        return False
    if not isinstance(reviewed, str):
        return False
    try:
        timestamp = datetime.fromisoformat(reviewed.replace("Z", "+00:00"))
    except ValueError:
        return False
    return timestamp.tzinfo is not None and timestamp.utcoffset() is not None and timestamp <= now


def _redacted_output(value: dict[str, Any]) -> dict[str, Any]:
    # Terraform outputs are expected to contain public identifiers only. Keep an allow-list so
    # future state/output additions do not silently become durable public evidence.
    return {"vms": value.get("vms", {}), "network": value.get("network", {}), "placement": value.get("placement", {})}


def _remaining_inventory(entries: dict[str, Any]) -> dict[str, list[Any]]:
    remaining: dict[str, list[Any]] = {}
    for name, resources in entries.items():
        if resources is None:
            remaining[name] = ["inventory_unknown"]
            continue
        if name == "instances":
            resources = [item for item in resources if item.get("State", {}).get("Name") != "terminated"]
        if name == "peerings":
            resources = [item for item in resources if item.get("Status", {}).get("Code") != "deleted"]
        if resources:
            remaining[name] = resources
    return remaining


def _has_exact_ownership_tags(resource: dict[str, Any], run_id: str, deployment_id: str) -> bool:
    tags = {item.get("Key"): item.get("Value") for item in resource.get("Tags", []) if isinstance(item, dict)}
    return tags.get("cnb_run_id") == run_id and tags.get("cnb_deployment_id") == deployment_id


def _host_key_cloud_init(public_key: str) -> str:
    script = (
        "boot_id=$(cat /proc/sys/kernel/random/boot_id); "
        "fingerprint=$(ssh-keygen -E sha256 -lf /etc/ssh/ssh_host_ed25519_key.pub | awk '{print $2}'); "
        "printf 'CNB_HOSTKEY_BOOT_ID=%s\\nCNB_ED25519_HOST_FINGERPRINT=%s\\n' \"$boot_id\" \"$fingerprint\""
    )
    return "#cloud-config\n" + yaml.safe_dump(
        {"ssh_authorized_keys": [public_key], "runcmd": [["bash", "-lc", script]]},
        sort_keys=False,
    )
