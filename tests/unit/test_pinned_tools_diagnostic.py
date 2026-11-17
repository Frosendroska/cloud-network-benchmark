from __future__ import annotations

import argparse
import runpy
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
