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


@dataclass(frozen=True)
class RemoteGuestExecution:
    outcome: Any | None
    actions: tuple[Any, ...]
    failure: str | None = None


def stage_guest_runtime(connection: Any, *, source_root: Path, source_revision: str,
                        remote_root: str, runner: Any, deadline: Any,
                        clock: Callable[[], float] = __import__("time").monotonic) -> GuestRuntimeEvidence:
    """Stage the exact package source on A and verify its private venv.

    The guest venv isolates the staged application and records the prepared
    Ubuntu Python dependency versions. Bootstrap installs the venv tooling and
    the existing distro Pydantic/PyYAML packages; this step never downloads or
    resolves dependencies and fails before measurement if they are absent.
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
                if path.is_file() and "__pycache__" not in path.relative_to(package_root).parts and path.suffix != ".pyc":
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
        mkdir_request = _request(f"f09-runtime-mkdir-{connection.deployment_id}", vm,
            ["sh", "-c", f"mkdir -p {shlex.quote(str(root))}"],
            min(connection.timeouts.command_seconds, transfer_timeout))
        mkdir_result = _execute(runner, mkdir_request)
        actions.append(_action(mkdir_request.action_id, mkdir_request.action_type, vm, mkdir_request, mkdir_result))
        if mkdir_result.outcome != CommandOutcome.SUCCEEDED:
            raise RuntimeError("guest runtime destination creation failed")
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
            f"{shlex.quote(runtime + '/venv/bin/python')} -m pip install 'pydantic>=2.0,<3' 'PyYAML>=6.0,<7'",
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
        try:
            pydantic_major = int(pydantic_version.split(".", 1)[0])
        except ValueError as exc:
            raise ValueError("guest Pydantic version is malformed") from exc
        if pydantic_major < 2:
            raise ValueError("guest Pydantic must satisfy the package's Pydantic >=2 requirement")
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


def prepare_remote_pair(connection: Any, *, bootstrap_config: Any, readiness_config: Any,
                         runner: Any, deadline: Any, source_root: Path,
                         source_revision: str, remote_root: str,
                         clock: Callable[[], float] = __import__("time").monotonic):
    """Run the existing paired bootstrap/readiness and A-local source preparation.

    This is a production composition helper, not a replacement for F03/F04.
    It returns failure evidence at the first incomplete boundary and never
    treats a successful transport command as a ready pair.
    """
    from . import access
    from .orchestration import ProviderStageOutcome

    prepared = access.prepare(connection, bootstrap_config, runner, deadline=deadline, clock=clock)
    if prepared.succeeded is not True:
        return ProviderStageOutcome("prepare", False, {"bootstrap": prepared})
    readiness = access.check_readiness(connection, readiness_config, runner,
        prepared=prepared, deadline=deadline, clock=clock)
    if readiness.ready is not True:
        return ProviderStageOutcome("prepare", False,
            {"bootstrap": prepared, "readiness": readiness})
    guest = stage_guest_runtime(connection, source_root=source_root,
        source_revision=source_revision, remote_root=remote_root, runner=runner,
        deadline=deadline, clock=clock)
    return ProviderStageOutcome("prepare", True,
        {"bootstrap": prepared, "readiness": readiness, "guest_runtime": guest})


def run_remote_f03(connection: Any, request: Any, *, runner: Any, runtime: GuestRuntimeEvidence,
                   deadline: Any, clock: Callable[[], float] = __import__("time").monotonic) -> RemoteGuestExecution:
    """Run the existing F03 entry on VM A and strictly retrieve its outcome.

    Request and response files use unique per-invocation names under the
    staged deployment runtime. Every SSH/SCP operation uses the existing
    trusted access command builder; transport success is not measurement
    success until the returned ``GuestOutcome`` validates and matches identity.
    """
    import uuid
    from .access import _action, _execute, _request
    from .contracts.common import CommandOutcome
    from .lifecycle_guest import GuestOutcome, GuestRequest

    request = GuestRequest.model_validate(request.model_dump(mode="python"))
    if (request.identity.deployment_id != connection.deployment_id
            or request.identity.run_id != connection.run_id
            or request.identity.provider != connection.provider
            or request.package_sha256 != runtime.package_sha256
            or request.source_revision != runtime.source_revision
            or request.package_root != Path(runtime.remote_root) / "source" / "cloud_network_benchmark"):
        raise ValueError("remote F03 request does not match the staged deployment package")
    invocation = uuid.uuid4().hex
    root = PurePosixPath(runtime.remote_root) / "control"
    request_path, outcome_path = root / f"{invocation}.request.json", root / f"{invocation}.outcome.json"
    local_outcome = None
    actions = []
    vm = connection.vm_a
    try:
        import tempfile
        with tempfile.TemporaryDirectory(prefix="cnb-f09-guest-") as temp_dir:
            local_request = Path(temp_dir) / "request.json"
            local_outcome = Path(temp_dir) / "outcome.json"
            local_request.write_text(request.model_dump_json(indent=2), encoding="utf-8")
            remaining = deadline.remaining(clock)
            if remaining <= 0:
                raise TimeoutError("F03 request staging is outside the active cap")
            mkdir = _request(f"f09-guest-control-mkdir-{invocation}", vm,
                ["sh", "-c", f"mkdir -p {shlex.quote(str(root))}"],
                min(connection.timeouts.command_seconds, remaining))
            mkdir_result = _execute(runner, mkdir)
            actions.append(_action(mkdir.action_id, mkdir.action_type, vm, mkdir, mkdir_result))
            if mkdir_result.outcome != CommandOutcome.SUCCEEDED:
                raise RuntimeError("F03 control directory creation failed")
            remaining = deadline.remaining(clock)
            if remaining <= 0:
                raise TimeoutError("F03 request transfer is outside the active cap")
            upload = _request(f"f09-guest-request-{invocation}", vm,
                [str(local_request), f"{vm.user}@{vm.host}:{request_path}"],
                min(connection.timeouts.transfer_seconds, remaining), action_type="scp")
            upload_result = _execute(runner, upload)
            actions.append(_action(upload.action_id, upload.action_type, vm, upload, upload_result))
            if upload_result.outcome != CommandOutcome.SUCCEEDED:
                raise RuntimeError("F03 request transfer failed")
            remaining = deadline.remaining(clock)
            if remaining <= 0:
                raise TimeoutError("F03 execution is outside the active cap")
            python = str(PurePosixPath(runtime.remote_root) / "venv" / "bin" / "python")
            module_root = str(PurePosixPath(runtime.remote_root) / "source")
            command = (f"PYTHONPATH={shlex.quote(module_root)} {shlex.quote(python)} -m "
                       f"cloud_network_benchmark.lifecycle_guest --request {shlex.quote(str(request_path))} "
                       f"--outcome {shlex.quote(str(outcome_path))}")
            execute = _request(f"f09-guest-run-{invocation}", vm,
                ["sh", "-c", command], remaining)
            execute_result = _execute(runner, execute)
            actions.append(_action(execute.action_id, execute.action_type, vm, execute, execute_result))
            if execute_result.outcome != CommandOutcome.SUCCEEDED:
                raise RuntimeError("VM-A F03 lifecycle guest failed")
            remaining = deadline.remaining(clock)
            if remaining <= 0:
                raise TimeoutError("F03 outcome retrieval is outside the active cap")
            download = _request(f"f09-guest-outcome-{invocation}", vm,
                [f"{vm.user}@{vm.host}:{outcome_path}", str(local_outcome)],
                min(connection.timeouts.transfer_seconds, remaining), action_type="scp")
            download_result = _execute(runner, download)
            actions.append(_action(download.action_id, download.action_type, vm, download, download_result))
            if download_result.outcome != CommandOutcome.SUCCEEDED or not local_outcome.is_file():
                raise RuntimeError("VM-A F03 outcome retrieval failed")
            outcome = GuestOutcome.model_validate_json(local_outcome.read_text(encoding="utf-8"))
            if (outcome.identity != request.identity or outcome.source_revision != runtime.source_revision
                    or outcome.package_sha256 != runtime.package_sha256):
                raise ValueError("VM-A F03 outcome identity or source digest changed")
            return RemoteGuestExecution(outcome, tuple(actions))
    except BaseException as exc:
        return RemoteGuestExecution(None, tuple(actions), f"{type(exc).__name__}: F03 remote handoff failed")


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
