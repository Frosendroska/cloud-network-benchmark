# Fixed pre-DAY approval draft

The `campaign draft` command freezes the production-built identities for one fixed pre-DAY card: the simultaneous all-cloud V-S1/V-S3 parents and their children, the three registered missing-fault checks, and one retained-pair CAL-S1 stage. It creates no approval evidence and does not authorize provider calls. The production `prepare_stage(..., draft=True)` hook must be present; without it the command stops.

The card does not add a standalone GCS probe. Reuse qualified storage and IAM component evidence in the review, while making no claim of live archive acceptance. Price retained result archives and their continuing storage liability in the real batch. Actual generation, hashing, readback, restore, and native replay evidence must come from the normal batch/calibration result path. An archive failure blocks later new creation; cleanup of already-created resources continues.

```sh
cloud-network-benchmark campaign draft \
  --plan plans/acceptance.yaml \
  --plan plans/calibration.yaml \
  --settings /secure/private/operator.yaml \
  --output /secure/private/state/fixed-pre-day-draft.json
```

Review the emitted configs, run IDs, runtime/cost/allowance bindings, missing prerequisites, and refreshed price inputs before requesting approval. A missing quote or gate remains pending in the draft. Do not add placeholder evidence or treat the draft as executable.

After the user has approved the exact card and all prerequisites for one stage are genuinely satisfied, activation rechecks the saved draft and asks the production activation hook to validate the actual authorization and evidence. The hook must run the driver's full financial preflight against its in-memory, source-validated runtime DTOs before it writes any approval evidence or marks the stage ready. If the complete card-to-runtime binding fails, no approval evidence is published and the same draft remains retryable. The stage ID is required so calibration can remain pending until the real acceptance receipts exist.

```sh
cloud-network-benchmark campaign activate \
  --draft /secure/private/fixed-pre-day-draft.json \
  --stage-id acceptance-20261122T000000Z-abcdef01 \
  --settings /secure/private/operator.yaml \
  --execute
```

Activation consumes a stage once and dispatches its frozen IDs. It never calls the preparer to regenerate IDs. Resume an already dispatched stage through the ordinary `campaign resume`/`campaign run` service path; do not repeat activation. A changed source, config, runtime, manifest, or bundle digest blocks activation.

The draft and activation commands are not cloud-readiness evidence. The actual acceptance, cleanup, and calibration results remain separate gates.
