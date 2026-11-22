from __future__ import annotations

import hashlib
import ast
import base64
import json
import os
from pathlib import Path
import sys
import subprocess
import re
import tarfile
import io

import pytest

from cloud_network_benchmark.bootstrap import BootstrapConfig, SourceBuild
from cloud_network_benchmark.build_bundle import build_bundle, linux_installer_script, validate_bundle


COMMITS = {
    "sockperf": ("5ebd327da983225321818c0355db922515e026bd",
        "tool_patches/sockperf-3.10-acquisition.patch",
        "bfee91183037577a0861c24c27dd9c644134a644ac2edead0ab460f4875ac5fc",
        "a35a1daf310551b9f675af4fb7d95a6a10389fda4c7605d70a21554be0773f8e",
        "https://codeload.github.com/Mellanox/sockperf/tar.gz/5ebd327da983225321818c0355db922515e026bd"),
    "iperf3": ("2acfcfe94e928e74542c9f107e02aa6dd4748a79",
        "tool_patches/iperf-3.17.1-acquisition.patch",
        "11b96daadb4a1f17dc24376bb3d0d7feec330efeedc550b1979b1566e9b81661",
        "d6b4d2c7b20157c5b7fbd2de66f22bd3428958b5f2e28899151b6e22745886fe",
        "https://codeload.github.com/esnet/iperf/tar.gz/2acfcfe94e928e74542c9f107e02aa6dd4748a79"),
}


def source(tool: str) -> SourceBuild:
    commit, patch, patch_sha, source_sha, url = COMMITS[tool]
    return SourceBuild(tool=tool, source_url=url, source_reference=("sockperf-" if tool == "sockperf" else "iperf-") + commit,
        archive_name="sockperf.tar.gz" if tool == "sockperf" else "iperf3.tar.gz",
        source_sha256=source_sha, upstream_commit=commit, observation_patch_path=patch,
        observation_patch_sha256=patch_sha)


def _result(argv, stdout):
    return {"argv": argv, "returncode": 0, "stdout": stdout, "stderr": ""}


def evidence(tool: str, binary: bytes, temp: Path) -> dict:
    src = source(tool); commit, patch, patch_sha, source_sha, url = COMMITS[tool]
    flags = ["./configure", "--prefix=/var/lib/cnb/install"] + (["--disable-shared"] if tool == "iperf3" else [])
    patch_argv = ["patch", "--batch", "--fuzz=0", "-p1", "-i", str(temp / Path(patch).name)]
    steps = [{"argv": patch_argv, "returncode": 0}]
    if tool == "sockperf": steps.append({"argv": ["./autogen.sh"], "returncode": 0})
    steps.extend([{"argv": flags, "returncode": 0}, {"argv": ["make", "-j2"], "returncode": 0},
        {"argv": ["make", "install", f"DESTDIR={temp}/stage"], "returncode": 0}])
    from cloud_network_benchmark.bootstrap import _required_capabilities
    required = _required_capabilities(tool)
    caps = "usage " + " ".join(required)
    binary_path = f"{temp}/stage/var/lib/cnb/install/bin/{tool}"
    version = "sockperf, version 3.10-no.git\n" if tool == "sockperf" else "iperf 3.17.1 (cJSON 1.7.15)\nLinux build-host 7.0 x86_64\n"
    b = {"path": binary_path, "sha256": hashlib.sha256(binary).hexdigest(), "size_bytes": len(binary),
        "version": _result([binary_path, "--version"], version),
        "help": _result([binary_path, "--help"], caps),
        "file": _result(["file", binary_path], "ELF 64-bit LSB pie executable x86-64"),
        "elf": _result(["readelf", "-h", "-l", "-V", binary_path],
            "ELF Header: ELF64 Machine: Advanced Micro Devices X86-64 Interpreter /lib64/ld-linux-x86-64.so.2 Name: GLIBC_2.38"),
        "shared_libraries": _result(["ldd", binary_path], "libc.so.6 => /lib/x86_64-linux-gnu/libc.so.6"),
        "acquisition_marker_present": True}
    if tool == "sockperf":
        b["mode_help"] = {name: _result([binary_path, name, "--help"], "usage " + " ".join(flags))
            for name, flags in {"server": ("server", "-i", "-p"),
                "under-load": ("under-load", "-i", "-p", "--full-rtt", "--no-rdtsc", "--full-log")}.items()}
    return {"schema": "CNB_OFFLINE_OPERATIONAL_BUILD_EVIDENCE_V1", "origin": "controller-prebuilt",
        "not_framework_guest_receipt": True, "provider_calls": 0, "success": True,
        "framework_head": "b52be5252fa081d62b7ef4bd102873908360a200", "driver_sha256": "a"*64,
        "source": {"tool": tool, "source_url": url, "source_reference": src.source_reference,
            "archive_name": src.archive_name, "source_sha256": source_sha,
            "observation_patch_path": patch, "observation_patch_sha256": patch_sha,
            "upstream_commit": commit}, "archive_sha256": source_sha, "patch_sha256": patch_sha,
        "configured_prefix": "/var/lib/cnb/install", "target_os_release": 'ID=ubuntu\nVERSION_ID="24.04"\n',
        "uname": _result(["uname", "-a"], "Linux host x86_64 x86_64 GNU/Linux"),
        "environment": {"PATH": "/usr/bin:/bin", "HOME": str(temp), "LANG": "C", "LC_ALL": "C"},
        "offline_artifact_build_ceiling_seconds": 900, "production_command_seconds_unchanged": 90,
        "compilers": {name: _result([name, "--version"], f"{name} (Ubuntu 13.3.0) 13.3.0\n")
            for name in ("cc", "c++")},
        "target_triples": {name: _result([name, "-dumpmachine"], "x86_64-linux-gnu\n")
            for name in ("cc", "c++")}, "steps": steps, "binary": b}


