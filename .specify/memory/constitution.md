<!--
Sync Impact Report
Version change: 1.0.0 -> 1.1.0
Modified principles:
- I. Thesis Traceability Is Mandatory -> clarified run-level and claim-level traceability
- III. Immutable Raw Results And Provenance -> III. Immutable Results And Honest Provenance
- V. Validation Before Paid Campaigns -> V. Offline-First, Cleanup-Safe Validation
Added sections:
- None
Removed sections:
- None
Follow-up TODOs:
- None
-->
# Cloud Network Benchmark Constitution

## Core Principles

### I. Thesis Traceability Is Mandatory

Every executable experiment config MUST contain a stable `experiment_id` that maps to the
research-side design in the sibling `../Thesis` repository. Every accepted run MUST be traceable
through its experiment ID, config path and snapshot or hash, implementation Git commit,
thesis/design Git commit when available, run ID, manifest, and raw result bundle. Runs used as
thesis evidence MUST extend this chain through the analysis, figure or table, finding, and claim.
Implementation work MUST preserve thesis terminology including `same_zone`, `cross_zone`,
`inter_region`, and `placement_optimization`.

Rationale: measurements are useful only when their exact design, configuration, implementation,
and later interpretation can be reconstructed.

### II. Deliberately Small Architecture

The baseline framework MUST remain provider-specific Terraform, minimal VM bootstrap, local
lifecycle orchestration, FLENT execution, local raw result retrieval, metadata capture,
validation, and cleanup. Object storage, remote Terraform state, cloud-side agents, databases,
queues, Packer, containers, and telemetry platforms MUST NOT be added unless the user explicitly
requests them or the thesis design is updated.

Rationale: the framework is an experimental instrument for one thesis. A small architecture keeps
cost, failure modes, implementation time, and thesis explanation under control.

### III. Immutable Results And Honest Provenance

After config validation assigns a `run_id`, every run attempt MUST create a manifest under
`results/manifests/`, including failed and interrupted runs. The manifest MUST record available
run identity, config, provider, scenario, VM and cloud metadata, tool versions, timestamps, Git
provenance, raw result location, lifecycle state, failure details, and cleanup state. Missing or
unavailable values MUST be represented honestly; code MUST NOT fabricate values.

Raw benchmark outputs MUST be preserved under `results/raw/<run_id>/` without overwriting or
post-hoc editing, including raw `.flent.gz` files when produced. Manifests SHOULD remain small
enough to version-control when practical; large raw bundles are normally excluded from Git.

Rationale: immutable evidence and explicit failure state make results reproducible and prevent
later analysis from mistaking incomplete runs for valid observations.

### IV. Provider-Explicit Implementation

AWS, Azure, and GCP implementation paths MUST stay explicit unless a shared abstraction removes
real duplication without hiding provider-specific semantics. Terraform layouts, config fields,
metadata capture, placement controls, billing assumptions, region and zone names, and network
diagnostics MUST preserve provider differences.

Rationale: the thesis compares tenant-observed behavior across providers. Hiding provider-specific
constraints would weaken implementation correctness and result interpretation.

### V. Offline-First, Cleanup-Safe Validation

Before any test uses cloud credentials or creates paid resources, the relevant unit tests,
integration tests, fake end-to-end lifecycle tests, failure-injection tests, and static Terraform
checks MUST pass. Terraform, SSH, SCP, FLENT, and other external command execution MUST use
replaceable boundaries so local tests can exercise success, failure, timeout, and interruption
without contacting a cloud.

Once resource creation may have started, every terminal path MUST attempt cleanup. The manifest
MUST record cleanup status, and a cleanup failure MUST NOT hide the original run failure. A paid
experiment campaign MUST NOT begin until one controlled smoke observation succeeds on each cloud
and at least one intentional failure or interruption demonstrates honest failure recording and
cleanup behavior.

Rationale: credential-free validation protects the budget during development, while controlled
cloud smoke tests provide evidence that mocks cannot provide about permissions, provisioning,
networking, VM readiness, benchmark execution, retrieval, and destruction.

## Implementation Boundaries

This repository owns executable infrastructure, provisioning, bootstrap, benchmark orchestration,
tool invocation, executable experiment configs, raw measurement collection, metadata and
diagnostic collection, run manifests, implementation-coupled preprocessing, validation tooling,
tests, and implementation documentation.

The sibling `../Thesis` repository owns research questions, methodology, experiment design,
project tracking, advisor material, thesis-specific analysis, canonical thesis figures and tables,
writing, and presentation material. This repository MUST NOT copy thesis documents, duplicate
large raw datasets into thesis-facing files, create a submodule for `../Thesis`, or nest either
repository inside the other.

Executable configs belong under `configs/experiments/`. Raw result bundles belong under
`results/raw/<run_id>/`. Run manifests belong under `results/manifests/`. Thesis-specific analysis
belongs in `../Thesis/tracking/experiments/analyses/`; canonical thesis figures belong in
`../Thesis/tracking/experiments/figures/`.

## Development Workflow

Before implementing experiments or changing benchmark behavior, contributors MUST read the
relevant research source-of-truth files in `../Thesis/DECISIONS.md`, `../Thesis/STATUS.md`,
`../Thesis/experiments/`, and `../Thesis/tracking/`. If implementation reveals an impossible,
ambiguous, or inconsistent design requirement, contributors MUST surface the mismatch instead of
silently changing the research design.

Changes that affect benchmark behavior, result schemas, provenance, validation gates, cloud
resource lifecycle, or cost exposure MUST include focused tests or validation evidence appropriate
to their risk. Documentation MUST name real commands only after those commands exist. Secrets,
cloud credentials, SSH private keys, Terraform state, generated plans, and large raw result bundles
MUST NOT be committed.

Advisor-dependent parameters, including FLENT details, aggregate stream count, phase duration,
observation schedule, and optional scenario scope, MUST remain configurable until advisor feedback
and calibration justify freezing them. Implementation MAY follow the current advisor-review design
while those choices remain provisional.

## Governance

This constitution supersedes informal repository practices for implementation work. Amendments MUST
be made by editing `.specify/memory/constitution.md`, documenting the version change in the sync
impact report, and preserving the resolved Spec Kit template structure. Any amendment that changes
the research/implementation boundary or benchmark validity gates MUST be checked against the
current thesis-side design before adoption.

Versioning follows semantic versioning. MAJOR increments apply to backward-incompatible governance
or principle redefinitions, MINOR increments apply to new principles or materially expanded
guidance, and PATCH increments apply to clarifications, typo fixes, and non-semantic refinements.

Spec plans, tasks, code reviews, and implementation changes MUST verify compliance with the core
principles. Non-compliant work requires an explicit thesis-design update or user-approved
governance amendment before implementation proceeds.

**Version**: 1.1.0 | **Ratified**: 2026-09-24 | **Last Amended**: 2026-09-27
