from pathlib import Path
import hashlib
import re
import shlex
import subprocess
import sys
import pytest
sys.path.insert(0, str(Path(__file__).parents[1]))
from fixtures.f02r.support import BootstrapRunner, bootstrap_config, connection, deadline, result
from cloud_network_benchmark.bootstrap import SourceBuild, run_bootstrap, _required_capabilities
from cloud_network_benchmark.bootstrap import _build_config_prelude, _capabilities_observed, _compiler_identity_observed, _identity_preflight_script, _recipe


def test_preparation_observes_both_tools_on_both_vms_and_private_servers(tmp_path):
    c=connection(tmp_path); runner=BootstrapRunner()
    result=run_bootstrap(c,bootstrap_config(),runner,deadline=deadline(),clock=lambda:100)
    assert result.succeeded
    assert len(result.builds)==4 and {x.tool for x in result.builds}=={"sockperf","iperf3"}
    assert len(result.listeners)==2
    assert {(x.protocol,x.address,x.port) for x in result.listeners}=={("udp","10.0.0.2",11111),("tcp","10.0.0.2",5201)}
    assert all("StrictHostKeyChecking=yes" in request.argv for request in runner.requests)
    assert all(x.source_sha256 in {"a"*64,"b"*64} and x.binary_sha256=="c"*64 for x in result.builds)
    sockperf=next(request for request in runner.requests if request.action_id=="f02r-vm_b-listener-sockperf")
    iperf3=next(request for request in runner.requests if request.action_id=="f02r-vm_b-listener-iperf3")
    assert "/var/lib/cnb/install/bin/sockperf" in sockperf.argv[-1] and "-i 10.0.0.2 -p 11111" in sockperf.argv[-1]
    assert "/var/lib/cnb/install/bin/iperf3" in iperf3.argv[-1] and "-B 10.0.0.2 -p 5201 --json-stream --forceflush" in iperf3.argv[-1]
    acl_rules=[request.argv[-1] for request in runner.requests if "acl" in request.action_id and "create" in request.action_id]
    assert any("10.0.0.1/32" in rule and "10.0.0.2" in rule and "11111" in rule and "accept" in rule for rule in acl_rules)
    assert any("10.0.0.2" in rule and "11111" in rule and "drop" in rule for rule in acl_rules)
    assert any("10.0.0.1/32" in rule and "10.0.0.2" in rule and "5201" in rule and "accept" in rule for rule in acl_rules)
    assert any("10.0.0.2" in rule and "5201" in rule and "drop" in rule for rule in acl_rules)


def test_source_digest_mismatch_stops_before_extract_or_other_vm(tmp_path):
    c=connection(tmp_path)
    class BadDigest(BootstrapRunner):
        def run(self, request):
            r=super().run(request)
            if request.action_id.endswith("sockperf-2"):
                from fixtures.f02r.support import result
                return result(request.action_id,"d"*64+" archive")
            return r
    runner=BadDigest(); result=run_bootstrap(c,bootstrap_config(),runner,deadline=deadline(),clock=lambda:100)
    assert not result.succeeded and result.failure.category=="source_digest_mismatch"
    assert not any("vm_b" in x.action_id for x in result.actions)


def test_build_failure_stops_before_binary_observation_or_other_vm(tmp_path):
    c=connection(tmp_path)
    class FailedBuild(BootstrapRunner):
        def run(self,request):
            if request.action_id=="f02r-vm_a-sockperf-4":
                from fixtures.f02r.support import result
                return result(request.action_id,success=False,stderr="synthetic compiler failure")
            return super().run(request)
    runner=FailedBuild(); outcome=run_bootstrap(c,bootstrap_config(),runner,deadline=deadline(),clock=lambda:100)
    assert not outcome.succeeded and outcome.failure.category=="source_build_failed"
    assert not any(request.action_id.endswith("sockperf-5") or "vm_b" in request.action_id for request in runner.requests)


def test_explicit_https_and_digest_are_required():
    with pytest.raises(Exception): SourceBuild(tool="sockperf",source_url="http://bad",source_reference="src",archive_name="a.tar",source_sha256="x")


def test_matching_existing_listeners_are_reused_and_conflicts_fail_closed(tmp_path):
    c=connection(tmp_path); runner=BootstrapRunner(existing_listeners=True,existing_acl=True)
    outcome=run_bootstrap(c,bootstrap_config(),runner,deadline=deadline(),clock=lambda:100)
    assert outcome.succeeded
    assert not any("listener-log-directory" in request.action_id or "listener-sockperf" in request.action_id or "listener-iperf3" in request.action_id for request in runner.requests)
    conflict=BootstrapRunner(existing_listeners=True,existing_acl=True,mismatched_listener=True)
    failed=run_bootstrap(c,bootstrap_config(),conflict,deadline=deadline(),clock=lambda:100)
    assert not failed.succeeded and failed.failure.category=="listener_state_mismatch"
    assert not any("listener-log-directory" in request.action_id or request.action_id.endswith("listener-sockperf") for request in conflict.requests)


