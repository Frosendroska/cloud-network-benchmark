from datetime import datetime,timezone
import hashlib
from pathlib import Path
import subprocess
import sys
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]))
from fixtures.f02r.support import connection,deadline,result
from cloud_network_benchmark.retrieval import RetrievalItem,RetrievalManifest,run_retrieval
from cloud_network_benchmark.retrieval import _inventory_script
from cloud_network_benchmark.contracts.artifacts import ArtifactInput


def manifest(c,payload=b"native"):
    now=datetime.now(timezone.utc)
    declaration=ArtifactInput(artifact_id="sockperf-a",path="measurements/a.csv",kind="sockperf-csv",producer="vm_a",unit_id="unit-1",expectation="required")
    item=RetrievalItem(artifact_id="sockperf-a",deployment_id=c.deployment_id,source_vm=c.vm_a.host,source_role="vm_a",source_path="/var/lib/cnb/work/a.csv",destination_path="measurements/a.csv",kind="sockperf-csv",unit_id="unit-1",expected_size_bytes=len(payload),expected_sha256=hashlib.sha256(payload).hexdigest(),timeout_seconds=5)
    return RetrievalManifest(identity=c.identity.model_dump(),deployment_id=c.deployment_id,remote_workspace="/var/lib/cnb/work",declarations=[declaration],items=[item],measurement_ended_at=now,producers_closed_at=now,closure_evidence="producer flush observed")


def multi_manifest(c,payload=b"native"):
    first=manifest(c,payload); now=first.measurement_ended_at
    declaration=ArtifactInput(artifact_id="iperf3-b",path="measurements/b.jsonl",kind="iperf3-jsonl",producer="vm_b",expectation="required")
    item=RetrievalItem(artifact_id="iperf3-b",deployment_id=c.deployment_id,source_vm=c.vm_b.host,source_role="vm_b",source_path="/var/lib/cnb/work/b.jsonl",destination_path="measurements/b.jsonl",kind="iperf3-jsonl",expected_size_bytes=len(payload),expected_sha256=hashlib.sha256(payload).hexdigest(),timeout_seconds=5)
    return RetrievalManifest(identity=c.identity.model_dump(),deployment_id=c.deployment_id,remote_workspace="/var/lib/cnb/work",declarations=[*first.declarations,declaration],items=[*first.items,item],measurement_ended_at=now,producers_closed_at=now,closure_evidence="both producers flush observed")


def test_retrieval_verifies_materialized_bytes_and_final_record(tmp_path):
    c=connection(tmp_path); payload=b"native"
    class Runner:
        def __init__(self): self.requests=[]
        def run(self,request):
            self.requests.append(request)
            if request.action_id.startswith("f02r-retrieve-observe"):
                out=f"{len(payload)} {hashlib.sha256(payload).hexdigest()}"
            else:
                Path(request.argv[-1]).write_bytes(payload); out=""
            return result(request.action_id,out)
    runner=Runner(); outcome=run_retrieval(c,manifest(c,payload),runner,tmp_path,deadline=deadline(),clock=lambda:100)
    assert outcome.complete and outcome.cleanup_required and outcome.retrieved==["sockperf-a"]
    outcome.records[0].verify_materialized_file(tmp_path)
    assert (tmp_path/"measurements/a.csv").read_bytes()==payload
    assert runner.requests[-1].action_type=="scp" and "StrictHostKeyChecking=yes" in runner.requests[-1].argv


def test_observed_zero_byte_native_file_is_verifiable(tmp_path):
    c=connection(tmp_path); payload=b""
    class Runner:
        def run(self,request):
            if request.action_type=="ssh": out=f"0 {hashlib.sha256(payload).hexdigest()}"
            else: Path(request.argv[-1]).write_bytes(payload); out=""
            return result(request.action_id,out)
    outcome=run_retrieval(c,manifest(c,payload),Runner(),tmp_path,deadline=deadline(),clock=lambda:100)
    assert outcome.complete and outcome.records[0].size_bytes.value==0
    assert outcome.records[0].sha256.value==hashlib.sha256(payload).hexdigest()


