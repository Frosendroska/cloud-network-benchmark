#!/usr/bin/env python3
"""Pre-positioned, read-only watcher for one exact original saved-plan request."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
from typing import Callable

from cloud_network_benchmark import plan_disclosure
from cloud_network_benchmark.terraform_plan import PlanReviewError


def _root(path: Path) -> Path:
    if not path.is_absolute():
        raise PlanReviewError("each allowlisted provider root must be absolute")
    absolute = path.absolute()
    if path.is_symlink() or absolute != absolute.resolve(strict=False):
        raise PlanReviewError("allowlisted provider root must not traverse symlinks")
    if absolute.exists() and not absolute.is_dir():
        raise PlanReviewError("allowlisted provider root must be a directory when present")
    ancestor = absolute
    while not ancestor.exists():
        ancestor = ancestor.parent
    if ancestor.is_symlink() or not ancestor.is_dir():
        raise PlanReviewError("allowlisted provider root must descend from a real directory")
    return absolute


def _requests(roots: list[Path], mirror_snapshot=None,
              cache_links: dict | None = None) -> dict[Path, str]:
    found = {}
    for root in roots:
        if not root.exists():
            continue
        for child in plan_disclosure._walk_owned(root, mirror_snapshot, cache_links):
            if child.name != "review-request.json" or "-review-" not in child.parent.name:
                continue
            if not child.is_file():
                raise PlanReviewError("review request is not a regular file")
            found[child] = hashlib.sha256(child.read_bytes()).hexdigest()
    return found


def _complete_binding(path: Path, roots: list[Path], baseline: dict[Path, str],
                      cutoff: float, watch_end_epoch: float,
                      poll_seconds: float, mirror_snapshot,
                      expected_cache_links: dict) -> tuple[dict, bytes, int, str]:
    required = {"action_id", "root", "state", "state_sha256", "plan_sha256", "json_sha256",
                "variables_sha256", "source_sha256", "scope"}
    last: tuple[int, str] | None = None
    stable = 0
    while time.time() < min(cutoff, watch_end_epoch):
        for marker in ("operator-review.json", "accepted-review.json",
                       "review-cancelled.json", "failure.json"):
            if (path.parent / marker).exists() or (path.parent / marker).is_symlink():
                raise PlanReviewError("original producer closed or cancelled the review while publishing")
        cache_links = {}
        current = _requests(roots, mirror_snapshot, cache_links)
        if not plan_disclosure._cache_links_are_append_only(expected_cache_links, cache_links):
            raise PlanReviewError("Terraform provider cache links changed during request publication")
        expected_cache_links.update(cache_links)
        if any(current.get(old_path) != old_hash for old_path, old_hash in baseline.items()):
            raise PlanReviewError("previous original request changed or disappeared during publication")
        new = [candidate for candidate in current if candidate not in baseline]
        if new != [path]:
            raise PlanReviewError("multiple or changed original requests appeared during publication")
        stat = path.stat()
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        try:
            binding = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            binding = None
        identity = (stat.st_mtime_ns, digest)
        if isinstance(binding, dict) and required.issubset(binding):
            stable = stable + 1 if identity == last else 1
            if stable >= 2:
                return binding, payload, stat.st_mtime_ns, digest
        else:
            stable = 0
        last = identity
        time.sleep(min(poll_seconds, max(0.0, min(cutoff, watch_end_epoch) - time.time())))
    raise PlanReviewError("original request did not become complete and stable before its fixed cutoff")


def watch(roots: list[Path], checkpoint: Path, watch_end_epoch: float,
          *, poll_seconds: float = 0.025,
          display_limit: int = 32768,
          on_ready: Callable[[], None] | None = None) -> Path:
    if (isinstance(watch_end_epoch, bool) or not isinstance(watch_end_epoch, (int, float))
            or not math.isfinite(watch_end_epoch) or watch_end_epoch < 1_000_000_000
            or watch_end_epoch <= time.time()
            or isinstance(poll_seconds, bool) or not isinstance(poll_seconds, (int, float))
            or not math.isfinite(poll_seconds) or poll_seconds <= 0):
        raise PlanReviewError("one finite positive overall watch end and poll interval are required")
    owned = [_root(p) for p in roots]
    if not 1 <= len(owned) <= 6:
        raise PlanReviewError("between one and six exact allowlisted original child roots are required")
    if len(set(owned)) != len(owned):
        raise PlanReviewError("duplicate owned attempt roots are not allowed")
    if any(left != right and (left.is_relative_to(right) or right.is_relative_to(left))
           for index, left in enumerate(owned) for right in owned[index + 1:]):
        raise PlanReviewError("allowlisted provider roots must not overlap")
    watch_started = time.time()
    mirror_snapshot = plan_disclosure._provider_mirror_snapshot()
    baseline_cache_links = {}
    baseline = _requests(owned, mirror_snapshot, baseline_cache_links)
    for request in baseline:
        if not any((request.parent / marker).exists() or (request.parent / marker).is_symlink()
                   for marker in ("accepted-review.json", "review-cancelled.json", "failure.json")):
            raise PlanReviewError("a pre-existing request is still pending or unresolved")
    if on_ready is not None:
        on_ready()
    seen = dict(baseline)
    while time.time() < watch_end_epoch:
        current_cache_links = {}
        current = _requests(owned, mirror_snapshot, current_cache_links)
        if not plan_disclosure._cache_links_are_append_only(baseline_cache_links, current_cache_links):
            raise PlanReviewError("Terraform provider cache links changed while watching")
        baseline_cache_links.update(current_cache_links)
        for path, old_hash in seen.items():
            if current.get(path) != old_hash:
                raise PlanReviewError("observed original request changed or disappeared while watching")
        candidates = [path for path, digest in current.items()
                      if path not in baseline and path not in seen]
        if candidates:
            if len(candidates) != 1 or len(current) != len(baseline) + 1:
                raise PlanReviewError("multiple or ambiguous review requests appeared")
            request = candidates[0]
            directory = request.parent
            publication = request.stat()
            if publication.st_mtime < watch_started or publication.st_mtime > time.time() + 1:
                raise PlanReviewError("newly observed request predates this watcher and is stale")
            cutoff = min(publication.st_mtime + plan_disclosure.tp.LEGACY_PUBLICATION_REVIEW_SECONDS,
                         watch_end_epoch)
            try:
                published_binding = json.loads(plan_disclosure._regular(request))
            except (json.JSONDecodeError, UnicodeDecodeError):
                published_binding = None
            if isinstance(published_binding, dict):
                timing_fields = {"review_window_seconds", "review_deadline_utc_epoch",
                                 "review_published_at_utc_epoch"}
                if timing_fields & published_binding.keys():
                    cutoff = plan_disclosure.tp.original_review_cutoff(
                        directory, published_binding, watch_end_epoch)
            if time.time() >= cutoff:
                raise PlanReviewError("original producer review cutoff elapsed before disclosure")
            for marker in ("operator-review.json", "accepted-review.json", "review-cancelled.json", "failure.json"):
                if (directory / marker).exists() or (directory / marker).is_symlink():
                    raise PlanReviewError("new request is accepted, cancelled, or no longer pending")
            binding, request_bytes, request_mtime_ns, request_hash = _complete_binding(
                request, owned, baseline, cutoff, watch_end_epoch, poll_seconds,
                mirror_snapshot, current_cache_links)
            cutoff = plan_disclosure.tp.original_review_cutoff(
                directory, binding, watch_end_epoch)
            if time.time() >= cutoff:
                raise PlanReviewError("original producer review cutoff elapsed before disclosure")
            if not isinstance(binding, dict) or not isinstance(binding.get("action_id"), str):
                raise PlanReviewError("new original request has no valid action identity")
            current_publication = request.stat()
            latest_cache_links = {}
            latest = _requests(owned, mirror_snapshot, latest_cache_links)
            if (current_publication.st_mtime_ns != request_mtime_ns
                    or hashlib.sha256(request.read_bytes()).hexdigest() != request_hash):
                raise PlanReviewError("original request hash or publication time changed while watching")
            if (latest.get(request) != request_hash or len(latest) != len(baseline) + 1
                    or not plan_disclosure._cache_links_are_append_only(
                        current_cache_links, latest_cache_links)):
                raise PlanReviewError("review request set changed before disclosure began")
            current_cache_links.update(latest_cache_links)
            result = plan_disclosure.disclose(
                owned, checkpoint, cutoff, display_limit,
                request_path=request, mirror_snapshot=mirror_snapshot,
                cache_links=current_cache_links)
            return result[0]
        seen.update(current)
        time.sleep(min(poll_seconds, max(0.0, watch_end_epoch - time.time())))
    raise PlanReviewError("finite overall watch end elapsed without one current request")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owned-root", action="append", required=True, type=Path,
                        help="one exact original child root (supply one to six non-overlapping roots)")
    parser.add_argument("--checkpoint", required=True, type=Path,
                        help="new protected disclosure checkpoint directory")
    parser.add_argument("--watch-end-epoch", required=True, type=float,
                        help="finite absolute initial-watch horizon from the approved stage packet")
    parser.add_argument("--poll-seconds", type=float, default=0.025)
    parser.add_argument("--display-limit", type=int, default=32768)
    args = parser.parse_args(argv)
    watch(args.owned_root, args.checkpoint, args.watch_end_epoch,
          poll_seconds=args.poll_seconds,
          display_limit=args.display_limit,
          on_ready=lambda: print("watch_ready=true", flush=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PlanReviewError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"disclosure withheld: {exc}", file=sys.stderr)
        raise SystemExit(2)
