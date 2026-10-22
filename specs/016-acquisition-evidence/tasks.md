# Tasks: Source-bound acquisition evidence

**Input**: Design artifacts in `specs/016-acquisition-evidence/`.

**Prerequisites**: spec.md, plan.md, research.md, data-model.md and contracts/acquisition-evidence.md checked; coordinator serializes this branch's shared-contract/build/F03 ownership. Base is merged F01R/F02R/F03, with no unmerged sibling dependency.

**Tests**: Explicitly required by FR-008. Write focused tests before their implementation and confirm the intended failure, not an incidental import failure alone. These tasks are unexecuted at planning handoff.

## Phase 1: Setup

- [X] T001 Reconcile assigned branch/base and source constraints in docs/validation/acquisition-evidence.md; record ownership, current command/schema surfaces, offline limitations and exact research.md upstream commits/file hashes without modifying other worktrees or Thesis (FR-001/007/008/009).
- [X] T002 Create tiny identified/license-preserving source/control-flow fixtures and observation grammar examples in tests/fixtures/acquisition/README.md and tests/fixtures/acquisition/; obtain exact official upstream archives for patch checks outside Git, pin digest/commit identity and audit all normal/error/early-return/cleanup paths supporting the proposed hooks; stop if observation requires changing benchmark logic (FR-001/002/003/007).

## Phase 2: Foundational contracts

- [X] T003 Add runtime/schema tests in tests/contract/test_acquisition_contract.py for the data-model.md reference fields: tool/semantics enums, matching producer/action/invocation/session/clock domain, observed boot UUID, unique artifact references, safe paths and mapping indexes; require "A populated list is nonempty" and old V2 acquisition defaults to unavailable; references alone must not make coverage valid (FR-003/004/006, SC-002/004).
- [X] T004 Implement AcquisitionSource and optional MeasurementTiming.acquisition_sources in src/cloud_network_benchmark/contracts/measurements.py and export them from src/cloud_network_benchmark/contracts/__init__.py; implement the T003 strict constraints, including "All identifiers/paths use existing repository validators", "session_id ... absent only for Sockperf" and "Referenced mapping VM role must match producer"; retain old V2 defaults/unknowns (FR-003/004/006).
- [X] T005 Declare/validate per-tool observation stderr artifacts in src/cloud_network_benchmark/contracts/artifacts.py and src/cloud_network_benchmark/contracts/validation.py, including existing F03 short stderr paths and dynamic day probe/client stderr paths with correct VM roles; preserve old bundle readability and existing stdout/native paths; add focused contract fixtures/tests in tests/contract/test_acquisition_contract.py and export affected canonical schemas under specs/003-v2-shared-contracts/contracts/ with the existing exporter (FR-003/005/006/007).

## Phase 3: User Story 1 — Source-observed windows (P1, MVP)

**Goal**: Produce genuine acquisition/state and native receiver anchors with the unchanged approved protocol, and retain unknowns honestly.

**Independent Test**: Actual F03 producer over fake processes/clocks materializes the implemented raw grammar and derives all four mode windows. Static source/apply/build checks inspect real patches; fake intervals cannot substitute for hooks.

