import os
from pathlib import Path

import pytest
import yaml


def test_every_executable_config_has_a_concrete_thesis_anchor(repository_root: Path) -> None:
    configs = sorted((repository_root / "configs/experiments").glob("*.yaml"))
    configs += sorted((repository_root / "configs/tests").glob("*.yaml"))
    mapping = (repository_root / "configs/README.md").read_text(encoding="utf-8")
    thesis_root = Path(os.environ.get("THESIS_ROOT", repository_root.parent / "Thesis"))
    experiment_index = thesis_root / "experiments/EXPERIMENTS.md"
    if not experiment_index.exists():
        pytest.skip("Thesis repository is not adjacent; set THESIS_ROOT to verify cross-repository anchors")
    thesis_text = experiment_index.read_text(encoding="utf-8")

    for path in configs:
        experiment_id = yaml.safe_load(path.read_text(encoding="utf-8"))["experiment_id"]
        assert f"`{path.relative_to(repository_root / 'configs').as_posix()}` | `{experiment_id}`" in mapping
        assert f"### {experiment_id} " in thesis_text
        assert f"EXPERIMENTS.md#{experiment_id.lower()}-" in mapping
