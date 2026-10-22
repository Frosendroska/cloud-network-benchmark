# F04 local diagnostics validation

Implementation source and tests were validated at commit `6be1935` (`6be1935ab26377ea4bff9eaea6cb3b8619787fc7`) on `codex/f04-v2-diagnostics`, based on merged F03 head `7a70a3139c5ee9efa1f8ea13c3f6de40e9048121`. The later task-marker/documentation commit changes no executable files.

## What is implemented

`src/cloud_network_benchmark/diagnostics_guest.py` is a directly executable standard-library guest collector. It reads rooted `/proc` and `/sys` observations, derives the ten declared metrics from actual monotonic intervals, collects once-per-VM static metadata, and writes exclusive compact metadata JSON plus diagnostic and clock JSONL. Files stay on the guest output root and each JSONL line is flushed as it is written. Deadline, command, clock, file, and stop boundaries accept injected test doubles. It does not invoke a shell, traffic generator, provider API, or remote transport.

`src/cloud_network_benchmark/diagnostics.py` adapts provider deployment and optional bootstrap evidence into a validated role-specific context, returns three local `ArtifactInput` declarations (the existing metadata and diagnostic paths plus `vm_a_clock` or `vm_b_clock`), and streams shared-model records back through typed loaders. Clock-pair eligibility requires observed synchronized chrony sources and estimates no greater than 100 ms for both roles. It does not certify retrieval or interval coverage.

Counter decreases remain unavailable without wrap/clamp assumptions. Failed reads invalidate only affected evidence and recovery requires valid consecutive observations. Missed ticks retain actual elapsed time and are not backfilled. Missing chrony, image/build, driver, acceleration, or provider-limit evidence remains explicit. High host metrics do not trigger performance labels or exclusions.

## Checks

| Command | Exit and result |
| --- | --- |
| `PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/unit/test_diagnostics.py tests/unit/test_diagnostics_clock.py tests/contract/test_diagnostics_contract.py tests/integration/test_diagnostics_collection.py` | 0; 26 passed, no skips (9.08 s) |
| `THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m pytest -q` | 0; 653 passed, no skips (34.35 s) |
| `PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts --check` | 0; shared schemas unchanged |
| `/private/tmp/cnb-offline-venv/bin/python -m py_compile src/cloud_network_benchmark/diagnostics.py src/cloud_network_benchmark/diagnostics_guest.py` | 0 |
| `git diff --cached --check` | 0; no whitespace errors in the source/test commit |

The focused integration suite includes an uninterrupted fake 86,400-second stream, deadline exhaustion, early stop, delayed ticks, missing/reset/recovery cases, write/flush/close failures, output collisions and symlink rejection, clock query bounds, both-role artifact identity, and an isolated copy of the guest script run with `--help` and no project `PYTHONPATH`. These are offline fixture/fake results, not evidence about actual guest packages, clock accuracy, cloud permissions, NIC driver semantics, or measurement overhead.

## Offline boundary and handoff

No cloud credentials, real VM access, SSH/SCP, benchmark traffic, Terraform checks, paid execution, or external provider queries were used. Those checks are not applicable to F04-owned code and are not claimed as passed. A missing or unsupported local `chronyc` leaves synchronization accuracy unavailable; aligned plots must remain ineligible until real clock evidence exists. F09 owns staging, paired start/stop within the caller's cap, after-measurement retrieval, hash verification, and projecting the clock lines into timing evidence. F08/F12 own downstream parsing and interval coverage/discontinuity decisions. Convergence is the next Spec Kit stage.
