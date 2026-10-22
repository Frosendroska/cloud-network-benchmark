# Tasks: F06 V2 Azure provider and expiry

**Input**: `specs/008-v2-azure-provider/{spec,plan,research,data-model}.md` and `contracts/azure-provider.md`.

**Prerequisites**: Merged F01R; checked artifacts; no unmerged dependency. Scope and file ownership are fixed in plan.md. Tests are explicitly required by FR-012. The 27 task markers record offline implementation completion; real cloud acceptance remains separately authorized work.

## Phase 1: Setup

- [x] T001 Create Azure-only deterministic response/command/clock fixtures and independent provider-expiry state in `tests/fixtures/f06/`, covering all four scenarios without shared conftest or sibling imports (FR-006/012).
- [x] T002 Define `AzureRuntimeOptions`, `AzureRecovery`, `AzureProviderResult` and `AzureCleanupResult` in `src/cloud_network_benchmark/azure_provider.py` using unchanged shared DTOs and exact constraints from data-model.md: explicit subscription/tenant UUIDs, controller IPv4 /32, private exclusive paths, positive finite budgets/timeouts, original monotonic deadline, dated price SHA-256, and `trigger < retry_end_at <= reservation.expires_at` (FR-001/005/009/011).

## Phase 2: Foundational

- [x] T003 Implement pure request/admission/identity/namespace validation and cumulative original deadlines in `src/cloud_network_benchmark/azure_provider.py`; require reserved matching evidence, no outstanding cleanup, required priority-review reference, complete-workflow and independent post-registration fit, minimum 300-second schedule lead and explicit expiry pricing; clip every command/wait and never reset cap (FR-001/008/009).
- [x] T004 Implement exclusive mode-0700 attempt workspace, mode-0600 write-before-action atomic recovery journal, separate pair/expiry state and TF_DATA_DIR, collision/symlink/foreign-identity refusal and redacted action evidence in `src/cloud_network_benchmark/azure_provider.py` (FR-005/006/011).

**Checkpoint**: A caller can validate an Azure request and its immutable original cap without contacting Azure. Failure fixtures exercise zero side effects.

## Phase 3: User Story 1 - Comparable private pair (Priority: P1)

**Goal**: Pure mapping, explicit infrastructure and normalized/authenticated output. Public provisioning remains gated by US2.

**Independent Test**: Actual mocked HCL plans cover S1–S4; pure contract/output/host-key fakes prove mappings and errors independently of cloud expiry execution.

- [x] T005 [P] [US1] Add failing request/output contract tests in `tests/contract/test_azure_provider_contract.py` for two roles, source identities, Regular options, logical vs unavailable physical zones, full/explained partial DTOs, network-limit limitations and authenticated connection references (FR-001/002/003/005/007).
- [x] T006 [P] [US1] Add mocked-provider plan tests in `terraform/azure/pair/tests/topologies.tftest.hcl` for all four topologies and invalid inputs, asserting actual HCL values for two VMs, disks/NICs, same/cross/PPG/localities, both S4 peering directions and restrictive NSGs/default-rule overrides (FR-002/003/004/012).
- [x] T007 [US1] Implement `terraform/azure/pair/{versions.tf,main.tf,variables.tf,outputs.tf}` and exact `.terraform.lock.hcl` plus `build_pair_inputs` in `src/cloud_network_benchmark/azure_provider.py`: Ubuntu24.04 amd64 concrete image, D4s_v5 Regular, 32GiB StandardSSD_LRS E4, accelerated NIC, MTU1460/CUBIC/DSCP0/ECNoff cloud-init, two zone-matched Standard public IPs, Frankfurt/London CIDRs, Standard PPG only S3, Global VNet Peering only S4, controller /32 SSH and private A-only B listeners, explicit deny before broad NSG defaults; omit Spot-only arguments and forbidden infrastructure (FR-002/003/004/006).
- [x] T008 [US1] Implement output normalization and actual/unknown image/zone/shape/address/placement/acceleration/network metadata in `src/cloud_network_benchmark/azure_provider.py`, preserving existing ProviderDeploymentOutput and per-role partial-unavailable rules; documented aggregate maximum 12,500Mbps is not measured throughput (FR-001/007).
- [x] T009 [US1] Implement bounded authenticated Azure Run Command public-host-key readback against each exact VM ID and private no-overwrite known_hosts materialization in `src/cloud_network_benchmark/azure_provider.py`; wrong resource/address/key/response fails without usable connection, no keyscan or private-host-key retrieval (FR-005).
- [x] T010 [US1] Add focused unit cases in `tests/unit/test_azure_provider.py` for every topology, no substitutions, source options vs active Regular argv/HCL, private/public role separation, NSG ordering, PPG intent constraints, malformed host keys, logical-zone metadata and materialized known_hosts bytes (FR-002/003/004/005/007/012).

