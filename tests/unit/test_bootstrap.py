from pathlib import Path
import hashlib
import shlex
import subprocess
import sys
import pytest
sys.path.insert(0, str(Path(__file__).parents[1]))
from fixtures.f02r.support import BootstrapRunner, bootstrap_config, connection, deadline, result
from cloud_network_benchmark.bootstrap import SourceBuild, run_bootstrap
from cloud_network_benchmark.bootstrap import _capabilities_observed, _identity_preflight_script, _recipe


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
    marker.write_text(f"{source.source_reference} {source.source_sha256} {digest}\n")
    probe=subprocess.run(["sh","-c",_identity_preflight_script(source,c)],text=True,capture_output=True)
    assert probe.returncode==0 and probe.stdout.strip()=="matching"
    recipe=_recipe(source,c)
    checked=subprocess.run(["sh","-n","-c",recipe[4][-1]],text=True,capture_output=True)
    assert checked.returncode==0
    before=binary.read_bytes(); reused=subprocess.run(["sh","-c",recipe[4][-1]],text=True,capture_output=True)
    assert reused.returncode==0 and binary.read_bytes()==before
    marker.write_text(f"other-source {source.source_sha256} {digest}\n")
    conflict=subprocess.run(["sh","-c",_identity_preflight_script(source,c)],text=True,capture_output=True)
    assert conflict.returncode != 0


def test_shell_unsafe_build_values_are_rejected_and_help_is_token_based():
    c=bootstrap_config()
    with pytest.raises(Exception): type(c).model_validate({**c.model_dump(),"workspace":"/var/lib/cnb/work;touch"})
    with pytest.raises(Exception): SourceBuild(tool="sockperf",source_url="https://example.invalid/x",source_reference="src;touch",archive_name="x.tar.gz",source_sha256="a"*64)
    assert not _capabilities_observed("Usage: tool [options]\nunder-loadish -iSomething --full-rtt-extra", "sockperf")


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


def test_acl_rejects_accept_or_jump_bypass_before_expected_rules():
    from cloud_network_benchmark.bootstrap import _acl_matches
    valid="type filter hook input priority -10; policy accept;\nip saddr 10.0.0.1/32 ip daddr 10.0.0.2 udp dport 11111 accept\nip daddr 10.0.0.2 udp dport 11111 drop\nip saddr 10.0.0.1/32 ip daddr 10.0.0.2 tcp dport 5201 accept\nip daddr 10.0.0.2 tcp dport 5201 drop"
    assert _acl_matches(valid,"10.0.0.1","10.0.0.2")
    assert not _acl_matches(valid.replace("priority -10; policy accept;", "priority -10; policy accept;\nip daddr 10.0.0.2 accept"),"10.0.0.1","10.0.0.2")
    assert not _acl_matches(valid.replace("priority -10; policy accept;", "priority -10; policy accept;\njump unrestricted"),"10.0.0.1","10.0.0.2")
