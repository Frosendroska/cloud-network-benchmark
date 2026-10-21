# Tasks: F01R V2 shared contracts

**Input**: `specs/003-v2-shared-contracts/` spec, plan, research, data-model, shared contracts and quickstart.
**Prerequisites**: Merged R00 at e7fc15f; no unfinished-sibling dependency. Tests are explicitly
required by FR-023/FR-024. One implementer/writer; do not revert other edits or modify historical specs.

## Format: `[ID] [P?] [Story] Description`

All task paths are repository-relative. `[P]` means only separate-file work with no incomplete
prerequisite; it is a planning opportunity, not authorization to delegate this assignment.
Data-model.md and contracts/shared-contracts.md are normative for every listed field/constraint.

## Phase 1: Setup

- [X] T001 Record the base/environment and known 203-pass/3-failure baseline in docs/f01r-offline-validation.md; keep specs/001-core-offline-contracts/ and specs/002-vm-access-bootstrap/ byte-identical (FR-001, FR-024).

## Phase 2: Foundational contracts

- [X] T002 Add V2 common enums/identity/status records in src/cloud_network_benchmark/contracts/common.py and exports in src/cloud_network_benchmark/contracts/__init__.py, with `schema_version: 2` on public envelopes, identifier pattern `^[A-Za-z0-9][A-Za-z0-9._-]{0,191}$`, finite Evidence values, explicit pending statuses and preserved existing execution/cleanup enum values; add focused tests/unit/test_v2_common.py (FR-001, FR-010, FR-017).
- [X] T003 Define strict ScheduleEntry and AdmissionRequirements in src/cloud_network_benchmark/contracts/admission.py: config-id pattern `^[a-z0-9][a-z0-9_-]{0,63}$`, aware-or-null scheduled time, original row/header/hash evidence, exact 1200/1800/87600-second caps, `$300`/`$50`, dated source evidence and scenario/mode-derived prerequisites; validate fields only, no CSV reader/budget admission (FR-002, FR-008, FR-009, FR-019).
- [X] T004 Define mode-aware ArtifactLayout/ArtifactInput declarations in src/cloud_network_benchmark/contracts/artifacts.py using contracts/shared-contracts.md paths, normalized root-contained POSIX destinations, producer/unit identity and `required/optional/not_expected`; separate concrete path from dynamic chunk template and remove FLENT slots (FR-003, FR-014).

## Phase 3: User Story 1 - Describe V2 intent (Priority: P1, MVP)

**Independent test**: Resolve four modes and all ten configs; invalid/version/secret/provider cases
fail without writes; resolved pending intent does not invent N/date or admission. Exercise the
config library at this checkpoint; full CLI/rendering/schema proof closes in US3, not required to
make incomplete US2 manifest imports work during US1.

