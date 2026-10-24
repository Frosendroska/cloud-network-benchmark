# F09 V2 lifecycle implementation evidence

This record covers the credential-free implementation worktree only. The module-level preview is inspect-only. Paid lifecycle execution and provider-specific recovery adapters remain blocked pending complete paid prerequisites and a separately authorized stage. No provider APIs, credentials, Terraform plans, SSH, or native benchmark traffic were used; guest staging was exercised only through fake SCP/SSH runners.

## Commands run

Environment: `/private/tmp/cnb-f09`, `login=false`, `PYTHONPATH=src`, `THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis`, and `PATH=/private/tmp/cnb-tools/powershell-7.5.11:$PATH`; Python `/private/tmp/cnb-offline-venv/bin/python`.

| Check | Result |
| --- | --- |
| Focused F09 lifecycle/guest/bundle/CLI/retrieval/contract and four-mode replay suites | Passed, 73 tests in 2.30s |
| F09 mode/failure/recovery/replay plus F04 diagnostics integration suites | Passed, 63 tests in 9.93s |
| Full `python -m pytest -q` regression | Passed, 1342 tests in 168.38s |
| `python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts --check` | Passed, exit 0; no shared schema changes |
| `python -m cloud_network_benchmark.lifecycle_cli --help` and `... preview --help` | Passed; isolated module-level parser only |
| Root CLI lifecycle dispatch and final integrated-head gates | Pending coordinator confirmation that F12 CLI integration is merged |

The retrieval API accepts strict integer `max_attempts=1` or `2`, retaining the legacy default of two. F09 calls the API once per round with `max_attempts=1`; its only retry filters to one previously failed concrete artifact, recomputes the existing bounded copy handoff using the original deadline and cleanup reserve, checks admission again, and preserves both round records with unique action IDs (`attempt_offset=1`). An interrupted transfer is never retried. Tests cover the legacy default, rejection before side effects for invalid limits, one-attempt refusal, retry action identity and F09 retry admitted/refused/interrupted paths.

The four-mode replay test sends synthetic source-realistic bytes through closed inventory and fake retrieval, binds a clock map to a retrieved raw observation anchor, strictly saves/reparses `ValidatorInput`, invokes F08 validation, and seals the temporary bundle. It checks original file hashes remain unchanged. Its fixtures establish controller code-path behavior, not real provider behavior or valid research data. Guest staging likewise uses fake SCP/SSH; it verifies command construction, revision/archive checks, and dependency-version output without installing packages on an actual Ubuntu guest.

The selected package source is transferred with the existing trusted SCP options, checked by SHA-256 and source-revision marker, unpacked under a deployment-specific directory on A, and used through a newly created venv. The offline-safe venv uses system site packages and records the guest interpreter/Pydantic/PyYAML versions; missing dependencies fail before F03. This is a deliberate no-download implementation assumption, not evidence that a real image satisfies it.

## Remaining acceptance limits

The final root `cli.py` dispatch depends on coordinator confirmation that the F12 CLI merge is integrated; T026 remains pending. The full regression and shared-schema check above were run on the current pre-F12-integration feature head and must be repeated after integration. Offline mocks do not prove real cloud expiry, paid admission, guest package installation on Ubuntu, Linux guest behavior, native traffic or cloud cleanup. Those are separate gates; F10 is the end of this implementation stage.
