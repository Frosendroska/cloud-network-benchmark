from __future__ import annotations

import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pytest


@pytest.fixture
def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def results_root(tmp_path: Path) -> Path:
    root = tmp_path / "results"
    (root / "manifests").mkdir(parents=True)
    (root / "raw").mkdir()
    return root


@pytest.fixture
def frozen_clock() -> Callable[[], datetime]:
    return lambda: datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def deterministic_token() -> Callable[[], str]:
    return lambda: "abc123"


@pytest.fixture
def fake_git_commits() -> dict[str, str]:
    return {"implementation": "a" * 40, "design": "b" * 40}


@pytest.fixture
def fail_at() -> Callable[[str], None]:
    return lambda _point: None


@pytest.fixture
def deny_external_io(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("credential-free test attempted external I/O")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(subprocess, "run", blocked)
    monkeypatch.setattr(subprocess, "Popen", blocked)
