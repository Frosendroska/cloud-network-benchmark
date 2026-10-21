from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).parents[1]))
from fixtures.f02r.support import BootstrapRunner, bootstrap_config, connection, deadline
from cloud_network_benchmark.bootstrap import SourceBuild, run_bootstrap


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
    acl_rules=[request.argv[-1] for request in runner.requests if "acl-create" in request.action_id]
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
