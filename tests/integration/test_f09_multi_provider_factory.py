"""Exercise default provider factories through source-shaped offline commands."""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

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


def _source_bound_test_common_n(tmp_path, request, frozen_clock, deterministic_token, fake_git_commits):
    """Select N from saved F08 source bundles for an explicitly synthetic F09 fixture."""
    from cloud_network_benchmark.calibration import select_common_n
    from tests.integration.test_native_validation_replay import _materialize_complete_bundle

    results = []
    for provider in ("aws", "azure", "gcp"):
        bundle, result = _materialize_complete_bundle(ROOT, tmp_path / "f08-source" / provider,
            frozen_clock, deterministic_token, fake_git_commits, request,
            config_name="exp-001-calibration.yaml", provider=provider)
        assert all(record.sha256.value == hashlib.sha256(
            (Path(bundle.child_result_root) / record.path).read_bytes()).hexdigest()
            for record in bundle.artifact_records)
        results.append(result)
    recommendation = select_common_n(results)
    assert recommendation.common_n.value == 2 and recommendation.plateau_found.value is True
    assert recommendation.source_campaign_id.value and recommendation.source_sha256.value
    return recommendation.common_n_state_fields()


class Clock:
    def __init__(self):
        self._owner = threading.get_ident()
        self._offsets = {self._owner: 0.0}
        self._lock = threading.Lock()
    def _offset(self):
        ident = threading.get_ident()
        with self._lock:
            if ident == self._owner:
                return max(self._offsets.values())
            return self._offsets.setdefault(ident, self._offsets[self._owner])
    def utc_now(self): return NOW + timedelta(seconds=self._offset())
    def monotonic(self): return 100.0 + self._offset()
    def sleep(self, seconds):
        ident = threading.get_ident()
        with self._lock:
            self._offsets[ident] = self._offsets.get(ident, max(self._offsets.values())) + seconds


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
            self.clock.sleep(2)
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
            value["networkInterfaces"][0]["accessConfigs"][0]["natIP"] = "8.8.8.8" if role == "vm_a" else "1.1.1.1"
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
            outcome = self.delegate.run(request)
            if request.action_type == "expiry_destroy" and outcome.outcome == CommandOutcome.SUCCEEDED:
                self.delegate.account_present = False
                self.delegate.present_children.clear()
            return outcome
    runner = AzureCommandRunner()
    original_azure_hooks = adapters.azure_hooks
    observed = {}
    def observe_azure_hooks(deployment, expiry_request, **kwargs):
        observed.update(deployment=deployment, expiry_request=expiry_request, options=kwargs["options"])
        runner.delegate = CleanupRunner(deployment, kwargs["options"], clock.utc_now(),
            fail_delete_once="expiry_schedules_delete")
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
    assert provider.values["cleanup"].cleanup_state == "failed"
    assert provider.values["cleanup"].launch_blocked is True
    assert observed["deployment"].run_id == children["azure"].run_id
    assert observed["expiry_request"].target_selector
    assert {request.action_type for request in runner.requests} >= {
        "expiry_outputs", "pair_apply", "pair_output", "pair_inventory", "pair_destroy"}
    recovery = json.loads(built["recovery_path"].read_text(encoding="utf-8"))
    saved_settings = Path(recovery["providers"][0]["cleanup_settings_reference"])
    assert saved_settings.is_file()
    saved_settings_bytes = saved_settings.read_bytes()
    assert Path(recovery["providers"][0]["journal_reference"]).is_file()
    before_recovery = len(runner.requests)
    cleanup_code = lifecycle_main(["cleanup", "--recovery", str(built["recovery_path"]),
        "--controller-session", "azure-recovery-session", "--format", "json"],
        cleanup_runner=runner, cleanup_utc_now=clock.utc_now,
        cleanup_monotonic_now=clock.monotonic)
    assert cleanup_code == 0
    recovered = runner.requests[before_recovery:]
    assert any(request.action_type == "expiry_schedules_delete" for request in recovered)
    assert any(request.action_type == "pair_inventory" for request in recovered)
    assert not any(request.action_type in {"pair_apply", "expiry_create", "measurement"}
        for request in recovered)
    assert saved_settings.read_bytes() == saved_settings_bytes


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


@pytest.mark.parametrize(("successful_protection", "config_name", "interrupt_f11_cleanup", "interrupt_controller"), [
    (False, "configs/tests/exp-903-three-provider-smoke.yaml", False, False),
    (True, "configs/tests/exp-903-three-provider-smoke.yaml", False, False),
    (True, "configs/tests/exp-903-three-provider-smoke.yaml", True, False),
    (True, "configs/tests/exp-903-three-provider-smoke.yaml", False, True),
    (True, "configs/experiments/exp-001-calibration.yaml", False, False),
    (True, "configs/experiments/exp-001-multi-provider.yaml", False, False),
    (True, "configs/experiments/exp-001-diagnostic-day.yaml", False, False),
], ids=["protection-failure", "all-provider-acceptance", "receipt-before-f11-recovery",
    "controller-interrupt-during-native-f03", "all-provider-calibration",
    "all-provider-short-study", "all-provider-diagnostic-day"])
