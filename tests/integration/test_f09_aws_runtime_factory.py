"""Exercise the default F09 AWS factory with only fake command boundaries."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from cloud_network_benchmark.admission import load_cost_inputs
from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.contracts.common import Provider
from cloud_network_benchmark.contracts.execution import CommandResult
from cloud_network_benchmark.lifecycle_runtime import build_runtime
from cloud_network_benchmark.orchestration import run_lifecycle

ROOT = Path(__file__).parents[2]
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self):
        self.elapsed = 0.0

    def utc_now(self):
        return NOW + timedelta(seconds=self.elapsed)

    def monotonic(self):
        return 100.0 + self.elapsed

    def sleep(self, seconds):
        self.elapsed += seconds


class FakeAwsRunner:
    """Replies locally to every command; no process or cloud is contacted."""
    def __init__(self):
        self.actions = []
        self.run_id = ""
        self.deployment_id = ""

    def run(self, request):
        action = request.action_id
        self.actions.append(action)
        stdout = "{}"
        if action.startswith("aws-safety-apply-"):
            state = next(value.removeprefix("-state=") for value in request.argv if value.startswith("-state="))
            Path(state).write_text("fake-state")
        if action.startswith("aws-safety-output-"):
            region = action.removeprefix("aws-safety-output-")
            stdout = json.dumps({key: {"value": value} for key, value in {
                "document_name": f"expiry-{region}", "document_version": "1",
                "document_hash": "a" * 64,
                "role_arn": f"arn:aws:iam::123456789012:role/expiry-{region}",
            }.items()})
        elif action == "aws-pair-output":
            stdout = json.dumps({"deployment": {"value": {"vms": {
                "vm_a": {"instance_id": "i-vm-a", "private_ipv4": "10.60.1.10", "public_ipv4": "8.8.8.8",
                    "region": "eu-central-1", "zone": "eu-central-1a", "zone_id": "use1-az2",
                    "image_id": "ami-0123456789abcdef0", "image_owner_id": "099720109477",
                    "architecture": "x86_64", "shape": "m7i.xlarge", "boot_volume_id": "vol-a",
                    "placement_group": None, "placement_group_strategy": None},
                "vm_b": {"instance_id": "i-vm-b", "private_ipv4": "10.60.1.11", "public_ipv4": "1.1.1.1",
                    "region": "eu-central-1", "zone": "eu-central-1a", "zone_id": "use1-az2",
                    "image_id": "ami-0123456789abcdef0", "image_owner_id": "099720109477",
                    "architecture": "x86_64", "shape": "m7i.xlarge", "boot_volume_id": "vol-b",
                    "placement_group": None, "placement_group_strategy": None}},
                "network": {}, "placement": {}}}})
        elif action.startswith("aws-get-caller-identity"):
            stdout = json.dumps({"Account": "123456789012"})
        elif action.startswith("aws-describe-instances"):
            instance_id = request.argv[request.argv.index("--instance-ids") + 1]
            role = "vm_a" if instance_id == "i-vm-a" else "vm_b"
            private_ip = "10.60.1.10" if role == "vm_a" else "10.60.1.11"
            public_ip = "8.8.8.8" if role == "vm_a" else "1.1.1.1"
            stdout = json.dumps({"Reservations": [{"Instances": [{"InstanceId": instance_id,
                "State": {"Name": "running"}, "Tags": [{"Key": "cnb_run_id", "Value": self.run_id},
                    {"Key": "cnb_deployment_id", "Value": self.deployment_id}, {"Key": "cnb_role", "Value": role}],
                "InstanceType": "m7i.xlarge", "ImageId": "ami-0123456789abcdef0",
                "PrivateIpAddress": private_ip, "PublicIpAddress": public_ip,
                "Placement": {"AvailabilityZone": "eu-central-1a", "GroupName": None},
                "NetworkInterfaces": [{"InterfaceType": "interface"}],
                "BlockDeviceMappings": [{"Ebs": {"VolumeId": "vol-a" if role == "vm_a" else "vol-b",
                    "DeleteOnTermination": True}}], "LaunchTime": "2026-10-22T11:50:00Z"}]}]})
        elif action.startswith("aws-describe-instance-topology"):
            stdout = json.dumps({"Instances": [{"ZoneId": "use1-az2"}]})
        elif action.startswith("aws-describe-images"):
            stdout = json.dumps({"Images": [{"ImageId": "ami-0123456789abcdef0", "OwnerId": "099720109477",
                "Architecture": "x86_64", "VirtualizationType": "hvm", "RootDeviceType": "ebs",
                "State": "available", "Name": "ubuntu-noble-24.04-amd64-server-20261001", "EnaSupport": True}]})
        elif action.startswith("aws-describe-volumes"):
            stdout = json.dumps({"Volumes": [{"VolumeId": "vol-a", "Size": 32, "VolumeType": "gp3"}]})
        elif action.startswith("aws-describe-instance-types"):
            stdout = json.dumps({"InstanceTypes": [{"InstanceType": "m7i.xlarge",
                "ProcessorInfo": {"SupportedArchitectures": ["x86_64"]},
                "VCpuInfo": {"DefaultVCpus": 4}, "MemoryInfo": {"SizeInMiB": 16384}}]})
        elif action.startswith("aws-get-console-output"):
            instance_id = request.argv[request.argv.index("--instance-id") + 1]
            stdout = json.dumps({"InstanceId": instance_id, "Timestamp": "2026-10-22T11:51:00Z",
                "Output": "CNB_HOSTKEY_BOOT_ID=12345678-1234-4234-8234-123456789abc\nCNB_ED25519_HOST_FINGERPRINT=SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAA\n"})
        elif action.startswith("aws-host-keyscan-"):
            stdout = "fake-host ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n"
        elif action.startswith("aws-host-key-fingerprint-"):
            stdout = "256 SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAA fake-host (ED25519)\n"
        elif action.startswith("aws-expiry-start-"):
            region = action.removeprefix("aws-expiry-start-")
            stdout = json.dumps({"AutomationExecutionId": f"exec-{region}"})
        elif action.startswith("aws-expiry-confirm-"):
            region = action.removeprefix("aws-expiry-confirm-")
            parameters = {"AutomationAssumeRole": [f"arn:aws:iam::123456789012:role/expiry-{region}"],
                "ExpiryTriggerUtc": ["2026-10-22T12:14:00Z"], "RunId": [self.run_id],
                "DeploymentId": [self.deployment_id], "Region": [region]}
            stdout = json.dumps({"AutomationExecution": {"AutomationExecutionId": f"exec-{region}",
                "DocumentName": f"expiry-{region}", "DocumentVersion": "1",
                "AutomationExecutionStatus": "InProgress", "CurrentAction": "aws:sleep",
                "CurrentStepName": "waitUntilExpiry", "Parameters": parameters,
                "StepExecutionsTruncated": False, "StepExecutions": [{"StepName": "waitUntilExpiry",
                    "Action": "aws:sleep", "StepStatus": "InProgress",
                    "Inputs": {"Timestamp": "2026-10-22T12:14:00Z"}}]}})
        result = CommandResult(action_id=action, outcome="succeeded", started_at=NOW,
            finished_at=NOW, duration_seconds=0, exit_code=0, stdout=stdout, stderr="")
        return result


def _cost_source(path: Path, run_id: str, cost_input_id: str) -> None:
    payload = json.loads((ROOT / "tests/fixtures/f11/cost-inputs.json").read_text())
    payload.update(cost_input_id=cost_input_id, prices_checked_on="2026-10-22",
        acquired_on="2026-10-22", source_sha256="a" * 64,
        funding={"primary": "250", "replacements": "0", "tax": "0", "uncertainty": "0"},
        refresh_required_before_paid_execution=False)
    region = "eu-central-1"
    components = {}
    for name in ("compute", "disks", "addresses", "traffic_export", "tax", "uncertainty"):
        components[name] = {"amount_usd": "0.1", "quantity": "1", "unit": "unit",
            "inclusion_rule": "synthetic test source", "funding_bucket": "primary"}
    components["expiry_cleanup_support"] = {"amount_usd": "0.0139", "quantity": "1",
        "unit": "region-session", "inclusion_rule": "synthetic test source", "funding_bucket": "primary"}
    payload["provider_run_envelopes"] = {"aws": {"envelope_id": "fixture-aws", "provider": "aws",
        "run_id": run_id, "priced_on": "2026-10-22", "source_kind": "provider-price-input",
        "regions": [region], "unit_basis": "one-two-vm-provider-run", "components": components}}
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _runtime(tmp_path, repository_root, cost_path, initialized_parent, cost_digest):
    aws_fixture = repository_root / "tests/fixtures/aws/cost-inputs/aws-ssm-automation-public-2026-10-22.json"
    fixture_hash = hashlib.sha256(aws_fixture.read_bytes()).hexdigest()
    refs = {}
    for gate in ("f10_offline", "paid_authorization", "quota_review"):
        evidence_source = tmp_path / f"{gate}.md"
        evidence_source.write_text(f"synthetic test evidence for {gate}\n", encoding="utf-8")
        evidence = {"evidence_id": f"fixture-{gate}", "source_path": evidence_source.name,
            "source_sha256": hashlib.sha256(evidence_source.read_bytes()).hexdigest()}
        evidence_path = tmp_path / f"{gate}.json"
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
        refs[gate] = evidence_path
    key = tmp_path / "test.pub"
    key.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA test\n")
    identity = tmp_path / "fake-private-key"
    identity.write_text("synthetic private identity for fake runner only\n")
    identity.chmod(0o600)
    from cloud_network_benchmark.admission import WorkflowAllowance
    from cloud_network_benchmark.access import TimeoutPolicy
    from cloud_network_benchmark.bootstrap import BootstrapConfig, SourceBuild
    from cloud_network_benchmark.readiness import ReadinessConfig
    bootstrap = BootstrapConfig(sources=[
        SourceBuild(tool="sockperf", source_url="https://example.invalid/sockperf.tar.gz",
            source_reference="sockperf-src", archive_name="sockperf.tar.gz", source_sha256="a"*64),
        SourceBuild(tool="iperf3", source_url="https://example.invalid/iperf3.tar.gz",
            source_reference="iperf3-src", archive_name="iperf3.tar.gz", source_sha256="b"*64)],
        remote_root="/var/lib/cnb", workspace="/var/lib/cnb/work",
        install_prefix="/var/lib/cnb/install")
    access = {"timeouts": TimeoutPolicy(ssh_seconds=5, command_seconds=10,
            cloud_init_seconds=10, private_path_seconds=5, server_seconds=5, transfer_seconds=10),
        "readiness": ReadinessConfig(poll_interval_seconds=.01, attempt_ceiling=1),
        "bootstrap": bootstrap, "guest_remote_root": "/var/lib/cnb/runtime",
        "diagnostic_interfaces": {"aws": {"vm_a":"ens5", "vm_b":"ens5"}}}
    return {"initialized_campaign_manifest": initialized_parent,
        "ledger": {"path": tmp_path / "ledger.json", "scope_id": "test-aws", "initialize_if_missing": True},
        "allowance": WorkflowAllowance(10, 30, 30, 60, 80, 5, 10, 10,
            "synthetic-test-policy", {"aws": 360}), "copy_retry_seconds": 1,
        "approval_refs": refs,
        "approval_evidence": {gate: json.loads(path.read_text()) for gate, path in refs.items()},
        "access": access,
        "providers": {"aws": {"options": {"account_id": "123456789012", "controller_cidr": "203.0.113.4/32",
            "ami_ids": {"eu-central-1": "ami-0123456789abcdef0"}, "public_key_path": key,
            "authentication_reference": str(identity)},
            "bootstrap_template": repository_root / "terraform/aws/pair/main.tf",
            "terraform_directory": repository_root / "terraform/aws",
            "sources": {"expiry_cost_path": aws_fixture, "primary_review_path": None}}}}


def test_actual_f11_reserve_then_aws_protect_claim_provision_and_cleanup_fake_only(tmp_path, repository_root):
    from cloud_network_benchmark.manifests import initialize_campaign
    from cloud_network_benchmark.lifecycle_runtime import load_initialized_campaign

    config = repository_root / "configs/tests/exp-900-single-provider.yaml"
    initialized = initialize_campaign(config, repository_root, tmp_path / "results",
        clock=lambda: NOW, token_source=lambda: "campaign")
    parent_path = Path(initialized.parent_manifest_path)
    parent, children = load_initialized_campaign(parent_path, repository_root=repository_root,
        config_path=config)
    child = children["aws"]
    runner = FakeAwsRunner()
    runner.run_id, runner.deployment_id = child.run_id, child.deployment_id
    cost_path = tmp_path / "refreshed-costs.json"
    campaign, _ = resolve_campaign(config, repository_root)
    _cost_source(cost_path, child.run_id, campaign.admission.cost_input_id)
    costs = load_cost_inputs(cost_path)
    runtime = _runtime(tmp_path, repository_root, cost_path, parent_path, costs.payload_sha256)
    clock = FakeClock()
    built = build_runtime(campaign=campaign, parent=parent,
        children={"aws": child}, costs=costs, runtime=runtime,
        controller_session_id="fake-session", runner=runner, clock=clock,
        private_root=tmp_path / "private", repository_root=repository_root)
    result = run_lifecycle(ledger=built["ledger"], reservation_request=built["request"],
        admission_plan=built["plan"], allowance=built["allowance"],
        workflow_factory=built["workflow_factory"], clock=clock,
        recovery_path=built["recovery_path"], recovery_factory=built["recovery_factory"])
    assert result.admission_status == "reserved", result.admission_reasons
    provider_result = result.provider_results["aws"]
    assert provider_result.values["protect"].state == "registered"
    assert provider_result.values["claim"].eligible is True, provider_result.values
    assert provider_result.values["claim"].creation_granted is True, provider_result.values
    assert provider_result.primary_failure == "aws prepare failed (adapter reported prepare unsuccessful)"
    assert provider_result.values["create"].run_id == child.run_id
    assert provider_result.creation_may_have_started is True
    assert provider_result.values["cleanup"] is not None
    assert provider_result.values["prepare"].stage == "prepare"
    assert provider_result.values["prepare"].succeeded is False
    assert provider_result.values["close"].stage == "close"
    assert provider_result.values["retrieve"].stage == "retrieve"
    assert any(action.startswith("f02r-") for action in runner.actions)
    assert "aws-expiry-start-eu-central-1" in runner.actions
    assert "aws-pair-apply" in runner.actions
    assert "aws-pair-output" in runner.actions
    assert "aws-pair-destroy" in runner.actions
    from cloud_network_benchmark.lifecycle_projection import finalize_production_batch
    from cloud_network_benchmark.contracts.validation import ValidatorInput
    from cloud_network_benchmark.validation import validate_bundle
    finalized = finalize_production_batch(parent_path=parent_path, children={"aws": child},
        parent=parent, lifecycle=result, repository_root=repository_root, evaluated_at=NOW + timedelta(minutes=1))
    result_ref = next(row for row in parent.child_runs if str(row.provider) == "aws")
    result_root = (repository_root / result_ref.result_path).resolve()
    result_file = result_root / "validation" / "input.json"
    saved = ValidatorInput.model_validate_json(result_file.read_text(encoding="utf-8"))
    replay = validate_bundle(saved, evaluated_at=NOW + timedelta(minutes=1))
    assert replay.model_dump(mode="json") == finalized["aws"][0].model_dump(mode="json")
    assert (result_file.parent.parent / "checksums.sha256").is_file()


def test_default_factory_stabilizes_for_full_minimum_with_fake_clock(tmp_path, repository_root, monkeypatch):
    from cloud_network_benchmark.manifests import initialize_campaign
    from cloud_network_benchmark.lifecycle_runtime import load_initialized_campaign
    from cloud_network_benchmark.orchestration import ProviderStageOutcome
    import cloud_network_benchmark.lifecycle_adapters as adapters

    config = repository_root / "configs/tests/exp-900-single-provider.yaml"
    initialized = initialize_campaign(config, repository_root, tmp_path / "results",
        clock=lambda: NOW, token_source=lambda: "campaign")
    parent_path = Path(initialized.parent_manifest_path)
    parent, children = load_initialized_campaign(parent_path, repository_root=repository_root,
        config_path=config)
    child = children["aws"]
    runner = FakeAwsRunner()
    runner.run_id, runner.deployment_id = child.run_id, child.deployment_id
    cost_path = tmp_path / "refreshed-costs.json"
    campaign, _ = resolve_campaign(config, repository_root)
    _cost_source(cost_path, child.run_id, campaign.admission.cost_input_id)
    costs = load_cost_inputs(cost_path)
    runtime = _runtime(tmp_path, repository_root, cost_path, parent_path, costs.payload_sha256)
    clock = FakeClock()
    runtime["sleep"] = clock.sleep
    monkeypatch.setattr(adapters, "prepare_remote_pair", lambda *args, **kwargs:
        ProviderStageOutcome("prepare", True, {"bootstrap": object(), "guest_runtime": object()}))
    built = build_runtime(campaign=campaign, parent=parent, children={"aws": child},
        costs=costs, runtime=runtime, controller_session_id="fake-session", runner=runner,
        clock=clock, private_root=tmp_path / "private", repository_root=repository_root)
    result = run_lifecycle(ledger=built["ledger"], reservation_request=built["request"],
        admission_plan=built["plan"], allowance=built["allowance"],
        workflow_factory=built["workflow_factory"], clock=clock,
        recovery_path=built["recovery_path"], recovery_factory=built["recovery_factory"])
    provider_result = result.provider_results["aws"]
    assert provider_result.values["stabilize"].succeeded is True
    assert provider_result.values["stabilize"].evidence["elapsed_seconds"] >= 60
    assert clock.elapsed >= 60
    assert provider_result.values["cleanup"] is not None


def test_post_cleanup_batch_projects_all_selected_providers_before_sealing(tmp_path, repository_root):
    from cloud_network_benchmark.manifests import initialize_campaign
    from cloud_network_benchmark.contracts.admission import ReservationEvidence
    from cloud_network_benchmark.contracts.common import Evidence, StringEvidence
    from cloud_network_benchmark.orchestration import LifecycleResult, ProviderLifecycleResult
    from cloud_network_benchmark.lifecycle_projection import finalize_production_batch
    from cloud_network_benchmark.lifecycle_runtime import load_initialized_campaign
    from cloud_network_benchmark.contracts.validation import ValidatorInput
    from cloud_network_benchmark.validation import validate_bundle

    config = repository_root / "configs/tests/exp-903-three-provider-smoke.yaml"
    initialized = initialize_campaign(config, repository_root, tmp_path / "results",
        clock=lambda: NOW, token_source=lambda: "batch")
    parent_path = Path(initialized.parent_manifest_path)
    parent, children = load_initialized_campaign(parent_path, repository_root=repository_root,
        config_path=config)
    reservation = ReservationEvidence(schema_version=2, campaign_id=parent.campaign_id,
        reservation_id=Evidence[str](value="synthetic-batch-reservation"), state="reserved",
        reserved_cost_usd=Evidence[float](value=1), cap_started_at=Evidence[datetime](value=NOW),
        expires_at=Evidence[datetime](value=NOW + timedelta(hours=1)),
        cap_start_policy_id=StringEvidence(value="synthetic-policy"), source_path="synthetic-test.json",
        source_sha256="a" * 64)
    provider_results = {provider: ProviderLifecycleResult(provider=provider,
        execution_state="failed", primary_failure=f"synthetic {provider} stage failure")
        for provider in children}
    lifecycle = LifecycleResult(provider_results, completed=False,
        reservation_evidence=reservation, started_at=NOW, finished_at=NOW + timedelta(minutes=1))
    evaluated_at = NOW + timedelta(minutes=2)
    finalized = finalize_production_batch(parent_path=parent_path, children=children,
        parent=parent, lifecycle=lifecycle, repository_root=repository_root, evaluated_at=evaluated_at)
    assert set(finalized) == {"aws", "azure", "gcp"}
    saved_inputs = {}
    for provider, child in children.items():
        ref = next(row for row in parent.child_runs if str(row.provider) == provider)
        root = (repository_root / ref.result_path).resolve()
        saved = ValidatorInput.model_validate_json((root / "validation/input.json").read_text())
        saved_inputs[provider] = saved
        assert (root / "checksums.sha256").is_file()
    preliminary = {provider: validate_bundle(value, evaluated_at=evaluated_at)
        for provider, value in saved_inputs.items()}
    from cloud_network_benchmark.validation import evaluate_comparison
    compared = evaluate_comparison(list(saved_inputs.values()), list(preliminary.values()))
    for provider, value in saved_inputs.items():
        replay = compared[value.identity.run_id]
        assert replay.model_dump(mode="json") == finalized[provider][0].model_dump(mode="json")
    assert Path(initialized.parent_manifest_path).is_file()
