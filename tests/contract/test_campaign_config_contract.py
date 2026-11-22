from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cloud_network_benchmark.config import load_campaign
from cloud_network_benchmark.campaign_inputs import CONFIGS, _frozen_config
from cloud_network_benchmark.contracts import CampaignConfiguration
from cloud_network_benchmark.errors import ValidationError


def test_all_twelve_configs_are_strict_v2_contracts(repository_root: Path) -> None:
    paths = sorted((repository_root / "configs/experiments").glob("exp-*.yaml")) + sorted((repository_root / "configs/tests").glob("exp-*.yaml"))
    assert len(paths) == 12
    identities = set()
    for path in paths:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        config = CampaignConfiguration.model_validate(data)
        assert config.schema_version == 2
        assert config.config_id not in identities
        identities.add(config.config_id)
        assert set(config.selected_providers) == set(config.provider_configs)
        assert config.admission.hard_budget_usd == 300
        assert config.admission.protected_buffer_usd == 50
        assert config.admission.estimated_cost_usd.value is None
        assert config.scheduled_start is None


@pytest.mark.parametrize(
    ("mode", "duration", "pause", "chunks"),
    [("acceptance", 10, 10, None), ("short_study", 60, 10, None),
     ("calibration", 60, 10, None), ("diagnostic_day", 86400, 0, 300)],
)
def test_fixed_mode_settings_are_runtime_contracts(repository_root: Path, mode: str, duration: int, pause: int, chunks) -> None:
    candidates = sorted((repository_root / "configs/experiments").glob("*.yaml")) + sorted((repository_root / "configs/tests").glob("*.yaml"))
    path = next(path for path in candidates if yaml.safe_load(path.read_text())["mode"] == mode)
    data = yaml.safe_load(path.read_text())
    config = CampaignConfiguration.model_validate(data)
    assert config.benchmark.measured_seconds == duration
    assert config.benchmark.pause_seconds == pause
    assert config.benchmark.probe_chunk_seconds == chunks
    assert config.benchmark.warmup_seconds == 5
    assert config.benchmark.stabilization_seconds == 60


def test_legacy_protocol_fields_unknown_options_and_spot_are_rejected(repository_root: Path) -> None:
    data = yaml.safe_load((repository_root / "configs/experiments/exp-001-multi-provider.yaml").read_text())
    data["benchmark"]["idle_latency"] = {"enabled": True, "test_name": "flent", "duration_seconds": 60}
    with pytest.raises(ValueError):
        CampaignConfiguration.model_validate(data)
    data = yaml.safe_load((repository_root / "configs/tests/exp-900-single-provider.yaml").read_text())
    data["provider_configs"]["aws"]["provider_options"]["instance_market_type"] = "spot"
    with pytest.raises(ValueError):
        CampaignConfiguration.model_validate(data)
    data = yaml.safe_load((repository_root / "configs/tests/exp-900-single-provider.yaml").read_text())
    data["unexpected"] = True
    with pytest.raises(ValueError):
        CampaignConfiguration.model_validate(data)


def test_common_n_requires_source_evidence_and_fallback_consistency(repository_root: Path) -> None:
    data = yaml.safe_load((repository_root / "configs/experiments/exp-001-multi-provider.yaml").read_text())
    data["benchmark"]["common_n"] = {
        "state": "frozen", "common_n": 4, "source_campaign_id": "calibration",
        "source_sha256": "a" * 64, "plateau_found": False,
    }
    with pytest.raises(ValueError, match="fallback common_n=8"):
        CampaignConfiguration.model_validate(data)
    data["benchmark"]["common_n"]["common_n"] = 8
    config = CampaignConfiguration.model_validate(data)
    assert config.benchmark.common_n.common_n == 8


def _northern_acceptance(repository_root: Path, provider: str, region: str, zone: str) -> dict:
    filename = {"aws":"exp-900-single-provider.yaml", "azure":"exp-901-azure-single-provider.yaml", "gcp":"exp-902-gcp-single-provider.yaml"}[provider]
    data = yaml.safe_load((repository_root / "configs/tests" / filename).read_text(encoding="utf-8"))
    item = data["provider_configs"][provider]
    old_region = item["regions"]["vm_a"]
    item["regions"] = {"vm_a": region, "vm_b": region}
    item["zones"] = {"vm_a": zone, "vm_b": zone}
    item["network"]["address_spaces"] = {region: item["network"]["address_spaces"][old_region]}
    item["network"]["subnets"] = {region: item["network"]["subnets"][old_region]}
    return data


