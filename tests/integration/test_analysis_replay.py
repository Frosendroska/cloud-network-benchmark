from __future__ import annotations

import json

from cloud_network_benchmark.cli import main


def test_materialized_four_mode_inputs_are_revalidated_and_reported(repository_root, tmp_path,
        frozen_clock, deterministic_token, fake_git_commits, request, monkeypatch):
    from tests.f12_support import materialize_f08_pair
    from cloud_network_benchmark.analysis import _iperf_receiver_origin
    from cloud_network_benchmark.validation import _parse_unit, _validate_artifacts
    from cloud_network_benchmark.validation import _acquisition_windows

    for mode, expected_width in (("short_study", 1), ("acceptance", 1),
            ("calibration", 1), ("diagnostic_day", 60)):
        case = tmp_path / mode
        case.mkdir()
        value, expected, selection = materialize_f08_pair(repository_root, case, frozen_clock,
            deterministic_token, fake_git_commits, request, mode=mode)
        _findings, records, _provenance = _validate_artifacts(value)
        origins_by_unit = {}
        for timing_record in value.timings:
            origins = {}
            _windows, errors = _acquisition_windows(value, timing_record,
                verified_iperf_origins=origins)
            assert not errors, errors
            origins_by_unit[timing_record.unit_id] = origins
        parsed_sessions = [(_parse_unit(value, unit, records,
            origins_by_unit.get(unit.unit_id, {}))[1], unit) for unit in value.measurement_units]
        tcp, tcp_unit = next((parsed, unit) for parsed, unit in parsed_sessions if parsed and parsed.session_details)
        verified_origins = origins_by_unit[tcp_unit.unit_id]
        origin = _iperf_receiver_origin(tcp, verified_origins=verified_origins)
        assert origin is not None
        cookie = tcp.session_details[0]["start"]["cookie"]
        assert origin == (verified_origins[cookie]["origin_utc"],
            verified_origins[cookie]["uncertainty_seconds"])
        timing = next(item for item in value.timings if item.unit_id == tcp_unit.unit_id)
        measured_start = timing.measured_started_at.value.timestamp()
        measured_end = timing.measured_finished_at.value.timestamp()
        measured_intervals = [item for item in tcp.receiver_intervals if not item.omitted]
        assert measured_intervals
        assert measured_start - origin[1] <= origin[0] + min(item.start_seconds for item in measured_intervals)
        assert origin[0] + max(item.end_seconds for item in measured_intervals) <= measured_end + origin[1]
        if mode == "diagnostic_day":
            import weakref
            import cloud_network_benchmark.analysis as analysis_module
            prior_refs = []
            prior_chunk = None
            released_before_next_parse = []
            original_parse = analysis_module._parse_unit
            def track_native_lifetimes(current_value, current_unit, current_records,
                    verified_iperf_origins=None):
                nonlocal prior_refs, prior_chunk
                if current_unit.kind == "probe_chunk" and prior_chunk is not None:
                    released_before_next_parse.append((prior_chunk,
                        sum(reference() is not None for reference in prior_refs)))
                parsed_sock, parsed_tcp = original_parse(current_value, current_unit, current_records,
                    verified_iperf_origins)
                if current_unit.kind == "probe_chunk":
                    prior_chunk = current_unit.unit_id
                    prior_refs = [weakref.ref(sample) for sample in parsed_sock.samples] if parsed_sock else []
                return parsed_sock, parsed_tcp
            monkeypatch.setattr(analysis_module, "_parse_unit", track_native_lifetimes)
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
            assert len(released_before_next_parse) > 1
            assert all(live == 0 for _chunk, live in released_before_next_parse), released_before_next_parse[:5]
            context = report["day_load_context"][0]
            assert context["initial_300_seconds"].get("n", 0) > 0
            assert context["subsequent_supported_bins"].get("n", 0) > 0
            assert context["idle_baseline"]["unavailable_reason"]
            assert {row["axis"] for row in report["bins"]} == {
                "UTC-mapped seconds from continuous-day origin"}
            assert any(row["chunk_ids"] for row in report["bins"]
                if row["rtt"].get("valid_replies", 0))


