import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cloud_network_benchmark.diagnostics_guest import collect_local
from cloud_network_benchmark.diagnostics import load_collection_result
from cloud_network_benchmark.contracts.measurements import VmMetadata
from cloud_network_benchmark.contracts.common import Evidence, StringEvidence, StructuredEvidence


def seed(role="vm_a", provider="aws"):
    return VmMetadata(schema_version=2,deployment_id="RUN-deployment",vm_role=role,provider=provider,
        cpu_model=StringEvidence(unavailable_reason="pending"),nic_driver=StringEvidence(unavailable_reason="pending"),
        acceleration=StringEvidence(unavailable_reason="pending"),image=StringEvidence(value="img-fixture"),
        kernel=StringEvidence(unavailable_reason="pending"),congestion_control=StringEvidence(unavailable_reason="pending"),
        tool_versions=StructuredEvidence(unavailable_reason="pending"),source_builds=StructuredEvidence(unavailable_reason="not supplied"),
        documented_network_limit=StructuredEvidence(value={"source":"fixture docs","limit":"provider-specific"}),
        mtu=Evidence[int](unavailable_reason="pending"),observed_region=StringEvidence(value="region-fixture"),
        observed_zone=StringEvidence(value="zone-fixture"),physical_zone_id=StringEvidence(unavailable_reason="not exposed"),
        actual_shape=StringEvidence(value="shape-fixture")).model_dump(mode="json")


class FakeClock:
    def __init__(self): self.now=0.0
    def monotonic(self): return self.now
    def utc_now(self): return datetime(2026,10,22,tzinfo=timezone.utc)+timedelta(seconds=self.now)
    def wait_until(self, deadline): self.now=max(self.now,deadline)


def snapshot(i):
    cpu=[100+i*10,20,40,800+i*10,5,10,5,2]
    return {"cpu":{"cpu":cpu,"cpu0":cpu,"cpu1":cpu},"tcp":4+i,
        "nic":dict(rx_dropped=0,tx_dropped=0,rx_errors=0,tx_errors=0,rx_bytes=i*100,tx_bytes=i*200)}


def test_fake_day_streams_actual_rows_and_adapter_validates(tmp_path):
    out=tmp_path/"out"; out.mkdir()
    calls=[]
    def command(argv, timeout):
        calls.append((argv,timeout)); return {"returncode":1,"stdout":"","stderr":"chronyc unavailable"}
    snapshots=(snapshot(i) for i in range(86401))
    fixture_root=Path(__file__).parents[1]/"fixtures/f04"
    context={"deployment_id":"RUN-deployment","vm_role":"vm_a","provider":"aws","interface":"eth0",
        "output_root":str(out),"duration_seconds":86400,"metadata_seed":seed()}
    outcome=collect_local(context,root=fixture_root,clock=FakeClock(),run_command=command,stop_requested=lambda:False,
                          snapshot_reader=lambda interface:next(snapshots))
    assert outcome["state"] == "completed", outcome
    assert outcome["sample_count"] == 86400
    assert outcome["mapping_count"] == 86401
    assert all(timeout <= .2 and timeout > 0 for _,timeout in calls)
    loaded=load_collection_result(context,outcome)
    assert loaded.state == "completed" and not loaded.failures
    assert len(loaded.artifacts) == 3
    assert (out/"diagnostics/vm_a.jsonl").read_text().count("\n") == 86400


def test_existing_output_is_not_overwritten_and_symlink_rejected(tmp_path):
    out=tmp_path/"out"; (out/"metadata").mkdir(parents=True); (out/"metadata/vm_a.json").write_text("keep")
    context={"deployment_id":"RUN-deployment","vm_role":"vm_a","provider":"aws","interface":"eth0","output_root":str(out),"duration_seconds":1,"metadata_seed":seed()}
    result=collect_local(context,root=Path(__file__).parents[1]/"fixtures/f04",clock=FakeClock(),run_command=lambda *_:{"returncode":1,"stdout":"","stderr":""},snapshot_reader=lambda *_:snapshot(0))
    assert result["state"] == "failed"
    assert (out/"metadata/vm_a.json").read_text() == "keep"


