from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timezone

import pytest

from cloud_network_benchmark.azure_auxiliary_cleanup import (AuxiliaryCleanupCohort, authorization_payload,
    validate_spec)
from cloud_network_benchmark.lifecycle_runtime import _validate_azure_auxiliary_owners


SUBSCRIPTION = "188a5848-76fb-4d57-8f2a-32c09284d486"
GROUP = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/NetworkWatcherRG"
WATCHER = GROUP + "/providers/Microsoft.Network/networkWatchers/NetworkWatcher_swedencentral"


def _owner(entry: str, suffix: str) -> dict:
    campaign = f"campaign-{suffix}"
    run_id = f"{campaign}-azure"
    deployment = f"{run_id}-deployment"
    group_suffix = hashlib.sha256(f"{campaign}\0{deployment}".encode()).hexdigest()[:8]
    stem = re.sub(r"[^a-z0-9-]", "-", f"cnb-{campaign}-{deployment}".lower()).strip("-")[:36].rstrip("-")
    stem += "-" + group_suffix
    rg = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{stem}"
    return {
        "entry_id": entry,
        "run_id": run_id,
        "campaign_id": campaign,
        "deployment_id": deployment,
        "config_sha256": hashlib.sha256(suffix.encode()).hexdigest(),
        "resource_group_id": rg,
        "vm_ids": [rg + f"/providers/Microsoft.Compute/virtualMachines/{stem}-{i}" for i in ("a", "b")],
        "nic_ids": [rg + f"/providers/Microsoft.Network/networkInterfaces/{stem}-{i}-nic" for i in ("a", "b")],
    }


def _spec(tmp_path, *, owners=None, target=WATCHER, status="approved"):
    owners = owners or [_owner("V-S1", "s1"), _owner("V-S3", "s3")]
    source = tmp_path / "config.yaml"
    source.write_text("source: exact\n")
    runtime_path = tmp_path / "runtime.json"
    runtime_path.write_bytes(b"exact runtime")
    runtime_sha = hashlib.sha256(runtime_path.read_bytes()).hexdigest()
    card = {
        "schema_version": 1,
        "approval_status": status,
        "action": "delete_exact_auto_created_network_watcher_and_empty_parent",
        "profile": "normal_pair" if len(owners) == 2 else "calibration_self",
        "runtime_sha256": runtime_sha,
        "subscription_id": SUBSCRIPTION,
        "region": "swedencentral",
        "resource_group_id": GROUP,
        "watcher_id": target,
        "maximum_leaf_deletes": 1,
        "maximum_group_deletes": 1,
        "owners": owners,
        "source_path": str(source),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "expected_caller": "00000000-0000-0000-0000-000000000001",
        "not_before_utc": "2026-11-25T00:00:00Z",
        "not_after_utc": "2026-11-26T00:00:00Z",
    }
    card_path = tmp_path / "approval.json"
    card_path.write_text(json.dumps(card))
    return {
        "schema_version": 1,
        "cohort_id": "cleanup-test",
        "state_root": str(tmp_path / "shared"),
        "subscription_id": SUBSCRIPTION,
        "region": "swedencentral",
        "resource_group_id": GROUP,
        "watcher_id": WATCHER,
        "profile": "normal_pair" if len(owners) == 2 else "calibration_self",
        "owners": owners,
        "authorization": {
            "card_path": str(card_path),
            "card_sha256": hashlib.sha256(card_path.read_bytes()).hexdigest(),
            "runtime_path": str(runtime_path),
            "runtime_sha256": runtime_sha,
        },
    }


def test_future_authorization_fixture_accepts_exact_target_and_source_bindings(tmp_path):
    spec = _spec(tmp_path)
    assert validate_spec(spec)["watcher_id"] == WATCHER


@pytest.mark.parametrize("mutation", ["foreign_subscription", "foreign_region", "foreign_watcher", "draft_card"])
def test_authorization_fails_closed_for_foreign_or_unapproved_bindings(tmp_path, mutation):
    spec = _spec(tmp_path)
    if mutation == "foreign_subscription":
        spec["subscription_id"] = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    elif mutation == "foreign_region":
        spec["region"] = "northeurope"
    elif mutation == "foreign_watcher":
        spec = _spec(tmp_path, target=GROUP + "/providers/Microsoft.Network/networkWatchers/other")
    else:
        spec = _spec(tmp_path, status="draft")
    with pytest.raises((ValueError, PermissionError)):
        validate_spec(spec)