def test_missing_tool_capability_stops_before_listener_preparation(tmp_path):
    c=connection(tmp_path)
    class MissingHelp(BootstrapRunner):
        def run(self,request):
            if request.action_id.endswith("sockperf-8"):
                from fixtures.f02r.support import result
                return result(request.action_id,"server under-load")
            return super().run(request)
    runner=MissingHelp(); outcome=run_bootstrap(c,bootstrap_config(),runner,deadline=deadline(),clock=lambda:100)
    assert not outcome.succeeded and outcome.failure.category=="capability_missing"
    assert not any("listener" in request.action_id for request in runner.requests)


def test_nested_build_script_is_syntax_safe_and_identity_reuse_is_verified(tmp_path):
    c=bootstrap_config().model_copy(update={"remote_root":str(tmp_path),"workspace":str(tmp_path/"work"),"install_prefix":str(tmp_path/"install")})
    source=next(item for item in c.sources if item.tool=="iperf3")
    binary=Path(c.install_prefix)/"bin/iperf3"; binary.parent.mkdir(parents=True); binary.write_bytes(b"compiled binary")
    digest=hashlib.sha256(binary.read_bytes()).hexdigest(); marker=Path(c.install_prefix)/".f02r-iperf3.identity"
    config_hash=subprocess.run(["sh", "-c", _build_config_prelude(c) + "printf '%s' \"$config_sha\""],text=True,capture_output=True,check=True).stdout
    marker.write_text(f"{source.source_reference} {source.source_sha256} legacy {config_hash} {digest}\n")
    probe=subprocess.run(["sh","-c",_identity_preflight_script(source,c)],text=True,capture_output=True)
    assert probe.returncode==0 and probe.stdout.strip()=="matching"
    recipe=_recipe(source,c)
    checked=subprocess.run(["sh","-n","-c",recipe[4][-1]],text=True,capture_output=True)
    assert checked.returncode==0
    before=binary.read_bytes(); reused=subprocess.run(["sh","-c",recipe[4][-1]],text=True,capture_output=True)
    assert reused.returncode==0 and binary.read_bytes()==before
    marker.write_text(f"other-source {source.source_sha256} legacy {config_hash} {digest}\n")
    conflict=subprocess.run(["sh","-c",_identity_preflight_script(source,c)],text=True,capture_output=True)
    assert conflict.returncode != 0


def test_shell_unsafe_build_values_are_rejected_and_help_is_token_based():
    c=bootstrap_config()
    with pytest.raises(Exception): type(c).model_validate({**c.model_dump(),"workspace":"/var/lib/cnb/work;touch"})
    with pytest.raises(Exception): SourceBuild(tool="sockperf",source_url="https://example.invalid/x",source_reference="src;touch",archive_name="x.tar.gz",source_sha256="a"*64)
    assert not _capabilities_observed("Usage: tool [options]\nunder-loadish -iSomething --full-rtt-extra", "sockperf")


def test_pinned_acquisition_patch_identity_is_mandatory_and_part_of_cache_key(tmp_path):
    from pathlib import Path
    from cloud_network_benchmark.bootstrap import SourceBuild
    sockperf = Path(__file__).parents[2] / "src/cloud_network_benchmark/tool_patches/sockperf-3.10-acquisition.patch"
    import hashlib
    digest = hashlib.sha256(sockperf.read_bytes()).hexdigest()
    pinned = SourceBuild(tool="sockperf", source_url="https://github.com/Mellanox/sockperf/archive/5ebd327da983225321818c0355db922515e026bd.tar.gz",
        source_reference="sockperf-5ebd327da983225321818c0355db922515e026bd", upstream_commit="5ebd327da983225321818c0355db922515e026bd", archive_name="sockperf.tar.gz",
        source_sha256="a35a1daf310551b9f675af4fb7d95a6a10389fda4c7605d70a21554be0773f8e",
        observation_patch_path="tool_patches/sockperf-3.10-acquisition.patch", observation_patch_sha256=digest)
    with pytest.raises(Exception, match="exact packaged observation patch"):
        SourceBuild(**{**pinned.model_dump(), "observation_patch_sha256": "f" * 64})
    config = bootstrap_config().model_copy(update={"remote_root": str(tmp_path), "workspace": str(tmp_path / "work"),
                                                    "install_prefix": str(tmp_path / "install")})
    script = _recipe(pinned, config)[4][-1]
    assert "patch --batch --fuzz=0 -p1" in script
    assert digest in script and "old_patch" in script
    assert subprocess.run(["sh", "-n", "-c", script], capture_output=True).returncode == 0
    assert "config_sha" in script and "CNB_ACQ_V1" in " ".join(" ".join(row) for row in _recipe(pinned, config))
    iperf_patch = Path(__file__).parents[2] / "src/cloud_network_benchmark/tool_patches/iperf-3.17.1-acquisition.patch"
    iperf_digest = hashlib.sha256(iperf_patch.read_bytes()).hexdigest()
    iperf = SourceBuild(tool="iperf3", source_url="https://github.com/esnet/iperf/archive/2acfcfe94e928e74542c9f107e02aa6dd4748a79.tar.gz",
        source_reference="iperf-2acfcfe94e928e74542c9f107e02aa6dd4748a79", upstream_commit="2acfcfe94e928e74542c9f107e02aa6dd4748a79", archive_name="iperf.tar.gz",
        source_sha256="d6b4d2c7b20157c5b7fbd2de66f22bd3428958b5f2e28899151b6e22745886fe",
        observation_patch_path="tool_patches/iperf-3.17.1-acquisition.patch", observation_patch_sha256=iperf_digest)
    iperf_script = _recipe(iperf, config)[4][-1]
    assert "patch --batch --fuzz=0 -p1" in iperf_script
    assert subprocess.run(["sh", "-n", "-c", iperf_script], capture_output=True).returncode == 0


