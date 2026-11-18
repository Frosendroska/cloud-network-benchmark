"""Source-bound provider remote-stage composition for F09."""
from __future__ import annotations

import subprocess
import shlex
import re
import time
import threading
from dataclasses import dataclass
from datetime import datetime
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
    iperf3_control_pre_measure: dict[str, Any] | None = None


@dataclass(frozen=True)
class _MeasurementStageWindow:
    execution_deadline_monotonic: float
    shutdown_deadline_monotonic: float


def _measurement_stage_window(*, overall_deadline: float, measurement_started: float,
        allowance: Any) -> _MeasurementStageWindow:
    """Bound measurement and shutdown while preserving retrieval and cleanup reserves."""
    workflow_end = overall_deadline - float(allowance.retrieval_seconds) - float(allowance.cleanup_seconds)
    shutdown = float(allowance.measurement_shutdown_seconds)
    execution_end = min(measurement_started + float(allowance.measurement_seconds), workflow_end - shutdown)
    return _MeasurementStageWindow(execution_end, execution_end + shutdown)


def _wait_for_stabilization(*, wait_until: float, deadline: Any,
        clock: Callable[[], float], cancelled: Callable[[], bool],
        sleep: Callable[[float], None]) -> None:
    """Wait to the stabilization boundary without deriving sleep from two clocks."""
    while True:
        if cancelled():
            raise InterruptedError("controller cancellation was requested during stabilization")
        now = clock()
        if deadline.remaining(lambda: now) <= 0:
            raise TimeoutError("original provider cap expired during stabilization/slot wait")
        remaining = wait_until - now
        if remaining <= 0:
            return
        sleep(min(1.0, remaining))


