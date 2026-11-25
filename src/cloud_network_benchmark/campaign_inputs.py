"""Translate one operator YAML file into existing, source-bound runtime contracts.

This module prepares local contracts and runs bounded read-only identity checks.
It never grants a missing approval or creates cloud resources.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import math
import shutil
import sys
import ipaddress
import uuid
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from .admission import (COST_COMPONENTS, WorkflowAllowance, minimum_protocol_seconds,
    rounded_provider_cost_total)
from .config import resolve_campaign
from .contracts.common import ExperimentMode
from .manifests import initialize_campaign
from .paths import atomic_write_json, write_exclusive
from .scheduling import load_schedule
from .campaign_identity import check_provider_identities
from .storage_admission import validate_storage_review
from .validation_profiles import (ACCEPTANCE_PROFILE, CALIBRATION_PROFILE,
    FAILURE_PROFILE, PROFILE_SPECS, profile_allowance, profile_spec)

STAGES = {"acceptance", "calibration", "day", "week"}
CONFIGS = {
    "s1_acceptance": "configs/tests/exp-903-three-provider-smoke.yaml",
    "aws_failure": "configs/tests/exp-900-single-provider-north.yaml",
    "azure_failure": "configs/tests/azure-901-single-provider-north.yaml",
    "gcp_failure": "configs/tests/exp-902-gcp-single-provider-north.yaml",
    "s3_acceptance": "configs/experiments/exp-003-placement-optimization.yaml",
    "calibration": "configs/experiments/exp-001-calibration.yaml",
    "day": "configs/experiments/exp-001-diagnostic-day.yaml",
    "s1": "configs/experiments/exp-001-multi-provider.yaml",
    "s3": "configs/experiments/exp-003-placement-optimization.yaml",
}
WINDOWS = ("night", "morning", "afternoon", "evening")


class _UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    pairs = loader.construct_pairs(node, deep=deep)
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate YAML key: {key}")
        result[key] = value
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_operator_settings(path: Path) -> dict[str, Any]:
    path = Path(path).resolve(strict=True)
    value = yaml.load(path.read_text(), Loader=_UniqueLoader)
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("operator settings require schema_version: 1")
    value["_settings_path"] = str(path)
    return value


def _path(value: Any, label: str, *, file=False) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"set {label} to a real absolute path")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} must be an absolute path without parent traversal")
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError(f"{label} cannot traverse symlinks")
    if file and not path.is_file():
        raise ValueError(f"missing {label}: {path}")
    return path


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _utc(value: Any, label: str) -> datetime:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"set {label} from reviewed stage timing evidence") from exc
    if result.tzinfo is None:
        raise ValueError(f"{label} requires an explicit UTC offset")
    return result.astimezone(timezone.utc)


def _now(clock):
    return (clock.utc_now() if hasattr(clock, "utc_now") else clock()).astimezone(timezone.utc)


def _selected(plan, settings=None):
    stage = plan.get("stage")
    if stage not in STAGES:
        raise ValueError("stage must be acceptance, calibration, day or week")
    if stage == "acceptance":
        from .failure_waiver import source_waiver
        if source_waiver(settings) is not None:
            return [("V-S1", "s1_acceptance"), ("V-S3", "s3_acceptance")]
        # One registered failure attempt per cloud, with supervised intervention.
        return [("V-S1", "s1_acceptance"), ("V-S3", "s3_acceptance"),
                ("V-A-FAIL", "aws_failure"), ("V-Z-FAIL", "azure_failure"),
                ("V-G-FAIL", "gcp_failure")]
    if stage in {"calibration", "day"}:
        return [("CAL-S1" if stage == "calibration" else "DAY", stage)]
    return [(f"M-S{scenario}-D{day}-{window.upper()}", f"s{scenario}")
            for day in range(1, 8) for window in WINDOWS for scenario in (1, 3)]


def _allowance(settings, stage, providers, *, entry_id=None):
    stage_inputs = settings.get("stage_inputs", {}).get(stage, {})
    configured_profile = stage_inputs.get("workflow_profile")
    if stage == "acceptance" and entry_id in {"V-A-FAIL", "V-Z-FAIL", "V-G-FAIL"}:
        profile = stage_inputs.get("failure_workflow_profile")
    else:
        profile = configured_profile
    if profile is not None:
        spec = profile_spec(profile)
        if spec is None or spec["stage"] != stage:
            raise ValueError(f"unsupported workflow_profile for {stage}")
        row = profile_allowance(profile, set(providers))
        supplied = stage_inputs.get("allowance")
        if supplied is not None:
            normalized = copy.deepcopy(supplied)
            normalized["provider_leads_seconds"] = {p: normalized.get("provider_leads_seconds", {}).get(p) for p in sorted(providers)}
            normalized.pop("source_id", None)
            normalized.pop("completed_stages", None)
            expected = {k: v for k, v in row.items() if k != "completed_stages"}
            def contradicts(value, wanted):
                if value is None:
                    return False
                if isinstance(value, dict) and isinstance(wanted, dict):
                    return any(key not in wanted or contradicts(item, wanted[key]) for key, item in value.items())
                return value != wanted
            if any(contradicts(value, expected.get(key)) for key, value in normalized.items()):
                raise ValueError(f"{profile} requires its complete fixed stage vector")
    else:
        try:
            row = copy.deepcopy(stage_inputs["allowance"])
        except KeyError as exc:
            raise ValueError(f"set stage_inputs.{stage}.allowance from measured setup/copy/cleanup times") from exc
    row["provider_leads_seconds"] = {p: row["provider_leads_seconds"][p] for p in providers}
    row["completed_stages"] = []
    allowance = WorkflowAllowance(**row)
    mode = {"acceptance": "acceptance", "calibration": "calibration",
            "day": "diagnostic_day", "week": "short_study"}[stage]
    if allowance.measurement_seconds < minimum_protocol_seconds(ExperimentMode(mode)) + allowance.measurement_shutdown_seconds:
        raise ValueError(f"{stage} allowance.measurement_seconds does not cover the complete protocol")
    cap = profile_spec(profile)["cap_seconds"] if profile else {"acceptance": 1200, "calibration": 1800, "day": 87600, "week": 1200}[stage]
    barrier = profile_spec(profile)["barrier_seconds"] if profile else 0
    if allowance.total_seconds + barrier + max(allowance.provider_leads_seconds.values()) > cap:
        raise ValueError(f"{stage} complete workflow plus provider expiry lead exceeds registered {cap}s cap")
    if allowance.stabilization_seconds != 60:
        raise ValueError("allowance must preserve 60-second stabilization")
    return row


def _required_gate_keys(stage):
    required = {"f10_offline", "paid_authorization", "quota_review"}
    if stage != "acceptance":
        required.add("paid_acceptance")
    required |= {"acceptance": {"paired_acceptance"}, "calibration": {"calibration_authorized"},
                 "day": {"common_n", "diagnostic_day_authorized", "size_export_review"},
                 "week": {"common_n", "study_window", "weekly_concurrency", "size_export_review"}}[stage]
    return required


def _gate_rows(settings, stage, *, draft=False):
    declarations = {**settings.get("approvals", {}),
                    **settings.get("stage_inputs", {}).get(stage, {}).get("approvals", {})}
    required = _required_gate_keys(stage)
    if draft:
        return {key: declarations.get(key, {}) for key in required}
    for key in sorted(required):
        row = declarations.get(key)
        if not isinstance(row, dict) or row.get("confirmed") is not True:
            raise ValueError(f"explicitly confirm {key} against real source evidence in operator settings")
        _path(row.get("source"), f"approvals.{key}.source", file=True)
        if not isinstance(row.get("fields", {}), dict):
            raise ValueError(f"approvals.{key}.fields must be a map")
    return {key: declarations[key] for key in required}


def _approval_paths(directory: Path, stage: str) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    return {key: str(directory / f"{key}.json") for key in sorted(_required_gate_keys(stage))}


def _azure_auxiliary_owner(entry_id: str, child: Any) -> dict[str, Any]:
    campaign_id, deployment_id = child.campaign_id, child.deployment_id
    suffix = hashlib.sha256(f"{campaign_id}\0{deployment_id}".encode()).hexdigest()[:8]
    stem = re.sub(r"[^a-z0-9-]", "-", f"cnb-{campaign_id}-{deployment_id}".lower()).strip("-")
    slug = stem[:36].rstrip("-") + "-" + suffix
    group = f"/subscriptions/188a5848-76fb-4d57-8f2a-32c09284d486/resourceGroups/{slug}"
    return {"entry_id": entry_id, "run_id": child.run_id, "campaign_id": campaign_id,
        "deployment_id": deployment_id, "config_sha256": child.config_provenance.sha256,
        "resource_group_id": group,
        "vm_ids": [f"{group}/providers/Microsoft.Compute/virtualMachines/{slug}-{role}" for role in ("a", "b")],
        "nic_ids": [f"{group}/providers/Microsoft.Network/networkInterfaces/{slug}-{role}-nic" for role in ("a", "b")]}


def _profile_execution_window(inputs: dict, stage: str) -> dict[str, str]:
    window = inputs.get("execution_window_utc")
    if not isinstance(window, dict) or set(window) != {"not_before_utc", "not_after_utc"}:
        raise ValueError(f"{stage}.execution_window_utc requires not_before_utc and not_after_utc for ready-triggered validation")
    start = _utc(window["not_before_utc"], f"{stage}.execution_window_utc.not_before_utc")
    end = _utc(window["not_after_utc"], f"{stage}.execution_window_utc.not_after_utc")
    if end <= start:
        raise ValueError("validation execution window must be positive")
    return {"not_before_utc": start.isoformat(), "not_after_utc": end.isoformat()}


def _profile_dispatch_window(inputs: dict, stage: str, execution_window: dict[str, str]) -> dict[str, str]:
    """Validate a split dispatch window, preserving the historical shared-window default."""
    if "dispatch_window_utc" not in inputs:
        # Older ready-profile settings used one interval for both dispatch and
        # common measurement start. Preserve that exact, finite behavior.
        return dict(execution_window)
    window = inputs["dispatch_window_utc"]
    if not isinstance(window, dict) or set(window) != {"not_before_utc", "not_after_utc"}:
        raise ValueError(f"{stage}.dispatch_window_utc requires not_before_utc and not_after_utc for ready-triggered validation")
    start = _utc(window["not_before_utc"], f"{stage}.dispatch_window_utc.not_before_utc")
    end = _utc(window["not_after_utc"], f"{stage}.dispatch_window_utc.not_after_utc")
    common_end = _utc(execution_window["not_after_utc"], f"{stage}.execution_window_utc.not_after_utc")
    if end <= start or end > common_end:
        raise ValueError("validation dispatch window must be positive and end no later than the common measurement window")
    return {"not_before_utc": start.isoformat(), "not_after_utc": end.isoformat()}


def _ready_credential_horizon(inputs: dict, stage: str, cap_seconds: int) -> datetime:
    execution_window = _profile_execution_window(inputs, stage)
    dispatch_window = _profile_dispatch_window(inputs, stage, execution_window)
    return _utc(dispatch_window["not_after_utc"], f"{stage}.dispatch_window_utc.not_after_utc") + timedelta(seconds=cap_seconds)


def _acceptance_dispatch_policy(settings, approval_refs):
    declaration = {**settings.get("approvals", {}),
                   **settings.get("stage_inputs", {}).get("acceptance", {}).get("approvals", {})}["paired_acceptance"]
    fields = declaration.get("fields", {})
    inputs = settings.get("stage_inputs", {}).get("acceptance", {})
    ready_triggered = inputs.get("workflow_profile") == ACCEPTANCE_PROFILE
    window = _profile_execution_window(inputs, "acceptance") if ready_triggered else None
    dispatch_window = _profile_dispatch_window(inputs, "acceptance", window) if ready_triggered else None
    if (fields.get("mode") != "paired_acceptance_s1_s3" or fields.get("max_parallel_entries") != 2
            or (ready_triggered and fields.get("execution_window_utc") != window)
            or (ready_triggered and fields.get("dispatch_window_utc", window) != dispatch_window)
            or (not ready_triggered and _utc(fields.get("start_utc"), "approvals.paired_acceptance.fields.start_utc").isoformat()
                != _utc(inputs.get("start_utc"), "stage_inputs.acceptance.start_utc").isoformat())):
        raise ValueError("paired_acceptance must bind the pair and exact approved validation timing policy")
    evidence_path = _path(approval_refs["paired_acceptance"], "paired_acceptance evidence", file=True)
    if ready_triggered:
        return {"policy_id": "acceptance-paired-s1-s3-ready-v1", "group_by": "acceptance_slot",
            "scenarios": ["S1", "S3"], "max_parallel_entries": 2,
            "start_policy": "all_ready", "execution_window": window,
            "dispatch_window_utc": dispatch_window,
            "ready_window_seconds": 600, "evidence_path": str(evidence_path), "evidence_sha256": _sha(evidence_path)}
    start_utc = _utc(inputs.get("start_utc"), "stage_inputs.acceptance.start_utc").isoformat()
    return {"policy_id": "acceptance-paired-s1-s3-v1", "group_by": "acceptance_slot",
        "scenarios": ["S1", "S3"], "max_parallel_entries": 2, "start_utc": start_utc,
        "evidence_path": str(evidence_path), "evidence_sha256": _sha(evidence_path)}


def _weekly_dispatch_policy(settings, approval_refs):
    """Freeze the only supported parallel WEEK group to the reviewed S1/S3 pair."""
    declaration = {**settings.get("approvals", {}),
                   **settings.get("stage_inputs", {}).get("week", {}).get("approvals", {})}["weekly_concurrency"]
    _validate_weekly_concurrency(declaration)
    evidence_path = _path(approval_refs["weekly_concurrency"], "weekly_concurrency evidence", file=True)
    return {"policy_id": "weekly-paired-s1-s3-v1", "group_by": "launch_at",
        "scenarios": ["S1", "S3"], "priorities": ["P0", "P1"],
        "max_parallel_entries": 2, "evidence_path": str(evidence_path),
        "evidence_sha256": _sha(evidence_path)}


def _validate_weekly_concurrency(declaration):
    fields = declaration.get("fields", {})
    if fields.get("mode") != "paired_s1_s3" or fields.get("max_parallel_entries") != 2:
        raise ValueError("weekly_concurrency must explicitly admit paired_s1_s3 with max_parallel_entries: 2")


def _common_n(settings):
    path = _path(settings.get("common_n_recommendation"), "common_n_recommendation", file=True)
    recommendation = json.loads(path.read_text())
    # Recommendation is produced on the laptop. The runner only checks its
    # frozen identity; it does not analyse measurements or select a workload.
    if not isinstance(recommendation, dict) or recommendation.get("status") != "recommended":
        raise ValueError("common_n_recommendation requires locally saved recommend-common-n --format json output")
    frozen = {"state": "frozen", "common_n": recommendation.get("common_n", {}).get("value"),
        "source_campaign_id": recommendation.get("source_campaign_id", {}).get("value"),
        "source_sha256": recommendation.get("source_sha256", {}).get("value"),
        "plateau_found": recommendation.get("plateau_found", {}).get("value")}
    if len(set(recommendation.get("source_run_ids", []))) != 3:
        raise ValueError("local common-N recommendation must identify all three saved calibration runs")
    from .contracts.campaign import CommonNState
    return CommonNState.model_validate(frozen).model_dump(mode="json")


def validate_stage_inputs(plan: dict, settings: dict, root: Path, *, local_preparation: bool = False,
                          draft: bool = False) -> list[str]:
    """Read-only preflight; an empty list is local readiness, not cloud acceptance."""
    errors = []
    try:
        entries = _selected(plan, settings)
        stage = plan["stage"]
        archive_sink = settings.get("archive_sink")
        if archive_sink is not None:
            from .gcs_archive import valid_bucket_name
            if (not isinstance(archive_sink, dict)
                    or not {"bucket", "maximum_staging_bytes", "maximum_archive_bytes",
                        "minimum_controller_free_bytes", "minimum_guest_free_bytes", "maximum_capsule_bytes",
                        "controller_quota_free_bytes", "controller_quota_evidence_path",
                        "controller_quota_evidence_sha256", "archive_root", "staging_directory",
                        "source_credentials_file", "expected_source_principal", "expected_project_id",
                        "expected_service_account_email"}.issubset(archive_sink)
                    or set(archive_sink) - {"bucket", "maximum_staging_bytes", "maximum_archive_bytes",
                        "minimum_controller_free_bytes", "minimum_guest_free_bytes", "maximum_capsule_bytes",
                        "controller_quota_free_bytes", "controller_quota_evidence_path",
                        "controller_quota_evidence_sha256", "restore_path", "archive_root", "staging_directory",
                        "source_credentials_file", "expected_source_principal", "expected_project_id",
                        "expected_service_account_email"}
                    or not valid_bucket_name(archive_sink.get("bucket"))
                    or type(archive_sink.get("maximum_staging_bytes")) is not int
                    or archive_sink["maximum_staging_bytes"] <= 0
                    or type(archive_sink.get("maximum_archive_bytes")) is not int
                    or archive_sink["maximum_archive_bytes"] <= 0
                    or type(archive_sink.get("minimum_controller_free_bytes")) is not int
                    or archive_sink["minimum_controller_free_bytes"] < 0
                    or type(archive_sink.get("minimum_guest_free_bytes")) is not int
                    or archive_sink["minimum_guest_free_bytes"] < 0
                    or type(archive_sink.get("maximum_capsule_bytes")) is not int
                    or archive_sink["maximum_capsule_bytes"] <= 0
                    or type(archive_sink.get("controller_quota_free_bytes")) is not int
                    or archive_sink["controller_quota_free_bytes"] < 0):
                raise ValueError("archive_sink requires measured guest/controller bounds and capsule state maximum")
            from .gcs_archive import valid_archive_identity, valid_archive_source
            if (not valid_archive_source(archive_sink.get("expected_source_principal"))
                    or not valid_archive_identity(archive_sink.get("expected_project_id"),
                        archive_sink.get("expected_service_account_email"))):
                raise ValueError("archive_sink source ADC and approved archive account identity must be explicit")
            _path(archive_sink["source_credentials_file"], "archive_sink.source_credentials_file")
            _path(archive_sink["controller_quota_evidence_path"], "archive_sink.controller_quota_evidence_path", file=True)
            from .storage_admission import validate_controller_quota_evidence
            validate_controller_quota_evidence(archive_sink["controller_quota_free_bytes"],
                archive_sink["controller_quota_evidence_path"], archive_sink["controller_quota_evidence_sha256"])
            if "restore_path" in archive_sink:
                _path(archive_sink["restore_path"], "archive_sink.restore_path")
            for field in ("archive_root", "staging_directory"):
                candidate = Path(archive_sink[field])
                for base_name in ("results_root", "state_root"):
                    base = Path(settings[base_name])
                    if candidate == base or candidate in base.parents or base in candidate.parents:
                        raise ValueError(f"archive_sink.{field} must not overlap {base_name}")
            _path(archive_sink["archive_root"], "archive_sink.archive_root")
            _path(archive_sink["staging_directory"], "archive_sink.staging_directory")
            for label in ("archive_root", "staging_directory"):
                candidate = Path(archive_sink[label])
                for base_name in ("results_root", "state_root"):
                    base = Path(settings[base_name])
                    if candidate == base or candidate in base.parents or base in candidate.parents:
                        raise ValueError(f"archive_sink.{label} must not overlap {base_name}")
        _path(settings.get("state_root"), "state_root")
        _path(settings.get("results_root"), "results_root")
        if Path(settings["state_root"]) == Path(settings["results_root"]):
            raise ValueError("state_root and results_root must be separate")
        for ancestor, other in ((Path(settings["state_root"]), Path(settings["results_root"])),
                                (Path(settings["results_root"]), Path(settings["state_root"]))):
            if ancestor in other.parents:
                raise ValueError("private state and exportable results cannot contain one another")
        if sys.version_info < (3, 12):
            raise ValueError("deploy the campaign driver with Python 3.12 or newer")
        gates = _gate_rows(settings, stage, draft=draft)
        from .failure_waiver import source_waiver
        acceptance_waived = stage == "acceptance" and source_waiver(settings) is not None
        if (stage == "acceptance"
                and settings.get("stage_inputs", {}).get(stage, {}).get("workflow_profile") == ACCEPTANCE_PROFILE
                and not acceptance_waived
                and settings.get("stage_inputs", {}).get(stage, {}).get("failure_workflow_profile") != FAILURE_PROFILE):
            raise ValueError("the paired acceptance profile requires its separate finite registered-failure profile")
        if stage == "week":
            _validate_weekly_concurrency(gates["weekly_concurrency"])
        elif stage == "acceptance" and not draft:
            declaration = gates.get("paired_acceptance")
            if declaration is not None and "acceptance" in settings.get("stage_inputs", {}):
                fields = declaration.get("fields", {})
                inputs = settings["stage_inputs"][stage]
                if inputs.get("workflow_profile") == ACCEPTANCE_PROFILE:
                    window = _profile_execution_window(inputs, "acceptance")
                    dispatch_window = _profile_dispatch_window(inputs, "acceptance", window)
                    if (fields.get("mode") != "paired_acceptance_s1_s3" or fields.get("max_parallel_entries") != 2
                            or fields.get("execution_window_utc") != window
                            or fields.get("dispatch_window_utc", window) != dispatch_window):
                        raise ValueError("paired_acceptance must bind both exact ready-triggered validation windows")
                else:
                    start = _utc(inputs.get("start_utc"), "stage_inputs.acceptance.start_utc")
                    if (fields.get("mode") != "paired_acceptance_s1_s3" or fields.get("max_parallel_entries") != 2
                            or _utc(fields.get("start_utc"), "approvals.paired_acceptance.fields.start_utc") != start):
                        raise ValueError("paired_acceptance must bind paired_acceptance_s1_s3, two entries and the exact fixed UTC start")
        _cost_payload(settings, root)
        if stage in {"day", "week"}:
            _common_n(settings)
        if stage == "week":
            start = date.fromisoformat(str(settings["stage_inputs"][stage]["first_day_utc"]))
            windows = settings["stage_inputs"][stage]["windows_utc"]
            if set(windows) != set(WINDOWS) or len(set(windows.values())) != 4:
                raise ValueError("WEEK needs four distinct reviewed UTC windows after DAY analysis")
            for window, time in windows.items():
                _utc(f"{start}T{time}+00:00", f"windows_utc.{window}")
        elif stage == "calibration" and settings.get("stage_inputs", {}).get(stage, {}).get("workflow_profile") == CALIBRATION_PROFILE:
            inputs = settings["stage_inputs"][stage]
            window = _profile_execution_window(inputs, "calibration")
            dispatch_window = _profile_dispatch_window(inputs, "calibration", window)
            fields = gates["calibration_authorized"].get("fields", {})
            if not draft and "dispatch_window_utc" in inputs and (
                    fields.get("execution_window_utc") != window
                    or fields.get("dispatch_window_utc") != dispatch_window):
                raise ValueError("calibration_authorized must bind both exact ready-triggered validation windows")
        elif stage != "acceptance":
            _utc(settings["stage_inputs"][stage].get("start_utc"), f"stage_inputs.{stage}.start_utc")
        elif entries and settings.get("stage_inputs", {}).get(stage, {}).get("workflow_profile") != ACCEPTANCE_PROFILE:
            _utc(settings["stage_inputs"][stage].get("start_utc"), "stage_inputs.acceptance.start_utc")
        for entry_id, alias in entries:
            path = root / plan.get("configs", {}).get(alias, CONFIGS[alias])
            data = yaml.safe_load(path.read_text())
            providers = data["selected_providers"]
            _allowance(settings, stage, providers, entry_id=entry_id)
            for provider in providers:
                options = settings["providers"][provider]
                for name in ("authentication_reference", "public_key_path" if provider != "azure" else "ssh_public_key_path"):
                    _path(options.get(name), f"providers.{provider}.{name}", file=True)
                if not options.get("controller_cidr"):
                    raise ValueError(f"set providers.{provider}.controller_cidr to runner public IPv4 /32")
                network = ipaddress.ip_network(options["controller_cidr"], strict=True)
                if network.version != 4 or network.prefixlen != 32:
                    raise ValueError(f"providers.{provider}.controller_cidr must be runner IPv4 /32")
                required = {"aws": ("account_id", "ami_ids"), "azure": ("subscription_id", "tenant_id", "selected_image"),
                            "gcp": ("project_id", "images")}[provider]
                for name in required:
                    if not options.get(name):
                        raise ValueError(f"set real providers.{provider}.{name}")
                interfaces = options.get("diagnostic_interfaces")
                if (not isinstance(interfaces, dict) or set(interfaces) != {"vm_a", "vm_b"}
                    or any(not isinstance(name, str) or not name for name in interfaces.values())):
                    raise ValueError(f"set providers.{provider}.diagnostic_interfaces.vm_a/vm_b from actual image interface names")
                public_key = Path(options["public_key_path" if provider != "azure" else "ssh_public_key_path"]).read_text().strip()
                from .providers.aws import AwsRuntimeSettings
                AwsRuntimeSettings.public_key_only(public_key)
                if provider == "aws":
                    if not re.fullmatch(r"[0-9]{12}", str(options["account_id"])) or not all(
                        isinstance(image, str) and re.fullmatch(r"ami-[a-f0-9]{8,17}", image) for image in options["ami_ids"].values()):
                        raise ValueError("set actual AWS account ID and concrete regional Ubuntu AMI IDs")
                elif provider == "azure":
                    from .azure_provider import AzureRuntimeOptions
                    uuid.UUID(options["subscription_id"])
                    uuid.UUID(options["tenant_id"])
                    AzureRuntimeOptions.concrete_ubuntu_image(options["selected_image"])
                elif provider == "gcp":
                    from .providers.gcp import validate_ubuntu_image_identities
                    selected_regions = set(data["provider_configs"]["gcp"]["regions"].values())
                    validate_ubuntu_image_identities(options["images"], selected_regions=selected_regions)
        # Credential refresh is a reviewed operator fact, never inferred from a login.
        refresh = settings.get("credential_refresh", {})
        if not refresh.get("confirmed") or not refresh.get("valid_through_utc"):
            raise ValueError("record tested cloud-supported credential_refresh and valid_through_utc")
        _path(refresh.get("source"), "credential_refresh.source", file=True)
        valid_through = _utc(refresh["valid_through_utc"], "credential_refresh.valid_through_utc")
        now = datetime.now(timezone.utc)
        selected_profiles = [settings.get("stage_inputs", {}).get(stage, {}).get("workflow_profile")]
        if stage == "acceptance":
            selected_profiles.append(settings.get("stage_inputs", {}).get(stage, {}).get("failure_workflow_profile"))
        selected_caps = [profile_spec(profile)["cap_seconds"] for profile in selected_profiles if profile is not None]
        stage_horizon = max(selected_caps) if selected_caps else {"acceptance": 6000, "calibration": 1800, "day": 87600, "week": 1200}[stage]
        horizon = now + timedelta(seconds=stage_horizon)
        if stage == "week":
            last = max(_utc(f"{start + timedelta(days=6)}T{time}+00:00", "WEEK final slot") for time in windows.values())
            horizon = last + timedelta(seconds=1200)
        elif stage in {"acceptance", "calibration"} and selected_caps:
            horizon = _ready_credential_horizon(settings["stage_inputs"][stage], stage, stage_horizon)
        elif stage in {"calibration", "day"}:
            horizon = _utc(settings["stage_inputs"][stage]["start_utc"], "start_utc") + timedelta(seconds=stage_horizon if selected_caps else 1800 if stage == "calibration" else 87600)
        if valid_through < horizon:
            raise ValueError(f"credential_refresh evidence must remain valid through stage cleanup ({horizon.isoformat()})")
        missing_tools = [name for name in ("terraform", "aws", "az", "gcloud", "ssh", "scp") if shutil.which(name) is None]
        if missing_tools:
            raise ValueError("install required controller tools on Linux: " + ", ".join(missing_tools))
        prebuilt_bundle = settings.get("prebuilt_bundle")
        if prebuilt_bundle is not None:
            if not isinstance(prebuilt_bundle, dict) or set(prebuilt_bundle) != {"path", "sha256"}:
                raise ValueError("prebuilt_bundle requires exactly the reviewed local path and SHA-256")
            from .bootstrap import BootstrapConfig
            try:
                BootstrapConfig.model_validate({**_bootstrap(prebuilt_bundle),
                    "install_prefix": "/var/lib/cnb/install"})
            except (TypeError, ValueError) as exc:
                raise ValueError("prebuilt_bundle path, digest or production source pins are invalid") from exc
            timeouts = settings.get("timeouts", {})
            if (timeouts.get("command_seconds", 90) != 90
                    or timeouts.get("transfer_seconds", 45) != 45):
                raise ValueError("prebuilt bundle profile must preserve the accepted 90s command and 45s transfer limits")
        minimum = settings.get("minimum_free_bytes")
        if type(minimum) is not int or minimum <= 0:
            raise ValueError("set positive minimum_free_bytes from measured stage storage needs")
        if stage in {"day", "week"} and not draft:
            review = _gate_rows(settings, stage)["size_export_review"].get("fields", {})
            remote_archive = archive_sink is not None
            if remote_archive and review.get("capsule_state_manifest_bytes", 0) < archive_sink["maximum_capsule_bytes"]:
                raise ValueError("storage review capsule state bound is below archive_sink.maximum_capsule_bytes")
            if remote_archive and (review.get("controller_quota_free_bytes") != archive_sink["controller_quota_free_bytes"]
                    or review.get("controller_quota_evidence_sha256") != archive_sink["controller_quota_evidence_sha256"]
                    or review.get("controller_quota_evidence_path") != archive_sink["controller_quota_evidence_path"]):
                raise ValueError("storage review controller quota does not match the archive sink evidence")
            validate_storage_review(stage, review, minimum_free_bytes=minimum,
                                    results_path=Path(settings["results_root"]),
                                    storage_mode="remote_archive" if remote_archive else "local")
        disk_path = Path(settings["results_root"])
        while not disk_path.exists():
            disk_path = disk_path.parent
        storage_floor = (review.get("expected_concurrent_archive_part_count", 1)
            * review.get("maximum_archive_part_bytes", 0)
            + review.get("capsule_state_manifest_bytes", 0)
            + review.get("controller_reserve_bytes", 0)) if stage in {"day", "week"} and archive_sink is not None else minimum
        observed_storage_free = (archive_sink["controller_quota_free_bytes"]
            if stage in {"day", "week"} and archive_sink is not None
            else shutil.disk_usage(disk_path).free)
        if observed_storage_free < storage_floor:
            raise ValueError("measured destination quota or free space is below the declared stage requirement")
        # Check the installed bounded transport before read-only identity APIs.
        # Source-bound amounts and approval still fail closed at admission.
        if not local_preparation:
            from .financial_controls import require_transfer_capability
            require_transfer_capability()
            errors.extend(check_provider_identities(settings["providers"]))
    except (ValueError, KeyError, OSError, TypeError) as exc:
        errors.append(str(exc) if not isinstance(exc, KeyError) else f"missing operator setting: {exc.args[0]}")
    return errors


def _cost_payload(settings, root):
    """One immutable source for every stage; amounts must be supplied from real prices."""
    pricing = settings.get("pricing")
    if not isinstance(pricing, dict) or pricing.get("reviewed") is not True:
        raise ValueError("pricing.reviewed and dated real source prices are required; fixture prices are forbidden")
    source = _path(pricing.get("source"), "pricing.source", file=True)
    if "tests/fixtures" in str(source) or pricing.get("fixture_scope"):
        raise ValueError("test fixture prices cannot authorize a campaign")
    checked = str(pricing.get("checked_on", ""))
    if date.fromisoformat(checked) > datetime.now(timezone.utc).date():
        raise ValueError("pricing.checked_on cannot be future-dated")
    workflow_profiles = set()
    from .failure_waiver import source_waiver
    acceptance_waived = source_waiver(settings) is not None
    for stage, inputs in settings.get("stage_inputs", {}).items():
        if stage not in STAGES or not isinstance(inputs, dict):
            continue
        profile_names = ("workflow_profile", "failure_workflow_profile")
        if stage == "acceptance" and acceptance_waived:
            # The source-bound waiver selects only V-S1/V-S3, so the registered
            # failure profile is not part of this reduced campaign's priced scope.
            profile_names = ("workflow_profile",)
        for name in profile_names:
            profile = inputs.get(name)
            if profile is not None:
                workflow_profiles.add(profile)
    declared_profiles = pricing.get("workflow_profiles", [])
    if (not isinstance(declared_profiles, list) or len(set(declared_profiles)) != len(declared_profiles)
            or set(declared_profiles) != workflow_profiles):
        if workflow_profiles:
            raise ValueError("pricing.workflow_profiles must exactly bind every selected finite workflow profile")
        if declared_profiles:
            raise ValueError("pricing.workflow_profiles must be empty when no validation profile is selected")
    templates = {}
    if workflow_profiles:
        selected_aliases = set()
        if ACCEPTANCE_PROFILE in workflow_profiles:
            selected_aliases.update({"s1_acceptance", "s3_acceptance"})
        if FAILURE_PROFILE in workflow_profiles:
            selected_aliases.update({"aws_failure", "azure_failure", "gcp_failure"})
        if CALIBRATION_PROFILE in workflow_profiles:
            selected_aliases.add("calibration")
        if not selected_aliases:
            raise ValueError("selected validation workflow profiles do not bind a finite priced config set")
    else:
        selected_aliases = set(CONFIGS)
    for alias, path in CONFIGS.items():
        if alias not in selected_aliases:
            continue
        data = yaml.safe_load((root / path).read_text())
        config_id = "exp-003-acceptance" if alias == "s3_acceptance" else data["config_id"]
        category = "acceptance" if "acceptance" in alias or "failure" in alias else alias
        prices = pricing.get("components", {}).get(category)
        if not isinstance(prices, dict):
            raise ValueError(f"supply pricing.components.{category} for all selected clouds")
        rows = {}
        for provider in data["selected_providers"]:
            components = prices.get(provider)
            if not isinstance(components, dict) or set(components) != set(COST_COMPONENTS):
                raise ValueError(f"pricing.components.{category}.{provider} requires {', '.join(COST_COMPONENTS)}")
            normalized = {}
            for name, value in components.items():
                if type(value) in {int, float, str}:
                    value = {"amount_usd": value, "quantity": "1", "unit": "complete-workflow component",
                        "inclusion_rule": f"Reviewed {category}/{provider}/{name} in {source}",
                        "funding_bucket": name if name in {"tax", "uncertainty"} else "primary"}
                if not isinstance(value, dict) or not all(k in value for k in ("amount_usd", "quantity", "unit", "inclusion_rule", "funding_bucket")):
                    raise ValueError(f"set actual priced USD amount for pricing.components.{category}.{provider}.{name}")
                try:
                    amount = Decimal(str(value["amount_usd"]))
                    quantity = Decimal(str(value["quantity"]))
                except InvalidOperation as exc:
                    raise ValueError(f"set finite real price component {category}.{provider}.{name}") from exc
                if not amount.is_finite() or amount < 0 or not quantity.is_finite() or quantity <= 0:
                    raise ValueError("price components require finite nonnegative amounts and positive quantities")
                normalized[name] = {**value, "amount_usd": str(amount), "quantity": str(quantity)}
            regions = sorted(set(data["provider_configs"][provider]["regions"].values()))
            transfer = pricing.get("result_transfer", {}).get(category, {}).get(provider)
            if not isinstance(transfer, dict):
                raise ValueError(f"supply priced pricing.result_transfer.{category}.{provider} payload and protocol allowances")
            rows[provider] = {"envelope_id": f"{config_id}-{provider}", "provider": provider,
                "priced_on": checked, "source_kind": "provider-price-input", "regions": regions,
                "unit_basis": "one-two-vm-provider-run", "components": normalized,
                "result_transfer": transfer}
            from .financial_controls import transfer_terms
            transfer_terms(rows[provider])
        templates[config_id] = rows
    funding = pricing.get("funding")
    if not isinstance(funding, dict):
        raise ValueError("supply reviewed pricing.funding from run register, preserving $300 cap/$50 buffer")
    allowed_funding = {"primary", "replacements", "tax", "uncertainty", "optional", "primary_forecast", "working_limit", "protected_buffer", "total_limit"}
    if set(funding) - allowed_funding or not {"primary", "replacements", "tax", "uncertainty"} <= set(funding):
        raise ValueError("pricing.funding must name reviewed primary/replacements/tax/uncertainty amounts")
    from .admission import _decimal
    amounts = {k: _decimal(v, f"pricing.funding.{k}") for k, v in funding.items()}
    if (sum((amounts.get(k, Decimal(0)) for k in ("primary", "replacements", "tax", "uncertainty", "optional")), Decimal(0)) > 250
        or amounts.get("total_limit", Decimal(300)) != 300
        or amounts.get("protected_buffer", Decimal(50)) != 50
        or amounts.get("working_limit", Decimal(250)) != 250):
        raise ValueError("pricing.funding must preserve the $300 cap, $250 working limit and $50 protected buffer")
    support = _support_payloads(settings)
    aws_raw = (json.dumps(support["aws"], indent=2, sort_keys=True) + "\n").encode()
    profile_bindings = [{"profile_id": profile, "stage": PROFILE_SPECS[profile]["stage"],
        "lifetime_cap_seconds": PROFILE_SPECS[profile]["cap_seconds"],
        "allowance": PROFILE_SPECS[profile]["allowance"]} for profile in sorted(workflow_profiles)]
    result = {"schema_version": 1, "cost_input_id": pricing.get("id") or f"operator-prices-{checked}",
        "prices_checked_on": checked, "source_scope": [str(source)], "source_sha256": _sha(source),
        "source_identity_kind": "operator-reviewed-dated-price-source", "currency": "USD",
        "funding": funding,
        "live_quote": False,
        "provider_support": {"aws": {"sha256": hashlib.sha256(aws_raw).hexdigest(),
            "per_region_usd": str(support["aws"]["worst_planned_cost_per_region"])}},
        "provider_run_envelope_templates": templates}
    if workflow_profiles:
        result["workflow_profiles"] = profile_bindings
        result["refresh_required_before_paid_execution"] = False
    return result


def _support_payloads(settings):
    pricing = settings["pricing"]
    source = _path(pricing.get("source"), "pricing.source", file=True)
    expiry = pricing.get("expiry", {})
    try:
        step = Decimal(str(expiry["aws"]["automation_step_price"]))
        second = Decimal(str(expiry["aws"]["script_second_price"]))
        azure_rate = Decimal(str(expiry["azure"]["automation_rate_per_job_minute"]))
        azure_bound = Decimal(str(expiry["azure"]["job_minutes_bound"]))
    except (KeyError, TypeError, InvalidOperation) as exc:
        raise ValueError("supply real pricing.expiry AWS step/second and Azure per-minute rates/job bound") from exc
    if any(not n.is_finite() or n < 0 for n in (step, second, azure_rate)) or not azure_bound.is_finite() or azure_bound <= 0:
        raise ValueError("expiry prices must be finite nonnegative values and Azure job bound positive")
    aws_total = float((step * 2 + second * 330).quantize(Decimal("0.0001"), rounding=ROUND_CEILING))
    aws = {"id": f"aws-expiry-{pricing['checked_on']}", "checked_on": str(pricing["checked_on"]),
        "source": str(source), "currency": "USD", "automation_step_price": float(step),
        "script_second_price": float(second), "planned_steps_per_region": 2,
        "discovery_horizon_seconds_per_region": 300, "planned_action_seconds_per_region": 330,
        "worst_planned_cost_per_region": aws_total, "worst_planned_cost_for_two_regions": round(aws_total * 2, 4),
        "free_tier_assumed": False}
    azure = {"source_id": f"azure-expiry-{pricing['checked_on']}", "date": str(pricing["checked_on"]),
        "sha256": _sha(source), "automation_rate_per_job_minute": float(azure_rate),
        "job_minutes_bound": float(azure_bound)}
    gcp = {"id": f"gcp-support-{pricing['checked_on']}", "prices_checked_on": str(pricing["checked_on"]),
        "source_path": str(source), "source_sha256": _sha(source), "template_support_cost_usd": 0,
        "placement_policy_support_cost_usd": 0, "rationale": expiry.get("gcp", {}).get("zero_support_rationale")}
    if not gcp["rationale"]:
        raise ValueError("verify pricing.expiry.gcp.zero_support_rationale against actual documented source")
    return {"aws": aws, "azure": azure, "gcp": gcp}


def _bootstrap(prebuilt_bundle=None):
    # Existing source/patch identities verified by SourceBuild, not invented prices.
    return {"sources": [
        {"tool": "sockperf", "source_url": "https://codeload.github.com/Mellanox/sockperf/tar.gz/5ebd327da983225321818c0355db922515e026bd",
         "source_reference": "sockperf-5ebd327da983225321818c0355db922515e026bd", "archive_name": "sockperf.tar.gz",
         "source_sha256": "a35a1daf310551b9f675af4fb7d95a6a10389fda4c7605d70a21554be0773f8e",
         "upstream_commit": "5ebd327da983225321818c0355db922515e026bd",
         "observation_patch_path": "tool_patches/sockperf-3.10-acquisition.patch",
         "observation_patch_sha256": "bfee91183037577a0861c24c27dd9c644134a644ac2edead0ab460f4875ac5fc"},
        {"tool": "iperf3", "source_url": "https://codeload.github.com/esnet/iperf/tar.gz/2acfcfe94e928e74542c9f107e02aa6dd4748a79",
         "source_reference": "iperf-2acfcfe94e928e74542c9f107e02aa6dd4748a79", "archive_name": "iperf3.tar.gz",
         "source_sha256": "d6b4d2c7b20157c5b7fbd2de66f22bd3428958b5f2e28899151b6e22745886fe",
         "upstream_commit": "2acfcfe94e928e74542c9f107e02aa6dd4748a79",
         "observation_patch_path": "tool_patches/iperf-3.17.1-acquisition.patch",
         "observation_patch_sha256": "11b96daadb4a1f17dc24376bb3d0d7feec330efeedc550b1979b1566e9b81661"}],
        "remote_root": "/var/lib/cnb", "workspace": "/var/lib/cnb/work",
        "results_workspace": "/var/lib/cnb/results", "install_prefix": "/var/lib/cnb/install",
        **({"prebuilt_bundle": copy.deepcopy(prebuilt_bundle)} if prebuilt_bundle is not None else {})}


def provider_scope(providers):
    keys = {"aws": ("account_id", "ami_ids"), "azure": ("subscription_id", "tenant_id", "selected_image"),
            "gcp": ("project_id", "images")}
    return {provider: {key: options[key] for key in keys[provider]} for provider, options in providers.items()}


def _review_from_prepared(settings, prepared):
    """Use actual saved acceptance identities plus explicit operator confirmations.

    The operator records only observations/source paths in the one settings file;
    internal review documents, attempt IDs and hashes are generated here.
    """
    entries = {entry["entry_id"]: entry for entry in prepared.get("entries", [])}
    from .failure_waiver import (NORMAL_ENTRIES, bind_prepared_acceptance,
        registered_failure_record, source_waiver, validate_prepared_binding)
    selected_waiver = source_waiver(settings)
    waived = set(entries) == set(NORMAL_ENTRIES)
    if prepared.get("stage") != "acceptance" or not (
            set(entries) == {"V-S1", "V-S3", "V-A-FAIL", "V-Z-FAIL", "V-G-FAIL"}
            or waived and selected_waiver is not None):
        raise ValueError("paid_acceptance source must be a complete strict stage or an explicitly waived reduced stage")
    if waived:
        binding = bind_prepared_acceptance(selected_waiver, [entries[name] for name in NORMAL_ENTRIES])
        declared = prepared.get("failure_waiver")
        if declared is not None:
            validate_prepared_binding(declared, [entries[name] for name in NORMAL_ENTRIES])
            if declared != binding:
                raise ValueError("prepared acceptance failure waiver does not match current source/run identities")
        review = {"schema_version": 1, "repository_root": prepared["repository_root"],
            "accepted_campaigns": [entries[name]["parent_manifest_path"] for name in NORMAL_ENTRIES],
            "accepted_runtime_configs": {entries[name]["parent_manifest_path"]: {
                "path": entries[name]["runtime_config_path"],
                "sha256": _sha(_path(entries[name]["runtime_config_path"], "accepted runtime", file=True))}
                for name in NORMAL_ENTRIES}}
        return review, registered_failure_record(binding)
    checks = {}
    declarations = settings.get("stage_inputs", {}).get("acceptance", {}).get("failure_checks", {})
    for registered, provider in (("V-A-FAIL", "aws"), ("V-Z-FAIL", "azure"), ("V-G-FAIL", "gcp")):
        entry = entries[registered]
        parent = json.loads(_path(entry["parent_manifest_path"], "acceptance manifest", file=True).read_text())
        declaration = declarations.get(registered, {})
        reuse = declaration.get("reuse") if isinstance(declaration, dict) else None
        if reuse is not None:
            from .acceptance_review import _failure_review, _runtime_scope_sha256
            if not isinstance(reuse, dict):
                raise ValueError(f"failure_checks.{registered}.reuse must identify a source-bound completed check")
            reuse_path = _path(reuse.get("source"), f"failure_checks.{registered}.reuse.source", file=True)
            reuse_hash = reuse.get("sha256")
            if not isinstance(reuse_hash, str) or _sha(reuse_path) != reuse_hash:
                raise ValueError(f"failure_checks.{registered}.reuse source hash differs")
            _failure_review({"path": str(reuse_path), "sha256": reuse_hash}, required_entries={registered})
            reused_review = yaml.safe_load(reuse_path.read_text(encoding="utf-8"))
            reused = reused_review["checks"][registered]
            config_path = _path(entry["config_path"], f"{registered} config", file=True)
            runtime_path = _path(entry["runtime_config_path"], f"{registered} runtime config", file=True)
            identity = reused.get("config_identity", {})
            if (identity.get("config_sha256") != _sha(config_path)
                    or identity.get("source_config_sha256") != entry["source_config_sha256"]
                    or identity.get("runtime_scope_sha256") != _runtime_scope_sha256(runtime_path)
                    or identity.get("implementation_git_commit") != parent.get("implementation_git_commit")):
                raise ValueError(f"reused {registered} failure evidence does not match the exact prepared config/runtime sources")
            checks[registered] = reused
            continue
        config_path = _path(entry["config_path"], f"{registered} config", file=True)
        runtime_path = _path(entry["runtime_config_path"], f"{registered} runtime config", file=True)
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        if Path(runtime.get("initialized_campaign_manifest", "")) != Path(entry["parent_manifest_path"]):
            raise ValueError(f"{registered} runtime config does not belong to its prepared attempt")
        from .acceptance_review import _runtime_scope_sha256
        checks[registered] = {"provider": provider, "attempt_id": parent["campaign_id"],
            "config_identity": {"config_path": str(config_path), "config_sha256": _sha(config_path),
                "source_config_sha256": entry["source_config_sha256"],
                "runtime_config_path": str(runtime_path), "runtime_config_sha256": _sha(runtime_path),
                "runtime_scope_sha256": _runtime_scope_sha256(runtime_path),
                "implementation_git_commit": parent.get("implementation_git_commit")}}
        row = declaration
        for action in ("server_failure", "expiry", "cleanup"):
            observation = row.get(action, {})
            if observation.get("confirmed") is not True:
                raise ValueError(f"record supervised acceptance.failure_checks.{registered}.{action} confirmation and actual source before research")
            source = _path(observation.get("source"), f"failure_checks.{registered}.{action}.source", file=True)
            checks[registered][action] = {"status": "passed", "source_path": str(source), "source_sha256": _sha(source)}
        controller = row.get("controller_loss", {})
        if controller.get("confirmed") is True:
            source = _path(controller.get("source"), f"failure_checks.{registered}.controller_loss.source", file=True)
            checks[registered]["controller_loss"] = {"status": "passed", "source_path": str(source), "source_sha256": _sha(source)}
        elif controller.get("waived") is True and isinstance(controller.get("reason"), str) and controller["reason"].strip():
            source = _path(controller.get("source"), f"failure_checks.{registered}.controller_loss.waiver_source", file=True)
            recovery = row.get("ordinary_recovery", {})
            if recovery.get("confirmed") is not True:
                raise ValueError(f"physical controller-loss waiver for {registered} requires actual ordinary_recovery evidence")
            recovery_source = _path(recovery.get("source"), f"failure_checks.{registered}.ordinary_recovery.source", file=True)
            checks[registered]["controller_loss"] = {"status": "waived", "source_path": str(source),
                "source_sha256": _sha(source), "reason": controller["reason"].strip()}
            checks[registered]["ordinary_recovery"] = {"status": "passed", "source_path": str(recovery_source),
                "source_sha256": _sha(recovery_source)}
        else:
            raise ValueError(f"record supervised acceptance.failure_checks.{registered}.controller_loss as passed or explicitly waived with a source")
    accepted = [entries[key] for key in ("V-S1", "V-S3")]
    review = {"schema_version": 1, "repository_root": prepared["repository_root"],
        "accepted_campaigns": [row["parent_manifest_path"] for row in accepted],
        "accepted_runtime_configs": {row["parent_manifest_path"]: {"path": row["runtime_config_path"],
            "sha256": _sha(_path(row["runtime_config_path"], "accepted runtime", file=True))} for row in accepted}}
    return review, {"schema_version": 1, "checks": checks}


def _write_evidence(directory, declarations, scope, settings):
    refs = {}
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    for key, declaration in declarations.items():
        source = _path(declaration["source"], f"approvals.{key}.source", file=True)
        # Explicit operator declarations supplement, never replace, the actual source.
        evidence = {**declaration.get("fields", {}), "evidence_id": f"operator-{key}-{_sha(source)[:16]}",
                    "source_path": str(source), "source_sha256": _sha(source)}
        if key == "paid_acceptance":
            # The review source itself lists actual completed acceptance
            # manifests and registered failure evidence; no asserted wrapper pass.
            review = yaml.safe_load(source.read_text())
            if isinstance(review, dict) and review.get("stage") == "acceptance":
                original = source
                review, failures = _review_from_prepared(settings, review)
                failure_path = directory / "registered-failure-review.yaml"
                failure_path.write_text(yaml.safe_dump(failures, sort_keys=True))
                review["registered_failure_review"] = str(failure_path)
                review["prepared_stage_source"] = {"path": str(original), "sha256": _sha(original)}
                source = directory / "acceptance-scope-review.yaml"
                source.write_text(yaml.safe_dump(review, sort_keys=True))
                evidence.update(source_path=str(source), source_sha256=_sha(source))
            if not isinstance(review, dict) or not isinstance(review.get("accepted_campaigns"), list):
                raise ValueError("paid_acceptance source must list actual accepted_campaigns paths and registered_failure_review path")
            evidence["accepted_campaigns"] = [{"path": str(_path(p, "accepted_campaigns", file=True)),
                "sha256": _sha(Path(p))} for p in review["accepted_campaigns"]]
            failure = _path(review.get("registered_failure_review"), "registered_failure_review", file=True)
            evidence["registered_failure_review"] = {"path": str(failure), "sha256": _sha(failure)}
            evidence["repository_root"] = review["repository_root"]
            evidence["requested_provider_scope"] = scope
        path = directory / f"{key}.json"
        atomic_write_json(path, evidence)
        refs[key] = str(path)
    return refs


def _frozen_config(data, alias, settings, entry_id):
    data = copy.deepcopy(data)
    stage_inputs = settings.get("stage_inputs", {})
    workflow_profile = None
    if alias in {"s1_acceptance", "s3_acceptance"}:
        workflow_profile = stage_inputs.get("acceptance", {}).get("workflow_profile")
    elif alias in {"aws_failure", "azure_failure", "gcp_failure"}:
        workflow_profile = stage_inputs.get("acceptance", {}).get("failure_workflow_profile")
    elif alias == "calibration":
        workflow_profile = stage_inputs.get("calibration", {}).get("workflow_profile")
    if workflow_profile is not None:
        data["admission"]["workflow_profile"] = workflow_profile
        data["admission"]["lifetime_cap_seconds"] = profile_spec(workflow_profile)["cap_seconds"]
    if alias == "s3_acceptance":
        data["config_id"] = "exp-003-acceptance"
        data["mode"] = "acceptance"
        data["benchmark"]["measured_seconds"] = 10
        data["benchmark"]["common_n"] = {"state": "pending", "common_n": None,
            "source_campaign_id": None, "source_sha256": None, "plateau_found": None}
        data["admission"].update(requires_common_n=False, requires_schedule=False)
    if alias in {"day", "s1", "s3"}:
        data["benchmark"]["common_n"] = _common_n(settings)
    for provider in data["selected_providers"]:
        pc = data["provider_configs"][provider]
        pc["security"]["controller_cidr"] = {"value": settings["providers"][provider]["controller_cidr"]}
        if pc["placement"].get("name"):
            # Unique actual membership is still verified by each provider driver.
            pc["placement"]["name"] = f"cnb-{entry_id.lower()}-{uuid.uuid4().hex[:10]}"
    return data


def prepare_stage(plan: dict, settings: dict, root: Path, state_root: Path,
                  results_root: Path, clock, *, draft: bool = False) -> dict[str, Any]:
    """Prepare identities durably; only read-only identity commands may run."""
    if draft and plan.get("stage") not in {"acceptance", "calibration"}:
        raise ValueError("unapproved drafts are currently supported only for acceptance and calibration")
    errors = validate_stage_inputs(plan, settings, root, local_preparation=True, draft=draft)
    if errors:
        raise ValueError("; ".join(errors))
    root = Path(root).resolve()
    stage = plan["stage"]
    inputs = settings["stage_inputs"][stage]
    auxiliary_opt_in = inputs.get("azure_auxiliary_cleanup", False)
    if type(auxiliary_opt_in) is not bool:
        raise ValueError("stage_inputs.azure_auxiliary_cleanup must be an explicit boolean")
    selected = _selected(plan, settings)
    selected_entry_ids = {entry_id for entry_id, _alias in selected}
    if auxiliary_opt_in:
        expected_profile = ACCEPTANCE_PROFILE if stage == "acceptance" else CALIBRATION_PROFILE if stage == "calibration" else None
        expected_entries = {"V-S1", "V-S3"} if stage == "acceptance" else {"CAL-S1"}
        if (not draft or expected_profile is None or inputs.get("workflow_profile") != expected_profile
                or settings.get("providers", {}).get("azure", {}).get("subscription_id")
                    != "188a5848-76fb-4d57-8f2a-32c09284d486"
                or selected_entry_ids != expected_entries):
            raise ValueError("Azure auxiliary cleanup requires an unapproved supported-profile draft selecting exactly the normal pair or CAL-S1 in the reviewed subscription")
    now = _now(clock)
    stage_id = f"{stage}-{now:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    private = Path(state_root) / stage_id
    private.mkdir(parents=True, mode=0o700)
    private.chmod(0o700)
    stage_private = private
    result = Path(results_root) / stage_id
    result.mkdir(parents=True)
    cost_path = Path(state_root) / "cost-inputs.json"
    cost_payload = _cost_payload(settings, root)
    serialized = json.dumps(cost_payload, indent=2, sort_keys=True) + "\n"
    if cost_path.exists() and cost_path.read_text() != serialized:
        raise ValueError("reviewed prices changed: preserve the existing ledger and use its source reconciliation workflow before preparing another stage")
    if not cost_path.exists():
        atomic_write_json(cost_path, cost_payload)
    ledger = settings.get("ledger", {})
    ledger_path = _path(ledger.get("path"), "ledger.path")
    if not ledger.get("scope_id"):
        raise ValueError("ledger.scope_id must identify one shared thesis budget")
    if draft:
        approvals = _approval_paths(private / "approvals", stage)
        if auxiliary_opt_in:
            approvals["azure_shared_cleanup"] = str(private / "approvals" / "azure_shared_cleanup.json")
    else:
        approvals = _write_evidence(private / "approvals", _gate_rows(settings, stage), provider_scope(settings["providers"]), settings)
    support_dir = Path(state_root) / "support-inputs"
    support_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    support_paths = {}
    for provider, payload in _support_payloads(settings).items():
        path = support_dir / f"{provider}.json"
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        if path.exists() and path.read_text() != text:
            raise ValueError("provider support pricing changed; reconcile the existing budget source first")
        if not path.exists():
            atomic_write_json(path, payload)
        support_paths[provider] = path
    generated = root / "configs" / ("tests" if stage == "acceptance" else "experiments") / "campaign-generated" / stage_id
    generated.mkdir(parents=True)
    bootstrap_path = private / "bootstrap.yaml"
    bootstrap_path.write_text("#cloud-config\n# Provider-native authenticated host-key bootstrap is generated by the existing provider drivers.\n{}\n")
    session = f"campaign-{stage_id}"
    entries = []
    config_paths = {}
    schedule_rows = []
    source_configs = {}
    azure_cleanup_owners = []
    azure_parent_snapshots = {}
    for index, (entry_id, alias) in enumerate(selected):
        template = root / plan.get("configs", {}).get(alias, CONFIGS[alias])
        data = _frozen_config(yaml.safe_load(template.read_text()), alias, settings, entry_id)
        if stage == "acceptance" and entry_id.endswith("FAIL"):
            profile = settings.get("stage_inputs", {}).get(stage, {}).get("failure_workflow_profile")
        else:
            profile = settings.get("stage_inputs", {}).get(stage, {}).get("workflow_profile")
        if profile is not None:
            spec = profile_spec(profile)
            if spec is None or spec["stage"] != stage:
                raise ValueError(f"unsupported workflow_profile for {stage}")
            data["admission"]["workflow_profile"] = profile
            data["admission"]["lifetime_cap_seconds"] = spec["cap_seconds"]
            data["admission"]["requires_schedule"] = False
            provider_registration = spec["provider_registration_seconds"]
            data["options"]["provisioning_timeout_seconds"] = spec["allowance"]["provision_seconds"]
            data["options"]["readiness_timeout_seconds"] = spec["allowance"]["readiness_seconds"]
            data["options"]["cleanup_timeout_seconds"] = spec["allowance"]["cleanup_seconds"]
        else:
            provider_registration = None
        priced_rows = cost_payload["provider_run_envelope_templates"][data["config_id"]]
        estimated = rounded_provider_cost_total({provider: priced_rows[provider]
            for provider in data["selected_providers"]})
        data["admission"].update(cost_input_id=cost_payload["cost_input_id"],
            prices_checked_on=cost_payload["prices_checked_on"],
            cost_input_sha256={"value": cost_payload["source_sha256"]},
            estimated_cost_usd={"value": float(estimated)})
        data["scheduled_start"] = None
        config_path = generated / f"{entry_id.lower()}.yaml"
        config_path.write_text(yaml.safe_dump(data, sort_keys=False))
        source_configs[entry_id] = (config_path, data)
        config_paths[data["config_id"]] = config_path
        providers = data["selected_providers"]
        allowance = _allowance(settings, stage, providers, entry_id=entry_id)
        prep = sum(allowance[k] for k in ("registration_seconds", "provision_seconds", "readiness_seconds", "stabilization_seconds"))
        if profile:
            prep += profile_spec(profile)["barrier_seconds"]
        if stage == "week":
            parts = entry_id.split("-")
            day = int(parts[2][1:])
            window = parts[3].lower()
            date_utc = date.fromisoformat(str(inputs["first_day_utc"])) + timedelta(days=day - 1)
            scheduled = _utc(f"{date_utc}T{inputs['windows_utc'][window]}+00:00", "WEEK window")
        elif stage == "acceptance" and entry_id in {"V-S1", "V-S3"} and profile != ACCEPTANCE_PROFILE:
            scheduled = _utc(inputs["start_utc"], "acceptance.start_utc")
            window = None
        elif stage == "acceptance" and entry_id in {"V-S1", "V-S3"} and profile == ACCEPTANCE_PROFILE:
            scheduled = None
            window = None
        elif stage == "calibration" and profile == CALIBRATION_PROFILE:
            scheduled = None
            window = None
        elif stage != "acceptance":
            scheduled = _utc(inputs["start_utc"], f"{stage}.start_utc")
            window = "night"
        else:
            scheduled = None
            window = None
        launch_at = scheduled - timedelta(seconds=prep) if scheduled else now
        if profile == ACCEPTANCE_PROFILE and entry_id in {"V-S1", "V-S3"}:
            launch_at = _utc(_profile_dispatch_window(inputs, "acceptance",
                _profile_execution_window(inputs, "acceptance"))["not_before_utc"],
                "acceptance.dispatch_window_utc.not_before_utc")
        elif profile == CALIBRATION_PROFILE:
            launch_at = _utc(_profile_dispatch_window(inputs, "calibration",
                _profile_execution_window(inputs, "calibration"))["not_before_utc"],
                "calibration.dispatch_window_utc.not_before_utc")
        if scheduled and launch_at <= now:
            raise ValueError(f"{entry_id}: setup lead time is already missed; explicitly review new timing, no backfill")
        end = (scheduled or now) + timedelta(seconds=allowance["measurement_seconds"] + allowance["measurement_shutdown_seconds"] + allowance["retrieval_seconds"] + allowance["cleanup_seconds"])
        if _utc(settings["credential_refresh"]["valid_through_utc"], "credential_refresh.valid_through_utc") < end:
            raise ValueError(f"credential_refresh evidence does not cover {entry_id} through cleanup")
        if scheduled:
            schedule_rows.append([entry_id, data["config_id"], data["experiment_id"], scheduled.isoformat(), window, data["admission"]["priority"]])
        scenario = "S1" if alias in {"s1", "s1_acceptance"} else "S3" if alias in {"s3", "s3_acceptance"} else None
        entries.append({"entry_id": entry_id, "config_alias": alias, "scenario": scenario,
            "priority": data["admission"]["priority"], "config_path": str(config_path), "scheduled_start": scheduled.isoformat() if scheduled else None,
            "launch_at": launch_at.isoformat(), "private_root": str(private / entry_id),
            "requires_supervision": entry_id.endswith("FAIL"),
            "acceptance_check": "server_and_controller_loss" if entry_id.endswith("FAIL") else None,
            "config_sha256": _sha(config_path), "source_config_path": str(template),
            "source_config_sha256": _sha(template)})
    # Each row must resolve against its exact per-entry config, not the last P0/P1 config.
    for entry in entries:
        config_path, data = source_configs[entry["entry_id"]]
        scheduled = entry["scheduled_start"]
        schedule_path = None
        selected_entry = None
        if scheduled:
            row = next(row for row in schedule_rows if row[0] == entry["entry_id"])
            buffer = io.StringIO(newline="")
            writer = csv.writer(buffer)
            writer.writerow(["entry_id", "config_id", "experiment_id", "scheduled_start", "window_label", "priority"])
            writer.writerow(row)
            schedule_path = private / f"{entry['entry_id']}-schedule.csv"
            schedule_path.write_text(buffer.getvalue())
            selected_entry = load_schedule(schedule_path, config_paths={data["config_id"]: config_path}, repository_root=root)[0]
        initialized = initialize_campaign(config_path, root, result, schedule_entry=selected_entry)
        if auxiliary_opt_in:
            azure_child = next((child for child in initialized.children
                if str(getattr(child.provider, "value", child.provider)) == "azure"), None)
            if azure_child is None:
                raise ValueError("Azure auxiliary cleanup scope has no initialized Azure parent")
            azure_cleanup_owners.append(_azure_auxiliary_owner(entry["entry_id"], azure_child))
        parent_path = Path(initialized.parent_manifest_path)
        if not parent_path.is_absolute():
            parent_path = root / parent_path
        if auxiliary_opt_in:
            # Keep the live parent path in runtime/entry identity. The cohort
            # source hash binds a private byte-for-byte initialization snapshot,
            # because normal lifecycle updates rewrite the live manifest.
            snapshot_path = private / entry["entry_id"] / "initialized-parent-manifest.json"
            snapshot_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            snapshot_path.parent.chmod(0o700)
            if parent_path.is_symlink() or not parent_path.is_file() or any(parent.is_symlink() for parent in parent_path.parents):
                raise ValueError("initialized campaign parent must be a regular non-symlink file before snapshot")
            parent_bytes = parent_path.read_bytes()
            write_exclusive(snapshot_path, parent_bytes)
            snapshot_path.chmod(0o600)
            snapshot_sha256 = hashlib.sha256(parent_bytes).hexdigest()
            if _sha(snapshot_path) != snapshot_sha256:
                raise ValueError("initialized campaign parent snapshot hash mismatch")
            azure_parent_snapshots[entry["entry_id"]] = (snapshot_path, snapshot_sha256)
        providers = data["selected_providers"]
        allowance = _allowance(settings, stage, providers, entry_id=entry["entry_id"])
        provider_runtime = {}
        for provider in providers:
            row = copy.deepcopy(settings["providers"][provider])
            row.pop("sources", None)
            sources = {"primary_review_path": None}
            if provider in {"aws", "azure"}:
                sources["expiry_cost_path"] = str(support_paths[provider])
            else:
                handoff = private / entry["entry_id"] / "gcp-cost-handoff.json"
                handoff.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                priced = cost_payload["provider_run_envelope_templates"][data["config_id"]][provider]
                atomic_write_json(handoff, {"source_path": str(cost_path), "source_sha256": _sha(cost_path),
                    "prices_checked_on": cost_payload["prices_checked_on"],
                    "estimated_total_usd": float(sum(Decimal(str(c["amount_usd"])) for c in priced["components"].values())),
                    "units": {"vm_count": 2, "lifetime_seconds": data["admission"]["lifetime_cap_seconds"]}})
                sources.update(workflow_cost_handoff_path=str(handoff), support_cost_path=str(support_paths["gcp"]))
            interfaces = row.pop("diagnostic_interfaces", None)
            if not interfaces or set(interfaces) != {"vm_a", "vm_b"}:
                raise ValueError(f"set providers.{provider}.diagnostic_interfaces from actual VM interface names")
            tf = root / "terraform" / provider
            provider_runtime[provider] = {"options": row, "bootstrap_template": str(bootstrap_path),
                "terraform_directory": str(tf), "sources": sources}
        runtime = {"schema_version": 1, "initialized_campaign_manifest": str(parent_path),
            "ledger": {"path": str(ledger_path), "scope_id": ledger["scope_id"], "initialize_if_missing": True},
                "allowance": allowance,
                "copy_retry_seconds": inputs.get("copy_retry_seconds", 30),
            "access": {"timeouts": settings.get("timeouts", {"ssh_seconds": 30, "command_seconds": 120,
                "cloud_init_seconds": 300, "private_path_seconds": 60, "server_seconds": 60, "transfer_seconds": 300}),
                "readiness": {"poll_interval_seconds": 2, "attempt_ceiling": 150},
                "bootstrap": _bootstrap(settings.get("prebuilt_bundle")), "guest_remote_root": "/var/lib/cnb/work",
                "diagnostic_interfaces": {p: settings["providers"][p]["diagnostic_interfaces"] for p in providers}},
            "providers": provider_runtime, "approval_refs": approvals}
        if data["admission"].get("workflow_profile"):
            runtime.update(workflow_profile=data["admission"]["workflow_profile"],
                provider_registration_timeout_seconds=provider_registration,
                plan_review_admission_path=str(stage_private / "plan-review-admission.lock"))
        if stage in {"day", "acceptance", "calibration"}:
            stage_private = Path(state_root) / stage_id
            runtime["observer"] = {"stage_id": stage_id, "campaign_id": stage_id,
                "entry_id": entry["entry_id"],
                "workspace": runtime["access"]["bootstrap"]["workspace"],
                "state_path": str(stage_private / "observer-aggregate.json"),
                "journal_path": str(stage_private / "campaign-journal.json"),
                "prepared_stage_path": str(stage_private / "prepared-stage.json"),
                "bindings_path": str(stage_private / "observer-bindings.json"),
                "launch_receipt_root": str(stage_private / "observer-launch-receipts")}
        if settings.get("archive_sink") is not None:
            runtime["archive_sink"] = copy.deepcopy(settings["archive_sink"])
            if stage in {"day", "week"}:
                review = _gate_rows(settings, stage)["size_export_review"].get("fields", {})
                runtime["archive_sink"]["controller_quota_free_bytes"] = review["controller_quota_free_bytes"]
                runtime["archive_sink"]["controller_quota_evidence_sha256"] = review["controller_quota_evidence_sha256"]
        runtime_path = private / entry["entry_id"] / "runtime.json"
        runtime_path.parent.mkdir(exist_ok=True, mode=0o700)
        atomic_write_json(runtime_path, runtime)
        entry.update(schedule_path=str(schedule_path) if schedule_path else None,
            row_id=entry["entry_id"] if scheduled else None, runtime_config_path=str(runtime_path),
            runtime_config_sha256=_sha(runtime_path),
            cost_inputs_path=str(cost_path), parent_manifest_path=str(parent_path),
            run_ids={str(child.provider): child.run_id for child in initialized.children})
    stage_data = {"stage_id": stage_id, "stage": stage, "controller_session_id": session,
        "status": "DRAFT_UNAPPROVED" if draft else "PREPARED",
        "ledger_path": str(ledger_path), "scope_id": ledger["scope_id"], "state_root": str(state_root),
        "results_root": str(result), "repository_root": str(root), "entries": entries,
        "minimum_free_bytes": int(settings.get("minimum_free_bytes", 0)),
        "operator_log_path": str(Path(state_root) / "campaign.log"),
        "observer_identity": {provider: {key: settings["providers"][provider][key] for key in
            ({"aws": ("account_id",), "azure": ("subscription_id", "tenant_id"),
             "gcp": ("project_id",)}[provider])} for provider in sorted(settings["providers"])},
        "credential_valid_through_utc": settings["credential_refresh"]["valid_through_utc"],
        "approval_refs": approvals, "cost_input_path": str(cost_path),
        "cost_input_sha256": _sha(cost_path),
        "backup_destination": settings.get("backup_destination"), "notification_hook": settings.get("notification_hook")}
    if stage in {"day", "acceptance", "calibration"}:
        stage_data.update(campaign_id=stage_id,
            observer_bindings_path=str(private / "observer-bindings.json"),
            observer_launch_receipt_root=str(private / "observer-launch-receipts"))
    if stage == "week" and not draft:
        stage_data["dispatch_policy"] = _weekly_dispatch_policy(settings, approvals)
    elif stage == "acceptance":
        if not draft:
            stage_data["dispatch_policy"] = _acceptance_dispatch_policy(settings, approvals)
        normal = [entry for entry in entries if entry["entry_id"] in {"V-S1", "V-S3"}]
        participants = [{"entry_id": entry["entry_id"], "provider": provider, "run_id": run_id}
            for entry in normal for provider, run_id in sorted(entry["run_ids"].items())]
        if len(participants) != 6:
            raise ValueError("paired acceptance measurement barrier requires exactly six initialized normal children")
        barrier = {"schema_version": 1, "barrier_id": f"{stage_id}-normal-measurement-terminal",
            "expected_participants": participants,
            "barrier_root": str(Path(state_root) / stage_id / "measurement-barriers" / "normal-terminal")}
        start_barrier = {"schema_version": 1, "barrier_id": f"{stage_id}-normal-measurement-start",
            "expected_participants": participants,
            "barrier_root": str(Path(state_root) / stage_id / "measurement-barriers" / "normal-start")}
        if inputs.get("workflow_profile") == ACCEPTANCE_PROFILE:
            execution_window = _profile_execution_window(inputs, "acceptance")
            start_barrier.update(start_policy="all_ready", execution_window=execution_window,
                dispatch_window_utc=_profile_dispatch_window(inputs, "acceptance", execution_window),
                ready_window_seconds=600)
        else:
            start_barrier["start_utc"] = _utc(inputs["start_utc"], "acceptance.start_utc").isoformat()
        stage_data["post_measurement_barrier"] = barrier
        stage_data["measurement_start_barrier"] = start_barrier
        if inputs.get("workflow_profile") == ACCEPTANCE_PROFILE:
            stage_data["dispatch_window_utc"] = start_barrier["dispatch_window_utc"]
        for entry in normal:
            runtime_path = Path(entry["runtime_config_path"])
            runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
            runtime["post_measurement_barrier"] = barrier
            runtime["measurement_start_barrier"] = start_barrier
            if inputs.get("workflow_profile") == ACCEPTANCE_PROFILE:
                runtime["dispatch_window_utc"] = start_barrier["dispatch_window_utc"]
            atomic_write_json(runtime_path, runtime)
            entry["runtime_config_sha256"] = _sha(runtime_path)
        for entry in entries:
            if not entry.get("requires_supervision"):
                continue
            declaration = settings.get("stage_inputs", {}).get("acceptance", {}).get("failure_checks", {}).get(entry["entry_id"], {})
            reuse = declaration.get("reuse") if isinstance(declaration, dict) else None
            if reuse is None:
                continue
            if not isinstance(reuse, dict):
                raise ValueError(f"failure_checks.{entry['entry_id']}.reuse must identify a source-bound completed check")
            from .acceptance_review import _failure_review, _runtime_scope_sha256
            source = _path(reuse.get("source"), f"failure_checks.{entry['entry_id']}.reuse.source", file=True)
            source_hash = reuse.get("sha256")
            if not isinstance(source_hash, str) or _sha(source) != source_hash:
                raise ValueError(f"failure_checks.{entry['entry_id']}.reuse source hash differs")
            _failure_review({"path": str(source), "sha256": source_hash}, required_entries={entry["entry_id"]})
            old = yaml.safe_load(source.read_text(encoding="utf-8"))["checks"][entry["entry_id"]]
            identity = old.get("config_identity", {})
            parent = json.loads(Path(entry["parent_manifest_path"]).read_text(encoding="utf-8"))
            if (identity.get("config_sha256") != entry["config_sha256"]
                    or identity.get("source_config_sha256") != entry["source_config_sha256"]
                    or identity.get("runtime_scope_sha256") != _runtime_scope_sha256(entry["runtime_config_path"])
                    or identity.get("implementation_git_commit") != parent.get("implementation_git_commit")):
                raise ValueError(f"reused {entry['entry_id']} failure evidence does not match the exact prepared config/runtime sources")
            entry["reused_failure_check"] = {"source_path": str(source), "source_sha256": source_hash,
                "attempt_id": old["attempt_id"]}
    elif stage == "calibration":
        entry = entries[0]
        participants = [{"entry_id": entry["entry_id"], "provider": provider, "run_id": run_id}
            for provider, run_id in sorted(entry["run_ids"].items())]
        if len(participants) != 3:
            raise ValueError("calibration start barrier requires exactly three all-cloud children")
        start_barrier = {"schema_version": 1, "barrier_id": f"{stage_id}-calibration-measurement-start",
            "expected_participants": participants,
            "barrier_root": str(Path(state_root) / stage_id / "measurement-barriers" / "calibration-start")}
        if runtime.get("workflow_profile") == CALIBRATION_PROFILE:
            execution_window = _profile_execution_window(inputs, "calibration")
            start_barrier.update(start_policy="all_ready", execution_window=execution_window,
                dispatch_window_utc=_profile_dispatch_window(inputs, "calibration", execution_window),
                ready_window_seconds=600)
        else:
            start_barrier["start_utc"] = _utc(inputs["start_utc"], "calibration.start_utc").isoformat()
        stage_data["measurement_start_barrier"] = start_barrier
        if runtime.get("workflow_profile") == CALIBRATION_PROFILE:
            stage_data["dispatch_window_utc"] = start_barrier["dispatch_window_utc"]
        runtime_path = Path(entry["runtime_config_path"])
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        runtime["measurement_start_barrier"] = start_barrier
        if runtime.get("workflow_profile") == CALIBRATION_PROFILE:
            runtime["dispatch_window_utc"] = start_barrier["dispatch_window_utc"]
        atomic_write_json(runtime_path, runtime)
        entry["runtime_config_sha256"] = _sha(runtime_path)
    if auxiliary_opt_in:
        expected_entries = {"V-S1", "V-S3"} if stage == "acceptance" else {"CAL-S1"}
        if {owner["entry_id"] for owner in azure_cleanup_owners} != expected_entries:
            raise ValueError("Azure auxiliary cleanup draft must bind exactly the supported S1/S3 or CAL-S1 owner set")
        cohort_id = f"{stage_id}-azure-shared-cleanup"
        cohort_root = private / "azure-shared-cleanup-state"
        manifest_path = private / "azure-auxiliary-cleanup-scope.json"
        card_path = Path(approvals["azure_shared_cleanup"])
        profile = "normal_pair" if stage == "acceptance" else "calibration_self"
        watcher_group = "/subscriptions/188a5848-76fb-4d57-8f2a-32c09284d486/resourceGroups/NetworkWatcherRG"
        watcher_id = watcher_group + "/providers/Microsoft.Network/networkWatchers/NetworkWatcher_swedencentral"
        selected_entries = [entry for entry in entries if entry["entry_id"] in expected_entries]
        for entry in selected_entries:
            runtime_path = Path(entry["runtime_config_path"])
            runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
            runtime["azure_auxiliary_cleanup"] = {"schema_version": 1, "cohort_id": cohort_id,
                "state_root": str(cohort_root), "subscription_id": "188a5848-76fb-4d57-8f2a-32c09284d486",
                "region": "swedencentral", "resource_group_id": watcher_group, "watcher_id": watcher_id,
                "profile": profile, "owners": azure_cleanup_owners, "card_path": str(card_path),
                "cohort_manifest_path": str(manifest_path)}
            atomic_write_json(runtime_path, runtime)
            entry["runtime_config_sha256"] = _sha(runtime_path)
        members = []
        for entry, owner in zip(selected_entries, azure_cleanup_owners):
            parent_snapshot_path, parent_snapshot_sha256 = azure_parent_snapshots[entry["entry_id"]]
            members.append({"entry_id": owner["entry_id"], "run_id": owner["run_id"],
                "config_sha256": owner["config_sha256"],
                "runtime_path": entry["runtime_config_path"], "runtime_sha256": entry["runtime_config_sha256"],
                # Existing member fields bind the immutable initialized-parent
                # snapshot. The mutable live path remains in runtime config.
                "parent_manifest_path": str(parent_snapshot_path),
                "parent_manifest_sha256": parent_snapshot_sha256,
                "config_path": entry["config_path"], "source_config_path": entry["source_config_path"],
                "source_config_sha256": entry["source_config_sha256"]})
        scope = {"schema_version": 1, "cohort_id": cohort_id, "profile": profile,
            "subscription_id": "188a5848-76fb-4d57-8f2a-32c09284d486", "region": "swedencentral",
            "resource_group_id": watcher_group, "watcher_id": watcher_id,
            "owners": azure_cleanup_owners, "members": members}
        atomic_write_json(manifest_path, scope)
        stage_data["azure_auxiliary_cleanup_scope"] = {"path": str(manifest_path),
            "sha256": _sha(manifest_path), "card_path": str(card_path)}
    if stage == "acceptance":
        from .failure_waiver import bind_prepared_acceptance, source_waiver
        waiver = source_waiver(settings)
        if waiver is not None:
            normal_entries = [entry for entry in entries if entry["entry_id"] in {"V-S1", "V-S3"}]
            # Bind only after acceptance barriers and other runtime finalization
            # have been written and entry runtime hashes updated.
            stage_data["failure_waiver"] = bind_prepared_acceptance(waiver, normal_entries)
    if draft:
        stage_data["pending_prerequisites"] = [
            f"source-bound approval evidence: {key}" for key in sorted(approvals)]
        stage_data["approval_files_created"] = False
    atomic_write_json(private / "prepared.json", stage_data)
    return stage_data


def activate_prepared_stage(prepared: dict, settings: dict, root: Path, *, preflight) -> dict:
    """Bind genuine approval sources to an existing draft without regenerating entries."""
    if not isinstance(prepared, dict) or prepared.get("status") != "DRAFT_UNAPPROVED":
        raise ValueError("only an explicitly unapproved prepared-stage draft can be activated")
    stage, stage_id = prepared.get("stage"), prepared.get("stage_id")
    if stage not in {"acceptance", "calibration"} or not isinstance(stage_id, str) or not stage_id:
        raise ValueError("prepared draft identity is incomplete")
    entries = prepared.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("prepared draft has no frozen entries")
    for entry in entries:
        for path_key, digest_key in (("config_path", "config_sha256"), ("source_config_path", "source_config_sha256"),
                                     ("runtime_config_path", "runtime_config_sha256")):
            path = _path(entry.get(path_key), f"prepared {path_key}", file=True)
            if _sha(path) != entry.get(digest_key):
                raise ValueError(f"prepared frozen source changed: {entry.get('entry_id')} {path_key}")
    cost_path = _path(prepared.get("cost_input_path"), "prepared cost input", file=True)
    if _sha(cost_path) != prepared.get("cost_input_sha256"):
        raise ValueError("prepared cost input changed")
    aliases = {entry["config_alias"]: entry["source_config_path"] for entry in entries}
    errors = validate_stage_inputs({"stage": stage, "configs": aliases}, settings, Path(root),
        local_preparation=True, draft=False)
    if errors:
        raise ValueError("draft prerequisites remain incomplete: " + "; ".join(errors))
    private = _path(prepared.get("state_root"), "prepared state root") / stage_id
    refs = prepared.get("approval_refs")
    if not isinstance(refs, dict):
        raise ValueError("draft approval destinations are malformed")
    cleanup_scope = prepared.get("azure_auxiliary_cleanup_scope")
    cleanup_path = refs.get("azure_shared_cleanup")
    present = {key for key, value in refs.items() if Path(value).exists() or Path(value).is_symlink()}
    if present - {"azure_shared_cleanup"}:
        raise ValueError("ordinary approval destinations must remain absent until financial preflight succeeds")
    if cleanup_scope is not None:
        scope_path = _path(cleanup_scope.get("path"), "Azure auxiliary cleanup frozen scope", file=True)
        if _sha(scope_path) != cleanup_scope.get("sha256") or cleanup_path != cleanup_scope.get("card_path"):
            raise ValueError("Azure auxiliary cleanup frozen scope or card path changed")
        if "azure_shared_cleanup" not in present:
            raise ValueError("Azure auxiliary cleanup draft needs its separately reviewed approval card before activation")
    elif "azure_shared_cleanup" in refs:
        raise ValueError("Azure auxiliary cleanup approval reference has no frozen cohort scope")
    if not callable(preflight):
        raise ValueError("activation requires a read-only financial preflight callback")
    runtime_configs = {}
    for entry in entries:
        runtime_path = _path(entry.get("runtime_config_path"), "prepared runtime config", file=True)
        if _sha(runtime_path) != entry.get("runtime_config_sha256"):
            raise ValueError(f"prepared frozen runtime changed: {entry.get('entry_id')}")
        from .lifecycle_runtime import load_runtime_config
        runtime_configs[entry["entry_id"]] = load_runtime_config(runtime_path,
            selected_providers=set(entry.get("run_ids", {})), allow_missing_approval_evidence=True)
        if cleanup_scope is not None and not runtime_configs[entry["entry_id"]].get(
                "azure_auxiliary_cleanup", {}).get("authorization"):
            raise PermissionError("Azure auxiliary cleanup approval card is not genuinely approved and source-bound")
    candidate = copy.deepcopy(prepared)
    candidate["status"] = "READY_FOR_ADMISSION"
    candidate["approval_files_created"] = True
    preflight(candidate, runtime_configs=runtime_configs)
    # The callback is read-only; rebind all frozen sources before publishing
    # evidence in case an input changed during validation.
    for entry in entries:
        for path_key, digest_key in (("config_path", "config_sha256"), ("source_config_path", "source_config_sha256"),
                                     ("runtime_config_path", "runtime_config_sha256")):
            path = _path(entry.get(path_key), f"prepared {path_key}", file=True)
            if _sha(path) != entry.get(digest_key):
                raise ValueError(f"prepared frozen source changed during activation: {entry.get('entry_id')} {path_key}")
    refs = _write_evidence(private / "approvals", _gate_rows(settings, stage),
        provider_scope(settings["providers"]), settings)
    if cleanup_scope is not None:
        refs["azure_shared_cleanup"] = cleanup_path
    if refs != prepared.get("approval_refs"):
        raise ValueError("activated approval paths differ from preallocated draft references")
    activated = copy.deepcopy(prepared)
    if stage == "acceptance":
        activated["dispatch_policy"] = _acceptance_dispatch_policy(settings, refs)
    activated["status"] = "READY_FOR_ADMISSION"
    activated["approval_files_created"] = True
    activated["pending_prerequisites"] = []
    activated["activated_at_utc"] = datetime.now(timezone.utc).isoformat()
    atomic_write_json(private / "prepared.json", activated)
    return activated