def test_stdout_does_not_fabricate_missing_transfer_file(tmp_path):
    c=connection(tmp_path); payload=b"native"
    class Runner:
        def run(self,request):
            out=f"{len(payload)} {hashlib.sha256(payload).hexdigest()}" if "observe" in request.action_id else "native"
            return result(request.action_id,out)
    outcome=run_retrieval(c,manifest(c,payload),Runner(),tmp_path,deadline=deadline(),clock=lambda:100)
    assert not outcome.complete and not (tmp_path/"measurements/a.csv").exists()
    assert outcome.attempts[0].reason=="copy_not_materialized"


def test_open_or_unclosed_inventory_is_rejected(tmp_path):
    c=connection(tmp_path); m=manifest(c)
    with pytest.raises(ValueError): RetrievalManifest.model_validate({**m.model_dump(),"producers_closed_at":datetime.now(timezone.utc).replace(year=2020)})


def test_destination_collision_is_preserved_as_failure(tmp_path):
    c=connection(tmp_path); (tmp_path/"measurements").mkdir(); (tmp_path/"measurements/a.csv").write_text("old")
    outcome=run_retrieval(c,manifest(c),lambda req: (_ for _ in ()).throw(AssertionError("transfer started")),tmp_path,deadline=deadline(),clock=lambda:100)
    assert not outcome.complete and outcome.failure.category=="destination_invalid"


def test_symlink_destination_parent_is_rejected_without_copy(tmp_path):
    c=connection(tmp_path); outside=tmp_path/"outside-target"; outside.mkdir()
    (tmp_path/"measurements").symlink_to(outside,target_is_directory=True)
    outcome=run_retrieval(c,manifest(c),lambda req: (_ for _ in ()).throw(AssertionError("transfer started")),tmp_path,deadline=deadline(),clock=lambda:100)
    assert not outcome.complete and outcome.failure.category=="destination_invalid"
    assert not list(outside.iterdir())


def test_cleanup_reserve_exhaustion_stops_before_remote_observation(tmp_path):
    c=connection(tmp_path); runner=lambda req: (_ for _ in ()).throw(AssertionError("copy started after cleanup reserve"))
    outcome=run_retrieval(c,manifest(c),runner,tmp_path,deadline=deadline(),clock=lambda:291)
    assert not outcome.complete and outcome.cleanup_required
    assert outcome.failure.category=="cleanup_allowance_reached" and not outcome.actions


def test_inventory_consuming_remaining_allowance_returns_pending_item_evidence(tmp_path):
    c=connection(tmp_path); payload=b"native"; now=[100.0]
    class SlowInventory:
        def __init__(self): self.requests=[]
        def __call__(self,request): return self.run(request)
        def run(self,request):
            self.requests.append(request)
            now[0]=290.0
            return result(request.action_id,f"{len(payload)} {hashlib.sha256(payload).hexdigest()}")
    runner=SlowInventory()
    outcome=run_retrieval(c,multi_manifest(c,payload),runner,tmp_path,deadline=deadline(),clock=lambda:now[0])
    assert not outcome.complete and outcome.cleanup_required
    assert len(outcome.actions)==1 and outcome.actions[0].action_type=="ssh"
    assert not outcome.attempts and len(outcome.records)==2
    assert outcome.records[0].failure.category=="cleanup_allowance_reached_before_transfer"
    assert outcome.records[1].failure.category=="not_attempted_after_stop"
    assert outcome.failure.category=="cleanup_allowance_reached_before_transfer"


def test_staging_that_consumes_copy_allowance_prevents_scp_and_records_exhaustion(tmp_path,monkeypatch):
    import cloud_network_benchmark.retrieval as retrieval
    c=connection(tmp_path); payload=b"native"; now=[100.0]; requests=[]
    class Clock:
        def __call__(self): return now[0]
    class Inventory:
        def run(self,request):
            requests.append(request)
            if request.action_type=="ssh":
                return result(request.action_id,f"{len(payload)} {hashlib.sha256(payload).hexdigest()}")
            Path(request.argv[-1]).write_bytes(b"partial")
            return result(request.action_id)
    real_mkdir=retrieval.Path.mkdir
    staging_calls=[0]
    def delayed_mkdir(path,*args,**kwargs):
        value=real_mkdir(path,*args,**kwargs)
        if path.name==".f02r-attempts":
            staging_calls[0]+=1
            now[0]=150.0 if staging_calls[0]==1 else 290.0
        return value
    monkeypatch.setattr(retrieval.Path,"mkdir",delayed_mkdir)
    outcome=run_retrieval(c,multi_manifest(c,payload),Inventory(),tmp_path,deadline=deadline(),clock=Clock())
    assert [request.action_type for request in requests]==["ssh","scp"]
    assert requests[1].timeout_seconds>0 and requests[1].timeout_seconds<=5
    assert len(outcome.attempts)==1 and outcome.attempts[0].size_bytes==len(b"partial") and not outcome.complete
    assert outcome.records[0].failure.category=="cleanup_allowance_reached_before_transfer"
    assert outcome.records[1].failure.category=="not_attempted_after_stop"
    assert (tmp_path/".f02r-attempts").is_dir()


