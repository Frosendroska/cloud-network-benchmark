from datetime import datetime, timedelta, timezone

import pytest

from cloud_network_benchmark.providers.gcp import GcpProviderDriver, GcpProviderError, SAFETY_LEAD_SECONDS
from cloud_network_benchmark.contracts.admission import ReservationEvidence
from cloud_network_benchmark.contracts.common import Evidence, StringEvidence


def reservation(start, end):
    return ReservationEvidence(schema_version=2, campaign_id="campaign", reservation_id=Evidence[str](value="reservation-1"), state="reserved", reserved_cost_usd=Evidence[float](value=2.0), cap_started_at=Evidence[datetime](value=start), expires_at=Evidence[datetime](value=end), cap_start_policy_id=StringEvidence(value="reservation-activation-before-provider-expiry"), source_path="register.csv", source_sha256="a" * 64)


def test_expiry_time_is_original_cap_minus_initiation_lead():
    end = datetime(2026, 10, 23, 12, 20, tzinfo=timezone.utc)
    trigger = end - timedelta(seconds=SAFETY_LEAD_SECONDS)
    assert SAFETY_LEAD_SECONDS == 30
    assert trigger == datetime(2026, 10, 23, 12, 19, 30, tzinfo=timezone.utc)


def test_expiry_fit_requires_entire_remaining_workflow_before_trigger():
    now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    start = now - timedelta(seconds=10)
    end = now + timedelta(seconds=60)
    res = reservation(start, end)
    driver = object.__new__(GcpProviderDriver)
    driver.clock = lambda: now
    with pytest.raises(GcpProviderError, match="whole remaining workflow"):
        driver._fit(res, end-timedelta(seconds=30), 120)


def test_expiry_fit_accepts_exact_boundary_but_rejects_new_cap_or_expired_window():
    now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    end = now + timedelta(seconds=600)
    res = reservation(now - timedelta(seconds=1), end)
    driver = object.__new__(GcpProviderDriver)
    driver.clock = lambda: now
    trigger = end - timedelta(seconds=SAFETY_LEAD_SECONDS)
    assert driver._fit(res, trigger, (trigger - now).total_seconds()) is None
    with pytest.raises(GcpProviderError, match="whole remaining workflow"):
        driver._fit(res, trigger, (trigger - now).total_seconds() + 0.001)
    with pytest.raises(GcpProviderError, match="original reservation cap"):
        driver._fit(res, trigger + timedelta(seconds=1), 1)
    future_start = reservation(now + timedelta(seconds=1), end)
    with pytest.raises(GcpProviderError, match="whole remaining workflow"):
        driver._fit(future_start, trigger, 1)
    expired = reservation(now - timedelta(seconds=30), now + timedelta(seconds=20))
    with pytest.raises(GcpProviderError, match="whole remaining workflow"):
        driver._fit(expired, now - timedelta(seconds=10), 1)


def test_template_creation_must_fall_inside_original_reservation_window():
    cap_start = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    registered = cap_start + timedelta(seconds=10)
    assert GcpProviderDriver._validate_template_created_at("2026-10-23T12:00:05Z", cap_start, registered) == cap_start + timedelta(seconds=5)
    for invalid in (None, "bad timestamp", "2026-10-23T11:59:59Z", "2026-10-23T12:00:11Z", "2026-10-23T12:00:05"):
        with pytest.raises(GcpProviderError, match="template creation"):
            GcpProviderDriver._validate_template_created_at(invalid, cap_start, registered)
