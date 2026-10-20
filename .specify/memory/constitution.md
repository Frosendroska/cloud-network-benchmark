# Cloud Network Benchmark Constitution

## Core Principles

### I. Thesis Traceability Is Mandatory

Every executable experiment config MUST contain a stable `experiment_id` mapping to the research
sources in the Thesis repository. Every accepted run MUST retain experiment, run, observation,
provider attempt and shared-deployment identity as applicable; executed YAML configuration and CSV
schedule entry; configuration hash; implementation Git revision and dirty-state evidence; available
Thesis source revision; manifest; and immutable native artifacts. Missing provenance MUST remain
explicit. Derived reports MUST retain source identifiers and analysis revision; thesis claims MUST
extend that chain through their analysis, figure or table and finding.

Calibration trials and diagnostic windows MUST identify their retained deployment and MUST NOT be
represented as independent fresh deployments or separate launches. Preserve `same_zone`,
`cross_zone`, `placement_optimization` and `inter_region` terminology.

Rationale: measurements, failures and later claims must be reconstructable from their actual inputs.

### II. Deliberately Small Architecture

The framework MUST remain provider-specific Terraform, minimal cloud-init/bootstrap, a local Python
controller using OpenSSH/SCP, native Sockperf and iperf3 execution, local diagnostics and retrieval,
validation, deterministic native-file analysis and cleanup. Each selected cloud MUST use exactly
two comparable Ubuntu VMs: A sends private IPv4 traffic to receiver B. The controller remains
outside the measured path. Object storage, remote Terraform state, cloud-side agents, databases,
queues, Packer, containers and telemetry platforms MUST NOT be added without an explicit user
change to the design. The required independent provider expiry is a narrow safety mechanism,
not authorization for a general orchestration service.

V2 RTT MUST use Sockperf UDP full RTT with OS monotonic timing, measuring tenant-observed
socket-to-socket round trips including endpoint processing. TCP throughput MUST use receiver B's
iperf3 application bytes and measured elapsed time, with A-to-B TCP CUBIC and sender zerocopy.
Use the handoff's 64-byte, 1,000-mps probes and exclude warm-up/omitted intervals. The runner MUST
wait for Sockperf's test-start notification, then two seconds before TCP, and record actual
measurement windows; process launch plus a sleep is not overlap evidence.

Fresh short research experiments MUST run sequential idle, single-flow and common-N aggregate
phases, each with 60 measured seconds, five-second warm-up and ten-second pauses between phases.
Acceptance MUST use ten measured seconds per phase, with N=4 before calibration. Calibration
MUST retain a separate same-zone pair per cloud and test 1/2/4/8 streams three times each using
the fixed common orders in the current handoff. Select the smallest common N in 2/4/8 reaching
at least 95% of each cloud's best tested median; otherwise use N=8 with explicit no-common-plateau
evidence. Research MUST keep that common N fixed across providers.

The diagnostic day MUST retain another pair per cloud for one uninterrupted 86,400-second
multi-flow TCP measurement after one warm-up, with streaming receiver output and local Sockperf
chunks of at most 300 seconds. Chunk rotation MUST NOT restart TCP. Calibration and diagnostic
modes MUST NOT invent idle baselines or latency inflation. Both VMs MUST collect the prescribed
local host diagnostics once per second and static metadata once; no CloudWatch or automatic
performance-warning thresholds are required.

Rationale: distinct modes preserve the designed workload and deployment unit without building a
benchmark platform or claiming absolute link capacity.

### III. Immutable Results And Honest Provenance

After validation assigns a `run_id`, every run attempt MUST create a manifest under
`results/manifests/`, including failed and interrupted attempts. Record available config and
schedule identity, provider/scenario and VM metadata, actual commands and tool/image/source
identity, UTC and monotonic timing mappings, clock estimates, measurement windows, raw locations,
checksums, costs/reservations, failures and cleanup state. Execution success, data validity and
comparison eligibility MUST remain distinguishable. Code MUST NOT fabricate missing values or
silently reinterpret historical FLENT bundles as V2 evidence.

