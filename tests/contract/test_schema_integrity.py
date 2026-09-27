import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from cloud_network_benchmark.contracts import CampaignConfiguration
from cloud_network_benchmark.manifests import StaticGitProvenance, initialize_campaign


def test_all_schemas_are_valid_draft_2020_12(repository_root: Path) -> None:
    for path in (repository_root / "specs/001-core-offline-contracts/contracts").glob("*.schema.json"):
        Draft202012Validator.check_schema(json.loads(path.read_text()))


def test_runtime_serialization_validates_against_published_schemas(repository_root: Path, tmp_path: Path, frozen_clock: object, deterministic_token: object) -> None:
    contracts = repository_root / "specs/001-core-offline-contracts/contracts"
    config_data = yaml.safe_load((repository_root / "configs/tests/exp-900-single-provider.yaml").read_text())
    serialized = CampaignConfiguration.model_validate(config_data).model_dump(mode="json")
    Draft202012Validator(json.loads((contracts / "campaign-config.schema.json").read_text()), format_checker=FormatChecker()).validate(serialized)
    initialize_campaign(repository_root / "configs/tests/exp-900-single-provider.yaml", repository_root, tmp_path / "results", frozen_clock, deterministic_token, StaticGitProvenance("a" * 40), StaticGitProvenance("b" * 40))
    for path in (tmp_path / "results/manifests").glob("*.json"):
        data = json.loads(path.read_text())
        name = "child-run-manifest.schema.json" if data["manifest_type"] == "child_run" else "campaign-manifest.schema.json"
        Draft202012Validator(json.loads((contracts / name).read_text()), format_checker=FormatChecker()).validate(data)
