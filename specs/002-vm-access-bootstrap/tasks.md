---

description: "Implementation tasks for F02 VM access, bootstrap, readiness, and retrieval"

---

# Tasks: VM Access, Bootstrap, Readiness, and Retrieval

**Input**: Design documents from `/specs/002-vm-access-bootstrap/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/remote-access.md, quickstart.md

**Organization**: Tasks are grouped by user story; all tests are credential-free and fake-backed as required by the specification.

## Phase 1: Setup

- [X] T001 Confirm the F01 command-runner, artifact-path, and deployment-output public contracts in `src/cloud_network_benchmark/` before adding F02 imports
- [X] T002 [P] Add F02 test module placeholders at `tests/contract/test_remote_access_contract.py`, `tests/unit/test_bootstrap.py`, `tests/unit/test_readiness.py`, `tests/unit/test_retrieval.py`, and `tests/integration/test_remote_sequences.py`
- [X] T003 [P] Add non-secret connection/bootstrap/readiness/retrieval fixtures under `tests/fixtures/f02/` with exactly two VMs and explicit VM A/VM B roles

## Phase 2: Foundational

**Purpose**: Shared models, validation, redaction, and failure evidence required by all stories.

- [X] T004 Implement F02 value models for Connection Output, VM Connection, SSH Reference, Timeout Policy, and role literals in `src/cloud_network_benchmark/access.py`; enforce exactly two unique VMs, VM A `client`, VM B `server`, valid distinct private IPv4 addresses, and positive bounded timeout values from `data-model.md`
- [X] T005 Implement structured Remote Action Result and Access Failure Evidence models in `src/cloud_network_benchmark/access.py`; preserve stage, VM, role, action/source path, category, timestamps, deadline, last output, and partial-result references
- [X] T006 [P] Implement secret/reference redaction for command arguments, environment classifications, stdout, stderr, and failure summaries in `src/cloud_network_benchmark/access.py`; never persist private keys or credential contents
- [X] T007 [P] Add contract tests for connection validation, role invariants, IPv4/address validation, timeout validation, redaction, and failure schema in `tests/contract/test_remote_access_contract.py`
- [X] T008 Export the F02 public operation and model names from `src/cloud_network_benchmark/__init__.py` without changing existing F01 serialized contracts

## Phase 3: User Story 1 - Prepare the VM pair (Priority: P1) 🎯 MVP

**Goal**: Render and execute minimal pinned bootstrap for VM A and VM B, verify cloud-init completion, and return role-specific evidence.

**Independent Test**: Fake SSH actions for both VMs and assert the ordered bootstrap/cloud-init sequence, pinned-tool evidence, role setup, timestamps, and failure short-circuit behavior.

### Tests for User Story 1

- [X] T009 [P] [US1] Write failing bootstrap rendering tests for configurable pinned FLENT/netperf versions, required Linux tools, role-specific setup, and cloud-init completion markers in `tests/unit/test_bootstrap.py`
- [X] T010 [P] [US1] Write failing fake bootstrap integration tests for success, command failure, cloud-init failure, timeout, interruption, captured output, and no-readiness-after-failure in `tests/integration/test_remote_sequences.py`

### Implementation for User Story 1

- [X] T011 [P] [US1] Implement configurable Bootstrap Plan and per-VM Bootstrap Record models in `src/cloud_network_benchmark/bootstrap.py`; record resolved pins, ordered action IDs, installed-tool evidence, cloud-init result, timestamps, and `succeeded`/`failed`/`timed_out`/`interrupted` outcomes
- [X] T012 [US1] Implement bootstrap command rendering in `src/cloud_network_benchmark/bootstrap.py` for Ubuntu Linux tools plus pinned FLENT/netperf, using argument-safe commands and explicit client/server role setup
- [X] T013 [US1] Implement `prepare(connection, bootstrap_config, command_runner)` in `src/cloud_network_benchmark/access.py`; validate first, run VM A and VM B bootstrap through F01 SSH CommandRequest values, verify cloud-init completion, and preserve first failure evidence
- [X] T014 [US1] Add bootstrap failure mapping and redacted command evidence in `src/cloud_network_benchmark/bootstrap.py`; distinguish SSH, bootstrap, cloud-init, timeout, and interruption categories without exposing authentication material
- [X] T015 [US1] Make the US1 fake bootstrap tests pass and verify the focused story command `python -m pytest tests/unit/test_bootstrap.py tests/integration/test_remote_sequences.py -q`

**Checkpoint**: VM A and VM B can be prepared independently of Terraform and real SSH, with explicit roles and honest failure evidence.

## Phase 4: User Story 2 - Establish readiness (Priority: P1)

**Goal**: Prove SSH, cloud-init, private-path, and VM B benchmark-server readiness through ordered bounded checks.

**Independent Test**: Fake delayed, failed, and timed-out SSH responses and verify that readiness is granted only after private-path and server-health checks pass.

### Tests for User Story 2

- [X] T016 [P] [US2] Write failing readiness unit tests for ordered checks, polling attempts, deadlines, delayed success, last output, and typed timeout evidence in `tests/unit/test_readiness.py`
- [X] T017 [P] [US2] Extend fake remote sequence tests for SSH-only readiness rejection, private-path failure, server-not-ready, delayed readiness success, readiness timeout, and interruption in `tests/integration/test_remote_sequences.py`

### Implementation for User Story 2

- [X] T018 [P] [US2] Implement Readiness Check and Readiness Record models in `src/cloud_network_benchmark/readiness.py`; encode SSH, cloud-init, private-path, and server-health check types and outcomes from `data-model.md`
- [X] T019 [US2] Implement bounded polling and per-check timeout handling in `src/cloud_network_benchmark/readiness.py`; record attempts, elapsed time, configured deadline, last output, and the original failure category
- [X] T020 [US2] Implement private-path readiness from VM A to VM B using the configured private IPv4 target in `src/cloud_network_benchmark/readiness.py`; ensure public SSH reachability cannot satisfy this check
- [X] T021 [US2] Implement VM B benchmark-server health readiness in `src/cloud_network_benchmark/readiness.py`; accept only the configured healthy condition and exclude FLENT phase execution
- [X] T022 [US2] Implement `check_readiness(connection, readiness_config, command_runner)` in `src/cloud_network_benchmark/access.py`; require successful preparation prerequisites, execute checks in contract order, and return manifest-ready structured evidence
- [X] T023 [US2] Make the US2 fake readiness tests pass and verify `python -m pytest tests/unit/test_readiness.py tests/integration/test_remote_sequences.py -q`

**Checkpoint**: Readiness cannot be declared from SSH alone and all bounded failure paths are inspectable.

## Phase 5: User Story 3 - Retrieve immutable evidence (Priority: P2)

**Goal**: Retrieve declared artifacts through fakeable SCP into exclusive F01 result paths while preserving partial evidence.

**Independent Test**: Fake complete, missing, failed, integrity-failed, timed-out, interrupted, partial, and collision transfers and verify source/destination/role mapping.

### Tests for User Story 3

- [X] T024 [P] [US3] Write failing retrieval unit tests for manifest validation, source VM/role mapping, exclusive destinations, transfer timeout, integrity evidence, and outcome categories in `tests/unit/test_retrieval.py`
- [X] T025 [P] [US3] Add fake SCP integration tests for complete retrieval, missing source, transfer failure, integrity failure, partial success, interruption, timeout, and destination collision in `tests/integration/test_remote_sequences.py`

### Implementation for User Story 3

- [X] T026 [P] [US3] Implement Retrieval Manifest and Retrieval Record models in `src/cloud_network_benchmark/retrieval.py`; require artifact name, source VM/role/path, F01 destination path, expected integrity metadata, and transfer timeout
- [X] T027 [US3] Implement destination preflight and exclusive local transfer handling in `src/cloud_network_benchmark/retrieval.py`; refuse existing evidence destinations and allow only explicitly empty temporary locations
- [X] T028 [US3] Implement SCP request construction and transfer result mapping in `src/cloud_network_benchmark/retrieval.py`; capture action ID, bytes, timing, integrity evidence, redacted output, and missing/failed/timed-out/interrupted outcomes
- [X] T029 [US3] Implement `retrieve(connection, retrieval_manifest, command_runner, filesystem)` in `src/cloud_network_benchmark/access.py`; retain successful artifacts and return partial failure evidence without overwriting F01 paths
- [X] T030 [US3] Make the US3 fake retrieval tests pass and verify `python -m pytest tests/unit/test_retrieval.py tests/integration/test_remote_sequences.py -q`

**Checkpoint**: Declared remote evidence is retrieved safely and incompleteness is never reported as success.

## Phase 6: Polish and Cross-Cutting Validation

- [X] T031 [P] Add public contract assertions for all four operations and action types to `tests/contract/test_remote_access_contract.py`, including the exact sequence guarantees in `contracts/remote-access.md`
- [X] T032 [P] Add F02 API/module documentation and link the data model and contract from `docs/f02-vm-access-bootstrap.md`
- [X] T033 Run the focused F02 suite from `specs/002-vm-access-bootstrap/quickstart.md` and confirm no test invokes cloud credentials, live VMs, Terraform resources, real SSH/SCP, or benchmark phases
- [X] T034 Run the complete existing pytest suite with `python -m pytest -q` and resolve only regressions caused by F02 public-contract integration
- [X] T035 Review changed files for secret leakage, destination overwrite paths, unbounded retry loops, provider Terraform leakage, and accidental FLENT phase semantics before marking F02 complete

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 has no dependencies.
- Phase 2 depends on Phase 1 and blocks all user stories.
- US1 and US2 are both P1; US1 supplies preparation prerequisites for the normal US2 path, but US2 tests may use direct prepared fixtures.
- US3 depends on Phase 2 and may proceed in parallel with US1/US2 using declared artifact fixtures.
- Phase 6 depends on the desired user-story implementations.

### Parallel Opportunities

- T002 and T003 can run in parallel.
- T006 and T007 can run in parallel after T004/T005 model decisions.
- Within US1, T009/T010 can run in parallel; T011/T012 can run in parallel before T013.
- Within US2, T016/T017 can run in parallel; T018 can proceed alongside US1 implementation after foundational models exist.
- Within US3, T024/T025 can run in parallel; T026 can proceed independently of T027/T028 until integration T029.
- US3 can be developed in parallel with US1/US2 after Phase 2.

## Implementation Strategy

### MVP First

1. Complete Phase 1 and Phase 2.
2. Complete US1 preparation and its focused fake tests.
3. Stop and validate the bootstrap sequence independently.

### Incremental Delivery

1. Add US2 readiness while preserving US1 behavior.
2. Add US3 retrieval and partial-evidence behavior.
3. Run cross-cutting validation and the full offline suite.

### Completion Evidence

The feature is ready for implementation review when all 35 tasks are complete, the focused quickstart suite and existing suite pass, and the tests demonstrate every required fake terminal outcome without credentials or live remote actions.

## Phase 7: Convergence

- [X] T036 Replace `bash -lc` bootstrap command construction in `src/cloud_network_benchmark/bootstrap.py` with shell-free argument vectors and add tests proving command arguments preserve paths and configured values without shell interpretation per FR-006, FR-017, and Constitution V (contradicts)
- [X] T037 Preserve provider and scenario metadata in `ConnectionOutput.from_deployment` in `src/cloud_network_benchmark/access.py`; require the normalized deployment handoff to carry the actual scenario instead of fabricating `"unknown"` per FR-001 and Constitution I (partial)
- [X] T038 Implement actual fakeable SCP materialization in `src/cloud_network_benchmark/retrieval.py`; copy or stage successful fake transfer content into the exclusive destination, validate missing/truncated artifacts, and prove complete retrieval in `tests/unit/test_retrieval.py` and `tests/integration/test_remote_sequences.py` per FR-012 and SC-004 (missing)
- [X] T039 Separate cloud-init completion from generic command timeout handling in `src/cloud_network_benchmark/bootstrap.py`; use `cloud_init_seconds`, record configured deadlines and last output, and map interruption distinctly per FR-007, FR-008, FR-015, and SC-006 (partial)
- [X] T040 Add bounded maximum timeout validation and typed timeout evidence to `src/cloud_network_benchmark/access.py`, `src/cloud_network_benchmark/readiness.py`, and `src/cloud_network_benchmark/retrieval.py`; test every configured SSH, command, readiness, and transfer deadline per FR-008 and SC-006 (partial)
- [X] T041 Validate readiness prerequisites using an explicit successful BootstrapResult rather than allowing `check_readiness` to run after failed preparation in `src/cloud_network_benchmark/access.py` and `src/cloud_network_benchmark/readiness.py` per US2/AC1, FR-010, and FR-014 (partial)
- [X] T042 Add a documented successful/failed evidence review fixture and concise reviewer procedure in `docs/f02-vm-access-bootstrap.md` with a test or scripted assertion for the SC-007 under-five-minute inspection criterion per SC-007 (missing)
- [X] T043 Add a repository-local test command or documented `pytest` invocation that always uses the installed project environment, update `specs/002-vm-access-bootstrap/quickstart.md`, and verify imports from a clean checkout per SC-008 (partial)

## Phase 8: Convergence

- [X] T044 Include the configured SSH user, port, authentication reference, and host-key handling in the provider-neutral command requests without persisting secrets; add contract tests for the complete connection invocation per FR-001, FR-006, and US1/AC1 (missing)
- [X] T045 Redact stdout/stderr and command-derived failure details before constructing durable `AccessFailureEvidence` in `src/cloud_network_benchmark/access.py`; add tests proving credentials and private-key material cannot appear in failure records per FR-006, FR-015, and Constitution III (contradicts)
- [X] T046 Replace attempt-count-only readiness polling with deadline-aware polling that records elapsed time, configured deadline, and every attempt, and remove the assertion-based control flow in `src/cloud_network_benchmark/readiness.py` per FR-008, FR-011, and SC-006 (partial)
- [X] T047 Split benchmark-server startup from server-health readiness in `src/cloud_network_benchmark/readiness.py`; make the readiness command observe an explicit healthy condition without launching FLENT or netserver as a side effect per FR-010 and US2/AC2 (contradicts)
- [X] T048 Record transfer size and computed checksum evidence for every successfully retrieved artifact, reject empty or truncated materialization, and test real-success and fake-success paths in `src/cloud_network_benchmark/retrieval.py` per FR-012, FR-013, and SC-004 (partial)
- [X] T049 Add a committed successful/failed evidence JSON fixture plus an executable inspection assertion or documented review script in `tests/` and `docs/f02-vm-access-bootstrap.md` per SC-007 (missing)

## Phase 9: Convergence

- [X] T050 Pass the configured SSH `authentication_reference` as the runtime identity option in `_request()` in `src/cloud_network_benchmark/access.py`, while retaining secret-reference redaction, and add an exact argv contract test per FR-001, FR-006, and T044 (partial)