def test_pinned_bootstrap_records_observed_patch_and_capability_identity(tmp_path):
    sock_patch = Path(__file__).parents[2] / "src/cloud_network_benchmark/tool_patches/sockperf-3.10-acquisition.patch"
    iperf_patch = Path(__file__).parents[2] / "src/cloud_network_benchmark/tool_patches/iperf-3.17.1-acquisition.patch"
    sock_sha, iperf_sha = hashlib.sha256(sock_patch.read_bytes()).hexdigest(), hashlib.sha256(iperf_patch.read_bytes()).hexdigest()
    sock_commit, iperf_commit = "5ebd327da983225321818c0355db922515e026bd", "2acfcfe94e928e74542c9f107e02aa6dd4748a79"
    config = bootstrap_config().model_copy(update={"sources": [
        SourceBuild(tool="sockperf", source_url=f"https://github.com/Mellanox/sockperf/archive/{sock_commit}.tar.gz",
            source_reference=f"sockperf-{sock_commit}", archive_name="sockperf.tar.gz",
            source_sha256="a35a1daf310551b9f675af4fb7d95a6a10389fda4c7605d70a21554be0773f8e",
            upstream_commit=sock_commit, observation_patch_path="tool_patches/sockperf-3.10-acquisition.patch",
            observation_patch_sha256=sock_sha),
        SourceBuild(tool="iperf3", source_url=f"https://github.com/esnet/iperf/archive/{iperf_commit}.tar.gz",
            source_reference=f"iperf-{iperf_commit}", archive_name="iperf3.tar.gz",
            source_sha256="d6b4d2c7b20157c5b7fbd2de66f22bd3428958b5f2e28899151b6e22745886fe",
            upstream_commit=iperf_commit, observation_patch_path="tool_patches/iperf-3.17.1-acquisition.patch",
            observation_patch_sha256=iperf_sha)]})
    class PatchedRunner(BootstrapRunner):
        def run(self, request):
            aid = request.action_id
            tool = "sockperf" if "sockperf" in aid else "iperf3"
            if "CNB_BUILD_V1" in request.argv[-1]:
                import base64
                source = next(item for item in config.sources if item.tool == tool)
                version = ("sockperf, version 3.10\nCopyright (c) Mellanox Technologies\nBuild options: acquisition"
                           if tool == "sockperf" else
                           "iperf 3.17.1\nLinux synthetic-host 6.8.0\nCompiler: gcc 13.3.0")
                compiler = "gcc (Ubuntu 13.3.0-6ubuntu2) 13.3.0\nCopyright (C) 2024 Free Software Foundation, Inc."
                build_configuration = f"CC=${{CC:-cc}}; compiler={compiler}; ./configure --prefix=/var/lib/cnb/install; make -j2"
                semantics = "sockperf-3.10-enabled-v1" if tool == "sockperf" else "iperf-3.17.1-stats-v1"
                fields = ["CNB_BUILD_V1", f"tool={tool}", f"source_reference={source.source_reference}",
                    f"source_sha256={source.source_sha256}", f"upstream_commit={source.upstream_commit}",
                    f"patch_path={source.observation_patch_path}", f"patch_sha256={source.observation_patch_sha256}",
                    "compiler_version_b64=" + base64.b64encode(compiler.encode()).decode(),
                    "build_configuration_b64=" + base64.b64encode(build_configuration.encode()).decode(),
                    f"binary_path=/var/lib/cnb/install/bin/{tool}",
                    "binary_version_b64=" + base64.b64encode(version.encode()).decode(),
                    f"binary_sha256={'c' * 64}", "observation_capability=CNB_ACQ_V1",
                    f"observation_semantics={semantics}", "capabilities=" + ",".join(_required_capabilities(tool))]
                return result(aid, "\n".join(fields))
            if aid.endswith(f"{tool}-5"):
                return result(aid, "gcc (Ubuntu 13.3.0-6ubuntu2) 13.3.0\nCopyright (C) 2024 Free Software Foundation, Inc.\n")
            if aid.endswith(f"{tool}-6"):
                version = ("sockperf, version 3.10\nCopyright (c) Mellanox Technologies\nBuild options: acquisition\n"
                           if tool == "sockperf" else
                           "iperf 3.17.1\nLinux synthetic-host 6.8.0\nCompiler: gcc 13.3.0\n")
                return result(aid, version)
            if aid.endswith(f"{tool}-2"):
                digest = config.sources[0 if tool == "sockperf" else 1].source_sha256
                archive = "sockperf.tar.gz" if tool == "sockperf" else "iperf3.tar.gz"
                return result(aid, f"{digest} /var/lib/cnb/work/{archive}")
            if aid.endswith(("sockperf-11", "iperf3-9")):
                return result(aid, "CNB_ACQ_V1")
            response = super().run(request)
            if aid.endswith("listener-verify-iperf3"):
                return result(aid, response.stdout + "\nEnvironment=CNB_ACQUISITION_ID=f02r-vm_b-listener-iperf3\n")
            if aid.endswith("listener-config-iperf3"):
                return result(aid, response.stdout + "\nEnvironment=CNB_ACQUISITION_ID=f02r-vm_b-listener-iperf3\n")
            return response
    outcome = run_bootstrap(connection(tmp_path), config, PatchedRunner(), deadline=deadline(), clock=lambda: 100)
    assert outcome.succeeded
    for build in outcome.builds:
        assert "\nCopyright" in build.compiler_version
        assert "\n" in build.binary_version
        assert build.observation_capability == "CNB_ACQ_V1"
        assert build.patch_sha256 == (sock_sha if build.tool == "sockperf" else iperf_sha)
        assert build.observation_semantics == ("sockperf-3.10-enabled-v1" if build.tool == "sockperf" else "iperf-3.17.1-stats-v1")
        assert build.build_configuration and build.artifact_id
        assert build.build_configuration == (f"CC=${{CC:-cc}}; compiler={build.compiler_version}; "
            "./configure --prefix=/var/lib/cnb/install; make -j2")
    assert "compiler=$(cc --version)" in _recipe(config.sources[0], config)[-1][-1]
    assert "--version | head -n 1" not in _recipe(config.sources[0], config)[-1][-1]

    class MalformedSockperfBanner(PatchedRunner):
        def run(self, request):
            if request.action_id.endswith("sockperf-6"):
                from fixtures.f02r.support import result
                return result(request.action_id,
                    "sockperf, release 3.10\nCopyright (c) Mellanox Technologies\n")
            return super().run(request)
    rejected = run_bootstrap(connection(tmp_path), config, MalformedSockperfBanner(),
        deadline=deadline(), clock=lambda: 100)
    assert not rejected.succeeded and rejected.failure.category == "binary_version_missing"


