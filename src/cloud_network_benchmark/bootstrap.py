from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import base64
import math
import re
import shlex
import time
from typing import Callable, Dict, List, Literal, Optional
from urllib.parse import urlparse
from pathlib import Path, PurePosixPath

from pydantic import Field, field_validator, model_validator

from .access import AccessFailureEvidence, ConnectionOutput, RemoteActionEvidence, RemoteDeadline, _action, _execute as _run_command, _failure, _identity_references_available, _request as _build_request, _request_failure
from .contracts.common import CommandOutcome, IdentityContext, StrictModel
from .contracts.execution import CommandRequest, CommandRunner
from .contracts.artifacts import ArtifactRecord
from .contracts.common import Evidence, StringEvidence, VmRole


def _request(action_id, connection, argv, timeout, action_type="ssh") -> CommandRequest:
    """Keep local request-construction failures inside the action evidence stream."""
    try:
        return _build_request(action_id, connection, argv, timeout, action_type)
    except Exception:
        safe_timeout = timeout if math.isfinite(timeout) and timeout > 0 else 0.001
        return CommandRequest(action_id=action_id, action_type=action_type,
            argv=["scp" if action_type == "scp" else "ssh", "[request-construction-failed]"], cwd=".",
            environment_classification="contains_secret_references", timeout_seconds=safe_timeout,
            redactions=[connection.authentication_reference, connection.host_key_reference])


def _execute(runner: CommandRunner, request: CommandRequest):
    if request.argv and request.argv[-1] == "[request-construction-failed]":
        from datetime import datetime, timezone
        from .contracts.common import CommandOutcome
        from .contracts.execution import CommandResult
        now = datetime.now(timezone.utc)
        return CommandResult(action_id=request.action_id, outcome=CommandOutcome.FAILED, started_at=now,
            finished_at=now, duration_seconds=0, exit_code=1, stdout="",
            stderr="request construction failed: authenticated transport preflight", redactions=request.redactions)
    return _run_command(runner, request)


class SourceBuild(StrictModel):
    tool: Literal["sockperf", "iperf3"]
    source_url: str = Field(min_length=1)
    source_reference: str = Field(min_length=1)
    archive_name: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    observation_patch_path: str | None = None
    observation_patch_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    upstream_commit: str | None = Field(default=None, pattern=r"^[a-f0-9]{40}$")

    @model_validator(mode="after")
    def safe_source(self):
        parsed = urlparse(self.source_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("source URL must be credential-free HTTPS")
        for name, value in (("source_reference", self.source_reference), ("archive_name", self.archive_name)):
            if value.startswith("-") or "/" in value or "\\" in value or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}", value):
                raise ValueError(f"{name} must be a safe identifier or basename")
        if (self.observation_patch_path is None) != (self.observation_patch_sha256 is None):
            raise ValueError("observation patch path and digest must be supplied together")
        if self.observation_patch_path is not None and (
            self.observation_patch_path.startswith("/") or ".." in PurePosixPath(self.observation_patch_path).parts
            or "\\" in self.observation_patch_path
        ):
            raise ValueError("observation patch path must be a safe package-relative path")
        pinned = {
            "5ebd327da983225321818c0355db922515e026bd": (
                "tool_patches/sockperf-3.10-acquisition.patch",
                "3f0bb59a3c70b247e5ebde77254e1427c35dc6ec39e2a78a3c779cff7015a285",
                "a35a1daf310551b9f675af4fb7d95a6a10389fda4c7605d70a21554be0773f8e", "sockperf-"),
            "2acfcfe94e928e74542c9f107e02aa6dd4748a79": (
                "tool_patches/iperf-3.17.1-acquisition.patch",
                "11b96daadb4a1f17dc24376bb3d0d7feec330efeedc550b1979b1566e9b81661",
                "d6b4d2c7b20157c5b7fbd2de66f22bd3428958b5f2e28899151b6e22745886fe", "iperf-"),
        }
        expected_prefix = "sockperf-" if self.tool == "sockperf" else "iperf-"
        pinned_reference = next((commit for commit, values in pinned.items()
                                 if values[3] == expected_prefix and self.source_reference == values[3] + commit), None)
        if pinned_reference is not None and self.upstream_commit != pinned_reference:
            raise ValueError("pinned source reference requires its exact observation source identity")
        if self.upstream_commit in pinned:
            expected_path, expected_digest, expected_source_digest, prefix = pinned[self.upstream_commit]
            if self.observation_patch_path != expected_path or self.observation_patch_sha256 != expected_digest:
                raise ValueError("pinned upstream source requires its exact packaged observation patch identity")
            if self.source_reference != prefix + self.upstream_commit:
                raise ValueError("pinned source archive extraction directory does not match its upstream commit")
            if self.source_sha256 != expected_source_digest:
                raise ValueError("pinned upstream source requires the official archive digest")
        elif self.observation_patch_path is not None:
            raise ValueError("observation patching is supported only for the pinned upstream commits")
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
    artifact_id: str | None = None
    upstream_commit: str | None = None
    patch_path: str | None = None
    patch_sha256: str | None = None
    build_configuration: str | None = None
    observation_capability: str | None = None
    observation_semantics: str | None = None
    identity_bytes: str | None = None

    @model_validator(mode="after")
    def complete_observation(self):
        if not all((self.source_reference, self.compiler_version.strip(), self.binary_path.startswith("/"), self.binary_version.strip())):
            raise ValueError("build observation must retain actual source/compiler/binary evidence")
        if not re.fullmatch(r"[a-f0-9]{64}", self.source_sha256) or not re.fullmatch(r"[a-f0-9]{64}", self.binary_sha256):
            raise ValueError("source and binary observations require lowercase SHA-256")
        if not set(_required_capabilities(self.tool)) <= set(self.capabilities):
            raise ValueError("build observation is missing required capability evidence")
        observed_fields = (self.upstream_commit, self.patch_path, self.patch_sha256,
                           self.build_configuration, self.observation_capability, self.observation_semantics)
        if any(value is not None for value in observed_fields) and not all(value is not None for value in observed_fields):
            raise ValueError("observation build identity must be complete or unavailable")
        if self.observation_capability is not None:
            expected = "sockperf-3.10-enabled-v1" if self.tool == "sockperf" else "iperf-3.17.1-stats-v1"
            if self.observation_capability != "CNB_ACQ_V1" or self.observation_semantics != expected:
                raise ValueError("observation build capability does not match tool semantics")
            if not re.fullmatch(r"[a-f0-9]{40}", self.upstream_commit or "") or not re.fullmatch(r"[a-f0-9]{64}", self.patch_sha256 or ""):
                raise ValueError("observation build requires pinned upstream commit and patch digest")
            if not self.identity_bytes:
                raise ValueError("observation build requires captured original build identity sidecar bytes")
        return self


