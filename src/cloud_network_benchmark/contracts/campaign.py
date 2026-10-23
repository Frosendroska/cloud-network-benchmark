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

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema: Any, handler: Any) -> Dict[str, Any]:
        schema = handler(core_schema)
        schema["allOf"] = [
            {"if": {"properties": {"state": {"const": "pending"}}, "required": ["state"]},
             "then": {"properties": {"common_n": {"type": "null"}, "source_campaign_id": {"type": "null"},
                                       "source_sha256": {"type": "null"}, "plateau_found": {"type": "null"}}}},
            {"if": {"not": {"required": ["state"]}},
             "then": {"properties": {"common_n": {"type": "null"}, "source_campaign_id": {"type": "null"},
                                       "source_sha256": {"type": "null"}, "plateau_found": {"type": "null"}}}},
            {"if": {"properties": {"state": {"const": "frozen"}}, "required": ["state"]},
             "then": {"required": ["common_n", "source_campaign_id", "source_sha256", "plateau_found"],
                      "properties": {"common_n": {"enum": [2, 4, 8]}, "source_campaign_id": {"type": "string", "minLength": 1},
                                     "source_sha256": {"type": "string", "pattern": SHA256_PATTERN}, "plateau_found": {"type": "boolean"}}}},
            {"if": {"properties": {"state": {"const": "frozen"}, "plateau_found": {"const": False}},
                    "required": ["state", "plateau_found"]},
             "then": {"properties": {"common_n": {"const": 8}}}},
        ]
        return schema


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
        if self.kind == "phase":
            if self.trial_index is not None or self.parent_unit_id is not None:
                raise ValueError("phase units cannot declare a trial or parent unit")
            if self.phase == BenchmarkPhase.SINGLE_FLOW and self.streams != 1:
                raise ValueError("single_flow requires exactly one stream")
        if self.kind == "calibration_trial" and self.trial_index is None:
            raise ValueError("calibration trial requires trial_index")
        if self.kind == "calibration_trial" and (self.phase != BenchmarkPhase.MULTI_FLOW or self.streams is None or self.parent_unit_id is not None):
            raise ValueError("calibration trials require multi_flow streams and no parent unit")
        if self.kind != "calibration_trial" and self.trial_index is not None:
            raise ValueError("trial_index is only valid for calibration_trial")
        if self.kind == "continuous_day" and (self.phase != BenchmarkPhase.MULTI_FLOW or self.measured_seconds != 86400 or self.parent_unit_id is not None):
            raise ValueError("continuous_day requires one 86400-second multi_flow unit without a parent")
        if self.kind == "probe_chunk" and self.parent_unit_id is None:
            raise ValueError("probe_chunk requires a parent unit")
        if self.kind == "probe_chunk" and (self.phase != BenchmarkPhase.MULTI_FLOW or self.parent_unit_id == self.unit_id):
            raise ValueError("probe_chunk must reference a distinct retained multi_flow parent unit")
        if self.kind == "probe_chunk" and self.measured_seconds > 300:
            raise ValueError("planned probe chunks must not exceed 300 seconds")
        return self

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema: Any, handler: Any) -> Dict[str, Any]:
        schema = handler(core_schema)
        schema.setdefault("allOf", []).append({
            "if": {"properties": {"kind": {"const": "probe_chunk"}}, "required": ["kind"]},
            "then": {"properties": {"measured_seconds": {"maximum": 300}}},
        })
        return schema


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
            option_values = self.provider_options["provisioning_model"] == "STANDARD" and self.provider_options["instance_termination_action"] == "STOP"
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
        _validate_protocol_scope(mode, self.context, self.replacement_context, providers, self.scenario,
                                 complete_campaign=True)
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
            if item.vm_shape != {
                Provider.AWS: "m7i.xlarge", Provider.AZURE: "Standard_D4s_v5", Provider.GCP: "n2-standard-4"
            }[provider]:
                raise ValueError(f"{provider.value} VM shape does not match the selected design")
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

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema: Any, handler: Any) -> Dict[str, Any]:
        schema = handler(core_schema)
        mode_specs = {
            "acceptance": {"measured_seconds": 10, "pause_seconds": 10, "probe_chunk_seconds": None},
            "short_study": {"measured_seconds": 60, "pause_seconds": 10, "probe_chunk_seconds": None},
            "calibration": {"measured_seconds": 60, "pause_seconds": 10, "probe_chunk_seconds": None},
            "diagnostic_day": {"measured_seconds": 86400, "pause_seconds": 0, "probe_chunk_seconds": 300},
        }
        schema["allOf"] = [
            {"if": {"properties": {"mode": {"const": mode}}, "required": ["mode"]},
             "then": {"properties": {"benchmark": {"properties": {
                 "measured_seconds": {"const": values["measured_seconds"]},
                 "pause_seconds": {"const": values["pause_seconds"]},
                 "probe_chunk_seconds": {"type": "null"} if values["probe_chunk_seconds"] is None else {"const": values["probe_chunk_seconds"]},
             }}}}}
            for mode, values in mode_specs.items()
        ]
        _add_protocol_scope_schema(schema)
        return schema


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
    schema_version: Literal[2]
    config_id: str = Field(pattern=CONFIG_ID_PATTERN)
    experiment_id: str = Field(pattern=r"^EXP-[0-9]{3,}$")
    mode: ExperimentMode
    context: Literal["standard", "failure_check", "replacement"]
    replacement_context: Optional[ReplacementContext] = None
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
        _validate_resolved_protocol(
            config_id=self.config_id, experiment_id=self.experiment_id, mode=self.mode, context=self.context,
            replacement_context=self.replacement_context, provider_set=[self.provider], scenario=self.scenario,
            observation_provider=Provider(self.provider),
            scheduled_start=self.scheduled_start, schedule_entry=self.schedule_entry.value,
            benchmark=self.benchmark, measurement_units=self.measurement_units, admission=self.admission,
        )
        expected_layout = ArtifactLayout.for_mode(self.mode, self.measurement_units)
        if self.artifact_layout.mode is None:
            object.__setattr__(self, "artifact_layout", expected_layout)
        elif self.artifact_layout != expected_layout:
            raise ValueError("artifact_layout must be derived from observation mode and units")
        return self

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema: Any, handler: Any) -> Dict[str, Any]:
        return _resolved_protocol_schema(handler(core_schema))


