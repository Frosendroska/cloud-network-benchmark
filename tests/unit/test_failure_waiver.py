from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cloud_network_benchmark.campaign_inputs import _review_from_prepared, _selected
from cloud_network_benchmark.campaign_draft import validate_fixed_batch_scope
from cloud_network_benchmark.failure_waiver import (
    NORMAL_ENTRIES, RESIDUAL_RISK, SUPPORTED_STAGES, WAIVED_ENTRIES, WAIVED_RESOURCES,
    bind_prepared_acceptance, registered_failure_record, source_waiver,
    validate_later_stage, validate_prepared_binding, validate_registered_failure_record,
)


def _declaration(tmp_path: Path):
    source = tmp_path / "DECISIONS.md"
    source.write_text("## 2026-11-23 Waive deliberate pre-DAY fault, expiry and recovery experiments\n")
    return {"stage_inputs": {"acceptance": {"failure_waiver": {
        "confirmed": True, "source": str(source),
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}}}}


def _prepared(tmp_path: Path):
    entries = []
    for index, entry_id in enumerate(NORMAL_ENTRIES):
        config = tmp_path / f"{entry_id}.yaml"; config.write_text(f"config-{entry_id}\n")
        runtime = tmp_path / f"{entry_id}.json"; runtime.write_text(json.dumps({"stage": entry_id}))
        parent = tmp_path / f"{entry_id}-parent.json"
        providers = {provider: f"{entry_id}-{provider}" for provider in ("aws", "azure", "gcp")}
        parent.write_text(json.dumps({"campaign_id": f"campaign-{entry_id}",
            "implementation_git_commit": "a" * 40,
            "config_provenance": {"sha256": hashlib.sha256(config.read_bytes()).hexdigest()},
            "child_runs": [{"provider": p, "run_id": run} for p, run in providers.items()]}))
        entries.append({"entry_id": entry_id, "config_path": str(config),
            "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
            "source_config_path": str(config),
            "source_config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
            "runtime_config_path": str(runtime),
            "runtime_config_sha256": hashlib.sha256(runtime.read_bytes()).hexdigest(),
            "parent_manifest_path": str(parent), "run_ids": providers,
            "implementation_git_commit": "a" * 40})
    return {"stage": "acceptance", "repository_root": str(tmp_path), "entries": entries}


def test_explicit_waiver_selects_only_normal_acceptance_and_records_not_tested(tmp_path):
    settings = _declaration(tmp_path)
    assert _selected({"stage": "acceptance"}) == [
        ("V-S1", "s1_acceptance"), ("V-S3", "s3_acceptance"),
        ("V-A-FAIL", "aws_failure"), ("V-Z-FAIL", "azure_failure"), ("V-G-FAIL", "gcp_failure")]
    assert _selected({"stage": "acceptance"}, settings) == [
        ("V-S1", "s1_acceptance"), ("V-S3", "s3_acceptance")]
    prepared = _prepared(tmp_path)
    review, failures = _review_from_prepared(settings, prepared)
    assert [row["entry_id"] for row in failures["checks"]["V-A-FAIL"]["normal_acceptance"]] == list(NORMAL_ENTRIES)
    assert failures["status"] == "WAIVED"
    assert {row["result"] for row in failures["checks"].values()} == {"NOT TESTED"}
    assert failures["waiver"]["not_tested"] == list(WAIVED_ENTRIES)
    assert failures["waiver"]["waived_resources"] == list(WAIVED_RESOURCES)
    assert failures["waiver"]["supported_stages"] == ["acceptance", "calibration", "day"]
    assert review["accepted_campaigns"] == [row["parent_manifest_path"] for row in prepared["entries"]]
    validate_registered_failure_record(failures)


@pytest.mark.parametrize("damage", ["wrong_scope", "wrong_stage", "tampered_source", "malformed"])
def test_waiver_rejects_malformed_wrong_scope_stage_or_tampered_source(tmp_path, damage):
    settings = _declaration(tmp_path)
    declaration = settings["stage_inputs"]["acceptance"]["failure_waiver"]
    if damage == "malformed":
        declaration["extra"] = True
    elif damage == "tampered_source":
        Path(declaration["source"]).write_text("changed decision\n")
    elif damage == "wrong_scope":
        declaration["sha256"] = "0" * 64
    else:
        declaration["source"] = str(tmp_path / "missing-decision.md")
    with pytest.raises(ValueError):
        source_waiver(settings)


def test_waiver_record_tampering_and_unsupported_later_stage_fail_closed(tmp_path):
    settings = _declaration(tmp_path)
    prepared = _prepared(tmp_path)
    base = source_waiver(settings)
    binding = bind_prepared_acceptance(base, prepared["entries"])
    validate_prepared_binding(binding, prepared["entries"])
    record = registered_failure_record(binding)
    validate_registered_failure_record(record)
    assert set(SUPPORTED_STAGES) == {"acceptance", "calibration", "day"}
    assert "WAIVED / NOT TESTED" in RESIDUAL_RISK
    validate_later_stage(record, "calibration")
    validate_later_stage(record, "day")
    with pytest.raises(ValueError, match="only"):
        validate_later_stage(record, "week")
    record["checks"]["V-A-FAIL"]["stage"] = "week"
    with pytest.raises(ValueError, match="scope"):
        validate_registered_failure_record(record)


@pytest.mark.parametrize("field,value", [
    ("supported_stages", ["acceptance", "calibration", "week"]),
    ("waived_resources", ["server_fault_injection"]),
])
def test_waiver_rejects_wrong_stage_or_resource_scope(tmp_path, field, value):
    prepared = _prepared(tmp_path)
    binding = bind_prepared_acceptance(source_waiver(_declaration(tmp_path)), prepared["entries"])
    binding[field] = value
    with pytest.raises(ValueError, match="stage/resource/scope"):
        validate_prepared_binding(binding)


def test_waiver_rejects_wrong_normal_run_source_identity(tmp_path):
    prepared = _prepared(tmp_path)
    binding = bind_prepared_acceptance(source_waiver(_declaration(tmp_path)), prepared["entries"])
    binding["normal_acceptance"][0]["run_ids"]["aws"] = "foreign-run"
    with pytest.raises(ValueError, match="exact prepared campaign source identities"):
        validate_prepared_binding(binding)


def test_fixed_batch_accepts_exact_reduced_selection_only_with_bound_waiver(tmp_path):
    prepared = _prepared(tmp_path)
    binding = bind_prepared_acceptance(source_waiver(_declaration(tmp_path)), prepared["entries"])
    acceptance_entries = []
    for entry, scenario in zip(prepared["entries"], ("S1", "S3")):
        acceptance_entries.append({**entry, "scenario": scenario, "requires_supervision": False})
    calibration = {"entry_id": "CAL-S1", "run_ids": {p: f"CAL-S1-{p}" for p in ("aws", "azure", "gcp")}}
    stages = [{"stage": "acceptance", "entries": acceptance_entries, "failure_waiver": binding},
        {"stage": "calibration", "entries": [calibration]}]
    validate_fixed_batch_scope(stages)
    stages[0]["failure_waiver"]["residual_risk"] = ""
    with pytest.raises(ValueError, match="waiver"):
        validate_fixed_batch_scope(stages)
