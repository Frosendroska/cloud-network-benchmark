from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.errors import ValidationError

from cloud_network_benchmark.contracts import (
    IdentityContext,
    Placement,
    CampaignOptions,
    DiskIntent,
    NetworkIntent,
    SecurityIntent,
    AccelerationIntent,
    Evidence,
    ProviderDeploymentInput,
    ProviderDeploymentOutput,
    ProviderVmOutput,
    RemoteConnectionData,
    StructuredEvidence,
    StringEvidence,
    VmIntent,
    ExpiryEvidence,
    ReservationEvidence,
)


NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


def vm(role: str, function: str) -> VmIntent:
    return VmIntent(role=role, function=function, region="eu-central-1", zone="euc1-az1", vm_shape="m7i.xlarge", image="ubuntu", connection_user="ubuntu", disk=DiskIntent(size_gib=32, kind="gp3"), network=NetworkIntent(address_spaces={"eu-central-1":"10.60.0.0/16"}, subnets={"eu-central-1":["10.60.1.0/24"]}), security=SecurityIntent(controller_cidr=Evidence[str](unavailable_reason="not observed")), acceleration=AccelerationIntent(kind="ena"))


def test_provider_deployment_input_keeps_provider_semantics(repository_root: Path) -> None:
    resolved, _ = resolve_campaign(repository_root / "configs/tests/exp-900-single-provider.yaml", repository_root)
    value = ProviderDeploymentInput(schema_version=2,
        config_id=resolved.config_id, observation_id="campaign-aws-observation", deployment_id="campaign-aws-deployment",
        mode="acceptance", context="failure_check", admission=resolved.admission,
        campaign_id="campaign", run_id="campaign-aws", experiment_id="EXP-001", provider="aws", scenario="same_zone",
        scheduled_start=NOW, vm_a=vm("vm_a", "client_traffic_generator"), vm_b=vm("vm_b", "server_receiver"),
        placement=Placement(kind="none", name=None), bootstrap_template="cloud-init.yaml", terraform_directory="terraform/aws",
        provider_options={"instance_market_type": "on_demand"},
        campaign_options=CampaignOptions(labels={"purpose": "test"}, provisioning_timeout_seconds=60, readiness_timeout_seconds=45, cleanup_timeout_seconds=60),
        child_result_path="results/raw/campaign-aws", provisioning_timeout_seconds=60, readiness_timeout_seconds=45, cleanup_timeout_seconds=60,
        config_sha256="a" * 64, implementation_git_commit=StringEvidence(value="b" * 40),
        design_git_commit=StringEvidence(unavailable_reason="design repository unavailable"),
    )
    assert value.provider == "aws"
    assert value.vm_a.role == "vm_a"
    assert value.provider_options == {"instance_market_type": "on_demand"}
    assert value.campaign_options.labels == {"purpose": "test"}


def test_remote_connection_accepts_public_management_ipv4_and_rejects_invalid_hosts() -> None:
    connection = RemoteConnectionData(role="vm_a", host="10.0.0.4", user="ubuntu", authentication_reference="runtime:ssh", host_key_reference="runtime:known-hosts")
    assert connection.port == 22
    assert RemoteConnectionData(role="vm_a", host="8.8.8.8", user="ubuntu", authentication_reference="runtime:ssh", host_key_reference="runtime:known-hosts")
    for host in ("0.0.0.0", "224.0.0.1"):
        with pytest.raises(ValueError):
            RemoteConnectionData(role="vm_a", host=host, user="ubuntu", authentication_reference="runtime:ssh", host_key_reference="runtime:known-hosts")
    with pytest.raises(ValueError):
        RemoteConnectionData(role="vm_a", host="8.8.8.8", user="ubuntu", authentication_reference="  ", host_key_reference="known")
    with pytest.raises(ValueError):
        RemoteConnectionData(role="vm_a", host="8.8.8.8", user="ubuntu", authentication_reference="ssh", host_key_reference="  ")


