"""Explicit provider and remote-stage adapters for the F09 lifecycle."""
from __future__ import annotations

import hashlib
import io
import json
import re
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
    receiver_command_evidence: tuple[Any, ...] = ()


@dataclass(frozen=True)
class RemoteDiagnosticsProcess:
    deployment_id: str
    role: str
    invocation_id: str
    pid: int
    process_group: int
    boot_id: str
    start_ticks: int
    remote_root: str
    context_path: str
    outcome_path: str
    context: Mapping[str, Any]
    actions: tuple[Any, ...]


@dataclass(frozen=True)
class RemoteDiagnosticsClosure:
    deployment_id: str
    role: str
    state: str
    closed_at: datetime | None
    outcome: Mapping[str, Any] | None
    actions: tuple[Any, ...]
    failures: tuple[str, ...] = ()


@dataclass(frozen=True)
class RemoteListenerClosure:
    deployment_id: str
    role: str
    closed: bool
    closed_at: datetime | None
    observations: Mapping[str, Mapping[str, str]]
    actions: tuple[Any, ...]
    failures: tuple[str, ...] = ()


@dataclass(frozen=True)
class RemoteFileInventory:
    deployment_id: str
    role: str
    workspace: str
    files: tuple[Mapping[str, Any], ...]
    actions: tuple[Any, ...]
    failures: tuple[str, ...] = ()


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
    cancellation_status = "not_needed"
    stage = "request staging"
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
            stage = "guest execution request"
            remaining = deadline.remaining(clock)
            if remaining <= 0:
                raise TimeoutError("F03 execution is outside the active cap")
            stage = "guest command construction"
            python = str(PurePosixPath(runtime.remote_root) / "venv" / "bin" / "python")
            module_root = str(PurePosixPath(runtime.remote_root) / "source")
            pid_path = root / f"{invocation}.pid"
            # Isolate the guest process group and publish its PID before it can
            # outlive an interrupted SSH transport. A reachable controller can
            # then cancel the exact invocation and verify the group exited.
            guest_argv = (f"PYTHONPATH={shlex.quote(module_root)} {shlex.quote(python)} -m "
                          f"cloud_network_benchmark.lifecycle_guest --request {shlex.quote(str(request_path))} "
                          f"--outcome {shlex.quote(str(outcome_path))}")
            command = ("set -u; pidfile=" + shlex.quote(str(pid_path)) + "; "
                       "setsid sh -c " + shlex.quote(guest_argv) + " & child=$!; "
                       "printf '%s\\n' \"$child\" > \"$pidfile\"; "
                       "wait \"$child\"; status=$?; rm -f -- \"$pidfile\"; exit \"$status\"")
            execute = _request(f"f09-guest-run-{invocation}", vm,
                ["sh", "-c", command], remaining)
            stage = "guest command execution"
            execute_result = _execute(runner, execute)
            stage = "guest command evidence"
            actions.append(_action(execute.action_id, execute.action_type, vm, execute, execute_result))
            if execute_result.outcome != CommandOutcome.SUCCEEDED:
                if execute_result.outcome in {CommandOutcome.INTERRUPTED, CommandOutcome.TIMED_OUT}:
                    cancel_remaining = deadline.remaining(clock, cleanup=True)
                    if cancel_remaining > 0:
                        cancel_command = (
                            "set -u; pidfile=" + shlex.quote(str(pid_path)) + "; "
                            "test -s \"$pidfile\" || { echo invocation_pid_unavailable; exit 2; }; "
                            "pid=$(cat \"$pidfile\"); case \"$pid\" in ''|*[!0-9]*) exit 2;; esac; "
                            "kill -TERM -- -\"$pid\" 2>/dev/null || true; i=0; "
                            "while kill -0 -- -\"$pid\" 2>/dev/null; do "
                            "i=$((i+1)); if [ \"$i\" -ge 10 ]; then echo process_group_still_running; exit 1; fi; "
                            "sleep 1; done; rm -f -- \"$pidfile\"; echo process_group_stopped")
                        cancel = _request(f"f09-guest-cancel-{invocation}", vm,
                            ["sh", "-c", cancel_command],
                            min(connection.timeouts.command_seconds, cancel_remaining))
                        cancel_result = _execute(runner, cancel)
                        actions.append(_action(cancel.action_id, cancel.action_type, vm, cancel, cancel_result))
                        cancellation_status = ("verified_stopped" if cancel_result.outcome == CommandOutcome.SUCCEEDED
                                               and "process_group_stopped" in cancel_result.stdout
                                               else "unverified")
                    else:
                        cancellation_status = "not_attempted_cleanup_allowance_exhausted"
                raise RuntimeError(f"VM-A F03 lifecycle guest failed; cancellation={cancellation_status}")
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
        return RemoteGuestExecution(None, tuple(actions),
            f"{type(exc).__name__}: F03 {stage} failed ({exc}); cancellation={cancellation_status}")


