"""Exercise the real all-provider factory through a failed protection barrier."""
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
    def utc_now(self): return NOW
    def monotonic(self): return 100.0


class FailedExternalCommands:
    def __init__(self): self.actions = []
    def run(self, request):
        self.actions.append(request.action_id)
        return CommandResult(action_id=request.action_id, outcome="failed", started_at=NOW,
            finished_at=NOW, duration_seconds=0, exit_code=1, stdout="", stderr="injected offline failure")


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