def test_partial_provider_output_represents_unavailable_data() -> None:
    missing = StructuredEvidence(unavailable_reason="apply failed")
    output = ProviderDeploymentOutput(schema_version=2,
        identity=IdentityContext(experiment_id="EXP-001",config_id="exp_001",campaign_id="campaign",observation_id="campaign-aws-observation",run_id="campaign-aws",deployment_id="campaign-aws-deployment",provider="aws"),
        run_id="campaign-aws", deployment_id="campaign-aws-deployment", mode="acceptance", provider="aws", scenario="same_zone", apply_action_id="apply", started_at=NOW, finished_at=NOW,
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


def test_vm_output_keeps_public_management_and_private_benchmark_ips_distinct() -> None:
    observed = StructuredEvidence(value={"kind": "observed"})
    connection = RemoteConnectionData(role="vm_a", host="8.8.8.8", user="ubuntu",
                                      authentication_reference="auth-ref", host_key_reference="hostkey-ref")
    value = ProviderVmOutput(role="vm_a", resource_id="vm-a", private_ipv4="10.0.0.4",
                             public_ipv4={"value":"8.8.8.8"}, physical_zone_id={"value":"zone-id"},
                             image_identity="image", actual_shape="m7i.xlarge", region="eu-central-1",
                             zone="euc1-az1", placement_metadata=observed, connection=connection, unavailable=None)
    assert value.private_ipv4 == "10.0.0.4" and value.connection.host == "8.8.8.8"
    for address in ("0.0.0.0", "224.0.0.1", "127.0.0.1"):
        with pytest.raises(ValueError, match="public_ipv4"):
            ProviderVmOutput(role="vm_a", resource_id=None, private_ipv4=None, public_ipv4={"value":address},
                             image_identity=None, actual_shape=None, region=None, zone=None,
                             placement_metadata={"unavailable_reason":"partial"}, connection=None,
                             unavailable={"unavailable_reason":"partial"})


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
    assert value.observation_id == "campaign-aws-observation"
    assert value.deployment_id == "campaign-aws-deployment"
    assert not hasattr(value, "identity")


def test_deployment_input_factory_preserves_replacement_and_safety_evidence(repository_root: Path) -> None:
    from cloud_network_benchmark.contracts.admission import ExpiryEvidence, ReservationEvidence
    from cloud_network_benchmark.contracts.campaign import ReplacementContext
    resolved, _ = resolve_campaign(repository_root / "configs/experiments/exp-001-multi-provider.yaml", repository_root)
    replacement = ReplacementContext(observation_id="stable-observation", replaces_run_id="prior-aws")
    observation = resolved.observations[0].model_copy(update={"context":"replacement", "replacement_context":replacement})
    reservation = ReservationEvidence(
        schema_version=2, campaign_id="campaign", reservation_id={"unavailable_reason":"rejected"},
        state="rejected", reserved_cost_usd={"unavailable_reason":"rejected"},
        cap_started_at={"unavailable_reason":"not reserved"}, expires_at={"unavailable_reason":"not reserved"},
        cap_start_policy_id={"unavailable_reason":"not reserved"}, source_path="prices.csv", source_sha256="a"*64,
        reasons=["budget admission is not approved"],
    )
    expiry = ExpiryEvidence(
        schema_version=2, run_id="campaign-aws", deployment_id="campaign-aws-deployment", provider="aws", action="stop",
        state="not_registered", registration_id={"unavailable_reason":"not registered"},
        registered_at={"unavailable_reason":"not registered"}, expires_at={"unavailable_reason":"not registered"},
        target_selector="run-tag", provider_metadata={"unavailable_reason":"not registered"},
    )
    value = ProviderDeploymentInput.from_observation(
        observation, campaign_id="campaign", run_id="campaign-aws", bootstrap_template="cloud-init.yaml",
        terraform_directory="terraform/aws", child_result_path="results/raw/campaign-aws",
        config_sha256=resolved.config_source.sha256, resolved_sha256=StringEvidence(value=resolved.resolved_sha256),
        reservation=Evidence[ReservationEvidence](value=reservation), expiry=Evidence[ExpiryEvidence](value=expiry),
        implementation_git_commit=StringEvidence(value="b"*40), design_git_commit=StringEvidence(value="c"*40),
    )
    assert value.observation_id == "stable-observation"
    assert value.replaces_run_id == "prior-aws"
    assert value.reservation.value == reservation and value.expiry.value == expiry
    assert value.campaign_options.labels["purpose"] == "thesis-experiment"
    assert value.provisioning_timeout_seconds == resolved.options.provisioning_timeout_seconds
    assert value.readiness_timeout_seconds == resolved.options.readiness_timeout_seconds
    assert value.cleanup_timeout_seconds == resolved.options.cleanup_timeout_seconds
    with pytest.raises(ValueError, match="source observation"):
        ProviderDeploymentInput.from_observation(
            observation, campaign_id="campaign", run_id="campaign-aws", bootstrap_template="cloud-init.yaml",
            terraform_directory="terraform/aws", child_result_path="results/raw/campaign-aws",
            config_sha256=resolved.config_source.sha256,
            implementation_git_commit=StringEvidence(value="b"*40), design_git_commit=StringEvidence(value="c"*40),
            replacement_context=ReplacementContext(observation_id="other-observation", replaces_run_id="prior-aws"),
        )


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


@pytest.mark.parametrize("field", ["region", "zone", "vm_shape", "image", "connection_user"])
def test_deployment_input_rejects_empty_vm_intent_fields(repository_root: Path, field: str) -> None:
    resolved, _ = resolve_campaign(repository_root / "configs/tests/exp-900-single-provider.yaml", repository_root)
    value = ProviderDeploymentInput.from_observation(
        resolved.observations[0], campaign_id="campaign", run_id="campaign-aws",
        bootstrap_template="cloud-init.yaml", terraform_directory="terraform/aws",
        child_result_path="results/raw/campaign-aws", config_sha256=resolved.config_source.sha256,
        implementation_git_commit=StringEvidence(value="b" * 40),
        design_git_commit=StringEvidence(value="c" * 40),
    ).model_dump(mode="python")
    value["vm_a"][field] = ""
    value["vm_b"][field] = ""
    with pytest.raises(ValueError):
        ProviderDeploymentInput.model_validate(value)


def test_direct_deployment_input_rejects_mismatched_identity_and_provider_values(repository_root: Path) -> None:
    resolved, _ = resolve_campaign(repository_root / "configs/tests/exp-900-single-provider.yaml", repository_root)
    valid = ProviderDeploymentInput.from_observation(
        resolved.observations[0], campaign_id="campaign", run_id="campaign-aws",
        bootstrap_template="cloud-init.yaml", terraform_directory="terraform/aws",
        child_result_path="results/raw/campaign-aws", config_sha256=resolved.config_source.sha256,
        implementation_git_commit=StringEvidence(value="b" * 40),
        design_git_commit=StringEvidence(value="c" * 40),
    ).model_dump(mode="json")
    for field, value in (
        ("run_id", "other-azure"),
        ("experiment_id", "bad"),
        ("config_sha256", "not-a-hash"),
        ("provider_options", {"instance_market_type": "invalid"}),
        ("placement", {"kind": "proximity_placement_group", "name": "group"}),
    ):
        candidate = dict(valid)
        candidate[field] = value
        with pytest.raises(ValueError):
            ProviderDeploymentInput.model_validate(candidate)


def test_deployment_input_rejects_replacement_link_without_replacement_context(repository_root: Path) -> None:
    resolved, _ = resolve_campaign(repository_root / "configs/experiments/exp-001-multi-provider.yaml", repository_root)
    value = ProviderDeploymentInput.from_observation(
        resolved.observations[0], campaign_id="campaign", run_id="campaign-aws",
        bootstrap_template="cloud-init.yaml", terraform_directory="terraform/aws",
        child_result_path="results/raw/campaign-aws", config_sha256=resolved.config_source.sha256,
        implementation_git_commit=StringEvidence(value="b"*40), design_git_commit=StringEvidence(value="c"*40),
    ).model_dump(mode="python")
    value["replaces_run_id"] = "earlier-aws"
    with pytest.raises(ValueError, match="replacement context"):
        ProviderDeploymentInput.model_validate(value)


def test_supplied_expiry_must_precede_apply_and_reserved_cap(repository_root: Path) -> None:
    resolved, _ = resolve_campaign(repository_root / "configs/tests/exp-900-single-provider.yaml", repository_root)
    output_identity = IdentityContext(
        experiment_id="EXP-900", config_id="exp-900-acceptance", campaign_id="campaign",
        observation_id="campaign-aws-observation", run_id="campaign-aws",
        deployment_id="campaign-aws-deployment", provider="aws",
    )
    missing = StructuredEvidence(unavailable_reason="partial apply")
    expiry = ExpiryEvidence(
        schema_version=2, run_id="campaign-aws", deployment_id="campaign-aws-deployment", provider="aws", action="stop",
        state="registered", registration_id={"value":"expiry-1"}, registered_at={"value":NOW.replace(minute=10)},
        expires_at={"value":NOW.replace(day=28)}, target_selector="run-tag", provider_metadata={"value":{"tag":"run"}},
    )
    with pytest.raises(ValueError, match="provider apply start"):
        ProviderDeploymentOutput(
            schema_version=2, identity=output_identity, run_id="campaign-aws", deployment_id="campaign-aws-deployment",
            mode="acceptance", provider="aws", scenario="same_zone", apply_action_id="apply", started_at=NOW,
            finished_at=NOW, vm_a=ProviderVmOutput(role="vm_a", resource_id=None, private_ipv4=None, image_identity=None,
                actual_shape=None, region=None, zone=None, placement_metadata=missing, connection=None, unavailable=missing),
            vm_b=ProviderVmOutput(role="vm_b", resource_id=None, private_ipv4=None, image_identity=None,
                actual_shape=None, region=None, zone=None, placement_metadata=missing, connection=None, unavailable=missing),
            provider_metadata=missing, documented_network_limit=missing, expiry={"value":expiry},
        )

    request = ProviderDeploymentInput.from_observation(
        resolved.observations[0], campaign_id="campaign", run_id="campaign-aws",
        bootstrap_template="cloud-init.yaml", terraform_directory="terraform/aws",
        child_result_path="results/raw/campaign-aws", config_sha256=resolved.config_source.sha256,
        implementation_git_commit=StringEvidence(value="b"*40), design_git_commit=StringEvidence(value="c"*40),
    )
    reservation = ReservationEvidence(
        schema_version=2, campaign_id="campaign", reservation_id={"value":"reservation-1"}, state="reserved",
        reserved_cost_usd={"value":10.0}, cap_started_at={"value":NOW.replace(minute=10)},
        expires_at={"value":NOW.replace(day=28)}, cap_start_policy_id={"value":"apply-start"},
        source_path="prices.csv", source_sha256="a"*64,
    )
    prior_expiry = expiry.model_copy(update={"registered_at":Evidence[datetime](value=NOW.replace(minute=5))})
    with pytest.raises(ValueError, match="reserved cap start"):
        ProviderDeploymentInput.model_validate({
            **request.model_dump(mode="python"),
            "reservation":Evidence[ReservationEvidence](value=reservation),
            "expiry":Evidence[ExpiryEvidence](value=prior_expiry),
        })


def test_deployment_output_links_connection_roles_and_canonical_path() -> None:
    observed = StructuredEvidence(value={"kind": "none"})
    connection_a = RemoteConnectionData(role="vm_a", host="10.0.0.4", user="ubuntu", authentication_reference="runtime:ssh", host_key_reference="runtime:known-hosts")
    connection_b = RemoteConnectionData(role="vm_b", host="10.0.0.5", user="ubuntu", authentication_reference="runtime:ssh", host_key_reference="runtime:known-hosts")
    vm_a = ProviderVmOutput(role="vm_a", resource_id="i-a", private_ipv4="10.0.0.4", image_identity="ami-1", actual_shape="m7i.xlarge", region="eu-central-1", zone="euc1-az1", placement_metadata=observed, connection=connection_a, unavailable=None)
    vm_b = ProviderVmOutput(role="vm_b", resource_id="i-b", private_ipv4="10.0.0.5", image_identity="ami-1", actual_shape="m7i.xlarge", region="eu-central-1", zone="euc1-az1", placement_metadata=observed, connection=connection_b, unavailable=None)
    identity = IdentityContext(experiment_id="EXP-001",config_id="exp_001",campaign_id="campaign",observation_id="campaign-aws-observation",run_id="campaign-aws",deployment_id="campaign-aws-deployment",provider="aws")
    base = dict(identity=identity, run_id="campaign-aws", deployment_id="campaign-aws-deployment", mode="acceptance", provider="aws", scenario="same_zone", apply_action_id="apply", started_at=NOW, finished_at=NOW, vm_a=vm_a, vm_b=vm_b, provider_metadata=observed, documented_network_limit=observed)
    with pytest.raises(ValueError, match="connection role"):
        ProviderDeploymentOutput(schema_version=2, **{**base, "vm_a": vm_a.model_copy(update={"connection": connection_b})})
    with pytest.raises(ValueError, match="output_artifact_path"):
        ProviderDeploymentOutput(schema_version=2, **base, output_artifact_path="other.json")
    with pytest.raises(ValueError):
        ProviderDeploymentOutput(schema_version=2, **{**base, "apply_action_id": ""})


def test_complete_provider_output_has_scenario_and_no_unavailable_marker() -> None:
    observed = StructuredEvidence(value={"kind": "none"})
    connection_a = RemoteConnectionData(role="vm_a", host="10.0.0.4", user="ubuntu", authentication_reference="runtime:ssh", host_key_reference="runtime:known-hosts")
    connection_b = RemoteConnectionData(role="vm_b", host="10.0.0.5", user="ubuntu", authentication_reference="runtime:ssh", host_key_reference="runtime:known-hosts")
    output = ProviderDeploymentOutput(schema_version=2,
        identity=IdentityContext(experiment_id="EXP-001",config_id="exp_001",campaign_id="campaign",observation_id="campaign-aws-observation",run_id="campaign-aws",deployment_id="campaign-aws-deployment",provider="aws"),
        run_id="campaign-aws", deployment_id="campaign-aws-deployment", mode="acceptance", provider="aws", scenario="same_zone", apply_action_id="apply",
        started_at=NOW, finished_at=NOW,
        vm_a=ProviderVmOutput(role="vm_a", resource_id="i-a", private_ipv4="10.0.0.4", image_identity="ami-1", actual_shape="m7i.xlarge", region="eu-central-1", zone="euc1-az1", placement_metadata=observed, connection=connection_a, unavailable=None),
        vm_b=ProviderVmOutput(role="vm_b", resource_id="i-b", private_ipv4="10.0.0.5", image_identity="ami-1", actual_shape="m7i.xlarge", region="eu-central-1", zone="euc1-az1", placement_metadata=observed, connection=connection_b, unavailable=None),
        provider_metadata=StructuredEvidence(value={"account": "redacted"}),
        documented_network_limit=StructuredEvidence(value={"description": "up to 12.5 Gbps"}),
    )
    assert output.scenario == "same_zone"
    assert output.vm_a.unavailable is None


@pytest.mark.parametrize(
    ("provider", "provider_options", "allowed"),
    [
        ("aws", {"instance_market_type": "on_demand"}, True),
        ("aws", {"instance_market_type": "spot"}, False),
        ("azure", {"priority": "Regular", "eviction_policy": "Delete", "max_price": -1.0}, True),
        ("azure", {"priority": "Spot", "eviction_policy": "Delete", "max_price": -1.0}, False),
        ("gcp", {"provisioning_model": "STANDARD", "instance_termination_action": "DELETE"}, True),
        ("gcp", {"provisioning_model": "SPOT", "instance_termination_action": "DELETE"}, False),
    ],
)
def test_deployment_input_serializes_provider_native_purchase_options(
    repository_root: Path, tmp_path: Path, provider: str, provider_options: dict[str, object], allowed: bool
) -> None:
    data = yaml.safe_load((repository_root / "configs/tests/exp-903-three-provider-smoke.yaml").read_text())
    data["selected_providers"] = [provider]
    data["context"] = "failure_check"
    data["provider_configs"] = {provider: data["provider_configs"][provider]}
    data["provider_configs"][provider]["provider_options"] = provider_options
    root = tmp_path / f"{provider}-{provider_options}"
    path = root / "configs/tests/spot.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    if not allowed:
        with pytest.raises(ValidationError):
            resolve_campaign(path, root)
        return
    resolved, _ = resolve_campaign(path, root)
    value = ProviderDeploymentInput.from_observation(
        resolved.observations[0], campaign_id="campaign", run_id=f"campaign-{provider}",
        bootstrap_template="cloud-init.yaml", terraform_directory=f"terraform/{provider}",
        child_result_path=f"results/raw/campaign-{provider}", config_sha256=resolved.config_source.sha256,
        implementation_git_commit=StringEvidence(value="b" * 40),
        design_git_commit=StringEvidence(value="c" * 40),
    )
    assert value.model_dump(mode="json")["provider_options"] == provider_options
