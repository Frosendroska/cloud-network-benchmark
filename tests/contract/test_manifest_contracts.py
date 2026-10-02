import json
from copy import deepcopy
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from cloud_network_benchmark.manifests import (
    CampaignManifest, ChildRunManifest, StaticGitProvenance, initialize_campaign,
)
from cloud_network_benchmark.contracts import LifecycleEvent
import pytest
from datetime import datetime, timedelta, timezone


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


@pytest.mark.parametrize(
    ("field", "value"),
    [("sha256", "not-a-hash"), ("byte_length", 0)],
)
def test_runtime_and_schema_reject_invalid_manifest_provenance(
    repository_root: Path, field: str, value: object
) -> None:
    fixture = json.loads((repository_root / "tests/fixtures/manifests/initialized-child.json").read_text())
    fixture["config_provenance"][field] = value
    schema = json.loads(
        (repository_root / "specs/001-core-offline-contracts/contracts/child-run-manifest.schema.json").read_text()
    )
    with pytest.raises(ValueError):
        ChildRunManifest.model_validate(fixture)
    assert list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(fixture))


def test_runtime_and_schema_reject_untyped_resolved_observation(repository_root: Path) -> None:
    fixture = json.loads((repository_root / "tests/fixtures/manifests/initialized-child.json").read_text())
    fixture["resolved_observation"] = {}
    schema = json.loads(
        (repository_root / "specs/001-core-offline-contracts/contracts/child-run-manifest.schema.json").read_text()
    )
    with pytest.raises(ValueError):
        ChildRunManifest.model_validate(fixture)
    assert list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(fixture))


@pytest.mark.parametrize(
    ("execution_state", "failure", "cleanup_state", "cleanup_failure"),
    [
        ("failed", None, "not_started", None),
        ("succeeded", {"category": "x", "message": "x", "occurred_at": "2026-09-27T12:00:00Z", "action_id": None, "details": {}}, "not_started", None),
        ("initialized", None, "failed", None),
        ("initialized", None, "succeeded", {"category": "x", "message": "x", "occurred_at": "2026-09-27T12:00:00Z", "action_id": None, "details": {}}),
    ],
)
def test_runtime_and_schema_reject_inconsistent_failure_fields(
    repository_root: Path,
    execution_state: str,
    failure: object,
    cleanup_state: str,
    cleanup_failure: object,
) -> None:
    fixture = json.loads((repository_root / "tests/fixtures/manifests/initialized-child.json").read_text())
    fixture["execution_state"] = execution_state
    fixture["failure"] = failure
    fixture["cleanup_state"] = cleanup_state
    fixture["cleanup_failure"] = cleanup_failure
    fixture["lifecycle_events"] = [
        {"state_domain": "execution", "state": execution_state, "occurred_at": fixture["created_at"], "detail": None}
    ]
    if cleanup_state != "not_started":
        fixture["lifecycle_events"].append(
            {"state_domain": "cleanup", "state": cleanup_state, "occurred_at": fixture["created_at"], "detail": None}
        )
    schema = json.loads(
        (repository_root / "specs/001-core-offline-contracts/contracts/child-run-manifest.schema.json").read_text()
    )
    with pytest.raises(ValueError):
        ChildRunManifest.model_validate(fixture)
    assert list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(fixture))


def test_campaign_manifest_rejects_duplicate_or_unlinked_children(repository_root: Path) -> None:
    fixture = json.loads((repository_root / "tests/fixtures/manifests/initialized-parent.json").read_text())
    fixture["selected_providers"] = ["aws", "azure"]
    duplicate = deepcopy(fixture)
    duplicate["selected_providers"] = ["aws", "aws"]
    with pytest.raises(ValueError):
        CampaignManifest.model_validate(duplicate)
    unlinked = deepcopy(fixture)
    unlinked["child_runs"][0]["provider"] = "gcp"
    with pytest.raises(ValueError):
        CampaignManifest.model_validate(unlinked)


