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
    from cloud_network_benchmark.manifests import RepositoryGitProvenance
    child.config_provenance = SimpleNamespace(
        implementation_git_commit=RepositoryGitProvenance().commit(repository_root))
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
    # Request construction now retains executable/template provenance too. This
    # synthetic descriptor supplies identity only and grants no paid authority.
    runtime_path = tmp_path / "SYNTHETIC-runtime.json"
    runtime_path.write_text('{"scope":"SYNTHETIC-request-only"}')
    runtime = {"providers": {provider: {"sources": {"expiry_cost_path": aws_expiry_source},
        "bootstrap_template": repository_root / "terraform/aws/bootstrap.yaml",
        "terraform_directory": repository_root / "terraform/aws/pair"}},
        "runtime_source": {"path": str(runtime_path),
            "sha256": hashlib.sha256(runtime_path.read_bytes()).hexdigest()},
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
    assert runtime["runtime_source"] in plan.financial_binding["sources"]
    assert plan.financial_binding["initialized_revisions"] == [child.config_provenance.implementation_git_commit.value]

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
    allowance = WorkflowAllowance(105, 180, 210, 60, 100, 15, 45, 120, "test-policy", {"aws": 360})
    runtime = {"allowance": allowance, "providers": {"aws": {
        "options": {"account_id": "123456789012", "controller_cidr": "203.0.113.4/32",
            "ami_ids": {"eu-central-1": "ami-0123456789abcdef0"},
            "public_key_path": public_key, "authentication_reference": "test-key-ref",
            "plan_review_timeout_seconds": 60, "iam_settle_seconds": 30},
        "bootstrap_template": repository_root / "terraform/aws/bootstrap.yaml",
        "terraform_directory": repository_root / "terraform/aws",
        "sources": {"expiry_cost_path": provider_root / "aws-ssm-automation-public-2026-10-22.json",
            "primary_review_path": None}}}}
    class Clock:
        current = now
        @classmethod
        def utc_now(cls): return cls.current
        @staticmethod
        def monotonic(): return 100.0
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
    assert captured["driver"].settings.complete_workflow_remaining_seconds == 835
    assert captured["driver"].settings.post_registration_workflow_seconds == 730
    assert captured["driver"].settings.post_provision_workflow_seconds == 550
    assert captured["driver"].settings.registration_timeout_seconds == 105
    assert captured["driver"].settings.plan_review_timeout_seconds == 60
    assert captured["driver"].settings.iam_settle_seconds == 30
    # At the full sourced registration allowance, the fixed 840s trigger span
    # still leaves exactly the sourced 180s provisioning phase after reserving
    # readiness through cleanup; the 1,200s reservation cap is unchanged.
    full_registration_remaining = 840 - 105
    full_registration_apply_window = (full_registration_remaining
        - captured["driver"].settings.post_provision_workflow_seconds)
    assert full_registration_apply_window == 185
    from cloud_network_benchmark.providers.aws import _pair_apply_timeout_seconds
    assert _pair_apply_timeout_seconds(180, 120, 60, full_registration_apply_window) == 180
    # Advance the production driver's injected clock by the observed 74s registration.
    # The fixed cap remains 1,200s and AWS's trigger remains 840s after activation.
    Clock.current = now + timedelta(seconds=74)
    remaining_after_registration = (captured["request"].expires_at.value - captured["driver"].clock()).total_seconds()
    assert remaining_after_registration == 766
    assert remaining_after_registration < captured["driver"].settings.complete_workflow_remaining_seconds
    assert remaining_after_registration >= captured["driver"].settings.post_registration_workflow_seconds
    available_to_provision = (remaining_after_registration
        - captured["driver"].settings.post_provision_workflow_seconds)
    assert available_to_provision == 216  # 180s sourced provision allowance plus 36s available slack.
    # 60s review is inside the 180s provision phase, leaving the normal 120s
    # command budget. The trigger slack is not mistaken for extra phase budget.
    assert _pair_apply_timeout_seconds(180, 120, 60, available_to_provision) == 180


def test_gcp_workflow_factory_supplies_full_and_post_registration_allowances(repository_root, tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from cloud_network_benchmark.contracts.admission import ReservationEvidence
    from cloud_network_benchmark.contracts.common import Evidence
    from cloud_network_benchmark.lifecycle_adapters import RemoteStages
    from cloud_network_benchmark.lifecycle_runtime import build_provider_workflows

    campaign, _ = resolve_campaign(repository_root / "configs/tests/exp-902-gcp-single-provider.yaml",
                                   repository_root)
    child = SimpleNamespace(run_id="initialized-campaign-gcp", deployment_id="initialized-campaign-gcp-deployment",
        observation_id="initialized-gcp-observation", execution_state="initialized", mode="acceptance",
        config_provenance=SimpleNamespace(sha256=campaign.config_source.sha256,
            resolved_snapshot_sha256=StringEvidence(value="a" * 64),
            implementation_git_commit=StringEvidence(value="b" * 40),
            design_git_commit=StringEvidence(value="c" * 40)))
    parent = SimpleNamespace(campaign_id="initialized-campaign",
        child_runs=[SimpleNamespace(provider="gcp", result_path="results/initialized-gcp")])
    source = tmp_path / "costs.json"
    payload = {"provider_run_envelopes": {"gcp": {"run_id": child.run_id}}}
    source.write_text(json.dumps(payload), encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    costs = CostInputs("fixture", "2026-10-23", str(source), digest, digest, payload, ("fixture",), False)
    now = datetime.now(timezone.utc)
    reservation = ReservationEvidence(schema_version=2, campaign_id=parent.campaign_id,
        reservation_id=Evidence[str](value="reservation-gcp-test"), state="reserved",
        reserved_cost_usd=Evidence[float](value=10), cap_started_at=Evidence[datetime](value=now),
        expires_at=Evidence[datetime](value=now + timedelta(seconds=1200)),
        cap_start_policy_id=StringEvidence(value="reservation-activation-before-provider-expiry"),
        source_path=str(source), source_sha256=digest)
    workflow_cost = tmp_path / "gcp-cost-handoff.json"
    workflow_cost.write_text(json.dumps({"source_path": str(source), "source_sha256": digest,
        "prices_checked_on": "2026-10-23", "estimated_total_usd": 10, "units": {"vm": "hour"}}))
    public_key = tmp_path / "id_ed25519.pub"
    public_key.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA test\n")
    allowance = WorkflowAllowance(90, 180, 540, 60, 100, 15, 45, 120, "source-bound-fixture", {"gcp": 30})
    runtime = {"allowance": allowance, "providers": {"gcp": {
        "options": {"project_id": "cnb-test-12345", "controller_cidr": "203.0.113.4/32",
            "images": {"europe-west3": "projects/ubuntu-os-cloud/global/images/ubuntu-2404-lts-amd64-v20261001",
                "europe-west2": "projects/ubuntu-os-cloud/global/images/ubuntu-2404-lts-amd64-v20261001"},
            "public_key_path": public_key, "authentication_reference": "test-key-ref"},
        "bootstrap_template": repository_root / "terraform/gcp/bootstrap.yaml",
        "terraform_directory": repository_root / "terraform/gcp",
        "sources": {"workflow_cost_handoff_path": workflow_cost,
            "support_cost_path": repository_root / "tests/fixtures/gcp/zero-support-cost-2026-10-23.json",
            "primary_review_path": None}}}}

    class Clock:
        @staticmethod
        def utc_now(): return now
        @staticmethod
        def monotonic(): return 100.0
    class Ledger:
        controller_session_id = "test-session"
        def inspect(self):
            return {"reservations": {"reservation-gcp-test": {"session": self.controller_session_id,
                "cap_start_monotonic": 100, "cap_seconds": 1200}}}

    captured = {}
    def capture(driver, deployment, request, **kwargs):
        captured.update(driver=driver, deployment=deployment, request=request, kwargs=kwargs)
        return SimpleNamespace(provider="gcp")
    monkeypatch.setattr("cloud_network_benchmark.lifecycle_adapters.gcp_hooks", capture)
    stages = RemoteStages(*(lambda *_args: None for _ in range(6)))
    workflows = build_provider_workflows(campaign=campaign, parent=parent,
        children={"gcp": child}, costs=costs, runtime=runtime, reservation=reservation,
        ledger=Ledger(), runner=object(), clock=Clock(), stages={"gcp": stages}, private_root=tmp_path)

    assert set(workflows) == {"gcp"}
    assert captured["driver"].settings.complete_workflow_remaining_seconds == 1150
    assert captured["driver"].settings.registration_workflow_seconds == 90
    assert captured["driver"].settings.post_registration_workflow_seconds == 1060
    assert captured["request"].expires_at.value == reservation.expires_at.value - timedelta(seconds=30)
