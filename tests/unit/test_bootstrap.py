from pathlib import Path
import hashlib
import shlex
import subprocess
import sys
import pytest
sys.path.insert(0, str(Path(__file__).parents[1]))
from fixtures.f02r.support import BootstrapRunner, bootstrap_config, connection, deadline, result
from cloud_network_benchmark.bootstrap import SourceBuild, run_bootstrap, _required_capabilities, _tcp_congestion_probe_script
from cloud_network_benchmark.bootstrap import _build_config_prelude, _capabilities_observed, _compiler_identity_observed, _identity_preflight_script, _recipe, _unit_environment_matches


def test_systemd_listener_identity_accepts_only_exact_balanced_environment_quotes():
    expected = "CNB_ACQUISITION_ID=f02r-vm_b-listener-iperf3"
    show = f"Environment={expected}\n"

    def matches(value):
        return _unit_environment_matches(show, f"[Service]\nEnvironment={value}\n", "f02r-vm_b-listener-iperf3")

    assert matches(expected)
    assert matches(f'"{expected}"')
    for value in (
        '"CNB_ACQUISITION_ID=wrong"',
        '"CNB_ACQUISITION_ID=f02r-vm_b-listener-iperf3-extra"',
        f'{expected} trailing',
        f'"{expected}',
        f'{expected}"',
    ):
        assert not matches(value)
    assert not _unit_environment_matches("Environment=CNB_ACQUISITION_ID=wrong\n",
                                        f"[Service]\nEnvironment=\"{expected}\"\n",
                                        "f02r-vm_b-listener-iperf3")


def test_preparation_observes_both_tools_on_both_vms_and_private_servers(tmp_path):
    c=connection(tmp_path); runner=BootstrapRunner()
    result=run_bootstrap(c,bootstrap_config(),runner,deadline=deadline(),clock=lambda:100)
    assert result.succeeded
    assert len(result.builds)==4 and {x.tool for x in result.builds}=={"sockperf","iperf3"}
    assert len(result.listeners)==2
    assert {(x.protocol,x.address,x.port) for x in result.listeners}=={("udp","10.0.0.2",11111),("tcp","10.0.0.2",5201)}
    assert all("StrictHostKeyChecking=yes" in request.argv for request in runner.requests)
    assert all(x.source_sha256 in {"a"*64,"b"*64} and x.binary_sha256=="c"*64 for x in result.builds)
    sockperf=next(request for request in runner.requests if request.action_id=="f02r-vm_b-listener-sockperf")
    iperf3=next(request for request in runner.requests if request.action_id=="f02r-vm_b-listener-iperf3")
    assert "/var/lib/cnb/install/bin/sockperf" in sockperf.argv[-1] and "-i 10.0.0.2 -p 11111" in sockperf.argv[-1]
    assert "/var/lib/cnb/install/bin/iperf3" in iperf3.argv[-1] and "-B 10.0.0.2 -p 5201 --json-stream --forceflush" in iperf3.argv[-1]
    acl_rules=[request.argv[-1] for request in runner.requests if "acl" in request.action_id and "create" in request.action_id]
    assert any("10.0.0.1/32" in rule and "10.0.0.2" in rule and "11111" in rule and "accept" in rule for rule in acl_rules)
    assert any("10.0.0.2" in rule and "11111" in rule and "drop" in rule for rule in acl_rules)
    assert any("10.0.0.1/32" in rule and "10.0.0.2" in rule and "5201" in rule and "accept" in rule for rule in acl_rules)
    assert any("10.0.0.2" in rule and "5201" in rule and "drop" in rule for rule in acl_rules)


def test_opted_in_runner_overlaps_vm_preparation_and_keeps_role_order(tmp_path):
    from threading import Barrier, Lock

    class ConcurrentRunner(BootstrapRunner):
        supports_concurrent_commands = True

        def __init__(self):
            super().__init__()
            self.started = Barrier(2)
            self.guard = Lock()
            self.active = 0
            self.max_active = 0
            self.per_role = {"vm_a": [], "vm_b": []}

        def run(self, request):
            role = "vm_a" if "vm_a" in request.action_id else "vm_b"
            if request.action_id.endswith("cloud-init"):
                with self.guard:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                self.started.wait(timeout=5)
            self.per_role[role].append(request.action_id)
            try:
                return super().run(request)
            finally:
                if request.action_id.endswith("cloud-init"):
                    with self.guard:
                        self.active -= 1

    runner = ConcurrentRunner()
    outcome = run_bootstrap(connection(tmp_path), bootstrap_config(), runner,
                            deadline=deadline(), clock=lambda: 100)
    assert outcome.succeeded
    assert runner.max_active == 2
    for requests in runner.per_role.values():
        assert requests[0].endswith("cloud-init")
        assert requests.index(next(x for x in requests if x.endswith("sockperf-0"))) < requests.index(
            next(x for x in requests if x.endswith("iperf3-0")))
    assert [item.role for item in outcome.builds] == ["vm_a", "vm_a", "vm_b", "vm_b"]
    roles_in_order = [action.role for action in outcome.actions]
    assert roles_in_order == sorted(roles_in_order, key=lambda role: 0 if role == "vm_a" else 1)


def test_shared_readiness_deadline_is_not_reset_between_workers(tmp_path):
    from threading import Barrier, Lock
    from datetime import datetime, timedelta, timezone
    from cloud_network_benchmark.access import RemoteDeadline

    class SharedClock:
        value = 10.0
        lock = Lock()

        def __call__(self):
            with self.lock:
                return self.value

    clock = SharedClock()
    now = datetime.now(timezone.utc)
    limit = RemoteDeadline(readiness_deadline_monotonic=15, overall_deadline_monotonic=20,
        cleanup_reserve_seconds=1, cap_started_at=now, cap_expires_at=now + timedelta(seconds=30))

    class ExpiringRunner(BootstrapRunner):
        supports_concurrent_commands = True

        def __init__(self):
            super().__init__()
            self.started = Barrier(2, action=lambda: setattr(clock, "value", 16))

        def run(self, request):
            if request.action_id.endswith("cloud-init"):
                self.started.wait(timeout=5)
            return super().run(request)

    runner = ExpiringRunner()
    outcome = run_bootstrap(connection(tmp_path), bootstrap_config(), runner,
                            deadline=limit, clock=clock)
    assert not outcome.succeeded
    assert outcome.failure.category == "readiness_deadline_exhausted"
    assert {action.role for action in outcome.actions} == {"vm_a", "vm_b"}
    assert all(action.action_id.endswith("cloud-init") for action in outcome.actions)


