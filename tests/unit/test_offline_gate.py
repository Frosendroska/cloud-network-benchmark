from __future__ import annotations

import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from scripts import validate_offline as gate


FACTORY_MODULE = "tests/integration/test_f09_multi_provider_factory"
FACTORY_FUNCTION = "test_default_three_provider_factory_reserves_and_enforces_global_claim_barrier"
FACTORY_CASES = ("all-provider-acceptance", "all-provider-calibration", "all-provider-short-study",
                 "all-provider-diagnostic-day", "controller-interrupt-during-native-f03",
                 "controller-interrupt-unverified-stop", "protection-failure")
AZURE_MODULE = "tests/integration/test_azure_provider_flow.py"
AZURE_CASE = f"{AZURE_MODULE}::test_registration_requires_lead_and_exact_target_ids"


def factory_identities():
    return {f"tests/integration/test_f09_multi_provider_factory.py::{FACTORY_FUNCTION}[{case}]"
            for case in FACTORY_CASES}


def baseline_collection(*identities):
    return factory_identities() | {AZURE_CASE, *identities}


def azure_xml_case():
    return '<testcase classname="tests.integration.test_azure_provider_flow" name="test_registration_requires_lead_and_exact_target_ids"/>'


REQUIRED_VARIANTS = {
    "tests/integration/test_acquisition_producer.py::test_iperf_replay_requires_native_receiver_corroboration": (
        "[client_action_collision]", "[cookie_only]", "[native_endpoint]", "[native_settings]",
        "[native_stream]", "[observation_bytes]", "[observation_endpoint]"),
    "tests/unit/test_f09_lifecycle.py::test_original_cap_or_earlier_provider_trigger_blocks_stages_but_still_cleans": (
        "[201.0-200.0-None]", "[999.0-1000.0-200.0]"),
    "tests/unit/test_f11_budget.py::test_full_remaining_and_reserved_liabilities_obey_working_and_total_caps": (
        "[250-True]", "[250.0001-False]", "[300-False]"),
}


def xml_testcase(identity):
    module, name = identity.split("::", 1)
    classname = ".".join(Path(module).with_suffix("").parts)
    return f'<testcase classname="{classname}" name="{name}"/>'


def test_junit_requires_named_cases_and_only_exact_darwin_proc_gaps(tmp_path):
    coverage = tmp_path / "coverage.md"
    coverage.write_text("`tests/unit/test_ok.py::test_kept`\n`tests/unit/test_f09_guest.py::test_f03_scoped_stop_reaps_owned_session_when_original_leader_has_exited`\n")
    report = tmp_path / "junit.xml"
    factory = FACTORY_MODULE.replace("/", ".")
    cases = "".join(f'<testcase classname="{factory}" name="{FACTORY_FUNCTION}[{case}]"/>' for case in FACTORY_CASES)
    report.write_text(f'<testsuites><testsuite tests="10" skipped="1"><testcase classname="tests.unit.test_ok" name="test_kept"/><testcase classname="tests.unit.test_f09_guest" name="test_f03_scoped_stop_reaps_owned_session_when_original_leader_has_exited"><skipped message="the local test host has no Linux /proc process identity interface /private/tmp/cnb-f10/tests/unit/test_f09_guest.py:299: the local test host has no Linux /proc process identity interface"/></testcase>{cases}{azure_xml_case()}</testsuite></testsuites>')
    collected = baseline_collection("tests/unit/test_ok.py::test_kept", "tests/unit/test_f09_guest.py::test_f03_scoped_stop_reaps_owned_session_when_original_leader_has_exited")
    result = gate.junit_coverage(report, coverage, "Darwin", collected)
    assert result["tests"] == 10
    assert result["platform_gaps"][0]["state"] == "not_run"
    with pytest.raises(ValueError, match="unexpected required skips"):
        gate.junit_coverage(report, coverage, "Linux", collected)


def test_junit_missing_malformed_and_empty_reports_fail(tmp_path):
    coverage = tmp_path / "coverage.md"
    coverage.write_text("`tests/unit/test_ok.py::test_kept`\n")
    report = tmp_path / "junit.xml"
    for raw, message in (("<broken", "malformed"), ("<testsuites/>", "empty")):
        report.write_text(raw)
        with pytest.raises(ValueError, match=message):
            gate.junit_coverage(report, coverage, "Darwin", set())
    with pytest.raises(ValueError, match="missing or malformed"):
        gate.junit_coverage(tmp_path / "missing.xml", coverage, "Darwin", set())
    report.write_text('<testsuites><testsuite><testcase classname="tests.unit.test_other" name="test_other"/>' + azure_xml_case() + "</testsuite></testsuites>")
    with pytest.raises(ValueError, match="coverage missing"):
        gate.junit_coverage(report, coverage, "Darwin", baseline_collection("tests/unit/test_other.py::test_other"))


