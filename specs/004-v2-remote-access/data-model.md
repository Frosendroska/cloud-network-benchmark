# F02R local data model

All new types are local to the existing F02 modules. Reuse Pydantic `StrictModel` and frozen F01R evidence wrappers; do not introduce another public schema registry. Shared model names below refer to existing imports, not replacements.

## ConnectionOutput and RemoteDeadline — access.py

Extend existing `ConnectionOutput` with required `identity: IdentityContext` and `deployment_id`; retain run/provider/scenario, two `RemoteConnectionData`, two private IPv4 addresses and `TimeoutPolicy`. `from_deployment` copies rather than reconstructs identities. Roles must be VM A/VM B; run/deployment/provider must equal `identity`; addresses must be distinct usable private IPv4; complete provider output and connection references are required. Authentication/known-host references stay private runtime paths and must be nonblank existing readable files at execution. Do not serialize their contents or paths into exported evidence.

`RemoteDeadline` is a small immutable local value containing `readiness_deadline_monotonic`, `overall_deadline_monotonic`, `cleanup_reserve_seconds` and source `cap_started_at`/`cap_expires_at` UTC evidence. Monotonic fields must be finite; `cleanup_reserve_seconds` must be finite and positive; readiness deadline must not exceed overall deadline; cap expiry must follow cap start. These are absolute instants supplied once by the caller, never per-action durations. The caller maps UTC to monotonic once; a clock object/callable is injectable. No automatic renewal or current-time default may mint a fresh lifetime.

`TimeoutPolicy` retains positive finite per-command ceilings. These ceilings never override the applicable absolute deadline. An exhausted deadline produces a result with no new command rather than constructing an invalid zero-timeout `CommandRequest`.

## RemoteActionEvidence / AccessFailureEvidence — access.py

Retain action identity, VM/role, result, stage/category and timestamps. Store `CommandRequest.durable_view()` and `CommandResult.durable_view()` or equivalently scrubbed durable payloads with explicit credential/key/host-reference redactions applied on all success/failure paths. Raw runtime request data must not leak through nested model serialization. Preserve command outcome/exit/elapsed and the primary/secondary distinction. Missing observations remain explained; never fill in requested versions as observed versions.

## SourceBuild and BootstrapConfig/Result — bootstrap.py

`SourceBuild` fields: `tool` is `sockperf` or `iperf3`; `source_url` is an explicit HTTPS source URL without embedded credentials; `source_reference` and `archive_name` are nonblank; `source_sha256` is exactly 64 lowercase hex characters. Archive names and generated identifiers must be safe single basenames, not paths/options/shell fragments. Build a fixed tool-specific command recipe, not arbitrary user shell input.

`BootstrapConfig` requires one source entry for each tool, deployment-scoped remote workspace/install prefix and allowed bootstrap packages. Prefix/workspace are normalized absolute paths under the experiment's remote root; legacy `flent_version`, `netperf_version` and arbitrary setup shell fragments are rejected. Tool commands use absolute installed paths. Server names derive only from validated deployment ID and tool.

`BootstrapResult` retains `succeeded`, `actions`, `failure` and observed versions, with required matching `identity`/`deployment_id`. Per VM/tool, keep source hash verification, build actions, compiler version, executable path/version/hash, capability observations and installed-source identity. Success requires all observations for both tools on both VMs and matching B listener evidence. Missing evidence stays unavailable on failed results and cannot qualify for readiness.

Listener evidence identifies B private IPv4, protocol/port, service/process/binary identity, log destinations and allowed A private IPv4. Expected pairs are exactly UDP 11111 and TCP 5201. States are prepared/active/mismatch/failed; repeated matching active preparation reuses state, mismatch never silently restarts it.

## ReadinessConfig/Result — readiness.py

Readiness consumes required successful `BootstrapResult` for this exact deployment and the original `RemoteDeadline`. Remove implicit `bootstrap_succeeded=True`. `ReadinessConfig` contains a positive finite poll interval and positive attempt ceiling; it cannot replace required predicates with arbitrary success-output text. Fixed predicates are SSH A/B, cloud-init done A/B, private ICMP A-to-B, TCP connect A-to-B:5201, B unit/process identity, exact private socket bindings and scoped ingress rules. Each result records ordered action/observation IDs, total attempts, final ready flag, original deadline, elapsed/remaining time and failure. Success requires every predicate inside the same budget. Poll exhaustion and deadline exhaustion are distinguishable failures.

## RetrievalItem/Manifest — retrieval.py

Retain existing names while migrating fields to `ArtifactInput` identity and the frozen layout. Each concrete item has unique `artifact_id`, `deployment_id`, `source_role` (VM A or VM B), matching `source_vm`, normalized absolute `source_path` under the deployment workspace, normalized relative `destination_path`, native `kind`, optional `unit_id`, `expectation`, `closed=true`, observed `expected_size_bytes` and `expected_sha256`, and positive finite command timeout. `expected_size_bytes` is a nonnegative integer; `expected_sha256` is exactly 64 lowercase hex characters. Do not treat optional expected hash as verification. Dynamic chunk templates must resolve to unique concrete safe paths before transfer; controller-produced artifacts are not SCP sources.

`RetrievalManifest` requires nonempty `declarations: list[ArtifactInput]` for the supplied mode/layout, concrete `items` (possibly empty on total export failure), `unavailable_items` keyed by declaration ID with role/path/reason, matching `identity`/`deployment_id`, UTC `measurement_ended_at`, `producers_closed_at >= measurement_ended_at`, explicit closure evidence, and concrete items. Open or active inventories fail before transfer. Every remote declaration must resolve to either one concrete item or an unavailable entry; duplicate/unknown entries fail validation. Controller declarations are preserved locally and excluded from SCP. Required remote absence becomes a failed item with unavailable size/hash, not a fake concrete closed file; represent that in result evidence. Optional remote absence remains explicit unavailable evidence. A failure may legitimately omit one VM's files; successful export must cover required artifacts from both roles for the supplied mode/layout. Existing local controller artifacts remain untouched.

## RetrievalResult and attempt evidence — retrieval.py

Retain `complete`, `actions`, `retrieved`, `failure`, `integrity`; add verified/failed F01R `ArtifactRecord` objects, all failures/unavailable items, per-attempt local paths and `cleanup_required=true`. `failure` can remain the first retrieval failure for facade compatibility; the full list preserves later failures. Primary measurement failure remains caller-owned and is never overwritten.

Attempt number is 1 or 2 only. Every attempt has a unique contained staging path; preserve unsuccessful bytes and their observed size/hash when available. No path may overwrite a prior file, be a symlink or escape the child root. A successful stage is published without overwrite at the declared destination only after exact remote/local size/hash equality. Hash in bounded chunks. Verified records must pass existing `ArtifactRecord.verify_materialized_file(child_root)`. A later successful retry may yield a verified final record, but earlier failed attempt evidence remains. `complete` means every required inventory item verified; it says nothing about data validity/comparison eligibility. Cleanup-required remains true on success, failure, interruption and cap exhaustion.

## State transitions and invariants

`validated connection -> preparing -> prepared | preparation_failed`; only matching `prepared` may enter `checking -> ready | not_ready`. The original deadline passes through preparation/checking unchanged. No benchmark action is performed.

Caller-owned `measuring -> ended -> producers_closed` permits `copying -> verified | incomplete`. Every copy path returns cleanup-required; normal cleanup/expiry/resource state is outside F02R. Retry does not reenter measuring or extend any cap. Native data and diagnostic counts remain unchanged; F08 owns semantic validation and F09 owns cleanup/primary-failure aggregation.
