# F07 planning handoff

Completed 22 October 2026 in `/private/tmp/cnb-f07`, branch `codex/f07-v2-gcp`, feature directory `specs/009-v2-gcp-provider`. Planner-owned changes are confined to this directory. No application, shared-contract, research, coordinator-state or cloud changes were made.

## Revisions and source boundary

Prepared branch HEAD `a07c1c7140f158f6ae7230b298c41c6e82be974e` was clean and fast-forwarded to coordinator-assigned verified main `e03a07eaa5ed9ea372c695db19fd6fbcffdad024` before writing artifacts. Thesis HEAD observed `d9d1051e2c908f0665ea957ab4b0db9047fb6fbf`; routed current design/decisions/handoff/F07 prompt/experiments/M04/roadmap/status/tasks and active constitution were read. The final planning commit is the commit containing this handoff (record its SHA from git rev-parse HEAD after commit; no self-referential hash is fabricated here).

## Stages and artifact review

Actual installed skills read: speckit-specify, clarify, plan, tasks, checklist and analyze. Explicit context on scripts: `GIT_BRANCH_NAME=codex/f07-v2-gcp SPECIFY_FEATURE=codex/f07-v2-gcp SPECIFY_FEATURE_DIRECTORY=specs/009-v2-gcp-provider`. Active spec/plan/tasks/checklist templates were read. `.specify/extensions.yml` is absent, so no mandatory/optional hooks existed; local feature.json was generated independently, not copied.

Specify and clarify: 13 functional requirements, five measurable criteria, three prioritized stories. Zero human questions; all clarification taxonomy categories are clear in requirements or engineering decisions. No research/source conflict was found; exact N/windows and paid admissions are intentionally evidence-dependent. Requirements checklist 16/16 -> 16/16 after clarification, no regressions. Plan includes research/data model/public boundary/quickstart with explicit ownership. Tasks: 26 unchecked implementation tasks, six US1, six US2, five US3, five setup/foundation and four final checks. GCP-safety checklist generated with 20 unchecked requirements-quality questions, then reviewed 20/20 satisfied by the authorized planner artifact check. Checkmarks do not mean implemented, merged or cloud-verified.

## Read-only analysis result

Spec Kit analysis ran after complete task generation; it did not modify source artifacts. Result: zero actionable owned-artifact consistency/ambiguity/duplication/coverage findings. One separately tracked external integration blocker remains, below; **not** zero integration blockers or ready-to-implement on current base.

| Requirement | Task coverage |
| --- | --- |
| FR-001 identities/isolation | T001, T003–T005, T012–T013, T017, T021 |
| FR-002 selected VMs/image/NIC | T002–T003, T005, T008, T012–T014, T016 |
| FR-003 scenarios/placement/admission | T003, T005, T007–T008, T012–T014, T017 |
| FR-004 private VPC/CIDRs/settings | T003, T005, T007–T008, T012–T014, T016–T017 |
| FR-005 security/trust | T003, T005, T007–T008, T012–T017, T023 |
| FR-006 original cap/cost/full workflow | T003–T006, T009–T010, T012, T017, T021, T023 |
| FR-007 independent pre-create STOP | T001–T002, T006–T014, T017, T023 |
| FR-008 evidence/cancellation | T006, T008–T012, T014, T017–T018, T020, T022–T023 |
| FR-009 honest normalized metadata | T003, T005, T012–T017, T022–T023 |
| FR-010 complete cleanup | T003, T014, T018–T023 |
| FR-011 residual launch blockers/recovery | T003–T004, T018–T023 |
| FR-012 external/redaction/private state | T002–T004, T009, T014–T015, T018–T019, T021–T023, T025 |
| FR-013 offline/failure proof | T005, T011–T012, T016–T018, T022–T026 |
| SC-001 exact scenarios | T012, T014, T016–T017, T024, T026 |
| SC-002 zero unprotected creations | T006, T010–T012, T014, T017, T024, T026 |
| SC-003 actual/trusted output | T012, T014–T017, T024, T026 |
| SC-004 cleanup attempts/blockers | T018–T022, T024, T026 |
| SC-005 recorded offline gate | T023–T026 |

