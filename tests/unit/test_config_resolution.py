from pathlib import Path
from datetime import datetime, timezone

import pytest
import yaml

from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.contracts import ResolvedCampaign, ResolvedObservation
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
    assert resolved.options.provisioning_timeout_seconds == 900
    assert all(item.options == resolved.options for item in resolved.observations)


def test_resolved_models_enforce_parent_child_and_vm_role_invariants(repository_root: Path) -> None:
    resolved, _ = resolve_campaign(repository_root / "configs/experiments/exp-001-multi-provider.yaml", repository_root)
    bad_set = resolved.model_dump(mode="json")
    bad_set["selected_providers"] = ["aws"]
    with pytest.raises(ValueError):
        ResolvedCampaign.model_validate(bad_set)
    bad_role = resolved.observations[0].model_dump(mode="json")
    bad_role["vm_a"]["role"] = "vm_b"
    with pytest.raises(ValueError):
        ResolvedObservation.model_validate(bad_role)


def test_resolved_timestamps_are_normalized_to_utc(repository_root: Path, tmp_path: Path) -> None:
    data = yaml.safe_load((repository_root / "configs/tests/exp-900-single-provider.yaml").read_text())
    data["scheduled_start"] = "2026-09-27T14:00:00+02:00"
    root = tmp_path / "offset"
    path = root / "configs/tests/campaign.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    resolved, _ = resolve_campaign(path, root)
    assert resolved.scheduled_start == datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
    assert resolved.observations[0].scheduled_start == resolved.scheduled_start


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
    with pytest.raises(ValidationError) as error:
        resolve_campaign(path, root)
    assert "instance_market_type" in error.value.details["issues"][0]["message"]


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


@pytest.mark.parametrize(
    ("region_b", "zone_b"),
    [("eu-west-2", "euw2-az1"), ("eu-central-1", "euc1-az2")],
)
def test_placement_optimization_requires_shared_region_and_zone(
    repository_root: Path, tmp_path: Path, region_b: str, zone_b: str
) -> None:
    data = yaml.safe_load((repository_root / "configs/tests/exp-900-single-provider.yaml").read_text())
    data["scenario"] = "placement_optimization"
    aws = data["provider_configs"]["aws"]
    aws["regions"]["vm_b"] = region_b
    aws["zones"]["vm_b"] = zone_b
    aws["placement"] = {"kind": "cluster_placement_group", "name": "bench"}
    root = tmp_path / f"{region_b}-{zone_b}"
    path = root / "configs/tests/campaign.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValidationError, match="share a region and zone"):
        resolve_campaign(path, root)


@pytest.mark.parametrize("scenario", ["same_zone", "cross_zone", "placement_optimization"])
def test_s1_through_s3_require_multi_flow(repository_root: Path, tmp_path: Path, scenario: str) -> None:
    filename = {
        "same_zone": "exp-001-multi-provider.yaml",
        "cross_zone": "exp-002-cross-zone.yaml",
        "placement_optimization": "exp-003-placement-optimization.yaml",
    }[scenario]
    data = yaml.safe_load((repository_root / "configs/experiments" / filename).read_text())
    data["benchmark"]["multi_flow"]["enabled"] = False
    data["benchmark"]["multi_flow"]["parameters"] = {}
    root = tmp_path / scenario
    path = root / "configs/experiments/campaign.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValidationError) as error:
        resolve_campaign(path, root)
    assert "require multi_flow" in error.value.details["issues"][0]["message"]


def test_inter_region_may_disable_multi_flow_without_stream_count(repository_root: Path, tmp_path: Path) -> None:
    data = yaml.safe_load((repository_root / "configs/experiments/exp-004-inter-region.yaml").read_text())
    data["benchmark"]["multi_flow"]["parameters"] = {}
    root = tmp_path / "inter-region"
    path = root / "configs/experiments/campaign.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    resolved, _ = resolve_campaign(path, root)
    assert resolved.benchmark.multi_flow.enabled is False
    assert "upload_streams" not in resolved.benchmark.multi_flow.parameters


@pytest.mark.parametrize(
    "fixture",
    [
        "duplicate-key.yaml",
        "malformed-id.yaml",
        "missing-start.yaml",
        "provider-mismatch.yaml",
        "unknown-provider.yaml",
        "unknown-scenario.yaml",
        "contradictory-cross-zone.yaml",
    ],
)
def test_executable_invalid_fixtures_are_rejected(repository_root: Path, tmp_path: Path, fixture: str) -> None:
    source = repository_root / "tests/fixtures/configs/invalid" / fixture
    root = tmp_path / fixture
    path = root / "configs/tests" / fixture
    path.parent.mkdir(parents=True)
    path.write_bytes(source.read_bytes())
    with pytest.raises(ValidationError):
        resolve_campaign(path, root)
