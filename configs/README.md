# Executable Campaign Configurations

`experiments/` contains primary research campaigns. `tests/` contains reduced-cost executable smoke-test campaigns. Both use the same campaign schema and explicitly select the providers to run.

| Configuration | Experiment ID | Thesis-side mapping |
| --- | --- | --- |
| `experiments/exp-001-multi-provider.yaml` | `EXP-001` | `../Thesis/experiments/EXPERIMENTS.md`, core same-zone matrix |
| `tests/exp-900-single-provider.yaml` | `EXP-900` | `../Thesis/experiments/EXPERIMENTS.md`, pre-experiment framework smoke validation |

Each command processes one explicitly started campaign. Recurring triggers are external to this framework.
