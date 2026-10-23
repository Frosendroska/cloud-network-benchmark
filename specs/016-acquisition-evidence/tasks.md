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


## Phase 6: Convergence

- [x] T021 CRITICAL Bind each acquisition build reference to one closed, verified, same-deployment/producer build-identity ArtifactRecord and its original bytes in src/cloud_network_benchmark/bootstrap.py and src/cloud_network_benchmark/acquisition.py; compare the replayed upstream/archive/patch/compiler/configuration/installed-binary/capability fields with the supplied BuildObservation and the actual producing command executable/environment rather than accepting a matching in-memory artifact_id alone. Currently iter_source_windows never resolves or hashes build_artifact_id, and synthetic build objects without any build file authorize windows. Add materialized missing/substituted build-file and wrong executable/environment regressions using existing artifact infrastructure per Constitution III, FR-003/006/007, contracts/acquisition-evidence.md: verify all consumed native/log/build artifacts, and T009/T013/T014 (partial).
- [x] T022 Align the packaged iperf hook, strict parser and materialized four-mode fixtures with the actual pinned native record sequence in src/cloud_network_benchmark/tool_patches/iperf-3.17.1-acquisition.patch, src/cloud_network_benchmark/acquisition.py and acquisition tests: receiver_interval currently omits protocol while _iperf_windows requires protocol=tcp; normal omitted intervals precede post_omit and are rejected; native reset_stats updates start_time but retains the preceding interval end, so the first post-reset interval may start before the reset. Preserve those native boundaries/omitted state and exclude unsupported warm-up portions without changing native timers/statistics/CLI. Exercise initial origins, omitted records, reset, the straddling first measured interval and integer socket IDs from the source grammar rather than fixtures that add protocol and start directly at post_omit; refresh exact patch identities and zero-fuzz/source checks when patch bytes change per FR-001/002/005/008, SC-001/004, contracts/acquisition-evidence.md: iperf stats-v1, and T006/T008/T010/T012/T018 (partial).
- [x] T023 Corroborate each source window with parsed immutable native session/stream/endpoints/settings and receiver output in src/cloud_network_benchmark/acquisition.py using the existing source-bound interface: the current iperf native check accepts any bytes containing the cookie, does not require receiver JSON/JSONL or compare interval offsets/bytes/omitted state, and accepts arbitrary nonempty observation endpoints. Require the uniquely matching client invocation and original receiver evidence, compare native origins/offsets/stream associations and counts without prorating bytes, and reject settings/endpoint/stream/session substitutions while retaining zero-byte intervals. Add source-realistic materialized tests proving that cookie-only JSON and self-consistent altered observation records cannot authorize windows; preserve Sockperf native/count corroboration within the same identity checks per FR-002/003/006/008, SC-002, US2/AC1-2, contracts/acquisition-evidence.md: source output grammar and producer/replay interfaces, and T011/T013/T014 (partial).
- [x] T024 Preserve independently verified closed receiver intervals and other units' sources when a command fails, a final record is truncated or one source is invalid in src/cloud_network_benchmark/acquisition.py and src/cloud_network_benchmark/measurement.py. Currently iter_source_windows rejects every non-success command before parsing, and run_measurement catches one source error around the whole replay and replaces every timing's acquisition_sources with unavailable, discarding valid earlier intervals/other chunks. Keep the failure and unknown tail explicit, never turn partial evidence into complete execution, and expose a bounded replay result/reason path so consumers retain closed subsets without extrapolation. Add multi-unit/chunk and failed-receiver materialized regressions through actual F03 timing assembly per FR-005/006/008, SC-002/003, US2/AC2, data-model.md: lifecycle and failures, and T010/T014/T016/T017 (partial).
- [x] T025 Correct overlap_coverage_lower_bound in src/cloud_network_benchmark/acquisition.py to intersect probe inner support with the union of non-omitted receiver inner support, subtract missing receiver periods, and divide by the enclosing continuous receiver outer duration without buffering a day. Currently only the outer receiver envelope clips the probe numerator: receiver bounds [0,100]/[900,1000] and probe [0,1000] return 1.0 instead of the specified 0.8 lower bound; receiver intervals [0,100] and [900,1000] with that probe return 1.0 instead of 0.2. Cover receiver uncertainty straddling .99, probes spanning receiver gaps, overlapping multi-stream intervals and malformed/reversed bounds; preserve unknown cross-domain transforms, full receiver duration and separate generation/100-ms plot decisions. Use the same replayable support for downstream sample selection per FR-004/005/009, SC-002/003, contracts/acquisition-evidence.md: clocks and conservative overlap, research.md: Decision 3, and T015/T016/T017/T020 (partial).


