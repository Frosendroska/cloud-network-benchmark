"""Exercise the default F09 AWS factory with only fake command boundaries."""
from __future__ import annotations

import hashlib
import base64
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from cloud_network_benchmark.admission import load_cost_inputs
from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.contracts.common import Provider
from cloud_network_benchmark.contracts.execution import CommandResult
from cloud_network_benchmark.lifecycle_runtime import build_runtime
from cloud_network_benchmark.orchestration import run_lifecycle

ROOT = Path(__file__).parents[2]
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self):
        self.elapsed = 0.0

    def utc_now(self):
        return NOW + timedelta(seconds=self.elapsed)

    def monotonic(self):
        return 100.0 + self.elapsed

    def sleep(self, seconds):
        self.elapsed += seconds


class FakeAwsRunner:
    """Replies locally to every command; no process or cloud is contacted."""
    def __init__(self):
        self.actions = []
        self.run_id = ""
        self.deployment_id = ""

    def run(self, request):
        action = request.action_id
        self.actions.append(action)
        stdout = "{}"
        if action.startswith("aws-safety-apply-"):
            state = next(value.removeprefix("-state=") for value in request.argv if value.startswith("-state="))
            Path(state).write_text("fake-state")
        if action.startswith("aws-safety-output-"):
            region = action.removeprefix("aws-safety-output-")
            stdout = json.dumps({key: {"value": value} for key, value in {
                "document_name": f"expiry-{region}", "document_version": "1",
                "document_hash": "a" * 64,
                "role_arn": f"arn:aws:iam::123456789012:role/expiry-{region}",
            }.items()})
        elif action == "aws-pair-output":
            stdout = json.dumps({"deployment": {"value": {"vms": {
                "vm_a": {"instance_id": "i-vm-a", "private_ipv4": "10.60.1.10", "public_ipv4": "8.8.8.8",
                    "region": "eu-central-1", "zone": "eu-central-1a", "zone_id": "use1-az2",
                    "image_id": "ami-0123456789abcdef0", "image_owner_id": "099720109477",
                    "architecture": "x86_64", "shape": "m7i.xlarge", "boot_volume_id": "vol-a",
                    "placement_group": None, "placement_group_strategy": None},
                "vm_b": {"instance_id": "i-vm-b", "private_ipv4": "10.60.1.11", "public_ipv4": "1.1.1.1",
                    "region": "eu-central-1", "zone": "eu-central-1a", "zone_id": "use1-az2",
                    "image_id": "ami-0123456789abcdef0", "image_owner_id": "099720109477",
                    "architecture": "x86_64", "shape": "m7i.xlarge", "boot_volume_id": "vol-b",
                    "placement_group": None, "placement_group_strategy": None}},
                "network": {}, "placement": {}}}})
        elif action.startswith("aws-get-caller-identity"):
            stdout = json.dumps({"Account": "123456789012"})
        elif action.startswith("aws-describe-instances"):
            instance_id = request.argv[request.argv.index("--instance-ids") + 1]
            role = "vm_a" if instance_id == "i-vm-a" else "vm_b"
            private_ip = "10.60.1.10" if role == "vm_a" else "10.60.1.11"
            public_ip = "8.8.8.8" if role == "vm_a" else "1.1.1.1"
            stdout = json.dumps({"Reservations": [{"Instances": [{"InstanceId": instance_id,
                "State": {"Name": "running"}, "Tags": [{"Key": "cnb_run_id", "Value": self.run_id},
                    {"Key": "cnb_deployment_id", "Value": self.deployment_id}, {"Key": "cnb_role", "Value": role}],
                "InstanceType": "m7i.xlarge", "ImageId": "ami-0123456789abcdef0",
                "PrivateIpAddress": private_ip, "PublicIpAddress": public_ip,
                "Placement": {"AvailabilityZone": "eu-central-1a", "GroupName": None},
                "NetworkInterfaces": [{"InterfaceType": "interface"}],
                "BlockDeviceMappings": [{"Ebs": {"VolumeId": "vol-a" if role == "vm_a" else "vol-b",
                    "DeleteOnTermination": True}}], "LaunchTime": "2026-10-22T11:50:00Z"}]}]})
        elif action.startswith("aws-describe-instance-topology"):
            stdout = json.dumps({"Instances": [{"ZoneId": "use1-az2"}]})
        elif action.startswith("aws-describe-images"):
            stdout = json.dumps({"Images": [{"ImageId": "ami-0123456789abcdef0", "OwnerId": "099720109477",
                "Architecture": "x86_64", "VirtualizationType": "hvm", "RootDeviceType": "ebs",
                "State": "available", "Name": "ubuntu-noble-24.04-amd64-server-20261001", "EnaSupport": True}]})
        elif action.startswith("aws-describe-volumes"):
            stdout = json.dumps({"Volumes": [{"VolumeId": "vol-a", "Size": 32, "VolumeType": "gp3"}]})
        elif action.startswith("aws-describe-instance-types"):
            stdout = json.dumps({"InstanceTypes": [{"InstanceType": "m7i.xlarge",
                "ProcessorInfo": {"SupportedArchitectures": ["x86_64"]},
                "VCpuInfo": {"DefaultVCpus": 4}, "MemoryInfo": {"SizeInMiB": 16384}}]})
        elif action.startswith("aws-get-console-output"):
            instance_id = request.argv[request.argv.index("--instance-id") + 1]
            stdout = json.dumps({"InstanceId": instance_id, "Timestamp": "2026-10-22T11:51:00Z",
                "Output": "CNB_HOSTKEY_BOOT_ID=12345678-1234-4234-8234-123456789abc\nCNB_ED25519_HOST_FINGERPRINT=SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAA\n"})
        elif action.startswith("aws-host-keyscan-"):
            stdout = "fake-host ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n"
        elif action.startswith("aws-host-key-fingerprint-"):
            stdout = "256 SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAA fake-host (ED25519)\n"
        elif action.startswith("aws-expiry-start-"):
            region = action.removeprefix("aws-expiry-start-")
            stdout = json.dumps({"AutomationExecutionId": f"exec-{region}"})
        elif action.startswith("aws-expiry-confirm-"):
            region = action.removeprefix("aws-expiry-confirm-")
            parameters = {"AutomationAssumeRole": [f"arn:aws:iam::123456789012:role/expiry-{region}"],
                "ExpiryTriggerUtc": ["2026-10-22T12:14:00Z"], "RunId": [self.run_id],
                "DeploymentId": [self.deployment_id], "Region": [region]}
            stdout = json.dumps({"AutomationExecution": {"AutomationExecutionId": f"exec-{region}",
                "DocumentName": f"expiry-{region}", "DocumentVersion": "1",
                "AutomationExecutionStatus": "InProgress", "CurrentAction": "aws:sleep",
                "CurrentStepName": "waitUntilExpiry", "Parameters": parameters,
                "StepExecutionsTruncated": False, "StepExecutions": [{"StepName": "waitUntilExpiry",
                    "Action": "aws:sleep", "StepStatus": "InProgress",
                    "Inputs": {"Timestamp": "2026-10-22T12:14:00Z"}}]}})
        result = CommandResult(action_id=action, outcome="succeeded", started_at=NOW,
            finished_at=NOW, duration_seconds=0, exit_code=0, stdout=stdout, stderr="")
        return result


