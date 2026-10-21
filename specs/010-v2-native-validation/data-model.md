# F08 Data Model

Reuse merged `contracts/validation.py`, `measurements.py`, `artifacts.py`, `campaign.py` and `common.py` unchanged. Names below are F08-local value records/metrics payloads, not new shared DTOs or JSON-schema infrastructure.

## Native parser records (`native.py`)

- **SourceRef**: `artifact_id`, relative `path`, observed `sha256`, `unit_id`, `deployment_id`, `producer`, `binary_version`/source identity evidence. Every record carries `source` and one-based native line/event location; separate log counts retain a separate log SourceRef/hash.
- **RttSample**: `packet_index` nonnegative integer (export index, not transmitted sequence/loss evidence), `tx_seconds`, `rx_seconds` finite monotonic-clock seconds with `rx>=tx>=0`, `rtt_us` finite nonnegative full RTT. Retain exported RTT precision and reject timing/RTT disagreement beyond documented native rounding, rather than silently halving/doubling RTT. Scope is source invocation/chunk.
- **ProbeCounts**: `sent`, `received`, `dropped`, `duplicated`, `out_of_order`, `skipped` as `Evidence[int]` nonnegative integers; `runtime_seconds` positive finite evidence and reporting-scope label. Never substitute missing counts with zero; a documented omitted skipped field means zero only for the explicit native branch that omits it when none were skipped. Sent messages are the tool's actual send count, not CSV row count. Reported runtime is not automatically the post-warm-up measured interval.
- **ReceiverInterval**: `session_id` stable source/session ordinal, `start_seconds`, `end_seconds`, `seconds`, `bytes`, `omitted`, `sender=false`, `source`. Finite nonnegative start, end>start, seconds>0, bytes integer>=0 and bool rejected as a number. Duration/end consistency follows native precision. Summed parallel stream bytes share one elapsed interval; do not sum parallel stream seconds. Keep original offsets; map to UTC through actual session start/measurement timing, never assumed wall time.
- **NativeParse**: immutable ordered tuples `samples`, `receiver_intervals`, `probe_counts`, `findings` (existing ValidationFinding), and source/session metadata. One source parse may be partially usable. Preserve each session and malformed-line location; no crash or fabricated complete dataset after a bad tail. F12 can parse one chunk at a time.

## Metric evidence

`ValidationResult.metrics` uses existing `StructuredEvidence`. When any evidence is available, its value contains this stable `format_version=1` payload:

| Key | Meaning and constraints |
| --- | --- |
| `identity` | Full supplied F01R identity plus provider/scenario/mode/context; preserve shared deployment. |
| `execution` | Supplied execution state and primary failure, retrieval/cleanup failures, cleanup state and `bundle_complete` bool. No execution-state rewrite by validation. |
| `provenance` | Config/resolved/schedule hashes, Git snapshots, tool/build identity, source refs; unavailable evidence with reasons. |
| `units` | Ordered by declared unit order, one record per unit; unit ID/kind/phase/stream/trial/parent/deployment and status/findings. |
| `units[].rtt` | `valid_replies`, `median_us`, `p95_us`, `p99_us`, `sample_stddev_us`, `p95_minus_median_us`, `latency_inflation_us`; metrics are Evidence values, with explicit minimum-count/mode/baseline reasons. |
| `units[].tcp` | `received_bytes`, `measured_seconds`, `throughput_gbps` evidence; measured receiver intervals/source references, never sender fallback. |
| `units[].probe_counts` | Separate per-invocation ProbeCounts evidence and `generation_ratio` on its own reporting scope. Do not label generated/replied ratios as packet loss. |
| `units[].coverage` | Requested/observed measured duration, active covered seconds/ratio evidence, clipped valid intervals and explicit gaps; full-day continuity; measured TCP start bracket and coverage lower bound when exact timing is unavailable; no inferred perfect coverage. |
| `alignment` | `one_second_eligible` evidence, clock-error estimates and reasons; RTT on A is separate. |
| `diagnostics` | Both-VM supplied samples and metadata evidence, reset/missing/gap observations without performance warnings. |
| `research_eligible_mode` | true only for short_study; acceptance/calibration/day are distinguishable from fresh-pair research comparison. |

Unavailable entire metrics use the shared unavailable wrapper. Native samples/receiver intervals remain retrievable through parser functions; metric JSON does not need to duplicate a whole day of RTT samples. Every interval includes source references so F12 can reread native bytes and recompute bins.

## Findings and reduction

Existing `ValidationFinding(code,severity,message,unit_id,artifact_id)` is reused. Stable code families: `native.*`, `artifact.*`, `identity.*`, `provenance.*`, `command.*`, `timing.*`, `generation.*`, `cleanup.*`, `comparison.*`, `calibration.*`. Messages include source location/reason without secret-bearing raw payload. Sort by declared unit order, artifact ID, code, source location/message for deterministic replay. Existing result fields retain supplied `run_id`, `mode`, caller-provided timezone-aware `evaluated_at`.

Data reduction: trusted complete mandatory data -> `valid`; trusted usable subset with missing/truncated measurement intervals -> `partial`; integrity/identity/settings contradictions or unsupported essential evidence -> `invalid`; no assessable measurements -> `unavailable`. Highest trust failure wins over partial; retain diagnostic partial metrics with explicit source-trust findings. Isolated diagnostic counter resets/missing samples stay explicit diagnostic evidence and do not alone invalidate primary measurement intervals. Missing required diagnostic/metadata artifacts do prevent complete data. Non-valid states require reasons. Cleanup failure affects `bundle_complete=false` and comparison acceptance, not the validity of otherwise correct measured bytes. Execution and data status are independent.

Comparison reduction: known disqualifying facts -> `ineligible`; missing prerequisites/peer starts -> `unavailable`; `eligible` only with `data_validity=valid`, complete accepted bundle, research mode, matched slot/phase/scenario/protocol/providers, resolved frozen N and eligible actual timing. Local validation alone cannot assert an all-provider match. Separate one-second alignment eligibility cannot suppress single-host RTT.

## Calibration recommendation (`calibration.py`)

**CalibrationRecommendation** is a small frozen dataclass with `common_n: Evidence[int]`, `plateau_found: Evidence[bool]`, per-provider/per-count median and ratio maps, `source_campaign_id: Evidence[str]`, canonical evidence `source_sha256: Evidence[str]`, sources and findings. Complete calibration has three providers, one retained deployment per provider, twelve unique ordered trial indices per provider, three valid trials/count. Each trial's `throughput_gbps` must be finite and nonnegative; all-zero best median makes ratio undefined and recommendation unavailable. No extra repetition, duplicate, invalid/missing trial or mixed campaign/deployment can be silently discarded. No-plateau fallback only applies to complete valid evidence with defined ratios. Digest is SHA-256 of canonical sorted JSON containing campaign/source identities/hashes and trial throughput evidence, excluding evaluation time. Existing CommonNState can later consume the recommendation but F08 never freezes/mutates configuration.
