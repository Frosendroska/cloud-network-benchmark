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
        "results_root": str(tmp_path / "results"), "minimum_free_bytes": 4096,
        "ledger": {"path": str(tmp_path / "private" / "ledger.json"), "scope_id": "offline-generated-shared"},
        "credential_refresh": {"confirmed": True, "source": str(source),
            "valid_through_utc": (datetime.now(timezone.utc) + timedelta(days=40)).isoformat()},
        "providers": {
            "aws": {**keys, "account_id": "123456789012", "ami_ids": {"eu-central-1": "ami-0123456789abcdef0"}, "public_key_path": str(key)},
            "azure": {**keys, "subscription_id": "11111111-1111-1111-1111-111111111111",
                "tenant_id": "22222222-2222-2222-2222-222222222222", "selected_image": "Canonical:ubuntu-24_04-lts:server:24.04.202610010",
                "ssh_public_key_path": str(key), "poll_seconds": 1, "command_timeout_seconds": 30},
            "gcp": {**keys, "project_id": "offline-project", "images": {region: "projects/ubuntu-os-cloud/global/images/ubuntu-2404-lts-amd64-v20261001" for region in ("europe-west3", "europe-west2", "europe-north1")}, "public_key_path": str(key)}},
        "approvals": {name: {"confirmed": True, "source": str(source)} for name in
            ("f10_offline", "paid_authorization", "quota_review", "paid_acceptance", "common_n", "study_window", "calibration_authorized")},
        "pricing": {"reviewed": True, "id": "offline-generated-test-prices", "source": str(source),
            "checked_on": datetime.now(timezone.utc).date().isoformat(),
            "funding": {"primary": "100", "replacements": "0", "tax": "20", "uncertainty": "25"},
            "expiry": {"aws": {"automation_step_price": ".001", "script_second_price": ".00001"},
                "azure": {"automation_rate_per_job_minute": ".001", "job_minutes_bound": "5"},
                "gcp": {"zero_support_rationale": "synthetic fixture no cost"}}, "components": {},
            "result_transfer": {}},
        "stage_inputs": {}}
    for category in ("acceptance", "calibration", "day", "s1", "s3"):
        settings["pricing"]["components"][category] = {provider: {name: {
            "amount_usd": ".01", "quantity": "1", "unit": "GB-egress" if name == "traffic_export" else "test-unit", "inclusion_rule": "synthetic offline fixture",
            "funding_bucket": "primary"} for name in COST_COMPONENTS} for provider in ("aws", "azure", "gcp")}
        settings["pricing"]["result_transfer"][category] = {provider: {
            "payload_limit_bytes": 1000, "protocol_overhead_allowance_bytes": 1000,
            "protocol_overhead_priced_usd": ".001"} for provider in ("aws", "azure", "gcp")}
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
    settings["approvals"]["weekly_concurrency"] = {"confirmed": True, "source": str(source),
        "fields": {"mode": "paired_s1_s3", "max_parallel_entries": 2}}
    settings["approvals"]["paired_acceptance"] = {"confirmed": True, "source": str(source),
        "fields": {"mode": "paired_acceptance_s1_s3", "max_parallel_entries": 2,
            "start_utc": f"{tomorrow}T06:00:00+00:00"}}
    for stage, measurement in (("acceptance", 76), ("week", 226), ("calibration", 1180)):
        settings["stage_inputs"][stage] = {"first_day_utc": tomorrow,
            "start_utc": f"{tomorrow}T06:00:00+00:00",
            "windows_utc": {"night": "00:00:00", "morning": "06:00:00", "afternoon": "12:00:00", "evening": "18:00:00"},
            "allowance": {"registration_seconds": 10, "provision_seconds": 60, "readiness_seconds": 10,
                "stabilization_seconds": 60, "measurement_seconds": measurement, "measurement_shutdown_seconds": 1,
                "retrieval_seconds": 20, "cleanup_seconds": 20, "source_id": "synthetic-measured-fixture",
                "provider_leads_seconds": {"aws": 360, "azure": 60, "gcp": 60}}}
    settings["approvals"]["size_export_review"] = {"confirmed": True, "source": str(source),
        "fields": {"measured_run_raw_bytes": 1, "measured_run_seconds": 180,
            "expected_raw_bytes": 2000, "expected_archive_bytes": 100,
            "max_guest_raw_bytes": 1, "measured_guest_free_bytes": 1000,
            "guest_headroom_bytes": 1, "destination_headroom_bytes": 1,
            "archive_manifest_sha256": "a" * 64, "archive_checksum_verified": True,
            "expected_archive_part_count": 1}}
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


def test_generated_acceptance_does_not_manufacture_financial_authority(repository_root, tmp_path, monkeypatch, request):
    settings = operator_settings(tmp_path, repository_root, monkeypatch)
    clock = Clock()
    stage = prepare_stage({"stage": "acceptance"}, settings, repository_root,
        Path(settings["state_root"]), Path(settings["results_root"]), clock)
    register_generated_cleanup(stage, request)
    assert stage["observer_bindings_path"]
    assert stage["observer_launch_receipt_root"]
    book = BudgetLedger(Path(stage["ledger_path"]), scope_id=stage["scope_id"],
        utc_now=clock.utc_now, monotonic_now=clock.monotonic, controller_session_id=stage["controller_session_id"])
    book.initialize(load_cost_inputs(Path(stage["entries"][0]["cost_inputs_path"])))
    namespaces = []
    for entry in stage["entries"]:
        runtime, parent, children, request, plan = load_entry(stage, entry, repository_root)
        assert runtime["observer"]["stage_id"] == stage["stage_id"]
        assert runtime["observer"]["entry_id"] == entry["entry_id"]
        assert runtime["observer"]["workspace"] == runtime["access"]["bootstrap"].workspace
        namespaces.extend(plan.namespaces.values())
        decision = book.reserve(request, plan=plan)
        assert decision.status == "rejected"
        assert decision.reasons == ("financial_authorization_invalid",)
        assert len(children) == len(entry["run_ids"])
        assert all(child.mode == "acceptance" for child in children.values())
        assert all(Path(row["bootstrap_template"]).is_file() for row in runtime["providers"].values())
    assert len(book.inspect()["reservations"]) == 0
    assert len(namespaces) == len(set(namespaces)) == 9


