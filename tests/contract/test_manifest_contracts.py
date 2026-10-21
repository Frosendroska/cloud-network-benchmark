import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from cloud_network_benchmark.contracts import FailureEvidence, LifecycleEvent, ValidationStatus
from cloud_network_benchmark.manifests import CampaignManifest, ChildRunManifest


def fixture(root: Path, name: str) -> dict:
    return json.loads((root / "tests/fixtures/manifests" / name).read_text())


def test_generated_v2_manifest_fixtures_match_runtime_schemas(repository_root: Path) -> None:
    schema_root = repository_root / "specs/003-v2-shared-contracts/contracts"
    for name in ("initialized-parent.json", "initialized-child.json"):
        data = fixture(repository_root, name)
        model = CampaignManifest if name.endswith("parent.json") else ChildRunManifest
        schema_name = "campaign-manifest.schema.json" if name.endswith("parent.json") else "child-run-manifest.schema.json"
        schema = json.loads((schema_root / schema_name).read_text())
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(data)
        model.model_validate(data)


def test_manifest_rejects_unknown_fields_and_invalid_identifiers(repository_root: Path) -> None:
    parent = fixture(repository_root, "initialized-parent.json")
    child = fixture(repository_root, "initialized-child.json")
    with pytest.raises(ValueError):
        CampaignManifest.model_validate({**parent, "mystery": "x"})
    bad_child = {**child, "config_id": "BAD ID"}
    with pytest.raises(ValueError):
        ChildRunManifest.model_validate(bad_child)


def test_v2_manifest_failure_channels_remain_independent(repository_root: Path) -> None:
    child = fixture(repository_root, "initialized-child.json")
    child["failure"] = {"category": "benchmark", "message": "primary", "occurred_at": child["created_at"], "details": {}}
    child["execution_state"] = "failed"
    child["lifecycle_events"].append({"state_domain": "execution", "state": "failed", "occurred_at": child["created_at"], "detail": "primary"})
    child["retrieval_failure"] = {"category": "retrieval", "message": "partial copy", "occurred_at": child["created_at"], "details": {}}
    child["cleanup_failure"] = {"category": "cleanup", "message": "cleanup failed", "occurred_at": child["created_at"], "details": {}}
    child["cleanup_state"] = "failed"
    child["lifecycle_events"].append({"state_domain": "cleanup", "state": "failed", "occurred_at": child["created_at"], "detail": "cleanup"})
    validated = ChildRunManifest.model_validate(child)
    assert validated.failure.message == "primary"
    assert validated.retrieval_failure.message == "partial copy"
    assert validated.cleanup_failure.message == "cleanup failed"
    assert ValidationStatus().data_validity == "not_validated"


def test_campaign_child_identity_is_runtime_checked(repository_root: Path) -> None:
    parent = fixture(repository_root, "initialized-parent.json")
    reference = parent["child_runs"][0]
    child = fixture(repository_root, "initialized-child.json")
    child["mode"] = "calibration"
    with pytest.raises(ValueError):
        ChildRunManifest.model_validate(child)
    parent["aggregate_state"] = "succeeded"
    parent["child_runs"] = []
    parent["lifecycle_events"][-1]["state"] = "succeeded"
    with pytest.raises(ValueError):
        CampaignManifest.model_validate(parent)


@pytest.mark.parametrize("field", ["manifest_path", "result_path"])
def test_child_reference_rejects_traversal(repository_root: Path, field: str) -> None:
    parent = fixture(repository_root, "initialized-parent.json")
    parent["child_runs"][0][field] = "results/raw/../outside"
    with pytest.raises(ValueError):
        CampaignManifest.model_validate(parent)


def test_manifest_chronology_is_preserved(repository_root: Path) -> None:
    child = fixture(repository_root, "initialized-child.json")
    child["updated_at"] = "2026-09-27T11:59:59Z"
    with pytest.raises(ValueError, match="updated_at"):
        ChildRunManifest.model_validate(child)
    with pytest.raises(ValueError):
        LifecycleEvent(state_domain="execution", state="pending", occurred_at=datetime.now(timezone.utc))


@pytest.mark.parametrize("state", ["failed", "interrupted"])
def test_terminal_failure_state_requires_primary_failure_evidence(repository_root: Path, state: str) -> None:
    child = fixture(repository_root, "initialized-child.json")
    child["execution_state"] = state
    child["lifecycle_events"].append({"state_domain": "execution", "state": state, "occurred_at": child["created_at"], "detail": state})
    with pytest.raises(ValueError, match="requires failure evidence"):
        ChildRunManifest.model_validate(child)


@pytest.mark.parametrize("state", ["initialized", "running", "succeeded"])
def test_primary_failure_is_only_retained_for_failed_or_interrupted_execution(repository_root: Path, state: str) -> None:
    child = fixture(repository_root, "initialized-child.json")
    child["execution_state"] = state
    child["lifecycle_events"][-1]["state"] = state
    child["failure"] = {"category": "primary", "message": "preserved", "occurred_at": child["created_at"], "details": {}}
    with pytest.raises(ValueError, match="only valid"):
        ChildRunManifest.model_validate(child)


def test_failed_cleanup_requires_secondary_evidence(repository_root: Path) -> None:
    child = fixture(repository_root, "initialized-child.json")
    child["cleanup_state"] = "failed"
    child["lifecycle_events"].append({"state_domain": "cleanup", "state": "failed", "occurred_at": child["created_at"], "detail": "failed"})
    with pytest.raises(ValueError, match="cleanup failure"):
        ChildRunManifest.model_validate(child)


