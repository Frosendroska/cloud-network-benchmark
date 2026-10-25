"""Cleanup-only reconstruction from the private, source-bound F09 attempt."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import stat
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
        absent_ids = _known_resource_ids(journal)
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


def _known_resource_ids(path: Path) -> list[str]:
    """Read only the provider journal's explicit category/region ID arrays."""
    try:
        categories = json.loads(path.read_text(encoding="utf-8")).get("known_resource_ids", {})
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    values = []
    if isinstance(categories, dict):
        for regions in categories.values():
            if isinstance(regions, dict):
                for ids in regions.values():
                    if isinstance(ids, list):
                        values.extend(item for item in ids if isinstance(item, str) and item.strip())
    return list(dict.fromkeys(values))


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
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return path, raw


def _result_is_absent(provider: Provider, result: Any, absent_ids: list[str]) -> bool:
    from .contracts.common import CleanupState
    state = getattr(result, "cleanup_state", None)
    if state not in {CleanupState.SUCCEEDED, CleanupState.NOT_REQUIRED}:
        return False
    if provider == Provider.AWS:
        if not absent_ids:
            return False
        inventory = result.inventory.value if result.inventory else None
        if not isinstance(inventory, dict) or inventory.get("complete") is not True:
            return False
        regions, safety = inventory.get("regions"), inventory.get("safety_absent")
        return (isinstance(regions, dict) and bool(regions)
            and all(row.get("complete") is True and not row.get("remaining") for row in regions.values())
            and isinstance(safety, dict) and bool(safety) and all(value is True for value in safety.values()))
    if provider == Provider.GCP:
        if not absent_ids:
            return False
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
        and isinstance(result.resources, list)
        and all(item.get("type") == "Microsoft.Automation/automationAccounts"
            and item.get("id") in absent_ids for item in result.resources))


