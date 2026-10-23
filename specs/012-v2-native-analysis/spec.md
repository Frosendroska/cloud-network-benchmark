# Feature Specification: Deterministic native analysis and visual reports

**Feature Branch**: `codex/f12-v2-analysis`

**Created**: 2026-10-23

**Status**: Implemented on assigned branch; offline validation recorded in [quickstart.md](quickstart.md); convergence handoff pending

**Input**: F12 authoritative goal in Thesis tracking/IMPLEMENTATION_PROMPTS.md, based on merged F08 `4a80f4af86feddc86e4ab930d254a19e29b35591`: produce local reproducible summaries, figures and reports from validated native bundles, preserving research units, provenance and raw files. Planning owns only this feature's artifacts; implementation follows separately.

## Clarifications

### Session 2026-10-23

No critical ambiguities require human questions; routine engineering choices are resolved under the authorized workflow, not recorded as human answers:

- DAY uses one-minute reporting windows, not monthly analysis; windows retain their deployment and original completeness status.
- S3-minus-S1 is the RTT difference direction; relative throughput change uses S1 as denominator, with zero/missing baselines unavailable.
- Existing F08 validity, sample-selection and comparison policies remain authoritative; selected missing peers are never silently dropped.
- A report may expose partial/ineligible source evidence descriptively while keeping it out of trusted matched deployment summaries.
- Fixed renderer environment and analysis identity define the byte-replay boundary; unknown costs stay unavailable and output destinations cannot overlap sources.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Understand one saved experiment (Priority: P1)

An operator selects saved benchmark evidence and receives phase/trial results and time-series/distribution views that show the actual measurements, missing intervals, diagnostics and failures without keeping cloud resources running.

**Why this priority**: Each report must be traceable and numerically correct before several experiments are compared.

**Independent Test**: Replay materialized short-study, acceptance, calibration and partial diagnostic-day fixtures with known receiver bytes/time and raw RTT values; compare the report data with independently calculated expected results and retained source hashes.

**Acceptance Scenarios**:

1. **Given** a valid short experiment, **When** it is analysed, **Then** results describe its phases, receiver-side TCP throughput, raw-sample RTT statistics, observed settings and source evidence.
2. **Given** a retained diagnostic day with probe restarts or missing intervals, **When** it is analysed, **Then** one-minute summaries show supported measurements, gaps and initial sustained-load context, without an invented idle baseline or latency inflation.
3. **Given** a corrupt artifact or incomplete timing, **When** analysis runs, **Then** invalid or unavailable evidence is identified, valid independent intervals survive and no requested duration or filename becomes proof.

---

### User Story 2 - Compare the intended independent experiments (Priority: P2)

A researcher selects saved experiments, providers, scenarios and time windows and obtains distributions and differences whose units are independent fresh deployments. Calibration trials and DAY windows remain separately identified repeated measurements.

**Why this priority**: Treating correlated packets or retained windows as independent deployments would misstate the research evidence.

**Independent Test**: A synthetic selection contains matched and unmatched provider slots, distinct S1/S3 deployments, repeated retained windows, duplicate inputs, failures, replacements and different study dates. Compare its cohorts, deployment counts, medians/IQR and scenario changes with explicit expected tables.

**Acceptance Scenarios**:

1. **Given** eligible providers sharing a scheduled slot and phase, **When** they are compared, **Then** existing validation eligibility is respected, missing peers remain visible and every point identifies its deployment.
2. **Given** S1 and S3 deployment distributions within one provider and declared time-of-day window, **When** their effect is reported, **Then** absolute RTT differences and relative receiver-throughput changes identify the two groups and actual study dates, including deviations from concurrent same-week execution.
3. **Given** acceptance, calibration, retained DAY windows or duplicate deployment input, **When** a fresh-study cohort is formed, **Then** those data do not inflate its independent sample count; exclusions and unavailable comparisons have reasons.

---

### User Story 3 - Reproduce and inspect a portable local report (Priority: P3)

An operator creates machine-readable tables, figures and readable HTML, then repeats the same analysis with the same evidence and analysis version to obtain the same report without changing raw inputs.

