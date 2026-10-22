# F03 offline validation guide

Run from `/private/tmp/cnb-f03`, branch `codex/f03-v2-measurement`, with merged F01R as base. Python dependencies already exist in `/private/tmp/cnb-offline-venv`; no credentials, actual tool executable, SSH, cloud API or benchmark traffic are required. Absolute Thesis context is read-only at `/Users/katya/Desktop/University/Masters/Thesis`.

## Artifact prerequisite check (planning and implementation)

```sh
GIT_BRANCH_NAME=codex/f03-v2-measurement SPECIFY_FEATURE_DIRECTORY=specs/005-v2-measurement-runner bash .specify/scripts/bash/check-prerequisites.sh --json --require-spec --require-tasks --include-tasks
```

Expected: this feature directory plus spec/plan/tasks and design artifacts. The script's `BRANCH` field derives its feature label from feature.json; `git branch --show-current` is authoritative for the actual branch.

## Focused tests (available after implementation)

```sh
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/contract/test_measurement_contract.py tests/unit/test_measurement_commands.py tests/unit/test_measurement_process.py tests/unit/test_measurement_timing.py tests/unit/test_measurement_modes.py tests/integration/test_measurement_runner.py
```

Expected: all pass; fake clocks advance complete/partial day in bounded real time. This command is a planned acceptance command; these files do not exist at planning time. Do not call an unrun command passed.

Scenarios must prove exact client argv and rejection of unsafe paths/identity/N, notification fragmentation/delay/liveness, actual versus requested/omitted timing, gaps and unavailable mappings, all short/calibration orders, day single-TCP continuity, closed materialized native files and primary/secondary failures. Full duration and coverage policy cases include 99% exactly, just below 99%, shortened receiver duration, duplicate intervals, exact endpoints unavailable but a fully covered conservative envelope, insufficient coverage at a possible envelope endpoint, contradictory bounds and unknown/unbounded timing. Generation >=95% and <=100 ms clocks remain separate evidence rules assessed by F08; F03 must never fabricate them.

## Existing regression and diff checks

```sh
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q
git diff --check
```

Expected: existing and new credential-free suites pass; no whitespace errors. These are implementation acceptance, not evidence of cloud/server/traffic/expiry behavior. No Terraform check is relevant to F03-owned files.

## Handoff to later F09

Use [contracts/measurement-runner.md](contracts/measurement-runner.md). F09 supplies the prepared/admitted pair, invokes the callable locally on A, gathers B's original receiver stream, retrieves closed files from both VMs, composes manifests and calls independently merged F08. F03 neither imports unfinished siblings nor claims copied/sealed results. A missing precise timing anchor remains unavailable through that handoff until sufficient native evidence exists.

Record actual command/exit/test counts at the implementation SHA in `docs/validation/f03-v2-measurement.md`. Real native flushing, precise tool windows, receiver streaming across persistent sessions and paid deployment behavior need separate authorized acceptance after the final offline F10 gate.