class ResolvedCampaign(StrictModel):
    schema_version: Literal[2]
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
        providers = [Provider(provider) for provider in self.selected_providers]
        if not providers or len(providers) != len(set(providers)):
            raise ValueError("selected_providers must be non-empty and unique")
        _validate_resolved_protocol(
            config_id=self.config_id, experiment_id=self.experiment_id, mode=self.mode, context=self.context,
            replacement_context=self.replacement_context, provider_set=providers, scenario=self.scenario,
            scheduled_start=self.scheduled_start, schedule_entry=self.schedule_entry.value,
            benchmark=self.benchmark, measurement_units=self.measurement_units, admission=self.admission,
            launch_blockers=self.launch_blockers,
        )
        if self.context == "standard" and set(providers) != {Provider.AWS, Provider.AZURE, Provider.GCP}:
            raise ValueError("standard resolved campaigns require AWS, Azure, and GCP")
        observation_providers = [Provider(item.provider) for item in self.observations]
        if len(observation_providers) != len(set(observation_providers)) or set(observation_providers) != set(providers):
            raise ValueError("observations must match selected_providers exactly once")
        for item in self.observations:
            if (item.config_id, item.experiment_id, item.mode, item.context, item.replacement_context, item.scenario, item.scheduled_start,
                item.schedule_entry, item.measurement_units, item.admission, item.benchmark, item.options) != (
                self.config_id, self.experiment_id, self.mode, self.context, self.replacement_context, self.scenario, self.scheduled_start,
                self.schedule_entry, self.measurement_units, self.admission, self.benchmark, self.options):
                raise ValueError("observation shared intent must match campaign")
        return self

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema: Any, handler: Any) -> Dict[str, Any]:
        return _resolved_protocol_schema(handler(core_schema))


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


