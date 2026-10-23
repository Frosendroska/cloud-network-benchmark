"""Isolated F09 lifecycle command parser and read-only admission preview."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .admission import load_cost_inputs
from .config import resolve_campaign
from .scheduling import load_schedule


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
    cleanup.add_argument("--runtime-config", required=True, type=Path)
    cleanup.add_argument("--private-root", required=True, type=Path)
    cleanup.add_argument("--controller-session", required=True)
    cleanup.add_argument("--format", choices=("human", "json"), default="human")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
        if args.action in {"preview", "run"}:
            for path in (args.config, args.cost_inputs, args.runtime_config):
                if not path.is_file() or path.is_symlink():
                    raise ValueError(f"required local input is unavailable: {path.name}")
            runtime = json.loads(args.runtime_config.read_text(encoding="utf-8"))
            if not isinstance(runtime, dict):
                raise ValueError("runtime config must be a JSON object containing sourced non-secret references")
            report = preview_lifecycle(config_path=args.config, repository_root=Path.cwd(),
                schedule_path=args.schedule, row_id=args.row_id, cost_inputs_path=args.cost_inputs,
                now=datetime.now(timezone.utc))
            if args.action == "run":
                report["status"] = "rejected"
                report["reason_codes"] = sorted(set(report["reason_codes"] + ["paid_prerequisites_not_authorized_or_incomplete"]))
                report["execution_scope"] = args.execution_scope
                print(json.dumps(report, indent=2, sort_keys=True) if args.format == "json" else
                      "Lifecycle run rejected: current offline stage has no verified paid prerequisites.")
                return 1
            print(json.dumps(report, indent=2, sort_keys=True) if args.format == "json" else
                  f"Lifecycle preview: {report['status']}; paid eligible: no; external calls: 0")
            return 0 if report["status"] != "invalid" else 2
        # A cleanup command must never guess runtime adapters from a public file.
        # The direct Python API uses provider journals/settings and is supplied by
        # the owning orchestration stage; absent those, fail closed without calls.
        if not args.recovery.is_file() or args.recovery.is_symlink():
            raise ValueError("recovery reference must be a regular non-symlink file")
        report = {"status": "incomplete", "execution": "not_resumed", "cleanup": "not_attempted",
                  "reason_codes": ["provider_cleanup_runtime_adapter_required"], "external_calls": 0}
        print(json.dumps(report, indent=2, sort_keys=True) if args.format == "json" else
              "Cleanup not attempted: provider runtime adapters are unavailable.", file=sys.stderr)
        return 1
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "invalid", "reason_codes": ["invalid_lifecycle_request"],
                          "error": str(exc), "external_calls": 0}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