def test_default_three_provider_factory_reserves_and_enforces_global_claim_barrier(
        tmp_path, monkeypatch, request, frozen_clock, deterministic_token, fake_git_commits,
        successful_protection, config_name, interrupt_f11_cleanup, interrupt_controller):
    config = ROOT / config_name
    schedule_entry = None
    is_calibration = "calibration" in config_name
    is_short_study = "multi-provider" in config_name
    is_diagnostic_day = "diagnostic-day" in config_name
    if is_calibration or is_short_study or is_diagnostic_day:
        import yaml
        from cloud_network_benchmark.contracts.admission import ScheduleEntry
        source_config = config
        config_data = yaml.safe_load(source_config.read_text(encoding="utf-8"))
        base_campaign, _ = resolve_campaign(source_config, ROOT)
        if is_short_study or is_diagnostic_day:
            config_data["benchmark"]["common_n"] = _source_bound_test_common_n(
                tmp_path, request, frozen_clock, deterministic_token, fake_git_commits)
        scenario_source = json.loads((ROOT / "tests/fixtures/f11/scenarios.json").read_text(encoding="utf-8"))
        window_label = "evening" if is_short_study else "night"
        window = scenario_source["synthetic_windows"][window_label]
        scheduled_start = NOW.replace(hour=int(window[:2]), minute=int(window[3:5]), second=int(window[6:8]), microsecond=0)
        config_data["scheduled_start"] = scheduled_start.isoformat().replace("+00:00", "Z")
        config = ROOT / "configs/tests" / f"f09-{tmp_path.name}-synthetic-calibration.yaml"
        request.addfinalizer(lambda: config.unlink(missing_ok=True))
        config.write_text(yaml.safe_dump(config_data, sort_keys=False), encoding="utf-8")
        header = "entry_id,config_id,experiment_id,scheduled_start,window_label,priority"
        mode_suffix = "CAL" if is_calibration else "SHORT" if is_short_study else "DAY"
        entry_id = f"F09-SYNTHETIC-{mode_suffix}"
        row = f"{entry_id},{base_campaign.config_id},{base_campaign.experiment_id},{scheduled_start.isoformat()},{window_label},{base_campaign.admission.priority}"
        schedule_bytes = (header + "\n" + row + "\n").encode("utf-8")
        schedule_path = tmp_path / "synthetic-calibration-schedule.csv"
        schedule_path.write_bytes(schedule_bytes)
        schedule_entry = ScheduleEntry(schema_version=2, entry_id=entry_id,
            config_id=base_campaign.config_id, experiment_id=base_campaign.experiment_id,
            scheduled_start=scheduled_start, window_label=window_label, source_path=str(schedule_path),
            source_sha256=hashlib.sha256(schedule_bytes).hexdigest(),
            row_sha256=hashlib.sha256(row.encode()).hexdigest(), original_header=header,
            original_row=row, columns=header.split(","), priority=base_campaign.admission.priority)
        synthetic_now = scheduled_start - timedelta(minutes=5)
        monkeypatch.setattr(__import__(__name__, fromlist=["NOW"]), "NOW", synthetic_now)
        import tests.integration.test_f09_aws_runtime_factory as aws_runtime_test
        monkeypatch.setattr(aws_runtime_test, "NOW", synthetic_now)
    campaign, _ = resolve_campaign(config, ROOT, schedule_entry=schedule_entry)
    initialized = initialize_campaign(config, ROOT, tmp_path / "results",
        clock=lambda: NOW, token_source=lambda: "campaign", schedule_entry=schedule_entry)
    parent_path = Path(initialized.parent_manifest_path)
    parent, children = load_initialized_campaign(parent_path, repository_root=ROOT, config_path=config)
    costs = _costs(tmp_path / "costs.json", campaign, children)
    cost_hash = costs.payload_sha256
    gates = {}
    for name in ("f10_offline", "paid_authorization", "quota_review"):
        source = tmp_path / f"{name}.txt"; source.write_text(f"synthetic {name}\n")
        gates[name] = {"evidence_id": f"synthetic-{name}", "source_path": str(source),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    if is_calibration or is_short_study or is_diagnostic_day:
        source = tmp_path / "synthetic-mode-authorization.txt"
        source.write_text("offline synthetic mode authorization fixture; test-only evidence\n")
        evidence = {"evidence_id": "synthetic-mode-authorization",
            "source_path": str(source), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
        if is_calibration:
            gates["calibration_authorized"] = evidence
        if is_short_study or is_diagnostic_day:
            gates["common_n"] = {**evidence, **config_data["benchmark"]["common_n"]}
            gates["study_window"] = evidence
        if is_diagnostic_day:
            gates["diagnostic_day_authorized"] = evidence
            gates["size_export_review"] = evidence
        gates["paid_acceptance"] = {**evidence, "status": "completed",
            "config_id": campaign.config_id, "mode": "acceptance",
            "providers": list(campaign.selected_providers)}
    aws_cost = ROOT / "tests/fixtures/aws/cost-inputs/aws-ssm-automation-public-2026-10-22.json"
    gcp_handoff = tmp_path / "gcp-handoff.json"
    gcp_total = sum(float(value["amount_usd"])
        for value in costs.payload["provider_run_envelopes"]["gcp"]["components"].values())
    gcp_handoff.write_text(json.dumps({"source_path": costs.source_path, "source_sha256": cost_hash,
        "prices_checked_on": "2026-10-22", "estimated_total_usd": gcp_total,
        "units": {"two-vm-run": 1}}))
    azure_cost = tmp_path / "azure-expiry-cost.json"
    azure_cost.write_text(json.dumps({"source_id": "synthetic-azure-prices", "date": "2026-10-22",
        "sha256": "a" * 64, "automation_rate_per_job_minute": 0.002, "job_minutes_bound": 20}))
    pub = tmp_path / "test.pub"
    pub.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE9F4huSqss0wx3n6f5+WJiEpePM3oXTbuOzBLRR5NCc test\n")
    identities = {}
    for provider in ("aws", "azure", "gcp"):
        identity = tmp_path / f"{provider}-id"
        identity.write_text(f"synthetic {provider} identity for offline runtime only\n")
        identity.chmod(0o600)
        identities[provider] = identity
    bootstrap = BootstrapConfig(sources=[
        SourceBuild(tool="sockperf", source_url="https://example.invalid/sockperf.tar.gz",
            source_reference="sockperf-src", archive_name="sockperf.tar.gz", source_sha256="a"*64),
        SourceBuild(tool="iperf3", source_url="https://example.invalid/iperf3.tar.gz",
            source_reference="iperf3-src", archive_name="iperf3.tar.gz", source_sha256="b"*64)],
        remote_root="/var/lib/cnb", workspace="/var/lib/cnb/work", install_prefix="/var/lib/cnb/install")
    runtime = {"initialized_campaign_manifest": parent_path,
        "ledger": {"path": tmp_path / "ledger.json", "scope_id": "three-provider", "initialize_if_missing": True},
        "allowance": WorkflowAllowance(5, 5, 300, 60,
            960 if is_calibration else 230 if is_short_study else 86412 if is_diagnostic_day else 80, 5, 10, 10,
            "synthetic-test-policy", {"aws": 360, "azure": 360, "gcp": 30}),
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
                "authentication_reference": str(identities["aws"])}, "bootstrap_template": ROOT / "terraform/aws/pair/main.tf",
                "terraform_directory": ROOT / "terraform/aws", "sources": {"expiry_cost_path": aws_cost,
                    "primary_review_path": None}},
            "azure": {"options": {"subscription_id": "00000000-0000-0000-0000-000000000001",
                "tenant_id": "00000000-0000-0000-0000-000000000002", "controller_cidr": "203.0.113.4/32",
                "selected_image": "Canonical:0001-com-ubuntu-server-noble:24_04-lts:2026.10.01",
                "ssh_public_key_path": pub, "authentication_reference": str(identities["azure"]),
                "poll_seconds": 15, "command_timeout_seconds": 60},
                "bootstrap_template": ROOT / "terraform/azure/pair/main.tf", "terraform_directory": ROOT / "terraform/azure",
                "sources": {"expiry_cost_path": azure_cost, "primary_review_path": None}},
            "gcp": {"options": {"project_id": "benchmark-project1", "controller_cidr": "203.0.113.4/32",
                "images": {"europe-west3": "projects/ubuntu-os-cloud/global/images/ubuntu-2404-lts-amd64-20261001",
                    "europe-west2": "projects/ubuntu-os-cloud/global/images/ubuntu-2404-lts-amd64-20261001"},
                "public_key_path": pub, "authentication_reference": str(identities["gcp"])},
                "bootstrap_template": ROOT / "terraform/gcp/pair/main.tf", "terraform_directory": ROOT / "terraform/gcp",
                "sources": {"workflow_cost_handoff_path": gcp_handoff,
                    "support_cost_path": ROOT / "tests/fixtures/gcp/zero-support-cost-2026-10-23.json",
                    "primary_review_path": None}}}}
    clock = Clock()
    f03_status_started = threading.Event()
    provider_status_started = {provider: threading.Event() for provider in children}
    provider_cancel_called = {provider: threading.Event() for provider in children}
    if successful_protection:
        import cloud_network_benchmark.lifecycle_adapters as adapters
        from cloud_network_benchmark.bootstrap import BootstrapConfig as NativeBootstrapConfig, SourceBuild as NativeSourceBuild
        from cloud_network_benchmark.contracts.common import VmRole
        from tests.integration.test_acquisition_producer import _build
        from tests.integration.test_f09_aws_runtime_factory import FakePreparedAwsRunner
        from tests.integration.test_azure_provider_flow import CleanupRunner
        native_builds = [_build(tool, VmRole.VM_A, f"f02r-vm_a-{tool}-build")
            for tool in ("sockperf", "iperf3")]
        runtime["access"]["bootstrap"] = NativeBootstrapConfig(sources=[NativeSourceBuild(tool=build.tool,
            source_url=f"https://example.invalid/{build.tool}.tar.gz", source_reference=build.source_reference,
            archive_name=f"{build.tool}.tar.gz", source_sha256=build.source_sha256,
            upstream_commit=build.upstream_commit, observation_patch_path=build.patch_path,
            observation_patch_sha256=build.patch_sha256) for build in native_builds],
            remote_root="/var/lib/cnb", workspace="/var/lib/cnb/work", install_prefix="/var/lib/cnb/install")
        class ThreeProviderDispatch:
            def __init__(self, root):
                self.clock = clock
                self.aws = FakePreparedAwsRunner()
                self.gcp = GcpSourceRunner()
                self.gcp.clock = clock
                self.azure = None
                self.remote = {"aws": self.aws, "gcp": FakePreparedAwsRunner(),
                    "azure": FakePreparedAwsRunner()}
                self.identity_provider = {str(value): name for name, value in identities.items()}
                for name, responder in self.remote.items():
                    responder.full_native = True
                    responder.clock = clock
                    responder.late_tail = True
                    mode_value = getattr(campaign.mode, "value", campaign.mode)
                    responder.protocol_elapsed = (12 * 60 if mode_value == "calibration" else
                        86_407 if mode_value == "diagnostic_day" else 60)
                    responder.workspace_roots = {role: root / name / role for role in ("vm_a", "vm_b")}
                    responder.native_root = responder.workspace_roots["vm_a"]
                self.requests = []
                self.actions = []
                self.ledger = None
                self.reservation_id = None
                self.claim_snapshot_at_first_apply = None
                self.aws_pair_destroyed = False
            def _assert_claim_barrier(self):
                if self.claim_snapshot_at_first_apply is not None:
                    return
                state = self.ledger.inspect()
                if self.reservation_id is None:
                    assert len(state["reservations"]) == 1, state["reservations"].keys()
                    self.reservation_id = next(iter(state["reservations"]))
                claims = state["reservations"][self.reservation_id]["creation_claims"]
                assert set(claims) == {"aws", "azure", "gcp"}, claims
                self.claim_snapshot_at_first_apply = tuple(sorted(claims))
            def run(self, request):
                self.requests.append(request)
                self.actions.append(request.action_id)
                if request.action_id.startswith("aws-"):
                    if request.action_id == "aws-pair-apply": self._assert_claim_barrier()
                    if request.action_id == "aws-pair-destroy": self.aws_pair_destroyed = True
                    if request.action_id.startswith(("aws-expiry-read-terminal-", "aws-expiry-confirm-cancel-")):
                        region = request.argv[request.argv.index("--region") + 1]
                        execution_id = request.argv[request.argv.index("--automation-execution-id") + 1]
                        trigger = self.aws.expiry_request.expires_at.value.strftime("%Y-%m-%dT%H:%M:%SZ")
                        parameters = {"AutomationAssumeRole": [f"arn:aws:iam::123456789012:role/expiry-{region}"],
                            "ExpiryTriggerUtc": [trigger], "RunId": [self.aws.run_id],
                            "DeploymentId": [self.aws.deployment_id], "Region": [region]}
                        now = clock.utc_now()
                        response = {"AutomationExecution": {"AutomationExecutionId": execution_id,
                            "DocumentName": f"expiry-{region}", "DocumentVersion": "1",
                            "AutomationExecutionStatus": "Cancelled", "Parameters": parameters,
                            "StepExecutionsTruncated": False, "StepExecutions": []}}
                        return CommandResult(action_id=request.action_id, outcome="succeeded", started_at=now,
                            finished_at=now, duration_seconds=0, exit_code=0, stdout=json.dumps(response), stderr="")
                    if request.action_id.startswith(("aws-expiry-document-absence-", "aws-expiry-role-absence-")):
                        code = "InvalidDocument" if "document-absence" in request.action_id else "NoSuchEntity"
                        now = clock.utc_now()
                        return CommandResult(action_id=request.action_id, outcome="failed", started_at=now,
                            finished_at=now, duration_seconds=0, exit_code=254, stdout="",
                            stderr=f"An error occurred ({code}) when calling the offline absence check")
                    if self.aws_pair_destroyed and any(token.startswith("describe-") for token in request.argv):
                        operation = next((token for token in request.argv if token.startswith("describe-")), None)
                        collections = {"describe-instances": "Reservations", "describe-volumes": "Volumes",
                            "describe-network-interfaces": "NetworkInterfaces", "describe-security-groups": "SecurityGroups",
                            "describe-subnets": "Subnets", "describe-route-tables": "RouteTables",
                            "describe-internet-gateways": "InternetGateways", "describe-vpcs": "Vpcs",
                            "describe-vpc-peering-connections": "VpcPeeringConnections",
                            "describe-placement-groups": "PlacementGroups", "describe-key-pairs": "KeyPairs",
                            "describe-addresses": "Addresses"}
                        if operation in collections:
                            now = clock.utc_now()
                            return CommandResult(action_id=request.action_id, outcome="succeeded", started_at=now,
                                finished_at=now, duration_seconds=0, exit_code=0,
                                stdout=json.dumps({collections[operation]: []}), stderr="")
                    return self.aws.run(request)
                if request.action_id.startswith("gcp-"):
                    if request.action_id == "gcp-pair-apply": self._assert_claim_barrier()
                    return self.gcp.run(request)
                if request.action_type == "pair_apply":
                    self._assert_claim_barrier()
                if request.action_id.startswith("azure-"):
                    assert self.azure is not None
                    self.azure.now = clock.utc_now()
                    if request.action_type == "vm_image_observation":
                        self.azure.requests.append(request)
                        publisher, offer, sku, version = self.azure.o.selected_image.split(":")
                        payload = {"storageProfile": {"imageReference": {"publisher": publisher,
                            "offer": offer, "sku": sku, "exactVersion": version}}}
                        now = clock.utc_now()
                        return CommandResult(action_id=request.action_id, outcome="succeeded", started_at=now,
                            finished_at=now, duration_seconds=0, exit_code=0, stdout=json.dumps(payload), stderr="")
                    outcome = self.azure.run(request)
                    if request.action_type == "pair_destroy" and outcome.outcome == "succeeded":
                        self.azure.account_present = False
                        self.azure.present_children.clear()
                    return outcome
                identity = request.argv[request.argv.index("-i") + 1] if "-i" in request.argv else None
                provider = self.identity_provider.get(identity)
                if provider is None:
                    raise AssertionError(f"remote action lacks a known provider identity: {request.action_id} {request.argv}")
                responder = self.remote[provider]
                if request.action_type == "scp":
                    return responder.run(request)
                if request.action_id.startswith("f09-guest-status-") and interrupt_controller:
                    f03_status_started.set()
                    provider_status_started[provider].set()
                    assert provider_cancel_called[provider].wait(10), (
                        "production cancel callback did not reach the active F03 status poll", provider)
                    now = clock.utc_now()
                    return CommandResult(action_id=request.action_id, outcome="succeeded",
                        started_at=now, finished_at=now, duration_seconds=0, exit_code=0,
                        stdout="running\n", stderr="")
                if request.action_id.startswith(("f02r-", "f09-")):
                    return responder.run(request)
                now = clock.utc_now()
                raise AssertionError(f"unexpected remote action {request.action_id} {request.argv}")
        runner = ThreeProviderDispatch(tmp_path / "remote")
    else:
        runner = FailedExternalCommands()
    built = build_runtime(campaign=campaign, parent=parent, children=children, costs=costs,
        runtime=runtime, controller_session_id="synthetic-session", runner=runner,
        clock=clock, private_root=tmp_path / "private", repository_root=ROOT)
    original_workflow_factory = built["workflow_factory"]
    workflow_factory = original_workflow_factory
    captured_workflows = {}
    if interrupt_controller:
        def capture_workflows(reservation):
            hooks = original_workflow_factory(reservation)
            from dataclasses import replace
            observed_hooks = {}
            for provider_name, hooks_for_provider in hooks.items():
                original_cancel = hooks_for_provider.cancel
                def observed_cancel(provider_name=provider_name, callback=original_cancel):
                    value = callback()
                    provider_cancel_called[provider_name].set()
                    return value
                observed_hooks[provider_name] = replace(hooks_for_provider, cancel=observed_cancel)
            captured_workflows.update(observed_hooks)
            return observed_hooks
        workflow_factory = capture_workflows
    if getattr(campaign.mode, "value", campaign.mode) == "calibration":
        from cloud_network_benchmark.admission import BudgetLedger, _validate_paid_acceptance_evidence
        for gate in ("calibration_authorized", "common_n", "study_window", "diagnostic_day_authorized", "size_export_review", "paid_acceptance"):
            if gate in built["plan"].prerequisites:
                BudgetLedger._validate_gate_evidence(built["plan"].prerequisites[gate])
        _validate_paid_acceptance_evidence(built["plan"].prerequisites["paid_acceptance"], campaign)
    if interrupt_controller:
        import cloud_network_benchmark.orchestration as orchestration_module
        original_as_completed = orchestration_module.as_completed
        interrupted_once = {"done": False}
        def inject_controller_interrupt(futures):
            if not interrupted_once["done"]:
                interrupted_once["done"] = True
                assert all(event.wait(15) for event in provider_status_started.values()), (
                    "all actual provider F03 stages did not enter their bounded status polls")
                raise KeyboardInterrupt("injected controller interrupt while actual F03 was active")
            return original_as_completed(futures)
        monkeypatch.setattr(orchestration_module, "as_completed", inject_controller_interrupt)
    if successful_protection:
        runtime["sleep"] = clock.sleep
        runner.ledger = built["ledger"]
        if interrupt_f11_cleanup:
            original_reconcile = built["ledger"].reconcile
            interrupted = {"done": False}
            def fail_gcp_cleanup_once(event):
                if (event.provider == "gcp" and event.cleanup_verified_absent
                        and not interrupted["done"]):
                    interrupted["done"] = True
                    raise OSError("injected F11 persistence interruption after receipt")
                return original_reconcile(event)
            monkeypatch.setattr(built["ledger"], "reconcile", fail_gcp_cleanup_once)
        original_aws_hooks, original_gcp_hooks, original_azure_hooks = adapters.aws_hooks, adapters.gcp_hooks, adapters.azure_hooks
        original_prepare_remote_pair = adapters.prepare_remote_pair
        prepare_errors = []
        observed = {}
        def observe_aws(driver, deployment, expiry_request, **kwargs):
            runner.aws.run_id = deployment.run_id
            runner.aws.deployment_id = deployment.deployment_id
            runner.aws.expiry_request = expiry_request
            observed["aws"] = (driver, deployment, expiry_request)
            return original_aws_hooks(driver, deployment, expiry_request, **kwargs)
        def observe_gcp(driver, deployment, expiry_request, **kwargs):
            runner.gcp.driver, runner.gcp.deployment, runner.gcp.expiry_request = driver, deployment, expiry_request
            observed["gcp"] = (driver, deployment, expiry_request)
            return original_gcp_hooks(driver, deployment, expiry_request, **kwargs)
        def observe_azure(deployment, expiry_request, **kwargs):
            runner.azure = CleanupRunner(deployment, kwargs["options"], clock.utc_now())
            observed["azure"] = (deployment, expiry_request, kwargs["options"])
            return original_azure_hooks(deployment, expiry_request, **kwargs)
        def observe_prepare_remote_pair(connection, **kwargs):
            provider = runner.identity_provider.get(connection.vm_a.authentication_reference)
            assert provider is not None, connection.vm_a.authentication_reference
            responder = runner.remote[provider]
            responder.private_a, responder.private_b = connection.private_ipv4_a, connection.private_ipv4_b
            responder.public_a, responder.public_b = connection.vm_a.host, connection.vm_b.host
            responder.native_base_time = clock.utc_now()
            responder.run_id, responder.deployment_id = connection.run_id, connection.deployment_id
            responder.connection = connection
            try:
                return original_prepare_remote_pair(connection, **kwargs)
            except Exception as exc:
                prepare_errors.append((type(exc).__name__, str(exc)))
                raise
        monkeypatch.setattr(adapters, "aws_hooks", observe_aws)
        monkeypatch.setattr(adapters, "gcp_hooks", observe_gcp)
        monkeypatch.setattr(adapters, "azure_hooks", observe_azure)
        monkeypatch.setattr(adapters, "prepare_remote_pair", observe_prepare_remote_pair)
    result = run_lifecycle(ledger=built["ledger"], reservation_request=built["request"],
        admission_plan=built["plan"], allowance=built["allowance"],
        workflow_factory=workflow_factory, clock=clock,
        recovery_path=built["recovery_path"], recovery_factory=built["recovery_factory"])
    if interrupt_controller:
        assert interrupted_once["done"] and f03_status_started.is_set()
        assert result.admission_status == "reserved" and result.completed is False
        assert set(captured_workflows) == {"aws", "azure", "gcp"}
        assert all(provider_cancel_called[provider].is_set() for provider in children)
        assert set(result.provider_results) == {"aws", "azure", "gcp"}
        for provider_name, provider_result in result.provider_results.items():
            assert provider_result.interrupted is True, (provider_name, provider_result)
            assert provider_result.values.get("cancel", {}).get("requested") is True
            assert provider_result.values.get("cancel", {}).get("verified_stopped") is False
            assert provider_result.values.get("cleanup") is not None
            assert provider_result.cleanup_state == "succeeded", (provider_name, provider_result.values.get("cleanup"))
            actions = runner.remote[provider_name].actions
            assert any(action.startswith("f09-guest-cancel-") for action in actions), (provider_name, actions)
            assert len([action for action in actions if action.startswith("f09-diagnostics-stop-")]) == 2
            assert len([action for action in actions if action.startswith("f09-listener-stop-")]) >= 1
        reservation_id = result.reservation_evidence.reservation_id.value
        ledger_state = built["ledger"].inspect()
        reservation_record = ledger_state["reservations"][reservation_id]
        assert reservation_record["provider_cleanup"] == {p: True for p in children}
        assert all(item["final"] is False for item in reservation_record["provider_billing"].values())
        assert reservation_record.get("released_usd", "0") == "0"
        return
    if interrupt_f11_cleanup:
        assert interrupted["done"] is True
        assert result.admission_status == "reserved"
        assert set(result.provider_results) == {"aws", "azure", "gcp"}
        for provider_name, provider_result in result.provider_results.items():
            assert provider_result.values["cleanup"] is not None, (provider_name, provider_result.values)
            assert provider_result.values["cleanup"].cleanup_state == "succeeded", provider_result.values["cleanup"]
        reservation_id = result.reservation_evidence.reservation_id.value
        reservation_record = built["ledger"].inspect()["reservations"][reservation_id]
        assert reservation_record["provider_cleanup"] == {"aws": True, "azure": True, "gcp": False}
        assert reservation_record["provider_billing"]["gcp"]["final"] is False
        assert reservation_record.get("released_usd", "0") == "0"
        recovery = json.loads(built["recovery_path"].read_text(encoding="utf-8"))
        gcp_row = next(row for row in recovery["providers"] if row["provider"] == "gcp")
        assert gcp_row["cleanup_open"] is True
        receipt_candidates = list(Path(gcp_row["cleanup_settings_reference"]).parent.glob(
            f"{reservation_id}-gcp-cleanup-*.json"))
        assert len(receipt_candidates) == 1
        receipt = json.loads(receipt_candidates[0].read_text(encoding="utf-8"))
        assert receipt["absence_proved"] is True and receipt["absent_resource_ids"]
        class NoUnexpectedCleanupCommand:
            def run(self, request):
                raise AssertionError(f"receipt recovery must reconcile without provider commands: {request.action_id}")
        cleanup_status = lifecycle_main(["cleanup", "--recovery", str(built["recovery_path"]),
            "--controller-session", "receipt-recovery-session", "--format", "json"],
            cleanup_runner=NoUnexpectedCleanupCommand(), cleanup_utc_now=clock.utc_now,
            cleanup_monotonic_now=clock.monotonic)
        assert cleanup_status == 0
        recovered = json.loads(built["recovery_path"].read_text(encoding="utf-8"))
        assert all(not row["cleanup_open"] and not row["expiry_registered"]
            and not row["creation_may_have_started"] for row in recovered["providers"])
        recovered_record = built["ledger"].inspect()["reservations"][reservation_id]
        assert recovered_record["provider_cleanup"] == {p: True for p in children}
        assert all(item["final"] is False for item in recovered_record["provider_billing"].values())
        assert recovered_record.get("released_usd", "0") == "0"
        return
    if successful_protection:
        assert result.admission_status == "reserved", (result.admission_reasons, result.primary_failure, built["plan"].child_identities.keys(), built["plan"].namespaces.keys(), built["plan"].allowance.provider_leads_seconds.keys(), campaign.selected_providers)
        assert set(result.provider_results) == {"aws", "azure", "gcp"}
        assert set(observed) == {"aws", "azure", "gcp"}
        assert set(runner.claim_snapshot_at_first_apply or ()) == {"aws", "azure", "gcp"}, (result.primary_failure, {p: (r.primary_failure, r.values.keys()) for p, r in result.provider_results.items()}, runner.actions)
        for provider_name, provider_result in result.provider_results.items():
            assert provider_result.values["protect"].state == "registered", (provider_name, provider_result.values)
            assert provider_result.values["claim"].eligible is True, (provider_name, provider_result.values["claim"])
            assert provider_result.values["claim"].creation_granted is True, (provider_name, provider_result.values["claim"])
            assert provider_result.values["create"] is not None, (provider_name, provider_result.primary_failure, provider_result.values)
            created = provider_result.values["create"]
            if provider_name == "azure":
                created = created.deployment
            assert created is not None, (provider_name, provider_result.values["create"])
            assert created.run_id == children[provider_name].run_id
            assert {str(created.vm_a.role), str(created.vm_b.role)} == {"vm_a", "vm_b"}
            assert provider_result.values["cleanup"] is not None, (provider_name, provider_result.values)
            assert provider_result.values["cleanup"].cleanup_state == "succeeded", (provider_name, provider_result.values["cleanup"].cleanup_failure, provider_result.values["cleanup"].secondary_failures, provider_result.values["cleanup"].inventory.value, [(r.action_id, r.argv) for r in runner.requests if any("describe-" in str(token) for token in r.argv)])
            assert provider_result.values["prepare"].succeeded is True, (provider_name, provider_result.values["prepare"], prepare_errors)
            for stage in ("stabilize", "measure", "close", "retrieve"):
                outcome = provider_result.values.get(stage)
                if stage == "retrieve" and outcome is not None and not outcome.succeeded:
                    retrieval = outcome.evidence
                    details = {
                        "complete": getattr(retrieval, "complete", None),
                        "failure": getattr(retrieval, "failure", None),
                        "failures": getattr(retrieval, "failures", [])[:8],
                        "failure_count": len(getattr(retrieval, "failures", [])),
                        "attempt_count": len(getattr(retrieval, "attempts", [])),
                        "action_count": len(getattr(retrieval, "actions", [])),
                    }
                    assert outcome.succeeded is True, (provider_name, stage, details,
                        provider_result.primary_failure, provider_result.secondary_failures)
                assert outcome is not None and outcome.succeeded is True, (provider_name, stage,
                    provider_result.primary_failure, provider_result.secondary_failures)
        assert all(observed[p][1].run_id == children[p].run_id for p in children)
        reservation_id = result.reservation_evidence.reservation_id.value
        ledger_state = built["ledger"].inspect()
        reservation_record = ledger_state["reservations"][reservation_id]
        assert reservation_record["provider_cleanup"] == {p: True for p in children}, {
            provider: (item.cleanup_state, item.primary_failure, item.secondary_failures)
            for provider, item in result.provider_results.items()}
        assert reservation_id not in ledger_state["cleanup_open"]
        assert reservation_id not in ledger_state["cleanup_blocked"]
        assert all(item["final"] is False for item in reservation_record["provider_billing"].values())
        assert reservation_record.get("released_usd", "0") == "0"
        recovery = json.loads(built["recovery_path"].read_text(encoding="utf-8"))
        assert all(not row["cleanup_open"] and not row["expiry_registered"]
            and not row["creation_may_have_started"] for row in recovery["providers"])
        for row in recovery["providers"]:
            receipt_candidates = list(Path(row["cleanup_settings_reference"]).parent.glob(
                f"{reservation_id}-{row['provider']}-cleanup-*.json"))
            assert len(receipt_candidates) == 1, (row["provider"], receipt_candidates)
            receipt = json.loads(receipt_candidates[0].read_text(encoding="utf-8"))
            assert receipt["absence_proved"] is True and receipt["absent_resource_ids"]
            from cloud_network_benchmark.orchestration import update_provider_recovery
            update_provider_recovery(built["recovery_path"], row["provider"], "create", "started")
        class NoUnexpectedCleanupCommand:
            def run(self, request):
                raise AssertionError(f"already-reconciled cleanup must not invoke provider commands: {request.action_id}")
        cleanup_status = lifecycle_main(["cleanup", "--recovery", str(built["recovery_path"]),
            "--controller-session", "cleanup-restart-session", "--format", "json"],
            cleanup_runner=NoUnexpectedCleanupCommand(), cleanup_utc_now=clock.utc_now,
            cleanup_monotonic_now=clock.monotonic)
        assert cleanup_status == 0
        recovered = json.loads(built["recovery_path"].read_text(encoding="utf-8"))
        assert all(not row["cleanup_open"] and not row["expiry_registered"]
            and not row["creation_may_have_started"] for row in recovered["providers"])
        from cloud_network_benchmark.lifecycle_projection import finalize_production_batch
        from cloud_network_benchmark.contracts.validation import ValidatorInput
        from cloud_network_benchmark.validation import evaluate_comparison, validate_bundle
        evaluated_at = max(clock.utc_now(), datetime.now(timezone.utc)) + timedelta(minutes=1)
        finalized = finalize_production_batch(parent_path=parent_path, children=children,
            parent=parent, lifecycle=result, repository_root=ROOT, evaluated_at=evaluated_at)
        assert set(finalized) == {"aws", "azure", "gcp"}
        projected_parent = json.loads(parent_path.read_text(encoding="utf-8"))
        saved_inputs, roots = {}, {}
        for provider in children:
            ref = next(row for row in projected_parent["child_runs"] if row["provider"] == provider)
            roots[provider] = Path(ref["result_path"]).resolve()
            saved_inputs[provider] = ValidatorInput.model_validate_json(
                (roots[provider] / "validation" / "input.json").read_text(encoding="utf-8"))
        preliminary = {provider: validate_bundle(value, evaluated_at=evaluated_at)
            for provider, value in saved_inputs.items()}
        replays = evaluate_comparison(list(saved_inputs.values()), list(preliminary.values()))
        for provider, saved in saved_inputs.items():
            replay = replays[saved.identity.run_id]
            assert replay.model_dump(mode="json") == finalized[provider][0].model_dump(mode="json")
            assert replay.validation_status.data_validity == "valid", [
                (finding.code, finding.severity, finding.message) for finding in replay.findings]
            if is_diagnostic_day:
                metrics = replay.metrics.value
                day = next(unit for unit in metrics["units"] if unit["kind"] == "continuous_day")
                chunks = [unit for unit in metrics["units"] if unit["kind"] == "probe_chunk"]
                assert day["tcp"]["measured_seconds"]["value"] == 86_400
                assert day["coverage"]["complete"] is True
                assert day["coverage"]["active_probe_ratio"]["value"] >= .99
                assert len(chunks) + 1 == len(saved.measurement_units)
                assert all(unit["coverage"]["complete"] is True for unit in chunks)
                last_chunk_id = chunks[-1]["unit_id"]
                last_chunk_command = next(command for command in saved.commands
                    if command.unit_id == last_chunk_id and command.producer == "vm_a")
                assert last_chunk_command.result["outcome"] == "succeeded"
            if is_calibration:
                assert replay.validation_status.comparison_eligibility == "ineligible"
            assert not [finding for finding in replay.findings if finding.severity == "error"
                and finding.code != "comparison.ineligible"], replay.findings
            assert (roots[provider] / "checksums.sha256").is_file()
        if is_calibration:
            from cloud_network_benchmark.calibration import select_common_n
            recommendation = select_common_n(list(preliminary.values()))
            assert recommendation.unavailable_reason is None, recommendation.unavailable_reason
            assert recommendation.common_n.value in {2, 4, 8}
            assert recommendation.source_sha256.value and recommendation.plateau_found.value in {True, False}
        return
    assert result.admission_status == "reserved", (result.admission_reasons, result.primary_failure, built["plan"].child_identities.keys(), built["plan"].namespaces.keys(), built["plan"].allowance.provider_leads_seconds.keys(), campaign.selected_providers)
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
    assert all(action == "gcp-inventory-all-zones" or action.startswith("azure-")
        for action in cleanup_runner.actions), cleanup_runner.actions
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
    assert all(action == "gcp-inventory-all-zones" or action.startswith("azure-")
        for action in tamper_runner.actions), tamper_runner.actions


