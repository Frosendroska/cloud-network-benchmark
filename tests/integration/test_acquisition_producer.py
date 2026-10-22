"""End-to-end raw-source binding over the existing F03/F01R evidence records."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import hashlib

import pytest

from cloud_network_benchmark.acquisition import bind_acquisition_sources, iter_source_windows
from cloud_network_benchmark.contracts import ArtifactRecord, Evidence, StringEvidence, VmRole
from cloud_network_benchmark.measurement import run_measurement
from f03_support import FakeClock, FakeProcesses, context, observation


ROOT = Path(__file__).parents[1] / "fixtures" / "acquisition"
DEPLOYMENT = "deployment-acq-fixture"


def _record(root: Path, artifact_id: str, path: str, kind: str, producer: VmRole,
            payload: bytes, unit_id: str | None = None) -> ArtifactRecord:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return ArtifactRecord(artifact_id=artifact_id, path=path, kind=kind, producer=producer,
        unit_id=unit_id, deployment_id=DEPLOYMENT, closed=True, size_bytes=Evidence(value=len(payload)),
        sha256=StringEvidence(value=hashlib.sha256(payload).hexdigest()), retrieval_state="verified")


def _command(action: str, producer: VmRole, unit_id: str | None):
    return SimpleNamespace(request={"action_id": action}, result={"action_id": action},
        producer=producer, unit_id=unit_id, deployment_id=DEPLOYMENT)


def _build(tool: str, producer: VmRole, artifact_id: str):
    semantics = "sockperf-3.10-enabled-v1" if tool == "sockperf" else "iperf-3.17.1-stats-v1"
    commit = "5ebd327da983225321818c0355db922515e026bd" if tool == "sockperf" else "2acfcfe94e928e74542c9f107e02aa6dd4748a79"
    patch = ("3f0bb59a3c70b247e5ebde77254e1427c35dc6ec39e2a78a3c779cff7015a285" if tool == "sockperf"
             else "2619b1070eabbef72dd648372f7eecec2140d08f87e5a237685d98c5802f49df")
    source_sha = ("a35a1daf310551b9f675af4fb7d95a6a10389fda4c7605d70a21554be0773f8e" if tool == "sockperf"
                  else "d6b4d2c7b20157c5b7fbd2de66f22bd3428958b5f2e28899151b6e22745886fe")
    return SimpleNamespace(role=producer, tool=tool, artifact_id=artifact_id,
        source_reference=f"{('sockperf-' if tool == 'sockperf' else 'iperf-')}{commit}", source_sha256=source_sha,
        patch_path=("tool_patches/sockperf-3.10-acquisition.patch" if tool == "sockperf"
                    else "tool_patches/iperf-3.17.1-acquisition.patch"),
        observation_capability="CNB_ACQ_V1", observation_semantics=semantics,
        upstream_commit=commit, patch_sha256=patch, binary_sha256="b" * 64,
        build_configuration="CC=gcc; ./configure --prefix=/opt/cnb; make -j2", compiler_version="gcc 13.2")


def test_f03_source_binder_replays_closed_sockperf_and_persistent_iperf_sources(tmp_path):
    sock_log = (ROOT / "sockperf-window.log").read_bytes()
    iperf_log = (ROOT / "iperf-receiver.log").read_bytes()
    sock_csv = b"Sockperf 3.10 native CSV fixture\n"
    client_json = b'{"start":{},"cookie":"native-cookie-1","end":{}}\n'
    records = [
        _record(tmp_path, "sock-log", "retrieval/sockperf.stderr", "sockperf-observation-stderr", VmRole.VM_A, sock_log, "short-multiflow"),
        _record(tmp_path, "sock-csv", "measurements/short-multiflow/sockperf.csv", "sockperf-csv", VmRole.VM_A, sock_csv, "short-multiflow"),
        _record(tmp_path, "iperf-log", "retrieval/iperf3.stderr", "iperf3-observation-stderr", VmRole.VM_B, iperf_log),
        _record(tmp_path, "iperf-client", "measurements/short-multiflow/iperf3.json", "iperf3-json", VmRole.VM_A, client_json, "short-multiflow"),
    ]
    identity = SimpleNamespace(deployment_id=DEPLOYMENT)
    commands = [_command("sockperf-short-multiflow", VmRole.VM_A, "short-multiflow"),
                _command("iperf3-short-multiflow", VmRole.VM_A, "short-multiflow")]
    receiver = [_command("f02r-vm_b-listener-iperf3", VmRole.VM_B, None)]
    builds = [_build("sockperf", VmRole.VM_A, "sock-build"),
              _build("iperf3", VmRole.VM_B, "iperf-build")]
    sources = bind_acquisition_sources(child_root=tmp_path, run_identity=identity, commands=commands,
        receiver_command_evidence=receiver, build_evidence=builds, source_artifact_records=records)
    assert {(row.tool, row.session_id) for row in sources} == {("sockperf", None), ("iperf3", "native-cookie-1")}
    all_commands = (*commands, *receiver)
    windows = {source.tool: list(iter_source_windows(tmp_path, source, records, all_commands, builds))
               for source in sources}
    assert windows["sockperf"][0].has_positive_inner_support
    assert windows["iperf3"][0].omitted is False
    assert windows["iperf3"][0].start_latest_ns == 5_000_001_000


def test_mutated_closed_source_bytes_are_rejected_before_replay(tmp_path):
    log = (ROOT / "sockperf-window.log").read_bytes()
    row = _record(tmp_path, "sock-log", "retrieval/sockperf.stderr", "sockperf-observation-stderr",
                  VmRole.VM_A, log, "short-multiflow")
    native = _record(tmp_path, "sock-csv", "measurements/short-multiflow/sockperf.csv", "sockperf-csv",
                     VmRole.VM_A, b"native", "short-multiflow")
    from cloud_network_benchmark.contracts import AcquisitionSource
    source = AcquisitionSource(tool="sockperf", semantics_id="sockperf-3.10-enabled-v1", producer=VmRole.VM_A,
        command_action_id="short-multiflow-sockperf", invocation_id="short-multiflow-sockperf",
        boot_id="12345678-1234-1234-1234-123456789abc", clock_domain="CLOCK_MONOTONIC",
        log_artifact_id=row.artifact_id, log_path=row.path, native_artifact_ids=[native.artifact_id],
        build_artifact_id="sock-build")
    (tmp_path / row.path).write_bytes(log + b"altered")
    with pytest.raises(ValueError, match="size does not match"):
        list(iter_source_windows(tmp_path, source, [row, native],
            [_command("short-multiflow-sockperf", VmRole.VM_A, "short-multiflow")],
            [_build("sockperf", VmRole.VM_A, "sock-build")]))


def test_mutated_build_command_path_and_duplicate_artifact_refs_are_rejected(tmp_path):
    log = (ROOT / "sockperf-window.log").read_bytes()
    row = _record(tmp_path, "sock-log", "retrieval/sockperf.stderr", "sockperf-observation-stderr",
                  VmRole.VM_A, log, "short-multiflow")
    native = _record(tmp_path, "sock-csv", "measurements/short-multiflow/sockperf.csv", "sockperf-csv",
                     VmRole.VM_A, b"native", "short-multiflow")
    from cloud_network_benchmark.contracts import AcquisitionSource
    source = AcquisitionSource(tool="sockperf", semantics_id="sockperf-3.10-enabled-v1", producer=VmRole.VM_A,
        command_action_id="sockperf-short-multiflow", invocation_id="sockperf-short-multiflow",
        boot_id="12345678-1234-1234-1234-123456789abc", clock_domain="CLOCK_MONOTONIC",
        log_artifact_id=row.artifact_id, log_path=row.path, native_artifact_ids=[native.artifact_id],
        build_artifact_id="sock-build")
    commands = [_command("sockperf-short-multiflow", VmRole.VM_A, "short-multiflow")]
    build = _build("sockperf", VmRole.VM_A, "sock-build")
    for field, value in (("patch_sha256", "f" * 64), ("patch_path", "tool_patches/other.patch"),
                         ("source_sha256", "c" * 64), ("upstream_commit", "1" * 40),
                         ("binary_sha256", None), ("observation_capability", None)):
        changed = SimpleNamespace(**{**vars(build), field: value})
        with pytest.raises(ValueError):
            list(iter_source_windows(tmp_path, source, [row, native], commands, [changed]))
    with pytest.raises(ValueError, match="one command"):
        list(iter_source_windows(tmp_path, source, [row, native],
            [_command("sockperf-other-action", VmRole.VM_A, "short-multiflow")], [build]))
    with pytest.raises(ValueError, match="uniquely"):
        list(iter_source_windows(tmp_path, source, [row, native, row], commands, [build]))


def test_symlinked_source_artifact_cannot_escape_child_root(tmp_path):
    log = (ROOT / "sockperf-window.log").read_bytes()
    row = _record(tmp_path, "sock-log", "retrieval/sockperf.stderr", "sockperf-observation-stderr",
                  VmRole.VM_A, log, "short-multiflow")
    native = _record(tmp_path, "sock-csv", "measurements/short-multiflow/sockperf.csv", "sockperf-csv",
                     VmRole.VM_A, b"native", "short-multiflow")
    from cloud_network_benchmark.contracts import AcquisitionSource
    source = AcquisitionSource(tool="sockperf", semantics_id="sockperf-3.10-enabled-v1", producer=VmRole.VM_A,
        command_action_id="sockperf-short-multiflow", invocation_id="sockperf-short-multiflow",
        boot_id="12345678-1234-1234-1234-123456789abc", clock_domain="CLOCK_MONOTONIC",
        log_artifact_id=row.artifact_id, log_path=row.path, native_artifact_ids=[native.artifact_id],
        build_artifact_id="sock-build")
    outside = tmp_path.parent / "outside-acquisition.log"
    outside.write_bytes(log)
    target = tmp_path / row.path
    target.unlink()
    target.symlink_to(outside)
    with pytest.raises(ValueError, match="escapes"):
        list(iter_source_windows(tmp_path, source, [row, native],
            [_command("sockperf-short-multiflow", VmRole.VM_A, "short-multiflow")],
            [_build("sockperf", VmRole.VM_A, "sock-build")]))


@pytest.mark.parametrize("mode", ["acceptance", "short_study", "calibration", "diagnostic_day"])
def test_f03_modes_keep_action_scoped_observation_paths_and_existing_process_arguments(
        mode, repository_root, tmp_path):
    resolved = observation(repository_root, mode)
    clock = FakeClock()
    processes = FakeProcesses(clock)
    result = run_measurement(resolved, context(resolved, tmp_path), processes=processes, clock=clock)
    assert result.outcome == "succeeded"
    assert processes.handles
    for handle in processes.handles:
        request = handle.request
        assert request.environment["CNB_ACQUISITION_ID"] == request.action_id
        if request.argv[0] == "sockperf":
            assert request.argv[1] == "under-load"
            assert "--no-rdtsc" in request.argv and "--full-rtt" in request.argv
            assert "--mps" in request.argv and "--reply-every" in request.argv
            assert str(handle.stderr_path).endswith("sockperf-acquisition.stderr") or "acquisition.stderr" in str(handle.stderr_path)
        else:
            assert request.argv[0] == "iperf3" and "-c" in request.argv and "-Z" in request.argv
            if mode == "diagnostic_day":
                assert "--json-stream" in request.argv and "--forceflush" in request.argv
            else:
                assert "-J" in request.argv and "--get-server-output" in request.argv
            assert "iperf3-acquisition.stderr" in str(handle.stderr_path)
    if mode == "diagnostic_day":
        assert len([row for row in processes.handles if row.request.argv[0] == "iperf3"]) == 1
        assert len([row for row in processes.handles if row.request.argv[0] == "sockperf"]) >= 288
