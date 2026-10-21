from pathlib import Path
import json,sys
sys.path.insert(0,str(Path(__file__).parents[1]))
from fixtures.f02r.support import BootstrapRunner,bootstrap_config,connection,deadline,result,readiness_output
from cloud_network_benchmark.bootstrap import run_bootstrap
from cloud_network_benchmark.readiness import ReadinessConfig,run_readiness


def test_readiness_uses_matching_preparation_and_observes_all_fixed_predicates(tmp_path):
    c=connection(tmp_path); prep=run_bootstrap(c,bootstrap_config(),BootstrapRunner(),deadline=deadline(),clock=lambda:100)
    class Runner:
        def __init__(self): self.requests=[]
        def run(self,request):
            self.requests.append(request); aid=request.action_id
            return result(aid,readiness_output(request,prep))
    runner=Runner(); outcome=run_readiness(c,ReadinessConfig(poll_interval_seconds=.1,attempt_ceiling=2),runner,prepared=prep,deadline=deadline(),clock=lambda:100)
    assert outcome.ready and len(outcome.observations)==11
    assert all(not ("systemctl" in req.argv and any(req.argv[index] in {"start", "restart"} for index in range(len(req.argv)))) for req in runner.requests)


def test_deadline_exhaustion_starts_no_remote_command(tmp_path):
    c=connection(tmp_path); prep=run_bootstrap(c,bootstrap_config(),BootstrapRunner(),deadline=deadline(),clock=lambda:100)
    class Never:
        requests=[]
        def run(self,request): self.requests.append(request); raise AssertionError("no command after deadline")
    runner=Never(); outcome=run_readiness(c,ReadinessConfig(poll_interval_seconds=1,attempt_ceiling=3),runner,prepared=prep,deadline=deadline(),clock=lambda:200)
    assert not outcome.ready and outcome.failure.category=="readiness_deadline_exhausted" and not runner.requests


def test_stale_bootstrap_evidence_fails_closed(tmp_path):
    c=connection(tmp_path); prep=run_bootstrap(c,bootstrap_config(),BootstrapRunner(),deadline=deadline(),clock=lambda:100)
    from cloud_network_benchmark.bootstrap import BootstrapResult
    stale=prep.model_copy(update={"deployment_id":"other"})
    outcome=run_readiness(c,ReadinessConfig(poll_interval_seconds=1,attempt_ceiling=1),BootstrapRunner(),prepared=stale,deadline=deadline(),clock=lambda:100)
    assert not outcome.ready and outcome.failure.category=="bootstrap_evidence_mismatch"


def test_wildcard_listener_never_satisfies_private_bind_predicate(tmp_path):
    from cloud_network_benchmark.readiness import _predicate
    c=connection(tmp_path); prep=run_bootstrap(c,bootstrap_config(),BootstrapRunner(),deadline=deadline(),clock=lambda:100)
    assert not _predicate("socket-sockperf",'UNCONN 0 0 0.0.0.0:11111 0.0.0.0:* users:(("sockperf",pid=20,fd=3))',c,prep)
    assert not _predicate("socket-iperf3",'LISTEN 0 4096 *:5201 *:* users:(("iperf3",pid=21,fd=3))',c,prep)


def test_wrong_private_target_service_binary_and_acl_are_unhealthy(tmp_path):
    from cloud_network_benchmark.readiness import _predicate
    c=connection(tmp_path); prep=run_bootstrap(c,bootstrap_config(),BootstrapRunner(),deadline=deadline(),clock=lambda:100)
    assert not _predicate("private-icmp","64 bytes from 203.0.113.4",c,prep)
    wrong_service="LoadState=loaded\nActiveState=active\nExecStart={ path=/tmp/iperf3; argv[]=/tmp/iperf3 -s -B 10.0.0.2 -p 5201 --json-stream --forceflush; }\nStandardOutput=append:/var/lib/cnb/work/server-logs/iperf3-server.jsonl\nStandardError=append:/var/lib/cnb/work/server-logs/iperf3-server.err\nRestart=no\n"
    assert not _predicate("service-iperf3",wrong_service,c,prep)
    assert not _predicate("guest-acl","hook input policy accept 0.0.0.0/0 10.0.0.2 11111 5201 drop",c,prep)


def test_slow_checks_shrink_budgets_and_stop_at_deadline(tmp_path):
    from datetime import datetime,timedelta,timezone
    from fixtures.f02r.helpers import FakeClock
    from cloud_network_benchmark.access import RemoteDeadline
    c=connection(tmp_path); prep=run_bootstrap(c,bootstrap_config(),BootstrapRunner(),deadline=deadline(),clock=lambda:100)
    clock=FakeClock(100)
    class Slow:
        def __init__(self): self.requests=[]
        def run(self,request):
            self.requests.append(request); clock.advance(4)
            from fixtures.f02r.support import result
            return result(request.action_id,"unhealthy",success=False)
    runner=Slow(); now=datetime.now(timezone.utc)
    limit=RemoteDeadline(readiness_deadline_monotonic=108,overall_deadline_monotonic=108,cleanup_reserve_seconds=1,cap_started_at=now,cap_expires_at=now+timedelta(seconds=20))
    outcome=run_readiness(c,ReadinessConfig(poll_interval_seconds=1,attempt_ceiling=3),runner,prepared=prep,deadline=limit,clock=clock)
    assert not outcome.ready and outcome.failure.category=="readiness_deadline_exhausted"
    assert [request.timeout_seconds for request in runner.requests]==[5,4]
    assert clock.now==108


def test_interrupted_readiness_check_is_reported_without_retry(tmp_path):
    from datetime import datetime,timezone
    from cloud_network_benchmark.contracts.execution import CommandResult
    from cloud_network_benchmark.contracts.common import CommandOutcome
    c=connection(tmp_path); prep=run_bootstrap(c,bootstrap_config(),BootstrapRunner(),deadline=deadline(),clock=lambda:100)
    class Interrupted:
        def __init__(self): self.requests=[]
        def run(self,request):
            self.requests.append(request); now=datetime.now(timezone.utc)
            return CommandResult(action_id=request.action_id,outcome=CommandOutcome.INTERRUPTED,started_at=now,finished_at=now,duration_seconds=0,exit_code=None,stdout="",stderr="")
    runner=Interrupted(); outcome=run_readiness(c,ReadinessConfig(poll_interval_seconds=.1,attempt_ceiling=4),runner,prepared=prep,deadline=deadline(),clock=lambda:100)
    assert not outcome.ready and outcome.failure.category=="readiness_interrupted"
    assert len(runner.requests)==1
