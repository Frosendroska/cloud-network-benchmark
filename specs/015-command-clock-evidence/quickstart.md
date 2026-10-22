# Offline validation guide

Assigned cwd: /private/tmp/cnb-clock-evidence; Git branch: codex/f01r-clock-evidence. These are acceptance commands for implementation; planning has not run new feature tests.

Use login:false and the PWD assertion. Stage-script environment: GIT_BRANCH_NAME=codex/f01r-clock-evidence SPECIFY_FEATURE_DIRECTORY=specs/015-command-clock-evidence. Thesis root /Users/katya/Desktop/University/Masters/Thesis remains read-only.

## Focused checks

```sh
test "$PWD" = /private/tmp/cnb-clock-evidence && env PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/unit/test_execution.py tests/contract/test_v2_handoffs.py tests/contract/test_v2_schema_export.py
```

Expected: backward/forward/no-step, zero elapsed, aware offsets, tolerance boundary, malformed/nonfinite/partial/boolean/inconsistent cases pass. Controlled synchronous tests exercise success/nonzero/TimeoutExpired/KeyboardInterrupt with actual UTC retention, monotonic duration, signed discrepancy, outcome/partial output and redaction. Native JSON round-trips preserve producer/deployment/unit/build identity plus enclosing manifest/validator provenance. No real traffic or long waits.

## Active schemas

Regenerate after model changes, then check:

```sh
test "$PWD" = /private/tmp/cnb-clock-evidence && env PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts
test "$PWD" = /private/tmp/cnb-clock-evidence && env PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts --check
```

Expected: only child-run-manifest and validator-input snapshots differ; schema version/registry remain unchanged. Arithmetic is checked at runtime, not claimed as JSON Schema validation.

## Regression and ownership

```sh
test "$PWD" = /private/tmp/cnb-clock-evidence && env PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m pytest -q
test "$PWD" = /private/tmp/cnb-clock-evidence && git diff --check
test "$PWD" = /private/tmp/cnb-clock-evidence && git diff --name-only cef838e42fc620f90b5943f5a8d1528ddc0b0724
test "$PWD" = /private/tmp/cnb-clock-evidence && git rev-parse HEAD
```

Record actual exits/counts/tested SHA and paths in feature evidence for convergence. Confirm plan ownership; historical 001/002, research/config/provider/MeasurementTiming/ClockMapping unchanged. Artifact consistency does not prove implemented behavior; fakes never prove cloud synchronization/networking. Next after implementation/checks: speckit-converge, then coordinator integration.
