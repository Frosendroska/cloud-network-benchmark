# Research: source-bound acquisition evidence

## Authority and inspected baseline

Read AGENTS.md, constitution v2.0.0, docs/autonomous/WORKFLOW.md and these live read-only Thesis files: advisor/docs/design_document.md; DECISIONS.md; experiments/FRAMEWORK_IMPLEMENTATION_NOTES.md; experiments/EXPERIMENTS.md; tracking/IMPLEMENTATION_PROMPTS.md; tracking/M04_implementation.md. Binding requirements are actual tool timestamps/overlap, native filtering/omission, 99% diagnostic RTT time coverage, separate >=95% probe generation, native files, source-built identity, local saving and later copying. No source requires continuous CPU scheduling or guarantees delivery. No source permits changing measurement algorithms to achieve coverage.

Merged base `7a70a3139c5ee9efa1f8ea13c3f6de40e9048121` contains F01R/F02R/F03. `measurement.py:_timing` leaves actual boundaries/overlap unavailable. `conservative_sockperf_run_start_bounds` only bounds run start; `_sockperf_full_log_timing` says runtime/sample span are not coverage windows. Version-regex checks cannot justify a new acquisition claim. MeasurementTiming has no raw acquisition source. Read-only F08 T042/T047 explains why declared overlap/sample hull cannot unblock complete replay. No unmerged F08 runtime is imported or changed.

## Exact official sources

Retrieved 2026-10-22 from official repositories. Sockperf tag 3.10 object `396f9daa9e9e4991dd3661ff294ce89996d92b41` resolves to commit **5ebd327da983225321818c0355db922515e026bd**; iperf tag 3.17.1 object `6326580298857302356a615bf31d7491f8046469` resolves to commit **2acfcfe94e928e74542c9f107e02aa6dd4748a79**. These tags are unsigned; this is identity, not signature verification.

