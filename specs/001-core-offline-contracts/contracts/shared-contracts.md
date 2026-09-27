# Shared Contracts

## Stable Enums

```text
Provider: aws | azure | gcp
Scenario: same_zone | cross_zone | placement_optimization | inter_region
VmRole: vm_a | vm_b
MeasurementDirection: vm_a_to_vm_b
BenchmarkPhase: idle_latency | single_flow | multi_flow
CommandOutcome: succeeded | failed | timed_out | interrupted
ArtifactExpectation: required | optional | not_expected
ExecutionState: initialized | provision_ready | running | collecting | validating | succeeded | failed | interrupted
CleanupState: not_started | not_required | attempted | succeeded | failed
CampaignState: initialized | active | partially_complete | succeeded | failed | interrupted
```

Consumers must preserve these serialized values exactly.

## Provider Deployment Input

F05-F07 receive one provider-specific input per child run. Required groups:

- Identity: `campaign_id`, `run_id`, `experiment_id`, `provider`, `scenario`.
- Start intent: common `scheduled_start`; F01 processes one explicitly started campaign and leaves recurrence, sharding, and triggering to an external reconciler, `tmux` process, or human.
- VM intents: exactly two entries, VM A/client and VM B/server, each with provider-native region, zone, shape, image intent, connection user, and role.
- Placement: provider-specific kind, optional name, and provider options.
- Bootstrap: template reference and non-secret inputs.
- Local execution: provider Terraform directory, child result path, provisioning timeout, and cleanup timeout.
- Provenance: config hash and honest implementation/design commit evidence.

The input never embeds credentials, private keys, authentication material, or Terraform variable secrets.

## Provider Deployment Output

F05-F07 return one output with:

- Matching `run_id` and `provider`.
- Deployment action identity and start/end timestamps.
- Exactly one VM A and one VM B record.
- Provider resource ID, private IPv4 address, actual image identity, actual shape, actual region/zone, and provider placement metadata for each VM.
- Remote Connection Data for each VM.
- `terraform-outputs.json` artifact location.
- Provider metadata and documented network-limit evidence without cross-cloud normalization.

Missing fields remain null only when accompanied by unavailable evidence and when the downstream lifecycle permits a partial failure output.

## Remote Connection Data

```text
role
host
port
user
authentication_reference
host_key_reference
readiness_expectations
failure
```

`authentication_reference` names a runtime source; it is not secret material. A durable manifest may record the reference classification but must not store a private-key path when that path would expose user-specific or sensitive information.

## Artifact Layout

Paths are child-result-root relative and never provider-specific:

| Logical name | Canonical path | Producer |
| --- | --- | --- |
| Config snapshot | `config.yaml` | F01 |
| Provider outputs | `terraform-outputs.json` | F05-F07 |
| Idle FLENT | `flent/idle.flent.gz` | F03 |
| Single-flow FLENT | `flent/single_flow.flent.gz` | F03 |
| Multi-flow FLENT | `flent/multi_flow.flent.gz` | F03 |
| Single-flow VM A diagnostics | `diagnostics/single_flow/vm_a.json` | F04 |
| Single-flow VM B diagnostics | `diagnostics/single_flow/vm_b.json` | F04 |
| Multi-flow VM A diagnostics | `diagnostics/multi_flow/vm_a.json` | F04 |
| Multi-flow VM B diagnostics | `diagnostics/multi_flow/vm_b.json` | F04 |
| VM A static metadata | `metadata/vm_a.json` | F04 |
| VM B static metadata | `metadata/vm_b.json` | F04 |
| Validation result | `validation/result.json` | F08 |
| Human summary | `validation/summary.txt` | F08 |

Producers use exclusive creation and never overwrite an existing artifact. A disabled phase is represented in validator inputs as `not_expected`; its canonical path remains stable.

## Validator Input

F08 receives:

- Campaign manifest path and child manifest path.
- Child result root.
- Config snapshot path and expected SHA-256.
- Provider outputs path and expectation.
- Every benchmark, diagnostic, metadata, and validation artifact location plus its expectation.
- Provider, scenario, enabled phases, expected VM roles, and measurement direction.
- Execution state, cleanup state, original failure, and cleanup failure.
- Required provenance fields and unavailable reasons.
- VM metadata, cloud/provider metadata, and tool-version structured evidence, including explicit unavailable reasons.

F01 defines locations and expectations only. F08 owns integrity checks, content checks, plausibility rules, and the final validation result.

## Manifest Evidence and Reservation

A child run ID is a candidate until its initial child manifest is exclusively created. That create is the atomic reservation that assigns the ID. Every later initialization failure preserves or recovers each assigned child manifest with `execution_state: failed` and partial-initialization failure evidence; assigned manifests are never rolled back or deleted.

Child manifests always contain `vm_metadata`, `provider_metadata`, and `tool_versions` structured-evidence fields. Each has either an object `value` and null `unavailable_reason`, or a null `value` and a non-empty reason. Campaign manifests also carry controller/runtime `tool_versions` evidence.

Implementation commit provenance is obtained through an injectable provider for both primary and smoke-test campaigns. Offline tests inject deterministic commits and do not invoke Git through the command runner or a subprocess.

## External Command Boundary

### CommandRequest

```text
action_id: string
action_type: terraform | ssh | scp | flent | local_tool | string extension
argv: non-empty sequence[string]
cwd: repository-relative or absolute runtime path
environment: runtime-only map[string, string]
environment_classification: none | non_secret | contains_secret_references
timeout_seconds: positive number
stdin: optional runtime string
```

Commands use an argument sequence and `shell=false`. Durable command provenance includes executable and arguments after redaction, working location, environment key names/classification, timeout, and action ID. It excludes secret environment values and sensitive stdin.

### CommandResult

```text
action_id: string
outcome: succeeded | failed | timed_out | interrupted
started_at: RFC 3339 timestamp
finished_at: RFC 3339 timestamp
duration_seconds: non-negative number
exit_code: integer or null
stdout: string
stderr: string
redactions: sequence[string]
```

Consistency rules:

- `succeeded` requires exit code `0`.
- `failed` requires a non-zero exit code.
- `timed_out` and `interrupted` may have a null exit code.
- Finished time cannot precede start time.
- Output is captured for evidence but must be redacted before durable storage when callers identify sensitive values.

### CommandRunner Protocol

```text
run(request: CommandRequest) -> CommandResult
```

The production runner is the only F01 component allowed to invoke a subprocess. `ScriptedCommandRunner` accepts an ordered queue of expected request matchers and results, records every request, and fails tests on missing, extra, or out-of-order calls. Fixtures cover all four outcomes.

## Dry-Run Report

The report is available as stable JSON and a human-readable rendering. It contains:

- Config source, role, hash, experiment ID, scheduled start, scenario, and selected providers.
- Exactly one child summary per selected provider.
- Provider-native regions, zones, placement kind, shape/image intent, VM roles, and A-to-B direction.
- Explicit benchmark order, enablement, durations, stream count, and timeouts.
- Candidate campaign/child ID shape and manifest/result path templates.
- Canonical artifact locations and validator expectations.
- Lifecycle and cleanup outline.
- External action types later lifecycle features would request.

It contains no durable ID reservation, credentials, authentication material, environment values, private keys, cloud contact, subprocess execution, or filesystem writes.
