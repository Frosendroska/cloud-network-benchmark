# Tasks: F04 V2 local diagnostics

**Input**: `specs/006-v2-local-diagnostics/` spec, plan, research, data model and contracts.
**Tests**: Explicitly required Linux fixtures/fake clocks, schema and materialized failure checks. Write focused tests before corresponding implementation and observe meaningful failures first.
**Organization**: One writer in `/private/tmp/cnb-f04`, branch `codex/f04-v2-diagnostics`. Other workers own other worktrees; do not revert their work. Owned paths/interfaces/checks are fixed in plan.md. All tasks start incomplete; planner checks are not implementation completion.

## Phase 1: Setup

- [X] T001 Add small documented Linux CPU/TCP/NIC, chrony and provider/build cases under `tests/fixtures/f04/` with expected deltas, missing/reset/recovery/gaps and synthetic identities; do not copy real credentials/results. Cover FR-001–009, FR-012 and SC-001–004.

## Phase 2: Foundational

- [X] T002 Define feature-local context/outcome adapter and artifact declarations in `src/cloud_network_benchmark/diagnostics.py` using unchanged DiagnosticSample/VmMetadata/ClockMapping/ArtifactInput; enforce context identity, `schema_version is 2`, `vm_role` vm_a/vm_b, provider aws/azure/gcp, and canonical plus declared clock paths. Use `deployment_id` existing identifier pattern; preserve unavailable evidence. Cover FR-010/014.
- [X] T003 Add standalone stdlib input/clock/file/command boundaries and direct `--help` entry point in `src/cloud_network_benchmark/diagnostics_guest.py`; require `duration_seconds` finite positive non-bool, explicit safe NIC and normalized absolute nonsymlink output root, reject unknown fields/identity mismatch before writes, compute one guest-local deadline including metadata startup, and bound every local observation command by remaining time. Cover FR-011/012/014 and SC-005.

## Phase 3: US1 - Local host samples (P1, MVP)

**Goal**: Produce truthful local sampling independently of benchmark traffic.
**Independent test**: Known Linux snapshots and fake time reproduce metrics, resets/missing/gaps and partial file outcomes on either role.

- [X] T004 [US1] Add arithmetic/read-error fixture tests in `tests/unit/test_diagnostics.py`: all ten values, aggregate versus per-core, guest double-count prevention, zero/negative ticks, core changes, named TCP header pairing, missing interface, large integers and reset/recovery. Assert `finite percentages in [0,100] or unavailable evidence` and `nonnegative integer deltas or unavailable evidence`. Cover FR-002–005 and SC-001.
- [X] T005 [US1] Implement rooted proc/sys snapshot readers and ordinary parsers in `src/cloud_network_benchmark/diagnostics_guest.py`; require first-eight CPU fields, named Tcp RetransSegs and explicit-interface sysfs statistics, isolating read/parse failures by metric. Cover FR-001–004.
- [X] T006 [US1] Implement active/steal CPU and network deltas in `src/cloud_network_benchmark/diagnostics_guest.py` per data-model.md; no wrap/clamp or stale-valid carry across missing reads; `reset_fields` are existing Evidence field names whose values are unavailable. Cover FR-002–005 and SC-001.
- [X] T007 [US1] Implement monotonic baseline/tick scheduling and actual UTC/monotonic sample records in `src/cloud_network_benchmark/diagnostics_guest.py`: `interval_seconds` finite and strictly positive, `monotonic_seconds` finite and nonnegative; skip missed tick opportunities, keep real multi-second deltas/gap reasons and never catch up or restart across phases. Cover FR-001/005/012 and SC-002.
- [X] T008 [US1] Add materialized collection failure tests in `tests/integration/test_diagnostics_collection.py` for early stop, existing files, symlink escape/collision, read/write/flush/close errors, stop signal and deadline exhaustion; prior bytes survive, failures retain completed lines and no false closure. Cover FR-004/005/011/012 and SC-005.
- [X] T009 [US1] Implement exclusive streaming compact JSONL, per-line flush, bounded stop and partial outcome in `src/cloud_network_benchmark/diagnostics_guest.py`; reject NaN and keep histories out of memory; preserve earlier files/lines on failure and never certify retrieval. Cover FR-010–014 and SC-002/005.

## Phase 4: US2 - Once-per-VM identity (P2)

**Goal**: Produce one actual static metadata record with honest unknowns.
**Independent test**: Provider/guest/build fixtures produce matching typed metadata without live endpoints; differing settings and absent observations remain visible.

- [X] T010 [US2] Add metadata/provider/acceleration/build tests in `tests/unit/test_diagnostics.py` for both roles/all providers, mixed CPU models, non-1460/non-CUBIC observed settings, missing/mismatched builds/images, Azure VF evidence and documented-limit provenance; `mtu is a positive integer or unavailable evidence`. Cover FR-006/007 and SC-003.
- [X] T011 [US2] Implement `build_collection_context` in `src/cloud_network_benchmark/diagnostics.py` using role-specific ProviderDeploymentOutput and optional matching BootstrapResult; reject mismatch/duplicate builds, preserve absent identity and original provider limit semantics, and pass no secrets/credential refs. Cover FR-006/007/010/014.
- [X] T012 [US2] Implement once-per-VM guest CPU/kernel/driver/acceleration/MTU/congestion-control observations and exclusive metadata write in `src/cloud_network_benchmark/diagnostics_guest.py`; separate observed driver from acceleration verification and never mutate guest settings. Cover FR-006/007/010/011/013 and SC-003.
- [X] T013 [US2] Add bounded actual tool version/binary-hash reads and source/compiler/build comparison to `src/cloud_network_benchmark/diagnostics_guest.py`; preserve per-tool missing/mismatch details within shared StructuredEvidence and do not invent source identity. Cover FR-007/011/012 and SC-003/005.

