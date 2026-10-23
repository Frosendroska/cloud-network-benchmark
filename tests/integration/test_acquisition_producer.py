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


def _tool_name(argv):
    return Path(argv[0]).name


def _record(root: Path, artifact_id: str, path: str, kind: str, producer: VmRole,
            payload: bytes, unit_id: str | None = None, deployment_id: str = DEPLOYMENT) -> ArtifactRecord:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return ArtifactRecord(artifact_id=artifact_id, path=path, kind=kind, producer=producer,
        unit_id=unit_id, deployment_id=deployment_id, closed=True, size_bytes=Evidence(value=len(payload)),
        sha256=StringEvidence(value=hashlib.sha256(payload).hexdigest()), retrieval_state="verified")


def _record_stream(root: Path, artifact_id: str, path: str, kind: str, producer: VmRole,
                   chunks, unit_id: str | None = None, deployment_id: str = DEPLOYMENT) -> ArtifactRecord:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with target.open("wb") as stream:
        for chunk in chunks:
            stream.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    return ArtifactRecord(artifact_id=artifact_id, path=path, kind=kind, producer=producer,
        unit_id=unit_id, deployment_id=deployment_id, closed=True, size_bytes=Evidence(value=size),
        sha256=StringEvidence(value=digest.hexdigest()), retrieval_state="verified")


def _native_iperf(cookie: str, *, duration: int = 60, stream: int = 17,
                  start: float = 1.0, end: float = 1.0, byte_count: int = 0,
                  local_host: str = "10.0.0.2", remote_host: str = "10.0.0.1",
                  interval_count: int = 1, omit: int = 5, include_omitted: bool = True) -> bytes:
    settings = {"protocol": "TCP", "num_streams": 1, "omit": omit, "duration": duration,
        "interval": 1, "blksize": 131072, "reverse": False}
    rows = [{"socket": stream, "start": start + index, "end": end + index,
        "bytes": byte_count, "omitted": False, "sender": False} for index in range(interval_count)]
    if include_omitted:
        rows.insert(0, {"socket": stream, "start": 0, "end": 4,
            "bytes": 250, "omitted": True, "sender": False})
    receiver = {"start": {"cookie": cookie, "test_start": settings, "connected": [{"socket": stream,
        "local_host": local_host, "local_port": 5201, "remote_host": remote_host,
        "remote_port": 50100}]}, "intervals": [{"streams": [row]} for row in rows]}
    return (json.dumps({"start": {"cookie": cookie, "test_start": settings},
        "server_output_json": receiver, "end": {}}, indent=2) + "\n").encode()


def _native_iperf_client_jsonl(cookie: str, *, duration: int = 60, omit: int = 5) -> bytes:
    settings = {"protocol": "TCP", "num_streams": 1, "omit": omit, "duration": duration,
        "interval": 1, "blksize": 131072, "reverse": False}
    return (json.dumps({"event": "start", "data": {"cookie": cookie, "test_start": settings}})
        + "\n" + json.dumps({"event": "end", "data": {}}) + "\n").encode()


def _native_iperf_server_jsonl(cookie: str, *, duration: int = 60, stream: int = 17,
                               interval_count: int = 1, start: float = 1.0, end: float = 1.0,
                               byte_count: int = 0, omit: int = 5,
                               include_omitted: bool = True) -> bytes:
    client = json.loads(_native_iperf(cookie, duration=duration, stream=stream,
        interval_count=interval_count, start=start, end=end, byte_count=byte_count,
        omit=omit, include_omitted=include_omitted))
    receiver = client["server_output_json"]
    events = [json.dumps({"event": "start", "data": receiver["start"]})]
    for interval in receiver["intervals"]:
        events.append(json.dumps({"event": "interval", "data": interval}))
    events.append(json.dumps({"event": "end", "data": client["end"]}))
    return ("\n".join(events) + "\n").encode()


def _two_stream_iperf_tail_fixture(cookie: str, *, final_duration_us: int = 1_000,
                                   final_bytes: tuple[int, int] = (0, 0),
                                   include_final_native: bool = False,
                                   final_skew_us: int = 0) -> tuple[bytes, bytes]:
    """Two source-observed streams with omitted, normal, and final intervals."""
    raw = [json.loads(line[len(b"CNB_ACQ_V1 "):])
           for line in (ROOT / "iperf-receiver.log").read_bytes().splitlines()]
    for value in raw:
        value["cookie"] = cookie
    anchor, settings = raw[:2]
    settings["num_streams"] = 2
    initial = next(row for row in raw if row.get("record_kind") == "stats_origin" and row.get("phase") == "initial")
    post = next(row for row in raw if row.get("record_kind") == "stats_origin" and row.get("phase") == "post_omit")
    origins = []
    for stream_id in ("17", "18"):
        origins.append({**initial, "stream_id": stream_id})
    omitted = next(row for row in raw if row.get("record_kind") == "receiver_interval" and row.get("omitted"))
    normal = {**next(row for row in raw if row.get("record_kind") == "receiver_interval" and not row.get("omitted")),
              "interval_start_us": 5_000_000, "interval_end_us": 6_000_000, "bytes": 0}
    final_start = 6_000_000
    def observation_for_stream(value, index):
        return {**value, "stream_id": ("17", "18")[index],
            "local_port": str(5201 + index), "remote_port": str(50100 + index)}
    normal_rows = [observation_for_stream(omitted, index) for index in range(2)]
    post_rows = [observation_for_stream(post, index) for index in range(2)]
    measured_rows = [observation_for_stream(normal, index) for index in range(2)]
    final_rows = [observation_for_stream({**normal, "interval_start_us": final_start,
        "interval_end_us": final_start + final_duration_us + (final_skew_us if index else 0),
        "bytes": final_bytes[index]}, index)
        for index in range(2)]
    observations = [anchor, settings, *origins, *normal_rows, *post_rows,
        *measured_rows, *final_rows]
    log = b"".join(b"CNB_ACQ_V1 " + json.dumps(row, separators=(",", ":")).encode() + b"\n"
        for row in observations)

    client_settings = {"protocol": "TCP", "num_streams": 2, "omit": 5, "duration": 60,
        "interval": 1, "blksize": 131072, "reverse": False}
    connections = [{"socket": stream_id, "local_host": "10.0.0.2", "local_port": 5201 + index,
        "remote_host": "10.0.0.1", "remote_port": 50100 + index}
        for index, stream_id in enumerate((17, 18))]
    native_rows = [
        [{"socket": stream_id, "start": 0, "end": 4, "bytes": 250, "omitted": True, "sender": False}
         for stream_id in (17, 18)],
        [{"socket": stream_id, "start": 0, "end": 1, "bytes": 0, "omitted": False, "sender": False}
         for stream_id in (17, 18)],
    ]
    if include_final_native:
        native_rows.append([
            {"socket": stream_id, "start": 1, "end": 1 + final_duration_us / 1_000_000,
             "bytes": final_bytes[index], "omitted": False, "sender": False}
            for index, stream_id in enumerate((17, 18))])
    receiver = {"start": {"cookie": cookie, "test_start": client_settings, "connected": connections},
        "intervals": [{"streams": rows} for rows in native_rows]}
    client = {"start": {"cookie": cookie, "test_start": client_settings},
        "server_output_json": receiver, "end": {}}
    return log, (json.dumps(client, indent=2) + "\n").encode()