def start_remote_diagnostics(connection: Any, context: Mapping[str, Any], *, runner: Any,
        remote_root: str, deadline: Any,
        clock: Callable[[], float] = __import__("time").monotonic) -> RemoteDiagnosticsProcess:
    """Start the existing standalone F04 collector on one selected VM.

    The collector runs in a unique session/process group. Its boot ID, PID,
    process group and kernel start ticks are captured so closure can signal only
    this invocation, even if a stale PID is reused.
    """
    import uuid
    from .access import _action, _execute, _request
    from .contracts.common import CommandOutcome
    from .diagnostics_guest import validate_context

    context = dict(context)
    # Validate all standalone collector fields on a local temporary path; the
    # configured output_root belongs to the guest filesystem and must not be
    # followed through host-specific symlinks during controller validation.
    from tempfile import TemporaryDirectory
    with TemporaryDirectory(prefix="cnb-f09-context-", dir=Path(__file__).resolve().parents[2]) as temp_root:
        validate_context({**context, "output_root": str(Path(temp_root) / "output")})
    role = context["vm_role"]
    if context["deployment_id"] != connection.deployment_id or role not in {"vm_a", "vm_b"}:
        raise ValueError("diagnostics context must match a selected deployment role")
    root = PurePosixPath(remote_root)
    if not remote_root.startswith("/") or root.as_posix() != remote_root or ".." in root.parts:
        raise ValueError("diagnostics control root must be normalized absolute POSIX")
    invocation = uuid.uuid4().hex
    vm = connection.vm_a if role == "vm_a" else connection.vm_b
    actions = []
    context_path = str(root / f"{invocation}.context.json")
    outcome_path = str(root / f"{invocation}.outcome.json")
    process_path = str(root / f"{invocation}.process.json")
    try:
        with TemporaryDirectory(prefix="cnb-f09-diagnostics-") as temp_dir:
            temp = Path(temp_dir)
            helper = Path(__file__).with_name("diagnostics_guest.py")
            context_file = temp / "context.json"
            context_file.write_text(json.dumps(context, sort_keys=True, separators=(",", ":")) + "\n",
                                    encoding="utf-8")
            remaining = deadline.remaining(clock)
            if remaining <= 0:
                raise TimeoutError("diagnostics staging is outside the active cap")
            mkdir = _request(f"f09-diagnostics-mkdir-{invocation}", vm,
                ["sh", "-c", f"mkdir -p {shlex.quote(str(root))}"],
                min(connection.timeouts.command_seconds, remaining))
            mkdir_result = _execute(runner, mkdir)
            actions.append(_action(mkdir.action_id, mkdir.action_type, vm, mkdir, mkdir_result))
            if mkdir_result.outcome != CommandOutcome.SUCCEEDED:
                raise RuntimeError("diagnostics control directory creation failed")
            for suffix, local_path, remote_path in (("helper", helper, str(root / "diagnostics_guest.py")),
                                                     ("context", context_file, context_path)):
                remaining = deadline.remaining(clock)
                if remaining <= 0:
                    raise TimeoutError("diagnostics input transfer is outside the active cap")
                upload = _request(f"f09-diagnostics-{suffix}-{invocation}", vm,
                    [str(local_path), f"{vm.user}@{vm.host}:{remote_path}"],
                    min(connection.timeouts.transfer_seconds, remaining), action_type="scp")
                upload_result = _execute(runner, upload)
                actions.append(_action(upload.action_id, upload.action_type, vm, upload, upload_result))
                if upload_result.outcome != CommandOutcome.SUCCEEDED:
                    raise RuntimeError(f"diagnostics {suffix} transfer failed")
            remaining = deadline.remaining(clock)
            if remaining <= 0:
                raise TimeoutError("diagnostics collector start is outside the active cap")
            script = (
                "set -eu; test ! -e " + shlex.quote(process_path) + "; "
                "setsid python3 " + shlex.quote(str(root / "diagnostics_guest.py")) + " --context "
                + shlex.quote(context_path) + " > " + shlex.quote(outcome_path) + " 2>&1 </dev/null & pid=$!; "
                "python3 -c " + shlex.quote(
                    "import json,os,pathlib,sys; p=int(sys.argv[1]); "
                    "s=pathlib.Path(f'/proc/{p}/stat').read_text(); f=s[s.rfind(')')+2:].split(); "
                    "r={'pid':p,'process_group':os.getpgid(p),'boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'start_ticks':int(f[19])}; "
                    "pathlib.Path(sys.argv[2]).write_text(json.dumps(r,sort_keys=True)+'\\n'); print(json.dumps(r,sort_keys=True)"
                    ) + " \"$pid\" " + shlex.quote(process_path))
            start = _request(f"f09-diagnostics-start-{invocation}", vm,
                ["sh", "-c", script], min(connection.timeouts.command_seconds, remaining))
            start_result = _execute(runner, start)
            actions.append(_action(start.action_id, start.action_type, vm, start, start_result))
            if start_result.outcome != CommandOutcome.SUCCEEDED:
                raise RuntimeError("diagnostics collector start failed")
            try:
                process = json.loads(start_result.stdout.strip().splitlines()[-1])
                pid, pgid, boot_id, start_ticks = (process[key] for key in
                    ("pid", "process_group", "boot_id", "start_ticks"))
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                raise ValueError("diagnostics process identity evidence is malformed") from exc
            if (any(isinstance(value, bool) or not isinstance(value, int) or value <= 0
                    for value in (pid, pgid, start_ticks)) or pid != pgid
                    or not isinstance(boot_id, str) or not re.fullmatch(r"[a-f0-9-]{36}", boot_id)):
                raise ValueError("diagnostics process identity evidence is incomplete")
            return RemoteDiagnosticsProcess(connection.deployment_id, role, invocation,
                pid, pgid, boot_id, start_ticks, str(root), context_path, outcome_path,
                context, tuple(actions))
    except BaseException:
        raise


