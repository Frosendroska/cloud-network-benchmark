# F03 measurement runner contract for F09

Status: planned F03 public interface; implemented/tested only at the later implementation gate.
Dependency: merged F01R only. Import this interface only after F03 merges. F08 is independent and consumes existing F01R evidence/native formats, never this Python runner or an unmerged sidecar.

## Entry point and ownership

`cloud_network_benchmark.measurement.run_measurement(observation: ResolvedObservation, context: MeasurementContext, *, processes: MeasurementProcesses, clock: MeasurementClock) -> MeasurementResult`

The function runs locally on A. `MeasurementContext` is a small frozen F03 value containing the runtime fields in [data-model.md](../data-model.md): identity, private B IPv4, artifact root, absolute monotonic deadline, reserved shutdown grace, clock mappings and tool identities. No secrets or cloud credentials belong in it. The caller must provide a validated prepared pair and ensure the resolved plan is admitted and authorized. This module does not implement remote transport/deployment, readiness/stabilization, diagnostics, validation, retrieval, sealing or cloud cleanup. F09 owns those compositions, including preserved primary failure if later retrieval/cleanup fails. No new CLI or SSH wrapper is required in F03.

The returned `MeasurementResult` contains executed/unexecuted IDs, execution outcome, original native artifact references and existing command/timing/failure models. Validation remains pending. Do not mutate observation/identity/shared models or claim B server logs were produced by A.

## Minimal local process and clock boundary

Owned in `measurement_process.py`; preserve shared `CommandRunner` unchanged.

- `MeasurementClock.monotonic() -> float`, `utc_now() -> aware datetime`, and `wait_until(deadline_monotonic)`: production uses OS clocks; fakes advance instantly. UTC jumps do not extend monotonic waits. A clock read alone supplies no synchronization error estimate.
- `MeasurementProcesses.start(request: CommandRequest, *, stdout_path: Path, stderr_path: Path) -> handle`: launch shell-free argv locally with exclusive output files; stream and drain both channels. No tool/network process is launched by tests.
- `next_event(handles, deadline_monotonic) -> event or None`: bounded wait for output fragment/exit; event identifies handle, channel and receipt monotonic/UTC time. Preserve fragments in files and use only a bounded detector tail. `None` means the supplied event deadline expired, not success.
- `poll(handle) -> CommandResult or None`: completed result or still running. Actual process times/outcome match F01R validation; stream previews are bounded and durable views redacted.
- `stop(handle, deadline_monotonic) -> CommandResult`: bounded terminate, kill if needed, drain, close and reap owned clients. Deadline is never renewed; reserve shutdown grace within the original deadline. Preserve stop errors beside the primary failure. B servers are not owned handles.

Handle/event details are F03-local implementation values, not persisted shared contracts. Pure timing projection and argv construction may be ordinary helper functions in `measurement.py`; avoid a generic orchestration abstraction. No sidecar is required.

## Exact client argv

Placeholders in this document denote runtime values and are not shell text. Use separate argv elements; no redirection tokens, shell scripts, `-R`, `--bidir`, `--tcp` for Sockperf, rate cap for TCP, custom socket buffers, or changed tool flags.

Sockperf common argv:

```text
sockperf under-load -i B_PRIVATE -p 11111 -m 64 --mps 1000 --reply-every 1 --full-rtt --no-rdtsc --full-log CSV_PATH -t PROBE_SECONDS
```

| Mode/unit | PROBE_SECONDS | TCP streams | TCP measured seconds |
| --- | --- | --- | --- |
| Short idle | 65 | none | none |
| Short single / aggregate | 70 | 1 / frozen N | 60 |
| Acceptance idle | 15 | none | none |
| Acceptance single / aggregate | 20 | 1 / 4 pending, otherwise frozen N | 10 |
| Calibration trial | 70 | supplied canonical 1/4/2/8 order | 60 |
| Diagnostic chunk | <=300, final may be shorter | one existing parent TCP only | parent 86400 |

Short/acceptance/calibration TCP:

```text
iperf3 -c B_PRIVATE -p 5201 -P STREAMS -t MEASURED_SECONDS -O 5 -i 1 -l 131072 -C cubic -Z -J --get-server-output
```

Diagnostic TCP:

```text
iperf3 -c B_PRIVATE -p 5201 -P FROZEN_N -t 86400 -O 5 -i 1 -l 131072 -C cubic -Z --json-stream --forceflush
```

Persistent B server output is prepared by the caller at `ArtifactLayout.iperf3_server_log`/`iperf3_server_error`, Sockperf server log likewise. These are source native files, not missing outputs F03 can replace with sender metrics. Preserve the exact client request as `CommandEvidence.request` using `durable_view()`.

## Timing behavior and evidence