def _command(action: str, producer: VmRole, unit_id: str | None, deployment_id: str = DEPLOYMENT):
    if producer == VmRole.VM_B and action == "f02r-vm_b-listener-iperf3":
        argv = ["systemd-run", "--property=Environment=CNB_ACQUISITION_ID=f02r-vm_b-listener-iperf3",
            "/opt/cnb/bin/iperf3", "-s", "--json-stream", "--forceflush"]
    else:
        argv = [f"/opt/cnb/bin/{'sockperf' if action.startswith('sockperf') else 'iperf3'}"]
    return SimpleNamespace(request={"action_id": action, "argv": argv,
        "acquisition_invocation_id": action}, result={"action_id": action},
        producer=producer, unit_id=unit_id, deployment_id=deployment_id)


def _fixture_processes(clock, *, mutation=None, failed_action=None, hang_action=None):
    """Run the real F03 producer against pinned-patch grammar fixtures."""
    from f03_support import FakeProcesses

    class SourceFixtureProcesses(FakeProcesses):
        def __init__(self, clock):
            super().__init__(clock, failed_action=failed_action, hang_action=hang_action)

        def start(self, request, *, stdout_path, stderr_path):
            handle = super().start(request, stdout_path=stdout_path, stderr_path=stderr_path)
            if _tool_name(request.argv) == "sockperf":
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
    from cloud_network_benchmark.bootstrap import _required_capabilities
    semantics = "sockperf-3.10-enabled-v1" if tool == "sockperf" else "iperf-3.17.1-stats-v1"
    commit = "5ebd327da983225321818c0355db922515e026bd" if tool == "sockperf" else "2acfcfe94e928e74542c9f107e02aa6dd4748a79"
    patch = ("3f0bb59a3c70b247e5ebde77254e1427c35dc6ec39e2a78a3c779cff7015a285" if tool == "sockperf"
             else "11b96daadb4a1f17dc24376bb3d0d7feec330efeedc550b1979b1566e9b81661")
    source_sha = ("a35a1daf310551b9f675af4fb7d95a6a10389fda4c7605d70a21554be0773f8e" if tool == "sockperf"
                  else "d6b4d2c7b20157c5b7fbd2de66f22bd3428958b5f2e28899151b6e22745886fe")
    commit_path = ("tool_patches/sockperf-3.10-acquisition.patch" if tool == "sockperf"
        else "tool_patches/iperf-3.17.1-acquisition.patch")
    compiler_version = "gcc (Ubuntu 13.2.0-23ubuntu4) 13.2.0\nCopyright (C) 2023 Free Software Foundation, Inc."
    binary_version = f"{tool} 3.17.1\nLinux synthetic-host 6.8.0\nCompiler: gcc 13.2.0"
    build_configuration = f"CC=${{CC:-cc}}; compiler={compiler_version}; ./configure --prefix=/opt/cnb; make -j2"
    identity_bytes = ("CNB_BUILD_V1\n" + "\n".join((f"tool={tool}", f"source_reference={('sockperf-' if tool == 'sockperf' else 'iperf-')}{commit}",
        f"source_sha256={source_sha}", f"upstream_commit={commit}", f"patch_path={commit_path}", f"patch_sha256={patch}",
        "compiler_version_b64=" + __import__("base64").b64encode(compiler_version.encode()).decode(),
        "build_configuration_b64=" + __import__("base64").b64encode(build_configuration.encode()).decode(),
        f"binary_path=/opt/cnb/bin/{tool}", "binary_version_b64=" + __import__("base64").b64encode(binary_version.encode()).decode(),
        f"binary_sha256={'b' * 64}", "observation_capability=CNB_ACQ_V1",
        f"observation_semantics={semantics}", f"capabilities={','.join(_required_capabilities(tool))}")) + "\n")
    return SimpleNamespace(role=producer, tool=tool, artifact_id=artifact_id,
        source_reference=f"{('sockperf-' if tool == 'sockperf' else 'iperf-')}{commit}", source_sha256=source_sha,
        patch_path=commit_path,
        observation_capability="CNB_ACQ_V1", observation_semantics=semantics,
        upstream_commit=commit, patch_sha256=patch, binary_sha256="b" * 64,
        binary_path=f"/opt/cnb/bin/{tool}", binary_version=binary_version,
        capabilities=_required_capabilities(tool),
        build_configuration=build_configuration, compiler_version=compiler_version, identity_bytes=identity_bytes)


def _build_records(root, deployment, builds):
    from cloud_network_benchmark.bootstrap import materialize_build_identity_artifacts
    return list(materialize_build_identity_artifacts(root, deployment, builds))


def test_f03_source_binder_replays_closed_sockperf_and_persistent_iperf_sources(tmp_path):
    sock_log = (ROOT / "sockperf-window.log").read_bytes()
    iperf_log = (ROOT / "iperf-receiver.log").read_bytes()
    sock_csv = b"Sockperf 3.10 native CSV fixture\n"
    client_json = _native_iperf("native-cookie-1")
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
    records.extend(_build_records(tmp_path, DEPLOYMENT, builds))
    sources = bind_acquisition_sources(child_root=tmp_path, run_identity=identity, commands=commands,
        receiver_command_evidence=receiver, build_evidence=builds, source_artifact_records=records)
    assert {(row.tool, row.session_id) for row in sources} == {("sockperf", None), ("iperf3", "native-cookie-1")}
    all_commands = (*commands, *receiver)
    windows = {source.tool: list(iter_source_windows(tmp_path, source, records, all_commands, builds))
               for source in sources}
    assert windows["sockperf"][0].has_positive_inner_support
    assert windows["iperf3"][0].omitted is True
    assert windows["iperf3"][1].omitted is False


