from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import pytest

from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.contracts import IdentityContext, ValidatorInput, artifact_inputs
from cloud_network_benchmark.contracts.campaign import PhaseConfiguration, derive_measurement_units
from cloud_network_benchmark.contracts.common import ComparisonEligibility, DataValidity, Evidence, ExperimentMode, FailureEvidence, ValidationStatus
from cloud_network_benchmark.contracts.measurements import ClockMapping, CommandEvidence, MeasurementTiming, TimingGap
from cloud_network_benchmark.contracts.admission import ScheduleEntry
from cloud_network_benchmark.contracts.artifacts import ArtifactLayout, ArtifactRecord
from cloud_network_benchmark.contracts.validation import ValidationResult
from cloud_network_benchmark.native import NativeParse, ProbeCounts, ReceiverInterval, SourceRef, rtt_statistics
from cloud_network_benchmark.validation import _command_findings, _coverage, _resolved_snapshot_findings, _sequence_timing_findings, _validate_artifacts, evaluate_comparison, validate_bundle

NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)


def make_input(repository_root, provider="aws", campaign="camp", *, config_name="exp-001-multi-provider.yaml", mode_override=None):
    resolved, _ = resolve_campaign(repository_root / "configs/experiments" / config_name, repository_root)
    obs = next(item for item in resolved.observations if item.provider == provider)
    mode = ExperimentMode(mode_override) if mode_override else resolved.mode
    units = derive_measurement_units(mode, resolved.benchmark) if mode_override else obs.measurement_units
    if mode == ExperimentMode.ACCEPTANCE:
        units = [unit.model_copy(update={"measured_seconds": 10}) for unit in units]
    units = [unit.model_copy(update={"streams": 4}) if mode == ExperimentMode.SHORT_STUDY
             and unit.kind == "phase" and unit.phase == "multi_flow" and unit.streams is None else unit for unit in units]
    run_id = f"{campaign}-{provider}"
    deployment = f"{run_id}-deployment"
    identity = IdentityContext(experiment_id=resolved.experiment_id, config_id=resolved.config_id,
                               campaign_id=campaign, observation_id=f"{campaign}-{provider}-obs",
                               run_id=run_id, deployment_id=deployment, provider=provider)
    start = NOW
    timings = []
    elapsed = 0
    for unit in units:
        probe_start = start + timedelta(seconds=elapsed)
        measured_offset = 5 if unit.phase == "idle_latency" else 10
        measured_start = probe_start + timedelta(seconds=measured_offset)
        timings.append(MeasurementTiming(unit_id=unit.unit_id, deployment_id=deployment,
            probe_test_started_at={"value":probe_start},
            tcp_started_at=(Evidence[datetime](unavailable_reason="no TCP client in idle phase") if unit.phase == "idle_latency"
                            else Evidence[datetime](value=probe_start + timedelta(seconds=7))),
            measured_started_at={"value":measured_start}, measured_finished_at={"value":measured_start + timedelta(seconds=unit.measured_seconds)},
            monotonic_started={"value":float(elapsed + 1)}, monotonic_finished={"value":float(elapsed + unit.measured_seconds + 1)},
            omitted_seconds=5, overlap_seconds={"value":unit.measured_seconds}))
        elapsed += unit.measured_seconds + (5 if unit.phase == "idle_latency" else 10) + 10
    row = "slot-one,EXP-001,P0"
    schedule = ScheduleEntry(schema_version=2, entry_id="slot-one", config_id=resolved.config_id,
        experiment_id=resolved.experiment_id, scheduled_start=NOW, window_label="morning",
        source_path="schedule.csv", source_sha256="c" * 64,
        row_sha256=hashlib.sha256(row.encode()).hexdigest(), original_header="entry_id,experiment_id,priority",
        original_row=row, columns=["entry_id", "experiment_id", "priority"], priority="P0")
    return ValidatorInput(schema_version=2, identity=identity, mode=mode, context=resolved.context,
        campaign_manifest_path="results/manifests/camp.json", child_manifest_path=f"results/manifests/{run_id}.json",
        child_result_root="results/raw/"+run_id, config_snapshot_path="results/raw/"+run_id+"/config.yaml",
        config_sha256=resolved.config_source.sha256, resolved_snapshot_path="results/raw/"+run_id+"/config.resolved.json",
        provider=provider, scenario=resolved.scenario, enabled_phases=list(dict.fromkeys(item.phase for item in units)),
        expected_vm_roles=["vm_a", "vm_b"], measurement_direction="vm_a_to_vm_b",
        artifacts=artifact_inputs(ArtifactLayout.for_mode(mode, units), mode=mode, measurement_units=units),
        measurement_units=units, timings=timings,
        schedule_entry={"value": schedule},
        execution_state="succeeded", cleanup_state="succeeded", failure=None, cleanup_failure=None,
        vm_metadata={"unavailable_reason":"not supplied"}, provider_metadata={"unavailable_reason":"not supplied"},
        tool_versions={"unavailable_reason":"not supplied"}, implementation_git_commit={"value":"a"*40},
        design_git_commit={"value":"b"*40})


