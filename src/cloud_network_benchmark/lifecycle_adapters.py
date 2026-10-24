"""Explicit provider and remote-stage adapters for the F09 lifecycle."""
from __future__ import annotations

import hashlib
import io
import json
import shlex
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from .contracts.admission import ExpiryRequest
from .contracts.deployment import ProviderDeploymentInput
from .contracts.common import Evidence
from .providers.aws import AwsProviderDriver
from .providers.gcp import GcpProviderDriver


@dataclass(frozen=True)
class GuestRuntimeEvidence:
    source_revision: str
    package_sha256: str
    archive_sha256: str
    remote_root: str
    python_version: str
    pydantic_version: str
    yaml_version: str
    actions: tuple[Any, ...]


def stage_guest_runtime(connection: Any, *, source_root: Path, source_revision: str,
                        remote_root: str, runner: Any, deadline: Any,
                        clock: Callable[[], float] = __import__("time").monotonic) -> GuestRuntimeEvidence:
    """Stage the exact package source on A and verify its private venv.

    The guest venv isolates the staged application and records the existing
    Ubuntu Python dependency versions. It uses system site packages so this
    offline-safe step never downloads or resolves new dependencies; a guest
    missing pydantic or PyYAML fails before measurement.
    """
    from .access import _action, _execute, _request
    from .contracts.common import CommandOutcome
    from .lifecycle_guest import package_tree_sha256

    source_root = Path(source_root).resolve(strict=True)
    package_root = source_root / "src" / "cloud_network_benchmark"
    if not package_root.is_dir() or package_root.is_symlink():
        raise ValueError("controller package source directory is unavailable")
    if not source_revision or any(ord(char) < 32 or ord(char) == 127 for char in source_revision):
        raise ValueError("source revision must be a visible nonempty value")
    root = PurePosixPath(remote_root)
    if (not remote_root.startswith("/") or root.as_posix() != remote_root
            or any(part in {".", ".."} for part in root.parts)):
        raise ValueError("guest runtime root must be normalized absolute POSIX")
    package_digest = package_tree_sha256(package_root)
    actions = []
    with tempfile.TemporaryDirectory(prefix="cnb-f09-runtime-") as temp_dir:
        archive = Path(temp_dir) / "runtime-source.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            for path in sorted(package_root.rglob("*")):
                if path.is_symlink():
                    raise ValueError("controller package source cannot contain symlinks")
                if path.is_file():
                    tar.add(path, arcname=(PurePosixPath("source/cloud_network_benchmark")
                                           / path.relative_to(package_root)).as_posix(), recursive=False)
            marker = source_revision.encode("utf-8") + b"\n"
            info = tarfile.TarInfo("runtime-source-revision.txt")
            info.size = len(marker); info.mode = 0o600
            tar.addfile(info, io.BytesIO(marker))
        archive_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        destination = str(root / "runtime-source.tar.gz")
        vm = connection.vm_a
        transfer_timeout = min(connection.timeouts.transfer_seconds, deadline.remaining(clock, readiness=True))
        if transfer_timeout <= 0:
            raise TimeoutError("runtime staging does not fit inside the readiness remainder")
        transfer = _request(f"f09-runtime-stage-{connection.deployment_id}", vm,
            [str(archive), f"{vm.user}@{vm.host}:{destination}"], transfer_timeout, action_type="scp")
        transfer_result = _execute(runner, transfer)
        actions.append(_action(transfer.action_id, transfer.action_type, vm, transfer, transfer_result))
        if transfer_result.outcome != CommandOutcome.SUCCEEDED:
            raise RuntimeError("guest runtime archive transfer failed")

        remaining = deadline.remaining(clock, readiness=True)
        if remaining <= 0:
            raise TimeoutError("runtime verification does not fit inside the readiness remainder")
        runtime = str(root / "runtime")
        python_probe = ("import json,sys,pydantic,yaml; "
            "print(json.dumps({'python':'.'.join(map(str,sys.version_info[:3])),"
            "'pydantic':pydantic.__version__,'yaml':yaml.__version__}))")
        script = "set -eu; " + "; ".join((
            f"mkdir -p {shlex.quote(runtime)}",
            f"test \"$(sha256sum {shlex.quote(destination)} | cut -d ' ' -f1)\" = {shlex.quote(archive_digest)}",
            f"tar -xzf {shlex.quote(destination)} -C {shlex.quote(runtime)}",
            f"test \"$(cat {shlex.quote(runtime + '/runtime-source-revision.txt')})\" = {shlex.quote(source_revision)}",
            f"python3 -m venv --system-site-packages {shlex.quote(runtime + '/venv')}",
            f"PYTHONPATH={shlex.quote(runtime + '/source')} {shlex.quote(runtime + '/venv/bin/python')} -c {shlex.quote(python_probe)}",
        ))
        verify = _request(f"f09-runtime-verify-{connection.deployment_id}", vm,
            ["sh", "-c", script], min(connection.timeouts.command_seconds, remaining))
        verify_result = _execute(runner, verify)
        actions.append(_action(verify.action_id, verify.action_type, vm, verify, verify_result))
        if verify_result.outcome != CommandOutcome.SUCCEEDED:
            raise RuntimeError("guest runtime digest, revision, interpreter or dependency verification failed")
        try:
            versions = json.loads(verify_result.stdout.strip().splitlines()[-1])
            python_version, pydantic_version, yaml_version = (versions[key] for key in ("python", "pydantic", "yaml"))
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ValueError("guest runtime version evidence is malformed") from exc
        if not all(isinstance(value, str) and value for value in
                   (python_version, pydantic_version, yaml_version)):
            raise ValueError("guest runtime version evidence is incomplete")
        if tuple(int(part) for part in python_version.split(".")) < (3, 9, 0):
            raise ValueError("guest Python must satisfy the package's Python >=3.9 requirement")
        return GuestRuntimeEvidence(source_revision, package_digest, archive_digest, runtime,
            python_version, pydantic_version, yaml_version, tuple(actions))


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
