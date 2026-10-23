# Data model

These are proposed contracts, not implemented values. Reuse existing strict Evidence, ArtifactRecord, CommandEvidence and ClockMapping contracts. Add no top-level campaign subsystem.

## AcquisitionSource (public value)

One source reference contains:

| Field | Meaning and constraint |
| --- | --- |
| tool | `sockperf` or `iperf3` |
| semantics_id | `sockperf-3.10-enabled-v1` or `iperf-3.17.1-stats-v1`; other values are unsupported, never guessed |
| producer | `vm_a` or `vm_b` |
| command_action_id | Existing producing command action ID; persistent listener ID for B |
| invocation_id | Nonempty safe identifier echoed by the tool from the producer's non-measurement environment; must match the producing action ID |
| session_id | iperf native cookie for a particular session; absent only for Sockperf |
| clock_domain | `(producer, boot_id, CLOCK_MONOTONIC)`; boot_id is the observed Linux boot UUID, never inferred from process start |
| log_artifact_id, log_path | Existing closed observation stderr ArtifactRecord identity and normalized relative path |
| native_artifact_ids | Nonempty references to original Sockperf CSV/count log or iperf native client/receiver outputs needed to corroborate this source |
| build_artifact_id | Existing metadata/build-identity artifact containing observed upstream, patch, compiler/configuration, binary and capability identity |
| mapping_indexes | Indexes of existing MeasurementTiming.clock_mappings associated with this exact domain; empty means cross-domain mapping unavailable |

All identifiers/paths use existing repository validators. References must resolve uniquely within the same child/deployment; same path with contradictory records is invalid. Never copy raw log contents, full-day interval lists or trusted overlap totals into this value.

## MeasurementTiming extension

`acquisition_sources: Evidence[list[AcquisitionSource]]` defaults to unavailable reason `acquisition observations not captured`. A populated list is nonempty. Older V2 input remains parseable with the default and retains unknown coverage. Populated references are not proof until actual bytes, commands and builds are verified. Existing measured/overlap fields remain compatible summaries; they never override the referenced sources.

Existing ClockMapping values are associated with boot/clock domain through each source's mapping_indexes. Referenced mapping VM role must match producer. Unknown estimated_error_ms or synchronization_source cannot provide a finite cross-host error bound. A captured monotonic/realtime mapping pair alone must not be labeled synchronized.

## ObservationBuild (existing bootstrap value extension)

Extend SourceBuild/BuildObservation narrowly with optional requested observation patch identity and observed observation capability/build fields, rather than another build manager. Old serialized observations remain readable with unavailable/default observation capability. New supported evidence requires identified upstream commit/archive digest, patch relative package path/digest, observation revision, compiler/configuration record, installed binary path/digest and capability format. Build cache identity includes the patch and build configuration; a matching upstream/version is insufficient. Missing patch or no installed capability cannot yield a source-backed window.

## SourceWindow (streaming helper result, not trusted manifest input)

`kind` is `probe_enabled` or `receiver_measured`. It has source reference(s) with exact byte ranges, clock domain, native session/stream identity, earliest/latest start and end in integer nanoseconds, omitted state, and an unavailable reason when not derivable. Bounds are finite, nonnegative, ordered; booleans are not numbers. Earliest <= latest for each boundary; latest_start < earliest_end is required for positive proven inner support. Zero/negative inner support contributes no time. Original iperf microseconds are retained with truncation uncertainty; conversion to nanoseconds does not improve precision.

No caller constructs SourceWindow to obtain validity. `iter_source_windows` derives it from verified raw source bytes. Receiver intervals are yielded incrementally, one session/stream at a time, without a day-sized DTO list. The consumer unions/intersects support using this same helper; omitted intervals do not become measured support. Gaps remain the complement/subtracted source-observed support, never filled by elapsed process time.

## Lifecycle and failures

Requested source/patch -> verified build and observed capability -> invocation-bound observations on VM -> files closed -> retrieval and observed hashes -> verified source references -> derivable windows or unavailable reasons. F03 can save unverified local source references, but no consumer may accept them before the existing retrieval/integrity gate. Truncated final records and missing normal termination retain earlier closed receiver intervals; they never extrapolate probe acquisition. An interrupted Sockperf invocation with no complete lifecycle record has unknown support even if CSV samples survive.
