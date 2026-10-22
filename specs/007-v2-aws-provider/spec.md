# Feature Specification: F05 V2 AWS provider

**Feature Branch**: `codex/f05-v2-aws`
**Created**: 2026-10-22
**Status**: Planned; implementation pending
**Input**: F05 exact prompt/acceptance in Thesis `tracking/IMPLEMENTATION_PROMPTS.md`; independent AWS expiry and provider driver, merged F01R base `cef838e42fc620f90b5943f5a8d1528ddc0b0724`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Protect the pair before creation (Priority: P1)

The operator needs an admitted AWS attempt protected against controller loss before either VM exists.

**Why this priority**: Independent compute stop must survive loss of the local controller.
**Independent Test**: Fake provider responses prove reservation/expiry ordering and reject unprotected creation.
**Acceptance Scenarios**:

1. **Given** a complete active reservation and isolated attempt, **When** expiry is registered, **Then** authenticated evidence for every involved region and its absolute deadline precede the first VM creation request.
2. **Given** failed, uncertain or partial expiry registration, **When** provisioning is requested, **Then** zero VMs are created and failure/recovery identifiers are retained.
3. **Given** controller loss when zero, one or two VMs may exist, **When** expiry runs, **Then** it discovers only this attempt's VMs without preknown IDs and requests compute stop; disks and other leftovers remain explicitly uncleaned.

### User Story 2 - Obtain the selected private AWS topology (Priority: P2)

The researcher needs two comparable and identifiable AWS machines, private benchmark traffic and authenticated management access.

**Why this priority**: Incorrect placement, shape or route invalidates the selected scenario.
**Independent Test**: S1–S4 fixtures yield two normalized roles and exact topology/security settings; malformed outputs fail honestly.
**Acceptance Scenarios**:

1. **Given** same_zone or cross_zone, **When** creation completes, **Then** A/B use the selected Frankfurt zone(s), default placement and private IPv4 paths.
2. **Given** placement_optimization, **When** creation completes, **Then** both VMs belong to one Cluster Placement Group in Frankfurt's selected zone.
3. **Given** admitted inter_region, **When** creation completes, **Then** A is in Frankfurt and B in London with reciprocal private VPC peering routes; missing admission blocks creation.
4. **Given** provider-authenticated host evidence, **When** management outputs are emitted, **Then** strict host identity is available; absent, stale, mismatched or ambiguous evidence prevents usable connections.

### User Story 3 - Explain and finish failed attempts (Priority: P3)

The operator needs recoverable evidence and verified deletion of all billable experiment resources after success or failure.

**Why this priority**: A successful destroy command or emergency compute stop alone does not prove cleanup.
**Independent Test**: Fake apply/output/destroy/inventory failures preserve primary errors and distinguish absence, leftovers and unknown inventory.
**Acceptance Scenarios**:

1. **Given** partial creation or malformed output, **When** failure occurs, **Then** cleanup uses the same private state and available partial identities.
2. **Given** deletion completes, **When** inventory is checked, **Then** VMs, boot disks, public addresses and attempt infrastructure are absent in all involved regions before expiry protection is cancelled and removed.
3. **Given** a leftover, denied verification or failed cancellation, **When** the attempt finishes, **Then** cleanup stays open with recovery evidence and a launch blocker.

### Edge Cases

