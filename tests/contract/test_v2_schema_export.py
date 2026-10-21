from pathlib import Path

from cloud_network_benchmark.contracts.schema import SCHEMAS, export, schema_bytes


def test_public_schema_set_matches_reviewed_contracts(repository_root: Path) -> None:
    target = repository_root / "specs/003-v2-shared-contracts/contracts"
    assert {path.name for path in target.glob("*.schema.json")} == set(SCHEMAS)
    assert export(target, check=True)
    for filename, model in SCHEMAS.items():
        assert (target / filename).read_bytes() == schema_bytes(model)
