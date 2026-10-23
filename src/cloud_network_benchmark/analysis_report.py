"""Deterministic, source-disjoint report serialization and atomic publication."""
from __future__ import annotations

import csv
import html
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .analysis import AnalysisReport
from .analysis_plots import render_figures


_TABLES = {
    "runs.csv": ("run_id", "deployment_id", "provider", "scenario", "mode", "context", "unit_id", "kind", "phase", "streams", "data_validity", "recorded_data_validity", "current_data_validity", "comparison_eligibility", "rtt", "receiver", "metadata", "source_refs"),
    "bins.csv": ("run_id", "deployment_id", "unit_id", "axis", "start_seconds", "end_seconds", "nominal_seconds", "rtt", "receiver_bytes", "source_refs"),
    "deployments.csv": ("provider", "scenario", "phase", "metric", "n", "points", "median", "q1", "q3", "iqr"),
    "comparisons.csv": ("comparison_id", "scenario", "slot_id", "window_label", "expected_providers", "present_providers", "missing_providers", "eligibility", "phase_ids", "run_ids", "reasons"),
    "diagnostics.csv": ("run_id", "vm_role", "sampled_at", "monotonic_seconds", "interval_seconds", "metrics", "reset_fields", "gap_reason", "clock_mappings_within_100ms", "source"),
    "reliability.csv": ("run_id", "provider", "scenario", "mode", "attempts", "recorded_data_validity", "current_data_validity", "failure_reason"),
    "costs.csv": ("run_id", "reservation", "actual", "estimated", "final", "runtime", "transfer"),
}


