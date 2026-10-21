from pathlib import Path

import pytest
from pydantic import ValidationError
from jsonschema import Draft202012Validator
import yaml

from cloud_network_benchmark.contracts import DiagnosticSample, Evidence, ValidationResult
from cloud_network_benchmark.contracts.schema import SCHEMAS, export, schema_bytes


def test_public_schema_set_matches_reviewed_contracts(repository_root: Path) -> None:
    target = repository_root / "specs/003-v2-shared-contracts/contracts"
    assert {path.name for path in target.glob("*.schema.json")} == set(SCHEMAS)
    assert export(target, check=True)
    for filename, model in SCHEMAS.items():
        schema = model.model_json_schema(mode="validation")
        assert "schema_version" in schema.get("required", [])
        with pytest.raises(ValidationError) as error:
            model.model_validate({})
        assert any(item["loc"] == ("schema_version",) for item in error.value.errors())
        assert (target / filename).read_bytes() == schema_bytes(model)


def test_schema_constraints_match_evidence_mode_status_and_numeric_runtime(repository_root: Path) -> None:
    evidence_schema = Evidence[str].model_json_schema()
    validator = Draft202012Validator(evidence_schema)
    assert validator.is_valid({"value": "observed"})
    assert validator.is_valid({"unavailable_reason": "pending"})
    assert not validator.is_valid({})
    assert not validator.is_valid({"value": "observed", "unavailable_reason": "pending"})
    assert not validator.is_valid({"value": "  "})

    acceptance = yaml.safe_load((repository_root / "configs/tests/exp-903-three-provider-smoke.yaml").read_text())
    config_schema = SCHEMAS["campaign-config.schema.json"].model_json_schema()
    assert Draft202012Validator(config_schema).is_valid(acceptance)
    wrong_duration = {**acceptance, "benchmark": {**acceptance["benchmark"], "measured_seconds": 60}}
    assert not Draft202012Validator(config_schema).is_valid(wrong_duration)

    invalid_status = ValidationResult(
        schema_version=2, run_id="sample-aws", mode="acceptance",
        validation_status={"data_validity":"valid", "data_reasons":[],
                           "comparison_eligibility":"eligible", "comparison_reasons":[]},
        evaluated_at="2026-10-21T00:00:00Z",
    ).model_dump(mode="json")
    invalid_status["validation_status"]["data_validity"] = "invalid"
    invalid_status["validation_status"]["data_reasons"] = ["bad"]
    status_validator = Draft202012Validator(SCHEMAS["validation-result.schema.json"].model_json_schema())
    assert not status_validator.is_valid(invalid_status)

    bad_cpu = DiagnosticSample(
        schema_version=2, deployment_id="sample-aws-deployment", vm_role="vm_a",
        sampled_at="2026-10-21T00:00:00Z", monotonic_seconds=1, interval_seconds=1,
        cpu_total_percent={"value":10}, cpu_max_core_percent={"unavailable_reason":"pending"},
        cpu_steal_percent={"unavailable_reason":"pending"}, tcp_retransmissions={"unavailable_reason":"pending"},
        interface_rx_drops={"unavailable_reason":"pending"}, interface_tx_drops={"unavailable_reason":"pending"},
        interface_rx_errors={"unavailable_reason":"pending"}, interface_tx_errors={"unavailable_reason":"pending"},
        rx_bytes={"unavailable_reason":"pending"}, tx_bytes={"unavailable_reason":"pending"},
    ).model_dump(mode="json")
    bad_cpu["cpu_total_percent"]["value"] = 101
    assert not Draft202012Validator(SCHEMAS["diagnostic-sample.schema.json"].model_json_schema()).is_valid(bad_cpu)
