from __future__ import annotations

import ipaddress
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import Field, field_validator, model_validator

from .admission import AdmissionRequirements, ScheduleEntry
from .artifacts import ArtifactLayout
from .common import (
    CONFIG_ID_PATTERN,
    IDENTIFIER_PATTERN,
    SHA256_PATTERN,
    BenchmarkPhase,
    Evidence,
    ExperimentMode,
    MeasurementDirection,
    Provider,
    Scenario,
    StrictModel,
    VmRole,
)


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
    collocated: bool = False
    max_distance: Optional[int] = None

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: Optional[str]) -> Optional[str]:
        return _nonblank(value) if value is not None else None

    @model_validator(mode="after")
    def no_protocol_distance(self) -> "Placement":
        if self.max_distance is not None:
            raise ValueError("max_distance is not supported by the selected protocol")
        return self


class OperatingSystemIntent(StrictModel):
    distribution: Literal["ubuntu"] = "ubuntu"
    release: Literal["24.04"] = "24.04"
    architecture: Literal["amd64"] = "amd64"


class DiskIntent(StrictModel):
    size_gib: Literal[32] = 32
    kind: Literal["gp3", "StandardSSD_LRS_E4", "pd-balanced"]


class NetworkIntent(StrictModel):
    private_ipv4: Literal[True] = True
    public_ipv4_per_vm: Literal[1] = 1
    mtu: Literal[1460] = 1460
    tcp_congestion_control: Literal["cubic"] = "cubic"
    dscp: Literal[0] = 0
    ecn: Literal[False] = False
    default_offloads: Literal[True] = True
    address_spaces: Dict[str, str]
    subnets: Dict[str, List[str]]

    @model_validator(mode="after")
    def nonoverlapping_networks(self) -> "NetworkIntent":
        networks = [ipaddress.ip_network(value, strict=True) for value in self.address_spaces.values()]
        subnets = [ipaddress.ip_network(value, strict=True) for values in self.subnets.values() for value in values]
        if len(networks) != len(set(networks)) or len(subnets) != len(set(subnets)):
            raise ValueError("network CIDRs must not overlap or repeat")
        for index, network in enumerate(networks):
            if any(network.overlaps(other) for other in networks[index + 1:]):
                raise ValueError("address spaces must not overlap within a run")
        for subnet in subnets:
            if not any(subnet.subnet_of(network) for network in networks):
                raise ValueError("each subnet must belong to a declared address space")
        for index, subnet in enumerate(subnets):
            if any(subnet.overlaps(other) for other in subnets[index + 1:]):
                raise ValueError("subnets must not overlap within a run")
        return self


class SecurityIntent(StrictModel):
    controller_cidr: Evidence[str]
    sockperf_udp_port: Literal[11111] = 11111
    iperf3_tcp_port: Literal[5201] = 5201
    benchmark_source: Literal["vm_a_private"] = "vm_a_private"
    strict_host_key_checking: Literal[True] = True
    batch_mode: Literal[True] = True

    @field_validator("controller_cidr")
    @classmethod
    def controller_is_pending_or_ipv4_host(cls, value: Evidence[str]) -> Evidence[str]:
        if value.value is not None:
            network = ipaddress.ip_network(value.value, strict=True)
            if network.version != 4 or network.prefixlen != 32:
                raise ValueError("controller_cidr must be an IPv4 /32")
        return value


class AccelerationIntent(StrictModel):
    kind: Literal["ena", "accelerated_networking", "gvnic"]