def test_worker_exception_after_success_keeps_its_partial_evidence(tmp_path):
    from threading import current_thread

    class LaterFailureClock:
        def __init__(self):
            self.vm_a_calls = 0

        def __call__(self):
            if current_thread().name == "cnb-bootstrap_0":
                self.vm_a_calls += 1
                if self.vm_a_calls == 3:
                    raise RuntimeError("synthetic deadline clock failure")
            return 100

    class ConcurrentRunner(BootstrapRunner):
        supports_concurrent_commands = True

    runner = ConcurrentRunner()
    outcome = run_bootstrap(connection(tmp_path), bootstrap_config(), runner,
                            deadline=deadline(), clock=LaterFailureClock())
    assert not outcome.succeeded
    assert outcome.failure.category == "preparation_worker_failed"
    assert outcome.failure.role == "vm_a"
    assert any(action.action_id == "f02r-vm_a-cloud-init" for action in outcome.actions)
    assert {action.role for action in outcome.actions} == {"vm_a", "vm_b"}


def test_vm_b_primary_failure_is_not_replaced_by_vm_a_peer_cancellation(tmp_path):
    from threading import Barrier

    class VmBFailureRunner(BootstrapRunner):
        supports_concurrent_commands = True

        def __init__(self):
            super().__init__()
            self.started = Barrier(2)

        def run(self, request):
            if request.action_id.endswith("cloud-init"):
                self.started.wait(timeout=5)
                if "vm_b" in request.action_id:
                    return result(request.action_id, success=False, stderr="vm_b failed first")
            return super().run(request)

    runner = VmBFailureRunner()
    outcome = run_bootstrap(connection(tmp_path), bootstrap_config(), runner,
                            deadline=deadline(), clock=lambda: 100)
    assert not outcome.succeeded
    assert outcome.failure.category == "cloud_init_failed"
    assert outcome.failure.role == "vm_b"
    assert {action.role for action in outcome.actions} == {"vm_a", "vm_b"}
    assert not any("listener-" in action.action_id for action in outcome.actions)


def test_main_thread_interrupt_cancels_and_joins_inflight_role_workers(tmp_path, monkeypatch):
    import concurrent.futures
    import threading
    from threading import Barrier, Event

    release_inflight = Event()
    inflight = Event()
    cloud_init_barrier = Barrier(2)
    original_event = threading.Event

    class CancellationEvent(original_event):
        def set(self):
            super().set()
            release_inflight.set()

    created_events = []

    def event_factory(*args, **kwargs):
        event = (CancellationEvent(*args, **kwargs) if not created_events
                 else original_event(*args, **kwargs))
        created_events.append(event)
        return event

    class BlockingRunner(BootstrapRunner):
        supports_concurrent_commands = True

        def __init__(self):
            super().__init__()
            self.active = 0
            self.guard = threading.Lock()

        def run(self, request):
            if request.action_id.endswith("cloud-init"):
                cloud_init_barrier.wait(timeout=5)
            else:
                with self.guard:
                    self.active += 1
                inflight.set()
                release_inflight.wait(timeout=5)
            try:
                return super().run(request)
            finally:
                if not request.action_id.endswith("cloud-init"):
                    with self.guard:
                        self.active -= 1

    runner = BlockingRunner()

    def interrupted_completion(_futures):
        assert inflight.wait(timeout=5)
        with runner.guard:
            assert runner.active >= 1
        raise KeyboardInterrupt()
        yield  # pragma: no cover

    monkeypatch.setattr(threading, "Event", event_factory)
    monkeypatch.setattr(concurrent.futures, "as_completed", interrupted_completion)
    outcome = run_bootstrap(connection(tmp_path), bootstrap_config(), runner,
                            deadline=deadline(), clock=lambda: 100)
    assert not outcome.succeeded
    assert outcome.failure.category == "command_interrupted"
    assert {action.role for action in outcome.actions} == {"vm_a", "vm_b"}
    assert not any(action.action_id.startswith(("f02r-vm_b-listener-inspect-",
        "f02r-vm_b-listener-sockperf", "f02r-vm_b-listener-iperf3",
        "f02r-vm_b-acl-pre")) for action in outcome.actions)
    assert runner.active == 0
    assert created_events[0].is_set()
    assert all(len([x for x in runner.requests if role in x.action_id]) <= 2
               for role in ("vm_a", "vm_b"))


@pytest.mark.parametrize("raises", [False, True])
def test_concurrent_failure_preserves_peer_evidence_and_never_starts_listeners(tmp_path, raises):
    from threading import Barrier

    class FailingConcurrentRunner(BootstrapRunner):
        supports_concurrent_commands = True

        def __init__(self):
            super().__init__()
            self.started = Barrier(2)

        def run(self, request):
            if request.action_id.endswith("cloud-init"):
                self.started.wait(timeout=5)
                if "vm_a" in request.action_id:
                    if raises:
                        raise RuntimeError("synthetic runner exception")
                    return result(request.action_id, success=False, stderr="synthetic failure")
            return super().run(request)

    runner = FailingConcurrentRunner()
    outcome = run_bootstrap(connection(tmp_path), bootstrap_config(), runner,
                            deadline=deadline(), clock=lambda: 100)
    assert not outcome.succeeded
    assert outcome.failure.role == "vm_a"
    assert {action.role for action in outcome.actions} == {"vm_a", "vm_b"}
    assert not outcome.listeners
    assert not any("f02r-vm_b-listener-" in action.action_id for action in outcome.actions)


