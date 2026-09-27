# cloud-network-benchmark

Implementation repository for the Master's thesis "Benchmarking Network Performance across Clouds".

This repository is the reproducible software artifact for deploying cloud infrastructure, running VM-to-VM network benchmarks, collecting metadata, and preserving raw experimental results.

The sibling repository `../Thesis` remains the source of truth for research questions, methodology, experiment design, project tracking, thesis analysis, interpretation, and writing. This repository implements the executable side of that design.

## Current Status

F01, the credential-free contracts and offline harness, is implemented. It validates and resolves campaign YAML, initializes immutable local campaign/child evidence, publishes stable downstream contracts, and renders side-effect-free dry runs. Provider deployment and benchmark execution remain later features.

The current thesis-side design expects a deliberately small framework:

- provider-specific Terraform for AWS, Azure, and GCP
- minimal VM bootstrap
- local lifecycle orchestration
- short-lived two-VM observations
- three sequential FLENT phases from VM A to VM B
- raw `.flent.gz` result preservation
- lightweight per-VM diagnostics and static metadata
- failure logging and cleanup validation

Before paid experiment campaigns, the framework must produce validated smoke observations for AWS, Azure, and GCP.

## Relationship To `../Thesis`

Use `../Thesis` to understand what should be measured and why:

- `../Thesis/DECISIONS.md`
- `../Thesis/STATUS.md`
- `../Thesis/experiments/`
- `../Thesis/tracking/`

Use this repository to define exactly how an approved experiment is executed.

Do not copy thesis documents, analysis, canonical figures, or project trackers into this repository. Connect design to implementation with stable experiment IDs, run IDs, config paths, and Git revisions.

## Directory Structure

```text
.
├── AGENTS.md
├── README.md
├── configs/
│   ├── experiments/
│   └── tests/
├── docs/
├── scripts/
├── terraform/
│   ├── aws/
│   ├── azure/
│   └── gcp/
├── tests/
└── results/
    ├── manifests/
    ├── processed/
    └── raw/
```

## Experiment Configs

Primary executable experiment campaigns belong in `configs/experiments/`. Reduced-cost executable smoke-test campaigns belong in `configs/tests/`. Both use the same schema, explicitly select one or more providers, and map their stable `experiment_id` to the Thesis design in `configs/README.md`.

The committed primary matrix contains one three-provider campaign for each approved scenario: same-zone, cross-zone, provider-specific placement optimization, and inter-region. The smoke matrix contains separate reduced AWS, Azure, and GCP provisioning campaigns plus one short, reduced three-provider campaign.

Each config should include at least:

- `experiment_id`
- `scenario`
- `selected_providers`
- regions and zones where relevant
- VM configuration
- benchmark phases and tool parameters
- configurable phase durations, stream count, and one explicit scheduled start
- provider-native capacity options for regular/on-demand or spot instances

Config files should remain concise. Long methodological explanations belong in `../Thesis`.

Stable experiment IDs should map to thesis-side design entries. Current thesis terminology includes:

- `same_zone`
- `cross_zone`
- `inter_region`
- `placement_optimization`

Spot intent stays explicit per provider: AWS uses `instance_market_type`, Azure uses `priority` with eviction and price settings, and GCP uses `provisioning_model` with a termination action. The committed fixtures default to regular/on-demand capacity for repeatable validation.

## Results And Provenance

Raw results are written to:

```text
results/raw/<run_id>/
```

Run manifests are written to:

```text
results/manifests/<run_id>.json
```

Manifests should be lightweight and should make each result attributable to the exact implementation revision that produced it. As the framework is implemented, manifests should record the implementation Git commit, thesis/design Git commit when available, config path, config hash or snapshot, provider, scenario, VM metadata, tool versions, raw result location, timestamps, and success/failure state.

Raw result bundles may become large and are normally excluded from Git. Manifests are intended to be version-controlled when practical.

Recurring scheduling and campaign sharding are outside this framework. An external reconciler, `tmux` process, or human invokes one campaign command at a time.

## Development And Testing

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest tests/unit tests/contract tests/integration
```

F01 CLI examples:

```bash
.venv/bin/python -m cloud_network_benchmark validate --config configs/experiments/exp-001-multi-provider.yaml --format json
.venv/bin/python -m cloud_network_benchmark resolve --config configs/experiments/exp-001-multi-provider.yaml --format json
.venv/bin/python -m cloud_network_benchmark dry-run --config configs/tests/exp-900-single-provider.yaml --format human
.venv/bin/python -m cloud_network_benchmark dry-run --config configs/tests/exp-903-three-provider-smoke.yaml --format human
.venv/bin/python -m cloud_network_benchmark init --config configs/tests/exp-900-single-provider.yaml --results-root results --format json
```

These F01 commands do not contact clouds or invoke Terraform, SSH, SCP, or FLENT.