def make_bundle(tmp_path):
    sources, binaries, evidence_paths = [source(t) for t in ("sockperf", "iperf3")], {}, {}
    for tool in ("sockperf", "iperf3"):
        binary = tmp_path / tool; binary.write_bytes((tool + " bytes").encode()); binary.chmod(0o755)
        evidence_path = tmp_path / f"{tool}.json"
        evidence_path.write_text(json.dumps(evidence(tool, binary.read_bytes(), tmp_path)))
        binaries[tool] = binary; evidence_paths[tool] = evidence_path
    bundle = tmp_path / "bundle.tar.gz"
    declared = build_bundle(sources=sources, binaries=binaries, build_evidence=evidence_paths, output=bundle)
    return sources, bundle, declared


def test_imported_operational_evidence_builds_a_hash_bound_two_tool_bundle(tmp_path):
    sources, bundle_path, declared = make_bundle(tmp_path)
    assert declared["size_bytes"] < 64 * 1024 * 1024
    assert validate_bundle(bundle_path, declared["sha256"], sources)["origin"] == "controller-prebuilt"
    config = BootstrapConfig(sources=sources, remote_root="/var/lib/cnb", workspace="/var/lib/cnb/work",
        results_workspace="/var/lib/cnb/results", install_prefix="/var/lib/cnb/install",
        prebuilt_bundle={"path": str(bundle_path), "sha256": declared["sha256"]})
    assert config.prebuilt_bundle.sha256 == declared["sha256"]


@pytest.mark.parametrize("mutation", ["source", "patch", "compiler", "target", "command_order", "install_args"])
def test_operational_evidence_tampering_fails_bundle_import(tmp_path, mutation):
    sources, binaries, evidence_paths = [source(t) for t in ("sockperf", "iperf3")], {}, {}
    for tool in ("sockperf", "iperf3"):
        binary = tmp_path / tool; binary.write_bytes((tool + " bytes").encode()); binary.chmod(0o755)
        data = evidence(tool, binary.read_bytes(), tmp_path)
        if tool == "iperf3":
            if mutation == "source": data["archive_sha256"] = "f"*64
            elif mutation == "patch": data["patch_sha256"] = "f"*64
            elif mutation == "compiler": data["target_triples"]["c++"]["stdout"] = "aarch64-linux-gnu"
            elif mutation == "target": data["target_os_release"] = 'ID=ubuntu\nVERSION_ID="22.04"\n'
            elif mutation == "command_order": data["steps"][1], data["steps"][2] = data["steps"][2], data["steps"][1]
            elif mutation == "install_args": data["steps"][-1]["argv"].append("--unexpected")
        path = tmp_path / f"{tool}.json"; path.write_text(json.dumps(data))
        binaries[tool] = binary; evidence_paths[tool] = path
    with pytest.raises(ValueError):
        build_bundle(sources=sources, binaries=binaries, build_evidence=evidence_paths,
            output=tmp_path / "bad.tar.gz")


