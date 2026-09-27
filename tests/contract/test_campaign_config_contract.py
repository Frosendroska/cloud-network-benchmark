import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from cloud_network_benchmark.contracts import CampaignConfiguration


def test_all_executable_configs_validate_with_schema_and_runtime(repository_root: Path) -> None:
    schema = json.loads((repository_root / "specs/001-core-offline-contracts/contracts/campaign-config.schema.json").read_text())
    paths = sorted((repository_root / "configs/experiments").glob("*.yaml")) + sorted((repository_root / "configs/tests").glob("*.yaml"))
    assert len(paths) == 8
    for path in paths:
        data = yaml.safe_load(path.read_text())
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(data)
        runtime = CampaignConfiguration.model_validate(data)
        assert runtime.model_dump(mode="json") == data
