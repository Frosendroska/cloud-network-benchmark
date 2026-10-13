import json
from pathlib import Path

import yaml
import pytest
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


@pytest.mark.parametrize("scenario", ["same_zone", "cross_zone", "placement_optimization"])
def test_schema_and_runtime_reject_disabled_multi_flow_for_s1_to_s3(repository_root: Path, scenario: str) -> None:
    schema = json.loads((repository_root / "specs/001-core-offline-contracts/contracts/campaign-config.schema.json").read_text())
    data = yaml.safe_load((repository_root / "configs/experiments/exp-001-multi-provider.yaml").read_text())
    data["scenario"] = scenario
    data["benchmark"]["multi_flow"]["enabled"] = False
    data["benchmark"]["multi_flow"]["parameters"] = {}
    assert list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(data))
    with pytest.raises(ValueError):
        CampaignConfiguration.model_validate(data)


def test_schema_and_runtime_accept_disabled_multi_flow_without_streams_for_s4(repository_root: Path) -> None:
    schema = json.loads((repository_root / "specs/001-core-offline-contracts/contracts/campaign-config.schema.json").read_text())
    data = yaml.safe_load((repository_root / "configs/experiments/exp-004-inter-region.yaml").read_text())
    data["benchmark"]["multi_flow"]["parameters"] = {}
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(data)
    assert CampaignConfiguration.model_validate(data).benchmark.multi_flow.parameters == {}


def test_schema_and_runtime_require_positive_streams_when_multi_flow_is_enabled(repository_root: Path) -> None:
    schema = json.loads((repository_root / "specs/001-core-offline-contracts/contracts/campaign-config.schema.json").read_text())
    data = yaml.safe_load((repository_root / "configs/experiments/exp-001-multi-provider.yaml").read_text())
    data["benchmark"]["multi_flow"]["parameters"] = {}
    assert list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(data))
    with pytest.raises(ValueError):
        CampaignConfiguration.model_validate(data)


@pytest.mark.parametrize(
    "path",
    [
        ("provider_configs", "aws", "regions", "vm_a"),
        ("provider_configs", "aws", "regions", "vm_b"),
        ("provider_configs", "aws", "zones", "vm_a"),
        ("provider_configs", "aws", "zones", "vm_b"),
        ("provider_configs", "aws", "vm_shape"),
        ("provider_configs", "aws", "image"),
        ("provider_configs", "aws", "connection_user"),
        ("benchmark", "idle_latency", "test_name"),
        ("provider_configs", "aws", "placement", "name"),
    ],
)
def test_campaign_schema_and_runtime_reject_whitespace_only_required_strings(
    repository_root: Path, path: tuple[str, ...]
) -> None:
    schema = json.loads((repository_root / "specs/001-core-offline-contracts/contracts/campaign-config.schema.json").read_text())
    data = yaml.safe_load((repository_root / "configs/tests/exp-900-single-provider.yaml").read_text())
    if path[-2:] == ("placement", "name"):
        data["scenario"] = "placement_optimization"
        data["provider_configs"]["aws"]["placement"] = {
            "kind": "cluster_placement_group",
            "name": " ",
        }
    else:
        target = data
        for component in path[:-1]:
            target = target[component]
        target[path[-1]] = " \t "

    assert list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(data))
    with pytest.raises(ValueError):
        CampaignConfiguration.model_validate(data)