1. Prepare exclusive destinations and snapshot the local monotonic/UTC mapping without inventing clock accuracy. Respect the supplied observation/identity agreement and frozen N.
2. Launch the current unit/chunk's probe, preserve stdout/stderr and detect source-backed `Starting test...` notification, including split reads. Unrelated/duplicate/stale notifications cannot open the gate. Exit, cancellation and deadline preempt the wait.
3. For a loaded unit or the first day chunk, wait two monotonic seconds after notification receipt while observing probe liveness. Then launch TCP once. `CommandEvidence` stores actual process launch time. Notification receipt and launch time are observable events; neither proves measured traffic timing.
4. Use actual native timestamps/relative intervals with their justified clock mapping to locate measured windows and omitted/filtering exclusions. The short `-J` output may arrive only at exit; do not needlessly change it to streaming. The day preserves JSONL incrementally. A coarse connection timestamp is insufficient for a precise measurement-start claim; if no defensible exact anchor exists, output unavailable exact measured endpoints/overlap with reason and leave source data untouched. Still derive conservative timing bounds from the recorded process lifetime, verified actual omission, native receiver duration and observed stream/test events as specified in research.md. A bounded envelope fully covered by active probes can prove >=99% coverage without inventing an exact start. Unbounded uncertainty or unsupported tool semantics cannot establish coverage. Return any such bound/coverage notes as optional F03-local notes; F08 independently reconstructs them from existing native/shared evidence and does not require these notes.
5. For loaded units, `MeasurementTiming.measured_*` and its monotonic bounds describe the actual measured TCP interval. `tcp_started_at` is actual tool TCP start evidence when available; unavailable otherwise. `probe_test_started_at` is observed current notification receipt with uncertainty retained. For idle/chunks, measured bounds describe actual retained probe acquisition, not process lifetime. `overlap_seconds` contains the union of active measured probe windows intersecting TCP after exclusions/gaps, or unavailable. No CSV-count-derived time/loss. Native sample filtering alone must not be reclassified as packet loss.
6. Fill existing timing fields conservatively. Where receipt latency makes a nominal chronology invalid, retain source in logs/command evidence and mark the affected field unavailable with reason; do not reorder actual timestamps or weaken the F01R validator. Day parent and chunk timings share deployment identity and distinguish initial five-second TCP omission from later probe acquisition gaps. Numeric policy is full requested receiver TCP duration + >=99% active RTT time coverage for loaded short/acceptance/calibration; idle full requested probe interval. Day remains full 86400 + >=99%; generation >=95% and clock <=100 ms remain separate F08 rules.
7. Ten-second pauses follow completion/closed files between short units/trials only, not before first/after last. Refuse a next unit when requested duration plus known protocol overhead and reserved shutdown grace cannot fit. No resumed unit or timeout may extend the absolute deadline.

Unknown native timing may leave an otherwise exited-successfully command unvalidated. This is honest execution evidence, not an implementation test pass or comparison permission. F08 independently validates receiver duration, clocks, settings, sample/generation evidence, overlap and status from existing shared models/native files.

## Diagnostic rotation and failures

One `diagnostic-day` TCP handle remains active across all chunk transitions. The first probe notification gates TCP. Subsequent chunk notifications describe only their probe acquisitions and never trigger another TCP launch or warm-up. Use increasing `probe-chunk-NNNNNN` IDs, parent `diagnostic-day`, existing `PhaseConfiguration(kind='probe_chunk', measured_seconds<=300)`, and the dynamic `ArtifactLayout.day_probe_*_template` paths. The number of chunks depends on actual startup/filtering/rotation gaps; 288 is not proof of full coverage. Final request fits remaining measurement time and <=300-second acquisition bound. Record actual wall/process overhead separately; it is never measured coverage.

A day probe exit/failure closes that chunk, records failure/gap, and may start the next uniquely identified chunk within the original deadline while TCP continues. TCP failure, cancellation or deadline ends the whole run and boundedly stops active probes. No failed invocation is overwritten/retried; no automatic repeat of day or calibration; no idle/inflation outputs for either mode.

Short/trial failure stops later units and closes/drains owned clients. Primary failure remains first, with termination/file-write errors appended. Early process success with insufficient/unavailable measurement remains evidence for validation; it cannot be promoted to data/comparison success. Do not delete partial native files. Return enough references for F09 retrieval and cleanup on every terminal path, including exceptions while starting, reading, writing or reaping.

## Artifact projection

Use `ArtifactLayout.unit_paths(unit_id)` and `day_iperf3_client`/`day_probe_*_template` exactly. F01R declares canonical command/timing artifacts as controller-produced: the A-local runner returns typed evidence in MeasurementResult; F09 serializes `commands.json` as a JSON array of F01R `CommandEvidence` and `timing.json` as an array of F01R `MeasurementTiming`, including parent/chunk entries as applicable. Do not change artifact producers or require an additional acquisition sidecar. F03 tests emulate this caller projection to prove compatibility. Short `iperf3.json` and day client/server JSONL remain native tool output. F01R also declares aggregate `stdout.log`/`stderr.log` controller-produced; F09 projects the returned local log references into those files, preserving complete probe output and client stderr; additional distinct tool stderr paths may be local F03 artifact references where needed to preserve attribution without changing native bytes. The required day chunk `.log` retains probe stdout/stderr with attribution. Avoid interleaving labels into native JSON/JSONL or CSV.

Files are local and closed before return; manifest/global snapshot/checksum sealing/retrieval is upstream. `ArtifactInput` references do not certify a copied file's size/hash. Preserve native reported dropped/duplicate/reordered counts in full logs for F08, without CSV-derived loss. Do not commit large raw bundles or secret-bearing logs. Durable evidence redacts configured secrets; no credential material is supplied to benchmark commands.