def test_listener_failure_projection_retains_cat_response_and_first_failed_predicate(tmp_path):
    import base64
    from cloud_network_benchmark.lifecycle_adapters import prepare_remote_pair
    from cloud_network_benchmark.lifecycle_cli import _prepare_failure_projection, _safe_diagnostic_payload
    from cloud_network_benchmark.readiness import _predicate as readiness_predicate
    from tests.fixtures.f02r.support import nft_listing

    iperf_commit = "2acfcfe94e928e74542c9f107e02aa6dd4748a79"
    patch_path = Path(__file__).parents[2] / "src/cloud_network_benchmark/tool_patches/iperf-3.17.1-acquisition.patch"
    patch_sha = hashlib.sha256(patch_path.read_bytes()).hexdigest()
    base = bootstrap_config()
    iperf = SourceBuild(tool="iperf3", source_url=f"https://github.com/esnet/iperf/archive/{iperf_commit}.tar.gz",
        source_reference=f"iperf-{iperf_commit}", upstream_commit=iperf_commit, archive_name="iperf3.tar.gz",
        source_sha256="d6b4d2c7b20157c5b7fbd2de66f22bd3428958b5f2e28899151b6e22745886fe",
        observation_patch_path="tool_patches/iperf-3.17.1-acquisition.patch", observation_patch_sha256=patch_sha)
    config = base.model_copy(update={"sources": [base.sources[0], iperf]})
    connected = connection(tmp_path).model_copy(update={"private_ipv4_a": "10.80.1.10", "private_ipv4_b": "10.80.1.11"})

    class SourceGeneratedUnitRunner(BootstrapRunner):
        def __init__(self, *, include_cat_environment, include_sensitive_output=False,
                     cat_environment_value=None, extra_cat_environment=None):
            super().__init__()
            self.include_cat_environment = include_cat_environment
            self.include_sensitive_output = include_sensitive_output
            self.cat_environment_value = cat_environment_value
            self.extra_cat_environment = extra_cat_environment
            self.listener_launches = {}
            self.last_iperf_cat = None

        def run(self, request):
            aid = request.action_id
            tool = "sockperf" if "sockperf" in aid else "iperf3"
            source = next(item for item in config.sources if item.tool == tool)
            if "CNB_BUILD_V1" in request.argv[-1]:
                version = "iperf 3.17.1\nLinux synthetic-host 6.8.0\nCompiler: gcc 13.3.0" if tool == "iperf3" else "sockperf, version 3.10\nBuild options: acquisition"
                compiler = "gcc (Ubuntu 13.3.0-6ubuntu2) 13.3.0\nCopyright (C) 2024 Free Software Foundation, Inc."
                build_configuration = f"CC=${{CC:-cc}}; compiler={compiler}; ./configure --prefix=/var/lib/cnb/install; make -j2"
                semantics = "iperf-3.17.1-stats-v1" if tool == "iperf3" else "sockperf-3.10-enabled-v1"
                fields = ["CNB_BUILD_V1", f"tool={tool}", f"source_reference={source.source_reference}",
                    f"source_sha256={source.source_sha256}", f"upstream_commit={source.upstream_commit or ''}",
                    f"patch_path={source.observation_patch_path or ''}", f"patch_sha256={source.observation_patch_sha256 or ''}",
                    "compiler_version_b64=" + base64.b64encode(compiler.encode()).decode(),
                    "build_configuration_b64=" + base64.b64encode(build_configuration.encode()).decode(),
                    f"binary_path=/var/lib/cnb/install/bin/{tool}", "binary_version_b64=" + base64.b64encode(version.encode()).decode(),
                    "binary_sha256=" + "c" * 64, "observation_capability=CNB_ACQ_V1",
                    f"observation_semantics={semantics}", "capabilities=" + ",".join(_required_capabilities(tool))]
                return result(aid, "\n".join(fields))
            if aid.endswith("iperf3-2"):
                return result(aid, f"{source.source_sha256} /var/lib/cnb/work/{source.archive_name}")
            if aid.endswith("iperf3-5"):
                return result(aid, "gcc (Ubuntu 13.3.0-6ubuntu2) 13.3.0\nCopyright (C) 2024 Free Software Foundation, Inc.")
            if aid.endswith("iperf3-6"):
                return result(aid, "iperf 3.17.1\nLinux synthetic-host 6.8.0\nCompiler: gcc 13.3.0")
            if aid.endswith("iperf3-9"):
                return result(aid, "CNB_ACQ_V1")
            if aid in {"f02r-vm_b-listener-sockperf", "f02r-vm_b-listener-iperf3"}:
                tokens = shlex.split(request.argv[-1])
                start = tokens.index("systemd-run")
                self.listener_launches[tool] = tokens[start:]
                return result(aid)
            if "listener-verify-" in aid and aid.endswith(("sockperf", "iperf3")):
                launch = self.listener_launches[tool]
                binary_index = next(index for index, token in enumerate(launch) if token.endswith(f"/bin/{tool}"))
                unit = launch[launch.index("--unit") + 1]
                pid = 5345 if tool == "sockperf" else 5346
                properties = [token.removeprefix("--property=") for token in launch if token.startswith("--property=")]
                property_map = dict(item.split("=", 1) for item in properties)
                argv_text = shlex.join(launch[binary_index:])
                output = (f"Restart={property_map['Restart']}\nMainPID={pid}\nExecStart={{ path={launch[binary_index]} ; "
                    f"argv[]={argv_text} ; ignore_errors=no ; start_time=[Thu 2026-11-26 20:07:24 UTC] ; "
                    f"stop_time=[n/a] ; pid={pid} ; code=(null) ; status=0/0 }}\n")
                if "Environment" in property_map:
                    output += f"Environment={property_map['Environment']}\n"
                output += ("StandardOutput=append\nStandardError=append\nLoadState=loaded\nActiveState=active\n")
                return result(aid, output)
            if "listener-config-" in aid and aid.endswith(("sockperf", "iperf3")):
                assert tool in self.listener_launches
                launch = self.listener_launches[tool]
                unit = launch[launch.index("--unit") + 1]
                properties = [token.removeprefix("--property=") for token in launch if token.startswith("--property=")]
                property_map = dict(item.split("=", 1) for item in properties)
                lines = [f"# /run/systemd/transient/{unit}.service", "[Service]"]
                lines.extend(f"{key}={value}" for key, value in property_map.items()
                    if key in {"StandardOutput", "StandardError"})
                if self.include_cat_environment and "Environment" in property_map:
                    environment = self.cat_environment_value or property_map["Environment"]
                    lines.append(f'Environment="{environment}"')
                if self.extra_cat_environment is not None:
                    lines.append(f'Environment="{self.extra_cat_environment}"')
                if tool == "iperf3" and self.include_sensitive_output:
                    lines.extend([f"# auth-reference={connected.vm_b.authentication_reference}",
                        'Environment="AWS_SESSION_TOKEN=SESSION_VALUE WITH SPACES"',
                        'Environment="PASSWORD=PASSWORD_VALUE WITH SPACES"',
                        "Environment=AWS_SECRET_ACCESS_KEY=AKIA_SYNTHETIC_VALUE"])
                # This is the source-generated transient unit projection. The negative
                # variant models a successful cat response missing one required field.
                output = "\n".join(lines) + "\n"
                if tool == "iperf3":
                    self.last_iperf_cat = output
                return result(aid, output)
            if "listener-process-" in aid and aid.endswith(("sockperf", "iperf3")):
                pid = 5345 if tool == "sockperf" else 5346
                return result(aid, f"/var/lib/cnb/install/bin/{tool}\n" + "c" * 64 + f" /proc/{pid}/exe")
            if aid == "f02r-vm_b-acl-inspect":
                listing = nft_listing(connected.deployment_id).replace("10.0.0.1", connected.private_ipv4_a).replace(
                    "10.0.0.2", connected.private_ipv4_b)
                return result(aid, listing)
            return super().run(request)

    failed_runner = SourceGeneratedUnitRunner(include_cat_environment=False, include_sensitive_output=True)
    failed = prepare_remote_pair(connected, bootstrap_config=config, readiness_config=None,
        runner=failed_runner, deadline=deadline(),
        source_root=Path(__file__).parents[2], source_revision="synthetic-source-revision",
        remote_root="/var/lib/cnb", clock=lambda: 100)
    assert failed.stage == "prepare" and failed.succeeded is False
    assert failed.evidence["bootstrap"].failure.category == "listener_identity_unverified"
    projection = _prepare_failure_projection("synthetic-gcp-run", "gcp", failed)
    identity = projection["failures"][0]["listener_identity"]
    assert identity["first_failed_predicate"] == "systemctl_cat.acquisition_environment"
    cat = identity["systemctl_cat"]
    assert "# auth-reference=[REDACTED]" in cat["diagnostic"]
    assert cat["diagnostic"].count("Environment=[REDACTED]") == 3
    assert "SESSION_VALUE" not in cat["diagnostic"] and "WITH SPACES" not in cat["diagnostic"]
    assert "PASSWORD_VALUE" not in cat["diagnostic"]
    assert connected.vm_b.authentication_reference not in cat["diagnostic"]
    assert "AKIA_SYNTHETIC_VALUE" not in cat["diagnostic"]
    assert cat["source_bytes"] == len(failed_runner.last_iperf_cat.encode())
    assert cat["retained_diagnostic_sha256"] == hashlib.sha256(cat["diagnostic"].encode()).hexdigest()
    assert not cat["diagnostic_truncated"] and not cat["source_scan_truncated"]
    assert identity["systemctl_show_outcome"] == identity["systemctl_cat_outcome"] == "succeeded"
    assert identity["systemctl_show_exit_code"] == identity["systemctl_cat_exit_code"] == 0
    direct_sensitive = _safe_diagnostic_payload(
        'Environment="CNB_ACQUISITION_ID=f02r-vm_b-listener-iperf3" "AWS_SESSION_TOKEN=SESSION VALUE"')
    assert direct_sensitive["diagnostic"] == "Environment=[REDACTED]"
    direct_password = _safe_diagnostic_payload('Environment="PASSWORD=PASSWORD VALUE"')
    assert direct_password["diagnostic"] == "Environment=[REDACTED]"

    # The same production launch command, with all generated unit properties
    # present in `systemctl cat`, must satisfy the unchanged strict consumer.
    accepted_runner = SourceGeneratedUnitRunner(include_cat_environment=True)
    accepted = run_bootstrap(connected, config, accepted_runner,
        deadline=deadline(), clock=lambda: 100)
    assert accepted.succeeded, accepted.failure
    assert accepted_runner.listener_launches["iperf3"][-8:] == [
        "/var/lib/cnb/install/bin/iperf3", "-s", "-B", "10.80.1.11", "-p", "5201", "--json-stream", "--forceflush"]
    expected_environment = "CNB_ACQUISITION_ID=f02r-vm_b-listener-iperf3"
    assert readiness_predicate("unit-config-iperf3", accepted_runner.last_iperf_cat, connected, accepted)
    wrong_environment = accepted_runner.last_iperf_cat.replace(expected_environment, expected_environment + "-wrong")
    assert not readiness_predicate("unit-config-iperf3", wrong_environment, connected, accepted)
    duplicate_environment = accepted_runner.last_iperf_cat + f'Environment="{expected_environment}"\n'
    assert not readiness_predicate("unit-config-iperf3", duplicate_environment, connected, accepted)

    for rejected_runner in (
        SourceGeneratedUnitRunner(include_cat_environment=True,
            cat_environment_value=expected_environment + "-wrong"),
        SourceGeneratedUnitRunner(include_cat_environment=True,
            extra_cat_environment="CNB_ACQUISITION_ID=wrong"),
    ):
        rejected = run_bootstrap(connected, config, rejected_runner,
            deadline=deadline(), clock=lambda: 100)
        assert not rejected.succeeded and rejected.failure.category == "listener_identity_unverified"
        assert rejected.failure.details["listener_identity"]["first_failed_predicate"] == "systemctl_cat.acquisition_environment"


