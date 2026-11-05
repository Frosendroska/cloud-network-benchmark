# Feature Specification: Remote campaign driver

**Feature Branch**: `codex/campaign-driver`  
**Created**: 2026-10-31  
**Status**: Specified; offline implementation authorized, paid execution excluded  
**Input**: User's remote Ubuntu runner workflow, acceptance → calibration → DAY → WEEK; Thesis remains read-only.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Prepare and check a stage (Priority: P1)

The operator enters account IDs, credential/key paths and reviewed stage inputs once, selects a prepared plan, and receives actionable setup errors before any resource creation.

**Why this priority**: Real source inputs and safe admission are prerequisites to every stage.

**Independent Test**: Check all four prepared stage plans without credentials; missing inputs fail clearly with zero resource-creating calls and no budget reservation.

**Acceptance Scenarios**:
1. **Given** missing credentials, approvals, dated costs, calibrated N or exact windows, **when** checking the affected stage, **then** report the missing actual input without synthesizing evidence.
2. **Given** complete real inputs, **when** starting without explicit execution, **then** no resources or benchmarks start.
3. **Given** a selected schedule row, **when** preparing a run, **then** initialized manifests retain its exact row and schedule hashes.

### User Story 2 - Run and resume one remote stage (Priority: P1)

The operator starts a stage once on the always-on runner and can disconnect the terminal. At each WEEK slot P0 and P1 start independently and concurrently, each across all three clouds, with fresh resources and shared budget accounting.

**Why this priority**: The user must be able to close the laptop without losing dispatch or cleanup.

**Independent Test**: A fake clock traverses seven days and 56 observations with paired simultaneous dispatch, durable state and no paid retries.

**Acceptance Scenarios**:
1. **Given** 28 aligned P0/P1 slots, **when** the clock advances, **then** launch 56 independent observations and retain no pair between slots.
2. **Given** restart before or after uncertain launch, **when** resuming, **then** reconcile manifests, recovery and resource evidence before deciding, never repeat a paid measurement automatically.
3. **Given** missing a slot or failed cleanup, **when** dispatching again, **then** mark the missed slot or block launches and preserve liabilities.
4. **Given** calibration or DAY, **when** running, **then** preserve their separate retained-pair protocols and perform no report analysis or automatic N/window choice.

### User Story 3 - Inspect and export evidence (Priority: P2)

The operator sees progress, failures, disk and cleanup liabilities, then copies completed or partial native results to the laptop and analyses the portable selection locally.

**Why this priority**: Research evidence must survive collection and remain independently replayable.

**Independent Test**: Copy an exported bundle into a new location, remove access to the original and run the existing local analysis selection.

**Acceptance Scenarios**:
1. **Given** closed verified native files, **when** exporting, **then** include raw files, public manifests, portable selection and stage summary while excluding private state and credentials.
2. **Given** transfer/checksum failure, **when** finalizing, **then** preserve available partial evidence and failed status, attempt cleanup and block later launches if cleanup fails.
3. **Given** no optional backup/hook, **when** a stage finishes, **then** local collection/status work and do not claim off-machine protection or notifications.

### Edge Cases

