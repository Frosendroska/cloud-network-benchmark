# Feature Specification: Source-bound acquisition evidence

**Feature Branch**: `codex/f01r-acquisition-evidence`

**Created**: 2026-10-22

**Status**: Specified and clarified; implementation not run

**Input**: Coordinator-assigned serialized amendment to establish honest, immutable acquisition-window evidence for the approved Sockperf/iperf3 protocol. Base `7a70a3139c5ee9efa1f8ea13c3f6de40e9048121` contains merged F01R/F02R/F03. Own this feature's artifacts; implementation follows separately.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Know when acquisition was enabled (Priority: P1)

An experiment operator needs recorded tool state to distinguish actual probe acquisition and receiver measurement windows from process lifetimes or successful reply timestamps.

**Why this priority**: Without these windows, the required RTT coverage and loaded overlap remain unknown even when commands succeed.

**Independent Test**: Replay saved evidence through the producer with fake processes and clocks. Actual saved observations must determine the windows; supplied timing summaries cannot create them.

**Acceptance Scenarios**:

1. **Given** identified source-built tools and complete acquisition observations, **When** a short, acceptance, calibration or diagnostic unit closes, **Then** its conservative acquisition and receiver bounds are reconstructable from immutable files, including native filtering and omitted intervals.
2. **Given** only command launch/end, elapsed runtime or first/last accepted replies, **When** evidence is assembled, **Then** continuous acquisition remains unavailable with a reason.
3. **Given** a diagnostic probe restart or interrupted final chunk, **When** available evidence closes, **Then** windows remain separate, restart gaps remain visible and continuous TCP is not restarted.

---

### User Story 2 - Replay and challenge the evidence (Priority: P2)

A validator author needs a small stable contract that binds every interval to its producer, actual build, clock domain and original bytes.

**Why this priority**: An unbound interval is another unsupported claim and must not unblock complete/eligible results.

**Independent Test**: Round-trip the evidence and public schemas; mutate file bytes, identities, clocks and bounds to demonstrate rejection or explicit unavailability.

**Acceptance Scenarios**:

1. **Given** verified observations from the matching invocation and receiver session, **When** evidence is consumed, **Then** the consumer can intersect the same temporal support for coverage and RTT selection without relying on a scalar overlap field.
2. **Given** substituted files/builds, ambiguous sessions, conflicting duplicate records, mismatched boots, missing clocks or truncated records, **When** evidence is consumed, **Then** no unsupported complete result is possible and valid partial evidence survives.
3. **Given** an older V2 bundle with no acquisition evidence, **When** it is read, **Then** it remains readable and acquisition coverage remains unknown; no migration invents evidence.

### Edge Cases

