from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import math
import re
import shlex
import time
from typing import Callable, Dict, List, Literal, Optional
from urllib.parse import urlparse
from pathlib import PurePosixPath

from pydantic import Field, field_validator, model_validator

from .access import AccessFailureEvidence, ConnectionOutput, RemoteActionEvidence, RemoteDeadline, _action, _execute, _failure, _request
from .contracts.common import CommandOutcome, IdentityContext, StrictModel
from .contracts.execution import CommandRequest, CommandRunner


class SourceBuild(StrictModel):
    tool: Literal["sockperf", "iperf3"]
    source_url: str = Field(min_length=1)
    source_reference: str = Field(min_length=1)
    archive_name: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def safe_source(self):
        parsed = urlparse(self.source_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("source URL must be credential-free HTTPS")
        for name, value in (("source_reference", self.source_reference), ("archive_name", self.archive_name)):
            if value.startswith("-") or "/" in value or "\\" in value or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}", value):
                raise ValueError(f"{name} must be a safe identifier or basename")
        return self


class BootstrapConfig(StrictModel):
    sources: List[SourceBuild] = Field(min_length=2, max_length=2)
    remote_root: str = Field(min_length=1)
    workspace: str = Field(min_length=1)
    install_prefix: str = Field(min_length=1)
    bootstrap_packages: List[str] = Field(default_factory=lambda: ["build-essential", "autoconf", "automake", "libtool", "pkg-config", "curl", "ca-certificates", "tar", "gzip", "iproute2", "iputils-ping", "netcat-openbsd", "nftables"])

    @model_validator(mode="after")
    def validate_paths_and_tools(self):
        if {s.tool for s in self.sources} != {"sockperf", "iperf3"}:
            raise ValueError("exactly one Sockperf and one iperf3 source is required")
        for name in ("remote_root", "workspace", "install_prefix"):
            value = getattr(self, name)
            if not value.startswith("/") or PurePosixPath(value).as_posix() != value or ".." in value.split("/") or any(ord(c) < 32 for c in value):
                raise ValueError(f"{name} must be a normalized absolute path")
            if not re.fullmatch(r"/[A-Za-z0-9._/-]+", value) or any(part in {".", ".."} for part in value.split("/")):
                raise ValueError(f"{name} contains shell-unsafe path characters")
            if name != "remote_root" and not value.startswith(self.remote_root.rstrip("/") + "/"):
                raise ValueError(f"{name} must be under remote_root")
        if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+.-]{0,63}", p) for p in self.bootstrap_packages):
            raise ValueError("bootstrap package names must be simple package identifiers")
        return self


class BuildObservation(StrictModel):
    role: str
    tool: str
    source_reference: str
    source_sha256: str
    compiler_version: str
    binary_path: str
    binary_version: str
    binary_sha256: str
    capabilities: List[str]

    @model_validator(mode="after")
    def complete_observation(self):
        if not all((self.source_reference, self.compiler_version.strip(), self.binary_path.startswith("/"), self.binary_version.strip())):
            raise ValueError("build observation must retain actual source/compiler/binary evidence")
        if not re.fullmatch(r"[a-f0-9]{64}", self.source_sha256) or not re.fullmatch(r"[a-f0-9]{64}", self.binary_sha256):
            raise ValueError("source and binary observations require lowercase SHA-256")
        if not set(_required_capabilities(self.tool)) <= set(self.capabilities):
            raise ValueError("build observation is missing required capability evidence")
        return self


class ListenerObservation(StrictModel):
    role: str = "vm_b"
    tool: str
    address: str
    protocol: str
    port: int
    unit: str
    binary_path: str
    stdout_path: str
    stderr_path: str
    allowed_source: str
    state: Literal["prepared", "active", "mismatch", "failed"]


class BootstrapResult(StrictModel):
    succeeded: bool
    identity: IdentityContext
    deployment_id: str
    actions: List[RemoteActionEvidence] = Field(default_factory=list)
    failure: AccessFailureEvidence | None = None
    builds: List[BuildObservation] = Field(default_factory=list)
    listeners: List[ListenerObservation] = Field(default_factory=list)

    @model_validator(mode="after")
    def successful_result_is_complete(self):
        if self.succeeded:
            if len(self.builds) != 4 or {(b.role, b.tool) for b in self.builds} != {(r, t) for r in ("vm_a", "vm_b") for t in ("sockperf", "iperf3")}:
                raise ValueError("successful preparation requires both tools observed on both VMs")
            if len(self.listeners) != 2 or {(x.tool, x.protocol, x.port) for x in self.listeners} != {("sockperf", "udp", 11111), ("iperf3", "tcp", 5201)}:
                raise ValueError("successful preparation requires both intended persistent listeners")
            if any(x.state != "active" for x in self.listeners):
                raise ValueError("successful preparation requires matching active listeners")
            if any(x.address != self.listeners[0].address for x in self.listeners):
                raise ValueError("listeners must share B's private address")
        return self