@pytest.mark.parametrize("fail_recovery_update", [False, True])
def test_campaign_consumer_reconciles_terminal_factory_failure_without_resuming_entry(
        fail_recovery_update,
        repository_root, tmp_path, monkeypatch, request):
    """Blocked timing closes only when every recovery journal update succeeded."""
    from contextlib import redirect_stderr, redirect_stdout
    from io import StringIO
    from types import SimpleNamespace
    from cloud_network_benchmark.campaign import CampaignDriver, _write_json
    from cloud_network_benchmark.orchestration import SavedLifecycleRecovery, SavedProviderRecovery

    settings = operator_settings(tmp_path, repository_root, monkeypatch)
    stage = prepare_stage({"stage": "acceptance"}, settings, repository_root,
        Path(settings["state_root"]), Path(settings["results_root"]), Clock())
    register_generated_cleanup(stage, request)
    assert [row["entry_id"] for row in stage["entries"][:2]] == ["V-S1", "V-S3"]
    entry = stage["entries"][0]
    recovery_path = Path(entry["private_root"]) / "lifecycle-recovery.json"
    recovery_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    recovery = SavedLifecycleRecovery(schema_version=1, attempt_id="synthetic-factory-failure",
        controller_session_id=stage["controller_session_id"], providers=[
            SavedProviderRecovery(provider=provider, deployment_id=f"{run_id}-deployment",
                journal_reference=str(recovery_path.parent / provider / "attempt.json"),
                expiry_registered=False, creation_may_have_started=False, cleanup_open=True,
                cleanup_timing_status=("late" if provider == "aws" else
                    "unknown" if provider == "azure" else None))
            for provider, run_id in entry["run_ids"].items()])
    recovery_path.write_text(recovery.model_dump_json(indent=2) + "\n")
    recovery_path.chmod(0o600)
    if fail_recovery_update:
        import cloud_network_benchmark.orchestration as orchestration_module
        original_update = orchestration_module.update_provider_recovery
        def fail_close_update(path, provider, stage, status, **kwargs):
            if provider == "aws" and stage == "cleanup" and status == "succeeded":
                raise OSError("synthetic recovery journal write failure")
            return original_update(path, provider, stage, status, **kwargs)
        monkeypatch.setattr(orchestration_module, "update_provider_recovery", fail_close_update)
    state_root = Path(stage["state_root"])
    journal_path = state_root / stage["stage_id"] / "campaign-journal.json"
    synthetic_cleanup_hook_calls = []
    def ordinary_cleanup_cli(command, *, cwd, stdout, stderr, check):
        assert command[1:3] == ["lifecycle", "cleanup"]
        from cloud_network_benchmark.lifecycle_cli import main
        actions = {provider: (lambda _record: synthetic_cleanup_hook_calls.append(provider) or {})
            for provider in entry["run_ids"]}
        output = StringIO()
        with redirect_stdout(output):
            with redirect_stderr(output):
                code = main(command[2:], cleanup_actions=actions,
                    reconcile=lambda _record, _result: {"absence_proved": True,
                        "billable_resources_present": False, "cleanup_open": False})
        stdout.write(output.getvalue().encode())
        return SimpleNamespace(returncode=code)
    import cloud_network_benchmark.campaign as campaign_module
    monkeypatch.setattr(campaign_module.subprocess, "run", ordinary_cleanup_cli)
    driver = CampaignDriver(clock=Clock(), reconcile=CampaignDriver._reconcile_lifecycle,
        launch=lambda *_: pytest.fail("terminal failed entry must never resume"))
    journal = driver._load_or_create_journal(stage, journal_path)
    journal["entries"]["V-S1"].update(state="failed", finished_at=Clock().utc_now().isoformat(),
        error="provider factory failed before initialization completed",
        result={"cleanup_open": True, "execution": "not_resumed"})
    _write_json(journal_path, journal)
    monkeypatch.setattr(CampaignDriver, "_archive_closed_entry", staticmethod(lambda *_: None))

    outcome = driver.run(stage)
    saved = json.loads(journal_path.read_text())
    assert saved["entries"]["V-S1"]["state"] == "failed"
    assert saved["entries"]["V-S1"]["result"]["execution"] == "not_resumed"
    cleanup_result = saved["entries"]["V-S1"]["result"]
    if fail_recovery_update:
        assert cleanup_result["cleanup_open"] is True
        from cloud_network_benchmark.campaign import _last_json_object
        failed_cli_report = _last_json_object(state_root / stage["stage_id"] / "V-S1-recovery.log")
        assert failed_cli_report["status"] == "blocked"
        assert failed_cli_report["providers"]["aws"]["absence_proved"] is True
        assert failed_cli_report["providers"]["aws"]["reconciled"] is False
        assert failed_cli_report["providers"]["aws"]["errors"]
    else:
        assert cleanup_result["cleanup_report"]["status"] == "blocked"
        assert cleanup_result["cleanup_open"] is False
        assert cleanup_result["cleanup_acceptance_failed"] is True
        assert all(not result["cleanup_open"] and result["absence_proved"]
            and result["reconciled"] and not result["errors"]
            for result in cleanup_result["cleanup_report"]["providers"].values())
    assert saved["entries"]["V-S3"]["state"] == "missed"
    assert outcome["status"] == "awaiting_operator"
    assert len(synthetic_cleanup_hook_calls) == 3  # In-process test hooks; no provider SDK/CLI.


def test_production_ready_acceptance_stage_composes_with_driver_pair_validation(
        repository_root, tmp_path, monkeypatch, request):
    """Exercise real stage generation and driver validation for the frozen all-ready pair."""
    from cloud_network_benchmark.campaign import _acceptance_pair
    from cloud_network_benchmark.validation_profiles import ACCEPTANCE_PROFILE, FAILURE_PROFILE

    settings = operator_settings(tmp_path, repository_root, monkeypatch)
    lower = (datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=2)).isoformat()
    upper = (datetime.fromisoformat(lower) + timedelta(hours=5)).isoformat()
    window = {"not_before_utc": lower, "not_after_utc": upper}
    acceptance = settings["stage_inputs"]["acceptance"]
    acceptance.update(workflow_profile=ACCEPTANCE_PROFILE,
        failure_workflow_profile=FAILURE_PROFILE, execution_window_utc=window)
    acceptance.pop("allowance", None)
    settings["pricing"]["workflow_profiles"] = [ACCEPTANCE_PROFILE, FAILURE_PROFILE]
    settings["approvals"]["paired_acceptance"]["fields"] = {
        "mode": "paired_acceptance_s1_s3", "max_parallel_entries": 2,
        "execution_window_utc": window}

    stage = prepare_stage({"stage": "acceptance"}, settings, repository_root,
        Path(settings["state_root"]), Path(settings["results_root"]), Clock())
    register_generated_cleanup(stage, request)

    pair = stage["entries"][:2]
    assert [row["entry_id"] for row in pair] == ["V-S1", "V-S3"]
    assert all(row["scheduled_start"] is None for row in pair)
    assert {row["launch_at"] for row in pair} == {lower}
    assert stage["dispatch_policy"]["execution_window"] == window
    assert stage["dispatch_policy"]["ready_window_seconds"] == 600
    assert stage["measurement_start_barrier"]["execution_window"] == window
    assert stage["measurement_start_barrier"]["ready_window_seconds"] == 600
    expected = {(entry["entry_id"], provider, run_id)
        for entry in pair for provider, run_id in entry["run_ids"].items()}
    observed = {(row["entry_id"], row["provider"], row["run_id"])
        for row in stage["measurement_start_barrier"]["expected_participants"]}
    assert len(expected) == 6 and observed == expected
    assert _acceptance_pair(stage) == pair

    from cloud_network_benchmark.lifecycle_remote import build_provider_remote_stages
    for entry in pair:
        runtime, parent, children, _request, _plan = load_entry(stage, entry, repository_root)
        campaign, _ = resolve_campaign(Path(entry["config_path"]), repository_root)
        assert runtime["measurement_start_barrier"]["start_policy"] == "all_ready"
        for provider in children:
            stages = build_provider_remote_stages(provider=provider, campaign=campaign,
                parent=parent, children=children, runtime=runtime, ledger=None,
                reservation=None, runner=None, clock=Clock(), repository_root=repository_root,
                recovery_path=tmp_path / f"{entry['entry_id']}-{provider}-recovery.json")
            assert callable(stages.measure)


