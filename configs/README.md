# Executable Campaign Configurations

`experiments/` contains primary research campaigns. `tests/` contains reduced-cost executable smoke-test campaigns. Both use the same campaign schema and explicitly select the providers to run.

| Configuration | Experiment ID | Thesis-side mapping |
| --- | --- | --- |
| `experiments/exp-001-multi-provider.yaml` | `EXP-001` | `../Thesis/experiments/EXPERIMENTS.md#exp-001-same-zone-campaign` |
| `experiments/exp-002-cross-zone.yaml` | `EXP-002` | `../Thesis/experiments/EXPERIMENTS.md#exp-002-cross-zone-campaign` |
| `experiments/exp-003-placement-optimization.yaml` | `EXP-003` | `../Thesis/experiments/EXPERIMENTS.md#exp-003-placement-optimization-campaign` |
| `experiments/exp-004-inter-region.yaml` | `EXP-004` | `../Thesis/experiments/EXPERIMENTS.md#exp-004-inter-region-campaign` |
| `tests/exp-900-single-provider.yaml` | `EXP-900` | `../Thesis/experiments/EXPERIMENTS.md#exp-900-aws-smoke-validation` |
| `tests/exp-901-azure-single-provider.yaml` | `EXP-901` | `../Thesis/experiments/EXPERIMENTS.md#exp-901-azure-smoke-validation` |
| `tests/exp-902-gcp-single-provider.yaml` | `EXP-902` | `../Thesis/experiments/EXPERIMENTS.md#exp-902-gcp-smoke-validation` |
| `tests/exp-903-three-provider-smoke.yaml` | `EXP-903` | `../Thesis/experiments/EXPERIMENTS.md#exp-903-three-provider-smoke-validation` |

Primary configs use the design-document VM shapes: AWS `m7i.xlarge`, Azure `Standard_D4s_v5`, and GCP `n2-standard-4`. Smoke configs use `t3.small`, `Standard_B1s`, and `e2-micro` with 10-second phases and one aggregate stream.

The primary matrix is review-ready, not advisor-approved. Phase durations, aggregate stream count, and whether S3/S4 run remain provisional configuration values until advisor review and calibration freeze them.

Capacity purchase semantics remain provider-explicit under `provider_options`:

- AWS: change `instance_market_type` from `on_demand` to `spot`.
- Azure: change `priority` from `Regular` to `Spot`; `eviction_policy` and `max_price` remain explicit.
- GCP: change `provisioning_model` from `STANDARD` to `SPOT`; `instance_termination_action` remains explicit.

The committed defaults favor repeatable on-demand smoke validation. A later provider implementation must pass these values to its native Terraform inputs without normalizing away provider differences.

Each command processes one explicitly started campaign. Recurring triggers are external to this framework.
