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


def _read_review_decision(path: Path) -> dict[str, Any]:
    """Read the provider review and verify its exact referenced source bytes."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("source_path"), str):
        raise ValueError("primary review must identify its source path and digest")
    declared = Path(value["source_path"])
    source = declared if declared.is_absolute() else path.parent / declared
    source = source.resolve(strict=True)
    if source.is_symlink() or not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != value.get("source_sha256"):
        raise ValueError("primary review source hash does not match its bytes")
    return value


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
                            costs: Any, runtime: dict[str, Any],
                            ledger_state: dict[str, Any] | None = None,
                            controller_session_id: str | None = None):
    """Build F11 inputs from the initialized identities and exact sourced cost rows."""
    from decimal import Decimal
    from .admission import AdmissionPlan, WorkflowAllowance
    from .contracts.admission import ReservationRequest
    from .contracts.common import Evidence, Provider
    from .contracts.deployment import ProviderDeploymentInput

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
    outstanding_cleanup = False
    if ledger_state is not None:
        reservations = ledger_state.get("reservations")
        cleanup_blocked = ledger_state.get("cleanup_blocked")
        if not isinstance(reservations, dict) or not isinstance(cleanup_blocked, list):
            raise ValueError("ledger cleanup state is malformed")
        outstanding_cleanup = bool(cleanup_blocked) or any(
            isinstance(record, dict) and record.get("cleanup_open")
            and record.get("session") != controller_session_id
            for record in reservations.values())
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
        outstanding_cleanup=outstanding_cleanup)
    namespaces = {}
    for provider, child in children.items():
        if provider == "aws":
            namespaces[provider] = f"cnb-{child.run_id}-{child.deployment_id}"
        elif provider == "gcp":
            namespaces[provider] = child.run_id
        else:
            from .azure_provider import _slug
            observation = providers[provider]
            deployment = ProviderDeploymentInput.from_observation(
                    observation, campaign_id=parent.campaign_id, run_id=child.run_id,
                    observation_id=child.observation_id, deployment_id=child.deployment_id,
                    bootstrap_template=str(runtime["providers"][provider]["bootstrap_template"]),
                    terraform_directory=str(runtime["providers"][provider]["terraform_directory"]),
                    child_result_path=str(next(ref.result_path for ref in parent.child_runs
                        if str(ref.provider) == provider)),
                    config_sha256=child.config_provenance.sha256,
                    resolved_sha256=child.config_provenance.resolved_snapshot_sha256,
                    implementation_git_commit=child.config_provenance.implementation_git_commit,
                    design_git_commit=child.config_provenance.design_git_commit)
            namespaces[provider] = _slug(deployment)
    prerequisites = runtime.get("approval_evidence", {})
    # Keep the validated cost payload immutable while deriving runtime support
    # evidence. Mutating this nested mapping makes its in-memory value diverge
    # from the source bytes F11 re-reads before reserve.
    support = dict(costs.payload.get("provider_support", {}))
    if "aws" in child_ids:
        from .admission import aws_expiry_cost_handoff
        aws_source = runtime["providers"]["aws"]["sources"]["expiry_cost_path"]
        if aws_source is None:
            raise ValueError("AWS expiry cost source is required for provider protection")
        observation = providers["aws"]
        regions = sorted({observation.vm_a.region, observation.vm_b.region})
        support = {**support, "aws": aws_expiry_cost_handoff(
            costs, aws_source, regions, reservation_sha256=costs.payload_sha256)}
    for provider in child_ids:
        if provider == "aws":
            continue
        envelope = envelopes[provider]
        included = envelope["components"]["expiry_cleanup_support"]["amount_usd"]
        support[provider] = {"source_path": costs.source_path,
            "source_sha256": costs.payload_sha256, "included_usd": included}
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


def build_aws_provider_workflows(*, campaign: Any, parent: Any, children: dict[str, Any],
        costs: Any, runtime: dict[str, Any], reservation: Any, ledger: Any,
        runner: Any, clock: Any, stages: Any, private_root: Path) -> dict[str, Any]:
    """Bind one accepted AWS reservation to the existing driver and lifecycle hooks.

    This is intentionally post-reservation: provider settings, deployment
    identity, expiry trigger, private attempt root, and creation claim all bind
    to the ReservationEvidence returned by F11.
    """
    from datetime import timedelta
    from math import ceil
    from .admission import aws_expiry_cost_handoff
    from .contracts.admission import ExpiryRequest
    from .contracts.common import Evidence, Provider, StructuredEvidence, StringEvidence
    from .contracts.deployment import ProviderDeploymentInput
    from .lifecycle_adapters import aws_hooks
    from .orchestration import _remaining_f11_allowance
    from .providers import aws as aws_provider
    from .providers.aws import AwsProviderDriver, AwsRuntimeSettings

    if set(children) != {"aws"} or {str(value) for value in campaign.selected_providers} != {"aws"}:
        raise ValueError("the AWS acceptance factory requires exactly the initialized AWS child")
    child = children["aws"]
    if child.mode != "acceptance" or child.execution_state != "initialized":
        raise ValueError("the AWS acceptance factory requires an unlaunched acceptance child")
    observation = next((item for item in campaign.observations
        if str(getattr(item.provider, "value", item.provider)) == "aws"), None)
    if observation is None:
        raise ValueError("resolved AWS observation is unavailable")
    if (reservation.state != "reserved" or reservation.source_path != costs.source_path
            or reservation.source_sha256 != costs.payload_sha256):
        raise ValueError("accepted AWS reservation differs from refreshed source bytes")
    record = ledger.inspect().get("reservations", {}).get(reservation.reservation_id.value)
    if not isinstance(record, dict):
        raise ValueError("accepted AWS reservation is unavailable in the ledger")
    config = runtime["providers"]["aws"]
    options, sources = config["options"], config["sources"]
    child_root = Path(parent.child_runs[[str(ref.provider) for ref in parent.child_runs].index("aws")].result_path)
    if not child_root.is_absolute():
        child_root = Path.cwd() / child_root
    provenance = child.config_provenance
    deployment = ProviderDeploymentInput.from_observation(observation,
        campaign_id=parent.campaign_id, run_id=child.run_id,
        observation_id=child.observation_id, deployment_id=child.deployment_id,
        bootstrap_template=str(config["bootstrap_template"]),
        terraform_directory=str(config["terraform_directory"]), child_result_path=str(child_root),
        config_sha256=provenance.sha256, resolved_sha256=provenance.resolved_snapshot_sha256,
        implementation_git_commit=provenance.implementation_git_commit,
        design_git_commit=provenance.design_git_commit,
        reservation=Evidence(value=reservation))
    # The resolved campaign retains the configured source digest. AWS runtime
    # validation binds this one handoff to the accepted payload hash domain.
    deployment_data = deployment.model_dump(mode="python")
    admission_data = dict(deployment_data["admission"])
    admission_data["cost_input_sha256"] = StringEvidence(value=reservation.source_sha256)
    deployment_data["admission"] = admission_data
    deployment = ProviderDeploymentInput.model_validate(deployment_data)
    regions = list(dict.fromkeys((observation.vm_a.region, observation.vm_b.region)))
    support = aws_expiry_cost_handoff(costs, sources["expiry_cost_path"], sorted(set(regions)),
        reservation_sha256=reservation.source_sha256)
    remaining = _remaining_f11_allowance(runtime["allowance"], "registration")
    public_key = options["public_key_path"].read_text(encoding="utf-8").strip()
    settings_values = {"account_id": options["account_id"],
        "controller_cidr": options["controller_cidr"], "ami_ids": options["ami_ids"],
        "public_key": public_key, "authentication_reference": options["authentication_reference"],
        "private_state_root": (Path(private_root) / "aws").absolute(),
        "cost_input_id": costs.cost_input_id, "cost_input_sha256": reservation.source_sha256,
        "cost_source_path": reservation.source_path,
        "complete_workflow_cost_handoff": StructuredEvidence(value=support),
        "complete_workflow_remaining_seconds": ceil(remaining.total_seconds)}
    if observation.admission.requires_primary_review:
        review = sources.get("primary_review_path")
        if review is None:
            raise ValueError("AWS scenario requires its source-bound primary review")
        decision = json.loads(review.read_text(encoding="utf-8"))
        declared_path, declared_hash = decision.get("source_path"), decision.get("source_sha256")
        if not isinstance(declared_path, str) or not isinstance(declared_hash, str):
            raise ValueError("AWS primary review must identify its source bytes")
        review_source = (review.parent / declared_path).resolve(strict=True)
        if (review_source.is_symlink() or not review_source.is_file()
                or hashlib.sha256(review_source.read_bytes()).hexdigest() != declared_hash):
            raise ValueError("AWS primary review source hash does not match its bytes")
        settings_values["primary_review_decision"] = StructuredEvidence(value=decision)
    settings = AwsRuntimeSettings(**settings_values)
    driver = AwsProviderDriver(runner, settings, clock=clock.utc_now)
    trigger = reservation.expires_at.value - timedelta(seconds=aws_provider.SAFETY_LEAD_SECONDS)
    request = ExpiryRequest(schema_version=2, run_id=child.run_id,
        deployment_id=child.deployment_id, provider=Provider.AWS, action="stop",
        expires_at=Evidence(value=trigger), reservation_id=reservation.reservation_id,
        resource_namespace=aws_provider._namespace(deployment),
        target_selector=driver._target_selector(deployment, driver._regions(deployment)))

    def claim(bound_deployment: Any, _claim_data: Any):
        expiry = bound_deployment.expiry.value
        return ledger.claim_creation(reservation.reservation_id.value, provider=Provider.AWS,
            expiry_requests=[request], expiry_evidence=[expiry])

    from .lifecycle_adapters import RemoteStages
    bound_stages = RemoteStages(prepare=stages.prepare, stabilize=stages.stabilize,
        measure=stages.measure, close=stages.close, retrieve=stages.retrieve, claim=claim)
    return {"aws": aws_hooks(driver, deployment, request, stages=bound_stages,
        reservation=reservation, claim_data=None)}


def build_runtime(*, campaign: Any, parent: Any, children: dict[str, Any],
        costs: Any, runtime: dict[str, Any], controller_session_id: str,
        runner: Any, clock: Any, private_root: Path, stages: Any = None,
        repository_root: Path | None = None):
    """Build the selected-provider runtime around one source-bound F11 ledger.

    The returned workflow factory is intentionally lazy. ``run_lifecycle``
    calls it with the ReservationEvidence returned by ``BudgetLedger.reserve``.
    """
    from .admission import BudgetLedger
    from .orchestration import SavedLifecycleRecovery, SavedProviderRecovery

    selected = {str(getattr(p, "value", p)) for p in campaign.selected_providers}
    if set(children) != selected or set(runtime["providers"]) != selected:
        raise ValueError("the production runtime requires exactly the initialized selected-provider set")
    if any(child.execution_state != "initialized" or child.mode != campaign.mode
            for child in children.values()):
        raise ValueError("the production runtime requires matching unlaunched initialized children")
    ledger = BudgetLedger(runtime["ledger"]["path"], scope_id=runtime["ledger"]["scope_id"],
        utc_now=clock.utc_now, monotonic_now=clock.monotonic,
        controller_session_id=controller_session_id)
    if not ledger.path.exists():
        if runtime["ledger"]["initialize_if_missing"] is not True:
            raise ValueError("ledger is missing and runtime configuration forbids initialization")
        ledger.initialize(costs)
    request, plan = build_admission_request(campaign=campaign, parent=parent, children=children,
        costs=costs, runtime=runtime, ledger_state=ledger.inspect(),
        controller_session_id=controller_session_id)

    cleanup_references: dict[str, str] = {}
    def workflow_factory(reservation: Any):
        actual_stages = stages
        if actual_stages is None:
            from .lifecycle_remote import build_provider_remote_stages
            actual_stages = {}
            for provider, child in children.items():
                actual_stages[provider] = build_provider_remote_stages(provider=provider,
                    campaign=campaign, parent=parent, children={provider: child}, runtime=runtime,
                    ledger=ledger, reservation=reservation, runner=runner, clock=clock,
                    repository_root=repository_root or Path.cwd(),
                    recovery_path=Path(private_root) / "lifecycle-recovery.json",
                    sleep=runtime.get("sleep", __import__("time").sleep))
        return build_provider_workflows(campaign=campaign, parent=parent,
            children=children, costs=costs, runtime=runtime, reservation=reservation,
            ledger=ledger, runner=runner, clock=clock, stages=actual_stages,
            private_root=private_root, cleanup_references=cleanup_references)

    def recovery_factory(reservation: Any, _workflows: Any):
        providers = []
        for provider, child in children.items():
            if provider == "aws":
                private_provider_root = (Path(private_root) / "aws").absolute()
                namespace = f"cnb-{child.run_id}-{child.deployment_id}"
                journal = private_provider_root / namespace / child.run_id / child.deployment_id / "attempt.json"
            elif provider == "gcp":
                prefix = hashlib.sha256(child.run_id.encode()).hexdigest()[:12]
                journal = Path(private_root) / "gcp" / prefix / child.run_id / child.deployment_id / "attempt.json"
            else:
                journal = Path(private_root) / "azure" / child.run_id / child.deployment_id / "recovery.json"
            providers.append(SavedProviderRecovery(provider=provider, deployment_id=child.deployment_id,
                journal_reference=str(journal), expiry_registered=False,
                creation_may_have_started=False, cleanup_open=False,
                cleanup_settings_reference=cleanup_references.get(provider)))
        return SavedLifecycleRecovery(schema_version=1, attempt_id=parent.campaign_id,
            controller_session_id=controller_session_id,
            reservation_id=reservation.reservation_id.value,
            ledger_path=str(ledger.path), ledger_scope_id=ledger.scope_id,
            providers=providers, retry_consumed_ids=[])

    return {"ledger": ledger, "request": request, "plan": plan,
        "allowance": runtime["allowance"], "workflow_factory": workflow_factory,
        "recovery_factory": recovery_factory,
        "recovery_path": Path(private_root) / "lifecycle-recovery.json"}


def build_provider_workflows(*, campaign: Any, parent: Any, children: dict[str, Any],
        costs: Any, runtime: dict[str, Any], reservation: Any, ledger: Any,
        runner: Any, clock: Any, stages: dict[str, Any], private_root: Path,
        cleanup_references: dict[str, str] | None = None) -> dict[str, Any]:
    """Bind every selected provider to one accepted F11 reservation and claim barrier."""
    from datetime import timedelta
    from math import ceil
    from .contracts.admission import ExpiryRequest
    from .contracts.common import Evidence, Provider, StringEvidence, StructuredEvidence
    from .contracts.deployment import ProviderDeploymentInput
    from .lifecycle_adapters import RemoteStages, aws_hooks, azure_hooks, gcp_hooks
    from .orchestration import _remaining_f11_allowance
    from .providers import aws as aws_provider
    from .providers.aws import AwsProviderDriver, AwsRuntimeSettings
    from .providers.gcp import GcpProviderDriver, GcpRuntimeSettings
    from .admission import aws_expiry_cost_handoff
    from .lifecycle_cleanup import save_cleanup_settings
    from . import azure_provider

    selected = {str(getattr(p, "value", p)) for p in campaign.selected_providers}
    if set(children) != selected or set(stages) != selected:
        raise ValueError("runtime workflows, stages and initialized provider set must match")
    if (reservation.state != "reserved" or reservation.source_path != costs.source_path
            or reservation.source_sha256 != costs.payload_sha256):
        raise ValueError("accepted reservation differs from exact refreshed cost bytes")
    record = ledger.inspect().get("reservations", {}).get(reservation.reservation_id.value)
    if not isinstance(record, dict) or record.get("session") != ledger.controller_session_id:
        raise ValueError("accepted reservation or controller session is unavailable")
    refs = {str(ref.provider): ref for ref in parent.child_runs}
    observations = {str(getattr(item.provider, "value", item.provider)): item
        for item in campaign.observations}
    deployments = {}
    requests = {}
    options_by_provider = {}
    for provider, child in children.items():
        if child.mode != campaign.mode or child.execution_state != "initialized":
            raise ValueError("default provider factory requires matching unlaunched initialized children")
        row = runtime["providers"][provider]
        observation = observations[provider]
        child_root = Path(refs[provider].result_path)
        if not child_root.is_absolute():
            child_root = Path.cwd() / child_root
        deployment = ProviderDeploymentInput.from_observation(observation,
            campaign_id=parent.campaign_id, run_id=child.run_id,
            observation_id=child.observation_id, deployment_id=child.deployment_id,
            bootstrap_template=str(row["bootstrap_template"]),
            terraform_directory=str(row["terraform_directory"]),
            child_result_path=str(child_root),
            config_sha256=child.config_provenance.sha256,
            resolved_sha256=child.config_provenance.resolved_snapshot_sha256,
            implementation_git_commit=child.config_provenance.implementation_git_commit,
            design_git_commit=child.config_provenance.design_git_commit,
            reservation=Evidence(value=reservation))
        if provider in {"aws", "gcp"}:
            data = deployment.model_dump(mode="python")
            admission = dict(data["admission"])
            admission["cost_input_sha256"] = StringEvidence(value=reservation.source_sha256)
            data["admission"] = admission
            deployment = ProviderDeploymentInput.model_validate(data)
        deployments[provider] = deployment
        config = row["options"]
        if provider == "aws":
            namespace = aws_provider._namespace(deployment)
            trigger = reservation.expires_at.value - timedelta(seconds=aws_provider.SAFETY_LEAD_SECONDS)
            expiry_fixture = row["sources"]["expiry_cost_path"]
            support = aws_expiry_cost_handoff(costs, expiry_fixture,
                sorted(set((observation.vm_a.region, observation.vm_b.region))),
                reservation_sha256=reservation.source_sha256)
            settings_args = {"account_id": config["account_id"], "controller_cidr": config["controller_cidr"],
                "ami_ids": config["ami_ids"], "public_key": config["public_key_path"].read_text().strip(),
                "authentication_reference": config["authentication_reference"],
                "private_state_root": (Path(private_root) / "aws").absolute(),
                "cost_input_id": costs.cost_input_id, "cost_input_sha256": reservation.source_sha256,
                "cost_source_path": reservation.source_path,
                "complete_workflow_cost_handoff": StructuredEvidence(value=support),
                "complete_workflow_remaining_seconds": ceil(_remaining_f11_allowance(
                    runtime["allowance"], "registration").total_seconds)}
            if observation.admission.requires_primary_review:
                review = row["sources"].get("primary_review_path")
                if review is None: raise ValueError("AWS scenario requires source-bound primary review")
                settings_args["primary_review_decision"] = StructuredEvidence(value=_read_review_decision(review))
            settings = AwsRuntimeSettings(**settings_args)
            driver = AwsProviderDriver(runner, settings, clock=clock.utc_now)
            requests[provider] = ExpiryRequest(schema_version=2, run_id=child.run_id,
                deployment_id=child.deployment_id, provider=Provider.AWS, action="stop",
                expires_at=Evidence(value=trigger), reservation_id=reservation.reservation_id,
                resource_namespace=namespace,
                target_selector=driver._target_selector(deployment, driver._regions(deployment)))
            if cleanup_references is not None:
                cleanup_references[provider] = str(save_cleanup_settings(provider=provider,
                    deployment=deployment, request=requests[provider], reservation=reservation,
                    provider_lead_seconds=(reservation.expires_at.value - requests[provider].expires_at.value).total_seconds(),
                    settings=settings, private_root=Path(private_root)))
            options_by_provider[provider] = (driver, None)
        elif provider == "gcp":
            from .providers.gcp import SAFETY_LEAD_SECONDS
            sources = row["sources"]
            handoff = json.loads(sources["workflow_cost_handoff_path"].read_text())
            support_evidence = json.loads(sources["support_cost_path"].read_text())
            settings_args = {"project_id": config["project_id"], "controller_cidr": config["controller_cidr"],
                "images": config["images"], "public_key": config["public_key_path"].read_text().strip(),
                "authentication_reference": config["authentication_reference"],
                "private_state_root": (Path(private_root) / "gcp").absolute(),
                "cost_input_id": costs.cost_input_id, "cost_input_sha256": reservation.source_sha256,
                "cost_source_path": reservation.source_path,
                "complete_workflow_cost_handoff": StructuredEvidence(value=handoff),
                "support_cost_evidence": StructuredEvidence(value=support_evidence),
                "complete_workflow_remaining_seconds": float(runtime["allowance"].total_seconds)}
            if observation.admission.requires_primary_review:
                review = sources.get("primary_review_path")
                if review is None: raise ValueError("GCP scenario requires source-bound primary review")
                settings_args["primary_review_decision"] = StructuredEvidence(value=_read_review_decision(review))
            settings = GcpRuntimeSettings(**settings_args)
            driver = GcpProviderDriver(runner, settings, clock=clock.utc_now)
            trigger = reservation.expires_at.value - timedelta(seconds=SAFETY_LEAD_SECONDS)
            requests[provider] = ExpiryRequest(schema_version=2, run_id=child.run_id,
                deployment_id=child.deployment_id, provider=Provider.GCP, action="stop",
                expires_at=Evidence(value=trigger), reservation_id=reservation.reservation_id,
                resource_namespace=child.run_id,
                target_selector=f"project={settings.project_id};run_id={child.run_id};deployment_id={child.deployment_id};roles=vm_a,vm_b")
            if cleanup_references is not None:
                cleanup_references[provider] = str(save_cleanup_settings(provider=provider,
                    deployment=deployment, request=requests[provider], reservation=reservation,
                    provider_lead_seconds=(reservation.expires_at.value - requests[provider].expires_at.value).total_seconds(),
                    settings=settings, private_root=Path(private_root)))
            options_by_provider[provider] = (driver, None)
        else:
            lead = float(runtime["allowance"].provider_leads_seconds[provider])
            trigger = reservation.expires_at.value - timedelta(seconds=lead)
            cap_end = float(record["cap_start_monotonic"]) + float(record["cap_seconds"])
            attempt_root = (Path(private_root) / "azure" / child.run_id / child.deployment_id).absolute()
            price = json.loads(row["sources"]["expiry_cost_path"].read_text())
            admission_evidence = {"reservation_id": reservation.reservation_id.value,
                "source_sha256": reservation.source_sha256, "priority": deployment.admission.priority,
                "outstanding_cleanup": False}
            if observation.admission.requires_primary_review:
                review = row["sources"].get("primary_review_path")
                if review is None: raise ValueError("Azure scenario requires source-bound primary review")
                _read_review_decision(review)
                admission_evidence["priority_review_reference"] = str(review)
            options = azure_provider.AzureRuntimeOptions(subscription_id=config["subscription_id"],
                tenant_id=config["tenant_id"], controller_cidr=config["controller_cidr"],
                attempt_root=attempt_root, authentication_reference=config["authentication_reference"],
                ssh_public_key=config["ssh_public_key_path"].read_text().strip(),
                selected_image=config["selected_image"],
                complete_workflow_seconds=float(runtime["allowance"].total_seconds),
                post_registration_workflow_seconds=float(_remaining_f11_allowance(
                    runtime["allowance"], "preparation").total_seconds),
                cap_deadline_monotonic=cap_end, admission_evidence=admission_evidence,
                retry_end_at=reservation.expires_at.value, poll_seconds=config["poll_seconds"],
                command_timeout_seconds=config["command_timeout_seconds"], expiry_cost_input=price)
            name, ids = azure_provider._validate_request(deployment, options, clock.utc_now(), clock.monotonic())
            requests[provider] = ExpiryRequest(schema_version=2, run_id=child.run_id,
                deployment_id=child.deployment_id, provider=Provider.AZURE, action="deallocate",
                expires_at=Evidence(value=trigger), reservation_id=reservation.reservation_id,
                resource_namespace=name, target_selector=";".join(ids))
            if cleanup_references is not None:
                cleanup_references[provider] = str(save_cleanup_settings(provider=provider,
                    deployment=deployment, request=requests[provider], reservation=reservation,
                    provider_lead_seconds=lead, settings=options, private_root=Path(private_root)))
            options_by_provider[provider] = (options, None)

    protected: dict[str, Any] = {}
    def on_protected(provider: str, evidence: Any) -> None:
        if provider not in requests or provider in protected:
            raise ValueError("provider protection evidence is duplicate or outside the selected set")
        protected[provider] = evidence
    def claim(deployment: Any, _data: Any):
        provider = str(getattr(deployment.provider, "value", deployment.provider))
        if set(protected) != selected:
            return ledger.claim_creation(reservation.reservation_id.value, provider=Provider(provider),
                expiry_requests=[], expiry_evidence=[])
        return ledger.claim_creation(reservation.reservation_id.value, provider=Provider(provider),
            expiry_requests=[requests[key] for key in sorted(requests)],
            expiry_evidence=[protected[key] for key in sorted(protected)])

    workflows = {}
    for provider in children:
        source_stage = stages[provider]
        stage = RemoteStages(prepare=source_stage.prepare, stabilize=source_stage.stabilize,
            measure=source_stage.measure, close=source_stage.close,
            retrieve=source_stage.retrieve, claim=claim)
        deployment, request = deployments[provider], requests[provider]
        driver_or_options, _ = options_by_provider[provider]
        if provider == "aws":
            workflows[provider] = aws_hooks(driver_or_options, deployment, request,
                stages=stage, reservation=reservation, on_protected=on_protected)
        elif provider == "gcp":
            workflows[provider] = gcp_hooks(driver_or_options, deployment, request,
                stages=stage, reservation=reservation, on_protected=on_protected)
        else:
            workflows[provider] = azure_hooks(deployment, request,
                options=driver_or_options, runner=runner, stages=stage, reservation=reservation,
                clock=clock.monotonic, utc_now=clock.utc_now, on_protected=on_protected)
    return workflows
