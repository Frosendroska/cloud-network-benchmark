# Remote Safety Checklist: F02R V2 remote preparation and native retrieval

**Purpose**: Requirements-quality gate for remote contracts, deadlines and final-copy lifecycle
**Created**: 2026-10-21
**Feature**: [spec.md](../spec.md)

**Review Ownership**: Planner reviews requirements under the authorized autonomous workflow. `[x]` means requirements quality is satisfied; it does not mean implemented behavior passed. The implement skill reads markers and does not edit them.

## Requirement Completeness

- [x] CHK001 Are complete-pair, deployment/role identity and frozen F01R migration boundaries specified? [Completeness, Spec §FR-001]
- [x] CHK002 Are source digest verification, observed compiler/binary identity and required capabilities explicit for both VMs? [Completeness, Spec §FR-003–004]
- [x] CHK003 Are private listener bindings, A-only ingress, lifetime, repeat preparation and log preservation specified? [Completeness, Spec §FR-005]
- [x] CHK004 Are measurement-end and producer closure prerequisites explicit for both-VM retrieval? [Completeness, Spec §FR-008]

## Requirement Clarity and Consistency

- [x] CHK005 Is strict authenticated SSH/SCP identity defined consistently with safe remote argument interpretation and durable redaction? [Clarity, Spec §FR-002; Contract §SSH/SCP transport]
- [x] CHK006 Does one original readiness deadline bound all required checks/polls without allowing readiness to mutate services? [Consistency, Spec §FR-006–007]
- [x] CHK007 Is cap activation before expiry registration and creation explicit without transferring reservation/provider ownership into F02R? [Consistency, Spec §FR-012]
- [x] CHK008 Is the distinction between preparation observations and proof of actual UDP/provider behavior explicit? [Clarity, Spec §FR-006]

## Acceptance Criteria Quality

- [x] CHK009 Are success criteria measurable through exact commands, cumulative fake time and physically materialized bytes? [Measurability, Spec §SC-001–004]
- [x] CHK010 Are mandatory remote size/hash comparisons and stdout-only copy rejection specified? [Clarity, Spec §FR-009]
- [x] CHK011 Are zero-byte transfer integrity and semantic data-validity responsibilities distinguished? [Clarity, Spec §FR-009]

## Scenario and Edge Case Coverage

- [x] CHK012 Are corrupt/missing/partial artifacts, optional absence, unsafe paths, symlinks, collisions and immutable evidence covered? [Coverage, Spec §Edge Cases; §FR-009–010]
- [x] CHK013 Are maximum copy attempts, original remaining cap, cleanup allowance and interruption behavior specified without a new timer or automatic retention? [Coverage, Spec §FR-011; Research R6]
- [x] CHK014 Are primary failure and later retrieval/cleanup failures kept distinguishable? [Coverage, Spec §FR-010–011]
- [x] CHK015 Are open diagnostic chunks excluded from off-VM copying until overall measurement end? [Coverage, Spec §FR-008]

## Dependencies and Assumptions

- [x] CHK016 Are implementation/test paths, interface ownership and no-unfinished-sibling dependencies explicit? [Dependency, Plan §Phases and ownership]
- [x] CHK017 Are offline fake evidence and unrun real compilation/traffic/provider acceptance clearly distinguished? [Clarity, Spec §FR-013; Quickstart §Limits]
- [x] CHK018 Are cap/copy choices labeled engineering assumptions while research N/windows/prices remain evidence-dependent? [Assumption, Spec §Assumptions; Research R4/R6]

## Notes

Created by checklist stage as unchecked questions; separate planner review records their quality status. Focus: strict transport/build evidence, one deadline and closed final-copy safety. Depth: standard with focused edge cases; audience: next implementer/converger, before implementation. All 18 items carry traceability references. No routine clarification question was needed because the assignment specified scope, audience, risk focus and offline gate.

Planner requirements review: 18/18 passed on 2026-10-21 after reading spec, plan, research, data model, contract and tasks. No unresolved requirement-quality issue. This separate review toggles only planner-owned quality markers; all 24 implementation tasks remain unchecked.
