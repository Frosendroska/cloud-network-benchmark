import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from cloud_network_benchmark.contracts import CampaignConfiguration


def test_config_validates_with_schema_and_runtime(repository_root: Path) -> None:
    data = yaml.safe_load((repository_root / "configs/experiments/exp-001-multi-provider.yaml").read_text())
    schema = json.loads((repository_root / "specs/001-core-offline-contracts/contracts/campaign-config.schema.json").read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(data)
    runtime = CampaignConfiguration.model_validate(data)
    assert runtime.model_dump(mode="json") == data
