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
from datetime import date
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


def minimum_protocol_seconds(mode: ExperimentMode) -> int:
    return {ExperimentMode.ACCEPTANCE:75, ExperimentMode.SHORT_STUDY:225,
        ExperimentMode.CALIBRATION:950, ExperimentMode.DIAGNOSTIC_DAY:86407}[ExperimentMode(mode)]


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


COST_COMPONENTS = (
    "compute", "disks", "addresses", "traffic_export", "tax", "uncertainty", "expiry_cleanup_support",
)


def _validated_cost_inputs(inputs: CostInputs) -> CostInputs:
    """Re-read the declared source so a copied CostInputs object cannot forge saved bytes."""
    loaded = load_cost_inputs(Path(inputs.source_path))
    if (loaded.payload_sha256 != inputs.payload_sha256 or loaded.source_sha256 != inputs.source_sha256
        or loaded.cost_input_id != inputs.cost_input_id or loaded.source_scope != inputs.source_scope
        or loaded.payload != inputs.payload or loaded.refresh_required != inputs.refresh_required):
        raise ValueError("source_mismatch: saved cost input changed or does not match its validated payload")
    return loaded


def _campaign_provider_regions(campaign: Any) -> dict[str, list[str]]:
    observations = {Provider(item.provider).value: item for item in campaign.observations}
    result: dict[str, list[str]] = {}
    for provider in campaign.selected_providers:
        name = Provider(provider).value
        observation = observations.get(name)
        if observation is None:
            raise ValueError("identity_mismatch: resolved provider observation is missing")
        vm_regions = (observation.provider_config.regions.vm_a, observation.provider_config.regions.vm_b)
        if any(not isinstance(region, str) or not region.strip() for region in vm_regions):
            raise ValueError("identity_mismatch: provider region set must identify both VM locations")
        # Same-zone runs have two VMs in one region; inter-region runs have two.
        regions = sorted(set(vm_regions))
        result[name] = regions
    return result


