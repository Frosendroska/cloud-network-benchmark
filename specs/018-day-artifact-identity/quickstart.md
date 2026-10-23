# Validation guide: DAY artifact identity

These are implementation acceptance instructions, not passing test evidence. Planning base is `6971ba72cf5031ad50c4658606cc1d55c10242ac`; no cloud, credentials, traffic or instrumented-tool execution is authorized.

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

## Implementation reconciliation

Coordinator confirmed the verified 017 merge `2cd36e9ea7b12681545e8c6680ccda42388ffded`,
integrated latest `origin/main` and released the serialized shared-contract writer for this
implementation stage. This feature branch merged that main commit normally: integrated main base
`2cd36e9ea7b12681545e8c6680ccda42388ffded`, merge head `79dc82d930d7c4aa23650aaa88612e6e55c78912`.
Branch/worktree: `codex/f01r-day-identity` at `/private/tmp/cnb-day-identity`. The checked-out
constitution is `HEAD:.specify/memory/constitution.md` blob
`ba72e63aa5b58260ee5fe57f1b02f8c6b1c0a07a`. Both feature checklists passed (requirements 16/16,
contracts 12/12); there are no extension hooks. Existing `.gitignore` and `.terraformignore`
cover Python/test outputs, Terraform state/vars/plans, and local secrets; no ignore edits were
needed.

T002 reconciliation confirmed the assigned production API mismatch: `ArtifactLayout` emits the
fixed DAY client declarations with `unit_id=None` (`contracts/artifacts.py:226-232`), while
`measurement.py` runs the client as `iperf3-{day_unit_id}` and acquisition binding requires that
exact unit. `ValidatorInput` currently admits dynamic DAY template paths by pattern but requires
the base family ID and day-parent unit (`contracts/validation.py:127-154`), while
`RetrievalManifest` emits `<family-id>-<chunk-unit>` with the chunk unit (`retrieval.py:75-140`).
The existing multi-mode producer fixture manually constructs records around that boundary
(`tests/integration/test_acquisition_producer.py:874-956`), so it does not catch retrieval-to-DTO
identity drift. Retrieval, acquisition and F03 remain read-only owners/boundaries in this change.

Baseline command, before shared code/test/schema edits:

```text
test "$PWD" = /private/tmp/cnb-day-identity || exit 97
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis \
  /private/tmp/cnb-offline-venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/contract/test_v2_handoffs.py tests/integration/test_acquisition_producer.py
exit 0: 86 passed in 47.79s
```

Writable implementation scope remains `contracts/artifacts.py`, `contracts/validation.py`,
`tests/contract/test_day_artifact_identity.py`, the assigned acquisition producer integration
fixture, affected generated 003 schemas only, and 018 feature documentation.

## Evidence and handoff

| Stage | State at planning handoff |
| --- | --- |
| Specify/clarify/plan/tasks/analyze | Completed by artifact review; see stage results reported with planning commit |
| 017 merge / writer release | Verified `2cd36e9ea7b12681545e8c6680ccda42388ffded`; implementation release received after planning gates |
| Latest-main integration | Merged normally; main base `2cd36e9ea7b12681545e8c6680ccda42388ffded`, integrated head `79dc82d930d7c4aa23650aaa88612e6e55c78912` |
| Application code/schema changes | Pending implementation |
| New focused tests / regression suite / schema check | Not run during planning |
| Real native-tool/cloud acceptance | Not run; outside this feature |

## Implementation checkpoint

The first focused contract checkpoint adds `tests/contract/test_day_artifact_identity.py` and
changes only the assigned artifact declaration/validation models. Fixed DAY client records now
associate with the single declared day unit; dynamic chunk IDs resolve to exactly one family and
must match the concrete suffix, expanded path, declared chunk unit and day parent. The validator
also admits only the two specified historical forms without rewriting their IDs, paths or units.

Before the model changes, the new contract test command failed on the fixed DAY declaration being
unassigned and on a correctly suffixed retrieval record being unrecognized. After the changes:

```text
test "$PWD" = /private/tmp/cnb-day-identity || exit 97
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis \
  /private/tmp/cnb-offline-venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/contract/test_day_artifact_identity.py
exit 0: 13 passed in 0.24s
git diff --check
exit 0
```

This is a focused contract checkpoint only. The canonical F03-to-retrieval-to-validator-to-helper
integration proof, full regressions, schema check and complete offline suite are still pending;
no real native-tool or cloud acceptance is claimed.

## Integration and schema checkpoint

Added the end-to-end DAY boundary fixture. It executes F03 with the existing fake process/clock,
uses actual emitted command IDs and producer timing-note parents to define two chunk units, builds
the real layout declarations, closes a fake retrieval inventory, and passes `run_retrieval` output
unchanged into `ValidatorInput`. Those same retrieved records plus the existing synthetic build
fixture materialization feed unchanged `bind_acquisition_sources` and `iter_source_windows`.
The fixture confirms two independently supported Sockperf windows separated by a gap and one
abbreviated receiver prefix; it does not establish full-day data completeness. It also reconstructs
one historical base-family record at the read boundary and verifies the helper cannot promote it,
while the other modern chunk and receiver remain usable. A corrupt candidate similarly does not
erase the independent chunk.

Focused regression command:

```text
test "$PWD" = /private/tmp/cnb-day-identity || exit 97
PATH=/private/tmp/cnb-tools/powershell-7.5.11:$PATH PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 \
  THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis \
  /private/tmp/cnb-offline-venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/contract/test_day_artifact_identity.py tests/contract/test_acquisition_contract.py \
  tests/contract/test_v2_handoffs.py tests/unit/test_retrieval.py \
  tests/integration/test_acquisition_producer.py
exit 0: 140 passed in 48.29s
```

Schema exporter/checker completed with exit 0 and no schema diff. Schema integrity completed with
exit 0: 2 passed in 1.50s. Full offline suite remains pending. All acquisition/build evidence in
these fixtures is synthetic; cloud access, real native execution and traffic remain unrun.

Implementer records exact head/base, commands/exit results, focused test count, schema diff/no-diff and remaining limitations here when executing the tasks. Coordinator dispatches existing convergence after code/checks; a passing identity fixture is not F08 or F10 completion. No shared runtime state or PR is owned by this planner.
