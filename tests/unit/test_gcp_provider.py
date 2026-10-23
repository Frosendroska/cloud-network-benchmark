from __future__ import annotations

import pytest
from pydantic import ValidationError

from cloud_network_benchmark.providers.gcp import GcpCleanupResult, GcpRuntimeSettings
from cloud_network_benchmark.contracts.common import CleanupState, Evidence, StructuredEvidence

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA test"


def settings(tmp_path, **changes):
    data = {
        "project_id": "cnb-test-12345",
        "controller_cidr": "203.0.113.4/32",
        "images": {"europe-west3": "projects/ubuntu-os-cloud/global/images/ubuntu-2404-lts-amd64-v20261001", "europe-west2": "projects/ubuntu-os-cloud/global/images/ubuntu-2404-lts-amd64-v20261001"},
        "public_key": KEY,
        "authentication_reference": "secret-manager-reference:attempt-key",
        "private_state_root": tmp_path / "state",
        "cost_input_id": "dated-register-2026-10-23",
        "cost_input_sha256": "a" * 64,
        "cost_source_path": "tracking/run-register-2026-10-23.csv",
        "complete_workflow_cost_handoff": StructuredEvidence(value={"source_path": "tracking/run-register-2026-10-23.csv", "source_sha256": "a" * 64, "prices_checked_on": "2026-10-23", "estimated_total_usd": 9.5, "units": {"boot_disk_gib": 32, "public_ipv4": "hour", "transfer": "GiB"}}),
        "support_cost_evidence": StructuredEvidence(value={"id": "gcp-template-placement-support-2026-10-23", "prices_checked_on": "2026-10-23", "source_path": "tests/fixtures/gcp/zero-support-cost-source.md", "source_sha256": "ec0caa3e3b553d14e1e4d60bdd0bd325e6cf8eaf583da3cd9c3884ce310bbcaf", "template_support_cost_usd": 0, "placement_policy_support_cost_usd": 0, "rationale": "No separate per-template or placement-policy support fee is charged."}),
        "complete_workflow_remaining_seconds": 600.0,
    }
    data.update(changes)
    return GcpRuntimeSettings(**data)


def test_runtime_requires_controller_ipv4_host_route(tmp_path):
    with pytest.raises(ValidationError):
        settings(tmp_path, controller_cidr="203.0.113.0/24")


def test_runtime_rejects_private_key_material(tmp_path):
    with pytest.raises(ValidationError):
        settings(tmp_path, public_key="-----BEGIN OPENSSH PRIVATE KEY-----secret")


def test_runtime_requires_complete_sourced_cost_evidence(tmp_path):
    with pytest.raises(ValidationError):
        settings(tmp_path, complete_workflow_cost_handoff=StructuredEvidence(value={"source_path": "x", "source_sha256": "a" * 64}))


@pytest.mark.parametrize("estimate", [0, -1, float("inf"), True])
def test_runtime_rejects_invalid_complete_cost_total(tmp_path, estimate):
    handoff = {"source_path": "tracking/run-register-2026-10-23.csv", "source_sha256": "a" * 64, "prices_checked_on": "2026-10-23", "estimated_total_usd": estimate, "units": {"compute": "hour"}}
    with pytest.raises(ValidationError):
        settings(tmp_path, complete_workflow_cost_handoff=StructuredEvidence(value=handoff))


def test_runtime_rejects_nonfinite_complete_workflow_estimate(tmp_path):
    with pytest.raises(ValidationError):
        settings(tmp_path, complete_workflow_remaining_seconds=float("inf"))


def test_runtime_rejects_malformed_dated_support_cost_source(tmp_path):
    data=settings(tmp_path).model_dump()
    data["complete_workflow_cost_handoff"]=StructuredEvidence(value={"source_path":"tracking/run-register.csv","source_sha256":"a"*64,"prices_checked_on":"23-10-2026","estimated_total_usd":9.5,"units":{"compute":"hour"}})
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        GcpRuntimeSettings(**data)