def test_lost_known_hosts_during_inventory_keeps_request_failure_and_skips_scp(tmp_path):
    c=connection(tmp_path); payload=b"native"
    class LostIdentity:
        def __init__(self): self.requests=[]
        def run(self,request):
            self.requests.append(request)
            Path(c.vm_a.host_key_reference).unlink(missing_ok=True)
            return result(request.action_id,f"{len(payload)} {hashlib.sha256(payload).hexdigest()}")
    runner=LostIdentity()
    outcome=run_retrieval(c,manifest(c,payload),runner,tmp_path,deadline=deadline(),clock=lambda:100)
    assert not outcome.complete and len(outcome.actions)==2
    assert len(runner.requests)==1 and outcome.failure.category=="transport_preflight_failed"
    assert outcome.actions[-1].action_type=="scp"
    assert "request construction failed" in outcome.actions[-1].result["stderr"]
    assert outcome.records[0].failure.category=="transport_preflight_failed"


def test_remote_workspace_and_declaration_mismatches_are_rejected(tmp_path):
    c=connection(tmp_path); m=manifest(c)
    with pytest.raises(ValueError,match="under the deployment workspace"):
        RetrievalManifest.model_validate({**m.model_dump(),"items":[{**m.items[0].model_dump(),"source_path":"/tmp/escape.csv"}]})
    with pytest.raises(ValueError,match="does not match its declaration"):
        RetrievalManifest.model_validate({**m.model_dump(),"items":[{**m.items[0].model_dump(),"kind":"other-native"}]})


def test_retry_materializes_only_verified_bytes_and_preserves_attempts(tmp_path):
    c=connection(tmp_path); payload=b"native"; calls=[]
    class Retry:
        def run(self,request):
            calls.append(request)
            if request.action_type=="ssh": out=f"{len(payload)} {hashlib.sha256(payload).hexdigest()}"
            else:
                attempt=len([req for req in calls if req.action_type=="scp"])
                Path(request.argv[-1]).write_bytes(b"partial" if attempt==1 else payload); out=""
            return result(request.action_id,out)
    outcome=run_retrieval(c,manifest(c,payload),Retry(),tmp_path,deadline=deadline(),clock=lambda:100)
    assert outcome.complete and len(outcome.attempts)==2
    assert outcome.attempts[0].reason=="size_mismatch" and outcome.attempts[1].outcome=="verified"
    assert outcome.records[-1].retrieval_state=="verified"
    assert not outcome.attempts[0].staging_path==outcome.attempts[1].staging_path


def test_optional_remote_absence_is_explicit_without_fabricated_closed_record(tmp_path):
    c=connection(tmp_path); m=manifest(c)
    declaration=m.declarations[0].model_copy(update={"expectation":"optional"})
    unavailable={"sockperf-a":{"role":"vm_a","path":"/var/lib/cnb/work/a.csv","reason":"producer did not emit optional log"}}
    closed=RetrievalManifest(identity=m.identity,deployment_id=m.deployment_id,remote_workspace=m.remote_workspace,
        declarations=[declaration],items=[],unavailable_items=unavailable,
        measurement_ended_at=m.measurement_ended_at,producers_closed_at=m.producers_closed_at,
        closure_evidence=m.closure_evidence)
    outcome=run_retrieval(c,closed,lambda req: (_ for _ in ()).throw(AssertionError("unavailable artifact must not be copied")),tmp_path,deadline=deadline(),clock=lambda:100)
    assert outcome.complete and outcome.cleanup_required and outcome.failure is None
    assert outcome.records[0].retrieval_state=="not_retrieved" and outcome.records[0].size_bytes.value is None


