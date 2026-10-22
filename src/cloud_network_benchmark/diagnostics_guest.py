"""Small, standalone Linux guest collector for F04.

This module intentionally has no package imports so F09 can copy it directly to a VM.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional


SAMPLE_FIELDS = {
    "cpu_total_percent", "cpu_max_core_percent", "cpu_steal_percent",
    "tcp_retransmissions", "interface_rx_drops", "interface_tx_drops",
    "interface_rx_errors", "interface_tx_errors", "rx_bytes", "tx_bytes",
}
COUNTERS = {
    "tcp_retransmissions": ("tcp", "RetransSegs"),
    "interface_rx_drops": ("nic", "rx_dropped"),
    "interface_tx_drops": ("nic", "tx_dropped"),
    "interface_rx_errors": ("nic", "rx_errors"),
    "interface_tx_errors": ("nic", "tx_errors"),
    "rx_bytes": ("nic", "rx_bytes"),
    "tx_bytes": ("nic", "tx_bytes"),
}


def _e(value=None, reason=None):
    return {"value": value, "unavailable_reason": reason} if value is None else {"value": value, "unavailable_reason": None}


def _finite_positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return float(value)


def _validate_interface(interface):
    if not isinstance(interface, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,32}", interface) or interface in (".", ".."):
        raise ValueError("interface must be a Linux interface basename")


def validate_context(context):
    required = {"deployment_id", "vm_role", "provider", "interface", "output_root", "duration_seconds", "metadata_seed"}
    if not isinstance(context, dict) or set(context) != required:
        raise ValueError("context must contain exactly the documented fields")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,191}", str(context["deployment_id"])):
        raise ValueError("invalid deployment_id")
    if context["vm_role"] not in ("vm_a", "vm_b") or context["provider"] not in ("aws", "azure", "gcp"):
        raise ValueError("invalid VM role or provider")
    interface = context["interface"]
    _validate_interface(interface)
    duration = _finite_positive(context["duration_seconds"], "duration_seconds")
    root = Path(context["output_root"])
    if not root.is_absolute() or root != Path(os.path.normpath(str(root))):
        raise ValueError("output_root must be a normalized absolute path")
    if not isinstance(context["metadata_seed"], dict):
        raise ValueError("metadata_seed must be an object")
    for part in (root, *root.parents):
        if part.is_symlink():
            raise ValueError("output_root must not contain symlink components")
    seed = context["metadata_seed"]
    if seed.get("schema_version") != 2 or seed.get("deployment_id") != context["deployment_id"] or seed.get("vm_role") != context["vm_role"] or seed.get("provider") != context["provider"]:
        raise ValueError("metadata_seed identity or schema does not match context")
    metadata_fields = {"schema_version", "deployment_id", "vm_role", "provider", "cpu_model", "nic_driver", "acceleration", "image", "kernel", "congestion_control", "tool_versions", "source_builds", "documented_network_limit", "mtu", "observed_region", "observed_zone", "physical_zone_id", "actual_shape"}
    if set(seed) != metadata_fields:
        raise ValueError("metadata_seed must match the V2 VM metadata fields")
    for name in ("cpu_model", "nic_driver", "acceleration", "image", "kernel", "congestion_control", "observed_region", "observed_zone", "physical_zone_id", "actual_shape"):
        _validate_evidence(seed[name], str, name)
    for name in ("tool_versions", "source_builds", "documented_network_limit"):
        _validate_evidence(seed[name], dict, name)
    mtu = seed["mtu"]
    _validate_evidence(mtu, int, "mtu")
    if mtu["value"] is not None and mtu["value"] <= 0:
        raise ValueError("metadata_seed MTU must be positive")
    return duration, root


def _validate_evidence(value, expected_type, field):
    if not isinstance(value, dict) or set(value) != {"value", "unavailable_reason"}:
        raise ValueError(f"metadata_seed {field} must be a V2 Evidence object")
    observed, reason = value["value"], value["unavailable_reason"]
    if (observed is None) == (reason is None) or (reason is not None and (not isinstance(reason, str) or not reason.strip())):
        raise ValueError(f"metadata_seed {field} must contain exactly a value or unavailable reason")
    if observed is not None:
        if expected_type is int and (isinstance(observed, bool) or not isinstance(observed, int)):
            raise ValueError(f"metadata_seed {field} value must be an integer")
        if expected_type is not int and not isinstance(observed, expected_type):
            raise ValueError(f"metadata_seed {field} has the wrong evidence type")
        if isinstance(observed, str) and not observed.strip() or isinstance(observed, dict) and not observed:
            raise ValueError(f"metadata_seed {field} observed value must not be empty")


def _read(path, root=Path("/")):
    path = Path(path)
    if path.is_absolute():
        path = Path(root) / str(path).lstrip("/")
    try:
        return _resolve_rooted(path, root).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"unavailable read {path}: {exc.__class__.__name__}: {exc}") from exc


def _ints(text, count=None):
    fields = text.split()
    if count is not None and len(fields) < count:
        raise ValueError("too few counter fields")
    if any(not re.fullmatch(r"[0-9]+", item) for item in fields):
        raise ValueError("counter fields must be nonnegative integers")
    return [int(item) for item in fields]


def parse_proc_stat(text):
    result = {}
    for line in text.splitlines():
        match = re.fullmatch(r"(cpu(?:[0-9]+)?)\s+(.+)", line)
        if match:
            name = match.group(1)
            try:
                result[name] = _ints(match.group(2), 8)[:8]
            except ValueError as exc:
                result[name] = {"error": str(exc)}
    return result


def parse_tcp_retrans(text):
    lines = text.splitlines()
    headers = [line.split()[1:] for line in lines if line.startswith("Tcp:")]
    values = [line.split()[1:] for line in lines if line.startswith("Tcp:")]
    if len(headers) != 2 or len(values) != 2 or len(headers[0]) != len(values[1]):
        raise ValueError("TCP header/value pairing is malformed")
    if headers[0].count("RetransSegs") != 1:
        raise ValueError("TCP RetransSegs field is missing or duplicated")
    idx = headers[0].index("RetransSegs")
    tokens = values[1]
    if idx >= len(tokens) or not re.fullmatch(r"[0-9]+", tokens[idx]):
        raise ValueError("TCP RetransSegs is not a nonnegative integer")
    return int(tokens[idx])


def read_linux_snapshot(interface, *, root=Path("/")):
    _validate_interface(interface)
    root = Path(root)
    try: cpu = parse_proc_stat(_read("/proc/stat", root))
    except ValueError: cpu = None
    tcp = None
    tcp_error = None
    try:
        tcp = parse_tcp_retrans(_read("/proc/net/snmp", root))
    except ValueError as exc:
        tcp_error = str(exc)
    nic = {}
    for key in ("rx_dropped", "tx_dropped", "rx_errors", "tx_errors", "rx_bytes", "tx_bytes"):
        try:
            raw = _read(f"/sys/class/net/{interface}/statistics/{key}", root).strip()
            if not re.fullmatch(r"[0-9]+", raw):
                raise ValueError("not a nonnegative integer")
            nic[key] = int(raw)
        except ValueError as exc:
            nic[key] = {"error": str(exc)}
    return {"cpu": cpu, "tcp": tcp, "tcp_error": tcp_error, "nic": nic}


def _evidence_delta(previous, current, field, source_key, reset_fields):
    if isinstance(previous, dict) and "error" in previous or isinstance(current, dict) and "error" in current or previous is None or current is None:
        return _e(None, "current or previous counter unavailable")
    delta = current - previous
    if delta < 0:
        reset_fields.append(field)
        return _e(None, "counter decreased; reset or wrap cause unknown")
    return _e(delta)


def _cpu_values(previous, current, reset_fields):
    unavailable = "CPU counters unavailable"
    if not previous or not current:
        return _e(None, unavailable), _e(None, unavailable), _e(None, unavailable)
    def calc(old, new, field):
        if not isinstance(old, (list, tuple)) or not isinstance(new, (list, tuple)) or len(old) < 8 or len(new) < 8:
            return None
        deltas = [b-a for a,b in zip(old, new)]
        if any(x < 0 for x in deltas):
            reset_fields.append(field)
            if field == "cpu_total_percent": reset_fields.append("cpu_steal_percent")
            return None
        total = sum(deltas)
        active = sum(deltas[i] for i in (0,1,2,5,6))
        steal = deltas[7]
        if total <= 0:
            return None
        return (100*active/total, 100*steal/total)
    prev_cpu, curr_cpu = previous.get("cpu"), current.get("cpu")
    aggregate = calc(prev_cpu.get("cpu") if isinstance(prev_cpu, dict) else prev_cpu,
                     curr_cpu.get("cpu") if isinstance(curr_cpu, dict) else curr_cpu,
                     "cpu_total_percent")
    if aggregate is None:
        total_e = _e(None, "CPU aggregate has reset or zero elapsed ticks")
        steal_e = _e(None, "CPU aggregate has reset or zero elapsed ticks")
    else:
        total_e, steal_e = _e(aggregate[0]), _e(aggregate[1])
    old_cpu, new_cpu = previous.get("cpu", {}), current.get("cpu", {})
    old_cpu = old_cpu if isinstance(old_cpu, dict) else {}
    new_cpu = new_cpu if isinstance(new_cpu, dict) else {}
    core_name = re.compile(r"cpu[0-9]+\Z")
    old_cores = {k:v for k,v in old_cpu.items() if core_name.fullmatch(k)}
    new_cores = {k:v for k,v in new_cpu.items() if core_name.fullmatch(k)}
    if (not old_cores or old_cores.keys() != new_cores.keys()
            or any(not isinstance(v, (list, tuple)) or len(v) < 8 for v in old_cores.values())
            or any(not isinstance(v, (list, tuple)) or len(v) < 8 for v in new_cores.values())):
        max_e = _e(None, "CPU core membership changed or unavailable")
    else:
        values = [calc(old_cores[k], new_cores[k], "cpu_max_core_percent") for k in sorted(old_cores)]
        if any(x is None for x in values):
            max_e = _e(None, "one or more CPU cores reset or have zero elapsed ticks")
        else:
            max_e = _e(max(x[0] for x in values))
    return total_e, max_e, steal_e


def derive_sample(previous, current, *, deployment_id, vm_role, sampled_at, monotonic_seconds, interval_seconds, gap_reason=None):
    if not math.isfinite(interval_seconds) or interval_seconds <= 0 or not math.isfinite(monotonic_seconds) or monotonic_seconds < 0:
        raise ValueError("sample interval and monotonic time must be finite and valid")
    resets = []
    cpu_total, cpu_max, cpu_steal = _cpu_values(previous, current, resets)
    values = {}
    for field, (kind, key) in COUNTERS.items():
        old = previous.get(kind) if previous else None
        new = current.get(kind) if current else None
        if kind == "tcp":
            old = old if not isinstance(old, dict) else None
            new = new if not isinstance(new, dict) else None
        elif old is not None:
            old = old.get(key)
        if kind == "nic" and new is not None:
            new = new.get(key)
        values[field] = _evidence_delta(old, new, field, key, resets)
    return {
        "schema_version": 2, "deployment_id": deployment_id, "vm_role": vm_role,
        "sampled_at": sampled_at.isoformat().replace("+00:00", "Z"), "monotonic_seconds": monotonic_seconds,
        "interval_seconds": interval_seconds, "cpu_total_percent": cpu_total,
        "cpu_max_core_percent": cpu_max, "cpu_steal_percent": cpu_steal,
        **values, "reset_fields": sorted(set(resets)), "gap_reason": gap_reason,
    }


@dataclass
class SystemClock:
    def monotonic(self): return time.monotonic()
    def utc_now(self): return datetime.now(timezone.utc)
    def wait_until(self, deadline):
        delay = deadline - time.monotonic()
        if delay > 0: time.sleep(delay)


def _command(argv, timeout):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False,
                                env={"PATH":os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL":"C"})
        return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"returncode": None, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}"}


def _clock_record(role, monotonic_seconds, utc_time, result, query_elapsed, utc_bracket):
    error = None
    source = "chronyc tracking unavailable; query failed"
    text = result.get("stdout", "")
    fields = {}
    for line in text.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    try:
        def seconds(name, pattern):
            match = re.fullmatch(pattern, fields[name])
            if not match: raise ValueError(name)
            v = float(match.group(1))
            if not math.isfinite(v): raise ValueError(name)
            return v
        number = r"([-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))"
        offset = seconds("System time", number + r" seconds (?:fast|slow) of NTP time")
        delay = seconds("Root delay", number + r" seconds")
        dispersion = seconds("Root dispersion", number + r" seconds")
        if not re.fullmatch(r"[0-9]+", fields["Stratum"]): raise ValueError("Stratum")
        stratum = int(fields["Stratum"])
        reference = fields["Reference ID"]
        leap = fields["Leap status"]
        ref_time = fields.get("Ref time (UTC)", "")
        datetime.strptime(ref_time, "%a %b %d %H:%M:%S %Y")
        if not re.fullmatch(r"[0-9A-Fa-f]{8}(?:\s+\([^)]{1,64}\))?", reference): raise ValueError("Reference ID")
        synchronized = result.get("returncode") == 0 and leap.lower() == "normal" and 1 <= stratum <= 15 and reference.split()[0].lower() not in ("7f7f0101", "00000000") and delay >= 0 and dispersion >= 0 and math.isfinite(query_elapsed) and query_elapsed >= 0 and math.isfinite(utc_bracket) and utc_bracket >= 0
        details = f"reference={reference}; ref_time={ref_time}; stratum={stratum}; leap={leap}; offset={fields.get('System time')}; root_delay={fields.get('Root delay')}; root_dispersion={fields.get('Root dispersion')}"
        source = f"chronyc tracking {'synchronized' if synchronized else 'unsynchronized'}; {details}"
        if synchronized:
            error = 1000 * (abs(offset) + dispersion + delay/2 + query_elapsed + utc_bracket/2)
    except (KeyError, ValueError, OverflowError):
        source = "chronyc tracking unsynchronized; invalid or incomplete tracking output"
    reason = None if error is not None else ("synchronization estimate unavailable" if result.get("returncode") == 0 else result.get("stderr") or "chronyc query unavailable")
    return {"vm_role": role, "monotonic_seconds": monotonic_seconds, "utc_timestamp": utc_time.isoformat().replace("+00:00", "Z"), "estimated_error_ms": _e(error, reason), "synchronization_source": _e(source)}


def _mapping(role, clock, run_command, deadline):
    m0 = clock.monotonic(); utc = clock.utc_now(); m1 = clock.monotonic()
    midpoint = (m0+m1)/2
    remaining = deadline - clock.monotonic()
    if remaining <= 0:
        result = {"returncode": None, "stdout": "", "stderr": "collection deadline reached"}
        elapsed = 0.0
    else:
        before = clock.monotonic()
        result = run_command(["chronyc", "-n", "tracking"], min(0.2, remaining))
        elapsed = max(0.0, clock.monotonic()-before)
    return _clock_record(role, midpoint, utc, result, elapsed, max(0.0,m1-m0))


def read_static_metadata(context, *, root=Path("/"), run_command=_command, remaining=lambda: 1.0):
    _validate_interface(context.get("interface") if isinstance(context, dict) else None)
    seed = dict(context["metadata_seed"])
    def unavailable(reason): return _e(None, reason)
    try:
        cpu = _read("/proc/cpuinfo", root)
        models = sorted({model.strip() for model in re.findall(r"^model name\s*:\s*(.+)$", cpu, re.M) if model.strip()})
        seed["cpu_model"] = _e("; ".join(models)) if models else unavailable("CPU model not observed")
    except ValueError as exc: seed["cpu_model"] = unavailable(str(exc))
    try:
        kernel = _read("/proc/sys/kernel/osrelease", root).strip()
        seed["kernel"] = _e(kernel) if kernel else unavailable("kernel release is empty")
    except ValueError as exc: seed["kernel"] = unavailable(str(exc))
    base = root / "sys/class/net" / context["interface"]
    try:
        mtu = int(_read(f"/sys/class/net/{context['interface']}/mtu", root).strip())
        seed["mtu"] = _e(mtu) if mtu > 0 else unavailable("MTU must be positive")
    except (OSError, ValueError): seed["mtu"] = unavailable("MTU unavailable or malformed")
    try:
        driver_link = base / "device/driver"
        seed["nic_driver"] = _e(_rooted_link_name(driver_link, root))
    except OSError: seed["nic_driver"] = unavailable("selected NIC driver unavailable")
    seed["acceleration"] = _acceleration(root, context["interface"], seed.get("provider"), seed.get("nic_driver"))
    try:
        congestion = _read("/proc/sys/net/ipv4/tcp_congestion_control", root).strip()
        seed["congestion_control"] = _e(congestion) if congestion else unavailable("congestion control is empty")
    except ValueError as exc: seed["congestion_control"] = unavailable(str(exc))
    versions, builds = {}, {}
    for tool in ("sockperf", "iperf3"):
        argv = [tool, "--version"] if tool == "sockperf" else [tool, "--version"]
        timeout = min(0.2, remaining())
        result = run_command(argv, timeout) if remaining() > 0 else {"returncode": None, "stdout": "", "stderr": "deadline reached"}
        binary_name = shutil_which(tool)
        version = (result.get("stdout") or result.get("stderr", "")).strip().splitlines()
        data = {"version": _e(version[0][:240]) if result.get("returncode") == 0 and version else unavailable("version unavailable"),
                "binary_sha256": unavailable("binary unavailable")}
        if binary_name:
            try:
                digest = _hash_binary(Path(binary_name), remaining)
                data["binary_sha256"] = _e(digest) if digest else unavailable("binary identity deadline reached or binary unavailable")
            except OSError: pass
        versions[tool] = data
        source_value = (seed.get("source_builds") or {}).get("value") or {}
        supplied = source_value.get(tool) if isinstance(source_value, dict) else None
        builds[tool] = {"supplied": supplied if isinstance(supplied, dict) else unavailable("build identity not supplied"),
                        "current_binary_sha256": data["binary_sha256"],
                        "matches_bootstrap_binary": _e(None, "bootstrap binary hash unavailable"),
                        "matches_bootstrap_version": _e(None, "bootstrap binary version unavailable")}
        if isinstance(supplied, dict) and data["binary_sha256"]["value"]:
            expected = supplied.get("binary_sha256", {}).get("value") if isinstance(supplied.get("binary_sha256"), dict) else None
            builds[tool]["matches_bootstrap_binary"] = _e(data["binary_sha256"]["value"] == expected) if expected else _e(None, "bootstrap binary hash unavailable")
        if isinstance(supplied, dict):
            expected_version = supplied.get("binary_version")
            actual_version = data["version"].get("value")
            builds[tool]["matches_bootstrap_version"] = _e(actual_version == expected_version) if actual_version and expected_version else _e(None, "bootstrap binary version unavailable")
    seed["tool_versions"] = _e(versions)
    seed["source_builds"] = _e(builds)
    return seed


def shutil_which(name):
    import shutil
    return shutil.which(name)


def _hash_binary(path, remaining, *, chunk_size=65536):
    """Hash incrementally and discard identity when the collection budget expires."""
    digest = hashlib.sha256()
    if remaining() <= 0:
        return None
    with Path(path).open("rb") as stream:
        while remaining() > 0:
            block = stream.read(min(chunk_size, 65536))
            if not block:
                return digest.hexdigest() if remaining() > 0 else None
            digest.update(block)
    return None


def _acceleration(root, interface, provider, driver):
    if not driver or driver.get("value") is None:
        return _e(None, "acceleration mechanism unavailable without observed driver")
    value = driver["value"]
    if value in ("ena", "gve"):
        return _e(f"observed driver mechanism: {value}; optional entitlement exclusions not verified")
    if value == "hv_netvsc":
        try: net = _resolve_rooted(root/"sys/class/net"/interface, root)
        except OSError: return _e(None, "Azure SR-IOV VF evidence unavailable")
        for lower in net.glob("lower_*"):
            try:
                rooted_lower = _resolve_rooted(lower, root)
                vf_driver = _rooted_link_name(rooted_lower/"device/driver", root)
                if vf_driver and vf_driver != "hv_netvsc":
                    return _e(f"observed linked VF driver: {vf_driver}")
            except OSError:
                continue
        return _e(None, "Azure SR-IOV VF evidence unavailable")
    return _e(f"observed driver {value}; acceleration semantics not verified")


def _resolve_rooted(path, root):
    """Resolve symlink components without following absolute fixture links on host."""
    root = Path(root).absolute()
    path = Path(path)
    try:
        relative = path.relative_to(root) if path.is_absolute() else path
    except ValueError as exc:
        raise OSError("sysfs path escapes fixture root") from exc
    pending, resolved, links = list(relative.parts), root, 0
    while pending:
        part = pending.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if resolved == root: raise OSError("sysfs link escapes fixture root")
            resolved = resolved.parent
            continue
        candidate = resolved / part
        if candidate.is_symlink():
            links += 1
            if links > 40: raise OSError("sysfs symlink loop")
            target = os.readlink(candidate)
            if target.startswith("/"):
                resolved = root
                pending = list(Path(target.lstrip("/")).parts) + pending
            else:
                pending = list(Path(target).parts) + pending
        else:
            resolved = candidate
    try: resolved.relative_to(root)
    except ValueError as exc: raise OSError("sysfs link escapes fixture root") from exc
    if not resolved.exists(): raise OSError("sysfs link target is unavailable")
    return resolved


def _rooted_link_name(link, root):
    return _resolve_rooted(link, root).name


def collect_local(context, *, root=Path("/"), clock=None, run_command=None, stop_requested=None, snapshot_reader=None):
    duration, output_root = validate_context(context)
    if not output_root.exists() or not output_root.is_dir():
        raise ValueError("output_root must be an existing directory")
    clock = clock or SystemClock()
    run_command = run_command or _command
    snapshot_reader = snapshot_reader or (lambda interface: read_linux_snapshot(interface, root=root))
    stop_requested = stop_requested or (lambda: False)
    deadline = clock.monotonic()+duration
    role, deployment = context["vm_role"], context["deployment_id"]
    names = {"metadata": f"metadata/{role}.json", "diagnostics": f"diagnostics/{role}.jsonl", "clock": f"diagnostics/{role}-clock.jsonl"}
    paths = {key: output_root/name for key,name in names.items()}
    opened = {}
    closed, failures, samples, mappings = [], [], 0, 0
    state = "completed"
    def take_snapshot():
        try: return snapshot_reader(context["interface"]), None
        except Exception as exc:
            empty = {"error": f"snapshot read failed: {type(exc).__name__}: {exc}"}
            return {"cpu":None,"tcp":None,"nic":{key:empty for _,key in COUNTERS.values() if key != "RetransSegs"}}, empty["error"]
    def timed_snapshot():
        before_mono, before_utc = clock.monotonic(), clock.utc_now()
        snapshot, error = take_snapshot()
        after_mono, after_utc = clock.monotonic(), clock.utc_now()
        # The end of acquisition is a paired, actually observed UTC/monotonic
        # instant. Averaging wall time across a clock step would invent a time.
        return snapshot, error, after_mono, after_utc, after_mono
    try:
        for path in paths.values():
            inside_root = False
            for parent in reversed(path.parents):
                if parent == output_root:
                    inside_root = True
                    continue
                if not inside_root: continue
                if parent.is_symlink(): raise ValueError(f"artifact directory contains a symlink: {parent}")
                if not parent.exists(): parent.mkdir()
                try: parent.resolve().relative_to(output_root.resolve())
                except ValueError as exc: raise ValueError("artifact path escapes output_root") from exc
            if path.exists() or path.is_symlink(): raise FileExistsError(str(path))
        for key,path in paths.items():
            fd = os.open(str(path), os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
            opened[key] = os.fdopen(fd, "w", encoding="utf-8")
        metadata = read_static_metadata(context, root=root, run_command=run_command,
                                        remaining=lambda: deadline-clock.monotonic())
        metadata["deployment_id"], metadata["vm_role"], metadata["provider"], metadata["schema_version"] = deployment, role, context["provider"], 2
        opened["metadata"].write(json.dumps(metadata, separators=(",",":"), allow_nan=False)+"\n"); opened["metadata"].flush()
        def write_mapping():
            nonlocal mappings
            mapping = _mapping(role, clock, run_command, deadline)
            opened["clock"].write(json.dumps(mapping, separators=(",",":"), allow_nan=False)+"\n")
            opened["clock"].flush(); mappings += 1
        if clock.monotonic() < deadline:
            previous, baseline_error, baseline, _, baseline_finished = timed_snapshot()
            write_mapping()
            if baseline_finished > deadline:
                state = "interrupted"
        else:
            # Startup consumed the supplied duration; preserve a UTC/monotonic
            # mapping with unavailable accuracy without starting another command.
            previous, baseline_error, baseline = None, None, deadline
            write_mapping()
        target = baseline+1.0
        while target <= deadline and not stop_requested():
            clock.wait_until(target)
            now = clock.monotonic()
            if now > deadline: break
            current, current_error, observed_at, observed_utc, read_finished = timed_snapshot()
            gap_parts = [item for item in (baseline_error, current_error) if item]
            if observed_at-target >= 1.0: gap_parts.append(f"scheduled tick delayed; {math.floor(observed_at-target)} tick opportunities omitted")
            crossed_deadline = read_finished > deadline
            if crossed_deadline:
                gap_parts.append("snapshot completed after collection deadline; partial observation")
            gap = "; ".join(gap_parts) or None
            sample = derive_sample(previous, current, deployment_id=deployment, vm_role=role,
                sampled_at=observed_utc, monotonic_seconds=observed_at, interval_seconds=observed_at-baseline, gap_reason=gap)
            opened["diagnostics"].write(json.dumps(sample, separators=(",",":"), allow_nan=False)+"\n"); opened["diagnostics"].flush(); samples += 1
            if crossed_deadline:
                state = "interrupted"
                break
            write_mapping()
            previous, baseline, baseline_error = current, observed_at, current_error
            target += max(1, math.floor((read_finished-target)/1.0)+1)
        if stop_requested(): state = "interrupted"
    except BaseException as exc:
        state = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        failures.append({"category": type(exc).__name__, "message": str(exc)[:500], "occurred_at": datetime.now(timezone.utc).isoformat()})
    finally:
        for key,stream in opened.items():
            try:
                stream.flush()
            except BaseException as exc:
                state = "failed"
                failures.append({"category": "flush_failure", "message": f"{key}: {type(exc).__name__}: {exc}"[:500], "occurred_at": datetime.now(timezone.utc).isoformat()})
            try:
                stream.close(); closed.append(str(paths[key]))
            except BaseException as exc:
                state = "failed"
                failures.append({"category": "close_failure", "message": f"{key}: {type(exc).__name__}: {exc}"[:500], "occurred_at": datetime.now(timezone.utc).isoformat()})
    return {"deployment_id": deployment, "vm_role": role, "state": state, "sample_count": samples,
            "mapping_count": mappings, "artifacts": {key:str(path) for key,path in paths.items() if path.exists()},
            "closed_paths": closed, "failures": failures}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True)
    args = parser.parse_args(argv)
    stop = {"value": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("value", True))
    try:
        context = json.loads(Path(args.context).read_text(encoding="utf-8"))
        outcome = collect_local(context, stop_requested=lambda: stop["value"])
    except Exception as exc:
        print(json.dumps({"state":"failed", "failures":[{"category":type(exc).__name__, "message":str(exc)[:500]}]}))
        return 1
    print(json.dumps(outcome, separators=(",",":"), allow_nan=False))
    return 0 if outcome["state"] == "completed" else 130 if outcome["state"] == "interrupted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
