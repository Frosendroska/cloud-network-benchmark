from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Tuple

import yaml
from pydantic import ValidationError as PydanticValidationError

from .contracts.campaign import (
    CampaignConfiguration,
    ConfigProvenance,
    ResolvedCampaign,
    ResolvedObservation,
    derive_measurement_units,
    vm_intent,
)
from .contracts.admission import ScheduleEntry
from .contracts.artifacts import ArtifactLayout
from .contracts.common import Evidence, ExperimentMode, Provider, Scenario, VmRole
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


def _field_path(location: Tuple[Any, ...]) -> str:
    return ".".join(["config", *(str(part) for part in location)])


def _safe_validation_issues(exc: PydanticValidationError) -> list[Dict[str, str]]:
    return [
        {
            "field_path": _field_path(tuple(issue["loc"])),
            "message": issue["msg"],
            "error_type": issue["type"],
        }
        for issue in exc.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )
    ]


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
        mark = getattr(exc, "problem_mark", None)
        details = {}
        if mark is not None:
            details = {"line": mark.line + 1, "column": mark.column + 1}
        raise ValidationError("invalid YAML", "config", details) from exc
    if not isinstance(raw, dict):
        raise ValidationError("campaign config must be a mapping", "config")
    version = raw.get("schema_version")
    if version is None:
        raise ValidationError("legacy schema is unsupported; schema_version 2 is required", "config.schema_version", {"reason": "legacy_schema_unsupported"})
    if version != 2:
        raise ValidationError("unsupported schema version", "config.schema_version", {"reason": "unsupported_schema_version"})
    secret_paths = _secret_paths(raw)
    if secret_paths:
        secret_paths.sort(key=lambda item: (0 if "private_key" in item.lower() or "private-key" in item.lower() else 1, item))
        raise ValidationError(
            "secret-bearing configuration is not safe to snapshot", secret_paths[0],
            {"issues": [{"field_path": item, "message": "secret-bearing field is not permitted", "error_type": "secret_field"} for item in secret_paths]},
        )
    try:
        config = CampaignConfiguration.model_validate(raw)
    except PydanticValidationError as exc:
        issues = _safe_validation_issues(exc)
        field = issues[0]["field_path"] if issues else "config"
        raise ValidationError(
            "campaign configuration is invalid",
            field,
            {"issues": issues},
        ) from exc
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
    if scenario == Scenario.PLACEMENT_OPTIMIZATION and not (same_region and same_zone):
        raise ValidationError(
            f"{provider} placement_optimization requires both VMs to share a region and zone"
        )


def _secret_paths(value: Any, prefix: str = "config") -> list[str]:
    found: list[str] = []
    secret_words = ("password", "passwd", "token", "secret", "credential", "private_key", "private-key", "access_key", "secret_key")
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            child = f"{prefix}.{key_text}"
            if any(word in key_text.lower() for word in secret_words):
                found.append(child)
            else:
                found.extend(_secret_paths(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_secret_paths(item, f"{prefix}[{index}]"))
    elif isinstance(value, str) and ("-----BEGIN " in value and "PRIVATE KEY-----" in value):
        found.append(prefix)
    return found


def resolve_campaign(
    path: Path,
    repository_root: Path,
    schedule_entry: ScheduleEntry | None = None,
) -> Tuple[ResolvedCampaign, bytes]:
    config, source, provenance, role = load_campaign(path, repository_root)
    selected = [Provider(value) for value in config.selected_providers]
    if set(selected) != set(config.provider_configs):
        raise ValidationError("selected_providers must exactly match provider_configs keys")
    if schedule_entry is not None:
        if schedule_entry.config_id != config.config_id or schedule_entry.experiment_id != config.experiment_id:
            raise ValidationError("schedule entry references a different configuration", "config.schedule_entry")
        if config.scheduled_start is not None and schedule_entry.scheduled_start != config.scheduled_start:
            raise ValidationError("schedule timestamp does not match configuration", "config.scheduled_start")
        if schedule_entry.priority != config.admission.priority:
            raise ValidationError("schedule priority does not match admission requirements", "config.schedule_entry.priority")
    scheduled_start = config.scheduled_start or (schedule_entry.scheduled_start if schedule_entry else None)
    units = derive_measurement_units(ExperimentMode(config.mode), config.benchmark)
    blockers = ["admission_not_evaluated"]
    if scheduled_start is None or (config.admission.requires_schedule and schedule_entry is None):
        blockers.append("schedule_pending")
    if config.benchmark.common_n.state == "pending" and config.mode in {ExperimentMode.SHORT_STUDY, ExperimentMode.DIAGNOSTIC_DAY}:
        blockers.append("calibration_pending")
    observations = []
    for provider in selected:
        provider_config = config.provider_configs[provider]
        if Provider(provider_config.provider) != provider:
            raise ValidationError(f"provider discriminator mismatch for {provider}")
        _validate_scenario(config, provider)
        observations.append(ResolvedObservation(
            config_id=config.config_id,
            experiment_id=config.experiment_id,
            mode=config.mode,
            context=config.context,
            provider=provider,
            scenario=config.scenario,
            scheduled_start=scheduled_start,
            schedule_entry=Evidence[ScheduleEntry](value=schedule_entry) if schedule_entry else Evidence[ScheduleEntry](unavailable_reason="no executed schedule row supplied"),
            measurement_units=units,
            admission=config.admission,
            vm_a=vm_intent(VmRole.VM_A, provider_config),
            vm_b=vm_intent(VmRole.VM_B, provider_config),
            placement=provider_config.placement,
            benchmark=config.benchmark,
            artifact_layout=ArtifactLayout(),
            provider_config=provider_config,
            options=config.options,
        ))
    intent = {
        "config_id": config.config_id,
        "experiment_id": config.experiment_id,
        "mode": config.mode,
        "context": config.context,
        "replacement_context": config.replacement_context.model_dump(mode="json") if config.replacement_context else None,
        "scenario": config.scenario,
        "selected_providers": selected,
        "scheduled_start": scheduled_start,
        "schedule_entry": schedule_entry.model_dump(mode="json") if schedule_entry else None,
        "benchmark": config.benchmark.model_dump(mode="json"),
        "admission": config.admission.model_dump(mode="json"),
        "measurement_units": [unit.model_dump(mode="json") for unit in units],
        "observations": [item.model_dump(mode="json") for item in observations],
        "options": config.options.model_dump(mode="json"),
    }
    canonical = json.dumps(intent, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    resolved_sha256 = hashlib.sha256(canonical).hexdigest()
    resolved_provenance = ConfigProvenance(**{**provenance.model_dump(), "resolved_sha256": resolved_sha256})
    return ResolvedCampaign(
        schema_version=2,
        config_id=config.config_id,
        experiment_id=config.experiment_id,
        mode=config.mode,
        context=config.context,
        replacement_context=config.replacement_context,
        configuration_role=role,
        config_source=resolved_provenance,
        resolved_sha256=resolved_sha256,
        scheduled_start=scheduled_start,
        schedule_entry=Evidence[ScheduleEntry](value=schedule_entry) if schedule_entry else Evidence[ScheduleEntry](unavailable_reason="no executed schedule row supplied"),
        scenario=config.scenario,
        selected_providers=selected,
        benchmark=config.benchmark,
        admission=config.admission,
        measurement_units=units,
        launch_blockers=blockers,
        options=config.options,
        observations=observations,
    ), source