def test_collection_parser_requires_canonical_nonempty_node_ids(tmp_path):
    output = tmp_path / "collection.log"
    output.write_text("tests/unit/test_ok.py::test_plain\ntests/unit/test_ok.py::test_param[value]\n2 tests collected\n")
    assert gate.parse_collection(output) == {"tests/unit/test_ok.py::test_plain", "tests/unit/test_ok.py::test_param[value]"}
    output.write_text("no test identifiers\n")
    with pytest.raises(ValueError, match="collection is empty or malformed"):
        gate.parse_collection(output)


@pytest.mark.parametrize("base,variant", [
    (base, variant) for base, variants in REQUIRED_VARIANTS.items() for variant in variants
])
def test_junit_rejects_each_missing_collected_variant_with_sibling_retained(tmp_path, base, variant):
    coverage = tmp_path / "coverage.md"
    coverage.write_text("\n".join(f"`{name}`" for name in REQUIRED_VARIANTS) + "\n")
    collected_variants = {f"{name}{value}" for name, values in REQUIRED_VARIANTS.items() for value in values}
    module_cases = {AZURE_CASE, f"{AZURE_MODULE}::test_missing_expiry_is_rejected_before_workspace_mutation"}
    collected = baseline_collection(*collected_variants, *module_cases)
    identities = (collected_variants - {f"{base}{variant}"}) | module_cases | factory_identities()
    report = tmp_path / "junit.xml"
    report.write_text("<testsuites><testsuite>" + "".join(xml_testcase(item) for item in identities) + "</testsuite></testsuites>")
    with pytest.raises(ValueError, match=re.escape(f"{base}{variant}")):
        gate.junit_coverage(report, coverage, "Darwin", collected)


def test_junit_rejects_omitted_whole_required_module(tmp_path):
    coverage = tmp_path / "coverage.md"
    coverage.write_text("`tests/unit/test_ok.py::test_kept`\n")
    module_cases = {AZURE_CASE, f"{AZURE_MODULE}::test_missing_expiry_is_rejected_before_workspace_mutation"}
    collected = baseline_collection("tests/unit/test_ok.py::test_kept", *module_cases)
    report = tmp_path / "junit.xml"
    report.write_text("<testsuites><testsuite>" + "".join(xml_testcase(item) for item in factory_identities() | {"tests/unit/test_ok.py::test_kept"}) + "</testsuite></testsuites>")
    with pytest.raises(ValueError, match="test_registration_requires_lead_and_exact_target_ids"):
        gate.junit_coverage(report, coverage, "Darwin", collected)


def test_junit_rejects_missing_azure_powerShell_case_with_sibling_retained(tmp_path):
    coverage = tmp_path / "coverage.md"
    coverage.write_text("`tests/unit/test_ok.py::test_kept`\n")
    azure_cases = {
        AZURE_CASE,
        f"{AZURE_MODULE}::test_deallocation_runbook_has_two_target_retries_and_no_start_create_delete",
    }
    collected = baseline_collection("tests/unit/test_ok.py::test_kept", *azure_cases)
    report = tmp_path / "junit.xml"
    report.write_text("<testsuites><testsuite>" + "".join(xml_testcase(item) for item in factory_identities() | azure_cases - {next(item for item in azure_cases if "deallocation_runbook" in item)} | {"tests/unit/test_ok.py::test_kept"}) + "</testsuite></testsuites>")
    with pytest.raises(ValueError, match="deallocation_runbook_has_two_target_retries"):
        gate.junit_coverage(report, coverage, "Darwin", collected)


