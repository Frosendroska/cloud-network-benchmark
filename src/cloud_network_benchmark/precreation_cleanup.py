"""Source-bound read-only absence recovery for pre-provider factory failures."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import fcntl
from typing import Any

from .campaign_draft import load_and_verify_draft


_RECOVERY_FLAGS = ("expiry_registered", "creation_may_have_started")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file(path: Path, expected: str, label: str) -> bytes:
    path = Path(path)
    if (not path.is_absolute() or path.is_symlink() or not path.is_file()
            or any(parent.is_symlink() for parent in path.parents)):
        raise ValueError(f"source-bound {label} must be an absolute regular file without symlink parents")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError(f"source-bound {label} bytes differ from the frozen identity")
    return raw


def _entry(draft: dict[str, Any], stage_id: str, entry_id: str) -> dict[str, Any]:
    stages = [row for row in draft["stages"] if row.get("stage_id") == stage_id]
    if len(stages) != 1:
        raise ValueError("frozen source stage is missing or ambiguous")
    entries = [row for row in stages[0].get("entries", []) if row.get("entry_id") == entry_id]
    if len(entries) != 1:
        raise ValueError("frozen source entry is missing or ambiguous")
    if entry_id not in {"V-S1", "V-S3"} or stages[0].get("stage") != "acceptance":
        raise ValueError("precreation cleanup supports only the frozen S1/S3 acceptance entries")
    return entries[0]


def _assert_terminal_factory_and_idle_stage(*, recovery: Any, draft: dict[str, Any],
                                             stage_id: str, entry_id: str,
                                             hold: bool = False) -> list[int]:
    """Fail closed unless failure is durable and the stage driver is idle."""
    if not isinstance(recovery.initialization_failure, str) or not recovery.initialization_failure.strip():
        raise ValueError("precreation recovery requires the original terminal initialization failure")
    stage_rows = [row for row in draft["stages"] if row.get("stage_id") == stage_id]
    if len(stage_rows) != 1:
        raise ValueError("frozen source stage is missing or ambiguous")
    stage = stage_rows[0]
    state_root = Path(stage.get("state_root", ""))
    if not state_root.is_absolute() or state_root.is_symlink():
        raise ValueError("frozen campaign state root is unsafe")
    journal_path = state_root / stage_id / "campaign-journal.json"
    if journal_path.is_symlink() or not journal_path.is_file():
        raise ValueError("terminal stage journal is unavailable")
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    entry_row = journal.get("entries", {}).get(entry_id)
    result = entry_row.get("result") if isinstance(entry_row, dict) else None
    if (not isinstance(entry_row, dict) or entry_row.get("state") != "failed"
            or not isinstance(result, dict) or result.get("cleanup_open") is not True):
        raise ValueError("stage journal does not bind this terminal factory failure to open cleanup")
    # The campaign runner holds both locks while its factory can run. Require
    # both existing lock files and acquire them nonblocking before inventory.
    lock_paths = (state_root / ".campaign-driver.lock",
        state_root / stage_id / "campaign-journal.lock")
    descriptors = []
    try:
        for lock_path in lock_paths:
            if lock_path.is_symlink() or not lock_path.is_file():
                raise ValueError("stage owner lock is unavailable; precreation absence is unproved")
            fd = os.open(lock_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                os.close(fd)
                raise ValueError("campaign factory or stage owner is still active") from exc
            descriptors.append(fd)
    except BaseException:
        for fd in descriptors:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        raise
    if not hold:
        for fd in descriptors:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        return []
    return descriptors


def _verify_entry_paths(*, draft: dict[str, Any], entry: dict[str, Any],
                        config_path: Path, runtime_path: Path,
                        parent_manifest_path: Path) -> dict[str, str]:
    paths = {
        "config": ("config_path", "config_sha256", config_path),
        "runtime": ("runtime_config_path", "runtime_config_sha256", runtime_path),
        "parent_manifest": ("parent_manifest_path", None, parent_manifest_path),
    }
    verified: dict[str, str] = {}
    artifacts = draft["artifacts"]
    for name, (path_key, hash_key, supplied) in paths.items():
        frozen = Path(entry[path_key]).absolute()
        actual = Path(supplied).absolute()
        if actual != frozen:
            raise ValueError(f"explicit {name} path differs from the frozen entry")
        expected = entry.get(hash_key) if hash_key else artifacts.get(str(frozen))
        if not isinstance(expected, str) or artifacts.get(str(frozen)) != expected:
            raise ValueError(f"frozen artifact map omits the exact {name} identity")
        _file(frozen, expected, name)
        verified[name] = expected
    source = Path(entry["source_config_path"]).absolute()
    source_hash = entry["source_config_sha256"]
    # Frozen campaign drafts bind original repository source files in the
    # repository_sources tree map; artifacts binds generated/external files.
    # Require the exact frozen root-relative path and digest from that map.
    roots = [Path(stage["repository_root"]).resolve()
        for stage in draft.get("stages", []) if entry in stage.get("entries", [])]
    if len(roots) != 1:
        raise ValueError("source configuration has no unique frozen repository root")
    try:
        relative_source = source.relative_to(roots[0]).as_posix()
    except ValueError as exc:
        raise ValueError("source configuration is outside its frozen repository root") from exc
    repository_sources = draft.get("repository_sources", {})
    source_map = repository_sources.get(str(roots[0])) if isinstance(repository_sources, dict) else None
    if (not isinstance(repository_sources, dict)
            or not isinstance(source_map, dict) or source_map.get(relative_source) != source_hash):
        raise ValueError("frozen repository source map omits the original source configuration")
    _file(source, source_hash, "source configuration")
    cost = Path(entry["cost_inputs_path"]).absolute()
    cost_hash = artifacts.get(str(cost))
    if not isinstance(cost_hash, str):
        raise ValueError("frozen artifact map omits the original F11 cost input")
    _file(cost, cost_hash, "cost input")
    verified.update(source_config=source_hash, cost_input=cost_hash)
    return verified


def _reservation_binding(record: dict[str, Any]) -> str:
    immutable = ("reservation_id", "evidence", "session", "cap_start_monotonic",
        "cap_seconds", "request", "provider_leads", "child_identities", "plan",
        "allowance_fields", "execution_scope")
    if any(key not in record for key in immutable):
        raise ValueError("original F11 reservation lacks immutable source-binding fields")
    return _digest({key: record[key] for key in immutable})


def _validate_reservation(*, recovery: Any, entry: dict[str, Any], runtime: dict[str, Any],
                          campaign: Any, children: dict[str, Any], costs: Any,
                          ledger: Any, require_open: bool) -> tuple[Any, dict[str, Any], str]:
    if (not recovery.reservation_id or not recovery.ledger_path or not recovery.ledger_scope_id
            or Path(recovery.ledger_path).resolve() != Path(runtime["ledger"]["path"]).resolve()
            or recovery.ledger_scope_id != runtime["ledger"]["scope_id"]):
        raise ValueError("original recovery journal and runtime disagree on the F11 ledger")
    if (recovery.attempt_id != campaign.campaign_id
            or recovery.controller_session_id != ledger.controller_session_id
            or Path(recovery.ledger_path).resolve() != ledger.path.resolve()):
        raise ValueError("original campaign, session or ledger identity differs")
    if set(entry["run_ids"]) != set(children) or entry["run_ids"] != {
            provider: child.run_id for provider, child in children.items()}:
        raise ValueError("frozen entry run IDs differ from the original child manifests")
    if len(recovery.providers) != 3 or {row.provider for row in recovery.providers} != set(children):
        raise ValueError("original recovery journal does not cover the exact three-provider entry")
    for row in recovery.providers:
        if ((require_open and not row.cleanup_open) or row.expiry_registered or row.creation_may_have_started):
            raise ValueError("source replay is limited to unresolved pre-registration/precreation liabilities")
        if Path(row.journal_reference).exists() or Path(row.journal_reference).is_symlink():
            raise ValueError("provider journal exists; source-only precreation recovery is not applicable")
    state = ledger.inspect()
    record = state.get("reservations", {}).get(recovery.reservation_id)
    if (not isinstance(record, dict) or record.get("reservation_id") != recovery.reservation_id
            or record.get("session") != recovery.controller_session_id
            or (require_open and record.get("cleanup_open") is not True)
            or record.get("creation_claims") not in (None, [])
            or record.get("expiry_fingerprint") is not None
            or (require_open and record.get("provider_cleanup") != {provider: False for provider in children})
            or not isinstance(record.get("provider_cleanup"), dict)
            or set(record["provider_cleanup"]) != set(children)
            or (not require_open and any(type(flag) is not bool for flag in record["provider_cleanup"].values()))
            or record.get("child_identities") != entry["run_ids"]):
        raise ValueError("original F11 reservation contains a claim or identity inconsistent with precreation failure")
    for row in recovery.providers:
        acknowledged = record["provider_cleanup"][row.provider]
        if not row.cleanup_open and not acknowledged:
            raise ValueError("recovery and F11 provider cleanup states disagree")
    from .contracts.admission import ReservationEvidence
    reservation = ReservationEvidence.model_validate(record["evidence"])
    if (reservation.state != "reserved" or reservation.campaign_id != campaign.campaign_id
            or reservation.source_path != costs.source_path
            or reservation.source_sha256 != costs.payload_sha256
            or record.get("request", {}).get("source_path") != costs.source_path
            or record.get("request", {}).get("source_sha256") != costs.payload_sha256
            or record.get("plan", {}).get("cost_envelopes") is None):
        raise ValueError("original F11 reservation does not bind the frozen campaign cost source")
    plan_envelopes = record["plan"]["cost_envelopes"]
    if set(plan_envelopes) != set(children):
        raise ValueError("original F11 plan provider set differs from the frozen entry")
    for provider, child in children.items():
        envelope = plan_envelopes[provider]
        if (envelope.get("run_id") != child.run_id
                or envelope.get("source_path") != costs.source_path
                or envelope.get("source_sha256") != costs.payload_sha256):
            raise ValueError("original F11 cost envelope differs from the exact child/source")
    return reservation, record, _reservation_binding(record)


def build_precreation_context(*, draft_path: Path, stage_id: str, entry_id: str,
        config_path: Path, runtime_path: Path, parent_manifest_path: Path,
        recovery_path: Path, repository_root: Path, current_session_id: str,
        original_session_id: str, reservation_id: str, runner: Any, clock: Any,
        allow_reconciled: bool = False) -> tuple[dict[str, Any], dict[str, Any], Any, Any]:
    """Verify exact frozen inputs and derive provider targets without writes/calls."""
    from .admission import BudgetLedger, load_cost_inputs
    from .config import resolve_campaign
    from .lifecycle_runtime import build_provider_workflows, load_runtime_config
    from .orchestration import SavedLifecycleRecovery

    recovery_path = Path(recovery_path)
    if (recovery_path.is_symlink() or not recovery_path.is_file()
            or any(parent.is_symlink() for parent in recovery_path.parents)
            or stat.S_IMODE(recovery_path.stat().st_mode) & 0o077):
        raise ValueError("original recovery journal must remain a private regular file")
    recovery = SavedLifecycleRecovery.validate_unique(json.loads(recovery_path.read_text(encoding="utf-8")))
    if (recovery.controller_session_id != original_session_id
            or recovery.reservation_id != reservation_id):
        raise ValueError("explicit original session/reservation differs from the saved recovery identity")
    draft_path = Path(draft_path)
    source_draft = load_and_verify_draft(draft_path, allow_evidence_for=stage_id)
    entry = _entry(source_draft, stage_id, entry_id)
    if not allow_reconciled:
        _assert_terminal_factory_and_idle_stage(recovery=recovery, draft=source_draft,
            stage_id=stage_id, entry_id=entry_id)
    verified = _verify_entry_paths(draft=source_draft, entry=entry,
        config_path=config_path, runtime_path=runtime_path,
        parent_manifest_path=parent_manifest_path)
    from .manifests import CampaignManifest, ChildRunManifest
    parent = CampaignManifest.model_validate_json(Path(parent_manifest_path).read_text(encoding="utf-8"))
    if parent.campaign_id != entry.get("campaign_id", parent.campaign_id):
        raise ValueError("original parent manifest differs from the frozen campaign identity")
    child_refs = {str(ref.provider): ref for ref in parent.child_runs}
    children = {}
    for provider, run_id in entry["run_ids"].items():
        ref = child_refs.get(provider)
        if ref is None or ref.run_id != run_id:
            raise ValueError("original parent manifest does not contain the frozen provider run")
        manifest_path = (Path(repository_root) / ref.manifest_path).resolve(strict=True)
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise ValueError("original child manifest is unavailable or unsafe")
        child = ChildRunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        if (child.campaign_id != parent.campaign_id or child.run_id != run_id
                or str(child.provider) != provider or child.config_provenance.sha256 != entry["config_sha256"]
                or child.actual_started_at is not None or child.commands or child.timings
                or child.artifact_records):
            raise ValueError("original child manifest shows execution or a different frozen identity")
        children[provider] = child
    selected = {str(provider) for provider in parent.selected_providers}
    if selected != set(children) or selected != {"aws", "azure", "gcp"}:
        raise ValueError("original parent manifest is not the exact three-provider entry")
    campaign, _ = resolve_campaign(Path(config_path), Path(repository_root))
    mode = getattr(campaign.mode, "value", campaign.mode)
    scenario = getattr(campaign.scenario, "value", campaign.scenario)
    if (mode != "acceptance" or scenario not in {"same_zone", "placement_optimization"}
            or campaign.config_source.sha256 != entry["config_sha256"]):
        raise ValueError("frozen config is outside the bounded acceptance recovery scope")
    runtime = load_runtime_config(Path(runtime_path), selected_providers=selected)
    if Path(runtime["initialized_campaign_manifest"]).resolve() != Path(parent_manifest_path).resolve():
        raise ValueError("frozen runtime points to another initialized parent manifest")
    costs = load_cost_inputs(Path(entry["cost_inputs_path"]))
    ledger = BudgetLedger(Path(runtime["ledger"]["path"]), scope_id=runtime["ledger"]["scope_id"],
        utc_now=clock.utc_now, monotonic_now=clock.monotonic,
        controller_session_id=recovery.controller_session_id)
    reservation, record, reservation_hash = _validate_reservation(recovery=recovery,
        entry=entry, runtime=runtime, campaign=campaign, children=children,
        costs=costs, ledger=ledger, require_open=not allow_reconciled)
    context = build_provider_workflows(campaign=campaign, parent=parent, children=children,
        costs=costs, runtime=runtime, reservation=reservation, ledger=ledger,
        runner=runner, clock=clock, stages=None,
        private_root=Path(entry["private_root"]), cleanup_only=True)
    private_root = Path(entry["private_root"]).absolute()
    for row in recovery.providers:
        deployment = context[row.provider]["deployment"]
        if row.provider == "aws":
            from .providers.aws import _namespace
            expected_journal = private_root / "aws" / _namespace(deployment) / deployment.run_id / deployment.deployment_id / "attempt.json"
        elif row.provider == "gcp":
            prefix = hashlib.sha256(deployment.run_id.encode()).hexdigest()[:12]
            expected_journal = private_root / "gcp" / prefix / deployment.run_id / deployment.deployment_id / "attempt.json"
        else:
            expected_journal = private_root / "azure" / deployment.run_id / deployment.deployment_id / "recovery.json"
        expected_settings = private_root / "f09-cleanup" / row.provider / f"{deployment.run_id}-{deployment.deployment_id}.json"
        if (row.deployment_id != deployment.deployment_id
                or Path(row.journal_reference).absolute() != expected_journal
                or Path(row.cleanup_settings_reference or "").absolute() != expected_settings
                or expected_journal.exists() or expected_journal.is_symlink()
                or expected_settings.exists() or expected_settings.is_symlink()):
            raise ValueError("original provider recovery/settings references differ from exact frozen targets or already exist")
    target_selectors = {provider: item["expiry_request"].target_selector
        for provider, item in context.items()}
    binding = {"schema_version": 1, "draft_path": str(draft_path.absolute()),
        "draft_file_sha256": hashlib.sha256(draft_path.read_bytes()).hexdigest(),
        "draft_sha256": source_draft["draft_sha256"], "stage_id": stage_id,
        "entry_id": entry_id, "config_path": str(Path(config_path).absolute()),
        "config_sha256": verified["config"],
        "source_config_path": str(Path(entry["source_config_path"]).absolute()),
        "source_config_sha256": verified["source_config"],
        "runtime_config_path": str(Path(runtime_path).absolute()),
        "runtime_config_sha256": verified["runtime"],
        "parent_manifest_path": str(Path(parent_manifest_path).absolute()),
        "parent_manifest_sha256": verified["parent_manifest"],
        "cost_input_path": str(Path(entry["cost_inputs_path"]).absolute()),
        "cost_input_sha256": verified["cost_input"],
        "ledger_path": str(ledger.path.resolve()), "ledger_scope_id": ledger.scope_id,
        "reservation_id": recovery.reservation_id,
        "reservation_binding_sha256": reservation_hash,
        "original_session_id": recovery.controller_session_id,
        "campaign_id": parent.campaign_id, "run_ids": dict(entry["run_ids"]),
        "target_selectors": target_selectors}
    return context, binding, ledger, recovery


def _absence_resource_ids(binding: dict[str, Any], provider: str) -> list[str]:
    selector = binding["target_selectors"][provider]
    return [f"selector:{provider}:{selector}"]


def _receipt_directory(record: Any) -> Path:
    if not record.cleanup_settings_reference:
        raise ValueError("precreation receipt lacks its frozen private destination")
    return Path(record.cleanup_settings_reference).parent.parent / "precreation-receipts"


def _persist_absence_receipt(record: Any, *, recovery: Any, binding: dict[str, Any],
        proof: dict[str, Any], utc_now) -> dict[str, Any]:
    from .admission import Reconciliation
    from .lifecycle_cleanup import _private_receipt

    provider = record.provider
    if proof.get("provider") != provider or proof.get("verified") is not True:
        raise ValueError("source-bound provider inventory did not prove absence")
    if proof.get("target_selector") != binding.get("target_selectors", {}).get(provider):
        raise ValueError("source-bound provider inventory selector differs from frozen target")
    absent_ids = _absence_resource_ids(binding, provider)
    payload = {"schema_version": 1, "kind": "precreation_source_bound_absence",
        "provider": provider, "reservation_id": recovery.reservation_id,
        "deployment_id": record.deployment_id, "binding": binding,
        "binding_sha256": _digest(binding), "target_selector": proof.get("target_selector"),
        "absence_proof": proof, "absence_proved": True,
        "absent_resource_ids": absent_ids, "occurred_at": utc_now().isoformat(),
        "billing_final": False, "incurred_usd": None, "funding_released": False}
    raw = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()
    digest = hashlib.sha256(raw).hexdigest()
    directory = _receipt_directory(record)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if any(parent.is_symlink() for parent in (directory, *directory.parents)):
        raise ValueError("precreation receipt path cannot contain symlink parents")
    receipt_path = directory / f"{recovery.reservation_id}-{provider}-precreation-absence-{digest[:16]}.json"
    _private_receipt(receipt_path, payload)
    event = Reconciliation(event_id=f"precreation-absence-{recovery.reservation_id}-{provider}-{digest[:16]}",
        reservation_id=recovery.reservation_id, provider=provider, source_sha256=digest,
        incurred_usd=None, actual=False, cleanup_verified_absent=True,
        billable_resources_present=False, cleanup_status_reported=True,
        occurred_at=datetime.fromisoformat(payload["occurred_at"]), billing_final=False,
        billing_amount_kind="cumulative", absence_evidence_id=f"{provider}-precreation-{digest[:16]}",
        absent_resource_ids=tuple(absent_ids), source_path=str(receipt_path))
    return {"ledger_event": event, "absence_proved": True,
        "billable_resources_present": False, "cleanup_open": False,
        "billing_final": False, "incurred_usd": None}


def replay_precreation_absence_receipt(record: Any, *, recovery: Any, ledger: Any,
                                       recovery_path: Path) -> bool:
    """Replay only a digest-valid source-bound read-only absence receipt."""
    if not record.cleanup_settings_reference or not recovery.reservation_id:
        return False
    directory = _receipt_directory(record)
    candidates = sorted(directory.glob(
        f"{recovery.reservation_id}-{record.provider}-precreation-absence-*.json")) if directory.is_dir() else []
    if not candidates:
        return False
    if len(candidates) != 1:
        raise ValueError("multiple source-bound precreation receipts are ambiguous")
    path = candidates[0]
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError("source-bound precreation receipt is unsafe")
    raw = path.read_bytes()
    receipt = json.loads(raw)
    digest = hashlib.sha256(raw).hexdigest()
    if (path.name != f"{recovery.reservation_id}-{record.provider}-precreation-absence-{digest[:16]}.json"
            or receipt.get("kind") != "precreation_source_bound_absence"
            or receipt.get("provider") != record.provider
            or receipt.get("reservation_id") != recovery.reservation_id
            or receipt.get("deployment_id") != record.deployment_id
            or receipt.get("absence_proved") is not True
            or receipt.get("billing_final") is not False
            or receipt.get("incurred_usd") is not None
            or receipt.get("funding_released") is not False
            or receipt.get("binding_sha256") != _digest(receipt.get("binding"))):
        raise ValueError("source-bound precreation receipt failed its digest or identity checks")
    binding = receipt["binding"]
    from .contracts.execution import SubprocessCommandRunner
    try:
        class Clock:
            @staticmethod
            def utc_now(): return datetime.now(timezone.utc)
            @staticmethod
            def monotonic():
                import time
                return time.monotonic()
        context, verified_binding, _, _ = build_precreation_context(
            draft_path=Path(binding["draft_path"]), stage_id=binding["stage_id"],
            entry_id=binding["entry_id"], config_path=Path(binding["config_path"]),
            runtime_path=Path(binding["runtime_config_path"]),
            parent_manifest_path=Path(binding["parent_manifest_path"]),
            recovery_path=Path(recovery_path),
            repository_root=Path.cwd(), current_session_id=ledger.controller_session_id,
            original_session_id=binding["original_session_id"],
            reservation_id=binding["reservation_id"], runner=SubprocessCommandRunner(),
            clock=Clock(), allow_reconciled=True)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError("source-bound precreation receipt no longer matches frozen sources") from exc
    if (verified_binding != binding or binding["reservation_id"] != recovery.reservation_id
            or record.provider not in context
            or context[record.provider]["deployment"].deployment_id != record.deployment_id
            or receipt.get("target_selector") != binding["target_selectors"].get(record.provider)
            or receipt.get("absent_resource_ids") != _absence_resource_ids(binding, record.provider)):
        raise ValueError("source-bound precreation receipt differs from current deployment targets")
    return _reconcile_replayed_receipt(receipt, digest, record, recovery, ledger, path)


def _reconcile_replayed_receipt(receipt: dict[str, Any], digest: str, record: Any,
                                recovery: Any, ledger: Any, path: Path) -> bool:
    from .admission import Reconciliation
    if (Path(ledger.path).resolve() != Path(recovery.ledger_path).resolve()
            or ledger.scope_id != recovery.ledger_scope_id):
        return False
    state = ledger.inspect()
    row = state.get("reservations", {}).get(recovery.reservation_id, {})
    if row.get("provider_cleanup", {}).get(record.provider) is True:
        return True
    event = Reconciliation(event_id=f"precreation-absence-{recovery.reservation_id}-{record.provider}-{digest[:16]}",
        reservation_id=recovery.reservation_id, provider=record.provider, source_sha256=digest,
        incurred_usd=None, actual=False, cleanup_verified_absent=True,
        billable_resources_present=False, cleanup_status_reported=True,
        occurred_at=datetime.fromisoformat(receipt["occurred_at"]), billing_final=False,
        billing_amount_kind="cumulative", absence_evidence_id=f"{record.provider}-precreation-{digest[:16]}",
        absent_resource_ids=tuple(receipt["absent_resource_ids"]), source_path=str(path))
    ledger.reconcile(event)
    return ledger.inspect().get("reservations", {}).get(recovery.reservation_id, {}).get(
        "provider_cleanup", {}).get(record.provider) is True


def run_precreation_cleanup(*, draft_path: Path, stage_id: str, entry_id: str,
        config_path: Path, runtime_path: Path, parent_manifest_path: Path,
        recovery_path: Path, repository_root: Path, current_session_id: str,
        original_session_id: str, reservation_id: str, runner: Any,
        utc_now, monotonic_now):
    """Run verified read-only absence checks and reconcile the original liability."""
    from .orchestration import cleanup_lifecycle
    class Clock:
        @staticmethod
        def utc_now(): return utc_now()
        @staticmethod
        def monotonic(): return monotonic_now()
    clock = Clock()
    contexts, binding, ledger, recovery = build_precreation_context(
        draft_path=draft_path, stage_id=stage_id, entry_id=entry_id,
        config_path=config_path, runtime_path=runtime_path,
        parent_manifest_path=parent_manifest_path, recovery_path=recovery_path,
        repository_root=repository_root, current_session_id=current_session_id,
        original_session_id=original_session_id, reservation_id=reservation_id,
        runner=runner, clock=clock, allow_reconciled=True)
    if any(Path(row.cleanup_settings_reference).exists()
            for row in recovery.providers if row.cleanup_settings_reference):
        raise ValueError("partial saved provider settings require the ordinary cleanup path")
    proofs: dict[str, dict[str, Any]] = {}
    def inspect(record: Any):
        context = contexts[record.provider]
        deployment = context["deployment"]
        adapter = context["driver_or_options"]
        if record.provider == "azure":
            from .azure_provider import verify_unstarted_absence
            proof = verify_unstarted_absence(deployment, adapter, runner,
                clock=clock.monotonic, utc_now=clock.utc_now)
        else:
            proof = adapter.verify_unstarted_absence(deployment)
        proofs[record.provider] = proof
        return proof
    def reconcile(record: Any, proof: Any):
        if not isinstance(proof, dict) or proofs.get(record.provider) is not proof:
            raise ValueError("precreation absence proof changed before F11 reconciliation")
        return _persist_absence_receipt(record, recovery=recovery, binding=binding,
            proof=proof, utc_now=clock.utc_now)
    locked = _assert_terminal_factory_and_idle_stage(recovery=recovery,
        draft=load_and_verify_draft(Path(draft_path), allow_evidence_for=stage_id),
        stage_id=stage_id, entry_id=entry_id, hold=True)
    try:
        return cleanup_lifecycle(Path(recovery_path), current_session_id=current_session_id,
            cleanup_actions={provider: inspect for provider in contexts},
            reconcile=reconcile, ledger=ledger)
    finally:
        for fd in locked:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
