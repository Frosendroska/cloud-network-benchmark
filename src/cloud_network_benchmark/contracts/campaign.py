from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional, Union

from pydantic import Field, field_validator, model_validator

from .artifacts import ArtifactLayout
from .common import MeasurementDirection, Provider, Scenario, StrictModel, VmRole


Scalar = Union[str, int, float, bool]


class Pair(StrictModel):
    vm_a: str = Field(min_length=1)
    vm_b: str = Field(min_length=1)


class Placement(StrictModel):
    kind: str
    name: Optional[str]


class PhaseConfiguration(StrictModel):
    enabled: bool
    test_name: str = Field(min_length=1)
    duration_seconds: int = Field(gt=0)
    timeout_seconds: int = Field(gt=0)
    parameters: Dict[str, Scalar]


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


class ConfigProvenance(StrictModel):
    source_path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    byte_length: int = Field(gt=0)
    snapshot_path: Optional[str] = None
    implementation_git_commit: Optional[Dict[str, Any]] = None
    design_git_commit: Optional[Dict[str, Any]] = None


class VmIntent(StrictModel):
    role: VmRole
    function: str
    region: str
    zone: str
    vm_shape: str
    image: str
    connection_user: str


class ResolvedObservation(StrictModel):
    experiment_id: str
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


class ResolvedCampaign(StrictModel):
    schema_version: Literal[1]
    experiment_id: str
    configuration_role: Literal["experiment", "test"]
    config_source: ConfigProvenance
    scheduled_start: datetime
    scenario: Scenario
    selected_providers: List[Provider]
    benchmark: BenchmarkPlan
    observations: List[ResolvedObservation]
