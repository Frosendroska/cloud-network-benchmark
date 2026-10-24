"""Source-bound AWS remote-stage composition for F09."""
from __future__ import annotations

import shlex
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


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


def build_aws_remote_stages(*, campaign: Any, parent: Any, children: dict[str, Any],
        runtime: dict[str, Any], ledger: Any, reservation: Any, runner: Any,
        clock: Any, repository_root: Path, recovery_path: Path,
        sleep: Callable[[float], None] = time.sleep):
    """Bind real F02/F03/F04/retrieval adapters to one accepted AWS reservation.

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
    from .orchestration import ProviderStageOutcome, _remaining_f11_allowance
    from .lifecycle_guest import GuestRequest

    if set(children) != {"aws"} or str(getattr(campaign.mode, "value", campaign.mode)) != "acceptance":
        raise ValueError("AWS production remote stages currently require one acceptance child")
    child = children["aws"]
    observation = child.resolved_observation
    if (str(getattr(observation.provider, "value", observation.provider)) != "aws"
            or observation.mode != campaign.mode or observation.config_id != child.config_id
            or observation.experiment_id != child.experiment_id):
        raise ValueError("initialized AWS observation identity does not match the child")
    revision = child.config_provenance.implementation_git_commit.value
    if not revision:
        raise ValueError("initialized implementation revision is unavailable")
    current = subprocess.run(["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True).stdout.strip()
    if current != revision:
        raise ValueError("initialized implementation revision differs from the executing source")
    ref = next((item for item in parent.child_runs if str(item.provider) == "aws"), None)
    if ref is None:
        raise ValueError("initialized parent has no AWS child result path")
    result_root = Path(ref.result_path)
    if not result_root.is_absolute():
        result_root = Path(repository_root) / result_root
    result_root = result_root.resolve(strict=True)
    attempts: dict[str, _Attempt] = {}

    def pair_of(value: Any):
        pair = value if hasattr(value, "vm_a") else getattr(value, "deployment", None)
        if pair is None or pair.deployment_id != child.deployment_id:
            raise ValueError("provider output does not match the initialized AWS deployment")
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
            while clock.monotonic() < wait_until:
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
            item = attempt(deployment, provision)
            if item.stable is None or item.prepared is None or not item.prepared.succeeded:
                return ProviderStageOutcome("measure", False,
                    {"failure": "successful preparation and stabilization are required"})
            pair, connection = pair_of(provision), item.connection
            bootstrap = item.prepared.evidence["bootstrap"]
            guest_runtime = item.prepared.evidence["guest_runtime"]
            total_seconds = float(runtime["allowance"].measurement_seconds)
            for role in ("vm_a", "vm_b"):
                context = build_collection_context(pair, role=role,
                    interface=runtime["access"]["diagnostic_interfaces"]["aws"][role],
                    output_root=runtime["access"]["bootstrap"].workspace,
                    duration_seconds=total_seconds, prepared=bootstrap)
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
            listener_action = next((action for action in bootstrap.actions
                if action.action_id == "f02r-vm_b-listener-iperf3"), None)
            receiver_commands = []
            if listener_action is not None:
                build = next((row for row in builds if row.role == "vm_b" and row.tool == "iperf3"), None)
                argv = listener_action.request.get("argv")
                remote_argv = shlex.split(argv[-1]) if isinstance(argv, list) and argv else []
                if build is not None and remote_argv:
                    result_data = listener_action.result
                    receiver_commands.append(CommandEvidence(schema_version=2,
                        request={"argv": remote_argv, "action_id": listener_action.action_id},
                        result={"action_id": listener_action.action_id,
                            "outcome": result_data.get("outcome", "succeeded"),
                            "started_at": result_data.get("started_at"),
                            "finished_at": result_data.get("finished_at"),
                            "duration_seconds": result_data.get("duration_seconds", 0),
                            "exit_code": result_data.get("exit_code"),
                            "stdout": result_data.get("stdout", ""), "stderr": result_data.get("stderr", "")},
                        producer="vm_b", deployment_id=deployment.deployment_id,
                        unit_id=next((unit.unit_id for unit in item.observation.measurement_units
                                      if unit.phase.value == "multi_flow"), None),
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
                runtime=guest_runtime, deadline=item.deadline, clock=clock.monotonic)
            item.guest = __import__("dataclasses").replace(item.guest,
                receiver_command_evidence=tuple(receiver_commands))
            item.measurement_ended_at = (item.guest.outcome.guest_finished_at
                if item.guest.outcome is not None else after_utc)
            return ProviderStageOutcome("measure", item.guest.outcome is not None
                and item.guest.outcome.outcome == "succeeded", item.guest)
        except Exception as exc:
            return ProviderStageOutcome("measure", False,
                {"failure": f"{type(exc).__name__}: remote measurement handoff failed"})

    def close(deployment: Any, provision: Any):
        try:
            item = attempt(deployment, provision)
            if item.measurement_ended_at is None:
                item.measurement_ended_at = clock.utc_now()
            for role in ("vm_a", "vm_b"):
                process = (item.diagnostics or {}).get(role)
                if process is not None:
                    item.closures[role] = stop_remote_diagnostics(item.connection, process,
                        runner=runner, deadline=item.deadline, clock=clock.monotonic)
            item.listener_closure = stop_vm_b_listeners(item.connection, runner=runner,
                deadline=item.deadline, clock=clock.monotonic)
            closures = item.closures or {}
            valid = (set(closures) == {"vm_a", "vm_b"}
                and all(value.state == "closed" and value.closed_at is not None and not value.failures
                        for value in closures.values())
                and item.listener_closure.closed and item.listener_closure.closed_at is not None
                and not item.listener_closure.failures
                and item.guest is not None and item.guest.outcome is not None)
            item.closures["vm_a_measurement_closed_at"] = item.guest.outcome.guest_finished_at if item.guest and item.guest.outcome else None
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
            for role in ("vm_a", "vm_b"):
                item.inventories[role] = inventory_remote_workspace(item.connection, role=role,
                    workspace=runtime["access"]["bootstrap"].workspace, runner=runner,
                    deadline=item.deadline, clock=clock.monotonic)
            if any(value.failures for value in item.inventories.values()):
                return ProviderStageOutcome("retrieve", False,
                    {"failure": "one or more remote inventories are incomplete", "inventories": item.inventories})
            declarations = lifecycle_declarations(item.observation.artifact_layout,
                mode=item.observation.mode, measurement_units=item.observation.measurement_units,
                schedule_supplied=item.observation.schedule_entry.value is not None)
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
                {"failure": f"{type(exc).__name__}: closed bundle retrieval failed"})

    return RemoteStages(prepare=prepare, stabilize=stabilize, measure=measure,
        close=close, retrieve=retrieve, claim=lambda *_: None)
