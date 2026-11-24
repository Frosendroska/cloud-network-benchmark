#!/usr/bin/env python3
"""Read-only V13 F11 claim invariant report for execution on Halle.

This never constructs a BudgetLedger, calls claim_creation, or writes files.
Run from the frozen source checkout with its normal PYTHONPATH and pass the
sealed evidence root plus the actual ledger path. The reported clock interval
is reconstructed from the last successful provider registration and the
unchanged 15:50 UTC start boundary; it is not a recorded claim timestamp.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cloud_network_benchmark.admission import WorkflowAllowance
from cloud_network_benchmark.financial_controls import FinancialError, revalidate_financial_record


RID = "340da4d5c0932a12185363f445fa1717"
CLAIM_LOWER = datetime.fromisoformat("2026-11-24T15:49:55.394949+00:00")
CLAIM_UPPER = datetime.fromisoformat("2026-11-24T15:50:00+00:00")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def stamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def select_source_contract(directory: Path, provider: str) -> tuple[Path, dict]:
    """Select exactly one recognizable saved request contract, failing closed."""
    candidates: list[tuple[Path, dict]] = []
    for path in sorted(directory.glob("*.json")):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"non-regular evidence entry: {path.name}")
        value = load(path)
        if not isinstance(value, dict):
            raise ValueError(f"evidence entry is not an object: {path.name}")
        # Cleanup receipts are JSON too, but lack these original-contract keys.
        if any(key in value for key in ("expiry_request", "snapshot_sha256", "settings")):
            candidates.append((path, value))
    if len(candidates) != 1:
        raise ValueError(f"expected one source contract, found {len(candidates)}")
    path, contract = candidates[0]
    required = {"schema_version", "provider", "deployment", "expiry_request",
        "provider_lead_seconds", "reservation", "settings", "snapshot_sha256"}
    if (set(contract) != required or contract.get("schema_version") != 1
            or contract.get("provider") != provider
            or not isinstance(contract.get("deployment"), dict)
            or not isinstance(contract.get("expiry_request"), dict)
            or not isinstance(contract.get("reservation"), dict)):
        raise ValueError("source contract shape or provider identity mismatch")
    return path, contract


def evidence_value(value: object) -> object:
    """Unwrap the exact evidence envelope used by saved F11 contracts."""
    if not isinstance(value, dict) or value.get("unavailable_reason") is not None:
        return None
    return value.get("value")


def validate_source_request(contract: dict, *, provider: str, child_id: str,
        namespace: str, reservation_id: str, cap_end: datetime, lead: float) -> tuple[bool, str]:
    req = contract["expiry_request"]
    deployment_id = contract["deployment"].get("deployment_id")
    expected_trigger = cap_end - timedelta(seconds=float(lead))
    checks = {
        "provider": contract.get("provider") == provider == req.get("provider"),
        "child": req.get("run_id") == child_id,
        "deployment": bool(deployment_id) and req.get("deployment_id") == deployment_id,
        "reservation": evidence_value(req.get("reservation_id")) == reservation_id
            and evidence_value(contract["reservation"].get("reservation_id")) == reservation_id,
        "namespace": req.get("resource_namespace") == namespace,
        "action": req.get("action") == {"aws": "stop", "azure": "deallocate", "gcp": "stop"}[provider],
        "target_selector": isinstance(req.get("target_selector"), str) and bool(req["target_selector"]),
        "provider_lead": float(contract.get("provider_lead_seconds", -1)) == float(lead),
        "trigger": evidence_value(req.get("expires_at")) is not None
            and stamp(evidence_value(req["expires_at"])) == expected_trigger,
    }
    return all(checks.values()), ",".join(key for key, ok in checks.items() if not ok)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True,
        help="original-failure-evidence-02, captured during original cleanup")
    args = parser.parse_args()
    ledger = load(args.ledger)
    row = ledger["reservations"][RID]
    if row.get("reservation_id") != RID:
        raise SystemExit("reservation identity mismatch")

    print("financial_revalidation: read-only against the current ledger snapshot")
    for when in (CLAIM_LOWER, CLAIM_UPPER):
        try:
            revalidate_financial_record(row, state=ledger, now=when,
                end=stamp(row["evidence"]["expires_at"]["value"]),
                quote_freshness_at=when)
        except FinancialError as exc:
            print(f"  at {when.isoformat()}: DENIED {exc.reason}")
        else:
            print(f"  at {when.isoformat()}: PASS")

    print("expiry_bindings: immutable request fields vs sealed registration readbacks")
    child_ids = row["child_identities"]
    leads = row["provider_leads"]
    cap_end = stamp(row["evidence"]["expires_at"]["value"])
    registration_times: list[datetime] = []
    for provider in sorted(row["plan"]["providers"]):
        try:
            _contract_path, contract = select_source_contract(
                args.evidence / "V-S3" / "f09-cleanup" / provider, provider)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"  {provider}: FAIL source contract selection: {type(exc).__name__}: {exc}")
            continue
        req = contract["expiry_request"]
        request_ok, request_mismatches = validate_source_request(contract,
            provider=provider, child_id=child_ids[provider],
            namespace=row["plan"]["namespaces"][provider], reservation_id=RID,
            cap_end=cap_end, lead=leads[provider])
        print(f"  {provider}: source_request_identity={'PASS' if request_ok else 'FAIL'}; "
              f"mismatches={request_mismatches or 'none'}; "
              f"action={req.get('action')}; target_selector_present={bool(req.get('target_selector'))}; "
              f"cap_lead_seconds={leads[provider]}")

        provider_dir = args.evidence / "V-S3" / provider
        if provider == "azure":
            paths = list(provider_dir.glob("*/**/recovery.json"))
            if len(paths) != 1:
                print("    saved_registration=UNKNOWN (recovery artifact ambiguous/missing)")
                continue
            reg = load(paths[0])["expiry"]
            meta = reg.get("metadata", {})
            readback = meta.get("schedule_readback", {})
            requested = stamp(req["expires_at"]["value"])
            floor = requested.replace(second=0, microsecond=0)
            checks = (reg.get("state") == "registered"
                and meta.get("mechanism") == "azure_automation_once_deallocate"
                and meta.get("reservation_id") == RID
                and meta.get("source_sha256") == row["evidence"]["source_sha256"]
                and meta.get("resource_group") == req["resource_namespace"]
                and meta.get("target_ids") == req["target_selector"].split(";")
                and meta.get("requested_trigger") == requested.isoformat()
                and meta.get("trigger") == floor.isoformat()
                and readback.get("timeZone") in {"UTC", "Etc/UTC"}
                and stamp(readback["startTime"]) == floor)
            print(f"    saved_registration_and_exact_minute_floor={'PASS' if checks else 'FAIL'}; "
                  f"requested={requested.isoformat()}; effective={floor.isoformat()}; "
                  f"requested_metadata_present={meta.get('requested_trigger') == requested.isoformat()}; "
                  f"metadata_source_match={meta.get('source_sha256') == row['evidence']['source_sha256']}")
        else:
            attempts = list(provider_dir.glob("*/**/attempt.json"))
            if len(attempts) != 1:
                print("    saved_registration=UNKNOWN (attempt artifact ambiguous/missing)")
                continue
            attempt = load(attempts[0])
            reg = attempt.get("expiry_evidence") if provider == "aws" else attempt.get("expiry")
            if not reg:
                print("    saved_registration=UNKNOWN (no evidence object)")
                continue
            reg_at = stamp(reg["registered_at"]["value"])
            registration_times.append(reg_at)
            checks = all(reg.get(key) == value for key, value in {
                "provider": provider, "run_id": child_ids[provider],
                "deployment_id": req["deployment_id"], "action": req["action"],
                "target_selector": req["target_selector"]}.items())
            checks = checks and stamp(reg["expires_at"]["value"]) == stamp(req["expires_at"]["value"])
            print(f"    saved_registration_identity_and_trigger={'PASS' if checks else 'FAIL'}; "
                  f"registered_at={reg_at.isoformat()}; saved_state_now={reg.get('state')} "
                  "(later cancellation state is expected after cleanup)")

    # Remaining fit uses only immutable reservation fields. Elapsed monotonic
    # time is reconstructed from UTC around the final recorded registration.
    fields = row["allowance_fields"]
    remaining = WorkflowAllowance(0, fields["provision_seconds"], fields["readiness_seconds"],
        fields["stabilization_seconds"], fields["measurement_seconds"],
        fields["measurement_shutdown_seconds"], fields["retrieval_seconds"],
        fields["cleanup_seconds"], "V13-read-only-reconstruction", leads, ("registration",))
    anchor = row["cap_start_monotonic"]
    base = stamp(row["evidence"]["cap_started_at"]["value"])
    deadline = min(cap_end, stamp(row["plan"]["effective_deadline"]))
    elapsed = [(t - base).total_seconds() for t in (CLAIM_LOWER, CLAIM_UPPER)]
    live = [min(row["cap_seconds"], row.get("provider_trigger_monotonic", anchor + row["cap_seconds"]) - anchor) - e
            for e in elapsed]
    required = remaining.fit_seconds(now=CLAIM_UPPER, preparation_can_overlap=True)
    print("remaining_fit: reconstructed UTC-to-monotonic interval; not a recorded claim event")
    print(f"  elapsed_seconds={elapsed[0]:.3f}..{elapsed[1]:.3f}; live_seconds={min(live):.3f}..{max(live):.3f}; "
          f"required_seconds={required:.3f}; fit={'PASS' if min(live) >= required else 'FAIL'}; "
          f"effective_deadline={deadline.isoformat()}")
    print("limits: ledger snapshot was copied during cleanup; terminal/cleanup flags are post-claim. "
          "This script does not reset them or call claim_creation. Expiry registrations are readbacks "
          "preserved in original execution artifacts; later cancellation does not prove a claim result.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
