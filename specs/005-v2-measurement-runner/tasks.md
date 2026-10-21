# Tasks: F03 V2 measurement runner

**Input**: `specs/005-v2-measurement-runner/` spec, plan, research, data-model and contracts.
**Prerequisite**: merged F01R `cef838e42fc620f90b5943f5a8d1528ddc0b0724`; no unfinished siblings.
**Tests**: Explicitly required by F03 prompt. Use fake processes/clocks and materialized temporary files; no real benchmark traffic, SSH, cloud commands or 24-hour waits. Tests precede behavior implementation and must demonstrate a meaningful failure first.
**Ownership**: One implementer owns only the additions in plan.md. Preserve others' changes. Shared contracts, existing application modules/CLI, sibling tests and Thesis are outside scope. Task completion markers below are implementation work, all initially incomplete.

## Phase 1: Setup

- [ ] T001 Record fixture scope, synthetic versus upstream-source attribution, F01R base, execution limitations and exact focused/regression commands in tests/fixtures/f03/README.md and docs/validation/f03-v2-measurement.md (FR-012, FR-013, SC-005).

## Phase 2: Foundational boundaries

**Goal**: Injectable, bounded local processes and existing evidence models without a shared-contract amendment.

- [ ] T002 Write public-call/result/identity/path tests in tests/contract/test_measurement_contract.py for the contract's MeasurementContext, MeasurementResult and run_measurement, including mismatched config/provider IDs, unsafe/nonprivate IPv4, existing destinations, symlink escape and research/day pending N (FR-001, FR-012).
- [ ] T003 [P] Write concurrent process/clock tests in tests/unit/test_measurement_process.py with scripted events and real temporary files: split stdout/stderr, bounded output previews, exclusive creation, UTC jumps, cancellation, deadline, terminate/kill/reap failures and durable redaction; prove no real command executes (FR-005, FR-010, FR-011, FR-013).
- [ ] T004 Implement MeasurementClock and minimal MeasurementProcesses real local Popen boundary in src/cloud_network_benchmark/measurement_process.py per contracts/measurement-runner.md; use existing CommandRequest/Result, shell=False, concurrent draining, incremental files and bounded shutdown inside one absolute deadline; preserve shared CommandRunner unchanged (FR-010, FR-011, FR-012).
- [ ] T005 Implement frozen MeasurementContext/MeasurementResult and input/path validation in src/cloud_network_benchmark/measurement.py; enforce 'finite future absolute deadline', 'finite positive' reserved shutdown grace, 'config/experiment/provider must agree', exclusive normalized destinations, existing Evidence models and unchanged F01R serialization (FR-001, FR-006, FR-010, FR-012).

**Checkpoint**: Foundational contract/process tests pass; production adapter exists but tests cannot invoke benchmark executables. All stories depend on T001–T005.

## Phase 3: User Story 1 — Short and acceptance measurement (P1, MVP)

**Goal**: Exact three-phase protocol with notification gating and honest timing.
**Independent test**: Fake prepared observation executes three units with exact argv/two pauses, producing native temporary files and F01R evidence; failure/unknown cases cannot imply valid data.

