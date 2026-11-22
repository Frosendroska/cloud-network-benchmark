"""Unprivileged, SSH-independent campaign process and local observer."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import threading
import time
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read_only_preflight(stage: dict) -> dict:
    from .campaign_identity import check_provider_identities
    identity_errors = check_provider_identities(stage.get("observer_identity", {}))
    try:
        completed = subprocess.run(["terraform", "version", "-json"], check=False,
                                   capture_output=True, text=True, timeout=15)
        terraform = json.loads(completed.stdout) if completed.returncode == 0 else {}
        version = terraform.get("terraform_version") if isinstance(terraform, dict) else None
        terraform_ok = bool(version)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        version, terraform_ok = None, False
    expiry = stage.get("credential_valid_through_utc")
    try:
        expiry_ok = datetime.fromisoformat(str(expiry).replace("Z", "+00:00")) > datetime.now(timezone.utc)
    except (TypeError, ValueError):
        expiry_ok = False
    return {"provider_identity": "blocked" if identity_errors else "qualified",
            "provider_blockers": identity_errors, "terraform_version": version,
            "terraform_ready": terraform_ok, "credential_valid_through_utc": expiry,
            "credential_valid_through": expiry_ok,
            "status": "qualified" if not identity_errors and terraform_ok and expiry_ok else "blocked"}


def observe(state_root: Path, stop: threading.Event, interval: float = 30.0, *,
            provider_preflight: bool = False) -> None:
    """Write durable local snapshots without provider calls or guest-health claims."""
    last_preflight = 0.0
    preflight = {"status": "not_run_by_observer"}
    while not stop.is_set():
        try:
            pointer = json.loads((state_root / "active-stage.json").read_text(encoding="utf-8"))
            stage_id = pointer["stage_id"]
            stage_dir = Path(pointer["stage_path"]).parent
            stage = json.loads((stage_dir / "prepared-stage.json").read_text(encoding="utf-8"))
            journal_path = stage_dir / "campaign-journal.json"
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            entries = journal.get("entries", {})
            liabilities = []
            output_freshness = {}
            archive_status = {}
            archive_faults = []
            stale_outputs = []
            for entry_id, row in entries.items():
                result = row.get("result") or {}
                prepared_entry = next((item for item in stage.get("entries", [])
                                       if item.get("entry_id") == entry_id), {})
                progress_archives = {}
                try:
                    runtime_path = Path(prepared_entry.get("runtime_config_path", ""))
                    if runtime_path.is_file() and not runtime_path.is_symlink():
                        archive_sink = json.loads(runtime_path.read_text(encoding="utf-8")).get("archive_sink")
                        if isinstance(archive_sink, dict):
                            status_root = Path(archive_sink["archive_root"])
                            for provider, run_id in prepared_entry.get("run_ids", {}).items():
                                status_path = status_root / f"{provider}-{run_id}.status.json"
                                if status_path.is_file() and not status_path.is_symlink():
                                    saved = json.loads(status_path.read_text(encoding="utf-8"))
                                    progress_archives[provider] = {key: saved[key] for key in (
                                        "status", "verified", "verified_parts", "verified_bytes",
                                        "manifest_sha256", "completion_generation", "archive_bytes",
                                        "error", "partial_object_count") if key in saved}
                except (OSError, ValueError, KeyError, TypeError):
                    progress_archives = {}
                provider_results = result.get("provider_results", {})
                if isinstance(provider_results, dict):
                    archives = {provider: value.get("archive_status")
                        for provider, value in provider_results.items()
                        if isinstance(value, dict) and value.get("archive_status") is not None}
                    progress_archives.update(archives)
                    if archives:
                        archive_status[entry_id] = progress_archives
                    elif progress_archives:
                        archive_status[entry_id] = progress_archives
                    for provider, archive in progress_archives.items():
                        if isinstance(archive, dict):
                            state = archive.get("status")
                            if state in {"failed", "withheld"} or (
                                    state == "verified" and archive.get("verified") is not True):
                                archive_faults.append({"entry_id": entry_id, "provider": provider,
                                    "status": state, "verified": archive.get("verified")})
                if result.get("cleanup_open") is True or result.get("launch_uncertain") is True:
                    liabilities.append({"entry_id": entry_id, "cleanup_open": result.get("cleanup_open"),
                                        "launch_uncertain": result.get("launch_uncertain")})
                if row.get("state") in {"starting", "running"}:
                    latest = None
                    for run_id in next((item.get("run_ids", {}).values() for item in stage.get("entries", [])
                                        if item.get("entry_id") == entry_id), []):
                        run_root = Path(stage["results_root"]) / "raw" / str(run_id)
                        if run_root.is_dir() and not run_root.is_symlink():
                            for candidate in run_root.rglob("*"):
                                if candidate.is_file() and not candidate.is_symlink():
                                    latest = max(latest or 0, candidate.stat().st_mtime)
                    age = None if latest is None else max(0, int(time.time() - latest))
                    try:
                        started = datetime.fromisoformat(str(row.get("started_at")).replace("Z", "+00:00"))
                        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
                    except (TypeError, ValueError):
                        elapsed = 0
                    output_freshness[entry_id] = {"latest_output_age_seconds": age,
                                                  "evidence": "controller_local_files_only"}
                    if elapsed > 120 and (age is None or age > 120):
                        stale_outputs.append(entry_id)
            usage = shutil.disk_usage(state_root)
            minimum_free = int(stage.get("minimum_free_bytes", 0))
            disk_below_minimum = bool(minimum_free and usage.free < minimum_free)
            has_live_entry = any(row.get("state") in {"starting", "running"}
                                  for row in entries.values())
            guest_health = {"health": "unknown", "errors": ["admitted_guest_bindings_unavailable"]}
            if has_live_entry and stage.get("observer_bindings_path"):
                from .guest_observer import observe_bindings
                active_ids = {key for key, row in entries.items() if row.get("state") in {"starting", "running"}}
                guest_health = observe_bindings(stage, Path(stage["observer_bindings_path"]),
                                                active_entry_ids=active_ids)
            if provider_preflight and time.monotonic() - last_preflight >= 300:
                preflight = _read_only_preflight(stage)
                last_preflight = time.monotonic()
            if liabilities or stale_outputs or archive_faults or disk_below_minimum or any(
                    row.get("state") in {"failed", "missed"} for row in entries.values()):
                health = "fault"
            elif has_live_entry and guest_health.get("health") == "fault":
                health = "fault"
            elif provider_preflight and preflight.get("status") == "blocked":
                health = "fault"
            elif has_live_entry:
                health = "healthy" if guest_health.get("health") == "healthy" else "unknown"
            else:
                health = "healthy"
            status = {
                "schema_version": 1, "observed_at": datetime.now(timezone.utc).isoformat(),
                "stage_id": stage_id, "campaign_id": stage.get("campaign_id"),
                "stage": stage.get("stage"), "journal_path": str(journal_path),
                "results_root": stage.get("results_root"), "entry_states": {
                    key: row.get("state") for key, row in entries.items()},
                "missed_entries": sorted(key for key, row in entries.items() if row.get("state") == "missed"),
                "cleanup_liabilities": liabilities, "output_freshness": output_freshness,
                "stale_output_entries": stale_outputs,
                "provider_expiry_evidence": {
                    key: (row.get("result") or {}).get("provider_results", {}) for key, row in entries.items()
                    if (row.get("result") or {}).get("provider_results")},
                "remote_archive_status": archive_status,
                "remote_archive_faults": archive_faults,
                "guest_health": guest_health,
                "credential_preflight": preflight,
                "disk": {"path": str(state_root), "free_bytes": usage.free, "total_bytes": usage.total,
                         "minimum_free_bytes": minimum_free,
                         "below_minimum": disk_below_minimum},
                "health": health,
                "observer_mode": "controller_local_only_guest_health_unqualified",
            }
        except Exception as exc:
            status = {"schema_version": 1, "observed_at": datetime.now(timezone.utc).isoformat(),
                      "health": "fault", "observer_error": f"{type(exc).__name__}: {exc}",
                      "state_root": str(state_root), "observer_mode": "read_only_no_mutations"}
        _atomic_json(state_root / "observer-status.json", status)
        stop.wait(interval)


def run(state_root: Path, *, provider_preflight: bool = False) -> int:
    stop = threading.Event()
    watcher = threading.Thread(target=observe, args=(state_root, stop),
        kwargs={"provider_preflight": provider_preflight}, name="campaign-observer", daemon=True)
    watcher.start()
    try:
        from .campaign import CampaignDriver
        CampaignDriver().run_active(state_root)
    finally:
        stop.set()
        watcher.join(timeout=5)
        pid_path = state_root / "campaign-supervisor.pid"
        try:
            pid_path.unlink()
        except FileNotFoundError:
            pass
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--provider-preflight", action="store_true",
        help="explicitly run read-only provider identity and Terraform checks (may refresh credentials)")
    args = parser.parse_args(argv)
    return run(args.state_root, provider_preflight=args.provider_preflight)


if __name__ == "__main__":
    raise SystemExit(main())
