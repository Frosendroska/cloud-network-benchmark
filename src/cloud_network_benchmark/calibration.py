"""Evidence-based common stream-count recommendation for F08 calibration."""
from __future__ import annotations

import hashlib
import json
import statistics
from dataclasses import dataclass
from typing import Any, Sequence

from .contracts.common import DataValidity, Evidence, ExperimentMode
from .contracts.validation import ValidationResult


@dataclass(frozen=True)
class CalibrationRecommendation:
    common_n: Evidence[int]
    plateau_found: Evidence[bool]
    provider_medians_gbps: dict[str, dict[int, float]]
    qualifying_ratios: dict[int, dict[str, float]]
    source_campaign_id: Evidence[str]
    source_run_ids: tuple[str, ...]
    source_sha256: Evidence[str]
    unavailable_reason: str | None = None

    def common_n_state_fields(self) -> dict[str, Any]:
        return {"state": "frozen" if self.common_n.value is not None else "pending",
                "common_n": self.common_n.value,
                "source_campaign_id": self.source_campaign_id.value if self.common_n.value is not None else None,
                "source_sha256": self.source_sha256.value if self.common_n.value is not None else None,
                "plateau_found": self.plateau_found.value if self.common_n.value is not None else None}


_ORDERS = {
    "aws": (1, 4, 2, 8, 8, 2, 4, 1, 2, 8, 1, 4),
    "azure": (8, 2, 4, 1, 1, 4, 2, 8, 4, 1, 8, 2),
    "gcp": (2, 8, 1, 4, 4, 1, 8, 2, 1, 4, 2, 8),
}


def _unavailable(results: Sequence[ValidationResult], reason: str) -> CalibrationRecommendation:
    return CalibrationRecommendation(
        Evidence[int](unavailable_reason=reason), Evidence[bool](unavailable_reason=reason), {}, {},
        Evidence[str](unavailable_reason="one shared source campaign is required"),
        tuple(sorted(result.run_id for result in results)), Evidence[str](unavailable_reason="complete calibration evidence is required"), reason)


def select_common_n(results: Sequence[ValidationResult]) -> CalibrationRecommendation:
    if len(results) != 3:
        return _unavailable(results, "exactly one calibration result per provider is required")
    providers: dict[str, ValidationResult] = {}
    campaigns: set[str] = set()
    deployments: set[str] = set()
    all_trials: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        if result.mode != ExperimentMode.CALIBRATION or result.validation_status.data_validity != DataValidity.VALID:
            return _unavailable(results, "each provider must have valid calibration data")
        payload = result.metrics.value
        identity = payload.get("identity", {}) if payload else {}
        provider = identity.get("provider")
        campaign = identity.get("campaign_id")
        deployment = identity.get("deployment_id")
        if provider not in _ORDERS or provider in providers or not campaign or not deployment or deployment in deployments:
            return _unavailable(results, "provider, campaign or retained deployment identity is missing, duplicate or inconsistent")
        providers[provider] = result
        campaigns.add(campaign)
        deployments.add(deployment)
        units = payload.get("units", [])
        if len(units) != 12:
            return _unavailable(results, f"{provider} must provide twelve calibration trials")
        unit_by_index: dict[int, dict[str, Any]] = {}
        for unit in units:
            index = unit.get("trial_index")
            if isinstance(index, bool) or not isinstance(index, int) or index in unit_by_index:
                return _unavailable(results, f"{provider} has missing or duplicate trial indices")
            unit_by_index[index] = unit
        if tuple(unit_by_index.get(i, {}).get("streams") for i in range(1, 13)) != _ORDERS[provider]:
            return _unavailable(results, f"{provider} trial order does not match the retained calibration order")
        all_trials[provider] = [unit_by_index[i] for i in range(1, 13)]
    if len(campaigns) != 1:
        return _unavailable(results, "calibration providers must share one source campaign")
    medians: dict[str, dict[int, float]] = {}
    evidence_rows: list[dict[str, Any]] = []
    for provider in sorted(all_trials):
        values: dict[int, list[float]] = {n: [] for n in (1, 2, 4, 8)}
        for unit in all_trials[provider]:
            ev = unit.get("tcp", {}).get("throughput_gbps", {})
            value = ev.get("value") if isinstance(ev, dict) else None
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                return _unavailable(results, f"{provider} has missing, invalid or all-zero receiver throughput evidence")
            sources = unit.get("sources", [])
            source_rows = sorted(
                ({"artifact_id": source.get("artifact_id"), "path": source.get("path"), "sha256": source.get("sha256")}
                 for source in sources if isinstance(source, dict)),
                key=lambda source: (str(source["artifact_id"]), str(source["path"]), str(source["sha256"])),
            )
            if not source_rows or any(
                not isinstance(source.get("sha256"), str)
                or len(source["sha256"]) != 64
                or any(char not in "0123456789abcdef" for char in source["sha256"])
                for source in source_rows
            ):
                return _unavailable(results, f"{provider} trial {unit.get('trial_index')} lacks source artifact SHA-256 evidence")
            values[unit["streams"]].append(float(value))
            evidence_rows.append({"provider": provider, "run_id": providers[provider].run_id,
                                  "unit_id": unit.get("unit_id"), "trial_index": unit.get("trial_index"),
                                  "streams": unit.get("streams"), "throughput_gbps": value,
                                  "sources": source_rows})
        if any(len(values[n]) != 3 for n in values):
            return _unavailable(results, f"{provider} must provide three valid trials per stream count")
        medians[provider] = {n: statistics.median(values[n]) for n in values}
    best = {provider: max(counts.values()) for provider, counts in medians.items()}
    ratios = {n: {provider: medians[provider][n] / best[provider] for provider in sorted(medians)} for n in (2, 4, 8)}
    selected = next((n for n in (2, 4, 8) if all(ratios[n][provider] >= .95 for provider in medians)), None)
    plateau = selected is not None
    if selected is None:
        selected = 8
    canonical = json.dumps({"campaign_id": next(iter(campaigns)), "evidence": evidence_rows}, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return CalibrationRecommendation(
        Evidence[int](value=selected), Evidence[bool](value=plateau), medians, ratios,
        Evidence[str](value=next(iter(campaigns))), tuple(sorted(result.run_id for result in results)),
        Evidence[str](value=hashlib.sha256(canonical.encode()).hexdigest()), None)