class CommonNState(StrictModel):
    state: Literal["pending", "frozen"] = "pending"
    common_n: Optional[Literal[2, 4, 8]] = None
    source_campaign_id: Optional[str] = Field(default=None, pattern=IDENTIFIER_PATTERN)
    source_sha256: Optional[str] = Field(default=None, pattern=SHA256_PATTERN)
    plateau_found: Optional[bool] = None

    @model_validator(mode="after")
    def coherent_state(self) -> "CommonNState":
        evidence = (self.common_n, self.source_campaign_id, self.source_sha256, self.plateau_found)
        if self.state == "pending" and any(value is not None for value in evidence):
            raise ValueError("pending common-N state must have null result/source evidence")
        if self.state == "frozen" and any(value is None for value in evidence):
            raise ValueError("frozen common-N state requires N, source identity/hash and plateau outcome")
        if self.state == "frozen" and self.plateau_found is False and self.common_n != 8:
            raise ValueError("no common plateau requires fallback common_n=8")
        return self


class ReplacementContext(StrictModel):
    observation_id: str = Field(pattern=IDENTIFIER_PATTERN)
    replaces_run_id: str = Field(pattern=IDENTIFIER_PATTERN)


class PhaseConfiguration(StrictModel):
    unit_id: str = Field(pattern=IDENTIFIER_PATTERN)
    kind: Literal["phase", "calibration_trial", "continuous_day", "probe_chunk"]
    phase: BenchmarkPhase
    measured_seconds: int = Field(gt=0)
    streams: Optional[int] = Field(default=None, gt=0)
    trial_index: Optional[int] = Field(default=None, ge=1, le=12)
    parent_unit_id: Optional[str] = Field(default=None, pattern=IDENTIFIER_PATTERN)

    @model_validator(mode="after")
    def phase_stream_consistency(self) -> "PhaseConfiguration":
        if self.phase == BenchmarkPhase.IDLE_LATENCY and self.streams is not None:
            raise ValueError("idle_latency does not use TCP streams")
        if self.kind == "calibration_trial" and self.trial_index is None:
            raise ValueError("calibration trial requires trial_index")
        if self.kind != "calibration_trial" and self.trial_index is not None:
            raise ValueError("trial_index is only valid for calibration_trial")
        if self.kind == "probe_chunk" and self.parent_unit_id is None:
            raise ValueError("probe_chunk requires a parent unit")
        return self


