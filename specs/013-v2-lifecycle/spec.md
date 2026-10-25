# Feature Specification: F09 V2 lifecycle integration

**Feature Branch**: `codex/f09-v2-lifecycle`
**Created**: 2026-10-23
**Status**: Reviewed for planning
**Input**: Thesis `tracking/IMPLEMENTATION_PROMPTS.md:155–163`, F09 lifecycle integration.

Compose the merged instrument into explicit acceptance, calibration, fresh short-study and diagnostic lifecycles. The researcher receives an auditable outcome per selected cloud, original native files and separate execution, data and cleanup states. Dependencies F02R/F03–F08/F11 are merged at `527451d8697917c795d8dc9224cf8a17947c4311`. Implementation remains credential-free; paid execution and thesis interpretation are separate stages.

## Clarifications

### Session 2026-10-23

Agent artifact check replaces routine human questions under the authorized workflow. All ten ambiguity categories are Clear: functional scope; domain/identity/state; CLI flow; quality/security; external dependencies; failure/recovery; constraints; terminology; completion signals; placeholders. No research/source conflict or question remains.

- Engineering resolution: use existing F11 cap-start, stage-fit, one-copy-retry and skew policies unchanged (FR-003/009/012).
- Engineering resolution: no VM creation until every selected provider has verified protection; after that, healthy peers can continue despite a provision/readiness failure if their complete remainders fit (FR-004/005).
- Engineering resolution: invoke the existing A-local runner on A through a narrow guest entry, stage the same-revision runtime explicitly, and treat staging as bounded preparation (FR-006/007).
- Engineering resolution: inventory actual closed files; declare extra build/log artifacts explicitly and leave missing proof unavailable, rather than weakening shared input validation (FR-010/014).
- Engineering resolution: terminal execution reflects the actual command workflow; validation and cleanup remain independent dimensions, and campaign success requires all mandatory dimensions (FR-015).
- Engineering resolution: explicit cleanup/reconciliation is allowed after restart; no resumed measurement or replay of uncertain creation (FR-017).

Built-in specification checklist before/after clarification:16/16 satisfied. No requirements changed by ambiguity review.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run a bounded selected observation (Priority: P1)

A researcher selects a predefined configuration and saved schedule row and receives protected, prepared measurements on the selected clouds.

**Why this priority**: Makes the merged components usable while enforcing cost/lifetime before creation.

**Independent Test**: Three-cloud fake acceptance trace proves reservation, verified all-cloud expiry, concurrent pair creation, readiness, stabilization and measurement.

**Acceptance Scenarios**:

1. **Given** admitted inputs, **When** execution starts, **Then** complete workflow cost/cap is durably reserved and every selected cloud's independent expiry verified before any VM creation.
2. **Given** healthy prepared pairs, **When** the shared slot is reached after readiness and 60-second stabilization, **Then** the configured mode runs locally with actual timestamps/skew; waiting consumes the original cap.
3. **Given** a peer provision/readiness failure after protection, **When** healthy peers can still fit their complete workflows, **Then** they continue with explicit partial-provider evidence and the failed peer is cleaned up.

### User Story 2 - Receive a verified final bundle (Priority: P1)

A researcher receives immutable native results and replayable validation after the pair finishes.

**Why this priority**: A successful tool exit without verified original bytes cannot support research.

**Independent Test**: Real temporary fixture bytes flow through producer closure, both-VM retrieval, deletion and strict saved-input replay.

**Acceptance Scenarios**:

1. **Given** measurement completion, **When** finalization runs, **Then** all producers close, both VMs' copies are verified, normal deletion verifies resource absence, and validation/sealing records separate outcomes.
2. **Given** failed copying, **When** a retry fits before original cap/expiry minus cleanup allowance, **Then** failed artifacts get at most one retry; otherwise partial evidence survives and cleanup proceeds.
3. **Given** a saved input, **When** replayed, **Then** source IDs, exact checksums, commands, clock/build/native evidence and manifests agree; missing acquisition proof remains unavailable.

### User Story 3 - Retain pairs for the selected protocol (Priority: P2)

A researcher runs CAL or DAY on a separate retained pair per cloud, without a launch per trial or reporting window.

**Why this priority**: These modes have distinct measurement and resource lifetimes.

**Independent Test**: Fake time completes 12 ordered calibration trials and one continuous diagnostic day with one reservation/deployment per cloud.

**Acceptance Scenarios**:

1. **Given** CAL, **When** it runs, **Then** the same pair/listeners execute three interleaved repeats of 1/2/4/8 streams, then close/copy/clean once; selection yields evidence, not automatic freezing/approval of N.
2. **Given** DAY and supplied frozen N, **When** it runs, **Then** one uninterrupted 86,400-second TCP process has local probe chunks ≤300 seconds and local one-second diagnostics; gaps remain and reporting windows add no launches.
3. **Given** acceptance before calibration, **When** resolved, **Then** ten measured seconds and N=4 apply; research short observations retain 60 measured seconds, five-second warm-up and ten-second phase pauses.

### User Story 4 - Recover honestly after failure (Priority: P1)

A researcher can inspect partial files/private recovery evidence and explicitly remove residual resources without silently duplicating work.

**Why this priority**: Independent compute expiry does not replace full deletion or durable accounting.

**Independent Test**: Failure injection across every provider/stage, including loss without a controller finalizer, proves expiry evidence and blocked uncertain restarts.

**Acceptance Scenarios**:

1. **Given** measurement failure followed by copy/deletion failure, **When** terminal, **Then** primary, retrieval and cleanup failures remain separately visible with available files.
2. **Given** abrupt controller loss, **When** no finalizer executes, **Then** registered AWS/GCP stop or Azure deallocation remains independently scheduled and cleanup is unresolved; restart cannot assume absence or replay creation.
3. **Given** later verified absence, **When** explicit cleanup reconciles its evidence, **Then** only justified liabilities clear; compute stop alone never counts as full deletion.

### Edge Cases

Partial safety registration; uncertain creation/no output; duplicate claims or changed input bytes; missed slot/session change; insufficient remaining cap; server death; missing notification/acquisition proof; clock step; disk/close failure; diagnostic gaps; invalid/traversing inventory; copy timeout/hash mismatch/collision; cancellation in each stage; cleanup raising while peers finish; controller loss without finally; DAY chunk identity versus deployment-scoped receiver/build files.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Provide explicit CLI preview, selected execution and cleanup/reconciliation for existing modes/configurations and saved rows, reporting stable IDs/status/reasons. Never automatically launch another row or repeat a campaign; preview/initialization is not execution.
- **FR-002**: Reuse admission/config rules, dated cost inputs, P0/P1 protection, optional-scenario gates and explicit failed-cloud replacement context. Reject missing paid prerequisites, changed evidence, duplicate/uncertain creation or unresolved liabilities. No new automatic replacements.
- **FR-003**: Activate one complete reservation immediately before expiry registration, retaining original cap/session/source hashes. Existing 1,200/1,800/87,600-second caps include registration, preparation, waiting, 60-second stabilization, measurement/shutdown, final copy and cleanup; never restart or extend them.
- **FR-004**: Verify the complete selected-provider expiry set before any VM creation, then consume each one-shot creation claim. AWS/GCP stop and Azure deallocation are compute safeguards, not deletion. Partial safety-resource registration also requires cleanup.
- **FR-005**: Provision selected clouds concurrently with independent namespaces and exactly two VMs each. Preserve provider-specific settings/journals and partial failures; admission-wide failure prevents all creation, while protected healthy peers may continue within their caps.
- **FR-006**: Prepare tools/listeners, verify private connectivity and bootstrap-bound readiness, then stabilize for 60 seconds. Failed readiness cancels that provider's measurement. Recheck the full remaining workflow before each stage.
- **FR-007**: Execute measurement locally on A and diagnostics locally on both VMs with persistent B listeners and real tool/build/clock identity. Never execute client traffic on the controller or treat transport timing as acquisition proof. Record runtime/package provenance and preparation failures.
- **FR-008**: Preserve merged short/acceptance/CAL/DAY settings, order, warm-up, pauses, common-N evidence and diagnostic cadence. Keep one pair/listener set throughout CAL/DAY and fresh pairs for fresh short observations. No CAL/DAY idle baseline/inflation.
- **FR-009**: Coordinate shared UTC slots and record actual starts/skew/uncertainty/misses and partial providers. Reuse ≤60-second target, >60–300 explicitly flagged otherwise eligible, >300 excluded; unknown timing is unavailable.
- **FR-010**: Close/flush clients, diagnostics and persistent receiver outputs before final inventory/copy. Preserve closure evidence, actual ends, partial files and gaps on every reachable terminal path. A transport exit alone proves no producer closure.
- **FR-011**: Build both-VM source declarations/closed inventory from actual files/sizes/hashes and use verified final retrieval with exclusive destinations and per-attempt evidence. No periodic remote checkpoints or automatic disk retention/recovery.
- **FR-012**: Reuse bounded-copy policy: at most one retry per failed artifact fitting before earlier original cap/provider trigger minus cleanup allowance. Retain verified/partial evidence, refuse exhausted work, then attempt normal deletion; add no new timer/cap.
- **FR-013**: Once resource-creating work may have begun, attempt explicit normal deletion on every reachable terminal path, including registration/provision failure/interruption. Verify absence of VMs, disks, public addresses, safety infrastructure and other billable resources; one failure cannot skip peer cleanup.
- **FR-014**: Persist immutable native bytes and strict replayable validation input with exact identities/declarations/records/commands/timings/metadata/snapshots/reservation/expiry/source revisions. Bind acquisition only from actual verified native/observation/build bytes; legacy/incomplete proof remains unavailable.
- **FR-015**: Validate after final copy/deletion attempts, exposing execution, data validity, comparison eligibility and cleanup separately. Persist results/readable summaries/manifest projections and final checksums with a defined immutable scope and no self-hash cycle. Incomplete evidence or residual cleanup forbids successful campaign completion.
- **FR-016**: Preserve original failure separately from retrieval/cleanup failures and retain multiple stage/provider details and unexecuted units. Write atomic durable manifests/private recovery references before uncertain side effects; redact secrets and exclude private Terraform/access state from public results.
- **FR-017**: Reconcile observed incurred/estimated charges, active reservations and verified absence through the ledger without invented totals or release of unknown liabilities. Restart never resets caps or replays uncertain claims. Explicit cleanup can use saved recovery evidence without measurement.
- **FR-018**: Credential-free fake commands/time and actual temporary native bytes must cover four modes and AWS/Azure/GCP, server death, abrupt loss without finalizer, independent expiry, stage-boundary failures, partial providers, copy/deletion failure and recovery blocking. Fakes are not cloud/native Linux acceptance.
- **FR-019**: Keep ordinary local functions, existing contracts, explicit provider Terraform and SSH/SCP. No platform/services, compulsory Parquet, cloud telemetry, automatic performance warnings, thesis interpretation or research changes.