def test_emitted_installer_is_fixed_and_test_root_requires_explicit_opt_in(tmp_path):
    digest = "a" * 64
    caps = {t: ["--full-rtt"] if t == "sockperf" else ["--json-stream"] for t in ("sockperf", "iperf3")}
    pins = {t: {"source_sha256": "b"*64} for t in ("sockperf", "iperf3")}
    installer = linux_installer_script("/var/lib/cnb/work/prebuilt.tar.gz", digest,
        "/var/lib/cnb/install", "vm_a", caps, pins)
    compile(installer, "<generated-linux-installer>", "exec")
    assert "getmembers()" not in installer
    assert 'open(tmp,"xb")' in installer and 'os.replace(tmp,target)' in installer
    assert 'os.replace(temp/tool,tmp)' not in installer
    assert 'if sha(target)!=row["binary_sha256"]' in installer
    assert 'cache_config=hashlib.sha256(("cc\\n"+compiler+"\\n"+spec+"\\n").encode()).hexdigest()' in installer
    assert '"guest_verification":dict(r)' in installer and 'r["origin_proof_bytes"]=origin_text' in installer
    compiler="cc (Ubuntu 13.3.0) 13.3.0\nCopyright (C) toolchain"
    spec="./configure --prefix=/var/lib/cnb/install; make -j2"
    env=os.environ.copy(); env.pop("CC",None)
    shell_bytes=subprocess.check_output(["sh","-c",'printf \'%s\\n\' "${CC:-cc}" "$1" "$2"',"sh",compiler,spec],env=env)
    shell_digest=hashlib.sha256(shell_bytes).hexdigest()
    python_digest=hashlib.sha256(("cc\n"+compiler+"\n"+spec+"\n").encode()).hexdigest()
    assert shell_digest==python_digest
    tree=ast.parse(installer)
    regexes={ast.literal_eval(node.args[0]) for node in ast.walk(tree)
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)
        and node.func.attr in {"search","findall"} and node.args and isinstance(node.args[0],ast.Constant)}
    os_pattern=next(pattern for pattern in regexes if "VERSION_ID" in pattern)
    glibc_pattern=next(pattern for pattern in regexes if pattern.startswith("GLIBC_"))
    stable_pattern=next(pattern for pattern in regexes if "no\\.git" in pattern)
    assert re.search(os_pattern,'ID=ubuntu\nVERSION_ID="24.04"\n')
    assert re.findall(glibc_pattern,"Name: GLIBC_2.38") == ["2.38"]
    assert re.search(stable_pattern,"sockperf, version 3.10-no.git").group(1)=="3.10"
    assert re.search(stable_pattern,"iperf 3.17.1 (cJSON 1.7.15)").group(1)=="3.17.1"
    rstrip=next(node for node in ast.walk(tree) if isinstance(node,ast.Call)
        and isinstance(node.func,ast.Attribute) and node.func.attr=="rstrip")
    nul=ast.literal_eval(rstrip.args[0])
    assert nul==b"\0" and b"cubic\0".rstrip(nul)==b"cubic"
    assignments={target.id:node for node in ast.walk(tree) if isinstance(node,ast.Assign)
        for target in node.targets if isinstance(target,ast.Name)}
    ns={"hashlib":hashlib,"base64":base64,"b64":base64,"json":json,"compiler":compiler,"spec":spec,
        "tool":"sockperf","row":{"source_reference":"sockperf-pin","source_sha256":"a"*64,
            "upstream_commit":"b"*40,"patch_path":"patch.diff","patch_sha256":"c"*64,
            "c_compiler_version":compiler,"configure_flags":["./configure","--prefix=/var/lib/cnb/install"],
            "binary_version":"sockperf, version 3.10-no.git","binary_sha256":"d"*64,
            "capabilities":["server","under-load"]},"r":{"binary_version":"sockperf, version 3.10-no.git",
            "binary_sha256":"d"*64},"target":Path("/var/lib/cnb/install/bin/sockperf"),
        "cache_config":python_digest,"cfg":"CC=${CC:-cc}; compiler="+compiler+"; "+spec,
        "semantics":"sockperf-3.10-enabled-v1","caps":"server,under-load",
        "origin":{"schema":"CNB_PREBUILT_V1"}}
    exec(compile(ast.Module(body=[assignments["identity"]],type_ignores=[]),"<emitted-receipt-fragment>","exec"),ns)
    assert ns["identity"].startswith("CNB_BUILD_V1\n") and ns["identity"].endswith("\n")
    assert ns["identity"].splitlines()[0]=="CNB_BUILD_V1"
    exec(compile(ast.Module(body=[assignments["origin_text"]],type_ignores=[]),"<emitted-origin-fragment>","exec"),ns)
    assert ns["origin_text"].endswith("\n") and json.loads(ns["origin_text"])["schema"]=="CNB_PREBUILT_V1"
    assert "CNB_ACQ_V1" in installer and "readelf" in installer and "ldd" in installer
    with pytest.raises(ValueError):
        linux_installer_script("/var/lib/cnb/work/prebuilt.tar.gz", digest,
            "/tmp/install", "vm_a", caps, pins)
    with pytest.raises(ValueError):
        linux_installer_script("/var/lib/cnb/work/prebuilt.tar.gz", digest,
            "/var/lib/cnb/install", "vm_a", caps, pins, test_filesystem_root=tmp_path)


@pytest.mark.parametrize("shape", ["traversal", "symlink", "duplicate", "oversized", "hash"])
def test_archive_structure_duplicate_expansion_and_digest_tampering_fail(tmp_path, shape):
    path = tmp_path / "bad.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        names = ["../manifest.json", "manifest.json", "manifest.json"] if shape == "traversal" else ["manifest.json"]
        if shape == "duplicate": names = ["manifest.json", "manifest.json"]
        if shape == "symlink":
            info = tarfile.TarInfo("manifest.json"); info.type = tarfile.SYMTYPE; info.linkname = "bin/sockperf"
            tar.addfile(info)
        else:
            for name in names:
                data = b"x" * (1024 * 1024 + 1 if shape == "oversized" else 2)
                info = tarfile.TarInfo(name); info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    digest = "0" * 64 if shape == "hash" else hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises((ValueError, tarfile.TarError, OSError)):
        validate_bundle(path, digest, [source("sockperf"), source("iperf3")])