def test_empty_compiler_and_runner_exception_return_accumulated_failure_evidence(tmp_path):
    c=connection(tmp_path)
    class EmptyCompiler(BootstrapRunner):
        def run(self,request):
            if request.action_id.endswith("sockperf-5"): return result(request.action_id,"")
            return super().run(request)
    failed=run_bootstrap(c,bootstrap_config(),EmptyCompiler(),deadline=deadline(),clock=lambda:100)
    assert not failed.succeeded and failed.failure.category=="compiler_identity_missing", (failed.failure.category, [a.action_id for a in failed.actions[-5:]])
    class Raising(BootstrapRunner):
        def run(self,request):
            if request.action_id.endswith("sockperf-1"): raise OSError("synthetic")
            return super().run(request)
    failed=run_bootstrap(c,bootstrap_config(),Raising(),deadline=deadline(),clock=lambda:100)
    assert not failed.succeeded and len(failed.actions)>=3 and failed.failure.category=="source_build_failed"


def test_interrupted_runner_and_lost_identity_file_preserve_failure_evidence(tmp_path):
    c=connection(tmp_path)
    class Interrupted(BootstrapRunner):
        def run(self,request):
            if request.action_id.endswith("cloud-init"):
                raise KeyboardInterrupt()
            return super().run(request)
    outcome=run_bootstrap(c,bootstrap_config(),Interrupted(),deadline=deadline(),clock=lambda:100)
    assert not outcome.succeeded and outcome.actions and outcome.failure.category=="command_interrupted"
    class LostKey(BootstrapRunner):
        def run(self,request):
            response=super().run(request)
            if request.action_id.endswith("cloud-init"):
                Path(c.vm_a.authentication_reference).unlink(missing_ok=True)
            return response
    runner=LostKey(); outcome=run_bootstrap(c,bootstrap_config(),runner,deadline=deadline(),clock=lambda:100)
    assert not outcome.succeeded and outcome.actions and outcome.failure.category=="transport_preflight_failed"
    assert all("preflight-failed" not in request.argv for request in runner.requests)


