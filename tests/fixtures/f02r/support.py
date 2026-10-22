from __future__ import annotations
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
from cloud_network_benchmark.access import ConnectionOutput, RemoteDeadline, TimeoutPolicy
from cloud_network_benchmark.bootstrap import BootstrapConfig, SourceBuild
from cloud_network_benchmark.contracts.common import IdentityContext
from cloud_network_benchmark.contracts.deployment import RemoteConnectionData
from cloud_network_benchmark.contracts.execution import CommandResult

def connection(tmp_path: Path) -> ConnectionOutput:
    key, known = tmp_path / "attempt-key", tmp_path / "known-hosts"
    key.write_text("synthetic-key-reference"); known.write_text("10.0.0.1 ssh-ed25519 AAAA\n10.0.0.2 ssh-ed25519 AAAA\n")
    identity = IdentityContext(experiment_id="EXP-001", config_id="exp_001", campaign_id="campaign", observation_id="campaign-aws-observation", run_id="campaign-aws", deployment_id="campaign-aws-deployment", provider="aws")
    timeouts = TimeoutPolicy(ssh_seconds=5, command_seconds=10, cloud_init_seconds=10, private_path_seconds=5, server_seconds=5, transfer_seconds=10)
    return ConnectionOutput(identity=identity, deployment_id=identity.deployment_id, run_id=identity.run_id, provider="aws", scenario="same_zone", vm_a=RemoteConnectionData(role="vm_a", host="10.0.0.1", user="ubuntu", authentication_reference=str(key), host_key_reference=str(known)), vm_b=RemoteConnectionData(role="vm_b", host="10.0.0.2", user="ubuntu", authentication_reference=str(key), host_key_reference=str(known)), private_ipv4_a="10.0.0.1", private_ipv4_b="10.0.0.2", timeouts=timeouts)

def deadline() -> RemoteDeadline:
    now = datetime.now(timezone.utc)
    return RemoteDeadline(readiness_deadline_monotonic=200, overall_deadline_monotonic=300, cleanup_reserve_seconds=10, cap_started_at=now, cap_expires_at=now+timedelta(minutes=5))

def bootstrap_config() -> BootstrapConfig:
    return BootstrapConfig(sources=[SourceBuild(tool="sockperf", source_url="https://example.invalid/sockperf.tar.gz", source_reference="sockperf-src", archive_name="sockperf.tar.gz", source_sha256="a"*64), SourceBuild(tool="iperf3", source_url="https://example.invalid/iperf3.tar.gz", source_reference="iperf3-src", archive_name="iperf3.tar.gz", source_sha256="b"*64)], remote_root="/var/lib/cnb", workspace="/var/lib/cnb/work", install_prefix="/var/lib/cnb/install")

def result(action_id: str, stdout: str="", success: bool=True, stderr: str="") -> CommandResult:
    now=datetime.now(timezone.utc)
    return CommandResult(action_id=action_id, outcome="succeeded" if success else "failed", started_at=now, finished_at=now, duration_seconds=0, exit_code=0 if success else 1, stdout=stdout, stderr=stderr)

def nft_listing(deployment_id: str, *, numeric_priority: bool = True) -> str:
    chain="cnb_"+"".join(ch if ch.isalnum() else "_" for ch in deployment_id)[:32]
    priority = "-10" if numeric_priority else "filter - 10"
    return (f"table inet cnb_f02r {{\n chain {chain} {{\n  type filter hook input priority {priority}; policy accept;\n"
        "  ip saddr 10.0.0.1 ip daddr 10.0.0.2 udp dport 11111 accept\n"
        "  ip daddr 10.0.0.2 udp dport 11111 drop\n"
        "  ip saddr 10.0.0.1 ip daddr 10.0.0.2 tcp dport 5201 accept\n"
        "  ip daddr 10.0.0.2 tcp dport 5201 drop\n }\n}\n")

