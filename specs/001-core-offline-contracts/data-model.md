# Data Model: F01 Core Contracts and Offline Harness

## Model Conventions

- All serialized field names use `snake_case`.
- All timestamps are RFC 3339 strings with an explicit offset and are normalized to UTC when resolved.
- All paths stored in manifests are repository-relative POSIX paths; runtime code resolves them against an injected repository root.
- Enums serialize to the exact lowercase values shown here.
- Resolved specifications and command requests are immutable value objects.
- Optional or unavailable evidence is represented as `null` plus an explanatory field where required; placeholder values are forbidden.
- Unknown input fields are rejected so spelling errors cannot silently change an experiment.

## Campaign Configuration

User-authored YAML from `configs/experiments/` for primary campaigns or `configs/tests/` for executable reduced-cost smoke-test campaigns. Both roles retain an `experiment_id` with a documented Thesis-side experiment or verification mapping.

| Field | Type | Rules |
| --- | --- | --- |
| `schema_version` | integer | Required; `1` for this contract. |
| `experiment_id` | string | Required; stable thesis identifier matching `EXP-[0-9]{3,}`. |
| `scheduled_start` | timestamp | Required common intended start for every selected provider in this single explicitly started campaign. It is not a recurrence rule. |
| `selected_providers` | list of provider enum | Required, unique, 1-3 entries; allowed values are `aws`, `azure`, `gcp`. |
| `scenario` | scenario enum | Required; `same_zone`, `cross_zone`, `placement_optimization`, or `inter_region`. |
| `benchmark` | Benchmark Plan | Required; keeps duration, streams, and optional phase enablement explicit. |
| `provider_configs` | map of Provider Configuration | Required; keys must exactly equal `selected_providers`. |
| `options` | Campaign Options | Required, may be empty; contains non-methodology run controls such as labels and declared timeouts. |

The loader derives `configuration_role` as `experiment` or `test` from the canonical root. Both roles use exactly the same schema and validation path. An external reconciler, `tmux` process, or human may invoke future commands at the configured time; recurrence, automatic triggering, and sharding are not modelled by F01.

### Benchmark Plan

| Field | Type | Rules |
| --- | --- | --- |
| `idle_latency` | Phase Configuration | Required and enabled for every campaign. |
| `single_flow` | Phase Configuration | Required and enabled for every campaign. |
| `multi_flow` | Multi-flow Phase Configuration | Required as an explicit object; may be disabled only where the approved scenario/config scope permits it. |
| `execution_order` | list | Must equal `idle_latency`, `single_flow`, `multi_flow`; disabled phases remain declared but are skipped. |

Each phase carries `enabled`, `test_name`, `duration_seconds`, `timeout_seconds`, and a string-to-scalar `parameters` map. Durations and timeouts are positive. `multi_flow.parameters.upload_streams` is a positive integer when enabled and is never inferred from vCPU count.

### Provider Configuration

Provider configuration is a discriminated union. Every member has:

| Field | Type | Rules |
| --- | --- | --- |
| `provider` | provider literal | Must match its `provider_configs` key. |
| `regions.vm_a`, `regions.vm_b` | string | Required provider-native region names. |
| `zones.vm_a`, `zones.vm_b` | string | Required provider-native zone names or logical-zone identifiers. |
| `vm_shape` | string | Required provider-native shape/SKU/machine type. |
| `image` | string | Required resolvable image intent; exact deployed identity is later output evidence. |
| `connection_user` | string | Required non-secret remote user identity. |
| `documented_network_limit` | string or null | Interpretation metadata; nullable with no fabricated default. |
| `placement` | Placement Configuration | Required and explicit even when disabled. |
| `provider_options` | provider-specific object | Non-secret provider inputs retained without cross-cloud normalization. |

Placement kinds are provider-specific:

- AWS: `none` or `cluster_placement_group`.
- Azure: `none` or `proximity_placement_group`.
- GCP: `none` or `compact_placement_policy`.

### Scenario Validation

