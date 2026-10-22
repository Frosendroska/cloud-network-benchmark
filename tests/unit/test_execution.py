from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path

import pytest

from cloud_network_benchmark.contracts import CommandRequest, CommandResult, ScriptedCommandRunner
from cloud_network_benchmark.contracts import execution


NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


def request(action_id: str = "terraform-plan") -> CommandRequest:
    return CommandRequest(action_id=action_id, action_type="terraform", argv=["terraform", "plan"], cwd="terraform/aws", environment={"TOKEN": "secret"}, environment_classification="contains_secret_references", timeout_seconds=30, stdin="sensitive")


def result(action_id: str = "terraform-plan", outcome: str = "succeeded", exit_code: int = 0) -> CommandResult:
    return CommandResult(action_id=action_id, outcome=outcome, started_at=NOW, finished_at=NOW, duration_seconds=0, exit_code=exit_code, stdout="ok", stderr="")


def test_durable_request_excludes_secrets() -> None:
    durable = request().durable_view()
    assert "environment" not in durable and "stdin" not in durable
    assert durable["environment_keys"] == ["TOKEN"]


def test_durable_request_redacts_secret_argv_and_cwd_values() -> None:
    secret = CommandRequest(action_id="x", action_type="test", argv=["tool", "--token", "sentinel"],
                           cwd="/tmp/sentinel", timeout_seconds=1, redactions=["sentinel"])
    durable = secret.durable_view()
    assert "sentinel" not in str(durable)
    assert "[REDACTED]" in str(durable)


def test_scripted_runner_records_exact_order() -> None:
    expected = request()
    runner = ScriptedCommandRunner([(expected, result())])
    assert runner.run(expected).outcome == "succeeded"
    runner.assert_exhausted()
    assert runner.requests == [expected]
    with pytest.raises(AssertionError, match="unexpected"):
        runner.run(expected)


def test_scripted_runner_detects_mismatch_and_unconsumed() -> None:
    runner = ScriptedCommandRunner([(request(), result())])
    with pytest.raises(AssertionError, match="mismatch"):
        runner.run(request("ssh"))
    with pytest.raises(AssertionError, match="not consumed"):
        runner.assert_exhausted()


def test_scripted_runner_rejects_result_for_another_action() -> None:
    runner = ScriptedCommandRunner([(request(), result("different-action"))])
    with pytest.raises(ValueError, match="action_id"):
        runner.run(request())


def test_command_result_checks_elapsed_time_and_secret_redaction() -> None:
    with pytest.raises(ValueError, match="duration"):
        CommandResult(
            action_id="terraform-plan", outcome="succeeded", started_at=NOW,
            finished_at=NOW.replace(second=2), duration_seconds=1, exit_code=0,
            stdout="ok", stderr="",
        )
    safe = CommandResult(
        action_id="terraform-plan", outcome="succeeded", started_at=NOW,
        finished_at=NOW, duration_seconds=0, exit_code=0,
        stdout="token=secret-value", stderr="", redactions=["secret-value"],
    ).durable_view()
    assert "secret-value" not in str(safe)
    assert "[REDACTED]" in safe["stdout"]


def monotonic_result(*, utc_delta: float, elapsed: float = 2.0, start: datetime = NOW, **updates: object) -> CommandResult:
    values: dict[str, object] = {
        "action_id": "clock-test", "outcome": "succeeded", "started_at": start,
        "finished_at": start + timedelta(seconds=utc_delta), "duration_seconds": elapsed,
        "exit_code": 0, "stdout": "ok", "stderr": "", "duration_basis": "monotonic",
        "monotonic_started": 100.0, "monotonic_finished": 100.0 + elapsed,
        "utc_elapsed_discrepancy_seconds": utc_delta - elapsed,
    }
    values.update(updates)
    return CommandResult(**values)


@pytest.mark.parametrize("utc_delta, discrepancy", [(-5.0, -7.0), (12.0, 10.0), (2.0, 0.0), (0.0, -2.0)])
def test_monotonic_result_preserves_actual_utc_and_signed_discrepancy(utc_delta: float, discrepancy: float) -> None:
    observed = monotonic_result(utc_delta=utc_delta)
    assert observed.started_at == NOW
    assert observed.finished_at == NOW + timedelta(seconds=utc_delta)
    assert observed.duration_seconds == 2
    assert observed.utc_elapsed_discrepancy_seconds == discrepancy


def test_command_result_normalizes_aware_offsets_and_accepts_consistency_tolerance() -> None:
    offset_start = datetime(2026, 9, 27, 2, tzinfo=timezone(timedelta(hours=2)))
    observed = monotonic_result(utc_delta=2.0, elapsed=2.0009, start=offset_start,
                                utc_elapsed_discrepancy_seconds=-0.0009)
    assert observed.started_at == NOW
    assert observed.started_at.tzinfo == timezone.utc


@pytest.mark.parametrize("updates", [
    {"started_at": datetime(2026, 9, 27)},
    {"duration_seconds": True}, {"duration_seconds": math.nan}, {"duration_seconds": math.inf},
    {"monotonic_started": True}, {"monotonic_started": math.nan}, {"monotonic_started": -1},
    {"monotonic_finished": math.inf}, {"monotonic_finished": 99},
    {"monotonic_finished": None}, {"utc_elapsed_discrepancy_seconds": None},
    {"utc_elapsed_discrepancy_seconds": True}, {"utc_elapsed_discrepancy_seconds": math.inf},
    {"duration_basis": "other"}, {"duration_basis": "utc", "monotonic_started": 100.0},
    {"duration_seconds": 2.01}, {"utc_elapsed_discrepancy_seconds": -6.0},
])
def test_monotonic_result_rejects_malformed_or_contradictory_values(updates: dict[str, object]) -> None:
    with pytest.raises((ValueError, TypeError)):
        monotonic_result(utc_delta=-5.0, **updates)


