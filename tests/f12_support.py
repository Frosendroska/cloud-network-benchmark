from __future__ import annotations

import json
from pathlib import Path


def materialize_f08_pair(repository_root, tmp_path, frozen_clock, deterministic_token,
                         fake_git_commits, request, *, mode="short_study", provider="aws",
                         config_name=None, shared_slot=False):
    """Use F08's validated lifecycle fixture, then persist the exact existing DTOs."""
    from tests.integration.test_native_validation_replay import _materialize_complete_bundle
    from cloud_network_benchmark.contracts import ArtifactExpectation, ArtifactInput
    from cloud_network_benchmark.contracts.validation import ValidatorInput
    from cloud_network_benchmark.validation import validate_bundle
    from tests.unit.test_validation import NOW

    config_name = config_name or {"calibration": "exp-001-calibration.yaml",
        "diagnostic_day": "exp-001-diagnostic-day.yaml"}.get(mode, "exp-001-multi-provider.yaml")
    complete, result = _materialize_complete_bundle(
        repository_root, tmp_path, frozen_clock, deterministic_token, fake_git_commits,
        request, mode_override="acceptance" if mode == "acceptance" else None,
        config_name=config_name, provider=provider, frozen_n=(mode == "short_study"), shared_slot=shared_slot,
    )
    # F08's replay helper adds observed producer sidecars with model_copy for
    # focused tests. F12 persists them as proper F01R supplemental declarations
    # and keeps the materialized identities/bytes unchanged.
    raw = complete.model_dump(mode="json")
    records = []
    supplemental = {}
    for item in raw["artifact_records"]:
        if item["kind"] == "sockperf-log":
            item["artifact_id"] = f"{item['unit_id']}-sockperf-log"
        records.append(item)
        if item["kind"] in {"sockperf-log", "build-identity"}:
            supplemental[item["artifact_id"]] = ArtifactInput(
                artifact_id=item["artifact_id"], path=item["path"], kind=item["kind"],
                producer=item["producer"], unit_id=item["unit_id"],
                expectation=ArtifactExpectation.REQUIRED,
            ).model_dump(mode="json")
    raw["artifact_records"] = records
    raw["artifacts"].update(supplemental)
    complete = ValidatorInput.model_validate(raw)
    complete = ValidatorInput.model_validate_json(complete.model_dump_json())
    child_path = Path(complete.child_manifest_path)
    child = json.loads(child_path.read_text(encoding="utf-8"))
    child["artifact_records"] = [item for item in records
        if item["unit_id"] is None or item["unit_id"] in {unit.unit_id for unit in complete.measurement_units}]
    child_path.write_text(json.dumps(child, sort_keys=True), encoding="utf-8")
    result = validate_bundle(complete, evaluated_at=NOW)

    pair = tmp_path / "f12-pair"
    pair.mkdir()
    validator_path = pair / "validator-input.json"
    result_path = pair / "validation-result.json"
    validator_path.write_text(complete.model_dump_json(), encoding="utf-8")
    result_path.write_text(result.model_dump_json(), encoding="utf-8")
    selection_path = pair / "selection.json"
    selection_path.write_text(json.dumps({"format_version": 1, "runs": [{
        "validator_input": validator_path.name,
        "validation_result": result_path.name,
    }]}), encoding="utf-8")
    return complete, result, selection_path