def test_source_digest_mismatch_stops_before_extract_or_other_vm(tmp_path):
    c=connection(tmp_path)
    class BadDigest(BootstrapRunner):
        def run(self, request):
            r=super().run(request)
            if request.action_id.endswith("sockperf-2"):
                from fixtures.f02r.support import result
                return result(request.action_id,"d"*64+" archive")
            return r
    runner=BadDigest(); result=run_bootstrap(c,bootstrap_config(),runner,deadline=deadline(),clock=lambda:100)
    assert not result.succeeded and result.failure.category=="source_digest_mismatch"
    assert not any("vm_b" in x.action_id for x in result.actions)


def test_build_failure_stops_before_binary_observation_or_other_vm(tmp_path):
    c=connection(tmp_path)
    class FailedBuild(BootstrapRunner):
        def run(self,request):
            if request.action_id=="f02r-vm_a-sockperf-4":
                from fixtures.f02r.support import result
                return result(request.action_id,success=False,stderr="synthetic compiler failure")
            return super().run(request)
    runner=FailedBuild(); outcome=run_bootstrap(c,bootstrap_config(),runner,deadline=deadline(),clock=lambda:100)
    assert not outcome.succeeded and outcome.failure.category=="source_build_failed"
    assert not any(request.action_id.endswith("sockperf-5") or "vm_b" in request.action_id for request in runner.requests)


def test_explicit_https_and_digest_are_required():
    with pytest.raises(Exception): SourceBuild(tool="sockperf",source_url="http://bad",source_reference="src",archive_name="a.tar",source_sha256="x")


def test_package_install_refreshes_indexes_and_stops_when_refresh_fails(tmp_path):
    import os

    config = bootstrap_config()
    command = _recipe(config.sources[0], config)[0]
    assert command[:3] == ["sudo", "sh", "-c"]
    script = command[3]
    assert script.index("apt-get update") < script.index("apt-get install -y")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    apt_get = fake_bin / "apt-get"
    state = tmp_path / "apt-index-ready"
    log = tmp_path / "apt.log"
    apt_get.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(log))}\n"
        "if [ \"$1\" = update ]; then\n"
        "  [ \"${APT_UPDATE_FAIL:-0}\" = 0 ] || { echo 'synthetic apt index refresh failure' >&2; exit 17; }\n"
        f"  : > {shlex.quote(str(state))}; exit 0\n"
        "fi\n"
        f"[ -f {shlex.quote(str(state))} ] || {{ echo 'package index is stale' >&2; exit 100; }}\n"
        "[ \"$1\" = install ]\n"
    )
    apt_get.chmod(0o755)
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}

    refreshed = subprocess.run(["sh", "-c", script], text=True, capture_output=True, env=env)
    assert refreshed.returncode == 0, refreshed.stderr
    assert log.read_text().splitlines() == ["update", "install -y " + " ".join(config.bootstrap_packages)]

    state.unlink()
    log.unlink()
    env["APT_UPDATE_FAIL"] = "1"
    failed = subprocess.run(["sh", "-c", script], text=True, capture_output=True, env=env)
    assert failed.returncode == 17
    assert "synthetic apt index refresh failure" in failed.stderr
    assert log.read_text().splitlines() == ["update"]


def test_matching_existing_listeners_are_reused_and_conflicts_fail_closed(tmp_path):
    c=connection(tmp_path); runner=BootstrapRunner(existing_listeners=True,existing_acl=True)
    outcome=run_bootstrap(c,bootstrap_config(),runner,deadline=deadline(),clock=lambda:100)
    assert outcome.succeeded
    assert not any("listener-log-directory" in request.action_id or "listener-sockperf" in request.action_id or "listener-iperf3" in request.action_id for request in runner.requests)
    conflict=BootstrapRunner(existing_listeners=True,existing_acl=True,mismatched_listener=True)
    failed=run_bootstrap(c,bootstrap_config(),conflict,deadline=deadline(),clock=lambda:100)
    assert not failed.succeeded and failed.failure.category=="listener_state_mismatch"
    assert not any("listener-log-directory" in request.action_id or request.action_id.endswith("listener-sockperf") for request in conflict.requests)


def test_missing_tool_capability_stops_before_listener_preparation(tmp_path):
    c=connection(tmp_path)
    class MissingHelp(BootstrapRunner):
        def run(self,request):
            if request.action_id.endswith("sockperf-8"):
                from fixtures.f02r.support import result
                return result(request.action_id,"server under-load")
            return super().run(request)
    runner=MissingHelp(); outcome=run_bootstrap(c,bootstrap_config(),runner,deadline=deadline(),clock=lambda:100)
    assert not outcome.succeeded and outcome.failure.category=="capability_missing"
    assert not any("listener" in request.action_id for request in runner.requests)


def test_iperf_help_option_and_kernel_algorithm_are_checked_separately(tmp_path):
    help_text = ("Usage: iperf3 [options]\n-s -c -B -p -P -t -O -i -l\n"
        "-C, --congestion <algo> set TCP congestion control algorithm\n-Z, --zerocopy\n"
        "-J --get-server-output --json-stream --forceflush")
    assert "cubic" not in help_text.lower()
    assert _capabilities_observed(help_text, "iperf3")
    assert _tcp_congestion_probe_script().startswith("exec(bytes.fromhex('")

    class UnsupportedCubic(BootstrapRunner):
        def run(self, request):
            if request.argv and request.argv[-1].startswith("python3 -c "):
                from fixtures.f02r.support import result
                return result(request.action_id, success=False, stderr="setsockopt TCP_CONGESTION cubic: unsupported")
            return super().run(request)

    runner = UnsupportedCubic()
    outcome = run_bootstrap(connection(tmp_path), bootstrap_config(), runner,
        deadline=deadline(), clock=lambda: 100)
    assert not outcome.succeeded
    assert outcome.failure.category == "tcp_congestion_control_unavailable"
    assert not any("listener" in request.action_id for request in runner.requests)


@pytest.mark.skipif(sys.platform != "linux", reason="TCP_CONGESTION probe requires Linux")
def test_kernel_rejects_an_unavailable_congestion_algorithm():
    probe = subprocess.run([sys.executable, "-c", _tcp_congestion_probe_script("cnb-missing")],
        text=True, capture_output=True)
    assert probe.returncode != 0
    assert "CNB_TCP_CONGESTION=" not in probe.stdout


