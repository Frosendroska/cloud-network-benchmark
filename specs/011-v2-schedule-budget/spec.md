# Feature Specification: F11 V2 schedule and budget admission

**Feature Branch**: `codex/f11-v2-schedule-budget`

**Created**: 2026-10-22

**Status**: Ready for implementation after artifact checks

**Input**: F11 CSV scheduling and complete-workflow budget admission under the current V2 design, without provisioning or paid execution.

## Context

The operator needs an offline decision about whether an identified experiment can occupy a shared UTC slot and reserve its complete cost and lifetime. The research run register remains authoritative. A reservation permits the later lifecycle to register provider expiry; it does not establish that expiry, cloud acceptance, calibration, diagnostic coverage or paid authorization exists.

Scope includes saved CSV rows referring to predefined configuration identifiers, primary schedule checks, a local restart-safe accounting record, priority/replacement rules, lifetime checks and auditable decisions. Provisioning, provider expiry mechanisms, measurement, copying, normal deletion, cloud billing lookup and thesis interpretation remain with their owners. No scheduler service, database, queue, remote state or additional experiment is introduced.

## Clarifications

### Session 2026-10-22

- Engineering resolution: activate the original lifetime cap when the durable reservation is accepted, immediately before expiry registration. Registration, setup, stabilization, measurement, final copy and cleanup consume that cap; no later event restarts it.
- Engineering resolution: require the complete remaining workflow to fit before the earliest selected provider's compute-expiry trigger, including registration and cleanup allowances. An earlier trigger reduces usable time without enlarging the source cap.
- Engineering resolution: preserve actual per-phase provider starts; skew ≤60 seconds meets target, >60 through 300 seconds remains eligible with a target-missed timing finding, >300 seconds is excluded from matched-time comparison, and missing starts are unavailable. Execution and data validity remain separate.
- Engineering resolution: reuse the merged bounded-copy policy: at most one retry per failed artifact inside the original cap minus cleanup allowance, then request normal cleanup and preserve incomplete evidence. No automatic remote retention/recovery or new copying timer is implied.
- Engineering resolution: serialize all invocations against one local accounting file; an interrupted active reservation is never silently freed or relaunched. A duplicate identity with different inputs is rejected.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Inspect the intended schedule (Priority: P1)

The operator validates saved schedule entries against existing configurations before reserving any resources.

**Why this priority**: Wrong dates, duplicated slots or invented study windows invalidate both experiment identity and accounting.

**Independent Test**: Local configuration and schedule fixtures yield an ordered preview with exact row/source identity, pending evidence, missed slots and precise rejections, without invoking any external command.

**Acceptance Scenarios**:

1. **Given** 28 P0/S1 and 28 P1/S3 entries for four named windows on seven consecutive days, **When** their dates and UTC starts are evidenced, **Then** matching day/window entries share the same UTC start and study week, have distinct experiment execution identities/namespaces, and demand four VMs per cloud at an overlapping slot.
2. **Given** an unresolved common N or exact UTC start, **When** the schedule is inspected, **Then** intent is retained as pending and launch admission is blocked; no value is invented.
3. **Given** duplicate entry IDs, unknown configuration IDs, conflicting configuration identity, altered row evidence or secret-bearing fields, **When** entries are inspected, **Then** the invalid schedule is rejected before a reservation is created.
4. **Given** DAY or calibration, **When** its entries are inspected, **Then** each retained experiment has one schedule entry and one eventual reservation; diagnostic bins and calibration trials cannot become separate launches.

### User Story 2 - Reserve the whole experiment safely (Priority: P1)

The operator reserves an experiment while protecting committed primary work and all simultaneous invocations.

**Why this priority**: Under-reservation can consume the protected buffer or leave resources running after the usable lifetime.

**Independent Test**: Fake time and sourced local cost inputs prove budget/time acceptance and refusal at exact boundaries, including simultaneous P0/P1 reservations and interrupted persistence.

**Acceptance Scenarios**:

1. **Given** identified dated cost inputs, sufficient remaining budget and complete-workflow allowances, **When** admission succeeds, **Then** one durable reservation includes source identity, cost, activation time, cap end and policy before any expiry registration or resource creation.
2. **Given** incurred charges, other active reservations and remaining primary/replacement/tax/uncertainty allowances, **When** a candidate would make the working commitment exceed $250 or total including the $50 protected buffer exceed $300, **Then** admission is refused without partially committing the candidate.
3. **Given** an earlier provider expiry trigger, **When** registration/setup/measurement/copy/cleanup cannot all finish before that trigger, **Then** admission or a later stage-fit check refuses further work without extending the original 20/30/1,460-minute cap.
4. **Given** a process restart, race or interrupted write, **When** the same reservation is requested again, **Then** the original reservation is returned only for exactly matching inputs; an uncertain/corrupt record blocks admission and cannot cause a duplicate launch.

### User Story 3 - Reconcile failures and constrained follow-up work (Priority: P2)