@pytest.mark.parametrize("installer_success", [True, False])
def test_actual_optional_bootstrap_request_has_typed_success_or_failure_without_source_fallback(tmp_path, installer_success):
    import base64
    from datetime import datetime, timedelta, timezone
    from cloud_network_benchmark.access import RemoteDeadline
    from cloud_network_benchmark.bootstrap import _prepare_prebuilt_role, _required_capabilities
    from tests.fixtures.f02r.support import connection, result

    sources, path, declared = make_bundle(tmp_path)
    config = BootstrapConfig(sources=sources, remote_root="/var/lib/cnb", workspace="/var/lib/cnb/work",
        results_workspace="/var/lib/cnb/results", install_prefix="/var/lib/cnb/install",
        prebuilt_bundle={"path": str(path), "sha256": declared["sha256"]})
    manifest = declared["manifest"]["tools"]
    output = {}
    for tool in ("sockperf", "iperf3"):
        row = manifest[tool]
        binary = row["binary_sha256"]
        version = row["binary_version"].strip()
        compiler = row["c_compiler_version"]
        spec = " ".join(row["configure_flags"]) + "; make -j2"
        cfg = f"CC=${{CC:-cc}}; compiler={compiler}; {spec}"
        b64 = lambda value: base64.b64encode(value.encode()).decode()
        identity = "\n".join(("CNB_BUILD_V1", f"tool={tool}", f"source_reference={row['source_reference']}",
            f"source_sha256={row['source_sha256']}", f"upstream_commit={row['upstream_commit']}",
            f"patch_path={row['patch_path']}", f"patch_sha256={row['patch_sha256']}",
            f"compiler_version_b64={b64(compiler)}", f"build_configuration_b64={b64(cfg)}",
            f"binary_path=/var/lib/cnb/install/bin/{tool}", f"binary_version_b64={b64(version)}",
            f"binary_sha256={binary}", "observation_capability=CNB_ACQ_V1",
            "observation_semantics=" + ("sockperf-3.10-enabled-v1" if tool == "sockperf" else "iperf-3.17.1-stats-v1"),
            "capabilities=" + ",".join(_required_capabilities(tool)))) + "\n"
        origin = {"schema":"CNB_PREBUILT_V1", "origin":"controller-prebuilt", "tool":tool,
            "role":"vm_a", "binary_sha256":binary, "bundle_sha256":declared["sha256"],
            "bundle_artifact_id":"f02r-controller-prebuilt-bundle", "controller_receipt":row,
            "guest_verification":{"binary_sha256":binary,"binary_version":version,
                "binary_path":f"/var/lib/cnb/install/bin/{tool}"}}
        captext = "Usage: tool " + " ".join(_required_capabilities(tool))
        output[tool] = {"binary_version":version, "binary_sha256":binary,
            "binary_path":f"/var/lib/cnb/install/bin/{tool}", "identity_bytes":identity,
            "origin_proof_bytes":json.dumps(origin)+"\n", "help_outputs":{"main":captext,"modes":{
                "server":"Usage: sockperf server -i -p", "under-load":"Usage: sockperf under-load -i -p --full-rtt --no-rdtsc --full-log"} if tool == "sockperf" else {}}}

    class Runner:
        def __init__(self): self.requests=[]
        def run(self, request):
            self.requests.append(request)
            if request.action_id.endswith("bundle-identity-preflight"): return result(request.action_id, "missing")
            if request.action_id.endswith("prebuilt-verify-install"):
                role = "vm_b" if "vm_b" in request.action_id else "vm_a"
                payload=json.loads(json.dumps(output))
                for row in payload.values():
                    proof=json.loads(row["origin_proof_bytes"]); proof["role"]=role
                    row["origin_proof_bytes"]=json.dumps(proof)+"\n"
                return result(request.action_id, json.dumps(payload), success=installer_success, stderr="installer failed")
            return result(request.action_id)

    runner=Runner(); c=connection(tmp_path); now=datetime.now(timezone.utc)
    c=c.model_copy(update={"timeouts":c.timeouts.model_copy(update={"command_seconds":9,"transfer_seconds":7})})
    deadline=RemoteDeadline(readiness_deadline_monotonic=200,overall_deadline_monotonic=300,
        cleanup_reserve_seconds=10,cap_started_at=now,cap_expires_at=now+timedelta(minutes=5))
    builds=[]; actions=[]; listeners=[]
    failure=_prepare_prebuilt_role(c,c.vm_a,"vm_a",config,runner,deadline,lambda:100,actions,builds,listeners)
    if installer_success:
        assert failure is None and len(builds)==2 and all(b.build_origin=="controller-prebuilt" for b in builds)
        builds_b=[]
        failure_b=_prepare_prebuilt_role(c,c.vm_b,"vm_b",config,runner,deadline,lambda:100,[],builds_b,[])
        assert failure_b is None and len(builds_b)==2
        from cloud_network_benchmark.bootstrap import materialize_build_identity_artifacts
        from cloud_network_benchmark.contracts.artifacts import ArtifactLayout
        from cloud_network_benchmark.lifecycle_bundle import lifecycle_declarations
        all_builds=builds+builds_b
        # Model the real guest namespace: it receives controller-prebuilt
        # provenance, but cannot resolve the controller's absolute bundle path.
        guest_builds = [build.model_copy(update={"prebuilt_bundle_path": str(
            tmp_path / "guest-filesystem" / "controller-tools.tar.gz")}) for build in builds]
        guest_root = tmp_path / "guest-results"
        guest_records = materialize_build_identity_artifacts(guest_root, c.deployment_id,
            guest_builds, include_controller_bundle=False)
        assert {record.kind for record in guest_records} == {"build-identity", "build-origin"}
        assert all((guest_root / record.path).is_file() for record in guest_records)
        assert not (guest_root / "metadata/build/controller-prebuilt-tools.tar.gz").exists()

        result_root=tmp_path/"results"
        records=materialize_build_identity_artifacts(result_root,c.deployment_id,all_builds)
        record_by_id={record.artifact_id:record for record in records}
        for build in all_builds:
            assert (result_root/record_by_id[build.artifact_id].path).read_bytes()==build.identity_bytes.encode()
            assert (result_root/record_by_id[build.origin_artifact_id].path).read_bytes()==build.origin_proof_bytes.encode()
        assert {r.kind for r in records}=={"build-identity","build-origin","controller-prebuilt-bundle"}
        bundle_record = next(record for record in records if record.kind == "controller-prebuilt-bundle")
        assert (result_root / bundle_record.path).read_bytes() == path.read_bytes()

        missing = builds[0].model_copy(update={"prebuilt_bundle_path": str(tmp_path / "missing-bundle")})
        with pytest.raises(ValueError, match="prebuilt bundle changed"):
            materialize_build_identity_artifacts(tmp_path / "missing-output", c.deployment_id, [missing])
        bundle_bytes = path.read_bytes()
        path.write_bytes(bundle_bytes + b"tampered")
        with pytest.raises(ValueError, match="prebuilt bundle changed"):
            materialize_build_identity_artifacts(tmp_path / "tampered-output", c.deployment_id, [builds[0]])
        path.write_bytes(bundle_bytes)
        symlink_path = tmp_path / "bundle-link.tar.gz"
        symlink_path.symlink_to(path)
        linked = builds[0].model_copy(update={"prebuilt_bundle_path": str(symlink_path)})
        with pytest.raises(ValueError, match="prebuilt bundle changed"):
            materialize_build_identity_artifacts(tmp_path / "symlink-output", c.deployment_id, [linked])

        declarations=lifecycle_declarations(ArtifactLayout(),mode="acceptance",measurement_units=[],build_observations=all_builds)
        declared={d.artifact_id for d in declarations}
        assert {f"f02r-{role}-{tool}-origin" for role in ("vm_a","vm_b") for tool in ("sockperf","iperf3")} <= declared
        assert "f02r-controller-prebuilt-bundle" in declared
        from cloud_network_benchmark.bootstrap import BuildObservation
        for field,value in (("bundle_sha256","0"*64),("role","vm_b"),("binary_sha256","1"*64)):
            bad=builds[0].model_dump()
            proof=json.loads(bad["origin_proof_bytes"]); proof[field]=value
            bad["origin_proof_bytes"]=json.dumps(proof)
            with pytest.raises(ValueError,match="origin proof"):
                BuildObservation(**bad)
    else:
        assert failure is not None and failure.failure.category=="prebuilt_bundle_verification_failed" and not builds
    installer=next(r for r in runner.requests if r.action_id.endswith("prebuilt-verify-install"))
    assert "python3 -c" in " ".join(installer.argv)
    transfer=next(r for r in runner.requests if r.action_id.endswith("prebuilt-transfer"))
    assert transfer.action_type=="scp" and transfer.timeout_seconds==7
    assert installer.timeout_seconds==9
    assert not any("sockperf-0" in r.action_id or "iperf3-0" in r.action_id for r in runner.requests)


