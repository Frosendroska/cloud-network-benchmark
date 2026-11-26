from __future__ import annotations

import pytest

from cloud_network_benchmark.orchestration import LifecycleContext, ProviderHooks, ProviderStageOutcome, execute_provider_workflows
from tests.fixtures.f09.helpers import FakeClock


STAGES = ("protect", "claim", "create", "prepare", "stabilize", "measure", "close", "retrieve", "cleanup")


def make_hooks(provider, fail_at=None, *, failures=()):
    def action(stage):
        def run():
            if stage == fail_at or stage in failures:
                raise RuntimeError(f"synthetic {provider} {stage}")
            return ProviderStageOutcome(stage, True, {"synthetic": True, "stage": stage})
        return run
    return ProviderHooks(provider, *(action(stage) for stage in STAGES))


@pytest.mark.parametrize("provider", ["aws", "azure", "gcp"])
@pytest.mark.parametrize("stage", STAGES)
def test_stage_failure_is_recorded_and_all_protected_peers_are_finalized(provider, stage):
    peers = {name: make_hooks(name, fail_at=stage if name == provider else None)
             for name in ("aws", "azure", "gcp")}
    outcome = execute_provider_workflows(peers,
        context=LifecycleContext("session", 1, 100000, 100000), clock=FakeClock())
    assert not outcome.completed
    assert outcome.primary_failure is not None
    for name, item in outcome.provider_results.items():
        assert item.cleanup_state == ("failed" if stage == "cleanup" and name == provider else "succeeded")
    if stage == "protect":
        assert not any(item.values.get("create") for item in outcome.provider_results.values())
    assert sum("create" in item.values for item in outcome.provider_results.values()) <= 3


def test_primary_failure_survives_cleanup_and_records_secondary_failure():
    peers = {"aws": make_hooks("aws", fail_at="measure", failures=("cleanup",)),
             "gcp": make_hooks("gcp")}
    outcome = execute_provider_workflows(peers,
        context=LifecycleContext("session", 1, 100000, 100000), clock=FakeClock())
    aws = outcome.provider_results["aws"]
    assert aws.primary_failure == "aws measure failed (RuntimeError)"
    assert "aws cleanup failed (RuntimeError)" in aws.secondary_failures
    assert aws.cleanup_state == "failed"
    assert outcome.provider_results["gcp"].cleanup_state == "succeeded"


def test_changed_controller_session_prevents_attempt_reuse():
    context = LifecycleContext("original-session", 1, 100, 120)
    with pytest.raises(ValueError, match="cannot resume"):
        context.assert_session("new-session")


def test_real_failed_prepare_runs_cleanup_and_persists_cause_in_child_manifest(tmp_path):
    from datetime import datetime, timezone
    import json
    from pathlib import Path
    from cloud_network_benchmark.lifecycle_adapters import prepare_remote_pair
    from cloud_network_benchmark.lifecycle_projection import _sync_manifest
    from cloud_network_benchmark.manifests import initialize_campaign, ChildRunManifest
    from cloud_network_benchmark.lifecycle_runtime import load_initialized_campaign
    from cloud_network_benchmark.contracts.execution import CommandResult
    from tests.fixtures.f02r.support import connection, bootstrap_config, deadline
    from tests.unit.test_aws_provider import ROOT

    config = ROOT / "configs/tests/exp-900-single-provider.yaml"
    initialized = initialize_campaign(config, ROOT, tmp_path / "results",
        clock=lambda: datetime(2026, 11, 26, tzinfo=timezone.utc), token_source=lambda: "evidence")
    parent_path = Path(initialized.parent_manifest_path)
    parent, children = load_initialized_campaign(parent_path, repository_root=ROOT, config_path=config)
    child = children["aws"]
    connected = connection(tmp_path)
    identity = connected.identity.model_copy(update={"campaign_id": child.campaign_id,
        "observation_id": child.observation_id, "run_id": child.run_id,
        "deployment_id": child.deployment_id, "experiment_id": child.experiment_id,
        "config_id": child.config_id})
    connected = connected.model_copy(update={"identity": identity, "run_id": child.run_id,
        "deployment_id": child.deployment_id, "provider": "aws"})
    remote_deadline = deadline()
    remote_calls = []

    class FailingBootstrapRunner:
        def run(self, request):
            remote_calls.append(request.action_id)
            now = datetime.now(timezone.utc)
            return CommandResult(
                action_id=request.action_id, outcome="failed", started_at=now, finished_at=now,
                duration_seconds=0, exit_code=255, stdout="",
                stderr="Permission denied during synthetic bootstrap; Authorization: Bearer synthetic-token")

        def run_bounded_copy(self, *_args, **_kwargs):
            raise AssertionError("prepare failure must occur before copy")

    runner = FailingBootstrapRunner()
    called = []
    def outcome(stage):
        def callback():
            called.append(stage)
            return ProviderStageOutcome(stage, True, {"synthetic": True})
        return callback

    hooks = ProviderHooks("aws", outcome("protect"), outcome("claim"), outcome("create"),
        lambda: prepare_remote_pair(connected, bootstrap_config=bootstrap_config(),
            readiness_config=object(), runner=runner, deadline=remote_deadline,
            source_root=ROOT, source_revision="a"*40, remote_root="/var/lib/cnb",
            clock=lambda: 100.0), outcome("stabilize"), outcome("measure"),
        outcome("close"), outcome("retrieve"), outcome("cleanup"))
    class Clock:
        @staticmethod
        def monotonic(): return 100.0
        @staticmethod
        def utc_now(): return datetime.now(timezone.utc)

    lifecycle = execute_provider_workflows({"aws": hooks},
        context=LifecycleContext("synthetic-prepare-failure", 1, 100000, 100000), clock=Clock())
    result = lifecycle.provider_results["aws"]
    assert not lifecycle.completed and result.execution_state == "failed"
    assert result.cleanup_state == "succeeded" and "cleanup" in called
    assert remote_calls and result.values["prepare"].succeeded is False
    assert result.primary_failure == "aws prepare failed (adapter reported prepare unsuccessful)"

    child_ref = next(ref for ref in parent.child_runs if str(ref.provider) == "aws")
    child_path, result_root = Path(child_ref.manifest_path), Path(child_ref.result_path)
    if not child_path.is_absolute(): child_path = ROOT / child_path
    if not result_root.is_absolute(): result_root = ROOT / result_root
    _sync_manifest(parent_path=parent_path, child_path=child_path, root=result_root, child=child,
        result=result, reservation=None, evaluated_at=Clock.utc_now())
    saved = ChildRunManifest.model_validate_json(child_path.read_text(encoding="utf-8"))
    evidence = saved.failure.details["prepare_failure_evidence"]
    assert evidence["run_id"] == child.run_id and evidence["provider"] == "aws"
    assert evidence["stage"] == "prepare" and evidence["failures"][0]["category"]
    assert "Permission denied" in evidence["failures"][0]["diagnostic"]
    encoded = json.dumps(evidence)
    assert "synthetic-token" not in encoded and "Bearer" not in encoded
