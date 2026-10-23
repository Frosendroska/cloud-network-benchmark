# F04 local diagnostics validation

Implementation source and tests were validated at code/test head `7361476` (`7361476861a88bc58794e0a2ec2be2a204a63dee`) on `codex/f04-v2-diagnostics`, based on F04 implementation commit `08f8f84de40ab6f7d970bd204641ea9eb283c3ab`. This head includes a coordinator-authorized test-fixture timing repair for inherited F03 cancellation coverage; the following documentation commit changes no executable files.

## What is implemented

`src/cloud_network_benchmark/diagnostics_guest.py` is a directly executable standard-library guest collector. It reads rooted `/proc` and `/sys` observations, derives the ten declared metrics from actual monotonic intervals, collects once-per-VM static metadata, and writes exclusive compact metadata JSON plus diagnostic and clock JSONL. Files stay on the guest output root and each JSONL line is flushed as it is written. Deadline, command, clock, file, and stop boundaries accept injected test doubles. It does not invoke a shell, traffic generator, provider API, or remote transport.

`src/cloud_network_benchmark/diagnostics.py` adapts provider deployment and optional bootstrap evidence into a validated role-specific context, returns three local `ArtifactInput` declarations (the existing metadata and diagnostic paths plus `vm_a_clock` or `vm_b_clock`), and streams shared-model records back through typed loaders. Clock-pair eligibility requires observed synchronized chrony sources and estimates no greater than 100 ms for both roles. It does not certify retrieval or interval coverage.

Counter decreases remain unavailable without wrap/clamp assumptions. Failed reads invalidate only affected evidence and recovery requires valid consecutive observations. Missed ticks retain actual elapsed time and are not backfilled. Missing chrony, image/build, driver, acceleration, or provider-limit evidence remains explicit. High host metrics do not trigger performance labels or exclusions.

Convergence repairs keep malformed CPU cores from hiding aggregate values or fabricating maximum-core utilization; timestamp CPU and UTC around snapshot reads and preserve a partial sample when a read crosses the deadline; hash tool binaries in bounded chunks; resolve rooted sysfs links component by component; convert invalid MTU and empty guest strings to unavailable evidence; reject provider, declaration, closure, count, and monotonic clock-order contradictions in materialized outcomes; and strictly parse complete chrony numeric fields and units. Regression fixtures cover aggregate-only/malformed/recovered cores, delayed and deadline-crossing snapshots, deadline-exhausted binary identity with observed/supplied mismatch, relative lower-interface and absolute VF links, invalid metadata, outcome contradictions, malformed chrony fields, and UTC reversal.

The follow-up rooted-sysfs repair also resolves class-NIC symlinks before reading all six NIC counters, MTU and lower-interface enumeration, while rejecting missing and escaping targets. Malformed or absent aggregate CPU rows remain isolated from complete numbered-core rows. Snapshot records use the actually observed UTC/monotonic endpoint, so a wall-clock step is preserved without interpolating an unobserved timestamp; typed loading accepts structurally valid UTC reversals and keeps collection completion independent from later interval/discontinuity eligibility.

The final integrated regression exposed one inherited F03 fixture issue: the cancellation test passed the current monotonic instant as its stop deadline, leaving no budget for process-reap and pipe-drain workers. The test now supplies a bounded one-second shutdown budget after the 1.25-second measured interval. Its duration, UTC-jump, kill outcome, closed streams and captured bytes assertions remain unchanged; production process code and failure checks were not edited.

## Checks

| Command | Exit and result |
| --- | --- |
| `test "$PWD" = /private/tmp/cnb-f04 && PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/unit/test_diagnostics.py tests/unit/test_diagnostics_clock.py tests/contract/test_diagnostics_contract.py tests/integration/test_diagnostics_collection.py` | 0; 41 passed, no skips (9.72 s) |
| `test "$PWD" = /private/tmp/cnb-f04 && THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m pytest -q` | 0; 668 passed, no skips (35.06 s) |
| `test "$PWD" = /private/tmp/cnb-f04 && PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/unit/test_measurement_process.py::test_production_adapter_preserves_monotonic_duration_on_kill_cancellation` | 0; 1 passed (1.25 s) |
| `PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts --check` | 0; shared schemas unchanged |
| `/private/tmp/cnb-offline-venv/bin/python -m py_compile src/cloud_network_benchmark/diagnostics.py src/cloud_network_benchmark/diagnostics_guest.py` | 0 |
| `git diff --check` | 0; no whitespace errors |

The focused integration suite includes an uninterrupted fake 86,400-second stream, deadline exhaustion, early stop, delayed ticks, missing/reset/recovery cases, write/flush/close failures, output collisions and symlink rejection, clock query bounds, both-role artifact identity, and an isolated copy of the guest script run with `--help` and no project `PYTHONPATH`. These are offline fixture/fake results, not evidence about actual guest packages, clock accuracy, cloud permissions, NIC driver semantics, or measurement overhead.

## Offline boundary and handoff

No cloud credentials, real VM access, SSH/SCP, benchmark traffic, Terraform checks, paid execution, or external provider queries were used. Those checks are not applicable to F04-owned code and are not claimed as passed. A missing or unsupported local `chronyc` leaves synchronization accuracy unavailable; aligned plots must remain ineligible until real clock evidence exists. F09 owns staging, paired start/stop within the caller's cap, after-measurement retrieval, hash verification, and projecting the clock lines into timing evidence. F08/F12 own downstream parsing and interval coverage/discontinuity decisions. Convergence is the next Spec Kit stage.
