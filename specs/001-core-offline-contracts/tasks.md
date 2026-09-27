# Tasks: F01 Core Contracts and Offline Harness

**Input**: Design documents from `specs/001-core-offline-contracts/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: Required by the specification and constitution. Within each story, write the listed tests first and confirm they fail before implementing the behavior.

**Organization**: Tasks are grouped by user story so each story has an independently testable outcome. F01 must remain credential-free throughout.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it changes different files and has no dependency on another incomplete task in the same phase.
- **[Story]**: Maps the task to a user story from `spec.md`.
- Every task names the exact file or files it owns.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish the Python package, dependency metadata, and test harness used by every story.

- [X] T001 Create `pyproject.toml` for Python 3.9+ with the `cloud-network-benchmark` entry point, Pydantic 2.x, PyYAML 6.x, pytest 8.x, JSON Schema test support, and pytest configuration for `tests/unit`, `tests/contract`, and `tests/integration`
- [X] T002 [P] Create the importable package and module entry-point skeletons in `src/cloud_network_benchmark/__init__.py`, `src/cloud_network_benchmark/__main__.py`, and `src/cloud_network_benchmark/contracts/__init__.py`
- [X] T003 [P] Create shared pytest fixtures in `tests/conftest.py` for repository roots, temporary result roots, frozen UTC time, deterministic random tokens, deterministic fake implementation/design Git commits, injected persistence failures, and guards that fail credential-free tests on socket access or unapproved subprocess execution

**Checkpoint**: The package installs in editable mode and pytest discovers the empty test layout without cloud tooling or credentials.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Implement shared vocabulary and errors that all four stories depend on.

**CRITICAL**: No user story implementation begins until this phase is complete.

### Tests

- [X] T004 [P] Add failing tests for structured validation, collision, persistence, and CLI error codes `2`, `3`, `4`, and `5` in `tests/unit/test_errors.py`
- [X] T005 [P] Add failing contract tests preserving exact provider, scenario, VM-role, measurement-direction, benchmark-phase, command-outcome, artifact-expectation, canonical artifact-path, child execution-state, cleanup-state, and campaign-state values in `tests/contract/test_artifact_contract.py`

### Implementation

- [X] T006 [P] Implement typed foundation exceptions and non-secret structured error serialization in `src/cloud_network_benchmark/errors.py`, mapping usage to `2`, validation/resolution to `3`, collisions to `4`, and post-validation local persistence failures to `5`
- [X] T007 [P] Implement stable enums, `FailureEvidence`, string and structured unavailable-evidence types, and canonical child-relative artifact constants in `src/cloud_network_benchmark/contracts/common.py` and `src/cloud_network_benchmark/contracts/artifacts.py`, preserving the exact values in `contracts/shared-contracts.md`, including child `initialized|provision_ready|running|collecting|validating|succeeded|failed|interrupted`, cleanup `not_started|not_required|attempted|succeeded|failed`, and campaign `initialized|active|partially_complete|succeeded|failed|interrupted`

**Checkpoint**: Shared errors and artifact vocabulary pass their focused tests and can be imported without initializing files or external tools.

---

## Phase 3: User Story 1 - Resolve an Approved Campaign Before Side Effects (Priority: P1) MVP

**Goal**: Safely load and validate campaign YAML, then resolve exactly one provider-specific observation per explicitly selected provider without IDs, writes, or external actions.

**Independent Test**: Submit valid three-provider experiment and one-provider test fixtures plus malformed and contradictory fixtures; valid input resolves one child per selected provider, while every invalid input returns precise field errors and leaves result roots untouched.

### Tests for User Story 1

- [X] T008 [US1] Create the runnable S1-S4 three-provider primary matrix in `configs/experiments/exp-001-multi-provider.yaml` through `exp-004-inter-region.yaml`, the AWS/Azure/GCP single-provider smoke fixtures and reduced three-provider smoke fixture in `configs/tests/exp-900-single-provider.yaml` through `exp-903-three-provider-smoke.yaml`, and explicit provider-native regular/spot purchase options; document every `experiment_id` and its Thesis-side experiment or smoke-verification mapping in `configs/README.md`; add duplicate-key, malformed-ID, missing-start, provider-mismatch, unknown-provider, unknown-scenario, same-zone, cross-zone, placement-optimization, and inter-region test-only fixtures in `tests/fixtures/configs/`
- [X] T009 [P] [US1] Add failing loader tests in `tests/unit/test_config_loading.py` for safe YAML parsing, duplicate-key rejection, canonical-root role derivation, exact-byte SHA-256 and byte length, unknown-field rejection, and zero filesystem writes
- [X] T010 [P] [US1] Add failing semantic-resolution tests in `tests/unit/test_config_resolution.py` covering exact selected-provider/config-key equality, one child per provider, one explicit common `scheduled_start`, explicit VM A/client and VM B/server roles, `vm_a_to_vm_b`, configurable durations/streams/timeouts, every provider/scenario invariant, and absence of recurrence or sharding fields
- [X] T011 [P] [US1] Add failing schema and Pydantic serialization parity tests for `specs/001-core-offline-contracts/contracts/campaign-config.schema.json` in `tests/contract/test_campaign_config_contract.py`
- [X] T012 [P] [US1] Add failing CLI integration tests for `validate` and `resolve` human/JSON output, exit code `3`, and no IDs, manifests, result paths, subprocesses, sockets, or cloud contacts in `tests/integration/test_validate_resolve_cli.py`

### Implementation for User Story 1

- [X] T013 [US1] Implement strict immutable campaign, benchmark, AWS, Azure, GCP, VM-intent, config-provenance, resolved-campaign, and resolved-observation models in `src/cloud_network_benchmark/contracts/campaign.py`: `schema_version` is required and equals `1`; `experiment_id` matches `EXP-[0-9]{3,}`; one `scheduled_start` is required and timezone-aware; recurrence/sharding fields are absent and therefore rejected; `selected_providers` is unique with 1-3 values from `aws|azure|gcp`; scenario values remain exact; positive durations/timeouts and positive `upload_streams` are never inferred; unknown fields are rejected
- [X] T014 [US1] Implement canonical config-path discovery, `experiment|test` role derivation, duplicate-key-safe YAML loading, exact source-byte hashing, and structured parse errors in `src/cloud_network_benchmark/config.py`, accepting only files under `configs/experiments/` or `configs/tests/`
- [X] T015 [US1] Implement whole-campaign semantic validation and resolution in `src/cloud_network_benchmark/config.py`: selected providers equal provider-config keys exactly; provider discriminators match map keys; `same_zone` uses equal regions/equal zones/placement `none`; `cross_zone` uses equal regions/different zones/placement `none`; `placement_optimization` requires AWS `cluster_placement_group`, Azure `proximity_placement_group`, or GCP `compact_placement_policy`; `inter_region` uses different regions and placement `none`; all children share benchmark intent and scheduled start
- [X] T016 [US1] Implement `validate` and `resolve` argument parsing, human/JSON rendering, stdout/stderr behavior, and structured exit handling in `src/cloud_network_benchmark/cli.py` and wire module execution in `src/cloud_network_benchmark/__main__.py`

**Checkpoint**: User Story 1 passes independently and proves invalid campaigns cannot assign IDs, create evidence, or invoke external actions.

---

## Phase 4: User Story 2 - Initialize Immutable Campaign and Run Evidence (Priority: P1)

**Goal**: Initialize one linked parent campaign and one immutable child run per selected provider with deterministic identities, provenance, manifests, lifecycle state, and collision-safe paths.

**Independent Test**: Initialize a resolved three-provider fixture with frozen clock/token and deterministic Git provenance sources; verify one parent plus three unique children, linked manifests, byte-identical snapshots, exact hashes and paths, canonical lifecycle aggregation, required metadata evidence, a manifest for every assigned ID under injected persistence failures, and refusal to modify any occupied destination.

### Tests for User Story 2

- [X] T017 [US2] Create representative initialized, partially initialized, failed, interrupted, cleanup-succeeded, cleanup-failed, observed-metadata, unavailable-metadata, and unavailable-provenance parent/child manifest fixtures in `tests/fixtures/manifests/`
- [X] T018 [P] [US2] Add failing deterministic identity and path tests in `tests/unit/test_ids_paths.py` for `<utc-timestamp>-<normalized-experiment-id>-<random-token>` campaign IDs, `<campaign-id>-<provider>` candidate child IDs, assignment only after exclusive child-manifest reservation, parent/child manifest paths, `results/raw/<run_id>/`, complete preflight, and zero-byte modification on every collision
- [X] T019 [P] [US2] Add failing lifecycle tests in `tests/unit/test_lifecycle.py` for the canonical `initialized -> provision_ready -> running -> collecting -> validating -> succeeded` path, failure/interruption from any non-terminal state, rejection of aliases such as `resolved`, `pending`, and `provision-ready`, immutable terminal execution outcomes, cleanup transitions, original-failure preservation, and deterministic parent aggregation
- [X] T020 [P] [US2] Add failing manifest/schema tests in `tests/contract/test_manifest_contracts.py` for both JSON Schemas, string and structured `value` xor `unavailable_reason`, observed and unavailable/null VM metadata, cloud/provider metadata, and tool versions, exact SHA-256, child-local snapshot paths, append-only events whose state matches their lifecycle domain, canonical lifecycle values with alias rejection, nullable actual timestamps, and separate `failure` versus `cleanup_failure`
- [X] T021 [P] [US2] Add failing integration and failure-injection tests for three-provider and single-provider `init` in `tests/integration/test_initialize_cli.py`: deterministic injected implementation Git commits for experiment and smoke-test runs, byte-identical snapshots, linked references, atomic JSON, occupied destinations, failures at every manifest/directory/snapshot persistence boundary, exactly one preserved failed manifest for every assigned run ID, no manifest requirement for unassigned candidate IDs, and exit codes `4`/`5`

### Implementation for User Story 2

- [X] T022 [P] [US2] Implement injectable UTC clock/token sources, candidate campaign ID generation, provider-suffixed candidate child IDs, and uniqueness validation in `src/cloud_network_benchmark/ids.py`
- [X] T023 [P] [US2] Implement repository-relative allocated path models, complete collision preflight, exclusive child-manifest reservation that atomically turns a candidate into an assigned run ID, injectable persistence fault points, exclusive directory/file creation helpers, and same-directory atomic JSON replacement in `src/cloud_network_benchmark/paths.py`
- [X] T024 [P] [US2] Implement execution/cleanup transition validation, append-only lifecycle events, failure evidence, and derived campaign states in `src/cloud_network_benchmark/lifecycle.py`, ensuring parent `succeeded` requires every child execution and cleanup obligation to succeed
- [X] T025 [US2] Implement campaign/child manifest models; required VM metadata, cloud/provider metadata, and tool-version structured evidence; an injectable Git provenance provider with deterministic fake support; exact-byte `config.yaml` snapshots; SHA-256 verification; manifest-first linked record initialization; partial-initialization recovery that marks every assigned child manifest failed without deleting evidence; and atomic lifecycle updates in `src/cloud_network_benchmark/manifests.py`
- [X] T026 [US2] Implement the `init` CLI flow and JSON/human success or persistence-failure output in `src/cloud_network_benchmark/cli.py`, performing full validation and collision preflight before reservation, assigning child IDs only through manifest creation, invoking no external command, and reporting all preserved manifests after partial initialization

**Checkpoint**: User Story 2 passes independently on a temporary result root, including collision and dual-failure evidence cases.

---

## Phase 5: User Story 3 - Share Stable Contracts With Parallel Feature Work (Priority: P2)

**Goal**: Publish importable deployment, remote-connection, artifact, validator-input, and external-command contracts with deterministic fakes for F02-F08.

**Independent Test**: Construct and round-trip every public contract for AWS, Azure, and GCP; scripted command fakes represent success, failure, timeout, and interruption in exact order without launching a subprocess.

### Tests for User Story 3

- [X] T027 [US3] Create scripted success, non-zero failure, timeout, interruption, redaction, missing-action, and extra-action fixtures in `tests/fixtures/commands/`
- [X] T028 [P] [US3] Add failing provider deployment and remote-connection contract tests in `tests/contract/test_deployment_contract.py` for campaign/run/provider identity, exactly VM A/client and VM B/server, provider-native placement/image/shape/network-limit metadata, private IPv4 outputs, authentication references without secret material, and partial-output unavailable evidence
- [X] T029 [P] [US3] Add failing command-boundary tests in `tests/unit/test_execution.py` for non-empty argument arrays with `shell=false`, explicit working directory and positive timeout, environment classification without durable secret values, outcome/exit-code consistency, timing, stdout/stderr, ordered request recording, and exhausted/mismatched fake scripts
- [X] T030 [P] [US3] Add failing validator-input tests in `tests/contract/test_validation_contract.py` for manifest/config/provider-output paths, every benchmark/diagnostic/metadata artifact, `required|optional|not_expected`, enabled-phase handling, VM/provider/tool metadata evidence, and the canonical lifecycle/failure/provenance types established by T007
- [X] T031 [P] [US3] Add failing public-import and serialization smoke tests for all F02-F08 contract symbols in `tests/contract/test_public_contracts.py`

### Implementation for User Story 3

- [X] T032 [P] [US3] Implement provider deployment input/output, provider VM output, provider-explicit placement metadata, and secret-free remote connection models in `src/cloud_network_benchmark/contracts/deployment.py`
- [X] T033 [P] [US3] Implement `CommandRequest`, `CommandResult`, `CommandRunner`, subprocess-backed runner, durable redaction view, and ordered `ScriptedCommandRunner` outcomes in `src/cloud_network_benchmark/contracts/execution.py`, with the real runner as the only production subprocess boundary
- [X] T034 [P] [US3] Implement artifact expectations and validator input models in `src/cloud_network_benchmark/contracts/validation.py` using the lifecycle, failure, and unavailable-evidence types completed in T007, marking disabled phase artifacts `not_expected` while retaining canonical paths
- [X] T035 [US3] Export the reviewed stable public surface for F02-F08 from `src/cloud_network_benchmark/contracts/__init__.py` without exporting internal persistence helpers

**Checkpoint**: User Story 3 passes independently and downstream features can import contracts and fakes without credentials, cloud SDKs, Terraform, SSH, SCP, or FLENT.

---

## Phase 6: User Story 4 - Preview a Campaign Without Credentials (Priority: P3)

**Goal**: Render a complete human/JSON campaign preview with candidate identity/path patterns and no durable reservation, filesystem writes, secrets, network access, or command execution.

**Independent Test**: Dry-run the valid multi-provider and single-provider fixtures and verify all configured providers, scheduled start, placement, benchmark settings, artifacts, validator expectations, lifecycle/cleanup outline, and action classes appear while the filesystem and fake-command request log remain unchanged.

### Tests for User Story 4

- [X] T036 [P] [US4] Add failing dry-run model and renderer tests in `tests/unit/test_dry_run.py` for stable JSON/human output, one child summary per selected provider, explicit provisional parameters, canonical artifacts, candidate rather than reserved IDs, action classes, cleanup expectation, and redaction of credentials, authentication material, private keys, environment values, and sensitive stdin
- [X] T037 [P] [US4] Add failing CLI integration tests in `tests/integration/test_dry_run_cli.py` for experiment/test fixtures, malformed input exit code `3`, zero result/manifests changes, zero subprocess calls, and zero socket access

### Implementation for User Story 4

- [X] T038 [US4] Implement immutable Dry-Run Report models plus deterministic human and JSON renderers in `src/cloud_network_benchmark/dry_run.py`, reusing resolved observations and path/artifact templates without generating or reserving IDs
- [X] T039 [US4] Implement the `dry-run` command and output selection in `src/cloud_network_benchmark/cli.py`, guaranteeing validation/resolution only and no call to manifest initialization or any `CommandRunner`

**Checkpoint**: All four user stories are independently functional and the complete F01 surface remains credential-free.

---

## Phase 7: Polish and Cross-Cutting Validation

**Purpose**: Prove contract coherence, performance, security boundaries, and accurate user-facing instructions across all stories.

- [X] T040 [P] Add cross-command JSON error-shape, no-secret-output, no-network, and no-unapproved-subprocess regression tests in `tests/integration/test_offline_boundaries.py`
- [X] T041 [P] Add bounded performance tests in `tests/integration/test_performance.py` proving validation/resolution of three providers completes under one second and local initialization under two seconds on the development baseline, excluding injected filesystem faults
- [X] T042 [P] Add JSON Schema metaschema validation and runtime-model/schema parity regression tests for all files under `specs/001-core-offline-contracts/contracts/` in `tests/contract/test_schema_integrity.py`
- [X] T043 Update `README.md` only after the CLI exists, documenting the real editable-install, `validate`, `resolve`, `init`, `dry-run`, and complete credential-free pytest commands; the primary `configs/experiments/` versus executable smoke-test `configs/tests/` roles; their Thesis-side mapping table; and the external ownership of recurring triggers
- [X] T044 Execute every scenario in `specs/001-core-offline-contracts/quickstart.md`, run the full unit/contract/integration suite from a clean temporary result root, conduct a lightweight timed acceptance review confirming that a human can understand dry-run output for every provider/scenario fixture within five minutes, and record command versions, pass counts, timings, review results, credential-free status, and limitations in `docs/f01-offline-validation.md`

**Checkpoint**: A clean checkout can install and run F01 validation locally, all schemas and contracts agree, and the recorded evidence demonstrates zero cloud contact.

---

## Dependencies and Execution Order

### Phase Dependencies

```text
Phase 1 Setup
    -> Phase 2 Foundation
        -> Phase 3 US1 Validation/Resolution
            -> Phase 4 US2 Initialization
                -> Phase 6 US4 Dry Run
        -> Phase 5 US3 Shared Contracts

