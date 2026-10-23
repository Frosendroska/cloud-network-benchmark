# Tasks: F11 V2 schedule and budget admission

**Input**: `specs/011-v2-schedule-budget/` spec, plan, research, data model and interface contract.
**Tests**: Focused offline tests explicitly required by FR-016/SC-006. No real cloud/benchmark action.
**Ownership**: Only the paths declared in plan.md. No shared DTO/schema, config, CLI/lifecycle/provider/Terraform/Thesis or coordinator-state edits. One writer in the assigned worktree; `[P]` describes independent files, not permission to create another agent or worktree.

## Phase 1: Setup

- [x] T001 Create small source-scoped `tests/fixtures/f11/cost-inputs.json` and source notes under `tests/fixtures/f11/README.md` from research.md's actual bounded extracts:63 Required/9 Conditional/9 Deferred, $100.40/$1.95/$20.47/$25 funding and dated $147.82 forecast; include ranges/date attribution/raw-extract hashes/normalization and hash actual selected fixture bytes honestly. Preserve unknown support/refresh evidence, no workbook revision/hash or live quote; do not copy the workbook. FR-007/012/016.
- [x] T002 Add synthetic valid/pending/invalid week CSVs including `tests/fixtures/f11/schedule-pending.csv`, `tests/fixtures/f11/config-map.json`, scoped fixture configurations and event/policy cases under `tests/fixtures/f11/`; retain56 unique register entry IDs,28 paired slots, DAY/CAL single entries, failure/replacement/optional cases and explicit simulated N/UTC/support/prerequisite evidence. Leave shipped configs unchanged. FR-001–004/009–014; SC-001/004/005.

## Phase 2: Foundational

- [x] T003 Define only feature-local types/error reasons in `src/cloud_network_benchmark/admission.py` and inspection/skew records in `src/cloud_network_benchmark/scheduling.py`, reusing actual frozen DTO imports. Enforce V1 `identifiers and policy/source paths are nonblank; hashes are lowercase SHA-256; unknown fields fail`; V2 `money is finite nonnegative non-boolean Decimal; local JSON stores decimal strings; calculated ceilings round upward to 0.0001 USD`; V3 `timestamps are timezone-aware UTC; durations are finite nonnegative non-boolean seconds; cleanup_seconds is positive; provider leads are explicitly sourced and nonnegative`; V5 `selected providers/child identities/regions are unique and match the resolved campaign; namespace collisions fail; source/config/schedule fingerprints must agree`. Preserve unavailable config cost hash/estimate in sidecars and reject observed mismatches; no shared exports/schema changes. FR-001/005–009/016.
- [x] T004 Implement explicit local ledger initialization/read/transaction helpers in `src/cloud_network_benchmark/admission.py`: private regular nonsymlink files, durable initialized-scope marker in the adjacent lock file, explicit exclusive initialization, positive finite non-boolean lock_timeout_seconds, bounded kernel flock covering read/validate/decision/fsync/replace/parent fsync, no auto-reset/TTL deletion or recreation of missing initialized files. Enforce V7 `schema_version is 1; scope_id is fixed; ledger revision increases once per committed mutation; event and entry/campaign claim IDs are unique; existing source history and attempts cannot be erased`. Preserve ambiguous commit errors for locked readback. FR-009/016; SC-004.

## Phase 3: US1 — Inspect the intended schedule (P1, MVP)

**Goal**: Safe source-preserving preview without reservations. **Independent test**: Local CSV/config fixtures identify all slots/pending values and exact refusal reasons without external calls.

- [x] T005 [US1] Add focused cases in `tests/unit/test_f11_schedule.py` for exact UTF-8/BOM/quoted-multiline records/hashes, secret/unknown/duplicate fields and IDs, config/EXP/priority/replacement mismatch, naive/offset/pending timestamps,56-entry same-week alignment/windows, identity collisions/four-VM demand, retained single entry, past slots and skew at60/60+epsilon/300/300+epsilon/unavailable. FR-001–004/013/014/016; SC-001/005.
- [x] T006 [US1] Implement `load_schedule` in `src/cloud_network_benchmark/scheduling.py` per contracts/schedule-admission.md: exact bytes/record text, strict required/optional columns, source/row hashes, safe explicit existing configuration map, existing `resolve_campaign` and secret-safe errors; generate no YAML or runtime allocation. FR-001/016.
- [x] T007 [US1] Implement `inspect_schedule` in `src/cloud_network_benchmark/scheduling.py`: require28+28 entries with four windows/day over seven consecutive UTC dates and matching starts/all clouds for complete primary validation; retain unresolved N/dates/prerequisites as pending, report distinct execution/resource/state/result identity and four VMs/cloud; DAY/CAL stay one entry and unactivated past target stays missed without catch-up. FR-002–004/014; SC-001/005.
- [x] T008 [US1] Implement `assess_provider_skew` in `src/cloud_network_benchmark/scheduling.py` from actual per-phase starts only, preserving ≤60 target, (60,300] eligible finding, >300 exclusion and unavailable evidence without changing execution/data status. FR-013/016; SC-005.