def test_authorization_card_byte_change_is_rejected(tmp_path):
    spec = _spec(tmp_path)
    with open(spec["authorization"]["card_path"], "a") as stream:
        stream.write(" ")
    with pytest.raises(PermissionError, match="changed"):
        validate_spec(spec)


def test_authorization_source_parent_symlink_is_rejected(tmp_path):
    spec = _spec(tmp_path)
    real = tmp_path / "source-real"
    real.mkdir()
    source = real / "input.yaml"
    source.write_text("source: exact\n")
    link = tmp_path / "source-link"
    link.symlink_to(real, target_is_directory=True)
    card_path = spec["authorization"]["card_path"]
    card = json.loads(open(card_path).read())
    card["source_path"] = str(link / source.name)
    card["source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    open(card_path, "w").write(json.dumps(card))
    spec["authorization"]["card_sha256"] = hashlib.sha256(open(card_path, "rb").read()).hexdigest()
    with pytest.raises(PermissionError, match="source is missing or changed"):
        validate_spec(spec)


def test_two_parent_barrier_grants_one_leaf_claim_and_orders_group_after_leaf(tmp_path):
    spec = _spec(tmp_path)
    coordinator_a = AuxiliaryCleanupCohort(spec)
    coordinator_b = AuxiliaryCleanupCohort(spec)
    owners = spec["owners"]
    hashes = [hashlib.sha256(b"baseline-" + str(i).encode()).hexdigest() for i in range(2)]
    event_hashes = [hashlib.sha256(b"events-" + str(i).encode()).hexdigest() for i in range(2)]
    results = []

    def register(coordinator, owner, baseline, activity):
        results.append(coordinator.wait_all_absent(owner=owner, baseline_sha256=baseline,
            activity_sha256=activity, deadline_monotonic=100.0, clock=lambda: 10.0))

    first = threading.Thread(target=register, args=(coordinator_a, owners[0], hashes[0], event_hashes[0]))
    first.start()
    register(coordinator_b, owners[1], hashes[1], event_hashes[1])
    first.join(timeout=2)
    assert not first.is_alive()
    claim_a = coordinator_a.claim_leaf(now_monotonic=20.0)
    claim_b = coordinator_b.claim_leaf(now_monotonic=20.0)
    assert (claim_a is None) != (claim_b is None)
    claim_id = claim_a or claim_b
    assert claim_id
    leaf_evidence = hashlib.sha256(b"typed-exact-404").hexdigest()
    winner = coordinator_a if claim_a else coordinator_b
    winner.record_outcome(claim_id, action="leaf_delete", identity=WATCHER,
        outcome="succeeded", evidence_sha256=leaf_evidence, typed_404_identity=WATCHER)
    assert winner.claim_group(claim_id, now_monotonic=21.0)
    winner.record_outcome(claim_id, action="group_delete", identity=GROUP,
        outcome="succeeded", evidence_sha256=hashlib.sha256(b"group-absent").hexdigest())
    assert winner.wait_complete(deadline_monotonic=90, clock=lambda: 22.0)["claim"]["state"] == "complete"


def test_missing_peer_and_deadline_never_claim_leaf_or_extend_deadline(tmp_path):
    spec = _spec(tmp_path)
    coordinator = AuxiliaryCleanupCohort(spec)
    owner = spec["owners"][0]
    coordinator.register_absent(owner, baseline_sha256=hashlib.sha256(b"base").hexdigest(),
        activity_sha256=hashlib.sha256(b"events").hexdigest(), deadline_monotonic=2.0)
    with pytest.raises(TimeoutError, match="deadline"):
        coordinator.wait_all_absent(owner=owner, baseline_sha256=hashlib.sha256(b"base").hexdigest(),
            activity_sha256=hashlib.sha256(b"events").hexdigest(), deadline_monotonic=2.0,
            clock=lambda: 2.0, sleep=lambda _: None)
    with pytest.raises(RuntimeError, match="active or unproven peer"):
        coordinator.claim_leaf(now_monotonic=1.0)


def test_claim_terminal_outcome_cannot_be_retried(tmp_path):
    spec = _spec(tmp_path)
    cohort = AuxiliaryCleanupCohort(spec)
    for index, owner in enumerate(spec["owners"]):
        cohort.register_absent(owner, baseline_sha256=hashlib.sha256(f"b{index}".encode()).hexdigest(),
            activity_sha256=hashlib.sha256(f"a{index}".encode()).hexdigest(), deadline_monotonic=100.0)
    claim = cohort.claim_leaf(now_monotonic=1.0)
    assert claim
    cohort.record_outcome(claim, action="leaf_delete", identity=WATCHER, outcome="unknown",
        evidence_sha256=hashlib.sha256(b"unknown").hexdigest())
    assert cohort.claim_leaf(now_monotonic=2.0) is None
    with pytest.raises(RuntimeError, match="terminal"):
        cohort.record_outcome(claim, action="leaf_delete", identity=WATCHER, outcome="succeeded",
            evidence_sha256=hashlib.sha256(b"later").hexdigest(), typed_404_identity=WATCHER)


def test_leaf_absence_journal_can_resume_group_only_without_reclaiming_leaf(tmp_path):
    spec = _spec(tmp_path)
    cohort = AuxiliaryCleanupCohort(spec)
    owners = spec["owners"]
    for index, owner in enumerate(owners):
        cohort.register_absent(owner, baseline_sha256=hashlib.sha256(f"b{index}".encode()).hexdigest(),
            activity_sha256=hashlib.sha256(f"a{index}".encode()).hexdigest(), deadline_monotonic=100.0)
    claim_id = cohort.claim_leaf(now_monotonic=1.0)
    assert claim_id
    cohort.record_outcome(claim_id, action="leaf_delete", identity=WATCHER, outcome="succeeded",
        evidence_sha256=hashlib.sha256(b"persisted exact 404").hexdigest(), typed_404_identity=WATCHER)
    recovered = AuxiliaryCleanupCohort(spec)
    assert recovered.claim_leaf(now_monotonic=2.0) == claim_id
    assert recovered.claim_group(claim_id, now_monotonic=2.0)
    recovered.record_outcome(claim_id, action="group_delete", identity=GROUP, outcome="succeeded",
        evidence_sha256=hashlib.sha256(b"group absent").hexdigest())
    assert recovered.snapshot()["claim"]["state"] == "complete"


def test_authorization_window_expiry_blocks_action(tmp_path):
    spec = _spec(tmp_path)
    with pytest.raises(PermissionError, match="outside its finite validity window"):
        authorization_payload(spec, datetime(2026, 11, 27, tzinfo=timezone.utc))


def test_state_root_symlink_is_rejected(tmp_path):
    spec = _spec(tmp_path)
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    spec["state_root"] = str(link)
    with pytest.raises(ValueError, match="symlink"):
        AuxiliaryCleanupCohort(spec)


def test_runtime_owner_set_is_bound_to_barrier_run_identities():
    owners = [{**_owner("V-S1", "s1"), "provider": "azure"},
        {**_owner("V-S3", "s3"), "provider": "azure"}]
    runtime = {"post_measurement_barrier": {"expected_participants": [
        {"entry_id": entry, "provider": provider, "run_id": run_id}
        for entry, provider, run_id in (("V-S1", "azure", "campaign-s1-azure"),
            ("V-S3", "azure", "campaign-s3-azure"), ("V-S1", "aws", "a1"),
            ("V-S1", "gcp", "g1"), ("V-S3", "aws", "a3"), ("V-S3", "gcp", "g3"))]}}
    auxiliary = {"profile": "normal_pair", "owners": owners}
    assert len(_validate_azure_auxiliary_owners(auxiliary, runtime)) == 2
    runtime["post_measurement_barrier"]["expected_participants"][1]["run_id"] = "foreign-run"
    with pytest.raises(ValueError, match="barrier identities"):
        _validate_azure_auxiliary_owners(auxiliary, runtime)


def test_calibration_runtime_allows_only_the_single_registered_azure_parent():
    owner = {**_owner("CAL-S1", "cal"), "provider": "azure"}
    runtime = {"measurement_start_barrier": {"expected_participants": [
        {"entry_id": "CAL-S1", "provider": provider, "run_id": run}
        for provider, run in (("azure", owner["run_id"]), ("aws", "calibration-aws"),
            ("gcp", "calibration-gcp"))]}}
    auxiliary = {"profile": "calibration_self", "owners": [owner]}
    assert len(_validate_azure_auxiliary_owners(auxiliary, runtime)) == 1
    auxiliary["owners"] = [_owner("V-S1", "foreign") | {"provider": "azure"}]
    with pytest.raises(ValueError, match="scope"):
        _validate_azure_auxiliary_owners(auxiliary, runtime)