| Official source | Inspected evidence | SHA-256 of fetched file |
| --- | --- | --- |
| [Sockperf client.cpp](https://github.com/Mellanox/sockperf/blob/5ebd327da983225321818c0355db922515e026bd/src/client.cpp#L326-L345) | CSV accepted tx/rx, not all attempts/states | `a79a3b29dfad3f7c5adf8f93f770598d6cd6d380889c00d63a179f127534245f` |
| [Sockperf client.h](https://github.com/Mellanox/sockperf/blob/5ebd327da983225321818c0355db922515e026bd/src/client.h#L150-L176) | success/error/skipped send branches | `2308ccce41ebdc6579081a08201a5e13584c56a539abd29ee9e8eea65dcd1b4d` |
| [Sockperf ticks.cpp](https://github.com/Mellanox/sockperf/blob/5ebd327da983225321818c0355db922515e026bd/src/ticks.cpp#L100-L124) | native runtime clock mode | `f94bbf81a60e022c0899e6bba0a07c016962bb290ccc12690945db5230298de7` |
| [Sockperf ticks.h](https://github.com/Mellanox/sockperf/blob/5ebd327da983225321818c0355db922515e026bd/src/ticks.h#L184-L193) | no-rdtsc clock ticks; debugToNsec domain | `7079c302cb7516015644f7acfb3142c1a0211c26b92d81ca7f93bc2f1af6b187` |
| [Sockperf ticks_os.h](https://github.com/Mellanox/sockperf/blob/5ebd327da983225321818c0355db922515e026bd/src/ticks_os.h#L110-L128) | Linux CLOCK_MONOTONIC, inspected directly | `98d27aa60269e5886f6d582bbf5b127f9dac416afca3be671bebe2e8e6617da6` |
| [iperf_api.c](https://github.com/esnet/iperf/blob/2acfcfe94e928e74542c9f107e02aa6dd4748a79/src/iperf_api.c#L3314-L3385) | reset origin and native interval endpoints | `960341011c83b3990de0e431d082001d5126b05390cacdca23b23b6731e577fb` |
| [iperf_server_api.c](https://github.com/esnet/iperf/blob/2acfcfe94e928e74542c9f107e02aa6dd4748a79/src/iperf_server_api.c#L365-L407) | actual omit-reset callback; TEST_RUNNING precedes workers at 840–887 | `12a19ecf2dc20d500dfb60af740d9c5a96b562c53552c5cc65af41f7a31646e5` |
| [iperf_client_api.c](https://github.com/esnet/iperf/blob/2acfcfe94e928e74542c9f107e02aa6dd4748a79/src/iperf_client_api.c#L219-L257) | A omit reset is independent of B | `dfd7a23abc9be5d644bed38c756037024f31625da033a3e8cde35965a1b3a72b` |
| [iperf_time.c](https://github.com/esnet/iperf/blob/2acfcfe94e928e74542c9f107e02aa6dd4748a79/src/iperf_time.c#L34-L65) | monotonic with HAVE_CLOCK_GETTIME; microsecond truncation; wall fallback otherwise | `f7d52905eb00bb196f0930f50709dd872d7521d089feb9a0e28bdbeacc717a54` |

Checksums identify inspected bytes, not installed sources. Implementation pins upstream archives/commits, additive patches and resulting binaries. Preserve license notices. Do not vendor a full upstream tree.

## Decision 1 — Observe enabled acquisition without replacing measurement

**Decision**: Add minimal observational hooks to existing source builds. Sockperf retains constant per-invocation state: sender-loop entry, receiver-loop entry, native termination timestamp/reason and exact first/last transmitted-probe filter limits. Export one structured observation after measurement to stderr. Event values use the native no-rdtsc monotonic domain; emission time is not event time. No per-probe disk logging, new traffic, rate change, wait/handshake or duration adjustment.

**Source support**: client.cpp:997–1025 starts the receiver then announces Starting test before s_startTime; 720–724 loops client_receive until g_b_exit; 1104–1107 loops client_send_burst until g_b_exit. 651–678 captures s_endTime before setting g_b_exit. 437–445 derives testStart from first transmitted request plus native startup exclusion and testEnd from last transmitted request minus native final exclusion. 480–525 includes dropped attempts in duration but not RTT rows and skips reordered replies. Cleanup at 737–747 kills/detaches the receiver; no receiver join time can be inferred. Export already calculated filter limits rather than duplicating constants.

**Proof**: For the inspected time-based under-load path, enabled support lies inside the intersection of sender/receiver enabled state, native termination and native filter limits. Capture entry after observing the enabled loop guard, not at thread creation. Reuse the native timestamp before disable. Publish cross-thread observation state race-safely with a fixed slot; do not introduce a synchronization wait. Absent start/termination/filter evidence cannot yield a window. Require original logs/CSV/counts, expected settings, non-error outcome and the identified modified binary. All alternate error/early-return paths must be audited before accepting support; any unobserved exit fails closed.

**Limits**: This proves enabled acquisition lifecycle, not uninterrupted scheduling, cadence or successful replies throughout. Generation and RTT remain separate. Sample hulls cannot fill missing records. Kill/abort before export yields unknown support, never a controller-invented end. An interruption may retain closed evidence but cannot become successful complete execution.

**Alternatives rejected**: process/notification/runtime/sample hull lack state; per-probe writes add volume and timing overhead without requiring delivery coverage; tracing/eBPF/services exceed the small architecture. No timer, packet processing, warm-up or filtering change is authorized.

## Decision 2 — Export native receiver statistic anchors

**Decision**: Export existing iperf start_time/start_time_fixed at init/reset and completed receiver interval_start_time/interval_end_time, omitted state and stream/session identity. Bind the native cookie and endpoints to native A/B outputs; PID alone is insufficient for the persistent server. Emit after statistics values are fixed; use those values, not stderr read time. Keep stdout JSON/JSONL unchanged.

**Rationale**: iperf_api.c:2006–2028 initializes origin; 3318–3344 resets measured origin after omit; 3350–3385 captures actual interval endpoints. 892–908 start event contains settings; 950 connection timestamp is coarse and not a measured origin. TEST_RUNNING precedes worker creation, so is not first receive. Native statistics define throughput time including legitimate zero-byte intervals; slow/zero results remain evidence.

**Precision**: Export integer native microseconds; enclose truncation by [t,t+1 microsecond]. Presentation cannot create nanosecond accuracy. Wall-clock fallback builds remain unavailable for these monotonic semantics. Match native interval offsets/bytes/stream identity; conflicting/ambiguous sessions remain unknown. Never prorate straddling interval bytes.

## Decision 3 — Raw binding and clock domains

**Decision**: Optional evidence references on MeasurementTiming reuse ArtifactRecord and actual metadata/build artifacts. acquisition.py verifies closed bytes, checks installed semantics and yields windows incrementally. Declared scalar overlap is never authoritative. Capture boot_id and clock kind per VM/tool; command action ID, deployment, invocation/session and record byte range bind sources. Package patch digest alone is not installed binary proof.

**Rationale**: Monotonic values compare directly only within one VM/boot/clock domain. Tie existing ClockMapping evidence to that domain for cross-VM mapping, retaining both clock and timestamp uncertainty. A realtime/monotonic read pair is a mapping observation, not UTC synchronization accuracy. Unknown error/source remains unknown. Do not consume unmerged F04 or invent a clock-accuracy estimator: accept independently supplied mappings or emit unknown cross-domain bounds.

**Conservative math**: Probe support uses latest start/earliest end. Receiver endpoints have earliest/latest admissible bounds. The sufficient lower-bound formula in the contract uses covered inner receiver support divided by outer receiver duration; it cannot overstate any admissible window's coverage. It need not solve an exact minimization problem. Sample selection uses that same inner support and recorded transmit timestamps. Uncertainty never shrinks to pass a threshold. Same-VM comparisons avoid UTC conversion; cross-VM errors combine conservatively. <=100 ms remains the separate aligned-plot policy.

## Decision 4 — Build and delivery boundary

**Decision**: One serialized implementer owns shared contract, F02R build consumption and F03 production. Bind upstream archive/commit, patch digest, compiler/configuration, capability format and observed binary digest; reject stale cache/capability. Ship patches as package data, verify bytes and apply without fuzz. Retain original identity plus a new observation revision. Version text alone is insufficient. F08 consumes only after merge.

**Limits**: Additional reads/state/I/O can perturb timing. Do not claim equivalence, negligible overhead, advisor approval, calibration results or successful real execution. Compile/apply-check without traffic offline when available; separate registered paid acceptance proves actual output, session/clock linkage, retrieval and cleanup. Include added logs in existing disk/export feasibility checks, without a new campaign. If hooks require benchmark logic changes or cannot establish these state semantics, stop and report the exact mismatch rather than relaxing validity.