## Phase 4: US2 — Reserve the whole experiment safely (P1)

**Goal**: Complete source-backed reservation before expiry registration/create. **Independent test**: Fake-time local ledger tests enforce budget/time/identity boundaries and prove no duplicate grant under concurrent calls.

- [x] T009 [US2] Add `tests/unit/test_f11_budget.py` cases for strict local values/source byte/hash matching, exact250/300 boundaries, funding moves/no double-count, active incurred/unspent, unknown/overrun liabilities, one/two-region AWS support, missing non-AWS support, mode minimum/registration/wait/shutdown/cleanup allowances, UTC steps/restart and earliest trigger. FR-005–010/016; SC-002–004/006.
- [x] T010 [P] [US2] Add frozen-value/API handoff checks in `tests/contract/test_f11_admission.py`: ReservationRequest/Evidence roundtrip, config unavailable vs observed source binding, exact AWS StructuredEvidence payload/actual fixture digest/region sorting, matching all-provider ExpiryRequest/Evidence/action/run/deployment/reservation/trigger ordering and separate RemoteDeadline/copy policy handoff. No shared fixture edits. FR-005–007/015/016; SC-003/006.
- [x] T011 [P] [US2] Add materialized concurrent-process/restart/fault-injection checks in `tests/integration/test_f11_ledger.py` for initialization collision, same/changed input retry, atomic before/after commit states, source changes retaining claims, duplicate creation grant, missing/corrupt/symlink files, bounded lock refusal and no external command invocation. Simulate long modes with fake time. FR-009/016; SC-004/006.
- [x] T012 [US2] Implement `BudgetLedger.reserve` and funding/charge-view validation in `src/cloud_network_benchmark/admission.py`; derive authoritative amounts/cleanup from locked ledger and AdmissionPlan, activate one immutable cap/source/policy record before registration and return same evidence/newly_reserved=false on identical retry. Enforce V6 `remaining funding and incurred liabilities are nonnegative; funding moves balance exactly; active unspent equals max(ceiling minus allocated incurred, 0); working commitment <=250 and working plus protected50 <=300 for new admission`. Unknown/overrun liability never frees headroom. FR-005/007–009/016; SC-002/004.
- [x] T013 [US2] Implement workflow minimum validation and `check_stage_fit` in `src/cloud_network_benchmark/admission.py` with initial-plan V4 `cap seconds are 1200 acceptance/short_study, 1800 calibration, 87600 diagnostic_day; stabilization_seconds is 60; measurement_shutdown_seconds is positive; measurement_seconds covers mode minimum plus shutdown grace` and V10 `nonpositive remaining time forbids new work; original cap and earliest provider trigger never move later; different controller session forbids new creation`. Cover75/225/950/86407-second default mode lower bounds plus supplied grace, registration/setup/wait/copy/cleanup, known stage names/completed-stage zero remainders, live monotonic enforcement and actual UTC evidence; healthy same-session concurrency is allowed while unresolved earlier-session work blocks new reservations. No cap reset or unfinished provider import. FR-005/006/009/010/014; SC-003/004.
- [x] T014 [US2] Implement `load_cost_inputs` and per-provider complete-workflow cost inclusion in `src/cloud_network_benchmark/admission.py`: hash exact saved scoped payload, preserve October14 forecast/refresh boundary, explicitly move sourced safety/cleanup/tax funding, consume actual merged AWS fixture $0.0139/unique region or $0.0278 S4 and emit matching AwsRuntimeSettings handoff without assuming free support/tiers. Missing provider support remains blocked for paid requests. FR-007/008/016; SC-002/006.
- [x] T015 [US2] Implement `claim_creation` in `src/cloud_network_benchmark/admission.py`: revalidate complete remaining fit and global cleanup, require all selected providers' exact registered expiry request/evidence sets and correct actions/earlier triggers before any creation, then durably grant each provider once. Replayed/uncertain claims cannot reauthorize; different controller session blocks. Actual registration/provisioning remains F09/providers. FR-006/009/010/016; SC-003/004.