def stop_remote_diagnostics(connection: Any, process: RemoteDiagnosticsProcess, *, runner: Any,
        deadline: Any, clock: Callable[[], float] = __import__("time").monotonic) -> RemoteDiagnosticsClosure:
    """Stop one captured F04 process group, observe exit, then read its flushed outcome."""
    from .access import _action, _execute, _request
    from .contracts.common import CommandOutcome

    if process.deployment_id != connection.deployment_id:
        raise ValueError("diagnostics process belongs to another deployment")
    vm = connection.vm_a if process.role == "vm_a" else connection.vm_b
    actions = []
    failures = []
    closed_at = None
    outcome = None
    remaining = deadline.remaining(clock, cleanup=True)
    if remaining <= 0:
        return RemoteDiagnosticsClosure(process.deployment_id, process.role, "unverified", None,
            None, process.actions, ("cleanup allowance exhausted before diagnostics stop",))
    source = "exec(" + repr("\n".join((
        "import datetime,json,os,pathlib,signal,sys,time",
        "path=pathlib.Path(sys.argv[1]); expected=(int(sys.argv[2]),int(sys.argv[3]),sys.argv[4],int(sys.argv[5]))",
        "r=json.loads(path.read_text())",
        "(r['pid'],r['process_group'],r['boot_id'],r['start_ticks'])==expected or sys.exit(3)",
        "pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()==expected[2] or sys.exit(4)",
        "stat=pathlib.Path(f'/proc/{expected[0]}/stat')",
        "(not stat.exists() or int(stat.read_text()[stat.read_text().rfind(')')+2:].split()[19])==expected[3]) or sys.exit(5)",
        "try:",
        " os.killpg(expected[1],0)",
        "except ProcessLookupError:",
        " exists=False",
        "else:",
        " exists=True",
        "if exists:",
        " os.killpg(expected[1],signal.SIGTERM)",
        " deadline=time.monotonic()+10",
        " while True:",
        "  try: os.killpg(expected[1],0)",
        "  except ProcessLookupError: break",
        "  if time.monotonic()>=deadline: sys.exit(6)",
        "  time.sleep(0.1)",
        "print(json.dumps({'state':'stopped','closed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00','Z')}))",
    ))) + ")"
    command = "python3 -c " + shlex.quote(source) + " " + " ".join(shlex.quote(str(value)) for value in
        (str(PurePosixPath(process.remote_root) / f"{process.invocation_id}.process.json"), process.pid,
         process.process_group, process.boot_id, process.start_ticks))
    stop = _request(f"f09-diagnostics-stop-{process.invocation_id}", vm,
        ["sh", "-c", command], min(connection.timeouts.command_seconds, remaining))
    stop_result = _execute(runner, stop)
    actions.append(_action(stop.action_id, stop.action_type, vm, stop, stop_result))
    if stop_result.outcome == CommandOutcome.SUCCEEDED:
        try:
            report = json.loads(stop_result.stdout.strip().splitlines()[-1])
            if report.get("state") != "stopped":
                raise ValueError("diagnostics stop result did not confirm stop")
            closed_at = datetime.fromisoformat(report["closed_at_utc"].replace("Z", "+00:00"))
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            failures.append(f"diagnostics stop evidence is malformed ({type(exc).__name__})")
    else:
        failures.append("diagnostics process group was not observed stopped")
    remaining = deadline.remaining(clock, cleanup=True)
    if remaining > 0 and closed_at is not None:
        read = _request(f"f09-diagnostics-outcome-{process.invocation_id}", vm,
            ["cat", "--", process.outcome_path], min(connection.timeouts.command_seconds, remaining))
        read_result = _execute(runner, read)
        actions.append(_action(read.action_id, read.action_type, vm, read, read_result))
        if read_result.outcome == CommandOutcome.SUCCEEDED:
            try:
                outcome = json.loads(read_result.stdout.strip().splitlines()[-1])
                if (outcome.get("deployment_id") != process.deployment_id
                        or outcome.get("vm_role") != process.role or outcome.get("state") != "interrupted"
                        or set(outcome.get("closed_paths", ())) != {
                            str(Path(process.context["output_root"]) / f"metadata/{process.role}.json"),
                            str(Path(process.context["output_root"]) / f"diagnostics/{process.role}.jsonl"),
                            str(Path(process.context["output_root"]) / f"diagnostics/{process.role}-clock.jsonl")}):
                    raise ValueError("diagnostics outcome does not prove the selected collector closed its files")
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                failures.append(f"diagnostics outcome is unavailable or invalid ({type(exc).__name__})")
        else:
            failures.append("diagnostics outcome retrieval failed")
    elif closed_at is None:
        failures.append("diagnostics outcome was not read because process stop is unverified")
    state = "closed" if closed_at is not None and outcome is not None and not failures else "failed"
    return RemoteDiagnosticsClosure(process.deployment_id, process.role, state, closed_at,
        outcome, (*process.actions, *actions), tuple(failures))


