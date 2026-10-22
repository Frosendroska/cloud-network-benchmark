from datetime import datetime,timezone
import hashlib,json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parents[1]))
from fixtures.f02r.support import BootstrapRunner,bootstrap_config,connection,deadline,result,readiness_output
from cloud_network_benchmark.bootstrap import run_bootstrap
from cloud_network_benchmark.readiness import ReadinessConfig,run_readiness
from cloud_network_benchmark.retrieval import RetrievalItem,RetrievalManifest,run_retrieval
from cloud_network_benchmark.contracts.artifacts import ArtifactInput, ArtifactLayout
from cloud_network_benchmark.contracts.common import BenchmarkPhase, Evidence, StringEvidence


def _prepared(c):
    return run_bootstrap(c,bootstrap_config(),BootstrapRunner(),deadline=deadline(),clock=lambda:100)


def _manifest(c, mode, payload, chunk=False):
    now=datetime.now(timezone.utc)
    aid=f"{mode}-native"; path=f"measurements/{mode}/native.bin"
    declarations=[ArtifactInput(artifact_id=aid,path=path,kind="sockperf-csv" if mode!="diagnostic_day" else "sockperf-log",producer="vm_a",unit_id=f"{mode}-unit",expectation="required")]
    items=[RetrievalItem(artifact_id=aid,deployment_id=c.deployment_id,source_vm=c.vm_a.host,source_role="vm_a",source_path=f"/var/lib/cnb/{mode}.bin",destination_path=path,kind=declarations[0].kind,unit_id=f"{mode}-unit",expected_size_bytes=len(payload),expected_sha256=hashlib.sha256(payload).hexdigest(),timeout_seconds=5)]
    if chunk:
        declarations.append(ArtifactInput(artifact_id="day-probe-csv",path_template="measurements/day/probes/{chunk_id}.csv",kind="sockperf-csv",producer="vm_a",unit_id=None,expectation="required"))
        part=b"chunk"
        items.append(RetrievalItem(artifact_id="day-probe-csv-chunk-001",deployment_id=c.deployment_id,source_vm=c.vm_a.host,source_role="vm_a",source_path="/var/lib/cnb/chunk-001.csv",destination_path="measurements/day/probes/chunk-001.csv",kind="sockperf-csv",unit_id="chunk-001",expected_size_bytes=len(part),expected_sha256=hashlib.sha256(part).hexdigest(),timeout_seconds=5))
    return RetrievalManifest(identity=c.identity.model_dump(),deployment_id=c.deployment_id,remote_workspace="/var/lib/cnb",declarations=declarations,items=items,measurement_ended_at=now,producers_closed_at=now,closure_evidence=f"{mode} producers closed and flushed")


def test_all_v2_modes_prepare_observe_and_retrieve_closed_native_bytes(tmp_path):
    modes=("acceptance","calibration","short_study","diagnostic_day")
    for mode in modes:
        root=tmp_path/mode; root.mkdir()
        c=connection(root); prepared=_prepared(c)
        class ReadyRunner:
            def run(self,request):
                aid=request.action_id
                return result(aid,readiness_output(request,prepared))
        ready=run_readiness(c,ReadinessConfig(poll_interval_seconds=.01,attempt_ceiling=1),ReadyRunner(),prepared=prepared,deadline=deadline(),clock=lambda:100)
        assert ready.ready, mode
        payload=f"{mode}-native".encode(); manifest=_manifest(c,mode,payload)
        class TransferRunner:
            def run(self,request):
                if request.action_type=="ssh": out=f"{len(payload)} {hashlib.sha256(payload).hexdigest()}"
                else: Path(request.argv[-1]).write_bytes(payload); out=""
                return result(request.action_id,out)
        retrieved=run_retrieval(c,manifest,TransferRunner(),root,deadline=deadline(),clock=lambda:100)
        assert retrieved.complete and retrieved.cleanup_required, mode
        retrieved.records[0].verify_materialized_file(root)


def test_retained_pair_final_export_resolves_closed_probe_chunks(tmp_path):
    root=tmp_path/"day"; root.mkdir(); c=connection(root); payload=b"day client"; chunk=b"chunk"
    m=_manifest(c,"diagnostic_day",payload,chunk=True)
    class Runner:
        def run(self,request):
            if request.action_type=="ssh":
                value=payload if request.action_id.endswith("diagnostic_day-native") else chunk
                out=f"{len(value)} {hashlib.sha256(value).hexdigest()}"
            else:
                value=payload if request.action_id.endswith("diagnostic_day-native-1") else chunk
                Path(request.argv[-1]).write_bytes(value); out=""
            return result(request.action_id,out)
    outcome=run_retrieval(c,m,Runner(),root,deadline=deadline(),clock=lambda:100)
    assert outcome.complete and set(outcome.retrieved)=={"diagnostic_day-native","day-probe-csv-chunk-001"}


