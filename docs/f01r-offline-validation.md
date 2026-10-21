# F01R offline validation

## Environment and baseline

- Worktree: `/private/tmp/cnb-f01r`
- Branch: `codex/f01r-v2-contracts`
- Reviewed implementation start: `17ae3714cfd043df62721a1761bb8d0547a58eb7`
- Merged dependency base: `e7fc15ff1a7d6a4aa43dfebe5209592689a87fa7`
- Python: `/private/tmp/cnb-offline-venv/bin/python`
- Host: Darwin arm64
- Test import path: `PYTHONPATH=src`; Thesis source root: `/Users/katya/Desktop/University/Masters/Thesis`
- Earlier source-code test commit: `7d3f6ca5a54d68cb465bd6579e5cd80b599bc917` (pre-convergence checkpoint). Final repair source-code test commit: `95c149d`; the convergence repair supersedes the intermediate result below.

Baseline command:

```bash
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q
```

Exit 1: **203 passed, 3 failed in 3.06s**. The failures matched the planned migration cases:

- `tests/contract/test_config_traceability.py::test_every_executable_config_has_a_concrete_thesis_anchor` assumed legacy `### EXP-001` headings rather than the current Thesis scenario/config table.
- `tests/contract/test_deployment_contract.py::test_deployment_output_links_connection_roles_and_canonical_path` expected legacy optional-scenario behavior; V2 requires scenario identity even for partial output.
- `tests/contract/test_remote_access_contract.py::test_deployment_handoff_requires_scenario_metadata` used malformed `run_id="r"`, which failed provider-suffix validation before reaching the intended scenario assertion.

No cloud, credential, Terraform, or benchmark operation was run. Historical `specs/001-core-offline-contracts/` and `specs/002-vm-access-bootstrap/` remain byte-identical to the merged dependency base.

## Final implementation validation

Pass 1 checkpoint `95c149d` passed 232 tests. Pass 2 adds T051–T057 and supersedes that result.
The final quickstart full-suite command passed at repair source commit
`c676b3587f134590ef554fa33ae7ff61061da336` (before this validation-record-only commit):

```bash
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q --tb=short
```

Exit 0: **247 passed in 9.86s**. This includes all original baseline migration cases, local Git provenance scenarios, V2 manifests and failure preservation, deployment/access handoffs, cross-record validation/artifact/timing constraints, schema/runtime consistency, pending/frozen acceptance stream resolution, malformed commit fallback, secret-bearing CSV header rejection, partial metadata, and retained day probe chunks.

Schema export and drift check both exited 0 at the final repair source head:

```bash
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts --check
```

All four quickstart CLI routes exited 0 without launching external actions at the final repair source head:

```bash
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark validate --config configs/tests/exp-903-three-provider-smoke.yaml --format json
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark dry-run --config configs/experiments/exp-001-multi-provider.yaml --format json
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark dry-run --config configs/experiments/exp-001-calibration.yaml --format json
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark dry-run --config configs/experiments/exp-001-diagnostic-day.yaml --format human
```

The acceptance preview preserves pending common-N state while resolving multi-flow to four streams; a focused test also verifies a frozen N is used after calibration. Research pending N remains null and blocking. The three-provider research preview remains pending admission/calibration. Calibration renders twelve ordered units and the day mode renders one continuous unit with its probe chunk template.

Convergence tasks T034–T057 are implemented and checked in `tasks.md`; reviewer-owned checklist markers remain unchanged. Final focused validation of validation, deployment, schema, admission, Git provenance and the new secret/Git initialization regressions passed (**66 passed**). The schema drift check was repeated after final source changes.

Final hygiene checks at the repair source head:

```bash
git diff --check
git diff e7fc15ff1a7d6a4aa43dfebe5209592689a87fa7 -- specs/001-core-offline-contracts specs/002-vm-access-bootstrap
```

Both exited 0; the historical-spec diff was empty. Local CI configuration was not run or inspected as a remote CI result. Terraform static checks are not applicable/not run because F01R does not change Terraform. No cloud, SSH/SCP, benchmark, F10, or real expiry/readiness operation was run; this work is credential-free contract and CLI validation only. The final validation-record commit is recorded separately from tested code head `c676b3587f134590ef554fa33ae7ff61061da336`.

## Convergence pass 3

The final assigned convergence tasks T058–T059 are implemented at source/test commit
`ea02fa993efa6f8bb66bd88e3be78b20876f6651`. T058 caps planned `probe_chunk` units at 300
seconds in runtime validation and the public schema. T059 validates protocol settings, derived
units, admission intent, schedule linkage, replacement context and blockers at both resolved DTO
deserialization boundaries; exported resolved campaign schemas enforce mode-specific durations,
units, stream state and admission values. Pending N remains pending, and acceptance still resolves
its aggregate measurement units to four streams.

Verification at that source commit:

```bash
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/contract/test_resolved_protocol_contract.py
```

Exit 0: **15 passed**.

```bash
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q
```

Exit 0: **262 passed in 10.95s**.

Schema export/drift check exited 0, and all four required CLI checks exited 0: acceptance
`validate --format json`, and short-study/calibration/day `dry-run` (JSON for the first two,
human-readable for day). The commands used were the same as listed in the preceding section. The
first integrated full-suite run had three failures: an existing replacement-boundary error message,
a scenario-tampering error message, and stale exported schemas. After preserving the two established
error paths and regenerating the schemas, the targeted cases and full suite passed as recorded.

