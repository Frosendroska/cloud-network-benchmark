"""Strict, source-bound F09 runtime configuration loading."""
from __future__ import annotations

import json
import hashlib
import math
from pathlib import Path
from typing import Any


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate runtime configuration key: {key}")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"{label} must contain exactly: {', '.join(sorted(expected))}")
    return value


def _source_path(root: Path, value: Any, label: str, *, must_exist: bool = True) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty source path")
    candidate = Path(value)
    if ".." in candidate.parts:
        raise ValueError(f"{label} cannot contain parent traversal")
    path = candidate if candidate.is_absolute() else root / candidate
    path = path.absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError(f"{label} cannot traverse symlinks")
    if must_exist and (not path.is_file() or path.is_symlink()):
        raise ValueError(f"{label} must identify an existing regular non-symlink file")
    return path


def load_runtime_config(path: Path, *, selected_providers: set[str] | None = None) -> dict[str, Any]:
    """Load only the bounded F09 runtime vocabulary and resolve local references.

    This parser does not create credentials or grant admission. F11 still
    evaluates every referenced source and rejects missing paid prerequisites.
    """
    path = Path(path).absolute()
    if not path.is_file() or path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("runtime configuration must be a regular non-symlink file")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_object,
                         parse_constant=_constant)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("runtime configuration is not valid UTF-8 JSON") from exc
    root = _keys(raw, {"schema_version", "initialized_campaign_manifest", "ledger", "allowance",
        "copy_retry_seconds", "access", "providers", "approval_refs"}, "runtime config")
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        raise ValueError("runtime configuration schema_version must be integer 1")
    root["initialized_campaign_manifest"] = _source_path(path.parent,
        root["initialized_campaign_manifest"], "initialized_campaign_manifest")
    ledger = _keys(root["ledger"], {"path", "scope_id", "initialize_if_missing"}, "ledger")
    ledger["path"] = _source_path(path.parent, ledger["path"], "ledger.path", must_exist=False)
    if not isinstance(ledger["scope_id"], str) or not ledger["scope_id"].strip():
        raise ValueError("ledger.scope_id must be nonempty")
    if type(ledger["initialize_if_missing"]) is not bool:
        raise ValueError("ledger.initialize_if_missing must be boolean")
    if type(root["copy_retry_seconds"]) not in {int, float} or not math.isfinite(root["copy_retry_seconds"]) or root["copy_retry_seconds"] <= 0:
        raise ValueError("copy_retry_seconds must be positive and finite")
    allowance = _keys(root["allowance"], {"registration_seconds", "provision_seconds",
        "readiness_seconds", "stabilization_seconds", "measurement_seconds",
        "measurement_shutdown_seconds", "retrieval_seconds", "cleanup_seconds",
        "source_id", "provider_leads_seconds", "completed_stages"}, "allowance")
    access = _keys(root["access"], {"timeouts", "readiness", "bootstrap", "guest_remote_root",
        "diagnostic_interfaces"}, "access")
    remote_root = access["guest_remote_root"]
    if (not isinstance(remote_root, str) or not remote_root.startswith("/")
            or Path(remote_root).as_posix() != remote_root
            or any(part in {".", ".."} for part in Path(remote_root).parts)):
        raise ValueError("guest_remote_root must be a normalized absolute path")
    providers = root["providers"]
    if not isinstance(providers, dict) or not providers or set(providers) - {"aws", "azure", "gcp"}:
        raise ValueError("providers must select one or more supported providers")
    if selected_providers is not None and set(providers) != selected_providers:
        raise ValueError("runtime providers must exactly match the selected campaign providers")
    interfaces = access["diagnostic_interfaces"]
    if not isinstance(interfaces, dict) or set(interfaces) != set(providers):
        raise ValueError("diagnostic interfaces must exactly match selected providers")
    for provider, roles in interfaces.items():
        if not isinstance(roles, dict) or set(roles) != {"vm_a", "vm_b"} or any(
                not isinstance(name, str) or not name.strip() for name in roles.values()):
            raise ValueError(f"diagnostic interfaces for {provider} must name vm_a and vm_b")
    if not isinstance(allowance["provider_leads_seconds"], dict) or set(allowance["provider_leads_seconds"]) != set(providers):
        raise ValueError("allowance provider leads must exactly match selected providers")
    if allowance["completed_stages"] != []:
        raise ValueError("initial allowance completed_stages must be empty")
    try:
        from .admission import WorkflowAllowance
        root["allowance"] = WorkflowAllowance(**allowance)
    except (TypeError, ValueError) as exc:
        raise ValueError("allowance does not match the strict F11 workflow contract") from exc
    from .access import TimeoutPolicy
    from .bootstrap import BootstrapConfig
    from .readiness import ReadinessConfig
    access["timeouts"] = TimeoutPolicy.model_validate(access["timeouts"])
    access["readiness"] = ReadinessConfig.model_validate(access["readiness"])
    access["bootstrap"] = BootstrapConfig.model_validate(access["bootstrap"])
    option_keys = {
        "aws": {"account_id", "controller_cidr", "ami_ids", "public_key_path", "authentication_reference"},
        "gcp": {"project_id", "controller_cidr", "images", "public_key_path", "authentication_reference"},
        "azure": {"subscription_id", "tenant_id", "controller_cidr", "selected_image",
            "ssh_public_key_path", "authentication_reference", "poll_seconds", "command_timeout_seconds"},
    }
    refs = {"aws": {"expiry_cost_path", "primary_review_path"},
        "gcp": {"workflow_cost_handoff_path", "support_cost_path", "primary_review_path"},
        "azure": {"expiry_cost_path", "primary_review_path"}}
    for provider, value in providers.items():
        row = _keys(value, {"options", "bootstrap_template", "terraform_directory", "sources"}, f"providers.{provider}")
        options = _keys(row["options"], option_keys[provider], f"providers.{provider}.options")
        for name in ("public_key_path", "ssh_public_key_path"):
            if name in options:
                options[name] = _source_path(path.parent, options[name], f"providers.{provider}.{name}")
        row["bootstrap_template"] = _source_path(path.parent, row["bootstrap_template"], f"providers.{provider}.bootstrap_template")
        tf_dir = Path(row["terraform_directory"])
        if not tf_dir.is_absolute():
            tf_dir = path.parent / tf_dir
        if tf_dir.is_symlink() or not tf_dir.is_dir() or any(parent.is_symlink() for parent in tf_dir.parents):
            raise ValueError(f"providers.{provider}.terraform_directory must be a source directory")
        row["terraform_directory"] = tf_dir
        sources = _keys(row["sources"], refs[provider], f"providers.{provider}.sources")
        for name, source in list(sources.items()):
            if source is not None:
                sources[name] = _source_path(path.parent, source, f"providers.{provider}.sources.{name}")
    if not isinstance(root["approval_refs"], dict):
        raise ValueError("approval_refs must be a map of existing F11 gate names to evidence files")
    allowed_gates = {"f10_offline", "paid_authorization", "quota_review", "paid_acceptance", "common_n",
        "study_window", "calibration_authorized", "diagnostic_day_authorized", "size_export_review",
        "optional_review", "original_failure"}
    if set(root["approval_refs"]) - allowed_gates:
        raise ValueError("approval_refs names an unknown F11 gate")
    root["approval_refs"] = {key: _source_path(path.parent, value, f"approval_refs.{key}")
                              for key, value in root["approval_refs"].items()}
    root["approval_evidence"] = {}
    for key, reference in root["approval_refs"].items():
        try:
            evidence = json.loads(reference.read_text(encoding="utf-8"), object_pairs_hook=_object,
                                  parse_constant=_constant)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"approval evidence {key} is not valid source JSON") from exc
        if not isinstance(evidence, dict):
            raise ValueError(f"approval evidence {key} must be a JSON object")
        declared_path = evidence.get("source_path")
        declared_hash = evidence.get("source_sha256")
        if not isinstance(declared_path, str) or not isinstance(declared_hash, str):
            raise ValueError(f"approval evidence {key} must identify source_path and source_sha256")
        source = _source_path(reference.parent, declared_path, f"approval_evidence.{key}.source_path")
        if hashlib.sha256(source.read_bytes()).hexdigest() != declared_hash:
            raise ValueError(f"approval evidence {key} source hash does not match its bytes")
        root["approval_evidence"][key] = evidence
    return root


