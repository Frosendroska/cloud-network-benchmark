# Engineering research and decisions

Date: 2026-10-22. Existing contracts and routed sources determine this bounded amendment; no external research required. Thesis context is read-only.

## Source reconciliation

- Implementation AGENTS.md, constitution 2.0.0, docs/autonomous/WORKFLOW.md: actual commands, UTC/monotonic mappings, honest failed/interrupted evidence, serialized shared contracts, offline checks.
- Thesis `advisor/docs/design_document.md` §§3–5, 7–8: native timing, failures, reconstructable result bundles.
- Thesis `DECISIONS.md` 18–19 October: monotonic RTT timing with UTC mapping/error estimates; no fabricated alignment/protocol changes.
- Thesis `experiments/FRAMEWORK_IMPLEMENTATION_NOTES.md`, Measurement/commands, Lifecycle/timings, T04/T08: actual timestamps, clock-error estimates and failure preservation.
- Thesis `tracking/IMPLEMENTATION_PROMPTS.md` F01R/F03/F04: command provenance, fake-clock failures and per-host monotonic/UTC evidence. `tracking/M04_implementation.md`, `experiments/EXPERIMENTS.md`, `tracking/framework_implementation_roadmap.html`: merged contracts precede consumers; offline proof remains separate from paid acceptance.
- No current research conflict found. This changes neither RTT computation nor TCP measurement/overlap, alignment eligibility, schedule/skew, budget, calibration N or study windows.

## D1: Explicit basis and raw monotonic endpoints

**Decision**: Add `duration_basis='utc'|'monotonic'` defaulting to utc. Add nullable monotonic endpoints to CommandResult; reuse Evidence endpoints in CommandEvidence. Require complete monotonic opt-in; keep legacy UTC strict.

**Rationale**: execution.py rejects backward UTC and requires elapsed equal UTC subtraction; measurements.py independently rejects backward command UTC. Opt-in distinguishes old valid payloads from new evidence without fabricated migration.

**Alternatives considered**: Unconditional relaxation loses legacy checks; clamping/sorting falsifies UTC; dropping UTC loses provenance; a clock service adds needless architecture.

## D2: Signed discrepancy without inferred accuracy

**Decision**: Add nullable `utc_elapsed_discrepancy_seconds`, required in monotonic mode and validated against UTC delta minus monotonic delta. Retain existing 0.001-second consistency tolerance, finite numbers, no boolean numeric inputs. Record actual discrepancy even when small/zero.

**Rationale**: Readers can audit clock disagreement. Sampling, rounding, slew or cancelling steps mean it cannot certify exact step cause or absolute UTC error. Zero discrepancy does not prove synchronization or eligibility. Existing ClockMapping remains the source of synchronization/error estimates.

**Alternatives considered**: Boolean step flag hides magnitude/sign and overclaims cause; automatic invalidation invents policy; serialization-only computed properties complicate strict reload. A small validated persisted scalar makes round-trip semantics explicit.

## D3: Redacted projection and additive V2

**Decision**: Retain CommandEvidence.result as durable dictionary. Monotonic basis requires standard result timing fields, numeric agreement with outer evidence and matching UTC instants. Preserve documented durable metadata (`redactions_applied`, existing `argv`) without admitting excluded secrets. UTC-native records retain existing partial/unavailable behavior; new fields default utc/None.

**Rationale**: Replacing the dictionary with a model breaks older consumers and durable metadata. New fields are additive within version 2; strict pre-amendment readers need upgrading before new payloads. V1 rejection remains.

**Alternatives considered**: No projection consistency permits contradictory evidence; requiring all legacy dictionaries to become complete CommandResults exceeds scope; new schema files unnecessary because manifest and validator exports already embed CommandEvidence.

## D4: Synchronous capture and scope

**Decision**: Sample aware UTC then monotonic immediately before execution, monotonic then aware UTC after each currently returned subprocess path. Use real time.monotonic elapsed, controlled clocks in tests. Keep current invocation/outcome/exception behavior. Do not change MeasurementTiming, TimingGap, ClockMapping, diagnostics or providers.

**Rationale**: Scientific measurement-window interpretation belongs to its feature. This repairs contracts F03 cannot truthfully populate during a step and supports consumers independently of unfinished siblings.

**Alternatives considered**: F03-only fix leaves synchronous runner incorrect; UTC-derived duration misstates basis; cross-host monotonic subtraction has no shared origin.

## Clarification scan

Zero human questions under authorized workflow. All ten clarify categories are Clear after D1–D4: scope, data, interactions, quality, dependencies, failures, constraints, terminology, completion and placeholders. Spec quality 16/16. Unknown scientific clock accuracy is explicitly unavailable rather than a pending implementation choice.

## Tooling observation

Spec Kit reports nominal BRANCH=015-command-clock-evidence from feature directory despite explicit branch context. Actual Git remains codex/f01r-clock-evidence; feature paths resolve correctly. Do not rename Git branch to match resolver label.
