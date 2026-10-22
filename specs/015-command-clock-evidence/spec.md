# Feature Specification: Honest command clock evidence

**Feature Branch**: `codex/f01r-clock-evidence`

**Created**: 2026-10-22

**Status**: Ready for implementation after artifact checks

**Input**: Coordinator-assigned serialized F01R_CLOCK amendment: preserve actual aware UTC command start/finish independently of monotonic elapsed duration, including UTC clock steps; retain compatibility, honest uncertainty, failure outcomes and redaction. Planning only in this stage.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Preserve a command across clock steps (Priority: P1)

As a framework operator, I need command evidence to survive a forward or backward UTC adjustment so a successful, failed, timed-out or interrupted command retains its real timestamps and elapsed duration.

**Why this priority**: Rejecting truthful timestamps can replace the primary command outcome with an evidence-construction error.

**Independent Test**: Supply known UTC and monotonic observations, including a finish UTC earlier than start, and recover the unchanged observations, elapsed duration and command outcome.

**Acceptance Scenarios**:

1. **Given** UTC 12:00:00 to 11:59:55 and monotonic 100 to 102 seconds, **When** evidence is constructed with monotonic duration, **Then** both UTC timestamps remain actual, elapsed duration is 2 seconds and signed UTC-minus-monotonic discrepancy is -7 seconds.
2. **Given** UTC 12:00:00 to 12:00:12 and monotonic 100 to 102, **When** evidence is recorded, **Then** elapsed duration is 2 and discrepancy is +10 seconds.
3. **Given** each of the four command outcomes, **When** a UTC step occurs, **Then** its existing exit-code, stdout/stderr and redaction semantics remain intact.

---

### User Story 2 - Replay durable timing without inventing certainty (Priority: P2)

As a downstream evidence consumer, I need the saved command and its native-evidence projection to retain the same timing basis, observations and discrepancy so replay cannot silently substitute UTC subtraction for elapsed time.

**Why this priority**: In-memory support alone does not repair saved manifests or validator handoffs.

**Independent Test**: Serialize and reload native command evidence and its enclosing V2 manifest/validator handoff; compare timing, provenance and redaction against the original.

**Acceptance Scenarios**:

1. **Given** monotonic command timing, **When** it is projected and round-tripped, **Then** producer/deployment/unit identity, actual UTC bounds, monotonic bounds, basis and discrepancy are retained and internally consistent.
2. **Given** missing synchronization/error estimates, **When** command clock discrepancy is available, **Then** it is described as observed clock disagreement, with no inferred synchronization accuracy, exact step cause or comparison eligibility.
3. **Given** an existing V2 command with no new timing fields, **When** it is read, **Then** the old ordered-UTC and elapsed-equals-UTC checks still apply; legacy V1 bundles remain rejected.

### Edge Cases

