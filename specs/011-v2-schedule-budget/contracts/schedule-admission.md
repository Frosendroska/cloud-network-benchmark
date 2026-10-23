# F11 schedule/admission interface contract

These are proposed F11-owned library APIs for implementation, not existing executable capabilities. Frozen imports come from `cloud_network_benchmark.contracts`; no shared schema change is requested. F09 owns later lifecycle calls and side effects.

## Scheduling APIs (`scheduling.py`)

```python
load_schedule(path: Path, *, config_paths: Mapping[str, Path], repository_root: Path) -> list[ScheduleEntry]
inspect_schedule(entries: Sequence[ScheduleEntry], *, campaigns_by_entry_id: Mapping[str, ResolvedCampaign], now: datetime, require_primary_week: bool = False) -> ScheduleInspection
assess_provider_skew(phase_id: str, *, selected_providers: Sequence[Provider], actual_starts: Mapping[Provider, Evidence[datetime]]) -> SkewAssessment
```

CSV required columns: `entry_id,config_id,experiment_id,scheduled_start,window_label,priority`; optional `replaces_run_id`; no other/duplicate/blank/secret-bearing columns. UTF-8 input, optional UTF-8 BOM only at file start; preserve full source bytes/hash and exact original header/record text excluding its final record delimiter, preserving embedded quoted newlines. Reject invalid CSV/UTF-8, duplicate entry IDs and malformed aware timestamps; empty scheduled_start means intentional pending. Config paths are supplied explicitly and must resolve under repository config roots without traversal/symlink escape. Reuse actual `resolve_campaign(path, repository_root, schedule_entry=entry)`; no config file generation or mutation. EXP-* is config research identity, entry_id is the human register execution identity such as M-S1-D1-NIGHT.

Preview allows pending N/time and deferred scenarios, reports every blocker and exact input identity. Primary-week validation requires all56 entries with 28 per scenario, four named windows per seven consecutive dates, all providers and matching P0/P1 UTC starts. If dates are pending, report the incomplete check, never pass it. Reports distinct campaign/child resource-state-result namespaces and four-VM/cloud demand, not actual quota.

Skew uses max-minus-min actual per-phase UTC starts; ≤60 meets target, (60,300] eligible with `provider_skew_target_missed`, >300 `provider_skew_excluded`, missing `provider_skew_unavailable`. Do not mutate execution/data validity or infer starts from launch/schedule timestamps.

## Admission APIs (`admission.py`)

```python
load_cost_inputs(path: Path) -> CostInputs
BudgetLedger(path: Path, *, scope_id: str, utc_now: Callable[[], datetime], monotonic_now: Callable[[], float], controller_session_id: str, lock_timeout_seconds: float)
BudgetLedger.initialize(inputs: CostInputs) -> LedgerState
BudgetLedger.inspect() -> LedgerState
BudgetLedger.reserve(request: ReservationRequest, *, plan: AdmissionPlan) -> AdmissionDecision
BudgetLedger.check_stage_fit(reservation_id: str, *, stage: str, remaining: WorkflowAllowance) -> StageDecision
BudgetLedger.claim_creation(reservation_id: str, *, provider: Provider, expiry_requests: Sequence[ExpiryRequest], expiry_evidence: Sequence[ExpiryEvidence]) -> StageDecision
BudgetLedger.reconcile(event: Reconciliation) -> ReservationEvidence
```

Caller constructs the existing ReservationRequest from the resolved campaign and current ledger view; reserve revalidates every field against authoritative plan/ledger rather than trusting caller cost/cleanup booleans. Request.source_sha256 hashes the exact saved scoped CostInputs payload. Observed configured cost IDs/hashes/amounts must agree; intentionally unavailable config estimate/hash is filled only in the F11 sidecar and never silently replaces the original config's identity. Refuse mismatched mode/cap/priority/provider/entry/estimate, secret fields, missing source bytes or false source hashes.

`initialize` is exclusive, explicit and must fail if ledger or initialized lock/scope marker already exists; no CLI auto-init, corruption recovery or clearing active state. The adjacent private regular lock file stores the initialized scope marker before first ledger commit and survives normal unlock. Interrupted initialization remains unavailable for automatic replacement. Shared path is caller-selected; separate P0/P1 invocations must use the same scope/file and live controller_session_id. Reads/mutations require both files and validate scope/schema/source history. Use flock on the existing safe regular file; exclusive lock covers read, validation, budget/claim decision, fsynced replace and revision. Fail closed on missing initialized ledger/marker, malformed state or ambiguous commit. Supplied lock_timeout_seconds is finite positive non-boolean and bounded further by any active operation deadline; no lock-file deletion/TTL recovery. Kernel lock ownership, not marker existence, serializes normal operations.