def test_run_bootstrap_build_observation_materializes_and_replays_exact_multiline_identity(tmp_path):
    import base64
    from cloud_network_benchmark.bootstrap import (BootstrapConfig, SourceBuild, run_bootstrap,
        _required_capabilities)
    from fixtures.f02r.support import BootstrapRunner, bootstrap_config, connection, deadline, result

    patch_root = Path(__file__).parents[2] / "src/cloud_network_benchmark/tool_patches"
    sock_patch = patch_root / "sockperf-3.10-acquisition.patch"
    iperf_patch = patch_root / "iperf-3.17.1-acquisition.patch"
    sock_commit, iperf_commit = "5ebd327da983225321818c0355db922515e026bd", "2acfcfe94e928e74542c9f107e02aa6dd4748a79"
    config = bootstrap_config().model_copy(update={"sources": [
        SourceBuild(tool="sockperf", source_url=f"https://github.com/Mellanox/sockperf/archive/{sock_commit}.tar.gz",
            source_reference=f"sockperf-{sock_commit}", archive_name="sockperf.tar.gz",
            source_sha256="a35a1daf310551b9f675af4fb7d95a6a10389fda4c7605d70a21554be0773f8e",
            upstream_commit=sock_commit, observation_patch_path="tool_patches/sockperf-3.10-acquisition.patch",
            observation_patch_sha256=hashlib.sha256(sock_patch.read_bytes()).hexdigest()),
        SourceBuild(tool="iperf3", source_url=f"https://github.com/esnet/iperf/archive/{iperf_commit}.tar.gz",
            source_reference=f"iperf-{iperf_commit}", archive_name="iperf3.tar.gz",
            source_sha256="d6b4d2c7b20157c5b7fbd2de66f22bd3428958b5f2e28899151b6e22745886fe",
            upstream_commit=iperf_commit, observation_patch_path="tool_patches/iperf-3.17.1-acquisition.patch",
            observation_patch_sha256=hashlib.sha256(iperf_patch.read_bytes()).hexdigest())]})

    class MultilineBuildRunner(BootstrapRunner):
        compiler = "gcc (Ubuntu 13.3.0-6ubuntu2) 13.3.0\nCopyright (C) 2024 Free Software Foundation, Inc."
        def run(self, request):
            aid = request.action_id
            tool = "sockperf" if "sockperf" in aid else "iperf3"
            source = next(item for item in config.sources if item.tool == tool)
            version = ("sockperf, version 3.10\nCopyright (c) Mellanox Technologies\nBuild options: acquisition"
                if tool == "sockperf" else "iperf 3.17.1\nLinux synthetic-host 6.8.0\nCompiler: gcc 13.3.0")
            if "CNB_BUILD_V1" in request.argv[-1]:
                build_config = (f"CC=${{CC:-cc}}; compiler={self.compiler}; "
                    f"./configure --prefix={config.install_prefix}; make -j2")
                semantics = "sockperf-3.10-enabled-v1" if tool == "sockperf" else "iperf-3.17.1-stats-v1"
                fields = ["CNB_BUILD_V1", f"tool={tool}", f"source_reference={source.source_reference}",
                    f"source_sha256={source.source_sha256}", f"upstream_commit={source.upstream_commit}",
                    f"patch_path={source.observation_patch_path}", f"patch_sha256={source.observation_patch_sha256}",
                    "compiler_version_b64=" + base64.b64encode(self.compiler.encode()).decode(),
                    "build_configuration_b64=" + base64.b64encode(build_config.encode()).decode(),
                    f"binary_path={config.install_prefix}/bin/{tool}",
                    "binary_version_b64=" + base64.b64encode(version.encode()).decode(),
                    f"binary_sha256={'c' * 64}", "observation_capability=CNB_ACQ_V1",
                    f"observation_semantics={semantics}",
                    "capabilities=" + ",".join(_required_capabilities(tool))]
                return result(aid, "\n".join(fields))
            if aid.endswith(f"{tool}-5"):
                return result(aid, self.compiler + "\n")
            if aid.endswith(f"{tool}-6"):
                return result(aid, version + "\n")
            if aid.endswith(f"{tool}-2"):
                return result(aid, f"{source.source_sha256} /var/lib/cnb/work/{source.archive_name}")
            if aid.endswith("sockperf-11") or aid.endswith("iperf3-9"):
                return result(aid, "CNB_ACQ_V1")
            response = super().run(request)
            if aid.endswith("listener-verify-iperf3") or aid.endswith("listener-config-iperf3"):
                return result(aid, response.stdout + "\nEnvironment=CNB_ACQUISITION_ID=f02r-vm_b-listener-iperf3\n")
            return response

    bootstrap = run_bootstrap(connection(tmp_path), config, MultilineBuildRunner(),
        deadline=deadline(), clock=lambda: 100)
    assert bootstrap.succeeded
    production_build = next(item for item in bootstrap.builds if item.role == "vm_b" and item.tool == "iperf3")
    production_sockperf_build = next(item for item in bootstrap.builds if item.role == "vm_a" and item.tool == "sockperf")
    expected_configuration = (f"CC=${{CC:-cc}}; compiler={production_build.compiler_version}; "
        f"./configure --prefix={config.install_prefix}; make -j2")
    assert production_build.build_configuration == expected_configuration

    cookie = "actual-bootstrap-build-cookie"
    log = (ROOT / "iperf-receiver.log").read_bytes().replace(b"native-cookie-1", cookie.encode())
    native = _native_iperf(cookie)
    records = [
        _record(tmp_path, "actual-bootstrap-sock-log", "retrieval/actual-bootstrap-sockperf.stderr",
            "sockperf-observation-stderr", VmRole.VM_A,
            (ROOT / "sockperf-window.log").read_bytes(), "short-multiflow"),
        _record(tmp_path, "actual-bootstrap-sock-csv", "measurements/short-multiflow/actual-bootstrap-sockperf.csv",
            "sockperf-csv", VmRole.VM_A, b"Sockperf 3.10 native CSV fixture\n", "short-multiflow"),
        _record(tmp_path, "actual-bootstrap-log", "retrieval/actual-bootstrap.stderr",
            "iperf3-observation-stderr", VmRole.VM_B, log),
        _record(tmp_path, "actual-bootstrap-native", "retrieval/actual-bootstrap.json",
            "iperf3-json", VmRole.VM_A, native, "short-multiflow"),
        *_build_records(tmp_path, DEPLOYMENT, [production_build, production_sockperf_build]),
    ]
    client = _command("iperf3-short-multiflow", VmRole.VM_A, "short-multiflow")
    sockperf_client = _command("sockperf-short-multiflow", VmRole.VM_A, "short-multiflow")
    sockperf_client.request["argv"] = [production_sockperf_build.binary_path, "under-load"]
    sockperf_client.request["acquisition_invocation_id"] = "sockperf-short-multiflow"
    receiver = _command("f02r-vm_b-listener-iperf3", VmRole.VM_B, None)
    receiver.request["argv"] = ["systemd-run",
        "--property=Environment=CNB_ACQUISITION_ID=f02r-vm_b-listener-iperf3",
        f"{production_build.binary_path}", "-s", "--json-stream", "--forceflush"]
    builds = [production_build, production_sockperf_build]
    sources = bind_acquisition_sources(child_root=tmp_path,
        run_identity=SimpleNamespace(deployment_id=DEPLOYMENT), commands=[client, sockperf_client],
        receiver_command_evidence=[receiver], build_evidence=builds, source_artifact_records=records)
    assert {source.tool for source in sources} == {"iperf3", "sockperf"}
    all_commands = [client, sockperf_client, receiver]
    replayed = {source.tool: list(iter_source_windows(tmp_path, source, records,
        all_commands, builds)) for source in sources}
    assert len(replayed["iperf3"]) == 2
    assert replayed["sockperf"][0].has_positive_inner_support


