from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import fcntl

import pytest
import os

from cloud_network_benchmark.admission import (
    BudgetLedger, Reconciliation, UnlaunchedCancellationEvidence, load_cost_inputs,
)
from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.contracts.admission import ReservationEvidence
from cloud_network_benchmark.contracts.common import Evidence, Provider
from cloud_network_benchmark.admission import AdmissionPlan, ReservationRequest, WorkflowAllowance
from tests.fixtures.f11.envelope_factory import make_synthetic_envelopes
from tests.integration.test_f11_ledger import ROOT
from cloud_network_benchmark.financial_controls import check_shared_finances
from cloud_network_benchmark.financial_controls import FinancialError


RID = "609687d8ec5928cde1915b7d2fc2cc49"
ATTEMPT = "20261123T151109Z-exp-903-c7346234"
STAGE = "acceptance-20261123T151109Z-52ef4ee7"


def _ledger(path: Path) -> BudgetLedger:
    return BudgetLedger(path, scope_id="offline-scope",
        utc_now=lambda: datetime(2026, 11, 23, tzinfo=timezone.utc),
        monotonic_now=lambda: 1.0, controller_session_id="session-1")


def _hash_write(path: Path, payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True).encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _reservation(book: BudgetLedger) -> None:
    costs = load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json")
    campaign, _ = resolve_campaign(ROOT / "configs/tests/exp-903-three-provider-smoke.yaml", ROOT)
    providers = [Provider.AWS, Provider.AZURE, Provider.GCP]
    children = {p.value: f"{ATTEMPT}-{p.value}" for p in providers}
    funding = {"primary": Decimal("0.5")}
    allowance = WorkflowAllowance(10, 20, 10, 60, 76, 1, 10, 10,
        "synthetic-test-policy", {p.value: 60 for p in providers})
    request = ReservationRequest(schema_version=2, campaign_id=ATTEMPT,
        config_id=campaign.config_id,
        schedule_entry_id=Evidence[str](unavailable_reason="acceptance has no scheduled row"),
        selected_providers=providers, mode=campaign.mode, priority=campaign.admission.priority,
        lifetime_cap_seconds=1200, estimated_cost_usd=Evidence[float](value=.5),
        source_path=costs.source_path, source_sha256=costs.payload_sha256,
        incurred_cost_usd=Evidence[float](value=0), active_reserved_usd=Evidence[float](value=0),
        outstanding_cleanup=False)
    plan = AdmissionPlan(campaign, children,
        {p.value: f"{ATTEMPT}-{p.value}-ns" for p in providers}, costs, allowance,
        funding=funding, cost_envelopes=make_synthetic_envelopes(campaign, children, funding))
    assert book.reserve(request, plan=plan).newly_reserved
    state = book._read()
    old_id, rec = next(iter(state["reservations"].items()))
    del state["reservations"][old_id]
    state["cleanup_open"] = []
    state["cleanup_blocked"] = []
    rec["reservation_id"] = RID
    rec["request"]["campaign_id"] = ATTEMPT
    rec["child_identities"] = children
    rec["provider_cleanup"] = {p.value: True for p in providers}
    rec["cleanup_open"] = False
    rec["lifecycle_terminal"] = True
    rec["creation_claims"] = []
    rec["financial"] = {"authority": {"path": "synthetic/authority.json", "sha256": "a" * 64},
        "binding": {}, "caps": {"project_eur": "10", "working_eur": "10", "protected_eur": "1",
            "personal_cash_eur": "10", "run_eur": "10", "batch_eur": "10", "stage_eur": "10",
            "providers_eur": {p.value: "10" for p in providers}},
        "rate": "1", "gross_eur": "0.50", "cash_eur": "0.50",
        "providers_eur": {"aws": "0.16", "azure": "0.17", "gcp": "0.17"},
        "external_gross_eur": "0", "external_cash_eur": "0", "scope_id": "offline-scope",
        "batch_id": "synthetic-batch", "stage_id": "synthetic-stage"}
    rec.pop("expiry_fingerprint", None)
    rec["evidence"] = ReservationEvidence.model_validate(rec["evidence"]).model_copy(
        update={"reservation_id": Evidence[str](value=RID), "campaign_id": ATTEMPT}).model_dump(mode="json")
    for claim in state["claims"].values():
        claim["evidence"] = rec["evidence"]
    for claim in state.get("campaign_claims", {}).values():
        claim["evidence"] = rec["evidence"]
    state["reservations"][RID] = rec
    book._write(state)
    return request, plan