def _required_capabilities(tool: str) -> List[str]:
    if tool == "sockperf":
        return ["server", "under-load", "-i", "-p", "-m", "--mps", "--reply-every", "--full-rtt", "--no-rdtsc", "--full-log", "-t"]
    return ["-s", "-c", "-B", "-p", "-P", "-t", "-O", "-i", "-l", "-C", "cubic", "-Z", "-J", "--get-server-output", "--json-stream", "--forceflush"]


def _capabilities_observed(text: str, tool: str) -> bool:
    if not text.strip() or "usage" not in text.lower() or "error" in text.lower() or "unknown option" in text.lower():
        return False
    tokens = set(re.findall(r"--[A-Za-z0-9-]+|-[A-Za-z0-9]|[A-Za-z][A-Za-z0-9-]*", text))
    return set(_required_capabilities(tool)) <= tokens


def _unit_matches(text: str, tool: str, address: str, port: int, prefix: str, stdout_path: str, stderr_path: str) -> bool:
    fields = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    match = re.search(r"argv\[\]=(.*?);", fields.get("ExecStart", ""))
    if not match:
        return False
    try:
        argv = shlex.split(match.group(1))
    except ValueError:
        return False
    binary = f"{prefix}/bin/{tool}"
    expected = [binary, "server", "-i", address, "-p", str(port)] if tool == "sockperf" else [binary, "-s", "-B", address, "-p", str(port), "--json-stream", "--forceflush"]
    return (fields.get("LoadState") == "loaded" and fields.get("ActiveState") == "active"
            and fields.get("MainPID", "0").isdigit() and int(fields.get("MainPID", "0")) > 0
            and argv == expected and fields.get("Restart") == "no"
            and fields.get("StandardOutput") == "append:" + stdout_path
            and fields.get("StandardError") == "append:" + stderr_path)


def _acl_matches(text: str, source: str, address: str) -> bool:
    rules = [re.sub(r"\bcounter packets \d+ bytes \d+\b", "", " ".join(line.strip().split())).strip() for line in text.splitlines() if line.strip()]
    expected = [f"ip saddr {source}/32 ip daddr {address} udp dport 11111 accept",
                f"ip daddr {address} udp dport 11111 drop",
                f"ip saddr {source}/32 ip daddr {address} tcp dport 5201 accept",
                f"ip daddr {address} tcp dport 5201 drop"]
    positions = []
    for rule in expected:
        found = [index for index, line in enumerate(rules) if line == rule]
        if len(found) != 1:
            return False
        positions.append(found[0])
    return positions == sorted(positions) and any("hook input" in line and "policy accept" in line for line in rules)


def _recipe(source: SourceBuild, config: BootstrapConfig) -> List[List[str]]:
    archive = f"{config.workspace}/{source.archive_name}"
    source_dir = f"{config.workspace}/{source.source_reference}"
    prefix = config.install_prefix
    source_dir_q, prefix_q = shlex.quote(source_dir), shlex.quote(prefix)
    binary = f"{prefix}/bin/{source.tool}"
    marker = f"{prefix}/.f02r-{source.tool}.identity"
    marker_q, binary_q = shlex.quote(marker), shlex.quote(binary)
    ref_q, digest_q = shlex.quote(source.source_reference), shlex.quote(source.source_sha256)
    build = "./autogen.sh && " if source.tool == "sockperf" else ""
    build_script = (f"if [ -f {marker_q} ]; then IFS=' ' read -r old_ref old_sha old_bin < {marker_q}; "
                    f"current=$(sha256sum {binary_q}) || exit 42; set -- $current; "
                    f"[ \"$old_ref\" = {ref_q} ] && [ \"$old_sha\" = {digest_q} ] && [ \"$old_bin\" = \"$1\" ] || exit 42; exit 0; fi; "
                    f"[ ! -e {binary_q} ] || exit 43; cd {source_dir_q} && {build}./configure --prefix={prefix_q} && make -j2 && make install && "
                    f"current=$(sha256sum {binary_q}) && set -- $current && printf '%s %s %s\\n' {ref_q} {digest_q} \"$1\" > {marker_q}")
    return [
        ["sudo", "apt-get", "install", "-y", *config.bootstrap_packages],
        ["curl", "--fail", "--location", "--silent", "--show-error", "--output", archive, source.source_url],
        ["sha256sum", archive],
        ["tar", "-xf", archive, "-C", config.workspace],
        ["sh", "-c", build_script],
        ["cc", "--version"],
        [f"{prefix}/bin/{source.tool}", "--version"],
        ["sha256sum", f"{prefix}/bin/{source.tool}"],
        [f"{prefix}/bin/{source.tool}", "--help"],
    ]


