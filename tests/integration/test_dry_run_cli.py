import json
from pathlib import Path

import yaml

from cloud_network_benchmark.cli import main


def test_dry_run_experiment_and_test_are_side_effect_free(repository_root: Path, capsys: object, deny_external_io: None) -> None:
    before = {p: p.stat().st_mtime_ns for p in (repository_root / "results").rglob("*")}
    configs = [
        ("configs/experiments/exp-001-multi-provider.yaml", 3),
        ("configs/experiments/exp-002-cross-zone.yaml", 3),
        ("configs/experiments/exp-003-placement-optimization.yaml", 3),
        ("configs/experiments/exp-004-inter-region.yaml", 3),
        ("configs/tests/exp-900-single-provider.yaml", 1),
        ("configs/tests/exp-901-azure-single-provider.yaml", 1),
        ("configs/tests/exp-902-gcp-single-provider.yaml", 1),
        ("configs/tests/exp-903-three-provider-smoke.yaml", 3),
    ]
    for config, count in configs:
        assert main(["dry-run", "--config", config, "--format", "json"]) == 0
        preview = json.loads(capsys.readouterr().out)
        assert len(preview["children"]) == count
        gcp_children = [item for item in preview["children"] if item["provider"] == "gcp"]
        for child in gcp_children:
            assert child["provider_options"] == {
                "provisioning_model": "STANDARD", "instance_termination_action": "STOP"
            }
    assert main(["dry-run", "--config", "configs/experiments/exp-001-diagnostic-day.yaml", "--format", "human"]) == 0
    assert '"instance_termination_action": "STOP"' in capsys.readouterr().out
    assert {p: p.stat().st_mtime_ns for p in (repository_root / "results").rglob("*")} == before


def test_delete_rejection_is_sanitized_and_side_effect_free(repository_root: Path, tmp_path: Path, capsys: object, deny_external_io: None) -> None:
    before = {p: p.stat().st_mtime_ns for p in (repository_root / "results").rglob("*")}
    config = yaml.safe_load((repository_root / "configs/tests/exp-903-three-provider-smoke.yaml").read_text())
    config["provider_configs"]["gcp"]["provider_options"]["instance_termination_action"] = "DELETE"
    invalid_path = tmp_path / "invalid-gcp.yaml"
    invalid_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    assert main(["validate", "--config", str(invalid_path), "--format", "json"]) != 0
    output = capsys.readouterr()
    assert "DELETE" not in output.out + output.err
    assert {p: p.stat().st_mtime_ns for p in (repository_root / "results").rglob("*")} == before
