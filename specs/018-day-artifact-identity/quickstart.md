# Validation guide: DAY artifact identity

These are implementation acceptance instructions, not passing test evidence. Planning writes only 018 artifacts. Baseline is `6971ba72cf5031ad50c4658606cc1d55c10242ac`; no cloud, credentials, traffic or instrumented-tool execution is authorized.

## Prerequisites and ownership gate

Coordinator reported verified 017 merge at `2cd36e9ea7b12681545e8c6680ccda42388ffded` during planning and released the sole shared-contract writer conditional on these planning gates passing. Before implementation, integrate latest main containing that merge into `codex/f01r-day-identity` and record the actual integrated base/head. Record the actual 017 merge SHA and integrated base before changing shared code. Do not consume unmerged 017 or change F08 files. The assigned worktree is `/private/tmp/cnb-day-identity`.

For every implementation command, retain explicit feature context and cwd:

```sh
test "$PWD" = /private/tmp/cnb-day-identity || exit 97
export GIT_BRANCH_NAME=codex/f01r-day-identity
export SPECIFY_FEATURE_DIRECTORY=specs/018-day-artifact-identity
export PYTHONPATH=src
export PYTHONDONTWRITEBYTECODE=1
CNB_PYTHON=/private/tmp/cnb-offline-venv/bin/python
test -x "$CNB_PYTHON"
git branch --show-current
git rev-parse HEAD
git diff --check
```

The runtime path is the existing offline environment, not an instruction to install or contact a package registry. If unavailable, report that gap or use a coordinator-identified equivalent with existing required dependencies; do not claim tests passed.

## Acceptance matrix

| Case | Required outcome | Test surface |
| --- | --- | --- |
| Non-default day unit | Both fixed DAY client files identify the actual parent; receiver files remain deployment scoped | contract |
| Several real produced chunks | Family templates become unique suffixed records with exact unit/path/parent; ValidatorInput accepts actual retrieval records unchanged | contract + integration |
| Canonical full boundary chain | ArtifactLayout/artifact_inputs → F03 files → closed RetrievalManifest → fake run_retrieval → ValidatorInput → bind_acquisition_sources → iter_source_windows | integration |
| Bad identity | Wrong suffix/path/unit/parent/producer/kind/deployment or duplicate concrete identity rejected at applicable boundary; no legacy fallback | contract + integration |
| Legacy inputs | Fixed client unit None and repeated base-family records with None/day-parent units remain readable against original and new declarations; no rewritten identity or acquisition proof | contract + integration |
| Missing/corrupt/truncated source | Hash/byte/command/build/session proof stays enforced; valid earlier chunks/closed intervals survive while invalid source/unknown tail stays unavailable | integration + existing replay regression |
| Other modes | Acceptance, short-study and calibration retain existing declaration/proof behavior and native paths | existing four-mode regressions |
| Schemas | Deterministic canonical schema check succeeds; no invented schema diff or claim of cross-record schema enforcement | export/check + contract suite |

The pipeline must use the existing actual F03 fake process/clock boundary and materialized pinned acquisition grammar. Supply declared probe_chunk units from actual executed command identities and producer parent notes; do not infer acquisition boundaries from filenames. Native B output and listener action/build evidence use the existing materialized fixture and closure/retrieval sequence. Source IDs/units in the modern pipeline are not patched after retrieval. Separate legacy bundle-read fixtures recreate documented historical serialized records over materialized producer bytes; they are not rerun through the modern RetrievalManifest, which rejects their legacy inventory IDs. Unrelated unavailable metadata stays honest; the fixture proves identity and supported intervals, not complete data validity or a genuine 86400-second receiver observation from an abbreviated native fixture.

## Commands after implementation

Run from this assigned worktree with the context above:

```sh
"$CNB_PYTHON" -m pytest -q -p no:cacheprovider tests/contract/test_day_artifact_identity.py tests/contract/test_acquisition_contract.py tests/contract/test_v2_handoffs.py tests/unit/test_retrieval.py tests/integration/test_acquisition_producer.py
"$CNB_PYTHON" -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts --check
"$CNB_PYTHON" -m pytest -q -p no:cacheprovider tests/contract/test_schema_integrity.py
"$CNB_PYTHON" -m pytest -q -p no:cacheprovider
git diff --check
```

If model metadata changes generated schemas, run the existing exporter once without `--check`, review and retain only affected canonical files, then repeat schema check and affected tests. Do not hand-edit generated schema or add a new schema root. A runtime-only change may correctly leave all schemas unchanged. New failing tests must demonstrate the actual identity mismatch before implementation rather than an import error.

## Evidence and handoff

| Stage | State at planning handoff |
| --- | --- |
| Specify/clarify/plan/tasks/analyze | Completed by artifact review; see stage results reported with planning commit |
| 017 merge / writer release | Coordinator reported verified `2cd36e9ea7b12681545e8c6680ccda42388ffded`; release conditional on completed planning gates |
| Latest-main integration | Not run by planner; required in this worktree before implementation |
| Application code/schema changes | Not made |
| New focused tests / regression suite / schema check | Not run during planning |
| Real native-tool/cloud acceptance | Not run; outside this feature |

Implementer records exact head/base, commands/exit results, focused test count, schema diff/no-diff and remaining limitations here when executing the tasks. Coordinator dispatches existing convergence after code/checks; a passing identity fixture is not F08 or F10 completion. No shared runtime state or PR is owned by this planner.