The operator records missed, failed, interrupted and completed attempts without losing the original costs or admitting unapproved work.

**Why this priority**: Failure evidence and residual resources must remain visible before another experiment is considered.

**Independent Test**: Local events prove idempotent reconciliation, unresolved-cleanup blocking and bounded conditional replacement eligibility without repeating an experiment.

**Acceptance Scenarios**:

1. **Given** failed or unknown cleanup anywhere in the accounting scope, **When** any new launch reservation is requested, **Then** it is blocked until identified evidence verifies all billable experiment and safety resources are absent.
2. **Given** a failed-cloud short S1/S3 attempt, **When** a conditional replacement is requested, **Then** it references that failure and provider, reserves another complete fresh deployment, allows at most one replacement of that failed observation and three per provider, and leaves the original costs intact.
3. **Given** DAY, calibration, S2/S4 or a successful original attempt, **When** automatic repetition/replacement is requested, **Then** it is refused. P2/P3 remain offline configuration support until sourced higher-priority execution, analysis and budget review explicitly admit their scope.
4. **Given** observed actual measurement starts or a missed slot, **When** timing is recorded, **Then** skew is classified by the declared boundaries and a missed slot remains missed; no catch-up launch is created.

### Edge Cases

- Exact $250/$300 boundary; missing, negative, non-finite or boolean money; underestimated cost; duplicate charge events; unknown final billing; expired reservation with resources still present.
- UTC offsets, naive timestamps, clock reversal/step, controller restart with an unusable prior monotonic origin, absent provider starts and exact 60/300-second boundaries.
- Simultaneous processes; duplicate entry across changed files; same ID with changed config/source; corrupt, missing previously initialized or truncated ledger; interruption before/after durable commit.
- Expiry registration fails after creating safety resources; unknown cleanup; result-copy failure; a stage allowance exceeds time left; unregistered/late expiry evidence.
- Pending study windows/N; partial day evidence; illustrative optional rows; repeated replacement of a replacement; partial provider failure and independent P0/P1 namespaces.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST load saved CSV entries referencing predefined YAML configuration identifiers, retain exact source/header/row bytes and their hashes, validate unique safe fields/IDs and resolve matching configuration/experiment/priority/replacement identity without generating another YAML plan.
- **FR-002**: Primary-study validation MUST require 28 S1/P0 and 28 S3/P1 entries, four named windows per day for seven consecutive days, matching UTC starts in the same week, all three selected clouds and independent execution/resource/state/result namespaces. It MUST report simultaneous demand for four VMs per cloud without claiming quota availability.
- **FR-003**: Pending N, exact UTC dates/windows, required schedule, paid acceptance and diagnostic/calibration prerequisites MUST remain explicit admission blockers. Inspection MUST still support pending intent and all S1–S4 configurations; no research evidence or paid authorization may be fabricated.
- **FR-004**: Calibration and DAY MUST each retain one experiment identity, one schedule row and one reservation across their trials/chunks/bins. They MUST preserve their distinct retained-pair protocols and MUST NOT be expanded into short three-phase experiments.
- **FR-005**: Reservation activation MUST durably reserve complete workflow cost before expiry registration and resource creation, set cap start once at activation, and retain the original 1,200-second acceptance/short, 1,800-second calibration or 87,600-second DAY cap including registration, setup, 60-second stabilization, mode-specific measurement, final retrieval and cleanup.
- **FR-006**: Admission and each later stage-fit decision MUST require identified finite nonnegative remaining stage allowances to fit before both cap end and the earliest selected provider compute-expiry trigger. No expiry failure, retry, restart or clock step may extend the original cap; no resource creation is authorized without matching registered expiry evidence for every selected provider.
- **FR-007**: Costs MUST use identified dated register/config inputs covering compute, disks, addresses, traffic, bulk export where applicable, independent-expiry and cleanup support, tax and uncertainty, with explicit units and inclusion rules. No free tier or missing price may be assumed. The $147.82 October 14-input primary forecast MUST remain a dated forecast, not a fresh quote or actual bill; refreshed input/review evidence is required before paid execution.
- **FR-008**: Accounting MUST protect remaining primary work, conditional replacements, tax, uncertainty and the $50 buffer while reconciling incurred costs and all active reservations within $300 ($250 working allocation). Moving work/allowances into a reservation MUST NOT double-count it; recording incurred costs MUST NOT release the still-unspent reservation ceiling. Unknown charges retain a conservative sourced allowance.
- **FR-009**: Concurrent invocations MUST share a durable serialized local accounting record. Identical reservation/event retries MUST be idempotent, changed inputs under the same identity MUST fail, and crashes/unknown persistence MUST not cause duplicate reservations, launches or silent release. Initialization is explicit; loss/corruption of an initialized record blocks further admission.
- **FR-010**: Any failed or unknown billable-resource cleanup MUST block all new launch reservations in the shared accounting scope. Cap expiry, compute stop/deallocation, absent result data or passing measurements MUST NOT imply complete deletion or release of unresolved cost liability.
- **FR-011**: Conditional replacement admission MUST be limited to the failed provider of an original failed short S1/S3 observation, one replacement per failed observation and at most three per provider across S1/S3. It MUST link immutable original failure identity, preserve original costs, reserve a fresh complete deployment, and prohibit replacement chains and automatic DAY/calibration/S2/S4 repeats.
- **FR-012**: P2/P3 executable configurations and dated estimates MUST be inspectable offline but excluded from primary commitments and launches until sourced higher-priority execution, analysis and budget review admit an explicit scope. Mere headroom, a deferred example or a supported configuration MUST NOT authorize execution.
- **FR-013**: Skew MUST use actual UTC measurement starts per phase across selected providers: ≤60 seconds target met; >60 through 300 seconds eligible with explicit target-missed finding; >300 seconds excluded; missing starts unavailable. Timing evidence MUST preserve execution, data validity and comparison eligibility separately.
- **FR-014**: A slot not activated by its scheduled measurement start MUST be recorded missed and MUST NOT trigger automatic catch-up. Preparation may start before the slot only within an explicit sourced setup allowance and the complete cap; actual starts and deviations remain evidence.
- **FR-015**: Failed copying MUST reuse the bounded final-copy/cleanup policy: at most one retry per failed artifact inside the remaining original cap minus cleanup allowance; preserve available local evidence and request normal cleanup on every terminal path. No added copy timer, periodic off-VM checkpoint, automatic disk retention/recovery, cap increase or claimed recovery guarantee is allowed.
- **FR-016**: Every preview, refusal, activation and reconciliation MUST retain applicable config/schedule/cost identities, policy, timestamps, reason codes and original failure links, redact secret material, distinguish missing evidence from zero and expose existing reservation/expiry value contracts for later lifecycle integration. Offline verification MUST use fake clocks, local files and replaceable boundaries, without credentials, cloud contact, external benchmark traffic or provisioning.

