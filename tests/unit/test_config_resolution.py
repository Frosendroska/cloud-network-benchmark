from pathlib import Path

import pytest
import yaml

from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.errors import ValidationError


def test_resolves_one_observation_per_explicit_provider(repository_root: Path) -> None:
    resolved, _ = resolve_campaign(repository_root / "configs/experiments/exp-001-multi-provider.yaml", repository_root)
    assert resolved.selected_providers == ["aws", "azure", "gcp"]
    assert [item.provider for item in resolved.observations] == ["aws", "azure", "gcp"]
    assert all(item.measurement_direction == "vm_a_to_vm_b" for item in resolved.observations)
    assert all(item.vm_a.role == "vm_a" and item.vm_b.role == "vm_b" for item in resolved.observations)


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
