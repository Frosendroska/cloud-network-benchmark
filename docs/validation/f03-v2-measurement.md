# F03 offline validation

Implementation commit tested: `70eb201` on
`codex/f03-v2-measurement`, based on merged F01R
`a468bb0219ff1f61bae6cbb3577ab797bdb9763b`. Environment: Python 3.11.13 at
`/private/tmp/cnb-offline-venv/bin/python`, repository root `/private/tmp/cnb-f03`,
`PYTHONPATH=src`, and read-only Thesis context at
`/Users/katya/Desktop/University/Masters/Thesis`.

## Checks

| Command | Result |
| --- | --- |
| `GIT_BRANCH_NAME=codex/f03-v2-measurement SPECIFY_FEATURE_DIRECTORY=specs/005-v2-measurement-runner bash .specify/scripts/bash/check-prerequisites.sh --json --require-spec --require-tasks --include-tasks` | Exit 0; feature directory and required specification/task artifacts found. |
| `PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/contract/test_measurement_contract.py tests/unit/test_measurement_commands.py tests/unit/test_measurement_process.py tests/unit/test_measurement_timing.py tests/unit/test_measurement_modes.py tests/integration/test_measurement_runner.py` | Exit 0; 67 passed, including all four convergence repairs. |
| `PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q` | Exit 0; 557 passed. |
| `PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/contract/test_schema_integrity.py tests/contract/test_v2_schema_export.py` | Exit 0; 7 passed. |
| `git diff --check` | Exit 0; no whitespace errors. |
| `git diff a468bb0219ff1f61bae6cbb3577ab797bdb9763b -- specs/001-core-offline-contracts specs/002-vm-access-bootstrap` | Exit 0; no changes to historical F01/F02 specifications. |

## Convergence repairs

T022–T024 are implemented and checked at `3182b23`: process events are interpreted only
for currently awaited handles; diagnostic probes that finish alongside TCP are recorded
once with their command, timing and native references; and bounded shutdown closes both
owned output streams after attempting termination, kill, reaping and drain joins. The
offline regressions exercise queued previous-unit exit events in acceptance and calibration,
current-probe early exit, simultaneous day completion, day finalization errors, and terminate,
kill and reap failures while retaining partial bytes.

T025 is implemented at `d30d4c3` using the merged F01R `duration_basis`, monotonic endpoints,
and `utc_elapsed_discrepancy_seconds` fields. The production adapter retains actual UTC
timestamps, derives elapsed duration from the monotonic endpoints, and records the observed
UTC elapsed minus monotonic elapsed discrepancy. F03 projects those same values into
`CommandEvidence`. Fake production-adapter tests cover both one-hour clock-step directions
for successful and failed exits, plus backward-step cancellation that escalates to kill;
each result round-trips through `CommandEvidence` with matching timing fields. These checks
make no claim about clock synchronization accuracy and do not clamp or synthesize UTC values.

T026–T029 are implemented and tested at `70eb201`. Production shutdown now attempts kill and
reap after an unexpected timed-wait error, closes each owned output stream independently even
when an earlier flush/close fails, and retains shutdown errors in command evidence. Boundary
checks after the two-second lead reject a current probe exit exactly at notification plus two
seconds for short, acceptance and calibration modes. Diagnostic-day completion now fails the
overall result when a final owned probe remains unresolved after stop fails, while retaining
the completed TCP evidence and identifying unresolved shutdown. Day chunk paths are registered
before process start, then absent paths are removed if startup fails; partially created native
CSV/log files remain referenced. Fake Popen/stream and materialized-file tests cover these cases.
Terminal exit and cancellation events during the lead are both fail-stop conditions.

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

## T030 normal-finalization repair

The T030 implementation and focused/full checks were run in the worktree at base commit
`ba5b499f86508f7458de9452f8f7ddb25ec181a7`, with the T030 source and regression-test changes
present as working-tree edits. This records the exact source base plus tested edits; it is not a
claim that those edits were committed at that SHA. Python 3.11.13 and the environment above were
used.

| Command | Result |
| --- | --- |
| `PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/unit/test_measurement_process.py -k 'normal_poll_closes_stderr_and_caches_failed_result_after_stdout_close_errors'` | Exit 0; 1 passed, 17 deselected. |
| `PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/contract/test_measurement_contract.py tests/unit/test_measurement_commands.py tests/unit/test_measurement_process.py tests/unit/test_measurement_timing.py tests/unit/test_measurement_modes.py tests/integration/test_measurement_runner.py` | Exit 0; 68 passed. |
| `PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q` | Exit 0; 627 passed. |
| `PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/contract/test_schema_integrity.py tests/contract/test_v2_schema_export.py` | Exit 0; 7 passed. |
| `git diff --check` | Exit 0; no whitespace errors. |
| `git diff HEAD -- specs/001-core-offline-contracts specs/002-vm-access-bootstrap` | Exit 0; no historical F01/F02 specification changes. |

Normal `poll()` finalization now flushes and closes stdout and stderr independently, records each
failure, and retries a still-open stream once. A stream that remains open is named explicitly in
the failed command evidence; the result is cached so repeated polling cannot throw again or
duplicate command/timing evidence. The production-adapter regression uses a normal-exit fake
process whose stdout close persistently fails, and verifies stderr closure, bounded close attempts,
both streams' partial native bytes, and the cached failed result. Real subprocess, OS-level file
failure behavior, benchmark tools, receiver, cloud access and traffic remain unrun.