def good_result(value):
    return ValidationResult(schema_version=2, run_id=value.identity.run_id, mode=value.mode,
        validation_status=ValidationStatus(data_validity=DataValidity.VALID, data_reasons=[],
            comparison_eligibility=ComparisonEligibility.UNAVAILABLE, comparison_reasons=["not matched yet"]),
        metrics={"value":{"identity":{"provider":value.provider},
                          "common_n":{"state":"frozen","common_n":4},
                          "execution":{"execution_state":"succeeded","bundle_complete":True}}}, evaluated_at=NOW)


def test_empty_materialized_bundle_is_unavailable_and_explains_missing_evidence(repository_root):
    value = make_input(repository_root)
    result = validate_bundle(value, evaluated_at=NOW)
    assert result.validation_status.data_validity == "unavailable"
    assert result.validation_status.comparison_eligibility == "unavailable"
    assert any(f.code == "artifact.missing" for f in result.findings)
    assert any(f.code == "identity.campaign_manifest_missing" for f in result.findings)
    assert any(f.code == "identity.child_manifest_missing" for f in result.findings)
    assert result.metrics.value["execution"]["bundle_complete"] is True
    assert len(result.metrics.value["units"]) == 3


@pytest.mark.parametrize(("config_name", "mode", "units"), [
    ("exp-001-multi-provider.yaml", "acceptance", 3),
    ("exp-001-multi-provider.yaml", "short_study", 3),
    ("exp-001-calibration.yaml", "calibration", 12),
    ("exp-001-diagnostic-day.yaml", "diagnostic_day", 1),
])
def test_all_modes_keep_their_declared_unit_shape(repository_root, config_name, mode, units):
    value = make_input(repository_root, config_name=config_name,
                       mode_override="acceptance" if mode == "acceptance" else None)
    result = validate_bundle(value, evaluated_at=NOW)
    observed = result.metrics.value["units"]
    assert len(observed) == units
    assert result.metrics.value["research_eligible_mode"] is (mode == "short_study")


def test_validation_replay_is_deterministic_and_rejects_naive_evaluation_time(repository_root):
    value = make_input(repository_root)
    first = validate_bundle(value, evaluated_at=NOW)
    second = validate_bundle(value, evaluated_at=NOW)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    import pytest
    with pytest.raises(ValueError, match="timezone-aware"):
        validate_bundle(value, evaluated_at=datetime(2026, 10, 21))


def test_comparison_uses_actual_start_skew_and_returns_copied_results(repository_root):
    values = [make_input(repository_root, p) for p in ("aws", "azure", "gcp")]
    adjusted = []
    for value, seconds in zip(values, (0, 60.001, 0)):
        timing = value.timings[1].model_copy(update={
            "measured_started_at": Evidence[datetime](value=NOW + timedelta(seconds=seconds))})
        adjusted.append(value.model_copy(update={"timings": [*value.timings[:1], timing, *value.timings[2:]]}))
    originals = [good_result(value) for value in adjusted]
    output = evaluate_comparison(adjusted, originals)
    assert all(item.validation_status.comparison_eligibility == "eligible" for item in output.values())
    assert any(f.code == "comparison.start_target_degraded" for result in output.values() for f in result.findings)
    assert all(item.validation_status.comparison_eligibility == "unavailable" for item in originals)


