from datetime import datetime, timezone
from types import SimpleNamespace
import pytest

from cloud_network_benchmark.lifecycle_projection import _preparation_failure


def _failure(category, message, action_id):
    return SimpleNamespace(stage="readiness", category=category, message=message,
        action_id=action_id, occurred_at=datetime(2026, 11, 15, tzinfo=timezone.utc))


def test_bootstrap_failure_retains_bounded_redacted_action_diagnostics():
    from datetime import timedelta
    from cloud_network_benchmark.access import _action
    from cloud_network_benchmark.contracts.execution import CommandRequest, CommandResult

    secret = "/private/identity/known-host-secret"
    vm = SimpleNamespace(host="192.0.2.2", role="vm_b", authentication_reference=secret,
        host_key_reference=secret)
    request = CommandRequest(action_id="probe-4", action_type="ssh",
        argv=["ssh", "-i", secret, "probe"], cwd=".", timeout_seconds=3,
        redactions=[secret])
    started = datetime(2026, 11, 15, tzinfo=timezone.utc)
    response = CommandResult(action_id="probe-4", outcome="failed", started_at=started,
        finished_at=started + timedelta(seconds=1), duration_seconds=1, exit_code=1,
        stdout="stdout has " + secret, stderr=(secret + " ") * 4000,
        redactions=[secret])
    action = _action("probe-4", "ssh", vm, request, response)
    failure = _failure("readiness_probe_failed", "probe unavailable", "probe-4")
    bootstrap = SimpleNamespace(failure=failure, actions=[action])
    outcome = SimpleNamespace(stage="prepare", evidence={"bootstrap": bootstrap})

    selected, payload = _preparation_failure(SimpleNamespace(values={"prepare": outcome}))

    assert selected is failure
    assert payload["stage"] == "readiness"
    assert payload["category"] == "readiness_probe_failed"
    serialized = __import__("json").dumps(payload)
    assert secret not in serialized
    assert len(payload["actions"][0]["result"]["stderr"]) <= 4096
    assert payload["actions"][0]["request"]["argv"][2] == "[REDACTED]"
    assert "[REDACTED]" in payload["actions"][0]["result"]["stdout"]
    assert "stdin" not in serialized and "environment" not in serialized


def test_long_preparation_failure_keeps_compiler_error_tail_after_redaction():
    from datetime import timedelta
    from cloud_network_benchmark.access import _action
    from cloud_network_benchmark.contracts.execution import CommandRequest, CommandResult

    secret = "/private/build/token"
    vm = SimpleNamespace(host="192.0.2.2", role="vm_a", authentication_reference=secret,
        host_key_reference=secret)
    request = CommandRequest(action_id="build-4", action_type="ssh", argv=["ssh", "build"], cwd=".",
        timeout_seconds=3, redactions=[secret])
    started = datetime(2026, 11, 15, tzinfo=timezone.utc)
    short = "configure completed\n"
    long_stderr = ("prefix noise " * 500) + f"failed with {secret}\nfinal compiler error: missing libtoolize"
    response = CommandResult(action_id="build-4", outcome="failed", started_at=started,
        finished_at=started + timedelta(seconds=1), duration_seconds=1, exit_code=2,
        stdout=short, stderr=long_stderr, redactions=[secret])
    action = _action("build-4", "ssh", vm, request, response)
    failure = _failure("bootstrap_command_failed", "build failed", "build-4")
    result = SimpleNamespace(values={"prepare": SimpleNamespace(stage="prepare", evidence={
        "bootstrap": SimpleNamespace(failure=failure, actions=[action])})})

    _selected, payload = _preparation_failure(result)
    output = payload["actions"][0]["result"]
    assert output["stdout"] == short
    assert len(output["stderr"]) <= 4096
    assert "final compiler error: missing libtoolize" in output["stderr"]
    assert "...[truncated; retained final diagnostic characters]..." in output["stderr"]
    assert secret not in output["stderr"]


