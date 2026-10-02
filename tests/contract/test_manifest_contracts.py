import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from cloud_network_benchmark.manifests import (
    CampaignManifest, ChildRunManifest, StaticGitProvenance, initialize_campaign,
)
from cloud_network_benchmark.contracts import LifecycleEvent
import pytest
from datetime import datetime, timezone


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


@pytest.mark.parametrize(
    ("domain", "state"),
    [("execution", "pending"), ("execution", "provision-ready"), ("cleanup", "running"), ("campaign", "collecting")],
)
def test_lifecycle_event_rejects_aliases_and_domain_mismatches(domain: str, state: str) -> None:
    with pytest.raises(ValueError):
        LifecycleEvent(state_domain=domain, state=state, occurred_at=datetime(2026, 9, 27, tzinfo=timezone.utc))


def test_schema_valid_manifest_fixtures_cover_canonical_states_and_evidence(repository_root: Path) -> None:
    fixture_root = repository_root / "tests/fixtures/manifests"
    contract_root = repository_root / "specs/001-core-offline-contracts/contracts"
    cases = json.loads((fixture_root / "manifest-cases.json").read_text())
    parent = json.loads((fixture_root / "initialized-parent.json").read_text())
    child = json.loads((fixture_root / "initialized-child.json").read_text())
    parent_schema = json.loads((contract_root / "campaign-manifest.schema.json").read_text())
    child_schema = json.loads((contract_root / "child-run-manifest.schema.json").read_text())

    for state in cases["parent_states"]:
        candidate = dict(parent)
        candidate["aggregate_state"] = state
        candidate["lifecycle_events"] = [{"state_domain": "campaign", "state": state, "occurred_at": parent["created_at"], "detail": "fixture"}]
        Draft202012Validator(parent_schema, format_checker=FormatChecker()).validate(candidate)
        CampaignManifest.model_validate(candidate)

    for state in cases["child_execution_states"]:
        candidate = dict(child)
        candidate["execution_state"] = state
        candidate["failure"] = ({"category": "fixture", "message": "failed", "occurred_at": child["created_at"], "action_id": None, "details": {}} if state == "failed" else None)
        candidate["lifecycle_events"] = [{"state_domain": "execution", "state": state, "occurred_at": child["created_at"], "detail": "fixture"}]
        Draft202012Validator(child_schema, format_checker=FormatChecker()).validate(candidate)
        ChildRunManifest.model_validate(candidate)

    for state in cases["cleanup_states"]:
        candidate = dict(child)
        candidate["cleanup_state"] = state
        candidate["cleanup_failure"] = ({"category": "fixture", "message": "cleanup failed", "occurred_at": child["created_at"], "action_id": None, "details": {}} if state == "failed" else None)
        candidate["lifecycle_events"] = [{"state_domain": "execution", "state": "initialized", "occurred_at": child["created_at"], "detail": "fixture"}]
        if state != "not_started":
            candidate["lifecycle_events"].append({"state_domain": "cleanup", "state": state, "occurred_at": child["created_at"], "detail": "fixture"})
        Draft202012Validator(child_schema, format_checker=FormatChecker()).validate(candidate)
        ChildRunManifest.model_validate(candidate)

    for evidence in (cases["observed_metadata"], cases["unavailable_metadata"]):
        candidate = dict(child)
        candidate["vm_metadata"] = evidence
        Draft202012Validator(child_schema, format_checker=FormatChecker()).validate(candidate)
        ChildRunManifest.model_validate(candidate)

    unavailable_provenance = dict(parent)
    unavailable_provenance["config_provenance"] = dict(parent["config_provenance"])
    unavailable_provenance["config_provenance"]["design_git_commit"] = cases["unavailable_provenance"]
    Draft202012Validator(parent_schema, format_checker=FormatChecker()).validate(unavailable_provenance)
    CampaignManifest.model_validate(unavailable_provenance)