- [X] T006 [US1] Add source-patch/build checks in tests/unit/test_acquisition.py and bootstrap regressions in tests/unit/test_bootstrap.py for zero-fuzz pinned application, source diff restricted to observation, no timer/rate/omit/measurement changes, actual patch/binary/capability identity and stale-cache rejection; include assertions that native CSV/JSON output paths/format and tool argv remain unchanged (FR-001/002/007/008, SC-004).
- [X] T007 [US1] Add src/cloud_network_benchmark/tool_patches/sockperf-3.10-acquisition.patch implementing probe_window and the pre-measurement boot/domain/clock_anchor capture from contracts/acquisition-evidence.md: fixed race-safe sender/receiver enabled observations after guards, existing native termination/reason and exact native filter limits, exported after measurement to stderr; no per-probe disk writes, wait/handshake, signal I/O or changed statistics; preserve notices and fail closed on incomplete/error paths (FR-001/002/003/005/007).
- [X] T008 [P] [US1] Add src/cloud_network_benchmark/tool_patches/iperf-3.17.1-acquisition.patch exporting existing init/post-omit origins and completed receiver intervals with actual cookie/stream/endpoints/omitted/bytes plus pre-measurement boot/domain/clock_anchor capture, using integer native microseconds and no new worker timing/counter/control-flow logic; preserve native stdout and source notices; reject non-monotonic build capability (FR-001/002/003/004/007).
- [X] T009 [US1] Extend SourceBuild/BuildObservation, verified patch application, cache identity and installed capability evidence in src/cloud_network_benchmark/bootstrap.py; package patches through pyproject.toml; bind upstream/patch/configuration/compiler/binary identities, expose unavailable observation identity on old records, and set/verify persistent receiver CNB_ACQUISITION_ID as its action ID without changing tool argv or source algorithms (FR-001/003/007/008).
- [X] T010 [US1] Implement strict bounded CNB_ACQ_V1 parsing and probe/native receiver boundary extraction in src/cloud_network_benchmark/acquisition.py against the actual T007/T008 grammar, retaining malformed recognized records/conflicting duplicates/truncated tails as explicit reasons; implement SourceWindow constraints "Bounds are finite, nonnegative, ordered; booleans are not numbers", earliest<=latest, positive inner support only, and native microsecond truncation uncertainty [t,t+1 microsecond] (FR-002/004/005/008, SC-001/003).
- [X] T011 [US1] Connect F03 source production and the contract's default-empty actual build/receiver-command/artifact context inputs in src/cloud_network_benchmark/measurement.py: set the non-secret action-derived invocation environment, capture source boot/domain and bracketed pre-measurement clock_anchor, bind observation/native/build artifacts after close, associate receiver sessions by actual cookie/endpoints/settings, preserve notification-then-two-seconds coordination and all argv/sequences; accept independent ClockMapping error evidence or leave cross-domain bounds unknown without importing F04; B records arrive only through post-measurement retrieval, never a new F03 copying loop (FR-001/002/003/004/005/007).
- [X] T012 [US1] Materialize four-mode producer fixtures through implemented fake process boundaries in tests/integration/test_acquisition_producer.py and tests/fixtures/acquisition/; assert acceptance/short/calibration windows, one continuous day TCP session and separate <=300-second probe chunks/gaps, genuine producer references, separate counts/zero throughput, and missing-start/filter/end/failure unknowns without hand-asserting complete intervals (FR-001/002/005/008, SC-001/004).

**Checkpoint**: Source hooks, real build identity path and producer exist. An uncompiled/unexecuted instrumented tool remains explicitly unproved; do not declare actual guest behavior from these fixtures.

## Phase 4: User Story 2 — Verified replay (P2)

**Goal**: Make source-supported windows independently replayable and resistant to unsupported declarations or substituted evidence.

**Independent Test**: Raw saved sources plus ordinary existing contracts reconstruct the same bounded windows; mutations degrade them without changing bytes or inventing ends.

- [X] T013 [US2] Add materialized trust/compatibility mutation tests in tests/unit/test_acquisition.py and tests/contract/test_acquisition_contract.py: alter consumed log/native/build bytes, path/symlink, producer/deployment/unit/action/invocation/session/endpoint/stream, patch/binary capability and duplicate records; test old V2 bundles and declared scalar overlap cannot bypass absent raw evidence (FR-003/005/006/008, SC-002/004).
- [X] T014 [US2] Implement bind_acquisition_sources and iter_source_windows in src/cloud_network_benchmark/acquisition.py using existing ArtifactRecord safe-resolution/closed-size-SHA verification for every consumed source; require matching installed modified build/command/session/boot and exact record byte ranges; enforce "References must resolve uniquely within the same child/deployment" and stream receiver intervals without a full-day list (FR-003/005/006/008, SC-002/003).
- [X] T015 [US2] Add clock/window uncertainty and gap boundary tests in tests/unit/test_measurement_timing.py: same boot/local domain, changed boot, cross-VM missing/error mappings, UTC steps, native microsecond precision, disjoint support, exact .99 and uncertainty straddling .99; test no sample-hull/elapsed-runtime completion and unchanged independent <=100-ms plot criterion (FR-002/004/005/008/009, SC-002/003).
- [X] T016 [US2] Implement conservative domain-bound window outputs and source-linked gap preservation in src/cloud_network_benchmark/acquisition.py and F03 timing assembly in src/cloud_network_benchmark/measurement.py; latest starts/earliest ends define inner support, preserve the contract's inner-numerator/outer-denominator lower-bound ingredients without normalizing gaps away, unknown mapping errors remain unknown and interrupted chunks do not extrapolate; keep legacy overlap derived/unknown only, never an authority (FR-002/004/005/006/009).
- [X] T017 [US2] Prove immutable deterministic streaming replay in tests/integration/test_acquisition_producer.py: compare hashes before/after repeated production/replay, large generated receiver streams and bounded per-chunk processing, partial/truncated/error evidence and gap union; old bundles remain parseable and every unavailable result has a specific reason (FR-005/006/008, SC-001/002/003/004).