def reconcile_saved_cleanup_result(record: Any, result: Any, *, recovery: Any,
                                   ledger: Any, utc_now,
                                   provider_resource_ids: list[str] | None = None) -> dict[str, Any]:
    """Persist and construct the same source-bound F11 receipt for either cleanup path.

    This helper does not mutate the F11 ledger or clear recovery flags. Callers
    must durably reconcile its event first, then clear the corresponding
    recovery liability.
    """
    from .admission import Reconciliation

    if not record.cleanup_settings_reference:
        raise ValueError("cleanup receipt cannot be associated with saved settings")
    saved = load_cleanup_settings(Path(record.cleanup_settings_reference))
    if (record.provider != str(saved.provider) or record.deployment_id != saved.deployment.deployment_id
            or saved.reservation.reservation_id.value != recovery.reservation_id):
        raise ValueError("cleanup receipt identity differs from the saved attempt")
    if not recovery.ledger_path or not recovery.ledger_scope_id or not recovery.reservation_id:
        raise ValueError("saved attempt lacks its original F11 ledger reference")
    if (Path(ledger.path).resolve() != Path(recovery.ledger_path).resolve()
            or ledger.scope_id != recovery.ledger_scope_id):
        raise ValueError("cleanup reconciliation did not use the original F11 ledger")
    try:
        reservation = ledger.inspect()["reservations"][recovery.reservation_id]
        expected = ReservationEvidence.model_validate(reservation["evidence"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("original F11 reservation evidence is unavailable") from exc
    if saved.reservation.model_dump(mode="json") != expected.model_dump(mode="json"):
        raise ValueError("saved provider cleanup reservation differs from original F11 evidence")

    journal = Path(record.journal_reference)
    try:
        state = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        state = {}
    if provider_resource_ids is not None:
        if (not isinstance(provider_resource_ids, list)
                or any(not isinstance(item, str) or not item.strip() for item in provider_resource_ids)):
            raise ValueError("provider cleanup resource identities are malformed")
        absent_ids = list(dict.fromkeys(provider_resource_ids))
    elif saved.provider == Provider.AWS:
        absent_ids = _known_resource_ids(journal) + _nested_resource_ids(state.get("safety", {}))
    elif saved.provider == Provider.GCP:
        absent_ids = (_journal_ids(journal, "observed_resources") + _journal_ids(journal, "observed_disks")
            + _nested_resource_ids(state.get("templates", {}))
            + _nested_resource_ids(state.get("observed_resources", {}))
            + _nested_resource_ids(state.get("observed_disks", [])))
    else:
        absent_ids = [value for value in state.get("vm_ids", ()) if isinstance(value, str)]
        prereq = state.get("expiry", {})
        absent_ids.extend(value for key, value in prereq.items()
            if key.endswith("_id") and isinstance(value, str) and value.startswith("/subscriptions/"))
        if not absent_ids:
            # Azure removes its private provider journal only after every direct
            # VM/Automation absence check, resource inventory, and RBAC inventory
            # succeeds. `_validate_request` above binds these IDs to that attempt.
            absent_ids = [item for item in saved.expiry_request.target_selector.split(";") if item]
    if saved.provider == Provider.AZURE:
        identity = getattr(result, "identity", None)
        if (identity is None or identity.run_id != saved.deployment.run_id
                or identity.deployment_id != saved.deployment.deployment_id
                or getattr(identity.provider, "value", identity.provider) != str(saved.provider)):
            raise ValueError("Azure cleanup result identity differs from the saved deployment")
    absent_ids = list(dict.fromkeys(item for item in absent_ids if isinstance(item, str) and item.strip()))
    verified_absent = result is not None and _result_is_absent(saved.provider, result, absent_ids)
    at = utc_now()
    typed = result.model_dump(mode="json") if result is not None and hasattr(result, "model_dump") else None
    receipt = {"schema_version": 1, "provider": str(saved.provider),
        "reservation_id": recovery.reservation_id, "deployment_id": saved.deployment.deployment_id,
        "occurred_at": at.isoformat(), "cleanup_result": typed,
        "absence_proved": verified_absent,
        "absent_resource_ids": sorted(set(absent_ids)) if verified_absent else []}
    raw = (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode()
    digest = hashlib.sha256(raw).hexdigest()
    receipt_path = Path(record.cleanup_settings_reference).with_name(
        f"{recovery.reservation_id}-{record.provider}-cleanup-{digest[:16]}.json")
    receipt_path, _ = _private_receipt(receipt_path, receipt)
    failure = None
    if not verified_absent:
        cleanup_fail = getattr(result, "cleanup_failure", None) if result is not None else None
        if cleanup_fail is not None:
            failure = {"category": cleanup_fail.category, "message": cleanup_fail.message[:500]}
        else:
            failure = {"category": "cleanup_incomplete", "message": "provider absence is not proved"}
    event = Reconciliation(event_id=f"cleanup-{recovery.reservation_id}-{record.provider}-{digest[:16]}",
        reservation_id=recovery.reservation_id, provider=record.provider,
        source_sha256=digest, source_path=str(receipt_path), incurred_usd=None,
        actual=False, cleanup_verified_absent=verified_absent,
        billable_resources_present=False if verified_absent else None,
        cleanup_status_reported=True, failure=failure, occurred_at=at,
        billing_final=False, billing_amount_kind="cumulative",
        absence_evidence_id=(f"{record.provider}-cleanup-{digest[:16]}" if verified_absent else None),
        absent_resource_ids=tuple(sorted(set(absent_ids))) if verified_absent else ())
    return {"ledger_event": event, "absence_proved": verified_absent,
        "billable_resources_present": False if verified_absent else None,
        "cleanup_open": not verified_absent, "billing_final": False, "incurred_usd": None}


def replay_saved_cleanup_receipt(record: Any, *, recovery: Any, ledger: Any) -> bool:
    """Verify a durable cleanup receipt and idempotently reconcile its exact event."""
    from .admission import Reconciliation

    if not record.cleanup_settings_reference or not recovery.reservation_id:
        return False
    directory = Path(record.cleanup_settings_reference).parent
    saved = load_cleanup_settings(Path(record.cleanup_settings_reference))
    if (str(saved.provider) != record.provider or saved.deployment.deployment_id != record.deployment_id
            or saved.reservation.reservation_id.value != recovery.reservation_id):
        return False
    if (Path(ledger.path).resolve() != Path(recovery.ledger_path).resolve()
            or ledger.scope_id != recovery.ledger_scope_id):
        return False
    for receipt_path in sorted(directory.glob(f"{recovery.reservation_id}-{record.provider}-cleanup-*.json")):
        if receipt_path.is_symlink():
            continue
        try:
            raw = receipt_path.read_bytes()
            receipt = json.loads(raw)
            digest = hashlib.sha256(raw).hexdigest()
            if (receipt_path.name != f"{recovery.reservation_id}-{record.provider}-cleanup-{digest[:16]}.json"
                    or receipt.get("provider") != record.provider
                    or receipt.get("reservation_id") != recovery.reservation_id
                    or receipt.get("deployment_id") != record.deployment_id
                    or receipt.get("absence_proved") is not True
                    or not receipt.get("absent_resource_ids")):
                continue
            from .contracts.common import Provider
            if saved.provider == Provider.AWS:
                from .providers.aws import AwsCleanupResult
                result = AwsCleanupResult.model_validate(receipt["cleanup_result"])
                expected_ids = _known_resource_ids(_expected_journal(saved))
                state = json.loads(_expected_journal(saved).read_text(encoding="utf-8"))
                expected_ids.extend(_nested_resource_ids(state.get("safety", {})))
                if set(expected_ids) != set(receipt["absent_resource_ids"]):
                    continue
            elif saved.provider == Provider.GCP:
                from .providers.gcp import GcpCleanupResult
                result = GcpCleanupResult.model_validate(receipt["cleanup_result"])
                journal = _expected_journal(saved)
                state = json.loads(journal.read_text(encoding="utf-8"))
                expected_ids = (_journal_ids(journal, "observed_resources")
                    + _journal_ids(journal, "observed_disks")
                    + _nested_resource_ids(state.get("templates", {}))
                    + _nested_resource_ids(state.get("observed_resources", {}))
                    + _nested_resource_ids(state.get("observed_disks", [])))
                if set(expected_ids) != set(receipt["absent_resource_ids"]):
                    continue
            else:
                from .azure_provider import AzureCleanupResult
                result = AzureCleanupResult.model_validate(receipt["cleanup_result"])
                if (result.identity.run_id != saved.deployment.run_id
                        or result.identity.deployment_id != saved.deployment.deployment_id
                        or result.identity.provider != str(saved.provider)):
                    continue
                if not set(saved.expiry_request.target_selector.split(";")) <= set(receipt["absent_resource_ids"]):
                    continue
            if not _result_is_absent(saved.provider, result, receipt["absent_resource_ids"]):
                continue
            if (receipt_path.parent.resolve() != directory.resolve()
                    or stat.S_IMODE(receipt_path.stat().st_mode) & 0o077):
                continue
            event_id = f"cleanup-{recovery.reservation_id}-{record.provider}-{digest[:16]}"
            event = Reconciliation(event_id=event_id, reservation_id=recovery.reservation_id,
                provider=record.provider, source_sha256=digest,
                incurred_usd=None, actual=False, cleanup_verified_absent=True,
                billable_resources_present=False, cleanup_status_reported=True, failure=None,
                occurred_at=datetime.fromisoformat(receipt["occurred_at"]), billing_final=False,
                billing_amount_kind="cumulative", released_funding_by_bucket={},
                source_path=str(receipt_path), absence_evidence_id=f"{record.provider}-cleanup-{digest[:16]}",
                absent_resource_ids=tuple(sorted(set(receipt["absent_resource_ids"]))))
            try:
                ledger.reconcile(event)
            except BaseException as exc:
                # A validated durable receipt is now the recovery source of
                # truth. Never fall through to another provider command when
                # the original F11 acknowledgement could not be persisted.
                raise RuntimeError("original F11 cleanup receipt reconciliation failed") from exc
            return ledger.inspect().get("reservations", {}).get(recovery.reservation_id, {}).get(
                "provider_cleanup", {}).get(record.provider) is True
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return False


def build_cleanup_runtime(*, recovery_path: Path, recovery: Any, current_session_id: str, runner: Any,
                          utc_now, monotonic_now):
    """Rebuild only the saved cleanup and reconciliation path; no admission or launch path."""
    from .admission import BudgetLedger
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
    outputs: dict[str, Any] = {}

    def cleanup(record: Any):
        try:
            result, saved, _absent_ids = cleanup_saved_provider(record, runner=runner,
                utc_now=utc_now, monotonic_now=monotonic_now)
            outputs[record.provider] = result
            return result
        except BaseException as exc:
            outputs[record.provider] = None
            raise

    def reconcile(record: Any, result: Any):
        if record.provider in outputs and result is not outputs[record.provider]:
            raise ValueError("cleanup callback result changed before reconciliation")
        return reconcile_saved_cleanup_result(record, result, recovery=recovery,
            ledger=ledger, utc_now=utc_now)

    return cleanup_lifecycle(Path(recovery_path), current_session_id=current_session_id,
        cleanup_actions={row.provider: cleanup for row in recovery.providers},
        reconcile=reconcile, ledger=ledger)
