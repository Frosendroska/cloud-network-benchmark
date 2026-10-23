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
    start = datetime.now(timezone.utc) - timedelta(seconds=10)
    end = datetime.now(timezone.utc) + timedelta(seconds=60)
    res = reservation(start, end)
    with pytest.raises(GcpProviderError, match="whole remaining workflow"):
        GcpProviderDriver._fit(res, end-timedelta(seconds=30), 120)
