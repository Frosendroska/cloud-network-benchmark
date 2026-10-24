"""Strict, source-bound F09 runtime configuration loading."""
from __future__ import annotations

import json
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
    if not isinstance(access["guest_remote_root"], str) or not access["guest_remote_root"].startswith("/"):
        raise ValueError("guest_remote_root must be absolute")
    providers = root["providers"]
    if not isinstance(providers, dict) or not providers or set(providers) - {"aws", "azure", "gcp"}:
        raise ValueError("providers must select one or more supported providers")
    if selected_providers is not None and set(providers) != selected_providers:
        raise ValueError("runtime providers must exactly match the selected campaign providers")
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
    return root