def test_delayed_ticks_keep_actual_interval_without_catchup(tmp_path):
    class Delayed(FakeClock):
        def __init__(self): super().__init__(); self.targets=[]
        def wait_until(self,target):
            self.targets.append(target)
            self.now=max(self.now,target+(2.5 if len(self.targets)==1 else 0))
    out=tmp_path/"delayed"; out.mkdir(); clock=Delayed(); count=[0]
    def read(_):
        value=snapshot(count[0]); count[0]+=1; return value
    context={"deployment_id":"RUN-deployment","vm_role":"vm_b","provider":"gcp","interface":"eth0","output_root":str(out),"duration_seconds":5,"metadata_seed":seed("vm_b","gcp")}
    outcome=collect_local(context,root=Path(__file__).parents[1]/"fixtures/f04",clock=clock,run_command=lambda *_:{"returncode":1,"stdout":"","stderr":"missing"},snapshot_reader=read)
    assert outcome["state"] == "completed"
    assert clock.targets == [1.0,4.0,5.0]
    rows=[json.loads(line) for line in (out/"diagnostics/vm_b.jsonl").read_text().splitlines()]
    assert rows[0]["interval_seconds"] == 3.5
    assert "tick opportunities omitted" in rows[0]["gap_reason"]
    assert len(rows) == 3


def test_graceful_early_stop_closes_all_partial_artifacts(tmp_path):
    out=tmp_path/"stop"; out.mkdir(); calls=[0]
    def stop(): calls[0]+=1; return calls[0] > 1
    context={"deployment_id":"RUN-deployment","vm_role":"vm_a","provider":"azure","interface":"eth0","output_root":str(out),"duration_seconds":10,"metadata_seed":seed("vm_a","azure")}
    result=collect_local(context,root=Path(__file__).parents[1]/"fixtures/f04",clock=FakeClock(),run_command=lambda *_:{"returncode":1,"stdout":"","stderr":"missing"},snapshot_reader=lambda *_:snapshot(0),stop_requested=stop)
    assert result["state"] == "interrupted"
    assert len(result["closed_paths"]) == 3
    assert (out/"diagnostics/vm_a.jsonl").is_file()


def test_snapshot_read_failure_is_local_and_recovery_needs_valid_pair(tmp_path):
    out=tmp_path/"read-error"; out.mkdir(); count=[0]
    def read(_):
        count[0]+=1
        if count[0] == 2: raise OSError("synthetic proc read error")
        return snapshot(count[0])
    context={"deployment_id":"RUN-deployment","vm_role":"vm_b","provider":"aws","interface":"eth0","output_root":str(out),"duration_seconds":4,"metadata_seed":seed("vm_b")}
    result=collect_local(context,root=Path(__file__).parents[1]/"fixtures/f04",clock=FakeClock(),run_command=lambda *_:{"returncode":1,"stdout":"","stderr":"missing"},snapshot_reader=read)
    assert result["state"] == "completed"
    rows=[json.loads(line) for line in (out/"diagnostics/vm_b.jsonl").read_text().splitlines()]
    assert len(rows) == 4
    assert rows[0]["cpu_total_percent"]["value"] is None
    assert rows[1]["cpu_total_percent"]["value"] is None
    assert rows[2]["cpu_total_percent"]["value"] is not None
    assert "snapshot read failed" in rows[0]["gap_reason"]


def test_output_subdirectory_symlink_is_rejected_without_writing_outside(tmp_path):
    out=tmp_path/"symlink-root"; out.mkdir(); external=tmp_path/"outside"; external.mkdir()
    (out/"metadata").symlink_to(external,target_is_directory=True)
    context={"deployment_id":"RUN-deployment","vm_role":"vm_a","provider":"aws","interface":"eth0","output_root":str(out),"duration_seconds":1,"metadata_seed":seed()}
    result=collect_local(context,root=Path(__file__).parents[1]/"fixtures/f04",clock=FakeClock(),run_command=lambda *_:{"returncode":1,"stdout":"","stderr":"missing"},snapshot_reader=lambda *_:snapshot(0))
    assert result["state"] == "failed"
    assert list(external.iterdir()) == []


