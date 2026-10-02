import json
from pathlib import Path

from cloud_network_benchmark.cli import main


def test_validate_and_resolve_without_side_effects(repository_root: Path, tmp_path: Path, capsys: object) -> None:
    before = set((repository_root / "results").rglob("*"))
    assert main(["validate", "--config", "configs/experiments/exp-001-multi-provider.yaml", "--format", "json"]) == 0
    validation = json.loads(capsys.readouterr().out)
    assert validation["valid"] is True
    assert validation["configuration_role"] == "experiment"
    assert validation["scenario"] == "same_zone"
    assert validation["scheduled_start"]
    assert len(validation["source_sha256"]) == 64
    assert validation["child_count"] == 3
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


def test_malformed_dry_run_has_stable_human_and_json_errors(repository_root: Path, capsys: object) -> None:
    path = repository_root / "configs/tests/invalid-temporary.yaml"
    try:
        path.write_bytes((repository_root / "tests/fixtures/configs/invalid/malformed-id.yaml").read_bytes())
        assert main(["dry-run", "--config", str(path), "--format", "human"]) == 3
        assert "Error [validation_error]" in capsys.readouterr().err
        assert main(["dry-run", "--config", str(path), "--format", "json"]) == 3
        payload = json.loads(capsys.readouterr().err)
        assert payload["error"]["error_code"] == "validation_error"
        assert payload["error"]["field_path"] == "config"
    finally:
        path.unlink(missing_ok=True)