## Phase 4: User Story 2 - Independent pre-create deallocation (Priority: P1)

**Goal**: Date-specific Azure-side action precedes VM apply and survives controller loss in the fake external boundary.

**Independent Test**: Independent fake provider clock runs expiry after the local controller stops; failures prevent VM apply and preserve preparation cleanup obligations.

- [x] T011 [P] [US2] Add failing registration/provision gate and controller-loss scenarios in `tests/integration/test_azure_provider_flow.py` plus `tests/fixtures/f06/`: zero apply on missing/mismatched/draft/stale publication, binding, permission, pricing, admission, reservation or time evidence; explicit late/creating/absent/denied targets and A-fails/B-still-attempted (FR-008/009/010/012).
- [x] T012 [P] [US2] Add mocked-provider tests in `terraform/azure/expiry/tests/prerequisites.tftest.hcl` asserting zero VM/network resources, Basic account/system identity, exact deallocate/read/instanceView custom actions and attempt-RG scope with no start/create/delete or broad Contributor permission (FR-008/010/012).
- [x] T013 [US2] Implement `terraform/azure/expiry/{versions.tf,main.tf,variables.tf,outputs.tf}` and exact `.terraform.lock.hcl` using the same AzureRM version as pair: attempt RG, Basic account/system MI, narrow role/assignment and deterministic future A/B ARM IDs only; state/timeouts remain isolated and bounded (FR-006/008/009/010).
- [x] T014 [US2] Implement the fixed deallocation-only `terraform/azure/expiry/deallocate.ps1` with explicit PowerShell/Az.Accounts runtime identity, exact two-ID/subscription/namespace validation, managed-identity REST deallocate without hibernation, per-target errors and async observations, retry of absent/creating/transient states until finite absolute retry end, bounded waits and honest late-dispatch output; never skip B after A error or claim initial 404 prevents late creation (FR-008/010).
- [x] T015 [US2] Implement expiry-prerequisite apply/readback and authenticated Automation `2024-10-23` runbook draft/upload/publish/published-content-digest/runtime/module/RBAC verification through argv-based CommandRunner in `src/cloud_network_benchmark/azure_provider.py`; poll completion within original deadline, no hosted-source link and no VM creation (FR-008/009/010).
- [x] T016 [US2] Implement OneTime UTC schedule and jobSchedule REST payloads, exact lower-case target parameters, complete readback validation, actual registration ID/time/trigger metadata and failure evidence in `src/cloud_network_benchmark/azure_provider.py`; recheck 300-second pre-write lead, returned timing/cap fit and independent post-registration budget (FR-008/009).
- [x] T017 [US2] Implement `provision` success path and independent pre-apply publication/schedule/binding/RBAC/identity/cap revalidation in `src/cloud_network_benchmark/azure_provider.py`, consuming matching reserved/registered DTOs and real apply/output argv, then US1 normalization/host authentication; never rely only on expiry DTO state or permit Spot/default-state fallbacks (FR-001/006/008/009).
- [x] T018 [US2] Complete independent fake expiry, actual uploaded-script/REST-payload assertions, async202/deallocated distinction, late target appearance and bounded retries/controller-loss coverage in `tests/integration/test_azure_provider_flow.py` and `tests/unit/test_azure_provider.py`; test static runbook deallocate-only restrictions and state explicitly that no PowerShell/cloud runtime is proved (FR-008/009/010/012).

## Phase 5: User Story 3 - Failure evidence and full cleanup (Priority: P2)

**Goal**: Partial/interrupted creation still has recoverable evidence and honest deletion status.

**Independent Test**: Fake state/inventory leaves disks/IPs/expiry resources or unknowns; every such case remains cleanup failed/open and launch blocked.