Raw outputs MUST remain immutable under `results/raw/<run_id>/`: native Sockperf CSV and separately
reported diagnostic counts, iperf3 client JSON/JSONL and receiver JSON/JSONL, stdout/stderr, local
host diagnostics, metadata and configuration/schedule snapshots. Lossless compression is allowed;
compulsory Parquet conversion is not. Files MUST be saved locally during measurement, closed at
measurement end, then copied from both VMs and verified before normal deletion. Do not add
periodic off-VM copying or automatic disk-retention/recovery workflows.

Validation and analysis MUST preserve slow results, partial valid intervals, acquisition gaps and
separate dropped/duplicate/reordered counts. CSV row counts MUST NOT be treated as exact packet
loss. RTT quantiles MUST use underlying samples, never averages of quantiles. The diagnostic day
requires the full TCP interval and at least 99% RTT time coverage for completeness; aligned
one-second plots require estimated clock error at most 100 ms. Further short-coverage and
comparison policies MUST be declared by the owning feature before implementation.

Rationale: immutable native evidence and honest status prevent incomplete or incomparable data
from becoming accepted observations. Large raw bundles are excluded from Git; small non-secret
manifests may be tracked.

### IV. Provider-Explicit Implementation

AWS, Azure and GCP MUST retain explicit Terraform, placement, image, zone, acceleration,
network-limit, billing and cleanup semantics. Share code only when it removes actual duplication
without hiding these differences. Match compute shape without claiming equivalent hardware,
network entitlement or placement controls. Use the selected On-Demand Ubuntu 24.04 amd64 shapes
and settings in the current Thesis sources; do not silently substitute zones, VM types or CIDRs.

Research MUST coordinate all three clouds on a shared UTC schedule. P0/S1 and P1/S3 each comprise
28 fresh-pair experiments, four per day for seven days, concurrently in the same week/windows
with independent resources, local state and results. Account for active reservations and four
VMs per cloud at overlapping slots. DAY is one experiment and one reservation; its analysis bins
are not launches. P2/S2 and P3/S4 configurations MUST be supported offline, but paid admission
requires higher-priority execution, analysis and budget review. There is no alternating placement
order or extra three-pair comparison block.

Provider compute expiry MUST stop AWS/GCP VMs and deallocate Azure VMs; Azure guest shutdown
alone is insufficient. Expiry does not prove deletion of disks, addresses or other billable
resources. Provider comparisons and placement interpretation MUST remain bounded to the tested
settings, deployments and dates.

Rationale: the instrument compares tenant-observed behavior while preserving provider differences
and the independent-deployment unit.

### V. Offline-First, Cleanup-Safe Validation

The implementation stage MUST end at F10's credential-free gate: relevant unit, contract,
integration, fake end-to-end, failure-injection and Terraform static checks plus deterministic
native-file report replay MUST pass. Terraform, SSH, SCP, Sockperf, iperf3 and other external
commands MUST have replaceable boundaries for success, failure, deadlines, interruption and
controller loss. Offline tests MUST NOT require credentials, contact clouds, generate real
benchmark traffic or incur cloud charges. Fakes MUST NOT be claimed as proof of real provider
permissions, networking, expiry or deletion.

Before resource-creating side effects, admission MUST reserve the complete workflow lifetime and
cost and register an independent provider-side compute expiry fallback. Account for incurred
costs, active reservations, remaining primary work, tax and uncertainty within the $300 total,
protecting the $50 buffer. Prices and overall lifetime caps MUST come from identified, dated
register/config inputs; refresh them before paid execution. Expiry registration failure MUST
prevent VM creation. Concrete expiry mechanisms, cap-start events and failed-copy handling MUST
be specified within the source bounds by the owning features; no destructive retention policy
or additional copying timer is implied.

Once resource creation may have started, every terminal path MUST attempt cleanup and preserve
available failure evidence. Retrieval or cleanup failures MUST NOT hide the primary failure.
Normal cleanup MUST delete and verify removal of VMs, disks, public addresses and other billable
experiment resources. Residual resources MUST keep cleanup open and block further launches.