def test_attempt_namespaces_are_isolated_and_reject_traversal(tmp_path):
    from types import SimpleNamespace
    from cloud_network_benchmark.providers.gcp import GcpProviderDriver, GcpProviderError

    driver = GcpProviderDriver(object(), settings(tmp_path))
    first = driver._attempt_dir(SimpleNamespace(run_id="run-1", deployment_id="deploy-1"))
    second = driver._attempt_dir(SimpleNamespace(run_id="run-1", deployment_id="deploy-2"))
    assert first != second
    assert first.parent.parent == second.parent.parent
    with pytest.raises(GcpProviderError, match="safe single path component"):
        driver._attempt_dir(SimpleNamespace(run_id="../outside", deployment_id="deploy-3"))


def test_hostkey_write_rejects_symlinked_attempt_namespace_before_provider_reads(tmp_path):
    from cloud_network_benchmark.providers.gcp import GcpProviderDriver, GcpProviderError
    root = tmp_path / "state"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "namespace-vm").symlink_to(outside, target_is_directory=True)
    driver = GcpProviderDriver(object(), settings(tmp_path))
    with pytest.raises(GcpProviderError, match="known_hosts path"):
        driver._trusted_hostkey("namespace-vm-a", "europe-west3-a", {"id": "1"})


def test_foreign_or_symlinked_attempt_journals_fail_closed(tmp_path):
    from types import SimpleNamespace
    import json
    from cloud_network_benchmark.providers.gcp import GcpProviderDriver, GcpProviderError
    driver=GcpProviderDriver(object(),settings(tmp_path))
    deployment=SimpleNamespace(run_id="run-1",deployment_id="deploy-1")
    attempt=driver._attempt_dir(deployment)
    path=attempt/"attempt.json"
    path.write_text(json.dumps({"run_id":"foreign-run","deployment_id":"deploy-1"}))
    with pytest.raises(GcpProviderError,match="journal identity mismatch"):
        driver._load(attempt,deployment)
    path.unlink()
    outside=tmp_path/"foreign.json"
    outside.write_text("{}")
    path.symlink_to(outside)
    with pytest.raises(GcpProviderError,match="journal cannot be a symlink"):
        driver._load(attempt,deployment)


def test_command_deadline_and_journal_store_redacted_durable_views(tmp_path):
    from datetime import datetime, timezone
    import json
    import time
    from pathlib import Path
    from cloud_network_benchmark.contracts.execution import CommandRequest, CommandResult
    from cloud_network_benchmark.providers.gcp import GcpProviderDriver, GcpProviderError

    class Runner:
        def __init__(self): self.request = None
        def run(self, request):
            self.request = request
            now = datetime.now(timezone.utc)
            return CommandResult(action_id=request.action_id, outcome="succeeded", started_at=now, finished_at=now, duration_seconds=0.0, exit_code=0, stdout="sensitive-provider-response", stderr="")

    runner = Runner()
    driver = GcpProviderDriver(runner, settings(tmp_path, command_timeout_seconds=5))
    attempt = Path(tmp_path) / "state" / "attempt"
    attempt.mkdir(parents=True)
    driver._active_attempt = attempt
    driver._save(attempt, {"run_id": "run-1", "deployment_id": "deploy-1", "commands": []})
    driver._run("test-action", ["tool", "arg"], attempt, timeout=12)
    assert runner.request.timeout_seconds == 5
    driver._save(attempt, {"run_id": "run-1", "deployment_id": "deploy-1", "commands": [], "later_state": "saved"})
    journal = json.loads((attempt / "attempt.json").read_text())
    assert journal["later_state"] == "saved"
    assert len(journal["commands"]) == 1
    assert journal["commands"][0]["request"]["argv"] == ["tool", "arg"]
    assert "sensitive-provider-response" not in json.dumps(journal)
    assert "sha256=" in journal["commands"][0]["result"]["stdout"]
    driver._deadline = time.monotonic() - 1
    with pytest.raises(GcpProviderError, match="deadline elapsed"):
        driver._run("expired-action", ["tool"], attempt)


