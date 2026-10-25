# Feature Specification: F10 V2 offline readiness gate

**Feature Branch**: `codex/f10-v2-offline-gate`
**Created**: 2026-10-25
**Status**: Ready for implementation after artifact checks
**Input**: F10 packet in Thesis `tracking/IMPLEMENTATION_PROMPTS.md`: prove the integrated V2 instrument from a clean checkout without credentials, cloud calls or real benchmark traffic; retain reproducible evidence and a separate paid-acceptance runbook.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Reproduce the offline gate (Priority: P1)

A maintainer runs one local command against a clean integrated revision and receives the full offline check results, exact environment and retained logs.

**Why this priority**: A prior feature's test count cannot establish the final integrated instrument's readiness.

**Independent Test**: Run the command with prepared local tools; inspect its exit status, evidence and unchanged checkout. Inject a failed check, missing tool, dirty checkout and unexpected skipped check to demonstrate that none becomes success.

**Acceptance Scenarios**:

1. **Given** clean committed sources and prepared local dependencies, **When** the gate runs, **Then** all required suites, provider static checks and replay checks run and the evidence identifies the exact revision and each command/result.
2. **Given** a dirty checkout or unavailable prerequisite, **When** the gate starts, **Then** it reports incomplete validation with a nonzero exit and never installs tools or launches resources.
3. **Given** a failing, missing or unexpectedly skipped required check, **When** results are evaluated, **Then** the gate exits nonzero and preserves available logs and the failure reason.
4. **Given** the two identified Linux process checks on macOS, **When** those checks skip because `/proc` is unavailable, **Then** each is recorded as not run, separately from passed offline checks and from future Linux/cloud acceptance.

### User Story 2 - Follow produced native evidence through reports (Priority: P1)

A maintainer checks that all-provider simulated acceptance, calibration, short-study and diagnostic-day lifecycles produce traceable bundles which the existing validator and analysis driver can replay.

**Why this priority**: The final gate must connect the integrated instrument's saved outputs to its reports and preserve honest failures.

**Independent Test**: Run the existing default all-provider factories with fake commands/time, feed their saved bundle evidence to analysis, compare repeated reports and confirm source files remain byte-identical.

**Acceptance Scenarios**:

1. **Given** each of the four modes across AWS, Azure and GCP, **When** the simulated lifecycle completes, **Then** mode-specific native evidence, identifiers, configuration/schedule snapshots, provenance, integrity and cleanup state remain traceable through validation and analysis.
2. **Given** identical saved inputs and analysis revision, **When** reports replay, **Then** summaries and generated plots are byte-identical and raw inputs remain unchanged.
3. **Given** server/controller loss, readiness exhaustion, missing probe-start overlap, probe gaps, receiver-stream failure, incomplete/corrupt retrieval, budget/cap exhaustion, expiry failure or residual resources, **When** the relevant offline checks run, **Then** primary/secondary failures, retained evidence, cleanup attempts and launch blocking are represented accurately.
4. **Given** calibration or a retained diagnostic deployment, **When** a report is generated, **Then** trials/windows retain their shared deployment, calibration comparison limits and unknown costs; no idle baseline or latency inflation is invented for these modes.

### User Story 3 - Hand off to separate paid acceptance (Priority: P2)

A future authorized operator can identify exactly what remains unproved and follow a registered acceptance/calibration/storage sequence without treating F10 as launch permission.

**Why this priority**: Offline readiness is the stopping point of this implementation workflow.

**Independent Test**: Review the runbook against current sources and actual local command help; every paid step has registration, evidence and cleanup criteria, and no paid step is executed during F10.

**Acceptance Scenarios**:

1. **Given** completed offline evidence, **When** an operator reads the runbook, **Then** it distinguishes unproved provider/native behavior from offline proof and names the registered all-cloud ten-second phases, server-failure/controller-loss checks, common-N calibration and measured disk/export prerequisites.
2. **Given** pending research N, UTC windows, price refresh, approvals or unresolved cleanup, **When** the handoff is inspected, **Then** those remain launch blockers; no defaults, automatic paid launches or automatic campaign repeats are introduced.

### Edge Cases

