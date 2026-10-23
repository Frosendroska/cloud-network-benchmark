from __future__ import annotations

from datetime import datetime
from ipaddress import IPv4Address
import re
from typing import Any, Dict, List, Optional, Literal

from pydantic import Field, field_validator, model_validator

from .campaign import CampaignOptions, Placement, ReplacementContext, ResolvedObservation, VmIntent
from .common import CONFIG_ID_PATTERN, IDENTIFIER_PATTERN, Evidence, ExperimentMode, FailureEvidence, IdentityContext, Provider, Scenario, StrictModel, StringEvidence, StructuredEvidence, VmRole, utc_datetime
from .admission import AdmissionRequirements, ExpiryEvidence, ReservationEvidence


class RemoteConnectionData(StrictModel):
    role: VmRole
    host: str = Field(min_length=1)
    port: int = Field(default=22, ge=1, le=65535)
    user: str = Field(min_length=1)
    authentication_reference: str = Field(min_length=1)
    host_key_reference: str = Field(min_length=1)
    readiness_expectations: List[str] = Field(default_factory=list)
    failure: Optional[FailureEvidence] = None

    @field_validator("host")
    @classmethod
    def private_ipv4(cls, value: str) -> str:
        address = IPv4Address(value)
        if address.is_unspecified or address.is_multicast:
            raise ValueError("host must not be an unspecified or multicast IPv4 address")
        return value

    @model_validator(mode="after")
    def nonblank_references(self) -> "RemoteConnectionData":
        if not self.authentication_reference.strip() or not self.host_key_reference.strip() or not self.user.strip():
            raise ValueError("user, authentication and host-key references must be nonblank")
        return self


