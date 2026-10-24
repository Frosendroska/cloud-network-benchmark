"""Exercise default provider factories through source-shaped offline commands."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cloud_network_benchmark.admission import WorkflowAllowance, load_cost_inputs
from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.contracts.execution import CommandResult
from cloud_network_benchmark.lifecycle_runtime import build_runtime
from cloud_network_benchmark.manifests import initialize_campaign
from cloud_network_benchmark.orchestration import run_lifecycle
from cloud_network_benchmark.lifecycle_runtime import load_initialized_campaign
from cloud_network_benchmark.lifecycle_cli import main as lifecycle_main
from cloud_network_benchmark.access import TimeoutPolicy
from cloud_network_benchmark.bootstrap import BootstrapConfig, SourceBuild
from cloud_network_benchmark.readiness import ReadinessConfig


ROOT = Path(__file__).parents[2]
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self): self.elapsed = 0.0
    def utc_now(self): return NOW + timedelta(seconds=self.elapsed)
    def monotonic(self): return 100.0 + self.elapsed
    def sleep(self, seconds): self.elapsed += seconds


class FailedExternalCommands:
    def __init__(self): self.actions = []
    def run(self, request):
        self.actions.append(request.action_id)
        return CommandResult(action_id=request.action_id, outcome="failed", started_at=NOW,
            finished_at=NOW, duration_seconds=0, exit_code=1, stdout="", stderr="injected offline failure")


class GcpSourceRunner:
    """Source-shaped gcloud/Terraform replies for an observed real GCP driver."""
    def __init__(self):
        self.driver = None
        self.deployment = None
        self.requests = []
        self.actions = []
        self.error = None
        self.clock = None

    def run(self, request):
        import base64, hashlib, struct
        from tests.integration.test_gcp_provider_sequences import _template_response, _instance_response
        self.requests.append(request)
        self.actions.append(request.action_id)
        driver = self.driver
        assert driver is not None, "the default factory must construct the real GCP driver first"
        d, settings = self.deployment, driver.settings
        action = request.action_id
        now = self.clock.utc_now()
        trigger = d.reservation.value.expires_at.value - __import__("datetime").timedelta(seconds=30)
        namespace = driver._namespace(d)
        def result(value=""):
            return CommandResult(action_id=action, outcome="succeeded", started_at=now,
                finished_at=now, duration_seconds=0, exit_code=0,
                stdout=value if isinstance(value, str) else json.dumps(value), stderr="")
        if action.endswith("-apply") or action.endswith("-destroy"):
            return result({})
        if action == "gcp-protection-outputs":
            self.clock.elapsed += 2
            now = self.clock.utc_now()
            templates = {role: {"id": str(111+i), "name": f"{namespace}-{'a' if i == 0 else 'b'}",
                "self_link_unique": f"https://www.googleapis.com/compute/v1/projects/{settings.project_id}/global/instanceTemplates/{namespace}-{'a' if i == 0 else 'b'}?uniqueId={111+i}"}
                for i, role in enumerate(("vm_a", "vm_b"))}
            return result({"templates": {"value": templates}, "compact_policy_id": {"value": None}})
        if action == "gcp-pair-outputs":
            instances = {role: {"id": str(73101+i), "name": f"{namespace}-{'a' if i == 0 else 'b'}",
                "zone": (d.vm_a if i == 0 else d.vm_b).zone,
                "self_link": f"projects/{settings.project_id}/zones/{(d.vm_a if i == 0 else d.vm_b).zone}/instances/{namespace}-{'a' if i == 0 else 'b'}"}
                for i, role in enumerate(("vm_a", "vm_b"))}
            return result({"instances": {"value": instances}})
        if "inventory" in action:
            return result([])
        role = "vm_a" if action.endswith("vm_a") or f"{namespace}-a" in request.argv else "vm_b"
        intent = d.vm_a if role == "vm_a" else d.vm_b
        suffix = "a" if role == "vm_a" else "b"
        if action.startswith(("gcp-template-read", "gcp-template-precreate")):
            value = _template_response(driver, d, role, "111" if role == "vm_a" else "112", trigger, NOW+timedelta(seconds=1))
            return result(value)
        if action.startswith(("gcp-instance-read", "gcp-hostkey-instance")):
            value = _instance_response(driver, d, role)
            value.update({"scheduling": {"provisioningModel": "STANDARD", "instanceTerminationAction": "STOP",
                "terminationTime": trigger.isoformat().replace("+00:00", "Z"), "automaticRestart": False},
                "resourcePolicies": []})
            return result(value)
        if action == "gcp-guest-hostkeys":
            blob = base64.b64encode(struct.pack(">I", 11)+b"ssh-ed25519"+struct.pack(">I", 32)+b"h"*32).decode()
            return result({"queryValue": {"items": [{"namespace": "hostkeys", "key": "ssh-ed25519", "value": blob}]}})
        if action.startswith("gcp-boot-disk"):
            return result({"id": "2001" if role == "vm_a" else "2002", "name": f"{namespace}-{suffix}-boot",
                "zone": f"https://www.googleapis.com/compute/v1/projects/{settings.project_id}/zones/{intent.zone}",
                "sizeGb": "32", "type": f"zones/{intent.zone}/diskTypes/pd-balanced",
                "sourceImage": settings.images[intent.region], "labels": {"cnb-run": hashlib.sha256(d.run_id.encode()).hexdigest()[:20],
                    "cnb-deploy": hashlib.sha256(d.deployment_id.encode()).hexdigest()[:20]}})
        if action.startswith("gcp-image-read"):
            return result({"architecture": "X86_64", "name": settings.images[intent.region].rsplit("/", 1)[-1], "guestOsFeatures": [{"type": "GVNIC"}]})
        raise AssertionError(f"unexpected GCP command {action}: {request.argv}")


def test_default_gcp_factory_protects_provisions_and_cleans_up_with_source_shaped_commands(
        tmp_path, repository_root, monkeypatch):
    """Use real F11, GCP provider hooks and F09 finalizer with only command replies faked."""
    import cloud_network_benchmark.lifecycle_adapters as adapters

    config = repository_root / "configs/tests/exp-902-gcp-single-provider.yaml"
    campaign, _ = resolve_campaign(config, repository_root)
    initialized = initialize_campaign(config, repository_root, tmp_path / "results",
        clock=lambda: NOW, token_source=lambda: "gcp-source-shaped")
    parent_path = Path(initialized.parent_manifest_path)
    parent, children = load_initialized_campaign(parent_path, repository_root=repository_root, config_path=config)
    costs = _costs(tmp_path / "costs.json", campaign, children)
    gates = {}
    for name in ("f10_offline", "paid_authorization", "quota_review"):
        source = tmp_path / f"{name}.txt"; source.write_text(f"synthetic {name}\n")
        gates[name] = {"evidence_id": f"synthetic-{name}", "source_path": str(source),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    pub = tmp_path / "test.pub"
    pub.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE9F4huSqss0wx3n6f5+WJiEpePM3oXTbuOzBLRR5NCc test\n")
    handoff = tmp_path / "gcp-handoff.json"
    gcp_total = sum(float(value["amount_usd"]) for value in costs.payload["provider_run_envelopes"]["gcp"]["components"].values())
    handoff.write_text(json.dumps({"source_path": costs.source_path, "source_sha256": costs.payload_sha256,
        "prices_checked_on": "2026-10-22", "estimated_total_usd": gcp_total,
        "units": {"two-vm-run": 1}}))
    bootstrap = BootstrapConfig(sources=[
        SourceBuild(tool="sockperf", source_url="https://example.invalid/sockperf.tar.gz",
            source_reference="sockperf-src", archive_name="sockperf.tar.gz", source_sha256="a"*64),
        SourceBuild(tool="iperf3", source_url="https://example.invalid/iperf3.tar.gz",
            source_reference="iperf3-src", archive_name="iperf3.tar.gz", source_sha256="b"*64)],
        remote_root="/var/lib/cnb", workspace="/var/lib/cnb/work", install_prefix="/var/lib/cnb/install")
    runtime = {"initialized_campaign_manifest": parent_path,
        "ledger": {"path": tmp_path / "ledger.json", "scope_id": "gcp-source-shaped", "initialize_if_missing": True},
        "allowance": WorkflowAllowance(10, 30, 30, 60, 80, 5, 10, 10,
                "synthetic-test-policy", {"gcp": 30}), "copy_retry_seconds": 1,
        "approval_evidence": gates,
        "access": {"timeouts": TimeoutPolicy(ssh_seconds=5, command_seconds=10,
                cloud_init_seconds=10, private_path_seconds=5, server_seconds=5, transfer_seconds=10),
            "readiness": ReadinessConfig(poll_interval_seconds=.01, attempt_ceiling=1),
            "bootstrap": bootstrap, "guest_remote_root": "/var/lib/cnb/runtime",
            "diagnostic_interfaces": {"gcp": {"vm_a": "ens4", "vm_b": "ens4"}}},
        "providers": {"gcp": {"options": {"project_id": "benchmark-project1", "controller_cidr": "203.0.113.4/32",
                "images": {"europe-west3": "projects/ubuntu-os-cloud/global/images/ubuntu-2404-lts-amd64-20261001",
                    "europe-west2": "projects/ubuntu-os-cloud/global/images/ubuntu-2404-lts-amd64-20261001"},
                "public_key_path": pub, "authentication_reference": "fake-gcp-auth"},
            "bootstrap_template": repository_root / "terraform/gcp/pair/main.tf",
            "terraform_directory": repository_root / "terraform/gcp",
            "sources": {"workflow_cost_handoff_path": handoff,
                "support_cost_path": repository_root / "tests/fixtures/gcp/zero-support-cost-2026-10-23.json",
                "primary_review_path": None}}}}
    clock, runner = Clock(), GcpSourceRunner()
    runner.clock = clock
    original_gcp_hooks = adapters.gcp_hooks
    def observe_gcp_hooks(driver, deployment, expiry_request, **kwargs):
        runner.driver = driver
        runner.deployment = deployment
        runner.expiry_request = expiry_request
        return original_gcp_hooks(driver, deployment, expiry_request, **kwargs)
    monkeypatch.setattr(adapters, "gcp_hooks", observe_gcp_hooks)
    built = build_runtime(campaign=campaign, parent=parent, children=children, costs=costs,
        runtime=runtime, controller_session_id="synthetic-session", runner=runner,
        clock=clock, private_root=tmp_path / "private", repository_root=repository_root)
    assert load_cost_inputs(Path(costs.source_path)).payload_sha256 == costs.payload_sha256
    result = run_lifecycle(ledger=built["ledger"], reservation_request=built["request"],
        admission_plan=built["plan"], allowance=built["allowance"],
        workflow_factory=built["workflow_factory"], clock=clock,
        recovery_path=built["recovery_path"], recovery_factory=built["recovery_factory"])
    assert result.admission_status == "reserved", result.admission_reasons
    assert set(result.provider_results) == {"gcp"}, (result.primary_failure, result.admission_status, result.provider_results)
    provider = result.provider_results["gcp"]
    assert provider.values.get("protect") is not None, (provider.primary_failure, runner.actions)
    assert provider.values["protect"].state == "registered", (provider.values["protect"].failure, runner.actions)
    assert provider.values["claim"].eligible is True
    assert provider.values["claim"].creation_granted is True
    assert provider.values.get("create") is not None, (provider.primary_failure, provider.values, runner.actions)
    assert provider.values["create"].run_id == children["gcp"].run_id
    assert provider.values["cleanup"] is not None
    assert provider.values["cleanup"].cleanup_state == "succeeded"
    assert provider.values["cleanup"].inventory.value["pages_complete"] is True
    assert provider.values["cleanup"].inventory.value["resources"] == []
    assert "gcp-protection-outputs" in [request.action_id for request in runner.requests]
    assert "gcp-pair-outputs" in [request.action_id for request in runner.requests]
    assert "gcp-pair-apply" in [request.action_id for request in runner.requests]
    assert "gcp-pair-destroy" in [request.action_id for request in runner.requests]
    # Reopen only the cleanup obligation to model a process loss after the
    # provider journal was written. The default CLI must reload the original
    # saved provider settings and reconcile without creating a new attempt.
    recovery = json.loads(built["recovery_path"].read_text())
    recovery["providers"][0].update(expiry_registered=True,
        creation_may_have_started=True, cleanup_open=True)
    built["recovery_path"].write_text(json.dumps(recovery, sort_keys=True))
    before = len(runner.actions)
    cleanup_code = lifecycle_main(["cleanup", "--recovery", str(built["recovery_path"]),
        "--controller-session", "recovery-session", "--format", "json"],
        cleanup_runner=runner, cleanup_utc_now=clock.utc_now, cleanup_monotonic_now=clock.monotonic)
    assert cleanup_code == 0
    assert not any("apply" in action or "measure" in action for action in runner.actions[before:])


def test_default_azure_factory_uses_observed_cleanup_runner_for_provision_and_cleanup(
        tmp_path, repository_root, monkeypatch):
    """Keep Azure's real hook and provider functions while faking only CLI replies."""
    import cloud_network_benchmark.lifecycle_adapters as adapters
    from tests.integration.test_azure_provider_flow import CleanupRunner
    from cloud_network_benchmark.contracts.common import CommandOutcome

    config = repository_root / "configs/tests/exp-901-azure-single-provider.yaml"
    campaign, _ = resolve_campaign(config, repository_root)
    initialized = initialize_campaign(config, repository_root, tmp_path / "results",
        clock=lambda: NOW, token_source=lambda: "azure-source-shaped")
    parent_path = Path(initialized.parent_manifest_path)
    parent, children = load_initialized_campaign(parent_path, repository_root=repository_root, config_path=config)
    costs = _costs(tmp_path / "costs.json", campaign, children)
    gates = {}
    for name in ("f10_offline", "paid_authorization", "quota_review"):
        source = tmp_path / f"{name}.txt"; source.write_text(f"synthetic {name}\n")
        gates[name] = {"evidence_id": f"synthetic-{name}", "source_path": str(source),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    pub = tmp_path / "test.pub"
    pub.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE9F4huSqss0wx3n6f5+WJiEpePM3oXTbuOzBLRR5NCc test\n")
    identity = tmp_path / "azure-id"
    identity.write_text("synthetic private identity for offline runtime only\n")
    identity.chmod(0o600)
    expiry_cost = tmp_path / "azure-expiry-cost.json"
    expiry_cost.write_text(json.dumps({"source_id": "synthetic-azure-prices", "date": "2026-10-22",
        "sha256": "a" * 64, "automation_rate_per_job_minute": 0.002, "job_minutes_bound": 20}))
    runtime = {"initialized_campaign_manifest": parent_path,
        "ledger": {"path": tmp_path / "ledger.json", "scope_id": "azure-source-shaped", "initialize_if_missing": True},
        "allowance": WorkflowAllowance(10, 30, 30, 60, 80, 5, 10, 10,
            "synthetic-test-policy", {"azure": 360}), "copy_retry_seconds": 1,
        "approval_evidence": gates,
        "access": {"timeouts": TimeoutPolicy(ssh_seconds=5, command_seconds=10,
                cloud_init_seconds=10, private_path_seconds=5, server_seconds=5, transfer_seconds=10),
            "readiness": ReadinessConfig(poll_interval_seconds=.01, attempt_ceiling=1),
            "guest_remote_root": "/var/lib/cnb/runtime",
            "diagnostic_interfaces": {"azure": {"vm_a": "eth0", "vm_b": "eth0"}}},
        "providers": {"azure": {"options": {"subscription_id": "00000000-0000-0000-0000-000000000001",
                "tenant_id": "00000000-0000-0000-0000-000000000002", "controller_cidr": "203.0.113.4/32",
                "selected_image": "Canonical:0001-com-ubuntu-server-noble:24_04-lts:2026.10.01",
                "ssh_public_key_path": pub, "authentication_reference": str(identity),
                "poll_seconds": 15, "command_timeout_seconds": 60},
            "bootstrap_template": repository_root / "terraform/azure/pair/main.tf",
            "terraform_directory": repository_root / "terraform/azure",
            "sources": {"expiry_cost_path": expiry_cost, "primary_review_path": None}}}}
    clock = Clock()
    class AzureCommandRunner:
        def __init__(self):
            self.delegate = None
            self.requests = []
        def run(self, request):
            assert self.delegate is not None, "actual Azure hook must bind the cleanup runner before protection"
            self.requests.append(request)
            self.delegate.now = clock.utc_now()
            if request.action_type == "vm_image_observation":
                self.delegate.requests.append(request)
                publisher, offer, sku, version = self.delegate.o.selected_image.split(":")
                payload = {"storageProfile": {"imageReference": {"publisher": publisher,
                    "offer": offer, "sku": sku, "exactVersion": version}}}
                return CommandResult(action_id=request.action_id, outcome=CommandOutcome.SUCCEEDED,
                    started_at=clock.utc_now(), finished_at=clock.utc_now(), duration_seconds=0,
                    exit_code=0, stdout=json.dumps(payload), stderr="")
            return self.delegate.run(request)
    runner = AzureCommandRunner()
    original_azure_hooks = adapters.azure_hooks
    observed = {}
    def observe_azure_hooks(deployment, expiry_request, **kwargs):
        observed.update(deployment=deployment, expiry_request=expiry_request, options=kwargs["options"])
        runner.delegate = CleanupRunner(deployment, kwargs["options"], clock.utc_now())
        return original_azure_hooks(deployment, expiry_request, **kwargs)
    monkeypatch.setattr(adapters, "azure_hooks", observe_azure_hooks)
    built = build_runtime(campaign=campaign, parent=parent, children=children, costs=costs,
        runtime=runtime, controller_session_id="synthetic-session", runner=runner,
        clock=clock, private_root=tmp_path / "private", repository_root=repository_root)
    assert load_cost_inputs(Path(costs.source_path)).payload_sha256 == costs.payload_sha256
    result = run_lifecycle(ledger=built["ledger"], reservation_request=built["request"],
        admission_plan=built["plan"], allowance=built["allowance"],
        workflow_factory=built["workflow_factory"], clock=clock,
        recovery_path=built["recovery_path"], recovery_factory=built["recovery_factory"])
    assert result.admission_status == "reserved", result.admission_reasons
    assert set(result.provider_results) == {"azure"}
    provider = result.provider_results["azure"]
    assert provider.values["protect"].state == "registered", provider.values["protect"].failure
    assert provider.values["claim"].eligible is True
    assert provider.values["claim"].creation_granted is True
    assert provider.values["create"] is not None, (provider.primary_failure, provider.values)
    assert provider.values["create"].deployment is not None, provider.values["create"].failures[0].message
    assert provider.values["create"].deployment.run_id == children["azure"].run_id
    assert provider.values["cleanup"].cleanup_state == "succeeded"
    assert provider.values["cleanup"].failures == []
    assert all(resource["type"] == "Microsoft.Automation/automationAccounts"
        for resource in provider.values["cleanup"].resources)
    assert observed["deployment"].run_id == children["azure"].run_id
    assert observed["expiry_request"].target_selector
    assert {request.action_type for request in runner.requests} >= {
        "expiry_outputs", "pair_apply", "pair_output", "pair_inventory", "pair_destroy"}


def _costs(path: Path, campaign, children):
    regions = {str(item.provider): sorted({item.vm_a.region, item.vm_b.region})
        for item in campaign.observations}
    components = {}
    for provider in children:
        parts = {}
        for name in ("compute", "disks", "addresses", "traffic_export", "tax", "uncertainty"):
            parts[name] = {"amount_usd": "0.1", "quantity": "1", "unit": "unit",
                "inclusion_rule": "synthetic test input", "funding_bucket": "primary"}
        parts["expiry_cleanup_support"] = {"amount_usd": "0.0139", "quantity": "1",
            "unit": "region-session", "inclusion_rule": "synthetic test input", "funding_bucket": "primary"}
        components[provider] = {"envelope_id": f"synthetic-{provider}", "provider": provider,
            "run_id": children[provider].run_id, "priced_on": "2026-10-22",
            "source_kind": "provider-price-input", "regions": regions[provider],
            "unit_basis": "one-two-vm-provider-run", "components": parts}
    payload = json.loads((ROOT / "tests/fixtures/f11/cost-inputs.json").read_text())
    payload.update(cost_input_id=campaign.admission.cost_input_id, prices_checked_on="2026-10-22",
        acquired_on="2026-10-22", refresh_required_before_paid_execution=False,
        funding={"primary": "2", "replacements": "0", "tax": "0", "uncertainty": "0"},
        provider_run_envelopes=components)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return load_cost_inputs(path)


def test_default_three_provider_factory_reserves_then_blocks_every_claim_after_protection_failure(tmp_path):
    config = ROOT / "configs/tests/exp-903-three-provider-smoke.yaml"
    campaign, _ = resolve_campaign(config, ROOT)
    initialized = initialize_campaign(config, ROOT, tmp_path / "results",
        clock=lambda: NOW, token_source=lambda: "campaign")
    parent_path = Path(initialized.parent_manifest_path)
    parent, children = load_initialized_campaign(parent_path, repository_root=ROOT, config_path=config)
    costs = _costs(tmp_path / "costs.json", campaign, children)
    cost_hash = costs.payload_sha256
    gates = {}
    for name in ("f10_offline", "paid_authorization", "quota_review"):
        source = tmp_path / f"{name}.txt"; source.write_text(f"synthetic {name}\n")
        gates[name] = {"evidence_id": f"synthetic-{name}", "source_path": str(source),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    aws_cost = ROOT / "tests/fixtures/aws/cost-inputs/aws-ssm-automation-public-2026-10-22.json"
    gcp_handoff = tmp_path / "gcp-handoff.json"
    gcp_handoff.write_text(json.dumps({"source_path": costs.source_path, "source_sha256": cost_hash,
        "prices_checked_on": "2026-10-22", "estimated_total_usd": 1.0,
        "units": {"two-vm-run": 1}}))
    azure_cost = tmp_path / "azure-expiry-cost.json"
    azure_cost.write_text(json.dumps({"source_id": "synthetic-azure-prices", "date": "2026-10-22",
        "sha256": "a" * 64, "automation_rate_per_job_minute": 0.002, "job_minutes_bound": 20}))
    pub = tmp_path / "test.pub"
    pub.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE9F4huSqss0wx3n6f5+WJiEpePM3oXTbuOzBLRR5NCc test\n")
    bootstrap = BootstrapConfig(sources=[
        SourceBuild(tool="sockperf", source_url="https://example.invalid/sockperf.tar.gz",
            source_reference="sockperf-src", archive_name="sockperf.tar.gz", source_sha256="a"*64),
        SourceBuild(tool="iperf3", source_url="https://example.invalid/iperf3.tar.gz",
            source_reference="iperf3-src", archive_name="iperf3.tar.gz", source_sha256="b"*64)],
        remote_root="/var/lib/cnb", workspace="/var/lib/cnb/work", install_prefix="/var/lib/cnb/install")
    runtime = {"initialized_campaign_manifest": parent_path,
        "ledger": {"path": tmp_path / "ledger.json", "scope_id": "three-provider", "initialize_if_missing": True},
        "allowance": WorkflowAllowance(10, 30, 30, 60, 80, 5, 10, 10, "synthetic-test-policy",
            {"aws": 360, "azure": 360, "gcp": 360}),
        "copy_retry_seconds": 1, "approval_evidence": gates,
        "access": {"timeouts": TimeoutPolicy(ssh_seconds=5, command_seconds=10,
                cloud_init_seconds=10, private_path_seconds=5, server_seconds=5, transfer_seconds=10),
            "readiness": ReadinessConfig(poll_interval_seconds=.01, attempt_ceiling=1),
            "bootstrap": bootstrap, "guest_remote_root": "/var/lib/cnb/runtime",
            "diagnostic_interfaces": {p: {"vm_a": "eth0", "vm_b": "eth0"}
                for p in ("aws", "azure", "gcp")}},
        "providers": {
            "aws": {"options": {"account_id": "123456789012", "controller_cidr": "203.0.113.4/32",
                "ami_ids": {"eu-central-1": "ami-0123456789abcdef0"}, "public_key_path": pub,
                "authentication_reference": "fake-aws-auth"}, "bootstrap_template": ROOT / "terraform/aws/pair/main.tf",
                "terraform_directory": ROOT / "terraform/aws", "sources": {"expiry_cost_path": aws_cost,
                    "primary_review_path": None}},
            "azure": {"options": {"subscription_id": "00000000-0000-0000-0000-000000000001",
                "tenant_id": "00000000-0000-0000-0000-000000000002", "controller_cidr": "203.0.113.4/32",
                "selected_image": "Canonical:0001-com-ubuntu-server-noble:24_04-lts:2026.10.01",
                "ssh_public_key_path": pub, "authentication_reference": "fake-azure-auth",
                "poll_seconds": 15, "command_timeout_seconds": 60},
                "bootstrap_template": ROOT / "terraform/azure/pair/main.tf", "terraform_directory": ROOT / "terraform/azure",
                "sources": {"expiry_cost_path": azure_cost, "primary_review_path": None}},
            "gcp": {"options": {"project_id": "benchmark-project1", "controller_cidr": "203.0.113.4/32",
                "images": {"europe-west3": "projects/ubuntu-os-cloud/global/images/ubuntu-2404-lts-amd64-20261001",
                    "europe-west2": "projects/ubuntu-os-cloud/global/images/ubuntu-2404-lts-amd64-20261001"},
                "public_key_path": pub, "authentication_reference": "fake-gcp-auth"},
                "bootstrap_template": ROOT / "terraform/gcp/pair/main.tf", "terraform_directory": ROOT / "terraform/gcp",
                "sources": {"workflow_cost_handoff_path": gcp_handoff,
                    "support_cost_path": ROOT / "tests/fixtures/gcp/zero-support-cost-2026-10-23.json",
                    "primary_review_path": None}}}}
    clock, runner = Clock(), FailedExternalCommands()
    built = build_runtime(campaign=campaign, parent=parent, children=children, costs=costs,
        runtime=runtime, controller_session_id="synthetic-session", runner=runner,
        clock=clock, private_root=tmp_path / "private", repository_root=ROOT)
    result = run_lifecycle(ledger=built["ledger"], reservation_request=built["request"],
        admission_plan=built["plan"], allowance=built["allowance"],
        workflow_factory=built["workflow_factory"], clock=clock,
        recovery_path=built["recovery_path"], recovery_factory=built["recovery_factory"])
    assert result.admission_status == "reserved", (result.admission_reasons, result.primary_failure)
    assert set(result.provider_results) == {"aws", "azure", "gcp"}
    assert all(item.primary_failure is not None for item in result.provider_results.values())
    assert all(item.values["protect"].state == "failed" for item in result.provider_results.values())
    assert all(item.values["protect"] is not None for item in result.provider_results.values())
    assert not any(action in {"aws-pair-apply", "gcp-pair-apply", "pair_apply"} for action in runner.actions)

    cleanup_runner = FailedExternalCommands()
    cleanup_code = lifecycle_main(["cleanup", "--recovery", str(built["recovery_path"]),
        "--controller-session", "cleanup-session", "--format", "json"],
        cleanup_runner=cleanup_runner, cleanup_utc_now=clock.utc_now,
        cleanup_monotonic_now=clock.monotonic)
    assert cleanup_code == 1
    assert set(cleanup_runner.actions) <= {"gcp-inventory-all-zones"}
    assert not any("apply" in action or "measure" in action for action in cleanup_runner.actions)
    state = built["ledger"].inspect()
    assert result.reservation_evidence.reservation_id.value in state["cleanup_open"]
    assert result.reservation_evidence.reservation_id.value in state["cleanup_blocked"]

    recovery = json.loads(built["recovery_path"].read_text())
    settings_path = Path(recovery["providers"][0]["cleanup_settings_reference"])
    settings_path.write_text(settings_path.read_text() + " ")
    tamper_runner = FailedExternalCommands()
    tamper_code = lifecycle_main(["cleanup", "--recovery", str(built["recovery_path"]),
        "--controller-session", "cleanup-session-2", "--format", "json"],
        cleanup_runner=tamper_runner, cleanup_utc_now=clock.utc_now,
        cleanup_monotonic_now=clock.monotonic)
    assert tamper_code == 1
    assert set(tamper_runner.actions) <= {"gcp-inventory-all-zones"}
