from datetime import datetime, timezone

import pytest

from cloud_network_benchmark.contracts import CommandRequest, CommandResult, ScriptedCommandRunner


NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


def request(action_id: str = "terraform-plan") -> CommandRequest:
    return CommandRequest(action_id=action_id, action_type="terraform", argv=["terraform", "plan"], cwd="terraform/aws", environment={"TOKEN": "secret"}, environment_classification="contains_secret_references", timeout_seconds=30, stdin="sensitive")


def result(action_id: str = "terraform-plan", outcome: str = "succeeded", exit_code: int = 0) -> CommandResult:
    return CommandResult(action_id=action_id, outcome=outcome, started_at=NOW, finished_at=NOW, duration_seconds=0, exit_code=exit_code, stdout="ok", stderr="")


def test_durable_request_excludes_secrets() -> None:
    durable = request().durable_view()
    assert "environment" not in durable and "stdin" not in durable
    assert durable["environment_keys"] == ["TOKEN"]


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
