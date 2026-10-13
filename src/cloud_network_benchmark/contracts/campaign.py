from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional, Union

from pydantic import Field, field_validator, model_validator

from .artifacts import ArtifactLayout
from .common import MeasurementDirection, Provider, Scenario, StrictModel, VmRole


Scalar = Union[str, int, float, bool]


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be blank")
    return value


class Pair(StrictModel):
    vm_a: str = Field(min_length=1)
    vm_b: str = Field(min_length=1)

    @field_validator("vm_a", "vm_b")
    @classmethod
    def locality_not_blank(cls, value: str) -> str:
        return _nonblank(value)


class Placement(StrictModel):
    kind: str
    name: Optional[str]

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: Optional[str]) -> Optional[str]:
        return _nonblank(value) if value is not None else None


class PhaseConfiguration(StrictModel):
    enabled: bool
    test_name: str = Field(min_length=1)
    duration_seconds: int = Field(gt=0)
    timeout_seconds: int = Field(gt=0)
    parameters: Dict[str, Scalar]

    @field_validator("test_name")
    @classmethod
    def test_name_not_blank(cls, value: str) -> str:
        return _nonblank(value)


class BenchmarkPlan(StrictModel):
    idle_latency: PhaseConfiguration
    single_flow: PhaseConfiguration
    multi_flow: PhaseConfiguration
    execution_order: List[str]

    @model_validator(mode="after")
    def validate_plan(self) -> "BenchmarkPlan":
        expected = ["idle_latency", "single_flow", "multi_flow"]
        if self.execution_order != expected:
            raise ValueError(f"execution_order must equal {expected}")
        if not self.idle_latency.enabled or not self.single_flow.enabled:
            raise ValueError("idle_latency and single_flow must be enabled")
        streams = self.multi_flow.parameters.get("upload_streams")
        if self.multi_flow.enabled and (not isinstance(streams, int) or isinstance(streams, bool) or streams < 1):
            raise ValueError("enabled multi_flow requires positive integer upload_streams")
        return self


class CampaignOptions(StrictModel):
    labels: Dict[str, str] = Field(default_factory=dict)
    provisioning_timeout_seconds: Optional[int] = Field(default=None, gt=0)
    readiness_timeout_seconds: Optional[int] = Field(default=None, gt=0)
    cleanup_timeout_seconds: Optional[int] = Field(default=None, gt=0)


class ProviderConfiguration(StrictModel):
    provider: Provider
    regions: Pair
    zones: Pair
    vm_shape: str = Field(min_length=1)
    image: str = Field(min_length=1)
    connection_user: str = Field(min_length=1)
    documented_network_limit: Optional[str]
    placement: Placement
    provider_options: Dict[str, Optional[Scalar]]

    @field_validator("vm_shape", "image", "connection_user")
    @classmethod
    def required_intent_not_blank(cls, value: str) -> str:
        return _nonblank(value)

    @field_validator("documented_network_limit")
    @classmethod
    def network_limit_not_blank(cls, value: Optional[str]) -> Optional[str]:
        return _nonblank(value) if value is not None else None

    @model_validator(mode="after")
    def validate_placement_kind(self) -> "ProviderConfiguration":
        allowed = {
            Provider.AWS: {"none", "cluster_placement_group"},
            Provider.AZURE: {"none", "proximity_placement_group"},
            Provider.GCP: {"none", "compact_placement_policy"},
        }
        if self.placement.kind not in allowed[Provider(self.provider)]:
            raise ValueError(f"invalid placement kind for {self.provider}")
        if self.placement.kind == "none" and self.placement.name is not None:
            raise ValueError("placement name must be null when kind is none")
        if self.placement.kind != "none" and not self.placement.name:
            raise ValueError("optimized placement requires a name")
        provider_options = self.provider_options
        provider = Provider(self.provider)
        expected_option_keys = {
            Provider.AWS: {"instance_market_type"},
            Provider.AZURE: {"priority", "eviction_policy", "max_price"},
            Provider.GCP: {"provisioning_model", "instance_termination_action"},
        }
        if set(provider_options) != expected_option_keys[provider]:
            raise ValueError(f"provider_options for {provider.value} must contain exactly {sorted(expected_option_keys[provider])}")
        if provider == Provider.AWS and provider_options["instance_market_type"] not in {"on_demand", "spot"}:
            raise ValueError("AWS instance_market_type must be on_demand or spot")
        if provider == Provider.AZURE:
            if provider_options["priority"] not in {"Regular", "Spot"}:
                raise ValueError("Azure priority must be Regular or Spot")
            if provider_options["eviction_policy"] not in {"Delete", "Deallocate"}:
                raise ValueError("Azure eviction_policy must be Delete or Deallocate")
            max_price = provider_options["max_price"]
            if isinstance(max_price, bool) or not isinstance(max_price, (int, float)):
                raise ValueError("Azure max_price must be numeric")
        if provider == Provider.GCP:
            if provider_options["provisioning_model"] not in {"STANDARD", "SPOT"}:
                raise ValueError("GCP provisioning_model must be STANDARD or SPOT")
            if provider_options["instance_termination_action"] not in {"DELETE", "STOP"}:
                raise ValueError("GCP instance_termination_action must be DELETE or STOP")
        return self


