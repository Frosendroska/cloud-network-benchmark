# Native Evidence Requirements Checklist: F08

**Purpose**: Review completeness and consistency of native evidence, timing and consumer contracts before implementation
**Created**: 2026-10-21
**Feature**: [spec.md](../spec.md)
**Review Ownership**: Reviewer-owned requirements-quality artifact. Agent review is authorized by the autonomous workflow.
**Marker Semantics**: Checked means requirements quality satisfied, not application implementation complete.

## Requirement Completeness

- [x] CHK001 Are all four modes, required artifact roles and unsupported legacy input requirements defined? [Completeness, Spec §FR-001/005]
- [x] CHK002 Are receiver bytes/time, omitted intervals and sender-only fallback restrictions explicit? [Clarity, Spec §FR-002]
- [x] CHK003 Are quantile convention, sample gates, units, variability and inflation availability stated? [Measurability, Spec §FR-003]
- [x] CHK004 Are reported counts/generation scope and the prohibition on row-derived loss distinguished? [Consistency, Spec §FR-004]

## Timing and Status Clarity

- [x] CHK005 Are full duration, idle, loaded and day coverage requirements numerically defined? [Clarity, Spec §FR-007]
- [x] CHK006 Are actual acquisition evidence, gaps, filtering and missing-window outcomes distinguished from process launch? [Coverage, Spec §FR-006/007]
- [x] CHK007 Are exact 60/300-second skew boundaries and unknown timestamps specified consistently? [Clarity, Spec §FR-009]
- [x] CHK008 Is the 100-ms one-second alignment criterion separated from single-host RTT validity? [Consistency, Spec §FR-009]
- [x] CHK009 Are execution, data/comparison statuses, cleanup acceptance and trusted partial intervals separately defined? [Completeness, Spec §FR-008; Data model §Findings and reduction]

## Scenario and Contract Coverage

- [x] CHK010 Are corruption, truncation, resets, duplicate sessions, missing counters and integrity/path failures covered? [Coverage, Spec §Edge Cases; Contract §Supported native grammar]
- [x] CHK011 Are complete and incomplete calibration, fixed order, smallest common count and fallback outcomes specified? [Completeness, Spec §FR-010]
- [x] CHK012 Are source IDs/hashes, deterministic evaluation time and raw immutability requirements explicit? [Traceability, Spec §FR-005/011/012]
- [x] CHK013 Are F09/F12 public inputs/outputs and exclusive file ownership declared without an unfinished sibling dependency? [Dependencies, Plan §Project Structure; Contract §Signatures]
- [x] CHK014 Are numerical engineering assumptions distinguished from pending research N/UTC windows and advisor approval? [Assumption, Spec §Clarifications/Assumptions]
- [x] CHK015 Are slow/partial evidence retention and excluded performance-warning/cloud/plotting work clearly bounded? [Consistency, Spec §FR-012]
- [x] CHK016 Are measurable offline acceptance scenarios present for every user story and numerical boundary? [Acceptance Criteria, Spec §SC-001–005; Quickstart §Implementation acceptance]

## Notes

Generated as 16 unchecked requirements-quality items. A separate authorized artifact review records evaluation before implementation; implement must read the resulting gate and must not edit markers. Built-in requirements.md has its separate specify/clarify lifecycle.

Authorized planner review on 2026-10-21: CHK001–CHK016 satisfied against the referenced spec/plan/contract sections after remediation. This is a separate evaluation of the originally unchecked generated checklist, not implementation completion.