## Phase 5: US3 — Reconcile failures and constrained follow-up work (P2)

**Goal**: Honest liabilities/failure evidence and gated follow-up. **Independent test**: Reconciliation events keep cleanup open, enforce conditional limits and never create automatic repeats/catch-up.

- [x] T016 [US3] Extend `tests/unit/test_f11_budget.py` and `tests/integration/test_f11_ledger.py` with idempotent/conflicting reconciliation, actual-vs-estimated billing, primary/secondary failures, partial provider cleanup, expiry-not-deletion, original failure/provider mismatches, one/original and three/provider across S1/S3, replacement chains, successful originals, DAY/CAL/S2/S4 refusals, optional scope/prerequisite review and bounded-copy handoff. FR-010–012/015/016; SC-004–006.
- [x] T017 [US3] Implement `reconcile` in `src/cloud_network_benchmark/admission.py` preserving source/charge events and original failure, marking unknown billing conservatively, clearing cleanup only for sourced verified absence of all billable attempt/safety resources, releasing only proved unused funding and retaining unknown/failed liabilities. Stop/deallocate/cap expiry never releases automatically; any open cleanup blocks every new launch reservation in scope. FR-008–010/016; SC-002/004.
- [x] T018 [US3] Implement replacement qualification/counting in `src/cloud_network_benchmark/admission.py` with V8 `replacement context identifies an original failed short S1/S3 provider attempt; one replacement per original and three per provider; no replacement chains or automatic retained/S2/S4 repeat`. Bind original ledger failure/manifest identity and failed provider, require cleanup/budget gate, allocate new complete fresh deployment and preserve original costs. FR-011; SC-005.
- [x] T019 [US3] Implement stage-specific prerequisite/optional admission in `src/cloud_network_benchmark/admission.py` with V9 `offline_simulation cannot supply paid authorization; missing prerequisite/support/refresh evidence blocks paid_request; deferred optional rows never imply admission`. Keep acceptance/calibration/DAY/short gates distinct and source-linked, P0/P1 protection before explicit P2/P3 execution/analysis/budget review, no automatic incomplete-day repeat or invented N/windows. FR-003/004/007/012; SC-001/005.
- [x] T020 [US3] Expose bounded final-copy/cleanup policy and remaining effective-deadline sidecar in `src/cloud_network_benchmark/admission.py` for F09/RemoteDeadline: at most one retry per failed artifact inside original remainder minus positive cleanup allowance, preserve partial evidence/primary failure and request normal cleanup. Do not execute retrieval/delete, add timers/retention/checkpoints or change `retrieval.py`; verify boundary in `tests/contract/test_f11_admission.py`. FR-015/016; SC-003/006.

## Phase 6: Polish and cross-cutting checks

- [x] T021 Add read-only `--help`/`preview` module entry point in `src/cloud_network_benchmark/admission.py` and coverage in `tests/integration/test_f11_ledger.py` for contract flags, deterministic JSON/reason identities, valid-pending exit0 vs invalid exit2, no ledger creation and zero external calls; existing CLI remains untouched. FR-001/003/007/012/014/016; SC-001/006.
- [x] T022 Document verified actual APIs/preview usage, shared local path, funding/cap/earlier-trigger/copy/restart assumptions and provider/paid evidence limits in `docs/f11-schedule-budget.md`; preserve research authority and no real-readiness claim. FR-016; SC-006.
- [x] T023 Run quickstart.md's actual focused/full-suite/schema/preview/diff checks after implementation; repair only owned paths and record exact head, environment, commands/exits/counts/no skips or explicit gaps in `docs/f11-schedule-budget.md`. Leave unavailable/not-run checks clearly distinct from passing; deliver to Spec Kit convergence without cloud/SSH/paid actions. FR-001–016; SC-001–006.

## Dependencies and execution order

T001–002 → T003–004 → US1(T005–008) → US2(T009–015) → US3(T016–020) → T021–023. US1 is the minimal read-only MVP; it can be validated before ledger behavior exists. US2 uses resolved inputs/fixtures from US1. US3 uses US2 reservations with fake failure records. No feature depends on unfinished siblings; only merged contracts/access/AWS behavior are referenced. F09 integrates after this feature is merged, and F10 proves final system readiness separately.

Within US2, T010 and T011 can be authored independently after T009 with separate files, then implementation T012–015 is serial because admission.py has one owner. US1's scheduling implementation and budget-test drafting can be independent after the foundation, but the assigned workflow retains one writer. US3's failure unit and integration cases can be drafted independently only if their earlier US2 file ownership is complete; no parallel marker implies overlapping file writes. No other parallel execution is needed.

