from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parents[1]))
from fixtures.f02r.support import BootstrapRunner,bootstrap_config,connection,deadline,result
from cloud_network_benchmark.bootstrap import run_bootstrap


def test_failed_cloud_init_stops_before_other_vm(tmp_path):
    c=connection(tmp_path); runner=BootstrapRunner(fail_action="f02r-vm_a-cloud-init")
    outcome=run_bootstrap(c,bootstrap_config(),runner,deadline=deadline(),clock=lambda:100)
    assert not outcome.succeeded and outcome.failure.category=="cloud_init_failed"
    assert len(outcome.actions)==1
    assert not any("vm_b" in item.action_id for item in outcome.actions)


def test_preparation_deadline_is_consumed_cumulatively(tmp_path):
    c=connection(tmp_path); calls=[]
    class Slow:
        def run(self,request):
            calls.append(request)
            from fixtures.f02r.support import BootstrapRunner
            return BootstrapRunner().run(request)
    clock=lambda:200
    outcome=run_bootstrap(c,bootstrap_config(),Slow(),deadline=deadline(),clock=clock)
    assert not outcome.succeeded and outcome.failure.category=="readiness_deadline_exhausted"
    assert calls==[]
