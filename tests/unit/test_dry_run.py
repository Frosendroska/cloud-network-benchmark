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


def test_dry_run_does_not_render_secret_bearing_fields(repository_root: Path) -> None:
    resolved, _ = resolve_campaign(repository_root / "configs/tests/exp-900-single-provider.yaml", repository_root)
    output = render_json(build_dry_run(resolved)).lower()
    for forbidden in ("authentication_reference", "private_key", "environment", "stdin", "credential"):
        assert forbidden not in output


def test_every_provider_scenario_fixture_is_human_readable(repository_root: Path, tmp_path: Path) -> None:
    base = yaml.safe_load((repository_root / "configs/experiments/exp-001-multi-provider.yaml").read_text())
    matrix = yaml.safe_load((repository_root / "tests/fixtures/configs/scenario-matrix.yaml").read_text())
    started = perf_counter()
    for index, case in enumerate(matrix):
        provider = case["provider"]
        data = dict(base)
        data["experiment_id"] = f"EXP-{1000 + index}"
        data["selected_providers"] = [provider]
        data["scenario"] = case["scenario"]
        provider_config = dict(base["provider_configs"][provider])
        provider_config["regions"] = dict(provider_config["regions"], vm_b=case["region_b"])
        provider_config["zones"] = dict(provider_config["zones"], vm_b=str(case["zone_b"]))
        provider_config["placement"] = {"kind": case["placement_kind"], "name": case["placement_name"]}
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