def stop_vm_b_listeners(connection: Any, *, runner: Any, deadline: Any,
        clock: Callable[[], float] = __import__("time").monotonic) -> RemoteListenerClosure:
    """Stop only this deployment's two F02 B-side systemd listeners and verify exit.

    systemd owns these listeners; stopping the selected unit waits for its
    process and append-log descriptors to close. No broad process-name kill is
    used. Guest terminal timestamps and MainPID/ActiveState must agree.
    """
    from .access import _action, _execute, _request
    from .contracts.common import CommandOutcome

    vm = connection.vm_b
    actions = []
    observations: dict[str, dict[str, str]] = {}
    failures = []
    for tool in ("sockperf", "iperf3"):
        unit = f"cnb-{connection.deployment_id}-{tool}"
        remaining = deadline.remaining(clock, cleanup=True)
        if remaining <= 0:
            failures.append(f"{tool} listener stop exceeded the cleanup allowance")
            continue
        stop = _request(f"f09-listener-stop-{connection.deployment_id}-{tool}", vm,
            ["sudo", "systemctl", "stop", unit],
            min(connection.timeouts.command_seconds, remaining))
        result = _execute(runner, stop)
        actions.append(_action(stop.action_id, stop.action_type, vm, stop, result))
        if result.outcome != CommandOutcome.SUCCEEDED:
            failures.append(f"{tool} listener stop command failed")
        remaining = deadline.remaining(clock, cleanup=True)
        if remaining <= 0:
            failures.append(f"{tool} listener exit observation exceeded the cleanup allowance")
            continue
        inspect = _request(f"f09-listener-exit-{connection.deployment_id}-{tool}", vm,
            ["systemctl", "show", unit,
             "--property=LoadState,ActiveState,MainPID,InactiveEnterTimestamp"],
            min(connection.timeouts.server_seconds, remaining))
        inspected = _execute(runner, inspect)
        actions.append(_action(inspect.action_id, inspect.action_type, vm, inspect, inspected))
        if inspected.outcome != CommandOutcome.SUCCEEDED:
            failures.append(f"{tool} listener terminal state could not be observed")
            continue
        fields = dict(line.split("=", 1) for line in inspected.stdout.splitlines() if "=" in line)
        observations[tool] = fields
        if (fields.get("LoadState") != "loaded" or fields.get("ActiveState") != "inactive"
                or fields.get("MainPID") != "0"):
            failures.append(f"{tool} listener is not observed inactive with MainPID=0")
            continue
        raw_time = fields.get("InactiveEnterTimestamp", "")
        try:
            observed = datetime.strptime(raw_time, "%a %Y-%m-%d %H:%M:%S %Z")
            if raw_time.endswith(" UTC"):
                observed = observed.replace(tzinfo=timezone.utc)
            if observed.tzinfo is None or observed.utcoffset() is None:
                raise ValueError("systemd terminal timestamp is not timezone-aware")
            observations[tool]["closed_at_utc"] = observed.astimezone(timezone.utc).isoformat()
        except ValueError:
            failures.append(f"{tool} listener terminal timestamp is unavailable or malformed")
    times = [datetime.fromisoformat(row["closed_at_utc"]) for row in observations.values()
             if "closed_at_utc" in row]
    closed_at = max(times) if len(times) == 2 and not failures else None
    return RemoteListenerClosure(connection.deployment_id, "vm_b", closed_at is not None,
        closed_at, observations, tuple(actions), tuple(failures))


