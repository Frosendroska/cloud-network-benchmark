# F09 V2 lifecycle implementation evidence

This record covers the credential-free implementation worktree only. The module-level preview is inspect-only. Paid lifecycle execution and provider-specific recovery adapters remain blocked pending complete paid prerequisites and a separately authorized stage. No provider APIs, credentials, Terraform plans, SSH, or native benchmark traffic were used.

## Commands run

Environment: `/private/tmp/cnb-f09`, `login=false`, `PYTHONPATH=src`, `THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis`, and `PATH=/private/tmp/cnb-tools/powershell-7.5.11:$PATH`; Python `/private/tmp/cnb-offline-venv/bin/python`.

| Check | Result |
| --- | --- |
| Focused F09 unit, contract and integration suites before the retrieval boundary extension | Passed, 66 tests |
| `python -m pytest -q tests/unit/test_retrieval.py tests/unit/test_f09_bundle.py` after the strict retry integration | Passed, 42 tests |
| `python -m pytest -q tests/unit/test_f09_bundle.py` including strict saved-input replay through F08 | Passed, 7 tests |
| `python -m cloud_network_benchmark.lifecycle_cli --help` and `... preview --help` | Passed; module-level parser only |
| Shared schema, full pytest regression, integrated root CLI tests, `git diff --check` | Pending final integrated-head gate |

The retrieval API accepts strict integer `max_attempts=1` or `2`, retaining the legacy default of two. F09 calls the API once per round with `max_attempts=1`; its only retry filters to one previously failed concrete artifact, recomputes the existing bounded copy handoff using the original deadline and cleanup reserve, checks admission again, and preserves both round records with unique action IDs (`attempt_offset=1`). An interrupted transfer is never retried. Tests cover the legacy default, rejection before side effects for invalid limits, one-attempt refusal, retry action identity and F09 retry admitted/refused/interrupted paths.

The saved-input replay test writes a real strict `ValidatorInput`, reparses it from disk and invokes F08 validation. Its fixture is synthetic; the test establishes controller code-path behavior, not real provider behavior or valid research data.

## Remaining acceptance limits

The final root `cli.py` dispatch depends on coordinator confirmation that the F12 CLI merge is integrated. Full regression and shared-schema checks have not yet run after final integration. Offline mocks do not prove real cloud expiry, paid admission, guest package installation, Linux guest behavior, native traffic or cloud cleanup. Those are separate gates; F10 is the end of this implementation stage.
