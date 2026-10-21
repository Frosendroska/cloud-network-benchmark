# F03 offline validation

Implementation commit tested: `d232dbe4b4757c7c760196fa8b2d355af00e3b17` on
`codex/f03-v2-measurement`, based on merged F01R `cef838e42fc620f90b5943f5a8d1528ddc0b0724`.
Environment: Python 3.11.13 at `/private/tmp/cnb-offline-venv/bin/python`; repository root
`/private/tmp/cnb-f03`; `PYTHONPATH=src` and read-only Thesis context at
`/Users/katya/Desktop/University/Masters/Thesis`.

## Checks

| Command | Result |
| --- | --- |
| `GIT_BRANCH_NAME=codex/f03-v2-measurement SPECIFY_FEATURE_DIRECTORY=specs/005-v2-measurement-runner bash .specify/scripts/bash/check-prerequisites.sh --json --require-spec --require-tasks --include-tasks` | Exit 0; F03 spec, plan, research, data model, contracts, quickstart and tasks found. |
| `PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/contract/test_measurement_contract.py tests/unit/test_measurement_commands.py tests/unit/test_measurement_process.py tests/unit/test_measurement_timing.py tests/unit/test_measurement_modes.py tests/integration/test_measurement_runner.py` | Exit 0; 23 passed. |
| `PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q` | Exit 0; 445 passed. |
| `PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/contract/test_schema_integrity.py tests/contract/test_v2_schema_export.py` | Exit 0; 6 passed. |
| `git diff --check` | Exit 0. |
| `git diff cef838e42fc620f90b5943f5a8d1528ddc0b0724 -- specs/001-core-offline-contracts specs/002-vm-access-bootstrap` | Exit 0; no historical-spec changes. |

## Interface and evidence boundary

F09 can call
`run_measurement(observation: ResolvedObservation, context: MeasurementContext, *, processes: MeasurementProcesses, clock: MeasurementClock) -> MeasurementResult`.
The runner returns typed F01R command, timing and failure evidence plus native artifact
references; F09 remains responsible for canonical controller projections and retrieval.
Process execution is injectable and shell-free in production. Tests replace the process and
clock boundaries, materialize only synthetic native-like files, and launch no benchmark tool.

The result preserves complete local Sockperf CSV and process logs, iperf client JSON/JSONL,
bounded/redacted durable previews, actual process timing and any observed test-start notification.
The notification is only a scheduling event. The synthetic Sockperf CSVs in this feature do not
demonstrate actual acquisition windows, and the supplied tool build's omission semantics have
not been verified. Consequently measured endpoints, overlap and coverage remain unavailable;
the runner does not promote requested duration, process sleep, CSV rows, or coarse iperf
connection timestamps into measured intervals. Pure conservative-bound and interval-coverage
helpers refuse missing/unverified inputs, but these helpers are not evidence that the runtime
currently receives defensible native acquisition windows.

No actual Sockperf/iperf3 executable, server/receiver, SSH, credential, cloud API, network
traffic, full-day wait, or paid acceptance operation was used. Real native flush behavior,
precise tool timing, verified omission/receiver behavior and receiver-side day streaming remain
separate authorized acceptance requirements. No shared schema or historical F01/F02 spec changed.
