"""Saved-plan response fixture for the reduced, production-path operator chain.

This is deliberately a CommandRunner decorator. It answers only the Terraform
plan and show calls issued by ``terraform_plan.reviewed_apply``. The actual
driver, reviewed_apply, scope validators, and apply command remain in the chain.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping

from cloud_network_benchmark.contracts.execution import CommandRequest, CommandResult


class ReducedPlanProtocol:
    """Materialize one deterministic plan per actual private apply request."""

    def __init__(self, repository_root: Path, runtime_roots: Mapping[str, Path] | None = None):
        self.repository_root = Path(repository_root).resolve(strict=True)
        self.runtime_roots = {}
        for provider, roots in (runtime_roots or {}).items():
            roots = (roots,) if isinstance(roots, (str, Path)) else tuple(roots)
            for root in roots:
                self.register_runtime_root(provider, root)
        self._plans: dict[tuple[str, str, str], tuple[Path, str, dict, str, str, str, str]] = {}

    def register_runtime_root(self, provider: str, root: Path) -> None:
        """Bind an observed provider leaf when the production factory creates it."""
        if provider not in {"gcp", "azure"}:
            raise ValueError("only GCP and Azure runtime leaves are in this protocol")
        path = Path(root).absolute()
        # The root may be created lazily by the provider factory. Resolve
        # strictly at the first actual request, immediately before use.
        self.runtime_roots.setdefault(provider, set()).add(path.resolve(strict=False))

    def run(self, request: CommandRequest, delegate) -> CommandResult:
        """Intercept only actual plan/show argv; delegate every other request."""
        argv = list(request.argv)
        # AWS is intentionally supplied by its separately reviewed leaf
        # protocol. Never let this GCP/Azure-only wrapper inspect AWS calls.
        if not request.action_id.startswith(("gcp-", "azure-")):
            return delegate.run(request)
        if argv and Path(argv[0]).name == "terraform" and "plan" in argv:
            return self._plan(request)
        if argv and Path(argv[0]).name == "terraform" and "show" in argv:
            return self._show(request)
        return delegate.run(request)

    def _binding(self, request: CommandRequest, *, require_variables: bool = True):
        cwd = Path(request.cwd).resolve(strict=True)
        provider = None
        for candidate, roots in self.runtime_roots.items():
            for expected in roots:
                # Compare the path observed from the real factory first. This
                # avoids touching another scenario's not-yet-created leaf.
                expected = expected.resolve(strict=False)
                if expected.exists() and cwd == expected.resolve(strict=True):
                    provider = candidate
                    break
                if candidate == "gcp" and expected.exists() and self._owned_gcp_workdir(cwd, expected.resolve(strict=True)):
                    provider = candidate
                    break
            if provider is not None:
                break
        if provider is None:
            raise ValueError("saved-plan cwd is not the exact bound private provider root")
        if not request.action_id.startswith(provider + "-"):
            raise ValueError("saved-plan action identity does not match its provider root")
        chdir = next((Path(arg.split("=", 1)[1]).resolve(strict=True)
                      for arg in request.argv if arg.startswith("-chdir=")), None)
        if chdir is None or chdir.parent != self.repository_root / "terraform" / provider:
            raise ValueError("saved-plan Terraform root is outside its exact trusted provider source tree")
        allowed_roots = {"gcp": {"protection", "pair"}, "azure": {"expiry", "pair"}}
        if chdir.name not in allowed_roots[provider]:
            raise ValueError("saved-plan Terraform root is outside the reduced protocol scope")
        if provider == "gcp" and chdir.name != cwd.name:
            raise ValueError("saved-plan Terraform source root does not match its private work directory")
        if provider == "gcp" and require_variables:
            state_arg = next((arg.split("=", 1)[1] for arg in request.argv
                              if arg.startswith("-state=")), None)
            if state_arg is None:
                raise ValueError("saved-plan request has no exact private state path")
            state_path = Path(state_arg).resolve(strict=False)
            if not state_path.is_relative_to(cwd):
                raise ValueError("saved-plan state must remain inside its exact private work directory")
        var_arg = next((arg.split("=", 1)[1] for arg in request.argv
                        if arg.startswith("-var-file=")), None)
        variables_path = variables_bytes = variables = None
        if var_arg is not None:
            variables_path = Path(var_arg).resolve(strict=True)
            if not variables_path.is_file() or variables_path.is_symlink() or not variables_path.is_relative_to(cwd):
                raise ValueError("saved-plan variables must be a regular file in the exact private root")
            variables_bytes = variables_path.read_bytes()
            variables = json.loads(variables_bytes)
            if not isinstance(variables, dict):
                raise ValueError("saved-plan variables must be a JSON object")
        elif require_variables:
            raise ValueError("saved-plan request has no exact variable file")
        source_hashes = []
        for source in sorted(chdir.rglob("*.tf")):
            if source.is_symlink() or not source.is_file():
                raise ValueError("trusted Terraform source contains a non-regular file")
            source_hashes.append((str(source.relative_to(chdir)), hashlib.sha256(source.read_bytes()).hexdigest()))
        if not source_hashes:
            raise ValueError("trusted Terraform root has no source files")
        return provider, cwd, chdir, variables_path, variables_bytes, variables, source_hashes

    @staticmethod
    def _owned_gcp_workdir(cwd: Path, expected: Path) -> bool:
        """Bind GCP review cwd to one real private attempt and its identity."""
        if cwd.name not in {"protection", "pair"}:
            return False
        try:
            parts = cwd.relative_to(expected).parts
        except ValueError:
            return False
        # GCP _attempt_dir uses <12-char run digest>/<run>/<deployment>, then
        # _terraform selects the pair or protection work directory beneath it.
        if len(parts) != 4 or parts[-1] != cwd.name:
            return False
        digest, run_id, deployment_id, _work = parts
        if digest != hashlib.sha256(run_id.encode()).hexdigest()[:12]:
            return False
        attempt = cwd.parent
        journal = attempt / "attempt.json"
        if journal.is_symlink() or not journal.is_file():
            return False
        try:
            identity = json.loads(journal.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return identity.get("run_id") == run_id and identity.get("deployment_id") == deployment_id

    @staticmethod
    def _substitute(value, original: dict, actual: dict, *, provider: str):
        # Keep the reviewed fixture resource graph, binding every fixture input
        # to the real private tfvars. Derived names are rooted in the actual
        # attempt namespace/resource group as the production HCL requires.
        for key, old in original.items():
            if value == old and key in actual:
                return actual[key]
        if isinstance(value, dict):
            return {key: ReducedPlanProtocol._substitute(item, original, actual, provider=provider)
                    for key, item in value.items()}
        if isinstance(value, list):
            return [ReducedPlanProtocol._substitute(item, original, actual, provider=provider)
                    for item in value]
        if isinstance(value, str):
            # Plan JSON can embed variable values inside known ARM/self-link
            # strings, so replace those substrings as well as exact fields.
            for key, old in sorted(original.items(), key=lambda pair: len(str(pair[1])), reverse=True):
                if isinstance(old, str) and old and key in actual and isinstance(actual[key], str):
                    value = value.replace(old, actual[key])
            if provider == "gcp" and "namespace" in actual:
                value = value.replace("scope", actual["namespace"])
            if provider == "azure" and "resource_group_name" in actual:
                value = value.replace("scope", actual["resource_group_name"])
        return value

    @classmethod
    def _document(cls, provider: str, root: Path, variables: dict) -> dict:
        if provider == "gcp":
            from tests.unit.test_terraform_plan_review import plan_document, scope

            fixture_variables, resources = scope("gcp", root.name, variables.get("scenario", "same_zone"))
            resources = cls._substitute(resources, fixture_variables, variables, provider=provider)
            if root.name == "protection":
                # The fixture's subnetwork and compact-policy regions are
                # derived from its example zones. Re-derive them from the
                # actual private variables after substitution.
                for suffix in ("a", "b"):
                    address = "google_compute_subnetwork." + suffix + ("[0]" if suffix == "b" else "")
                    if address in resources:
                        resources[address]["region"] = variables["zone_" + suffix].rsplit("-", 1)[0]
                policy = resources.get("google_compute_resource_policy.compact[0]")
                if policy is not None:
                    policy["region"] = variables["zone_a"].rsplit("-", 1)[0]
            # Scope fixtures encode canonical topology. These values are
            # persisted by the real provider path and therefore flow through
            # unchanged; the validators below check them against those inputs.
            return plan_document(variables, resources, provider)

        from tests.unit.test_azure_saved_plan import azure_document, azure_scope

        scenario = variables.get("scenario", "same_zone")
        region = variables.get("location", "swedencentral")
        fixture_variables, resources = azure_scope(root.name, scenario, region)
        resources = cls._substitute(resources, fixture_variables, variables, provider=provider)
        if root.name == "pair":
            image_parts = variables["selected_image"].split(":")
            if len(image_parts) != 4 or any(not part for part in image_parts):
                raise ValueError("actual Azure selected_image must contain publisher, offer, SKU, and version")
            publisher, offer, sku, version = image_parts
            for role in ("a", "b"):
                resources[f'azurerm_linux_virtual_machine.pair["{role}"]']["source_image_reference"] = [{
                    "publisher": publisher, "offer": offer, "sku": sku, "version": version,
                }]
        document = azure_document(variables, resources)
        # Pair HCL includes one read-only existing-group data source. It is
        # part of the actual source plan and is permitted by validate_scope.
        if root.name == "pair":
            group = variables["resource_group_name"]
            document["resource_changes"].insert(0, {
                "mode": "data", "address": "data.azurerm_resource_group.pair",
                "type": "azurerm_resource_group", "provider_name": "registry.terraform.io/hashicorp/azurerm",
                "change": {"actions": ["read"], "before": None, "after": {"name": group}},
            })
            document["configuration"]["root_module"]["resources"].append(
                {"address": "data.azurerm_resource_group.pair", "provider_config_key": "azurerm",
                 "expressions": {}})
        return document

    def _plan(self, request: CommandRequest) -> CommandResult:
        provider, cwd, root, variables_path, variables_bytes, variables, source_hashes = self._binding(request)
        output_arg = next((arg.split("=", 1)[1] for arg in request.argv
                           if arg.startswith("-out=")), None)
        if output_arg is None:
            raise ValueError("actual reviewed plan request has no binary output path")
        target = Path(output_arg).resolve(strict=False)
        if not target.is_absolute() or not target.is_relative_to(cwd):
            raise ValueError("saved-plan output must remain inside the exact private runtime root")
        if target.is_symlink() or not target.is_file() or target.stat().st_size != 0:
            raise ValueError("reviewed_apply did not precreate an empty regular saved-plan destination")
        base_action = request.action_id.removesuffix("-plan")
        sources_digest = hashlib.sha256(json.dumps(source_hashes, sort_keys=True).encode()).hexdigest()
        binding = json.dumps([base_action, provider, str(root), hashlib.sha256(variables_bytes).hexdigest(),
                              sources_digest], sort_keys=True).encode()
        payload = b"CNB-OFFLINE-SAVED-PLAN\0" + hashlib.sha256(binding).digest()
        with target.open("wb") as stream:
            stream.write(payload)
        binary_hash = hashlib.sha256(payload).hexdigest()
        self._plans[(provider, str(cwd), base_action)] = (target, binary_hash,
            self._document(provider, root, variables), str(root), sources_digest,
            str(variables_path), hashlib.sha256(variables_bytes).hexdigest())
        return self._result(request, "")

    def _show(self, request: CommandRequest) -> CommandResult:
        provider, cwd, root, _variables_path, _variables_bytes, _variables, source_hashes = self._binding(
            request, require_variables=False)
        base_action = request.action_id.removesuffix("-show")
        entry = self._plans.get((provider, str(cwd), base_action))
        if entry is None or len(request.argv) < 4:
            raise ValueError("show request has no matching actual saved-plan response")
        path = Path(request.argv[-1]).resolve(strict=True)
        if (path != entry[0] or path.is_symlink() or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != entry[1]):
            raise ValueError("show request path or binary bytes differ from its generated plan")
        if str(root) != entry[3] or hashlib.sha256(json.dumps(source_hashes, sort_keys=True).encode()).hexdigest() != entry[4]:
            raise ValueError("show request source tree differs from the actual plan request")
        variables_path = Path(entry[5])
        if (variables_path.is_symlink() or not variables_path.is_file()
                or hashlib.sha256(variables_path.read_bytes()).hexdigest() != entry[6]):
            raise ValueError("actual private variable file changed between plan and show")
        return self._result(request, json.dumps(entry[2], sort_keys=True))

    @staticmethod
    def _result(request: CommandRequest, stdout: str) -> CommandResult:
        from datetime import datetime, timezone
        from cloud_network_benchmark.contracts.common import CommandOutcome

        now = datetime.now(timezone.utc)
        return CommandResult(action_id=request.action_id, outcome=CommandOutcome.SUCCEEDED,
            started_at=now, finished_at=now, duration_seconds=0.0, exit_code=0,
            stdout=stdout, stderr="")