### Key Entities

- **Schedule entry**: Exact saved row, configuration reference, experiment identity, priority, optional evidence-dependent UTC start/window and replacement link.
- **Cost input snapshot**: Scoped source identity, dated monetary/volume/time envelopes, inclusion rules and refresh/review state; never an invented workbook revision.
- **Reservation**: One complete experiment's accounting identity, cost ceiling, cap activation/end, state and source/policy evidence.
- **Local accounting record**: Serialized primary commitments, charge events, active unspent ceilings, conditional replacement counts and cleanup liabilities shared by concurrent invocations.
- **Admission decision**: Allowed/pending/refused/missed evidence and later stage-fit/expiry guard result; it does not itself launch resources.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Offline examples retain all 56 primary experiment identities in 28 concurrent paired slots, reject duplicate/misaligned entries and keep unknown N/UTC values pending without allocating resources.
- **SC-002**: Boundary examples prove no accepted working commitment exceeds $250 or consumes the protected $50, with incurred, active and remaining commitments each counted exactly once.
- **SC-003**: Fake-time examples prove original 20/30/1,460-minute caps and earlier provider triggers cannot be extended; insufficient complete-workflow time or missing expiry blocks creation eligibility.
- **SC-004**: Restart, concurrent invocation and interruption examples produce one reservation per identical experiment and no released liability while cleanup/charges are unresolved.
- **SC-005**: Replacement and optional-scope examples enforce all declared limits; retained trials/bins never consume extra launches, and all timing boundaries/missed slots produce explicit outcomes.
- **SC-006**: Focused offline unit/contract/integration checks pass with auditable source/reason evidence and no cloud commands, credentials, paid action or real traffic; passing fakes establish only offline admission behavior.

## Assumptions

- The coordinator assigns the branch/worktree and integrates shared amendments; this feature changes no shared DTO/schema or sibling implementation. Existing F01R value contracts are sufficient when combined with feature-local accounting/policy records.
- Small local files shared on one controller filesystem are sufficient for the planned concurrent invocations. Multi-host/distributed scheduling and calendar services are outside scope.
- The supplied connected-source extracts cover named Budget/Runs/Cost-model ranges and retain an acquisition date. Their exact scoped contents can be hashed; they do not prove a full workbook hash/revision, current prices or paid authorization.
- The current merged AWS handoff supplies a dated $0.0139 per involved region ($0.0278 for S4) expiry-support envelope; F11 includes it explicitly within sourced uncertainty funding without counting that funding twice. Other providers require their own supplied dated support inputs before paid admission; they are never assigned zero by default.
- Schedule slots mean measurement-start targets. Preparation uses explicit bounded allowances before the slot; a local preview/claim cannot promise real readiness, quotas, provider timing or paid execution.
- Formal advisor approval is not recorded. Evidence-dependent N, exact UTC windows, optional paid scope and incomplete-day review remain research inputs, not engineering defaults.
