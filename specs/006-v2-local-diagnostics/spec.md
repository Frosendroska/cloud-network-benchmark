# Feature Specification: F04 V2 local diagnostics

**Feature Branch**: `codex/f04-v2-diagnostics`
**Created**: 2026-10-22
**Status**: Ready for implementation after artifact checks
**Input**: F04 in Thesis `tracking/IMPLEMENTATION_PROMPTS.md`: both-VM local one-second diagnostics, once-per-VM metadata, clock mappings and aligned-plot eligibility.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Interpret measurements with local host evidence (Priority: P1)

As a researcher, I need the same host measurements from A and B throughout the executed mode so I can inspect CPU pressure and network counters alongside benchmark results.

**Why this priority**: Both endpoint histories are required context for every mode.
**Independent Test**: Linux fixtures and a simulated clock reproduce a day of sampling without traffic or real-day waits.

**Acceptance Scenarios**:

1. **Given** successive Linux snapshots for either VM, **When** a scheduled interval completes, **Then** its record contains average active CPU, maximum core CPU, steal, TCP retransmissions, separate RX/TX drops/errors and byte deltas with actual elapsed time.
2. **Given** unchanged counters, **When** a record is produced, **Then** observed zero remains zero; missing, malformed or decreasing counters have unavailable reasons rather than zero or negative deltas.
3. **Given** skipped deadlines or disappearing interfaces/cores, **When** sampling resumes, **Then** actual gaps/intervals remain visible and only affected evidence becomes unavailable; no replacement one-second samples are invented.
4. **Given** acceptance, short-study, calibration or diagnostic-day execution, **When** the caller starts and stops collection, **Then** collection spans that interval, continues across phase pauses/probe rotation and closes partial files on graceful interruption or failure.

### User Story 2 - Identify the actual tested VM (Priority: P2)

As a researcher, I need one static metadata record per VM so hardware, software, network settings and documented limits are traceable without claiming provider equivalence.

**Why this priority**: Interpretation requires actual machine identity rather than intended settings.
**Independent Test**: Guest fixtures and supplied deployment/build evidence reproduce metadata for all three providers, including missing and contradictory observations.

**Acceptance Scenarios**:

1. **Given** deployment and guest observations, **When** collection starts, **Then** exactly one metadata file per VM records CPU model, benchmark-interface driver/acceleration/MTU, congestion control, image, kernel, tool/source/build identity, provider, actual shape, region, zone, exposed physical zone and documented network limits.
2. **Given** missing acceleration, image or build evidence, **When** metadata is assembled, **Then** missing facts are explained and intended settings never become observed settings.
3. **Given** existing output destinations, **When** collection is attempted again, **Then** it fails without overwriting earlier evidence.

### User Story 3 - Judge one-second clock alignment honestly (Priority: P2)

As a researcher, I need monotonic/UTC observations and synchronization error estimates so aligned one-second plots use adequate clock evidence from both VMs.

**Why this priority**: A-local RTT needs no synchronized endpoint clocks; cross-VM alignment does.
**Independent Test**: Clock fixtures cover estimates at 100 ms, above 100 ms, unknown synchronization, UTC jumps and delayed reads.

**Acceptance Scenarios**:

1. **Given** synchronized A/B mappings with known errors at most 100 ms each, **When** mapping-pair eligibility is assessed, **Then** the pair meets the clock-error condition for aligned one-second plots.
2. **Given** missing, unsynchronized, malformed or larger-error evidence on either VM, **When** eligibility is assessed, **Then** the pair is ineligible with a reason, while host records and A-local RTT remain retained.
3. **Given** UTC jumps while monotonic time advances, **When** collection continues, **Then** actual UTC mappings remain intact and counter intervals/deadlines continue using monotonic time.

### Edge Cases

