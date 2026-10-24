from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from cloud_network_benchmark.analysis_plots import _histogram_edges, render_figures


def test_histogram_has_50_equal_width_edges_and_constant_value_rule():
    edges = _histogram_edges([1, 2, 3])
    assert len(edges) == 51
    assert edges[0] == 1 and edges[-1] == 3
    assert len({round(edges[i + 1] - edges[i], 12) for i in range(50)}) == 1
    constant = _histogram_edges([7, 7])
    assert len(constant) == 51
    assert constant[0] < 7 < constant[-1]


def test_render_produces_deterministic_offline_figures(tmp_path):
    payload = {"selection_fingerprint": "f" * 64, "runs": [{"run_id": "run-1", "unit_id": "u1",
        "phase": "multi_flow", "mode": "short_study", "provider": "aws", "rtt": {"valid_replies": 2},
        "source_refs": ["sockperf.csv"]}], "bins": [{"run_id": "run-1", "unit_id": "u1",
        "start_seconds": 0, "end_seconds": 1, "rtt": {"valid_replies": 2,
        "median_us": {"value": 3.0}}, "source_refs": ["sockperf.csv"]}],
        "deployments": [{"provider": "aws", "scenario": "same_zone", "phase": "multi_flow",
        "metric": "throughput_gbps", "n": 1, "points": [2.0], "median": 2.0, "q1": 2.0,
        "q3": 2.0, "iqr": 0.0}], "comparisons": [], "sources": [], "diagnostics": [], "costs": [],
        "reliability": []}
    first, second = tmp_path / "one", tmp_path / "two"
    specs_a = render_figures(payload, first)
    specs_b = render_figures(payload, second)
    assert specs_a == specs_b
    assert {path.suffix for path in first.iterdir()} == {".png", ".svg"}
    for left in first.iterdir():
        assert left.read_bytes() == (second / left.name).read_bytes()
        if left.suffix == ".svg":
            svg = left.read_text()
            assert "Date:" not in svg
            assert not re.search(r"(?:href|src)=\"https?://|<script", svg)
            root = ET.fromstring(svg)
            assert root.attrib.get("width", "").endswith("pt")
            assert root.attrib.get("height", "").endswith("pt")
    assert any("no eligible" in item["caption"].lower() or item["row_ids"] for item in specs_a)