def test_operator_recovery_uses_entry_parent_campaign_not_stage_aggregation_id(
        repository_root, tmp_path, monkeypatch, request):
    """Production stage output binds recovery through the entry's real parent/child IDs."""
    from types import SimpleNamespace
    from cloud_network_benchmark import operator_recovery

    settings = operator_settings(tmp_path, repository_root, monkeypatch)
    stage = prepare_stage({"stage": "acceptance"}, settings, repository_root,
        Path(settings["state_root"]), Path(settings["results_root"]), Clock())
    register_generated_cleanup(stage, request)
    entry = next(row for row in stage["entries"] if row["entry_id"] == "V-A-FAIL")
    parent_manifest = json.loads(Path(entry["parent_manifest_path"]).read_text())
    _, children = load_initialized_campaign(Path(entry["parent_manifest_path"]),
        repository_root=repository_root, config_path=Path(entry["config_path"]))
    deployment = children["aws"]
    provider_record = SimpleNamespace(provider="aws", deployment_id=deployment.deployment_id,
        expiry_registered=True, cleanup_open=True, cleanup_settings_reference=None,
        journal_reference=None)

    assert stage["campaign_id"] == stage["stage_id"]
    assert parent_manifest["campaign_id"] != stage["campaign_id"]
    assert entry["run_ids"]["aws"] == deployment.run_id
    operator_recovery._validate_campaign_identity(entry=entry,
        recovery=SimpleNamespace(attempt_id=parent_manifest["campaign_id"]),
        provider_record=provider_record, parent_manifest=parent_manifest,
        saved_deployment=deployment)
    with pytest.raises(ValueError, match="parent manifest campaign"):
        operator_recovery._validate_campaign_identity(entry=entry,
            recovery=SimpleNamespace(attempt_id=stage["campaign_id"]),
            provider_record=provider_record, parent_manifest=parent_manifest,
            saved_deployment=deployment)

    stage_dir = Path(stage["state_root"]) / stage["stage_id"]
    prepared_path = stage_dir / "prepared-stage.json"
    prepared_path.write_text(json.dumps(stage) + "\n")
    prepared_path.chmod(0o600)
    journal_path = stage_dir / "campaign-journal.json"
    journal_path.write_text(json.dumps({"entries": {
        row["entry_id"]: {"state": "running" if row is entry else "scheduled"}
        for row in stage["entries"]}}) + "\n")
    journal_path.chmod(0o600)
    private_root = Path(entry["private_root"])
    private_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    recovery_path = private_root / "lifecycle-recovery.json"
    provider_journal = private_root / "provider-journal.json"
    cleanup_settings = private_root / "cleanup-settings.json"
    ledger_path = Path(stage["ledger_path"])
    for path in (provider_journal, cleanup_settings, ledger_path):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text("{}\n")
        path.chmod(0o600)
    provider_record.cleanup_settings_reference = str(cleanup_settings)
    provider_record.journal_reference = str(provider_journal)
    recovery_path.write_text(json.dumps({"schema_version": 1,
        "attempt_id": parent_manifest["campaign_id"],
        "controller_session_id": stage["controller_session_id"],
        "ledger_path": str(ledger_path), "providers": [{
            "provider": "aws", "deployment_id": deployment.deployment_id,
            "journal_reference": str(provider_journal), "expiry_registered": True,
            "creation_may_have_started": True, "cleanup_open": True,
            "cleanup_settings_reference": str(cleanup_settings)}]}) + "\n")
    recovery_path.chmod(0o600)
    saved = SimpleNamespace(deployment=SimpleNamespace(provider="aws",
        campaign_id=deployment.campaign_id, run_id=deployment.run_id,
        deployment_id=deployment.deployment_id))
    from cloud_network_benchmark import lifecycle_cleanup
    monkeypatch.setattr(lifecycle_cleanup, "load_cleanup_settings", lambda _: saved)
    monkeypatch.setattr(operator_recovery, "original_expiry_window", lambda _: {
        "provider": "aws", "registration_id": "native-registration",
        "target_selector": "exact-vm-pair", "action": "stop",
        "expected_state": "stopped", "expiry_at": "2026-11-23T00:00:00+00:00",
        "latest_safe_cleanup_start": "2026-11-23T01:00:00+00:00",
        "cleanup_seconds": 660, "cap_at": "2026-11-23T01:11:00+00:00",
        "binding_sha256": "a" * 64})
    binding = operator_recovery.bind_failure_attempt(prepared_stage=prepared_path,
        campaign_journal=journal_path, recovery=recovery_path,
        runtime_config=Path(entry["runtime_config_path"]), entry_id="V-A-FAIL")
    assert binding["attempt_id"] == parent_manifest["campaign_id"]
    assert binding["attempt_id"] != stage["campaign_id"]
    assert binding["server_fault_unit"] == f"cnb-{deployment.deployment_id}-iperf3.service"
    published = json.loads(prepared_path.read_text())
    published["observer_bindings_sha256"] = "b" * 64
    prepared_path.write_text(json.dumps(published) + "\n")
    prepared_path.chmod(0o600)
    operator_recovery.revalidate_before_freeze(binding)
    published["status"] = "MUTATED_AUTHORITY"
    prepared_path.write_text(json.dumps(published) + "\n")
    prepared_path.chmod(0o600)
    with pytest.raises(ValueError, match="immutable authority changed"):
        operator_recovery.revalidate_before_freeze(binding)
    published["status"] = stage["status"]
    prepared_path.write_text(json.dumps(published) + "\n")
    prepared_path.chmod(0o600)
    cleanup_settings.write_text('{"changed":true}\n')
    cleanup_settings.chmod(0o600)
    with pytest.raises(ValueError, match="input changed before freeze"):
        operator_recovery.revalidate_before_freeze(binding)