def test_known_hosts_loss_between_vm_a_and_vm_b_returns_accumulated_result(tmp_path):
    c=connection(tmp_path)
    class LostAtBoundary(BootstrapRunner):
        def run(self,request):
            response=super().run(request)
            if request.action_id=="f02r-vm_a-iperf3-8":
                Path(c.vm_a.host_key_reference).unlink(missing_ok=True)
            return response
    runner=LostAtBoundary(); outcome=run_bootstrap(c,bootstrap_config(),runner,deadline=deadline(),clock=lambda:100)
    assert not outcome.succeeded and outcome.failure.category=="transport_preflight_failed"
    assert len(outcome.actions)>10 and not any("vm_b-cloud-init" in request.action_id for request in runner.requests)


def test_request_construction_failure_after_identity_preflight_is_returned_as_evidence(tmp_path):
    c=connection(tmp_path)
    class LostDuringIdentityCheck(BootstrapRunner):
        def run(self,request):
            response=super().run(request)
            if request.action_id=="f02r-vm_a-sockperf-identity-preflight":
                Path(c.vm_a.host_key_reference).unlink(missing_ok=True)
            return response
    runner=LostDuringIdentityCheck()
    outcome=run_bootstrap(c,bootstrap_config(),runner,deadline=deadline(),clock=lambda:100)
    assert not outcome.succeeded and outcome.failure.category=="transport_preflight_failed"
    assert len(outcome.actions)>2 and outcome.actions[-1].action_id=="f02r-vm_a-sockperf-workspace"
    assert not any(request.action_id=="f02r-vm_a-sockperf-workspace" for request in runner.requests)
    assert "request construction failed" in outcome.actions[-1].result["stderr"]