def test_deadline_consumed_by_metadata_keeps_unknown_mapping_without_sampling(tmp_path):
    out=tmp_path/"deadline"; out.mkdir(); clock=FakeClock(); calls=[]
    def command(argv, timeout):
        calls.append(argv)
        if argv[0] == "sockperf": clock.now=0.5
        return {"returncode":1,"stdout":"","stderr":"unavailable"}
    context={"deployment_id":"RUN-deployment","vm_role":"vm_a","provider":"aws","interface":"eth0","output_root":str(out),"duration_seconds":0.5,"metadata_seed":seed()}
    result=collect_local(context,root=Path(__file__).parents[1]/"fixtures/f04",clock=clock,run_command=command,snapshot_reader=lambda *_:snapshot(0))
    rows=[json.loads(line) for line in (out/"diagnostics/vm_a-clock.jsonl").read_text().splitlines()]
    assert result["state"] == "completed" and result["sample_count"] == 0
    assert result["mapping_count"] == 1 and rows[0]["estimated_error_ms"]["value"] is None
    assert "chronyc" not in calls


def test_write_flush_and_close_failures_are_reported_and_close_is_attempted(tmp_path, monkeypatch):
    from cloud_network_benchmark import diagnostics_guest as guest
    real_open,real_fdopen=guest.os.open,guest.os.fdopen
    destinations={}
    def tracked_open(path, flags, *args, **kwargs):
        fd=real_open(path,flags,*args,**kwargs); destinations[fd]=str(path); return fd
    def wrapped_fdopen(fd,*args,**kwargs):
        stream=real_fdopen(fd,*args,**kwargs); path=destinations.pop(fd)
        fault="write" if path.endswith("vm_a.jsonl") and "/diagnostics/" in path and "clock" not in path else "clock" if "vm_a-clock" in path else None
        if not fault: return stream
        class Proxy:
            def write(self,value):
                if fault == "write": raise OSError("injected write failure")
                return stream.write(value)
            def flush(self):
                if fault == "clock" and mode == "flush": raise OSError("injected flush failure")
                return stream.flush()
            def close(self):
                stream.close()
                if fault == "clock" and mode == "close": raise OSError("injected close failure")
            def __getattr__(self,name): return getattr(stream,name)
        return Proxy()
    monkeypatch.setattr(guest.os,"open",tracked_open)
    monkeypatch.setattr(guest.os,"fdopen",wrapped_fdopen)
    for mode in ("write","flush","close"):
        out=tmp_path/mode; out.mkdir(); context={"deployment_id":"RUN-deployment","vm_role":"vm_a","provider":"aws","interface":"eth0","output_root":str(out),"duration_seconds":2,"metadata_seed":seed()}
        result=collect_local(context,root=Path(__file__).parents[1]/"fixtures/f04",clock=FakeClock(),run_command=lambda *_:{"returncode":1,"stdout":"","stderr":"missing"},snapshot_reader=lambda *_:snapshot(0))
        assert result["state"] == "failed"
        assert result["failures"]
        assert (out/"metadata/vm_a.json").is_file()
        assert (out/"diagnostics/vm_a-clock.jsonl").is_file()
        if mode == "close": assert str(out/"diagnostics/vm_a-clock.jsonl") not in result["closed_paths"]


def test_copied_guest_help_needs_no_project_path(tmp_path):
    import subprocess, sys
    source=Path(__file__).parents[2]/"src/cloud_network_benchmark/diagnostics_guest.py"
    copied=tmp_path/"diagnostics_guest.py"; shutil.copy(source,copied)
    subprocess.run([sys.executable,str(copied),"--help"],cwd=tmp_path,env={"PATH":__import__("os").environ["PATH"]},check=True,timeout=3,capture_output=True)


