import json
import hashlib
from decimal import Decimal
from types import SimpleNamespace

import pytest

from cloud_network_benchmark.lifecycle_runtime import load_runtime_config
from cloud_network_benchmark.lifecycle_runtime import build_admission_request
from cloud_network_benchmark.admission import CostInputs, WorkflowAllowance
from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.contracts.common import Provider


def test_runtime_config_rejects_duplicate_keys_nonfinite_and_unknown_fields(tmp_path):
    for content, message in (
        ('{"schema_version":1,"schema_version":1}', "duplicate"),
        ('{"schema_version":NaN}', "non-finite"),
        (json.dumps({"schema_version": 1, "surprise": True}), "exactly"),
    ):
        path = tmp_path / "runtime.json"
        path.write_text(content, encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            load_runtime_config(path)


def test_runtime_config_requires_exact_provider_selection(tmp_path):
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="exactly"):
        load_runtime_config(path, selected_providers={"gcp"})


def test_f11_request_uses_initialized_child_ids_and_exact_cost_rows(repository_root, tmp_path):
    campaign, _ = resolve_campaign(repository_root / "configs/tests/exp-900-single-provider.yaml",
                                   repository_root)
    provider = Provider(campaign.selected_providers[0]).value
    child = SimpleNamespace(run_id=f"initialized-{provider}", deployment_id=f"initialized-{provider}-deployment",
        observation_id=f"initialized-{provider}-observation", execution_state="initialized")
    rows = {}
    for name in ("compute", "disks", "addresses", "traffic_export", "tax", "uncertainty", "expiry_cleanup_support"):
        rows.setdefault(provider, {"envelope_id": f"env-{provider}", "provider": provider,
            "run_id": child.run_id, "priced_on": "2026-10-23", "source_kind": "provider-price-input",
            "regions": sorted({campaign.observations[0].provider_config.regions.vm_a,
                               campaign.observations[0].provider_config.regions.vm_b}),
            "unit_basis": "one-two-vm-provider-run", "components": {}})
        rows[provider]["components"][name] = {"amount_usd": "0.01", "quantity": "1", "unit": "hour",
            "inclusion_rule": "fixture source row", "funding_bucket": "primary"}
    payload = {"provider_run_envelopes": rows}
    source = tmp_path / "cost-inputs.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    raw = source.read_bytes()
    costs = CostInputs("fixture", "2026-10-23", str(source), hashlib.sha256(raw).hexdigest(),
        hashlib.sha256(raw).hexdigest(), payload, ("fixture",), False)
    allowance = WorkflowAllowance(10, 20, 10, 60, 100, 5, 10, 10, "fixture-policy", {provider: 60})
    runtime = {"providers": {provider: {}}, "allowance": allowance, "approval_evidence": {}}
    parent = SimpleNamespace(campaign_id="initialized-campaign")

    request, plan = build_admission_request(campaign=campaign, parent=parent, children={provider: child},
        costs=costs, runtime=runtime)

    assert plan.child_identities == {provider: child.run_id}
    assert plan.cost_envelopes[provider]["run_id"] == child.run_id
    assert plan.cost_envelopes[provider]["source_path"] == str(source)
    assert request.campaign_id == parent.campaign_id
    assert request.estimated_cost_usd.value == pytest.approx(0.07)
    assert plan.funding == {"primary": Decimal("0.07")}
