from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from cloud_network_benchmark.contracts.artifacts import ArtifactInput
from cloud_network_benchmark.contracts.common import ArtifactExpectation, VmRole
from cloud_network_benchmark.contracts.campaign import PhaseConfiguration
from cloud_network_benchmark.contracts.artifacts import ArtifactLayout
from cloud_network_benchmark.lifecycle_bundle import (build_closed_inventory, build_remote_closed_inventory,
    finalize_validation, lifecycle_declarations, retry_failed_items, retry_manifest,
    retrieve_closed_bundle, seal_bundle)
from cloud_network_benchmark.retrieval import RetrievalResult


def test_closed_inventory_hashes_only_real_files_and_expands_day_families(tmp_path):
    roots = {"vm_a": tmp_path / "a", "vm_b": tmp_path / "b"}
    for root in roots.values():
        root.mkdir()
    (roots["vm_a"] / "probe").mkdir()
    (roots["vm_a"] / "probe" / "chunk-01.csv").write_bytes(b"synthetic,not-native\n")
    (roots["vm_b"] / "server.log").write_bytes(b"synthetic server closure\n")
    declarations = [
        ArtifactInput(artifact_id="probe-family", path_template="probe/{chunk_id}.csv", kind="sockperf-csv", producer="vm_a", expectation="required"),
        ArtifactInput(artifact_id="server-log", path="server.log", kind="iperf3-server-jsonl", producer="vm_b", expectation="required"),
        ArtifactInput(artifact_id="missing", path="absent.json", kind="vm_metadata", producer="vm_a", expectation="required"),
        ArtifactInput(artifact_id="controller-note", path="controller.json", kind="command_evidence", producer="controller", expectation="optional"),
    ]
    ended = datetime(2026, 10, 23, tzinfo=timezone.utc)
    manifest = build_closed_inventory(
        identity={"provider": "aws"}, deployment_id="run-aws-deployment",
        remote_workspace="/home/ubuntu/cnb/results", declarations=declarations,
        source_roots=roots, source_vm_ids={"vm_a": "10.1.0.1", "vm_b": "10.1.0.2"},
        measurement_ended_at=ended,
        closure={"vm_a": ended + timedelta(seconds=1), "vm_b": ended + timedelta(seconds=2)},
    )
    assert {item.artifact_id for item in manifest.items} == {"probe-family-chunk-01", "server-log"}
    probe = next(item for item in manifest.items if item.artifact_id.startswith("probe-family"))
    assert probe.expected_sha256 == hashlib.sha256(b"synthetic,not-native\n").hexdigest()
    assert manifest.unavailable_items["missing"]["reason"]
    assert manifest.closure_evidence.startswith("observed")


def test_remote_inventory_manifest_uses_observed_remote_hashes_and_controller_sidecar_origin():
    ended = datetime(2026, 10, 23, tzinfo=timezone.utc)
    declarations = [
        ArtifactInput(artifact_id="day-family", path_template="probe/{chunk_id}.csv",
            kind="sockperf-csv", producer="vm_a", expectation="required"),
        ArtifactInput(artifact_id="build-b", path="metadata/build/vm_b-iperf3.identity",
            kind="build-identity", producer="vm_b", expectation="required"),
        ArtifactInput(artifact_id="missing", path="metadata/vm_a.json", kind="vm_metadata",
            producer="vm_a", expectation="required"),
    ]
    files = {
        "vm_a":[{"path":"probe/chunk-01.csv", "size_bytes":7, "sha256":hashlib.sha256(b"native\n").hexdigest()}],
        "vm_b":[{"path":"metadata/build/vm_b-iperf3.identity", "size_bytes":9,
                  "sha256":hashlib.sha256(b"sidecar!!").hexdigest()}],
    }
    manifest = build_remote_closed_inventory(identity={"provider":"aws"},
        deployment_id="run-aws-deployment", remote_workspace="/var/lib/cnb/work",
        declarations=declarations, remote_files=files,
        source_vm_ids={"vm_a":"10.1.0.1", "vm_b":"10.1.0.2"},
        measurement_ended_at=ended,
        closure={"vm_a":ended+timedelta(seconds=1), "vm_b":ended+timedelta(seconds=2)},
        controller_materialized_ids=("build-b",))
    assert [item.artifact_id for item in manifest.items] == ["day-family-chunk-01"]
    assert manifest.items[0].expected_sha256 == hashlib.sha256(b"native\n").hexdigest()
    assert manifest.declarations[1].producer == "controller"
    assert "missing" in manifest.unavailable_items


