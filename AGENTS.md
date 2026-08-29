# AGENTS.md

This is the executable implementation repository for the Master's thesis "Benchmarking Network Performance across Clouds".

Canonical repository path: `/Users/katya/Desktop/University/Masters/cloud-network-benchmark`.

The thesis knowledge-base and project-management repository is a sibling repository at `../Thesis`. Treat it as read-only by default.

## Repository Boundary

This repository owns:

- Terraform for AWS, Azure, and GCP deployments
- provisioning/bootstrap code
- benchmark orchestration
- FLENT, iperf, ping, and related tool invocation
- executable experiment configuration
- machine and cloud metadata collection
- raw measurement collection
- run manifests
- validation tooling and tests
- implementation documentation
- implementation-coupled preprocessing

The `../Thesis` repository owns:

- research questions, methodology, and experiment design
- project tracking and advisor material
- thesis-specific statistical analysis and interpretation
- canonical thesis figures and tables
- thesis writing and presentation material

Do not copy the Thesis repository here. Do not create a submodule. Do not nest one repository inside the other.

## Research Source Of Truth

Before implementing experiments or changing benchmark behavior, read the relevant files in:

- `../Thesis/DECISIONS.md`
- `../Thesis/STATUS.md`
- `../Thesis/experiments/`
- `../Thesis/tracking/`

Preserve thesis terminology such as `same_zone`, `cross_zone`, `inter_region`, and `placement_optimization`.

If implementation reveals an impossible, ambiguous, or inconsistent design requirement, surface the mismatch clearly instead of silently changing the research design.

## Experiment And Result Contract

Executable configs belong under `configs/experiments/`. Each config must include a stable `experiment_id` that maps back to the thesis-side experiment design.

Raw result bundles belong under `results/raw/<run_id>/` and are normally not committed to Git.

Lightweight run manifests belong under `results/manifests/` and should be version-controlled when practical. Every run manifest should make the result traceable to:

- `run_id`
- `experiment_id`
- implementation Git commit
- thesis/design Git commit when available
- config path and config hash or snapshot
- provider, scenario, regions/zones, VM configuration, tool versions, timestamps, and success/failure state when available

Thesis-specific analysis belongs in `../Thesis/tracking/experiments/analyses/`, not in this repository. Canonical thesis figures belong in `../Thesis/tracking/experiments/figures/`.

## Implementation Rules

Keep the framework deliberately small unless the thesis design changes. The current baseline is provider-specific Terraform, minimal bootstrap, local orchestration, FLENT execution, local raw result retrieval, metadata capture, validation, and cleanup.

Do not add object storage, remote Terraform state, cloud-side agents, databases, queues, Packer, containers, or telemetry platforms unless the user explicitly asks or the thesis design is updated.

Implementation changes must be committed to this Git repository, not to `../Thesis`.
