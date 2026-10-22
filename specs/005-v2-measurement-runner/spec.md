# Feature Specification: F03 V2 measurement runner

**Feature Branch**: `codex/f03-v2-measurement`
**Created**: 2026-10-21
**Status**: Ready for implementation after artifact checks
**Input**: F03 prompt in Thesis `tracking/IMPLEMENTATION_PROMPTS.md`, sections F03 and Engineering clarifications, with the coordinator's short-coverage decision.

Execute the four V2 measurement modes on an already prepared VM pair. Preserve the actual commands, native outputs, timing and failures needed to assess what was measured. This feature depends only on merged F01R (`cef838e42fc620f90b5943f5a8d1528ddc0b0724`). It does not provision, start servers, perform readiness, schedule clouds, collect host diagnostics, retrieve files, validate statistics, choose N, or clean up cloud resources. F09 later composes those separately owned capabilities.

## Clarifications

### Session 2026-10-21

No human questions were needed under the authorized autonomous workflow. The following engineering choices resolve the handoff's open short-coverage policy; they do not change the research campaign.

- Loaded short, acceptance and calibration units require the full requested receiver TCP measured duration and active measured probe-window coverage of at least 99% of that interval. Idle requires its full requested measured probe interval. F03 records evidence; F08 decides data validity and comparison eligibility.
- Active coverage means the union of actual probe acquisition intervals intersected with the measured TCP interval, after warm-up/tool filtering and with recorded gaps removed. It is not a fraction of expected CSV rows. The source's separate probe-generation threshold remains at least 95% of target.
- Missing or ambiguous native timing remains unavailable, including unknown mapping error. Process launch/exit and a requested duration do not prove actual measurement. Neither buffered notification receipt nor second-resolution connection metadata proves a precise traffic-start instant.
- A failed unit ends this runner invocation without automatic retries or subsequent short/calibration units. A diagnostic probe chunk may fail while the same TCP process continues; preserve the gap and continue later chunks within the original absolute deadline. Never restart failed TCP.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Measure a short experiment (Priority: P1)

A researcher receives separate idle, single-flow and common-N aggregate evidence for a prepared pair, using the selected protocol or its ten-second acceptance variant.

**Why this priority**: These three phases are the core fresh-pair experiment and the acceptance path.

**Independent Test**: A simulated prepared pair and clock produce the three ordered units, exact requested settings, separate native files and observed windows without network traffic.

**Acceptance Scenarios**:

1. **Given** a resolved short-study plan with frozen N, **When** measurement runs, **Then** idle, single and aggregate execute in order with 60 measured seconds, five-second warm-up and ten-second pauses only between phases.
2. **Given** acceptance before calibration, **When** measurement runs, **Then** each phase requests ten measured seconds and aggregate N=4; after calibration the supplied frozen N is used.
3. **Given** delayed probe startup, **When** a loaded unit runs, **Then** TCP cannot launch before the genuine Sockperf test-start notification followed by two monotonic seconds; actual windows, rather than that wait, establish overlap.
4. **Given** early exit or absent timing, **When** execution returns, **Then** partial files and unavailable timing are preserved; command success does not imply valid data.

### User Story 2 - Measure interleaved calibration (Priority: P2)

A researcher obtains twelve loaded trials on the same prepared deployment with the common stream orders, without an invented idle baseline or a selected N.

**Why this priority**: Frozen research N requires these trials; selection belongs to F08.

**Independent Test**: Simulated calibration runs exactly `1,4,2,8; 8,2,4,1; 2,8,1,4` with one retained deployment identity.

**Acceptance Scenarios**:

1. **Given** a calibration plan, **When** it completes, **Then** twelve 60-second loaded trials retain their supplied IDs and trial indices, each count occurs three times, and eleven ten-second pauses separate trials.
2. **Given** a failed trial, **When** the runner stops, **Then** earlier outputs and the failed trial remain identifiable, later trials are unexecuted, and no provider-specific tuning or automatic repeat occurs.

### User Story 3 - Measure a continuous diagnostic day (Priority: P3)

