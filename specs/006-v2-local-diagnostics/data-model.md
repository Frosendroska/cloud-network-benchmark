# F04 data model and arithmetic

Use existing shared records unchanged from `contracts/measurements.py`; no new shared schema export. All wire records use compact JSON with `allow_nan=False`. Evidence is exactly an observed value or a nonblank `unavailable_reason`; no fabricated defaults.

## Collection context (feature-local JSON input)

Required: `deployment_id` (existing identifier pattern), `vm_role` (`vm_a` or `vm_b`), `provider` (`aws`, `azure`, `gcp`), `interface` (one Linux NIC basename, no slash/traversal/control characters), `output_root` (normalized absolute guest directory with no symlink components), `duration_seconds` (finite positive number), `metadata_seed` (one validated VmMetadata-shaped object with deployment/role/provider matching context). Reject unknown context fields, invalid identities/paths and non-finite/bool durations before writes. A/B contexts have different role-specific file paths; same deployment is expected.

`metadata_seed` initially holds observed provider output/build observations or unavailable evidence. It is not a substitute for guest CPU/NIC/kernel/MTU/congestion-control reads. Build details can be held in its existing `source_builds` structured evidence; no secrets, environment dumps or credential paths are supplied. Controller and standalone guest both validate their relevant input shape; the host uses actual F01R models. The one-time duration bound starts before metadata/command acquisition, so startup consumes the same budget; baseline sampling begins after metadata collection. A normal zero-cost fake startup can therefore produce exactly 86,400 intervals in a supplied 86,400-second test, while real startup time is never silently added to the bound.

## DiagnosticSample (US1)

| Fields | Constraints and semantics |
| --- | --- |
| schema_version, deployment_id, vm_role | `schema_version is 2`; identity matches context |
| sampled_at | Observed timezone-aware UTC at snapshot acquisition; never reconstructed from a pretend wall clock |
| monotonic_seconds | `finite and nonnegative`; snapshot-local monotonic time |
| interval_seconds | `finite and strictly positive`; actual current minus previous snapshot monotonic time |
| cpu_total_percent, cpu_max_core_percent, cpu_steal_percent | `finite percentages in [0,100] or unavailable evidence` |
| tcp_retransmissions | `nonnegative integer delta or unavailable evidence`; host-wide Tcp RetransSegs, not exact path loss |
| interface_rx_drops, interface_tx_drops, interface_rx_errors, interface_tx_errors | `nonnegative integer deltas or unavailable evidence`; selected NIC counters |
| rx_bytes, tx_bytes | `nonnegative integer byte deltas or unavailable evidence`; interface bytes, not receiver application throughput |
| reset_fields | `existing Evidence field names whose values are unavailable`; only fields affected by observed counter decreases |
| gap_reason | null or nonblank human-readable reason including missed tick count/read problems; no automatic performance label |

The ten metric values are three CPU percentages, TCP retransmissions, four drop/error deltas and two byte deltas.

CPU columns are user, nice, system, idle, iowait, irq, softirq, steal. Require all eight nonnegative integers; ignore subsequent guest/guest_nice in totals because they are already accounted in user/nice. Let `D` be the sum of first-eight counter deltas, `A` the sum of user/nice/system/irq/softirq deltas. Report aggregate active CPU `100*A/D` and steal `100*delta_steal/D`. Maximum core is the maximum of the same active formula for each core in stable complete membership. Do not average per-core percentages to derive aggregate CPU. Any negative constituent or D<=0 invalidates dependent CPU evidence; mark corresponding reset_fields only on decreases. Changed/missing core membership invalidates maximum-core evidence for that interval without discarding a valid aggregate CPU reading. A decreased iowait is not proof of reboot.

Read TCP headers and corresponding values by name, not fixed column index. Reject malformed count/header pairing, negative/non-integer values and duplicate RetransSegs fields. Sysfs selected-interface counters map `rx_dropped/tx_dropped/rx_errors/tx_errors/rx_bytes/tx_bytes` directly. Current minus immediately preceding valid read gives a delta; if either read is unavailable the delta is unavailable. On decreases, no wrap or reset-to-current assumption. Rebase on the current observation so a subsequent valid interval can recover. Do not carry older valid values across missing reads.

## VmMetadata (US2)

All existing fields are required. `schema_version is 2`; deployment/role/provider match context. CPU model comes from `/proc/cpuinfo` (mixed models remain explicit); kernel from `/proc/sys/kernel/osrelease`; driver from selected NIC device/driver symlink; MTU from NIC sysfs; congestion control from `/proc/sys/net/ipv4/tcp_congestion_control`. `mtu is a positive integer or unavailable evidence`. Never replace actual non-CUBIC/non-1460 values with configured intentions.

