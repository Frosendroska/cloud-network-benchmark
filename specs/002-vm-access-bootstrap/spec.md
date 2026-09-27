# Feature Specification: VM Access, Bootstrap, Readiness, and Retrieval

**Feature Branch**: `002-vm-access-bootstrap`

**Created**: 2026-09-27

**Status**: Draft

**Input**: User description: "F02 VM access, bootstrap, readiness, and retrieval. Define the provider-neutral boundary after Terraform returns connection outputs. Cover minimal bootstrap for pinned FLENT/netperf and Linux tools, SSH command execution, SCP retrieval, VM A client and VM B server roles, cloud-init completion, private-path and benchmark-server readiness, timeouts, and clear failure evidence. Test remote sequences with fakes. Exclude provider Terraform resources, benchmark phase semantics, and real-cloud tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Prepare the VM pair for benchmarking (Priority: P1)

A lifecycle controller receives two provider-produced connection descriptions and prepares VM A as the client and VM B as the server with the minimal pinned benchmark and Linux tooling required by later features.

**Why this priority**: Later benchmark execution is only trustworthy when both VMs have the same known baseline and their roles are explicit.

**Independent Test**: Use fake remote actions to render bootstrap for both roles, execute the expected commands, and verify that successful completion records the installed tool versions and role-specific setup without contacting a cloud.

**Acceptance Scenarios**:

1. **Given** valid connection outputs for VM A and VM B, **When** preparation runs, **Then** bootstrap is rendered from pinned FLENT/netperf and Linux-tool inputs, applied to both VMs, and the roles are recorded as client/traffic-generator for A and server/receiver for B.
2. **Given** bootstrap succeeds on both VMs, **When** preparation completes, **Then** the result records cloud-init completion, installed-tool evidence, role setup, command outcomes, and timestamps for each VM.
3. **Given** bootstrap fails or exceeds its timeout on either VM, **When** preparation stops, **Then** the failure identifies the VM, phase, command, timeout or exit condition, captured stdout/stderr, and the next readiness step is not attempted.

### User Story 2 - Establish private-path and server readiness (Priority: P1)

A lifecycle controller can verify that the VMs are reachable over SSH, can reach each other over the intended private IPv4 path, and that VM B is ready to receive later benchmark traffic.

**Why this priority**: A successful Terraform deployment or public SSH connection does not prove that the measured private path or benchmark server is usable.

**Independent Test**: Drive fake SSH responses through cloud-init, private-path, and server-readiness checks, including delayed, failed, and timed-out responses, and verify deterministic state transitions and evidence.

**Acceptance Scenarios**:

1. **Given** completed bootstrap and reachable SSH endpoints, **When** readiness checks run, **Then** VM A verifies the configured private address of VM B, VM B verifies its server-side prerequisites, and the pair is marked ready only after all required checks pass.
2. **Given** VM B’s benchmark server is not listening or reports an unhealthy state, **When** readiness is checked, **Then** the pair remains not ready, the evidence names the expected server check and observed output, and benchmark execution is not authorized.
3. **Given** a check initially reports not ready but becomes ready before its configured deadline, **When** polling continues, **Then** the controller records the successful attempt and elapsed readiness time without treating the earlier response as a terminal failure.
4. **Given** the private path, cloud-init, SSH, or benchmark-server check reaches its deadline, **When** readiness ends, **Then** the result records a typed timeout with the VM, check, deadline, attempts, last output, and connection target.

### User Story 3 - Retrieve immutable remote evidence (Priority: P2)

After later benchmark or collector work produces remote artifacts, the lifecycle controller can retrieve the requested files from both VMs into the F01-assigned local result paths without silently overwriting evidence.

**Why this priority**: Raw results and supporting metadata must remain available locally even after temporary VMs are destroyed.

**Independent Test**: Use fake SCP actions for complete, missing, partial, duplicate, and failed transfers and verify destination safety, transfer evidence, and failure reporting.

**Acceptance Scenarios**:

