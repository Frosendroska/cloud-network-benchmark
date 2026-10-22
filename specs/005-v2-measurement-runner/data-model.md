# F03 data model

Reuse F01R frozen models and their validation; do not add a shared JSON schema. Public F03 values below can be ordinary frozen dataclasses in `measurement.py`. The process handle remains private.

## Runtime context

- `ResolvedObservation`: existing validated mode/units/settings/provider/artifact layout. Do not synthesize or reorder units. Research/day require frozen common N; acceptance may use canonical pending-calibration N=4.
- `IdentityContext`: existing experiment/config/campaign/observation/run/deployment/provider identities. Config/experiment/provider must agree with the resolved observation. Every unit shares the supplied deployment.
- `b_private_ipv4`: required IPv4 traffic destination; reject public, multicast, unspecified or loopback values. Do not substitute management addresses.
- `root`: existing provider-attempt artifact root on A; all normalized relative paths use `resolve_artifact_path`, reject symlink escapes and existing destinations.
- `deadline_monotonic`: finite future absolute deadline, supplied by caller; never renewed per unit. `shutdown_grace_seconds`: finite positive budget reserved inside the same deadline, not a second lifetime timer.
- `clock_mappings`: existing `ClockMapping` values with actual VM role/source/error; unavailable estimates remain unavailable. Monotonic values from different machines must not be directly compared.
- `tool_identity`: per-tool binary/source `Evidence[str]` supplied by preparation. Missing identity remains unavailable, not a guessed version. Capability/prepared-pair authorization remains the caller's responsibility.

## Local process record

Existing `CommandRequest` carries argv/cwd/remaining timeout/redactions; process output routes are explicit local file paths. Existing `CommandResult` records actual process outcome and UTC start/finish, actual elapsed duration, exit code and bounded stdout/stderr previews. The original files remain complete and unchanged. Large streams must not be read back into memory merely to fill previews.

A local event record contains action ID, current process identity, output channel, bounded text fragment and receipt monotonic/UTC mapping. It exists to handle notification fragments and exit. It is not a new required native artifact format. The complete notification remains in original logs, with its observed receipt time in `probe_test_started_at` and its uncertainty explained in timing/command evidence; actual native acquisition timing is separate.

## Per-unit evidence

- `CommandEvidence`: schema_version=2, durable redacted request/result, producer `vm_a`, deployment and unit identity, source/binary evidence and actual process monotonic/UTC bounds.
- `MeasurementTiming`: unit/deployment identity; `probe_test_started_at` is observed notification receipt; `tcp_started_at` is actual TCP start evidence when available, not inferred process launch. `measured_started_at`/`measured_finished_at` describe the actual measured TCP interval for loaded units and actual retained measurement interval for idle/chunks. `monotonic_started`/`monotonic_finished` describe the same interval on its identified clock. `omitted_seconds=5` for TCP/initial idle measurement; subsequent day probe chunks introduce no new study warm-up (`0`). Tool filtering is additionally excluded.
- `overlap_seconds`: union of actual active probe intervals intersected with the unit's measured TCP interval, after exclusions/gaps. Idle has no TCP overlap and uses unavailable reason `idle unit has no TCP workload`; unknown boundaries/mappings use an explanatory unavailable reason.
- `gaps`: actual missing acquisition intervals with UTC bounds and reason, only when endpoints are known. Unknown endpoints remain explicit failure/unavailable evidence rather than fabricated zero-length gaps.
- `clock_mappings`: retain actual mappings; error >100 ms or unknown error never becomes eligible for aligned one-second plots through F03.

F03 must not put process timestamps into a measured field to satisfy chronology validation. When the native source lacks a defensible start/end mapping, that measured field is unavailable; the process times and native relative intervals remain preserved for F08. No sentinel zero values.

## Measurement result

`MeasurementResult` groups the identity, executed unit IDs, unexecuted unit IDs, `CommandEvidence` list, `MeasurementTiming` list, closed-artifact references, execution outcome, and ordered `FailureEvidence` list (primary first). Optional local timing notes may describe conservative start/end bounds and minimum time coverage, reconstructed from native/process evidence per research.md. They are neither a required persisted schema nor substitutes for exact MeasurementTiming fields. No data-validity/comparison eligibility is computed; F09 leaves F01R validation pending until F08 runs. Artifact references use existing `ArtifactInput`/paths, without pretending files have been retrieved or sealed. Canonical controller-produced commands/timing and aggregate stdout/stderr artifacts are projected later by F09 from this returned evidence and local log references; do not relabel F01R producers.

## States and relationships

Per short/trial unit: prepared → probe started → notification received → lead elapsed → TCP started (loaded only) → clients finished → evidence closed. Failure/cancellation/deadline from any active state enters bounded shutdown, closes files and returns partial evidence. Only successful prior units may precede the next phase/trial pause.

Day: probe started → notification → two-second lead → single TCP started → rotating probes while TCP active → TCP finished/failed → bounded client shutdown/files closed. Each generated `probe-chunk-NNNNNN` is an existing F01R `PhaseConfiguration(kind='probe_chunk', parent_unit_id='diagnostic-day', measured_seconds<=300)`; same deployment, unique ID, no new experiment/reservation. At most one probe client at a time; gaps between chunks are real gaps. Do not replace the resolved continuous-day parent or its requested 86400 duration with reporting bins.