def materialize_build_identity_artifacts(root: Path, deployment_id: str,
        builds: List[BuildObservation] | tuple[BuildObservation, ...]) -> tuple[ArtifactRecord, ...]:
    """Persist exact remote sidecar bytes captured by the bootstrap command."""
    records: list[ArtifactRecord] = []
    for build in builds:
        if build.identity_bytes is None or build.artifact_id is None:
            continue
        raw = build.identity_bytes.encode("utf-8")
        path = f"metadata/build/{build.role}-{build.tool}.identity"
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() != raw:
            raise ValueError("build identity sidecar conflicts with existing attempt evidence")
        if not target.exists():
            target.write_bytes(raw)
        role = VmRole(build.role)
        record = ArtifactRecord(artifact_id=build.artifact_id, path=path, kind="build-identity",
            producer=role, unit_id=None, deployment_id=deployment_id, closed=True,
            size_bytes=Evidence[int](value=len(raw)), sha256=StringEvidence(value=hashlib.sha256(raw).hexdigest()),
            retrieval_state="verified")
        record.verify_materialized_file(root)
        records.append(record)
    return tuple(records)


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


def _build_config_prelude(config: BootstrapConfig) -> str:
    spec = shlex.quote(f"./configure --prefix={config.install_prefix}; make -j2")
    # Command substitution removes the terminal newline but retains the complete
    # multiline version banner used by BuildObservation and the sidecar.
    return ("compiler=$(cc --version) || exit 42; "
            f"cfg=$(printf '%s\\n' \"${{CC:-cc}}\" \"$compiler\" {spec} | sha256sum) || exit 42; "
            "set -- $cfg; config_sha=$1; ")


def _capabilities_observed(text: str, tool: str, *, required: List[str] | None = None) -> bool:
    if not text.strip() or "usage" not in text.lower() or "error" in text.lower() or "unknown option" in text.lower():
        return False
    tokens = set(re.findall(r"--[A-Za-z0-9-]+|-[A-Za-z0-9]|[A-Za-z][A-Za-z0-9-]*", text))
    return set(required if required is not None else _required_capabilities(tool)) <= tokens


def _unit_matches(text: str, config_text: str, tool: str, address: str, port: int, prefix: str, stdout_path: str, stderr_path: str,
                  observation_id: str | None = None) -> bool:
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
            and fields.get("StandardOutput") == "append"
            and fields.get("StandardError") == "append"
            and _unit_log_destinations_match(config_text, stdout_path, stderr_path)
            and (observation_id is None or _unit_environment_matches(text, config_text, observation_id)))


def _unit_log_destinations_match(text: str, stdout_path: str, stderr_path: str) -> bool:
    effective = {"StandardOutput": None, "StandardError": None}
    section = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section != "Service" or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in effective:
            # systemd's empty assignment resets an inherited setting; the
            # subsequent effective value must still be explicit and exact.
            effective[key] = value or None
    return effective == {"StandardOutput": f"append:{stdout_path}", "StandardError": f"append:{stderr_path}"}


def _unit_environment_matches(show_text: str, config_text: str, observation_id: str) -> bool:
    expected = f"CNB_ACQUISITION_ID={observation_id}"
    fields = dict(line.split("=", 1) for line in show_text.splitlines() if "=" in line)
    effective = None
    section = None
    for raw in config_text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif section == "Service" and line.startswith("Environment="):
            effective = line.split("=", 1)[1]
    return expected in fields.get("Environment", "") and effective == expected


def _process_identity_command(unit: str, binary: str) -> list[str]:
    unit_q, binary_q = shlex.quote(unit), shlex.quote(binary)
    script = (f"pid=$(systemctl show --property=MainPID --value {unit_q}) || exit 1; "
              f"case $pid in ''|*[!0-9]*) exit 1;; esac; [ \"$pid\" -gt 0 ] || exit 1; "
              f"actual=$(readlink -f /proc/$pid/exe) || exit 1; [ \"$actual\" = {binary_q} ] || exit 1; "
              "printf '%s\\n' \"$actual\"; sha256sum /proc/$pid/exe")
    return ["sudo", "sh", "-c", script]


def _process_identity_matches(text: str, binary: str, expected_sha256: str) -> bool:
    lines = text.strip().splitlines()
    return (len(lines) == 2 and lines[0] == binary
            and re.fullmatch(r"[a-f0-9]{64}\s+/proc/[1-9][0-9]*/exe", lines[1]) is not None
            and lines[1].split()[0] == expected_sha256)