def _proof_files(tmp_path: Path, book: BudgetLedger) -> UnlaunchedCancellationEvidence:
    state = book._read()
    rec = state["reservations"][RID]
    config_digest, runtime_digest = "1" * 64, "2" * 64
    request = rec["request"]
    rec["financial"]["binding"] = {"campaign_id": ATTEMPT, "config_id": "exp-903-acceptance",
        "config_sha256": config_digest, "cost_sha256": request["source_sha256"],
        "run_ids": rec["child_identities"], "namespaces": rec["plan"]["namespaces"],
        "runtime": {"implementation_revision": "70267ec9dd58b0149f281fb66a8d2520b19cfd92",
            "initialized_revisions": ["70267ec9dd58b0149f281fb66a8d2520b19cfd92"],
            "runtime_source": {"sha256": runtime_digest}}}
    book._write(state)
    state = book._read()
    rec = state["reservations"][RID]
    provider_results = {}
    for provider in ("aws", "azure", "gcp"):
        provider_results[provider] = {
            "primary_failure": (f"{provider} protect failed (registered expiry evidence is missing or "
                "mismatched: terraform_plan_review: operator inspection required before saved-plan apply; "
                "deadline expired at /private/tmp/review)")
        }
    terminal = {"stage_id": STAGE, "input_digests": {"V-S1": {
        "config_path": config_digest, "runtime_config_path": runtime_digest}},
        "entries": {"V-S1": {"state": "failed", "result": {
        "cleanup_open": False, "lifecycle_status_complete": False,
        "provider_results": provider_results}}}}
    state_root = tmp_path / "state"
    stage_root = state_root / STAGE
    stage_root.mkdir(parents=True, exist_ok=True)
    driver_path = state_root / ".campaign-driver.lock"
    journal_lock_path = stage_root / "campaign-journal.lock"
    driver_path.touch(); journal_lock_path.touch()
    driver_stat, journal_stat = driver_path.stat(), journal_lock_path.stat()
    recovery = {"schema_version": 1, "attempt_id": ATTEMPT, "reservation_id": RID,
        "controller_session_id": "campaign-acceptance-20261123T151109Z-52ef4ee7",
        "providers": [{"provider": p, "deployment_id": f"{rec['child_identities'][p]}-deployment",
            "expiry_registered": False, "creation_may_have_started": False, "cleanup_open": False,
            "journal_reference": str(stage_root / "V-S1" / p / f"{rec['plan']['namespaces'][p]}" /
                "run" / "deployment" / "attempt.json")}
            for p in ("aws", "azure", "gcp")]}
    owner = {"attempt_id": ATTEMPT, "controller_session_id": recovery["controller_session_id"],
        "state": "terminal", "active_processes": [],
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "proc_scan": {"proc_root": "/proc", "complete": True, "attempt_id": ATTEMPT,
            "boot_id": "synthetic", "uid": os.getuid(), "process_count": 0, "processes": []},
        "locks": {"driver": {"path": str(driver_path), "held_exclusive": True,
                "device": driver_stat.st_dev, "inode": driver_stat.st_ino},
            "journal": {"path": str(journal_lock_path), "held_exclusive": True,
                "device": journal_stat.st_dev, "inode": journal_stat.st_ino}}}
    terminal_path, recovery_path, owner_path = (tmp_path / "terminal.json", tmp_path / "recovery.json",
        tmp_path / "controller-owner.json")
    terminal_hash = _hash_write(terminal_path, terminal)
    recovery_hash = _hash_write(recovery_path, recovery)
    owner_hash = _hash_write(owner_path, owner)
    authority_path = tmp_path / "user-accounting-exception-20261123.json"
    authority_hash = _hash_write(authority_path, {"approved":
        "Cancel independently proven-unused estimates; preserve unchanged EUR154.23 personal-cash ceiling.",
        "not_approved": ["Fabricated final zero invoices"]})
    return UnlaunchedCancellationEvidence(RID, hashlib.sha256(book.path.read_bytes()).hexdigest(),
        "70267ec9dd58b0149f281fb66a8d2520b19cfd92", "cfdd9dda76ee4dbb3f42c08d5dcf364d962545ac",
        str(terminal_path), terminal_hash, str(recovery_path), recovery_hash,
        str(owner_path), owner_hash, str(journal_lock_path), str(driver_path),
        str(authority_path), authority_hash)