@pytest.mark.parametrize("child_mode", ["empty", "subset", "duplicate", "tampered"])
def test_succeeded_campaign_requires_complete_matching_child_references(
    repository_root: Path, child_mode: str
) -> None:
    fixture = json.loads((repository_root / "tests/fixtures/manifests/initialized-parent.json").read_text())
    fixture["selected_providers"] = ["aws", "azure"]
    fixture["aggregate_state"] = "succeeded"
    fixture["lifecycle_events"] = [
        {"state_domain": "campaign", "state": "succeeded", "occurred_at": fixture["created_at"], "detail": None}
    ]
    if child_mode == "empty":
        fixture["child_runs"] = []
    elif child_mode == "subset":
        fixture["child_runs"] = fixture["child_runs"][:1]
    elif child_mode == "duplicate":
        fixture["child_runs"].append(deepcopy(fixture["child_runs"][0]))
    else:
        fixture["child_runs"][0]["provider"] = "azure"
    schema = json.loads(
        (repository_root / "specs/001-core-offline-contracts/contracts/campaign-manifest.schema.json").read_text()
    )
    with pytest.raises(ValueError):
        CampaignManifest.model_validate(fixture)
    assert list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(fixture))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("experiment_id", "bad"),
        ("scheduled_start", "2026-09-27T12:00:00"),
        ("vm_metadata", {"value": {}, "unavailable_reason": None}),
    ],
)
def test_manifest_runtime_and_schema_reject_invalid_child_evidence_fields(
    repository_root: Path, field: str, value: object
) -> None:
    fixture = json.loads((repository_root / "tests/fixtures/manifests/initialized-child.json").read_text())
    fixture[field] = value
    schema = json.loads(
        (repository_root / "specs/001-core-offline-contracts/contracts/child-run-manifest.schema.json").read_text()
    )
    with pytest.raises(ValueError):
        ChildRunManifest.model_validate(fixture)
    assert list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(fixture))


@pytest.mark.parametrize("manifest_name", ["initialized-parent.json", "initialized-child.json"])
def test_manifest_runtime_and_schema_reject_consistently_malformed_experiment_id(
    repository_root: Path, manifest_name: str
) -> None:
    fixture = json.loads((repository_root / "tests/fixtures/manifests" / manifest_name).read_text())
    fixture["experiment_id"] = "bad"
    schema_name = "campaign-manifest.schema.json" if manifest_name == "initialized-parent.json" else "child-run-manifest.schema.json"
    if manifest_name == "initialized-child.json":
        fixture["resolved_observation"]["experiment_id"] = "bad"
    schema = json.loads(
        (repository_root / "specs/001-core-offline-contracts/contracts" / schema_name).read_text()
    )
    model = CampaignManifest if manifest_name == "initialized-parent.json" else ChildRunManifest
    with pytest.raises(ValueError):
        model.model_validate(fixture)
    assert list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(fixture))


@pytest.mark.parametrize(
    ("intent_field", "provider_field"),
    [
        ("region", "regions"),
        ("zone", "zones"),
        ("vm_shape", "vm_shape"),
        ("image", "image"),
        ("connection_user", "connection_user"),
    ],
)
def test_child_manifest_schema_and_runtime_reject_whitespace_vm_intent(
    repository_root: Path, intent_field: str, provider_field: str
) -> None:
    fixture = json.loads((repository_root / "tests/fixtures/manifests/initialized-child.json").read_text())
    observation = fixture["resolved_observation"]
    observation["vm_a"][intent_field] = " \t "
    if provider_field in {"regions", "zones"}:
        observation["provider_config"][provider_field]["vm_a"] = " \t "
    else:
        observation["provider_config"][provider_field] = " \t "
    schema = json.loads(
        (repository_root / "specs/001-core-offline-contracts/contracts/child-run-manifest.schema.json").read_text()
    )
    with pytest.raises(ValueError):
        ChildRunManifest.model_validate(fixture)
    assert list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(fixture))


@pytest.mark.parametrize("manifest_name", ["initialized-parent.json", "initialized-child.json"])
def test_manifest_schema_and_runtime_reject_empty_campaign_id(
    repository_root: Path, manifest_name: str
) -> None:
    fixture = json.loads((repository_root / "tests/fixtures/manifests" / manifest_name).read_text())
    fixture["campaign_id"] = ""
    schema_name = "campaign-manifest.schema.json" if manifest_name == "initialized-parent.json" else "child-run-manifest.schema.json"
    if manifest_name == "initialized-parent.json":
        fixture["child_runs"][0].update(
            run_id="-aws",
            manifest_path="results/manifests/-aws.json",
            result_path="results/raw/-aws",
        )
    else:
        fixture["run_id"] = "-aws"
    schema = json.loads(
        (repository_root / "specs/001-core-offline-contracts/contracts" / schema_name).read_text()
    )
    model = CampaignManifest if manifest_name == "initialized-parent.json" else ChildRunManifest
    with pytest.raises(ValueError):
        model.model_validate(fixture)
    assert list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(fixture))