Paid acceptance is a separate authorized stage after F10: registered ten-second all-cloud phases,
server-failure and abrupt controller-loss checks MUST prove real execution, retrieval, expiry and
cleanup before research. Calibration then selects N; measured log growth MUST establish disk and
export feasibility before the diagnostic day, whose analysis selects exact study windows.
Incomplete diagnostic evidence MUST NOT trigger an automatic repeat. No extra calibration,
duration-selection or probe-overhead campaign is authorized here.

Rationale: offline proof protects development cost; separate real acceptance supplies evidence
that simulated external boundaries cannot establish.

## Implementation Boundaries

This repository owns executable infrastructure/configuration, bootstrap, local orchestration,
benchmark and collector code, raw results/manifests, validation, reusable deterministic analysis
and implementation documentation/tests. Executable experiment configs belong under
`configs/experiments/`; raw bundles under `results/raw/<run_id>/`; manifests under
`results/manifests/`.

Thesis owns research questions, methodology, experiment design, advisor material, project tracking,
thesis interpretation, canonical figures/tables, writing and presentation. Use adjacent `../Thesis`
or `/Users/katya/Desktop/University/Masters/Thesis` from an external worktree; read it without
modification unless explicitly authorized. Thesis analysis/figures belong in its existing
`tracking/experiments/analyses/` and `tracking/experiments/figures/` locations. Do not copy/nest
repositories, create a submodule or duplicate large raw datasets into Thesis.

## Development Workflow

Before specification, planning or benchmark changes, read AGENTS.md routing, this constitution,
the current Thesis V2 design, latest DECISIONS.md, FRAMEWORK_IMPLEMENTATION_NOTES.md and relevant
experiment/milestone/prompts. Newer binding decisions supersede historical proposals. Historical
F01/F02 artifacts remain preserved evidence of the delivered baseline; migrate through F01R/F02R.
If current sources genuinely conflict or are impossible, surface the exact mismatch instead of
changing research choices. Formal advisor approval MUST NOT be inferred from implementation
permission or the user-revised design.

Use one bounded feature per branch/worktree and one writer at a time. R00 is constitution plus
routing reconciliation; application features use specify, clarify, plan, tasks, analyze,
implement and converge with committed artifacts. Under the explicitly authorized autonomous
workflow, agent artifact checks replace routine human review pauses. Merge R00 then F01R before
dependent features. Follow docs/autonomous/WORKFLOW.md for ownership, dependencies and acceptance;
workers MUST NOT modify coordinator runtime state or merge PRs.

Record ordinary engineering assumptions in the owning feature; do not reopen fixed protocol
choices. Keep evidence-dependent N, exact UTC windows and paid scenario admission unresolved
until their stated evidence exists. Source-backed software/build identity MUST be recorded;
ordinary locks are allowed without introducing a software-freeze approval gate. Changes affecting
behavior, contracts, provenance, validity, lifecycle or cost MUST include appropriate focused
checks. Document commands as executable only after verifying they exist. Secrets, credentials,
private SSH keys, Terraform state/plans and large raw bundles MUST NOT be committed.

## Governance

This constitution supersedes informal implementation practices. Amendments MUST preserve the
resolved Spec Kit template structure, record the version rationale and source-backed
supersessions in a durable governance/feature artifact, and verify the current Thesis design and
decisions before changing the research boundary or validity gates. The skill's temporary Sync
Impact Report MUST be reviewed and removed before commit; its durable rationale belongs in the
R00 governance handoff or later amendment record.

Versioning follows semantic versioning: MAJOR for incompatible principle redefinitions, MINOR
for new principles or materially expanded guidance, PATCH for non-semantic refinements. Specs,
plans, tasks, implementation and convergence MUST verify compliance. Non-compliance requires an
explicit source-backed design update or user-authorized governance amendment before proceeding.

**Version**: 2.0.0 | **Ratified**: 2026-09-24 | **Last Amended**: 2026-10-21