def test_required_remote_absence_is_failed_evidence_without_fake_integrity(tmp_path):
    c=connection(tmp_path); m=manifest(c)
    unavailable={"sockperf-a":{"role":"vm_a","path":"/var/lib/cnb/work/a.csv","reason":"producer did not emit required output"}}
    closed=RetrievalManifest(identity=m.identity,deployment_id=m.deployment_id,remote_workspace=m.remote_workspace,
        declarations=m.declarations,items=[],unavailable_items=unavailable,
        measurement_ended_at=m.measurement_ended_at,producers_closed_at=m.producers_closed_at,
        closure_evidence=m.closure_evidence)
    outcome=run_retrieval(c,closed,lambda req: (_ for _ in ()).throw(AssertionError("unavailable artifact must not be copied")),tmp_path,deadline=deadline(),clock=lambda:100)
    assert not outcome.complete and outcome.failure.category=="required_artifact_unavailable"
    assert outcome.records[0].retrieval_state=="failed" and outcome.records[0].size_bytes.value is None


def test_duplicate_inventory_and_hostile_source_paths_fail_validation(tmp_path):
    c=connection(tmp_path); m=manifest(c); item=m.items[0].model_dump()
    with pytest.raises(ValueError,match="duplicate artifact IDs"):
        RetrievalManifest.model_validate({**m.model_dump(),"items":[item,item]})
    with pytest.raises(ValueError,match="duplicate artifact destinations"):
        RetrievalManifest.model_validate({**m.model_dump(),"items":[item,{**item,"artifact_id":"sockperf-b"}]})
    for unsafe in ("/var/lib/cnb/work/../escape", "/var/lib/cnb/work/a;touch"):
        with pytest.raises(ValueError):
            RetrievalItem.model_validate({**item,"artifact_id":"safe-id","source_path":unsafe})


def test_interruption_stops_retry_and_preserves_required_failure(tmp_path):
    c=connection(tmp_path); payload=b"native"
    from cloud_network_benchmark.contracts.common import CommandOutcome
    from cloud_network_benchmark.contracts.execution import CommandResult
    class Stop:
        def __init__(self): self.calls=[]
        def run(self,request):
            self.calls.append(request)
            if request.action_type=="ssh": return result(request.action_id,f"{len(payload)} {hashlib.sha256(payload).hexdigest()}")
            Path(request.argv[-1]).write_bytes(b"partial")
            now=datetime.now(timezone.utc)
            return CommandResult(action_id=request.action_id,outcome=CommandOutcome.INTERRUPTED,started_at=now,finished_at=now,duration_seconds=0,exit_code=None,stdout="partial",stderr="")
    runner=Stop(); outcome=run_retrieval(c,manifest(c,payload),runner,tmp_path,deadline=deadline(),clock=lambda:100)
    assert not outcome.complete and outcome.failure.category=="copy_interrupted"
    assert len([request for request in runner.calls if request.action_type=="scp"])==1
    assert len(outcome.attempts)==1 and outcome.attempts[0].outcome=="failed"
    assert outcome.attempts[0].size_bytes==len(b"partial") and outcome.records[-1].sha256.value==hashlib.sha256(b"partial").hexdigest()


def test_cap_consumed_during_local_hash_preserves_partial_integrity_without_publish(tmp_path,monkeypatch):
    import cloud_network_benchmark.retrieval as retrieval
    c=connection(tmp_path); payload=b"native"; now=[100.0]
    class Runner:
        def run(self,request):
            if request.action_type=="ssh": return result(request.action_id,f"{len(payload)} {hashlib.sha256(payload).hexdigest()}")
            Path(request.argv[-1]).write_bytes(payload); return result(request.action_id)
    original=retrieval._hash_file
    def slow_hash(path,**kwargs):
        value=original(path,**kwargs); now[0]=291.0; return value
    monkeypatch.setattr(retrieval,"_hash_file",slow_hash)
    outcome=retrieval.run_retrieval(c,manifest(c,payload),Runner(),tmp_path,deadline=deadline(),clock=lambda:now[0])
    assert not outcome.complete and outcome.failure.category=="cleanup_allowance_reached_during_local_verification"
    assert not (tmp_path/"measurements/a.csv").exists()
    assert outcome.records[0].retrieval_state=="failed" and outcome.records[0].size_bytes.value==len(payload)
    assert outcome.records[0].sha256.value==hashlib.sha256(payload).hexdigest()