def test_comparison_rejects_over_300_second_skew(repository_root):
    values = [make_input(repository_root, p) for p in ("aws", "azure", "gcp")]
    adjusted = [value.model_copy(update={"timings":[*value.timings[:1], value.timings[1].model_copy(update={
        "measured_started_at": Evidence[datetime](value=NOW + timedelta(seconds=skew))}), *value.timings[2:]]})
        for value, skew in zip(values, (0, 300.001, 0))]
    output = evaluate_comparison(adjusted, [good_result(value) for value in adjusted])
    assert all(item.validation_status.comparison_eligibility == "ineligible" for item in output.values())


@pytest.mark.parametrize(("skew", "eligibility", "degraded"), [
    (60, "eligible", False), (60.001, "eligible", True), (300, "eligible", True),
    (300.001, "ineligible", False),
])
def test_comparison_skew_boundaries(repository_root, skew, eligibility, degraded):
    values = [make_input(repository_root, p) for p in ("aws", "azure", "gcp")]
    adjusted = []
    for value, seconds in zip(values, (0, skew, 0)):
        timing = value.timings[1].model_copy(update={
            "measured_started_at": Evidence[datetime](value=NOW + timedelta(seconds=seconds))})
        adjusted.append(value.model_copy(update={"timings": [*value.timings[:1], timing, *value.timings[2:]]}))
    output = evaluate_comparison(adjusted, [good_result(value) for value in adjusted])
    assert all(item.validation_status.comparison_eligibility == eligibility for item in output.values())
    assert all(any(f.code == "comparison.start_target_degraded" for f in item.findings) == degraded
               for item in output.values())


def test_comparison_missing_actual_start_is_unavailable(repository_root):
    values = [make_input(repository_root, p) for p in ("aws", "azure", "gcp")]
    missing = values[1].timings[1].model_copy(update={"measured_started_at": Evidence[datetime](unavailable_reason="not captured")})
    values[1] = values[1].model_copy(update={"timings":[*values[1].timings[:1], missing, *values[1].timings[2:]]})
    output = evaluate_comparison(values, [good_result(value) for value in values])
    assert all(item.validation_status.comparison_eligibility == "unavailable" for item in output.values())


def _coverage_inputs(repository_root, *, duration=60.0, gap_seconds=0.0):
    value = make_input(repository_root)
    unit = value.measurement_units[1]
    start = NOW
    gaps = [TimingGap(started_at=start + timedelta(seconds=30),
                      finished_at=start + timedelta(seconds=30 + gap_seconds), reason="recorded gap")] if gap_seconds else []
    mappings = [
        ClockMapping(vm_role="vm_a", monotonic_seconds=100, utc_timestamp=start,
                     estimated_error_ms={"value": 0}, synchronization_source={"value": "offline fixture"}),
        ClockMapping(vm_role="vm_b", monotonic_seconds=200, utc_timestamp=start,
                     estimated_error_ms={"value": 0}, synchronization_source={"value": "offline fixture"}),
    ]
    timing = MeasurementTiming(unit_id=unit.unit_id, deployment_id=value.identity.deployment_id,
        probe_test_started_at={"value": start}, tcp_started_at={"value": start + timedelta(seconds=5)},
        measured_started_at={"value": start + timedelta(seconds=5)},
        measured_finished_at={"value": start + timedelta(seconds=5 + duration)},
        monotonic_started={"value": 100}, monotonic_finished={"value": 100 + duration},
        omitted_seconds=5, overlap_seconds={"value": min(60, duration)}, gaps=gaps, clock_mappings=mappings)
    value = value.model_copy(update={"timings": [timing]})
    count = ProbeCounts(sent=Evidence[int](value=65000), received=Evidence[int](value=60000),
        dropped=Evidence[int](value=0), duplicated=Evidence[int](value=0), out_of_order=Evidence[int](value=0),
        skipped=Evidence[int](value=0), runtime_seconds=Evidence[float](value=65),
        reporting_scope="one Sockperf invocation", source=SourceRef("sockperf-log", "sockperf.log", "d" * 64))
    sock = NativeParse(probe_counts=(count,))
    tcp = NativeParse(receiver_intervals=(ReceiverInterval("1", 5, 5 + duration, duration, 1000,
        False, False, SourceRef("iperf3_receiver", "iperf3.json", "e" * 64), 1),))
    return value, unit, sock, tcp


