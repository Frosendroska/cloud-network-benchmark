"""Offline schedule admission and a small, controller-local budget ledger.

This module deliberately performs no provider, subprocess, or benchmark work.
Callers receive durable evidence before handing work to the lifecycle layer.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import stat
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from pydantic import ValidationError

from .contracts.admission import (ExpiryEvidence, ExpiryRequest, ReservationEvidence,
                                  ReservationRequest)
from .contracts.common import Evidence, ExperimentMode, Provider, StringEvidence
from .scheduling import assess_provider_skew, inspect_schedule, load_schedule


def _decimal(value: Any, name: str = "money") -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite nonnegative Decimal")
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{name} must be a finite nonnegative Decimal") from exc
    if not result.is_finite() or result < 0:
        raise ValueError(f"{name} must be a finite nonnegative Decimal")
    return result


def _hash(value: str, name: str) -> str:
    if not value or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"{name} must be lowercase SHA-256")
    return value


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _duration(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite nonnegative seconds")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{name} must be finite nonnegative seconds")
    return result


@dataclass(frozen=True)
class CostInputs:
    cost_input_id: str
    prices_checked_on: str
    source_path: str
    source_sha256: str
    payload_sha256: str
    payload: Mapping[str, Any]
    source_scope: tuple[str, ...]
    refresh_required: bool


def load_cost_inputs(path: Path) -> CostInputs:
    raw = path.read_bytes()
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid cost input JSON") from exc
    required = {"schema_version", "cost_input_id", "prices_checked_on", "source_scope", "source_sha256", "source_identity_kind", "currency", "funding"}
    if not isinstance(data, dict) or set(data) - (required | {"acquired_on", "continuation_sha256", "normalization", "rows", "refresh_required_before_paid_execution", "live_quote", "provider_support"}) or not required <= set(data):
        raise ValueError("invalid cost input fields")
    if data["schema_version"] != 1 or data["currency"] != "USD":
        raise ValueError("unsupported cost input scope")
    if not data["cost_input_id"].strip() or not data["source_scope"]:
        raise ValueError("cost input identity and scope must be nonblank")
    source_hash = _hash(data["source_sha256"], "source_sha256")
    return CostInputs(data["cost_input_id"], data["prices_checked_on"], path.as_posix(), source_hash,
                      hashlib.sha256(raw).hexdigest(), data, tuple(data["source_scope"]),
                      bool(data.get("refresh_required_before_paid_execution", False)))


def aws_expiry_cost_handoff(cost_inputs: CostInputs, aws_fixture: Path, regions: Sequence[str], *, reservation_sha256: str) -> dict[str, Any]:
    """Build the explicit F05 payload; its support amount is included in the reservation."""
    _hash(reservation_sha256, "reservation_sha256")
    raw = aws_fixture.read_bytes()
    fixture = json.loads(raw)
    digest = hashlib.sha256(raw).hexdigest()
    if fixture.get("free_tier_assumed") is not False or fixture.get("id") != "aws-ssm-automation-public-2026-10-22":
        raise ValueError("support_cost_missing")
    selected = sorted(set(regions))
    if not selected or len(selected) != len(regions) or any(not item.strip() for item in selected):
        raise ValueError("provider regions must be unique and nonblank")
    per_region = _decimal(fixture["worst_planned_cost_per_region"])
    included = (per_region * len(selected)).quantize(Decimal("0.0001"), rounding=ROUND_CEILING)
    return {"source_path": cost_inputs.source_path, "source_sha256": reservation_sha256,
            "expiry_input_id": fixture["id"], "expiry_input_sha256": digest,
            "expiry_cost_allowance_per_region_usd": float(per_region), "expiry_cost_regions": selected,
            "expiry_cost_included_usd": float(included)}


@dataclass(frozen=True)
class WorkflowAllowance:
    registration_seconds: float
    provision_seconds: float
    readiness_seconds: float
    stabilization_seconds: float
    measurement_seconds: float
    measurement_shutdown_seconds: float
    retrieval_seconds: float
    cleanup_seconds: float
    source_id: str
    provider_leads_seconds: Mapping[str, float]

    def __post_init__(self) -> None:
        for name in ("registration_seconds", "provision_seconds", "readiness_seconds", "stabilization_seconds",
                     "measurement_seconds", "measurement_shutdown_seconds", "retrieval_seconds", "cleanup_seconds"):
            _duration(getattr(self, name), name)
        if self.cleanup_seconds <= 0 or (self.measurement_seconds > 0 and self.measurement_shutdown_seconds <= 0):
            raise ValueError("cleanup must be positive and active measurement requires shutdown grace")
        if self.measurement_seconds == 0 and self.measurement_shutdown_seconds != 0:
            raise ValueError("completed measurement must have zero shutdown remainder")
        if not self.source_id.strip() or not self.provider_leads_seconds:
            raise ValueError("allowance policy and provider leads must be sourced")
        for name, value in self.provider_leads_seconds.items():
            if not name.strip():
                raise ValueError("provider lead identity must be nonblank")
            _duration(value, "provider lead")

    @property
    def total_seconds(self) -> float:
        return sum(getattr(self, name) for name in ("registration_seconds", "provision_seconds", "readiness_seconds",
                   "stabilization_seconds", "measurement_seconds", "retrieval_seconds", "cleanup_seconds"))


@dataclass(frozen=True)
class AdmissionPlan:
    resolved_campaign: Any
    child_identities: Mapping[str, str]
    namespaces: Mapping[str, str]
    costs: CostInputs
    allowance: WorkflowAllowance
    execution_scope: str = "offline_simulation"
    prerequisites: Mapping[str, Any] = field(default_factory=dict)
    funding: Mapping[str, Decimal] = field(default_factory=dict)
    provider_support: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    original_failure: Mapping[str, Any] | None = None
    optional_reviewed: bool = False
    optional_review_evidence: Mapping[str, Any] = field(default_factory=dict)
    observation_id: str | None = None

    def __post_init__(self) -> None:
        if self.execution_scope not in {"offline_simulation", "paid_request"}:
            raise ValueError("unknown execution scope")
        if len(set(self.child_identities.values())) != len(self.child_identities):
            raise ValueError("child identities must be unique")
        if len(set(self.namespaces.values())) != len(self.namespaces):
            raise ValueError("namespace collisions are not allowed")
        for amount in self.funding.values():
            _decimal(amount)


@dataclass(frozen=True)
class AdmissionDecision:
    status: str
    newly_reserved: bool
    evidence: ReservationEvidence | None
    reasons: tuple[str, ...]
    revision: int
    effective_deadline: datetime | None = None


@dataclass(frozen=True)
class StageDecision:
    eligible: bool
    reason_codes: tuple[str, ...]
    effective_deadline: datetime | None
    creation_granted: bool = False


@dataclass(frozen=True)
class CopyHandoff:
    reservation_id: str
    work_deadline: datetime
    cleanup_seconds: float
    retry_artifact_ids: tuple[str, ...]
    refused_artifact_ids: tuple[str, ...]
    preserve_partial_evidence: bool = True
    request_normal_cleanup: bool = True


def bounded_copy_handoff(*, reservation_id: str, cap_end: datetime, provider_trigger: datetime,
                         now: datetime, cleanup_seconds: float, retry_seconds: float,
                         failed_artifact_ids: Sequence[str], already_retried: Sequence[str]) -> CopyHandoff:
    """Describe the existing one-retry copy handoff without executing retrieval."""
    cap_end, provider_trigger, now = _utc(cap_end), _utc(provider_trigger), _utc(now)
    cleanup, retry = _duration(cleanup_seconds, "cleanup_seconds"), _duration(retry_seconds, "retry_seconds")
    if cleanup <= 0 or not reservation_id.strip() or len(set(failed_artifact_ids)) != len(failed_artifact_ids):
        raise ValueError("copy policy identity/cleanup is invalid")
    deadline = min(cap_end, provider_trigger) - timedelta(seconds=cleanup)
    retried = set(already_retried)
    eligible = tuple(sorted(item for item in failed_artifact_ids if item not in retried and item.strip() and now + timedelta(seconds=retry) <= deadline))
    refused = tuple(sorted(set(failed_artifact_ids) - set(eligible)))
    return CopyHandoff(reservation_id, deadline, cleanup, eligible, refused)


@dataclass(frozen=True)
class Reconciliation:
    event_id: str
    reservation_id: str
    provider: Provider
    source_sha256: str
    incurred_usd: Decimal | None = None
    actual: bool = False
    cleanup_verified_absent: bool = False
    billable_resources_present: bool | None = None
    failure: Mapping[str, Any] | None = None
    occurred_at: datetime | None = None


class BudgetLedger:
    """Serialized JSON ledger shared by cooperating processes on one local disk."""
    def __init__(self, path: Path, *, scope_id: str, utc_now: Callable[[], datetime],
                 monotonic_now: Callable[[], float], controller_session_id: str,
                 lock_timeout_seconds: float = 5.0):
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.scope_id = scope_id
        self.utc_now = utc_now
        self.monotonic_now = monotonic_now
        self.controller_session_id = controller_session_id
        self.lock_timeout_seconds = _duration(lock_timeout_seconds, "lock_timeout_seconds")
        if self.lock_timeout_seconds <= 0 or not scope_id.strip() or not controller_session_id.strip():
            raise ValueError("ledger scope, session, and positive lock timeout are required")

    def _lock(self, *, initialize: bool = False):
        if initialize:
            if self.path.exists() or self.lock_path.exists():
                raise ValueError("ledger initialization is exclusive")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        else:
            for path in (self.path, self.lock_path):
                if path.is_symlink() or not path.exists() or not stat.S_ISREG(path.stat().st_mode):
                    raise ValueError("ledger_unavailable")
            fd = os.open(self.lock_path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
        os.fchmod(fd, 0o600)
        deadline = time.monotonic() + self.lock_timeout_seconds
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise TimeoutError("ledger_unavailable: lock timeout")
                time.sleep(min(.01, max(0, deadline-time.monotonic())))
        if not initialize:
            try:
                marker = json.loads(os.pread(fd, 4096, 0).decode("utf-8"))
                if marker != {"schema_version": 1, "scope_id": self.scope_id}:
                    raise ValueError("ledger_unavailable")
            except Exception as exc:
                fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)
                raise ValueError("ledger_unavailable") from exc
        return fd

    def _read(self, *, initializing: bool = False) -> dict[str, Any]:
        if initializing:
            return {"schema_version": 1, "scope_id": self.scope_id, "revision": 0,
                    "funding_remaining": {}, "reservations": {}, "claims": {}, "events": {},
                    "cleanup_open": [], "cleanup_blocked": [], "source_history": []}
        try:
            def unique(pairs):
                obj = {}
                for key, value in pairs:
                    if key in obj: raise ValueError("ledger_unavailable")
                    obj[key] = value
                return obj
            state = json.loads(self.path.read_text(encoding="utf-8"), object_pairs_hook=unique)
        except Exception as exc:
            raise ValueError("ledger_unavailable") from exc
        required = {"schema_version", "scope_id", "revision", "funding_remaining", "reservations", "claims", "events", "cleanup_open", "cleanup_blocked", "source_history"}
        allowed = required | {"campaign_claims", "replacement_counts", "budget_blocked"}
        if (not isinstance(state, dict) or not required <= set(state) or set(state) - allowed
            or state.get("schema_version") != 1 or state.get("scope_id") != self.scope_id
            or isinstance(state.get("revision"), bool) or not isinstance(state.get("revision"), int) or state["revision"] < 1
            or not all(isinstance(state.get(key), dict) for key in ("funding_remaining", "reservations", "claims", "events"))
            or not all(isinstance(state.get(key), list) for key in ("cleanup_open", "cleanup_blocked", "source_history"))):
            raise ValueError("ledger_unavailable")
        try:
            for amount in state["funding_remaining"].values(): _decimal(amount)
        except (ValueError, TypeError):
            raise ValueError("ledger_unavailable")
        return state

    def _write(self, state: dict[str, Any]) -> None:
        state["revision"] += 1
        fd, name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(state, stream, sort_keys=True, separators=(",", ":"))
                stream.flush(); os.fsync(stream.fileno())
            os.replace(name, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try: os.fsync(directory)
            finally: os.close(directory)
        finally:
            if os.path.exists(name): os.unlink(name)

    def initialize(self, inputs: CostInputs) -> Mapping[str, Any]:
        fd = self._lock(initialize=True)
        try:
            os.write(fd, json.dumps({"schema_version": 1, "scope_id": self.scope_id}, sort_keys=True).encode())
            os.fsync(fd)
            state = self._read(initializing=True)
            funding = inputs.payload.get("funding", {})
            state["funding_remaining"] = {k: str(_decimal(v, k)) for k, v in funding.items() if k in {"primary", "replacements", "tax", "uncertainty"}}
            state["source_history"].append({"path": inputs.source_path, "sha256": inputs.payload_sha256, "source_sha256": inputs.source_sha256})
            self._write(state)
            return state
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)

    def inspect(self) -> Mapping[str, Any]:
        fd = self._lock()
        try: return self._read()
        finally: fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)

    def reserve(self, request: ReservationRequest, *, plan: AdmissionPlan) -> AdmissionDecision:
        campaign = plan.resolved_campaign
        if request.config_id != campaign.config_id or request.mode != campaign.mode or request.priority != campaign.admission.priority:
            return AdmissionDecision("rejected", False, None, ("identity_mismatch",), 0)
        if request.source_sha256 != plan.costs.payload_sha256 or request.source_path != plan.costs.source_path:
            return AdmissionDecision("rejected", False, None, ("source_mismatch",), 0)
        reasons = []
        if plan.execution_scope == "paid_request":
            if plan.costs.refresh_required: reasons.append("price_refresh_required")
            required = {ExperimentMode.ACCEPTANCE:{"f10_offline","paid_authorization","quota_review"},
                ExperimentMode.SHORT_STUDY:{"f10_offline","paid_authorization","quota_review","common_n","study_window"},
                ExperimentMode.CALIBRATION:{"f10_offline","paid_authorization","quota_review","calibration_authorized"},
                ExperimentMode.DIAGNOSTIC_DAY:{"f10_offline","paid_authorization","quota_review","common_n","diagnostic_day_authorized","size_export_review"}}[ExperimentMode(campaign.mode)]
            if not required <= set(plan.prerequisites) or any(not isinstance(plan.prerequisites.get(k), Mapping) for k in required):
                reasons.append("prerequisite_pending")
            else:
                try:
                    for key in required: self._validate_gate_evidence(plan.prerequisites[key])
                except ValueError:
                    reasons.append("prerequisite_pending")
            if plan.optional_reviewed is False and request.priority in {"P2", "P3"}: reasons.append("optional_review_required")
            for provider in request.selected_providers:
                support = plan.provider_support.get(Provider(provider).value)
                if not support or not support.get("source_sha256") or not support.get("included_usd"):
                    reasons.append("support_cost_missing")
                elif support.get("source_sha256") != plan.costs.payload_sha256 or not support.get("source_path"):
                    reasons.append("support_cost_missing")
        if request.priority in {"P2", "P3"} and not plan.optional_reviewed:
            reasons.append("optional_review_required")
        elif request.priority in {"P2", "P3"}:
            try: self._validate_gate_evidence(plan.optional_review_evidence)
            except ValueError: reasons.append("optional_review_required")
        if campaign.mode == ExperimentMode.SHORT_STUDY and campaign.benchmark.common_n.state != "frozen":
            reasons.append("calibration_pending")
        selected = {Provider(p).value for p in request.selected_providers}
        if set(plan.child_identities) != selected or set(plan.namespaces) != selected or set(plan.allowance.provider_leads_seconds) != selected:
            reasons.append("identity_mismatch")
        min_measurement = {ExperimentMode.ACCEPTANCE: 75, ExperimentMode.SHORT_STUDY: 225,
                           ExperimentMode.CALIBRATION: 950, ExperimentMode.DIAGNOSTIC_DAY: 86407}[ExperimentMode(campaign.mode)]
        if plan.allowance.stabilization_seconds != 60 or plan.allowance.measurement_shutdown_seconds <= 0 or plan.allowance.measurement_seconds < min_measurement + plan.allowance.measurement_shutdown_seconds:
            reasons.append("workflow_time_insufficient")
        if campaign.admission.requires_schedule and campaign.scheduled_start is None:
            reasons.append("schedule_pending")
        if reasons:
            return AdmissionDecision("pending", False, None, tuple(sorted(set(reasons))), 0)
        if campaign.admission.requires_schedule and _utc(self.utc_now()) >= _utc(campaign.scheduled_start):
            return AdmissionDecision("missed", False, None, ("slot_missed",), 0)
        fd = self._lock()
        try:
            state = self._read()
            if state.get("budget_blocked"):
                return AdmissionDecision("rejected", False, None, ("budget_exhausted",), state["revision"])
            mode = ExperimentMode(campaign.mode)
            key = request.campaign_id if mode in {ExperimentMode.CALIBRATION, ExperimentMode.DIAGNOSTIC_DAY} or request.schedule_entry_id.value is None else request.schedule_entry_id.value
            plan_payload = {"scope": plan.execution_scope, "prerequisites": dict(plan.prerequisites),
                "funding": {k: str(v) for k,v in plan.funding.items()}, "children": dict(plan.child_identities),
                "namespaces": dict(plan.namespaces), "allowance": {"registration":plan.allowance.registration_seconds,
                "provision":plan.allowance.provision_seconds,"readiness":plan.allowance.readiness_seconds,
                "stabilization":plan.allowance.stabilization_seconds,"measurement":plan.allowance.measurement_seconds,
                "shutdown":plan.allowance.measurement_shutdown_seconds,"retrieval":plan.allowance.retrieval_seconds,
                "cleanup":plan.allowance.cleanup_seconds,"source_id":plan.allowance.source_id,
                "provider_leads":dict(plan.allowance.provider_leads_seconds)},
                "provider_support":dict(plan.provider_support), "original_failure":plan.original_failure,
                "optional_reviewed":plan.optional_reviewed,"optional_review_evidence":dict(plan.optional_review_evidence),
                "observation_id":plan.observation_id,"scheduled_start":campaign.scheduled_start.isoformat() if campaign.scheduled_start else None}
            fingerprint = hashlib.sha256(json.dumps({"request": request.model_dump(mode="json"), "source": plan.costs.payload_sha256, "plan":plan_payload}, sort_keys=True, default=str).encode()).hexdigest()
            prior = state["claims"].get(key) or state.get("campaign_claims",{}).get(request.campaign_id)
            if prior:
                if prior["fingerprint"] != fingerprint:
                    return AdmissionDecision("rejected", False, None, ("duplicate_conflict",), state["revision"])
                ev = ReservationEvidence.model_validate(prior["evidence"])
                return AdmissionDecision("reserved", False, ev, (), state["revision"])
            namespaces = set(plan.namespaces.values())
            if any(namespaces & set(record.get("plan", {}).get("namespaces", {}).values()) for record in state["reservations"].values()):
                return AdmissionDecision("rejected", False, None, ("identity_mismatch",), state["revision"])
            replacement_of = None
            context = str(getattr(campaign.context, "value", campaign.context))
            if context == "replacement":
                failure = plan.original_failure or {}
                replaced_run = campaign.replacement_context.replaces_run_id if campaign.replacement_context else None
                provider = replaced_run.rsplit("-", 1)[-1] if replaced_run else ""
                replacement_of = failure.get("original_reservation_id")
                original = state["reservations"].get(replacement_of)
                failed_attempt = original.get("failures_by_provider", {}).get(provider, {}) if original else {}
                if ExperimentMode(campaign.mode) != ExperimentMode.SHORT_STUDY or not original or original.get("context") == "replacement":
                    return AdmissionDecision("rejected", False, None, ("identity_mismatch",), state["revision"])
                if (not failed_attempt or failed_attempt.get("run_id") != replaced_run or failure.get("failed_run_id") != replaced_run
                    or failure.get("failed_provider") != provider):
                    return AdmissionDecision("rejected", False, None, ("prerequisite_pending",), state["revision"])
                if original.get("request", {}).get("mode") != ExperimentMode.SHORT_STUDY.value or original.get("request", {}).get("priority") not in {"P0", "P1"}:
                    return AdmissionDecision("rejected", False, None, ("identity_mismatch",), state["revision"])
                if original.get("cleanup_open") or replacement_of in state.get("cleanup_blocked", []):
                    return AdmissionDecision("rejected", False, None, ("cleanup_open",), state["revision"])
                if provider not in selected or original.get("replacement_claimed"):
                    return AdmissionDecision("rejected", False, None, ("duplicate_conflict",), state["revision"])
                if state.get("replacement_counts", {}).get(provider, 0) >= 3:
                    return AdmissionDecision("rejected", False, None, ("optional_review_required",), state["revision"])
            if state.get("cleanup_blocked"):
                return AdmissionDecision("rejected", False, None, ("cleanup_open",), state["revision"])
            if any(v.get("cleanup_open") and v.get("session") != self.controller_session_id for v in state["reservations"].values()):
                return AdmissionDecision("rejected", False, None, ("controller_session_changed",), state["revision"])
            price = sum((_decimal(v) for v in plan.funding.values()), Decimal("0")).quantize(Decimal("0.0001"), rounding=ROUND_CEILING)
            if price <= 0 or not plan.funding:
                return AdmissionDecision("rejected", False, None, ("support_cost_missing",), state["revision"])
            if request.estimated_cost_usd.value is not None and _decimal(request.estimated_cost_usd.value).quantize(Decimal("0.0001"), rounding=ROUND_CEILING) != price:
                return AdmissionDecision("rejected", False, None, ("source_mismatch",), state["revision"])
            active = sum((max(_decimal(v["reserved_cost_usd"]) - _decimal(v.get("incurred_usd", "0")), Decimal("0")) for v in state["reservations"].values()), Decimal("0"))
            spent = sum((_decimal(v.get("incurred_usd", "0")) for v in state["reservations"].values()), Decimal("0"))
            commitment = active + spent + price
            if commitment > Decimal("250") or commitment + Decimal("50") > Decimal("300"):
                return AdmissionDecision("rejected", False, None, ("budget_exhausted",), state["revision"])
            now = _utc(self.utc_now())
            deadline = min([now + timedelta(seconds=request.lifetime_cap_seconds)] + [now + timedelta(seconds=request.lifetime_cap_seconds - float(v)) for v in plan.allowance.provider_leads_seconds.values()])
            if plan.allowance.total_seconds > request.lifetime_cap_seconds or plan.allowance.total_seconds > (deadline-now).total_seconds():
                return AdmissionDecision("rejected", False, None, ("workflow_time_insufficient",), state["revision"])
            rid = hashlib.sha256(f"{self.scope_id}:{key}:{fingerprint}".encode()).hexdigest()[:32]
            evidence = ReservationEvidence(schema_version=2, campaign_id=request.campaign_id,
                reservation_id=Evidence[str](value=rid), state="reserved", reserved_cost_usd=Evidence[float](value=float(price)),
                cap_started_at=Evidence[datetime](value=now), expires_at=Evidence[datetime](value=now+timedelta(seconds=request.lifetime_cap_seconds)),
                cap_start_policy_id=StringEvidence(value="reservation-activation-before-provider-expiry"), source_path=request.source_path,
                source_sha256=request.source_sha256, reasons=[])
            rec = {"reservation_id": rid, "evidence": evidence.model_dump(mode="json"), "reserved_cost_usd": str(price), "incurred_usd": "0", "session": self.controller_session_id,
                   "cap_start_monotonic": self.monotonic_now(), "allowance": plan.allowance.total_seconds,
                   "allowance_fields": {"registration_seconds":plan.allowance.registration_seconds,"provision_seconds":plan.allowance.provision_seconds,
                   "readiness_seconds":plan.allowance.readiness_seconds,"stabilization_seconds":plan.allowance.stabilization_seconds,
                   "measurement_seconds":plan.allowance.measurement_seconds,"measurement_shutdown_seconds":plan.allowance.measurement_shutdown_seconds,
                   "retrieval_seconds":plan.allowance.retrieval_seconds,"cleanup_seconds":plan.allowance.cleanup_seconds}, "cleanup_open": False,
                   "request": request.model_dump(mode="json"), "provider_leads":dict(plan.allowance.provider_leads_seconds),
                   "provider_cleanup":{},
                   "child_identities":dict(plan.child_identities), "plan": {"fingerprint": fingerprint, "providers": [Provider(p).value for p in request.selected_providers], "support": plan.provider_support,"namespaces":dict(plan.namespaces)}}
            rec["context"] = str(getattr(campaign.context,"value",campaign.context))
            rec["replacement_context"] = campaign.replacement_context.model_dump(mode="json") if campaign.replacement_context else None
            state["reservations"][rid] = rec
            if replacement_of:
                original = state["reservations"][replacement_of]
                original["replacement_claimed"] = rid
                counts = state.setdefault("replacement_counts", {})
                failed_provider = plan.original_failure["failed_provider"]
                counts[failed_provider] = counts.get(failed_provider, 0) + 1
            for bucket, amount in plan.funding.items():
                remaining = _decimal(state["funding_remaining"].get(bucket, "0"))
                if amount > remaining:
                    return AdmissionDecision("rejected", False, None, ("budget_exhausted",), state["revision"])
            for bucket, amount in plan.funding.items():
                state["funding_remaining"][bucket] = str(_decimal(state["funding_remaining"][bucket]) - _decimal(amount))
            state["claims"][key] = {"fingerprint": fingerprint, "evidence": evidence.model_dump(mode="json")}
            state.setdefault("campaign_claims",{})[request.campaign_id] = state["claims"][key]
            state["source_history"].append({"path": plan.costs.source_path, "sha256": plan.costs.payload_sha256, "source_sha256": plan.costs.source_sha256})
            self._write(state)
            return AdmissionDecision("reserved", True, evidence, (), state["revision"], deadline)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)

    def check_stage_fit(self, reservation_id: str, *, stage: str, remaining: WorkflowAllowance) -> StageDecision:
        if stage not in {"registration", "preparation", "measurement", "retrieval", "cleanup"}:
            raise ValueError("unknown stage")
        state = self.inspect()
        rec = state["reservations"].get(reservation_id)
        if not rec: return StageDecision(False, ("ledger_unavailable",), None)
        originals = rec.get("allowance_fields", {})
        remain_fields = {"registration_seconds":remaining.registration_seconds,"provision_seconds":remaining.provision_seconds,
            "readiness_seconds":remaining.readiness_seconds,"stabilization_seconds":remaining.stabilization_seconds,
            "measurement_seconds":remaining.measurement_seconds,"measurement_shutdown_seconds":remaining.measurement_shutdown_seconds,
            "retrieval_seconds":remaining.retrieval_seconds,"cleanup_seconds":remaining.cleanup_seconds}
        reasons = []
        if any(name not in originals or value > originals[name] for name, value in remain_fields.items()):
            reasons.append("workflow_time_insufficient")
        completed = {"preparation": ("registration_seconds",),
            "measurement": ("registration_seconds","provision_seconds","readiness_seconds","stabilization_seconds"),
            "retrieval": ("registration_seconds","provision_seconds","readiness_seconds","stabilization_seconds","measurement_seconds","measurement_shutdown_seconds"),
            "cleanup": ("registration_seconds","provision_seconds","readiness_seconds","stabilization_seconds","measurement_seconds","measurement_shutdown_seconds","retrieval_seconds")}.get(stage, ())
        if any(remain_fields[name] != 0 for name in completed): reasons.append("workflow_time_insufficient")
        if stage == "cleanup" and remaining.cleanup_seconds <= 0: reasons.append("workflow_time_insufficient")
        evidence = ReservationEvidence.model_validate(rec["evidence"])
        deadline = evidence.expires_at.value
        if rec.get("provider_deadline"):
            deadline = min(deadline, datetime.fromisoformat(rec["provider_deadline"]))
        if rec["session"] != self.controller_session_id: reasons.append("controller_session_changed")
        now = _utc(self.utc_now())
        live = rec["allowance"] - (self.monotonic_now() - rec["cap_start_monotonic"])
        if live <= 0 or remaining.total_seconds > min(live, (deadline-now).total_seconds()): reasons.append("workflow_time_insufficient")
        return StageDecision(not reasons, tuple(reasons), deadline)

    def claim_creation(self, reservation_id: str, *, provider: Provider, expiry_requests: Sequence[ExpiryRequest], expiry_evidence: Sequence[ExpiryEvidence]) -> StageDecision:
        fit = self.check_stage_fit(reservation_id, stage="preparation", remaining=WorkflowAllowance(0,0,0,60,75,1,0,1,"f11-policy",{Provider(provider).value:0}))
        if not fit.eligible: return fit
        fd = self._lock()
        try:
            state = self._read(); rec = state["reservations"].get(reservation_id)
            if not rec: return StageDecision(False,("ledger_unavailable",),None)
            providers = set(rec["plan"]["providers"])
            reqs = {Provider(r.provider).value:r for r in expiry_requests}
            evs = {Provider(e.provider).value:e for e in expiry_evidence}
            if len(reqs) != len(expiry_requests) or len(evs) != len(expiry_evidence) or set(reqs) != providers or set(evs) != providers: return StageDecision(False,("expiry_required",),None)
            for name in providers:
                req, ev = reqs[name], evs[name]
                if (ev.state != "registered" or ev.run_id != req.run_id or ev.deployment_id != req.deployment_id
                    or ev.provider != req.provider or ev.action != req.action or ev.expires_at.value != req.expires_at.value
                    or ev.target_selector != req.target_selector or req.reservation_id.value != reservation_id
                    or req.resource_namespace != rec["plan"]["namespaces"].get(name)
                    or req.run_id != rec["child_identities"].get(name)):
                    return StageDecision(False,("expiry_mismatch",),None)
                if ev.registered_at.value is None or _utc(ev.registered_at.value) > _utc(self.utc_now()) or req.expires_at.value is None or _utc(req.expires_at.value) <= _utc(self.utc_now()):
                    return StageDecision(False,("expiry_mismatch",),None)
            deadline = min([ReservationEvidence.model_validate(rec["evidence"]).expires_at.value] + [_utc(r.expires_at.value) for r in reqs.values()])
            for name, req in reqs.items():
                lead = _duration(rec["provider_leads"].get(name, -1), "provider lead")
                cap_end = ReservationEvidence.model_validate(rec["evidence"]).expires_at.value
                if _utc(req.expires_at.value) != cap_end - timedelta(seconds=lead):
                    return StageDecision(False,("expiry_mismatch",),deadline)
            expiry_fingerprint = hashlib.sha256(json.dumps({"requests":[reqs[k].model_dump(mode="json") for k in sorted(reqs)],"evidence":[evs[k].model_dump(mode="json") for k in sorted(evs)]},sort_keys=True,default=str).encode()).hexdigest()
            if rec.get("expiry_fingerprint") and rec["expiry_fingerprint"] != expiry_fingerprint:
                return StageDecision(False,("expiry_mismatch",),deadline)
            required = rec["allowance"] - (self.monotonic_now() - rec["cap_start_monotonic"])
            if required <= 0 or (deadline - _utc(self.utc_now())).total_seconds() < required:
                return StageDecision(False,("workflow_time_insufficient",),deadline)
            rec["provider_deadline"] = deadline.isoformat()
            rec["expiry_fingerprint"] = expiry_fingerprint
            claims = rec.setdefault("creation_claims", [])
            if Provider(provider).value in claims: return StageDecision(True,("creation_already_claimed",),fit.effective_deadline,False)
            claims.append(Provider(provider).value)
            rec["cleanup_open"] = True
            if reservation_id not in state["cleanup_open"]: state["cleanup_open"].append(reservation_id)
            self._write(state)
            return StageDecision(True,(),fit.effective_deadline,True)
        finally: fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)

    def reconcile(self, event: Reconciliation) -> ReservationEvidence:
        if not event.event_id.strip() or not event.reservation_id.strip() or not isinstance(event.actual, bool):
            raise ValueError("reconciliation identity/status is invalid")
        if event.occurred_at is None: raise ValueError("reconciliation timestamp is required")
        occurred_at = _utc(event.occurred_at)
        _hash(event.source_sha256,"source_sha256")
        if event.incurred_usd is not None: _decimal(event.incurred_usd)
        fd=self._lock()
        try:
            state=self._read(); payload={"reservation_id":event.reservation_id,"provider":Provider(event.provider).value,"source_sha256":event.source_sha256,
                "incurred_usd":None if event.incurred_usd is None else str(event.incurred_usd),"actual":event.actual,
                "cleanup_verified_absent":event.cleanup_verified_absent,"billable_resources_present":event.billable_resources_present,"failure":event.failure,
                "occurred_at":occurred_at.isoformat()}
            digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
            old=state["events"].get(event.event_id)
            if old:
                if old["digest"]!=digest: raise ValueError("duplicate_conflict")
                return ReservationEvidence.model_validate(state["reservations"][event.reservation_id]["evidence"])
            rec=state["reservations"].get(event.reservation_id)
            if rec is None: raise ValueError("identity_mismatch")
            provider = Provider(event.provider).value
            if provider not in rec["plan"]["providers"]: raise ValueError("identity_mismatch")
            if event.incurred_usd is not None: rec["incurred_usd"]=str(_decimal(rec["incurred_usd"])+_decimal(event.incurred_usd))
            if _decimal(rec["incurred_usd"]) > _decimal(rec["reserved_cost_usd"]):
                blocked=state.setdefault("budget_blocked",[])
                if event.reservation_id not in blocked: blocked.append(event.reservation_id)
            if event.failure is not None:
                if not isinstance(event.failure, Mapping) or not event.failure:
                    raise ValueError("failure evidence must be structured")
                secret_key = re.compile(r"(?i)(password|passwd|token|secret|credential|private[_-]?key|access[_-]?key)")
                def inspect_secret(value: Any, key: str = "") -> bool:
                    if secret_key.search(key): return True
                    if isinstance(value, Mapping): return any(inspect_secret(v, str(k)) for k,v in value.items())
                    if isinstance(value, (list, tuple)): return any(inspect_secret(v) for v in value)
                    return isinstance(value, str) and (("-----BEGIN " in value and "PRIVATE KEY-----" in value) or bool(re.search(r"(?i)(password|passwd|token|secret|credential)\s*[:=]", value)))
                if inspect_secret(event.failure): raise ValueError("failure evidence contains secret-bearing material")
                provider = Provider(event.provider).value
                rec.setdefault("failures", []).append({"provider":provider,"evidence":dict(event.failure)})
                rec.setdefault("failures_by_provider", {})[provider] = dict(event.failure)
                rec.setdefault("failure", dict(event.failure))
                rec.setdefault("failure_provider", provider)
            if event.cleanup_verified_absent and event.billable_resources_present is False:
                rec["provider_cleanup"][provider] = True
                if all(rec["provider_cleanup"].get(name) is True for name in rec["plan"]["providers"]):
                    rec["cleanup_open"]=False
                    if event.reservation_id in state["cleanup_open"]: state["cleanup_open"].remove(event.reservation_id)
                    if event.reservation_id in state["cleanup_blocked"]: state["cleanup_blocked"].remove(event.reservation_id)
            else:
                if event.reservation_id not in state["cleanup_blocked"]: state["cleanup_blocked"].append(event.reservation_id)
            state["events"][event.event_id]={"digest":digest,"payload":payload}
            self._write(state)
            return ReservationEvidence.model_validate(rec["evidence"])
        finally: fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)

    @staticmethod
    def _validate_gate_evidence(evidence: Mapping[str, Any]) -> None:
        if not isinstance(evidence, Mapping) or not evidence.get("evidence_id") or not evidence.get("source_path"):
            raise ValueError("gate evidence must have source identity")
        _hash(evidence.get("source_sha256", ""), "source_sha256")


def preview(schedule: Path, config_map: Path, cost_inputs: Path, now: datetime, *, root: Path) -> dict[str, Any]:
    mapping=json.loads(config_map.read_text(encoding="utf-8"))
    paths={key:(root/value if not Path(value).is_absolute() else Path(value)) for key,value in mapping.items()}
    entries=load_schedule(schedule,config_paths=paths,repository_root=root)
    campaigns={}
    for entry in entries:
        from .config import resolve_campaign
        campaigns[entry.entry_id]=resolve_campaign(paths[entry.config_id],root,schedule_entry=entry)[0]
    inspection=inspect_schedule(entries,campaigns_by_entry_id=campaigns,now=now)
    costs=load_cost_inputs(cost_inputs)
    pending={k:list(v) for k,v in inspection.pending_by_entry.items()}
    return {"status":"inspectable","paid_eligible":False,"entries":[e.model_dump(mode="json") for e in entries],"pending":pending,
            "cost_input":{"id":costs.cost_input_id,"sha256":costs.payload_sha256,"prices_checked_on":costs.prices_checked_on,"refresh_required":costs.refresh_required},
            "schedule":{"complete_primary_week":inspection.complete_primary_week,"errors":list(inspection.errors),"missed_entries":list(inspection.missed_entries),
                        "simultaneous_vm_demand":dict(inspection.simultaneous_vm_demand)},
            "reason_codes":sorted({reason for reasons in pending.values() for reason in reasons} | {"prerequisite_pending"}),"external_calls":0}


def main(argv: Sequence[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    p=sub.add_parser("preview",help="read-only local schedule preview")
    p.add_argument("--schedule",required=True,type=Path);p.add_argument("--config-map",required=True,type=Path);p.add_argument("--cost-inputs",required=True,type=Path);p.add_argument("--now",required=True)
    args=parser.parse_args(argv)
    try:
        now=datetime.fromisoformat(args.now.replace("Z","+00:00")); _utc(now)
        report=preview(args.schedule,args.config_map,args.cost_inputs,now,root=Path.cwd())
        print(json.dumps(report,sort_keys=True,separators=(",",":")))
        return 0
    except Exception as exc:
        print(json.dumps({"status":"invalid","paid_eligible":False,"reason_codes":["invalid_schedule"],"error":str(exc)},sort_keys=True),file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
