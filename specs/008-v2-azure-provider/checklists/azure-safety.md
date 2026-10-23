# Azure Safety Requirements Checklist: F06

**Purpose**: Review requirement quality for expiry, identity, costs and cleanup before implementation.
**Created**: 2026-10-22
**Feature**: [spec.md](../spec.md)

**Review Ownership**: Reviewer-owned requirements-quality artifact. `[x]` means reviewed and satisfied, not implemented. The authorized planner performs this review; implement reads markers and does not change them.

## Completeness and clarity

- [x] CHK001 Are exactly-two-VM, selected image/shape/disk/acceleration and all four localities stated without silent substitutions? [Completeness, Spec §FR-002–FR-004]
- [x] CHK002 Are logical-zone and maximum-network-limit meanings distinguished from physical equivalence or measured guarantees? [Clarity, Spec §FR-003/FR-007]
- [x] CHK003 Are controller /32 management, A-only private listeners and broad-default ingress denial explicit? [Clarity, Spec §FR-004]
- [x] CHK004 Is host identity bound to authenticated provider evidence before first management use, with private references and failure behavior stated? [Completeness, Spec §FR-005]

## Consistency and scenario coverage

- [x] CHK005 Is independent deallocation registered before VM creation, with actual executable/schedule/target/permission evidence required rather than a DTO label? [Consistency, Spec §FR-008]
- [x] CHK006 Is complete workflow lifetime/cost coverage stated from reservation activation through setup, registration, measurement, final copy and cleanup? [Completeness, Spec §FR-009]
- [x] CHK007 Are trigger lead/rounding, earlier-trigger fit, runtime/permission uncertainty and dated expiry price inputs resolved without cap extensions or free-tier assumptions? [Clarity, Spec §FR-009/Assumptions; Research Decisions 2–4]
- [x] CHK008 Are controller loss, asynchronous/late deallocation, initial absence/late creation, partial A/B failures and finite retries included without a hard timing/billing guarantee? [Coverage, Spec §FR-010/Edge Cases]

## Failure and dependency boundaries

- [x] CHK009 Is full deletion distinct from Azure deallocation, including unknown/residual disks/addresses/expiry resources and blocking future admission? [Consistency, Spec §FR-011]
- [x] CHK010 Are cancellation order, preparatory/partial creation, lost state, foreign ownership and interruption requirements complete and traceable? [Coverage, Spec §FR-006/FR-011; Contract Cleanup]
- [x] CHK011 Are independent concurrent attempts and unchanged shared interfaces explicit, with no unfinished sibling runtime dependency? [Dependency, Spec §FR-001/FR-006/Assumptions]
- [x] CHK012 Are credential-free/static/fake evidence limits, exact acceptance ownership and separate paid checks explicit without changing N/windows/research admission? [Measurability, Spec §FR-012/SC-001–SC-005/Assumptions]

## Notes

Standard depth for planner/implementer/converger handoff. Focus: expiry safety and Azure-specific identity/cleanup. Every item references a requirement or linked design decision. Mark only after review; no implementation check is implied.
