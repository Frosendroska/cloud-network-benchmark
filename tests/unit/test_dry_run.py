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
    bases = {
        "aws": yaml.safe_load((repository_root / "configs/tests/exp-900-single-provider.yaml").read_text()),
        "azure": yaml.safe_load((repository_root / "configs/tests/exp-901-azure-single-provider.yaml").read_text()),
        "gcp": yaml.safe_load((repository_root / "configs/tests/exp-902-gcp-single-provider.yaml").read_text()),
    }
    matrix = yaml.safe_load((repository_root / "tests/fixtures/configs/scenario-matrix.yaml").read_text())
    started = perf_counter()
    for index, case in enumerate(matrix):
        provider = case["provider"]
        data = dict(bases[provider])
        data["provider_configs"] = {provider: dict(data["provider_configs"][provider])}
        data["experiment_id"] = f"EXP-{1000 + index}"
        data["context"] = "failure_check"
        data["selected_providers"] = [provider]
        data["scenario"] = case["scenario"]
        data["admission"]["priority"] = {"same_zone": "P0", "placement_optimization": "P1", "cross_zone": "P2", "inter_region": "P3"}[case["scenario"]]
        provider_config = dict(data["provider_configs"][provider])
        provider_config["regions"] = dict(provider_config["regions"], vm_b=case["region_b"])
        provider_config["zones"] = dict(provider_config["zones"], vm_b=str(case["zone_b"]))
        provider_config["placement"] = {"kind": case["placement_kind"], "name": case["placement_name"],
                                         "collocated": case.get("collocated", False)}
        regions = sorted({case["region_a"], case["region_b"]})
        network_map = {
            "aws": {"eu-central-1": "10.60.0.0/16", "eu-west-2": "10.61.0.0/16"},
            "azure": {"germanywestcentral": "10.70.0.0/16", "uksouth": "10.71.0.0/16"},
            "gcp": {"europe-west3": "10.80.1.0/24", "europe-west2": "10.81.1.0/24"},
        }[provider]
        provider_config["network"]["address_spaces"] = {region: network_map[region] for region in regions}
        provider_config["network"]["subnets"] = {
            region: ["10.60.1.0/24" if provider == "aws" and region == "eu-central-1" else
                     "10.61.1.0/24" if provider == "aws" else
                     "10.70.1.0/24" if provider == "azure" and region == "germanywestcentral" else
                     "10.71.1.0/24" if provider == "azure" else network_map[region]]
            for region in regions
        }
        data["provider_configs"] = {provider: provider_config}
        root = tmp_path / f"case-{index}"
        path = root / "configs/tests/campaign.yaml"
        path.parent.mkdir(parents=True)
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        resolved, _ = resolve_campaign(path, root)
        output = render_human(build_dry_run(resolved))
        assert provider in output and case["scenario"] in output
        assert "VM A client" in output and "VM B server" in output
    assert perf_counter() - started < 300
