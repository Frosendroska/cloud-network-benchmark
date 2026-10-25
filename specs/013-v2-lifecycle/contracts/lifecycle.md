# F09 lifecycle interfaces

Proposed F09 interfaces; existing imported component APIs are the executable authority. These local dataclasses/functions add no shared DTO fields or schemas.

## Controller functions

`preview_lifecycle(request, *, ledger, clock) -> preview` resolves the selected config/row/cost source and returns eligibility/reasons/IDs without registering expiry, claiming creation or invoking external commands. It does not manufacture missing paid prerequisites.

`run_lifecycle(request, *, runner, clock, measurement_clock, ledger, provider_settings) -> outcome` initializes immutable snapshots/IDs, reserves through F11, composes all selected providers and returns per-provider evidence plus overall status. The replaceable runner is the existing CommandRunner; offline fixtures inject fake guest/process boundaries. Provider settings are explicit AwsRuntimeSettings/GcpRuntimeSettings/AzureRuntimeOptions with private references. Caller-supplied sourced allowances must include actual remaining runtime staging/registration/readiness/stabilization/measurement/close/copy/cleanup; no arbitrary new default timer.

`cleanup_lifecycle(saved_recovery, *, runner, clock, ledger, provider_settings) -> outcome` only deletes/verifies/reconciles an identified prior attempt. It never reruns provisioning or measurement and never claims compute expiry is full cleanup.

## Required orchestration sequence

1. Resolve/strictly validate config/row; snapshot IDs and Git/config/schedule/cost provenance; detect collisions. Pass explicit current session shared by healthy concurrent independent invocations.
2. Build exact F11 AdmissionPlan/ReservationRequest; reserve full lifetime/cost immediately before registration. If rejected/pending/missed, persist reason and execute no VM work.
3. Register and read back AWS, Azure and GCP expiry as selected. Preserve partial safety journals; any failure blocks all selected creates and enters cleanup of registration liabilities.
4. Submit provision functions concurrently only after the complete verified barrier and individual F11 one-shot claims. Normalize outcomes without losing original results/recovery references. Prepare, bootstrap, stage guest runtime, verify readiness and stabilize each healthy pair for 60 seconds. All stage transitions check complete remaining work against original cap/earliest trigger.
5. Coordinate UTC start; run existing standalone diagnostics locally on A/B and F03 via A-local guest entry with persistent B listeners. Retain CAL/DAY pairs through all units. Capture actual timing/clock uncertainty, misses/skew and unexecuted units.
6. On completion/interruption stop owned clients/diagnostics and exact B listener services, wait for actual exit/flush and record closure. Where closure/inventory fails, record unavailable/failed evidence rather than a closed boolean assertion. Do not invent files for a failure with no output.
7. Build full closed inventory from exact declarations/actual filesystem hashes; final retrieve/verify A and B via access.retrieve/run_retrieval. Use bounded_copy_handoff only for failed concrete artifacts not already retried and only while cleanup allowance remains. Successful destinations are never recopied/overwritten. Preserve every attempt and failed/missing record.
8. Attempt full normal provider cleanup/absence even on closure/retrieval failure; retain all peers' cleanup results. Reconcile observed charges/absence through F11, leaving unknown liabilities/reservations blocked.
9. Materialize controller projections and strict terminal manifests/ValidatorInput; bind acquisition from verified originals; validate_bundle with explicit evaluated_at and evaluate_comparison among appropriate attempted peers; persist result/summary/status. For CAL expose select_common_n recommendation as evidence only, with no config rewrite or research launch.
10. Seal final public files/checksum scope and return IDs plus separate execution/data/eligibility/cleanup states; overall success requires all required outcomes. No next-row loop or automatic campaign retry.

## Explicit provider adapters

- AWS: construct existing AwsProviderDriver with runner/settings; use register_expiry, provision, cleanup. Preserve regional protection, dated full-cost handoff, images/shape review and recovery journals.
- GCP: construct existing GcpProviderDriver with runner/settings; use register_expiry, provision, cleanup. Preserve STOP semantics, zonal/cross-region safety, cost/support/creation claims and private identities.
- Azure: use register_expiry/provision/destroy with AzureRuntimeOptions and required deadlines; preserve returned deployment/expiry/actions/failures/recovery_reference and launch_blocked cleanup result. Azure deallocation differs from normal deletion.

Adapters fill existing F11/provider handoff settings from observed reservation/cost/allowance evidence. They must not invent paid-acceptance/support/image/pricing evidence to make a provider pass. Use existing access connection/trust/deadline/redaction construction; narrow adapter helpers may reuse its internal request construction at this integrated revision rather than duplicating weaker SSH options. No generic driver plugin registry or provider module rewrite.

## Guest boundary

