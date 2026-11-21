from datetime import datetime, timedelta, timezone

from cloud_network_benchmark import azure_provider
from cloud_network_benchmark.contracts.common import Evidence
from tests.contract.test_azure_provider_contract import options as base_options

PROFILE = "azure_exp901_sweden_acceptance_v1"
START = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)


class Clock:
    def __init__(self, monotonic=1000.0, utc=START + timedelta(seconds=100)):
        self.mono = monotonic
        self.wall = utc

    def monotonic(self):
        return self.mono

    def utc_now(self):
        return self.wall


def fixture(tmp_path):
    from types import SimpleNamespace

    reservation = SimpleNamespace(
        reservation_id=SimpleNamespace(value="profile-reservation"),
        cap_started_at=SimpleNamespace(value=START),
        expires_at=SimpleNamespace(value=START + timedelta(seconds=1800)),
    )
    deployment = SimpleNamespace(admission=SimpleNamespace(timing_profile=PROFILE),
        run_id="profile-run", deployment_id="profile-deployment", config_sha256="a" * 64,
        reservation=SimpleNamespace(value=reservation))
    base = base_options(tmp_path)
    value = base.model_dump(mode="python")
    value.update(complete_workflow_seconds=1480, post_registration_workflow_seconds=1300,
        cap_deadline_monotonic=2700, retry_end_at=START + timedelta(seconds=1800),
        timing_profile=PROFILE, cleanup_timing_path=tmp_path / "private" / "timing.json",
        profile_allowance={"registration_seconds":180,"provision_seconds":240,
            "readiness_seconds":180,"stabilization_seconds":60,"measurement_seconds":100,
            "measurement_shutdown_seconds":15,"retrieval_seconds":45,"cleanup_seconds":660,
            "azure_provider_lead_seconds":120})
    return deployment, azure_provider.AzureRuntimeOptions.model_validate(value)


def test_profile_cleanup_deadlines_survive_recovery_and_exhaust_to_sticky_emergency(tmp_path):
    deployment, options = fixture(tmp_path)
    clock = Clock()
    azure_provider._initialize_profile_timing(deployment, options, clock=clock.monotonic, utc_now=clock.utc_now)
    stop = START + timedelta(seconds=1680)
    azure_provider._bind_profile_stop(deployment, options, stop)

    first_deadline, first_state, failure = azure_provider._begin_profile_cleanup(
        deployment, options, clock=clock.monotonic, utc_now=clock.utc_now, invocation_seconds=400)
    assert first_deadline == 1300.0
    assert first_state["routine_deadline_monotonic"] == 1600.0
    assert first_state["absence_deadline_monotonic"] == 1660.0
    assert first_state["cleanup_invocations"] == 1 and failure is None

    # A process restart on the same OS monotonic epoch reads the original deadline.
    clock.mono = 1400.0
    clock.wall = START + timedelta(seconds=500)
    second_deadline, second_state, failure = azure_provider._begin_profile_cleanup(
        deployment, options, clock=clock.monotonic, utc_now=clock.utc_now, invocation_seconds=300)
    assert second_deadline == 1600.0
    assert second_state["routine_deadline_monotonic"] == 1600.0 and failure is None

    # Exhaustion marks acceptance failed and still returns a bounded emergency invocation.
    clock.mono = 1600.0
    clock.wall = START + timedelta(seconds=800)
    emergency_deadline, late_state, failure = azure_provider._begin_profile_cleanup(
        deployment, options, clock=clock.monotonic, utc_now=clock.utc_now, invocation_seconds=300)
    assert emergency_deadline == 1900.0
    assert late_state["timing_status"] == "late" and failure
    clock.mono = 1601.0
    fourth_deadline, fourth_state, _ = azure_provider._begin_profile_cleanup(
        deployment, options, clock=clock.monotonic, utc_now=clock.utc_now, invocation_seconds=300)
    assert fourth_deadline == 1901.0
    assert fourth_state["cleanup_invocations"] == 4
    assert fourth_state["routine_deadline_monotonic"] == 1600.0
    assert fourth_state["timing_status"] == "late"


def test_timing_finalization_checks_clock_after_durable_write(tmp_path, monkeypatch):
    deployment, options = fixture(tmp_path)
    clock = Clock()
    azure_provider._initialize_profile_timing(deployment, options, clock=clock.monotonic, utc_now=clock.utc_now)
    azure_provider._bind_profile_stop(deployment, options, START + timedelta(seconds=1680))
    _, state, failure = azure_provider._begin_profile_cleanup(
        deployment, options, clock=clock.monotonic, utc_now=clock.utc_now, invocation_seconds=400)
    assert failure is None and state["routine_deadline_monotonic"] == 1600.0
    identity_token = azure_provider._EXPLICIT_TIMING_IDENTITY.set(
        azure_provider._profile_timing_identity(deployment))
    original_atomic = azure_provider._atomic_private
    def slow_atomic(path, value):
        original_atomic(path, value)
        if path == options.cleanup_timing_path:
            clock.mono = 1600.0
            clock.wall = START + timedelta(seconds=700)
    monkeypatch.setattr(azure_provider, "_atomic_private", slow_atomic)
    try:
        result = azure_provider._finish_profile_timing(options, clock=clock.monotonic,
            utc_now=clock.utc_now, absence_verified=False)
    finally:
        azure_provider._EXPLICIT_TIMING_IDENTITY.reset(identity_token)
    assert result["timing_status"] == "late"
    assert "during durable timing write" in result["timing_failure"]