def test_changed_bundle_fails_immediate_role_upload_recheck(tmp_path):
    from datetime import datetime, timedelta, timezone
    from cloud_network_benchmark.access import RemoteDeadline
    from cloud_network_benchmark.bootstrap import _prepare_prebuilt_role
    from tests.fixtures.f02r.support import connection, result
    sources,path,declared=make_bundle(tmp_path)
    config=BootstrapConfig(sources=sources,remote_root="/var/lib/cnb",workspace="/var/lib/cnb/work",
        results_workspace="/var/lib/cnb/results",install_prefix="/var/lib/cnb/install",
        prebuilt_bundle={"path":str(path),"sha256":declared["sha256"]})
    path.write_bytes(path.read_bytes()+b"changed")
    class Runner:
        def __init__(self): self.requests=[]
        def run(self,request):
            self.requests.append(request)
            return result(request.action_id,"missing" if request.action_id.endswith("bundle-identity-preflight") else "ok")
    runner=Runner(); c=connection(tmp_path); now=datetime.now(timezone.utc)
    deadline=RemoteDeadline(readiness_deadline_monotonic=200,overall_deadline_monotonic=300,
        cleanup_reserve_seconds=10,cap_started_at=now,cap_expires_at=now+timedelta(minutes=5))
    failure=_prepare_prebuilt_role(c,c.vm_a,"vm_a",config,runner,deadline,lambda:100,[],[],[])
    assert failure is not None and failure.failure.category=="prebuilt_bundle_local_changed"
    assert not any(r.action_type=="scp" for r in runner.requests)


@pytest.mark.parametrize(
    "update_code,install_code,expected_stage,expected_exit,expected_calls",
    [(17, 0, "update", 17, ["update"]), (0, 23, "install", 23, ["update", "install"])],
)
def test_prebuilt_package_script_bounds_apt_and_preserves_stage_failure(
    update_code, install_code, expected_stage, expected_exit, expected_calls
):
    import contextlib
    import io
    import subprocess as real_subprocess
    from types import SimpleNamespace
    from cloud_network_benchmark.bootstrap import _prebuilt_package_setup_script

    script = _prebuilt_package_setup_script(["build-essential", "python3-venv"], 90)
    source_text = script.split("\ntry:\n    sys.exit(main())", 1)[0]
    calls = []
    def fake_run(argv, **kwargs):
        operation = "update" if "update" in argv else "install"
        calls.append((operation, argv, kwargs))
        code = update_code if operation == "update" else install_code
        return SimpleNamespace(returncode=code, stdout=(
            "synthetic apt failure https://name:secret@example.ubuntu.com/x\n" if code else "ok\n"))

    ns = {}
    exec(compile(source_text, "<prebuilt-package-script>", "exec"), ns)
    ns["subprocess"] = SimpleNamespace(run=fake_run, TimeoutExpired=real_subprocess.TimeoutExpired,
                                       PIPE=real_subprocess.PIPE, STDOUT=real_subprocess.STDOUT)
    ns["diagnostics"] = lambda *_args: (_ for _ in ()).throw(RuntimeError("diagnostics unavailable"))
    rendered = io.StringIO()
    with contextlib.redirect_stdout(rendered):
        result_code = ns["main"]()

    assert result_code == expected_exit
    assert [call[0] for call in calls] == expected_calls
    update_argv = next(argv for operation, argv, _ in calls if operation == "update")
    assert update_argv[0] == "apt-get" and update_argv[-1] == "update"
    assert "Acquire::Retries=0" in update_argv
    assert "Acquire::http::Timeout=8" in update_argv
    assert "Acquire::https::Timeout=8" in update_argv
    assert "APT::Update::Error-Mode=any" in update_argv
    assert [call[2]["timeout"] for call in calls] == pytest.approx([87.0] * len(calls), abs=0.01)
    if install_code:
        install_argv = next(argv for operation, argv, _ in calls if operation == "install")
        assert install_argv[-3:] == ["-y", "build-essential", "python3-venv"]
    assert f"CNB_APT_RESULT=stage:{expected_stage} exit={expected_exit}" in rendered.getvalue()
    assert "CNB_DIAGNOSTICS_ERROR=RuntimeError" in rendered.getvalue()
    assert "name:secret@" not in rendered.getvalue()


