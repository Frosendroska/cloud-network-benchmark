# F03 offline validation

Implementation commit tested: `3182b2364a7d1421d6382fe90f27672f8a13b722` on
`codex/f03-v2-measurement`, based on merged F01R
`cef838e42fc620f90b5943f5a8d1528ddc0b0724`. Environment: Python 3.11.13 at
`/private/tmp/cnb-offline-venv/bin/python`, repository root `/private/tmp/cnb-f03`,
`PYTHONPATH=src`, and read-only Thesis context at
`/Users/katya/Desktop/University/Masters/Thesis`.

## Checks

| Command | Result |
| --- | --- |
| `GIT_BRANCH_NAME=codex/f03-v2-measurement SPECIFY_FEATURE_DIRECTORY=specs/005-v2-measurement-runner bash .specify/scripts/bash/check-prerequisites.sh --json --require-spec --require-tasks --include-tasks` | Exit 0; feature directory and required specification/task artifacts found. |
| `PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/contract/test_measurement_contract.py tests/unit/test_measurement_commands.py tests/unit/test_measurement_process.py tests/unit/test_measurement_timing.py tests/unit/test_measurement_modes.py tests/integration/test_measurement_runner.py` | Exit 0; 52 passed, including stale prior-process exit, simultaneous day-process completion and failed shutdown stream closure cases. |
| `PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q` | Exit 0; 474 passed. |
| `PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/contract/test_schema_integrity.py tests/contract/test_v2_schema_export.py` | Exit 0; 6 passed. |
| `git diff --check` | Exit 0; no whitespace errors. |
| `git diff cef838e42fc620f90b5943f5a8d1528ddc0b0724 -- specs/001-core-offline-contracts specs/002-vm-access-bootstrap` | Exit 0; no changes to historical F01/F02 specifications. |

## Convergence repairs and remaining contract dependency

T022–T024 are implemented and checked at `3182b23`: process events are interpreted only
for currently awaited handles; diagnostic probes that finish alongside TCP are recorded
once with their command, timing and native references; and bounded shutdown closes both
owned output streams after attempting termination, kill, reaping and drain joins. The
offline regressions exercise queued previous-unit exit events in acceptance and calibration,
current-probe early exit, simultaneous day completion, day finalization errors, and terminate,
kill and reap failures while retaining partial bytes.

T025 remains open for the coordinator's F01R clock-evidence amendment. The current shared
`CommandResult` validator in `src/cloud_network_benchmark/contracts/execution.py` requires
`duration_seconds` to equal `(finished_at - started_at).total_seconds()` and rejects
`finished_at < started_at`; `CommandEvidence` also rejects backward UTC chronology. For a
two-second monotonic process interval with a forward UTC step of one hour, the actual UTC
delta is 3,602 seconds, so the shared model cannot represent the required monotonic duration.
With the same interval and a backward step of one hour, the actual UTC finish precedes its
start and validation rejects the completion entirely. F03 did not alter these shared
contracts, synthesize timestamps, or add a permanently failing test. The production-adapter
clock-jump tests and T025 implementation await the coordinator's shared amendment.

## Interface and evidence boundary

F09 can call
`run_measurement(observation: ResolvedObservation, context: MeasurementContext, *, processes: MeasurementProcesses, clock: MeasurementClock) -> MeasurementResult`.
The result returns typed F01R command, timing and failure evidence plus references to
materialized native artifacts. F09 owns the canonical `commands.json` and `timing.json`
projection and retrieval; F03 does not import F08 or create shared-contract changes.
Production process execution is local and shell-free. Automated checks use replaceable
fake processes/clocks and temporary files only.

The inspected upstream [Sockperf 3.10 client source](https://github.com/Mellanox/sockperf/blob/3.10/src/client.cpp)
defines `[Total Run] RunTime` as `s_endTime - s_startTime` and writes it to the `--full-log`
file. F03 extracts that native summary and, when caller-supplied tool identity identifies
3.10, records a conservative monotonic bracket for the test-run start from process lifetime
and the three-decimal duration. `[Valid Duration]` records first-valid-send to last-valid-receive
span. Neither value supplies continuous acquisition intervals: the full-log CSV contains only
filtered valid sample rows. F03 therefore preserves these raw facts but leaves probe active
coverage, exact measured endpoints and overlap unavailable. Caller-supplied version identity
is not verification of the installed executable.

The inspected upstream [iperf3 3.17.1 API source](https://github.com/esnet/iperf/blob/3.17.1/src/iperf_api.c)
supports reading the requested omit from `start.test_start.omit` and receiver duration from
`server_output_json.end.sum_received.seconds`. When both process bounds and these native
values exist, and the supplied identity identifies 3.17.1, F03 records a conservative TCP
measurement-start bracket. The fake fixtures use these upstream field shapes; they do not
establish the semantics or timing behavior of a deployed build. A valid receiver duration and
Sockperf test-run bracket still do not prove active RTT coverage. Whole-second UTC anchors,
launch sleeps, requested duration and CSV row timestamps are not promoted into measured
intervals or a <=100 ms alignment claim.

No actual Sockperf/iperf3 executable, receiver, SSH, credentials, cloud API, network traffic,
full-day wait or paid acceptance operation was used. Real native output flushing, exact
filtered acquisition bounds, installed-build identity/semantics, receiver-side day streaming,
and full-duration/99% coverage remain to be checked during separately authorized acceptance.
