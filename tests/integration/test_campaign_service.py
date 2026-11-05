"""Offline systemd handoff checks; actual Ubuntu disconnect remains supervised."""
from __future__ import annotations

import subprocess

import pytest
from pathlib import Path
from types import SimpleNamespace

from cloud_network_benchmark.campaign import CampaignDriver


ROOT = Path(__file__).parents[2]


def test_launch_hands_off_to_one_systemd_service_without_waiting_for_measurements(tmp_path, monkeypatch):
    called = []

    def systemctl(command, **kwargs):
        called.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", systemctl)
    result = CampaignDriver._dispatch_systemd({"state_root": str(tmp_path)})
    assert called
    command = called[0][0]
    assert "systemctl" in command[0]
    assert "cloud-network-benchmark-campaign.service" in command
    assert "start" in command or "restart" in command
    assert result is not None


def test_service_restarts_the_driver_and_keeps_its_processes_on_terminal_exit():
    path = ROOT / "deploy/cloud-network-benchmark-campaign.service"
    source = path.read_text()
    assert "ExecStart=" in source
    assert "campaign run --state-root" in source
    assert "Restart=on-failure" in source
    assert "KillMode=control-group" in source
    assert "StandardOutput=journal" in source
    assert "Restart=always" not in source


@pytest.mark.parametrize("outcome", ["complete", "failed", "missed", "awaiting_operator"])
def test_terminal_service_outcome_does_not_trigger_an_infinite_systemd_restart(outcome, tmp_path, capsys):
    from cloud_network_benchmark.campaign_cli import main

    driver = SimpleNamespace(run_active=lambda _: {"status": outcome, "counts": {"scheduled": 0}})
    assert main(["run", "--state-root", str(tmp_path)], driver=driver) == 0
    assert outcome in capsys.readouterr().out