def test_shared_driver_keeps_concurrent_attempt_deadlines_and_journals_isolated(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timezone
    from threading import Barrier
    import json
    from cloud_network_benchmark.contracts.execution import CommandResult
    from cloud_network_benchmark.providers.gcp import GcpProviderDriver
    barrier=Barrier(2)
    class Runner:
        def run(self,request):
            barrier.wait(timeout=5)
            now=datetime.now(timezone.utc)
            return CommandResult(action_id=request.action_id,outcome="succeeded",started_at=now,finished_at=now,duration_seconds=0.0,exit_code=0,stdout=request.action_id,stderr="")
    driver=GcpProviderDriver(Runner(),settings(tmp_path))
    attempts=[]
    for index in (1,2):
        path=tmp_path/"state"/f"attempt-{index}"
        path.mkdir(parents=True)
        driver._save(path,{"run_id":f"run-{index}","deployment_id":f"deploy-{index}","commands":[]})
        attempts.append(path)
    def invoke(index):
        driver._active_attempt=attempts[index-1]
        driver._deadline=None
        driver._run(f"action-{index}",["tool",str(index)],attempts[index-1])
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(invoke,(1,2)))
    for index,path in enumerate(attempts,1):
        journal=json.loads((path/"attempt.json").read_text())
        assert len(journal["commands"])==1
        assert journal["commands"][0]["request"]["action_id"]==f"action-{index}"
        assert f"action-{3-index}" not in json.dumps(journal)


def test_cleanup_success_requires_observed_empty_inventory():
    with pytest.raises(ValidationError):
        GcpCleanupResult(cleanup_state=CleanupState.SUCCEEDED, inventory=Evidence[dict](value={}), expiry=Evidence(unavailable_reason="not registered"))


def test_cleanup_failure_keeps_failure_evidence_separate():
    with pytest.raises(ValidationError):
        GcpCleanupResult(cleanup_state=CleanupState.FAILED, inventory=Evidence[dict](value={}), expiry=Evidence(unavailable_reason="not registered"))


def test_guest_hostkey_requires_authenticated_identity_and_writes_private_known_hosts(tmp_path):
    from datetime import datetime, timezone
    import base64, struct
    import json
    import os
    from cloud_network_benchmark.providers.gcp import GcpProviderDriver
    from cloud_network_benchmark.contracts.execution import CommandResult

    class Runner:
        def __init__(self, changed=False):
            self.calls = []
            self.changed = changed

        def run(self, request):
            self.calls.append(request)
            started = datetime.now(timezone.utc)
            if "get-guest-attributes" in request.argv:
                blob = base64.b64encode(struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + bytes([1 if self.changed else 0]) * 32).decode()
                stdout = json.dumps({"queryValue": {"items": [{"namespace": "hostkeys", "key": "ssh-ed25519", "value": blob}]}})
            else:
                stdout = json.dumps({"id": "123456789", "name": "attempt-a", "zone": "https://compute.googleapis.com/compute/v1/projects/cnb-test-12345/zones/europe-west3-a", "selfLink": "https://compute.googleapis.com/compute/v1/projects/cnb-test-12345/zones/europe-west3-a/instances/attempt-a", "networkInterfaces": [{"networkIP": "10.80.1.10", "accessConfigs": [{"natIP": "8.8.8.8"}]}]})
            return CommandResult(action_id=request.action_id, outcome="succeeded", started_at=started, finished_at=started, duration_seconds=0.0, exit_code=0, stdout=stdout, stderr="")

    runner = Runner()
    driver = GcpProviderDriver(runner, settings(tmp_path))
    initial = {"id": "123456789", "name": "attempt-a", "zone": "https://compute.googleapis.com/compute/v1/projects/cnb-test-12345/zones/europe-west3-a", "selfLink": "https://compute.googleapis.com/compute/v1/projects/cnb-test-12345/zones/europe-west3-a/instances/attempt-a", "networkInterfaces": [{"networkIP": "10.80.1.10", "accessConfigs": [{"natIP": "8.8.8.8"}]}]}
    key = driver._trusted_hostkey("attempt-a", "europe-west3-a", initial)
    hosts = tmp_path / "state" / "attempt" / "known_hosts"
    assert key.startswith("ssh-ed25519 ")
    # The file is private and all reads used the project/zone-scoped command runner.
    hostfile = next((tmp_path / "state").rglob("known_hosts"))
    assert os.stat(hostfile).st_mode & 0o777 == 0o600
    assert len(runner.calls) == 3
    assert all(request.argv[0].endswith("gcloud") for request in runner.calls)