def test_prebuilt_package_script_timeout_is_reported_and_stops_before_install():
    import contextlib
    import io
    import subprocess as real_subprocess
    from types import SimpleNamespace
    from cloud_network_benchmark.bootstrap import _prebuilt_package_setup_script

    script = _prebuilt_package_setup_script(["build-essential"], 90)
    source_text = script.split("\ntry:\n    sys.exit(main())", 1)[0]
    calls = []
    def timeout_run(argv, **kwargs):
        calls.append(argv)
        raise real_subprocess.TimeoutExpired(argv, kwargs["timeout"], output=b"partial apt output")
    ns = {}
    exec(compile(source_text, "<prebuilt-package-script>", "exec"), ns)
    ns["subprocess"] = SimpleNamespace(run=timeout_run, TimeoutExpired=real_subprocess.TimeoutExpired,
                                       PIPE=real_subprocess.PIPE, STDOUT=real_subprocess.STDOUT)
    ns["diagnostics"] = lambda *_args: None
    rendered = io.StringIO()
    with contextlib.redirect_stdout(rendered):
        assert ns["main"]() == 124
    assert len(calls) == 1 and calls[0][-1] == "update"
    assert "stage:update exit=124" in rendered.getvalue()


def test_prebuilt_package_script_interruption_keeps_interrupted_status_without_install():
    import contextlib
    import io
    import subprocess as real_subprocess
    from types import SimpleNamespace
    from cloud_network_benchmark.bootstrap import _prebuilt_package_setup_script

    script = _prebuilt_package_setup_script(["build-essential"], 90)
    calls = []
    def interrupted_run(argv, **kwargs):
        calls.append(argv)
        raise KeyboardInterrupt()
    ns = {}
    source_text = script.split("\ntry:\n    sys.exit(main())", 1)[0]
    exec(compile(source_text, "<prebuilt-package-script>", "exec"), ns)
    ns["subprocess"] = SimpleNamespace(run=interrupted_run, TimeoutExpired=real_subprocess.TimeoutExpired,
                                       PIPE=real_subprocess.PIPE, STDOUT=real_subprocess.STDOUT)
    ns["diagnostics"] = lambda *_args: (_ for _ in ()).throw(RuntimeError("diagnostics unavailable"))
    rendered = io.StringIO()
    with contextlib.redirect_stdout(rendered):
        assert ns["main"]() == 130
    assert len(calls) == 1 and calls[0][-1] == "update"
    assert "CNB_APT_RESULT=stage:update exit=130" in rendered.getvalue()
    assert "CNB_DIAGNOSTICS_ERROR=RuntimeError" in rendered.getvalue()


def test_prebuilt_package_output_write_failure_preserves_apt_failure():
    import contextlib
    import io
    import subprocess as real_subprocess
    from types import SimpleNamespace
    from cloud_network_benchmark.bootstrap import _prebuilt_package_setup_script

    script = _prebuilt_package_setup_script(["build-essential"], 90)
    source_text = script.split("\ntry:\n    sys.exit(main())", 1)[0]
    ns = {}
    exec(compile(source_text, "<prebuilt-package-script>", "exec"), ns)
    ns["subprocess"] = SimpleNamespace(run=lambda *_args, **_kwargs: SimpleNamespace(
        returncode=17, stdout="apt update failed"), TimeoutExpired=real_subprocess.TimeoutExpired,
        PIPE=real_subprocess.PIPE, STDOUT=real_subprocess.STDOUT)
    ns["emit_output"] = lambda _value: (_ for _ in ()).throw(OSError("report write failed"))
    ns["diagnostics"] = lambda *_args: None
    with contextlib.redirect_stdout(io.StringIO()):
        assert ns["main"]() == 17
    diagnostic = ns["DIAGNOSTIC_PROGRAM"]
    assert "/etc/apt/sources.list.d/ubuntu.sources" in diagnostic
    assert 'getattr(os, "O_NOFOLLOW", 0)' in diagnostic and "read(65536)" in diagnostic
    assert "getaddrinfo(\"security.ubuntu.com\"" in diagnostic
    assert "socket.socket(first[0], first[1], first[2])" in diagnostic


