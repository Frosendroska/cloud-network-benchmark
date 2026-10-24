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
from cloud_network_benchmark.contracts.common import StringEvidence


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
    aws_expiry_source = repository_root / "tests/fixtures/aws/cost-inputs/aws-ssm-automation-public-2026-10-22.json"
    runtime = {"providers": {provider: {"sources": {"expiry_cost_path": aws_expiry_source}}},
        "allowance": allowance, "approval_evidence": {}}
    parent = SimpleNamespace(campaign_id="initialized-campaign")

    request, plan = build_admission_request(campaign=campaign, parent=parent, children={provider: child},
        costs=costs, runtime=runtime)

    assert plan.child_identities == {provider: child.run_id}
    assert plan.cost_envelopes[provider]["run_id"] == child.run_id
    assert plan.cost_envelopes[provider]["source_path"] == str(source)
    assert request.campaign_id == parent.campaign_id
    assert request.estimated_cost_usd.value == pytest.approx(0.07)
    assert plan.funding == {"primary": Decimal("0.07")}

    blocked, _ = build_admission_request(campaign=campaign, parent=parent, children={provider: child},
        costs=costs, runtime=runtime,
        ledger_state={"cleanup_blocked": [], "reservations": {"old": {
            "cleanup_open": True, "session": "prior-session"}}},
        controller_session_id="current-session")
    assert blocked.outstanding_cleanup is True


def test_aws_workflow_factory_binds_actual_reservation_and_source_hash(repository_root, tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from cloud_network_benchmark.contracts.admission import ReservationEvidence
    from cloud_network_benchmark.contracts.common import Evidence
    from cloud_network_benchmark.lifecycle_adapters import RemoteStages
    from cloud_network_benchmark.lifecycle_runtime import build_aws_provider_workflows

    campaign, _ = resolve_campaign(repository_root / "configs/tests/exp-900-single-provider.yaml",
                                   repository_root)
    source = tmp_path / "costs.json"
    payload = {"source": "source-bound-test-fixture"}
    source.write_text(json.dumps(payload), encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    now = datetime.now(timezone.utc)
    reservation = ReservationEvidence(schema_version=2, campaign_id="initialized-campaign",
        reservation_id=Evidence[str](value="reservation-aws-test"), state="reserved",
        reserved_cost_usd=Evidence[float](value=1), cap_started_at=Evidence[datetime](value=now),
        expires_at=Evidence[datetime](value=now + timedelta(seconds=1200)),
        cap_start_policy_id=StringEvidence(value="reservation-activation-before-provider-expiry"),
        source_path=str(source), source_sha256=digest)
    child = SimpleNamespace(run_id="initialized-campaign-aws", deployment_id="initialized-campaign-aws-deployment",
        observation_id="initialized-campaign-aws-observation", mode="acceptance", execution_state="initialized",
        config_provenance=SimpleNamespace(sha256=campaign.config_source.sha256,
            resolved_snapshot_sha256=StringEvidence(value="a" * 64),
            implementation_git_commit=StringEvidence(value="b" * 40),
            design_git_commit=StringEvidence(value="c" * 40)))
    parent = SimpleNamespace(campaign_id="initialized-campaign",
        child_runs=[SimpleNamespace(provider="aws", result_path="results/initialized-aws")])
    provider_root = repository_root / "tests/fixtures/aws/cost-inputs"
    public_key = tmp_path / "id_ed25519.pub"
    public_key.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA test\n")
    observation = campaign.observations[0]
    envelope = {"provider_run_envelopes": {"aws": {"run_id": child.run_id}}}
    costs = CostInputs("test-costs", "2026-10-23", str(source), digest, digest, envelope, ("fixture",), False)
    allowance = WorkflowAllowance(10, 20, 10, 60, 76, 1, 10, 10, "test-policy", {"aws": 360})
    runtime = {"allowance": allowance, "providers": {"aws": {
        "options": {"account_id": "123456789012", "controller_cidr": "203.0.113.4/32",
            "ami_ids": {"eu-central-1": "ami-0123456789abcdef0"},
            "public_key_path": public_key, "authentication_reference": "test-key-ref"},
        "bootstrap_template": repository_root / "terraform/aws/bootstrap.yaml",
        "terraform_directory": repository_root / "terraform/aws",
        "sources": {"expiry_cost_path": provider_root / "aws-ssm-automation-public-2026-10-22.json",
            "primary_review_path": None}}}}
    class Clock:
        utc_now = staticmethod(lambda: now)
        monotonic = staticmethod(lambda: 100.0)
    class Ledger:
        def inspect(self):
            return {"reservations": {"reservation-aws-test": {"cap_start_monotonic": 100,
                "cap_seconds": 1200}}}
    stages = RemoteStages(*(lambda *_args: None for _ in range(6)))
    captured = {}
    def capture(driver, deployment, request, **kwargs):
        captured.update(driver=driver, deployment=deployment, request=request, kwargs=kwargs)
        return SimpleNamespace(provider="aws")
    monkeypatch.setattr("cloud_network_benchmark.lifecycle_adapters.aws_hooks", capture)
    workflows = build_aws_provider_workflows(campaign=campaign, parent=parent,
        children={"aws": child}, costs=costs, runtime=runtime, reservation=reservation,
        ledger=Ledger(), runner=object(), clock=Clock(), stages=stages, private_root=tmp_path)
    assert set(workflows) == {"aws"}
    assert workflows["aws"].provider == "aws"
    assert captured["deployment"].admission.cost_input_sha256.value == digest
    assert captured["deployment"].config_sha256 == campaign.config_source.sha256
    assert captured["deployment"].resolved_sha256.value == "a" * 64
    assert captured["request"].expires_at.value == reservation.expires_at.value - timedelta(seconds=360)
    assert captured["driver"].settings.cost_input_sha256 == digest
