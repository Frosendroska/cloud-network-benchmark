"""Project WEEK lifecycle JSON through the campaign runner at the fake Popen edge.

No lifecycle command or provider boundary runs here. Archive/capsule JSON is
synthetic projection input; this file tests status preservation and financial
fail-closed behavior, not archive replay or native validation.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

import pytest

from cloud_network_benchmark import campaign as campaign_module
from cloud_network_benchmark.admission import BudgetLedger, load_cost_inputs
from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.contracts.common import Provider
from cloud_network_benchmark.orchestration import SavedLifecycleRecovery, SavedProviderRecovery
from tests.integration.test_campaign_driver import FakeClock, stage_fixture
from tests.integration.test_f09_aws_runtime_factory import _runtime
from tests.integration.test_f11_ledger import record_absence, scheduled_request_and_plan


ROOT = Path(__file__).parents[2]


def _encode_runtime_value(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"unsupported runtime fixture value {type(value).__name__}")


def _stage_entry(tmp_path):
    stage = stage_fixture(tmp_path, slots=1)
    stage["stage"] = "week"
    stage["repository_root"] = str(ROOT)
    return stage, stage["entries"][0]


def _runtime_ledger_fixture(tmp_path, stage, entry, *, settle: bool):
    """Write a parsed F09 test runtime and real local ledger/recovery state."""
    parent_path = Path(entry["parent_manifest_path"])
    parent_path.parent.mkdir(parents=True, exist_ok=True)
    parent_path.write_text(json.dumps({"campaign_id": entry["entry_id"]}) + "\n", encoding="utf-8")
    private_root = Path(entry["private_root"])
    private_root.mkdir(parents=True, exist_ok=True)

    costs = load_cost_inputs(ROOT / "tests/fixtures/f11/cost-inputs.json")
    clock = FakeClock()
    book = BudgetLedger(Path(stage["ledger_path"]), scope_id=stage["scope_id"],
        utc_now=clock.utc_now, monotonic_now=clock.monotonic,
        controller_session_id=stage["controller_session_id"])
    book.initialize(costs)
    campaign, _ = resolve_campaign(ROOT / "configs/tests/exp-903-three-provider-smoke.yaml", ROOT)
    providers = [Provider.AWS, Provider.AZURE, Provider.GCP]
    request, plan = scheduled_request_and_plan(campaign, costs,
        campaign_id=entry["entry_id"], providers=providers,
        child_prefix=entry["entry_id"], namespace_prefix=entry["entry_id"],
        funding={"primary": ".5"}, measurement_seconds=76)
    decision = book.reserve(request, plan=plan)
    assert decision.status == "reserved", decision.reasons
    reservation_id = decision.evidence.reservation_id.value

    if settle:
        for provider in providers:
            record_absence(book, f"{entry['entry_id']}-{provider.value}", reservation_id,
                provider, clock.utc_now())
        recovery = SavedLifecycleRecovery(schema_version=1, attempt_id=entry["entry_id"],
            controller_session_id=stage["controller_session_id"], reservation_id=reservation_id,
            ledger_path=stage["ledger_path"], ledger_scope_id=stage["scope_id"],
            providers=[SavedProviderRecovery(provider=provider.value,
                deployment_id=f"{entry['run_ids'][provider.value]}-deployment",
                journal_reference=f"synthetic/{entry['run_ids'][provider.value]}/attempt.json",
                expiry_registered=True, creation_may_have_started=True, cleanup_open=False)
                for provider in providers])
        (private_root / "lifecycle-recovery.json").write_text(recovery.model_dump_json(), encoding="utf-8")

    raw_runtime = _runtime(tmp_path, ROOT, ROOT / "tests/fixtures/f11/cost-inputs.json",
        parent_path, costs.payload_sha256)
    raw_runtime.pop("approval_evidence", None)
    raw_runtime["schema_version"] = 1
    raw_runtime["initialized_campaign_manifest"] = parent_path
    raw_runtime["ledger"] = {"path": Path(stage["ledger_path"]), "scope_id": stage["scope_id"],
        "initialize_if_missing": False}
    runtime_path = tmp_path / f"{entry['entry_id']}-runtime.json"
    runtime_path.write_text(json.dumps(raw_runtime, default=_encode_runtime_value, sort_keys=True) + "\n",
        encoding="utf-8")
    entry["runtime_config_path"] = str(runtime_path)
    return reservation_id, book, runtime_path


def _run_projection(monkeypatch, tmp_path, stage, entry, report, *, returncode=0):
    output = json.dumps(report, sort_keys=True).encode("utf-8") + b"\n"
    calls = []

    class ExitedProcess:
        pid = 0

        def __init__(self):
            self.returncode = returncode

        def poll(self):
            return self.returncode

    def fake_popen(command, *, cwd, stdout, stderr):
        calls.append((command, cwd, stderr))
        assert command[1:5] == ["-m", "cloud_network_benchmark", "lifecycle", "run"]
        stdout.write(output)
        stdout.flush()
        return ExitedProcess()

    monkeypatch.setattr(campaign_module.subprocess, "Popen", fake_popen)
    result = campaign_module.CampaignDriver._launch_lifecycle(entry, stage)
    assert len(calls) == 1
    assert Path(calls[0][0][calls[0][0].index("--config") + 1]) == Path(entry["config_path"])
    return result


def _report(entry, *, status="archive_pending_validation", capsule_ref=None, run_ids=None,
            reservation_id=None, admission_status=None):
    capsule_ref = capsule_ref or {"status": "archive_pending_replay", "path": "/synthetic/capsule.json",
        "sha256": "a" * 64}
    report = {"status": status, "run_ids": run_ids or entry["run_ids"],
        "providers": {provider: {"cleanup_state": "succeeded"}
            for provider in entry["run_ids"]},
        "replay_capsule": capsule_ref}
    if reservation_id is not None:
        report["reservation_id"] = reservation_id
    if admission_status is not None:
        report["admission_status"] = admission_status
    return report


def test_week_shape_only_capsule_is_not_projected_as_pending(tmp_path, monkeypatch):
    stage, entry = _stage_entry(tmp_path)
    reservation_id, book, _runtime_path = _runtime_ledger_fixture(tmp_path, stage, entry, settle=True)
    capsule_path = tmp_path / "archive-pending.json"
    capsule_path.write_text(json.dumps({"schema_version": 2, "status": "archive_pending_replay",
        "reservation": book.inspect()["reservations"][reservation_id]["evidence"],
        "archives": {provider: {} for provider in entry["run_ids"]}}, sort_keys=True) + "\n")
    capsule_ref = {"status": "archive_pending_replay", "path": str(capsule_path),
        "sha256": hashlib.sha256(capsule_path.read_bytes()).hexdigest()}

    result = _run_projection(monkeypatch, tmp_path, stage, entry,
        _report(entry, capsule_ref=capsule_ref, reservation_id=reservation_id, admission_status="reserved"))

    assert result["status"] == "failed"
    assert result["cleanup_open"] is False
    assert result["validated_outputs_present"] is False
    assert result["lifecycle_status_complete"] is False
    assert "replay_capsule" not in result
    assert result["ledger_reconciliation_required"] is False
    assert not any((Path(stage["results_root"]) / "raw" / run_id / "validation").exists()
                   for run_id in entry["run_ids"].values())


def test_genuine_producer_capsule_is_projected_pending_after_strict_binding(tmp_path, monkeypatch):
    from tests.integration.week_archive_fixture import build_single_provider_week_archive_fixture

    fixture = build_single_provider_week_archive_fixture(tmp_path=tmp_path / "producer",
        monkeypatch=monkeypatch, repository_root=ROOT)
    try:
        stage, entry = _stage_entry(tmp_path / "driver")
        parent_doc = fixture.capsule["parent_manifest"]
        campaign_id = parent_doc["campaign_id"]
        reservation_id = fixture.capsule["reservation"]["reservation_id"]["value"]
        archive_root = tmp_path / "bound-archive"
        capsule_path = archive_root / "replay-capsules" / f"{campaign_id}-{reservation_id}.json"
        capsule_path.parent.mkdir(parents=True)
        capsule_bytes = fixture.capsule_path.read_bytes()
        capsule_path.write_bytes(capsule_bytes)
        runtime_path = tmp_path / "bound-runtime.json"
        runtime_path.write_text(json.dumps({"archive_sink": {"archive_root": str(archive_root),
            "maximum_capsule_bytes": len(capsule_bytes) + 1}}) + "\n")
        entry.update(run_ids={"aws": fixture.capsule["child_manifests"]["aws"]["run_id"]},
            parent_manifest_path=str(fixture.parent_path), runtime_config_path=str(runtime_path))
        stage["repository_root"] = str(fixture.repository_root)
        provider_result = fixture.capsule["provider_results"]["aws"]
        capsule_ref = {"status": "archive_pending_replay", "path": str(capsule_path),
            "sha256": hashlib.sha256(capsule_bytes).hexdigest(), "bytes": len(capsule_bytes),
            "providers": ["aws"], "manifest_sha256": fixture.capsule["manifest_sha256"]}
        report = {"status": "archive_pending_validation", "campaign_id": campaign_id,
            "reservation_id": reservation_id, "run_ids": entry["run_ids"],
            "providers": {"aws": {"execution_state": provider_result["execution_state"],
                "cleanup_state": provider_result["cleanup_state"],
                "archive_status": fixture.receipt}}, "replay_capsule": capsule_ref}
        from cloud_network_benchmark import lifecycle_runtime
        monkeypatch.setattr(lifecycle_runtime, "load_runtime_config", lambda *_a, **_k: {})
        monkeypatch.setattr(campaign_module, "_week_ledger_settled", lambda *_a, **_k: True)

        result = _run_projection(monkeypatch, tmp_path, stage, entry, report)

        assert result["status"] == "archive_pending_validation"
        assert result["replay_capsule"] == capsule_ref
        assert result["ledger_reconciliation_required"] is False
    finally:
        fixture.cleanup()


@pytest.mark.parametrize("bad_case", ["malformed_digest", "wrong_run_ids", "wrong_capsule_status"])
def test_invalid_or_wrongrun_capsule_is_not_projected_as_pending(tmp_path, monkeypatch, bad_case):
    stage, entry = _stage_entry(tmp_path)
    capsule_ref = {"status": "archive_pending_replay", "path": "/synthetic/capsule.json", "sha256": "a" * 64}
    run_ids = entry["run_ids"]
    if bad_case == "malformed_digest":
        capsule_ref["sha256"] = "A" * 64
    elif bad_case == "wrong_run_ids":
        run_ids = {**entry["run_ids"], "aws": "wrong-run-id"}
    else:
        capsule_ref["status"] = "replayed"

    result = _run_projection(monkeypatch, tmp_path, stage, entry,
        _report(entry, capsule_ref=capsule_ref, run_ids=run_ids))

    assert result["status"] == "failed"
    assert "replay_capsule" not in result
    assert result["validated_outputs_present"] is False


def test_failed_cleanupclosed_with_open_real_reservation_sets_ledger_blocker(tmp_path, monkeypatch):
    stage, entry = _stage_entry(tmp_path)
    reservation_id, book, _runtime_path = _runtime_ledger_fixture(tmp_path, stage, entry, settle=False)
    report = _report(entry, status="failed", reservation_id=reservation_id, admission_status="reserved")

    result = _run_projection(monkeypatch, tmp_path, stage, entry, report, returncode=1)

    assert result["status"] == "failed"
    assert result["cleanup_open"] is False
    assert result["validated_outputs_present"] is False
    assert result["ledger_reconciliation_required"] is True
    assert book.inspect()["reservations"][reservation_id]["cleanup_open"] is True


def test_reserved_week_projection_without_runtime_fails_closed(tmp_path, monkeypatch):
    stage, entry = _stage_entry(tmp_path)
    result_log = _report(entry, status="failed", reservation_id="reservation-without-runtime",
        admission_status="reserved")

    with pytest.raises(ValueError, match="runtime configuration"):
        _run_projection(monkeypatch, tmp_path, stage, entry, result_log)


def test_reserved_week_projection_without_durable_ledger_sets_blocker(tmp_path, monkeypatch):
    stage, entry = _stage_entry(tmp_path)
    reservation_id, _book, _runtime_path = _runtime_ledger_fixture(tmp_path, stage, entry, settle=True)
    Path(stage["ledger_path"]).unlink()

    result = _run_projection(monkeypatch, tmp_path, stage, entry,
        _report(entry, status="failed", reservation_id=reservation_id, admission_status="reserved"),
        returncode=1)

    assert result["status"] == "failed"
    assert result["cleanup_open"] is False
    assert result["ledger_reconciliation_required"] is True
