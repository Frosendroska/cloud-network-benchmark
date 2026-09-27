# Executable Campaign Configurations

`experiments/` contains primary research campaigns. `tests/` contains reduced-cost executable smoke-test campaigns. Both use the same campaign schema and explicitly select the providers to run.

| Configuration | Experiment ID | Thesis-side mapping |
| --- | --- | --- |
| `experiments/exp-001-multi-provider.yaml` | `EXP-001` | `../Thesis/experiments/EXPERIMENTS.md`, core same-zone matrix |
| `experiments/exp-002-cross-zone.yaml` | `EXP-002` | Core cross-zone matrix (S2) |
| `experiments/exp-003-placement-optimization.yaml` | `EXP-003` | Provider-specific placement matrix (S3) |
| `experiments/exp-004-inter-region.yaml` | `EXP-004` | Cost-gated Frankfurt-to-London matrix (S4) |
| `tests/exp-900-single-provider.yaml` | `EXP-900` | AWS provisioning smoke validation |
| `tests/exp-901-azure-single-provider.yaml` | `EXP-901` | Azure provisioning smoke validation |
| `tests/exp-902-gcp-single-provider.yaml` | `EXP-902` | GCP provisioning smoke validation |
| `tests/exp-903-three-provider-smoke.yaml` | `EXP-903` | Short, cheap full-framework smoke validation |

Primary configs use the design-document VM shapes: AWS `m7i.xlarge`, Azure `Standard_D4s_v5`, and GCP `n2-standard-4`. Smoke configs use `t3.small`, `Standard_B1s`, and `e2-micro` with 10-second phases and one aggregate stream.

Capacity purchase semantics remain provider-explicit under `provider_options`:

- AWS: change `instance_market_type` from `on_demand` to `spot`.
- Azure: change `priority` from `Regular` to `Spot`; `eviction_policy` and `max_price` remain explicit.
- GCP: change `provisioning_model` from `STANDARD` to `SPOT`; `instance_termination_action` remains explicit.

The committed defaults favor repeatable on-demand smoke validation. A later provider implementation must pass these values to its native Terraform inputs without normalizing away provider differences.

Each command processes one explicitly started campaign. Recurring triggers are external to this framework.
