from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from cloud_network_benchmark.config import resolve_campaign

from cloud_network_benchmark.contracts import (
    Placement,
    CampaignOptions,
    ProviderDeploymentInput,
    ProviderDeploymentOutput,
    ProviderVmOutput,
    RemoteConnectionData,
    StructuredEvidence,
    StringEvidence,
    VmIntent,
)


NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


def vm(role: str, function: str) -> VmIntent:
    return VmIntent(role=role, function=function, region="eu-central-1", zone="euc1-az1", vm_shape="m7i.xlarge", image="ubuntu", connection_user="ubuntu")


def test_provider_deployment_input_keeps_provider_semantics() -> None:
    value = ProviderDeploymentInput(
        campaign_id="campaign", run_id="campaign-aws", experiment_id="EXP-001", provider="aws", scenario="same_zone",
        scheduled_start=NOW, vm_a=vm("vm_a", "client"), vm_b=vm("vm_b", "server"),
        placement=Placement(kind="none", name=None), bootstrap_template="cloud-init.yaml", terraform_directory="terraform/aws",
        provider_options={"instance_market_type": "spot"},
        campaign_options=CampaignOptions(labels={"purpose": "test"}, provisioning_timeout_seconds=60, readiness_timeout_seconds=45, cleanup_timeout_seconds=60),
        child_result_path="results/raw/campaign-aws", provisioning_timeout_seconds=60, readiness_timeout_seconds=45, cleanup_timeout_seconds=60,
        config_sha256="a" * 64, implementation_git_commit=StringEvidence(value="b" * 40),
        design_git_commit=StringEvidence(unavailable_reason="design repository unavailable"),
    )
    assert value.provider == "aws"
    assert value.vm_a.role == "vm_a"
    assert value.provider_options == {"instance_market_type": "spot"}
    assert value.campaign_options.labels == {"purpose": "test"}


def test_remote_connection_requires_private_ipv4() -> None:
    connection = RemoteConnectionData(role="vm_a", host="10.0.0.4", user="ubuntu", authentication_reference="runtime:ssh")
    assert connection.port == 22
    with pytest.raises(ValueError):
        RemoteConnectionData(role="vm_a", host="8.8.8.8", user="ubuntu", authentication_reference="runtime:ssh")


def test_partial_provider_output_represents_unavailable_data() -> None:
    missing = StructuredEvidence(unavailable_reason="apply failed")
    output = ProviderDeploymentOutput(
        run_id="campaign-aws", provider="aws", scenario="same_zone", apply_action_id="apply", started_at=NOW, finished_at=NOW,
        vm_a=ProviderVmOutput(role="vm_a", resource_id=None, private_ipv4=None, image_identity=None, actual_shape=None, region=None, zone=None, placement_metadata=missing, connection=None, unavailable=missing),
        vm_b=ProviderVmOutput(role="vm_b", resource_id=None, private_ipv4=None, image_identity=None, actual_shape=None, region=None, zone=None, placement_metadata=missing, connection=None, unavailable=missing),
        provider_metadata=missing, documented_network_limit=missing,
    )
    assert output.vm_a.unavailable.unavailable_reason == "apply failed"


def test_partial_vm_output_requires_unavailable_evidence() -> None:
    missing = StructuredEvidence(unavailable_reason="apply failed")
    with pytest.raises(ValueError, match="requires unavailable evidence"):
        ProviderVmOutput(
            role="vm_a", resource_id=None, private_ipv4=None, image_identity=None,
            actual_shape=None, region=None, zone=None, placement_metadata=missing,
            connection=None, unavailable=None,
        )


def test_deployment_input_factory_preserves_resolved_options(repository_root: Path) -> None:
    resolved, _ = resolve_campaign(repository_root / "configs/tests/exp-900-single-provider.yaml", repository_root)
    value = ProviderDeploymentInput.from_observation(
        resolved.observations[0],
        campaign_id="campaign",
        run_id="campaign-aws",
        bootstrap_template="cloud-init.yaml",
        terraform_directory="terraform/aws",
        child_result_path="results/raw/campaign-aws",
        config_sha256=resolved.config_source.sha256,
        implementation_git_commit=StringEvidence(value="b" * 40),
        design_git_commit=StringEvidence(value="c" * 40),
    )
    assert value.provider_options["instance_market_type"] == "on_demand"
    assert value.campaign_options.labels["purpose"] == "framework-smoke-test"
    assert value.provisioning_timeout_seconds == 300
    assert value.readiness_timeout_seconds == 300
    assert value.cleanup_timeout_seconds == 300


