# F09 offline implementation acceptance

This guide records the credential-free implementation checks. Never run lifecycle paid commands as part of this gate. Preview is inspect-only; the root CLI delegates lifecycle subcommands to the isolated lifecycle parser after F12 CLI integration.

## Environment

Use assigned worktree, `login=false`, PWD guard and explicit feature context on every shell invocation:

```sh
set -e
test "$PWD" = /private/tmp/cnb-f09 || exit 97
export GIT_BRANCH_NAME=codex/f09-v2-lifecycle
export SPECIFY_FEATURE_DIRECTORY=specs/013-v2-lifecycle
export THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis
export PYTHONPATH=src
export PATH=/private/tmp/cnb-tools/powershell-7.5.11:$PATH
```

Coordinator integrates latest main before implementation; use `/private/tmp/cnb-offline-venv/bin/python` (prepared Python3.11.13). No dependency download is required for F09. Terraform/provider/SSH/SCP/native commands are fake in these tests. Runtime staging fakes are not actual guest installation proof.

## Focused gates after implementation

```sh
/private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/unit/test_f09_lifecycle.py tests/unit/test_f09_guest.py tests/unit/test_f09_bundle.py tests/unit/test_f09_cli.py tests/unit/test_retrieval.py tests/contract/test_f09_interfaces.py tests/integration/test_f09_bundle_replay.py
/private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/integration/test_f09_lifecycle_modes.py tests/integration/test_f09_lifecycle_failures.py tests/integration/test_f09_lifecycle_recovery.py
/private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts --check
/private/tmp/cnb-offline-venv/bin/python -m pytest -q
/private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark.cli --help
/private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark.cli lifecycle --help
git diff --check
```

F12 analysis CLI help/dispatch is checked through its actual integrated parser tests, preserving the selected command names after latest-main integration. Do not assert an unmerged sibling command exists at this base. No Terraform source changes are planned, so no new provider static validation run is claimed by F09; if implementation needs such changes, coordinator serializes ownership and requires that provider's actual static/fake checks too.

## Required fixture evidence

1. Parameterize three provider adapters across acceptance/short/CAL/DAY; assert exact identities and trace ordering. Use fake clocks for full durations without actual waiting. Healthy providers overlap provisioning/preparation; all expiry verification finishes before first create. Captured guest argv executes on A; diagnostics on A/B; never invoke real binaries/network.
2. Feed actual source-realistic native/observation/build bytes through the declared closed inventory and existing retrieval boundary into strict saved ValidatorInput; replay validate_bundle and comparison with fixed evaluated_at. Use production bind_acquisition_sources/iter_source_windows. Both child/parent snapshots match, including DAY family/concrete projection and unique phase logs. No F08 fixture editing or unchecked DTO construction.
3. Trace full CAL sequence/one pair and continuous DAY/one TCP process, chunk rotation and gaps; record actual timestamps, slot misses and ≤60/>60–300/>300/unknown skew. No invented N, study dates or CAL/DAY baseline.
4. Inject stage failures for every provider: reserve/registration/claim/create (including uncertain create), runtime staging/bootstrap/readiness, server/client/diagnostics/clock/close, inventory/first copy/retry/hash/destination, cleanup/absence/reconciliation/persistence. Assert failure preservation, best-effort peer finalization, cap/cleanup remainder and no extra launches.
5. Abrupt-loss fake omits finalizer after durable expiry/claim. Restart with changed session blocks work/creation, explicit cleanup verifies residual resources including safety infrastructure, and reconciliation clears only proved liabilities. Independent registered expiry remains evidence, not proof it actually executed.
6. Validate sealed scope/digests and unchanged original bytes; input/result/index have no self-reference; corrupt or missing source proof remains invalid/incomplete/unavailable. Replaying a sealed bundle performs no transport/provider operations.

## Reporting

Record exact integrated head, commands/exit results/test counts in `docs/validation/f09-v2-lifecycle.md`; distinguish not run, unavailable and passed. Finish Spec Kit implementation/convergence with zero actionable findings. Offline fakes establish instrument readiness only; F10 integrated gate and separate paid Linux/cloud/expiry acceptance remain unproved.