- [X] T005 [P] [US1] Add four-mode positive/negative fixtures in tests/fixtures/configs/v2-mode-cases.yaml and focused tests/unit/test_v2_modes.py for exact 10/60/86400 measured seconds, 5-second warm-up, 10-second pauses (day 0), 60-second stabilization, 64-byte/1000-mps probes, fixed TCP settings, 12 calibration counts and common-N pending/frozen rules (FR-003, FR-004, FR-005, FR-023).
- [X] T006 [US1] Migrate CampaignConfiguration/BenchmarkPlan/PhaseConfiguration/CommonNState in src/cloud_network_benchmark/contracts/campaign.py to data-model.md fields: literal V2, distinct config_id, aware-or-null schedule, `pending` requires null N/source/plateau; `frozen` requires common N in 2/4/8 plus source identity and false plateau implies N=8; derive units in specified order and blockers instead of disabled phases or provisional research N (FR-001–FR-005).
- [X] T007 [US1] Extend explicit provider/VM intent in src/cloud_network_benchmark/contracts/campaign.py and source checks in src/cloud_network_benchmark/config.py for exact S1–S4 topology, chosen shapes/OS/disks/acceleration/network/security, requested AWS names versus observed physical IDs, On-Demand-only options and GCP COLLOCATED/no max_distance; standard research/calibration/day and standard acceptance require all three clouds, single-cloud only declared failure/replacement context (FR-006, FR-007).
- [X] T008 [US1] Extend load/resolve in src/cloud_network_benchmark/config.py and errors in src/cloud_network_benchmark/errors.py with explicit legacy/missing/unknown-version rejection, duplicate/unknown/secret rejection before snapshotting, typed optional ScheduleEntry equality checks, exact-byte source hash and deterministic resolved hash; preserve real field-path/redacted diagnostics and pure no-write resolution (FR-001, FR-002, FR-008, FR-013, FR-020).
- [X] T009 [US1] Migrate the four configs/experiments/exp-001-multi-provider.yaml, exp-002-cross-zone.yaml, exp-003-placement-optimization.yaml and exp-004-inter-region.yaml; add configs/experiments/exp-001-calibration.yaml and exp-001-diagnostic-day.yaml exactly per the ten-config contract matrix; scheduled_start=null and common_n pending, S4 includes all phases, no actual research result/paid admission (FR-003–FR-007, FR-009).
- [X] T010 [US1] Migrate configs/tests/exp-900-single-provider.yaml, exp-901-azure-single-provider.yaml, exp-902-gcp-single-provider.yaml and exp-903-three-provider-smoke.yaml to declared acceptance/failure contexts and native settings; update configs/README.md with real current Thesis table/ID anchors and registered-context mapping (FR-002, FR-006, FR-024).
- [X] T011 [US1] Update tests/unit/test_config_loading.py, tests/unit/test_config_resolution.py, tests/contract/test_config_traceability.py, tests/fixtures/configs/scenario-matrix.yaml and invalid fixtures for the complete ten-config/provider/scenario matrix; retain invalid locality/unknown fields/embedded secrets, disallow omitted S4 aggregate and remove obsolete heading/count assumptions (FR-001–FR-009, FR-023, FR-024).

## Phase 4: User Story 2 - Preserve attempts and evidence (Priority: P1)

**Independent test**: Use injected IDs/time and temporary roots/Git repositories to prove stable
observation linkage, new attempt/deployment identity, exact snapshots and collision/partial recovery.

- [X] T012 [P] [US2] Extend identity/collision/partial-init fixtures in tests/unit/test_ids_paths.py and tests/integration/test_initialize_cli.py for retained units, provider suffixes, replacement observation linkage, original/resolved/supplied-row hashes and preservation of pre-existing bytes (FR-010–FR-013, FR-023).
- [X] T013 [US2] Extend src/cloud_network_benchmark/ids.py and src/cloud_network_benchmark/paths.py only as needed for observation/deployment identity and new exclusive snapshot paths; retain run_id as attempt_id alias, `<campaign_id>-<provider>` first attempt and current collision/atomic allocation behavior; never allocate per-trial/chunk directories as new deployments (FR-010, FR-011).
- [X] T014 [P] [US2] Add real temporary normal/detached/linked-worktree/packed-ref and staged/unstaged/untracked provenance fixtures in tests/unit/test_git_provenance.py; assert deterministic hashes, honest unreadable/unavailable evidence and no diff/content/secret export (FR-013, FR-023).
- [X] T015 [US2] Extend RepositoryGitProvenance/StaticGitProvenance in src/cloud_network_benchmark/manifests.py with injected local-only snapshot() using commit/dirty/hash evidence; preserve commit() compatibility, canonical length-delimited tracked diff plus sorted nonignored untracked path/content hashing, explicit missing/race reasons and no cloud-runner dependency (FR-013, FR-020).
- [X] T016 [US2] Migrate CampaignManifest/ChildRunManifest/provenance and evidence updates in src/cloud_network_benchmark/manifests.py to data-model.md: V2 identity/mode/optional schedule, independent primary/retrieval/cleanup failures, default data=`not_validated`/comparison=`not_evaluated`, unavailable metadata/reservation/expiry and immutable parent/child chronology/equality checks (FR-010, FR-012, FR-013, FR-017).
- [X] T017 [US2] Extend initialize_campaign in src/cloud_network_benchmark/manifests.py for config.yaml/config.resolved.json and conditional exact schedule.csv snapshots with hashes and new identity links; preserve every existing fault-injection point/partial-parent recovery, fail before allocation for invalid input and keep init local even with pending prerequisites (FR-008, FR-011, FR-012, FR-013, FR-022).
- [X] T018 [US2] Migrate tests/contract/test_manifest_contracts.py, tests/unit/test_lifecycle.py, tests/unit/test_ids_paths.py and tests/fixtures/manifests/ to V2 and add meaningful independent-status/secondary-failure cases; adjust src/cloud_network_benchmark/lifecycle.py only if needed to preserve existing transition semantics, not to add an execution engine (FR-010–FR-013, FR-017, FR-023).

