# Feature Specification: F02R V2 remote preparation and native retrieval

**Feature Branch**: `codex/f02r-v2-remote-access`

**Created**: 2026-10-21

**Status**: Checked for planning

**Input**: F02R in Thesis `tracking/IMPLEMENTATION_PROMPTS.md`: migrate the merged F02 access, bootstrap, readiness and retrieval foundation to V2; checksum-verified source builds, authenticated management access, persistent private listeners, one absolute readiness deadline and verified post-measurement native retrieval. Preserve historical F01/F02 specifications.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Prepare a trustworthy VM pair (Priority: P1)

The experiment operator needs both VMs prepared with the selected measurement tools and an authenticated connection, so later measurements use known binaries and B's private servers.

**Why this priority**: No measurement is admissible when software identity or management identity is unknown.

**Independent Test**: A fake deployment and exact command recorder prepare two VMs, verify tool evidence and show the two intended listeners; a bad source digest or host identity prevents success.

**Acceptance Scenarios**:

1. **Given** a complete V2 deployment with authenticated host references, **When** preparation succeeds, **Then** both VMs have checksum-verified source-built tools with observed compiler/binary identities and required capabilities, while B runs persistent servers bound to its private address.
2. **Given** a missing/mismatched host identity, failed source verification, build failure or missing required capability, **When** preparation is attempted, **Then** it fails with redacted evidence and cannot qualify for readiness.
3. **Given** the same deployment is prepared again, **When** existing installations/listeners match their recorded identities, **Then** preparation reuses matching state without duplicate server processes or discarding prior logs; mismatched state fails explicitly.

### User Story 2 - Observe readiness within one deadline (Priority: P1)

The operator needs a single bounded readiness decision across both VMs before measurement is allowed.

**Why this priority**: Independent timeouts can consume the complete experiment cap and conceal a broken private path.

**Independent Test**: Fake clocks and command results exercise delayed SSH, cloud-init, private connectivity and both listeners; the sum of all checks/polls cannot extend the original readiness deadline or overall lifetime cap.

**Acceptance Scenarios**:

1. **Given** successful preparation, **When** both SSH connections, both cloud-init completions, private A-to-B connectivity and B's intended listeners are observed, **Then** readiness succeeds and retains each observation.
2. **Given** earlier checks consume the available time, **When** a later check or poll would exceed the deadline, **Then** no new command starts and readiness fails with elapsed/remaining-time evidence.
3. **Given** missing preparation evidence or unhealthy listeners, **When** readiness runs, **Then** it cannot report ready or start/restart a server as part of checking health.

### User Story 3 - Preserve verified native files after measurement (Priority: P1)

The operator needs closed files from both VMs copied into immutable local result paths, with integrity and incomplete-copy evidence, before normal resource cleanup.

**Why this priority**: Native measurements and failures must remain traceable even when copying fails.

**Independent Test**: Fake transfers materialize bytes in temporary directories; closed-file inventories from A/B are checked against their exact local sizes and SHA-256 digests. Partial, absent, corrupt and interrupted transfers remain identifiable.

**Acceptance Scenarios**:

1. **Given** measurement has ended and producers have closed their files, **When** the inventory is retrieved, **Then** native CSV/JSON/JSONL, diagnostic/server logs and metadata from both VMs retain their identities and match their recorded remote size/hash.
2. **Given** measurement is still active or an artifact is open, **When** retrieval is requested, **Then** copying is rejected before a transfer starts.
3. **Given** a transfer fails or is corrupt, **When** the operator receives the result, **Then** verified earlier files and partial local bytes are preserved, missing/failed items are explicit and any primary measurement failure remains separate.
4. **Given** remaining time permits one bounded retry, **When** a failed item is retried, **Then** it uses a separate local attempt path without overwriting evidence; exhaustion stops further copying and requests normal cleanup within the original cap.

### Edge Cases