class FakePreparedAwsRunner(FakeAwsRunner):
    """The real F02/F09 adapters run over source-shaped fake command responses."""
    def __init__(self):
        super().__init__()
        from tests.fixtures.f02r.support import BootstrapRunner
        self.bootstrap_runner = BootstrapRunner()
        self.prepared = None
        self.remote_files = {"vm_a": {}, "vm_b": {}}
        self.uploads = {}
        self.current_elapsed = 0
        self.guest_request = None
        self.guest_outcome = None
        self.guest_exception = None
        self.native_root = None
        self.workspace_roots = {}
        self.diagnostics_contexts = {}
        self.diagnostics_outcomes = {}
        self.inventory_roles = {}
        self.full_native = False

    def run(self, request):
        from tests.fixtures.f02r.support import nft_listing
        action = request.action_id
        if action.startswith("f02r-ready-"):
            self.actions.append(action)
            name = action.removeprefix("f02r-ready-").rsplit("-", 1)[0]
            tool = name.removeprefix("service-").removeprefix("socket-").removeprefix("unit-config-").removeprefix("process-")
            if name.startswith("cloud-init"):
                stdout = "status: done"
            elif name == "private-icmp":
                stdout = "64 bytes from 10.60.1.11"
            elif name in {"private-tcp", "ssh-vm-a", "ssh-vm-b"}:
                stdout = ""
            elif name.startswith("service-"):
                port, pid = (11111, 20) if tool == "sockperf" else (5201, 21)
                args = f"server -i 10.60.1.11 -p {port}" if tool == "sockperf" else "-s -B 10.60.1.11 -p 5201 --json-stream --forceflush"
                stdout = (f"LoadState=loaded\nActiveState=active\nMainPID={pid}\n"
                    f"ExecStart={{ path=/var/lib/cnb/install/bin/{tool}; argv[]=/var/lib/cnb/install/bin/{tool} {args}; }}\n"
                    "StandardOutput=append\nStandardError=append\nRestart=no\n")
                if self.full_native and tool == "iperf3":
                    stdout += "Environment=CNB_ACQUISITION_ID=f02r-vm_b-listener-iperf3\n"
            elif name.startswith("unit-config-"):
                stdout = f"[Service]\nStandardOutput=append:/var/lib/cnb/work/server-logs/{'sockperf.log' if tool == 'sockperf' else 'iperf3-server.jsonl'}\nStandardError=append:/var/lib/cnb/work/server-logs/{'sockperf.err' if tool == 'sockperf' else 'iperf3-server.err'}\n"
                if self.full_native and tool == "iperf3":
                    stdout += "Environment=CNB_ACQUISITION_ID=f02r-vm_b-listener-iperf3\n"
            elif name.startswith("process-"):
                if self.full_native:
                    from tests.integration.test_acquisition_producer import _build
                    from cloud_network_benchmark.contracts.common import VmRole
                    binary_hash = _build(tool, VmRole.VM_B, f"f02r-vm_b-{tool}-build").binary_sha256
                else:
                    binary_hash = "c" * 64
                stdout = f"/var/lib/cnb/install/bin/{tool}\n{binary_hash} /proc/{20 if tool == 'sockperf' else 21}/exe"
            elif name == "socket-sockperf":
                stdout = 'UNCONN 0 0 10.60.1.11:11111 0.0.0.0:* users:(("sockperf",pid=20,fd=3))'
            elif name == "socket-iperf3":
                stdout = 'LISTEN 0 4096 10.60.1.11:5201 0.0.0.0:* users:(("iperf3",pid=21,fd=3))'
            elif name == "guest-acl":
                stdout = nft_listing(self.deployment_id).replace("10.0.0.1", "10.60.1.10").replace("10.0.0.2", "10.60.1.11")
            else:
                stdout = "authenticated"
            if name.startswith("service-"):
                requested = request.argv[-1].split("--property=",1)[1].split(",") if "--property=" in request.argv[-1] else []
                stdout = "\n".join(line for line in stdout.splitlines() if line.split("=",1)[0] in requested or line == "[Service]")
            return CommandResult(action_id=action, outcome="succeeded", started_at=NOW,
                finished_at=NOW, duration_seconds=0, exit_code=0, stdout=stdout, stderr="")
        if action.startswith("f02r-") and not action.startswith(("f02r-retrieve-observe-", "f02r-retrieve-")):
            self.actions.append(action)
            if self.full_native and action == "f02r-vm_b-listener-config-iperf3":
                value = self.bootstrap_runner.run(request)
                stdout = value.stdout + "Environment=CNB_ACQUISITION_ID=f02r-vm_b-listener-iperf3\n"
                return value.model_copy(update={"stdout": stdout})
            if self.full_native and action == "f02r-vm_b-listener-verify-iperf3":
                stdout = ("LoadState=loaded\nActiveState=active\nMainPID=21\n"
                    "ExecStart={ path=/var/lib/cnb/install/bin/iperf3; argv[]=/var/lib/cnb/install/bin/iperf3 -s -B 10.60.1.11 -p 5201 --json-stream --forceflush; }\n"
                    "StandardOutput=append\nStandardError=append\nRestart=no\n"
                    "Environment=CNB_ACQUISITION_ID=f02r-vm_b-listener-iperf3")
                return CommandResult(action_id=action, outcome="succeeded", started_at=NOW,
                    finished_at=NOW, duration_seconds=0, exit_code=0, stdout=stdout, stderr="")
            if self.full_native and action in {"f02r-vm_b-listener-process-sockperf",
                    "f02r-vm_b-listener-process-iperf3"}:
                tool = action.rsplit("-", 1)[-1]
                from tests.integration.test_acquisition_producer import _build
                from cloud_network_benchmark.contracts.common import VmRole
                observed = _build(tool, VmRole.VM_B, f"f02r-vm_b-{tool}-build")
                stdout = f"/var/lib/cnb/install/bin/{tool}\n{observed.binary_sha256} /proc/{20 if tool == 'sockperf' else 21}/exe"
                return CommandResult(action_id=action, outcome="succeeded", started_at=NOW,
                    finished_at=NOW, duration_seconds=0, exit_code=0, stdout=stdout, stderr="")
            match = re.fullmatch(r"f02r-(vm_[ab])-(sockperf|iperf3)-(\d+)", action)
            if self.full_native and match:
                role, tool, index_text = match.groups()
                index = int(index_text)
                from tests.integration.test_acquisition_producer import _build
                from cloud_network_benchmark.contracts.common import VmRole
                observed = _build(tool, VmRole(role), f"f02r-{role}-{tool}-build")
                binary_version = ("sockperf, version 3.10\nLinux synthetic-host 6.8.0\nCompiler: gcc 13.2.0"
                    if tool == "sockperf" else observed.binary_version)
                archive_path = f"/var/lib/cnb/work/{tool}.tar.gz"
                binary_path = f"/var/lib/cnb/install/bin/{tool}"
                build_configuration = (f"CC=${{CC:-cc}}; compiler={observed.compiler_version}; "
                    "./configure --prefix=/var/lib/cnb/install; make -j2")
                identity = "\n".join(("CNB_BUILD_V1", f"tool={tool}",
                    f"source_reference={observed.source_reference}", f"source_sha256={observed.source_sha256}",
                    f"upstream_commit={observed.upstream_commit}", f"patch_path={observed.patch_path}",
                    f"patch_sha256={observed.patch_sha256}",
                    "compiler_version_b64=" + base64.b64encode(observed.compiler_version.encode()).decode(),
                    "build_configuration_b64=" + base64.b64encode(build_configuration.encode()).decode(),
                    f"binary_path={binary_path}",
                    "binary_version_b64=" + base64.b64encode(binary_version.encode()).decode(),
                    f"binary_sha256={observed.binary_sha256}", "observation_capability=CNB_ACQ_V1",
                    f"observation_semantics={observed.observation_semantics}",
                    f"capabilities={','.join(observed.capabilities)}", ""))
                if index == 2:
                    stdout = f"{observed.source_sha256} {archive_path}"
                elif index == 5:
                    stdout = observed.compiler_version
                elif index == 6:
                    stdout = binary_version
                elif index == 7:
                    stdout = f"{observed.binary_sha256} {binary_path}"
                elif any("CNB_BUILD_V1" in token for token in request.argv):
                    stdout = identity
                elif any("printf CNB_ACQ_V1" in token for token in request.argv):
                    stdout = "CNB_ACQ_V1"
                elif index >= 8:
                    if tool == "sockperf":
                        stdout = ("Usage: sockperf [options]\nserver under-load -i -p -m --mps "
                            "--reply-every --full-rtt --no-rdtsc --full-log -t" if index == 8 else
                            "Usage: sockperf server -i -p" if index == 9 else
                            "Usage: sockperf under-load -i -p -m --mps --reply-every --full-rtt --no-rdtsc --full-log -t")
                    else:
                        stdout = "Usage: iperf3 [options]\n-s -c -B -p -P -t -O -i -l -C cubic -Z -J --get-server-output --json-stream --forceflush"
                else:
                    stdout = "ok"
                return CommandResult(action_id=action, outcome="succeeded", started_at=NOW,
                    finished_at=NOW, duration_seconds=0, exit_code=0, stdout=stdout, stderr="")
            value = self.bootstrap_runner.run(request)
            stdout = value.stdout.replace("10.0.0.1", "10.60.1.10").replace("10.0.0.2", "10.60.1.11")
            if action.endswith("acl-inspect"):
                stdout = stdout.replace("chain cnb_campaign_aws_deployment", f"chain {request.argv[-1].split()[-1]}")
            value = value.model_copy(update={"stdout": stdout})
            return value
        if action.startswith("f09-runtime-"):
            self.actions.append(action)
            if request.action_type == "scp":
                return CommandResult(action_id=action, outcome="succeeded", started_at=NOW,
                    finished_at=NOW, duration_seconds=0, exit_code=0, stdout="", stderr="")
            if "runtime-verify" in action:
                return CommandResult(action_id=action, outcome="succeeded", started_at=NOW,
                    finished_at=NOW, duration_seconds=0, exit_code=0,
                    stdout=json.dumps({"python":"3.11.0","pydantic":"2.10.0","yaml":"6.0.2"}), stderr="")
            return CommandResult(action_id=action, outcome="succeeded", started_at=NOW,
                finished_at=NOW, duration_seconds=0, exit_code=0, stdout="", stderr="")
        if action.startswith("f09-guest-request-"):
            self.actions.append(action)
            local_source = next(Path(value) for value in request.argv
                if value.endswith("request.json") and Path(value).is_file())
            self.guest_request = local_source.read_text(encoding="utf-8")
            return CommandResult(action_id=action, outcome="succeeded", started_at=NOW,
                finished_at=NOW, duration_seconds=0, exit_code=0, stdout="", stderr="")
        if action.startswith("f09-guest-run-"):
            self.actions.append(action)
            from cloud_network_benchmark.lifecycle_guest import GuestRequest, execute_request
            from tests.f03_support import FakeClock as GuestClock, FakeProcesses
            from tests.integration.test_acquisition_producer import _fixture_processes
            class NativeFixtureProcesses(FakeProcesses):
                """Run the actual F03 driver while returning captured F08 grammar bytes."""
                def start(self, request, *, stdout_path, stderr_path):
                    handle = super().start(request, stdout_path=stdout_path, stderr_path=stderr_path)
                    executable = Path(request.argv[0]).name
                    fixture_root = ROOT / "tests/fixtures/f08/native"
                    if executable == "sockperf":
                        native = Path(request.argv[request.argv.index("--full-log") + 1])
                        native.write_bytes((fixture_root / "sockperf.csv").read_bytes())
                    elif executable == "iperf3":
                        stdout_path.write_bytes((fixture_root / "iperf3.json").read_bytes())
                    return handle
            guest_clock = GuestClock()
            guest_clock.utc_adjustment = NOW - guest_clock.origin
            guest_request = GuestRequest.model_validate_json(self.guest_request).model_copy(update={
                "output_root": self.native_root,
                "package_root": ROOT / "src/cloud_network_benchmark"})
            self.native_root.mkdir(parents=True, exist_ok=True)
            try:
                if self.full_native:
                    processes = _fixture_processes(guest_clock)
                    original_start = processes.start
                    def source_observed_start(native_request, *, stdout_path, stderr_path):
                        handle = original_start(native_request, stdout_path=stdout_path, stderr_path=stderr_path)
                        executable = Path(native_request.argv[0]).name
                        if executable == "sockperf":
                            duration = int(native_request.argv[native_request.argv.index("-t") + 1])
                            base_ns = int(handle.started_monotonic * 1_000_000_000)
                            timer_start_ns = base_ns + 1_100_000_000
                            late_tail = .1 if getattr(self, "late_tail", False) else 0.0
                            termination_ns = timer_start_ns + int((duration + late_tail) * 1_000_000_000)
                            # The pinned client starts its -t timer after
                            # Starting test / receiver readiness, not at process spawn.
                            # The positive case models observed delayed signal delivery;
                            # the punctual case preserves the honest short-tail failure.
                            handle.finishes += 1.1 + late_tail
                            rows = []
                            for line in stderr_path.read_text(encoding="utf-8").splitlines():
                                if not line.startswith("CNB_ACQ_V1 "):
                                    continue
                                record = json.loads(line[len("CNB_ACQ_V1 "):])
                                if record.get("record_kind") == "clock_anchor":
                                    realtime_ns = int((NOW.timestamp() + base_ns / 1_000_000_000 - 1000)
                                        * 1_000_000_000)
                                    record.update(mono_before_ns=base_ns, realtime_ns=realtime_ns,
                                        mono_after_ns=base_ns + 1000)
                                elif record.get("record_kind") == "probe_window":
                                    record.update(sender_enabled_ns=base_ns + 1_000_000_000,
                                        receiver_enabled_ns=timer_start_ns,
                                        termination_ns=termination_ns,
                                        native_filter_start_ns=timer_start_ns + 400_000_000,
                                        native_filter_end_ns=termination_ns - 50_000_000)
                                if record["record_kind"] == "receiver_interval" and record["omitted"]:
                                    record["interval_end_us"] = base_ns // 1000 + 4_000_000
                                rows.append("CNB_ACQ_V1 " + json.dumps(record, separators=(",", ":")))
                            stderr_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
                            csv_path = Path(native_request.argv[native_request.argv.index("--full-log") + 1])
                            sample_start = handle.started_monotonic + 1.5
                            sample_end = handle.started_monotonic + 1.1 + duration + late_tail - .05 - .00001
                            csv_path.write_text("sockperf: test output\n------------------------------\n"
                                "packet, txTime(sec), rxTime(sec), rtt(usec)\n"
                                f"0, {sample_start:.6f}, {sample_start + .000001:.6f}, 1.000\n"
                                f"1, {sample_end:.6f}, {sample_end + .000001:.6f}, 1.000\n"
                                "------------------------------\n"
                                f"[Total Run] RunTime={duration + late_tail:.3f} sec; Warm up time=5000 msec; SentMessages={int((duration + late_tail) * 1000)}; ReceivedMessages=2\n"
                                f"[Valid Duration] RunTime={duration + late_tail - .45:.3f} sec; SentMessages={int((duration + late_tail - .45) * 1000)}; ReceivedMessages=2\n",
                                encoding="utf-8")
                        elif executable == "iperf3":
                            duration = int(native_request.argv[native_request.argv.index("-t") + 1])
                            streams = int(native_request.argv[native_request.argv.index("-P") + 1])
                            cookie = native_request.action_id
                            settings = {"protocol": "TCP", "num_streams": streams, "omit": 5,
                                "duration": duration, "interval": 1, "blksize": 131072, "reverse": False}
                            connected = [{"socket": 17 + i, "local_host": guest_request.b_private_ipv4,
                                "local_port": 5201, "remote_host": "10.60.1.10", "remote_port": 50100 + i}
                                for i in range(streams)]
                            rows = []
                            for i in range(streams):
                                rows.extend([{"socket": 17 + i, "start": 0, "end": 4, "seconds": 4,
                                    "bytes": 250, "omitted": True, "sender": False},
                                    {"socket": 17 + i, "start": 0, "end": duration, "seconds": duration,
                                    "bytes": 125000000 // streams, "omitted": False, "sender": False}])
                            receiver = {"start": {"cookie": cookie, "test_start": settings, "connected": connected},
                                "intervals": [{"streams": [row for row in rows if row["omitted"]]},
                                    {"streams": [row for row in rows if not row["omitted"]]}],
                                "end": {"sum_received": {"bytes": 125000000, "seconds": duration}}}
                            client = {"start": {"cookie": cookie, "test_start": settings},
                                "server_output_json": receiver,
                                "end": {"sum_received": {"bytes": 125000000, "seconds": duration}}}
                            stdout_path.write_text(json.dumps(client), encoding="utf-8")
                        return handle
                    processes.start = source_observed_start
                else:
                    processes = NativeFixtureProcesses(guest_clock)
                outcome = execute_request(guest_request, processes=processes, clock=guest_clock)
            except Exception as exc:
                self.guest_exception = f"{type(exc).__name__}: {exc}"
                raise
            self.guest_outcome = outcome.model_dump_json()
            if self.full_native:
                self._write_source_observed_receiver_logs(outcome)
            return CommandResult(action_id=action, outcome="succeeded", started_at=NOW,
                finished_at=NOW, duration_seconds=0, exit_code=0, stdout="", stderr="")
        if action.startswith("f09-guest-outcome-"):
            self.actions.append(action)
            if self.guest_outcome is not None:
                Path(request.argv[-1]).write_text(self.guest_outcome, encoding="utf-8")
            return CommandResult(action_id=action, outcome="succeeded", started_at=NOW,
                finished_at=NOW, duration_seconds=0, exit_code=0, stdout="", stderr="")
        if action.startswith("f09-diagnostics-context-"):
            self.actions.append(action)
            source = next(Path(value) for value in request.argv
                if value.endswith("context.json") and Path(value).is_file())
            invocation = action.removeprefix("f09-diagnostics-context-")
            self.diagnostics_contexts[invocation] = json.loads(source.read_text(encoding="utf-8"))
            return CommandResult(action_id=action, outcome="succeeded", started_at=NOW,
                finished_at=NOW, duration_seconds=0, exit_code=0, stdout="", stderr="")
        if action.startswith("f09-diagnostics-start-"):
            self.actions.append(action)
            invocation = action.removeprefix("f09-diagnostics-start-")
            context = self.diagnostics_contexts[invocation]
            role, root = context["vm_role"], self.workspace_roots[context["vm_role"]]
            if role == "vm_b":
                logs = root / "server-logs"
                logs.mkdir(parents=True, exist_ok=True)
                (logs / "sockperf.log").write_text("Sockperf listener captured by offline source fixture\n", encoding="utf-8")
                (logs / "iperf3-server.jsonl").write_text(json.dumps({"event":"server_listening","data":{}})+"\n", encoding="utf-8")
                (logs / "iperf3-server.err").write_text("No server stderr bytes in offline fixture.\n", encoding="utf-8")
            from cloud_network_benchmark.contracts.measurements import VmMetadata, DiagnosticSample, ClockMapping
            metadata_path = root / "metadata" / f"{role}.json"
            metadata_path.parent.mkdir(parents=True, exist_ok=True)
            metadata_seed = dict(context["metadata_seed"])
            supplied_builds = metadata_seed.get("source_builds", {}).get("value", {})
            metadata_seed["tool_versions"] = {"value": {tool: {
                "version": {"value": build["binary_version"]},
                "binary_sha256": {"value": build["binary_sha256"]}}
                for tool, build in supplied_builds.items()}}
            metadata_seed["source_builds"] = {"value": {tool: {
                "supplied": build,
                "current_binary_sha256": {"value": build["binary_sha256"]},
                "matches_bootstrap_binary": {"value": True},
                "matches_bootstrap_version": {"value": True}}
                for tool, build in supplied_builds.items()}}
            metadata_path.write_text(VmMetadata.model_validate(metadata_seed).model_dump_json()+"\n", encoding="utf-8")
            diag_path = root / "diagnostics" / f"{role}.jsonl"
            diag_path.parent.mkdir(parents=True, exist_ok=True)
            sample = DiagnosticSample(schema_version=2, deployment_id=context["deployment_id"], vm_role=role,
                sampled_at=NOW, monotonic_seconds=1, interval_seconds=1,
                cpu_total_percent={"value":5}, cpu_max_core_percent={"value":10}, cpu_steal_percent={"value":0},
                tcp_retransmissions={"value":0}, interface_rx_drops={"value":0}, interface_tx_drops={"value":0},
                interface_rx_errors={"value":0}, interface_tx_errors={"value":0}, rx_bytes={"value":1000}, tx_bytes={"value":1000})
            diag_path.write_text(sample.model_dump_json()+"\n", encoding="utf-8")
            mapping = ClockMapping(vm_role=role, monotonic_seconds=1000 if self.full_native else 1, utc_timestamp=NOW,
                estimated_error_ms={"value":1}, synchronization_source={"value":"synthetic chrony fixture"})
            (root / "diagnostics" / f"{role}-clock.jsonl").write_text(mapping.model_dump_json()+"\n", encoding="utf-8")
            output_root = context["output_root"]
            self.diagnostics_outcomes[invocation] = {"deployment_id":context["deployment_id"],"vm_role":role,
                "state":"interrupted","closed_paths":[f"{output_root}/metadata/{role}.json",
                    f"{output_root}/diagnostics/{role}.jsonl",f"{output_root}/diagnostics/{role}-clock.jsonl"]}
            stdout = json.dumps({"pid":501,"process_group":501,
                "boot_id":"12345678-1234-4234-8234-123456789abc","start_ticks":1234})
            return CommandResult(action_id=action, outcome="succeeded", started_at=NOW,
                finished_at=NOW, duration_seconds=0, exit_code=0, stdout=stdout, stderr="")
        if action.startswith("f09-diagnostics-stop-"):
            self.actions.append(action)
            closed_at = NOW + timedelta(minutes=5)
            return CommandResult(action_id=action,outcome="succeeded",started_at=closed_at,finished_at=closed_at,
                duration_seconds=0,exit_code=0,stdout=json.dumps({"state":"stopped","closed_at_utc":closed_at.isoformat()}),stderr="")
        if action.startswith("f09-diagnostics-outcome-"):
            self.actions.append(action)
            invocation = action.removeprefix("f09-diagnostics-outcome-")
            return CommandResult(action_id=action,outcome="succeeded",started_at=NOW,finished_at=NOW,
                duration_seconds=0,exit_code=0,stdout=json.dumps(self.diagnostics_outcomes[invocation]),stderr="")
        if action.startswith("f09-listener-exit-"):
            self.actions.append(action)
            stamp = (NOW + timedelta(minutes=5)).strftime("%a %Y-%m-%d %H:%M:%S UTC")
            stdout = f"LoadState=loaded\nActiveState=inactive\nMainPID=0\nInactiveEnterTimestamp={stamp}\n"
            return CommandResult(action_id=action,outcome="succeeded",started_at=NOW,finished_at=NOW,
                duration_seconds=0,exit_code=0,stdout=stdout,stderr="")
        if action.startswith("f09-inventory-"):
            self.actions.append(action)
            role = "vm_a" if action.endswith("vm_a") else "vm_b"
            root = self.workspace_roots[role]
            rows=[]
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    data=path.read_bytes(); rows.append({"path":path.relative_to(root).as_posix(),
                        "size_bytes":len(data),"sha256":hashlib.sha256(data).hexdigest()})
                    self.inventory_roles[path.relative_to(root).as_posix()] = role
            return CommandResult(action_id=action,outcome="succeeded",started_at=NOW,finished_at=NOW,
                duration_seconds=0,exit_code=0,stdout=json.dumps({"files":rows},sort_keys=True,separators=(",",":")),stderr="")
        if action.startswith("f02r-retrieve-observe-"):
            self.actions.append(action)
            match = re.search(r"\[ -f (.+?) \] &&", request.argv[-1])
            assert match is not None, request.argv[-1]
            source = __import__("shlex").split(match.group(1))[0]
            from pathlib import PurePosixPath
            relative = PurePosixPath(source).relative_to("/var/lib/cnb/work").as_posix()
            artifact_id = action.removeprefix("f02r-retrieve-observe-")
            role = "vm_b" if artifact_id.startswith("vm_b_") or artifact_id in {
                "sockperf_server_log", "iperf3_server_log", "iperf3_server_error"} else "vm_a"
            local = self.workspace_roots[role] / relative
            data = local.read_bytes()
            return CommandResult(action_id=action,outcome="succeeded",started_at=NOW,finished_at=NOW,
                duration_seconds=0,exit_code=0,stdout=f"{len(data)} {hashlib.sha256(data).hexdigest()}",stderr="")
        if action.startswith("f02r-retrieve-") and request.action_type == "scp":
            self.actions.append(action)
            remote = next(value for value in request.argv if ":/var/lib/cnb/work/" in value)
            from pathlib import PurePosixPath
            role = "vm_a" if "8.8.8.8" in remote else "vm_b"
            local = self.workspace_roots[role] / PurePosixPath(remote.split(":",1)[1]).relative_to("/var/lib/cnb/work")
            Path(request.argv[-1]).write_bytes(local.read_bytes())
            return CommandResult(action_id=action,outcome="succeeded",started_at=NOW,finished_at=NOW,
                duration_seconds=0,exit_code=0,stdout="",stderr="")
        if action.startswith("f09-control-clock-"):
            self.actions.append(action)
            timestamp = (NOW + timedelta(seconds=self.current_elapsed)).isoformat(timespec="microseconds").replace("+00:00", "Z")
            return CommandResult(action_id=action, outcome="succeeded", started_at=NOW,
                finished_at=NOW, duration_seconds=0, exit_code=0, stdout=timestamp, stderr="")
        return super().run(request)

    def _write_source_observed_receiver_logs(self, outcome):
        root = self.workspace_roots["vm_b"] / "server-logs"
        root.mkdir(parents=True, exist_ok=True)
        event_lines, source_lines = [], []
        for command in outcome.commands:
            request = command.get("request", {})
            argv = request.get("argv", [])
            if not argv or Path(argv[0]).name != "iperf3":
                continue
            duration = int(argv[argv.index("-t") + 1])
            streams = int(argv[argv.index("-P") + 1])
            cookie = command["result"]["action_id"]
            settings = {"protocol": "TCP", "num_streams": streams, "omit": 5,
                "duration": duration, "interval": 1, "blksize": 131072, "reverse": False}
            command_started_at = datetime.fromisoformat(command["started_at"]["value"].replace("Z", "+00:00"))
            start_mono = 1000 + (command_started_at - NOW).total_seconds()
            start_us = int(start_mono * 1_000_000)
            connected = [{"socket": 17 + i, "local_host": "10.60.1.11", "local_port": 5201,
                "remote_host": "10.60.1.10", "remote_port": 50100 + i} for i in range(streams)]
            native_intervals = []
            for i in range(streams):
                native_intervals.extend([
                    {"socket": 17 + i, "start": 0, "end": 5, "seconds": 5, "bytes": 250,
                        "omitted": True, "sender": False},
                    {"socket": 17 + i, "start": 0, "end": duration, "seconds": duration,
                        "bytes": 125000000 // streams, "omitted": False, "sender": False}])
            session_start = command_started_at
            events = [{"event": "start", "data": {"cookie": cookie, "test_start": settings,
                "connected": connected, "timestamp": {"timesecs": session_start.timestamp()}}},
                {"event": "interval", "data": {"streams": [row for row in native_intervals if row["omitted"]]}},
                {"event": "interval", "data": {"streams": [row for row in native_intervals if not row["omitted"]]}},
                {"event": "end", "data": {"sum_received": {"bytes": 125000000, "seconds": duration}}}]
            event_lines.extend(json.dumps(event, separators=(",", ":")) for event in events)
            common = {"format": 1, "tool": "iperf3", "semantics_id": "iperf-3.17.1-stats-v1",
                "invocation_id": "f02r-vm_b-listener-iperf3", "pid": 51,
                "boot_id": "12345678-1234-4234-8234-123456789def", "clock": "CLOCK_MONOTONIC", "cookie": cookie}
            anchor = {**common, "record_kind": "clock_anchor", "mono_before_ns": 1_000_000_000_000,
                "realtime_ns": int(NOW.timestamp() * 1_000_000_000), "mono_after_ns": 1_000_000_001_000}
            source_lines.append("CNB_ACQ_V1 " + json.dumps(anchor, separators=(",", ":")))
            source_lines.append("CNB_ACQ_V1 " + json.dumps({**common, "record_kind": "session_settings",
                "protocol": "TCP", "num_streams": streams, "omit": 5, "duration": duration,
                "interval_us": 1_000_000, "block_size": 131072, "reverse": False}, separators=(",", ":")))
            origins = []
            for i in range(streams):
                stream = str(17 + i)
                initial, post = start_us, start_us + 5_000_000
                origins.append((i, stream, initial, post))
                source_lines.append("CNB_ACQ_V1 " + json.dumps({**common, "record_kind": "stats_origin",
                    "stream_id": stream, "phase": "initial", "origin_us": initial,
                    "start_time_fixed_us": initial}, separators=(",", ":")))
                source_lines.append("CNB_ACQ_V1 " + json.dumps({**common, "record_kind": "stats_origin",
                    "stream_id": stream, "phase": "post_omit", "origin_us": post,
                    "start_time_fixed_us": initial}, separators=(",", ":")))
            # iperf emits each interval event with all streams, so the source
            # records must preserve that native order rather than interleave phases.
            for omitted in (True, False):
                for i, stream, initial, post in origins:
                    start, end = ((initial, initial + 4_000_000) if omitted else
                        (post, post + duration * 1_000_000))
                    count = 250 if omitted else 125000000 // streams
                    source_lines.append("CNB_ACQ_V1 " + json.dumps({**common, "record_kind": "receiver_interval",
                        "stream_id": stream, "role": "receiver", "protocol": "TCP",
                        "local_host": "10.60.1.11", "local_port": "5201",
                        "remote_host": "10.60.1.10", "remote_port": str(50100 + i),
                        "interval_start_us": start, "interval_end_us": end, "bytes": count,
                        "omitted": omitted}, separators=(",", ":")))
        (root / "iperf3-server.jsonl").write_text("\n".join(event_lines) + "\n", encoding="utf-8")
        (root / "iperf3-server.err").write_text("\n".join(source_lines) + "\n", encoding="utf-8")


def _cost_source(path: Path, run_id: str, cost_input_id: str) -> None:
    payload = json.loads((ROOT / "tests/fixtures/f11/cost-inputs.json").read_text())
    payload.update(cost_input_id=cost_input_id, prices_checked_on="2026-10-22",
        acquired_on="2026-10-22", source_sha256="a" * 64,
        funding={"primary": "250", "replacements": "0", "tax": "0", "uncertainty": "0"},
        refresh_required_before_paid_execution=False)
    region = "eu-central-1"
    components = {}
    for name in ("compute", "disks", "addresses", "traffic_export", "tax", "uncertainty"):
        components[name] = {"amount_usd": "0.1", "quantity": "1", "unit": "unit",
            "inclusion_rule": "synthetic test source", "funding_bucket": "primary"}
    components["expiry_cleanup_support"] = {"amount_usd": "0.0139", "quantity": "1",
        "unit": "region-session", "inclusion_rule": "synthetic test source", "funding_bucket": "primary"}
    payload["provider_run_envelopes"] = {"aws": {"envelope_id": "fixture-aws", "provider": "aws",
        "run_id": run_id, "priced_on": "2026-10-22", "source_kind": "provider-price-input",
        "regions": [region], "unit_basis": "one-two-vm-provider-run", "components": components}}
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _runtime(tmp_path, repository_root, cost_path, initialized_parent, cost_digest, *, full_native=False):
    aws_fixture = repository_root / "tests/fixtures/aws/cost-inputs/aws-ssm-automation-public-2026-10-22.json"
    fixture_hash = hashlib.sha256(aws_fixture.read_bytes()).hexdigest()
    refs = {}
    for gate in ("f10_offline", "paid_authorization", "quota_review"):
        evidence_source = tmp_path / f"{gate}.md"
        evidence_source.write_text(f"synthetic test evidence for {gate}\n", encoding="utf-8")
        evidence = {"evidence_id": f"fixture-{gate}", "source_path": evidence_source.name,
            "source_sha256": hashlib.sha256(evidence_source.read_bytes()).hexdigest()}
        evidence_path = tmp_path / f"{gate}.json"
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
        refs[gate] = evidence_path
    key = tmp_path / "test.pub"
    key.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA test\n")
    identity = tmp_path / "fake-private-key"
    identity.write_text("synthetic private identity for fake runner only\n")
    identity.chmod(0o600)
    from cloud_network_benchmark.admission import WorkflowAllowance
    from cloud_network_benchmark.access import TimeoutPolicy
    from cloud_network_benchmark.bootstrap import BootstrapConfig, SourceBuild
    from cloud_network_benchmark.contracts.common import VmRole
    from cloud_network_benchmark.readiness import ReadinessConfig
    from tests.integration.test_acquisition_producer import _build
    sockperf_build = _build("sockperf", VmRole.VM_A, "f02r-vm_a-sockperf-build")
    iperf3_build = _build("iperf3", VmRole.VM_A, "f02r-vm_a-iperf3-build")
    if full_native:
        source_builds = [SourceBuild(tool=build.tool, source_url=f"https://example.invalid/{build.tool}.tar.gz",
            source_reference=build.source_reference, archive_name=f"{build.tool}.tar.gz",
            source_sha256=build.source_sha256, upstream_commit=build.upstream_commit,
            observation_patch_path=build.patch_path, observation_patch_sha256=build.patch_sha256)
            for build in (sockperf_build, iperf3_build)]
    else:
        source_builds = [SourceBuild(tool="sockperf", source_url="https://example.invalid/sockperf.tar.gz",
            source_reference="sockperf-src", archive_name="sockperf.tar.gz", source_sha256="a" * 64),
            SourceBuild(tool="iperf3", source_url="https://example.invalid/iperf3.tar.gz",
            source_reference="iperf3-src", archive_name="iperf3.tar.gz", source_sha256="b" * 64)]
    bootstrap = BootstrapConfig(sources=source_builds,
        remote_root="/var/lib/cnb", workspace="/var/lib/cnb/work",
        install_prefix="/var/lib/cnb/install")
    access = {"timeouts": TimeoutPolicy(ssh_seconds=5, command_seconds=10,
            cloud_init_seconds=10, private_path_seconds=5, server_seconds=5, transfer_seconds=10),
        "readiness": ReadinessConfig(poll_interval_seconds=.01, attempt_ceiling=1),
        "bootstrap": bootstrap, "guest_remote_root": "/var/lib/cnb/runtime",
        "diagnostic_interfaces": {"aws": {"vm_a":"ens5", "vm_b":"ens5"}}}
    return {"initialized_campaign_manifest": initialized_parent,
        "ledger": {"path": tmp_path / "ledger.json", "scope_id": "test-aws", "initialize_if_missing": True},
        "allowance": WorkflowAllowance(10, 30, 30, 60, 80, 5, 10, 10,
            "synthetic-test-policy", {"aws": 360}), "copy_retry_seconds": 1,
        "approval_refs": refs,
        "approval_evidence": {gate: json.loads(path.read_text()) for gate, path in refs.items()},
        "access": access,
        "providers": {"aws": {"options": {"account_id": "123456789012", "controller_cidr": "203.0.113.4/32",
            "ami_ids": {"eu-central-1": "ami-0123456789abcdef0"}, "public_key_path": key,
            "authentication_reference": str(identity)},
            "bootstrap_template": repository_root / "terraform/aws/pair/main.tf",
            "terraform_directory": repository_root / "terraform/aws",
            "sources": {"expiry_cost_path": aws_fixture, "primary_review_path": None}}}}


@pytest.mark.parametrize(("context_name", "full_native", "late_tail", "expected_validity"), [
    ("failure_check", False, False, "partial"),
    ("failure_check", True, False, "partial"),
    ("failure_check", True, True, "valid"),
])
def test_actual_f11_reserve_then_aws_protect_claim_provision_and_cleanup_fake_only(
        tmp_path, repository_root, request, context_name, full_native, late_tail, expected_validity):
    from cloud_network_benchmark.manifests import initialize_campaign
    from cloud_network_benchmark.lifecycle_runtime import load_initialized_campaign

    source_config = repository_root / "configs/tests/exp-900-single-provider.yaml"
    config_data = yaml.safe_load(source_config.read_text(encoding="utf-8"))
    config_data["context"] = context_name
    config = repository_root / "configs/tests" / f"f09-{tmp_path.name}-{context_name}.yaml"
    request.addfinalizer(lambda: config.unlink(missing_ok=True))
    config.write_text(yaml.safe_dump(config_data, sort_keys=False), encoding="utf-8")
    initialized = initialize_campaign(config, repository_root, tmp_path / "results",
        clock=lambda: NOW, token_source=lambda: "campaign")
    parent_path = Path(initialized.parent_manifest_path)
    parent, children = load_initialized_campaign(parent_path, repository_root=repository_root,
        config_path=config)
    child = children["aws"]
    runner = FakePreparedAwsRunner()
    runner.full_native = full_native
    runner.late_tail = late_tail
    runner.workspace_roots = {"vm_a":tmp_path / "remote-vm-a", "vm_b":tmp_path / "remote-vm-b"}
    runner.native_root = runner.workspace_roots["vm_a"]
    runner.run_id, runner.deployment_id = child.run_id, child.deployment_id
    cost_path = tmp_path / "refreshed-costs.json"
    campaign, _ = resolve_campaign(config, repository_root)
    _cost_source(cost_path, child.run_id, campaign.admission.cost_input_id)
    costs = load_cost_inputs(cost_path)
    runtime = _runtime(tmp_path, repository_root, cost_path, parent_path, costs.payload_sha256, full_native=full_native)
    clock = FakeClock()
    runtime["sleep"] = clock.sleep
    built = build_runtime(campaign=campaign, parent=parent,
        children={"aws": child}, costs=costs, runtime=runtime,
        controller_session_id="fake-session", runner=runner, clock=clock,
        private_root=tmp_path / "private", repository_root=repository_root)
    result = run_lifecycle(ledger=built["ledger"], reservation_request=built["request"],
        admission_plan=built["plan"], allowance=built["allowance"],
        workflow_factory=built["workflow_factory"], clock=clock,
        recovery_path=built["recovery_path"], recovery_factory=built["recovery_factory"])
    assert result.admission_status == "reserved", result.admission_reasons
    provider_result = result.provider_results["aws"]
    assert provider_result.values["protect"].state == "registered"
    assert provider_result.values["claim"].eligible is True, provider_result.values
    assert provider_result.values["claim"].creation_granted is True, provider_result.values
    assert provider_result.primary_failure is not None
    assert provider_result.values["create"].run_id == child.run_id
    assert provider_result.creation_may_have_started is True
    assert provider_result.values["cleanup"] is not None
    assert provider_result.values["prepare"].stage == "prepare"
    assert provider_result.values["prepare"].succeeded is True, provider_result.values["prepare"].evidence
    assert provider_result.values["measure"].succeeded is True, (runner.guest_exception, provider_result.values["measure"].evidence,
        clock.elapsed, built["ledger"].inspect()["reservations"])
    observed_builds = provider_result.values["prepare"].evidence["bootstrap"].builds
    from cloud_network_benchmark.contracts.measurements import CommandEvidence
    for raw_command in provider_result.values["measure"].evidence.outcome.commands:
        command = CommandEvidence.model_validate(raw_command)
        role = str(command.producer)
        tool = Path(command.request["argv"][0]).name
        build = next(item for item in observed_builds if str(item.role) == role and item.tool == tool)
        assert command.binary_version.value == build.binary_version
        assert command.source_identity.value == build.source_reference
    receiver_commands = provider_result.values["measure"].evidence.receiver_command_evidence
    assert {Path(token).name for command in receiver_commands
        for token in command.request["argv"] if token in {"/var/lib/cnb/install/bin/sockperf", "/var/lib/cnb/install/bin/iperf3"}} == {"sockperf", "iperf3"}
    assert provider_result.values["close"].stage == "close"
    assert provider_result.values["close"].succeeded is True, provider_result.values["close"].evidence
    assert provider_result.values["retrieve"].stage == "retrieve"
    assert provider_result.values["retrieve"].succeeded is True, getattr(provider_result.values["retrieve"].evidence, "failures", provider_result.values["retrieve"].evidence)
    assert any(action.startswith("f02r-") for action in runner.actions)
    assert "aws-expiry-start-eu-central-1" in runner.actions
    assert "aws-pair-apply" in runner.actions
    assert "aws-pair-output" in runner.actions
    assert "aws-pair-destroy" in runner.actions
    from cloud_network_benchmark.lifecycle_projection import finalize_production_batch
    from cloud_network_benchmark.contracts.validation import ValidatorInput
    from cloud_network_benchmark.validation import validate_bundle
    finalized = finalize_production_batch(parent_path=parent_path, children={"aws": child},
        parent=parent, lifecycle=result, repository_root=repository_root, evaluated_at=NOW + timedelta(minutes=1))
    result_ref = next(row for row in parent.child_runs if str(row.provider) == "aws")
    result_root = (repository_root / result_ref.result_path).resolve()
    result_file = result_root / "validation" / "input.json"
    saved = ValidatorInput.model_validate_json(result_file.read_text(encoding="utf-8"))
    replay = validate_bundle(saved, evaluated_at=NOW + timedelta(minutes=1))
    assert replay.model_dump(mode="json") == finalized["aws"][0].model_dump(mode="json")
    assert replay.validation_status.data_validity == expected_validity, [
        (item.code, item.severity, item.message, item.unit_id, item.artifact_id) for item in replay.findings]
    assert not [item for item in replay.findings if item.severity == "error"], replay.findings
    if full_native:
        assert not [item for item in replay.findings if item.code == "command.listener_evidence_missing"], replay.findings
    else:
        assert any(item.code == "command.listener_evidence_missing" for item in replay.findings), replay.findings
    if full_native:
        assert not [item for item in replay.findings if item.code.startswith("native.iperf3.interval_")], replay.findings
        if expected_validity == "valid":
            assert not [item for item in replay.findings if item.code == "timing.coverage_unavailable"], replay.findings
        else:
            assert any(item.unit_id == "idle_latency" and item.code == "timing.coverage_unavailable"
                for item in replay.findings), replay.findings
    assert (result_file.parent.parent / "checksums.sha256").is_file()


def test_default_factory_stabilizes_for_full_minimum_with_fake_clock(tmp_path, repository_root, monkeypatch):
    from cloud_network_benchmark.manifests import initialize_campaign
    from cloud_network_benchmark.lifecycle_runtime import load_initialized_campaign
    from cloud_network_benchmark.orchestration import ProviderStageOutcome
    import cloud_network_benchmark.lifecycle_adapters as adapters

    config = repository_root / "configs/tests/exp-900-single-provider.yaml"
    initialized = initialize_campaign(config, repository_root, tmp_path / "results",
        clock=lambda: NOW, token_source=lambda: "campaign")
    parent_path = Path(initialized.parent_manifest_path)
    parent, children = load_initialized_campaign(parent_path, repository_root=repository_root,
        config_path=config)
    child = children["aws"]
    runner = FakeAwsRunner()
    runner.run_id, runner.deployment_id = child.run_id, child.deployment_id
    cost_path = tmp_path / "refreshed-costs.json"
    campaign, _ = resolve_campaign(config, repository_root)
    _cost_source(cost_path, child.run_id, campaign.admission.cost_input_id)
    costs = load_cost_inputs(cost_path)
    runtime = _runtime(tmp_path, repository_root, cost_path, parent_path, costs.payload_sha256)
    clock = FakeClock()
    runtime["sleep"] = clock.sleep
    monkeypatch.setattr(adapters, "prepare_remote_pair", lambda *args, **kwargs:
        ProviderStageOutcome("prepare", True, {"bootstrap": object(), "guest_runtime": object()}))
    built = build_runtime(campaign=campaign, parent=parent, children={"aws": child},
        costs=costs, runtime=runtime, controller_session_id="fake-session", runner=runner,
        clock=clock, private_root=tmp_path / "private", repository_root=repository_root)
    result = run_lifecycle(ledger=built["ledger"], reservation_request=built["request"],
        admission_plan=built["plan"], allowance=built["allowance"],
        workflow_factory=built["workflow_factory"], clock=clock,
        recovery_path=built["recovery_path"], recovery_factory=built["recovery_factory"])
    provider_result = result.provider_results["aws"]
    assert provider_result.values["stabilize"].succeeded is True
    assert provider_result.values["stabilize"].evidence["elapsed_seconds"] >= 60
    assert clock.elapsed >= 60
    assert provider_result.values["cleanup"] is not None


def test_post_cleanup_batch_projects_all_selected_providers_before_sealing(tmp_path, repository_root):
    from cloud_network_benchmark.manifests import initialize_campaign
    from cloud_network_benchmark.contracts.admission import ReservationEvidence
    from cloud_network_benchmark.contracts.common import Evidence, StringEvidence
    from cloud_network_benchmark.orchestration import LifecycleResult, ProviderLifecycleResult
    from cloud_network_benchmark.lifecycle_projection import finalize_production_batch
    from cloud_network_benchmark.lifecycle_runtime import load_initialized_campaign
    from cloud_network_benchmark.contracts.validation import ValidatorInput
    from cloud_network_benchmark.validation import validate_bundle

    config = repository_root / "configs/tests/exp-903-three-provider-smoke.yaml"
    initialized = initialize_campaign(config, repository_root, tmp_path / "results",
        clock=lambda: NOW, token_source=lambda: "batch")
    parent_path = Path(initialized.parent_manifest_path)
    parent, children = load_initialized_campaign(parent_path, repository_root=repository_root,
        config_path=config)
    reservation = ReservationEvidence(schema_version=2, campaign_id=parent.campaign_id,
        reservation_id=Evidence[str](value="synthetic-batch-reservation"), state="reserved",
        reserved_cost_usd=Evidence[float](value=1), cap_started_at=Evidence[datetime](value=NOW),
        expires_at=Evidence[datetime](value=NOW + timedelta(hours=1)),
        cap_start_policy_id=StringEvidence(value="synthetic-policy"), source_path="synthetic-test.json",
        source_sha256="a" * 64)
    provider_results = {provider: ProviderLifecycleResult(provider=provider,
        execution_state="failed", primary_failure=f"synthetic {provider} stage failure")
        for provider in children}
    lifecycle = LifecycleResult(provider_results, completed=False,
        reservation_evidence=reservation, started_at=NOW, finished_at=NOW + timedelta(minutes=1))
    evaluated_at = NOW + timedelta(minutes=2)
    finalized = finalize_production_batch(parent_path=parent_path, children=children,
        parent=parent, lifecycle=lifecycle, repository_root=repository_root, evaluated_at=evaluated_at)
    assert set(finalized) == {"aws", "azure", "gcp"}
    saved_inputs = {}
    for provider, child in children.items():
        ref = next(row for row in parent.child_runs if str(row.provider) == provider)
        root = (repository_root / ref.result_path).resolve()
        saved = ValidatorInput.model_validate_json((root / "validation/input.json").read_text())
        saved_inputs[provider] = saved
        assert (root / "checksums.sha256").is_file()
    preliminary = {provider: validate_bundle(value, evaluated_at=evaluated_at)
        for provider, value in saved_inputs.items()}
    from cloud_network_benchmark.validation import evaluate_comparison
    compared = evaluate_comparison(list(saved_inputs.values()), list(preliminary.values()))
    for provider, value in saved_inputs.items():
        replay = compared[value.identity.run_id]
        assert replay.model_dump(mode="json") == finalized[provider][0].model_dump(mode="json")
    assert Path(initialized.parent_manifest_path).is_file()