`git diff --check` exited 0. Comparing historical specs 001 and 002 with dependency base
`e7fc15ff1a7d6a4aa43dfebe5209592689a87fa7` produced an empty diff. Reviewer-owned checklist files
remain unchanged; T058 and T059 are checked off in `tasks.md`. No cloud, credential, Terraform
apply, benchmark, F10, or remote action was run. The validation-record-only commit follows the
tested source commit above.

## Convergence pass 4

The final assigned convergence tasks T060–T061 are implemented at source/test commit
`a9541a0be04cafb668c1d28920fe1f99f5667633`. Shared source-intent validation now keeps
calibration and diagnostic-day campaigns in `same_zone`, constrains replacement mode and selected
provider consistently across source, campaign and observation DTOs, and exports the matching
context/scenario/provider rules. Common-N schema conditionals now treat omitted state/evidence as
pending defaults and only apply frozen-N stream constraints when the nested frozen record is
present. Pending research N and calibration/schedule blockers remain evidence-driven.

The focused convergence corpus covers all 4 modes × 4 scenarios × 3 contexts at the source DTO
boundary, both retained modes across all scenarios and failure/replacement contexts at resolved DTO
boundaries, each provider's replacement linkage, and omitted `common_n`, omitted state, explicit
pending/frozen values, malformed frozen evidence and stream mismatches through both resolved DTO
schemas and runtime validators.

```bash
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/contract/test_resolved_protocol_contract.py
```

Exit 0: **116 passed in 6.15s**.

```bash
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q --tb=short
```

Exit 0: **363 passed in 16.44s**. The first integrated run before schema regeneration had 362
passed and one expected schema-drift failure; after exporting the updated campaign, resolved
campaign and child manifest schemas, the repeated full run passed.

Schema export and drift check both exited 0. All four required CLI checks exited 0: acceptance
`validate --format json`, short-study and calibration `dry-run --format json`, and diagnostic-day
`dry-run --format human`.

`git diff --check` exited 0; the historical-spec comparison against
`e7fc15ff1a7d6a4aa43dfebe5209592689a87fa7` was empty. Reviewer checklist markers and historical
specs remain unchanged; T060 and T061 are checked in `tasks.md`. No cloud, credential, Terraform
apply, benchmark, F10 or remote action was run. The final validation-record commit is recorded
separately from the tested source commit above.

## Convergence pass 5

T062 is implemented at source/test/schema commit
`e3986eeb3bf43826c84ac81e6ab0f02b34d393a9`. In replacement contexts, the public schemas now
require a non-null object containing both `observation_id` and `replaces_run_id`, with the same
identifier and provider-suffix constraints enforced by runtime validators. Standard and
failure-check contexts continue to accept omitted or null replacement metadata.

The focused corpus exercises omitted, null, empty, incomplete and valid replacement metadata for
standard, failure-check and replacement contexts across AWS, Azure and GCP, against source config,
ResolvedCampaign and ResolvedObservation runtime and JSON Schema validation.

```bash
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/contract/test_resolved_protocol_contract.py
```

Exit 0: **161 passed in 11.15s**.

```bash
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q --tb=short
```

Exit 0: **408 passed in 21.43s**. Schema export and drift check both exited 0. Acceptance validate
JSON and short-study, calibration and day dry-runs exited 0. `git diff --check` exited 0; specs
001 and 002 remain byte-identical to dependency base `e7fc15ff1a7d6a4aa43dfebe5209592689a87fa7`.
T062 is checked off and reviewer-owned checklist markers remain unchanged. No cloud, credentials,
Terraform apply, benchmark or remote action was run. The final validation-record commit follows the
tested source commit above.

## Convergence pass 6

T063 is implemented at source/test/schema commit
`74d221a147bc2436a750d782274f1e06d685ffc6`. The standard provider-scope schema condition now
requires the `context` property before checking for subset contexts, so an omitted context follows
the same all-cloud rule as runtime's default `standard`. Explicit `failure_check` and valid
single-provider `replacement` scopes remain covered.

```bash
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/contract/test_resolved_protocol_contract.py
```

Exit 0: **175 passed in 11.39s**. This includes exp-900 with omitted and explicit standard context,
and the omitted/explicit context × one/two/three-provider source matrix.

```bash
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q --tb=short
```

Exit 0: **422 passed in 23.06s**. Schema export and drift check both exited 0. Acceptance validate
JSON and the short-study, calibration and day dry-runs exited 0. `git diff --check` exited 0; the
historical specs 001/002 comparison against dependency base
`e7fc15ff1a7d6a4aa43dfebe5209592689a87fa7` was empty. T063 is checked and reviewer-owned checklist
markers remain unchanged. No cloud, credentials, Terraform apply, benchmark or remote action was
run. The validation-record commit follows the tested source commit above.

## Delivery boundary

Schema V2, configuration/native intent, provenance snapshots, manifests, handoff DTOs, deterministic schema export, validation/timing/artifact contracts, and human/machine dry-run previews are implemented in the assigned F01R scope. Source prices/cost snapshot hash remain unavailable because no exact workbook snapshot was supplied; no value was inferred or fabricated. F01R completion does not establish cloud readiness or authorize paid work. Next stage is Spec Kit convergence against the delivered feature artifacts, then coordinator review/merge before dependent features consume these contracts.
