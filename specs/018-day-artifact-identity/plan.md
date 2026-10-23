# Implementation Plan: DAY artifact identity

**Branch**: `codex/f01r-day-identity` | **Date**: 2026-10-23 | **Spec**: [spec.md](spec.md)

**Input**: `specs/018-day-artifact-identity/spec.md`; planning base `6971ba72cf5031ad50c4658606cc1d55c10242ac`.

## Summary

Align DAY artifact declaration and ValidatorInput admission with the concrete chunk identity convention already enforced by retrieval. Associate fixed DAY client files with the sole continuous-day unit, retain receiver deployment scope, and preserve legacy inputs with unavailable acquisition proof. Prove the fix through the existing producer, fake retrieval, validation DTO and unchanged source-verification helpers.

## Technical Context

**Language/Version**: Existing Python >=3.9 package; no version migration.
**Primary Dependencies**: Existing Pydantic 2, PyYAML 6; pytest 8/jsonschema 4 test extras; no added dependency.
**Storage**: Existing immutable local native files and manifests under a temporary child result root in tests.
**Testing**: Credential-free pytest contract/integration tests and canonical schema export/check.
**Target Platform**: Local controller/offline development; no guest, cloud or traffic execution.
**Project Type**: Existing Python CLI/library.
**Performance Goals**: Preserve bounded existing source replay; no complete-day input buffering or new performance target.
**Constraints**: One branch/worktree/writer; only assigned files; unchanged helpers, workload, native paths, schema roots and scientific thresholds; legacy identity remains unchanged.
**Scale/Scope**: One continuous DAY client plus actual separately produced <=300-second probe chunks; focused four-mode regressions. No hardcoded 288-chunk completion assumption.

## Constitution Check

*Before research and after design: PASS.*

| Principle | Design check |
| --- | --- |
| I Traceability | Concrete record IDs identify existing command/unit/deployment evidence; no new research experiment identity. |
| II Small architecture | Two existing model files and focused tests; existing functions/DTOs; no service or new dependency. |
| III Immutable/honest provenance | Verify native/log/build hashes and commands unchanged; legacy and unknown tails remain unproved; no record rewriting. |
| IV Provider explicit | No provider behavior, scenario, reservation or expiry change. |
| V Offline/cleanup-safe | Fake processes/clocks/transfers only; source files remain immutable; no cloud or credentials. |
| Workflow | 017 verified merge, latest-main integration and coordinator writer release block all shared implementation. F08 retains consumer ownership. |

No constitution exception or amendment is required. The execution prerequisite remains pending at planning handoff; this is not a claim that implementation is ready to write shared files now.

## Project Structure

### Documentation (this feature)

```text
specs/018-day-artifact-identity/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/day-artifact-identity.md
├── tasks.md
└── checklists/{requirements,contracts}.md
```

### Source Code (repository root)

```text
src/cloud_network_benchmark/contracts/{artifacts,validation}.py
tests/contract/test_day_artifact_identity.py
tests/integration/test_acquisition_producer.py
specs/003-v2-shared-contracts/contracts/*.schema.json  # only exporter-affected files
```

**Structure Decision**: Keep the identity repair in its current DTO modules. Reuse retrieval/acquisition implementations as read-only acceptance boundaries. The existing acquisition fixture supplies raw grammar, fake processes and observed build identity; do not duplicate that framework.

## Interfaces and ownership

- `ArtifactLayout.declarations_for_mode` and `artifact_inputs` retain signatures. Fixed DAY client records get the actual day unit; chunk-family declarations remain templates with unit None.
- `ValidatorInput` retains every field and schema version. Add exact dynamic template resolution and narrowly scoped legacy compatibility in its existing validator. Preserve all other identity and integrity checks. Canonical schema generation remains through `contracts.schema` only.
- `RetrievalManifest`, `run_retrieval`, `AcquisitionSource`, `bind_acquisition_sources`, `iter_source_windows`, `BuildObservation`, and F03 command/timing behavior remain unchanged.
- Implementation ownership is limited to the tree above. Canonical schema files are changed only when the exporter changes their bytes. `tests/unit/test_retrieval.py`, `tests/contract/test_acquisition_contract.py`, `tests/contract/test_v2_handoffs.py` and schema-integrity tests are regression inputs, not required edits.
- No edits to `acquisition.py`, `measurement.py`, `bootstrap.py`, `retrieval.py`, F08 native/validation/calibration/summaries code, historical specs, Thesis, coordinator state or another worktree. Further shared gaps return to the coordinator.

## Phase 0 — Engineering research

Completed in [research.md](research.md): current retrieval convention, fixed DAY client gap, legacy read-only compatibility, existing fixture bypass and 017 serialization. No external fact or unresolved technology choice is required.

## Phase 1 — Design

[Data model](data-model.md) fixes relationships without new fields. [Contract](contracts/day-artifact-identity.md) specifies exact modern/legacy forms and rejection rules. [Quickstart](quickstart.md) gives acceptance commands and expected evidence. Pre/post-design constitution gates pass; no application code is changed during planning.

## Implementation sequence and acceptance

1. After coordinator release, integrate latest main containing verified 017 merge; record integration/base/constitution identity in quickstart before any shared edit. Reconcile this plan if 017 affects the exact files.
2. Add failing focused contract and actual boundary-chain tests, then change only the two identity model files.
3. Modern cases: non-default DAY ID, exact fixed-client unit, multiple uniquely identified chunk records, exact template expansion and declared parent; reject mismatch/ambiguity without helper changes.
4. Legacy cases: both original and freshly derived declarations admit historic unassigned clients and repeated unassigned or parent-scoped family records unchanged; unchanged replay cannot promote those records. Mutation/partial cases retain independent closed intervals and byte hashes.
5. Run quickstart's focused/regression tests, exporter drift check and full credential-free Python suite. Export only affected canonical schemas if needed, then repeat affected checks. Record exact head, commands, exit codes, limitations and unrun checks; request existing convergence stage through coordinator.

MVP is US1's canonical identity path. The shared amendment is not complete or mergeable without US2 legacy and failure proof plus the final checks. No actual cloud/tool acceptance is inferred.
