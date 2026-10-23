# F11 local data model

All new types live in `admission.py` (inspection types may live in `scheduling.py`). Revalidate even supplied model instances on entry. Existing shared DTO/schema definitions remain unchanged. Durable files contain no credentials, Terraform state or private key paths.

## Frozen shared values

Use `ScheduleEntry`, `AdmissionRequirements`, `ReservationRequest`, `ReservationEvidence`, `ExpiryRequest`, `ExpiryEvidence`, `ResolvedCampaign`, `ProviderDeploymentInput`, `Evidence`, `StructuredEvidence`, `FailureEvidence`, `CleanupState` and existing identity/enums. Missing evidence remains `{unavailable_reason: ...}`. ScheduleEntry original row hash is SHA-256 of its exact UTF-8 original_row. ReservationEvidence.expires_at is cap end; ExpiryRequest/ExpiryEvidence.expires_at is the provider's earlier compute trigger when required. They are deliberately not conflated.

`ReservationRequest` lacks workflow/prerequisite/funding state; feature-local `AdmissionPlan` supplies it. `ExpiryEvidence` lacks reservation_id; bind it with the matching ExpiryRequest and provider attempt input. Config cost hash/estimate may intentionally be unavailable pending F11: resolve them in the admission sidecar, preserving original config bytes/hash. If config supplies observed values, mismatches fail; no silent config mutation.

## Local records and constraints

| Entity | Fields and relationships |
| --- | --- |
| CostInputs | cost_input_id, prices_checked_on, source_path, exact source_sha256, scope/ranges, acquired_on evidence, schema/normalization ID; register-entry envelopes and funding buckets; provider expiry/cleanup policies; price-refresh/review evidence. Small saved JSON payload is authoritative for its declared scope only. |
| WorkflowAllowance | registration_seconds, provision_seconds, readiness_seconds, stabilization_seconds, measurement_seconds, measurement_shutdown_seconds, retrieval_seconds, cleanup_seconds; source/policy IDs; selected provider lead_seconds. Registration/provision/readiness + stabilization form preparation. measurement_seconds includes measurement_shutdown_seconds; default protocol lower bounds before that grace are75 acceptance,225 short,950 calibration (12×70+11×10),86407 DAY (86400+5+2). These are minimums, not promises about real startup/notification latency. Supplied allowances must cover additional observed/policy startup costs. |
| GateEvidence | execution_scope (`offline_simulation` or `paid_request`), F10/paid-authorization/acceptance/calibration/day/window/size-export/quota-review evidence as applicable, optional primary-review evidence. Synthetic fixtures are labeled and cannot authorize paid_request. Acceptance/calibration/day have stage-specific prerequisites; acceptance never requires its own successful outcome. |
| AdmissionPlan | resolved campaign and child identities/namespaces, source-scoped CostInputs, WorkflowAllowance, GateEvidence, exact schedule entry, per-bucket candidate funding, provider policy inputs, original-failure evidence if replacement. Canonical input fingerprint excludes observation-clock samples but includes all identity/config/source/policy/funding/gate content. |
| LedgerState | schema_version1, scope_id, source/input identity history, revision, remaining primary/replacement/tax/uncertainty buckets, records indexed by campaign_id, stable entry claims, charge/reconciliation event IDs, provider replacement counts, unresolved cleanup set. All invocations use this same explicitly supplied file. |
| LedgerReservation | existing ReservationEvidence plus source/config/schedule/input fingerprints, per-bucket funding, selected child run/deployment/namespace identities, per-provider cost handoffs/triggers, creation-claim set, controller_session_id and activation monotonic value, allocated incurred liability, cleanup/reconciliation evidence. Preserve the original activation/cap forever. |
| AdmissionDecision | status (`pending`, `rejected`, `reserved`, `missed`), newly_reserved, existing ReservationEvidence, reasons, ledger revision, source/config/row identity, per-provider complete-workflow cost handoffs, effective work deadline and reported simultaneous VM demand. Missing evidence is explicit. |
| StageDecision | eligible boolean, reason codes, remaining allowance/deadline evidence; creation_granted boolean only for a new durable per-provider claim. A repeated claim returns false even with valid expiry. |
| Reconciliation | event_id, reservation_id, provider/attempt identity, source hash/path, incurred amount and actual/estimated label, cleanup evidence with billable inventory, terminal status/failure. An event can update cleanup without claiming an actual bill; unknown charges preserve conservative liability. |
| ScheduleInspection | exact shared entries, resolved config IDs, per-entry pending/error/missed reasons, primary matched slot structure, namespace collision findings and simultaneous demand; never allocates resources. |
| SkewAssessment | phase/unit identity, selected providers and actual UTC starts, skew Evidence[float], comparison eligibility, target_met Evidence[bool], timing finding; execution/data states are untouched. |

