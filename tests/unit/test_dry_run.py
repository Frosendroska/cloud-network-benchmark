from pathlib import Path
from time import perf_counter

import yaml

from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.dry_run import build_dry_run, render_human, render_json


def test_dry_run_is_complete_and_deterministic(repository_root: Path) -> None:
    resolved, _ = resolve_campaign(repository_root / "configs/experiments/exp-001-multi-provider.yaml", repository_root)
    report = build_dry_run(resolved)
    assert [child.provider for child in report.children] == ["aws", "azure", "gcp"]
    assert all(child.candidate_run_id.startswith("<campaign-id>") for child in report.children)
    assert render_json(report) == render_json(report)
    human = render_human(report)
    assert "VM A client" in human and "VM B server" in human
    assert "terraform" in human and "cleanup" in human
    assert "Config:" in human and "Manifest path:" in human
    assert "vm_a_to_vm_b" in human
    assert "Benchmark order: idle_latency, single_flow, multi_flow" in human
    assert "Validator expectations:" in human
    assert "provisioning_timeout_seconds" in human
    assert report.config_source == "configs/experiments/exp-001-multi-provider.yaml"
    assert report.children[0].provider_options["instance_market_type"] == "on_demand"


def test_dry_run_does_not_render_secret_bearing_fields(repository_root: Path) -> None:
    resolved, _ = resolve_campaign(repository_root / "configs/tests/exp-900-single-provider.yaml", repository_root)
    output = render_json(build_dry_run(resolved)).lower()
    for forbidden in ("authentication_reference", "private_key", "environment", "stdin", "credential"):
        assert forbidden not in output


def test_acceptance_preview_reports_resolved_stream_count(repository_root: Path) -> None:
    resolved, _ = resolve_campaign(repository_root / "configs/tests/exp-903-three-provider-smoke.yaml", repository_root)
    report = build_dry_run(resolved)
    assert resolved.benchmark.common_n.state == "pending"
    assert report.measurement_units[-1]["streams"] == 4
    assert "multi_flow, 10s, 4 streams" in render_human(report)


def test_every_provider_scenario_fixture_is_human_readable(repository_root: Path, tmp_path: Path) -> None:
    scenarios = {
        "same_zone": "configs/experiments/exp-001-multi-provider.yaml",
        "cross_zone": "configs/experiments/exp-002-cross-zone.yaml",
        "placement_optimization": "configs/experiments/exp-003-placement-optimization.yaml",
        "inter_region": "configs/experiments/exp-004-inter-region.yaml",
    }
    bases = {scenario: yaml.safe_load((repository_root / path).read_text())
             for scenario, path in scenarios.items()}
    matrix = yaml.safe_load((repository_root / "tests/fixtures/configs/scenario-matrix.yaml").read_text())
    assert {(case["provider"], case["scenario"]) for case in matrix} == {
        (provider, scenario) for provider in ("aws", "azure", "gcp") for scenario in scenarios
    }
    started = perf_counter()
    for index, case in enumerate(matrix):
        provider = case["provider"]
        data = dict(bases[case["scenario"]])
        root = tmp_path / f"case-{index}"
        path = root / "configs/tests/campaign.yaml"
        path.parent.mkdir(parents=True)
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        resolved, _ = resolve_campaign(path, root)
        report = build_dry_run(resolved)
        output = render_human(report)
        assert provider in output and case["scenario"] in output
        assert provider in {child.provider for child in report.children}
        assert len(report.children) == 3
        assert "VM A client" in output and "VM B server" in output
    assert perf_counter() - started < 300
