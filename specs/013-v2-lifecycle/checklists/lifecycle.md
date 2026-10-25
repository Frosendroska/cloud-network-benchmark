# Lifecycle Requirements Quality Checklist: F09

**Purpose**: Reviewer check of written lifecycle/evidence/failure requirements, not code correctness.
**Created**: 2026-10-23
**Feature**: [spec.md](../spec.md)
**Review Ownership**: Authorized agent artifact review replaces routine human pause. Generation leaves items unchecked; a separate review marks satisfaction. `[x]` never means implementation completed.

## Completeness

- [x] CHK001 Are all four selected modes and explicit preview/run/cleanup journeys specified without an implicit next-row loop? [Completeness; Spec §FR-001/008;US1/US3]
- [x] CHK002 Are full reservation, original cap activation and all-provider expiry-before-create ordering stated? [Completeness; Spec §FR-002–004;SC-001]
- [x] CHK003 Are provider concurrency, exactly two VMs and partial-peer behavior specified? [Completeness; Spec §FR-005;US1]
- [x] CHK004 Are bootstrap/readiness and60-second stabilization required before local measurement? [Completeness; Spec §FR-006/007;SC-002]

## Clarity

- [x] CHK005 Are runtime staging identity and distinct guest/controller clock domains explained without transport-derived proof? [Clarity; Spec §FR-007;data-model Clock/deadline domains]
- [x] CHK006 Are retained CAL/DAY and fresh observation identities/resource lifetimes unambiguous? [Clarity; Spec §FR-008;SC-005]
- [x] CHK007 Are actual slot starts, unknown timing and all three skew bands explicitly defined? [Clarity; Spec §FR-009]
- [x] CHK008 Does producer closure precede inventory/copy with missing closure remaining unavailable? [Clarity; Spec §FR-010/011]
- [x] CHK009 Is copy retry limited per artifact to once within original cap/trigger minus cleanup, without a new timer? [Clarity; Spec §FR-012;SC-006]

## Consistency

- [x] CHK010 Do success and exception paths preserve final-copy/cleanup order and permit bounded failure cleanup? [Consistency; Spec §FR-010–013;contracts Required orchestration sequence]
- [x] CHK011 Are compute expiry and normal deletion/verified billable absence kept distinct for each provider? [Consistency; Spec §FR-004/013;US4]
- [x] CHK012 Are first failure, retrieval failure and cleanup failure separate across all provider outcomes? [Consistency; Spec §FR-016;SC-003]
- [x] CHK013 Are execution, data validity, eligibility, cleanup and overall lifecycle status distinguished consistently? [Consistency; Spec §FR-015;data-model State and crash boundaries]

## Acceptance criteria

- [x] CHK014 Are all acceptance criteria measurable offline without cloud/traffic claims? [Acceptance criteria; Spec §SC-001–006;FR-018]
- [x] CHK015 Is strict production-path saved-input roundtrip/replay required rather than unchecked fixture DTO construction? [Acceptance criteria; Spec §FR-014;SC-004;tasks T025]

## Scenario coverage

- [x] CHK016 Are partial registration/uncertain creation and residual safety-resource cleanup covered? [Scenario coverage; Spec §FR-004/013/017;Edge Cases]
- [x] CHK017 Are server death, missing observations, clock/diagnostic gaps and failed closes included? [Scenario coverage; Spec §FR-010/014/018;Edge Cases]
- [x] CHK018 Does abrupt-loss behavior explicitly omit finalization and preserve uncertain liabilities/expiry evidence? [Scenario coverage; Spec §FR-016–018;US4]

## Recovery coverage

- [x] CHK019 Are restart/session changes, duplicate claims and explicit cleanup reconciliation defined without automatic measurement replay? [Recovery coverage; Spec §FR-002/017;SC-006]
- [x] CHK020 Are failed-copy/hash/collision/persistence cases specified without disk retention or platform additions? [Recovery coverage; Spec §FR-011–013/016/019;Edge Cases]

## Traceability

- [x] CHK021 Are actual build/log source declarations, unique DAY records and original byte hashes required without fabricated proof? [Traceability; Spec §FR-014;data-model Identity and projection]
- [x] CHK022 Are immutable seal scope and excluded self-hashes/private state defined? [Traceability; Spec §FR-014–016;data-model Immutability and sealing]

## Dependencies

- [x] CHK023 Are latest-main dependency checks, F12 CLI ownership and shared-amendment serialization explicit? [Dependencies; Spec §plan Project Structure;tasks T026]

## Boundaries

- [x] CHK024 Are research/Thesis, paid prerequisites, ordinary engineering decisions and deferred F10/cloud acceptance clearly separated? [Boundaries; Spec §FR-002/018/019;Assumptions]

## Notes

Scope: all four modes, every provider/failure/abrupt-loss boundary, evidence integrity and serialized integration. No checklist question requires a research change.

Agent reviewer pass2026-10-23:24/24 satisfied after reading spec/plan/contracts/tasks;100% traceability. This evaluates requirements quality only.
