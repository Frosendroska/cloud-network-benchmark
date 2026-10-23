# Research: deterministic native analysis

Date: 2026-10-23. Base: merged F08 `4a80f4af86feddc86e4ab930d254a19e29b35591`. No cloud, traffic, external research or application edits occurred.

## Source reconciliation

Read current AGENTS, autonomous WORKFLOW and constitution 2.0.0; canonical Thesis `advisor/docs/design_document.md` §§5.1/8/10, latest DECISIONS, FRAMEWORK_IMPLEMENTATION_NOTES §§results/provenance/analysis and deferred skill, EXPERIMENTS, M04, roadmap and IMPLEMENTATION_PROMPTS F12 lines145–153. Current F08 code and specs/010-v2-native-validation define parser/validation behavior. Canonical sources agree on native files, one-minute DAY summaries, independent fresh deployments, within-provider placement effects, source provenance and immutable inputs. Historical F08 planning text saying functions are not implemented is superseded by their merged code. Older notes leaving skew policy open do not undo F08's declared <=60/60–300/>300 handling. No research conflict remains.

## Decision 1 — Consume the existing validation boundary

**Decision**: A small F12-local selection JSON pairs saved ValidatorInput and ValidationResult paths. Parse existing models, require matching run/mode, reread manifests/native artifacts and call validate_bundle with the saved result's evaluated_at. Use current validation as authority; preserve the recorded result/hash and describe any changed local validation rather than trusting a saved success flag. Evaluate matched provider groups with existing evaluate_comparison. Inputs and saved results are never modified.

**Rationale**: F09 persistence does not need to exist before F12 can replay serialized existing DTOs. The descriptor is a local invocation contract, not a new shared DTO/schema root. Saved evaluation time avoids inventing a fresh wall-clock value during deterministic replay.

**Evidence**: validation.py:1260 validate_bundle and :1441 evaluate_comparison; metrics format_version=1 contains identity, common_n, commands, timings, execution, sources, units and diagnostics. Existing test_native_validation_replay.py:_materialize_complete_bundle provides actual four-mode materialization.

**Alternatives**: Trust saved metrics without checking bytes; require F09 CLI output; or amend shared DTOs. Each adds a trust gap or unnecessary dependency.

## Decision 2 — Reuse measured support rather than duplicate validity logic

**Decision**: Keep a narrow adapter inside analysis.py over native public parsers/statistics and the existing read-only F08 selection helpers (_parse_unit, _acquisition_windows, _source_supported_windows, _measured_samples) plus acquisition replay. These helper imports are internal coupling to the merged base, not a claim of a new public F08 API. Parity tests compare accepted samples, measured totals and exclusions with F08, including future main integration. Do not change F08 files.

**Rationale**: Reconstructing support from sample hulls, filenames, requested durations or copied overlap totals would be incorrect. Reimplementing the existing proof and warm-up selection invites drift. Chunkwise parsing bounds DAY RTT materialization to one native chunk plus active bin buffers; histograms can use a deterministic second pass instead of storing all daily RTT samples.

**Binning decision**: Default 1-second short/calibration views and 60-second DAY bins, anchored to supported measured starts, half-open. RTT is assigned by supported transmit time; keep any boundary uncertainty explicit. Receiver bin values use only whole non-omitted native aggregate receiver intervals wholly attributable to the bin, no prorating or sender fallback. A straddling/unknown-byte interval makes the affected bin's primary throughput unavailable; preserve separately identified whole-interval support and gaps. No whole-day RTT quantile is made by combining chunk quantiles: DAY publishes per-bin/per-chunk RTT and a raw-sample histogram. Overall receiver totals are computed from original intervals.

**Alternatives**: Average interval rates/quantiles, divide bytes over nominal missing time, or average stream durations. These change the measured quantity. F08 already sums parallel bytes within one native interval; do not sum parallel elapsed seconds.

## Decision 3 — Preserve independent units and existing eligibility

**Decision**: Deduplicate identical selected run descriptors; reject contradictory duplicate run IDs. Fresh scalar phase metrics are one point per unique deployment, provider, phase and scenario; conflicting attempts claiming one deployment do not become extra replicates. Display all selected attempts for reliability, but a failed/replaced attempt does not duplicate the accepted deployment. Acceptance/CAL/DAY are separate repeated/non-study views.