def _synthetic_apply(monkeypatch, proof):
    import cloud_network_benchmark.admission as admission
    monkeypatch.setattr(admission, "_live_proc_quiescence",
        lambda stage_id, attempt_id, reservation_id: {"complete": True, "processes": [], "scan_sha256": "0" * 64})


def test_protected_cwd_exception_requires_exact_recorded_identity_and_ancestry():
    import cloud_network_benchmark.admission as admission
    chain = admission._PROTECTED_PROC_CHAINS[65770]
    ancestry = [{"pid": pid, "uid": uid, "ppid": ppid, "start_ticks": ticks,
        "cmdline_sha256": cmd_sha} for pid, uid, ppid, ticks, cmd_sha in chain]
    row = {"pid": 65770, "uid": chain[0][1], "start_ticks": chain[0][3],
        "cmdline_sha256": chain[0][4], "cwd": None,
        "cwd_observation": "permission_denied_preexisting_infrastructure",
        "infrastructure_ancestry": ancestry,
        "infrastructure_evidence": {"observation_sha256": admission._PROTECTED_PROC_OBSERVATION_SHA256,
            "ancestry_sha256": admission._PROTECTED_PROC_ANCESTRY_SHA256,
            "boot_id": admission._PROTECTED_PROC_BOOT_ID, "uid": admission._PROTECTED_PROC_UID,
            "clock_ticks": admission._PROTECTED_PROC_CLK_TCK,
            "cap_started_at": admission._PROTECTED_PROC_CAP_START_UTC[RID],
            "cap_start_monotonic": admission._PROTECTED_PROC_CAP_START_MONOTONIC[RID]}}
    scan = {"boot_id": admission._PROTECTED_PROC_BOOT_ID,
        "uid": admission._PROTECTED_PROC_UID}
    assert admission._valid_protected_exception_row(row, scan, RID)
    changed = dict(row, cmdline_sha256="0" * 64)
    assert not admission._valid_protected_exception_row(changed, scan, RID)
    changed_ancestry = [dict(item) for item in ancestry]
    changed_ancestry[1]["ppid"] = 1
    assert not admission._valid_protected_exception_row(
        dict(row, infrastructure_ancestry=changed_ancestry), scan, RID)
    assert not admission._valid_protected_exception_row(
        row, dict(scan, boot_id="different-boot"), RID)
    assert not admission._valid_protected_exception_row(row, scan, "unregistered-reservation")


def test_protected_cwd_exception_reads_each_live_ancestry_identity(tmp_path, monkeypatch):
    import cloud_network_benchmark.admission as admission
    chain = admission._PROTECTED_PROC_CHAINS[65770]
    identities = {pid: {"pid": pid, "uid": uid, "ppid": ppid,
        "start_ticks": ticks, "cmdline_sha256": cmd_sha}
        for pid, uid, ppid, ticks, cmd_sha in chain}
    monkeypatch.setattr(admission, "_proc_identity", lambda _proc, pid: identities[pid])
    monkeypatch.setattr(admission.os, "getuid", lambda: admission._PROTECTED_PROC_UID)
    accepted = admission._protected_proc_exception(
        tmp_path, 65770, RID, admission._PROTECTED_PROC_BOOT_ID)
    assert accepted == list(identities.values())
    identities[65733] = dict(identities[65733], start_ticks=identities[65733]["start_ticks"] + 1)
    assert admission._protected_proc_exception(
        tmp_path, 65770, RID, admission._PROTECTED_PROC_BOOT_ID) is None


