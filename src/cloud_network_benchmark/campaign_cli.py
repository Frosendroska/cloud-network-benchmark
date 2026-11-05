"""Operator commands for prepared benchmark campaigns."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .campaign import CampaignDriver
from .campaign_inputs import load_operator_settings

DEFAULT_SETTINGS = Path("/etc/cloud-network-benchmark/operator.yaml")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cloud-network-benchmark campaign")
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("check", "start"):
        item = commands.add_parser(action)
        item.add_argument("--plan", required=True, type=Path)
        item.add_argument("--settings", type=Path)
        item.add_argument("--execute", action="store_true", help="required to hand off a campaign to systemd")
    status = commands.add_parser("status")
    status.add_argument("--settings", type=Path)
    logs = commands.add_parser("logs")
    logs.add_argument("--settings", type=Path)
    logs.add_argument("--follow", action="store_true")
    continuation = commands.add_parser("continue")
    continuation.add_argument("--settings", type=Path)
    continuation.add_argument("--stage-id", required=True)
    continuation.add_argument("--entry-id", required=True)
    continuation.add_argument("--confirm-supervision", action="store_true")
    # Private systemd entry point. It resumes the driver from its durable active pointer.
    runner = commands.add_parser("run", help=argparse.SUPPRESS)
    runner.add_argument("--state-root", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None, *, driver: CampaignDriver | None = None) -> int:
    args = _parser().parse_args(argv)
    campaign = driver or CampaignDriver()
    try:
        if args.action in {"check", "start"}:
            result = (campaign.check(args.plan, settings_path=args.settings) if args.action == "check"
                      else campaign.start(args.plan, settings_path=args.settings, execute=args.execute))
        elif args.action == "run":
            result = campaign.run_active(args.state_root)
        else:
            settings = load_operator_settings(args.settings or DEFAULT_SETTINGS)
            if args.action == "status":
                result = campaign.status(Path(settings["state_root"]))
            elif args.action == "logs":
                path = Path(settings.get("operator_log_path") or Path(settings["state_root"]) / "campaign.log")
                for line in campaign.logs(path, follow=args.follow):
                    sys.stdout.write(line)
                return 0
            else:
                result = campaign.continue_supervised(Path(settings["state_root"]), args.stage_id,
                    args.entry_id, confirmed=args.confirm_supervision)
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        if args.action == "status":
            return 0
        if args.action == "run":
            # A returned status means the durable journal handled the campaign
            # state. systemd restarts only when the driver itself raises; a
            # terminal failed/missed observation must not loop the service.
            return 0
        status = result.get("status") if isinstance(result, dict) else None
        return 0 if status in {"ready", "dispatched", "complete", "active", "awaiting_operator"} else 1
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f"Campaign error: {exc}", file=sys.stderr)
        return 2