def test_forward_utc_step_past_registered_stop_clips_profile_cleanup(tmp_path):
    deployment, options = fixture(tmp_path)
    clock = Clock()
    azure_provider._initialize_profile_timing(deployment, options, clock=clock.monotonic, utc_now=clock.utc_now)
    stop = START + timedelta(seconds=1680)
    azure_provider._bind_profile_stop(deployment, options, stop)
    first_deadline, state, failure = azure_provider._begin_profile_cleanup(
        deployment, options, clock=clock.monotonic, utc_now=clock.utc_now, invocation_seconds=300)
    assert failure is None and first_deadline == 1300.0
    clock.mono = 1100.0
    clock.wall = stop + timedelta(seconds=1)
    emergency_deadline, late, failure = azure_provider._begin_profile_cleanup(
        deployment, options, clock=clock.monotonic, utc_now=clock.utc_now, invocation_seconds=300)
    assert emergency_deadline == 1400.0
    assert late["timing_status"] == "late" and failure
    assert late["routine_deadline_monotonic"] == state["routine_deadline_monotonic"]
    assert late["routine_deadline_utc"] == state["routine_deadline_utc"]


def test_missing_anchor_and_clock_backstep_fail_closed_without_renewal(tmp_path):
    deployment, options = fixture(tmp_path)
    clock = Clock()
    # Missing profile anchor does not create a replacement 600-second window.
    deadline, state, failure = azure_provider._begin_profile_cleanup(
        deployment, options, clock=clock.monotonic, utc_now=clock.utc_now, invocation_seconds=300)
    assert deadline == 1300.0 and state is None and failure
    assert not options.cleanup_timing_path.exists()

    azure_provider._initialize_profile_timing(deployment, options, clock=clock.monotonic, utc_now=clock.utc_now)
    azure_provider._bind_profile_stop(deployment, options, START + timedelta(seconds=1680))
    azure_provider._begin_profile_cleanup(deployment, options, clock=clock.monotonic,
        utc_now=clock.utc_now, invocation_seconds=300)
    clock.mono += 10
    clock.wall -= timedelta(seconds=1)
    deadline, state, failure = azure_provider._begin_profile_cleanup(deployment, options,
        clock=clock.monotonic, utc_now=clock.utc_now, invocation_seconds=300)
    assert deadline == clock.mono + 300
    assert state["timing_status"] == "unknown" and failure
    assert state["routine_deadline_monotonic"] == 1600.0


def test_missing_registered_stop_keeps_cleanup_bounded_to_original_cap_and_acceptance_unknown(tmp_path):
    deployment, options = fixture(tmp_path)
    clock = Clock()
    azure_provider._initialize_profile_timing(deployment, options, clock=clock.monotonic, utc_now=clock.utc_now)

    deadline, state, failure = azure_provider._begin_profile_cleanup(
        deployment, options, clock=clock.monotonic, utc_now=clock.utc_now, invocation_seconds=400)

    assert deadline == 1300.0
    assert state["provider_stop_at"] is None
    assert state["cleanup_invocations"] == 1
    assert state["first_cleanup_monotonic"] is None
    assert state["missing_stop_cleanup_observed_monotonic"] == clock.mono
    assert state["routine_deadline_monotonic"] == min(clock.mono + 600, options.cap_deadline_monotonic - 60)
    assert state["absence_deadline_monotonic"] == min(clock.mono + 660, options.cap_deadline_monotonic)
    assert state["timing_status"] == "unknown"
    assert failure == "actual registered STOP timing anchor is missing"
    assert "TypeError" not in failure

    original_observation = state["missing_stop_cleanup_observed_monotonic"]
    original_routine = state["routine_deadline_monotonic"]
    original_absence = state["absence_deadline_monotonic"]
    original_cap = state["cap_deadline_monotonic"]
    clock.mono = original_cap + 25
    clock.wall = START + timedelta(seconds=original_cap - 1000 + 25)
    repeated_deadline, repeated, repeated_failure = azure_provider._begin_profile_cleanup(
        deployment, options, clock=clock.monotonic, utc_now=clock.utc_now, invocation_seconds=400)
    assert repeated_deadline == clock.mono + 300
    assert repeated["routine_deadline_monotonic"] == original_routine
    assert repeated["absence_deadline_monotonic"] == original_absence
    assert repeated["cap_deadline_monotonic"] == original_cap
    assert repeated["cleanup_invocations"] == 2
    assert repeated["missing_stop_cleanup_observed_monotonic"] == original_observation
    assert repeated["timing_status"] == "unknown"
    assert repeated_failure == failure


def test_different_clock_epoch_uses_emergency_deadline_and_preserves_original(monkeypatch, tmp_path):
    deployment, options = fixture(tmp_path)
    clock = Clock()
    azure_provider._initialize_profile_timing(deployment, options, clock=clock.monotonic, utc_now=clock.utc_now)
    azure_provider._bind_profile_stop(deployment, options, START + timedelta(seconds=1680))
    _, original, _ = azure_provider._begin_profile_cleanup(deployment, options,
        clock=clock.monotonic, utc_now=clock.utc_now, invocation_seconds=300)
    monkeypatch.setattr(azure_provider, "_clock_identity", lambda: "different-boot")
    clock.mono = 80
    deadline, changed, failure = azure_provider._begin_profile_cleanup(deployment, options,
        clock=clock.monotonic, utc_now=clock.utc_now, invocation_seconds=300)
    assert deadline == 380
    assert changed["routine_deadline_monotonic"] == original["routine_deadline_monotonic"]
    assert changed["timing_status"] == "unknown" and failure