def _validate_protocol_scope(
    mode: ExperimentMode,
    context: str,
    replacement_context: Optional[ReplacementContext],
    providers: list[Provider],
    scenario: Scenario,
    *,
    observation_provider: Optional[Provider] = None,
    complete_campaign: bool = False,
) -> None:
    mode = ExperimentMode(mode)
    context = str(getattr(context, "value", context))
    scenario = Scenario(scenario)
    if mode in {ExperimentMode.CALIBRATION, ExperimentMode.DIAGNOSTIC_DAY} and scenario != Scenario.SAME_ZONE:
        raise ValueError("calibration and diagnostic_day require same_zone")
    if complete_campaign and context == "standard" and set(providers) != {Provider.AWS, Provider.AZURE, Provider.GCP}:
        raise ValueError("standard contexts require AWS, Azure, and GCP")
    if context == "failure_check" and mode != ExperimentMode.ACCEPTANCE:
        raise ValueError("failure_check context is reserved for registered acceptance validation")
    if (context == "replacement") != (replacement_context is not None):
        raise ValueError("replacement context metadata must match context")
    if context != "replacement":
        return
    if mode != ExperimentMode.SHORT_STUDY:
        raise ValueError("replacement context requires short_study mode")
    run_provider = replacement_context.replaces_run_id.rsplit("-", 1)[-1]
    try:
        replacement_provider = Provider(run_provider)
    except ValueError as exc:
        raise ValueError("replacement run identity must name a provider") from exc
    if observation_provider is not None:
        if replacement_provider != Provider(observation_provider):
            raise ValueError("replacement provider must match observation provider")
    elif providers and replacement_provider not in {Provider(item) for item in providers}:
        raise ValueError("replacement provider must be selected by the resolved campaign")
    if complete_campaign and set(providers) != {replacement_provider}:
        raise ValueError("replacement context must select only the replaced provider")


def _add_protocol_scope_schema(schema: Dict[str, Any], *, observation: bool = False) -> None:
    same_zone = {"if": {"properties": {"mode": {"enum": ["calibration", "diagnostic_day"]}},
                         "required": ["mode"]},
                 "then": {"properties": {"scenario": {"const": "same_zone"}}}}
    replacement = {
        "if": {"properties": {"context": {"const": "replacement"}}, "required": ["context"]},
        "then": {"required": ["replacement_context"], "properties": {
            "mode": {"const": "short_study"},
            "replacement_context": {
                "type": "object", "additionalProperties": False,
                "required": ["observation_id", "replaces_run_id"],
                "properties": {
                    "observation_id": {"type": "string", "pattern": IDENTIFIER_PATTERN},
                    "replaces_run_id": {"type": "string", "allOf": [
                        {"pattern": IDENTIFIER_PATTERN}, {"pattern": "-(aws|azure|gcp)$"}
                    ]},
                },
            },
        }},
        "else": {"properties": {"replacement_context": {"type": "null"}}},
    }
    failure_check = {"if": {"properties": {"context": {"const": "failure_check"}}, "required": ["context"]},
                     "then": {"properties": {"mode": {"const": "acceptance"}}}}
    schema.setdefault("allOf", []).extend([same_zone, replacement, failure_check])
    if observation:
        # Provider-specific suffix validation is emitted as one conditional per provider.
        for provider in ("aws", "azure", "gcp"):
            schema["allOf"].append({
                "if": {"properties": {"provider": {"const": provider}, "context": {"const": "replacement"}},
                      "required": ["provider", "context"]},
                "then": {"properties": {"replacement_context": {"properties": {
                    "replaces_run_id": {"pattern": f"-{provider}$"}
                }}}},
            })
    else:
        schema["allOf"].append({
            "if": {"not": {"properties": {"context": {"enum": ["failure_check", "replacement"]}},
                             "required": ["context"]}},
            "then": {"properties": {"selected_providers": {
                "minItems": 3, "maxItems": 3,
                "allOf": [{"contains": {"const": provider}} for provider in ("aws", "azure", "gcp")],
            }}}
        })
        for provider in ("aws", "azure", "gcp"):
            schema["allOf"].append({
                "if": {"properties": {"context": {"const": "replacement"}, "replacement_context": {
                    "properties": {"replaces_run_id": {"pattern": f"-{provider}$"}}, "required": ["replaces_run_id"]}},
                      "required": ["context", "replacement_context"]},
                "then": {"properties": {"selected_providers": {"minItems": 1, "maxItems": 1,
                                                                      "contains": {"const": provider}}}},
            })


