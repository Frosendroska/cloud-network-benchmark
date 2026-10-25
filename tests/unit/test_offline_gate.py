from __future__ import annotations

import hashlib
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from scripts import validate_offline as gate


def test_junit_requires_named_cases_and_only_exact_darwin_proc_gaps(tmp_path):
    coverage = tmp_path / "coverage.md"
    coverage.write_text("`tests/unit/test_ok.py::test_kept`\n`tests/unit/test_f09_guest.py::test_f03_scoped_stop_reaps_owned_session_when_original_leader_has_exited`\n")
    report = tmp_path / "junit.xml"
    factory = "tests.integration.test_f09_multi_provider_factory"
    cases = "".join(f'<testcase classname="{factory}" name="test_default_three_provider_factory_reserves_and_enforces_global_claim_barrier[{case}]"/>' for case in ("all-provider-acceptance", "all-provider-calibration", "all-provider-short-study", "all-provider-diagnostic-day", "controller-interrupt-during-native-f03", "controller-interrupt-unverified-stop"))
    report.write_text(f'<testsuites><testsuite tests="8" skipped="1"><testcase classname="tests.unit.test_ok" name="test_kept"/><testcase classname="tests.unit.test_f09_guest" name="test_f03_scoped_stop_reaps_owned_session_when_original_leader_has_exited"><skipped message="Linux /proc is unavailable"/></testcase>{cases}</testsuite></testsuites>')
    result = gate.junit_coverage(report, coverage, "Darwin")
    assert result["tests"] == 8
    assert result["platform_gaps"][0]["state"] == "not_run"
    with pytest.raises(ValueError, match="unexpected required skips"):
        gate.junit_coverage(report, coverage, "Linux")


def test_junit_missing_malformed_and_empty_reports_fail(tmp_path):
    coverage = tmp_path / "coverage.md"
    coverage.write_text("`tests/unit/test_ok.py::test_kept`\n")
    report = tmp_path / "junit.xml"
    for raw, message in (("<broken", "malformed"), ("<testsuites/>", "empty")):
        report.write_text(raw)
        with pytest.raises(ValueError, match=message):
            gate.junit_coverage(report, coverage, "Darwin")
    with pytest.raises(ValueError, match="missing or malformed"):
        gate.junit_coverage(tmp_path / "missing.xml", coverage, "Darwin")
    report.write_text('<testsuites><testsuite><testcase classname="tests.unit.test_other" name="test_other"/></testsuite></testsuites>')
    with pytest.raises(ValueError, match="coverage missing"):
        gate.junit_coverage(report, coverage, "Darwin")


def test_run_check_records_exit_timeout_interruption_and_log_hash(tmp_path, monkeypatch):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    summary = {"checks": []}
    monkeypatch.setattr(gate.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 7, "rejected"))
    assert gate.run_check(summary, evidence, "failure", ["fixed", "argv"], os_env(), timeout=1) == 7
    row = summary["checks"][0]
    assert row["status"] == "failed" and row["exit_code"] == 7
    assert row["log_sha256"] == hashlib.sha256((evidence / row["log"]).read_bytes()).hexdigest()
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"], output="partial")
    monkeypatch.setattr(gate.subprocess, "run", timeout)
    assert gate.run_check(summary, evidence, "timeout", ["fixed"], os_env(), timeout=1) == 124
    assert summary["checks"][1]["status"] == "failed"
    monkeypatch.setattr(gate.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        gate.run_check(summary, evidence, "interrupt", ["fixed"], os_env(), timeout=1)
    assert summary["checks"][2]["exit_code"] == 130


def test_checkout_revision_and_tree_must_remain_clean():
    clean = {"head": "a", "tree": "b", "clean": True}
    assert gate.same_source_state(clean, {**clean, "status": []})
    assert not gate.same_source_state(clean, {**clean, "clean": False})
    assert not gate.same_source_state(clean, {**clean, "head": "c"})
    assert not gate.same_source_state(clean, {**clean, "tree": "d"})


def test_dirty_checkout_and_existing_evidence_are_rejected_without_dispatch(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    evidence = tmp_path / "new-evidence"
    monkeypatch.setattr(gate, "ROOT", repo)
    monkeypatch.setattr(gate, "source_state", lambda: {"head": "a", "tree": "b", "clean": False, "status": ["?? new"]})
    with pytest.raises(ValueError, match="checkout must be clean"):
        gate.prerequisites(evidence, tmp_path / "terraform", tmp_path / "pwsh", tmp_path / "mirror")
    monkeypatch.setattr(gate, "source_state", lambda: {"head": "a", "tree": "b", "clean": True, "status": []})
    evidence.mkdir()
    with pytest.raises(ValueError, match="already exists"):
        gate.prerequisites(evidence, tmp_path / "terraform", tmp_path / "pwsh", tmp_path / "mirror")


def os_env():
    import os
    return os.environ.copy()
