from datetime import datetime, timezone
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).parents[1]))
from fixtures.f02r.support import connection
from cloud_network_benchmark.access import ConnectionOutput, RemoteDeadline, _action, _failure, _request
from cloud_network_benchmark.contracts.common import IdentityContext
from cloud_network_benchmark.contracts.execution import CommandResult


def test_connection_requires_matching_identity_and_private_pair(tmp_path):
    c=connection(tmp_path)
    assert c.vm_a.role == "vm_a" and c.identity.run_id == c.run_id
    with pytest.raises(ValueError): ConnectionOutput.model_validate({**c.model_dump(), "private_ipv4_b":"8.8.8.8"})
    with pytest.raises(ValueError): ConnectionOutput.model_validate({**c.model_dump(), "deployment_id":"other"})


def test_transport_is_strict_noninteractive_and_remote_command_is_single_quoted_argument(tmp_path):
    c=connection(tmp_path); vm=c.vm_a
    request=_request("check",vm,["printf","%s","value; touch /tmp/no"],5)
    assert request.argv[:2] == ["ssh","-p"]
    assert "BatchMode=yes" in request.argv and "IdentitiesOnly=yes" in request.argv
    assert "StrictHostKeyChecking=yes" in request.argv and "PasswordAuthentication=no" in request.argv
    assert request.argv[-2] == "ubuntu@10.0.0.1"
    assert len(request.argv[-1]) and "'value; touch /tmp/no'" in request.argv[-1]
    assert vm.authentication_reference not in str(request.durable_view())


def test_missing_strict_identity_file_fails_before_runner(tmp_path):
    c=connection(tmp_path)
    object.__setattr__(c.vm_a,"host_key_reference",str(tmp_path/"missing"))
    with pytest.raises(ValueError,match="known-host"):
        _request("x",c.vm_a,["true"],1)


def test_empty_identity_files_and_remote_control_values_are_rejected(tmp_path):
    c=connection(tmp_path)
    empty=tmp_path/"empty-known-hosts"; empty.touch()
    object.__setattr__(c.vm_a,"host_key_reference",str(empty))
    with pytest.raises(ValueError,match="nonempty readable"):
        _request("empty",c.vm_a,["true"],1)
    object.__setattr__(c.vm_a,"host_key_reference",str(tmp_path/"known-hosts"))
    with pytest.raises(ValueError,match="safe string tokens"):
        _request("control",c.vm_a,["printf","bad\nvalue"],1)
    with pytest.raises(ValueError,match="safe string tokens"):
        _request("nul",c.vm_a,["printf","bad\x00value"],1)


def test_deadline_rejects_nonfinite_and_readiness_after_overall(tmp_path):
    from datetime import datetime,timedelta,timezone
    now=datetime.now(timezone.utc)
    with pytest.raises(ValueError): RemoteDeadline(readiness_deadline_monotonic=2,overall_deadline_monotonic=1,cleanup_reserve_seconds=1,cap_started_at=now,cap_expires_at=now+timedelta(seconds=2))


def test_legacy_bootstrap_configuration_is_rejected():
    from cloud_network_benchmark.bootstrap import BootstrapConfig
    with pytest.raises(Exception): BootstrapConfig(flent_version="1",netperf_version="1",linux_tools=["iproute2"])


def test_success_and_failure_evidence_redact_nested_local_references(tmp_path):
    c=connection(tmp_path); vm=c.vm_a; request=_request("redact",vm,["true"],1)
    now=datetime.now(timezone.utc)
    secret=f"{vm.authentication_reference} -----BEGIN PRIVATE KEY----- synthetic -----END PRIVATE KEY-----"
    result=CommandResult(action_id="redact",outcome="failed",started_at=now,finished_at=now,duration_seconds=0,exit_code=1,stdout=secret,stderr=secret)
    action=_action("redact","ssh",vm,request,result)
    failure=_failure("bootstrap",vm,"failed",result,"failed",request)
    for evidence in (action.model_dump_json(),failure.model_dump_json()):
        assert vm.authentication_reference not in evidence
        assert "synthetic" not in evidence and "BEGIN PRIVATE KEY" not in evidence