@pytest.mark.parametrize("duration", [math.nan, math.inf, -math.inf, True])
def test_utc_result_rejects_nonfinite_or_boolean_duration(duration: object) -> None:
    with pytest.raises((ValueError, TypeError)):
        CommandResult(action_id="clock-test", outcome="succeeded", started_at=NOW,
                      finished_at=NOW, duration_seconds=duration, exit_code=0, stdout="", stderr="")


def test_utc_result_remains_strict_and_normalizes_aware_offsets() -> None:
    offset_start = datetime(2026, 9, 27, 2, tzinfo=timezone(timedelta(hours=2)))
    observed = CommandResult(action_id="clock-test", outcome="succeeded", started_at=offset_start,
                             finished_at=NOW, duration_seconds=0, exit_code=0, stdout="", stderr="")
    assert observed.started_at == NOW
    with pytest.raises(ValueError):
        CommandResult(action_id="clock-test", outcome="succeeded", started_at=NOW,
                      finished_at=NOW - timedelta(seconds=1), duration_seconds=0, exit_code=0, stdout="", stderr="")


@pytest.mark.parametrize("outcome, exit_code, utc_delta", [
    ("succeeded", 0, -5.0), ("failed", 3, 12.0), ("timed_out", None, -5.0), ("interrupted", None, 12.0),
])
def test_subprocess_runner_uses_monotonic_elapsed_and_preserves_step_timestamps(
    monkeypatch: pytest.MonkeyPatch, outcome: str, exit_code: object, utc_delta: float,
) -> None:
    utc_values = iter([NOW, NOW + timedelta(seconds=utc_delta)])
    mono_values = iter([100.0, 102.0])

    class FakeDateTime:
        @staticmethod
        def now(tz: object = None) -> datetime:
            assert tz is timezone.utc
            return next(utc_values)

    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def run(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        if outcome == "timed_out":
            raise execution.subprocess.TimeoutExpired(args[0], 30, output="partial out", stderr="partial err")
        if outcome == "interrupted":
            raise KeyboardInterrupt
        return type("Completed", (), {"returncode": exit_code, "stdout": "token=sentinel", "stderr": ""})()

    monkeypatch.setattr(execution, "datetime", FakeDateTime)
    monkeypatch.setattr(execution.time, "monotonic", lambda: next(mono_values))
    monkeypatch.setattr(execution.subprocess, "run", run)
    req = CommandRequest(action_id="clock-test", action_type="test", argv=["tool", "sentinel"],
                         cwd=".", timeout_seconds=30, redactions=["sentinel"])
    observed = execution.SubprocessCommandRunner().run(req)
    assert (observed.outcome, observed.exit_code) == (outcome, exit_code)
    assert observed.started_at == NOW and observed.finished_at == NOW + timedelta(seconds=utc_delta)
    assert observed.duration_basis == "monotonic"
    assert observed.duration_seconds == 2
    assert observed.monotonic_started == 100 and observed.monotonic_finished == 102
    assert observed.utc_elapsed_discrepancy_seconds == utc_delta - 2
    if outcome == "timed_out":
        assert observed.stdout == "partial out" and observed.stderr == "partial err"
    elif outcome == "interrupted":
        assert observed.stderr == "interrupted"
    else:
        assert observed.stdout == "token=sentinel"
    assert "sentinel" not in str(observed.durable_view())
    assert calls[0][0] == (["tool", "sentinel"],)
    assert calls[0][1]["shell"] is False and calls[0][1]["timeout"] == 30


def test_scripted_runner_preserves_supplied_monotonic_timing() -> None:
    expected = request("clock-test")
    supplied = monotonic_result(utc_delta=-5.0)
    runner = ScriptedCommandRunner([(expected, supplied)])
    assert runner.run(expected) is supplied


@pytest.mark.parametrize(
    ("outcome", "exit_code"),
    [("succeeded", 1), ("failed", 0), ("failed", None), ("timed_out", 0), ("interrupted", 130)],
)
def test_result_consistency(outcome: str, exit_code: object) -> None:
    with pytest.raises(ValueError):
        result(outcome=outcome, exit_code=exit_code)


@pytest.mark.parametrize(("outcome", "exit_code"), [("succeeded", 0), ("failed", 2), ("timed_out", None), ("interrupted", None)])
def test_all_scripted_outcomes_are_representable(outcome: str, exit_code: object) -> None:
    assert result(outcome=outcome, exit_code=exit_code).outcome == outcome


def test_scripted_outcome_fixture_materializes_and_preserves_partial_files(tmp_path: Path) -> None:
    fixture = json.loads((Path(__file__).parents[1] / "fixtures/commands/outcomes.json").read_text())
    for outcome, details in fixture.items():
        command = request(outcome)
        expected = CommandResult(action_id=outcome, outcome=outcome, started_at=NOW, finished_at=NOW,
                                 duration_seconds=0, exit_code=details["exit_code"], stdout=details["stdout"],
                                 stderr=details["stderr"])
        runner = ScriptedCommandRunner([(command, expected)])
        observed = runner.run(command)
        runner.assert_exhausted()
        assert observed.stdout == details["stdout"] and observed.stderr == details["stderr"]
        for item in details["materialized_files"]:
            path = tmp_path / outcome / item["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(item["content"])
            assert path.read_text() == item["content"]