def test_fixed_acceptance_draft_prices_the_northern_failure_configs(
        repository_root, tmp_path, monkeypatch, request):
    """Production drafting keeps plan overrides aligned with priced config identities."""
    from cloud_network_benchmark.validation_profiles import (
        ACCEPTANCE_PROFILE, CALIBRATION_PROFILE, FAILURE_PROFILE,
    )

    settings = operator_settings(tmp_path, repository_root, monkeypatch)
    settings["stage_inputs"]["acceptance"].update(
        workflow_profile=ACCEPTANCE_PROFILE, failure_workflow_profile=FAILURE_PROFILE)
    lower = (datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=2)).isoformat()
    upper = (datetime.fromisoformat(lower) + timedelta(hours=5)).isoformat()
    settings["stage_inputs"]["acceptance"]["execution_window_utc"] = {
        "not_before_utc": lower, "not_after_utc": upper}
    settings["stage_inputs"]["calibration"].update(workflow_profile=CALIBRATION_PROFILE)
    settings["stage_inputs"]["calibration"]["execution_window_utc"] = {
        "not_before_utc": lower, "not_after_utc": upper}
    settings["stage_inputs"]["acceptance"].pop("allowance", None)
    settings["stage_inputs"]["calibration"].pop("allowance", None)
    settings["pricing"]["workflow_profiles"] = [
        ACCEPTANCE_PROFILE, FAILURE_PROFILE, CALIBRATION_PROFILE]
    plan = {"stage": "acceptance", "configs": {
        "aws_failure": "configs/tests/exp-900-single-provider-north.yaml",
        "azure_failure": "configs/tests/azure-901-single-provider-north.yaml",
        "gcp_failure": "configs/tests/exp-902-gcp-single-provider-north.yaml",
    }}

    stage = prepare_stage(plan, settings, repository_root, Path(settings["state_root"]),
        Path(settings["results_root"]), Clock(), draft=True)
    calibration = prepare_stage({"stage": "calibration"}, settings, repository_root,
        Path(settings["state_root"]), Path(settings["results_root"]), Clock(), draft=True)

    assert stage["status"] == "DRAFT_UNAPPROVED"
    assert len(stage["entries"]) == 5
    assert calibration["status"] == "DRAFT_UNAPPROVED"
    assert len(stage["entries"]) + len(calibration["entries"]) == 6
    assert sum(len(row["run_ids"]) for row in stage["entries"] + calibration["entries"]) == 12
    assert all(not Path(path).exists() for path in calibration["approval_refs"].values())
    failures = {row["config_alias"]: yaml.safe_load(Path(row["config_path"]).read_text())
        for row in stage["entries"] if row["entry_id"].endswith("FAIL")}
    expected = {
        "aws_failure": ("exp-900-north-acceptance", "eu-north-1"),
        "azure_failure": ("exp-901-north-acceptance", "swedencentral"),
        "gcp_failure": ("exp-902-north-acceptance", "europe-north1"),
    }
    for alias, (config_id, region) in expected.items():
        assert failures[alias]["config_id"] == config_id
        assert region in failures[alias]["provider_configs"][
            failures[alias]["selected_providers"][0]]["regions"].values()
    prices = json.loads(Path(stage["cost_input_path"]).read_text())["provider_run_envelope_templates"]
    for alias, (config_id, region) in expected.items():
        provider = {"aws_failure": "aws", "azure_failure": "azure", "gcp_failure": "gcp"}[alias]
        assert region in prices[config_id][provider]["regions"]
    assert {yaml.safe_load(Path(row["config_path"]).read_text())["admission"]["lifetime_cap_seconds"]
        for row in stage["entries"] if row["entry_id"] in {"V-S1", "V-S3"}} == {4200}
    assert {yaml.safe_load(Path(row["config_path"]).read_text())["admission"]["lifetime_cap_seconds"]
        for row in stage["entries"] if row["entry_id"].endswith("FAIL")} == {3900}
    assert yaml.safe_load(Path(calibration["entries"][0]["config_path"]).read_text())[
        "admission"]["lifetime_cap_seconds"] == 5100
    assert all(row["requires_supervision"] for row in stage["entries"] if row["entry_id"].endswith("FAIL"))
    assert all(not Path(path).exists() for path in stage["approval_refs"].values())

    # The unapproved six-entry acceptance + calibration batch must write the
    # same per-provider rounded estimate that lifecycle request construction
    # and F11 reservation validation use. Check every provider envelope and
    # cumulative funding against the generated source without activating it.
    from decimal import Decimal, ROUND_CEILING
    from cloud_network_benchmark.admission import rounded_provider_cost_total

    all_entries = stage["entries"] + calibration["entries"]
    source_payload = json.loads(Path(stage["cost_input_path"]).read_text())
    templates = source_payload["provider_run_envelope_templates"]
    cumulative_by_bucket = {}
    total_provider_liability = Decimal("0")
    for entry in all_entries:
        config = yaml.safe_load(Path(entry["config_path"]).read_text())
        priced = templates[config["config_id"]]
        selected_rows = {provider: priced[provider] for provider in config["selected_providers"]}
        estimate = rounded_provider_cost_total(selected_rows)
        assert Decimal(str(config["admission"]["estimated_cost_usd"]["value"])) == estimate
        for provider, row in selected_rows.items():
            provider_total = rounded_provider_cost_total({provider: row})
            total_provider_liability += provider_total
            for component in row["components"].values():
                bucket = component["funding_bucket"]
                cumulative_by_bucket[bucket] = cumulative_by_bucket.get(bucket, Decimal("0")) + Decimal(
                    str(component["amount_usd"]))
    for bucket, amount in cumulative_by_bucket.items():
        reserved = amount.quantize(Decimal("0.0001"), rounding=ROUND_CEILING)
        assert reserved <= Decimal(str(source_payload["funding"][bucket]))
    assert len(all_entries) == 6
    assert sum(len(entry["run_ids"]) for entry in all_entries) == 12
    assert total_provider_liability > 0