def test_cap_consumed_during_final_materialized_verification_is_failure(tmp_path,monkeypatch):
    from cloud_network_benchmark.contracts.artifacts import ArtifactRecord
    c=connection(tmp_path); payload=b"native"; now=[100.0]
    class Runner:
        def run(self,request):
            if request.action_type=="ssh": return result(request.action_id,f"{len(payload)} {hashlib.sha256(payload).hexdigest()}")
            Path(request.argv[-1]).write_bytes(payload); return result(request.action_id)
    original=ArtifactRecord.verify_materialized_file
    def slow_verify(record,root,**kwargs):
        original(record,root,**kwargs); now[0]=291.0
    monkeypatch.setattr(ArtifactRecord,"verify_materialized_file",slow_verify)
    outcome=run_retrieval(c,manifest(c,payload),Runner(),tmp_path,deadline=deadline(),clock=lambda:now[0])
    assert not outcome.complete and outcome.failure.category=="cleanup_allowance_reached_during_publication_verification"
    assert outcome.records[0].retrieval_state=="failed" and outcome.records[0].size_bytes.value==len(payload)


def test_materialized_verification_stops_between_chunks_at_original_cap(tmp_path,monkeypatch):
    from cloud_network_benchmark.contracts.artifacts import ArtifactRecord
    import cloud_network_benchmark.retrieval as retrieval
    c=connection(tmp_path); payload=b"z"*(3*1024*1024); now=[100.0]
    class Runner:
        def run(self,request):
            if request.action_type=="ssh": return result(request.action_id,f"{len(payload)} {hashlib.sha256(payload).hexdigest()}")
            Path(request.argv[-1]).write_bytes(payload); return result(request.action_id)
    original=ArtifactRecord.verify_materialized_file
    def cap_during_stream(record,root,*,budget_check=None):
        def check(size,digest):
            if size>=1024*1024:
                now[0]=291.0
            if budget_check is not None:
                budget_check(size,digest)
        return original(record,root,budget_check=check)
    monkeypatch.setattr(ArtifactRecord,"verify_materialized_file",cap_during_stream)
    outcome=retrieval.run_retrieval(c,manifest(c,payload),Runner(),tmp_path,deadline=deadline(),clock=lambda:now[0])
    assert not outcome.complete and outcome.failure.category=="cleanup_allowance_reached_during_publication_verification"
    assert outcome.records[0].retrieval_state=="failed"
    assert outcome.records[0].size_bytes.value==len(payload)
    assert outcome.records[0].sha256.value==hashlib.sha256(payload).hexdigest()
    assert outcome.records[0].failure.details["verified_prefix_bytes"]==1024*1024
    assert outcome.records[0].failure.details["verified_prefix_sha256"]==hashlib.sha256(payload[:1024*1024]).hexdigest()


