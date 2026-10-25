#!/usr/bin/env python3
"""Run the prepared, credential-free F10 validation gate and retain local evidence."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOTS = ("aws/pair", "aws/safety", "azure/pair", "azure/expiry", "gcp/pair", "gcp/protection")
MOCKS = {"azure/pair": "tests/topologies.tftest.hcl", "azure/expiry": "tests/prerequisites.tftest.hcl", "gcp/pair": "tests/inheritance.tftest.hcl", "gcp/protection": "tests/scenarios.tftest.hcl"}
PROC_CASES = {
    "tests/unit/test_f09_guest.py::test_f03_scoped_stop_reaps_owned_session_when_original_leader_has_exited",
    "tests/unit/test_f09_guest.py::test_f03_scoped_stop_refuses_reused_pid_start_ticks",
}
REQUIRED_COLLECTION_TARGETS = (
    "tests/integration/test_acquisition_producer.py",
    "tests/integration/test_analysis_replay.py",
    "tests/integration/test_f09_lifecycle_recovery.py",
    "tests/integration/test_f09_multi_provider_factory.py",
    "tests/integration/test_measurement_runner.py",
    "tests/integration/test_offline_boundaries.py",
    "tests/integration/test_azure_provider_flow.py",
    "tests/unit/test_bootstrap.py",
    "tests/unit/test_f09_guest.py",
    "tests/unit/test_f09_lifecycle.py",
    "tests/unit/test_f11_budget.py",
    "tests/unit/test_git_provenance.py",
    "tests/unit/test_measurement_timing.py",
    "tests/unit/test_readiness.py",
    "tests/unit/test_retrieval.py",
    "tests/unit/test_v2_admission_artifacts.py",
)
REQUIRED_FULL_MODULES = ("tests/integration/test_azure_provider_flow.py",)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def source_state():
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True, capture_output=True, check=True).stdout.strip()
    tree = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD^{tree}"], text=True, capture_output=True, check=True).stdout.strip()
    status = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=all"], text=True, capture_output=True, check=True).stdout
    return {"head": head, "tree": tree, "clean": not bool(status), "status": status.splitlines()}


def same_source_state(before, after):
    return (before["head"] == after["head"] and before["tree"] == after["tree"]
            and after["clean"])


def require(condition, message):
    if not condition:
        raise ValueError(message)


def has_provider_binary(root: Path) -> bool:
    for path in root.rglob("*"):
        if path.is_symlink() and path.is_dir():
            if any(child.name.startswith("terraform-provider-") and child.is_file()
                   for child in path.iterdir()):
                return True
        if path.name.startswith("terraform-provider-") and path.is_file():
            return True
    return False


def prerequisites(evidence: Path, terraform: Path, pwsh: Path, mirror: Path):
    state = source_state()
    require(state["clean"], "source checkout must be clean before execution")
    require(evidence.is_absolute(), "evidence directory must be absolute")
    require(not evidence.exists() and not evidence.is_symlink(), "evidence path already exists")
    for parent in evidence.parent.parents:
        require(not parent.is_symlink(), f"evidence path traverses symlink: {parent}")
    require(not evidence.parent.is_symlink(), f"evidence path traverses symlink: {evidence.parent}")
    resolved = evidence.parent.resolve(strict=True) / evidence.name
    require(resolved != ROOT and ROOT not in resolved.parents and resolved not in ROOT.parents,
            "evidence path overlaps source checkout")
    for name in ("pytest",):
        require(shutil.which(name), f"required executable is missing: {name}")
    for pkg in ("pydantic", "PyYAML", "matplotlib", "numpy"):
        try:
            importlib.metadata.version(pkg)
        except importlib.metadata.PackageNotFoundError as exc:
            raise ValueError(f"required package is missing: {pkg}") from exc
    require(terraform.is_file() and os.access(terraform, os.X_OK), f"Terraform executable unavailable: {terraform}")
    require(pwsh.is_file() and os.access(pwsh, os.X_OK), f"PowerShell executable unavailable: {pwsh}")
    require(mirror.is_dir() and has_provider_binary(mirror), "prepared filesystem provider mirror is missing packages")
    locks = {"aws": ("aws", "6.70.0"), "azurerm": ("azure", "4.81.0"), "google": ("gcp", "7.46.1")}
    for rel in ROOTS:
        path = ROOT / "terraform" / rel
        lock = path / ".terraform.lock.hcl"
        require(lock.is_file(), f"committed provider lock missing: {rel}")
        lock_text = lock.read_text()
        provider = next(name for name, (group, _version) in locks.items() if group == rel.split("/")[0])
        version = locks[provider][1]
        require(f'provider "registry.terraform.io/hashicorp/{provider}"' in lock_text,
                f"expected {provider} provider lock missing in {rel}")
        require(re.search(rf'\bversion\s*=\s*"{re.escape(version)}"', lock_text) is not None,
                f"unexpected {provider} lock in {rel}")
        cached = path / ".terraform/providers"
        platform_rel = Path("registry.terraform.io/hashicorp") / provider / version / "darwin_arm64"
        require(cached.is_dir() and has_provider_binary(cached / platform_rel), f"initialized local providers missing: {rel}")
        require(has_provider_binary(mirror / platform_rel), f"locked darwin_arm64 package missing from mirror: {provider} {version}")
    for rel, testfile in MOCKS.items():
        content = (ROOT / "terraform" / rel / testfile).read_text()
        run_count = len(re.findall(r'(?m)^\s*run\s+"', content))
        plan_count = len(re.findall(r"\bcommand\s*=\s*plan\b", content))
        require("mock_provider" in content and run_count > 0 and plan_count == run_count,
                f"every existing Terraform test must be an explicit mock plan in {rel}")
        require(not re.search(r"command\s*=\s*(apply|destroy)", content), f"non-plan Terraform test in {rel}")
    return state


def parse_collection(path: Path):
    identities = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        candidate = line.strip()
        if candidate.startswith("tests/") and "::" in candidate:
            identities.add(candidate)
    require(bool(identities), "required pytest collection is empty or malformed")
    return identities


def junit_coverage(path: Path, coverage_file: Path, platform_name: str, collected_identities):
    try:
        root = ET.parse(path).getroot()
        cases = root.findall(".//testcase")
        require(bool(cases), "JUnit report is empty")
    except (ET.ParseError, OSError) as exc:
        raise ValueError(f"JUnit report missing or malformed: {exc}") from exc
    identities = set()
    skipped = []
    failed = []
    for case in cases:
        classname, name = case.get("classname", ""), case.get("name", "")
        module_path = classname.replace(".", "/") + ".py" if classname else ""
        identity = f"{module_path}::{name}" if classname else name
        identities.add(identity)
        if case.find("failure") is not None or case.find("error") is not None:
            failed.append(identity)
        skip = case.find("skipped")
        if skip is not None:
            skipped.append((identity, (skip.get("message", "") + " " + (skip.text or "")).strip()))
    inventory = coverage_file.read_text()
    expected = set(re.findall(r"`(tests/[^`]+::[^`]+)`", inventory))
    factory = "tests/integration/test_f09_multi_provider_factory.py::test_default_three_provider_factory_reserves_and_enforces_global_claim_barrier"
    # Resolve abbreviated test names and parameter cases using their table row context.
    for line in inventory.splitlines():
        full = re.findall(r"`(tests/[^`]+::[^`]+)`", line)
        module = full[0].split("::", 1)[0] if full else None
        if module:
            shorthand = re.sub(r"`tests/[^`]+::[^`]+`", "", line)
            for name in re.findall(r"\b(test_[A-Za-z0-9_]+)\b", shorthand):
                expected.add(f"{module}::{name}")
        for case in re.findall(r"Same function\s+\[([^\]]+)\]", line):
            expected.add(f"{factory}[{case}]")
    expected.update(f"{factory}[{case}]" for case in
                    ("all-provider-acceptance", "all-provider-calibration", "all-provider-short-study",
                     "all-provider-diagnostic-day", "controller-interrupt-during-native-f03",
                     "controller-interrupt-unverified-stop", "protection-failure"))
    # For bare function rows, require every variant independently collected from pytest.
    required = set()
    missing = set()
    for req in expected:
        if "[" in req:
            required.add(req)
            if req not in collected_identities:
                missing.add(req)
            continue
        variants = {item for item in collected_identities
                    if item == req or item.startswith(req + "[")}
        if not variants:
            missing.add(req)
        required.update(variants)
    for module in REQUIRED_FULL_MODULES:
        module_cases = {item for item in collected_identities
                        if item.startswith(module + "::")}
        if not module_cases:
            missing.add(module + "::<no collected tests>")
        required.update(module_cases)
    missing.update(required - identities)
    missing = sorted(missing)
    gaps, unexpected = [], []
    for identity, reason in skipped:
        proc_reason = "the local test host has no Linux /proc process identity interface"
        allowed = (platform_name == "Darwin" and identity in PROC_CASES
                   and (reason == proc_reason or re.fullmatch(
                       re.escape(proc_reason)
                       + r" /[^ ]*/tests/unit/test_f09_guest\.py:\d+: "
                       + re.escape(proc_reason), reason) is not None))
        if allowed:
            gaps.append({"identity": identity, "state": "not_run", "reason": reason})
        else:
            unexpected.append({"identity": identity, "reason": reason})
    require(not missing, "required named coverage missing: " + ", ".join(missing))
    require(not failed, "pytest JUnit reports failed cases: " + ", ".join(failed[:10]))
    require(not unexpected, "unexpected required skips: " + json.dumps(unexpected))
    return {"tests": len(cases), "failures": len(failed), "skips": len(skipped), "required_identities": len(required), "missing": missing, "unexpected_skips": unexpected, "platform_gaps": gaps}


def run_check(summary, evidence, name, argv, env, timeout=900):
    log = evidence / f"{len(summary['checks']) + 1:02d}-{name}.log"
    started = time.monotonic()
    record = {"name": name, "argv": list(map(str, argv)), "status": "not_run", "exit_code": None,
              "duration_seconds": None, "log": log.name, "log_sha256": None}
    summary["checks"].append(record)
    try:
        completed = subprocess.run(list(map(str, argv)), cwd=ROOT, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        output, code = completed.stdout, completed.returncode
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or ""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", errors="replace")
        output, code = partial + "\nTIMEOUT\n", 124
    except KeyboardInterrupt:
        output, code = "INTERRUPTED\n", 130
        log.write_text(output, encoding="utf-8")
        record.update(status="failed", exit_code=code, duration_seconds=round(time.monotonic()-started, 3), log_sha256=sha(log))
        raise
    log.write_text(output, encoding="utf-8")
    record.update(status="passed" if code == 0 else "failed", exit_code=code,
                   duration_seconds=round(time.monotonic()-started, 3), log_sha256=sha(log))
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run prepared local F10 offline checks; no install, init, cloud, SSH, or benchmark traffic.")
    parser.add_argument("--evidence-dir", required=True, type=Path)
    parser.add_argument("--terraform", required=True, type=Path)
    parser.add_argument("--pwsh", required=True, type=Path)
    parser.add_argument("--provider-mirror", required=True, type=Path)
    args = parser.parse_args(argv)
    evidence = args.evidence_dir
    try:
        started_state = prerequisites(evidence, args.terraform, args.pwsh, args.provider_mirror)
    except Exception as exc:
        if (evidence.is_absolute() and not evidence.exists() and not evidence.is_symlink()
                and evidence.parent.is_dir() and not evidence.parent.is_symlink()):
            resolved = evidence.parent.resolve() / evidence.name
            if resolved != ROOT and ROOT not in resolved.parents and resolved not in ROOT.parents:
                evidence.mkdir()
                failure = {"format_version": 1, "status": "failed", "checks": [],
                           "error": f"{type(exc).__name__}: {exc}", "source_unchanged": False,
                           "native_linux_verified": False, "cloud_verified": False}
                try:
                    failure["end_state"] = source_state()
                except Exception as state_exc:
                    failure["end_state_error"] = str(state_exc)
                (evidence / "summary.json").write_text(json.dumps(failure, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return 1
    evidence.mkdir(parents=True)
    summary = {"format_version": 1, "revision": started_state["head"], "tree": started_state["tree"],
        "clean_at_start": True, "started_at_unix": time.time(), "status": "failed", "checks": [],
        "native_linux_verified": False, "cloud_verified": False}
    pytest_xml = evidence / "pytest.xml"
    checks = [
        ("full-pytest", [sys.executable, "-m", "pytest", "-q", f"--junitxml={pytest_xml}"]),
        ("required-collection", [sys.executable, "-m", "pytest", "--collect-only", "-q", *REQUIRED_COLLECTION_TARGETS]),
        ("schema-check", [sys.executable, "-m", "cloud_network_benchmark.contracts.schema", "--output", "specs/003-v2-shared-contracts/contracts", "--check"]),
        ("diff-check", ["git", "diff", "--check"]),
    ]
    for rel in ROOTS:
        checks += [(f"fmt-{rel.replace('/', '-')}", [str(args.terraform), f"-chdir={ROOT / 'terraform' / rel}", "fmt", "-check", "-recursive"]),
                   (f"validate-{rel.replace('/', '-')}", [str(args.terraform), f"-chdir={ROOT / 'terraform' / rel}", "validate", "-no-color"])]
        if rel in MOCKS:
            checks.append((f"mock-{rel.replace('/', '-')}", [str(args.terraform), f"-chdir={ROOT / 'terraform' / rel}", "test", "-no-color"]))
    try:
        thesis_root = Path(os.environ.get("THESIS_ROOT", "/Users/katya/Desktop/University/Masters/Thesis")).resolve(strict=True)
        thesis_revision = subprocess.run(["git", "-C", str(thesis_root), "rev-parse", "HEAD"],
            text=True, capture_output=True, check=True).stdout.strip()
        tf_config = evidence / "terraform.rc"
        tf_config.write_text('disable_checkpoint = true\nprovider_installation {\n  filesystem_mirror {\n    path = "' + str(args.provider_mirror.resolve()) + '"\n    include = ["registry.terraform.io/hashicorp/*"]\n  }\n}\n', encoding="utf-8")
        env = os.environ.copy()
        for key in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
            env.pop(key, None)
        for key in list(env):
            upper = key.upper()
            if (upper.startswith(("AWS_", "AZURE_", "ARM_", "GOOGLE_", "GCP_", "CLOUDSDK_", "TF_VAR_"))
                    or upper in {"SSH_AUTH_SOCK", "SSH_AGENT_PID", "GH_TOKEN", "GITHUB_TOKEN"}):
                env.pop(key, None)
        env.update({"PYTHONPATH": "src", "THESIS_ROOT": env.get("THESIS_ROOT", "/Users/katya/Desktop/University/Masters/Thesis"),
            "MPLBACKEND": "Agg", "MPLCONFIGDIR": str(evidence / "matplotlib"), "PATH": str(args.pwsh.parent) + os.pathsep + env.get("PATH", ""),
            "TF_CLI_CONFIG_FILE": str(tf_config), "CHECKPOINT_DISABLE": "1", "TF_IN_AUTOMATION": "1"})
        Path(env["MPLCONFIGDIR"]).mkdir()
        terraform_version = json.loads(subprocess.run([str(args.terraform), "version", "-json"],
            capture_output=True, text=True, check=True).stdout).get("terraform_version")
        summary["environment"] = {"host_os": platform.system(), "host_architecture": platform.machine(),
            "python": platform.python_version(), "terraform": terraform_version,
            "pwsh": subprocess.run([str(args.pwsh), "--version"], capture_output=True, text=True, check=True).stdout.strip(),
            "packages": {p: importlib.metadata.version(p) for p in ("pytest", "pydantic", "PyYAML", "matplotlib", "numpy")},
            "terraform_cli_config_sha256": sha(tf_config), "thesis_revision": thesis_revision}
        for index, (name, command) in enumerate(checks):
            code = run_check(summary, evidence, name, command, env, timeout=2400 if name == "full-pytest" else 300)
            if code:
                for omitted, omitted_argv in checks[index + 1:]:
                    summary["checks"].append({"name": omitted, "argv": list(map(str, omitted_argv)),
                        "status": "not_run", "exit_code": None, "duration_seconds": None,
                        "log": None, "log_sha256": None})
                break
        if summary["checks"] and summary["checks"][0]["name"] == "full-pytest" and summary["checks"][0]["exit_code"] == 0:
            collection_check = next((item for item in summary["checks"] if item["name"] == "required-collection"), None)
            if collection_check is not None and collection_check["status"] == "passed":
                collected = parse_collection(evidence / collection_check["log"])
                summary["coverage"] = junit_coverage(pytest_xml, ROOT / "specs/014-v2-offline-gate/contracts/coverage.md", platform.system(), collected)
            summary["junit_report"] = {"path": pytest_xml.name, "sha256": sha(pytest_xml)}
        summary["end_state"] = source_state()
        unchanged = same_source_state(started_state, summary["end_state"])
        summary["source_unchanged"] = unchanged
        passed = len(summary["checks"]) == len(checks) and all(x["status"] == "passed" for x in summary["checks"]) and bool(summary.get("coverage")) and unchanged
        summary["status"] = "passed" if passed else "failed"
        return 0 if passed else 1
    except BaseException as exc:
        summary["error"] = f"{type(exc).__name__}: {exc or 'interrupted'}"
        if isinstance(exc, KeyboardInterrupt):
            summary["interrupted"] = True
        recorded = {row["name"] for row in summary["checks"]}
        for name, command in checks:
            if name not in recorded:
                summary["checks"].append({"name": name, "argv": list(map(str, command)),
                    "status": "not_run", "exit_code": None, "duration_seconds": None,
                    "log": None, "log_sha256": None})
        try:
            summary["end_state"] = source_state()
        except Exception as state_exc:
            summary["end_state_error"] = str(state_exc)
        summary["source_unchanged"] = same_source_state(started_state, summary["end_state"]) if "end_state" in summary else False
        summary["status"] = "failed"
        return 130 if isinstance(exc, KeyboardInterrupt) else 1
    finally:
        summary["finished_at_unix"] = time.time()
        (evidence / "summary.json").write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n", encoding="utf-8")

if __name__ == "__main__":
    raise SystemExit(main())
