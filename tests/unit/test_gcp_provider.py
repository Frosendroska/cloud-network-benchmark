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


def test_runtime_rejects_nonfinite_complete_workflow_estimate(tmp_path):
    with pytest.raises(ValidationError):
        settings(tmp_path, complete_workflow_remaining_seconds=float("inf"))


def test_cleanup_success_requires_observed_empty_inventory():
    with pytest.raises(ValidationError):
        GcpCleanupResult(cleanup_state=CleanupState.SUCCEEDED, inventory=Evidence[dict](value={}), expiry=Evidence(unavailable_reason="not registered"))


def test_cleanup_failure_keeps_failure_evidence_separate():
    with pytest.raises(ValidationError):
        GcpCleanupResult(cleanup_state=CleanupState.FAILED, inventory=Evidence[dict](value={}), expiry=Evidence(unavailable_reason="not registered"))


def test_guest_hostkey_requires_authenticated_identity_and_writes_private_known_hosts(tmp_path):
    from datetime import datetime, timezone
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
                key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA host"
                if self.changed:
                    key = "ssh-ed25519 AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyA changed"
                stdout = json.dumps({"queryValue": {"items": [{"namespace": "hostkeys", "key": "0", "value": key}]}})
            else:
                stdout = json.dumps({"id": "123456789", "zone": "https://compute.googleapis.com/compute/v1/projects/cnb-test/zones/europe-west3-a", "networkInterfaces": [{"networkIP": "10.80.1.10"}]})
            return CommandResult(action_id=request.action_id, outcome="succeeded", started_at=started, finished_at=started, duration_seconds=0.0, exit_code=0, stdout=stdout, stderr="")

    runner = Runner()
    driver = GcpProviderDriver(runner, settings(tmp_path))
    key = driver._trusted_hostkey("attempt-a", "europe-west3-a", {"id": "123456789"})
    hosts = tmp_path / "state" / "attempt" / "known_hosts"
    assert key.startswith("ssh-ed25519 ")
    # The file is private and all reads used the project/zone-scoped command runner.
    hostfile = next((tmp_path / "state").rglob("known_hosts"))
    assert os.stat(hostfile).st_mode & 0o777 == 0o600
    assert len(runner.calls) == 3
    assert all(request.argv[0].endswith("gcloud") for request in runner.calls)


def test_guest_hostkey_rejects_changed_key_for_same_private_address(tmp_path):
    from datetime import datetime, timezone
    import json
    from cloud_network_benchmark.providers.gcp import GcpProviderDriver, GcpProviderError
    from cloud_network_benchmark.contracts.execution import CommandResult

    class Runner:
        def __init__(self): self.guest_reads = 0
        def run(self, request):
            started = datetime.now(timezone.utc)
            if "get-guest-attributes" in request.argv:
                self.guest_reads += 1
                value = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA host" if self.guest_reads == 1 else "ssh-ed25519 AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyA host"
                stdout = json.dumps({"queryValue": {"items": [{"namespace": "hostkeys", "key": "0", "value": value}]}})
            else:
                stdout = json.dumps({"id": "123456789", "zone": "https://compute.googleapis.com/compute/v1/projects/cnb-test/zones/europe-west3-a", "networkInterfaces": [{"networkIP": "10.80.1.10"}]})
            return CommandResult(action_id=request.action_id, outcome="succeeded", started_at=started, finished_at=started, duration_seconds=0.0, exit_code=0, stdout=stdout, stderr="")

    driver = GcpProviderDriver(Runner(), settings(tmp_path))
    first = {"id": "123456789", "zone": "https://compute.googleapis.com/compute/v1/projects/cnb-test/zones/europe-west3-a", "networkInterfaces": [{"networkIP": "10.80.1.10"}]}
    driver._trusted_hostkey("attempt-a", "europe-west3-a", first)
    with pytest.raises(GcpProviderError, match="host-key changed"):
        driver._trusted_hostkey("attempt-a", "europe-west3-a", first)
