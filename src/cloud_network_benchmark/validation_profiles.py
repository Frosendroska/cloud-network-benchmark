"""Finite, opt-in timing bounds for the all-cloud validation campaign.

These are conservative admission ceilings, not observed durations. They do
not require waiting for the cap when work finishes early.
"""
from __future__ import annotations

from copy import deepcopy


ACCEPTANCE_PROFILE = "all_cloud_acceptance_v1"
CALIBRATION_PROFILE = "all_cloud_calibration_v1"
FAILURE_PROFILE = "all_cloud_failure_acceptance_v1"
PROFILE_SPECS = {
    ACCEPTANCE_PROFILE: {
        "stage": "acceptance", "mode": "acceptance", "cap_seconds": 4200,
        "barrier_seconds": 60,
        "allowance": {
            "registration_seconds": 900, "provision_seconds": 900,
            "readiness_seconds": 600, "stabilization_seconds": 60,
            "measurement_seconds": 100, "measurement_shutdown_seconds": 15,
            "retrieval_seconds": 300, "cleanup_seconds": 660,
            "provider_leads_seconds": {"aws": 360, "azure": 120, "gcp": 30},
        },
        "provider_registration_seconds": 300,
    },
    CALIBRATION_PROFILE: {
        "stage": "calibration", "mode": "calibration", "cap_seconds": 5100,
        "barrier_seconds": 60,
        "allowance": {
            "registration_seconds": 900, "provision_seconds": 900,
            "readiness_seconds": 600, "stabilization_seconds": 60,
            "measurement_seconds": 1000, "measurement_shutdown_seconds": 15,
            "retrieval_seconds": 300, "cleanup_seconds": 660,
            "provider_leads_seconds": {"aws": 360, "azure": 120, "gcp": 30},
        },
        "provider_registration_seconds": 300,
    },
    FAILURE_PROFILE: {
        "stage": "acceptance", "mode": "acceptance", "cap_seconds": 3900,
        "barrier_seconds": 0,
        "allowance": {
            "registration_seconds": 300, "provision_seconds": 900,
            "readiness_seconds": 600, "stabilization_seconds": 60,
            "measurement_seconds": 100, "measurement_shutdown_seconds": 15,
            "retrieval_seconds": 300, "cleanup_seconds": 660,
            "provider_leads_seconds": {"aws": 720, "azure": 720, "gcp": 720},
        },
        "provider_registration_seconds": 300,
    },
}


def profile_spec(profile: str | None):
    return PROFILE_SPECS.get(profile) if profile is not None else None


def provider_lead_seconds(profile: str | None, provider: str, default: int) -> int:
    spec = profile_spec(profile)
    return int(spec["allowance"]["provider_leads_seconds"].get(provider, default)) if spec else default


def profile_allowance(profile: str, providers: set[str]) -> dict:
    spec = PROFILE_SPECS.get(profile)
    if spec is None:
        raise ValueError("unsupported workflow profile")
    if not providers or not providers <= {"aws", "azure", "gcp"}:
        raise ValueError("workflow profile provider set is invalid")
    row = deepcopy(spec["allowance"])
    row["provider_leads_seconds"] = {p: row["provider_leads_seconds"][p] for p in sorted(providers)}
    row["source_id"] = f"{profile}:fixed-profile-v1"
    row["completed_stages"] = []
    return row


def validate_profile_allowance(profile: str | None, allowance, providers: set[str]) -> None:
    if profile is None:
        return
    spec = PROFILE_SPECS.get(profile)
    if spec is None:
        raise ValueError("unsupported workflow profile")
    expected = profile_allowance(profile, providers)
    actual = {key: getattr(allowance, key, None) for key in expected if key != "completed_stages"}
    actual["provider_leads_seconds"] = dict(getattr(allowance, "provider_leads_seconds", {}))
    if actual != {key: value for key, value in expected.items() if key != "completed_stages"}:
        raise ValueError("workflow profile requires its complete fixed allowance vector")
    if (allowance.total_seconds + spec["barrier_seconds"]
            + max(allowance.provider_leads_seconds.values()) > spec["cap_seconds"]):
        raise ValueError("workflow profile allowance and provider expiry lead exceed its fixed cap")