def test_listener_request_construction_failure_preserves_setup_actions(tmp_path):
    c=connection(tmp_path)
    class LostBeforeListener(BootstrapRunner):
        def run(self,request):
            response=super().run(request)
            if request.action_id=="f02r-vm_b-listener-log-directory-sockperf":
                Path(c.vm_b.authentication_reference).unlink(missing_ok=True)
            return response
    runner=LostBeforeListener()
    outcome=run_bootstrap(c,bootstrap_config(),runner,deadline=deadline(),clock=lambda:100)
    assert not outcome.succeeded and outcome.failure.category=="transport_preflight_failed"
    assert outcome.actions[-1].action_id=="f02r-vm_b-listener-sockperf"
    assert not any(request.action_id=="f02r-vm_b-listener-sockperf" for request in runner.requests)


def test_listener_observation_exhausting_deadline_returns_evidence(tmp_path):
    c=connection(tmp_path); clock_value=[100.0]
    class Clock:
        def __call__(self): return clock_value[0]
    class SlowFinalObservation(BootstrapRunner):
        def run(self,request):
            response=super().run(request)
            if "listener-config-sockperf" in request.action_id:
                clock_value[0]=200.0
            return response
    outcome=run_bootstrap(c,bootstrap_config(),SlowFinalObservation(),deadline=deadline(),clock=Clock())
    assert not outcome.succeeded and outcome.failure.category=="readiness_deadline_exhausted"
    assert outcome.actions and not any("listener-process-sockperf" in action.action_id for action in outcome.actions)


def test_successful_build_observations_redact_credentials_and_secret_text(tmp_path):
    c=connection(tmp_path); key=c.vm_a.authentication_reference
    class LeakyCompiler(BootstrapRunner):
        def run(self,request):
            if request.action_id.endswith("-5"):
                return result(request.action_id,f"gcc version 13.2 key={key} token=synthetic-secret")
            return super().run(request)
    outcome=run_bootstrap(c,bootstrap_config(),LeakyCompiler(),deadline=deadline(),clock=lambda:100)
    assert outcome.succeeded
    serialized=outcome.model_dump_json()
    assert key not in serialized and "synthetic-secret" not in serialized


