# F12 validation and user handoff

Planning only. Assigned worktree `/private/tmp/cnb-f12`, branch `codex/f12-v2-analysis`, verified F08 merge base `4a80f4af86feddc86e4ab930d254a19e29b35591`. Shared DTOs/F08/F07/Thesis/state are outside ownership. See [contract](contracts/native-analysis.md) and [data model](data-model.md).

## Runtime and context

Use every command from this assigned worktree with explicit context:

```sh
test "$PWD" = /private/tmp/cnb-f12 || exit 97
export GIT_BRANCH_NAME=codex/f12-v2-analysis
export SPECIFY_FEATURE_DIRECTORY=specs/012-v2-native-analysis
export THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis
export PYTHONPATH=src
export PYTHONDONTWRITEBYTECODE=1
export PATH=/private/tmp/cnb-tools/powershell-7.5.11:$PATH
export MPLBACKEND=Agg
export MPLCONFIGDIR=/private/tmp/cnb-matplotlib-config
CNB_PYTHON=/private/tmp/cnb-offline-venv/bin/python
test -x "$CNB_PYTHON"
```

Optional dependency declaration is `analysis = ["matplotlib>=3.7,<4"]`. Coordinator reported installation/smoke success with Python3.11.13 x86_64 process (Darwin arm64 host), Matplotlib3.11.2 and NumPy2.4.6. Planner independently verified installed package metadata and Python process architecture; Agg smoke remains coordinator-reported. Do not confuse host/process architecture. Exact F12 renderer acceptance is not yet run. Verify actual runtime metadata before implementation checks; use existing official PowerShell PATH if the full suite needs it, without changing provider tests.

## Planned user command and inputs

After implementation, a selection file pairs saved existing DTO files:

```json
{
  "format_version": 1,
  "runs": [
    {"validator_input": "run-a/validator-input.json", "validation_result": "run-a/validation-result.json"}
  ]
}
```

These are user-selected existing files, not a new YAML experiment plan. Paths resolve relative to the selector; optional UTC/cost selection fields are defined in the contract. No F09 dependency or actual paid bundle is required for fixture replay. The planned command is:

```sh
"$CNB_PYTHON" -m cloud_network_benchmark analyze --selection selection.json --output results/processed/report-new
```

Use the existing ignored results/processed area or a source-disjoint directory outside the implementation checkout so generated outputs do not alter observed analysis Git dirty identity on the next replay. This uses existing ignore rules; no ignore or provenance implementation change is required. It writes the documented normalized tables, PNG/SVG figures and index.html in a fresh directory. Its exit0 means report generation succeeded, not that every dataset is valid/eligible. Existing or source-overlapping output is an error; current validation and unavailable reasons remain visible. README user guidance stays concise; detailed calculation/replay limits belong in docs/native-analysis.md.

## Exact checks after implementation

```sh
"$CNB_PYTHON" -m pytest -q -p no:cacheprovider tests/unit/test_analysis.py tests/unit/test_analysis_plots.py tests/contract/test_analysis_interface.py tests/integration/test_analysis_replay.py
"$CNB_PYTHON" -m pytest -q -p no:cacheprovider tests/unit/test_native.py tests/unit/test_validation.py tests/integration/test_native_validation_replay.py tests/integration/test_validate_resolve_cli.py tests/integration/test_dry_run_cli.py tests/integration/test_initialize_cli.py
"$CNB_PYTHON" -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts --check
"$CNB_PYTHON" -m pytest -q -p no:cacheprovider
git diff --check
```

The new tests are planned files, not runnable planning evidence. Record actual command/exit/count/head after implementation. No Terraform/provider changes require new paid checks; the full Python suite uses existing fakes/static fixtures. If a dependency or tool is unavailable, report it as not run rather than skip it into a passing claim.

## Acceptance matrix and visual review

| Case | Required proof |
| --- | --- |
| Numeric oracle | Unequal interval rates use total receiver bytes/elapsed; parallel bytes have one elapsed denominator; exact raw-bin RTT quantiles, SD/count thresholds and zero baseline treatment match independent expected values. |
| Mode/support | Actual F08 materialized four-mode inputs revalidate; omit/warm-up/gaps/straddling intervals and unknown clocks do not become fabricated bins; partial DAY has one-minute rows without idle/inflation or fake completeness. |
| Cohorts | Correct deployment counts/median/IQR/points; duplicate/replacement/retained units stay separate; provider matching preserves full expected cohort, frozen N/skew gates; S1/S3 within provider/window records actual dates and same-week deviation. |
| Context | Both-role reset/missing diagnostics survive; selected-attempt reliability denominator and sourced cost distinctions/dedup are explicit; missing charge/runtime/transfer is unavailable. |
| Trust/output | Changed source or stale saved result cannot restore validity; unsafe paths/existing reports refused; raw hashes and record identities unchanged; failed rendering does not publish a complete report. |
| Replay | Actual CLI generates all required formats twice in new directories, with reversed descriptor order/name, same source/revision/environment; every produced file byte and source hash matches. |
| Visual | Open representative PNGs for each distinct plot class at full size; inspect matching SVG labels/dimensions and HTML in a local browser. Axes/units/counts, spikes/gaps, status/source captions and source/table/image links must be readable, unclipped and offline. |

Visual checks include nonempty short-study and partial DAY, deployment/provider/placement distributions and an empty/ineligible selection. Avoid golden-image-only tests: numerical plot data and eligibility need independent oracles. Use actual generated report paths printed by integration tests; record those paths and inspection findings. Native metadata/software warnings are not performance warnings and do not authorize clipping slow results.

## Evidence at planning handoff

| Item | Status |
| --- | --- |
| F08 dependency | Merged at the assigned base; branch verified locally |
| Spec Kit planning/artifact review | Completed stages reported with planning commit |
| Plot dependency tooling | Coordinator-reported installation/Agg smoke exit0; F12 application acceptance unrun |
| Application code / shared schema edits | Not made by planner |
| New/focused/full application tests and visual report review | Not run during planning |
| Clouds / native benchmark execution | Not run; outside F12 |

Implementer fills exact tested head/base, versions, command results, figure paths and visual/deterministic evidence here; coordinator dispatches convergence. F12 completion does not establish F10 or paid acceptance.
