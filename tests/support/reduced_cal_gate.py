"""Narrow source-bound paid-acceptance checks for reduced CAL qualification.

The caller supplies evidence and a campaign from the real preparation/runtime
loaders. This helper never constructs acceptance manifests, validation
descriptors, or passing results.
"""
from __future__ import annotations

import copy


def check_actual_calibration_acceptance_prerequisite(*, evidence: dict, campaign) -> dict:
    """Run the production conditional CAL gate and bounded fail-closed checks.

    ``evidence`` must be ``plan.prerequisites['paid_acceptance']`` from the
    production ``build_admission_request`` call for a prepared CAL campaign
    whose accepted campaigns/manifests/native outputs already exist.
    """
    from cloud_network_benchmark.admission import _validate_paid_acceptance_evidence

    _validate_paid_acceptance_evidence(evidence, campaign)
    rejected = {}
    cases = {
        "missing": {},
        "pending_incomplete": {"evidence_id": evidence.get("evidence_id", "pending"),
            "source_path": evidence.get("source_path"),
            "source_sha256": evidence.get("source_sha256"), "status": "pending"},
        "source_mismatch": {**evidence, "source_sha256": "0" * 64},
    }
    for name, candidate in cases.items():
        try:
            _validate_paid_acceptance_evidence(candidate, campaign)
        except (ValueError, OSError, KeyError, StopIteration):
            rejected[name] = True
        else:
            rejected[name] = False
    if not all(rejected.values()):
        failed = sorted(name for name, value in rejected.items() if not value)
        raise AssertionError("production CAL gate accepted invalid prerequisite cases: " + ", ".join(failed))

    mismatched_campaigns = copy.deepcopy(evidence)
    references = mismatched_campaigns.get("accepted_campaigns")
    if isinstance(references, list) and references:
        references[0]["sha256"] = "0" * 64
        try:
            _validate_paid_acceptance_evidence(mismatched_campaigns, campaign)
        except (ValueError, OSError, KeyError, StopIteration):
            rejected["accepted_manifest_source_mismatch"] = True
        else:
            rejected["accepted_manifest_source_mismatch"] = False
        if not rejected["accepted_manifest_source_mismatch"]:
            raise AssertionError("production CAL gate accepted a changed acceptance manifest binding")

    return {"accepted": True, "rejected_cases": rejected,
        "accepted_campaign_count": len(evidence.get("accepted_campaigns", [])),
        "campaign_mode": getattr(getattr(campaign, "mode", None), "value", campaign.mode)}
