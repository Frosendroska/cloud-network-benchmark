import json
from pathlib import Path

from cloud_network_benchmark.cli import main


def test_dry_run_experiment_and_test_are_side_effect_free(repository_root: Path, capsys: object, deny_external_io: None) -> None:
    before = {p: p.stat().st_mtime_ns for p in (repository_root / "results").rglob("*")}
    for config, count in [("configs/experiments/exp-001-multi-provider.yaml", 3), ("configs/tests/exp-900-single-provider.yaml", 1)]:
        assert main(["dry-run", "--config", config, "--format", "json"]) == 0
        assert len(json.loads(capsys.readouterr().out)["children"]) == count
    assert {p: p.stat().st_mtime_ns for p in (repository_root / "results").rglob("*")} == before
