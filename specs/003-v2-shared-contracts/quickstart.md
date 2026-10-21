# F01R offline validation and handoff

Run from `/private/tmp/cnb-f01r`; use explicit PYTHONPATH because the shared editable environment
points to the coordinator checkout. Required environment is already provisioned for this run:
`/private/tmp/cnb-offline-venv/bin/python` with declared dependencies/test extras. Tooling/schema
commands marked planned below become executable only when their tasks are implemented.

## Planning-stage evidence

Implementation base: `e7fc15ff1a7d6a4aa43dfebe5209592689a87fa7` (merged R00).
The following unchanged-baseline command was run during planning:

```bash
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q
```

Exit **1**: **203 passed, 3 failed in 3.27s**, log
`/private/tmp/cnb-f01r-planning-baseline.log`. Known failures:

- `tests/contract/test_config_traceability.py::test_every_executable_config_has_a_concrete_thesis_anchor`:
  old test assumes `### EXP-001` headings; current experiment index uses the scenario/config table.
- `tests/contract/test_deployment_contract.py::test_deployment_output_links_connection_roles_and_canonical_path`:
  old scenario-required assertion conflicts with optional scenario field. V2 requires it.
- `tests/contract/test_remote_access_contract.py::test_deployment_handoff_requires_scenario_metadata`:
  malformed `run_id="r"` fails before intended scenario check. Use valid provider-suffixed identities
  and test the newly required scenario boundary; preserve remote handoff rejection coverage.

Do not xfail/delete these checks or treat baseline count as V2 acceptance. F01R migrates the
expectations/fixtures and must finish without unexplained failures. No cloud or benchmark test ran.

## Implementer acceptance commands

After task completion, run the full suite (same exact command above): exit **0** required. Tests
must cover all FRs and the config/native/schema/dry-run matrix, including actual temporary files,
real local Git repositories, unknown/missing values, interruption and no external side effects.
The external-I/O test denies cloud/SSH/SCP/benchmark processes; optional local Git provenance is
injected or exercised separately in local repositories, never allowed through the cloud boundary.

These existing CLI routes must work with migrated configs, without launching anything:

```bash
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark validate --config configs/tests/exp-903-three-provider-smoke.yaml --format json
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark dry-run --config configs/experiments/exp-001-multi-provider.yaml --format json
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark dry-run --config configs/experiments/exp-001-calibration.yaml --format json
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark dry-run --config configs/experiments/exp-001-diagnostic-day.yaml --format human
```

Expected: version 2; acceptance ten-second phases/N=4; research 60-second units and pending-N
blocker; calibration 12 units in specified order; DAY one continuous unit, 300-second probe template
and no idle/single flow. Every preview lists admission pending; no path or cloud effect occurs.
Unit/integration tests enumerate all ten configs and every provider/scenario rather than repeating
manual CLI calls. Tests exercise `init` only in temporary roots; no committed result bundles.

Planned schema export/check route (task must implement it before documenting it as available):

```bash
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts --check
```

Export creates only reviewed schema files; check returns exit 0 without mutation when current,
nonzero on drift. Runtime round-trips and structural/conditional schema fixtures must agree.
Finish with `git diff --check` and `git status --short`; record exact tested head in
`docs/f01r-offline-validation.md` and report whether required CI exists separately from local tests.

## Acceptance scope

US1: valid/invalid four-mode S1–S4 matrix; all-cloud requirement; no inferred calibration or paid
admission; legacy/secret/unknown-field rejection; no config side effects.

US2: unique attempt/deployment identity, retained-unit linkage, original/resolved/schedule snapshots,
source/dirty Git evidence and collision/partial failure byte preservation.

US3: exported contracts and deterministic schemas, separate validation statuses, timing and native
artifact scope/producer/closed/integrity declarations, safety DTOs, strict provider connection
identity, shell-free redaction, materialized fakes and matching human/machine previews.

Terraform is not changed by this feature; Terraform static checks are **not applicable/not run**
for F01R. `/private/tmp/cnb-tools/terraform` is available for later provider features. F01R passing
would not prove native tool builds, measurement coverage, real expiry, cloud readiness or F10.
Next after implementation: Spec Kit convergence, repair findings and repeat relevant checks;
coordinator merges validated F01R before dependent features consume its public contracts.
