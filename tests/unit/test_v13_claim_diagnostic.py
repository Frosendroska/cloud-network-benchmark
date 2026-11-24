from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "diagnose_v13_f11_claim.py"
SPEC = importlib.util.spec_from_file_location("v13_claim_diagnostic", SCRIPT)
DIAGNOSTIC = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(DIAGNOSTIC)


def _contract(provider: str) -> dict:
    cap_end = datetime(2026, 11, 24, 16, 52, 35, 97911, tzinfo=timezone.utc)
    lead = {"aws": 360, "azure": 120, "gcp": 30}[provider]
    run_id = f"child-{provider}"
    deployment_id = f"{run_id}-deployment"
    namespace = f"namespace-{provider}"
    target = f"target-{provider}"
    return {
        "schema_version": 1,
        "provider": provider,
        "deployment": {"deployment_id": deployment_id},
        "expiry_request": {
            "provider": provider, "run_id": run_id, "deployment_id": deployment_id,
            "action": {"aws": "stop", "azure": "deallocate", "gcp": "stop"}[provider],
            "reservation_id": {"value": "reservation-1", "unavailable_reason": None},
            "resource_namespace": namespace,
            "target_selector": target,
            "expires_at": {"value": (cap_end-timedelta(seconds=lead)).isoformat(),
                "unavailable_reason": None},
        },
        "provider_lead_seconds": lead,
        "reservation": {"reservation_id": {"value": "reservation-1", "unavailable_reason": None}},
        "settings": {},
        "snapshot_sha256": "a" * 64,
    }


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_actual_two_json_cleanup_layout_selects_only_original_contract(tmp_path):
    # Mirrors the sealed V13 AWS/GCP layout: a deployment contract plus a
    # same-directory cleanup receipt. Both are JSON, only one is a request.
    for provider in ("aws", "gcp"):
        root = tmp_path / "V-S3" / "f09-cleanup" / provider
        root.mkdir(parents=True)
        contract = _contract(provider)
        source = root / f"child-{provider}-deployment.json"
        _write(source, contract)
        _write(root / f"reservation-1-{provider}-cleanup-deadbeef.json", {
            "schema_version": 1, "provider": provider, "reservation_id": "reservation-1",
            "deployment_id": contract["deployment"]["deployment_id"], "absence_proved": True,
        })

        selected_path, selected = DIAGNOSTIC.select_source_contract(root, provider)
        assert selected_path == source
        valid, mismatches = DIAGNOSTIC.validate_source_request(selected,
            provider=provider, child_id=f"child-{provider}", namespace=f"namespace-{provider}",
            reservation_id="reservation-1",
            cap_end=datetime(2026, 11, 24, 16, 52, 35, 97911, tzinfo=timezone.utc),
            lead=selected["provider_lead_seconds"])
        assert valid, mismatches


def test_source_contract_selection_fails_closed_for_foreign_or_ambiguous_contracts(tmp_path):
    root = tmp_path / "aws"
    root.mkdir()
    contract = _contract("aws")
    _write(root / "original.json", contract)
    foreign = _contract("gcp")
    _write(root / "foreign.json", foreign)
    try:
        DIAGNOSTIC.select_source_contract(root, "aws")
    except ValueError as exc:
        assert "found 2" in str(exc)
    else:
        raise AssertionError("duplicate/foreign candidate must fail closed")

    (root / "foreign.json").unlink()
    contract["provider"] = "gcp"
    _write(root / "foreign-only.json", contract)
    (root / "original.json").unlink()
    try:
        DIAGNOSTIC.select_source_contract(root, "aws")
    except ValueError as exc:
        assert "provider identity mismatch" in str(exc)
    else:
        raise AssertionError("foreign-only contract must fail closed")


def test_source_request_rejects_evidence_wrapper_mismatch(tmp_path):
    contract = _contract("aws")
    contract["expiry_request"]["reservation_id"]["value"] = "foreign-reservation"
    valid, mismatches = DIAGNOSTIC.validate_source_request(contract,
        provider="aws", child_id="child-aws", namespace="namespace-aws",
        reservation_id="reservation-1",
        cap_end=datetime(2026, 11, 24, 16, 52, 35, 97911, tzinfo=timezone.utc), lead=360)
    assert not valid
    assert "reservation" in mismatches