Low free disk before launch; missing saved files; source/config changed after freezing; duplicated entry IDs; concurrent controller process; changed controller session; two active budget reservations; provider cleanup failure; crash between subprocess launch and journal update; stale initialized manifests; hook/backup failure; large DAY bundles; source prices requiring refresh; expired interactive credentials.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Expose stage check, explicit start, status and following logs through the requested campaign interface, with one operator settings file and four small prepared stage plans.
- **FR-002**: Check tools, inputs, disk and source-bound identities without resources, benchmarks or reservations; never claim read-only IAM/quota checking proves placement or provisioning succeeds.
- **FR-003**: Generate internal contracts from actual operator inputs and existing configuration, without fabricated approvals, prices, calibration results or windows; preserve underlying paid execution scope and all admission/expiry/cleanup gates.
- **FR-004**: Acceptance covers registered V-S1, V-A/Z/G-FAIL and V-S3, ten measured seconds/phase and pre-calibration N=4. Record actual placement membership; physical controller loss remains explicitly supervised and never passes automatically.
- **FR-005**: Calibration uses a separate S1 pair per cloud with fixed 1/2/4/8 streams, three 60-second trials each, existing orders and ten-second pauses; recommendation remains local and uses saved valid results and the 95% rule.
- **FR-006**: DAY uses separate retained pairs with one uninterrupted 86,400-second aggregate TCP measurement, at most 300-second local RTT chunks and one-second diagnostics; actual disk/log-growth/export review is required before launch.
- **FR-007**: WEEK comprises exactly 28 P0/S1 and 28 P1/S3 observations at identical four UTC slots/day for seven consecutive days, with separate fresh pairs, independent concurrent calls and no P2/P3 or automatic paid replacements.
- **FR-008**: Use one shared budget scope and ledger for active commitments, session ownership and all stages; namespaces, Terraform state/work directories, runtime, recovery and results remain collision-free under two simultaneous calls.
- **FR-009**: Freeze exact configuration and schedule identities, planned slots and per-run state before launch; use measured setup lead inside existing lifetime caps and retain UTC internally.
- **FR-010**: Persist scheduled/starting/running/complete/failed/missed states durably; reconcile uncertain launches against existing manifests, private recovery and resource evidence; never blindly relaunch or silently backfill.
- **FR-011**: Run one systemd-supervised driver per stage; restart resumes reconciliation, never the benchmark measurement; terminal disconnect must not kill the driver.
- **FR-012**: Show current/next slot, expected/completed/failed/missed counts, paths, disk and cleanup liabilities; retain readable stage and separate run/provider logs, rotate controller logs only, record peak disk/memory during tests and acceptance.
- **FR-013**: Seal all available closed native files with checksums, preserve partial failure evidence and perform normal full billable-resource cleanup; failed cleanup blocks future launches and independent provider expiry remains required.
- **FR-014**: Export portable existing-analysis selection and a concise stage summary; keep secret, state and recovery files separate; use streaming copying/hashing for large bundles.
- **FR-015**: Support optional backup of closed verified bundles and completion/failure hook; report unset/failed protection honestly and document remaining data-loss limits.
- **FR-016**: Deploy on Python 3.12; investigate/test or correct advertised Python minimum; document cloud-supported credential renewal required for actual stage duration.

### Key Entities

- **Stage plan/settings**: Public stage choice and configuration references plus operator-specific inputs and private paths.
- **Frozen stage journal**: Exact input hashes, stable session, complete run identities, slots, statuses, PID/launch evidence and cleanup liabilities.
- **Run preparation**: Existing initialized parent/children plus source-bound runtime/cost/admission evidence and original private recovery path.
- **Portable bundle**: Immutable native bytes, public manifests, relocation descriptors, selection, stage summary and checksum seal.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Missing real inputs and starts without execution cause zero resource-creating calls.
- **SC-002**: An offline seven-day traversal accounts for all 56 observations, proves simultaneous P0/P1 dispatch and both active shared reservations, with no path collisions.
- **SC-003**: Restart tests before/after ambiguous launch, missed slots, cleanup failure, low disk and transfer corruption produce honest statuses and zero duplicated paid measurements.
- **SC-004**: Copied bundles replay through the existing local analysis command without dependence on runner paths or private files.
- **SC-005**: The service continues after the invoking terminal exits; test peak memory/disk is recorded and supervised cloud acceptance remains explicitly pending.

## Assumptions

- Ubuntu 24.04 x86-64, Python 3.12, one persistent controller, provisional 4 vCPU/8 GB RAM/200 GB disk; disk suitability needs empirical evidence.
- Engineering-generated source wrappers bind real saved source bytes and faithfully copy declared decisions; generation never grants approval.
- Exact N, UTC windows, real credentials, funding/price inputs and paid authorizations remain operator evidence, intentionally pending in examples.
- User authorization for this implementation does not authorize paid cloud calls or Thesis edits.

## Clarifications

### Session 2026-10-31

- Existing same-session concurrent reservations are retained. A singleton driver owns one durable stage session; restart must reconcile uncertain prior work before dispatch.
- Multi-run costs use one immutable all-stage source with exact config/provider envelope templates. Runtime binds only the initialized child run ID; validation compares every other field against the exact saved template. No per-run budgets or automatic human-review fabrication.
- Physical controller loss cannot be simulated as a successful cloud check. Registered per-provider failure checks require supervised instructions and real evidence.