@pytest.mark.parametrize(
    "options",
    [
        CampaignOptions(),
        CampaignOptions(labels={"scope": "smoke"}, cleanup_timeout_seconds=120),
    ],
)
def test_deployment_input_factory_preserves_optional_campaign_options(
    repository_root: Path, options: CampaignOptions
) -> None:
    resolved, _ = resolve_campaign(repository_root / "configs/tests/exp-900-single-provider.yaml", repository_root)
    observation = resolved.observations[0].model_copy(update={"options": options})
    value = ProviderDeploymentInput.from_observation(
        observation,
        campaign_id="campaign",
        run_id="campaign-aws",
        bootstrap_template="cloud-init.yaml",
        terraform_directory="terraform/aws",
        child_result_path="results/raw/campaign-aws",
        config_sha256=resolved.config_source.sha256,
        implementation_git_commit=StringEvidence(value="b" * 40),
        design_git_commit=StringEvidence(value="c" * 40),
    )
    assert value.campaign_options == options
    assert value.provisioning_timeout_seconds == options.provisioning_timeout_seconds
    assert value.readiness_timeout_seconds == options.readiness_timeout_seconds
    assert value.cleanup_timeout_seconds == options.cleanup_timeout_seconds


def test_complete_provider_output_has_scenario_and_no_unavailable_marker() -> None:
    observed = StructuredEvidence(value={"kind": "none"})
    connection_a = RemoteConnectionData(role="vm_a", host="10.0.0.4", user="ubuntu", authentication_reference="runtime:ssh")
    connection_b = RemoteConnectionData(role="vm_b", host="10.0.0.5", user="ubuntu", authentication_reference="runtime:ssh")
    output = ProviderDeploymentOutput(
        run_id="campaign-aws", provider="aws", scenario="same_zone", apply_action_id="apply",
        started_at=NOW, finished_at=NOW,
        vm_a=ProviderVmOutput(role="vm_a", resource_id="i-a", private_ipv4="10.0.0.4", image_identity="ami-1", actual_shape="m7i.xlarge", region="eu-central-1", zone="euc1-az1", placement_metadata=observed, connection=connection_a, unavailable=None),
        vm_b=ProviderVmOutput(role="vm_b", resource_id="i-b", private_ipv4="10.0.0.5", image_identity="ami-1", actual_shape="m7i.xlarge", region="eu-central-1", zone="euc1-az1", placement_metadata=observed, connection=connection_b, unavailable=None),
        provider_metadata=StructuredEvidence(value={"account": "redacted"}),
        documented_network_limit=StructuredEvidence(value={"description": "up to 12.5 Gbps"}),
    )
    assert output.scenario == "same_zone"
    assert output.vm_a.unavailable is None


@pytest.mark.parametrize(
    ("provider", "provider_options"),
    [
        ("aws", {"instance_market_type": "on_demand"}),
        ("aws", {"instance_market_type": "spot"}),
        ("azure", {"priority": "Regular", "eviction_policy": "Delete", "max_price": -1.0}),
        ("azure", {"priority": "Spot", "eviction_policy": "Delete", "max_price": -1.0}),
        ("gcp", {"provisioning_model": "STANDARD", "instance_termination_action": "DELETE"}),
        ("gcp", {"provisioning_model": "SPOT", "instance_termination_action": "DELETE"}),
    ],
)
def test_deployment_input_serializes_provider_native_purchase_options(
    repository_root: Path, tmp_path: Path, provider: str, provider_options: dict[str, object]
) -> None:
    data = yaml.safe_load((repository_root / "configs/tests/exp-903-three-provider-smoke.yaml").read_text())
    data["selected_providers"] = [provider]
    data["provider_configs"] = {provider: data["provider_configs"][provider]}
    data["provider_configs"][provider]["provider_options"] = provider_options
    root = tmp_path / f"{provider}-{provider_options}"
    path = root / "configs/tests/spot.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    resolved, _ = resolve_campaign(path, root)
    value = ProviderDeploymentInput.from_observation(
        resolved.observations[0], campaign_id="campaign", run_id=f"campaign-{provider}",
        bootstrap_template="cloud-init.yaml", terraform_directory=f"terraform/{provider}",
        child_result_path=f"results/raw/campaign-{provider}", config_sha256=resolved.config_source.sha256,
        implementation_git_commit=StringEvidence(value="b" * 40),
        design_git_commit=StringEvidence(value="c" * 40),
    )
    assert value.model_dump(mode="json")["provider_options"] == provider_options
