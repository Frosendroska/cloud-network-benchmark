# Feature Specification: F01R V2 shared contracts

**Feature Branch**: `codex/f01r-v2-contracts`

**Created**: 2026-10-21

**Status**: Planned; implementation not started

**Input**: User description: "F01R V2 shared-contract migration. Inspect merged F01/F02 and preserve their historical artifacts. Extend the existing config, manifest, provider, artifact, validator and fake-command contracts for acceptance, calibration, fresh short-study and retained continuous-day modes. Define stable experiment/campaign/observation/attempt/shared-deployment IDs, YAML identifiers referenced by CSV entries, actual commands/times/versions/checksums and dirty Git provenance. Replace FLENT artifact/phase assumptions with native Sockperf CSV and iperf3 JSON/JSONL, one-second diagnostics and separate execution/data/comparison status. Encode selected 60-second defaults, 10-second acceptance, common-N calibration state, all-cloud research, S1–S4 configs and priority admission metadata. Declare schedule/reservation/expiry interfaces for F11/provider drivers without implementing them. Preserve honest missing values, immutable paths and redaction. Review migration/legacy rejection explicitly and merge the new public contracts before dependent features; use only offline tests."

## Clarifications

### Session 2026-10-21

Engineering clarifications were resolved under the authorized autonomous workflow; zero human
questions were needed. These are implementation assumptions, not new research decisions.

- Q: How are historical inputs handled? → A: Active contracts accept only version 2; reject
  missing/version-1 inputs explicitly and preserve historical files without automatic conversion.
- Q: Can unresolved research intent be previewed? → A: Yes; pending N and schedule are explicit,
  previews list blockers, and validation/init grant no launch permission or substitute N/date.
- Q: How are identity and retained deployments represented? → A: Keep `run_id` as provider-attempt
  identity (`attempt_id` is its documented alias), add stable observation/deployment links, and
  keep one invocation/campaign across its providers. Retained units share that deployment.
- Q: Does F01R parse or execute schedules/admission? → A: No; it accepts a typed optional saved
  schedule-entry handoff and publishes safety records. CSV reading, budget decisions and execution
  remain later features; a YAML-only preview may have no executed row.
- Q: Must provider outputs carry known scenario and distinguish addresses? → A: Yes; scenario is
  required even for partial deployment; management host and benchmark private IPv4 are distinct.
  Usable connection records carry strict host-key/authentication references; failures stay partial.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Describe the actual V2 experiment (Priority: P1)

As the experiment operator, I can validate and preview an experiment configuration for the intended
mode and scenario, including unresolved calibration or scheduling prerequisites, without creating
resources or accidentally running a historical protocol.

**Why this priority**: Every later feature needs one trustworthy description of the experiment.

**Independent Test**: Resolve representative configurations for all four modes and S1–S4 with
local fixtures; compare expected settings and launch blockers without external actions.

**Acceptance Scenarios**:

1. **Given** an acceptance configuration, **When** I inspect it, **Then** it specifies ten measured
   seconds per phase, N=4 before calibration, the selected providers and native evidence.
2. **Given** a short-study configuration with pending common N and study time, **When** I preview
   it, **Then** 60-second phases remain fixed, unknown values stay unknown and unmet prerequisites
   are explicit; validation does not falsely admit a paid launch.
3. **Given** calibration or DAY, **When** I inspect the plan, **Then** calibration has the prescribed
   12 loaded trials on one retained pair/provider and DAY has one continuous TCP interval with
   local probe chunks; neither invents an idle phase or independent chunk deployments.
4. **Given** version-1 input, a duplicate/unknown field or inconsistent scenario, **When** I validate
   it, **Then** it is rejected with an actionable, redacted explanation before result allocation.

### User Story 2 - Preserve the identity of every attempt (Priority: P1)

As the researcher, I can initialize a local evidence bundle that links the configuration and any
schedule entry to the provider attempt and shared deployment, including incomplete attempts and
honest unavailable values.

**Why this priority**: Native evidence is useful only when its configuration and deployment unit
remain reconstructable after failure and across retained trials.

**Independent Test**: Initialize local bundles with injected time, identity and failures; inspect
snapshots, manifests and immutable destinations without executing external commands.

