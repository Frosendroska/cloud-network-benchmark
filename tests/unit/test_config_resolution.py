from pathlib import Path

import pytest
import yaml

from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.errors import ValidationError


PRIMARY_MATRIX = {
    "exp-001-multi-provider.yaml": ("same_zone", "m7i.xlarge", "Standard_D4s_v5", "n2-standard-4"),
    "exp-002-cross-zone.yaml": ("cross_zone", "m7i.xlarge", "Standard_D4s_v5", "n2-standard-4"),
    "exp-003-placement-optimization.yaml": ("placement_optimization", "m7i.xlarge", "Standard_D4s_v5", "n2-standard-4"),
    "exp-004-inter-region.yaml": ("inter_region", "m7i.xlarge", "Standard_D4s_v5", "n2-standard-4"),
}

SMOKE_MATRIX = {
    "exp-900-single-provider.yaml": (["aws"], ["t3.small"]),
    "exp-901-azure-single-provider.yaml": (["azure"], ["Standard_B1s"]),
    "exp-902-gcp-single-provider.yaml": (["gcp"], ["e2-micro"]),
    "exp-903-three-provider-smoke.yaml": (["aws", "azure", "gcp"], ["t3.small", "Standard_B1s", "e2-micro"]),
}


def test_resolves_one_observation_per_explicit_provider(repository_root: Path) -> None:
    resolved, _ = resolve_campaign(repository_root / "configs/experiments/exp-001-multi-provider.yaml", repository_root)
    assert resolved.selected_providers == ["aws", "azure", "gcp"]
    assert [item.provider for item in resolved.observations] == ["aws", "azure", "gcp"]
    assert all(item.measurement_direction == "vm_a_to_vm_b" for item in resolved.observations)
    assert all(item.vm_a.role == "vm_a" and item.vm_b.role == "vm_b" for item in resolved.observations)


@pytest.mark.parametrize(("filename", "expected"), PRIMARY_MATRIX.items())
def test_primary_experiment_matrix(repository_root: Path, filename: str, expected: tuple[str, str, str, str]) -> None:
    scenario, aws_shape, azure_shape, gcp_shape = expected
    resolved, _ = resolve_campaign(repository_root / "configs/experiments" / filename, repository_root)
    assert resolved.scenario == scenario
    assert resolved.selected_providers == ["aws", "azure", "gcp"]
    assert [item.provider_config.vm_shape for item in resolved.observations] == [aws_shape, azure_shape, gcp_shape]
    assert all(item.benchmark.idle_latency.duration_seconds == 60 for item in resolved.observations)


@pytest.mark.parametrize(("filename", "expected"), SMOKE_MATRIX.items())
def test_smoke_campaign_matrix(repository_root: Path, filename: str, expected: tuple[list[str], list[str]]) -> None:
    providers, shapes = expected
    resolved, _ = resolve_campaign(repository_root / "configs/tests" / filename, repository_root)
    assert resolved.selected_providers == providers
    assert [item.provider_config.vm_shape for item in resolved.observations] == shapes
    assert all(item.benchmark.idle_latency.duration_seconds == 10 for item in resolved.observations)
    assert all(item.benchmark.multi_flow.parameters["upload_streams"] == 1 for item in resolved.observations)


def test_primary_matrix_placement_and_inter_region_details(repository_root: Path) -> None:
    placement, _ = resolve_campaign(repository_root / "configs/experiments/exp-003-placement-optimization.yaml", repository_root)
    assert [item.placement.kind for item in placement.observations] == [
        "cluster_placement_group", "proximity_placement_group", "compact_placement_policy"
    ]
    inter_region, _ = resolve_campaign(repository_root / "configs/experiments/exp-004-inter-region.yaml", repository_root)
    assert [(item.vm_a.region, item.vm_b.region) for item in inter_region.observations] == [
        ("eu-central-1", "eu-west-2"),
        ("germanywestcentral", "uksouth"),
        ("europe-west3", "europe-west2"),
    ]
    assert inter_region.benchmark.multi_flow.enabled is False


def test_provider_native_spot_options_are_preserved(repository_root: Path, tmp_path: Path) -> None:
    data = yaml.safe_load((repository_root / "configs/tests/exp-903-three-provider-smoke.yaml").read_text())
    data["provider_configs"]["aws"]["provider_options"]["instance_market_type"] = "spot"
    data["provider_configs"]["azure"]["provider_options"]["priority"] = "Spot"
    data["provider_configs"]["gcp"]["provider_options"]["provisioning_model"] = "SPOT"
    root = tmp_path / "spot"
    path = root / "configs/tests/spot.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    resolved, _ = resolve_campaign(path, root)
    options = [item.provider_config.provider_options for item in resolved.observations]
    assert options[0]["instance_market_type"] == "spot"
    assert options[1]["priority"] == "Spot"
    assert options[2]["provisioning_model"] == "SPOT"


def test_invalid_provider_purchase_option_is_rejected(repository_root: Path, tmp_path: Path) -> None:
    data = yaml.safe_load((repository_root / "configs/tests/exp-900-single-provider.yaml").read_text())
    data["provider_configs"]["aws"]["provider_options"]["instance_market_type"] = "spoot"
    root = tmp_path / "invalid-spot"
    path = root / "configs/tests/invalid.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValidationError, match="instance_market_type"):
        resolve_campaign(path, root)


def test_rejects_selected_provider_mismatch(repository_root: Path, tmp_path: Path) -> None:
    source = repository_root / "configs/tests/exp-900-single-provider.yaml"
    data = yaml.safe_load(source.read_text())
    data["selected_providers"] = ["aws", "gcp"]
    root = tmp_path / "repo"
    path = root / "configs/tests/bad.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValidationError, match="exactly match"):
        resolve_campaign(path, root)


@pytest.mark.parametrize(
    ("scenario", "region_b", "zone_b", "placement_kind", "placement_name"),
    [
        ("same_zone", "eu-central-1", "euc1-az1", "none", None),
        ("cross_zone", "eu-central-1", "euc1-az2", "none", None),
        ("placement_optimization", "eu-central-1", "euc1-az1", "cluster_placement_group", "bench"),
        ("inter_region", "eu-west-2", "euw2-az1", "none", None),
    ],
)
def test_all_scenario_invariants(repository_root: Path, tmp_path: Path, scenario: str, region_b: str, zone_b: str, placement_kind: str, placement_name: str) -> None:
    data = yaml.safe_load((repository_root / "configs/tests/exp-900-single-provider.yaml").read_text())
    data["scenario"] = scenario
    aws = data["provider_configs"]["aws"]
    aws["regions"]["vm_b"] = region_b
    aws["zones"]["vm_b"] = zone_b
    aws["placement"] = {"kind": placement_kind, "name": placement_name}
    root = tmp_path / scenario
    path = root / "configs/tests/campaign.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    resolved, _ = resolve_campaign(path, root)
    assert resolved.scenario == scenario
