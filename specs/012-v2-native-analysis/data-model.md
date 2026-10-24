# F12 local data model

Reuse existing ValidatorInput, ValidationResult, GitSnapshot, native SourceRef/RttSample/ReceiverInterval and Evidence. No shared model/schema changes. All calculated numbers are finite; booleans are not numbers; unavailable values keep reasons rather than zero substitutes.

## Selection and report container

- **Selection v1**: `format_version=1`, nonempty `runs` list of `{validator_input: path, validation_result: path}`, optional `utc_window={start,end}` with aware UTC-convertible times and `start < end`, and optional `cost_snapshot={path,sha256}` with lowercase SHA-256. No unknown keys. Resolve paths relative to the selector or accept explicitly supplied absolute local paths; no remote URLs. Runs themselves select experiments/providers/scenarios/schedule windows. The optional half-open UTC window restricts descriptive measurement rows, not original completeness or schedules. Unknown time attribution remains explicitly unavailable, not guessed.
- **Selected run**: Parsed existing input/result, file digests, full run/experiment/config/campaign/observation/attempt/deployment/provider/scenario/mode/context identity and actual schedule/measurement dates. Require result run/mode matches input. Deduplicate identical run/input/result evidence; reject conflicting duplicate run IDs. Current revalidated result, recorded result and comparison-specific result are distinct read-only values.
- **AnalysisReport**: F12-local frozen dataclass with `payload: dict` and `protected_paths: tuple[Path,...]`. Protected paths are publication guards, not serialized report data. Payload has format_version, semantic selection fingerprint, analysis Git/environment identity, source index, run/unit/bin/summary/context rows and figure specifications. No raw full-day sample array.

## Output rows

| Row family | Required fields and relationships |
| --- | --- |
| Run/unit | source IDs, mode/kind/phase/stream/trial/parent/deployment, execution/data/comparison status and reasons, current/recorded-validation difference, measured metrics, raw count scope, metadata, source refs |
| Bin | source run/deployment/unit, axis and half-open bounds, nominal seconds, observed receiver seconds/bytes, RTT count/statistics, coverage/gaps, status/reasons and source refs; DAY width=60 seconds, other default width=1 second |
| Deployment point | one value per eligible fresh deployment/provider/scenario/phase/metric and recorded window; no packet/trial/bin pseudo-replicates |
| Cohort summary | provider/scenario/phase/window group key and optional actual UTC-date/weekday dimension, contributing deployment/run IDs, n, median/Q1/Q3/IQR, individual points, eligibility/exclusions, actual UTC date range and selected schedule labels |
| Provider match | existing compatible slot/campaign/config/scenario/phase identity, expected versus present providers, existing F08 comparison status/reasons and source points |
| Placement effect | provider/phase/window, S1 and S3 group IDs/counts/dates, `S3-S1` median RTT difference in us and `100*(S3/S1-1)` throughput percent; zero/missing baseline unavailable |
| Diagnostic | role, recorded timestamp/local axis, counter/value Evidence, resets/gaps, alignment eligibility, source; no new performance thresholds |
| Reliability | selected unique-attempt denominator, provider/scenario/mode/context, success/partial/failure/missing-phase counts, failure classes and replacement links; no claim of all scheduled attempts outside selection |
| Cost | identified reservation/campaign/provider/stage and source date/hash, separate reserved/estimated/actual/final evidence, runtime/transfer only if sourced; dedup shared reservation totals and do not fabricate provider allocation |
| Source/figure | stable source key including run/artifact/path/hash; figure ID, linked rows and sources, units, scope, gaps/limitations, analysis identity and relative output path |

Each scalar uses existing Evidence-style value/reason representation. Summary fields describing repeated CAL/DAY views identify the retained deployment and are not merged into fresh-study summaries. Partial rows may be descriptive but cannot become eligible matched observations.

## Source and timing constraints

All consumed native/log/build/manifest/config/diagnostic evidence is resolved through existing safe child-root paths and checked integrity. Reuse F08 measured-sample selection/proof; packet CSV filtering and generation counts remain separate. Receiver intervals preserve session/omitted state, raw bytes and whole native elapsed time; parallel bytes are already aggregated by F08 native parsing. Support crossing a bin edge with unknown within-interval bytes cannot be prorated: primary throughput for the affected bin is unavailable, and the excluded interval's reason/source is retained. Do not count a partial interval twice.

RTT bin membership uses supported transmit time; raw RTT sample values retain F08 precision and count thresholds. Bounded clock/source support determines possible time placement; ambiguous boundaries stay explicit. Unknown UTC cannot be fabricated from requested start or first/last sample. Cross-host aligned one-second views require F08 eligibility; otherwise supported per-host data remain separately labeled.

## Deterministic reduction and publication

Sort runs/rows/sources/figures by stable semantic identity. Fingerprint the canonical sorted selection of input/result content digests and options; exclude descriptor filename, list order and output directory. Referenced source-file byte hashes remain recorded. Do not include fresh wall-clock timestamps or random renderer metadata. Preserve all source hashes/serialized identities before and after replay.

Publication refuses any existing output, equal/ancestor/descendant overlap with protected source files or raw roots, and resolved symlink overlap. Stage derived files under the requested output parent, publish only on complete success, and remove only the attempt's own temporary staging directory on failure. Never mutate existing reports or raw bundles.
