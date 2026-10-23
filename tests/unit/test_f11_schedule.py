from datetime import datetime, timezone
from datetime import timedelta
import csv
import hashlib
import json
from pathlib import Path

import pytest

from cloud_network_benchmark.contracts.common import Evidence, Provider
from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.scheduling import inspect_schedule
from cloud_network_benchmark.scheduling import assess_provider_skew, load_schedule


ROOT = Path(__file__).parents[2]


def test_pending_fixture_preserves_row_and_does_not_invent_time_or_n():
    entries = load_schedule(ROOT / "tests/fixtures/f11/schedule-pending.csv",
        config_paths={"exp-001-short": ROOT / "configs/experiments/exp-001-multi-provider.yaml"},
        repository_root=ROOT)
    assert len(entries) == 1
    assert entries[0].scheduled_start is None
    assert entries[0].original_row == "F11-SYNTHETIC-S1,exp-001-short,EXP-001,,night,P0"
    assert len(entries[0].source_sha256) == len(entries[0].row_sha256) == 64


def test_bom_quoted_multiline_record_keeps_exact_source_and_row_hashes(tmp_path):
    source=tmp_path/"bom.csv"
    header="entry_id,config_id,experiment_id,scheduled_start,window_label,priority"
    row='"F11-QUOTED",exp-001-short,EXP-001,2026-10-22T12:00:00+02:00,"night\n",P0'
    raw=("\ufeff"+header+"\r\n"+row+"\r\n").encode("utf-8")
    source.write_bytes(raw)
    entries=load_schedule(source,config_paths={"exp-001-short":ROOT/"configs/experiments/exp-001-multi-provider.yaml"},repository_root=ROOT)
    assert entries[0].original_row==row
    assert entries[0].original_header==header
    assert entries[0].source_sha256==hashlib.sha256(raw).hexdigest()
    assert entries[0].row_sha256==hashlib.sha256(row.encode()).hexdigest()
    assert entries[0].scheduled_start==datetime(2026,10,22,10,0,tzinfo=timezone.utc)


@pytest.mark.parametrize(("header","rows","message"),[
    ("entry_id,config_id,experiment_id,scheduled_start,window_label,priority,extra",["E1,exp-001-short,EXP-001,,night,P0,x"],"unknown"),
    ("entry_id,config_id,experiment_id,scheduled_start,window_label,priority,priority",["E1,exp-001-short,EXP-001,,night,P0,P0"],"duplicated"),
    ("entry_id,config_id,experiment_id,scheduled_start,window_label,priority",["E1,exp-001-short,EXP-001,,night,P0","E1,exp-001-short,EXP-001,,night,P0"],"duplicate entry id"),
    ("entry_id,config_id,experiment_id,scheduled_start,window_label,priority",["E1,exp-001-short,EXP-999,,night,P0"],"different configuration"),
    ("entry_id,config_id,experiment_id,scheduled_start,window_label,priority",["E1,exp-001-short,EXP-001,,night,P1"],"priority does not match"),
    ("entry_id,config_id,experiment_id,scheduled_start,window_label,priority",["E1,exp-001-short,EXP-001,2026-10-22T12:00:00,night,P0"],"include UTC offset"),
    ("entry_id,config_id,experiment_id,scheduled_start,window_label,priority",["E1,exp-001-short,EXP-001,,token=secret,P0"],"secret-bearing"),
])
def test_invalid_schedule_fields_and_config_identity_fail_closed(tmp_path,header,rows,message):
    source=tmp_path/"invalid.csv"
    source.write_text(header+"\n"+"\n".join(rows)+"\n",encoding="utf-8")
    with pytest.raises(Exception,match=message):
        load_schedule(source,config_paths={"exp-001-short":ROOT/"configs/experiments/exp-001-multi-provider.yaml"},repository_root=ROOT)


@pytest.mark.parametrize(("delta", "eligibility", "target", "finding"), [
    (60, "eligible", True, None), (60.001, "eligible", False, "provider_skew_target_missed"),
    (300, "eligible", False, "provider_skew_target_missed"), (300.001, "ineligible", False, "provider_skew_excluded"),
])
def test_skew_uses_actual_per_phase_starts(delta, eligibility, target, finding):
    start = datetime(2026, 10, 22, tzinfo=timezone.utc)
    result = assess_provider_skew("single_flow", selected_providers=[Provider.AWS, Provider.AZURE],
        actual_starts={Provider.AWS: Evidence[datetime](value=start),
                       Provider.AZURE: Evidence[datetime](value=start.replace(microsecond=0) + __import__("datetime").timedelta(seconds=delta))})
    assert result.comparison_eligibility == eligibility
    assert result.target_met is target
    assert result.finding == finding


def test_missing_actual_start_is_unavailable():
    start = datetime(2026, 10, 22, tzinfo=timezone.utc)
    result = assess_provider_skew("idle_latency", selected_providers=[Provider.AWS, Provider.GCP],
        actual_starts={Provider.AWS: Evidence[datetime](value=start), Provider.GCP: Evidence[datetime](unavailable_reason="not_recorded")})
    assert result.comparison_eligibility == "unavailable"
    assert result.finding == "provider_skew_unavailable"


