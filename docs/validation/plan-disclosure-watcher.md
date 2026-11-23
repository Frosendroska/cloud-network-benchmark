# Pre-positioned saved-plan disclosure watcher

This offline operator helper monitors one to six explicitly supplied provider
roots. The caller uses six roots for the normal simultaneous acceptance slot,
three for the fixed CAL slot, and one for a single-provider registered-failure
gate. Each path is an exact allowlisted root, not a broad shared parent. A root
may not exist when monitoring starts; the watcher never creates it and rejects
symlinks in its path or discovered tree. It recursively detects one new original
`*-review-*/review-request.json`, including nested provider layouts such as:

```text
<aws-root>/<namespace>/<run>/<deployment>/aws-safety-<region>-review-<id>/review-request.json
<gcp-root>/<hash>/<run>/<deployment>/protection/gcp-protection-apply-review-<id>/review-request.json
```

The watcher selects that exact original request path, finds its unique variables
file by the request hash under the six roots, then delegates scope validation,
complete disclosure, source/state comparison and immutable checkpointing to
`cloud_network_benchmark.plan_disclosure`.

Start the watcher before the provider stage. Supply one finite absolute `watch
end` from the approved stage packet as the initial-watch horizon. The watcher
derives the review cutoff from the first observed original request publication
mtime plus 40 seconds, capped by that watch end. It never resets either cutoff.
The producer's original 50-second budget remains authoritative and unchanged.
The request is accepted only after its complete binding is valid and stable over
consecutive polls; its first observed publication time still sets the cutoff.

Prepare an existing private parent directory with mode 0700 for the new
checkpoint. In the already prepared private operator terminal, before starting
the provider stage, run the command with the exact number of provider roots for
the approved slot and its finite overall end:

```sh
PYTHONPATH=/absolute/reviewed/checkout/src /absolute/venv/bin/python \
  /absolute/reviewed/checkout/scripts/watch_plan_review.py \
  --owned-root /absolute/exact/allowlisted/provider-child-root-1 \
  --owned-root /absolute/exact/allowlisted/provider-child-root-2 \
  --owned-root /absolute/exact/allowlisted/provider-child-root-3 \
  --owned-root /absolute/exact/allowlisted/provider-child-root-4 \
  --owned-root /absolute/exact/allowlisted/provider-child-root-5 \
  --owned-root /absolute/exact/allowlisted/provider-child-root-6 \
  --checkpoint /absolute/private/checkpoints/new-disclosure-unique-name \
  --watch-end-epoch ORIGINAL_FINITE_WATCH_END_EPOCH
```

Keep terminal output private because it contains the complete saved-plan
payload. The watcher fails closed if the original evidence is stale, changed,
accepted, cancelled, failed, symlinked, ambiguous, sensitive, incomplete or
truncated, or if another request appears. The successor helper checks the
original pending state and request bytes before checkpoint completion and again
before printing the receipt command. Cancellation during disclosure leaves
private checkpoint evidence but withholds the actionable command.

The helper creates only a new 0700 disclosure checkpoint with 0600 files. It
never creates a receipt or invokes Terraform/provider APIs. After inspecting the
complete payload, resource/action scope, account/project, region, inputs, state
and source, use the emitted command manually with the **original producer review
directory** and request hash. Never target a copied plan or checkpoint. On
rejection, changed evidence or timeout, allow the existing cleanup path to finish
and do not retry automatically.

Focused offline checks:

```sh
PYTHONPATH=src /absolute/venv/bin/python -m pytest -q \
  tests/unit/test_plan_disclosure_operator.py
```

These checks do not establish native Ubuntu timing or live operator terminal
behavior. Native Linux rehearsal remains required before relying on the watcher
for a paid gate.