@pytest.mark.parametrize(("duration", "gap", "complete", "ratio"), [
    (59.999, 0, False, 1.0), (60.0, 0, True, 1.0), (60.001, 0, True, 60 / 60.001),
    (60.0, .6, True, .99), (60.0, .6001, False, 59.3999 / 60),
])
def test_coverage_requires_full_receiver_duration_and_exact_99_percent(repository_root, duration, gap, complete, ratio):
    value, unit, sock, tcp = _coverage_inputs(repository_root, duration=duration, gap_seconds=gap)
    result = _coverage(value, unit, sock, tcp)
    assert result["complete"] is complete
    assert result["active_probe_ratio"]["value"] == pytest.approx(ratio, abs=1e-9)


@pytest.mark.parametrize(("command_finish", "expected", "available"), [
    (165, 60.0, True),   # exact admissible measured-start bracket
    (225, 0.0, True),    # broad bracket includes a start after the probe window
    (164, None, False),  # command cannot contain omission plus receiver duration
])
def test_conservative_command_native_bracket_uses_worst_case_overlap(repository_root, command_finish, expected, available):
    value, unit, sock, tcp = _coverage_inputs(repository_root)
    ref = SourceRef("iperf3_receiver", "iperf3.json", "e" * 64)
    tcp = NativeParse(receiver_intervals=(
        ReceiverInterval("1", 0, 5, 5, 0, True, False, ref, 1),
        *tcp.receiver_intervals))
    timing = value.timings[0].model_copy(update={
        "unit_id": unit.unit_id,
        "probe_test_started_at": Evidence[datetime](value=NOW),
        "measured_started_at": Evidence[datetime](unavailable_reason="not captured"),
        "measured_finished_at": Evidence[datetime](unavailable_reason="not captured"),
        "clock_mappings": [ClockMapping(vm_role="vm_a", monotonic_seconds=100, utc_timestamp=NOW,
            estimated_error_ms={"value": 0}, synchronization_source={"value": "offline fixture"})],
    })
    command = CommandEvidence(schema_version=2,
        request={"argv":["iperf3", "-c", "10.0.0.2"]}, result={"outcome":"succeeded"},
        producer="vm_a", deployment_id=value.identity.deployment_id, unit_id=unit.unit_id,
        binary_version={"value":"3.17.1"}, source_identity={"value":"fixture build"},
        monotonic_started={"value":100}, monotonic_finished={"value":command_finish})
    value = value.model_copy(update={"timings":[timing], "commands":[command]})
    result = _coverage(value, unit, sock, tcp)
    assert result["active_probe_seconds"]["value"] == expected
    assert (result["tcp_start_bracket"].get("exact_utc_start_available") is False) if available else \
        result["tcp_start_bracket"]["unavailable_reason"]


def test_frozen_common_n_consistency_and_inflation_are_bound_to_same_short_deployment(repository_root, monkeypatch):
    value = make_input(repository_root)
    idle = value.measurement_units[0].unit_id
    loaded = value.measurement_units[1].unit_id
    parses = {}
    for unit_id, samples in ((idle, (10.0, 12.0)), (loaded, (14.0, 16.0))):
        ref = SourceRef("sockperf-csv", f"{unit_id}.csv", "f" * 64, unit_id,
                        value.identity.deployment_id, "vm_a", "3.10")
        from cloud_network_benchmark.native import RttSample
        parses[unit_id] = NativeParse(samples=tuple(RttSample(i, i, i + sample / 1_000_000,
            sample, ref, i + 1) for i, sample in enumerate(samples)))
    import cloud_network_benchmark.validation as module
    monkeypatch.setattr(module, "_parse_unit", lambda _value, unit, _records:
                        (parses.get(unit.unit_id, NativeParse()), None))
    result = module.validate_bundle(value, evaluated_at=NOW)
    inflation = result.metrics.value["units"][1]["rtt"]["latency_inflation_us"]["value"]
    assert inflation == 4.0
    assert result.metrics.value["common_n"]["state"] == "pending"