def test_readiness_failure_keeps_later_close_failure_separate():
    failure = _failure("readiness_deadline_exhausted", "deadline", "probe-8")
    readiness = SimpleNamespace(failure=failure, actions=[])
    prepare = SimpleNamespace(stage="prepare", evidence={"readiness": readiness})
    result = SimpleNamespace(values={"prepare": prepare, "close": object()},
        primary_failure="gcp prepare failed (adapter reported prepare unsuccessful)",
        secondary_failures=["gcp close failed (adapter reported close unsuccessful)"])

    selected, payload = _preparation_failure(result)

    assert selected.category == "readiness_deadline_exhausted"
    assert payload["actions"] == []
    assert result.primary_failure.startswith("gcp prepare failed")
    assert result.secondary_failures == ["gcp close failed (adapter reported close unsuccessful)"]


def test_successful_prepare_has_no_failure_artifact():
    outcome = SimpleNamespace(stage="prepare", evidence={"bootstrap": SimpleNamespace(failure=None)})
    assert _preparation_failure(SimpleNamespace(values={"prepare": outcome})) == (None, None)


def test_failed_measurement_handoff_keeps_only_bounded_redacted_diagnostics():
    import json
    from datetime import timedelta
    from cloud_network_benchmark.access import _action
    from cloud_network_benchmark.contracts.execution import CommandRequest, CommandResult
    from cloud_network_benchmark.lifecycle_adapters import RemoteGuestExecution
    from cloud_network_benchmark.lifecycle_projection import _measurement_failure
    from cloud_network_benchmark.orchestration import ProviderStageOutcome

    secret = "/private/identity/known-host-secret"
    vm = SimpleNamespace(host="192.0.2.2", role="vm_a", authentication_reference=secret,
        host_key_reference=secret)
    started = datetime(2026, 11, 17, tzinfo=timezone.utc)
    request = CommandRequest(action_id="f09-diagnostics-start", action_type="ssh",
        argv=["ssh", "-i", secret, "--token", "paid-token", "--client-secret", "client-secret-value",
            "--password=inline-password",
            "Bearer paid-bearer", "diagnostics"], cwd=".", timeout_seconds=3,
        redactions=[secret])
    response = CommandResult(action_id="f09-diagnostics-start", outcome="failed", started_at=started,
        finished_at=started + timedelta(seconds=1), duration_seconds=1, exit_code=1,
        stdout="private " + secret + "\n-----BEGIN PRIVATE KEY-----\nPEM-DATA\n-----END PRIVATE KEY-----",
        stderr=("token=secret-value Bearer raw-bearer " + secret + " ") * 500,
        redactions=[secret])
    action = _action("f09-diagnostics-start", "ssh", vm, request, response)
    process = SimpleNamespace(actions=[action])
    guest = SimpleNamespace(outcome="failed", executed_unit_ids=["idle_latency"],
        unexecuted_unit_ids=["single_flow"], guest_started_at=started,
        guest_finished_at=started + timedelta(seconds=1),
        failures=[{"category": "measurement", "message": "token=paid-secret"}])
    remote = RemoteGuestExecution(outcome=guest, failure="password=hunter2; diagnostic launch failed",
        actions=(action,), diagnostics={"vm_a": process})
    measure = ProviderStageOutcome("measure", False, remote)
    result = SimpleNamespace(primary_failure="gcp measure failed (adapter reported measure unsuccessful)",
        stage_times={"measure": {"started_at": started, "finished_at": started + timedelta(seconds=2)}},
        values={"measure": measure})

    failed_stage, payload = _measurement_failure(result)
    serialized = json.dumps(payload)
    assert failed_stage == "measure"
    assert payload["stages"][0]["failure"] == "password=[REDACTED]; diagnostic launch failed"
    assert payload["stages"][0]["guest_failures"] == ["measurement: token=[REDACTED]"]
    assert all(value not in serialized for value in (secret, "hunter2", "secret-value", "paid-token", "client-secret-value",
        "inline-password", "paid-bearer", "raw-bearer", "PEM-DATA"))
    assert "paid-secret" not in serialized
    assert len(payload["stages"][0]["actions"][0]["result"]["stderr"]) <= 4096
    assert payload["stages"][0]["actions"][0]["request"]["argv"][2] == "[REDACTED]"
    argv = payload["stages"][0]["actions"][0]["request"]["argv"]
    assert argv[4] == "[REDACTED]" and argv[5] == "--client-secret" and argv[6] == "[REDACTED]"
    assert argv[7] == "--password=[REDACTED]"
    assert "environment" not in serialized and "stdin" not in serialized
    assert "native" not in serialized and "commands" not in serialized


