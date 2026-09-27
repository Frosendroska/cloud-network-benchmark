# Feature Specification: F01 Core Contracts and Offline Harness

**Feature Branch**: `001-core-offline-contracts`

**Created**: 2026-09-27

**Status**: Draft

**Input**: User description: "F01 Core contracts and offline harness. Define the credential-free foundation and shared contracts used by every later feature. Turn experiment YAML into validated, resolved observation specifications and initialized run records; reject invalid combinations before side effects; assign run IDs; snapshot or hash config; define lifecycle and manifest state; allocate immutable result paths; and provide dry-run output. Define stable boundaries for provider deployment inputs/outputs, remote connection data, benchmark artifact names, diagnostic locations, validator inputs, and replaceable external-command execution with fakes so F02-F08 can work in parallel. Surface the single-provider template versus multi-cloud lifecycle wording as a clarification. Keep provisional parameters configurable and do not contact clouds."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Resolve an approved campaign before side effects (Priority: P1)

A thesis implementer selects an executable campaign configuration and receives either a complete, validated campaign specification with one observation specification per selected cloud provider or a clear rejection before any provider, remote host, benchmark tool, or cleanup action can be attempted.

**Why this priority**: Every later feature depends on a common understanding of experiment identity, provider, scenario, VM roles, benchmark parameters, and result locations. Invalid research combinations must be stopped before they can spend money or produce misleading evidence.

**Independent Test**: Can be fully tested by submitting valid and invalid campaign configurations and confirming that accepted configurations produce exactly one resolved observation specification for each explicitly selected provider while rejected configurations produce precise reasons and no initialized campaign or child-run side effects.

**Acceptance Scenarios**:

1. **Given** a valid campaign configuration selecting AWS, Azure, and GCP, **When** the user resolves it, **Then** the system produces one parent campaign specification and exactly three child observation specifications, one for each selected provider, sharing the campaign identity, scenario intent, benchmark settings, and scheduled start while retaining provider-specific placement and VM details.
2. **Given** a valid test campaign selecting only one provider, **When** the user resolves it, **Then** the system produces one parent campaign specification and exactly one child observation specification for that provider, using the explicitly configured reduced parameters and VM shape intent.
3. **Given** a configuration with an unsupported scenario name, an empty or unsupported provider selection, a missing experiment ID, missing provider-specific placement data, or contradictory region and zone settings, **When** the user resolves it, **Then** the system rejects the entire campaign with actionable validation messages and does not assign a campaign ID or child run ID or allocate result paths.
4. **Given** advisor-dependent parameters such as phase duration, aggregate stream count, the campaign's explicit scheduled start, or optional scenario scope, **When** the user resolves a configuration, **Then** those values remain explicit and configurable rather than frozen by the foundation.
5. **Given** an experiment or smoke-test campaign configured for provider-native spot capacity, **When** the user resolves it, **Then** the selected AWS market type, Azure priority/eviction policy/maximum price, or GCP provisioning model/termination action remains explicit and available to the provider deployment contract.

---

### User Story 2 - Initialize immutable campaign and run evidence (Priority: P1)

A thesis implementer starts a credential-free campaign attempt and receives a campaign ID, one child run ID per selected provider, config snapshot or hash, linked initial manifest state, and immutable local result paths that can be used consistently by deployment, benchmark, diagnostic, retrieval, validation, and cleanup features.

**Why this priority**: The constitution requires every accepted run attempt to be traceable through run ID, configuration, implementation provenance, raw result location, lifecycle state, failure details, and cleanup state. Later features cannot be developed safely without this evidence contract.

**Independent Test**: Can be fully tested by initializing campaigns from resolved campaign specifications and verifying unique parent and child identities, exact provider-to-child mapping, deterministic path shape, linked manifest creation, config provenance, lifecycle state, and refusal to overwrite any existing evidence.

**Acceptance Scenarios**:

