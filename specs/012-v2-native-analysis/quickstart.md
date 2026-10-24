# F12 validation and user handoff

F12 implementation and the appended T026–T038 repairs are on the assigned branch `codex/f12-v2-analysis` in `/private/tmp/cnb-f12`. The T034–T037 checkpoint is `eec2ea8ab3bdb3bb11a1ec940accfef3a81f4126`; the T038 streaming checkpoint is `65dba1f3e8e887600792f382d381c58de42b17b8`. Final task/evidence bookkeeping is committed separately. The approved main base was integrated before these repairs. Shared DTO/schema, F07/F08 production code, Thesis, and coordinator state were not changed. The coordinator owns final convergence.

## Runtime and boundaries

Validation used Python 3.11.13 on macOS 15.2 x86_64, Matplotlib 3.11.2, NumPy 2.4.6, and Agg with `MPLCONFIGDIR=/private/tmp/cnb-matplotlib-config`. PowerShell 7.5.11 ran from `/private/tmp/cnb-tools/powershell-7.5.11/pwsh` (arm64 binary under the available host runtime). No clouds, benchmark traffic, credentials, or paid acceptance were used. The full suite's temporary `configs/tests/f08-*-aws.yaml` fixtures were cleaned up; final search found none.

Deployment summaries and S1/S3 comparisons count one independent deployment once. Identical attempts are coalesced; conflicting metric values for a deployment are excluded and reported. Cost aggregates are recomputed from the matching reservation/campaign/provider history and digest-verified billing event/source bytes; a mismatch is unavailable. Each shared reservation appears once in totals.

Receiver bins use native session timestamps and F02R clock uncertainty only when a complete interval lies inside a recorded iperf acquisition window and one output bin. Gaps are preserved, intervals are not prorated, and a UTC filter applies to both RTT and receiver intervals. Missing origin or clock evidence remains unavailable. DAY chunks share the parent continuous-day origin, with unsupported minutes and partial coverage kept visible. Diagnostic alignment is eligible only for the concrete overlapping measurement unit(s) with both VM mappings within 100 ms; DAY uses probe-chunk scopes, not the enclosing day declaration. DAY context is descriptive and does not imply an idle baseline, causal burst effect, or continuous every-send proof.

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
| Focused F12: `PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis PATH=/private/tmp/cnb-tools/powershell-7.5.11:$PATH MPLBACKEND=Agg MPLCONFIGDIR=/private/tmp/cnb-matplotlib-config /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/unit/test_analysis.py tests/unit/test_analysis_convergence.py tests/unit/test_analysis_plots.py tests/contract/test_analysis_interface.py tests/integration/test_analysis_replay.py` | Exit 0; 35 passed in 102.37s. |
| Full credential-free suite at `feacf19f987d5869e85615b0b6f5c639a2e59a2e`: `PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis PATH=/private/tmp/cnb-tools/powershell-7.5.11:$PATH MPLBACKEND=Agg MPLCONFIGDIR=/private/tmp/cnb-matplotlib-config /private/tmp/cnb-offline-venv/bin/python -m pytest -q` | Exit 0; 1289 passed in 281.55s; no skips reported. |
| Canonical contracts: `python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts --check` | Exit 0; no schema drift. |
| CLI: `python -m cloud_network_benchmark analyze --help` | Exit 0; exposes only `--selection` and `--output`. |
| `git diff --check` | Exit 0. |
| Renderer environment: `pwsh -v` with official path; Python version/import/backend probe | Exit 0; PowerShell 7.5.11, Python 3.11.13, Matplotlib 3.11.2, NumPy 2.4.6, Agg, x86_64. |

Focused and full integration replay exercise materialized F08 lifecycle evidence for short-study, acceptance, calibration, and diagnostic-day modes through the F12 CLI. Reversed descriptor order and different output names produce byte-identical report files; checks cover source hashes, status recomputation, independent expected arithmetic, missing/partial evidence, full provider cohort eligibility, S1/S3 distribution comparison, and failure cases. New regressions cover deployment deduplication/conflicts and ordering, verified billing aggregates/campaign identity, acquisition gaps, saved-result identity, date grouping, plot artifact requirements, and concrete diagnostic alignment scope. Integration support adds exact supplemental artifact declarations and matching child-manifest rows for observation/build files already produced by the F08 fixture; it does not synthesize success or relax production validation. The S1/S3 fixture uses a disposable real config copy and adapts only the synthetic schedule row/summary label.

## Visual and replay review

The current suite's short-study and DAY time-series and deployment PNGs, empty DAY receiver panel, and nonempty S1/S3 RTT and relative-throughput placement panels were opened at full size. The DAY receiver panel explicitly reports that this fixture has no receiver intervals with sufficient timestamp/window support; it does not invent a series. Deployment labels remain readable and multiline; both placement views show units and direction. Corresponding SVGs parse and use consistent 576pt × 324pt dimensions; no date metadata is injected. The static HTML parser check found local navigation/source links and all table/image links with no external HTTP assets; hostile labels are escaped.