def test_nested_build_script_is_syntax_safe_and_identity_reuse_is_verified(tmp_path):
    c=bootstrap_config().model_copy(update={"remote_root":str(tmp_path),"workspace":str(tmp_path/"work"),"install_prefix":str(tmp_path/"install")})
    source=next(item for item in c.sources if item.tool=="iperf3")
    binary=Path(c.install_prefix)/"bin/iperf3"; binary.parent.mkdir(parents=True); binary.write_bytes(b"compiled binary")
    digest=hashlib.sha256(binary.read_bytes()).hexdigest(); marker=Path(c.install_prefix)/".f02r-iperf3.identity"
    config_hash=subprocess.run(["sh", "-c", _build_config_prelude(source, c) + "printf '%s' \"$config_sha\""],text=True,capture_output=True,check=True).stdout
    marker.write_text(f"{source.source_reference} {source.source_sha256} legacy {config_hash} {digest}\n")
    probe=subprocess.run(["sh","-c",_identity_preflight_script(source,c)],text=True,capture_output=True)
    assert probe.returncode==0 and probe.stdout.strip()=="matching"
    recipe=_recipe(source,c)
    checked=subprocess.run(["sh","-n","-c",recipe[4][-1]],text=True,capture_output=True)
    assert checked.returncode==0
    before=binary.read_bytes(); reused=subprocess.run(["sh","-c",recipe[4][-1]],text=True,capture_output=True)
    assert reused.returncode==0 and binary.read_bytes()==before
    marker.write_text(f"other-source {source.source_sha256} legacy {config_hash} {digest}\n")
    conflict=subprocess.run(["sh","-c",_identity_preflight_script(source,c)],text=True,capture_output=True)
    assert conflict.returncode != 0


def test_fresh_host_identity_preflight_does_not_need_compiler_and_state_fails_closed(tmp_path):
    import os
    import shlex
    from fixtures.f02r.support import result

    config = bootstrap_config().model_copy(update={
        "remote_root": str(tmp_path), "workspace": str(tmp_path / "work"),
        "results_workspace": str(tmp_path / "results"), "install_prefix": str(tmp_path / "install"),
    })
    source = next(item for item in config.sources if item.tool == "sockperf")
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    env = {**os.environ, "PATH": str(empty_path)}

    # /bin/sh builtins alone must identify the pristine state and avoid cc/sha256sum.
    fresh = subprocess.run(["/bin/sh", "-c", _identity_preflight_script(source, config)],
        text=True, capture_output=True, env=env)
    assert fresh.returncode == 0 and fresh.stdout.strip() == "missing"

    for state_kind in ("source", "archive", "binary", "marker", "build-identity", "dangling-source", "mismatched"):
        state_root = tmp_path / state_kind
        state_config = config.model_copy(update={
            "workspace": str(state_root / "work"),
            "results_workspace": str(state_root / "results"),
            "install_prefix": str(state_root / "install"),
        })
        state_source = next(item for item in state_config.sources if item.tool == "sockperf")
        if state_kind == "source":
            Path(state_config.workspace, source.source_reference).mkdir(parents=True)
        elif state_kind == "dangling-source":
            Path(state_config.workspace).mkdir(parents=True)
            Path(state_config.workspace, source.source_reference).symlink_to("missing-source-target")
        elif state_kind == "archive":
            Path(state_config.workspace).mkdir(parents=True)
            Path(state_config.workspace, source.archive_name).touch()
        else:
            Path(state_config.install_prefix).mkdir(parents=True)
            identity_path = (Path(state_config.install_prefix) / "bin/sockperf" if state_kind == "binary"
                else Path(state_config.install_prefix) / (".f02r-sockperf.identity" if state_kind in {"marker", "mismatched"}
                else ".f02r-sockperf.build-identity"))
            identity_path.parent.mkdir(parents=True, exist_ok=True)
            if state_kind == "mismatched":
                identity_path.write_text("wrong source identity\n")
                binary_path = Path(state_config.install_prefix) / "bin/sockperf"
                binary_path.parent.mkdir(parents=True, exist_ok=True)
                binary_path.write_bytes(b"conflicting binary")
            else:
                identity_path.touch()
        stateful = subprocess.run(["/bin/sh", "-c", _identity_preflight_script(state_source, state_config)],
            text=True, capture_output=True, env=env)
        assert stateful.returncode == 42, (state_kind, stateful.returncode, stateful.stderr)

    class ShellPreflightRunner(BootstrapRunner):
        def __init__(self, *, partial_state=False):
            super().__init__()
            self.partial_state = partial_state

        def run(self, request):
            self.requests.append(request)
            if request.action_id.endswith("identity-preflight"):
                if self.partial_state:
                    Path(config.workspace).mkdir(parents=True, exist_ok=True)
                    Path(config.workspace, source.source_reference).symlink_to("missing-source-target")
                remote_argv = shlex.split(request.argv[-1])
                proc = subprocess.run(["/bin/sh", "-c", remote_argv[2]], text=True,
                    capture_output=True, env=env)
                return result(request.action_id, proc.stdout, success=proc.returncode == 0,
                    stderr=proc.stderr)
            for build_source in config.sources:
                if request.action_id.endswith(f"{build_source.tool}-2"):
                    return result(request.action_id, build_source.source_sha256 + f"  {config.workspace}/{build_source.archive_name}")
                if request.action_id.endswith(f"{build_source.tool}-7"):
                    return result(request.action_id, "c" * 64 + f"  {config.install_prefix}/bin/{build_source.tool}")
            if request.action_id == "f02r-vm_a-iperf3-8":
                return result(request.action_id, success=False, stderr="stop after fake build-flow observation")
            self.requests.pop()
            return super().run(request)

    fresh_runner = ShellPreflightRunner()
    fresh_result = run_bootstrap(connection(tmp_path), config, fresh_runner,
        deadline=deadline(), clock=lambda: 100)
    assert not fresh_result.succeeded and fresh_result.failure.category == "source_build_failed"
    actions = [request.action_id for request in fresh_runner.requests]
    assert actions.index("f02r-vm_a-sockperf-identity-preflight") < actions.index("f02r-vm_a-sockperf-workspace")
    assert all(action in actions for action in (
        "f02r-vm_a-sockperf-0", "f02r-vm_a-sockperf-4",
        "f02r-vm_a-iperf3-0", "f02r-vm_a-iperf3-4"))

    (tmp_path / "partial").mkdir()
    partial_runner = ShellPreflightRunner(partial_state=True)
    partial_result = run_bootstrap(connection(tmp_path / "partial"), config, partial_runner,
        deadline=deadline(), clock=lambda: 100)
    assert not partial_result.succeeded and partial_result.failure.category == "installed_identity_mismatch"
    partial_actions = [request.action_id for request in partial_runner.requests]
    assert "f02r-vm_a-sockperf-identity-preflight" in partial_actions
    assert not any(action.endswith("-workspace") or action.endswith("-0") for action in partial_actions)