| Scenario | Region invariant | Zone invariant | Placement invariant |
| --- | --- | --- | --- |
| `same_zone` | VM A and VM B regions equal | VM A and VM B zones equal | `none` |
| `cross_zone` | VM A and VM B regions equal | VM A and VM B zones differ | `none` |
| `placement_optimization` | Provider-valid shared region | Provider-valid placement locality | Provider's named optimization kind required |
| `inter_region` | VM A and VM B regions differ | Both zones explicit | `none` |

F01 validates these offline invariants. Provider features retain responsibility for cloud-specific availability, quotas, and Terraform behavior.

## Resolved Campaign Specification

Immutable, side-effect-free result of successful validation.

| Field | Type | Description |
| --- | --- | --- |
| `schema_version` | integer | Contract version. |
| `experiment_id` | string | Stable thesis identity. |
| `configuration_role` | enum | `experiment` or `test`. |
| `config_source` | Config Provenance | Source path, SHA-256, byte length, and snapshot intent. |
| `scheduled_start` | UTC timestamp | Common intended child start. |
| `scenario` | scenario enum | Exact thesis term. |
| `selected_providers` | ordered unique list | Preserves user order for display only; behavior does not depend on order. |
| `benchmark` | resolved Benchmark Plan | Explicit phase settings and order. |
| `observations` | list of Resolved Observation Specification | Exactly one item per selected provider. |

Validation requires the observation providers to match `selected_providers` one-to-one.

## Resolved Observation Specification

Immutable provider-child plan passed to later features.

| Field | Type | Description |
| --- | --- | --- |
| `experiment_id` | string | Parent thesis identity. |
| `provider` | provider enum | Exactly one of AWS, Azure, or GCP. |
| `scenario` | scenario enum | Exact campaign scenario. |
| `scheduled_start` | UTC timestamp | Copied from the parent. |
| `vm_a` | VM Intent | Role `client`, traffic generator, provider-native placement and shape. |
| `vm_b` | VM Intent | Role `server`, receiver, provider-native placement and shape. |
| `measurement_direction` | literal | Always `vm_a_to_vm_b`. |
| `placement` | provider-specific Placement Configuration | Preserves cloud semantics. |
| `benchmark` | resolved Benchmark Plan | Same protocol intent for every child. |
| `artifact_layout` | Artifact Layout | Canonical child-relative names. |
| `deployment_input` | Provider Deployment Input | Stable handoff to F05-F07. |

## Identity and Paths

### Campaign Identity

`campaign_id` has the logical form `<utc-timestamp>-<normalized-experiment-id>-<random-token>`. The clock and token source are injected; exact formatting is centralized and covered by tests.

### Child Identity

`run_id` has the logical form `<campaign_id>-<provider>`. One campaign cannot contain two children for the same provider. A generated value is only a candidate until an exclusive child-manifest create succeeds; that atomic reservation is the point at which the run ID becomes assigned.

### Allocated Paths

| Evidence | Path |
| --- | --- |
| Parent manifest | `results/manifests/<campaign_id>.json` |
| Child manifest | `results/manifests/<run_id>.json` |
| Child result bundle | `results/raw/<run_id>/` |
| Child config snapshot | `results/raw/<run_id>/config.yaml` |

All destinations are preflighted before creation. Existing files or directories cause a collision error and are never modified. After preflight, each child manifest is exclusively created before its raw-result directory or snapshot. If a later persistence step fails, every successfully reserved child manifest is atomically updated to `failed` with partial-initialization evidence; assigned manifests are never deleted. Fault injection covers each persistence boundary.

## Config Provenance

| Field | Type | Description |
| --- | --- | --- |
| `source_path` | path | Canonical repository-relative YAML path. |
| `sha256` | string | Lowercase SHA-256 of exact source bytes. |
| `byte_length` | integer | Exact source byte count. |
| `snapshot_path` | path or null | Child-local snapshot path after initialization. |
| `implementation_git_commit` | Evidence Value | Current implementation commit obtained through the injected Git provenance provider, or an explicit reason when genuinely unavailable. |
| `design_git_commit` | Evidence Value | Thesis repository commit or explicit unavailable reason. |

An Evidence Value contains `value` and `unavailable_reason`; exactly one is non-null. Production initialization uses a repository-backed provider, while offline tests inject deterministic commit values and invoke no Git subprocess.

