"""Source-backed acceptance applicability using real materialized native DTOs.

Every review here is explicitly synthetic offline evidence for testing; this
module never executes a provider or asserts empirical acceptance.
"""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest
import yaml


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _materialize_review(repository_root, tmp_path, frozen_clock, deterministic_token,
                        fake_git_commits, request):
    from tests.f12_support import materialize_f08_pair
    from cloud_network_benchmark.manifests import ChildRunManifest, CampaignManifest, recompute_parent_manifest
    from cloud_network_benchmark.contracts.campaign import ResolvedCampaign

    # Materialize every selected provider in this actual one-provider scope,
    # rather than falsely marking untouched siblings of a three-cloud parent done.
    config_name = "exp-900-single-provider-north.yaml"
    value, result, _ = materialize_f08_pair(repository_root, tmp_path, frozen_clock,
        deterministic_token, fake_git_commits, request, mode="acceptance", config_name=config_name)
    raw = Path(value.child_result_root)
    (raw / "validation").mkdir(exist_ok=True)
    (raw / "validation/input.json").write_text(value.model_dump_json())
    (raw / "validation/result.json").write_text(result.model_dump_json())
    child_path = Path(value.child_manifest_path)
    child = ChildRunManifest.model_validate_json(child_path.read_bytes())
    child = child.model_copy(update={"validation_status": result.validation_status})
    child_path.write_text(child.model_dump_json())
    parent_path = Path(value.campaign_manifest_path)
    parent = CampaignManifest.model_validate_json(parent_path.read_bytes())
    parent = recompute_parent_manifest(parent_path, max(parent.updated_at, child.updated_at) + timedelta(seconds=1),
                                      "synthetic offline native fixture accepted")
    assert parent.aggregate_state == "succeeded"
    target = ResolvedCampaign.model_validate_json((raw / "config.resolved.json").read_bytes())
    checks = {}
    for name, provider in (("V-A-FAIL", "aws"), ("V-Z-FAIL", "azure"), ("V-G-FAIL", "gcp")):
        attempt = f"synthetic-offline-{provider}-attempt"
        row = {"provider": provider, "attempt_id": attempt}
        for action in ("server_failure", "controller_loss", "expiry", "cleanup"):
            source = tmp_path / f"{name}-{action}.txt"
            source.write_text(f"SYNTHETIC OFFLINE TEST ONLY: {attempt}, {action}\n")
            row[action] = {"status": "passed", "source_path": str(source), "source_sha256": _sha(source)}
        checks[name] = row
    failures = tmp_path / "synthetic-failure-review.yaml"
    failures.write_text(yaml.safe_dump({"schema_version": 1, "checks": checks}))
    accepted_options = {"account_id": "123456789012", "ami_ids": {"eu-north-1": "ami-0123456789abcdef0"}}
    runtime_path = tmp_path / "synthetic-accepted-runtime.json"
    runtime_path.write_text(json.dumps({"initialized_campaign_manifest": str(parent_path),
        "providers": {"aws": {"options": accepted_options}}}))
    source = tmp_path / "synthetic-paid-review.yaml"
    source.write_text(yaml.safe_dump({"schema_version": 1, "accepted_campaigns": [str(parent_path)],
        "repository_root": str(repository_root), "registered_failure_review": str(failures),
        "accepted_runtime_configs": {str(parent_path): {"path": str(runtime_path), "sha256": _sha(runtime_path)}}}))
    evidence = {"evidence_id": "synthetic-offline-acceptance-review", "source_path": str(source),
        "source_sha256": _sha(source), "accepted_campaigns": [{"path": str(parent_path), "sha256": _sha(parent_path)}],
        "repository_root": str(repository_root), "registered_failure_review": {"path": str(failures), "sha256": _sha(failures)},
        "requested_provider_scope": {"aws": accepted_options}}
    return target, evidence, raw


def test_materialized_native_acceptance_and_exact_failure_sources_cover_matching_scope(repository_root, tmp_path,
        frozen_clock, deterministic_token, fake_git_commits, request):
    from cloud_network_benchmark.admission import _validate_paid_acceptance_evidence

    campaign, evidence, _ = _materialize_review(repository_root, tmp_path, frozen_clock,
        deterministic_token, fake_git_commits, request)
    _validate_paid_acceptance_evidence(evidence, campaign)


def test_registered_failure_review_accepts_explicit_physical_loss_waiver_with_ordinary_recovery(tmp_path):
    from cloud_network_benchmark.acceptance_review import _failure_review, _runtime_scope_sha256

    checks = {}
    for registered, provider in (("V-A-FAIL", "aws"), ("V-Z-FAIL", "azure"), ("V-G-FAIL", "gcp")):
        attempt = f"synthetic-{provider}-attempt"
        config = tmp_path / f"{registered}-config.yaml"
        config.write_text("SYNTHETIC OFFLINE TEST ONLY: registered config\n")
        runtime = tmp_path / f"{registered}-runtime.json"
        runtime.write_text('{"synthetic": true}\n')
        row = {"provider": provider, "attempt_id": attempt, "config_identity": {
            "config_path": str(config), "config_sha256": _sha(config), "source_config_sha256": "a" * 64,
            "runtime_config_path": str(runtime), "runtime_config_sha256": _sha(runtime),
            "runtime_scope_sha256": _runtime_scope_sha256(runtime), "implementation_git_commit": {"value": "a" * 40}}}
        for action in ("server_failure", "expiry", "cleanup", "ordinary_recovery"):
            source = tmp_path / f"{registered}-{action}.txt"
            source.write_text(f"SYNTHETIC OFFLINE TEST ONLY: {attempt}: {action}\n")
            row[action] = {"status": "passed", "source_path": str(source), "source_sha256": _sha(source)}
        waiver = tmp_path / f"{registered}-waiver.txt"
        waiver.write_text("SYNTHETIC OFFLINE TEST ONLY: the physical controller-loss check is waived.\n")
        row["controller_loss"] = {"status": "waived", "reason": "Physical Halle loss is explicitly waived.",
            "source_path": str(waiver), "source_sha256": _sha(waiver)}
        checks[registered] = row
    review = tmp_path / "failure-review.yaml"
    review.write_text(yaml.safe_dump({"schema_version": 1, "checks": checks}))
    _failure_review({"path": str(review), "sha256": _sha(review)})


