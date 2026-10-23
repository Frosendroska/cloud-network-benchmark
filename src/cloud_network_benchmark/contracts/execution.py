from __future__ import annotations

import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from math import isfinite
from typing import Dict, List, Literal, Optional, Protocol, Sequence

from pydantic import ConfigDict, Field, model_validator

from .common import CommandOutcome, StrictModel


class CommandRequest(StrictModel):
    action_id: str = Field(min_length=1)
    action_type: str = Field(min_length=1)
    argv: List[str] = Field(min_length=1)
    cwd: str = Field(min_length=1)
    environment: Dict[str, str] = Field(default_factory=dict, exclude=True)
    environment_classification: Literal["none", "non_secret", "contains_secret_references"] = "none"
    acquisition_invocation_id: Optional[str] = None
    timeout_seconds: float = Field(gt=0)
    stdin: Optional[str] = Field(default=None, exclude=True)
    redactions: List[str] = Field(default_factory=list, exclude=True)

    def durable_view(self) -> Dict[str, object]:
        payload = self.model_dump()
        payload["environment_keys"] = sorted(self.environment)
        payload["acquisition_invocation_id"] = self.acquisition_invocation_id
        payload["stdin_supplied"] = self.stdin is not None
        for secret in sorted((item for item in self.redactions if item), key=len, reverse=True):
            payload["argv"] = [item.replace(secret, "[REDACTED]") for item in payload["argv"]]
            payload["cwd"] = payload["cwd"].replace(secret, "[REDACTED]")
        return payload


class CommandResult(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True, json_schema_extra={
        "allOf": [
            {"if": {"not": {"properties": {"duration_basis": {"const": "monotonic"}}, "required": ["duration_basis"]}},
             "then": {"properties": {"monotonic_started": {"type": "null"},
                                      "monotonic_finished": {"type": "null"},
                                      "utc_elapsed_discrepancy_seconds": {"type": "null"}}}},
            {"if": {"properties": {"duration_basis": {"const": "monotonic"}}, "required": ["duration_basis"]},
             "then": {"required": ["monotonic_started", "monotonic_finished", "utc_elapsed_discrepancy_seconds"],
                      "properties": {"monotonic_started": {"type": "number", "minimum": 0},
                                     "monotonic_finished": {"type": "number", "minimum": 0},
                                     "utc_elapsed_discrepancy_seconds": {"type": "number"}}}},
        ]
    })
    action_id: str
    outcome: CommandOutcome
    started_at: datetime
    finished_at: datetime
    duration_seconds: float = Field(ge=0, strict=True)
    duration_basis: Literal["utc", "monotonic"] = "utc"
    monotonic_started: Optional[float] = Field(default=None, ge=0, strict=True)
    monotonic_finished: Optional[float] = Field(default=None, ge=0, strict=True)
    utc_elapsed_discrepancy_seconds: Optional[float] = Field(default=None, strict=True)
    exit_code: Optional[int]
    stdout: str
    stderr: str
    redactions: List[str] = Field(default_factory=list, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def reject_boolean_timing(cls, value: object) -> object:
        if isinstance(value, dict):
            for name in ("duration_seconds", "monotonic_started", "monotonic_finished", "utc_elapsed_discrepancy_seconds"):
                if isinstance(value.get(name), bool):
                    raise ValueError(f"{name} must be numeric, not boolean")
        return value

    @model_validator(mode="after")
    def consistent(self) -> "CommandResult":
        if self.started_at.tzinfo is None or self.started_at.utcoffset() is None or self.finished_at.tzinfo is None or self.finished_at.utcoffset() is None:
            raise ValueError("command timestamps must include a timezone")
        if not isfinite(self.duration_seconds):
            raise ValueError("duration_seconds must be finite")
        object.__setattr__(self, "started_at", self.started_at.astimezone(timezone.utc))
        object.__setattr__(self, "finished_at", self.finished_at.astimezone(timezone.utc))
        if self.duration_basis == "utc":
            if any(value is not None for value in (self.monotonic_started, self.monotonic_finished, self.utc_elapsed_discrepancy_seconds)):
                raise ValueError("UTC duration basis cannot include monotonic timing fields")
            if self.finished_at < self.started_at:
                raise ValueError("finished_at precedes started_at")
            elapsed = (self.finished_at - self.started_at).total_seconds()
            if abs(elapsed - self.duration_seconds) > 0.001:
                raise ValueError("duration_seconds must match command timestamps")
        else:
            if self.monotonic_started is None or self.monotonic_finished is None or self.utc_elapsed_discrepancy_seconds is None:
                raise ValueError("monotonic duration basis requires complete timing evidence")
            values = (self.monotonic_started, self.monotonic_finished, self.utc_elapsed_discrepancy_seconds)
            if not all(isfinite(value) for value in values):
                raise ValueError("monotonic timing values must be finite")
            if self.monotonic_finished < self.monotonic_started:
                raise ValueError("monotonic finish must not precede start")
            monotonic_elapsed = self.monotonic_finished - self.monotonic_started
            utc_elapsed = (self.finished_at - self.started_at).total_seconds()
            if abs(monotonic_elapsed - self.duration_seconds) > 0.001:
                raise ValueError("duration_seconds must match monotonic timestamps")
            if abs(self.utc_elapsed_discrepancy_seconds - (utc_elapsed - monotonic_elapsed)) > 0.001:
                raise ValueError("UTC elapsed discrepancy must match observed clock intervals")
        outcome = CommandOutcome(self.outcome)
        if outcome == CommandOutcome.SUCCEEDED and self.exit_code != 0:
            raise ValueError("succeeded requires exit code 0")
        if outcome == CommandOutcome.FAILED and (self.exit_code is None or self.exit_code == 0):
            raise ValueError("failed requires non-zero exit code")
        if outcome in {CommandOutcome.TIMED_OUT, CommandOutcome.INTERRUPTED} and self.exit_code is not None:
            raise ValueError(f"{outcome.value} requires a null exit code")
        return self

    def durable_view(self) -> Dict[str, object]:
        payload = self.model_dump(mode="json")
        for secret in sorted((item for item in self.redactions if item), key=len, reverse=True):
            payload["stdout"] = payload["stdout"].replace(secret, "[REDACTED]")
            payload["stderr"] = payload["stderr"].replace(secret, "[REDACTED]")
            payload["argv"] = [item.replace(secret, "[REDACTED]") for item in payload.get("argv", [])]
        payload["redactions_applied"] = bool(self.redactions)
        return payload


class CommandRunner(Protocol):
    def run(self, request: CommandRequest) -> CommandResult:
        ...


class SubprocessCommandRunner:
    def run(self, request: CommandRequest) -> CommandResult:
        started = datetime.now(timezone.utc)
        monotonic_started = time.monotonic()
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
        monotonic_finished = time.monotonic()
        finished = datetime.now(timezone.utc)
        duration = monotonic_finished - monotonic_started
        utc_elapsed = (finished - started).total_seconds()
        return CommandResult(
            action_id=request.action_id,
            outcome=outcome,
            started_at=started,
            finished_at=finished,
            duration_seconds=duration,
            duration_basis="monotonic",
            monotonic_started=monotonic_started,
            monotonic_finished=monotonic_finished,
            utc_elapsed_discrepancy_seconds=utc_elapsed - duration,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            redactions=request.redactions,
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
        if result.action_id != request.action_id:
            raise ValueError("result action_id must match request action_id")
        self._script.pop(0)
        self.requests.append(request)
        return result

    def assert_exhausted(self) -> None:
        if self._script:
            raise AssertionError(f"{len(self._script)} scripted command(s) not consumed")
