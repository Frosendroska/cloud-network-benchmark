"""Strict post-cleanup projection from observed F09 outcomes into F08 bundles."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from threading import Lock
from typing import Any, Mapping


def _failure(message: str, occurred_at: datetime, category: str) -> Any:
    from .contracts.common import FailureEvidence
    return FailureEvidence(category=category, message=message, occurred_at=occurred_at)


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


def _day_units(child: Any, guest: Any) -> list[Any]:
    from .contracts.campaign import PhaseConfiguration
    from .contracts.common import BenchmarkPhase
    units = list(child.measurement_units)
    if _value(child.mode) != "diagnostic_day" or guest is None:
        return units
    parent = next((unit for unit in units if unit.kind == "continuous_day"), None)
    if parent is None:
        return units
    seen = {unit.unit_id for unit in units}
    chunks = {}
    for command in guest.commands:
        payload = command.model_dump(mode="python") if hasattr(command, "model_dump") else command
        unit_id = payload.get("unit_id")
        request = payload.get("request", {})
        argv = request.get("argv", []) if isinstance(request, dict) else []
        if not isinstance(unit_id, str) or not unit_id.startswith("probe-chunk-") or unit_id in seen:
            continue
        try:
            seconds = int(argv[argv.index("-t") + 1])
        except (ValueError, IndexError, TypeError):
            continue
        if not 1 <= seconds <= 300:
            continue
        candidate = PhaseConfiguration(unit_id=unit_id, kind="probe_chunk",
            phase=BenchmarkPhase.MULTI_FLOW, measured_seconds=seconds,
            streams=parent.streams, parent_unit_id=parent.unit_id)
        chunks[unit_id] = candidate
        seen.add(unit_id)
    return [*units, *chunks.values()]


def _outcome(result: Any, stage: str) -> Any:
    value = result.values.get(stage)
    if getattr(value, "stage", None) != stage:
        return None
    return value


def _guest_from(result: Any) -> Any:
    measured = _outcome(result, "measure")
    evidence = getattr(measured, "evidence", None)
    return getattr(evidence, "outcome", None)


def _records_from(result: Any) -> list[Any]:
    retrieved = _outcome(result, "retrieve")
    evidence = getattr(retrieved, "evidence", None)
    return list(getattr(evidence, "records", ()) or ())


def _builds_from(result: Any) -> list[Any]:
    prepared = _outcome(result, "prepare")
    evidence = getattr(prepared, "evidence", None)
    bootstrap = evidence.get("bootstrap") if isinstance(evidence, dict) else None
    return list(getattr(bootstrap, "builds", ()) or ())


def _controller_record(root: Path, deployment_id: str, declaration: Any, payload: bytes | None = None) -> Any:
    from .contracts.artifacts import ArtifactRecord, resolve_artifact_path
    from .contracts.common import Evidence, StringEvidence
    target = resolve_artifact_path(root, declaration.path)
    if payload is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.is_symlink() or not target.is_file() or target.read_bytes() != payload:
                raise ValueError("controller projection destination already contains different bytes")
        else:
            descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
    if target.is_symlink() or not target.is_file():
        return None
    observed = target.read_bytes()
    record = ArtifactRecord(artifact_id=declaration.artifact_id, path=declaration.path,
        kind=declaration.kind, producer="controller", unit_id=declaration.unit_id,
        deployment_id=deployment_id, closed=True, size_bytes=Evidence[int](value=len(observed)),
        sha256=StringEvidence(value=hashlib.sha256(observed).hexdigest()),
        retrieval_state="verified")
    record.verify_materialized_file(root)
    return record


def _materialize_controller_evidence(root: Path, child: Any, result: Any,
                                     declarations: list[Any]) -> list[Any]:
    from .contracts.measurements import CommandEvidence, MeasurementTiming
    by_id = {item.artifact_id: item for item in declarations}
    records = []
    snapshot_hashes = {
        "config_snapshot": child.config_provenance.sha256,
        "resolved_snapshot": child.config_provenance.resolved_snapshot_sha256.value,
        "schedule_snapshot": child.config_provenance.schedule_sha256.value,
    }
    for artifact_id, expected in snapshot_hashes.items():
        declaration = by_id[artifact_id]
        if artifact_id == "schedule_snapshot" and child.schedule_entry.value is None:
            continue
        record = _controller_record(root, child.deployment_id, declaration)
        if record is None or record.sha256.value != expected:
            raise ValueError(f"initialized {artifact_id} bytes do not match their source digest")
        records.append(record)

    guest = _guest_from(result)
    commands = [CommandEvidence.model_validate(item) for item in
        (getattr(guest, "commands", ()) or ())]
    commands.extend(CommandEvidence.model_validate(item) for item in
        (getattr(guest, "receiver_command_evidence", ()) or ()))
    timings = [MeasurementTiming.model_validate(item) for item in
        (getattr(guest, "timings", ()) or ())]
    if _value(child.mode) == "diagnostic_day":
        parent_units = {unit.unit_id for unit in child.measurement_units}
        commands = [item for item in commands if item.unit_id in parent_units]
        timings = [item for item in timings if item.unit_id in parent_units]
    for unit in child.measurement_units:
        unit_commands = [item for item in commands if item.unit_id == unit.unit_id]
        unit_timings = [item for item in timings if item.unit_id == unit.unit_id]
        groups = {
            "commands": ([item.model_dump(mode="json") for item in unit_commands],
                "".join(str(item.result.get("stdout", "")) for item in unit_commands),
                "".join(str(item.result.get("stderr", "")) for item in unit_commands)),
            "timing": ([item.model_dump(mode="json") for item in unit_timings], None, None),
        }
        for suffix, (payload_value, stdout, stderr) in groups.items():
            if payload_value:
                name = f"{unit.unit_id}-{suffix}"
                payload = (json.dumps(payload_value, sort_keys=True, indent=2) + "\n").encode()
                record = _controller_record(root, child.deployment_id, by_id[name], payload)
                if record:
                    records.append(record)
                if suffix == "commands" and _value(child.mode) != "diagnostic_day":
                    for kind, content in (("stdout", stdout), ("stderr", stderr)):
                        sidecar = _controller_record(root, child.deployment_id,
                            by_id[f"{unit.unit_id}-{kind}"], content.encode("utf-8"))
                        if sidecar:
                            records.append(sidecar)
    return records


def _sync_manifest(*, parent_path: Path, child_path: Path, root: Path, child: Any,
                   result: Any, reservation: Any, evaluated_at: datetime) -> Any:
    from .contracts.common import CleanupState, ExecutionState
    from .manifests import (ChildRunManifest, update_child_cleanup,
        update_child_evidence, update_child_execution, update_child_runtime_evidence)
    current = ChildRunManifest.model_validate_json(child_path.read_text(encoding="utf-8"))
    stage_times = result.stage_times
    terminal_at = max((value["finished_at"] for value in stage_times.values()),
        default=evaluated_at)
    creation = stage_times.get("create")
    execution = ExecutionState(result.execution_state)
    if execution == ExecutionState.PROVISION_READY and current.execution_state == ExecutionState.INITIALIZED:
        current = update_child_execution(child_path, ExecutionState.PROVISION_READY,
            creation["finished_at"] if creation else evaluated_at, parent_manifest_path=parent_path)
    elif execution == ExecutionState.FAILED:
        if current.execution_state not in {ExecutionState.FAILED, ExecutionState.INTERRUPTED}:
            stage = next((name for name in ("protect", "claim", "create", "prepare", "stabilize", "measure", "close", "retrieve")
                if _outcome(result, name) is not None and not _outcome(result, name).succeeded), None)
            observed_at = stage_times.get(stage, {}).get("finished_at", terminal_at)
            provider = getattr(child.provider, "value", child.provider)
            cause = result.primary_failure or f"{provider} lifecycle failed"
            failure = _failure(cause, observed_at, "lifecycle_failure")
            target = ExecutionState.INTERRUPTED if result.interrupted else ExecutionState.FAILED
            current = update_child_execution(child_path, target, observed_at, failure,
                parent_manifest_path=parent_path)
    elif execution == ExecutionState.SUCCEEDED:
        if current.execution_state == ExecutionState.INITIALIZED:
            create_at = stage_times["create"]["finished_at"]
            current = update_child_execution(child_path, ExecutionState.PROVISION_READY,
                create_at, parent_manifest_path=parent_path)
            guest = _guest_from(result)
            run_at = guest.guest_started_at if guest is not None else stage_times["measure"]["started_at"]
            current = update_child_execution(child_path, ExecutionState.RUNNING,
                run_at, parent_manifest_path=parent_path)
            current = update_child_execution(child_path, ExecutionState.COLLECTING,
                stage_times["close"]["started_at"], parent_manifest_path=parent_path)
            current = update_child_execution(child_path, ExecutionState.VALIDATING,
                stage_times["retrieve"]["finished_at"], parent_manifest_path=parent_path)

    cleanup_state = CleanupState(result.cleanup_state)
    cleanup = stage_times.get("cleanup")
    if cleanup and current.cleanup_state == CleanupState.NOT_STARTED:
        current = update_child_cleanup(child_path, CleanupState.ATTEMPTED,
            cleanup["started_at"], parent_manifest_path=parent_path)
        if cleanup_state == CleanupState.SUCCEEDED:
            current = update_child_cleanup(child_path, CleanupState.SUCCEEDED,
                cleanup["finished_at"], parent_manifest_path=parent_path)
        elif cleanup_state == CleanupState.FAILED:
            provider = getattr(child.provider, "value", child.provider)
            current = update_child_cleanup(child_path, CleanupState.FAILED,
                cleanup["finished_at"], _failure(f"{provider} cleanup failed",
                    cleanup["finished_at"], "cleanup_failure"), parent_manifest_path=parent_path)

    if execution == ExecutionState.SUCCEEDED and current.execution_state == ExecutionState.VALIDATING:
        current = update_child_execution(child_path, ExecutionState.SUCCEEDED,
            terminal_at, parent_manifest_path=parent_path)

    commands, timings = [], []
    guest = _guest_from(result)
    if guest is not None:
        from .contracts.measurements import CommandEvidence, MeasurementTiming
        commands = [CommandEvidence.model_validate(item) for item in guest.commands]
        commands.extend(CommandEvidence.model_validate(item) for item in guest.receiver_command_evidence)
        timings = [MeasurementTiming.model_validate(item) for item in guest.timings]
    from .lifecycle_bundle import lifecycle_declarations
    declarations = lifecycle_declarations(child.artifacts, mode=child.mode,
        measurement_units=child.measurement_units,
        schedule_supplied=child.schedule_entry.value is not None)
    for role in ("vm_a", "vm_b"):
        from .diagnostics import _declarations
        declarations.extend(_declarations(role))
    records = _records_from(result)
    records.extend(_materialize_controller_evidence(root, child, result, declarations))
    if _value(child.mode) == "diagnostic_day":
        parent_units = {unit.unit_id for unit in child.measurement_units}
        commands = [item for item in commands if item.unit_id in parent_units]
        timings = [item for item in timings if item.unit_id in parent_units]
        records = [item for item in records if item.unit_id is None or item.unit_id in parent_units]
    builds = _builds_from(result)
    if builds:
        from .bootstrap import materialize_build_identity_artifacts
        records.extend(materialize_build_identity_artifacts(root, child.deployment_id, builds))
    retrieval = _outcome(result, "retrieve")
    retrieval_failure = None
    if retrieval is not None and not retrieval.succeeded:
        retrieval_time = stage_times.get("retrieve", {}).get("finished_at", evaluated_at)
        retrieval_failure = _failure("closed bundle retrieval did not complete", retrieval_time,
            "retrieval_failure")
    protected = getattr(_outcome(result, "protect"), "evidence", None)
    expiry = getattr(protected, "expiry", protected if hasattr(protected, "expires_at") else None)
    update_child_runtime_evidence(child_path, evaluated_at, reservation=reservation,
        expiry=expiry,
        commands=commands or None, timings=timings or None, artifact_records=records or None,
        retrieval_failure=retrieval_failure, parent_manifest_path=parent_path)
    # Preserve typed provider deployment metadata and materialized per-role VM
    # observations in the existing manifest fields. Missing sources remain as
    # their initialized unavailable evidence.
    create = _outcome(result, "create")
    deployment = getattr(create, "evidence", None)
    deployment = deployment if hasattr(deployment, "provider_metadata") else getattr(deployment, "deployment", None)
    evidence_updates = {}
    if deployment is not None and hasattr(deployment, "provider_metadata"):
        evidence_updates["provider_metadata"] = deployment.provider_metadata
    observed_metadata = {}
    for role in ("vm_a", "vm_b"):
        record = next((item for item in records if item.artifact_id == f"{role}_metadata"
            and item.retrieval_state == "verified"), None)
        if record is not None:
            record.verify_materialized_file(root)
            from .contracts.measurements import VmMetadata
            parsed = VmMetadata.model_validate_json((root / record.path).read_bytes())
            if parsed.deployment_id != child.deployment_id or parsed.vm_role.value != role:
                raise ValueError("materialized VM metadata identity differs from the child deployment")
            observed_metadata[role] = parsed.model_dump(mode="json")
    if observed_metadata:
        from .contracts.common import StructuredEvidence
        evidence_updates["vm_metadata"] = StructuredEvidence(value=observed_metadata)
    if evidence_updates:
        update_child_evidence(child_path, evaluated_at, **evidence_updates)
    return ChildRunManifest.model_validate_json(child_path.read_text(encoding="utf-8"))


def _validator_input(*, parent: Any, parent_path: Path, child: Any, child_path: Path,
                     root: Path, result: Any, reservation: Any) -> Any:
    from .contracts.common import Evidence, IdentityContext, VmRole
    from .contracts.measurements import CommandEvidence, DiagnosticSample, MeasurementTiming, VmMetadata
    from .contracts.validation import ValidatorInput
    from .lifecycle_bundle import lifecycle_declarations
    from .diagnostics import _declarations, iter_clock_mappings, iter_samples

    guest = _guest_from(result)
    units = _day_units(child, guest)
    observation = child.resolved_observation
    declarations = lifecycle_declarations(child.artifacts, mode=child.mode,
        measurement_units=units, schedule_supplied=child.schedule_entry.value is not None)
    for role in ("vm_a", "vm_b"):
        declarations.extend(_declarations(role))
    artifacts = {}
    for item in declarations:
        previous = artifacts.get(item.artifact_id)
        if previous is not None and previous != item:
            raise ValueError("artifact declaration ID has contradictory producer or path")
        artifacts[item.artifact_id] = item
    commands = [CommandEvidence.model_validate(value) for value in child.commands]
    timings = [MeasurementTiming.model_validate(value) for value in child.timings]
    if guest is not None:
        commands.extend(CommandEvidence.model_validate(value) for value in guest.commands)
        commands.extend(CommandEvidence.model_validate(value) for value in guest.receiver_command_evidence)
        timings.extend(MeasurementTiming.model_validate(value) for value in guest.timings)
    commands_by_key = {}
    for command in commands:
        key = (command.request.get("action_id"), command.deployment_id, command.unit_id)
        if key in commands_by_key and commands_by_key[key] != command:
            raise ValueError("command identity has conflicting captured content")
        commands_by_key[key] = command
    commands = list(commands_by_key.values())
    timings_by_key = {}
    for timing in timings:
        if timing.unit_id in timings_by_key and timings_by_key[timing.unit_id] != timing:
            raise ValueError("timing identity has conflicting captured content")
        timings_by_key[timing.unit_id] = timing
    timings = list(timings_by_key.values())
    allowed_units = {unit.unit_id for unit in units}
    commands = [item for item in commands if item.unit_id in allowed_units]
    timings = [item for item in timings if item.unit_id in allowed_units]
    records = list(child.artifact_records) + _records_from(result)
    builds = _builds_from(result)
    record_map = {}
    for record in records:
        key = (record.artifact_id, record.path, record.unit_id)
        if key in record_map and record_map[key] != record:
            raise ValueError("duplicate source records conflict during strict projection")
        record_map[key] = record
    records = list(record_map.values())
    verified = {}
    for record in records:
        if record.retrieval_state == "verified":
            record.verify_materialized_file(root)
            verified[record.artifact_id] = record
    diagnostics, metadata, clock_mappings = [], [], []
    for role in ("vm_a", "vm_b"):
        for suffix, parser in (("metadata", VmMetadata.model_validate_json),
                ("diagnostics", iter_samples), ("clock", iter_clock_mappings)):
            record = verified.get(f"{role}_{suffix}")
            if record is None:
                continue
            payload_path = root / record.path
            parsed = parser(payload_path.read_bytes()) if suffix == "metadata" else list(parser(payload_path))
            if suffix == "metadata":
                metadata.append(parsed)
            elif suffix == "diagnostics":
                diagnostics.extend(DiagnosticSample.model_validate(item) for item in parsed)
            else:
                clock_mappings.extend(parsed)
    receiver_commands = list(getattr(guest, "receiver_command_evidence", ()) or ()) if guest is not None else []
    resolved_path = root / child.config_provenance.resolved_snapshot_path
    config_path = root / child.config_provenance.snapshot_path
    schedule_path = (Evidence[str](value=str(root / "schedule.csv"))
        if child.schedule_entry.value is not None and child.config_provenance.schedule_sha256.value
        else Evidence[str](unavailable_reason="no schedule row supplied"))
    if child.schedule_entry.value is not None and not schedule_path.value:
        schedule_path = Evidence[str](unavailable_reason="schedule snapshot path is not declared")
    identity = IdentityContext(experiment_id=child.experiment_id, config_id=child.config_id,
        campaign_id=child.campaign_id, observation_id=child.observation_id, run_id=child.run_id,
        deployment_id=child.deployment_id, provider=child.provider,
        replaces_run_id=child.replaces_run_id)
    value = ValidatorInput(schema_version=2, identity=identity, mode=child.mode,
        context=child.context, campaign_manifest_path=str(parent_path),
        child_manifest_path=str(child_path), child_result_root=str(root),
        config_snapshot_path=str(config_path), config_sha256=child.config_provenance.sha256,
        resolved_snapshot_path=str(resolved_path),
        resolved_sha256=Evidence[str].model_validate(child.config_provenance.resolved_snapshot_sha256.model_dump(mode="python")),
        schedule_sha256=Evidence[str].model_validate(child.config_provenance.schedule_sha256.model_dump(mode="python")),
        schedule_snapshot_path=schedule_path, schedule_entry=child.schedule_entry,
        provider=child.provider, scenario=child.scenario,
        enabled_phases=list(dict.fromkeys(unit.phase for unit in units)),
        expected_vm_roles=[VmRole.VM_A, VmRole.VM_B],
        measurement_direction=observation.measurement_direction,
        artifacts=artifacts, measurement_units=units, artifact_records=records,
        commands=commands, timings=timings, diagnostics=diagnostics, vm_metadata_records=metadata,
        execution_state=child.execution_state, cleanup_state=child.cleanup_state,
        failure=child.failure, cleanup_failure=child.cleanup_failure,
        retrieval_failure=child.retrieval_failure, vm_metadata=child.vm_metadata,
        provider_metadata=child.provider_metadata, tool_versions=child.tool_versions,
        implementation_git_commit=child.config_provenance.implementation_git_commit,
        design_git_commit=child.config_provenance.design_git_commit,
        implementation_git=child.config_provenance.implementation_git,
        design_git=child.config_provenance.design_git,
        validation_status=child.validation_status,
        reservation=(Evidence(value=reservation) if reservation is not None else child.reservation),
        expiry=child.expiry)
    return value, builds, receiver_commands, clock_mappings


def finalize_production_batch(*, parent_path: Path, children: Mapping[str, Any],
        parent: Any, lifecycle: Any, repository_root: Path, evaluated_at: datetime | None = None) -> dict[str, Any]:
    """Persist strict per-provider F08 inputs/results, then mutate statuses, then seal all."""
    from .contracts.common import ValidationStatus
    from .contracts.validation import ValidatorInput
    from .lifecycle_bundle import persist_validation, seal_bundle
    from .manifests import (CampaignManifest, ChildRunManifest, recompute_parent_manifest,
        update_child_runtime_evidence)
    from .validation import evaluate_comparison, validate_bundle

    fixed_at = evaluated_at or datetime.now(timezone.utc)
    lock = Lock()
    projections = {}
    results = {}
    inputs = {}
    roots = {}
    paths = {}
    reservation = lifecycle.reservation_evidence
    if reservation is None:
        raise ValueError("post-cleanup projection requires an accepted source-bound reservation")
    for provider, child_arg in children.items():
        ref = next(row for row in parent.child_runs if str(row.provider) == provider)
        child_path = (repository_root / ref.manifest_path).resolve(strict=True)
        child = ChildRunManifest.model_validate_json(child_path.read_text(encoding="utf-8"))
        root = (repository_root / ref.result_path).resolve(strict=True)
        result = lifecycle.provider_results[provider]
        with lock:
            child = _sync_manifest(parent_path=parent_path, child_path=child_path,
                root=root, child=child, result=result, reservation=reservation, evaluated_at=fixed_at)
        value, builds, receiver_commands, clock_mappings = _validator_input(parent=parent, parent_path=parent_path, child=child,
            child_path=child_path, root=root, result=result, reservation=reservation)
        value = ValidatorInput.model_validate(value.model_dump(mode="python"))
        from .lifecycle_bundle import bind_retrieved_acquisition_sources
        bound, _sources, _unavailable = bind_retrieved_acquisition_sources(value=value, child_root=root,
            source_artifact_records=value.artifact_records, build_evidence=builds,
            receiver_command_evidence=receiver_commands, clock_mappings=clock_mappings)
        inputs[provider], roots[provider], paths[provider] = bound, root, child_path
    preliminary = {provider: validate_bundle(value, evaluated_at=fixed_at)
        for provider, value in inputs.items()}
    compared = (evaluate_comparison(list(inputs.values()), list(preliminary.values()))
        if len(inputs) > 1 else {inputs[next(iter(inputs))].identity.run_id:
            preliminary[next(iter(inputs))]})
    for provider in children:
        peers = [value for key, value in inputs.items() if key != provider]
        peer_results = [preliminary[key] for key in inputs if key != provider]
        result, summary = persist_validation(root=roots[provider],
            validator_input_path=roots[provider] / "validation" / "input.json",
            value=inputs[provider], evaluated_at=fixed_at, peers=peers,
            peer_results=peer_results)
        if result.model_dump(mode="json") != compared[inputs[provider].identity.run_id].model_dump(mode="json"):
            raise ValueError("per-provider persisted validation differs from batch peer comparison")
        results[provider] = (result, summary)
    for provider, (result, _summary) in results.items():
        child = ChildRunManifest.model_validate_json(paths[provider].read_text(encoding="utf-8"))
        update_child_runtime_evidence(paths[provider], fixed_at,
            validation_status=ValidationStatus.model_validate(result.validation_status),
            parent_manifest_path=parent_path)
    recompute_parent_manifest(parent_path, fixed_at, "post-cleanup validation batch persisted")
    for root in roots.values():
        seal_bundle(root)
    return results
