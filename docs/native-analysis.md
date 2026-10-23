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

Analysis uses the existing native Sockperf and receiver-side iperf3 parsers and current F08 validation/comparison policy. Receiver throughput is derived from receiver bytes divided by receiver elapsed seconds; parallel streams contribute bytes without multiplying elapsed time. RTT percentiles use raw replies and retain F08 sample-count thresholds. Deployment summaries count independent deployments and show points, median, and linear quartiles. Partial evidence, source hashes, current and recorded statuses, failure attempts, diagnostics, and unavailable reasons stay visible. Cost fields are shown only when the saved ledger, event digests, and referenced billing-source bytes can be verified; shared reservations are not allocated to individual providers. Unknown time alignment, missing charges, and unsupported bins remain unavailable rather than being inferred.

Outputs are `report.json`, seven normalized CSV tables (`runs`, `bins`, `deployments`, `comparisons`, `diagnostics`, `reliability`, and `costs`), paired PNG/SVG figures, and a static offline `index.html`. Figures use Matplotlib's Agg backend. The report records the analysis Git snapshot and source identities. Replaying an unchanged selection under the same revision and renderer environment is intended to reproduce every output byte.

The output directory must be new and must not overlap selected source files or raw bundle roots. Publication is staged beside the target and only the complete directory is made visible. F12 is descriptive: repeated calibration/diagnostic-day units are not fresh independent deployments, a short UTC subwindow does not revise original completeness, and no provider-wide or recurring-week inference is made.