def test_one_vm_loss_keeps_previously_verified_native_artifacts(tmp_path):
    c=connection(tmp_path); now=datetime.now(timezone.utc); a=b"client"; b=b"server"
    declarations=[ArtifactInput(artifact_id="sockperf-a",path="measurements/a.csv",kind="sockperf-csv",producer="vm_a",unit_id="unit-1",expectation="required"),
        ArtifactInput(artifact_id="iperf3-b",path="measurements/b.json",kind="iperf3-json",producer="vm_b",unit_id="unit-1",expectation="required")]
    items=[RetrievalItem(artifact_id="sockperf-a",deployment_id=c.deployment_id,source_vm=c.vm_a.host,source_role="vm_a",source_path="/var/lib/cnb/work/a.csv",destination_path="measurements/a.csv",kind="sockperf-csv",unit_id="unit-1",expected_size_bytes=len(a),expected_sha256=hashlib.sha256(a).hexdigest(),timeout_seconds=5),
        RetrievalItem(artifact_id="iperf3-b",deployment_id=c.deployment_id,source_vm=c.vm_b.host,source_role="vm_b",source_path="/var/lib/cnb/work/b.json",destination_path="measurements/b.json",kind="iperf3-json",unit_id="unit-1",expected_size_bytes=len(b),expected_sha256=hashlib.sha256(b).hexdigest(),timeout_seconds=5)]
    m=RetrievalManifest(identity=c.identity.model_dump(),deployment_id=c.deployment_id,remote_workspace="/var/lib/cnb/work",declarations=declarations,items=items,measurement_ended_at=now,producers_closed_at=now,closure_evidence="both producers closed")
    class Loss:
        def run(self,request):
            aid=request.action_id
            if "sockperf-a" in aid: payload=a
            else: payload=b
            if request.action_type=="ssh": return result(aid,f"{len(payload)} {hashlib.sha256(payload).hexdigest()}")
            if "iperf3-b" in aid: return result(aid,success=False,stderr="VM B unavailable")
            Path(request.argv[-1]).write_bytes(payload); return result(aid)
    outcome=run_retrieval(c,m,Loss(),tmp_path,deadline=deadline(),clock=lambda:100)
    assert not outcome.complete and outcome.retrieved==["sockperf-a"]
    assert (tmp_path/"measurements/a.csv").read_bytes()==a
    assert [record.retrieval_state for record in outcome.records]==["verified","failed"]
    assert len(outcome.attempts)==3


def test_generated_inventory_shell_observes_real_closed_file_bytes(tmp_path):
    source=tmp_path/"native.csv"; payload=b"timestamp,value\n1,2\n"; source.write_bytes(payload)
    command=subprocess.run(["sh","-c",_inventory_script(str(source))],text=True,capture_output=True,check=True)
    assert command.stdout.split()==[str(len(payload)),hashlib.sha256(payload).hexdigest()]
    missing=subprocess.run(["sh","-c",_inventory_script(str(tmp_path/"missing.csv"))],text=True,capture_output=True)
    assert missing.returncode != 0 and not missing.stdout


def test_required_dynamic_declaration_needs_every_resolved_chunk(tmp_path):
    c=connection(tmp_path); now=datetime.now(timezone.utc); first=b"chunk-one"; second=b"chunk-two"
    declaration=ArtifactInput(artifact_id="day-probe-csv",path_template="measurements/day/{chunk_id}.csv",kind="sockperf-csv",producer="vm_a",unit_id=None,expectation="required")
    items=[RetrievalItem(artifact_id=f"day-probe-csv-unit-{index}",deployment_id=c.deployment_id,source_vm=c.vm_a.host,source_role="vm_a",source_path=f"/var/lib/cnb/work/chunk-{index}.csv",destination_path=f"measurements/day/unit-{index}.csv",kind="sockperf-csv",unit_id=f"unit-{index}",expected_size_bytes=len(payload),expected_sha256=hashlib.sha256(payload).hexdigest(),timeout_seconds=5) for index,payload in ((1,first),(2,second))]
    m=RetrievalManifest(identity=c.identity.model_dump(),deployment_id=c.deployment_id,remote_workspace="/var/lib/cnb/work",declarations=[declaration],items=items,measurement_ended_at=now,producers_closed_at=now,closure_evidence="both probe chunks closed")
    class Mixed:
        def run(self,request):
            if request.action_type=="ssh":
                payload=first if "unit-1" in request.action_id else second
                return result(request.action_id,f"{len(payload)} {hashlib.sha256(payload).hexdigest()}")
            if "unit-2" in request.action_id: return result(request.action_id,success=False,stderr="source unavailable")
            Path(request.argv[-1]).write_bytes(first); return result(request.action_id)
    outcome=run_retrieval(c,m,Mixed(),tmp_path,deadline=deadline(),clock=lambda:100)
    assert not outcome.complete and outcome.retrieved==["day-probe-csv-unit-1"]
    assert [record.retrieval_state for record in outcome.records]==["verified","failed"]


