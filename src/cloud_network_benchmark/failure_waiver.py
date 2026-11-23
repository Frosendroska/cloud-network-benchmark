"""Explicit source-bound support for the 2026-11-23 pre-DAY failure waiver."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import yaml

WAIVED_ENTRIES = ("V-A-FAIL", "V-Z-FAIL", "V-G-FAIL")
WAIVED_RESOURCES = ("server_fault_injection", "emergency_expiry_wait", "controller_process_ssh_recovery")
NORMAL_ENTRIES = ("V-S1", "V-S3")
SUPPORTED_STAGES = ("acceptance", "calibration", "day")
DECISION_TITLE = "2026-11-23 Waive deliberate pre-DAY fault, expiry and recovery experiments"
RESIDUAL_RISK = (
    "Server-fault injection, forced expiry firing, and controller/process/SSH recovery "
    "remain WAIVED / NOT TESTED; failure-handling behavior is unverified."
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_waiver(settings: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Validate the optional operator declaration; absence preserves strict legacy behavior."""
    stage_inputs = (settings or {}).get("stage_inputs", {})
    acceptance = stage_inputs.get("acceptance", {}) if isinstance(stage_inputs, dict) else {}
    declaration = acceptance.get("failure_waiver") if isinstance(acceptance, dict) else None
    if declaration is None:
        return None
    if (not isinstance(declaration, dict)
            or set(declaration) != {"confirmed", "source", "sha256"}
            or declaration.get("confirmed") is not True):
        raise ValueError("failure_waiver must explicitly confirm and bind source plus sha256")
    source_text = declaration.get("source")
    expected_hash = declaration.get("sha256")
    if not isinstance(source_text, str) or not isinstance(expected_hash, str) or len(expected_hash) != 64:
        raise ValueError("failure_waiver requires an exact source path and SHA-256")
    path = Path(source_text)
    if (not path.is_absolute() or ".." in path.parts or not path.is_file()
            or any(parent.is_symlink() for parent in (path, *path.parents))):
        raise ValueError("failure_waiver source must be a regular absolute file without symlinks")
    actual_hash = _sha(path)
    if actual_hash != expected_hash:
        raise ValueError("failure_waiver source hash changed")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("failure_waiver source is unreadable") from exc
    if DECISION_TITLE not in text:
        raise ValueError("failure_waiver source does not contain the current dated decision")
    return {"schema_version": 1, "status": "WAIVED", "not_tested": list(WAIVED_ENTRIES),
        "waived_resources": list(WAIVED_RESOURCES),
        "supported_stages": list(SUPPORTED_STAGES), "normal_entries": list(NORMAL_ENTRIES),
        "source": {"path": str(path), "sha256": actual_hash}, "residual_risk": RESIDUAL_RISK}


def bind_prepared_acceptance(waiver: Mapping[str, Any], entries: list[Mapping[str, Any]]) -> dict[str, Any]:
    if [entry.get("entry_id") for entry in entries] != list(NORMAL_ENTRIES):
        raise ValueError("waiver binding requires exactly the normal V-S1/V-S3 acceptance entries")
    campaigns = []
    for entry in entries:
        parent_path = Path(entry["parent_manifest_path"])
        parent = json.loads(parent_path.read_text(encoding="utf-8"))
        campaigns.append({"entry_id": entry["entry_id"], "parent_manifest_path": entry["parent_manifest_path"],
            "parent_campaign_id": parent["campaign_id"],
            "config_path": entry["config_path"],
            "config_sha256": entry["config_sha256"], "source_config_sha256": entry["source_config_sha256"],
            "source_config_path": entry["source_config_path"],
            "runtime_config_path": entry["runtime_config_path"],
            "runtime_config_sha256": entry["runtime_config_sha256"],
            "implementation_git_commit": parent.get("implementation_git_commit"),
            "run_ids": dict(sorted(entry["run_ids"].items()))})
    return {**dict(waiver), "normal_acceptance": campaigns}