Metrics: 18/18 buildable requirement/criterion coverage (100%); 26 ordered tasks; no unmapped tasks; ambiguity 0, duplication 0; owned-artifact critical issues 0. Current-runtime constitutional mismatch remains one known blocking shared prerequisite; it cannot be fixed within planner ownership. Next action is that prerequisite's verified integration, then the normal implement/converge flow. No extra independent-review stage.

## Exact internal prerequisite: F01R_GCP_EXPIRY

Resolved before implementation: coordinator verified F01R_GCP_EXPIRY PR #17 merged to main at `2cd36e9ea7b12681545e8c6680ccda42388ffded`. This feature branch integrated that latest `origin/main` with a normal merge; integration base/head is `d2bd3bc296c85fb95be7debdcdbd61cbc759369e`. The STOP/DELETE regression corpus and deterministic canonical schema check passed on that head (see `docs/f07-gcp-provider.md`). The implementation proceeds against the governing STANDARD/STOP validators and active configurations. Historical F01/F02 artifacts remain untouched.

Observed affected live files (not F07-owned):

- The two runtime validators above and affected expectations in `tests/contract/test_deployment_contract.py`; add focused campaign/deployment STOP acceptance and DELETE rejection, not broad textual replacements of other providers' deletion settings.
- GCP blocks in six `configs/experiments/` files: exp-001-calibration, exp-001-diagnostic-day, exp-001-multi-provider, exp-002-cross-zone, exp-003-placement-optimization, exp-004-inter-region; and `configs/tests/exp-902-gcp-single-provider.yaml`, `exp-903-three-provider-smoke.yaml`.
- Verify schema determinism/runtime parity for `specs/003-v2-shared-contracts/contracts/{campaign-config,resolved-campaign,provider-deployment-input}.schema.json` and other generated dependents. Current provider_options is a generic object: model-validator changes alone may leave JSON schemas byte-identical. Do not invent schema diff or pretend runtime cross-property validation already exists in schema.
- Resolve affected fixtures/hashes/dry-run/config/deployment test expectations from actual tests; a literal search found the named contract test, not a standalone GCP fixture with the action at this base. Preserve historical F01/F02 artifacts.

Acceptance before F07 implementation: actual merged shared SHA; live campaign and deployment accept governing STANDARD/STOP and reject DELETE; migrated configurations/dry-runs/affected tests and deterministic schema check pass. Integrate at a clean checkpoint, rerun relevant artifact/contract checks and record the new base/head. Until then, T001 remains incomplete and no F07 application implementation should start.

## Frozen interfaces and implementation ownership

`providers/gcp.py`: GcpProviderDriver.register_expiry -> ExpiryEvidence; provision -> ProviderDeploymentOutput; cleanup -> local GcpCleanupResult. Reuse existing deployment/admission/common/execution models, with no shared CleanupEvidence invented. Local settings/journal carry exact runtime/project/image/trust/review/cost/time details.

Implementer owns only `src/cloud_network_benchmark/providers/gcp.py`, `terraform/gcp/{protection,pair}/` and their locks, `tests/{unit/test_gcp_provider.py,unit/test_gcp_expiry.py,contract/test_gcp_provider_contract.py,integration/test_gcp_provider_sequences.py}`, `tests/fixtures/gcp/`, `docs/f07-gcp-provider.md` and this feature's evidence/task updates. Existing package marker/AWS/shared/CLI/config files are read-only. They are not alone in the codebase; preserve other agents' changes. Exact inputs/outputs/acceptance are in contracts/gcp-provider.md and quickstart.md.

## Decisions to retain

Two provider-created immutable global templates, authenticated readback before pair creation, unique source-template binding with no weakening overrides, actual VM scheduling readback; no assumed source-template return field. Native absolute STOP is independent of the controller once inherited. T=original reservation cap end−30 seconds; whole workflow must fit before T. The lead concerns initiation, never a hard completion/billing guarantee. Native time-limit scheduling coexists with S3's one no-distance COLLOCATED policy without an instance-schedule resource. Exact CIDRs, fixed usable .10/.11 addresses and separate VPCs keep P0/P1 isolated. Guest hostkeys use authenticated API identity and tenant guest-agent trust, not attestation. Templates/placement have sourced zero additional support charge, with a dated fixture/hash required; other billable costs remain sourced reservations. Full deletion/inventory is separate from STOP and from template deletion. No invented failed-copy timer, paid run or research result.