def test_interrupted_inventory_stops_remote_commands_and_accounts_for_later_item(tmp_path):
    c=connection(tmp_path); base=manifest(c); first=base.items[0]; now=base.measurement_ended_at
    second=first.model_copy(update={"artifact_id":"sockperf-b","source_role":"vm_b","source_vm":c.vm_b.host,"source_path":"/var/lib/cnb/work/b.csv","destination_path":"measurements/b.csv"})
    declarations=[base.declarations[0],base.declarations[0].model_copy(update={"artifact_id":"sockperf-b","path":"measurements/b.csv","producer":"vm_b"})]
    m=RetrievalManifest(identity=c.identity.model_dump(),deployment_id=c.deployment_id,remote_workspace=base.remote_workspace,declarations=declarations,items=[first,second],measurement_ended_at=now,producers_closed_at=now,closure_evidence="closed")
    from cloud_network_benchmark.contracts.common import CommandOutcome
    from cloud_network_benchmark.contracts.execution import CommandResult
    class Interrupted:
        def __init__(self): self.calls=[]
        def run(self,request):
            self.calls.append(request); stamp=datetime.now(timezone.utc)
            return CommandResult(action_id=request.action_id,outcome=CommandOutcome.INTERRUPTED,started_at=stamp,finished_at=stamp,duration_seconds=0,exit_code=None,stdout="partial",stderr="")
    runner=Interrupted(); outcome=run_retrieval(c,m,runner,tmp_path,deadline=deadline(),clock=lambda:100)
    assert not outcome.complete and len(runner.calls)==1 and runner.calls[0].action_type=="ssh"
    assert [record.retrieval_state for record in outcome.records]==["failed","failed"]


def test_identity_file_loss_between_artifacts_preserves_first_verified_export(tmp_path):
    c=connection(tmp_path); base=manifest(c,b"first"); first=base.items[0]; second_payload=b"second"
    second=RetrievalItem(artifact_id="iperf3-b",deployment_id=c.deployment_id,source_vm=c.vm_b.host,source_role="vm_b",source_path="/var/lib/cnb/work/b.json",destination_path="measurements/b.json",kind="iperf3-json",unit_id="unit-2",expected_size_bytes=len(second_payload),expected_sha256=hashlib.sha256(second_payload).hexdigest(),timeout_seconds=5)
    declarations=[base.declarations[0],ArtifactInput(artifact_id="iperf3-b",path="measurements/b.json",kind="iperf3-json",producer="vm_b",unit_id="unit-2",expectation="required")]
    m=RetrievalManifest(identity=c.identity.model_dump(),deployment_id=c.deployment_id,remote_workspace=base.remote_workspace,declarations=declarations,items=[first,second],measurement_ended_at=base.measurement_ended_at,producers_closed_at=base.producers_closed_at,closure_evidence="both closed")
    class LostKey:
        def run(self,request):
            payload=b"first" if "sockperf-a" in request.action_id else second_payload
            if request.action_type=="ssh": return result(request.action_id,f"{len(payload)} {hashlib.sha256(payload).hexdigest()}")
            Path(request.argv[-1]).write_bytes(payload)
            Path(c.vm_a.host_key_reference).unlink(missing_ok=True)
            return result(request.action_id)
    outcome=run_retrieval(c,m,LostKey(),tmp_path,deadline=deadline(),clock=lambda:100)
    assert not outcome.complete and outcome.failure.category=="transport_preflight_failed"
    assert outcome.retrieved==["sockperf-a"] and (tmp_path/"measurements/a.csv").read_bytes()==b"first"
    assert [record.retrieval_state for record in outcome.records]==["verified","failed"]


def test_large_native_file_hash_stops_between_stream_chunks_at_cleanup_cap(tmp_path):
    c=connection(tmp_path); payload=b"x"*(3*1024*1024)
    class Clock:
        value=100.0
        def __call__(self): return self.value
    clock=Clock()
    class Runner:
        def run(self,request):
            if request.action_type=="ssh": return result(request.action_id,f"{len(payload)} {hashlib.sha256(payload).hexdigest()}")
            Path(request.argv[-1]).write_bytes(payload); clock.value=291.0; return result(request.action_id)
    outcome=run_retrieval(c,manifest(c,payload),Runner(),tmp_path,deadline=deadline(),clock=clock)
    assert not outcome.complete and outcome.failure.category=="cleanup_allowance_reached_during_local_verification"
    assert outcome.attempts[0].size_bytes==1024*1024
    assert outcome.attempts[0].sha256==hashlib.sha256(payload[:1024*1024]).hexdigest()
    assert not (tmp_path/"measurements/a.csv").exists()