def test_day_utc_window_limits_gap_rows_and_marks_partial_canonical_minutes(repository_root,
        tmp_path, frozen_clock, deterministic_token, fake_git_commits, request):
    from datetime import timedelta
    from tests.f12_support import materialize_f08_pair
    from cloud_network_benchmark.analysis import analyze_selection
    from cloud_network_benchmark.contracts.common import Evidence, GitSnapshot, StringEvidence

    value, _result, selection = materialize_f08_pair(repository_root, tmp_path, frozen_clock,
        deterministic_token, fake_git_commits, request, mode="diagnostic_day")
    day_unit = next(unit for unit in value.measurement_units if unit.kind == "continuous_day")
    day_start = next(item.measured_started_at.value for item in value.timings if item.unit_id == day_unit.unit_id)
    original = json.loads(selection.read_text(encoding="utf-8"))
    original["runs"] = [{key: str((selection.parent / ref).resolve())
        for key, ref in item.items()} for item in original["runs"]]
    snapshot = GitSnapshot(commit=StringEvidence(value="a" * 40), dirty=Evidence(value=False),
        diff_sha256=StringEvidence(value="b" * 64))

    future_selection = tmp_path / "future-window.json"
    future = {**original, "utc_window": {"start": "2099-01-01T00:00:00Z", "end": "2099-01-01T00:01:00Z"}}
    future_selection.write_text(json.dumps(future), encoding="utf-8")
    future_report = analyze_selection(future_selection, analysis_git=snapshot).payload
    assert future_report["bins"] == []
    assert future_report["day_load_context"][0]["initial_300_seconds"]["unavailable_reason"]

    partial_selection = tmp_path / "partial-window.json"
    partial = {**original, "utc_window": {
        "start": (day_start + timedelta(seconds=30)).isoformat(),
        "end": (day_start + timedelta(seconds=150)).isoformat()}}
    partial_selection.write_text(json.dumps(partial), encoding="utf-8")
    partial_report = analyze_selection(partial_selection, analysis_git=snapshot).payload
    rows = partial_report["bins"]
    assert [row["start_seconds"] for row in rows] == [0, 60, 120]
    assert [row["selected_seconds"] for row in rows] == [30, 60, 30]
    assert [row["selection_start_seconds"] for row in rows] == [30, 60, 120]
    assert [row["selection_end_seconds"] for row in rows] == [60, 120, 150]
    assert all(row["kind"] != "acquisition_gap" or row["selected_seconds"] > 0 for row in rows)


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


def test_missing_native_file_still_publishes_invalid_evidence_report(repository_root, tmp_path,
        frozen_clock, deterministic_token, fake_git_commits, request):
    import hashlib
    from pathlib import Path
    from tests.f12_support import materialize_f08_pair

    value, _result, selection = materialize_f08_pair(repository_root, tmp_path, frozen_clock,
        deterministic_token, fake_git_commits, request)
    missing_record = next(record for record in value.artifact_records if record.kind == "sockperf-csv")
    missing_path = Path(value.child_result_root) / missing_record.path
    other_sources = {(Path(value.child_result_root) / record.path): record.sha256.value
        for record in value.artifact_records if record.artifact_id != missing_record.artifact_id}
    missing_path.unlink()

    output = selection.parent / "invalid-report"
    assert main(["analyze", "--selection", str(selection), "--output", str(output)]) == 0
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["runs"] and report["runs"][0]["current_data_validity"] == "invalid"
    source = next(item for item in report["sources"] if item.get("artifact_id") == missing_record.artifact_id
        and item.get("kind") == "sockperf-csv" and item.get("resolved_path") is None)
    assert source["resolved_path"] is None
    assert source["unavailable_reason"]
    page = (output / "index.html").read_text(encoding="utf-8")
    assert "Unavailable: recorded artifact cannot be resolved" in page
    assert all((output / name).is_file() for name in (
        "report.json", "runs.csv", "bins.csv", "deployments.csv", "comparisons.csv",
        "diagnostics.csv", "reliability.csv", "costs.csv", "index.html"))
    assert len(list((output / "figures").iterdir())) == 14
    for path, digest in other_sources.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


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


