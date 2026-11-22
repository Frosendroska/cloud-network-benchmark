"""Isolated F09 lifecycle command parser and read-only admission preview."""
from __future__ import annotations

import argparse
import json
import hashlib
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .admission import load_cost_inputs
from .config import resolve_campaign
from .scheduling import load_schedule
from .lifecycle_bundle import recommend_calibration_common_n
from .lifecycle_runtime import _constant, _object, load_runtime_config, load_initialized_campaign


def _archive_status_projection(result: Any) -> dict[str, Any]:
    """Keep durable nonsecret archive outcome without request/session data."""
    outcome = result.values.get("retrieve")
    evidence = getattr(outcome, "evidence", None)
    status = getattr(evidence, "archive_status", None)
    if not isinstance(status, dict):
        return {"status": "not_configured", "verified": False}
    keys = {"status", "verified", "bucket", "prefix", "manifest_sha256", "archive_bytes",
        "controller_archive_disk_bytes", "staging_peak_bytes", "staging_filesystem_free_bytes",
        "completion_generation", "reason", "error", "partial_objects"}
    return {key: status[key] for key in sorted(keys & status.keys())}


def _verified_replay_entry_id(*, capsule_path: Path, capsule_sha256: str,
                              observer: dict[str, Any], repository_root: Path) -> str:
    """Bind replay export reuse to this runtime's registered archive-pending entry."""
    capsule_path = Path(capsule_path)
    if capsule_path.is_symlink() or not capsule_path.is_file():
        raise ValueError("replay capsule must be a regular non-symlink file")
    capsule_bytes = capsule_path.read_bytes()
    if hashlib.sha256(capsule_bytes).hexdigest() != capsule_sha256:
        raise ValueError("replay capsule bytes differ from their separately bound digest")
    capsule = json.loads(capsule_bytes)
    if capsule.get("schema_version") != 2 or capsule.get("status") != "archive_pending_replay":
        raise ValueError("replay capsule is not a batch archive-pending capsule")
    stage_path = Path(observer["prepared_stage_path"])
    journal_path = Path(observer["journal_path"])
    if any(path.is_symlink() or not path.is_file() for path in (stage_path, journal_path)):
        raise ValueError("registered prepared stage or campaign journal is unavailable")
    stage = json.loads(stage_path.read_text(encoding="utf-8"))
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    if (stage.get("stage_id") != observer.get("stage_id")
            or stage.get("stage_id") != journal.get("stage_id")
            or Path(stage.get("repository_root", "")).resolve(strict=True) != repository_root.resolve(strict=True)):
        raise ValueError("registered stage identity or repository root differs from replay runtime")
    entry_id = observer.get("entry_id")
    entries = [row for row in stage.get("entries", [])
        if isinstance(row, dict) and row.get("entry_id") == entry_id]
    if len(entries) != 1:
        raise ValueError("runtime does not identify exactly one registered campaign entry")
    entry = entries[0]
    capsule_runs = {provider: str(child.get("run_id"))
        for provider, child in capsule.get("child_manifests", {}).items() if isinstance(child, dict)}
    if not capsule_runs or entry.get("run_ids") != capsule_runs:
        raise ValueError("registered campaign run ids differ from the replay capsule")
    parent_relative = Path(capsule.get("parent_path", ""))
    if parent_relative.is_absolute() or ".." in parent_relative.parts:
        raise ValueError("replay capsule parent path is unsafe")
    capsule_parent = (repository_root / parent_relative).resolve(strict=True)
    registered_parent = Path(entry.get("parent_manifest_path", "")).resolve(strict=True)
    if capsule_parent != registered_parent:
        raise ValueError("registered parent manifest differs from replay capsule")
    record = journal.get("entries", {}).get(entry_id)
    result = record.get("result") if isinstance(record, dict) else None
    replay_ref = result.get("replay_capsule") if isinstance(result, dict) else None
    if (not isinstance(record, dict) or record.get("state") != "archive_pending_validation"
            or not isinstance(result, dict) or result.get("cleanup_open") is not False
            or not isinstance(replay_ref, dict)
            or replay_ref.get("sha256") != capsule_sha256
            or Path(replay_ref.get("path", "")).resolve(strict=True) != capsule_path.resolve(strict=True)):
        raise ValueError("campaign journal does not bind this capsule to a closed archive-pending entry")
    return entry_id