`python -m cloud_network_benchmark.lifecycle_guest --request <absolute-json> --outcome <absolute-json>` is an internal VM-A command, not an independent service. The request includes existing strictly parsed resolved observation/identity, paths, original effective UTC limit and safe remaining-duration/clock information, tool/build/listener context and package digest/revision. Guest validates identity/path/deadline/import provenance before invoking run_measurement with LocalMeasurementProcesses and a local clock. It writes durable typed commands/timings/units/failures/outcome after F03 closes owned files; source binding may remain unavailable until B's final verified retrieval.

Preparation stages same-revision package bytes and existing dependency requirements into an isolated per-deployment runtime and records exact installed versions/digests. No credentials are embedded in request/outcome. Cancellation targets only this deployment/invocation, closes clients and preserves partial files. Controller loss cannot depend on SSH hangup for safety; independent expiry is already registered. Existing diagnostics_guest is staged/invoked separately on both roles, with exact context identity, stop/closure evidence and original outcome paths. Polling bounded control status is allowed; copying measurement files during the run is not.

## Bundle contract

Start with ArtifactLayout's expectations and add exact build sidecars/per-unit Sockperf log declarations. An inventory entry has actual artifact_id, deployment, source role/VM/path, destination, kind, unit, expectation, size/hash and closed state; unavailable declarations have explicit reasons. Accept only normalized in-root paths and unique IDs/destinations. Never derive proof from expected labels/version strings.


Build identity records may already be materialized on the controller by bootstrap.materialize_build_identity_artifacts from the original captured SSH sidecar bytes; retain observed role origin but do not pretend those paths physically exist on B. Separate these exact controller-captured source records from the remote retrieval subset. If F03 materializes identical copies on A, deduplicate by exact ID/path/bytes rather than inventing a second source or copying a B-origin path from the wrong VM. The final remote inventory still retrieves all actual measurement/diagnostic/receiver files from their real producers.

After both-VM verified copy, use acquisition.bind_acquisition_sources with original commands, real BuildObservations reconstructed/validated from sidecar bytes, verified records, receiver command evidence and clock mappings. Attach resulting AcquisitionSource references to the correct unit/parent timings using strictly validated MeasurementTiming construction and actual retrieved clock mappings; do not rerun measurement for binding or recompute/guess missing native boundaries. Preserve original guest timing payload separately; helper iter_source_windows/F08 must independently accept the same exact bytes. Do not replace legacy unknowns with synthetic observations. Diagnostic local-path projection uses a checked remote-to-local mapping and leaves original context/outcome immutable.

Persist complete ValidatorInput JSON, parse it back with model_validate_json and use the parsed value for validate_bundle. Match exact child/parent snapshots and DAY projection described in data-model.md. Persist ValidationResult, human summary and final public checksum index excluding itself/private state. No shared BuildObservation field, relaxed canonical_roles, helper bypass, model_construct or unchecked model_copy.

## CLI contract and ownership

Proposed forms after implementation:

- `cloud-network-benchmark lifecycle preview --config PATH --schedule PATH --row-id ID --cost-inputs PATH --runtime-config PATH --format json`
- `cloud-network-benchmark lifecycle run --config PATH --schedule PATH --row-id ID --cost-inputs PATH --runtime-config PATH --results-root PATH --private-root PATH --controller-session ID --execution-scope paid_request --format json`
- `cloud-network-benchmark lifecycle cleanup --recovery PATH --runtime-config PATH --private-root PATH --controller-session ID --format json`

Acceptance/failure-check configurations may omit schedule selection only where existing resolution/admission supports it; short-study schedule evidence cannot be silently omitted. runtime-config contains sourced allowance/provider options and non-secret private-path references, not a second experiment plan. No public flag bypasses paid gates; offline_simulation scope is programmatic fixture-only and must never dispatch real external provider commands. Existing validate/resolve/dry-run/init and F12 analysis commands remain available.

F09 owns lifecycle_cli.py. The final thin cli.py dispatch must occur after coordinator confirms latest-main/F12 CLI integration and tests both command sets. Exit0 means preview accepted or run fully completed with required data/cleanup; rejected/pending/missed, partial/failed/incomplete run or unresolved cleanup exits1; malformed usage exits2. JSON reports separate dimensions/reasons even when a nonzero exit is appropriate.

## Error and private recovery contract

First cause remains primary; retrieval_failure and cleanup_failure retain their own first causes plus a bounded reference to complete per-stage failure evidence. Public failures are redacted. Persist journal references before uncertain operations; private files keep restrictive permissions and are never source artifacts/checksummed public bundle files. A journal/ledger-write failure prevents new side effects; if resources may exist, cleanup still uses available in-memory/provider references and reports persistence uncertainty. Abrupt controller loss intentionally leaves no invented retrieval/deletion success. Only provider absence evidence reconciles liabilities; billing absence is not a fabricated zero incurred amount.