@pytest.mark.parametrize("mutation", ["cookie_only", "native_endpoint", "native_settings",
    "native_stream", "observation_endpoint", "observation_bytes", "client_action_collision"])
def test_iperf_replay_requires_native_receiver_corroboration(mutation, tmp_path):
    log_bytes = (ROOT / "iperf-receiver.log").read_bytes()
    native_bytes = _native_iperf("native-cookie-1")
    if mutation == "cookie_only":
        native_bytes = b'{"start":{"cookie":"native-cookie-1"},"intervals":[]}\n'
    elif mutation == "native_endpoint":
        native_bytes = _native_iperf("native-cookie-1", remote_host="10.0.0.99")
    elif mutation == "native_settings":
        native_bytes = _native_iperf("native-cookie-1", omit=4)
    elif mutation == "native_stream":
        native_bytes = _native_iperf("native-cookie-1", stream=18)
    log = _record(tmp_path, "iperf-log", "retrieval/iperf.stderr", "iperf3-observation-stderr",
        VmRole.VM_B, log_bytes)
    native = _record(tmp_path, "iperf-client", "retrieval/iperf.json", "iperf3-json",
        VmRole.VM_A, native_bytes, "short-multiflow")
    receiver = _command("f02r-vm_b-listener-iperf3", VmRole.VM_B, None)
    client = _command("iperf3-short-multiflow", VmRole.VM_A, "short-multiflow")
    build = _build("iperf3", VmRole.VM_B, "iperf-build")
    records = [log, native, *_build_records(tmp_path, DEPLOYMENT, [build])]
    sources = bind_acquisition_sources(child_root=tmp_path,
        run_identity=SimpleNamespace(deployment_id=DEPLOYMENT), commands=[client],
        receiver_command_evidence=[receiver], build_evidence=[build], source_artifact_records=records)
    assert len(sources) == 1
    if mutation == "observation_bytes":
        changed = log_bytes.replace(b'"bytes":0,"omitted":false', b'"bytes":1,"omitted":false')
        assert changed != log_bytes
        log = _record(tmp_path, "iperf-log", "retrieval/iperf.stderr", "iperf3-observation-stderr",
            VmRole.VM_B, changed)
        records = [log, native, *records[2:]]
    elif mutation == "observation_endpoint":
        changed = log_bytes.replace(b'"remote_host":"10.0.0.1"', b'"remote_host":"10.0.0.99"')
        assert changed != log_bytes
        log = _record(tmp_path, "iperf-log", "retrieval/iperf.stderr", "iperf3-observation-stderr",
            VmRole.VM_B, changed)
        records = [log, native, *records[2:]]
    if mutation == "client_action_collision":
        collision = _command("iperf3-short-multiflow-extra", VmRole.VM_A, "short-multiflow")
        with pytest.raises(ValueError, match="one client command"):
            list(iter_source_windows(tmp_path, sources[0], records, [collision, receiver], [build]))
        return
    with pytest.raises(ValueError, match="native iperf|native iperf JSON"):
        list(iter_source_windows(tmp_path, sources[0], records, [client, receiver], [build]))


@pytest.mark.parametrize("mutation", ["native_duration", "observer_stream_count", "observer_origin", "native_omitted"])
def test_iperf_shared_native_prerequisite_failure_yields_no_partial_windows(mutation, tmp_path):
    log_bytes = (ROOT / "iperf-receiver.log").read_bytes()
    native_bytes = _native_iperf("native-cookie-1")
    if mutation == "native_duration":
        native_bytes = _native_iperf("native-cookie-1", duration=999)
    elif mutation == "observer_stream_count":
        log_bytes = log_bytes.replace(b'"num_streams":1', b'"num_streams":2')
    elif mutation == "observer_origin":
        log_bytes = log_bytes.replace(b'"origin_us":5000000', b'"origin_us":6000000')
    elif mutation == "native_omitted":
        value = json.loads(native_bytes)
        value["server_output_json"]["intervals"][0]["streams"][0]["bytes"] = 251
        native_bytes = json.dumps(value).encode()
    log = _record(tmp_path, "iperf-log", "retrieval/iperf.stderr", "iperf3-observation-stderr",
        VmRole.VM_B, log_bytes)
    native = _record(tmp_path, "iperf-client", "retrieval/iperf.json", "iperf3-json",
        VmRole.VM_A, native_bytes, "short-multiflow")
    receiver = _command("f02r-vm_b-listener-iperf3", VmRole.VM_B, None)
    client = _command("iperf3-short-multiflow", VmRole.VM_A, "short-multiflow")
    build = _build("iperf3", VmRole.VM_B, "iperf-build")
    records = [log, native, *_build_records(tmp_path, DEPLOYMENT, [build])]
    sources = bind_acquisition_sources(child_root=tmp_path,
        run_identity=SimpleNamespace(deployment_id=DEPLOYMENT), commands=[client],
        receiver_command_evidence=[receiver], build_evidence=[build], source_artifact_records=records)
    assert len(sources) == 1
    windows = []
    with pytest.raises(ValueError):
        for window in iter_source_windows(tmp_path, sources[0], records, [client, receiver], [build]):
            windows.append(window)
    assert windows == []


@pytest.mark.parametrize("tail", ["failed_command", "truncated_observation", "truncated_native_jsonl"])
def test_iperf_closed_prefix_survives_only_an_explicit_incomplete_tail(tail, tmp_path):
    log_bytes = (ROOT / "iperf-receiver.log").read_bytes()
    if tail == "truncated_observation":
        log_bytes += b'CNB_ACQ_V1 {"format":1'
    log = _record(tmp_path, "iperf-log", "retrieval/iperf.stderr", "iperf3-observation-stderr",
        VmRole.VM_B, log_bytes)
    if tail == "truncated_native_jsonl":
        client_native = _record(tmp_path, "iperf-client", "retrieval/iperf-client.jsonl",
            "iperf3-client-jsonl", VmRole.VM_A, _native_iperf_client_jsonl("native-cookie-1"), "short-multiflow")
        server_bytes = _native_iperf_server_jsonl("native-cookie-1") + b'{"event":"interval","data":'
        receiver_native = _record(tmp_path, "iperf-server", "retrieval/iperf-server.jsonl",
            "iperf3-server-jsonl", VmRole.VM_B, server_bytes)
        native_records = [client_native, receiver_native]
    else:
        native = _record(tmp_path, "iperf-client", "retrieval/iperf.json", "iperf3-json",
            VmRole.VM_A, _native_iperf("native-cookie-1"), "short-multiflow")
        native_records = [native]
    receiver = _command("f02r-vm_b-listener-iperf3", VmRole.VM_B, None)
    client = _command("iperf3-short-multiflow", VmRole.VM_A, "short-multiflow")
    if tail == "failed_command":
        receiver.result["outcome"] = "failed"
    build = _build("iperf3", VmRole.VM_B, "iperf-build")
    records = [log, *native_records, *_build_records(tmp_path, DEPLOYMENT, [build])]
    sources = bind_acquisition_sources(child_root=tmp_path,
        run_identity=SimpleNamespace(deployment_id=DEPLOYMENT), commands=[client],
        receiver_command_evidence=[receiver], build_evidence=[build], source_artifact_records=records)
    assert len(sources) == 1
    windows = []
    with pytest.raises(ValueError, match="unknown tail|truncated acquisition record|truncated tail"):
        for window in iter_source_windows(tmp_path, sources[0], records, [client, receiver], [build]):
            windows.append(window)
    assert len(windows) == 2


