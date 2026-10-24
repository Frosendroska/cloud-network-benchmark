# Implementation Plan: deterministic native analysis and visual reports

**Branch**: `codex/f12-v2-analysis` | **Date**: 2026-10-23 | **Spec**: [spec.md](spec.md)

**Input**: specs/012-v2-native-analysis/spec.md; merged F08 base `4a80f4af86feddc86e4ab930d254a19e29b35591`.

## Summary

Build a local read-only analysis function over selected existing ValidatorInput/ValidationResult files and manifests. Revalidate via merged F08, recompute bins from native evidence, reduce independent deployment cohorts and emit deterministic CSV/JSON/PNG/SVG/HTML in a new output directory. Reuse current proof/statistics/eligibility; add no shared contract, service or data-conversion layer.

## Technical Context

**Language/Version**: Existing Python >=3.9; implementation runtime currently Python3.11.13.
**Primary Dependencies**: Existing Pydantic/PyYAML; optional `analysis` extra `matplotlib>=3.7,<4`, lazy Agg renderer. No direct NumPy dependency.
**Storage**: Existing immutable local bundles, small local selection JSON and optional explicitly selected F11 cost snapshot; derived report directory only.
**Testing**: Existing pytest/jsonschema environment; known arithmetic fixtures, materialized F08 bundles, byte replay and image/HTML inspection.
**Target Platform**: Local controller/desktop; Agg rendering headless/offline once dependencies are installed.
**Project Type**: Existing CLI/library, one new analysis command.
**Performance Goals**: Process DAY RTT one <=300-second chunk at a time; retain active one-minute samples/histogram accumulators instead of flattening all daily RTT. Native receiver parsing retains current F08 behavior; no speculative new stream parser.
**Constraints**: Preserve F08 data/comparison status, native integrity and provenance; no silent sample/window imputation or raw writes; output-directory-independent deterministic bytes within recorded environment.
**Scale/Scope**: One run or explicitly selected studies including 28+28 primary experiments; four modes; new analysis/report/plot functions and focused tests only.

## Constitution Check

*Before research and after design: PASS.*

| Principle | Check |
| --- | --- |
| I Traceability | IDs, checksums, schedule/config, raw selection and analysis Git/environment accompany every row/figure. |
| II Small architecture | Ordinary local functions and one optional plotting dependency; no service, database, agents or mandatory intermediate format. |
| III Immutable honest evidence | Revalidate native bytes; exact support, unavailable values and separate statuses; raw sample quantiles, receiver bytes/time and legacy unknowns preserved. |
| IV Provider explicit | Provider and scenario metadata remain explicit; no provider execution or reinterpretation. |
| V Offline gate | Fakes/local files only; tests do not contact clouds or create traffic; no paid acceptance claim. |
| Ownership/workflow | Planning artifacts only now; implementation owns listed files, leaves F07/shared DTOs/F08/Thesis/state untouched; coordinator handles integration/merges. |

No exception, constitution amendment or source conflict is required. Missing plotting dependencies are an implementation prerequisite, not an unresolved research choice.

## Project Structure

### Documentation (this feature)

```text
specs/012-v2-native-analysis/
  spec.md; plan.md; research.md; data-model.md; quickstart.md; tasks.md
  contracts/native-analysis.md
  checklists/{requirements,analysis-quality}.md
```

### Source Code (repository root)

```text
src/cloud_network_benchmark/analysis.py          # selection, F08 adapter, bins/cohorts/context
src/cloud_network_benchmark/analysis_plots.py    # Agg PNG/SVG from report data
src/cloud_network_benchmark/analysis_report.py   # sorted CSV/JSON, escaped HTML, publication
src/cloud_network_benchmark/cli.py               # narrow analyze dispatch only
pyproject.toml                                 # optional analysis extra only
README.md                                     # concise user command only
docs/native-analysis.md                        # inputs, units, outputs, replay limits
tests/f12_support.py
tests/fixtures/f12/{README.md,expected.json}    # tiny known expected arithmetic, no large raw day
tests/unit/test_analysis.py
tests/unit/test_analysis_plots.py
tests/contract/test_analysis_interface.py
tests/integration/test_analysis_replay.py
```

**Structure Decision**: Three ordinary modules separate calculation from scientific rendering and file publication. Reuse existing evidence models and native/F08 functions. No registry, new package hierarchy or shared schema root. New tests may read/reuse F08's materialized bundle helper; do not edit F08 tests or duplicate its full fixture platform.

## Implementation reconciliation

