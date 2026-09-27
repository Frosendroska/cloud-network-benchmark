# Phase 0 Research: F01 Core Contracts and Offline Harness

## Python Foundation

**Decision**: Use a Python 3.9+ `src`-layout package with Pydantic 2.x, PyYAML 6.x, standard-library `argparse`, and pytest 8.x.

**Rationale**: The approved architecture already calls for a local Python controller, and the repository host provides Python 3.9.2. Pydantic supplies typed nested models, precise validation errors, immutable resolved models, and schema-compatible serialization. PyYAML safely parses the required YAML input. `argparse` avoids adding a CLI framework before the command surface warrants one.

**Alternatives considered**: Hand-written dictionaries and validators were rejected because provider/scenario cross-field rules and stable serialization would be brittle. JSON-only configuration was rejected because YAML is part of the approved design. A larger web or workflow framework was rejected as outside the deliberately small architecture.

## Campaign and Child Granularity

**Decision**: Treat one accepted YAML file as one explicitly started campaign for one scenario. It resolves into one parent campaign plus exactly one child observation for each explicitly selected provider. Recurrence, sharding, and automatic triggering remain outside F01 and may be supplied later by an external reconciler, `tmux` process, or human invocation.

**Rationale**: This implements the clarified requirement directly: a main campaign may select AWS, Azure, and GCP, while a cheap test campaign may select only one. Keeping one scenario per campaign prevents ambiguous provider-by-scenario fan-out and gives every child one VM pair and one immutable result bundle.

**Alternatives considered**: One independent top-level run per provider was rejected because it cannot represent coordinated sampling. Provider-by-scenario matrix expansion inside one YAML was deferred because the specification requires one child per provider and scenario scope remains configurable through separate campaign files.

## Configuration Discovery

**Decision**: Map primary and smoke-test roles to the constitution's canonical `configs/experiments/` and `configs/tests/` paths. Require `selected_providers` to be non-empty and unique, require its set to equal the keys in `provider_configs`, and document each executable configuration's Thesis-side experiment or verification mapping.

**Rationale**: Exact key equality prevents an unselected provider block from being executed accidentally and catches a selected provider with no deployable settings. Both directories use the same schema; their difference is intent and configured cost/scale, not a separate code path.

**Alternatives considered**: Automatically adding all supported providers was rejected because provider choice must be explicit. Silently ignoring extra provider blocks was rejected because stale configuration can cause costly surprises. Separate experiment and test schemas were rejected as unnecessary divergence.

## Executable Matrix and Capacity Purchase

**Decision**: Commit one three-provider primary campaign for each approved S1-S4 scenario, three reduced single-provider smoke campaigns, and one reduced three-provider smoke campaign. Preserve provider-native capacity purchase controls: AWS `instance_market_type`, Azure `priority`/`eviction_policy`/`max_price`, and GCP `provisioning_model`/`instance_termination_action`. Defaults use regular/on-demand capacity, while valid spot selections remain configurable.

**Rationale**: The validation plan requires each cloud to be provisioned separately on a cheap shape before a short coordinated three-cloud run. The primary matrix makes every approved scenario executable without embedding multiple scenarios into one campaign. Provider-native purchase fields let later Terraform features evaluate spot instances for short runs without pretending that the three clouds expose identical semantics.

**Alternatives considered**: A single generic `spot: true` flag was rejected because eviction, pricing, and termination behavior differ by provider. Committing spot as the default was rejected because capacity interruptions would make the first smoke-validation path less repeatable. Generating the scenario matrix dynamically was rejected because explicit reviewed YAML is easier to trace to the Thesis design.

## Validation Boundary

**Decision**: Parse, structurally validate, semantically validate, and fully resolve every selected provider before generating IDs, creating directories, writing manifests, or calling an external action.

**Rationale**: Whole-campaign validation provides the strongest no-side-effects guarantee and prevents a three-provider campaign from being partially initialized because the final provider is invalid.

**Alternatives considered**: Validate and initialize children incrementally was rejected because it creates partial records for invalid input. Provider-specific late validation was rejected because F01 must reject invalid combinations before later features can spend money.

## Provider and Scenario Semantics

**Decision**: Use an explicit discriminated provider configuration for AWS, Azure, and GCP. Apply common scenario invariants plus provider-specific placement kinds: AWS `cluster_placement_group`, Azure `proximity_placement_group`, and GCP `compact_placement_policy`.

**Rationale**: Shared region/zone checks remove genuine duplication, while explicit provider models preserve naming, placement, image, VM shape, and documented network-limit metadata. `same_zone`, `cross_zone`, `placement_optimization`, and `inter_region` remain exact serialized values.

**Alternatives considered**: A free-form provider map was rejected because it hides required fields and delays errors. A single normalized placement abstraction was rejected because the three provider controls are not semantically equivalent.

## Identity and Determinism

