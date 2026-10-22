# Tasks: Honest command clock evidence

**Input**: `specs/015-command-clock-evidence/` spec, plan, research, data model and contract.
**Tests**: Explicitly required by FR-008. Add focused regressions before code; record expected failures, then implementation results. This is a serialized amendment with one writer; no subagents or parallel feature dependency.
**Format**: `- [ ] Tnnn [USn] description with paths`; all tasks remain unimplemented at planning handoff.

## Phase 1: Setup

- [X] T001 Verify actual branch/worktree and merged base, run the existing focused command from specs/015-command-clock-evidence/quickstart.md, and record baseline command/exit/count/SHA in specs/015-command-clock-evidence/analysis.md; preserve others' work and stay within plan ownership. Covers FR-008/SC-004.

## Phase 2: Foundational contract gate

- [X] T002 Reconcile src/cloud_network_benchmark/contracts/execution.py and src/cloud_network_benchmark/contracts/measurements.py against specs/015-command-clock-evidence/contracts/command-timing.md; confirm no unfinished sibling API is needed and only the two declared active nested schema exports are affected. Record discrepancies in specs/015-command-clock-evidence/analysis.md before edits. Covers FR-007.

## Phase 3: User Story 1 - Preserve command clock evidence (P1, MVP)

**Goal**: Actual UTC survives clock steps while monotonic elapsed and existing outcomes remain honest.
**Independent Test**: Backward -7 and forward +10 discrepancy cases, UTC-only strictness, malformed inputs and all four synchronous returned outcomes under controlled clocks.

- [X] T003 [US1] Add failing CommandResult cases in tests/unit/test_execution.py for backward/forward/no-step/zero elapsed, aware offsets, 0.001-second tolerance boundary and outside tolerance; reject naive UTC, booleans, NaN/infinities, negative/reversed/partial monotonic values, missing/mismatched discrepancy/basis/duration and mismatched outcomes. Preserve legacy ordered-UTC/duration equality tests. Covers FR-001/FR-002/FR-003, SC-001/SC-002.
- [X] T004 [US1] Extend CommandResult in src/cloud_network_benchmark/contracts/execution.py with duration_basis default utc and nullable monotonic_started/monotonic_finished/utc_elapsed_discrepancy_seconds. Enforce data-model constraints: "Monotonic mode: finite nonnegative number; booleans rejected"; "Monotonic mode: finite nonnegative number, finish >= start; booleans rejected"; "Monotonic mode: finite signed number; booleans rejected". Enforce duration "finite nonnegative number; booleans rejected", aware normalized UTC, complete monotonic opt-in, both 0.001-second equalities, UTC mode all new numeric fields None and old chronology checks. Preserve outcome rules and durable redaction. Covers FR-001/FR-002/FR-003.
- [X] T005 [US1] Add controlled synchronous runner regressions in tests/unit/test_execution.py, stubbing subprocess.run and module UTC/monotonic reads for success, nonzero, TimeoutExpired and KeyboardInterrupt under backward and forward UTC; assert actual endpoints, positive monotonic duration/discrepancy, existing partial-output behavior, exact invocation and redaction. Assert ScriptedCommandRunner preserves supplied timing. Covers FR-004/FR-008, SC-001.
- [X] T006 [US1] Update SubprocessCommandRunner in src/cloud_network_benchmark/contracts/execution.py to sample UTC then time.monotonic before subprocess, monotonic then UTC after current returned paths; derive duration and signed discrepancy from observations and opt into monotonic basis. Preserve invocation, timeout, output, exception and outcome behavior; no clamping or extra abstraction. Covers FR-004/FR-006.
- [X] T007 [US1] Run tests/unit/test_execution.py through the assigned offline runtime and record actual exits/counts in specs/015-command-clock-evidence/analysis.md; repair only owned code until US1 checks pass. Covers FR-008/SC-001/SC-002.

## Phase 4: User Story 2 - Preserve durable native timing (P2)

**Goal**: Saved native evidence retains consistent timing, provenance and redaction under V2.
**Independent Test**: Build redacted native evidence from known monotonic result; JSON round-trip plus enclosing child-manifest/validator handoff; reject conflicting projection and keep UTC-only historical V2 examples accepted.