Image/actual_shape/observed_region/observed_zone/physical_zone_id come from observed role-specific provider output, or unavailable reasons. `documented_network_limit` is copied from identified provider evidence, not a measured or guessed value. Preserve source/date/baseline/burst/aggregate/egress semantics as supplied; do not normalize limits into equivalence claims.

`tool_versions.value` is a dictionary by tool with `version` Evidence and current `binary_sha256` Evidence; `source_builds.value` is a dictionary by tool with supplied observed source/build fields, current hash and `matches_bootstrap_binary` Evidence. Keep per-tool missing/mismatch evidence visible. If no tool data exists, the outer evidence can be unavailable. Source/build input includes only nonsecret source reference/hash, compiler version, absolute binary path/version/hash. Once-per-VM guest version/hash reads must be bounded; a failure makes that fact unavailable without inventing successful identity.

`acceleration` is a truthful description of observed NIC mechanism. Record ena or gve binding without certifying optional entitlement exclusions. Azure netvsc alone is insufficient to verify SR-IOV; inspect linked lower/VF device-driver evidence or record unavailable verification. NIC driver remains separately available when acceleration verification is missing.

## ClockMapping (US3)

Use existing fields `vm_role`, `monotonic_seconds`, `utc_timestamp`, `estimated_error_ms`, `synchronization_source`. `monotonic_seconds is finite and nonnegative`; UTC is observed timezone-aware UTC; `estimated_error_ms is finite and nonnegative or unavailable evidence`.

Read-only local chrony status is bracketed by monotonic reads; separately bracket the adjacent UTC read. Mapping monotonic time is the midpoint of the UTC-read bracket. Estimated milliseconds = `1000 * (abs(system_offset) + root_dispersion + root_delay/2 + query_elapsed + utc_bracket_width/2)`. All components must be finite/nonnegative except the signed offset whose magnitude is used. Parse seconds and fast/slow direction truthfully. Require Normal leap status, valid non-local reference ID, valid UTC reference time and stratum 1..15. Unknown/unsynchronized/local mode, malformed fields, timeout or unavailable utility yields an unavailable error with reason. For a supported synchronized observation, `synchronization_source.value` starts with `chronyc tracking synchronized;` followed by the observed reference ID/time, stratum, leap status, offset, root delay/dispersion and query identity. An observed unsupported/unsynchronized status starts with `chronyc tracking unsynchronized;` with details; an absent observation is unavailable evidence. This feature-local textual convention lets the eligibility helper distinguish observed synchronization from an arbitrary nonempty string without changing StringEvidence. It never just says configured NTP.

One mapping at baseline and per completed snapshot, with no stale-success cache. Query timeout <=0.2 seconds and remaining duration; no query after deadline. A final mapping may have unknown error because no query budget remains. Missing final accuracy is honest; it cannot silently inherit prior estimates. Mappings retain UTC reversals/steps. Aligned-plot consumers must inspect interval coverage and contradictory/discontinuous mappings, not interpolate unknown accuracy.

## Local outcome and state

Standalone collector returns a JSON-safe outcome: deployment_id, vm_role, `state` (completed/interrupted/failed), sample_count, mapping_count, artifact paths, `closed_paths` and nonsecret FailureEvidence-shaped failure entries (category/message/occurred_at and optional action_id). Identity matches context; counts are nonnegative integers; closed_paths may include only actually closed artifacts. Controller adapter returns `DiagnosticCollectionResult` with matching identity, the three ArtifactInput declarations, counts, closure and FailureEvidence. It validates files by streaming, never loading a whole day into memory, and reports malformed/truncated lines without modifying them. Completed means the bounded loop ended normally, not that every metric is available: ordinary optional read/query failures remain unavailable evidence and do not fail otherwise successful collection. Fatal startup/output errors mean failed; requested stop means interrupted. Adapter parse/count/closure contradictions add failures and prevent a completed result from being presented as successfully validated.

Lifecycle: validated -> exclusive files opened -> metadata once and baseline -> scheduled append/flush -> stop/deadline/failure -> close attempts -> outcome. Existing files or escaping/symlink destinations fail before overwrite; preserve any partially created files on later errors. On full-disk/write/flush/close failure, preserve earlier lines and report failed/partial evidence; do not claim successful closed content. Signals stop the loop and attempt close. Abrupt loss cannot guarantee a final outcome or complete last line. Retrieval, materialized size/hash and global validity remain caller-owned.