Phases 4, 5, and 6 complete
    -> Phase 7 Polish and Cross-Cutting Validation
```

- Phase 1 has no dependencies.
- Phase 2 depends on Phase 1 and blocks every user story.
- User Story 1 depends only on Phase 2 and is the MVP.
- User Story 2 consumes the Resolved Campaign Specification from User Story 1.
- User Story 3 depends on the Phase 2 lifecycle, failure, unavailable-evidence, and artifact types completed by T007. T030 and T034 do not depend on the later US2 transition or persistence implementations T024-T025 and may run in parallel with User Stories 1 and 2 after Phase 2.
- User Story 4 consumes User Story 1 resolution plus User Story 2 path templates; it does not initialize evidence.
- Phase 7 depends on all selected user stories.

### Within Each User Story

- Create fixtures before tests that consume them.
- Write and run all story tests first; confirm they fail for the intended missing behavior.
- Implement typed models before services and renderers.
- Implement services before CLI wiring.
- Run the story's focused tests at its checkpoint before starting dependent work.

## Parallel Opportunities

- T002 and T003 can run in parallel after task generation.
- T004 and T005 can run in parallel; T006 and T007 can then run in parallel.
- US1 test tasks T009-T012 target separate files and can run in parallel after T008.
- US2 test tasks T018-T021 can run in parallel after T017; implementations T022-T024 can run in parallel before T025, and T025 must complete before T026.
- US3 test tasks T028-T031 and implementation tasks T032-T034 each target separate files and can run in parallel after T027.
- US4 test tasks T036 and T037 can run in parallel.
- Cross-cutting tasks T040-T042 can run in parallel after all story checkpoints.

## Parallel Examples

### User Story 1

```text
Task T009: Loader and provenance tests in tests/unit/test_config_loading.py
Task T010: Semantic resolution tests in tests/unit/test_config_resolution.py
Task T011: Campaign schema parity tests in tests/contract/test_campaign_config_contract.py
Task T012: Validate/resolve CLI tests in tests/integration/test_validate_resolve_cli.py
```

### User Story 2

```text
Task T022: Deterministic IDs in src/cloud_network_benchmark/ids.py
Task T023: Collision-safe paths in src/cloud_network_benchmark/paths.py
Task T024: Lifecycle transitions in src/cloud_network_benchmark/lifecycle.py
```

### User Story 3

```text
Task T032: Deployment and remote connection contracts in src/cloud_network_benchmark/contracts/deployment.py
Task T033: Command runner and fakes in src/cloud_network_benchmark/contracts/execution.py
Task T034: Validator input contract in src/cloud_network_benchmark/contracts/validation.py
```

### User Story 4

```text
Task T036: Dry-run unit tests in tests/unit/test_dry_run.py
Task T037: Dry-run CLI tests in tests/integration/test_dry_run_cli.py
```

## Implementation Strategy

### MVP First

1. Complete Phase 1 and Phase 2.
2. Complete User Story 1 through T016.
3. Run only the US1 unit, contract, and integration tests.
4. Stop and review that valid campaigns resolve correctly and every invalid campaign produces no side effects.

### Incremental Delivery

1. Add User Story 2 to create traceable local campaign/child evidence.
2. Add User Story 3 to unblock F02-F08 parallel feature work.
3. Add User Story 4 for reviewer-friendly dry-run output.
4. Complete Phase 7 and record the clean credential-free validation evidence.

### Parallel Team Strategy

After Phase 2, keep one owner on US1 because US2 consumes its resolved model. A second owner can begin US3 against the stable common/artifact contracts. Begin US4 after the resolver and path templates are stable. Avoid concurrent edits to `src/cloud_network_benchmark/cli.py`; apply T016, T026, and T039 sequentially.

## Notes

- `[P]` means different files and no dependency on another incomplete task in the same phase.
- Story labels provide requirement traceability and are omitted from setup, foundational, and polish phases by design.
- Do not add credentials, cloud SDK calls, paid resources, remote state, object storage, agents, databases, queues, Packer, containers, or telemetry platforms.
- Keep phase duration, aggregate stream count, scheduled start, optional scenarios, and provider selections configurable.
- Never overwrite manifests, snapshots, raw artifacts, or occupied result directories.
