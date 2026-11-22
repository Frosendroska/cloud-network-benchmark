"""Offline systemd handoff checks; actual Ubuntu disconnect remains supervised."""
from __future__ import annotations

import subprocess

import pytest
from pathlib import Path
from types import SimpleNamespace

from cloud_network_benchmark.campaign import CampaignDriver


ROOT = Path(__file__).parents[2]


def test_launch_detaches_one_user_space_supervisor_without_systemd_or_linger(tmp_path, monkeypatch):
    called = []

    def popen(command, **kwargs):
        called.append((command, kwargs))
        return SimpleNamespace(pid=4321)

    monkeypatch.setattr(subprocess, "Popen", popen)
    result = CampaignDriver._dispatch_user_space({"state_root": str(tmp_path), "repository_root": str(ROOT)})
    assert called
    command = called[0][0]
    assert command[-3:] == ["--state-root", str(tmp_path),] or command[-2:] == ["--state-root", str(tmp_path)]
    assert "campaign_supervisor" in " ".join(command)
    assert called[0][1]["start_new_session"] is True
    assert called[0][1]["stdin"] is subprocess.DEVNULL
    assert result["supervisor"] == "user-space"
    assert (tmp_path / "campaign-supervisor.pid").read_text().strip() == "4321"


def test_user_space_supervisor_keeps_host_reboot_risk_explicit():
    campaign_source = (ROOT / "src/cloud_network_benchmark/campaign.py").read_text()
    supervisor_source = (ROOT / "src/cloud_network_benchmark/campaign_supervisor.py").read_text()
    assert "user-space supervisor" in campaign_source
    assert "cloud-network-benchmark-campaign.service" not in campaign_source
    assert "start_new_session=True" in campaign_source
    assert "host_reboot_recovery\": \"not provided\"" in campaign_source


@pytest.mark.parametrize("outcome", ["complete", "failed", "missed", "awaiting_operator"])
def test_terminal_service_outcome_does_not_trigger_an_infinite_systemd_restart(outcome, tmp_path, capsys):
    from cloud_network_benchmark.campaign_cli import main

    driver = SimpleNamespace(run_active=lambda _: {"status": outcome, "counts": {"scheduled": 0}})
    assert main(["run", "--state-root", str(tmp_path)], driver=driver) == 0
    assert outcome in capsys.readouterr().out


def test_resume_is_read_only_and_returns_saved_observer_state(tmp_path, monkeypatch, capsys):
    from cloud_network_benchmark.campaign_cli import main
    from cloud_network_benchmark.campaign_inputs import load_operator_settings

    settings = tmp_path / "operator.yaml"
    settings.write_text(f"schema_version: 1\nstate_root: {tmp_path / 'state'}\nresults_root: {tmp_path / 'results'}\n")
    state = tmp_path / "state"
    state.mkdir()
    (state / "observer-status.json").write_text('{"health":"healthy","stage_id":"same-campaign"}\n')
    driver = SimpleNamespace(status=lambda _: [{"stage_id": "same-campaign", "status": "active"}])
    assert main(["resume", "--settings", str(settings)], driver=driver) == 0
    result = __import__("json").loads(capsys.readouterr().out)
    assert result["starts_work"] is False
    assert result["campaigns"][0]["stage_id"] == result["observer"]["stage_id"]