- Zero elapsed time, equal UTC endpoints, non-UTC timezone offsets and clock movement in either direction.
- Missing one monotonic endpoint, negative/reversed/non-finite monotonic values, non-finite elapsed/discrepancy values, booleans masquerading as numbers, contradictory duration/basis/discrepancy, naive timestamps.
- Native evidence whose top-level timing disagrees with the projected command result. Producers are responsible for keeping one monotonic origin; validators cannot detect mixed origins from bare endpoint values alone.
- Timeout/interruption retaining partial available output and secret redaction, without new outcome classes.
- Ordinary UTC-only evidence with both, one or neither UTC observation unavailable keeps existing missing-evidence behavior; partial legacy monotonic evidence remains representable without opting into monotonic duration.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Command evidence MUST retain actual timezone-aware UTC start and finish independently of elapsed duration; a backward UTC interval MUST be representable when complete consistent monotonic timing is supplied. Never clamp, sort or fabricate UTC bounds.
- **FR-002**: Timing MUST declare either the existing UTC duration basis or an explicit monotonic basis. Monotonic basis MUST require finite nonnegative ordered endpoints from one clock domain, finite nonnegative duration matching their difference within 0.001 seconds, and finite signed discrepancy equal to UTC delta minus monotonic elapsed within 0.001 seconds. Partial or contradictory opt-in timing and boolean numeric input MUST be rejected.
- **FR-003**: Existing commands omitting the extension MUST retain ordered UTC bounds and duration equality within 0.001 seconds. UTC basis MUST reject the new monotonic-only numeric fields. All supplied UTC timestamps MUST be aware and normalized to UTC; all command durations MUST be finite.
- **FR-004**: The synchronous command runner MUST capture its own monotonic bounds and actual UTC bounds on every currently returned success, failure, timeout and interruption path, derive duration from monotonic elapsed, and record signed discrepancy. Existing outcome/exit-code rules, command execution semantics, captured outputs and redaction MUST remain unchanged.
- **FR-005**: V2 native command evidence MUST support the same basis and discrepancy, using its existing monotonic evidence fields, and preserve consistent timing through redacted durable projection, serialization, manifests and validator inputs. Monotonic opt-in MUST require both actual UTC observations and agree with the timing fields in its projected result; inconsistent projections MUST be rejected. Existing unavailable-evidence records MUST remain readable under UTC basis.
- **FR-006**: Signed discrepancy MUST remain observation evidence, not an estimated UTC error, proof of clock synchronization, detection of every possible clock step or automatic data/comparison decision. Existing clock mappings/error estimates and status semantics MUST remain unchanged.
- **FR-007**: The amendment MUST remain additive within schema version 2 and update only affected active deterministic schema exports and contract documentation. Preserve historical specifications 001/002 and V1 rejection; no migration that invents monotonic evidence is allowed. Existing V2 payloads remain readable; pre-amendment strict readers need upgrading before consuming new fields.
- **FR-008**: Focused credential-free checks MUST cover backward/forward/no-step UTC, malformed clock inputs, all four outcomes, real synchronous-runner timing wiring under controlled clocks, native projection, durable JSON round-trip, enclosing provenance and redaction. No cloud/SSH/benchmark traffic or real long-duration waits are allowed.

### Key Entities *(include if feature involves data)*

- **Command result**: Actual command outcome, UTC endpoints, elapsed duration, explicit timing basis and optional monotonic/discrepancy observations.
- **Native command evidence**: Redacted command request/result plus producer, deployment/unit and build provenance, with its observed UTC/monotonic timing.
- **Clock discrepancy**: Signed UTC interval minus monotonic elapsed seconds; negative/positive values preserve observed disagreement without establishing its cause or external clock accuracy.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All four existing command outcomes retain their actual UTC endpoints and 2-second elapsed duration in both specified backward and forward clock-step cases, with exact expected signed discrepancy within 0.001 seconds.
- **SC-002**: Every numeric and structural malformed-input class in Edge Cases is rejected, while existing valid UTC-only evidence remains accepted with its original timing checks. Producer clock-origin correctness is an explicit provenance assumption rather than a detectable numeric property.
- **SC-003**: Saved and reloaded command evidence preserves all timing observations and producer/deployment/unit/build references; secret sentinels remain absent from durable output.
- **SC-004**: Focused offline checks, deterministic active-schema drift checks and the existing regression suite pass at the implemented head; no research protocol, status semantics or historical specifications change.

## Assumptions

- This is the ordinary engineering repair requested by the coordinator after F03 convergence T025; merged F01R `cef838e42fc620f90b5943f5a8d1528ddc0b0724` is the only dependency. No unfinished sibling is consumed.
- The two clocks are sampled sequentially around a command. Their observed discrepancy is retained exactly; this feature introduces no scientific clock-error threshold. The 0.001-second tolerance is the existing command-consistency tolerance and is not the research alignment gate.
- Native evidence's producer identifies its local monotonic domain; endpoint values from different processes/hosts must not be mixed. Monotonic origin has no cross-host UTC meaning.
- Missing synchronization evidence remains explicit in existing clock-mapping contracts. MeasurementTiming, TimingGap, diagnostics, measured-window coverage, schedule/skew policies and external exception behavior are outside this bounded amendment.
- The implementation owns only the narrow files declared in plan.md. This planner owns `specs/015-command-clock-evidence/` only; no app code, cloud operations, Thesis edits, PR or merge.

## Clarifications

### Session 2026-10-22

- Engineering resolution: retain strict UTC-only compatibility; new monotonic timing is an explicit opt-in with complete finite evidence and signed discrepancy. No timestamps are repaired.
- Engineering resolution: preserve producer-local monotonic observations through native DTOs, and validate duplicated timing rather than silently selecting one representation.
- Engineering resolution: discrepancy records uncertainty about the UTC mapping without asserting an exact clock step or changing validity/comparison outcomes. No research/source conflict was found.