1. **Given** a resolved campaign specification, **When** the user initializes it, **Then** the system assigns one campaign ID and one child run ID for each selected provider, records the experiment ID and configuration provenance, creates linked campaign and child manifests under `results/manifests/`, and reserves each child's raw-result storage under `results/raw/<run_id>/`.
2. **Given** an existing campaign ID, child run ID, manifest location, or occupied raw-result location, **When** the user initializes a campaign, **Then** the system refuses to overwrite existing evidence and explains the collision.
3. **Given** one child run that fails while other selected-provider runs proceed or complete, **When** manifests are updated, **Then** the child outcome remains provider-specific and the parent campaign records the aggregate outcome without hiding the failed child or its cleanup state.
4. **Given** a child run that fails, is interrupted, or has incomplete external evidence later in the lifecycle, **When** its manifest is updated, **Then** unavailable values remain explicit and the original failure is preserved even if cleanup later fails.
5. **Given** a child run has not yet produced deployment evidence, **When** its initial manifest is created, **Then** VM metadata, provider metadata, and tool versions are present as explicit unavailable evidence and may later be replaced with observed values.
6. **Given** initialization fails after one or more child IDs have been durably assigned, **When** recovery completes, **Then** every assigned run ID still has a child manifest recording the partial initialization failure and no created evidence is deleted.

---

### User Story 3 - Share stable contracts with parallel feature work (Priority: P2)

Developers implementing F02-F08 need stable, credential-free contracts for provider deployment inputs and outputs, remote connection data, benchmark artifact names, diagnostic locations, validator inputs, and replaceable external action execution so they can work in parallel without redefining shared vocabulary.

**Why this priority**: F01 blocks parallel work. If each feature invents its own boundary, F09 integration will be fragile and provider-specific semantics may be hidden or lost.

**Independent Test**: Can be fully tested by checking that each shared contract has required fields, explicit provider/scenario terminology, fakeable external action records, and sample fixtures for success and failure.

**Acceptance Scenarios**:

1. **Given** a provider feature needs to hand off VM details, **When** it uses the F01 deployment-output contract, **Then** it can express provider, scenario, VM A and VM B identities, private addresses, image and shape metadata, placement metadata, and connection readiness inputs without cloud credentials.
2. **Given** a benchmark or diagnostic feature needs file locations, **When** it uses the F01 artifact contract, **Then** it receives canonical names and locations for idle, single-flow, and multi-flow raw benchmark artifacts, per-VM TCP-phase diagnostics, static VM metadata, provider outputs, validation records, and human-readable dry-run output.
3. **Given** a feature needs to simulate Terraform, SSH, SCP, FLENT, or similar external actions, **When** it uses the F01 execution boundary, **Then** success, failure, timeout, interruption, stdout, stderr, exit status, timing, and command provenance can be represented without invoking the real external action.

---

### User Story 4 - Preview a campaign without credentials (Priority: P3)

A thesis implementer can perform a dry run that shows the resolved parent campaign, all selected-provider child observations, lifecycle plan, result paths, expected artifacts, and external actions that would be needed, without contacting clouds, remote hosts, or benchmark tools.

**Why this priority**: Dry-run output makes the implementation reviewable before paid smoke tests and helps verify that a configuration maps to the intended thesis scenario.

**Independent Test**: Can be fully tested by requesting dry-run output for representative valid configurations and confirming that it includes the campaign and child-run plans, no secret values, no real external side effects, and the same identity and path scheme later campaign initialization would use.

**Acceptance Scenarios**:

1. **Given** a valid configuration, **When** the user requests a dry run, **Then** the system displays the campaign identity, common scheduled start, selected providers, one child run plan per provider, scenario, VM roles, provider-specific placement summary, benchmark phase plan, provisional parameters, raw result paths, manifest paths, expected artifact names, validation inputs, and cleanup expectation.
2. **Given** a dry run is requested, **When** the output is produced, **Then** no cloud resources, remote connections, benchmark executions, or paid operations are attempted.