def test_shared_reservation_cost_replay_is_stable_for_reordered_same_provider_attempts(repository_root,
        tmp_path, frozen_clock, deterministic_token, fake_git_commits, request, monkeypatch):
    from datetime import datetime, timedelta, timezone
    import hashlib
    from pathlib import Path
    import yaml
    from tests.f12_support import materialize_f08_pair
    from cloud_network_benchmark.contracts.admission import ReservationEvidence
    from cloud_network_benchmark.contracts.common import Evidence, StringEvidence
    import tests.integration.test_native_validation_replay as f08_replay

    s3_source = repository_root / "configs/experiments/exp-003-placement-optimization.yaml"
    fixture_config = repository_root / "configs/experiments/f12-cost-replay-test.yaml"
    fixture_config.write_text(yaml.safe_dump(yaml.safe_load(s3_source.read_text(encoding="utf-8")),
        sort_keys=False), encoding="utf-8")
    request.addfinalizer(lambda: fixture_config.unlink(missing_ok=True))
    actual_schedule_model = f08_replay.ScheduleEntry
    actual_summarize = f08_replay.summarize_validation
    monkeypatch.setattr(f08_replay, "summarize_validation",
        lambda result: "EXP-001 fixture compatibility marker\n" + actual_summarize(result))
    def configured_schedule(**kwargs):
        if kwargs.get("config_id") == "exp-003-short":
            kwargs["priority"] = "P1"
        return actual_schedule_model(**kwargs)
    monkeypatch.setattr(f08_replay, "ScheduleEntry", configured_schedule)

    pairs = []
    for name, config_name in (("attempt-a", "exp-001-multi-provider.yaml"),
            ("attempt-b", fixture_config.name)):
        _value, _result, selection = materialize_f08_pair(repository_root, tmp_path / name,
            frozen_clock, deterministic_token, fake_git_commits, request, config_name=config_name)
        pair = json.loads(selection.read_text(encoding="utf-8"))["runs"][0]
        validator_path = Path(pair["validator_input"])
        if not validator_path.is_absolute():
            validator_path = selection.parent / validator_path
        raw = json.loads(validator_path.read_text(encoding="utf-8"))
        start = datetime(2026, 10, 20, tzinfo=timezone.utc)
        reservation = ReservationEvidence(schema_version=2, campaign_id=raw["identity"]["campaign_id"],
            reservation_id=Evidence(value="shared-reservation"), state="reserved",
            reserved_cost_usd=Evidence(value=10.0), cap_started_at=Evidence(value=start),
            expires_at=Evidence(value=start + timedelta(days=1)),
            cap_start_policy_id=StringEvidence(value="test-policy"), source_path="costs/not-supplied.json",
            source_sha256="a" * 64)
        raw["reservation"] = Evidence(value=reservation).model_dump(mode="json")
        validator_path.write_text(json.dumps(raw), encoding="utf-8")
        pairs.append({key: str((selection.parent / value).resolve()) if not Path(value).is_absolute() else value
            for key, value in pair.items()})

    run_ids = [json.loads(Path(pair["validator_input"]).read_text())["identity"]["run_id"] for pair in pairs]
    assert run_ids[0] != run_ids[1]
    input_hashes = {Path(path): hashlib.sha256(Path(path).read_bytes()).hexdigest()
        for pair in pairs for path in pair.values()}
    first, second = tmp_path / "shared-first.json", tmp_path / "shared-second.json"
    first.write_text(json.dumps({"format_version": 1, "runs": pairs}), encoding="utf-8")
    second.write_text(json.dumps({"format_version": 1, "runs": list(reversed(pairs))}), encoding="utf-8")
    out_a, out_b = tmp_path / "shared-report-a", tmp_path / "shared-report-b"
    assert main(["analyze", "--selection", str(first), "--output", str(out_a)]) == 0
    assert main(["analyze", "--selection", str(second), "--output", str(out_b)]) == 0
    files_a = {path.relative_to(out_a): path.read_bytes() for path in out_a.rglob("*") if path.is_file()}
    files_b = {path.relative_to(out_b): path.read_bytes() for path in out_b.rglob("*") if path.is_file()}
    assert files_a == files_b
    report = json.loads((out_a / "report.json").read_text(encoding="utf-8"))
    costs = [row for row in report["costs"] if row["reservation_id"] == "shared-reservation"]
    assert len(costs) == 1
    assert costs[0]["run_id"] == min(run_ids) and costs[0]["run_ids"] == sorted(run_ids)
    assert costs[0]["source_verified"] is False
    assert costs[0]["actual"].get("value") is None and costs[0]["actual"]["unavailable_reason"]
    assert input_hashes == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in input_hashes}


def test_cli_analyze_requires_local_selection_and_output():
    from cloud_network_benchmark.cli import _parser

    try:
        _parser().parse_args(["analyze", "--selection", "selection.json"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("analyze requires an output directory")