- [ ] T006 [P] [US1] Add exact command-vector tests in tests/unit/test_measurement_commands.py for every handoff option and prohibition, 65/70 versus 15/20 probe durations, iperf3 -t 60/10 -O 5, N=4 pending acceptance/frozen N, private B ports and native output routes (FR-002, FR-003, FR-004).
- [ ] T007 [P] [US1] Add observed timing tests in tests/unit/test_measurement_timing.py with tests/fixtures/f03/timing-cases.json: delayed/split/current notification plus two monotonic seconds, omitted/filtering exclusions, active-window union without double counting, gaps, 99% boundary, coarse/missing anchors, conservative start bracket [process start + verified omission, completion - native receiver duration], coverage over every possible bounded interval, unbounded uncertainty rejection, unknown/>100 ms clocks and no fabricated process-time/CSV-row coverage (FR-005, FR-006, FR-007, SC-004).
- [ ] T008 [P] [US1] Add short/acceptance mode tests in tests/unit/test_measurement_modes.py for idle/single/multi order, two ten-second pauses, supplied identity, zero TCP for idle, no trailing pause, early exit, failed-unit stop and insufficient remaining budget (FR-001, FR-002, FR-011, SC-001).
- [ ] T009 [US1] Implement exact command/output construction in src/cloud_network_benchmark/measurement.py for handoff Sockperf/iperf3 options and ArtifactLayout.unit_paths, preserving native files, uncapped CUBIC/zerocopy TCP and separate stderr without shell redirects or default changes (FR-003, FR-004, FR-010).
- [ ] T010 [US1] Implement loaded/idle and short/acceptance sequencing in src/cloud_network_benchmark/measurement.py with current-process notification gating, probe liveness during two-second wait, reserved shutdown allowance, canonical three units, pauses, fail-stop and cancellation (FR-001, FR-002, FR-005, FR-011, SC-001).
- [ ] T011 [US1] Implement conservative timing/command evidence projection and in-memory result serialization in src/cloud_network_benchmark/measurement.py: actual process times versus native measurement bounds, receiver timing source references, omitted_seconds=5 for initial measured units, existing unavailable Evidence, gap/overlap/clock mappings, optional local conservative timing notes per research.md and unvalidated return status; preserve whole native files for independent F08 assessment (FR-006, FR-007, FR-010, FR-012, SC-004).

**Checkpoint**: US1 is the MVP and works independently with fakes. Do not claim full-duration/99% eligibility merely because commands exit zero. The numeric policy is recorded; F08 issues validation findings.

## Phase 4: User Story 2 — Retained calibration (P2)

**Goal**: Twelve loaded trials with one deployment and no selected N.
**Independent test**: A fresh fake calibration invocation verifies twelve units and eleven pauses without needing a short-study run first.

- [ ] T012 [US2] Extend tests/unit/test_measurement_modes.py with the exact twelve trial IDs/order/counts, 60 measured seconds/5 omitted/70-second probes, eleven pauses, retained deployment, failed-trial stop, unexecuted later IDs and no N selection/idle/inflation output (FR-008, FR-011, SC-002).
- [ ] T013 [US2] Implement calibration iteration in src/cloud_network_benchmark/measurement.py by consuming F01R units in order `1,4,2,8; 8,2,4,1; 2,8,1,4`, reusing loaded-unit behavior and preserving trial indices/artifacts and failures without retries or tuning (FR-008, SC-002).

**Checkpoint**: US2 is independently testable on the shared foundation/loaded helper. F08 owns common-N selection.

## Phase 5: User Story 3 — Continuous diagnostic day (P3)

**Goal**: One uninterrupted TCP client plus bounded local probe rotation.
**Independent test**: Fake full/partial day verifies one TCP start, virtual 86400 seconds and recorded chunk gaps in seconds of real test runtime.

- [ ] T014 [US3] Add day continuity/rotation tests in tests/unit/test_measurement_modes.py and tests/fixtures/f03/day-events.json: exactly one -t 86400 -O 5 TCP request, streaming output, initial notification/lead, <=300-second probe requests, partial last chunk, dynamic unique parent-linked IDs, actual startup/rotation gaps, no per-chunk TCP warm-up/restart, probe failure continuation and TCP failure termination (FR-004, FR-005, FR-009, FR-011, SC-003).
- [ ] T015 [US3] Implement day mode in src/cloud_network_benchmark/measurement.py: consume canonical parent, stream client JSONL, rotate local Sockperf chunks with existing PhaseConfiguration(kind='probe_chunk', parent_unit_id='diagnostic-day', measured_seconds<=300), write dynamic ArtifactLayout paths, preserve process overhead/gaps and final partial interval, and never restart TCP (FR-009, FR-010, SC-003).
- [ ] T016 [US3] Implement day failure/cancellation/deadline finalization in src/cloud_network_benchmark/measurement.py and src/cloud_network_benchmark/measurement_process.py: isolated probe failures rotate within the original deadline, TCP failure ends mode, bounded owned-client shutdown closes files, primary failure precedes stop/write errors, no automatic repeat or fabricated idle/inflation output (FR-009, FR-010, FR-011, SC-004).

