from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.contracts import ResolvedCampaign, ResolvedObservation
from cloud_network_benchmark.errors import ValidationError


PRIMARY_MATRIX = {
    "exp-001-multi-provider.yaml": ("same_zone", "P0"),
    "exp-002-cross-zone.yaml": ("cross_zone", "P2"),
    "exp-003-placement-optimization.yaml": ("placement_optimization", "P1"),
    "exp-004-inter-region.yaml": ("inter_region", "P3"),
}


def test_resolves_one_observation_per_explicit_provider(repository_root: Path) -> None:
    resolved, _ = resolve_campaign(repository_root / "configs/experiments/exp-001-multi-provider.yaml", repository_root)
    assert resolved.selected_providers == ["aws", "azure", "gcp"]
    assert [item.provider for item in resolved.observations] == ["aws", "azure", "gcp"]
    assert all(item.measurement_direction == "vm_a_to_vm_b" for item in resolved.observations)
    assert all(item.vm_a.role == "vm_a" and item.vm_b.role == "vm_b" for item in resolved.observations)
    assert resolved.launch_blockers == ["admission_not_evaluated", "schedule_pending", "calibration_pending"]
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


def test_scheduled_timestamps_are_normalized_to_utc(repository_root: Path, tmp_path: Path) -> None:
    data = yaml.safe_load((repository_root / "configs/tests/exp-900-single-provider.yaml").read_text())
    data["scheduled_start"] = "2026-09-27T14:00:00+02:00"
    root = tmp_path / "offset"
    path = root / "configs/tests/campaign.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    resolved, _ = resolve_campaign(path, root)
    assert resolved.scheduled_start == datetime(2026, 9, 27, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize(("filename", "expected"), PRIMARY_MATRIX.items())
def test_primary_experiment_matrix(repository_root: Path, filename: str, expected: tuple[str, str]) -> None:
    scenario, priority = expected
    resolved, _ = resolve_campaign(repository_root / "configs/experiments" / filename, repository_root)
    assert resolved.scenario == scenario
    assert resolved.admission.priority == priority
    assert resolved.selected_providers == ["aws", "azure", "gcp"]
    assert [item.provider_config.vm_shape for item in resolved.observations] == ["m7i.xlarge", "Standard_D4s_v5", "n2-standard-4"]
    assert [item.phase for item in resolved.measurement_units] == ["idle_latency", "single_flow", "multi_flow"]
    assert all(item.measured_seconds == 60 for item in resolved.measurement_units)


def test_smoke_campaign_matrix(repository_root: Path) -> None:
    cases = {
        "exp-900-single-provider.yaml": (["aws"], ["t3.small"]),
        "exp-901-azure-single-provider.yaml": (["azure"], ["Standard_B1s"]),
        "exp-902-gcp-single-provider.yaml": (["gcp"], ["e2-micro"]),
        "exp-903-three-provider-smoke.yaml": (["aws", "azure", "gcp"], ["t3.small", "Standard_B1s", "e2-micro"]),
    }
    for filename, (providers, shapes) in cases.items():
        resolved, _ = resolve_campaign(repository_root / "configs/tests" / filename, repository_root)
        assert resolved.mode == "acceptance" and resolved.context == "failure_check"
        assert resolved.selected_providers == providers
        assert [item.provider_config.vm_shape for item in resolved.observations] == shapes
        assert [item.measured_seconds for item in resolved.measurement_units] == [10, 10, 10]
        assert [item.streams for item in resolved.measurement_units] == [None, 1, 4]


def test_provider_purchase_options_reject_spot(repository_root: Path, tmp_path: Path) -> None:
    data = yaml.safe_load((repository_root / "configs/tests/exp-903-three-provider-smoke.yaml").read_text())
    data["provider_configs"]["aws"]["provider_options"]["instance_market_type"] = "spot"
    root = tmp_path / "spot"
    path = root / "configs/tests/spot.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValidationError):
        resolve_campaign(path, root)


def test_selected_provider_mismatch_and_secret_rejected(repository_root: Path, tmp_path: Path) -> None:
    data = yaml.safe_load((repository_root / "configs/tests/exp-900-single-provider.yaml").read_text())
    data["selected_providers"] = ["aws", "gcp"]
    root = tmp_path / "repo"
    path = root / "configs/tests/bad.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValidationError):
        resolve_campaign(path, root)


@pytest.mark.parametrize("filename", PRIMARY_MATRIX)
def test_s4_and_every_short_scenario_keep_all_three_phases(repository_root: Path, filename: str) -> None:
    resolved, _ = resolve_campaign(repository_root / "configs/experiments" / filename, repository_root)
    assert [unit.phase for unit in resolved.measurement_units] == ["idle_latency", "single_flow", "multi_flow"]
    assert len(resolved.measurement_units) == 3


@pytest.mark.parametrize(
    "filename",
    [
        "duplicate-key.yaml", "malformed-id.yaml", "missing-start.yaml", "provider-mismatch.yaml",
        "unknown-provider.yaml", "unknown-scenario.yaml", "contradictory-cross-zone.yaml",
    ],
)
def test_migrated_v2_invalid_fixtures_remain_rejected(repository_root: Path, tmp_path: Path, filename: str) -> None:
    source = repository_root / "tests/fixtures/configs/invalid" / filename
    root = tmp_path / filename
    path = root / "configs/tests" / filename
    path.parent.mkdir(parents=True)
    path.write_bytes(source.read_bytes())
    with pytest.raises(ValidationError):
        resolve_campaign(path, root)