- Partial provider output, mismatched deployment/VM identity, missing strict host-key file, wrong private address, unsupported tool output or stale server installation.
- Cloud-init still running, each readiness check eventually healthy only after deadline, command interruption and cap exhaustion before the first command.
- Listener on a public/wildcard address, correct port with wrong process, stopped server, changed firewall restriction, ICMP failure or TCP connection failure.
- Transfer success without any materialized file; truncation, wrong digest, zero-byte stderr, missing optional file, duplicate artifact ID/path, traversal, symlink escape, existing destination and corrupt retry.
- Large native files, a partial diagnostic day, completed local probe chunks before overall measurement end, loss of one VM and secondary retrieval failure after a measurement failure.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Reuse/migrate merged F02 modules against merged F01R V2 deployment, identity, command and artifact contracts; preserve `specs/001-*` and `specs/002-*`. Reject incomplete pairs or inconsistent identities before remote actions. No dependency on an unfinished sibling is allowed.
- **FR-002**: Every management connection and copy MUST use the dedicated attempt key, noninteractive authentication and strict authenticated known-host identity; missing/mismatched identities fail closed. Commands and paths MUST be transported without treating input values as executable shell fragments. Durable evidence MUST redact secret values, private key contents and private credential paths.
- **FR-003**: Preparation MUST verify the expected SHA-256 of each configured source archive before extracting/building Sockperf and iperf3 on both VMs. It MUST record actual source identity, build commands, compiler version, executable path/version/hash and capability observations; requested versions alone cannot establish success. Failed verification/build/capability checks stop preparation.
- **FR-004**: Required capabilities MUST cover Sockperf UDP server/under-load, 64-byte/1,000-mps full-RTT monotonic timing and native full-log output, and iperf3 TCP CUBIC, zerocopy, parallel streams, omitted warm-up, client JSON/server-output and client/server JSON streaming with forceflush. Capability checks are replaceable and generate no real benchmark traffic during offline validation.
- **FR-005**: Preparation MUST start B's Sockperf UDP 11111 and iperf3 TCP 5201 servers once, bind to B's private IPv4, restrict benchmark ingress to A, and save server stdout/stderr at the F01R native paths. Their lifetime spans all phases/trials/chunks of the deployment. Repeated preparation MUST reuse matching state or fail on a mismatch without duplicate listeners, log truncation or hidden restarts.
- **FR-006**: Readiness MUST require observed successful preparation for this deployment, authenticated SSH and cloud-init completion on A and B, private A-to-B connectivity and both intended healthy B listeners. Health observations MUST distinguish binding, process identity and access restriction; readiness MUST NOT start or restart servers. These checks do not claim proof of real UDP performance or provider firewall enforcement.
- **FR-007**: Readiness MUST use one monotonic absolute deadline across both VMs and all SSH/cloud-init/private-path/listener checks and polls. It MUST be bounded by the remaining overall lifetime cap; each command timeout and polling wait is bounded by remaining time, with no deadline reset. At zero remaining time no command starts; failure records the last observation and elapsed time.
- **FR-008**: Retrieval MUST require measurement-end and closed-file evidence, including stopped/flushed producers for server logs. It MUST not copy completed diagnostic chunks off-VM while measurement continues. Inventory uses F01R artifact/deployment/role/unit identities and preserves native bytes, command/timing evidence, logs, diagnostics and metadata from both VMs without mandatory conversion.
- **FR-009**: Every claimed verified transfer MUST compare materialized local bytes to required observed remote size and SHA-256. Missing materialization, size/digest mismatch and required missing files fail explicitly. Zero bytes is allowed only when remote size is zero; data validity belongs to F08. Requested hashes, stdout from a copy command and an exit code alone cannot prove retrieval.
- **FR-010**: Artifact destinations MUST remain normalized, contained under the child result root, unique and immutable. Reject traversal, symlink escape, duplicate identities/destinations and collisions before transfer. Preserve verified bytes and any partial failed-copy bytes; track failed/missing items without replacing the primary failure.
- **FR-011**: Failed-copy handling MUST attempt at most one retry per failed artifact only within the remaining original overall cap and the caller's cleanup allowance. Retry bytes use a distinct path; no successful or partial evidence is overwritten. If time is insufficient, terminate copying, retain available evidence and return cleanup-required status. F09 owns normal cleanup and must attempt it by the cap; this feature MUST NOT retain disks/resources automatically, reset the cap, add a copying timer or launch a replacement experiment.
- **FR-012**: Consume the caller's original cap context: activate the admitted budget reservation immediately before independent provider-expiry registration, confirm expiry before first resource creation, and preserve `cap_started_at <= expiry.registered_at <= creation`. Setup, registration, measurement, retrieval and cleanup all count. F02R MUST never create/renew admission, expiry or a deadline; provider expiry and resource deletion are other owners' work.
- **FR-013**: All external steps MUST use replaceable command/time/file boundaries. Unit, contract and integration tests MUST cover exact arguments, source verification, strict identity, deadlines, listener predicates and actual temporary-file retrieval including failures, with no credentials, real SSH/SCP/cloud calls or real benchmark traffic.

### Key Entities

- **Prepared deployment**: V2 provider/deployment/VM identities, authenticated connection references, source/build/capability evidence and persistent B listener ownership.
- **Readiness decision**: Original absolute deadline and overall cap, ordered observations, attempts, terminal readiness/failure and redacted evidence.
- **Closed-file inventory**: Measurement-end/closure evidence and per-artifact source VM, native destination, unit ID, observed size and hash.
- **Retrieval outcome**: Verified F01R artifact records, failed/missing records, partial local attempt locations, copy attempts and cleanup-required signal; primary measurement failure is independent.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For both simulated VMs, all successful preparation cases include observed source/build/binary/capability identity; every injected identity, source or capability failure prevents readiness.
- **SC-002**: Every readiness case finishes within its one original deadline and remaining overall cap, with zero server mutations during readiness and zero commands started after time exhaustion.
- **SC-003**: Every artifact marked verified has materialized bytes matching both observed size and hash; all injected corruption, missing-file and collision cases remain unverified with preserved local evidence.
- **SC-004**: All four experiment modes share the same preparation/retrieval boundaries; tests prove no retrieval before measurement end, at most one retry per failed artifact, and no lifetime extension or credential/network dependency.

## Assumptions

- F01R merge `cef838e42fc620f90b5943f5a8d1528ddc0b0724` is the sole shared baseline. F02R owns local adapters/evidence and freezes shared contracts.
- The operator supplies identified sources with digests; no research-version freeze or new approval gate is introduced. Tool capability evidence must match the actual built binary.
- Provider drivers supply authenticated host identity and provider firewall restrictions; F02R also applies narrowly scoped guest listener restrictions without changing management access. Offline checks establish command behavior only.
- One original caller-owned workflow cap includes setup and expiry registration. A caller-supplied cleanup allowance comes from its existing cleanup timeout inside that cap; F02R starts no independent copy timer.
- Retry is a bounded engineering policy, not a data-retention promise. If cleanup is late or fails, F09/provider owners retain cleanup-open evidence and block launches; F02R cannot guarantee provider deletion.
- Exact N, paid prices and UTC study windows are evidence-dependent and unchanged. Terraform, measurement/diagnostic orchestration, parsing/statistics, budget accounting, provider expiry/deletion and actual cloud acceptance are excluded.