def test_observer_binds_ids_paths_and_records_cleanup_disk_and_stale_fault(tmp_path, monkeypatch):
    import json
    import threading
    from datetime import datetime, timedelta, timezone
    import cloud_network_benchmark.campaign_supervisor as supervisor

    state = tmp_path / "state"
    stage_dir = state / "stage-1"
    stage_dir.mkdir(parents=True)
    result_root = tmp_path / "results"
    raw = result_root / "raw" / "run-1"
    raw.mkdir(parents=True)
    (raw / "stale.csv").write_text("closed")
    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).timestamp()
    import os
    os.utime(raw / "stale.csv", (old, old))
    prepared = {"stage_id": "stage-1", "stage": "day", "results_root": str(result_root),
                "entries": [{"entry_id": "entry-1", "run_ids": {"aws": "run-1"}}]}
    journal = {"entries": {"entry-1": {"state": "running",
        "started_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
        "result": {"cleanup_open": True}}}}
    (stage_dir / "prepared-stage.json").write_text(json.dumps(prepared))
    (stage_dir / "campaign-journal.json").write_text(json.dumps(journal))
    (state / "active-stage.json").write_text(json.dumps({"stage_id": "stage-1",
        "stage_path": str(stage_dir / "prepared-stage.json")}))
    monkeypatch.setattr(supervisor, "_read_only_preflight", lambda _: {"status": "qualified"})
    class StopAfterOne:
        calls = 0
        def is_set(self):
            return self.calls > 0
        def wait(self, _):
            self.calls += 1
    stop = StopAfterOne()
    supervisor.observe(state, stop, interval=0)
    snapshot = json.loads((state / "observer-status.json").read_text())
    assert snapshot["stage_id"] == "stage-1"
    assert snapshot["journal_path"] == str(stage_dir / "campaign-journal.json")
    assert snapshot["health"] == "fault"
    assert snapshot["cleanup_liabilities"][0]["entry_id"] == "entry-1"
    assert snapshot["stale_output_entries"] == ["entry-1"]
    assert snapshot["disk"]["free_bytes"] > 0


@pytest.mark.parametrize("row_state,minimum,free,expected_health", [
    ("failed", 100, 500, "fault"),
    ("missed", 100, 500, "healthy"),
    ("complete", 100, 50, "fault"),
    ("complete", 100, 500, "healthy"),
])
def test_observer_reports_child_failure_missed_state_and_low_disk(tmp_path, monkeypatch,
                                                                   row_state, minimum, free, expected_health):
    import json
    import threading
    from types import SimpleNamespace
    import cloud_network_benchmark.campaign_supervisor as supervisor

    state = tmp_path / "state"
    stage_dir = state / "stage-1"
    stage_dir.mkdir(parents=True)
    stage = {"stage_id": "stage-1", "stage": "acceptance", "results_root": str(tmp_path / "results"),
             "minimum_free_bytes": minimum, "entries": []}
    journal = {"entries": {"entry-1": {"state": row_state, "result": {}}}}
    (stage_dir / "prepared-stage.json").write_text(json.dumps(stage))
    (stage_dir / "campaign-journal.json").write_text(json.dumps(journal))
    (state / "active-stage.json").write_text(json.dumps({"stage_id": "stage-1",
        "stage_path": str(stage_dir / "prepared-stage.json")}))
    monkeypatch.setattr(supervisor, "_read_only_preflight", lambda _: {"status": "qualified"})
    monkeypatch.setattr(supervisor.shutil, "disk_usage", lambda _: SimpleNamespace(free=free, total=1000))

    class StopAfterOne:
        calls = 0
        def is_set(self):
            return self.calls > 0
        def wait(self, _):
            self.calls += 1

    supervisor.observe(state, StopAfterOne(), interval=0)
    snapshot = json.loads((state / "observer-status.json").read_text())
    assert snapshot["health"] == expected_health
    assert snapshot["missed_entries"] == (["entry-1"] if row_state == "missed" else [])
    assert snapshot["disk"]["below_minimum"] is (free < minimum)
    assert snapshot["disk"]["minimum_free_bytes"] == minimum