**Acceptance Scenarios**:

1. **Given** a three-provider experiment, **When** I initialize it, **Then** each provider attempt
   has unique immutable output paths and linked experiment/campaign/observation/attempt/deployment
   identities; the parent links exactly its initialized children.
2. **Given** retained calibration or DAY, **When** trial/chunk evidence is described, **Then** it
   shares its provider deployment; changing a trial or analysis window never creates a new pair.
3. **Given** unavailable Git metadata, dirty/untracked files or a failed snapshot write, **When**
   initialization finishes or fails, **Then** available evidence and primary/secondary failures
   survive without fabricated commits, timestamps, files or successful validation.
4. **Given** an existing bundle, **When** initialization collides, **Then** existing raw evidence
   and manifests remain untouched and the collision is reported.

### User Story 3 - Build independent features against one public contract (Priority: P2)

As a feature developer, I can consume one documented, versioned set of provider, timing, native
artifact, diagnostic, command, validation and admission records without depending on unmerged
siblings or interpreting a successful command as valid, comparable research.

**Why this priority**: Independent implementation depends on clear handoffs before parallel work.

**Independent Test**: Round-trip representative records through their published schemas, feed
materialized fake command outcomes and compare human/machine previews with resolved intent.

**Acceptance Scenarios**:

1. **Given** execution success but missing coverage or clock evidence, **When** its record is
   inspected, **Then** execution, data validity, comparison eligibility and cleanup remain separate.
2. **Given** a partial provider output or failed expiry registration, **When** the record is
   consumed, **Then** known identity/scenario and failure evidence remain available; missing
   connection or safety evidence cannot masquerade as readiness/admission.
3. **Given** a fake native-file transfer, timeout or interruption, **When** evidence is recorded,
   **Then** actual files, sizes/checksums, output redaction and primary failure identity agree.
4. **Given** the configuration matrix, **When** previews and schema checks run offline, **Then**
   every supported mode/scenario agrees across records and no cloud or benchmark action occurs.

### Edge Cases

- Missing or version-1 schema markers; former FLENT fields in an otherwise V2 input.
- Pending common N or UTC study windows; explicit frozen N without calibration source evidence;
  different N for different research providers; unsupported N or fixed protocol overrides.
- One provider in an ordinary research experiment; duplicate providers or mismatched provider keys.
- S2 with identical zones, S3 with no provider placement, or S4 with aggregate phase removed.
- Reused CSV row identity with different config identity/hash; conflicting scheduled timestamps;
  DAY chunks or calibration trials incorrectly acquiring separate reservation/deployment IDs.
- Local source/config/schedule snapshots containing secrets or path traversal; dirty Git state with
  untracked content and detached/packed-ref/linked-worktree provenance.
- Partial initialization, missing materialized artifact, checksum mismatch, command timeout,
  interruption, retrieval failure and cleanup failure; unavailable evidence is not zero.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Active configurations and durable V2 records MUST be explicitly versioned as V2,
  reject legacy/missing/unknown versions and unknown fields, and have published schemas matching
  their serialized form. Historical F01/F02 artifacts MUST remain unchanged and readable as history.
- **FR-002**: Each predefined YAML MUST have a stable configuration identifier and existing stable
  experiment identifier; schedule entries MUST reference that configuration identity without
  generating another YAML plan. Configuration source bytes and resolved intent MUST be traceable.
- **FR-003**: Configuration and resolution MUST distinguish `acceptance`, `calibration`,
  `short_study` and `diagnostic_day`, their fresh/retained deployment unit and mode-specific
  required evidence; unsupported combinations MUST be rejected.
- **FR-004**: The selected protocol MUST encode the fixed source settings: Sockperf UDP full RTT,
  64 bytes/1,000 mps, monotonic timing; receiver A-to-B iperf3 TCP CUBIC/sender zerocopy; 60-second
  short/calibration measurements, ten-second acceptance, five-second warm-up, ten-second pauses,
  60-second stabilization and notification-based two-second TCP lead. DAY MUST describe one
  86,400-second TCP interval and probe chunks ≤300 seconds, without scheduled TCP restarts.