def test_frozen_common_n_snapshot_contradiction_is_invalid_and_explicit(repository_root, tmp_path):
    value = make_input(repository_root)
    resolved, _ = resolve_campaign(repository_root / "configs/experiments/exp-001-multi-provider.yaml", repository_root)
    snapshot = resolved.model_dump(mode="json")
    snapshot["benchmark"]["common_n"] = {"state":"frozen", "common_n":4,
        "source_campaign_id":"calibration-campaign", "source_sha256":"d" * 64, "plateau_found":True}
    snapshot["launch_blockers"].remove("calibration_pending")
    for unit in snapshot["measurement_units"]:
        if unit["phase"] == "multi_flow":
            unit["streams"] = 4
    # The resolved snapshot freezes N=4; the supplied native attempt claims N=2.
    for observation in snapshot["observations"]:
        observation["benchmark"]["common_n"] = snapshot["benchmark"]["common_n"]
        for unit in observation["measurement_units"]:
            if unit["phase"] == "multi_flow":
                unit["streams"] = 4
    path = tmp_path / "resolved.json"
    payload = json.dumps(snapshot, sort_keys=True).encode()
    path.write_bytes(payload)
    record = ArtifactRecord(artifact_id="resolved_snapshot", path="resolved.json", kind="resolved_configuration",
        producer="controller", unit_id=None, deployment_id=value.identity.deployment_id, closed=True,
        size_bytes={"value":len(payload)}, sha256={"value":hashlib.sha256(payload).hexdigest()}, retrieval_state="verified")
    units = [unit.model_copy(update={"streams":2}) if unit.phase == "multi_flow" else unit
             for unit in value.measurement_units]
    changed = value.model_copy(update={"measurement_units":units, "child_result_root":str(tmp_path),
        "resolved_snapshot_path":"resolved.json", "resolved_sha256":Evidence[str](value=hashlib.sha256(payload).hexdigest()),
        "artifact_records":[record]})
    result = validate_bundle(changed, evaluated_at=NOW)
    codes = {finding.code for finding in result.findings}
    assert "identity.resolved_units" in codes, codes
    assert "calibration.frozen_n_mismatch" in codes, codes
    assert result.validation_status.data_validity == "invalid"


def test_materialized_diagnostic_chunks_extend_resolved_day_without_redefining_it(repository_root, tmp_path):
    value = make_input(repository_root, config_name="exp-001-diagnostic-day.yaml")
    day = value.measurement_units[0]
    chunk = PhaseConfiguration(unit_id="probe-chunk-001", kind="probe_chunk", phase="multi_flow",
        measured_seconds=300, streams=4, parent_unit_id=day.unit_id)
    resolved, _ = resolve_campaign(repository_root / "configs/experiments/exp-001-diagnostic-day.yaml", repository_root)
    payload = json.dumps(resolved.model_dump(mode="json"), sort_keys=True).encode()
    (tmp_path / "resolved.json").write_bytes(payload)
    record = ArtifactRecord(artifact_id="resolved_snapshot", path="resolved.json", kind="resolved_configuration",
        producer="controller", unit_id=None, deployment_id=value.identity.deployment_id, closed=True,
        size_bytes={"value":len(payload)}, sha256={"value":hashlib.sha256(payload).hexdigest()}, retrieval_state="verified")
    extended = value.model_copy(update={"measurement_units":[day, chunk], "child_result_root":str(tmp_path),
        "resolved_snapshot_path":"resolved.json", "artifact_records":[record]})
    findings, raw = _resolved_snapshot_findings(extended)
    assert raw
    assert not any(item.code in {"identity.resolved_units", "identity.resolved_chunks"} for item in findings)
    invalid_chunk = chunk.model_copy(update={"parent_unit_id":"other-day"})
    extended = extended.model_copy(update={"measurement_units":[day, invalid_chunk]})
    findings, _ = _resolved_snapshot_findings(extended)
    assert any(item.code == "identity.resolved_chunks" for item in findings)


