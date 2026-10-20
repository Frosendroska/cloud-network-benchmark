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
exactly two comparable Ubuntu VMs per selected cloud with provider-specific Terraform, measures
Sockperf UDP full RTT and receiver-side iperf3 A-to-B TCP throughput, collects local host
diagnostics, retrieves immutable native results, writes traceable manifests, validates/replays
the bundle, and cleans up resources on success or failure. Fresh short research experiments use
idle, single-flow and calibrated-N aggregate phases. Separate retained-pair calibration and
continuous diagnostic-day modes have their own protocols; they are not three-phase short runs.

The current implementation phase ends when credential-free unit, integration, fake end-to-end,
failure-injection, Terraform static checks and deterministic native-file analysis replay pass at
F10. Schedule/budget admission and analysis are implemented offline in F11/F12 before F10.
Real cloud acceptance, calibration, diagnostic traffic, paid experiments and thesis interpretation
follow as separate authorized stages; this workflow does not launch them.

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
| `experiments/FRAMEWORK_IMPLEMENTATION_NOTES.md` | Always. Retained V2 command/settings detail, mode-specific timing, native evidence, diagnostics, safety and declared engineering clarification boundaries. |
| `tracking/IMPLEMENTATION_PROMPTS.md` | Always for R00 and feature planning. Current packet scope, ownership, dependencies, acceptance and supersessions. |
| `experiments/EXPERIMENTS.md` | For configs, benchmark behavior, calibration, provider scenarios, validation runs, and campaign preparation. |
| `tracking/M04_implementation.md` | For implementation scope, validation gates, feature order, and M04 completion criteria. |
| `tracking/framework_implementation_roadmap.html` | For R00/F01R/F02R/F03–F12 dependencies, worktree boundaries and the separate paid-validation handoff. |
| `STATUS.md` and `tracking/TASKS.md` | When selecting the next feature or checking current priority, blockers, and completed work. |
| `tracking/M05_experiments.md` | After the offline implementation gate, when preparing smoke tests, calibration, scheduling, or paid runs. |
| `tracking/experiments/` | When real run manifests exist and implementation output must be connected to thesis-side run tracking. |

Use these secondary research notes only for the named topic; they provide background and MUST NOT
override the design document or `DECISIONS.md`:

| Research note | Read when |
| --- | --- |
| `literature/research_notes/2026-08-15_deep_research_metrics_and_tools.md` | Historical tool/metric background only; its FLENT protocol does not override the current V2 Sockperf/iperf3 design. |
| `literature/research_notes/2026-08-15_deep_research_placement_features.md` | AWS, Azure, or GCP placement scenario implementation and interpretation. |
| `literature/research_notes/2026-08-15_deep_research_equivalent_vm_selection.md` | VM shape, network-limit metadata, and cross-provider matching. |
| `literature/research_notes/2026-08-22_deep_research_framework_architecture.md` | Architecture, lifecycle, error handling, or testing background when the final design is insufficient. |

## Source Precedence

- This file and the constitution govern repository and implementation behavior.
- `DECISIONS.md` governs approved or superseded thesis choices.
- `advisor/docs/design_document.md` is the main whole-project design.
- `experiments/FRAMEWORK_IMPLEMENTATION_NOTES.md` retains V2 implementation detail omitted from
  the concise design; `experiments/EXPERIMENTS.md`, current prompts and milestones refine execution
  scope, ownership and validation. Newer decisions override older details within these sources.
- The linked run register owns paid run lists, dated cost inputs and overall lifetime caps;
  refresh it before paid execution. Its local snapshot is not a live price quote.
- Historical F01/F02 specifications and FLENT notes describe delivered legacy foundations;
  preserve them but migrate through F01R/F02R. They do not override current V2 governance.
- Research notes are supporting context only.