def _bind_two_stream_iperf_fixture(tmp_path, *, final_duration_us=1_000,
                                   final_bytes=(0, 0), include_final_native=False,
                                   receiver_outcome="succeeded", final_skew_us=0):
    cookie = "native-cookie-two-stream"
    log_bytes, native_bytes = _two_stream_iperf_tail_fixture(cookie,
        final_duration_us=final_duration_us, final_bytes=final_bytes,
        include_final_native=include_final_native, final_skew_us=final_skew_us)
    log = _record(tmp_path, "two-stream-log", "retrieval/two-stream-iperf.stderr",
        "iperf3-observation-stderr", VmRole.VM_B, log_bytes)
    native = _record(tmp_path, "two-stream-client", "retrieval/two-stream-iperf.json",
        "iperf3-json", VmRole.VM_A, native_bytes, "short-multiflow")
    receiver = _command("f02r-vm_b-listener-iperf3", VmRole.VM_B, None)
    receiver.result["outcome"] = receiver_outcome
    client = _command("iperf3-short-multiflow", VmRole.VM_A, "short-multiflow")
    build = _build("iperf3", VmRole.VM_B, "two-stream-build")
    records = [log, native, *_build_records(tmp_path, DEPLOYMENT, [build])]
    sources = bind_acquisition_sources(child_root=tmp_path,
        run_identity=SimpleNamespace(deployment_id=DEPLOYMENT), commands=[client],
        receiver_command_evidence=[receiver], build_evidence=[build], source_artifact_records=records)
    assert len(sources) == 1
    return sources[0], records, [client, receiver], [build]


def test_iperf_native_reporter_short_all_zero_tail_is_explicit_and_keeps_closed_prefix(tmp_path):
    source, records, commands, builds = _bind_two_stream_iperf_fixture(tmp_path)
    windows = []
    with pytest.raises(ValueError, match=r"filtered the final all-stream zero-byte interval.*1000 us"):
        for window in iter_source_windows(tmp_path, source, records, commands, builds):
            windows.append(window)
    # Both omitted rows and the normal one-second zero-byte reports remain
    # independently correlated; only the final 1 ms interval is unsupported.
    assert len(windows) == 4
    assert sum(window.omitted is False for window in windows) == 2


def test_iperf_native_reporter_accepts_per_stream_skew_on_terminal_zero_tail(tmp_path):
    source, records, commands, builds = _bind_two_stream_iperf_fixture(
        tmp_path, final_skew_us=1)
    windows = []
    with pytest.raises(ValueError, match=r"filtered the final all-stream zero-byte interval.*stream 18 \[6000000, 6001001\)"):
        for window in iter_source_windows(tmp_path, source, records, commands, builds):
            windows.append(window)
    assert len(windows) == 4


@pytest.mark.parametrize(("duration", "byte_counts"), [
    (1_000, (0, 1)),  # a positive stream prevents all-stream suppression
    (1_000_000, (0, 0)),  # normal one-second zero-byte intervals are reported
])
def test_iperf_zero_byte_reporting_is_not_overfiltered(duration, byte_counts, tmp_path):
    source, records, commands, builds = _bind_two_stream_iperf_fixture(tmp_path,
        final_duration_us=duration, final_bytes=byte_counts, include_final_native=True)
    windows = list(iter_source_windows(tmp_path, source, records, commands, builds))
    assert len(windows) == 6
    assert sum(window.omitted is False for window in windows) == 4


def test_iperf_short_zero_tail_with_failed_receiver_is_unknown_not_reporter_filtered(tmp_path):
    source, records, commands, builds = _bind_two_stream_iperf_fixture(tmp_path,
        receiver_outcome="failed")
    windows = []
    with pytest.raises(ValueError, match="unknown unmatched interval tail") as error:
        for window in iter_source_windows(tmp_path, source, records, commands, builds):
            windows.append(window)
    assert "filtered" not in str(error.value)
    assert len(windows) == 4


def _truncated_diagnostic_client(cookie: str, duration: int) -> bytes:
    settings = {"protocol": "TCP", "num_streams": 1, "omit": 5, "duration": duration,
        "interval": 1, "blksize": 131072, "reverse": False}
    start = json.dumps({"event": "start", "data": {"cookie": cookie, "test_start": settings}})
    return (start + "\n" + '{"event":"end","data":').encode()