## Campaign Record and Manifest

Parent record for one initialized campaign attempt.

| Field | Type | Description |
| --- | --- | --- |
| `manifest_type` | literal | `campaign`. |
| `schema_version` | integer | Manifest contract version. |
| `campaign_id` | string | Unique initialized attempt. |
| `experiment_id` | string | Stable thesis identity. |
| `configuration_role` | enum | `experiment` or `test`. |
| `scheduled_start` | UTC timestamp | Common intended start. |
| `selected_providers` | list | Exact selected set in display order. |
| `scenario` | scenario enum | Exact thesis term. |
| `config_provenance` | Config Provenance | Parent provenance; snapshot paths are represented on children. |
| `aggregate_state` | Campaign State | Derived from children. |
| `child_runs` | list of Child Reference | One per provider with run ID and child manifest/result paths. |
| `tool_versions` | Structured Evidence | Controller/runtime tool versions known at initialization, or explicit unavailable evidence. |
| `created_at`, `updated_at` | UTC timestamp | Manifest timing. |
| `lifecycle_events` | list of Lifecycle Event | Append-only parent history. |

### Campaign State

- `initialized`: all children initialized and none advanced.
- `active`: at least one child is progressing and none are terminally inconsistent.
- `partially_complete`: a mix of terminal and non-terminal child outcomes exists.
- `succeeded`: every child execution succeeded and every required cleanup succeeded.
- `failed`: all children are terminal and at least one failed or has failed cleanup.
- `interrupted`: all children are terminal, at least one was interrupted, and none failed.

## Child Run Record and Manifest

Provider-specific record for one campaign child.

| Field | Type | Description |
| --- | --- | --- |
| `manifest_type` | literal | `child_run`. |
| `schema_version` | integer | Manifest contract version. |
| `run_id` | string | Unique child attempt. |
| `campaign_id` | string | Parent identity. |
| `experiment_id` | string | Stable thesis identity. |
| `provider` | provider enum | Child provider. |
| `scenario` | scenario enum | Child scenario. |
| `scheduled_start` | UTC timestamp | Intended common start. |
| `actual_started_at`, `actual_finished_at` | timestamp or null | Observed lifecycle timing. |
| `config_provenance` | Config Provenance | Includes child snapshot path. |
| `resolved_observation` | Resolved Observation Specification | Exact provider plan. |
| `execution_state` | Execution State | Original lifecycle outcome. |
| `cleanup_state` | Cleanup State | Independent cleanup outcome. |
| `failure` | Failure Evidence or null | Original failure; never replaced by cleanup failure. |
| `cleanup_failure` | Failure Evidence or null | Cleanup-specific evidence. |
| `vm_metadata` | Structured Evidence | VM A/VM B metadata when observed, otherwise null with an unavailable reason. |
| `provider_metadata` | Structured Evidence | Provider/cloud metadata when observed, otherwise null with an unavailable reason. |
| `tool_versions` | Structured Evidence | Relevant controller, Terraform, benchmark, and diagnostic tool versions when observed, otherwise null with an unavailable reason. |
| `artifacts` | Artifact Layout | Expected child-relative evidence. |
| `lifecycle_events` | list of Lifecycle Event | Append-only transition history. |
| `created_at`, `updated_at` | UTC timestamp | Manifest timing. |

### Execution State

`initialized`, `provision_ready`, `running`, `collecting`, `validating`, `succeeded`, `failed`, `interrupted`.

Normal transition path:

```text
initialized -> provision_ready -> running -> collecting -> validating -> succeeded
```

`failed` and `interrupted` may be entered from any non-terminal state. Terminal execution states cannot transition to another execution state.

### Cleanup State

`not_started`, `not_required`, `attempted`, `succeeded`, `failed`.

Allowed transitions:

```text
not_started -> not_required
not_started -> attempted -> succeeded
not_started -> attempted -> failed
```

Cleanup changes never alter `execution_state` or `failure`.

These exact underscore-separated values are the only serialized lifecycle vocabulary. Human output may add explanatory labels but must not serialize aliases such as `provision-ready`, `pending`, or `cleanup attempted`.

