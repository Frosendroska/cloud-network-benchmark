from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cloud_network_benchmark.lifecycle_cli import main, preview_lifecycle
from cloud_network_benchmark.orchestration import SavedLifecycleRecovery, write_recovery_journal
from cloud_network_benchmark.cli import main as root_main


def test_lifecycle_module_help_is_available(capsys):
    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    assert "preview" in capsys.readouterr().out


def test_preview_is_local_and_reports_unresolved_admission(repository_root):
    report = preview_lifecycle(config_path=repository_root / "configs/tests/exp-900-single-provider.yaml",
        repository_root=repository_root, schedule_path=None, row_id=None,
        cost_inputs_path=repository_root / "tests/fixtures/f11/cost-inputs.json")
    assert report["external_calls"] == 0
    assert report["paid_eligible"] is False
    assert "f11_reservation_not_evaluated" in report["reason_codes"]


def test_run_refuses_without_paid_prerequisites_and_has_no_side_effects(repository_root, tmp_path, capsys):
    runtime = tmp_path / "runtime.json"; runtime.write_text(json.dumps({"scope": "references-only"}))
    results = tmp_path / "results"
    private = tmp_path / "private"
    code = main(["run", "--config", str(repository_root / "configs/tests/exp-900-single-provider.yaml"),
        "--cost-inputs", str(repository_root / "tests/fixtures/f11/cost-inputs.json"),
        "--runtime-config", str(runtime), "--results-root", str(results), "--private-root", str(private),
        "--controller-session", "session-f09", "--execution-scope", "paid_request", "--format", "json"])
    captured = capsys.readouterr()
    report = json.loads(captured.err)
    assert code == 2
    assert report["status"] == "invalid"
    assert report["reason_codes"] == ["invalid_lifecycle_request"]
    assert not results.exists() and not private.exists()


def test_cli_requires_explicit_paid_scope_for_run():
    try:
        main(["run", "--help"])
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("argparse help must exit successfully")


def test_root_cli_keeps_existing_commands_and_delegates_lifecycle_help(capsys):
    for args, expected in ((["--help"], ("validate", "resolve", "dry-run", "init", "analyze", "lifecycle")),
                           (["lifecycle", "--help"], ("preview", "run", "cleanup", "recommend-common-n", "replay-archives"))):
        try:
            root_main(args)
        except SystemExit as exc:
            assert exc.code == 0
        output = capsys.readouterr().out
        assert all(command in output for command in expected)


def test_root_lifecycle_run_uses_isolated_safe_refusal_without_side_effects(repository_root, tmp_path, capsys):
    runtime = tmp_path / "runtime.json"
    runtime.write_text(json.dumps({"scope": "references-only"}))
    results = tmp_path / "results"
    private = tmp_path / "private"
    code = root_main(["lifecycle", "run", "--config",
        str(repository_root / "configs/tests/exp-900-single-provider.yaml"),
        "--cost-inputs", str(repository_root / "tests/fixtures/f11/cost-inputs.json"),
        "--runtime-config", str(runtime), "--results-root", str(results), "--private-root", str(private),
        "--controller-session", "session-root-f09", "--execution-scope", "paid_request", "--format", "json"])
    captured = capsys.readouterr()
    report = json.loads(captured.err)
    assert code == 2 and report["status"] == "invalid" and report["external_calls"] == 0
    assert not results.exists() and not private.exists()


