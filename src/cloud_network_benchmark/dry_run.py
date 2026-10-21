from __future__ import annotations

import json
from typing import Any, Dict, List

from pydantic import Field

from .contracts.artifacts import ArtifactLayout
from .contracts.campaign import ResolvedCampaign
from .contracts.common import StrictModel
from .contracts.validation import artifact_inputs
from .contracts.common import ValidationStatus


class DryRunChild(StrictModel):
    provider: str
    scenario: str
    candidate_run_id: str
    candidate_observation_id: str
    candidate_deployment_id: str
    manifest_path: str
    result_path: str
    vm_a: Dict[str, Any]
    vm_b: Dict[str, Any]
    measurement_direction: str
    placement: Dict[str, Any]
    benchmark: Dict[str, Any]
    artifacts: Dict[str, Any]
    validation_inputs: Dict[str, Any]
    provider_options: Dict[str, Any]
    campaign_options: Dict[str, Any]
    external_actions: List[str]


class DryRunReport(StrictModel):
    config_id: str
    mode: str
    context: str
    experiment_id: str
    configuration_role: str
    config_sha256: str
    config_source: str
    scheduled_start: str | None
    schedule_evidence: Dict[str, Any]
    admission: Dict[str, Any]
    validation_status: Dict[str, Any]
    measurement_units: List[Dict[str, Any]]
    launch_blockers: List[str]
    scenario: str
    selected_providers: List[str]
    campaign_options: Dict[str, Any]
    candidate_campaign_id: str = "<timestamp>-<experiment-id>-<token>"
    lifecycle: List[str] = Field(default_factory=lambda: ["initialized", "provision_ready", "running", "collecting", "validating", "succeeded"])
    cleanup: List[str] = Field(default_factory=lambda: ["not_started", "attempted", "succeeded"])
    children: List[DryRunChild]


def build_dry_run(resolved: ResolvedCampaign) -> DryRunReport:
    children = []
    for observation in resolved.observations:
        provider = str(observation.provider)
        candidate = f"<campaign-id>-{provider}"
        layout = observation.artifact_layout
        children.append(
            DryRunChild(
                provider=provider,
                scenario=str(observation.scenario),
                candidate_run_id=candidate,
                candidate_observation_id=resolved.replacement_context.observation_id if resolved.replacement_context else f"{candidate}-observation",
                candidate_deployment_id=f"{candidate}-deployment",
                manifest_path=f"results/manifests/{candidate}.json",
                result_path=f"results/raw/{candidate}/",
                vm_a=observation.vm_a.model_dump(mode="json"),
                vm_b=observation.vm_b.model_dump(mode="json"),
                measurement_direction=getattr(observation.measurement_direction, "value", observation.measurement_direction),
                placement=observation.placement.model_dump(mode="json"),
                benchmark=observation.benchmark.model_dump(mode="json"),
                artifacts=layout.model_dump(),
                validation_inputs={name: value.model_dump(mode="json") for name, value in artifact_inputs(
                    layout, mode=resolved.mode,
                    measurement_units=resolved.measurement_units, schedule_supplied=resolved.schedule_entry.value is not None,
                ).items()},
                provider_options=observation.provider_config.provider_options,
                campaign_options=observation.options.model_dump(mode="json"),
                external_actions=["Terraform provisioning", "SSH readiness", "Sockperf latency", "iperf3 throughput", "SCP retrieval", "validation", "provider cleanup"],
            )
        )
    return DryRunReport(
        config_id=resolved.config_id,
        mode=str(resolved.mode),
        context=resolved.context,
        experiment_id=resolved.experiment_id,
        configuration_role=resolved.configuration_role,
        config_sha256=resolved.config_source.sha256,
        config_source=resolved.config_source.source_path,
        scheduled_start=resolved.scheduled_start.isoformat() if resolved.scheduled_start else None,
        schedule_evidence=(
            {"state":"observed", "entry_id":resolved.schedule_entry.value.entry_id,
             "row_sha256":resolved.schedule_entry.value.row_sha256,
             "source_path":resolved.schedule_entry.value.source_path,
             "source_sha256":resolved.schedule_entry.value.source_sha256}
            if resolved.schedule_entry.value is not None else {"state":"pending", "row_sha256":None}
        ),
        admission=resolved.admission.model_dump(mode="json"),
        validation_status=ValidationStatus().model_dump(mode="json"),
        measurement_units=[item.model_dump(mode="json") for item in resolved.measurement_units],
        launch_blockers=resolved.launch_blockers,
        scenario=str(resolved.scenario),
        selected_providers=[str(provider) for provider in resolved.selected_providers],
        campaign_options=resolved.options.model_dump(mode="json"),
        children=children,
    )


def render_json(report: DryRunReport) -> str:
    return json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True)


def render_human(report: DryRunReport) -> str:
    lines = [
        f"Campaign {report.experiment_id} ({report.configuration_role})",
        f"Config ID: {report.config_id}",
        f"Mode/context: {report.mode} / {report.context}",
        f"Config: {report.config_source} (sha256 {report.config_sha256})",
        f"Scheduled start: {report.scheduled_start or 'pending'}",
        f"Schedule row: {report.schedule_evidence.get('state')}; SHA-256 {report.schedule_evidence.get('row_sha256') or 'pending'}",
        f"Admission: priority {report.admission['priority']}; lifetime cap {report.admission['lifetime_cap_seconds']}s; reservation pending",
        f"Validation status: {report.validation_status['data_validity']} / {report.validation_status['comparison_eligibility']}",
        f"Measurement units: {len(report.measurement_units)}; blockers: {', '.join(report.launch_blockers) or 'none'}",
        f"Scenario: {report.scenario}",
        f"Providers: {', '.join(report.selected_providers)}",
        f"Candidate campaign ID: {report.candidate_campaign_id}",
        f"Lifecycle: {' -> '.join(report.lifecycle)}",
        f"Cleanup: {' -> '.join(report.cleanup)}",
    ]
    for child in report.children:
        lines.extend([
            "",
            f"[{child.provider}] {child.candidate_run_id}",
            f"  VM A client: {child.vm_a['region']} / {child.vm_a['zone']} / {child.vm_a['vm_shape']}",
            f"  VM B server: {child.vm_b['region']} / {child.vm_b['zone']} / {child.vm_b['vm_shape']}",
            f"  Images: VM A {child.vm_a['image']}; VM B {child.vm_b['image']}",
            f"  Direction: {child.measurement_direction}",
            f"  Placement: {child.placement['kind']}",
            "  Benchmark order: " + ", ".join(dict.fromkeys(unit["phase"] for unit in report.measurement_units)),
            f"  Provider options: {json.dumps(child.provider_options, sort_keys=True)}",
            f"  Campaign options: {json.dumps(child.campaign_options, sort_keys=True)}",
            "  Benchmark units: " + ", ".join(
                f"{unit['unit_id']} ({unit['phase']}, {unit['measured_seconds']}s"
                f"{', ' + str(unit['streams']) + ' streams' if unit['streams'] is not None else ''})"
                for unit in report.measurement_units
            ),
            f"  Benchmark settings: {json.dumps(child.benchmark, sort_keys=True)}",
            f"  Manifest path: {child.manifest_path}",
            f"  Result path: {child.result_path}",
            f"  Artifacts: {', '.join(child.artifacts.values())}",
            "  Validator expectations: " + ", ".join(
                f"{name}={value['expectation']}" for name, value in child.validation_inputs.items()
            ),
            f"  Planned actions: {', '.join(child.external_actions)}",
        ])
    lines.append("\nNo IDs or paths are reserved; no external action is executed.")
    return "\n".join(lines)