## Phase 5: US3 - Clock evidence (P2)

**Goal**: Preserve actual mappings and assess the declared mapping-pair clock condition.
**Independent test**: Fake chrony/clock cases reproduce estimates, unknown synchronization, jumps and boundary eligibility without contacting NTP/clouds.

- [X] T014 [US3] Add clock parsing/query/eligibility tests in `tests/unit/test_diagnostics_clock.py`: exact read-only local argv/locale, query timeout <=0.2 and remaining duration, missing/unsynchronized/local mode, invalid stratum/reference, malformed/non-finite error inputs, capture allowance, 100/100.001 ms boundary, wrong roles, UTC reversal and deadline exhaustion. `estimated_error_ms is finite and nonnegative or unavailable evidence`. Cover FR-008/009/012 and SC-004.
- [X] T015 [US3] Implement bounded local chronyc tracking observation and ClockMapping-compatible records in `src/cloud_network_benchmark/diagnostics_guest.py` using the data-model formula, observed source/status details and truthful unavailable fallback; no daemon configuration or external host argument. Cover FR-008/009/012/013.
- [X] T016 [US3] Stream baseline/per-snapshot clock mappings to declared role clock JSONL in `src/cloud_network_benchmark/diagnostics_guest.py`; no stale error cache, no query after deadline, preserve clock jumps without changing sampling/deadlines, flush/close alongside diagnostics. Cover FR-008/010/011 and SC-004/005.
- [X] T017 [US3] Implement typed streaming loaders, local result validation and `alignment_eligibility` in `src/cloud_network_benchmark/diagnostics.py`; validate roles/observed synchronized source and both errors <=100 ms, report parse/missing/truncation/count/closure contradictions without rewriting bytes; do not equate mapping eligibility with interval/data validity. Cover FR-008–012 and SC-004/005.

## Phase 6: Cross-cutting acceptance

- [X] T018 Add `tests/contract/test_diagnostics_contract.py` to validate materialized samples/metadata against unchanged F01R runtime models and exported schemas, clock lines against ClockMapping, and exact ArtifactInput IDs/roles/paths including extra clocks; distinguish collection/closure from retrieval/integrity and keep source/reset/unavailable semantics. Cover FR-004/007–011 and SC-001/003–005.
- [X] T019 Extend `tests/integration/test_diagnostics_collection.py` for all four mode duration contexts, distinct A/B outputs, uninterrupted fake 86,400-second streaming with bounded memory, delayed/no-catch-up/UTC cases, and copied guest `--help` outside repo with PYTHONPATH removed; assert no shell/cloud/benchmark commands, performance labels, stale clock accuracy or premature retrieval. Cover FR-001/005/010–014 and SC-002/005.
- [X] T020 Document actual APIs, local artifacts, exact focused/full/schema commands/results/tested SHA and offline limitations in `docs/validation/f04-local-diagnostics.md`; reconcile implementation task completion in `specs/006-v2-local-diagnostics/tasks.md` and F09/F08/F12 handoff with the final code. Cover FR-010–014 and SC-005.
- [X] T021 Run quickstart's focused tests, full offline pytest suite, shared-schema --check, py_compile and git diff --check; repair only owned files and record exact commands/exits/counts in `docs/validation/f04-local-diagnostics.md`, then hand off to convergence. Cover FR-012–014 and SC-001–005. Checks not run remain explicitly not run.

## Dependencies & Execution Order

Setup T001 -> foundation T002/T003 -> US1 T004–T009 -> US2 T010–T013 -> US3 T014–T017 -> final T018–T021. This is a single-writer sequence; no [P] markers because most source/test files are shared within this small feature. Each story can be assessed with its fixture inputs without requiring benchmark traffic or unfinished sibling features. US2 seeds may be unavailable during the US1 MVP; clocks remain unknown until US3.

Within a story, add failing focused tests before behavior, then parsers/arithmetic/writes. The final acceptance tasks verify integrated wire/closure/streaming behavior. Do not call the feature complete at the US1 MVP checkpoint.

## Parallel examples and implementation strategy

No parallel code writers are authorized in this worktree. Independent read-only fixture calculations may be compared while implementing US1; US2 provider fixtures and US3 clock fixtures can be evaluated separately after their dependencies are available. A future coordinator could divide disjoint test files, but no agent spawning is required or added by this task list.

Deliver US1 as the minimal useful host history, then actual metadata and clocks, then the full offline acceptance suite. Stop at the verified local feature handoff; F09 lifecycle/transport, F08/F12 clock consumption and real paid acceptance remain separate work.