**Checkpoint**: Day tests preserve one TCP process across all rotations. Full-TCP/99% RTT coverage and <=100 ms alignment are evidence-dependent, never granted by planned chunk count.

## Phase 6: Integration, failure evidence and handoff

- [ ] T017 [P] Add materialized fake all-mode integration tests in tests/integration/test_measurement_runner.py: exact paths and original CSV/JSON/JSONL/log bytes, caller-projected commands.json/timing.json existing-model round trip (F01R declares these controller-produced; run_measurement returns their typed source evidence), source/identity linkage, closed files, dynamic day chunks, bounded long-output memory and pending validation; keep F08/sibling code unimported (FR-006, FR-010, FR-012, FR-013, SC-001, SC-002, SC-003, SC-005).
- [ ] T018 Add start/read/write/disk-full/early-exit/interruption/deadline/termination failure matrix to tests/integration/test_measurement_runner.py, including no notification, buffered late notification, missing timing, actual short receiver interval and partial-day artifacts; assert retained primary/secondary evidence, zero new units after short/trial failure, and no traffic/SSH/cloud calls (FR-005, FR-006, FR-007, FR-010, FR-011, FR-013, SC-004, SC-005).
- [ ] T019 Review runtime output/recording implementation in src/cloud_network_benchmark/measurement.py and src/cloud_network_benchmark/measurement_process.py against native-file immutability, configured secret redaction in durable views, complete output references, unsafe/existing path rejection and bounded process/resource handling; resolve focused test failures without shared-contract changes (FR-001, FR-006, FR-010, FR-011, FR-012).
- [ ] T020 Run all commands in specs/005-v2-measurement-runner/quickstart.md and record exact tested SHA, interpreter/environment, commands/exit results, focused/full test counts and limitations in docs/validation/f03-v2-measurement.md; distinguish not run from passed and verify git diff --check (FR-013, SC-005).
- [ ] T021 Finalize docs/validation/f03-v2-measurement.md with the actual F09 callable interface, owned files, known unavailable native timing and remaining paid-tool/receiver acceptance limits; update specs/005-v2-measurement-runner/tasks.md only for work actually completed and return head for convergence (FR-012, FR-013, SC-005).

## Dependencies & execution order

T001 → T002/T003 → T004/T005 → US1 tests T006/T007/T008 → T009/T010/T011. US2 T012→T013 and US3 T014→T015→T016 reuse the completed loaded-unit helper; each can be exercised without executing US1's full campaign. T017/T018 follow all modes, T019 follows their findings, T020/T021 finalize. One writer implements source changes sequentially; [P] identifies genuinely disjoint test files/read-only work, not permission to launch extra writers in this worktree.

Graph: Setup → Foundation → US1/MVP → {US2, US3} → Integration/final checks → Converge. No F02R/F08 dependency exists. F09 waits for merged F03 and other prerequisites.

## Parallel examples

- Foundation: T002 contract test and T003 process test authoring use distinct files; source T004/T005 follows.
- US1: T006 commands, T007 timing and T008 modes tests use distinct files. Source changes remain serialized.
- US2: review the twelve-order fixture independently while the single writer completes T013; there are no disjoint implementation files, so no [P] implementation marker.
- US3: inspect day-events fixture and output path expectations independently; mode/process changes remain serialized.
- Final: T017 integration authoring can be compared with documentation review; T018 touches the same file and must follow T017.

## Implementation strategy

Deliver the tested short/acceptance MVP first, then calibration and day on the same boundary. Use synthetic events with source-attributed native fragments, not invented real execution claims. At each checkpoint run the smallest affected test group; after all changes run the focused set and existing full regression once. Convergence follows actual implementation/testing. Do not launch clouds, change research choices, edit shared runtime state, create/merge a PR or touch Thesis in this worker stage.