A researcher receives one uninterrupted 86,400-second loaded TCP measurement with local probe chunks, gaps and partial output from the retained pair.

**Why this priority**: The diagnostic protocol differs from short experiments and must not be approximated by repeated TCP tests.

**Independent Test**: A fake clock advances over the day while exactly one TCP process remains active across all probe rotations.

**Acceptance Scenarios**:

1. **Given** a diagnostic plan with frozen N, **When** it executes, **Then** one TCP invocation requests 86,400 measured seconds after a single five-second omission, with streaming client output and the prepared persistent receiver stream.
2. **Given** a probe chunk closes or fails, **When** the next local chunk starts, **Then** no TCP restart or TCP pause occurs, each probe invocation requests at most 300 seconds, filenames are unique and restart gaps are retained.
3. **Given** controller cancellation, process failure or deadline exhaustion, **When** the runner terminates owned clients, **Then** available native files remain, missing intervals are explicit and the upstream caller can retrieve evidence and clean up.

### Edge Cases

- Notification split across reads, unrelated startup text, duplicate notification, missing notification, buffering and exit before notification.
- TCP connection delay, omitted intervals, early successful exit, truncated native output, unknown tool/source identity or clock estimates, clock adjustments and out-of-order timing.
- Deadlines during notification wait, lead wait, phase pause, process drain or final chunk; cancellation with both clients active; terminate/kill/reap failures alongside the primary failure.
- Probe rotation includes a partial last chunk and startup/filtering gaps. CSV silence alone does not establish an acquisition gap or exact packet loss.
- Existing destinations, path traversal/symlink escape, write failure/disk full, malformed inputs and pending common N for research/day.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Consume the F01R resolved mode, ordered units, retained deployment/unit identities, canonical artifact layout and explicit runtime inputs. Reject inconsistent identity, unsafe destinations, invalid private IPv4 or unresolved research/day N before launching a client. Do not generate independent launches for trials/chunks.
- **FR-002**: Execute short-study idle/single/aggregate in the supplied canonical order, with 60 measured seconds, five-second warm-up, ten-second pauses between units, one stream for single and frozen common N for aggregate. Acceptance requests ten measured seconds, N=4 while calibration is pending and frozen N afterwards.
- **FR-003**: Use Sockperf UDP `under-load`, 64-byte messages, 1,000 mps, reply-every 1, full RTT and OS monotonic timing, private B port 11111, and per-invocation full CSV and logs. Preserve the handoff durations: idle 65/15 seconds and loaded 70/20 seconds for research/acceptance respectively; calibration loaded probes request 70 seconds.
- **FR-004**: Use A-to-B iperf3 TCP on port 5201, CUBIC, sender zerocopy, 131072-byte blocks, default socket buffers and uncapped traffic. Short/trial clients request 60/10 measured seconds with five seconds omitted, one-second reporting, JSON and server output. Day clients request 86400 seconds with the same omission and streaming/forceflush output. Native receiver evidence is authoritative for throughput; sender summaries cannot substitute.
- **FR-005**: Wait on A for the current Sockperf invocation's genuine test-start notification, then two monotonic seconds before the first TCP launch. Observe process exit/cancellation/deadline during both waits. Do not treat process launch, stale notification or sleep completion as active probing evidence.
- **FR-006**: Record observed process starts/ends, notification receipt, actual measured windows, omitted time, monotonic/UTC mappings, mapping uncertainty, overlap and gaps through the existing F01R timing/command contracts plus original native outputs. Exclude warm-up/omitted/tool-filtered intervals. Preserve unavailable evidence instead of inferred precise timestamps.
- **FR-007**: Supply evidence for the declared full-duration/99% short loaded coverage policy and full idle interval. Preserve the fixed day full-TCP/99% RTT coverage, at least 95% probe generation and at most 100 ms clock-error rules for later F08 assessment. Execution, validity and comparison status remain separate; F03 does not mark data/comparison valid.
- **FR-008**: Execute twelve retained calibration loaded trials with 60 measured seconds, five-second omission, ten-second inter-trial pauses and exactly the orders `1,4,2,8; 8,2,4,1; 2,8,1,4`. Do not select N, add idle phases or emit latency inflation.
- **FR-009**: Execute one diagnostic TCP process for 86400 measured seconds after one warm-up, without scheduled pauses/restarts. Rotate Sockperf locally in invocations of at most 300 seconds, including any shorter final invocation; preserve distinct files, parent identity and actual gaps. Do not reapply a study warm-up to each day chunk or create extra experiments/reservations.
- **FR-010**: Write native output incrementally on the measuring VM, preserve separate stdout/stderr and reported counters without rewriting samples or inferring exact loss from row counts. Close owned files at completion/interruption before returning. Use exclusive destinations and preserve existing evidence. Do not copy periodically off VM, delete artifacts or keep day output in an unbounded memory buffer.
- **FR-011**: Bound every wait and subprocess by one caller-supplied absolute monotonic deadline, with bounded termination/drain/reap. On cancellation/failure preserve the primary failure and secondary shutdown/file errors. Stop later short/calibration units; continue day chunks after isolated probe failure without retrying a failed invocation or restarting TCP. Return partial evidence for upstream retrieval/cleanup.
- **FR-012**: Run on a prepared pair with persistent B listeners and supplied tool/source/clock evidence, independent of unfinished sibling implementations. Provide an A-local callable entry point usable by F09, with replaceable processes/clocks and unchanged shared contracts. Do not perform SSH, provision, start/stop B servers, wait for readiness/stabilization, implement diagnostics/validation/admission, or infer paid authorization.
- **FR-013**: Prove the requirements with credential-free command, fake-clock/process, materialized-file, failure and F01R compatibility checks. No real benchmark traffic, SSH/cloud contact or day-long waits are permitted in this feature's acceptance.

