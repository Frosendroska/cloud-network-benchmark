# Source-bound acquisition evidence validation

This amendment owns the serialized shared-contract/build/F03 source-observation change for
`specs/016-acquisition-evidence`. It preserves the existing measurement CLI, timing, omit,
warm-up, phase sequence, and native Sockperf/iperf output. Runtime acquisition remains unknown
until a patched, identified tool emits complete source records and the closed files pass the
existing retrieval integrity checks. The branch does not change F08 runtime or declare the
F08 eligibility/coverage gate complete.

## Upstream identity and source audit

The pinned Sockperf 3.10 annotated tag object
`396f9daa9e9e4991dd3661ff294ce89996d92b41` resolves to unsigned commit
`5ebd327da983225321818c0355db922515e026bd`. The inspected `src/client.cpp` SHA-256 is
`a79a3b29dfad3f7c5adf8f93f770598d6cd6d380889c00d63a179f127534245f`; related unmodified
source file hashes and references are in `specs/016-acquisition-evidence/research.md`.
Sockperf `COPYING` carries the Mellanox three-condition BSD-style notice; the patch changes
only `src/client.cpp` and does not copy upstream source into fixtures.

The pinned iperf3 3.17.1 annotated tag object
`6326580298857302356a615bf31d7491f8046469` resolves to unsigned commit
`2acfcfe94e928e74542c9f107e02aa6dd4748a79`. Inspected source SHA-256 values are:

| File | SHA-256 |
| --- | --- |
| `src/iperf_api.c` | `960341011c83b3990de0e431d082001d5126b05390cacdca23b23b6731e577fb` |
| `src/iperf_server_api.c` | `12a19ecf2dc20d500dfb60af740d9c5a96b562c53552c5cc65af41f7a31646e5` |
| `src/iperf_client_api.c` | `dfd7a23abc9be5d644bed38c756037024f31625da033a3e8cde35965a1b3a72b` |
| `src/iperf_time.c` | `f7d52905eb00bb196f0930f50709dd872d7521d089feb9a0e28bdbeacc717a54` |

The iperf license is retained with its upstream distribution. Its patch changes only
`src/iperf_api.c` and adds `src/cnb_acquisition.h`.

The reviewed Sockperf control flow exits before measurement on setup, network, affinity,
thread-start, or early interrupt failures; the new normal-window record is emitted only after
the existing statistics/file cleanup path, and requires the timer signal, both loop-entry
timestamps, and native filter endpoints. Atomic entry capture occurs once at sender and
receiver loop entry, not for each send, receive, or probe. Native time-filter bounds are read
from the existing time-based statistics filter. The observation anchor is taken after existing
warm-up and before `Starting test...`; it does not adjust the test timer.

The iperf hook exports existing native values after initialization/reset and after receiver
interval statistics are populated. It is server-role TCP only, carries the native cookie,
stream socket identity and endpoints, and writes observation records to stderr. Existing JSON,
JSONL, counters, protocol flow, and interval callbacks remain in place. Records are absent on
setup/early failure paths that never reach the native callbacks. An absent ending interval is
not extrapolated.

Exact patch digests packaged for the pinned commits:

| Patch | SHA-256 |
| --- | --- |
| `sockperf-3.10-acquisition.patch` | `3f0bb59a3c70b247e5ebde77254e1427c35dc6ec39e2a78a3c779cff7015a285` |
| `iperf-3.17.1-acquisition.patch` | `2619b1070eabbef72dd648372f7eecec2140d08f87e5a237685d98c5802f49df` |

The source archives were fetched from the official GitHub repository archive endpoints at the
pinned commits. Their downloaded gzip bytes had SHA-256
`a35a1daf310551b9f675af4fb7d95a6a10389fda4c7605d70a21554be0773f8e` (Sockperf) and
`d6b4d2c7b20157c5b7fbd2de66f22bd3428958b5f2e28899151b6e22745886fe` (iperf3). The extracted
source trees were retained under `/private/tmp/cnb-acq-sources` for patch application checks;
the downloaded archive files were not retained. Unsigned Git tags and archive checksums establish
content identity, not publisher signatures.

## Conservative evidence limits