def test_cleanup_failure_evidence_is_not_attached_to_success(repository_root: Path) -> None:
    child = fixture(repository_root, "initialized-child.json")
    child["cleanup_state"] = "succeeded"
    child["lifecycle_events"].append({"state_domain": "cleanup", "state": "succeeded", "occurred_at": child["created_at"], "detail": "succeeded"})
    child["cleanup_failure"] = {"category": "cleanup", "message": "failed", "occurred_at": child["created_at"], "details": {}}
    with pytest.raises(ValueError, match="requires failed cleanup state"):
        ChildRunManifest.model_validate(child)


def test_failed_data_status_cannot_be_comparison_eligible() -> None:
    from cloud_network_benchmark.contracts import ValidationStatus
    with pytest.raises(ValueError, match="requires valid data"):
        ValidationStatus(data_validity="partial", data_reasons=["gap"], comparison_eligibility="eligible")


def test_campaign_rejects_duplicate_and_unselected_children(repository_root: Path) -> None:
    parent = fixture(repository_root, "initialized-parent.json")
    child = dict(parent["child_runs"][0])
    child.update(provider="gcp", run_id=parent["campaign_id"] + "-gcp",
                 manifest_path="results/manifests/" + parent["campaign_id"] + "-gcp.json",
                 result_path="results/raw/" + parent["campaign_id"] + "-gcp")
    parent["selected_providers"] = ["aws"]
    parent["child_runs"] = [child]
    with pytest.raises(ValueError, match="selected"):
        CampaignManifest.model_validate(parent)
    parent = fixture(repository_root, "initialized-parent.json")
    child.update(provider="aws", run_id=parent["campaign_id"] + "-aws",
                 manifest_path="results/manifests/" + parent["campaign_id"] + "-aws.json",
                 result_path="results/raw/" + parent["campaign_id"] + "-aws")
    parent["child_runs"] = [parent["child_runs"][0], child]
    with pytest.raises(ValueError, match="unique"):
        CampaignManifest.model_validate(parent)


@pytest.mark.parametrize(("field", "value"), [("sha256", "not-hash"), ("byte_length", 0)])
def test_child_config_provenance_rejects_bad_hash_and_length(repository_root: Path, field: str, value: object) -> None:
    child = fixture(repository_root, "initialized-child.json")
    child["config_provenance"][field] = value
    with pytest.raises(ValueError):
        ChildRunManifest.model_validate(child)


def test_child_resolved_observation_identity_cannot_be_tampered(repository_root: Path) -> None:
    child = fixture(repository_root, "initialized-child.json")
    child["resolved_observation"]["scenario"] = "inter_region"
    with pytest.raises(ValueError, match="identity"):
        ChildRunManifest.model_validate(child)


@pytest.mark.parametrize("field", ["manifest_path", "result_path"])
def test_manifest_child_reference_filename_and_path_identity(repository_root: Path, field: str) -> None:
    parent = fixture(repository_root, "initialized-parent.json")
    parent["child_runs"][0][field] = "results/raw/../escaped"
    with pytest.raises(ValueError):
        CampaignManifest.model_validate(parent)
    parent = fixture(repository_root, "initialized-parent.json")
    parent["child_runs"][0][field] = "results/manifests/other.json" if field == "manifest_path" else "results/raw/other"
    with pytest.raises(ValueError):
        CampaignManifest.model_validate(parent)


@pytest.mark.parametrize("manifest_name", ["initialized-parent.json", "initialized-child.json"])
def test_manifest_rejects_malformed_experiment_and_empty_campaign_identity(repository_root: Path, manifest_name: str) -> None:
    data = fixture(repository_root, manifest_name)
    data["experiment_id"] = "bad"
    if manifest_name.endswith("child.json"):
        data["resolved_observation"]["experiment_id"] = "bad"
    model = CampaignManifest if manifest_name.endswith("parent.json") else ChildRunManifest
    with pytest.raises(ValueError):
        model.model_validate(data)
    data = fixture(repository_root, manifest_name)
    data["campaign_id"] = ""
    if manifest_name.endswith("child.json"):
        data["run_id"] = "-aws"
    with pytest.raises(ValueError):
        model.model_validate(data)


def test_succeeded_campaign_requires_every_selected_child(repository_root: Path) -> None:
    parent = fixture(repository_root, "initialized-parent.json")
    parent["aggregate_state"] = "succeeded"
    parent["lifecycle_events"][-1]["state"] = "succeeded"
    parent["child_runs"] = []
    with pytest.raises(ValueError, match="every selected"):
        CampaignManifest.model_validate(parent)


def test_child_manifest_rejects_reversed_actual_times_and_event_order(repository_root: Path) -> None:
    child = fixture(repository_root, "initialized-child.json")
    child["execution_state"] = "running"
    child["actual_started_at"] = "2026-09-27T12:00:02Z"
    child["updated_at"] = "2026-09-27T12:00:03Z"
    child["lifecycle_events"] = [
        {"state_domain":"execution", "state":"initialized", "occurred_at":"2026-09-27T12:00:01Z"},
        {"state_domain":"execution", "state":"running", "occurred_at":"2026-09-27T12:00:00Z"},
    ]
    with pytest.raises(ValueError, match="chronological"):
        ChildRunManifest.model_validate(child)
    child["lifecycle_events"].reverse()
    child["actual_finished_at"] = "2026-09-27T12:00:01Z"
    with pytest.raises(ValueError, match="actual_finished_at"):
        ChildRunManifest.model_validate(child)
