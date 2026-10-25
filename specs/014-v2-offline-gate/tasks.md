# Tasks: F10 V2 offline readiness gate

**Input**: [spec.md](spec.md), [plan.md](plan.md), [research.md](research.md), [data-model.md](data-model.md), [contracts](contracts/), [quickstart.md](quickstart.md).
**Tests**: Explicitly required by F10. Reuse existing full suites; add focused gate-behavior checks and the actual saved-bundle analysis bridge only.
**Ownership**: One implementer at a time in `/private/tmp/cnb-f10`. Owned paths are declared in plan.md. No application/shared-contract/Thesis/coordinator-state/GitHub changes. Do not revert others' edits. All boxes below mean implementation work, currently not run.

## Phase 1: Setup

- [ ] T001 Reconcile clean branch/base, actual six-root/provider/tool prerequisites and required named coverage in `docs/validation/f10-offline-gate.md`, using `specs/014-v2-offline-gate/contracts/coverage.md`; record observed facts separately from validation results (FR-001–004, FR-010, FR-015).
- [ ] T002 Establish focused injected gate test helpers in `tests/unit/test_offline_gate.py` for Git/tool/subprocess/time/JUnit outcomes without invoking a recursive real suite or external resources (FR-002, FR-011–012).

## Phase 2: Foundational — safe command and evidence boundary

- [ ] T003 Implement planned argparse/help, fixed repository root, explicit local tool/mirror prerequisites, new-output/collision guards and clean HEAD/tree preflight in `scripts/validate_offline.py`; missing prerequisites must be incomplete/nonzero with no install/init fallback (FR-001–002, FR-010–011).
- [ ] T004 Implement filesystem-only Terraform CLI configuration/checkpoints-disabled environment and exact committed-lock/local-provider checks in `scripts/validate_offline.py`; permit no direct installation/network fallback, cloud CLI or live plan/apply/destroy (FR-002, FR-004).
- [ ] T005 Implement plain command result/log/hash recording and safe allowlisted host/tool/package/Thesis-version metadata in `scripts/validate_offline.py`; `format_version=1`, states `passed/failed/not_run`, no whole environment or credentials, no overwriting prior evidence (FR-007, FR-010–011).

## Phase 3: User Story 1 — Reproduce the offline gate (P1)

**Goal**: One real command runs every required local scope at an unchanged clean code revision.
**Independent test**: Focused injected guard/error tests, followed by the complete committed-code command in Phase 6. No hardcoded baseline counts.

- [ ] T006 [US1] Add dirty/untracked checkout, changed HEAD/tree, missing dependency/tool/cache, existing evidence/source collision and secret-like environment cases in `tests/unit/test_offline_gate.py`; assert no unsafe command/installer dispatch and accurate nonzero evidence (FR-001–002, FR-010–011; SC-004).
- [ ] T007 [US1] Wire full pytest/JUnit, canonical schema `--check`, diff check and all six Terraform fmt/validate commands in `scripts/validate_offline.py`; add explicit subprocess deadlines, preserve nonzero/timeout/interruption and mark dependent omitted scopes not run (FR-003–004, FR-011; SC-001).
- [ ] T008 [US1] Wire only existing four Azure/GCP mocked plan suites in `scripts/validate_offline.py`, checking actual test inputs use mocked providers and plan-only runs; document absent AWS mock suites rather than fabricate checks (FR-002, FR-004).
- [ ] T009 [US1] Evaluate JUnit counts, every required named coverage identity and four exact mode IDs from `specs/014-v2-offline-gate/contracts/coverage.md` in `scripts/validate_offline.py`; missing/deselected/empty/malformed required evidence is incomplete/nonzero even after pytest exit 0 (FR-003, FR-005–007, FR-010–012; SC-001–002, SC-004).
- [ ] T010 [US1] Classify only the two exact unavailable-Linux-`/proc` Darwin cases as separate not-run platform gaps in `scripts/validate_offline.py`; all other required skips fail, and native Linux/cloud verified flags remain false (FR-011–012; SC-004).
- [ ] T011 [US1] Cover command failure/timeout/interruption, malformed/empty/missing JUnit, omitted required cases, exact known versus unexpected skips, and ending dirty/revised source state in `tests/unit/test_offline_gate.py`; assert retained logs/hashes and honest counts/status (FR-001, FR-010–012; SC-004).
- [ ] T012 [US1] Run `tests/unit/test_offline_gate.py`, script `--help` and syntax/import checks; record exact commands/exits in `docs/validation/f10-offline-gate.md`, distinguishing focused results from the unrun full gate (FR-010–011; SC-001, SC-004).

## Phase 4: User Story 2 — Integrated native evidence through reports (P1)

**Goal**: Actual default-factory saved bundles feed F12 for every mode; required failure cases remain executed and source/report evidence is unchanged.
**Independent test**: Existing `test_f09_multi_provider_factory.py` four successful mode parameters with the appended saved-bundle report assertions, plus full existing failure/native/source suites.