def _identity_preflight_script(source: SourceBuild, config: BootstrapConfig) -> str:
    binary = shlex.quote(f"{config.install_prefix}/bin/{source.tool}")
    marker = shlex.quote(f"{config.install_prefix}/.f02r-{source.tool}.identity")
    reference, digest = shlex.quote(source.source_reference), shlex.quote(source.source_sha256)
    return (f"if [ ! -e {marker} ]; then [ ! -e {binary} ] || exit 43; printf 'missing\\n'; exit 0; fi; "
            f"IFS=' ' read -r old_ref old_sha old_bin < {marker}; current=$(sha256sum {binary}) || exit 42; set -- $current; "
            f"[ \"$old_ref\" = {reference} ] && [ \"$old_sha\" = {digest} ] && [ \"$old_bin\" = \"$1\" ] || exit 42; printf 'matching\\n'")


def _sha256sum_output(text: str, expected_path: str) -> str | None:
    lines = text.strip().splitlines()
    if len(lines) != 1:
        return None
    match = re.fullmatch(r"([a-f0-9]{64})\s+\*?(.+)", lines[0].strip())
    return match.group(1) if match and match.group(2) == expected_path else None


def _sha256sum_output(text: str, expected_path: str) -> str | None:
    lines = text.strip().splitlines()
    if len(lines) != 1:
        return None
    match = re.fullmatch(r"([a-f0-9]{64})\s+\*?(.+)", lines[0].strip())
    return match.group(1) if match and match.group(2) == expected_path else None


def render_commands(config: BootstrapConfig, role: str) -> List[List[str]]:
    commands: List[List[str]] = [["cloud-init", "status", "--wait"]]
    for source in config.sources:
        commands.extend(_recipe(source, config))
    return commands


def _failure_result(connection, actions, vm, request, result, category, message, builds, listeners):
    return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id,
                           actions=actions, failure=_failure("bootstrap", vm, category, result, message, request), builds=builds, listeners=listeners)