class BenchmarkPlan(StrictModel):
    measured_seconds: int = Field(gt=0)
    warmup_seconds: Literal[5] = 5
    pause_seconds: Literal[0, 10]
    stabilization_seconds: Literal[60] = 60
    probe_lead_seconds: Literal[2] = 2
    probe_bytes: Literal[64] = 64
    probe_mps: Literal[1000] = 1000
    reply_every: Literal[1] = 1
    full_rtt: Literal[True] = True
    no_rdtsc: Literal[True] = True
    tcp_congestion_control: Literal["cubic"] = "cubic"
    tcp_zerocopy: Literal[True] = True
    tcp_buffer_bytes: Literal[131072] = 131072
    sample_interval_seconds: Literal[1] = 1
    probe_chunk_seconds: Optional[Literal[300]] = None
    common_n: CommonNState = Field(default_factory=CommonNState)

    @field_validator("measured_seconds")
    @classmethod
    def duration_finite_positive(cls, value: int) -> int:
        if isinstance(value, bool):
            raise ValueError("measured_seconds must be an integer")
        return value

    def validate_mode(self, mode: ExperimentMode) -> None:
        expected = {
            ExperimentMode.ACCEPTANCE: (10, 10, None),
            ExperimentMode.SHORT_STUDY: (60, 10, None),
            ExperimentMode.CALIBRATION: (60, 10, None),
            ExperimentMode.DIAGNOSTIC_DAY: (86400, 0, 300),
        }[ExperimentMode(mode)]
        if (self.measured_seconds, self.pause_seconds, self.probe_chunk_seconds) != expected:
            raise ValueError(f"benchmark settings do not match {ExperimentMode(mode).value} protocol")


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
    os: OperatingSystemIntent = Field(default_factory=OperatingSystemIntent)
    disk: DiskIntent
    network: NetworkIntent
    security: SecurityIntent
    acceleration: AccelerationIntent

    @field_validator("vm_shape", "image", "connection_user")
    @classmethod
    def required_intent_not_blank(cls, value: str) -> str:
        return _nonblank(value)

    @field_validator("documented_network_limit")
    @classmethod
    def network_limit_not_blank(cls, value: Optional[str]) -> Optional[str]:
        return _nonblank(value) if value is not None else None

    @model_validator(mode="after")
    def validate_provider_settings(self) -> "ProviderConfiguration":
        provider = Provider(self.provider)
        expected = {
            Provider.AWS: ("gp3", "ena", "on_demand", {"instance_market_type"}),
            Provider.AZURE: ("StandardSSD_LRS_E4", "accelerated_networking", "Regular", {"priority", "eviction_policy", "max_price"}),
            Provider.GCP: ("pd-balanced", "gvnic", "STANDARD", {"provisioning_model", "instance_termination_action"}),
        }[provider]
        disk, acceleration, purchase, keys = expected
        if self.disk.kind != disk or self.acceleration.kind != acceleration:
            raise ValueError(f"disk or acceleration intent does not match {provider.value}")
        if set(self.provider_options) != keys:
            raise ValueError(f"provider_options for {provider.value} must contain exactly {sorted(keys)}")
        if provider == Provider.AWS:
            option_values = self.provider_options["instance_market_type"] == purchase
        elif provider == Provider.AZURE:
            option_values = self.provider_options["priority"] == "Regular" and self.provider_options["eviction_policy"] == "Delete" and self.provider_options["max_price"] == -1.0
        else:
            option_values = self.provider_options["provisioning_model"] == "STANDARD" and self.provider_options["instance_termination_action"] == "DELETE"
        if not option_values:
            raise ValueError("only the declared On-Demand provider settings are supported")
        placement_kind = {
            Provider.AWS: {"none", "cluster_placement_group"},
            Provider.AZURE: {"none", "proximity_placement_group"},
            Provider.GCP: {"none", "compact_placement_policy"},
        }[provider]
        if self.placement.kind not in placement_kind:
            raise ValueError(f"invalid placement kind for {provider.value}")
        if (self.placement.kind == "none") != (self.placement.name is None):
            raise ValueError("placement name must be null only when kind is none")
        if self.placement.collocated and provider != Provider.GCP:
            raise ValueError("collocated placement is only supported by GCP")
        regions = set(self.regions.model_dump().values())
        if set(self.network.address_spaces) != regions or set(self.network.subnets) != regions:
            raise ValueError("network address spaces/subnets must match selected regions")
        expected_networks = {
            Provider.AWS: {"eu-central-1": "10.60.0.0/16", "eu-west-2": "10.61.0.0/16"},
            Provider.AZURE: {"germanywestcentral": "10.70.0.0/16", "uksouth": "10.71.0.0/16"},
            Provider.GCP: {"europe-west3": "10.80.1.0/24", "europe-west2": "10.81.1.0/24"},
        }[provider]
        for region in regions:
            if self.network.address_spaces[region] != expected_networks[region]:
                raise ValueError(f"unexpected network range for {provider.value} {region}")
        if provider == Provider.GCP and self.placement.kind == "compact_placement_policy" and not self.placement.collocated:
            raise ValueError("GCP placement optimization requires collocated=true")
        return self


