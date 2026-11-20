# Feature Specification: Bounded paired WEEK dispatch

**Feature Branch**: `codex/week-slot-concurrency-20261120`
**Status**: Offline implementation; provider acceptance excluded

## User Scenarios & Testing

### User Story 1 - Admit and run one aligned S1/S3 slot (P1)

An operator with reviewed weekly-concurrency evidence can prepare the existing P0/S1 and P1/S3 WEEK stages. Each of 28 reviewed UTC slots dispatches exactly two independent all-cloud lifecycle calls through one campaign service, one shared ledger and isolated per-entry state/results.

**Independent Test**: Use a source-hashed test admission, the production WEEK dispatch branch, two concurrent fake lifecycle calls that both make real in-memory ledger reservations, and a full 56-entry fake-clock traversal.

**Acceptance Scenarios**:
1. Given explicit `paired_s1_s3` approval capped at two, when a valid slot arrives, then exactly one P0/S1 and one P1/S3 call both enter real ledger admission concurrently.
2. Given duplicate, incomplete, mismatched, or changed policy evidence, when the stage starts or resumes, then no singleton/over-cap dispatch occurs.
3. Given a missed slot or a failed weekly call, when the slot settles, then later slots are recorded missed and never dispatched.
4. Given a restart with ambiguous or partially terminal slot state, when reconciliation runs, then it never relaunches an attempted member or starts only the remaining member.

### User Story 2 - Preserve serialized acceptance and lifecycle boundaries (P1)

Acceptance baseline and supervised failure entries retain their existing serial workflow, genuine review, source provenance, original deadlines, trust, expiry, cleanup and one-service lock.

**Independent Test**: Assert acceptance concurrency peak is one, supervised entries remain serial, and a second driver cannot take the active campaign lock.

## Requirements

- **FR-001**: WEEK parallel dispatch requires an explicit, source-hashed `weekly_concurrency` admission naming `mode: paired_s1_s3` and `max_parallel_entries: 2`.
- **FR-002**: Each weekly launch time contains exactly one P0/S1 and one P1/S3 entry with the same scheduled window and unique identities.
- **FR-003**: The production WEEK path dispatches at most those two calls concurrently through the existing single campaign service and shared ledger; per-entry run, state, Terraform, result and placement namespaces remain isolated.
- **FR-004**: Both members of an admitted slot settle. A failed or missed member blocks later slots even when cleanup is verified; ambiguous/restart state never triggers a repeat or singleton launch.
- **FR-005**: Acceptance, including registered supervised failure entries, stays serial. Existing locks, genuine plan reviews, trust, expiry, cleanup, deadlines and failure evidence remain intact.
- **FR-006**: DAY remains the research 86,400-second protocol. Reuse existing DAY/native coverage; add no short DAY mode, pilot, or unrelated refactor.
- **FR-007**: Offline fakes and local/native checks are labeled accurately and do not establish provider acceptance.

## Success Criteria

- **SC-001**: All 28 WEEK slots have exactly two admitted independent entries; a full fake traversal launches 56 calls and demonstrates a peak of two shared-ledger reservations.
- **SC-002**: Malformed admission/pairs, missed slots, partial failures and restart contradictions fail closed without later or duplicate dispatch.
- **SC-003**: Acceptance concurrency remains one and the active-stage lock remains exclusive.
- **SC-004**: DAY protocol and native evidence contracts remain unchanged; no cloud calls, credential use, live ledger writes or paid launches occur.

## Assumptions

- An operator's weekly concurrency source is reviewed evidence; confirmation itself supplies no paid authorization.
- Existing provider, budget, acceptance, schedule, expiry and cleanup gates remain authoritative.
- The focused validation is synthetic/offline; real provider acceptance and the full DAY remain separate.