1. **Given** a declared retrieval manifest and empty immutable local destinations, **When** retrieval runs, **Then** every requested artifact is copied from the correct VM, associated with its role and source path, and recorded with size, checksum or equivalent integrity evidence, timing, and command outcome.
2. **Given** a remote artifact is missing, unreadable, truncated, or transfer fails, **When** retrieval ends, **Then** the result distinguishes missing, transfer, integrity, and timeout failures, preserves partial evidence, and never reports the bundle as complete.
3. **Given** a local destination already contains evidence, **When** retrieval would overwrite it, **Then** the operation refuses the collision unless the destination is explicitly an empty temporary transfer location, and the refusal is recorded.
4. **Given** one VM’s retrieval succeeds and the other VM’s retrieval fails, **When** the overall operation ends, **Then** successful files remain preserved and the failure names the incomplete VM-specific retrieval without hiding the successful evidence.

### Edge Cases

- Terraform outputs may omit, duplicate, or contain malformed private addresses, SSH usernames, keys, or role assignments; the access boundary must reject them before remote commands.
- VM A and VM B may be reachable through SSH while their private path is unavailable; SSH readiness must not substitute for private-path readiness.
- Cloud-init may complete with a non-zero status, incomplete marker, stale marker, or output that cannot be parsed; these are explicit failures, not successful bootstrap.
- A readiness command may return transient failure, empty output, non-zero exit status, or a timeout; each attempt and the final reason must remain inspectable.
- The benchmark server may already be running, may be running with the wrong configuration, or may fail to bind its private address; readiness must distinguish acceptable idempotent state from unsafe state.
- Remote paths may contain spaces or unexpected names; command and retrieval boundaries must preserve path arguments without shell ambiguity.
- A timeout or interruption during one VM action must not discard evidence already captured from the other VM.
- Bootstrap and readiness settings must remain configurable per run; this feature must not freeze benchmark duration, stream count, or phase ordering.
- No credentials, private keys, Terraform state, or raw result bundles may appear in normal failure summaries.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST accept the provider-neutral connection output from F01 after Terraform has returned, including provider identity, scenario, VM A and VM B identities, private IPv4 addresses, SSH connection details, expected roles, and any provider metadata needed for traceability, without creating or interpreting provider resources.
- **FR-002**: System MUST validate connection outputs before any remote action, rejecting missing, ambiguous, inconsistent, or unsafe VM identity, role, address, username, key-reference, or timeout data with actionable evidence.
- **FR-003**: System MUST preserve explicit VM roles: VM A is the client and traffic generator, and VM B is the server and receiver; role assignment MUST be visible in preparation, readiness, and retrieval records.
- **FR-004**: System MUST render a minimal, repeatable bootstrap plan that installs or verifies pinned versions of FLENT and netperf plus the Linux tools required by later benchmark, metadata, and diagnostics features.
- **FR-005**: System MUST keep tool versions, package sources or artifact references, bootstrap commands, and role-specific setup inputs configurable and record the resolved values used for each VM.
- **FR-006**: System MUST execute remote commands through the replaceable F01 external-command boundary, capturing command identity, target VM, start/end time, exit status, stdout, stderr, timeout, and interruption state while redacting secrets from user-facing evidence.
- **FR-007**: System MUST verify cloud-init completion on each VM before declaring bootstrap successful, including an explicit success signal and failure evidence when completion is absent, stale, malformed, non-zero, or timed out.
- **FR-008**: System MUST support configurable bounded timeouts for SSH connection, each remote command, cloud-init completion, private-path readiness, benchmark-server readiness, and each retrieval transfer; timeout evidence MUST include the configured deadline and last observed state.
- **FR-009**: System MUST perform private-path readiness from VM A to VM B using the configured private address and a provider-neutral readiness probe, and MUST not treat public reachability or SSH access alone as proof of private-path readiness.
- **FR-010**: System MUST verify benchmark-server readiness on VM B using an explicit server-health condition suitable for later benchmark execution, while excluding benchmark phase execution and phase-specific semantics from this feature.
- **FR-011**: System MUST support bounded polling for transient readiness states, recording attempts and elapsed time, and MUST stop at the configured deadline with a typed failure when readiness does not become true.
- **FR-012**: System MUST retrieve declared remote artifacts through the replaceable SCP boundary into the immutable local paths assigned by F01, associating each artifact with source VM, role, source path, destination path, transfer result, and integrity evidence.
- **FR-013**: System MUST refuse unsafe local destination collisions and preserve successfully retrieved artifacts when another transfer fails, times out, or is interrupted.
- **FR-014**: System MUST expose structured preparation, readiness, and retrieval outcomes that later lifecycle integration can attach to the run manifest, including successful, partial, failed, timed-out, and interrupted states.
- **FR-015**: System MUST preserve clear failure evidence for every failed remote sequence, including stage, VM, role, action, command or source path, reason category, exit/timeout/interruption details, last output, and timestamps; cleanup ownership remains with lifecycle integration.
- **FR-016**: System MUST provide fake-backed tests covering successful bootstrap and retrieval, delayed readiness, private-path failure, server-not-ready, command failure, SCP failure, timeout, interruption, partial retrieval, and destination collision without cloud credentials or live VMs.
- **FR-017**: System MUST exclude provider-specific Terraform resources and apply/destroy behavior, benchmark phase semantics, result validation policy, and real-cloud tests from this feature.

