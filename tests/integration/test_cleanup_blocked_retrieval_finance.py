"""Regression for bounded retrieval from an existing reservation during sibling cleanup."""
from dataclasses import replace

from cloud_network_benchmark.contracts.common import Provider
from tests.financial_helpers import NOW, synthetic_finances
from tests.integration.test_f11_ledger import record_absence


def test_sibling_cleanup_blocks_new_work_but_not_existing_bounded_retrieval(tmp_path):
    book, plans, _, _ = synthetic_finances(tmp_path, count=2, paid=True)
    reservations = []
    for request, plan in plans:
        decision = book.reserve(request, plan=plan)
        assert decision.status == "reserved", decision.reasons
        rid = decision.evidence.reservation_id.value
        reservations.append((rid, plan))
    blocked_rid, _ = reservations[0]
    retrieval_rid, retrieval_plan = reservations[1]
    record_absence(book, "SYNTHETIC-sibling-partial-cleanup", blocked_rid,
        Provider.AWS, NOW)
    state = book.inspect()
    assert blocked_rid in state["cleanup_blocked"]

    # Existing retrieval passes the real bounded debit path while strict
    # registration/measurement revalidation still rejects the global hold.
    assert book.debit_result_transfer(retrieval_rid, provider="aws",
        attempt_id="vm-a:1", maximum_payload_bytes=1)
    registration = book.check_stage_fit(retrieval_rid, stage="registration",
        remaining=retrieval_plan.allowance)
    assert not registration.eligible
    assert "financial_outstanding_liability" in registration.reason_codes

    allowance = retrieval_plan.allowance
    retrieval = replace(allowance, registration_seconds=0, provision_seconds=0,
        readiness_seconds=0, stabilization_seconds=0, measurement_seconds=0,
        measurement_shutdown_seconds=0,
        completed_stages=("registration", "preparation", "measurement"))
    decision = book.check_stage_fit(retrieval_rid, stage="retrieval", remaining=retrieval)
    assert "financial_outstanding_liability" not in decision.reason_codes
    assert decision.eligible, decision.reason_codes
    assert not state.get("budget_blocked")
