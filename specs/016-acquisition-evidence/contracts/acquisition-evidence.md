# Acquisition evidence contract

This additive contract belongs to the serialized F01R/F02R/F03 amendment. It is a design to implement; current producers do not emit this format. Do not add fake records to old bundles and describe them as real producer output.

## Source output grammar

An observation line is UTF-8 `CNB_ACQ_V1 ` followed by one strict JSON object and LF. Native text on other stderr lines is retained. A malformed recognized-prefix line, duplicate JSON keys, unsupported format/revision, conflicting duplicate or unterminated final line is explicit missing/invalid evidence, not silently accepted. Stream parse with a bounded record-size limit sufficient for this fixed schema; never buffer a whole day. Do not treat source line receipt timestamps as event timestamps.

Common fields: format=1, tool, semantics_id, invocation_id, pid, boot_id, clock=`CLOCK_MONOTONIC`, record_kind. PID is observed nonnegative integer, not a cross-run/session ID. The producer sets `CNB_ACQUISITION_ID` to its unique action ID before launch; the hook reads it once before measurement, reports it, and never changes measurement settings. It is a non-secret provenance value, not a new tool CLI option. A missing/mismatched ID cannot be rebound by filename alone. B uses the persistent listener action ID and the native iperf cookie distinguishes each session; client cookie/native endpoint/settings evidence associates that session with one loaded unit.

Capture a pre-measurement `clock_anchor` record with the same boot/domain and a CLOCK_REALTIME read bracketed by native monotonic reads. Its elapsed bracket bounds the local mapping observation; it is not a bound on UTC synchronization error. Preserve the observed bracket and match referenced ClockMapping anchors to it. Independently supplied synchronization error/source is still required for a finite cross-host bound; absent error remains unknown. Do not perform remote synchronization or alter tool clocks.

### Sockperf enabled-v1

One normal final record has record_kind=`probe_window` and integer-native-nanosecond values `sender_enabled_ns`, `receiver_enabled_ns`, `termination_ns`, `native_filter_start_ns`, `native_filter_end_ns`, plus native termination reason/error state. These are proposed observation fields, **not existing Sockperf native CSV fields**.

- Sender/receiver enabled values are captured once on their actual loop path after the enabled guard is observed. Do not label thread creation or Starting test as acquisition.
- Termination reuses the tool's s_endTime captured before g_b_exit. Do not timestamp delayed cleanup or write from the signal handler. Retain native termination reason without adding signal behavior.
- Filter values are copied from the tool's exact calculated testStart/testEnd (first/last transmitted request and existing warm-up/cool-down); no replacement constants or rewritten filter policy.
- Use fixed race-safe observation storage, no sender/receiver handshake, blocking measured-path file write or per-packet disk logging. Export after measurement when those state values are safely available. Audit every early return, fatal receive/send/error and cleanup path; only the proven normal path yields complete support.
- Proven probe interval is the inner intersection `[max(sender_enabled,receiver_enabled,native_filter_start), min(termination,native_filter_end)]`, further restricted by applicable boundary uncertainty and consumer warm-up/measured selection. Native startup/final exclusions do not substitute for the configured five-second warm-up.
- Retain separate native total/sent/dropped/duplicate/reordered evidence. A CSV first/last pair or nonzero counts never replaces this record. Failed/unknown lifecycle remains unavailable even when useful RTT samples survive.

### iperf stats-v1

Records contain native cookie, stream socket/identity, endpoints/direction and role, plus:

- `stats_origin`: actual per-stream native start_time/start_time_fixed at init and actual start_time after omit reset, phase=`initial` or `post_omit`. A configured O=5 cannot replace the observed reset. Session cookies/streams/endpoints must match original native outputs and the intended client command.
- `receiver_interval`: existing native interval_start_time/interval_end_time in integer microseconds, omitted flag, native per-interval receiver application bytes and source stream identity, emitted only after the existing interval values are fixed. Exported values must agree with original receiver JSON/JSONL offsets, byte counts, omitted state and stream association; they do not replace those files.
- Require verified HAVE_CLOCK_GETTIME/CLOCK_MONOTONIC build semantics. Each native microsecond value t encloses true captured clock value in `[t,t+1 microsecond]`; do not round the uncertainty away. Bind native reset origin to subsequent interval offsets using the inspected algorithm, not a coarse connection timestamp or a blanket plus-five-seconds shift.

