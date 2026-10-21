from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from cloud_network_benchmark.contracts import (
    ArtifactRecord, ClockMapping, CommandEvidence, DiagnosticSample, Evidence, IdentityContext,
    ValidationResult, VmMetadata,
)


def identity() -> IdentityContext:
    return IdentityContext(experiment_id="EXP-900", config_id="exp_900_aws_validation", campaign_id="campaign",
                          observation_id="campaign-aws-observation", run_id="campaign-aws",
                          deployment_id="campaign-aws-deployment", provider="aws")


def test_diagnostic_and_metadata_contracts_reject_unknown_or_mismatched_identity() -> None:
    with pytest.raises(ValidationError):
        DiagnosticSample(schema_version=2, deployment_id="campaign-aws-deployment", vm_role="vm_a",
                         sampled_at=datetime.now(timezone.utc), monotonic_seconds=1, interval_seconds=1,
                         cpu_total_percent={"value": 10, "unavailable_reason": None},
                         cpu_max_core_percent={"unavailable_reason":"not collected"},
                         cpu_steal_percent={"unavailable_reason":"not collected"},
                         tcp_retransmissions={"unavailable_reason":"not collected"},
                         interface_rx_drops={"unavailable_reason":"not collected"},
                         interface_tx_drops={"unavailable_reason":"not collected"},
                         interface_rx_errors={"unavailable_reason":"not collected"},
                         interface_tx_errors={"unavailable_reason":"not collected"},
                         rx_bytes={"unavailable_reason":"not collected"}, tx_bytes={"unavailable_reason":"not collected"},
                         unexplained="rejected")
    with pytest.raises(ValidationError):
        VmMetadata(schema_version=2, deployment_id="other-deployment", vm_role="vm_a", provider="aws")


def test_artifact_success_requires_materialized_integrity_evidence() -> None:
    with pytest.raises(ValidationError):
        ArtifactRecord(artifact_id="sockperf", path="measurements/idle/sockperf.csv", kind="sockperf",
                      producer="vm_a", unit_id="idle_latency", deployment_id="campaign-aws-deployment",
                      closed=False, size_bytes=Evidence[int](unavailable_reason="not materialized"),
                      sha256=Evidence[str](unavailable_reason="not materialized"), retrieval_state="verified")


def test_command_handoff_rejects_raw_environment_and_stdin_fields() -> None:
    with pytest.raises(ValidationError, match="durable view"):
        CommandEvidence(request={"argv": ["tool"], "environment": {"TOKEN": "secret"}},
                        result={"stdout": "", "stderr": ""}, producer="controller",
                        deployment_id="campaign-aws-deployment", binary_version={"unavailable_reason":"pending"},
                        source_identity={"unavailable_reason":"pending"},
                        monotonic_started={"unavailable_reason":"pending"},
                        monotonic_finished={"unavailable_reason":"pending"})


def test_validation_result_keeps_unavailable_sources_explicit() -> None:
    result = ValidationResult(identity=identity(), mode="acceptance", status="not_validated",
                              comparison_eligibility="not_evaluated", data_reasons=["pending"],
                              comparison_reasons=["pending"], source_sha256={"unavailable_reason":"pending"},
                              resolved_sha256={"unavailable_reason":"pending"})
    assert result.source_sha256.value is None