@pytest.mark.parametrize("case", ["V-S1", "V-S3", "V-AWS-FAIL", "V-AZURE-FAIL", "V-GCP-FAIL", "CAL-S1"])
def test_source_profile_allowance_reaches_materialized_azure_factory(
        repository_root, tmp_path, monkeypatch, request, case):
    """Materialize actual Azure hooks from generated normal, failure, and CAL runtimes."""
    import json
    from cloud_network_benchmark.admission import load_cost_inputs
    from cloud_network_benchmark.campaign_inputs import prepare_stage
    from cloud_network_benchmark.contracts.admission import ReservationEvidence
    from cloud_network_benchmark.contracts.common import Evidence, Provider, StringEvidence, StructuredEvidence
    from cloud_network_benchmark.lifecycle_adapters import RemoteStages
    from cloud_network_benchmark.lifecycle_runtime import build_provider_workflows
    from cloud_network_benchmark.validation_profiles import PROFILE_SPECS
    import cloud_network_benchmark.lifecycle_adapters as adapters
    import cloud_network_benchmark.azure_provider as azure_provider

    settings = operator_settings(tmp_path, repository_root, monkeypatch)
    lower = (datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=2)).isoformat()
    window = {"not_before_utc": lower,
        "not_after_utc": (datetime.fromisoformat(lower) + timedelta(hours=5)).isoformat()}
    settings["stage_inputs"]["acceptance"].update(workflow_profile="all_cloud_acceptance_v1",
        failure_workflow_profile="all_cloud_failure_acceptance_v1", execution_window_utc=window)
    settings["stage_inputs"]["acceptance"].pop("allowance", None)
    settings["stage_inputs"]["calibration"].update(workflow_profile="all_cloud_calibration_v1",
        execution_window_utc=window)
    settings["stage_inputs"]["calibration"].pop("allowance", None)
    settings["pricing"]["workflow_profiles"] = ["all_cloud_acceptance_v1",
        "all_cloud_failure_acceptance_v1", "all_cloud_calibration_v1"]
    plan = {"stage": "acceptance", "configs": {
        "aws_failure": "configs/tests/exp-900-single-provider-north.yaml",
        "azure_failure": "configs/tests/azure-901-single-provider-north.yaml",
        "gcp_failure": "configs/tests/exp-902-gcp-single-provider-north.yaml"}}
    acceptance = prepare_stage(plan, settings, repository_root,
        Path(settings["state_root"]), Path(settings["results_root"]), Clock(), draft=True)
    calibration = prepare_stage({"stage": "calibration"}, settings, repository_root,
        Path(settings["state_root"]), Path(settings["results_root"]), Clock(), draft=True)
    register_generated_cleanup(acceptance, request)
    register_generated_cleanup(calibration, request)
    entries = {row["entry_id"]: (row, acceptance) for row in acceptance["entries"]}
    entries.update({row["entry_id"]: (row, calibration) for row in calibration["entries"]})
    for alias, provider in (("aws_failure", "AWS"), ("azure_failure", "AZURE"), ("gcp_failure", "GCP")):
        failure_entry = next(row for row in acceptance["entries"] if row["config_alias"] == alias)
        entries[f"V-{provider}-FAIL"] = (failure_entry, acceptance)
    entry, stage = entries[case]
    runtime = load_runtime_config(Path(entry["runtime_config_path"]),
        allow_missing_approval_evidence=True)
    parent, all_children = load_initialized_campaign(Path(entry["parent_manifest_path"]),
        repository_root=repository_root, config_path=Path(entry["config_path"]))
    campaign, _ = resolve_campaign(Path(entry["config_path"]), repository_root)
    costs = load_cost_inputs(Path(entry["cost_inputs_path"]))
    profile = runtime["workflow_profile"]
    spec = PROFILE_SPECS[profile]
    if "azure" in runtime["providers"]:
        public_key = tmp_path / f"{case}-azure.pub"
        public_key.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE9F4huSqss0wx3n6f5+WJiEpePM3oXTbuOzBLRR5NCc test\n")
        runtime["providers"]["azure"]["options"]["ssh_public_key_path"] = public_key
        expiry_cost = json.loads(runtime["providers"]["azure"]["sources"]["expiry_cost_path"].read_text())
        expiry_cost["job_minutes_bound"] = 20
        expiry_cost_path = tmp_path / f"{case}-azure-expiry-cost.json"
        expiry_cost_path.write_text(json.dumps(expiry_cost))
        runtime["providers"]["azure"]["sources"]["expiry_cost_path"] = expiry_cost_path

    # Materialize the actual selected provider set from each generated source
    # entry. This yields 12 hooksets across paired S1/S3, three single-provider
    # failure checks and CAL, without invoking any provider command.
    children = all_children
    start = datetime.now(timezone.utc).replace(microsecond=0)
    cap = spec["cap_seconds"]
    reservation_id = f"factory-{case.lower()}-reservation"
    reservation = ReservationEvidence(schema_version=2, campaign_id=parent.campaign_id,
        workflow_profile=profile, reservation_id=Evidence(value=reservation_id), state="reserved",
        reserved_cost_usd=Evidence(value=100.0), cap_started_at=Evidence(value=start),
        expires_at=Evidence(value=start + timedelta(seconds=cap)),
        cap_start_policy_id=StringEvidence(value="synthetic-fixed-profile-cap"),
        source_path=costs.source_path, source_sha256=costs.payload_sha256)
    class FakeLedger:
        scope_id = stage["scope_id"]
        controller_session_id = stage["controller_session_id"]
        def inspect(self):
            return {"reservations": {reservation_id: {"session": self.controller_session_id,
                "cap_start_monotonic": 1000.0, "cap_seconds": cap}}}
    class FakeClock:
        def utc_now(self): return start
        def monotonic(self): return 1000.0
    clock = FakeClock()
    captured = {}
    original_azure_hooks = adapters.azure_hooks
    def capture_hooks(deployment, expiry_request, **kwargs):
        captured.update(deployment=deployment, request=expiry_request, options=kwargs["options"])
        return original_azure_hooks(deployment, expiry_request, **kwargs)
    monkeypatch.setattr(adapters, "azure_hooks", capture_hooks)
    stage_hooks = {provider: RemoteStages(prepare=lambda *_: None, stabilize=lambda *_: None,
        measure=lambda *_: None, close=lambda *_: None, retrieve=lambda *_: None,
        claim=lambda *_: None) for provider in children}
    cleanup_references = {}
    private_root = tmp_path / case / "private"
    private_root.mkdir(parents=True)
    workflows = build_provider_workflows(campaign=campaign, parent=parent, children=children,
        costs=costs, runtime=runtime, reservation=reservation, ledger=FakeLedger(), runner=object(),
        clock=clock, stages=stage_hooks, private_root=private_root,
        cleanup_references=cleanup_references)
    assert set(workflows) == set(children)
    assert all(callable(hooks.protect) and callable(hooks.create) for hooks in workflows.values())
    source = spec["allowance"]
    expected = {key: float(source[key]) for key in (
        "registration_seconds", "provision_seconds", "readiness_seconds", "stabilization_seconds",
        "measurement_seconds", "measurement_shutdown_seconds", "retrieval_seconds", "cleanup_seconds")}
    expected["azure_provider_lead_seconds"] = float(source["provider_leads_seconds"]["azure"])
    assert set(cleanup_references) == set(children)
    assert all(Path(path).is_file() for path in cleanup_references.values())
    if "azure" in children:
        assert captured["options"].profile_allowance == expected
        saved_settings = Path(cleanup_references["azure"])
        saved_value = json.loads(saved_settings.read_text())
        assert saved_value["settings"]["profile_allowance"] == expected
        missing_allowance = captured["options"].model_copy(update={"profile_allowance": None})
        with pytest.raises(ValueError, match="exact registered allowance vector"):
            azure_provider._validate_request(captured["deployment"], missing_allowance,
                start, None, check_live_clock=False)

    deadlines = {}
    def capture_registration(_deployment, _expiry_request, options, *_args, **_kwargs):
        deadlines["registration"] = options.workflow_stage_deadline_monotonic
        raise RuntimeError("offline cutoff capture")
    monkeypatch.setattr(azure_provider, "_register_expiry_impl", capture_registration)
    if "azure" in children:
        with pytest.raises(ValueError, match="reservation/deadline has expired"):
            azure_provider.register_expiry(captured["deployment"], captured["request"],
                captured["options"], object(), clock=lambda: 1000.0 + cap,
                utc_now=lambda: start + timedelta(seconds=cap))
        assert deadlines == {}
        assert workflows["azure"].protect().state == "failed"
        assert deadlines["registration"] == 1000.0 + source["registration_seconds"]

    if "azure" not in children:
        return
    stop_at = captured["request"].expires_at.value
    azure_provider._bind_profile_stop(captured["deployment"], captured["options"], stop_at)
    expiry = azure_provider.ExpiryEvidence(schema_version=2,
        run_id=captured["deployment"].run_id, deployment_id=captured["deployment"].deployment_id,
        provider=Provider.AZURE, action="deallocate", state="registered",
        registration_id=Evidence(value=f"schedule-{case}"), registered_at=Evidence(value=start),
        expires_at=Evidence(value=stop_at), target_selector=captured["request"].target_selector,
        provider_metadata=StructuredEvidence(value={"reservation_id": reservation_id,
            "source_sha256": reservation.source_sha256}))
    provision_deployment = captured["deployment"].model_copy(update={"expiry": Evidence(value=expiry)})
    provision_deadlines = []
    def capture_first_provider_command(_root, options, *_args, **_kwargs):
        provision_deadlines.append(options.workflow_stage_deadline_monotonic)
        raise RuntimeError("offline provision cutoff capture")
    monkeypatch.setattr(azure_provider, "_tf", capture_first_provider_command)
    azure_provider.provision(provision_deployment, captured["options"], object(),
        clock=clock.monotonic, utc_now=clock.utc_now)
    stop_cap = 1000.0 + (stop_at - start).total_seconds()
    expected_provision = min(1000.0 + source["registration_seconds"] + source["provision_seconds"],
        1000.0 + float(provision_deployment.provisioning_timeout_seconds),
        1000.0 + cap, stop_cap)
    assert provision_deadlines == [expected_provision]
    assert expected_provision > 1000.0