- [ ] T013 [US2] Extend only the successful four-mode tail in `tests/integration/test_f09_multi_provider_factory.py` to build a selection from its actual saved `validation/input.json` and validation results after `finalize_production_batch`; call existing F12 analysis/output APIs with stable analysis revision, reusing every current factory/native source (FR-005, FR-008, FR-015; SC-002).
- [ ] T014 [US2] Assert all three providers/run/experiment/attempt/deployment/config/schedule/source identities through F12 in `tests/integration/test_f09_multi_provider_factory.py`; retain calibration comparison ineligibility, separate retained-day context/no idle inflation, actual mode durations and unknown costs (FR-007–008, FR-015; SC-002).
- [ ] T015 [US2] Hash saved inputs/native roots, write two report trees from identical/reordered selection and compare complete generated CSV/JSON/HTML/PNG/SVG file sets/bytes in `tests/integration/test_f09_multi_provider_factory.py`; assert all source hashes unchanged (FR-009; SC-003).
- [ ] T016 [US2] Run the existing default-factory mode/failure/source/replay suites with the new bridge and update the named eleven-category evidence inventory/results in `docs/validation/f10-offline-gate.md`; preserve controller/server failure, deadlines/overlap/gaps/receiver/retrieval/budget/expiry/residual safeguards without duplicating tests (FR-005–009, FR-015; SC-002–003).

## Phase 5: User Story 3 — Separate paid-acceptance handoff (P2)

**Goal**: Deliver a source-backed future runbook without running a paid step.
**Independent test**: Check registration IDs/protocol/cleanup/prerequisites against current sources and actual CLI help; document unproved Linux/provider behavior and pending inputs.

- [ ] T017 [P] [US3] Write `docs/paid-acceptance.md` covering refresh/register/quota/complete-budget-lifetime/expiry prerequisites, V-S1 ten-second phases/N=4, V-A/Z/G-FAIL server/controller loss, later V-S3 and separately admitted V-S2/V-S4, strict source-built guest/receiver streaming/SSH/retrieval/full-cleanup evidence (FR-012–014; SC-005).
- [ ] T018 [US3] Complete `docs/paid-acceptance.md` with native Linux `/proc` and instrumented-tool obligations, CAL-S1 canonical orders/three sixty-second trials per count/≥95% common-N rule, measured log-growth versus 32-GiB disks/20-GiB per-cloud export gate, separate DAY/window-selection order and no automatic repeat/launch; keep real N/windows/prices pending (FR-013–015; SC-005).
- [ ] T019 [US3] Check `docs/paid-acceptance.md` against actual CLI help and current Thesis sources, recording exact read-only commands and findings in `docs/validation/f10-offline-gate.md`; clearly label any future command templates and execute no paid action (FR-013–014; SC-005).

## Phase 6: Final integration, evidence and handoff

- [ ] T020 Finish focused verification and commit all executable/test/config changes before the full run; commit the pre-run procedure in `docs/validation/f10-offline-gate.md` and return exact committed revision/clean status in a local log without making tracked files dirty or claiming the full gate yet (FR-001, FR-016; SC-006).
- [ ] T021 Run the single documented `scripts/validate_offline.py` command from clean final integrated sources with prepared tools/providers; retain all logs/JUnit/summary under a new local evidence directory and return their actual paths/results to the coordinator (FR-001–012, FR-016; SC-001–004, SC-006).
- [ ] T022 Record exact final command/revision/environment/counts/static/mock/replay outcomes, raw-log references/hashes and explicit two native-platform gaps/remaining paid limitations in `docs/validation/f10-offline-gate.md`; update `specs/014-v2-offline-gate/quickstart.md` to actual runnable paths and evidence (FR-010–014, FR-016; SC-005–006).
- [ ] T023 Review any post-gate change in `specs/014-v2-offline-gate/tasks.md` and `docs/validation/f10-offline-gate.md`: prove later edits are documentation only with unchanged executable/test/config/Terraform/source-patch content, or rerun the whole command after the last behavior change (FR-016; SC-006).
- [ ] T024 Return clean head SHA, exact checks/exits/logs, assumptions/limitations and implementation handoff in `specs/014-v2-offline-gate/tasks.md` evidence notes; leave the coordinator to run configured Spec Kit convergence and final integration/delivery, with zero cloud/paid launches (FR-014–016; SC-005–006).

## Dependencies and parallel opportunities

Setup T001–002 → foundation T003–005 → US1 T006–012. US2 T013–016 uses the existing merged factory; it can follow foundation independently of US1, but there is one writer. US3 T017–019 is documentation-only and independent of US1/US2; T017 is the sole `[P]` candidate and does not authorize another writer. Final T020–024 follows all stories; T021 follows the final code commit, T022–023 preserve honest tested-revision provenance. The coordinator integrates prerequisites before the definitive full run.

Parallel example by story: US1 command/evidence and its guard tests are deliberately sequential because the contract is shared; US2 output identity/hash assertions share one existing test tail and are sequential; US3 runbook drafting may overlap read-only validation waiting if there is still one writer. Do not spawn extra agents for this bounded implementation.

## Implementation strategy

MVP is US1's safe reproducible command; completion additionally requires the actual US2 lifecycle→analysis bridge and US3 handoff. Reuse existing failures instead of duplicating them. Run focused tests during development, then one complete command from the final clean code revision. Repair actual failures; rerun affected checks and the final full gate after behavioral edits. No application implementation, paid acceptance or extra independent review stage belongs to this plan.
