# Existing GCP option contract

No new entity, field, mode or state transition.

| Existing location | Constraint after migration |
| --- | --- |
| ProviderConfiguration.provider_options when provider is gcp | Exactly two required keys: `provisioning_model` and `instance_termination_action` |
| Same mapping | `provisioning_model` MUST equal `STANDARD` |
| Same mapping | `instance_termination_action` MUST equal `STOP` |
| ProviderDeploymentInput.provider_options when provider is gcp | Identical required keys/values, checked independently |
| Resolved campaign/observation provider_config | Retains validated ProviderConfiguration without rewriting |
| Dry-run child provider_options | Retains exact values in human and JSON output |

Both keys are explicit, without defaults. STANDARD represents selected On-Demand/nonpreemptible intent. An extra `preemptible` key, even false, stays unsupported.

## Required acceptance matrix

Mutate otherwise valid inputs; apply every row to runtime and Draft 2020-12 schema validation of configuration, resolved campaign (including observations) and direct deployment. Construct/validate direct deployment independently so config rejection cannot mask it. The same options apply to all four valid modes; existing scenario/admission restrictions remain independent.

| Option case | Outcome |
| --- | --- |
| Exact `{provisioning_model: STANDARD, instance_termination_action: STOP}` | Accept |
| Action DELETE | Reject |
| Action omitted | Reject |
| Action null | Reject |
| Action empty string, `stop`, `Stop`, unknown string | Reject each |
| Action boolean, number, list or mapping | Reject each |
| Provisioning model SPOT, unknown string, null or omitted | Reject each |
| Extra preemptible key, true or false | Reject each |
| Unknown extra provider-option key | Reject |

Use valid resolved/deployment envelopes with actual role/identity/mode fields. Prove acceptance before mutation to avoid invalid envelopes yielding false positives. Mutate campaign config blocks and observation config blocks separately in resolved documents. Existing valid AWS/Azure and forbidden purchase-model cases are non-regression controls.

## Lifecycle/provenance boundary

STOP intent does not register or prove expiry. Reservation, expiry evidence, cap and cleanup states remain owned by existing features. Active YAML changes naturally change content-derived hashes; saved evidence remains immutable. Resubmitting a saved DELETE configuration as a new V2 input rejects instead of silently translating it.
