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

Coordinator integrates latest main before implementation; use `/private/tmp/cnb-offline-venv-20261025/bin/python` (Python 3.12.14). Terraform/provider/SSH/SCP/native commands are fake in these tests. Runtime staging fakes are not actual guest installation proof.

## Focused gates after implementation

```sh
/private/tmp/cnb-offline-venv-20261025/bin/python -m pytest -q tests/unit/test_f09_lifecycle.py tests/unit/test_f09_guest.py tests/unit/test_f09_bundle.py tests/unit/test_f09_cli.py tests/unit/test_retrieval.py tests/contract/test_f09_interfaces.py tests/integration/test_f09_bundle_replay.py
/private/tmp/cnb-offline-venv-20261025/bin/python -m pytest -q tests/integration/test_f09_lifecycle_modes.py tests/integration/test_f09_lifecycle_failures.py tests/integration/test_f09_lifecycle_recovery.py
/private/tmp/cnb-offline-venv-20261025/bin/python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts --check
/private/tmp/cnb-offline-venv-20261025/bin/python -m pytest -q
/private/tmp/cnb-offline-venv-20261025/bin/python -m cloud_network_benchmark.cli --help
/private/tmp/cnb-offline-venv-20261025/bin/python -m cloud_network_benchmark.cli lifecycle --help
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

## Historical offline gate — 2026-10-25 (superseded below)

The complete repository suite passed on code head `709fe24be285dc5fef5feb5b6ec2c51f40aec598`:

```sh
PATH=/private/tmp/cnb-tools/powershell-7.5.11:$PATH PYTHONPATH=src THESIS_ROOT='/Users/katya/Desktop/University/Masters/Thesis' MPLBACKEND=Agg MPLCONFIGDIR=/private/tmp/cnb-matplotlib-config /private/tmp/cnb-offline-venv-20261025/bin/python -m pytest -q
```

Result: **1,436 passed**, one warning, exit 0, 344.99 seconds. The sole warning is a `DeprecationWarning` in `tests/unit/test_f09_guest.py::test_staged_runtime_import_execution_does_not_mutate_immutable_package_digest`: `archive.extractall(runtime_root)` will use a changed extraction-filter default in Python 3.14. It did not fail the test. Full output is retained at `/private/tmp/cnb-f09-full-pytest-709fe24.log`.

The focused F12 analysis/replay checks passed: 43 passed, exit 0. The shared-contract schema check and root/lifecycle CLI help commands also exited 0. These checks were run offline; no cloud, credentials, paid experiment, or F10 readiness claim is implied.

## Latest F09 convergence checks — 2026-10-25

Checked code head: `d035121f309963fcb0b144ec50f79b6708d9bd2e`.

T040 now binds controller cancellation in all three default provider workflows to an event observed by the active F03 polling owner. The exact default-factory regression `[controller-interrupt-during-native-f03]` passed for AWS, Azure and GCP: cancellation arrives while their actual F03 status polls are blocked, then scoped stop, diagnostic/listener closure, retrieval and provider cleanup run. Controller interruption remains primary; cleanup reconciles in F11, billing remains nonfinal, and no funding is released. Guest SIGTERM is converted to a catchable interruption and the prior signal handler is restored. Two process identity consequence tests requiring Linux `/proc` were skipped on macOS and are not claimed as passed.

T041 normal cleanup writes source-bound absence receipts and reconciles the original F11 reservation before clearing private recovery flags. Recovery replays a durable receipt after injected F11 persistence interruption without provider commands. Unknown absence keeps cleanup open; unknown billing retains funding. Azure cleanup-only reconstruction retains observed absent resource IDs for reconciliation.

T043 preserves available F03 failure evidence after verified cancellation. The actual three-provider default factory runs each provider through its first native short-study unit, interrupts the real F03 status polls, and verifies that the closed partial outcome's exact source bytes, completed/unexecuted unit identity, commands and timings survive strict projection before cleanup. The outcome remains a failure and the F11 reservation releases no funds. Paired unverified-stop coverage verifies no outcome SCP/source record is accepted while cleanup still completes. Unit cases cover missing, malformed, wrong-identity, nonzero-exit and natural-exit cancellation-race outcomes.

Focused command:

```sh
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis MPLBACKEND=Agg MPLCONFIGDIR=/private/tmp/cnb-matplotlib-config PATH=/private/tmp/cnb-tools/powershell-7.5.11:$PATH /private/tmp/cnb-offline-venv-20261025/bin/python -m pytest -q tests/unit/test_f09_guest.py tests/integration/test_f09_aws_runtime_factory.py
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis MPLBACKEND=Agg MPLCONFIGDIR=/private/tmp/cnb-matplotlib-config PATH=/private/tmp/cnb-tools/powershell-7.5.11:$PATH /private/tmp/cnb-offline-venv-20261025/bin/python -m pytest -q tests/integration/test_f09_multi_provider_factory.py
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis MPLBACKEND=Agg MPLCONFIGDIR=/private/tmp/cnb-matplotlib-config PATH=/private/tmp/cnb-tools/powershell-7.5.11:$PATH /private/tmp/cnb-offline-venv-20261025/bin/python -m pytest -q 'tests/integration/test_f09_multi_provider_factory.py::test_default_three_provider_factory_reserves_and_enforces_global_claim_barrier[controller-interrupt-during-native-f03]' 'tests/integration/test_f09_multi_provider_factory.py::test_default_three_provider_factory_reserves_and_enforces_global_claim_barrier[controller-interrupt-unverified-stop]'
```

Result for the T043 guest/AWS focused command: 27 passed, 2 skipped because the local macOS host has no Linux `/proc` process identity interface, one Python tarfile deprecation warning, exit 0, 3.58 seconds. The multi-provider factory module passed separately: 11 passed, including synthetic DAY, exit 0, 104.02 seconds. Verified and unverified three-provider interruption parameters both passed: 2 passed, exit 0, 6.57 seconds.

The full repository command was run with `set -o pipefail` and captured using `tee` to `/private/tmp/cnb-f09-full-pytest-d035121.log`. It passed on the code head above: 1,445 passed, 2 skipped (the same Linux `/proc` cases), one Python tarfile deprecation warning, exit 0, 359.97 seconds. The warning is `DeprecationWarning` for `archive.extractall(runtime_root)` at `tests/unit/test_f09_guest.py:628`, whose extraction-filter default changes in Python 3.14.

The schema `--check`, root CLI help, lifecycle help, lifecycle module help, preview help and `git diff --check` each exited 0. All checks use the Python 3.12.14 runtime, `PYTHONPATH=src`, `THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis`, `MPLBACKEND=Agg`, `MPLCONFIGDIR=/private/tmp/cnb-matplotlib-config`, and bundled PowerShell 7.5.11 from `/private/tmp/cnb-f09` with `login=false`. The full suite uses offline fakes and synthetic source-bound fixtures. Native Linux behavior, provider API behavior, cloud cleanup/expiry and paid acceptance remain unproved; no cloud, credentials, paid experiment or F10 readiness claim is implied.
