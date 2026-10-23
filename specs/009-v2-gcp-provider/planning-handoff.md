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

Planning is complete. F01R_GCP_EXPIRY is integrated and verified. The initial prerequisite blocker above is resolved by PR #17 and the normal merge recorded above. Implementation evidence and remaining incomplete test cases are recorded in `docs/f07-gcp-provider.md`; this checkpoint is ready for Sol convergence review, not cloud acceptance or system readiness.

## Implementation checkpoint (2026-10-23)

Current implementation base: `d2bd3bc296c85fb95be7debdcdbd61cbc759369e`. The F07 changes are limited to the provider, its two Terraform roots, owned tests, fixture evidence, documentation, and task/handoff records. Latest focused GCP checks passed 19 tests; the full offline suite passed 1,078 tests with zero skips using the official PowerShell path. Both Terraform roots passed backend-disabled init and validate against locked Google provider 7.46.1; provider-schema assertions confirmed native absolute STOP fields, unique template references, and pair source-template binding. Terraform formatting and `git diff --check` passed.

Not complete: T004, T006, T010–T012, T016–T022 and T026 remain unchecked where the full specified isolated-state, cap-edge, scenario matrix, controller-loss, exhaustive failure-injection, pagination/recovery and convergence handoff evidence is incomplete. The code's list commands rely on gcloud CLI pagination and fail closed on command/read shape errors, but do not expose page tokens for separate later-page injection. The task table records implementation status; no Terraform plan/apply/destroy, cloud API, credentials, SSH, benchmark traffic, paid admission, F10 readiness or research conclusions are claimed. The exact current implementation commit and complete command outcomes are in `docs/f07-gcp-provider.md`.
