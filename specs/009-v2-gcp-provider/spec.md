# Feature Specification: F07 V2 GCP provider

**Feature Branch**: `codex/f07-v2-gcp`
**Created**: 2026-10-22
**Status**: Specified and clarified for offline implementation
**Input**: Current Thesis F07 prompt: two selected GCP VMs, explicit S1–S4 topology, authenticated management identity, independent compute-stop expiry registered before creation, normalized evidence and verified cleanup. Scope ends at credential-free proof.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Protect an admitted deployment before creating VMs (Priority: P1)

The researcher can admit one GCP attempt only after its complete workflow is reserved and an independent automatic compute-stop action is registered for both intended VMs.

**Why this priority**: Controller loss must not leave unprotected compute running.
**Independent Test**: Simulated registration and fake time prove the ordering without creating resources.

**Acceptance Scenarios**:

1. **Given** a current reserved attempt with sourced costs and sufficient remaining workflow time, **When** protection is registered, **Then** both VM roles have observed pre-create protection, bound to the same original cap and attempt.
2. **Given** failed, partial, stale, mismatched or uncertain protection, **When** creation is requested, **Then** neither VM is created and available failure/recovery evidence is retained.
3. **Given** protection registered and VM creation begun, **When** the controller disappears, **Then** the already registered provider action remains responsible for automatic compute stop; local process survival is unnecessary.

### User Story 2 - Obtain the selected private GCP pair (Priority: P2)

The researcher receives exactly two traceable, comparable GCP VMs for the selected scenario, with strict authenticated management connections and private A-to-B measurement addressing.

**Why this priority**: The deployment must represent the approved experiment rather than a silently substituted machine or topology.
**Independent Test**: Static topology checks and provider-response fixtures cover all four scenarios and output/trust failures.

**Acceptance Scenarios**:

1. **Given** a protected S1, S2, S3 or admitted S4 input, **When** deployment succeeds, **Then** exactly A and B use the selected machine, image, disk, NIC, network and placement settings, and observed output agrees with the requested identities.
2. **Given** S3, **When** placement is described, **Then** the shared regional COLLOCATED compact policy has no maximum-distance setting and is not claimed equivalent to other providers' mechanisms.
3. **Given** S4, **When** private connectivity is described, **Then** Frankfurt and London subnets belong to one custom-mode VPC; no public measurement path or peering substitution is introduced.
4. **Given** absent, stale or mismatched authenticated host-key evidence, **When** management connection data is normalized, **Then** connection readiness is not asserted and the attempt fails with available partial evidence.

### User Story 3 - Close cleanup with evidence (Priority: P3)

The researcher can distinguish stopped compute from complete removal, recover private attempt state, and block later launches while owned resources or unverifiable cleanup remain.

**Why this priority**: Stopping compute leaves disks and potentially addresses billable.
**Independent Test**: Simulated destroy, inventory, interruption and repeated cleanup prove closure rules and failure preservation.

**Acceptance Scenarios**:

1. **Given** normal completion or a terminal error after creation may have started, **When** cleanup runs, **Then** deletion is attempted and observed absence covers VMs, disks, addresses and all remaining attempt resources before cleanup succeeds.
2. **Given** a stopped VM, leftover disk/address, denied inventory or incomplete pagination, **When** cleanup is evaluated, **Then** cleanup remains open, new launches are blocked, and primary and cleanup failures remain distinguishable.
3. **Given** a partial creation or interrupted cleanup, **When** cleanup resumes, **Then** it reuses the original private ownership/state/cap, deletes only owned resources and never resets lifetime or creates replacements.

### Edge Cases

