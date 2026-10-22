# F03 research and engineering decisions

Date: 2026-10-21. Implementation base: `cef838e42fc620f90b5943f5a8d1528ddc0b0724`.
Thesis sources read at `8927ff5a17a7113ac8080c15b412a4b8d8270744`; read-only.

## Source routing and supersessions

Read AGENTS.md, constitution 2.0.0, docs/autonomous/WORKFLOW.md, and canonical Thesis `advisor/docs/design_document.md`, `DECISIONS.md`, `experiments/FRAMEWORK_IMPLEMENTATION_NOTES.md`, `experiments/EXPERIMENTS.md`, `tracking/IMPLEMENTATION_PROMPTS.md` (F03 exact prompt/acceptance), `tracking/M04_implementation.md`, and `tracking/framework_implementation_roadmap.html`. Latest dated choices override historical FLENT/four-flow/five-minute proposals. This stage does not need live prices/run-register cells because it neither admits nor costs a paid experiment. Formal advisor approval is unrecorded.

## Decision: preserve the F01R interface

**Rationale**: `ResolvedObservation.measurement_units` already supplies three canonical phases, twelve calibration trials or a continuous day. `MeasurementTiming` carries notification/TCP/measured timestamps, monotonic bounds, omitted seconds, overlap, gaps and clock mappings; `CommandEvidence` links durable requests/results and tool/process identity. Native CSV/JSON/JSONL and logs preserve richer source timing. No mandatory information requires a shared-contract amendment at planning time.

**Alternatives considered**: Extending shared `CommandRunner` with concurrent lifecycle methods would affect independent siblings. An acquisition sidecar with its own required cross-feature schema would create an unfinished dependency for F08. Both are unnecessary. F03-local runtime records may organize data but are not an F08 prerequisite. Unknown values use existing `Evidence(unavailable_reason=...)`.

## Decision: a minimal A-local concurrent boundary

**Rationale**: Synchronous `CommandRunner.run()` cannot gate a second process on a live notification from the first. A local adapter accepting the existing request and returning the existing result supplies start, incremental output, poll and bounded shutdown. Keep process handles local, use no shell, drain stdout/stderr concurrently, write day streams to files and retain only a bounded detection tail. The adapter is injected in tests; the production adapter uses ordinary `Popen` on A.

**Alternatives considered**: SSH/controller timing would place the gate on the wrong machine and depend on unfinished F02R. A long shell script would hide cancellation, output and timing. A cloud agent/service would violate the small architecture. No new framework is needed.

## Decision: notification and actual measurement are distinct

