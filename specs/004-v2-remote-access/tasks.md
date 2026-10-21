# Tasks: F02R V2 remote preparation and native retrieval

**Input**: Design documents from `/private/tmp/cnb-f02r/specs/004-v2-remote-access/`
**Prerequisites**: Checked spec.md, plan.md, research.md, data-model.md, contracts/remote-access.md and quickstart.md; merged F01R `cef838e42fc620f90b5943f5a8d1528ddc0b0724`.
**Tests**: Explicitly required by FR-013. Add focused failing cases before behavior changes; retain useful delivered F02 safety checks and replace superseded FLENT assertions. All remote commands/clock/transfer materialization are fake. No real cloud/SSH/SCP/tool traffic.
**Organization**: One writer in this worktree. Exact paths are relative to its root. Task markers indicate completed implementation, not planning completion. No task is implemented yet.

## Phase 1: Setup

- [ ] T001 Record the actual base/head, existing F02 migration targets, frozen-contract/owned-file boundaries and baseline command/result in `docs/f02r-remote-access.md`; use `specs/004-v2-remote-access/research.md` as design rationale, without rewriting historical specs 001/002 (FR-001, FR-013).
- [ ] T002 Add small synthetic source/help/compiler/listener/closed-inventory evidence and reusable fake clock/materializing runner helpers under `tests/fixtures/f02r/`; fixtures must contain no credentials and invoke no external command (FR-003–004, FR-013).

## Phase 2: Foundational local boundary

All stories depend on T001–T005. Shared F01R `contracts/` and schemas remain unchanged.

- [ ] T003 Add failing cases for complete/partial/mismatched V2 handoffs, missing/empty strict identity files, exact SSH/SCP options, remote injection/control/option-like values, finite deadline constraints and nested successful/failed redaction in `tests/contract/test_remote_access_contract.py`; keep tests credential-free and reject legacy silent defaults (FR-001–002, FR-007, FR-012–013).
- [ ] T004 Extend existing `ConnectionOutput` and add original `RemoteDeadline` in `src/cloud_network_benchmark/access.py` per data-model.md: preserve copied F01R identities; roles are A/B and private addresses distinct/usable; deadlines are finite absolute monotonic instants, readiness <= overall, `cleanup_reserve_seconds` is finite and positive, UTC cap expiry follows start; no fresh cap/time default. Retain positive finite per-command ceilings (FR-001, FR-007, FR-012).
- [ ] T005 Migrate `_request`/facades and copy-option helpers in `src/cloud_network_benchmark/access.py` to required strict noninteractive key/known-host identity, local reference validation, safe quoted single remote command, literal path/user/name validation and full success/failure nested durable redaction; keep frozen `CommandRunner` and require original deadline arguments from the contract (FR-002, FR-012–013).

## Phase 3: User Story 1 — Prepare a trustworthy VM pair (P1; MVP)

**Goal**: Prepare source-verified tools on A/B and persistent private B servers.
**Independent test**: Given a synthetic complete deployment and original deadline, exact fake commands succeed only after source/build/capability observations, with one matching listener per tool. Every injected verification/identity/mismatch failure stops preparation.

- [ ] T006 [US1] Replace legacy setup assertions with failing source verification/order, compiler/binary observation, malformed/missing help, build failure and deadline cases in `tests/unit/test_bootstrap.py`; confirm no FLENT/netperf path or real tool invocation (FR-003–004, FR-013; SC-001).
- [ ] T007 [US1] Migrate `BootstrapConfig`/`SourceBuild`/`BootstrapResult` in `src/cloud_network_benchmark/bootstrap.py`: tool is sockperf or iperf3, exactly one of each; credential-free explicit HTTPS URL/reference, safe single archive basename, `source_sha256` exactly 64 lowercase hex characters; safe absolute deployment workspace/prefix; reject legacy fields/arbitrary setup shell; success requires observed both-VM/tool identity/capabilities and matching listeners (FR-003–005).
- [ ] T008 [US1] Implement cloud-init-before-install and fixed tool-specific download→SHA-256 verify→extract/build/install→compiler/binary/version/hash/help observations in `src/cloud_network_benchmark/bootstrap.py`; use absolute installed binaries, short-circuit failures, reuse only matching verified installs and preserve actual/missing evidence. Every action uses the same original readiness/overall deadline and command ceiling (FR-003–004, FR-007, FR-012).
- [ ] T009 [US1] Implement deployment-owned persistent B Sockperf private UDP 11111 and iperf3 private TCP 5201 streaming/forceflush units, append-safe F01R log destinations and idempotent narrow A-private-/32 guest ingress rules in `src/cloud_network_benchmark/bootstrap.py`; preserve management access; reuse matching live state, fail mismatches without restarts/duplicates/log truncation; no automatic restart policy (FR-005).
- [ ] T010 [US1] Extend `tests/unit/test_bootstrap.py` for exact B server argv, private protocol/address/port/ACL semantics, repeated matching preparation, stale/conflicting unit/source/binary and log preservation; ensure missing capabilities stop server preparation and all actions stay fake (FR-003–005, FR-013; SC-001).