def test_sockperf_subcommand_capabilities_are_observed_independently(tmp_path):
    c=connection(tmp_path)
    class MissingUnderLoad(BootstrapRunner):
        def run(self,request):
            if request.action_id.endswith("-10") and "sockperf" in request.action_id:
                return result(request.action_id,"Usage: sockperf under-load -i -p -m --mps --reply-every --full-log -t")
            return super().run(request)
    outcome=run_bootstrap(c,bootstrap_config(),MissingUnderLoad(),deadline=deadline(),clock=lambda:100)
    assert not outcome.succeeded and outcome.failure.category=="capability_missing"
    assert not any("listener-" in action.action_id for action in outcome.actions)


def test_cc_version_accepts_authentic_driver_banner_and_rejects_malformed_text():
    assert _compiler_identity_observed("cc (Ubuntu 13.3.0-6ubuntu2) 13.3.0")
    assert _compiler_identity_observed("Apple clang version 16.0.0")
    assert _compiler_identity_observed("gcc (GCC) 14.2.1")
    assert not _compiler_identity_observed("")
    assert not _compiler_identity_observed("cc version not found")


def test_acl_rejects_accept_or_jump_bypass_before_expected_rules():
    from cloud_network_benchmark.bootstrap import _acl_matches
    from fixtures.f02r.support import nft_listing
    valid=nft_listing("campaign-aws-deployment"); chain="cnb_campaign_aws_deployment"
    assert _acl_matches(valid,"10.0.0.1","10.0.0.2",chain)
    assert not _acl_matches(valid.replace("priority -10; policy accept;", "priority -10; policy accept;\n  ip daddr 10.0.0.2 accept"),"10.0.0.1","10.0.0.2",chain)
    assert not _acl_matches(valid.replace("priority -10; policy accept;", "type filter hook output priority -10; policy accept;"),"10.0.0.1","10.0.0.2",chain)
    assert not _acl_matches(valid.replace("chain cnb_campaign_aws_deployment {", "chain cnb_campaign_aws_deployment {\n  jump unrestricted"),"10.0.0.1","10.0.0.2",chain)
    assert not _acl_matches(valid,"10.0.0.1","10.0.0.2","wrong_chain")


def test_acl_preflight_requests_numeric_priority_format_for_fresh_and_reused_chains(tmp_path):
    for existing in (False, True):
        case=tmp_path / str(existing); case.mkdir()
        c=connection(case); runner=BootstrapRunner(existing_acl=existing)
        outcome=run_bootstrap(c,bootstrap_config(),runner,deadline=deadline(),clock=lambda:100)
        assert outcome.succeeded
        request=next(request for request in runner.requests if request.action_id=="f02r-vm_b-acl-preflight-inspect")
        assert "nft -n -y list chain" in request.argv[-1]


def test_effective_systemd_log_destinations_honor_ordered_overrides_and_resets():
    from cloud_network_benchmark.bootstrap import _unit_log_destinations_match
    expected_out="/var/lib/cnb/work/server.log"; expected_err="/var/lib/cnb/work/server.err"
    base=("# /run/systemd/transient/unit.service\n[Service]\n"
          f"StandardOutput=append:{expected_out}\nStandardError=append:{expected_err}\n")
    assert _unit_log_destinations_match(base,expected_out,expected_err)
    assert not _unit_log_destinations_match(base+"# /etc/systemd/system/unit.service.d/override.conf\n[Service]\nStandardOutput=append:/var/lib/cnb/wrong.log\n",expected_out,expected_err)
    assert not _unit_log_destinations_match(base+"# reset drop-in\n[Service]\nStandardError=\n",expected_out,expected_err)
    assert not _unit_log_destinations_match(base+"# conflicting drop-in\n[Service]\nStandardError=append:/var/lib/cnb/other.err\n",expected_out,expected_err)
    assert _unit_log_destinations_match(base+"[Unit]\nStandardOutput=append:/wrong/section\n",expected_out,expected_err)


@pytest.mark.parametrize("override",[
    "StandardOutput=append:/var/lib/cnb/wrong.log\n",
    "StandardError=append:/var/lib/cnb/wrong.err\n",
    "StandardOutput=\n",
    "StandardError=\n",
])
def test_repeated_preparation_rejects_effective_log_override_or_reset(tmp_path,override):
    c=connection(tmp_path)
    class WrongDropin(BootstrapRunner):
        def run(self,request):
            if request.action_id=="f02r-vm_b-preflight-listener-config-sockperf":
                return result(request.action_id,
                    "[Service]\nStandardOutput=append:/var/lib/cnb/work/server-logs/sockperf.log\n"
                    "StandardError=append:/var/lib/cnb/work/server-logs/sockperf.err\n"
                    "# /etc/systemd/system/override.conf\n[Service]\n"+override)
            return super().run(request)
    outcome=run_bootstrap(c,bootstrap_config(),WrongDropin(existing_listeners=True,existing_acl=True),deadline=deadline(),clock=lambda:100)
    assert not outcome.succeeded and outcome.failure.category=="listener_state_mismatch"
