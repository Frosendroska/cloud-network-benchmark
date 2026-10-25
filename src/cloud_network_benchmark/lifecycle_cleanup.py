"""Cleanup-only reconstruction from the private, source-bound F09 attempt."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any

from pydantic import Field, model_validator

from .contracts.admission import ExpiryRequest, ReservationEvidence
from .contracts.common import Provider, StrictModel
from .contracts.deployment import ProviderDeploymentInput


class SavedProviderCleanupSettings(StrictModel):
    schema_version: int = Field(strict=True, ge=1, le=1)
    provider: Provider
    deployment: ProviderDeploymentInput
    reservation: ReservationEvidence
    expiry_request: ExpiryRequest
    provider_lead_seconds: float = Field(gt=0)
    settings: dict[str, Any]
    snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def source_and_identity(self):
        payload = self.model_dump(mode="json", exclude={"snapshot_sha256"})
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        if hashlib.sha256(raw).hexdigest() != self.snapshot_sha256:
            raise ValueError("saved provider cleanup settings digest does not match its bytes")
        if self.deployment.provider != self.provider or self.deployment.reservation.value != self.reservation:
            raise ValueError("saved provider cleanup identity differs from its reservation")
        if (self.expiry_request.provider != self.provider or self.expiry_request.run_id != self.deployment.run_id
                or self.expiry_request.deployment_id != self.deployment.deployment_id
                or self.expiry_request.reservation_id.value != self.reservation.reservation_id.value):
            raise ValueError("saved expiry request differs from its original deployment")
        return self


def save_cleanup_settings(*, provider: str, deployment: ProviderDeploymentInput,
        request: ExpiryRequest, reservation: ReservationEvidence, provider_lead_seconds: float, settings: Any,
        private_root: Path) -> Path:
    provider = Provider(provider).value
    root = Path(private_root)
    cleanup_root = root / "f09-cleanup"
    folder = cleanup_root / provider
    if any(path.is_symlink() for path in (root, cleanup_root, folder)):
        raise ValueError("private cleanup settings directory cannot be a symlink")
    for path in (root, cleanup_root, folder):
        path.mkdir(mode=0o700, parents=False, exist_ok=True)
        os.chmod(path, 0o700)
    target = folder / f"{deployment.run_id}-{deployment.deployment_id}.json"
    settings_value = settings.model_dump(mode="json") if hasattr(settings, "model_dump") else settings
    body = {"schema_version": 1, "provider": provider,
        "deployment": deployment.model_dump(mode="json"),
        "reservation": reservation.model_dump(mode="json"),
        "expiry_request": request.model_dump(mode="json"),
        "provider_lead_seconds": provider_lead_seconds, "settings": settings_value}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    payload = {**body, "snapshot_sha256": hashlib.sha256(raw).hexdigest()}
    serialized = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()
    if target.exists() or target.is_symlink():
        if target.is_symlink() or target.read_bytes() != serialized:
            raise FileExistsError("saved provider cleanup settings already differ from this attempt")
        return target
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(serialized); stream.flush(); os.fsync(stream.fileno())
    return target


def load_cleanup_settings(path: Path) -> SavedProviderCleanupSettings:
    path = Path(path)
    if path.is_symlink() or not path.is_file() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("saved provider cleanup settings must be a regular file without symlink parents")
    if os.stat(path).st_mode & 0o077:
        raise PermissionError("saved provider cleanup settings must remain private")
    return SavedProviderCleanupSettings.model_validate_json(path.read_text(encoding="utf-8"))


class CleanupClock:
    @staticmethod
    def monotonic() -> float: return time.monotonic()
    @staticmethod
    def utc_now() -> datetime: return datetime.now(timezone.utc)


def _expected_journal(saved: SavedProviderCleanupSettings) -> Path:
    deployment = saved.deployment
    if saved.provider == Provider.AWS:
        from .providers import aws
        settings = __import__("cloud_network_benchmark.providers.aws", fromlist=["AwsRuntimeSettings"]).AwsRuntimeSettings.model_validate(saved.settings)
        return settings.private_state_root / aws._namespace(deployment) / deployment.run_id / deployment.deployment_id / "attempt.json"
    if saved.provider == Provider.GCP:
        settings = __import__("cloud_network_benchmark.providers.gcp", fromlist=["GcpRuntimeSettings"]).GcpRuntimeSettings.model_validate(saved.settings)
        prefix = hashlib.sha256(deployment.run_id.encode()).hexdigest()[:12]
        return settings.private_state_root / prefix / deployment.run_id / deployment.deployment_id / "attempt.json"
    from .azure_provider import AzureRuntimeOptions
    options = AzureRuntimeOptions.model_validate(saved.settings)
    return options.attempt_root / "recovery.json"


def _validate_request(saved: SavedProviderCleanupSettings) -> None:
    d, r, reservation = saved.deployment, saved.expiry_request, saved.reservation
    from datetime import timedelta
    if r.expires_at.value != reservation.expires_at.value - timedelta(seconds=saved.provider_lead_seconds):
        raise ValueError("saved provider expiry request no longer matches its original F11 lead")
    if saved.provider == Provider.AWS:
        from .providers.aws import AwsProviderDriver, AwsRuntimeSettings, SAFETY_LEAD_SECONDS
        settings = AwsRuntimeSettings.model_validate(saved.settings)
        driver = AwsProviderDriver.__new__(AwsProviderDriver); driver.settings = settings
        expected_namespace = __import__("cloud_network_benchmark.providers.aws", fromlist=["_namespace"])._namespace(d)
        expected_target = driver._target_selector(d, driver._regions(d))
        expected_action = "stop"
        from .contracts.common import Provider as P
        if (r.resource_namespace != expected_namespace or r.target_selector != expected_target
                or r.action != expected_action or saved.provider_lead_seconds != SAFETY_LEAD_SECONDS
                or r.expires_at.value != reservation.expires_at.value - timedelta(seconds=SAFETY_LEAD_SECONDS)):
            raise ValueError("saved AWS request target differs from the original deployment")
    elif saved.provider == Provider.GCP:
        from .providers.gcp import GcpRuntimeSettings, SAFETY_LEAD_SECONDS
        settings = GcpRuntimeSettings.model_validate(saved.settings)
        expected = f"project={settings.project_id};run_id={d.run_id};deployment_id={d.deployment_id};roles=vm_a,vm_b"
        if (r.resource_namespace != d.run_id or r.target_selector != expected or r.action != "stop"
                or saved.provider_lead_seconds != SAFETY_LEAD_SECONDS
                or r.expires_at.value != reservation.expires_at.value - timedelta(seconds=SAFETY_LEAD_SECONDS)):
            raise ValueError("saved GCP request target differs from the original deployment")
    else:
        from . import azure_provider
        from .azure_provider import AzureRuntimeOptions
        options = AzureRuntimeOptions.model_validate(saved.settings)
        name = azure_provider._slug(d)
        ids = [f"/subscriptions/{options.subscription_id}/resourceGroups/{name}/providers/Microsoft.Compute/virtualMachines/{name}-{role}" for role in ("a", "b")]
        if r.resource_namespace != name or r.target_selector != ";".join(ids) or r.action != "deallocate":
            raise ValueError("saved Azure request target differs from the original deployment")


def cleanup_saved_provider(record: Any, *, runner: Any, utc_now=None, monotonic_now=None) -> tuple[Any, SavedProviderCleanupSettings, list[str]]:
    if not record.cleanup_settings_reference:
        raise ValueError("saved recovery record has no original provider settings reference")
    saved = load_cleanup_settings(Path(record.cleanup_settings_reference))
    if (record.provider != str(saved.provider) or record.deployment_id != saved.deployment.deployment_id
            or _expected_journal(saved).resolve() != Path(record.journal_reference).resolve()):
        raise ValueError("saved provider settings do not match the exact recovery journal reference")
    _validate_request(saved)
    d = saved.deployment
    clock = CleanupClock()
    utc_now = utc_now or clock.utc_now
    monotonic_now = monotonic_now or clock.monotonic
    if saved.provider == Provider.AWS:
        from .providers.aws import AwsProviderDriver, AwsRuntimeSettings
        result = AwsProviderDriver(runner, AwsRuntimeSettings.model_validate(saved.settings),
            clock=utc_now).cleanup(d)
        journal = Path(record.journal_reference)
        absent_ids = _journal_ids(journal, "known_resource_ids")
        try:
            absent_ids.extend(_nested_resource_ids(json.loads(journal.read_text()).get("safety", {})))
        except (OSError, json.JSONDecodeError):
            pass
    elif saved.provider == Provider.GCP:
        from .providers.gcp import GcpProviderDriver, GcpRuntimeSettings
        result = GcpProviderDriver(runner, GcpRuntimeSettings.model_validate(saved.settings),
            clock=utc_now).cleanup(d)
        journal = Path(record.journal_reference)
        absent_ids = _journal_ids(journal, "observed_resources") + _journal_ids(journal, "observed_disks")
        try:
            state = json.loads(journal.read_text())
            absent_ids.extend(_nested_resource_ids(state.get("templates", {})))
            absent_ids.extend(_nested_resource_ids(state.get("observed_resources", {})))
            absent_ids.extend(_nested_resource_ids(state.get("observed_disks", [])))
        except (OSError, json.JSONDecodeError):
            pass
    else:
        from . import azure_provider
        from .azure_provider import AzureRuntimeOptions
        options = AzureRuntimeOptions.model_validate(saved.settings)
        try:
            state = json.loads(Path(record.journal_reference).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
        absent_ids = [value for value in state.get("vm_ids", ()) if isinstance(value, str)]
        prereq = state.get("expiry", {})
        absent_ids.extend(value for key, value in prereq.items()
            if key.endswith("_id") and isinstance(value, str) and value.startswith("/subscriptions/"))
        result = azure_provider.destroy(d, options, runner, clock=monotonic_now, utc_now=utc_now)
    return result, saved, list(dict.fromkeys(item for item in absent_ids if isinstance(item, str) and item.strip()))


def _nested_resource_ids(value: Any) -> list[str]:
    rows = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"id", "resource_id", "instance_id", "volume_id", "network_interface_id", "arn", "self_link_unique"} and isinstance(item, str) and item.strip():
                rows.append(item)
            else:
                rows.extend(_nested_resource_ids(item))
    elif isinstance(value, (list, tuple)):
        for item in value: rows.extend(_nested_resource_ids(item))
    return rows


def _journal_ids(path: Path, key: str) -> list[str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get(key, {})
    except (OSError, json.JSONDecodeError):
        return []
    return _nested_resource_ids(value)


def _private_receipt(path: Path, value: dict[str, Any]) -> tuple[Path, bytes]:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError("cleanup receipt path cannot contain symlinks")
    raw = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    if path.exists():
        if path.read_bytes() != raw:
            raise FileExistsError("cleanup reconciliation receipt is immutable")
        return path, raw
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    try:
        os.link(temporary, path)
    except FileExistsError:
        if path.read_bytes() != raw:
            raise
    finally:
        temporary.unlink(missing_ok=True)
    return path, raw


def _result_is_absent(provider: Provider, result: Any, absent_ids: list[str]) -> bool:
    from .contracts.common import CleanupState
    state = getattr(result, "cleanup_state", None)
    if state not in {CleanupState.SUCCEEDED, CleanupState.NOT_REQUIRED} or not absent_ids:
        return False
    if provider == Provider.AWS:
        inventory = result.inventory.value if result.inventory else None
        if not isinstance(inventory, dict) or inventory.get("complete") is not True:
            return False
        regions, safety = inventory.get("regions"), inventory.get("safety_absent")
        return (isinstance(regions, dict) and bool(regions)
            and all(row.get("complete") is True and not row.get("remaining") for row in regions.values())
            and isinstance(safety, dict) and bool(safety) and all(value is True for value in safety.values()))
    if provider == Provider.GCP:
        inventory = result.inventory.value if result.inventory else None
        return (isinstance(inventory, dict) and inventory.get("pages_complete") is True
            and not inventory.get("resources")
            and all(not inventory.get(name) for name in ("instances", "disks", "addresses", "support_resources")))
    # Azure's typed cleanup retains the pre-expiry Terraform inventory in
    # `resources`; the owned Automation account is expected there before the
    # expiry resources are destroyed and its exact absence is checked. Treat
    # that trace as compatible with absence only when its ID was recorded in
    # the saved recovery journal. Unexpected resource identities remain a
    # cleanup blocker.
    return (result.cleanup_state == CleanupState.SUCCEEDED and result.launch_blocked is False
        and all(item.get("type") == "Microsoft.Automation/automationAccounts"
            and item.get("id") in absent_ids for item in result.resources))


def build_cleanup_runtime(*, recovery_path: Path, recovery: Any, current_session_id: str, runner: Any,
                          utc_now, monotonic_now):
    """Rebuild only the saved cleanup and reconciliation path; no admission or launch path."""
    from .admission import BudgetLedger, Reconciliation
    from .orchestration import cleanup_lifecycle

    if not recovery.ledger_path or not recovery.ledger_scope_id or not recovery.reservation_id:
        raise ValueError("saved attempt lacks its original F11 ledger reference")
    ledger_path = Path(recovery.ledger_path)
    if ledger_path.is_symlink() or not ledger_path.is_file() or any(p.is_symlink() for p in ledger_path.parents):
        raise ValueError("saved original F11 ledger is unavailable or unsafe")
    ledger = BudgetLedger(ledger_path, scope_id=recovery.ledger_scope_id,
        utc_now=utc_now, monotonic_now=monotonic_now,
        controller_session_id=current_session_id)
    try:
        reservation_record = ledger.inspect()["reservations"][recovery.reservation_id]
    except (KeyError, TypeError) as exc:
        raise ValueError("saved reservation is missing from its original F11 ledger") from exc
    saved_reservation = ReservationEvidence.model_validate(reservation_record["evidence"])
    outputs: dict[str, tuple[Any, SavedProviderCleanupSettings, list[str], str | None]] = {}

    def cleanup(record: Any):
        try:
            result, saved, absent_ids = cleanup_saved_provider(record, runner=runner,
                utc_now=utc_now, monotonic_now=monotonic_now)
            if saved.reservation.reservation_id.value != recovery.reservation_id:
                raise ValueError("provider cleanup settings reference a different F11 reservation")
            if saved.reservation.model_dump(mode="json") != saved_reservation.model_dump(mode="json"):
                raise ValueError("provider cleanup reservation differs from the original ledger evidence")
            outputs[record.provider] = (result, saved, absent_ids, None)
            return result
        except BaseException as exc:
            saved = load_cleanup_settings(Path(record.cleanup_settings_reference))
            outputs[record.provider] = (None, saved, [], f"{type(exc).__name__}: {str(exc)[:300]}")
            raise

    def reconcile(record: Any, result: Any):
        prior = outputs.get(record.provider)
        if prior is None:
            if not record.cleanup_settings_reference:
                raise ValueError("cleanup receipt cannot be associated with saved settings")
            saved = load_cleanup_settings(Path(record.cleanup_settings_reference))
            absent_ids, failure = [], "cleanup adapter did not return typed evidence"
        else:
            result, saved, absent_ids, failure = prior
        if result is not None and result is not prior[0]:
            raise ValueError("cleanup callback result changed before reconciliation")
        typed = result.model_dump(mode="json") if result is not None and hasattr(result, "model_dump") else None
        verified_absent = result is not None and _result_is_absent(saved.provider, result, absent_ids)
        at = utc_now()
        receipt = {"schema_version": 1, "provider": str(saved.provider),
            "reservation_id": recovery.reservation_id, "deployment_id": saved.deployment.deployment_id,
            "occurred_at": at.isoformat(), "cleanup_result": typed,
            "failure": failure, "absence_proved": verified_absent,
            "absent_resource_ids": sorted(set(absent_ids)) if verified_absent else []}
        raw = (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode()
        digest = hashlib.sha256(raw).hexdigest()
        receipt_path = Path(record.cleanup_settings_reference).with_name(
            f"{recovery.reservation_id}-{record.provider}-cleanup-{digest[:16]}.json")
        receipt_path, raw = _private_receipt(receipt_path, receipt)
        result_failure = None
        if not verified_absent:
            cleanup_fail = getattr(result, "cleanup_failure", None) if result is not None else None
            if cleanup_fail is not None:
                result_failure = {"category": cleanup_fail.category, "message": cleanup_fail.message[:500]}
            elif failure:
                result_failure = {"category": "cleanup_incomplete", "message": failure}
        event = Reconciliation(event_id=f"cleanup-{recovery.reservation_id}-{record.provider}-{digest[:16]}",
            reservation_id=recovery.reservation_id, provider=record.provider,
            source_sha256=digest, source_path=str(receipt_path), incurred_usd=None,
            actual=False, cleanup_verified_absent=verified_absent,
            billable_resources_present=False if verified_absent else None,
            cleanup_status_reported=True, failure=result_failure, occurred_at=at,
            billing_final=False, billing_amount_kind="cumulative",
            absence_evidence_id=(f"{record.provider}-cleanup-{digest[:16]}" if verified_absent else None),
            absent_resource_ids=tuple(sorted(set(absent_ids))) if verified_absent else ())
        return {"ledger_event": event, "absence_proved": verified_absent,
            "billable_resources_present": False if verified_absent else None,
            # Provider-local clearance is independent of whether peer providers
            # have completed their own cleanup event in this sequential loop.
            "cleanup_open": not verified_absent, "billing_final": False,
            "incurred_usd": None}

    return cleanup_lifecycle(Path(recovery_path), current_session_id=current_session_id,
        cleanup_actions={row.provider: cleanup for row in recovery.providers},
        reconcile=reconcile, ledger=ledger)