def test_snapshot_timestamps_bracket_actual_reads_and_deadline_read_is_partial(tmp_path):
    from datetime import datetime
    out=tmp_path/"crossing"; out.mkdir(); clock=FakeClock(); reads=[0]
    def read(_):
        reads[0]+=1
        if reads[0] == 2: clock.now += 1.5
        return snapshot(reads[0])
    context={"deployment_id":"RUN-deployment","vm_role":"vm_a","provider":"aws","interface":"eth0","output_root":str(out),"duration_seconds":2,"metadata_seed":seed()}
    outcome=collect_local(context,root=Path(__file__).parents[1]/"fixtures/f04",clock=clock,run_command=lambda *_:{"returncode":1,"stdout":"","stderr":"missing"},snapshot_reader=read)
    rows=[json.loads(line) for line in (out/"diagnostics/vm_a.jsonl").read_text().splitlines()]
    assert outcome["state"] == "interrupted" and len(rows) == 1
    row=rows[0]
    utc=datetime.fromisoformat(row["sampled_at"].replace("Z","+00:00"))
    assert abs(utc.timestamp()-datetime(2026,10,22,tzinfo=timezone.utc).timestamp()-row["monotonic_seconds"]) < 1e-9
    assert row["monotonic_seconds"] > 1 and row["interval_seconds"] == row["monotonic_seconds"]
    assert "after collection deadline" in row["gap_reason"]
    assert outcome["mapping_count"] == 1


def test_loader_rejects_provider_foreign_closure_and_empty_completed_clock_stream(tmp_path):
    from cloud_network_benchmark.diagnostics import load_collection_result
    from cloud_network_benchmark.contracts.measurements import VmMetadata
    from cloud_network_benchmark.contracts.common import StringEvidence

    def collect(case):
        out=tmp_path/case; out.mkdir()
        context={"deployment_id":"RUN-deployment","vm_role":"vm_a","provider":"aws","interface":"eth0","output_root":str(out),"duration_seconds":1,"metadata_seed":seed()}
        outcome=collect_local(context,root=Path(__file__).parents[1]/"fixtures/f04",clock=FakeClock(),run_command=lambda *_:{"returncode":1,"stdout":"","stderr":"missing"},snapshot_reader=lambda _:snapshot(0))
        return context,outcome,out

    context,outcome,out=collect("provider")
    payload=json.loads((out/"metadata/vm_a.json").read_text()); payload["provider"]="gcp"
    (out/"metadata/vm_a.json").write_text(json.dumps(payload)+"\n")
    loaded=load_collection_result(context,outcome)
    assert loaded.state == "failed" and any("provider" in f["message"] for f in loaded.failures)

    context,outcome,out=collect("foreign-close")
    outcome["closed_paths"].append(str(out/"foreign.json"))
    loaded=load_collection_result(context,outcome)
    assert loaded.state == "failed" and any("foreign" in f["message"] for f in loaded.failures)

    context,outcome,out=collect("empty-clock")
    (out/"diagnostics/vm_a-clock.jsonl").write_text("")
    outcome["mapping_count"]=0
    loaded=load_collection_result(context,outcome)
    assert loaded.state == "failed" and any(f["category"] == "clock_mapping_missing" for f in loaded.failures)


def test_loader_rejects_duplicate_closure_and_preserves_partial_records(tmp_path):
    from cloud_network_benchmark.diagnostics import load_collection_result
    out=tmp_path/"duplicate"; out.mkdir()
    context={"deployment_id":"RUN-deployment","vm_role":"vm_b","provider":"gcp","interface":"eth0","output_root":str(out),"duration_seconds":2,"metadata_seed":seed("vm_b","gcp")}
    outcome=collect_local(context,root=Path(__file__).parents[1]/"fixtures/f04",clock=FakeClock(),run_command=lambda *_:{"returncode":1,"stdout":"","stderr":"missing"},snapshot_reader=lambda _:snapshot(0),stop_requested=lambda:True)
    outcome["closed_paths"].append(outcome["closed_paths"][0])
    loaded=load_collection_result(context,outcome)
    assert loaded.state == "interrupted"
    assert any("duplicate" in f["message"] for f in loaded.failures)
    assert loaded.metadata is not None


