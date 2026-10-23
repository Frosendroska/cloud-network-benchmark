# Research: DAY artifact identity

Date: 2026-10-23. Read-only investigation base: `6971ba72cf5031ad50c4658606cc1d55c10242ac`. No external research is required for this local engineering correction.

## Sources and scope

Read `AGENTS.md`, `docs/autonomous/WORKFLOW.md`, constitution 2.0.0 and canonical Thesis sources under `/Users/katya/Desktop/University/Masters/Thesis`: `advisor/docs/design_document.md` §§5.1/8, latest `DECISIONS.md` diagnostic refinement, `experiments/FRAMEWORK_IMPLEMENTATION_NOTES.md` §§terminology/commands/results/engineering clarifications, `experiments/EXPERIMENTS.md`, `tracking/IMPLEMENTATION_PROMPTS.md` F01R/F03/F08, `tracking/M04_implementation.md` and relevant roadmap sections. Read shared specs 003 and acquisition amendment 016, their contracts and current implementation. No research-source conflict was found: one retained DAY deployment, one continuous TCP invocation, separate bounded probe invocations and immutable native files remain fixed.

## Decision 1 — Keep retrieval's concrete identity convention

**Decision**: New dynamic records use `artifact_id = declaration.artifact_id + "-" + chunk.unit_id`, `unit_id = chunk.unit_id` and the exact template expansion with that same ID. The declared chunk has `kind="probe_chunk"` and `parent_unit_id` equal to the sole continuous-day unit.

**Evidence**: `src/cloud_network_benchmark/retrieval.py:100-123` already rejects duplicate concrete IDs and enforces the suffix/path/unit relation. `tests/unit/test_retrieval.py:331-345` exercises two uniquely identified concrete chunks. `contracts/validation.py:129-153` instead looks up the base ID and accepts repeated parent-unit records. `artifact_inputs()` derives template-family declarations, not concrete-record keys.

**Rationale**: Aligning admission with existing retrieval removes the inconsistency without weakening replay. AcquisitionSource refers to IDs, and `acquisition.py:551-555` requires unique resolution.

**Alternatives considered**: Repeated family IDs plus path-only disambiguation require expanding AcquisitionSource references and rewriting two helpers; normalizing or renaming old records manufactures association metadata. Both exceed the necessary correction.

## Decision 2 — Associate the fixed DAY client with its actual unit

**Decision**: Set the fixed `day-iperf3-client` and `day-iperf3-observation` declarations to the actual sole `continuous_day` unit ID, including non-default day names. Keep dynamic family declarations and persistent receiver artifacts deployment scoped.

**Evidence**: `contracts/artifacts.py:228-232` currently assigns None to all DAY families. `measurement.py:698-701` executes `iperf3-{parent.unit_id}`; `acquisition.py:603-608,841-846` requires matching client unit/action. F03 generates `probe-chunk-000001` etc., separate command IDs and parent notes at `measurement.py:723-741,769-791`.

**Rationale**: No new evidence is captured; the declaration identifies the producing unit already present in the resolved experiment and command record.

**Alternatives considered**: Relax client matching to any deployment command, or guess the unit from a filename. Both remove existing proof and permit ambiguous associations.

## Decision 3 — Preserve legacy unknowns without repair

**Decision**: Admit only the narrowly documented historical forms in contracts/day-artifact-identity.md while preserving their fields exactly. Existing acquisition helpers must continue to fail closed for their missing/ambiguous identity. No broad fallback for an otherwise invalid modern suffix/path/parent is permitted.

**Evidence**: 016 FR-006/SC-004 and contracts/acquisition-evidence.md require historical V2 readability with unknown acquisition evidence. Current helpers reject mismatching parent/chunk units and unassigned DAY client units; no new proof-disabling flag is required.

**Rationale**: Compatibility is a read path, not evidence migration. Its fixtures start from persisted historical records and materialized original producer bytes at bundle read, because current RetrievalManifest rejects repeated base-family inventory IDs. The modern pipeline retains its actual retrieval records unchanged. Other valid sources and supported intervals survive missing legacy proof.

**Alternatives considered**: Reject all historical DAY records, silently rewrite them to canonical records, or add a new compatibility/version field. These break readability, invent identity, or unnecessarily expand the DTO.

## Decision 4 — Prove the actual boundary chain

**Decision**: Extend the existing acquisition producer integration fixture with canonical ArtifactLayout/artifact_inputs declarations, concrete RetrievalManifest items and fake run_retrieval materialization; feed its real ArtifactRecords unchanged into ValidatorInput, bind_acquisition_sources and iter_source_windows. Use existing source-observation grammar/build sidecars/F03 command evidence. Add focused contract cases in tests/contract/test_day_artifact_identity.py.

**Evidence**: Current `tests/integration/test_acquisition_producer.py:894-900,917-920` gives records ad hoc IDs/paths and the command's unit, bypassing layout/retrieval/admission. Its abbreviated receiver evidence yields valid partial support, not full 86400-second proof.

**Rationale**: A new fixture that manually repairs identity after retrieval would hide the bug again. Day duration stays 86400 with fake time; count actual produced chunks rather than fixing an exact count. Receiver listener command evidence uses the existing first/parent-unit association while its artifacts stay deployment scoped; actual action/invocation/cookie/build proof is unchanged. Missing unrelated metadata is represented honestly rather than synthesized to claim data completeness.

**Alternatives considered**: Helper-only tests, manually built validator-only records, or adding a new runner/fixture platform. None tests the failing integration, and a platform adds unjustified scope.

## Decision 5 — Serialize and preserve schema roots

**Decision**: Planning is artifact-only. Before implementation, coordinator verifies 017 merged, releases the shared writer and integrates latest main into this branch. Only contracts/artifacts.py, contracts/validation.py, focused tests, this feature's artifacts and affected exporter-generated 003 schemas are writable. Existing schema exporter is checked; a semantic-validator-only repair may produce no schema diff, which is recorded rather than forced.

**Rationale**: 017 owns shared contracts concurrently. No dependency is consumed until merged; no new schema root, BuildObservation field, acquisition/measurement/bootstrap/retrieval code or F08 implementation is owned here.

**Alternatives considered**: Concurrent shared writes or branching from unmerged 017. Both violate declared ownership. No benchmark/cloud calls or new paid checks are introduced.

## Clarification coverage

0 human questions; coordinator-provided scope resolves routine choices. Functional scope, domain/data, interaction/read paths, non-functional constraints, integration/dependencies, failure handling, constraints/tradeoffs, terminology, completion signals and placeholders are Clear. No deferred research decisions or unresolved implementation choices remain. N and study windows stay evidence-dependent outside this repair. Requirements checklist stayed 16/16 passing after clarification; no marker changed.
