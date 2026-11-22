"""Source-bound provider remote-stage composition for F09."""
from __future__ import annotations

import subprocess
import shlex
import hashlib
import json
import time
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping


@dataclass
class _Attempt:
    deployment: Any
    connection: Any
    deadline: Any
    child: Any
    observation: Any
    result_root: Path
    source_revision: str
    prepared: Any = None
    stable: Any = None
    diagnostics: dict[str, Any] | None = None
    guest: Any = None
    measurement_ended_at: datetime | None = None
    closures: dict[str, Any] | None = None
    listener_closure: Any = None
    inventories: dict[str, Any] | None = None
    manifest: Any = None
    retrieval: Any = None
    f03_process_record: dict[str, Any] | None = None
    f03_process_absent_at: datetime | None = None


def _fetch_bounded_guest_archive_part(*, runner: Any, connection: Any, role: str,
        source_path: str, destination: Path, staging_directory: Path, size_bytes: int,
        maximum_staging_bytes: int, minimum_free_bytes: int,
        controller_quota_free_bytes: int, deadline: Any, clock: Callable[[], float],
        action_id: str, actions: list[Any]) -> Path:
    """Receive one frozen guest part through the bounded SSH stdout path."""
    from pathlib import PurePosixPath
    from .access import _action, _request
    from .contracts.common import CommandOutcome
    from .retrieval import _bounded_stream_script

    source = PurePosixPath(source_path)
    if (not source_path.startswith("/") or source.as_posix() != source_path
            or ".." in source.parts):
        raise ValueError("guest archive part source must be normalized absolute POSIX")
    if (type(size_bytes) is not int or size_bytes <= 0
            or type(maximum_staging_bytes) is not int or maximum_staging_bytes <= 0
            or type(minimum_free_bytes) is not int or minimum_free_bytes < 0
            or type(controller_quota_free_bytes) is not int or controller_quota_free_bytes < 0):
        raise ValueError("guest archive receive bounds must be positive measured integers")
    if size_bytes > maximum_staging_bytes or size_bytes + minimum_free_bytes > controller_quota_free_bytes:
        raise OSError("one bounded guest part does not fit the configured staging quota")
    staging_directory, destination = Path(staging_directory), Path(destination)
    if (staging_directory.is_symlink() or not staging_directory.is_dir()
            or destination.parent.resolve() != staging_directory.resolve()
            or destination.exists() or destination.is_symlink()):
        raise ValueError("bounded guest part destination is not a new one-part staging file")
    remaining = deadline.remaining(clock, cleanup=True)
    if remaining <= 0:
        raise TimeoutError("guest archive part reached the original retrieval window")
    vm = connection.vm_a if role == "vm_a" else connection.vm_b if role == "vm_b" else None
    if vm is None:
        raise ValueError("guest archive part role must be vm_a or vm_b")
    request = _request(action_id, vm,
        ["sh", "-c", _bounded_stream_script(source_path, size_bytes)],
        min(connection.timeouts.transfer_seconds, remaining))
    request = request.model_copy(update={"argv": [request.argv[0], "-T", *request.argv[1:]]})
    from .contracts.execution import CommandResult
    started = datetime.now(timezone.utc)
    try:
        result = runner.run_bounded_copy(request, destination, size_bytes)
    except KeyboardInterrupt:
        finished = datetime.now(timezone.utc)
        result = CommandResult(action_id=action_id, outcome=CommandOutcome.INTERRUPTED,
            started_at=started, finished_at=finished,
            duration_seconds=max(0.0, (finished - started).total_seconds()),
            exit_code=None, stdout="", stderr="bounded guest archive receive interrupted",
            redactions=request.redactions)
    except Exception as exc:
        finished = datetime.now(timezone.utc)
        result = CommandResult(action_id=action_id, outcome=CommandOutcome.FAILED,
            started_at=started, finished_at=finished,
            duration_seconds=max(0.0, (finished - started).total_seconds()),
            exit_code=1, stdout="", stderr=f"bounded guest archive receiver raised {type(exc).__name__}",
            redactions=request.redactions)
    actions.append(_action(request.action_id, request.action_type, vm, request, result))
    if result.outcome != CommandOutcome.SUCCEEDED:
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"one bounded archive part retrieval failed for {role}")
    if destination.is_symlink() or not destination.is_file() or destination.stat().st_size != size_bytes:
        destination.unlink(missing_ok=True)
        raise ValueError("retrieved guest part differs from its frozen byte bound")
    return destination