## Requirement coverage

| Requirement | Task IDs |
| --- | --- |
| FR-001 | T002,T003,T005,T006,T021,T023 |
| FR-002 | T002,T005,T007,T023 |
| FR-003 | T002,T007,T019,T021,T023 |
| FR-004 | T002,T007,T019,T023 |
| FR-005 | T003,T009,T010,T012,T013,T023 |
| FR-006 | T009,T010,T013,T015,T023 |
| FR-007 | T001,T003,T009,T010,T012,T014,T019,T023 |
| FR-008 | T003,T009,T012,T014,T017,T023 |
| FR-009 | T002,T003,T004,T011,T012,T015,T017,T023 |
| FR-010 | T009,T015,T016,T017,T023 |
| FR-011 | T002,T016,T018,T023 |
| FR-012 | T001,T002,T016,T019,T021,T023 |
| FR-013 | T002,T005,T008,T023 |
| FR-014 | T002,T005,T007,T013,T021,T023 |
| FR-015 | T010,T016,T020,T023 |
| FR-016 | T001,T003–006,T008–012,T014–017,T020–023 |
| SC-001 | T002,T005–007,T019,T021,T023 |
| SC-002 | T009,T012,T014,T017,T023 |
| SC-003 | T010,T013,T015,T020,T023 |
| SC-004 | T004,T011,T012,T015–017,T023 |
| SC-005 | T002,T005,T007,T008,T016,T018,T019,T023 |
| SC-006 | T009–011,T014,T016,T020–023 |

23 tasks: setup2, foundation2, US1=4, US2=7, US3=5, final3. All22 requirements/buildable criteria covered; no unmapped tasks. Local data constraints V1–V10 are quoted in owning tasks. Tasks remain unchecked until implementation evidence exists.


## Phase 7: Convergence

- [x] T024 CRITICAL Validate a candidate's complete source-backed workflow cost in `src/cloud_network_benchmark/admission.py`: dated per-provider envelopes account for compute, disks, addresses, traffic/export, tax, uncertainty and expiry/cleanup support with explicit units/inclusion rules, exact resolved regions and exact funding-bucket totals. Saved provider-price rows are validated separately from synthetic offline fixtures; paid reservations reject synthetic evidence. Regressions cover missing components, wrong regions, underfunded rows and a correct dated provider-price boundary. No shared DTO changed.
- [x] T025 CRITICAL Track estimated/actual/final billing per selected provider; unknown charges remain within the active reservation. Funding release now requires final actual billing for every provider plus sourced all-provider absence and records terminal `released` evidence with a reason. Regressions exercise incomplete billing and complete release.
- [x] T026 CRITICAL Mark activation cleanup-open before expiry registration, retain it across controller restarts, and permit concurrent same-session primary reservations. Sourced absence across providers closes resource uncertainty; unknown resources and failure evidence keep launch blocks. Restart-before-registration is covered.
- [x] T027 CRITICAL Require explicit stage prefixes and every future allowance, allow current-stage progress only within the original amounts, and use fixed original-cap/earliest-trigger monotonic bounds. Tests cover omitted future work, completed-stage remainder and forward/backward wall-clock changes.
- [x] T028 Repair creation claims to validate the durable mode-specific allowance under the ledger lock, require active selected-provider claims and exact expiry registration ordering, and reject terminal/replayed grants. Acceptance, short study, calibration and diagnostic day receive mode-specific successful/insufficient/replay checks.
- [x] T029 Re-read cost source bytes on initialization and admission, persist the active cost source, reject unreconciled switches and support explicit reviewed source reconciliation while preserving prior source history and reservation allocations. Tests cover altered/missing bytes and source reconciliation.
- [x] T030 Look up existing durable claims before current missed-slot checks; exact retries after target return the same reservation without a new grant, while changed fingerprints conflict.
- [x] T031 Require source-linked completed acceptance scoped to the target config, acceptance mode and selected providers before later paid modes; keep the acceptance request itself separate. Tests cover failed and mismatched scope evidence and a matching evidence boundary. Synthetic prices/gates never authorize paid work.
- [x] T032 Require replacement campaigns to select only the provider whose original child attempt failed, preserve the original observation/failure links and reject replacement chains. Existing replacement-count and qualification tests remain active.
- [x] T033 Preview now emits deterministic invalid schedule/collision reason identities and exits 2 when inspection reports errors; valid pending intent remains exit 0. Duplicate retained calibration rows are covered.