def _validate_resolved_protocol(
    *, config_id: str, experiment_id: str, mode: ExperimentMode, context: str,
    replacement_context: Optional[ReplacementContext], provider_set: list[Provider], scenario: Scenario,
    observation_provider: Optional[Provider] = None,
    scheduled_start: Optional[datetime], schedule_entry: Optional[ScheduleEntry], benchmark: BenchmarkPlan,
    measurement_units: list[PhaseConfiguration], admission: AdmissionRequirements,
    launch_blockers: Optional[list[str]] = None,
) -> None:
    normalized_mode = ExperimentMode(mode)
    normalized_context = str(getattr(context, "value", context))
    benchmark.validate_mode(normalized_mode)
    _validate_protocol_scope(normalized_mode, context, replacement_context, provider_set, scenario,
                             observation_provider=observation_provider,
                             complete_campaign=observation_provider is None)
    if measurement_units != derive_measurement_units(normalized_mode, benchmark):
        raise ValueError("resolved measurement units must be derived from benchmark mode and N state")
    for name, expected in AdmissionRequirements.expected_for(normalized_mode, scenario).items():
        if getattr(admission, name) != expected:
            raise ValueError(f"resolved identity admission.{name} must match mode and scenario")
    if scheduled_start is not None and (scheduled_start.tzinfo is None or scheduled_start.utcoffset() is None):
        raise ValueError("resolved scheduled_start must include a timezone")
    if schedule_entry is not None:
        if schedule_entry.config_id != config_id or schedule_entry.experiment_id != experiment_id:
            raise ValueError("schedule entry identity must match resolved config")
        if schedule_entry.scheduled_start != scheduled_start:
            raise ValueError("schedule entry time must match resolved scheduled_start")
        if schedule_entry.priority != admission.priority:
            raise ValueError("schedule priority must match resolved admission")
        expected_replacement = replacement_context.replaces_run_id if replacement_context else None
        if schedule_entry.replaces_run_id != expected_replacement:
            raise ValueError("schedule replacement link must match resolved context")
    if launch_blockers is not None:
        expected_blockers = ["admission_not_evaluated"]
        if scheduled_start is None or (admission.requires_schedule and schedule_entry is None):
            expected_blockers.append("schedule_pending")
        if benchmark.common_n.state == "pending" and normalized_mode in {ExperimentMode.SHORT_STUDY, ExperimentMode.DIAGNOSTIC_DAY}:
            expected_blockers.append("calibration_pending")
        if launch_blockers != expected_blockers:
            raise ValueError("resolved launch blockers must preserve pending admission, schedule and calibration state")


def _unit_json_schema(
    unit_id: str, kind: str, phase: str, duration: int, *,
    streams: object = None, trial_index: Optional[int] = None, parent_unit_id: Optional[str] = None,
) -> Dict[str, Any]:
    properties: Dict[str, Any] = {
        "unit_id": {"const": unit_id}, "kind": {"const": kind}, "phase": {"const": phase},
        "measured_seconds": {"const": duration},
    }
    required = ["unit_id", "kind", "phase", "measured_seconds"]
    if streams is None:
        properties["streams"] = {"type": "null"}
    else:
        properties["streams"] = streams if isinstance(streams, dict) else {"const": streams}
        required.append("streams")
    if trial_index is None:
        properties["trial_index"] = {"type": "null"}
    else:
        properties["trial_index"] = {"const": trial_index}
        required.append("trial_index")
    if parent_unit_id is None:
        properties["parent_unit_id"] = {"type": "null"}
    else:
        properties["parent_unit_id"] = {"const": parent_unit_id}
        required.append("parent_unit_id")
    return {"type": "object", "properties": properties, "required": required}


