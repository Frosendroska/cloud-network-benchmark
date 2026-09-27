# Quickstart: Validate F01 Offline

This is the acceptance guide for the implementation produced from this plan. The package, fixtures, and CLI entry point are created during F01 implementation; until then, the commands below describe the required runnable interface rather than an already available tool.

Primary executable campaign configurations live under `configs/experiments/`. Reduced-cost executable smoke-test campaigns live under `configs/tests/`. Both use the same schema and retain documented Thesis-side experiment or verification mappings. Each command handles one explicitly started campaign; any recurring trigger is external to F01.

## Prerequisites

- Python 3.9 or newer.
- A clean local checkout with no cloud credentials required.
- No Terraform, SSH server, FLENT, or cloud CLI is needed for F01 validation.

## Setup

From the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
```

## Run the Complete F01 Suite

```bash
.venv/bin/python -m pytest tests/unit tests/contract tests/integration
```

Expected outcome: all tests pass without network access, credentials, subprocess-driven cloud tools, or files written outside pytest temporary directories.

## Scenario 1: Validate and Resolve a Three-Provider Campaign

```bash
.venv/bin/cloud-network-benchmark validate \
  --config configs/experiments/exp-001-multi-provider.yaml \
  --format json

.venv/bin/cloud-network-benchmark resolve \
  --config configs/experiments/exp-001-multi-provider.yaml \
  --format json
```

Expected outcome:

- Validation reports `EXP-001`, the configured scheduled start, and `aws`, `azure`, and `gcp`.
- Resolution contains exactly three children and no other provider.
- Each child retains provider-native region, zone, placement, shape, and image values.
- Every child declares VM A as client, VM B as server, and `vm_a_to_vm_b` direction.
- No `results/` path is created or changed.

## Scenario 2: Preview a Single-Provider Test Campaign

```bash
.venv/bin/cloud-network-benchmark dry-run \
  --config configs/tests/exp-900-single-provider.yaml \
  --format human
```

Expected outcome:

- The role is `test` and exactly one selected provider is shown.
- Reduced VM shape and benchmark parameters remain visible.
- The report shows candidate identity/path patterns, canonical artifacts, lifecycle steps, validation expectations, and later external action types.
- No durable ID, manifest, result directory, command execution, credential, or secret value is produced.

## Scenario 3: Initialize Linked Local Evidence

Create an isolated destination, then initialize the three-provider fixture:

```bash
F01_RESULTS_ROOT="$(mktemp -d)"
.venv/bin/cloud-network-benchmark init \
  --config configs/experiments/exp-001-multi-provider.yaml \
  --results-root "$F01_RESULTS_ROOT" \
  --format json
```

Expected outcome:

- One campaign ID and three provider-suffixed child run IDs are returned.
- `manifests/` contains one parent and three child JSON manifests.
- `raw/` contains exactly three child directories.
- Each child directory contains a byte-identical `config.yaml` snapshot.
- Parent and child manifests reference one another and share the source SHA-256.
- Initial child history contains `initialized`; cleanup is `not_started`.

Run the same command again with deterministic test ID sources in the integration suite. Expected outcome: exit code `4`, with every existing byte unchanged.

## Scenario 4: Reject an Invalid Campaign Before Side Effects

```bash
.venv/bin/python -m pytest \
  tests/unit/test_config_loading.py \
  tests/unit/test_config_resolution.py \
  tests/integration/test_validate_resolve_cli.py
```

Expected outcome:

- The provider-mismatch case reports exit code `3` and identifies the mismatch between `selected_providers` and `provider_configs`.
- No campaign ID or child run ID is returned.
- The injected temporary result root contains no manifest or raw-result evidence.
- No external action was requested.

The `invalid-cross-zone.yaml` case must identify equal zones as incompatible with `cross_zone`.

## Scenario 5: Verify Lifecycle and Failure Preservation

```bash
.venv/bin/python -m pytest \
  tests/unit/test_lifecycle.py \
  tests/integration/test_initialize_cli.py
```

Expected outcome:

- Valid state transitions pass and illegal terminal-state rewrites fail.
- A child execution failure remains unchanged after cleanup failure.
- Parent state is never `succeeded` unless every selected child succeeded and met cleanup obligations.
- Partial and interrupted child outcomes aggregate deterministically.

## Scenario 6: Verify Command Fakes

```bash
.venv/bin/python -m pytest tests/unit/test_execution.py
```

Expected outcome: scripted fake outcomes cover success, non-zero failure, timeout, and interruption; requests are asserted in order; a missing or extra action fails the test; no real subprocess is launched.

## Contract References

- [Data model](data-model.md)
- [Campaign configuration schema](contracts/campaign-config.schema.json)
- [Campaign manifest schema](contracts/campaign-manifest.schema.json)
- [Child run manifest schema](contracts/child-run-manifest.schema.json)
- [Shared contracts](contracts/shared-contracts.md)
- [CLI contract](contracts/cli.md)
