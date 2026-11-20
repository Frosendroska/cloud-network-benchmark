# Campaign dispatch contract

WEEK preparation requires `approvals.weekly_concurrency` (or the stage-specific override) with `confirmed: true`, an existing source file, and fields exactly naming `mode: paired_s1_s3` and `max_parallel_entries: 2`. The prepared-stage policy binds the generated evidence-file path and SHA-256. The runner rechecks it before weekly dispatch.

The driver groups only by the frozen `launch_at`; every full slot must contain exactly one S1/P0 and one S3/P1 with matching scheduled time. At runtime, the whole pair must still be pending. There is no configurable larger pool. Acceptance continues to serialize entries, including all supervised failure checks.

The lock, ledger, source/config/schedule provenance, original deadlines, plan-review, host-trust, expiry, cleanup and failure-evidence contracts are unchanged.
