# F09 V2 lifecycle implementation evidence

## Current offline implementation gate (2026-10-25)

The latest checked code head is `d035121f309963fcb0b144ec50f79b6708d9bd2e`. It includes actual default AWS/Azure/GCP factory composition, source-bound cleanup receipt reconciliation, bounded guest cancellation, and preservation of the available closed guest outcome after verified stop. All validation was credential-free and used fake external commands, synthetic source-bound inputs, and fake time. No cloud, credentials, Terraform apply, real SSH/SCP, or native benchmark traffic ran. The two Linux `/proc` process identity integration checks were skipped on this macOS host; native Linux guest behavior and cloud acceptance remain unproved.

| Check | Result |
| --- | --- |
| T043 guest/AWS focused suites | Passed, 27; skipped 2 Linux `/proc` cases; one tarfile deprecation warning (3.58s) |
| Multi-provider factory mode suite | Passed, 11, including synthetic DAY (104.02s) |
| Three-provider interruption variants | Passed, 2 (6.57s); verified stop projected partial source bytes/commands/timings; unverified stop made no outcome transfer while cleanup completed |
| Full repository `pytest -q` on the code head above | Passed, 1,445; skipped 2 Linux `/proc` cases; one tarfile deprecation warning (359.97s); raw output `/private/tmp/cnb-f09-full-pytest-d035121.log` |
| Shared-contract schema `--check` | Passed, exit 0; no schema changes |
| Root CLI help, lifecycle help, lifecycle module help and preview help | Passed, all four exit 0 |
| `git diff --check` | Passed, exit 0 |

The sole warning is `DeprecationWarning` at `tests/unit/test_f09_guest.py:628`: Python 3.14 will change `tarfile.extractall` filtering defaults. It did not fail validation. The full suite and focused tests used `/private/tmp/cnb-offline-venv-20261025/bin/python` (Python 3.12.14), `PYTHONPATH=src`, `THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis`, `MPLBACKEND=Agg`, `MPLCONFIGDIR=/private/tmp/cnb-matplotlib-config`, and `PATH=/private/tmp/cnb-tools/powershell-7.5.11:$PATH`, from `/private/tmp/cnb-f09` with `login=false`. The full-suite command was `set -o pipefail; PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis MPLBACKEND=Agg MPLCONFIGDIR=/private/tmp/cnb-matplotlib-config PATH=/private/tmp/cnb-tools/powershell-7.5.11:$PATH /private/tmp/cnb-offline-venv-20261025/bin/python -m pytest -q 2>&1 | tee /private/tmp/cnb-f09-full-pytest-d035121.log`; exit 0.

The controller interruption regression blocks all selected providers in their real production F03 status-poll stages until the actual default-hook cancellation callbacks run. Each provider then issues its scoped stop, closes diagnostic processes and its listener, retrieves available closed evidence, and completes cleanup. The controller interruption remains the primary lifecycle failure; the F11 reservation remains nonfinal and releases no funds. T043 additionally executes native F03 for the first selected short-study unit before injected interruption. After verified stop, the closed partial GuestOutcome is identity/source validated, exact raw JSON bytes are recorded at `metadata/guest-outcome-source.json`, and actual command/timing evidence reaches saved ValidatorInput before cleanup seals the bundle. Paired unverified-stop coverage verifies the framework does not attempt outcome SCP or create a source record, while provider cleanup still runs. Unit cases cover absent, malformed, wrong-identity and nonzero-exit outcomes. Guest tests also verify the SIGTERM-to-`KeyboardInterrupt` bridge restores the previous signal handler, while process identity consequences that require Linux `/proc` are explicitly not claimed from the skipped cases.

Normal provider cleanup persists an absence receipt, reconciles the original F11 reservation, then clears recovery flags. If F11 persistence fails after the receipt, explicit cleanup replays the receipt without provider commands and completes reconciliation. Unknown absence keeps cleanup liability open; unknown billing keeps funding retained. These results establish offline implementation behavior only.

The following checkpoint narratives are historical and superseded where their completion statements conflict with this current gate. They are retained to preserve the progression of implementation evidence.

Historical checkpoint: F09 code commit `9a66147277f188a1473d58f2346bbff4b20f99bb` after normal integration of verified F12 `origin/main` at `5028ab97c3a906e51a42ad11dade746251847e71` (merge commit `57fec394ecd57f58f03e5c4b1989120022a07df5`).

## Commands run

Environment: `/private/tmp/cnb-f09`, `login=false`, `PYTHONPATH=src`, `THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis`, and `PATH=/private/tmp/cnb-tools/powershell-7.5.11:$PATH`; Python `/private/tmp/cnb-offline-venv/bin/python`.

| Check | Result |
| --- | --- |
| F09 focused lifecycle/guest/bundle/CLI/retrieval/contract and four-mode replay suites | Passed, 75 tests in 3.09s |
| F09 mode/failure/recovery integration suites | Passed, 45 tests in 0.22s |
| F09 CLI + F12 analysis replay + existing config CLI compatibility suites | Passed, 70 tests in 215.44s |
| Full `python -m pytest -q` regression after F12 integration and T026 | Passed, 1387 tests in 397.23s |
| `python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts --check` | Passed, exit 0; no shared schema changes |
| Root `cli --help`, root `cli lifecycle --help`, lifecycle module `--help` and `preview --help` | Passed, exit 0; all four commands |
| `git diff --check` | Passed, exit 0 |

