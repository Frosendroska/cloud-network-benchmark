from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Tuple

import yaml
from pydantic import ValidationError as PydanticValidationError

from .contracts.campaign import (
    CampaignConfiguration,
    ConfigProvenance,
    ResolvedCampaign,
    ResolvedObservation,
    VmIntent,
)
from .contracts.common import Provider, Scenario, VmRole
from .errors import ValidationError


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False) -> Dict[Any, Any]:
    mapping: Dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValidationError(f"duplicate YAML key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def _role_and_relative_path(path: Path, repository_root: Path) -> Tuple[str, str]:
    resolved = path.resolve()
    roots = {
        "experiment": (repository_root / "configs" / "experiments").resolve(),
        "test": (repository_root / "configs" / "tests").resolve(),
    }
    for role, root in roots.items():
        try:
            resolved.relative_to(root)
            return role, resolved.relative_to(repository_root.resolve()).as_posix()
        except ValueError:
            continue
    raise ValidationError("config must be under configs/experiments or configs/tests", "config")


def load_campaign(path: Path, repository_root: Path) -> Tuple[CampaignConfiguration, bytes, ConfigProvenance, str]:
    role, relative = _role_and_relative_path(path, repository_root)
    try:
        source = path.read_bytes()
    except OSError as exc:
        raise ValidationError(f"cannot read config: {exc}", "config") from exc
    try:
        raw = yaml.load(source, Loader=UniqueKeyLoader)
    except ValidationError:
        raise
    except yaml.YAMLError as exc:
        raise ValidationError(f"invalid YAML: {exc}", "config") from exc
    if not isinstance(raw, dict):
        raise ValidationError("campaign config must be a mapping", "config")
    try:
        config = CampaignConfiguration.model_validate(raw)
    except PydanticValidationError as exc:
        raise ValidationError(str(exc), "config") from exc
    provenance = ConfigProvenance(
        source_path=relative,
        sha256=hashlib.sha256(source).hexdigest(),
        byte_length=len(source),
    )
    return config, source, provenance, role


def _validate_scenario(config: CampaignConfiguration, provider: Provider) -> None:
    item = config.provider_configs[provider]
    same_region = item.regions.vm_a == item.regions.vm_b
    same_zone = item.zones.vm_a == item.zones.vm_b
    placement = item.placement.kind
    scenario = Scenario(config.scenario)
    if scenario == Scenario.SAME_ZONE and not (same_region and same_zone and placement == "none"):
        raise ValidationError(f"{provider} same_zone requires equal regions/zones and placement none")
    if scenario == Scenario.CROSS_ZONE and not (same_region and not same_zone and placement == "none"):
        raise ValidationError(f"{provider} cross_zone requires equal regions, different zones, and placement none")
    if scenario == Scenario.INTER_REGION and not (not same_region and placement == "none"):
        raise ValidationError(f"{provider} inter_region requires different regions and placement none")
    if scenario == Scenario.PLACEMENT_OPTIMIZATION and placement == "none":
        raise ValidationError(f"{provider} placement_optimization requires provider placement")


def resolve_campaign(path: Path, repository_root: Path) -> Tuple[ResolvedCampaign, bytes]:
    config, source, provenance, role = load_campaign(path, repository_root)
    selected = [Provider(value) for value in config.selected_providers]
    if set(selected) != set(config.provider_configs):
        raise ValidationError("selected_providers must exactly match provider_configs keys")
    observations = []
    for provider in selected:
        provider_config = config.provider_configs[provider]
        if Provider(provider_config.provider) != provider:
            raise ValidationError(f"provider discriminator mismatch for {provider}")
        _validate_scenario(config, provider)
        observations.append(
            ResolvedObservation(
                experiment_id=config.experiment_id,
                provider=provider,
                scenario=config.scenario,
                scheduled_start=config.scheduled_start,
                vm_a=VmIntent(
                    role=VmRole.VM_A,
                    function="client_traffic_generator",
                    region=provider_config.regions.vm_a,
                    zone=provider_config.zones.vm_a,
                    vm_shape=provider_config.vm_shape,
                    image=provider_config.image,
                    connection_user=provider_config.connection_user,
                ),
                vm_b=VmIntent(
                    role=VmRole.VM_B,
                    function="server_receiver",
                    region=provider_config.regions.vm_b,
                    zone=provider_config.zones.vm_b,
                    vm_shape=provider_config.vm_shape,
                    image=provider_config.image,
                    connection_user=provider_config.connection_user,
                ),
                placement=provider_config.placement,
                benchmark=config.benchmark,
                provider_config=provider_config,
            )
        )
    return ResolvedCampaign(
        schema_version=1,
        experiment_id=config.experiment_id,
        configuration_role=role,
        config_source=provenance,
        scheduled_start=config.scheduled_start,
        scenario=config.scenario,
        selected_providers=selected,
        benchmark=config.benchmark,
        observations=observations,
    ), source