- Registration accepts a provider operation but readback fails; one template is missing/replaced; or the deadline passes before the pair command.
- Reservation activation precedes long protection setup; remaining setup, preparation, stabilization, mode execution, final retrieval and cleanup no longer fit before the earlier stop trigger.
- Template or instance scheduling is overridden, has relative runtime, uses DELETE, or names another role/project/zone/image.
- A provider call times out after partial creation; outputs are malformed, duplicated or incomplete; zone, exact image or gVNIC support is unavailable.
- Independent concurrent P0/P1 attempts use the same fixed private CIDRs in different run-scoped VPCs, requiring distinct resources, state, protection and results.
- No exact physical zone/host location is exposed; no guest host keys exist yet; an instance name is reused with a different numeric instance ID.
- Provider stop begins late, or a VM is stopped before retrieval; no hard timing, billing, retrieval or automatic disk-recovery guarantee is inferred.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Accept only the existing V2 GCP deployment/identity/admission contracts and exactly A as client and B as receiver. Use one run-scoped namespace and private local state per attempt, isolated from concurrent P0/P1 runs. Reject unsupported or substituted settings before creation.
- **FR-002**: Use exactly two On-Demand Ubuntu Server 24.04 LTS amd64 `n2-standard-4` VMs, each 4 vCPU/16 GB, one 32 GiB `pd-balanced` boot disk, one gVNIC, no Tier_1, no Spot/reservation consumption, no data disks or affinity. Record the exact `ubuntu-os-cloud` image and actual hardware identity; do not invent an image revision or observed CPU platform.
- **FR-003**: Preserve `same_zone` S1 in `europe-west3-a`; `cross_zone` S2 in `europe-west3-a`/`europe-west3-b`; `placement_optimization` S3 with both in `europe-west3-a` under one COLLOCATED compact policy without `max_distance`; and `inter_region` S4 with A in `europe-west3-a` and B in `europe-west2-a`. S2/P2 and S4/P3 support is offline; any later execution requires supplied sourced admission/review evidence, including optional-scenario acceptance.
- **FR-004**: Use one custom-mode attempt VPC, Frankfurt `10.80.1.0/24`, and London `10.81.1.0/24` only for S4. Use private IPv4 for A-to-B measurements, MTU 1460, CUBIC, DSCP 0, ECN off and default offloads/socket buffers. Do not introduce peering, NAT, a bastion or load balancer.
- **FR-005**: Provide one public management IPv4 per VM, SSH allowed only from supplied controller IPv4 /32 with attempt-specific public authentication key, BatchMode and strict authenticated host keys. B's private UDP 11111/TCP 5201 listeners accept only A's private address; ICMP is pair-only. Package access is for bootstrap. Expose requested preparation settings separately from verified guest observations.
- **FR-006**: Reserve complete workflow cost and lifetime before any resource-creating side effect. Preserve reservation activation as original cap start; require `cap_started_at <= registered_at <= creation_start`, expiry no later than reserved end, dated identified cost inputs and no outstanding cleanup. Registration/setup time counts against the cap; neither retry nor creation restarts it. A supplied complete remaining workflow estimate must fit before the actual earlier provider trigger, checked at registration and immediately before creation.
- **FR-007**: Register an independent provider-side absolute compute-STOP action for both intended roles before either VM exists. Require observed registration IDs, timestamp, exact target/binding and policy readback. Creation must use that registered policy without weakening/overriding expiry. Failed/unknown/partial registration prevents both creations. The action must survive controller loss and must not require guest shutdown, queues, agents or a new orchestration platform.
- **FR-008**: Record registration, failure, trigger and cancellation evidence honestly. Account for provider initiation delay by selecting an earlier trigger within the original cap, with no invented hard completion/billing guarantee or cap increase. Keep protection effective while compute may exist. Cancellation can be reported only after verified compute absence and removal of pre-create protection; a policy deletion does not cancel a policy already inherited by a live VM.
- **FR-009**: Normalize both roles, observed resource IDs, exact images, shape, private/public IPv4, region/zone, placement and provider evidence through existing output contracts. Authenticate host identity against the specific project/zone/numeric VM identity before supplying strict known-hosts references. Missing metadata is explicitly unavailable; logical zones are not physical host IDs. Record documented N2 egress limit separately from observed performance and retain GiB billing units (2^30 bytes), distinct from decimal throughput Gbit/s.
- **FR-010**: After creation may have started, attempt normal deletion on every terminal path, preserving primary failure and partial output independently of cleanup failure. Verify removal of both VMs, boot disks, public addresses and all owned support resources by owned-ID and scoped paginated inventory, including both S4 regions. Successful destroy, stopped compute, absent state or an empty first page alone cannot prove cleanup.
- **FR-011**: Keep failed/unknown/residual cleanup open and block new launches using recoverable private attempt evidence. Repeated cleanup is safe, preserves ownership, and cannot delete foreign resources. Do not invent a failed-retrieval deletion timer, automatic retention/recovery service or periodic copying; later lifecycle integration owns the post-measurement copy/verify decision within the original cap.
- **FR-012**: Route all external operations through replaceable, deadline-bound, shell-free command boundaries. Preserve redacted commands/timestamps/operation evidence and private recovery state; exclude secrets, keys, raw state/plans and credentials from durable exports and Git. Do not contact GCP, use credentials, run SSH/benchmark traffic or create resources during offline checks.
- **FR-013**: Prove S1–S4 structure, reservation/registration-before-create, absolute expiry inheritance, partial creation, controller loss, identity mismatch, remaining-time exhaustion, pagination and residual cleanup with credential-free static/fake checks. Record exact commands, revisions and skips; only later authorized paid acceptance can establish real IAM, placement, expiry, networking, billing and deletion.