Provider groups use the existing campaign/config/experiment/scenario/schedule/phase identity and evaluate_comparison, preserving missing peers and frozen-N/source/timing restrictions. Group summaries use deployment points, median and Q1/Q3 with F08-compatible linear quantiles; n=1 has degenerate descriptive IQR and no inferential claim. A distribution of deployment-P95 points is labeled as such, never presented as a pooled packet P95.

For placement, compare valid standard fresh S1 versus S3 distributions within provider, phase, recorded window label and compatible non-placement measurement settings/common N. Report S3 median minus S1 median RTT in microseconds and 100*(S3 median throughput/S1 median throughput-1) percent. Zero/missing S1 is unavailable. Distinct config/scenario identities remain visible and do not make S1/S3 invalid merely because placement is intentionally different. No invented pairwise matching, p-value or provider ranking. Retain actual UTC date ranges and same-week/concurrent deviations rather than silently excluding date differences or claiming recurrence.

## Decision 4 — Reliability, diagnostics and costs are recorded context

**Decision**: Reliability counts unique selected attempts, preserves original/replacement links and reports its denominator, missing phases and provider failures. Diagnostics retain both roles, missing/reset states and F08 one-second alignment eligibility; otherwise supported per-host series may use labeled local/relative axes. Do not introduce performance thresholds.

Costs use validated saved admission/reservation metadata and an optional explicit digest-pinned snapshot of the existing F11 ledger. Parse it read-only; never instantiate or lock/write a BudgetLedger. Join selected reservation/campaign/provider IDs, preserve recorded provider_billing actual/estimate/final distinctions and event/source references, verify event digests and supplied underlying source hashes before labeling a charge verified. Missing/unverifiable charge/runtime/transfer evidence stays unavailable. Count a shared reservation once per reservation ID, never once per child/bin. Keep scope totals separate from provider allocations; no fabricated allocation or current price lookup. This is reporting, not accounting reconciliation or new admission behavior.

## Decision 5 — Standard static plots and deterministic local publication

**Decision**: Add only optional `analysis = ["matplotlib>=3.7,<4"]` in pyproject.toml. Use lazy Matplotlib Agg imports and bundled DejaVu Sans, record resolved Matplotlib/NumPy/backend/font versions. No direct NumPy dependency or replacement quantile math. Dependency metadata check found existing offline Python3.11.13/pytest8.4.2/Pydantic2.14.1, with Matplotlib and NumPy absent. Coordinator subsequently reported successful authorized official-PyPI installation: Python3.11.13 process architecture x86_64 on Darwin arm64 host, Matplotlib3.11.2/NumPy2.4.6, Agg smoke PNG/SVG exit0, MPLCONFIGDIR=/private/tmp/cnb-matplotlib-config. This is coordinator-reported tooling evidence, not executed F12 acceptance. Implementation records actual versions and cannot skip visual checks as passing.

Output sorted CSV/JSON, static PNG/SVG and escaped offline HTML using standard library HTML generation. Plot fixed source-derived data and stable styles; disable SVG dates/random IDs using fixed hash salt, deterministic metadata and no wall-clock/output-path content. Histograms use 50 equal-width edges from supported raw-sample min/max (deterministic two-pass; constant values use one centered nonzero-width bucket); deployment distributions show all points plus median/IQR. Use no hidden trimming or automatic spike removal. Require byte-identical replay under the recorded fixed environment; cross-version bytes are not promised.

Publication preflights existing destinations and resolved path/symlink overlap with every input/root, stages only under the requested output parent and publishes a completed directory once. A failed render must not advertise a complete report or alter a source/existing output. Recommend the already ignored results/processed location or a source-disjoint output outside the checkout, so generated report files do not themselves change the next observed Git dirty identity. Do not weaken RepositoryGitProvenance or introduce new ignore rules. No network assets, browser service, Parquet or new platform. Thesis interpretation and canonical figures remain outside this repo; reusable analysis skill remains deferred.

## Clarification coverage

0 human questions. All ten clarification taxonomy categories are Clear: functional scope, domain/data, interaction, non-functional qualities, integrations, edge/failure cases, constraints/tradeoffs, terminology, completion and placeholders. Ordinary engineering choices above resolve execution details; N, exact study windows and scientific conclusions remain evidence-dependent/outside scope. Specification checklist stays 16/16 with no toggled markers.
