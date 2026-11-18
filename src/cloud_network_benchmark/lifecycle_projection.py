"""Strict post-cleanup projection from observed F09 outcomes into F08 bundles."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
from threading import Lock
from typing import Any, Mapping

from .contracts.artifacts import resolve_artifact_path


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


def _failed_stage(result: Any, stage_times: Mapping[str, Any]) -> str | None:
    """Find the earliest attempted stage that failed, including raised errors.

    Exceptions raised before a typed stage outcome is returned leave no value
    in ``result.values``. Their timestamp is still present in ``stage_times``;
    using it prevents cleanup from being recorded after the later cleanup
    high-water mark and then rejecting the earlier cleanup-start event.
    """
    candidates = ("protect", "claim", "create", "prepare", "stabilize", "measure", "close", "retrieve")
    failure = getattr(result, "primary_failure", None)
    if isinstance(failure, str) and " failed (" in failure:
        name = failure.split(" ", 1)[-1].split(" failed (", 1)[0]
        if name in candidates and name in stage_times:
            return name
    for name in candidates:
        if name not in stage_times:
            continue
        outcome = _outcome(result, name)
        if outcome is not None and not outcome.succeeded:
            return name
    return None


def _guest_from(result: Any) -> Any:
    measured = _outcome(result, "measure")
    evidence = getattr(measured, "evidence", None)
    return getattr(evidence, "outcome", None)


def _guest_execution_from(result: Any) -> Any:
    measured = _outcome(result, "measure")
    return getattr(measured, "evidence", None)


def _records_from(result: Any) -> list[Any]:
    retrieved = _outcome(result, "retrieve")
    evidence = getattr(retrieved, "evidence", None)
    return list(getattr(evidence, "records", ()) or ())


def _merge_artifact_records(records: list[Any]) -> list[Any]:
    """Collapse identical artifact identities while rejecting conflicting captures."""
    merged = {}
    for record in records:
        key = (record.artifact_id, record.path, record.unit_id)
        if key in merged and merged[key] != record:
            raise ValueError("duplicate source records conflict during strict projection")
        merged[key] = record
    return list(merged.values())


def _builds_from(result: Any) -> list[Any]:
    prepared = _outcome(result, "prepare")
    evidence = getattr(prepared, "evidence", None)
    bootstrap = evidence.get("bootstrap") if isinstance(evidence, dict) else None
    return list(getattr(bootstrap, "builds", ()) or ())


def _preparation_failure(result: Any) -> tuple[Any, dict[str, Any]] | tuple[None, None]:
    """Return bounded, already-redacted bootstrap/readiness failure evidence."""
    prepared = _outcome(result, "prepare")
    evidence = getattr(prepared, "evidence", None)
    if not isinstance(evidence, dict):
        return None, None
    failure = None
    for name in ("bootstrap", "readiness"):
        observed = getattr(evidence.get(name), "failure", None)
        if observed is not None:
            failure = observed
            break
    if failure is None:
        return None, None
    actions = []
    def bounded_diagnostic(value: Any) -> Any:
        """Keep short output byte-for-byte and retain the useful end of long output."""
        if not isinstance(value, str) or len(value) <= 4096:
            return value
        marker = "\n...[truncated; retained final diagnostic characters]...\n"
        return marker + value[-(4096 - len(marker)):]

    for name in ("bootstrap", "readiness"):
        for action in getattr(evidence.get(name), "actions", ()) or ():
            row = action.model_dump(mode="json") if hasattr(action, "model_dump") else {}
            request, response = row.get("request", {}), row.get("result", {})
            argv = request.get("argv")
            safe_argv = ([str(value)[:256] for value in argv[:32]
                          if isinstance(value, (str, int, float, bool))]
                         if isinstance(argv, list) else None)
            # Remote action evidence is redacted by the access runner. Keep a
            # small bounded diagnostic view and omit arbitrary nested payloads.
            actions.append({"action_id": str(row.get("action_id", ""))[:256],
                "action_type": str(row.get("action_type", ""))[:128],
                "vm_id": str(row.get("vm_id", ""))[:256], "role": str(row.get("role", ""))[:32],
                "request": {**{k: (str(request[k])[:256] if isinstance(request[k], str) else request[k])
                    for k in ("action_id", "action_type", "timeout_seconds") if k in request},
                    **({"argv": safe_argv} if safe_argv is not None else {})},
                "result": {k: (bounded_diagnostic(response[k]) if k in {"stdout", "stderr"} else response[k])
                    for k in ("outcome", "exit_code", "started_at", "finished_at", "stdout", "stderr", "redactions_applied")
                    if k in response}})
    omitted = max(0, len(actions) - 24)
    actions = actions[-24:]
    return failure, {"schema_version": 1, "stage": str(failure.stage)[:64], "category": str(failure.category)[:256],
        "message": str(failure.message)[:1000], "action_id": str(failure.action_id)[:256] if failure.action_id else None,
        "occurred_at": failure.occurred_at.isoformat(), "omitted_action_count": omitted, "actions": actions}


def _preparation_failure_declaration(result: Any) -> Any | None:
    """Return the fixed artifact declaration when failed preparation evidence exists."""
    _failure, payload = _preparation_failure(result)
    if payload is None:
        return None
    from .contracts.artifacts import ArtifactInput
    return ArtifactInput(artifact_id="f09-preparation-failure",
        path="metadata/preparation-failure.json", kind="lifecycle_failure_diagnostics",
        producer="controller", unit_id=None, expectation="required")


def _bounded_failure_text(value: Any, *, limit: int = 4096) -> str:
    import re
    text = str(value)
    text = re.sub(r"-----BEGIN [^-]+-----.*?-----END [^-]+-----", "[REDACTED_KEY]", text, flags=re.S)
    text = re.sub(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", r"\1 [REDACTED]", text)
    text = re.sub(r"(?i)(--?(?:token|password|secret|secret[-_]?key|client[-_]?secret|session[-_]?token|authorization|api[-_]?key|access[-_]?(?:token|key))(?:=|\s+))([^\s,;]+)",
        r"\1[REDACTED]", text)
    text = re.sub(r"(?i)([\"']?(?:password|token|secret|secret[-_ ]?key|client[-_ ]?secret|session[-_ ]?token|access[-_ ]?(?:token|key)|private[_ -]?key|authorization)[\"']?\s*[:=]\s*[\"']?)[^\"'\s,;}]+",
        r"\1[REDACTED]", text)
    if len(text) > limit:
        marker = "\n...[truncated; retained final diagnostic characters]...\n"
        text = marker + text[-(limit - len(marker)):]
    return text


def _remote_action_diagnostic(action: Any) -> dict[str, Any]:
    row = action.model_dump(mode="json") if hasattr(action, "model_dump") else action
    if not isinstance(row, Mapping):
        return {}
    request, response = row.get("request", {}), row.get("result", {})
    request = request if isinstance(request, Mapping) else {}
    response = response if isinstance(response, Mapping) else {}
    argv = request.get("argv")
    safe_argv = None
    if isinstance(argv, list):
        safe_argv, redact_next = [], False
        sensitive_flags = {"--token", "--password", "--secret", "--secret-key", "--secret_key",
            "--client-secret", "--client_secret", "--session-token", "--session_token",
            "--authorization", "--api-key", "--api_key", "--access-token", "--access_token",
            "--access-key", "--access_key"}
        for value in argv[:32]:
            if not isinstance(value, (str, int, float, bool)):
                continue
            rendered = str(value)
            if redact_next:
                safe_argv.append("[REDACTED]")
                redact_next = False
            else:
                safe_argv.append(_bounded_failure_text(rendered, limit=256))
                redact_next = rendered.lower() in sensitive_flags
    return {"action_id": _bounded_failure_text(row.get("action_id", ""), limit=256),
        "action_type": _bounded_failure_text(row.get("action_type", ""), limit=128),
        "vm_id": _bounded_failure_text(row.get("vm_id", ""), limit=256),
        "role": _bounded_failure_text(row.get("role", ""), limit=32),
        "request": {key: (_bounded_failure_text(request[key], limit=256)
                           if isinstance(request[key], str) else request[key])
                    for key in ("action_id", "action_type", "timeout_seconds") if key in request}
            | ({"argv": safe_argv} if safe_argv is not None else {}),
        "result": {key: (_bounded_failure_text(response[key], limit=2048 if key in {"stdout", "stderr"} else 256)
                          if isinstance(response[key], str) else response[key])
                   for key in ("outcome", "exit_code", "started_at", "finished_at",
                          "duration_seconds", "stdout", "stderr", "redactions_applied") if key in response}}


def _measurement_failure(result: Any) -> tuple[Any, dict[str, Any]] | tuple[None, None]:
    """Capture bounded controller diagnostics for failed measure/close/retrieve stages."""
    failed = _failed_stage(result, getattr(result, "stage_times", {}))
    if failed not in {"measure", "close", "retrieve"}:
        return None, None

    def actions(value: Any) -> list[Any]:
        candidates = list(getattr(value, "actions", ()) or ())
        if isinstance(value, Mapping):
            candidates.extend(value.get("actions", ()) or ())
        diagnostics = getattr(value, "diagnostics", None)
        if isinstance(diagnostics, Mapping):
            for process in diagnostics.values():
                candidates.extend(getattr(process, "actions", ()) or ())
        closures = getattr(value, "closures", None)
        if isinstance(closures, Mapping):
            for closure in closures.values():
                candidates.extend(getattr(closure, "actions", ()) or ())
        listeners = getattr(value, "listener_closure", None)
        candidates.extend(getattr(listeners, "actions", ()) or ())
        seen = set()
        selected = []
        for action in candidates:
            row = action.model_dump(mode="json") if hasattr(action, "model_dump") else action
            key = (row.get("action_id"), row.get("role")) if isinstance(row, Mapping) else None
            if key is not None and key in seen:
                continue
            if key is not None:
                seen.add(key)
            selected.append(_remote_action_diagnostic(action))
        return selected[-12:]

    def failures(value: Any) -> list[str]:
        observed = getattr(value, "failures", ()) or ()
        output = []
        for failure in list(observed)[-16:]:
            if isinstance(failure, Mapping):
                fields = [failure[key] for key in ("category", "message") if isinstance(failure.get(key), str)]
                text = ": ".join(fields)
            else:
                text = failure if isinstance(failure, str) else ""
            if text:
                output.append(_bounded_failure_text(text, limit=1000))
        return output

    stages = []
    for name in ("measure", "close", "retrieve"):
        outcome = _outcome(result, name)
        if outcome is None:
            continue
        evidence = getattr(outcome, "evidence", None)
        row: dict[str, Any] = {"stage": name, "succeeded": bool(outcome.succeeded)}
        if isinstance(evidence, Mapping):
            failure = evidence.get("failure")
            if failure is not None:
                row["failure"] = _bounded_failure_text(failure, limit=1000)
            if evidence.get("failure_type") is not None:
                row["failure_type"] = _bounded_failure_text(evidence["failure_type"], limit=128)
            if evidence.get("startup_failure") is not None:
                row["startup_failure"] = _bounded_failure_text(evidence["startup_failure"], limit=1000)
            row["actions"] = actions(evidence)
            diagnostics = evidence.get("diagnostics")
            if isinstance(diagnostics, Mapping):
                observed_failures = []
                for process in list(diagnostics.values())[:2]:
                    row["actions"].extend(actions(process))
                    observed_failures.extend(failures(process))
                row["actions"] = row["actions"][-12:]
                if observed_failures:
                    row["failures"] = observed_failures[-16:]
            listeners = evidence.get("listeners")
            if listeners is not None:
                row["actions"].extend(actions(listeners))
                row["actions"] = row["actions"][-12:]
                row["failures"] = [*row.get("failures", []), *failures(listeners)][-16:]
            inventories = evidence.get("inventories")
            if isinstance(inventories, Mapping):
                observed_failures = []
                for inventory in list(inventories.values())[:2]:
                    row["actions"].extend(actions(inventory))
                    observed_failures.extend(failures(inventory))
                row["actions"] = row["actions"][-12:]
                row["failures"] = [*row.get("failures", []), *observed_failures][-16:]
        else:
            if getattr(evidence, "failure", None) is not None:
                row["failure"] = _bounded_failure_text(evidence.failure, limit=1000)
            guest = getattr(evidence, "outcome", None)
            if guest is not None:
                row["guest_outcome"] = {"outcome": _bounded_failure_text(getattr(guest, "outcome", ""), limit=64),
                    "executed_unit_ids": list(getattr(guest, "executed_unit_ids", ()) or ())[:32],
                    "unexecuted_unit_ids": list(getattr(guest, "unexecuted_unit_ids", ()) or ())[:32],
                    "guest_started_at": str(getattr(guest, "guest_started_at", "")),
                    "guest_finished_at": str(getattr(guest, "guest_finished_at", "")),
                    "failure_count": len(getattr(guest, "failures", ()) or ())}
                guest_failures = failures(guest)
                if guest_failures:
                    row["guest_failures"] = guest_failures
            row["actions"] = actions(evidence)
            observed_failures = failures(evidence)
            if observed_failures:
                row["failures"] = observed_failures
        stages.append(row)
    if not stages:
        return None, None
    return failed, {"schema_version": 1, "stage": failed, "stages": stages}


def _measurement_failure_declaration(result: Any) -> Any | None:
    _failed, payload = _measurement_failure(result)
    if payload is None:
        return None
    from .contracts.artifacts import ArtifactInput
    return ArtifactInput(artifact_id="f09-measurement-failure",
        path="metadata/measurement-failure.json", kind="lifecycle_failure_diagnostics",
        producer="controller", unit_id=None, expectation="required")


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


def _created_deployment(child: Any, result: Any) -> Any:
    """Resolve only supported, child-bound provider outputs for control proof."""
    from .azure_provider import AzureProviderResult
    from .contracts.deployment import ProviderDeploymentOutput

    value = getattr(result, "values", {}).get("create")
    if isinstance(value, AzureProviderResult):
        if value.failures or _value(child.provider) != "azure":
            return None
        value = value.deployment
    if not isinstance(value, ProviderDeploymentOutput):
        return None
    for identity in (value, value.identity):
        if (identity.run_id != child.run_id
                or identity.deployment_id != child.deployment_id
                or _value(identity.provider) != _value(child.provider)):
            return None
    if (value.vm_a is None or value.vm_b is None
            or _value(value.vm_a.role) != "vm_a" or _value(value.vm_b.role) != "vm_b"):
        return None
    return value


def _materialize_controller_evidence(root: Path, child: Any, result: Any,
                                     declarations: list[Any], *,
                                     source_records: list[Any] | None = None) -> list[Any]:
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

    control_declaration = by_id.get("iperf3_control_evidence")
    if control_declaration is not None:
        prepared = getattr(_outcome(result, "prepare"), "evidence", None)
        measured = getattr(_outcome(result, "measure"), "evidence", None)
        close = getattr(_outcome(result, "close"), "evidence", None)
        deployment = _created_deployment(child, result)
        readiness = prepared.get("readiness") if isinstance(prepared, Mapping) else None
        bootstrap = prepared.get("bootstrap") if isinstance(prepared, Mapping) else None
        listeners = close.get("listeners") if isinstance(close, Mapping) else None
        vm_a = getattr(deployment, "vm_a", None)
        vm_b = getattr(deployment, "vm_b", None)
        vm_a_host = getattr(getattr(vm_a, "connection", None), "host", None)
        vm_b_host = getattr(getattr(vm_b, "connection", None), "host", None)
        private_ipv4_b = getattr(vm_b, "private_ipv4", None)
        declared_listener = next((item for item in (getattr(bootstrap, "listeners", ()) or ())
            if getattr(item, "tool", None) == "iperf3"), None)

        def action_row(action: Any) -> dict[str, Any]:
            row = action.model_dump(mode="json") if hasattr(action, "model_dump") else dict(action)
            request, response = row.get("request", {}), row.get("result", {})
            raw_argv = request.get("argv", []) if isinstance(request, Mapping) else []
            remote_command = (str(raw_argv[-1])[:8192]
                if isinstance(raw_argv, list) and raw_argv and str(raw_argv[0]).endswith("ssh") else None)
            # Keep only bounded, redacted command metadata; never copy arbitrary output.
            argv = [str(part)[:512] for part in raw_argv[:32]] if isinstance(raw_argv, list) else []
            return {"action_id": str(row.get("action_id", ""))[:256],
                "action_type": str(row.get("action_type", ""))[:64],
                "vm_id": str(row.get("vm_id", ""))[:256],
                "role": str(row.get("role", ""))[:16], "argv": argv,
                "outcome": str(response.get("outcome", ""))[:32],
                "exit_code": response.get("exit_code"),
                "started_at": response.get("started_at"),
                "finished_at": response.get("finished_at"),
                "remote_command": remote_command}

        def action_id(item: Any) -> str:
            return str(item.get("action_id", "") if isinstance(item, Mapping)
                else getattr(item, "action_id", ""))
        ready_actions = [action_row(item) for item in (getattr(readiness, "actions", ()) or ())
            if "private-tcp" in action_id(item)]
        listener_actions = [action_row(item) for item in (getattr(listeners, "actions", ()) or ())
            if "iperf3" in action_id(item)]
        measure_actions = [action_row(item) for item in (getattr(measured, "actions", ()) or ())
            if "guest-run" in action_id(item) or "guest-outcome" in action_id(item)]
        pre_measure = (getattr(measured, "iperf3_control_pre_measure", None)
            if not isinstance(measured, Mapping) else measured.get("iperf3_control_pre_measure"))
        pre_measure = pre_measure if isinstance(pre_measure, Mapping) else {}
        pre_measure_snapshot = pre_measure.get("snapshot") if isinstance(pre_measure.get("snapshot"), Mapping) else None
        pre_measure_action = pre_measure.get("action")
        source_build = next((item for item in (getattr(bootstrap, "builds", ()) or ())
            if getattr(item, "role", None) == "vm_b" and getattr(item, "tool", None) == "iperf3"), None)
        observations = getattr(listeners, "observations", {}) or {}
        iperf_observation = observations.get("iperf3") if isinstance(observations, Mapping) else None
        evidence = {"schema_version": 1, "run_id": child.run_id,
            "deployment_id": child.deployment_id, "vm_role": "vm_b",
            "vm_a_host_id": vm_a_host, "vm_b_host_id": vm_b_host,
            "private_ipv4_b": private_ipv4_b,
            "iperf3_stdout_path": getattr(declared_listener, "stdout_path", None),
            "iperf3_stderr_path": getattr(declared_listener, "stderr_path", None),
            "readiness": {"ready": getattr(readiness, "ready", None), "actions": ready_actions,
                "deployment_id": getattr(bootstrap, "deployment_id", None),
                "event_association": "unavailable: native log boundary offsets were not captured"},
            "controller_session_id": (pre_measure_snapshot.get("controller_session_id")
                if pre_measure_snapshot else None),
            "source": ({"tool": "iperf3", "binary_version": source_build.binary_version,
                "source_reference": source_build.source_reference,
                "source_sha256": source_build.source_sha256,
                "binary_sha256": source_build.binary_sha256,
                "upstream_commit": source_build.upstream_commit,
                "patch_path": source_build.patch_path,
                "patch_sha256": source_build.patch_sha256,
                "observation_semantics": source_build.observation_semantics,
                "observation_capability": source_build.observation_capability}
                if source_build is not None else None),
            "pre_measure": {"available": pre_measure.get("available") is True,
                "snapshot": pre_measure_snapshot,
                "action": action_row(pre_measure_action) if pre_measure_action is not None else None},
            "measure_actions": measure_actions,
            "listener": {"closed": getattr(listeners, "closed", None),
                "closed_at": getattr(listeners, "closed_at", None).isoformat()
                    if getattr(listeners, "closed_at", None) else None,
                "measurement_ended_at": (close.get("measurement_ended_at").isoformat()
                    if isinstance(close, Mapping) and isinstance(close.get("measurement_ended_at"), datetime) else None),
                "failures": list(getattr(listeners, "failures", ()) or ()),
                "iperf3_owner_and_log_identity": iperf_observation,
                "pre_stop_prefix": (iperf_observation.get("pre_stop_prefix")
                    if isinstance(iperf_observation, Mapping) else None),
                "actions": listener_actions}}
        association_records = _merge_artifact_records(list(child.artifact_records)
            + (list(source_records) if source_records is not None else _records_from(result)))
        association, _ = _iperf3_control_event_association(root,
            association_records, evidence,
            vm_a_host_id=vm_a_host, vm_b_host_id=vm_b_host,
            private_ipv4_b=private_ipv4_b, stdout_path=evidence["iperf3_stdout_path"])
        evidence["readiness"]["event_association"] = association
        payload = (json.dumps(evidence, sort_keys=True, indent=2, default=str) + "\n").encode()
        record = _controller_record(root, child.deployment_id, control_declaration, payload)
        if record is not None:
            records.append(record)

    guest = _guest_from(result)
    remote = _guest_execution_from(result)
    source_bytes = getattr(remote, "outcome_source_bytes", None)
    source_declaration = by_id.get("guest_outcome_source")
    if source_bytes is not None and source_declaration is not None:
        record = _controller_record(root, child.deployment_id, source_declaration, source_bytes)
        if record is not None:
            records.append(record)
    commands = [CommandEvidence.model_validate(item) for item in
        (getattr(guest, "commands", ()) or ())]
    commands.extend(CommandEvidence.model_validate(item) for item in
        (getattr(remote, "receiver_command_evidence", ()) or ()))
    timings = [MeasurementTiming.model_validate(item) for item in
        (getattr(guest, "timings", ()) or ())]
    # DAY chunk identities are recovered from the actual guest command list.
    # Materialize their controller-owned command/timing records for strict
    # ValidatorInput replay; `_sync_manifest` separately keeps the child
    # manifest's parent-only projection.
    units = _day_units(child, guest)
    for unit in units:
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


def _verified_iperf3_control_evidence(root: Path, child: Any, records: list[Any], *,
        vm_a_host_id: str | None, vm_b_host_id: str | None, private_ipv4_b: str | None,
        stdout_path: str | None, stderr_path: str | None) -> dict[str, Any] | None:
    """Return only identity-bound, checksum-verified next-capture control evidence."""
    record = next((item for item in records if item.artifact_id == "iperf3_control_evidence"), None)
    if (record is None or record.producer != "controller" or record.unit_id is not None
            or record.path != "metadata/iperf3-control-evidence.json"
            or record.kind != "iperf3-control-evidence"
            or record.deployment_id != child.deployment_id):
        return None
    try:
        record.verify_materialized_file(root)
        payload = json.loads((root / record.path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    if (not isinstance(payload, dict) or payload.get("schema_version") != 1
            or payload.get("run_id") != child.run_id
            or payload.get("deployment_id") != child.deployment_id
            or payload.get("vm_role") != "vm_b"):
        return None
    expected = {"vm_a_host_id": vm_a_host_id, "vm_b_host_id": vm_b_host_id,
        "private_ipv4_b": private_ipv4_b, "iperf3_stdout_path": stdout_path,
        "iperf3_stderr_path": stderr_path}
    if any(value is None or payload.get(key) != value for key, value in expected.items()):
        return None
    readiness, listener = payload.get("readiness"), payload.get("listener")
    if (not isinstance(readiness, dict) or readiness.get("ready") is not True
            or readiness.get("deployment_id") != child.deployment_id
            or not isinstance(readiness.get("actions"), list)):
        return None
    ready = [item for item in readiness["actions"] if isinstance(item, dict)
        and item.get("action_id", "").startswith("f02r-ready-private-tcp-")
        and item.get("role") == "vm_a" and item.get("vm_id") == vm_a_host_id
        and item.get("action_type") == "ssh"
        and item.get("outcome") == "succeeded"
        and item.get("exit_code") == 0
        and isinstance(item.get("remote_command"), str)
        and item["remote_command"] == f"nc -z -w 1 {private_ipv4_b} 5201"
        and isinstance(item.get("started_at"), str) and isinstance(item.get("finished_at"), str)
        and _ordered_utc(item["started_at"], item["finished_at"])]
    owner = listener.get("iperf3_owner_and_log_identity") if isinstance(listener, dict) else None
    close_actions = listener.get("actions") if isinstance(listener, dict) else None
    required_actions = {f"f09-listener-stop-{child.deployment_id}-iperf3",
        f"f09-listener-owner-process-{child.deployment_id}-iperf3",
        f"f09-listener-closed-files-{child.deployment_id}-iperf3"}
    successful_rows = {item.get("action_id"): item for item in close_actions if isinstance(item, dict)
        and item.get("role") == "vm_b" and item.get("vm_id") == vm_b_host_id
        and item.get("action_type") == "ssh"
        and item.get("outcome") == "succeeded" and item.get("exit_code") == 0} if isinstance(close_actions, list) else {}
    owner_row = successful_rows.get(f"f09-listener-owner-process-{child.deployment_id}-iperf3", {})
    close_row = successful_rows.get(f"f09-listener-closed-files-{child.deployment_id}-iperf3", {})
    stop_row = successful_rows.get(f"f09-listener-stop-{child.deployment_id}-iperf3", {})
    closed_at = listener.get("closed_at") if isinstance(listener, dict) else None
    measurement_ended_at = listener.get("measurement_ended_at") if isinstance(listener, dict) else None
    if (len(ready) != 1 or not isinstance(listener, dict) or listener.get("closed") is not True
            or listener.get("failures") != [] or not isinstance(owner, dict)
            or not all(key in owner for key in ("pid", "start_ticks", "boot_id", "cgroup", "log_ids", "closed_at_utc"))
            or not required_actions.issubset(successful_rows)
            or stop_row.get("remote_command") != f"sudo systemctl stop cnb-{child.deployment_id}-iperf3"
            or not isinstance(closed_at, str) or not isinstance(measurement_ended_at, str)
            or not _ordered_utc(stop_row.get("started_at"), stop_row.get("finished_at"))
            or not _ordered_utc(owner.get("closed_at_utc"), closed_at)
            or any(path not in str(row.get("remote_command", ""))
                for row in (owner_row, close_row) for path in (stdout_path, stderr_path))
            or not _valid_owned_log_identity(owner, child.deployment_id)):
        return None
    association, _ = _iperf3_control_event_association(root, records, payload,
        vm_a_host_id=vm_a_host_id, vm_b_host_id=vm_b_host_id,
        private_ipv4_b=private_ipv4_b, stdout_path=stdout_path)
    if readiness.get("event_association") != association:
        return None
    return payload


def _ordered_utc(start: str, finish: str) -> bool:
    if not isinstance(start, str) or not isinstance(finish, str):
        return False
    try:
        left, right = datetime.fromisoformat(start.replace("Z", "+00:00")), datetime.fromisoformat(finish.replace("Z", "+00:00"))
        return left.tzinfo is not None and right.tzinfo is not None and left.utcoffset() is not None and right.utcoffset() is not None and right >= left
    except (TypeError, ValueError):
        return False


def _valid_owned_log_identity(owner: Mapping[str, Any], deployment_id: str) -> bool:
    unit = f"cnb-{deployment_id}-iperf3.service"
    ids = owner.get("log_ids")
    return (isinstance(owner.get("pid"), int) and not isinstance(owner.get("pid"), bool) and owner["pid"] > 0
        and isinstance(owner.get("start_ticks"), int) and not isinstance(owner.get("start_ticks"), bool)
        and owner["start_ticks"] >= 0 and isinstance(owner.get("boot_id"), str)
        and re.fullmatch(r"[0-9a-fA-F-]{36}", owner["boot_id"]) is not None
        and isinstance(owner.get("cgroup"), str) and owner["cgroup"].rstrip("/").endswith("/" + unit)
        and isinstance(ids, list) and len(ids) == 2 and all(isinstance(row, list) and len(row) == 2
            and all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in row)
            for row in ids) and ids[0] != ids[1]
        and isinstance(owner.get("closed_at_utc"), str))


def _iperf3_control_event_association(root: Path, records: list[Any], payload: Mapping[str, Any],
        *, vm_a_host_id: str | None = None, vm_b_host_id: str | None = None,
        private_ipv4_b: str | None = None, stdout_path: str | None = None
        ) -> tuple[dict[str, Any], set[tuple[int, str]]]:
    """Replay exact listener-prefix snapshots against the closed raw server log."""
    unavailable = lambda: ({"status": "unavailable",
        "reason": "closed-log, owner, source, or boundary proof is incomplete"}, set())
    try:
        ready = payload["readiness"]
        listener = payload["listener"]
        pre_measure = payload["pre_measure"]
        pre = pre_measure["snapshot"]
        owner = listener["iperf3_owner_and_log_identity"]
        post = listener["pre_stop_prefix"]
        session = payload["controller_session_id"]
        deployment = payload["deployment_id"]
        source = payload["source"]
        if (pre_measure.get("available") is not True or not isinstance(pre, Mapping)
                or not isinstance(post, Mapping) or post.get("available") is not True
                or not isinstance(owner, Mapping) or not isinstance(source, Mapping)
                or ready.get("ready") is not True or ready.get("deployment_id") != deployment
                or listener.get("closed") is not True or listener.get("failures") != []
                or not _valid_owned_log_identity(owner, deployment)
                or not isinstance(listener.get("closed_at"), str)
                or not _ordered_utc(owner.get("closed_at_utc"), listener.get("closed_at"))
                or not isinstance(session, str) or not session or not deployment):
            return unavailable()
        if (source.get("tool") != "iperf3"
                or source.get("observation_capability") != "CNB_ACQ_V1"
                or source.get("observation_semantics") != "iperf-3.17.1-stats-v1"):
            return unavailable()
        fields = ("deployment_id", "controller_session_id", "listener_invocation_id", "unit",
            "pid", "start_ticks", "boot_id", "cgroup", "log_path", "log_ids")
        if any(pre.get(key) != post.get(key) for key in fields):
            return unavailable()
        if (pre.get("deployment_id") != deployment or pre.get("controller_session_id") != session
                or pre.get("listener_invocation_id") != f"f02r-vm_b-listener-iperf3"
                or pre.get("unit") != f"cnb-{deployment}-iperf3"
                or pre.get("log_path") != stdout_path or pre.get("log_path") != post.get("log_path")
                or (vm_a_host_id is not None and payload.get("vm_a_host_id") != vm_a_host_id)
                or (vm_b_host_id is not None and payload.get("vm_b_host_id") != vm_b_host_id)
                or (private_ipv4_b is not None and payload.get("private_ipv4_b") != private_ipv4_b)):
            return unavailable()
        for snap in (pre, post):
            count, digest = snap.get("prefix_bytes"), snap.get("prefix_sha256")
            ids = snap.get("log_ids")
            if (isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 65536
                    or not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None
                    or not isinstance(ids, list) or len(ids) != 2):
                return unavailable()
            if any(not isinstance(pair, list) or len(pair) != 2
                    or any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in pair)
                    for pair in ids):
                return unavailable()
        if pre["prefix_bytes"] > post["prefix_bytes"]:
            return unavailable()
        if (owner.get("pid") != pre.get("pid") or owner.get("start_ticks") != pre.get("start_ticks")
                or owner.get("boot_id") != pre.get("boot_id") or owner.get("cgroup") != pre.get("cgroup")
                or owner.get("log_ids") != pre.get("log_ids")):
            return unavailable()

        log_rows = [item for item in records if item.artifact_id == "iperf3_server_log"]
        build_rows = [item for item in records if item.artifact_id == "f02r-vm_b-iperf3-build"]
        acquisition_rows = [item for item in records if item.artifact_id == "iperf3_server_error"]
        if len(log_rows) != 1 or len(build_rows) != 1 or len(acquisition_rows) != 1:
            return unavailable()
        log_record, build_record, acquisition_record = log_rows[0], build_rows[0], acquisition_rows[0]
        try:
            expected_relative_log_path = str(PurePosixPath(stdout_path).relative_to("/var/lib/cnb/results"))
            expected_relative_error_path = str(PurePosixPath(payload["iperf3_stderr_path"]).relative_to("/var/lib/cnb/results"))
        except (TypeError, ValueError):
            return unavailable()
        if (log_record.path != expected_relative_log_path or log_record.producer != "vm_b"
                or log_record.kind != "iperf3-server-jsonl" or log_record.unit_id is not None
                or log_record.retrieval_state != "verified" or log_record.closed is not True
                or acquisition_record.path != expected_relative_error_path or acquisition_record.producer != "vm_b"
                or acquisition_record.kind != "iperf3-observation-stderr" or acquisition_record.unit_id is not None
                or acquisition_record.retrieval_state != "verified" or acquisition_record.closed is not True
                or build_record.producer != "vm_b" or build_record.kind != "build-identity"
                or build_record.retrieval_state != "verified" or build_record.closed is not True):
            return unavailable()
        log_record.verify_materialized_file(root)
        build_record.verify_materialized_file(root)
        acquisition_record.verify_materialized_file(root)
        raw = resolve_artifact_path(root, log_record.path).read_bytes()
        acquisition_raw = resolve_artifact_path(root, acquisition_record.path).read_bytes()
        from .acquisition import _parse_build_identity
        build = _parse_build_identity(resolve_artifact_path(root, build_record.path).read_bytes())
        source_fields = ("source_reference", "source_sha256", "upstream_commit", "patch_path",
            "patch_sha256", "observation_capability", "observation_semantics")
        if any(source.get(key) != build.get(key) for key in source_fields):
            return unavailable()
        from .acquisition import _canonical_version_text, _expected_build_identity
        pinned_commit, pinned_patch_path, pinned_patch_sha256, pinned_source_sha256 = _expected_build_identity("iperf3")
        expected_reference = f"iperf-{pinned_commit}"
        version = build.get("binary_version")
        if (build.get("tool") != "iperf3" or build.get("upstream_commit") != pinned_commit
                or build.get("source_reference") != expected_reference
                or build.get("source_sha256") != pinned_source_sha256
                or build.get("patch_path") != pinned_patch_path
                or build.get("patch_sha256") != pinned_patch_sha256
                or not isinstance(version, str)
                or re.match(r"^iperf 3\.17\.1(?:\s|\()", _canonical_version_text(version)) is None
                or _canonical_version_text(source.get("binary_version", "")) != _canonical_version_text(version)
                or source.get("binary_sha256") != build.get("binary_sha256")
                or build.get("observation_semantics") != "iperf-3.17.1-stats-v1"):
            return unavailable()
        source_pids: set[int] = set()
        source_boot_ids: set[str] = set()
        for line in acquisition_raw.splitlines():
            if not line.startswith(b"CNB_ACQ_V1 "):
                continue
            try:
                row = json.loads(line[len(b"CNB_ACQ_V1 "):])
            except (UnicodeDecodeError, json.JSONDecodeError):
                return unavailable()
            if (not isinstance(row, Mapping) or row.get("tool") != "iperf3"
                    or row.get("semantics_id") != "iperf-3.17.1-stats-v1"
                    or row.get("invocation_id") != "f02r-vm_b-listener-iperf3"
                    or isinstance(row.get("pid"), bool) or not isinstance(row.get("pid"), int)
                    or not isinstance(row.get("boot_id"), str)):
                return unavailable()
            source_pids.add(row["pid"])
            source_boot_ids.add(row["boot_id"])
        if (not source_pids or source_pids != {pre["pid"]}
                or source_boot_ids != {pre["boot_id"]}):
            return unavailable()
        for snap in (pre, post):
            size = snap["prefix_bytes"]
            prefix = raw[:size]
            if (len(prefix) != size or (prefix and not prefix.endswith(b"\n"))
                    or hashlib.sha256(prefix).hexdigest() != snap["prefix_sha256"]):
                return unavailable()
        if post["prefix_bytes"] > len(raw):
            return unavailable()

        by_line: list[tuple[int, int, int, Mapping[str, Any]]] = []
        offset = 0
        for line_number, line in enumerate(raw.splitlines(keepends=True), 1):
            end = offset + len(line)
            try:
                item = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                item = None
            if isinstance(item, Mapping):
                by_line.append((line_number, offset, end, item))
            offset = end
        def pairs(message: str) -> list[tuple[int, int, int, int]]:
            found = []
            for index, row in enumerate(by_line[:-1]):
                line, start, _end, event = row
                next_line, _next_start, pair_end, ending = by_line[index + 1]
                if (next_line == line + 1 and event.get("event") == "error"
                        and event.get("data") == message and ending.get("event") == "end"
                        and ending.get("data") == {}):
                    found.append((line, start, next_line, pair_end))
            return found
        readiness_pairs = pairs("error - unable to receive cookie at server: ")
        stop_pairs = pairs("interrupt - the server has terminated")
        error_counts = {message: sum(item.get("event") == "error" and item.get("data") == message
            for _line, _start, _end, item in by_line)
            for message in ("error - unable to receive cookie at server: ",
                "interrupt - the server has terminated")}
        empty_end_count = sum(item.get("event") == "end" and item.get("data") == {}
            for _line, _start, _end, item in by_line)
        if (len(readiness_pairs) != 1 or len(stop_pairs) != 1
                or any(count != 1 for count in error_counts.values()) or empty_end_count != 2
                or readiness_pairs[0][3] > pre["prefix_bytes"]
                or stop_pairs[0][1] < post["prefix_bytes"]):
            return unavailable()

        pre_action = pre_measure.get("action")
        measure_actions = payload.get("measure_actions")
        dispatch = [item for item in measure_actions if isinstance(item, Mapping)
            and item.get("action_id", "").startswith("f09-guest-run-")]
        measurement_receipts = [item for item in measure_actions if isinstance(item, Mapping)
            and item.get("action_id", "").startswith("f09-guest-outcome-")]
        ready_actions = [item for item in ready.get("actions", ()) if isinstance(item, Mapping)
            and item.get("action_id", "").startswith("f02r-ready-private-tcp-")
            and item.get("role") == "vm_a" and item.get("vm_id") == payload.get("vm_a_host_id")
            and item.get("action_type") == "ssh" and item.get("outcome") == "succeeded"
            and item.get("exit_code") == 0
            and item.get("remote_command") == f"nc -z -w 1 {payload.get('private_ipv4_b')} 5201"]
        close_actions = listener.get("actions", ())
        action_map = {item.get("action_id"): item for item in close_actions if isinstance(item, Mapping)}
        ready_row = ready_actions[0] if len(ready_actions) == 1 else {}
        stop_row = action_map.get(f"f09-listener-stop-{deployment}-iperf3", {})
        owner_row = action_map.get(f"f09-listener-owner-process-{deployment}-iperf3", {})
        closed_row = action_map.get(f"f09-listener-closed-files-{deployment}-iperf3", {})
        snapshot_action = pre_action if isinstance(pre_action, Mapping) else {}
        owner_close_command = str(closed_row.get("remote_command", ""))
        same_owner_tokens = [str(pre["pid"]), str(pre["start_ticks"]), str(pre["boot_id"]),
            str(pre["cgroup"]), *(str(value) for pair in pre["log_ids"] for value in pair),
            str(pre["log_path"]), str(payload.get("iperf3_stderr_path"))]
        if (len(dispatch) != 1 or len(measurement_receipts) != 1 or len(ready_actions) != 1
                or snapshot_action.get("action_id") != f"f09-iperf3-control-prefix-measure-{deployment}"
                or snapshot_action.get("role") != "vm_b" or snapshot_action.get("vm_id") != payload.get("vm_b_host_id")
                or snapshot_action.get("outcome") != "succeeded" or snapshot_action.get("exit_code") != 0
                or snapshot_action.get("action_type") != "ssh"
                or payload.get("iperf3_stdout_path") not in str(snapshot_action.get("remote_command", ""))
                or payload.get("iperf3_stderr_path") not in str(snapshot_action.get("remote_command", ""))
                or str(pre.get("controller_session_id")) not in str(snapshot_action.get("remote_command", ""))
                or str(pre.get("unit")) not in str(snapshot_action.get("remote_command", ""))
                or dispatch[0].get("role") != "vm_a" or dispatch[0].get("vm_id") != payload.get("vm_a_host_id")
                or dispatch[0].get("action_type") != "ssh" or dispatch[0].get("outcome") != "succeeded"
                or dispatch[0].get("exit_code") != 0
                or measurement_receipts[0].get("role") != "vm_a"
                or measurement_receipts[0].get("vm_id") != payload.get("vm_a_host_id")
                or measurement_receipts[0].get("action_type") != "scp"
                or measurement_receipts[0].get("outcome") != "succeeded"
                or measurement_receipts[0].get("exit_code") != 0
                or stop_row.get("remote_command") != f"sudo systemctl stop cnb-{deployment}-iperf3"
                or any(token not in owner_close_command for token in same_owner_tokens)):
            return unavailable()
        for row in (ready_row, snapshot_action, dispatch[0], measurement_receipts[0], owner_row, stop_row, closed_row):
            expected_role = "vm_a" if row in (ready_row, dispatch[0], measurement_receipts[0]) else "vm_b"
            if (row.get("outcome") != "succeeded" or row.get("exit_code") != 0
                    or row.get("role") != expected_role):
                return unavailable()
            if (row in (snapshot_action, owner_row, stop_row, closed_row)
                    and (row.get("vm_id") != payload.get("vm_b_host_id") or row.get("action_type") != "ssh")):
                return unavailable()
            if (row in (ready_row, dispatch[0], measurement_receipts[0])
                    and row.get("vm_id") != payload.get("vm_a_host_id")):
                return unavailable()
        if (not _ordered_utc(ready_row.get("finished_at"), snapshot_action.get("started_at"))
                or not _ordered_utc(snapshot_action.get("finished_at"), dispatch[0].get("started_at"))
                or not _ordered_utc(dispatch[0].get("finished_at"), measurement_receipts[0].get("started_at"))
                or not _ordered_utc(measurement_receipts[0].get("finished_at"), owner_row.get("started_at"))
                or not _ordered_utc(owner_row.get("finished_at"), stop_row.get("started_at"))
                or not _ordered_utc(stop_row.get("finished_at"), closed_row.get("started_at"))
                or not _ordered_utc(closed_row.get("started_at"), closed_row.get("finished_at"))):
            return unavailable()
        association = {"status": "verified", "artifact_id": log_record.artifact_id,
            "path": log_record.path, "raw_sha256": log_record.sha256.value,
            "readiness_cookie": {"classification": "readiness probe rejected by listener",
                "error_line": readiness_pairs[0][0], "end_line": readiness_pairs[0][2],
                "byte_start": readiness_pairs[0][1], "byte_end": readiness_pairs[0][3],
                "prefix_bytes": pre["prefix_bytes"], "prefix_sha256": pre["prefix_sha256"]},
            "server_interrupt": {"classification": "owned listener stop",
                "error_line": stop_pairs[0][0], "end_line": stop_pairs[0][2],
                "byte_start": stop_pairs[0][1], "byte_end": stop_pairs[0][3],
                "prefix_bytes": post["prefix_bytes"], "prefix_sha256": post["prefix_sha256"]}}
        suppressions = {(readiness_pairs[0][0], "native.iperf3.error"),
            (readiness_pairs[0][2], "native.iperf3.event"),
            (stop_pairs[0][0], "native.iperf3.error"),
            (stop_pairs[0][2], "native.iperf3.event")}
        return association, suppressions
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError):
        return unavailable()


def _include_guest_outcome_source(declarations: list[Any], remote: Any) -> list[Any]:
    """Declare exact transferred GuestOutcome bytes for strict bundle projection."""
    if getattr(remote, "outcome_source_bytes", None) is None:
        return declarations
    from .contracts.artifacts import ArtifactInput
    declaration = ArtifactInput(artifact_id="guest_outcome_source",
        path="metadata/guest-outcome-source.json", kind="guest_outcome_source",
        producer="controller", unit_id=None, expectation="required")
    existing = next((item for item in declarations if item.artifact_id == declaration.artifact_id), None)
    if existing is not None and existing != declaration:
        raise ValueError("guest outcome source declaration conflicts with the fixed F09 projection path")
    if existing is None:
        declarations.append(declaration)
    return declarations


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
    if execution == ExecutionState.INITIALIZED and result.primary_failure:
        execution = ExecutionState.FAILED
    if execution == ExecutionState.PROVISION_READY and current.execution_state == ExecutionState.INITIALIZED:
        current = update_child_execution(child_path, ExecutionState.PROVISION_READY,
            creation["finished_at"] if creation else evaluated_at, parent_manifest_path=parent_path)
    elif execution == ExecutionState.FAILED:
        if current.execution_state not in {ExecutionState.FAILED, ExecutionState.INTERRUPTED}:
            stage = _failed_stage(result, stage_times)
            observed_at = stage_times.get(stage, {}).get("finished_at", terminal_at)
            provider = getattr(child.provider, "value", child.provider)
            cause = result.primary_failure or f"{provider} lifecycle failed"
            observed_failure = getattr(result.values.get("protect"),"failure",None)
            if stage == "prepare":
                preparation_failure, _ = _preparation_failure(result)
                if preparation_failure is not None:
                    failure = _failure(preparation_failure.message[:1000],
                        preparation_failure.occurred_at, preparation_failure.category)
                    failure = failure.model_copy(update={"action_id": preparation_failure.action_id})
                else:
                    failure = observed_failure or _failure(cause, observed_at, "lifecycle_failure")
            else:
                failure = observed_failure or _failure(cause, observed_at, "lifecycle_failure")
            if observed_failure is not None:
                observed_at = observed_failure.occurred_at
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
    remote = _guest_execution_from(result)
    if guest is not None:
        from .contracts.measurements import CommandEvidence, MeasurementTiming
        commands = [CommandEvidence.model_validate(item) for item in guest.commands]
        timings = [MeasurementTiming.model_validate(item) for item in guest.timings]
    from .contracts.measurements import CommandEvidence
    commands.extend(CommandEvidence.model_validate(item) for item in
        (getattr(remote, "receiver_command_evidence", ()) or ()))
    from .lifecycle_bundle import lifecycle_declarations
    builds = _builds_from(result)
    declarations = lifecycle_declarations(child.artifacts, mode=child.mode,
        measurement_units=_day_units(child, guest),
        schedule_supplied=child.schedule_entry.value is not None,
        build_observations=builds)
    _include_guest_outcome_source(declarations, remote)
    for role in ("vm_a", "vm_b"):
        from .diagnostics import _declarations
        declarations.extend(_declarations(role))
    records = _records_from(result)
    preparation_failure, preparation_payload = _preparation_failure(result)
    if preparation_payload is not None:
        declaration = _preparation_failure_declaration(result)
        declarations.append(declaration)
        payload = (json.dumps(preparation_payload, sort_keys=True, indent=2) + "\n").encode()
        record = _controller_record(root, child.deployment_id, declaration, payload)
        if record is not None:
            records.append(record)
    _measurement_failed_stage, measurement_payload = _measurement_failure(result)
    if measurement_payload is not None:
        declaration = _measurement_failure_declaration(result)
        declarations.append(declaration)
        payload = (json.dumps(measurement_payload, sort_keys=True, indent=2) + "\n").encode()
        record = _controller_record(root, child.deployment_id, declaration, payload)
        if record is not None:
            records.append(record)
    if builds:
        from .bootstrap import materialize_build_identity_artifacts
        records.extend(materialize_build_identity_artifacts(root, child.deployment_id, builds))
    records = _merge_artifact_records(records)
    records.extend(_materialize_controller_evidence(root, child, result, declarations,
        source_records=records))
    if _value(child.mode) == "diagnostic_day":
        parent_units = {unit.unit_id for unit in child.measurement_units}
        commands = [item for item in commands if item.unit_id in parent_units]
        timings = [item for item in timings if item.unit_id in parent_units]
        records = [item for item in records if item.unit_id is None or item.unit_id in parent_units]
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
            if parsed.deployment_id != child.deployment_id or str(parsed.vm_role) != role:
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
    from .contracts.common import Evidence, IdentityContext, StructuredEvidence, VmRole
    from .contracts.measurements import CommandEvidence, DiagnosticSample, MeasurementTiming, VmMetadata
    from .contracts.validation import ValidatorInput
    from .lifecycle_bundle import lifecycle_declarations
    from .diagnostics import _declarations, iter_clock_mappings, iter_samples

    guest = _guest_from(result)
    units = _day_units(child, guest)
    observation = child.resolved_observation
    builds = _builds_from(result)
    declarations = lifecycle_declarations(child.artifacts, mode=child.mode,
        measurement_units=units, schedule_supplied=child.schedule_entry.value is not None,
        build_observations=builds)
    remote = _guest_execution_from(result)
    _include_guest_outcome_source(declarations, remote)
    preparation_declaration = _preparation_failure_declaration(result)
    if preparation_declaration is not None:
        declarations.append(preparation_declaration)
    measurement_declaration = _measurement_failure_declaration(result)
    if measurement_declaration is not None:
        declarations.append(measurement_declaration)
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
        timings.extend(MeasurementTiming.model_validate(value) for value in guest.timings)
    commands.extend(CommandEvidence.model_validate(value) for value in
        (getattr(remote, "receiver_command_evidence", ()) or ()))
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
    # The child manifest intentionally omits concrete DAY probe chunks. Their
    # controller-owned command/timing sidecars still belong in the complete
    # ValidatorInput and are rematerialized from the captured guest outcome.
    source_records = _merge_artifact_records(list(child.artifact_records) + _records_from(result))
    controller_records = _materialize_controller_evidence(root, child, result, declarations,
        source_records=source_records)
    records = _merge_artifact_records(source_records + controller_records)
    create_evidence = _created_deployment(child, result)
    vm_a = getattr(create_evidence, "vm_a", None)
    vm_b = getattr(create_evidence, "vm_b", None)
    prep_evidence = getattr(_outcome(result, "prepare"), "evidence", None)
    bootstrap_evidence = prep_evidence.get("bootstrap") if isinstance(prep_evidence, Mapping) else None
    iperf_listener = next((item for item in (getattr(bootstrap_evidence, "listeners", ()) or ())
        if getattr(item, "tool", None) == "iperf3"), None)
    control_evidence = _verified_iperf3_control_evidence(root, child, records,
        vm_a_host_id=getattr(getattr(vm_a, "connection", None), "host", None),
        vm_b_host_id=getattr(getattr(vm_b, "connection", None), "host", None),
        private_ipv4_b=getattr(vm_b, "private_ipv4", None),
        stdout_path=getattr(iperf_listener, "stdout_path", None),
        stderr_path=getattr(iperf_listener, "stderr_path", None))
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
    receiver_commands = list(getattr(remote, "receiver_command_evidence", ()) or ())
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
        iperf3_control_evidence=(StructuredEvidence(value=control_evidence) if control_evidence is not None
            else StructuredEvidence(unavailable_reason="verified identity-bound control evidence is unavailable")),
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
        from .contracts.common import Evidence, StringEvidence
        from .contracts.validation import ValidatorInput
        from .paths import atomic_write_json
        from .contracts.artifacts import resolve_artifact_path
        bound, _sources, _unavailable = bind_retrieved_acquisition_sources(value=value, child_root=root,
            source_artifact_records=value.artifact_records, build_evidence=builds,
            receiver_command_evidence=receiver_commands, clock_mappings=clock_mappings)
        projected_records = { (record.artifact_id, record.path, record.unit_id): record
            for record in bound.artifact_records }
        changed_timing_records = []
        for timing in bound.timings:
            prior_timing = next((item for item in value.timings if item.unit_id == timing.unit_id), None)
            if prior_timing == timing:
                continue
            record = next((item for item in value.artifact_records
                if item.kind == "measurement_timing" and item.unit_id == timing.unit_id
                and item.retrieval_state == "verified"), None)
            if record is None:
                raise ValueError("projected timing lacks its initialized saved timing artifact")
            record.verify_materialized_file(root)
            payload = [timing.model_dump(mode="json")]
            timing_path = resolve_artifact_path(root, record.path)
            atomic_write_json(timing_path, payload)
            raw = timing_path.read_bytes()
            updated_record = record.model_copy(update={
                "size_bytes": Evidence[int](value=len(raw)),
                "sha256": StringEvidence(value=hashlib.sha256(raw).hexdigest())})
            updated_record = type(record).model_validate(updated_record.model_dump(mode="python"))
            changed_timing_records.append(updated_record)
            projected_records[(record.artifact_id, record.path, record.unit_id)] = updated_record
        bound = ValidatorInput.model_validate({**bound.model_dump(mode="python"),
            "artifact_records": [item.model_dump(mode="python") for item in projected_records.values()]})
        manifest_unit_ids = {unit.unit_id for unit in child.measurement_units}
        manifest_timings = [timing for timing in bound.timings
            if timing.unit_id in manifest_unit_ids]
        manifest_timing_records = [record for record in changed_timing_records
            if record.unit_id is None or record.unit_id in manifest_unit_ids]
        child = update_child_runtime_evidence(child_path, fixed_at, timings=manifest_timings,
            artifact_records=manifest_timing_records or None, parent_manifest_path=parent_path)
        if child.timings != manifest_timings:
            raise ValueError("child manifest timings differ from strict manifest-unit source projection")
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