### Key Entities

- **Deployment attempt**: Existing campaign/run/deployment identity, two roles, scenario, preparation handoff, original reservation and independent private state.
- **Pre-create protection**: Both registered provider policy identities, absolute stop trigger, bound role settings, original cap/cost source and later observed scheduling evidence.
- **Observed pair**: Requested versus provider-observed VM/network/image/placement identity, authenticated management trust and explicit unavailable evidence.
- **Cleanup result**: Owned resource inventory, primary/cleanup failures, protection state, verified absence or an open launch blocker.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All four scenario cases describe exactly the selected two-role pair and intended locality/security; every unsupported substitution is rejected.
- **SC-002**: Every failed, uncertain or incomplete protection case creates zero VMs; every admitted creation trace shows reservation activation and both registrations first, with original-cap and complete-workflow-fit checks.
- **SC-003**: Every complete output identifies both actual VMs and authenticated host trust; missing or mismatched evidence cannot become a complete successful output.
- **SC-004**: Every simulated post-create terminal path attempts cleanup; every residual or unknown inventory blocks future launch, and verified success accounts for all owned resources.
- **SC-005**: The focused credential-free suite and static checks pass at a recorded revision with no cloud traffic or charges. Evidence clearly separates offline proof from unrun paid acceptance.

## Assumptions

- The canonical design and F07 prompt define fixed research choices; F07 resolves only engineering mechanisms. N, UTC study windows, prices/quotas and paid admissions remain evidence-dependent.
- The caller supplies admitted reservation/review evidence, exact available image identity, controller /32, public authentication key, private credential reference, preparation content and complete remaining workflow duration. F07 does not import unmerged F06/F11 or implement F09 composition.
- The merged GCP `instance_termination_action=DELETE` contract predates selecting the concrete native time-limit mechanism. A coordinator-owned narrow shared amendment to STOP is required before implementation consumes the native mechanism; F07 does not silently reinterpret DELETE or edit shared contracts/configs.

## Clarifications

### Session 2026-10-22

- No routine human questions were required. Scope, entities, flows, failure/recovery, security, external dependencies, constraints, terminology and completion signals are covered; engineering choices are recorded in research.md.
- Registration means both provider-created immutable instance templates have been read back with absolute `terminationTime` and STOP before VM creation; neither local files nor a scheduling field first supplied during VM creation qualify. Each standalone VM must inherit its registered template without scheduling overrides.
- Original cap starts at reservation activation. The selected stop trigger is the reserved end minus 30 seconds for documented initiation delay; the whole workflow must fit before that earlier trigger. This is a conservative request policy, not proof of stopping or billing completion at a hard deadline.
