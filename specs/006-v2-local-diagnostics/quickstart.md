# F04 offline validation guide

Run in `/private/tmp/cnb-f04` on `codex/f04-v2-diagnostics`. No cloud credentials, guest access, benchmark traffic or network setup is required. Existing environment: `/private/tmp/cnb-offline-venv/bin/python`, `PYTHONPATH=src`, `THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis`. The source modules/tests below are planned implementation outputs; they must not be reported as already passing at the planning handoff.

## Focused checks after implementation

```sh
test "$PWD" = /private/tmp/cnb-f04
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/unit/test_diagnostics.py tests/unit/test_diagnostics_clock.py tests/contract/test_diagnostics_contract.py tests/integration/test_diagnostics_collection.py
```

Expected: no failures/skips, no cloud/traffic commands and no real-day wait. Inspect:

1. Arithmetic fixtures distinguish active/steal CPU, guest accounting, aggregate versus maximum core, named TCP columns, NIC direction/bytes, true zero and large integers. Missing/reset/recovery/core changes invalidate only affected evidence.
2. Fake time covers normal 86,400-second streaming, delayed ticks, no catch-up burst, backward/forward UTC and early stop. Assert actual intervals and bounded memory, not a whole-day list.
3. AWS/Azure/GCP A/B metadata fixtures preserve observed values, missing identity, mixed CPU models, source/binary mismatches, driver versus acceleration evidence and provider-limit documentation. No live provider metadata endpoint is used.
4. Clock fixtures assert exact local read-only argv, finite positive timeouts <=0.2 seconds and remaining duration, no query at exhaustion, estimate units/capture allowance, unsynchronized/local/unknown evidence and 100/100.001-ms eligibility. No synchronization mutation occurs.
5. Materialized JSON/JSONL uses unchanged shared runtime models and exported schemas; a truncated line remains unchanged with a parse failure. Existing bytes/symlink collisions survive; disk/write/flush/close/interruption failures report partial artifacts and truthful closure.
6. Directly copied guest file runs `--help` under a temporary cwd with `PYTHONPATH` removed, proving no package/Pydantic dependency. A subprocess timeout bounds this test. Real Linux reads use rooted fixtures; no cloud guest is executed.

## Regression and schema checks

```sh
THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m pytest -q
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts --check
/private/tmp/cnb-offline-venv/bin/python -m py_compile src/cloud_network_benchmark/diagnostics.py src/cloud_network_benchmark/diagnostics_guest.py
git diff --check
```

Expected: all exit 0, no unexplained skips, no shared schema changes. Record actual counts, commands/exits and tested SHA in `docs/validation/f04-local-diagnostics.md`. Do not call Terraform, real clouds or live benchmark traffic: no changed infrastructure is owned here.

## Later integration acceptance (not performed by F04)

F09 stages nonsecret context/script to each guest, resolves the private-path NIC, starts collection before the mode and stops it afterward under the existing overall cap. It consumes each role's declarations/closure, copies only after measurement, verifies hashes, projects clock mappings into timing, and still attempts cleanup on failures. F08/F12 integration reads the extra clock files and checks interval coverage/discontinuities before aligned plots. Separate paid acceptance must establish actual Python/clock utility availability, bound acceleration observations and measured overhead; offline fixtures prove none of those real-cloud properties.

## Planner artifact checks

```sh
GIT_BRANCH_NAME=codex/f04-v2-diagnostics SPECIFY_FEATURE_DIRECTORY=specs/006-v2-local-diagnostics .specify/scripts/bash/check-prerequisites.sh --json --require-spec --require-tasks --include-tasks
git diff --check
```

The tooling may display `BRANCH=006-v2-local-diagnostics` from feature context; actual Git branch must separately be `codex/f04-v2-diagnostics`. This is tooling nomenclature, not a branch mutation. Every command uses the assigned absolute cwd and feature environment explicitly.