- First read is a baseline, not a fabricated delta. CPU counter decreases, zero ticks, missing steal and changed core membership remain explicit.
- TCP header/value mismatch, large counters, missing NICs and permissions must not corrupt unrelated measurements.
- Slow/missing synchronization utilities, local unsynchronized clock mode and non-finite estimates cannot create alignment eligibility.
- Early stop, interruption during writing and full/unwritable destinations preserve complete lines with partial-output evidence. Abrupt process/VM loss may leave an incomplete final line.
- A/B never share files; missing data or high host metrics never imply performance-based exclusion.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Collect both VMs locally at one-second scheduled intervals throughout the caller-supplied mode, including idle/loaded intervals; do not change benchmark ordering, durations or traffic.
- **FR-002**: Derive aggregate active CPU, maximum active core CPU and aggregate steal percentages from successive counters with declared denominator and treatment of guest/idle/wait/steal time.
- **FR-003**: Record nonnegative integer deltas for TCP retransmissions and the explicit benchmark NIC's receive/transmit drops, errors and bytes, preserving direction and units.
- **FR-004**: Preserve observed zero. Missing/malformed current or previous readings and counter decreases yield unavailable affected values with reasons. Decreases identify reset-affected fields without asserting cause, wrapping or clamping.
- **FR-005**: Record actual positive monotonic duration, sample UTC/monotonic time, skipped intervals and incomplete evidence; do not backfill or present a multi-second delta as a one-second count.
- **FR-006**: Capture US2 metadata once per VM/deployment, separating guest observations from supplied observed deployment/build records and documented limits; never infer acceleration from provider/type intent or absent commands.
- **FR-007**: Preserve actual image/kernel/tool/source/compiler/binary identity and provider-specific documented-limit evidence; explain missing facts and retain mismatched settings without configuring them away.
- **FR-008**: Capture monotonic/UTC mappings and observed synchronization source/error estimates on both VMs; missing/unsynchronized estimates stay unavailable, and UTC changes never alter elapsed sampling/deadlines.
- **FR-009**: Expose the at-most-100-ms mapping-pair condition: both VMs require observed synchronization and finite nonnegative estimates at most 100 ms. Clock eligibility remains separate from execution/data validity and full plot coverage.
- **FR-010**: Emit compact structured histories/metadata/clock evidence using existing V2 record contracts, deployment identity, VM roles and canonical diagnostic/metadata paths; declare extra clock artifacts explicitly.
- **FR-011**: Keep files on their producer VM until measurement ends. Flush/close on bounded stop, graceful interruption or failure and return available files, closure and failure evidence; never overwrite files or certify retrieval/integrity.
- **FR-012**: Replace local file, command and clock boundaries for offline Linux, missing/reset/gap, partial-write, deadline and day-length checks without credentials, cloud contact or real benchmark traffic.
- **FR-013**: Retain high host measurements; add no automatic performance-warning thresholds/labels, slow-run exclusions, mandatory ENA allowance counters, CloudWatch, intermediate format or monitoring platform.
- **FR-014**: Add no remote checkpoint copying, reservations, provisioning, provider queries, cleanup policy or research analysis. Caller owns paired start/stop, caps, transport, retrieval/checksums and cleanup; F04 supplies local collection and artifacts.

### Key Entities *(include if feature involves data)*

- **Diagnostic sample**: Deployment/VM identity, observed times/interval, host percentages/deltas and unavailable/reset/gap evidence.
- **VM metadata**: Once-per-VM actual static observations plus identified deployment/build/documentation evidence.
- **Clock mapping**: VM-local monotonic/UTC pair, synchronization source and estimated error or reason unavailable.
- **Collection outcome**: Completed/partial artifacts, closure and failures; never a retrieval certificate or research inclusion decision.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Known two-VM fixtures reproduce all ten diagnostic values/units, including zero, missing, decreasing and recovering counters, without invented data.
- **SC-002**: Normal simulated collection produces one record per complete second for 86,400 seconds; delays report actual intervals and omitted opportunities without catch-up bursts or real-day waits.
- **SC-003**: Each VM has one traceable metadata record; every required fact is evidenced or explicitly unavailable, and attempted reuse leaves earlier files unchanged.
- **SC-004**: Cases at 100 ms and immediately above, unknown/unsynchronized sources and changed UTC reproduce the alignment condition while preserving elapsed values and measurements.
- **SC-005**: Offline failure cases retain completed records and truthful closure/failure states; no check contacts clouds, launches benchmark traffic or treats a fake as real guest acceptance.

## Assumptions

- Constitution v2.0.0 and current routed Thesis V2 sources govern. The current F04 packet supersedes historical FLENT/memory-utilization lists; no research/source conflict was found.
- One benchmark NIC is explicit caller input; do not guess from interface order or sum management/loopback traffic. Counters describe host/interface scope, not isolated benchmark packets.
- Active CPU excludes idle, I/O wait and steal; steal is separate. Aggregate CPU uses aggregate deltas; per-core maximum requires complete stable membership. Exact arithmetic belongs in data-model.md.
- Caller starts collection before mode measurement and stops after its final interval. Warm-ups/pauses may remain; analysis selects actual measured windows.
- Synchronization observation is read-only. Missing supported clock-status observation means unknown error, not permission to configure a daemon or invent accuracy.
- F04 adds two local clock artifacts without shared-schema changes. F09 retrieves/checksums them and projects mappings; F08/F12 downstream consumption belongs to owning integration follow-ups.
- Available merged dependencies at base `7a70a3139c5ee9efa1f8ea13c3f6de40e9048121` include F01R/F02R/F03. No unfinished feature is imported; N, study windows and paid acceptance remain outside scope.

## Clarifications

### Session 2026-10-22

No critical ambiguities required human questions. The authorized workflow resolves ordinary choices in these assumptions and research.md. Scope, data/identity/lifecycle, interactions/failures, quality, integrations, edge cases, constraints, terminology and completion signals are clear. Accessibility/localization is inapplicable to this internal collector.