class CampaignConfiguration(StrictModel):
    schema_version: Literal[2]
    config_id: str = Field(pattern=CONFIG_ID_PATTERN)
    experiment_id: str = Field(pattern=r"^EXP-[0-9]{3,}$")
    mode: ExperimentMode
    context: Literal["standard", "failure_check", "replacement"] = "standard"
    scheduled_start: Optional[datetime]
    selected_providers: List[Provider] = Field(min_length=1, max_length=3)
    scenario: Scenario
    benchmark: BenchmarkPlan
    provider_configs: Dict[Provider, ProviderConfiguration]
    options: CampaignOptions
    admission: AdmissionRequirements
    replacement_context: Optional[ReplacementContext] = None

    @field_validator("scheduled_start")
    @classmethod
    def timezone_required(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("scheduled_start must include a timezone")
        return value.astimezone(timezone.utc) if value is not None else None

    @field_validator("selected_providers")
    @classmethod
    def unique_providers(cls, value: List[Provider]) -> List[Provider]:
        if len(set(value)) != len(value):
            raise ValueError("selected_providers must be unique")
        return value

    @model_validator(mode="after")
    def validate_intent(self) -> "CampaignConfiguration":
        mode = ExperimentMode(self.mode)
        self.benchmark.validate_mode(mode)
        providers = {Provider(item) for item in self.selected_providers}
        configs = {Provider(item) for item in self.provider_configs}
        if providers != configs or len(providers) != len(self.provider_configs):
            raise ValueError("provider_configs keys must exactly match selected_providers")
        all_clouds = providers == {Provider.AWS, Provider.AZURE, Provider.GCP}
        if self.context == "standard" and (not all_clouds or mode == ExperimentMode.ACCEPTANCE and not all_clouds):
            raise ValueError("standard contexts require AWS, Azure, and GCP")
        if self.context == "failure_check" and mode != ExperimentMode.ACCEPTANCE:
            raise ValueError("failure_check context is reserved for registered acceptance validation")
        if self.context == "replacement":
            if mode != ExperimentMode.SHORT_STUDY or not self.replacement_context:
                raise ValueError("replacement context requires short_study and replacement metadata")
        elif self.replacement_context is not None:
            raise ValueError("replacement metadata requires context=replacement")
        if mode in {ExperimentMode.CALIBRATION, ExperimentMode.DIAGNOSTIC_DAY} and self.scenario != Scenario.SAME_ZONE:
            raise ValueError("calibration and diagnostic_day require same_zone")
        admission_expected = AdmissionRequirements.expected_for(mode, Scenario(self.scenario))
        for name, expected in admission_expected.items():
            if getattr(self.admission, name) != expected:
                raise ValueError(f"admission.{name} does not match mode/scenario")
        for provider in providers:
            item = self.provider_configs[provider]
            same_region = item.regions.vm_a == item.regions.vm_b
            same_zone = item.zones.vm_a == item.zones.vm_b
            placement = item.placement.kind
            scenario = Scenario(self.scenario)
            valid = {
                Scenario.SAME_ZONE: same_region and same_zone and placement == "none",
                Scenario.CROSS_ZONE: same_region and not same_zone and placement == "none",
                Scenario.PLACEMENT_OPTIMIZATION: same_region and same_zone and placement != "none",
                Scenario.INTER_REGION: not same_region and placement == "none",
            }[scenario]
            if not valid:
                raise ValueError(f"{provider.value} locality/placement does not match {scenario.value}")
            if mode != ExperimentMode.ACCEPTANCE and item.vm_shape != {
                Provider.AWS: "m7i.xlarge", Provider.AZURE: "Standard_D4s_v5", Provider.GCP: "n2-standard-4"
            }[provider]:
                raise ValueError(f"{provider.value} research VM shape does not match the selected design")
            expected_locality = {
                Provider.AWS: {
                    Scenario.SAME_ZONE: (("eu-central-1", "eu-central-1"), ("eu-central-1a", "eu-central-1a")),
                    Scenario.CROSS_ZONE: (("eu-central-1", "eu-central-1"), ("eu-central-1a", "eu-central-1b")),
                    Scenario.PLACEMENT_OPTIMIZATION: (("eu-central-1", "eu-central-1"), ("eu-central-1a", "eu-central-1a")),
                    Scenario.INTER_REGION: (("eu-central-1", "eu-west-2"), ("eu-central-1a", "eu-west-2a")),
                },
                Provider.AZURE: {
                    Scenario.SAME_ZONE: (("germanywestcentral", "germanywestcentral"), ("1", "1")),
                    Scenario.CROSS_ZONE: (("germanywestcentral", "germanywestcentral"), ("1", "2")),
                    Scenario.PLACEMENT_OPTIMIZATION: (("germanywestcentral", "germanywestcentral"), ("1", "1")),
                    Scenario.INTER_REGION: (("germanywestcentral", "uksouth"), ("1", "1")),
                },
                Provider.GCP: {
                    Scenario.SAME_ZONE: (("europe-west3", "europe-west3"), ("europe-west3-a", "europe-west3-a")),
                    Scenario.CROSS_ZONE: (("europe-west3", "europe-west3"), ("europe-west3-a", "europe-west3-b")),
                    Scenario.PLACEMENT_OPTIMIZATION: (("europe-west3", "europe-west3"), ("europe-west3-a", "europe-west3-a")),
                    Scenario.INTER_REGION: (("europe-west3", "europe-west2"), ("europe-west3-a", "europe-west2-a")),
                },
            }[provider][scenario]
            if (tuple(item.regions.model_dump().values()), tuple(item.zones.model_dump().values())) != expected_locality:
                raise ValueError(f"{provider.value} regions/zones do not match the registered {scenario.value} design")
            expected_collocated = provider == Provider.GCP and scenario == Scenario.PLACEMENT_OPTIMIZATION
            if item.placement.collocated != expected_collocated:
                raise ValueError("GCP placement intent must use COLLOCATED only for placement_optimization")
        return self


class ConfigProvenance(StrictModel):
    source_path: str
    sha256: str = Field(pattern=SHA256_PATTERN)
    byte_length: int = Field(gt=0)
    snapshot_path: Optional[str] = None
    resolved_sha256: Optional[str] = Field(default=None, pattern=SHA256_PATTERN)


class VmIntent(StrictModel):
    role: VmRole
    function: str = Field(min_length=1)
    region: str = Field(min_length=1)
    zone: str = Field(min_length=1)
    vm_shape: str = Field(min_length=1)
    image: str = Field(min_length=1)
    connection_user: str = Field(min_length=1)
    os: OperatingSystemIntent = Field(default_factory=OperatingSystemIntent)
    disk: DiskIntent
    network: NetworkIntent
    security: SecurityIntent
    acceleration: AccelerationIntent


class ResolvedObservation(StrictModel):
    schema_version: Literal[2] = 2
    config_id: str = Field(pattern=CONFIG_ID_PATTERN)
    experiment_id: str = Field(pattern=r"^EXP-[0-9]{3,}$")
    mode: ExperimentMode
    context: Literal["standard", "failure_check", "replacement"]
    provider: Provider
    scenario: Scenario
    scheduled_start: Optional[datetime]
    schedule_entry: Evidence[ScheduleEntry]
    measurement_units: List[PhaseConfiguration]
    admission: AdmissionRequirements
    vm_a: VmIntent
    vm_b: VmIntent
    measurement_direction: MeasurementDirection = MeasurementDirection.VM_A_TO_VM_B
    placement: Placement
    benchmark: BenchmarkPlan
    artifact_layout: ArtifactLayout = Field(default_factory=ArtifactLayout)
    provider_config: ProviderConfiguration
    options: CampaignOptions

    @model_validator(mode="after")
    def intent_matches_provider(self) -> "ResolvedObservation":
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
        if self.vm_a != vm_intent(VmRole.VM_A, self.provider_config) or self.vm_b != vm_intent(VmRole.VM_B, self.provider_config):
            raise ValueError("VM intent must match provider configuration")
        if self.scheduled_start is not None and (self.scheduled_start.tzinfo is None or self.scheduled_start.utcoffset() is None):
            raise ValueError("scheduled_start must include a timezone")
        return self


class ResolvedCampaign(StrictModel):
    schema_version: Literal[2] = 2
    config_id: str = Field(pattern=CONFIG_ID_PATTERN)
    experiment_id: str = Field(pattern=r"^EXP-[0-9]{3,}$")
    mode: ExperimentMode
    context: Literal["standard", "failure_check", "replacement"]
    replacement_context: Optional[ReplacementContext] = None
    configuration_role: Literal["experiment", "test"]
    config_source: ConfigProvenance
    resolved_sha256: str = Field(pattern=SHA256_PATTERN)
    scheduled_start: Optional[datetime]
    schedule_entry: Evidence[ScheduleEntry]
    scenario: Scenario
    selected_providers: List[Provider]
    benchmark: BenchmarkPlan
    admission: AdmissionRequirements
    measurement_units: List[PhaseConfiguration]
    launch_blockers: List[str]
    options: CampaignOptions
    observations: List[ResolvedObservation]

    @model_validator(mode="after")
    def observations_match_campaign(self) -> "ResolvedCampaign":
        if (self.context == "replacement") != (self.replacement_context is not None):
            raise ValueError("replacement context metadata must match context")
        providers = [Provider(provider) for provider in self.selected_providers]
        if not providers or len(providers) != len(set(providers)):
            raise ValueError("selected_providers must be non-empty and unique")
        observation_providers = [Provider(item.provider) for item in self.observations]
        if len(observation_providers) != len(set(observation_providers)) or set(observation_providers) != set(providers):
            raise ValueError("observations must match selected_providers exactly once")
        for item in self.observations:
            if (item.config_id, item.experiment_id, item.mode, item.context, item.scenario, item.scheduled_start,
                item.measurement_units, item.admission, item.benchmark, item.options) != (
                self.config_id, self.experiment_id, self.mode, self.context, self.scenario, self.scheduled_start,
                self.measurement_units, self.admission, self.benchmark, self.options):
                raise ValueError("observation shared intent must match campaign")
        return self


def derive_measurement_units(mode: ExperimentMode, benchmark: BenchmarkPlan) -> list[PhaseConfiguration]:
    mode = ExperimentMode(mode)
    if mode in {ExperimentMode.SHORT_STUDY, ExperimentMode.ACCEPTANCE}:
        streams = 4 if mode == ExperimentMode.ACCEPTANCE and benchmark.common_n.state == "pending" else benchmark.common_n.common_n
        return [
            PhaseConfiguration(unit_id=f"{phase.value}", kind="phase", phase=phase,
                               measured_seconds=benchmark.measured_seconds,
                               streams=None if phase == BenchmarkPhase.IDLE_LATENCY else 1 if phase == BenchmarkPhase.SINGLE_FLOW else streams)
            for phase in (BenchmarkPhase.IDLE_LATENCY, BenchmarkPhase.SINGLE_FLOW, BenchmarkPhase.MULTI_FLOW)
        ]
    if mode == ExperimentMode.CALIBRATION:
        orders = (1, 4, 2, 8, 8, 2, 4, 1, 2, 8, 1, 4)
        return [PhaseConfiguration(unit_id=f"calibration-trial-{index:02d}", kind="calibration_trial",
                                   phase=BenchmarkPhase.MULTI_FLOW, measured_seconds=benchmark.measured_seconds,
                                   streams=streams, trial_index=index)
                for index, streams in enumerate(orders, 1)]
    return [PhaseConfiguration(unit_id="diagnostic-day", kind="continuous_day",
                               phase=BenchmarkPhase.MULTI_FLOW, measured_seconds=benchmark.measured_seconds,
                               streams=benchmark.common_n.common_n)]


def vm_intent(role: VmRole, config: ProviderConfiguration) -> VmIntent:
    region, zone = (config.regions.vm_a, config.zones.vm_a) if role == VmRole.VM_A else (config.regions.vm_b, config.zones.vm_b)
    function = "client_traffic_generator" if role == VmRole.VM_A else "server_receiver"
    return VmIntent(role=role, function=function, region=region, zone=zone, vm_shape=config.vm_shape,
                    image=config.image, connection_user=config.connection_user, os=config.os, disk=config.disk,
                    network=config.network, security=config.security, acceleration=config.acceleration)