def _synthetic_validated_evidence(proof):
    return {"proof_type": "synthetic transaction-test evidence", "artifacts": [
        {"kind": kind, "path": path, "sha256": digest} for kind, path, digest in (
            ("terminal_journal", proof.terminal_journal_path, proof.terminal_journal_sha256),
            ("lifecycle_recovery", proof.recovery_path, proof.recovery_sha256),
            ("controller_quiescence", proof.controller_quiescence_path, proof.controller_quiescence_sha256))],
        "controller_owner_binding": {"attempt_id": ATTEMPT, "controller_session_id":
            "campaign-acceptance-20261123T151109Z-52ef4ee7", "state": "terminal",
            "driver_lock": {"path": proof.campaign_driver_lock_path, "held_exclusive": True, "device": 1, "inode": 1},
            "journal_lock": {"path": proof.campaign_lock_path, "held_exclusive": True, "device": 1, "inode": 2}}}


def _apply_synthetic(book, proof):
    return book._cancel_proven_unlaunched(proof, preview=False,
        _prevalidated_evidence=_synthetic_validated_evidence(proof))


def _preview_synthetic(book, proof):
    return book._cancel_proven_unlaunched(proof, preview=True,
        _prevalidated_evidence=_synthetic_validated_evidence(proof))


def test_proven_unlaunched_cancellation_restores_once_and_keeps_original_evidence(tmp_path, monkeypatch):
    costs = load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json")
    book = _ledger(tmp_path / "ledger.json")
    book.initialize(costs)
    request, plan = _reservation(book)
    original = book.inspect()["reservations"][RID]
    before = Decimal(book.inspect()["funding_remaining"]["primary"])
    proof = _proof_files(tmp_path, book)
    _synthetic_apply(monkeypatch, proof)

    result = _apply_synthetic(book, proof)
    after = book.inspect()
    rec = after["reservations"][RID]
    assert result["status"] == "cancelled_unused"
    assert rec["evidence"] == original["evidence"]
    assert rec["request"] == original["request"]
    assert rec["plan"] == original["plan"]
    assert Decimal(after["funding_remaining"]["primary"]) == before + Decimal("0.5")
    snapshot = dict(rec["financial"])
    snapshot.update({"gross_eur": "0", "cash_eur": "0",
        "providers_eur": {name: "0" for name in ("aws", "azure", "gcp")},
        "caps": {"project_eur": "100", "working_eur": "0.75", "protected_eur": "0",
            "personal_cash_eur": "100", "run_eur": "100", "batch_eur": "100", "stage_eur": "100",
            "providers_eur": {name: "100" for name in ("aws", "azure", "gcp")}}})
    check_shared_finances(after, snapshot, remaining_usd=Decimal("0.5"))
    # A later capture has a different fresh process inventory and file digest.
    owner_path = Path(proof.controller_quiescence_path)
    owner = json.loads(owner_path.read_text())
    owner["observed_at"] = datetime.now(timezone.utc).isoformat()
    owner["proc_scan"]["processes"] = [{"pid": 765432, "uid": os.getuid(),
        "start_ticks": 7654321, "cwd": str(tmp_path), "cmdline_sha256": "b" * 64,
        "ancestry_pids": [], "attempt_scope_match": False}]
    owner["proc_scan"]["process_count"] = 1
    fresh_owner_hash = _hash_write(owner_path, owner)
    replay_proof = proof.__class__(**{**proof.__dict__,
        "controller_quiescence_sha256": fresh_owner_hash})
    assert _apply_synthetic(book, replay_proof) == result
    assert book.inspect()["revision"] == after["revision"]
    repeated = book.reserve(request, plan=plan)
    assert repeated.status == "rejected"
    assert repeated.reasons == ("reservation_terminal",)


def test_lock_paths_are_derived_from_deep_provider_attempt_journals(tmp_path):
    import cloud_network_benchmark.admission as admission
    recovery = {"providers": [{"provider": provider, "journal_reference": str(
        tmp_path / STAGE / "V-S1" / provider / "namespace" / "run" / "deployment" / "attempt.json")}
        for provider in ("aws", "azure", "gcp")]}
    driver, journal = admission._cancellation_lock_paths(recovery, STAGE, "V-S1")
    assert driver == tmp_path / ".campaign-driver.lock"
    assert journal == tmp_path / STAGE / "campaign-journal.lock"


