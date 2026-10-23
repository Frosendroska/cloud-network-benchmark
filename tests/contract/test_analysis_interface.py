from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cloud_network_benchmark.contracts.common import Evidence, GitSnapshot, StringEvidence


def _git():
    return GitSnapshot(commit=StringEvidence(value="a" * 40), dirty=Evidence(value=False),
                       diff_sha256=StringEvidence(value="0" * 64))


@pytest.mark.parametrize("payload", [
    {},
    {"format_version": 2, "runs": [{"validator_input": "a", "validation_result": "b"}]},
    {"format_version": 1, "runs": []},
    {"format_version": 1, "runs": [{"validator_input": "a", "validation_result": "b", "extra": 1}]},
])
def test_selection_rejects_missing_unknown_or_unsupported_fields(tmp_path, payload):
    from cloud_network_benchmark.analysis import analyze_selection

    path = tmp_path / "selection.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises((ValueError, OSError)):
        analyze_selection(path, analysis_git=_git())


def test_actual_f08_pair_revalidates_and_fingerprints_descriptor_order(repository_root, tmp_path,
        frozen_clock, deterministic_token, fake_git_commits, request):
    from tests.f12_support import materialize_f08_pair
    from cloud_network_benchmark.analysis import analyze_selection

    _value, _result, selection = materialize_f08_pair(repository_root, tmp_path, frozen_clock,
        deterministic_token, fake_git_commits, request)
    report = analyze_selection(selection, analysis_git=_git())
    assert report.payload["runs"]
    assert report.payload["sources"]
    assert report.payload["selection_fingerprint"]
    assert report.payload["runs"][0]["current_data_validity"] == "valid"


def test_changed_native_source_cannot_keep_recorded_validity(repository_root, tmp_path,
        frozen_clock, deterministic_token, fake_git_commits, request):
    from tests.f12_support import materialize_f08_pair
    from cloud_network_benchmark.analysis import analyze_selection

    value, _result, selection = materialize_f08_pair(repository_root, tmp_path, frozen_clock,
        deterministic_token, fake_git_commits, request)
    native_record = next(item for item in value.artifact_records if item.kind == "sockperf-csv")
    native_path = Path(value.child_result_root) / native_record.path
    native_path.write_bytes(native_path.read_bytes() + b"\n")
    report = analyze_selection(selection, analysis_git=_git())
    assert report.payload["runs"][0]["recorded_data_validity"] == "valid"
    assert report.payload["runs"][0]["current_data_validity"] == "invalid"


def test_utc_window_requires_aware_increasing_times(tmp_path):
    from cloud_network_benchmark.analysis import analyze_selection

    path = tmp_path / "selection.json"
    path.write_text(json.dumps({"format_version": 1, "runs": [{
        "validator_input": "a", "validation_result": "b"}],
        "utc_window": {"start": "2026-01-02T00:00:00", "end": "2026-01-01T00:00:00Z"}}), encoding="utf-8")
    with pytest.raises(ValueError):
        analyze_selection(path, analysis_git=_git())


def test_utc_window_normalizes_offsets_and_duplicate_run_conflicts(repository_root, tmp_path,
        frozen_clock, deterministic_token, fake_git_commits, request):
    from tests.f12_support import materialize_f08_pair
    from cloud_network_benchmark.analysis import analyze_selection

    _value, result, selection = materialize_f08_pair(repository_root, tmp_path, frozen_clock,
        deterministic_token, fake_git_commits, request)
    raw = json.loads(selection.read_text())
    raw["utc_window"] = {"start": "2026-10-21T02:00:00+02:00", "end": "2026-10-21T03:00:00+02:00"}
    selection.write_text(json.dumps(raw))
    first = analyze_selection(selection, analysis_git=_git())
    alternate = selection.with_name("renamed-selection.json")
    alternate.write_text(selection.read_text())
    second = analyze_selection(alternate, analysis_git=_git())
    assert first.payload["selection_fingerprint"] == second.payload["selection_fingerprint"]
    pair = selection.parent
    conflicting = pair / "different-result.json"
    altered = result.model_copy(update={"evaluated_at": result.evaluated_at.replace(minute=1)})
    conflicting.write_text(altered.model_dump_json())
    raw["runs"].append({"validator_input": "validator-input.json", "validation_result": conflicting.name})
    selection.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="conflicting duplicate run"):
        analyze_selection(selection, analysis_git=_git())


def test_protected_source_target_is_not_publishable(repository_root, tmp_path,
        frozen_clock, deterministic_token, fake_git_commits, request):
    from tests.f12_support import materialize_f08_pair
    from cloud_network_benchmark.analysis import analyze_selection
    from cloud_network_benchmark.analysis_report import write_analysis

    _value, _result, selection = materialize_f08_pair(repository_root, tmp_path, frozen_clock,
        deterministic_token, fake_git_commits, request)
    report = analyze_selection(selection, analysis_git=_git())
    with pytest.raises(ValueError):
        write_analysis(report, selection.parent)


def test_publish_writes_fixed_tables_escaped_offline_html_and_rejects_existing(tmp_path):
    from cloud_network_benchmark.analysis import AnalysisReport
    from cloud_network_benchmark.analysis_report import write_analysis

    report = AnalysisReport(payload={"format_version": 1, "selection_fingerprint": "a" * 64,
        "runs": [{"run_id": "<script>alert(1)</script>", "provider": "aws", "scenario": "same_zone",
            "mode": "short_study", "unit_id": "u1", "phase": "multi_flow", "data_validity": "partial",
            "comparison_eligibility": "ineligible"}], "bins": [], "deployments": [], "comparisons": [],
        "diagnostics": [], "reliability": [], "costs": [], "sources": [], "figures": []}, protected_paths=())
    output = tmp_path / "report"
    files = write_analysis(report, output)
    assert len(files) == 17
    assert {path.name for path in output.iterdir()} == {
        "report.json", "runs.csv", "bins.csv", "deployments.csv", "comparisons.csv",
        "diagnostics.csv", "reliability.csv", "costs.csv", "index.html", "figures"}
    page = (output / "index.html").read_text()
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<script>alert(1)</script>" not in page
    assert 'href="runs.csv"' in page and 'href="report.json"' in page
    assert "Matched provider comparisons" in page and "Recorded costs" in page
    assert 'href="figures/time-series.svg"' in page
    assert "https://" not in page
    with pytest.raises(ValueError, match="already exists"):
        write_analysis(report, output)


def test_publish_failure_cleans_only_staging_and_never_exposes_target(tmp_path, monkeypatch):
    import cloud_network_benchmark.analysis_report as publishing
    from cloud_network_benchmark.analysis import AnalysisReport

    report = AnalysisReport(payload={"selection_fingerprint": "b" * 64, "runs": [], "bins": [],
        "deployments": [], "comparisons": [], "diagnostics": [], "reliability": [], "costs": [],
        "sources": []}, protected_paths=())
    def fail(_payload, _directory):
        raise RuntimeError("synthetic renderer failure")
    monkeypatch.setattr(publishing, "render_figures", fail)
    target = tmp_path / "failed-report"
    with pytest.raises(RuntimeError, match="synthetic renderer failure"):
        publishing.write_analysis(report, target)
    assert not target.exists()
    assert not list(tmp_path.glob(".cnb-analysis-*"))
