# Analyze saved native results

F12 analyzes existing local F01R validation bundles. It does not launch experiments, contact providers, repair source evidence, or make thesis conclusions. It revalidates each selected pair using the saved validation timestamp, so a saved success label cannot hide changed native bytes. A successful command means the report was completely published; dataset validity and comparison eligibility are reported separately.

Install the optional renderer and run the CLI:

```sh
python -m pip install -e '.[analysis]'
cloud-network-benchmark analyze --selection selection.json --output results/processed/report-001
```

The selection file is JSON version 1. Paths are local and relative to the selection file unless absolute. Optional `utc_window` has UTC `start` and `end` timestamps with a half-open end. Optional `cost_snapshot` names a saved F11 schema-version-1 ledger and its lowercase SHA-256.

```json
{
  "format_version": 1,
  "runs": [
    {
      "validator_input": "run-a/validator-input.json",
      "validation_result": "run-a/validation-result.json"
    }
  ]
}
```

Analysis uses the existing native Sockperf and receiver-side iperf3 parsers and current F08 validation/comparison policy. Receiver throughput is derived from receiver bytes divided by receiver elapsed seconds; parallel streams contribute bytes without multiplying elapsed time. RTT percentiles use raw replies and retain F08 sample-count thresholds. Deployment summaries count independent deployments and show points, median, and linear quartiles. Identical retries for one deployment are coalesced; attempts with conflicting accepted values are excluded from that metric and reported as a cohort finding. Provider comparisons retain full expected peers and F08 eligibility reasons. S1/S3 views require compatible recorded non-placement settings and report distinct config/placement identities, independent scenario medians, direction, dates, and same-week deviation; they do not invent pairs.

Per-bin receiver bytes require a native iperf session timestamp, F02R clock uncertainty, and whole-interval containment within a recorded receiver acquisition window and one output bin. A UTC selection filters both RTT and receiver evidence. Intervals crossing a bin/window boundary are not prorated; acquisition gaps remain gaps, and unsupported origins or clock mappings produce unavailable receiver values. The full F08 validation/completeness decision remains separately visible. Partial evidence, source hashes, current and recorded statuses, failure attempts, both-role diagnostics, and unavailable reasons stay visible. Diagnostic-day output places all probe chunks on the parent continuous-day origin, retaining chunk identity and unsupported minutes, and gives a descriptive initial-300-second versus later-supported-bin view without an idle baseline or causal label. Diagnostic clock alignment is scoped to the overlapping measurement unit; a DAY sample uses its concrete probe-chunk mapping. Per-host diagnostic values remain visible when cross-host alignment is unavailable.

Cost fields are shown only when the saved ledger, matching reservation/campaign/provider, billing history, digest-checked event payloads, and referenced billing-source bytes agree. The amount, cumulative/delta semantics, actual/estimate status, and final event are recomputed from verified history and checked against the saved aggregate. A mismatch is unavailable. Shared reservations are represented once in report totals and are not allocated to individual providers. Unknown time alignment, missing charges, and unsupported bins remain unavailable rather than being inferred.

Outputs are `report.json`, seven normalized CSV tables (`runs`, `bins`, `deployments`, `comparisons`, `diagnostics`, `reliability`, and `costs`), paired PNG/SVG figures, and a static offline `index.html`. Figures separate RTT from receiver throughput and keep run, measurement unit, phase, and schedule window series distinct; unsupported gaps are not connected. Placement reports show separate RTT and relative-throughput panels. Figures use Matplotlib's Agg backend. The report records the analysis Git snapshot, Python/numeric/renderer versions, input source identities and digests, supported study dates, and deployment date/weekday groups. Replaying an unchanged selection under the same revision and renderer environment is intended to reproduce every output byte.

The output directory must be new and must not overlap selected source files or raw bundle roots. Publication is staged beside the target and only the complete directory is made visible. F12 is descriptive: repeated calibration/diagnostic-day units are not fresh independent deployments, a short UTC subwindow does not revise original completeness, and no provider-wide or recurring-week inference is made.
