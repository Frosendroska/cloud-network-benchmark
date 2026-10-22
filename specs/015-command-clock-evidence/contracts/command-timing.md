# Additive V2 command timing contract

Serialized F01R_CLOCK amendment to merged F01R. Old ordinary V2 results retain UTC-only timing. Strict pre-amendment readers need upgrading before reading new fields. Historical V1 bundles remain rejected.

## CommandResult

Add ordinary validated constructor/model fields to contracts.execution.CommandResult:

```text
duration_basis: Literal['utc', 'monotonic'] = 'utc'
monotonic_started: Optional[float] = None
monotonic_finished: Optional[float] = None
utc_elapsed_discrepancy_seconds: Optional[float] = None
```

No legacy constructor field removed/renamed. CommandRunner.run(request) -> CommandResult unchanged. New fields persist in model_dump(mode='json') and durable_view(), and reload through model_validate. For durable-view metadata, explicitly select model fields before model validation; do not relax extra='forbid' globally.

The [data model](../data-model.md) defines numeric/awareness/order/tolerance rules. UTC basis requires all new numeric values None. Monotonic basis requires all present and:

```text
monotonic_elapsed = monotonic_finished - monotonic_started
abs(duration_seconds - monotonic_elapsed) <= 0.001
abs(utc_elapsed_discrepancy_seconds - (UTC_delta - monotonic_elapsed)) <= 0.001
```

Backward: actual UTC 2026-10-22T12:00:00Z → 11:59:55Z, monotonic 100 → 102, duration 2, discrepancy -7. Forward: UTC finish 12:00:12Z, same monotonic pair, discrepancy +10. No-step: UTC finish 12:00:02Z, discrepancy 0. Preserve actual instants in all cases.

Reject naive UTC, nonfinite/negative/reversed monotonic values, partial pairs, absent discrepancy in monotonic mode, nonfinite duration/discrepancy, boolean numeric inputs and inconsistent observations. UTC normalization preserves instants. Existing succeeded=exit 0, failed=nonzero exit, timeout/interruption=null exit rules remain.

## SubprocessCommandRunner

Use actual time.monotonic readings, never UTC subtraction for elapsed duration. Capture UTC then monotonic immediately before subprocess.run; monotonic then UTC after current success/failure/TimeoutExpired/KeyboardInterrupt handling. Return real readings and discrepancy with monotonic basis. Preserve invocation/environment/timeout/output/redaction/outcome behavior. Existing uncaught exceptions remain outside this feature; no new outcomes. Tests monkeypatch module clocks/subprocess rather than run SSH/cloud commands. ScriptedCommandRunner returns exactly supplied validated results.

## CommandEvidence and durable projection

Add to contracts.measurements.CommandEvidence:

```text
duration_basis: Literal['utc', 'monotonic'] = 'utc'
utc_elapsed_discrepancy_seconds: Optional[float] = None
```

Reuse existing monotonic_started/monotonic_finished/started_at/finished_at Evidence fields. Monotonic wrappers require all observed values and a complete valid CommandResult durable projection in result. Match wrapper/result basis, UTC instants and numeric evidence (0.001 s tolerance). Normalize both copies consistently. Explicitly monotonic result cannot be wrapped as UTC. Historical UTC wrappers with partial/missing dictionaries retain existing validation.

Producers supply request.durable_view(), result.durable_view() and outer timing/identity. A native producer constructing its own result supplies the same valid fields. Validate a projection onto CommandResult fields while permitting documented durable metadata argv/redactions_applied. Preserve raw environment/stdin/redactions rejection exactly as before. Do not add a generic serializer/projection service.

## Evidence limits and schema exports

Signed discrepancy records auditable clock disagreement including possible sampling/rounding/slew/steps. It cannot certify synchronization, cause, absolute accuracy or detection of cancelling steps. Existing clock estimates and validity/comparison policies retain ownership.

Regenerate active specs/003-v2-shared-contracts/contracts/child-run-manifest.schema.json and validator-input.schema.json from models. Keep registry unchanged. Link this amendment from existing shared-contracts.md. Encode representable basis/presence/numeric constraints in generated JSON Schema. Cross-field arithmetic stays runtime validation/tests because ordinary JSON Schema cannot compare field differences; do not claim schema checks alone prove arithmetic.
