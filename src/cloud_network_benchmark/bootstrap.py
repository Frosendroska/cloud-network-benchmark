from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import math
import re
import time
from typing import Callable, Dict, List, Literal, Optional
from urllib.parse import urlparse
from pathlib import PurePosixPath

from pydantic import Field, field_validator, model_validator

from .access import AccessFailureEvidence, ConnectionOutput, RemoteActionEvidence, RemoteDeadline, _action, _failure, _request
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


def _recipe(source: SourceBuild, config: BootstrapConfig) -> List[List[str]]:
    archive = f"{config.workspace}/{source.archive_name}"
    source_dir = f"{config.workspace}/{source.source_reference}"
    prefix = config.install_prefix
    return [
        ["sudo", "apt-get", "install", "-y", *config.bootstrap_packages],
        ["curl", "--fail", "--location", "--silent", "--show-error", "--output", archive, source.source_url],
        ["sha256sum", archive],
        ["tar", "-xf", archive, "-C", config.workspace],
        ["sh", "-c", f"cd {source_dir} && ./autogen.sh && ./configure --prefix={prefix} && make -j2 && make install"] if source.tool == "sockperf" else ["sh", "-c", f"cd {source_dir} && ./configure --prefix={prefix} && make -j2 && make install"],
        ["cc", "--version"],
        [f"{prefix}/bin/{source.tool}", "--version"],
        ["sha256sum", f"{prefix}/bin/{source.tool}"],
        [f"{prefix}/bin/{source.tool}", "--help"],
    ]


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
            result = runner.run(req)
            actions.append(_action(req.action_id, "ssh", vm, req, result))
            if result.outcome != CommandOutcome.SUCCEEDED or "done" not in result.stdout.lower():
                return _failure_result(connection, actions, vm, req, result, "cloud_init_failed", "cloud-init completion was not observed", builds, listeners)
        for source in config.sources:
            recipe = _recipe(source, config)
            evidence: Dict[str, str] = {}
            for index, argv in enumerate(recipe):
                remaining = deadline.remaining(clock, readiness=True)
                if remaining <= 0:
                    return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, failure=AccessFailureEvidence(stage="bootstrap", role=role, category="readiness_deadline_exhausted", message="preparation deadline exhausted", occurred_at=datetime.now(timezone.utc)), builds=builds, listeners=listeners)
                timeout = min(connection.timeouts.command_seconds, remaining)
                aid = f"f02r-{role}-{source.tool}-{index}"
                req = _request(aid, vm, argv, timeout)
                result = runner.run(req)
                actions.append(_action(aid, "ssh", vm, req, result))
                if result.outcome != CommandOutcome.SUCCEEDED:
                    return _failure_result(connection, actions, vm, req, result, "source_build_failed", f"{source.tool} build action failed", builds, listeners)
                text = result.stdout.strip()
                if index == 2:
                    match = re.search(r"\b([a-f0-9]{64})\b", text)
                    if not match or match.group(1) != source.source_sha256:
                        return _failure_result(connection, actions, vm, req, result, "source_digest_mismatch", f"{source.tool} source archive digest did not match", builds, listeners)
                    evidence["source_sha256"] = match.group(1)
                elif index == 5:
                    evidence["compiler_version"] = text
                elif index == 6:
                    evidence["binary_version"] = text
                elif index == 7:
                    match = re.search(r"\b([a-f0-9]{64})\b", text)
                    if not match:
                        return _failure_result(connection, actions, vm, req, result, "binary_hash_missing", f"{source.tool} binary hash was not observed", builds, listeners)
                    evidence["binary_sha256"] = match.group(1)
                elif index == 8:
                    missing = [cap for cap in _required_capabilities(source.tool) if cap not in text]
                    if missing:
                        return _failure_result(connection, actions, vm, req, result, "capability_missing", f"{source.tool} required capability help was incomplete", builds, listeners)
                    evidence["capabilities"] = text
            binary_path = f"{config.install_prefix}/bin/{source.tool}"
            builds.append(BuildObservation(role=role, tool=source.tool, source_reference=source.source_reference,
                                           source_sha256=evidence["source_sha256"], compiler_version=evidence["compiler_version"],
                                           binary_path=binary_path, binary_version=evidence["binary_version"], binary_sha256=evidence["binary_sha256"],
                                           capabilities=_required_capabilities(source.tool)))
        if role == "vm_b":
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
                inspect_result = runner.run(inspect_request)
                actions.append(_action(inspect_id, "ssh", vm, inspect_request, inspect_result))
                if inspect_result.outcome == CommandOutcome.SUCCEEDED:
                    output = inspect_result.stdout
                    fields = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
                    if fields.get("LoadState") == "not-found":
                        pass
                    elif (fields.get("LoadState") == "loaded" and fields.get("ActiveState") == "active"
                          and f"{prefix}/bin/{tool}" in fields.get("ExecStart", "")
                          and address in fields.get("ExecStart", "") and str(port) in fields.get("ExecStart", "")
                          and fields.get("Restart") == "no"
                          and f"{config.workspace}/{stdout_path}" in fields.get("StandardOutput", "")
                          and f"{config.workspace}/{stderr_path}" in fields.get("StandardError", "")):
                        listeners.append(ListenerObservation(tool=tool, address=address, protocol=protocol, port=port,
                            unit=unit, binary_path=f"{prefix}/bin/{tool}", stdout_path=stdout_path,
                            stderr_path=stderr_path, allowed_source=f"{connection.private_ipv4_a}/32", state="active"))
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
                mkdir_result = runner.run(mkdir_request)
                actions.append(_action(mkdir_id, "ssh", vm, mkdir_request, mkdir_result))
                if mkdir_result.outcome != CommandOutcome.SUCCEEDED:
                    return _failure_result(connection, actions, vm, mkdir_request, mkdir_result, "listener_log_setup_failed", f"could not prepare {tool} log destination", builds, listeners)
                command = ["sudo", "systemd-run", "--unit", unit, "--property=Restart=no", "--property=StandardOutput=append:" + stdout_abs, "--property=StandardError=append:" + stderr_abs, *server_argv]
                aid = f"f02r-vm_b-listener-{tool}"
                remaining = deadline.remaining(clock, readiness=True)
                if remaining <= 0:
                    return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, failure=AccessFailureEvidence(stage="bootstrap", role=role, category="readiness_deadline_exhausted", message="preparation deadline exhausted before listeners", occurred_at=datetime.now(timezone.utc)), builds=builds, listeners=listeners)
                req = _request(aid, vm, command, min(connection.timeouts.server_seconds, remaining))
                result = runner.run(req)
                actions.append(_action(aid, "ssh", vm, req, result))
                if result.outcome != CommandOutcome.SUCCEEDED or ("active" not in result.stdout.lower() and "started" not in result.stdout.lower()):
                    return _failure_result(connection, actions, vm, req, result, "listener_setup_failed", f"{tool} listener was not observed", builds, listeners)
                listeners.append(ListenerObservation(tool=tool, address=address, protocol=protocol, port=port,
                    unit=unit, binary_path=f"{prefix}/bin/{tool}", stdout_path=stdout_path,
                    stderr_path=stderr_path, allowed_source=f"{connection.private_ipv4_a}/32", state="active"))
            chain = f"cnb_{re.sub(r'[^A-Za-z0-9_]', '_', connection.deployment_id)[:32]}"
            inspect_id = "f02r-vm_b-acl-inspect"
            remaining = deadline.remaining(clock, readiness=True)
            if remaining <= 0:
                return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, failure=AccessFailureEvidence(stage="bootstrap", role=role, category="readiness_deadline_exhausted", message="preparation deadline exhausted before ACL inspection", occurred_at=datetime.now(timezone.utc)), builds=builds, listeners=listeners)
            inspect = _request(inspect_id, vm, ["sudo", "nft", "list", "chain", "inet", "cnb_f02r", chain], min(connection.timeouts.command_seconds, remaining))
            acl_result = runner.run(inspect)
            actions.append(_action(inspect_id, "ssh", vm, inspect, acl_result))
            expected_acl = [f"{connection.private_ipv4_a}/32", connection.private_ipv4_b, "11111", "5201", "drop", "hook input"]
            if acl_result.outcome == CommandOutcome.SUCCEEDED:
                if not all(value in acl_result.stdout for value in expected_acl):
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
                    result = runner.run(request)
                    actions.append(_action(aid, "ssh", vm, request, result))
                    if result.outcome != CommandOutcome.SUCCEEDED:
                        return _failure_result(connection, actions, vm, request, result, "listener_acl_failed", "guest benchmark ingress restriction failed", builds, listeners)
            else:
                return _failure_result(connection, actions, vm, inspect, acl_result, "listener_acl_inspection_failed", "could not inspect guest benchmark ACL", builds, listeners)
    return BootstrapResult(succeeded=True, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, builds=builds, listeners=listeners)
