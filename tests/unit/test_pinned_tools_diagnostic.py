from __future__ import annotations

import argparse
import json
import runpy
import sys
import tempfile
from pathlib import Path

import pytest


def _diagnostic(repository_root: Path):
    return runpy.run_path(str(repository_root / "scripts/diagnostics/build_pinned_tools.py"))


def test_pinned_tool_diagnostic_config_keeps_all_paths_under_private_root(repository_root, tmp_path):
    diagnostic = _diagnostic(repository_root)
    config = diagnostic["build_config"](tmp_path / "native-build")
    assert config.workspace == str(tmp_path / "native-build/work")
    assert config.results_workspace == str(tmp_path / "native-build/results")
    assert config.install_prefix == str(tmp_path / "native-build/install")


def test_pinned_tool_diagnostic_retains_only_a_new_workspace_under_system_temp(tmp_path):
    diagnostic = _diagnostic(Path(__file__).resolve().parents[2])
    temp_root = Path(tempfile.gettempdir()).resolve()
    parent = tmp_path.resolve()
    if not parent.is_relative_to(temp_root):
        pytest.skip("pytest temporary path is outside the system temporary directory")
    requested = parent / "pinned-build"
    context = diagnostic["workspace_context"](requested, argparse.ArgumentParser())
    with context as retained:
        assert Path(retained) == requested
        assert requested.is_dir()
    with pytest.raises(SystemExit):
        diagnostic["workspace_context"](requested, argparse.ArgumentParser())


@pytest.mark.parametrize("use_cache", [False, True], ids=["download", "verified-cache"])
def test_pinned_tool_diagnostic_main_runs_through_archive_and_identity_checks(monkeypatch, tmp_path, use_cache):
    repository_root = Path(__file__).resolve().parents[2]
    diagnostic = _diagnostic(repository_root)
    globals_ = diagnostic["main"].__globals__
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    workspace = tmp_path / "private-workspace"
    argv = ["build_pinned_tools.py", "--prepared-host", "--evidence-dir", str(evidence),
        "--workspace", str(workspace)]
    source_cache = tmp_path / "source-cache"
    if use_cache:
        source_cache.mkdir()
        for source in diagnostic["SOURCES"]:
            (source_cache / source["archive_name"]).write_bytes(b"fixture archive")
        argv.extend(["--source-cache", str(source_cache)])
        expected_by_archive = {source["archive_name"]: source["source_sha256"] for source in diagnostic["SOURCES"]}
        monkeypatch.setitem(globals_, "digest", lambda path: expected_by_archive.get(Path(path).name, "a" * 64))
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(diagnostic["shutil"], "which", lambda name: f"/fake/{name}")
    monkeypatch.setattr(diagnostic["platform"], "platform", lambda: "fake Linux")
    monkeypatch.setattr(diagnostic["platform"], "machine", lambda: "x86_64")
    monkeypatch.setattr(diagnostic["subprocess"], "run", lambda *args, **kwargs:
        type("Result", (), {"stdout": "fake compiler\n"})())
    monkeypatch.setitem(globals_, "_capabilities_observed", lambda *args, **kwargs: True)
    monkeypatch.setitem(globals_, "_parse_build_identity", lambda text, source: {"tool": source.tool})
    def fake_run_logged(argv, cwd, env, stdout_path, stderr_path):
        command = " ".join(argv)
        if argv[0] == "curl":
            Path(argv[6]).write_bytes(b"fixture archive")
            output = ""
        elif argv[0] == "sha256sum" and argv[1].endswith(".tar.gz"):
            output = f"{diagnostic['SOURCES'][0]['source_sha256']}  {argv[1]}\n" if "sockperf" in argv[1] else f"{diagnostic['SOURCES'][1]['source_sha256']}  {argv[1]}\n"
        elif argv[0] == "sha256sum":
            output = f"{'a' * 64}  {argv[1]}\n"
        elif "CNB_BUILD_V1" in command:
            output = "CNB_BUILD_V1\ntool=fake\n"
        elif "if [ -L" in command:
            output = "matching\n"
        elif argv[0] == "python3":
            output = "CNB_TCP_CONGESTION=cubic\n"
        else:
            output = "version or help output\n"
        stdout_path.write_text(output)
        stderr_path.write_text("")
        return 0, False, 0.0

    monkeypatch.setitem(globals_, "run_logged", fake_run_logged)
    assert diagnostic["main"]() == 0
    summary = json.loads((evidence / "summary.json").read_text())
    assert summary["status"] == "passed"
    assert summary["retained_workspace"] == str(workspace)
    assert [row["status"] for row in summary["tools"]] == ["passed", "passed"]
    assert [row.get("source_cache_used", False) for row in summary["tools"]] == [use_cache, use_cache]
    assert (workspace / "work" / "sockperf.tar.gz").is_file()
    assert (workspace / "work" / "iperf3.tar.gz").is_file()
