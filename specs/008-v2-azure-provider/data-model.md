# F06 data model

All new local models live in `src/cloud_network_benchmark/azure_provider.py`; no edits to `contracts/`, schemas or manifests. Reuse existing `StrictModel`, `Evidence`, `StringEvidence`, `StructuredEvidence`, `FailureEvidence`, `CleanupState`, `CommandRequest/Result/Runner`, `ProviderDeploymentInput/Output`, `ProviderVmOutput`, `RemoteConnectionData`, `ReservationEvidence` and `ExpiryRequest/Evidence`.

## AzureRuntimeOptions (feature-local, private input)

| Field | Constraint |
| --- | --- |
| subscription_id, tenant_id | Valid explicit UUIDs; match authenticated provider/account responses; no inferred default subscription |
| controller_cidr | IPv4 /32; equals the supplied security intent |
| attempt_root | Absolute private directory, identity-bound and exclusively created; refuse foreign/symlink paths |
| authentication_reference | Nonempty private attempt key path; no key contents exported |
| ssh_public_key | Valid public-only authorized key; never derive through unauthenticated remote access |
| selected_image | Canonical Ubuntu 24.04 amd64 concrete version/ARM image reference resolved during preflight; never leave `latest` as observed output |
| complete_workflow_seconds | Finite positive caller bound from cap activation through full normal cleanup, including expiry setup, measurement and final copy; no invented stage defaults |
| post_registration_workflow_seconds | Finite positive conservative bound for pair apply, bootstrap/readiness/stabilization, measurement, final copy and cleanup; rechecked without subtracting consumed registration time |
| cap_deadline_monotonic | Finite original monotonic deadline mapped once from active reservation; retained unchanged across calls |
| admission_evidence | Caller snapshot bound to reservation/source identity, outstanding_cleanup=false and explicit priority-review reference when required; not an F06 budget ledger |
| retry_end_at | Aware UTC with `expiry_trigger < retry_end_at <= reservation.expires_at` |
| poll_seconds, command_timeout_seconds | Finite positive ceilings; every command/wait clipped to remaining original monotonic deadline |
| expiry_cost_input | Nonempty source ID, date, SHA-256, nonnegative Automation rate and positive bounded job-minute estimate; no assumed free tier |

The trigger comes from existing `ExpiryRequest.expires_at`, not a new shared field. `retry_end_at - trigger` is the configured emergency allowance; it is reserved in cost/lifetime and is not a guaranteed dispatch margin. Both `cap_started_at + complete_workflow_seconds <= trigger` initially and `utc_now + post_registration_workflow_seconds <= actual trigger` after registration/before pair apply must hold. Registration delays do not shrink the required post-registration workflow. The latter bound includes caller-supplied measurement and remaining setup/copy/cleanup bounds, never less than the selected mode's actual required duration. Admission evidence is explicitly supplied by the caller; it is never fabricated from an estimated price or scenario name.

## AzureRecovery (feature-local private journal)

Bound to `IdentityContext`, run_id, deployment_id, subscription and config hash. Contains deterministic resource group/VM/disk/NIC/public-IP/network/PPG/peering names and expected ARM IDs; expiry account/runbook/schedule/job GUID/custom-role/assignment IDs; pair/expiry state and private known-host/key references; original cap UTC+monotonic mapping; command action IDs; whether creation may have started; actual/unknown resource observations and primary/secondary failures. Write the deterministic intent before the first side effect, append observed IDs without replacing prior evidence, and atomically persist after each boundary. Files mode 0600 under a mode-0700 workspace, no Git/result-bundle export. Never adopt an existing different identity. Recovery with lost state must inventory exact expected IDs and owned group; unknowns remain unresolved.

## AzureProviderResult (feature-local return)

- `deployment`: existing `ProviderDeploymentOutput`, complete when both roles have confirmed resource/address/image/shape/region/zone/connection evidence; otherwise each missing field has `unavailable` structured evidence per existing validation.
- `expiry`: existing `ExpiryEvidence` for the same attempt/provider/action `deallocate`.
- `actions`: durable redacted `CommandRequest/Result` records including real UTC and monotonic elapsed fields where available, not synthesized success logs.
- `failures`: ordered `FailureEvidence` list; first is primary, subsequent output/host identity/cleanup failures remain separate.
- `recovery_reference`: private journal path in memory; durable export substitutes a nonsecret recovery ID.
- `cleanup_required`: true once preparatory or pair creation may have started, including failed apply with no outputs.

No lifecycle manifest is written here; F09 uses these results with the existing manifest API.

## ExpiryEvidence mapping (existing DTO)

`registration_id` is the observed ARM jobSchedule resource ID, `registered_at` is the controller's actual UTC after final matching Azure readback, `expires_at` is the observed scheduled trigger, `target_selector` canonically encodes the ordered exact A/B ARM IDs. The declared request is checked against reservation ID/campaign, run/deployment and namespace. `provider_metadata` is structured evidence, with at least:

- Mechanism `azure_automation_once_deallocate`, API version, resource group/account/schedule/runbook IDs, job GUID and managed-identity principal ID.
- Observed enabled OneTime UTC start, binding's exact string parameters, published-state/content SHA-256, runtime and module provisioning/version evidence, custom-role actions and scope/assignment IDs.
- Reservation ID/cap start/end, actual trigger, retry-end, complete-workflow bound, minimum lead check and dated price-input identity/job-minute bound.
- Registration/readback command IDs and per-target triggered results when observed, failure details and cancellation observations. Triggered means the action was observed running; per-target instance-view evidence separately establishes deallocation. Do not fabricate an Azure native `expiry` VM field.

States: not_registered → registered or failed; registered → triggered, cancelled or failed. Failure after registration retains prior registration metadata. `cancelled` requires observed unlink/removal after both VMs are verified absent. DTO schema validation alone does not prove publication/permissions; the driver enforces metadata predicates.

## AzureCleanupResult (feature-local return)

`identity`, `cleanup_state` using `CleanupState`, `attempted_at`/`finished_at`, action evidence, failures, `resources` observations and `launch_blocked`. Each resource observation contains type, exact ARM ID, ownership binding, observed state (`absent`, `present`, `unknown`), observation time and source action ID. `succeeded` requires all expected/discovered owned resources absent, empty complete group inventory and removed expiry prerequisites; `failed` plus `launch_blocked=true` for any residual/unknown, permission/timeout/interruption or missing ownership proof. A Terraform exit zero, empty local state, HTTP 202 or VM deallocated status alone is insufficient.

Cleanup progression uses existing not_started → attempted → succeeded/failed semantics. Recovery retries are explicit new cleanup attempts retaining history; no mutation of a completed existing lifecycle event or automatic launch is implied.
