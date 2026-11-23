"""Small durable driver for explicit, preplanned benchmark campaigns."""
from __future__ import annotations

import json
import hashlib
import inspect
import os
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

from .campaign_inputs import load_operator_settings, prepare_stage, validate_stage_inputs
from .campaign_draft import (ACTIVATED_STATUS, create_draft_bundle,
    load_and_verify_draft, mark_consumed, validate_fixed_batch_scope, _stage_identity,
    verify_activation)


def _utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("campaign timestamps must be ISO-8601 UTC values")
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("campaign timestamps must include a UTC offset")
    return result.astimezone(timezone.utc)


def _now(clock: Any) -> datetime:
    value = clock.utc_now() if hasattr(clock, "utc_now") else clock()
    return _utc(value)


def _validate_week_dispatch_policy(policy: Any) -> None:
    """Recheck the frozen, source-bound cap immediately before dispatch."""
    if (not isinstance(policy, dict) or policy.get("policy_id") != "weekly-paired-s1-s3-v1"
            or policy.get("group_by") != "launch_at" or policy.get("scenarios") != ["S1", "S3"]
            or policy.get("priorities") != ["P0", "P1"] or policy.get("max_parallel_entries") != 2):
        raise ValueError("WEEK dispatch requires the frozen paired S1/S3 two-entry policy")
    evidence = Path(policy.get("evidence_path", ""))
    if (not evidence.is_file() or evidence.is_symlink()
            or any(parent.is_symlink() for parent in evidence.parents)
            or _digest(evidence) != policy.get("evidence_sha256")):
        raise ValueError("WEEK concurrency admission evidence is missing or changed")
    try:
        admission = json.loads(evidence.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("WEEK concurrency admission evidence is unreadable") from exc
    source = Path(admission.get("source_path", ""))
    source_hash = admission.get("source_sha256")
    if (admission.get("mode") != "paired_s1_s3" or admission.get("max_parallel_entries") != 2
            or not isinstance(source_hash, str) or len(source_hash) != 64
            or any(char not in "0123456789abcdef" for char in source_hash)
            or not source.is_file() or source.is_symlink() or _digest(source) != source_hash):
        raise ValueError("WEEK concurrency admission evidence does not authorize the bounded S1/S3 pair")


def _validate_acceptance_dispatch_policy(policy: Any) -> None:
    """Recheck the separately registered, finite acceptance pair admission."""
    ready = isinstance(policy, dict) and policy.get("policy_id") == "acceptance-paired-s1-s3-ready-v1"
    if (not isinstance(policy, dict) or policy.get("policy_id") not in {
                "acceptance-paired-s1-s3-v1", "acceptance-paired-s1-s3-ready-v1"}
            or policy.get("group_by") != "acceptance_slot"
            or policy.get("scenarios") != ["S1", "S3"]
            or policy.get("max_parallel_entries") != 2):
        raise ValueError("acceptance dispatch requires the frozen paired S1/S3 two-entry policy")
    evidence = Path(policy.get("evidence_path", ""))
    if (not evidence.is_file() or evidence.is_symlink()
            or _digest(evidence) != policy.get("evidence_sha256")):
        raise ValueError("acceptance paired admission evidence is missing or changed")
    try:
        admission = json.loads(evidence.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("acceptance paired admission evidence is unreadable") from exc
    if not isinstance(admission, dict):
        raise ValueError("acceptance paired admission evidence must be an object")
    source = Path(admission.get("source_path", ""))
    source_hash = admission.get("source_sha256")
    policy_bound = (admission.get("execution_window_utc") == policy.get("execution_window")
        if ready else admission.get("start_utc") == policy.get("start_utc"))
    if (admission.get("mode") != "paired_acceptance_s1_s3" or admission.get("max_parallel_entries") != 2
            or not policy_bound
            or not isinstance(source_hash, str) or len(source_hash) != 64
            or any(char not in "0123456789abcdef" for char in source_hash)
            or not source.is_absolute() or ".." in source.parts or not source.is_file() or source.is_symlink()
            or any(parent.is_symlink() for parent in source.parents) or _digest(source) != source_hash):
        raise ValueError("acceptance admission evidence does not authorize the bounded S1/S3 pair")
    if ready and (policy.get("start_policy") != "all_ready"
            or type(policy.get("ready_window_seconds")) is not int
            or not 1 <= policy["ready_window_seconds"] <= 600
            or not isinstance(policy.get("execution_window"), dict)):
        raise ValueError("ready-triggered acceptance requires a bounded all_ready policy and frozen execution window")


def _acceptance_pair(stage: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the one normal S1/S3 pair; registered failure checks stay supervised."""
    _validate_acceptance_dispatch_policy(stage.get("dispatch_policy"))
    entries = stage.get("entries", [])
    ids = [entry.get("entry_id") for entry in entries]
    expected = ["V-S1", "V-S3", "V-A-FAIL", "V-Z-FAIL", "V-G-FAIL"]
    if ids != expected:
        raise ValueError("acceptance stage must contain only the five registered V-S1/S3 and failure identities in order")
    pair = entries[:2]
    if ([row.get("entry_id") for row in pair] != ["V-S1", "V-S3"]
            or [row.get("scenario") for row in pair] != ["S1", "S3"]
            or any(row.get("requires_supervision") for row in pair)
            or any(not row.get("requires_supervision") for row in entries[2:])):
        raise ValueError("acceptance dispatch is limited to one normal S1/S3 pair followed by supervised failures")
    barrier = stage.get("post_measurement_barrier")
    participants = barrier.get("expected_participants") if isinstance(barrier, dict) else None
    expected = {(entry["entry_id"], provider, run_id)
        for entry in pair for provider, run_id in entry.get("run_ids", {}).items()}
    observed = {(row.get("entry_id"), row.get("provider"), row.get("run_id"))
        for row in participants} if isinstance(participants, list) else set()
    expected_root = Path(stage["state_root"]) / stage["stage_id"] / "measurement-barriers" / "normal-terminal"
    expected_barrier_id = f"{stage['stage_id']}-normal-measurement-terminal"
    if (any(set(entry.get("run_ids", {})) != {"aws", "azure", "gcp"} for entry in pair)
            or not isinstance(barrier, dict) or barrier.get("schema_version") != 1
            or barrier.get("barrier_id") != expected_barrier_id
            or barrier.get("barrier_root") != str(expected_root)
            or not isinstance(participants, list) or len(participants) != 6 or observed != expected):
        raise ValueError("paired acceptance requires a shared terminal-measurement barrier for its exact six normal children")
    launches = {_utc(row.get("launch_at")) for row in pair}
    start_barrier = stage.get("measurement_start_barrier")
    start_participants = start_barrier.get("expected_participants") if isinstance(start_barrier, dict) else None
    common_invalid = (not isinstance(start_barrier, dict) or start_barrier.get("schema_version") != 1
            or start_barrier.get("barrier_id") != f"{stage['stage_id']}-normal-measurement-start"
            or start_barrier.get("barrier_root") != str(Path(stage["state_root"]) / stage["stage_id"] / "measurement-barriers" / "normal-start")
            or start_barrier.get("expected_participants") != participants
            or not isinstance(start_participants, list) or len(start_participants) != 6)
    if common_invalid:
        raise ValueError("paired acceptance requires a shared six-child readiness barrier")
    if stage["dispatch_policy"].get("start_policy") == "all_ready":
        window = stage["dispatch_policy"].get("execution_window")
        ready_window_seconds = stage["dispatch_policy"].get("ready_window_seconds")
        if (type(ready_window_seconds) is not int or not 1 <= ready_window_seconds <= 600
                or not isinstance(window, dict) or set(window) != {"not_before_utc", "not_after_utc"}
                or start_barrier.get("start_policy") != "all_ready"
                or start_barrier.get("execution_window") != window
                or start_barrier.get("ready_window_seconds") != ready_window_seconds
                or any(row.get("scheduled_start") is not None for row in pair)
                or len(launches) != 1 or _utc(pair[0].get("launch_at")) != _utc(window.get("not_before_utc"))):
            raise ValueError("ready-triggered acceptance requires null scheduled starts and the frozen all-ready window")
        try:
            lower, upper = _utc(window.get("not_before_utc")), _utc(window.get("not_after_utc"))
        except (TypeError, ValueError) as exc:
            raise ValueError("ready-triggered acceptance window bounds must be UTC timestamps") from exc
        if upper <= lower:
            raise ValueError("ready-triggered acceptance execution window must be positive")
    else:
        scheduled = {_utc(row.get("scheduled_start")) for row in pair}
        if (len(scheduled) != 1 or len(launches) != 1
                or start_barrier.get("start_policy") is not None
                or start_barrier.get("execution_window") is not None
                or _utc(start_barrier.get("start_utc")) not in scheduled
                or _utc(start_barrier.get("start_utc")) != _utc(stage["dispatch_policy"].get("start_utc"))):
            raise ValueError("legacy paired acceptance requires one frozen UTC start; null starts are invalid")
    return pair


def _validate_reused_failure_entry(entry: dict[str, Any]) -> None:
    reference = entry.get("reused_failure_check")
    if not isinstance(reference, dict):
        raise ValueError("reused failure receipt is missing its frozen reference")
    path = Path(reference.get("source_path", ""))
    if (not path.is_absolute() or not path.is_file() or path.is_symlink()
            or _digest(path) != reference.get("source_sha256")):
        raise ValueError("reused failure receipt source is missing or changed")
    from .acceptance_review import _failure_review, _runtime_scope_sha256
    _failure_review({"path": str(path), "sha256": reference["source_sha256"]},
        required_entries={entry["entry_id"]})
    row = yaml.safe_load(path.read_text(encoding="utf-8"))["checks"][entry["entry_id"]]
    identity = row.get("config_identity", {})
    parent = json.loads(Path(entry["parent_manifest_path"]).read_text(encoding="utf-8"))
    if (row.get("attempt_id") != reference.get("attempt_id")
            or identity.get("config_sha256") != _digest(entry.get("config_path"))
            or identity.get("source_config_sha256") != entry.get("source_config_sha256")
            or identity.get("runtime_scope_sha256") != _runtime_scope_sha256(entry.get("runtime_config_path"))
            or identity.get("implementation_git_commit") != parent.get("implementation_git_commit")):
        raise ValueError("reused failure receipt does not match the frozen attempt/config/runtime/source identity")


def _week_slot_groups(stage: dict[str, Any]) -> dict[datetime, list[dict[str, Any]]]:
    """Validate and return the explicitly admitted two-entry S1/S3 WEEK slots."""
    _validate_week_dispatch_policy(stage.get("dispatch_policy"))
    groups: dict[datetime, list[dict[str, Any]]] = {}
    entry_ids: set[str] = set()
    for entry in stage.get("entries", []):
        entry_id = entry.get("entry_id")
        if not isinstance(entry_id, str) or entry_id in entry_ids:
            raise ValueError("WEEK entries must have unique stable identities")
        entry_ids.add(entry_id)
        if entry.get("scenario") not in {"S1", "S3"} or entry.get("priority") not in {"P0", "P1"}:
            raise ValueError("WEEK concurrency is limited to the registered P0/S1 and P1/S3 pair")
        if not entry.get("scheduled_start"):
            raise ValueError("WEEK paired entries require their frozen schedule slot")
        groups.setdefault(_utc(entry["launch_at"]), []).append(entry)
    if not groups:
        raise ValueError("WEEK requires at least one admitted S1/S3 slot")
    for slot, entries in groups.items():
        if (len(entries) != 2
                or {entry["scenario"] for entry in entries} != {"S1", "S3"}
                or {entry["priority"] for entry in entries} != {"P0", "P1"}
                or len({entry["scheduled_start"] for entry in entries}) != 1):
            raise ValueError(f"WEEK slot {slot.isoformat()} must contain exactly one P0/S1 and one P1/S3 entry")
    return groups


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, default=str)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    # Persist the directory entry as well as the file contents so a sudden
    # controller or machine loss does not roll back an acknowledged journal.
    try:
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        # Some supported filesystems do not implement directory fsync; the
        # journal file itself is still flushed before replacement above.
        pass


def _digest(path: Any) -> str | None:
    if not path:
        return None
    source = Path(path)
    if not source.is_file() or source.is_symlink():
        return None
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class _AggregateAdmissionError(ValueError):
    def __init__(self, message: str, reservation_ids: list[str]):
        super().__init__(message)
        self.reservation_ids = list(reservation_ids)


class CampaignDriver:
    """Run a prepared stage with an append-safe journal and injected boundaries."""

    def __init__(self, *, prepare: Callable[..., dict[str, Any]] = prepare_stage,
                 validate: Callable[..., list[str]] = validate_stage_inputs,
                 activate_prepared: Callable[..., dict[str, Any]] | None = None,
                 financial_preflight: Callable[..., dict[str, Any]] | None = None,
                 settings_loader: Callable[[Path], dict[str, Any]] = load_operator_settings,
                 launch: Callable[[dict[str, Any], dict[str, Any]], Any] | None = None,
                 dispatch: Callable[[dict[str, Any]], Any] | None = None,
                 pre_admit: Callable[[list[dict[str, Any]], dict[str, Any]], Any] | None = None,
                 reconcile: Callable[[dict[str, Any], dict[str, Any]], str | None] | None = None,
                 clock: Any = None, sleep: Callable[[float], None] = time.sleep):
        self.prepare = prepare
        self.validate = validate
        self.activate_prepared = activate_prepared
        self.financial_preflight = financial_preflight
        self.settings_loader = settings_loader
        self.launch = launch or self._launch_lifecycle
        self.pre_admit = pre_admit if pre_admit is not None else (self._pre_admit_acceptance_pair if launch is None else None)
        self.dispatch = dispatch or self._dispatch_user_space
        self.reconcile = reconcile if reconcile is not None else (self._reconcile_lifecycle if launch is None else None)
        self.clock = clock or _SystemClock()
        self.sleep = sleep

    def check(self, plan_path: Path, *, settings_path: Path | None = None,
              root: Path | None = None) -> dict[str, Any]:
        root = (root or Path.cwd()).resolve()
        plan_path = Path(plan_path).resolve(strict=True)
        plan = _load_plan(plan_path)
        selected_settings = Path(settings_path or plan.get("settings", "/etc/cloud-network-benchmark/operator.yaml"))
        if not selected_settings.is_absolute():
            selected_settings = (plan_path.parent / selected_settings).resolve()
        settings = self.settings_loader(selected_settings)
        problems = self.validate(plan, settings, root)
        return {"status": "ready" if not problems else "blocked", "stage": plan.get("stage"),
                "plan": str(plan_path), "settings": str(selected_settings),
                "blockers": list(problems), "resource_creating_calls": 0,
                "identity_checks": "read_only"}

    def draft(self, plan_paths: list[Path], *, settings_path: Path | None = None,
              output_path: Path, root: Path | None = None) -> dict[str, Any]:
        """Freeze the supported acceptance + calibration batch through prepare_stage."""
        if len(plan_paths) != 2:
            raise ValueError("fixed pre-DAY draft requires exactly one acceptance plan and one calibration plan")
        root = (root or Path.cwd()).resolve()
        plans: list[dict[str, Any]] = []
        stages: list[dict[str, Any]] = []
        missing: dict[str, list[str]] = {}
        state_root = results_root = selected_settings = None
        for plan_path in plan_paths:
            plan_file = Path(plan_path).resolve(strict=True)
            plan = _load_plan(plan_file)
            selected = Path(settings_path or plan.get("settings", "/etc/cloud-network-benchmark/operator.yaml"))
            if not selected.is_absolute():
                selected = (plan_file.parent / selected).resolve()
            selected = selected.resolve(strict=True)
            if selected_settings is not None and selected != selected_settings:
                raise ValueError("the fixed batch must use one shared operator settings file")
            selected_settings = selected
            settings = self.settings_loader(selected)
            current_state, current_results = Path(settings["state_root"]), Path(settings["results_root"])
            if state_root is not None and (current_state != state_root or current_results != results_root):
                raise ValueError("the fixed batch must use one shared private state root and results root")
            state_root, results_root = current_state, current_results
            plans.append({"path": str(plan_file), "stage": plan.get("stage"), "plan": plan})
            parameters = inspect.signature(self.prepare).parameters.values()
            if not any(parameter.name == "draft" or parameter.kind is inspect.Parameter.VAR_KEYWORD
                       for parameter in parameters):
                raise ValueError("production prepare_stage draft hook is not installed")
            stage = self.prepare(plan, settings, root, state_root, results_root, self.clock, draft=True)
            if not isinstance(stage, dict) or stage.get("status") != "DRAFT_UNAPPROVED":
                raise ValueError("production prepare_stage did not return a DRAFT_UNAPPROVED stage")
            if stage.get("stage") != plan.get("stage"):
                raise ValueError("production preparer returned a stage different from its plan")
            stage_id = str(stage.get("stage_id"))
            stages.append(stage)
            missing[stage_id] = stage.get("pending_prerequisites", [])
        if {item["stage"] for item in plans} != {"acceptance", "calibration"}:
            raise ValueError("fixed pre-DAY batch contains only acceptance and calibration stages")
        validate_fixed_batch_scope(stages)
        if len({stage["stage_id"] for stage in stages}) != 2:
            raise ValueError("production preparer must allocate distinct stable stage IDs")
        return create_draft_bundle(plan={"plans": plans, "batch_id": "fixed-pre-day-v1"},
            stages=stages, output_path=output_path, missing_prerequisites=missing,
            preparer=self.prepare)

    def activate_frozen_draft(self, draft_path: Path, *, stage_id: str,
                              settings_path: Path | None = None,
                              root: Path | None = None,
                              execute: bool = False) -> dict[str, Any]:
        """Preflight then activate one frozen stage; preparation is never repeated.

        The production activation hook must invoke the supplied ``preflight``
        callback with its in-memory validated runtime DTOs before writing any
        approval evidence or READY state.
        """
        draft_path = Path(draft_path).absolute()
        draft = load_and_verify_draft(draft_path)
        if any(Path(draft_path).with_suffix(Path(draft_path).suffix + f".{sid}.consumed.json").exists()
               for sid in [stage_id]):
            raise ValueError(f"campaign draft stage was already activated or dispatched: {stage_id}")
        stage = next((row for row in draft["stages"] if row.get("stage_id") == stage_id), None)
        if stage is None:
            raise ValueError("stage_id is not part of this fixed batch draft")
        settings_file = Path(settings_path or "/etc/cloud-network-benchmark/operator.yaml").resolve(strict=True)
        settings = self.settings_loader(settings_file)
        if not execute:
            return {"status": "blocked", "stage_id": stage_id,
                    "blockers": list(draft["missing_prerequisites"].get(stage_id, [])),
                    "execution_requested": False, "resource_creating_calls": 0}
        if self.activate_prepared is None:
            try:
                from .campaign_inputs import activate_prepared_stage
            except ImportError as exc:
                raise ValueError("production frozen-stage activation hook is not installed") from exc
            activate = activate_prepared_stage
        else:
            activate = self.activate_prepared
        try:
            activation_parameters = inspect.signature(activate).parameters.values()
        except (TypeError, ValueError):
            activation_parameters = ()
        if not any(parameter.name == "preflight" or parameter.kind is inspect.Parameter.VAR_KEYWORD
                   for parameter in activation_parameters):
            raise ValueError("production activation hook must support financial preflight before publishing approval evidence")
        root = (root or Path.cwd()).resolve()
        state_root = Path(stage["state_root"]).resolve(strict=True)
        journal_path = state_root / stage_id / "campaign-journal.json"
        with _file_lock(state_root / ".campaign-driver.lock", blocking=False):
            for previous in self.status(state_root):
                if previous["entries"] and any(row["state"] in {"scheduled", "starting", "running", "archive_pending_validation"}
                                                 for row in previous["entries"].values()):
                    raise RuntimeError("another campaign stage is still active or awaiting operator action")
            draft = load_and_verify_draft(draft_path)
            if self.financial_preflight is None:
                self._financial_card_source(draft, settings, stage)
            preflight_result: dict[str, Any] | None = None
            preflight_calls = 0

            def preflight(candidate: dict[str, Any], runtime_configs: dict[str, dict[str, Any]] | None = None):
                nonlocal preflight_result, preflight_calls
                preflight_calls += 1
                if preflight_calls != 1:
                    raise ValueError("activation must run financial preflight exactly once before publishing evidence")
                for evidence_path in draft.get("pending_evidence_paths", {}).get(stage_id, []):
                    evidence = Path(evidence_path)
                    if evidence.exists() or evidence.is_symlink():
                        raise ValueError("approval evidence was published before financial preflight")
                if not isinstance(candidate, dict) or _stage_identity(candidate) != draft["stage_identities"][
                        next(i for i, row in enumerate(draft["stages"]) if row["stage_id"] == stage_id)]:
                    raise ValueError("activation preflight candidate changed the frozen draft identity")
                preflight_result = self._validate_activated_stage_finances(
                    candidate, draft, settings, root, runtime_configs=runtime_configs)
                return dict(preflight_result)

            activated = activate(prepared=stage, settings=settings, root=root, preflight=preflight)
            if preflight_calls != 1 or preflight_result is None:
                raise ValueError("production activation did not complete financial preflight before evidence publication")
            revalidated = load_and_verify_draft(draft_path, allow_evidence_for=stage_id)
            if revalidated["draft_sha256"] != draft["draft_sha256"]:
                raise ValueError("frozen draft changed while activation was being checked")
            if not isinstance(activated, dict):
                raise ValueError("production activation hook must return the activated frozen stage")
            stage = activated
            verify_activation(draft, stage_id, stage)
            if draft.get("expected_dispatch_policies", {}).get(stage_id) is not None:
                _validate_acceptance_dispatch_policy(stage.get("dispatch_policy"))
            finance = preflight_result
            if Path(stage["state_root"]).resolve(strict=True) != state_root or stage["stage_id"] != stage_id:
                raise ValueError("activation changed the frozen private state root or stage ID")
            stage["authorization_binding_sha256"] = finance["financial_binding_sha256"]
            stage["authorization_source_sha256"] = finance["authorization_source_sha256"]
            stage["repository_root"] = str(root)
            stage["operator_log_path"] = str(settings.get("operator_log_path") or state_root / "campaign.log")
            _write_json(state_root / stage_id / "activated-stage.json", stage)
            journal = self._load_or_create_journal(stage, journal_path)
            pointer = state_root / "active-stage.json"
            _write_json(pointer, {"stage_id": stage_id,
                "stage_path": str(journal_path.parent / "prepared-stage.json"),
                "activation_status": ACTIVATED_STATUS,
                "authorization_binding_sha256": stage["authorization_binding_sha256"]})
            mark_consumed(draft_path, draft, stage_id=stage_id,
                          activated_stage_digest=_stage_digest(stage))
        dispatched = self.dispatch(stage)
        _append_log(stage, f"frozen stage {stage_id} dispatched; {len(stage['entries'])} planned entries")
        return {"status": "dispatched", "stage_id": stage_id,
                "journal": str(journal_path), "entries": len(journal["entries"]),
                "supervisor": dispatched.get("supervisor", "unknown"), "dispatch": dispatched,
                "frozen_draft_sha256": draft["draft_sha256"]}

    @staticmethod
    def _financial_card_source(draft: dict[str, Any], settings: dict[str, Any],
                               stage: dict[str, Any]):
        from .financial_controls import FinancialAuthorization

        declarations = {**settings.get("approvals", {}),
                        **settings.get("stage_inputs", {}).get(stage["stage"], {}).get("approvals", {})}
        declaration = declarations.get("paid_authorization")
        if not isinstance(declaration, dict) or declaration.get("confirmed") is not True:
            raise ValueError("a genuine paid_authorization source is required before stage activation")
        authority_path = Path(declaration.get("source", ""))
        if (not authority_path.is_absolute() or ".." in authority_path.parts
                or any(parent.is_symlink() for parent in (authority_path, *authority_path.parents))
                or not authority_path.is_file()):
            raise ValueError("paid_authorization source must be a real non-symlink file")
        authority_bytes = authority_path.read_bytes()
        authority_sha256 = hashlib.sha256(authority_bytes).hexdigest()
        card = FinancialAuthorization.model_validate_json(authority_bytes)
        expected_by_campaign: dict[str, dict[str, str]] = {}
        expected_run_ids: set[str] = set()
        for frozen_stage in draft["stages"]:
            for entry in frozen_stage["entries"]:
                parent = json.loads(Path(entry["parent_manifest_path"]).read_text(encoding="utf-8"))
                campaign_id = parent.get("campaign_id")
                run_ids = dict(entry.get("run_ids", {}))
                if (not isinstance(campaign_id, str) or not run_ids
                        or campaign_id in expected_by_campaign):
                    raise ValueError("frozen financial batch contains duplicate or incomplete parent identities")
                expected_by_campaign[campaign_id] = run_ids
                expected_run_ids.update(run_ids.values())
        card_by_campaign = {row.get("campaign_id"): row for row in card.entries}
        if (len(card.entries) != len(expected_by_campaign)
                or set(card_by_campaign) != set(expected_by_campaign)
                or any(card_by_campaign[campaign_id].get("run_ids") != run_ids
                       for campaign_id, run_ids in expected_by_campaign.items())
                or card.approved_run_count != len(expected_run_ids)
                or {run for row in card.entries for run in row.get("run_ids", {}).values()} != expected_run_ids
                or card.ledger_scope_id != stage.get("scope_id")
                or card.batch_id != draft.get("plan", {}).get("batch_id")):
            raise ValueError("FinancialAuthorization entries do not cover the exact frozen fixed batch")
        return authority_path, authority_sha256

    def _validate_activated_stage_finances(self, stage: dict[str, Any], draft: dict[str, Any],
                                           settings: dict[str, Any], root: Path, *,
                                           runtime_configs: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        if self.financial_preflight is not None:
            return self.financial_preflight(stage=stage, draft=draft, settings=settings, root=root)
        from .admission import load_cost_inputs
        from .config import resolve_campaign
        from .financial_controls import digest, validate_paid_financial_plan
        from .lifecycle_runtime import build_admission_request, load_initialized_campaign
        from .scheduling import load_schedule

        authority_path, authority_sha256 = self._financial_card_source(draft, settings, stage)
        if not isinstance(runtime_configs, dict) or set(runtime_configs) != {
                entry["entry_id"] for entry in stage.get("entries", [])}:
            raise ValueError("activation preflight requires in-memory runtime configs for every frozen entry")
        validated_bindings = []
        authorization_ref = {"source_path": str(authority_path), "source_sha256": authority_sha256}
        for entry in stage["entries"]:
            config_path = Path(entry["config_path"])
            config_data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            schedule_entry = None
            schedule_path = Path(entry["schedule_path"]) if entry.get("schedule_path") else None
            if schedule_path:
                rows = load_schedule(schedule_path, config_paths={config_data["config_id"]: config_path},
                                     repository_root=root)
                matches = [row for row in rows if row.entry_id == entry.get("row_id")]
                if len(matches) != 1:
                    raise ValueError(f"{entry['entry_id']} has no unique frozen schedule row")
                schedule_entry = matches[0]
            campaign, _ = resolve_campaign(config_path, root, schedule_entry=schedule_entry)
            parent, children = load_initialized_campaign(Path(entry["parent_manifest_path"]),
                repository_root=root, config_path=config_path, schedule_path=schedule_path)
            runtime = runtime_configs[entry["entry_id"]]
            if not isinstance(runtime, dict):
                raise ValueError(f"{entry['entry_id']} in-memory preflight runtime is invalid")
            runtime_source = runtime.get("runtime_source", {})
            if (runtime_source.get("path") != str(Path(entry["runtime_config_path"]).resolve())
                    or runtime_source.get("sha256") != entry.get("runtime_config_sha256")):
                raise ValueError(f"{entry['entry_id']} preflight runtime does not match its frozen runtime source")
            runtime["approval_evidence"] = {"paid_authorization": authorization_ref}
            costs = load_cost_inputs(Path(entry["cost_inputs_path"]))
            request, plan = build_admission_request(campaign=campaign, parent=parent, children=children,
                costs=costs, runtime=runtime, controller_session_id=stage["controller_session_id"])
            snapshot = validate_paid_financial_plan(plan, request, scope_id=stage["scope_id"],
                                                     now=_now(self.clock))
            if snapshot is None:
                raise ValueError(f"{entry['entry_id']} is not a paid-request financial plan")
            validated_bindings.append({"entry_id": entry["entry_id"],
                "campaign_id": request.campaign_id, "binding_sha256": digest(snapshot["binding"]),
                "authority_sha256": snapshot["authority"]["sha256"]})
        return {"financial_binding_sha256": digest(validated_bindings),
                "authorization_source_sha256": authority_sha256,
                "validated_entries": validated_bindings}

    def start(self, plan_path: Path, *, settings_path: Path | None = None,
              root: Path | None = None, execute: bool = False) -> dict[str, Any]:
        checked = self.check(plan_path, settings_path=settings_path, root=root)
        if checked["status"] != "ready" or not execute:
            checked["execution_requested"] = bool(execute)
            return checked
        root = (root or Path.cwd()).resolve()
        plan_path = Path(plan_path).resolve(strict=True)
        plan = _load_plan(plan_path)
        selected_settings = Path(settings_path or plan.get("settings", "/etc/cloud-network-benchmark/operator.yaml"))
        if not selected_settings.is_absolute():
            selected_settings = (plan_path.parent / selected_settings).resolve()
        settings = self.settings_loader(selected_settings)
        state_root, results_root = Path(settings["state_root"]), Path(settings["results_root"])
        with _file_lock(state_root / ".campaign-driver.lock", blocking=False):
            for previous in self.status(state_root):
                if previous["entries"] and any(row["state"] in {"scheduled", "starting", "running", "archive_pending_validation"}
                                                 for row in previous["entries"].values()):
                    raise RuntimeError("another campaign stage is still active or awaiting operator action")
            stage = self.prepare(plan, settings, root, state_root, results_root, self.clock)
            stage["repository_root"] = str(root)
            stage["operator_log_path"] = str(settings.get("operator_log_path") or state_root / "campaign.log")
            journal_path = state_root / stage["stage_id"] / "campaign-journal.json"
            journal = self._load_or_create_journal(stage, journal_path)
            pointer = state_root / "active-stage.json"
            _write_json(pointer, {"stage_id": stage["stage_id"], "stage_path": str(journal_path.parent / "prepared-stage.json")})
        dispatch_result = self.dispatch(stage)
        _append_log(stage, f"stage {stage['stage_id']} dispatched to user-space supervisor; {len(stage['entries'])} planned entries")
        return {"status": "dispatched", "stage_id": stage["stage_id"],
                "journal": str(journal_path), "entries": len(journal["entries"]),
                "supervisor": dispatch_result.get("supervisor", "unknown"), "dispatch": dispatch_result}

    def run(self, stage: dict[str, Any]) -> dict[str, Any]:
        if stage.get("status") == "DRAFT_UNAPPROVED":
            raise ValueError("an unapproved draft cannot enter the campaign runner")
        state_root = Path(stage["state_root"]).resolve()
        weekly_slots = _week_slot_groups(stage) if stage.get("stage") == "week" else None
        acceptance_pair = None
        journal_path = state_root / stage["stage_id"] / "campaign-journal.json"
        lock_path = journal_path.with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with _file_lock(state_root / ".campaign-driver.lock", blocking=False):
          with lock_path.open("a+") as lock:
            try:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (ImportError, BlockingIOError) as exc:
                raise RuntimeError("campaign driver is already active for this stage") from exc
            journal = self._load_or_create_journal(stage, journal_path)
            if stage.get("stage") == "acceptance":
                normal_ids = {"V-S1", "V-S3"}
                if any(journal["entries"].get(entry_id, {}).get("state") == "scheduled" for entry_id in normal_ids):
                    acceptance_pair = _acceptance_pair(stage)
            self._reconcile_ambiguous(stage, journal, journal_path)
            blocked = any((item.get("result") or {}).get("cleanup_open")
                          or (item.get("result") or {}).get("launch_uncertain")
                          or item.get("state") == "archive_pending_validation"
                          for item in journal["entries"].values())
            if stage.get("stage") == "acceptance":
                blocked |= any(item["state"] == "failed" for item in journal["entries"].values())
            if stage.get("stage") == "week":
                blocked |= any(item["state"] in {"failed", "missed"} for item in journal["entries"].values())
            entries_by_time: dict[datetime, list[dict[str, Any]]] = {}
            for entry in stage["entries"]:
                state = journal["entries"][entry["entry_id"]]["state"]
                if state == "scheduled" and not entry.get("requires_supervision"):
                    bucket = entries_by_time.setdefault(_utc(entry["launch_at"]), [])
                    bucket.append(entry)
            for launch_at in sorted(entries_by_time):
                pending = entries_by_time[launch_at]
                if blocked:
                    for entry in pending:
                        self._transition(journal, entry["entry_id"], "missed", finished_at=_now(self.clock).isoformat(),
                                         error="earlier run failed or left cleanup open; later launches are blocked")
                    _write_json(journal_path, journal)
                    continue
                now = _now(self.clock)
                pending = self._exclude_expired_slots(pending, journal, journal_path, now)
                if not pending:
                    if stage.get("stage") == "week":
                        blocked = True
                    continue
                min_free = int(stage.get("minimum_free_bytes", 0))
                if min_free:
                    free = self._free_disk(stage)
                    if free < min_free:
                        for entry in pending:
                            self._transition(journal, entry["entry_id"], "missed", finished_at=now.isoformat(),
                                             error=f"free disk {free} is below required minimum {min_free}")
                        _write_json(journal_path, journal)
                        blocked = True
                        continue
                while any(entry.get("scheduled_start") is not None for entry in pending):
                    now = _now(self.clock)
                    delay = (launch_at - now).total_seconds()
                    if delay <= 0:
                        break
                    chunk = min(delay, 60)
                    self.sleep(chunk)
                    if min_free and self._free_disk(stage) < min_free:
                        break
                if min_free and self._free_disk(stage) < min_free:
                    for entry in pending:
                        self._transition(journal, entry["entry_id"], "missed", finished_at=_now(self.clock).isoformat(),
                                         error="free disk fell below the configured minimum while waiting")
                    _write_json(journal_path, journal)
                    blocked = True
                    continue
                pending = self._exclude_expired_slots(pending, journal, journal_path, _now(self.clock))
                if not pending:
                    continue
                if min_free:
                    free = self._free_disk(stage)
                    if free < min_free:
                        for entry in pending:
                            self._transition(journal, entry["entry_id"], "missed", finished_at=_now(self.clock).isoformat(),
                                             error=f"free disk {free} is below required minimum {min_free} at launch")
                        _write_json(journal_path, journal)
                        blocked = True
                        continue
                if stage.get("stage") == "week":
                    _validate_week_dispatch_policy(stage.get("dispatch_policy"))
                if stage.get("stage") == "acceptance":
                    paired = acceptance_pair or []
                    if (len(pending) != 2 or {item["entry_id"] for item in pending}
                            != {item["entry_id"] for item in paired}):
                        for item in pending:
                            self._transition(journal, item["entry_id"], "missed",
                                finished_at=_now(self.clock).isoformat(),
                                error="paired acceptance slot is incomplete; no singleton dispatch is permitted")
                        _write_json(journal_path, journal)
                        blocked = True
                        continue
                    groups = [paired]
                elif stage.get("stage") == "week":
                    admitted = (weekly_slots or {}).get(launch_at, [])
                    if len(pending) != 2 or {item["entry_id"] for item in pending} != {
                            item["entry_id"] for item in admitted}:
                        for item in pending:
                            self._transition(journal, item["entry_id"], "missed",
                                finished_at=_now(self.clock).isoformat(),
                                error="paired weekly slot is incomplete; no singleton dispatch is permitted")
                        _write_json(journal_path, journal)
                        blocked = True
                        continue
                    groups = [admitted]
                else:
                    groups = [pending]
                for group in groups:
                    aggregate_reservation_ids: list[str] = []
                    if stage.get("stage") == "acceptance" and self.pre_admit is not None:
                        try:
                            aggregate_reservation_ids = self.pre_admit(group, stage) or []
                        except Exception as exc:
                            message = f"aggregate pre-admission failed before provider calls: {type(exc).__name__}: {exc}"
                            for entry in group:
                                self._transition(journal, entry["entry_id"], "missed",
                                    finished_at=_now(self.clock).isoformat(), error=message,
                                    result={"cleanup_open": False, "pre_admission_failed": True,
                                        "pre_admission_reservation_ids": getattr(exc, "reservation_ids", [])})
                            _write_json(journal_path, journal)
                            _append_log(stage, message)
                            blocked = True
                            break
                    if any(entry.get("scheduled_start") is not None
                            and _utc(entry["scheduled_start"]) <= _now(self.clock) for entry in group):
                        for entry in group:
                            self._transition(journal, entry["entry_id"], "missed",
                                finished_at=_now(self.clock).isoformat(),
                                error="fixed acceptance start passed during aggregate pre-admission; no start shift or backfill",
                                result={"cleanup_open": False, "pre_admission_complete": True,
                                    "pre_admission_reservation_ids": aggregate_reservation_ids})
                        _write_json(journal_path, journal)
                        blocked = True
                        break
                    for entry in group:
                        self._transition(journal, entry["entry_id"], "starting",
                            started_at=_now(self.clock).isoformat(),
                            aggregate_reservation_ids=aggregate_reservation_ids if stage.get("stage") == "acceptance" else [])
                    _write_json(journal_path, journal)
                    _append_log(stage, f"starting {', '.join(item['entry_id'] for item in group)}")
                    for entry in group:
                        self._transition(journal, entry["entry_id"], "running")
                    _write_json(journal_path, journal)
                    with ThreadPoolExecutor(max_workers=max(1, len(group))) as pool:
                        futures = {pool.submit(self.launch, entry, stage): entry for entry in group}
                        for future, entry in futures.items():
                            entry_id = entry["entry_id"]
                            try:
                                result = future.result()
                                status = result.get("status") if isinstance(result, dict) else None
                                if status not in {"complete", "failed", "archive_pending_validation"}:
                                    status = "failed"
                                result_payload = result if isinstance(result, dict) else {"returncode": result}
                                if status == "failed" and "cleanup_open" not in result_payload:
                                    result_payload["cleanup_open"] = True
                                self._transition(journal, entry_id, status, finished_at=_now(self.clock).isoformat(),
                                                 result=result_payload,
                                                 **({"error": "lifecycle did not report complete"} if status == "failed" else {}))
                            except Exception as exc:
                                self._transition(journal, entry_id, "failed", finished_at=_now(self.clock).isoformat(),
                                                 error=f"{type(exc).__name__}: {exc}",
                                                 result={"cleanup_open": True, "launch_uncertain": True})
                            _write_json(journal_path, journal)
                            if journal["entries"][entry_id]["state"] in {"complete", "failed"}:
                                self._archive_closed_entry(stage, entry, journal["entries"][entry_id])
                                _write_json(journal_path, journal)
                            _append_log(stage, f"{entry_id}: {journal['entries'][entry_id]['state']}")
                            if ((journal["entries"][entry_id].get("result") or {}).get("cleanup_open")
                                    or (journal["entries"][entry_id].get("result") or {}).get("launch_uncertain")
                                    or journal["entries"][entry_id]["state"] == "archive_pending_validation"
                                    or (stage.get("stage") == "acceptance"
                                        and journal["entries"][entry_id]["state"] == "failed")
                                    or (stage.get("stage") == "week"
                                        and journal["entries"][entry_id]["state"] == "failed")):
                                blocked = True
                    if blocked:
                        unstarted_entries = stage["entries"] if stage.get("stage") == "acceptance" else pending
                        for unstarted in unstarted_entries:
                            if journal["entries"][unstarted["entry_id"]]["state"] == "scheduled":
                                self._transition(journal, unstarted["entry_id"], "missed",
                                    finished_at=_now(self.clock).isoformat(),
                                    error="earlier acceptance attempt failed or left cleanup open")
                        _write_json(journal_path, journal)
                        break
            journal["updated_at"] = _now(self.clock).isoformat()
            _write_json(journal_path, journal)
            result = _status_payload(stage, journal)
            _finalize_exports(stage, journal, result)
            _append_log(stage, f"stage {stage['stage_id']} status {result['status']}; counts {result['counts']}")
            return result

    def reconcile_archive(self, state_root: Path, stage_id: str, entry_id: str, receipt_path: Path) -> dict[str, Any]:
        """Close an archive-pending entry only after replay artifacts and cleanup revalidate."""
        root = Path(state_root); folder = root / stage_id
        with _file_lock(root / ".campaign-driver.lock", blocking=False):
            stage = json.loads((folder / "prepared-stage.json").read_text(encoding="utf-8"))
            journal_path = folder / "campaign-journal.json"
            journal = self._load_or_create_journal(stage, journal_path)
            record = journal["entries"].get(entry_id)
            if not record or record.get("state") != "archive_pending_validation":
                raise ValueError("entry is not awaiting archive validation")
            prior = record.get("result") or {}; capsule_ref = prior.get("replay_capsule")
            if prior.get("cleanup_open") is not False or not isinstance(capsule_ref, dict):
                raise ValueError("archive-pending entry lacks closed cleanup or a source-bound capsule")
            capsule_file = Path(capsule_ref.get("path", ""))
            receipt = Path(receipt_path)
            if (capsule_file.is_symlink() or not capsule_file.is_file()
                    or _digest(capsule_file) != capsule_ref.get("sha256")
                    or receipt.is_symlink() or not receipt.is_file()):
                raise ValueError("capsule or replay receipt is missing, unsafe, or changed")
            capsule_doc = json.loads(capsule_file.read_text(encoding="utf-8"))
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            entry = next((item for item in stage["entries"] if item["entry_id"] == entry_id), None)
            if entry is None:
                raise ValueError("prepared stage no longer contains the pending entry")
            run_ids = entry.get("run_ids", {})
            repository = Path(stage["repository_root"]).resolve(strict=True)
            from .manifests import CampaignManifest, ChildRunManifest
            from .orchestration import SavedLifecycleRecovery
            from .contracts.admission import ReservationEvidence
            reservation = ReservationEvidence.model_validate(capsule_doc.get("reservation", {}))
            if (capsule_doc.get("schema_version") != 2
                    or capsule_doc.get("status") != "archive_pending_replay"
                    or capsule_ref.get("status") != "archive_pending_replay"
                    or reservation.state != "reserved"):
                raise ValueError("capsule is not a typed batch replay capsule from a reserved lifecycle")
            parent_rel = Path(capsule_doc.get("parent_path", ""))
            if parent_rel.is_absolute() or ".." in parent_rel.parts:
                raise ValueError("capsule parent path is unsafe")
            parent_path = repository / parent_rel
            if (parent_path.is_symlink() or not parent_path.is_file()
                    or _digest(parent_path) != _digest(entry.get("parent_manifest_path"))):
                raise ValueError("source parent manifest is missing or differs from the prepared entry")
            parent_doc = json.loads(parent_path.read_text(encoding="utf-8"))
            replayed_manifests = payload.get("replayed_manifests")
            if not isinstance(replayed_manifests, dict) or set(replayed_manifests) != {"parent", "children"}:
                raise ValueError("replay receipt does not bind the post-replay manifests")
            parent_binding = replayed_manifests["parent"]
            if (not isinstance(parent_binding, dict) or parent_binding.get("path") != parent_rel.as_posix()
                    or parent_binding.get("sha256") != _digest(parent_path)):
                raise ValueError("replay receipt parent manifest binding is missing or changed")
            capsule_parent = CampaignManifest.model_validate(capsule_doc.get("parent_manifest", {}))
            parent = CampaignManifest.model_validate(parent_doc)
            immutable_parent_fields = ("campaign_id", "config_id", "experiment_id", "mode", "context",
                "configuration_role", "scheduled_start", "schedule_entry", "admission", "selected_providers",
                "scenario", "config_provenance", "tool_versions", "child_runs", "created_at")
            changed_parent_fields = [field for field in immutable_parent_fields
                if getattr(parent, field) != getattr(capsule_parent, field)]
            if changed_parent_fields:
                raise ValueError("replayed parent manifest changed capsule identity fields: "
                    + ", ".join(changed_parent_fields))
            if parent.aggregate_state != "succeeded":
                raise ValueError("replayed parent manifest changed its capsule identity or lacks successful finalization")
            if (set(run_ids) != set(capsule_doc.get("archives", {}))
                    or set(run_ids) != set(capsule_doc.get("child_manifests", {}))
                    or set(run_ids) != set(capsule_doc.get("provider_results", {}))
                    or not isinstance(replayed_manifests.get("children"), dict)
                    or set(run_ids) != set(replayed_manifests["children"])):
                raise ValueError("capsule provider set differs from the prepared campaign")
            if parent.campaign_id != reservation.campaign_id:
                raise ValueError("capsule reservation campaign identity differs from its parent")
            children = {}
            for provider, run_id in run_ids.items():
                rel = Path(capsule_doc["child_paths"][provider])
                if rel.is_absolute() or ".." in rel.parts:
                    raise ValueError("capsule child path is unsafe")
                child_path = repository / rel
                if child_path.is_symlink() or not child_path.is_file():
                    raise ValueError("source child manifest is missing")
                child_doc = json.loads(child_path.read_text(encoding="utf-8"))
                child_binding = replayed_manifests["children"][provider]
                if (not isinstance(child_binding, dict) or child_binding.get("path") != rel.as_posix()
                        or child_binding.get("sha256") != _digest(child_path)):
                    raise ValueError("replay receipt child manifest binding is missing or changed")
                capsule_child = ChildRunManifest.model_validate(capsule_doc["child_manifests"][provider])
                child = ChildRunManifest.model_validate(child_doc)
                immutable_child_fields = ("config_id", "mode", "context", "run_id", "campaign_id",
                    "experiment_id", "provider", "scenario", "observation_id", "deployment_id",
                    "replaces_run_id", "scheduled_start", "schedule_entry", "measurement_units",
                    "config_provenance", "resolved_observation", "artifacts", "created_at")
                changed_child_fields = [field for field in immutable_child_fields
                    if getattr(child, field) != getattr(capsule_child, field)]
                if changed_child_fields:
                    raise ValueError("replayed child manifest changed capsule identity fields: "
                        + ", ".join(changed_child_fields))
                if child.reservation.value != reservation:
                    raise ValueError("replayed child manifest reservation differs from the capsule")
                if (child.execution_state != "succeeded" or child.cleanup_state != "succeeded"
                        or child.validation_status.data_validity != "valid"):
                    raise ValueError("replayed child manifest lacks successful cleanup and validation")
                archive = capsule_doc["archives"][provider]
                if child.run_id != run_id or archive.get("run_id") != run_id or str(child.provider) != provider:
                    raise ValueError("capsule run/provider identity differs from the prepared campaign")
                children[provider] = child
            recovery_path = Path(entry["private_root"]) / "lifecycle-recovery.json"
            if recovery_path.is_symlink() or not recovery_path.is_file():
                raise ValueError("independent lifecycle cleanup record is unavailable")
            recovery = SavedLifecycleRecovery.model_validate_json(recovery_path.read_text(encoding="utf-8"))
            recovery_rows = {str(item.provider): item for item in recovery.providers}
            reservation_id = reservation.reservation_id.value
            if (recovery.controller_session_id != stage.get("controller_session_id")
                    or recovery.attempt_id != parent.campaign_id
                    or recovery.reservation_id != reservation_id
                    or set(recovery_rows) != set(run_ids)
                    or any(item.cleanup_open or item.deployment_id != f"{run_ids[provider]}-deployment"
                           for provider, item in recovery_rows.items())):
                raise ValueError("saved lifecycle recovery does not independently prove cleanup for this exact run set")
            validations = payload.get("validation", {})
            expected_archives = {provider: {"manifest_sha256": item.get("manifest_sha256"),
                "completion_generation": item.get("completion_generation")}
                for provider, item in capsule_doc["archives"].items()}
            observed_archives = {provider: {"manifest_sha256": item.get("manifest_sha256"),
                "completion_generation": item.get("completion_generation")}
                for provider, item in payload.get("archives", {}).items()}
            if (payload.get("status") != "replayed" or payload.get("capsule_sha256") != capsule_ref.get("sha256")
                    or observed_archives != expected_archives or set(validations) != set(run_ids)
                    or payload.get("analysis", {}).get("status") != "completed"):
                raise ValueError("receipt does not bind successful replay and analysis to this capsule")
            # Re-run the canonical validator over its saved typed inputs and compare the
            # exact persisted/receipt result. Status strings alone never close a journal entry.
            from .contracts.validation import ValidatorInput, ValidationResult
            from .retrieval import RetrievalManifest
            from .validation import evaluate_comparison, validate_bundle
            result_root = Path(stage["results_root"]) / "raw"
            inputs, saved_results = {}, {}
            for provider, run_id in run_ids.items():
                bundle = result_root / str(run_id)
                input_path, result_path = bundle / "validation" / "input.json", bundle / "validation" / "result.json"
                if (bundle.is_symlink() or not bundle.is_dir() or input_path.is_symlink()
                        or result_path.is_symlink() or not input_path.is_file() or not result_path.is_file()):
                    raise ValueError("saved canonical validation inputs/results are missing")
                validator_input = ValidatorInput.model_validate_json(input_path.read_text(encoding="utf-8"))
                saved = ValidationResult.model_validate_json(result_path.read_text(encoding="utf-8"))
                child = children[provider]
                if (validator_input.identity.run_id != run_id or validator_input.provider != provider
                        or validator_input.identity.experiment_id != child.experiment_id
                        or validator_input.identity.config_id != child.config_id
                        or validator_input.identity.campaign_id != child.campaign_id
                        or validator_input.identity.deployment_id != child.deployment_id
                        or validator_input.config_sha256 != child.config_provenance.sha256
                        or validator_input.execution_state != "succeeded"
                        or validator_input.cleanup_state != "succeeded"
                        or saved.run_id != run_id or saved.validation_status.data_validity != "valid"):
                    raise ValueError("saved validator input/result identity or validity differs from the source child")
                closed_inventory = RetrievalManifest.model_validate(
                    capsule_doc["archives"][provider]["manifest"]["closed_inventory"])
                expected_inventory = {item.destination_path: item for item in closed_inventory.items}
                observed_records = {item.path: item for item in validator_input.artifact_records}
                if not set(expected_inventory) <= set(observed_records):
                    raise ValueError("typed validator artifact set omits frozen remote inventory items")
                context_rows = capsule_doc.get("controller_context_inventory", {}).get(provider)
                if not isinstance(context_rows, list):
                    raise ValueError("capsule lacks its frozen controller context inventory")
                context_inventory = {item.get("path"): item for item in context_rows if isinstance(item, dict)}
                extra_records = set(observed_records) - set(expected_inventory)
                for relative in extra_records:
                    artifact = observed_records[relative]
                    context_item = context_inventory.get(relative)
                    if context_item is not None and (artifact.size_bytes.value != context_item.get("size_bytes")
                            or artifact.sha256.value != context_item.get("sha256")):
                        raise ValueError("controller-context artifact differs from its frozen capsule inventory")
                    artifact.verify_materialized_file(bundle)
                for relative, expected in expected_inventory.items():
                    artifact = observed_records[relative]
                    if (artifact.artifact_id != expected.artifact_id
                            or artifact.size_bytes.value != expected.expected_size_bytes
                            or artifact.sha256.value != expected.expected_sha256):
                        raise ValueError("typed validator artifact identity differs from the frozen remote inventory")
                    artifact.verify_materialized_file(bundle)
                config_snapshot = Path(validator_input.config_snapshot_path)
                if config_snapshot.is_symlink() or not config_snapshot.is_file() or _digest(config_snapshot) != validator_input.config_sha256:
                    raise ValueError("saved config snapshot is missing or does not match the validated config hash")
                inputs[provider] = validator_input; saved_results[provider] = saved
            base = {provider: validate_bundle(value, evaluated_at=saved_results[provider].evaluated_at)
                    for provider, value in inputs.items()}
            replayed = evaluate_comparison(list(inputs.values()), list(base.values())) if len(inputs) > 1 else {
                next(iter(inputs.values())).identity.run_id: next(iter(base.values()))}
            for provider, value in inputs.items():
                run_id = value.identity.run_id
                stated = ValidationResult.model_validate(validations[provider].get("result", {}))
                replayed_payload = replayed[run_id].model_dump(mode="json")
                saved_payload = saved_results[provider].model_dump(mode="json")
                differences = []
                if replayed_payload != saved_payload:
                    differences.append("canonical replay")
                if stated.model_dump(mode="json") != saved_payload:
                    differences.append("receipt result")
                if validations[provider].get("status") != saved_results[provider].validation_status.model_dump(mode="json"):
                    differences.append("receipt status")
                if differences:
                    raise ValueError("receipt validation differs from canonical replay of saved native evidence: "
                        + ", ".join(differences))
            analysis = payload["analysis"]
            for path_key, hash_key in (("selection_path", "selection_sha256"), ("report_path", "report_sha256")):
                artifact = Path(analysis.get(path_key, ""))
                if artifact.is_symlink() or not artifact.is_file() or _digest(artifact) != analysis.get(hash_key):
                    raise ValueError("replay analysis artifact is missing or differs from its bound digest")
            from .analysis import analyze_selection
            analysis_result = analyze_selection(Path(analysis["selection_path"]),
                analysis_git=next(iter(children.values())).config_provenance.implementation_git)
            report_payload = json.loads(Path(analysis["report_path"]).read_text(encoding="utf-8"))
            if report_payload != analysis_result.payload:
                raise ValueError("analysis report differs from deterministic analysis of the bound selection")
            record["result"] = {**prior, "archive_replay_receipt": str(receipt.resolve()),
                "archive_replay_receipt_sha256": _digest(receipt), "archive_validation": "valid"}
            record.update(state="complete", finished_at=_now(self.clock).isoformat(), error=None)
            _write_json(journal_path, journal)
            self._archive_closed_entry(stage, entry, record); _write_json(journal_path, journal)
            return _status_payload(stage, journal)

    def reconcile_export(self, state_root: Path, stage_id: str, entry_id: str,
                         preserve_to: Path) -> dict[str, Any]:
        """Preserve and rebuild one failed, cleanup-closed local export; never dispatch work."""
        if (not stage_id or stage_id in {".", ".."} or Path(stage_id).name != stage_id
                or "/" in stage_id or "\\" in stage_id
                or not entry_id or entry_id in {".", ".."} or Path(entry_id).name != entry_id
                or "/" in entry_id or "\\" in entry_id):
            raise ValueError("stage and entry IDs must be single safe path components")
        root = Path(state_root).expanduser()
        if root.is_symlink() or not root.is_dir():
            raise ValueError("campaign state root is missing or unsafe")
        root = root.resolve(strict=True)
        folder = root / stage_id
        if folder.is_symlink() or not folder.is_dir() or folder.resolve(strict=True).parent != root:
            raise ValueError("campaign stage directory is missing or unsafe")
        with _file_lock(root / ".campaign-driver.lock", blocking=False):
            prepared = folder / "prepared-stage.json"
            journal_path = folder / "campaign-journal.json"
            if (prepared.is_symlink() or journal_path.is_symlink()
                    or not prepared.is_file() or not journal_path.is_file()):
                raise ValueError("campaign state is missing or unsafe")
            stage = json.loads(prepared.read_text(encoding="utf-8"))
            if stage.get("stage_id") != stage_id:
                raise ValueError("prepared campaign stage identity differs")
            # The journal already exists, so this canonical loader only validates
            # frozen stage/input digests; it cannot create or reset campaign state.
            journal = self._load_or_create_journal(stage, journal_path)
            record = journal.get("entries", {}).get(entry_id)
            if (not record or record.get("state") != "failed"
                    or (record.get("result") or {}).get("cleanup_open") is not False
                    or not str(record.get("export_status", "")).startswith("failed:")):
                raise ValueError("export repair requires a failed entry with cleanup closed and a failed export")
            entry = next((item for item in stage.get("entries", []) if item.get("entry_id") == entry_id), None)
            if entry is None:
                raise ValueError("prepared stage no longer contains the failed entry")
            from .campaign_export import export_entry, verify_export
            results_root = Path(stage["results_root"]).expanduser()
            if results_root.is_symlink() or not results_root.is_dir():
                raise ValueError("prepared results root is missing or unsafe")
            results_root = results_root.resolve(strict=True)
            closed_root = results_root / "closed"
            entry_root = closed_root / entry_id
            export_root = entry_root / "export"
            if (closed_root.is_symlink() or entry_root.is_symlink() or export_root.is_symlink()
                    or not closed_root.is_dir() or not entry_root.is_dir() or not export_root.is_dir()
                    or closed_root.resolve(strict=True).parent != results_root
                    or entry_root.resolve(strict=True).parent != closed_root.resolve(strict=True)
                    or export_root.resolve(strict=True).parent != entry_root.resolve(strict=True)):
                raise ValueError("failed export path is missing, symlinked, or outside the prepared results root")
            preserved = Path(preserve_to).expanduser()
            if (export_root.is_symlink() or not export_root.is_dir() or verify_export(export_root)
                    or preserved.name in {".", ".."} or preserved.parent.resolve(strict=True) != export_root.parent.resolve(strict=True)
                    or preserved.exists() or preserved.is_symlink()):
                raise ValueError("repair requires one invalid export and a new sibling preservation path")
            os.rename(export_root, preserved)
            export_entry(stage, entry, export_root)
            if not verify_export(export_root):
                record["export_status"] = "failed: rebuilt local export failed integrity check"
                _write_json(journal_path, journal)
                raise OSError("rebuilt local export failed integrity check; prior bytes remain preserved")
            record["export_status"] = "verified"
            record["export_repair_preserved"] = str(preserved)
            record["export_repaired_at"] = _now(self.clock).isoformat()
            journal["updated_at"] = _now(self.clock).isoformat()
            _write_json(journal_path, journal)
            return {"stage_id": stage_id, "entry_id": entry_id, "export_status": "verified",
                    "evidence_status": json.loads((export_root / "export-provenance.json").read_text()).get("evidence_status"),
                    "preserved_export": str(preserved), "dispatch_started": False}

    def _exclude_expired_slots(self, entries: list[dict[str, Any]], journal: dict[str, Any],
                               journal_path: Path, now: datetime) -> list[dict[str, Any]]:
        active: list[dict[str, Any]] = []
        changed = False
        for entry in entries:
            scheduled_start = entry.get("scheduled_start")
            if scheduled_start is not None and _utc(scheduled_start) <= now:
                self._transition(journal, entry["entry_id"], "missed", finished_at=now.isoformat(),
                                 error="scheduled measurement start passed; automatic backfill is disabled")
                changed = True
            else:
                active.append(entry)
        if changed:
            _write_json(journal_path, journal)
        return active

    def run_active(self, state_root: Path) -> dict[str, Any]:
        root = Path(state_root)
        pointer = json.loads((root / "active-stage.json").read_text(encoding="utf-8"))
        stage = json.loads(Path(pointer["stage_path"]).read_text(encoding="utf-8"))
        if stage.get("stage_id") != pointer.get("stage_id"):
            raise ValueError("active campaign pointer does not match its prepared stage")
        if stage.get("status") == "DRAFT_UNAPPROVED":
            raise ValueError("an unapproved campaign draft cannot be resumed or dispatched")
        if pointer.get("activation_status") is not None and (
                pointer.get("activation_status") != ACTIVATED_STATUS
                or stage.get("status") != ACTIVATED_STATUS
                or pointer.get("authorization_binding_sha256") != stage.get("authorization_binding_sha256")):
            raise ValueError("active frozen stage activation binding is missing or changed")
        if pointer.get("supervised_entry_id"):
            journal_path = Path(stage["state_root"]) / stage["stage_id"] / "campaign-journal.json"
            if not journal_path.is_file() or journal_path.is_symlink():
                raise ValueError("supervised stage journal is missing or unsafe")
            journal = self._load_or_create_journal(stage, journal_path)
            state = journal["entries"][pointer["supervised_entry_id"]]["state"]
            if state in {"starting", "running"}:
                return self.run(stage)
            if state in {"complete", "failed", "missed", "archive_pending_validation"}:
                return _status_payload(stage, journal)
            return self._run_supervised(stage, pointer["supervised_entry_id"])
        return self.run(stage)

    def continue_supervised(self, state_root: Path, stage_id: str, entry_id: str, *, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("registered failure checks require --confirm-supervision after the operator is ready")
        root = Path(state_root)
        folder = root / stage_id
        journal_path, prepared_path = folder / "campaign-journal.json", folder / "prepared-stage.json"
        stage = None
        with _file_lock(root / ".campaign-driver.lock", blocking=False):
            stage = json.loads(prepared_path.read_text(encoding="utf-8"))
            entry = next((item for item in stage["entries"] if item["entry_id"] == entry_id), None)
            if entry is None or not entry.get("requires_supervision"):
                raise ValueError("entry is not a registered supervised acceptance check")
            if not journal_path.is_file() or journal_path.is_symlink():
                raise ValueError("supervised stage journal is missing or unsafe")
            journal = self._load_or_create_journal(stage, journal_path)
            record = journal["entries"].get(entry_id)
            if not record or record["state"] != "scheduled":
                raise ValueError("supervised entry was already attempted or is not pending")
            ordered = stage["entries"]
            for earlier in ordered:
                if earlier["entry_id"] == entry_id:
                    break
                previous_state = journal["entries"][earlier["entry_id"]]["state"]
                accepted_states = {"failed"} if earlier.get("requires_supervision") else {"complete"}
                if previous_state not in accepted_states:
                    raise ValueError(f"complete prior acceptance entry {earlier['entry_id']} first")
                if earlier.get("requires_supervision"):
                    previous_record = journal["entries"][earlier["entry_id"]]
                    previous_result = previous_record.get("result") or {}
                    if previous_result.get("cleanup_open") is not False:
                        if self.reconcile is None:
                            raise ValueError(f"failure check {earlier['entry_id']} cleanup remains unproved")
                        # Explicit continuation also authorizes cleanup-only recovery
                        # for prior attempts. The callback is bound to the saved
                        # recovery/session and cannot relaunch measurements.
                        refreshed = self.reconcile(earlier, stage)
                        if (not isinstance(refreshed, dict) or refreshed.get("cleanup_open") is not False
                                or refreshed.get("status") not in {"complete", "failed"}):
                            raise ValueError(f"failure check {earlier['entry_id']} cleanup remains unproved")
                        previous_record["result"] = {**previous_result, **refreshed}
                        previous_record["error"] = previous_record.get("error") or "registered failure check requires operator review"
                        self._archive_closed_entry(stage, earlier, previous_record)
                        _write_json(journal_path, journal)
                    if previous_record.get("export_status") != "verified":
                        raise ValueError(f"failure check {earlier['entry_id']} lacks closed cleanup and verified evidence")
            if entry.get("reused_failure_check") is not None:
                _validate_reused_failure_entry(entry)
                record.update(state="failed", finished_at=_now(self.clock).isoformat(),
                    result={"status": "failed", "cleanup_open": False,
                        "measurement_reused": True,
                        "reused_failure_evidence_sha256": entry["reused_failure_check"]["source_sha256"]},
                    error="registered failure outcome reused from exact source-bound completed evidence",
                    export_status="verified", supervision_confirmed_at=_now(self.clock).isoformat())
                _write_json(journal_path, journal)
                _append_log(stage, f"reused completed failure evidence for {entry_id}; no measurement relaunched")
                return {"status": "reused", "stage_id": stage_id, "entry_id": entry_id,
                    "source_sha256": entry["reused_failure_check"]["source_sha256"], "dispatch": None}
            record["supervision_confirmed_at"] = _now(self.clock).isoformat()
            pointer = root / "active-stage.json"
            _write_json(pointer, {"stage_id": stage_id, "stage_path": str(prepared_path),
                                  "supervised_entry_id": entry_id})
            _append_log(stage, f"supervised attempt {entry_id} acknowledged; dispatching service")
        dispatch = self.dispatch(stage)
        return {"status": "dispatched", "stage_id": stage_id, "entry_id": entry_id,
                "dispatch": dispatch, "supervisor_result": "pending_review"}

    def _run_supervised(self, stage: dict[str, Any], entry_id: str) -> dict[str, Any]:
        state_root = Path(stage["state_root"])
        journal_path = state_root / stage["stage_id"] / "campaign-journal.json"
        with _file_lock(state_root / ".campaign-driver.lock", blocking=False):
            with _file_lock(journal_path.with_suffix(".lock"), blocking=False):
                if not journal_path.is_file() or journal_path.is_symlink():
                    raise ValueError("supervised stage journal is missing or unsafe")
                journal = self._load_or_create_journal(stage, journal_path)
                entry = next((item for item in stage["entries"] if item["entry_id"] == entry_id), None)
                if entry is None or not entry.get("requires_supervision"):
                    raise ValueError("active service pointer references a non-supervised entry")
                if journal["entries"][entry_id]["state"] != "scheduled":
                    raise ValueError("supervised entry was already attempted; no automatic retry")
                if self._low_disk(stage):
                    self._transition(journal, entry_id, "missed", finished_at=_now(self.clock).isoformat(),
                                     error="free disk is below the configured minimum")
                else:
                    self._transition(journal, entry_id, "starting", started_at=_now(self.clock).isoformat())
                    _write_json(journal_path, journal)
                    self._transition(journal, entry_id, "running")
                    _write_json(journal_path, journal)
                    try:
                        result = self.launch(entry, stage)
                        status = result.get("status") if isinstance(result, dict) else None
                        status = status if status in {"complete", "failed", "archive_pending_validation"} else "failed"
                        result_payload = result if isinstance(result, dict) else {"returncode": result}
                        if status == "failed" and "cleanup_open" not in result_payload:
                            result_payload["cleanup_open"] = True
                        self._transition(journal, entry_id, status, finished_at=_now(self.clock).isoformat(),
                                         result=result_payload,
                                         supervisor_result="pending_review")
                        if result_payload.get("cleanup_open"):
                            for later in stage["entries"]:
                                later_record = journal["entries"][later["entry_id"]]
                                if later.get("requires_supervision") and later_record["state"] == "scheduled":
                                    later_record["blocked_reason"] = "cleanup_pending"
                    except Exception as exc:
                        self._transition(journal, entry_id, "failed", finished_at=_now(self.clock).isoformat(),
                                         error=f"{type(exc).__name__}: {exc}",
                                         result={"cleanup_open": True, "launch_uncertain": True},
                                         supervisor_result="pending_review")
                _write_json(journal_path, journal)
                if journal["entries"][entry_id]["state"] in {"complete", "failed"}:
                    self._archive_closed_entry(stage, entry, journal["entries"][entry_id])
                    _write_json(journal_path, journal)
                status = _status_payload(stage, journal)
                if not any(item["state"] in {"scheduled", "starting", "running", "archive_pending_validation"}
                           for item in journal["entries"].values()):
                    _finalize_exports(stage, journal, status)
                _append_log(stage, f"supervised attempt {entry_id} ended {journal['entries'][entry_id]['state']}; review pending")
                return status

    def status(self, state_root: Path, stage_id: str | None = None) -> list[dict[str, Any]]:
        root = Path(state_root)
        journals = [root / stage_id / "campaign-journal.json"] if stage_id else sorted(root.glob("*/campaign-journal.json"))
        output = []
        for path in journals:
            if path.is_file() and not path.is_symlink():
                value = json.loads(path.read_text(encoding="utf-8"))
                prepared_path = path.parent / "prepared-stage.json"
                prepared = json.loads(prepared_path.read_text(encoding="utf-8")) if prepared_path.is_file() else {}
                counts = {state: sum(row.get("state") == state for row in value.get("entries", {}).values())
                          for state in ("scheduled", "starting", "running", "archive_pending_validation", "complete", "failed", "missed")}
                counts["expected"] = len(value.get("entries", {}))
                pending_supervision = any(row.get("blocked_reason") in {"supervision_required", "cleanup_pending"}
                                          and row.get("state") == "scheduled"
                                          for row in value.get("entries", {}).values())
                stage_status = ("active" if counts["starting"] + counts["running"] else
                                "awaiting_operator" if pending_supervision else
                                "archive_pending_validation" if counts["archive_pending_validation"] else
                                "active" if counts["scheduled"] else
                                "failed" if counts["failed"] else
                                "missed" if counts["missed"] else "complete")
                ordered_entries = prepared.get("entries", [])
                current_entry = next((entry for entry in ordered_entries
                                      if value.get("entries", {}).get(entry["entry_id"], {}).get("state")
                                      in {"starting", "running"}), None)
                next_entry = current_entry or next((entry for entry in ordered_entries
                                   if value.get("entries", {}).get(entry["entry_id"], {}).get("state")
                                   == "scheduled"), None)
                exports = {key: row.get("export_status", "pending")
                           for key, row in value.get("entries", {}).items()}
                backups = {key: row.get("backup_status", "pending")
                           for key, row in value.get("entries", {}).items()
                           if row.get("backup_status") is not None or prepared.get("backup_destination")}
                notifications = {key: row.get("notification_status", "pending")
                                 for key, row in value.get("entries", {}).items()
                                 if row.get("notification_status") is not None or prepared.get("notification_hook")}
                summary = {}
                results_root = Path(prepared["results_root"]) if prepared.get("results_root") else None
                summary_path = results_root / "stage-summary.json" if results_root else None
                if summary_path and summary_path.is_file() and not summary_path.is_symlink():
                    try:
                        candidate = json.loads(summary_path.read_text(encoding="utf-8"))
                        if candidate.get("stage_id") == value.get("stage_id"):
                            summary = candidate
                    except (OSError, ValueError):
                        summary = {"status": "unavailable", "reason": "summary could not be read"}
                if summary.get("export_status"):
                    exports["stage"] = summary["export_status"]
                if summary.get("notification_status"):
                    notifications["stage"] = summary["notification_status"]
                elif prepared.get("notification_hook"):
                    marker = path.parent / "completion-notification.json"
                    if marker.is_file() and not marker.is_symlink():
                        try:
                            notifications["stage"] = json.loads(marker.read_text(encoding="utf-8")).get("status", "attempted")
                        except (OSError, ValueError):
                            notifications["stage"] = "attempted"
                    else:
                        notifications["stage"] = "pending"
                output.append({"stage_id": value["stage_id"], "updated_at": value.get("updated_at"),
                               "stage": prepared.get("stage"), "status": stage_status,
                               "current_or_next_entry": next_entry.get("entry_id") if next_entry else None,
                               "next_slot": next_entry.get("scheduled_start") or next_entry.get("launch_at") if next_entry else None,
                               "counts": counts, "free_disk_bytes": _free_bytes(prepared.get("results_root")),
                               "cleanup_liabilities": sum(bool((row.get("result") or {}).get("cleanup_open"))
                                                          for row in value.get("entries", {}).values()),
                               "exports": exports, "backups": backups, "notifications": notifications,
                               "stage_summary": summary,
                               "entries": value["entries"], "journal": str(path),
                               "results_root": prepared.get("results_root")})
        return output

    @staticmethod
    def logs(path: Path, *, follow: bool = False, sleep: Callable[[float], None] = time.sleep):
        target = Path(path)

        def open_log():
            if target.is_symlink():
                raise OSError(f"refusing to follow a symlinked controller log: {target}")
            return target.open("r", encoding="utf-8", errors="replace")

        stream = open_log()
        try:
            while True:
                line = stream.readline()
                if line:
                    yield line
                elif not follow:
                    return
                else:
                    sleep(1)
                    try:
                        named = target.stat()
                        opened = os.fstat(stream.fileno())
                    except FileNotFoundError:
                        continue
                    if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
                        # Rotation renames the old inode and creates a new path.
                        # Follow the path like tail -F so new controller events
                        # remain visible after the original descriptor reaches EOF.
                        stream.close()
                        stream = open_log()
        finally:
            stream.close()

    def _load_or_create_journal(self, stage: dict[str, Any], path: Path) -> dict[str, Any]:
        if path.is_file():
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.get("stage_id") != stage["stage_id"] or set(value.get("entries", {})) != {
                    entry["entry_id"] for entry in stage["entries"]}:
                raise ValueError("saved campaign journal does not match this prepared stage")
            frozen = value.get("input_digests", {})
            current = {entry["entry_id"]: _entry_digests(entry) for entry in stage["entries"]}
            if frozen != current or value.get("stage_sha256") != _stage_digest(stage):
                raise ValueError("prepared campaign inputs changed after journaling; do not reuse this stage")
            return value
        now = _now(self.clock).isoformat()
        prepared_path = path.parent / "prepared-stage.json"
        if not prepared_path.exists():
            _write_json(prepared_path, stage)
        elif json.loads(prepared_path.read_text(encoding="utf-8")).get("stage_id") != stage["stage_id"]:
            raise ValueError("prepared stage record does not match this campaign")
        value = {"schema_version": 1, "stage_id": stage["stage_id"], "created_at": now,
                 "updated_at": now, "entries": {entry["entry_id"]: {
                     "state": "scheduled", "scheduled_start": entry.get("scheduled_start"),
                     "launch_at": entry["launch_at"], "started_at": None, "finished_at": None,
                     "result": None, "error": None,
                     "blocked_reason": "supervision_required" if entry.get("requires_supervision") else None}
                     for entry in stage["entries"]},
                 "input_digests": {entry["entry_id"]: _entry_digests(entry) for entry in stage["entries"]},
                 "stage_sha256": _stage_digest(stage)}
        _write_json(path, value)
        return value

    def _reconcile_ambiguous(self, stage: dict[str, Any], journal: dict[str, Any], path: Path) -> None:
        by_id = {item["entry_id"]: item for item in stage["entries"]}
        for entry_id, record in journal["entries"].items():
            # A terminal factory/lifecycle failure may have durable cleanup
            # evidence completed after the campaign journal was written. Replay
            # that evidence as cleanup-only while retaining FAILED and never
            # dispatching or resuming measurement for this entry.
            prior_result = record.get("result") or {}
            if record.get("state") == "failed" and prior_result.get("cleanup_open") is True:
                recovered = self.reconcile(by_id[entry_id], stage) if self.reconcile else None
                if (isinstance(recovered, dict) and recovered.get("status") == "failed"
                        and recovered.get("cleanup_open") is False
                        and recovered.get("reconciliation") == "cleanup_complete"):
                    record["result"] = {**prior_result, **recovered,
                        "cleanup_open": False, "execution": "not_resumed"}
                    record["cleanup_reconciled_at"] = _now(self.clock).isoformat()
                    self._archive_closed_entry(stage, by_id[entry_id], record)
                continue
            if record["state"] not in {"starting", "running"}:
                continue
            recovered = self.reconcile(by_id[entry_id], stage) if self.reconcile else None
            recovered_result = recovered if isinstance(recovered, dict) else None
            recovered_state = recovered_result.get("status") if recovered_result else recovered
            if isinstance(recovered, str) and recovered in {"complete", "failed", "missed"}:
                self._transition(journal, entry_id, recovered, finished_at=_now(self.clock).isoformat(),
                                 error=None if recovered == "complete" else "reconciled after controller restart",
                                 **({"result": {"cleanup_open": True, "reconciliation": "cleanup_unproved"}}
                                    if recovered == "failed" else {}))
            elif recovered_state in {"complete", "failed", "missed"}:
                self._transition(journal, entry_id, recovered_state, finished_at=_now(self.clock).isoformat(),
                                 error=None if recovered_state == "complete" else "reconciled after controller restart",
                                 result=recovered_result)
            else:
                self._transition(journal, entry_id, "failed", finished_at=_now(self.clock).isoformat(),
                                 error="ambiguous launch requires operator recovery; automatic relaunch is disabled",
                                 result={"cleanup_open": True, "reconciliation": "unresolved"})
            if journal["entries"][entry_id]["state"] in {"complete", "failed"}:
                self._archive_closed_entry(stage, by_id[entry_id], journal["entries"][entry_id])
        _write_json(path, journal)

    def _transition(self, journal: dict[str, Any], entry_id: str, state: str, **fields: Any) -> None:
        journal["entries"][entry_id].update(state=state, **fields)
        journal["updated_at"] = _now(self.clock).isoformat()

    @staticmethod
    def _archive_closed_entry(stage: dict[str, Any], entry: dict[str, Any], record: dict[str, Any]) -> None:
        result = record.get("result") or {}
        if result.get("cleanup_open"):
            record["export_status"] = "deferred_cleanup_open"
            if (record.get("state") == "failed" or result.get("cleanup_open")
                    or str(record.get("export_status", "")).startswith("failed:")):
                record["notification_status"] = _notify_operator_once(stage,
                    f"failure-notification-{entry['entry_id']}.json", {"stage_id": stage["stage_id"], "entry_id": entry["entry_id"],
                                         "status": "failed", "error": record.get("error"),
                                         "result": result, "export_status": record["export_status"]})
            return
        from .campaign_export import export_entry, verify_export
        export_root = Path(stage["results_root"]) / "closed" / entry["entry_id"] / "export"
        try:
            if export_root.exists():
                if not verify_export(export_root):
                    raise OSError(f"existing closed export failed integrity check: {export_root}")
            else:
                export_entry(stage, entry, export_root)
            if not verify_export(export_root):
                raise OSError(f"closed export failed integrity check: {export_root}")
            record["export_status"] = "verified"
            if stage.get("backup_destination"):
                backup = Path(stage["backup_destination"]).expanduser() / stage["stage_id"] / "closed" / entry["entry_id"] / "export"
                try:
                    if backup.exists() or backup.is_symlink():
                        if not verify_export(backup):
                            raise OSError(f"existing backup failed integrity check: {backup}")
                    else:
                        backup.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copytree(export_root, backup, copy_function=shutil.copy2)
                    for item in export_root.rglob("*"):
                        if item.is_file() and _digest(item) != _digest(backup / item.relative_to(export_root)):
                            raise OSError(f"backup checksum differs: {item.relative_to(export_root)}")
                    if not verify_export(backup):
                        raise OSError(f"backup failed integrity check: {backup}")
                    record["backup_status"] = "verified"
                except (OSError, ValueError, KeyError) as exc:
                    record["backup_status"] = f"failed: {type(exc).__name__}: {exc}"
        except (OSError, ValueError, KeyError) as exc:
            record["export_status"] = f"failed: {type(exc).__name__}: {exc}"
        if (record.get("state") == "failed"
                or str(record.get("export_status", "")).startswith("failed:")
                or str(record.get("backup_status", "")).startswith("failed:")):
            record["notification_status"] = _notify_operator_once(stage,
                f"failure-notification-{entry['entry_id']}.json", {"stage_id": stage["stage_id"], "entry_id": entry["entry_id"],
                                     "status": "failed", "error": record.get("error"),
                                     "result": result, "export_status": record.get("export_status"),
                                     "backup_status": record.get("backup_status")})

    @staticmethod
    def _low_disk(stage: dict[str, Any]) -> bool:
        minimum = int(stage.get("minimum_free_bytes", 0))
        if not minimum:
            return False
        try:
            return CampaignDriver._free_disk(stage) < minimum
        except OSError:
            return True

    @staticmethod
    def _free_disk(stage: dict[str, Any]) -> int:
        return shutil.disk_usage(Path(stage["results_root"])).free

    def _pre_admit_acceptance_pair(self, entries: list[dict[str, Any]], stage: dict[str, Any]) -> list[str]:
        """Reserve both complete three-provider liabilities before either lifecycle starts."""
        if [entry.get("entry_id") for entry in entries] != ["V-S1", "V-S3"]:
            raise ValueError("acceptance pre-admission requires the complete V-S1/V-S3 pair")
        from .admission import BudgetLedger, load_cost_inputs
        from .config import resolve_campaign
        from .lifecycle_runtime import build_admission_request, load_initialized_campaign, load_runtime_config

        root = Path(stage["repository_root"]).resolve(strict=True)
        reservations = []
        ledger = None
        for entry in entries:
            config_path = Path(entry["config_path"])
            runtime_path = Path(entry["runtime_config_path"])
            parent_path = Path(entry["parent_manifest_path"])
            runtime = load_runtime_config(runtime_path, selected_providers={"aws", "azure", "gcp"})
            parent, children = load_initialized_campaign(parent_path, repository_root=root,
                config_path=config_path, schedule_path=None)
            campaign, _ = resolve_campaign(config_path, root)
            costs = load_cost_inputs(Path(entry["cost_inputs_path"]))
            if ledger is None:
                ledger = BudgetLedger(runtime["ledger"]["path"], scope_id=runtime["ledger"]["scope_id"],
                    utc_now=lambda: _now(self.clock), monotonic_now=getattr(self.clock, "monotonic", time.monotonic),
                    controller_session_id=stage["controller_session_id"])
                if not ledger.path.exists():
                    if runtime["ledger"]["initialize_if_missing"] is not True:
                        raise ValueError("acceptance shared ledger is missing and initialization is not authorized")
                    ledger.initialize(costs)
            request, plan = build_admission_request(campaign=campaign, parent=parent, children=children,
                costs=costs, runtime=runtime, ledger_state=ledger.inspect(),
                controller_session_id=stage["controller_session_id"])
            decision = ledger.reserve(request, plan=plan)
            if decision.status != "reserved" or not decision.newly_reserved:
                reasons = ", ".join(decision.reasons) or "reservation already exists for this attempt"
                raise _AggregateAdmissionError(
                    f"paired acceptance aggregate pre-admission blocked before provider calls: {reasons}", reservations)
            reservations.append(decision.evidence.reservation_id.value)
        if len(reservations) != 2 or len(set(reservations)) != 2:
            raise _AggregateAdmissionError("paired acceptance failed to reserve two distinct complete parent liabilities", reservations)
        return reservations

    @staticmethod
    def _launch_lifecycle(entry: dict[str, Any], stage: dict[str, Any]) -> dict[str, Any]:
        import sys
        root = Path(stage.get("repository_root", Path.cwd()))
        command = [sys.executable, "-m", "cloud_network_benchmark", "lifecycle", "run",
            "--config", str(entry["config_path"]), "--cost-inputs", str(entry["cost_inputs_path"]),
            "--runtime-config", str(entry["runtime_config_path"]), "--results-root", str(stage["results_root"]),
            "--private-root", str(entry["private_root"]), "--controller-session", str(stage["controller_session_id"]),
            "--execution-scope", "paid_request", "--format", "json"]
        if entry.get("schedule_path"):
            command += ["--schedule", str(entry["schedule_path"]), "--row-id", str(entry["row_id"])]
        run_log = Path(stage["state_root"]) / stage["stage_id"] / f"{entry['entry_id']}.log"
        run_log.parent.mkdir(parents=True, exist_ok=True)
        free_start = _free_bytes(stage.get("results_root"))
        free_samples = [free_start] if free_start is not None else []
        memory_peak = 0
        with run_log.open("ab") as output:
            process = subprocess.Popen(command, cwd=root, stdout=output, stderr=subprocess.STDOUT)
            while process.poll() is None:
                try:
                    status = Path(f"/proc/{process.pid}/status").read_text(encoding="ascii")
                    resident = next((line for line in status.splitlines() if line.startswith("VmRSS:")), None)
                    if resident:
                        memory_peak = max(memory_peak, int(resident.split()[1]) * 1024)
                except (OSError, ValueError, IndexError):
                    pass
                free_sample = _free_bytes(stage.get("results_root"))
                if free_sample is not None:
                    free_samples.append(free_sample)
                time.sleep(1)
            return_code = process.returncode
        free_end = _free_bytes(stage.get("results_root"))
        if free_end is not None:
            free_samples.append(free_end)
        minimum_free = min(free_samples) if free_samples else None
        usage_delta = (max(0, free_start - minimum_free)
                       if free_start is not None and minimum_free is not None else None)
        valid_outputs = True
        for run_id in entry["run_ids"].values():
            validation = Path(stage["results_root"]) / "raw" / str(run_id) / "validation"
            try:
                validator_input = json.loads((validation / "input.json").read_text(encoding="utf-8"))
                saved_result = json.loads((validation / "result.json").read_text(encoding="utf-8"))
                valid_outputs &= (saved_result.get("run_id") == run_id
                    and saved_result.get("validation_status", {}).get("data_validity") == "valid"
                    and validator_input.get("execution_state") == "succeeded"
                    and validator_input.get("cleanup_state") == "succeeded")
            except (OSError, ValueError, TypeError):
                valid_outputs = False
        report = _last_json_object(run_log)
        providers = report.get("providers", {}) if isinstance(report, dict) else {}
        cleanup_open = not providers or any(
            item.get("cleanup_state") not in {"succeeded", "not_required"} for item in providers.values())
        lifecycle_complete = return_code == 0 and report.get("status") == "complete"
        success = return_code == 0 and valid_outputs and lifecycle_complete
        return {"status": "complete" if success else "failed", "returncode": return_code,
                "validated_outputs_present": valid_outputs, "lifecycle_status_complete": lifecycle_complete,
                "cleanup_open": cleanup_open, "provider_results": providers,
                "log": str(run_log), "peak_process_rss_bytes": memory_peak,
                "free_disk_bytes_start": free_start, "free_disk_bytes_end": free_end,
                "minimum_free_disk_bytes_during_run": minimum_free,
                "peak_observed_shared_filesystem_usage_delta_bytes": usage_delta,
                "disk_sample_scope": "shared_results_filesystem"}

    @staticmethod
    def _reconcile_lifecycle(entry: dict[str, Any], stage: dict[str, Any]) -> dict[str, Any]:
        recovery = Path(entry["private_root"]) / "lifecycle-recovery.json"
        if not recovery.is_file() or recovery.is_symlink():
            return {"status": "failed", "cleanup_open": True, "reconciliation": "missing_recovery"}
        completed = CampaignDriver._verified_completed_lifecycle(entry, stage)
        if completed is not None:
            # The native lifecycle may have finished all work and persisted valid,
            # sealed results immediately before the controller lost its journal
            # commit. Preserve that evidence and avoid a second cleanup transition.
            return completed
        root = Path(stage.get("repository_root", Path.cwd()))
        log = Path(stage["state_root"]) / stage["stage_id"] / f"{entry['entry_id']}-recovery.log"
        command = ["cloud-network-benchmark", "lifecycle", "cleanup", "--recovery", str(recovery),
                   "--controller-session", str(stage["controller_session_id"]), "--format", "json"]
        with log.open("ab") as output:
            completed = subprocess.run(command, cwd=root, stdout=output, stderr=subprocess.STDOUT, check=False)
        report = _last_json_object(log)
        providers = report.get("providers", {}) if isinstance(report, dict) else {}
        expected_providers = set(entry.get("run_ids", {}))
        # Physical closure and timing acceptance are separate outcomes. The
        # ordinary cleanup CLI can return blocked (exit 1) after proving every
        # provider absent when a deadline was late/unknown. Preserve that
        # acceptance failure while allowing the campaign journal to close its
        # cleanup liability and remain terminal/failed.
        physical_closure = (bool(expected_providers) and set(providers) == expected_providers
            and all(isinstance(item, dict) for item in providers.values())
            and all(item.get("cleanup") in {"succeeded", "not_required"}
                    and item.get("reconciled") is True
                    and item.get("errors", []) == []
                    and item.get("launch_blocked") is False
                    and item.get("cleanup_open") is False
                    and item.get("absence_proved") is True
                    and item.get("billable_resources_present") is False
                    for item in providers.values()))
        cli_status = report.get("status") if isinstance(report, dict) else None
        cli_exit_matches = ((cli_status == "complete" and completed.returncode == 0)
            or (cli_status == "blocked" and completed.returncode == 1))
        timing_only_block = (cli_status == "blocked"
            and any(item.get("acceptance_failed") is True for item in providers.values()))
        closed = (isinstance(report, dict) and report.get("execution") == "not_resumed"
            and physical_closure and cli_exit_matches)
        # Cleanup reconciliation never claims that an interrupted measurement completed.
        return {"status": "failed", "cleanup_open": not closed, "reconciliation": "cleanup_complete" if closed else "cleanup_unproved",
                "cleanup_acceptance_failed": timing_only_block,
                "cleanup_report": report}

    @staticmethod
    def _verified_completed_lifecycle(entry: dict[str, Any], stage: dict[str, Any]) -> dict[str, Any] | None:
        """Return complete only when sealed native bundles replay as valid and lifecycle cleanup is recorded."""
        try:
            run_ids = entry["run_ids"]
            if not run_ids:
                return None
            recovery = Path(entry["private_root"]) / "lifecycle-recovery.json"
            if recovery.is_symlink() or not recovery.is_file():
                return None
            from .orchestration import SavedLifecycleRecovery
            recovery_record = SavedLifecycleRecovery.model_validate_json(recovery.read_text(encoding="utf-8"))
            expected_providers = set(run_ids)
            if (recovery_record.controller_session_id != stage["controller_session_id"]
                    or {str(item.provider) for item in recovery_record.providers} != expected_providers
                    or any(item.cleanup_open for item in recovery_record.providers)):
                return None
            log = Path(stage["state_root"]) / stage["stage_id"] / f"{entry['entry_id']}.log"
            report = _last_json_object(log)
            providers = report.get("providers", {})
            if (report.get("status") != "complete" or report.get("run_ids") != run_ids
                    or set(providers) != set(run_ids)):
                return None

            from .contracts.validation import ValidatorInput, ValidationResult
            from .validation import evaluate_comparison, validate_bundle

            result_roots = Path(stage["results_root"]) / "raw"
            saved_inputs: dict[str, Any] = {}
            persisted_results: dict[str, Any] = {}
            provider_by_run: dict[str, str] = {}
            for provider, run_id in run_ids.items():
                provider_result = providers[provider]
                if provider_result.get("cleanup_state") not in {"succeeded", "not_required"}:
                    return None
                recovery_provider = next(item for item in recovery_record.providers
                                         if str(item.provider) == provider)
                if recovery_provider.deployment_id != f"{run_id}-deployment":
                    return None
                journal_path = Path(recovery_provider.journal_reference)
                if journal_path.is_symlink() or not journal_path.is_file():
                    return None
                provider_journal = json.loads(journal_path.read_text(encoding="utf-8"))
                if (provider_journal.get("run_id") != run_id
                        or provider_journal.get("cleanup_state") != "succeeded"):
                    return None
                bundle = result_roots / str(run_id)
                if bundle.is_symlink() or not bundle.is_dir():
                    return None
                index = bundle / "checksums.sha256"
                if index.is_symlink() or not index.is_file():
                    return None
                for line in index.read_text(encoding="utf-8").splitlines():
                    digest, sep, name = line.partition("  ")
                    relative = Path(name)
                    if (not sep or len(digest) != 64 or relative.is_absolute()
                            or ".." in relative.parts or any(part == "private" for part in relative.parts)):
                        return None
                    artifact = bundle / relative
                    if artifact.is_symlink() or not artifact.is_file() or _digest(artifact) != digest:
                        return None
                input_path = bundle / "validation" / "input.json"
                persisted_path = bundle / "validation" / "result.json"
                if input_path.is_symlink() or persisted_path.is_symlink():
                    return None
                validator_input = ValidatorInput.model_validate_json(input_path.read_text(encoding="utf-8"))
                persisted = ValidationResult.model_validate_json(persisted_path.read_text(encoding="utf-8"))
                if (validator_input.identity.run_id != run_id
                        or recovery_record.attempt_id != validator_input.identity.campaign_id
                        or validator_input.execution_state != "succeeded"
                        or validator_input.cleanup_state != "succeeded"
                        or persisted.run_id != run_id
                        or persisted.validation_status.data_validity != "valid"):
                    return None
                if provider_result.get("validation_status", {}).get("data_validity") != "valid":
                    return None
                saved_inputs[provider] = validator_input
                persisted_results[provider] = persisted
                provider_by_run[run_id] = provider
            base_results = [validate_bundle(value, evaluated_at=persisted_results[provider].evaluated_at)
                            for provider, value in saved_inputs.items()]
            if len(saved_inputs) > 1:
                replayed = evaluate_comparison(list(saved_inputs.values()), base_results)
            else:
                provider = next(iter(saved_inputs))
                replayed = {saved_inputs[provider].identity.run_id: base_results[0]}
            for run_id, provider in provider_by_run.items():
                if replayed[run_id].model_dump(mode="json") != persisted_results[provider].model_dump(mode="json"):
                    return None
            return {"status": "complete", "cleanup_open": False,
                    "reconciliation": "completed_validated", "provider_results": providers,
                    "validated_outputs_present": True, "lifecycle_status_complete": True}
        except (OSError, ValueError, TypeError, KeyError, ImportError):
            # Any incomplete or contradictory local evidence takes the existing
            # cleanup-only path below; it never authorizes a measurement replay.
            return None

    @staticmethod
    def _dispatch_user_space(stage: dict[str, Any]) -> dict[str, Any]:
        """Detach one user-space supervisor from the invoking SSH session.

        A session service cannot meet the Halle operating contract because it
        may require linger or sudo.  A new session, closed stdin and durable
        append-only output keep this process independent of SSH and Codex while
        deliberately retaining the documented host-reboot limitation.
        """
        import os
        import subprocess
        import sys
        state_root = Path(stage["state_root"]).resolve()
        state_root.mkdir(parents=True, exist_ok=True)
        log_path = Path(stage.get("operator_log_path") or state_root / "campaign-supervisor.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path = state_root / "campaign-supervisor.pid"
        if pid_path.is_file():
            try:
                old_pid = int(pid_path.read_text().strip())
                os.kill(old_pid, 0)
            except (ValueError, ProcessLookupError):
                pass
            else:
                raise RuntimeError(f"campaign supervisor already exists as PID {old_pid}; inspect status before resuming")
        with log_path.open("ab", buffering=0) as output:
            process = subprocess.Popen([sys.executable, "-m", "cloud_network_benchmark.campaign_supervisor",
                                        "--state-root", str(state_root)], stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
                                       close_fds=True, cwd=stage.get("repository_root") or None)
        temporary = pid_path.with_suffix(".tmp")
        temporary.write_text(f"{process.pid}\n", encoding="ascii")
        os.replace(temporary, pid_path)
        return {"supervisor": "user-space", "pid": process.pid, "started": True,
                "log": str(log_path), "host_reboot_recovery": "not provided"}


class _SystemClock:
    @staticmethod
    def utc_now() -> datetime:
        return datetime.now(timezone.utc)


def _load_plan(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ValueError("campaign plan must be a regular non-symlink file")
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("stage") not in {"acceptance", "calibration", "day", "week"}:
        raise ValueError("campaign plan must name acceptance, calibration, day, or week")
    return value


def _status_payload(stage: dict[str, Any], journal: dict[str, Any]) -> dict[str, Any]:
    counts = {state: sum(item["state"] == state for item in journal["entries"].values())
              for state in ("scheduled", "starting", "running", "archive_pending_validation", "complete", "failed", "missed")}
    counts["expected"] = len(journal["entries"])
    next_entry = next((entry for entry in stage["entries"]
                       if journal["entries"][entry["entry_id"]]["state"] == "scheduled"), None)
    awaiting = any(item.get("blocked_reason") in {"supervision_required", "cleanup_pending"}
                   and item["state"] == "scheduled"
                   for item in journal["entries"].values())
    settled = counts["scheduled"] == 0 or awaiting
    outcome = ("archive_pending_validation" if counts["archive_pending_validation"] else
               "failed" if counts["failed"] else "missed" if counts["missed"] else "complete")
    return {"stage_id": stage["stage_id"], "status": "awaiting_operator" if awaiting else outcome if settled else "active",
            "current_or_next_entry": next_entry["entry_id"] if next_entry else None,
            "counts": counts, "entries": journal["entries"],
            "free_disk_bytes": _free_bytes(stage.get("results_root")),
            "cleanup_liabilities": sum(bool((item.get("result") or {}).get("cleanup_open"))
                                        for item in journal["entries"].values()),
            "journal": str(Path(stage["state_root"]) / stage["stage_id"] / "campaign-journal.json")}


def _entry_digests(entry: dict[str, Any]) -> dict[str, str | None]:
    return {name: _digest(entry.get(name)) for name in
            ("config_path", "schedule_path", "runtime_config_path", "cost_inputs_path")}


def _stage_digest(stage: dict[str, Any]) -> str:
    frozen = {key: value for key, value in stage.items() if key not in {
        "observer_bindings_sha256", "observer_launch_receipt_path", "observer_launch_receipt_sha256"}}
    raw = json.dumps(frozen, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _last_json_object(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 1024 * 1024))
            text = stream.read().decode("utf-8", errors="replace")
        decoder = json.JSONDecoder()
        start = text.rfind("{")
        # Pretty JSON contains nested braces; locate the first object opening in the tail.
        for start in (index for index, char in enumerate(text) if char == "{"):
            try:
                payload, _ = decoder.raw_decode(text[start:])
                if isinstance(payload, dict) and "status" in payload:
                    return payload
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return {}


@contextmanager
def _file_lock(path: Path, *, blocking: bool):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as stream:
        try:
            import fcntl
            operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            fcntl.flock(stream.fileno(), operation)
        except (ImportError, BlockingIOError) as exc:
            raise RuntimeError("campaign driver is already active") from exc
        try:
            yield
        finally:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            except (ImportError, UnboundLocalError):
                pass


def _finalize_exports(stage: dict[str, Any], journal: dict[str, Any], result: dict[str, Any]) -> None:
    export_root = Path(stage["results_root"]) / "export"
    export_status = "not_ready"
    selection = None
    try:
        pending = any(item["state"] in {"scheduled", "starting", "running", "archive_pending_validation"} for item in journal["entries"].values())
        completed_entries = [] if pending else [entry for entry in stage["entries"]
                            if journal["entries"][entry["entry_id"]]["state"] in {"complete", "failed"}]
        if pending:
            export_status = "awaiting_campaign_completion"
        elif completed_entries:
            from .campaign_export import aggregate_closed_exports, verify_export
            if not export_root.exists():
                selection = aggregate_closed_exports(stage, journal, export_root)
            else:
                selection = export_root / "selection.json"
            if not verify_export(export_root):
                raise OSError(f"stage export failed integrity check: {export_root}")
            export_status = "verified" if selection and selection.is_file() else "failed: no validated selection is available"
            failed_exports = [entry["entry_id"] for entry in completed_entries
                              if (journal["entries"][entry["entry_id"]].get("export_status", "")).startswith("failed:")]
            if failed_exports:
                export_status = "failed: closed-entry exports unavailable for " + ", ".join(failed_exports)
    except (OSError, ValueError, KeyError) as exc:
        export_status = f"failed: {type(exc).__name__}: {exc}"
    result["export"] = str(selection) if selection else None
    result["export_status"] = export_status
    if export_status.startswith("failed:"):
        result["status"] = "failed"
    summary = {"stage_id": stage["stage_id"], "stage": stage["stage"], "status": result["status"],
        "counts": result["counts"], "free_disk_bytes": result.get("free_disk_bytes"),
        "cleanup_liabilities": result.get("cleanup_liabilities"), "export": result["export"],
        "export_status": export_status, "journal": result["journal"]}
    (Path(stage["results_root"]) / "stage-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result["notification_status"] = ("pending" if export_status == "awaiting_campaign_completion"
                                     else _notify_operator_once(stage, "completion-notification.json", summary))


def _free_bytes(path: Any) -> int | None:
    if not path:
        return None
    try:
        return shutil.disk_usage(Path(path)).free
    except OSError:
        return None


def _append_log(stage: dict[str, Any], message: str) -> None:
    path = stage.get("operator_log_path") or Path(stage["state_root"]) / "campaign.log"
    target = Path(path)
    if target.is_symlink():
        raise OSError(f"refusing to append to a symlinked controller log: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    _rotate_operator_log(target)
    with target.open("a", encoding="utf-8") as stream:
        stream.write(f"{_now(_SystemClock()).isoformat()} {message}\n")
        stream.flush()
        os.fsync(stream.fileno())


def _rotate_operator_log(target: Path) -> None:
    if not target.exists() or target.stat().st_size < 5 * 1024 * 1024:
        return
    oldest = target.with_name(target.name + ".3")
    if oldest.exists():
        oldest.unlink()
    for index in (2, 1):
        source = target.with_name(target.name + f".{index}")
        if source.exists():
            source.replace(target.with_name(target.name + f".{index + 1}"))
    target.replace(target.with_name(target.name + ".1"))


def _notify_operator(stage: dict[str, Any], payload: dict[str, Any]) -> str:
    if not stage.get("notification_hook"):
        return "not_configured"
    try:
        subprocess.run([str(Path(stage["notification_hook"])), json.dumps(payload, sort_keys=True)],
                       check=True, timeout=20, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"failed: {type(exc).__name__}: {exc}"
    else:
        return "sent"


def _notify_operator_once(stage: dict[str, Any], marker_name: str, payload: dict[str, Any]) -> str:
    if not stage.get("notification_hook"):
        return "not_configured"
    if not stage.get("state_root") or not stage.get("stage_id"):
        return _notify_operator(stage, payload)
    marker = Path(stage["state_root"]) / stage["stage_id"] / marker_name
    if marker.is_file() and not marker.is_symlink():
        try:
            saved = json.loads(marker.read_text(encoding="utf-8"))
            return str(saved.get("status", "attempted"))
        except (OSError, ValueError):
            return "attempted"
    # Persist an at-most-once marker before invoking an external hook so a
    # systemd restart cannot repeat a notification whose outcome was lost.
    _write_json(marker, {"status": "attempted", "created_at": _now(_SystemClock()).isoformat()})
    status = _notify_operator(stage, payload)
    _write_json(marker, {"status": status, "created_at": _now(_SystemClock()).isoformat()})
    return status