If current sources genuinely conflict, a required source is unavailable, or a requirement is
impossible, stop and surface the exact mismatch. Apply explicit dated supersessions rather than
reopening historical proposals. Resolve ordinary design-consistent engineering choices in the
owning feature and record assumptions. Do not silently change research choices or infer formal
advisor approval from permission to implement the user-revised V2 design.

## SpecKit Workflow

- Use one bounded feature per branch, worktree, and Codex session.
- R00 runs the constitution skill followed by separately scoped routing/governance checks;
  it does not invent an application specification. See `docs/autonomous/r00-v2-governance.md`.
- Application features run the full flow: specify, clarify, plan, tasks, analyze, implement, converge.
- Check `spec.md` after specify/clarify, `plan.md` after planning, and `tasks.md` before implementation.
  Under the authorized autonomous workflow, agent artifact checks replace routine human pauses.
- Generated feature artifacts live under `specs/<feature>/` and are committed with the feature.
- Merge R00 then F01R shared-contract migration before dependent feature branches.
- F02R/F03/F04/F05/F06/F07/F08/F11 depend only on merged F01R contracts.
- F12 follows F08. F09 follows F02R and F03–F08 plus F11. F10 follows F09 and F12.
- Parallel features MUST NOT depend on unfinished siblings. The coordinator serializes shared
  contract amendments and merges; workers own only their assigned stage/worktree.
- F10 proves credential-free readiness for separate controlled paid acceptance, not cloud behavior.

## Non-Negotiable Implementation Rules

- Preserve `same_zone`, `cross_zone`, `placement_optimization`, and `inter_region` terminology.
- Preserve selected V2 settings: 60 measured seconds per short phase; ten for acceptance;
  specified common-N calibration; continuous 86,400-second diagnostic TCP with ≤300-second local
  probe chunks. Keep N and exact UTC study windows evidence-dependent, not invented defaults.
- Run P0/S1 and P1/S3 as 28 experiments each concurrently in the same week/windows with independent
  resources/state/results. P2/P3 execution remains gated; support their configurations offline.
- Preserve native Sockperf CSV, iperf3 JSON/JSONL, separate diagnostic counts, commands/timestamps,
  configuration/schedule snapshots and checksums. Copy and verify after measurement, then clean up.
- Keep one-second diagnostics local; no mandatory Parquet, CloudWatch, periodic remote copying,
  automatic performance-warning labels or software-freeze approval gate.
- Reserve complete workflow cost/lifetime and register independent provider compute expiry before
  VM creation. AWS/GCP stop and Azure deallocation do not replace full billable-resource cleanup;
  failed cleanup blocks new launches. Use identified dated inputs within $300 and protect $50.
- Keep AWS, Azure, and GCP semantics explicit; share code only where it does not hide differences.
- Do not contact clouds or require credentials during the offline implementation gate.
- Always preserve failure evidence and attempt cleanup once resource creation may have started.
- Do not commit secrets, SSH keys, Terraform state or plans, or large raw result bundles.
- Do not add object storage, remote state, agents, databases, queues, Packer, containers, or
  telemetry platforms unless the user explicitly changes the design.
- Implementation changes belong in this repository; thesis interpretation and canonical figures do not.


## Autonomous implementation workflow

For an explicitly launched autonomous V2 implementation, follow
`docs/autonomous/WORKFLOW.md` and use `docs/autonomous/KICKOFF.md` to start.
The coordinator may delegate eligible feature stages to model-configured subagents;
each feature still has one branch/worktree and one writer at a time. The coordinator
owns dependency scheduling, merges and factual Thesis progress updates. Under the
user's explicit workflow authorization, agent artifact checks replace routine human
review pauses; the constitution, research boundaries and tool permissions remain.

The setup is not execution evidence. R00 reconciles the legacy governance with the current V2
sources; its amendment and checks are recorded in `docs/autonomous/r00-v2-governance.md`.
Merge that governance and F01R before dependent V2 implementation. Historical FLENT artifacts
remain preserved; governance reconciliation does not establish benchmark or cloud readiness.