Do not add timing hooks to worker data-plane loops or alter stats counters, omit timers, callbacks, client/server synchronization or JSON. Per-interval stderr output adds unquantified observation I/O; record this build difference. Native zero-byte intervals remain legitimate measured time. Retain incomplete/interrupted receiver intervals only when they have actual closed native endpoints and corroboration; never extend to requested duration.

## Producer and replay interfaces

`bind_acquisition_sources(...)` in acquisition.py takes existing run/unit identity, command/build evidence, materialized source records, and available clock mappings. It validates associations and produces the optional public references with specific unavailable reasons. It does not assign data-valid/comparison status. F03 calls it for short phases, acceptance, retained calibration trials and each diagnostic chunk plus the continuous TCP unit.

Extend MeasurementContext with optional `build_observations: tuple[BuildObservation,...]`, `receiver_command_evidence: tuple[CommandEvidence,...]` and `source_artifact_records: tuple[ArtifactRecord,...]`, all default empty. These contain actual existing bootstrap/command/materialized records, not expected identity strings. Keep legacy tool_identity for existing evidence and never let its version regex authorize acquisition semantics. The producer records A observations locally and references the existing B stderr/native artifacts; B is closed/retrieved only after measurement through the existing lifecycle. No F03 SCP/checkpoint loop is added. Binding may therefore remain unavailable until the post-retrieval caller supplies closed verified B records to the same helper. F09 will wire merged components later; fixtures explicitly materialize that post-measurement boundary. B listener commands are persistent and can belong to the first unit; session cookies map later units without inventing extra listeners.

`iter_source_windows(child_root, source, artifact_records, commands, build_evidence, clock_mappings)` verifies closed file size/SHA-256 and safe resolution using existing ArtifactRecord before parsing, rechecks command/build/session/boot/domain linkage, and yields only source-derived SourceWindow results. Exact byte offset/length plus whole-file observed hash binds every returned boundary. Verify all consumed native/log/build artifacts, including extra count logs; no arbitrary dictionary bypass. Source-only declarations cannot be trusted, even if a caller has already populated MeasurementTiming fields.

Record formats are versioned narrowly by semantics_id. Preserve old V2 unknowns. Export affected canonical schemas using the existing schema exporter, including validator-input and nested manifest/resolved types only where model changes require them. Do not create another schema root or rewrite historical feature specs.

## Clocks and conservative overlap

Same VM/boot/CLOCK_MONOTONIC values compare directly. Cross-VM evidence requires independently bounded mappings for both domains, associated through mapping_indexes. Unknown synchronization error is not zero; UTC steps remain observed discrepancies. A/B clock mapping uncertainty adds to native precision; <=100 ms plot eligibility remains separate. Capture boot/domain identity through the source hook before measurement; do not depend on an unfinished diagnostics feature.

Probe support uses latest admissible starts/earliest ends, unioned without bridging gaps. A sufficient conservative coverage lower bound for receiver endpoints S in [s0,s1], E in [e0,e1] is `length(probe_support intersect [s1,e0], minus known gaps) / (e1-s0)` when the denominator is positive. This inner numerator/outer denominator bounds coverage for every admissible receiver window; it may conservatively undercount and must be labeled a lower bound. For multiple intervals, union inner support without double-counting and retain the enclosing continuous receiver measurement denominator, including missing periods; never normalize away gaps. Unknown bounds do not yield a numeric ratio. Select RTT samples using their recorded transmit times in that same inner temporal support; retain native reply filtering/count semantics. Bytes from receiver intervals crossing selection boundaries are not prorated. Diagnostic completeness still needs full native receiver duration and >=0.99 proven lower-bound RTT coverage; insufficient precision at the threshold remains incomplete/unknown. No interval count, rounded span or asserted overlap can override this.

## Ownership and downstream follow-up

Shared DTO/schema, observation patch/build application and F03 producer land together under this branch's serialized implementation ownership. F02R bootstrap must actually install/identify the patches on both VMs and prepare the persistent receiver with an invocation ID; otherwise F03 keeps evidence unavailable. Existing native/observation stderr files use normal local-close/retrieve/verify sequencing; dynamic day stderr declarations must be added to the existing artifact layout if absent, not folded into or overwrite CSV/stdout logs.

After this amendment merges, F08 owns minimal consumption in its native/validation code and replay tests: verified helper iteration, same support for coverage/selection, native receiver association, old-bundle unknowns, exact threshold cases, valid four-mode fixtures and eligible frozen-N peer comparison linked to prior calibration. Source-realistic fakes must be materialized through the implemented producer and format. They do not establish actual instrumented-tool behavior. No changes to quantiles, calibration, workload, scenario admission or research thresholds are needed.
