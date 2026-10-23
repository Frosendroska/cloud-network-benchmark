from __future__ import annotations

import json
from pathlib import Path

from cloud_network_benchmark.lifecycle_cli import main, preview_lifecycle


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
