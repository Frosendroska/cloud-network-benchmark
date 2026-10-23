"""Optional deterministic local Matplotlib rendering for F12 reports."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any


def _histogram_edges(values: list[float]) -> list[float]:
    if not values:
        return []
    low, high = min(values), max(values)
    if low == high:
        width = max(abs(low) * .02, 1.0)
        low, high = low - width / 2, high + width / 2
    return [low + (high - low) * index / 50 for index in range(51)]


def _fmt(value: Any) -> str:
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        return f"{value:g}"
    return str(value)


def _make_plots(payload: dict[str, Any], output_dir: Path) -> list[dict[str, Any]]:
    try:
        import matplotlib
        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError("analysis plots require the optional dependency; install cloud-network-benchmark[analysis]") from exc

    fingerprint = payload.get("selection_fingerprint", "f12")
    metadata = {"Date": None, "Creator": "cloud-network-benchmark"}
    specs: list[dict[str, Any]] = []
    output_dir.mkdir(parents=True, exist_ok=True)

    def save(name: str, title: str, caption: str, draw: Any, row_ids: list[str], sources: list[str]) -> None:
        with matplotlib.rc_context({"font.family": "DejaVu Sans", "svg.hashsalt": fingerprint,
                                    "axes.grid": True, "grid.alpha": .25, "figure.dpi": 100}):
            fig, axis = plt.subplots(figsize=(8, 4.5), constrained_layout=True)
            draw(axis)
            axis.set_title(title)
            axis.text(0, -0.2, caption, transform=axis.transAxes, fontsize=8, va="top", wrap=True)
            for extension in ("png", "svg"):
                fig.savefig(output_dir / f"{name}.{extension}", format=extension,
                            metadata=metadata if extension == "svg" else {"Software": "cloud-network-benchmark"})
            plt.close(fig)
        specs.append({"figure_id": name, "caption": caption, "row_ids": row_ids,
                      "sources": sorted(set(sources)), "png": f"figures/{name}.png",
                      "svg": f"figures/{name}.svg"})

    runs = payload.get("runs", [])
    bins = payload.get("bins", [])
    def timeline(axis):
        eligible = [row for row in bins if row.get("rtt", {}).get("valid_replies", 0)]
        if not eligible:
            axis.text(.5, .5, "No eligible RTT bins", ha="center", va="center", transform=axis.transAxes)
        else:
            for run_id in sorted({row["run_id"] for row in eligible}):
                points = sorted((row["start_seconds"], row["rtt"]["median_us"].get("value"))
                                for row in eligible if row["run_id"] == run_id
                                and row["rtt"]["median_us"].get("value") is not None)
                if points:
                    axis.plot([point[0] for point in points], [point[1] for point in points], marker=".", label=run_id)
            if eligible:
                axis.legend(loc="best")
        axis.set_xlabel("Source-supported elapsed seconds")
        axis.set_ylabel("RTT (µs)")
    save("time-series", "Supported RTT over time", "Raw per-bin medians; gaps are not interpolated.",
         timeline, [row.get("unit_id", "") for row in bins],
         [source for row in bins for source in row.get("source_refs", [])])

    def distribution(axis):
        plotted = False
        for row in runs:
            hist = row.get("rtt_histogram", {})
            edges, counts = hist.get("edges_us", []), hist.get("counts", [])
            if len(edges) == 51 and len(counts) == 50:
                centers = [(left + right) / 2 for left, right in zip(edges, edges[1:])]
                axis.step(centers, counts, where="mid", label=f"{row.get('run_id')} {row.get('unit_id')}")
                plotted = True
        if not plotted:
            axis.text(.5, .5, "No eligible raw RTT samples", ha="center", va="center", transform=axis.transAxes)
        elif len(runs) <= 6:
            axis.legend(loc="best", fontsize=7)
        axis.set_xlabel("Raw RTT (µs)")
        axis.set_ylabel("Sample count")
    save("rtt-distribution", "Raw RTT distributions", "50 equal-width bins per supported native unit; no smoothing or trimming.",
         distribution, [row.get("unit_id", "") for row in runs],
         [source for row in runs for source in row.get("source_refs", [])])

    def deployment(axis):
        points = payload.get("deployments", [])
        if not points:
            axis.text(.5, .5, "No eligible deployment points", ha="center", va="center", transform=axis.transAxes)
        else:
            labels, medians, lows, highs = [], [], [], []
            for row in points:
                label = f"{row['provider']}\n{row['scenario']}\n{row['phase']}"
                for value in row.get("points", []):
                    axis.scatter(label, value, marker="o", color="#4c78a8", alpha=.8)
                labels.append(label); medians.append(row["median"])
                lows.append(row["median"] - row["q1"]); highs.append(row["q3"] - row["median"])
            axis.errorbar(labels, medians, yerr=[lows, highs], fmt="_", capsize=5, color="#e45756", label="median and IQR")
            axis.tick_params(axis="x", labelrotation=0, labelsize=8)
        metrics = sorted({row.get("metric", "") for row in points}) if points else []
        units = {"throughput_gbps": "Throughput (Gb/s)", "median_rtt_us": "Median RTT (µs)"}
        metric = metrics[0] if metrics else ""
        axis.set_ylabel(units.get(metric, metric or "Metric value (see report)"))
    save("deployment-points", "Independent deployment points", "Every deployment point is shown; bars span Q1 to Q3.",
         deployment, sorted({run for row in payload.get("deployments", []) for run in row.get("run_ids", [])}),
         [source for row in payload.get("deployments", []) for source in row.get("source_refs", [])])

    placement_rows = [row for row in payload.get("placement_comparisons", []) if row.get("metric") == "median_rtt_us"]
    def placement(axis):
        rows = placement_rows
        values = [(row.get("provider", ""), row.get("rtt_difference_us", {}).get("value"))
                  for row in rows if row.get("rtt_difference_us", {}).get("value") is not None]
        if not values:
            axis.text(.5, .5, "No eligible S1/S3 comparison", ha="center", va="center", transform=axis.transAxes)
        else:
            axis.axhline(0, color="#777", linewidth=.8)
            axis.scatter([item[0] for item in values], [item[1] for item in values], color="#f58518")
        axis.set_ylabel("S3 − S1 median RTT (µs)")
        axis.set_xlabel("Provider")
    save("placement-comparison", "Within-provider placement effect", "S3 − S1 direction; only eligible, recorded groups are plotted.",
         placement, sorted({run for row in placement_rows for run in (*row.get("s1_run_ids", []), *row.get("s3_run_ids", []))}),
         [source for row in placement_rows for source in row.get("source_refs", [])])
    return specs


def render_figures(payload: dict[str, Any], output_dir: Path) -> list[dict[str, Any]]:
    """Render the report's fixed plot set with Agg and stable SVG identities."""
    return _make_plots(payload, Path(output_dir))
