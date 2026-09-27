import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from cloud_network_benchmark.manifests import StaticGitProvenance, initialize_campaign


def test_generated_manifests_match_schemas(repository_root: Path, tmp_path: Path, frozen_clock: object, deterministic_token: object, fake_git_commits: dict[str, str]) -> None:
    result = initialize_campaign(
        repository_root / "configs/experiments/exp-001-multi-provider.yaml",
        repository_root,
        tmp_path / "results",
        frozen_clock,
        deterministic_token,
        StaticGitProvenance(fake_git_commits["implementation"]),
        StaticGitProvenance(fake_git_commits["design"]),
    )
    schema_root = repository_root / "specs/001-core-offline-contracts/contracts"
    for path in (tmp_path / "results/manifests").glob("*.json"):
        data = json.loads(path.read_text())
        schema_name = "child-run-manifest.schema.json" if data["manifest_type"] == "child_run" else "campaign-manifest.schema.json"
        schema = json.loads((schema_root / schema_name).read_text())
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(data)
        if data["manifest_type"] == "child_run":
            assert data["vm_metadata"]["value"] is None
            assert data["provider_metadata"]["unavailable_reason"]
            assert data["tool_versions"]["value"]["cloud_network_benchmark"] == "0.1.0"
            assert data["config_provenance"]["implementation_git_commit"]["value"] == fake_git_commits["implementation"]


def test_structured_evidence_schema_accepts_observed_and_unavailable(repository_root: Path) -> None:
    schema = json.loads((repository_root / "specs/001-core-offline-contracts/contracts/child-run-manifest.schema.json").read_text())
    definition = schema["$defs"]["structured_evidence"]
    validator = Draft202012Validator(definition)
    validator.validate({"value": {"vm_a": {"mtu": 1500}}, "unavailable_reason": None})
    validator.validate({"value": None, "unavailable_reason": "not deployed"})