@pytest.mark.parametrize("omitted", [
    "test_failed_day_tcp_preserves_partial_day_and_does_not_restart",
    "test_late_success_at_original_deadline_is_not_ready",
    "test_failed_day_probe_rotates_without_restarting_tcp",
])
def test_junit_resolves_abbreviated_names_and_rejects_each_omission(tmp_path, omitted):
    coverage = tmp_path / "coverage.md"
    coverage.write_text("| Loss | `tests/integration/test_measurement_runner.py::test_failed_short_unit_returns_primary_failure_and_no_later_units` and `test_failed_day_tcp_preserves_partial_day_and_does_not_restart` |\n"
                        "| Deadline | `tests/unit/test_readiness.py::test_slow_checks_shrink_budgets_and_stop_at_deadline` and `test_late_success_at_original_deadline_is_not_ready` |\n"
                        "| Chunks | `tests/integration/test_measurement_runner.py::test_day_keeps_one_tcp_invocation_and_rotates_local_bounded_chunks` and `test_failed_day_probe_rotates_without_restarting_tcp` |\n")
    required = [
        ("tests.integration.test_measurement_runner", "test_failed_short_unit_returns_primary_failure_and_no_later_units"),
        ("tests.integration.test_measurement_runner", "test_failed_day_tcp_preserves_partial_day_and_does_not_restart"),
        ("tests.unit.test_readiness", "test_slow_checks_shrink_budgets_and_stop_at_deadline"),
        ("tests.unit.test_readiness", "test_late_success_at_original_deadline_is_not_ready"),
        ("tests.integration.test_measurement_runner", "test_day_keeps_one_tcp_invocation_and_rotates_local_bounded_chunks"),
        ("tests.integration.test_measurement_runner", "test_failed_day_probe_rotates_without_restarting_tcp"),
    ]
    cases = "".join(f'<testcase classname="{module}" name="{name}"/>' for module, name in required if name != omitted)
    cases += "".join(f'<testcase classname="{FACTORY_MODULE.replace("/", ".")}" name="{FACTORY_FUNCTION}[{case}]"/>' for case in FACTORY_CASES)
    cases += azure_xml_case()
    report = tmp_path / "junit.xml"
    report.write_text(f"<testsuites><testsuite>{cases}</testsuite></testsuites>")
    with pytest.raises(ValueError, match="coverage missing"):
        gate.junit_coverage(report, coverage, "Darwin", baseline_collection(*(f"{module.replace('.', '/')}.py::{name}" for module, name in required)))


def test_junit_requires_exact_parameter_variants_and_protection_failure(tmp_path):
    coverage = tmp_path / "coverage.md"
    coverage.write_text("`tests/integration/test_f09_multi_provider_factory.py::test_default_three_provider_factory_reserves_and_enforces_global_claim_barrier[all-provider-acceptance]`\nSame function [protection-failure]\n")
    report = tmp_path / "junit.xml"
    report.write_text("<testsuites><testsuite>" + "".join(f'<testcase classname="{FACTORY_MODULE.replace("/", ".")}" name="{FACTORY_FUNCTION}[{case}]"/>' for case in FACTORY_CASES if case != "protection-failure") + azure_xml_case() + "</testsuite></testsuites>")
    with pytest.raises(ValueError, match="protection-failure"):
        gate.junit_coverage(report, coverage, "Darwin", baseline_collection())
    report.write_text("<testsuites><testsuite>" + "".join(f'<testcase classname="{FACTORY_MODULE.replace("/", ".")}" name="{FACTORY_FUNCTION}[{case}]"/>' for case in FACTORY_CASES if case != "all-provider-acceptance") + azure_xml_case() + "</testsuite></testsuites>")
    with pytest.raises(ValueError, match="all-provider-acceptance"):
        gate.junit_coverage(report, coverage, "Darwin", baseline_collection())


def test_only_exact_proc_gap_reason_is_allowed(tmp_path):
    coverage = tmp_path / "coverage.md"
    identity = "tests/unit/test_f09_guest.py::test_f03_scoped_stop_reaps_owned_session_when_original_leader_has_exited"
    coverage.write_text(f"`{identity}`\n")
    report = tmp_path / "junit.xml"
    factory = FACTORY_MODULE.replace("/", ".")
    cases = "".join(f'<testcase classname="{factory}" name="{FACTORY_FUNCTION}[{case}]"/>' for case in FACTORY_CASES)
    report.write_text(f'<testsuites><testsuite><testcase classname="tests.unit.test_f09_guest" name="{identity.split("::")[1]}"><skipped message="unexpected process regression"/></testcase>{cases}{azure_xml_case()}</testsuite></testsuites>')
    with pytest.raises(ValueError, match="unexpected required skips"):
        gate.junit_coverage(report, coverage, "Darwin", baseline_collection(identity))


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