class ProviderDeploymentInput(StrictModel):
    schema_version: Literal[2]
    config_id: str = Field(pattern=CONFIG_ID_PATTERN)
    mode: ExperimentMode
    context: Literal["standard", "failure_check", "replacement"]
    observation_id: str = Field(pattern=IDENTIFIER_PATTERN)
    deployment_id: str = Field(pattern=IDENTIFIER_PATTERN)
    replaces_run_id: Optional[str] = Field(default=None, pattern=IDENTIFIER_PATTERN)
    campaign_id: str = Field(pattern=IDENTIFIER_PATTERN)
    run_id: str = Field(pattern=IDENTIFIER_PATTERN)
    experiment_id: str = Field(pattern=r"^EXP-[0-9]{3,}$")
    provider: Provider
    scenario: Scenario
    scheduled_start: Optional[datetime]
    vm_a: VmIntent
    vm_b: VmIntent
    placement: Placement
    provider_options: Dict[str, Any]
    campaign_options: CampaignOptions
    bootstrap_template: str
    bootstrap_inputs: Dict[str, Any] = Field(default_factory=dict)
    terraform_directory: str
    child_result_path: str
    provisioning_timeout_seconds: Optional[int] = Field(default=None, gt=0)
    readiness_timeout_seconds: Optional[int] = Field(default=None, gt=0)
    cleanup_timeout_seconds: Optional[int] = Field(default=None, gt=0)
    config_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    resolved_sha256: StringEvidence = Field(default_factory=lambda: StringEvidence(unavailable_reason="not supplied"))
    implementation_git_commit: StringEvidence
    design_git_commit: StringEvidence
    admission: AdmissionRequirements
    reservation: Evidence[ReservationEvidence] = Field(default_factory=lambda: Evidence[ReservationEvidence](unavailable_reason="not evaluated"))
    expiry: Evidence[ExpiryEvidence] = Field(default_factory=lambda: Evidence[ExpiryEvidence](unavailable_reason="not registered"))

    @classmethod
    def from_observation(
        cls,
        observation: ResolvedObservation,
        *,
        campaign_id: str,
        run_id: str,
        bootstrap_template: str,
        terraform_directory: str,
        child_result_path: str,
        config_sha256: str,
        implementation_git_commit: StringEvidence,
        design_git_commit: StringEvidence,
        bootstrap_inputs: Optional[Dict[str, Any]] = None,
        resolved_sha256: Optional[StringEvidence] = None,
        reservation: Optional[Evidence[ReservationEvidence]] = None,
        expiry: Optional[Evidence[ExpiryEvidence]] = None,
        replacement_context: Optional[Any] = None,
        observation_id: Optional[str] = None,
        deployment_id: Optional[str] = None,
    ) -> "ProviderDeploymentInput":
        options = observation.options
        replacement = replacement_context or observation.replacement_context
        if observation.replacement_context is not None and replacement != observation.replacement_context:
            raise ValueError("supplied replacement metadata must match the source observation")
        if (observation.context == "replacement") != (replacement is not None):
            raise ValueError("replacement metadata presence must match the source observation context")
        if replacement is not None:
            replacement = ReplacementContext.model_validate(replacement)
            if observation.mode != ExperimentMode.SHORT_STUDY or observation.context != "replacement":
                raise ValueError("replacement metadata requires a short_study replacement observation")
            if not replacement.replaces_run_id.endswith(f"-{Provider(observation.provider).value}"):
                raise ValueError("replacement run provider must match observation provider")
            if observation_id is not None and observation_id != replacement.observation_id:
                raise ValueError("supplied observation_id must match replacement context")
        elif observation.context == "replacement":
            raise ValueError("replacement observation requires replacement metadata")
        observed_id = observation_id or (replacement.observation_id if replacement else f"{run_id}-observation")
        actual_deployment_id = deployment_id or f"{run_id}-deployment"
        return cls(
            schema_version=2,
            config_id=observation.config_id,
            observation_id=observed_id,
            deployment_id=actual_deployment_id,
            replaces_run_id=replacement.replaces_run_id if replacement else None,
            campaign_id=campaign_id,
            run_id=run_id,
            mode=observation.mode,
            context=observation.context,
            experiment_id=observation.experiment_id,
            provider=observation.provider,
            scenario=observation.scenario,
            scheduled_start=observation.scheduled_start,
            vm_a=observation.vm_a,
            vm_b=observation.vm_b,
            placement=observation.placement,
            provider_options=observation.provider_config.provider_options,
            campaign_options=options,
            bootstrap_template=bootstrap_template,
            bootstrap_inputs=bootstrap_inputs or {},
            terraform_directory=terraform_directory,
            child_result_path=child_result_path,
            provisioning_timeout_seconds=options.provisioning_timeout_seconds,
            readiness_timeout_seconds=options.readiness_timeout_seconds,
            cleanup_timeout_seconds=options.cleanup_timeout_seconds,
            config_sha256=config_sha256,
            resolved_sha256=resolved_sha256 or StringEvidence(unavailable_reason="resolved hash not supplied to handoff"),
            implementation_git_commit=implementation_git_commit,
            design_git_commit=design_git_commit,
            admission=observation.admission,
            reservation=reservation or Evidence[ReservationEvidence](unavailable_reason="reservation not evaluated"),
            expiry=expiry or Evidence[ExpiryEvidence](unavailable_reason="expiry not registered"),
        )

    @model_validator(mode="after")
    def exactly_two_roles(self) -> "ProviderDeploymentInput":
        provider = Provider(self.provider)
        if self.run_id != f"{self.campaign_id}-{provider.value}":
            raise ValueError("run_id must match campaign_id and provider")
        if self.deployment_id != f"{self.run_id}-deployment":
            raise ValueError("deployment_id must match run_id")
        if not self.observation_id or not self.observation_id.strip():
            raise ValueError("observation_id must be nonblank")
        if self.replaces_run_id is not None and not self.replaces_run_id.endswith(f"-{provider.value}"):
            raise ValueError("replaces_run_id provider suffix must match deployment provider")
        if (self.context == "replacement") != (self.replaces_run_id is not None):
            raise ValueError("replacement context and replaces_run_id must be supplied together")
        if self.context == "replacement" and ExperimentMode(self.mode) != ExperimentMode.SHORT_STUDY:
            raise ValueError("replacement deployments are only valid for short_study mode")
        for name, expected in AdmissionRequirements.expected_for(self.mode, self.scenario).items():
            if getattr(self.admission, name) != expected:
                raise ValueError(f"admission.{name} must match deployment mode and scenario")
        if self.vm_a.role != VmRole.VM_A.value or self.vm_b.role != VmRole.VM_B.value:
            raise ValueError("deployment requires VM A and VM B roles")
        if self.vm_a.function != "client_traffic_generator" or self.vm_b.function != "server_receiver":
            raise ValueError("deployment requires client/traffic-generator and server/receiver functions")
        expected_keys = {
            Provider.AWS: {"instance_market_type"},
            Provider.AZURE: {"priority", "eviction_policy", "max_price"},
            Provider.GCP: {"provisioning_model", "instance_termination_action"},
        }
        if set(self.provider_options) != expected_keys[Provider(self.provider)]:
            raise ValueError(f"provider_options do not match {self.provider}")
        placement_kinds = {
            Provider.AWS: {"none", "cluster_placement_group"},
            Provider.AZURE: {"none", "proximity_placement_group"},
            Provider.GCP: {"none", "compact_placement_policy"},
        }
        if self.placement.kind not in placement_kinds[provider]:
            raise ValueError(f"invalid placement kind for {provider.value}")
        if (self.placement.kind == "none") != (self.placement.name is None):
            raise ValueError("placement name must be null only when placement is none")
        purchase_values = {
            Provider.AWS: {"instance_market_type": {"on_demand"}},
            Provider.AZURE: {"priority": {"Regular"}, "eviction_policy": {"Delete"}},
            Provider.GCP: {"provisioning_model": {"STANDARD"}, "instance_termination_action": {"STOP"}},
        }
        for name, allowed in purchase_values[provider].items():
            if not any(self.provider_options.get(name) == value for value in allowed):
                raise ValueError(f"invalid provider option {name} for {provider.value}")
        if provider == Provider.AZURE:
            price = self.provider_options.get("max_price")
            if isinstance(price, bool) or not isinstance(price, (int, float)):
                raise ValueError("Azure max_price must be numeric")
        if self.scheduled_start is not None and (self.scheduled_start.tzinfo is None or self.scheduled_start.utcoffset() is None):
            raise ValueError("scheduled_start must include a timezone")
        if self.scheduled_start is not None:
            object.__setattr__(self, "scheduled_start", utc_datetime(self.scheduled_start))
        if self.resolved_sha256.value is not None and not re.fullmatch(r"^[a-f0-9]{64}$", self.resolved_sha256.value):
            raise ValueError("resolved_sha256 must be lowercase SHA-256")
        expiry = self.expiry.value
        if expiry is not None and (expiry.run_id != self.run_id or expiry.deployment_id != self.deployment_id or expiry.provider != provider):
            raise ValueError("expiry identity must match deployment input")
        reservation = self.reservation.value
        if reservation is not None and reservation.campaign_id != self.campaign_id:
            raise ValueError("reservation campaign_id must match deployment input")
        if reservation is not None and expiry is not None and reservation.expires_at.value is not None and expiry.expires_at.value is not None:
            if expiry.expires_at.value > reservation.expires_at.value:
                raise ValueError("provider expiry must not exceed reserved lifetime cap")
        if reservation is not None and expiry is not None and reservation.cap_started_at.value is not None and expiry.registered_at.value is not None:
            if expiry.registered_at.value < reservation.cap_started_at.value:
                raise ValueError("expiry registration must not precede reserved cap start")
        region_equal = self.vm_a.region == self.vm_b.region
        zone_equal = self.vm_a.zone == self.vm_b.zone
        if self.scenario == Scenario.SAME_ZONE and not (region_equal and zone_equal and self.placement.kind == "none"):
            raise ValueError("same_zone deployment requires equal locality and no placement optimization")
        if self.scenario == Scenario.CROSS_ZONE and not (region_equal and not zone_equal and self.placement.kind == "none"):
            raise ValueError("cross_zone deployment requires same region, distinct zones, and no placement optimization")
        if self.scenario == Scenario.PLACEMENT_OPTIMIZATION and not (region_equal and zone_equal and self.placement.kind != "none"):
            raise ValueError("placement_optimization requires shared region and zone with provider placement")
        if self.scenario == Scenario.INTER_REGION and not (not region_equal and self.placement.kind == "none"):
            raise ValueError("inter_region deployment requires distinct regions and no placement optimization")
        return self


    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema: Any, handler: Any) -> Dict[str, Any]:
        schema = handler(core_schema)
        schema.setdefault("allOf", []).append({
            "if": {"properties": {"provider": {"const": "gcp"}}, "required": ["provider"]},
            "then": {"properties": {"provider_options": {
                "type": "object",
                "required": ["provisioning_model", "instance_termination_action"],
                "minProperties": 2,
                "maxProperties": 2,
                "properties": {
                    "provisioning_model": {"const": "STANDARD"},
                    "instance_termination_action": {"const": "STOP"},
                },
            }}},
        })
        return schema