def test_recovery_journal_is_private_exclusive_and_cli_cleanup_never_resumes(tmp_path, capsys):
    recovery_path = tmp_path / "private" / "recovery.json"
    recovery = SavedLifecycleRecovery(schema_version=1, attempt_id="attempt-1", controller_session_id="old",
        providers=[{"provider":"aws", "deployment_id":"run-aws-deployment",
            "journal_reference":"private/aws-journal.json", "expiry_registered":True,
            "creation_may_have_started":True, "cleanup_open":True}])
    write_recovery_journal(recovery_path, recovery)
    assert recovery_path.stat().st_mode & 0o777 == 0o600
    try:
        write_recovery_journal(recovery_path, recovery)
    except FileExistsError:
        pass
    else:
        raise AssertionError("recovery journal must be exclusive")
    actions = []
    code = main(["cleanup", "--recovery", str(recovery_path), "--runtime-config", "unused.json",
        "--private-root", str(tmp_path / "private"), "--controller-session", "new", "--format", "json"],
        cleanup_actions={"aws":lambda record: actions.append(record.provider) or {"delete_requested":True}},
        reconcile=lambda record, result:{"absence_proved":True, "billable_resources_present":False,
            "cleanup_open":False, "billing_final":False, "incurred_usd":None, "provider":record.provider})
    report = json.loads(capsys.readouterr().out)
    assert code == 0 and report["status"] == "complete" and report["execution"] == "not_resumed"
    assert actions == ["aws"] and report["external_calls"] == 0
    saved = SavedLifecycleRecovery.validate_unique(json.loads(recovery_path.read_text()))
    assert saved.providers[0].cleanup_open is False
    assert saved.providers[0].creation_may_have_started is False
    assert saved.providers[0].expiry_registered is False