### Edge Cases

- Missing, duplicate, or malformed `experiment_id` values must be rejected because they break thesis traceability.
- An empty provider list, duplicate provider entry, or provider outside AWS, Azure, and GCP must be rejected; an omitted supported provider must not be added implicitly.
- A campaign that omits its one explicit scheduled start must be rejected. Recurring scheduling, campaign sharding, and automatic triggering are outside F01 and remain the responsibility of an external reconciler, `tmux` process, or human invocation.
- Scenario terminology other than `same_zone`, `cross_zone`, `placement_optimization`, or `inter_region` must be rejected unless explicitly added by a later approved design update.
- Provider-specific placement requirements must be explicit; a provider may not silently reinterpret another provider's region, zone, or placement fields.
- Optional `inter_region` and placement-optimization scope must remain configurable because advisor feedback and budget checks are still pending.
- Phase durations, aggregate stream count, the single campaign scheduled start, and optional scenario scope must remain accepted as configuration values and must not be frozen by F01.
- A campaign ID, child run ID, manifest path, or result path collision must fail before evidence can be overwritten.
- The implementation Git commit must be recorded for every initialized experiment or test run through an injectable provenance provider; an explicit unavailable reason is permitted only when the repository commit genuinely cannot be read. Thesis-design provenance must likewise never be fabricated.
- Failed, interrupted, or cleanup-failed runs must still have manifest states that preserve the available evidence and original failure.
- Dry-run output must not expose secrets, credentials, private keys, or values that cannot be known before deployment.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST accept an executable campaign configuration as the input source for a parent campaign specification and one child observation specification per explicitly selected provider, and MUST preserve the stable `experiment_id` used to connect implementation evidence to thesis design.
- **FR-002**: System MUST validate required campaign and provider-specific configuration fields before campaign initialization, including experiment identity, a non-empty provider selection, common scheduled start, scenario, provider-specific region or zone placement, VM shape intent, benchmark phase settings, and run-level options.
- **FR-003**: System MUST preserve the scenario terms `same_zone`, `cross_zone`, `placement_optimization`, and `inter_region` exactly in accepted specifications and MUST reject unknown scenario terms.
- **FR-004**: System MUST keep AWS, Azure, and GCP provider semantics explicit in resolved specifications, including provider-specific region, zone, placement, VM-shape, image, and documented network-limit metadata when present.
- **FR-005**: System MUST reject the entire campaign when any selected provider has an invalid provider and scenario combination, before assigning a campaign ID or child run ID, writing any manifest, reserving any raw-result path, or invoking any external action.
- **FR-006**: System MUST keep advisor-dependent parameters configurable, including phase duration, aggregate stream count, the campaign's single explicit scheduled start, optional scenario inclusion, and any calibrated benchmark settings; recurrence and campaign sharding are outside F01.
- **FR-007**: System MUST produce a resolved observation specification that identifies VM A as client and traffic generator, VM B as server and receiver, and the private A-to-B measurement direction.
- **FR-008**: System MUST model every accepted configuration as one parent campaign with an explicit provider selection and MUST derive exactly one child run per selected provider; AWS, Azure, and GCP MUST all be supported selections, individually or in combination, and unlisted providers MUST NOT run.
- **FR-009**: System MUST assign a campaign ID and all child run IDs only after whole-campaign validation succeeds; each campaign ID MUST uniquely identify one initialized campaign attempt, and each child run ID MUST uniquely identify one provider execution within that campaign. A run ID becomes assigned only when its child manifest is exclusively and durably reserved.
- **FR-010**: System MUST record configuration provenance for every initialized campaign and child run through a configuration path plus either an immutable snapshot, a hash, or both.
- **FR-011**: System MUST allocate immutable local result locations for every initialized campaign and child run, including linked parent and child manifests under `results/manifests/` and child raw evidence under `results/raw/<run_id>/`.
- **FR-012**: System MUST refuse to reuse or overwrite an existing campaign ID, child run ID, manifest, or raw-result location for a new campaign attempt.
- **FR-013**: System MUST use one canonical serialized lifecycle vocabulary everywhere: child execution states `initialized`, `provision_ready`, `running`, `collecting`, `validating`, `succeeded`, `failed`, and `interrupted`; child cleanup states `not_started`, `not_required`, `attempted`, `succeeded`, and `failed`; and parent campaign states `initialized`, `active`, `partially_complete`, `succeeded`, `failed`, and `interrupted`.
- **FR-014**: System MUST allow parent and child manifest updates to record partial evidence, missing evidence, failure reason, failure timing, and cleanup state without hiding original child failures; a parent campaign MUST NOT report success unless every selected-provider child succeeds and satisfies its cleanup requirements.
- **FR-015**: System MUST define shared provider deployment input fields needed by later provider features, including provider, scenario, region and zone selection, placement settings, VM role intent, VM shape intent, image intent, bootstrap inputs, and result provenance inputs.
- **FR-016**: System MUST define shared provider deployment output fields needed by later lifecycle features, including VM A and VM B identifiers, private addresses, connection endpoint data, provider metadata, image identity, placement metadata, and provider output evidence.
- **FR-017**: System MUST define shared remote connection data without requiring real remote connectivity, including role, address, user identity reference, authentication material reference, readiness expectations, and connection failure evidence.
- **FR-018**: System MUST define canonical benchmark artifact names for the three sequential phases: idle latency, single-flow TCP, and multi-flow aggregate TCP.
- **FR-019**: System MUST define canonical locations for TCP-phase diagnostics for VM A and VM B and static metadata for VM A and VM B.
- **FR-020**: System MUST define validator input expectations for manifests, configuration provenance, provider outputs, benchmark artifacts, diagnostics, static metadata, lifecycle state, and cleanup state.
- **FR-021**: System MUST define a replaceable external action boundary that can represent intended command, arguments, environment classification, working location, timeout, start and end timestamps, exit status, standard output, standard error, success, failure, timeout, and interruption.
- **FR-022**: System MUST provide fake external action outcomes so later features can test Terraform, SSH, SCP, FLENT, and related external behavior without cloud credentials or installed benchmark tools.
- **FR-023**: System MUST provide dry-run output for valid configurations that summarizes the parent campaign, common scheduled start, selected providers, child run identity plans, provider-specific resolved observations, lifecycle plan, result paths, artifact names, validation inputs, and external actions without performing those actions.
- **FR-024**: System MUST guarantee that dry-run and validation paths do not contact cloud providers, remote VMs, or paid resources.
- **FR-025**: System MUST provide credential-free examples or fixtures for valid configurations, invalid configurations, initialized runs, manifest transitions, dry-run output, and fake external action outcomes.
- **FR-026**: System MUST treat configurations under `configs/experiments/` as primary experiment campaigns intended to run across their explicitly selected providers and scenarios, without requiring all supported providers to be selected, and MUST document their experiment IDs' Thesis-side mappings.
- **FR-027**: System MUST treat executable configurations under `configs/tests/` as smaller, cheaper smoke-test campaigns that use the same parent-child contracts and may select a single provider, reduced benchmark parameters, and smaller provider-specific VM shape intent as permitted by the design document; their experiment IDs and Thesis-side verification mappings MUST also be documented.
- **FR-028**: System MUST preserve one common scheduled start in the parent campaign and propagate it to every child observation so selected providers are coordinated against the same intended campaign time while recording each child's actual lifecycle timestamps separately.
- **FR-029**: System MUST initialize every assigned child run ID with a corresponding manifest using an atomic exclusive reservation boundary and MUST preserve or recover that manifest with failure evidence when later initialization steps fail.
- **FR-030**: System MUST include VM metadata, cloud/provider metadata, and tool versions in every child manifest, representing each category as observed structured evidence or an explicit unavailable/null value with a reason.
- **FR-031**: System MUST obtain the implementation Git commit through an injectable provenance provider and record it for primary experiments and smoke-test campaigns; offline tests MUST be able to supply deterministic fake commit data without invoking Git.
- **FR-032**: System MUST process one explicitly started campaign per command invocation and MUST NOT implement recurring scheduling, campaign sharding, or an internal reconciler in F01.
- **FR-033**: System MUST validate and preserve provider-native capacity purchase options: AWS `instance_market_type`, Azure `priority`, `eviction_policy`, and `max_price`, and GCP `provisioning_model` and `instance_termination_action`; both regular/on-demand and spot selections MUST remain configurable without flattening their provider-specific semantics.

