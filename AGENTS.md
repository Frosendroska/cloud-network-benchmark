# AGENTS.md

This is the executable implementation repository for the Master's thesis
"Benchmarking Network Performance across Clouds".

Implementation repository: `/Users/katya/Desktop/University/Masters/cloud-network-benchmark`

Thesis context repository: `/Users/katya/Desktop/University/Masters/Thesis`

Use `../Thesis` when the repositories are adjacent. In a worktree elsewhere, use the canonical
absolute Thesis path above. Treat the Thesis repository as read-only unless the user explicitly
requests a cross-repository update.

## Project Goal

Build a deliberately small framework that reads an approved experiment configuration, provisions
exactly two comparable Ubuntu VMs on AWS, Azure, or GCP with provider-specific Terraform, runs
three sequential FLENT phases from VM A to VM B, collects lightweight host metadata and
diagnostics, retrieves immutable local results, writes a traceable run manifest, validates the
bundle, and cleans up resources on success or failure.

The current implementation phase ends when credential-free unit, integration, fake end-to-end,
failure-injection, and Terraform static checks pass. Real cloud smoke tests, calibration, campaign
scheduling, paid experiments, and thesis analysis follow as separate stages.

## Repository Boundary

This repository owns executable configs, Terraform, bootstrap, orchestration, benchmark and
collector code, implementation tests, raw results, manifests, validation, and implementation
documentation. The Thesis repository owns research questions, methodology, experiment design,
project tracking, thesis analysis, canonical figures and tables, writing, and presentation.

Do not copy the Thesis repository here, create a submodule, duplicate thesis analysis, or move raw
results into the Thesis repository. Connect the repositories with experiment IDs, run IDs, config
hashes or snapshots, Git commits, manifests, and result references.

## Required Context Routing

Before `$speckit-specify`, planning, or any change to benchmark behavior, read
`.specify/memory/constitution.md` and the feature-relevant sources below. The SpecKit command only
needs the feature goal and scope; this file supplies the routing context.

All paths in the following tables are relative to the Thesis context repository named above.

| Thesis source | Read when |
| --- | --- |
| `advisor/docs/design_document.md` | Always. Canonical project objective, measurement protocol, architecture, lifecycle, scenarios, validation plan, result bundle, limitations, and open questions. |
| `DECISIONS.md` | Always. Binding decisions and supersessions; newer decisions override older proposals. |
| `experiments/EXPERIMENTS.md` | For configs, benchmark behavior, calibration, provider scenarios, validation runs, and campaign preparation. |
| `tracking/M04_implementation.md` | For implementation scope, validation gates, feature order, and M04 completion criteria. |
| `tracking/framework_implementation_roadmap.html` | For F01-F10 dependencies, parallel worktree boundaries, per-feature goals, and the handoff to cloud validation. |
| `STATUS.md` and `tracking/TASKS.md` | When selecting the next feature or checking current priority, blockers, and completed work. |
| `tracking/M05_experiments.md` | After the offline implementation gate, when preparing smoke tests, calibration, scheduling, or paid runs. |
| `tracking/experiments/` | When real run manifests exist and implementation output must be connected to thesis-side run tracking. |

Use these secondary research notes only for the named topic; they provide background and MUST NOT
override the design document or `DECISIONS.md`:

| Research note | Read when |
| --- | --- |
| `literature/research_notes/2026-08-15_deep_research_metrics_and_tools.md` | FLENT behavior, metrics, and benchmark-tool details. |
| `literature/research_notes/2026-08-15_deep_research_placement_features.md` | AWS, Azure, or GCP placement scenario implementation and interpretation. |
| `literature/research_notes/2026-08-15_deep_research_equivalent_vm_selection.md` | VM shape, network-limit metadata, and cross-provider matching. |
| `literature/research_notes/2026-08-22_deep_research_framework_architecture.md` | Architecture, lifecycle, error handling, or testing background when the final design is insufficient. |

## Source Precedence

- This file and the constitution govern repository and implementation behavior.
- `DECISIONS.md` governs approved or superseded thesis choices.
- `advisor/docs/design_document.md` is the main whole-project design.
- `experiments/EXPERIMENTS.md` and milestone files refine execution status and validation work.
- Research notes are supporting context only.

If sources conflict, a required file is unavailable, or a requirement is impossible, stop and
surface the exact mismatch. Do not silently choose a different research design.

## SpecKit Workflow

- Use one bounded feature per branch, worktree, and Codex session.
- Run the full flow: specify, clarify, plan, tasks, analyze, implement, converge.
- Review `spec.md` after specify/clarify, `plan.md` after planning, and `tasks.md` before implementation.
- Generated feature artifacts live under `specs/<feature>/` and are committed with the feature.
- Complete and merge F01 shared contracts before creating parallel F02-F08 worktrees.
- Parallel features MUST depend only on merged F01 contracts, not on unfinished sibling features.
- F09 integrates F01-F08. F10 proves credential-free readiness for controlled cloud smoke tests.

## Non-Negotiable Implementation Rules

- Preserve `same_zone`, `cross_zone`, `placement_optimization`, and `inter_region` terminology.
- Keep duration, stream count, schedule, and optional scenario scope configurable until approved.
- Keep AWS, Azure, and GCP semantics explicit; share code only where it does not hide differences.
- Do not contact clouds or require credentials during the offline implementation gate.
- Always preserve failure evidence and attempt cleanup once resource creation may have started.
- Do not commit secrets, SSH keys, Terraform state or plans, or large raw result bundles.
- Do not add object storage, remote state, agents, databases, queues, Packer, containers, or
  telemetry platforms unless the user explicitly changes the design.
- Implementation changes belong in this repository; thesis interpretation and canonical figures do not.
