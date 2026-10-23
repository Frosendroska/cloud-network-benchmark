# Feature Specification: F06 V2 Azure provider and expiry

**Feature Branch**: `codex/f06-v2-azure`

**Created**: 2026-10-22

**Status**: Checked for planning; implementation pending

**Input**: Current Thesis `tracking/IMPLEMENTATION_PROMPTS.md`, F06: exactly two On-Demand Ubuntu 24.04 amd64 Standard_D4s_v5 VMs with 32 GiB Standard SSD LRS E4, Accelerated Networking, four explicit scenarios, authenticated management access, independent pre-create Azure deallocation expiry, normalized evidence and complete cleanup. Offline implementation only.

## Clarifications

### Session 2026-10-22

- No human questions required. Routine engineering decisions use the authorized workflow and are recorded in [research.md](research.md). Functional scope, identities, flow/error handling, security, dependencies, constraints, terminology and completion signals are clear. No research decisions changed.
- The complete lifetime starts at reservation activation before expiry setup. Registration consumes that lifetime. An earlier emergency trigger is allowed only if the complete remaining workflow fits before it; it never extends the reservation.
- Expiry is an independent deallocation fallback, not deletion or a hard billing guarantee. Failed copying never invents a new retention deadline or authorizes a disk-recovery workflow.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Request a comparable private Azure pair (Priority: P1)

An experiment operator receives exactly the requested Azure A/B pair and traceable connection details without silently changing placement, images or networking.

**Why this priority**: The pair is the Azure instrument's measurement boundary.

**Independent Test**: Static deployment checks and simulated provider responses cover all four scenarios, invalid inputs and normalized full/partial outputs.

**Acceptance Scenarios**:

1. **Given** an admitted Azure request with confirmed matching expiry, **When** same_zone, cross_zone, placement_optimization or inter_region is prepared, **Then** there are exactly A and B, each with the selected shape, image family, disk and acceleration, and scenario-specific private routing.
2. **Given** same_zone or placement_optimization, **When** locality is selected, **Then** both VMs use germanywestcentral logical zone 1; only placement_optimization has a shared Standard Proximity Placement Group.
3. **Given** cross_zone or inter_region, **When** locality is selected, **Then** cross_zone uses germanywestcentral zones 1/2, and inter_region uses germanywestcentral zone 1 to uksouth zone 1 through Global VNet Peering. Offline support does not admit paid P2/P3 execution.
4. **Given** public management addresses, **When** the pair is handed off, **Then** SSH is key-only from controller /32, host identity is verified through authenticated Azure evidence, and B's benchmark ports accept only A's private address.

### User Story 2 - Keep independent compute expiry active (Priority: P1)

The operator can lose the controller while a previously registered Azure action still attempts to deallocate both exact experiment VMs.

**Why this priority**: Controller failure must not leave compute relying on a local timer or guest shutdown.

**Independent Test**: An independent fake provider clock drives expiry after controller termination and records per-target outcomes, without Azure access.

**Acceptance Scenarios**:

1. **Given** an active complete-workflow reservation, **When** expiry is registered, **Then** observed schedule, executable action, target scope and permission evidence are confirmed before any VM-creating action; missing or contradictory evidence blocks creation.
2. **Given** registration failure, exhausted registration lead time, expired reservation or insufficient remaining lifetime/cost, **When** provisioning is requested, **Then** no VM creation occurs and any preparatory resources are accounted for and cleaned up.
3. **Given** controller loss after either VM may exist, **When** the scheduled action runs, **Then** it attempts Azure deallocation for both targets independently, records missing/creating/failed targets honestly and preserves residual cleanup obligations.

### User Story 3 - Preserve failures and establish full cleanup (Priority: P2)

The operator receives usable partial evidence and a factual cleanup result even after partial provisioning, output failure, identity failure or interrupted deletion.

**Why this priority**: Result validity and future admission depend on knowing what remains billable.

**Independent Test**: Simulated partial apply, failed output, deallocation and deletion exercise recovery inventory and residual-resource blocking.

**Acceptance Scenarios**:

1. **Given** creation may have started, **When** any stage fails or is interrupted, **Then** available failure/output/recovery evidence is preserved and normal cleanup is attempted within the original budget.
2. **Given** successful deletion commands, **When** inventory cannot prove absence or still finds VMs, disks, public addresses or expiry resources, **Then** cleanup remains failed/open and further launches are blocked.
3. **Given** both target VMs and their other experiment resources are verified absent, **When** expiry is cancelled and its resources removed, **Then** cleanup may succeed; a deallocated VM alone never satisfies cleanup.

### Edge Cases