### Key Entities *(include if feature involves data)*

- **Campaign Configuration**: User-authored executable description of what should be measured and where. Key attributes include experiment ID, common scheduled start, explicit selected-provider set, scenario intent, provider-specific region or zones, placement settings, VM shape and capacity-purchase intent, benchmark phases, provisional parameters, and campaign options. Primary configurations live under `configs/experiments/`; smaller verification configurations live under `configs/tests/`.
- **Resolved Campaign Specification**: Validated, side-effect-free parent description of one campaign. It fixes campaign identity, common scheduled start, selected providers, shared benchmark intent, provenance inputs, and the exact child observation set.
- **Resolved Observation Specification**: Validated, side-effect-free description of one observation to execute. It fixes provider-specific interpretation, VM roles, measurement direction, benchmark settings, placement meaning, and provenance inputs for later features.
- **Campaign Record**: Parent evidence container for one accepted campaign attempt. It includes campaign ID, experiment ID, selected providers, common scheduled start, configuration provenance, aggregate lifecycle state, parent manifest path, and references to every child run.
- **Child Run Record**: Provider-specific evidence container for one execution within a campaign. It includes run ID, parent campaign ID, provider, experiment ID, configuration provenance, lifecycle state, manifest path, raw-result path, actual timestamps, failure state, cleanup state, and available provenance.
- **Manifest**: Durable parent or child evidence file. A parent manifest records campaign identity, configuration, selected providers, the single scheduled start, child references, aggregate state, and tool-version evidence. A child manifest records provider/scenario identity, VM metadata, cloud/provider metadata, tool versions, lifecycle state, Git provenance, artifact locations, failure details, and cleanup outcome. Evidence not yet available is represented explicitly as null with a reason.
- **Result Bundle**: Immutable local directory under a child run ID containing that provider execution's raw benchmark artifacts, diagnostics, static metadata, provider outputs, validation outputs, and other preserved evidence.
- **Provider Deployment Input**: Shared contract passed to provider-specific deployment work. It carries resolved provider/scenario/placement/VM/bootstrap values without hiding provider differences.
- **Provider Deployment Output**: Shared contract returned by provider-specific deployment work. It carries VM identities, private addresses, connection data, provider metadata, placement metadata, and deployment evidence.
- **Remote Connection Data**: Role-specific information later features need to reach VM A or VM B, expressed as a contract that can also be faked offline.
- **Benchmark Artifact Set**: Canonical names and locations for idle latency, single-flow TCP, and multi-flow aggregate TCP raw outputs.
- **Diagnostic Artifact Set**: Canonical names and locations for TCP-phase per-VM diagnostics and static per-VM metadata.
- **Validator Input Set**: The manifest, configuration provenance, provider outputs, raw benchmark artifacts, diagnostics, static metadata, lifecycle data, and cleanup data required for result-bundle validation.
- **External Action Record**: Replaceable representation of any external process or remote operation, including intended action, observed outcome, timing, and failure evidence.
- **Dry-Run Report**: Credential-free human-readable preview of the parent campaign, selected-provider child observations, lifecycle, artifacts, paths, and external actions.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of invalid sample configurations are rejected before campaign ID or child run ID assignment, manifest creation, raw-result path reservation, or external action attempt.
- **SC-002**: 100% of valid sample configurations resolve into exactly one child observation per selected provider, and every observation includes experiment ID, campaign identity, provider, scenario, VM A and VM B roles, private A-to-B direction, benchmark phase settings, placement interpretation, common scheduled start, and provenance inputs.
- **SC-003**: 100% of initialized sample campaigns produce a unique campaign ID, exactly one unique child run ID per selected provider, linked parent and child manifest locations, child raw-result locations, and configuration provenance without overwriting existing evidence.
- **SC-004**: 100% of manifest lifecycle fixture cases preserve terminal success, failure, interruption, cleanup success, and cleanup failure states with the original failure still visible when cleanup also fails.
- **SC-005**: 100% of dry-run executions for valid fixtures complete without cloud credentials, remote connectivity, benchmark execution, or paid resource creation.
- **SC-006**: Later F02-F08 feature specifications can reference F01 contracts for provider inputs/outputs, remote connection data, artifact names, diagnostic locations, validator inputs, and fake external actions without redefining those boundaries.
- **SC-007**: A reviewer can inspect dry-run output for each supported provider and scenario fixture and confirm the intended lifecycle, result paths, artifact names, and cleanup expectation in under 5 minutes per fixture.
- **SC-008**: All provisional methodology values present in sample configurations remain visible in resolved specifications and dry-run output, with no advisor-dependent value silently replaced by a fixed default.
- **SC-009**: A three-provider fixture produces AWS, Azure, and GCP child runs, while each single-provider fixture produces exactly one child run and no records or actions for unselected providers.
- **SC-010**: Representative configurations from both `configs/experiments/` and `configs/tests/` resolve through the same campaign contract, with test fixtures retaining their explicitly reduced provider, VM, and benchmark scope.
- **SC-011**: Every run ID durably assigned during injected success or failure cases has exactly one preserved child manifest, including failures at each initialization persistence boundary.
- **SC-012**: Manifest contract tests verify observed and unavailable/null forms for VM metadata, cloud/provider metadata, and tool versions.
- **SC-013**: Every initialized primary experiment and smoke-test fixture records the deterministic implementation commit supplied by the provenance provider.
- **SC-014**: The committed executable matrix contains one three-provider primary campaign for each canonical scenario, one reduced single-provider smoke campaign for each supported cloud, and one reduced three-provider smoke campaign; all resolve offline and preserve valid regular or spot purchase options.