class ProviderVmOutput(StrictModel):
    role: VmRole
    resource_id: Optional[str]
    private_ipv4: Optional[str]
    image_identity: Optional[str]
    actual_shape: Optional[str]
    region: Optional[str]
    zone: Optional[str]
    placement_metadata: StructuredEvidence
    connection: Optional[RemoteConnectionData]
    unavailable: Optional[StructuredEvidence] = None
    public_ipv4: Evidence[str] = Field(default_factory=lambda: Evidence[str](unavailable_reason="not observed"))
    physical_zone_id: StringEvidence = Field(default_factory=lambda: StringEvidence(unavailable_reason="not observed"))

    @field_validator("private_ipv4")
    @classmethod
    def private_output_ipv4(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            address = IPv4Address(value)
            if address.is_unspecified or address.is_multicast or not address.is_private:
                raise ValueError("private_ipv4 must be a usable private IPv4 address")
        return value

    @field_validator("public_ipv4")
    @classmethod
    def valid_public_ipv4(cls, value: Evidence[str]) -> Evidence[str]:
        if value.value is not None:
            address = IPv4Address(value.value)
            if address.is_private or address.is_unspecified or address.is_multicast or address.is_loopback or address.is_reserved:
                raise ValueError("public_ipv4 must be a usable public IPv4 address")
        return value

    @model_validator(mode="after")
    def complete_or_explained_partial(self) -> "ProviderVmOutput":
        if self.connection is not None:
            if self.connection.role != self.role:
                raise ValueError("connection role must match VM output role")
        deployment_fields = (
            self.resource_id,
            self.private_ipv4,
            self.image_identity,
            self.actual_shape,
            self.region,
            self.zone,
            self.connection,
        )
        complete = all(value is not None for value in deployment_fields)
        if complete and self.unavailable is not None:
            raise ValueError("complete VM output must not include unavailable evidence")
        if not complete and self.unavailable is None:
            raise ValueError("partial VM output requires unavailable evidence")
        return self


class ProviderDeploymentOutput(StrictModel):
    schema_version: Literal[2]
    identity: IdentityContext
    run_id: str = Field(min_length=1)
    deployment_id: str = Field(min_length=1)
    mode: ExperimentMode
    provider: Provider
    scenario: Scenario
    apply_action_id: str = Field(min_length=1)
    started_at: datetime
    finished_at: datetime
    vm_a: ProviderVmOutput
    vm_b: ProviderVmOutput
    provider_metadata: StructuredEvidence
    documented_network_limit: StructuredEvidence
    output_artifact_path: str = "terraform-outputs.redacted.json"
    expiry: Evidence[ExpiryEvidence] = Field(default_factory=lambda: Evidence[ExpiryEvidence](unavailable_reason="not registered"))

    @model_validator(mode="after")
    def roles_and_time(self) -> "ProviderDeploymentOutput":
        if self.finished_at < self.started_at:
            raise ValueError("finished_at precedes started_at")
        if self.started_at.tzinfo is None or self.finished_at.tzinfo is None:
            raise ValueError("deployment timestamps must include a timezone")
        object.__setattr__(self, "started_at", utc_datetime(self.started_at))
        object.__setattr__(self, "finished_at", utc_datetime(self.finished_at))
        if self.vm_a.role != VmRole.VM_A.value or self.vm_b.role != VmRole.VM_B.value:
            raise ValueError("deployment output requires VM A and VM B")
        if self.run_id.rsplit("-", 1)[-1] != Provider(self.provider).value:
            raise ValueError("run_id provider suffix must match deployment provider")
        if self.output_artifact_path != "terraform-outputs.redacted.json":
            raise ValueError("output_artifact_path must be terraform-outputs.redacted.json")
        if self.deployment_id != f"{self.run_id}-deployment":
            raise ValueError("deployment_id must identify this provider attempt")
        if self.identity.run_id != self.run_id or self.identity.deployment_id != self.deployment_id:
            raise ValueError("deployment output identity must match run and deployment IDs")
        if self.identity.provider != self.provider:
            raise ValueError("deployment output identity provider must match output provider")
        if self.expiry.value is not None and (
            self.expiry.value.run_id != self.run_id or self.expiry.value.deployment_id != self.deployment_id
            or self.expiry.value.provider != self.provider
        ):
            raise ValueError("deployment expiry identity must match output run, deployment and provider")
        if self.expiry.value is not None and self.expiry.value.registered_at.value is not None and self.expiry.value.registered_at.value > self.started_at:
            raise ValueError("expiry registration must precede provider apply start")
        return self
