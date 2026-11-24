"""Draft bundle integrity and no-call tests using the production manifest initializer."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from cloud_network_benchmark.campaign import CampaignDriver
from cloud_network_benchmark.campaign import _acceptance_pair
from cloud_network_benchmark.campaign_draft import (
    DRAFT_STATUS, load_and_verify_draft, mark_consumed,
    verify_activation, _stage_identity, _expected_acceptance_policy,
)


ROOT = Path(__file__).parents[2]


def _ready_acceptance_stage(tmp_path):
    source = tmp_path / "approval-source.yaml"; source.write_text("synthetic-offline-fixture\n")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    lower, upper = "2026-11-23T00:00:00+00:00", "2026-11-23T06:00:00+00:00"
    dispatch = {"not_before_utc": lower, "not_after_utc": "2026-11-23T03:00:00+00:00"}
    admission = {"mode": "paired_acceptance_s1_s3", "max_parallel_entries": 2,
        "execution_window_utc": {"not_before_utc": lower, "not_after_utc": upper},
        "dispatch_window_utc": dispatch,
        "source_path": str(source), "source_sha256": source_hash}
    admission_path = tmp_path / "paired-acceptance.json"
    admission_path.write_text(json.dumps(admission))
    policy = {"policy_id": "acceptance-paired-s1-s3-ready-v1", "group_by": "acceptance_slot",
        "scenarios": ["S1", "S3"], "max_parallel_entries": 2, "start_policy": "all_ready",
        "execution_window": admission["execution_window_utc"], "dispatch_window_utc": dispatch,
        "ready_window_seconds": 90,
        "evidence_path": str(admission_path), "evidence_sha256": hashlib.sha256(admission_path.read_bytes()).hexdigest()}
    entries = []
    participants = []
    for entry_id, scenario in (("V-S1", "S1"), ("V-S3", "S3")):
        run_ids = {provider: f"{entry_id}-{provider}" for provider in ("aws", "azure", "gcp")}
        entries.append({"entry_id": entry_id, "scenario": scenario, "requires_supervision": False,
            "run_ids": run_ids, "scheduled_start": None, "launch_at": lower})
        participants.extend({"entry_id": entry_id, "provider": provider, "run_id": run_id}
            for provider, run_id in run_ids.items())
    entries.extend({"entry_id": entry_id, "requires_supervision": True}
        for entry_id in ("V-A-FAIL", "V-Z-FAIL", "V-G-FAIL"))
    stage_id = "acceptance-ready"
    state_root = tmp_path / "state"
    barrier_root = state_root / stage_id / "measurement-barriers"
    return {"stage_id": stage_id, "stage": "acceptance", "state_root": str(state_root),
        "entries": entries, "dispatch_policy": policy, "dispatch_window_utc": dispatch,
        "post_measurement_barrier": {"schema_version": 1,
            "barrier_id": f"{stage_id}-normal-measurement-terminal",
            "barrier_root": str(barrier_root / "normal-terminal"), "expected_participants": participants},
        "measurement_start_barrier": {"schema_version": 1,
            "barrier_id": f"{stage_id}-normal-measurement-start",
            "barrier_root": str(barrier_root / "normal-start"), "expected_participants": participants,
            "start_policy": "all_ready", "execution_window": policy["execution_window"],
            "dispatch_window_utc": dispatch,
            "ready_window_seconds": policy["ready_window_seconds"]}}


def test_ready_acceptance_pair_freezes_window_and_all_six_participants(tmp_path):
    stage = _ready_acceptance_stage(tmp_path)
    assert [row["entry_id"] for row in _acceptance_pair(stage)] == ["V-S1", "V-S3"]

    legacy_null = json.loads(json.dumps(stage))
    admission_path = Path(legacy_null["dispatch_policy"]["evidence_path"])
    admission = json.loads(admission_path.read_text())
    admission.pop("execution_window_utc")
    admission["start_utc"] = "2026-11-23T00:00:00+00:00"
    admission_path.write_text(json.dumps(admission))
    legacy_null["dispatch_policy"].update(policy_id="acceptance-paired-s1-s3-v1",
        start_policy=None, ready_window_seconds=None, start_utc="2026-11-23T00:00:00+00:00",
        evidence_sha256=hashlib.sha256(admission_path.read_bytes()).hexdigest())
    legacy_null["measurement_start_barrier"].update(start_policy=None,
        execution_window=None, ready_window_seconds=None, start_utc="2026-11-23T00:00:00+00:00")
    with pytest.raises(ValueError, match="campaign timestamps"):
        _acceptance_pair(legacy_null)


def test_legacy_ready_profile_defaults_dispatch_to_the_original_execution_window(tmp_path):
    stage = _ready_acceptance_stage(tmp_path)
    admission_path = Path(stage["dispatch_policy"]["evidence_path"])
    admission = json.loads(admission_path.read_text())
    admission.pop("dispatch_window_utc")
    admission_path.write_text(json.dumps(admission))
    policy = stage["dispatch_policy"]
    policy.pop("dispatch_window_utc")
    policy["evidence_sha256"] = hashlib.sha256(admission_path.read_bytes()).hexdigest()
    stage.pop("dispatch_window_utc")
    stage["measurement_start_barrier"].pop("dispatch_window_utc")
    assert _acceptance_pair(stage) == stage["entries"][:2]
    assert _expected_acceptance_policy(stage)["execution_window"] == admission["execution_window_utc"]


def test_ready_acceptance_rejects_inconsistent_barrier_policy_and_non_null_schedule(tmp_path):
    stage = _ready_acceptance_stage(tmp_path)
    stage["entries"][0]["scheduled_start"] = "2026-11-23T00:00:00+00:00"
    with pytest.raises(ValueError, match="null scheduled starts"):
        _acceptance_pair(stage)


def test_ready_acceptance_rejects_changed_dispatch_window_binding(tmp_path):
    stage = _ready_acceptance_stage(tmp_path)
    stage["measurement_start_barrier"]["dispatch_window_utc"]["not_after_utc"] = "2026-11-23T05:00:00+00:00"
    with pytest.raises(ValueError, match="acceptance admission evidence"):
        _acceptance_pair(stage)


def test_ready_acceptance_draft_freezes_dispatch_window_across_stage_and_barrier(tmp_path):
    stage = _ready_acceptance_stage(tmp_path)
    expected = _expected_acceptance_policy(stage)
    assert expected["dispatch_window_utc"] == stage["dispatch_window_utc"]
    changed = json.loads(json.dumps(stage))
    changed["dispatch_policy"]["dispatch_window_utc"]["not_after_utc"] = "2026-11-23T05:00:00+00:00"
    with pytest.raises(ValueError, match="across stage, policy and runtime barrier"):
        _expected_acceptance_policy(changed)


def test_campaign_driver_excludes_expired_ready_dispatch_rows(tmp_path):
    from datetime import datetime, timezone

    runner = CampaignDriver.__new__(CampaignDriver)
    class Clock:
        def utc_now(self):
            return datetime(2026, 11, 23, 3, 0, 1, tzinfo=timezone.utc)
    runner.clock = Clock()
    entry = {"entry_id": "V-S1", "scheduled_start": None,
        "launch_at": "2026-11-23T00:00:00+00:00"}
    journal = {"entries": {"V-S1": {"state": "scheduled"}}}
    kept = runner._exclude_expired_slots([entry], journal, tmp_path / "journal.json",
        datetime(2026, 11, 23, 3, 0, 1, tzinfo=timezone.utc),
        (datetime(2026, 11, 23, 0, tzinfo=timezone.utc), datetime(2026, 11, 23, 3, tzinfo=timezone.utc)))
    assert kept == []
    assert journal["entries"]["V-S1"]["state"] == "missed"


def test_draft_driver_uses_initializer_outputs_and_freezes_two_stages(tmp_path, monkeypatch):
    import cloud_network_benchmark.campaign_inputs as inputs
    import cloud_network_benchmark.manifests as manifests

    calls = []
    provider_calls = []
    monkeypatch.setattr(inputs, "check_provider_identities", lambda *_: provider_calls.append(1))
    real_initialize = manifests.initialize_campaign

    class Clock:
        def utc_now(self):
            return datetime(2026, 11, 22, tzinfo=timezone.utc)

    state_root, results_root = tmp_path / "private", tmp_path / "results"
    state_root.mkdir()
    results_root.mkdir()
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text("fixture: offline-no-call-only\n")

    def loader(_):
        return {"state_root": str(state_root), "results_root": str(results_root)}

    def production_preparer(plan, settings, root, state, results, clock, *, draft=False):
        assert draft is True
        if plan["stage"] == "acceptance":
            specs = [("V-S1", "configs/tests/exp-903-three-provider-smoke.yaml", "S1", False),
                     ("V-S3", "configs/tests/exp-903-three-provider-smoke.yaml", "S3", False),
                     ("V-A-FAIL", "configs/tests/exp-900-single-provider.yaml", None, True),
                     ("V-Z-FAIL", "configs/tests/exp-901-azure-single-provider.yaml", None, True),
                     ("V-G-FAIL", "configs/tests/exp-902-gcp-single-provider.yaml", None, True)]
        else:
            specs = [("CAL-S1", "configs/experiments/exp-001-calibration.yaml", None, False)]
        entries = []
        for entry_id, alias, scenario, supervised in specs:
            config = root / alias
            initialized = manifests.initialize_campaign(config, root, results, clock=clock.utc_now)
            parent = Path(initialized.parent_manifest_path)
            if not parent.is_absolute():
                parent = root / parent
            manifest = json.loads(parent.read_text())
            runtime = state / f"{entry_id}-runtime.json"
            cost = state / f"{entry_id}-cost.json"
            runtime.write_text('{"approval_refs":{"paid_authorization":"PENDING_PATH_ONLY"}}\n')
            cost.write_text('{"cost_source":"SYNTHETIC OFFLINE TEST ONLY"}\n')
            entries.append({"entry_id": entry_id, "scenario": scenario,
                "priority": "P0", "config_path": str(config),
                "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
                "source_config_sha256": manifest["config_provenance"]["sha256"],
                "runtime_config_path": str(runtime), "runtime_config_sha256": hashlib.sha256(runtime.read_bytes()).hexdigest(),
                "schedule_path": None, "scheduled_start": None, "launch_at": datetime(2026, 11, 23, tzinfo=timezone.utc).isoformat(),
                "parent_manifest_path": str(parent), "cost_inputs_path": str(cost),
                "run_ids": {str(child["provider"]): child["run_id"] for child in manifest["child_runs"]},
                "private_root": str(state / entry_id), "requires_supervision": supervised})
        return {"stage_id": plan["stage"] + "-frozen", "stage": plan["stage"],
            "status": DRAFT_STATUS, "controller_session_id": "offline-session",
            "ledger_path": str(state / "ledger.json"), "scope_id": "one-shared-scope",
            "state_root": str(state), "results_root": str(results), "repository_root": str(root),
            "entries": entries, "draft_pending_evidence_paths": [str(state / f"{plan['stage']}-actual-approval.json")],
            "missing_prerequisites": ["paid_authorization", "quota_review"]}

    # Count the actual production manifest builder calls made by the preparer.
    def counted_initialize(*args, **kwargs):
        calls.append(1)
        return real_initialize(*args, **kwargs)
    monkeypatch.setattr(manifests, "initialize_campaign", counted_initialize)

    plans = []
    for stage in ("acceptance", "calibration"):
        path = tmp_path / f"{stage}.yaml"
        path.write_text(yaml.safe_dump({"stage": stage}))
        plans.append(path)

    driver = CampaignDriver(prepare=production_preparer, settings_loader=loader, clock=Clock())
    output = state_root / "fixed-batch-draft.json"
    bundle = driver.draft(plans, settings_path=settings_file, output_path=output, root=ROOT)
    assert calls == [1, 1, 1, 1, 1, 1]
    assert provider_calls == []
    assert bundle["status"] == DRAFT_STATUS
    assert [entry["entry_id"] for entry in bundle["stages"][0]["entries"]] == [
        "V-S1", "V-S3", "V-A-FAIL", "V-Z-FAIL", "V-G-FAIL"]
    assert len({run_id for stage in bundle["stages"] for entry in stage["entries"]
                for run_id in entry["run_ids"].values()}) == 12
    assert set(bundle["missing_prerequisites"]) == {"acceptance-frozen", "calibration-frozen"}
    assert all(not Path(path).exists() for paths in bundle["pending_evidence_paths"].values() for path in paths)
    first = load_and_verify_draft(output)
    second = load_and_verify_draft(output)
    assert first["draft_sha256"] == second["draft_sha256"]
    assert first["artifacts"] == second["artifacts"]


def test_draft_rejects_artifact_and_bundle_tampering(tmp_path):
    from cloud_network_benchmark.campaign_draft import create_draft_bundle

    config = tmp_path / "config.yaml"
    runtime = tmp_path / "runtime.json"
    cost = tmp_path / "cost.json"
    manifest = tmp_path / "manifest.json"
    source = tmp_path / "source.yaml"
    source_tree = tmp_path / "src" / "cloud_network_benchmark"
    source_tree.mkdir(parents=True)
    implementation = source_tree / "workflow.py"
    implementation.write_text("source-v1")
    for path, content in ((config, "c"), (runtime, "{}"), (cost, "{}"), (source, "s")):
        path.write_text(content)
    manifest.write_text(json.dumps({"config_provenance": {"source_path": str(source)}}))
    stage = {"stage_id": "a", "stage": "acceptance", "status": DRAFT_STATUS,
        "repository_root": str(tmp_path), "state_root": str(tmp_path), "entries": [{"entry_id": "V-S1", "config_path": str(config),
        "runtime_config_path": str(runtime), "cost_inputs_path": str(cost),
        "parent_manifest_path": str(manifest), "run_ids": {"aws": "stable-run"}}],
        "draft_pending_evidence_paths": []}
    bundle_path = tmp_path / "draft.json"
    bundle = create_draft_bundle(plan={"batch_id": "fixture"}, stages=[stage], output_path=bundle_path,
        missing_prerequisites={"a": ["paid_authorization"]}, preparer=lambda: None)
    assert load_and_verify_draft(bundle_path)["draft_sha256"] == bundle["draft_sha256"]
    alias = tmp_path / "draft-alias.json"
    alias.symlink_to(bundle_path)
    with pytest.raises(ValueError, match="symlinks"):
        load_and_verify_draft(alias)
    source.write_text("changed")
    with pytest.raises(ValueError, match="artifacts changed"):
        load_and_verify_draft(bundle_path)

    # Restore file content, then independently detect edits to the wrapper.
    source.write_text("s")
    implementation.write_text("source-v2")
    with pytest.raises(ValueError, match="source tree changed"):
        load_and_verify_draft(bundle_path)
    implementation.write_text("source-v1")
    bundle["missing_prerequisites"]["a"].append("tampered")
    bundle_path.write_text(json.dumps(bundle))
    with pytest.raises(ValueError, match="bundle digest changed"):
        load_and_verify_draft(bundle_path)


def test_draft_blocks_activation_without_execute_and_rejects_identity_change(tmp_path):
    from cloud_network_benchmark.campaign_draft import create_draft_bundle

    config = tmp_path / "config.yaml"; config.write_text("config")
    runtime = tmp_path / "runtime.json"; runtime.write_text("{}")
    cost = tmp_path / "cost.json"; cost.write_text("{}")
    manifest = tmp_path / "manifest.json"; manifest.write_text("{}")
    state = tmp_path / "state"; state.mkdir()
    results = tmp_path / "results"; results.mkdir()
    settings_file = tmp_path / "settings.yaml"; settings_file.write_text("offline fixture\n")
    stage = {"stage_id": "acceptance-1", "stage": "acceptance", "status": DRAFT_STATUS,
        "state_root": str(state), "results_root": str(results), "repository_root": str(ROOT),
        "entries": [{"entry_id": "V-S1", "config_path": str(config), "runtime_config_path": str(runtime),
        "cost_inputs_path": str(cost), "parent_manifest_path": str(manifest),
        "run_ids": {"aws": "stable-run"}}], "draft_pending_evidence_paths": []}
    bundle_path = state / "draft.json"
    create_draft_bundle(plan={"batch_id": "fixture"}, stages=[stage], output_path=bundle_path,
        missing_prerequisites={"acceptance-1": ["paid_authorization"]}, preparer=lambda: None)
    activated = []
    driver = CampaignDriver(settings_loader=lambda _: {}, activate_prepared=lambda **_: activated.append(1))
    result = driver.activate_frozen_draft(bundle_path, stage_id="acceptance-1", settings_path=settings_file,
                                          execute=False, root=ROOT)
    assert result["status"] == "blocked" and result["resource_creating_calls"] == 0
    assert activated == []

    changed = dict(stage)
    changed["entries"] = [dict(stage["entries"][0], run_ids={"aws": "different-run"})]
    with pytest.raises(ValueError, match="changed frozen identities"):
        verify_activation({"stages": [stage], "stage_identities": [_stage_identity(stage)]}, "acceptance-1", changed)


def test_missing_real_paid_card_blocks_before_activation_or_dispatch(tmp_path):
    from cloud_network_benchmark.campaign_draft import create_draft_bundle

    config = tmp_path / "config.yaml"; config.write_text("config")
    runtime = tmp_path / "runtime.json"; runtime.write_text("{}")
    cost = tmp_path / "cost.json"; cost.write_text("{}")
    manifest = tmp_path / "manifest.json"; manifest.write_text("{}")
    state = tmp_path / "state"; state.mkdir()
    results = tmp_path / "results"; results.mkdir()
    settings_file = tmp_path / "settings.yaml"; settings_file.write_text("offline fixture\n")
    stage = {"stage_id": "acceptance-1", "stage": "acceptance", "status": DRAFT_STATUS,
        "state_root": str(state), "results_root": str(results), "repository_root": str(ROOT),
        "scope_id": "fixture-scope", "entries": [{"entry_id": "V-S1", "config_path": str(config),
        "runtime_config_path": str(runtime), "cost_inputs_path": str(cost),
        "parent_manifest_path": str(manifest), "run_ids": {"aws": "stable-run"}}],
        "draft_pending_evidence_paths": []}
    path = state / "draft.json"
    create_draft_bundle(plan={"batch_id": "fixed-pre-day-v1"}, stages=[stage], output_path=path,
        missing_prerequisites={"acceptance-1": ["paid_authorization"]}, preparer=lambda: None)
    calls = []
    driver = CampaignDriver(settings_loader=lambda _: {},
        activate_prepared=lambda **_: calls.append("activation"),
        dispatch=lambda *_: calls.append("dispatch"))
    with pytest.raises(ValueError, match="genuine paid_authorization"):
        driver.activate_frozen_draft(path, stage_id="acceptance-1", settings_path=settings_file,
                                     execute=True, root=ROOT)
    assert calls == []


def test_campaign_runner_and_resume_reject_draft_state(tmp_path):
    state = tmp_path / "state"; state.mkdir()
    stage_folder = state / "pending-stage"; stage_folder.mkdir()
    prepared = stage_folder / "prepared-stage.json"
    prepared.write_text(json.dumps({"stage_id": "pending-stage", "stage": "acceptance",
        "status": DRAFT_STATUS, "state_root": str(state), "entries": []}))
    (state / "active-stage.json").write_text(json.dumps({"stage_id": "pending-stage",
        "stage_path": str(prepared)}))
    driver = CampaignDriver()
    with pytest.raises(ValueError, match="unapproved campaign draft"):
        driver.run_active(state)
    with pytest.raises(ValueError, match="unapproved draft"):
        driver.run({"stage_id": "pending-stage", "status": DRAFT_STATUS,
                    "state_root": str(state), "entries": []})


def test_valid_activation_dispatches_same_frozen_stage_once(tmp_path):
    from cloud_network_benchmark.campaign_draft import create_draft_bundle

    config = tmp_path / "config.yaml"; config.write_text("config")
    runtime = tmp_path / "runtime.json"; runtime.write_text("{}")
    cost = tmp_path / "cost.json"; cost.write_text("{}")
    manifest = tmp_path / "manifest.json"; manifest.write_text("{}")
    state = tmp_path / "state"; state.mkdir()
    results = tmp_path / "results"; results.mkdir()
    stage_id = "acceptance-frozen"
    (state / stage_id).mkdir()
    approval = state / "fixture-approval-evidence.json"
    settings_file = tmp_path / "settings.yaml"; settings_file.write_text("offline fixture\n")
    entry = {"entry_id": "V-S1", "config_path": str(config),
        "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
        "runtime_config_path": str(runtime), "runtime_config_sha256": hashlib.sha256(runtime.read_bytes()).hexdigest(),
        "cost_inputs_path": str(cost), "parent_manifest_path": str(manifest),
        "run_ids": {"aws": "stable-run"}, "private_root": str(state / stage_id / "V-S1"),
        "launch_at": "2026-11-23T00:00:00+00:00", "scheduled_start": None}
    stage = {"stage_id": stage_id, "stage": "acceptance", "status": DRAFT_STATUS,
        "state_root": str(state), "results_root": str(results), "repository_root": str(ROOT),
        "ledger_path": str(state / "ledger.json"), "scope_id": "shared-scope",
        "controller_session_id": "offline-session", "entries": [entry],
        "draft_pending_evidence_paths": [str(approval)]}
    draft_path = state / "draft.json"
    bundle = create_draft_bundle(plan={"batch_id": "fixed-pre-day-v1"}, stages=[stage],
        output_path=draft_path, missing_prerequisites={stage_id: ["paid_authorization"]},
        preparer=lambda: None)
    prepared_again = []
    dispatched = []

    def activate(*, prepared, settings, root, preflight):
        finance = preflight(prepared, {})
        approval.write_text('{"fixture":"SYNTHETIC OFFLINE TEST ONLY"}\n')
        assert finance["financial_binding_sha256"] == "b" * 64
        return {**prepared, "status": "READY_FOR_ADMISSION",
                "approval_files_created": True, "pending_prerequisites": []}

    driver = CampaignDriver(prepare=lambda *_a, **_k: prepared_again.append(1),
        financial_preflight=lambda **_: {"financial_binding_sha256": "b" * 64, "authorization_source_sha256": "c" * 64},
        settings_loader=lambda _: {"operator_log_path": str(state / "campaign.log")},
        activate_prepared=activate,
        dispatch=lambda frozen: dispatched.append(frozen) or {"supervisor": "no-call-test"})
    result = driver.activate_frozen_draft(draft_path, stage_id=stage_id,
        settings_path=settings_file, execute=True, root=ROOT)
    assert result["status"] == "dispatched"
    assert result["frozen_draft_sha256"] == bundle["draft_sha256"]
    assert prepared_again == []
    assert len(dispatched) == 1 and dispatched[0]["entries"][0]["run_ids"] == {"aws": "stable-run"}
    assert load_and_verify_draft(draft_path)["draft_sha256"] == bundle["draft_sha256"]
    with pytest.raises(ValueError, match="already activated or dispatched"):
        driver.activate_frozen_draft(draft_path, stage_id=stage_id,
            settings_path=settings_file, execute=True, root=ROOT)
    assert len(dispatched) == 1


def test_financial_preflight_failure_publishes_no_approval_evidence_and_keeps_draft_retryable(tmp_path):
    from cloud_network_benchmark.campaign_draft import create_draft_bundle

    config = tmp_path / "config.yaml"; config.write_text("config")
    runtime = tmp_path / "runtime.json"; runtime.write_text("{}")
    cost = tmp_path / "cost.json"; cost.write_text("{}")
    manifest = tmp_path / "manifest.json"; manifest.write_text("{}")
    state = tmp_path / "state"; state.mkdir()
    results = tmp_path / "results"; results.mkdir()
    stage_id = "acceptance-preflight-failure"
    stage_dir = state / stage_id; stage_dir.mkdir()
    approval = stage_dir / "approvals" / "paid_authorization.json"
    settings_file = tmp_path / "settings.yaml"; settings_file.write_text("offline fixture\n")
    stage = {"stage_id": stage_id, "stage": "acceptance", "status": DRAFT_STATUS,
        "state_root": str(state), "results_root": str(results), "repository_root": str(ROOT),
        "entries": [{"entry_id": "V-S1", "config_path": str(config),
        "runtime_config_path": str(runtime), "cost_inputs_path": str(cost),
        "parent_manifest_path": str(manifest), "run_ids": {"aws": "stable-run"}}],
        "draft_pending_evidence_paths": [str(approval)]}
    path = state / "draft.json"
    create_draft_bundle(plan={"batch_id": "fixed-pre-day-v1"}, stages=[stage], output_path=path,
        missing_prerequisites={stage_id: ["paid_authorization"]}, preparer=lambda: None)
    calls = []

    def activation(*, prepared, settings, root, preflight):
        calls.append("preflight")
        preflight(prepared, {})
        approval.parent.mkdir(parents=True, exist_ok=True)
        approval.write_text("must never happen")
        return {**prepared, "status": "READY_FOR_ADMISSION",
            "approval_files_created": True, "pending_prerequisites": []}

    driver = CampaignDriver(settings_loader=lambda _: {}, activate_prepared=activation,
        financial_preflight=lambda **_: (_ for _ in ()).throw(ValueError("complete binding mismatch")),
        dispatch=lambda *_: pytest.fail("failed financial preflight reached dispatch"))
    with pytest.raises(ValueError, match="complete binding mismatch"):
        driver.activate_frozen_draft(path, stage_id=stage_id, settings_path=settings_file,
                                     execute=True, root=ROOT)
    assert calls == ["preflight"]
    assert not approval.exists()
    assert load_and_verify_draft(path)["stages"][0]["entries"][0]["run_ids"] == {"aws": "stable-run"}


@pytest.mark.parametrize(("invalid_case", "expected_error"), [
    ("expired", "financial_authorization_expired"),
    ("runtime_binding", "financial_scope_mismatch"),
])
def test_real_paid_card_binding_failure_precedes_evidence_publication(
        tmp_path, monkeypatch, invalid_case, expected_error):
    from cloud_network_benchmark.campaign_draft import create_draft_bundle
    from dataclasses import replace
    from types import SimpleNamespace
    from tests.financial_helpers import synthetic_finances, NOW
    from cloud_network_benchmark.financial_controls import FinancialError

    book, plans, card, card_path = synthetic_finances(tmp_path, count=6, paid=True)
    card.update(batch_id="fixed-pre-day-v1", ledger_scope_id=book.scope_id)
    if invalid_case == "expired":
        card["valid_until"] = "2000-01-01T00:00:00Z"
    else:
        card["entries"][0]["runtime"]["effective_runtime_sha256"] = "e" * 64
    card_path.write_text(json.dumps(card, sort_keys=True))
    authority_ref = {"source_path": str(card_path),
        "source_sha256": hashlib.sha256(card_path.read_bytes()).hexdigest()}

    state = tmp_path / "private"; state.mkdir()
    results = tmp_path / "results"; results.mkdir()
    settings_path = tmp_path / "settings.yaml"; settings_path.write_text("offline fixture\n")
    config_path = ROOT / "configs/tests/exp-903-three-provider-smoke.yaml"
    entries = []
    plan_by_campaign = {request.campaign_id: (request, plan) for request, plan in plans}
    for index, (request, plan) in enumerate(plans):
        runtime_path = tmp_path / f"runtime-{index}.json"; runtime_path.write_text("{}\n")
        parent_path = tmp_path / f"parent-{index}.json"
        parent_path.write_text(json.dumps({"campaign_id": request.campaign_id}))
        entries.append({"entry_id": f"entry-{index}", "scenario": "S1" if index % 2 == 0 else "S3",
            "config_path": str(config_path), "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
            "runtime_config_path": str(runtime_path), "runtime_config_sha256": hashlib.sha256(runtime_path.read_bytes()).hexdigest(),
            "cost_inputs_path": str(plan.costs.source_path), "parent_manifest_path": str(parent_path),
            "schedule_path": None, "run_ids": dict(plan.child_identities)})
    approval = state / "acceptance-approval.json"
    cal_approval = state / "calibration-approval.json"
    acceptance = {"stage_id": "acceptance-draft", "stage": "acceptance", "status": DRAFT_STATUS,
        "state_root": str(state), "results_root": str(results), "repository_root": str(ROOT),
        "scope_id": book.scope_id, "controller_session_id": "SYNTHETIC-NO-LAUNCH",
        "entries": entries[:5], "draft_pending_evidence_paths": [str(approval)]}
    calibration = {**acceptance, "stage_id": "calibration-draft", "stage": "calibration",
        "entries": entries[5:], "draft_pending_evidence_paths": [str(cal_approval)]}
    draft_path = state / "draft.json"
    bundle = create_draft_bundle(plan={"batch_id": "fixed-pre-day-v1"}, stages=[acceptance, calibration],
        output_path=draft_path,
        missing_prerequisites={acceptance["stage_id"]: ["paid_authorization"],
            calibration["stage_id"]: ["paid_acceptance"]}, preparer=lambda: None)

    import cloud_network_benchmark.admission as admission
    import cloud_network_benchmark.config as config_module
    import cloud_network_benchmark.lifecycle_runtime as runtime_module
    import cloud_network_benchmark.scheduling as scheduling
    monkeypatch.setattr(admission, "load_cost_inputs", lambda _path: plans[0][1].costs)
    monkeypatch.setattr(config_module, "resolve_campaign", lambda *_a, **_k: (object(), None))
    monkeypatch.setattr(scheduling, "load_schedule", lambda *_a, **_k: [])

    def load_initialized(parent_path, **_kwargs):
        campaign_id = json.loads(Path(parent_path).read_text())["campaign_id"]
        return SimpleNamespace(campaign_id=campaign_id), {}

    monkeypatch.setattr(runtime_module, "load_initialized_campaign", load_initialized)
    monkeypatch.setattr(runtime_module, "build_admission_request", lambda *, parent, **_kwargs: (
        plan_by_campaign[parent.campaign_id][0],
        replace(plan_by_campaign[parent.campaign_id][1], prerequisites={
            **plan_by_campaign[parent.campaign_id][1].prerequisites,
            "paid_authorization": authority_ref})))

    stage_runtime = {}
    for entry in entries[:5]:
        runtime_file = Path(entry["runtime_config_path"])
        stage_runtime[entry["entry_id"]] = {"runtime_source": {"path": str(runtime_file.absolute()),
            "sha256": entry["runtime_config_sha256"]}, "approval_evidence": {}}

    def activation(*, prepared, settings, root, preflight):
        preflight(prepared, stage_runtime)
        pytest.fail("approval evidence must not be written when real financial validation fails")

    settings = {"approvals": {"paid_authorization": {"confirmed": True, "source": str(card_path)}}}
    driver = CampaignDriver(settings_loader=lambda _: settings, activate_prepared=activation,
        clock=SimpleNamespace(utc_now=lambda: NOW), dispatch=lambda *_: pytest.fail("dispatch is forbidden"))
    with pytest.raises(FinancialError, match=expected_error):
        driver.activate_frozen_draft(draft_path, stage_id=acceptance["stage_id"],
            settings_path=settings_path, execute=True, root=ROOT)
    assert not approval.exists()
    assert not cal_approval.exists()
    reloaded = load_and_verify_draft(draft_path)
    assert reloaded["draft_sha256"] == bundle["draft_sha256"]
    assert [row["entry_id"] for row in reloaded["stages"][0]["entries"]] == [row["entry_id"] for row in entries[:5]]


def test_cli_has_explicit_draft_and_execute_activation_arguments(capsys):
    from cloud_network_benchmark.campaign_cli import _parser, main

    parser = _parser()
    draft = parser.parse_args(["draft", "--plan", "acceptance.yaml", "--plan", "calibration.yaml",
                              "--output", "/private/state/draft.json"])
    assert draft.action == "draft" and len(draft.plan) == 2
    activation = parser.parse_args(["activate", "--draft", "/private/state/draft.json",
                                    "--stage-id", "acceptance-1"])
    assert not activation.execute
    activation = parser.parse_args(["activate", "--draft", "/private/state/draft.json",
                                    "--stage-id", "acceptance-1", "--execute"])
    assert activation.execute

    class FakeDraftDriver:
        def draft(self, plans, *, settings_path=None, output_path, root=None):
            assert len(plans) == 2
            return {"status": DRAFT_STATUS, "resource_creating_calls": 0}

    assert main(["draft", "--plan", "acceptance.yaml", "--plan", "calibration.yaml",
                 "--output", "/private/state/draft.json"], driver=FakeDraftDriver()) == 0
    assert json.loads(capsys.readouterr().out)["status"] == DRAFT_STATUS


def test_consumed_draft_cannot_be_replayed(tmp_path):
    from cloud_network_benchmark.campaign_draft import create_draft_bundle

    files = []
    for name in ("config", "runtime", "cost", "manifest"):
        path = tmp_path / f"{name}.json"; path.write_text("{}" if name in {"manifest", "runtime", "cost"} else name); files.append(path)
    approval = tmp_path / "acceptance-approval.json"
    cal_approval = tmp_path / "calibration-approval.json"
    stage = {"stage_id": "stage-1", "stage": "acceptance", "status": DRAFT_STATUS,
        "repository_root": str(tmp_path), "state_root": str(tmp_path), "entries": [{"entry_id": "V-S1", "config_path": str(files[0]),
        "runtime_config_path": str(files[1]), "cost_inputs_path": str(files[2]),
        "parent_manifest_path": str(files[3]), "run_ids": {"aws": "stable"}}],
        "draft_pending_evidence_paths": [str(approval)]}
    calibration = {**stage, "stage_id": "stage-2", "stage": "calibration",
                   "entries": [{**stage["entries"][0], "entry_id": "CAL-S1"}],
                   "draft_pending_evidence_paths": [str(cal_approval)]}
    path = tmp_path / "draft.json"
    bundle = create_draft_bundle(plan={}, stages=[stage, calibration], output_path=path,
        missing_prerequisites={"stage-1": [], "stage-2": ["paid_acceptance"]}, preparer=lambda: None)
    approval.write_text('{"fixture":"offline actual-approval placeholder"}\n')
    mark_consumed(path, bundle, stage_id="stage-1", activated_stage_digest="a" * 64)
    assert load_and_verify_draft(path)["draft_sha256"] == bundle["draft_sha256"]
    assert not cal_approval.exists()
    with pytest.raises(ValueError, match="already activated or dispatched"):
        mark_consumed(path, bundle, stage_id="stage-1", activated_stage_digest="b" * 64)
    approval.write_text('{"fixture":"tampered"}\n')
    with pytest.raises(ValueError, match="approval evidence changed"):
        load_and_verify_draft(path)