**Decision**: Generate a unique campaign ID after validation from UTC time, normalized experiment ID, and an injected random token. Derive child run IDs by appending the provider. Inject the clock and token source in tests.

**Rationale**: Human-readable, sortable IDs aid operations; a random suffix prevents collisions; provider-derived child IDs make parent-child relationships visible. Injection makes IDs and all paths deterministic in tests without weakening production uniqueness.

**Alternatives considered**: Content hashes alone were rejected because repeated observations of the same configuration need distinct attempts. Database sequences were rejected because no database is permitted. Provider-generated IDs were rejected because identity must exist before provider work.

## Provenance and Snapshots

**Decision**: Hash the exact source YAML bytes with SHA-256 and preserve those bytes as `config.yaml` in every initialized child bundle. Record source path, hash, implementation Git commit, and thesis/design Git commit. Commit acquisition uses an injectable provenance provider so offline tests supply deterministic fake commits without a Git subprocess; genuinely unavailable values use explicit null-plus-reason fields.

**Rationale**: Byte-level hashing and snapshots reconstruct exactly what was submitted, while child-local copies keep each result bundle self-contained. Honest unavailable markers satisfy provenance requirements without inventing data.

**Alternatives considered**: Hashing only normalized data was rejected because comments and source formatting would not be preserved. A single mutable central snapshot was rejected because child bundles must remain independently interpretable.

## Manifest and Lifecycle Model

**Decision**: Keep execution state, cleanup state, and append-only lifecycle events as separate fields. Use only the canonical underscore-separated state values defined in the data model. Derive the parent aggregate state from all child states and never allow parent success unless every child succeeds and cleanup obligations are satisfied. Child manifests always include VM metadata, cloud/provider metadata, and tool-version structured evidence; unavailable observations remain null with a reason.

**Rationale**: A single status field cannot preserve both the original benchmark failure and a later cleanup failure. Separate dimensions retain causality while the event history records transitions and timestamps.

**Alternatives considered**: One combined enum was rejected because cleanup would overwrite the execution outcome. Parent state updated independently of children was rejected because it can drift from evidence.

## Filesystem Immutability

**Decision**: Preflight every parent and child destination, create paths exclusively, write JSON through same-directory temporary files plus atomic replacement, and refuse all collisions. A candidate child run ID becomes assigned only when its initial child manifest is exclusively created. If any later persistence step fails, preserve or recover every assigned manifest with partial-initialization failure evidence; never delete assigned evidence. Once a child artifact exists, F01 may add new expected files through exclusive creation but never edit raw benchmark artifacts or config snapshots.

**Rationale**: Local files match the approved architecture. Defining assignment at exclusive manifest creation gives every assigned run ID durable evidence. Atomic manifest replacement prevents torn JSON while still allowing lifecycle and recovery updates.

**Alternatives considered**: Database transactions and object storage were rejected by scope. Deleting partial initialization evidence was rejected because assigned attempts and failures should remain traceable.

## External Action Boundary

**Decision**: Define `CommandRequest`, `CommandResult`, and a `CommandRunner` protocol. The real implementation uses argument arrays without a shell; scripted fakes return queued success, failure, timeout, or interruption outcomes and record requests.

**Rationale**: One small boundary supports Terraform, SSH, SCP, FLENT, and local tools while keeping later features testable. Argument arrays reduce quoting risk. Recorded requests let tests assert order and exact intent.

**Alternatives considered**: Mocking `subprocess` directly in every feature was rejected because it couples tests to implementation. Shell command strings were rejected because quoting and secret leakage are harder to control.

## Artifact Vocabulary

**Decision**: Publish canonical child-relative paths: `config.yaml`, `terraform-outputs.json`, `flent/idle.flent.gz`, `flent/single_flow.flent.gz`, `flent/multi_flow.flent.gz`, `diagnostics/{single_flow,multi_flow}/{vm_a,vm_b}.json`, `metadata/{vm_a,vm_b}.json`, `validation/result.json`, and `validation/summary.txt`.

**Rationale**: These names match the approved result-bundle shape and give F02-F08 stable ownership boundaries. Diagnostics are limited to TCP phases, and raw FLENT files remain untouched.

**Alternatives considered**: Feature-specific naming was rejected because F09 would need adapters. Provider-specific artifact layouts were rejected because the evidence categories are genuinely shared even though their contents remain provider-explicit.

## Dry-Run Semantics

**Decision**: Dry-run performs full read/validation/resolution and renders candidate identity/path patterns, selected providers, scheduled start, per-child placement, artifacts, lifecycle outline, and action classes. It does not reserve durable IDs, create result paths, invoke commands, or print secret values.

**Rationale**: This is reviewable and side-effect free. Showing patterns rather than reserved IDs avoids implying that a dry-run created durable evidence.

**Alternatives considered**: Initializing manifests during dry-run was rejected as a side effect. Omitting paths and actions was rejected because those are the primary review value.
