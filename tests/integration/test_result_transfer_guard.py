"""Synthetic, credential-free boundaries for the paid result-transfer path."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys

import pytest

from cloud_network_benchmark.admission import BudgetLedger
from cloud_network_benchmark.contracts.admission import ExpiryEvidence, ExpiryRequest
from cloud_network_benchmark.contracts.common import Evidence, Provider, StructuredEvidence
from cloud_network_benchmark.contracts.execution import (
    BOUNDED_COPY_EOF_MARKER, CommandRequest, CommandResult, SubprocessCommandRunner,
)
from cloud_network_benchmark.financial_controls import (
    FinancialError, financial_binding, transfer_terms, validate_authority,
)
from cloud_network_benchmark.lifecycle_bundle import retrieve_closed_bundle_with_ledger
from cloud_network_benchmark.orchestration import SavedLifecycleRecovery, write_recovery_journal
from cloud_network_benchmark.orchestration import _remaining_f11_allowance
from cloud_network_benchmark.retrieval import _bounded_stream_script, run_retrieval
from tests.financial_helpers import NOW, rewrite_card, save, synthetic_finances
from tests.fixtures.f02r.support import connection, deadline, result
from tests.integration.test_f11_ledger import record_absence
from tests.unit.test_retrieval import manifest, multi_manifest


def _paid_reservation(tmp_path):
    book, plans, _, _ = synthetic_finances(tmp_path, paid=True)
    decision = book.reserve(plans[0][0], plan=plans[0][1])
    assert decision.status == "reserved", decision.reasons
    return book, decision.evidence.reservation_id.value, plans[0][1].allowance


def test_sourced_price_requires_payload_and_separate_protocol_allowance(tmp_path):
    _, plans, _, _ = synthetic_finances(tmp_path, paid=True)
    envelope = plans[0][1].cost_envelopes["aws"]
    assert transfer_terms(envelope) == (1000, 1000)
    for replacement in (
        None,
        {"payload_limit_bytes": 1000, "protocol_overhead_allowance_bytes": 0,
         "protocol_overhead_priced_usd": "0.001"},
        {"payload_limit_bytes": True, "protocol_overhead_allowance_bytes": 1000,
         "protocol_overhead_priced_usd": "0.001"},
        {"payload_limit_bytes": 1000, "protocol_overhead_allowance_bytes": 1000,
         "protocol_overhead_priced_usd": "0"},
    ):
        changed = {**envelope, "result_transfer": replacement}
        with pytest.raises(FinancialError):
            transfer_terms(changed)
    too_small = {**envelope, "components": {**envelope["components"],
        "traffic_export": {**envelope["components"]["traffic_export"], "quantity": "0.000001"}}}
    with pytest.raises(FinancialError, match="financial_transfer_price_invalid"):
        transfer_terms(too_small)


def test_day_age_keeps_prelaunch_quote_review_and_live_source_hash_checks(tmp_path):
    book, plans, card, card_path = synthetic_finances(tmp_path, paid=True)
    valid_until = (NOW + timedelta(hours=30)).isoformat()
    card["valid_until"] = valid_until
    for key in ("price", "fx", "external_liabilities"):
        card[key]["valid_until"] = valid_until
    fx_path = Path(card["fx"]["path"])
    fx_source = json.loads(fx_path.read_text())
    card["fx"].update(save(fx_path, {**fx_source, "valid_until": valid_until}))
    request, plan = rewrite_card(plans, card_path, card)[0]
    authority = plan.prerequisites["paid_authorization"]
    binding = financial_binding(plan, request)
    later = NOW + timedelta(hours=25)
    with pytest.raises(FinancialError, match="financial_quote_stale"):
        validate_authority(authority, binding=binding, scope_id=book.scope_id,
            now=later, end=NOW + timedelta(hours=26))
    validate_authority(authority, binding=binding, scope_id=book.scope_id,
        now=later, end=NOW + timedelta(hours=26), quote_freshness_at=NOW)
    fx_path.write_text("changed after approval")
    with pytest.raises(FinancialError, match="financial_source_changed"):
        validate_authority(authority, binding=binding, scope_id=book.scope_id,
            now=later, end=NOW + timedelta(hours=26), quote_freshness_at=NOW)


def test_each_provider_creation_claim_needs_fresh_quote_while_earlier_launch_can_transfer(tmp_path):
    initialized, plans, card, card_path = synthetic_finances(tmp_path, paid=True)
    valid_until = (NOW + timedelta(hours=30)).isoformat()
    card["valid_until"] = valid_until
    for key in ("price", "fx", "external_liabilities"):
        card[key]["valid_until"] = valid_until
    fx_path = Path(card["fx"]["path"])
    card["fx"].update(save(fx_path, {**json.loads(fx_path.read_text()),
        "valid_until": valid_until}))
    request, plan = rewrite_card(plans, card_path, card)[0]
    utc_clock = [NOW + timedelta(hours=23, minutes=58)]
    monotonic_clock = [1.0]
    book = BudgetLedger(initialized.path, scope_id=initialized.scope_id,
        utc_now=lambda: utc_clock[0], monotonic_now=lambda: monotonic_clock[0],
        controller_session_id="SYNTHETIC-SESSION")
    reserved = book.reserve(request, plan=plan)
    assert reserved.status == "reserved", reserved.reasons
    rid = reserved.evidence.reservation_id.value
    requests, evidence = [], []
    for provider in Provider:
        name = provider.value
        run_id = plan.child_identities[name]
        trigger = reserved.evidence.expires_at.value - timedelta(
            seconds=plan.allowance.provider_leads_seconds[name])
        action = "deallocate" if provider == Provider.AZURE else "stop"
        expiry = ExpiryRequest(schema_version=2, run_id=run_id,
            deployment_id=f"{run_id}-deployment", provider=provider, action=action,
            expires_at=Evidence[datetime](value=trigger),
            reservation_id=Evidence[str](value=rid),
            resource_namespace=plan.namespaces[name], target_selector=f"target-{name}")
        requests.append(expiry)
        evidence.append(ExpiryEvidence(schema_version=2, run_id=run_id,
            deployment_id=expiry.deployment_id, provider=provider, action=action,
            state="registered", registration_id=Evidence[str](value=f"registration-{name}"),
            registered_at=Evidence[datetime](value=utc_clock[0]),
            expires_at=Evidence[datetime](value=trigger),
            target_selector=expiry.target_selector,
            provider_metadata=StructuredEvidence(value={"synthetic": True})))

    utc_clock[0] = NOW + timedelta(hours=23, minutes=59)
    monotonic_clock[0] = 61.0
    first = book.claim_creation(rid, provider=Provider.AWS,
        expiry_requests=requests, expiry_evidence=evidence)
    assert first.eligible and first.creation_granted, first.reason_codes
    utc_clock[0] = NOW + timedelta(hours=24, minutes=1)
    monotonic_clock[0] = 181.0
    restarted = BudgetLedger(book.path, scope_id=book.scope_id,
        utc_now=lambda: utc_clock[0], monotonic_now=lambda: monotonic_clock[0],
        controller_session_id="SYNTHETIC-SESSION")
    assert restarted.debit_result_transfer(rid, provider="aws", attempt_id="vm-a:1",
        maximum_payload_bytes=1)
    late = restarted.claim_creation(rid, provider=Provider.AZURE,
        expiry_requests=requests, expiry_evidence=evidence)
    assert not late.eligible and late.reason_codes == ("financial_quote_stale",)
    assert restarted.inspect()["reservations"][rid]["creation_claims"] == ["aws"]


def test_both_vm_attempts_share_exact_durable_cap_and_cleanup_survives(tmp_path):
    book, rid, _ = _paid_reservation(tmp_path)
    assert book.debit_result_transfer(rid, provider="aws", attempt_id="vm-a:1", maximum_payload_bytes=600)
    restarted = BudgetLedger(book.path, scope_id=book.scope_id, utc_now=lambda: NOW,
        monotonic_now=lambda: 1., controller_session_id="SYNTHETIC-SESSION")
    assert not restarted.debit_result_transfer(rid, provider="aws", attempt_id="vm-a:1", maximum_payload_bytes=1)
    assert not restarted.debit_result_transfer(rid, provider="aws", attempt_id="vm-a:2", maximum_payload_bytes=401)
    state = restarted.inspect()
    assert rid in state["budget_blocked"]
    assert state["reservations"][rid]["result_transfer"]["aws"]["debited_bytes"] == 600
    assert not restarted.debit_result_transfer(rid, provider="aws", attempt_id="vm-b:1", maximum_payload_bytes=1)
    for provider in Provider:
        record_absence(restarted, f"SYNTHETIC-{provider}", rid, provider, NOW)
    assert not restarted.inspect()["reservations"][rid]["cleanup_open"]


def test_partial_retry_consumes_full_declared_attempt_across_restart(tmp_path):
    book, rid, _ = _paid_reservation(tmp_path)
    assert book.debit_result_transfer(rid, provider="aws", attempt_id="vm-a:1", maximum_payload_bytes=600)
    restarted = BudgetLedger(book.path, scope_id=book.scope_id, utc_now=lambda: NOW,
        monotonic_now=lambda: 1., controller_session_id="SYNTHETIC-SESSION")
    assert restarted.debit_result_transfer(rid, provider="aws", attempt_id="vm-a:2", maximum_payload_bytes=400)
    row = restarted.inspect()["reservations"][rid]["result_transfer"]["aws"]
    assert row["debited_bytes"] == row["payload_limit_bytes"] == 1000
    assert row["attempts"] == {"vm-a:1": 600, "vm-a:2": 400}
    assert restarted.debit_result_transfer(rid, provider="azure", attempt_id="vm-a:1", maximum_payload_bytes=1)
    assert not restarted.debit_result_transfer(rid, provider="aws", attempt_id="empty:1", maximum_payload_bytes=0)
    assert not restarted.debit_result_transfer(rid, provider="aws", attempt_id="vm-b:1", maximum_payload_bytes=1)


def test_concurrent_attempts_cannot_both_cross_shared_cap(tmp_path):
    book, rid, _ = _paid_reservation(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        decisions = list(pool.map(lambda role: book.debit_result_transfer(rid,
            provider="aws", attempt_id=role, maximum_payload_bytes=600), ("vm-a:1", "vm-b:1")))
    assert sorted(decisions) == [False, True]
    row = book.inspect()["reservations"][rid]["result_transfer"]["aws"]
    assert row["debited_bytes"] == 600 and len(row["attempts"]) == 1


class _SyntheticBoundedRunner:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def run(self, request):
        assert request.action_type == "ssh"  # Paid result payload never uses SCP.
        self.calls.append(("inventory", request.action_id))
        payload = self.payload
        return result(request.action_id, f"{len(payload)} {hashlib.sha256(payload).hexdigest()}")

    def run_bounded_copy(self, request, destination, maximum_payload_bytes):
        assert "-T" in request.argv
        self.calls.append(("copy", request.action_id))
        Path(destination).write_bytes(self.payload[:maximum_payload_bytes])
        return result(request.action_id)


def test_paid_retrieval_stops_before_second_vm_inventory_at_cap(tmp_path):
    book, rid, _ = _paid_reservation(tmp_path)
    c = connection(tmp_path)
    payload = b"x" * 600
    runner = _SyntheticBoundedRunner(payload)
    def charge(item, attempt):
        return book.debit_result_transfer(rid, provider="aws",
            attempt_id=f"{item.source_role}:{attempt}", maximum_payload_bytes=item.expected_size_bytes)
    outcome = run_retrieval(c, multi_manifest(c, payload), runner, tmp_path,
        deadline=deadline(), clock=lambda: 100, max_attempts=1, transfer_admission=charge)
    assert not outcome.complete and outcome.retrieved == ["sockperf-a"]
    assert [kind for kind, _ in runner.calls] == ["inventory", "copy"]
    assert outcome.records[-1].failure.category == "result_transfer_allowance_exhausted"
    assert book.inspect()["reservations"][rid]["result_transfer"]["aws"]["debited_bytes"] == 600


def test_paid_retrieval_accepts_exact_two_vm_payload_sum(tmp_path):
    book, rid, _ = _paid_reservation(tmp_path)
    c = connection(tmp_path)
    payload = b"x" * 500
    runner = _SyntheticBoundedRunner(payload)
    def charge(item, attempt):
        return book.debit_result_transfer(rid, provider="aws",
            attempt_id=f"{item.source_role}:{attempt}", maximum_payload_bytes=item.expected_size_bytes)
    outcome = run_retrieval(c, multi_manifest(c, payload), runner, tmp_path,
        deadline=deadline(), clock=lambda: 100, max_attempts=1, transfer_admission=charge)
    assert outcome.complete and len(outcome.retrieved) == 2
    row = book.inspect()["reservations"][rid]["result_transfer"]["aws"]
    assert row["debited_bytes"] == 1000 and len(row["attempts"]) == 2


def test_interrupted_partial_copy_retains_debit_and_stops_next_vm(tmp_path):
    book, rid, _ = _paid_reservation(tmp_path)
    c = connection(tmp_path)
    payload = b"x" * 600

    class Interrupted(_SyntheticBoundedRunner):
        def run_bounded_copy(self, request, destination, maximum_payload_bytes):
            self.calls.append(("copy", request.action_id))
            Path(destination).write_bytes(payload[:4])
            now = datetime.now(timezone.utc)
            return CommandResult(action_id=request.action_id, outcome="interrupted",
                started_at=now, finished_at=now, duration_seconds=0,
                exit_code=None, stdout="", stderr="synthetic controller interruption")

    runner = Interrupted(payload)
    outcome = run_retrieval(c, multi_manifest(c, payload), runner, tmp_path,
        deadline=deadline(), clock=lambda: 100, max_attempts=1,
        transfer_admission=lambda item, attempt: book.debit_result_transfer(rid,
            provider="aws", attempt_id=f"{item.source_role}:{attempt}",
            maximum_payload_bytes=item.expected_size_bytes))
    assert not outcome.complete and [kind for kind, _ in runner.calls] == ["inventory", "copy"]
    assert outcome.attempts[0].size_bytes == 4
    assert book.inspect()["reservations"][rid]["result_transfer"]["aws"]["debited_bytes"] == 600


def test_production_bundle_wrapper_durably_charges_partial_retry(tmp_path):
    book, rid, allowance = _paid_reservation(tmp_path)
    c = connection(tmp_path)
    payload = b"native"
    journal = tmp_path / "private" / "recovery.json"
    write_recovery_journal(journal, SavedLifecycleRecovery(schema_version=1,
        attempt_id="synthetic-retrieval", controller_session_id="SYNTHETIC-SESSION",
        providers=[{"provider": "aws", "deployment_id": c.deployment_id,
            "journal_reference": "private/aws.json", "expiry_registered": True,
            "creation_may_have_started": True, "cleanup_open": True}]))

    class PartialThenComplete(_SyntheticBoundedRunner):
        def __init__(self):
            super().__init__(payload)
            self.copy_count = 0

        def run_bounded_copy(self, request, destination, maximum_payload_bytes):
            assert "-T" in request.argv
            self.copy_count += 1
            self.calls.append(("copy", request.action_id))
            Path(destination).write_bytes(b"part" if self.copy_count == 1 else payload)
            return result(request.action_id, success=self.copy_count > 1)

    runner = PartialThenComplete()
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="bounded result-transfer runner"):
        retrieve_closed_bundle_with_ledger(ledger=book, reservation_id=rid,
            recovery_path=journal, allowance=_remaining_f11_allowance(allowance, "retrieval"),
            connection=c, manifest=manifest(c, payload), runner=lambda request: result(request.action_id),
            filesystem=tmp_path, deadline=deadline(), provider_trigger=now + timedelta(minutes=5),
            retry_seconds=1, now_utc=lambda: now, clock=lambda: 100)
    assert book.inspect()["reservations"][rid]["result_transfer"]["aws"]["debited_bytes"] == 0
    outcome = retrieve_closed_bundle_with_ledger(ledger=book, reservation_id=rid,
        recovery_path=journal, allowance=_remaining_f11_allowance(allowance, "retrieval"),
        connection=c, manifest=manifest(c, payload), runner=runner, filesystem=tmp_path,
        deadline=deadline(), provider_trigger=now + timedelta(minutes=5), retry_seconds=1,
        now_utc=lambda: now, clock=lambda: 100)
    assert outcome.complete and [attempt.attempt for attempt in outcome.attempts] == [1, 2], (
        outcome.failures, [attempt.model_dump() for attempt in outcome.attempts])
    row = book.inspect()["reservations"][rid]["result_transfer"]["aws"]
    assert row["debited_bytes"] == 2 * len(payload)
    assert set(row["attempts"]) == {
        f"{c.deployment_id}:sockperf-a:1", f"{c.deployment_id}:sockperf-a:2"}
    assert runner.copy_count == 2


@pytest.mark.parametrize("emitted,marker,expected", [
    (b"abcdef", True, "succeeded"), (b"abc", True, "succeeded"),
    (b"abcdefghi", False, "failed"), (b"abcdef", False, "failed"),
])
def test_local_binary_reader_never_writes_beyond_cap(tmp_path, emitted, marker, expected):
    program = "import sys; sys.stdout.buffer.write(%r); sys.stdout.flush(); sys.stderr.write(%r)" % (
        emitted, (BOUNDED_COPY_EOF_MARKER + "\n") if marker else "remote excess\n")
    request = CommandRequest(action_id="synthetic-copy", action_type="ssh",
        argv=[sys.executable, "-c", program], cwd=str(tmp_path), timeout_seconds=3)
    destination = tmp_path / "copied.part"
    outcome = SubprocessCommandRunner().run_bounded_copy(request, destination, 6)
    assert outcome.outcome == expected
    assert destination.read_bytes() == emitted[:6]


def test_local_binary_reader_timeout_preserves_partial_evidence(tmp_path):
    program = "import sys,time; sys.stdout.buffer.write(b'x'); sys.stdout.flush(); time.sleep(2)"
    request = CommandRequest(action_id="synthetic-timeout", action_type="ssh",
        argv=[sys.executable, "-c", program], cwd=str(tmp_path), timeout_seconds=0.2)
    destination = tmp_path / "partial.part"
    outcome = SubprocessCommandRunner().run_bounded_copy(request, destination, 6)
    assert outcome.outcome == "timed_out" and destination.read_bytes() == b"x"


@pytest.mark.parametrize("size,expected_status,expected_size", [(6, 0, 6), (3, 0, 3), (9, 1, 6)])
def test_gnu_remote_stream_probes_only_one_excess_byte(tmp_path, size, expected_status, expected_size):
    gnu_dd = shutil.which("dd" if sys.platform == "linux" else "gdd")
    if gnu_dd is None:
        pytest.skip("GNU dd is required for the Ubuntu remote-stream check")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "dd").symlink_to(gnu_dd)
    source = tmp_path / "native.bin"
    source.write_bytes(b"x" * size)
    completed = subprocess.run(["sh", "-c", _bounded_stream_script(str(source), 6)],
        capture_output=True, timeout=3, check=False,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"})
    assert completed.returncode == expected_status
    assert len(completed.stdout) == expected_size
    assert (BOUNDED_COPY_EOF_MARKER.encode() in completed.stderr) == (expected_status == 0)


class _ArchivePartReceiver:
    def __init__(self, payload, *, outcome="succeeded", copied_bytes=None):
        self.payload = payload
        self.outcome = outcome
        self.copied_bytes = copied_bytes
        self.calls = []

    def run_bounded_copy(self, request, destination, maximum_payload_bytes):
        assert request.action_type == "ssh"
        assert request.action_id.startswith("f09-guest-part-")
        assert request.argv[:2] == ["ssh", "-T"]
        command = shlex.split(request.argv[-1])
        script = command[-1]
        assert command[:2] == ["sh", "-c"]
        assert f"count={maximum_payload_bytes} iflag=count_bytes" in script
        assert BOUNDED_COPY_EOF_MARKER in script
        self.calls.append((request, maximum_payload_bytes))
        count = (maximum_payload_bytes if self.copied_bytes is None else self.copied_bytes)
        Path(destination).write_bytes(self.payload[:count])
        observed = result(request.action_id, success=self.outcome == "succeeded")
        return observed.model_copy(update={"outcome": self.outcome})


def _receive_archive_part(tmp_path, runner, *, size=6, staging_limit=6, quota=16,
        remaining=30):
    from cloud_network_benchmark.lifecycle_remote import _fetch_bounded_guest_archive_part

    c = connection(tmp_path)
    staging = tmp_path / "staging"
    staging.mkdir(exist_ok=True)
    destination = staging / "one.part"
    captured_actions = []
    class Deadline:
        def remaining(self, _clock, *, cleanup=False):
            return remaining
    received = _fetch_bounded_guest_archive_part(runner=runner, connection=c,
        role="vm_a", source_path="/tmp/cnb-closed/run-vm_a/archive.tar.gz.part-00001",
        destination=destination, staging_directory=staging, size_bytes=size,
        maximum_staging_bytes=staging_limit, minimum_free_bytes=10,
        controller_quota_free_bytes=quota, deadline=Deadline(), clock=lambda: 100,
        action_id="f09-guest-part-run-vm_a-1", actions=captured_actions)
    return received, captured_actions


def test_archive_sink_part_uses_declared_size_as_actual_ssh_receiver_cap(tmp_path):
    payload = b"part-data"
    runner = _ArchivePartReceiver(payload)
    received, actions = _receive_archive_part(tmp_path, runner, size=len(payload),
        staging_limit=len(payload), quota=len(payload) + 10)
    request, cap = runner.calls[0]
    assert received.read_bytes() == payload
    assert cap == len(payload) and len(runner.calls) == 1
    assert request.timeout_seconds == 10
    assert len(actions) == 1


def test_archive_sink_bounded_transport_publishes_through_injected_gcs_backend(tmp_path):
    from cloud_network_benchmark.gcs_archive import publish_guest_archive_parts
    from cloud_network_benchmark.lifecycle_remote import _fetch_bounded_guest_archive_part
    from tests.unit.test_gcs_archive import FakeGCS

    staging = tmp_path / "staging"
    staging.mkdir()
    payloads = {"vm_a": b"vm-a-archive-part", "vm_b": b"vm-b-archive-part"}
    guest_archives, part_rows, runners = [], {}, {}
    for role, payload in payloads.items():
        digest = hashlib.sha256(payload).hexdigest()
        part = {"path": "archive.tar.gz.part-00001", "size_bytes": len(payload), "sha256": digest}
        part_rows[role] = part
        guest_archives.append({"role": role, "manifest": {
            "files": [{"path": f"{role}/native.csv", "size_bytes": len(payload), "sha256": digest}],
            "parts": [part], "raw_bytes": len(payload), "archive_bytes": len(payload),
            "archive_sha256": digest}})
        runners[role] = _ArchivePartReceiver(payload)
    c = connection(tmp_path)
    captured_actions = []

    def fetch(role, part):
        assert part == part_rows[role]
        return _fetch_bounded_guest_archive_part(runner=runners[role], connection=c,
            role=role, source_path=f"/tmp/cnb-closed/run-{role}/{part['path']}",
            destination=staging / "one.part", staging_directory=staging,
            size_bytes=part["size_bytes"], maximum_staging_bytes=4096,
            minimum_free_bytes=1024, controller_quota_free_bytes=16_384,
            deadline=deadline(), clock=lambda: 100,
            action_id=f"f09-guest-part-run-{role}-1", actions=captured_actions)

    backend = FakeGCS()
    receipt = publish_guest_archive_parts(guest_archives=guest_archives, fetch_part=fetch,
        staging_directory=staging, backend=backend, bucket="valid-bucket", provider="aws",
        experiment_id="EXP-001", run_id="run-archive-001", closed_inventory={"items": []},
        maximum_part_bytes=4096, maximum_archive_bytes=8192,
        minimum_controller_free_bytes=1024, controller_quota_free_bytes=16_384,
        deadline=deadline(), clock=lambda: 100)
    assert receipt["status"] == "verified"
    assert len(captured_actions) == 2
    assert all(len(runners[role].calls) == 1 for role in ("vm_a", "vm_b"))
    assert not list(staging.iterdir())


def test_archive_sink_source_growth_is_bounded_and_not_retried(tmp_path):
    runner = _ArchivePartReceiver(b"grown-part", outcome="failed")
    with pytest.raises(RuntimeError, match="bounded archive part retrieval failed"):
        _receive_archive_part(tmp_path, runner, size=6, staging_limit=6, quota=16)
    assert len(runner.calls) == 1
    assert runner.calls[0][1] == 6
    assert not (tmp_path / "staging" / "one.part").exists()


@pytest.mark.parametrize(("staging_limit", "quota", "remaining"), [
    (5, 16, 30), (6, 15, 30), (6, 16, 0),
])
def test_archive_sink_overflow_and_cancelled_window_fail_before_transport(
        tmp_path, staging_limit, quota, remaining):
    runner = _ArchivePartReceiver(b"part-data")
    with pytest.raises((OSError, TimeoutError)):
        _receive_archive_part(tmp_path, runner, size=6, staging_limit=staging_limit,
            quota=quota, remaining=remaining)
    assert runner.calls == []
    assert not (tmp_path / "staging" / "one.part").exists()


def test_archive_sink_interrupted_receiver_removes_partial_and_does_not_retry(tmp_path):
    runner = _ArchivePartReceiver(b"part-data", outcome="interrupted", copied_bytes=3)
    with pytest.raises(RuntimeError, match="bounded archive part retrieval failed"):
        _receive_archive_part(tmp_path, runner)
    assert len(runner.calls) == 1
    assert not (tmp_path / "staging" / "one.part").exists()