### Key Entities *(include if feature involves data)*

- **Selected lifecycle**: mode/config, row, campaign/observation/run/deployment identities and execution scope.
- **Admission/protection handoff**: reservation, cap/session, complete expiry set, stage allowance and creation claims.
- **Provider attempt**: pair outputs, readiness, local measurement/diagnostics, phase/chunk identities and recovery references.
- **Final bundle**: original files/declarations/records, snapshots, commands/timings, strict validation input/result and checksum index.
- **Recovery/terminal evidence**: primary/secondary failures, resource absence, private journals, reconciliation and separate states.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All four modes/three providers have offline trace checks; zero VM creations precede complete verified expiry and valid reservation/claim.
- **SC-002**: Every successful fake trace proves readiness →60-second stabilization →measurement →all writers closed →both-VM verified copy →normal deletion/absence →validation/sealing, with no periodic copying.
- **SC-003**: All reachable injected failures retain first cause/available bytes and attempt cleanup; abrupt-loss simulations preserve independent expiry evidence and block uncertain creation without claiming a finalizer ran.
- **SC-004**: Every saved final validation input strictly roundtrips and replays its result from original bytes; corruption/missing closure/acquisition proof cannot yield fabricated validity.
- **SC-005**: Retained tests prove one reservation/pair per cloud, 12 ordered CAL trials, one uninterrupted DAY TCP process with ≤300-second probes; fresh observations have distinct deployments.
- **SC-006**: Failure/recovery tests prove ≤one copy retry, unchanged cap, reserved cleanup time, no launches over residual liabilities and no campaign repetition.

## Assumptions

- Authoritative research choices and merged contracts govern; implementation permission is not formal advisor approval or paid readiness.
- F11 already resolves cap activation, copy retry and intermediate skew. Healthy peers continue after protection only if full remaining work fits.
- Abrupt loss tests omit the finalizer; registration fakes cannot prove real expiry execution or guarantee recovered data.
- Runtime staging and exact source declaration projection are F09 engineering choices. F12 owns analysis/reports; entry dispatch integration is serialized.
- Paid prerequisite evidence remains unavailable until separately established. No cloud/credential/traffic operation runs in this stage.