**Checkpoint**: US1 can be tested independently. This is preparation only, not a measurement launch.

## Phase 4: User Story 2 — Observe readiness within one deadline (P1)

**Goal**: Return a bounded observation-only readiness decision for the exact prepared deployment.
**Independent test**: A synthetic successful preparation plus fake clock satisfies fixed predicates; late success, wrong listener or preparation mismatch fails without start/restart or another deadline.

- [ ] T011 [US2] Add cumulative fake-time failure cases across both VMs, preparation/build time, SSH/cloud-init/private/listener checks and polls in `tests/unit/test_readiness.py`; assert shrinking per-command budgets, no command after zero and no readiness mutation, including slow earlier checks followed by apparently healthy late checks (FR-006–007, FR-012–013; SC-002).
- [ ] T012 [US2] Replace boolean `bootstrap_succeeded=True` and arbitrary health overrides in `src/cloud_network_benchmark/readiness.py` with required matching successful preparation, positive finite poll interval/attempt ceiling, original deadline and ordered observation evidence; retain distinguishable poll/deadline failures and last observation (FR-006–007).
- [ ] T013 [US2] Implement fixed SSH A/B, cloud-init-done A/B, ICMP A→B, TCP connect A→B:5201, B unit/process/binary identity, exact private UDP/TCP binding and guest-ingress predicates in `src/cloud_network_benchmark/readiness.py`; every command/wait is bounded by original remaining readiness/overall time; readiness never installs/starts/restarts/modifies firewall (FR-005–007, FR-012).
- [ ] T014 [US2] Cover each unhealthy predicate, wrong/wildcard/public address, wrong port/protocol/process/binary/ACL, stale preparation identity, interrupted checks and missing cloud-init completion in `tests/unit/test_readiness.py`; document that UDP listener plus ACL is not end-to-end UDP delivery proof (FR-006–007, FR-013; SC-002).

**Checkpoint**: US2 tests use supplied preparation evidence and do not depend on running US1 remotely. Production calls use US1's matching result.

## Phase 5: User Story 3 — Preserve verified native files after measurement (P1)

**Goal**: Retrieve closed native files from both roles with materialized integrity, immutable local paths and bounded failure attempts.
**Independent test**: An ended/closed synthetic inventory drives physical fake transfer bytes; verify each final `ArtifactRecord`, preserve failed attempts and stop copying before cleanup allowance/cap.

- [ ] T015 [US3] Add failing tests in `tests/unit/test_retrieval.py` for pre-end/open producer rejection, both-role native inventories/dynamic chunk paths, strict remote size/hash, successful exit without materialization, corruption/truncation/zero-byte logs and optional absence (FR-008–009, FR-013; SC-003–004).
- [ ] T016 [US3] Migrate `RetrievalItem`/`RetrievalManifest`/`RetrievalResult` in `src/cloud_network_benchmark/retrieval.py` per data-model.md: exact deployment/role/unit identities; unique safe native destinations; closed inventory requires UTC `producers_closed_at >= measurement_ended_at`; required observed size is a nonnegative integer and hash exactly 64 lowercase hex; positive finite item timeout; nonempty `ArtifactInput` declarations with every remote declaration resolved exactly once to a concrete item or explicit `unavailable_items` entry, and full independent failure list (FR-008–010).
- [ ] T017 [US3] Implement pre-transfer validation/remote closed-file inventory observations and both-role native artifact selection in `src/cloud_network_benchmark/retrieval.py`, consuming F01R declarations/resolved chunk IDs without importing siblings; preserve controller files and reject active/open measurement before remote copying (FR-008–009).
- [ ] T018 [US3] Implement contained unique attempt staging, actual SCP materialization checks, bounded-chunk local hashing, exact remote/local size/hash comparison, no-overwrite publication and verified/failed F01R `ArtifactRecord` output in `src/cloud_network_benchmark/retrieval.py`; remove stdout-to-file fabrication and verify final materialized records with shared helpers (FR-009–010; SC-003).
- [ ] T019 [US3] Add at most one retry per artifact with distinct preserved staging path, continuation across ordinary item failures, immediate interruption/exhaustion stop and original overall budget minus caller cleanup allowance in `src/cloud_network_benchmark/retrieval.py`; always return cleanup-required and never overwrite the caller's primary failure, retain disks, register expiry, reset cap or start another timer/experiment (FR-010–012; SC-004).
- [ ] T020 [US3] Cover traversal/symlink/collision/duplicate ID/destination and hostile remote paths, interrupted/failed/truncated/missing materialization, partial bytes, later valid retry, max-two-attempt limit, remaining-time exhaustion, optional absence and one-VM loss in `tests/unit/test_retrieval.py`; assert exact SCP options, earlier verified evidence survives and zero bytes verifies only against observed zero size (FR-002, FR-008–013; SC-003–004).

