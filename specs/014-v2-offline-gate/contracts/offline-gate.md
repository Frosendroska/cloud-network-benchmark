# Offline gate command contract

## Planned interface

`python scripts/validate_offline.py --evidence-dir PATH --terraform PATH --pwsh PATH --provider-mirror PATH`

This command is a planned F10 deliverable, not implemented/executed during planning. Use the invoking Python for pytest/schema. Resolve tool paths explicitly, require a prepared local provider mirror and initialized roots with committed locks; never download or run init inside the gate. `--help` must work without those prerequisites and describe the boundary.

## Inputs and preflight

- Source root is the script's repository, not arbitrary current-directory discovery; require real Git HEAD/tree and clean tracked/unignored untracked status.
- Evidence directory is new, outside source paths or under ignored local evidence; refuse existing directory/collision/symlink locations that could overwrite sources. Create logs only after safe preflight; preserve failure details where possible.
- Require pytest/Pydantic/PyYAML/Matplotlib and all six initialized exact-locked provider roots. Missing tool/cache returns incomplete/nonzero with no installer fallback.
- Use allowlisted version/host/source metadata only. No environment dump, private keys, credentials, Terraform state/plan exports or raw research bundle commits.
- Generate/use a Terraform CLI config with only filesystem mirror installation, no direct source, checkpoints disabled. Export `TF_CLI_CONFIG_FILE`, `CHECKPOINT_DISABLE=1`, `TF_IN_AUTOMATION=1`; record config content/hash because it is non-secret. Preserve caller permissions and never bypass tool review.

## Fixed commands

1. Full `python -m pytest -q --junitxml=EVIDENCE/pytest.xml` with `PYTHONPATH=src`, canonical `THESIS_ROOT`, Agg backend, private Matplotlib config and supplied PowerShell on PATH. Clear inherited pytest selection options such as `PYTEST_ADDOPTS`; do not add selection/deselection/skip flags. Parse complete JUnit and required identities from [coverage.md](coverage.md).
2. `python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts --check`.
3. `git diff --check`.
4. For each `terraform/aws/{pair,safety}`, `terraform/azure/{pair,expiry}`, `terraform/gcp/{pair,protection}`: `terraform -chdir=ROOT fmt -check -recursive`, then `terraform -chdir=ROOT validate -no-color`.
5. `terraform -chdir=ROOT test -no-color` only for Azure pair/expiry and GCP pair/protection. Before dispatch, require their checked-in `.tftest.hcl` inputs to use `mock_provider` and `command = plan` for every run. Their provider declarations remain mocked; no real plan is allowed. AWS has no existing `.tftest.hcl` suite and is covered by schema/static and Python fake tests.
6. Recheck exact Git HEAD/tree and clean state. No gate command may run init, live plan, apply, destroy, cloud CLIs, SSH/SCP or native traffic tools.

Each subprocess has an explicit local deadline appropriate to the full suite/static checks; expiration is failed/incomplete evidence. Use argv arrays, no shell evaluation. Retain all completed outputs and mark omitted checks not run after a failure; do not report them passed. Source-patch/bootstrap checks in full pytest remain mandatory and provide static/source-shaped proof only, not instrumented native Linux binary execution.

## Result and evidence

Zero means required offline fake/static/replay checks passed at the unchanged clean revision, with any exact allowed Darwin native gaps explicitly retained. Nonzero means a failed/incomplete gate. No skipped/unavailable/missing check is counted passed. Unexpected skips, malformed/empty JUnit or missing required test IDs produce nonzero even after pytest exit 0. Only the two exact test identities named in research D3 with actual unavailable-`/proc` reasons on Darwin may be platform gaps; do not waive whole modules or accept other platforms/reasons automatically.

`summary.json` records format version, revision/tree/start/end state, safe environment/source versions, overall offline result, invocation, command records, test totals, required coverage, actual skips/platform gaps and real acceptance limitations. Write relative log references with SHA-256. Durably flush available results on exceptions/interruption; do not overwrite prior evidence. Do not claim hard process termination can always produce a final summary.

## Final revision policy

Commit code/tests first, run the full command from clean sources at the final integrated code revision, then capture exact result. Documentation-only follow-up must show `git diff TESTED_HEAD..DELIVERY_HEAD -- scripts src tests configs terraform bootstrap pyproject.toml` empty and review all remaining paths as documentation. Any executable/config/test/provider/tool-patch change, including outside this list, requires a new full gate. The coordinator retains private logs and publishes factual offline outcome with native/provider limitations.