class BootstrapRunner:
    def __init__(self, fail_action: str|None=None, *, existing_listeners: bool=False, existing_acl: bool=False, mismatched_listener: bool=False):
        self.requests=[]; self.fail_action=fail_action; self.existing_listeners=existing_listeners; self.existing_acl=existing_acl; self.created_acl=False; self.mismatched_listener=mismatched_listener
    def run(self, request):
        self.requests.append(request); aid=request.action_id
        if aid==self.fail_action: return result(aid, success=False, stderr="failed")
        if aid.endswith("cloud-init"): out="status: done"
        elif "listener-verify-" in aid:
            tool=aid.rsplit("-",1)[-1]
            port=11111 if tool=="sockperf" else 5201
            args=f"server -i 10.0.0.2 -p {port}" if tool=="sockperf" else f"-s -B 10.0.0.2 -p {port} --json-stream --forceflush"
            out=f"LoadState=loaded\nActiveState=active\nMainPID={20 if tool=='sockperf' else 21}\nExecStart={{ path=/var/lib/cnb/install/bin/{tool}; argv[]=/var/lib/cnb/install/bin/{tool} {args}; }}\nStandardOutput=append:/var/lib/cnb/work/{'server-logs/sockperf.log' if tool=='sockperf' else 'server-logs/iperf3-server.jsonl'}\nStandardError=append:/var/lib/cnb/work/{'server-logs/sockperf.err' if tool=='sockperf' else 'server-logs/iperf3-server.err'}\nRestart=no\n"
        elif "listener-config-" in aid:
            tool=aid.rsplit("-",1)[-1]
            out=f"# /run/systemd/transient/cnb-campaign-aws-deployment-{tool}.service\n[Service]\nStandardOutput=append:/var/lib/cnb/work/{'server-logs/sockperf.log' if tool=='sockperf' else 'server-logs/iperf3-server.jsonl'}\nStandardError=append:/var/lib/cnb/work/{'server-logs/sockperf.err' if tool=='sockperf' else 'server-logs/iperf3-server.err'}\n"
        elif "listener-process-" in aid:
            tool=aid.rsplit("-",1)[-1]; pid=20 if tool=="sockperf" else 21
            out=f"/var/lib/cnb/install/bin/{tool}\n"+"c"*64+f" /proc/{pid}/exe"
        elif "listener-inspect-" in aid:
            tool=aid.rsplit("-",1)[-1]
            if not self.existing_listeners: out="LoadState=not-found\n"
            else:
                port=11111 if tool=="sockperf" else (5202 if self.mismatched_listener else 5201)
                out=f"LoadState=loaded\nActiveState=active\nMainPID={20 if tool=='sockperf' else 21}\nExecStart={{ path=/var/lib/cnb/install/bin/{tool}; argv[]=/var/lib/cnb/install/bin/{tool} {'server -i 10.0.0.2 -p '+str(port) if tool=='sockperf' else '-s -B 10.0.0.2 -p '+str(port)+' --json-stream --forceflush'}; }}\nStandardOutput=append:/var/lib/cnb/work/{'server-logs/sockperf.log' if tool=='sockperf' else 'server-logs/iperf3-server.jsonl'}\nStandardError=append:/var/lib/cnb/work/{'server-logs/sockperf.err' if tool=='sockperf' else 'server-logs/iperf3-server.err'}\nRestart=no\n"
        elif "acl" in aid and "inspect" in aid:
            if self.existing_acl or self.created_acl: out=nft_listing("campaign-aws-deployment", numeric_priority=" -y " in request.argv[-1])
            else: return result(aid,success=False,stderr="No such file or directory")
        elif "acl-pre-create" in aid: self.created_acl=True; out=""
        elif aid.endswith("-2") and any(f"-{tool}-2" in aid for tool in ("sockperf","iperf3")): out=("a"*64 if "sockperf" in aid else "b"*64)+f" /var/lib/cnb/work/{'sockperf.tar.gz' if 'sockperf' in aid else 'iperf3.tar.gz'}"
        elif aid.endswith("-5") and any(f"-{tool}-5" in aid for tool in ("sockperf","iperf3")): out="gcc version synthetic 1.0"
        elif aid.endswith("-6") and any(f"-{tool}-6" in aid for tool in ("sockperf","iperf3")): out="sockperf version 3.10" if "sockperf" in aid else "iperf3 version 3.17"
        elif aid.endswith("-7") and any(f"-{tool}-7" in aid for tool in ("sockperf","iperf3")): out="c"*64+f" /var/lib/cnb/install/bin/{'sockperf' if 'sockperf' in aid else 'iperf3'}"
        elif aid.endswith("-8") and any(f"-{tool}-8" in aid for tool in ("sockperf","iperf3")): out=("Usage: sockperf [options]\nserver under-load" if "sockperf" in aid else "Usage: iperf3 [options]\n-s -c -B -p -P -t -O -i -l -C cubic -Z -J --get-server-output --json-stream --forceflush")
        elif aid.endswith("-9") and "sockperf" in aid: out="Usage: sockperf server -i -p"
        elif aid.endswith("-10") and "sockperf" in aid: out="Usage: sockperf under-load -i -p -m --mps --reply-every --full-rtt --no-rdtsc --full-log -t"
        elif "listener-" in aid: out="Active: active (running)"
        else: out="ok"
        if aid.endswith("listener-sockperf") or aid.endswith("listener-iperf3"):
            out=""
        if "listener-inspect-" in aid or "listener-verify-" in aid:
            import re
            out=re.sub(r"(StandardOutput|StandardError)=append:[^\n]+",r"\1=append",out)
            requested=request.argv[-1].split("--property=",1)[1].split(",")
            out="\n".join(line for line in out.splitlines() if line.split("=",1)[0] in requested)
        return result(aid,out)