**Why this priority**: Results must be reviewable and reproducible independently of a running benchmark or external reporting service.

**Independent Test**: Run the local report workflow twice in different new output directories with reordered identical selection input. Compare all report bytes and source hashes; inspect plots, offline links and evidence/status tables.

**Acceptance Scenarios**:

1. **Given** unchanged selected inputs, analysis version and renderer environment, **When** the report is generated twice, **Then** all CSV, JSON, PNG, SVG and HTML outputs are byte-identical with source references and analysis identity.
2. **Given** failures, unavailable costs and diagnostic counter resets, **When** the report is viewed, **Then** selected-attempt reliability, dated cost evidence and both-VM diagnostics retain missing values and their scope without automatic performance warnings.
3. **Given** an existing output or an output destination overlapping input evidence, **When** publication is requested, **Then** analysis refuses to overwrite evidence or an existing report; no source file changes.

### Edge Cases

- No eligible comparisons, a single deployment, missing selected providers, incomplete phases and no valid samples.
- Multiple selections or replacements reference the same deployment; retained calibration/day units share a deployment.
- A receiver interval straddles a bin edge, warm-up or gap; native bytes cannot be divided by an invented proportion.
- P95/P99 have insufficient samples; zero baseline makes a relative throughput change undefined; clocks cannot support cross-host alignment.
- Diagnostic gaps/resets, partial DAY tails, source substitution, inconsistent validation results and legacy unavailable acquisition evidence.
- S1/S3 dates differ, scheduled windows are unresolved, optional S2/S4 are absent or selected as previously recorded data.
- Campaign-level reservations are repeated across child manifests; actual bills, source charges, runtime or transfer estimates are absent.
- Paths and labels contain HTML-sensitive characters; renderer metadata or output paths would otherwise introduce nondeterminism.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Analyse explicitly selected local bundles and their saved validation/manifest evidence; recheck identity, materialized integrity and current validation before deriving trusted measurements. Unsupported, corrupt or missing evidence MUST retain findings and unavailable values rather than gain validity from a saved success label.
- **FR-002**: Compute TCP throughput from receiver application bytes and measured elapsed time, excluding omitted intervals; compute RTT statistics from retained underlying samples using the existing linear-quantile, sample-standard-deviation and count rules. Never average precomputed quantiles, substitute sender throughput or infer exact loss from filtered CSV rows; keep native diagnostic counts separate.
- **FR-003**: Bin from source-supported measured timing, raw RTT samples and whole receiver intervals. Use one-minute DAY bins, preserve coverage and gaps, and never prorate unobserved receiver bytes or extrapolate missing time. Initial sustained-load/burst context MUST be descriptive evidence, not an automatic causal finding.
- **FR-004**: Keep acceptance, calibration, fresh short-study and retained DAY modes distinguishable. Calibration trials and DAY bins MUST remain repeated measurements on their deployment; no idle baseline or latency inflation may be invented for either mode.
- **FR-005**: Summarize fresh deployment-level phase metrics with medians, interquartile ranges and individual points. Deduplicate by actual deployment/attempt identity; packets, repeated windows and duplicate selection entries MUST NOT become independent replicates. Preserve slow measurements.
- **FR-006**: Compare providers only within compatible selected scheduled slots/phases and the existing validation eligibility rules. Incomplete peer groups MUST be shown with reasons and MUST NOT silently shrink into a supposedly matched cohort.
- **FR-007**: Compare S1/S3 deployment distributions within provider and declared time-of-day window using absolute RTT differences and relative TCP-throughput changes, with explicit direction, baseline and unavailable zero/missing-baseline treatment. Do not invent pairing between independent S1/S3 deployments.
- **FR-008**: Preserve actual timestamps, UTC study dates, scheduled slot/window identifiers and deviations from concurrent same-week S1/S3 execution. Do not infer missing study windows or generalize an observed week/day into recurring temporal behavior.
- **FR-009**: Include both-VM diagnostic/metadata evidence and gaps/resets beside performance, retaining the existing clock-eligibility rule for aligned one-second views. Missing alignment MUST NOT erase otherwise supported per-host evidence. Add no automatic CPU/steal/byte-ratio performance-warning thresholds.
- **FR-010**: Report reliability over the explicitly selected unique attempts, including failures, partial providers, retry/replacement links and missing phases. Report costs/runtime/transfer only from identified recorded evidence, keeping actual charges, estimates and reservations distinct and shared campaign amounts counted once; missing evidence MUST remain unavailable.
- **FR-011**: Produce normalized CSV and JSON summaries, PNG/SVG time-series and distribution figures and readable offline HTML for one run or a selected comparison. Show statuses, gaps, sample/deployment counts, measurement units, source links and limitations; HTML MUST escape input labels and require no external service or network asset.
- **FR-012**: Record source identifiers/checksums, selection/configuration, analysis revision/dirty identity and relevant numeric/renderer versions. Identical sources, settings, analysis identity and environment MUST yield deterministic output bytes independent of input ordering or output-directory name. Raw input bytes and record identities MUST remain unchanged; publication MUST refuse output/source overlap or replacement of existing output.
- **FR-013**: Prove calculations, cohort boundaries, partial/invalid evidence handling and deterministic report replay with known offline fixtures, including visual/HTML inspection and checks that every consumed source is traceable. No cloud, credentials or benchmark traffic is needed or implied by fake acceptance.
- **FR-014**: Keep this a small local analysis driver over merged validation contracts. Do not add mandatory intermediate data conversion, a platform/service, shared DTO changes, research decisions or a reusable analysis-agent skill. Thesis interpretation/canonical figures remain in Thesis; other agents' implementation and coordinator state remain untouched.