class CampaignConfiguration(StrictModel):
    schema_version: Literal[1]
    experiment_id: str = Field(pattern=r"^EXP-[0-9]{3,}$")
    scheduled_start: datetime
    selected_providers: List[Provider] = Field(min_length=1, max_length=3)
    scenario: Scenario
    benchmark: BenchmarkPlan
    provider_configs: Dict[Provider, ProviderConfiguration]
    options: CampaignOptions

    @field_validator("scheduled_start")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scheduled_start must include a timezone")
        return value

    @field_validator("selected_providers")
    @classmethod
    def unique_providers(cls, value: List[Provider]) -> List[Provider]:
        if len(set(value)) != len(value):
            raise ValueError("selected_providers must be unique")
        return value

    @model_validator(mode="after")
    def validate_scenario_phases(self) -> "CampaignConfiguration":
        if self.scenario != Scenario.INTER_REGION and not self.benchmark.multi_flow.enabled:
            raise ValueError(
                "same_zone, cross_zone, and placement_optimization require multi_flow to be enabled"
            )
        return self


class ConfigProvenance(StrictModel):
    source_path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    byte_length: int = Field(gt=0)
    snapshot_path: Optional[str] = None
    implementation_git_commit: Optional[Dict[str, Any]] = None
    design_git_commit: Optional[Dict[str, Any]] = None


class VmIntent(StrictModel):
    role: VmRole
    function: str = Field(min_length=1)
    region: str = Field(min_length=1)
    zone: str = Field(min_length=1)
    vm_shape: str = Field(min_length=1)
    image: str = Field(min_length=1)
    connection_user: str = Field(min_length=1)

    @field_validator("function", "region", "zone", "vm_shape", "image", "connection_user")
    @classmethod
    def intent_not_blank(cls, value: str) -> str:
        return _nonblank(value)


class ResolvedObservation(StrictModel):
    experiment_id: str = Field(pattern=r"^EXP-[0-9]{3,}$")
    provider: Provider
    scenario: Scenario
    scheduled_start: datetime
    vm_a: VmIntent
    vm_b: VmIntent
    measurement_direction: MeasurementDirection = MeasurementDirection.VM_A_TO_VM_B
    placement: Placement
    benchmark: BenchmarkPlan
    artifact_layout: ArtifactLayout = Field(default_factory=ArtifactLayout)
    provider_config: ProviderConfiguration
    options: CampaignOptions

    @field_validator("scheduled_start")
    @classmethod
    def normalize_schedule(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scheduled_start must include a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def provider_roles_and_intent(self) -> "ResolvedObservation":
        if self.vm_a.role != VmRole.VM_A or self.vm_b.role != VmRole.VM_B:
            raise ValueError("resolved observations require VM A then VM B roles")
        if self.vm_a.function != "client_traffic_generator" or self.vm_b.function != "server_receiver":
            raise ValueError("resolved observations require client/traffic-generator and server/receiver functions")
        if self.measurement_direction != MeasurementDirection.VM_A_TO_VM_B:
            raise ValueError("measurement_direction must be vm_a_to_vm_b")
        if Provider(self.provider_config.provider) != Provider(self.provider):
            raise ValueError("provider_config provider must match observation provider")
        if self.placement != self.provider_config.placement:
            raise ValueError("placement must match provider_config placement")
        for intent, role, region, zone in (
            (self.vm_a, VmRole.VM_A, self.provider_config.regions.vm_a, self.provider_config.zones.vm_a),
            (self.vm_b, VmRole.VM_B, self.provider_config.regions.vm_b, self.provider_config.zones.vm_b),
        ):
            if intent.role != role or (intent.region, intent.zone) != (region, zone):
                raise ValueError("VM intent role and locality must match provider configuration")
            if (intent.vm_shape, intent.image, intent.connection_user) != (
                self.provider_config.vm_shape,
                self.provider_config.image,
                self.provider_config.connection_user,
            ):
                raise ValueError("VM intent shape, image, and user must match provider configuration")
        return self


class ResolvedCampaign(StrictModel):
    schema_version: Literal[1]
    experiment_id: str = Field(pattern=r"^EXP-[0-9]{3,}$")
    configuration_role: Literal["experiment", "test"]
    config_source: ConfigProvenance
    scheduled_start: datetime
    scenario: Scenario
    selected_providers: List[Provider]
    benchmark: BenchmarkPlan
    options: CampaignOptions
    observations: List[ResolvedObservation]

    @field_validator("scheduled_start")
    @classmethod
    def normalize_schedule(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scheduled_start must include a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def observations_match_campaign(self) -> "ResolvedCampaign":
        providers = [Provider(provider) for provider in self.selected_providers]
        if not providers or len(providers) != len(set(providers)):
            raise ValueError("selected_providers must be non-empty and unique")
        observation_providers = [Provider(item.provider) for item in self.observations]
        if len(observation_providers) != len(set(observation_providers)) or set(observation_providers) != set(providers):
            raise ValueError("observations must match selected_providers exactly once")
        for item in self.observations:
            if (
                item.experiment_id != self.experiment_id
                or item.scenario != self.scenario
                or item.scheduled_start != self.scheduled_start
                or item.benchmark != self.benchmark
                or item.options != self.options
            ):
                raise ValueError("observation shared intent must match campaign")
        return self