def inventory_remote_workspace(connection: Any, *, role: str, workspace: str, runner: Any,
        deadline: Any, clock: Callable[[], float] = __import__("time").monotonic,
        maximum_files: int = 10000) -> RemoteFileInventory:
    """Read a bounded, symlink-free inventory and hashes from one closed VM workspace."""
    from .access import _action, _execute, _request
    from .contracts.common import CommandOutcome

    if role not in {"vm_a", "vm_b"}:
        raise ValueError("inventory role must identify a selected VM")
    root = PurePosixPath(workspace)
    if not workspace.startswith("/") or root.as_posix() != workspace or ".." in root.parts:
        raise ValueError("remote workspace must be normalized absolute POSIX")
    if isinstance(maximum_files, bool) or not isinstance(maximum_files, int) or maximum_files < 1:
        raise ValueError("inventory file bound must be a positive integer")
    vm = connection.vm_a if role == "vm_a" else connection.vm_b
    remaining = deadline.remaining(clock, cleanup=True)
    if remaining <= 0:
        return RemoteFileInventory(connection.deployment_id, role, workspace, (), (),
            ("cleanup allowance exhausted before remote inventory",))
    source = "\n".join((
        "import hashlib,json,pathlib,sys",
        "root=pathlib.Path(sys.argv[1]); bound=int(sys.argv[2])",
        "root.is_dir() and not root.is_symlink() or sys.exit(2)",
        "rows=[]",
        "for path in sorted(root.rglob('*')):",
        " if path.is_symlink(): sys.exit(3)",
        " if path.is_file():",
        "  rel=path.relative_to(root).as_posix()",
        "  rel and not rel.startswith('/') and '..' not in pathlib.PurePosixPath(rel).parts or sys.exit(4)",
        "  h=hashlib.sha256(); size=0",
        "  with path.open('rb') as stream:",
        "   while True:",
        "    chunk=stream.read(1048576)",
        "    if not chunk: break",
        "    h.update(chunk); size+=len(chunk)",
        "  rows.append({'path':rel,'size_bytes':size,'sha256':h.hexdigest()})",
        "  len(rows)<=bound or sys.exit(5)",
        "print(json.dumps({'files':rows},sort_keys=True,separators=(',',':')))",
    ))
    command = "python3 -c " + shlex.quote("exec(" + repr(source) + ")") + " " + shlex.quote(workspace) + " " + str(maximum_files)
    request = _request(f"f09-inventory-{connection.deployment_id}-{role}", vm,
        ["sh", "-c", command], min(connection.timeouts.transfer_seconds, remaining))
    result = _execute(runner, request)
    action = _action(request.action_id, request.action_type, vm, request, result)
    if result.outcome != CommandOutcome.SUCCEEDED:
        return RemoteFileInventory(connection.deployment_id, role, workspace, (), (action,),
            ("remote actual-file inventory failed or exceeded its bound",))
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        rows = payload["files"]
        if not isinstance(rows, list) or len(rows) > maximum_files:
            raise ValueError("remote file inventory is not a bounded array")
        checked = []
        for row in rows:
            if (not isinstance(row, dict) or set(row) != {"path", "size_bytes", "sha256"}
                    or not isinstance(row["path"], str) or not row["path"]
                    or PurePosixPath(row["path"]).is_absolute()
                    or PurePosixPath(row["path"]).as_posix() != row["path"]
                    or ".." in PurePosixPath(row["path"]).parts
                    or isinstance(row["size_bytes"], bool) or not isinstance(row["size_bytes"], int)
                    or row["size_bytes"] < 0 or not re.fullmatch(r"[a-f0-9]{64}", str(row["sha256"]))):
                raise ValueError("remote file inventory row is invalid")
            checked.append(row)
        if len({row["path"] for row in checked}) != len(checked):
            raise ValueError("remote file inventory paths must be unique")
        return RemoteFileInventory(connection.deployment_id, role, workspace,
            tuple(checked), (action,))
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        return RemoteFileInventory(connection.deployment_id, role, workspace, (), (action,),
            (f"remote file inventory evidence is malformed ({type(exc).__name__})",))