def test_prebuilt_diagnostic_program_reads_only_bounded_official_sources_and_handles_ipv6(tmp_path):
    import contextlib
    import io
    import json as json_module
    import pathlib as pathlib_module
    import re as re_module
    from types import SimpleNamespace
    from cloud_network_benchmark.bootstrap import _prebuilt_package_setup_script

    script = _prebuilt_package_setup_script(["build-essential"], 90)
    ns = {}
    source_text = script.split("\ntry:\n    sys.exit(main())", 1)[0]
    exec(compile(source_text, "<prebuilt-package-script>", "exec"), ns)
    contents = {
        "/etc/apt/sources.list": (
            "deb https://name:secret@mirror.ubuntu.com/ubuntu noble main\n"
            "deb https://archive.ubuntu.com/ubuntu noble-updates main\n"
            "deb https://packages.example.net/ubuntu noble main\n"),
        "/etc/apt/sources.list.d/ubuntu.sources": (
            "Types: deb\nURIs: http://security.ubuntu.com/ubuntu\n"
            "Suites: noble-security\nComponents: main\n"),
    }
    file_rows = {}
    opened = []
    def fake_open(path, flags):
        opened.append((path, flags))
        fd = len(opened)
        file_rows[fd] = contents[path]
        return fd
    fake_os = SimpleNamespace(open=fake_open, fdopen=lambda fd, *_args, **_kwargs: io.StringIO(file_rows[fd]),
                              O_RDONLY=0, O_NOFOLLOW=0x20000)
    reads = []
    class BoundedText(io.StringIO):
        def read(self, size=-1):
            reads.append(size)
            return super().read(size)
    fake_os.fdopen = lambda fd, *_args, **_kwargs: BoundedText(file_rows[fd])
    connections = []
    class FakeSocket:
        def __init__(self, family, socktype, proto):
            self.created = (family, socktype, proto)
        def settimeout(self, value):
            assert value == 0.7
        def connect(self, address):
            connections.append((self.created, address))
        def close(self):
            pass
    def getaddrinfo(host, port, type):
        assert (host, port, type) == ("security.ubuntu.com", 80, 1)
        return [(10, 1, 6, "", ("2001:db8::1", 80, 0, 0)),
                (2, 1, 6, "", ("192.0.2.1", 80))]
    fake_socket = SimpleNamespace(SOCK_STREAM=1, socket=FakeSocket, getaddrinfo=getaddrinfo)
    program = ns["DIAGNOSTIC_PROGRAM"].replace("import json, os, pathlib, re, socket", "")
    probe_ns = {"json": json_module, "os": fake_os, "pathlib": pathlib_module,
                "re": re_module, "socket": fake_socket}
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exec(compile(program, "<prebuilt-diagnostic>", "exec"), probe_ns)
    rendered = output.getvalue()
    assert len(opened) == 2
    assert [path for path, _ in opened] == list(contents)
    assert all(flags & fake_os.O_NOFOLLOW for _, flags in opened)
    assert reads == [65536, 65536]
    assert "archive.ubuntu.com=noble-updates" in rendered
    assert "security.ubuntu.com=noble-security" in rendered
    assert "mirror.ubuntu.com" not in rendered and "example.net" not in rendered
    assert "secret" not in rendered
    assert len(connections) == 1
    assert connections[0][0] == (10, 1, 6)
    assert connections[0][1] == ("2001:db8::1", 80, 0, 0)

    fake_socket.getaddrinfo = lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("secret DNS detail"))
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exec(compile(program, "<prebuilt-diagnostic>", "exec"), probe_ns)
    assert "ValueError" in output.getvalue()
    assert "secret DNS detail" not in output.getvalue()


def test_prebuilt_diagnostics_obey_remaining_command_budget_and_bound_output():
    import contextlib
    import io
    import subprocess as real_subprocess
    from types import SimpleNamespace
    from cloud_network_benchmark.bootstrap import _prebuilt_package_setup_script

    script = _prebuilt_package_setup_script(["build-essential"], 90)
    source_text = script.split("\ntry:\n    sys.exit(main())", 1)[0]
    calls = []
    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="Z" * 2000)
    ns = {}
    exec(compile(source_text, "<prebuilt-package-script>", "exec"), ns)
    ns["subprocess"] = SimpleNamespace(run=fake_run, TimeoutExpired=real_subprocess.TimeoutExpired,
                                       PIPE=real_subprocess.PIPE, STDOUT=real_subprocess.STDOUT)
    output = io.StringIO()
    ns["OUTER_TIMEOUT"] = 2.5
    with contextlib.redirect_stdout(output):
        assert ns["main"]() == 124
    assert len(calls) == 1
    assert calls[0][0][0] == ns["sys"].executable and calls[0][0][1:3] == ["-u", "-c"]
    assert calls[0][1]["timeout"] <= 2.5
    assert "Z" * 1601 not in output.getvalue()
    assert output.getvalue().count("Z") == 1600

    calls.clear()
    output = io.StringIO()
    ns["OUTER_TIMEOUT"] = 0
    with contextlib.redirect_stdout(output):
        assert ns["main"]() == 124
    assert not calls
    assert "CNB_DIAGNOSTICS_SKIPPED=command_budget_exhausted" in output.getvalue()


