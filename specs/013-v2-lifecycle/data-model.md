# F09 local handoffs and invariants

These are F09-local function inputs/results composed from existing strict DTOs, not additions to `contracts/` or exported schemas.

| Handoff | Contents | Rules |
| --- | --- | --- |
| Lifecycle request | config path, selected row ID/schedule path, cost inputs, provider runtime settings, allowance source, execution scope, shared controller session, results/private roots | Exact file hashes/IDs; mode comes from resolved config; preview has no external side effect; no implicit next row |
| Initialized campaign | existing InitializedCampaign, parent/child paths, ResolvedCampaign/observations, identities, snapshots | Existing collision protection; original config/resolved/schedule bytes and Git dirty/diff evidence; initialization is not admission |
| Admission/protection | AdmissionPlan/Decision, ReservationEvidence, ExpiryRequest/Evidence per selected provider, WorkflowAllowance, effective UTC deadline | One full reservation/CAL or DAY; original monotonic session and cap origin; complete verified protection before any claim/create |
| Provider execution | original provider input/settings/result, ConnectionOutput, BootstrapResult, ReadinessResult, role-specific diagnostic contexts and guest invocation | Provider differences/native failures preserved; journal reference private; no fictional pair output after uncertain create |
| Guest invocation | exact package digest/revision, resolved observation/identity, build/listener evidence, local workspace, bounded deadline/grace, diagnostic/control references | Local A F03 call; distinct controller/guest monotonic domains; existing binary hashes and invocation/session/boot IDs retained |
| Closed inventory | ArtifactInput declarations, concrete RetrievalItems, missing-reason map, producer closure records, actual end/closure time | Actual source size/hash, same deployment, role/unit/path; both VMs; original files remain unchanged; cannot manufacture closure |
| Retrieved bundle | RetrievalResults/attempts/verified ArtifactRecords, actual bytes, clock mappings, metadata, post-retrieval AcquisitionSources | Unique concrete IDs; same files checked by helper/F08; no record labeled verified without materialized bytes |
| Lifecycle outcome | per-provider execution/cleanup state, ValidationResult/status, original/secondary failures, source refs, reconciliation refs, overall result | Execution success is tool/workflow outcome; data validity/eligibility separate; campaign execution aggregate retains existing semantics; overall headline is incomplete/failed if required data or cleanup incomplete |

## Identity and projection

Reuse campaign_id/observation_id/run_id/deployment_id from initialization. Separate observations and fresh deployments never share namespaces. Retained CAL/DAY share their single deployment throughout their units; DAY minute bins belong to analysis, not the lifecycle identity graph.

ArtifactLayout known expectations are extended using actual supplemental ArtifactInput declarations. Four build sidecars keep their actual bootstrap IDs and deployment-scoped unit_id=None. Each short/CAL Sockperf stdout/counter log has a unique unit-qualified ID and exact actual path/kind/VM A/unit. DAY declarations retain `day-probe-csv/log/observation` family IDs with path_template and unit_id=None; concrete records use the exact family-plus-chunk rule. DAY continuous client belongs to its sole parent unit; receiver native/observation logs stay deployment-scoped.


Build identity records may already be materialized on the controller by bootstrap.materialize_build_identity_artifacts from the original captured SSH sidecar bytes; retain observed role origin but do not pretend those paths physically exist on B. Separate these exact controller-captured source records from the remote retrieval subset. If F03 materializes identical copies on A, deduplicate by exact ID/path/bytes rather than inventing a second source or copying a B-origin path from the wrong VM. The final remote inventory still retrieves all actual measurement/diagnostic/receiver files from their real producers.

The child manifest follows existing F08 projection: non-probe measurement units, commands/timings for those units, records with unit_id=None or a manifest unit. ValidatorInput retains every DAY chunk/record/command/timing. Use validated JSON construction for both; no model_construct or unchecked model_copy.

## State and crash boundaries

Reuse existing legal execution transitions: initialized →provision_ready →running →collecting →validating →succeeded, or failed/interrupted from legal nonterminal states. Cleanup independently follows not_started →attempted →succeeded/failed, or not_required only with evidence that no resource-creating work began. Preserve original failure before secondary failures. Successful data parsing does not change execution failure; data-invalid execution does not become a command failure merely to change a label. Overall lifecycle summary combines the separate dimensions honestly.

Persist recovery identities/references and admission creation claim before creation. Every possible safety/compute allocation remains a cleanup liability until provider evidence proves absence. On reachable exceptions cancel/close owned guest work, attempt final evidence retrieval within remaining allowance, then all relevant provider cleanup/reconciliation. Controller loss has no finalizer: its private journals/ledger survive, provider expiry remains independent, resources are unknown/residual and future launches blocked. A new session permits explicit cleanup accounting, never replay/resume of measurement or uncertain creation.

## Clock/deadline domains

Keep controller live cap origin/monotonic session in F11. Do not transmit a controller monotonic deadline as a guest monotonic value. Guest setup captures its own monotonic/UTC bracket and derives a conservative local duration bounded by the current effective UTC deadline, transport elapsed time and available clock uncertainty; if safe remaining time cannot be established, refuse measurement. Native acquisition anchors retain their guest boot/clock identity. Controller scheduling/stabilization or SSH timestamps cannot assert raw RTT/receiver support.

## Immutability and sealing

Original native outputs, build/observation files and config/schedule snapshots are immutable. Controller projections (aggregate command/stdout/stderr evidence where required, records, acquisition references, validation input/result, summaries) are separate declared files with their own hashes. Project diagnostics' remote output paths to verified local paths only for local parsing, preserving original outcome/context bytes and source mapping.

After terminal execution/cleanup snapshots are persisted, strict ValidatorInput roundtrips, F08 validates and peer comparison updates eligibility, persist final validation status/summary and finish parent aggregation before sealing. Validation input/result are not their own source ArtifactRecords. The final SHA-256 index enumerates the final immutable public bundle scope, excludes itself and private recovery data, and is written last. Its digest may be returned externally. Sealed bundles are never resumed or mutated; later recovery evidence is separate and referenced explicitly.
