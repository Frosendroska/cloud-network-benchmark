from __future__ import annotations

from datetime import datetime, timezone

from cloud_network_benchmark.calibration import select_common_n
from cloud_network_benchmark.contracts.common import ComparisonEligibility, DataValidity, ValidationStatus
from cloud_network_benchmark.contracts.validation import ValidationResult


ORDERS = {provider: [1,4,2,8,8,2,4,1,2,8,1,4] for provider in ("aws", "azure", "gcp")}


def result(provider, run, levels=None, *, valid=True, deployment=None, campaign="campaign"):
    units=[]
    seen={1:0,2:0,4:0,8:0}
    for index, streams in enumerate(ORDERS[provider], 1):
        seen[streams]+=1
        value=(levels or {1:4,2:9,4:9.6,8:10})[streams]
        units.append({"unit_id":f"trial-{index}", "trial_index":index, "streams":streams,
                      "deployment_id": deployment or f"deployment-{provider}",
                      "tcp":{"throughput_gbps":{"value":value}}, "receiver_intervals":[],
                      "sources":[{"artifact_id":"iperf3_receiver","path":f"{provider}/trial-{index}.json",
                                  "sha256":f"{index:064x}"}]})
    return ValidationResult(schema_version=2, run_id=run, mode="calibration",
        validation_status=ValidationStatus(data_validity="valid" if valid else "partial", data_reasons=[] if valid else ["partial"],
                    comparison_eligibility="ineligible", comparison_reasons=["calibration"]),
        metrics={"value":{"identity":{"provider":provider,"campaign_id":campaign,
                        "deployment_id":deployment or f"deployment-{provider}"},"units":units}},
        evaluated_at=datetime(2026,10,21,tzinfo=timezone.utc))


def test_selects_smallest_common_n_and_returns_existing_common_state_fields():
    recommendation=select_common_n([result("aws","a"),result("azure","b"),result("gcp","c")])
    assert recommendation.common_n.value == 4
    assert recommendation.plateau_found.value is True
    assert recommendation.common_n_state_fields()["state"] == "frozen"
    assert len(recommendation.source_sha256.value) == 64


def test_complete_no_plateau_uses_explicit_eight_stream_fallback():
    levels={1:100,2:10,4:20,8:30}
    recommendation=select_common_n([result("aws","a",levels),result("azure","b",levels),result("gcp","c",levels)])
    assert recommendation.common_n.value == 8
    assert recommendation.plateau_found.value is False


def test_missing_invalid_all_zero_or_duplicate_provider_stays_pending():
    cases=[[result("aws","a"),result("gcp","g")],
           [result("aws","a",valid=False),result("azure","b"),result("gcp","g")],
           [result("aws","a",{1:0,2:0,4:0,8:0}),result("azure","b",{1:0,2:0,4:0,8:0}),result("gcp","g",{1:0,2:0,4:0,8:0})],
           [result("aws","a"),result("aws","b"),result("gcp","g")]]
    for group in cases:
        assert select_common_n(group).common_n.value is None


def test_missing_trial_source_hash_stays_pending():
    value = result("aws", "a")
    value.metrics.value["units"][0]["sources"] = []
    assert select_common_n([value, result("azure", "b"), result("gcp", "c")]).common_n.value is None


def test_mixed_campaign_duplicate_deployment_and_duplicate_trial_stay_pending():
    aws = result("aws", "a")
    azure = result("azure", "b")
    gcp = result("gcp", "c")
    assert select_common_n([aws, azure, result("gcp", "c2", campaign="other")]).common_n.value is None
    assert select_common_n([aws, result("azure", "b", deployment="deployment-aws"), gcp]).common_n.value is None
    duplicate = result("aws", "a2")
    duplicate.metrics.value["units"][11]["trial_index"] = 1
    assert select_common_n([duplicate, azure, gcp]).common_n.value is None


def test_zero_trial_is_valid_when_best_median_is_positive_and_digest_binds_deployments():
    levels = {1: 0, 2: 9, 4: 9.6, 8: 10}
    values = [result(provider, provider, levels) for provider in ("aws", "azure", "gcp")]
    recommendation = select_common_n(values)
    assert recommendation.common_n.value == 4
    assert recommendation.source_sha256.value
    values[0].metrics.value["units"][0]["deployment_id"] = "another-deployment"
    assert select_common_n(values).common_n.value is None