def test_measurement_callback_failure_retains_diagnostic_handles_without_exception_text():
    from cloud_network_benchmark.lifecycle_remote import _measurement_handoff_failure

    process = SimpleNamespace(actions=[{"action_id": "f09-diagnostics-start"}])
    attempt = SimpleNamespace(diagnostics={"vm_a": process})
    outcome = _measurement_handoff_failure(attempt, RuntimeError("token=paid-secret"))
    serialized = str(outcome.evidence)
    assert outcome.stage == "measure" and outcome.succeeded is False
    assert outcome.evidence["failure"] == "RuntimeError: remote measurement handoff failed"
    assert outcome.evidence["diagnostics"]["vm_a"] is process
    assert "paid-secret" not in serialized


def test_measurement_failure_projection_materializes_verified_declared_artifact(
        tmp_path, repository_root, frozen_clock, deterministic_token, fake_git_commits):
    import hashlib
    import json
    from datetime import timedelta
    from pathlib import Path
    from cloud_network_benchmark.contracts.admission import ReservationEvidence
    from cloud_network_benchmark.contracts.common import Evidence, StringEvidence
    from cloud_network_benchmark.lifecycle_projection import _sync_manifest, _measurement_failure, _validator_input
    from cloud_network_benchmark.manifests import (CampaignManifest, ChildRunManifest,
        StaticGitProvenance, initialize_campaign)

    config = repository_root / "configs/experiments/exp-001-multi-provider.yaml"
    initialized = initialize_campaign(config, repository_root, tmp_path / "results", frozen_clock,
        deterministic_token, StaticGitProvenance(fake_git_commits["implementation"]),
        StaticGitProvenance(fake_git_commits["design"]))
    ref = initialized.children[0]
    child = ChildRunManifest.model_validate_json(Path(ref.manifest_path).read_text())
    started = frozen_clock()
    remote = {"failure": "diagnostic start failed", "startup_failure": "remote diagnostics start failed",
        "diagnostics": {}, "actions": [{"action_id": "f09-diagnostics-mkdir-attempt",
            "action_type": "ssh", "vm_id": "192.0.2.2", "role": "vm_a",
            "request": {"argv": ["ssh", "mkdir"]},
            "result": {"outcome": "failed", "exit_code": 1, "stderr": "permission denied",
                "redactions_applied": True}}]}
    result = SimpleNamespace(execution_state="failed", cleanup_state="succeeded", interrupted=False,
        primary_failure=f"{child.provider} measure failed (adapter reported measure unsuccessful)",
        secondary_failures=[], values={
            "measure": SimpleNamespace(stage="measure", succeeded=False, evidence=remote),
            "close": SimpleNamespace(stage="close", succeeded=False, evidence={
                "diagnostics": {"vm_a": SimpleNamespace(actions=[], failures=("diagnostic close failed",))},
                "listeners": SimpleNamespace(actions=[], failures=("listener close failed",))})},
        stage_times={"measure": {"started_at": started, "finished_at": started + timedelta(seconds=2)},
            "cleanup": {"started_at": started + timedelta(seconds=3),
                "finished_at": started + timedelta(seconds=4)}})
    reservation = ReservationEvidence(schema_version=2, campaign_id=child.campaign_id,
        reservation_id=Evidence[str](value="projection-reservation"), state="reserved",
        reserved_cost_usd=Evidence[float](value=0.0), cap_started_at=Evidence(value=started),
        expires_at=Evidence(value=started + timedelta(minutes=20)), cap_start_policy_id=StringEvidence(value="offline"),
        source_path="fixture.json", source_sha256="a" * 64)

    updated = _sync_manifest(parent_path=Path(initialized.parent_manifest_path),
        child_path=Path(ref.manifest_path), root=Path(ref.result_path), child=child, result=result,
        reservation=reservation, evaluated_at=started + timedelta(seconds=4))

    path = Path(ref.result_path) / "metadata/measurement-failure.json"
    record = next(row for row in updated.artifact_records if row.artifact_id == "f09-measurement-failure")
    assert record.sha256.value == hashlib.sha256(path.read_bytes()).hexdigest()
    record.verify_materialized_file(Path(ref.result_path))
    payload = json.loads(path.read_text())
    assert payload["stage"] == "measure"
    assert payload["stages"][0]["startup_failure"] == "remote diagnostics start failed"
    assert payload["stages"][0]["actions"][0]["action_id"] == "f09-diagnostics-mkdir-attempt"
    assert payload["stages"][1]["failures"] == ["diagnostic close failed", "listener close failed"]
    parent = CampaignManifest.model_validate_json(Path(initialized.parent_manifest_path).read_text())
    validator_input, _builds, _receiver_commands, _clock_mappings = _validator_input(
        parent=parent, parent_path=Path(initialized.parent_manifest_path),
        child=updated, child_path=Path(ref.manifest_path), root=Path(ref.result_path), result=result,
        reservation=reservation)
    declaration = validator_input.artifacts["f09-measurement-failure"]
    assert (declaration.path, declaration.kind, declaration.producer, declaration.unit_id) == (
        "metadata/measurement-failure.json", "lifecycle_failure_diagnostics", "controller", None)
    assert _measurement_failure(SimpleNamespace(primary_failure=None, stage_times={}, values={})) == (None, None)
    assert updated.failure.category == "lifecycle_failure"
    assert updated.execution_state == "failed" and updated.cleanup_state == "succeeded"