- Same-week P0/P1 executions share the prescribed CIDRs but have separate unpeered networks, attempt names, state, identities and reservations; no cross-attempt peering or deletion is permitted.
- Allocation failure, unavailable SKU/zone/image, malformed output, missing Accelerated Networking, wildcard listeners, unauthenticated host keys and scope mismatch fail without substitutions.
- ARM accepts asynchronous actions, VM creation can remain in flight, expiry can run late, and a target may be temporarily absent. None is silently converted to completed deallocation or deletion.
- Lost local state, permissions revoked after registration, cancellation failure, unknown inventory and elapsed cleanup deadline leave recovery evidence and block launches.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The provider MUST consume the merged V2 Azure deployment/reservation/expiry contracts, reject missing, mismatched or unadmitted launch evidence, and preserve experiment/campaign/observation/run/deployment identity and source hashes.
- **FR-002**: Each deployment MUST contain exactly two Regular On-Demand Ubuntu Server 24.04 amd64 Standard_D4s_v5 VMs, one 32 GiB Standard SSD LRS E4 boot disk and one accelerated NIC per VM, and actual image identity. No Spot settings or resource substitutions are allowed.
- **FR-003**: The provider MUST implement the four scenario localities above, shared Standard PPG only for placement_optimization, and bidirectional private Global VNet Peering only for inter_region. Azure logical zones MUST NOT be presented as cross-subscription physical identities; unknown physical IDs stay explicitly unavailable.
- **FR-004**: Networks MUST use Frankfurt 10.70.0.0/16 with a /24 subnet and London 10.71.0.0/16 when needed, private A-to-B measurement traffic, MTU 1460, CUBIC, DSCP 0, ECN off and default offloads. Each VM has one public IPv4 for management only. SSH admits controller /32 only; B admits A private /32 on UDP 11111, TCP 5201 and connectivity ICMP; other sources are denied explicitly, including Azure's broad default virtual-network allowance.
- **FR-005**: Management handoff MUST bind each host key to authenticated Azure VM/resource identity before first SSH, provide private key/known-host references privately, require strict checking and key-only access, and fail on missing/malformed/mismatched identity. Credentials, keys and secret-bearing state MUST never enter exported evidence or Git.
- **FR-006**: Attempt names, local state, working files, expiry targets and recovery inventory MUST be isolated and collision-safe, preserving independent concurrent P0/P1 pairs. Existing foreign state/resources MUST NOT be adopted or overwritten.
- **FR-007**: The provider MUST return normalized full or explained partial A/B outputs, actual image/locality/placement/acceleration and documented Azure maximum aggregate 12.5 Gbit/s network metadata with its source and limitations; no guaranteed throughput, invented host details or AWS baseline/burst semantics.
- **FR-008**: Before either VM can be created, the provider MUST confirm an independent date-specific Azure deallocation action covering both deterministic target IDs. Registration ID/time, trigger, target selector, executable action identity, permission scope and failure/cancellation/trigger evidence MUST be retained. Azure guest power-off is insufficient.
- **FR-009**: The provider MUST enforce reservation activation before expiry registration before VM creation; all expiry preparation, provisioning, bootstrap, measurement, final copy/verification and cleanup must fit the original cap and reserved costs. Missing dated expiry cost inputs, insufficient schedule lead or insufficient time before an earlier trigger MUST reject launch. Caps remain 1,200/1,800/87,600 seconds for short-or-acceptance/calibration/day, with $300 total and $50 protected; F11 owns admission accounting.
- **FR-010**: Independent expiry MUST attempt deallocation separately for A and B after controller loss and report asynchronous, absent, still-creating, denied and failed outcomes honestly. It MUST preserve enough Azure-side schedule/runbook/identity evidence for recovery, with bounded retries, no restart/create/delete permission and no claim of guaranteed dispatch or completion by the cap.
- **FR-011**: Every terminal path after preparatory or VM creation may have started MUST preserve primary and secondary failures and attempt full cleanup. Full deletion MUST account for VMs, boot disks, NICs, public addresses, networks/peerings/PPG, expiry account/action/schedule/roles and any other experiment resources. Unknown/residual billable inventory MUST fail cleanup and block launches; cancelling expiry before both VMs are verified absent is forbidden.
- **FR-012**: Credential-free static, contract and fake integration checks MUST cover all topologies, real command arguments, registration-before-create, expiry controller independence, deadlines, partial creation, host-identity mismatch, failed cleanup and redaction. They MUST make zero cloud calls or traffic and explicitly distinguish offline checks from later paid acceptance.

### Key Entities

- **Azure deployment**: Matching V2 request/output, A/B resource IDs, actual locality/image/placement/network context and private management references.
- **Expiry registration**: Matching reservation and target pair, Azure-side independent executable action/schedule/identity and observed registration/trigger/cancellation states.
- **Recovery and cleanup evidence**: Private state location, expected and observed resource inventory, commands/timestamps, primary/secondary failures and unresolved deletion obligations.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All four deployment scenarios pass positive and negative offline checks with exactly two correctly attributed VMs and no silent configuration substitution.
- **SC-002**: Every simulated VM-creating path is preceded by matching confirmed expiry; every missing/failed/expired/mismatched registration case results in zero VM-creating actions.
- **SC-003**: Independent fake-provider expiry handles both target identities after the controller stops, while failed or unobserved outcomes remain explicit and distinct from deletion.
- **SC-004**: Every tested terminal creation failure retains primary evidence and cleanup results; every residual or unknown billable resource prevents successful cleanup and subsequent launch eligibility.
- **SC-005**: Static and fake checks require zero credentials, cloud calls or real benchmark traffic; secret sentinel checks find no leaked private material in durable success or failure evidence.

## Assumptions

- This feature consumes merged F01R contracts at the assigned baseline; no unmerged sibling is a dependency and shared schemas are not changed.
- F09 composes lifecycle/retrieval and F11 owns reservation activation/admission. F06 consumes their DTOs through synthetic fixtures until integration; it does not implement their runtime behavior.
- Concrete Azure expiry is a narrow provider safety action; its chosen mechanism, permissions, runtime, pricing inputs and limitations belong in research/plan/contracts. Separate paid acceptance must verify real availability, RBAC, dispatch/deallocation and complete deletion before research.
- Stock Azure guest-agent capabilities may authenticate initial host identity during bootstrap; no additional monitoring or persistent framework agent is introduced. Measurement starts only after normal preparation/readiness by the owning feature.
- N, UTC study windows, live prices and P2/P3 paid admission remain evidence-dependent. User permission to implement V2 establishes no formal advisor approval.
