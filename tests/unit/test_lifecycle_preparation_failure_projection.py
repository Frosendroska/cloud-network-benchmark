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