@pytest.mark.parametrize(("sent", "below"), [(949, True), (950, False), (951, False)])
def test_generation_ratio_uses_native_sent_and_runtime(repository_root, monkeypatch, sent, below):
    value = make_input(repository_root)
    counts = ProbeCounts(sent=Evidence[int](value=sent), received=Evidence[int](value=sent),
        dropped=Evidence[int](value=0), duplicated=Evidence[int](value=0), out_of_order=Evidence[int](value=0),
        skipped=Evidence[int](value=0), runtime_seconds=Evidence[float](value=1),
        reporting_scope="one Sockperf invocation", source=SourceRef("sockperf-log", "sockperf.log", "f" * 64))
    import cloud_network_benchmark.validation as module
    monkeypatch.setattr(module, "_parse_unit", lambda *_: (NativeParse(probe_counts=(counts,)), None))
    result = module.validate_bundle(value, evaluated_at=NOW)
    generation = result.metrics.value["units"][0]["probe_counts"][0]["generation_ratio"]["value"]
    assert generation == pytest.approx(sent / 1000)
    assert any(item.code == "generation.below_target" for item in result.findings) is below


def test_clock_alignment_boundary_is_separate_from_a_local_rtt(repository_root):
    value = make_input(repository_root)
    mappings = [ClockMapping(vm_role=role, monotonic_seconds=index, utc_timestamp=NOW,
                estimated_error_ms={"value": error}, synchronization_source={"value": "offline fixture"})
                for role, index, error in (("vm_a", 1, 100), ("vm_b", 2, 100))]
    timings = [item.model_copy(update={"clock_mappings": mappings}) for item in value.timings]
    result = validate_bundle(value.model_copy(update={"timings": timings}), evaluated_at=NOW)
    assert result.metrics.value["units"][0]["alignment"]["one_second_eligible"]["value"] is True
    assert rtt_statistics([2.5])["median_us"].value == 2.5
    excessive = [mappings[0], mappings[1].model_copy(update={"estimated_error_ms": Evidence[float](value=100.001)})]
    failed = validate_bundle(value.model_copy(update={"timings":[item.model_copy(update={"clock_mappings": excessive}) for item in value.timings]}), evaluated_at=NOW)
    assert failed.metrics.value["units"][0]["alignment"]["one_second_eligible"]["value"] is False


def test_materialized_artifact_integrity_and_symlink_escape_are_reported(repository_root, tmp_path):
    value = make_input(repository_root)
    unit = value.measurement_units[0]
    declaration = value.artifacts[f"{unit.unit_id}-sockperf"]
    target = tmp_path / declaration.path
    target.parent.mkdir(parents=True)
    data = (repository_root / "tests/fixtures/f08/native/sockperf.csv").read_bytes()
    target.write_bytes(data)
    record = ArtifactRecord(artifact_id=declaration.artifact_id, path=declaration.path, kind=declaration.kind,
        producer=declaration.producer, unit_id=declaration.unit_id, deployment_id=value.identity.deployment_id,
        closed=True, size_bytes={"value": len(data)}, sha256={"value": hashlib.sha256(data).hexdigest()},
        retrieval_state="verified")
    materialized = value.model_copy(update={"child_result_root": str(tmp_path), "artifact_records": [record]})
    findings, _, _ = _validate_artifacts(materialized)
    assert not any(item.code == "artifact.integrity" for item in findings)
    target.write_bytes(data + b"tampered")
    findings, _, _ = _validate_artifacts(materialized)
    assert any(item.code == "artifact.integrity" for item in findings)
    outside = tmp_path.parent / f"{tmp_path.name}-outside.csv"
    outside.write_bytes(data)
    target.unlink()
    target.symlink_to(outside)
    findings, _, _ = _validate_artifacts(materialized)
    assert any(item.code == "artifact.path_or_read" for item in findings)


def test_not_expected_artifact_and_execution_cleanup_failures_remain_distinct(repository_root):
    value = make_input(repository_root)
    declaration = value.artifacts["schedule_snapshot"]
    unexpected = ArtifactRecord(artifact_id="schedule_snapshot", path=declaration.path, kind=declaration.kind,
        producer=declaration.producer, unit_id=None, deployment_id=value.identity.deployment_id,
        closed=True, size_bytes={"value": 0}, sha256={"value":"a" * 64}, retrieval_state="verified")
    findings, _, _ = _validate_artifacts(value.model_copy(update={"artifact_records":[unexpected]}))
    assert any(item.code == "artifact.unexpected" for item in findings)
    failed = value.model_copy(update={"execution_state":"failed", "cleanup_state":"failed",
        "failure":FailureEvidence(category="measurement", message="offline fixture failure", occurred_at=NOW),
        "cleanup_failure":FailureEvidence(category="cleanup", message="offline cleanup failure", occurred_at=NOW)})
    result = validate_bundle(failed, evaluated_at=NOW)
    assert result.metrics.value["execution"]["execution_state"] == "failed"
    assert result.metrics.value["execution"]["cleanup_state"] == "failed"
    assert result.metrics.value["execution"]["primary_failure"]["message"] == "offline fixture failure"
    assert result.metrics.value["execution"]["cleanup_failure"]["message"] == "offline cleanup failure"
    assert result.metrics.value["execution"]["bundle_complete"] is False