def test_prebuilt_bootstrap_timeout_keeps_both_role_evidence_and_cancels_followup(tmp_path):
    import threading
    from datetime import datetime, timedelta, timezone
    from cloud_network_benchmark.access import RemoteDeadline
    from cloud_network_benchmark.bootstrap import run_bootstrap
    from cloud_network_benchmark.contracts.execution import CommandResult
    from tests.fixtures.f02r.support import connection, result

    sources, bundle, declared = make_bundle(tmp_path)
    config = BootstrapConfig(sources=sources, remote_root="/var/lib/cnb", workspace="/var/lib/cnb/work",
        results_workspace="/var/lib/cnb/results", install_prefix="/var/lib/cnb/install",
        prebuilt_bundle={"path": str(bundle), "sha256": declared["sha256"]})
    c = connection(tmp_path)
    c = c.model_copy(update={"timeouts": c.timeouts.model_copy(update={"command_seconds": 90, "transfer_seconds": 45})})
    now = datetime.now(timezone.utc)
    deadline = RemoteDeadline(readiness_deadline_monotonic=200, overall_deadline_monotonic=1200,
        cleanup_reserve_seconds=10, cap_started_at=now, cap_expires_at=now + timedelta(seconds=1200))

    class Runner:
        supports_concurrent_commands = True
        def __init__(self):
            self.requests = []
            self.packages = threading.Barrier(2, timeout=5)
        def run(self, request):
            self.requests.append(request)
            if request.action_id.endswith("cloud-init"):
                return result(request.action_id, "status: done")
            if "listener-inspect-" in request.action_id:
                return result(request.action_id, "LoadState=not-found")
            if request.action_id.endswith("prebuilt-packages"):
                self.packages.wait()
                return CommandResult(action_id=request.action_id, outcome="timed_out", started_at=now,
                    finished_at=now + timedelta(seconds=90), duration_seconds=90, exit_code=None,
                    stdout="Hit: regional archive\\nIgn: security.ubuntu.com noble-security\\n", stderr="")
            raise AssertionError("unexpected follow-up after package timeout: " + request.action_id)

    runner = Runner()
    outcome = run_bootstrap(c, config, runner, deadline=deadline, clock=lambda: 100)
    package_ids = {request.action_id for request in runner.requests if request.action_id.endswith("prebuilt-packages")}
    assert package_ids == {"f02r-vm_a-prebuilt-packages", "f02r-vm_b-prebuilt-packages"}
    assert not outcome.succeeded and outcome.failure.category == "prebuilt_package_setup_failed"
    evidence_ids = {item.action_id for item in outcome.actions}
    assert package_ids <= evidence_ids
    assert not any(item.action_type == "scp" for item in outcome.actions)
    assert not outcome.builds and not outcome.listeners
    assert all("apt-get update" not in " ".join(request.argv) for request in runner.requests)
    package_request = next(request for request in runner.requests if request.action_id.endswith("prebuilt-packages"))
    assert package_request.timeout_seconds == 90
    command = package_request.argv[-1]
    assert command.startswith("sudo python3 -u -c ")


def test_local_bundle_revalidation_consuming_readiness_deadline_prevents_upload(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from cloud_network_benchmark.access import RemoteDeadline
    from cloud_network_benchmark.bootstrap import _prepare_prebuilt_role
    from tests.fixtures.f02r.support import connection, result
    sources,path,declared=make_bundle(tmp_path)
    config=BootstrapConfig(sources=sources,remote_root="/var/lib/cnb",workspace="/var/lib/cnb/work",
        results_workspace="/var/lib/cnb/results",install_prefix="/var/lib/cnb/install",
        prebuilt_bundle={"path":str(path),"sha256":declared["sha256"]})
    now=datetime.now(timezone.utc)
    deadline=RemoteDeadline(readiness_deadline_monotonic=200,overall_deadline_monotonic=300,
        cleanup_reserve_seconds=10,cap_started_at=now,cap_expires_at=now+timedelta(minutes=5))
    monotonic={"now":100}
    def consume_local_time(*_args,**_kwargs):
        monotonic["now"]=201
        return declared["manifest"]
    from cloud_network_benchmark import build_bundle as bundle_module
    monkeypatch.setattr(bundle_module,"validate_bundle",consume_local_time)
    class Runner:
        def __init__(self): self.requests=[]
        def run(self,request):
            self.requests.append(request)
            return result(request.action_id,"missing" if request.action_id.endswith("bundle-identity-preflight") else "ok")
    runner=Runner(); c=connection(tmp_path)
    failure=_prepare_prebuilt_role(c,c.vm_a,"vm_a",config,runner,deadline,
        lambda:monotonic["now"],[],[],[])
    assert failure is not None and failure.failure.category=="readiness_deadline_exhausted"
    assert not any(r.action_type=="scp" for r in runner.requests)


@pytest.mark.parametrize("role", ["vm_a", "vm_b"])
@pytest.mark.parametrize("tool", ["sockperf", "iperf3"])
def test_nested_partial_origin_signal_fails_closed_for_each_tool_and_role(repository_root, role, tool):
    from tests.unit.test_validation import make_input
    from cloud_network_benchmark.contracts.measurements import VmMetadata
    from cloud_network_benchmark.contracts.common import StructuredEvidence
    from cloud_network_benchmark.validation import _prebuilt_origin_errors
    value = make_input(repository_root)
    metadata = VmMetadata.model_construct(vm_role=role,
        source_builds=StructuredEvidence(value={tool:{"supplied":{"build_origin":"controller-prebuilt",
            "origin_artifact_id":f"f02r-{role}-{tool}-origin"}}}))
    candidate = value.model_copy(update={"vm_metadata_records":[metadata]})
    errors = _prebuilt_origin_errors(candidate)
    assert any("exactly one declared artifact for each tool and VM role" in e for e in errors)
    assert any(f"{role}/{tool}" in e for e in errors)