@pytest.mark.parametrize("reason", [
    "dependency", "tool", "provider-cache",
])
def test_main_retains_preflight_failure_without_dispatch(tmp_path, monkeypatch, reason):
    evidence = tmp_path / "evidence"
    repo = tmp_path / "repo"
    repo.mkdir()
    clean = {"head": "a", "tree": "b", "clean": True, "status": []}
    monkeypatch.setattr(gate, "ROOT", repo)
    monkeypatch.setattr(gate, "source_state", lambda: clean)
    tf, pwsh, mirror = tmp_path / "terraform", tmp_path / "pwsh", tmp_path / "mirror"
    if reason == "dependency":
        monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/pytest")
        monkeypatch.setattr(gate.importlib.metadata, "version", lambda pkg: (_ for _ in ()).throw(gate.importlib.metadata.PackageNotFoundError(pkg)) if pkg == "numpy" else "1")
        expected = "required package is missing: numpy"
    elif reason == "tool":
        monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/pytest")
        monkeypatch.setattr(gate.importlib.metadata, "version", lambda pkg: "1")
        expected = f"Terraform executable unavailable: {tf}"
    else:
        monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/pytest")
        monkeypatch.setattr(gate.importlib.metadata, "version", lambda pkg: "1")
        for executable in (tf, pwsh):
            executable.write_text("tool")
            executable.chmod(0o755)
        mirror.mkdir()
        expected = "prepared filesystem provider mirror is missing packages"
    monkeypatch.setattr(gate, "run_check", lambda *args, **kwargs: pytest.fail("check dispatched after failed preflight"))
    args = ["--evidence-dir", str(evidence), "--terraform", str(tf), "--pwsh", str(pwsh), "--provider-mirror", str(mirror)]
    assert gate.main(args) == 1
    result = json.loads((evidence / "summary.json").read_text())
    assert result["status"] == "failed" and expected in result["error"]
    assert result["end_state"]["head"] == "a"


def test_main_metadata_failure_records_all_omitted_checks_and_end_state(tmp_path, monkeypatch):
    evidence = tmp_path / "evidence"
    clean = {"head": "a", "tree": "b", "clean": True, "status": []}
    monkeypatch.setattr(gate, "prerequisites", lambda *args: clean)
    monkeypatch.setattr(gate, "source_state", lambda: clean)
    monkeypatch.setattr(gate.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(subprocess.CalledProcessError(9, a[0], stderr="version unavailable")))
    args = ["--evidence-dir", str(evidence), "--terraform", "/tf", "--pwsh", "/pwsh", "--provider-mirror", "/mirror"]
    assert gate.main(args) == 1
    result = json.loads((evidence / "summary.json").read_text())
    assert result["status"] == "failed" and result["source_unchanged"]
    assert "CalledProcessError" in result["error"] and result["end_state"] == clean
    expected_checks = 4 + sum(2 + (rel in gate.MOCKS) for rel in gate.ROOTS)
    assert len(result["checks"]) == expected_checks
    assert all(row["status"] == "not_run" for row in result["checks"])


def test_main_interrupt_preserves_failed_log_and_marks_remaining_not_run(tmp_path, monkeypatch):
    evidence = tmp_path / "evidence"
    clean = {"head": "a", "tree": "b", "clean": True, "status": []}
    monkeypatch.setattr(gate, "prerequisites", lambda *args: clean)
    monkeypatch.setattr(gate, "source_state", lambda: clean)
    calls = 0
    def startup_then_interrupt(argv, **kwargs):
        nonlocal calls
        calls += 1
        if "rev-parse" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="thesis-head")
        if "version" in argv and "-json" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout='{"terraform_version":"1.16.6"}')
        if "--version" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="PowerShell 7.5.11")
        raise KeyboardInterrupt
    monkeypatch.setattr(gate.subprocess, "run", startup_then_interrupt)
    args = ["--evidence-dir", str(evidence), "--terraform", "/tf", "--pwsh", "/pwsh", "--provider-mirror", "/mirror"]
    assert gate.main(args) == 130
    result = json.loads((evidence / "summary.json").read_text())
    assert result["interrupted"] and result["status"] == "failed" and result["source_unchanged"]
    assert result["checks"][0]["status"] == "failed" and result["checks"][0]["exit_code"] == 130
    assert (evidence / result["checks"][0]["log"]).read_text() == "INTERRUPTED\n"
    assert all(row["status"] == "not_run" for row in result["checks"][1:])


