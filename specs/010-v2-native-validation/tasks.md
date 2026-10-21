# Tasks: F08 V2 Native Result Validation

**Input**: `specs/010-v2-native-validation/` spec, plan, research, data model and contract.
**Prerequisites**: Merged F01R `cef838e42fc620f90b5943f5a8d1528ddc0b0724`; approved artifact checklists; no unfinished sibling dependency.
**Tests**: Explicitly requested native known fixtures, corruption/failure injection, boundary checks and replay. All tasks below are unimplemented at planning completion.
**Organization**: User-story phases; exact owned paths. One writer in this worktree; do not revert others. No shared-contract edits or additional services.

## Phase 1: Setup

- [x] T001 Reconcile the assigned branch/base and artifact/checklist gates, record current environment and exact validation commands in specs/010-v2-native-validation/quickstart.md; preserve canonical Thesis read-only and avoid credentials/traffic.
- [x] T002 Create the small source-grammar fixture inventory and provenance/expected-value index in tests/fixtures/f08/cases.json for acceptance, short_study, calibration and diagnostic_day; declare examples synthetic from the contract's actual native dialect, with no claims of executed benchmarks.

## Phase 2: Foundational

Blocking prerequisite: parser records and explicit result shapes; no shared schema edits.

- [x] T003 Define the public function/signature and metrics-shape contract checks in tests/contract/test_native_validation_interface.py using existing F01R models and feature contracts/native-validation.md; initially fail until owned modules exist, then require unavailable evidence reasons and unchanged shared schemas.
- [x] T004 Define small frozen SourceRef/RttSample/ProbeCounts/ReceiverInterval/NativeParse records in src/cloud_network_benchmark/native.py per data-model.md: packet index "nonnegative integer", times "finite monotonic-clock seconds with rx>=tx>=0", RTT "finite nonnegative full RTT", counters "nonnegative integers", bytes "integer>=0 and bool rejected as a number", "end>start, seconds>0"; retain source/line/session location and existing ValidationFinding with no parallel schema framework.

## Phase 3: User Story 1 - Recover trustworthy native metrics (P1, MVP)

**Goal**: Native inputs produce correct source-linked samples, receiver intervals and counts.
**Independent test**: Known native input bytes yield hand-calculated metrics/counts; source hashes remain unchanged.

- [x] T005 [US1] Add real-dialect small Sockperf CSV/log, client JSON, receiver JSON/JSONL and corrupt/truncated/reset/duplicate variants under tests/fixtures/f08/native/; include preamble/separators/full-RTT header, separate counts, omitted intervals, native sender flags and multiple persistent-server sessions, using the contract grammar rather than normalized-only fixtures.
- [x] T006 [US1] Write parser/numerical expectation tests in tests/unit/test_native.py covering FR-001–004, 0/1/2/999/1000/9999/10000 replies, exact linear interpolation vectors and sample n-1 standard deviation; test sender-only rejection, sum/per-stream double-count avoidance, warm-up exclusion and immutable files before parser implementation.
- [x] T007 [US1] Implement parse_sockperf in src/cloud_network_benchmark/native.py for the contract's four-column full-RTT table/native preamble and separate scoped counts; reconcile identical repeated summaries without adding counts, reject conflicting/half-RTT/invalid rows, preserve intact partial rows and precise findings; never infer generated/lost messages from exported indices/row counts and never double-subtract skipped from SentMessages.
- [x] T008 [US1] Implement parse_iperf3 in src/cloud_network_benchmark/native.py for JSON and incremental event/data JSONL with receiver authority, embedded server output, omitted intervals, bounded session association, final-versus-interval reconciliation and partial intact records; reject duplicate keys/nonfinite/boolean/negative numeric evidence and flag malformed tails/ambiguous sessions/resets/overlaps without double-counting bytes or stream seconds.
- [x] T009 [US1] Implement rtt_statistics and receiver_statistics in src/cloud_network_benchmark/native.py: exact NumPy linear definition, "median n>=1, P95 n>=1000, P99 n>=10000 and standard deviation n>=2", explicit unavailable reasons, sample denominator n-1, P95-minus-median, decimal receiver bytes/time Gbit/s and no sender/rate-average substitution; expose native samples/intervals for later F12 recomputation.

**Checkpoint**: US1 parser contract and unit tests pass independently of a runner or validator.

## Phase 4: User Story 2 - Decide validity and comparison eligibility (P1)

**Goal**: Saved four-mode attempts yield honest data/comparison findings with execution/cleanup facts retained.
**Independent test**: Materialized mode fixtures and boundary mutations reproduce expected statuses/findings offline.

