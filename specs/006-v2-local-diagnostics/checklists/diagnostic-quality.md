# Diagnostics Requirements Checklist: F04 V2 local diagnostics

**Purpose**: Review counter semantics, clock eligibility, provenance and lifecycle requirement quality before implementation.
**Created**: 2026-10-22
**Feature**: [spec.md](../spec.md)
**Review Ownership**: Reviewer-owned requirements-quality artifact; under the authorized autonomous workflow, the planner evaluates it in a separate artifact check.
**Marker Semantics**: A checked item means requirements quality is satisfied, never implementation completion. Generated items begin unchecked; implement reads and must not alter markers.

## Completeness and clarity

- [x] CHK001 Are both endpoint roles, all four modes and caller-controlled collection boundaries explicit? [Completeness, Spec FR-001/014]
- [x] CHK002 Are active CPU, steal, aggregate/core denominator and guest accounting definitions unambiguous? [Clarity, Spec FR-002, data-model CPU]
- [x] CHK003 Are TCP/NIC scope, direction, count/byte units and true-zero semantics defined? [Clarity, Spec FR-003/004]
- [x] CHK004 Are baseline, missing/decreasing counters, core/interface changes and recovery requirements specified without fabricated values? [Coverage, Spec FR-004/005]
- [x] CHK005 Are actual intervals, skipped ticks and absence of catch-up/backfill explicit? [Measurability, Spec FR-005, SC-002]
- [x] CHK006 Are all static fields tied to observed versus supplied evidence with mismatches and unknowns retained? [Completeness, Spec FR-006/007]

## Clock and integration consistency

- [x] CHK007 Are clock source/error observations, estimate units, capture uncertainty and unavailable fallback explicit? [Clarity, Spec FR-008, data-model ClockMapping]
- [x] CHK008 Is the <=100-ms condition defined for both roles separately from interval coverage/data validity? [Consistency, Spec FR-009]
- [x] CHK009 Are UTC changes and local monotonic deadlines treated consistently without claiming synchronization from mappings alone? [Coverage, Spec FR-005/008]
- [x] CHK010 Are canonical and additive clock artifact IDs/paths/producers declared without changing shared schemas? [Consistency, Spec FR-010, contract Artifacts]
- [x] CHK011 Are F09 staging/paired lifecycle/retrieval/checksum and F08/F12 interval-consumption responsibilities explicit? [Dependencies, Spec FR-014, plan Integration]

## Failure and scope boundaries

- [x] CHK012 Are exclusive outputs, partial writes, interruption, closure and abrupt-loss limitations specified? [Coverage, Spec FR-011, data-model lifecycle]
- [x] CHK013 Are offline fixture/fake-clock acceptance and real-cloud limitations separated? [Measurability, Spec FR-012, SC-005]
- [x] CHK014 Are performance warnings, required ENA counters, CloudWatch, remote copying and speculative platforms excluded consistently? [Consistency, Spec FR-013/014]
- [x] CHK015 Are source precedence, engineering assumptions and remaining evidence-dependent research choices explicit? [Assumption, Spec Assumptions, research Source reconciliation]

## Notes

Generated as 15 unchecked requirements-quality items; separately evaluated by the planner under the authorized artifact-review workflow on 2026-10-22: 15/15 satisfied, zero actionable findings. This is not a code test checklist. No pre/post extension hooks are registered. `$speckit-implement` does not change review markers.
