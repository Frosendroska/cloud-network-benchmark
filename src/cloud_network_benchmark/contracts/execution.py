from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Literal, Optional, Protocol, Sequence

from pydantic import Field, model_validator

from .common import CommandOutcome, StrictModel


class CommandRequest(StrictModel):
    action_id: str = Field(min_length=1)
    action_type: str = Field(min_length=1)
    argv: List[str] = Field(min_length=1)
    cwd: str = Field(min_length=1)
    environment: Dict[str, str] = Field(default_factory=dict, exclude=True)
    environment_classification: Literal["none", "non_secret", "contains_secret_references"] = "none"
    timeout_seconds: float = Field(gt=0)
    stdin: Optional[str] = Field(default=None, exclude=True)

    def durable_view(self) -> Dict[str, object]:
        payload = self.model_dump()
        payload["environment_keys"] = sorted(self.environment)
        payload["stdin_supplied"] = self.stdin is not None
        return payload


class CommandResult(StrictModel):
    action_id: str
    outcome: CommandOutcome
    started_at: datetime
    finished_at: datetime
    duration_seconds: float = Field(ge=0)
    exit_code: Optional[int]
    stdout: str
    stderr: str
    redactions: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent(self) -> "CommandResult":
        if self.finished_at < self.started_at:
            raise ValueError("finished_at precedes started_at")
        outcome = CommandOutcome(self.outcome)
        if outcome == CommandOutcome.SUCCEEDED and self.exit_code != 0:
            raise ValueError("succeeded requires exit code 0")
        if outcome == CommandOutcome.FAILED and (self.exit_code is None or self.exit_code == 0):
            raise ValueError("failed requires non-zero exit code")
        return self


class CommandRunner(Protocol):
    def run(self, request: CommandRequest) -> CommandResult:
        ...


class SubprocessCommandRunner:
    def run(self, request: CommandRequest) -> CommandResult:
        started = datetime.now(timezone.utc)
        try:
            completed = subprocess.run(
                request.argv,
                cwd=Path(request.cwd),
                env=request.environment or None,
                input=request.stdin,
                text=True,
                capture_output=True,
                timeout=request.timeout_seconds,
                shell=False,
                check=False,
            )
            outcome = CommandOutcome.SUCCEEDED if completed.returncode == 0 else CommandOutcome.FAILED
            exit_code, stdout, stderr = completed.returncode, completed.stdout, completed.stderr
        except subprocess.TimeoutExpired as exc:
            outcome, exit_code = CommandOutcome.TIMED_OUT, None
            stdout, stderr = str(exc.stdout or ""), str(exc.stderr or "")
        except KeyboardInterrupt:
            outcome, exit_code, stdout, stderr = CommandOutcome.INTERRUPTED, None, "", "interrupted"
        finished = datetime.now(timezone.utc)
        return CommandResult(
            action_id=request.action_id,
            outcome=outcome,
            started_at=started,
            finished_at=finished,
            duration_seconds=(finished - started).total_seconds(),
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )


class ScriptedCommandRunner:
    def __init__(self, script: Sequence[tuple[CommandRequest, CommandResult]]) -> None:
        self._script = list(script)
        self.requests: List[CommandRequest] = []

    def run(self, request: CommandRequest) -> CommandResult:
        if not self._script:
            raise AssertionError(f"unexpected command: {request.action_id}")
        expected, result = self._script[0]
        if request != expected:
            raise AssertionError(f"command mismatch: expected {expected}, got {request}")
        self._script.pop(0)
        self.requests.append(request)
        return result

    def assert_exhausted(self) -> None:
        if self._script:
            raise AssertionError(f"{len(self._script)} scripted command(s) not consumed")
