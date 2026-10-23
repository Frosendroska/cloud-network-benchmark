# DAY artifact identity contract

This contract repairs existing associations; it does not add fields or change native acquisition semantics. Normative for new DAY records; historical forms remain readable as unavailable proof. Existing 003 and 016 contracts apply otherwise.

## Fixed declarations

Let `D` be the actual sole declared continuous-day unit. `ArtifactLayout.declarations_for_mode` emits:

| artifact_id | path | kind | producer | unit_id |
| --- | --- | --- | --- | --- |
| day-iperf3-client | measurements/day/iperf3-client.jsonl | iperf3-client-jsonl | vm_a | D.unit_id |
| day-iperf3-observation | measurements/day/iperf3-acquisition.stderr | iperf3-observation-stderr | vm_a | D.unit_id |

Paths continue to honor the existing ArtifactLayout fields. They are not relocated under the unit's generic directory. Persistent receiver JSONL and observation stderr remain deployment scoped, and their native session cookie still identifies each receiver session. No fixed literal `day` unit or `iperf3-None` command is valid evidence.

## Dynamic declarations and concrete records

| Family artifact_id | path_template | kind |
| --- | --- | --- |
| day-probe-csv | measurements/day/probes/{chunk_id}.csv | sockperf-csv |
| day-probe-log | measurements/day/probes/{chunk_id}.log | sockperf-log |
| day-probe-observation | measurements/day/probes/{chunk_id}.acquisition.stderr | sockperf-observation-stderr |

All three family declarations retain `producer=vm_a`, `unit_id=None`, existing required expectation and layout-controlled paths. For each actual declared probe chunk `C` belonging to `D`, the concrete record uses:

- `artifact_id = family.artifact_id + "-" + C.unit_id`.
- `path = family.path_template.replace("{chunk_id}", C.unit_id)`.
- `unit_id = C.unit_id`, same producer/kind and the input deployment.

For example, `day-probe-csv-probe-chunk-000001` belongs to unit `probe-chunk-000001`, not the day parent. ValidatorInput resolves it to the base template declaration and checks exact ID/path/unit/parent agreement. Its artifacts dictionary may remain the unmodified output of artifact_inputs. Concrete records are the existing retrieval output, not renamed copies. No new generic template resolution service is introduced.

Rejected modern cases include unmatched/ambiguous family declaration, mismatching suffix/path/unit, undeclared chunk, incorrect parent, wrong producer/kind/deployment and duplicate concrete identity. Use exact matching, not permissive startswith or path-only inference. Identifiers and normalized safe paths retain existing model validators.

## Legacy compatibility

Only diagnostic_day mode admits these historical identity forms in addition to normal existing rules:

1. Fixed client/native observation record with its existing fixed ID/path/kind/producer and `unit_id=None`. Accept both original persisted declarations with None and freshly derived declarations with D.unit_id, without changing either record.
2. Probe record whose artifact_id is exactly one base family ID, whose path matches that family's existing template and whose unit is None or D.unit_id. Distinct paths may repeat the family ID because existing concrete identity is a tuple. Accept it without extracting a replacement chunk ID from its filename.

Legacy compatibility fixtures start from historically serialized records plus materialized original producer bytes at the bundle-read boundary. They do not rerun current retrieval with legacy inventory IDs: RetrievalManifest already rejects that form. The modern golden pipeline always supplies its actual retrieved records unchanged.

These allowances do not accept suffixed modern records with inconsistent relationships. They do not change a record, declaration, command, source reference or raw byte. They do not synthesize build or clock evidence. Binding/replay over the original F03 commands remains unavailable for the legacy records; valid independent modern sources can still replay. Absent acquisition observations retain their existing unavailable defaults. Compatibility never declares coverage valid or promotes claimed overlap.

## Unchanged proof and interfaces

`RetrievalManifest` continues enforcing its existing unique IDs and exact chunk expansion; no retrieval implementation change is required. `AcquisitionSource`, `bind_acquisition_sources` and `iter_source_windows` retain their current unique-reference, command, source/build, native-cookie/endpoint/settings, boot/domain, closed-file size/hash and partial-tail checks. No BuildObservation field is added to ValidatorInput. F08's verified sidecar decoding is separate consumer work.

The acceptance chain uses real ArtifactLayout/artifact_inputs output, F03-produced files/command evidence, fake closed-file RetrievalManifest/run_retrieval transfers, admitted ValidatorInput records and unchanged helpers. Source/build metadata supplied by existing deterministic fixtures is labeled synthetic. Any conversion of the persistent receiver command to CommandEvidence retains its actual action/invocation and associates it with the day parent under the existing first-unit convention; it does not invent another listener.

## Canonical schemas and serialized delivery

The public DTO shape/version stays unchanged. Run the existing contracts.schema exporter against specs/003-v2-shared-contracts/contracts; update only files whose generated bytes change. Cross-record semantic checks are runtime rules, not a claim that JSON Schema alone can express these associations. Record a no-diff schema result honestly if validators alone change.

Planning is isolated to specs/018-day-artifact-identity. Shared implementation is blocked until coordinator verifies 017 merged, integrates latest main into this feature branch and releases the sole shared-contract writer. Do not edit unmerged 017 or F08 code; return any new ownership gap to the coordinator.
