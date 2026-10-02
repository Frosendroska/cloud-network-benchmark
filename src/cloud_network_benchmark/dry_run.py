from __future__ import annotations

import json
from typing import Any, Dict, List

from pydantic import Field

from .contracts.artifacts import ArtifactLayout
from .contracts.campaign import ResolvedCampaign
from .contracts.common import StrictModel
from .contracts.validation import artifact_inputs


class DryRunChild(StrictModel):
    provider: str
    scenario: str
    candidate_run_id: str
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
    experiment_id: str
    configuration_role: str
    config_sha256: str
    config_source: str
    scheduled_start: str
    scenario: str
    selected_providers: List[str]
    campaign_options: Dict[str, Any]
    candidate_campaign_id: str = "<timestamp>-<experiment-id>-<token>"
    lifecycle: List[str] = Field(default_factory=lambda: ["initialized", "provision_ready", "running", "collecting", "validating", "succeeded"])
    cleanup: List[str] = Field(default_factory=lambda: ["not_started", "attempted", "succeeded"])
    children: List[DryRunChild]


def build_dry_run(resolved: ResolvedCampaign) -> DryRunReport:
    children = []
    layout = ArtifactLayout()
    for observation in resolved.observations:
        provider = str(observation.provider)
        candidate = f"<campaign-id>-{provider}"
        children.append(
            DryRunChild(
                provider=provider,
                scenario=str(observation.scenario),
                candidate_run_id=candidate,
                manifest_path=f"results/manifests/{candidate}.json",
                result_path=f"results/raw/{candidate}/",
                vm_a=observation.vm_a.model_dump(mode="json"),
                vm_b=observation.vm_b.model_dump(mode="json"),
                measurement_direction=getattr(observation.measurement_direction, "value", observation.measurement_direction),
                placement=observation.placement.model_dump(mode="json"),
                benchmark=observation.benchmark.model_dump(mode="json"),
                artifacts=layout.model_dump(),
                validation_inputs={name: value.model_dump(mode="json") for name, value in artifact_inputs(layout, observation.benchmark.multi_flow.enabled).items()},
                provider_options=observation.provider_config.provider_options,
                campaign_options=observation.options.model_dump(mode="json"),
                external_actions=["terraform", "ssh", "scp", "flent", "validation", "cleanup"],
            )
        )
    return DryRunReport(
        experiment_id=resolved.experiment_id,
        configuration_role=resolved.configuration_role,
        config_sha256=resolved.config_source.sha256,
        config_source=resolved.config_source.source_path,
        scheduled_start=resolved.scheduled_start.isoformat(),
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
        f"Config: {report.config_source} (sha256 {report.config_sha256})",
        f"Scheduled start: {report.scheduled_start}",
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
            f"  Provider options: {json.dumps(child.provider_options, sort_keys=True)}",
            f"  Campaign options: {json.dumps(child.campaign_options, sort_keys=True)}",
            f"  Benchmark order: {', '.join(child.benchmark['execution_order'])}",
            "  Benchmark phases: " + "; ".join(
                f"{name} enabled={phase['enabled']} test={phase['test_name']} duration={phase['duration_seconds']}s timeout={phase['timeout_seconds']}s parameters={json.dumps(phase['parameters'], sort_keys=True)}"
                for name, phase in child.benchmark.items() if name != "execution_order"
            ),
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