def test_loader_accepts_structural_clock_stream_with_utc_reversal_and_rejects_foreign_declaration(tmp_path):
    from cloud_network_benchmark.diagnostics import load_collection_result
    out=tmp_path/"clock-reversal"; out.mkdir()
    context={"deployment_id":"RUN-deployment","vm_role":"vm_a","provider":"aws","interface":"eth0","output_root":str(out),"duration_seconds":2,"metadata_seed":seed()}
    outcome=collect_local(context,root=Path(__file__).parents[1]/"fixtures/f04",clock=FakeClock(),run_command=lambda *_:{"returncode":1,"stdout":"","stderr":"missing"},snapshot_reader=lambda _:snapshot(0))
    outcome["artifacts"]["foreign"]=str(out/"foreign.json")
    (out/"diagnostics/vm_a-clock.jsonl").write_text(
        json.dumps({"vm_role":"vm_a","monotonic_seconds":2,"utc_timestamp":"2026-10-22T00:00:02Z","estimated_error_ms":{"value":None,"unavailable_reason":"unknown"},"synchronization_source":{"value":"chronyc tracking unavailable; missing","unavailable_reason":None}})+"\n"
        +json.dumps({"vm_role":"vm_a","monotonic_seconds":3,"utc_timestamp":"2026-10-21T00:00:03Z","estimated_error_ms":{"value":None,"unavailable_reason":"unknown"},"synchronization_source":{"value":"chronyc tracking unavailable; missing","unavailable_reason":None}})+"\n")
    outcome["mapping_count"]=2
    loaded=load_collection_result(context,outcome)
    assert loaded.state == "failed"
    assert any("exactly metadata" in f["message"] for f in loaded.failures)
    assert not any("time reversed" in f["message"] for f in loaded.failures)
    outcome["artifacts"].pop("foreign")
    loaded=load_collection_result(context,outcome)
    assert loaded.state == "completed" and not loaded.failures and loaded.mapping_count == 2


def test_snapshot_utc_step_records_observed_endpoint_and_typed_loading_keeps_collection_complete(tmp_path):
    from cloud_network_benchmark.diagnostics import load_collection_result
    class SteppedClock(FakeClock):
        def __init__(self):
            super().__init__(); self.offset=0
        def utc_now(self):
            return datetime(2026,10,22,tzinfo=timezone.utc)+timedelta(seconds=self.now+self.offset)
    out=tmp_path/"utc-step"; out.mkdir(); clock=SteppedClock(); reads=[0]
    def read(_):
        reads[0]+=1
        if reads[0] == 2: clock.offset=-3600
        return snapshot(reads[0])
    context={"deployment_id":"RUN-deployment","vm_role":"vm_a","provider":"aws","interface":"eth0","output_root":str(out),"duration_seconds":2,"metadata_seed":seed()}
    outcome=collect_local(context,root=Path(__file__).parents[1]/"fixtures/f04",clock=clock,run_command=lambda *_:{"returncode":1,"stdout":"","stderr":"missing"},snapshot_reader=read)
    rows=[json.loads(line) for line in (out/"diagnostics/vm_a.jsonl").read_text().splitlines()]
    mappings=[json.loads(line) for line in (out/"diagnostics/vm_a-clock.jsonl").read_text().splitlines()]
    assert outcome["state"] == "completed" and len(rows) == 2 and len(mappings) == 3
    assert rows[0]["sampled_at"] == "2026-10-21T23:00:01Z"
    assert rows[1]["sampled_at"] == "2026-10-21T23:00:02Z"
    assert [row["monotonic_seconds"] for row in rows] == [1,2]
    assert rows[1]["interval_seconds"] == 1
    assert mappings[1]["utc_timestamp"] < mappings[0]["utc_timestamp"]
    loaded=load_collection_result(context,outcome)
    assert loaded.state == "completed" and not loaded.failures and loaded.mapping_count == 3