def run_bootstrap(connection: ConnectionOutput, config: BootstrapConfig, runner: CommandRunner, *, deadline: RemoteDeadline, clock: Callable[[], float] = time.monotonic) -> BootstrapResult:
    actions: List[RemoteActionEvidence] = []
    builds: List[BuildObservation] = []
    listeners: List[ListenerObservation] = []
    sources = {source.tool: source for source in config.sources}
    tool_index = 0
    for vm, role in ((connection.vm_a, "vm_a"), (connection.vm_b, "vm_b")):
        init = [(["cloud-init", "status", "--wait"], "cloud_init_seconds", "cloud_init")]
        if deadline.remaining(clock, readiness=True) <= 0:
            return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, failure=AccessFailureEvidence(stage="bootstrap", role=role, category="readiness_deadline_exhausted", message="no time remains before remote preparation", occurred_at=datetime.now(timezone.utc)), builds=builds, listeners=listeners)
        for argv, timeout_name, kind in init:
            timeout = min(getattr(connection.timeouts, timeout_name), deadline.remaining(clock, readiness=True))
            req = _request(f"f02r-{role}-cloud-init", vm, argv, timeout)
            result = _execute(runner, req)
            actions.append(_action(req.action_id, "ssh", vm, req, result))
            if result.outcome != CommandOutcome.SUCCEEDED or "done" not in result.stdout.lower():
                return _failure_result(connection, actions, vm, req, result, "cloud_init_failed", "cloud-init completion was not observed", builds, listeners)
        if role == "vm_b":
            for tool, port, stdout_path, stderr_path in (("sockperf", 11111, "server-logs/sockperf.log", "server-logs/sockperf.err"), ("iperf3", 5201, "server-logs/iperf3-server.jsonl", "server-logs/iperf3-server.err")):
                remaining = deadline.remaining(clock, readiness=True)
                if remaining <= 0:
                    return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, failure=AccessFailureEvidence(stage="bootstrap", role=role, category="readiness_deadline_exhausted", message="preparation deadline exhausted before existing listener inspection", occurred_at=datetime.now(timezone.utc)), builds=builds, listeners=listeners)
                inspect_id = f"f02r-vm_b-preflight-listener-inspect-{tool}"
                inspect_request = _request(inspect_id, vm, ["systemctl", "show", f"cnb-{connection.deployment_id}-{tool}", "--property=LoadState,ActiveState,MainPID,ExecStart,StandardOutput,StandardError,Restart"], min(connection.timeouts.server_seconds, remaining))
                observed = _execute(runner, inspect_request)
                actions.append(_action(inspect_id, "ssh", vm, inspect_request, observed))
                if observed.outcome == CommandOutcome.SUCCEEDED:
                    load_state = dict(line.split("=", 1) for line in observed.stdout.splitlines() if "=" in line).get("LoadState")
                    if load_state != "not-found" and not _unit_matches(observed.stdout, tool, connection.private_ipv4_b, port, config.install_prefix, f"{config.workspace}/{stdout_path}", f"{config.workspace}/{stderr_path}"):
                        return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, failure=AccessFailureEvidence(stage="bootstrap", role=role, category="listener_state_mismatch", message=f"existing {tool} listener conflicts before build preparation", action_id=inspect_id, occurred_at=observed.finished_at), builds=builds, listeners=listeners)
                elif observed.outcome != CommandOutcome.FAILED or not any(text in (observed.stdout + observed.stderr).lower() for text in ("not found", "no such file", "could not be found")):
                    return _failure_result(connection, actions, vm, inspect_request, observed, "listener_inspection_failed", f"could not inspect existing {tool} listener", builds, listeners)
        for source in config.sources:
            remaining = deadline.remaining(clock, readiness=True)
            if remaining <= 0:
                return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, failure=AccessFailureEvidence(stage="bootstrap", role=role, category="readiness_deadline_exhausted", message="preparation deadline exhausted before workspace setup", occurred_at=datetime.now(timezone.utc)), builds=builds, listeners=listeners)
            identity_request = _request(f"f02r-{role}-{source.tool}-identity-preflight", vm, ["sh", "-c", _identity_preflight_script(source, config)], min(connection.timeouts.command_seconds, remaining))
            identity_result = _execute(runner, identity_request)
            actions.append(_action(identity_request.action_id, "ssh", vm, identity_request, identity_result))
            if identity_result.outcome != CommandOutcome.SUCCEEDED:
                return _failure_result(connection, actions, vm, identity_request, identity_result, "installed_identity_mismatch", f"existing {source.tool} source or binary identity conflicts with requested build", builds, listeners)
            remaining = deadline.remaining(clock, readiness=True)
            if remaining <= 0:
                return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, failure=AccessFailureEvidence(stage="bootstrap", role=role, category="readiness_deadline_exhausted", message="preparation deadline exhausted before workspace setup", occurred_at=datetime.now(timezone.utc)), builds=builds, listeners=listeners)
            mkdir_script = f"sudo install -d -o \"$(id -un)\" -g \"$(id -gn)\" -m 0750 {shlex.quote(config.workspace)} {shlex.quote(config.install_prefix)}"
            workspace_request = _request(f"f02r-{role}-{source.tool}-workspace", vm, ["sh", "-c", mkdir_script], min(connection.timeouts.command_seconds, remaining))
            try:
                workspace_result = _execute(runner, workspace_request)
            except Exception as exc:
                failure = AccessFailureEvidence(stage="bootstrap", role=role, category="workspace_setup_failed", message=type(exc).__name__, action_id=workspace_request.action_id, occurred_at=datetime.now(timezone.utc))
                return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, failure=failure, builds=builds, listeners=listeners)
            actions.append(_action(workspace_request.action_id, "ssh", vm, workspace_request, workspace_result))
            if workspace_result.outcome != CommandOutcome.SUCCEEDED:
                return _failure_result(connection, actions, vm, workspace_request, workspace_result, "workspace_setup_failed", "could not prepare writable deployment paths", builds, listeners)
            recipe = _recipe(source, config)
            evidence: Dict[str, str] = {}
            for index, argv in enumerate(recipe):
                remaining = deadline.remaining(clock, readiness=True)
                if remaining <= 0:
                    return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, failure=AccessFailureEvidence(stage="bootstrap", role=role, category="readiness_deadline_exhausted", message="preparation deadline exhausted", occurred_at=datetime.now(timezone.utc)), builds=builds, listeners=listeners)
                timeout = min(connection.timeouts.command_seconds, remaining)
                aid = f"f02r-{role}-{source.tool}-{index}"
                req = _request(aid, vm, argv, timeout)
                result = _execute(runner, req)
                actions.append(_action(aid, "ssh", vm, req, result))
                if result.outcome != CommandOutcome.SUCCEEDED:
                    return _failure_result(connection, actions, vm, req, result, "source_build_failed", f"{source.tool} build action failed", builds, listeners)
                text = result.stdout.strip()
                if index == 2:
                    observed_hash = _sha256sum_output(text, f"{config.workspace}/{source.archive_name}")
                    if observed_hash != source.source_sha256:
                        return _failure_result(connection, actions, vm, req, result, "source_digest_mismatch", f"{source.tool} source archive digest did not match", builds, listeners)
                    evidence["source_sha256"] = observed_hash
                elif index == 5:
                    if not text or not re.search(r"(?i)\b(gcc|clang|gnu compiler)\b", text):
                        return _failure_result(connection, actions, vm, req, result, "compiler_identity_missing", "compiler version was not observed", builds, listeners)
                    evidence["compiler_version"] = text
                elif index == 6:
                    if source.tool not in text.lower():
                        return _failure_result(connection, actions, vm, req, result, "binary_version_missing", f"{source.tool} binary version was not observed", builds, listeners)
                    evidence["binary_version"] = text
                elif index == 7:
                    observed_hash = _sha256sum_output(text, f"{config.install_prefix}/bin/{source.tool}")
                    if not observed_hash:
                        return _failure_result(connection, actions, vm, req, result, "binary_hash_missing", f"{source.tool} binary hash was not observed", builds, listeners)
                    evidence["binary_sha256"] = observed_hash
                elif index == 8:
                    if not _capabilities_observed(text, source.tool):
                        return _failure_result(connection, actions, vm, req, result, "capability_missing", f"{source.tool} required capability help was incomplete", builds, listeners)
                    evidence["capabilities"] = text
            binary_path = f"{config.install_prefix}/bin/{source.tool}"
            builds.append(BuildObservation(role=role, tool=source.tool, source_reference=source.source_reference,
                                           source_sha256=evidence["source_sha256"], compiler_version=evidence["compiler_version"],
                                           binary_path=binary_path, binary_version=evidence["binary_version"], binary_sha256=evidence["binary_sha256"],
                                           capabilities=_required_capabilities(source.tool)))
        if role == "vm_b":
            # Establish the private-only ingress chain before starting either listener.
            chain = f"cnb_{re.sub(r'[^A-Za-z0-9_]', '_', connection.deployment_id)[:32]}"
            pre_id = "f02r-vm_b-acl-preflight-inspect"
            pre_remaining = deadline.remaining(clock, readiness=True)
            if pre_remaining <= 0:
                return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, failure=AccessFailureEvidence(stage="bootstrap", role=role, category="readiness_deadline_exhausted", message="preparation deadline exhausted before guest restriction", occurred_at=datetime.now(timezone.utc)), builds=builds, listeners=listeners)
            pre_request = _request(pre_id, vm, ["sudo", "nft", "list", "chain", "inet", "cnb_f02r", chain], min(connection.timeouts.command_seconds, pre_remaining))
            pre_result = _execute(runner, pre_request)
            actions.append(_action(pre_id, "ssh", vm, pre_request, pre_result))
            if pre_result.outcome == CommandOutcome.SUCCEEDED:
                if not _acl_matches(pre_result.stdout, connection.private_ipv4_a, connection.private_ipv4_b):
                    return _failure_result(connection, actions, vm, pre_request, pre_result, "listener_acl_mismatch", "existing guest benchmark ACL does not match this deployment", builds, listeners)
            elif pre_result.outcome == CommandOutcome.FAILED and any(text in (pre_result.stdout + pre_result.stderr).lower() for text in ("no such file", "does not exist", "not found")):
                rules = [
                    ["sudo", "nft", "-exist", "add", "table", "inet", "cnb_f02r"],
                    ["sudo", "nft", "-exist", "add", "chain", "inet", "cnb_f02r", chain, "{", "type", "filter", "hook", "input", "priority", "-10", ";", "policy", "accept", ";", "}"],
                    ["sudo", "nft", "-exist", "add", "rule", "inet", "cnb_f02r", chain, "ip", "saddr", f"{connection.private_ipv4_a}/32", "ip", "daddr", connection.private_ipv4_b, "udp", "dport", "11111", "accept"],
                    ["sudo", "nft", "-exist", "add", "rule", "inet", "cnb_f02r", chain, "ip", "daddr", connection.private_ipv4_b, "udp", "dport", "11111", "drop"],
                    ["sudo", "nft", "-exist", "add", "rule", "inet", "cnb_f02r", chain, "ip", "saddr", f"{connection.private_ipv4_a}/32", "ip", "daddr", connection.private_ipv4_b, "tcp", "dport", "5201", "accept"],
                    ["sudo", "nft", "-exist", "add", "rule", "inet", "cnb_f02r", chain, "ip", "daddr", connection.private_ipv4_b, "tcp", "dport", "5201", "drop"],
                ]
                for index, argv in enumerate(rules):
                    remaining = deadline.remaining(clock, readiness=True)
                    if remaining <= 0:
                        return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, failure=AccessFailureEvidence(stage="bootstrap", role=role, category="readiness_deadline_exhausted", message="preparation deadline exhausted while restricting guest ingress", occurred_at=datetime.now(timezone.utc)), builds=builds, listeners=listeners)
                    request = _request(f"f02r-vm_b-acl-pre-create-{index}", vm, argv, min(connection.timeouts.command_seconds, remaining))
                    result = _execute(runner, request)
                    actions.append(_action(request.action_id, "ssh", vm, request, result))
                    if result.outcome != CommandOutcome.SUCCEEDED:
                        return _failure_result(connection, actions, vm, request, result, "listener_acl_failed", "guest benchmark ingress restriction failed", builds, listeners)
            else:
                return _failure_result(connection, actions, vm, pre_request, pre_result, "listener_acl_inspection_failed", "could not inspect guest benchmark ACL", builds, listeners)
            for tool, protocol, port, stdout_path, stderr_path in (("sockperf", "udp", 11111, "server-logs/sockperf.log", "server-logs/sockperf.err"), ("iperf3", "tcp", 5201, "server-logs/iperf3-server.jsonl", "server-logs/iperf3-server.err")):
                prefix = config.install_prefix
                address = connection.private_ipv4_b
                server_argv = [f"{prefix}/bin/{tool}", *( ["server", "-i", address, "-p", str(port)] if tool == "sockperf" else ["-s", "-B", address, "-p", str(port), "--json-stream", "--forceflush"])]
                unit = f"cnb-{connection.deployment_id}-{tool}"
                inspect_id = f"f02r-vm_b-listener-inspect-{tool}"
                remaining = deadline.remaining(clock, readiness=True)
                if remaining <= 0:
                    return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, failure=AccessFailureEvidence(stage="bootstrap", role=role, category="readiness_deadline_exhausted", message="preparation deadline exhausted before listener inspection", occurred_at=datetime.now(timezone.utc)), builds=builds, listeners=listeners)
                inspect_request = _request(inspect_id, vm, ["systemctl", "show", unit, "--property=LoadState,ActiveState,ExecStart,StandardOutput,StandardError,Restart"], min(connection.timeouts.server_seconds, remaining))
                inspect_result = _execute(runner, inspect_request)
                actions.append(_action(inspect_id, "ssh", vm, inspect_request, inspect_result))
                if inspect_result.outcome == CommandOutcome.SUCCEEDED:
                    output = inspect_result.stdout
                    fields = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
                    if fields.get("LoadState") == "not-found":
                        pass
                    elif _unit_matches(output, tool, address, port, prefix, f"{config.workspace}/{stdout_path}", f"{config.workspace}/{stderr_path}"):
                        listeners.append(ListenerObservation(tool=tool, address=address, protocol=protocol, port=port,
                            unit=unit, binary_path=f"{prefix}/bin/{tool}", stdout_path=f"{config.workspace}/{stdout_path}",
                            stderr_path=f"{config.workspace}/{stderr_path}", allowed_source=f"{connection.private_ipv4_a}/32", state="active"))
                        continue
                    elif fields.get("LoadState") != "not-found":
                        return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions,
                            failure=AccessFailureEvidence(stage="bootstrap", role=role, category="listener_state_mismatch", message=f"existing {tool} unit does not match the requested listener", occurred_at=inspect_result.finished_at), builds=builds, listeners=listeners)
                elif inspect_result.outcome != CommandOutcome.FAILED or not any(text in (inspect_result.stdout + inspect_result.stderr).lower() for text in ("not found", "no such file", "could not be found")):
                    return _failure_result(connection, actions, vm, inspect_request, inspect_result, "listener_inspection_failed", f"could not inspect existing {tool} listener", builds, listeners)
                stdout_abs = f"{config.workspace}/{stdout_path}"
                stderr_abs = f"{config.workspace}/{stderr_path}"
                mkdir_id = f"f02r-vm_b-listener-log-directory-{tool}"
                mkdir_remaining = deadline.remaining(clock, readiness=True)
                if mkdir_remaining <= 0:
                    return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, failure=AccessFailureEvidence(stage="bootstrap", role=role, category="readiness_deadline_exhausted", message="preparation deadline exhausted before log setup", occurred_at=datetime.now(timezone.utc)), builds=builds, listeners=listeners)
                mkdir_request = _request(mkdir_id, vm, ["sudo", "install", "-d", "-m", "0750", str(PurePosixPath(stdout_abs).parent)], min(connection.timeouts.command_seconds, mkdir_remaining))
                mkdir_result = _execute(runner, mkdir_request)
                actions.append(_action(mkdir_id, "ssh", vm, mkdir_request, mkdir_result))
                if mkdir_result.outcome != CommandOutcome.SUCCEEDED:
                    return _failure_result(connection, actions, vm, mkdir_request, mkdir_result, "listener_log_setup_failed", f"could not prepare {tool} log destination", builds, listeners)
                command = ["sudo", "systemd-run", "--unit", unit, "--property=Restart=no", "--property=StandardOutput=append:" + stdout_abs, "--property=StandardError=append:" + stderr_abs, *server_argv]
                aid = f"f02r-vm_b-listener-{tool}"
                remaining = deadline.remaining(clock, readiness=True)
                if remaining <= 0:
                    return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, failure=AccessFailureEvidence(stage="bootstrap", role=role, category="readiness_deadline_exhausted", message="preparation deadline exhausted before listeners", occurred_at=datetime.now(timezone.utc)), builds=builds, listeners=listeners)
                req = _request(aid, vm, command, min(connection.timeouts.server_seconds, remaining))
                result = _execute(runner, req)
                actions.append(_action(aid, "ssh", vm, req, result))
                if result.outcome != CommandOutcome.SUCCEEDED or not re.search(r"(?im)^active:\s+active\s+\(running\)\s*$", result.stdout):
                    return _failure_result(connection, actions, vm, req, result, "listener_setup_failed", f"{tool} listener was not observed", builds, listeners)
                verify_id = f"f02r-vm_b-listener-verify-{tool}"
                verify_remaining = deadline.remaining(clock, readiness=True)
                if verify_remaining <= 0:
                    return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, failure=AccessFailureEvidence(stage="bootstrap", role=role, category="readiness_deadline_exhausted", message="preparation deadline exhausted before listener identity verification", occurred_at=datetime.now(timezone.utc)), builds=builds, listeners=listeners)
                verify_request = _request(verify_id, vm, ["systemctl", "show", unit, "--property=LoadState,ActiveState,MainPID,ExecStart,StandardOutput,StandardError,Restart"], min(connection.timeouts.server_seconds, verify_remaining))
                verify_result = _execute(runner, verify_request)
                actions.append(_action(verify_id, "ssh", vm, verify_request, verify_result))
                if verify_result.outcome != CommandOutcome.SUCCEEDED or not _unit_matches(verify_result.stdout, tool, address, port, prefix, stdout_abs, stderr_abs):
                    return _failure_result(connection, actions, vm, verify_request, verify_result, "listener_identity_unverified", f"{tool} process or listener identity was not observed", builds, listeners)
                listeners.append(ListenerObservation(tool=tool, address=address, protocol=protocol, port=port,
                    unit=unit, binary_path=f"{prefix}/bin/{tool}", stdout_path=stdout_abs,
                    stderr_path=stderr_abs, allowed_source=f"{connection.private_ipv4_a}/32", state="active"))
            chain = f"cnb_{re.sub(r'[^A-Za-z0-9_]', '_', connection.deployment_id)[:32]}"
            inspect_id = "f02r-vm_b-acl-inspect"
            remaining = deadline.remaining(clock, readiness=True)
            if remaining <= 0:
                return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, failure=AccessFailureEvidence(stage="bootstrap", role=role, category="readiness_deadline_exhausted", message="preparation deadline exhausted before ACL inspection", occurred_at=datetime.now(timezone.utc)), builds=builds, listeners=listeners)
            inspect = _request(inspect_id, vm, ["sudo", "nft", "list", "chain", "inet", "cnb_f02r", chain], min(connection.timeouts.command_seconds, remaining))
            acl_result = _execute(runner, inspect)
            actions.append(_action(inspect_id, "ssh", vm, inspect, acl_result))
            expected_acl = [f"{connection.private_ipv4_a}/32", connection.private_ipv4_b, "11111", "5201", "drop", "hook input"]
            if acl_result.outcome == CommandOutcome.SUCCEEDED:
                if not _acl_matches(acl_result.stdout, connection.private_ipv4_a, connection.private_ipv4_b):
                    return _failure_result(connection, actions, vm, inspect, acl_result, "listener_acl_mismatch", "existing guest benchmark ACL does not match this deployment", builds, listeners)
            elif acl_result.outcome == CommandOutcome.FAILED and any(text in (acl_result.stdout + acl_result.stderr).lower() for text in ("no such file", "does not exist", "not found")):
                rules = [
                    ["sudo", "nft", "-exist", "add", "table", "inet", "cnb_f02r"],
                    ["sudo", "nft", "-exist", "add", "chain", "inet", "cnb_f02r", chain, "{", "type", "filter", "hook", "input", "priority", "-10", ";", "policy", "accept", ";", "}"],
                    ["sudo", "nft", "-exist", "add", "rule", "inet", "cnb_f02r", chain, "ip", "saddr", f"{connection.private_ipv4_a}/32", "ip", "daddr", connection.private_ipv4_b, "udp", "dport", "11111", "accept"],
                    ["sudo", "nft", "-exist", "add", "rule", "inet", "cnb_f02r", chain, "ip", "daddr", connection.private_ipv4_b, "udp", "dport", "11111", "drop"],
                    ["sudo", "nft", "-exist", "add", "rule", "inet", "cnb_f02r", chain, "ip", "saddr", f"{connection.private_ipv4_a}/32", "ip", "daddr", connection.private_ipv4_b, "tcp", "dport", "5201", "accept"],
                    ["sudo", "nft", "-exist", "add", "rule", "inet", "cnb_f02r", chain, "ip", "daddr", connection.private_ipv4_b, "tcp", "dport", "5201", "drop"],
                ]
                for index, argv in enumerate(rules):
                    aid = f"f02r-vm_b-acl-create-{index}"
                    remaining = deadline.remaining(clock, readiness=True)
                    if remaining <= 0:
                        return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, failure=AccessFailureEvidence(stage="bootstrap", role=role, category="readiness_deadline_exhausted", message="preparation deadline exhausted while applying ACL", occurred_at=datetime.now(timezone.utc)), builds=builds, listeners=listeners)
                    request = _request(aid, vm, argv, min(connection.timeouts.command_seconds, remaining))
                    result = _execute(runner, request)
                    actions.append(_action(aid, "ssh", vm, request, result))
                    if result.outcome != CommandOutcome.SUCCEEDED:
                        return _failure_result(connection, actions, vm, request, result, "listener_acl_failed", "guest benchmark ingress restriction failed", builds, listeners)
            else:
                return _failure_result(connection, actions, vm, inspect, acl_result, "listener_acl_inspection_failed", "could not inspect guest benchmark ACL", builds, listeners)
    if deadline.remaining(clock, readiness=True) <= 0:
        return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions,
                               failure=AccessFailureEvidence(stage="bootstrap", category="readiness_deadline_exhausted", message="preparation completed after the original deadline", occurred_at=datetime.now(timezone.utc)), builds=builds, listeners=listeners)
    if deadline.remaining(clock, readiness=True) <= 0:
        return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions,
                               failure=AccessFailureEvidence(stage="bootstrap", category="readiness_deadline_exhausted", message="preparation completed after the original deadline", occurred_at=datetime.now(timezone.utc)), builds=builds, listeners=listeners)
    return BootstrapResult(succeeded=True, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, builds=builds, listeners=listeners)
