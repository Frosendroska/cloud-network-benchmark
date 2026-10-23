# F11 offline validation guide

Planning artifacts describe proposed code. F11 modules, fixture paths and feature tests below do not exist at planning time; their commands are implementation acceptance targets, not recorded successes. No credential/cloud/SSH/Terraform apply operation is needed.

## Environment

Run from the assigned worktree with its branch verified. Existing setup uses Python `/private/tmp/cnb-offline-venv/bin/python`, `PYTHONPATH=src`, `THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis`; the Thesis path is read-only. No dependency installation is needed.

## Existing commands

```sh
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark --help
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts --check
git diff --check
```

The implementation stage runs the focused checks after adding the files, then runs the existing suite once after final changes:

```sh
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/unit/test_f11_schedule.py tests/unit/test_f11_budget.py tests/contract/test_f11_admission.py tests/integration/test_f11_ledger.py
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark.admission --help
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark.admission preview --schedule tests/fixtures/f11/schedule-pending.csv --config-map tests/fixtures/f11/config-map.json --cost-inputs tests/fixtures/f11/cost-inputs.json --now 2026-10-22T00:00:00Z
```

Preview exit0 means valid inspectable intent; pending N/windows/prices/paid authority remain explicit and no ledger is created. Invalid input exit2. Test fixtures containing a complete synthetic week identify their dates/N/support rates as simulated values and do not change shipped research configuration. The saved scoped cost fixture identifies its exact selected payload, original ranges, acquisition attribution, October14 price date and raw-extract hashes from research.md; it is not a workbook revision/hash or live quote.

## Acceptance scenarios

1. Preserve quoted/multiline CSV bytes and source/row hashes; reject duplicate/secret/unknown/mismatched references before any mutation. Preview56 primary entries as28 paired slots, with four VMs/cloud demand; reject missing windows/date misalignment and resource/state/result identity collisions. Keep pending values pending.
2. Start a materialized local ledger from explicit initial funding and fake time. Reserve simultaneous S1/S3 with distinct claims, moving primary/tax/support funding once. Reconcile incurred costs without freeing active unspent liability. Exact250 working passes; above250 refuses; protected50 remains outside commitments. Missing/underpriced support fails, including actual merged AWS fixture/hash and one/two-region inclusion.
3. Activate original20/30/1460-minute caps before registration. Include75/225/950/86407-second minimum mode execution plus supplied shutdown grace and60 stabilization. Reject too-small allowances, registration consumption, waiting-to-slot overflow, earliest-trigger overflow, UTC reversal extension, wrong action/identity/timestamp/source, missing all-provider expiry or duplicate creation grant. Never run the measurement to test this.
4. Exercise concurrent local processes, same/different fingerprint retries and fault points around fsync/replace. Restart retains reservation/uncertain claim and blocks automatic create. Corrupt/missing initialized/symlink state fails closed without reset; no duplicate launch grant.
5. Record actual vs estimated costs, primary plus secondary failures and unknown cleanup. No reservation proceeds while any billable resource liability remains open; stop/deallocate/cap-expiry never proves deletion. After sourced cleanup reconciliation, verify one replacement/original and three/provider; reject chains/successful originals/DAY/CAL/S2/S4 automatic retries. Optional scope stays gated.
6. Classify actual per-phase skew at60, just above60,300, just above300 and unavailable starts. Missed entries never schedule catch-up. Assert final-copy handoff preserves original-cap-minus-cleanup and at-most-one retry; this does not retest/implement retrieval. Assert all feature operations invoke zero external/cloud commands.

## Evidence boundary

Record exact implementation/convergence head, command exits, test counts/no skips, schema/diff/preview results and assumptions in `docs/f11-schedule-budget.md`. These prove local admission behavior. F09 composition, F10 final integrated gate and separate authorized paid acceptance still must establish their own evidence; provider timing/expiry/deletion/billing/quota are not proved by these fakes.
