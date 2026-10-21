# F03 fixtures

Fixtures in this directory are synthetic controller test inputs. They do not contain
real Sockperf or iperf3 captures and are not benchmark results. The implementation
base for the F03 runner is merged F01R commit `cef838e42fc620f90b5943f5a8d1528ddc0b0724`.
The local checks use replaceable fake processes and clocks only; no SSH, credentials,
cloud API or benchmark executable is invoked.

`timing-cases.json` and `day-events.json` describe timing-boundary and probe-rotation
cases. The focused command is listed in
`specs/005-v2-measurement-runner/quickstart.md`; the full regression command there
must also be run before the feature handoff.