def test_retry_selects_only_failed_concrete_ids_and_drops_siblings():
    ended = datetime(2026, 10, 23, tzinfo=timezone.utc)
    declarations = [ArtifactInput(artifact_id="a", path="a.csv", kind="sockperf-csv", producer="vm_a", expectation="required"),
                    ArtifactInput(artifact_id="b", path="b.csv", kind="sockperf-csv", producer="vm_b", expectation="required")]
    base = build_closed_inventory(
        identity={"provider": "aws"}, deployment_id="run-aws-deployment",
        remote_workspace="/home/ubuntu/cnb", declarations=declarations,
        source_roots={"vm_a": _root("a.csv", b"a"), "vm_b": _root("b.csv", b"b")},
        source_vm_ids={"vm_a": "10.1.0.1", "vm_b": "10.1.0.2"}, measurement_ended_at=ended,
        closure={"vm_a": ended, "vm_b": ended},
    )
    result = RetrievalResult(complete=False, failures=[{"artifact_id": "b"}], attempts=[])
    assert retry_failed_items(result, already_retried=()) == ("b",)
    retry = retry_manifest(base, ("b",))
    assert [item.artifact_id for item in retry.items] == ["b"]
    assert [item.artifact_id for item in retry.declarations if item.producer != "controller"] == ["b"]


def test_seal_is_exclusive_and_excludes_private_tree_and_its_own_index(tmp_path):
    (tmp_path / "raw").mkdir(); (tmp_path / "private").mkdir()
    (tmp_path / "raw" / "result.bin").write_bytes(b"unchanged")
    (tmp_path / "private" / "recovery.json").write_text("private")
    digest, records = seal_bundle(tmp_path)
    assert set(records) == {"raw/result.bin"}
    assert digest == hashlib.sha256((tmp_path / "checksums.sha256").read_bytes()).hexdigest()
    try:
        seal_bundle(tmp_path)
    except FileExistsError:
        pass
    else:
        raise AssertionError("sealed bundle should be immutable")


def test_supplemental_declarations_use_actual_f03_paths_and_day_family_ids():
    short_units = [PhaseConfiguration(unit_id="phase-idle", kind="phase", phase="idle_latency", measured_seconds=10),
                   PhaseConfiguration(unit_id="phase-single", kind="phase", phase="single_flow", measured_seconds=10, streams=1),
                   PhaseConfiguration(unit_id="phase-multi", kind="phase", phase="multi_flow", measured_seconds=10, streams=4)]
    short = lifecycle_declarations(ArtifactLayout.for_mode("acceptance", short_units),
                                   mode="acceptance", measurement_units=short_units)
    assert {item.artifact_id for item in short if item.kind == "build-identity"} == {
        f"f02r-{role}-{tool}-build" for role in ("vm_a", "vm_b") for tool in ("sockperf", "iperf3")}
    stdout = next(item for item in short if item.artifact_id == "phase-single-sockperf-stdout")
    assert stdout.path == "measurements/phase-single/sockperf.stdout.log"
    assert stdout.kind == "sockperf-log"
    day_units = [PhaseConfiguration(unit_id="study-day", kind="continuous_day", phase="multi_flow", measured_seconds=86400)]
    day = lifecycle_declarations(ArtifactLayout.for_mode("diagnostic_day", day_units), mode="diagnostic_day", measurement_units=day_units)
    assert next(item for item in day if item.artifact_id == "day-probe-csv").path_template.endswith("{chunk_id}.csv")
    assert not any(item.kind == "sockperf-stdout" for item in day)


def _root(filename, data):
    import tempfile
    root = Path(tempfile.mkdtemp())
    (root / filename).write_bytes(data)
    return root


from pathlib import Path


def test_f09_retrieval_rechecks_admission_and_records_one_campaign_retry(tmp_path):
    from tests.fixtures.f02r.support import connection, deadline, result as command_result
    from tests.unit.test_retrieval import manifest as retrieval_manifest
    from cloud_network_benchmark.orchestration import (SavedLifecycleRecovery,
        consume_retry_attempt, write_recovery_journal)
    now = datetime.now(timezone.utc)
    c = connection(tmp_path); payload = b"native"; calls = []
    journal = tmp_path / "private" / "recovery.json"
    write_recovery_journal(journal, SavedLifecycleRecovery(schema_version=1, attempt_id="attempt-retrieval",
        controller_session_id="session", providers=[{"provider":"aws", "deployment_id":"run-aws-deployment",
        "journal_reference":"private/aws.json", "expiry_registered":True,
        "creation_may_have_started":True, "cleanup_open":True}]))
    class Runner:
        def run(self, request):
            if request.action_type == "ssh":
                return command_result(request.action_id, f"{len(payload)} {hashlib.sha256(payload).hexdigest()}")
            calls.append(request)
            Path(request.argv[-1]).write_bytes(b"partial" if len(calls) == 1 else payload)
            return command_result(request.action_id)
    admissions = []
    consumed = []
    outcome = retrieve_closed_bundle(connection=c, manifest=retrieval_manifest(c, payload), runner=Runner(),
        filesystem=tmp_path, deadline=deadline(), retry_admission=lambda item: admissions.append(item.artifact_id) or True,
        consume_retry=lambda artifact_id: consumed.append(artifact_id) or consume_retry_attempt(journal, artifact_id),
        reservation_id="res-1", provider_trigger=now + timedelta(minutes=5), retry_seconds=1,
        now_utc=lambda: now, clock=lambda: 100)
    assert outcome.complete and outcome.retrieved == ["sockperf-a"]
    assert admissions == ["sockperf-a"]
    assert consumed == ["sockperf-a"]
    recovery = SavedLifecycleRecovery.validate_unique(__import__("json").loads(journal.read_text()))
    assert recovery.retry_consumed_ids == ["sockperf-a"]
    assert [attempt.attempt for attempt in outcome.attempts] == [1, 2]
    assert outcome.attempts[0].reason == "size_mismatch" and outcome.attempts[1].outcome == "verified"


