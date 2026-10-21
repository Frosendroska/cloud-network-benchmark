# F08 Validation Guide

## Context and current status

Worktree `/private/tmp/cnb-f08`, branch `codex/f08-v2-validation`, spec `specs/010-v2-native-validation`, merged F01R base `cef838e42fc620f90b5943f5a8d1528ddc0b0724`. Planning produces artifacts only; F08 functions/tests below are planned and do not exist yet. Use `PYTHONPATH=src`, canonical read-only `THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis` and `/private/tmp/cnb-offline-venv/bin/python`. No credentials, cloud utilities or benchmark traffic are needed.

## Planning checks available now

Run from this worktree, with explicit context on every Spec Kit command:

```sh
GIT_BRANCH_NAME=codex/f08-v2-validation SPECIFY_FEATURE_DIRECTORY=specs/010-v2-native-validation .specify/scripts/bash/check-prerequisites.sh --json --require-spec --require-tasks --include-tasks
git diff --check
```

The helper's JSON `BRANCH` may display the explicit spec directory basename rather than Git branch; `git branch --show-current` is authoritative. The feature paths must resolve under this worktree. Extension hooks are absent in this installation. The local ignored feature.json is worktree-specific and is not committed.

## Implementation acceptance (not run during planning)

After tasks create the owned modules/tests, run:

```sh
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/unit/test_native.py tests/unit/test_validation.py tests/unit/test_calibration.py tests/unit/test_summaries.py tests/contract/test_native_validation_interface.py tests/integration/test_native_validation_replay.py
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q
git diff --check
```

The focused suite must prove:

1. Actual Sockperf preamble/header/timing rows/log counts and iperf3 receiver JSON/JSONL sessions produce known bytes, elapsed time, quantiles and distinct counts; sender-only input cannot manufacture receiver evidence.
2. Boundaries at 999/1000 and 9999/10000 replies, sample n=0/1/2, target generation=.95, overlap=.99, full requested duration, 60/300-second skew and 100-ms clock error agree with the public contract. Include immediately below/above boundaries.
3. Four mode fixtures retain correctly associated units and all source evidence; loaded/idle/day coverage, continuous sessions, warm-up/omitted time, chunk gaps and actual timestamps are checked. Missing timestamps cannot prove coverage or matching.
4. Altered/truncated/missing/open/unverified files, symlink escape, corrupt rows/JSONL tails, duplicated/overlapping/reset receiver data, unavailable counters/provenance and contradictory command/timing records yield precise findings while preserving trusted partial intervals.
5. Twelve-trial/provider calibration fixtures reproduce count medians, smallest common N and no-common-plateau fallback; missing/duplicate/invalid/all-zero evidence gives no recommendation. No config changes or automatic frozen N.
6. Fixed evaluation time and unchanged raw source hashes yield identical machine-readable/text replay. Slow metrics/high diagnostics remain available without CPU/steal/byte-ratio warnings; acceptance/calibration/day never masquerade as independent short-study research deployments.

## Consumer handoff

F09 constructs the existing ValidatorInput, calls `validate_bundle` with a recorded evaluation time, uses the copied results from `evaluate_comparison` when complete matched peer evidence exists, then persists validation result/summary to the existing ArtifactLayout paths. F08 functions themselves do not write. F12 imports the native parsers to select underlying samples/receiver intervals for bins, uses validation status/source hashes and recomputes quantiles from samples. Neither needs an F03-local sidecar or new shared schema.

Actual cloud permissions, native source-build/runtime capability, real expiry/retrieval/cleanup and paid acceptance are not proved by F08 fixtures. Final offline system readiness is F10; paid stages remain separately authorized.
