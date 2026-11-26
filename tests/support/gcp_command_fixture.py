"""Offline GCP command replies for production provider paths in composed tests.

This adapter deliberately delegates response construction to the established
source-shaped GCP fixture. It does not replace provider hooks or validators.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from cloud_network_benchmark.contracts.execution import CommandRequest, CommandResult


class Clock(Protocol):
    def utc_now(self) -> datetime: ...
    def sleep(self, seconds: float) -> None: ...


@dataclass(frozen=True)
class GcpRunContext:
    provider: str
    run_id: str
    deployment_id: str
    driver: object
    deployment: object
    clock: Clock
    runtime_roots: tuple[Path, ...]


class GcpCommandFixture:
    """A CommandRunner leaf bound to one synthetic GCP run."""

    def __init__(self, context: GcpRunContext):
        if context.provider != "gcp":
            raise ValueError("GCP fixture requires provider='gcp'")
        if not context.runtime_roots:
            raise ValueError("at least one runtime root is required")
        self.context = context
        # Keep the existing deterministic gcloud/Terraform response fixture as
        # the sole response producer; the production GCP driver remains real.
        from tests.integration.test_f09_multi_provider_factory import GcpSourceRunner

        self._source = GcpSourceRunner()
        self._source.driver = context.driver
        self._source.deployment = context.deployment
        self._source.clock = context.clock
        self._protection_created_at: datetime | None = None

    def _check_request(self, request: CommandRequest) -> None:
        if not request.action_id.startswith("gcp-"):
            raise AssertionError(f"non-GCP request sent to GCP fixture: {request.action_id}")
        if request.cwd:
            cwd = Path(request.cwd).resolve()
            roots = tuple(root.resolve() for root in self.context.runtime_roots)
            if not any(cwd == root or root in cwd.parents for root in roots):
                raise AssertionError(f"GCP request cwd is outside this run's runtime roots: {cwd}")

    def run(self, request: CommandRequest) -> CommandResult:
        self._check_request(request)
        if self.context.driver is None or self.context.deployment is None:
            raise AssertionError("GCP fixture requires the actual driver and deployment")
        if getattr(self.context.deployment, "run_id", None) != self.context.run_id:
            raise AssertionError("GCP fixture run identity does not match deployment")
        if getattr(self.context.deployment, "deployment_id", None) != self.context.deployment_id:
            raise AssertionError("GCP fixture deployment identity does not match deployment")
        result = self._source.run(request)
        if request.action_id == "gcp-protection-apply" and result.outcome == "succeeded":
            # Bind the later API readback to the actual synthetic apply point,
            # which occurs inside this run's reservation window.
            self._protection_created_at = self.context.clock.utc_now()
        elif request.action_id.startswith(("gcp-template-read-", "gcp-template-precreate-")):
            if self._protection_created_at is None:
                raise AssertionError("template readback arrived before the synthetic protection apply")
            def update_template(value):
                value["creationTimestamp"] = self._protection_created_at.isoformat().replace("+00:00", "Z")
                properties = value.get("properties")
                if not isinstance(properties, dict):
                    raise AssertionError("source-shaped GCP template response has no properties object")
                policy_id = self._selected_policy_id()
                properties["resourcePolicies"] = [policy_id] if policy_id else []

            result = self._rewrite_json_result(result, update_template)
        elif request.action_id == "gcp-protection-outputs":
            policy_id = self._selected_policy_id()
            result = self._rewrite_json_result(result, lambda value: value.update({
                "compact_policy_id": {"value": policy_id}
            }))
        elif request.action_id.startswith(("gcp-instance-read-", "gcp-hostkey-instance-")):
            policy_id = self._selected_policy_id()
            result = self._rewrite_json_result(result, lambda value: value.update({
                "resourcePolicies": [policy_id] if policy_id else []
            }))
        return result

    def _selected_policy_id(self) -> str | None:
        deployment = self.context.deployment
        scenario = getattr(deployment.scenario, "value", deployment.scenario)
        if scenario != "placement_optimization":
            return None
        if not deployment.placement.name:
            raise AssertionError("placement scenario has no selected policy name")
        return (
            f"projects/{self.context.driver.settings.project_id}/regions/"
            f"{deployment.vm_a.region}/resourcePolicies/{deployment.placement.name}"
        )

    @staticmethod
    def _rewrite_json_result(result: CommandResult, update) -> CommandResult:
        if result.outcome != "succeeded" or result.exit_code != 0:
            return result
        import json

        payload = json.loads(result.stdout)
        if not isinstance(payload, dict):
            raise AssertionError("source-shaped GCP response must be a JSON object")
        update(payload)
        return result.model_copy(update={"stdout": json.dumps(payload, sort_keys=True)})

    def run_bounded_copy(
        self, request: CommandRequest, destination: Path, maximum_payload_bytes: int
    ) -> CommandResult:
        """Return a deterministic offline refusal; this fixture performs no file copy."""
        self._check_request(request)
        if maximum_payload_bytes < 0:
            raise ValueError("maximum_payload_bytes must be non-negative")
        now = self.context.clock.utc_now()
        return CommandResult(
            action_id=request.action_id,
            outcome="failed",
            started_at=now,
            finished_at=now,
            duration_seconds=0.0,
            exit_code=1,
            stdout="",
            stderr="offline GCP fixture does not perform bounded copies",
        )
