"""Independent, local read-only watcher for durable campaign observer snapshots.

The watcher never dispatches campaign work. Notifications are opt-in and use a
configured executable hook that receives one JSON event on stdin.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence


def _snapshot(path: Path, now: float, stale_after: float) -> tuple[str, dict]:
    try:
        if not math.isfinite(now) or not math.isfinite(stale_after) or stale_after <= 0:
            raise ValueError("watcher clock or staleness limit is invalid")
        value = json.loads(path.read_text(encoding="utf-8"))
        observed = datetime.fromisoformat(str(value["observed_at"]).replace("Z", "+00:00"))
        if observed.tzinfo is None:
            raise ValueError("observer timestamp must include a timezone")
        stamp = observed.timestamp()
        if not math.isfinite(now) or not math.isfinite(stamp) or stamp > now + 5:
            raise ValueError("observer timestamp is invalid or in the future")
        age = max(0.0, now - stamp)
        if age > stale_after:
            return "stale_observer", {"age_seconds": int(age), "health": value.get("health")}
        if value.get("health") != "healthy":
            return "observer_fault", {"age_seconds": int(age), "health": value.get("health"),
                                      "missed_entries": value.get("missed_entries", [])}
        return "healthy", {"age_seconds": int(age), "health": "healthy"}
    except (OSError, ValueError, TypeError, KeyError):
        return "observer_unavailable", {"age_seconds": None, "health": "unknown"}


def poll_once(state_root: Path, *, stale_after: float, now: float | None = None,
              notify: Callable[[dict], None] | None = None,
              previous_fingerprint: str | None = None) -> tuple[dict, str | None]:
    now = time.time() if now is None else now
    if not math.isfinite(now) or not math.isfinite(stale_after) or stale_after <= 0:
        now = time.time()
        status, details = "observer_unavailable", {"age_seconds": None, "health": "unknown"}
    else:
        status, details = _snapshot(Path(state_root) / "observer-status.json", now, stale_after)
    event = {"event": status, "observed_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
             "state_root": str(state_root), **details}
    fingerprint = json.dumps({k: event.get(k) for k in ("event", "health", "missed_entries")}, sort_keys=True)
    if status != "healthy" and fingerprint != previous_fingerprint and notify is not None:
        try:
            notify(event)
            event["notification"] = "delivered"
        except Exception as exc:
            event["notification"] = "failed"
            event["notification_error"] = type(exc).__name__
    next_fingerprint = fingerprint if status != "healthy" else None
    if event.get("notification") == "failed":
        next_fingerprint = previous_fingerprint
    return event, next_fingerprint


def run_watch(state_root: Path, *, interval: float = 30, stale_after: float = 90,
              hook: Sequence[str] | None = None, deliver: bool = False,
              stop_after: int | None = None,
              clock: Callable[[], float] = time.time,
              wait: Callable[[float], None] = time.sleep,
              emit: Callable[[dict], None] = print) -> int:
    previous = None
    count = 0

    def send(event: dict) -> None:
        if not deliver or not hook:
            return
        subprocess.run(list(hook), input=json.dumps(event), text=True, check=True,
                       timeout=10,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    while stop_after is None or count < stop_after:
        event, previous = poll_once(state_root, stale_after=stale_after, now=clock(),
                                    notify=send if deliver and hook else None,
                                    previous_fingerprint=previous)
        emit(event)
        count += 1
        if stop_after is None or count < stop_after:
            wait(interval)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=30)
    parser.add_argument("--stale-after", type=float, default=90)
    parser.add_argument("--hook", nargs="+", help="configured notification executable and arguments")
    parser.add_argument("--deliver", action="store_true", help="enable the configured notification hook")
    args = parser.parse_args(argv)
    if args.interval <= 0 or args.stale_after <= 0 or (args.deliver and not args.hook):
        parser.error("interval/stale-after must be positive; --deliver requires --hook")
    return run_watch(args.state_root, interval=args.interval, stale_after=args.stale_after,
                     hook=args.hook, deliver=args.deliver)


if __name__ == "__main__":
    raise SystemExit(main())
