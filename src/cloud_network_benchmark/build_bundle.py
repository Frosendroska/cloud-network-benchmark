"""Explicit, local-only packaging for reviewed controller-built benchmark tools.

This module never downloads sources or runs build commands. It packages already
built binaries only when a truthful per-tool receipt and exact production pins
are supplied.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tarfile
import tempfile
from typing import Any, TYPE_CHECKING
import base64

from pydantic import Field, model_validator

from .contracts.common import StrictModel
if TYPE_CHECKING:
    from .bootstrap import SourceBuild
    
MAX_TOOL_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
TARGET_OS = "ubuntu-24.04"
TARGET_ARCH = "x86_64"


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _regular(path: Path, label: str) -> Path:
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise ValueError(f"{label} must be a regular non-symlink file with safe parents")
    return path


class BuildReceipt(StrictModel):
    tool: str
    origin: str = "controller-prebuilt"
    source_url: str
    source_reference: str
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    upstream_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    patch_path: str
    patch_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    c_compiler_version: str = Field(min_length=1)
    cxx_compiler_version: str = Field(min_length=1)
    compiler_command: str = "cc"
    c_target_triple: str = Field(min_length=1)
    cxx_target_triple: str = Field(min_length=1)
    configure_flags: list[str]
    make_jobs: int = 2
    target_os: str = TARGET_OS
    target_arch: str = TARGET_ARCH
    elf_class: str = "ELF64"
    elf_machine: str = "Advanced Micro Devices X86-64"
    dynamic_abi: str = Field(min_length=1)
    binary_path: str
    binary_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    binary_version: str = Field(min_length=1)
    capabilities: list[str]
    acquisition_marker: str = "CNB_ACQ_V1"
    build_command: str = Field(min_length=1)
    build_argv: list[list[str]]
    build_evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def pinned(self):
        expected = {
            "sockperf": ("https://codeload.github.com/Mellanox/sockperf/tar.gz/5ebd327da983225321818c0355db922515e026bd",
                "5ebd327da983225321818c0355db922515e026bd",
                "bfee91183037577a0861c24c27dd9c644134a644ac2edead0ab460f4875ac5fc",
                "sockperf-3.10-enabled-v1"),
            "iperf3": ("https://codeload.github.com/esnet/iperf/tar.gz/2acfcfe94e928e74542c9f107e02aa6dd4748a79",
                "2acfcfe94e928e74542c9f107e02aa6dd4748a79",
                "11b96daadb4a1f17dc24376bb3d0d7feec330efeedc550b1979b1566e9b81661",
                "iperf-3.17.1-stats-v1"),
        }
        if self.tool not in expected or self.origin != "controller-prebuilt":
            raise ValueError("receipt must declare a supported tool and controller-prebuilt origin")
        url, commit, patch, _ = expected[self.tool]
        actual_urls = {url}
        if self.source_url not in actual_urls or self.upstream_commit != commit or self.patch_sha256 != patch:
            raise ValueError("receipt source or packaged patch pin is not production-approved")
        if self.make_jobs != 2 or self.target_os != TARGET_OS or self.target_arch != TARGET_ARCH:
            raise ValueError("receipt build settings or target do not match the approved bundle target")
        if not self.dynamic_abi.strip() or not self.c_compiler_version.strip() or not self.cxx_compiler_version.strip():
            raise ValueError("compiler and dynamic ABI evidence must be observed")
        if self.c_target_triple != self.cxx_target_triple or self.compiler_command != "cc":
            raise ValueError("C/C++ compiler targets must agree and the configured compiler command must be cc")
        if self.acquisition_marker != "CNB_ACQ_V1":
            raise ValueError("receipt must identify the patched acquisition marker")
        expected_flags = (["./configure", "--prefix=/var/lib/cnb/install"] if self.tool == "sockperf" else
                          ["./configure", "--prefix=/var/lib/cnb/install", "--disable-shared"])
        if self.configure_flags != expected_flags:
            raise ValueError("configure flags do not match the pinned build recipe")
        expected_path = "/var/lib/cnb/install/bin/" + self.tool
        if self.binary_path != expected_path:
            raise ValueError("receipt binary_path must be the approved configured install path")
        patch = [row for row in self.build_argv if row and row[0] == "patch"]
        if len(patch) != 1 or patch[0][:5] != ["patch", "--batch", "--fuzz=0", "-p1", "-i"] or len(patch[0]) != 6 or not Path(patch[0][5]).is_absolute():
            raise ValueError("build evidence must retain the exact patch argv")
        commands = [row for row in self.build_argv if row and row[0] != "patch"]
        expected_commands = ([ ["./autogen.sh"], expected_flags, ["make", "-j2"]]
            if self.tool == "sockperf" else [expected_flags, ["make", "-j2"]])
        if commands[:-1] != expected_commands or commands[-1][:2] != ["make", "install"] or not any(x.startswith("DESTDIR=") for x in commands[-1][2:]):
            raise ValueError("build argv must match the pinned configure/make/private-install recipe")
        abi = re.fullmatch(r"glibc-(\d+)\.(\d+)", self.dynamic_abi)
        if abi is None or tuple(map(int, abi.groups())) > (2, 39):
            raise ValueError("ELF glibc requirement must be no newer than Ubuntu 24.04")
        required_capabilities = ({"server", "under-load", "--full-rtt"} if self.tool == "sockperf" else
            {"--json-stream", "--forceflush", "--get-server-output"})
        if not required_capabilities <= set(self.capabilities):
            raise ValueError("receipt capability evidence is incomplete")
        return self


def _production(source: "SourceBuild", receipt: BuildReceipt, binary: Path) -> dict[str, Any]:
    if receipt.tool != source.tool or receipt.source_reference != source.source_reference:
        raise ValueError("receipt tool/source reference does not match production SourceBuild")
    if (receipt.source_sha256 != source.source_sha256
            or receipt.upstream_commit != source.upstream_commit
            or receipt.patch_path != source.observation_patch_path
            or receipt.patch_sha256 != source.observation_patch_sha256):
        raise ValueError("receipt source/archive/patch provenance does not match production SourceBuild")
    if source.upstream_commit is None or source.observation_patch_sha256 is None:
        raise ValueError("prebuilt bundles require the pinned CNB_ACQ_V1 production sources")
    size = binary.stat().st_size
    if size <= 0 or size > MAX_TOOL_BYTES:
        raise ValueError("binary size must be within the 256 MiB bound")
    if not (binary.stat().st_mode & 0o111):
        raise ValueError("supplied binary must retain executable mode")
    if _sha(binary) != receipt.binary_sha256:
        raise ValueError("receipt binary SHA-256 does not match supplied bytes")
    if not os.access(binary, os.X_OK):
        raise ValueError("supplied tool binary is not executable")
    return receipt.model_dump(mode="json") | {"binary_size_bytes": size}


def _receipt_from_evidence(raw: dict[str, Any], source: "SourceBuild", binary_sha256: str,
                           binary_size: int, evidence_sha256: str) -> BuildReceipt:
    if (raw.get("schema") != "CNB_OFFLINE_OPERATIONAL_BUILD_EVIDENCE_V1"
            or raw.get("origin") != "controller-prebuilt" or raw.get("not_framework_guest_receipt") is not True
            or raw.get("provider_calls") != 0 or raw.get("success") is not True):
        raise ValueError("operational build evidence origin or offline status is invalid")
    if (raw.get("framework_head") != "b52be5252fa081d62b7ef4bd102873908360a200"
            or not re.fullmatch(r"[a-f0-9]{64}", str(raw.get("driver_sha256", "")))
            or raw.get("offline_artifact_build_ceiling_seconds", 0) > 900
            or raw.get("production_command_seconds_unchanged") != 90):
        raise ValueError("operational evidence must bind the reviewed clean base and build driver")
    src = raw.get("source", {})
    if (src.get("tool") != source.tool or src.get("source_reference") != source.source_reference
            or src.get("source_sha256") != source.source_sha256 or src.get("upstream_commit") != source.upstream_commit
            or src.get("observation_patch_path") != source.observation_patch_path
            or src.get("observation_patch_sha256") != source.observation_patch_sha256
            or raw.get("archive_sha256") != source.source_sha256 or raw.get("patch_sha256") != source.observation_patch_sha256
            or raw.get("configured_prefix") != "/var/lib/cnb/install"):
        raise ValueError("operational build evidence does not match production archive, patch or prefix pins")
    patch_file = Path(__file__).parent / str(source.observation_patch_path)
    if patch_file.is_symlink() or not patch_file.is_file() or _sha(patch_file) != source.observation_patch_sha256:
        raise ValueError("packaged observation patch bytes do not match the production pin")
    release, uname = raw.get("target_os_release", ""), raw.get("uname", {})
    if "VERSION_ID=\"24.04\"" not in release or "ID=ubuntu" not in release or uname.get("returncode") != 0 or "x86_64" not in uname.get("stdout", ""):
        raise ValueError("operational build evidence target is not Ubuntu 24.04 x86-64")
    compilers, triples = raw.get("compilers", {}), raw.get("target_triples", {})
    for key, argv in (("cc", ["cc", "--version"]), ("c++", ["c++", "--version"])):
        item = compilers.get(key, {})
        if item.get("argv") != argv or item.get("returncode") != 0 or not item.get("stdout"):
            raise ValueError(f"operational evidence lacks actual {key} version output")
    for key, argv in (("cc", ["cc", "-dumpmachine"]), ("c++", ["c++", "-dumpmachine"])):
        item = triples.get(key, {})
        if item.get("argv") != argv or item.get("returncode") != 0 or not item.get("stdout", "").strip():
            raise ValueError(f"operational evidence lacks actual {key} target triple")
    c_target, cxx_target = triples["cc"]["stdout"].strip(), triples["c++"]["stdout"].strip()
    if c_target != cxx_target or "x86_64" not in c_target:
        raise ValueError("operational C and C++ target triples do not agree on x86-64")
    flags = ["./configure", "--prefix=/var/lib/cnb/install"] + (["--disable-shared"] if source.tool == "iperf3" else [])
    steps = raw.get("steps")
    if not isinstance(steps, list) or len(steps) != (4 if source.tool == "iperf3" else 5):
        raise ValueError("operational build evidence command count is unexpected")
    patch_step = steps[0]
    expected_patch_argv = ["patch", "--batch", "--fuzz=0", "-p1", "-i"]
    patch_argv = patch_step.get("argv", [])
    if (patch_argv[:5] != expected_patch_argv or len(patch_argv) != 6
            or Path(patch_argv[5]).name != Path(source.observation_patch_path).name
            or not Path(patch_argv[5]).is_absolute() or patch_step.get("returncode") != 0):
        raise ValueError("operational patch command does not match pinned packaged patch")
    expected_commands = [["./autogen.sh"], flags, ["make", "-j2"], ["make", "install"]]
    if source.tool == "iperf3":
        expected_commands = [flags, ["make", "-j2"], ["make", "install"]]
    observed_commands = [step.get("argv", []) for step in steps[1:]]
    if observed_commands[:-1] != expected_commands[:-1]:
        raise ValueError("operational build argv order or settings differ from the approved recipe")
    configure_index = 2 if source.tool == "sockperf" else 1
    configure = steps[configure_index]
    if configure.get("argv") != flags or configure.get("returncode") != 0:
        raise ValueError("operational configure argv/status does not match approved flags")
    make = steps[configure_index + 1]
    install = steps[-1]
    if make.get("argv") != ["make", "-j2"] or make.get("returncode") != 0:
        raise ValueError("operational make argv/status does not match approved two-worker build")
    install_argv = install.get("argv", [])
    if (install.get("returncode") != 0 or len(install_argv) != 3 or install_argv[:2] != ["make", "install"]
            or not install_argv[2].startswith("DESTDIR=/") or ".." in Path(install_argv[2].split("=",1)[1]).parts):
        raise ValueError("operational install must use private DESTDIR staging")
    if any(step.get("returncode") != 0 for step in steps):
        raise ValueError("every recorded patch/build/install command must have succeeded")
    env = raw.get("environment", {})
    if (set(env) != {"PATH", "HOME", "LANG", "LC_ALL"}
            or env.get("PATH") != "/usr/bin:/bin" or env.get("LANG") != "C" or env.get("LC_ALL") != "C"
            or not isinstance(env.get("HOME"), str) or not Path(env["HOME"]).is_absolute()
            ):
        raise ValueError("operational build environment is not the reviewed clean compiler environment")
    b = raw.get("binary", {})
    if b.get("sha256") != binary_sha256 or b.get("size_bytes") != binary_size or b.get("acquisition_marker_present") is not True:
        raise ValueError("operational binary evidence does not match bundle bytes or acquisition marker")
    for name in ("version", "help", "file", "elf", "shared_libraries"):
        if b.get(name, {}).get("returncode") != 0:
            raise ValueError(f"operational binary {name} inspection did not succeed")
    version = b["version"].get("stdout", "")
    helptext = b["help"].get("stdout", "")
    from .bootstrap import _required_capabilities, _capabilities_observed
    required_capabilities = _required_capabilities(source.tool)
    capability_text = helptext
    if source.tool == "sockperf":
        modes = b.get("mode_help", {})
        if set(modes) != {"server", "under-load"} or any(x.get("returncode") != 0 for x in modes.values()):
            raise ValueError("Sockperf operational evidence lacks successful server and under-load help")
        capability_text += "\n" + "\n".join(x.get("stdout", "") for x in modes.values())
    if not _capabilities_observed(capability_text, source.tool, required=required_capabilities):
        raise ValueError("operational binary help evidence lacks tool-specific required capabilities")
    elf = b["elf"].get("stdout", "")
    if "ELF64" not in elf or "Advanced Micro Devices X86-64" not in elf or "/lib64/ld-linux-x86-64.so.2" not in elf:
        raise ValueError("operational ELF evidence has incompatible class, machine, or loader")
    versions = re.findall(r"Name: GLIBC_(\d+\.\d+)", elf)
    if not versions:
        raise ValueError("operational ELF evidence lacks required glibc symbol versions")
    maximum = max((tuple(map(int, v.split("."))) for v in versions))
    if maximum > (2, 39):
        raise ValueError("binary requires glibc newer than Ubuntu 24.04")
    shared = b["shared_libraries"].get("stdout", "")
    if "not found" in shared or not shared.strip():
        raise ValueError("operational shared-library evidence is incomplete")
    binary_rel = "/var/lib/cnb/install/bin/" + source.tool
    build_argv = [step["argv"] for step in steps]
    receipt = BuildReceipt(tool=source.tool, source_url=src["source_url"], source_reference=source.source_reference,
        source_sha256=source.source_sha256, upstream_commit=source.upstream_commit,
        patch_path=source.observation_patch_path, patch_sha256=source.observation_patch_sha256,
        c_compiler_version=compilers["cc"]["stdout"].strip(), cxx_compiler_version=compilers["c++"]["stdout"].strip(),
        compiler_command="cc", c_target_triple=c_target, cxx_target_triple=cxx_target,
        configure_flags=flags, binary_path=binary_rel, binary_sha256=binary_sha256, binary_version=version,
        capabilities=required_capabilities,
        dynamic_abi="glibc-" + ".".join(map(str, maximum)), build_command="imported operational build evidence",
        build_argv=build_argv, build_evidence_sha256=evidence_sha256)
    return receipt


def build_bundle(*, sources: list[SourceBuild], binaries: dict[str, Path], build_evidence: dict[str, Path],
                 output: Path) -> dict[str, Any]:
    """Package exact pinned binaries with byte-bound imported operational evidence."""
    if {s.tool for s in sources} != {"sockperf", "iperf3"} or set(binaries) != {"sockperf", "iperf3"} or set(build_evidence) != {"sockperf", "iperf3"}:
        raise ValueError("exactly Sockperf and iperf3 sources, binaries and operational evidence are required")
    by_tool = {s.tool: s for s in sources}
    rows = {}
    safe_inputs = {}; evidence_payloads = {}
    for tool in ("sockperf", "iperf3"):
        binary = _regular(Path(binaries[tool]), f"{tool} binary")
        evidence_path = _regular(Path(build_evidence[tool]), f"{tool} operational build evidence")
        if evidence_path.stat().st_size > 1024 * 1024:
            raise ValueError("operational build evidence is over 1 MiB")
        try:
            raw_bytes = evidence_path.read_bytes()
            raw_evidence = json.loads(raw_bytes)
            receipt = _receipt_from_evidence(raw_evidence, by_tool[tool], _sha(binary), binary.stat().st_size,
                hashlib.sha256(raw_bytes).hexdigest())
        except Exception as exc:
            raise ValueError(f"{tool} operational build evidence/binary is invalid for production pins") from exc
        rows[tool] = receipt.model_dump(mode="json") | {"binary_size_bytes": binary.stat().st_size,
            "operational_build_evidence_json": raw_bytes.decode("utf-8")}
        safe_inputs[tool] = binary
        evidence_payloads[tool] = raw_bytes
    output = Path(output).absolute()
    if any(p.is_symlink() for p in (output, *output.parents)) or output.exists():
        raise ValueError("bundle output must be a new path with safe non-symlink parents")
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": 1, "origin": "controller-prebuilt", "target_os": TARGET_OS,
        "target_arch": TARGET_ARCH, "tools": rows}
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent, delete=False) as f:
        json.dump(manifest, f, sort_keys=True, indent=2); f.write("\n"); manifest_path = Path(f.name)
    try:
        with tarfile.open(output, "w:gz", compresslevel=9) as tar:
            for tool in ("sockperf", "iperf3"):
                info = tar.gettarinfo(str(safe_inputs[tool]), arcname=f"bin/{tool}")
                info.uid = info.gid = 0; info.uname = info.gname = ""; info.mtime = 0
                with safe_inputs[tool].open("rb") as f:
                    tar.addfile(info, f)
            info = tar.gettarinfo(str(manifest_path), arcname="manifest.json")
            info.uid = info.gid = 0; info.uname = info.gname = ""; info.mtime = 0
            with manifest_path.open("rb") as f:
                tar.addfile(info, f)
    except Exception:
        output.unlink(missing_ok=True)
        raise
    finally:
        manifest_path.unlink(missing_ok=True)
    if output.stat().st_size > MAX_ARCHIVE_BYTES:
        output.unlink(missing_ok=True)
        raise ValueError("compressed bundle exceeds the 64 MiB bound")
    return {"path": str(output), "sha256": _sha(output), "size_bytes": output.stat().st_size,
            "manifest": manifest}


def inspect_bundle(path: Path, expected_sha256: str, sources: list["SourceBuild"]) -> dict[str, Any]:
    """Read a bundle safely; reject links, duplicates, extras, expansion and digest mismatch."""
    path = _regular(path, "bundle")
    if path.stat().st_size > MAX_ARCHIVE_BYTES or _sha(path) != expected_sha256:
        raise ValueError("bundle compressed size or SHA-256 does not match its declaration")
    allowed = {"manifest.json", "bin/sockperf", "bin/iperf3"}
    seen = set(); total = 0
    with tarfile.open(path, "r:gz") as tar:
        members: dict[str, tarfile.TarInfo] = {}
        for member in tar:
            name = member.name
            if name not in allowed or name in seen or not member.isfile() or member.issym() or member.islnk():
                raise ValueError("bundle contains an unapproved, duplicate, or non-regular member")
            if name.startswith("bin/") and not (member.mode & 0o111):
                raise ValueError("bundled tool binary does not retain executable mode")
            seen.add(name); total += member.size; members[name] = member
            if total > MAX_TOOL_BYTES * 2 + 1024 * 1024:
                raise ValueError("bundle expanded size exceeds its bound")
        if seen != allowed:
            raise ValueError("bundle must contain exactly manifest and both tool binaries")
        stream = tar.extractfile("manifest.json")
        if stream is None: raise ValueError("bundle manifest is missing")
        manifest_bytes = stream.read(1024 * 1024 + 1)
        if len(manifest_bytes) > 1024 * 1024:
            raise ValueError("bundle manifest exceeds the 1 MiB bound")
        data = json.loads(manifest_bytes)
        if not isinstance(data, dict) or data.get("schema_version") != 1 or data.get("origin") != "controller-prebuilt":
            raise ValueError("bundle manifest origin or schema is invalid")
        if data.get("target_os") != TARGET_OS or data.get("target_arch") != TARGET_ARCH:
            raise ValueError("bundle target is not Ubuntu 24.04 x86-64")
        rows = data.get("tools")
        if not isinstance(rows, dict) or set(rows) != {"sockperf", "iperf3"}:
            raise ValueError("bundle must declare exactly the two pinned tools")
        source_by_tool = {s.tool: s for s in sources}
        if set(source_by_tool) != {"sockperf", "iperf3"}:
            raise ValueError("production source declarations must include exactly both tools")
        for tool in ("sockperf", "iperf3"):
            row = rows[tool]
            if not isinstance(row, dict) or row.get("origin") != "controller-prebuilt":
                raise ValueError("tool origin proof is missing or invalid")
            binary_member = members[f"bin/{tool}"]
            if binary_member.size > MAX_TOOL_BYTES or binary_member.size != row.get("binary_size_bytes"):
                raise ValueError("bundle binary size disagrees with its bounded receipt")
            stream = tar.extractfile(binary_member)
            if stream is None:
                raise ValueError("bundle binary is unavailable")
            digest = hashlib.sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
            actual_binary_sha = digest.hexdigest()
            evidence_text = row.get("operational_build_evidence_json")
            if not isinstance(evidence_text, str) or len(evidence_text.encode("utf-8")) > 1024 * 1024:
                raise ValueError("bundle lacks bounded operational build evidence")
            evidence_bytes = evidence_text.encode("utf-8")
            source = source_by_tool[tool]
            try:
                receipt = _receipt_from_evidence(json.loads(evidence_bytes), source, actual_binary_sha,
                    binary_member.size, hashlib.sha256(evidence_bytes).hexdigest())
                declared = {k: v for k, v in row.items()
                    if k not in {"binary_size_bytes", "operational_build_evidence_json"}}
                if receipt.model_dump(mode="json") != declared:
                    raise ValueError("manifest receipt disagrees with operational evidence")
            except Exception as exc:
                raise ValueError(f"{tool} operational evidence does not match production pins or binary bytes") from exc
            if actual_binary_sha != receipt.binary_sha256:
                raise ValueError("bundle binary bytes do not match operational evidence")
        return data


def validate_bundle(path: Path, expected_sha256: str, sources: list["SourceBuild"]) -> dict[str, Any]:
    """Validate artifact, source pins, receipt and actual binary bytes before VM creation."""
    return inspect_bundle(path, expected_sha256, sources)


def linux_installer_script(bundle_path: str, bundle_sha256: str, install_prefix: str, role: str,
                           required_capabilities: dict[str, list[str]],
                           source_pins: dict[str, dict[str, str]], *,
                           test_filesystem_root: Path | None = None, test_only: bool = False) -> str:
    """Return a fixed installer/verifier; manifests cannot supply commands or paths."""
    if not re.fullmatch(r"/var/lib/cnb/[A-Za-z0-9._/-]+", bundle_path) or ".." in Path(bundle_path).parts:
        raise ValueError("remote bundle path must be under /var/lib/cnb")
    if install_prefix != "/var/lib/cnb/install" or not re.fullmatch(r"[a-f0-9]{64}", bundle_sha256) or role not in {"vm_a", "vm_b"}:
        raise ValueError("installer target or bundle digest is invalid")
    fsroot = Path("/")
    if test_filesystem_root is not None:
        fsroot = Path(test_filesystem_root).absolute()
        if not test_only or fsroot == Path("/") or any(p.is_symlink() for p in (fsroot, *fsroot.parents)):
            raise ValueError("filesystem-root mapping is test-only and must use a safe private root")
    script = r'''import base64,hashlib,json,os,platform,re,subprocess,sys,tarfile,tempfile,pathlib
BUNDLE_LOGICAL=__BUNDLE__; DIGEST=__DIGEST__; PREFIX_LOGICAL=__PREFIX__; FSROOT=pathlib.Path(__FSROOT__); ROLE=__ROLE__; PINS=__PINS__
BUNDLE=FSROOT/str(BUNDLE_LOGICAL).lstrip("/"); PREFIX=FSROOT/str(PREFIX_LOGICAL).lstrip("/")
MAX_BINARY=256*1024*1024; ALLOWED={"manifest.json","bin/sockperf","bin/iperf3"}
def run(argv):
 p=subprocess.run(argv,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=8)
 if p.returncode: raise RuntimeError("verification command failed: "+argv[0])
 return p.stdout.strip()
def sha(p):
 h=hashlib.sha256()
 with open(p,"rb") as f:
  for b in iter(lambda:f.read(1048576),b""):h.update(b)
 return h.hexdigest()
def safe_path(p):
 p=pathlib.Path(p)
 while p!=p.parent:
  if p.is_symlink(): return False
  p=p.parent
 return True
def stable(s,tool):
 m=re.search(r"(?<!\d)(\d+\.\d+(?:\.\d+)?)(?:-no\.git)?",s.splitlines()[0] if s.splitlines() else "")
 return m.group(1) if m else None
if platform.system()!="Linux" or platform.machine() not in ("x86_64","amd64"): raise RuntimeError("guest target OS/architecture mismatch")
osrel=pathlib.Path("/etc/os-release").read_text()
if not re.search(r"(?m)^ID=ubuntu$",osrel) or not re.search(r'(?m)^VERSION_ID="?24\.04"?$',osrel): raise RuntimeError("guest is not Ubuntu 24.04")
if not safe_path(BUNDLE) or not BUNDLE.is_file() or BUNDLE.stat().st_size>64*1024*1024 or sha(BUNDLE)!=DIGEST: raise RuntimeError("uploaded bundle digest or file type mismatch")
with tarfile.open(BUNDLE,"r:gz") as t:
 ms=[]
 for m in t:
  ms.append(m)
  if len(ms)>3: raise RuntimeError("bundle has too many members")
 names=[m.name for m in ms]
 if len(names)!=3 or len(set(names))!=3 or set(names)!=ALLOWED: raise RuntimeError("bundle member set invalid")
 if any(not m.isfile() or m.issym() or m.islnk() or m.size<0 for m in ms): raise RuntimeError("bundle contains non-regular entries")
 if sum(m.size for m in ms)>2*MAX_BINARY+1048576: raise RuntimeError("bundle expanded-size bound exceeded")
 mf=t.extractfile("manifest.json")
 if mf is None: raise RuntimeError("bundle manifest missing")
 manifest_bytes=mf.read(1048577)
 if len(manifest_bytes)>1048576: raise RuntimeError("bundle manifest size bound exceeded")
 manifest=json.loads(manifest_bytes)
 if manifest.get("schema_version")!=1 or manifest.get("origin")!="controller-prebuilt" or manifest.get("target_os")!="ubuntu-24.04" or manifest.get("target_arch")!="x86_64": raise RuntimeError("bundle origin/target invalid")
 tools=manifest.get("tools")
 if not isinstance(tools,dict) or set(tools)!={"sockperf","iperf3"}: raise RuntimeError("bundle tools invalid")
 temp=pathlib.Path(tempfile.mkdtemp(prefix="cnb-bundle-"))
 try:
  results={}
  for tool in ("sockperf","iperf3"):
   row=tools[tool]
   if row.get("origin")!="controller-prebuilt" or row.get("target_os")!="ubuntu-24.04" or row.get("target_arch")!="x86_64": raise RuntimeError("tool origin/target invalid")
   if any(row.get(k)!=v for k,v in PINS[tool].items()): raise RuntimeError("source/archive/patch receipt mismatch")
   eb=row.get("operational_build_evidence_json")
   if not isinstance(eb,str) or hashlib.sha256(eb.encode()).hexdigest()!=row.get("build_evidence_sha256"): raise RuntimeError("controller build evidence digest mismatch")
   evidence=json.loads(eb)
   if evidence.get("origin")!="controller-prebuilt" or evidence.get("not_framework_guest_receipt") is not True or evidence.get("provider_calls")!=0 or evidence.get("success") is not True: raise RuntimeError("controller operational evidence origin invalid")
   if evidence.get("binary",{}).get("sha256")!=row.get("binary_sha256") or evidence.get("source",{}).get("upstream_commit")!=row.get("upstream_commit"): raise RuntimeError("controller build evidence does not bind tool/source receipt")
   member=t.extractfile("bin/"+tool)
   if member is None: raise RuntimeError("tool member missing")
   out=temp/tool; h=hashlib.sha256(); size=0
   with open(out,"xb") as f:
    for b in iter(lambda:member.read(1048576),b""):
     size+=len(b)
     if size>MAX_BINARY: raise RuntimeError("binary size bound exceeded")
     h.update(b); f.write(b)
   if size!=row.get("binary_size_bytes") or h.hexdigest()!=row.get("binary_sha256"): raise RuntimeError("binary bytes differ from reviewed receipt")
   out.chmod(0o700)
   if "CNB_ACQ_V1" not in out.read_bytes().decode("latin1"): raise RuntimeError("acquisition marker missing")
   hdr=run(["readelf","-h",str(out)])
   if "ELF64" not in hdr or "Advanced Micro Devices X86-64" not in hdr: raise RuntimeError("ELF target mismatch")
   ph=run(["readelf","-l",str(out)])
   if "/lib64/ld-linux-x86-64.so.2" not in ph: raise RuntimeError("unexpected ELF interpreter")
   ver=run(["readelf","--version-info",str(out)])
   glibc={tuple(map(int,x.split("."))) for x in re.findall(r"GLIBC_(\d+\.\d+)",ver)}
   if not glibc or max(glibc)>(2,39): raise RuntimeError("ELF glibc requirements exceed Ubuntu 24.04")
   ld=pathlib.Path("/lib64/ld-linux-x86-64.so.2")
   if not ld.exists(): raise RuntimeError("guest dynamic loader missing")
   libc=run(["ldd","--version"])
   if "2.39" not in libc: raise RuntimeError("guest glibc version mismatch")
   deps=run(["ldd",str(out)])
   if "not found" in deps: raise RuntimeError("guest dynamic dependency is missing")
   version=run([str(out),"--version"])
   if stable(version,tool)!=stable(row.get("binary_version",""),tool): raise RuntimeError("guest binary version differs from controller build")
   helptext=run([str(out),"--help"])
   required=CAPS[tool]
   mode_outputs={}
   if tool=="iperf3" and any(x not in helptext for x in required): raise RuntimeError("required tool help flags missing")
   if tool=="sockperf":
    for sub,flags in (("server",("-i","-p")),("under-load",("-i","-p","--full-rtt","--no-rdtsc","--full-log"))):
     subhelp=run([str(out),sub,"--help"])
     if any(x not in subhelp for x in flags): raise RuntimeError("Sockperf required subcommand options missing")
     mode_outputs[sub]=subhelp
   if tool=="iperf3":
    import socket
    s=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    try:
     s.setsockopt(socket.IPPROTO_TCP,socket.TCP_CONGESTION,b"cubic")
     if s.getsockopt(socket.IPPROTO_TCP,socket.TCP_CONGESTION,16).rstrip(b"\\0")!=b"cubic": raise RuntimeError("CUBIC congestion control readback mismatch")
    finally:s.close()
   results[tool]={"binary_version":version,"binary_sha256":h.hexdigest(),"binary_path":str(PREFIX/"bin"/tool),"elf_header":hdr,"elf_program_headers":ph,"elf_version_info":ver,"guest_glibc":libc,"guest_dependencies":deps,"capabilities":required,"help_outputs":{"main":helptext,"modes":mode_outputs}}
  for p in (FSROOT,BUNDLE.parent,PREFIX.parent):
   if not safe_path(p) or p.exists() and not p.is_dir(): raise RuntimeError("bundle install path is unsafe")
  PREFIX.mkdir(parents=True,exist_ok=True)
  for tool in ("sockperf","iperf3"):
   target=PREFIX/"bin"/tool; target.parent.mkdir(parents=True,exist_ok=True)
   if not safe_path(target.parent): raise RuntimeError("binary destination parent traverses a symlink")
   marker=PREFIX/(".f02r-"+tool+".identity"); side=PREFIX/(".f02r-"+tool+".build-identity"); origin=PREFIX/(".cnb-"+tool+".origin.json")
   tmp=target.with_name(target.name+".cnb-tmp")
   if any(p.exists() or p.is_symlink() for p in (target,marker,side,origin,tmp)): raise RuntimeError("pre-existing binary/cache/origin/temp state requires explicit reconciliation")
  if any(PREFIX.glob(".f02r-*") ) or any(PREFIX.glob(".cnb-*") ): raise RuntimeError("unmatched source/build/origin cache state exists")
  for tool in ("sockperf","iperf3"):
   row=tools[tool]; r=results[tool]; src=pathlib.Path(__file__) if False else None
   target=PREFIX/"bin"/tool; tmp=target.with_name(target.name+".cnb-tmp")
   try:
    h=hashlib.sha256(); size=0
    with open(temp/tool,"rb") as src, open(tmp,"xb") as dst:
     for block in iter(lambda:src.read(1048576),b""):
      size+=len(block)
      if size>MAX_BINARY: raise RuntimeError("installed staging size exceeded bound")
      h.update(block); dst.write(block)
    if size!=row["binary_size_bytes"] or h.hexdigest()!=row["binary_sha256"]: raise RuntimeError("same-filesystem staging copy hash mismatch")
    tmp.chmod(0o755); os.replace(tmp,target)
   finally: tmp.unlink(missing_ok=True)
   if sha(target)!=row["binary_sha256"]: raise RuntimeError("installed target bytes differ from verified controller binary")
   import base64 as b64
   configure=" ".join(row["configure_flags"])
   spec=configure+"; make -j2"
   compiler=row["c_compiler_version"]
   cfg="CC=${CC:-cc}; compiler="+compiler+"; "+spec
   cache_config=hashlib.sha256(("cc\n"+compiler+"\n"+spec+"\n").encode()).hexdigest()
   semantics="sockperf-3.10-enabled-v1" if tool=="sockperf" else "iperf-3.17.1-stats-v1"
   caps=",".join(row["capabilities"])
   identity="\n".join(("CNB_BUILD_V1","tool="+tool,"source_reference="+row["source_reference"],"source_sha256="+row["source_sha256"],"upstream_commit="+row["upstream_commit"],"patch_path="+row["patch_path"],"patch_sha256="+row["patch_sha256"],"compiler_version_b64="+b64.b64encode(row["c_compiler_version"].encode()).decode(),"build_configuration_b64="+b64.b64encode(cfg.encode()).decode(),"binary_path="+str(target),"binary_version_b64="+b64.b64encode(r["binary_version"].encode()).decode(),"binary_sha256="+r["binary_sha256"],"observation_capability=CNB_ACQ_V1","observation_semantics="+semantics,"capabilities="+caps))+"\n"
   origin={"schema":"CNB_PREBUILT_V1","origin":"controller-prebuilt","tool":tool,"binary_sha256":r["binary_sha256"],"build_host":{k:row[k] for k in ("c_compiler_version","cxx_compiler_version","c_target_triple","cxx_target_triple")},"controller_receipt":row,"guest_verification":dict(r),"bundle_sha256":DIGEST,"bundle_artifact_id":"f02r-controller-prebuilt-bundle","role":ROLE}
   origin_text=json.dumps(origin,sort_keys=True)+"\n"
   for p,data in ((PREFIX/(".f02r-"+tool+".identity")," ".join((row["source_reference"],row["source_sha256"],row["patch_sha256"],cache_config,r["binary_sha256"]))+"\n"),(PREFIX/(".f02r-"+tool+".build-identity"),identity),(PREFIX/(".cnb-"+tool+".origin.json"),origin_text)):
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w") as f:f.write(data)
   r["identity_bytes"]=identity; r["origin_proof_bytes"]=origin_text
  BUNDLE.unlink()
  print(json.dumps(results,sort_keys=True))
 finally:
  import shutil; shutil.rmtree(temp,ignore_errors=True)
'''
    encoded = base64.b64encode(json.dumps({"bundle": bundle_path, "digest": bundle_sha256,
    "prefix": install_prefix, "role": role, "caps": required_capabilities, "pins": source_pins}).encode()).decode()
    script = script.replace("__BUNDLE__", "json.loads(base64.b64decode(" + repr(encoded) + "))['bundle']")
    script = script.replace("__DIGEST__", "json.loads(base64.b64decode(" + repr(encoded) + "))['digest']")
    script = script.replace("__PREFIX__", "json.loads(base64.b64decode(" + repr(encoded) + "))['prefix']")
    script = script.replace("__ROLE__", "json.loads(base64.b64decode(" + repr(encoded) + "))['role']")
    script = script.replace("__PINS__", "json.loads(base64.b64decode(" + repr(encoded) + "))['pins']")
    script = script.replace("__FSROOT__", repr(str(fsroot)))
    script = "CAPS=" + repr(required_capabilities) + "\n" + script
    return script