def test_caller_cap_order_is_evidence_and_f02r_only_consumes_deadlines(tmp_path):
    from datetime import timedelta
    from cloud_network_benchmark.contracts.admission import ReservationEvidence, ExpiryEvidence
    from cloud_network_benchmark.contracts.common import StructuredEvidence
    cap=datetime.now(timezone.utc); registered=cap+timedelta(seconds=1); created=registered+timedelta(seconds=1)
    reservation=ReservationEvidence(schema_version=2,campaign_id="campaign",reservation_id=Evidence(value="reservation"),state="reserved",reserved_cost_usd=Evidence(value=0.1),cap_started_at=Evidence(value=cap),expires_at=Evidence(value=created+timedelta(seconds=60)),cap_start_policy_id=StringEvidence(value="policy"),source_path="local-register.json",source_sha256="a"*64)
    expiry=ExpiryEvidence(schema_version=2,run_id="run-aws",deployment_id="run-aws-deployment",provider="aws",action="stop",state="registered",registration_id=Evidence(value="expiry"),registered_at=Evidence(value=registered),expires_at=Evidence(value=created+timedelta(seconds=60)),target_selector="deployment-tag",provider_metadata=StructuredEvidence(value={"observed":True}))
    assert reservation.cap_started_at.value<=expiry.registered_at.value<=created<expiry.expires_at.value
    assert "expiry" not in run_bootstrap.__code__.co_names


def test_frozen_f01r_layouts_cover_both_roles_and_retained_pair_chunks():
    from types import SimpleNamespace
    phases=[BenchmarkPhase.IDLE_LATENCY,BenchmarkPhase.SINGLE_FLOW,BenchmarkPhase.MULTI_FLOW]
    cases=(("acceptance",phases),("calibration",phases),("short_study",phases),("diagnostic_day",[BenchmarkPhase.MULTI_FLOW]))
    for mode, selected in cases:
        units=[SimpleNamespace(unit_id=f"{mode}-{index}",phase=phase,kind="phase",parent_unit_id=None) for index,phase in enumerate(selected)]
        layout=ArtifactLayout.for_mode(mode,units)
        declarations=layout.declarations_for_mode(mode,units)
        producers={item.producer.value if hasattr(item.producer,"value") else item.producer for item in declarations}
        assert {"vm_a","vm_b"}<=producers
        if mode=="diagnostic_day":
            chunks=[SimpleNamespace(unit_id=f"chunk-{index}",phase=BenchmarkPhase.MULTI_FLOW,kind="probe_chunk",parent_unit_id=units[0].unit_id) for index in (1,2)]
            extended=layout.declarations_for_mode(mode,units+chunks)
            assert sum(item.artifact_id=="day-probe-csv" for item in extended)==1
            assert layout.day_probe_csv_template.count("{chunk_id}")==1
            assert all(chunk.parent_unit_id==units[0].unit_id for chunk in chunks)


