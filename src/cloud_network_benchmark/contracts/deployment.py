from __future__ import annotations

from datetime import datetime
from ipaddress import IPv4Address
from typing import Any, Dict, List, Optional

from pydantic import Field, field_validator, model_validator

from .campaign import CampaignOptions, Placement, ResolvedObservation, VmIntent
from .common import FailureEvidence, Provider, Scenario, StrictModel, StringEvidence, StructuredEvidence, VmRole


class RemoteConnectionData(StrictModel):
    role: VmRole
    host: str = Field(min_length=1)
    port: int = Field(default=22, ge=1, le=65535)
    user: str = Field(min_length=1)
    authentication_reference: str = Field(min_length=1)
    host_key_reference: Optional[str] = None
    readiness_expectations: List[str] = Field(default_factory=list)
    failure: Optional[FailureEvidence] = None

    @field_validator("host")
    @classmethod
    def private_ipv4(cls, value: str) -> str:
        address = IPv4Address(value)
        if not address.is_private:
            raise ValueError("host must be a private IPv4 address")
        return value


class ProviderDeploymentInput(StrictModel):
    campaign_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    experiment_id: str = Field(pattern=r"^EXP-[0-9]{3,}$")
    provider: Provider
    scenario: Scenario
    scheduled_start: datetime
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
    implementation_git_commit: StringEvidence
    design_git_commit: StringEvidence

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
    ) -> "ProviderDeploymentInput":
        options = observation.options
        return cls(
            campaign_id=campaign_id,
            run_id=run_id,
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
            implementation_git_commit=implementation_git_commit,
            design_git_commit=design_git_commit,
        )

    @model_validator(mode="after")
    def exactly_two_roles(self) -> "ProviderDeploymentInput":
        if self.run_id != f"{self.campaign_id}-{Provider(self.provider).value}":
            raise ValueError("run_id must match campaign_id and provider")
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
        provider = Provider(self.provider)
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
            Provider.AWS: {"instance_market_type": {"on_demand", "spot"}},
            Provider.AZURE: {"priority": {"Regular", "Spot"}, "eviction_policy": {"Delete", "Deallocate"}},
            Provider.GCP: {"provisioning_model": {"STANDARD", "SPOT"}, "instance_termination_action": {"DELETE", "STOP"}},
        }
        for name, allowed in purchase_values[provider].items():
            if self.provider_options.get(name) not in allowed:
                raise ValueError(f"invalid provider option {name} for {provider.value}")
        if provider == Provider.AZURE:
            price = self.provider_options.get("max_price")
            if isinstance(price, bool) or not isinstance(price, (int, float)):
                raise ValueError("Azure max_price must be numeric")
        if self.scheduled_start.tzinfo is None or self.scheduled_start.utcoffset() is None:
            raise ValueError("scheduled_start must include a timezone")
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

    @field_validator("private_ipv4")
    @classmethod
    def private_output_ipv4(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not IPv4Address(value).is_private:
            raise ValueError("private_ipv4 must be private")
        return value

    @model_validator(mode="after")
    def complete_or_explained_partial(self) -> "ProviderVmOutput":
        if self.connection is not None:
            if self.connection.role != self.role:
                raise ValueError("connection role must match VM output role")
            if self.private_ipv4 is not None and self.connection.host != self.private_ipv4:
                raise ValueError("connection host must match VM private_ipv4")
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
    run_id: str = Field(min_length=1)
    provider: Provider
    scenario: Scenario
    apply_action_id: str = Field(min_length=1)
    started_at: datetime
    finished_at: datetime
    vm_a: ProviderVmOutput
    vm_b: ProviderVmOutput
    provider_metadata: StructuredEvidence
    documented_network_limit: StructuredEvidence
    output_artifact_path: str = "terraform-outputs.json"

    @model_validator(mode="after")
    def roles_and_time(self) -> "ProviderDeploymentOutput":
        if self.finished_at < self.started_at:
            raise ValueError("finished_at precedes started_at")
        if self.started_at.tzinfo is None or self.finished_at.tzinfo is None:
            raise ValueError("deployment timestamps must include a timezone")
        if self.vm_a.role != VmRole.VM_A.value or self.vm_b.role != VmRole.VM_B.value:
            raise ValueError("deployment output requires VM A and VM B")
        if self.run_id.rsplit("-", 1)[-1] != Provider(self.provider).value:
            raise ValueError("run_id provider suffix must match deployment provider")
        if self.output_artifact_path != "terraform-outputs.json":
            raise ValueError("output_artifact_path must be terraform-outputs.json")
        return self