def test_delayed_destination_directory_setup_prevents_publication(tmp_path,monkeypatch):
    c=connection(tmp_path); payload=b"native"; now=[100.0]
    class Runner:
        def run(self,request):
            if request.action_type=="ssh": return result(request.action_id,f"{len(payload)} {hashlib.sha256(payload).hexdigest()}")
            Path(request.argv[-1]).write_bytes(payload); return result(request.action_id)
    original=Path.mkdir
    def slow_mkdir(path,*args,**kwargs):
        output=original(path,*args,**kwargs)
        if path.name=="measurements": now[0]=291.0
        return output
    monkeypatch.setattr(Path,"mkdir",slow_mkdir)
    outcome=run_retrieval(c,manifest(c,payload),Runner(),tmp_path,deadline=deadline(),clock=lambda:now[0])
    assert not outcome.complete and outcome.failure.category=="cleanup_allowance_reached_before_publication"
    assert not (tmp_path/"measurements/a.csv").exists()


def test_full_identity_and_late_destination_collisions_preflight_before_copy(tmp_path):
    c=connection(tmp_path); m=manifest(c)
    with pytest.raises(ValueError,match="identity"):
        run_retrieval(c,m.model_copy(update={"identity":{**m.identity,"provider":"azure"}}),lambda req: (_ for _ in ()).throw(AssertionError("copy started")),tmp_path,deadline=deadline(),clock=lambda:100)


def test_later_destination_collision_prevents_every_copy(tmp_path):
    c=connection(tmp_path); base=manifest(c); first=base.items[0]
    second=first.model_copy(update={"artifact_id":"sockperf-b","source_role":"vm_b","source_vm":c.vm_b.host,"source_path":"/var/lib/cnb/work/b.csv","destination_path":"measurements/b.csv"})
    declarations=[base.declarations[0],base.declarations[0].model_copy(update={"artifact_id":"sockperf-b","path":"measurements/b.csv","producer":"vm_b"})]
    m=RetrievalManifest(identity=c.identity.model_dump(),deployment_id=c.deployment_id,remote_workspace=base.remote_workspace,declarations=declarations,items=[first,second],measurement_ended_at=base.measurement_ended_at,producers_closed_at=base.producers_closed_at,closure_evidence="closed")
    (tmp_path/"measurements").mkdir(); (tmp_path/"measurements/b.csv").write_bytes(b"preexisting")
    outcome=run_retrieval(c,m,lambda req: (_ for _ in ()).throw(AssertionError("whole inventory must preflight before copy")),tmp_path,deadline=deadline(),clock=lambda:100)
    assert not outcome.complete and not outcome.actions and [r.retrieval_state for r in outcome.records]==["failed","failed"]


def test_inventory_runner_exception_returns_failure_evidence(tmp_path):
    c=connection(tmp_path); outcome=run_retrieval(c,manifest(c),lambda req: (_ for _ in ()).throw(OSError("synthetic")),tmp_path,deadline=deadline(),clock=lambda:100)
    assert not outcome.complete and outcome.failure.category=="inventory_command_error"
    assert outcome.actions and outcome.records[0].retrieval_state=="failed"


def test_budget_exhaustion_accounts_for_all_remaining_items(tmp_path):
    c=connection(tmp_path); base=manifest(c); first=base.items[0]
    second=first.model_copy(update={"artifact_id":"sockperf-b","source_role":"vm_b","source_vm":c.vm_b.host,"source_path":"/var/lib/cnb/work/b.csv","destination_path":"measurements/b.csv"})
    declarations=[base.declarations[0],base.declarations[0].model_copy(update={"artifact_id":"sockperf-b","path":"measurements/b.csv","producer":"vm_b"})]
    m=RetrievalManifest(identity=c.identity.model_dump(),deployment_id=c.deployment_id,remote_workspace=base.remote_workspace,declarations=declarations,items=[first,second],measurement_ended_at=base.measurement_ended_at,producers_closed_at=base.producers_closed_at,closure_evidence="closed")
    outcome=run_retrieval(c,m,lambda req: (_ for _ in ()).throw(AssertionError("zero budget must prevent command")),tmp_path,deadline=deadline(),clock=lambda:291)
    assert not outcome.complete and len(outcome.records)==2 and all(r.retrieval_state=="failed" for r in outcome.records)