def build_provider_remote_stages(*, provider: str, campaign: Any, parent: Any, children: dict[str, Any],
        runtime: dict[str, Any], ledger: Any, reservation: Any, runner: Any,
        clock: Any, repository_root: Path, recovery_path: Path,
        archive_sink: dict[str, Any] | None = None,
        observer_progress_callback: Callable[[Any, Any, Any, Any, Mapping[str, Any], dict[str, Any]], None] | None = None,
        sleep: Callable[[float], None] = time.sleep):
    """Bind real F02/F03/F04/retrieval adapters to one accepted reservation.

    The callbacks hold only evidence observed during this attempt. Missing
    observations produce typed failed outcomes, while orchestration still
    calls close, retrieve and provider cleanup after resource creation.
    """
    from .access import ConnectionOutput, RemoteDeadline, _execute, _request
    from .bootstrap import materialize_build_identity_artifacts
    from .contracts.common import CommandOutcome, Evidence, StringEvidence
    from .contracts.measurements import CommandEvidence
    from .diagnostics import _declarations
    from .diagnostics import build_collection_context
    from .lifecycle_adapters import (RemoteStages,
        guest_duration, inventory_remote_workspace, prepare_remote_pair,
        run_remote_f03, start_remote_diagnostics, stop_remote_diagnostics,
        stop_vm_b_listeners)
    from .lifecycle_bundle import (build_remote_closed_inventory, lifecycle_declarations,
        retrieve_closed_bundle_with_ledger)
    from .orchestration import ProviderStageOutcome, _remaining_f11_allowance, update_archive_recovery
    from .lifecycle_guest import GuestRequest
    from .post_measurement_barrier import PostMeasurementBarrier

    selected = {str(getattr(value, "value", value)) for value in campaign.selected_providers}
    if provider not in children or set(children) != selected:
        raise ValueError("production remote stages require the complete selected provider child set")
    child = children[provider]
    observation = child.resolved_observation
    if (str(getattr(observation.provider, "value", observation.provider)) != provider
            or observation.mode != campaign.mode or observation.config_id != child.config_id
            or observation.experiment_id != child.experiment_id):
        raise ValueError("initialized provider observation identity does not match the child")
    barrier = None
    barrier_participant = None
    barrier_spec = runtime.get("post_measurement_barrier")
    if barrier_spec is None and archive_sink is not None:
        participants = []
        for selected_provider, selected_child in sorted(children.items()):
            entry = getattr(getattr(selected_child.resolved_observation, "schedule_entry", None), "value", None)
            participants.append({"entry_id": getattr(entry, "entry_id", None) or selected_child.run_id,
                "provider": selected_provider, "run_id": selected_child.run_id})
        barrier_id = "archive-post-close-" + hashlib.sha256(json.dumps(
            participants, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
        barrier_spec = {"schema_version": 1, "barrier_id": barrier_id,
            "barrier_root": str(Path(archive_sink["archive_root"]) / ".post-measurement-barriers"),
            "participants": participants}
    if barrier_spec is not None:
        barrier = PostMeasurementBarrier(barrier_spec)
        schedule_entry = getattr(observation, "schedule_entry", None)
        schedule_entry = getattr(schedule_entry, "value", None)
        entry_id = getattr(schedule_entry, "entry_id", None) or child.run_id
        matches = [row for row in barrier.spec["participants"]
            if row["entry_id"] == entry_id and row["provider"] == provider
            and row["run_id"] == child.run_id]
        if len(matches) != 1:
            raise ValueError("prepared post-measurement barrier does not contain this exact child")
        barrier_participant = matches[0]
    measurement_start_barrier = None
    measurement_start_participant = None
    start_spec = runtime.get("measurement_start_barrier")
    if start_spec is not None:
        measurement_start_barrier = PostMeasurementBarrier(start_spec)
        if len(measurement_start_barrier.spec["participants"]) != 6:
            raise ValueError("measurement-start barrier must bind the exact six paired participants")
        schedule_entry = getattr(observation, "schedule_entry", None)
        schedule_entry = getattr(schedule_entry, "value", None)
        entry_id = getattr(schedule_entry, "entry_id", None) or child.run_id
        matches = [row for row in measurement_start_barrier.spec["participants"]
            if row["entry_id"] == entry_id and row["provider"] == provider
            and row["run_id"] == child.run_id]
        if len(matches) != 1:
            raise ValueError("prepared measurement-start barrier does not contain this exact child")
        if not measurement_start_barrier.spec.get("start_utc"):
            raise ValueError("measurement-start barrier requires one frozen UTC start")
        measurement_start_participant = matches[0]
    revision = child.config_provenance.implementation_git_commit.value
    if not revision:
        raise ValueError("initialized implementation revision is unavailable")
    current = subprocess.run(["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True).stdout.strip()
    if current != revision:
        raise ValueError("initialized implementation revision differs from the executing source")
    ref = next((item for item in parent.child_runs if str(item.provider) == provider), None)
    if ref is None:
        raise ValueError("initialized parent has no provider child result path")
    result_root = Path(ref.result_path)
    if not result_root.is_absolute():
        result_root = Path(repository_root) / result_root
    result_root = result_root.resolve(strict=True)
    attempts: dict[str, _Attempt] = {}
    cancel_event = threading.Event()
    control_lock = threading.Lock()
    control: dict[str, Any] = {"process_record": None, "verified_absent_at": None}

    def cancel():
        # This callback is deliberately nonblocking: the active F03 poll loop
        # owns the SSH stop operation and records whether process absence was
        # actually verified. Setting an Event alone is never reported as stop.
        cancel_event.set()
        if barrier is not None:
            try:
                barrier.abort("participant_cancelled", nonblocking=True)
            except (TimeoutError, InterruptedError):
                pass
            except Exception:
                pass
        with control_lock:
            return {"requested": True, "verified_stopped": False,
                    "invocation_id": (control["process_record"] or {}).get("invocation_id")}

    def pair_of(value: Any):
        pair = value if hasattr(value, "vm_a") else getattr(value, "deployment", None)
        if pair is None or pair.deployment_id != child.deployment_id:
            raise ValueError("provider output does not match the initialized deployment")
        return pair

    def deadline_for(deployment: Any, *, preparation_start: float | None = None):
        rid = reservation.reservation_id.value
        record = ledger.inspect()["reservations"].get(rid)
        if not isinstance(record, dict) or record.get("session") != ledger.controller_session_id:
            raise ValueError("accepted reservation timing or controller session is unavailable")
        cap_start = float(record["cap_start_monotonic"])
        cap_end = cap_start + float(record["cap_seconds"])
        trigger = float(record.get("provider_trigger_monotonic", cap_end))
        overall = min(cap_end, trigger)
        started = clock.monotonic() if preparation_start is None else preparation_start
        return RemoteDeadline(readiness_deadline_monotonic=min(overall,
                started + float(runtime["allowance"].readiness_seconds)),
            overall_deadline_monotonic=overall,
            cleanup_reserve_seconds=float(runtime["allowance"].cleanup_seconds),
            cap_started_at=reservation.cap_started_at.value,
            cap_expires_at=reservation.expires_at.value)

    def attempt(deployment: Any, provision: Any) -> _Attempt:
        item = attempts.get(deployment.deployment_id)
        if item is not None:
            return item
        pair = pair_of(provision)
        connection = ConnectionOutput.from_deployment(pair, runtime["access"]["timeouts"])
        item = _Attempt(deployment, connection, deadline_for(deployment), child,
            observation, result_root, revision, diagnostics={}, closures={}, inventories={})
        attempts[deployment.deployment_id] = item
        return item

    def prepare(deployment: Any, provision: Any):
        try:
            if cancel_event.is_set():
                return ProviderStageOutcome("prepare", False, {"failure": "controller cancellation was requested"})
            item = attempt(deployment, provision)
            started = clock.monotonic()
            item.deadline = deadline_for(deployment, preparation_start=started)
            item.prepared = prepare_remote_pair(item.connection,
                bootstrap_config=runtime["access"]["bootstrap"],
                readiness_config=runtime["access"]["readiness"], runner=runner,
                deadline=item.deadline, source_root=repository_root,
                source_revision=item.source_revision,
                remote_root=runtime["access"]["guest_remote_root"], clock=clock.monotonic)
            return item.prepared
        except Exception as exc:
            return ProviderStageOutcome("prepare", False,
                {"failure": "remote preparation failed", "failure_type": type(exc).__name__})

    def stabilize(deployment: Any, provision: Any):
        try:
            if cancel_event.is_set():
                return ProviderStageOutcome("stabilize", False, {"failure": "controller cancellation was requested"})
            item = attempt(deployment, provision)
            prepared = item.prepared
            if prepared is None or not prepared.succeeded:
                return ProviderStageOutcome("stabilize", False,
                    {"failure": "successful F02 bootstrap, readiness and runtime staging are required"})
            allowance = _remaining_f11_allowance(runtime["allowance"], "measurement")
            fit = ledger.check_stage_fit(reservation.reservation_id.value,
                stage="measurement", remaining=allowance)
            if not fit.eligible:
                return ProviderStageOutcome("stabilize", False,
                    {"failure": "F11 measurement remainder is insufficient", "reasons": fit.reason_codes})
            start_mono, start_utc = clock.monotonic(), clock.utc_now()
            slot = item.observation.scheduled_start
            if measurement_start_barrier is not None:
                frozen_start = datetime.fromisoformat(
                    measurement_start_barrier.spec["start_utc"].replace("Z", "+00:00"))
                if slot is None or abs((slot - frozen_start).total_seconds()) > 1e-6:
                    raise ValueError("paired scheduled_start must equal its source-bound frozen UTC start")
                until_slot = (frozen_start - start_utc).total_seconds()
                if until_slot <= 60.0:
                    raise TimeoutError("paired preparation did not finish at least 60 seconds before frozen start")
                wait_until = start_mono + max(60.0, until_slot - 60.0)
            else:
                # Ordinary single-run scheduling retains its existing wait behavior.
                wait_until = max(start_mono + 60.0,
                    start_mono + max(0.0, (slot - start_utc).total_seconds()) if slot else start_mono)
            if wait_until + allowance.measurement_seconds + allowance.retrieval_seconds + allowance.cleanup_seconds > item.deadline.overall_deadline_monotonic:
                return ProviderStageOutcome("stabilize", False,
                    {"failure": "stabilization and remaining stages do not fit the original cap"})
            while clock.monotonic() < wait_until:
                if cancel_event.is_set():
                    raise InterruptedError("controller cancellation was requested during stabilization")
                remaining = wait_until - clock.monotonic()
                if item.deadline.remaining(clock.monotonic) <= 0:
                    raise TimeoutError("original provider cap expired during stabilization/slot wait")
                sleep(min(1.0, remaining))
            end_mono, end_utc = clock.monotonic(), clock.utc_now()
            item.stable = {"started_at": start_utc, "finished_at": end_utc,
                "elapsed_seconds": end_mono-start_mono, "scheduled_start": slot}
            return ProviderStageOutcome("stabilize", end_mono-start_mono >= 60.0, item.stable)
        except Exception as exc:
            return ProviderStageOutcome("stabilize", False,
                {"failure": f"{type(exc).__name__}: stabilization failed"})

    def measure(deployment: Any, provision: Any):
        try:
            if cancel_event.is_set():
                return ProviderStageOutcome("measure", False, {"failure": "controller cancellation was requested"})
            item = attempt(deployment, provision)
            if item.stable is None or item.prepared is None or not item.prepared.succeeded:
                return ProviderStageOutcome("measure", False,
                    {"failure": "successful preparation and stabilization are required"})
            if measurement_start_barrier is not None:
                measurement_start_barrier.signal_terminal(measurement_start_participant,
                    succeeded=True, deadline=item.deadline, clock=clock.monotonic)
                measurement_start_barrier.wait_released(deadline=item.deadline,
                    clock=clock.monotonic, cancelled=cancel_event.is_set, sleep=sleep)
                frozen_start = datetime.fromisoformat(
                    measurement_start_barrier.spec["start_utc"].replace("Z", "+00:00"))
                now_utc = clock.utc_now()
                if now_utc >= frozen_start:
                    measurement_start_barrier.abort("participants_not_ready_before_frozen_start",
                        deadline=item.deadline, clock=clock.monotonic, nonblocking=True)
                    raise TimeoutError("paired participants missed the frozen measurement start")
                delay = (frozen_start - now_utc).total_seconds()
                if delay > item.deadline.remaining(clock.monotonic, cleanup=True):
                    measurement_start_barrier.abort("frozen_start_outside_original_deadline",
                        deadline=item.deadline, clock=clock.monotonic, nonblocking=True)
                    raise TimeoutError("frozen measurement start exceeds the original deadline")
                required_after_start = (sum(float(unit.measured_seconds) for unit in item.observation.measurement_units)
                    + float(runtime["allowance"].measurement_shutdown_seconds)
                    + float(runtime["allowance"].retrieval_seconds))
                if delay + required_after_start > item.deadline.remaining(clock.monotonic, cleanup=True):
                    measurement_start_barrier.abort("measurement_and_retrieval_do_not_fit_original_deadline",
                        deadline=item.deadline, clock=clock.monotonic, nonblocking=True)
                    raise TimeoutError("frozen start leaves insufficient measured and retrieval time before cleanup reserve")
                target_mono = clock.monotonic() + max(0.0, delay)
                while clock.monotonic() < target_mono:
                    if cancel_event.is_set() or item.deadline.remaining(clock.monotonic, cleanup=True) <= required_after_start:
                        measurement_start_barrier.abort("cancelled_or_cap_too_short_before_frozen_start",
                            deadline=item.deadline, clock=clock.monotonic, nonblocking=True)
                        raise InterruptedError("frozen measurement start wait lost its original cap fit")
                    sleep(min(0.25, target_mono - clock.monotonic()))
                if clock.utc_now() < frozen_start:
                    measurement_start_barrier.abort("utc_monotonic_start_mapping_was_early",
                        deadline=item.deadline, clock=clock.monotonic, nonblocking=True)
                    raise TimeoutError("monotonic start mapping preceded the frozen UTC boundary")
            pair, connection = pair_of(provision), item.connection
            bootstrap = item.prepared.evidence["bootstrap"]
            guest_runtime = item.prepared.evidence["guest_runtime"]
            mode = str(getattr(item.observation.mode, "value", item.observation.mode))
            total_seconds = (max(float(unit.measured_seconds) for unit in item.observation.measurement_units)
                if mode == "diagnostic_day" else
                sum(float(unit.measured_seconds) for unit in item.observation.measurement_units))
            # Diagnostics span the admitted measurement-stage allowance, not
            # only measured probe seconds; the separate shutdown allowance
            # keeps them alive through bounded F03/F04 closure.
            diagnostic_duration = (float(runtime["allowance"].measurement_seconds)
                + float(runtime["allowance"].measurement_shutdown_seconds))
            for role in ("vm_a", "vm_b"):
                context = build_collection_context(pair, role=role,
                    interface=runtime["access"]["diagnostic_interfaces"][provider][role],
                    # The controller validates the standalone F04 context, but
                    # the workspace is a guest POSIX path (often under /var,
                    # a macOS symlink). Validate its shape against a local
                    # no-symlink path, then bind the declared guest path.
                    output_root=repository_root / ".f09-remote-workspace" / deployment.deployment_id / role,
                    duration_seconds=max(total_seconds, diagnostic_duration), prepared=bootstrap)
                context["output_root"] = runtime["access"]["bootstrap"].workspace
                item.diagnostics[role] = start_remote_diagnostics(connection, context,
                    runner=runner, remote_root=str(Path(guest_runtime.remote_root) / "diagnostics-control"),
                    deadline=item.deadline, clock=clock.monotonic)

            # Bracket a trusted guest UTC observation over the authenticated VM-A
            # control channel. Full round-trip time is charged as uncertainty.
            vm = connection.vm_a
            before_mono, before_utc = clock.monotonic(), clock.utc_now()
            request = _request(f"f09-control-clock-{deployment.deployment_id}", vm,
                ["date", "-u", "+%Y-%m-%dT%H:%M:%S.%NZ"],
                min(connection.timeouts.command_seconds, item.deadline.remaining(clock.monotonic)))
            response = _execute(runner, request)
            after_mono, after_utc = clock.monotonic(), clock.utc_now()
            if response.outcome != CommandOutcome.SUCCEEDED:
                raise RuntimeError("authenticated guest UTC control observation failed")
            guest_now = datetime.fromisoformat(response.stdout.strip().replace("Z", "+00:00"))
            roundtrip = after_mono - before_mono
            active_end = item.deadline.overall_deadline_monotonic - float(runtime["allowance"].retrieval_seconds) - float(runtime["allowance"].cleanup_seconds)
            controller_remaining = active_end - after_mono
            effective_deadline = min(item.deadline.cap_expires_at,
                after_utc + __import__("datetime").timedelta(seconds=active_end-after_mono))
            safe = guest_duration(effective_deadline_utc=effective_deadline,
                guest_now_utc=guest_now, controller_observed_remaining=controller_remaining,
                transport_elapsed_seconds=roundtrip, clock_uncertainty_seconds=roundtrip)
            builds = bootstrap.builds
            tool_identity = {str(build.tool): StringEvidence(value=build.binary_version)
                for build in builds if build.role == "vm_a"}
            receiver_commands = []
            for listener in bootstrap.listeners:
                build = next((row for row in builds if row.role == "vm_b" and row.tool == listener.tool
                              and row.binary_path == listener.binary_path), None)
                if build is None or listener.state != "active":
                    continue
                action_id = f"f02r-vm_b-listener-{listener.tool}"
                listener_action = next((action for action in bootstrap.actions
                    if action.action_id == action_id), None)
                if listener.tool not in {"sockperf", "iperf3"}:
                    continue
                if listener_action is None or not isinstance(listener_action.request, Mapping):
                    raise ValueError(f"prepared {listener.tool} listener lacks its original launch request")
                request_argv = listener_action.request.get("argv")
                if not isinstance(request_argv, list) or len(request_argv) < 1:
                    raise ValueError(f"prepared {listener.tool} listener launch argv is unavailable")
                remote_command = request_argv[-1]
                if not isinstance(remote_command, str):
                    raise ValueError(f"prepared {listener.tool} listener launch command is unavailable")
                remote_argv = shlex.split(remote_command)
                if not remote_argv or remote_argv[0] != "sudo" or "systemd-run" not in remote_argv:
                    raise ValueError(f"prepared {listener.tool} listener launch request is not the recorded F02 systemd unit")
                result_data = listener_action.result
                receiver_commands.append(CommandEvidence(schema_version=2,
                    request={"argv": list(remote_argv), "action_id": action_id,
                        "acquisition_invocation_id": action_id},
                    result={"action_id": action_id,
                        "outcome": result_data.get("outcome", "succeeded"),
                        "started_at": result_data.get("started_at"),
                        "finished_at": result_data.get("finished_at"),
                        "duration_seconds": result_data.get("duration_seconds", 0),
                        "exit_code": result_data.get("exit_code"),
                        "stdout": result_data.get("stdout", ""), "stderr": result_data.get("stderr", "")},
                    producer="vm_b", deployment_id=deployment.deployment_id,
                    unit_id=next((unit.unit_id for unit in item.observation.measurement_units
                                  if str(getattr(unit.phase, "value", unit.phase)) == "multi_flow"), None),
                    binary_version=StringEvidence(value=build.binary_version),
                    source_identity=StringEvidence(value=build.source_reference),
                    monotonic_started=Evidence(unavailable_reason="bootstrap transport has no guest process monotonic start"),
                    monotonic_finished=Evidence(unavailable_reason="bootstrap transport has no guest process monotonic finish"),
                    started_at=Evidence(unavailable_reason="bootstrap transport timing is not native process timing"),
                    finished_at=Evidence(unavailable_reason="bootstrap transport timing is not native process timing")))
            guest_request = GuestRequest(schema_version=2, identity=pair.identity,
                observation=item.observation, b_private_ipv4=connection.private_ipv4_b,
                output_root=Path(runtime["access"]["bootstrap"].workspace),
                effective_deadline_utc=effective_deadline, safe_remaining_seconds=safe,
                clock_uncertainty_seconds=roundtrip, source_revision=guest_runtime.source_revision,
                expected_source_revision=item.source_revision,
                package_root=Path(guest_runtime.remote_root) / "source" / "cloud_network_benchmark",
                package_sha256=guest_runtime.package_sha256,
                shutdown_grace_seconds=float(runtime["allowance"].measurement_shutdown_seconds),
                tool_identity=tool_identity,
                build_observations=[build.model_dump(mode="json") for build in builds],
                receiver_command_evidence=[command.model_dump(mode="json") for command in receiver_commands],
                source_artifact_records=[])
            item.guest = run_remote_f03(connection, guest_request, runner=runner,
                runtime=guest_runtime, deadline=item.deadline, clock=clock.monotonic,
                cancel_event=cancel_event, sleep=sleep,
                on_process_record=lambda record: _record_f03_process(record, control, control_lock, item),
                on_observer_progress=(lambda progress: observer_progress_callback(
                    deployment, pair, connection, bootstrap, item.diagnostics or {}, progress)
                    if observer_progress_callback is not None and mode == "diagnostic_day" else None))
            if item.guest.failure and "process_group_stopped" in item.guest.failure:
                item.f03_process_absent_at = clock.utc_now()
                control["verified_absent_at"] = item.f03_process_absent_at
            item.guest = __import__("dataclasses").replace(item.guest,
                receiver_command_evidence=tuple(receiver_commands))
            item.measurement_ended_at = (item.guest.outcome.guest_finished_at
                if item.guest.outcome is not None else after_utc)
            return ProviderStageOutcome("measure", item.guest.outcome is not None
                and item.guest.outcome.outcome == "succeeded", item.guest)
        except Exception as exc:
            return ProviderStageOutcome("measure", False,
                {"failure": f"{type(exc).__name__}: remote measurement handoff failed ({exc})"})

    def close(deployment: Any, provision: Any):
        try:
            item = attempt(deployment, provision)
            if (item.listener_closure is not None and item.closures is not None
                    and {"vm_a", "vm_b", "vm_a_measurement_closed_at"} <= set(item.closures)
                    and all(item.closures[role].state == "closed" and item.closures[role].closed_at is not None
                            and not item.closures[role].failures for role in ("vm_a", "vm_b"))
                    and item.listener_closure.closed and item.listener_closure.closed_at is not None
                    and not item.listener_closure.failures):
                if barrier is not None:
                    barrier.signal_terminal(barrier_participant, succeeded=True,
                        deadline=item.deadline, clock=clock.monotonic)
                return ProviderStageOutcome("close", True,
                    {"diagnostics": item.closures, "listeners": item.listener_closure,
                     "measurement_ended_at": item.measurement_ended_at})
            if item.measurement_ended_at is None:
                item.measurement_ended_at = clock.utc_now()
            for role in ("vm_a", "vm_b"):
                prior = (item.closures or {}).get(role)
                if (prior is not None and prior.state == "closed" and prior.closed_at is not None
                        and not prior.failures):
                    continue
                process = (item.diagnostics or {}).get(role)
                if process is not None:
                    item.closures[role] = stop_remote_diagnostics(item.connection, process,
                        runner=runner, deadline=item.deadline, clock=clock.monotonic)
            if item.listener_closure is None or not item.listener_closure.closed or item.listener_closure.failures:
                item.listener_closure = stop_vm_b_listeners(item.connection, runner=runner,
                    deadline=item.deadline, clock=clock.monotonic)
            closures = item.closures or {}
            measurement_closed_at = (item.guest.outcome.guest_finished_at if item.guest and item.guest.outcome
                else item.f03_process_absent_at)
            valid = (set(closures) == {"vm_a", "vm_b"}
                and all(value.state == "closed" and value.closed_at is not None and not value.failures
                        for value in closures.values())
                and item.listener_closure.closed and item.listener_closure.closed_at is not None
                and not item.listener_closure.failures
                and item.guest is not None and measurement_closed_at is not None)
            item.closures["vm_a_measurement_closed_at"] = measurement_closed_at
            if barrier is not None:
                barrier.signal_terminal(barrier_participant, succeeded=valid,
                    deadline=item.deadline, clock=clock.monotonic)
            return ProviderStageOutcome("close", valid,
                {"diagnostics": closures, "listeners": item.listener_closure,
                 "measurement_ended_at": item.measurement_ended_at})
        except Exception as exc:
            if barrier is not None:
                try:
                    barrier.signal_terminal(barrier_participant, succeeded=False,
                        deadline=item.deadline, clock=clock.monotonic)
                except Exception:
                    pass
            return ProviderStageOutcome("close", False,
                {"failure": f"{type(exc).__name__}: producer closure failed"})

    def retrieve(deployment: Any, provision: Any):
        actions = []
        try:
            item = attempt(deployment, provision)
            if barrier is not None:
                # Close callbacks write only genuine observed terminal results.
                # Archive, retrieval, and upload cannot start until every
                # source-bound participant has reached successful closure.
                barrier.wait_released(deadline=item.deadline, clock=clock.monotonic,
                    cancelled=cancel_event.is_set, sleep=sleep)
            closures = item.closures or {}
            close_a, close_b = closures.get("vm_a"), closures.get("vm_b")
            if (close_a is None or close_b is None or close_a.closed_at is None or close_b.closed_at is None
                    or item.listener_closure is None or item.measurement_ended_at is None):
                return ProviderStageOutcome("retrieve", False,
                    {"failure": "both producer closures and measurement terminal time are required"})
            # Do not start another workspace inventory after a durable paid
            # result-transfer exhaustion. Cleanup remains in the outer flow.
            state = ledger.inspect()
            rec = state["reservations"].get(reservation.reservation_id.value)
            if rec is None:
                return ProviderStageOutcome("retrieve", False, {"failure": "retrieval reservation is unavailable"})
            if rec.get("execution_scope") == "paid_request":
                transfer = rec.get("result_transfer", {}).get(provider)
                if (state.get("budget_blocked")
                        or transfer is None or transfer["debited_bytes"] >= transfer["payload_limit_bytes"]):
                    return ProviderStageOutcome("retrieve", False,
                        {"failure": "result_transfer_allowance_exhausted"})
            for role in ("vm_a", "vm_b"):
                item.inventories[role] = inventory_remote_workspace(item.connection, role=role,
                    workspace=runtime["access"]["bootstrap"].workspace, runner=runner,
                    deadline=item.deadline, clock=clock.monotonic)
            if any(value.failures for value in item.inventories.values()):
                return ProviderStageOutcome("retrieve", False,
                    {"failure": "one or more remote inventories are incomplete", "inventories": item.inventories})
            declarations = lifecycle_declarations(item.observation.artifact_layout,
                mode=item.observation.mode, measurement_units=item.observation.measurement_units,
                schedule_supplied=item.observation.schedule_entry.value is not None,
                build_observations=item.prepared.evidence["bootstrap"].builds)
            for role in ("vm_a", "vm_b"):
                declarations.extend(_declarations(role))
            unique = {}
            for declaration in declarations:
                previous = unique.get(declaration.artifact_id)
                if previous is not None and previous != declaration:
                    raise ValueError("artifact declaration identity is contradictory")
                unique[declaration.artifact_id] = declaration
            materialized = materialize_build_identity_artifacts(item.result_root,
                deployment.deployment_id, item.prepared.evidence["bootstrap"].builds)
            closure_times = {
                "vm_a": max(item.measurement_ended_at, close_a.closed_at),
                "vm_b": max(close_b.closed_at, item.listener_closure.closed_at),
            }
            manifest = build_remote_closed_inventory(
                identity=pair_of(provision).identity.model_dump(mode="json"),
                deployment_id=deployment.deployment_id,
                remote_workspace=runtime["access"]["bootstrap"].workspace,
                declarations=list(unique.values()),
                remote_files={role: item.inventories[role].files for role in ("vm_a", "vm_b")},
                source_vm_ids={"vm_a": item.connection.vm_a.host, "vm_b": item.connection.vm_b.host},
                measurement_ended_at=item.measurement_ended_at, closure=closure_times,
                timeout_seconds=item.connection.timeouts.transfer_seconds,
                controller_materialized_ids=[record.artifact_id for record in materialized])
            item.manifest = manifest
            if archive_sink is None:
                item.retrieval = retrieve_closed_bundle_with_ledger(ledger=ledger,
                    reservation_id=reservation.reservation_id.value, recovery_path=recovery_path,
                    allowance=_remaining_f11_allowance(runtime["allowance"], "retrieval"),
                    connection=item.connection, manifest=manifest, runner=runner,
                    filesystem=item.result_root, deadline=item.deadline,
                    provider_trigger=deployment.expiry.value.expires_at.value,
                    retry_seconds=float(runtime["copy_retry_seconds"]), now_utc=clock.utc_now,
                    clock=clock.monotonic, compressed_transfer=True)
            else:
                from .gcs_archive import google_cloud_storage_backend, publish_guest_archive_parts
                from .guest_archive import create_remote_guest_archive
                from .retrieval import RetrievalResult
                import hashlib
                bucket = archive_sink["bucket"]
                max_staging = int(archive_sink["maximum_staging_bytes"])
                max_archive = int(archive_sink["maximum_archive_bytes"])
                min_free = int(archive_sink["minimum_controller_free_bytes"])
                staging_root = Path(archive_sink["staging_directory"])
                if staging_root.is_symlink() or not staging_root.is_dir():
                    raise ValueError("configured controller one-part staging directory is unavailable")
                fit = ledger.check_stage_fit(reservation.reservation_id.value, stage="retrieval",
                    remaining=_remaining_f11_allowance(runtime["allowance"], "retrieval"))
                if getattr(fit, "eligible", None) is not True:
                    raise ValueError("result-transfer retrieval stage is not admitted by the current ledger")
                max_part = min(max_staging, 0xFFFFFFFF)
                guest_archives = []
                total_projection = 0
                inventory_by_role = {}
                for role in ("vm_a", "vm_b"):
                    rows_by_path = {row["path"]: dict(row) for row in item.inventories[role].files}
                    workspace = Path(manifest.remote_workspace)
                    selected = []
                    for native in manifest.items:
                        if native.source_role != role:
                            continue
                        try:
                            relative = Path(native.source_path).relative_to(workspace).as_posix()
                        except ValueError as exc:
                            raise ValueError("closed native source path escaped the measured workspace") from exc
                        row = rows_by_path.get(relative)
                        if (row is None or row["size_bytes"] != native.expected_size_bytes
                                or row["sha256"] != native.expected_sha256):
                            raise ValueError("closed role inventory differs from its declared native artifacts")
                        selected.append(row)
                    if not selected:
                        raise ValueError(f"closed native inventory has no declared artifacts for {role}")
                    inventory_by_role[role] = selected
                    rows = selected
                    from .closed_archive import projected_archive_upper_bound
                    total_projection += projected_archive_upper_bound(sum(row["size_bytes"] for row in rows), len(rows))
                if total_projection > max_archive:
                    raise ValueError("closed guest archives exceed the configured aggregate archive byte bound")
                for role in ("vm_a", "vm_b"):
                    guest_dir = f"/tmp/cnb-closed-{deployment.deployment_id}-{role}"
                    guest_archives.append(create_remote_guest_archive(item.connection, role=role,
                        source_directory=manifest.remote_workspace, archive_directory=guest_dir,
                        expected_inventory=inventory_by_role[role],
                        maximum_part_bytes=max_part, maximum_archive_bytes=max_archive,
                        minimum_free_bytes=int(archive_sink.get("minimum_guest_free_bytes", min_free)),
                        runner=runner, deadline=item.deadline, clock=clock.monotonic))
                actions.extend(action for archive in guest_archives for action in archive["actions"])
                part_counter = 0
                def fetch_part(role: str, part: dict) -> Path:
                    nonlocal part_counter
                    part_counter += 1
                    remaining = item.deadline.remaining(clock.monotonic, cleanup=True)
                    size = part["size_bytes"]
                    if (remaining <= 0 or size > max_staging
                            or archive_sink["controller_quota_free_bytes"] < size + min_free):
                        raise OSError("one bounded guest part does not fit the original retrieval window or staging quota")
                    rec = ledger.inspect()["reservations"].get(reservation.reservation_id.value, {})
                    if rec.get("execution_scope") == "paid_request":
                        attempt_id = f"{deployment.deployment_id}:guest-archive:{role}:{part['path']}"
                        if not ledger.debit_result_transfer(reservation.reservation_id.value,
                                provider=provider, attempt_id=attempt_id, maximum_payload_bytes=size):
                            raise ValueError("result-transfer ledger denied this exact guest part")
                    destination = staging_root / f".{deployment.deployment_id}-{role}-{part_counter:06d}.part"
                    archive_dir = next(row["archive_directory"] for row in guest_archives if row["role"] == role)
                    return _fetch_bounded_guest_archive_part(runner=runner, connection=item.connection,
                        role=role, source_path=f"{archive_dir}/{part['path']}",
                        destination=destination, staging_directory=staging_root, size_bytes=size,
                        maximum_staging_bytes=max_staging, minimum_free_bytes=min_free,
                        controller_quota_free_bytes=archive_sink["controller_quota_free_bytes"],
                        deadline=item.deadline, clock=clock.monotonic,
                        action_id=f"f09-guest-part-{deployment.deployment_id}-{role}-{part_counter}",
                        actions=actions)
                backend = archive_sink.get("backend") or google_cloud_storage_backend(archive_sink)
                archive_status = publish_guest_archive_parts(guest_archives=guest_archives,
                    fetch_part=fetch_part, staging_directory=staging_root, backend=backend,
                    bucket=bucket, provider=provider, experiment_id=child.experiment_id,
                    run_id=pair_of(provision).identity.run_id, maximum_part_bytes=max_part,
                    maximum_archive_bytes=max_archive, minimum_controller_free_bytes=min_free,
                    controller_quota_free_bytes=archive_sink["controller_quota_free_bytes"],
                    closed_inventory=manifest.model_dump(mode="json"), deadline=item.deadline,
                    clock=clock.monotonic,
                    object_progress=lambda status: update_archive_recovery(
                        recovery_path, provider, status))
                archive_status["actions"] = actions
                item.retrieval = RetrievalResult(complete=True, cleanup_required=True,
                    actions=actions, records=[], failures=[],
                    archive_status={**archive_status, "validation_state": "archive_pending_validation"})
            return ProviderStageOutcome("retrieve", item.retrieval.complete
                and not item.retrieval.failures, item.retrieval)
        except Exception as exc:
            partial_objects = getattr(exc, "partial_objects", None)
            if isinstance(partial_objects, list):
                from .retrieval import RetrievalResult
                archive_status = {"status": "failed", "verified": False,
                    "partial_objects": partial_objects,
                    "error": f"{type(exc).__name__}: {exc}"}
                failed_retrieval = RetrievalResult(complete=False, cleanup_required=True,
                    actions=actions, records=[], failures=[{"category": "archive_publish_failed",
                        "message": f"{type(exc).__name__}: {exc}"}], archive_status=archive_status)
                return ProviderStageOutcome("retrieve", False, failed_retrieval)
            return ProviderStageOutcome("retrieve", False,
                {"failure": f"{type(exc).__name__}: closed bundle retrieval failed",
                 "details": [(error.get("loc"), error.get("msg")) for error in exc.errors()]
                    if hasattr(exc, "errors") else str(exc)})

    return RemoteStages(prepare=prepare, stabilize=stabilize, measure=measure,
        close=close, retrieve=retrieve, claim=lambda *_: None, cancel=cancel)


def _record_f03_process(record: dict[str, Any], control: dict[str, Any], lock: Any, attempt: _Attempt) -> None:
    with lock:
        control["process_record"] = dict(record)
        attempt.f03_process_record = dict(record)


def build_aws_remote_stages(**kwargs: Any):
    """Backward-compatible AWS constructor for the provider-bound stages."""
    return build_provider_remote_stages(provider="aws", **kwargs)