def test_generated_calibration_binds_runtime_observer_without_changing_workload(repository_root, tmp_path, monkeypatch, request):
    settings = operator_settings(tmp_path, repository_root, monkeypatch)
    clock = Clock()
    stage = prepare_stage({"stage": "calibration"}, settings, repository_root,
        Path(settings["state_root"]), Path(settings["results_root"]), clock)
    register_generated_cleanup(stage, request)
    assert stage["observer_bindings_path"]
    assert stage["observer_launch_receipt_root"]
    assert len(stage["entries"]) == 1
    runtime = load_runtime_config(Path(stage["entries"][0]["runtime_config_path"]))
    assert runtime["observer"]["stage_id"] == stage["stage_id"]
    assert runtime["observer"]["entry_id"] == "CAL-S1"
    assert runtime["observer"]["workspace"] == runtime["access"]["bootstrap"].workspace
    _parent, children = load_initialized_campaign(Path(stage["entries"][0]["parent_manifest_path"]),
        repository_root=repository_root, config_path=Path(stage["entries"][0]["config_path"]))
    assert len(children) == 3
    assert all(child.mode == "calibration" for child in children.values())
    runtime, parent, children, reservation_request, admission_plan = load_entry(
        stage, stage["entries"][0], repository_root)
    ledger = BudgetLedger(Path(stage["ledger_path"]), scope_id=stage["scope_id"],
        utc_now=clock.utc_now, monotonic_now=clock.monotonic,
        controller_session_id=stage["controller_session_id"])
    ledger.initialize(load_cost_inputs(Path(stage["entries"][0]["cost_inputs_path"])))
    decision = ledger.reserve(reservation_request, plan=admission_plan)
    assert decision.status == "rejected"
    assert decision.reasons == ("financial_authorization_invalid",)


def test_ready_triggered_calibration_runtime_binds_all_factory_stages(repository_root, tmp_path, monkeypatch, request):
    from cloud_network_benchmark.lifecycle_remote import build_provider_remote_stages
    from cloud_network_benchmark.validation_profiles import CALIBRATION_PROFILE

    settings = operator_settings(tmp_path, repository_root, monkeypatch)
    lower = (datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=2)).isoformat()
    window = {"not_before_utc": lower,
        "not_after_utc": (datetime.fromisoformat(lower) + timedelta(hours=5)).isoformat()}
    settings["stage_inputs"]["calibration"].update(workflow_profile=CALIBRATION_PROFILE,
        execution_window_utc=window)
    settings["stage_inputs"]["calibration"].pop("allowance", None)
    settings["pricing"]["workflow_profiles"] = [CALIBRATION_PROFILE]
    stage = prepare_stage({"stage": "calibration"}, settings, repository_root,
        Path(settings["state_root"]), Path(settings["results_root"]), Clock(), draft=True)
    register_generated_cleanup(stage, request)
    entry = stage["entries"][0]
    runtime = load_runtime_config(Path(entry["runtime_config_path"]),
        allow_missing_approval_evidence=True)
    parent, children = load_initialized_campaign(Path(entry["parent_manifest_path"]),
        repository_root=repository_root, config_path=Path(entry["config_path"]))
    campaign, _ = resolve_campaign(Path(entry["config_path"]), repository_root)
    assert runtime["measurement_start_barrier"]["start_policy"] == "all_ready"
    assert runtime["observer"]["entry_id"] == "CAL-S1"
    for provider in children:
        stages = build_provider_remote_stages(provider=provider, campaign=campaign,
            parent=parent, children=children, runtime=runtime, ledger=None,
            reservation=None, runner=None, clock=Clock(), repository_root=repository_root,
            recovery_path=tmp_path / f"calibration-{provider}-recovery.json",
            archive_sink={"archive_root": str(tmp_path / "calibration-archive")})
        assert callable(stages.measure)


def test_draft_activation_parses_runtime_and_runs_preflight_before_evidence(repository_root, tmp_path, monkeypatch, request):
    from cloud_network_benchmark.campaign_inputs import activate_prepared_stage

    settings = operator_settings(tmp_path, repository_root, monkeypatch)
    stage = prepare_stage({"stage": "acceptance"}, settings, repository_root,
        Path(settings["state_root"]), Path(settings["results_root"]), Clock(), draft=True)
    register_generated_cleanup(stage, request)
    observed = {}

    def preflight(candidate, *, runtime_configs):
        observed["candidate"] = candidate
        observed["runtimes"] = runtime_configs
        for entry in stage["entries"]:
            runtime = runtime_configs[entry["entry_id"]]
            assert runtime["runtime_source"] == {"path": entry["runtime_config_path"],
                "sha256": entry["runtime_config_sha256"]}
            assert runtime["approval_evidence"] == {}
        raise ValueError("synthetic financial preflight rejection")

    with pytest.raises(ValueError, match="synthetic financial preflight rejection"):
        activate_prepared_stage(stage, settings, repository_root, preflight=preflight)
    assert observed["candidate"]["status"] == "READY_FOR_ADMISSION"
    assert set(observed["runtimes"]) == {entry["entry_id"] for entry in stage["entries"]}
    assert all(not Path(path).exists() for path in stage["approval_refs"].values())


def test_real_calibration_activation_without_acceptance_receipt_writes_no_approval_files(
        repository_root, tmp_path, monkeypatch, request):
    from cloud_network_benchmark.campaign_inputs import activate_prepared_stage
    from cloud_network_benchmark.validation_profiles import CALIBRATION_PROFILE

    settings = operator_settings(tmp_path, repository_root, monkeypatch)
    lower = (datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=2)).isoformat()
    window = {"not_before_utc": lower,
        "not_after_utc": (datetime.fromisoformat(lower) + timedelta(hours=5)).isoformat()}
    calibration = settings["stage_inputs"]["calibration"]
    calibration.update(workflow_profile=CALIBRATION_PROFILE, execution_window_utc=window)
    calibration.pop("allowance", None)
    settings["pricing"]["workflow_profiles"] = [CALIBRATION_PROFILE]
    stage = prepare_stage({"stage": "calibration"}, settings, repository_root,
        Path(settings["state_root"]), Path(settings["results_root"]), Clock(), draft=True)
    register_generated_cleanup(stage, request)
    # Remove the fixture's generic review source: a calibration activation must
    # not mistake that placeholder for a completed acceptance receipt.
    Path(settings["approvals"]["paid_acceptance"]["source"]).unlink()

    with pytest.raises(ValueError):
        activate_prepared_stage(stage, settings, repository_root,
            preflight=lambda *_a, **_k: pytest.fail("missing acceptance receipt reached financial preflight"))
    assert stage["status"] == "DRAFT_UNAPPROVED"
    assert all(not Path(path).exists() for path in stage["approval_refs"].values())


def test_generated_week_freezes_the_bounded_source_hashed_pair_policy(repository_root, tmp_path, monkeypatch, request):
    settings = operator_settings(tmp_path, repository_root, monkeypatch)
    stage = prepare_stage({"stage": "week"}, settings, repository_root,
        Path(settings["state_root"]), Path(settings["results_root"]), Clock())
    register_generated_cleanup(stage, request)
    policy = stage["dispatch_policy"]
    evidence_path = Path(policy["evidence_path"])
    evidence = json.loads(evidence_path.read_text())
    assert len(stage["entries"]) == 56
    assert policy["policy_id"] == "weekly-paired-s1-s3-v1"
    assert policy["max_parallel_entries"] == 2
    assert policy["evidence_sha256"] == hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    assert evidence["mode"] == "paired_s1_s3" and evidence["max_parallel_entries"] == 2
    assert evidence["source_sha256"] == hashlib.sha256(Path(evidence["source_path"]).read_bytes()).hexdigest()
    assert {(entry["scenario"], entry["priority"]) for entry in stage["entries"][:2]} == {("S1", "P0"), ("S3", "P1")}
    assert len({entry["config_path"] for entry in stage["entries"]}) == 56
    assert len({entry["parent_manifest_path"] for entry in stage["entries"]}) == 56
    assert len({entry["private_root"] for entry in stage["entries"]}) == 56
    assert len({entry["runtime_config_path"] for entry in stage["entries"]}) == 56
    assert len({run_id for entry in stage["entries"] for run_id in entry["run_ids"].values()}) == 168


