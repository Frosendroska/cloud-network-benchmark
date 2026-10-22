# F03 fixtures

Fixtures in this directory are synthetic controller test inputs. They do not contain
real Sockperf or iperf3 captures and are not benchmark results. The implementation
base for the F03 runner is merged F01R commit `cef838e42fc620f90b5943f5a8d1528ddc0b0724`.
The local checks use replaceable fake processes and clocks only; no SSH, credentials,
cloud API or benchmark executable is invoked.

`timing-cases.json` and `day-events.json` describe timing-boundary and probe-rotation
cases. Exact offline validation commands, tested SHA and limitations are recorded in
`docs/validation/f03-v2-measurement.md`. From the repository root, the focused and full
regression commands are:

```sh
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/contract/test_measurement_contract.py tests/unit/test_measurement_commands.py tests/unit/test_measurement_process.py tests/unit/test_measurement_timing.py tests/unit/test_measurement_modes.py tests/integration/test_measurement_runner.py
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q
```
