from pathlib import Path

import pytest

from cloud_network_benchmark.config import load_campaign
from cloud_network_benchmark.errors import ValidationError


def test_loads_exact_bytes_and_role(repository_root: Path) -> None:
    path = repository_root / "configs/tests/exp-900-single-provider.yaml"
    config, source, provenance, role = load_campaign(path, repository_root)
    assert role == "test"
    assert config.experiment_id == "EXP-900"
    assert provenance.byte_length == len(source)
    assert len(provenance.sha256) == 64


def test_rejects_duplicate_keys(repository_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    path = root / "configs/tests/bad.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("schema_version: 1\nschema_version: 1\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="duplicate YAML key"):
        load_campaign(path, root)


def test_rejects_outside_canonical_roots(repository_root: Path, tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValidationError, match="configs/experiments"):
        load_campaign(path, repository_root)