def test_real_proof_validator_accepts_the_four_pinned_source_records(tmp_path):
    import cloud_network_benchmark.admission as admission
    bridge = Path("/Users/katya/Desktop/University/Masters/cloud-network-benchmark/build/codex-repairs/review-handoff-20261123")
    cases = [
        ("609687d8ec5928cde1915b7d2fc2cc49", "budget-ledger.after-v6-460.json", "v6-terminal-campaign-journal.json", "v6-V-S1-lifecycle-recovery.json", "V-S1"),
        ("a331c6b0be290a7723782d478a4c4b33", "budget-ledger.after-v6-460.json", "v6-terminal-campaign-journal.json", "v6-V-S3-lifecycle-recovery.json", "V-S3"),
        ("f26710f8218ba3061a480b21337feebd", "budget-ledger.after-retry1-468.json", "actual-retry1-terminal-journal.json", "actual-retry1-V-S1-recovery.json", "V-S1"),
        ("306102d70d8e0cd0533e07e8d1ff4f06", "budget-ledger.after-retry1-468.json", "actual-retry1-terminal-journal.json", "actual-retry1-V-S3-recovery.json", "V-S3"),
    ]
    required = [bridge / name for case in cases for name in case[1:4]]
    authority = bridge / "user-accounting-exception-20261123.json"
    if not authority.is_file() or any(not path.is_file() for path in required):
        pytest.skip("private reviewed source bundle is not attached")

    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    for rid, ledger_name, terminal_name, recovery_name, entry in cases:
        ledger = bridge / ledger_name
        rec = json.loads(ledger.read_text())["reservations"][rid]
        terminal = bridge / terminal_name
        recovery = bridge / recovery_name
        recovery_obj = json.loads(recovery.read_text())
        stage_id = next(spec[0] for spec in admission._UNLAUNCHED_RESERVATIONS.values()
            if spec[2] == recovery_obj["attempt_id"])
        driver, journal = admission._cancellation_lock_paths(recovery_obj, stage_id, entry)
        owner_path = tmp_path / f"owner-{rid}.json"
        owner = {"attempt_id": recovery_obj["attempt_id"],
            "controller_session_id": recovery_obj["controller_session_id"], "state": "terminal",
            "active_processes": [], "observed_at": datetime.now(timezone.utc).isoformat(),
            "proc_scan": {"proc_root": "/proc", "complete": True,
                "attempt_id": recovery_obj["attempt_id"], "boot_id": "offline-fixture",
                "uid": os.getuid(), "process_count": 0, "processes": []},
            "locks": {"driver": {"path": str(driver), "held_exclusive": True, "device": 1, "inode": 1},
                "journal": {"path": str(journal), "held_exclusive": True, "device": 1, "inode": 2}}}
        owner_hash = _hash_write(owner_path, owner)
        azure = {}
        if rid == "306102d70d8e0cd0533e07e8d1ff4f06":
            anchor = bridge / "azure-anchor" / "s3-anchor.json"
            settings = bridge / "azure-anchor" / "s3-settings.json"
            azure = {"azure_anchor_path": str(anchor), "azure_anchor_sha256": digest(anchor),
                "azure_settings_path": str(settings), "azure_settings_sha256": digest(settings)}
        proof = UnlaunchedCancellationEvidence(rid, digest(ledger), admission._UNLAUNCHED_SOURCE_COMMIT,
            admission._UNLAUNCHED_SOURCE_TREE, str(terminal), digest(terminal), str(recovery), digest(recovery),
            str(owner_path), owner_hash, str(journal), str(driver), str(authority), digest(authority), **azure)
        result = admission._verify_unlaunched_cancellation_proof(proof, rec)
        assert result["proof_type"] == "pinned_unlaunched_protection_rejection_v1"
        assert result["reservation_id"] == rid
        if rid == cases[0][0]:
            changed = json.loads(json.dumps(rec))
            changed["financial"]["binding"]["config_id"] = "wrong-config"
            with pytest.raises(ValueError, match="financial binding"):
                admission._verify_unlaunched_cancellation_proof(proof, changed)
            owner["active_processes"] = [{"pid": 123, "start_ticks": 456}]
            owner_hash = _hash_write(owner_path, owner)
            active_owner_proof = proof.__class__(**{**proof.__dict__,
                "controller_quiescence_sha256": owner_hash})
            with pytest.raises(ValueError, match="controller owner is active"):
                admission._verify_unlaunched_cancellation_proof(active_owner_proof, rec)
            owner["active_processes"] = []
            owner_hash = _hash_write(owner_path, owner)
            fresh_proof = proof.__class__(**{**proof.__dict__,
                "controller_quiescence_sha256": owner_hash})
            dummy = tmp_path / "dummy.lock"
            dummy.touch()
            with pytest.raises(ValueError, match="campaign locks do not derive"):
                admission._verify_unlaunched_cancellation_proof(
                    fresh_proof.__class__(**{**fresh_proof.__dict__, "campaign_lock_path": str(dummy)}), rec)
            owner["proc_scan"]["complete"] = False
            owner_hash = _hash_write(owner_path, owner)
            with pytest.raises(ValueError, match="controller owner is active"):
                admission._verify_unlaunched_cancellation_proof(proof.__class__(**{
                    **proof.__dict__, "controller_quiescence_sha256": owner_hash}), rec)