### Convergence completion note

T021–T025 were implemented by the serialized feature owner. F02R captures and immutably records
the remote build-identity sidecar; F03 replays it with the exact executable, environment, native
receiver JSON, source observation records, and closed artifact bytes. Failed producer outcomes and
malformed tails retain verified closed windows while marking the unobserved tail unknown. Native
iperf session, settings, interval, stream, endpoint, and byte-count mismatches fail replay. The
updated iperf patch applied to the pinned source with zero fuzz. See
[validation evidence](../../docs/validation/acquisition-evidence.md) for exact commands and limits.


## Phase 7: Convergence

- [X] T026 Match the pinned iperf native file grammar and reporting-origin semantics in src/cloud_network_benchmark/acquisition.py and the materialized four-mode fixtures. Both _native_iperf_cookie and _native_iperf_metadata currently json.loads each line, rejecting actual pretty-printed -J output; JSONStream_Output emits start.data as json_start directly, whereas replay expects data.start. Diagnostic argv deliberately has no --get-server-output, so requiring embedded server_output_json cannot corroborate diagnostic receiver intervals; consume the existing closed/retrieved B receiver JSONL artifact by its exact session instead of adding flags or fabricating embedded output. Native omitted intervals are reported relative to the initial origin when emitted, and iperf_time_diff produces absolute offsets for the first interval crossing reset; current corroboration subtracts post_omit for all rows and fixtures invent negative offsets (-4 and -1), rejecting source-realistic positive offsets. Reconcile origins per actual native reporter call order, preserved omission and stream/session/endpoints/settings without changing tools/CLI/statistics, and cover complete and truncated native -J/JSONL from all modes through F03/binder/replay per FR-001/002/003/005/008, SC-001/002/004, contracts/acquisition-evidence.md: iperf stats-v1, and T008/T010/T012/T022/T023.
- [X] T027 Restore bounded streaming acquisition replay and coverage in src/cloud_network_benchmark/acquisition.py: parse receiver JSONL incrementally, corroborate each native interval once, preflight with bounded per-session maps, and intersect chronological supports without storing a day of windows. Retain the complete outer receiver duration, gaps and native precision. A generated 100,000-interval source session exercises public iter_source_windows, and an increasing-size tracemalloc check exercises coverage with constant-space inputs per FR-008, SC-003, and T014/T017/T025.
- [X] T028 Verify common session/settings/origin/identity prerequisites before publishing any source window as independently valid. Materialized F03 mutations for session duration, origins, stream count, and omitted/native evidence fail before the first window; failed-command and truncated source/native tails retain only the independently corroborated closed prefix with an unknown tail per FR-003/005/006/008, SC-002, US2/AC2, and T014/T023/T024.
- [X] T029 Isolate corrupt per-unit Sockperf native candidates during binding/F03 assembly. Two-unit/chunk materialized regressions corrupt the native bytes before and after a valid unit and verify the unaffected unit remains available while the corrupt unit stays unknown; immutable-byte rejection remains enforced per FR-005/006/008, SC-002, US2/AC2, and T011/T014/T016/T017/T024.


## Phase 8: Convergence

- [X] T030 Use one explicitly defined version-text representation consistently across source-build observation, the captured CNB_BUILD_V1 sidecar, BuildObservation and source replay. Full multiline compiler/tool version text is retained in source observations and sidecar evidence; comparison trims only output framing while preserving internal lines. Source-realistic multiline bootstrap and replay regressions cover compiler, Sockperf and iperf3 banners, including upstream banner spellings.
- [X] T031 Reconcile native iperf reporter filtering with source-observed receiver intervals without changing timers, statistics, CLI or native files. Preflight recognizes only the pinned-reporter case of an all-stream, all-zero, positive-duration short final interval in a successful complete session. Prior independently corroborated windows remain available and the filtered duration is explicitly unsupported. Tests cover positive bytes, emitted one-second zero-byte intervals, incomplete sessions, and the source-grounded short filtered tail through replay/F03.
- [X] T032 Preserve a corroborated closed receiver prefix when A-side diagnostic client JSONL has one truncated final event. The binder validates unique start identity/settings and the complete prefix, pairs only the exact session/cookie with closed B receiver evidence, and leaves the unmatched tail unknown. Contradictory complete records and ambiguous starts are rejected. Materialized replay/F03 tests verify immutable bytes and retained prefix behavior.
