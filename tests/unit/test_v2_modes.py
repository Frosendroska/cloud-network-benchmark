from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from cloud_network_benchmark.config import load_campaign, resolve_campaign
from cloud_network_benchmark.contracts.admission import ScheduleEntry
from cloud_network_benchmark.contracts.campaign import CampaignConfiguration, CommonNState, derive_measurement_units
from cloud_network_benchmark.contracts.common import Evidence
from cloud_network_benchmark.errors import ValidationError


def test_ten_configs_cover_four_modes_and_complete_scenario_matrix(repository_root: Path) -> None:
    experiment_paths = sorted((repository_root / "configs/experiments").glob("*.yaml"))
    test_paths = sorted((repository_root / "configs/tests").glob("*.yaml"))
    paths = experiment_paths + test_paths
    assert len(paths) == 10
    configs = [CampaignConfiguration.model_validate(yaml.safe_load(path.read_text())) for path in paths]
    assert len({config.config_id for config in configs}) == 10
    assert {config.mode for config in configs} == {"acceptance", "short_study", "calibration", "diagnostic_day"}
    assert {config.scenario for config in configs if config.mode == "short_study"} == {
        "same_zone", "cross_zone", "placement_optimization", "inter_region"
    }
    for config in configs:
        assert config.schema_version == 2
        assert config.benchmark.full_rtt is True
        assert config.benchmark.probe_bytes == 64 and config.benchmark.probe_mps == 1000
        assert config.benchmark.tcp_congestion_control == "cubic"
        assert config.benchmark.tcp_zerocopy is True and config.benchmark.tcp_buffer_bytes == 131072


def test_mode_unit_resolution_matches_fixed_protocol(repository_root: Path) -> None:
    fixture = yaml.safe_load((repository_root / "tests/fixtures/configs/v2-mode-cases.yaml").read_text())
    for mode, expected in fixture["modes"].items():
        path = repository_root / "configs" / expected["config"]
        resolved, _ = resolve_campaign(path, repository_root)
        assert resolved.mode == mode
        assert len(resolved.measurement_units) == expected["units"]
        assert all(unit.measured_seconds == expected["measured_seconds"] for unit in resolved.measurement_units)
        assert "admission_not_evaluated" in resolved.launch_blockers
        plan = resolved.benchmark.model_dump(mode="json")
        for key, value in fixture["fixed"].items():
            if key not in {"day_pause_seconds", "pause_seconds"}:
                assert plan[key] == value
        assert plan["pause_seconds"] == (fixture["fixed"]["day_pause_seconds"] if mode == "diagnostic_day" else fixture["fixed"]["pause_seconds"])
    calibration, _ = resolve_campaign(repository_root / "configs/experiments/exp-001-calibration.yaml", repository_root)
    assert [unit.streams for unit in calibration.measurement_units] == fixture["calibration_streams"]
    assert [unit.trial_index for unit in calibration.measurement_units] == list(range(1, 13))
    day, _ = resolve_campaign(repository_root / "configs/experiments/exp-001-diagnostic-day.yaml", repository_root)
    assert day.measurement_units[0].kind == "continuous_day"
    assert "calibration_pending" in day.launch_blockers
    study, _ = resolve_campaign(repository_root / "configs/experiments/exp-001-multi-provider.yaml", repository_root)
    assert study.measurement_units[0].streams is None
    acceptance, _ = resolve_campaign(repository_root / "configs/tests/exp-903-three-provider-smoke.yaml", repository_root)
    assert [unit.streams for unit in acceptance.measurement_units] == [None, 1, 4]
    assert acceptance.benchmark.common_n.state == "pending"
    frozen_benchmark = acceptance.benchmark.model_copy(update={
        "common_n": CommonNState(
            state="frozen", common_n=8, source_campaign_id="exp_001_s1",
            source_sha256="a" * 64, plateau_found=True,
        )
    })
    assert [unit.streams for unit in derive_measurement_units(acceptance.mode, frozen_benchmark)] == [None, 1, 8]


