# Data Model: WEEK dispatch admission

## Dispatch policy

- `policy_id`: `weekly-paired-s1-s3-v1`
- `group_by`: `launch_at`
- `scenarios`: exactly `S1`, `S3`
- `priorities`: exactly `P0`, `P1`
- `max_parallel_entries`: exactly `2`
- `evidence_path`, `evidence_sha256`: generated source-bound admission evidence

## Entry pair

Each slot has two unique entries with stable scenario/priority, identical `launch_at` and `scheduled_start`, and independent runtime, state, provider resources, result root, placement identity, and child run IDs.

## Journal

Existing entry states remain `scheduled`, `starting`, `running`, `complete`, `failed`, and `missed`. A partial pair cannot launch. A failed or missed WEEK slot marks later scheduled slots missed. Ambiguous attempts use existing reconciliation and are never automatically remeasured.