def test_frozen_layouts_compose_through_prepare_readiness_and_retrieval(tmp_path):
    from datetime import timedelta
    from types import SimpleNamespace
    from cloud_network_benchmark.access import RemoteDeadline
    from cloud_network_benchmark.contracts.admission import ReservationEvidence, ExpiryEvidence
    from cloud_network_benchmark.contracts.common import BenchmarkPhase, Evidence, StringEvidence, StructuredEvidence
    phases=[BenchmarkPhase.IDLE_LATENCY,BenchmarkPhase.SINGLE_FLOW,BenchmarkPhase.MULTI_FLOW]
    cases=(("acceptance",phases),("calibration",phases),("short_study",phases),("diagnostic_day",[BenchmarkPhase.MULTI_FLOW]))
    for mode, selected in cases:
        root=tmp_path/mode; root.mkdir(); c=connection(root)
        units=[SimpleNamespace(unit_id=f"{mode}-{index}",phase=phase,kind="phase",parent_unit_id=None) for index,phase in enumerate(selected)]
        layout=ArtifactLayout.for_mode(mode,units)
        declarations=layout.declarations_for_mode(mode,units)
        if mode=="diagnostic_day":
            chunks=[SimpleNamespace(unit_id=f"{mode}-chunk-{index}",phase=BenchmarkPhase.MULTI_FLOW,kind="probe_chunk",parent_unit_id=units[0].unit_id) for index in (1,2)]
            declarations=layout.declarations_for_mode(mode,units+chunks)
        items=[]; payloads={}; unavailable={}
        for declaration in declarations:
            if declaration.producer=="controller" or declaration.expectation!="required": continue
            role=declaration.producer.value if hasattr(declaration.producer,"value") else declaration.producer
            if mode=="acceptance" and role=="vm_b" and declaration.artifact_id=="vm_b_metadata":
                unavailable[declaration.artifact_id]={"role":"vm_b","path":"/var/lib/cnb/acceptance/metadata/vm_b.json","reason":"VM B metadata was not produced"}
                continue
            paths=([declaration.path] if declaration.path else [declaration.path_template.replace("{chunk_id}",f"{mode}-chunk-{index}") for index in (1,2)])
            for index,path in enumerate(paths):
                chunk_id=f"{mode}-chunk-{index+1}"
                artifact_id=declaration.artifact_id if declaration.path else f"{declaration.artifact_id}-{chunk_id}"
                payload=f"{mode}:{artifact_id}".encode(); payloads[artifact_id]=payload
                items.append(RetrievalItem(artifact_id=artifact_id,deployment_id=c.deployment_id,
                    source_vm=c.vm_a.host if role=="vm_a" else c.vm_b.host,source_role=role,
                    source_path=f"/var/lib/cnb/{mode}/{artifact_id}.native",destination_path=path,
                    kind=declaration.kind,unit_id=declaration.unit_id if declaration.path else chunk_id,expected_size_bytes=len(payload),
                    expected_sha256=hashlib.sha256(payload).hexdigest(),timeout_seconds=5))
        now=datetime.now(timezone.utc); registered=now+timedelta(seconds=1); expires=registered+timedelta(seconds=600)
        reservation=ReservationEvidence(schema_version=2,campaign_id="campaign",reservation_id=Evidence(value=f"res-{mode}"),state="reserved",reserved_cost_usd=Evidence(value=1.0),cap_started_at=Evidence(value=now),expires_at=Evidence(value=expires),cap_start_policy_id=StringEvidence(value="caller-policy"),source_path="caller-register.json",source_sha256="a"*64)
        expiry=ExpiryEvidence(schema_version=2,run_id=c.run_id,deployment_id=c.deployment_id,provider=c.provider,action="stop",state="registered",registration_id=Evidence(value=f"exp-{mode}"),registered_at=Evidence(value=registered),expires_at=Evidence(value=expires),target_selector="deployment",provider_metadata=StructuredEvidence(value={"caller":"fixture"}))
        bound=RemoteDeadline(readiness_deadline_monotonic=180,overall_deadline_monotonic=300,cleanup_reserve_seconds=10,cap_started_at=reservation.cap_started_at.value,cap_expires_at=expiry.expires_at.value)
        manifest=RetrievalManifest(identity=c.identity.model_dump(),deployment_id=c.deployment_id,remote_workspace="/var/lib/cnb",declarations=declarations,items=items,unavailable_items=unavailable,measurement_ended_at=now,producers_closed_at=now,closure_evidence=f"{mode} producers flushed")
        class Clock:
            value=100.0
            def __call__(self):
                current=self.value; self.value+=.001; return current
            def wait(self,seconds): self.value+=seconds
        clock=Clock()
        prepared=run_bootstrap(c,bootstrap_config(),BootstrapRunner(),deadline=bound,clock=clock)
        class Ready:
            def run(self,request): return result(request.action_id,readiness_output(request,prepared))
        ready=run_readiness(c,ReadinessConfig(poll_interval_seconds=.01,attempt_ceiling=1),Ready(),prepared=prepared,deadline=bound,clock=clock)
        assert ready.ready, mode
        class Transfer:
            def run(self,request):
                artifact=next(key for key in payloads if key in request.action_id)
                payload=payloads[artifact]
                if request.action_type=="ssh": out=f"{len(payload)} {hashlib.sha256(payload).hexdigest()}"
                elif mode=="diagnostic_day" and "day-probe-csv-diagnostic_day-chunk-2" in artifact:
                    return result(request.action_id,success=False,stderr="later probe chunk unavailable")
                else: Path(request.argv[-1]).write_bytes(payload); out=""
                return result(request.action_id,out)
        retrieved=run_retrieval(c,manifest,Transfer(),root,deadline=bound,clock=clock)
        assert clock.value>100.0, "preparation/readiness/retrieval must consume the caller's shared monotonic budget"
        if mode=="acceptance":
            assert not retrieved.complete and retrieved.failure.category=="required_artifact_unavailable"
        elif mode=="diagnostic_day":
            assert not retrieved.complete and any("day-probe-csv-diagnostic_day-chunk-1" in item for item in retrieved.retrieved)
        else:
            assert retrieved.complete and len(retrieved.retrieved)==len(items), mode
        assert {item.source_role for item in items}=={"vm_a","vm_b"}
        if mode=="diagnostic_day": assert len([item for item in items if item.artifact_id.startswith("day-probe-csv-")])==2


def test_retrieval_failure_stays_secondary_to_caller_owned_measurement_failure(tmp_path):
    from cloud_network_benchmark.contracts.common import FailureEvidence
    root=tmp_path/"partial"; root.mkdir(); c=connection(root); payload=b"native"
    primary=FailureEvidence(category="measurement_failed",message="Sockperf failed",occurred_at=datetime.now(timezone.utc))
    class LostSource:
        def run(self,request):
            if request.action_type=="ssh": return result(request.action_id,f"{len(payload)} {hashlib.sha256(payload).hexdigest()}")
            return result(request.action_id,success=False,stderr="synthetic source unavailable")
    secondary=run_retrieval(c,_manifest(c,"short_study",payload),LostSource(),root,deadline=deadline(),clock=lambda:100)
    assert primary.category=="measurement_failed"
    assert not secondary.complete and secondary.cleanup_required and secondary.failure.category=="copy_failed"
