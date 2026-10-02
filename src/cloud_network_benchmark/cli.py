from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from .config import resolve_campaign
from .dry_run import build_dry_run, render_human, render_json
from .errors import BenchmarkError
from .manifests import initialize_campaign


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cloud-network-benchmark")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "resolve", "dry-run"):
        command = subparsers.add_parser(name)
        command.add_argument("--config", type=Path, required=True)
        command.add_argument("--format", choices=("human", "json"), default="human")
    init = subparsers.add_parser("init")
    init.add_argument("--config", type=Path, required=True)
    init.add_argument("--results-root", type=Path, default=Path("results"))
    init.add_argument("--format", choices=("human", "json"), default="human")
    return parser


def _json(value: object) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, indent=2, sort_keys=True)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
        root = _repository_root()
        config_path = args.config if args.config.is_absolute() else root / args.config
        if args.command in {"validate", "resolve"}:
            resolved, _ = resolve_campaign(config_path, root)
            if args.command == "validate":
                payload = {
                    "valid": True,
                    "experiment_id": resolved.experiment_id,
                    "configuration_role": resolved.configuration_role,
                    "scenario": resolved.scenario,
                    "scheduled_start": resolved.scheduled_start.isoformat(),
                    "providers": resolved.selected_providers,
                    "source_sha256": resolved.config_source.sha256,
                    "child_count": len(resolved.observations),
                }
                if args.format == "json":
                    print(_json(payload))
                else:
                    print(
                        f"Valid {payload['configuration_role']} campaign {payload['experiment_id']} "
                        f"for {payload['scenario']} at {payload['scheduled_start']}"
                    )
                    print(f"Providers ({payload['child_count']}): {', '.join(payload['providers'])}")
                    print(f"Source SHA-256: {payload['source_sha256']}")
            else:
                print(_json(resolved) if args.format == "json" else f"Resolved {resolved.experiment_id} into {len(resolved.observations)} provider observation(s)")
            return 0
        if args.command == "dry-run":
            resolved, _ = resolve_campaign(config_path, root)
            report = build_dry_run(resolved)
            print(render_json(report) if args.format == "json" else render_human(report))
            return 0
        results_root = args.results_root if args.results_root.is_absolute() else root / args.results_root
        initialized = initialize_campaign(config_path, root, results_root)
        if args.format == "json":
            print(_json(initialized))
        else:
            print(f"Initialized campaign {initialized.campaign_id}")
            print(f"Parent manifest: {initialized.parent_manifest_path}")
            for child in initialized.children:
                print(f"{child.provider}: {child.run_id} -> {child.result_path}")
        return 0
    except BenchmarkError as exc:
        output_format = getattr(locals().get("args", None), "format", "human")
        if output_format == "json":
            print(_json(exc.as_dict()), file=sys.stderr)
        else:
            print(f"Error [{exc.code}]: {exc}", file=sys.stderr)
            if exc.field is not None:
                print(f"Field: {exc.field}", file=sys.stderr)
            if exc.details:
                print("Details:", file=sys.stderr)
                for key, value in exc.details.items():
                    print(f"  {key}: {value}", file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