def test_truncated_diagnostic_client_retains_only_correlated_closed_receiver_prefix(
        repository_root, tmp_path):
    cookie = "diagnostic-client-truncated"
    log_bytes = ((ROOT / "iperf-receiver.log").read_bytes()
        .replace(b'"duration":60', b'"duration":86400')
        .replace(b'"cookie":"native-cookie-1"', f'"cookie":"{cookie}"'.encode()))
    log = _record(tmp_path, "truncated-client-log", "retrieval/diagnostic-iperf.stderr",
        "iperf3-observation-stderr", VmRole.VM_B, log_bytes)
    client_native = _record(tmp_path, "truncated-client-native", "retrieval/diagnostic-client.jsonl",
        "iperf3-client-jsonl", VmRole.VM_A, _truncated_diagnostic_client(cookie, 86400), "diagnostic-day")
    server_native = _record(tmp_path, "truncated-client-server", "retrieval/diagnostic-server.jsonl",
        "iperf3-server-jsonl", VmRole.VM_B,
        _native_iperf_server_jsonl(cookie, duration=86400))
    receiver = _command("f02r-vm_b-listener-iperf3", VmRole.VM_B, None)
    client = _command("iperf3-diagnostic-day", VmRole.VM_A, "diagnostic-day")
    build = _build("iperf3", VmRole.VM_B, "diagnostic-iperf-build")
    build_records = _build_records(tmp_path, DEPLOYMENT, [build])
    records = [log, client_native, server_native, *build_records]
    sources = bind_acquisition_sources(child_root=tmp_path,
        run_identity=SimpleNamespace(deployment_id=DEPLOYMENT), commands=[client],
        receiver_command_evidence=[receiver], build_evidence=[build], source_artifact_records=records)
    assert len(sources) == 1
    raw_hashes_before = {item.artifact_id: hashlib.sha256((tmp_path / item.path).read_bytes()).hexdigest()
        for item in records}
    windows = []
    with pytest.raises(ValueError, match="A-side diagnostic iperf client JSONL has an unknown truncated final event"):
        for window in iter_source_windows(tmp_path, sources[0], records, [client, receiver], [build]):
            windows.append(window)
    assert len(windows) == 2 and windows[0].omitted is True and windows[1].omitted is False
    assert raw_hashes_before == {item.artifact_id: hashlib.sha256((tmp_path / item.path).read_bytes()).hexdigest()
        for item in records}

    resolved = observation(repository_root, "diagnostic_day")
    runtime_context = context(resolved, tmp_path)
    deployment_id = runtime_context.identity.deployment_id
    # F03 uses its own deployment identity; rematerialize the same immutable
    # bytes with that identity so the binder exercises the actual runtime scope.
    f03_records = [
        _record(tmp_path, "truncated-client-log", "retrieval/diagnostic-iperf.stderr",
            "iperf3-observation-stderr", VmRole.VM_B, log_bytes, None, deployment_id),
        _record(tmp_path, "truncated-client-native", "retrieval/diagnostic-client.jsonl",
            "iperf3-client-jsonl", VmRole.VM_A, _truncated_diagnostic_client(cookie, 86400), "diagnostic-day", deployment_id),
        _record(tmp_path, "truncated-client-server", "retrieval/diagnostic-server.jsonl",
            "iperf3-server-jsonl", VmRole.VM_B, _native_iperf_server_jsonl(cookie, duration=86400), None, deployment_id),
    ]
    f03_receiver = _command("f02r-vm_b-listener-iperf3", VmRole.VM_B, None, deployment_id)
    f03_receiver.result["outcome"] = "succeeded"
    runtime = dataclass_replace(runtime_context, source_artifact_records=tuple(f03_records),
        build_observations=(build,), receiver_command_evidence=(f03_receiver,))
    clock = FakeClock()
    result = run_measurement(resolved, runtime, processes=_fixture_processes(clock), clock=clock)
    timing = next(row for row in result.timings if row.unit_id == "diagnostic-day")
    assert timing.acquisition_sources.value is not None
    assert any(source.tool == "iperf3" for source in timing.acquisition_sources.value)
    note = next(row for row in result.timing_notes
        if row.get("unit_id") == "diagnostic-day" and "acquisition_coverage_reason" in row)
    assert "unknown tail" in note["acquisition_coverage_reason"]
    assert "truncated final event" in note["acquisition_coverage_reason"]


@pytest.mark.parametrize("mutation", ["duplicate_start", "contradictory_cookie", "invalid_complete_record"])
def test_client_jsonl_rejects_ambiguous_or_contradictory_complete_prefix(mutation, tmp_path):
    cookie = "diagnostic-client-contradiction"
    log_bytes = ((ROOT / "iperf-receiver.log").read_bytes()
        .replace(b'"cookie":"native-cookie-1"', f'"cookie":"{cookie}"'.encode()))
    log = _record(tmp_path, "contradictory-client-log", "retrieval/contradictory-client.stderr",
        "iperf3-observation-stderr", VmRole.VM_B, log_bytes)
    settings = {"protocol": "TCP", "num_streams": 1, "omit": 5, "duration": 86400,
        "interval": 1, "blksize": 131072, "reverse": False}
    start = json.dumps({"event": "start", "data": {"cookie": cookie, "test_start": settings}})
    if mutation == "duplicate_start":
        client_bytes = (start + "\n" + start + "\n" + '{"event":"end","data":').encode()
    elif mutation == "contradictory_cookie":
        wrong = json.dumps({"event": "start", "data": {"cookie": "other-cookie", "test_start": settings}})
        client_bytes = (start + "\n" + wrong + "\n").encode()
    else:
        client_bytes = (start + '\n{"event":"interval","data":}\n').encode()
    client_native = _record(tmp_path, "contradictory-client-native", "retrieval/contradictory-client.jsonl",
        "iperf3-client-jsonl", VmRole.VM_A, client_bytes, "diagnostic-day")
    server_native = _record(tmp_path, "contradictory-client-server", "retrieval/contradictory-server.jsonl",
        "iperf3-server-jsonl", VmRole.VM_B,
        _native_iperf_server_jsonl(cookie, duration=86400))
    receiver = _command("f02r-vm_b-listener-iperf3", VmRole.VM_B, None)
    client = _command("iperf3-diagnostic-day", VmRole.VM_A, "diagnostic-day")
    build = _build("iperf3", VmRole.VM_B, "contradictory-client-build")
    records = [log, client_native, server_native, *_build_records(tmp_path, DEPLOYMENT, [build])]
    sources = bind_acquisition_sources(child_root=tmp_path,
        run_identity=SimpleNamespace(deployment_id=DEPLOYMENT), commands=[client],
        receiver_command_evidence=[receiver], build_evidence=[build], source_artifact_records=records)
    assert sources == ()