## Phase 5: User Story 3 - Stable independent-feature handoffs (Priority: P2)

**Independent test**: Public DTO imports and representative schema/fixture round-trips agree,
materialized fake artifacts and redaction are honest, previews match resolved intent with zero
external actions. Tests below may be prepared before their respective DTO implementations.

- [X] T019 [P] [US3] Add public safety/timing/diagnostic/artifact/status DTO contract cases in tests/contract/test_v2_handoffs.py, including unknown fields, unavailable/reset/gap values, mismatched identity/mode, partial connection, chronology and failed registration without invoking producers (FR-014–FR-019, FR-021, FR-023).
- [X] T020 [US3] Complete ReservationRequest/Evidence and ExpiryRequest/Evidence in src/cloud_network_benchmark/contracts/admission.py per data-model.md, with one campaign reservation, required known evidence for reserved/registered states, AWS/GCP `stop` versus Azure `deallocate`, pre-create target selector, cap-policy evidence and independent unresolved cleanup; no implementation of F11/provider side effects (FR-009, FR-019).
- [X] T021 [US3] Add CommandEvidence/ClockMapping/MeasurementTiming/TimingGap/DiagnosticSample/VmMetadata in src/cloud_network_benchmark/contracts/measurements.py exactly per data-model.md, including finite bounds, 0..100 CPU percentages, nonnegative deltas, unavailable reset values, actual UTC/monotonic evidence and static actual identity; do not collect counters, infer overlap thresholds or compute metrics (FR-013, FR-015, FR-016).
- [X] T022 [US3] Migrate src/cloud_network_benchmark/contracts/deployment.py to V2 identity/intent and safety handoffs, required known scenario even in partial output, management host distinct from private benchmark address, nonblank authentication/host-key refs and canonical terraform-outputs.redacted.json path; preserve complete-or-explained-partial rules and no remote execution (FR-006, FR-010, FR-018, FR-019).
- [X] T023 [US3] Complete ArtifactRecord in src/cloud_network_benchmark/contracts/artifacts.py and mode/unit artifact_inputs in src/cloud_network_benchmark/contracts/validation.py: verified requires `closed=true` plus observed size/hash, schedule not_expected when absent, validation/seal outputs optional until produced, dynamic day templates are not existing files; reject traversal/symlink escape and duplicate concrete artifact identity (FR-014, FR-020, FR-021).
- [X] T024 [US3] Migrate ValidatorInput and add ValidationFinding/ValidationResult in src/cloud_network_benchmark/contracts/validation.py with actual mode/units/native evidence, source and integrity links, separate statuses, extensible reason codes and partial diagnostics; eligible requires valid data, initialized data remains not_validated; no parsing/statistics/eligibility engine (FR-015–FR-017, FR-021).
- [X] T025 [US3] Harden durable redaction and preserve fake request/result protocol in src/cloud_network_benchmark/contracts/execution.py, src/cloud_network_benchmark/errors.py and tests/unit/test_execution.py; use explicit test materialization in tests/fixtures/commands/outcomes.json plus tests/contract/test_v2_handoffs.py to prove no size/hash success for missing files and retained partial evidence on failure/timeout/interruption; no shell execution or secret env/stdin/argv/output leakage (FR-020, FR-023).
- [X] T026 [US3] Update tests/contract/test_deployment_contract.py and tests/contract/test_remote_access_contract.py plus matching F02 test fixture construction to required scenario/valid provider-suffixed identities/host refs; preserve malformed-handoff rejection and existing remote behavior tests without editing access.py/bootstrap.py/readiness.py/retrieval.py (FR-018, FR-024).
- [X] T027 [US3] Complete public imports in src/cloud_network_benchmark/contracts/__init__.py and tests/contract/test_public_contracts.py for all declared contracts; remove misleading FLENT exports from active artifact expectations and verify no unfinished-feature import/dependency (FR-001, FR-014–FR-021).
- [X] T028 [US3] Migrate src/cloud_network_benchmark/dry_run.py and src/cloud_network_benchmark/cli.py human/JSON validate/resolve/dry-run/local-init output to V2 mode/units/native artifacts/identity/status/admission blockers, honest pending N/time/row, and planned external actions named Sockperf/iperf3; reserve nothing during preview, execute no cloud actions and add no run/schedule executor (FR-003–FR-009, FR-017, FR-022).
- [X] T029 [US3] Implement deterministic public-schema export/check in src/cloud_network_benchmark/contracts/schema.py for the 15 exact schema filenames and models in contracts/shared-contracts.md, literal V2/unknown-field/evidence/numeric/mode constraints and documented cross-record runtime checks; generate specs/003-v2-shared-contracts/contracts/*.schema.json without changing historical schemas (FR-001, FR-023).
- [X] T030 [US3] Migrate tests/contract/test_campaign_config_contract.py, test_artifact_contract.py, test_schema_integrity.py and test_validation_contract.py to active V2 schemas/records; check all four modes/ten configs plus invalid structural/semantic corpus, representative public DTO round-trips and schema drift, preserving separate cross-record/real-file validation checks (FR-001, FR-003–FR-007, FR-014–FR-021, FR-023).
- [X] T031 [US3] Update tests/unit/test_dry_run.py, tests/integration/test_validate_resolve_cli.py, test_dry_run_cli.py, test_initialize_cli.py and test_offline_boundaries.py to prove matching human/JSON intent, complete mode/provider matrix, safe pending defaults, synthetic-only frozen fixtures, version rejection before writes and zero cloud/SSH/SCP/benchmark calls in local flows (FR-022, FR-023, FR-024).

## Phase 6: Polish and cross-cutting acceptance

- [X] T032 Re-run the full quickstart.md suite/schema-check/CLI/diff commands from the assigned worktree with explicit PYTHONPATH/THESIS_ROOT, fix migration failures within the declared scope, and record exact head/environment/commands/exit/results and remaining limitations in docs/f01r-offline-validation.md; do not mark F10/cloud readiness or silently skip the three baseline failures (FR-023, FR-024; SC-001–SC-005).
- [X] T033 Reconcile tasks/checklists/public docs with final delivered contracts in specs/003-v2-shared-contracts/ and docs/f01r-offline-validation.md, preserve historical files and commit scoped work for Spec Kit convergence; implementation must not change reviewer-owned checklist markers or amend research/governance to hide failures (FR-001, FR-023, FR-024).

## Dependencies & Execution Order

T001 → T002 → T003/T004 → US1 (T005–T011) → US2 (T012–T018) → US3
(T019–T031) → T032/T033 → convergence. Within each story, tests precede their implementation.
T006/T007/T008 share files and are sequential. T013/T015/T016/T017 share manifest/identity paths
and are sequential. T020/T021/T022/T023/T024 can be prepared against declared foundation records,
but run sequentially in this one-writer worktree; T024 follows T021/T023. T027 follows all DTOs;
T028 follows US1/US2 and DTO completion; T029/T030 follow all exported DTOs; T031 integrates T028.
No sibling branch or cloud dependency. Later feature consumers start only after validated merge.

## Parallel examples by story

US1: after foundations, T005 writes only the new mode fixture/test files while provider fixture
expectations can be prepared separately; shared campaign/config implementation remains serial.
US2: T012 existing init/identity tests and T014 new Git test file are independent after US1.
US3: T019 new handoff test file and schema-case design can be prepared independently after US2;
T025 also touches that handoff file, so cannot overlap T019. No active parallel writers are requested.

## Implementation strategy

MVP is US1: trustworthy configuration/resolve intent for four modes and the full provider/scenario
matrix. It can be checked alone; it does not unblock dependent features or authorize clouds.
Then US2 preserves local evidence; US3 closes public handoff/schema/CLI consistency. Finish all
stories before convergence/merge. Maintain test semantics rather than old field names; no gratuitous
new framework, broad refactor, generated research evidence or automatic conversion of legacy data.
