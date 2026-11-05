"""Small durable driver for explicit, preplanned benchmark campaigns."""
from __future__ import annotations

import json
import hashlib
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


class CampaignDriver:
    """Run a prepared stage with an append-safe journal and injected boundaries."""

    def __init__(self, *, prepare: Callable[..., dict[str, Any]] = prepare_stage,
                 validate: Callable[..., list[str]] = validate_stage_inputs,
                 settings_loader: Callable[[Path], dict[str, Any]] = load_operator_settings,
                 launch: Callable[[dict[str, Any], dict[str, Any]], Any] | None = None,
                 dispatch: Callable[[dict[str, Any]], Any] | None = None,
                 reconcile: Callable[[dict[str, Any], dict[str, Any]], str | None] | None = None,
                 clock: Any = None, sleep: Callable[[float], None] = time.sleep):
        self.prepare = prepare
        self.validate = validate
        self.settings_loader = settings_loader
        self.launch = launch or self._launch_lifecycle
        self.dispatch = dispatch or self._dispatch_systemd
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
                if previous["entries"] and any(row["state"] in {"scheduled", "starting", "running"}
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
        _append_log(stage, f"stage {stage['stage_id']} dispatched to systemd; {len(stage['entries'])} planned entries")
        return {"status": "dispatched", "stage_id": stage["stage_id"],
                "journal": str(journal_path), "entries": len(journal["entries"]),
                "service": "cloud-network-benchmark-campaign.service", "dispatch": dispatch_result}

    def run(self, stage: dict[str, Any]) -> dict[str, Any]:
        state_root = Path(stage["state_root"]).resolve()
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
            self._reconcile_ambiguous(stage, journal, journal_path)
            blocked = any((item.get("result") or {}).get("cleanup_open")
                          or (item.get("result") or {}).get("launch_uncertain")
                          for item in journal["entries"].values())
            if stage.get("stage") == "acceptance":
                blocked |= any(item["state"] == "failed" for item in journal["entries"].values())
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
                groups = [[item] for item in pending] if stage.get("stage") == "acceptance" else [pending]
                for group in groups:
                    for entry in group:
                        self._transition(journal, entry["entry_id"], "starting", started_at=_now(self.clock).isoformat())
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
                                if status not in {"complete", "failed"}:
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
                                    or (stage.get("stage") == "acceptance"
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
        if pointer.get("supervised_entry_id"):
            journal_path = Path(stage["state_root"]) / stage["stage_id"] / "campaign-journal.json"
            if not journal_path.is_file() or journal_path.is_symlink():
                raise ValueError("supervised stage journal is missing or unsafe")
            journal = self._load_or_create_journal(stage, journal_path)
            state = journal["entries"][pointer["supervised_entry_id"]]["state"]
            if state in {"starting", "running"}:
                return self.run(stage)
            if state in {"complete", "failed", "missed"}:
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
                        status = status if status in {"complete", "failed"} else "failed"
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
                if not any(item["state"] in {"scheduled", "starting", "running"}
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
                          for state in ("scheduled", "starting", "running", "complete", "failed", "missed")}
                counts["expected"] = len(value.get("entries", {}))
                pending_supervision = any(row.get("blocked_reason") in {"supervision_required", "cleanup_pending"}
                                          and row.get("state") == "scheduled"
                                          for row in value.get("entries", {}).values())
                stage_status = ("active" if counts["starting"] + counts["running"] else
                                "awaiting_operator" if pending_supervision else
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
        closed = (completed.returncode == 0 and report.get("status") == "complete"
                  and bool(expected_providers) and set(providers) == expected_providers
                  and all(item.get("cleanup_open") is False and item.get("absence_proved") is True
                          for item in providers.values()))
        # Cleanup reconciliation never claims that an interrupted measurement completed.
        return {"status": "failed", "cleanup_open": not closed, "reconciliation": "cleanup_complete" if closed else "cleanup_unproved",
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
    def _dispatch_systemd(stage: dict[str, Any]) -> dict[str, Any]:
        import subprocess
        result = subprocess.run(["systemctl", "--user", "start", "cloud-network-benchmark-campaign.service"],
                                check=False, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError("could not hand campaign to systemd user service; install/enable the service and linger first: "
                               + (result.stderr.strip() or result.stdout.strip()))
        return {"service": "cloud-network-benchmark-campaign.service", "started": True}


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
              for state in ("scheduled", "starting", "running", "complete", "failed", "missed")}
    counts["expected"] = len(journal["entries"])
    next_entry = next((entry for entry in stage["entries"]
                       if journal["entries"][entry["entry_id"]]["state"] == "scheduled"), None)
    awaiting = any(item.get("blocked_reason") in {"supervision_required", "cleanup_pending"}
                   and item["state"] == "scheduled"
                   for item in journal["entries"].values())
    settled = counts["scheduled"] == 0 or awaiting
    outcome = "failed" if counts["failed"] else "missed" if counts["missed"] else "complete"
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
    raw = json.dumps(stage, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
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
        pending = any(item["state"] in {"scheduled", "starting", "running"} for item in journal["entries"].values())
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