@pytest.mark.parametrize(
    ("provider", "region", "zone"),
    [("aws", "eu-north-1", "eu-north-1a"), ("gcp", "europe-north1", "europe-north1-c"), ("azure", "swedencentral", "1")],
)
def test_northern_locality_is_accepted_for_named_same_zone_checks(
    repository_root: Path, provider: str, region: str, zone: str,
) -> None:
    data = _northern_acceptance(repository_root, provider, region, zone)
    config = CampaignConfiguration.model_validate(data)
    assert config.mode == "acceptance"
    assert config.context == "failure_check"
    assert config.scenario == "same_zone"
    assert config.provider_configs[provider].regions.vm_a == region
    assert config.provider_configs[provider].zones.vm_b == zone


@pytest.mark.parametrize(
    ("provider", "region", "zone", "wrong_zone", "wrong_experiment"),
    [
        ("aws", "eu-north-1", "eu-north-1a", "eu-north-1b", "EXP-999"),
        ("gcp", "europe-north1", "europe-north1-c", "europe-north1-b", "EXP-999"),
        ("azure", "swedencentral", "1", "2", "EXP-999"),
    ],
)
def test_northern_locality_rejects_wrong_zone_identity_and_network(
    repository_root: Path, provider: str, region: str, zone: str, wrong_zone: str, wrong_experiment: str,
) -> None:
    data = _northern_acceptance(repository_root, provider, region, zone)
    item = data["provider_configs"][provider]
    item["zones"]["vm_a"] = wrong_zone
    item["zones"]["vm_b"] = wrong_zone
    with pytest.raises(ValueError, match="regions/zones do not match"):
        CampaignConfiguration.model_validate(data)
    item["zones"] = {"vm_a": zone, "vm_b": zone}
    data["experiment_id"] = wrong_experiment
    with pytest.raises(ValueError, match="regions/zones do not match"):
        CampaignConfiguration.model_validate(data)
    data["experiment_id"] = {"aws":"EXP-900", "azure":"EXP-901", "gcp":"EXP-902"}[provider]
    item["network"]["address_spaces"][region] = "10.99.0.0/16" if provider != "gcp" else "10.99.1.0/24"
    item["network"]["subnets"][region] = ["10.99.1.0/24"]
    with pytest.raises(ValueError, match="unexpected network range"):
        CampaignConfiguration.model_validate(data)


@pytest.mark.parametrize(
    ("provider", "region", "zone", "unknown_region", "unknown_zone"),
    [
        ("aws", "eu-north-1", "eu-north-1a", "eu-future-1", "eu-future-1a"),
        ("gcp", "europe-north1", "europe-north1-c", "europe-future1", "europe-future1-a"),
        ("azure", "swedencentral", "1", "futurecentral", "1"),
    ],
)
def test_unknown_region_is_a_structured_validation_error(
    repository_root: Path, tmp_path: Path, provider: str, region: str, zone: str,
    unknown_region: str, unknown_zone: str,
) -> None:
    data = _northern_acceptance(repository_root, provider, region, zone)
    item = data["provider_configs"][provider]
    item["regions"] = {"vm_a": unknown_region, "vm_b": unknown_region}
    item["zones"] = {"vm_a": unknown_zone, "vm_b": unknown_zone}
    item["network"]["address_spaces"] = {unknown_region: item["network"]["address_spaces"][region]}
    item["network"]["subnets"] = {unknown_region: item["network"]["subnets"][region]}
    root = tmp_path / "repo"
    path = root / "configs/tests/unknown-region.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValidationError) as exc_info:
        load_campaign(path, root)
    error = exc_info.value.as_dict()["error"]
    assert error["error_code"] == "validation_error"
    assert error["field_path"] == f"config.provider_configs.{provider}"
    assert any(f"unsupported {provider} region: {unknown_region}" in issue["message"] for issue in error["details"]["issues"])


