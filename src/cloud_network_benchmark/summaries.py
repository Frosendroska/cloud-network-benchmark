"""Deterministic concise text summaries for native validation results."""
from __future__ import annotations

from typing import Any

from .contracts.validation import ValidationResult


def _value(value: Any) -> str:
    if isinstance(value, dict):
        if value.get("value") is not None:
            return str(value["value"])
        return "unavailable: " + str(value.get("unavailable_reason") or "not supplied")
    return "unavailable" if value is None else str(value)


def summarize_validation(value: ValidationResult) -> str:
    metrics = value.metrics.value or {}
    identity = metrics.get("identity", {})
    execution = metrics.get("execution", {})
    lines = [f"Run {value.run_id} ({identity.get('provider', 'provider unknown')}, {value.mode}; {identity.get('scenario', 'scenario unknown')}; context {identity.get('context', 'unknown')})",
             "Identity: " + ", ".join(f"{key}={identity.get(key, 'unavailable')}" for key in
                 ("experiment_id", "campaign_id", "observation_id", "deployment_id", "config_id"))
                 + f", attempt_id={identity.get('run_id', 'unavailable')}",
             f"Execution: {execution.get('execution_state', 'unknown')}; cleanup: {execution.get('cleanup_state', 'unknown')}; bundle complete: {execution.get('bundle_complete', False)}",
             f"Data: {value.validation_status.data_validity} ({'; '.join(value.validation_status.data_reasons) or 'all required checks passed'})",
             f"Comparison: {value.validation_status.comparison_eligibility} ({'; '.join(value.validation_status.comparison_reasons)})"]
    provenance = metrics.get("provenance", {})
    lines.append(f"Config SHA-256: {provenance.get('config_sha256', 'unavailable')}")
    lines.append(f"Resolved SHA-256: {_value(provenance.get('resolved_sha256'))}")
    lines.append(f"Schedule SHA-256: {_value(provenance.get('schedule_sha256'))}")
    for label, key in (("Implementation Git", "implementation_git"), ("Design Git", "design_git"), ("Tool versions", "tool_versions")):
        lines.append(f"{label}: {_value(provenance.get(key))}")
    for label, key in (("Primary failure", "primary_failure"), ("Retrieval failure", "retrieval_failure"), ("Cleanup failure", "cleanup_failure")):
        failure = execution.get(key)
        if failure:
            lines.append(f"{label}: {failure.get('message') or failure.get('reason') or failure.get('code') or 'recorded'}")
    for command in metrics.get("commands", []):
        request = command.get("request", {})
        argv = request.get("argv") if isinstance(request, dict) else None
        if isinstance(argv, list):
            lines.append(f"Command {command.get('unit_id')} ({command.get('producer')}): {' '.join(map(str, argv))}")
    for unit in metrics.get("units", []):
        lines.append(f"Unit {unit.get('unit_id')} [{unit.get('phase')}; {unit.get('data_validity', 'unavailable')}]: RTT median {_value(unit.get('rtt', {}).get('median_us'))} us; P95 {_value(unit.get('rtt', {}).get('p95_us'))} us; receiver TCP {_value(unit.get('tcp', {}).get('throughput_gbps'))} Gbit/s")
        for count in unit.get("probe_counts", []):
            lines.append("  Sockperf counts: " + ", ".join(f"{name}={_value(count.get(name))}" for name in ("sent", "received", "dropped", "duplicated", "out_of_order", "skipped")))
        coverage = unit.get("coverage", {})
        lines.append(f"  Coverage: {_value(coverage.get('active_probe_ratio'))}; active={_value(coverage.get('active_probe_seconds'))} s; native sample support={_value(coverage.get('native_sample_support_seconds'))} s; gaps={len(coverage.get('gaps', []))}; receiver intervals={unit.get('receiver_interval_count', 0)}; RTT samples={unit.get('sample_count', 0)}")
        for source in unit.get("sources", []):
            lines.append(f"  Source {source.get('artifact_id')} ({source.get('path')}; {source.get('producer', 'producer unavailable')}): SHA-256 {source.get('sha256', 'unavailable')}")
        for source in (unit.get("native_sample_source"),):
            if source:
                lines.append(f"  Native sample identity: {source.get('artifact_id')} version={source.get('binary_version', 'unavailable')} build={source.get('source_identity', 'unavailable')}")
        unit_findings = unit.get("findings", [])
        if unit_findings:
            lines.append("  Unit findings: " + "; ".join(f"{item.get('code')}: {item.get('message')}" for item in unit_findings))
    diagnostics = metrics.get("diagnostics", [])
    if diagnostics:
        by_role: dict[str, list[dict[str, Any]]] = {}
        for sample in diagnostics:
            by_role.setdefault(str(sample.get("vm_role", "unknown")), []).append(sample)
        lines.append("Diagnostics:")
        for role in sorted(by_role):
            samples = by_role[role]
            resets = sum(len(item.get("reset_fields", [])) for item in samples)
            gaps = sum(item.get("gap_reason") is not None for item in samples)
            cpu = [item.get("cpu_total_percent", {}).get("value") for item in samples
                   if item.get("cpu_total_percent", {}).get("value") is not None]
            cpu_range = f"; CPU total range {min(cpu):g}–{max(cpu):g}%" if cpu else ""
            lines.append(f"  {role}: {len(samples)} samples; counter resets={resets}; gaps={gaps}{cpu_range}")
    if value.findings:
        lines.append("Findings:")
        lines.extend(f"  {item.severity} {item.code}: {item.message}" for item in value.findings)
    return "\n".join(lines) + "\n"