### Key Entities

- **Resolved measurement**: Provider-specific mode, ordered unit IDs/settings and common-N evidence from F01R.
- **Measurement invocation**: One client command, actual process state/times, tool/source identity and native files.
- **Measurement timing**: Per-unit observed window, notification/TCP timing, omitted time, overlap, gaps and clock mappings.
- **Measurement result**: Executed/unexecuted units, command/timing/artifact references, execution outcome and primary/secondary failures, with validation still pending.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every simulated short/acceptance run produces exactly three correctly ordered units; all protocol settings and two pauses match the request, and no TCP command precedes notification plus two seconds.
- **SC-002**: Every simulated successful calibration produces twelve distinct trials in the prescribed order with three repetitions/count and one retained deployment identity.
- **SC-003**: A simulated full diagnostic day has exactly one continuous TCP invocation, all probe invocations at most 300 seconds, complete local output references and recorded restart gaps, without a real-time day wait.
- **SC-004**: Every injected failure/cancellation/deadline case returns honest partial/unavailable timing and retained output; no failed run becomes valid by process success or invented timestamps.
- **SC-005**: All focused offline checks and the existing credential-free regression suite pass at the implementation head; fake evidence is explicitly separated from future real-cloud acceptance.

## Assumptions

- Sources: Thesis V2 snapshot (19 October), DECISIONS (newest entries first), FRAMEWORK_IMPLEMENTATION_NOTES, EXPERIMENTS, M04, IMPLEMENTATION_PROMPTS and roadmap, read at Thesis revision `8927ff5a17a7113ac8080c15b412a4b8d8270744`. Local snapshots are sufficient for offline implementation; no paid register values or research results are refreshed or invented.
- Coordinator-confirmed engineering coverage policy is recorded above. N selection, exact UTC windows, provider skew/admission, lifetime cap-start and failed retrieval policy remain with their owning stages.
- Prepared listeners, capability verification and clock estimates are supplied runtime inputs. Unknown estimates remain unavailable; launching this module does not prove bootstrap/readiness or real tool behavior.
- Stop-on-unit-failure avoids additional measurement after an invalid short/calibration unit; day probe rotation continues within the original deadline to preserve useful partial diagnostic intervals. No campaign retry is introduced.
- Implementation remains deliberately small: ordinary functions and a concurrent process boundary, no service, agent daemon or new cross-feature acquisition schema.