def _resolved_protocol_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    _add_protocol_scope_schema(schema, observation="provider" in schema.get("properties", {}))
    mode_values = {
        "acceptance": (10, 10, None), "short_study": (60, 10, None),
        "calibration": (60, 10, None), "diagnostic_day": (86400, 0, 300),
    }
    schema_constraints: list[Dict[str, Any]] = []
    for mode, (duration, pause, chunk_seconds) in mode_values.items():
        if mode in {"acceptance", "short_study"}:
            n_streams: object = {"anyOf": [{"type": "null"}, {"enum": [2, 4, 8]}]}
            units = [
                _unit_json_schema("idle_latency", "phase", "idle_latency", duration),
                _unit_json_schema("single_flow", "phase", "single_flow", duration, streams=1),
                _unit_json_schema("multi_flow", "phase", "multi_flow", duration, streams=n_streams),
            ]
            unit_array: Dict[str, Any] = {"minItems": 3, "maxItems": 3, "prefixItems": units, "items": False}
            n_index = 2
        elif mode == "calibration":
            streams_order = (1, 4, 2, 8, 8, 2, 4, 1, 2, 8, 1, 4)
            units = [_unit_json_schema(f"calibration-trial-{index:02d}", "calibration_trial", "multi_flow", duration,
                                       streams=streams, trial_index=index)
                     for index, streams in enumerate(streams_order, 1)]
            unit_array = {"minItems": 12, "maxItems": 12, "prefixItems": units, "items": False}
            n_index = -1
        else:
            streams_schema = {"anyOf": [{"type": "null"}, {"enum": [2, 4, 8]}]}
            units = [_unit_json_schema("diagnostic-day", "continuous_day", "multi_flow", duration, streams=streams_schema)]
            chunk = {
                "type": "object", "required": ["unit_id", "kind", "phase", "measured_seconds", "parent_unit_id"],
                "properties": {"unit_id": {"type": "string", "minLength": 1}, "kind": {"const": "probe_chunk"},
                               "phase": {"const": "multi_flow"}, "measured_seconds": {"type": "integer", "minimum": 1, "maximum": 300},
                               "parent_unit_id": {"const": "diagnostic-day"}, "streams": {"type": "null"}, "trial_index": {"type": "null"}},
            }
            unit_array = {"minItems": 1, "prefixItems": units, "items": chunk}
            n_index = 0
        mode_condition = {
            "if": {"properties": {"mode": {"const": mode}}, "required": ["mode"]},
            "then": {"properties": {
                "benchmark": {"properties": {
                    "measured_seconds": {"const": duration}, "pause_seconds": {"const": pause},
                    "probe_chunk_seconds": {"type": "null"} if chunk_seconds is None else {"const": chunk_seconds},
                }},
                "measurement_units": unit_array,
            }},
        }
        schema_constraints.append(mode_condition)
        for scenario, priority in (("same_zone", "P0"), ("cross_zone", "P2"),
                                  ("placement_optimization", "P1"), ("inter_region", "P3")):
            scheduled = mode in {"short_study", "calibration", "diagnostic_day"}
            admission_values = {
                "priority": priority,
                "lifetime_cap_seconds": 1200 if mode in {"acceptance", "short_study"} else 1800 if mode == "calibration" else 87600,
                "requires_common_n": mode in {"short_study", "diagnostic_day"},
                "requires_schedule": scheduled,
                "requires_primary_review": scheduled and priority in {"P2", "P3"},
                "requires_expiry": True,
            }
            schema_constraints.append({
                "if": {"allOf": [mode_condition["if"], {"properties": {"scenario": {"const": scenario}}, "required": ["scenario"]}]},
                "then": {"properties": {"admission": {"properties": {
                    key: {"const": value} for key, value in admission_values.items()
                }}}},
            })
        if n_index >= 0:
            stream_value_index = n_index
            # Runtime defaults an omitted CommonNState.state to pending.
            pending_or_omitted = {"anyOf": [
                {"properties": {"benchmark": {"properties": {"common_n": {"properties": {"state": {"const": "pending"}}, "required": ["state"]}}}}},
                {"properties": {"benchmark": {"properties": {"common_n": {"not": {"required": ["state"]}}}}}},
            ]}
            pending_stream = 4 if mode == "acceptance" else None
            pending_constraint = {
                "if": {"allOf": [mode_condition["if"], pending_or_omitted]},
                "then": {"properties": {"measurement_units": {"prefixItems": [
                    {} for _ in range(stream_value_index)
                ] + [{"properties": {"streams": {"const": pending_stream}}}]}}},
            }
            schema_constraints.append(pending_constraint)
            for common_n in (2, 4, 8):
                stream_constraint = {
                    "if": {"allOf": [
                        mode_condition["if"],
                        {"properties": {"benchmark": {
                            "required": ["common_n"],
                            "properties": {"common_n": {
                                "properties": {"state": {"const": "frozen"}, "common_n": {"const": common_n}},
                                "required": ["state", "common_n"],
                            }},
                        }}}
                    ]},
                    "then": {"properties": {"measurement_units": {"prefixItems": [
                        {} for _ in range(stream_value_index)
                    ] + [{"properties": {"streams": {"const": common_n}}}]}}},
                }
                schema_constraints.append(stream_constraint)
    schema.setdefault("allOf", []).extend(schema_constraints)
    return schema


def vm_intent(role: VmRole, config: ProviderConfiguration) -> VmIntent:
    region, zone = (config.regions.vm_a, config.zones.vm_a) if role == VmRole.VM_A else (config.regions.vm_b, config.zones.vm_b)
    function = "client_traffic_generator" if role == VmRole.VM_A else "server_receiver"
    return VmIntent(role=role, function=function, region=region, zone=zone, vm_shape=config.vm_shape,
                    image=config.image, connection_user=config.connection_user, os=config.os, disk=config.disk,
                    network=config.network, security=config.security, acceleration=config.acceleration)