`CNB_ACQ_V1 ` records belong to the source instrumentation. Ordinary stderr notices are retained
as bytes and ignored by the parser. Recognized malformed or truncated records fail replay;
records are byte-bounded and streamed. Sockperf support is the intersection of sender/receiver
loop enablement, native filter bounds, and normal timer termination. It does not mean continuous
CPU scheduling, every-send success, cadence, or delivery. iperf intervals use the native receiver
clock and whole-microsecond truncation bounds of `[t, t + 1000 ns]`; a zero-byte interval remains
valid duration evidence, while omitted intervals do not support measurement.

Cross-host windows remain incomparable unless trusted existing clock evidence establishes the
same boot/domain and bounded mapping. A clock anchor is retained as evidence but is not, alone,
proof of synchronized clocks. Missing mapping uncertainty stays unknown. The lower-bound helper
uses inner probe support against the outer receiver duration, preserves disjoint gaps, and returns
unknown for different producers/boots/domains. The existing RTT plot tolerance remains a separate
100 ms criterion. The established generation threshold stays independent.

Old build records remain parseable and unavailable for acquisition capability. New patch identity
must be supplied with the pinned upstream commit; cache identity includes the patch digest and an
unpatched stale cache is rejected. The installed binary is checked for the `CNB_ACQ_V1` marker.
The bootstrap source patch is applied with `patch --batch --fuzz=0 -p1`. The existing tool argv is
unchanged. The iperf persistent listener receives the non-secret action ID in
`CNB_ACQUISITION_ID`; no cookie or raw output is copied into durable command evidence.

## Validation record

Environment: macOS Darwin arm64 with Python 3.11 in `/private/tmp/cnb-offline-venv`. The current
offline toolchain is not a supported Linux guest build environment; no Linux compilation, VM,
cloud, SSH, benchmark traffic, or credential use was performed. Instrumented executable behavior
therefore remains unproved pending the approved Linux build check.

Patch-application command, run from the feature worktree against clean extracted snapshots:

```text
patch --batch --fuzz=0 -p1 -d /private/tmp/cnb-acq-final-source-check/sockperf < src/cloud_network_benchmark/tool_patches/sockperf-3.10-acquisition.patch
exit 0
patch --batch --fuzz=0 -p1 -d /private/tmp/cnb-acq-final-source-check/iperf < src/cloud_network_benchmark/tool_patches/iperf-3.17.1-acquisition.patch
exit 0
```

Static source review confirmed the Sockperf patch touches only `src/client.cpp`, and the iperf3
patch touches `src/iperf_api.c` plus its new observation header. No timer, rate, omit, protocol,
command-line, native file-format, or measured-duration settings were changed.

The fake-process integration exercises command-bound environment IDs and observation file paths.
It does not impersonate the VM B persistent receiver, a Linux kernel clock, a patched tool binary,
or complete four-mode source output. Acquisition references are only accepted after materialized
bytes verify against closed ArtifactRecords and exact command/build/session identities. B-side
iperf records still require the existing post-measurement retrieval path before replay.

## Follow-up for the serialized downstream consumer

After this branch converges and merges, F08 should call the shared replay helper for both loaded
RTT sample selection and source-supported coverage, retain native receiver duration and the
existing 99% diagnostic-day rule, and keep generation checks independent. It must complete valid
four-mode and eligible prior-calibration peer replay, preserve old bundles as unknown, and leave
paid acceptance, actual tool output, guest clock mapping, disk overhead, and exported bundle checks
as separate gates. Do not use this change to consume or edit an unmerged F08 runtime.

## Recorded checks and task coverage

The completed and tested source tree is committed as
`c2acff441a763db96218a34c010a737b858a7152`, based on verified main
`e03a07eaa5ed9ea372c695db19fd6fbcffdad024`. The focused command
`env PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/integration/test_acquisition_producer.py tests/unit/test_measurement_timing.py tests/unit/test_acquisition.py tests/contract/test_acquisition_contract.py`
exited 0 with **53 passed in 23.68s** after all-mode replay, streaming lower-bound assembly,
and gap-boundary tests were added. The full offline command
`env PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q`
exited 0 with **816 passed in 76.83s**. The canonical schema check
`env PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts --check`
and `git diff --check` both exited 0. The pinned source patch application was separately rerun
against fresh exact extracts; both commands exited 0. No supported Linux compilation was
available, so compile remains **not run**.

All 20 tasks are checked. The four-mode fake process exercise traverses F03 commands through the
F01R binder and replay helper, keeps the 100 ms diagnostics alignment test separate, rejects
cross-host windows without a bounded common transform, preserves gap duration in the receiver
denominator, and checks repeated replay hashes plus a generated 50,000-interval stream. The final
day chunk without positive post-warm-up support remains unknown. Compilation is still not run.

