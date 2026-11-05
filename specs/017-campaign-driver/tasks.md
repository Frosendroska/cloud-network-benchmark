# Tasks: Remote campaign driver

**Input**: Design documents in `specs/017-campaign-driver/`.  
**Prerequisites**: spec, plan, research, data model and campaign contracts checked.  
**Tests**: Explicitly requested; fake provider/clock tests plus real saved-contract regression, no paid clouds.  
**Organization**: User stories independently checked; shared contracts land before dependent integration.

## Phase 1: Setup (Shared Infrastructure)

- [x] T001 Reconcile source-bound context and writer interfaces in `specs/017-campaign-driver/contracts/campaign.md`; keep Thesis read-only.
- [x] T002 Investigate advertised Python 3.9 imports, test supported interpreter where available, and correct deployment/package requirements in `pyproject.toml` and `docs/campaign-runner.md` (FR-016).

## Phase 2: Foundational (Blocking Prerequisites)

- [x] T003 Add backward-compatible `provider_run_envelope_templates` exact config/provider selection and actual child run binding in `src/cloud_network_benchmark/admission.py` and `src/cloud_network_benchmark/lifecycle_runtime.py`; preserve all funding/source/ownership/cleanup guards; add actual completed acceptance-scope mapping and external dated AWS expiry prices in `src/cloud_network_benchmark/providers/aws.py` with unchanged operational bounds (FR-003, FR-008).
- [x] T004 Define strict actual-input settings, plans and generation in `src/cloud_network_benchmark/campaign_inputs.py`; persist all manifest identities with `initialize_campaign(..., schedule_entry=entry)`, frozen config/schedule snapshots and one unchanged all-stage cost source; bind generated config admission id/date/source hash/estimate before initialization, use local saved N recommendation, and reference real bootstrap files (FR-003, FR-009).
- [x] T005 [P] Define durable journal, atomic replacement/directory fsync and exclusive single-driver lock in `src/cloud_network_benchmark/campaign.py`; states exactly `scheduled|starting|running|complete|failed|missed`, no unverified success (FR-010).
- [x] T006 [P] Add real shared-ledger/source-template tests in `tests/integration/test_campaign_admission.py`, proving two simultaneous reservations, preserved funding, stable session and rejection of source/session/cleanup conflicts; include a positive actual prepare→runtime-loader→admission test and acceptance-scope/source/hash rejection cases (FR-003, FR-008).

**Checkpoint**: Shared exact-source semantics and generator/driver API reconciled before integrated dispatch.

## Phase 3: User Story 1 - Prepare/check a stage (Priority: P1)

**Goal**: Requested setup/check/start interface with real inputs and no accidental paid launch.
**Independent test**: Missing real credentials/approval/prices/N/windows produce useful errors and zero resources/reservations.

- [x] T007 [US1] Implement campaign command routing/check/start-without-execute in `src/cloud_network_benchmark/campaign_cli.py` and `src/cloud_network_benchmark/cli.py` (FR-001, FR-002).
- [x] T008 [P] [US1] Provide one `plans/operator-settings.example.yaml` and four plans `plans/acceptance.yaml`, `plans/calibration.yaml`, `plans/day.yaml`, `plans/week.yaml`, referencing existing configs and leaving actual IDs/approvals/prices/N/windows pending (FR-001, FR-003).
- [x] T009 [US1] Generate registered acceptance config variants and evidence requirements in `src/cloud_network_benchmark/campaign_inputs.py`: V-S1, V-A/Z/G-FAIL, V-S3; ten measured seconds/phase, N=4, explicit actual placement membership and supervised controller loss (FR-004).
- [x] T010 [US1] Preserve exact retained calibration/DAY protocols and require common-N/actual size-export evidence through `src/cloud_network_benchmark/campaign_inputs.py`; never choose N/windows on runner (FR-005, FR-006).
- [x] T011 [P] [US1] Test read-only check, missing credentials/evidence/prices/N/windows, no-execute, acceptance variants and schedule hashes in `tests/unit/test_campaign_inputs.py` (FR-001 through FR-006, FR-009; SC-001).

## Phase 4: User Story 2 - Run/resume remote stage (Priority: P1)

**Goal**: Full bounded stage dispatch, honest recovery and disconnection independence.
**Independent test**: Traverse 56 fake-clock observations with simultaneous calls and durable journal/recovery handling.