def _read_replay_quota_evidence(path: Path, expected_sha256: str) -> dict[str, Any]:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("fresh quota evidence must be a regular non-symlink file")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected_sha256:
        raise ValueError("fresh quota evidence differs from its separately bound SHA-256")
    document = json.loads(content, object_pairs_hook=_object, parse_constant=_constant)
    fields = ("measured_free_bytes", "measured_at", "metadata_headroom_bytes",
        "projected_other_growth_bytes", "reserve_bytes")
    if (not isinstance(document, dict) or any(key not in document for key in fields)
            or any(type(document[key]) is not int or document[key] <= 0
                for key in ("measured_free_bytes", "metadata_headroom_bytes",
                    "projected_other_growth_bytes", "reserve_bytes"))
            or not isinstance(document["measured_at"], str)):
        raise ValueError("quota evidence must bind measured bytes/time, metadata, other growth, and reserve")
    return document


def preview_lifecycle(*, config_path: Path, repository_root: Path,
                       schedule_path: Path | None, row_id: str | None,
                       cost_inputs_path: Path, now: datetime | None = None) -> dict[str, Any]:
    """Inspect one selected row without reserving, registering or invoking a provider."""
    root = repository_root.resolve()
    config_path = config_path.resolve()
    resolved, _ = resolve_campaign(config_path, root)
    selected_entry = None
    reasons = list(resolved.launch_blockers)
    if schedule_path is not None:
        entries = load_schedule(schedule_path, config_paths={resolved.config_id: config_path}, repository_root=root)
        matches = [entry for entry in entries if entry.entry_id == row_id]
        if len(matches) != 1:
            reasons.append("selected_schedule_row_missing_or_ambiguous")
        else:
            selected_entry = matches[0]
            resolved, _ = resolve_campaign(config_path, root, schedule_entry=selected_entry)
    elif row_id is not None:
        reasons.append("schedule_row_requires_schedule_file")
    elif resolved.admission.requires_schedule:
        reasons.append("schedule_pending")
    costs = load_cost_inputs(cost_inputs_path)
    if costs.refresh_required:
        reasons.append("price_refresh_required")
    reasons.extend(("f11_reservation_not_evaluated", "paid_acceptance_prerequisites_not_evaluated",
                    "provider_expiry_not_registered", "complete_workflow_allowance_not_supplied"))
    return {
        "status": "inspectable" if selected_entry is not None or not resolved.admission.requires_schedule else "pending",
        "paid_eligible": False,
        "config_id": resolved.config_id,
        "experiment_id": resolved.experiment_id,
        "mode": str(resolved.mode),
        "selected_providers": [str(provider) for provider in resolved.selected_providers],
        "schedule_entry_id": selected_entry.entry_id if selected_entry else None,
        "cost_input": {"id": costs.cost_input_id, "sha256": costs.payload_sha256,
                       "prices_checked_on": str(costs.prices_checked_on),
                       "refresh_required": costs.refresh_required},
        "reason_codes": sorted(set(reasons)),
        "external_calls": 0,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cloud-network-benchmark lifecycle")
    commands = parser.add_subparsers(dest="action", required=True)
    for name in ("preview", "run"):
        command = commands.add_parser(name)
        command.add_argument("--config", required=True, type=Path)
        command.add_argument("--schedule", type=Path)
        command.add_argument("--row-id")
        command.add_argument("--cost-inputs", required=True, type=Path)
        command.add_argument("--runtime-config", required=True, type=Path)
        command.add_argument("--format", choices=("human", "json"), default="human")
        if name == "run":
            command.add_argument("--results-root", required=True, type=Path)
            command.add_argument("--private-root", required=True, type=Path)
            command.add_argument("--controller-session", required=True)
            command.add_argument("--execution-scope", choices=("paid_request",), required=True)
    cleanup = commands.add_parser("cleanup")
    cleanup.add_argument("--recovery", required=True, type=Path)
    cleanup.add_argument("--runtime-config", type=Path,
        help="deprecated for cleanup; original source-bound settings come from the private recovery journal")
    cleanup.add_argument("--private-root", type=Path,
        help="deprecated for cleanup; original private paths come from the private recovery journal")
    cleanup.add_argument("--controller-session", required=True)
    cleanup.add_argument("--format", choices=("human", "json"), default="human")
    recommendation = commands.add_parser("recommend-common-n")
    recommendation.add_argument("--validation-results", required=True, type=Path,
        help="JSON array of saved F08 calibration ValidationResult objects")
    recommendation.add_argument("--format", choices=("human", "json"), default="human")
    replay = commands.add_parser("replay-archives")
    replay.add_argument("--capsule", required=True, type=Path)
    replay.add_argument("--capsule-sha256", required=True)
    replay.add_argument("--runtime-config", required=True, type=Path)
    replay.add_argument("--restore-path", required=True, type=Path,
        help="current roomy destination; free space is checked again at restore time")
    replay.add_argument("--prune-verified-scratch", action="store_true",
        help="after valid replay and verified export, remove only generation-verified downloaded archive/native scratch")
    replay.add_argument("--quota-evidence", type=Path,
        help="fresh user-quota JSON receipt for opt-in pruning (must be measured within 15 minutes)")
    replay.add_argument("--quota-evidence-sha256",
        help="separate SHA-256 binding for --quota-evidence")
    replay.add_argument("--format", choices=("human", "json"), default="json")
    return parser


class CountingRunner:
    """Count actual command/copy dispatches, including failures and cleanup."""
    def __init__(self, delegate):
        self.delegate = delegate
        self.actions = []

    def run(self, request):
        self.actions.append(request.action_id)
        return self.delegate.run(request)

    def run_bounded_copy(self, request, destination, maximum_payload_bytes):
        self.actions.append(request.action_id)
        return self.delegate.run_bounded_copy(request,destination,maximum_payload_bytes)


def main(argv: Sequence[str] | None = None, *, cleanup_actions=None, reconcile=None,
         cleanup_runner=None, cleanup_utc_now=None, cleanup_monotonic_now=None, run_clock=None) -> int:
    parser = _parser()
    runner = None
    lifecycle = None
    initialized = None
    children = {}
    try:
        args = parser.parse_args(argv)
        if args.action in {"preview", "run"}:
            for path in (args.config, args.cost_inputs, args.runtime_config):
                if not path.is_file() or path.is_symlink():
                    raise ValueError(f"required local input is unavailable: {path.name}")
            resolved, _ = resolve_campaign(args.config, Path.cwd())
            runtime = load_runtime_config(args.runtime_config,
                selected_providers={str(provider) for provider in resolved.selected_providers})
            report = preview_lifecycle(config_path=args.config, repository_root=Path.cwd(),
                schedule_path=args.schedule, row_id=args.row_id, cost_inputs_path=args.cost_inputs,
                now=datetime.now(timezone.utc))
            if args.action == "run":
                try:
                    initialized, children = load_initialized_campaign(
                        runtime["initialized_campaign_manifest"], repository_root=Path.cwd(),
                        config_path=args.config, schedule_path=args.schedule)
                    if initialized.config_id != resolved.config_id or initialized.mode != resolved.mode:
                        raise ValueError("initialized campaign identity differs from the selected config")
                    if set(children) != {str(provider) for provider in resolved.selected_providers}:
                        raise ValueError("initialized provider set differs from the selected campaign")
                except (OSError, ValueError) as exc:
                    report.update(status="rejected", reason_codes=sorted(set(report["reason_codes"] +
                        ["initialized_campaign_unavailable_or_mismatched"])), detail=str(exc),
                        execution_scope=args.execution_scope, external_calls=0)
                    print(json.dumps(report, indent=2, sort_keys=True) if args.format == "json" else
                          "Lifecycle run rejected: initialized campaign identity is unavailable or mismatched.")
                    return 1
                from .contracts.execution import SubprocessCommandRunner
                from .lifecycle_runtime import build_runtime
                from .orchestration import run_lifecycle
                from .lifecycle_projection import finalize_production_batch

                class Clock:
                    @staticmethod
                    def monotonic():
                        return time.monotonic()

                    @staticmethod
                    def utc_now():
                        return datetime.now(timezone.utc)

                costs = load_cost_inputs(args.cost_inputs)
                selected_campaign, _ = resolve_campaign(args.config, Path.cwd(),
                    schedule_entry=(None if args.schedule is None else
                        next((entry for entry in load_schedule(args.schedule,
                            config_paths={resolved.config_id: args.config}, repository_root=Path.cwd())
                            if entry.entry_id == args.row_id), None)))
                runner, clock = CountingRunner(SubprocessCommandRunner()), (run_clock or Clock())
                built = build_runtime(campaign=selected_campaign, parent=initialized,
                    children=children, costs=costs, runtime=runtime,
                    controller_session_id=args.controller_session, runner=runner, clock=clock,
                    private_root=args.private_root, repository_root=Path.cwd())
                lifecycle = run_lifecycle(ledger=built["ledger"],
                    reservation_request=built["request"], admission_plan=built["plan"],
                    allowance=built["allowance"], workflow_factory=built["workflow_factory"],
                    clock=clock, recovery_path=built["recovery_path"],
                    recovery_factory=built["recovery_factory"])
                validations = {}
                finalization_failure = None
                replay_capsule = None
                archive_pending = bool(runtime.get("archive_sink"))
                if lifecycle.provider_results and lifecycle.reservation_evidence is not None:
                    try:
                        if archive_pending:
                            from .archive_replay import write_batch_replay_capsule
                            archives = {}
                            result_roots = {}
                            child_manifest_paths = {}
                            for provider, provider_result in lifecycle.provider_results.items():
                                retrieve = provider_result.values.get("retrieve")
                                retrieval = getattr(retrieve, "evidence", None)
                                receipt = getattr(retrieval, "archive_status", None)
                                if not isinstance(receipt, dict) or receipt.get("status") != "verified":
                                    raise ValueError("archive sink did not return a verified complete provider receipt")
                                archives[provider] = receipt
                                child_ref = next(row for row in initialized.child_runs
                                    if str(row.provider) == provider)
                                child_path = Path(child_ref.manifest_path)
                                result_root = Path(child_ref.result_path)
                                if not child_path.is_absolute(): child_path = Path.cwd() / child_path
                                if not result_root.is_absolute(): result_root = Path.cwd() / result_root
                                child_manifest_paths[provider] = child_path.resolve(strict=True)
                                result_roots[provider] = result_root.resolve(strict=True)
                            capsule_root = Path(runtime["archive_sink"]["archive_root"]) / "replay-capsules"
                            capsule_root.mkdir(parents=True, exist_ok=True)
                            capsule_path = capsule_root / f"{initialized.campaign_id}-{lifecycle.reservation_evidence.reservation_id.value}.json"
                            replay_capsule = write_batch_replay_capsule(capsule_path,
                                archives=archives, parent_manifest_path=runtime["initialized_campaign_manifest"],
                                child_manifest_paths=child_manifest_paths,
                                result_roots=result_roots, provider_results=lifecycle.provider_results,
                                reservation=lifecycle.reservation_evidence, repository_root=Path.cwd(),
                                maximum_capsule_bytes=runtime["archive_sink"]["maximum_capsule_bytes"])
                        else:
                            validations = finalize_production_batch(
                                parent_path=runtime["initialized_campaign_manifest"], children=children,
                                parent=initialized, lifecycle=lifecycle, repository_root=Path.cwd())
                    except (OSError, ValueError) as exc:
                        finalization_failure = {"category":"post_cleanup_validation_failed","message":str(exc)}
                data_valid = bool(validations) and all(
                    value[0].validation_status.data_validity == "valid"
                    for value in validations.values())
                unevaluated = {"f11_reservation_not_evaluated", "paid_acceptance_prerequisites_not_evaluated",
                    "provider_expiry_not_registered", "complete_workflow_allowance_not_supplied"}
                report.update(status=("archive_pending_validation" if lifecycle.completed and replay_capsule else
                    "complete" if lifecycle.completed and data_valid else
                    "rejected" if lifecycle.admission_status != "reserved" else "failed"),
                    reason_codes=sorted((({"post_cleanup_validation_failed"} if finalization_failure else set()) | set(lifecycle.admission_reasons)
                        | ({lifecycle.primary_failure} if lifecycle.primary_failure else set()))
                        | (set(report["reason_codes"]) - unevaluated)),
                    execution_scope=args.execution_scope, campaign_id=initialized.campaign_id,
                    run_ids={k: v.run_id for k, v in children.items()},
                    admission_status=lifecycle.admission_status,
                    reservation_id=(lifecycle.reservation_evidence.reservation_id.value
                        if lifecycle.reservation_evidence is not None else None),
                    providers={provider: {"execution_state": item.execution_state,
                        "cleanup_state": item.cleanup_state, "primary_failure": item.primary_failure,
                        "archive_status": _archive_status_projection(item),
                        "secondary_failures": item.secondary_failures,
                        "validation_status": (validations[provider][0].validation_status.model_dump(mode="json")
                            if provider in validations else None)}
                        for provider, item in lifecycle.provider_results.items()},
                    finalization_failure=finalization_failure,
                    replay_capsule=replay_capsule,
                    external_calls=len(runner.actions))
                print(json.dumps(report, indent=2, sort_keys=True) if args.format == "json" else
                      f"Lifecycle run {report['status']}; admission: {report['admission_status']}; external calls: {report['external_calls']}",
                      file=sys.stdout if report["status"] in {"complete", "archive_pending_validation"} else sys.stderr)
                return 0 if report["status"] in {"complete", "archive_pending_validation"} else 1
            print(json.dumps(report, indent=2, sort_keys=True) if args.format == "json" else
                  f"Lifecycle preview: {report['status']}; paid eligible: no; external calls: 0")
            return 0 if report["status"] != "invalid" else 2
        if args.action == "recommend-common-n":
            if not args.validation_results.is_file() or args.validation_results.is_symlink():
                raise ValueError("validation results must be a regular non-symlink file")
            payload = json.loads(args.validation_results.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise ValueError("validation results must be a JSON array")
            recommendation = recommend_calibration_common_n(payload)
            report = {"status": "recommended" if recommendation.common_n.value is not None else "pending",
                "common_n": recommendation.common_n.model_dump(mode="json"),
                "plateau_found": recommendation.plateau_found.model_dump(mode="json"),
                "provider_medians_gbps": recommendation.provider_medians_gbps,
                "qualifying_ratios": recommendation.qualifying_ratios,
                "source_campaign_id": recommendation.source_campaign_id.model_dump(mode="json"),
                "source_run_ids": list(recommendation.source_run_ids),
                "source_sha256": recommendation.source_sha256.model_dump(mode="json"),
                "reason": recommendation.unavailable_reason,
                "config_mutated": False}
            print(json.dumps(report, indent=2, sort_keys=True) if args.format == "json" else
                  (f"Recommended common N: {recommendation.common_n.value}" if recommendation.common_n.value is not None
                   else f"Common N pending: {recommendation.unavailable_reason}"))
            return 0 if recommendation.common_n.value is not None else 1
        if args.action == "replay-archives":
            if not args.runtime_config.is_file() or args.runtime_config.is_symlink():
                raise ValueError("runtime config must be a regular non-symlink file")
            runtime = load_runtime_config(args.runtime_config)
            sink = runtime.get("archive_sink")
            if sink is None:
                raise ValueError("runtime config has no archive sink")
            replay_options = {}
            if args.prune_verified_scratch:
                if args.quota_evidence is None or not args.quota_evidence_sha256:
                    raise ValueError("opt-in replay pruning requires --quota-evidence and --quota-evidence-sha256")
                if not re.fullmatch(r"[a-f0-9]{64}", args.quota_evidence_sha256):
                    raise ValueError("quota evidence SHA-256 must be a lowercase SHA-256 digest")
                quota = _read_replay_quota_evidence(args.quota_evidence, args.quota_evidence_sha256)
                observer = runtime.get("observer")
                if not isinstance(observer, dict):
                    raise ValueError("opt-in replay pruning requires the registered observer entry identity")
                entry_id = _verified_replay_entry_id(capsule_path=args.capsule,
                    capsule_sha256=args.capsule_sha256, observer=observer,
                    repository_root=Path.cwd())
                replay_options = {"prune_verified_scratch": True,
                    "closed_export_entry_id": entry_id,
                    "controller_quota_free_bytes": quota["measured_free_bytes"],
                    "controller_quota_evidence_path": args.quota_evidence,
                    "controller_quota_evidence_sha256": args.quota_evidence_sha256,
                    "metadata_headroom_bytes": quota["metadata_headroom_bytes"],
                    "projected_other_growth_bytes": quota["projected_other_growth_bytes"],
                    "minimum_free_bytes": quota["reserve_bytes"]}
            elif args.quota_evidence is not None or args.quota_evidence_sha256 is not None:
                raise ValueError("quota evidence arguments require --prune-verified-scratch")
            from .gcs_archive import google_cloud_storage_backend
            from .archive_replay import restore_and_finalize_batch_capsule
            output = restore_and_finalize_batch_capsule(capsule_path=args.capsule,
                expected_capsule_sha256=args.capsule_sha256,
                backend=None, backend_factory=lambda: google_cloud_storage_backend(sink), bucket=sink["bucket"],
                archive_staging_root=sink["staging_directory"],
                materialization_root=args.restore_path, repository_root=Path.cwd(),
                maximum_part_bytes=sink["maximum_staging_bytes"],
                minimum_free_bytes=replay_options.get("minimum_free_bytes",
                    sink["minimum_controller_free_bytes"]), **{key: value for key, value in replay_options.items()
                        if key != "minimum_free_bytes"})
            print(json.dumps(output, indent=2, sort_keys=True))
            return 0 if output["status"] == "replayed" else 1
        # Cleanup is rebuilt only from the private recovery journal, its saved
        # source-bound provider settings and the original F11 ledger reference.
        if not args.recovery.is_file() or args.recovery.is_symlink():
            raise ValueError("recovery reference must be a regular non-symlink file")
        if cleanup_actions is not None and reconcile is not None:
            from .orchestration import cleanup_lifecycle
            result = cleanup_lifecycle(args.recovery, current_session_id=args.controller_session,
                cleanup_actions=cleanup_actions, reconcile=reconcile)
        else:
            from .orchestration import SavedLifecycleRecovery
            from .lifecycle_cleanup import CleanupClock, build_cleanup_runtime
            from .contracts.execution import SubprocessCommandRunner
            recovery = SavedLifecycleRecovery.validate_unique(
                json.loads(args.recovery.read_text(encoding="utf-8")))
            clock = CleanupClock()
            runner = CountingRunner(cleanup_runner or SubprocessCommandRunner())
            result = build_cleanup_runtime(recovery_path=args.recovery, recovery=recovery,
                current_session_id=args.controller_session,
                runner=runner,
                utc_now=cleanup_utc_now or clock.utc_now,
                monotonic_now=cleanup_monotonic_now or clock.monotonic)
        report = {"status": "complete" if result.complete else "blocked",
                "execution": "not_resumed", "cleanup": "complete" if result.complete else "incomplete",
                "attempt_id": result.attempt_id,
                "original_session_id": result.original_session_id,
                "current_session_id": result.current_session_id,
                "providers": {provider: {key: value for key, value in item.items()
                    if key in {"cleanup", "reconciled", "absence_proved", "billable_resources_present",
                               "cleanup_open", "billing_final", "incurred_usd", "errors", "launch_blocked", "reason"}}
                    for provider, item in result.providers.items()},
                "external_calls": len(runner.actions) if runner is not None else 0}
        print(json.dumps(report, indent=2, sort_keys=True) if args.format == "json" else
                  f"Cleanup {report['status']}: execution was not resumed.",
                  file=sys.stdout if result.complete else sys.stderr)
        return 0 if result.complete else 1
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        error_report = {"status":"failed" if lifecycle is not None else "invalid",
            "reason_codes":["lifecycle_reporting_failed" if lifecycle is not None else "invalid_lifecycle_request"],
            "error":str(exc),"external_calls":len(runner.actions) if runner is not None else 0}
        if lifecycle is not None:
            error_report.update(primary_failure=lifecycle.primary_failure,
                campaign_id=initialized.campaign_id,run_ids={key:value.run_id for key,value in children.items()},
                providers={key:{"execution_state":value.execution_state,"cleanup_state":value.cleanup_state,
                    "primary_failure":value.primary_failure,"secondary_failures":value.secondary_failures,
                    "archive_status":_archive_status_projection(value)}
                    for key,value in lifecycle.provider_results.items()})
        print(json.dumps(error_report,sort_keys=True),file=sys.stderr)
        return 1 if lifecycle is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())