def test_synthetic_complete_primary_week_is_28_paired_slots(tmp_path):
    # Dates and times here are synthetic test values, not the research schedule.
    source = tmp_path / "week.csv"
    config_paths = {"exp-001-short": ROOT / "configs/experiments/exp-001-multi-provider.yaml",
                    "exp-003-short": ROOT / "configs/experiments/exp-003-placement-optimization.yaml"}
    policy=json.loads((ROOT/"tests/fixtures/f11/scenarios.json").read_text())
    assert policy["fixture_scope"]=="synthetic-test-only"
    assert policy["synthetic_common_n"] == 4
    assert policy["synthetic_prerequisites"]["paid_authorization"] is False
    windows = policy["synthetic_windows"]
    start_date=datetime.fromisoformat(policy["synthetic_week_start"]).date()
    with source.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["entry_id", "config_id", "experiment_id", "scheduled_start", "window_label", "priority"])
        for day in range(7):
            for window, hour in windows.items():
                start = f"{start_date+timedelta(days=day)}T{hour}+00:00"
                writer.writerow([f"S1-D{day+1}-{window}", "exp-001-short", "EXP-001", start, window, "P0"])
                writer.writerow([f"S3-D{day+1}-{window}", "exp-003-short", "EXP-003", start, window, "P1"])
    entries = load_schedule(source, config_paths=config_paths, repository_root=ROOT)
    campaigns = {e.entry_id: resolve_campaign(config_paths[e.config_id], ROOT, schedule_entry=e)[0] for e in entries}
    result = inspect_schedule(entries, campaigns_by_entry_id=campaigns,
        now=datetime(2026, 9, 30, tzinfo=timezone.utc), require_primary_week=True)
    assert len(entries) == 56
    assert result.complete_primary_week
    assert not result.errors
    assert result.simultaneous_vm_demand == {"aws": 4, "azure": 4, "gcp": 4}
    assert len(result.identities_by_entry) == 56
    assert len({i.campaign_id for i in result.identities_by_entry.values()}) == 56
    for identity in result.identities_by_entry.values():
        assert set(identity.child_identities) == {"aws", "azure", "gcp"}
        assert len(set(identity.resource_namespaces.values())) == 3
        assert len(set(identity.state_namespaces.values())) == 3
        assert len(set(identity.result_namespaces.values())) == 3
    assert {sum(result.simultaneous_vm_demand.values())} == {12}


def test_complete_week_rejects_two_primary_entries_with_same_priority(tmp_path):
    source = tmp_path / "week.csv"
    config_paths = {"exp-001-short": ROOT / "configs/experiments/exp-001-multi-provider.yaml",
                    "exp-003-short": ROOT / "configs/experiments/exp-003-placement-optimization.yaml"}
    policy=json.loads((ROOT/"tests/fixtures/f11/scenarios.json").read_text())
    windows = policy["synthetic_windows"]
    start_date=datetime.fromisoformat(policy["synthetic_week_start"]).date()
    with source.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["entry_id", "config_id", "experiment_id", "scheduled_start", "window_label", "priority"])
        for day in range(7):
            for window, hour in windows.items():
                start = f"{start_date+timedelta(days=day)}T{hour}+00:00"
                writer.writerow([f"S1-D{day+1}-{window}", "exp-001-short", "EXP-001", start, window, "P0"])
                writer.writerow([f"S3-D{day+1}-{window}", "exp-003-short", "EXP-003", start, window, "P1"])
    entries = load_schedule(source, config_paths=config_paths, repository_root=ROOT)
    campaigns = {e.entry_id: resolve_campaign(config_paths[e.config_id], ROOT, schedule_entry=e)[0] for e in entries}
    entries[1] = entries[1].model_copy(update={"priority": "P0"})
    result = inspect_schedule(entries, campaigns_by_entry_id=campaigns,
        now=datetime(2026, 9, 30, tzinfo=timezone.utc), require_primary_week=True)
    assert not result.complete_primary_week
    assert "invalid_schedule: primary week requires 28+28 aligned entries" in result.errors


def test_calibration_and_diagnostic_day_remain_single_entries(tmp_path):
    source = tmp_path / "retained.csv"
    configs = {"exp-001-calibration": ROOT / "configs/experiments/exp-001-calibration.yaml",
               "exp-001-day": ROOT / "configs/experiments/exp-001-diagnostic-day.yaml"}
    with source.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["entry_id", "config_id", "experiment_id", "scheduled_start", "window_label", "priority"])
        writer.writerow(["CAL-ONE", "exp-001-calibration", "EXP-001", "", "", "P0"])
        writer.writerow(["DAY-ONE", "exp-001-day", "EXP-001", "", "", "P0"])
    entries = load_schedule(source, config_paths=configs, repository_root=ROOT)
    campaigns = {e.entry_id: resolve_campaign(configs[e.config_id], ROOT, schedule_entry=e)[0] for e in entries}
    result = inspect_schedule(entries, campaigns_by_entry_id=campaigns,
        now=datetime(2026, 9, 30, tzinfo=timezone.utc))
    assert not result.errors
    assert len(entries) == 2
    assert len(result.identities_by_entry) == 2
    with source.open("a", newline="", encoding="utf-8") as stream:
        csv.writer(stream).writerow(["DAY-TWO", "exp-001-day", "EXP-001", "", "", "P0"])
    repeated = load_schedule(source, config_paths=configs, repository_root=ROOT)
    repeated_campaigns = {e.entry_id: resolve_campaign(configs[e.config_id], ROOT, schedule_entry=e)[0] for e in repeated}
    repeated_result = inspect_schedule(repeated, campaigns_by_entry_id=repeated_campaigns,
        now=datetime(2026, 9, 30, tzinfo=timezone.utc))
    assert "identity_mismatch:DAY-ONE:retained_mode_requires_one_entry" in repeated_result.errors
