from types import SimpleNamespace

import pytest

from cloud_network_benchmark.lifecycle_remote import (
    _exact_barrier_participant,
    _generated_archive_entry_id,
    _prepared_barrier_entry_id,
)


def _observation(entry_id=None):
    schedule = None if entry_id is None else SimpleNamespace(
        value=SimpleNamespace(entry_id=entry_id))
    return SimpleNamespace(schedule_entry=schedule)


def test_barrier_identity_uses_exact_prepared_observer_or_schedule_entry():
    assert _prepared_barrier_entry_id(_observation(),
        {"observer": {"entry_id": "V-S1"}}) == "V-S1"
    assert _prepared_barrier_entry_id(_observation("scheduled-17"),
        {"observer": {"entry_id": "scheduled-17"}}) == "scheduled-17"


def test_generated_archive_barrier_preserves_scheduled_entry_then_run_fallback():
    assert _generated_archive_entry_id(_observation("scheduled-17"), "run-a") == "scheduled-17"
    assert _generated_archive_entry_id(_observation(), "run-a") == "run-a"


@pytest.mark.parametrize("observation,runtime", [
    (_observation(), {}),
    (_observation("scheduled-17"), {"observer": {"entry_id": "V-S1"}}),
])
def test_barrier_identity_rejects_missing_or_conflicting_prepared_entry(observation, runtime):
    with pytest.raises(ValueError, match="entry identit|identities differ"):
        _prepared_barrier_entry_id(observation, runtime)


def test_foreign_observer_identity_cannot_match_frozen_barrier_membership():
    foreign = _prepared_barrier_entry_id(_observation(),
        {"observer": {"entry_id": "V-S1-foreign"}})
    with pytest.raises(ValueError, match="exactly one"):
        _exact_barrier_participant([{"entry_id": "V-S1", "provider": "aws",
            "run_id": "run-a"}], entry_id=foreign, provider="aws", run_id="run-a")


def test_barrier_membership_requires_one_exact_entry_provider_run_tuple():
    row = {"entry_id": "V-S1", "provider": "aws", "run_id": "run-a"}
    assert _exact_barrier_participant([row], entry_id="V-S1", provider="aws",
        run_id="run-a") is row
    with pytest.raises(ValueError, match="exactly one"):
        _exact_barrier_participant([row], entry_id="V-S1-extra", provider="aws",
            run_id="run-a")
    with pytest.raises(ValueError, match="exactly one"):
        _exact_barrier_participant([row, dict(row)], entry_id="V-S1", provider="aws",
            run_id="run-a")