**Rationale**: The fixed protocol waits for Sockperf test-start then two seconds. In the inspected upstream [Sockperf 3.10 client source](https://github.com/Mellanox/sockperf/blob/3.10/src/client.cpp), the message is `Starting test...`; initialization and warm-up precede it, and sample filtering removes head/tail samples. The runner must recognize that source-backed message rather than invent a `Test started` line. Recognition is for scheduling; receipt time is not silently promoted to exact native traffic start. Preserve tool output and use actual sample/tool timestamps and recorded mappings for coverage, with uncertainty explicit.

The upstream [iperf3 source](https://github.com/esnet/iperf/blob/3.17.1/src/iperf_api.c) records the JSON `start.timestamp` during connection setup and reports omitted/relative measurement intervals separately. This is evidence that connection time plus the requested duration is insufficient to claim a precise measured interval. Inspect the actual supplied build's native timing semantics; coarse or missing anchors stay unavailable. Source versions here identify inspected examples, not a new software freeze or a dependency on F02R's unmerged build choices.

**Executable conservative path**: Exact measured endpoints are not necessary to prove sufficient coverage when real native and process evidence bounds every possible interval. Let D be the native receiver non-omitted measured duration (first require D >= the requested duration), O be the verified omission lower bound, and p/q be A's observed process start/completion. Subject to verified tool semantics and clock uncertainty, measured start lies within [p+O, q-D]. Tighten that bracket with genuine native test/stream events, expand it by known mapping/observation uncertainty, and reject inconsistent bounds. For every possible measured interval [s,s+D] in that bracket, compute a lower bound on active probe coverage from actual acquisition windows minus gaps. A simple sufficient check is that uncovered time anywhere in the outer envelope [lowest possible start, highest possible end] is <=1% of D. This can prove >=99% actual coverage when probes span the whole bounded TCP envelope, even though exact timestamps remain unavailable. Omission must be verified from native/source semantics, not merely requested flags. Unknown receiver duration, unbounded uncertainty or no defensible acquisition bounds means unavailable coverage. This uses existing process CommandEvidence, native output, MeasurementTiming gaps and mappings, not a new required artifact schema.

F03 can return optional local timing notes describing conservative bounds/coverage; they are not exact MeasurementTiming endpoints and are not required by F08. F08 reconstructs the same proof from shared/native evidence. A whole-second UTC anchor still cannot establish <=100 ms cross-VM plot alignment. Real acceptance must verify the supplied build's omission, receiver duration, notification/stream flushing and observed bound width; fake tests prove the algorithm and its refusal of insufficient evidence.

**Alternatives considered**: Process-launch sleep, stale log matching, first/last CSV row as complete coverage, and whole-second connection metadata as a <=100 ms clock guarantee all overstate the evidence. F08 verifies the original files independently. Fakes prove controller behavior, not real stdout flushing, tool timing or receiver permissions; paid acceptance remains necessary.

## Decision: short coverage policy

**Decision**: Loaded short/acceptance/calibration units require full requested receiver measured TCP duration and at least 99% active measured probe-window coverage of that TCP interval. Idle requires the full requested measured probe interval. Compute time coverage from the union of observed acquisition windows intersected with TCP, after warm-up/filtering and recorded gaps; do not derive packet loss or active time from CSV counts. F03 records observed evidence without issuing F08 validity/comparison status.

**Rationale**: The handoff expressly delegates numerical short-overlap policy. The coordinator aligned F03/F08 on this engineering threshold. It tolerates at most 0.6 s for 60 s and 0.1 s for 10 s of measured acquisition gaps while requiring the complete TCP measurement. These are policy arithmetic examples, not measured outcomes or a new experiment. Overlap is capped by the actual measured interval; overlapping acquisitions must not double-count.

**Alternatives considered**: Any positive overlap would accept mostly unobserved load; 100% coverage would reject negligible acquisition gaps; a sample-count threshold would conflate tool filtering/reordering/loss with time coverage. The separate fixed generation >=95%, day full 86400/RTT >=99% and aligned-plot clock error <=100 ms rules remain unchanged. Do not invent numeric tolerance to turn a short receiver interval into full duration.

## Decision: modes use supplied units and original commands

**Rationale**: Short/acceptance use idle and loaded requests from the handoff; calibration reuses loaded execution in the F01R order with 11 pauses. Day launches TCP once with `-t 86400 -O 5 --json-stream --forceflush`, and uses local `-t <=300` Sockperf chunks throughout that measurement without re-warming TCP. Tool startup/filtering can create gaps; report them rather than assuming 288 planned chunks ensure coverage. Actual process overhead is distinct from the command's requested acquisition duration and is recorded, never added to measured coverage.

**Alternatives considered**: Repeated short TCP tests during DAY, new idle/day baselines, per-provider N selection, new calibration orders or automatic repeats conflict with current sources. Exact N and UTC windows remain evidence-dependent inputs.

## Decision: bounded failures, evidence and ownership

**Rationale**: One supplied absolute monotonic deadline bounds notification, lead, pauses, process execution and drain/termination. Runtime shutdown grace is supplied/reserved within that deadline; no per-unit timeout extends overall lifetime. Refuse a unit if its requested protocol cannot fit the remaining budget plus reserved shutdown allowance. Tests use a fake deadline, never the Sheet's actual lifetime as a hardcoded timer. On failure stop subsequent short/trial units; preserve partial files and shutdown errors. A day probe failure leaves the original TCP process running and rotates to the next chunk within the same deadline; TCP failure ends measurement, never restarts it.

**Alternatives considered**: Unbounded wait, reset-on-every-phase timeout, overwrite/retry of failed output, deleting failed evidence, periodic SCP and cloud cleanup inside this module all breach ownership or evidence boundaries. Upstream F09 is responsible for retrieval and cleanup on every terminal path. B persistent server logs are external inputs/output references, not servers F03 may start or stop.

## Clarification coverage

Functional scope, identities/state, user sequence, reliability/security, integration, exceptions, constraints, terminology, acceptance and placeholders were reviewed. All are clear after the recorded engineering choices. Zero human questions; no critical research/source conflict detected. No additional research agents were needed: design choices and the only timing policy were supplied, and upstream source inspection resolved the notification spelling and timing limitation directly.
