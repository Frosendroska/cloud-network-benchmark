"""End-to-end raw-source binding over the existing F03/F01R evidence records."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace as dataclass_replace
import hashlib
import json
import tracemalloc

import pytest

from cloud_network_benchmark.acquisition import bind_acquisition_sources, iter_source_windows
from cloud_network_benchmark.contracts import ArtifactRecord, Evidence, StringEvidence, VmRole
from cloud_network_benchmark.measurement import run_measurement
from f03_support import FakeClock, FakeProcesses, context, observation


ROOT = Path(__file__).parents[1] / "fixtures" / "acquisition"
DEPLOYMENT = "deployment-acq-fixture"


def _record(root: Path, artifact_id: str, path: str, kind: str, producer: VmRole,
            payload: bytes, unit_id: str | None = None, deployment_id: str = DEPLOYMENT) -> ArtifactRecord:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return ArtifactRecord(artifact_id=artifact_id, path=path, kind=kind, producer=producer,
        unit_id=unit_id, deployment_id=deployment_id, closed=True, size_bytes=Evidence(value=len(payload)),
        sha256=StringEvidence(value=hashlib.sha256(payload).hexdigest()), retrieval_state="verified")


def _command(action: str, producer: VmRole, unit_id: str | None, deployment_id: str = DEPLOYMENT):
    return SimpleNamespace(request={"action_id": action}, result={"action_id": action},
        producer=producer, unit_id=unit_id, deployment_id=deployment_id)


def _fixture_processes(clock, *, mutation=None, failed_action=None, hang_action=None):
    """Run the real F03 producer against pinned-patch grammar fixtures."""
    from f03_support import FakeProcesses

    class SourceFixtureProcesses(FakeProcesses):
        def __init__(self, clock):
            super().__init__(clock, failed_action=failed_action, hang_action=hang_action)

        def start(self, request, *, stdout_path, stderr_path):
            handle = super().start(request, stdout_path=stdout_path, stderr_path=stderr_path)
            if request.argv[0] == "sockperf":
                template = (ROOT / "sockperf-window.log").read_text(encoding="utf-8")
                template = template.replace("sockperf-short-multiflow", request.action_id)
                lines = template.splitlines()
                base_ns = int(clock.monotonic() * 1_000_000_000)
                seconds = int(request.argv[request.argv.index("-t") + 1])
                for index, line in enumerate(lines):
                    if not line.startswith("CNB_ACQ_V1 "):
                        continue
                    value = json.loads(line[len("CNB_ACQ_V1 "):])
                    if value.get("record_kind") == "clock_anchor":
                        value.update(mono_before_ns=base_ns, realtime_ns=base_ns, mono_after_ns=base_ns + 1_000)
                    elif value.get("record_kind") == "probe_window":
                        if mutation == "interrupted":
                            lines[index] = ""
                            continue
                        if seconds <= 5:
                            # No post-warmup native filter support is possible.
                            lines[index] = ""
                            continue
                        value.update(sender_enabled_ns=base_ns + 1_000_000_000,
                            receiver_enabled_ns=base_ns + 1_100_000_000,
                            termination_ns=base_ns + seconds * 1_000_000_000,
                            native_filter_start_ns=base_ns + 5_000_000_000,
                            native_filter_end_ns=base_ns + (seconds - 5) * 1_000_000_000)
                        if mutation == "missing_start":
                            value.pop("sender_enabled_ns", None)
                        elif mutation == "missing_filter":
                            value.pop("native_filter_start_ns", None)
                        elif mutation == "missing_end":
                            value.pop("termination_ns", None)
                    lines[index] = "CNB_ACQ_V1 " + json.dumps(value, separators=(",", ":"))
                template = "\n".join(lines) + "\n"
                stderr_path.write_text(template, encoding="utf-8")
            return handle

    return SourceFixtureProcesses(clock)


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


def test_large_receiver_replay_is_repeatable_immutable_and_stream_bounded(tmp_path):
    cookie = "cookie-day-stream"
    invocation = "f02r-vm_b-listener-iperf3"
    anchor = {"format": 1, "tool": "iperf3", "semantics_id": "iperf-3.17.1-stats-v1",
        "invocation_id": invocation, "pid": 71, "boot_id": "12345678-1234-1234-1234-123456789def",
        "clock": "CLOCK_MONOTONIC", "record_kind": "clock_anchor", "cookie": cookie,
        "mono_before_ns": 1, "realtime_ns": 2, "mono_after_ns": 3}
    origin = {**anchor, "record_kind": "stats_origin", "stream_id": "stream-1",
        "phase": "post_omit", "origin_us": 0}
    prefix = b"".join((b"CNB_ACQ_V1 " + json.dumps(value, separators=(",", ":")).encode() + b"\n")
        for value in (anchor, origin))
    count = 50_000
    rows = []
    for index in range(count):
        start = index * 1_000_000
        rows.append({**anchor, "record_kind": "receiver_interval", "stream_id": "stream-1",
            "role": "receiver", "protocol": "tcp", "local_host": "10.0.0.2", "local_port": "5201",
            "remote_host": "10.0.0.1", "remote_port": "50100", "interval_start_us": start,
            "interval_end_us": start + 1_000_000, "bytes": 0, "omitted": False})
    payload = prefix + b"".join(b"CNB_ACQ_V1 " + json.dumps(row, separators=(",", ":")).encode() + b"\n"
        for row in rows)
    log = _record(tmp_path, "large-receiver-log", "retrieval/large-iperf.stderr",
        "iperf3-observation-stderr", VmRole.VM_B, payload)
    native = _record(tmp_path, "large-receiver-native", "retrieval/large-iperf.json",
        "iperf3-json", VmRole.VM_A, json.dumps({"start": {}, "cookie": cookie}).encode(), "day-iperf")
    client = _command("iperf3-day-iperf", VmRole.VM_A, "day-iperf")
    receiver = _command(invocation, VmRole.VM_B, None)
    build = _build("iperf3", VmRole.VM_B, "iperf-build")
    sources = bind_acquisition_sources(child_root=tmp_path, run_identity=SimpleNamespace(deployment_id=DEPLOYMENT),
        commands=[client], receiver_command_evidence=[receiver], build_evidence=[build],
        source_artifact_records=[log, native])
    assert len(sources) == 1
    digest_before = hashlib.sha256((tmp_path / log.path).read_bytes()).hexdigest()
    tracemalloc.start()
    first_count = sum(1 for _ in iter_source_windows(tmp_path, sources[0], [log, native], [client, receiver], [build]))
    second_count = sum(1 for _ in iter_source_windows(tmp_path, sources[0], [log, native], [client, receiver], [build]))
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    digest_after = hashlib.sha256((tmp_path / log.path).read_bytes()).hexdigest()
    assert first_count == second_count == count
    assert digest_before == digest_after
    # The existing ArtifactRecord verifier hashes in 1 MiB chunks; replay's
    # bounded parser stays within three chunks despite a >10 MiB observation log.
    assert peak < 3 * 1024 * 1024


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


@pytest.mark.parametrize("mode", ["acceptance", "short_study", "calibration", "diagnostic_day"])
def test_f03_mode_commands_bind_and_replay_raw_sockperf_sources(mode, repository_root, tmp_path):
    """Exercise raw grammar through run_measurement's actual F03 command evidence."""
    resolved = observation(repository_root, mode)
    clock = FakeClock()
    processes = _fixture_processes(clock)
    runtime = context(resolved, tmp_path)
    result = run_measurement(resolved, runtime, processes=processes, clock=clock)
    assert result.outcome == "succeeded"

    records = []
    sockperf_commands = [command for command in result.command_evidence
        if command.request.get("argv", [None])[0] == "sockperf"]
    assert sockperf_commands
    for index, command in enumerate(sockperf_commands):
        action = str(command.result["action_id"])
        handle = next(row for row in processes.handles if row.request.action_id == action)
        log_bytes = handle.stderr_path.read_bytes()
        records.append(_record(tmp_path, f"sock-log-{index}", f"retrieval/{action}.stderr",
            "sockperf-observation-stderr", VmRole.VM_A, log_bytes, command.unit_id,
            runtime.identity.deployment_id))
        native_path = Path(handle.request.argv[handle.request.argv.index("--full-log") + 1])
        native_bytes = native_path.read_bytes()
        records.append(_record(tmp_path, f"sock-native-{index}", f"retrieval/{action}.csv",
            "sockperf-csv", VmRole.VM_A, native_bytes, command.unit_id, runtime.identity.deployment_id))

    tcp_commands = [command for command in result.command_evidence
        if command.request.get("argv", [None])[0] == "iperf3"]
    receiver_action = "f02r-vm_b-listener-iperf3"
    receiver_commands = [_command(receiver_action, VmRole.VM_B, None, runtime.identity.deployment_id)]
    for index, command in enumerate(tcp_commands):
        cookie = f"cookie-{mode}-{index:02d}"
        log_bytes = (ROOT / "iperf-receiver.log").read_text(encoding="utf-8").replace(
            "native-cookie-1", cookie).encode("utf-8")
        records.append(_record(tmp_path, f"iperf-log-{index}", f"retrieval/iperf-{index}.stderr",
            "iperf3-observation-stderr", VmRole.VM_B, log_bytes, None, runtime.identity.deployment_id))
        native_bytes = json.dumps({"start": {}, "cookie": cookie, "end": {}}).encode("utf-8")
        records.append(_record(tmp_path, f"iperf-native-{index}", f"retrieval/iperf-{index}.json",
            "iperf3-json", VmRole.VM_A, native_bytes, command.unit_id, runtime.identity.deployment_id))
    if tcp_commands:
        receiver_commands = [_command(receiver_action, VmRole.VM_B, None, runtime.identity.deployment_id)]

    builds = [_build("sockperf", VmRole.VM_A, "sock-build")]
    if tcp_commands:
        builds.append(_build("iperf3", VmRole.VM_B, "iperf-build"))
    raw_hashes_before = {item.artifact_id: hashlib.sha256((tmp_path / item.path).read_bytes()).hexdigest()
        for item in records}
    sources = bind_acquisition_sources(child_root=tmp_path, run_identity=runtime.identity,
        commands=result.command_evidence, receiver_command_evidence=receiver_commands, build_evidence=builds,
        source_artifact_records=records)
    assert len(sources) == len(sockperf_commands) + len(tcp_commands), (mode,
        [(source.tool, source.session_id, source.invocation_id) for source in sources],
        [command.result["action_id"] for command in tcp_commands])
    all_commands = (*result.command_evidence, *receiver_commands)
    replay_windows = []
    unavailable_sources = []
    for source in sources:
        try:
            replayed = list(iter_source_windows(tmp_path, source, records, all_commands, builds))
        except ValueError:
            unavailable_sources.append(source)
            assert mode == "diagnostic_day" and source.tool == "sockperf"
            continue
        assert len(replayed) == 1
        replay_windows.append((source, replayed[0]))
        if source.tool == "sockperf":
            assert replayed[0].has_positive_inner_support
        else:
            assert replayed[0].omitted is False and replayed[0].has_positive_inner_support
            assert b'"bytes":0' in (tmp_path / source.log_path).read_bytes()
    assert raw_hashes_before == {item.artifact_id: hashlib.sha256((tmp_path / item.path).read_bytes()).hexdigest()
        for item in records}
    if mode == "diagnostic_day":
        probe_windows = sorted((window for source, window in replay_windows if source.tool == "sockperf"),
            key=lambda window: window.start_earliest_ns)
        assert len(tcp_commands) == 1 and len(probe_windows) >= 288
        assert len(unavailable_sources) == 1
        assert all(left.end_latest_ns < right.start_earliest_ns
            for left, right in zip(probe_windows, probe_windows[1:]))
    # Source instrumentation is observation only; do not infer workload delivery.
    assert all(handle.request.argv[0] == "sockperf" for handle in processes.handles if handle.request.argv[0] == "sockperf")