def test_main_sanitizes_environment_and_records_allowlisted_metadata(tmp_path, monkeypatch):
    evidence = tmp_path / "evidence"
    clean = {"head": "a", "tree": "b", "clean": True, "status": []}
    monkeypatch.setattr(gate, "prerequisites", lambda *args: clean)
    monkeypatch.setattr(gate, "source_state", lambda: clean)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-pass")
    monkeypatch.setenv("TF_VAR_password", "must-not-pass")
    monkeypatch.setenv("GH_TOKEN", "must-not-pass")
    monkeypatch.setenv("SAFE_TEST_VALUE", "allowed-input")
    def version(argv, **kwargs):
        if "rev-parse" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="thesis-head")
        if "-json" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout='{"terraform_version":"1.16.6"}')
        return subprocess.CompletedProcess(argv, 0, stdout="PowerShell 7.5.11")
    monkeypatch.setattr(gate.subprocess, "run", version)
    observed = {}
    def fail_first(summary, output, name, argv, env, timeout=900):
        observed.update(env)
        summary["checks"].append({"name": name, "argv": argv, "status": "failed", "exit_code": 7,
                                  "duration_seconds": 0, "log": "01-failed.log", "log_sha256": "e"})
        (output / "01-failed.log").write_text("failed")
        return 7
    monkeypatch.setattr(gate, "run_check", fail_first)
    args = ["--evidence-dir", str(evidence), "--terraform", "/tf", "--pwsh", "/pwsh", "--provider-mirror", str(tmp_path)]
    assert gate.main(args) == 1
    result = json.loads((evidence / "summary.json").read_text())
    assert not {"AWS_SECRET_ACCESS_KEY", "TF_VAR_password", "GH_TOKEN"} & observed.keys()
    assert observed["SAFE_TEST_VALUE"] == "allowed-input"
    assert set(result["environment"]) == {"host_os", "host_architecture", "python", "terraform", "pwsh", "packages", "terraform_cli_config_sha256", "thesis_revision"}
    assert all(row["status"] == "not_run" for row in result["checks"][1:])


def test_main_rejects_symlink_evidence_collision_without_writing_through_link(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    target = tmp_path / "existing"
    target.mkdir()
    evidence = tmp_path / "evidence-link"
    evidence.symlink_to(target, target_is_directory=True)
    clean = {"head": "a", "tree": "b", "clean": True, "status": []}
    monkeypatch.setattr(gate, "ROOT", repo)
    monkeypatch.setattr(gate, "source_state", lambda: clean)
    args = ["--evidence-dir", str(evidence), "--terraform", "/tf", "--pwsh", "/pwsh", "--provider-mirror", "/mirror"]
    assert gate.main(args) == 1
    assert list(target.iterdir()) == []


def test_main_rejects_evidence_inside_source_without_creating_it(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    evidence = repo / "evidence"
    clean = {"head": "a", "tree": "b", "clean": True, "status": []}
    monkeypatch.setattr(gate, "ROOT", repo)
    monkeypatch.setattr(gate, "source_state", lambda: clean)
    args = ["--evidence-dir", str(evidence), "--terraform", "/tf", "--pwsh", "/pwsh", "--provider-mirror", "/mirror"]
    assert gate.main(args) == 1
    assert not evidence.exists()


def test_main_failure_detects_revised_or_dirty_end_state(tmp_path, monkeypatch):
    evidence = tmp_path / "evidence"
    clean = {"head": "a", "tree": "b", "clean": True, "status": []}
    changed = {"head": "a", "tree": "changed", "clean": False, "status": [" M src/file.py"]}
    monkeypatch.setattr(gate, "prerequisites", lambda *args: clean)
    monkeypatch.setattr(gate, "source_state", lambda: clean)
    def version(argv, **kwargs):
        if "rev-parse" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="thesis-head")
        if "-json" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout='{"terraform_version":"1.16.6"}')
        return subprocess.CompletedProcess(argv, 0, stdout="PowerShell 7.5.11")
    monkeypatch.setattr(gate.subprocess, "run", version)
    def dirty_first(summary, output, name, argv, env, timeout=900):
        monkeypatch.setattr(gate, "source_state", lambda: changed)
        summary["checks"].append({"name": name, "argv": argv, "status": "failed", "exit_code": 7,
                                  "duration_seconds": 0, "log": "01-failed.log", "log_sha256": "e"})
        (output / "01-failed.log").write_text("failed")
        return 7
    monkeypatch.setattr(gate, "run_check", dirty_first)
    args = ["--evidence-dir", str(evidence), "--terraform", "/tf", "--pwsh", "/pwsh", "--provider-mirror", str(tmp_path)]
    assert gate.main(args) == 1
    result = json.loads((evidence / "summary.json").read_text())
    assert result["status"] == "failed" and not result["source_unchanged"]
    assert result["end_state"] == changed


def os_env():
    import os
    return os.environ.copy()
