# GCP safety requirements checklist: F07

**Purpose**: Requirements-quality gate for expiry, trust, cost and cleanup.
**Created**: 2026-10-22
**Feature**: [spec.md](../spec.md)
**Review Ownership**: Reviewer-owned. Generate unchecked; agent artifact review is authorized by WORKFLOW.md. `[x]` means requirements quality is satisfied, never implementation complete. The implement skill reads but does not modify these markers.

## Completeness and clarity

- [x] CHK001 Are exactly two selected GCP VMs, disks, image architecture, NIC and network settings unambiguous? [Completeness, Spec FR-001–FR-005]
- [x] CHK002 Are S1–S4 locality and compact-without-distance semantics explicit without provider-equivalence claims? [Clarity, Spec FR-003]
- [x] CHK003 Is pre-create registration distinguished from a locally prepared or creation-time-only policy? [Clarity, Spec FR-007; Research §1]
- [x] CHK004 Are original cap activation, both registrations, creation ordering and complete workflow fit specified? [Completeness, Spec FR-006–FR-008]
- [x] CHK005 Are the earlier actual trigger and initiation-versus-completion/billing limitation explicit? [Clarity, Spec FR-008; Research §2]
- [x] CHK006 Are dated support-cost source/hash, zero-charge rationale and other reserved billable resources distinguished? [Completeness, Spec FR-006; Research §6]

## Consistency and dependencies

- [x] CHK007 Is the existing shared DELETE mismatch and mandatory STOP migration declared without silent override? [Consistency, Spec Assumptions; Plan Summary]
- [x] CHK008 Are provider-owned APIs/files frozen without extending shared DTOs or consuming unmerged siblings? [Consistency, Plan Interfaces and ownership]
- [x] CHK009 Are template source binding and independent observed VM scheduling defined without fictitious response fields? [Clarity, Research §1; Contract Terraform API]
- [x] CHK010 Are S3 policy-slot use and S4 global VPC/subnet routing consistent with the chosen expiry? [Consistency, Spec FR-003–FR-004; Research §4]

## Exception, recovery and security coverage

- [x] CHK011 Are failed/partial/unknown/replaced registrations and delayed setup defined to prevent creation? [Coverage, Spec FR-006–FR-008; Edge Cases]
- [x] CHK012 Are authenticated project/zone/numeric VM identity and guest host-key limitations explicit? [Security, Spec FR-005, FR-009; Research §5]
- [x] CHK013 Are requested guest settings separated from actual image/NIC/CPU/physical-zone observations? [Clarity, Spec FR-002, FR-009]
- [x] CHK014 Are cleanup and cancellation requirements clear when a live VM has already inherited expiry? [Recovery, Spec FR-008, FR-010–FR-011]
- [x] CHK015 Are residual disks/addresses, denied inventory, unknown operations and unfinished pagination launch blockers? [Coverage, Spec FR-010–FR-011]
- [x] CHK016 Are partial creation, repeated cleanup, concurrent namespace isolation and foreign ownership covered? [Coverage, Spec FR-001, FR-010–FR-011]
- [x] CHK017 Are failed-copy handling boundaries explicit without an invented retention/deletion timer? [Recovery, Spec FR-011]
- [x] CHK018 Are secret exclusion, private state and replaceable bounded commands specified? [Security, Spec FR-012]

## Acceptance quality

- [x] CHK019 Are static/fake acceptance cases measurable for all success/failure stories? [Measurability, Spec SC-001–SC-005]
- [x] CHK020 Are unrun paid IAM/expiry/network/billing/cleanup checks explicitly separate from offline evidence? [Clarity, Spec FR-013; Quickstart]

## Notes

Standard depth, planner/reviewer gate; focus is lifecycle/security/spend requirement quality. Shared STOP prerequisite remains pending and is not waived by checked markers. Review these written requirements against spec/plan/research/contracts before marking satisfied. No additional human review pause is required by the authorized workflow.

Planner artifact review completed 2026-10-22: 20/20 written requirement-quality criteria satisfied. These markers do not authorize application implementation before the shared prerequisite or claim any GCP behavior.