def build_provider_remote_stages(*, provider: str, campaign: Any, parent: Any, children: dict[str, Any],
        runtime: dict[str, Any], ledger: Any, reservation: Any, runner: Any,
        clock: Any, repository_root: Path, recovery_path: Path,
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
        capture_iperf3_control_prefix, guest_duration, inventory_remote_workspace, prepare_remote_pair,
        run_remote_f03, start_remote_diagnostics, stop_remote_diagnostics,
        stop_vm_b_listeners)
    from .lifecycle_bundle import (build_remote_closed_inventory, lifecycle_declarations,
        remote_inventory_paths_by_role, retrieve_closed_bundle_with_ledger)
    from .orchestration import ProviderStageOutcome, _remaining_f11_allowance
    from .lifecycle_guest import GuestRequest

    if set(children) != {provider}:
        raise ValueError("production remote stages require exactly one provider child")
    child = children[provider]
    observation = child.resolved_observation
    if (str(getattr(observation.provider, "value", observation.provider)) != provider
            or observation.mode != campaign.mode or observation.config_id != child.config_id
            or observation.experiment_id != child.experiment_id):
        raise ValueError("initialized provider observation identity does not match the child")
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
            wait_until = max(start_mono + 60.0,
                start_mono + max(0.0, (slot - start_utc).total_seconds()) if slot else start_mono)
            if wait_until + allowance.measurement_seconds + allowance.retrieval_seconds + allowance.cleanup_seconds > item.deadline.overall_deadline_monotonic:
                return ProviderStageOutcome("stabilize", False,
                    {"failure": "stabilization and remaining stages do not fit the original cap"})
            _wait_for_stabilization(wait_until=wait_until, deadline=item.deadline,
                clock=clock.monotonic, cancelled=cancel_event.is_set, sleep=sleep)
            end_mono, end_utc = clock.monotonic(), clock.utc_now()
            item.stable = {"started_at": start_utc, "finished_at": end_utc,
                "elapsed_seconds": end_mono-start_mono, "scheduled_start": slot}
            return ProviderStageOutcome("stabilize", end_mono-start_mono >= 60.0, item.stable)
        except Exception as exc:
            return ProviderStageOutcome("stabilize", False,
                {"failure": f"{type(exc).__name__}: stabilization failed"})

    def measure(deployment: Any, provision: Any):
        item = None
        try:
            if cancel_event.is_set():
                return ProviderStageOutcome("measure", False, {"failure": "controller cancellation was requested"})
            item = attempt(deployment, provision)
            if item.stable is None or item.prepared is None or not item.prepared.succeeded:
                return ProviderStageOutcome("measure", False,
                    {"failure": "successful preparation and stabilization are required"})
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
                context["output_root"] = runtime["access"]["bootstrap"].results_workspace
                item.diagnostics[role] = start_remote_diagnostics(connection, context,
                    runner=runner, remote_root=str(Path(guest_runtime.remote_root) / "diagnostics-control"),
                    deadline=item.deadline, clock=clock.monotonic)

            # Bracket a trusted guest UTC observation over the authenticated VM-A
            # control channel. Charge setup time to the reserved measurement
            # stage, and reserve shutdown before retrieval and cleanup.
            vm = connection.vm_a
            before_mono, before_utc = clock.monotonic(), clock.utc_now()
            measurement_allowance = _remaining_f11_allowance(runtime["allowance"], "measurement")
            measurement_window = _measurement_stage_window(
                overall_deadline=item.deadline.overall_deadline_monotonic,
                measurement_started=before_mono, allowance=measurement_allowance)
            clock_remaining = min(item.deadline.remaining(clock.monotonic),
                max(0.0, measurement_window.execution_deadline_monotonic - before_mono))
            request = _request(f"f09-control-clock-{deployment.deployment_id}", vm,
                ["date", "-u", "+%Y-%m-%dT%H:%M:%S.%NZ"],
                min(connection.timeouts.command_seconds, clock_remaining))
            response = _execute(runner, request)
            after_mono, after_utc = clock.monotonic(), clock.utc_now()
            if response.outcome != CommandOutcome.SUCCEEDED:
                raise RuntimeError("authenticated guest UTC control observation failed")
            guest_now = datetime.fromisoformat(response.stdout.strip().replace("Z", "+00:00"))
            roundtrip = after_mono - before_mono
            controller_remaining = measurement_window.shutdown_deadline_monotonic - after_mono
            effective_deadline = min(item.deadline.cap_expires_at,
                after_utc + __import__("datetime").timedelta(seconds=controller_remaining))
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
                output_root=Path(runtime["access"]["bootstrap"].results_workspace),
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
            iperf_listener = next((listener for listener in bootstrap.listeners
                if listener.tool == "iperf3" and listener.state == "active"), None)
            if iperf_listener is not None:
                item.iperf3_control_pre_measure = capture_iperf3_control_prefix(connection,
                    iperf_listener, controller_session_id=ledger.controller_session_id,
                    runner=runner, deadline=item.deadline,
                    active_deadline_monotonic=measurement_window.execution_deadline_monotonic,
                    clock=clock.monotonic)
            else:
                item.iperf3_control_pre_measure = {"available": False,
                    "reason": "prepared iperf3 listener identity is unavailable"}
            boundary_failure = _pre_measure_capture_failure(item.iperf3_control_pre_measure,
                connection=connection, listener=iperf_listener,
                controller_session_id=ledger.controller_session_id)
            if boundary_failure is not None:
                return _measurement_handoff_failure(item,
                    RuntimeError("VM B owner-bound pre-measure log-prefix proof is unavailable"),
                    boundary_failure=boundary_failure)
            item.guest = run_remote_f03(connection, guest_request, runner=runner,
                runtime=guest_runtime, deadline=item.deadline, clock=clock.monotonic,
                active_deadline_monotonic=measurement_window.execution_deadline_monotonic,
                shutdown_deadline_monotonic=measurement_window.shutdown_deadline_monotonic,
                cancel_event=cancel_event, sleep=sleep,
                on_process_record=lambda record: _record_f03_process(record, control, control_lock, item))
            if item.guest.failure and "process_group_stopped" in item.guest.failure:
                item.f03_process_absent_at = clock.utc_now()
                control["verified_absent_at"] = item.f03_process_absent_at
            item.guest = __import__("dataclasses").replace(item.guest,
                receiver_command_evidence=tuple(receiver_commands), diagnostics=dict(item.diagnostics),
                iperf3_control_pre_measure=item.iperf3_control_pre_measure or {})
            item.measurement_ended_at = (item.guest.outcome.guest_finished_at
                if item.guest.outcome is not None else after_utc)
            return ProviderStageOutcome("measure", item.guest.outcome is not None
                and item.guest.outcome.outcome == "succeeded", item.guest)
        except Exception as exc:
            return _measurement_handoff_failure(item, exc)

    def close(deployment: Any, provision: Any):
        try:
            item = attempt(deployment, provision)
            if (item.listener_closure is not None and item.closures is not None
                    and {"vm_a", "vm_b", "vm_a_measurement_closed_at"} <= set(item.closures)
                    and all(item.closures[role].state == "closed" and item.closures[role].closed_at is not None
                            and not item.closures[role].failures for role in ("vm_a", "vm_b"))
                    and item.listener_closure.closed and item.listener_closure.closed_at is not None
                    and not item.listener_closure.failures):
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
                    results_workspace=runtime["access"]["bootstrap"].results_workspace,
                    controller_session_id=ledger.controller_session_id,
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
            return ProviderStageOutcome("close", valid,
                {"diagnostics": closures, "listeners": item.listener_closure,
                 "measurement_ended_at": item.measurement_ended_at})
        except Exception as exc:
            return ProviderStageOutcome("close", False,
                {"failure": f"{type(exc).__name__}: producer closure failed"})

    def retrieve(deployment: Any, provision: Any):
        try:
            item = attempt(deployment, provision)
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
            declarations = list(unique.values())
            expected_paths = remote_inventory_paths_by_role(declarations)
            transfer = rec.get("result_transfer", {}).get(provider)
            inventory_byte_budget = None
            if rec.get("execution_scope") == "paid_request":
                if (not isinstance(transfer, Mapping)
                        or isinstance(transfer.get("payload_limit_bytes"), bool)
                        or not isinstance(transfer.get("payload_limit_bytes"), int)
                        or isinstance(transfer.get("debited_bytes"), bool)
                        or not isinstance(transfer.get("debited_bytes"), int)):
                    return ProviderStageOutcome("retrieve", False,
                        {"failure": "result_transfer_inventory_bound_unavailable"})
                inventory_byte_budget = transfer["payload_limit_bytes"] - transfer["debited_bytes"]
                if inventory_byte_budget <= 0:
                    return ProviderStageOutcome("retrieve", False,
                        {"failure": "result_transfer_allowance_exhausted"})
            inventoried_bytes = 0
            for role in ("vm_a", "vm_b"):
                item.inventories[role] = inventory_remote_workspace(item.connection, role=role,
                    workspace=runtime["access"]["bootstrap"].results_workspace, runner=runner,
                    deadline=item.deadline, clock=clock.monotonic, expected_paths=expected_paths[role],
                    maximum_bytes=(inventory_byte_budget - inventoried_bytes
                        if inventory_byte_budget is not None else None))
                inventoried_bytes += sum(int(row["size_bytes"]) for row in item.inventories[role].files)
            if any(value.failures for value in item.inventories.values()):
                return ProviderStageOutcome("retrieve", False,
                    {"failure": "one or more remote inventories are incomplete", "inventories": item.inventories})
            materialized = materialize_build_identity_artifacts(item.result_root,
                deployment.deployment_id, item.prepared.evidence["bootstrap"].builds)
            closure_times = {
                "vm_a": max(item.measurement_ended_at, close_a.closed_at),
                "vm_b": max(close_b.closed_at, item.listener_closure.closed_at),
            }
            manifest = build_remote_closed_inventory(
                identity=pair_of(provision).identity.model_dump(mode="json"),
                deployment_id=deployment.deployment_id,
                remote_workspace=runtime["access"]["bootstrap"].results_workspace,
                declarations=list(unique.values()),
                remote_files={role: item.inventories[role].files for role in ("vm_a", "vm_b")},
                source_vm_ids={"vm_a": item.connection.vm_a.host, "vm_b": item.connection.vm_b.host},
                measurement_ended_at=item.measurement_ended_at, closure=closure_times,
                timeout_seconds=item.connection.timeouts.transfer_seconds,
                controller_materialized_ids=[record.artifact_id for record in materialized])
            item.manifest = manifest
            item.retrieval = retrieve_closed_bundle_with_ledger(ledger=ledger,
                reservation_id=reservation.reservation_id.value, recovery_path=recovery_path,
                allowance=_remaining_f11_allowance(runtime["allowance"], "retrieval"),
                connection=item.connection, manifest=manifest, runner=runner,
                filesystem=item.result_root, deadline=item.deadline,
                provider_trigger=deployment.expiry.value.expires_at.value,
                retry_seconds=float(runtime["copy_retry_seconds"]), now_utc=clock.utc_now,
                clock=clock.monotonic)
            return ProviderStageOutcome("retrieve", item.retrieval.complete
                and not item.retrieval.failures, item.retrieval)
        except Exception as exc:
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


