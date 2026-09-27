# Offline CLI Contract

The installed command is `cloud-network-benchmark`. The equivalent module entry is `python -m cloud_network_benchmark`.

## Common Input Rules

- `--config PATH` accepts a YAML file under `configs/experiments/` or `configs/tests/`.
- `--format human|json` defaults to `human`.
- All commands parse and validate the whole campaign before further work.
- Validation errors are written to stderr in human mode and emitted as structured JSON in JSON mode.
- No command accepts credentials or secret values as CLI options.

## `validate`

```text
cloud-network-benchmark validate --config PATH [--format human|json]
```

Reads and validates the campaign. On success, reports experiment ID, configuration role, scenario, scheduled start, selected providers, source hash, and child count. It generates no IDs and writes no files.

## `resolve`

```text
cloud-network-benchmark resolve --config PATH [--format human|json]
```

Returns the complete Resolved Campaign Specification, including exactly one provider-specific child observation per selected provider. It generates no durable IDs and writes no files.

## `dry-run`

```text
cloud-network-benchmark dry-run --config PATH [--format human|json]
```

Returns the Dry-Run Report. Candidate IDs and paths are displayed as patterns, not reserved values. It invokes no external command and writes no files.

## `init`

```text
cloud-network-benchmark init --config PATH [--results-root PATH] [--format human|json]
```

After complete validation and collision preflight, initializes one parent campaign manifest, one child manifest and result directory per selected provider, and one byte-preserving config snapshot per child. A candidate child ID becomes assigned only when its child manifest is exclusively created. If a later persistence step fails, every assigned child manifest is preserved or recovered with partial-initialization failure evidence. `--results-root` defaults to `results/` and exists for tests and isolated local validation; production evidence still follows the canonical `results/manifests/` and `results/raw/` layout.

On success, output contains the campaign ID, parent manifest path, and each provider's run ID, child manifest path, and result path. On persistence failure, output identifies all manifests preserved for assigned IDs. Initialization records the implementation commit through the injected provenance provider and does not invoke Terraform, SSH, SCP, FLENT, or any cloud API.

## Exit Status

| Code | Meaning |
| --- | --- |
| `0` | Success. |
| `2` | CLI usage error. |
| `3` | YAML parse, structural validation, semantic validation, or resolution error. |
| `4` | Identity or path collision; existing evidence was not modified. |
| `5` | Local initialization or manifest persistence failure after validation. |

Machine-readable error output includes `error_code`, `message`, `field_path` when applicable, and a non-secret `details` object.