def test_all_provider_scenario_intent_is_explicit(repository_root: Path) -> None:
    expected = {
        "exp-001-multi-provider.yaml": "same_zone",
        "exp-002-cross-zone.yaml": "cross_zone",
        "exp-003-placement-optimization.yaml": "placement_optimization",
        "exp-004-inter-region.yaml": "inter_region",
    }
    for filename, scenario in expected.items():
        resolved, _ = resolve_campaign(repository_root / "configs/experiments" / filename, repository_root)
        assert resolved.scenario == scenario
        assert [item.provider for item in resolved.observations] == ["aws", "azure", "gcp"]
        for observation in resolved.observations:
            provider = observation.provider
            assert observation.vm_a.role == "vm_a" and observation.vm_b.role == "vm_b"
            assert observation.vm_a.network == observation.provider_config.network
            assert observation.vm_b.acceleration == observation.provider_config.acceleration
            assert observation.vm_a.region == observation.provider_config.regions.vm_a
            if provider == "gcp" and scenario == "placement_optimization":
                assert observation.placement.collocated is True and observation.placement.max_distance is None
    s4, _ = resolve_campaign(repository_root / "configs/experiments/exp-004-inter-region.yaml", repository_root)
    assert [unit.phase for unit in s4.measurement_units] == ["idle_latency", "single_flow", "multi_flow"]


def test_source_hash_is_exact_resolved_hash_is_semantic(repository_root: Path, tmp_path: Path) -> None:
    source = repository_root / "configs/experiments/exp-001-multi-provider.yaml"
    raw = source.read_bytes()
    _, _, provenance, _ = load_campaign(source, repository_root)
    assert provenance.sha256 == hashlib.sha256(raw).hexdigest()
    root = tmp_path / "repo"
    path = root / "configs/experiments/copy.yaml"
    path.parent.mkdir(parents=True)
    path.write_bytes(raw + b"\n")
    first, _ = resolve_campaign(source, repository_root)
    second, _ = resolve_campaign(path, root)
    assert first.resolved_sha256 == second.resolved_sha256
    assert first.config_source.sha256 != second.config_source.sha256


def test_schedule_entry_must_match_config_identity_and_timestamp(repository_root: Path) -> None:
    path = repository_root / "configs/experiments/exp-001-multi-provider.yaml"
    entry = ScheduleEntry(
        entry_id="slot-1", config_id="exp_001_s1", experiment_id="EXP-001",
        scheduled_start=datetime(2026, 10, 22, tzinfo=timezone.utc), window_label="morning",
        source_path="runs.csv", source_sha256="a" * 64,
        row_sha256=hashlib.sha256(b"slot-1,exp_001_s1").hexdigest(),
        original_header="entry,config", original_row="slot-1,exp_001_s1", columns=["entry", "config"], priority="P0",
    )
    resolved, _ = resolve_campaign(path, repository_root, entry)
    assert resolved.scheduled_start == entry.scheduled_start
    assert resolved.schedule_entry.value == entry
    bad = entry.model_copy(update={"config_id": "exp_002_s2"})
    with pytest.raises(ValidationError, match="different configuration"):
        resolve_campaign(path, repository_root, bad)


@pytest.mark.parametrize("version", [None, 1, 3])
def test_missing_legacy_and_unknown_versions_fail_before_resolution(repository_root: Path, tmp_path: Path, version) -> None:
    data = yaml.safe_load((repository_root / "configs/tests/exp-900-single-provider.yaml").read_text())
    if version is None:
        data.pop("schema_version")
    else:
        data["schema_version"] = version
    root = tmp_path / f"version-{version}"
    path = root / "configs/tests/bad.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValidationError) as error:
        load_campaign(path, root)
    assert error.value.field == "config.schema_version"


def test_secret_material_is_rejected_before_snapshotting(repository_root: Path, tmp_path: Path) -> None:
    data = yaml.safe_load((repository_root / "configs/tests/exp-900-single-provider.yaml").read_text())
    data["provider_configs"]["aws"]["provider_options"]["password"] = "do-not-print-this"
    root = tmp_path / "secret"
    path = root / "configs/tests/bad.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValidationError) as error:
        load_campaign(path, root)
    assert "do-not-print-this" not in str(error.value)
    assert error.value.field.endswith("password")


def test_fixed_settings_pending_n_and_unknown_fields_are_strict(repository_root: Path) -> None:
    data = yaml.safe_load((repository_root / "configs/experiments/exp-001-multi-provider.yaml").read_text())
    data["benchmark"]["measured_seconds"] = 61
    with pytest.raises(ValueError):
        CampaignConfiguration.model_validate(data)
    data = yaml.safe_load((repository_root / "configs/experiments/exp-001-multi-provider.yaml").read_text())
    data["benchmark"]["common_n"]["common_n"] = 4
    with pytest.raises(ValueError):
        CampaignConfiguration.model_validate(data)
    data = yaml.safe_load((repository_root / "configs/experiments/exp-001-multi-provider.yaml").read_text())
    data["injected_secret"] = "ignored? no"
    with pytest.raises(ValueError):
        CampaignConfiguration.model_validate(data)
