"""Positive real generation/runtime/admission wiring with explicit synthetic inputs.

All sources below are test-only local files. No provider command runs and these
review declarations cannot be delivered as empirical acceptance evidence.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from cloud_network_benchmark.admission import BudgetLedger, COST_COMPONENTS, load_cost_inputs
from cloud_network_benchmark.campaign_inputs import prepare_stage
from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.lifecycle_runtime import load_runtime_config, load_initialized_campaign, build_admission_request
from cloud_network_benchmark.scheduling import load_schedule


class Clock:
    def __init__(self):
        self.now = datetime.now(timezone.utc).replace(microsecond=0)

    def utc_now(self):
        return self.now

    def monotonic(self):
        return 100.0


def operator_settings(tmp_path, root, monkeypatch):
    import cloud_network_benchmark.campaign_inputs as inputs
    # Common-N selection is separately tested from saved real DTO fixtures; this
    # generator test isolates propagation of an already-reviewed selected value.
    monkeypatch.setattr(inputs, "_common_n", lambda _: {"state": "frozen", "common_n": 4,
        "source_campaign_id": "offline-calibration", "source_sha256": "a" * 64, "plateau_found": True})
    monkeypatch.setattr(inputs.shutil, "which", lambda name: f"/offline-test-bin/{name}")
    monkeypatch.setattr(inputs, "check_provider_identities", lambda _: [], raising=False)
    source = tmp_path / "synthetic-review.yaml"
    failure = tmp_path / "synthetic-failure-review.txt"
    failure.write_text("SYNTHETIC OFFLINE TEST ONLY: no provider validation")
    source.write_text(yaml.safe_dump({"fixture_scope": "synthetic-offline-test-only", "accepted_campaigns": [],
        "registered_failure_review": str(failure), "repository_root": str(root)}))
    key = tmp_path / "synthetic-key"
    key.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA offline-test-only\n")
    keys = {"authentication_reference": str(key), "controller_cidr": "192.0.2.1/32",
            "diagnostic_interfaces": {"vm_a": "ens5", "vm_b": "ens5"}}
    settings = {"schema_version": 1, "state_root": str(tmp_path / "private"),
        "results_root": str(tmp_path / "results"), "minimum_free_bytes": 1024,
        "ledger": {"path": str(tmp_path / "private" / "ledger.json"), "scope_id": "offline-generated-shared"},
        "credential_refresh": {"confirmed": True, "source": str(source),
            "valid_through_utc": (datetime.now(timezone.utc) + timedelta(days=40)).isoformat()},
        "providers": {
            "aws": {**keys, "account_id": "123456789012", "ami_ids": {"eu-central-1": "ami-0123456789abcdef0"}, "public_key_path": str(key)},
            "azure": {**keys, "subscription_id": "11111111-1111-1111-1111-111111111111",
                "tenant_id": "22222222-2222-2222-2222-222222222222", "selected_image": "Canonical:ubuntu-24_04-lts:server:24.04.202610010",
                "ssh_public_key_path": str(key), "poll_seconds": 1, "command_timeout_seconds": 30},
            "gcp": {**keys, "project_id": "offline-project", "images": {region: "projects/ubuntu-os-cloud/global/images/ubuntu-2404-lts-amd64-v20261001" for region in ("europe-west3", "europe-west2")}, "public_key_path": str(key)}},
        "approvals": {name: {"confirmed": True, "source": str(source)} for name in
            ("f10_offline", "paid_authorization", "quota_review", "paid_acceptance", "common_n", "study_window", "calibration_authorized")},
        "pricing": {"reviewed": True, "id": "offline-generated-test-prices", "source": str(source),
            "checked_on": datetime.now(timezone.utc).date().isoformat(),
            "funding": {"primary": "100", "replacements": "0", "tax": "20", "uncertainty": "25"},
            "expiry": {"aws": {"automation_step_price": ".001", "script_second_price": ".00001"},
                "azure": {"automation_rate_per_job_minute": ".001", "job_minutes_bound": "5"},
                "gcp": {"zero_support_rationale": "synthetic fixture no cost"}}, "components": {}},
        "stage_inputs": {}}
    for category in ("acceptance", "calibration", "day", "s1", "s3"):
        settings["pricing"]["components"][category] = {provider: {name: {
            "amount_usd": ".01", "quantity": "1", "unit": "test-unit", "inclusion_rule": "synthetic offline fixture",
            "funding_bucket": "primary"} for name in COST_COMPONENTS} for provider in ("aws", "azure", "gcp")}
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
    for stage, measurement in (("acceptance", 76), ("week", 226), ("calibration", 1180)):
        settings["stage_inputs"][stage] = {"first_day_utc": tomorrow,
            "start_utc": f"{tomorrow}T06:00:00+00:00",
            "windows_utc": {"night": "00:00:00", "morning": "06:00:00", "afternoon": "12:00:00", "evening": "18:00:00"},
            "allowance": {"registration_seconds": 10, "provision_seconds": 60, "readiness_seconds": 10,
                "stabilization_seconds": 60, "measurement_seconds": measurement, "measurement_shutdown_seconds": 1,
                "retrieval_seconds": 20, "cleanup_seconds": 20, "source_id": "synthetic-measured-fixture",
                "provider_leads_seconds": {"aws": 360, "azure": 60, "gcp": 60}}}
    return settings


def register_generated_cleanup(stage, request):
    generated = Path(stage["entries"][0]["config_path"]).parent
    assert generated.name == stage["stage_id"] and generated.parent.name == "campaign-generated"
    request.addfinalizer(lambda: shutil.rmtree(generated, ignore_errors=True))


def load_entry(stage, entry, root):
    runtime = load_runtime_config(Path(entry["runtime_config_path"]))
    parent, children = load_initialized_campaign(Path(entry["parent_manifest_path"]), repository_root=root,
        config_path=Path(entry["config_path"]), schedule_path=Path(entry["schedule_path"]) if entry["schedule_path"] else None)
    schedule_entry = None
    if entry["schedule_path"]:
        base, _ = resolve_campaign(Path(entry["config_path"]), root)
        schedule_entry = load_schedule(Path(entry["schedule_path"]),
            config_paths={base.config_id: Path(entry["config_path"])}, repository_root=root)[0]
    campaign, _ = resolve_campaign(Path(entry["config_path"]), root, schedule_entry=schedule_entry)
    costs = load_cost_inputs(Path(entry["cost_inputs_path"]))
    request, plan = build_admission_request(campaign=campaign, parent=parent, children=children,
        costs=costs, runtime=runtime, controller_session_id=stage["controller_session_id"])
    return runtime, parent, children, request, plan


def test_actual_acceptance_generation_loads_and_two_shared_reservations_are_admitted(repository_root, tmp_path, monkeypatch, request):
    settings = operator_settings(tmp_path, repository_root, monkeypatch)
    clock = Clock()
    stage = prepare_stage({"stage": "acceptance"}, settings, repository_root,
        Path(settings["state_root"]), Path(settings["results_root"]), clock)
    register_generated_cleanup(stage, request)
    book = BudgetLedger(Path(stage["ledger_path"]), scope_id=stage["scope_id"],
        utc_now=clock.utc_now, monotonic_now=clock.monotonic, controller_session_id=stage["controller_session_id"])
    book.initialize(load_cost_inputs(Path(stage["entries"][0]["cost_inputs_path"])))
    namespaces = []
    for entry in stage["entries"][:2]:
        runtime, parent, children, request, plan = load_entry(stage, entry, repository_root)
        namespaces.extend(plan.namespaces.values())
        decision = book.reserve(request, plan=plan)
        assert decision.status == "reserved", decision.reasons
        assert len(children) == 3
        assert all(child.mode == "acceptance" for child in children.values())
        assert all(Path(row["bootstrap_template"]).is_file() for row in runtime["providers"].values())
    assert len(book.inspect()["reservations"]) == 2
    assert len(namespaces) == len(set(namespaces)) == 6


def test_actual_week_generation_preserves_all56_schedule_hashes_and_runtime_isolation(repository_root, tmp_path, monkeypatch, request):
    settings = operator_settings(tmp_path, repository_root, monkeypatch)
    stage = prepare_stage({"stage": "week"}, settings, repository_root,
        Path(settings["state_root"]), Path(settings["results_root"]), Clock())
    register_generated_cleanup(stage, request)
    entries = stage["entries"]
    assert len(entries) == 56
    assert len(Counter(entry["scheduled_start"] for entry in entries)) == 28
    assert set(Counter(entry["scheduled_start"] for entry in entries).values()) == {2}
    namespaces, all_run_ids, priorities = [], [], []
    for entry in entries:
        runtime, parent, children, request, plan = load_entry(stage, entry, repository_root)
        priorities.append(request.priority)
        namespaces.extend(plan.namespaces.values())
        all_run_ids.extend(child.run_id for child in children.values())
        saved_schedule = Path(entry["schedule_path"]).read_bytes()
        expected_hash = hashlib.sha256(saved_schedule).hexdigest()
        assert all(child.schedule_entry.value.source_sha256 == expected_hash for child in children.values())
        for child in children.values():
            # Source CSV bytes and the manifest's canonical single-row
            # snapshot may use different newline encodings; retain both hashes.
            config_snapshot = Path(child.config_provenance.snapshot_path)
            if not config_snapshot.is_absolute():
                config_snapshot = repository_root / config_snapshot
            snapshot_hash = hashlib.sha256((config_snapshot.parent / "schedule.csv").read_bytes()).hexdigest()
            assert child.config_provenance.schedule_sha256.value == snapshot_hash
        assert all(row["options"]["authentication_reference"] == str(tmp_path / "synthetic-key") for row in runtime["providers"].values())
        assert runtime["ledger"]["scope_id"] == stage["scope_id"]
        assert plan.execution_scope == "paid_request"
    assert Counter(priorities) == {"P0": 28, "P1": 28}
    assert len(all_run_ids) == len(set(all_run_ids)) == 168
    assert len(namespaces) == len(set(namespaces)) == 168
    assert len({entry["private_root"] for entry in entries}) == 56
    assert len({entry["runtime_config_path"] for entry in entries}) == 56
    assert len({entry["cost_inputs_path"] for entry in entries}) == 1
    # Real source-template accounting at the paired first slot. Production paid
    # admission must still refuse our synthetic empty acceptance review.
    slot_clock = Clock()
    slot_clock.now = datetime.fromisoformat(entries[0]["launch_at"])
    book = BudgetLedger(Path(stage["ledger_path"]), scope_id=stage["scope_id"],
        utc_now=slot_clock.utc_now, monotonic_now=slot_clock.monotonic,
        controller_session_id=stage["controller_session_id"])
    book.initialize(load_cost_inputs(Path(entries[0]["cost_inputs_path"])))
    for entry in entries[:2]:
        _, _, _, request, plan = load_entry(stage, entry, repository_root)
        denied = book.reserve(request, plan=plan)
        assert denied.status in {"rejected", "pending"} and "prerequisite_pending" in denied.reasons
        # Keep real paid source-template and accounting validation; only the
        # empirical acceptance boundary is explicitly fake in this offline case.
        with monkeypatch.context() as approval_boundary:
            approval_boundary.setattr("cloud_network_benchmark.admission._validate_paid_acceptance_evidence", lambda *_: None)
            admitted = book.reserve(request, plan=plan)
        assert admitted.status == "reserved", admitted.reasons
    assert len(book.inspect()["reservations"]) == 2
    assert {row["session"] for row in book.inspect()["reservations"].values()} == {stage["controller_session_id"]}


def test_complete_static_check_uses_only_readonly_identity_boundary_without_mutation(repository_root, tmp_path, monkeypatch):
    import copy
    import cloud_network_benchmark.campaign_inputs as inputs

    settings = operator_settings(tmp_path, repository_root, monkeypatch)
    original = copy.deepcopy(settings)
    called = []
    monkeypatch.setattr(inputs, "check_provider_identities", lambda providers: called.append(providers) or [])
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    assert inputs.validate_stage_inputs({"stage": "acceptance"}, settings, repository_root) == []
    assert called == [settings["providers"]]
    assert settings == original
    assert sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*")) == before
    assert not Path(settings["state_root"]).exists()
    assert not Path(settings["results_root"]).exists()


def test_missing_real_identity_is_actionable_and_does_not_prepare_state(repository_root, tmp_path, monkeypatch):
    import cloud_network_benchmark.campaign_inputs as inputs

    settings = operator_settings(tmp_path, repository_root, monkeypatch)
    monkeypatch.setattr(inputs, "check_provider_identities", lambda _: ["AWS credentials unavailable; refresh reviewed account"])
    blockers = inputs.validate_stage_inputs({"stage": "acceptance"}, settings, repository_root)
    assert blockers == ["AWS credentials unavailable; refresh reviewed account"]
    assert not Path(settings["state_root"]).exists()
    assert not Path(settings["results_root"]).exists()


def test_operator_confirmations_generate_bound_acceptance_review_without_asserting_unrun_measurements(repository_root, tmp_path, monkeypatch, request):
    import cloud_network_benchmark.campaign_inputs as inputs
    from cloud_network_benchmark.admission import _validate_paid_acceptance_evidence

    settings = operator_settings(tmp_path, repository_root, monkeypatch)
    stage = prepare_stage({"stage": "acceptance"}, settings, repository_root,
        Path(settings["state_root"]), Path(settings["results_root"]), Clock())
    register_generated_cleanup(stage, request)
    declarations = {}
    for entry in stage["entries"]:
        if not entry["entry_id"].endswith("FAIL"):
            continue
        attempt = json.loads(Path(entry["parent_manifest_path"]).read_text())["campaign_id"]
        row = {}
        for action in ("server_failure", "controller_loss", "expiry", "cleanup"):
            evidence = tmp_path / f"{entry['entry_id']}-{action}-observed.txt"
            evidence.write_text(f"SYNTHETIC OFFLINE TEST ONLY: {attempt}: {action}")
            row[action] = {"confirmed": True, "source": str(evidence)}
        declarations[entry["entry_id"]] = row
    settings["stage_inputs"]["acceptance"]["failure_checks"] = declarations
    prepared = Path(stage["state_root"]) / stage["stage_id"] / "prepared.json"
    refs = inputs._write_evidence(tmp_path / "generated-review", {
        "paid_acceptance": {"confirmed": True, "source": str(prepared)}},
        inputs.provider_scope(settings["providers"]), settings)
    evidence = json.loads(Path(refs["paid_acceptance"]).read_text())
    review = yaml.safe_load(Path(evidence["source_path"]).read_text())
    assert evidence["source_sha256"] == hashlib.sha256(Path(evidence["source_path"]).read_bytes()).hexdigest()
    assert review["prepared_stage_source"]["sha256"] == hashlib.sha256(prepared.read_bytes()).hexdigest()
    assert len(review["accepted_campaigns"]) == 2
    for entry in stage["entries"][:2]:
        reference = review["accepted_runtime_configs"][entry["parent_manifest_path"]]
        assert reference["path"] == entry["runtime_config_path"]
        assert reference["sha256"] == hashlib.sha256(Path(reference["path"]).read_bytes()).hexdigest()
    _, _, _, _, plan = load_entry(stage, stage["entries"][0], repository_root)
    # Generated wrappers do not turn an initialized fake campaign into actual
    # completed native acceptance, even with operator failure confirmations.
    with pytest.raises(ValueError, match="completed|success"):
        _validate_paid_acceptance_evidence(evidence, plan.resolved_campaign)