def test_actual_command_options_accept_equivalent_spelling_and_reject_protocol_extras(repository_root):
    value = make_input(repository_root)
    unit = value.measurement_units[1]
    valid = CommandEvidence(schema_version=2,
        request={"argv":["iperf3", "-c", "10.0.0.2", "-p=5201", "-Z", "-C", "cubic", "-P", "1", "-t", "60", "-O", "5", "-l", "131072", "-i", "1", "--json", "--get-server-output"]},
        result={"outcome":"succeeded"}, producer="vm_a", deployment_id=value.identity.deployment_id,
        unit_id=unit.unit_id, binary_version={"value":"3.17.1"}, source_identity={"value":"fixture build"},
        monotonic_started={"value":1}, monotonic_finished={"value":61})
    findings = _command_findings(value.model_copy(update={"commands":[valid]}))
    assert not any(item.severity == "error" for item in findings)
    invalid = valid.model_copy(update={"request":{"argv":[*valid.request["argv"], "--reverse"]}})
    findings = _command_findings(value.model_copy(update={"commands":[invalid]}))
    assert any(item.code == "command.protocol_extra" for item in findings)
    udp = valid.model_copy(update={"request":{"argv":[*valid.request["argv"], "-u"]}})
    assert any(item.code == "command.protocol_extra" for item in
               _command_findings(value.model_copy(update={"commands":[udp]})))


def test_commands_require_private_targets_and_persistent_private_bound_listeners(repository_root):
    value = make_input(repository_root)
    unit = value.measurement_units[1]
    base = dict(schema_version=2, result={"outcome":"succeeded"}, deployment_id=value.identity.deployment_id,
        unit_id=unit.unit_id, binary_version={"value":"3.17.1"}, source_identity={"value":"fixture"},
        monotonic_started={"value":1}, monotonic_finished={"value":61})
    commands = [
        CommandEvidence(request={"argv":["sockperf", "server", "-i", "10.0.0.2", "-p", "11111"]},
            producer="vm_b", **base),
        CommandEvidence(request={"argv":["iperf3", "-s", "-B", "10.0.0.2", "-p", "5201", "--json-stream", "--forceflush"]},
            producer="vm_b", **base),
    ]
    findings = _command_findings(value.model_copy(update={"commands":commands}))
    assert not any(item.severity == "error" for item in findings)
    bad = commands[1].model_copy(update={"request":{"argv":["iperf3", "-s", "-B", "8.8.8.8", "-p", "5201"]}})
    findings = _command_findings(value.model_copy(update={"commands":[commands[0], bad]}))
    assert {item.code for item in findings} >= {"command.listener_bind", "command.streaming"}


def test_sequence_requires_actual_sockperf_runtime_and_ten_second_pause(repository_root):
    value = make_input(repository_root)
    first, second = value.measurement_units[:2]
    counts = ProbeCounts(sent=Evidence[int](value=65000), received=Evidence[int](value=65000),
        dropped=Evidence[int](value=0), duplicated=Evidence[int](value=0), out_of_order=Evidence[int](value=0),
        skipped=Evidence[int](value=0), runtime_seconds=Evidence[float](value=65),
        reporting_scope="one Sockperf invocation", source=SourceRef("sockperf-log", "sockperf.log", "d" * 64))
    too_early = value.timings[1].model_copy(update={"probe_test_started_at":Evidence[datetime](
        value=value.timings[0].probe_test_started_at.value + timedelta(seconds=74))})
    value = value.model_copy(update={"timings":[value.timings[0], too_early, *value.timings[2:]]})
    findings = _sequence_timing_findings(value, {first.unit_id:NativeParse(probe_counts=(counts,))})
    assert any(item.code == "timing.phase_pause" and item.unit_id == second.unit_id for item in findings)
