# F08 Research and Engineering Decisions

## Source baseline

Implementation base `cef838e42fc620f90b5943f5a8d1528ddc0b0724`; constitution 2.0.0. Thesis read-only HEAD at planning read: `8927ff5a17a7113ac8080c15b412a4b8d8270744`. Read actual AGENTS.md, autonomous WORKFLOW.md, V2 design, DECISIONS.md (newest concurrent-study supersession), FRAMEWORK_IMPLEMENTATION_NOTES.md, IMPLEMENTATION_PROMPTS.md F08/F12/F09, EXPERIMENTS.md, M04 and roadmap. The current source packet overrides historical FLENT foundations. Formal advisor approval and real calibration/UTC windows are not established.

## Decisions

### Native grammar is source-backed

Decision: Support the full-RTT Sockperf 3.10 CSV/log dialect and iperf3 3.17.1 JSON/stream dialect as documented in the contract; require recorded actual build identity and flag incompatible variants. These are parser reference dialects, not a new mandated tool-version freeze.

Evidence: [Sockperf client.cpp at 3.10](https://github.com/Mellanox/sockperf/blob/3.10/src/client.cpp), `dumpFullLog`, `client_statistics` and `stream_statistics`; [iperf3 iperf_api.c at 3.17.1](https://raw.githubusercontent.com/esnet/iperf/3.17.1/src/iperf_api.c), `JSONStream_Output`, interval/results and JSON-finish functions. Sources were read on 21 October 2026; local Sockperf source excerpts also inspected under `/private/tmp/`. Sockperf exports a four-column full-RTT table with timing seconds and RTT microseconds amid text, with separate counters. iperf3 streams one event/data object per line and can preserve server output. Receiver flags/session identity and omitted markers must be respected.

Rationale: Synthetic normalized CSV alone would fail to prove real native compatibility. Alternatives: accept arbitrary column guesses or a new sidecar schema; rejected because they hide evidence gaps. Fixtures are authored small examples of this grammar, not claims of executed traffic. No upstream code is vendored.

### Existing evidence contracts suffice

Decision: Consume F01R `ValidatorInput`, command/timing/clock records, artifact declarations/records and saved native files. Return F01R `ValidationResult`; F08-specific parser and recommendation records are ordinary dataclasses/dictionaries, not shared schema additions. Compare supplied structured records with their materialized JSON evidence; contradictions yield findings.

Rationale: Native logs provide source measurement timing/counts, DTOs provide identity, chronology, gaps and clock mappings. An optional producer-local sidecar is not required. Alternatives: depend on unmerged F03/F04/F11 or redefine their schemas; rejected. If evidence cannot establish acquisition boundaries, mark coverage unavailable rather than use process launch plus sleep.

### Numerical semantics without extra dependencies

Decision: Implement NumPy's linear quantile definition over sorted samples: `h=(n-1)*q`, interpolate adjacent observations at floor/ceil(h). Sample standard deviation uses denominator n-1. Median n>=1, P95 n>=1000, P99 n>=10000 and standard deviation n>=2; unavailable reasons are explicit.

Rationale: The coordinator confirmed numerical equivalence satisfies the source's NumPy-linear requirement. The offline venv has no NumPy; one short interpolation requires no new dependency. Alternatives: tool-reported percentiles, averaging chunk quantiles or stdlib's different default quantile convention; rejected. Known vectors freeze equivalence; samples remain available for F12 bins.

### Completeness and timing are declared engineering policies

Decision: Loaded short/acceptance/calibration require full requested receiver duration and >=99% active measured-probe time; idle full requested probe interval. Union actual acquisition windows after excluding warm-up/tool filtering and subtracting gaps; never count CSV rows as time coverage. Day retains full continuous 86400 seconds and >=99% RTT time; <=100 ms error gates only aligned one-second outputs. Generation uses real sent count/reporting duration at >=95% target, separately from coverage and reply counts.

Decision: Actual measurement starts yield skew <=60 seconds target-met; >60 through 300 timing-eligible with degraded-target finding; >300 ineligible; missing starts unavailable. Compare only like slot/scenario/protocol/unit and valid data.

Rationale: Source explicitly delegates short thresholds and intermediate skew to Spec Kit; the coordinator authorized these policies consistently with F03/F11. These are engineering assumptions, not research results. No floating tolerance may silently relax a threshold: timestamp precision is retained, insufficient precision gives an explicit finding. Alternatives: infer overlap from launch times, equate loss/coverage to row counts or exclude all >60-second starts; rejected.

### Failure, integrity and reduction

Decision: Verify actual bytes/size/SHA-256 through safe F01R path resolution. Parse intact records from corrupt/truncated artifacts for diagnostic partial evidence, while reporting integrity failures. `invalid` for untrusted identity/integrity/unsupported evidence; `partial` when trusted usable intervals survive incomplete mandatory measurements; `unavailable` when none can be assessed; `valid` only when all mandatory data checks pass. The shared statuses forbid eligible comparison with non-valid data. Execution/cleanup remain separate facts in metrics/summary; failed cleanup prevents complete acceptance without fabricating a measurement failure. Missing provenance prevents acceptance and comparison; available metrics are retained. Validation never creates resources, deletes results or chooses retry/cleanup policy.

Rationale: Missing data must not become zero; failure should not destroy valid partial measurements. Alternatives: fail-fast losing all later evidence or mark every successful process valid; rejected.

### Calibration is a recommendation

Decision: Require twelve valid, unique ordered trials on one retained deployment per cloud, three each for 1/2/4/8. Median of each trial's receiver bytes/time metric, best tested median includes 1, then smallest qualifying common N in 2/4/8 at >=95% across all three clouds. Complete non-plateau evidence gives N=8, `plateau_found=false`; incomplete/invalid evidence gives no selection. Save canonical evidence digest and sources for later explicit freezing through existing CommonNState; do not mutate configs.

Rationale: F08 evaluates saved evidence; lifecycle/config persistence belongs elsewhere. Exact UTC windows, paid admission and actual N remain unresolved by fixture examples.

### Conservative timing when native timestamps are coarse

Decision: iperf3 3.17.1 connection timestamps do not establish a precise measured start. Existing command monotonic bounds plus independently observed omitted time and receiver nonomitted duration may constrain the measured start; compute coverage as a lower bound across every admissible interval, corroborated by actual probe timing/gaps. Preserve timing uncertainty and leave matched UTC starts unavailable unless actual evidence establishes them. No F03 sidecar/new shared DTO is needed.

Rationale: This source-consistent engineering refinement avoids pretending that configured sleeps or whole-second connection timestamps are exact measurement windows. Missing actual omission or a too-wide/inconsistent bracket cannot pass coverage. It does not relax the <=100 ms clock rule or invent a precise UTC event. Native start fields reviewed in the same primary iperf3 source linked above.
