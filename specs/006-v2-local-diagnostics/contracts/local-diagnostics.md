# F04 local diagnostics interfaces

Version: 2026-10-22. Shared baseline `7a70a3139c5ee9efa1f8ea13c3f6de40e9048121`. These feature-local APIs do not amend shared contracts.

## Controller adapter: diagnostics.py

| Interface | Contract |
| --- | --- |
| `build_collection_context(deployment, *, role, interface, output_root, duration_seconds, prepared=None)` | ProviderDeploymentOutput plus optional matching BootstrapResult -> JSON-safe context per data-model.md. Validate role/deployment/identity; provider fields are observed, not config intent. Absent build evidence is unavailable. Reject mismatched prepared identity/deployment and duplicate role/tool build entries. |
| `load_collection_result(context, outcome)` | Validate guest outcome identity/counts/closure and stream existing local files using DiagnosticSample, VmMetadata, ClockMapping. Return DiagnosticCollectionResult; malformed/missing/truncated records become explicit failures, not passed validation. In offline tests output_root is the materialized fixture root; F09 maps retrieved files to its child root later. |
| `iter_samples(path)` / `iter_clock_mappings(path)` | Streaming typed records with parse/validation errors identifying line/path; do not rewrite bytes or accumulate lists. |
| `alignment_eligibility(mapping_a, mapping_b)` | `(bool, list[str])`; require vm_a/vm_b roles, observed synchronized source (the `chronyc tracking synchronized;` prefix specified in data-model.md) and finite nonnegative errors <=100 ms on both. Unknown/unsynchronized sources cannot pass even with a numeric value. This function checks mapping-pair clock evidence only; interval coverage/discontinuity policy belongs to consuming integration. |

`DiagnosticCollectionResult` is feature-local: deployment_id, vm_role, state, sample_count, mapping_count, artifacts, closed_paths, failures. It must not carry a verified/retrieved success flag. Metadata remains a typed single record; histories are streamed. Only the ten required metrics are collected; source identities and clock data are additional evidence, not new metrics.

## Standalone guest: diagnostics_guest.py

`collect_local(context, *, root=Path('/'), clock=None, run_command=None, stop_requested=None)` returns the JSON-safe local outcome. `root` redirects absolute proc/sys paths for fixture reads, never changes output_root semantics. `clock` supplies monotonic(), utc_now(), wait_until(deadline); tests inject it. `run_command` takes argv plus a positive bounded timeout and returns captured exit/stdout/stderr without a shell. Production defaults read only local files and execute bounded allowlisted local observation commands. Missing utilities/read permissions are evidence, not reasons to stop all counters. stop_requested is a callback; command-line SIGTERM/KeyboardInterrupt sets it. Inputs are validated before writes.

`read_linux_snapshot(interface, *, root=Path('/'))` and `read_static_metadata(context, *, root=Path('/'), run_command=...)` are ordinary functions reused by collector tests. Parsing and counter arithmetic live here once. Rooted fixture reads must never fall through to the host's real proc/sys paths; absolute fixture symlink targets are interpreted within the supplied fixture root for any subsequent read. No package imports or Pydantic are required when the module file is executed directly.

Guest invocation after F09 stages the file and context:

```text
python3 /<deployment-workspace>/diagnostics_guest.py --context /<deployment-workspace>/diagnostics-context-vm_a.json
```

The actual paths are supplied by F09; this notation declares argv, not an executable example. Guest main prints its small final outcome JSON to stdout, preserves error/exit evidence, and uses exit 0 for completed collection, 130 for interrupted and nonzero for failed collection. No stdout contains the diagnostic history or credentials. `--help` is safe without Linux files or measurement.

No host monotonic deadline is portable to another VM. F09 passes a bounded remaining duration, and guest computes its own one-time monotonic deadline. No repeated deadline extension or catch-up burst. Sampling continues across caller phases and local Sockperf chunk rotation because the collector owns neither.

## Artifacts and closure

| ID | Path relative to child/guest output root | Kind | Producer | Expectation |
| --- | --- | --- | --- | --- |
| vm_a_metadata / vm_b_metadata | metadata/vm_a.json / metadata/vm_b.json | vm_metadata | matching role | required |
| vm_a_diagnostics / vm_b_diagnostics | diagnostics/vm_a.jsonl / diagnostics/vm_b.jsonl | diagnostics | matching role | required |
| vm_a_clock / vm_b_clock | diagnostics/vm_a-clock.jsonl / diagnostics/vm_b-clock.jsonl | clock_mapping | matching role | required for started collector |

Each collector returns only its own three declarations, all `unit_id=None`. Canonical metadata/diagnostic IDs/paths come from ArtifactLayout; clock files are explicitly local additions under ordinary ArtifactInput. The existing declarations are deduplicated by F09, not emitted twice in its inventory. Every complete diagnostic line validates as shared DiagnosticSample schema_version=2, metadata as VmMetadata schema_version=2, and clock line as shared ClockMapping. Keep no ad hoc fields in shared records.

Create all output files exclusively beneath validated output_root, reject path/symlink escape and collisions, never truncate/append existing evidence, and stream compact JSON/JSONL with complete-line flushes. Do not publish ArtifactRecord(retrieval_state='verified'); F02R/F09 certify observed hashes after retrieval. A closed file can still be incomplete/invalid; closure and collection state remain separate from completeness.

Partial startup/write/close/interruption retains existing complete lines and explicit failure/closure state. Runtime read gaps do not fail unrelated fields. A malformed final line from abrupt loss remains intact and is reported by the adapter. No automatic retry, old-file resume, cloud cleanup or remote copy is added.

## Acceptance boundary

Focused tests must inspect exact local command argv/timeouts, real fixture reads, materialized bytes, unchanged shared model/schema validation and observed metadata. Fakes do not prove Linux package availability, actual clock precision, real driver/acceleration interpretation, experiment overhead or cloud behavior. F09 staged guest execution, verified retrieval, downstream sidecar consumption and separate paid acceptance remain explicit integration work.
