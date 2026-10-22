# F04 engineering decisions and source traceability

Date: 2026-10-22. Implementation base `7a70a3139c5ee9efa1f8ea13c3f6de40e9048121`; read-only Thesis revision `00ded5741d9afcc200e6c19bb517e69129e22717`.

## Source reconciliation

Read AGENTS.md, constitution v2.0.0, autonomous WORKFLOW and routed Thesis sources: `advisor/docs/design_document.md` sections 3/5/8; `DECISIONS.md` (especially 17–19 October supersessions); `experiments/FRAMEWORK_IMPLEMENTATION_NOTES.md` measurement/diagnostics, fixed configuration and result sections; `tracking/IMPLEMENTATION_PROMPTS.md` F04; `experiments/EXPERIMENTS.md`; `tracking/M04_implementation.md`; roadmap dependency/F04 sections; `STATUS.md` and `tracking/TASKS.md`. These are references, not copies. Formal advisor approval and cloud readiness remain unestablished.

Decision: Use the current F04 list, all executed modes and no automatic host warnings. Rationale: current packet/constitution refine the older 27 August FLENT-era memory-utilization and TCP-only proposal. Alternative: importing historical memory metrics or FLENT phase layout would expand or contradict the current bounded packet. No genuine research/source conflict remains.

## R1 CPU and counters

Decision: Use aggregate/per-core `/proc/stat` first-eight-field deltas, named `Tcp: RetransSegs` from `/proc/net/snmp`, and selected NIC sysfs `statistics/*` counters. Arithmetic, units and edge cases are declared in data-model.md. Active CPU excludes idle, iowait and steal; steal remains separate. Guest/guest_nice are excluded from the total to avoid counting already included guest execution twice. This definition is an engineering assumption, not a new performance exclusion.