def test_guest_hostkey_rejects_changed_key_for_same_private_address(tmp_path):
    from datetime import datetime, timezone
    import base64, struct
    import json
    from cloud_network_benchmark.providers.gcp import GcpProviderDriver, GcpProviderError
    from cloud_network_benchmark.contracts.execution import CommandResult

    class Runner:
        def __init__(self): self.guest_reads = 0
        def run(self, request):
            started = datetime.now(timezone.utc)
            if "get-guest-attributes" in request.argv:
                self.guest_reads += 1
                raw = bytes([self.guest_reads]) * 32
                value = base64.b64encode(struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + raw).decode()
                stdout = json.dumps({"queryValue": {"items": [{"namespace": "hostkeys", "key": "ssh-ed25519", "value": value}]}})
            else:
                stdout = json.dumps({"id": "123456789", "name": "attempt-a", "zone": "https://compute.googleapis.com/compute/v1/projects/cnb-test-12345/zones/europe-west3-a", "selfLink": "https://compute.googleapis.com/compute/v1/projects/cnb-test-12345/zones/europe-west3-a/instances/attempt-a", "networkInterfaces": [{"networkIP": "10.80.1.10", "accessConfigs": [{"natIP": "8.8.8.8"}]}]})
            return CommandResult(action_id=request.action_id, outcome="succeeded", started_at=started, finished_at=started, duration_seconds=0.0, exit_code=0, stdout=stdout, stderr="")

    driver = GcpProviderDriver(Runner(), settings(tmp_path))
    first = {"id": "123456789", "name": "attempt-a", "zone": "https://compute.googleapis.com/compute/v1/projects/cnb-test-12345/zones/europe-west3-a", "selfLink": "https://compute.googleapis.com/compute/v1/projects/cnb-test-12345/zones/europe-west3-a/instances/attempt-a", "networkInterfaces": [{"networkIP": "10.80.1.10", "accessConfigs": [{"natIP": "8.8.8.8"}]}]}
    driver._trusted_hostkey("attempt-a", "europe-west3-a", first)
    with pytest.raises(GcpProviderError, match="host-key changed"):
        driver._trusted_hostkey("attempt-a", "europe-west3-a", first)


def test_guest_hostkey_rejects_malformed_blob_and_changed_management_address(tmp_path):
    from datetime import datetime, timezone
    import json
    from cloud_network_benchmark.providers.gcp import GcpProviderDriver, GcpProviderError
    from cloud_network_benchmark.contracts.execution import CommandResult
    from cloud_network_benchmark.contracts.common import StructuredEvidence
    import base64, struct
    blob=base64.b64encode(struct.pack(">I",11)+b"ssh-ed25519"+struct.pack(">I",32)+b"k"*32).decode()
    base={"id":"123456789","name":"attempt-a","zone":"https://compute.googleapis.com/compute/v1/projects/cnb-test-12345/zones/europe-west3-a","selfLink":"https://compute.googleapis.com/compute/v1/projects/cnb-test-12345/zones/europe-west3-a/instances/attempt-a","networkInterfaces":[{"networkIP":"10.80.1.10","accessConfigs":[{"natIP":"8.8.8.8"}]}]}
    class Runner:
        def __init__(self, malformed=False, changed=False): self.calls=0; self.malformed=malformed; self.changed=changed
        def run(self,request):
            self.calls+=1; now=datetime.now(timezone.utc)
            if "get-guest-attributes" in request.argv:
                value="not-base64!" if self.malformed else blob
                payload={"queryValue":{"items":[{"namespace":"hostkeys","key":"ssh-ed25519","value":value}]}}
            else:
                payload=dict(base)
                if self.changed and request.action_id=="gcp-hostkey-instance-after":
                    payload["networkInterfaces"]=[{"networkIP":"10.80.1.10","accessConfigs":[{"natIP":"1.1.1.1"}]}]
            return CommandResult(action_id=request.action_id,outcome="succeeded",started_at=now,finished_at=now,duration_seconds=0,exit_code=0,stdout=json.dumps(payload),stderr="")
    bad=GcpProviderDriver(Runner(malformed=True),settings(tmp_path/"bad"))
    with pytest.raises(GcpProviderError,match="no valid OpenSSH"):
        bad._trusted_hostkey("attempt-a","europe-west3-a",base)
    changed=GcpProviderDriver(Runner(changed=True),settings(tmp_path/"changed"))
    with pytest.raises(GcpProviderError,match="identity changed after"):
        changed._trusted_hostkey("attempt-a","europe-west3-a",base)