`reserve` returns pending/rejected/missed reasons without funding mutation, or one observed ReservationEvidence with state reserved, stable ID, complete ceiling, exact UTC cap start/end and policy `f11-activation-before-expiry-v1`. Set cap immediately before registration; no initialization/preview time is charged. Persist live monotonic mapping/session, source/config/row fingerprint, per-provider identities and cost inclusion handoffs. Any unresolved cleanup/unknown initialized state blocks. Exact duplicate returns the same evidence with newly_reserved=false; changed input is rejected. Source/entry changes cannot evade the stable claim.

`check_stage_fit` checks all remaining stages, not just the next command; accepted stages are registration/preparation/measurement/retrieval/cleanup. Completed stages have zero remainder; remaining protocol units/grace retain minimum bounds from the original plan. Deadline is earliest trigger/cap, preserving original live monotonic origin and actual UTC evidence. Supplied durations cannot omit remaining fixed work or grow deadlines; waiting to slot counts. Restart/session mismatch blocks new work and creation; cleanup bookkeeping remains possible. Different-session unresolved active work also blocks new reservations, while same-session healthy concurrency is allowed. The F09 handoff exposes effective deadline and positive cleanup allowance for existing RemoteDeadline; F11 does not change RemoteDeadline or run access code. Copy handoff retains at-most-one retry and remaining-cap-minus-cleanup rule.

`claim_creation` checks complete current workflow fit, active matching reservation, no global cleanup liability, exact selected-provider request/evidence sets and identities, correct provider actions, matching registered times/triggers, and all providers registered before any selected cloud is created. It durably claims a provider once before returning creation_granted=true. Duplicate claims return false; uncertain claims never authorize replay. Reservation_id binding uses ExpiryRequest plus frozen provider inputs, not an invented ExpiryEvidence field. F09 must treat false as no creation permission and preserve uncertain work for cleanup.

`reconcile` is atomic/idempotent by event ID and exact payload fingerprint. It preserves primary and secondary failure evidence, all source/attempt identities, actual vs conservative-estimate cost and billable-resource cleanup. Expiry stop/deallocate is never cleanup completion. Only verified absence clears cleanup; absent price/billing does not release cost liability. Partial-provider failure remains linked to original; replacement qualification/counts are checked before new reservation, not automatic retry logic.

## Provider support payload

For merged AWS, each provider cost handoff is StructuredEvidence containing exactly the values required by AwsRuntimeSettings: `source_path`, `source_sha256`, `expiry_input_id`, `expiry_input_sha256`, `expiry_cost_allowance_per_region_usd`, `expiry_cost_regions`, `expiry_cost_included_usd`. Exact fixture ID/hash and regions bind $0.0139/region (S4 $0.0278); amount is inside the total reservation, sourced from named uncertainty allocation. Convert Decimal to the frozen DTO/provider numeric field only at boundary, with conservative upward rounding. Source path/hash identify complete scoped admission cost payload, while expiry ID/hash identify the separate dated AWS fixture. Never substitute one for the other.

Other providers supply feature-local lead/action/support inputs through AdmissionPlan, with explicit synthetic labels for offline tests. Missing real input blocks paid_request; no dependency on unmerged provider code. Later coordinator-owned integration adapts these to each merged provider's actual settings.

## Offline preview entry point

Planned `python -m cloud_network_benchmark.admission --help` and `preview --schedule PATH --config-map PATH --cost-inputs PATH --now UTC` accept only local files/time and emit deterministic JSON with schedule/pending/priority/budget-source/VM-demand findings. config-map maps existing config IDs to predefined paths. Preview never initializes or writes a ledger, registers expiry, claims creation, invokes a subprocess or launches a worker. Exit0 means valid inspectable intent (possibly pending); exit2 means invalid input. Actual paid eligibility is an explicit structured field, never inferred from exit0.

## Reason codes and testing

Stable reason families: `invalid_schedule`, `identity_mismatch`, `source_mismatch`, `schedule_pending`, `calibration_pending`, `prerequisite_pending`, `price_refresh_required`, `support_cost_missing`, `optional_review_required`, `slot_missed`, `budget_exhausted`, `workflow_time_insufficient`, `expiry_required`, `expiry_mismatch`, `cleanup_open`, `duplicate_conflict`, `creation_already_claimed`, `controller_session_changed`, `ledger_unavailable`. Evidence includes applicable identities/policy but never echoes secret contents. Test every family with materialized files/fake clocks; no real credentials/resources or benchmark traffic.