def test_archive_sink_runtime_config_loads_through_real_stage_generation(repository_root, tmp_path, monkeypatch, request):
    import json
    settings = operator_settings(tmp_path, repository_root, monkeypatch)
    archive_root, staging_root = tmp_path / "archive", tmp_path / "archive-staging"
    archive_root.mkdir()
    staging_root.mkdir()
    quota_evidence = tmp_path / "quota-evidence.json"
    quota_payload = json.dumps({"measured_free_bytes": 1_000_000}, sort_keys=True).encode()
    quota_evidence.write_bytes(quota_payload)
    settings["archive_sink"] = {"bucket": "valid-bucket", "maximum_staging_bytes": 8192,
        "maximum_archive_bytes": 1_000_000, "minimum_controller_free_bytes": 1024,
        "minimum_guest_free_bytes": 2048, "maximum_capsule_bytes": 65536,
        "controller_quota_free_bytes": 1_000_000,
        "controller_quota_evidence_path": str(quota_evidence),
        "controller_quota_evidence_sha256": hashlib.sha256(quota_payload).hexdigest(),
        "source_credentials_file": str(tmp_path / "synthetic-adc.json"),
        "expected_source_principal": "katyabraun0@gmail.com",
        "expected_project_id": "project-a70f8497-3463-4ef2-9cd",
        "expected_service_account_email": "cnb-archive-halle@project-a70f8497-3463-4ef2-9cd.iam.gserviceaccount.com",
        "restore_path": str(tmp_path / "restore"),
        "archive_root": str(archive_root), "staging_directory": str(staging_root)}

    stage = prepare_stage({"stage": "acceptance"}, settings, repository_root,
        Path(settings["state_root"]), Path(settings["results_root"]), Clock())
    register_generated_cleanup(stage, request)
    entry = stage["entries"][0]
    runtime = load_runtime_config(Path(entry["runtime_config_path"]))
    assert runtime["archive_sink"]["bucket"] == "valid-bucket"
    assert runtime["archive_sink"]["maximum_archive_bytes"] == 1_000_000
    assert runtime["archive_sink"]["maximum_capsule_bytes"] == 65536
    assert runtime["archive_sink"]["archive_root"] == archive_root
    assert runtime["archive_sink"]["staging_directory"] == staging_root
    assert runtime["archive_sink"]["source_credentials_file"] == tmp_path / "synthetic-adc.json"
    assert runtime["archive_sink"]["expected_source_principal"] == "katyabraun0@gmail.com"


def test_supported_preparer_pair_reserves_then_launches_both_normal_lifecycles(
        repository_root, tmp_path, monkeypatch, request):
    import copy
    import subprocess
    from types import SimpleNamespace
    from cloud_network_benchmark.admission import load_cost_inputs
    from cloud_network_benchmark.campaign import CampaignDriver
    from cloud_network_benchmark.contracts.common import Evidence, Provider
    from tests.integration.test_f11_ledger import scheduled_request_and_plan

    settings = operator_settings(tmp_path, repository_root, monkeypatch)
    class AdvancingClock(Clock):
        def __init__(self):
            self.now = datetime.now(timezone.utc).replace(microsecond=0)
            self.tick = 100.0
        def monotonic(self):
            return self.tick
        def sleep(self, seconds):
            self.tick += seconds
            self.now += timedelta(seconds=seconds)
    clock = AdvancingClock()
    fixed_start = (clock.utc_now() + timedelta(seconds=600)).isoformat()
    settings["stage_inputs"]["acceptance"]["start_utc"] = fixed_start
    settings["approvals"]["paired_acceptance"]["fields"]["start_utc"] = fixed_start
    stage = prepare_stage({"stage": "acceptance"}, settings, repository_root,
        Path(settings["state_root"]), Path(settings["results_root"]), clock)
    register_generated_cleanup(stage, request)
    providers = [Provider.AWS, Provider.AZURE, Provider.GCP]
    costs_path = repository_root / "tests/fixtures/f11/cost-inputs.json"
    for entry in stage["entries"][:2]:
        entry["runtime_config_path"] = str(tmp_path / f"{entry['entry_id']}-runtime.json")
        entry["parent_manifest_path"] = str(tmp_path / f"{entry['entry_id']}-parent.json")
        entry["cost_inputs_path"] = str(costs_path)
    costs = load_cost_inputs(costs_path)
    from cloud_network_benchmark.config import resolve_campaign
    campaign, _ = resolve_campaign(repository_root / "configs/tests/exp-903-three-provider-smoke.yaml", repository_root)
    runtime = {"ledger": {"path": stage["ledger_path"], "scope_id": stage["scope_id"],
                          "initialize_if_missing": True}}
    initialized_ids = iter(("V-S1", "V-S3"))

    def runtime_loader(_path, *, selected_providers=None):
        assert selected_providers == {"aws", "azure", "gcp"}
        return copy.deepcopy(runtime)

    def initialized(_path, *, repository_root, config_path, schedule_path):
        assert schedule_path is None
        entry_id = next(initialized_ids)
        return SimpleNamespace(campaign_id=entry_id), {
            provider.value: SimpleNamespace(run_id=f"{entry_id}-{provider.value}") for provider in providers}

    def admission_inputs(*, campaign, parent, children, costs, runtime, ledger_state, controller_session_id):
        admission, plan = scheduled_request_and_plan(campaign, costs, campaign_id=parent.campaign_id,
            providers=providers, child_prefix=parent.campaign_id, namespace_prefix=parent.campaign_id,
            funding={"primary": "50.20"}, measurement_seconds=76)
        admission = admission.model_copy(update={
            "incurred_cost_usd": Evidence[float](unavailable_reason="offline test ledger derives spend"),
            "active_reserved_usd": Evidence[float](unavailable_reason="offline test ledger derives holds")})
        return admission, plan

    import cloud_network_benchmark.lifecycle_runtime as lifecycle_runtime
    import cloud_network_benchmark.config as config_module
    monkeypatch.setattr(lifecycle_runtime, "load_runtime_config", runtime_loader)
    monkeypatch.setattr(lifecycle_runtime, "load_initialized_campaign", initialized)
    monkeypatch.setattr(lifecycle_runtime, "build_admission_request", admission_inputs)
    monkeypatch.setattr(config_module, "resolve_campaign", lambda *_a, **_k: (campaign, None))
    launched = []

    class OfflineLifecycle:
        returncode = 1
        pid = 999999
        def __init__(self, command, **kwargs):
            launched.append(command)
            kwargs["stdout"].write(b'{"status":"failed","providers":{}}\n')
            kwargs["stdout"].flush()
        def poll(self):
            return self.returncode

    monkeypatch.setattr(subprocess, "Popen", OfflineLifecycle)
    result = CampaignDriver(clock=clock, sleep=clock.sleep).run(stage)
    normal = {command[command.index("--config") + 1] for command in launched
        if command[command.index("--config") + 1].endswith(("v-s1.yaml", "v-s3.yaml"))}
    assert len(normal) == 2, {"launched": launched, "result": result,
        "states": {key: value["state"] for key, value in result["entries"].items()},
        "errors": {key: value.get("error") for key, value in result["entries"].items()},
        "schedule": [(row["entry_id"], row["scheduled_start"], row["launch_at"]) for row in stage["entries"]]}
    assert {Path(path).stem for path in normal} == {"v-s1", "v-s3"}
    ledger = BudgetLedger(Path(stage["ledger_path"]), scope_id=stage["scope_id"],
        utc_now=clock.utc_now, monotonic_now=clock.monotonic,
        controller_session_id=stage["controller_session_id"])
    assert len(ledger.inspect()["reservations"]) == 2
    assert set(result["entries"]) >= {"V-S1", "V-S3"}


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
    policy = stage["dispatch_policy"]
    assert policy["policy_id"] == "weekly-paired-s1-s3-v1"
    assert policy["max_parallel_entries"] == 2
    assert policy["evidence_sha256"] == hashlib.sha256(Path(policy["evidence_path"]).read_bytes()).hexdigest()
    policy_source = json.loads(Path(policy["evidence_path"]).read_text())
    assert policy_source["mode"] == "paired_s1_s3" and policy_source["max_parallel_entries"] == 2
    assert {(entry["scenario"], entry["priority"]) for entry in entries[:2]} == {("S1", "P0"), ("S3", "P1")}
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
        assert denied.status in {"rejected", "pending"} and "financial_authorization_invalid" in denied.reasons
        # An empirical acceptance stub cannot override missing financial authority.
        with monkeypatch.context() as approval_boundary:
            approval_boundary.setattr("cloud_network_benchmark.admission._validate_paid_acceptance_evidence", lambda *_: None)
            admitted = book.reserve(request, plan=plan)
        assert admitted.status == "rejected"
        assert admitted.reasons == ("financial_authorization_invalid",)
    assert len(book.inspect()["reservations"]) == 0
    assert {row["session"] for row in book.inspect()["reservations"].values()} == set()