- [x] T010 [US2] Author non-secret mode/config/schedule/identity/command/timing/diagnostic/metadata/artifact fixture descriptions in tests/fixtures/f08/bundles.json using existing F01R models, actual files and SHA-256; preserve one retained deployment for day/calibration and no F03-local sidecar dependency.
- [x] T011 [US2] Write focused validator tests in tests/unit/test_validation.py with in-test materialization of declared native fixtures and F01R input records; cover FR-005–009/012 and all four modes, safe paths, missing/not-expected artifacts, snapshot/manifest/identity/provenance contradictions, failed commands and separate cleanup outcomes.
- [x] T012 [US2] Implement validate_bundle input/materialized artifact verification in src/cloud_network_benchmark/validation.py using resolve_artifact_path, closed/retrieved record sizes/SHA-256, saved config/resolved/schedule/manifests and native parser sources; reject legacy/unsupported inputs, missing mandatory records and contradictions while retaining diagnostic partial evidence; do not make optional validation outputs self-required.
- [x] T013 [US2] Implement source-backed command/settings/unit/provenance checks in src/cloud_network_benchmark/validation.py for full-RTT monotonic 64-byte/1000-mps/reply-every-1 probes, private UDP11111/TCP5201 listeners, sender zerocopy/CUBIC/A-to-B receiver/stream settings, -l131072/one-second interval/output/streaming flags and no rate/reverse/bidirectional extras, durations/warm-up/notification delay/phase pauses/order, persistent-day single TCP and <=300-second chunks; cross-check DTOs against materialized command/timing/native evidence, not requested argv alone.
- [x] T014 [US2] Implement measured-window mapping/coverage/generation in src/cloud_network_benchmark/validation.py: loaded "full requested receiver TCP duration and at least 99% active-probe measured time", idle "full requested probe interval", day "full continuous 86400 seconds and >=99% RTT time", generation "at >=95% target" from actual sent/reporting runtime; union clipped acquisition windows minus warm-up/filtering/gaps, derive conservative TCP start brackets [A command start+observed omission, A completion-native measured duration] when exact starts are unavailable and prove the minimum coverage over every admissible interval; reject inconsistent/unsupported bounds, avoid row-count/process-launch proxies and retain uncertainty/partial intervals with unavailable reasons.
- [x] T015 [US2] Implement F01R ValidationResult/status/findings and metrics payload reduction in src/cloud_network_benchmark/validation.py per data-model.md: invalid trust/identity/settings evidence outranks partial, trusted incomplete surviving intervals partial, no measurements unavailable, mandatory complete data valid; execution/primary/retrieval/cleanup failures remain separate, failed cleanup sets bundle_complete false without erasing sound metrics; preserve slow values and diagnostic resets/gaps without performance labels.
- [x] T016 [US2] Implement evaluate_comparison and local comparison prerequisites in src/cloud_network_benchmark/validation.py: compatible slot/scenario/phase/settings/frozen N/provider cohort and valid complete short-study results; "skew <=60 seconds target-met; >60 through 300 timing-eligible with degraded-target finding; >300 ineligible; missing starts unavailable"; return copied ValidationResult values retaining all comparison findings, reject missing/duplicate/mismatched peers and leave calibration/acceptance/day out of fresh-study comparisons.
- [x] T017 [US2] Add exact duration and immediately below/at/above .95 generation, .99 coverage, 60/300-second skew and 100-ms clock tests in tests/unit/test_validation.py; prove one-second alignment needs both role estimates<=100 ms while A-local RTT remains usable; include whole-second connection timestamps that cannot prove exact UTC starts, conservative command/native start brackets whose worst-case coverage passes/fails or is unavailable, missing/contradictory timestamps, chunk unions/gaps/restarts and no implicit boundary tolerance or straddling-byte prorating.

**Checkpoint**: US2 validator tests pass with independent F01R inputs; no clouds, F03/F04/F11 imports or new shared DTOs.

## Phase 5: User Story 3 - Select common N and hand off summaries (P2)

**Goal**: Complete calibrated evidence yields a recommendation and every run yields a concise deterministic source-linked summary.
**Independent test**: Known three-cloud medians select exact common N/fallback, incomplete cases stay unresolved, and summary replay is stable.