def test_identity_preflight_rejects_foreign_or_symlinked_results_workspace(tmp_path):
    from cloud_network_benchmark.bootstrap import _identity_preflight_script

    config = bootstrap_config().model_copy(update={
        "remote_root": str(tmp_path), "workspace": str(tmp_path / "work"),
        "results_workspace": str(tmp_path / "results"), "install_prefix": str(tmp_path / "install")})
    source = next(item for item in config.sources if item.tool == "sockperf")
    script = _identity_preflight_script(source, config)
    results = Path(config.results_workspace)

    def observe():
        return subprocess.run(["/bin/sh", "-c", script], capture_output=True, text=True)

    pristine = observe()
    assert pristine.returncode == 0 and pristine.stdout == "missing\n"
    foreign = tmp_path / "foreign-results"
    foreign.mkdir(); (foreign / "native.csv").write_text("outside deployment")
    results.symlink_to(foreign, target_is_directory=True)
    linked = observe()
    assert linked.returncode == 44
    results.unlink()
    results.mkdir(); (results / ".foreign-marker").write_text("previous output")
    populated = observe()
    assert populated.returncode == 44
    (results / ".foreign-marker").unlink()
    empty = observe()
    assert empty.returncode == 0 and empty.stdout == "missing\n"


def test_shell_unsafe_build_values_are_rejected_and_help_is_token_based():
    c=bootstrap_config()
    with pytest.raises(Exception): type(c).model_validate({**c.model_dump(),"workspace":"/var/lib/cnb/work;touch"})
    with pytest.raises(Exception): SourceBuild(tool="sockperf",source_url="https://example.invalid/x",source_reference="src;touch",archive_name="x.tar.gz",source_sha256="a"*64)
    assert not _capabilities_observed("Usage: tool [options]\nunder-loadish -iSomething --full-rtt-extra", "sockperf")


def test_pinned_acquisition_patch_identity_is_mandatory_and_part_of_cache_key(tmp_path):
    from pathlib import Path
    from cloud_network_benchmark.bootstrap import SourceBuild
    sockperf = Path(__file__).parents[2] / "src/cloud_network_benchmark/tool_patches/sockperf-3.10-acquisition.patch"
    import hashlib
    digest = hashlib.sha256(sockperf.read_bytes()).hexdigest()
    pinned = SourceBuild(tool="sockperf", source_url="https://github.com/Mellanox/sockperf/archive/5ebd327da983225321818c0355db922515e026bd.tar.gz",
        source_reference="sockperf-5ebd327da983225321818c0355db922515e026bd", upstream_commit="5ebd327da983225321818c0355db922515e026bd", archive_name="sockperf.tar.gz",
        source_sha256="a35a1daf310551b9f675af4fb7d95a6a10389fda4c7605d70a21554be0773f8e",
        observation_patch_path="tool_patches/sockperf-3.10-acquisition.patch", observation_patch_sha256=digest)
    with pytest.raises(Exception, match="exact packaged observation patch"):
        SourceBuild(**{**pinned.model_dump(), "observation_patch_sha256": "f" * 64})
    config = bootstrap_config().model_copy(update={"remote_root": str(tmp_path), "workspace": str(tmp_path / "work"),
                                                    "install_prefix": str(tmp_path / "install")})
    script = _recipe(pinned, config)[4][-1]
    assert "patch --batch --fuzz=0 -p1" in script
    assert digest in script and "old_patch" in script
    assert subprocess.run(["sh", "-n", "-c", script], capture_output=True).returncode == 0
    assert "config_sha" in script and "CNB_ACQ_V1" in " ".join(" ".join(row) for row in _recipe(pinned, config))
    iperf_patch = Path(__file__).parents[2] / "src/cloud_network_benchmark/tool_patches/iperf-3.17.1-acquisition.patch"
    iperf_digest = hashlib.sha256(iperf_patch.read_bytes()).hexdigest()
    iperf = SourceBuild(tool="iperf3", source_url="https://github.com/esnet/iperf/archive/2acfcfe94e928e74542c9f107e02aa6dd4748a79.tar.gz",
        source_reference="iperf-2acfcfe94e928e74542c9f107e02aa6dd4748a79", upstream_commit="2acfcfe94e928e74542c9f107e02aa6dd4748a79", archive_name="iperf.tar.gz",
        source_sha256="d6b4d2c7b20157c5b7fbd2de66f22bd3428958b5f2e28899151b6e22745886fe",
        observation_patch_path="tool_patches/iperf-3.17.1-acquisition.patch", observation_patch_sha256=iperf_digest)
    iperf_script = _recipe(iperf, config)[4][-1]
    assert "patch --batch --fuzz=0 -p1" in iperf_script
    assert subprocess.run(["sh", "-n", "-c", iperf_script], capture_output=True).returncode == 0