@pytest.mark.parametrize("damage", ["no_waiver", "missing_recovery", "missing_expiry",
    "missing_server_failure", "missing_cleanup", "wrong_attempt", "wrong_source_hash", "changed_config"])
def test_physical_loss_waiver_does_not_bypass_registered_failure_outcomes(tmp_path, damage):
    from cloud_network_benchmark.acceptance_review import _failure_review, _runtime_scope_sha256

    checks = {}
    for registered, provider in (("V-A-FAIL", "aws"), ("V-Z-FAIL", "azure"), ("V-G-FAIL", "gcp")):
        attempt = f"synthetic-{provider}-attempt"
        config = tmp_path / f"{registered}-{damage}-config.yaml"
        config.write_text("SYNTHETIC OFFLINE TEST ONLY: registered config\n")
        runtime = tmp_path / f"{registered}-{damage}-runtime.json"
        runtime.write_text('{"synthetic": true}\n')
        row = {"provider": provider, "attempt_id": attempt, "config_identity": {
            "config_path": str(config), "config_sha256": _sha(config), "source_config_sha256": "b" * 64,
            "runtime_config_path": str(runtime), "runtime_config_sha256": _sha(runtime),
            "runtime_scope_sha256": _runtime_scope_sha256(runtime), "implementation_git_commit": {"value": "b" * 40}}}
        for action in ("server_failure", "expiry", "cleanup", "ordinary_recovery"):
            if damage == "missing_recovery" and action == "ordinary_recovery":
                continue
            if damage == "missing_expiry" and action == "expiry":
                continue
            if damage == "missing_server_failure" and action == "server_failure":
                continue
            if damage == "missing_cleanup" and action == "cleanup":
                continue
            source = tmp_path / f"{registered}-{damage}-{action}.txt"
            token = "some-other-attempt" if damage == "wrong_attempt" and action == "ordinary_recovery" else attempt
            source.write_text(f"SYNTHETIC OFFLINE TEST ONLY: {token}: {action}\n")
            row[action] = {"status": "passed", "source_path": str(source), "source_sha256": _sha(source)}
            if damage == "wrong_source_hash" and action == "cleanup":
                row[action]["source_sha256"] = "0" * 64
        waiver = tmp_path / f"{registered}-{damage}-waiver.txt"
        waiver.write_text("SYNTHETIC OFFLINE TEST ONLY: physical controller loss was waived.\n")
        if damage == "no_waiver":
            row["controller_loss"] = {"status": "missing"}
        else:
            row["controller_loss"] = {"status": "waived", "reason": "Explicitly waived.",
                "source_path": str(waiver), "source_sha256": _sha(waiver)}
        checks[registered] = row
        if damage == "changed_config":
            config.write_text("SYNTHETIC OFFLINE TEST ONLY: changed registered config\n")
    review = tmp_path / f"failure-review-{damage}.yaml"
    review.write_text(yaml.safe_dump({"schema_version": 1, "checks": checks}))
    with pytest.raises(ValueError):
        _failure_review({"path": str(review), "sha256": _sha(review)})


@pytest.mark.parametrize("corruption", ["arbitrary_failure_file", "changed_snapshot", "forged_review_wrapper", "changed_image_intent", "changed_account", "changed_concrete_image", "changed_accepted_runtime"])
def test_acceptance_scope_rejects_unverified_or_mismatched_sources(repository_root, tmp_path,
        frozen_clock, deterministic_token, fake_git_commits, request, corruption):
    from cloud_network_benchmark.admission import _validate_paid_acceptance_evidence

    campaign, evidence, raw = _materialize_review(repository_root, tmp_path, frozen_clock,
        deterministic_token, fake_git_commits, request)
    if corruption == "arbitrary_failure_file":
        failures = Path(evidence["registered_failure_review"]["path"])
        failures.write_text("This arbitrary file is not a supervised failure review.\n")
        evidence["registered_failure_review"]["sha256"] = _sha(failures)
    elif corruption == "changed_snapshot":
        path = raw / "config.resolved.json"
        payload = json.loads(path.read_text())
        payload["observations"][0]["vm_a"]["image"] = "changed-image"
        path.write_text(json.dumps(payload))
    elif corruption == "forged_review_wrapper":
        evidence["repository_root"] = str(tmp_path / "forged-root")
    elif corruption == "changed_account":
        evidence["requested_provider_scope"]["aws"]["account_id"] = "999999999999"
    elif corruption == "changed_concrete_image":
        evidence["requested_provider_scope"]["aws"]["ami_ids"] = {"eu-north-1": "ami-fedcba98765432100"}
    elif corruption == "changed_accepted_runtime":
        runtime = tmp_path / "synthetic-accepted-runtime.json"
        runtime.write_text(runtime.read_text() + " ")
    else:
        observation = campaign.observations[0]
        changed_vm = observation.vm_a.model_copy(update={"image": "changed-image"})
        changed = observation.model_copy(update={"vm_a": changed_vm})
        campaign = campaign.model_copy(update={"observations": [changed]})
    with pytest.raises(ValueError):
        _validate_paid_acceptance_evidence(evidence, campaign)
