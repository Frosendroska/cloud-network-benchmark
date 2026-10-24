# Analysis Requirements Checklist: deterministic native reports

**Purpose**: Standard-depth pre-implementation agent review of scientific units, evidence and deterministic output requirements.
**Created**: 2026-10-23
**Feature**: [spec.md](../spec.md)

**Review Ownership**: Generated via speckit-checklist; separate authorized reviewer pass owns markers.
**Marker Semantics**: [x] approves requirements quality only, not implementation. Newly generated items are unchecked.

## Requirements quality

- [x] CHK001 Are selected input/result identity, current validation authority and changed-source behavior unambiguous? [Completeness, Spec §FR-001]
- [x] CHK002 Are receiver bytes/time, raw RTT statistics and count thresholds specified without averaging rates or quantiles? [Clarity, Spec §FR-002]
- [x] CHK003 Are DAY bin width, source-supported origins, straddling intervals, missing timing and gaps explicitly addressed? [Coverage, Spec §FR-003]
- [x] CHK004 Are retained DAY/CAL and acceptance separated from independent fresh deployments and prohibited invented baselines? [Consistency, Spec §FR-004/005]
- [x] CHK005 Are duplicate/replacement identities, independent n, median/IQR and individual-point semantics defined? [Clarity, Spec §FR-005]
- [x] CHK006 Are missing-provider cohorts and existing eligibility/frozen-N/skew rules preserved? [Consistency, Spec §FR-006]
- [x] CHK007 Are placement direction, baseline, zero-denominator and non-paired group semantics explicit? [Clarity, Spec §FR-007]
- [x] CHK008 Are actual study dates, recorded windows and concurrent-week deviations required without recurrence claims? [Completeness, Spec §FR-008]
- [x] CHK009 Are both-role diagnostic reset/gap and clock eligibility requirements stated without automatic performance labels? [Coverage, Spec §FR-009]
- [x] CHK010 Are reliability denominators and retry/replacement/missing-phase scope explicit? [Clarity, Spec §FR-010]
- [x] CHK011 Are actual/estimated/reserved costs, sources, unknowns and shared-reservation dedup defined? [Completeness, Spec §FR-010]
- [x] CHK012 Are all output formats, source links, units, empty states and offline escaped HTML required? [Completeness, Spec §FR-011]
- [x] CHK013 Is the fixed-environment deterministic byte boundary independent of descriptor order and output path? [Measurability, Spec §FR-012]
- [x] CHK014 Are raw immutability, unsafe/output-overlap refusal and failure publication boundaries explicit? [Coverage, Spec §FR-012]
- [x] CHK015 Are known numeric oracles, materialized replay, visual review and passed-versus-unrun evidence defined? [Measurability, Spec §FR-013, §SC-001/003/006]
- [x] CHK016 Are dependency versions, owned files and read-only F08/shared/Thesis boundaries bounded without a new platform or reusable skill? [Dependencies, Spec §FR-014, Plan §Interfaces, Research §Decision5]

## Notes

Created 16 unchecked items. All trace to the spec/design. Implementer reads this gate without changing its review markers.

Separate authorized agent review on 2026-10-23: 16/16 requirements-quality criteria satisfied. Clarified output overlap wording and explicit UTC-subwindow/date/weekday coverage before approval. These markers do not claim implementation or renderer acceptance.
