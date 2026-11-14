# Feature Specification: EUR budget and personal cash safeguards

**Feature Branch**: `codex/eur-budget-cash-guard`
**Created**: 2026-11-10
**Status**: Reviewed for implementation
**Input**: Authorized offline audit/repair packet in Thesis tracking/IMPLEMENTATION_PROMPTS.md.

## User Scenarios & Testing

### User Story 1 - Refuse unauthorized spending (Priority: P1)
An operator receives a specific rejection before any reservation or provider call if the exact run
has no current, source-backed human authorization with explicit ceilings and resource scope.
**Independent Test**: Real admission and a factory that raises if invoked; invalid cards leave bytes unchanged.
**Acceptance Scenarios**:
1. Given missing, expired, wrong-scope or changed-source authority, attempting launch causes zero calls.
2. Given current approval but stale/missing price or FX evidence, creation is refused.
3. Given unsupported credit assumptions or insufficient cash ceiling, creation is refused.

### User Story 2 - Preserve shared gross and cash commitments (Priority: P1)
The operator can inspect conservative EUR commitments shared by P0/P1; restart and cleanup do not
manufacture headroom. Recommendations are never default authority.
**Independent Test**: Concurrent synthetic reservations in one ledger, reload/reconcile, boundary checks.
**Acceptance Scenarios**:
1. Given individually affordable runs that exceed a shared ceiling together, at most the affordable set reserves.
2. Given outstanding or legacy unconverted liabilities, new paid work is refused without resetting history.
3. Given final billing or cleanup, recorded maximum financial exposure remains conservative.

### User Story 3 - Recover safely and see limitations (Priority: P2)
The operator can clean up original owned resources despite stale quotes or expired approval, and can
see whether a cumulative transfer cap exists before trusting the budget forecast.
**Independent Test**: Existing recovery path and real admission rejection; deterministic renderer check.
**Acceptance Scenarios**:
1. Given expired launch authority, original scoped cleanup remains available, provisioning does not.
2. Given an SCP inventory/retry allowance, it is not called an enforced byte cap; paid dispatch blocks.
3. The hero states EUR300 all-in and labels EUR250/EUR50 as non-authorizing recommendations.

### Edge Cases
Concurrent P0/P1, duplicate run IDs, changed approval bytes, clock rollback/future quotes, cent rounding,
zero cash limit, retained unknown billing, open cleanup, old ledger without EUR history, retry bytes.

## Requirements

### Functional Requirements
- **FR-001**: Validate source-backed typed human authorization and exact config/runtime/price/code/run/provider/resource/count scope before reservation or provider calls; recheck before new work.
- **FR-002**: Enforce timezone-aware approval bounds and official-price/FX source checks within24h, through the planned cleanup deadline; dates or refresh flags alone cannot authorize.
- **FR-003**: Enforce EUR300 all-project gross ceiling, explicit working/protected/provider/stage/run/batch and personal cash caps, rounding liabilities upward and caps downward to cents; retain stricter USD controls.
- **FR-004**: Include external thesis costs, tax/FX/card fees, incurred/unbilled/active and remaining funded work atomically in a shared ledger; reject old unconverted history, never clear it to permit launch.
- **FR-005**: Track cash separately. This bounded version conservatively counts full gross as cash, rejects credit/reimbursement offsets and retains maximum exposure after reconciliation. No credit or promised reimbursement grants authority.
- **FR-006**: Preserve original scoped cleanup/reconciliation without fresh creation approval, quotes or cap headroom.
- **FR-007**: Report absent cumulative SCP byte enforcement and block paid dispatch until that capability is implemented; no attestation flag can enable it.
- **FR-008**: Correct only the requested renderer caption/date and preserve unrelated edits; create no live approval, live ledger, account changes or provider calls.

### Key Entities
Human authorization card; exact run binding; checked official price/FX evidence; EUR/cash commitment;
existing shared ledger and scoped recovery; explicit transfer limitation.

## Success Criteria
- **SC-001**: Every specified invalid admission case produces zero reservations and zero provider/factory calls.
- **SC-002**: Concurrent and restart tests preserve commitments and prevent aggregate overrun, including rounding boundaries.
- **SC-003**: Cleanup tests remain passing and renderer regenerates deterministically; appropriate affected/native checks recorded.

## Assumptions
The manually supplied card source is the human trust boundary, not a cryptographic signature or bank
spending cap. No real cash cap/FX/credit balance is invented. No credit offsets or automatic financial
headroom release are supported in this bounded repair. Cumulative network-byte enforcement is a
reported live blocker, not a transport redesign. Research protocol and provider settings are unchanged.

## Clarifications - 2026-11-10
No policy ambiguity requiring user input. Missing actual cash ceiling and launch approval are deliberate
blocked prerequisites. Full-cash accounting and retained maxima are conservative engineering choices.
The packet explicitly permits reporting the transport limitation; this feature additionally fails closed.
All scope/data/failure/terminology categories reviewed; UI/accessibility and scale services not applicable.