def test_f09_retrieval_refuses_retry_when_fresh_admission_fails(tmp_path):
    from tests.fixtures.f02r.support import connection, deadline, result as command_result
    from tests.unit.test_retrieval import manifest as retrieval_manifest
    now = datetime.now(timezone.utc)
    c = connection(tmp_path); payload = b"native"; calls = []
    class Runner:
        def run(self, request):
            if request.action_type == "ssh":
                return command_result(request.action_id, f"{len(payload)} {hashlib.sha256(payload).hexdigest()}")
            calls.append(request); Path(request.argv[-1]).write_bytes(b"partial")
            return command_result(request.action_id)
    outcome = retrieve_closed_bundle(connection=c, manifest=retrieval_manifest(c, payload), runner=Runner(),
        filesystem=tmp_path, deadline=deadline(), retry_admission=lambda item: False,
        reservation_id="res-1", provider_trigger=now + timedelta(minutes=5), retry_seconds=1,
        now_utc=lambda: now, clock=lambda: 100)
    assert not outcome.complete and len(calls) == len(outcome.attempts) == 1


def test_f09_retrieval_never_retries_after_interrupted_inventory(tmp_path):
    from cloud_network_benchmark.contracts.execution import CommandResult
    from tests.fixtures.f02r.support import connection, deadline
    from tests.unit.test_retrieval import manifest as retrieval_manifest
    now = datetime.now(timezone.utc); c = connection(tmp_path); attempted = []; admissions = []
    class Runner:
        def run(self, request):
            attempted.append(request)
            stamp = datetime.now(timezone.utc)
            return CommandResult(action_id=request.action_id, outcome="interrupted", started_at=stamp,
                finished_at=stamp, duration_seconds=0, exit_code=None, stdout="", stderr="")
    outcome = retrieve_closed_bundle(connection=c, manifest=retrieval_manifest(c), runner=Runner(),
        filesystem=tmp_path, deadline=deadline(), retry_admission=lambda item: admissions.append(item) or True,
        reservation_id="res-1", provider_trigger=now + timedelta(minutes=5), retry_seconds=1,
        now_utc=lambda: now, clock=lambda: 100)
    assert not outcome.complete and not admissions and len(attempted) == 1


def test_saved_validator_input_replays_real_f08_validation_and_seals_after_outputs(tmp_path, repository_root):
    from tests.unit.test_validation import NOW, make_input
    value = make_input(repository_root)
    root = tmp_path / "bundle"; root.mkdir()
    input_path = root / "validation" / "validator-input.json"
    result, summary, digest, records = finalize_validation(root=root, validator_input_path=input_path,
        value=value, evaluated_at=NOW)
    assert result.run_id == value.identity.run_id
    assert input_path.is_file() and (root / "validation/result.json").is_file()
    assert (root / "validation/summary.txt").read_text() == summary
    assert "checksums.sha256" not in records and "private" not in " ".join(records)
    assert digest == hashlib.sha256((root / "checksums.sha256").read_bytes()).hexdigest()


def test_persist_validation_defers_bundle_seal_for_batch_projection(tmp_path, repository_root):
    from tests.unit.test_validation import NOW, make_input
    from cloud_network_benchmark.lifecycle_bundle import persist_validation
    value = make_input(repository_root)
    root = tmp_path / "bundle"; root.mkdir()
    result, _ = persist_validation(root=root,
        validator_input_path=root / "validation" / "validator-input.json",
        value=value, evaluated_at=NOW)
    assert result.run_id == value.identity.run_id
    assert (root / "validation/result.json").is_file()
    assert not (root / "checksums.sha256").exists()