## Actual command evidence

All shell calls used assigned cwd `/private/tmp/cnb-f07`, `login:false`, with explicit PWD guards after the initial pwd. Each Spec Kit command used the explicit feature variables above.

| Actual command/check | Exit/result |
| --- | --- |
| git status --short; git rev-parse HEAD at start | 0; clean at a07c1c7 |
| git merge --no-edit e03a07eaa5ed9ea372c695db19fd6fbcffdad024 | 0; fast-forward, no conflict |
| check-prerequisites.sh --json --paths-only | 0; exact F07 paths |
| setup-plan.sh --json | 0; exact F07 plan copied/resolved |
| setup-tasks.sh --json | 0; exact F07 documents/template resolved |
| check-prerequisites.sh --json --template checklist-template | 0; exact checklist template |
| check-prerequisites.sh --json --require-spec --require-tasks --include-tasks | 0; complete F07 inputs |
| Inline Python artifact check: local Markdown links/placeholders, ordered T001–T026, story labels, FR/SC coverage | 0; nine artifact files before this handoff, 13 FR + 5 SC covered, 26 ordered tasks |
| Inline Python ProviderConfiguration probe against exp-902 config and copy with STOP | 0; actual current DELETE accepted and STOP rejected as expected, proving the prerequisite remains real |
| test existing three shared-contract quickstart test paths | 0; files exist, tests not executed |
| /private/tmp/cnb-tools/terraform version | 0; Terraform 1.16.6 darwin_arm64 |
| test Python executable /private/tmp/cnb-offline-venv/bin/python | 0; exists |
| git diff --check before handoff | 0 |

The initial optional extension-file cat returned 1 because no extension file exists; the absence was explicitly verified, so no hooks were skipped silently. An initial combined artifact patch was rejected for duplicate target operations before applying anything; it was split and applied successfully. Neither is a passing runtime test.

**Not run at planner handoff**: F07 pytest (new files absent), Google provider init/schema/fmt/validate (new roots absent), F07 convergence, actual GCP APIs/credentials/SSH/traffic, paid IAM/zone/image/host identity/placement/routing/stop/billing/deletion acceptance. See `docs/f07-gcp-provider.md` for implementation-stage evidence.

## Next stage

Planning is complete. F01R_GCP_EXPIRY is integrated and verified. The initial prerequisite blocker above is resolved by PR #17 and the normal merge recorded above. The implementation checkpoints below update the original planner-stage status; Sol convergence remains separate from cloud acceptance or system readiness.

## Earlier implementation checkpoint (2026-10-23)

Current implementation base: `d2bd3bc296c85fb95be7debdcdbd61cbc759369e`; final implementation source commit: `4336ce8c5fe3a7f5dbe542fec365f0310ea7eeb6`. Subsequent commits only record and clarify evidence. The F07 changes are limited to the provider, its two Terraform roots, owned tests, fixture evidence, documentation, and task/handoff records. Latest focused GCP checks passed 19 tests; the full offline suite passed 1,078 tests with zero skips using the official PowerShell path. Both Terraform roots passed backend-disabled init and validate against locked Google provider 7.46.1; provider-schema assertions confirmed native absolute STOP fields, unique template references, and pair source-template binding. Terraform formatting and `git diff --check` passed.

This earlier checkpoint recorded 13/26 checked tasks and is superseded by the current follow-up evidence in `docs/f07-gcp-provider.md`.

## Current implementation checkpoint (2026-10-23)