def load_initialized_campaign(parent_path: Path, *, repository_root: Path,
                              config_path: Path, schedule_path: Path | None = None):
    """Read existing CLI-init identities without allocating or rewriting them."""
    from .manifests import CampaignManifest, ChildRunManifest

    parent_path = Path(parent_path)
    repository_root = Path(repository_root).resolve(strict=True)
    if parent_path.is_symlink() or not parent_path.is_file():
        raise ValueError("initialized campaign manifest must be a regular source file")
    parent = CampaignManifest.model_validate_json(parent_path.read_text(encoding="utf-8"))
    children = {}
    for reference in parent.child_runs:
        manifest_path = (repository_root / reference.manifest_path).resolve(strict=True)
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise ValueError("initialized child manifest is unavailable")
        child = ChildRunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        if (child.campaign_id != parent.campaign_id or child.provider != reference.provider
                or child.run_id != reference.run_id or child.execution_state != "initialized"
                or child.actual_started_at is not None or child.actual_finished_at is not None
                or child.commands or child.timings or child.artifact_records):
            raise ValueError("initialized child identity has already launched or differs from parent")
        children[str(child.provider)] = child
    selected = {str(provider) for provider in parent.selected_providers}
    if set(children) != selected:
        raise ValueError("initialized parent does not contain exactly the selected provider children")

    def verified_snapshot(relative: str, expected_hash: str, label: str) -> Path:
        target = (repository_root / relative).resolve(strict=True)
        if target.is_symlink() or not target.is_file():
            raise ValueError(f"initialized {label} snapshot is unavailable")
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected_hash:
            raise ValueError(f"initialized {label} snapshot hash does not match its manifest")
        return target

    source = Path(config_path).resolve(strict=True)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    if source_hash != parent.config_provenance.sha256:
        raise ValueError("CLI config bytes differ from the initialized source identity")
    for child in children.values():
        verified_snapshot(child.config_provenance.snapshot_path, child.config_provenance.sha256, "config")
        if child.config_provenance.resolved_snapshot_sha256.value is None:
            raise ValueError("initialized resolved config snapshot hash is unavailable")
        verified_snapshot(child.config_provenance.resolved_snapshot_path,
            child.config_provenance.resolved_snapshot_sha256.value, "resolved config")
        if schedule_path is not None:
            if child.config_provenance.schedule_sha256.value is None or child.config_provenance.schedule_snapshot_path.value is None:
                raise ValueError("initialized schedule snapshot identity is unavailable")
            verified_snapshot(child.config_provenance.schedule_snapshot_path.value,
                child.config_provenance.schedule_sha256.value, "schedule")
    return parent, children