def _pre_measure_capture_failure(capture: Any, *, connection: Any, listener: Any,
        controller_session_id: str) -> str | None:
    """Fail closed before F03 unless the same prepared VM B listener was captured."""
    if not isinstance(capture, Mapping) or capture.get("available") is not True:
        return "pre-measure listener prefix snapshot is missing or unavailable"
    snapshot, action = capture.get("snapshot"), capture.get("action")
    if not isinstance(snapshot, Mapping) or action is None:
        return "pre-measure listener prefix snapshot has no owned action evidence"
    action_payload = action.model_dump(mode="json") if hasattr(action, "model_dump") else action
    if not isinstance(action_payload, Mapping):
        return "pre-measure listener prefix action evidence is malformed"
    result = action_payload.get("result")
    request = action_payload.get("request")
    argv = request.get("argv", []) if isinstance(request, Mapping) else []
    remote_command = " ".join(str(item) for item in argv)
    if (action_payload.get("action_id") != f"f09-iperf3-control-prefix-measure-{connection.deployment_id}"
            or action_payload.get("action_type") != "ssh"
            or action_payload.get("role") != "vm_b"
            or action_payload.get("vm_id") != connection.vm_b.host
            or not isinstance(result, Mapping) or result.get("outcome") != "succeeded"
            or result.get("exit_code") != 0
            or any(token not in remote_command for token in
                (listener.stdout_path, listener.stderr_path, controller_session_id, listener.unit))):
        return "pre-measure listener prefix action is not bound to the selected VM B listener"
    count, digest, ids = snapshot.get("prefix_bytes"), snapshot.get("prefix_sha256"), snapshot.get("log_ids")
    valid_ids = (isinstance(ids, list) and len(ids) == 2 and all(isinstance(pair, list) and len(pair) == 2
        and all(isinstance(number, int) and not isinstance(number, bool) and number >= 0 for number in pair)
        for pair in ids) and ids[0] != ids[1])
    if (snapshot.get("boundary") != "pre_measure"
            or snapshot.get("deployment_id") != connection.deployment_id
            or snapshot.get("controller_session_id") != controller_session_id
            or snapshot.get("listener_invocation_id") != "f02r-vm_b-listener-iperf3"
            or snapshot.get("unit") != listener.unit
            or snapshot.get("log_path") != listener.stdout_path
            or isinstance(snapshot.get("pid"), bool) or not isinstance(snapshot.get("pid"), int) or snapshot["pid"] <= 0
            or isinstance(snapshot.get("start_ticks"), bool) or not isinstance(snapshot.get("start_ticks"), int)
            or snapshot["start_ticks"] < 0
            or not isinstance(snapshot.get("boot_id"), str)
            or re.fullmatch(r"[0-9a-fA-F-]{36}", snapshot["boot_id"]) is None
            or snapshot.get("cgroup") != f"/system.slice/{listener.unit}.service"
            or not valid_ids
            or isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 65536
            or not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None):
        return "pre-measure listener prefix snapshot has foreign or incomplete owner/boundary identity"
    return None


def _measurement_handoff_failure(attempt: _Attempt | None, error: BaseException,
        *, boundary_failure: str | None = None) -> Any:
    """Retain diagnostic handles while keeping exception text out of durable evidence."""
    from .lifecycle_adapters import RemoteDiagnosticsStartError
    from .orchestration import ProviderStageOutcome
    evidence = {"failure": f"{type(error).__name__}: remote measurement handoff failed",
        "diagnostics": attempt.diagnostics if attempt is not None else {},
        "iperf3_control_pre_measure": (attempt.iperf3_control_pre_measure
            if attempt is not None and attempt.iperf3_control_pre_measure is not None
            else {"available": False, "reason": "measurement boundary was not captured"})}
    if boundary_failure is not None:
        evidence["boundary_failure"] = boundary_failure
    if isinstance(error, RemoteDiagnosticsStartError):
        evidence["startup_failure"] = error.safe_reason
        evidence["actions"] = error.actions
    return ProviderStageOutcome("measure", False, evidence)


def build_aws_remote_stages(**kwargs: Any):
    """Backward-compatible AWS constructor for the provider-bound stages."""
    return build_provider_remote_stages(provider="aws", **kwargs)
