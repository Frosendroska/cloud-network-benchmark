from datetime import datetime,timezone
import hashlib
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]))
from fixtures.f02r.support import connection,deadline,result
from cloud_network_benchmark.retrieval import RetrievalItem,RetrievalManifest,run_retrieval
from cloud_network_benchmark.contracts.artifacts import ArtifactInput


def manifest(c,payload=b"native"):
    now=datetime.now(timezone.utc)
    declaration=ArtifactInput(artifact_id="sockperf-a",path="measurements/a.csv",kind="sockperf-csv",producer="vm_a",unit_id="unit-1",expectation="required")
    item=RetrievalItem(artifact_id="sockperf-a",deployment_id=c.deployment_id,source_vm=c.vm_a.host,source_role="vm_a",source_path="/var/lib/cnb/work/a.csv",destination_path="measurements/a.csv",kind="sockperf-csv",unit_id="unit-1",expected_size_bytes=len(payload),expected_sha256=hashlib.sha256(payload).hexdigest(),timeout_seconds=5)
    return RetrievalManifest(identity=c.identity.model_dump(),deployment_id=c.deployment_id,remote_workspace="/var/lib/cnb/work",declarations=[declaration],items=[item],measurement_ended_at=now,producers_closed_at=now,closure_evidence="producer flush observed")


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
            now=datetime.now(timezone.utc)
            return CommandResult(action_id=request.action_id,outcome=CommandOutcome.INTERRUPTED,started_at=now,finished_at=now,duration_seconds=0,exit_code=None,stdout="partial",stderr="")
    runner=Stop(); outcome=run_retrieval(c,manifest(c,payload),runner,tmp_path,deadline=deadline(),clock=lambda:100)
    assert not outcome.complete and outcome.failure.category=="copy_interrupted"
    assert len([request for request in runner.calls if request.action_type=="scp"])==1
    assert len(outcome.attempts)==1 and outcome.attempts[0].outcome=="failed"


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