- **FR-005**: Common-N state MUST distinguish pending from frozen evidence. Calibration MUST
  describe common orders 1,4,2,8 / 8,2,4,1 / 2,8,1,4 and the smallest common 2/4/8 reaching ≥95%
  of every cloud's best tested median, with explicit N=8/no-common-plateau fallback. Acceptance
  uses N=4 before calibration and frozen N afterwards; research cannot substitute provisional N.
- **FR-006**: Ordinary research and calibration/DAY configurations MUST select AWS/Azure/GCP
  concurrently. Only registered acceptance/failure/replacement contexts MAY select fewer clouds.
  Intent MUST preserve selected On-Demand Ubuntu 24.04 amd64 VM, disk, network, security and
  acceleration settings and provider-specific network-limit metadata without substitutions.
- **FR-007**: The complete S1–S4 matrix MUST preserve scenario terminology, locality and provider
  placement distinctions. Every short-study scenario, including S4, MUST contain all three phases;
  calibration and DAY are S1-only. S1/P0 and S3/P1 primary scope and S2/P2/S4/P3 admission gates
  MUST be explicit; configuration support is not paid authorization.
- **FR-008**: A supplied schedule entry MUST retain its stable row identity, config reference,
  original content/hash, UTC slot or honest pending state and priority/replacement linkage. A
  conflicting config reference or nonmatching supplied YAML/row timestamp MUST be rejected.
- **FR-009**: Contracts MUST describe complete-lifetime/cost reservation and independent provider
  expiry prerequisites, dated cost/cap inputs, protected budget and unresolved admission evidence.
  Encode 20-minute short/acceptance, 30-minute calibration and 1,460-minute DAY input caps; do not
  implement scheduling, budget arithmetic, expiry registration or a destructive copy policy.
- **FR-010**: Identity MUST distinguish design experiment, config, all-cloud invocation/campaign,
  provider observation, provider attempt/run and shared deployment. Retried attempts get new
  attempt/deployment IDs but preserve original observation and replacement linkage; retained
  trials/chunks share one deployment, campaign-level reservation and schedule entry.
- **FR-011**: Local initialization MUST retain exclusive immutable raw paths and atomic manifest
  updates under existing results roots, preserve collision/failure evidence and assign identities
  only after input validation. It MUST NOT execute deployment, remote access or measurement.
- **FR-012**: Parent/child manifests MUST preserve all initialized child links, available terminal
  evidence and independent primary, retrieval and cleanup failures; missing metadata, measurement
  and validation MUST remain explicitly unavailable/pending, including failed initialization.
- **FR-013**: Provenance MUST preserve source and resolved config identity, available executed row,
  implementation/Thesis revision, actual tool/image/build identity and source hashes, plus dirty
  Git state and a deterministic diff/content hash when available. Missing Git information MUST
  have an explicit reason, without exposing file contents or secrets.
- **FR-014**: Native artifact declarations MUST identify producer VM, phase/trial/chunk scope,
  required/optional/not-expected role, closed state, normalized relative path, size and SHA-256
  evidence. They MUST cover Sockperf CSV/logs/reported counts, iperf3 client JSON/JSONL and receiver
  JSONL, both-VM diagnostics/metadata, commands/timing, config/schedule and bundle checksums.
- **FR-015**: Timing records MUST support actual command/test-start/measurement/omitted intervals,
  monotonic-to-UTC mappings, clock error, overlap and gaps without inventing measured timestamps.
  Fixed clock/diagnostic coverage rules MUST remain representable; unresolved numerical short
  overlap and intermediate-skew policies belong to their later owning features.
- **FR-016**: Shared diagnostic/metadata contracts MUST name one-second both-VM CPU total/max-core,
  steal, TCP retransmission and interface drop/error/RX/TX deltas; units, missing/reset/gap evidence
  and actual CPU/NIC/acceleration/MTU/CUBIC/image/kernel/tool/network-limit metadata MUST be explicit.
  No collector or automatic performance-warning engine is part of F01R.
- **FR-017**: Execution state, cleanup state, data validity and comparison eligibility MUST be
  separate, with machine-readable reasons. Successful execution MUST NOT imply valid/comparable
  data; unvalidated initialized bundles MUST NOT be marked valid or eligible.
