"""Source-preserving offline inspection of experiment schedule rows."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from .config import resolve_campaign
from .contracts.admission import ScheduleEntry
from .contracts.campaign import ResolvedCampaign
from .contracts.common import Evidence, Provider

REQUIRED = ("entry_id", "config_id", "experiment_id", "scheduled_start", "window_label", "priority")
OPTIONAL = ("replaces_run_id",)
WINDOWS = {"night", "morning", "afternoon", "evening"}


@dataclass(frozen=True)
class ExecutionIdentity:
    entry_id: str
    campaign_id: str
    child_identities: Mapping[str, str]
    resource_namespaces: Mapping[str, str]
    state_namespaces: Mapping[str, str]
    result_namespaces: Mapping[str, str]


@dataclass(frozen=True)
class ScheduleInspection:
    entries: tuple[ScheduleEntry, ...]
    pending_by_entry: Mapping[str, tuple[str, ...]]
    errors: tuple[str, ...]
    missed_entries: tuple[str, ...]
    primary_slots: tuple[tuple[str, str], ...]
    namespace_collisions: tuple[str, ...]
    simultaneous_vm_demand: Mapping[str, int]
    complete_primary_week: bool
    identities_by_entry: Mapping[str, ExecutionIdentity] = field(default_factory=dict)


@dataclass(frozen=True)
class SkewAssessment:
    phase_id: str
    starts: Mapping[str, datetime | None]
    skew_seconds: float | None
    comparison_eligibility: str
    target_met: bool | None
    finding: str | None


def load_schedule(path: Path, *, config_paths: Mapping[str, Path], repository_root: Path) -> list[ScheduleEntry]:
    """Parse exact CSV records and resolve each against a named existing config."""
    source = path.read_bytes()
    try:
        text = source.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("invalid_schedule: schedule must be UTF-8") from exc
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    lines = text.splitlines(keepends=True)
    try:
        header = next(reader)
        header_end_line = reader.line_num
    except (csv.Error, StopIteration) as exc:
        raise ValueError("invalid_schedule: empty schedule")
    if len(header) != len(set(header)) or any(c not in REQUIRED + OPTIONAL for c in header):
        raise ValueError("invalid_schedule: header fields are missing, duplicated, or unknown")
    if set(REQUIRED) - set(header):
        raise ValueError("invalid_schedule: required fields are missing")
    entries: list[ScheduleEntry] = []
    seen: set[str] = set()
    # Reparse records while retaining their exact physical CSV span, including quoted newlines.
    byte_text = text
    pos = sum(len(line) for line in lines[:header_end_line])
    try:
      records = iter(reader)
      for parsed in records:
        current_line = reader.line_num
        end = sum(len(line) for line in lines[:current_line])
        raw_record = byte_text[pos:end].rstrip("\r\n")
        pos = end
        if not parsed or all(not value for value in parsed):
            continue
        if len(parsed) != len(header):
            raise ValueError("invalid_schedule: row width does not match header")
        row = dict(zip(header, parsed))
        if any(not value.strip() for value in (row.get("entry_id", ""), row.get("config_id", ""), row.get("experiment_id", ""), row.get("priority", ""))):
            raise ValueError("invalid_schedule: blank required identity")
        entry_id = row["entry_id"]
        if entry_id in seen:
            raise ValueError("invalid_schedule: duplicate entry id")
        seen.add(entry_id)
        if any(re.search(r"(?i)(password|passwd|token|secret|credential|private[_-]?key|access[_-]?key)\s*[:=]", v) for v in parsed):
            raise ValueError("invalid_schedule: secret-bearing field")
        config_id = row["config_id"]
        if config_id not in config_paths:
            raise ValueError("identity_mismatch: configuration id is not mapped")
        config_path = config_paths[config_id]
        config_root = (repository_root / "configs").resolve()
        try:
            resolved = config_path.resolve(strict=True)
            resolved.relative_to(config_root)
        except (OSError, ValueError) as exc:
            raise ValueError("identity_mismatch: config path escapes repository config roots") from exc
        scheduled = None
        if row["scheduled_start"].strip():
            try:
                scheduled = datetime.fromisoformat(row["scheduled_start"].replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("invalid_schedule: malformed scheduled timestamp") from exc
            if scheduled.tzinfo is None or scheduled.utcoffset() is None:
                raise ValueError("invalid_schedule: timestamp must include UTC offset")
            scheduled = scheduled.astimezone(timezone.utc)
        label = row.get("window_label", "").strip().lower() or None
        if label is not None and label not in WINDOWS:
            raise ValueError("invalid_schedule: unknown window label")
        columns = list(header)
        header_raw = lines[0].rstrip("\r\n")
        entry = ScheduleEntry(schema_version=2, entry_id=entry_id, config_id=config_id,
            experiment_id=row["experiment_id"], scheduled_start=scheduled, window_label=label,
            source_path=path.as_posix(), source_sha256=hashlib.sha256(source).hexdigest(),
            row_sha256=hashlib.sha256(raw_record.encode("utf-8")).hexdigest(), original_header=header_raw,
            original_row=raw_record, columns=columns, priority=row["priority"],
            replaces_run_id=row.get("replaces_run_id") or None)
        campaign, _ = resolve_campaign(resolved, repository_root, schedule_entry=entry)
        if campaign.experiment_id != entry.experiment_id:
            raise ValueError("identity_mismatch: experiment id differs from configuration")
        entries.append(entry)
    except csv.Error as exc:
        raise ValueError("invalid_schedule: malformed CSV") from exc
    return entries


def inspect_schedule(entries: Sequence[ScheduleEntry], *, campaigns_by_entry_id: Mapping[str, ResolvedCampaign], now: datetime, require_primary_week: bool = False) -> ScheduleInspection:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("invalid_schedule: inspection time must be timezone-aware")
    pending: dict[str, tuple[str, ...]] = {}
    errors: list[str] = []
    missed: list[str] = []
    slots: dict[tuple[str, str], list[ScheduleEntry]] = defaultdict(list)
    collisions: set[str] = set()
    primary = [e for e in entries if e.priority in {"P0", "P1"}]
    for e in entries:
        reasons = []
        campaign = campaigns_by_entry_id.get(e.entry_id)
        mode = str(getattr(campaign.mode, "value", campaign.mode)) if campaign else None
        if campaign is None or campaign.config_id != e.config_id or campaign.experiment_id != e.experiment_id:
            errors.append(f"identity_mismatch:{e.entry_id}")
        if e.scheduled_start is None:
            reasons.append("schedule_pending")
        else:
            if e.scheduled_start < now.astimezone(timezone.utc):
                missed.append(e.entry_id)
            if e.window_label:
                slots[(e.scheduled_start.date().isoformat(), e.window_label)].append(e)
            elif e.priority in {"P0", "P1"}:
                reasons.append("window_pending")
        if campaign and mode in {"short_study", "diagnostic_day"} and campaign.benchmark.common_n.state == "pending":
            reasons.append("calibration_pending")
        if campaign and campaign.admission.requires_primary_review:
            reasons.append("primary_review_pending")
        if campaign:
            for blocker in campaign.launch_blockers:
                safe_blocker = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(blocker)).strip("_").lower()
                if safe_blocker:
                    reasons.append(f"launch_blocker:{safe_blocker}")
        if reasons:
            pending[e.entry_id] = tuple(reasons)
        if campaign:
            if mode in {"calibration", "diagnostic_day"}:
                same_mode = [item for item in entries if item.experiment_id == e.experiment_id
                    and campaigns_by_entry_id.get(item.entry_id) is not None
                    and str(getattr(campaigns_by_entry_id[item.entry_id].mode, "value", campaigns_by_entry_id[item.entry_id].mode)) == mode]
                if len(same_mode) != 1:
                    errors.append(f"identity_mismatch:{e.entry_id}:retained_mode_requires_one_entry")
            if e.priority in {"P0", "P1"} and {str(getattr(p, "value", p)) for p in campaign.selected_providers} != {p.value for p in Provider}:
                errors.append(f"identity_mismatch:{e.entry_id}:primary_requires_all_providers")
    dates = sorted({e.scheduled_start.date() for e in primary if e.scheduled_start})
    aligned = len(primary) == 56 and sum(e.priority == "P0" for e in primary) == 28 and sum(e.priority == "P1" for e in primary) == 28
    if dates:
        aligned = aligned and len(dates) == 7 and all((b-a).days == 1 for a, b in zip(dates, dates[1:]))
    else:
        aligned = False
    by_day_window: dict[tuple[date, str], list[ScheduleEntry]] = defaultdict(list)
    for e in primary:
        if e.scheduled_start and e.window_label:
            by_day_window[(e.scheduled_start.date(), e.window_label)].append(e)
    aligned = aligned and all(
        len(by_day_window[(d, w)]) == 2
        and {e.priority for e in by_day_window[(d, w)]} == {"P0", "P1"}
        and len({e.experiment_id for e in by_day_window[(d, w)]}) == 2
        and len({e.scheduled_start for e in by_day_window[(d, w)]}) == 1
        for d in dates for w in WINDOWS
    )
    aligned = aligned and all({e.window_label for e in primary if e.scheduled_start and e.scheduled_start.date() == d} == WINDOWS for d in dates)
    for entry in primary:
        campaign = campaigns_by_entry_id.get(entry.entry_id)
        if campaign is not None:
            mode = str(getattr(campaign.mode, "value", campaign.mode))
            scenario = str(getattr(campaign.scenario, "value", campaign.scenario))
            if mode != "short_study" or (entry.priority == "P0" and scenario != "same_zone") or (entry.priority == "P1" and scenario != "placement_optimization"):
                aligned = False
    if any(error.split(":", 2)[1] in {e.entry_id for e in primary} for error in errors if error.startswith("identity_mismatch:")):
        aligned = False
    if require_primary_week and not aligned:
        errors.append("invalid_schedule: primary week requires 28+28 aligned entries")
    identities = {}
    all_namespaces: dict[str, tuple[str, str]] = {}
    for entry in entries:
        campaign = campaigns_by_entry_id.get(entry.entry_id)
        if campaign is None:
            continue
        providers = sorted(Provider(provider).value for provider in campaign.selected_providers)
        child = {provider: f"{entry.entry_id}-{provider}" for provider in providers}
        resources = {provider: f"{entry.entry_id}-{provider}-resources" for provider in providers}
        states = {provider: f"{entry.entry_id}-{provider}-state" for provider in providers}
        results = {provider: f"{entry.entry_id}-{provider}-results" for provider in providers}
        for category, values in (("resource", resources), ("state", states), ("result", results)):
            for provider, namespace in values.items():
                previous = all_namespaces.get(namespace)
                identity = (entry.entry_id, provider)
                if previous is not None and previous != identity:
                    collisions.add(f"{category}:{namespace}")
                all_namespaces[namespace] = identity
        identities[entry.entry_id] = ExecutionIdentity(entry.entry_id, entry.entry_id, child, resources, states, results)
    return ScheduleInspection(tuple(entries), pending, tuple(errors), tuple(missed),
        tuple(sorted((d.isoformat(), w) for d, w in by_day_window)), tuple(sorted(collisions)),
        {p.value: 4 for p in Provider}, aligned, identities)


def assess_provider_skew(phase_id: str, *, selected_providers: Sequence[Provider], actual_starts: Mapping[Provider, Evidence[datetime]]) -> SkewAssessment:
    providers = [Provider(p) for p in selected_providers]
    if len(providers) != len(set(providers)):
        raise ValueError("identity_mismatch: selected providers must be unique")
    starts = {provider.value: actual_starts.get(provider, Evidence[datetime](unavailable_reason="missing_start")).value for provider in providers}
    if not starts or any(value is None for value in starts.values()):
        return SkewAssessment(phase_id, starts, None, "unavailable", None, "provider_skew_unavailable")
    if any(value.tzinfo is None or value.utcoffset() is None for value in starts.values() if value is not None):
        raise ValueError("invalid_schedule: actual provider starts must be timezone-aware")
    values = [v.astimezone(timezone.utc) for v in starts.values() if v is not None]
    skew = (max(values) - min(values)).total_seconds()
    if skew <= 60:
        return SkewAssessment(phase_id, starts, skew, "eligible", True, None)
    if skew <= 300:
        return SkewAssessment(phase_id, starts, skew, "eligible", False, "provider_skew_target_missed")
    return SkewAssessment(phase_id, starts, skew, "ineligible", False, "provider_skew_excluded")