def _acl_matches(text: str, source: str, address: str, chain: str | None = None) -> bool:
    lines = [re.sub(r"\bcounter packets \d+ bytes \d+\b", "", line.strip()).strip() for line in text.splitlines() if line.strip()]
    table_lines = [line for line in lines if re.fullmatch(r"table inet cnb_f02r \{", line)]
    chain_lines = [line for line in lines if re.fullmatch(r"chain [A-Za-z0-9_]+ \{", line)]
    if len(table_lines) != 1 or len(chain_lines) != 1:
        return False
    actual_chain = chain_lines[0].split()[1]
    if chain is not None and actual_chain != chain:
        return False
    start = lines.index(chain_lines[0]) + 1
    try:
        end = lines.index("}", start)
    except ValueError:
        return False
    body = [" ".join(line.split()) for line in lines[start:end]]
    headers = [line for line in body if re.fullmatch(r"type filter hook input priority -?\d+; policy accept;", line)]
    rules = [line for line in body if line not in headers]
    source_forms = {source, f"{source}/32"}
    expected_rules = [
        next((f"ip saddr {host} ip daddr {address} udp dport 11111 accept" for host in source_forms if f"ip saddr {host} ip daddr {address} udp dport 11111 accept" in rules), ""),
        f"ip daddr {address} udp dport 11111 drop",
        next((f"ip saddr {host} ip daddr {address} tcp dport 5201 accept" for host in source_forms if f"ip saddr {host} ip daddr {address} tcp dport 5201 accept" in rules), ""),
        f"ip daddr {address} tcp dport 5201 drop",
    ]
    return len(headers) == 1 and all(expected_rules) and rules == expected_rules and lines[end + 1:] == ["}"]


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
    patch_clause = ""
    cache_patch = "legacy"
    if source.observation_patch_path is not None:
        patch_path = Path(__file__).parent / source.observation_patch_path
        patch_bytes = patch_path.read_bytes()
        patch_digest = hashlib.sha256(patch_bytes).hexdigest()
        if patch_digest != source.observation_patch_sha256:
            raise ValueError("packaged observation patch digest does not match requested identity")
        patch_q = shlex.quote(f"{config.workspace}/{source.tool}.acquisition.patch")
        encoded = base64.b64encode(patch_bytes).decode("ascii")
        patch_clause = (f"printf '%s' {shlex.quote(encoded)} | base64 -d > {patch_q} && "
                        f"printf '%s  %s\\n' {shlex.quote(patch_digest)} {patch_q} | sha256sum -c - && "
                        f"cd {source_dir_q} && patch --batch --fuzz=0 -p1 < {patch_q} && ")
        cache_patch = patch_digest
    patch_q = shlex.quote(f"{config.workspace}/{source.tool}.acquisition.patch")
    cache_q = shlex.quote(cache_patch)
    build_script = (_build_config_prelude(config) +
                    f"if [ -f {marker_q} ]; then IFS=' ' read -r old_ref old_sha old_patch old_config old_bin < {marker_q}; "
                    f"current=$(sha256sum {binary_q}) || exit 42; set -- $current; "
                    f"[ \"$old_ref\" = {ref_q} ] && [ \"$old_sha\" = {digest_q} ] && [ \"$old_patch\" = {cache_q} ] && [ \"$old_config\" = \"$config_sha\" ] && [ \"$old_bin\" = \"$1\" ] || exit 42; exit 0; fi; "
                    f"[ ! -e {binary_q} ] || exit 43; {patch_clause}cd {source_dir_q} && {build}./configure --prefix={prefix_q} && make -j2 && make install && "
                    f"current=$(sha256sum {binary_q}) && set -- $current && printf '%s %s %s %s %s\\n' {ref_q} {digest_q} {cache_q} \"$config_sha\" \"$1\" > {marker_q}")
    commands = [
        ["sudo", "apt-get", "install", "-y", *config.bootstrap_packages],
        ["curl", "--fail", "--location", "--silent", "--show-error", "--output", archive, source.source_url],
        ["sha256sum", archive],
        ["tar", "-xf", archive, "-C", config.workspace],
        ["sh", "-c", build_script],
        ["cc", "--version"],
        [f"{prefix}/bin/{source.tool}", "--version"],
        ["sha256sum", f"{prefix}/bin/{source.tool}"],
    ]
    commands.append([f"{prefix}/bin/{source.tool}", "--help"])
    if source.tool == "sockperf":
        commands.extend([[f"{prefix}/bin/sockperf", "server", "--help"],
                         [f"{prefix}/bin/sockperf", "under-load", "--help"]])
    if source.observation_patch_path is not None:
        commands.append(["sh", "-c", f"grep -a -F 'CNB_ACQ_V1' {binary_q} >/dev/null && printf CNB_ACQ_V1"])
        identity_plain = f"{prefix}/.f02r-{source.tool}.build-identity"
        identity_path = shlex.quote(identity_plain)
        identity_tmp = identity_plain + ".$$.tmp"
        # Keep this description byte-for-byte aligned with BuildObservation's
        # captured config; shell-expand only the observed compiler version.
        build_config_prefix = "CC=${CC:-cc}; compiler="
        build_config_suffix = f"; ./configure --prefix={prefix}; make -j2"
        config_prelude = _build_config_prelude(config)
        identity_script = (config_prelude +
            f"binary_version=$(\"{binary}\" --version) || exit 42; "
            f"binary_hash=$(sha256sum {binary_q}) || exit 42; set -- $binary_hash; binary_sha=$1; "
            "compiler_b64=$(printf '%s' \"$compiler\" | base64 -w0) || exit 42; "
            "version_b64=$(printf '%s' \"$binary_version\" | base64 -w0) || exit 42; "
            f"config_b64=$(printf '%s%s%s' {shlex.quote(build_config_prefix)} \"$compiler\" {shlex.quote(build_config_suffix)} | base64 -w0) || exit 42; "
            f"printf '%s\\n' 'CNB_BUILD_V1' 'tool={source.tool}' 'source_reference={source.source_reference}' "
            f"'source_sha256={source.source_sha256}' 'upstream_commit={source.upstream_commit or ''}' "
            f"'patch_path={source.observation_patch_path}' 'patch_sha256={source.observation_patch_sha256}' "
            "\"compiler_version_b64=$compiler_b64\" \"build_configuration_b64=$config_b64\" "
            f"'binary_path={binary}' \"binary_version_b64=$version_b64\" \"binary_sha256=$binary_sha\" "
            f"'observation_capability=CNB_ACQ_V1' 'observation_semantics={'sockperf-3.10-enabled-v1' if source.tool == 'sockperf' else 'iperf-3.17.1-stats-v1'}' "
            f"'capabilities={','.join(_required_capabilities(source.tool))}' > {identity_tmp} && "
            f"if [ -f {identity_path} ]; then cmp -s {identity_tmp} {identity_path} || exit 43; rm {identity_tmp}; "
            f"else mv {identity_tmp} {identity_path}; fi && cat {identity_path}")
        commands.append(["sh", "-c", identity_script])
    return commands


