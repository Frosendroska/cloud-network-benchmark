import json
from pathlib import Path

import pytest
import yaml

from cloud_network_benchmark.cli import main


def test_validate_and_resolve_without_side_effects(repository_root: Path, tmp_path: Path, capsys: object) -> None:
    before = set((repository_root / "results").rglob("*"))
    assert main(["validate", "--config", "configs/experiments/exp-001-multi-provider.yaml", "--format", "json"]) == 0
    validation = json.loads(capsys.readouterr().out)
    assert validation["valid"] is True
    assert validation["configuration_role"] == "experiment"
    assert validation["scenario"] == "same_zone"
    assert validation["scheduled_start"] is None
    assert validation["config_id"] == "exp-001-short"
    assert validation["admission"]["priority"] == "P0"
    assert validation["schedule_evidence"] == {"state":"pending", "row_sha256":None}
    assert validation["validation_status"]["data_validity"] == "not_validated"
    assert "schedule_pending" in validation["launch_blockers"]
    assert len(validation["config_sha256"]) == 64
    assert len(validation["children"]) == 3
    assert main(["resolve", "--config", "configs/tests/exp-900-single-provider.yaml", "--format", "json"]) == 0
    resolved = json.loads(capsys.readouterr().out)
    assert resolved["configuration_role"] == "test"
    assert resolved["config_id"] == "exp-900-acceptance"
    assert set((repository_root / "results").rglob("*")) == before


def test_human_validate_and_resolve_show_mode_units_identity_and_pending_evidence(repository_root: Path, capsys: object) -> None:
    for command in ("validate", "resolve"):
        assert main([command, "--config", "configs/tests/exp-903-three-provider-smoke.yaml", "--format", "human"]) == 0
        output = capsys.readouterr().out
        assert "Config ID: exp-903-acceptance" in output
        assert "Mode/context: acceptance / standard" in output
        assert "Schedule row: pending" in output
        assert "Admission: priority P0" in output
        assert "Validation status: not_validated / not_evaluated" in output
        assert "4 streams" in output


def test_init_reports_identity_admission_and_pending_validation_without_cloud_actions(
    repository_root: Path, tmp_path: Path, capsys: object,
) -> None:
    output_root = tmp_path / "results"
    assert main(["init", "--config", str(repository_root / "configs/tests/exp-900-single-provider.yaml"),
                 "--results-root", str(output_root), "--format", "json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["config_id"] == "exp-900-acceptance"
    assert result["mode"] == "acceptance" and result["context"] == "failure_check"
    assert result["schedule_evidence"]["state"] == "pending"
    assert result["validation_status"]["data_validity"] == "not_validated"
    assert output_root.exists()


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
        assert payload["error"]["field_path"] == "config.config_id"
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.parametrize("command", ["validate", "resolve", "dry-run"])
@pytest.mark.parametrize("output_format", ["human", "json"])
def test_validation_errors_do_not_echo_rejected_secret_values(
    repository_root: Path, capsys: object, command: str, output_format: str
) -> None:
    sentinel = "TOP-SECRET-SENTINEL-4319"
    path = repository_root / "configs/tests/invalid-secret-temporary.yaml"
    data = yaml.safe_load((repository_root / "configs/tests/exp-900-single-provider.yaml").read_text())
    data.update({"private_key": sentinel, "credential": sentinel, "environment": sentinel, "stdin": sentinel})
    try:
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        assert main([command, "--config", str(path), "--format", output_format]) == 3
        error = capsys.readouterr().err
        assert sentinel not in error
        assert "private_key" in error
        if output_format == "json":
            payload = json.loads(error)["error"]
            assert payload["field_path"].startswith("config.")
            assert payload["details"]["issues"]
    finally:
        path.unlink(missing_ok=True)