- [x] T019 [US3] Add failing apply/output/identity/interruption/registration-preparation and cleanup fault scenarios in `tests/integration/test_azure_provider_flow.py`, including primary+secondary failures and partial state with no outputs (FR-011/012).
- [x] T020 [US3] Implement provision/preparation terminal failure paths in `src/cloud_network_benchmark/azure_provider.py`, preserving original primary/secondary failures, action timestamps, partial normalized output and private recovery state, with cleanup-required whenever preparatory or pair creation may have started (FR-007/011).
- [x] T021 [US3] Implement `destroy` pair deletion and direct-ID plus scoped complete inventory verification in `src/cloud_network_benchmark/azure_provider.py`; account for VMs, disks, NICs, IPs, networks/peerings/PPG, reject foreign IDs, retain expiry until both VMs observed absent and fail on202/unknown/missing state/timeout rather than trusting Terraform exit0 (FR-006/011).
- [x] T022 [US3] Implement ordered jobSchedule/schedule/runbook cancellation/deletion and expiry-prerequisite destroy only after VM absence proof in `src/cloud_network_benchmark/azure_provider.py`; preserve cancellation failures and independently verify role/account/group removal; any residual/unknown returns failed and launch_blocked (FR-008/011).
- [x] T023 [US3] Add recovery/idempotence and concurrent same-CIDR distinct-attempt cases in `tests/unit/test_azure_provider.py` and `tests/integration/test_azure_provider_flow.py`: lost state, interrupted action journal, no adoption/overwrite, no cross-attempt deletion, residual billables and incomplete inventory block success (FR-006/011/012).
- [x] T024 [US3] Add nested success/failure/output/command/journal-export secret sentinels and complete cleanup accounting checks in `tests/contract/test_azure_provider_contract.py` and `tests/unit/test_azure_provider.py`; retain primary error plus retrieval-context and cleanup errors distinctly without exposing credentials, PEM data, key paths or state (FR-005/011/012).

## Phase 6: Polish and offline gate

- [x] T025 Run focused then full pytest and unchanged schema-drift checks, record exact revision/commands/exits and no cloud/credential use in `docs/validation/f06-azure-provider.md`; repair within assigned files and distinguish any not-run checks (FR-012, SC-001–SC-005).
- [x] T026 Run Terraform fmt, provider initialization with backend disabled, validate and mock-provider plan tests for both Azure roots; record exact Terraform/AzureRM versions and results in `docs/validation/f06-azure-provider.md`, keeping provider downloads/state/plans private and never running real plan/apply (FR-002/003/004/008/010/012, SC-001/SC-002/SC-005).
- [x] T027 Document Azure permissions, authenticated host identity, exact expiry publication/register/recovery/deletion sequencing, dated job-minute pricing input and cap-fit limits, unsupported real-runtime claims and separate paid controller-loss acceptance in `docs/validation/f06-azure-provider.md`; reconcile `specs/008-v2-azure-provider/quickstart.md` with actual runnable commands and hand off exact-head convergence without launching paid checks (FR-009/010/011/012).

## Dependencies and execution order

T001–T002 → T003–T004 → US1 and US2 foundation. US1 mapping/static checks T005–T010 are independently testable; US2 prerequisite/static/runbook work T011–T016 is independently testable. T017 depends on both US1 output/identity work and T016 registered expiry. T018 confirms integrated happy-path/safety. US3 T019–T024 completes failure/recovery/delete behavior, then T025–T027 validate the whole feature. Do not describe a pair-mapping MVP as safe live provisioning before US2/US3 finish.

Only tasks explicitly marked [P] own different files without unfinished-task dependencies: T005/T006, and T011/T012. They are scheduling opportunities, not authorization for another concurrent writer in this worktree. US1 example: contract tests and HCL mocked tests can be authored independently after foundation. US2 example: independent fake registration cases and prerequisite HCL tests can be authored independently. US3 shares the one driver/test flow, so execute serially; no artificial parallel marker.

## Implementation strategy

Establish validation/journal first; prove pure pair semantics; register the genuine independent action; expose gated provision; close all failure/cleanup paths; run the credential-free feature gate. Keep ordinary helper functions inside the one Azure module. No new generic provider platform, shared schema, root CLI wiring or unrelated refactor. F09/F11 use fixtures until their own merged integration; no imports from their unfinished implementations. All 27 tasks are required for the F06 handoff; MVP labeling never authorizes cloud execution.