def _identity_preflight_script(source: SourceBuild, config: BootstrapConfig) -> str:
    binary = shlex.quote(f"{config.install_prefix}/bin/{source.tool}")
    marker = shlex.quote(f"{config.install_prefix}/.f02r-{source.tool}.identity")
    reference, digest = shlex.quote(source.source_reference), shlex.quote(source.source_sha256)
    patch_digest = shlex.quote(source.observation_patch_sha256 or "legacy")
    return (_build_config_prelude(config) +
            f"if [ ! -e {marker} ]; then [ ! -e {binary} ] || exit 43; printf 'missing\\n'; exit 0; fi; "
            f"IFS=' ' read -r old_ref old_sha old_patch old_config old_bin < {marker}; current=$(sha256sum {binary}) || exit 42; set -- $current; "
            f"[ \"$old_ref\" = {reference} ] && [ \"$old_sha\" = {digest} ] && [ \"$old_patch\" = {patch_digest} ] && [ \"$old_config\" = \"$config_sha\" ] && [ \"$old_bin\" = \"$1\" ] || exit 42; printf 'matching\\n'")


def _sha256sum_output(text: str, expected_path: str) -> str | None:
    lines = text.strip().splitlines()
    if len(lines) != 1:
        return None
    match = re.fullmatch(r"([a-f0-9]{64})\s+\*?(.+)", lines[0].strip())
    return match.group(1) if match and match.group(2) == expected_path else None


