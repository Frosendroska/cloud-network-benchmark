from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from typing import Optional

import pytest
from pydantic import ValidationError

from cloud_network_benchmark.contracts import (
    ArtifactRecord, ClockMapping, CommandEvidence, DiagnosticSample, Evidence, IdentityContext,
    ValidationResult, VmMetadata,
)
from cloud_network_benchmark.contracts import CommandResult
from cloud_network_benchmark.contracts import ARTIFACTS, ValidatorInput, artifact_inputs
from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.manifests import ChildRunManifest


def identity() -> IdentityContext:
    return IdentityContext(experiment_id="EXP-900", config_id="exp-900-acceptance", campaign_id="campaign",
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


def test_vm_metadata_accepts_structured_tool_build_and_limit_evidence() -> None:
    unavailable = {"unavailable_reason":"not observed"}
    metadata = VmMetadata(
        schema_version=2, deployment_id="campaign-aws-deployment", vm_role="vm_a", provider="aws",
        cpu_model=unavailable, nic_driver=unavailable, acceleration=unavailable, image=unavailable,
        kernel=unavailable, congestion_control=unavailable,
        tool_versions={"value":{"sockperf":"1.0", "iperf3":"3.15"}},
        source_builds={"value":{"framework_commit":"a"*40}},
        documented_network_limit={"value":{"source":"provider-spec", "value":"up to 12.5 Gbps"}},
        mtu=unavailable, observed_region=unavailable, observed_zone=unavailable,
        physical_zone_id=unavailable, actual_shape=unavailable,
    )
    assert metadata.tool_versions.value["iperf3"] == "3.15"


def test_artifact_success_requires_materialized_integrity_evidence() -> None:
    with pytest.raises(ValidationError):
        ArtifactRecord(artifact_id="sockperf", path="measurements/idle/sockperf.csv", kind="sockperf",
                      producer="vm_a", unit_id="idle_latency", deployment_id="campaign-aws-deployment",
                      closed=False, size_bytes=Evidence[int](unavailable_reason="not materialized"),
                      sha256=Evidence[str](unavailable_reason="not materialized"), retrieval_state="verified")


def test_artifact_file_verification_checks_real_size_and_hash(tmp_path) -> None:
    payload = b"native synthetic bytes\n"
    path = tmp_path / "measurements/unit/sockperf.csv"
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)
    record = ArtifactRecord(artifact_id="unit-sockperf", path="measurements/unit/sockperf.csv", kind="sockperf-csv",
                            producer="vm_a", unit_id="unit", deployment_id="campaign-aws-deployment",
                            closed=True, size_bytes={"value":len(payload)}, sha256={"value":hashlib.sha256(payload).hexdigest()},
                            retrieval_state="verified")
    record.verify_materialized_file(tmp_path)
    with pytest.raises(ValueError, match="size"):
        record.model_copy(update={"size_bytes":Evidence[int](value=len(payload)+1)}).verify_materialized_file(tmp_path)
    with pytest.raises(ValueError, match="hash"):
        record.model_copy(update={"sha256":Evidence[str](value="a"*64)}).verify_materialized_file(tmp_path)
    missing = record.model_copy(update={"path":"measurements/unit/missing.csv"})
    with pytest.raises(FileNotFoundError):
        missing.verify_materialized_file(tmp_path)


def test_command_handoff_rejects_raw_environment_and_stdin_fields() -> None:
    with pytest.raises(ValidationError, match="durable view"):
        CommandEvidence(schema_version=2, request={"argv": ["tool"], "environment": {"TOKEN": "secret"}},
                        result={"stdout": "", "stderr": ""}, producer="controller",
                        deployment_id="campaign-aws-deployment", binary_version={"unavailable_reason":"pending"},
                        source_identity={"unavailable_reason":"pending"},
                        monotonic_started={"unavailable_reason":"pending"},
                        monotonic_finished={"unavailable_reason":"pending"})


HANDOFF_START = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)