@pytest.mark.parametrize("stage", ["bootstrap", "readiness"])
def test_failed_prepare_persists_checksummed_child_diagnostic(tmp_path, repository_root, frozen_clock,
        deterministic_token, fake_git_commits, stage):
    import hashlib
    import json
    from pathlib import Path

    from cloud_network_benchmark.contracts.admission import ReservationEvidence
    from cloud_network_benchmark.contracts.common import Evidence, StringEvidence
    from cloud_network_benchmark.lifecycle_projection import _sync_manifest
    from cloud_network_benchmark.manifests import (ChildRunManifest, StaticGitProvenance,
        initialize_campaign)

    config = repository_root / "configs/experiments/exp-001-multi-provider.yaml"
    initialized = initialize_campaign(config, repository_root, tmp_path / "results", frozen_clock,
        deterministic_token, StaticGitProvenance(fake_git_commits["implementation"]),
        StaticGitProvenance(fake_git_commits["design"]))
    ref = initialized.children[0]
    child = ChildRunManifest.model_validate_json(Path(ref.manifest_path).read_text())
    start = frozen_clock()
    failure = SimpleNamespace(stage=stage, category=f"{stage}_command_failed",
        message="bounded diagnostic", action_id="cmd-33",
        occurred_at=start + __import__("datetime").timedelta(seconds=10))
    def make_action(idx):
        return SimpleNamespace(model_dump=lambda **_: {
            "action_id": f"cmd-{idx}", "action_type": "ssh", "vm_id": "vm-a", "role": "vm_a",
            "request": {"action_id": f"cmd-{idx}", "action_type": "ssh", "argv": ["bootstrap", str(idx)]},
            "result": {"outcome": "failed" if idx == 33 else "succeeded", "exit_code": 2 if idx == 33 else 0,
                "stderr": "redacted failure" if idx == 33 else "", "redactions_applied": True}})
    actions = [make_action(idx) for idx in range(4, 34)]
    stage_result = SimpleNamespace(failure=failure, actions=actions, builds=[], listeners=[])
    prepare = SimpleNamespace(stage="prepare", succeeded=False, evidence={stage: stage_result})
    result = SimpleNamespace(execution_state="failed", cleanup_state="succeeded", interrupted=False,
        primary_failure="aws prepare failed (adapter reported prepare unsuccessful)",
        secondary_failures=["aws close failed (adapter reported close unsuccessful)"],
        values={"prepare": prepare}, stage_times={
            "prepare": {"started_at": start, "finished_at": start + __import__("datetime").timedelta(seconds=10)},
            "cleanup": {"started_at": start + __import__("datetime").timedelta(seconds=11),
                "finished_at": start + __import__("datetime").timedelta(seconds=12)}})
    reservation = ReservationEvidence(schema_version=2, campaign_id=child.campaign_id,
        reservation_id=Evidence[str](value="projection-reservation"), state="reserved",
        reserved_cost_usd=Evidence[float](value=0.0), cap_started_at=Evidence(value=start),
        expires_at=Evidence(value=start + __import__("datetime").timedelta(seconds=1200)),
        cap_start_policy_id=StringEvidence(value="offline-fixture"), source_path="fixture.json",
        source_sha256="a" * 64)

    updated = _sync_manifest(parent_path=Path(initialized.parent_manifest_path),
        child_path=Path(ref.manifest_path), root=Path(ref.result_path), child=child, result=result,
        reservation=reservation, evaluated_at=start + __import__("datetime").timedelta(seconds=12))

    diagnostic = Path(ref.result_path) / "metadata/preparation-failure.json"
    payload = json.loads(diagnostic.read_text())
    record = next(item for item in updated.artifact_records if item.artifact_id == "f09-preparation-failure")
    assert record.sha256.value == hashlib.sha256(diagnostic.read_bytes()).hexdigest()
    assert payload["category"] == updated.failure.category == f"{stage}_command_failed"
    assert payload["stage"] == stage
    assert payload["action_id"] == updated.failure.action_id == "cmd-33"
    assert len(payload["actions"]) == 24
    assert payload["omitted_action_count"] == 6
    assert payload["actions"][-1]["action_id"] == "cmd-33"
    assert payload["actions"][-1]["result"]["stderr"] == "redacted failure"
    assert payload["actions"][-1]["result"]["stderr"] == "redacted failure"
    assert "secret" not in diagnostic.read_text()
    assert result.primary_failure.startswith("aws prepare failed")
    assert result.secondary_failures == ["aws close failed (adapter reported close unsuccessful)"]