The feature branch has continued from the earlier checkpoint, committed its owned implementation, and normally merged coordinator-verified origin/main PR18. The integrated head `34dc70bbb98f6dd7a2601b1cae40e2b4b0ce5922` passed the full offline suite (1,127, zero skips), Terraform formatting/validation and provider-schema assertions. The task table records 25/26 implementation tasks complete; T026 awaits Spec Kit convergence. Current focused checks and limitations are recorded in `docs/f07-gcp-provider.md`. The inventory commands rely on gcloud's internal pagination and do not expose page tokens for page-specific failure injection. No Terraform plan/apply/destroy, cloud API, credentials, SSH, benchmark traffic, paid admission, F10 readiness or research conclusions are claimed.

## Convergence implementation checkpoint (2026-10-23)

The F07-owned convergence repairs T027–T039 are committed at `ff207376f4ad6977f14460da87b7822f586e2f84`. This branch then normally merged coordinator-verified `origin/main` PR19 at `4a80f4af86feddc86e4ab930d254a19e29b35591`; integrated head is `c695a1a5e135f3b795cdabac21cafce15f02f75c`. The integrated offline suite passed 1,236 tests with zero skips; the focused GCP provider unit/sequence suite passed 65 tests, canonical schema check and Terraform formatting passed, both Terraform roots validated, and their mock-provider tests passed 4/4 protection scenarios and 1/1 pair inheritance case. T001–T025 and T027–T039 are checked; T026 remains the Spec Kit convergence task for Sol. No cloud API, credentials, real resources, Terraform plan/apply/destroy, SSH, or benchmark traffic was used. Known limitations are recorded in the latest section of `docs/f07-gcp-provider.md`, including lack of CLI continuation-token exposure for independent later-page injection and lack of cloud acceptance.

## Phase 8 implementation checkpoint (2026-10-23)

T040–T043 are implemented in the bounded F07 ownership. The provider now accepts the REST template `selfLink`/decimal-string `id` and pinned Google provider 7.46.1 `self_link_unique` representations, validates settings at documented template/instance fields and canonical resource paths, requires source-backed image GVNIC compatibility, and merges checkpointed journal evidence through stale provision saves. Session-aware lifecycle tests cover healthy live siblings versus prior-session uncertainty, and restart recovery verifies preserved disk/host evidence. Focused GCP unit and sequence tests pass 77/77. `git diff --check` passes. The full suite and final schema/Terraform/mock gates are intentionally not rerun until serialized after the separate T026 convergence review; they are not claimed passed at this checkpoint.

At this point T001–T025 and T027–T043 are implementation-checked (42/43); T026 remains exclusively the Sol convergence task. No cloud API, credentials, real resources, Terraform plan/apply/destroy, SSH session, benchmark traffic or paid run was used. The F07 implementation checkpoint still requires the integrated post-convergence gates and retains the offline/cloud evidence boundaries recorded in `docs/f07-gcp-provider.md`.

## Phase 9 implementation checkpoint (2026-10-23)

T044–T045 are implemented. Current-attempt registered-session reuse and provision now fail closed after a controller-session change, while cleanup can resolve the prior attempt without changing its original cap/session. Instance and template policy identities are canonicalized and compared as exact sets: S3 has only its selected policy and all other scenarios have none. Full offline traces cover policy extra/duplicate/missing/foreign/wrong-region failures and cleanup, along with the successful S3 trace. Focused GCP provider unit/sequence tests pass 83/83. The full suite and final Terraform/schema/mock gates remain for the serialized post-T026 Sol stage and are not claimed passed here.

Task state is now T001–T025 and T027–T045 implementation-checked (44/45); T026 remains the sole convergence task. No cloud API, credentials, real resources, Terraform plan/apply/destroy, SSH, benchmark traffic or paid run was used.

## Phase 10 convergence record (2026-10-23)

Sol completed convergence against `752d377ae133b7d3b0326b21f635763331533a2f`, assessed all 45 tasks, and reported zero findings. The tasks artifact remained unchanged during convergence. The focused post-convergence prerequisite/controller-session-change/cleanup offline probe was reported as 91 passing tests in 1.78 seconds. T026 is marked complete in `tasks.md`; the implementation base reviewed by Sol is unchanged.

The serialized full offline suite and final schema, Terraform format/validation, and mock-provider gates remain assigned to the parent coordinator at the exact final head. They have not been run or claimed in this handoff. F07 remains offline-only and is not cloud acceptance or F10 readiness.