def test_pinned_bootstrap_records_observed_patch_and_capability_identity(tmp_path):
    sock_patch = Path(__file__).parents[2] / "src/cloud_network_benchmark/tool_patches/sockperf-3.10-acquisition.patch"
    iperf_patch = Path(__file__).parents[2] / "src/cloud_network_benchmark/tool_patches/iperf-3.17.1-acquisition.patch"
    sock_sha, iperf_sha = hashlib.sha256(sock_patch.read_bytes()).hexdigest(), hashlib.sha256(iperf_patch.read_bytes()).hexdigest()
    sock_commit, iperf_commit = "5ebd327da983225321818c0355db922515e026bd", "2acfcfe94e928e74542c9f107e02aa6dd4748a79"
    config = bootstrap_config().model_copy(update={"sources": [
        SourceBuild(tool="sockperf", source_url=f"https://github.com/Mellanox/sockperf/archive/{sock_commit}.tar.gz",
            source_reference=f"sockperf-{sock_commit}", archive_name="sockperf.tar.gz",
            source_sha256="a35a1daf310551b9f675af4fb7d95a6a10389fda4c7605d70a21554be0773f8e",
            upstream_commit=sock_commit, observation_patch_path="tool_patches/sockperf-3.10-acquisition.patch",
            observation_patch_sha256=sock_sha),
        SourceBuild(tool="iperf3", source_url=f"https://github.com/esnet/iperf/archive/{iperf_commit}.tar.gz",
            source_reference=f"iperf-{iperf_commit}", archive_name="iperf3.tar.gz",
            source_sha256="d6b4d2c7b20157c5b7fbd2de66f22bd3428958b5f2e28899151b6e22745886fe",
            upstream_commit=iperf_commit, observation_patch_path="tool_patches/iperf-3.17.1-acquisition.patch",
            observation_patch_sha256=iperf_sha)]})
    class PatchedRunner(BootstrapRunner):
        sockperf_version = "sockperf, version 3.10\nCopyright (c) Mellanox Technologies\nBuild options: acquisition"

        def run(self, request):
            aid = request.action_id
            tool = "sockperf" if "sockperf" in aid else "iperf3"
            if "CNB_BUILD_V1" in request.argv[-1]:
                import base64
                source = next(item for item in config.sources if item.tool == tool)
                version = (self.sockperf_version
                           if tool == "sockperf" else
                           "iperf 3.17.1\nLinux synthetic-host 6.8.0\nCompiler: gcc 13.3.0")
                compiler = "gcc (Ubuntu 13.3.0-6ubuntu2) 13.3.0\nCopyright (C) 2024 Free Software Foundation, Inc."
                build_configuration = f"CC=${{CC:-cc}}; compiler={compiler}; ./configure --prefix=/var/lib/cnb/install; make -j2"
                semantics = "sockperf-3.10-enabled-v1" if tool == "sockperf" else "iperf-3.17.1-stats-v1"
                fields = ["CNB_BUILD_V1", f"tool={tool}", f"source_reference={source.source_reference}",
                    f"source_sha256={source.source_sha256}", f"upstream_commit={source.upstream_commit}",
                    f"patch_path={source.observation_patch_path}", f"patch_sha256={source.observation_patch_sha256}",
                    "compiler_version_b64=" + base64.b64encode(compiler.encode()).decode(),
                    "build_configuration_b64=" + base64.b64encode(build_configuration.encode()).decode(),
                    f"binary_path=/var/lib/cnb/install/bin/{tool}",
                    "binary_version_b64=" + base64.b64encode(version.encode()).decode(),
                    f"binary_sha256={'c' * 64}", "observation_capability=CNB_ACQ_V1",
                    f"observation_semantics={semantics}", "capabilities=" + ",".join(_required_capabilities(tool))]
                return result(aid, "\n".join(fields))
            if aid.endswith(f"{tool}-5"):
                return result(aid, "gcc (Ubuntu 13.3.0-6ubuntu2) 13.3.0\nCopyright (C) 2024 Free Software Foundation, Inc.\n")
            if aid.endswith(f"{tool}-6"):
                version = (self.sockperf_version + "\n"
                           if tool == "sockperf" else
                           "iperf 3.17.1\nLinux synthetic-host 6.8.0\nCompiler: gcc 13.3.0\n")
                return result(aid, version)
            if aid.endswith(f"{tool}-2"):
                digest = config.sources[0 if tool == "sockperf" else 1].source_sha256
                archive = "sockperf.tar.gz" if tool == "sockperf" else "iperf3.tar.gz"
                return result(aid, f"{digest} /var/lib/cnb/work/{archive}")
            if aid.endswith(("sockperf-11", "iperf3-9")):
                return result(aid, "CNB_ACQ_V1")
            response = super().run(request)
            if aid.endswith("listener-verify-iperf3"):
                return result(aid, response.stdout + "\nEnvironment=CNB_ACQUISITION_ID=f02r-vm_b-listener-iperf3\n")
            if aid.endswith("listener-config-iperf3"):
                return result(aid, response.stdout + "\nEnvironment=CNB_ACQUISITION_ID=f02r-vm_b-listener-iperf3\n")
            return response
    outcome = run_bootstrap(connection(tmp_path), config, PatchedRunner(), deadline=deadline(), clock=lambda: 100)
    assert outcome.succeeded
    iperf_probe = next(action for action in outcome.actions if action.action_id == "f02r-vm_a-iperf3-9")
    assert "/var/lib/cnb/install/bin/iperf3" in iperf_probe.request["argv"][-1]
    for build in outcome.builds:
        assert "\nCopyright" in build.compiler_version
        assert "\n" in build.binary_version
        assert build.observation_capability == "CNB_ACQ_V1"
        assert build.patch_sha256 == (sock_sha if build.tool == "sockperf" else iperf_sha)
        assert build.observation_semantics == ("sockperf-3.10-enabled-v1" if build.tool == "sockperf" else "iperf-3.17.1-stats-v1")
        assert build.build_configuration and build.artifact_id
        assert build.build_configuration == (f"CC=${{CC:-cc}}; compiler={build.compiler_version}; " +
            ("./configure --prefix=/var/lib/cnb/install; make -j2" if build.tool == "sockperf" else
             "./configure --prefix=/var/lib/cnb/install --disable-shared; make -j2"))
    assert "compiler=$(cc --version)" in _recipe(config.sources[0], config)[-1][-1]
    assert "--version | head -n 1" not in _recipe(config.sources[0], config)[-1][-1]

    class PinnedArchiveBuildBanner(PatchedRunner):
        sockperf_version = (
            "sockperf, version 3.10-no.git\n"
            "   compiled Nov 16 2026, 23:24:27\n\n"
            "Copyright (C) 2011-2022 Mellanox Technologies Ltd.\n"
            "SockPerf is open source software, see http://github.com/mellanox/sockperf\n")
    archive_banner = run_bootstrap(connection(tmp_path), config, PinnedArchiveBuildBanner(),
        deadline=deadline(), clock=lambda: 100)
    assert archive_banner.succeeded
    sockperf_build = next(build for build in archive_banner.builds if build.tool == "sockperf")
    assert sockperf_build.binary_version.startswith("sockperf, version 3.10-no.git\n")

    class LegacySockperfBanner(PatchedRunner):
        sockperf_version = "sockperf version 3.10\nCopyright (c) Mellanox Technologies\n"
    legacy_banner = run_bootstrap(connection(tmp_path), config, LegacySockperfBanner(),
        deadline=deadline(), clock=lambda: 100)
    assert legacy_banner.succeeded

    class MalformedSockperfBanner(PatchedRunner):
        pass

    for banner in ("sockperf, release 3.10\nCopyright (c) Mellanox Technologies\n",
                   "sockperf, version 3.10-no.gitx\nCopyright (c) Mellanox Technologies\n",
                   "sockperf, version 3.10.\nCopyright (c) Mellanox Technologies\n"):
        malformed_runner = MalformedSockperfBanner()
        malformed_runner.sockperf_version = banner
        rejected = run_bootstrap(connection(tmp_path), config, malformed_runner,
            deadline=deadline(), clock=lambda: 100)
        assert not rejected.succeeded and rejected.failure.category == "binary_version_missing"