def test_observer_exposes_failed_remote_archive_and_keeps_health_faulted(tmp_path, monkeypatch):
    import json
    import cloud_network_benchmark.campaign_supervisor as supervisor

    state = tmp_path / "state"
    stage_dir = state / "stage-1"
    stage_dir.mkdir(parents=True)
    stage = {"stage_id": "stage-1", "stage": "acceptance", "results_root": str(tmp_path / "results"),
        "minimum_free_bytes": 0, "entries": []}
    journal = {"entries": {"entry-1": {"state": "failed", "result": {
        "cleanup_open": True, "provider_results": {"aws": {
            "execution_state": "failed", "cleanup_state": "succeeded",
            "archive_status": {"status": "failed", "verified": False,
                "partial_objects": [{"key": "cnb/v1/aws/exp/run/manifest.json", "generation": "7"}]}}}}}}}
    (stage_dir / "prepared-stage.json").write_text(json.dumps(stage))
    (stage_dir / "campaign-journal.json").write_text(json.dumps(journal))
    (state / "active-stage.json").write_text(json.dumps({"stage_id": "stage-1",
        "stage_path": str(stage_dir / "prepared-stage.json")}))
    monkeypatch.setattr(supervisor, "_read_only_preflight", lambda _: {"status": "qualified"})

    class StopAfterOne:
        calls = 0
        def is_set(self):
            return self.calls > 0
        def wait(self, _):
            self.calls += 1

    supervisor.observe(state, StopAfterOne(), interval=0)
    snapshot = json.loads((state / "observer-status.json").read_text())
    assert snapshot["health"] == "fault"
    assert snapshot["remote_archive_status"]["entry-1"]["aws"]["verified"] is False
    assert snapshot["remote_archive_faults"][0]["provider"] == "aws"
    assert snapshot["cleanup_liabilities"][0]["entry_id"] == "entry-1"


def test_observer_exposes_secret_free_archive_progress_during_running_entry(tmp_path, monkeypatch):
    import json
    import cloud_network_benchmark.campaign_supervisor as supervisor
    state = tmp_path / "state"
    stage_dir = state / "stage-1"
    status_root = tmp_path / "archive-status"
    stage_dir.mkdir(parents=True)
    status_root.mkdir()
    runtime = tmp_path / "runtime.json"
    runtime.write_text(json.dumps({"archive_sink": {"archive_root": str(status_root)}}))
    (status_root / "aws-run-1.status.json").write_text(json.dumps({
        "status": "progress", "verified": False, "verified_parts": 3,
        "verified_bytes": 12288, "credential": "must-not-appear"}))
    stage = {"stage_id": "stage-1", "stage": "acceptance", "results_root": str(tmp_path / "results"),
        "minimum_free_bytes": 0, "entries": [{"entry_id": "entry-1",
            "runtime_config_path": str(runtime), "run_ids": {"aws": "run-1"}}]}
    journal = {"entries": {"entry-1": {"state": "running", "result": {}}}}
    (stage_dir / "prepared-stage.json").write_text(json.dumps(stage))
    (stage_dir / "campaign-journal.json").write_text(json.dumps(journal))
    (state / "active-stage.json").write_text(json.dumps({"stage_id": "stage-1",
        "stage_path": str(stage_dir / "prepared-stage.json")}))
    monkeypatch.setattr(supervisor, "_read_only_preflight", lambda _: {"status": "qualified"})

    class StopAfterOne:
        calls = 0
        def is_set(self): return self.calls > 0
        def wait(self, _): self.calls += 1

    supervisor.observe(state, StopAfterOne(), interval=0)
    snapshot_text = (state / "observer-status.json").read_text()
    snapshot = json.loads(snapshot_text)
    assert snapshot["remote_archive_status"]["entry-1"]["aws"]["status"] == "progress"
    assert snapshot["remote_archive_status"]["entry-1"]["aws"]["verified_parts"] == 3
    assert "must-not-appear" not in snapshot_text


def test_periodic_preflight_reports_identity_terraform_and_expiry_without_leaking_output(monkeypatch):
    import cloud_network_benchmark.campaign_identity as identity
    import cloud_network_benchmark.campaign_supervisor as supervisor
    from datetime import datetime, timedelta, timezone

    monkeypatch.setattr(identity, "check_provider_identities", lambda _: [])
    class Completed:
        returncode = 0
        stdout = '{"terraform_version":"1.10.2"}'
    monkeypatch.setattr(supervisor.subprocess, "run", lambda *a, **k: Completed())
    stage = {"observer_identity": {"aws": {"account_id": "000000000000"}},
             "credential_valid_through_utc": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
    result = supervisor._read_only_preflight(stage)
    assert result["status"] == "qualified"
    assert result["terraform_version"] == "1.10.2"
    stage["credential_valid_through_utc"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    assert supervisor._read_only_preflight(stage)["status"] == "blocked"
