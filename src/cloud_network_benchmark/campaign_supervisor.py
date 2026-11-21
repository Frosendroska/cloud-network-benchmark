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


def observe(state_root: Path, stop: threading.Event, interval: float = 30.0) -> None:
    """Write durable local health snapshots and periodic read-only preflights."""
    last_preflight = 0.0
    preflight = {"status": "not_checked"}
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
            stale_outputs = []
            for entry_id, row in entries.items():
                result = row.get("result") or {}
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
                    output_freshness[entry_id] = {"latest_output_age_seconds": age}
                    if elapsed > 120 and (age is None or age > 120):
                        stale_outputs.append(entry_id)
            if time.monotonic() - last_preflight >= 300:
                preflight = _read_only_preflight(stage)
                last_preflight = time.monotonic()
            usage = shutil.disk_usage(state_root)
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
                "credential_preflight": preflight,
                "disk": {"path": str(state_root), "free_bytes": usage.free, "total_bytes": usage.total},
                "health": "fault" if liabilities or stale_outputs or preflight.get("status") == "blocked" or any(x == "failed" for x in
                    (row.get("state") for row in entries.values())) else "healthy",
                "observer_mode": "read_only_provider_identity_and_expiry",
            }
        except Exception as exc:
            status = {"schema_version": 1, "observed_at": datetime.now(timezone.utc).isoformat(),
                      "health": "fault", "observer_error": f"{type(exc).__name__}: {exc}",
                      "state_root": str(state_root), "observer_mode": "read_only_no_mutations"}
        _atomic_json(state_root / "observer-status.json", status)
        stop.wait(interval)


def run(state_root: Path) -> int:
    stop = threading.Event()
    watcher = threading.Thread(target=observe, args=(state_root, stop), name="campaign-observer", daemon=True)
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
    args = parser.parse_args(argv)
    return run(args.state_root)


if __name__ == "__main__":
    raise SystemExit(main())