- A tool dies before exporting stored observations; an interrupted write ends the file; stderr contains unrelated native text.
- A persistent receiver handles multiple sessions with the same stream count and endpoint; sessions must not be selected by timestamp proximity.
- Monotonic clocks differ across VMs or boots, UTC steps backward, mapping errors are unavailable, or uncertainty straddles a coverage threshold.
- Native RTT rows omit drops/reordered replies; zero throughput and slow replies must remain results rather than performance exclusions.
- Build cache contains an unpatched binary, a patch applies with offsets/fuzz, or only a version string matches.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Preserve the approved tool commands, transport, workload, direction, durations, warm-up/omit, probe-start delay, native outputs and all four mode sequences. Evidence capture MUST observe existing state rather than replace the benchmark or add traffic.
- **FR-002**: Record source-observed acquisition-enabled probe windows and actual receiver statistic boundaries, including native probe filtering and receiver omit reset. Process lifetime, notification receipt time, rounded runtime and accepted sample hulls MUST NOT independently establish continuous coverage.
- **FR-003**: Bind each observation and derived window to the run/deployment/unit or receiver session, command, VM role, boot/clock domain, immutable original bytes and actual upstream, observation modification and installed build identities. A matching version string alone MUST NOT establish semantics.
- **FR-004**: Represent conservative boundary uncertainty explicitly. Compute intervals only in a common proven clock domain; unavailable mappings or contradictory bounds MUST remain unknown. Clock error for aligned plots and acquisition coverage are separate decisions.
- **FR-005**: Preserve missing observations, malformed/truncated output, process failure, closed intervals and between-chunk gaps without extrapolating unobserved ends, bridging gaps or restarting diagnostic TCP. Unknown evidence MUST NOT become zero or a complete window.
- **FR-006**: Extend the shared contract compatibly so historical V2 input remains readable with unknown acquisition evidence, and expose replayable source references instead of trusting declared overlap totals. Verify original byte size/digest and identity before consumption.
- **FR-007**: Capture installed observation capability and its actual source/build identity on both VMs, including persistent receiver preparation. Preserve unmodified native outputs and report the observational build as modified; do not claim equivalence, negligible overhead, formal advisor approval or real acceptance from offline checks.
- **FR-008**: Provide credential-free, materialized producer and contract checks for all modes and failure boundaries. Fakes MUST use the actual observation grammar and producer parser; offline proof MUST NOT claim real tool timing or cloud behavior.
- **FR-009**: Declare a minimal serialized downstream validator follow-up using these source-bound windows for both coverage and sample selection, preserving the existing 99% diagnostic coverage and separate generation checks. This feature MUST NOT consume or modify unmerged F08 implementation.

### Key Entities *(include if feature involves data)*

- **Acquisition observation**: Tool-owned state timestamp or native statistics boundary, tied to one invocation/session and preserved log bytes.
- **Window evidence**: A conservative, source-bound interval with explicit time domain, boundary uncertainty, state meaning and unavailable reasons.
- **Observation build identity**: Exact upstream source, additive observation patch, build configuration/compiler and observed installed binary identity.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Each of the four mode fixtures either reconstructs source-bound windows and gaps or reports a specific missing prerequisite; no window is accepted solely from supplied interval declarations.
- **SC-002**: Every byte/identity/session/clock substitution and missing-boundary case in the acceptance matrix prevents unsupported complete coverage while retaining independently valid partial evidence.
- **SC-003**: Replay leaves original evidence byte-identical and produces deterministic conservative bounds, including exact-threshold uncertainty cases.
- **SC-004**: Existing V2 bundles remain readable and all approved command/sequence checks continue to pass; no cloud call, SSH execution or benchmark traffic is required.

## Assumptions

- Time coverage means source-observed acquisition-enabled lifecycle time: both probe sender and receiver loops enabled, restricted to the native filter window. It does not mean uninterrupted CPU scheduling, successful replies at every instant, a guaranteed inter-probe cadence or loss-free delivery. The approved probe-generation check remains independent.
- Small additive observation hooks in the existing source-built tools are an engineering measurement-evidence amendment. Research metrics, measurement algorithms, CLI settings and priorities remain fixed. Modified builds have new identities and still require separate paid acceptance; measurement perturbation is not asserted absent.
- Initial supported semantics are the inspected Sockperf 3.10 and iperf3 3.17.1 source commits in research.md. Other builds remain unknown until their semantics are checked; no cross-provider version-freeze gate is introduced.
- The coordinator serializes shared contract, bootstrap producer and F03 changes before F08 consumes them. F08's outstanding valid/eligible materialized replay is not declared passed by this plan.

## Clarifications

### Session 2026-10-22

- Ordinary engineering clarification: capture internal acquisition/state and native statistic boundaries, rather than inferring windows from process events or samples. Keep the protocol unchanged and label modified builds explicitly.
- Ordinary engineering clarification: preserve uncertainty by using inner acquisition support and conservative receiver bounds; unknown precision cannot be promoted at the 99% boundary.
- Scope clarification: this amendment owns contract/build/measurement producers; subsequent F08 changes belong to the coordinator's separately serialized consumer follow-up. No routine human artifact pause is required under WORKFLOW.md.