### Lifecycle Event

Contains `state_domain` (`campaign`, `execution`, or `cleanup`), `state`, `occurred_at`, and optional `detail`. Events are append-only and ordered by occurrence.

### Failure Evidence

Contains `category`, `message`, `occurred_at`, optional `action_id`, and optional structured `details`. It must not contain secrets.

### Structured Evidence

Contains `value` as a JSON object or `null`, plus `unavailable_reason` as a string or `null`; exactly one is non-null. This wrapper is used for VM metadata, cloud/provider metadata, and tool versions so initial and partially initialized manifests are schema-valid before those observations exist.

## Result Bundle and Artifact Layout

All paths are relative to `results/raw/<run_id>/`:

```text
config.yaml
terraform-outputs.json
flent/idle.flent.gz
flent/single_flow.flent.gz
flent/multi_flow.flent.gz
diagnostics/single_flow/vm_a.json
diagnostics/single_flow/vm_b.json
diagnostics/multi_flow/vm_a.json
diagnostics/multi_flow/vm_b.json
metadata/vm_a.json
metadata/vm_b.json
validation/result.json
validation/summary.txt
```

Disabled phases remain named in the layout and are marked `not_expected` in validator input rather than silently omitted.

## Provider Deployment Input

| Field group | Contents |
| --- | --- |
| Identity | campaign ID, run ID, experiment ID, provider, scenario |
| Placement | provider-native regions, zones, placement kind/name/options |
| VM intent | explicit VM A/client and VM B/server shape, image, role, connection user |
| Bootstrap | non-secret bootstrap template reference and declared inputs |
| Local execution | provider Terraform directory, child result root, timeout values |
| Provenance | config hash, implementation commit, design commit evidence |

No credentials or secret material is embedded.

## Provider Deployment Output

| Field group | Contents |
| --- | --- |
| Identity | provider, run ID, apply action ID |
| VM A and VM B | provider resource ID, private IPv4 address, image identity, actual shape, role |
| Placement | actual provider-native region, zone, placement metadata |
| Connection | one Remote Connection Data value per VM |
| Evidence | Terraform output artifact path, timestamps, provider metadata, documented network limit evidence |

## Remote Connection Data

Contains VM role, host/private address, port, user, authentication-reference identifier, host-key policy/reference, readiness expectations, and optional connection failure evidence. Authentication material itself is never serialized.

## External Command Contracts

### Command Request

| Field | Type | Rules |
| --- | --- | --- |
| `action_id` | string | Caller-provided correlation identity. |
| `action_type` | enum/string | Such as `terraform`, `ssh`, `scp`, `flent`, or `local_tool`. |
| `argv` | non-empty list of strings | Executed without a shell. |
| `cwd` | path | Explicit working directory. |
| `environment` | map of string to string | Runtime-only; records expose key names/classification, not secret values. |
| `environment_classification` | enum | `none`, `non_secret`, or `contains_secret_references`. |
| `timeout_seconds` | positive number | Required. |
| `stdin` | string or null | Runtime input; omitted from durable records when sensitive. |

### Command Result

Contains action ID, outcome (`succeeded`, `failed`, `timed_out`, `interrupted`), UTC start/end timestamps, duration, exit code or null, stdout, stderr, and redaction metadata. A timed-out or interrupted result may have no exit code.

### Command Runner

One operation: accept a Command Request and return a Command Result. The scripted fake records requests and consumes predefined results; an exhausted or mismatched script is a test failure.

## Validator Input Set

Contains the child manifest path, config snapshot/hash, provider output path, each artifact's expected/optional/not-expected classification, static metadata paths, TCP diagnostic paths, execution and cleanup states, and provenance requirements. F08 owns content validation; F01 owns this stable input vocabulary.

## Dry-Run Report

Contains configuration identity and role, config hash, scheduled start, scenario, selected providers, one resolved child summary per provider, candidate ID/path patterns, expected artifact layout, lifecycle outline, validation inputs, cleanup expectation, and planned external action classes. It never contains credentials, authentication material, environment values, or durable reserved IDs.
