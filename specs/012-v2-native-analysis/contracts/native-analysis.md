# F12 native analysis interface and output contract

## Local entry and CLI

```text
analyze_selection(selection_path: Path, *, analysis_git: GitSnapshot) -> AnalysisReport
write_analysis(report: AnalysisReport, output_root: Path) -> tuple[Path, ...]
cloud-network-benchmark analyze --selection PATH --output NEW_DIRECTORY
python -m cloud_network_benchmark analyze --selection PATH --output NEW_DIRECTORY
```

These are planned F12 functions/command, not existing executable behavior at planning handoff. CLI adds a narrow dispatch before main's existing config-specific args are accessed. Existing validate/resolve/dry-run/init behavior is unchanged. Structural descriptor/I/O/publication errors return CLI exit2 with concise reason; successfully generated reports return0 even when their datasets contain failures, clearly labeled in data/HTML. Missing optional plotting dependency is a clear actionable error, not silently skipped figures.

Selection v1 contains `runs: [{validator_input, validation_result}]`, optional bounded `utc_window` and digest-pinned `cost_snapshot`, as defined in data-model.md. It is F12-local and adds no shared schema root. It uses saved existing models/manifests and therefore does not depend on unmerged F09 persistence. Revalidation uses each saved result's evaluated_at. Current F08 state determines trust/eligibility; a saved valid label cannot override changed raw bytes. Record changed local validation explicitly. Reevaluate selected matched groups with evaluate_comparison; do not mutate original inputs/results.

## Numerical and grouping rules

- Reuse F08 native parsing, rtt_statistics and receiver_statistics, including linear quantiles, P95>=1000 replies, P99>=10000, sample standard deviation and separate reported count scopes. Time-bin RTT statistics use underlying selected samples; no averaging of chunk quantiles.
- Receiver throughput is `8*sum(non-omitted receiver bytes)/sum(their measured elapsed seconds)/1e9`. Native parser aggregates parallel stream bytes for one interval; never sum parallel elapsed time, average interval rates or substitute sender totals. A native unique receiver total can support an overall measurement if F08 accepts it, but cannot create missing per-bin intervals.
- Bins are half-open, width60 seconds for DAY and default1 second otherwise, anchored to source-supported measured start in the stated domain. RTT membership uses supported transmit time. Preserve raw sample/clock precision, source windows, omit/warm-up exclusions, partial tails and gaps. Map receiver offsets only with corroborated session/reset origin and clock evidence, never a first-sample hull or configured omit shift. Reuse acquisition sources and F08 selection helpers read-only; native boundaries/settings/bytes must be corroborated by existing replay. Unknown mapping or a receiver interval straddling a bin edge makes its clipped-byte metric unavailable; retain a source-linked excluded interval and any other independently supported evidence.
- Overall measurement totals are not reconstructed by summing bins that have boundary omissions. DAY one-minute and per-chunk RTT plus raw-sample distribution views remain separate repeated measurements; no pooled DAY RTT quantile is made from chunk metrics. Do not invent DAY/CAL idle or inflation values. Original completeness remains F08's full-duration/99-percent decision even when a UTC subwindow is selected.
- Fresh points require standard short-study mode, valid contributing unit/data and accepted execution/cleanup for corresponding aggregate eligibility. Count unique deployment/provider/scenario/phase identity; retain invalid/partial attempts as labeled descriptive/reliability rows, not trusted comparative points. Duplicate identical selection entries collapse; conflicting duplicate run or deployment evidence is an explicit error/finding. A replacement's failed predecessor remains in reliability but is not an extra accepted replicate.
- Provider matching uses F08's current full expected cohort and compatible schedule/slot/phase/settings. Do not compare by nearest timestamp, silently reduce peer sets or change skew/frozen-N policies. For S1/S3, use valid standard fresh deployments grouped within provider, phase and recorded window with compatible non-placement settings. Intentional placement/config differences remain visible. Report group distributions, not invented pairs. RTT difference=`median(S3)-median(S1)` us; throughput change=`100*(median(S3)/median(S1)-1)` percent; missing/zero S1 is unavailable.
- Deployment summaries report n, individual points, median and linear Q1/Q3/IQR of deployment metric values. A summary of deployment P95 points is labeled a deployment-metric distribution, not a pooled packet P95. Actual UTC date ranges, weekday/selected-window summaries and concurrent same-week deviations are descriptive; no recurring-week/day or provider-wide inference.
- Float arithmetic acceptance uses independently calculated expected values with rel_tol=1e-12 and abs_tol=1e-9 in the stated numeric unit; integer byte/count/source identities are exact. Availability/coverage thresholds follow F08 exactly and gain no tolerance that promotes an otherwise invalid observation.

## Context evidence

Diagnostic panels include both roles' CPU/core/steal, retransmissions, drops/errors, byte counters, metadata, missing samples and resets. Aligned one-second cross-host overlays require existing <=100-ms eligibility; other supported per-host views remain labeled local/relative, never silently aligned. No automatic performance-warning classifier is added.

Reliability denominator is all explicitly selected unique attempts, with provider/scenario/mode, failures/partial providers, missing phases and retry/replacement links. Cost table retains dated actual/estimate/reservation scope and sources. Optional cost_snapshot reads the existing F11 schema_version1 saved JSON without ledger locks/writes; join matching reservation/campaign/provider evidence, verify recorded event digests and supplied source hashes before claiming verified charges. Missing/unknown provider bills, runtime and transfer remain unavailable. Deduplicate reservation-level totals; do not allocate campaign amounts to providers or add all cumulative billing events together. No price refresh or paid execution.

## Required output set

```text
report.json
runs.csv; bins.csv; deployments.csv; comparisons.csv
diagnostics.csv; reliability.csv; costs.csv
figures/*.png; figures/*.svg
index.html
```

report.json is the complete structured payload with format_version1, canonical semantic selection fingerprint/options, source index, original/current validation status, analysis Git dirty/diff identity, renderer/numeric versions, figures and all row families. CSVs are normalized projections with fixed column order, numeric units, stable Evidence value/reason columns, sources and analysis identity; empty datasets still have headers and reasons in report/HTML. Figure specifications link contributing row/source IDs; provide per-run time-series and distributions plus eligible deployment/provider/placement comparisons. Mark gaps and failures, do not smooth/clip away spikes. An empty/ineligible figure has a labeled no-eligible-data view rather than fabricated points.

Use Matplotlib Agg with optional `analysis = ["matplotlib>=3.7,<4"]`. Keep DejaVu Sans, fixed SVG hash salt, no SVG Date or nondeterministic metadata, and recorded exact environment. Histograms use 50 equal-width edges derived deterministically from supported raw sample min/max in two passes; constant values have one explicit finite-width bucket. All deployment points are shown with median/IQR. Repeated identical input/settings/revision/environment must reproduce every file byte independently of descriptor order/name and output path.

HTML uses escaped labels, static tables, relative local image/CSV/JSON links and no external fonts/scripts/CDNs. Include actual dates, mode/deployment counts, study-scope/initial sustained-load context and gaps, separate execution/data/comparison states, source/analysis identity and unavailable reasons. Source links identify local bundle/artifact references without copying raw files into the report. Do not write thesis conclusions or canonical thesis figures.

Output must be a new directory with no equal, ancestor or descendant overlap with any resolved protected source file or raw root. Preflight symlink and ancestor overlap, render in a private sibling staging directory, and atomically publish a complete set. Existing report/source contents are never overwritten; a failed render leaves no successful output manifest. Only own temporary staging files may be removed on failure.