def _validate_cost_envelopes(plan: "AdmissionPlan", request: ReservationRequest) -> tuple[Decimal, dict[str, Decimal], dict[str, Decimal]]:
    """Validate source-backed per-provider run envelopes and derive funding totals."""
    _validated_cost_inputs(plan.costs)
    selected = {Provider(p).value for p in request.selected_providers}
    if not isinstance(plan.cost_envelopes, Mapping) or set(plan.cost_envelopes) != selected:
        raise ValueError("cost_envelope_missing: one per-provider run envelope is required")
    expected_regions = _campaign_provider_regions(plan.resolved_campaign)
    totals: dict[str, Decimal] = {}
    bucket_totals: dict[str, Decimal] = {}
    support_totals: dict[str, Decimal] = {}
    common_sources: dict[tuple[str, str], Mapping[str, Any]] = {}
    for provider in sorted(selected):
        envelope = plan.cost_envelopes[provider]
        required = {"envelope_id", "provider", "run_id", "priced_on", "source_kind", "source_path",
            "source_sha256", "regions", "unit_basis", "components"}
        if not isinstance(envelope, Mapping) or set(envelope) != required:
            raise ValueError("cost_envelope_invalid: missing or unknown envelope fields")
        if (not all(isinstance(envelope.get(key), str) and envelope[key].strip() for key in
            ("envelope_id", "provider", "run_id", "priced_on", "source_kind", "source_path", "unit_basis"))
            or envelope["provider"] != provider or envelope["run_id"] != plan.child_identities.get(provider)
            or envelope["unit_basis"] != "one-two-vm-provider-run"
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", envelope["priced_on"])):
            raise ValueError("cost_envelope_invalid: envelope identity, date or unit basis mismatch")
        date.fromisoformat(envelope["priced_on"])
        _hash(envelope["source_sha256"], "cost envelope source hash")
        if envelope["regions"] != expected_regions[provider]:
            raise ValueError("cost_envelope_invalid: provider regions do not match resolved campaign")
        source_path = Path(envelope["source_path"])
        try:
            raw = source_path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != envelope["source_sha256"]:
                raise ValueError("cost_envelope_invalid: source bytes changed")
            source = json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("cost_envelope_invalid: envelope source is unavailable") from exc
        if not isinstance(source, Mapping) or source.get("schema_version") != 1:
            raise ValueError("cost_envelope_invalid: invalid envelope source")
        if envelope["source_kind"] == "synthetic-test-only":
            if plan.execution_scope != "offline_simulation" or source.get("fixture_scope") != "synthetic-test-only":
                raise ValueError("cost_envelope_invalid: synthetic cost evidence cannot authorize paid requests")
            source_row = source.get("provider_run_envelopes", {}).get(provider)
        elif envelope["source_kind"] == "provider-price-input":
            if plan.execution_scope != "paid_request" or envelope["source_path"] != plan.costs.source_path or envelope["source_sha256"] != plan.costs.payload_sha256:
                raise ValueError("cost_envelope_invalid: paid envelope must bind the refreshed cost input")
            source_row = plan.costs.payload.get("provider_run_envelopes", {}).get(provider)
        else:
            raise ValueError("cost_envelope_invalid: unknown source kind")
        if not isinstance(source_row, Mapping):
            raise ValueError("cost_envelope_invalid: source has no matching provider run row")
        public_envelope = {key: value for key, value in envelope.items() if key not in {"source_path", "source_sha256"}}
        if dict(source_row) != public_envelope:
            raise ValueError("cost_envelope_invalid: envelope differs from saved source row")
        if not isinstance(envelope["components"], Mapping) or set(envelope["components"]) != set(COST_COMPONENTS):
            raise ValueError("cost_envelope_invalid: every cost component is required exactly once")
        total = Decimal("0")
        for component_name in COST_COMPONENTS:
            component = envelope["components"][component_name]
            if not isinstance(component, Mapping) or set(component) != {"amount_usd", "quantity", "unit", "inclusion_rule", "funding_bucket"}:
                raise ValueError(f"cost_envelope_invalid: {component_name} requires amount, quantity, unit and inclusion rule")
            amount = _decimal(component["amount_usd"], f"{component_name} amount")
            quantity = _decimal(component["quantity"], f"{component_name} quantity")
            if (quantity <= 0 or not isinstance(component["unit"], str) or not component["unit"].strip()
                or not isinstance(component["inclusion_rule"], str) or not component["inclusion_rule"].strip()
                or component["funding_bucket"] not in plan.funding):
                raise ValueError(f"cost_envelope_invalid: {component_name} scope or funding bucket is missing")
            total += amount
            bucket_totals[component["funding_bucket"]] = bucket_totals.get(component["funding_bucket"], Decimal("0")) + amount
            if component_name == "expiry_cleanup_support": support_totals[provider] = amount
        totals[provider] = total.quantize(Decimal("0.0001"), rounding=ROUND_CEILING)
        source_key = (envelope["source_path"], envelope["source_sha256"])
        common_sources[source_key] = source
        support = plan.provider_support.get(provider)
        if support is not None:
            support_regions = sorted(support.get("expiry_cost_regions", expected_regions[provider]))
            if support_regions != expected_regions[provider]:
                raise ValueError("cost_envelope_invalid: support region set does not match campaign")
            inclusion_key = "expiry_cost_included_usd" if "expiry_cost_included_usd" in support else "included_usd"
            if inclusion_key in support and _decimal(support[inclusion_key]) != support_totals[provider]:
                raise ValueError("cost_envelope_invalid: support inclusion differs from envelope")
    total = sum(totals.values(), Decimal("0")).quantize(Decimal("0.0001"), rounding=ROUND_CEILING)
    if set(bucket_totals) != set(plan.funding) or any(
        bucket_totals.get(bucket, Decimal("0")).quantize(Decimal("0.0001"), rounding=ROUND_CEILING)
        != _decimal(amount).quantize(Decimal("0.0001"), rounding=ROUND_CEILING)
        for bucket, amount in plan.funding.items()
    ):
        raise ValueError("cost_envelope_invalid: funding moves do not equal sourced component totals")
    if request.estimated_cost_usd.value is None or _decimal(request.estimated_cost_usd.value).quantize(Decimal("0.0001"), rounding=ROUND_CEILING) != total:
        raise ValueError("cost_envelope_invalid: request estimate differs from complete provider envelopes")
    return total, bucket_totals, support_totals


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
    if not isinstance(data["cost_input_id"],str) or not data["cost_input_id"].strip() or not isinstance(data["source_scope"],list) or not data["source_scope"] or any(not isinstance(item,str) or not item.strip() for item in data["source_scope"]):
        raise ValueError("cost input identity and scope must be nonblank")
    try:
        if not isinstance(data["prices_checked_on"], str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", data["prices_checked_on"]):
            raise ValueError("prices_checked_on must use YYYY-MM-DD")
        date.fromisoformat(data["prices_checked_on"])
        if "acquired_on" in data:
            if not isinstance(data["acquired_on"], str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", data["acquired_on"]):
                raise ValueError("acquired_on must use YYYY-MM-DD")
            date.fromisoformat(data["acquired_on"])
    except (TypeError, ValueError) as exc:
        raise ValueError("cost input dates must use ISO YYYY-MM-DD") from exc
    source_hash = _hash(data["source_sha256"], "source_sha256")
    if "continuation_sha256" in data: _hash(data["continuation_sha256"],"continuation_sha256")
    if not isinstance(data["funding"],dict):
        raise ValueError("funding must be a named object")
    for name, value in data["funding"].items():
        if not isinstance(name,str) or not name.strip():
            raise ValueError("funding names must be nonblank")
        _decimal(value, f"funding {name}")
    if not isinstance(data.get("refresh_required_before_paid_execution",False),bool):
        raise ValueError("price refresh status must be boolean")
    if not isinstance(data.get("live_quote",False),bool):
        raise ValueError("live quote status must be boolean")
    scope_identity_kind=data["source_identity_kind"]
    if not isinstance(scope_identity_kind,str) or not scope_identity_kind.strip():
        raise ValueError("source identity kind must be nonblank")
    return CostInputs(data["cost_input_id"], data["prices_checked_on"], path.as_posix(), source_hash,
                      hashlib.sha256(raw).hexdigest(), data, tuple(data["source_scope"]),
                      bool(data.get("refresh_required_before_paid_execution", False)))


def aws_expiry_cost_handoff(cost_inputs: CostInputs, aws_fixture: Path, regions: Sequence[str], *, reservation_sha256: str) -> dict[str, Any]:
    """Build the explicit F05 payload; its support amount is included in the reservation."""
    _hash(reservation_sha256, "reservation_sha256")
    if reservation_sha256 != cost_inputs.payload_sha256:
        raise ValueError("source_mismatch")
    raw = aws_fixture.read_bytes()
    fixture = json.loads(raw)
    digest = hashlib.sha256(raw).hexdigest()
    if (digest != "315d3e49755396597f97cac3512b9843fcf1c95083fadc11fae667c770215a61"
        or fixture.get("free_tier_assumed") is not False
        or fixture.get("id") != "aws-ssm-automation-public-2026-10-22"
        or _decimal(fixture.get("worst_planned_cost_per_region")) != Decimal("0.0139")):
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
    completed_stages: tuple[str, ...] = ()

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

    @property
    def preparation_seconds(self) -> float:
        return sum(getattr(self, name) for name in ("registration_seconds", "provision_seconds", "readiness_seconds", "stabilization_seconds"))

    def fit_seconds(self, *, now: datetime, scheduled_start: datetime | None = None, preparation_can_overlap: bool = True) -> float:
        """Conservative end-to-end remainder; waiting consumes the same original cap."""
        preparation = self.preparation_seconds
        wait = max(0.0, (_utc(scheduled_start) - _utc(now)).total_seconds()) if scheduled_start else 0.0
        if preparation_can_overlap:
            preparation = max(preparation, wait)
        else:
            preparation += wait
        return preparation + self.measurement_seconds + self.retrieval_seconds + self.cleanup_seconds


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
    cost_envelopes: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.execution_scope not in {"offline_simulation", "paid_request"}:
            raise ValueError("unknown execution scope")
        if len(set(self.child_identities.values())) != len(self.child_identities):
            raise ValueError("child identities must be unique")
        if any(not key.strip() or not value.strip() for mapping in (self.child_identities, self.namespaces) for key, value in mapping.items()):
            raise ValueError("provider identities and namespaces must be nonblank")
        if len(set(self.namespaces.values())) != len(self.namespaces):
            raise ValueError("namespace collisions are not allowed")
        if any(not key.strip() for key in self.funding):
            raise ValueError("funding bucket identities must be nonblank")
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
    if cleanup <= 0 or retry <= 0 or not reservation_id.strip() or len(set(failed_artifact_ids)) != len(failed_artifact_ids):
        raise ValueError("copy policy identity/cleanup is invalid")
    if any(not item.strip() for item in failed_artifact_ids) or any(not item.strip() for item in already_retried):
        raise ValueError("copy artifact identities must be nonblank")
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
    billing_final: bool = False
    released_funding_by_bucket: Mapping[str, Decimal] = field(default_factory=dict)
    release_evidence_id: str | None = None
    release_source_path: str | None = None
    source_path: str | None = None
    absence_evidence_id: str | None = None
    absent_resource_ids: tuple[str, ...] = ()


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
            if os.path.lexists(self.path) or os.path.lexists(self.lock_path):
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
            fd = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > 16 * 1024 * 1024:
                    raise ValueError("ledger_unavailable")
                chunks = []
                remaining = info.st_size
                while remaining:
                    chunk = os.read(fd, min(remaining, 65536))
                    if not chunk:
                        raise ValueError("ledger_unavailable")
                    chunks.append(chunk)
                    remaining -= len(chunk)
                raw = b"".join(chunks)
            finally:
                os.close(fd)
            def unique(pairs):
                obj = {}
                for key, value in pairs:
                    if key in obj: raise ValueError("ledger_unavailable")
                    obj[key] = value
                return obj
            state = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
        except Exception as exc:
            raise ValueError("ledger_unavailable") from exc
        required = {"schema_version", "scope_id", "revision", "funding_remaining", "reservations", "claims", "events", "cleanup_open", "cleanup_blocked", "source_history"}
        allowed = required | {"campaign_claims", "replacement_counts", "budget_blocked", "active_cost_source"}
        if (not isinstance(state, dict) or not required <= set(state) or set(state) - allowed
            or state.get("schema_version") != 1 or state.get("scope_id") != self.scope_id
            or isinstance(state.get("revision"), bool) or not isinstance(state.get("revision"), int) or state["revision"] < 1
            or not all(isinstance(state.get(key), dict) for key in ("funding_remaining", "reservations", "claims", "events"))
            or not all(isinstance(state.get(key), list) for key in ("cleanup_open", "cleanup_blocked", "source_history"))):
            raise ValueError("ledger_unavailable")
        if any(key in state and not isinstance(state[key], dict) for key in ("campaign_claims","replacement_counts")):
            raise ValueError("ledger_unavailable")
        try:
            if any(key not in {"primary","replacements","tax","uncertainty","optional"} for key in state["funding_remaining"]):
                raise ValueError("ledger_unavailable")
            for amount in state["funding_remaining"].values(): _decimal(amount)
            if (len(set(state["cleanup_open"])) != len(state["cleanup_open"])
                or len(set(state["cleanup_blocked"])) != len(state["cleanup_blocked"])
                or any(item not in state["reservations"] for item in state["cleanup_open"] + state["cleanup_blocked"])):
                raise ValueError("ledger_unavailable")
            for item in state["source_history"]:
                if not isinstance(item, dict) or not item.get("path") or not item["path"].strip():
                    raise ValueError("ledger_unavailable")
                _hash(item.get("sha256", ""), "source history hash")
                _hash(item.get("source_sha256", ""), "source history source hash")
            for event_id, item in state["events"].items():
                if not event_id.strip() or not isinstance(item, dict) or set(item) != {"digest", "payload"}:
                    raise ValueError("ledger_unavailable")
                if not isinstance(item["payload"],dict):
                    raise ValueError("ledger_unavailable")
                _hash(item["digest"], "event digest")
                digest = hashlib.sha256(json.dumps(item["payload"], sort_keys=True).encode()).hexdigest()
                if digest != item["digest"] or item["payload"].get("reservation_id") not in state["reservations"]:
                    raise ValueError("ledger_unavailable")
            for reservation_id, item in state["reservations"].items():
                if not isinstance(item, dict) or item.get("reservation_id") != reservation_id:
                    raise ValueError("ledger_unavailable")
                evidence = ReservationEvidence.model_validate(item.get("evidence"))
                if evidence.reservation_id.value != reservation_id:
                    raise ValueError("ledger_unavailable")
                _decimal(item.get("reserved_cost_usd"), "reserved cost")
                _decimal(item.get("incurred_usd", "0"), "incurred cost")
                _decimal(item.get("released_usd", "0"), "released funding")
                _duration(item.get("cap_start_monotonic"), "monotonic activation")
                _duration(item.get("allowance"), "workflow allowance")
                _hash(item.get("plan", {}).get("fingerprint", ""), "plan fingerprint")
                providers = item.get("plan", {}).get("providers", [])
                if not providers or len(set(providers)) != len(providers):
                    raise ValueError("ledger_unavailable")
                ProviderSet = {Provider(provider).value for provider in providers}
                if set(item.get("child_identities", {})) != ProviderSet or set(item.get("plan", {}).get("namespaces", {})) != ProviderSet:
                    raise ValueError("ledger_unavailable")
                if len(set(item.get("creation_claims", []))) != len(item.get("creation_claims", [])) or not set(item.get("creation_claims", [])) <= ProviderSet:
                    raise ValueError("ledger_unavailable")
            for key, claim in state["claims"].items():
                if not key.strip() or not isinstance(claim, dict) or set(claim) != {"fingerprint", "evidence"}:
                    raise ValueError("ledger_unavailable")
                _hash(claim["fingerprint"], "claim fingerprint")
                claim_evidence=ReservationEvidence.model_validate(claim["evidence"])
                if claim_evidence.reservation_id.value not in state["reservations"]:
                    raise ValueError("ledger_unavailable")
            if "campaign_claims" in state:
                for key, claim in state["campaign_claims"].items():
                    if not key.strip() or not isinstance(claim, dict) or set(claim) != {"fingerprint", "evidence"}:
                        raise ValueError("ledger_unavailable")
                    _hash(claim["fingerprint"], "campaign claim fingerprint")
                    claim_evidence=ReservationEvidence.model_validate(claim["evidence"])
                    if claim_evidence.reservation_id.value not in state["reservations"]:
                        raise ValueError("ledger_unavailable")
            if "replacement_counts" in state:
                for provider, count in state["replacement_counts"].items():
                    if provider not in {p.value for p in Provider} or isinstance(count,bool) or not isinstance(count,int) or count < 0:
                        raise ValueError("ledger_unavailable")
            if "budget_blocked" in state and (not isinstance(state["budget_blocked"],list)
                or any(item not in state["reservations"] for item in state["budget_blocked"])):
                raise ValueError("ledger_unavailable")
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
        inputs = _validated_cost_inputs(inputs)
        fd = self._lock(initialize=True)
        try:
            os.write(fd, json.dumps({"schema_version": 1, "scope_id": self.scope_id}, sort_keys=True).encode())
            os.fsync(fd)
            state = self._read(initializing=True)
            funding = inputs.payload.get("funding", {})
            state["funding_remaining"] = {k: str(_decimal(v, k)) for k, v in funding.items() if k in {"primary", "replacements", "tax", "uncertainty", "optional"}}
            state["active_cost_source"] = {"path": inputs.source_path, "sha256": inputs.payload_sha256, "source_sha256": inputs.source_sha256}
            state["source_history"].append({"path": inputs.source_path, "sha256": inputs.payload_sha256, "source_sha256": inputs.source_sha256})
            self._write(state)
            return state
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)

    def inspect(self) -> Mapping[str, Any]:
        fd = self._lock()
        try: return self._read()
        finally: fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)

    def reconcile_cost_source(self, inputs: CostInputs, review_evidence: Mapping[str, Any]) -> Mapping[str, Any]:
        """Switch dated cost inputs only with a durable, source-linked human review record."""
        inputs = _validated_cost_inputs(inputs)
        self._validate_gate_evidence(review_evidence)
        if (review_evidence.get("reviewed_payload_sha256") != inputs.payload_sha256
            or review_evidence.get("scope_id") != self.scope_id
            or review_evidence.get("status") != "reviewed"):
            raise ValueError("source_mismatch: cost source review does not cover this payload and scope")
        fd = self._lock()
        try:
            state = self._read()
            current = state.get("active_cost_source")
            if current and current.get("sha256") == inputs.payload_sha256:
                return state
            new_funding = inputs.payload.get("funding", {})
            consumed: dict[str, Decimal] = {}
            for rec in state["reservations"].values():
                for bucket, amount in rec.get("funding_moves", {}).items():
                    released = _decimal(rec.get("released_by_bucket", {}).get(bucket, "0"))
                    consumed[bucket] = consumed.get(bucket, Decimal("0")) + max(_decimal(amount)-released, Decimal("0"))
            updated = {}
            for bucket in {"primary", "replacements", "tax", "uncertainty", "optional"}:
                remaining = _decimal(new_funding.get(bucket, "0")) - consumed.get(bucket, Decimal("0"))
                if remaining < 0:
                    raise ValueError("budget_exhausted: revised source is below preserved commitments")
                if remaining > 0 or bucket in new_funding:
                    updated[bucket] = str(remaining)
            state["funding_remaining"] = updated
            state["active_cost_source"] = {"path": inputs.source_path, "sha256": inputs.payload_sha256, "source_sha256": inputs.source_sha256}
            state["source_history"].append({"path": inputs.source_path, "sha256": inputs.payload_sha256,
                "source_sha256": inputs.source_sha256, "review_evidence_id": review_evidence["evidence_id"]})
            self._write(state)
            return state
        finally: fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)

    def reserve(self, request: ReservationRequest, *, plan: AdmissionPlan) -> AdmissionDecision:
        campaign = plan.resolved_campaign
        if request.config_id != campaign.config_id or request.mode != campaign.mode or request.priority != campaign.admission.priority:
            return AdmissionDecision("rejected", False, None, ("identity_mismatch",), 0)
        if {Provider(p) for p in request.selected_providers} != {Provider(p) for p in campaign.selected_providers}:
            return AdmissionDecision("rejected", False, None, ("identity_mismatch",), 0)
        if campaign.admission.cost_input_id != plan.costs.cost_input_id:
            return AdmissionDecision("rejected", False, None, ("source_mismatch",), 0)
        source_entry = campaign.schedule_entry.value
        if campaign.admission.requires_schedule:
            if source_entry is None or request.schedule_entry_id.value != source_entry.entry_id:
                return AdmissionDecision("rejected", False, None, ("identity_mismatch",), 0)
        elif request.schedule_entry_id.value is not None and source_entry is None:
            return AdmissionDecision("rejected", False, None, ("identity_mismatch",), 0)
        configured_hash = campaign.admission.cost_input_sha256.value
        if configured_hash is not None and configured_hash != plan.costs.source_sha256:
            return AdmissionDecision("rejected", False, None, ("source_mismatch",), 0)
        configured_estimate = campaign.admission.estimated_cost_usd.value
        if configured_estimate is not None and request.estimated_cost_usd.value != configured_estimate:
            return AdmissionDecision("rejected", False, None, ("source_mismatch",), 0)
        if request.source_sha256 != plan.costs.payload_sha256 or request.source_path != plan.costs.source_path:
            return AdmissionDecision("rejected", False, None, ("source_mismatch",), 0)
        try:
            envelope_total, envelope_buckets, envelope_support = _validate_cost_envelopes(plan, request)
        except ValueError as exc:
            reason = "source_mismatch" if "source_mismatch" in str(exc) else "cost_envelope_invalid"
            return AdmissionDecision("rejected", False, None, (reason,), 0)
        reasons = []
        support_components: list[Decimal] = []
        if plan.execution_scope == "paid_request":
            if plan.costs.refresh_required: reasons.append("price_refresh_required")
            required = {ExperimentMode.ACCEPTANCE:{"f10_offline","paid_authorization","quota_review"},
                ExperimentMode.SHORT_STUDY:{"f10_offline","paid_authorization","quota_review","common_n","study_window"},
                ExperimentMode.CALIBRATION:{"f10_offline","paid_authorization","quota_review","calibration_authorized"},
                ExperimentMode.DIAGNOSTIC_DAY:{"f10_offline","paid_authorization","quota_review","common_n","diagnostic_day_authorized","size_export_review"}}[ExperimentMode(campaign.mode)]
            if ExperimentMode(campaign.mode) != ExperimentMode.ACCEPTANCE:
                required.add("paid_acceptance")
            if not required <= set(plan.prerequisites) or any(not isinstance(plan.prerequisites.get(k), Mapping) for k in required):
                reasons.append("prerequisite_pending")
            else:
                try:
                    for key in required: self._validate_gate_evidence(plan.prerequisites[key])
                    if "paid_acceptance" in required:
                        acceptance = plan.prerequisites["paid_acceptance"]
                        if (acceptance.get("status") != "completed"
                            or acceptance.get("config_id") != campaign.config_id
                            or set(acceptance.get("providers", [])) != {Provider(p).value for p in campaign.selected_providers}):
                            raise ValueError("paid acceptance scope does not match requested work")
                except ValueError:
                    reasons.append("prerequisite_pending")
            if plan.optional_reviewed is False and request.priority in {"P2", "P3"}: reasons.append("optional_review_required")
            for provider in request.selected_providers:
                support = plan.provider_support.get(Provider(provider).value)
                if (not support or not support.get("source_sha256")
                    or not (support.get("included_usd") or support.get("expiry_cost_included_usd"))):
                    reasons.append("support_cost_missing")
                elif (support.get("source_sha256") != plan.costs.payload_sha256
                    or support.get("source_path") != plan.costs.source_path):
                    reasons.append("support_cost_missing")
                else:
                    try:
                        _hash(support["source_sha256"], "support source hash")
                        include_key = "expiry_cost_included_usd" if "expiry_cost_included_usd" in support else "included_usd"
                        support_amount = _decimal(support[include_key], "provider support inclusion")
                        if not support["source_path"].strip() or support_amount <= 0:
                            raise ValueError("support inclusion must be sourced and positive")
                        if Provider(provider) == Provider.AWS:
                            support_scope = plan.costs.payload.get("provider_support", {}).get("aws", {})
                            if (support.get("expiry_input_id") != "aws-ssm-automation-public-2026-10-22"
                                or support.get("expiry_input_sha256") != support_scope.get("sha256")
                                or _hash(support.get("expiry_input_sha256", ""), "provider support hash") != support_scope.get("sha256")
                                or not support.get("expiry_cost_regions")
                                or len(set(support["expiry_cost_regions"])) != len(support["expiry_cost_regions"])):
                                raise ValueError("AWS support evidence does not match its dated fixture")
                            per_region = _decimal(support_scope.get("per_region_usd"), "AWS support rate")
                            if support_amount < (per_region * len(support["expiry_cost_regions"])):
                                raise ValueError("AWS support inclusion undercounts selected regions")
                        if support_amount != envelope_support.get(Provider(provider).value):
                            raise ValueError("support inclusion differs from per-provider envelope")
                        support_components.append(support_amount)
                    except (KeyError, ValueError, AttributeError):
                        reasons.append("support_cost_missing")
        if request.priority in {"P2", "P3"}:
            if plan.execution_scope != "paid_request" or not plan.optional_reviewed or set(plan.funding) != {"optional"}:
                reasons.append("optional_review_required")
            required_optional = {"execution_review", "analysis_review", "budget_review", "primary_protection"}
            if not required_optional <= set(plan.optional_review_evidence):
                reasons.append("optional_review_required")
            else:
                try:
                    for key in required_optional:
                        self._validate_gate_evidence(plan.optional_review_evidence[key])
                except ValueError:
                    reasons.append("optional_review_required")
        elif "optional" in plan.funding:
            reasons.append("budget_exhausted")
        if campaign.mode in {ExperimentMode.SHORT_STUDY, ExperimentMode.DIAGNOSTIC_DAY} and campaign.admission.requires_common_n and campaign.benchmark.common_n.state != "frozen":
            reasons.append("calibration_pending")
        selected = {Provider(p).value for p in request.selected_providers}
        if set(plan.child_identities) != selected or set(plan.namespaces) != selected or set(plan.allowance.provider_leads_seconds) != selected:
            reasons.append("identity_mismatch")
        min_measurement = minimum_protocol_seconds(campaign.mode)
        if plan.allowance.stabilization_seconds != 60 or plan.allowance.measurement_shutdown_seconds <= 0 or plan.allowance.measurement_seconds < min_measurement + plan.allowance.measurement_shutdown_seconds:
            reasons.append("workflow_time_insufficient")
        if campaign.admission.requires_schedule and campaign.scheduled_start is None:
            reasons.append("schedule_pending")
        if reasons:
            return AdmissionDecision("pending", False, None, tuple(sorted(set(reasons))), 0)
        fd = self._lock()
        try:
            state = self._read()
            if state.get("budget_blocked"):
                return AdmissionDecision("rejected", False, None, ("budget_exhausted",), state["revision"])
            mode = ExperimentMode(campaign.mode)
            key = request.campaign_id if mode in {ExperimentMode.CALIBRATION, ExperimentMode.DIAGNOSTIC_DAY} or request.schedule_entry_id.value is None else request.schedule_entry_id.value
            plan_payload = {"scope": plan.execution_scope, "prerequisites": dict(plan.prerequisites),
                "funding": {k: str(v) for k,v in plan.funding.items()}, "children": dict(plan.child_identities),
                "config_source_sha256": campaign.config_source.sha256, "resolved_sha256": campaign.resolved_sha256,
                "schedule_entry": source_entry.model_dump(mode="json") if source_entry else None,
                "namespaces": dict(plan.namespaces), "allowance": {"registration":plan.allowance.registration_seconds,
                "provision":plan.allowance.provision_seconds,"readiness":plan.allowance.readiness_seconds,
                "stabilization":plan.allowance.stabilization_seconds,"measurement":plan.allowance.measurement_seconds,
                "shutdown":plan.allowance.measurement_shutdown_seconds,"retrieval":plan.allowance.retrieval_seconds,
                "cleanup":plan.allowance.cleanup_seconds,"source_id":plan.allowance.source_id,
                "provider_leads":dict(plan.allowance.provider_leads_seconds)},
                "provider_support":dict(plan.provider_support), "original_failure":plan.original_failure,
                "cost_envelopes":dict(plan.cost_envelopes),
                "optional_reviewed":plan.optional_reviewed,"optional_review_evidence":dict(plan.optional_review_evidence),
                "observation_id":plan.observation_id,"scheduled_start":campaign.scheduled_start.isoformat() if campaign.scheduled_start else None}
            fingerprint = hashlib.sha256(json.dumps({"request": request.model_dump(mode="json"), "source": plan.costs.payload_sha256, "plan":plan_payload}, sort_keys=True, default=str).encode()).hexdigest()
            prior = state["claims"].get(key) or state.get("campaign_claims",{}).get(request.campaign_id)
            if prior:
                if prior["fingerprint"] != fingerprint:
                    return AdmissionDecision("rejected", False, None, ("duplicate_conflict",), state["revision"])
                ev = ReservationEvidence.model_validate(prior["evidence"])
                return AdmissionDecision("reserved", False, ev, (), state["revision"])
            active_source = state.get("active_cost_source")
            if active_source and (active_source.get("path") != plan.costs.source_path or active_source.get("sha256") != plan.costs.payload_sha256):
                return AdmissionDecision("rejected", False, None, ("source_mismatch",), state["revision"])
            if campaign.admission.requires_schedule and _utc(self.utc_now()) >= _utc(campaign.scheduled_start):
                return AdmissionDecision("missed", False, None, ("slot_missed",), state["revision"])
            if state.get("cleanup_blocked"):
                return AdmissionDecision("rejected", False, None, ("cleanup_open",), state["revision"])
            current_spent = sum((_decimal(v.get("incurred_usd", "0")) for v in state["reservations"].values()), Decimal("0"))
            current_active = sum((max(_decimal(v["reserved_cost_usd"]) - _decimal(v.get("incurred_usd", "0")) - _decimal(v.get("released_usd", "0")), Decimal("0")) for v in state["reservations"].values()), Decimal("0"))
            unresolved_prior = bool(state.get("cleanup_blocked")) or any(
                v.get("cleanup_open") and v.get("session") != self.controller_session_id for v in state["reservations"].values())
            if (request.incurred_cost_usd.value is not None and _decimal(request.incurred_cost_usd.value) != current_spent
                or request.active_reserved_usd.value is not None and _decimal(request.active_reserved_usd.value) != current_active
                or request.outstanding_cleanup != unresolved_prior):
                return AdmissionDecision("rejected", False, None, ("source_mismatch",), state["revision"])
            namespaces = set(plan.namespaces.values())
            if any(namespaces & set(record.get("plan", {}).get("namespaces", {}).values()) for record in state["reservations"].values()):
                return AdmissionDecision("rejected", False, None, ("identity_mismatch",), state["revision"])
            replacement_of = None
            context = str(getattr(campaign.context, "value", campaign.context))
            funding_buckets=set(plan.funding)
            if request.priority in {"P0","P1"} and context != "replacement" and not funding_buckets <= {"primary","tax","uncertainty"}:
                return AdmissionDecision("rejected",False,None,("budget_exhausted",),state["revision"])
            if context == "replacement" and not funding_buckets <= {"replacements","tax","uncertainty"}:
                return AdmissionDecision("rejected",False,None,("budget_exhausted",),state["revision"])
            if request.priority in {"P2","P3"} and funding_buckets != {"optional"}:
                return AdmissionDecision("rejected",False,None,("optional_review_required",),state["revision"])
            if context == "replacement":
                failure = plan.original_failure or {}
                replaced_run = campaign.replacement_context.replaces_run_id if campaign.replacement_context else None
                provider = replaced_run.rsplit("-", 1)[-1] if replaced_run else ""
                replacement_of = failure.get("original_reservation_id")
                original = state["reservations"].get(replacement_of)
                failed_attempt = original.get("failures_by_provider", {}).get(provider, {}) if original else {}
                failure_event = state.get("events", {}).get(failure.get("failure_event_id"), {})
                failure_payload = failure_event.get("payload", {})
                if ExperimentMode(campaign.mode) != ExperimentMode.SHORT_STUDY or not original or original.get("context") == "replacement":
                    return AdmissionDecision("rejected", False, None, ("identity_mismatch",), state["revision"])
                if (not failed_attempt or failed_attempt.get("run_id") != replaced_run or failure.get("failed_run_id") != replaced_run
                    or failure.get("failed_provider") != provider
                    or not failure_event or failure_payload.get("reservation_id") != replacement_of
                    or failure_payload.get("provider") != provider
                    or failure_payload.get("source_sha256") != failure.get("failure_source_sha256")
                    or failure_payload.get("failure") != failed_attempt
                    or not campaign.replacement_context
                    or failed_attempt.get("observation_id") != campaign.replacement_context.observation_id
                    or failure.get("observation_id") != campaign.replacement_context.observation_id
                    or (plan.observation_id is not None and plan.observation_id != campaign.replacement_context.observation_id)):
                    return AdmissionDecision("rejected", False, None, ("prerequisite_pending",), state["revision"])
                if original.get("request", {}).get("mode") != ExperimentMode.SHORT_STUDY.value or original.get("request", {}).get("priority") not in {"P0", "P1"}:
                    return AdmissionDecision("rejected", False, None, ("identity_mismatch",), state["revision"])
                if original.get("cleanup_open") or replacement_of in state.get("cleanup_blocked", []):
                    return AdmissionDecision("rejected", False, None, ("cleanup_open",), state["revision"])
                if (selected != {provider} or not original or original.get("child_identities", {}).get(provider) != replaced_run
                    or plan.child_identities.get(provider) == replaced_run or original.get("replacement_claimed")):
                    return AdmissionDecision("rejected", False, None, ("duplicate_conflict",), state["revision"])
                if state.get("replacement_counts", {}).get(provider, 0) >= 3:
                    return AdmissionDecision("rejected", False, None, ("optional_review_required",), state["revision"])
            if any(v.get("cleanup_open") and v.get("session") != self.controller_session_id for v in state["reservations"].values()):
                return AdmissionDecision("rejected", False, None, ("controller_session_changed",), state["revision"])
            price = envelope_total
            if sum(support_components, Decimal("0")) > price:
                return AdmissionDecision("rejected", False, None, ("support_cost_missing",), state["revision"])
            if price <= 0 or not plan.funding:
                return AdmissionDecision("rejected", False, None, ("support_cost_missing",), state["revision"])
            if request.estimated_cost_usd.value is not None and _decimal(request.estimated_cost_usd.value).quantize(Decimal("0.0001"), rounding=ROUND_CEILING) != price:
                return AdmissionDecision("rejected", False, None, ("source_mismatch",), state["revision"])
            active = sum((max(_decimal(v["reserved_cost_usd"]) - _decimal(v.get("incurred_usd", "0")) - _decimal(v.get("released_usd", "0")), Decimal("0")) for v in state["reservations"].values()), Decimal("0"))
            spent = sum((_decimal(v.get("incurred_usd", "0")) for v in state["reservations"].values()), Decimal("0"))
            remaining = sum((_decimal(v) for v in state["funding_remaining"].values()), Decimal("0"))
            commitment = active + spent + remaining
            if commitment > Decimal("250") or commitment + Decimal("50") > Decimal("300"):
                return AdmissionDecision("rejected", False, None, ("budget_exhausted",), state["revision"])
            now = _utc(self.utc_now())
            deadline = min([now + timedelta(seconds=request.lifetime_cap_seconds)] + [now + timedelta(seconds=request.lifetime_cap_seconds - float(v)) for v in plan.allowance.provider_leads_seconds.values()])
            workflow_seconds = plan.allowance.fit_seconds(now=now, scheduled_start=campaign.scheduled_start)
            if workflow_seconds > request.lifetime_cap_seconds or workflow_seconds > (deadline-now).total_seconds():
                return AdmissionDecision("rejected", False, None, ("workflow_time_insufficient",), state["revision"])
            rid = hashlib.sha256(f"{self.scope_id}:{key}:{fingerprint}".encode()).hexdigest()[:32]
            evidence = ReservationEvidence(schema_version=2, campaign_id=request.campaign_id,
                reservation_id=Evidence[str](value=rid), state="reserved", reserved_cost_usd=Evidence[float](value=float(price)),
                cap_started_at=Evidence[datetime](value=now), expires_at=Evidence[datetime](value=now+timedelta(seconds=request.lifetime_cap_seconds)),
                cap_start_policy_id=StringEvidence(value="reservation-activation-before-provider-expiry"), source_path=request.source_path,
                source_sha256=request.source_sha256, reasons=[])
            rec = {"reservation_id": rid, "evidence": evidence.model_dump(mode="json"), "reserved_cost_usd": str(price), "incurred_usd": "0", "session": self.controller_session_id,
                   "cap_start_monotonic": _duration(self.monotonic_now(), "monotonic activation"), "cap_seconds": request.lifetime_cap_seconds,
                   "provider_trigger_monotonic": _duration(self.monotonic_now(), "monotonic activation") + min(request.lifetime_cap_seconds-float(v) for v in plan.allowance.provider_leads_seconds.values()),
                   "scheduled_start_monotonic": _duration(self.monotonic_now(), "monotonic activation") + max(0.0, (_utc(campaign.scheduled_start)-now).total_seconds()) if campaign.scheduled_start else None,
                   "allowance": workflow_seconds,
                   "funding_moves": {k: str(v) for k, v in plan.funding.items()},
                   "allowance_fields": {"registration_seconds":plan.allowance.registration_seconds,"provision_seconds":plan.allowance.provision_seconds,
                   "readiness_seconds":plan.allowance.readiness_seconds,"stabilization_seconds":plan.allowance.stabilization_seconds,
                   "measurement_seconds":plan.allowance.measurement_seconds,"measurement_shutdown_seconds":plan.allowance.measurement_shutdown_seconds,
                   "retrieval_seconds":plan.allowance.retrieval_seconds,"cleanup_seconds":plan.allowance.cleanup_seconds}, "cleanup_open": False,
            "request": request.model_dump(mode="json"), "provider_leads":dict(plan.allowance.provider_leads_seconds),
                   "provider_cleanup":{}, "provider_billing":{name:{"actual_usd":None,"estimate_usd":None,"final":False,"event_id":None} for name in selected},
                   "child_identities":dict(plan.child_identities), "plan": {"fingerprint": fingerprint,
                   "providers": [Provider(p).value for p in request.selected_providers], "support": plan.provider_support,
                   "cost_envelopes":dict(plan.cost_envelopes),"component_buckets":{k:str(v) for k,v in envelope_buckets.items()},
                   "namespaces":dict(plan.namespaces), "scheduled_start": campaign.scheduled_start.isoformat() if campaign.scheduled_start else None,
                   "effective_deadline": deadline.isoformat()}}
            # Activation precedes provider expiry registration; until sourced cleanup
            # proves absence, a controller restart must conservatively block launches.
            rec["cleanup_open"] = True
            rec["provider_cleanup"] = {name: False for name in selected}
            state["cleanup_open"].append(rid)
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
        stage_prefixes = {"registration": (), "preparation": ("registration",),
            "measurement": ("registration", "preparation"),
            "retrieval": ("registration", "preparation", "measurement"),
            "cleanup": ("registration", "preparation", "measurement", "retrieval")}
        expected_completed = stage_prefixes[stage]
        if tuple(remaining.completed_stages) != expected_completed:
            reasons.append("workflow_time_insufficient")
        completed = {"preparation": ("registration_seconds",),
            "measurement": ("registration_seconds","provision_seconds","readiness_seconds","stabilization_seconds"),
            "retrieval": ("registration_seconds","provision_seconds","readiness_seconds","stabilization_seconds","measurement_seconds","measurement_shutdown_seconds"),
            "cleanup": ("registration_seconds","provision_seconds","readiness_seconds","stabilization_seconds","measurement_seconds","measurement_shutdown_seconds","retrieval_seconds")}.get(stage, ())
        if any(remain_fields[name] != 0 for name in completed): reasons.append("workflow_time_insufficient")
        current = {"registration": {"registration_seconds"},
            "preparation": {"provision_seconds", "readiness_seconds", "stabilization_seconds"},
            "measurement": {"measurement_seconds", "measurement_shutdown_seconds"},
            "retrieval": {"retrieval_seconds"}, "cleanup": {"cleanup_seconds"}}[stage]
        future_by_stage = {"registration": {"provision_seconds", "readiness_seconds", "stabilization_seconds", "measurement_seconds", "measurement_shutdown_seconds", "retrieval_seconds", "cleanup_seconds"},
            "preparation": {"measurement_seconds", "measurement_shutdown_seconds", "retrieval_seconds", "cleanup_seconds"},
            "measurement": {"retrieval_seconds", "cleanup_seconds"}, "retrieval": {"cleanup_seconds"}, "cleanup": set()}[stage]
        if any(remain_fields[name] != originals[name] for name in future_by_stage):
            reasons.append("workflow_time_insufficient")
        if any(remain_fields[name] <= 0 for name in current):
            reasons.append("workflow_time_insufficient")
        if stage == "cleanup" and remaining.cleanup_seconds <= 0: reasons.append("workflow_time_insufficient")
        evidence = ReservationEvidence.model_validate(rec["evidence"])
        deadline = datetime.fromisoformat(rec["plan"].get("effective_deadline", evidence.expires_at.value.isoformat()))
        if rec.get("provider_deadline"):
            deadline = min(deadline, datetime.fromisoformat(rec["provider_deadline"]))
        if rec["session"] != self.controller_session_id: reasons.append("controller_session_changed")
        now = _utc(self.utc_now())
        monotonic_now = _duration(self.monotonic_now(), "monotonic time")
        elapsed = monotonic_now - rec["cap_start_monotonic"]
        live = min(rec.get("cap_seconds", rec["allowance"]), rec.get("provider_trigger_monotonic", rec["cap_start_monotonic"]+rec["allowance"]) - rec["cap_start_monotonic"]) - elapsed
        if elapsed < 0:
            reasons.append("controller_session_changed")
        minimum_measurement = minimum_protocol_seconds(ExperimentMode(rec["request"]["mode"]))
        if stage in {"registration", "preparation", "measurement"} and remaining.measurement_seconds < minimum_measurement + remaining.measurement_shutdown_seconds:
            reasons.append("workflow_time_insufficient")
        schedule_start = datetime.fromisoformat(rec["plan"].get("scheduled_start")) if rec["plan"].get("scheduled_start") else None
        wait_remaining = max(0.0, rec["scheduled_start_monotonic"] - monotonic_now) if rec.get("scheduled_start_monotonic") is not None else 0.0
        remaining_total = remaining.fit_seconds(now=now, preparation_can_overlap=stage in {"registration", "preparation", "measurement"})
        if stage in {"registration", "preparation", "measurement"}:
            remaining_total += max(0.0, wait_remaining - remaining.preparation_seconds)
        if live <= 0 or remaining_total > live: reasons.append("workflow_time_insufficient")
        return StageDecision(not reasons, tuple(reasons), deadline)

    def claim_creation(self, reservation_id: str, *, provider: Provider, expiry_requests: Sequence[ExpiryRequest], expiry_evidence: Sequence[ExpiryEvidence]) -> StageDecision:
        fd = self._lock()
        try:
            state = self._read(); rec = state["reservations"].get(reservation_id)
            if not rec: return StageDecision(False,("ledger_unavailable",),None)
            evidence_now = ReservationEvidence.model_validate(rec["evidence"])
            if evidence_now.state != "reserved" or rec.get("terminal_released"):
                return StageDecision(False,("reservation_terminal",),evidence_now.expires_at.value)
            if Provider(provider).value not in rec["plan"]["providers"]:
                return StageDecision(False,("identity_mismatch",),evidence_now.expires_at.value)
            if rec["session"] != self.controller_session_id:
                return StageDecision(False,("controller_session_changed",),evidence_now.expires_at.value)
            fields = rec["allowance_fields"]
            remaining = WorkflowAllowance(0, fields["provision_seconds"], fields["readiness_seconds"],
                fields["stabilization_seconds"], fields["measurement_seconds"], fields["measurement_shutdown_seconds"],
                fields["retrieval_seconds"], fields["cleanup_seconds"], "durable-reservation", rec["provider_leads"], ("registration",))
            minimum = minimum_protocol_seconds(ExperimentMode(rec["request"]["mode"]))
            if remaining.measurement_seconds < minimum + remaining.measurement_shutdown_seconds:
                return StageDecision(False,("workflow_time_insufficient",),evidence_now.expires_at.value)
            monotonic_now = _duration(self.monotonic_now(), "monotonic time")
            elapsed = monotonic_now - rec["cap_start_monotonic"]
            live = min(rec.get("cap_seconds",rec["allowance"]), rec.get("provider_trigger_monotonic",rec["cap_start_monotonic"]+rec["allowance"])-rec["cap_start_monotonic"]) - elapsed
            wait = max(0.0, rec["scheduled_start_monotonic"]-monotonic_now) if rec.get("scheduled_start_monotonic") is not None else 0.0
            required_fit = remaining.fit_seconds(now=_utc(self.utc_now()), preparation_can_overlap=True) + max(0.0, wait-remaining.preparation_seconds)
            if live <= 0 or required_fit > live:
                return StageDecision(False,("workflow_time_insufficient",),evidence_now.expires_at.value)
            fit = StageDecision(True,(),evidence_now.expires_at.value)
            if state.get("cleanup_blocked"):
                return StageDecision(False,("cleanup_open",),fit.effective_deadline)
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
                cap_start = ReservationEvidence.model_validate(rec["evidence"]).cap_started_at.value
                if (ev.registered_at.value is None or _utc(ev.registered_at.value) < _utc(cap_start)
                    or _utc(ev.registered_at.value) > _utc(self.utc_now()) or req.expires_at.value is None
                    or _utc(req.expires_at.value) <= _utc(ev.registered_at.value)):
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
        if (not event.event_id.strip() or not event.reservation_id.strip() or not isinstance(event.actual, bool)
            or not isinstance(event.billing_final, bool) or not isinstance(event.released_funding_by_bucket, Mapping)):
            raise ValueError("reconciliation identity/status is invalid")
        if event.occurred_at is None: raise ValueError("reconciliation timestamp is required")
        occurred_at = _utc(event.occurred_at)
        _hash(event.source_sha256,"source_sha256")
        if not event.source_path or not event.source_path.strip():
            raise ValueError("reconciliation source path is required")
        if event.cleanup_verified_absent and (not event.absence_evidence_id or not event.absence_evidence_id.strip()
            or not event.absent_resource_ids or len(set(event.absent_resource_ids)) != len(event.absent_resource_ids)
            or any(not item.strip() for item in event.absent_resource_ids)):
            raise ValueError("complete cleanup requires sourced absence evidence for every billable resource")
        if event.incurred_usd is not None: _decimal(event.incurred_usd)
        release_total = sum((_decimal(v, "released funding") for v in event.released_funding_by_bucket.values()), Decimal("0"))
        if release_total and (not event.billing_final or not event.actual or event.incurred_usd is None
            or not event.release_evidence_id or not event.release_source_path or not event.release_source_path.strip()):
            raise ValueError("unused funding release requires final sourced billing and cleanup evidence")
        if release_total and (not event.release_evidence_id.strip() or not event.release_source_path.strip()):
            raise ValueError("unused funding release evidence must be nonblank")
        fd=self._lock()
        try:
            state=self._read(); payload={"reservation_id":event.reservation_id,"provider":Provider(event.provider).value,"source_sha256":event.source_sha256,
                "incurred_usd":None if event.incurred_usd is None else str(event.incurred_usd),"actual":event.actual,
                "cleanup_verified_absent":event.cleanup_verified_absent,"billable_resources_present":event.billable_resources_present,"failure":event.failure,
                "occurred_at":occurred_at.isoformat(),"billing_final":event.billing_final,
                "released_funding_by_bucket":{k:str(v) for k,v in sorted(event.released_funding_by_bucket.items())},
                "release_evidence_id":event.release_evidence_id,"release_source_path":event.release_source_path,
                "source_path":event.source_path,"absence_evidence_id":event.absence_evidence_id,
                "absent_resource_ids":list(event.absent_resource_ids)}
            digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
            old=state["events"].get(event.event_id)
            if old:
                if old["digest"]!=digest: raise ValueError("duplicate_conflict")
                return ReservationEvidence.model_validate(state["reservations"][event.reservation_id]["evidence"])
            rec=state["reservations"].get(event.reservation_id)
            if rec is None: raise ValueError("identity_mismatch")
            provider = Provider(event.provider).value
            if provider not in rec["plan"]["providers"]: raise ValueError("identity_mismatch")
            bill = rec.setdefault("provider_billing", {name:{"actual_usd":None,"estimate_usd":None,"final":False,"event_id":None} for name in rec["plan"]["providers"]})[provider]
            if event.billing_final and (not event.actual or event.incurred_usd is None):
                raise ValueError("final provider billing requires an actual sourced charge")
            if bill.get("final") and event.billing_final and (bill.get("actual_usd") != str(_decimal(event.incurred_usd)) or bill.get("event_id") != event.event_id):
                raise ValueError("conflicting_final_billing")
            if event.incurred_usd is not None:
                bill["actual_usd" if event.actual else "estimate_usd"] = str(_decimal(event.incurred_usd))
            if event.billing_final:
                bill["final"] = True
                bill["event_id"] = event.event_id
            rec["incurred_usd"] = str(sum((_decimal(v["actual_usd"]) for v in rec["provider_billing"].values() if v.get("final") and v.get("actual_usd") is not None), Decimal("0")))
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
                rec["cleanup_open"] = True
                if event.reservation_id not in state["cleanup_blocked"]: state["cleanup_blocked"].append(event.reservation_id)
            if event.cleanup_verified_absent and event.billable_resources_present is False:
                rec["provider_cleanup"][provider] = True
                if all(rec["provider_cleanup"].get(name) is True for name in rec["plan"]["providers"]):
                    rec["cleanup_open"]=False
                    if event.reservation_id in state["cleanup_open"]: state["cleanup_open"].remove(event.reservation_id)
                    if event.reservation_id in state["cleanup_blocked"]: state["cleanup_blocked"].remove(event.reservation_id)
                elif event.reservation_id not in state["cleanup_blocked"]:
                    state["cleanup_blocked"].append(event.reservation_id)
            elif event.billable_resources_present is True:
                rec["provider_cleanup"][provider] = False
                rec["cleanup_open"] = True
                if event.reservation_id not in state["cleanup_open"]: state["cleanup_open"].append(event.reservation_id)
                if event.reservation_id not in state["cleanup_blocked"]: state["cleanup_blocked"].append(event.reservation_id)
            elif event.billable_resources_present is None:
                rec["cleanup_open"] = True
                if event.reservation_id not in state["cleanup_open"]: state["cleanup_open"].append(event.reservation_id)
                if event.reservation_id not in state["cleanup_blocked"]: state["cleanup_blocked"].append(event.reservation_id)
            if release_total:
                if not (event.cleanup_verified_absent and event.billable_resources_present is False
                    and all(rec["provider_cleanup"].get(name) is True for name in rec["plan"]["providers"])
                    and all(rec["provider_billing"].get(name,{}).get("final") for name in rec["plan"]["providers"])):
                    raise ValueError("unused funding requires final billing and all-provider cleanup")
                available = max(_decimal(rec["reserved_cost_usd"]) - _decimal(rec.get("incurred_usd", "0")) - _decimal(rec.get("released_usd", "0")), Decimal("0"))
                if release_total > available:
                    raise ValueError("released funding exceeds the proved unused reservation ceiling")
                for bucket, amount in event.released_funding_by_bucket.items():
                    if bucket not in rec.get("funding_moves", {}) or _decimal(amount) > _decimal(rec["funding_moves"][bucket]) - _decimal(rec.setdefault("released_by_bucket", {}).get(bucket, "0")):
                        raise ValueError("released funding does not match the original bucket allocation")
                    state["funding_remaining"][bucket] = str(_decimal(state["funding_remaining"].get(bucket, "0")) + _decimal(amount))
                    rec["released_by_bucket"][bucket] = str(_decimal(rec["released_by_bucket"].get(bucket, "0")) + _decimal(amount))
                rec["released_usd"] = str(_decimal(rec.get("released_usd", "0")) + release_total)
                rec["terminal_released"] = True
                ev = ReservationEvidence.model_validate(rec["evidence"])
                ev = ev.model_copy(update={"state":"released", "reasons":["all_provider_final_billing_and_cleanup_verified"]})
                rec["evidence"] = ev.model_dump(mode="json")
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
    for entry in entries:
        campaign=campaigns[entry.entry_id]
        blockers=pending.setdefault(entry.entry_id, [])
        if campaign.admission.requires_primary_review and "primary_review_pending" not in blockers:
            blockers.append("primary_review_pending")
        if campaign.admission.cost_input_sha256.value is None or campaign.admission.estimated_cost_usd.value is None:
            blockers.append("config_cost_unavailable_resolved_by_f11_sidecar")
        blockers.extend(f"launch_blocker:{item}" for item in campaign.launch_blockers if f"launch_blocker:{item}" not in blockers)
        pending[entry.entry_id]=sorted(set(blockers))
    reasons={reason for values in pending.values() for reason in values} | {"prerequisite_pending"}
    if costs.refresh_required:
        reasons.add("price_refresh_required")
    invalid_reasons = [*(f"schedule_error:{item}" for item in inspection.errors),
        *(f"namespace_collision:{item}" for item in inspection.namespace_collisions)]
    return {"status":"invalid" if invalid_reasons else "inspectable","paid_eligible":False,"entries":[e.model_dump(mode="json") for e in entries],"pending":pending,
            "cost_input":{"id":costs.cost_input_id,"sha256":costs.payload_sha256,"prices_checked_on":costs.prices_checked_on,"refresh_required":costs.refresh_required},
            "schedule":{"complete_primary_week":inspection.complete_primary_week,"errors":list(inspection.errors),"missed_entries":list(inspection.missed_entries),
                        "simultaneous_vm_demand":dict(inspection.simultaneous_vm_demand),
                        "execution_identities":{key:{"campaign_id":value.campaign_id,"children":dict(value.child_identities),
                            "resources":dict(value.resource_namespaces),"state":dict(value.state_namespaces),"results":dict(value.result_namespaces)}
                            for key,value in inspection.identities_by_entry.items()},
                        "namespace_collisions":list(inspection.namespace_collisions)},
            "reason_codes":sorted(reasons | set(invalid_reasons)),"external_calls":0}


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
        return 2 if report["status"] == "invalid" else 0
    except Exception as exc:
        print(json.dumps({"status":"invalid","paid_eligible":False,"reason_codes":["invalid_schedule"],"error":str(exc)},sort_keys=True),file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
