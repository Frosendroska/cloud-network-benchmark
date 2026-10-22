# Implementation Plan: Honest command clock evidence

**Branch**: `codex/f01r-clock-evidence` | **Date**: 2026-10-22 | **Spec**: [spec.md](spec.md)

**Input**: `specs/015-command-clock-evidence/spec.md`; merged F01R base `cef838e42fc620f90b5943f5a8d1528ddc0b0724`.

## Summary

Extend existing command models with explicit monotonic duration evidence while preserving actual UTC endpoints. Keep UTC-only mode strict. Capture real `time.monotonic()` bounds in the synchronous runner, carry signed UTC-minus-monotonic discrepancy through redacted native DTOs, regenerate only affected active schemas, and prove the contract with controlled offline clocks and regression tests.

## Technical Context

**Language/Version**: Existing Python >=3.9 project; `/private/tmp/cnb-offline-venv/bin/python`.

**Primary Dependencies**: Existing Pydantic 2, datetime/time/subprocess; no added dependency.

**Storage**: Existing JSON durable command views and V2 manifest/validator handoffs.

**Testing**: pytest, jsonschema; controlled clock/subprocess fakes, no benchmark traffic.

**Target Platform**: Existing local controller and offline tests on macOS/Linux.

**Project Type**: CLI/library shared-contract amendment.

**Performance Goals**: Two monotonic reads per returned subprocess outcome; no extra process or long wait.

**Constraints**: Serialized writer, existing outcome/redaction semantics, schema version 2, no new scientific threshold or platform abstraction.

**Scale/Scope**: Two existing models, one synchronous runner, affected schema snapshots, focused tests and contract reference.

## Constitution Check

| Gate | Before research | After design |
| --- | --- | --- |
| I traceability | Pass: existing identities retained | Pass: round-trip assertions include producer/deployment/unit/build and enclosing provenance |
| II small architecture | Pass: existing models/runner only | Pass: four Result fields and two native DTO fields; no dependency/service |
| III honest provenance | Pass: actual UTC survives steps | Pass: explicit basis/discrepancy; no invented correction/error/eligibility |
| IV provider semantics | Pass: no provider behavior change | Pass: no provider code/config modification |
| V offline/failure safety | Pass: no clouds/credentials | Pass: faked clocks/subprocess cover all returned outcomes |
| Governance | Pass: routed sources, merged F01R only | Pass: serialized amendment, no sibling/code/Thesis writes in planning |

No constitution amendment or complexity exception is required. The fixed ≤100 ms alignment gate and measurement protocols remain unchanged.

## Project Structure

### Documentation (this feature)

```text
specs/015-command-clock-evidence/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/command-timing.md
├── checklists/requirements.md
├── checklists/clock-contract.md
├── tasks.md
└── analysis.md
```

### Source Code (repository root)

```text
src/cloud_network_benchmark/contracts/execution.py
src/cloud_network_benchmark/contracts/measurements.py
tests/unit/test_execution.py
tests/contract/test_v2_handoffs.py
tests/contract/test_v2_schema_export.py
specs/003-v2-shared-contracts/contracts/shared-contracts.md
specs/003-v2-shared-contracts/contracts/child-run-manifest.schema.json
specs/003-v2-shared-contracts/contracts/validator-input.schema.json
```

**Structure Decision**: Reuse current modules/tests. No timing service, generic clock interface, schema registry addition or exporter. Monkeypatch module clock/subprocess boundaries. `contracts/schema.py` already exports both affected nested schemas and needs no change.

## Interfaces and file ownership

Planner writes only this feature's artifact directory. Next implementer owns only the source/test/schema/doc paths above plus task/evidence updates within this feature. Historical `specs/001-*`, `specs/002-*`, approved spec/plan/task text in 003 and sibling feature work are excluded. Only the active deterministic schema snapshots and shared contract reference in 003 may change, with an explicit amendment link to 015. Do not rewrite the original specification to hide the gap.

`CommandRequest`, `CommandRunner.run`, `ScriptedCommandRunner` signatures, outcomes and subprocess exception behavior remain unchanged. `CommandResult` adds `duration_basis`, `monotonic_started`, `monotonic_finished`, `utc_elapsed_discrepancy_seconds`; `CommandEvidence` adds `duration_basis`, `utc_elapsed_discrepancy_seconds` and reuses Evidence endpoints. Exact rules are in [contracts/command-timing.md](contracts/command-timing.md).

## Delivery phases and acceptance

1. Record baseline; introduce result validation and backward/forward/legacy/malformed checks (US1 MVP).
2. Wire synchronous runner to real monotonic elapsed under controlled clocks; cover all four returned outcomes (US1).
3. Add native DTO consistency and durable round-trip/provenance checks; regenerate two affected active schemas (US2).
4. Run quickstart commands, full existing suite, schema drift and diff/ownership checks. Record tested head and honest results; hand off to Spec Kit convergence. Coordinator serializes integration before consumers adopt the extension.

No implementation or acceptance-test pass is claimed by this plan. Any new file outside ownership returns to coordinator for serialized scope adjustment. No cloud, SSH, benchmark traffic, paid action, PR or merge in this stage.