def _parse_build_identity(text: str, source: SourceBuild) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0] != "CNB_BUILD_V1":
        raise ValueError("remote build identity sidecar is missing its format marker")
    fields: dict[str, str] = {}
    for line in lines[1:]:
        key, sep, value = line.partition("=")
        if not sep or key in fields:
            raise ValueError("remote build identity sidecar has malformed or duplicate fields")
        fields[key] = value
    required = {"tool", "source_reference", "source_sha256", "upstream_commit", "patch_path",
        "patch_sha256", "compiler_version_b64", "build_configuration_b64", "binary_path",
        "binary_version_b64", "binary_sha256", "observation_capability", "observation_semantics", "capabilities"}
    if fields.keys() != required:
        raise ValueError("remote build identity sidecar fields are incomplete or unexpected")
    for key, expected in (("tool", source.tool), ("source_reference", source.source_reference),
            ("source_sha256", source.source_sha256), ("upstream_commit", source.upstream_commit or ""),
            ("patch_path", source.observation_patch_path or ""),
            ("patch_sha256", source.observation_patch_sha256 or ""),
            ("observation_capability", "CNB_ACQ_V1")):
        if fields[key] != expected:
            raise ValueError(f"remote build identity sidecar {key} does not match requested build")
    for key in ("compiler_version_b64", "build_configuration_b64", "binary_version_b64"):
        try:
            fields[key + "_decoded"] = base64.b64decode(fields[key], validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError(f"remote build identity sidecar {key} is invalid") from exc
    if not re.fullmatch(r"/[^\n]+/bin/" + re.escape(source.tool), fields["binary_path"]):
        raise ValueError("remote build identity sidecar executable path is invalid")
    if not re.fullmatch(r"[a-f0-9]{64}", fields["binary_sha256"]):
        raise ValueError("remote build identity sidecar binary digest is invalid")
    return fields


def _compiler_identity_observed(text: str) -> bool:
    value = text.strip()
    if not value or re.search(r"(?i)\b(error|not found|unknown option)\b", value):
        return False
    identified = re.search(r"(?im)^(?:gcc|g\+\+|clang|Apple clang|cc\s*\()", value) is not None
    versioned = re.search(r"\b\d+(?:\.\d+){1,3}\b", value) is not None
    return identified and versioned


def _canonical_version_text(text: str) -> str:
    """Use the complete banner, removing only command-output framing whitespace."""
    return text.strip()


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
    if result.outcome == CommandOutcome.INTERRUPTED:
        category, message = "command_interrupted", "remote preparation command was interrupted"
    elif "request construction failed" in result.stderr:
        category, message = "transport_preflight_failed", "authenticated remote request could not be constructed"
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
        if not _identity_references_available(vm):
            failed_request, failed_result = _request_failure(f"f02r-{role}-transport-preflight", vm, connection.timeouts.command_seconds, ValueError("credential reference unavailable between VMs"))
            actions.append(_action(failed_request.action_id, "ssh", vm, failed_request, failed_result))
            return _failure_result(connection, actions, vm, failed_request, failed_result, "transport_preflight_failed", "SSH identity reference became unavailable between VM preparations", builds, listeners)
        if deadline.remaining(clock, readiness=True) <= 0:
            return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, failure=AccessFailureEvidence(stage="bootstrap", role=role, category="readiness_deadline_exhausted", message="no time remains before remote preparation", occurred_at=datetime.now(timezone.utc)), builds=builds, listeners=listeners)
        for argv, timeout_name, kind in init:
            timeout = min(getattr(connection.timeouts, timeout_name), deadline.remaining(clock, readiness=True))
            req = _request(f"f02r-{role}-cloud-init", vm, argv, timeout)
            result = _execute(runner, req)
            actions.append(_action(req.action_id, "ssh", vm, req, result))
            if result.outcome != CommandOutcome.SUCCEEDED or "done" not in result.stdout.lower():
                return _failure_result(connection, actions, vm, req, result, "cloud_init_failed", "cloud-init completion was not observed", builds, listeners)
        if not _identity_references_available(vm):
            failed_request, failed_result = _request_failure(f"f02r-{role}-transport-preflight", vm, connection.timeouts.command_seconds, ValueError("credential reference unavailable"))
            actions.append(_action(failed_request.action_id, "ssh", vm, failed_request, failed_result))
            return _failure_result(connection, actions, vm, failed_request, failed_result, "transport_preflight_failed", "SSH identity reference became unavailable", builds, listeners)
        if role == "vm_b":
            for tool, port, stdout_path, stderr_path in (("sockperf", 11111, "server-logs/sockperf.log", "server-logs/sockperf.err"), ("iperf3", 5201, "server-logs/iperf3-server.jsonl", "server-logs/iperf3-server.err")):
                remaining = deadline.remaining(clock, readiness=True)
                if remaining <= 0:
                    return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, failure=AccessFailureEvidence(stage="bootstrap", role=role, category="readiness_deadline_exhausted", message="preparation deadline exhausted before existing listener inspection", occurred_at=datetime.now(timezone.utc)), builds=builds, listeners=listeners)
                inspect_id = f"f02r-vm_b-preflight-listener-inspect-{tool}"
                inspect_request = _request(inspect_id, vm, ["systemctl", "show", f"cnb-{connection.deployment_id}-{tool}", "--property=LoadState,ActiveState,MainPID,ExecStart,StandardOutput,StandardError,Restart,Environment"], min(connection.timeouts.server_seconds, remaining))
                observed = _execute(runner, inspect_request)
                actions.append(_action(inspect_id, "ssh", vm, inspect_request, observed))
                if observed.outcome == CommandOutcome.SUCCEEDED:
                    load_state = dict(line.split("=", 1) for line in observed.stdout.splitlines() if "=" in line).get("LoadState")
                    if load_state != "not-found":
                        cat_remaining = deadline.remaining(clock, readiness=True)
                        if cat_remaining <= 0:
                            return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, failure=AccessFailureEvidence(stage="bootstrap", role=role, category="readiness_deadline_exhausted", message="preparation deadline exhausted inspecting service log destinations", occurred_at=datetime.now(timezone.utc)), builds=builds, listeners=listeners)
                        cat_request = _request(f"f02r-vm_b-preflight-listener-config-{tool}", vm, ["systemctl", "cat", f"cnb-{connection.deployment_id}-{tool}"], min(connection.timeouts.server_seconds, cat_remaining))
                        cat_result = _execute(runner, cat_request); actions.append(_action(cat_request.action_id, "ssh", vm, cat_request, cat_result))
                        if (cat_result.outcome != CommandOutcome.SUCCEEDED or
                            not _unit_matches(observed.stdout, cat_result.stdout, tool, connection.private_ipv4_b, port, config.install_prefix, f"{config.workspace}/{stdout_path}", f"{config.workspace}/{stderr_path}",
                                "f02r-vm_b-listener-iperf3" if tool == "iperf3" and sources[tool].observation_patch_path else None)):
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
            capability_outputs: list[str] = []
            identity_text: str | None = None
            for index, argv in enumerate(recipe):
                if not _identity_references_available(vm):
                    failed_request, failed_result = _request_failure(f"f02r-{role}-{source.tool}-transport-preflight", vm, connection.timeouts.command_seconds, ValueError("credential reference unavailable"))
                    actions.append(_action(failed_request.action_id, "ssh", vm, failed_request, failed_result))
                    return _failure_result(connection, actions, vm, failed_request, failed_result, "transport_preflight_failed", "SSH identity reference became unavailable during build", builds, listeners)
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
                for secret in (vm.authentication_reference, vm.host_key_reference):
                    text = re.sub(r"(?i)(password|token|secret|private[_ -]?key)\s*[=:]\s*\S+", r"\1=[REDACTED]", text)
                    text = text.replace(secret, "[REDACTED]")
                text = re.sub(r"-----BEGIN [^-]+-----.*?-----END [^-]+-----", "[REDACTED_KEY]", text, flags=re.DOTALL)
                if index == 2:
                    observed_hash = _sha256sum_output(text, f"{config.workspace}/{source.archive_name}")
                    if observed_hash != source.source_sha256:
                        return _failure_result(connection, actions, vm, req, result, "source_digest_mismatch", f"{source.tool} source archive digest did not match", builds, listeners)
                    evidence["source_sha256"] = observed_hash
                elif index == 5:
                    version_text = _canonical_version_text(text)
                    if not _compiler_identity_observed(version_text):
                        return _failure_result(connection, actions, vm, req, result, "compiler_identity_missing", "compiler version was not observed", builds, listeners)
                    evidence["compiler_version"] = version_text
                elif index == 6:
                    version_text = _canonical_version_text(text)
                    displayed_tools = {"iperf", "iperf3"} if source.tool == "iperf3" else {source.tool}
                    if not any(re.search(rf"(?im)^\s*{re.escape(name)}(?:\s|:|$)", version_text)
                               for name in displayed_tools):
                        return _failure_result(connection, actions, vm, req, result, "binary_version_missing", f"{source.tool} binary version was not observed", builds, listeners)
                    evidence["binary_version"] = version_text
                elif index == 7:
                    observed_hash = _sha256sum_output(text, f"{config.install_prefix}/bin/{source.tool}")
                    if not observed_hash:
                        return _failure_result(connection, actions, vm, req, result, "binary_hash_missing", f"{source.tool} binary hash was not observed", builds, listeners)
                    evidence["binary_sha256"] = observed_hash
                elif source.observation_patch_path is not None and index == len(recipe) - 2:
                    if text != "CNB_ACQ_V1":
                        return _failure_result(connection, actions, vm, req, result, "observation_capability_missing", f"{source.tool} installed binary lacks CNB_ACQ_V1 capability", builds, listeners)
                    evidence["observation_capability"] = text
                elif source.observation_patch_path is not None and index == len(recipe) - 1:
                    try:
                        identity = _parse_build_identity(result.stdout, source)
                    except ValueError as exc:
                        return _failure_result(connection, actions, vm, req, result, "build_identity_invalid", str(exc), builds, listeners)
                    if (identity["binary_sha256"] != evidence.get("binary_sha256")
                            or _canonical_version_text(identity["binary_version_b64_decoded"]) != evidence.get("binary_version")
                            or _canonical_version_text(identity["compiler_version_b64_decoded"]) != evidence.get("compiler_version")):
                        return _failure_result(connection, actions, vm, req, result, "build_identity_mismatch", "build identity sidecar does not match the observed compiler or installed executable", builds, listeners)
                    identity_text = result.stdout
                elif index >= 8:
                    if source.tool == "sockperf":
                        required = (["server", "-i", "-p"] if index == 9 else
                                    ["under-load", "-i", "-p", "-m", "--mps", "--reply-every", "--full-rtt", "--no-rdtsc", "--full-log", "-t"] if index == 10 else [])
                    else:
                        required = _required_capabilities(source.tool)
                    if not _capabilities_observed(text, source.tool, required=required):
                        return _failure_result(connection, actions, vm, req, result, "capability_missing", f"{source.tool} required capability help was incomplete", builds, listeners)
                    capability_outputs.append(text)
            evidence["capabilities"] = "\n".join(capability_outputs)
            binary_path = f"{config.install_prefix}/bin/{source.tool}"
            builds.append(BuildObservation(role=role, tool=source.tool, source_reference=source.source_reference,
                                           source_sha256=evidence["source_sha256"], compiler_version=evidence["compiler_version"],
                                           binary_path=binary_path, binary_version=evidence["binary_version"], binary_sha256=evidence["binary_sha256"],
                                           capabilities=_required_capabilities(source.tool),
                                           artifact_id=f"f02r-{role}-{source.tool}-build",
                                           upstream_commit=source.upstream_commit,
                                           patch_path=source.observation_patch_path,
                                           patch_sha256=source.observation_patch_sha256,
                                           build_configuration=(f"CC=${{CC:-cc}}; compiler={evidence['compiler_version']}; ./configure --prefix={config.install_prefix}; make -j2") if source.observation_patch_path else None,
                                           observation_capability=evidence.get("observation_capability"),
                                           observation_semantics=("sockperf-3.10-enabled-v1" if source.tool == "sockperf" else "iperf-3.17.1-stats-v1") if evidence.get("observation_capability") else None,
                                           identity_bytes=identity_text))
        if role == "vm_b":
            # Establish the private-only ingress chain before starting either listener.
            chain = f"cnb_{re.sub(r'[^A-Za-z0-9_]', '_', connection.deployment_id)[:32]}"
            pre_id = "f02r-vm_b-acl-preflight-inspect"
            pre_remaining = deadline.remaining(clock, readiness=True)
            if pre_remaining <= 0:
                return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, failure=AccessFailureEvidence(stage="bootstrap", role=role, category="readiness_deadline_exhausted", message="preparation deadline exhausted before guest restriction", occurred_at=datetime.now(timezone.utc)), builds=builds, listeners=listeners)
            pre_request = _request(pre_id, vm, ["sudo", "nft", "-n", "-y", "list", "chain", "inet", "cnb_f02r", chain], min(connection.timeouts.command_seconds, pre_remaining))
            pre_result = _execute(runner, pre_request)
            actions.append(_action(pre_id, "ssh", vm, pre_request, pre_result))
            if pre_result.outcome == CommandOutcome.SUCCEEDED:
                if not _acl_matches(pre_result.stdout, connection.private_ipv4_a, connection.private_ipv4_b, chain):
                    return _failure_result(connection, actions, vm, pre_request, pre_result, "listener_acl_mismatch", "existing guest benchmark ACL does not match this deployment", builds, listeners)
            elif pre_result.outcome == CommandOutcome.FAILED and any(text in (pre_result.stdout + pre_result.stderr).lower() for text in ("no such file", "does not exist", "not found")):
                rules = [
                    ["sudo", "sh", "-c", "nft list table inet cnb_f02r >/dev/null 2>&1 || nft add table inet cnb_f02r"],
                    ["sudo", "nft", "add", "chain", "inet", "cnb_f02r", chain, "{", "type", "filter", "hook", "input", "priority", "-10", ";", "policy", "accept", ";", "}"],
                    ["sudo", "nft", "add", "rule", "inet", "cnb_f02r", chain, "ip", "saddr", f"{connection.private_ipv4_a}/32", "ip", "daddr", connection.private_ipv4_b, "udp", "dport", "11111", "accept"],
                    ["sudo", "nft", "add", "rule", "inet", "cnb_f02r", chain, "ip", "daddr", connection.private_ipv4_b, "udp", "dport", "11111", "drop"],
                    ["sudo", "nft", "add", "rule", "inet", "cnb_f02r", chain, "ip", "saddr", f"{connection.private_ipv4_a}/32", "ip", "daddr", connection.private_ipv4_b, "tcp", "dport", "5201", "accept"],
                    ["sudo", "nft", "add", "rule", "inet", "cnb_f02r", chain, "ip", "daddr", connection.private_ipv4_b, "tcp", "dport", "5201", "drop"],
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
                inspect_request = _request(inspect_id, vm, ["systemctl", "show", unit, "--property=LoadState,ActiveState,MainPID,ExecStart,StandardOutput,StandardError,Restart,Environment"], min(connection.timeouts.server_seconds, remaining))
                inspect_result = _execute(runner, inspect_request)
                actions.append(_action(inspect_id, "ssh", vm, inspect_request, inspect_result))
                if inspect_result.outcome == CommandOutcome.SUCCEEDED:
                    output = inspect_result.stdout
                    fields = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
                    if fields.get("LoadState") == "not-found":
                        pass
                    else:
                        cat_remaining = deadline.remaining(clock, readiness=True)
                        if cat_remaining <= 0:
                            return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, failure=AccessFailureEvidence(stage="bootstrap", vm_id=vm.host, role=role, category="readiness_deadline_exhausted", message="deadline exhausted before service log destination observation", occurred_at=datetime.now(timezone.utc)), builds=builds, listeners=listeners)
                        cat_request = _request(f"f02r-vm_b-listener-config-{tool}", vm, ["systemctl", "cat", unit], min(connection.timeouts.server_seconds, cat_remaining))
                        cat_result = _execute(runner, cat_request); actions.append(_action(cat_request.action_id, "ssh", vm, cat_request, cat_result))
                        if cat_result.outcome != CommandOutcome.SUCCEEDED or not _unit_matches(output, cat_result.stdout, tool, address, port, prefix, f"{config.workspace}/{stdout_path}", f"{config.workspace}/{stderr_path}",
                                "f02r-vm_b-listener-iperf3" if tool == "iperf3" and sources[tool].observation_patch_path else None):
                            return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, failure=AccessFailureEvidence(stage="bootstrap", role=role, category="listener_state_mismatch", message=f"existing {tool} unit does not match requested output mode/path", occurred_at=cat_result.finished_at), builds=builds, listeners=listeners)
                        proc_id = f"f02r-vm_b-listener-process-{tool}"
                        proc_remaining = deadline.remaining(clock, readiness=True)
                        if proc_remaining <= 0:
                            return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, failure=AccessFailureEvidence(stage="bootstrap", vm_id=vm.host, role=role, category="readiness_deadline_exhausted", message="listener verification consumed the preparation deadline", occurred_at=datetime.now(timezone.utc)), builds=builds, listeners=listeners)
                        proc_request = _request(proc_id, vm, _process_identity_command(f"cnb-{connection.deployment_id}-{tool}", f"{prefix}/bin/{tool}"), min(connection.timeouts.server_seconds, proc_remaining))
                        proc_result = _execute(runner, proc_request)
                        actions.append(_action(proc_id, "ssh", vm, proc_request, proc_result))
                        build = next(b for b in builds if b.role == "vm_b" and b.tool == tool)
                        if proc_result.outcome != CommandOutcome.SUCCEEDED or not _process_identity_matches(proc_result.stdout, build.binary_path, build.binary_sha256):
                            return _failure_result(connection, actions, vm, proc_request, proc_result, "listener_process_mismatch", f"live {tool} executable did not match its recorded binary", builds, listeners)
                        listeners.append(ListenerObservation(tool=tool, address=address, protocol=protocol, port=port,
                            unit=unit, binary_path=f"{prefix}/bin/{tool}", stdout_path=f"{config.workspace}/{stdout_path}",
                            stderr_path=f"{config.workspace}/{stderr_path}", allowed_source=f"{connection.private_ipv4_a}/32", state="active"))
                        continue
                elif inspect_result.outcome != CommandOutcome.FAILED or not any(text in (inspect_result.stdout + inspect_result.stderr).lower() for text in ("not found", "no such file", "could not be found")):
                    return _failure_result(connection, actions, vm, inspect_request, inspect_result, "listener_inspection_failed", f"could not inspect existing {tool} listener", builds, listeners)
                stdout_abs = f"{config.workspace}/{stdout_path}"
                stderr_abs = f"{config.workspace}/{stderr_path}"
                mkdir_id = f"f02r-vm_b-listener-log-directory-{tool}"
                mkdir_remaining = deadline.remaining(clock, readiness=True)
                if mkdir_remaining <= 0:
                    return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, failure=AccessFailureEvidence(stage="bootstrap", role=role, category="readiness_deadline_exhausted", message="preparation deadline exhausted before log setup", occurred_at=datetime.now(timezone.utc)), builds=builds, listeners=listeners)
                mkdir_request = _request(mkdir_id, vm, ["sudo", "install", "-d", "-m", "0755", str(PurePosixPath(stdout_abs).parent)], min(connection.timeouts.command_seconds, mkdir_remaining))
                mkdir_result = _execute(runner, mkdir_request)
                actions.append(_action(mkdir_id, "ssh", vm, mkdir_request, mkdir_result))
                if mkdir_result.outcome != CommandOutcome.SUCCEEDED:
                    return _failure_result(connection, actions, vm, mkdir_request, mkdir_result, "listener_log_setup_failed", f"could not prepare {tool} log destination", builds, listeners)
                command = ["sudo", "systemd-run", "--unit", unit, "--property=Restart=no", "--property=UMask=0022", "--property=StandardOutput=append:" + stdout_abs, "--property=StandardError=append:" + stderr_abs]
                if tool == "iperf3" and sources[tool].observation_patch_path:
                    command.append("--property=Environment=CNB_ACQUISITION_ID=f02r-vm_b-listener-iperf3")
                command.extend(server_argv)
                aid = f"f02r-vm_b-listener-{tool}"
                remaining = deadline.remaining(clock, readiness=True)
                if remaining <= 0:
                    return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, failure=AccessFailureEvidence(stage="bootstrap", role=role, category="readiness_deadline_exhausted", message="preparation deadline exhausted before listeners", occurred_at=datetime.now(timezone.utc)), builds=builds, listeners=listeners)
                req = _request(aid, vm, command, min(connection.timeouts.server_seconds, remaining))
                result = _execute(runner, req)
                actions.append(_action(aid, "ssh", vm, req, result))
                if result.outcome != CommandOutcome.SUCCEEDED:
                    return _failure_result(connection, actions, vm, req, result, "listener_setup_failed", f"{tool} listener was not observed", builds, listeners)
                verify_id = f"f02r-vm_b-listener-verify-{tool}"
                verify_remaining = deadline.remaining(clock, readiness=True)
                if verify_remaining <= 0:
                    return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, failure=AccessFailureEvidence(stage="bootstrap", role=role, category="readiness_deadline_exhausted", message="preparation deadline exhausted before listener identity verification", occurred_at=datetime.now(timezone.utc)), builds=builds, listeners=listeners)
                verify_request = _request(verify_id, vm, ["systemctl", "show", unit, "--property=LoadState,ActiveState,MainPID,ExecStart,StandardOutput,StandardError,Restart,Environment"], min(connection.timeouts.server_seconds, verify_remaining))
                verify_result = _execute(runner, verify_request)
                actions.append(_action(verify_id, "ssh", vm, verify_request, verify_result))
                cat_remaining = deadline.remaining(clock, readiness=True)
                if cat_remaining <= 0:
                    return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, failure=AccessFailureEvidence(stage="bootstrap", vm_id=vm.host, role=role, category="readiness_deadline_exhausted", message="deadline exhausted before service output path verification", occurred_at=datetime.now(timezone.utc)), builds=builds, listeners=listeners)
                cat_request = _request(f"f02r-vm_b-listener-config-{tool}", vm, ["systemctl", "cat", unit], min(connection.timeouts.server_seconds, cat_remaining))
                cat_result = _execute(runner, cat_request); actions.append(_action(cat_request.action_id, "ssh", vm, cat_request, cat_result))
                if (verify_result.outcome != CommandOutcome.SUCCEEDED or cat_result.outcome != CommandOutcome.SUCCEEDED
                        or not _unit_matches(verify_result.stdout, cat_result.stdout, tool, address, port, prefix, stdout_abs, stderr_abs,
                            "f02r-vm_b-listener-iperf3" if tool == "iperf3" and sources[tool].observation_patch_path else None)):
                    return _failure_result(connection, actions, vm, verify_request, verify_result, "listener_identity_unverified", f"{tool} process or listener identity was not observed", builds, listeners)
                proc_id = f"f02r-vm_b-listener-process-{tool}"
                proc_remaining = deadline.remaining(clock, readiness=True)
                if proc_remaining <= 0:
                    return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, failure=AccessFailureEvidence(stage="bootstrap", vm_id=vm.host, role=role, category="readiness_deadline_exhausted", message="listener verification consumed the preparation deadline", occurred_at=datetime.now(timezone.utc)), builds=builds, listeners=listeners)
                proc_request = _request(proc_id, vm, _process_identity_command(unit, f"{prefix}/bin/{tool}"), min(connection.timeouts.server_seconds, proc_remaining))
                proc_result = _execute(runner, proc_request)
                actions.append(_action(proc_id, "ssh", vm, proc_request, proc_result))
                build = next(b for b in builds if b.role == "vm_b" and b.tool == tool)
                if proc_result.outcome != CommandOutcome.SUCCEEDED or not _process_identity_matches(proc_result.stdout, build.binary_path, build.binary_sha256):
                    return _failure_result(connection, actions, vm, proc_request, proc_result, "listener_process_mismatch", f"live {tool} executable did not match its recorded binary", builds, listeners)
                listeners.append(ListenerObservation(tool=tool, address=address, protocol=protocol, port=port,
                    unit=unit, binary_path=f"{prefix}/bin/{tool}", stdout_path=stdout_abs,
                    stderr_path=stderr_abs, allowed_source=f"{connection.private_ipv4_a}/32", state="active"))
            chain = f"cnb_{re.sub(r'[^A-Za-z0-9_]', '_', connection.deployment_id)[:32]}"
            inspect_id = "f02r-vm_b-acl-inspect"
            remaining = deadline.remaining(clock, readiness=True)
            if remaining <= 0:
                return BootstrapResult(succeeded=False, identity=connection.identity, deployment_id=connection.deployment_id, actions=actions, failure=AccessFailureEvidence(stage="bootstrap", role=role, category="readiness_deadline_exhausted", message="preparation deadline exhausted before ACL inspection", occurred_at=datetime.now(timezone.utc)), builds=builds, listeners=listeners)
            inspect = _request(inspect_id, vm, ["sudo", "nft", "-n", "-y", "list", "chain", "inet", "cnb_f02r", chain], min(connection.timeouts.command_seconds, remaining))
            acl_result = _execute(runner, inspect)
            actions.append(_action(inspect_id, "ssh", vm, inspect, acl_result))
            expected_acl = [f"{connection.private_ipv4_a}/32", connection.private_ipv4_b, "11111", "5201", "drop", "hook input"]
            if acl_result.outcome == CommandOutcome.SUCCEEDED:
                if not _acl_matches(acl_result.stdout, connection.private_ipv4_a, connection.private_ipv4_b, chain):
                    return _failure_result(connection, actions, vm, inspect, acl_result, "listener_acl_mismatch", "existing guest benchmark ACL does not match this deployment", builds, listeners)
            elif acl_result.outcome == CommandOutcome.FAILED and any(text in (acl_result.stdout + acl_result.stderr).lower() for text in ("no such file", "does not exist", "not found")):
                rules = [
                    ["sudo", "sh", "-c", "nft list table inet cnb_f02r >/dev/null 2>&1 || nft add table inet cnb_f02r"],
                    ["sudo", "nft", "add", "chain", "inet", "cnb_f02r", chain, "{", "type", "filter", "hook", "input", "priority", "-10", ";", "policy", "accept", ";", "}"],
                    ["sudo", "nft", "add", "rule", "inet", "cnb_f02r", chain, "ip", "saddr", f"{connection.private_ipv4_a}/32", "ip", "daddr", connection.private_ipv4_b, "udp", "dport", "11111", "accept"],
                    ["sudo", "nft", "add", "rule", "inet", "cnb_f02r", chain, "ip", "daddr", connection.private_ipv4_b, "udp", "dport", "11111", "drop"],
                    ["sudo", "nft", "add", "rule", "inet", "cnb_f02r", chain, "ip", "saddr", f"{connection.private_ipv4_a}/32", "ip", "daddr", connection.private_ipv4_b, "tcp", "dport", "5201", "accept"],
                    ["sudo", "nft", "add", "rule", "inet", "cnb_f02r", chain, "ip", "daddr", connection.private_ipv4_b, "tcp", "dport", "5201", "drop"],
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
