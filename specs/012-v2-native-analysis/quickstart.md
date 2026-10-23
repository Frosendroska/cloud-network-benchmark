# F12 validation and user handoff

F12 implementation is complete on assigned branch `codex/f12-v2-analysis` in `/private/tmp/cnb-f12`. The implementation/source-test checkpoint is `5b3552d`; the assigned F08 base was `4a80f4af86feddc86e4ab930d254a19e29b35591`; approved main updates were integrated, including GCP PR20 `527451d8697917c795d8dc9224cf8a17947c4311`. The feature was implemented only in its declared modules, CLI, optional plotting dependency, tests, docs, and feature artifacts. Shared DTO/schema, F07/F08 production code, Thesis, and coordinator state were not changed. Convergence remains a separate coordinator stage.

## Runtime and boundaries

Validation used Python 3.11.13 on macOS 15.2 x86_64, Matplotlib 3.11.2, NumPy 2.4.6, and Agg with `MPLCONFIGDIR=/private/tmp/cnb-matplotlib-config`. PowerShell 7.5.11 ran from `/private/tmp/cnb-tools/powershell-7.5.11/pwsh` (arm64 binary under the available host runtime). No clouds, benchmark traffic, credentials, or paid acceptance were used. The full suite's temporary `configs/tests/f08-*-aws.yaml` fixtures were cleaned up; final search found none.

Read-only schema validation and CLI checks pass. Existing native receiver interval attribution is retained: where it cannot establish exact UTC-to-bin support, per-bin throughput stays unavailable; validated aggregate receiver throughput remains available. DAY reports are descriptive and do not imply an idle baseline, causal burst effect, or continuous every-send proof. Cross-provider comparisons require the full expected peer set and reuse F08 policy. S1/S3 results compare independent compatible scenario distributions; they do not fabricate matched pairs. Receiver bytes are not prorated across bins.

## User command and inputs

A selection pairs existing saved `ValidatorInput` and `ValidationResult` JSON files:

```json
{
  "format_version": 1,
  "runs": [
    {"validator_input": "run-a/validator-input.json", "validation_result": "run-a/validation-result.json"}
  ]
}
```

Paths resolve relative to the selection. The CLI is:

```sh
cloud-network-benchmark analyze --selection selection.json --output results/processed/report-001
```

The output must be a new directory disjoint from the selected sources. Exit 0 means publication succeeded; validation and comparison eligibility remain explicit in the report and do not become an overall “all valid” claim. The command writes `report.json`, normalized CSV tables, PNG/SVG figures and offline `index.html`. Detailed calculations and limitations are in [native-analysis.md](../../docs/native-analysis.md).

## Checks and exact results

All commands ran from `/private/tmp/cnb-f12` with the assigned offline runtime and `PYTHONPATH=src` (plus `THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis` where required). The full command included the official PowerShell directory on `PATH`, Agg, and the isolated Matplotlib configuration directory.

| Check | Result |
| --- | --- |
| Focused F12: `tests/unit/test_analysis.py tests/unit/test_analysis_plots.py tests/contract/test_analysis_interface.py tests/integration/test_analysis_replay.py` | Exit 0; 30 passed in 95.64s. |
| Existing F08/native/CLI regressions: `tests/unit/test_native.py tests/unit/test_validation.py tests/integration/test_native_validation_replay.py tests/integration/test_validate_resolve_cli.py tests/integration/test_dry_run_cli.py tests/integration/test_initialize_cli.py` | Exit 0; 129 passed in 54.77s. |
| Full credential-free suite: `python -m pytest -q -p no:cacheprovider` | Exit 0; 1284 passed, 0 skipped in 261.79s. Command used `PATH=/private/tmp/cnb-tools/powershell-7.5.11:$PATH`. |
| Canonical contracts: `python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts --check` | Exit 0; no schema drift. |
| CLI: `python -m cloud_network_benchmark analyze --help` | Exit 0; exposes only `--selection` and `--output`. |
| `git diff --check` | Exit 0. |
| Renderer environment: `pwsh -v` with official path; Python version/import/backend probe | Exit 0; PowerShell 7.5.11, Python 3.11.13, Matplotlib 3.11.2, NumPy 2.4.6, Agg, x86_64. |

Focused and full integration replay exercise materialized F08 lifecycle evidence for short-study, acceptance, calibration, and diagnostic-day modes through the F12 CLI. Reversed descriptor order and different output names produce byte-identical report files; checks cover source hashes, status recomputation, independent expected arithmetic, missing/partial evidence, full provider cohort eligibility, S1/S3 distribution comparison, and failure cases. Integration support adds exact supplemental artifact declarations and matching child-manifest rows for observation/build files already produced by the F08 fixture; it does not synthesize success or relax production validation. The S1/S3 fixture uses a disposable real config copy and adapts only the synthetic schedule row/summary label.

## Visual and replay review

The generated PNGs for time-series, raw RTT distribution, deployment points, placement comparison, empty placement, and DAY views were opened at full size. Long category labels in deployment points were split across lines after visual inspection; the revised figure has readable, unclipped labels and a source caption. The nonempty S1/S3 placement plot has a readable zero-centered comparison and units. Corresponding SVGs parse and use consistent 576pt × 324pt dimensions; no date metadata is injected. The static HTML parser check found local navigation/source links and all table/image links with no external HTTP assets; hostile labels are escaped.

Representative generated artifacts from the final full-suite run are under `/private/var/folders/15/gp82yk6d61v0ldtfbgbcwlzw0000gp/T/pytest-of-katya/pytest-1107/`, including:

- `test_materialized_four_mode_in0/{short_study,acceptance,calibration,diagnostic_day}/report/`
- `test_s1_s3_scenario_distributi0/s1-s3-report/figures/placement-comparison.png`
- `test_render_produces_determini0/{one,two}/deployment-points.png`

Replay checks compare every report output byte and source hash across the paired output directories. Generated report artifacts remain in the temporary test directory and do not alter selected native input bytes.

## Evidence status

The full credential-free offline implementation gate passed. No native benchmark, provider operation, cloud acceptance, or thesis interpretation was attempted. F12 demonstrates deterministic local analysis of supplied evidence only; it does not establish F10 cloud readiness or approve paid execution. The coordinator should run convergence against the integrated branch before declaring the feature closed.
