"""Generated WEEK -> runtime publisher -> durable bindings -> native consumer.

Offline only. Financial admission and provider workflow creation are explicit
fixture boundaries; no reservation, provider command or campaign launch occurs.
Guest transport is a local Linux sandbox. Production observer config, callback,
progress writer, publication, hashes and consumer are never hand-authored.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from cloud_network_benchmark.campaign import CampaignDriver, _stage_digest, _write_json
from cloud_network_benchmark.campaign_inputs import prepare_stage
from cloud_network_benchmark.lifecycle_runtime import build_runtime, load_runtime_config
from tests.integration.test_campaign_admission import Clock, load_entry, operator_settings, register_generated_cleanup


def test_generated_week_observer_configuration(repository_root, tmp_path, monkeypatch, request):
    settings = operator_settings(tmp_path, repository_root, monkeypatch)
    stage = prepare_stage({"stage": "week"}, settings, repository_root,
        Path(settings["state_root"]), Path(settings["results_root"]), Clock())
    register_generated_cleanup(stage, request)
    assert len(stage["entries"]) == 56
    assert len({run for entry in stage["entries"] for run in entry["run_ids"].values()}) == 168
    assert stage["campaign_id"] == stage["stage_id"]
    for entry in stage["entries"]:
        runtime = load_runtime_config(Path(entry["runtime_config_path"]))
        observer = runtime["observer"]
        assert observer["entry_id"] == entry["entry_id"]
        assert observer["stage_id"] == observer["campaign_id"] == stage["stage_id"]
        assert observer["bindings_path"] == Path(stage["observer_bindings_path"])
        assert observer["launch_receipt_root"] == Path(stage["observer_launch_receipt_root"])
        assert observer["prepared_stage_path"] == Path(stage["state_root"]) / stage["stage_id"] / "prepared-stage.json"
        assert observer["workspace"] == runtime["access"]["bootstrap"].workspace
        assert hashlib.sha256(Path(entry["runtime_config_path"]).read_bytes()).hexdigest() == entry["runtime_config_sha256"]


@pytest.fixture
def week_chain(repository_root, tmp_path, monkeypatch, request,
        isolated_legacy_provider_financial_boundary):
    if sys.platform != "linux":
        pytest.skip("native Linux /proc observer qualification")
    from cloud_network_benchmark import lifecycle_remote, lifecycle_runtime, guest_observer
    from cloud_network_benchmark.admission import load_cost_inputs
    from cloud_network_benchmark.config import resolve_campaign
    from cloud_network_benchmark.observer_publisher import GuestObserverProgress, validate_guest_progress
    from cloud_network_benchmark.measurement_process import LocalMeasurementProcesses, SystemMeasurementClock
    from cloud_network_benchmark.contracts.execution import CommandRequest
    from cloud_network_benchmark.scheduling import load_schedule

    settings = operator_settings(tmp_path, repository_root, monkeypatch)
    stage = prepare_stage({"stage": "week"}, settings, repository_root,
        Path(settings["state_root"]), Path(settings["results_root"]), Clock())
    register_generated_cleanup(stage, request)
    state = Path(stage["state_root"])
    prepared = state / stage["stage_id"] / "prepared-stage.json"
    journal_path = prepared.with_name("campaign-journal.json")
    driver = CampaignDriver()
    journal = driver._load_or_create_journal(stage, journal_path)
    active = stage["entries"][:2]
    assert {entry["scenario"] for entry in active} == {"S1", "S3"}
    assert active[0]["scheduled_start"] == active[1]["scheduled_start"]
    for entry in active:
        journal["entries"][entry["entry_id"]].update(state="running",
            started_at=datetime.now(timezone.utc).isoformat())
    _write_json(journal_path, journal)
    _write_json(state / "active-stage.json", {"stage_id": stage["stage_id"], "stage_path": str(prepared)})
    frozen_digest = _stage_digest(stage)
    runtime_hashes = {entry["runtime_config_path"]: entry["runtime_config_sha256"] for entry in stage["entries"]}

    def denied(*args, **kwargs):
        raise AssertionError("offline WEEK observer test attempted provider/SSH execution")

    # The actual factory constructs every real remote-stage adapter. Capture the
    # callback it supplies; never construct a parallel observer configuration.
    callbacks, remote_stages, guest_data = {}, {}, {}
    reports = []
    from cloud_network_benchmark import access, lifecycle_adapters, diagnostics as diagnostics_module
    from cloud_network_benchmark.contracts.common import IdentityContext, CommandOutcome
    from cloud_network_benchmark.orchestration import ProviderStageOutcome
    class StageClock:
        def __init__(self, start):
            self.now, self.tick = start, 100.0
        def utc_now(self):
            return self.now
        def monotonic(self):
            return self.tick
        def sleep(self, seconds):
            self.tick += seconds
            self.now += timedelta(seconds=seconds)
    def fake_prepare(connection, **kwargs):
        row = guest_data[connection.run_id]
        return ProviderStageOutcome("prepare", True, {"bootstrap": row[3],
            "guest_runtime": SimpleNamespace(source_revision=kwargs["source_revision"],
                package_sha256="a" * 64, remote_root=kwargs["remote_root"])})
    def fake_diagnostics(connection, context, **kwargs):
        return guest_data[connection.run_id][4][context["role"]]
    forwarded = []
    def fake_f03(connection, guest_request, **kwargs):
        # Guest progress comes from the production writer and actual /proc
        # process receipts, transported through the real measure() callback.
        assert guest_request.identity.run_id == connection.run_id
        row = guest_data[connection.run_id]
        forwarded.append(connection.run_id)
        kwargs["on_observer_progress"](row[-1])
        return lifecycle_adapters.RemoteGuestExecution(outcome=None, actions=(),
            failure="offline transport fixture: no benchmark executed")
    monkeypatch.setattr(access.ConnectionOutput, "from_deployment",
        classmethod(lambda cls, pair, timeouts: guest_data[pair.run_id][2]))
    monkeypatch.setattr(lifecycle_adapters, "prepare_remote_pair", fake_prepare)
    monkeypatch.setattr(lifecycle_adapters, "start_remote_diagnostics", fake_diagnostics)
    monkeypatch.setattr(lifecycle_adapters, "run_remote_f03", fake_f03)
    monkeypatch.setattr(diagnostics_module, "build_collection_context",
        lambda pair, role, **kwargs: {"role": role})
    original_remote = lifecycle_remote.build_provider_remote_stages
    def capture_remote(**kwargs):
        callback = kwargs["observer_progress_callback"]
        assert callable(callback), "generated WEEK lost its production observer callback"
        child = kwargs["children"][kwargs["provider"]]
        callbacks[child.run_id] = callback
        def published(*args):
            report = callback(*args)
            reports.append(report)
            return report
        kwargs["observer_progress_callback"] = published
        clock = kwargs["clock"]
        cap = kwargs["campaign"].admission.lifetime_cap_seconds
        # In-memory admitted-timing seam; never reserve or modify an operational
        # ledger. Keep the generated cap and actual stage allowances unchanged.
        session = kwargs["ledger"].controller_session_id
        kwargs["ledger"] = SimpleNamespace(controller_session_id=session,
            inspect=lambda: {"reservations": {"offline-observer-only": {
                "session": session, "cap_start_monotonic": 100.0, "cap_seconds": cap}}},
            check_stage_fit=lambda *a, **k: SimpleNamespace(eligible=True))
        stages = original_remote(**kwargs)
        remote_stages[child.run_id] = (stages, clock)
        return stages
    monkeypatch.setattr(lifecycle_remote, "build_provider_remote_stages", capture_remote)
    def no_provider_workflows(**kwargs):
        assert set(kwargs["stages"]) == {"aws", "azure", "gcp"}
        return {}
    monkeypatch.setattr(lifecycle_runtime, "build_provider_workflows", no_provider_workflows)
    runner = SimpleNamespace(run=denied, run_bounded_copy=denied)

    for name in ("unused-known-hosts", "unused-identity"):
        (tmp_path / name).write_text("offline placeholder; no real credential\n")
    worker = tmp_path / "sockperf"
    worker.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(60)\n")
    worker.chmod(0o700)
    processes = []
    role_workers = []
    def native_role():
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        role_workers.append(process)
        stat = Path(f"/proc/{process.pid}/stat").read_text()
        return {"pid": process.pid, "boot_id": boot,
            "start_ticks": int(stat[stat.rfind(")") + 2:].split()[19])}
    def stop_owned():
        for runner, handle in processes:
            runner.stop(handle, time.monotonic() + 3)
        for process in role_workers:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=3)
    request.addfinalizer(stop_owned)
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    guest_roots = {}
    invocations = []
    for entry in active:
        runtime, parent, children, _, _ = load_entry(stage, entry, repository_root)
        base, _ = resolve_campaign(Path(entry["config_path"]), repository_root)
        schedule = load_schedule(Path(entry["schedule_path"]),
            config_paths={base.config_id: Path(entry["config_path"])}, repository_root=repository_root)[0]
        campaign, _ = resolve_campaign(Path(entry["config_path"]), repository_root, schedule_entry=schedule)
        clock = StageClock(datetime.fromisoformat(entry["scheduled_start"]) - timedelta(seconds=60))
        runtime["sleep"] = clock.sleep
        def fake_execute(runner, command):
            assert command.action_id.startswith("f09-control-clock-")
            return SimpleNamespace(outcome=CommandOutcome.SUCCEEDED, stdout=clock.utc_now().isoformat())
        monkeypatch.setattr(access, "_execute", fake_execute)
        built = build_runtime(campaign=campaign, parent=parent, children=children,
            costs=load_cost_inputs(Path(entry["cost_inputs_path"])), runtime=runtime,
            controller_session_id=stage["controller_session_id"], runner=runner, clock=clock,
            private_root=Path(entry["private_root"]), repository_root=repository_root)
        reservation = SimpleNamespace(reservation_id=SimpleNamespace(value="offline-observer-only"),
            cap_started_at=SimpleNamespace(value=clock.utc_now()),
            expires_at=SimpleNamespace(value=clock.utc_now() + timedelta(seconds=campaign.admission.lifetime_cap_seconds)))
        assert built["workflow_factory"](reservation) == {}
        assert built["ledger"].inspect()["reservations"] == {}
        for provider, child in children.items():
            workspace = tmp_path / child.run_id
            (workspace / "diagnostics").mkdir(parents=True)
            (workspace / "iperf3").mkdir()
            (workspace / "measurements").mkdir()
            for name in ("diagnostics/vm_a.jsonl", "diagnostics/vm_b.jsonl", "iperf3/server.jsonl"):
                (workspace / name).write_text("synthetic native guest output\n")
            progress_writer = GuestObserverProgress(workspace / "progress.json",
                deployment_id=child.deployment_id, observation=child.resolved_observation)
            process_runner = LocalMeasurementProcesses(clock=SystemMeasurementClock(),
                observer_callback=progress_writer, finalization_grace_seconds=15)
            unit = child.resolved_observation.measurement_units[0]
            command = CommandRequest(action_id=f"sockperf-{unit.unit_id}", action_type="measurement",
                argv=[str(worker), "--full-log", str(workspace / "measurements/probe.csv"), "-t", "60"],
                cwd=str(workspace), timeout_seconds=75)
            handle = process_runner.start(command, stdout_path=workspace / "out", stderr_path=workspace / "err")
            processes.append((process_runner, handle))
            progress = validate_guest_progress(json.loads(progress_writer.path.read_text()), deployment_id=child.deployment_id)
            assert progress["producer"]["pid"] == handle.process.pid
            assert progress["producer"]["boot_id"] == boot
            remotes = {}
            for role in ("vm_a", "vm_b"):
                vm_id = f"offline-{child.run_id}-{role}"
                guest_roots[vm_id] = workspace
                remotes[role] = SimpleNamespace(host="192.0.2.10", user="offline", port=22,
                    host_key_reference=str(tmp_path / "unused-known-hosts"),
                    authentication_reference=str(tmp_path / "unused-identity"))
            connection = SimpleNamespace(deployment_id=child.deployment_id, run_id=child.run_id,
                private_ipv4_b="10.0.0.2", timeouts=runtime["access"]["timeouts"], **remotes)
            identity = IdentityContext(**{key: getattr(child, key) for key in
                ("experiment_id", "config_id", "campaign_id", "observation_id", "run_id", "deployment_id", "provider")})
            pair = SimpleNamespace(deployment_id=child.deployment_id, run_id=child.run_id, identity=identity,
                **{role: SimpleNamespace(resource_id=f"offline-{child.run_id}-{role}") for role in remotes})
            bootstrap = SimpleNamespace(builds=[], listeners=[SimpleNamespace(tool="iperf3", role="vm_b", state="active",
                stdout_path=runtime["observer"]["workspace"] + "/iperf3/server.jsonl", **native_role())])
            diagnostics = {role: SimpleNamespace(deployment_id=child.deployment_id, role=role, **native_role())
                for role in remotes}
            invocation = (child, pair, connection, bootstrap, diagnostics, progress)
            callback = callbacks[child.run_id]
            invocations.append((callback, invocation))
            guest_data[child.run_id] = invocation
            stages, stage_clock = remote_stages[child.run_id]
            prepared_result = stages.prepare(child, pair)
            assert prepared_result.succeeded, prepared_result
            stable_result = stages.stabilize(child, pair)
            assert stable_result.succeeded, stable_result
            measured = stages.measure(child, pair)
            assert not isinstance(measured.evidence, dict), measured
            assert measured.evidence.failure == "offline transport fixture: no benchmark executed", measured
            assert forwarded[-1] == child.run_id
    assert [row["status"] for row in reports] == ["incomplete"] * 5 + ["published"], reports
    assert len(callbacks) == len(forwarded) == 6

    # Execute the unchanged production guest probe program with real Linux /proc.
    # Only SSH transport and the guest filesystem location are substituted.
    original_run = subprocess.run
    def local_probe(role, payload, **kwargs):
        payload = {**payload, "workspace": str(guest_roots[role["vm_id"]])}
        result = original_run([sys.executable, "-c", guest_observer._REMOTE, json.dumps(payload)],
            check=True, capture_output=True, text=True, timeout=5)
        return json.loads(result.stdout)
    monkeypatch.setattr(guest_observer, "_ssh_probe", local_probe)
    monkeypatch.setattr("cloud_network_benchmark.campaign_supervisor._read_only_preflight", denied)

    class OnePoll:
        done = False
        def is_set(self):
            return self.done
        def wait(self, interval):
            self.done = True
    def consume():
        from cloud_network_benchmark.campaign_supervisor import observe
        observe(state, OnePoll())
        result = json.loads((state / "observer-status.json").read_text())
        assert result["credential_preflight"] == {"status": "not_run_by_observer"}
        return result
    emitted = json.loads(prepared.read_text())
    assert _stage_digest(emitted) == frozen_digest
    for path, digest in runtime_hashes.items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
    assert consume()["health"] == "healthy"
    return SimpleNamespace(stage=emitted, prepared=prepared, journal=journal,
        journal_path=journal_path, consume=consume, invocations=invocations,
        guest_roots=guest_roots, frozen_digest=frozen_digest, driver=driver)


def test_week_native_publisher_receipt_and_supervisor_chain(week_chain):
    chain = week_chain
    stage = chain.stage
    receipt = Path(stage["observer_launch_receipt_path"])
    bindings = Path(stage["observer_bindings_path"])
    receipt_bytes = receipt.read_bytes()
    receipt_stat = receipt.stat()
    assert hashlib.sha256(receipt_bytes).hexdigest() == stage["observer_launch_receipt_sha256"]
    assert hashlib.sha256(bindings.read_bytes()).hexdigest() == stage["observer_bindings_sha256"]
    assert receipt_stat.st_mode & 0o777 == 0o600
    assert len(json.loads(receipt_bytes)["participants"]) == 6
    # A duplicate callback is an idempotent refresh, never another launch/owner.
    callback, invocation = chain.invocations[-1]
    assert callback(*invocation)["status"] == "published"
    assert receipt.read_bytes() == receipt_bytes
    assert receipt.stat().st_ino == receipt_stat.st_ino
    assert len(json.loads(bindings.read_text())["entries"]) == 6
    from cloud_network_benchmark.observer_publisher import write_launch_receipt
    with pytest.raises(FileExistsError, match="immutable"):
        write_launch_receipt(receipt, stage_id=stage["stage_id"], participants={tuple(json.loads(key)): value for key, value in json.loads(receipt_bytes)["participants"].items()})
    readback = json.loads(chain.prepared.read_text())
    assert _stage_digest(readback) == chain.frozen_digest
    assert chain.driver._load_or_create_journal(readback, chain.journal_path)["stage_id"] == stage["stage_id"]
    assert chain.consume()["health"] == "healthy"


@pytest.mark.parametrize("negative", ["missing_bindings", "missing_receipt", "missing_progress",
    "stale_output", "mismatched_pid", "mismatched_hash", "mismatched_run", "duplicate_binding",
    "duplicate_prepared", "mismatched_receipt", "next_pair_missing"])
def test_week_native_observer_negatives(week_chain, negative):
    chain = week_chain
    stage = chain.stage
    bindings_path = Path(stage["observer_bindings_path"])
    bindings = json.loads(bindings_path.read_text())
    if negative == "missing_bindings":
        bindings_path.unlink()
    elif negative == "missing_receipt":
        Path(stage["observer_launch_receipt_path"]).unlink()
    elif negative == "missing_progress":
        callback, invocation = chain.invocations[-1]
        broken = (*invocation[:-1], {**invocation[-1], "producer": None})
        assert callback(*broken)["status"] == "incomplete"
    elif negative == "stale_output":
        for root in chain.guest_roots.values():
            os.utime(root / "diagnostics/vm_a.jsonl", (time.time() - 180, time.time() - 180))
    elif negative == "mismatched_receipt":
        receipt = Path(stage["observer_launch_receipt_path"])
        receipt.write_bytes(receipt.read_bytes() + b" ")
    elif negative == "duplicate_prepared":
        stage["entries"].append(copy.deepcopy(stage["entries"][0]))
        _write_json(chain.prepared, stage)
    elif negative == "next_pair_missing":
        for entry in stage["entries"][:2]:
            chain.journal["entries"][entry["entry_id"]]["state"] = "complete"
        for entry in stage["entries"][2:4]:
            chain.journal["entries"][entry["entry_id"]]["state"] = "running"
        _write_json(chain.journal_path, chain.journal)
    else:
        if negative == "mismatched_pid":
            next(p for p in bindings["entries"][0]["roles"]["vm_a"]["processes"] if p["kind"] == "producer")["start_ticks"] += 1
        elif negative == "mismatched_run":
            bindings["entries"][0]["run_id"] = "foreign-run"
        elif negative == "duplicate_binding":
            bindings["entries"].append(copy.deepcopy(bindings["entries"][0]))
        else:
            bindings["campaign_id"] = "foreign-campaign"
        _write_json(bindings_path, bindings)
        if negative != "mismatched_hash":
            # Corruption test re-pins only the outer hash to exercise the
            # inner identity/duplicate validator, not merely hash mismatch.
            stage["observer_bindings_sha256"] = hashlib.sha256(bindings_path.read_bytes()).hexdigest()
            _write_json(chain.prepared, stage)
    observed = chain.consume()
    assert observed["health"] in {"unknown", "fault"}, observed
    errors = json.dumps(observed["guest_health"])
    expected = {"missing_bindings": "unavailable_or_invalid", "missing_receipt": "unavailable_or_invalid",
        "missing_progress": "active_guest_binding_set_mismatch", "stale_output": "output_stale:diagnostics",
        "mismatched_pid": "process_identity_mismatch:producer", "mismatched_hash": "bindings_hash_mismatch",
        "mismatched_run": "active_guest_binding_set_mismatch", "duplicate_binding": "duplicate_guest_participant",
        "duplicate_prepared": "duplicate_prepared_entry", "mismatched_receipt": "launch_receipt_hash_mismatch",
        "next_pair_missing": "active_guest_binding_set_mismatch"}
    assert expected[negative] in errors
