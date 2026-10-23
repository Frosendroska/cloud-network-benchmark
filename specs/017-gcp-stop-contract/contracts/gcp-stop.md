# Public GCP STOP interface amendment

Applies to new V2 inputs after verified merge; names and schema version 2 remain.

```yaml
provider: gcp
provider_options:
  provisioning_model: STANDARD
  instance_termination_action: STOP
```

Exactly these two required keys, with case-sensitive values. DELETE, missing action, Spot and extra options reject at configuration, direct deployment and exported-schema boundaries. No default or silent rewrite. [data-model.md](../data-model.md) defines the mutation corpus.

Consumers: existing loader/resolver, resolved campaign/observation, deployment constructor and CLI previews. Canonical schemas remain in `specs/003-v2-shared-contracts/contracts/`: at minimum `campaign-config.schema.json`, `resolved-campaign.schema.json`, `provider-deployment-input.schema.json` and any existing export embedding amended models. Export the current whole set and commit changed bytes only; this directory contains no competing generated schema set.

AWS `instance_market_type=on_demand` and Azure `priority=Regular`, `eviction_policy=Delete`, `max_price=-1.0` retain existing semantics. Do not replace Delete/DELETE globally across providers or history.

F07 propagates this intent into its pre-created immutable templates and checks provider observations. No unmerged F07 runtime is imported here. STOP is emergency compute protection, not proof of VM/disk/address deletion or closed cleanup. Normal cleanup still deletes/verifies all billable resources. No expiry API, CLI option or cloud operation is added by this feature.