@pytest.mark.parametrize("filename", [
    "exp-001-multi-provider.yaml", "exp-001-calibration.yaml",
    "exp-001-diagnostic-day.yaml", "exp-003-placement-optimization.yaml",
])
def test_northern_research_configs_resolve_through_normal_entrypoint(repository_root: Path, filename: str) -> None:
    config, _source, _provenance, _role = load_campaign(repository_root / "configs/experiments" / filename, repository_root)
    assert {config.provider_configs[p].regions.vm_a for p in config.selected_providers} == {
        "eu-north-1", "swedencentral", "europe-north1"
    }
    if config.scenario == "placement_optimization":
        assert config.provider_configs["gcp"].placement.collocated is True
        assert config.provider_configs["gcp"].placement.max_distance is None


def test_paired_week_uses_the_same_northern_s1_s3_configs(repository_root: Path) -> None:
    for slot in ("s1", "s3"):
        config, _source, _provenance, _role = load_campaign(repository_root / CONFIGS[slot], repository_root)
        assert config.scenario == ("same_zone" if slot == "s1" else "placement_optimization")
        assert {config.provider_configs[p].regions.vm_a for p in config.selected_providers} == {
            "eu-north-1", "swedencentral", "europe-north1"
        }


def test_northern_research_rejects_unregistered_scenario_and_zone(repository_root: Path) -> None:
    data = yaml.safe_load((repository_root / "configs/experiments/exp-001-multi-provider.yaml").read_text())
    data["scenario"] = "cross_zone"
    with pytest.raises(ValueError):
        CampaignConfiguration.model_validate(data)


def test_northern_s1_s3_acceptance_configs_resolve_without_fallback(repository_root: Path) -> None:
    s1 = yaml.safe_load((repository_root / "configs/tests/exp-903-three-provider-smoke.yaml").read_text())
    CampaignConfiguration.model_validate(s1)
    s3 = yaml.safe_load((repository_root / "configs/experiments/exp-003-placement-optimization.yaml").read_text())
    providers = {name: {"controller_cidr": "203.0.113.4/32"} for name in ("aws", "azure", "gcp")}
    frozen = _frozen_config(s3, "s3_acceptance", {"providers": providers}, "V-S3")
    config = CampaignConfiguration.model_validate(frozen)
    assert config.mode == "acceptance" and config.scenario == "placement_optimization"
    assert config.provider_configs["gcp"].zones.vm_a == "europe-north1-c"
    assert config.provider_configs["gcp"].placement.collocated is True
    assert config.provider_configs["gcp"].placement.max_distance is None


def test_primary_study_rejects_silent_frankfurt_fallback(repository_root: Path) -> None:
    data = yaml.safe_load((repository_root / "configs/experiments/exp-001-multi-provider.yaml").read_text())
    aws = data["provider_configs"]["aws"]
    aws["regions"] = {"vm_a": "eu-central-1", "vm_b": "eu-central-1"}
    aws["zones"] = {"vm_a": "eu-central-1a", "vm_b": "eu-central-1a"}
    aws["network"]["address_spaces"] = {"eu-central-1": "10.60.0.0/16"}
    aws["network"]["subnets"] = {"eu-central-1": ["10.60.1.0/24"]}
    with pytest.raises(ValueError, match="must use the exercised northern regions"):
        CampaignConfiguration.model_validate(data)


def test_northern_s3_rejects_wrong_shape_and_missing_provider_placement(repository_root: Path) -> None:
    data = yaml.safe_load((repository_root / "configs/experiments/exp-003-placement-optimization.yaml").read_text())
    data["provider_configs"]["aws"]["vm_shape"] = "m7i.2xlarge"
    with pytest.raises(ValueError, match="VM shape"):
        CampaignConfiguration.model_validate(data)
    data = yaml.safe_load((repository_root / "configs/experiments/exp-003-placement-optimization.yaml").read_text())
    data["provider_configs"]["azure"]["placement"]["kind"] = "none"
    data["provider_configs"]["azure"]["placement"]["name"] = None
    with pytest.raises(ValueError):
        CampaignConfiguration.model_validate(data)
    data = yaml.safe_load((repository_root / "configs/experiments/exp-001-multi-provider.yaml").read_text())
    data["provider_configs"]["gcp"]["zones"] = {"vm_a": "europe-north1-a", "vm_b": "europe-north1-a"}
    with pytest.raises(ValueError):
        CampaignConfiguration.model_validate(data)