All checks above used `/private/tmp/cnb-offline-venv/bin/python` with `PYTHONPATH=src`, `THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis`, `PATH=/private/tmp/cnb-tools/powershell-7.5.11:$PATH`, and `MPLCONFIGDIR=/private/tmp/cnb-matplotlib-config`, from `/private/tmp/cnb-f09` with `login=false`. The full regression ran against the exact code committed as `9a661477`; subsequent changes are limited to this evidence record and the T029 completion marker. The root lifecycle safe-run test returned rejected with zero external calls and created neither results nor private state paths.

The retrieval API accepts strict integer `max_attempts=1` or `2`, retaining the legacy default of two. F09 calls the API once per round with `max_attempts=1`; its only retry filters to one previously failed concrete artifact, recomputes the existing bounded copy handoff using the original deadline and cleanup reserve, checks admission again, and preserves both round records with unique action IDs (`attempt_offset=1`). An interrupted transfer is never retried. Tests cover the legacy default, rejection before side effects for invalid limits, one-attempt refusal, retry action identity and F09 retry admitted/refused/interrupted paths.

The four-mode replay test sends synthetic source-realistic bytes through closed inventory and fake retrieval, binds a clock map to a retrieved raw observation anchor, strictly saves/reparses `ValidatorInput`, invokes F08 validation, and seals the temporary bundle. It checks original file hashes remain unchanged. Its fixtures establish controller code-path behavior, not real provider behavior or valid research data. Guest staging likewise uses fake SCP/SSH; it verifies command construction, revision/archive checks, and dependency-version output without installing packages on an actual Ubuntu guest.

The selected package source is transferred with the existing trusted SCP options, checked by SHA-256 and source-revision marker, unpacked under a deployment-specific directory on A, and used through a newly created venv. The offline-safe venv uses system site packages and records the guest interpreter/Pydantic/PyYAML versions; missing dependencies fail before F03. This is a deliberate no-download implementation assumption, not evidence that a real image satisfies it.

The default runtime factory now constructs all selected AWS, Azure and GCP drivers behind one actual F11 reservation, with per-provider expiry requests and recovery references. Provider claims include the complete selected request set and all observed protection evidence. A three-provider synthetic paid-source envelope test injects failure at every external command boundary, confirms all three real protection adapters return typed failure evidence, and asserts no provider pair-creation action runs. This validates the failure barrier and sourced constructor handoff; it does not establish successful cross-provider protection/provision/cleanup or a native measurement.

### Cleanup-only runtime reconstruction (2026-10-24)

The recovery journal now stores the original F11 ledger path/scope and exact private per-provider cleanup-settings references before provider side effects. These settings retain original deployment/reservation/expiry identities, native provider options, and a content digest; cleanup validates the selected driver’s exact namespace, target selector, expiry lead, and journal path before calling AWS cleanup, GCP cleanup, or Azure destroy. The `cleanup` CLI no longer requires or reads current runtime configuration, cost inputs, or approvals. It never reserves again or resumes provision/measurement. Reconciliation writes a private receipt bound by SHA-256 and records unknown billing without releasing funding; F11 liability remains open unless typed provider inventory plus persisted absence IDs prove cleanup.

Offline integration uses the default cleanup CLI over a three-provider attempt that failed during the all-provider protection barrier. It invokes all three provider cleanup paths with a fake command runner, verifies the original F11 reservation remains in both cleanup-open and cleanup-blocked sets when complete absence cannot be proven, and verifies a tampered AWS cleanup-settings file is rejected before its provider command. Only GCP's read-only inventory boundary is reached for this no-create fixture. No clouds, current paid approvals, or current prices are used. This does not prove successful resource deletion for a populated cloud attempt.

## Remaining acceptance limits

Offline mocks do not prove real cloud expiry, paid admission, guest package installation on Ubuntu, Linux guest behavior, native traffic or cloud cleanup. Those are separate gates; F10 is the end of this implementation stage.

## Production lifecycle wiring checkpoint (2026-10-24)

The default lifecycle CLI now reserves through F11 and composes the AWS driver and production remote-stage callbacks. Post-cleanup projection in `lifecycle_projection.py` updates the existing strict child-manifest evidence, reconstructs a strict `ValidatorInput` from materialized sources, binds retrieved acquisition evidence, saves and reparses each input, runs fixed-time per-provider validation with selected-peer comparison, updates validation status and the parent manifest, then seals result roots only after the batch mutations. A synthetic three-provider failure-path integration test exercises the batch ordering and strict replay; the AWS factory test exercises the admitted default factory through provider apply and cleanup with fake commands, then verifies strict input replay and checksum sealing.

Focused command at this checkpoint: `PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m pytest tests/integration/test_f09_aws_runtime_factory.py tests/integration/test_f09_lifecycle_failures.py tests/unit/test_f09_lifecycle.py tests/unit/test_f09_cli.py tests/unit/test_f09_bundle.py tests/integration/test_f09_bundle_replay.py -q` — passed, 70 tests in 3.26s. `git diff --check` passed. This is not full F09 convergence evidence: the default AWS fake runner stops during guest preparation, so successful captured F03/F04/native retrieval is not demonstrated; Azure/GCP production factories, durable retry consumption and actual provider cleanup/reconciliation remain incomplete. No cloud or real paid calls were made.
