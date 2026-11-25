"""Bounded offline qualification for Azure Terraform cleanup deadlines."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from cloud_network_benchmark import azure_provider
from cloud_network_benchmark.contracts.execution import CommandResult
from tests.contract.test_azure_provider_contract import deployment, options


UTC = timezone.utc


class DeadlineRunner:
    def __init__(self, *, clock, elapsed=0.0, outcome="succeeded"):
        self.clock = clock
        self.elapsed = elapsed
        self.outcome = outcome
        self.requests = []
        self.cancelled_and_reaped = False

    def run(self, request):
        self.requests.append(request)
        start = datetime(2026, 11, 24, tzinfo=UTC) + timedelta(seconds=self.clock["mono"])
        if self.elapsed > request.timeout_seconds:
            # Model subprocess.run timing out: its production boundary kills and
            # reaps before returning TIMED_OUT to _tf/_command.
            self.clock["mono"] += request.timeout_seconds
            self.cancelled_and_reaped = True
            return CommandResult(action_id=request.action_id, outcome="timed_out",
                started_at=start, finished_at=start + timedelta(seconds=request.timeout_seconds),
                duration_seconds=request.timeout_seconds, exit_code=None,
                stdout="", stderr="bounded command timed out")
        self.clock["mono"] += self.elapsed
        return CommandResult(action_id=request.action_id, outcome=self.outcome,
            started_at=start, finished_at=start + timedelta(seconds=self.elapsed),
            duration_seconds=self.elapsed, exit_code=0 if self.outcome == "succeeded" else 1,
            stdout="", stderr="")


def configured_cleanup(repository_root: Path, tmp_path: Path, *, remaining=500.0):
    d = deployment(repository_root, "exp-003-placement-optimization.yaml")
    o = options(tmp_path, d.admission.priority).model_copy(update={
        "workflow_profile": "all_cloud_acceptance_v1",
        "command_timeout_seconds": 120.0,
        "cap_deadline_monotonic": 1000.0,
        "cleanup_timing_path": tmp_path / "cleanup-timing.json",
        "cleanup_timing_active": True,
        "cleanup_timing_phase": "routine",
    })
    mono = 100.0
    wall = datetime.now(UTC)
    o.cleanup_timing_path.write_text(json.dumps({
        "routine_deadline_monotonic": remaining,
        "routine_deadline_utc": (wall + timedelta(seconds=remaining - mono)).isoformat(),
    }), encoding="utf-8")
    return d, o, mono, wall


def terraform_destroy(options, runner, clock):
    return azure_provider._tf(Path(options.cleanup_timing_path.parent), options, runner,
        lambda: clock["mono"], "terraform/azure/expiry", "destroy", "-auto-approve",
        "-input=false", action="expiry_destroy")


def test_expiry_destroy_can_use_more_than_120_seconds_within_original_cleanup_budget(
        repository_root: Path, tmp_path: Path):
    _, opts, mono, wall = configured_cleanup(repository_root, tmp_path)
    clock = {"mono": mono}
    token = azure_provider._EXPLICIT_UTC_NOW.set(
        lambda: wall + timedelta(seconds=clock["mono"] - mono))
    try:
        runner = DeadlineRunner(clock=clock, elapsed=125.0)
        result = terraform_destroy(opts, runner, clock)
    finally:
        azure_provider._EXPLICIT_UTC_NOW.reset(token)
    assert result.outcome == "succeeded"
    assert runner.requests[0].timeout_seconds >= 125.0


def test_expiry_destroy_fails_closed_when_original_cleanup_time_is_insufficient(
        repository_root: Path, tmp_path: Path):
    _, opts, mono, wall = configured_cleanup(repository_root, tmp_path, remaining=220.0)
    clock = {"mono": mono}
    token = azure_provider._EXPLICIT_UTC_NOW.set(
        lambda: wall + timedelta(seconds=clock["mono"] - mono))
    try:
        runner = DeadlineRunner(clock=clock, elapsed=125.0)
        with pytest.raises(RuntimeError, match="expiry_destroy failed with timed_out"):
            terraform_destroy(opts, runner, clock)
    finally:
        azure_provider._EXPLICIT_UTC_NOW.reset(token)
    assert runner.requests[0].timeout_seconds == pytest.approx(120.0)
    assert runner.cancelled_and_reaped


def test_expiry_destroy_exact_deadline_crossing_is_cancelled_and_reaped(
        repository_root: Path, tmp_path: Path):
    _, opts, mono, wall = configured_cleanup(repository_root, tmp_path, remaining=225.0)
    clock = {"mono": mono}
    token = azure_provider._EXPLICIT_UTC_NOW.set(
        lambda: wall + timedelta(seconds=clock["mono"] - mono))
    try:
        runner = DeadlineRunner(clock=clock, elapsed=125.1)
        with pytest.raises(RuntimeError, match="expiry_destroy failed with timed_out"):
            terraform_destroy(opts, runner, clock)
    finally:
        azure_provider._EXPLICIT_UTC_NOW.reset(token)
    assert clock["mono"] == 225.0
    assert runner.cancelled_and_reaped


def test_replayed_destroy_keeps_original_deadline_and_other_azure_commands_keep_120s_cap(
        repository_root: Path, tmp_path: Path):
    _, opts, mono, wall = configured_cleanup(repository_root, tmp_path)
    clock = {"mono": mono}
    original = json.loads(opts.cleanup_timing_path.read_text(encoding="utf-8"))
    token = azure_provider._EXPLICIT_UTC_NOW.set(
        lambda: wall + timedelta(seconds=clock["mono"] - mono))
    try:
        first = DeadlineRunner(clock=clock, elapsed=90.0)
        terraform_destroy(opts, first, clock)
        replay = DeadlineRunner(clock=clock, elapsed=0.0)
        terraform_destroy(opts, replay, clock)
        probe = DeadlineRunner(clock=clock, elapsed=0.0)
        azure_provider._command(probe, opts, tmp_path, ["az", "group", "exists"],
            "cleanup_probe", lambda: clock["mono"])
    finally:
        azure_provider._EXPLICIT_UTC_NOW.reset(token)
    assert json.loads(opts.cleanup_timing_path.read_text(encoding="utf-8")) == original
    assert replay.requests[0].timeout_seconds == pytest.approx(310.0)
    assert probe.requests[0].timeout_seconds <= 120.0