Representative generated artifacts from the final full-suite run are under `/private/var/folders/15/gp82yk6d61v0ldtfbgbcwlzw0000gp/T/pytest-of-katya/pytest-1162/`, including:

- `test_materialized_four_mode_in0/{short_study,acceptance,calibration,diagnostic_day}/report/`
- `test_s1_s3_scenario_distributi0/s1-s3-report/figures/placement-comparison.png`
- `test_render_produces_determini0/{one,two}/deployment-points.png`

Replay checks compare every report output byte and source hash across the paired output directories. Generated report artifacts remain in the temporary test directory and do not alter selected native input bytes.

## Evidence status

The full credential-free offline implementation gate passed. No native benchmark, provider operation, cloud acceptance, or thesis interpretation was attempted. F12 demonstrates deterministic local analysis of supplied evidence only; it does not establish F10 cloud readiness or approve paid execution. The coordinator should run convergence against the integrated branch before declaring the feature closed.

## T034–T037 final convergence evidence

The current code checkpoint `eec2ea8ab3bdb3bb11a1ec940accfef3a81f4126` incorporates four final repairs. Receiver UTC origins now follow F08's mode-specific iperf reset behavior; receiver intervals that cross a bin boundary leave primary bin throughput unavailable while preserving whole-interval evidence and per-source exclusion reasons. DAY gap rows are clipped to the selected UTC interval, and canonical minute rows combine underlying samples and union supported windows across chunks without averaging chunk quantiles. Missing artifact paths remain explicit unavailable sources and publishable HTML text without broken links. Repeated shared-reservation/provider rows use a stable representative and become unknown when selected campaign or billing evidence conflicts.

| Check on `eec2ea8ab3bdb3bb11a1ec940accfef3a81f4126` | Result |
| --- | --- |
| Focused F12: `PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis PATH=/private/tmp/cnb-tools/powershell-7.5.11:$PATH MPLBACKEND=Agg MPLCONFIGDIR=/private/tmp/cnb-matplotlib-config /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/unit/test_analysis.py tests/unit/test_analysis_convergence.py tests/unit/test_analysis_plots.py tests/contract/test_analysis_interface.py tests/integration/test_analysis_replay.py` | Exit 0; 42 passed in 213.25s; no skips. |
| Full credential-free suite: `PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis PATH=/private/tmp/cnb-tools/powershell-7.5.11:$PATH MPLBACKEND=Agg MPLCONFIGDIR=/private/tmp/cnb-matplotlib-config /private/tmp/cnb-offline-venv/bin/python -m pytest -q` | Exit 0; 1296 passed in 390.98s; no skips. |
| Canonical contract schema: `PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts --check` | Exit 0; no schema drift. |
| Analyze CLI help and `git diff --check` | Exit 0; only `--selection`/`--output` are exposed; no whitespace errors. |
| Visual inspection of the DAY `receiver-time-series.png`; XML parse of all SVGs in its report; local-link check for its HTML | Exit 0; receiver panel truthfully states no source-supported intervals; 7 SVGs parsed and no unresolved local links. |

The final full-suite artifacts are under `/private/var/folders/15/gp82yk6d61v0ldtfbgbcwlzw0000gp/T/pytest-of-katya/pytest-1170/`. The materialized missing-native-file report and reordered same-provider shared-reservation replay were generated by the final suite. Temporary `configs/tests/f08-*-aws.yaml` fixtures were absent after cleanup. The full suite log was captured in the terminal session (not a separate file).

## T038 streaming reduction

DAY units are ordered by recorded acquisition start. After each chunk, F12 reduces each minute whose end is no later than the next chunk's measured-start bound to exact RTT statistics, then drops the raw sample references. It keeps raw samples for minutes a later chunk can still reach, so non-minute-aligned rotations still combine their underlying samples rather than averaging chunk quantiles. The materialized DAY regression checks weak references before each subsequent chunk parser call; all prior chunk samples were released. A deterministic 90s/150s boundary fixture verifies an unfinished minute stays live, cross-chunk median/support/source identity stays exact, and input unit order does not affect chronological processing.

| T038 check on `65dba1f3e8e887600792f382d381c58de42b17b8` | Result |
| --- | --- |
| Focused F12 command from the prior table, including the new lifetime and streaming regression | Exit 0; 43 passed in 212.39s; no skips. |
| Full credential-free suite command from the prior table | Exit 0; 1297 passed in 391.22s; no skips. |
| Canonical schema check, analyze help, `git diff --check`, final DAY PNG/SVG/HTML review | Exit 0; schema unchanged, 7 SVGs parsed, no unresolved local links. |

The T038 full-suite artifacts are under `/private/var/folders/15/gp82yk6d61v0ldtfbgbcwlzw0000gp/T/pytest-of-katya/pytest-1175/`; no `configs/tests/f08-*-aws.yaml` files remained after the run.