### Key Entities

- **Connection Output**: Provider-neutral handoff from deployment containing the two VM identities, roles, private addresses, SSH access references, provider/scenario context, and connection-related limits.
- **Bootstrap Plan and Record**: The resolved pinned tool inputs, commands, role setup, cloud-init completion evidence, and per-VM outcome.
- **Readiness Record**: Ordered SSH, cloud-init, private-path, and benchmark-server checks with attempts, deadlines, outputs, and final status.
- **Remote Action Result**: A fakeable command or SCP outcome containing target, action, timing, exit/transfer status, output, timeout/interruption state, and redacted evidence.
- **Retrieval Manifest**: Declared source artifacts and immutable local destinations, with per-artifact transfer and integrity outcomes.
- **Access Failure Evidence**: Structured, manifest-ready description of the first failure and any subsequent partial results, without replacing the original cause with later failures.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of valid fake connection fixtures prepare both VM roles with the configured pinned tool set and record cloud-init completion without any cloud credential or live VM.
- **SC-002**: 100% of invalid connection fixtures are rejected before the first remote action, with a failure naming the invalid field and affected VM.
- **SC-003**: At least one fake sequence for each supported terminal outcome—success, delayed readiness, command failure, transfer failure, timeout, interruption, and partial retrieval—produces structured evidence sufficient to identify VM, stage, action, and reason.
- **SC-004**: A complete fake retrieval fixture copies every declared artifact exactly once into its assigned local result path, preserves source-to-destination mapping, and detects an intentional integrity or destination-collision fault.
- **SC-005**: Readiness tests demonstrate that SSH reachability alone cannot produce a ready state: both private-path and benchmark-server checks must pass before readiness is granted.
- **SC-006**: Every configured timeout and polling deadline terminates within its declared bound in fake tests and records the last observed output and number of attempts.
- **SC-007**: A reviewer can inspect a successful and failed remote sequence and identify the VM role, ordered actions, timestamps, outputs, and original failure cause in under 5 minutes.
- **SC-008**: The feature’s full unit and fake integration test suite runs without cloud credentials, provider Terraform resources, live SSH/SCP, real benchmark traffic, or real-cloud tests.

## Assumptions

- F01 supplies the connection-output, local-result-path, artifact, command-execution, and manifest contracts; this feature extends those contracts only where access, bootstrap, readiness, and retrieval evidence is required.
- Provider drivers remain responsible for producing provider-specific Terraform outputs and provider metadata; F02 consumes their normalized handoff and does not provision infrastructure.
- Ubuntu is the target guest operating system for the current implementation phase, and package/artifact pins are supplied by configuration or approved repository defaults.
- SSH and SCP use the connection details returned by the provider driver, while private-path checks use the VM A-to-VM B private IPv4 address supplied in the handoff.
- Bootstrap is expected to be repeatable for retries, but unsafe partial installation or ambiguous tool versions must remain visible rather than being silently accepted.
- Benchmark-server readiness establishes that the server prerequisite is available; FLENT phase ordering, duration, stream count, and measured outputs belong to F03 and later integration.
- Cleanup and terminal manifest transitions belong to F09; F02 returns evidence that cleanup can consume and never hides an earlier failure.
- Real-cloud validation is a later paid stage after the credential-free offline gate and is intentionally not part of this feature.