def test_week_check_requires_exact_bounded_concurrency_admission(repository_root, tmp_path, monkeypatch):
    import cloud_network_benchmark.campaign_inputs as inputs

    settings = operator_settings(tmp_path, repository_root, monkeypatch)
    settings["approvals"]["weekly_concurrency"]["fields"]["max_parallel_entries"] = 3
    blockers = inputs.validate_stage_inputs({"stage": "week"}, settings, repository_root,
        local_preparation=True)
    assert blockers == ["weekly_concurrency must explicitly admit paired_s1_s3 with max_parallel_entries: 2"]
    assert not Path(settings["state_root"]).exists()
    assert not Path(settings["results_root"]).exists()


def test_complete_static_check_reaches_identity_review_without_mutation(repository_root, tmp_path, monkeypatch):
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
        for action in ("server_failure", "expiry", "cleanup"):
            evidence = tmp_path / f"{entry['entry_id']}-{action}-observed.txt"
            evidence.write_text(f"SYNTHETIC OFFLINE TEST ONLY: {attempt}: {action}")
            row[action] = {"confirmed": True, "source": str(evidence)}
        recovery = tmp_path / f"{entry['entry_id']}-ordinary-recovery-observed.txt"
        recovery.write_text(f"SYNTHETIC OFFLINE TEST ONLY: {attempt}: ordinary recovery")
        waiver = tmp_path / f"{entry['entry_id']}-physical-loss-waiver.txt"
        waiver.write_text("SYNTHETIC OFFLINE TEST ONLY: physical Halle loss is waived by the current decision")
        row["ordinary_recovery"] = {"confirmed": True, "source": str(recovery)}
        row["controller_loss"] = {"waived": True, "reason": "Physical Halle loss is explicitly waived.", "source": str(waiver)}
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


def test_exact_registered_failure_receipt_can_be_reused_without_recreating_its_attempt(repository_root, tmp_path,
        monkeypatch, request):
    import cloud_network_benchmark.campaign_inputs as inputs
    from cloud_network_benchmark.acceptance_review import _runtime_scope_sha256

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
        for action in ("server_failure", "expiry", "cleanup"):
            source = tmp_path / f"{entry['entry_id']}-{action}.txt"
            source.write_text(f"SYNTHETIC OFFLINE TEST ONLY: {attempt}: {action}")
            row[action] = {"confirmed": True, "source": str(source)}
        recovery = tmp_path / f"{entry['entry_id']}-recovery.txt"
        recovery.write_text(f"SYNTHETIC OFFLINE TEST ONLY: {attempt}: ordinary recovery")
        waiver = tmp_path / f"{entry['entry_id']}-waiver.txt"
        waiver.write_text("SYNTHETIC OFFLINE TEST ONLY: explicit physical loss waiver")
        row["ordinary_recovery"] = {"confirmed": True, "source": str(recovery)}
        row["controller_loss"] = {"waived": True, "reason": "Physical loss is waived.", "source": str(waiver)}
        declarations[entry["entry_id"]] = row

    reused_entry = next(entry for entry in stage["entries"] if entry["entry_id"] == "V-A-FAIL")
    parent = json.loads(Path(reused_entry["parent_manifest_path"]).read_text())
    config_path = Path(reused_entry["config_path"])
    runtime_path = Path(reused_entry["runtime_config_path"])
    identity = {"config_path": str(config_path), "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "source_config_sha256": reused_entry["source_config_sha256"], "runtime_config_path": str(runtime_path),
        "runtime_config_sha256": hashlib.sha256(runtime_path.read_bytes()).hexdigest(),
        "runtime_scope_sha256": _runtime_scope_sha256(runtime_path),
        "implementation_git_commit": parent.get("implementation_git_commit")}
    check = {"provider": "aws", "attempt_id": parent["campaign_id"], "config_identity": identity}
    for action in ("server_failure", "expiry", "cleanup", "ordinary_recovery"):
        source = tmp_path / f"reuse-{action}.txt"
        source.write_text(f"SYNTHETIC OFFLINE TEST ONLY: {parent['campaign_id']}: {action}")
        check[action] = {"status": "passed", "source_path": str(source), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    waiver = tmp_path / "reuse-waiver.txt"
    waiver.write_text("SYNTHETIC OFFLINE TEST ONLY: physical loss is waived")
    check["controller_loss"] = {"status": "waived", "reason": "Physical loss is waived.",
        "source_path": str(waiver), "source_sha256": hashlib.sha256(waiver.read_bytes()).hexdigest()}
    old = tmp_path / "completed-registered-check.yaml"
    old.write_text(yaml.safe_dump({"schema_version": 1, "checks": {"V-A-FAIL": check}}))
    declarations["V-A-FAIL"] = {"reuse": {"source": str(old), "sha256": hashlib.sha256(old.read_bytes()).hexdigest()}}
    settings["stage_inputs"]["acceptance"]["failure_checks"] = declarations
    _, review = inputs._review_from_prepared(settings, stage)
    assert review["checks"]["V-A-FAIL"]["attempt_id"] == parent["campaign_id"]
    assert review["checks"]["V-A-FAIL"]["config_identity"] == identity
    check["config_identity"]["runtime_config_sha256"] = "0" * 64
    old.write_text(yaml.safe_dump({"schema_version": 1, "checks": {"V-A-FAIL": check}}))
    settings["stage_inputs"]["acceptance"]["failure_checks"]["V-A-FAIL"]["reuse"]["sha256"] = hashlib.sha256(old.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="identity changed|exact prepared config/runtime"):
        inputs._review_from_prepared(settings, stage)