def validate_prepared_binding(binding: Any, entries: list[Mapping[str, Any]] | None = None) -> None:
    if (not isinstance(binding, dict) or binding.get("schema_version") != 1
            or binding.get("status") != "WAIVED"
            or binding.get("not_tested") != list(WAIVED_ENTRIES)
            or binding.get("waived_resources") != list(WAIVED_RESOURCES)
            or binding.get("supported_stages") != list(SUPPORTED_STAGES)
            or binding.get("normal_entries") != list(NORMAL_ENTRIES)
            or binding.get("residual_risk") != RESIDUAL_RISK):
        raise ValueError("failure waiver is malformed or outside its supported stage/resource/scope")
    source = binding.get("source")
    if not isinstance(source, dict) or set(source) != {"path", "sha256"}:
        raise ValueError("failure waiver source binding is malformed")
    verified = source_waiver({"stage_inputs": {"acceptance": {"failure_waiver": {
        "confirmed": True, "source": source["path"], "sha256": source["sha256"]}}}})
    if verified is None:
        raise ValueError("failure waiver is absent")
    bound = binding.get("normal_acceptance")
    if not isinstance(bound, list) or [row.get("entry_id") for row in bound] != list(NORMAL_ENTRIES):
        raise ValueError("failure waiver does not bind the exact reduced acceptance selection")
    for row in bound:
        for path_key, hash_key in (("config_path", "config_sha256"),
                ("source_config_path", "source_config_sha256"),
                ("runtime_config_path", "runtime_config_sha256")):
            path_text, expected_hash = row.get(path_key), row.get(hash_key)
            path = Path(path_text) if isinstance(path_text, str) else Path(".")
            if (not path.is_absolute() or ".." in path.parts or not path.is_file()
                    or any(parent.is_symlink() for parent in (path, *path.parents))
                    or _sha(path) != expected_hash):
                raise ValueError("failure waiver prepared config/runtime source identity changed")
        parent_text = row.get("parent_manifest_path")
        parent_path = Path(parent_text) if isinstance(parent_text, str) else Path(".")
        if (not parent_path.is_absolute() or not parent_path.is_file()
                or any(parent.is_symlink() for parent in (parent_path, *parent_path.parents))):
            raise ValueError("failure waiver parent manifest identity is missing or unsafe")
        parent = json.loads(parent_path.read_text(encoding="utf-8"))
        if (parent.get("campaign_id") != row.get("parent_campaign_id")
                or parent.get("implementation_git_commit") != row.get("implementation_git_commit")
                or {str(child.get("provider")): child.get("run_id")
                    for child in parent.get("child_runs", [])} != row.get("run_ids")
                or parent.get("config_provenance", {}).get("sha256") != row.get("config_sha256")):
            raise ValueError("failure waiver does not match its exact prepared campaign source identities")
    if entries is not None:
        expected = bind_prepared_acceptance(verified, entries)
        if binding != expected:
            raise ValueError("failure waiver binding does not match the prepared source/run identities")


def registered_failure_record(binding: Mapping[str, Any]) -> dict[str, Any]:
    validate_prepared_binding(binding)
    return {"schema_version": 2, "status": "WAIVED", "checks": {
        entry: {"status": "WAIVED", "result": "NOT TESTED", "provider": provider,
            "stage": "acceptance", "waiver_source": dict(binding["source"]),
            "normal_acceptance": binding["normal_acceptance"],
            "residual_risk": binding["residual_risk"]}
        for entry, provider in zip(WAIVED_ENTRIES, ("aws", "azure", "gcp"))},
        "waiver": {key: binding[key] for key in ("status", "not_tested", "waived_resources",
            "supported_stages", "source", "residual_risk")}}


def validate_registered_failure_record(record: Any) -> None:
    if not isinstance(record, dict) or record.get("schema_version") != 2 or record.get("status") != "WAIVED":
        raise ValueError("registered failure review is missing explicit WAIVED status")
    waiver = record.get("waiver")
    if not isinstance(waiver, dict):
        raise ValueError("registered failure review lacks its source-bound waiver")
    base = {"schema_version": 1, **waiver, "normal_entries": list(NORMAL_ENTRIES)}
    normal = record.get("checks", {}).get(WAIVED_ENTRIES[0], {}).get("normal_acceptance")
    base["normal_acceptance"] = normal
    validate_prepared_binding(base)
    expected = registered_failure_record(base)
    if record != expected:
        raise ValueError("registered failure review was altered or has a different resource/stage scope")


def validate_later_stage(record: Any, stage: str) -> None:
    validate_registered_failure_record(record)
    if stage not in {"calibration", "day"} or stage not in record["waiver"]["supported_stages"]:
        raise ValueError("failure waiver supports only the explicitly listed calibration and DAY stages")