### Key Entities *(include if feature involves data)*

- **Selection**: Explicit saved run inputs/results and optional recorded cost evidence, with a stable content fingerprint.
- **Attempt and deployment**: Source identities distinguishing independent fresh resources, retry/replacement attempts and retained measurement units.
- **Measured bin**: A bounded interval with supported native samples, receiver byte/time evidence, coverage/gaps and source references.
- **Comparison cohort**: Eligible deployment-level observations sharing the declared provider/scenario/slot/phase/window context.
- **Analysis report**: Machine-readable rows, figures and HTML linked to source evidence, validation outcomes and analysis identity.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Known fixtures reproduce every specified numeric summary and bin result, including unequal receiver intervals, raw-sample quantiles and undefined ratios, within the declared calculation precision.
- **SC-002**: Every cohort fixture has the expected independent deployment count and eligibility; no duplicate, acceptance, calibration, retained window or unmatched peer is silently counted as a fresh matched observation.
- **SC-003**: Two replays with reordered identical input and distinct fresh output locations produce identical CSV/JSON/PNG/SVG/HTML bytes under the recorded environment, while all source hashes remain unchanged.
- **SC-004**: Every reported metric, table and figure has resolvable source IDs/checksums and analysis identity; missing evidence and comparison restrictions have explicit reasons.
- **SC-005**: Partial DAY fixtures preserve expected one-minute rows, gaps and supported intervals without idle/inflation values or invented full-day completeness.
- **SC-006**: The local command produces every required format and a readable self-contained HTML report offline, refuses unsafe/existing outputs, and passes the documented focused and credential-free regression checks.

## Assumptions

- The merged F08 implementation supplies validation/parsing/eligibility rules; current code supersedes historical planning-time status text. No new research threshold is introduced by F12.
- UTC window labels come from saved schedules or explicit recorded selection context. Evidence-dependent N and exact study windows are never chosen here.
- S3-minus-S1 and S3-relative-to-S1 are the declared placement-effect directions. Comparisons describe selected deployment distributions, not paired experiments or provider-wide claims.
- Partial and ineligible measurements remain visible as identified descriptive evidence; only appropriately valid/eligible independent units enter corresponding aggregate comparisons.
- Determinism is assessed under a recorded fixed analysis/renderer environment. Cost/diagnostic evidence can be missing without being fabricated, and an absence of paid runs is not a zero-cost result.