def native_command(*, utc_delta: float = -5, outcome: str = "succeeded", exit_code: object = 0,
                   mutate_result: Optional[dict[str, object]] = None,
                   deployment_id: str = "campaign-aws-deployment", **updates: object) -> CommandEvidence:
    duration = 2.0
    result = CommandResult(
        action_id="unit-command", outcome=outcome, started_at=HANDOFF_START,
        finished_at=HANDOFF_START + timedelta(seconds=utc_delta), duration_seconds=duration,
        exit_code=exit_code, stdout="token=private-sentinel", stderr="", redactions=["private-sentinel"],
        duration_basis="monotonic", monotonic_started=100.0, monotonic_finished=102.0,
        utc_elapsed_discrepancy_seconds=utc_delta - duration,
    ).durable_view()
    if mutate_result:
        result.update(mutate_result)
    values: dict[str, object] = {
        "schema_version": 2, "request": {"argv": ["tool"], "cwd": "."}, "result": result,
        "producer": "controller", "deployment_id": deployment_id, "unit_id": "idle_latency",
        "binary_version": {"value": "test-tool-1"}, "source_identity": {"value": "framework commit " + "a" * 40},
        "monotonic_started": {"value": 100.0}, "monotonic_finished": {"value": 102.0},
        "started_at": {"value": HANDOFF_START}, "finished_at": {"value": HANDOFF_START + timedelta(seconds=utc_delta)},
        "duration_basis": "monotonic", "utc_elapsed_discrepancy_seconds": utc_delta - duration,
    }
    values.update(updates)
    return CommandEvidence(**values)


@pytest.mark.parametrize("utc_delta, discrepancy", [(-5.0, -7.0), (12.0, 10.0), (2.0, 0.0)])
@pytest.mark.parametrize("outcome, exit_code", [("succeeded", 0), ("failed", 3), ("timed_out", None), ("interrupted", None)])
def test_native_command_preserves_clock_step_timing_and_all_outcomes(
    utc_delta: float, discrepancy: float, outcome: str, exit_code: object,
) -> None:
    evidence = native_command(utc_delta=utc_delta, outcome=outcome, exit_code=exit_code)
    assert evidence.duration_basis == "monotonic"
    assert evidence.started_at.value == HANDOFF_START
    assert evidence.finished_at.value == HANDOFF_START + timedelta(seconds=utc_delta)
    assert evidence.utc_elapsed_discrepancy_seconds == discrepancy
    assert evidence.result["outcome"] == outcome


@pytest.mark.parametrize("updates", [
    {"duration_basis": "utc"}, {"utc_elapsed_discrepancy_seconds": None},
    {"duration_basis": "utc", "utc_elapsed_discrepancy_seconds": None},
    {"utc_elapsed_discrepancy_seconds": True}, {"utc_elapsed_discrepancy_seconds": math.inf},
    {"monotonic_started": {"value": True}}, {"monotonic_started": {"value": math.nan}},
    {"monotonic_finished": {"unavailable_reason": "missing"}},
    {"started_at": {"unavailable_reason": "missing"}},
    {"finished_at": {"value": datetime(2026, 10, 22, 11, 59, 55)}},
])
def test_native_command_rejects_partial_or_malformed_opt_in(updates: dict[str, object]) -> None:
    with pytest.raises((ValidationError, ValueError)):
        native_command(**updates)


@pytest.mark.parametrize("mutation", [
    {"duration_basis": "utc"}, {"started_at": (HANDOFF_START + timedelta(seconds=1)).isoformat()},
    {"monotonic_started": 101.0}, {"monotonic_finished": 103.0},
    {"utc_elapsed_discrepancy_seconds": -6.0}, {"duration_seconds": 3.0},
])
def test_native_command_rejects_projection_disagreement(mutation: dict[str, object]) -> None:
    with pytest.raises((ValidationError, ValueError)):
        native_command(mutate_result=mutation)


@pytest.mark.parametrize("field", ["duration_seconds", "finished_at", "stdout"])
def test_native_monotonic_projection_rejects_missing_result_fields(field: str) -> None:
    payload = native_command().model_dump(mode="python")
    del payload["result"][field]
    with pytest.raises((ValidationError, ValueError)):
        CommandEvidence.model_validate(payload)


def test_native_command_durable_json_round_trip_keeps_timing_provenance_and_redaction() -> None:
    evidence = native_command()
    durable = evidence.model_dump(mode="json")
    assert "private-sentinel" not in json.dumps(durable)
    reloaded = CommandEvidence.model_validate_json(json.dumps(durable))
    assert reloaded.producer == evidence.producer
    assert reloaded.deployment_id == evidence.deployment_id and reloaded.unit_id == evidence.unit_id
    assert reloaded.binary_version == evidence.binary_version and reloaded.source_identity == evidence.source_identity
    assert reloaded.duration_basis == "monotonic"
    assert reloaded.monotonic_started.value == 100 and reloaded.monotonic_finished.value == 102
    assert reloaded.started_at.value == HANDOFF_START
    assert reloaded.finished_at.value == HANDOFF_START - timedelta(seconds=5)
    assert reloaded.utc_elapsed_discrepancy_seconds == -7


