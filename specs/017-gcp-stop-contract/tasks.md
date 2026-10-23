# Tasks: GCP STOP Contract Migration

**Input**: `specs/017-gcp-stop-contract/` design artifacts.
**Prerequisites**: Reviewed spec, plan, research, data model, contract and checklists. Before T001 the coordinator must verify the acquisition-contract merge and release this single shared-contract writer.
**Tests**: Explicitly required by FR-007. All tasks remain unchecked at planning handoff.
**Ownership**: Exact paths in plan.md; one writer in `/private/tmp/cnb-gcp-stop`. Preserve other worktrees' changes. No cloud, SSH, Terraform apply or benchmark traffic. No runtime-state/Thesis/PR/merge ownership.

## Phase 1: Setup

- [ ] T001 Record coordinator-verified acquisition merge SHA, integration base and writer release in `specs/017-gcp-stop-contract/validation.md`; verify this branch includes that merged prerequisite before any shared source/config/schema edits or export. Do not claim completion from an unmerged sibling or re-create its exports. [FR-008]

## Phase 2: Foundational checks

- [ ] T002 Reconcile the merged base against `specs/017-gcp-stop-contract/plan.md` and scan `src/`, `configs/`, `tests/`, `docs/` for active GCP termination-action references; record the active config inventory, unchanged AWS/Azure option controls, historical specs 001/002 baseline and reproduced runtime/schema debt in `specs/017-gcp-stop-contract/validation.md`. Preserve existing unrelated and historical material. [FR-003, FR-005, FR-006, FR-008]

**Checkpoint**: Prerequisite and ownership confirmed. No other shared-contract writer is active.

## Phase 3: User Story 1 - Declare the required GCP fallback (P1)

**Goal**: Both independent runtime input boundaries enforce governing STOP.
**Independent Test**: Construct valid GCP config and deployment envelopes separately; STOP succeeds and each forbidden mutation rejects. Tests must first expose current debt.

- [ ] T003 [US1] Add runtime cases to `tests/contract/test_gcp_stop_contract.py` from `specs/017-gcp-stop-contract/data-model.md`: exactly two required keys, `provisioning_model` MUST equal `STANDARD`, `instance_termination_action` MUST equal `STOP`; independently reject DELETE, omitted/null/empty/case-variant/unknown/boolean/number/list/mapping actions, non-STANDARD/null/omitted provisioning, extra preemptible true/false and unknown keys at config and direct deployment boundaries; prove valid envelopes first and retain explicit no-coercion/invalid-input controls. [FR-001, FR-002, FR-007; SC-001]
- [ ] T004 [US1] Amend existing validators in `src/cloud_network_benchmark/contracts/campaign.py` and `src/cloud_network_benchmark/contracts/deployment.py` to enforce exactly the existing two required GCP keys, STANDARD and STOP without defaults/coercion; preserve AWS/Azure behavior, field names/version, identity, cap and cleanup contracts. [FR-001, FR-002, FR-005]
- [ ] T005 [US1] Correct the existing GCP expected purchase-option cases in `tests/contract/test_deployment_contract.py` and any directly affected assertions identified by T002; retain STANDARD/STOP acceptance plus STANDARD/DELETE and SPOT/STOP rejection, AWS/Azure controls and independent direct deployment coverage from T003. Run the runtime-focused cases and record outcomes in `specs/017-gcp-stop-contract/validation.md`. [FR-001, FR-002, FR-005, FR-007; SC-001]

## Phase 4: User Story 2 - Use consistent published inputs (P2)

**Goal**: Published schemas, examples, resolution and previews agree.
**Independent Test**: Validate option mutations directly against published schemas and inspect all active examples/CLI previews. US2's corpus can be prepared independently; execution uses US1's migrated validators.