Lost registration response; second region registration failure; expired/insufficient cap; concurrent P0/P1 attempts using identical CIDRs but separate networks/state/tags; route absent; extra/missing VM role; logical versus physical zone identity; image owner/architecture mismatch; placement capacity failure; missing console keys; keyscan mismatch; successful destroy with disk leftover; denied inventory; repeated cleanup; cancellation racing expiry; in-flight creation completing after controller loss; throttling and delayed tag visibility.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Accept existing F01R AWS deployment/reservation/expiry contracts only. Require matching identities, active complete reservation, dated identified cost inputs and no unresolved cleanup. Honor admission's priority review gates; F05 does not implement F11 or authorize paid launches.
- **FR-002**: Use exactly two On-Demand Ubuntu Server 24.04 LTS amd64 `m7i.xlarge` VMs, one NIC and 32 GiB gp3 boot disk each, ENA without Express/EFA, default tenancy/offloads. Do not substitute shape, zone or CIDR. Record actual image/owner/architecture/shape/acceleration evidence; explain unavailable values.
- **FR-003**: Support same_zone (eu-central-1a), cross_zone (eu-central-1a/1b), placement_optimization (same eu-central-1a Cluster Placement Group), and gated inter_region (A eu-central-1a, B eu-west-2a). Preserve logical and physical zone IDs; do not claim rack/hardware equivalence.
- **FR-004**: Use run-scoped Frankfurt `10.60.0.0/16`, subnets `10.60.1.0/24` and `10.60.2.0/24`; London `10.61.0.0/16` with explicitly selected /24. Verify non-overlap and reciprocal private peering routes for inter_region. Declare MTU 1460, CUBIC, DSCP 0 and ECN off as preparation inputs; guest verification belongs to F02R/F04.
- **FR-005**: Give each VM one management public IPv4. SSH is limited to validated controller IPv4 /32. B's UDP 11111/TCP 5201 accepts only A private IPv4; allow private ICMP connectivity checks. Internet access is preparation-only; no NAT, bastion or load balancer.
- **FR-006**: Supply dedicated attempt authentication and strict known-hosts references. Authenticate host fingerprints through provider identity bound to account/region/instance/boot; keyscan alone is insufficient. Reject mismatches and missing trust. Never export credentials, private keys or secret state.
- **FR-007**: Emit existing normalized A/B outputs with actual locality/image/shape/public/private addresses, placement, redacted provider evidence and documented AWS baseline/burst limits plus source/date/caveats. Explain partial output; never fabricate missing values.
- **FR-008**: Isolate private local state, keys, command deadlines, ownership tags and recovery evidence per attempt. Concurrent P0/P1 must not share namespaces. Preserve redacted command outcomes/timing through replaceable external boundaries; upstream accounts for four concurrent VMs.
- **FR-009**: Activate the supplied reservation/cap-start event before the first expiry-side effect; then register independent stop expiry in every involved region and confirm registration before VM creation. Reject failed/uncertain registration, expired caps or insufficient remaining complete-workflow time. Never restart the cap at apply/boot.
- **FR-010**: Select the smallest independent AWS compute-stop mechanism that finds not-yet-known IDs through exact attempt identity. Record registration, trigger/failure, cancellation, permissions, dated costs and limits. Provider delay, transient failures and late in-flight creation must remain visible; compute stop is distinct from deletion and cannot be replaced by guest shutdown.
- **FR-011**: After possible creation, attempt normal cleanup on every terminal path. Delete and verify VMs/disks/public addresses/network/placement/expiry support resources in all involved regions. Keep protection until compute absence is established. Preserve primary and secondary failures; unknown inventory or leftovers keep cleanup open and block launches.
- **FR-012**: Prove S1–S4, registration ordering, controller loss, partial creation, authenticated identity, cancellation and leftovers using credential-free static checks/fakes. No cloud calls, real Terraform plans/applies or benchmark traffic during this offline gate. Real permissions/networking/stop/deletion await separate paid acceptance.

### Key Entities *(include if feature involves data)*

- **AWS attempt**: Existing campaign/run/deployment identity, two VM roles, topology, local state and reservation.
- **Expiry registration**: Existing expiry request/evidence with aggregate ID, regional execution IDs, exact target selector, deadline and failure/cancellation state.
- **Deployment output**: Existing normalized A/B outputs plus observed AWS metadata and documented limits.
- **Cleanup evidence**: Region/resource inventory, deletion/verification outcomes, unknowns and residual launch blocker linked to the attempt.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All four scenario fixtures describe exactly two selected machines and the required private topology, with zero silent substitutions.
- **SC-002**: Every simulated creation trace records active reservation then confirmed independent expiry before VM creation; all failed/uncertain registrations create zero VMs.
- **SC-003**: Every simulated controller-loss case selects only its attempt even with no IDs at registration; compute stop and complete deletion remain separate outcomes.
- **SC-004**: Every missing/mismatched host identity and malformed output is rejected or explicitly partial; exported evidence contains zero private keys/credentials.
- **SC-005**: Every terminal path after possible creation attempts cleanup; every residual/unverifiable billable resource leaves cleanup open. Verified absence plus protection removal closes the attempt.
- **SC-006**: Offline acceptance uses zero cloud credentials/resources and records exact commands/results; real-provider proof remains a later acceptance gap.

## Assumptions

Merged F01R is the sole dependency. Unfinished F02R/F04/F09/F11 code is not imported. User authorization covers engineering choices and artifact reviews, not research changes/cloud execution. London subnet is `10.61.1.0/24`. Cap start is supplied reservation activation immediately before expiry side effects. Missing cost/admission/time evidence is refused. Exact N, study dates and availability remain evidence-dependent.

## Clarifications

### Session 2026-10-22

Zero human questions. Engineering resolutions: unknown IDs use EC2 discovery at expiry time; protection is registered separately from VM creation; authenticated console fingerprints establish host trust; inventory verifies deletion; no failed-copy retention policy is introduced. Mechanism/timing/IAM decisions are in research.md and contracts/aws-provider.md. All clarification taxonomy categories are Clear after these resolutions; research-result values are intentionally outside this feature.