- **FR-018**: Provider contracts MUST retain exactly A/client and B/receiver, known scenario,
  provider-locality intent, honest partial output, management versus private benchmark addresses,
  authenticated host-identity evidence and runtime credential references without secret values.
- **FR-019**: Shared handoffs MUST define schedule entry, reservation request/evidence and provider
  expiry request/evidence including provider stop/deallocation semantics, lifetime/cost source,
  registration-before-create and unresolved cleanup status. Owning later features implement them.
- **FR-020**: Existing shell-free command/fake boundaries MUST remain usable for success, failure,
  timeout and interruption. Declared fake artifacts MUST materialize before size/hash success can
  be claimed; durable evidence MUST redact secrets from arguments, outputs, errors and snapshots.
- **FR-021**: Validator inputs MUST describe the actual mode, measurement units, native artifacts,
  identities, integrity/timing evidence and independent statuses. Native parsing, statistical
  calculations and eligibility decisions themselves remain F08's work.
- **FR-022**: Existing validate/resolve/dry-run/local-init flows MUST expose V2 intent in human and
  machine output, including pending N/schedule/admission evidence, and MUST perform no cloud or
  measurement actions. Preview MUST reserve no identity/path; local-init MUST stay local.
- **FR-023**: Focused offline unit/contract/integration and failure fixtures MUST prove every mode,
  S1–S4/provider combination, serialization/schema agreement, legacy rejection, honest incomplete
  evidence, immutable initialization, redaction and no external side effects.
- **FR-024**: Migration MUST preserve useful F01/F02 guarantees and repair the three recorded
  baseline contract/traceability failures against the chosen V2 rules, without weakening checks
  or changing remote bootstrap/readiness/retrieval behavior owned by F02R.

### Key Entities *(include if feature involves data)*

- **Experiment configuration**: stable design/config identity, explicit mode, provider/scenario
  intent, fixed protocol, calibration state and source-backed safety/admission requirements.
- **Schedule entry**: one identified experiment row referencing the config, with UTC/pending slot,
  original row evidence, priority and any replacement relationship.
- **Campaign and provider attempt**: one invocation grouping provider observations, each with a
  unique attempt/run and deployment; trial/chunk units identify retained deployments.
- **Native evidence**: artifact declarations, actual command/timing records, diagnostics/metadata,
  integrity and provenance, all distinguishing observed from unavailable values.
- **Validation and safety handoffs**: independent execution/data/comparison/cleanup status,
  reservation and expiry requests/evidence; these express later-feature obligations.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All four modes and all four scenarios have representative valid and invalid fixture
  coverage; every resolved/previewed plan states its actual deployment unit and prerequisites.
- **SC-002**: Every representative configuration and initialized record agrees with its published
  schema; all legacy-version and protocol-conflict cases are rejected before output allocation.
- **SC-003**: All injected collision/interruption/partial-initialization cases retain available
  evidence and existing bytes, with no false success or invented observation.
- **SC-004**: All preview/initialization/contract checks finish without cloud credentials, cloud
  calls, benchmark traffic or paid resources; previews create zero result files.
- **SC-005**: Every public handoff has one declared owner, explicit identity/status semantics and
  offline acceptance evidence; the full existing suite has no unexplained migration failures.

## Assumptions

- Current sources: Thesis research revision `d45655364ef21423cfe6cae177e1eec23b44acf9` plus subsequent
  factual tracker updates; constitution 2.0.0 and merged R00 base `e7fc15f` govern implementation.
- Public contracts migrate to version 2; no dual execution engine or automatic conversion of old
  bundles is required. Historical specifications/schemas remain preserved.
- Pending N and UTC windows are valid planning inputs, not launch-ready research. Config validation
  describes intent; actual admission remains later F11/F09 work. No research result is fabricated.
- No application runner, provider Terraform, native parser, collector, scheduler, expiry mechanism
  or reusable analysis skill is implemented here. F02R migrates remote behavior after F01R merges.
- Offline contract changes warrant focused tests. The known legacy baseline is 203 passed/3 failed,
  not V2 readiness. No real traffic, cloud credentials, paid admission or advisor approval is implied.