- [ ] T006 [US2] Extend `tests/contract/test_gcp_stop_contract.py` with the same mutation matrix through Draft 2020-12 validation of canonical config, resolved-campaign and direct deployment schemas; prove valid envelopes, mutate campaign provider configs and nested observation provider configs independently, and compare runtime acceptance at each boundary. Include all four valid modes and preserve existing AWS/Azure controls. First demonstrate schema debt. [FR-001, FR-002, FR-004, FR-007; SC-001, SC-003]
- [ ] T007 [US2] Add minimal GCP-only schema conditionals to owning models in `src/cloud_network_benchmark/contracts/campaign.py` and `src/cloud_network_benchmark/contracts/deployment.py`: exactly two required keys; `provisioning_model` MUST equal `STANDARD`; `instance_termination_action` MUST equal `STOP`. Preserve existing hooks/other-provider schemas and let nested ProviderConfiguration references inherit the constraint. Add no options class, mode or shared export platform. [FR-004, FR-005; SC-003]
- [ ] T008 [US2] Change only GCP action values to STOP in `configs/experiments/exp-001-multi-provider.yaml`, `exp-001-calibration.yaml`, `exp-001-diagnostic-day.yaml`, `exp-002-cross-zone.yaml`, `exp-003-placement-optimization.yaml`, `exp-004-inter-region.yaml` and `configs/tests/exp-902-gcp-single-provider.yaml`, `exp-903-three-provider-smoke.yaml`; include any newly merged active GCP examples identified by T002. Preserve STANDARD, AWS/Azure dictionaries, admission gates and all unrelated settings. [FR-003, FR-005; SC-002]
- [ ] T009 [US2] Regenerate the complete current canonical set in `specs/003-v2-shared-contracts/contracts/` using the existing exporter after T007/T008; commit only changed schema bytes, preserve merged acquisition schema content and run a repeat export plus drift check. Run T006's schema parity corpus. [FR-004, FR-005, FR-008; SC-003]
- [ ] T010 [US2] Add/update `tests/integration/test_dry_run_cli.py` and `tests/contract/test_gcp_stop_contract.py` assertions for every active GCP example: runtime/schema validation, resolution, deployment handoff and human/JSON preview retain STANDARD/STOP across four modes and S1-S4 where supported; inspect existing pending admission/calibration/schedule evidence, changed content-derived config hashes and sanitized rejection without external actions. Retain the distinction between STOP and all-resource cleanup in `specs/017-gcp-stop-contract/contracts/gcp-stop.md`; correct active reference text only if T002 proves it contradictory and coordinator assigns that path. [FR-003, FR-005, FR-006, FR-007; SC-002]

## Phase 5: Validation and handoff

- [ ] T011 Run every command in `specs/017-gcp-stop-contract/quickstart.md`: focused regressions, repeated deterministic schema export/check, four mode CLI previews and full offline pytest. Record actual command, exit, pass count, environment and exact source/head/base in `specs/017-gcp-stop-contract/validation.md`; distinguish not run from passed and do not claim paid/cloud/F10 proof. [FR-004, FR-007; SC-003, SC-004]
- [ ] T012 Run whitespace, owned-diff and unchanged historical specs 001/002 checks from `specs/017-gcp-stop-contract/quickstart.md`; verify AWS/Azure YAML dictionaries and unrelated settings against T002's merged baseline. Finalize `specs/017-gcp-stop-contract/validation.md` with assumptions, limitations and next-stage Spec Kit convergence. Preserve reviewer checklist markers; F07 implementation stays gated until coordinator-verified STOP merge. [FR-005, FR-006, FR-008; SC-004]

## Dependencies and execution order

```text
Verified acquisition merge + coordinator release
  -> T001 -> T002 -> T003 -> T004 -> T005
  -> T006 -> T007 -> T008 -> T009 -> T010 -> T011 -> T012
  -> Spec Kit convergence -> coordinator integration/checks/verified merge
  -> F07 implementation
```

US1 MVP is the two runtime boundaries, but no partial implementation may unblock F07. US2 completes schema/example compatibility before delivery. T006's schema checks use valid envelopes independent of CLI resolution to avoid masking a schema gap.

## Parallel examples and implementation strategy

No tasks carry [P]: the feature has one writer, repeated edits to the same models/test file, and shared canonical exports. US1's read-only corpus inspection can run alongside inspection of US2 schema cases; no parallel code writer is authorized. Implement the small runtime slice, then schema/config/CLI consistency, then all acceptance checks. No extra services, modes, research choices or independent review stage are needed.