- [X] T008 [US2] Add failing native evidence/projection checks in tests/contract/test_v2_handoffs.py for backward/forward/no-step UTC, all four outcomes, complete finite monotonic opt-in, boolean values, disagreement in basis/UTC/monotonic/discrepancy, missing projected result fields, forbidden raw secrets and unchanged legacy partial/unavailable UTC records. Covers FR-005/FR-006/FR-008, SC-002/SC-003.
- [X] T009 [US2] Extend CommandEvidence in src/cloud_network_benchmark/contracts/measurements.py with duration_basis default utc and nullable discrepancy; reuse existing Evidence endpoints. Enforce data-model constraints: "Same basis as projected result for monotonic opt-in" and "Same finite signed discrepancy as projected result for monotonic opt-in; booleans rejected"; require complete observed monotonic/aware UTC pairs, ordered finite nonnegative values, boolean rejection in both bases, matching UTC instants and 0.001-second numeric agreement. Validate monotonic durable result via explicit CommandResult-field projection while retaining argv/redactions_applied metadata and existing secret restrictions. Preserve UTC partial/unavailable behavior; reject monotonic result wrapped as UTC and discrepancy under UTC basis. Do not change MeasurementTiming/ClockMapping/TimingGap or statuses. Covers FR-005/FR-006.
- [X] T010 [US2] Add durable JSON/model reload and enclosing ChildRunManifest/ValidatorInput tests in tests/contract/test_v2_handoffs.py; assert exact timing/basis/discrepancy, producer/deployment/unit/build identity and existing manifest config/Git provenance, secret sentinel exclusion, unchanged V2 version and V1 rejection. Explicitly select Result model fields from durable metadata for reload. Covers FR-005/FR-007/FR-008, SC-003.
- [X] T011 [US2] Extend tests/contract/test_v2_schema_export.py for new and legacy V2 timing payloads, new representable basis/presence/numeric schema constraints and unchanged registry/version; keep cross-field arithmetic assertions at runtime. Covers FR-007/SC-004.
- [X] T012 [US2] Regenerate specs/003-v2-shared-contracts/contracts/child-run-manifest.schema.json and specs/003-v2-shared-contracts/contracts/validator-input.schema.json using existing exporter, add amendment link/compatibility and uncertainty limits in specs/003-v2-shared-contracts/contracts/shared-contracts.md, and confirm no other schema or historical 001/002 artifact changes. Add representable conditional/presence schema constraints through owned runtime model definitions where required. Covers FR-006/FR-007.

## Phase 5: Cross-cutting validation and handoff

- [X] T013 Run focused/full pytest, schema drift, diff/ownership checks from specs/015-command-clock-evidence/quickstart.md; record actual commands/exits/counts/tested revision and any not-run check in specs/015-command-clock-evidence/analysis.md. Resolve actionable failures only within declared ownership. Covers FR-008/SC-004.
- [X] T014 Update task completion/evidence in specs/015-command-clock-evidence/tasks.md and specs/015-command-clock-evidence/analysis.md, verify requirements/checklist consistency, commit the completed bounded change and report head/ownership/interfaces/assumptions/limitations to coordinator for speckit-converge. No PR/merge/Thesis/shared-runtime writes. Covers FR-007/FR-008/SC-004.

## Dependencies and execution order

T001 → T002 → T003 → T004 → T005 → T006 → T007 (US1 MVP) → T008 → T009 → T010 → T011 → T012 → T013 → T014. US2 has independently runnable DTO tests but builds on the same result contract; it follows completed US1. No dependency on F03/F04 implementation or unfinished sibling.

## Parallel examples

US1: none scheduled; result and runner share execution.py/test_execution.py. US2: after T009, T010 handoff tests and T011 schema tests touch different files and could be prepared independently, but one assigned writer executes sequentially. No [P] markers or additional agents are authorized by this task list.

## Implementation strategy

Complete result/runner US1 first and validate its narrow behavior. Then add durable native US2 and active exports. Full acceptance requires both stories, focused/full checks and later clean Spec Kit convergence; a planning analysis or fake test alone is not cloud evidence.

## Phase 6: Convergence

- [X] T015 [US2] Close the MEDIUM schema/runtime parity gap in src/cloud_network_benchmark/contracts/measurements.py for monotonic CommandEvidence: require both outer started_at/finished_at observed UTC wrappers and encode the representable required fields, types and numeric constraints of the complete projected CommandResult in result, while preserving legacy UTC dictionaries and documented durable metadata. Add omission/malformed projection regressions in tests/contract/test_v2_schema_export.py (including absent outer UTC wrappers and absent projected UTC bounds, duration_seconds, outcome, stdout and exit_code), regenerate only the two active child-run-manifest/validator-input schema snapshots, and rerun focused/schema checks. Current schemas accept those omissions while runtime rejects them; cross-field arithmetic remains runtime-only. Required by FR-007, plan: active deterministic schema presence/type/numeric constraints, contracts/command-timing.md: Evidence limits and schema exports, and T011/T012 (partial).