def test_common_n_command_exposes_existing_recommendation_without_freezing_config(tmp_path, capsys):
    results = tmp_path / "results.json"; results.write_text("[]")
    code = main(["recommend-common-n", "--validation-results", str(results), "--format", "json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 1 and report["status"] == "pending"
    assert report["common_n"]["value"] is None and report["config_mutated"] is False


def _replay_cli_context(tmp_path, *, archives=None):
    repository = tmp_path / "repo"
    manifests = repository / "results" / "manifests"
    manifests.mkdir(parents=True)
    parent_path = manifests / "parent.json"
    parent_path.write_text(json.dumps({"campaign_id": "generated-campaign-91"}), encoding="utf-8")
    results_root = repository / "private-results" / "P0"
    (results_root / "raw" / "run-aws-91").mkdir(parents=True)
    capsule_path = tmp_path / "capsule.json"
    capsule = {"schema_version": 2, "status": "archive_pending_replay",
        "parent_path": "results/manifests/parent.json",
        "parent_manifest": {"campaign_id": "generated-campaign-91"},
        "result_paths": {"aws": "private-results/P0/raw/run-aws-91"},
        "child_manifests": {"aws": {"run_id": "run-aws-91"}},
        "archives": archives or {"aws": {"manifest": {}}}}
    capsule_path.write_text(json.dumps(capsule, sort_keys=True), encoding="utf-8")
    capsule_sha256 = hashlib.sha256(capsule_path.read_bytes()).hexdigest()
    private = tmp_path / "private"
    private.mkdir()
    stage_path, journal_path = private / "prepared-stage.json", private / "journal.json"
    stage = {"stage_id": "P0", "repository_root": str(repository),
        "results_root": str(results_root), "entries": [{
        "entry_id": "V-S1", "parent_manifest_path": str(parent_path),
        "run_ids": {"aws": "run-aws-91"}}]}
    stage_path.write_text(json.dumps(stage), encoding="utf-8")
    journal = {"stage_id": "P0", "entries": {"V-S1": {"state": "archive_pending_validation",
        "result": {"cleanup_open": False, "replay_capsule": {"path": str(capsule_path),
            "sha256": capsule_sha256}}}}}
    journal_path.write_text(json.dumps(journal), encoding="utf-8")
    staging, restore = tmp_path / "staging", tmp_path / "restore"
    staging.mkdir(); restore.mkdir()
    runtime_path = tmp_path / "runtime.json"
    runtime_path.write_text("{}", encoding="utf-8")
    runtime = {"archive_sink": {"bucket": "fixture-bucket", "staging_directory": staging,
            "maximum_staging_bytes": 4096, "minimum_controller_free_bytes": 512},
        "observer": {"stage_id": "P0", "campaign_id": "P0", "entry_id": "V-S1",
            "workspace": "/var/lib/cnb/work", "prepared_stage_path": stage_path,
            "journal_path": journal_path}}
    return {"repository": repository, "runtime_path": runtime_path, "runtime": runtime,
        "capsule_path": capsule_path, "capsule_sha256": capsule_sha256,
        "restore_path": restore, "staging": staging, "results_root": results_root}


def _replay_cli_args(context, *extra):
    return ["replay-archives", "--capsule", str(context["capsule_path"]),
        "--capsule-sha256", context["capsule_sha256"], "--runtime-config", str(context["runtime_path"]),
        "--restore-path", str(context["restore_path"]), *extra]


def test_replay_cli_default_retains_scratch_and_keeps_existing_export_mode(tmp_path, monkeypatch, capsys):
    from cloud_network_benchmark import archive_replay, gcs_archive, lifecycle_cli
    context = _replay_cli_context(tmp_path)
    monkeypatch.chdir(context["repository"])
    monkeypatch.setattr(lifecycle_cli, "load_runtime_config", lambda *_args, **_kwargs: context["runtime"])
    called = {}
    monkeypatch.setattr(gcs_archive, "google_cloud_storage_backend",
        lambda _sink: pytest.fail("default route defers backend until replay API calls it"))
    def restore(**kwargs):
        called.update(kwargs)
        return {"status": "replayed"}
    monkeypatch.setattr(archive_replay, "restore_and_finalize_batch_capsule", restore)
    code = main(_replay_cli_args(context))
    capsys.readouterr()
    assert code == 0
    assert called["backend"] is None and callable(called["backend_factory"])
    assert called["minimum_free_bytes"] == 512
    assert "prune_verified_scratch" not in called
    assert "closed_export_entry_id" not in called


def test_replay_cli_opt_in_binds_registered_entry_and_explicit_quota_receipt(
        tmp_path, monkeypatch, capsys):
    from cloud_network_benchmark import archive_replay, gcs_archive, lifecycle_cli
    context = _replay_cli_context(tmp_path)
    quota_path = tmp_path / "fresh-quota.json"
    quota = {"measured_free_bytes": 50_000, "measured_at": datetime.now(timezone.utc).isoformat(),
        "metadata_headroom_bytes": 4096, "projected_other_growth_bytes": 8192,
        "reserve_bytes": 512 * 1024 * 1024}
    quota_path.write_text(json.dumps(quota), encoding="utf-8")
    quota_sha256 = hashlib.sha256(quota_path.read_bytes()).hexdigest()
    monkeypatch.chdir(context["repository"])
    monkeypatch.setattr(lifecycle_cli, "load_runtime_config", lambda *_args, **_kwargs: context["runtime"])
    monkeypatch.setattr(gcs_archive, "google_cloud_storage_backend",
        lambda _sink: pytest.fail("backend must remain lazy in CLI handoff test"))
    called = {}
    monkeypatch.setattr(archive_replay, "restore_and_finalize_batch_capsule",
        lambda **kwargs: called.update(kwargs) or {"status": "replayed"})
    code = main(_replay_cli_args(context, "--prune-verified-scratch", "--quota-evidence",
        str(quota_path), "--quota-evidence-sha256", quota_sha256))
    capsys.readouterr()
    assert code == 0
    assert called["prune_verified_scratch"] is True
    assert called["closed_export_entry_id"] == "V-S1"
    assert called["closed_export_results_root"] == context["results_root"]
    assert called["closed_export_entry_id"] != "generated-campaign-91"
    assert called["controller_quota_free_bytes"] == quota["measured_free_bytes"]
    assert called["metadata_headroom_bytes"] == quota["metadata_headroom_bytes"]
    assert called["projected_other_growth_bytes"] == quota["projected_other_growth_bytes"]
    assert called["minimum_free_bytes"] == quota["reserve_bytes"]


def test_replay_cli_rejects_reserve_below_frozen_runtime_minimum_before_backend(
        tmp_path, monkeypatch, capsys):
    from cloud_network_benchmark import gcs_archive, lifecycle_cli
    context = _replay_cli_context(tmp_path)
    quota_path = tmp_path / "low-reserve.json"
    quota = {"measured_free_bytes": 50_000, "measured_at": datetime.now(timezone.utc).isoformat(),
        "metadata_headroom_bytes": 4096, "projected_other_growth_bytes": 8192, "reserve_bytes": 1}
    quota_path.write_text(json.dumps(quota), encoding="utf-8")
    quota_sha256 = hashlib.sha256(quota_path.read_bytes()).hexdigest()
    monkeypatch.chdir(context["repository"])
    monkeypatch.setattr(lifecycle_cli, "load_runtime_config", lambda *_args, **_kwargs: context["runtime"])
    monkeypatch.setattr(gcs_archive, "google_cloud_storage_backend",
        lambda _sink: pytest.fail("under-floor reserve must be rejected before backend creation"))
    code = main(_replay_cli_args(context, "--prune-verified-scratch", "--quota-evidence",
        str(quota_path), "--quota-evidence-sha256", quota_sha256))
    error = json.loads(capsys.readouterr().err)
    assert code == 2 and "below the frozen runtime controller minimum" in error["error"]


def test_replay_cli_rejects_missing_fresh_quota_before_backend(tmp_path, monkeypatch, capsys):
    from cloud_network_benchmark import gcs_archive, lifecycle_cli
    context = _replay_cli_context(tmp_path)
    monkeypatch.chdir(context["repository"])
    monkeypatch.setattr(lifecycle_cli, "load_runtime_config", lambda *_args, **_kwargs: context["runtime"])
    monkeypatch.setattr(gcs_archive, "google_cloud_storage_backend",
        lambda _sink: pytest.fail("missing quota must be rejected before backend creation"))
    code = main(_replay_cli_args(context, "--prune-verified-scratch"))
    error = json.loads(capsys.readouterr().err)
    assert code == 2 and "quota-evidence" in error["error"]


@pytest.mark.parametrize("freshness", ["stale", "underquota"])
def test_replay_cli_rejects_stale_or_underquota_evidence_before_backend(
        tmp_path, monkeypatch, capsys, freshness):
    from cloud_network_benchmark import gcs_archive, lifecycle_cli
    archives = {"aws": {"manifest": {"closed_inventory": {}, "roles": [
        {"role": "vm_a", "archive_bytes": 1}, {"role": "vm_b", "archive_bytes": 1}]}}}
    if freshness == "underquota":
        from cloud_network_benchmark.contracts.artifacts import ArtifactInput
        from cloud_network_benchmark.contracts.common import ArtifactExpectation, VmRole
        from cloud_network_benchmark.retrieval import RetrievalItem, RetrievalManifest
        now = datetime.now(timezone.utc)
        declaration = ArtifactInput(artifact_id="native", path="native.csv", kind="sockperf-csv",
            producer=VmRole.VM_A, expectation=ArtifactExpectation.REQUIRED)
        item = RetrievalItem(artifact_id="native", deployment_id="dep", source_vm="vm-a",
            source_role="vm_a", source_path="/work/native.csv", destination_path="native.csv",
            kind="sockperf-csv", expected_size_bytes=1000, expected_sha256="a" * 64,
            timeout_seconds=1)
        closed = RetrievalManifest(identity={}, deployment_id="dep", remote_workspace="/work",
            declarations=[declaration], items=[item], measurement_ended_at=now,
            producers_closed_at=now, closure_evidence="closed fixture")
        archives["aws"]["manifest"]["closed_inventory"] = closed.model_dump(mode="json")
    context = _replay_cli_context(tmp_path, archives=archives)
    quota_path = tmp_path / "quota.json"
    quota = {"measured_free_bytes": 1000, "measured_at": "2020-01-01T00:00:00+00:00"
            if freshness == "stale" else datetime.now(timezone.utc).isoformat(),
        "metadata_headroom_bytes": 100, "projected_other_growth_bytes": 100,
            "reserve_bytes": 512}
    quota_path.write_text(json.dumps(quota), encoding="utf-8")
    quota_sha256 = hashlib.sha256(quota_path.read_bytes()).hexdigest()
    monkeypatch.chdir(context["repository"])
    monkeypatch.setattr(lifecycle_cli, "load_runtime_config", lambda *_args, **_kwargs: context["runtime"])
    monkeypatch.setattr(gcs_archive, "google_cloud_storage_backend",
        lambda _sink: pytest.fail(f"{freshness} quota must fail before backend creation"))
    code = main(_replay_cli_args(context, "--prune-verified-scratch", "--quota-evidence",
        str(quota_path), "--quota-evidence-sha256", quota_sha256))
    error = json.loads(capsys.readouterr().err)
    assert code == 2
    if freshness == "stale":
        assert "15 minutes" in error["error"]
    else:
        assert "user quota cannot cover" in error["error"]
    assert list(context["staging"].iterdir()) == []
    assert list(context["restore_path"].iterdir()) == []