def test_empty_compiler_and_runner_exception_return_accumulated_failure_evidence(tmp_path):
    c=connection(tmp_path)
    class EmptyCompiler(BootstrapRunner):
        def run(self,request):
            if request.action_id.endswith("sockperf-5"): return result(request.action_id,"")
            return super().run(request)
    failed=run_bootstrap(c,bootstrap_config(),EmptyCompiler(),deadline=deadline(),clock=lambda:100)
    assert not failed.succeeded and failed.failure.category=="compiler_identity_missing", (failed.failure.category, [a.action_id for a in failed.actions[-5:]])
    class Raising(BootstrapRunner):
        def run(self,request):
            if request.action_id.endswith("sockperf-1"): raise OSError("synthetic")
            return super().run(request)
    failed=run_bootstrap(c,bootstrap_config(),Raising(),deadline=deadline(),clock=lambda:100)
    assert not failed.succeeded and len(failed.actions)>=3 and failed.failure.category=="source_build_failed"


def test_interrupted_runner_and_lost_identity_file_preserve_failure_evidence(tmp_path):
    c=connection(tmp_path)
    class Interrupted(BootstrapRunner):
        def run(self,request):
            if request.action_id.endswith("cloud-init"):
                raise KeyboardInterrupt()
            return super().run(request)
    outcome=run_bootstrap(c,bootstrap_config(),Interrupted(),deadline=deadline(),clock=lambda:100)
    assert not outcome.succeeded and outcome.actions and outcome.failure.category=="command_interrupted"
    class LostKey(BootstrapRunner):
        def run(self,request):
            response=super().run(request)
            if request.action_id.endswith("cloud-init"):
                Path(c.vm_a.authentication_reference).unlink(missing_ok=True)
            return response
    runner=LostKey(); outcome=run_bootstrap(c,bootstrap_config(),runner,deadline=deadline(),clock=lambda:100)
    assert not outcome.succeeded and outcome.actions and outcome.failure.category=="transport_preflight_failed"
    assert all("preflight-failed" not in request.argv for request in runner.requests)


def test_known_hosts_loss_between_vm_a_and_vm_b_returns_accumulated_result(tmp_path):
    c=connection(tmp_path)
    class LostAtBoundary(BootstrapRunner):
        def run(self,request):
            response=super().run(request)
            if request.action_id=="f02r-vm_a-iperf3-8":
                Path(c.vm_a.host_key_reference).unlink(missing_ok=True)
            return response
    runner=LostAtBoundary(); outcome=run_bootstrap(c,bootstrap_config(),runner,deadline=deadline(),clock=lambda:100)
    assert not outcome.succeeded and outcome.failure.category=="transport_preflight_failed"
    assert len(outcome.actions)>10 and not any("vm_b-cloud-init" in request.action_id for request in runner.requests)


def test_request_construction_failure_after_identity_preflight_is_returned_as_evidence(tmp_path):
    c=connection(tmp_path)
    class LostDuringIdentityCheck(BootstrapRunner):
        def run(self,request):
            response=super().run(request)
            if request.action_id=="f02r-vm_a-sockperf-identity-preflight":
                Path(c.vm_a.host_key_reference).unlink(missing_ok=True)
            return response
    runner=LostDuringIdentityCheck()
    outcome=run_bootstrap(c,bootstrap_config(),runner,deadline=deadline(),clock=lambda:100)
    assert not outcome.succeeded and outcome.failure.category=="transport_preflight_failed"
    assert len(outcome.actions)>2 and outcome.actions[-1].action_id=="f02r-vm_a-sockperf-workspace"
    assert not any(request.action_id=="f02r-vm_a-sockperf-workspace" for request in runner.requests)
    assert "request construction failed" in outcome.actions[-1].result["stderr"]


def test_listener_request_construction_failure_preserves_setup_actions(tmp_path):
    c=connection(tmp_path)
    class LostBeforeListener(BootstrapRunner):
        def run(self,request):
            response=super().run(request)
            if request.action_id=="f02r-vm_b-listener-log-directory-sockperf":
                Path(c.vm_b.authentication_reference).unlink(missing_ok=True)
            return response
    runner=LostBeforeListener()
    outcome=run_bootstrap(c,bootstrap_config(),runner,deadline=deadline(),clock=lambda:100)
    assert not outcome.succeeded and outcome.failure.category=="transport_preflight_failed"
    assert outcome.actions[-1].action_id=="f02r-vm_b-listener-sockperf"
    assert not any(request.action_id=="f02r-vm_b-listener-sockperf" for request in runner.requests)