## Serialized convergence repair (T021–T025)

The coordinator appended five trust-boundary tasks. The feature owner completed them in two code
commits: `f29156c4c528034a2b8cbd6e073c5bc62b8747a2` and
`f92232ed0f57f606d8bbeb35552e616155f4eda1`. The branch already contained verified `origin/main`
at `e03a07eaa5ed9ea372c695db19fd6fbcffdad024`; a normal merge check returned “Already up to date.”

Bootstrap now emits an immutable `CNB_BUILD_V1` sidecar with source/archive, upstream, patch,
compiler, build configuration, installed executable path/version/digest, capability and semantics.
The exact sidecar stdout bytes are materialized under the attempt's `metadata/build/` directory as
a closed, hashed `build-identity` ArtifactRecord. F03 returns those records and requires their
verified bytes to match the captured BuildObservation as well as the producing command path and
environment. A legacy build with no captured sidecar remains unavailable. The normal iperf
receiver hook records protocol/settings and initial/post-omit origins. Replay now corroborates
receiver intervals against native client JSON/JSONL session settings, server-receiver stream rows,
endpoints, offsets, omission and bytes; cookie-only files and self-consistent substituted logs do
not authorize windows. Failed producer outcomes or malformed tails retain already closed windows
and report an unknown tail per source. Other units' valid sources remain independent. The coverage
numerator intersects probe inner support with the union of receiver inner support while retaining
the full continuous receiver envelope in the denominator.

Validation on the final code before documentation updates:

```text
env PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis \
  /private/tmp/cnb-offline-venv/bin/python -m pytest -q
exit 0: 824 passed in 63.24s

env PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis \
  /private/tmp/cnb-offline-venv/bin/python -m pytest \
  tests/unit/test_acquisition.py tests/unit/test_measurement_timing.py \
  tests/integration/test_acquisition_producer.py tests/unit/test_bootstrap.py \
  tests/unit/test_measurement_commands.py tests/unit/test_measurement_modes.py \
  tests/integration/test_measurement_runner.py -q
exit 0: 114 passed in 28.50s

env PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python \
  -m cloud_network_benchmark.contracts.schema \
  --output specs/003-v2-shared-contracts/contracts --check
exit 0

git diff --check
exit 0
```

The updated iperf patch SHA-256 is
`11b96daadb4a1f17dc24376bb3d0d7feec330efeedc550b1979b1566e9b81661`; Sockperf remains
`3f0bb59a3c70b247e5ebde77254e1427c35dc6ec39e2a78a3c779cff7015a285`. Both actual patch files
applied with `--batch --fuzz=0` to clean extracted pinned-source copies. The iperf files matched
the research hashes before application: `src/iperf_api.c`
`960341011c83b3990de0e431d082001d5126b05390cacdca23b23b6731e577fb`,
`src/iperf_server_api.c` `12a19ecf2dc20d500dfb60af740d9c5a96b562c53552c5cc65af41f7a31646e5`,
`src/iperf_client_api.c` `dfd7a23abc9be5d644bed38c756037024f31625da033a3e8cde35965a1b3a72b`, and
`src/iperf_time.c` `f7d52905eb00bb196f0930f50709dd872d7521d089feb9a0e28bdbeacc717a54`. Sockperf
`src/client.cpp` matched research hash
`a79a3b29dfad3f7c5adf8f93f770598d6cd6d380889c00d63a179f127534245f`.

The source application commands returned 0:

```text
patch --batch --fuzz=0 -p1 -d /private/tmp/cnb-acquisition-repair2-source-check/iperf \
  < src/cloud_network_benchmark/tool_patches/iperf-3.17.1-acquisition.patch
patch --batch --fuzz=0 -p1 -d /private/tmp/cnb-acquisition-repair2-source-check/sockperf \
  < src/cloud_network_benchmark/tool_patches/sockperf-3.10-acquisition.patch
```

These checks establish source-context applicability and offline fake-boundary behavior only. A
Linux compile, installed tool help check, patched executable run, guest clock verification, cloud,
SSH, benchmark traffic, and credential use were not run. The retrieval integration fixture now
matches exact unit command IDs and artifact paths so similarly prefixed actions cannot be mistaken
for the producing client; this fixture repair stays inside the assigned feature test harness.
Convergence task markers T021–T025 are checked, for 25 total feature tasks.
