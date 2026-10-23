# Tasks: DAY artifact identity

**Input**: `specs/018-day-artifact-identity/` spec, plan, research, data model, contract and quickstart.

**Prerequisites**: Planning base `6971ba72cf5031ad50c4658606cc1d55c10242ac`; every shared implementation task waits for T001/T002. Coordinator alone confirms verified 017 merge, latest-main integration and release of the sole shared-contract writer. Coordinator reported 017 merged at `2cd36e9ea7b12681545e8c6680ccda42388ffded` and writer release conditional on planning completion; latest-main integration remains unrun and T001 must record the complete implementation gate.

**Tests**: Explicitly required by FR-007. Add focused failing tests before the related implementation. Test failure must demonstrate the identity mismatch, not an import error. All tasks below are unexecuted at planning handoff.

## Phase 1: Setup

- [X] T001 Record coordinator-confirmed 017 merge SHA, writer release, integrated latest-main base/head, assigned branch/worktree and checked-out constitution identity in specs/018-day-artifact-identity/quickstart.md before any shared code/test/schema edit; do not merge PRs, edit another worktree or write coordinator state (FR-008, SC-004).

## Phase 2: Foundational — integration reconciliation

- [X] T002 Reconcile the integrated src/cloud_network_benchmark/contracts/artifacts.py and src/cloud_network_benchmark/contracts/validation.py against research.md, existing retrieval/acquisition APIs and tests/integration/test_acquisition_producer.py; record any changed line references, baseline command/exit and exact writable files in specs/018-day-artifact-identity/quickstart.md; report any further shared gap to coordinator rather than editing outside ownership (FR-003/006/008).

**Checkpoint**: Only after T001/T002 may the implementer own the two shared identity files and assigned tests. Do not consume unmerged 017 or change F08.

## Phase 3: User Story 1 — canonical DAY identity (P1, MVP)

**Goal**: Actual retrieved DAY records pass the shared validation DTO and existing replay helpers with unambiguous unit/command identity.

**Independent Test**: The actual ArtifactLayout → producer → fake retrieval → ValidatorInput → helper chain accepts canonical records for several chunks and a non-default day ID; identity mutations cannot authorize another invocation.

- [X] T003 [US1] Add failing modern contract cases in tests/contract/test_day_artifact_identity.py: exactly one continuous-day parent with a non-default ID, fixed client/observation declarations assigned to that unit, unchanged deployment-scoped receiver files, and dynamic family declarations retaining "unit_id=None". Assert "artifact_id = family.artifact_id + '-' + chunk.unit_id", exact same-ID path expansion, declared "kind=probe_chunk" with the correct parent, and existing "0 < measured_seconds <= 300"; reject wrong suffix/path/unit/parent/producer/kind/deployment and duplicate concrete identity rather than accepting a legacy fallback (FR-001/002/003/007, SC-001/002).
- [X] T004 [US1] Add a failing canonical DAY pipeline fixture in tests/integration/test_acquisition_producer.py using the existing fake F03 process/clock grammar, actual command IDs and parent notes, real ArtifactLayout/artifact_inputs, closed RetrievalManifest and fake run_retrieval output. Pass actual retrieved ArtifactRecords unchanged into ValidatorInput and both existing acquisition helpers; materialize existing build/native receiver evidence, use at least two chunks and exactly one TCP client, preserve native paths and assert independently supported windows/gaps without claiming full-day completeness from abbreviated receiver evidence (FR-001/002/003/005/007, SC-001/002).
- [X] T005 [US1] Associate day-iperf3-client and day-iperf3-observation with the actual sole continuous_day unit in src/cloud_network_benchmark/contracts/artifacts.py; preserve signatures, required expectations, native layout paths, dynamic family "unit_id=None" and persistent receiver deployment scope; do not hardcode an example unit name or change other modes (FR-001/006).
- [X] T006 [US1] Align dynamic DAY record admission in src/cloud_network_benchmark/contracts/validation.py with the existing retrieval convention: resolve exactly one of the three base family template declarations, require exact suffix/path/unit plus declared probe_chunk and correct day parent, and preserve kind/producer/deployment/integrity checks; reject malformed concrete modern identities without broad fallback. Keep ValidatorInput fields/version and all acquisition/retrieval helper code unchanged (FR-002/003/006, SC-001/002).

**Checkpoint**: Canonical identity is the MVP; the feature is not mergeable before historical compatibility and evidence checks below.

## Phase 4: User Story 2 — historical readability and honest failures (P2)

**Goal**: Legacy inputs remain readable without fabricated acquisition proof, and valid independent sources survive a failed source.

**Independent Test**: Original and fresh declarations admit the exact legacy forms unchanged; raw-source replay against actual producer commands remains unavailable for those records while independently verified canonical chunks survive.