@pytest.mark.parametrize("corrupt_position", ["first", "second"])
def test_f03_corrupt_sockperf_native_candidate_does_not_erase_other_unit(corrupt_position,
        repository_root, tmp_path):
    resolved = observation(repository_root, "short_study")
    processes = _fixture_processes(FakeClock())
    build = _build("sockperf", VmRole.VM_A, "sock-build")
    units = list(resolved.measurement_units[:2])
    assert len(units) == 2 and units[0].unit_id != units[1].unit_id
    valid_index, corrupt_index = ((1, 0) if corrupt_position == "first" else (0, 1))
    records = []
    for index, unit in enumerate(units):
        action = f"sockperf-{unit.unit_id}"
        log_bytes = (ROOT / "sockperf-window.log").read_text(encoding="utf-8").replace(
            "sockperf-short-multiflow", action).encode()
        log = _record(tmp_path, f"sock-log-{index}", f"retrieval/sock-{index}.stderr",
            "sockperf-observation-stderr", VmRole.VM_A, log_bytes,
            unit.unit_id, context(resolved, tmp_path).identity.deployment_id)
        native = _record(tmp_path, f"sock-native-{index}", f"retrieval/sock-{index}.csv",
            "sockperf-csv", VmRole.VM_A, b"materialized native csv\n", unit.unit_id,
            context(resolved, tmp_path).identity.deployment_id)
        if index == corrupt_index:
            (tmp_path / native.path).write_bytes(b"corrupt after materialization")
        records.extend((log, native))
    runtime = dataclass_replace(context(resolved, tmp_path), build_observations=(build,),
        source_artifact_records=tuple(records))
    assembled = run_measurement(resolved, runtime, processes=processes, clock=processes.clock)
    valid_unit = units[valid_index].unit_id
    valid_timing = next(item for item in assembled.timings if item.unit_id == valid_unit)
    assert valid_timing.acquisition_sources.value is not None
    assert any(source.log_artifact_id == f"sock-log-{valid_index}"
        for source in valid_timing.acquisition_sources.value)


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
    build = _build("sockperf", VmRole.VM_A, "sock-build")
    records = [row, native, *_build_records(tmp_path, DEPLOYMENT, [build])]
    (tmp_path / row.path).write_bytes(log + b"altered")
    with pytest.raises(ValueError, match="size does not match"):
        list(iter_source_windows(tmp_path, source, records,
            [_command("short-multiflow-sockperf", VmRole.VM_A, "short-multiflow")],
            [build]))


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
    records = [row, native, *_build_records(tmp_path, DEPLOYMENT, [build])]
    for field, value in (("patch_sha256", "f" * 64), ("patch_path", "tool_patches/other.patch"),
                         ("source_sha256", "c" * 64), ("upstream_commit", "1" * 40),
                         ("binary_sha256", None), ("observation_capability", None)):
        changed = SimpleNamespace(**{**vars(build), field: value})
        with pytest.raises(ValueError):
            list(iter_source_windows(tmp_path, source, records, commands, [changed]))
    with pytest.raises(ValueError, match="uniquely"):
        list(iter_source_windows(tmp_path, source, [row, native], commands, [build]))
    changed_command = _command("sockperf-short-multiflow", VmRole.VM_A, "short-multiflow")
    changed_command.request["argv"][0] = "/opt/other/bin/sockperf"
    with pytest.raises(ValueError, match="executable"):
        list(iter_source_windows(tmp_path, source, records, [changed_command], [build]))
    changed_command = _command("sockperf-short-multiflow", VmRole.VM_A, "short-multiflow")
    changed_command.request["acquisition_invocation_id"] = "different-invocation"
    with pytest.raises(ValueError, match="environment identity"):
        list(iter_source_windows(tmp_path, source, records, [changed_command], [build]))
    build_path = tmp_path / records[-1].path
    original = build_path.read_bytes()
    build_path.write_bytes(original + b"substituted")
    with pytest.raises(ValueError, match="size does not match"):
        list(iter_source_windows(tmp_path, source, records, commands, [build]))
    build_path.write_bytes(original)
    with pytest.raises(ValueError, match="one command"):
        list(iter_source_windows(tmp_path, source, records,
            [_command("sockperf-other-action", VmRole.VM_A, "short-multiflow")], [build]))
    with pytest.raises(ValueError, match="uniquely"):
        list(iter_source_windows(tmp_path, source, [*records, row], commands, [build]))


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
    build = _build("sockperf", VmRole.VM_A, "sock-build")
    build_records = _build_records(tmp_path, DEPLOYMENT, [build])
    outside = tmp_path.parent / "outside-acquisition.log"
    outside.write_bytes(log)
    target = tmp_path / row.path
    target.unlink()
    target.symlink_to(outside)
    with pytest.raises(ValueError, match="escapes"):
        list(iter_source_windows(tmp_path, source, [row, native, *build_records],
            [_command("sockperf-short-multiflow", VmRole.VM_A, "short-multiflow")],
            [build]))


def test_large_receiver_replay_is_repeatable_immutable_and_stream_bounded(tmp_path):
    cookie = "cookie-day-stream"
    invocation = "f02r-vm_b-listener-iperf3"
    anchor = {"format": 1, "tool": "iperf3", "semantics_id": "iperf-3.17.1-stats-v1",
        "invocation_id": invocation, "pid": 71, "boot_id": "12345678-1234-1234-1234-123456789def",
        "clock": "CLOCK_MONOTONIC", "record_kind": "clock_anchor", "cookie": cookie,
        "mono_before_ns": 1, "realtime_ns": 2, "mono_after_ns": 3}
    settings = {**anchor, "record_kind": "session_settings", "protocol": "TCP", "num_streams": 1,
        "omit": 5, "duration": 86400, "interval_us": 1000000, "block_size": 131072, "reverse": False}
    origin = [{**anchor, "record_kind": "stats_origin", "stream_id": "1",
        "phase": phase, "origin_us": 0, "start_time_fixed_us": 0} for phase in ("initial", "post_omit")]
    prefix = b"".join((b"CNB_ACQ_V1 " + json.dumps(value, separators=(",", ":")).encode() + b"\n")
        for value in (anchor, settings, *origin))
    count = 100_000
    native_start = {"cookie": cookie, "test_start": {"protocol": "TCP", "num_streams": 1,
        "omit": 5, "duration": 86400, "interval": 1, "blksize": 131072, "reverse": False},
        "connected": [{"socket": 1, "local_host": "10.0.0.2", "local_port": 5201,
            "remote_host": "10.0.0.1", "remote_port": 50100}]}
    def observations():
        yield prefix
        for index in range(count):
            start = index * 1_000_000
            row = {**anchor, "record_kind": "receiver_interval", "stream_id": "1",
                "role": "receiver", "protocol": "tcp", "local_host": "10.0.0.2", "local_port": "5201",
                "remote_host": "10.0.0.1", "remote_port": "50100", "interval_start_us": start,
                "interval_end_us": start + 1_000_000, "bytes": 0, "omitted": False}
            yield b"CNB_ACQ_V1 " + json.dumps(row, separators=(",", ":")).encode() + b"\n"

    def receiver_events():
        yield (json.dumps({"event": "start", "data": native_start}, separators=(",", ":")) + "\n").encode()
        for index in range(count):
            row = {"socket": 1, "start": index, "end": index + 1, "bytes": 0,
                "omitted": False, "sender": False}
            event = {"event": "interval", "data": {"streams": [row]}}
            yield (json.dumps(event, separators=(",", ":")) + "\n").encode()
        yield b'{"event":"end","data":{}}\n'

    log = _record_stream(tmp_path, "large-receiver-log", "retrieval/large-iperf.stderr",
        "iperf3-observation-stderr", VmRole.VM_B, observations())
    client_native = _record(tmp_path, "large-receiver-client", "retrieval/large-iperf-client.jsonl",
        "iperf3-client-jsonl", VmRole.VM_A, _native_iperf_client_jsonl(cookie, duration=86400), "day-iperf")
    receiver_native = _record_stream(tmp_path, "large-receiver-native", "retrieval/large-iperf-server.jsonl",
        "iperf3-server-jsonl", VmRole.VM_B, receiver_events())
    client = _command("iperf3-day-iperf", VmRole.VM_A, "day-iperf")
    receiver = _command(invocation, VmRole.VM_B, None)
    build = _build("iperf3", VmRole.VM_B, "iperf-build")
    records = [log, client_native, receiver_native, *_build_records(tmp_path, DEPLOYMENT, [build])]
    sources = bind_acquisition_sources(child_root=tmp_path, run_identity=SimpleNamespace(deployment_id=DEPLOYMENT),
        commands=[client], receiver_command_evidence=[receiver], build_evidence=[build],
        source_artifact_records=records)
    assert len(sources) == 1
    digest_before = hashlib.sha256((tmp_path / log.path).read_bytes()).hexdigest()
    tracemalloc.start()
    first_count = sum(1 for _ in iter_source_windows(tmp_path, sources[0], records, [client, receiver], [build]))
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    digest_after = hashlib.sha256((tmp_path / log.path).read_bytes()).hexdigest()
    assert first_count == count
    assert digest_before == digest_after
    # Hash verification uses 1 MiB chunks; both source parsers and replay keep
    # memory bounded while processing a generated 100k-row receiver session.
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
        if _tool_name(request.argv) == "sockperf":
            assert request.argv[1] == "under-load"
            assert "--no-rdtsc" in request.argv and "--full-rtt" in request.argv
            assert "--mps" in request.argv and "--reply-every" in request.argv
            assert str(handle.stderr_path).endswith("sockperf-acquisition.stderr") or "acquisition.stderr" in str(handle.stderr_path)
        else:
            assert _tool_name(request.argv) == "iperf3" and "-c" in request.argv and "-Z" in request.argv
            if mode == "diagnostic_day":
                assert "--json-stream" in request.argv and "--forceflush" in request.argv
            else:
                assert "-J" in request.argv and "--get-server-output" in request.argv
            assert "iperf3-acquisition.stderr" in str(handle.stderr_path)
    if mode == "diagnostic_day":
        assert len([row for row in processes.handles if _tool_name(row.request.argv) == "iperf3"]) == 1
        assert len([row for row in processes.handles if _tool_name(row.request.argv) == "sockperf"]) >= 288


