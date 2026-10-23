"""Explicit provider and remote-stage adapters for the F09 lifecycle."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from .contracts.admission import ExpiryRequest
from .contracts.deployment import ProviderDeploymentInput
from .contracts.common import Evidence
from .providers.aws import AwsProviderDriver
from .providers.gcp import GcpProviderDriver


def _deployment_with(deployment: ProviderDeploymentInput, *, reservation: Any = None, expiry: Any = None) -> ProviderDeploymentInput:
    payload = deployment.model_dump(mode="json")
    if reservation is not None:
        payload["reservation"] = Evidence(value=reservation).model_dump(mode="json")
    if expiry is not None:
        payload["expiry"] = Evidence(value=expiry).model_dump(mode="json")
    # Re-parse through the canonical strict model; no unchecked copy can bypass
    # provider/action/identity or admission invariants.
    return ProviderDeploymentInput.model_validate_json(json.dumps(payload))


@dataclass(frozen=True)
class RemoteStages:
    prepare: Callable[[ProviderDeploymentInput, Any], Any]
    stabilize: Callable[[ProviderDeploymentInput, Any], Any]
    measure: Callable[[ProviderDeploymentInput, Any], Any]
    close: Callable[[ProviderDeploymentInput, Any], Any]
    retrieve: Callable[[ProviderDeploymentInput, Any], Any]
    claim: Callable[[ProviderDeploymentInput, Any], Any]


def aws_hooks(driver: AwsProviderDriver, deployment: ProviderDeploymentInput,
              expiry_request: ExpiryRequest, *, stages: RemoteStages,
              reservation: Any = None, claim_data: Any = None):
    from .orchestration import ProviderHooks
    state: dict[str, Any] = {"deployment": _deployment_with(deployment, reservation=reservation)}
    def protect():
        evidence = driver.register_expiry(state["deployment"], expiry_request)
        state["deployment"] = _deployment_with(state["deployment"], expiry=evidence)
        return evidence
    def create():
        state["result"] = driver.provision(state["deployment"])
        return state["result"]
    return ProviderHooks(
        provider="aws", protect=protect,
        claim=lambda: stages.claim(state["deployment"], claim_data),
        create=create,
        prepare=lambda: stages.prepare(state["deployment"], _last_result(state)),
        stabilize=lambda: stages.stabilize(state["deployment"], _last_result(state)),
        measure=lambda: stages.measure(state["deployment"], _last_result(state)),
        close=lambda: stages.close(state["deployment"], _last_result(state)),
        retrieve=lambda: stages.retrieve(state["deployment"], _last_result(state)),
        cleanup=lambda: driver.cleanup(state["deployment"]),
    )


def gcp_hooks(driver: GcpProviderDriver, deployment: ProviderDeploymentInput,
              expiry_request: ExpiryRequest, *, stages: RemoteStages,
              reservation: Any = None, claim_data: Any = None):
    from .orchestration import ProviderHooks
    state: dict[str, Any] = {"deployment": _deployment_with(deployment, reservation=reservation)}
    def protect():
        evidence = driver.register_expiry(state["deployment"], expiry_request)
        state["deployment"] = _deployment_with(state["deployment"], expiry=evidence)
        return evidence
    def create():
        state["result"] = driver.provision(state["deployment"])
        return state["result"]
    def stage(name: str):
        return getattr(stages, name)(state["deployment"], _last_result(state))
    return ProviderHooks(
        provider="gcp", protect=protect,
        claim=lambda: stages.claim(state["deployment"], claim_data),
        create=create, prepare=lambda: stage("prepare"), stabilize=lambda: stage("stabilize"),
        measure=lambda: stage("measure"), close=lambda: stage("close"),
        retrieve=lambda: stage("retrieve"), cleanup=lambda: driver.cleanup(state["deployment"]),
    )


def azure_hooks(deployment: ProviderDeploymentInput, expiry_request: ExpiryRequest, *,
                options: Any, runner: Any, stages: RemoteStages, reservation: Any = None,
                claim_data: Any = None, clock: Callable[[], float],
                utc_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
    from . import azure_provider
    from .orchestration import ProviderHooks
    state: dict[str, Any] = {"deployment": _deployment_with(deployment, reservation=reservation)}
    def protect():
        evidence = azure_provider.register_expiry(state["deployment"], expiry_request, options, runner,
                                                  clock=clock, utc_now=utc_now)
        state["deployment"] = _deployment_with(state["deployment"], expiry=evidence)
        return evidence
    def create():
        state["result"] = azure_provider.provision(state["deployment"], options, runner,
                                                    clock=clock, utc_now=utc_now)
        state["deployment"] = _deployment_with(state["deployment"], expiry=state["result"].expiry)
        return state["result"]
    def stage(name: str):
        return getattr(stages, name)(state["deployment"], _last_result(state))
    return ProviderHooks(
        provider="azure", protect=protect,
        claim=lambda: stages.claim(state["deployment"], claim_data),
        create=create, prepare=lambda: stage("prepare"), stabilize=lambda: stage("stabilize"),
        measure=lambda: stage("measure"), close=lambda: stage("close"),
        retrieve=lambda: stage("retrieve"),
        cleanup=lambda: azure_provider.destroy(state["deployment"], options, runner, clock=clock, utc_now=utc_now),
    )


def _last_result(state: Mapping[str, Any]) -> Any:
    result = state.get("result")
    if result is None:
        raise RuntimeError("provider result is unavailable before successful creation")
    return result


def guest_duration(*, effective_deadline_utc: datetime, guest_now_utc: datetime,
                   controller_observed_remaining: float, transport_elapsed_seconds: float,
                   clock_uncertainty_seconds: float) -> float:
    """Conservatively derive a guest-local duration; never transmit monotonic clocks."""
    if any(value.tzinfo is None or value.utcoffset() is None
           for value in (effective_deadline_utc, guest_now_utc)):
        raise ValueError("guest UTC clock and effective deadline must be timezone-aware")
    values = (controller_observed_remaining, transport_elapsed_seconds, clock_uncertainty_seconds)
    if any(value < 0 for value in values):
        raise ValueError("clock and transport uncertainty must be nonnegative")
    from_utc = (effective_deadline_utc.astimezone(timezone.utc) - guest_now_utc.astimezone(timezone.utc)).total_seconds()
    safe = min(controller_observed_remaining - transport_elapsed_seconds - clock_uncertainty_seconds,
               from_utc - clock_uncertainty_seconds)
    if safe <= 0:
        raise TimeoutError("safe guest-local remaining duration cannot be established")
    return safe