The initial WorkflowAllowance obeys V4 in full. A later stage remainder explicitly zeros completed stages and retains minimums/grace only for work still to execute; a completed stabilization is not charged again. Allowed stage names are `registration`, `preparation`, `measurement`, `retrieval`, `cleanup`; unknown stage names fail. Original plan and remaining-plan evidence remain separately recorded.

`controller_session_id` identifies one live controller session shared by its concurrent independent invocations on the same local machine; it is not a per-provider/process ID or a persistent license to reset deadlines. The caller provides a new ID on controller recovery. A session mismatch with any unresolved earlier active reservation blocks new launch reservations as uncertain cleanup; same-session healthy active reservations are expected concurrent work, not failed cleanup. Old costs/claims stay recorded. F09 owns explicit interruption/terminal cleanup evidence; F11 does not infer successful deletion from process status.

Normative validation rules referenced verbatim by implementation tasks:

- **V1**: `identifiers and policy/source paths are nonblank; hashes are lowercase SHA-256; unknown fields fail`.
- **V2**: `money is finite nonnegative non-boolean Decimal; local JSON stores decimal strings; calculated ceilings round upward to 0.0001 USD`.
- **V3**: `timestamps are timezone-aware UTC; durations are finite nonnegative non-boolean seconds; cleanup_seconds is positive; provider leads are explicitly sourced and nonnegative`.
- **V4**: `cap seconds are 1200 acceptance/short_study, 1800 calibration, 87600 diagnostic_day; stabilization_seconds is 60; measurement_shutdown_seconds is positive; measurement_seconds covers mode minimum plus shutdown grace`.
- **V5**: `selected providers/child identities/regions are unique and match the resolved campaign; namespace collisions fail; source/config/schedule fingerprints must agree`.
- **V6**: `remaining funding and incurred liabilities are nonnegative; funding moves balance exactly; active unspent equals max(ceiling minus allocated incurred, 0); working commitment <=250 and working plus protected50 <=300 for new admission`.
- **V7**: `schema_version is 1; scope_id is fixed; ledger revision increases once per committed mutation; event and entry/campaign claim IDs are unique; existing source history and attempts cannot be erased`.
- **V8**: `replacement context identifies an original failed short S1/S3 provider attempt; one replacement per original and three per provider; no replacement chains or automatic retained/S2/S4 repeat`.
- **V9**: `offline_simulation cannot supply paid authorization; missing prerequisite/support/refresh evidence blocks paid_request; deferred optional rows never imply admission`.
- **V10**: `nonpositive remaining time forbids new work; original cap and earliest provider trigger never move later; different controller session forbids new creation`.

## State transitions

Inspection is read-only: unresolved intent → pending; invalid input → rejected; past unactivated target → missed. A complete admissible plan under lock moves remaining funding to one reserved record and activates the cap. Exact retries return existing evidence without mutation/new grant. Rejected or pending checks never consume funding.

Reserved → provider creation intent (once/provider) only after all-provider matching expiry registration and stage-fit guard. Claim is durable before caller side effects; a crash afterward leaves creation uncertain. F11 executes no provider action.

Reserved/creation-uncertain → terminal reconciliation only with explicit event identity. Verified billable-resource absence clears cleanup liability; unknown/failed cleanup keeps it open and blocks all new reservations. Accounting release uses existing ReservationEvidence state `released` with reasons, preserves accrued actual/estimated costs and returns only proved unused funding. Cap expiry alone never releases. Duplicate events are idempotent; conflicting payloads under one event ID fail. Unknown billing retains the ceiling as estimated liability, preventing unsupported credit.

Replacements allocate a new reservation from remaining conditional funding after original failure/cleanup gates. Original record/costs remain immutable history. Optional scope requires a sourced explicit review and newly reconciled scoped inputs, never a new empty ledger.