Implementation stayed within the declared F12 modules, CLI, optional plotting extra, tests, and documentation. Provider matching delegates eligibility to F08; the S1/S3 descriptive view groups independent scenario distributions without inventing pairs. Fixture support extends the real materialized F08 evidence with exact supplemental declarations and matching child-manifest rows for its already-produced observation/build files. The S1/S3 integration test adapts only its disposable copy of the F08 synthetic schedule row and summary label. These are fixture compatibility details, not production validation relaxations. Final convergence repairs reject conflicting deployment attempts, verify cost history against event payloads and source bytes, preserve metric units and acquisition gaps in figures, map whole receiver intervals only with supported clocks/windows, and anchor DAY chunks on the continuous-day origin. T034–T037 additionally align receiver reset semantics with F08 by mode, mark boundary-straddling receiver bins unavailable while preserving whole-interval evidence, clip DAY minutes to the selected UTC window and combine cross-chunk raw samples, publish reports with missing native sources, and reduce shared reservation costs deterministically with contradictory evidence unknown. T038 orders DAY units chronologically, finalizes exact RTT statistics once the next chunk's measured-start bound closes a minute, and releases that minute's raw samples while retaining unfinished buckets. Saved result identities are checked against paired inputs and still revalidated by F08; unsupported diagnostic alignment remains explicit. No shared contract, F08 policy, or research decision changed. See actual commands, evidence boundaries, and visual review in [quickstart.md](quickstart.md).

## Interfaces, ownership and dependencies

`analyze_selection(selection_path: Path, *, analysis_git: GitSnapshot) -> AnalysisReport` is a read-only F12 entry. `AnalysisReport` is one local frozen record containing serializable payload and protected source paths; it is not a shared DTO. `write_analysis(report, output_root: Path) -> tuple[Path,...]` publishes derived files, calling plot rendering. CLI `analyze --selection PATH --output NEW_DIRECTORY` supplies observed RepositoryGitProvenance snapshot and reports publication outcome separately from dataset validity.

Selection pairs saved existing input/result models, allowing F12 fixture replay without unmerged F09 persistence. Saved result evaluated_at supplies deterministic F08 revalidation time; current F08 results determine trusted metrics. Private F08 helper reuse stays in one adapter and is checked for parity, not promoted to a new public API. Matched peer groups call evaluate_comparison; per-provider S1/S3 cohort compatibility is specified separately so intentional placement/config differences do not suppress the requested effect analysis.

Own only the source/test/docs paths above and this feature directory. F08 native/validation/calibration/summaries, acquisition, shared contracts/canonical schemas, providers/Terraform/F07, admission runtime, config research decisions, Thesis and coordinator state are read-only. If a genuine missing shared interface cannot be adapted safely, return exact evidence to coordinator for a separate amendment rather than editing it.

## Phase 0 — Research

Completed [research.md](research.md): native support reuse, deterministic selection/time identity, valid cohort units, recorded cost scope, plotting dependency and output safety. No unresolved technical or research decision remains. Coordinator can prepare the authorized plotting extra; record actual versions before renderer tests.

## Phase 1 — Design

[Data model](data-model.md) defines F12-local records/rows; [contract](contracts/native-analysis.md) defines invocation, numeric rules, gates and files. [Quickstart](quickstart.md) lists exact acceptance commands and visual inspection. Recheck constitution after design: PASS.

## Delivery and acceptance

1. Confirm assigned merged base/latest-main integration through coordinator and record actual head; do not consume unfinished F07. Add failing selection/provenance and calculation tests first.
2. Implement read-only F08 adapter and native bins, followed by deployment/provider/scenario summaries. Preserve partials/unknowns and keep failed sources out of trusted aggregates.
3. Add diagnostic/reliability/cost context, then deterministic publication/plots/HTML and narrow CLI dispatch before existing config-specific args are accessed.
4. Prove exact fixture values, all four modes, source mutation rejection, eligibility and duplicate handling, one-minute DAY gaps, missing clocks/costs and readonly hash preservation.
5. Run twice into new directories with input permutation; compare every produced byte and source hash under recorded analysis/environment. Inspect PNG/SVG and HTML labels/axes/links/gaps/statuses; no golden-image tautologies alone.
6. Run focused checks, existing CLI/F08 regressions, canonical schema no-drift and full credential-free Python suite. Record actual head/commands/exit results, unavailable checks and visual evidence. Coordinator dispatches existing convergence; no F10 or real-tool/cloud readiness follows from this feature alone.

US1 is the calculation MVP. The feature requires all three stories and complete report/replay acceptance before merge; this planner does not implement application code.
