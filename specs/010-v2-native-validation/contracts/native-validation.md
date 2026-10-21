# F08 Native Validation Public Interface

Status: planned/frozen for F08 implementation; depends only on merged F01R. These functions do not yet exist at planning time. Public records are described in [data-model.md](../data-model.md). No new shared contract/schema amendment.

## Ownership and imports

F08 owns `native.py`, `validation.py`, `calibration.py`, `summaries.py`, the F08 tests/fixtures and this spec directory. Import the existing F01R ValidatorInput/ValidationResult/ValidationStatus/ValidationFinding, artifacts and evidence helpers from `cloud_network_benchmark.contracts`. No imports from F03/F04/F11 or optional runner-local sidecars. F09 owns output persistence/CLI/manifests and matching integrated inputs; F12 owns binning/plots/reporting and comparisons of independent deployments.

## Signatures

```python
# cloud_network_benchmark.native
parse_sockperf(csv_path: Path, log_path: Optional[Path], *, source: SourceRef, log_source: Optional[SourceRef] = None) -> NativeParse
parse_iperf3(path: Path, *, source: SourceRef, format: str) -> NativeParse
rtt_statistics(samples_us: Sequence[float]) -> dict[str, object]
receiver_statistics(intervals: Sequence[ReceiverInterval]) -> dict[str, object]

# cloud_network_benchmark.validation
validate_bundle(value: ValidatorInput, *, evaluated_at: datetime) -> ValidationResult
evaluate_comparison(inputs: Sequence[ValidatorInput], results: Sequence[ValidationResult]) -> dict[str, ValidationResult]

# cloud_network_benchmark.calibration
select_common_n(results: Sequence[ValidationResult]) -> CalibrationRecommendation

# cloud_network_benchmark.summaries
summarize_validation(value: ValidationResult) -> str
```

`format` accepts only `json` or `jsonl`; source VM role/build and native sender flags establish whether an output contains receiver data. For a supplied log_path, log_source identifies that separate artifact/hash; a missing log_source leaves log-derived counts unavailable with a provenance finding. Parsers read existing files; no external commands, clocks, output writes, configuration changes or traffic. Expected I/O/format failures produce findings; programmer type errors are not swallowed. Parsers preserve intact records after localized errors; unsupported framing is never treated as a valid empty measurement. Callers validate materialized integrity before trusting metrics; parser source hashes are observed, not invented.

`validate_bundle` evaluates one provider attempt. Valid local data with absent matching peers has comparison `unavailable`; known non-research modes/disqualifications are `ineligible`. `evaluate_comparison` accepts same-run matching input/result pairs, rejects duplicate/missing/mismatched identity, returns one copied ValidationResult per run ID with updated ValidationStatus and comparison findings, and does not mutate inputs/results. It uses actual starts from inputs and validity/failures from results, not scheduler intent. F09 may store the returned copied results using existing models; F12 may reevaluate for a selected compatible group. Missing selected providers cannot be silently reduced to a smaller matched cohort. Build/settings/source mismatches remain explicit incompatibility, not automatic provider-performance labels.

`select_common_n` accepts all three provider calibration results for the same source campaign, after validation. It verifies the payload invariant/retained deployment/unit evidence described in the data model. It returns evidence for the existing CommonNState fields; F09/config preparation owns eventual explicit persistence. No public package `__init__.py` or CLI edit is necessary; consumers import modules directly.

## Supported native grammar

### Sockperf reference dialect

