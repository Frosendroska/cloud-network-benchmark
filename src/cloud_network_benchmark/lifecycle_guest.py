"""Bounded VM-A entry point that delegates execution to the existing F03 runner."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from .contracts import IdentityContext, ResolvedObservation, StringEvidence
from .contracts.common import StrictModel
from .measurement import MeasurementContext, run_measurement
from .measurement_process import LocalMeasurementProcesses, SystemMeasurementClock


class GuestRequest(StrictModel):
    schema_version: int = Field(strict=True, ge=2, le=2)
    identity: IdentityContext
    observation: ResolvedObservation
    b_private_ipv4: str
    output_root: Path
    effective_deadline_utc: datetime
    safe_remaining_seconds: float = Field(gt=0)
    clock_uncertainty_seconds: float = Field(ge=0)
    source_revision: str = Field(min_length=1)
    expected_source_revision: str = Field(min_length=1)
    package_root: Path
    package_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    shutdown_grace_seconds: float = Field(gt=0)
    tool_identity: dict[str, StringEvidence] = Field(default_factory=dict)
    build_observations: list[dict[str, Any]] = Field(default_factory=list)
    receiver_command_evidence: list[dict[str, Any]] = Field(default_factory=list)
    source_artifact_records: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def identity_and_deadline(self):
        if self.identity.provider != self.observation.provider or self.identity.deployment_id != f"{self.identity.run_id}-deployment":
            raise ValueError("guest request identity must match the resolved observation")
        if self.source_revision != self.expected_source_revision:
            raise ValueError("guest package revision differs from controller-selected source")
        if self.effective_deadline_utc.tzinfo is None or self.effective_deadline_utc.utcoffset() is None:
            raise ValueError("guest deadline must be timezone-aware UTC evidence")
        if not self.output_root.is_absolute() or not self.package_root.is_absolute():
            raise ValueError("guest package and output paths must be absolute")
        if self.safe_remaining_seconds <= self.clock_uncertainty_seconds:
            raise ValueError("safe duration is exhausted by clock uncertainty")
        return self


class GuestOutcome(StrictModel):
    schema_version: int = 2
    identity: IdentityContext
    outcome: str
    executed_unit_ids: list[str]
    unexecuted_unit_ids: list[str]
    commands: list[dict[str, Any]]
    timings: list[dict[str, Any]]
    artifact_paths: list[str]
    failures: list[dict[str, Any]] = Field(default_factory=list)
    source_revision: str
    package_sha256: str
    guest_started_at: datetime
    guest_finished_at: datetime


def package_tree_sha256(root: Path) -> str:
    root = root.resolve(strict=True)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("package root must be a real directory")
    digest = hashlib.sha256()
    files = sorted(path for path in root.rglob("*") if path.is_file())
    if any(path.is_symlink() for path in root.rglob("*")):
        raise ValueError("package tree cannot contain symlinks")
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big")); digest.update(relative)
        data = path.read_bytes()
        digest.update(len(data).to_bytes(8, "big")); digest.update(data)
    return digest.hexdigest()


def execute_request(request: GuestRequest, *, processes=None, clock=None) -> GuestOutcome:
    request = GuestRequest.model_validate(request.model_dump(mode="python"))
    clock = clock or SystemMeasurementClock()
    processes = processes or LocalMeasurementProcesses(clock)
    if request.observation.provider != request.identity.provider:
        raise ValueError("provider identity mismatch")
    if package_tree_sha256(request.package_root) != request.package_sha256:
        raise ValueError("staged package bytes do not match the selected digest")
    now_utc = clock.utc_now().astimezone(timezone.utc)
    utc_remaining = (request.effective_deadline_utc.astimezone(timezone.utc) - now_utc).total_seconds()
    safe = min(request.safe_remaining_seconds - request.clock_uncertainty_seconds, utc_remaining)
    if safe <= 0:
        raise TimeoutError("no safe guest-local runtime remains before the effective UTC deadline")
    started = now_utc
    context = MeasurementContext(
        identity=request.identity, b_private_ipv4=request.b_private_ipv4,
        root=request.output_root, deadline_monotonic=clock.monotonic() + safe,
        shutdown_grace_seconds=request.shutdown_grace_seconds,
        tool_identity=request.tool_identity,
        build_observations=tuple(_validated_build(item) for item in request.build_observations),
        receiver_command_evidence=tuple(_validated_command(item) for item in request.receiver_command_evidence),
        source_artifact_records=tuple(_validated_artifact(item) for item in request.source_artifact_records),
    )
    result = run_measurement(request.observation, context, processes=processes, clock=clock)
    return GuestOutcome(
        identity=request.identity, outcome=result.outcome,
        executed_unit_ids=list(result.executed_unit_ids), unexecuted_unit_ids=list(result.unexecuted_unit_ids),
        commands=[item.model_dump(mode="json") for item in result.command_evidence],
        timings=[item.model_dump(mode="json") for item in result.timings],
        artifact_paths=list(result.artifact_paths),
        failures=[item.model_dump(mode="json") for item in result.failures],
        source_revision=request.source_revision, package_sha256=request.package_sha256,
        guest_started_at=started, guest_finished_at=clock.utc_now().astimezone(timezone.utc),
    )


def _validated_build(value):
    from .bootstrap import BuildObservation
    return BuildObservation.model_validate(value)


def _validated_command(value):
    from .contracts.measurements import CommandEvidence
    return CommandEvidence.model_validate(value)


def _validated_artifact(value):
    from .contracts.artifacts import ArtifactRecord
    return ArtifactRecord.model_validate(value)


def _json_default(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(type(value).__name__)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Internal VM-A measurement entry point")
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--outcome", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if not args.request.is_absolute() or not args.outcome.is_absolute() or args.request == args.outcome:
            raise ValueError("request and outcome must be distinct absolute paths")
        request = GuestRequest.model_validate_json(args.request.read_text(encoding="utf-8"))
        result = execute_request(request)
        args.outcome.parent.mkdir(parents=True, exist_ok=True)
        with args.outcome.open("x", encoding="utf-8") as stream:
            json.dump(result.model_dump(mode="json"), stream, sort_keys=True, indent=2)
            stream.write("\n")
        return 0
    except KeyboardInterrupt:
        print("measurement interrupted; local process manager received cancellation", file=sys.stderr)
        return 1
    except Exception as exc:
        print(json.dumps({"outcome": "failed", "error_type": type(exc).__name__, "message": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