@pytest.mark.parametrize("mode", ["acceptance", "short_study", "calibration", "diagnostic_day"])
def test_f03_mode_commands_bind_and_replay_raw_sockperf_sources(mode, repository_root, tmp_path):
    """Exercise raw grammar through run_measurement's actual F03 command evidence."""
    resolved = observation(repository_root, mode)
    clock = FakeClock()
    processes = _fixture_processes(clock)
    builds = [_build("sockperf", VmRole.VM_A, "sock-build"), _build("iperf3", VmRole.VM_A, "iperf-client-build"),
        _build("iperf3", VmRole.VM_B, "iperf-build")]
    runtime = dataclass_replace(context(resolved, tmp_path), build_observations=tuple(builds))
    result = run_measurement(resolved, runtime, processes=processes, clock=clock)
    assert result.outcome == "succeeded"

    records = []
    sockperf_commands = [command for command in result.command_evidence
        if _tool_name(command.request.get("argv", [None])) == "sockperf"]
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
        if _tool_name(command.request.get("argv", [None])) == "iperf3"]
    receiver_action = "f02r-vm_b-listener-iperf3"
    receiver_commands = [_command(receiver_action, VmRole.VM_B, None, runtime.identity.deployment_id)]
    for index, command in enumerate(tcp_commands):
        cookie = f"cookie-{mode}-{index:02d}"
        log_bytes = (ROOT / "iperf-receiver.log").read_text(encoding="utf-8").replace(
            "native-cookie-1", cookie).encode("utf-8")
        if mode == "diagnostic_day":
            log_bytes = log_bytes.replace(b'"duration":60', b'"duration":86400')
        records.append(_record(tmp_path, f"iperf-log-{index}", f"retrieval/iperf-{index}.stderr",
            "iperf3-observation-stderr", VmRole.VM_B, log_bytes, None, runtime.identity.deployment_id))
        if mode == "diagnostic_day":
            client_bytes = _native_iperf_client_jsonl(cookie, duration=86400)
            server_bytes = _native_iperf_server_jsonl(cookie, duration=86400)
            records.append(_record(tmp_path, f"iperf-native-{index}", f"retrieval/iperf-{index}.jsonl",
                "iperf3-client-jsonl", VmRole.VM_A, client_bytes, command.unit_id, runtime.identity.deployment_id))
            records.append(_record(tmp_path, f"iperf-server-native-{index}", f"retrieval/iperf-{index}-server.jsonl",
                "iperf3-server-jsonl", VmRole.VM_B, server_bytes, None, runtime.identity.deployment_id))
            continue
        native_bytes = _native_iperf(cookie)
        records.append(_record(tmp_path, f"iperf-native-{index}", f"retrieval/iperf-{index}.json",
            "iperf3-json", VmRole.VM_A, native_bytes, command.unit_id, runtime.identity.deployment_id))
    if tcp_commands:
        receiver_commands = [_command(receiver_action, VmRole.VM_B, None, runtime.identity.deployment_id)]

    records.extend(_build_records(tmp_path, runtime.identity.deployment_id, builds))
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
        assert len(replayed) == (1 if source.tool == "sockperf" else 2)
        replay_windows.append((source, replayed[-1]))
        if source.tool == "sockperf":
            assert replayed[0].has_positive_inner_support
        else:
            assert replayed[0].omitted is True and replayed[1].omitted is False and replayed[1].has_positive_inner_support
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
    assert all(_tool_name(handle.request.argv) == "sockperf" for handle in processes.handles if _tool_name(handle.request.argv) == "sockperf")


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
    build = _build("sockperf", VmRole.VM_A, "sock-build")
    runtime = dataclass_replace(context(resolved, tmp_path), build_observations=(build,))
    result = run_measurement(resolved, runtime, processes=processes, clock=processes.clock)
    assert result.command_evidence, (result.outcome, result.failures)
    action = next(row.request.action_id for row in processes.handles if _tool_name(row.request.argv) == "sockperf")
    command = next(row for row in result.command_evidence if _tool_name(row.request.get("argv", [None])) == "sockperf")
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
    build_records = _build_records(tmp_path, runtime.identity.deployment_id, [build])
    sources = bind_acquisition_sources(child_root=tmp_path, run_identity=runtime.identity,
        commands=result.command_evidence, build_evidence=[build], source_artifact_records=[log, native, *build_records])
    assert sources
    if mutation == "failed_command":
        replayed = []
        with pytest.raises(ValueError, match="unknown tail"):
            for window in iter_source_windows(tmp_path, sources[0], [log, native, *build_records], result.command_evidence, [build]):
                replayed.append(window)
        assert replayed and command.result["outcome"] == "failed"
    else:
        with pytest.raises(ValueError):
            list(iter_source_windows(tmp_path, sources[0], [log, native, *build_records], result.command_evidence, [build]))


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
            "iperf3-json", VmRole.VM_A, _native_iperf(cookie),
            unit.unit_id, runtime.identity.deployment_id),
    ]
    receiver = _command(receiver_action, VmRole.VM_B, None, runtime.identity.deployment_id)
    receiver.result["outcome"] = "failed"
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
    assert "unknown tail" in replay_note["acquisition_coverage_reason"]