Primary source: [3.10 client.cpp](https://github.com/Mellanox/sockperf/blob/3.10/src/client.cpp), full-log/statistics functions. Recognize whitespace and tool log/preamble/separator lines surrounding the CSV header `packet, txTime(sec), rxTime(sec), rtt(usec)` and numeric rows in that order. Header must identify full RTT; a latency/half-RTT header is an incompatible setting. Preserve exported full-RTT values (no doubling). Parse reported total runtime/sent/received/skipped fields and the separate dropped/duplicated/out-of-order messages line, allowing native log prefix and ANSI color wrappers. Identical counters repeated in CSV preamble and log are corroboration; conflicting duplicates are findings and must not be added together. Require exactly one unambiguous invocation per CSV/log pair.

Preamble/header and reported values are not fabricated four-column normalized input. Packet column is an exported table index. Native sample rows reflect tool filtering and exclude certain replies; neither sample count nor packet index gaps establish generated messages or exact loss. SentMessages/runtime are invocation-scoped; do not subtract skipped from SentMessages a second time (source decrements its send sequence on a skipped send). A missing explicitly reported counter is unavailable. Generation ratio is actual sent/(1000*documented runtime) with reporting scope recorded; unavailable/mismatched runtime yields a finding, never a reply-row proxy. Truncated last rows and malformed interior rows retain intact samples with precise findings. Validate tx/rx/full-RTT consistency allowing only exported timestamp/RTT rounding (at most 0.003 microseconds), and reject nonfinite/negative values.

### iperf3 reference dialect

Primary source: [3.17.1 iperf_api.c](https://raw.githubusercontent.com/esnet/iperf/3.17.1/src/iperf_api.c), JSON-stream/interval/results functions. Monolithic objects have start/intervals/end and optional embedded server-output JSON; streams are newline event/data objects (`start`, `interval`, `end`, `error` and server-output). Server file may contain successive start-to-end sessions on a persistent listener. Associate a session to a unit from actual endpoints/start times/stream count/command timing; do not map by file order alone or mix sessions. Ambiguous association is unavailable with a finding.

Receiver evidence is B-produced interval `sum`/stream entries with sender=false, or explicitly receiver `end.sum_received`/stream receiver entries (including embedded server output). Never use client interval sender sums as receiver bytes. Prefer receiver intervals for interval summaries, reconcile against final receiver totals when both exist, and never add a final total to its intervals. Use one aggregate sum per interval, or sum per-stream receiver bytes with identical boundaries and count elapsed time once. Omitted intervals are excluded before measured offsets are normalized. Preserve source-native measured elapsed time; no hardcoded duration substitute, sum of rates or estimated sender replacement. End-only receiver evidence can establish an overall measured total when its scope/duration is explicit, but cannot manufacture per-bin intervals. Missing receiver intervals in continuous output means incomplete streaming evidence.

Malformed JSONL records/tails, duplicate keys, unsupported events/framing, error events and nonfinite/negative/boolean numeric values yield findings. Complete earlier interval events remain available. Duplicate or overlapping records cannot increase bytes/time; retain raw sources and report the conflict. Inconsistent/reset session clocks or cumulative counters are discontinuities, not negative measured throughput. A continuous day must have exactly one matching uninterrupted TCP session; restarted sessions remain partial evidence.

## Calculations and boundary policy

- Linear quantile: sort n samples; h=(n-1)q; interpolate at floor(h)/ceil(h), numerically equivalent to NumPy method='linear'. Known vector [1,2,3,4] has median 2.5 and linear q=.95 value 3.85 (public P95 unavailable at n=4). sample standard deviation sqrt(5/3). Generated reply sets 1..1000 give P95=950.05; 1..10000 give P99=9900.01. Sample thresholds remain strict.
- Receiver throughput: bytes from nonoverlapping measured receiver intervals, elapsed time counted once, times 8/1e9. A 125000000-byte one-second receiver interval is 1 Gbit/s. Warm-up/omitted data never enters numerator or denominator. Zero elapsed time is unavailable/error, not zero throughput.
- Interval comparisons are half-open [start,end), using actual timestamps. No precision tolerance silently promotes a subthreshold duration/ratio; inconsistent native timestamp precision is reported. No byte/time prorating across a boundary without native subinterval evidence. Fully bounded measured intervals can be combined; a straddling unknown-byte interval remains unavailable for that clipped metric.
- Active probe time is the union of actual acquisition windows, minus warm-up/filtering and recorded gaps, intersected with the receiver measured interval. F01R probe-test notification/timing/clock/command records plus native logs/sample bounds must corroborate actual windows. `overlap_seconds` is checked evidence, not a substitute for contradictory source timing. Process launch, requested -t or CSV count alone cannot establish active time. If an exact measured TCP start is absent, existing A-command monotonic start C0/completion C1, independently observed native omission O and successful native nonomitted receiver duration D constrain its start to [C0+O, C1-D]. Reject an empty/inconsistent bracket and require valid clock/time association; configured -O alone is not observed O. Tighten only with supported native events. Prove coverage only from the minimum overlap with active probe windows for every possible [s,s+D] in that bracket, subtracting observed gaps/filtering; a favorable midpoint or endpoint is not proof. Retain the bracket/coverage lower bound in metrics. iperf3 start.timestamp is a whole-second connection time, not a precise measured-start timestamp. Do not promote such bounds to exact UTC timestamps; actual-start matching remains unavailable when the required provider timing cannot be established. If even conservative windows cannot be reconstructed from supplied merged-contract evidence, coverage is unavailable. Preserve raw filtered sample support separately from acquisition time; packet loss is not a probe restart gap.
- Loaded short/acceptance/calibration: full requested measured receiver TCP duration and active measured-probe time >=99% of that interval. Idle: full requested measured probe interval. Day: continuous full 86400 receiver seconds and >=99% RTT time. At most 300 seconds per probe chunk, one TCP session through rotation; union overlapping chunks once. Generation>=.95 is independent.
- Clock error<=100 ms on both role mappings is required for aligned one-second outputs; missing error/mapping cannot prove alignment. RTT timed entirely on A remains usable. Actual measurement-start skew <=60 target-met; >60 through300 gives `comparison.start_target_degraded` warning and may stay eligible; >300 gives ineligible; missing/contradictory start evidence yields unavailable/ineligible respectively. Other eligibility gates always apply.
- Calibration: best tested count median includes 1; selected candidates only 2/4/8. All clouds must meet ratio>=.95. Complete evidence with none qualifying returns 8/false; missing/invalid/zero-best evidence returns unavailable. Actual N/windows are never inferred from fixtures.

## Validation and output policy

Validate all actual command options against the saved BenchmarkPlan: reply_every=1, UDP11111/TCP5201 bound to B private address, -l131072 and one-second interval, duration/omit, streams, CUBIC, zerocopy and native output/streaming options; reject rate caps, reverse/bidirectional or protocol-changing extras, while tolerating harmless argv spelling/order variants that preserve the resolved settings. Do not force versions to match across clouds when the declared source-built versions/settings are compatible; record identity and flag an actual evidence-backed incompatibility.

Read materialized snapshots using safe child-root paths and existing models; compare source hash/identity, selected providers/config/schedule, units, native commands/settings/version/build/timing evidence and diagnostics/metadata. Verify campaign/child manifests as recorded evidence, without rewriting lifecycle state. Required absent artifacts and contradictions are coded findings; `not_expected` artifacts are distinguished from missing required artifacts. Raw validation outputs listed as optional cannot require prior validation or hash themselves recursively.

Trust/integrity failures make data invalid; incomplete trusted measurement evidence with surviving intervals is partial; no assessable measurements is unavailable; all mandatory data checks pass is valid. A command failure or unresolved cleanup is retained separately in execution/bundle completion and blocks complete acceptance/comparison. Non-valid data cannot have eligible comparison under shared F01R rules. Slow metrics and high diagnostic values are retained; only structural evidence problems affect validity. Text summaries render all three outcome dimensions plus cleanup and reasons, not a single misleading success flag.

Output order follows declared unit order then artifact/code/location. `evaluated_at` is caller-provided and timezone-aware. Identical inputs/time give identical `ValidationResult.model_dump(mode='json')` and summary bytes. Every missing metric uses an Evidence unavailable reason. Summaries preserve counts separately, source identity/hash and valid intervals/gaps. No raw edits, file deletion, launching, automatic retries, performance labels or thesis conclusions.