def aws_hooks(driver: AwsProviderDriver, deployment: ProviderDeploymentInput,
              expiry_request: ExpiryRequest, *, stages: RemoteStages,
              reservation: Any = None, claim_data: Any = None, on_protected=None):
    from .orchestration import ProviderHooks
    state: dict[str, Any] = {"deployment": _deployment_with(deployment, reservation=reservation)}
    def protect():
        evidence = driver.register_expiry(state["deployment"], expiry_request)
        state["deployment"] = _deployment_with(state["deployment"], expiry=evidence)
        if on_protected is not None: on_protected("aws", evidence)
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
              reservation: Any = None, claim_data: Any = None, on_protected=None):
    from .orchestration import ProviderHooks
    state: dict[str, Any] = {"deployment": _deployment_with(deployment, reservation=reservation)}
    def protect():
        evidence = driver.register_expiry(state["deployment"], expiry_request)
        state["deployment"] = _deployment_with(state["deployment"], expiry=evidence)
        if on_protected is not None: on_protected("gcp", evidence)
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
                utc_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc), on_protected=None):
    from . import azure_provider
    from .orchestration import ProviderHooks
    state: dict[str, Any] = {"deployment": _deployment_with(deployment, reservation=reservation)}
    def protect():
        evidence = azure_provider.register_expiry(state["deployment"], expiry_request, options, runner,
                                                  clock=clock, utc_now=utc_now)
        state["deployment"] = _deployment_with(state["deployment"], expiry=evidence)
        if on_protected is not None: on_protected("azure", evidence)
        return evidence
    def create():
        state["result"] = azure_provider.provision(state["deployment"], options, runner,
                                                    clock=clock, utc_now=utc_now)
        state["deployment"] = _deployment_with(state["deployment"], expiry=state["result"].expiry)
        return state["result"]
    def stage(name: str):
        return getattr(stages, name)(state["deployment"], _last_result(state))
    def cleanup():
        # Azure's native destroy removes its provider journal only after every
        # absence check succeeds, so snapshot the exact source-bound identities
        # before invoking it for the F11 cleanup receipt.
        journal = Path(options.attempt_root) / "recovery.json"
        saved = json.loads(journal.read_text(encoding="utf-8")) if journal.is_file() and not journal.is_symlink() else {}
        ids = [item for item in saved.get("vm_ids", ()) if isinstance(item, str) and item.strip()]
        expiry = saved.get("expiry", {})
        if isinstance(expiry, Mapping):
            ids.extend(value for key, value in expiry.items()
                if key.endswith("_id") and isinstance(value, str) and value.strip())
            metadata = expiry.get("metadata", {})
            if isinstance(metadata, Mapping):
                account = metadata.get("automation_account_id")
                if isinstance(account, str) and account.strip():
                    ids.append(account)
                    for collection, key in (("runbooks", "runbook"), ("schedules", "schedule"),
                            ("jobSchedules", "job_schedule_id")):
                        child = metadata.get(key)
                        if isinstance(child, str) and child.strip():
                            ids.append(f"{account}/{collection}/{child}")
        state["cleanup_resource_ids"] = list(dict.fromkeys(ids))
        return azure_provider.destroy(state["deployment"], options, runner, clock=clock, utc_now=utc_now)
    return ProviderHooks(
        provider="azure", protect=protect,
        claim=lambda: stages.claim(state["deployment"], claim_data),
        create=create, prepare=lambda: stage("prepare"), stabilize=lambda: stage("stabilize"),
        measure=lambda: stage("measure"), close=lambda: stage("close"),
        retrieve=lambda: stage("retrieve"), cleanup=cleanup,
        cleanup_resource_ids=lambda: list(state.get("cleanup_resource_ids", ())),
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