def test_real_lock_contention_blocks_cancellation(tmp_path, monkeypatch):
    costs = load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json")
    book = _ledger(tmp_path / "ledger.json")
    book.initialize(costs)
    _reservation(book)
    proof = _proof_files(tmp_path, book)
    _synthetic_apply(monkeypatch, proof)
    book.lock_timeout_seconds = 0.01
    fd = os.open(proof.campaign_lock_path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(TimeoutError, match="owned by another process"):
            _preview_synthetic(book, proof)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_cancelled_reservations_keep_fx_and_external_liability_highwater(monkeypatch):
    import cloud_network_benchmark.financial_controls as finance
    monkeypatch.setattr(finance, "validate_stored_commitment", lambda *args, **kwargs: None)
    old = {"rate": "2", "external_gross_eur": "13", "external_cash_eur": "10",
        "gross_eur": "5", "cash_eur": "5", "providers_eur": {p: "1.67" for p in ("aws", "azure", "gcp")},
        "batch_id": "old-batch", "stage_id": "old-stage"}
    state = {"scope_id": "scope", "reservations": {"cancelled": {
        "financial": old, "financial_terminal_state": "cancelled_unused",
        "reserved_cost_usd": "0", "incurred_usd": "0", "plan": {"providers": []}}}}
    snapshot = {"caps": {"project_eur": "20", "working_eur": "14.99",
        "protected_eur": "1", "personal_cash_eur": "14.99", "run_eur": "20",
        "batch_eur": "20", "stage_eur": "20", "providers_eur": {p: "20" for p in ("aws", "azure", "gcp")}},
        "rate": "1", "gross_eur": "0", "cash_eur": "0", "providers_eur": {p: "0" for p in ("aws", "azure", "gcp")},
        "external_gross_eur": "0", "external_cash_eur": "0", "batch_id": "new", "stage_id": "new"}
    with pytest.raises(FinancialError, match="financial_project_ceiling_exceeded"):
        check_shared_finances(state, snapshot, remaining_usd=Decimal("1"))
    assert snapshot["external_gross_eur"] == "13"
    assert snapshot["external_cash_eur"] == "10"


def test_cancellation_rejects_hash_mismatch_partial_coverage_and_active_creation(tmp_path, monkeypatch):
    costs = load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json")
    book = _ledger(tmp_path / "ledger.json")
    book.initialize(costs)
    _reservation(book)
    proof = _proof_files(tmp_path, book)
    _synthetic_apply(monkeypatch, proof)
    with pytest.raises(ValueError, match="hash mismatch"):
        book._cancel_proven_unlaunched(proof.__class__(**{**proof.__dict__, "recovery_sha256": "0" * 64}), preview=False, _prevalidated_evidence=_synthetic_validated_evidence(proof))
    broken = json.loads(Path(proof.recovery_path).read_text())
    broken["providers"].pop()
    import cloud_network_benchmark.admission as admission
    with pytest.raises(ValueError, match="lock-binding provider rows"):
        admission._cancellation_lock_paths(broken, STAGE, "V-S1")

    monkeypatch.undo()
    proof = _proof_files(tmp_path, book)
    with pytest.raises(ValueError, match="pinned production source identity"):
        book.preview_proven_unlaunched(proof.__class__(**{**proof.__dict__, "source_tree": "0" * 40}))

    # Restore the valid recovery artifact, then make the reservation claim active.
    _synthetic_apply(monkeypatch, proof)
    proof = _proof_files(tmp_path, book)
    state = book._read()
    state["reservations"][RID]["creation_claims"] = ["aws"]
    book._write(state)
    proof = proof.__class__(**{**proof.__dict__,
        "expected_ledger_sha256": hashlib.sha256(book.path.read_bytes()).hexdigest()})
    with pytest.raises(ValueError, match="lifecycle, creation, cleanup"):
        _apply_synthetic(book, proof)


def test_positive_later_billing_reinstates_conservative_hold_and_blocks_new_work(tmp_path, monkeypatch):
    costs = load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json")
    book = _ledger(tmp_path / "ledger.json")
    book.initialize(costs)
    _reservation(book)
    proof = _proof_files(tmp_path, book)
    _synthetic_apply(monkeypatch, proof)
    _apply_synthetic(book, proof)
    book.reconcile(Reconciliation("later-charge", RID, Provider.AWS,
        hashlib.sha256(b"later provider billing evidence").hexdigest(), incurred_usd=Decimal("0.10"),
        actual=True, cleanup_verified_absent=True, billable_resources_present=False,
        occurred_at=datetime(2026, 11, 23, 1, tzinfo=timezone.utc),
        source_path="synthetic/later-billing.json", absence_evidence_id="absence-later",
        absent_resource_ids=("aws:vm-a", "aws:vm-b", "aws:expiry-support")))
    state = book.inspect()
    assert state["reservations"][RID]["financial_terminal_state"] == "contradicted"
    assert state["reservations"][RID]["financial_cancellation"]["status"] == "contradicted"
    assert RID in state["budget_blocked"]


def test_unknown_later_cleanup_reinstates_hold_and_blocks_reserve(tmp_path, monkeypatch):
    costs = load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json")
    book = _ledger(tmp_path / "ledger.json")
    book.initialize(costs)
    request, plan = _reservation(book)
    proof = _proof_files(tmp_path, book)
    _synthetic_apply(monkeypatch, proof)
    _apply_synthetic(book, proof)
    book.reconcile(Reconciliation("later-unknown-cleanup", RID, Provider.AWS,
        hashlib.sha256(b"later cleanup status").hexdigest(), incurred_usd=None,
        actual=False, cleanup_verified_absent=False, billable_resources_present=None,
        cleanup_status_reported=True,
        occurred_at=datetime(2026, 11, 23, 2, tzinfo=timezone.utc),
        source_path="synthetic/later-cleanup.json"))
    state = book.inspect()
    assert state["reservations"][RID]["financial_terminal_state"] == "contradicted"
    assert state["reservations"][RID]["released_usd"] == "0"
    assert RID in state["budget_blocked"]
    decision = book.reserve(request, plan=plan)
    assert decision.status == "rejected"
    assert "budget_exhausted" in decision.reasons


def test_cancellation_rejects_any_nonzero_billing_or_transfer_debit(tmp_path, monkeypatch):
    costs = load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json")
    book = _ledger(tmp_path / "ledger.json")
    book.initialize(costs)
    _reservation(book)
    state = book._read()
    rec = state["reservations"][RID]
    rec["provider_billing"]["aws"]["estimate_usd"] = "0.01"
    book._write(state)
    proof = _proof_files(tmp_path, book)
    _synthetic_apply(monkeypatch, proof)
    with pytest.raises(ValueError, match="billing evidence is nonzero"):
        _preview_synthetic(book, proof)

    state = book._read()
    rec = state["reservations"][RID]
    rec["provider_billing"]["aws"]["estimate_usd"] = None
    rec["result_transfer"] = {"aws": {"debited_bytes": 1,
        "attempts": {"synthetic-attempt": 1}}}
    book._write(state)
    proof = _proof_files(tmp_path, book)
    with pytest.raises(ValueError, match="result-transfer activity"):
        _preview_synthetic(book, proof)