def test_legacy_utc_native_command_keeps_partial_unavailable_timing_readable() -> None:
    evidence = CommandEvidence(
        schema_version=2, request={"argv": ["tool"]}, result={"stdout": "", "stderr": ""},
        producer="controller", deployment_id="campaign-aws-deployment",
        binary_version={"unavailable_reason": "pending"}, source_identity={"unavailable_reason": "pending"},
        monotonic_started={"value": 1.0}, monotonic_finished={"unavailable_reason": "not captured"},
        started_at={"value": HANDOFF_START}, finished_at={"unavailable_reason": "not captured"},
    )
    assert evidence.duration_basis == "utc" and evidence.utc_elapsed_discrepancy_seconds is None
    entirely_unavailable = CommandEvidence(
        schema_version=2, request={"argv": ["tool"]}, result={"stdout": "", "stderr": ""},
        producer="controller", deployment_id="campaign-aws-deployment",
        binary_version={"unavailable_reason": "pending"}, source_identity={"unavailable_reason": "pending"},
        monotonic_started={"unavailable_reason": "not captured"},
        monotonic_finished={"unavailable_reason": "not captured"},
    )
    assert entirely_unavailable.started_at.value is None and entirely_unavailable.finished_at.value is None


def test_monotonic_command_round_trip_inside_child_manifest_and_validator_input(repository_root) -> None:
    child = json.loads((repository_root / "tests/fixtures/manifests/initialized-child.json").read_text())
    child["commands"] = [native_command(deployment_id=child["deployment_id"]).model_dump(mode="json")]
    reloaded_child = ChildRunManifest.model_validate_json(json.dumps(child))
    assert reloaded_child.commands[0].deployment_id == child["deployment_id"]
    assert reloaded_child.commands[0].source_identity.value == "framework commit " + "a" * 40
    assert reloaded_child.config_provenance.sha256 == child["config_provenance"]["sha256"]
    assert reloaded_child.config_provenance.implementation_git_commit.value == child["config_provenance"]["implementation_git_commit"]["value"]

    resolved, _ = resolve_campaign(repository_root / "configs/tests/exp-900-single-provider.yaml", repository_root)
    validator_identity = IdentityContext(
        experiment_id=resolved.experiment_id, config_id=resolved.config_id, campaign_id="campaign",
        observation_id="campaign-aws-observation", run_id="campaign-aws",
        deployment_id="campaign-aws-deployment", provider="aws",
    )
    artifacts = artifact_inputs(ARTIFACTS, mode=resolved.mode, measurement_units=resolved.measurement_units)
    validator = ValidatorInput(
        schema_version=2, identity=validator_identity, mode=resolved.mode, context=resolved.context,
        campaign_manifest_path="results/manifests/campaign.json", child_manifest_path="results/manifests/run.json",
        child_result_root="results/raw/run", config_snapshot_path="results/raw/run/config.yaml",
        config_sha256=resolved.config_source.sha256, resolved_snapshot_path="results/raw/run/config.resolved.json",
        provider="aws", scenario=resolved.scenario, enabled_phases=[item.phase for item in resolved.measurement_units],
        expected_vm_roles=["vm_a", "vm_b"], measurement_direction="vm_a_to_vm_b", artifacts=artifacts,
        measurement_units=resolved.measurement_units,
        commands=[native_command(deployment_id=validator_identity.deployment_id).model_dump(mode="json")],
        execution_state="initialized", cleanup_state="not_started", failure=None, cleanup_failure=None,
        vm_metadata={"unavailable_reason": "pending"}, provider_metadata={"unavailable_reason": "pending"},
        tool_versions={"unavailable_reason": "pending"}, implementation_git_commit={"unavailable_reason": "pending"},
        design_git_commit={"unavailable_reason": "pending"},
    )
    reloaded_validator = ValidatorInput.model_validate_json(validator.model_dump_json())
    assert reloaded_validator.commands[0].duration_basis == "monotonic"
    assert reloaded_validator.commands[0].source_identity.value == "framework commit " + "a" * 40
    assert reloaded_validator.commands[0].utc_elapsed_discrepancy_seconds == -7
    assert reloaded_validator.identity == validator_identity


def test_validation_result_keeps_unavailable_sources_explicit() -> None:
    result = ValidationResult(schema_version=2, run_id=identity().run_id, mode="acceptance",
                              validation_status={"data_validity":"not_validated", "data_reasons":["pending"],
                                                 "comparison_eligibility":"not_evaluated", "comparison_reasons":["pending"]},
                              evaluated_at=datetime.now(timezone.utc))
    assert result.metrics.value is None
