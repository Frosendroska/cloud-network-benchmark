import json
from pathlib import Path

from cloud_network_benchmark.cli import main


def test_validate_and_resolve_without_side_effects(repository_root: Path, tmp_path: Path, capsys: object) -> None:
    before = set((repository_root / "results").rglob("*"))
    assert main(["validate", "--config", "configs/experiments/exp-001-multi-provider.yaml", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True
    assert main(["resolve", "--config", "configs/tests/exp-900-single-provider.yaml", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["configuration_role"] == "test"
    assert set((repository_root / "results").rglob("*")) == before


def test_invalid_config_returns_three(repository_root: Path, tmp_path: Path, capsys: object) -> None:
    path = repository_root / "configs/tests/invalid-temporary.yaml"
    try:
        path.write_text("schema_version: 1\n", encoding="utf-8")
        assert main(["validate", "--config", str(path), "--format", "json"]) == 3
        assert "validation_error" in capsys.readouterr().err
    finally:
        path.unlink(missing_ok=True)
