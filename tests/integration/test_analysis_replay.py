from __future__ import annotations

import json

from cloud_network_benchmark.cli import main


def test_materialized_four_mode_inputs_are_revalidated_and_reported(repository_root, tmp_path,
        frozen_clock, deterministic_token, fake_git_commits, request):
    from tests.f12_support import materialize_f08_pair
    from cloud_network_benchmark.analysis import analyze_selection
    from cloud_network_benchmark.contracts.common import Evidence, GitSnapshot, StringEvidence

    snapshot = GitSnapshot(commit=StringEvidence(value="a" * 40), dirty=Evidence(value=False),
        diff_sha256=StringEvidence(value="b" * 64))
    for mode, expected_width in (("short_study", 1), ("acceptance", 1),
            ("calibration", 1), ("diagnostic_day", 60)):
        case = tmp_path / mode
        case.mkdir()
        _, expected, selection = materialize_f08_pair(repository_root, case, frozen_clock,
            deterministic_token, fake_git_commits, request, mode=mode)
        report = analyze_selection(selection, analysis_git=snapshot).payload
        assert report["runs"]
        assert {row["mode"] for row in report["runs"]} == {mode}
        assert {row["recorded_data_validity"] for row in report["runs"]} == {str(expected.validation_status.data_validity)}
        assert all(row["nominal_seconds"] == expected_width for row in report["bins"])
        assert all(row["width"] if "width" in row else True for row in report["bins"])


def test_analyze_cli_structural_error_returns_two_and_keeps_legacy_cli(repository_root, tmp_path, capsys):
    bad = tmp_path / "selection.json"
    bad.write_text(json.dumps({"format_version": 1, "runs": []}), encoding="utf-8")
    result = main(["analyze", "--selection", str(bad), "--output", str(tmp_path / "report")])
    assert result == 2
    assert "runs must be a nonempty list" in capsys.readouterr().err
    assert main(["validate", "--config", "configs/experiments/exp-001-multi-provider.yaml", "--format", "json"]) == 0


def test_materialized_cli_report_replays_all_files_for_reordered_selection(repository_root, tmp_path,
        frozen_clock, deterministic_token, fake_git_commits, request):
    import hashlib
    from tests.f12_support import materialize_f08_pair

    entries = []
    for index, provider in enumerate(("aws", "azure")):
        _, _, selection_path = materialize_f08_pair(repository_root, tmp_path / f"run-{index}",
            frozen_clock, deterministic_token, fake_git_commits, request, provider=provider)
        item = json.loads(selection_path.read_text(encoding="utf-8"))["runs"][0]
        entries.append({key: str((selection_path.parent / value).resolve()) for key, value in item.items()})
    first, second = tmp_path / "first-selection.json", tmp_path / "second-selection.json"
    first.write_text(json.dumps({"format_version": 1, "runs": entries}), encoding="utf-8")
    second.write_text(json.dumps({"format_version": 1, "runs": list(reversed(entries))}), encoding="utf-8")
    source_hashes = {path: hashlib.sha256(open(path, "rb").read()).hexdigest()
        for item in entries for path in item.values()}
    out_a, out_b = tmp_path / "first-report", tmp_path / "second-report"
    assert main(["analyze", "--selection", str(first), "--output", str(out_a)]) == 0
    assert main(["analyze", "--selection", str(second), "--output", str(out_b)]) == 0
    files_a = {path.relative_to(out_a): path.read_bytes() for path in out_a.rglob("*") if path.is_file()}
    files_b = {path.relative_to(out_b): path.read_bytes() for path in out_b.rglob("*") if path.is_file()}
    assert files_a == files_b
    after_hashes = {path: hashlib.sha256(open(path, "rb").read()).hexdigest() for path in source_hashes}
    assert source_hashes == after_hashes


def test_cli_analyze_requires_local_selection_and_output():
    from cloud_network_benchmark.cli import _parser

    try:
        _parser().parse_args(["analyze", "--selection", "selection.json"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("analyze requires an output directory")