- [X] T007 [US2] Add failing legacy and unaffected-mode contract cases in tests/contract/test_day_artifact_identity.py: fixed DAY client/observation records with "unit_id=None" and exact base-family probe records with "unit is None or D.unit_id" and distinct template-matching paths. Cover original persisted versus freshly derived declarations and tuple identity "(artifact_id, path, unit_id)"; preserve repeated legacy family IDs without promoting mismatching suffixed modern records, and retain acceptance/short-study/calibration identities and absent-acquisition defaults (FR-004/006/007, SC-002/003/004).
- [X] T008 [US2] Add separate persisted-legacy read fixtures and canonical mutation/failure cases in tests/integration/test_acquisition_producer.py using the existing materialized producer bytes. Legacy fixtures represent historically saved records at the read boundary; do not feed them through modern RetrievalManifest or modify the modern pipeline's returned records. Cover ambiguous references, corrupt source bytes and failed/truncated chunk evidence. Require unchanged serialized record identities and raw hashes, unavailable legacy/invalid-source proof from unchanged helpers, and surviving independently verified earlier chunk/closed receiver support with explicit gap or unknown tail; never rename records, infer bounds from filenames or synthesize another receiver invocation (FR-003/004/005/007, SC-002/003).
- [X] T009 [US2] Implement only the narrow legacy read branches from contracts/day-artifact-identity.md in src/cloud_network_benchmark/contracts/validation.py: preserve exact fixed-client None and base-family None/day-parent identities against old/new declarations without modifying records, commands, references or bytes. Keep modern exact-match rules from T006, existing tuple uniqueness, absent-evidence defaults and non-DAY behavior; add no availability/build field or helper relaxation (FR-004/005/006, SC-002/003/004).

**Checkpoint**: US2 acceptance is independently testable with legacy fixtures; release still requires both stories and final validation.

## Phase 5: Polish and cross-cutting checks

- [X] T010 Run the existing src/cloud_network_benchmark/contracts/schema.py exporter/checker against specs/003-v2-shared-contracts/contracts/ and record generated diff or no-diff in specs/018-day-artifact-identity/quickstart.md. Retain only exporter-affected canonical schemas, validate them with tests/contract/test_schema_integrity.py, and do not hand-author cross-record guarantees or add another schema root (FR-006/007, SC-004).
- [X] T011 Run quickstart.md's focused contract/retrieval/acquisition integration commands and full credential-free Python suite at the final integrated head; record exact commands, exit codes, counts, schema check, git diff --check and unrun/native-cloud limitations in specs/018-day-artifact-identity/quickstart.md. Recheck only affected checks after any repair and never label fake acceptance as real timing/cloud proof (FR-003/005/006/007, SC-001/002/003/004).
- [X] T012 Reconcile specs/018-day-artifact-identity/{spec,plan,tasks,quickstart}.md with final ownership, evidence and requirement coverage, then return changed files, exact validated head/base, assumptions and next convergence stage to coordinator. Preserve F08 consumer ownership, checked-out constitution and all research choices; do not edit shared runtime state or create/merge a PR (FR-008, SC-004).

## Dependencies and execution order

T001 → T002 → T003 → T004 → T005 → T006 → T007 → T008 → T009 → T010 → T011 → T012. Tests precede their related implementation. US1/US2 tests each establish independently assessable outcomes; US2 integrates with US1's changed validation file and therefore implementation stays sequential. All code/schema/test edits wait for coordinator release even if their files appear independent of 017.

## Parallel opportunities and examples

No [P] writer tasks are declared: the two stories share a validator and existing integration fixture, and this branch has one writer. Within US1, the implementer may batch independent read-only inspection of the declaration and retrieval APIs. Within US2, independent legacy/rejection parameter cases may run in the same pytest command. This does not authorize another writer or agent. After 018 merges, coordinator may resume independent F08 consumer work.

## Implementation strategy

Complete setup/reconciliation, implement and validate US1's canonical identity path, then implement and validate US2's historical/failure behavior. Run schema and final offline gates once the final code is stable; record any subsequent repair and affected rechecks. Preserve the deliberately small framework and do not turn this repair into lifecycle integration, parser implementation, real-tool acceptance or a generic artifact system.


## Phase 6: Convergence

- [X] T013 Require the complete modern DAY family/chunk/path/unit/parent relation unconditionally for every suffixed record in the three known probe families in src/cloud_network_benchmark/contracts/validation.py, independently of ordinary declaration-unit equality or direct artifact-map lookup. Currently a family declaration has unit_id=None, so a modern suffixed record with unit_id=None bypasses dynamic_day_record whenever both units compare equal; day-probe-csv-probe-chunk-000002 with the chunk-000001 path and null unit is admitted. Adding a direct concrete artifact declaration also bypasses family resolution and admits the day parent as a modern chunk unit. Resolve modern known-family records against exactly one canonical base-family template and reject null/parent/undeclared units, suffix/path disagreement and direct-declaration shadowing without accepting them through normal or legacy branches. Add parameterized regressions for all three families, the null-unit/wrong-suffix/path combinations, direct concrete declarations and old/new declarations, preserving the exact allowed base-family legacy records, fixed-client compatibility, unaffected modes and unchanged retrieval/acquisition helpers. Extend the materialized boundary mutation fixture as needed to prove malformed modern identity cannot be admitted while independent valid records remain usable per FR-002/003/004/006/007, SC-002/003, US1/AC2, data-model.md: malformed modern identity, contracts/day-artifact-identity.md: rejected modern cases and legacy compatibility, and T003/T006/T007/T009 (completed).