@pytest.mark.parametrize("mutation", ["missing_start", "missing_filter", "missing_end", "failed_command", "interrupted"])
def test_f03_incomplete_source_or_failed_action_cannot_create_replay_window(
        mutation, repository_root, tmp_path):
    resolved = observation(repository_root, "acceptance")
    action = "sockperf-" + resolved.measurement_units[0].unit_id
    failed_action = action if mutation == "failed_command" else None
    hang_action = action if mutation == "interrupted" else None
    processes = _fixture_processes(FakeClock(),
        mutation=None if mutation == "failed_command" else mutation, failed_action=failed_action,
        hang_action=hang_action)
    runtime = context(resolved, tmp_path)
    result = run_measurement(resolved, runtime, processes=processes, clock=processes.clock)
    assert result.command_evidence, (result.outcome, result.failures)
    action = next(row.request.action_id for row in processes.handles if row.request.argv[0] == "sockperf")
    command = next(row for row in result.command_evidence if row.request.get("argv", [None])[0] == "sockperf")
    action = command.result["action_id"]
    if mutation == "interrupted":
        assert command.result["outcome"] == "interrupted"
    handle = next(row for row in processes.handles if row.request.action_id == action)
    raw = handle.stderr_path.read_bytes()
    log = _record(tmp_path, "failed-source-log", "retrieval/failed-source.stderr",
        "sockperf-observation-stderr", VmRole.VM_A, raw, command.unit_id, runtime.identity.deployment_id)
    native_path = Path(handle.request.argv[handle.request.argv.index("--full-log") + 1])
    native = _record(tmp_path, "failed-source-native", "retrieval/failed-source.csv", "sockperf-csv",
        VmRole.VM_A, native_path.read_bytes(), command.unit_id, runtime.identity.deployment_id)
    build = _build("sockperf", VmRole.VM_A, "sock-build")
    sources = bind_acquisition_sources(child_root=tmp_path, run_identity=runtime.identity,
        commands=result.command_evidence, build_evidence=[build], source_artifact_records=[log, native])
    assert sources
    with pytest.raises(ValueError):
        list(iter_source_windows(tmp_path, sources[0], [log, native], result.command_evidence, [build]))


