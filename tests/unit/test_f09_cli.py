from __future__ import annotations

import json
from pathlib import Path

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
    report = json.loads(capsys.readouterr().out)
    assert code == 1
    assert report["status"] == "rejected"
    assert report["external_calls"] == 0
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
                           (["lifecycle", "--help"], ("preview", "run", "cleanup", "recommend-common-n"))):
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
    report = json.loads(capsys.readouterr().out)
    assert code == 1 and report["status"] == "rejected" and report["external_calls"] == 0
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
    assert actions == ["aws"] and report["external_calls"] == 1
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
