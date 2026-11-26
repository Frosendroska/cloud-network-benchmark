"""Offline Azure leaf CommandRunner for composed lifecycle qualification.

This fixture replaces only provider command I/O. Azure hooks, validators,
workflows and lifecycle orchestration remain production code.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping

from cloud_network_benchmark.contracts.execution import CommandResult
from tests.integration.test_azure_provider_flow import CleanupRunner


@dataclass(frozen=True)
class AzureRunContext:
    provider: str
    run_id: str
    deployment_id: str
    deployment: object
    options: object
    clock: Callable[[], datetime]
    runtime_roots: Mapping[str, Path]
    control_root: Path | None = None


class AzureLeafProtocol:
    """Strict wrapper around the existing request-level Azure response fixture."""

    def __init__(self, context: AzureRunContext, *, bounded_payloads: Mapping[str, bytes] | None = None):
        if (context.provider != "azure" or context.run_id != context.deployment.run_id
                or context.deployment_id != context.deployment.deployment_id):
            raise ValueError("Azure leaf context identity does not match deployment")
        if not context.options.subscription_id or not context.options.tenant_id:
            raise ValueError("Azure leaf options require subscription and tenant identity")
        self.context = context
        self._delegate = CleanupRunner(context.deployment, context.options, context.clock())
        self._payloads = dict(bounded_payloads or {})
        self.requests = self._delegate.requests
        self._destroyed_attempt_groups: set[str] = set()
        self._pair_destroyed = False
        self._expiry_destroyed = False

    def _check_request(self, request, *, allow_repository_control: bool = False) -> None:
        cwd = Path(request.cwd).resolve()
        roots = tuple(Path(root).resolve() for root in self.context.runtime_roots.values())
        private_cwd = bool(roots) and any(cwd == root or root in cwd.parents for root in roots)
        if not private_cwd and not allow_repository_control:
            raise ValueError("Azure command cwd is outside this run's runtime roots")
        if request.action_type.startswith(("aws_", "gcp_")):
            raise ValueError("non-Azure command rejected")

    def run(self, request) -> CommandResult:
        executable = Path(request.argv[0]).name
        roots = tuple(Path(root).resolve() for root in self.context.runtime_roots.values())
        control = False
        if (self.context.control_root is not None
                and Path(request.cwd).resolve() == Path(self.context.control_root).resolve()
                and request.action_id.startswith("azure-")):
            if executable == "az":
                control = True
            elif executable == "terraform" and not any(token in request.argv for token in ("plan", "show", "apply")):
                chdir = next((Path(token.split("=", 1)[1]).resolve(strict=False)
                    for token in request.argv if token.startswith("-chdir=")), None)
                trusted_sources = {Path(__file__).resolve().parents[2] / "terraform/azure/pair",
                    Path(__file__).resolve().parents[2] / "terraform/azure/expiry"}
                private_state = any(token.startswith("-state=")
                    and Path(token.split("=", 1)[1]).resolve(strict=False).is_relative_to(root)
                    for token in request.argv for root in roots)
                backend_state = any(token.startswith("-backend-config=path=")
                    and Path(token.split("=", 2)[2]).resolve(strict=False).is_relative_to(root)
                    for token in request.argv for root in roots)
                control = chdir in trusted_sources and (private_state or backend_state)
        self._check_request(request, allow_repository_control=control)
        result = self._delegate.run(request)
        # The established responder is pinned to an earlier Germany fixture.
        # Rebind only external image and VM locality responses to this actual
        # deployment and the exact marketplace lookup request.
        region = str(self.context.deployment.vm_a.region)
        if request.action_type == "image_preflight" and result.outcome == "succeeded":
            import json
            payload = json.loads(result.stdout)
            if (request.argv[0:3] != ["az", "vm", "image"]
                    or "--location" not in request.argv
                    or request.argv[request.argv.index("--location") + 1] != region):
                raise ValueError("Azure marketplace lookup is not bound to actual deployment region")
            payload["location"] = region
            payload["id"] = payload["id"].replace("/locations/germanywestcentral/",
                f"/locations/{region}/")
            result = result.model_copy(update={"stdout": json.dumps(payload, sort_keys=True)})
        elif request.action_type == "pair_output" and result.outcome == "succeeded":
            import json
            payload = json.loads(result.stdout)
            for role, vm in (("a", self.context.deployment.vm_a),
                    ("b", self.context.deployment.vm_b)):
                payload["vm_" + role]["value"]["region"] = str(vm.region)
                payload["vm_" + role]["value"]["zone"] = str(vm.zone)
                from cloud_network_benchmark.contracts.common import Scenario
                expected_ppg = (self.context.deployment.placement.name
                    if self.context.deployment.scenario == Scenario.PLACEMENT_OPTIMIZATION else None)
                payload["vm_" + role]["value"]["ppg"] = expected_ppg
            result = result.model_copy(update={"stdout": json.dumps(payload, sort_keys=True)})
        elif request.action_type == "vm_image_observation" and result.outcome == "succeeded":
            # Bind the observed Marketplace image fields to the selected image
            # in the actual deployment options. Shared gallery references keep
            # their ID-based response from the underlying source fixture.
            import json
            selected = str(self.context.options.selected_image)
            if not selected.startswith("/"):
                image = json.loads(result.stdout)
                reference = image["storageProfile"]["imageReference"]
                parts = selected.split(":")
                if len(parts) != 4:
                    raise ValueError("selected Azure Marketplace image is not a four-part identity")
                reference.clear()
                reference.update(dict(zip(("publisher", "offer", "sku", "exactVersion"), parts)))
                result = result.model_copy(update={"stdout": json.dumps(image, sort_keys=True)})
        if request.action_type in {"pair_destroy", "expiry_destroy"} and result.outcome == "succeeded":
            from cloud_network_benchmark.azure_provider import _slug
            self._destroyed_attempt_groups.add(_slug(self.context.deployment))
            if request.action_type == "pair_destroy":
                self._pair_destroyed = True
            else:
                self._expiry_destroyed = True
        # The established CleanupRunner returns blank stdout for this one
        # profile-level boolean read. Azure CLI prints JSON true/false; model
        # the exact attempt group as absent only after a source-shaped destroy
        # of the owned pair or expiry group, preserving the production parser.
        if request.action_type == "profile_absence_group":
            from cloud_network_benchmark.azure_provider import _slug
            expected_group = _slug(self.context.deployment)
            argv = request.argv
            if (expected_group not in self._destroyed_attempt_groups
                    or argv != ["az", "group", "exists", "--subscription",
                        self.context.options.subscription_id, "--name", expected_group,
                        "--output", "json"]):
                raise ValueError("profile absence read does not match a successfully destroyed owned group")
            if result.exit_code == 0 and result.outcome == "succeeded" and not result.stdout.strip():
                result = result.model_copy(update={"stdout": "false"})
        # CleanupRunner models the per-deployment Azure resources, but its
        # generic AutomationRunner fallback returns an empty string for the
        # subscription-wide inventories used by the shared Network Watcher
        # cleanup path. Those are ARM collection responses, where an empty
        # inventory is represented as {"value": []}; blank output is
        # deliberately rejected by the production parser as unknown state.
        # Keep this narrowly scoped to the two auxiliary collection reads and
        # preserve the delegate's recorded request and command timing.
        if (request.action_type in {"azure_auxiliary_inventory_resources",
                "azure_auxiliary_inventory_groups"}
                and result.exit_code == 0 and result.outcome == "succeeded"
                and not result.stdout.strip()):
            import json
            result = result.model_copy(update={"stdout": json.dumps({"value": []})})
        if (request.action_type == "profile_absence_groups"
                and self._pair_destroyed and self._expiry_destroyed
                and result.exit_code == 0 and result.outcome == "succeeded"
                and not result.stdout.strip()):
            import json
            journal_path = self.context.options.attempt_root / "recovery.json"
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            baseline = journal.get("profile_external_baseline")
            if (not isinstance(baseline, dict)
                    or baseline.get("group_inventory") != []
                    or baseline.get("resource_inventory") != []):
                raise ValueError("blank Azure group inventory cannot be normalized without an empty captured baseline")
            result = result.model_copy(update={"stdout": json.dumps({"value": []})})
        if (request.action_type == "profile_absence_resources"
                and self._pair_destroyed and self._expiry_destroyed
                and result.exit_code == 0 and result.outcome == "succeeded"
                and not result.stdout.strip()):
            import json
            result = result.model_copy(update={"stdout": json.dumps({"value": []})})
        if (request.action_type == "profile_absence_role_assignments"
                and self._pair_destroyed and self._expiry_destroyed
                and result.exit_code == 0 and result.outcome == "succeeded"
                and not result.stdout.strip()):
            import json
            expected_argv = ["az", "rest", "--subscription", self.context.options.subscription_id,
                "--method", "GET", "--url",
                f"https://management.azure.com/subscriptions/{self.context.options.subscription_id}/providers/Microsoft.Authorization/roleAssignments?api-version=2022-04-01",
                "--output", "json"]
            journal_path = self.context.options.attempt_root / "recovery.json"
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            baseline = journal.get("profile_external_baseline")
            if (request.argv != expected_argv or not isinstance(baseline, dict)
                    or baseline.get("group_inventory") != []
                    or baseline.get("resource_inventory") != []):
                raise ValueError("blank Azure role inventory cannot be normalized without exact readback and empty captured baseline")
            result = result.model_copy(update={"stdout": json.dumps({"value": []})})
        if (request.action_type == "profile_absence_role_definitions"
                and self._pair_destroyed and self._expiry_destroyed
                and result.exit_code == 0 and result.outcome == "succeeded"
                and not result.stdout.strip()):
            import json
            expected_argv = ["az", "rest", "--subscription", self.context.options.subscription_id,
                "--method", "GET", "--url",
                f"https://management.azure.com/subscriptions/{self.context.options.subscription_id}/providers/Microsoft.Authorization/roleDefinitions?%24filter=type%20eq%20%27CustomRole%27&api-version=2022-04-01",
                "--output", "json"]
            journal_path = self.context.options.attempt_root / "recovery.json"
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            expiry = journal.get("expiry")
            if (request.argv != expected_argv or not isinstance(expiry, dict)
                    or not isinstance(expiry.get("role_definition_id"), str)
                    or not expiry["role_definition_id"].strip()):
                raise ValueError("blank Azure role-definition inventory lacks exact deleted-role identity")
            result = result.model_copy(update={"stdout": json.dumps({"value": []})})
        if (request.action_type == "profile_absence_automation"
                and self._pair_destroyed and self._expiry_destroyed
                and result.exit_code == 0 and result.outcome == "succeeded"
                and not result.stdout.strip()):
            import json
            from cloud_network_benchmark.azure_provider import _slug
            journal_path = self.context.options.attempt_root / "recovery.json"
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            expiry = journal.get("expiry")
            account = (expiry.get("automation_account_id")
                if isinstance(expiry, dict) else None)
            url = request.argv[request.argv.index("--url") + 1] if "--url" in request.argv else ""
            allowed = {f"{account}?api-version=2024-10-23"} if isinstance(account, str) else set()
            for collection, key in (("jobSchedules", "job_schedule_id"),
                    ("schedules", "schedule"), ("runbooks", "runbook")):
                metadata = expiry.get("metadata", {}) if isinstance(expiry, dict) else {}
                child = (expiry.get(key) or metadata.get(key)) if isinstance(metadata, dict) else None
                if isinstance(child, str) and child.strip():
                    allowed.add(f"{account}/{collection}/{child}?api-version=2024-10-23")
            if (url not in allowed or request.argv[:3] != ["az", "rest", "--subscription"]
                    or request.argv[3] != self.context.options.subscription_id
                    or request.argv[4:6] != ["--method", "GET"]
                    or request.argv[-2:] != ["--output", "json"]):
                raise ValueError("blank Azure Automation absence read is outside exact deleted identities")
            result = result.model_copy(update={"outcome": "failed", "exit_code": 1,
                "stdout": "", "stderr": json.dumps({"error": {"code": "ResourceNotFound"}})})
        return result

    def run_bounded_copy(self, request, destination, maximum_payload_bytes: int) -> CommandResult:
        self._check_request(request)
        if request.action_type != "ssh" or type(maximum_payload_bytes) is not int or maximum_payload_bytes < 0:
            raise ValueError("bounded copy requires an SSH request and nonnegative integer cap")
        payload = self._payloads.get(request.action_id)
        if payload is None:
            raise ValueError("no exact bounded-copy payload registered for this action")
        if len(payload) > maximum_payload_bytes:
            raise ValueError("bounded-copy fixture payload exceeds the caller's cap")
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(payload)
        now = self.context.clock()
        from cloud_network_benchmark.contracts.common import CommandOutcome
        return CommandResult(action_id=request.action_id, outcome=CommandOutcome.SUCCEEDED,
            started_at=now, finished_at=now, duration_seconds=0, exit_code=0,
            stdout="", stderr="")
