# Result Contract

This repository stores implementation-produced results and provenance.

## Raw Results

Raw result bundles live under:

```text
results/raw/<run_id>/
```

The raw bundle should preserve original benchmark output, including raw `.flent.gz` files when FLENT is used.

Raw results are normally excluded from Git because they may become large.

## Manifests

Run manifests live under:

```text
results/manifests/<run_id>.json
```

Manifests should be lightweight enough to version-control when practical.

As implementation becomes available, each manifest should record:

- `run_id`
- `experiment_id`
- timestamp
- provider
- scenario
- config path
- config hash or snapshot
- implementation Git commit
- thesis/design Git commit when available
- Terraform version
- benchmark tool versions
- VM and cloud metadata
- raw result location
- success, failure, or interrupted state

Do not invent fields that cannot yet be collected. Keep the interface open so this provenance can be added as the framework matures.
