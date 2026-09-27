from pathlib import Path
from time import perf_counter

from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.manifests import StaticGitProvenance, initialize_campaign


def test_local_operations_meet_bounds(repository_root: Path, tmp_path: Path, frozen_clock: object, deterministic_token: object) -> None:
    config = repository_root / "configs/experiments/exp-001-multi-provider.yaml"
    started = perf_counter()
    resolve_campaign(config, repository_root)
    assert perf_counter() - started < 1.0
    started = perf_counter()
    initialize_campaign(config, repository_root, tmp_path / "results", frozen_clock, deterministic_token, StaticGitProvenance("a" * 40), StaticGitProvenance("b" * 40))
    assert perf_counter() - started < 2.0
