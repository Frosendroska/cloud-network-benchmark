from __future__ import annotations

import pytest
from pydantic import ValidationError

from cloud_network_benchmark.contracts.common import (
    ComparisonEligibility,
    DataValidity,
    Evidence,
    ExperimentMode,
    GitSnapshot,
    IdentityContext,
    ValidationStatus,
)


def test_v2_identity_and_finite_status_values() -> None:
    identity = IdentityContext(
        experiment_id="EXP-001", config_id="exp_001", campaign_id="campaign",
        observation_id="campaign-observation", run_id="campaign-aws",
        deployment_id="campaign-deployment", provider="aws",
    )
    assert identity.run_id.endswith("-aws")
    assert {item.value for item in ExperimentMode} == {"acceptance", "calibration", "short_study", "diagnostic_day"}
    assert {item.value for item in DataValidity} == {"not_validated", "valid", "partial", "invalid", "unavailable"}
    assert {item.value for item in ComparisonEligibility} == {"not_evaluated", "eligible", "ineligible", "unavailable"}
    assert ValidationStatus().data_validity == DataValidity.NOT_VALIDATED.value


@pytest.mark.parametrize("value", ["../x", "a/b", "a b", "_bad", "x" * 193])
def test_identity_rejects_invalid_identifiers(value: str) -> None:
    with pytest.raises(ValidationError):
        IdentityContext(
            experiment_id="EXP-001", config_id="exp_001", campaign_id=value,
            observation_id="observation", run_id="run-aws", deployment_id="deployment", provider="aws",
        )


def test_evidence_and_validation_status_stay_honest() -> None:
    with pytest.raises(ValidationError):
        Evidence[str]()
    with pytest.raises(ValidationError):
        ValidationStatus(data_validity="partial", comparison_eligibility="eligible", data_reasons=["partial coverage"])
    status = ValidationStatus(
        data_validity="valid", comparison_eligibility="eligible",
    )
    assert status.comparison_eligibility == "eligible"


def test_git_snapshot_accepts_only_hash_evidence() -> None:
    snapshot = GitSnapshot(
        commit=Evidence[str](value="a" * 40), dirty=Evidence[bool](value=False),
        diff_sha256=Evidence[str](value="b" * 64),
    )
    assert snapshot.dirty.value is False
    with pytest.raises(ValidationError):
        GitSnapshot(commit=Evidence[str](value="not-a-hash"), dirty=Evidence[bool](value=False), diff_sha256=Evidence[str](value="b" * 64))