- [x] T018 [P] [US3] Write known median/plateau/fallback and missing/invalid/duplicate/mixed-campaign/deployment/all-zero calibration tests in tests/unit/test_calibration.py for FR-010; use valid existing ValidationResult payloads, twelve unique trial indices/provider in order 1,4,2,8 / 8,2,4,1 / 2,8,1,4, three trials/count, and source hashes.
- [x] T019 [P] [US3] Write deterministic source-linked readable summary tests in tests/unit/test_summaries.py for FR-011/012: execution/data/comparison/cleanup separated, units/IDs/hashes/counts/gaps/failures/missing reasons visible, diagnostic values retained, no fabricated calibration/day idle/inflation or independent deployment counts.
- [x] T020 [US3] Implement small frozen CalibrationRecommendation and select_common_n in src/cloud_network_benchmark/calibration.py: "three providers, one retained deployment per provider, twelve unique ordered trial indices per provider, three valid trials/count"; best tested median includes 1, smallest candidate 2/4/8 with ratio>=.95 all clouds; complete no plateau gives 8/false, any missing/invalid/duplicate/mixed/all-zero evidence gives unavailable selection; canonical sorted evidence digest excludes evaluation time and configs are never mutated.
- [x] T021 [US3] Add same-deployment valid three-phase latency inflation and frozen-N evidence consistency checks in src/cloud_network_benchmark/validation.py; compare resolved source CommonNState/trial recommendation identities, keep pending N/windows explicit and leave inflation unavailable for missing/invalid idle baselines, calibration and day.
- [x] T022 [US3] Implement summarize_validation in src/cloud_network_benchmark/summaries.py with declared deterministic order/units/evidence reasons and separate execution/data/comparison/cleanup outcomes, counts and source identity/hashes; return text only with no raw-file writes, reports/plots or performance warning rules.
- [x] T023 [US3] Add deterministic per-mode replay/integrity integration checks in tests/integration/test_native_validation_replay.py using actual materialized native bytes, fixed evaluated_at and before/after SHA-256; prove source/unit association, trusted partial retention, full status output, F09 comparison handoff and F12 sample/interval access without quantile averaging.

**Checkpoint**: Calibration and summary checks pass; research N remains a recommendation, not an automatically frozen setting.

## Phase 6: Polish and Cross-Cutting Checks

- [x] T024 Complete signature/payload checks in tests/contract/test_native_validation_interface.py for every public function/record and exact existing F01R ValidationResult model serialization; demonstrate no producer-local sidecar dependency, no shared schema drift and explicit source evidence/unavailable reasons.
- [x] T025 Run focused then full credential-free regression commands from specs/010-v2-native-validation/quickstart.md; record exact head/environment/commands/exit results in that file, fix attributable failures only in owned F08 files, and label unavailable checks as not run rather than passed.
- [x] T026 Review final implementation against specs/010-v2-native-validation/spec.md and contracts/native-validation.md, update verified usage/evidence in specs/010-v2-native-validation/quickstart.md, run git diff --check and hand off exact head/owned paths/remaining assumptions to coordinator for convergence; do not merge, launch resources or edit Thesis/shared state.

## Dependencies & Execution Order

Setup T001 -> T002 -> foundational T003/T004 -> US1 T005–T009 -> US2 T010–T017 -> US3 T018–T023 -> final T024–T026. T003 starts as an intentionally failing interface test; complete assertions in T024 after all modules exist. Within each story write the focused requested tests before implementation; validate failures, then repair and pass. US2 relies on US1 native primitives, while US3 calibration/summary units can use independent typed fixture results; end-to-end replay waits for all prior implementation.

| Requirement | Tasks |
| --- | --- |
| FR-001 | T002–T008 |
| FR-002 | T006/T008/T009/T023 |
| FR-003 | T006/T009/T021/T023 |
| FR-004 | T005–T007/T014/T017 |
| FR-005 | T010–T012/T024 |
| FR-006 | T011/T013/T017 |
| FR-007 | T011/T014/T017/T023 |
| FR-008 | T011/T015/T019/T022 |
| FR-009 | T016/T017/T023 |
| FR-010 | T018/T020/T021 |
| FR-011 | T019/T022–T024 |
| FR-012 | T001/T006/T011/T015/T019/T023/T025/T026 |
| SC-001 | T005–T009/T023 |
| SC-002 | T010–T015/T023 |
| SC-003 | T014/T016/T017 |
| SC-004 | T018/T020/T021 |
| SC-005 | T019/T022–T025 |

## Parallel Opportunities

One writer remains assigned. [P] means independently authorable files, not authorization to spawn extra agents or concurrent writers in this checkout. US1 parser tasks share native.py/test_native.py and are sequential. US2 T010 fixture inventory precedes T011 materialization tests; no US2 tasks are marked parallel. US3 T018 and T019 tests use different files and typed fixtures and can be authored independently; calibration.py and summaries.py implementation still follows their tests. Replay waits for all stories. Shared module edits T021 are serialized after US2.

## Implementation Strategy

US1 is the first useful increment: verify native calculations and untouched sources. Add US2 mode/integrity/timing status checks, then US3 common-N and readable summaries. Run focused tests at each checkpoint; run the full regression suite after all tasks. Keep individual functions small and reuse existing evidence models. Do not broaden to a CLI, schema platform, plotting pipeline or real acceptance. Return any actual unavoidable shared-contract mismatch to the coordinator before changing it.

## Notes

26 tasks: setup 2, foundation 2, US1 5, US2 8, US3 6, final 3. Numerical thresholds are authorized engineering policy or fixed source gates as recorded in research.md; passing fixtures are not real cloud or research evidence. Every task has an ID, correct story scope and owned path. All tasks are checked after offline implementation verification; fixture passes do not represent cloud or research evidence.
