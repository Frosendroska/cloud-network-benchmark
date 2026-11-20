# Tasks: Bounded paired WEEK dispatch

**Input**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/campaign-dispatch.md`.

## Phase 1: Contract and policy

- [x] T001 Require and freeze the source-hashed two-member WEEK concurrency admission in `campaign_inputs.py` and the operator example (FR-001, FR-003).
- [x] T002 Validate policy evidence and exact S1/P0 + S3/P1 slot membership in `campaign.py`; keep acceptance serial (FR-002, FR-005).
- [x] T003 Block later slots after miss/failure and prevent restart singleton dispatch (FR-004).
- [x] T004 Document the new WEEK evidence contract and unchanged DAY/acceptance boundaries (FR-005, FR-006).

## Phase 2: Focused qualification

- [x] T005 Qualify paired WEEK progress through concurrent fake calls making real locked ledger reservations; prove 56 entries, two active reservations, unique namespaces and no duplicate replay (SC-001).
- [x] T006 Cover incomplete/duplicate pairs, missed slots, partial failure and restart singleton/ambiguous-state handling (SC-002).
- [x] T007 Preserve acceptance serialization and active-stage locking (SC-003).
- [x] T008 Run the focused affected campaign and native DAY checks; record exact outcomes and distinguish fake/native evidence from provider acceptance (SC-004).
- [ ] T009 Integrate the final reviewed Azure delta once available and rerun only checks affected by that source change.
