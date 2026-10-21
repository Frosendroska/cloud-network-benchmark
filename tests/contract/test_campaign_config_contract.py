from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cloud_network_benchmark.contracts import CampaignConfiguration


def test_all_ten_configs_are_strict_v2_contracts(repository_root: Path) -> None:
    paths = sorted((repository_root / "configs/experiments").glob("*.yaml")) + sorted((repository_root / "configs/tests").glob("*.yaml"))
    assert len(paths) == 10
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