def build_admission_request(*, campaign: Any, parent: Any, children: dict[str, Any],
                            costs: Any, runtime: dict[str, Any]):
    """Build F11 inputs from the initialized identities and exact sourced cost rows."""
    from decimal import Decimal
    from .admission import AdmissionPlan, WorkflowAllowance
    from .contracts.admission import ReservationRequest
    from .contracts.common import Evidence, Provider

    providers = {Provider(item.provider).value: item for item in campaign.observations}
    child_ids = {provider: child.run_id for provider, child in children.items()}
    if set(providers) != set(child_ids) or set(runtime["providers"]) != set(child_ids):
        raise ValueError("runtime, initialized manifests and resolved provider set must match")
    if any(child.execution_state != "initialized" for child in children.values()):
        raise ValueError("initialized child already contains lifecycle execution")
    rows = costs.payload.get("provider_run_envelopes")
    if not isinstance(rows, dict) or set(rows) != set(child_ids):
        raise ValueError("refreshed cost input must contain exact per-provider run envelopes")
    envelopes = {}
    funding: dict[str, Decimal] = {}
    for provider, row in rows.items():
        if not isinstance(row, dict) or row.get("run_id") != child_ids[provider]:
            raise ValueError("cost envelope run identity differs from initialized child")
        envelopes[provider] = {**row, "source_path": costs.source_path,
                               "source_sha256": costs.payload_sha256}
        components = row.get("components")
        if not isinstance(components, dict):
            raise ValueError("cost envelope components are unavailable")
        for component in components.values():
            if not isinstance(component, dict) or not isinstance(component.get("funding_bucket"), str):
                raise ValueError("cost envelope funding source is invalid")
            bucket = component["funding_bucket"]
            try:
                amount = Decimal(str(component["amount_usd"]))
            except Exception as exc:
                raise ValueError("cost envelope amount is invalid") from exc
            funding[bucket] = funding.get(bucket, Decimal("0")) + amount
    estimated = sum((Decimal(str(component["amount_usd"]))
        for row in rows.values() for component in row["components"].values()), Decimal("0"))
    allowance = runtime["allowance"]
    if set(allowance.provider_leads_seconds) != set(child_ids):
        raise ValueError("allowance provider set differs from initialized campaign")
    schedule = campaign.schedule_entry.value
    request = ReservationRequest(schema_version=2, campaign_id=parent.campaign_id,
        config_id=campaign.config_id,
        schedule_entry_id=(Evidence[str](value=schedule.entry_id) if schedule else
            Evidence[str](unavailable_reason="acceptance has no schedule row")),
        selected_providers=[Provider(name) for name in child_ids], mode=campaign.mode,
        priority=campaign.admission.priority, lifetime_cap_seconds=campaign.admission.lifetime_cap_seconds,
        estimated_cost_usd=Evidence[float](value=float(estimated)), source_path=costs.source_path,
        source_sha256=costs.payload_sha256,
        incurred_cost_usd=Evidence[float](unavailable_reason="F11 ledger derives current incurred charges"),
        active_reserved_usd=Evidence[float](unavailable_reason="F11 ledger derives active reservations"),
        outstanding_cleanup=False)
    namespaces = {provider: f"cnb-{child.run_id}-{child.deployment_id}"
                  for provider, child in children.items()}
    prerequisites = runtime.get("approval_evidence", {})
    support = costs.payload.get("provider_support", {})
    plan = AdmissionPlan(campaign, child_ids, namespaces, costs, allowance,
        execution_scope="paid_request", prerequisites=prerequisites,
        funding=funding, provider_support=support,
        original_failure=prerequisites.get("original_failure"),
        optional_reviewed="optional_review" in prerequisites,
        optional_review_evidence=({"priority": prerequisites["optional_review"]}
            if "optional_review" in prerequisites else {}),
        observation_id=next(iter(children.values())).observation_id,
        cost_envelopes=envelopes)
    return request, plan
