# Data model: DAY artifact identity

No fields, enums, schema versions or public function signatures are added. Existing identifier/path/integrity validators remain authoritative. This document narrows the association rules for existing entities.

| Entity | Fields and relationship | Constraint |
| --- | --- | --- |
| Continuous day | Existing PhaseConfiguration with unit_id, kind, phase, measured_seconds | Exactly one `kind="continuous_day"`, `phase="multi_flow"`, `measured_seconds=86400`, no parent. The unit ID is supplied, never hardcoded. |
| Probe chunk | Existing PhaseConfiguration with unit_id, kind, parent_unit_id, measured_seconds | `kind="probe_chunk"`, `parent_unit_id` equals the sole day unit; existing `0 < measured_seconds <= 300` rule. Actual producer command/note IDs determine fixture units. |
| Fixed DAY declaration | Existing ArtifactInput with artifact_id, path, kind, producer, unit_id, expectation | `day-iperf3-client` and `day-iperf3-observation` use the actual day unit. Paths/kinds/VM A producer unchanged. |
| Dynamic family declaration | Existing ArtifactInput with artifact_id, path_template, kind, producer, unit_id, expectation | The three `day-probe-*` families retain `unit_id=None` and exactly one `{chunk_id}` destination component. Declaration is not a concrete file. |
| Concrete modern chunk record | Existing ArtifactRecord with artifact_id, path, kind, producer, unit_id, deployment_id, integrity/state | `artifact_id = family.artifact_id + "-" + chunk.unit_id`; `unit_id = chunk.unit_id`; `path = family.path_template.replace("{chunk_id}", chunk.unit_id)`. Declared chunk/parent, kind, producer and deployment must agree. |
| Persistent receiver record | Existing ArtifactRecord | Retain `unit_id=None`; native receiver session cookie plus actual listener command/build identifies the source. |
| Legacy fixed client record | Existing ArtifactRecord | Same fixed ID/path/kind/producer, but `unit_id=None`; retained unchanged and cannot be repaired into a modern proof association. |
| Legacy probe record | Existing ArtifactRecord | Base family ID, template-compatible path and unit either None or the sole day parent. Repeated IDs with different concrete paths remain readable; no derived chunk identity or source renaming. |
| Acquisition source | Existing AcquisitionSource and SourceWindow | References still resolve uniquely in the same deployment; original log/native/build bytes, actual invocation, native session and clock/build semantics remain required. |

Modern chunk IDs are unique within the child result inventory, as already required by retrieval. Existing legacy concrete identity is the tuple `(artifact_id, path, unit_id)`; do not impose global artifact-ID uniqueness that would make historical repeated-family records unreadable. Legacy records do not become modern records by passing through ValidatorInput.

## State and proof

Declarations → actual producer files close → concrete retrieval inventory → verified ArtifactRecords → ValidatorInput admission → unchanged source binding/replay. Only observed closed bytes with matching size/hash can support replay. No change makes declared or failed retrieval evidence verified.

Compatibility is a read branch, not a lifecycle migration: legacy identity/bytes remain byte-identical; source binding against the existing F03 command-unit identities stays unavailable. Independent canonical records may still yield their own supported intervals. Unknown tails or missing sources cannot establish the full 86400-second/99-percent completeness condition. No new availability flag is added.

## Malformed modern identity

A record with a concrete family suffix is modern and must satisfy all exact relations above. Wrong suffix, null/parent unit, undeclared unit, other parent, inconsistent path, producer/kind/deployment mismatch or duplicate concrete identity is rejected. Such a record cannot fall back to the base-family legacy branch. Arbitrary custom artifacts outside the three known DAY families retain their existing rules; this repair does not build a generic template registry.