def _stable(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(value)


def _check_target(report: AnalysisReport, target: Path) -> Path:
    if target.exists() or target.is_symlink():
        raise ValueError(f"output already exists: {target}")
    parent = target.parent.resolve(strict=True)
    resolved_target = parent / target.name
    if target.name in {"", ".", ".."}:
        raise ValueError("output must name a new directory")
    protected = [Path(path).resolve(strict=True) for path in report.protected_paths]
    for source in protected:
        if resolved_target == source or resolved_target in source.parents or source in resolved_target.parents:
            raise ValueError(f"output overlaps protected source: {source}")
    # Refuse any existing symlink component in the requested target path.
    current = target.parent
    while current != current.parent:
        if current.is_symlink():
            raise ValueError(f"output path traverses a symlink: {current}")
        current = current.parent
    return resolved_target


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _write_csv(path: Path, headers: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in sorted(rows, key=lambda item: tuple(_stable(item.get(key)) for key in headers)):
            writer.writerow({key: _stable(row.get(key)) for key in headers})


def _html(payload: dict[str, Any]) -> str:
    title = "Native benchmark analysis"
    rows = payload.get("runs", [])
    figures = payload.get("figures", [])
    images = "\n".join(f'<figure><a href="{html.escape(item["svg"], quote=True)}"><img src="{html.escape(item["png"], quote=True)}" alt="{html.escape(item["caption"], quote=True)}"></a><figcaption>{html.escape(item["caption"])}</figcaption></figure>' for item in figures)
    body_rows = "\n".join("<tr>" + "".join(f"<td>{html.escape(_stable(row.get(key)))}</td>" for key in ("run_id", "provider", "scenario", "mode", "unit_id", "phase", "data_validity", "comparison_eligibility")) + "</tr>" for row in rows)
    source_rows = "\n".join(f'<tr><td>{html.escape(item.get("run_id", ""))}</td><td>{html.escape(item.get("artifact_id", item.get("kind", "")))}</td><td><code>{html.escape(item.get("path", ""))}</code></td><td>{html.escape(item.get("sha256", ""))}</td></tr>' for item in payload.get("sources", []))
    table_families = (("Bins", "bins", ("run_id", "unit_id", "start_seconds", "end_seconds", "nominal_seconds", "supported_seconds", "coverage", "rtt", "receiver_bytes")),
        ("Deployment distributions", "deployments", ("provider", "scenario", "phase", "window_label", "metric", "n", "points", "median", "q1", "q3", "iqr", "study_dates")),
        ("Matched provider comparisons", "comparisons", ("scenario", "slot_id", "window_label", "expected_providers", "present_providers", "missing_providers", "eligibility", "phase_ids", "run_ids", "reasons")),
        ("S1/S3 placement comparisons", "placement_comparisons", ("provider", "phase", "window_label", "metric", "direction", "baseline", "comparison", "rtt_difference_us", "throughput_change_percent", "study_dates", "same_utc_week", "unavailable_reason")),
        ("Diagnostics", "diagnostics", ("run_id", "vm_role", "sampled_at", "metrics", "reset_fields", "gap_reason", "clock_mappings_within_100ms", "source")),
        ("Reliability", "reliability", ("run_id", "provider", "scenario", "mode", "attempts", "attempt_status", "missing_phases", "replacement_of", "failure_reason")),
        ("Recorded costs", "costs", ("run_id", "provider", "reservation", "actual", "estimated", "final", "runtime", "transfer")))
    sections = []
    for label, key, columns in table_families:
        rows = payload.get(key, [])
        heading = html.escape(label)
        header = "".join(f"<th>{html.escape(column)}</th>" for column in columns)
        body = "".join("<tr>" + "".join(f"<td>{html.escape(_stable(row.get(column)))}</td>" for column in columns) + "</tr>" for row in rows)
        sections.append(f"<h2>{heading}</h2><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>")
    downloads = " ".join(f'<a href="{html.escape(name, quote=True)}">{html.escape(name)}</a>' for name in
        ("report.json", "runs.csv", "bins.csv", "deployments.csv", "comparisons.csv", "diagnostics.csv", "reliability.csv", "costs.csv"))
    identity = html.escape(_stable(payload.get("analysis_git", {})))
    return ("<!doctype html>\n<html lang=\"en\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{title}</title><style>body{{font:14px 'DejaVu Sans',sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem}}"
            "table{border-collapse:collapse;width:100%;margin:1rem 0}th,td{border:1px solid #bbb;padding:.35rem;text-align:left}"
            "img{max-width:100%;height:auto}figure{margin:1.5rem 0}code{overflow-wrap:anywhere}td{vertical-align:top;overflow-wrap:anywhere}</style><body>"
            f"<h1>{title}</h1><p>Selection fingerprint: <code>{html.escape(payload.get('selection_fingerprint', ''))}</code></p>"
            f"<p>Analysis Git identity: <code>{identity}</code></p><p>Download tables and data: {downloads}</p>"
            f"<p>Current dataset state is reported per run; partial and unavailable evidence remains visible. This report is descriptive and does not assert cloud behavior.</p>"
            f"<h2>Figures</h2>{images}<h2>Run and unit rows</h2><table><thead><tr><th>Run</th><th>Provider</th><th>Scenario</th><th>Mode</th><th>Unit</th><th>Phase</th><th>Data</th><th>Comparison</th></tr></thead><tbody>{body_rows}</tbody></table>"
            + "".join(sections)
            + f"<h2>Sources</h2><table><thead><tr><th>Run</th><th>Artifact</th><th>Local path</th><th>SHA-256</th></tr></thead><tbody>{source_rows}</tbody></table>"
            "<p>Tables and figures are local files; no network resources are loaded.</p></body></html>\n")


def write_analysis(report: AnalysisReport, output_root: Path) -> tuple[Path, ...]:
    target = _check_target(report, Path(output_root).absolute())
    payload = json.loads(json.dumps(report.payload))
    staging = Path(tempfile.mkdtemp(prefix=".cnb-analysis-", dir=target.parent))
    try:
        payload["figures"] = render_figures(payload, staging / "figures")
        _write_json(staging / "report.json", payload)
        for filename, headers in _TABLES.items():
            key = filename.removesuffix(".csv")
            rows = payload.get(key, [])
            if not isinstance(rows, list):
                raise ValueError(f"report table {key} must be a row list")
            _write_csv(staging / filename, headers, rows)
        (staging / "index.html").write_text(_html(payload), encoding="utf-8")
        os.replace(staging, target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    files = [target / name for name in (*_TABLES, "report.json", "index.html")]
    files.extend(sorted((target / "figures").glob("*")))
    return tuple(files)