def test_empty_string_provenance_evidence_is_rejected(repository_root: Path) -> None:
    fixture = json.loads((repository_root / "tests/fixtures/manifests/initialized-child.json").read_text())
    fixture["config_provenance"]["implementation_git_commit"] = {
        "value": "",
        "unavailable_reason": None,
    }
    schema = json.loads(
        (repository_root / "specs/001-core-offline-contracts/contracts/child-run-manifest.schema.json").read_text()
    )
    with pytest.raises(ValueError):
        ChildRunManifest.model_validate(fixture)
    assert list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(fixture))


@pytest.mark.parametrize("field", ["manifest_path", "result_path"])
def test_child_reference_rejects_directory_traversal(repository_root: Path, field: str) -> None:
    fixture = json.loads((repository_root / "tests/fixtures/manifests/initialized-parent.json").read_text())
    fixture["child_runs"][0][field] = "results/raw/../outside"
    with pytest.raises(ValueError):
        CampaignManifest.model_validate(fixture)


def test_child_reference_filename_shape_is_rejected_by_schema_and_runtime(repository_root: Path) -> None:
    fixture = json.loads((repository_root / "tests/fixtures/manifests/initialized-parent.json").read_text())
    fixture["child_runs"][0]["manifest_path"] = "results/manifests/run-output.txt"
    with pytest.raises(ValueError):
        CampaignManifest.model_validate(fixture)
    schema = json.loads(
        (repository_root / "specs/001-core-offline-contracts/contracts/campaign-manifest.schema.json").read_text()
    )
    assert list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(fixture))


def test_child_reference_cross_field_filename_link_is_runtime_enforced(repository_root: Path) -> None:
    fixture = json.loads((repository_root / "tests/fixtures/manifests/initialized-parent.json").read_text())
    fixture["child_runs"][0]["manifest_path"] = "results/manifests/different-aws.json"
    with pytest.raises(ValueError):
        CampaignManifest.model_validate(fixture)
    schema = json.loads(
        (repository_root / "specs/001-core-offline-contracts/contracts/campaign-manifest.schema.json").read_text()
    )
    assert not list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(fixture))


@pytest.mark.parametrize("manifest_name", ["initialized-parent.json", "initialized-child.json"])
def test_manifests_reject_reversed_timestamps(repository_root: Path, manifest_name: str) -> None:
    fixture = json.loads((repository_root / "tests/fixtures/manifests" / manifest_name).read_text())
    created = datetime.fromisoformat(fixture["created_at"].replace("Z", "+00:00"))
    fixture["updated_at"] = (created - timedelta(seconds=1)).isoformat()
    model = CampaignManifest if fixture["manifest_type"] == "campaign" else ChildRunManifest
    with pytest.raises(ValueError, match="chronological|updated_at"):
        model.model_validate(fixture)


def test_child_manifest_rejects_out_of_order_events_and_actual_times(repository_root: Path) -> None:
    fixture = json.loads((repository_root / "tests/fixtures/manifests/initialized-child.json").read_text())
    fixture["execution_state"] = "running"
    fixture["actual_started_at"] = "2026-09-27T12:00:02Z"
    fixture["updated_at"] = "2026-09-27T12:00:03Z"
    fixture["lifecycle_events"] = [
        {"state_domain": "execution", "state": "initialized", "occurred_at": "2026-09-27T12:00:01Z", "detail": None},
        {"state_domain": "execution", "state": "running", "occurred_at": "2026-09-27T12:00:00Z", "detail": None},
    ]
    with pytest.raises(ValueError, match="chronological"):
        ChildRunManifest.model_validate(fixture)

    fixture["lifecycle_events"].reverse()
    fixture["actual_finished_at"] = "2026-09-27T12:00:01Z"
    with pytest.raises(ValueError, match="actual_finished_at"):
        ChildRunManifest.model_validate(fixture)