def test_parent_aggregate_keeps_high_water_for_overlapping_peer_events(tmp_path):
    from cloud_network_benchmark.contracts.common import ExecutionState
    from cloud_network_benchmark.manifests import initialize_campaign, update_child_execution
    from cloud_network_benchmark.lifecycle_runtime import load_initialized_campaign

    config = ROOT / "configs/tests/exp-903-three-provider-smoke.yaml"
    snapshots = []
    for name, order in (("late-first", ("aws", "azure")), ("early-first", ("azure", "aws"))):
        initialized = initialize_campaign(config, ROOT, tmp_path / name,
            clock=lambda: NOW, token_source=lambda: name)
        parent_path = Path(initialized.parent_manifest_path)
        parent, children = load_initialized_campaign(parent_path, repository_root=ROOT,
            config_path=config)
        event_times = {"aws": NOW + timedelta(seconds=2), "azure": NOW + timedelta(seconds=1)}
        references = {str(ref.provider): ref for ref in parent.child_runs}
        for provider in order:
            update_child_execution((ROOT / references[provider].manifest_path).resolve(),
                ExecutionState.PROVISION_READY, event_times[provider], parent_manifest_path=parent_path)
        current_parent = type(parent).model_validate_json(parent_path.read_text(encoding="utf-8"))
        assert current_parent.updated_at == NOW + timedelta(seconds=2)
        assert [event.occurred_at for event in current_parent.lifecycle_events] == sorted(
            event.occurred_at for event in current_parent.lifecycle_events)
        snapshots.append((current_parent.aggregate_state, current_parent.updated_at))
        with pytest.raises(ValueError, match="earlier"):
            update_child_execution((ROOT / references["aws"].manifest_path).resolve(),
                ExecutionState.RUNNING, NOW + timedelta(seconds=1), parent_manifest_path=parent_path)
    assert snapshots[0] == snapshots[1]
