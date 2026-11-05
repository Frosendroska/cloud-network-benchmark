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

from .admission import COST_COMPONENTS, WorkflowAllowance, minimum_protocol_seconds
from .config import resolve_campaign
from .contracts.common import ExperimentMode
from .manifests import initialize_campaign
from .paths import atomic_write_json
from .scheduling import load_schedule
from .campaign_identity import check_provider_identities

STAGES = {"acceptance", "calibration", "day", "week"}
CONFIGS = {
    "s1_acceptance": "configs/tests/exp-903-three-provider-smoke.yaml",
    "aws_failure": "configs/tests/exp-900-single-provider.yaml",
    "azure_failure": "configs/tests/exp-901-azure-single-provider.yaml",
    "gcp_failure": "configs/tests/exp-902-gcp-single-provider.yaml",
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


def _selected(plan):
    stage = plan.get("stage")
    if stage not in STAGES:
        raise ValueError("stage must be acceptance, calibration, day or week")
    if stage == "acceptance":
        # One registered failure attempt per cloud, with supervised intervention.
        return [("V-S1", "s1_acceptance"), ("V-S3", "s3_acceptance"),
                ("V-A-FAIL", "aws_failure"), ("V-Z-FAIL", "azure_failure"),
                ("V-G-FAIL", "gcp_failure")]
    if stage in {"calibration", "day"}:
        return [("CAL-S1" if stage == "calibration" else "DAY", stage)]
    return [(f"M-S{scenario}-D{day}-{window.upper()}", f"s{scenario}")
            for day in range(1, 8) for window in WINDOWS for scenario in (1, 3)]


def _allowance(settings, stage, providers):
    try:
        row = copy.deepcopy(settings["stage_inputs"][stage]["allowance"])
    except KeyError as exc:
        raise ValueError(f"set stage_inputs.{stage}.allowance from measured setup/copy/cleanup times") from exc
    row["provider_leads_seconds"] = {p: row["provider_leads_seconds"][p] for p in providers}
    row["completed_stages"] = []
    allowance = WorkflowAllowance(**row)
    mode = {"acceptance": "acceptance", "calibration": "calibration",
            "day": "diagnostic_day", "week": "short_study"}[stage]
    if allowance.measurement_seconds < minimum_protocol_seconds(ExperimentMode(mode)) + allowance.measurement_shutdown_seconds:
        raise ValueError(f"{stage} allowance.measurement_seconds does not cover the complete protocol")
    cap = {"acceptance": 1200, "calibration": 1800, "day": 87600, "week": 1200}[stage]
    if allowance.total_seconds + max(allowance.provider_leads_seconds.values()) > cap:
        raise ValueError(f"{stage} complete workflow plus provider expiry lead exceeds registered {cap}s cap")
    if allowance.stabilization_seconds != 60:
        raise ValueError("allowance must preserve 60-second stabilization")
    return row


def _gate_rows(settings, stage):
    declarations = {**settings.get("approvals", {}),
                    **settings.get("stage_inputs", {}).get(stage, {}).get("approvals", {})}
    required = {"f10_offline", "paid_authorization", "quota_review"}
    if stage != "acceptance":
        required.add("paid_acceptance")
    required |= {"acceptance": set(), "calibration": {"calibration_authorized"},
                 "day": {"common_n", "diagnostic_day_authorized", "size_export_review"},
                 "week": {"common_n", "study_window"}}[stage]
    for key in sorted(required):
        row = declarations.get(key)
        if not isinstance(row, dict) or row.get("confirmed") is not True:
            raise ValueError(f"explicitly confirm {key} against real source evidence in operator settings")
        _path(row.get("source"), f"approvals.{key}.source", file=True)
        if not isinstance(row.get("fields", {}), dict):
            raise ValueError(f"approvals.{key}.fields must be a map")
    return {key: declarations[key] for key in required}


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


def validate_stage_inputs(plan: dict, settings: dict, root: Path) -> list[str]:
    """Read-only preflight; an empty list is local readiness, not cloud acceptance."""
    errors = []
    try:
        entries = _selected(plan)
        stage = plan["stage"]
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
        _gate_rows(settings, stage)
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
        elif stage != "acceptance":
            _utc(settings["stage_inputs"][stage].get("start_utc"), f"stage_inputs.{stage}.start_utc")
        for _, alias in entries:
            path = root / plan.get("configs", {}).get(alias, CONFIGS[alias])
            data = yaml.safe_load(path.read_text())
            providers = data["selected_providers"]
            _allowance(settings, stage, providers)
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
                elif (set(options["images"]) != {"europe-west3", "europe-west2"} or not all(
                        isinstance(image, str) and re.fullmatch(r"projects/ubuntu-os-cloud/global/images/ubuntu-2404-lts-amd64-[a-z0-9-]+", image)
                        for image in options["images"].values())):
                    raise ValueError("set concrete GCP Ubuntu image resource identities for Frankfurt and London")
        # Credential refresh is a reviewed operator fact, never inferred from a login.
        refresh = settings.get("credential_refresh", {})
        if not refresh.get("confirmed") or not refresh.get("valid_through_utc"):
            raise ValueError("record tested cloud-supported credential_refresh and valid_through_utc")
        _path(refresh.get("source"), "credential_refresh.source", file=True)
        valid_through = _utc(refresh["valid_through_utc"], "credential_refresh.valid_through_utc")
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(seconds={"acceptance": 6000, "calibration": 1800, "day": 87600, "week": 1200}[stage])
        if stage == "week":
            last = max(_utc(f"{start + timedelta(days=6)}T{time}+00:00", "WEEK final slot") for time in windows.values())
            horizon = last + timedelta(seconds=1200)
        elif stage in {"calibration", "day"}:
            horizon = _utc(settings["stage_inputs"][stage]["start_utc"], "start_utc") + timedelta(seconds=1800 if stage == "calibration" else 87600)
        if valid_through < horizon:
            raise ValueError(f"credential_refresh evidence must remain valid through stage cleanup ({horizon.isoformat()})")
        missing_tools = [name for name in ("terraform", "aws", "az", "gcloud", "ssh", "scp", "systemctl") if shutil.which(name) is None]
        if missing_tools:
            raise ValueError("install required controller tools on Linux: " + ", ".join(missing_tools))
        if stage == "day":
            review = _gate_rows(settings, stage)["size_export_review"].get("fields", {})
            for name in ("measured_log_bytes", "measured_seconds", "expected_raw_bytes", "export_verified_bytes"):
                if type(review.get(name)) not in {int, float} or not math.isfinite(review[name]) or review[name] <= 0:
                    raise ValueError(f"DAY size_export_review requires actual positive {name} from acceptance/calibration")
            if review.get("export_checksum_verified") is not True:
                raise ValueError("DAY needs actual checksum-verified export evidence")
            expected = int(review["expected_raw_bytes"])
            if expected < review["measured_log_bytes"] / review["measured_seconds"] * 86400:
                raise ValueError("DAY projected raw bytes undercount measured log growth")
            if int(settings.get("minimum_free_bytes", 0)) < expected * 2:
                raise ValueError("minimum_free_bytes must cover DAY native bundles plus verified export staging")
        minimum = settings.get("minimum_free_bytes")
        if type(minimum) is not int or minimum <= 0:
            raise ValueError("set positive minimum_free_bytes from measured stage storage needs")
        disk_path = Path(settings["results_root"])
        while not disk_path.exists():
            disk_path = disk_path.parent
        if shutil.disk_usage(disk_path).free < minimum:
            raise ValueError("insufficient local free disk for the declared stage requirement")
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
    templates = {}
    for alias, path in CONFIGS.items():
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
            rows[provider] = {"envelope_id": f"{config_id}-{provider}", "provider": provider,
                "priced_on": checked, "source_kind": "provider-price-input", "regions": regions,
                "unit_basis": "one-two-vm-provider-run", "components": normalized}
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
    return {"schema_version": 1, "cost_input_id": pricing.get("id") or f"operator-prices-{checked}",
        "prices_checked_on": checked, "source_scope": [str(source)], "source_sha256": _sha(source),
        "source_identity_kind": "operator-reviewed-dated-price-source", "currency": "USD",
        "funding": funding, "refresh_required_before_paid_execution": False, "live_quote": False,
        "provider_support": {"aws": {"sha256": hashlib.sha256(aws_raw).hexdigest(),
            "per_region_usd": str(support["aws"]["worst_planned_cost_per_region"])}},
        "provider_run_envelope_templates": templates}


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


def _bootstrap():
    # Existing source/patch identities verified by SourceBuild, not invented prices.
    return {"sources": [
        {"tool": "sockperf", "source_url": "https://codeload.github.com/Mellanox/sockperf/tar.gz/5ebd327da983225321818c0355db922515e026bd",
         "source_reference": "sockperf-5ebd327da983225321818c0355db922515e026bd", "archive_name": "sockperf.tar.gz",
         "source_sha256": "a35a1daf310551b9f675af4fb7d95a6a10389fda4c7605d70a21554be0773f8e",
         "upstream_commit": "5ebd327da983225321818c0355db922515e026bd",
         "observation_patch_path": "tool_patches/sockperf-3.10-acquisition.patch",
         "observation_patch_sha256": "3f0bb59a3c70b247e5ebde77254e1427c35dc6ec39e2a78a3c779cff7015a285"},
        {"tool": "iperf3", "source_url": "https://codeload.github.com/esnet/iperf/tar.gz/2acfcfe94e928e74542c9f107e02aa6dd4748a79",
         "source_reference": "iperf-2acfcfe94e928e74542c9f107e02aa6dd4748a79", "archive_name": "iperf3.tar.gz",
         "source_sha256": "d6b4d2c7b20157c5b7fbd2de66f22bd3428958b5f2e28899151b6e22745886fe",
         "upstream_commit": "2acfcfe94e928e74542c9f107e02aa6dd4748a79",
         "observation_patch_path": "tool_patches/iperf-3.17.1-acquisition.patch",
         "observation_patch_sha256": "11b96daadb4a1f17dc24376bb3d0d7feec330efeedc550b1979b1566e9b81661"}],
        "remote_root": "/var/lib/cnb", "workspace": "/var/lib/cnb/work", "install_prefix": "/var/lib/cnb/install"}


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
    if prepared.get("stage") != "acceptance" or set(entries) != {"V-S1", "V-S3", "V-A-FAIL", "V-Z-FAIL", "V-G-FAIL"}:
        raise ValueError("paid_acceptance source must be the original complete prepared acceptance stage")
    checks = {}
    declarations = settings.get("stage_inputs", {}).get("acceptance", {}).get("failure_checks", {})
    for registered, provider in (("V-A-FAIL", "aws"), ("V-Z-FAIL", "azure"), ("V-G-FAIL", "gcp")):
        entry = entries[registered]
        parent = json.loads(_path(entry["parent_manifest_path"], "acceptance manifest", file=True).read_text())
        checks[registered] = {"provider": provider, "attempt_id": parent["campaign_id"]}
        for action in ("server_failure", "controller_loss", "expiry", "cleanup"):
            observation = declarations.get(registered, {}).get(action, {})
            if observation.get("confirmed") is not True:
                raise ValueError(f"record supervised acceptance.failure_checks.{registered}.{action} confirmation and actual source before research")
            source = _path(observation.get("source"), f"failure_checks.{registered}.{action}.source", file=True)
            checks[registered][action] = {"status": "passed", "source_path": str(source), "source_sha256": _sha(source)}
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
                  results_root: Path, clock) -> dict[str, Any]:
    """Prepare identities durably; only read-only identity commands may run."""
    errors = validate_stage_inputs(plan, settings, root)
    if errors:
        raise ValueError("; ".join(errors))
    root = Path(root).resolve()
    stage = plan["stage"]
    now = _now(clock)
    stage_id = f"{stage}-{now:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    private = Path(state_root) / stage_id
    private.mkdir(parents=True, mode=0o700)
    private.chmod(0o700)
    result = Path(results_root) / stage_id
    result.mkdir(parents=True)
    inputs = settings["stage_inputs"][stage]
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
    for index, (entry_id, alias) in enumerate(_selected(plan)):
        template = root / plan.get("configs", {}).get(alias, CONFIGS[alias])
        data = _frozen_config(yaml.safe_load(template.read_text()), alias, settings, entry_id)
        priced_rows = cost_payload["provider_run_envelope_templates"][data["config_id"]]
        estimated = sum(Decimal(str(component["amount_usd"]))
            for provider in data["selected_providers"] for component in priced_rows[provider]["components"].values())
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
        allowance = _allowance(settings, stage, providers)
        prep = sum(allowance[k] for k in ("registration_seconds", "provision_seconds", "readiness_seconds", "stabilization_seconds"))
        if stage == "week":
            parts = entry_id.split("-")
            day = int(parts[2][1:])
            window = parts[3].lower()
            date_utc = date.fromisoformat(str(inputs["first_day_utc"])) + timedelta(days=day - 1)
            scheduled = _utc(f"{date_utc}T{inputs['windows_utc'][window]}+00:00", "WEEK window")
        elif stage != "acceptance":
            scheduled = _utc(inputs["start_utc"], f"{stage}.start_utc")
            window = "night"
        else:
            scheduled = None
            window = None
        launch_at = scheduled - timedelta(seconds=prep) if scheduled else now
        if scheduled and launch_at <= now:
            raise ValueError(f"{entry_id}: setup lead time is already missed; explicitly review new timing, no backfill")
        end = (scheduled or now) + timedelta(seconds=allowance["measurement_seconds"] + allowance["measurement_shutdown_seconds"] + allowance["retrieval_seconds"] + allowance["cleanup_seconds"])
        if _utc(settings["credential_refresh"]["valid_through_utc"], "credential_refresh.valid_through_utc") < end:
            raise ValueError(f"credential_refresh evidence does not cover {entry_id} through cleanup")
        if scheduled:
            schedule_rows.append([entry_id, data["config_id"], data["experiment_id"], scheduled.isoformat(), window, data["admission"]["priority"]])
        entries.append({"entry_id": entry_id, "config_path": str(config_path), "scheduled_start": scheduled.isoformat() if scheduled else None,
            "launch_at": launch_at.isoformat(), "private_root": str(private / entry_id),
            "requires_supervision": entry_id.endswith("FAIL"),
            "acceptance_check": "server_and_controller_loss" if entry_id.endswith("FAIL") else None,
            "config_sha256": _sha(config_path), "source_config_sha256": _sha(template)})
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
        parent_path = Path(initialized.parent_manifest_path)
        if not parent_path.is_absolute():
            parent_path = root / parent_path
        providers = data["selected_providers"]
        allowance = _allowance(settings, stage, providers)
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
            "allowance": allowance, "copy_retry_seconds": inputs.get("copy_retry_seconds", 30),
            "access": {"timeouts": settings.get("timeouts", {"ssh_seconds": 30, "command_seconds": 120,
                "cloud_init_seconds": 300, "private_path_seconds": 60, "server_seconds": 60, "transfer_seconds": 300}),
                "readiness": {"poll_interval_seconds": 2, "attempt_ceiling": 150},
                "bootstrap": _bootstrap(), "guest_remote_root": "/var/lib/cnb/work",
                "diagnostic_interfaces": {p: settings["providers"][p]["diagnostic_interfaces"] for p in providers}},
            "providers": provider_runtime, "approval_refs": approvals}
        runtime_path = private / entry["entry_id"] / "runtime.json"
        runtime_path.parent.mkdir(exist_ok=True, mode=0o700)
        atomic_write_json(runtime_path, runtime)
        entry.update(schedule_path=str(schedule_path) if schedule_path else None,
            row_id=entry["entry_id"] if scheduled else None, runtime_config_path=str(runtime_path),
            cost_inputs_path=str(cost_path), parent_manifest_path=str(parent_path),
            run_ids={str(child.provider): child.run_id for child in initialized.children})
    stage_data = {"stage_id": stage_id, "stage": stage, "controller_session_id": session,
        "ledger_path": str(ledger_path), "scope_id": ledger["scope_id"], "state_root": str(state_root),
        "results_root": str(result), "repository_root": str(root), "entries": entries,
        "minimum_free_bytes": int(settings.get("minimum_free_bytes", 0)),
        "operator_log_path": str(Path(state_root) / "campaign.log"),
        "backup_destination": settings.get("backup_destination"), "notification_hook": settings.get("notification_hook")}
    atomic_write_json(private / "prepared.json", stage_data)
    return stage_data
