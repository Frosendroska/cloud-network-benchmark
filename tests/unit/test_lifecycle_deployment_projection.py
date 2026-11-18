"""Provider result normalization cannot supply control identities from foreign outputs."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from cloud_network_benchmark.azure_provider import AzureProviderResult
from cloud_network_benchmark.contracts.common import IdentityContext, StructuredEvidence
from cloud_network_benchmark.contracts.deployment import (
    ProviderDeploymentOutput, ProviderVmOutput, RemoteConnectionData,
)
from cloud_network_benchmark.lifecycle_projection import _created_deployment


def _pair(provider):
    run = f"projection-{provider}"
    identity = IdentityContext(experiment_id="EXP-001", config_id="projection",
        campaign_id="projection", observation_id=f"{run}-observation", run_id=run,
        deployment_id=f"{run}-deployment", provider=provider)
    now = datetime(2026, 11, 18, tzinfo=timezone.utc)
    observed = StructuredEvidence(value={"source": "offline fixture"})
    def vm(role, private, host):
        return ProviderVmOutput(role=role, resource_id=f"resource-{role}", private_ipv4=private,
            image_identity="ubuntu", actual_shape="fixture", region="region", zone="zone",
            placement_metadata=observed, connection=RemoteConnectionData(role=role, host=host,
                user="ubuntu", authentication_reference="offline:auth", host_key_reference="offline:keys"))
    pair = ProviderDeploymentOutput(schema_version=2, identity=identity, run_id=run,
        deployment_id=identity.deployment_id, mode="acceptance", provider=provider,
        scenario="same_zone", apply_action_id="offline-apply", started_at=now, finished_at=now,
        vm_a=vm("vm_a", "10.0.0.4", "8.8.8.8"), vm_b=vm("vm_b", "10.0.0.5", "1.1.1.1"),
        provider_metadata=observed, documented_network_limit=observed)
    child = SimpleNamespace(run_id=run, deployment_id=pair.deployment_id, provider=provider)
    return child, pair


def _project(child, value):
    return _created_deployment(child, SimpleNamespace(values={"create": value}))


def _wrapped(pair):
    # Expiry is outside this projection helper; the factory regression supplies
    # the real validated Azure result, including its independent expiry receipt.
    return AzureProviderResult.model_construct(deployment=pair, failures=[])


@pytest.mark.parametrize("provider", ["aws", "azure", "gcp"])
def test_direct_provider_pair_keeps_authenticated_management_and_private_hosts(provider):
    child, pair = _pair(provider)
    projected = _project(child, pair)
    assert projected is pair
    assert projected.vm_a.connection.host == "8.8.8.8"
    assert projected.vm_b.connection.host == "1.1.1.1"
    assert projected.vm_b.private_ipv4 == "10.0.0.5"


def test_azure_wrapper_resolves_to_the_exact_typed_pair():
    child, pair = _pair("azure")
    assert _project(child, _wrapped(pair)) is _project(child, pair)


@pytest.mark.parametrize("case", ["none", "missing_pair", "unrelated", "foreign_wrapper",
    "foreign_deployment", "foreign_run", "foreign_provider", "foreign_identity", "missing_vm",
    "wrong_role", "failed_wrapper", "azure_wrapper_for_aws"])
def test_unknown_partial_or_foreign_outputs_cannot_supply_control_identity(case):
    child, pair = _pair("azure")
    value = _wrapped(pair)
    if case == "none":
        value = None
    elif case == "missing_pair":
        value = _wrapped(None)
    elif case == "unrelated":
        value = SimpleNamespace(vm_a=pair.vm_a, vm_b=pair.vm_b)
    elif case == "foreign_wrapper":
        value = SimpleNamespace(deployment=pair)
    elif case == "foreign_deployment":
        value = _wrapped(pair.model_copy(update={"deployment_id": "foreign-deployment"}))
    elif case == "foreign_run":
        value = _wrapped(pair.model_copy(update={"run_id": "foreign-azure"}))
    elif case == "foreign_provider":
        value = _wrapped(pair.model_copy(update={"provider": "gcp"}))
    elif case == "foreign_identity":
        value = _wrapped(pair.model_copy(update={"identity": pair.identity.model_copy(
            update={"deployment_id": "foreign-deployment"})}))
    elif case == "missing_vm":
        value = _wrapped(pair.model_copy(update={"vm_b": None}))
    elif case == "wrong_role":
        value = _wrapped(pair.model_copy(update={"vm_b": pair.vm_a}))
    elif case == "failed_wrapper":
        value = value.model_copy(update={"failures": [object()]})
    else:
        child, pair = _pair("aws")
        value = _wrapped(pair)
    assert _project(child, value) is None