def test_failed_bootstrap_finalizes_with_declared_checksummed_diagnostic(tmp_path, repository_root, frozen_clock,
        deterministic_token, fake_git_commits):
    import hashlib
    import json
    from datetime import timedelta
    from pathlib import Path
    from types import SimpleNamespace

    from cloud_network_benchmark.access import AccessFailureEvidence, RemoteActionEvidence
    from cloud_network_benchmark.bootstrap import BootstrapResult
    from cloud_network_benchmark.contracts.admission import ReservationEvidence
    from cloud_network_benchmark.contracts.common import Evidence, IdentityContext, StringEvidence
    from cloud_network_benchmark.contracts.validation import ValidatorInput
    from cloud_network_benchmark.lifecycle_projection import finalize_production_batch
    from cloud_network_benchmark.manifests import StaticGitProvenance, initialize_campaign

    config = repository_root / "configs/experiments/exp-001-multi-provider.yaml"
    initialized = initialize_campaign(config, repository_root, tmp_path / "results", frozen_clock,
        deterministic_token, StaticGitProvenance(fake_git_commits["implementation"]),
        StaticGitProvenance(fake_git_commits["design"]))
    parent_path = Path(initialized.parent_manifest_path)
    from cloud_network_benchmark.manifests import CampaignManifest, ChildRunManifest
    parent = CampaignManifest.model_validate_json(parent_path.read_text())
    ref = parent.child_runs[0]
    child = ChildRunManifest.model_validate_json(Path(ref.manifest_path).read_text())
    identity = IdentityContext(experiment_id=child.experiment_id, config_id=child.config_id,
        campaign_id=child.campaign_id, observation_id=child.observation_id, run_id=child.run_id,
        deployment_id=child.deployment_id, provider=child.provider)
    at = frozen_clock()
    failure = AccessFailureEvidence(stage="bootstrap", category="bootstrap_command_failed",
        message="bootstrap failed", action_id="bootstrap-7", occurred_at=at + timedelta(seconds=10))
    action = RemoteActionEvidence(action_id="bootstrap-7", action_type="ssh", vm_id="vm-a", role="vm_a",
        request={"argv": ["cc", "--version"]},
        result={"outcome": "failed", "exit_code": 127, "stderr": "cc: not found", "redactions_applied": True})
    bootstrap = BootstrapResult(succeeded=False, identity=identity, deployment_id=child.deployment_id,
        actions=[action], failure=failure)
    prepare = SimpleNamespace(stage="prepare", succeeded=False, evidence={"bootstrap": bootstrap})
    result = SimpleNamespace(execution_state="failed", cleanup_state="succeeded", interrupted=False,
        primary_failure="aws prepare failed (adapter reported prepare unsuccessful)",
        secondary_failures=["aws close failed (adapter reported close unsuccessful)"],
        values={"prepare": prepare}, stage_times={
            "prepare": {"started_at": at, "finished_at": at + timedelta(seconds=10)},
            "cleanup": {"started_at": at + timedelta(seconds=11), "finished_at": at + timedelta(seconds=12)}})
    reservation = ReservationEvidence(schema_version=2, campaign_id=child.campaign_id,
        reservation_id=Evidence[str](value="projection-reservation"), state="reserved",
        reserved_cost_usd=Evidence[float](value=0.0), cap_started_at=Evidence(value=at),
        expires_at=Evidence(value=at + timedelta(minutes=20)), cap_start_policy_id=StringEvidence(value="offline-fixture"),
        source_path="fixture.json", source_sha256="a" * 64)
    lifecycle = SimpleNamespace(reservation_evidence=reservation, provider_results={"aws": result})

    finalized = finalize_production_batch(parent_path=parent_path, children={"aws": child}, parent=parent,
        lifecycle=lifecycle, repository_root=repository_root, evaluated_at=at + timedelta(seconds=12))

    diagnostic = Path(ref.result_path) / "metadata/preparation-failure.json"
    raw = diagnostic.read_bytes()
    projected_child = ChildRunManifest.model_validate_json(Path(ref.manifest_path).read_bytes())
    record = next(row for row in projected_child.artifact_records if row.artifact_id == "f09-preparation-failure")
    assert record.sha256.value == hashlib.sha256(raw).hexdigest()
    payload = json.loads(raw)
    assert payload["actions"][-1]["result"]["stderr"] == "cc: not found"
    assert finalized["aws"][0].validation_status.data_validity != "valid"
    saved = ValidatorInput.model_validate_json((Path(ref.result_path) / "validation/input.json").read_bytes())
    declaration = saved.artifacts["f09-preparation-failure"]
    assert (declaration.path, declaration.kind, declaration.producer, declaration.unit_id) == (
        "metadata/preparation-failure.json", "lifecycle_failure_diagnostics", "controller", None)
    assert saved.failure.category == "bootstrap_command_failed"
    assert saved.cleanup_state == "succeeded" and saved.execution_state == "failed"
    assert result.primary_failure.startswith("aws prepare failed")
    assert result.secondary_failures == ["aws close failed (adapter reported close unsuccessful)"]
    with pytest.raises(ValueError, match="materialized artifact hash does not match recorded SHA-256"):
        record.model_copy(update={"sha256": StringEvidence(value="0" * 64)}).verify_materialized_file(
            Path(ref.result_path))

    malformed = saved.model_dump(mode="python")
    malformed["artifacts"]["f09-preparation-failure"]["path"] = "metadata/wrong.json"
    with pytest.raises(ValueError, match="artifact evidence must match its declared input"):
        ValidatorInput.model_validate(malformed)