def readiness_output(request, prepared):
    aid=request.action_id
    if "process-" in aid:
        tool="sockperf" if "sockperf" in aid else "iperf3"
        pid=20 if tool=="sockperf" else 21
        build=next(item for item in prepared.builds if item.role=="vm_b" and item.tool==tool)
        return f"{build.binary_path}\n{build.binary_sha256} /proc/{pid}/exe"
    if "unit-config-" in aid:
        tool="sockperf" if "sockperf" in aid else "iperf3"
        return f"[Service]\nStandardOutput=append:{next(item.stdout_path for item in prepared.listeners if item.tool==tool)}\nStandardError=append:{next(item.stderr_path for item in prepared.listeners if item.tool==tool)}"
    if "cloud-init" in aid: return "status: done"
    if "private-icmp" in aid: return "64 bytes from 10.0.0.2"
    if "private-tcp" in aid: return ""
    if "service-" in aid:
        tool="sockperf" if "sockperf" in aid else "iperf3"
        port=11111 if tool=="sockperf" else 5201
        args=f"server -i 10.0.0.2 -p {port}" if tool=="sockperf" else f"-s -B 10.0.0.2 -p {port} --json-stream --forceflush"
        output=f"LoadState=loaded\nActiveState=active\nMainPID={20 if tool=='sockperf' else 21}\nExecStart={{ path=/var/lib/cnb/install/bin/{tool}; argv[]=/var/lib/cnb/install/bin/{tool} {args}; }}\nStandardOutput=append\nStandardError=append\nRestart=no\n"
        requested=request.argv[-1].split("--property=",1)[1].split(",")
        return "\n".join(line for line in output.splitlines() if line.split("=",1)[0] in requested)
    if "socket-sockperf" in aid: return 'UNCONN 0 0 10.0.0.2:11111 0.0.0.0:* users:(("sockperf",pid=20,fd=3))'
    if "socket-iperf3" in aid: return 'LISTEN 0 4096 10.0.0.2:5201 0.0.0.0:* users:(("iperf3",pid=21,fd=3))'
    if "guest-acl" in aid: return nft_listing("campaign-aws-deployment", numeric_priority=" -y " in request.argv[-1])
    return "authenticated"