def test_f03_timing_assembly_keeps_cross_host_coverage_unknown_and_attaches_raw_sources(
        repository_root, tmp_path):
    resolved = observation(repository_root, "short_study")
    clock = FakeClock()
    processes = _fixture_processes(clock)
    runtime = context(resolved, tmp_path)
    unit = next(unit for unit in resolved.measurement_units if unit.unit_id == "single_flow")
    sock_action = "sockperf-single_flow"
    receiver_action = "f02r-vm_b-listener-iperf3"
    cookie = "cookie-single-flow"
    sock_log = (ROOT / "sockperf-window.log").read_text(encoding="utf-8").replace(
        "sockperf-short-multiflow", sock_action).encode()
    iperf_log = (ROOT / "iperf-receiver.log").read_text(encoding="utf-8").replace(
        "native-cookie-1", cookie).encode()
    records = [
        _record(tmp_path, "assembly-sock-log", "retrieval/assembly-sock.stderr",
            "sockperf-observation-stderr", VmRole.VM_A, sock_log, unit.unit_id, runtime.identity.deployment_id),
        _record(tmp_path, "assembly-sock-native", "retrieval/assembly-sock.csv",
            "sockperf-csv", VmRole.VM_A, b"native sockperf", unit.unit_id, runtime.identity.deployment_id),
        _record(tmp_path, "assembly-iperf-log", "retrieval/assembly-iperf.stderr",
            "iperf3-observation-stderr", VmRole.VM_B, iperf_log, None, runtime.identity.deployment_id),
        _record(tmp_path, "assembly-iperf-native", "retrieval/assembly-iperf.json",
            "iperf3-json", VmRole.VM_A, json.dumps({"start": {}, "cookie": cookie}).encode(),
            unit.unit_id, runtime.identity.deployment_id),
    ]
    receiver = _command(receiver_action, VmRole.VM_B, None, runtime.identity.deployment_id)
    builds = [_build("sockperf", VmRole.VM_A, "assembly-sock-build"),
        _build("iperf3", VmRole.VM_B, "assembly-iperf-build")]
    runtime = dataclass_replace(runtime, source_artifact_records=tuple(records),
        build_observations=tuple(builds), receiver_command_evidence=(receiver,))
    result = run_measurement(resolved, runtime, processes=processes, clock=clock)
    timing = next(row for row in result.timings if row.unit_id == unit.unit_id)
    assert timing.acquisition_sources.value is not None
    assert {row.tool for row in timing.acquisition_sources.value} == {"sockperf", "iperf3"}
    replay_note = next(note for note in result.timing_notes
        if note.get("unit_id") == unit.unit_id and "acquisition_coverage_lower_bound" in note)
    assert replay_note["acquisition_coverage_lower_bound"] is None
    assert "clock domains" in replay_note["acquisition_coverage_reason"]
