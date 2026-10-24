from __future__ import annotations

import json

from cloud_network_benchmark.cli import main


def test_materialized_four_mode_inputs_are_revalidated_and_reported(repository_root, tmp_path,
        frozen_clock, deterministic_token, fake_git_commits, request):
    from tests.f12_support import materialize_f08_pair

    for mode, expected_width in (("short_study", 1), ("acceptance", 1),
            ("calibration", 1), ("diagnostic_day", 60)):
        case = tmp_path / mode
        case.mkdir()
        _, expected, selection = materialize_f08_pair(repository_root, case, frozen_clock,
            deterministic_token, fake_git_commits, request, mode=mode)
        output = case / "report"
        assert main(["analyze", "--selection", str(selection), "--output", str(output)]) == 0
        report = json.loads((output / "report.json").read_text(encoding="utf-8"))
        assert report["runs"]
        assert {row["mode"] for row in report["runs"]} == {mode}
        assert {row["recorded_data_validity"] for row in report["runs"]} == {str(expected.validation_status.data_validity)}
        assert all(row["nominal_seconds"] == expected_width for row in report["bins"])
        assert (output / "index.html").is_file()
        assert (output / "figures" / "time-series.png").is_file()
        assert (output / "figures" / "receiver-time-series.svg").is_file()
        assert all("receiver" in row and "source_refs" in row for row in report["bins"])
        if mode == "diagnostic_day":
            context = report["day_load_context"][0]
            assert context["initial_300_seconds"].get("n", 0) > 0
            assert context["subsequent_supported_bins"].get("n", 0) > 0
            assert context["idle_baseline"]["unavailable_reason"]
            assert {row["axis"] for row in report["bins"]} == {
                "UTC-mapped seconds from continuous-day origin"}
            assert all(row["chunk_id"] for row in report["bins"]
                if row["kind"] == "probe_chunk")


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


def test_s1_s3_scenario_distributions_are_kept_distinct_and_directional(repository_root, tmp_path,
        frozen_clock, deterministic_token, fake_git_commits, request, monkeypatch):
    import yaml
    from tests.f12_support import materialize_f08_pair
    from cloud_network_benchmark.analysis import analyze_selection
    from cloud_network_benchmark.analysis_report import write_analysis
    from cloud_network_benchmark.contracts.common import Evidence, GitSnapshot, StringEvidence

    # The reusable F08 fixture is pinned to an EXP-001 summary assertion/P0
    # schedule row. Adapt those fixture-only assumptions while preserving the
    # real S3 config identity and measurement/placement evidence.
    s3_source = repository_root / "configs/experiments/exp-003-placement-optimization.yaml"
    s3_config = yaml.safe_load(s3_source.read_text(encoding="utf-8"))
    fixture_config = repository_root / "configs/experiments/f12-placement-test.yaml"
    fixture_config.write_text(yaml.safe_dump(s3_config, sort_keys=False), encoding="utf-8")
    request.addfinalizer(lambda: fixture_config.unlink(missing_ok=True))
    import tests.integration.test_native_validation_replay as f08_replay
    actual_schedule_model = f08_replay.ScheduleEntry
    actual_summarize = f08_replay.summarize_validation
    def fixture_summary(result):
        return "EXP-001 fixture compatibility marker\n" + actual_summarize(result)
    monkeypatch.setattr(f08_replay, "summarize_validation", fixture_summary)
    def configured_schedule(**kwargs):
        if kwargs.get("config_id") == "exp-003-short":
            kwargs["priority"] = "P1"
        return actual_schedule_model(**kwargs)
    monkeypatch.setattr(f08_replay, "ScheduleEntry", configured_schedule)
    entries = []
    for name, config_name in (("s1", "exp-001-multi-provider.yaml"),
            ("s3", fixture_config.name)):
        _, result, selection = materialize_f08_pair(repository_root, tmp_path / name, frozen_clock,
            deterministic_token, fake_git_commits, request, config_name=config_name)
        assert result.validation_status.data_validity == "valid"
        item = json.loads(selection.read_text(encoding="utf-8"))["runs"][0]
        entries.append({key: str((selection.parent / value).resolve()) for key, value in item.items()})
    combined = tmp_path / "s1-s3-selection.json"
    combined.write_text(json.dumps({"format_version": 1, "runs": entries}), encoding="utf-8")
    snapshot = GitSnapshot(commit=StringEvidence(value="a" * 40), dirty=Evidence(value=False),
        diff_sha256=StringEvidence(value="b" * 64))
    analyzed = analyze_selection(combined, analysis_git=snapshot)
    report = analyzed.payload
    paired = [row for row in report["placement_comparisons"] if row["metric"] == "median_rtt_us"]
    assert paired
    assert paired[0]["s1_run_ids"] and paired[0]["s3_run_ids"]
    assert paired[0]["direction"] == "S3 minus S1"
    assert paired[0]["rtt_difference_us"]["value"] == 0
    assert paired[0]["config_ids"]["S1"] != paired[0]["config_ids"]["S3"]
    assert paired[0]["study_dates"]["S1"] and paired[0]["study_dates"]["S3"]
    assert paired[0]["same_utc_week"] is True
    assert any(row["date_basis"] in {"date", "weekday"} for row in report["deployment_time_groups"])
    generated = write_analysis(analyzed, tmp_path / "s1-s3-report")
    assert any(path.name == "placement-comparison.png" for path in generated)


def test_matched_provider_report_preserves_full_f08_cohort_and_missing_peer_reasons(repository_root, tmp_path,
        frozen_clock, deterministic_token, fake_git_commits, request):
    from tests.f12_support import materialize_f08_pair
    from cloud_network_benchmark.analysis import analyze_selection
    from cloud_network_benchmark.contracts.common import Evidence, GitSnapshot, StringEvidence

    entries = []
    for provider in ("aws", "azure", "gcp"):
        _, result, selection = materialize_f08_pair(repository_root, tmp_path / provider, frozen_clock,
            deterministic_token, fake_git_commits, request, provider=provider, shared_slot=True)
        assert result.validation_status.data_validity == "valid"
        item = json.loads(selection.read_text(encoding="utf-8"))["runs"][0]
        entries.append({key: str((selection.parent / value).resolve()) for key, value in item.items()})
    combined = tmp_path / "matched-selection.json"
    combined.write_text(json.dumps({"format_version": 1, "runs": entries}), encoding="utf-8")
    snapshot = GitSnapshot(commit=StringEvidence(value="a" * 40), dirty=Evidence(value=False),
        diff_sha256=StringEvidence(value="b" * 64))
    report = analyze_selection(combined, analysis_git=snapshot).payload
    assert len(report["comparisons"]) == 1
    assert report["comparisons"][0]["present_providers"] == ["aws", "azure", "gcp"]
    assert report["comparisons"][0]["missing_providers"] == []
    assert report["comparisons"][0]["eligibility"] == "eligible"
    assert all(row["comparison_eligibility"] == "eligible" for row in report["runs"])

    single = tmp_path / "single-selection.json"
    single.write_text(json.dumps({"format_version": 1, "runs": entries[:1]}), encoding="utf-8")
    incomplete = analyze_selection(single, analysis_git=snapshot).payload["comparisons"][0]
    assert incomplete["eligibility"] == "ineligible"
    assert incomplete["missing_providers"] == ["azure", "gcp"]


def test_cli_analyze_requires_local_selection_and_output():
    from cloud_network_benchmark.cli import _parser

    try:
        _parser().parse_args(["analyze", "--selection", "selection.json"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("analyze requires an output directory")