**Checkpoint**: Shared producer evidence is replayable; no F08 runtime/code/task has been modified and no F08 valid/eligible replay is claimed.

## Phase 5: Polish and handoff

- [X] T018 Apply both packaged patches to exact verified sources, inspect the resulting upstream diffs and compile without traffic when an offline supported Linux toolchain exists; record exact commands/exit results and observed capabilities in docs/validation/acquisition-evidence.md; if unavailable record compile not run, preserve the gap and do not declare source-built behavior proved (FR-001/002/007/008).
- [X] T019 Run the focused and full offline commands plus canonical schema check/git diff --check in specs/016-acquisition-evidence/quickstart.md; record exact head/environment/results in docs/validation/acquisition-evidence.md, distinguish passes from not-run checks and ensure no source/config/protocol regression or cloud/SSH/benchmark side effect (FR-001/006/008, SC-001/002/003/004).
- [X] T020 Finalize docs/validation/acquisition-evidence.md with source/build/precision/overhead limits and a minimal serialized F08 follow-up: consume merged raw helper, use identical support for coverage/RTT selection, preserve full receiver duration/.99/generation rules, finish valid four-mode and eligible prior-calibration peer replay; require convergence of actual patches/producers before coordinator merge, and leave paid acceptance/tool output/clock/disk/export checks separate (FR-007/009).

## Acceptance limits

Cross-host comparisons remain unknown when supplied monotonic sources do not share a boot/clock domain; the F03 timing note records the missing bounded common transform. No elapsed-runtime or sample-hull estimate is substituted. A final diagnostic-day probe chunk with no post-warm-up filter support remains unavailable even though the other bounded chunks replay. Linux compilation remains not run on this Darwin arm64 host, as allowed by T018's explicit fallback; no source-built executable behavior is claimed.

## Dependencies and execution order

T001 -> T002 -> T003 -> T004 -> T005. Foundation precedes both stories. US1: T006 then T007/T008 (independent patch files), then T009 -> T010 -> T011 -> T012. US2: T013 -> T014 -> T015 -> T016 -> T017; its mutation tests use saved fixture sources, and integration reuses US1's producer. T018–T020 follow both stories. T007/T008 require the same completed source audit; neither depends on an unfinished sibling feature. Shared contracts/bootstrap/F03 must converge and merge together before coordinator dispatches F08 consumption.

## Parallel opportunities

One writer is assigned. The [P] on T008 permits independent authoring alongside T007 after T006; it does not authorize another agent or concurrent checkout writer. US2 mutations can be designed from the contract independently, but same-file edits and final integration remain sequential. No runtime feature consumes an unfinished parallel sibling.

## Implementation strategy

MVP is US1 after the foundation: real observational hooks plus actual build/producer path and honest raw evidence. Add verified replay as US2, then complete source/compile/offline checks and convergence. A DTO-only checkpoint is not deliverable acquisition support. Retain explicit unknowns throughout. No paid run, research policy change, PR merge or additional platform is part of these tasks.

## Requirement coverage

| Requirement | Tasks |
| --- | --- |
| FR-001 | T001/T002/T006–T009/T011/T012/T018/T019 |
| FR-002 | T002/T006–T008/T010–T012/T015/T016/T018 |
| FR-003 | T002–T005/T007–T009/T011/T013/T014 |
| FR-004 | T003/T004/T008/T010/T011/T015/T016 |
| FR-005 | T005/T007/T010–T014/T016/T017 |
| FR-006 | T003–T005/T013/T014/T016/T017/T019 |
| FR-007 | T001/T002/T005–T009/T011/T018/T020 |
| FR-008 | T001/T003/T006/T009/T010/T012–T015/T017–T019 |
| FR-009 | T001/T015/T016/T020 |
| SC-001 | T010/T012/T017/T019 |
| SC-002 | T003/T013–T015/T017/T019 |
| SC-003 | T010/T014/T015/T017/T019 |
| SC-004 | T003/T006/T012/T013/T017/T019 |

Counts: 20 tasks, 7 US1 tasks, 5 US2 tasks, 5 setup/foundation tasks and 3 final tasks. No implementation task is checked at planning handoff.
