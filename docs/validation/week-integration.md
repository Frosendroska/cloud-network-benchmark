# Paired WEEK integration evidence

Status: combined implementation candidate prepared on published main for root review and publication. This records offline code integration only; it does not authorize or qualify a WEEK launch.

## Source and ancestry

- Published main at integration start: `1e87f31b206450003df86a6c9a3687a693d914a7`.
- Reviewed pre-DAY candidate: `bed9b98893540e52a0433a3213f20e75b3dd0d1d`, tree `15eaae842d9db6d0df3c142d1c2965d31d883a0c`.
- The histories share merge base `83bd9be99ce0df1be9491bbc1804657d30d4ab05`. The candidate was not a descendant of published main. A merge commit preserves both histories; the merge had no conflicts. The 44 candidate commits add the reviewed V19 production fixes and tests across 38 paths. Main's tree was unchanged from the shared parent, so the merge tree matched the reviewed candidate tree.
- WEEK observer source: `7e241c4650d275b4b798f358bde1055f8ed66689`, tree `c69ee254754dd236b25aa2e092775decf91d5c5d`.
- WEEK failure-policy source: `4c71aea3cedb4df66a3cc8a1861eca6097ad8786`, tree `f06d6a2e5bdc1574ec517ec174890edc18e37c57`.
- Combined commits: merge `7f9e105`, observer integration `f13ff35`, continuation integration `588daa6`.
- Combined candidate HEAD: `588daa6b382da29c94893165e1bc9747702af5e1`; tree `8067f649a13ae6654e38c4fbb6c20a8ec8cdbc14`.

The candidate-only diff from main is available with the integration bundle and patch. It contains the 44 reviewed pre-DAY changes plus the observer and failure-continuation changes. Root review found no removed financial safeguards, credentials, Terraform state or operational state in the diff.

## Adopted continuation behavior

After a WEEK slot, a later complete scheduled S1/S3 pair may proceed only when earlier attempted members have settled and each has literal `cleanup_open: false`. Open cleanup, launch uncertainty and `archive_pending_validation` still block. An unsettled future slot remains scheduled for an explicit later invocation; the runner does not poll providers or auto-restart. Existing missed records stay missed. Failures and missingness remain in the record; no singleton, retry, backfill, replacement ID, schedule shift, cap change or funding release was added. Acceptance-stage behavior is unchanged.

The change affects WEEK stopping and missingness policy. It does not change workload, slot count, UTC schedule, VM topology, measurement criteria, cost limits or cleanup deadlines. A successful sibling is not a valid matched S1/S3 pair. Report attempted, failed, missed, valid and matched counts with reasons; do not impute missing observations or claim all 28 pairs succeeded.

`archive_pending_validation` remains an explicit dispatch blocker in the integrated driver and in `test_week_failure_continuation.py`. This integration does not add archive progression or alter prepared stages/cards.

## Combined native Linux qualification

The combined affected selector set ran on Halle Linux with existing Python 3.12.3. It used the existing venv, an empty environment, `unshare -Urn`, disabled pytest plugin autoload and bytecode/cache writes, and an audit hook. The hook denied guarded network and process dispatch attempts before execution; it permitted only the source-bound read-only `git rev-parse HEAD`, local Python fixture children, and the test-created sleeping `sockperf` stub. No provider, credential, GCS, operational-state or launch action ran.

Result: **275 passed, 0 failures, 0 errors, 1 warning** in 144.64 seconds. JUnit SHA-256: `958748fb759e388e7d5f29e9e4e7bed015c9f10ef806f5dd64c78936c978d848`.

The selection combined the observer chain, failure-continuation regressions, campaign admission/driver/service tests, observer and guest tests, runtime configuration, campaign inputs and financial controls. It includes the original observer tests and failure regressions in one source tree; the earlier 238-test and 125-test handoffs remain separate historical runs and are not added to 275.

This is focused offline Linux evidence, not a full test suite, real provider acceptance, real cleanup proof, credential renewal, eight-day endurance or research measurement. Future WEEK preparation must regenerate inputs from this exact source and receive separate current approval. Existing prepared inputs, cards and operational state were not changed.