- [x] T012 [US2] Dispatch 28 paired P0/P1 UTC slots through existing lifecycle invocation in `src/cloud_network_benchmark/campaign.py`, using measured pre-slot setup lead, unique private/runtime/state/results roots and shared session/ledger; no P2/P3 or automatic replacements (FR-007 through FR-009).
- [x] T013 [US2] Reconcile interrupted starting/running entries against original manifests/recovery/resource evidence in `src/cloud_network_benchmark/campaign.py`; never relaunch ambiguous paid measurement, backfill missed slots or discard failures (FR-010).
- [x] T014 [US2] Gate later dispatch on free disk and actual cleanup liabilities in `src/cloud_network_benchmark/campaign.py`, preserving original private cleanup journals and independent expiry; exclude runner from cleanup targets (FR-006, FR-013).
- [x] T015 [US2] Add current/next slot/counts/paths/disk/liabilities status, readable stage/per-run/provider logs and controller-only rotation plus peak memory/disk recording in `src/cloud_network_benchmark/campaign.py` (FR-012).
- [x] T016 [P] [US2] Add single systemd service `deploy/cloud-network-benchmark-campaign.service` and explicit start/status/log instructions in `docs/campaign-runner.md`; supervisor restart resumes driver reconciliation (FR-011).
- [x] T017 [P] [US2] Test all 56 observations, concurrent P0/P1 actual generator/runtime identity isolation, missed slots, low disk, cleanup blocking, crash before/after uncertain spawn and frozen source/config mutation in `tests/integration/test_campaign_driver.py` (FR-007 through FR-013; SC-002, SC-003).
- [x] T018 [P] [US2] Test service start/disconnect/restart contract in `tests/integration/test_campaign_service.py`; record real Linux test availability and retain live supervised validation as pending when unavailable (FR-011, SC-005).

## Phase 5: User Story 3 - Inspect/export evidence (Priority: P2)

**Goal**: Preserve available raw evidence and replay copied results locally without secrets/state.
**Independent test**: Copied export replays existing analyze with runner originals hidden.

- [x] T019 [US3] Seal available closed native files, preserve partial failures, export public manifests/portable descriptors/selection and stage summary in `src/cloud_network_benchmark/campaign_export.py`; stream hashing/copying, never load DAY bundles whole (FR-013, FR-014).
- [x] T020 [US3] Add optional verified closed-bundle backup and completion/failure command hook in `src/cloud_network_benchmark/campaign.py` or `campaign_export.py`; unset/failed protection explicitly visible (FR-015).
- [x] T021 [P] [US3] Test copied selection with existing local analyze after relocation, hidden runner paths, excluded private/secret files, corruption/transfer failure, backup/hook failure and peak memory/disk in `tests/integration/test_campaign_export.py` (FR-012 through FR-015; SC-003 through SC-005).

## Phase 6: Polish & Cross-Cutting Concerns

- [x] T022 Complete concise exact VM setup/stage/analysis/physical-controller-loss commands and credential-refresh boundaries in `docs/campaign-runner.md`; document actual pending cloud acceptance and V-S3 membership evidence, one registered failure attempt/provider, capacity/lead measurement and remaining data-loss limits (FR-004, FR-011, FR-015, FR-016).
- [ ] T023 Run campaign tests and appropriate existing admission/lifecycle/analysis regressions; record command/results, interpreter, resource peaks, skipped native/systemd checks and exact tested revision in `docs/validation/campaign-driver.md`.
- [ ] T024 Perform actual SpecKit convergence against `specs/017-campaign-driver/{spec,plan,tasks}.md` and implemented paths, return application repairs to owning writer, then rerun affected checks at exact final integrated revision.

## Dependencies and parallel execution

T001–T004 define shared inputs. T005 and T006 may proceed independently against agreed semantics. US1 produces concrete preparation; US2 depends on generator and journal; US3 depends on finalized lifecycle evidence. Root alone owns campaign_inputs/admission/lifecycle_runtime/plans/docs/service; implementer owns remaining campaign modules/CLI; checker owns tests. T008/T011, T016/T017/T018 and T021 may be authored independently once their interfaces are fixed. Shared-files tasks are serialized by owner. Do not parallelize dependent integrations.

## Implementation strategy

First validate concrete check/preparation and unchanged admission guards. Then prove two-call slot dispatch and restart without paid duplicates. Finally seal/export portable evidence and deliver the service/runbook. User asked for the full workflow; US1 alone is an intermediate increment, not completed delivery. Every remaining real credential, price, approval, N/window or empirical acceptance input stays an actionable blocker rather than a fake default.

## Phase 7: Convergence

- [x] T025 Record the sampled minimum free disk and peak observed shared-filesystem usage delta during lifecycle execution, including the final sample and explicit sampling scope; add a deterministic fake-process/disk regression in `tests/integration/test_campaign_driver.py` per FR-012 / T015 (partial).
- [x] T026 Record backup failure separately from verified local export state and expose the actual failure through campaign status; strengthen the closed-bundle backup corruption regression in `tests/integration/test_campaign_export.py` per FR-015 / T020 (partial).