**Checkpoint**: US3 uses synthetic closure/layout evidence and fake physical file creation; no measurement runner or provider driver is required.

## Phase 6: Cross-cutting integration and delivery

- [ ] T021 Migrate existing fake sequence coverage in `tests/integration/test_remote_sequences.py` to required original deadlines, source config and V2 evidence, preserving its fail-before-next-VM behavior without weakening source/capability predicates (FR-001, FR-003–007, FR-013).
- [ ] T022 Add `tests/integration/test_v2_remote_access.py` covering all four frozen F01R modes, original cap-context ordering (`cap_started_at <= expiry.registered_at <= creation`) as supplied evidence, matching prepare→observe→closed retrieval, retained-pair chunk final export, cumulative deadlines, secondary retrieval failure with caller primary failure and cleanup-required; use only local synthetic files/runner/clocks, no sibling implementation imports (FR-001–013; SC-001–004).
- [ ] T023 Update only remote import/export entries in `src/cloud_network_benchmark/__init__.py` if needed for migrated facade types; preserve unrelated exports and reject legacy FLENT bootstrap inputs explicitly in the updated `tests/contract/test_remote_access_contract.py` (FR-001, FR-003, FR-013).
- [ ] T024 Run focused/full commands from `specs/004-v2-remote-access/quickstart.md`, `git diff --check` and ownership/historical-spec/frozen-contract diff inspection; record exact tested head, command exit results, counts, assumptions and unrun real checks in `docs/f02r-remote-access.md` (FR-001–013; SC-001–004).

## Dependencies & execution order

Setup T001→T002 precedes foundations T003→T004→T005. Within each story, add its failing tests, then models/behavior, then edge coverage. US1 T006→T007→T008→T009→T010; US2 T011→T012→T013→T014; US3 T015→T016→T017→T018→T019→T020. Production readiness consumes US1, but US2's independent tests use a synthetic successful `BootstrapResult`; US3's tests use a synthetic ended/closed inventory. T021–T024 follow all stories. All application work stays in the existing four modules plus narrowly scoped root exports.

No task is marked [P]: there is one authorized writer and sequential changes to shared local remote helpers. Independent test commands can be batched after the prerequisites; parallel-agent execution is not required.

## Parallel examples per story

Once implementation exists, US1's bootstrap tests and remote transport contract checks can run concurrently; US2's observation and deadline cases can run as separate test selections; US3's integrity and path/cap cases can run separately. They use isolated temporary roots/clocks and require no simultaneous writes. Execute artifact/file edits sequentially in this assigned worktree.

## Implementation strategy and ownership

Deliver US1 as the first independently testable increment, then US2 and US3; this is an internal incremental boundary, not permission to stop before completing the assigned feature. Use ordinary functions and existing contracts. Luna Medium owns only plan-listed application/test paths, `tests/fixtures/f02r/`, `docs/f02r-remote-access.md` and implementation progress in this tasks file. It is not alone; preserve others' edits. Any shared contract amendment belongs to the coordinator before use. Sol Medium then runs actual Spec Kit convergence; the coordinator owns publication and merges.

Do not edit Thesis, historical specs 001/002, shared F01R contracts/JSON schemas/configs or coordinator state. No Terraform resources, measurement phases, diagnostics sampler, parser/statistics, budget reservation/expiry registration/deletion or cloud tests are implementation tasks here.
