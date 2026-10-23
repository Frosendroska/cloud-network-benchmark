# AWS safety requirements checklist

**Purpose**: Reviewer requirements-quality gate for F05 before implementation.
**Created**: 2026-10-22
**Feature**: [spec.md](../spec.md)
**Review ownership**: Agent artifact review under authorized WORKFLOW.md. Checked means requirement quality satisfied, not code implemented.

## Completeness and clarity

- [x] CHK001 Are the exact two-VM shape, image, disk, acceleration and no-substitution requirements specified? [Completeness, FR-002]
- [x] CHK002 Are all four topology/zone/CIDR and private peering requirements explicit? [Clarity, FR-003–004]
- [x] CHK003 Are management /32 and A-only private listener trust boundaries stated? [Clarity, FR-005–006]
- [x] CHK004 Does registration clearly precede VM creation in every region and follow reservation activation? [Consistency, FR-009]
- [x] CHK005 Are unknown IDs, paginated discovery, delayed tags and in-flight creation addressed without overstated guarantees? [Coverage, FR-010; research.md]

## Failure and acceptance coverage

- [x] CHK006 Are all documented registration fields/status predicates and uncertainty outcomes measurable? [Measurability, contracts/aws-provider.md]
- [x] CHK007 Are stop versus observed stopped state versus complete deletion explicitly distinct? [Consistency, FR-010–011]
- [x] CHK008 Are cancellation races, partial creation, denied inventory and multi-region leftovers defined? [Coverage, FR-011]
- [x] CHK009 Are upfront IAM roles, separate charged expiry allowance and source/date specified? [Completeness, research.md]
- [x] CHK010 Are frozen interfaces, private settings and no unfinished-sibling dependency explicit? [Dependencies, plan.md]
- [x] CHK011 Are offline checks and later paid proofs distinguished with exact intended commands? [Acceptance quality, quickstart.md]
- [x] CHK012 Are all assumptions engineering choices within source bounds, with no cap increase, extra copying timer or research alteration? [Consistency, spec.md; research.md]

## Notes

Generated items began unchecked. Planner separately reviewed all 12 criteria against the linked requirements/design on 2026-10-22: 12/12 pass. Implementation must not interpret markers as code completion.