def test_listener_observation_exhausting_deadline_returns_evidence(tmp_path):
    c=connection(tmp_path); clock_value=[100.0]
    class Clock:
        def __call__(self): return clock_value[0]
    class SlowFinalObservation(BootstrapRunner):
        def run(self,request):
            response=super().run(request)
            if "listener-config-sockperf" in request.action_id:
                clock_value[0]=200.0
            return response
    outcome=run_bootstrap(c,bootstrap_config(),SlowFinalObservation(),deadline=deadline(),clock=Clock())
    assert not outcome.succeeded and outcome.failure.category=="readiness_deadline_exhausted"
    assert outcome.actions and not any("listener-process-sockperf" in action.action_id for action in outcome.actions)


def test_successful_build_observations_redact_credentials_and_secret_text(tmp_path):
    c=connection(tmp_path); key=c.vm_a.authentication_reference
    class LeakyCompiler(BootstrapRunner):
        def run(self,request):
            if request.action_id.endswith("-5"):
                return result(request.action_id,f"gcc version 13.2 key={key} token=synthetic-secret")
            return super().run(request)
    outcome=run_bootstrap(c,bootstrap_config(),LeakyCompiler(),deadline=deadline(),clock=lambda:100)
    assert outcome.succeeded
    serialized=outcome.model_dump_json()
    assert key not in serialized and "synthetic-secret" not in serialized


def test_sockperf_subcommand_capabilities_are_observed_independently(tmp_path):
    c=connection(tmp_path)
    class MissingUnderLoad(BootstrapRunner):
        def run(self,request):
            if request.action_id.endswith("-10") and "sockperf" in request.action_id:
                return result(request.action_id,"Usage: sockperf under-load -i -p -m --mps --reply-every --full-log -t")
            return super().run(request)
    outcome=run_bootstrap(c,bootstrap_config(),MissingUnderLoad(),deadline=deadline(),clock=lambda:100)
    assert not outcome.succeeded and outcome.failure.category=="capability_missing"
    assert not any("listener-" in action.action_id for action in outcome.actions)


def test_cc_version_accepts_authentic_driver_banner_and_rejects_malformed_text():
    assert _compiler_identity_observed("cc (Ubuntu 13.3.0-6ubuntu2) 13.3.0")
    assert _compiler_identity_observed("Apple clang version 16.0.0")
    assert _compiler_identity_observed("gcc (GCC) 14.2.1")
    assert not _compiler_identity_observed("")
    assert not _compiler_identity_observed("cc version not found")


def test_acl_rejects_accept_or_jump_bypass_before_expected_rules():
    from cloud_network_benchmark.bootstrap import _acl_matches
    from fixtures.f02r.support import nft_listing
    valid=nft_listing("campaign-aws-deployment"); chain="cnb_campaign_aws_deployment"
    assert _acl_matches(valid,"10.0.0.1","10.0.0.2",chain)
    assert not _acl_matches(valid.replace("priority -10; policy accept;", "priority -10; policy accept;\n  ip daddr 10.0.0.2 accept"),"10.0.0.1","10.0.0.2",chain)
    assert not _acl_matches(valid.replace("priority -10; policy accept;", "type filter hook output priority -10; policy accept;"),"10.0.0.1","10.0.0.2",chain)
    assert not _acl_matches(valid.replace("chain cnb_campaign_aws_deployment {", "chain cnb_campaign_aws_deployment {\n  jump unrestricted"),"10.0.0.1","10.0.0.2",chain)
    assert not _acl_matches(valid,"10.0.0.1","10.0.0.2","wrong_chain")


def test_acl_preflight_requests_numeric_priority_format_for_fresh_and_reused_chains(tmp_path):
    for existing in (False, True):
        case=tmp_path / str(existing); case.mkdir()
        c=connection(case); runner=BootstrapRunner(existing_acl=existing)
        outcome=run_bootstrap(c,bootstrap_config(),runner,deadline=deadline(),clock=lambda:100)
        assert outcome.succeeded
        request=next(request for request in runner.requests if request.action_id=="f02r-vm_b-acl-preflight-inspect")
        assert "nft -n -y list chain" in request.argv[-1]


def test_effective_systemd_log_destinations_honor_ordered_overrides_and_resets():
    from cloud_network_benchmark.bootstrap import _unit_log_destinations_match
    expected_out="/var/lib/cnb/work/server.log"; expected_err="/var/lib/cnb/work/server.err"
    base=("# /run/systemd/transient/unit.service\n[Service]\n"
          f"StandardOutput=append:{expected_out}\nStandardError=append:{expected_err}\n")
    assert _unit_log_destinations_match(base,expected_out,expected_err)
    assert not _unit_log_destinations_match(base+"# /etc/systemd/system/unit.service.d/override.conf\n[Service]\nStandardOutput=append:/var/lib/cnb/wrong.log\n",expected_out,expected_err)
    assert not _unit_log_destinations_match(base+"# reset drop-in\n[Service]\nStandardError=\n",expected_out,expected_err)
    assert not _unit_log_destinations_match(base+"# conflicting drop-in\n[Service]\nStandardError=append:/var/lib/cnb/other.err\n",expected_out,expected_err)
    assert _unit_log_destinations_match(base+"[Unit]\nStandardOutput=append:/wrong/section\n",expected_out,expected_err)


@pytest.mark.parametrize("override",[
    "StandardOutput=append:/var/lib/cnb/wrong.log\n",
    "StandardError=append:/var/lib/cnb/wrong.err\n",
    "StandardOutput=\n",
    "StandardError=\n",
])
def test_repeated_preparation_rejects_effective_log_override_or_reset(tmp_path,override):
    c=connection(tmp_path)
    class WrongDropin(BootstrapRunner):
        def run(self,request):
            if request.action_id=="f02r-vm_b-preflight-listener-config-sockperf":
                return result(request.action_id,
                    "[Service]\nStandardOutput=append:/var/lib/cnb/results/server-logs/sockperf.log\n"
                    "StandardError=append:/var/lib/cnb/results/server-logs/sockperf.err\n"
                    "# /etc/systemd/system/override.conf\n[Service]\n"+override)
            return super().run(request)
    outcome=run_bootstrap(c,bootstrap_config(),WrongDropin(existing_listeners=True,existing_acl=True),deadline=deadline(),clock=lambda:100)
    assert not outcome.succeeded and outcome.failure.category=="listener_state_mismatch"
