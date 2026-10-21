from pathlib import Path

import pytest
from pydantic import ValidationError
from jsonschema import Draft202012Validator
import yaml

from cloud_network_benchmark.contracts import CommonNState, DiagnosticSample, Evidence, ValidationResult
from cloud_network_benchmark.contracts.common import ValidationStatus
from cloud_network_benchmark.contracts.measurements import TimingGap
from cloud_network_benchmark.contracts.validation import ValidationFinding
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


def test_common_n_and_validation_status_schema_match_omitted_runtime_defaults() -> None:
    common_validator = Draft202012Validator(CommonNState.model_json_schema())
    valid_frozen = {"state":"frozen", "common_n":4, "source_campaign_id":"campaign-1",
                    "source_sha256":"a" * 64, "plateau_found":True}
    valid_fallback = {**valid_frozen, "common_n":8, "plateau_found":False}
    for value in ({}, valid_frozen, valid_fallback):
        assert common_validator.is_valid(value)
        CommonNState.model_validate(value)
    invalid_common_states = (
        {"state":"frozen", "common_n":None, "source_campaign_id":None, "source_sha256":None, "plateau_found":None},
        {"state":"frozen", "common_n":2, "source_campaign_id":"campaign-1", "source_sha256":"a" * 64, "plateau_found":False},
        {"common_n":4},
    )
    for value in invalid_common_states:
        assert not common_validator.is_valid(value)
        with pytest.raises(ValidationError):
            CommonNState.model_validate(value)

    status_validator = Draft202012Validator(ValidationStatus.model_json_schema())
    assert status_validator.is_valid({})
    assert ValidationStatus.model_validate({}).data_validity == "not_validated"
    invalid_statuses = (
        {"comparison_eligibility":"eligible", "comparison_reasons":[]},
        {"data_reasons":["   "]},
        {"comparison_reasons":["\t"]},
    )
    for value in invalid_statuses:
        assert not status_validator.is_valid(value)
        with pytest.raises(ValidationError):
            ValidationStatus.model_validate(value)
    eligible = {"data_validity":"valid", "data_reasons":[], "comparison_eligibility":"eligible", "comparison_reasons":[]}
    assert status_validator.is_valid(eligible)
    ValidationStatus.model_validate(eligible)


def test_timing_gap_and_validation_finding_reasons_are_nonblank_in_runtime_and_schema() -> None:
    gap_time = "2026-10-21T00:00:00Z"
    gap_schema = Draft202012Validator(TimingGap.model_json_schema())
    valid_gap = {"started_at":gap_time, "finished_at":"2026-10-21T00:00:01Z", "reason":"probe gap"}
    assert gap_schema.is_valid(valid_gap)
    TimingGap.model_validate(valid_gap)
    invalid_gap = {**valid_gap, "reason":"  "}
    assert not gap_schema.is_valid(invalid_gap)
    with pytest.raises(ValidationError):
        TimingGap.model_validate(invalid_gap)

    finding_schema = Draft202012Validator(ValidationFinding.model_json_schema())
    valid_finding = {"code":"missing_artifact", "severity":"warning", "message":"file was not retrieved"}
    assert finding_schema.is_valid(valid_finding)
    ValidationFinding.model_validate(valid_finding)
    for invalid_finding in ({**valid_finding, "code":" "}, {**valid_finding, "message":"\t"}):
        assert not finding_schema.is_valid(invalid_finding)
        with pytest.raises(ValidationError):
            ValidationFinding.model_validate(invalid_finding)
