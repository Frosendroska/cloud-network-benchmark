"""Per-command offline transport for the reduced three-provider operator path.

The router replaces only the existing ``SubprocessCommandRunner`` boundary.
Provider drivers, command factories, source validators, workflow hooks,
``run_lifecycle`` and guest execution remain the production implementations.
Every request is logged and routed by a bound private root or SSH identity.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cloud_network_benchmark.contracts.execution import CommandRequest, CommandResult


@dataclass
class _ProviderLeaf:
    provider: str
    deployment: Any
    runtime_roots: tuple[Path, ...]
    runner: Any
    origin_id: int


class _LeafDelegate:
    """Small adapter used by the optional plan protocol's delegate contract."""

    def __init__(self, router: "ReducedCommandRouter"):
        self.router = router

    def run(self, request: CommandRequest) -> CommandResult:
        return self.router._dispatch(request)


class _OriginDelegate:
    """Keep the actual boundary instance available through plan delegation."""

    def __init__(self, router: "ReducedCommandRouter", origin_runner: Any):
        self.router = router
        self.origin_runner = origin_runner

    def run(self, request: CommandRequest) -> CommandResult:
        return self.router._dispatch(request, origin_runner=self.origin_runner)


class ReducedCommandRouter:
    """Route external command requests to source-shaped offline response leaves.

    Args:
        remote_root: private local root for the six per-VM native fixture trees.
        repository_root: exact checkout root used to bind Terraform source paths.
        clock: the finite lifecycle clock used by the activated CLI.

    Call :meth:`install_hook_observers` before activation. The observer wraps
    the real adapter factories, records their actual driver/deployment inputs,
    and returns the original hooks unchanged. Install the optional plan
    protocol after hooks have been constructed so it sees all three private
    roots, then call :meth:`install_command_boundary` before CLI activation.
    """

    def __init__(self, *, remote_root: Path, repository_root: Path, clock: Any):
        self.remote_root = Path(remote_root).resolve()
        self.repository_root = Path(repository_root).resolve(strict=True)
        self.clock = clock
        self.requests: list[CommandRequest] = []
        self.routed_requests: list[dict[str, Any]] = []
        self.boundary_errors: list[dict[str, Any]] = []
        self.boundary_observations: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self._leaves: dict[tuple[str, str], _ProviderLeaf] = {}
        self._remotes: dict[str, Any] = {}
        self._identity_leaf: dict[str, set[tuple[str, str]]] = {}
        self._endpoint_leaf: dict[str, set[tuple[str, str]]] = {}
        self._invocation_leaf: dict[str, tuple[str, str]] = {}
        self._active = threading.local()
        self._thread_leaf: dict[threading.Thread, tuple[str, str]] = {}
        self.runtime_roots: dict[str, list[Path]] = {"aws": [], "gcp": [], "azure": []}
        self._plan_protocol: Any = None
        self._delegate = _LeafDelegate(self)

    def install_plan_protocol(self, protocol: Any) -> None:
        """Install ``ReducedPlanProtocol`` for exact GCP/Azure plan/show calls."""
        if not callable(getattr(protocol, "run", None)):
            raise TypeError("plan protocol must provide run(request, delegate)")
        self._plan_protocol = protocol
        register = getattr(protocol, "register_runtime_root", None)
        if callable(register):
            for (provider, _), leaf in self._leaves.items():
                for root in leaf.runtime_roots:
                    if provider in {"gcp", "azure"}:
                        register(provider, root)

    @staticmethod
    def _inside(path: Path, roots: tuple[Path, ...]) -> bool:
        path = path.resolve(strict=False)
        return any(path == root or root in path.parents for root in roots)

    def _register_leaf(self, provider: str, deployment: Any,
                       roots: tuple[Path, ...], runner: Any, origin_runner: Any) -> None:
        roots = tuple(Path(root).resolve(strict=False) for root in roots)
        if not roots or any(not str(root) for root in roots):
            raise ValueError(f"{provider} leaf requires bound private roots")
        run_id = getattr(deployment, "run_id", None)
        deployment_id = getattr(deployment, "deployment_id", None)
        if not run_id or not deployment_id:
            raise ValueError(f"{provider} leaf lacks actual deployment identity")
        with self._lock:
            key = (provider, str(roots[0]))
            existing = self._leaves.get(key)
            if existing is not None and existing.deployment is not deployment:
                raise ValueError(f"{provider} private root is already bound to another deployment")
            self._leaves[key] = _ProviderLeaf(provider, deployment, roots, runner,
                id(self._command_runner_origin(origin_runner)))
            if roots[0] not in self.runtime_roots[provider]:
                self.runtime_roots[provider].append(roots[0])
            if self._plan_protocol is not None and provider in {"gcp", "azure"}:
                register = getattr(self._plan_protocol, "register_runtime_root", None)
                if callable(register):
                    register(provider, roots[0])

    def install_hook_observers(self, monkeypatch: Any, adapters: Any) -> None:
        """Observe actual factory bindings and remote-pair preparation inputs."""
        from tests.support.aws_leaf_protocol import AwsLeafProtocol, AwsRunContext
        from tests.support.azure_leaf_protocol import AzureLeafProtocol, AzureRunContext
        from tests.support.gcp_command_fixture import GcpCommandFixture, GcpRunContext
        from tests.integration.test_azure_provider_flow import CleanupRunner

        original_aws = adapters.aws_hooks
        original_gcp = adapters.gcp_hooks
        original_azure = adapters.azure_hooks
        original_prepare = adapters.prepare_remote_pair

        def observe_aws(driver, deployment, expiry_request, **kwargs):
            runtime = Path(driver.settings.private_state_root).resolve(strict=False)
            configured_root = Path(deployment.terraform_directory).resolve(strict=True)
            pair_root = configured_root / "pair" if configured_root.name == "aws" else configured_root
            if pair_root.name != "pair" or pair_root.parent.name != "aws":
                raise ValueError("actual AWS deployment must bind to the configured aws/pair source")
            regions = sorted({str(deployment.vm_a.region), str(deployment.vm_b.region)})
            source_roots = {"aws-pair-apply": pair_root}
            source_roots.update({f"aws-safety-apply-{region}": pair_root.parent / "safety"
                for region in regions})
            leaf = AwsLeafProtocol(AwsRunContext(provider="aws", request=expiry_request,
                driver=driver, deployment=deployment, run_id=deployment.run_id,
                deployment_id=deployment.deployment_id, clock=self.clock.utc_now,
                runtime_roots={"vm_a": runtime, "vm_b": runtime}, source_roots=source_roots,
                control_root=self.repository_root))
            self._register_leaf("aws", deployment, (runtime,), leaf, driver.runner)
            return original_aws(driver, deployment, expiry_request, **kwargs)

        def observe_gcp(driver, deployment, expiry_request, **kwargs):
            runtime = Path(driver.settings.private_state_root).resolve(strict=False)
            leaf = GcpCommandFixture(GcpRunContext(provider="gcp", run_id=deployment.run_id,
                deployment_id=deployment.deployment_id, driver=driver, deployment=deployment,
                clock=self.clock, runtime_roots=(runtime,)))
            self._register_leaf("gcp", deployment, (runtime,), leaf, driver.runner)
            return original_gcp(driver, deployment, expiry_request, **kwargs)

        def observe_azure(deployment, expiry_request, **kwargs):
            options = kwargs["options"]
            runtime = Path(options.attempt_root).resolve(strict=False)
            source_runner = CleanupRunner(deployment, options, self.clock.utc_now())
            leaf = AzureLeafProtocol(AzureRunContext(provider="azure", run_id=deployment.run_id,
                deployment_id=deployment.deployment_id, deployment=deployment,
                options=options, clock=self.clock.utc_now, runtime_roots={"azure": runtime},
                control_root=self.repository_root))
            # Preserve request visibility for the integration test and use the
            # exact source-shaped cleanup responder selected above.
            leaf._delegate = source_runner
            leaf.requests = source_runner.requests
            self._register_leaf("azure", deployment, (runtime,), leaf, kwargs["runner"])
            return original_azure(deployment, expiry_request, **kwargs)

        def observe_prepare_remote_pair(connection, **kwargs):
            leaf_key = self._leaf_key_for_run(connection.run_id, connection.deployment_id)
            if leaf_key is None or leaf_key not in self._leaves:
                raise ValueError("remote pair does not match an observed provider deployment")
            provider = leaf_key[0]
            responder = self._remote_for(provider, connection.run_id, connection.deployment_id)
            responder.private_a, responder.private_b = connection.private_ipv4_a, connection.private_ipv4_b
            responder.public_a, responder.public_b = connection.vm_a.host, connection.vm_b.host
            responder.native_base_time = self.clock.utc_now()
            responder.run_id, responder.deployment_id = connection.run_id, connection.deployment_id
            responder.connection = connection
            for vm in (connection.vm_a, connection.vm_b):
                reference = str(vm.authentication_reference)
                existing = self._identity_leaf.get(reference)
                if existing is None:
                    existing = set()
                    self._identity_leaf[reference] = existing
                existing.add(leaf_key)
                normalized = str(Path(reference).resolve(strict=False))
                self._identity_leaf.setdefault(normalized, set()).add(leaf_key)
                self._endpoint_leaf.setdefault(self._endpoint(vm.user, vm.host, vm.port), set()).add(leaf_key)
            previous = getattr(self._active, "leaf_key", None)
            self._active.leaf_key = leaf_key
            # Production executes a provider's ordered workflow stages on its
            # provider worker. Keep that observed binding for later retrieval
            # commands, whose transport request has cwd='.' and whose artifact
            # IDs intentionally omit deployment identity.
            self._thread_leaf[threading.current_thread()] = leaf_key
            try:
                return original_prepare(connection, **kwargs)
            finally:
                if previous is None:
                    del self._active.leaf_key
                else:
                    self._active.leaf_key = previous

        monkeypatch.setattr(adapters, "aws_hooks", observe_aws)
        monkeypatch.setattr(adapters, "gcp_hooks", observe_gcp)
        monkeypatch.setattr(adapters, "azure_hooks", observe_azure)
        monkeypatch.setattr(adapters, "prepare_remote_pair", observe_prepare_remote_pair)

    def _leaf_key_for_run(self, run_id: str, deployment_id: str) -> tuple[str, str] | None:
        matches = [key for key, leaf in self._leaves.items()
            if (getattr(leaf.deployment, "run_id", None) == run_id
                and getattr(leaf.deployment, "deployment_id", None) == deployment_id)]
        return matches[0] if len(matches) == 1 else None

    def _remote_for(self, provider: str, run_id: str, deployment_id: str):
        key = (provider, run_id, deployment_id)
        responder = self._remotes.get(key)
        if responder is None:
            # Import the actual full-native source responder. Importing its
            # class does not inherit pytest marks/fixtures from its module.
            from tests.support.reduced_guest_archive_transport import ReducedGuestArchiveTransport
            responder = ReducedGuestArchiveTransport()
            responder.full_native = True
            responder.partial_after_first_unit = False
            responder.cancel_verified = True
            responder.clock = self.clock
            responder.late_tail = True
            responder.protocol_elapsed = 60
            base = self.remote_root / run_id / deployment_id
            responder.workspace_roots = {role: base / role for role in ("vm_a", "vm_b")}
            responder.native_root = responder.workspace_roots["vm_a"]
            self._remotes[key] = responder
        return responder

    def install_command_boundary(self, monkeypatch: Any) -> None:
        """Route actual external commands and bounded copies into this module."""
        from cloud_network_benchmark.contracts.execution import SubprocessCommandRunner

        monkeypatch.setattr(SubprocessCommandRunner, "run",
            lambda command_runner, request: self.run(request, origin_runner=command_runner))
        monkeypatch.setattr(SubprocessCommandRunner, "run_bounded_copy",
            lambda command_runner, request, destination, cap: self.run_bounded_copy(
                request, destination, cap, origin_runner=command_runner))

    @staticmethod
    def _command_runner_origin(runner: Any) -> Any:
        seen: set[int] = set()
        current = runner
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            delegate = getattr(current, "delegate", None)
            if delegate is None:
                break
            current = delegate
        if current is None:
            raise ValueError("command runner delegate chain ended without a boundary")
        return current

    def _identity(self, request: CommandRequest) -> str | None:
        argv = request.argv
        try:
            return str(Path(argv[argv.index("-i") + 1]).resolve(strict=False))
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _endpoint(user: str, host: str, port: int) -> str:
        return f"{user}@{host}:{port}"

    def _leaf_key_for_endpoint(self, request: CommandRequest) -> tuple[str, str] | None:
        try:
            port = int(request.argv[request.argv.index("-p") + 1])
        except (ValueError, IndexError):
            port = 22
        if request.action_type == "ssh" and len(request.argv) >= 2:
            target = request.argv[-2]
        else:
            target = next((arg for arg in reversed(request.argv) if "@" in arg and not arg.startswith("-")), None)
        if target is None or "@" not in target:
            return None
        candidates = self._endpoint_leaf.get(f"{target}:{port}", set())
        return next(iter(candidates)) if len(candidates) == 1 else None

    def _leaf_key_for_request(self, request: CommandRequest,
                              origin_runner: Any = None) -> tuple[str, str] | None:
        action = request.action_id
        active = getattr(self._active, "leaf_key", None)
        if active is not None and action.startswith("f02r-"):
            return active
        # Provider-private action IDs expose the exact deployment. Guest and
        # collector UUID actions acquire that binding from their real uploaded
        # source JSON immediately before the corresponding command.
        direct = [key for key, leaf in self._leaves.items()
            if getattr(leaf.deployment, "deployment_id", "") in action]
        if len(direct) == 1:
            return direct[0]
        invocation = re.search(r"(?:f09-(?:guest|diagnostics)-[a-z-]+-)([0-9a-f]{32})$", action)
        if invocation is not None and invocation.group(1) in self._invocation_leaf:
            return self._invocation_leaf[invocation.group(1)]
        endpoint = self._leaf_key_for_endpoint(request)
        if endpoint is not None:
            return endpoint
        identity = self._identity(request)
        if identity is not None:
            try:
                literal = request.argv[request.argv.index("-i") + 1]
            except (ValueError, IndexError):
                literal = identity
            candidates = self._identity_leaf.get(identity, set()) | self._identity_leaf.get(literal, set())
            if len(candidates) == 1:
                return next(iter(candidates))
        origin_id = id(self._command_runner_origin(origin_runner)) if origin_runner is not None else None
        cwd = Path(request.cwd).resolve(strict=False)
        origin_matches = [key for key, leaf in self._leaves.items()
            if leaf.origin_id == origin_id and any(cwd == root or root in cwd.parents
                for root in leaf.runtime_roots)] if origin_id is not None else []
        if len(origin_matches) == 1:
            return origin_matches[0]
        if request.action_type in {"ssh", "scp"} or action.startswith(("f02r-", "f09-")):
            thread_leaf = self._thread_leaf.get(threading.current_thread())
            if thread_leaf is not None:
                return thread_leaf
        if (origin_id is not None and cwd == self.repository_root
                and action.startswith("aws-")
                and request.argv and (
                    (Path(request.argv[0]).name == "aws"
                     and not any(Path(token).name == "terraform" for token in request.argv))
                    or (action.startswith("aws-host-keyscan-")
                        and Path(request.argv[0]).name == "ssh-keyscan")
                    or (action.startswith("aws-host-key-fingerprint-")
                        and Path(request.argv[0]).name == "ssh-keygen"))):
            aws_matches = [key for key, leaf in self._leaves.items()
                if key[0] == "aws" and leaf.origin_id == origin_id]
            if len(aws_matches) == 1:
                return aws_matches[0]
        if (origin_id is not None and cwd == self.repository_root
                and action.startswith("azure-") and request.argv):
            executable = Path(request.argv[0]).name
            if executable == "az" or (executable == "terraform"
                    and any(token in request.argv for token in ("destroy", "output", "init"))):
                azure_matches = [key for key, leaf in self._leaves.items()
                    if key[0] == "azure" and leaf.origin_id == origin_id]
                if len(azure_matches) == 1:
                    return azure_matches[0]
        matches = [key for key, leaf in self._leaves.items()
            if (origin_id is None or leaf.origin_id == origin_id)
            and any(cwd == root or root in cwd.parents for root in leaf.runtime_roots)]
        return matches[0] if len(matches) == 1 else None

    def _capture_invocation_binding(self, request: CommandRequest) -> None:
        action = request.action_id
        if not request.action_type == "scp" or not action.startswith((
                "f09-guest-request-", "f09-diagnostics-context-")):
            return
        invocation = action.rsplit("-", 1)[-1]
        local_path = next((Path(token) for token in request.argv
            if token.endswith(("request.json", "context.json")) and Path(token).is_file()), None)
        if local_path is None or local_path.is_symlink():
            raise ValueError(f"actual F09 source upload is missing: {action}")
        payload = json.loads(local_path.read_text(encoding="utf-8"))
        deployment_id = payload.get("identity", {}).get("deployment_id")
        if deployment_id is None:
            deployment_id = payload.get("deployment_id")
        matches = [key for key, leaf in self._leaves.items()
            if getattr(leaf.deployment, "deployment_id", None) == deployment_id]
        if len(matches) != 1:
            raise ValueError(f"F09 upload does not match one actual deployment: {action}")
        self._invocation_leaf[invocation] = matches[0]

    def _provider_for_request(self, request: CommandRequest, origin_runner: Any = None) -> str:
        action = request.action_id
        leaf_key = self._leaf_key_for_request(request, origin_runner)
        if leaf_key is not None:
            return leaf_key[0]
        if action.startswith("aws-"):
            return "aws"
        if action.startswith("gcp-"):
            return "gcp"
        if action.startswith("azure-"):
            return "azure"
        cwd = Path(request.cwd).resolve(strict=False)
        matches = [provider for provider, roots in self.runtime_roots.items()
            if any(cwd == root or root in cwd.parents for root in roots)]
        if len(matches) == 1:
            return matches[0]
        chdir = next((token.partition("=")[2] for token in request.argv
            if token.startswith("-chdir=")), None)
        if chdir:
            source = Path(chdir).resolve(strict=False)
            for (provider, _), leaf in self._leaves.items():
                deployment_source = Path(leaf.deployment.terraform_directory).resolve(strict=False)
                if provider == "aws" and source in {deployment_source, deployment_source.parent / "safety"}:
                    return provider
                if provider == "gcp" and source in {self.repository_root / "terraform/gcp/pair",
                        self.repository_root / "terraform/gcp/protection"}:
                    return provider
                if provider == "azure" and source in {self.repository_root / "terraform/azure/pair",
                        self.repository_root / "terraform/azure/expiry"}:
                    return provider
        raise ValueError(f"command has no unique bound provider: {action} cwd={request.cwd}")

    def run(self, request: CommandRequest, *, origin_runner: Any = None) -> CommandResult:
        self.requests.append(request)
        self._capture_invocation_binding(request)
        if self._plan_protocol is not None:
            return self._plan_protocol.run(request, _OriginDelegate(self, origin_runner))
        return self._dispatch(request, origin_runner=origin_runner)

    def _dispatch(self, request: CommandRequest, *, origin_runner: Any = None) -> CommandResult:
        provider = self._provider_for_request(request, origin_runner)
        leaf = self._leaf_for_request(request, provider, origin_runner)
        if leaf is None:
            raise ValueError(f"no actual {provider} provider hooks were observed for {request.action_id}")
        key = (provider, str(getattr(leaf.deployment, "run_id")),
            str(getattr(leaf.deployment, "deployment_id")))
        responder = self._remotes.get(key)
        self.routed_requests.append({"request": request, "provider": provider,
            "run_id": key[1], "deployment_id": key[2],
            "runtime_roots": [str(root) for root in leaf.runtime_roots],
            "guest_workspace_roots": ([str(path) for path in responder.workspace_roots.values()]
                if responder is not None else [])})
        if request.action_id.startswith("terraform-init-"):
            if "init" not in request.argv or "-backend=false" not in request.argv:
                raise ValueError("only private backend-free Terraform init is supported")
            return self._result(request, "")
        if request.action_type in {"ssh", "scp"} or request.action_id.startswith(("f02r-", "f09-")):
            key = (provider, getattr(leaf.deployment, "run_id"),
                getattr(leaf.deployment, "deployment_id"))
            responder = self._remotes.get(key)
            if responder is None:
                raise ValueError(f"remote fixture not bound to actual pair: {request.action_id}")
            return responder.run(request)
        if provider == "azure":
            leaf.runner._delegate.now = self.clock.utc_now()
        return leaf.runner.run(request)

    def _leaf_for_request(self, request: CommandRequest, provider: str,
                          origin_runner: Any = None) -> _ProviderLeaf | None:
        leaf_key = self._leaf_key_for_request(request, origin_runner)
        if leaf_key is not None:
            return self._leaves.get(leaf_key)
        cwd = Path(request.cwd).resolve(strict=False)
        origin_id = id(self._command_runner_origin(origin_runner)) if origin_runner is not None else None
        matches = [leaf for (name, _), leaf in self._leaves.items()
            if name == provider and (origin_id is None or leaf.origin_id == origin_id)
            and any(cwd == root or root in cwd.parents for root in leaf.runtime_roots)]
        if len(matches) == 1:
            return matches[0]
        # Parent-scoped Terraform requests keep their private cwd even though
        # their source -chdir is shared, so ambiguity here is a closed error.
        return None

    def run_bounded_copy(self, request: CommandRequest, destination: Path,
                         maximum_payload_bytes: int, *, origin_runner: Any = None) -> CommandResult:
        self.requests.append(request)
        provider = self._provider_for_request(request, origin_runner)
        leaf = self._leaf_for_request(request, provider, origin_runner)
        if leaf is None:
            raise ValueError(f"no actual {provider} provider hooks were observed for bounded copy")
        key = (provider, getattr(leaf.deployment, "run_id"),
            getattr(leaf.deployment, "deployment_id"))
        responder = self._remotes.get(key)
        if responder is None:
            raise ValueError(f"bounded-copy responder not bound to actual pair: {request.action_id}")
        self.routed_requests.append({"request": request, "provider": provider,
            "run_id": str(key[1]), "deployment_id": str(key[2]),
            "runtime_roots": [str(root) for root in leaf.runtime_roots],
            "guest_workspace_roots": [str(path) for path in responder.workspace_roots.values()]})
        try:
            result = responder.run_bounded_copy(request, destination, maximum_payload_bytes)
            target = Path(destination)
            self.boundary_observations.append({"action_id": request.action_id,
                "provider": provider, "run_id": str(key[1]),
                "deployment_id": str(key[2]), "outcome": str(result.outcome),
                "exit_code": result.exit_code, "maximum_payload_bytes": maximum_payload_bytes,
                "destination_size_bytes": target.stat().st_size if target.is_file() else None,
                "stdout": result.stdout, "stderr": result.stderr})
            return result
        except BaseException as exc:
            import traceback
            self.boundary_errors.append({"action_id": request.action_id,
                "provider": provider, "run_id": str(key[1]),
                "deployment_id": str(key[2]), "error": repr(exc),
                "traceback": traceback.format_exc()})
            raise

    def _result(self, request: CommandRequest, stdout: str) -> CommandResult:
        now = self.clock.utc_now()
        return CommandResult(action_id=request.action_id, outcome="succeeded",
            started_at=now, finished_at=now, duration_seconds=0, exit_code=0,
            stdout=stdout, stderr="")
