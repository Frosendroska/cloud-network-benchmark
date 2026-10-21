# F08 Validation Guide

## Scope and boundaries

F08 adds credential-free parsing, validation, calibration recommendations, and deterministic summaries on merged F01R contracts. It reads saved bundles and does not launch VMs, benchmark traffic, cloud CLIs, SSH, or Terraform. Fixtures are synthetic examples of the source-documented Sockperf 3.10 and iperf3 3.17.1 native grammars; they are not experiment evidence. The Thesis source at `/Users/katya/Desktop/University/Masters/Thesis` is read-only.

Run from `/private/tmp/cnb-f08` using `PYTHONPATH=src`, `THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis`, and `/private/tmp/cnb-offline-venv/bin/python`. Every Spec Kit command must receive explicit `GIT_BRANCH_NAME=codex/f08-v2-validation` and `SPECIFY_FEATURE_DIRECTORY=specs/010-v2-native-validation`.

## Offline checks

Prerequisite/artifact gate:

```sh
GIT_BRANCH_NAME=codex/f08-v2-validation SPECIFY_FEATURE_DIRECTORY=specs/010-v2-native-validation .specify/scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks
```

Focused F08 tests:

```sh
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/unit/test_native.py tests/unit/test_validation.py tests/unit/test_calibration.py tests/unit/test_summaries.py tests/contract/test_native_validation_interface.py tests/integration/test_native_validation_replay.py
```

Full credential-free regression and whitespace check:

```sh
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q
git diff --check
```

An exit status of zero means that command passed. A failed test or unavailable external paid/cloud stage must not be relabeled as passed. The gate covers parser/statistic boundaries, four mode shapes, evidence integrity, conservative timing bounds, status separation, comparison, calibration, summary determinism, and unchanged native source bytes.

## Evidence and downstream use

F09 supplies the existing `ValidatorInput`, calls `validate_bundle` with a recorded timezone-aware evaluation time, and uses copied results from `evaluate_comparison` for complete matched peers. F12 reads exposed native samples and receiver intervals to recompute statistics. F08 returns values and never writes validation output files. No F03-local sidecar or shared F01R schema change is needed.

The final offline system readiness gate is F10. F08 does not prove actual cloud permissions, source-build/runtime capability, real expiry/retrieval/cleanup, paid acceptance, or thesis interpretation. Those remain separate authorized stages.

## Execution record

Completed F08 offline commands, return codes, tested implementation commit, and final feature commit are recorded in [`docs/f08-offline-validation.md`](../../docs/f08-offline-validation.md). The worktree branch is `codex/f08-v2-validation`, based on merged F01R `cef838e42fc620f90b5943f5a8d1528ddc0b0724`.
