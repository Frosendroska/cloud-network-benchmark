# Tasks: EUR budget and personal cash safeguards

## Phase 1: Setup
- [X] T001 Record unchanged baseline negative reproductions and call-chain audit in specs/019-eur-budget-cash-guard/research.md (FR-001/007).
- [X] T002 Amend .specify/memory/constitution.md for dated EUR/cash policy and review spec/plan/checklist (FR-003/008).

## Phase 2: Foundation
- [X] T003 Add strict typed card/source/FX/binding and cent arithmetic tests in tests/unit/test_financial_controls.py before implementation (FR-001/002/003/005).
- [X] T004 Implement src/cloud_network_benchmark/financial_controls.py with no network I/O or live fixtures (FR-001/002/003/005).

## Phase 3: User Story 1 - Admission barrier
- [X] T005 [US1] Add real zero-reservation/factory-call negative matrix in tests/integration/test_financial_admission.py (SC-001).
- [X] T006 [US1] Bind runtime/config/code/source/resource identities and forbid paid auto-initialization in lifecycle_runtime.py (FR-001/002).
- [X] T007 [US1] Enforce financial and transport validation before reserve/provider identity reads in admission.py and campaign_inputs.py (FR-001/007).

## Phase 4: User Story 2 - Shared commitments
- [X] T008 [US2] Test rounding/concurrent P0/P1/reload/legacy/reconciliation cases in tests/integration/test_financial_admission.py (SC-002).
- [X] T009 [US2] Persist conservative EUR/cash commitments inside the existing admission.py lock, validate snapshots and recheck noncleanup stages/creation claims (FR-003/004/005).

## Phase 5: User Story 3 - Recovery and presentation
- [X] T010 [US3] Verify original cleanup remains available; explicitly isolate new guard in historical fake-stage fixtures without bypass switches in production (FR-006).
- [X] T011 [US3] Correct renderer caption/date only, regenerate HTML and --check; preserve unstaged pre-existing edits (FR-008).

## Phase 6: Validation and delivery
- [X] T012 Run affected/full credential-free and native checks if existing SSH works; record evidence/control matrix/prerequisites in docs/validation/eur-financial-guard.md (SC-001/002/003).
- [X] T013 Converge feature against spec/plan/tasks and selectively commit financial changes with no private state or unrelated runbook edits (FR-008).

## Dependencies and implementation strategy
T001/T002 -> T003/T004 -> T005/T006/T007 -> T008/T009 -> T010/T011 -> T012/T013.
US1 is the first useful barrier; US2 relies on its typed records; US3 preserves independent recovery.
One writer; no implementation parallelism. Independent arithmetic/source negative test cases can run
in parallel, but ledger mutation/reconciliation tests retain explicit process synchronization.