## Assumptions

- F01 defines shared contracts and credential-free behavior only; provider-specific deployment resources, VM access implementation, FLENT execution, diagnostics collection, result validation, lifecycle integration, and final offline system gating are owned by later features.
- The foundation may use sample configurations and fixtures that represent AWS, Azure, and GCP behavior, but it must not contact those providers.
- A campaign record and each of its child run records represent initialized attempts that deserve linked manifests even if later lifecycle steps fail or are interrupted.
- The common scheduled start expresses coordinated campaign intent; provider startup and benchmark execution may have distinct actual timestamps that must be retained for later validation and interpretation.
- Raw benchmark results may become large and are normally excluded from version control; manifests remain lightweight enough to version-control when practical.
- Thesis interpretation, canonical figures, and research-facing run tracking remain in the sibling Thesis repository and are linked through experiment IDs, run IDs, configuration provenance, commits, manifests, and raw-result references.
- The current advisor-review design is provisional; F01 must support revision of stream count, duration, the explicitly supplied campaign start, optional placement/inter-region scope, and other methodology-dependent settings. Recurring triggering remains external to this framework.
- The executable configuration matrix represents the approved S1-S4 design: primary campaigns use the documented experiment shapes and placements, while smoke campaigns use reduced shapes and durations. Committed defaults use regular/on-demand capacity; provider-native spot fields remain configurable for later controlled runs.