Rationale: kernel documentation describes aggregate/per-core counters and warns that iowait can decrease. A decrease therefore means unavailable interval evidence, not proof of reboot/reset cause. See [Linux proc documentation](https://www.kernel.org/doc/html/latest/filesystems/proc.html) section 1.8. Alternatives: shelling out to a monitoring package or applying wrap/clamp assumptions would hide evidence or add dependencies.

## R2 Cadence and missing data

Decision: Read baseline at local monotonic t0, then target t0+1, t0+2, ...; use actual elapsed snapshot time, advance to the next future tick after an overrun, and never catch up with back-to-back invented samples. The baseline itself emits no DiagnosticSample. At an exact final deadline, the last scheduled snapshot is allowed but no fresh subprocess is started beyond the bound. A duration shorter than one second can return an empty closed diagnostic file with explicit no-complete-interval outcome.

Rationale: cadence and elapsed deltas remain truthful across scheduler delays and wall-clock jumps. Read errors invalidate the affected current baseline, so recovery requires two valid consecutive readings; never bridge an unobserved read as if it were a normal one-second sample. A real delayed interval with two observed endpoints may retain its full delta with actual duration and gap reason.

Alternative: fabricated gap rows or division of a long delta into seconds would imply observations not made. Integer counters use Python integers, not fixed-width wrap arithmetic.

## R3 Static metadata provenance

Decision: Supply observed provider fields from `ProviderDeploymentOutput` and optional matching `BootstrapResult`; collect guest CPU/kernel/NIC/MTU/CUBIC once. Retain current version and SHA-256 observations of available installed Sockperf/iperf3 binaries and compare them with supplied build records. Per-tool missing/mismatched evidence remains visible inside structured fields.

Rationale: source config and intended acceleration are not observations. Driver links identify the bound driver; AWS ena/GCP gve identify the observed driver mechanism, without certifying Express/EFA or Tier_1 exclusions. Azure netvsc requires an observed linked SR-IOV VF/driver before reporting that mechanism; absence is unavailable verification, not an invented disabled/enabled state. The same evidence collection runs for both roles. Provider-specific documented limits are supplied identified documentation evidence, never probed bandwidth or a live price/limit lookup.

Alternative: defaulting to desired MTU 1460/CUBIC/image/acceleration would mask failed configuration. F04 reports actual differing values; validation policy remains downstream.

## R4 Clock evidence

Decision: Support read-only local `chronyc -n tracking` (LC_ALL=C, no shell) as the first concrete error-estimate reader, with explicit unavailable fallback if absent/failed/unsupported. It does not install/configure a daemon or contact an external host. Collect a mapping at baseline and each sampling observation. A bounded local query per tick is acceptable for this low-frequency diagnostic; it must not accumulate outstanding processes or displace deadlines silently.

The [chronyc manual](https://chrony-project.org/doc/4.5/chronyc.html) documents local monitoring, reference identity and the estimate `abs(system_time_offset) + root_dispersion + root_delay/2`. Treat it as an estimate conditional on the reference clock, never independently proven UTC accuracy. Our conservative capture allowance adds the full query elapsed time plus half the adjacent UTC-read monotonic bracket, in seconds before conversion to milliseconds. Reject non-Normal leap status, local-mode reference 7F7F0101, missing/non-finite/negative delay or dispersion, and invalid stratum/reference evidence as unavailable accuracy. Preserve observed source/status details even when accuracy is unavailable.

Rationale: an NTP-enabled boolean or a monotonic/UTC pair alone cannot quantify absolute error. The collector uses a 0.2-second maximum clock-query timeout, further limited by remaining collection duration; this is an engineering subprocess bound, not a research validity threshold. Timing overrun is reported as sampling evidence. At the hard duration bound, capture UTC/monotonic with unavailable error rather than start a query. Clock pair eligibility requires both estimates <=100 ms and both observed synchronized sources. Unknown sources cannot pass; UTC reversal/discontinuity remains visible in mappings and does not change counter/deadline arithmetic. Overall interval alignment must additionally inspect coverage/discontinuities downstream.

Alternative: assuming provider default synchronization or installing another clock/telemetry service would invent evidence or expand scope. Other clock readers can be added only for an actual supported deployment need; none are speculative requirements now. Missing chrony is an explicit paid-acceptance limitation for aligned plots, not an offline test skip or reason to lose RTT.

## R5 Guest runtime and lifecycle

Decision: Put pure parsers and streaming collector in one standalone stdlib module and typed controller projection in one adapter. Rooted Linux fixtures exercise the same parsers as real guest reads. Use exclusive safe file creation, compact JSON with NaN rejected, and line flushes. Graceful SIGTERM/KeyboardInterrupt requests bounded stop; abrupt SIGKILL/VM loss cannot promise closure. Return failures and available/closed paths; a write/close error cannot become successful closure. No resume/append-to-old-evidence behavior is introduced.

Rationale: Ubuntu has a Python runtime but no project package/Pydantic guest installation is implied by F02R. A directly copied file avoids a new installation pipeline. F09 owns authenticated transport and lifecycle; this feature is independently testable before integration. Alternatives: importing the controller package on the guest or duplicating parsers in an embedded shell script adds unnecessary coupling.

## R6 Durable clock artifact

Decision: Additional role-specific `diagnostics/vm_a-clock.jsonl` and `diagnostics/vm_b-clock.jsonl` contain unchanged ClockMapping lines. `DiagnosticSample` cannot include synchronization/error fields; keeping a small separate typed file avoids amending shared records. Coordinator explicitly authorized this on 2026-10-22. F09 owns retrieval/checksum/timing projection and downstream consumers own their integration follow-ups.

Rationale: a live in-memory result cannot carry all guest timing evidence across controller failure. Existing ArtifactInput permits explicit extra artifact paths/kinds. Alternative: embedding non-schema fields into diagnostics or changing shared ArtifactLayout would violate contract ownership.

No unsettled engineering choices, source conflicts or evidence-dependent research values are assigned to implementation.