- A command fails or is interrupted after earlier checks passed: retain earlier results and explicit incomplete status.
- A test is deselected, a report is missing/malformed, or a named required scenario is absent: do not accept an otherwise zero test-process exit.
- A tool/provider cache is missing: record not run and stop; tool preparation belongs outside the offline command.
- Tests leave source/config changes or the revision changes during validation: reject the run as clean-revision evidence.
- Unknown billing remains unknown after proven resource absence; successful fake cleanup is not a provider billing guarantee.
- Evidence output already exists or would overwrite sources: fail before overwriting it.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The gate MUST validate a clean, committed checkout and verify the same revision and clean source state after execution.
- **FR-002**: The gate MUST require no credentials and make no cloud, registry/download, real SSH/SCP or benchmark-traffic calls; missing prerequisites MUST NOT trigger installation or paid activity.
- **FR-003**: The gate MUST run the full unit, contract and integration suite, including relevant command/source-patch, diagnostic, provider, admission, lifecycle, native-validation and analysis checks.
- **FR-004**: The gate MUST check all six provider pair/protection roots for formatting and validity and run all existing provider mock-plan suites; it MUST state which checks exist rather than inventing absent suites.
- **FR-005**: The gate MUST prove all-provider simulated acceptance, twelve-trial calibration, fresh short-study and continuous 86,400-second diagnostic modes with fake time and the existing default factories.
- **FR-006**: Required coverage MUST include server/controller loss, readiness deadlines, notification-based actual overlap, probe chunk gaps, receiver streaming, incomplete/corrupt retrieval, reservation/cap exhaustion, expiry failure and residual-resource launch blocking.
- **FR-007**: Required coverage MUST retain native files, separate diagnostic counts, experiment/run/observation/attempt/deployment/source identities, executed configuration/schedule identity, actual timing/build/Git provenance and secret exclusion.
- **FR-008**: Saved outputs of the integrated four-mode lifecycles MUST feed the existing analysis driver, retaining mode/provider/source identity, calibration comparison limits, separate retained-day context and unknown costs.
- **FR-009**: Repeated summaries and plots from identical inputs/revision/environment MUST be byte-identical; analysis MUST leave native files and saved source evidence unchanged.
- **FR-010**: Evidence MUST identify the full tested revision, clean-state checks, relevant source revision, host/tool/dependency versions, exact validation invocation, per-check command/exit/duration/log location, test counts, log hashes and limitations without exporting secrets or the whole environment.
- **FR-011**: Failed, interrupted, missing or unavailable required checks MUST produce a nonzero gate result and retain available evidence; the gate MUST NOT relabel not run as passed.
- **FR-012**: Only the two specifically named Linux `/proc` process-identity checks may remain not run on macOS, with their actual reasons and future Linux acceptance obligation recorded. Any other required skip or absent named scenario MUST make the gate incomplete and nonzero. Passed offline proof and known native-platform gaps MUST be separate fields.
- **FR-013**: A separate paid-acceptance runbook MUST specify source-backed registration/refresh/preflight, all-cloud ten-second phases with N=4 before calibration, server/controller failures, provider expiry/full cleanup, fixed calibration selection and measured disk/export prerequisites before DAY/research.
- **FR-014**: F10 MUST stop after offline validation and handoff; it MUST NOT authorize or execute paid acceptance, cloud experiments, calibration traffic, diagnostic traffic, new research N/windows/prices or automatic retries.
- **FR-015**: F10 MUST reuse existing contracts, factories, fixtures and analysis, preserve all fixed protocol/provider/budget/retrieval rules and add only validation integration/evidence and handoff documentation.
- **FR-016**: Final delivery MUST retain successful clean-checkout evidence at the final integrated code revision. A subsequent documentation-only commit MUST identify the tested revision and demonstrate unchanged executable/test/config/infrastructure content; any behavioral change requires rerunning the gate.

### Key Entities

- **Gate invocation**: committed revision, clean state, environment, command, timestamps and overall offline outcome.
- **Check result**: named required scope, exact command, exit, duration, logs/checksums and passed/failed/not-run status.
- **Platform gap**: exact skipped test identity/reason, host and remaining native acceptance obligation.
- **Replay evidence**: saved input/source identities and hashes, analysis revision and generated output hashes.
- **Paid handoff**: registered stages, prerequisites, required real evidence and unresolved launch blockers.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: One documented command exercises every required offline scope at a clean integrated revision and preserves enough evidence to reproduce its result.
- **SC-002**: All four modes retain AWS/Azure/GCP identities through saved bundles, validation and reports; all eleven required failure categories have named executed coverage.
- **SC-003**: Repeated integrated reports match byte for byte and all source hashes remain unchanged.
- **SC-004**: Every check is classified accurately; injected failures, missing prerequisites/scenarios, dirty/revised sources and unexpected skips cannot yield a successful gate.
- **SC-005**: The paid handoff accounts for every registered acceptance/failure/calibration/storage stage, names native/provider limitations and leaves zero cloud or benchmark launches performed by F10.
- **SC-006**: Delivered evidence identifies the exact tested revision/environment and either that final revision or explicit unchanged executable-content proof for a later documentation-only delivery.

## Assumptions

- F09 PR #22 and F12 PR #21 are merged in base `eb0be0b40d2b38607212cad3761f454295122f9b`; historical test totals are context, not F10 results.
- Tools and exact locked providers can be prepared locally before the validation command. Filesystem-only provider configuration prevents registry fallback; offline validation itself performs no installation.
- Synthetic evidence-bound calibration N, schedule/cost inputs and fake clocks are explicitly test-only. Real N/windows and refreshed paid inputs remain pending.
- The two named macOS `/proc` gaps are declared limitations of native process execution, while all applicable fake/static safeguards must pass. They supply no Linux guest or cloud proof.
- The coordinator owns final integration, evidence retention and delivery; the planner owns only this feature's artifacts and the implementer later owns the declared validation/test/docs paths.

## Clarifications

### Session 2026-10-25

No critical research/source ambiguity remains. The authorized workflow resolves routine engineering choices without human artifact pauses: reuse the full existing suite; connect actual lifecycle bundles to F12; distinguish the exact two macOS native skips; require prepared local tools with no registry fallback; record rather than invent paid inputs. Missing/failed/unexpectedly skipped required checks are incomplete. Source precedence and protocol remain unchanged.
