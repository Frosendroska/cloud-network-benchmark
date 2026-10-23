# Feature Specification: F08 V2 Native Result Validation

**Feature Branch**: `codex/f08-v2-validation`
**Created**: 2026-10-21
**Status**: Ready for implementation after artifact analysis
**Input**: F08 prompt in Thesis `tracking/IMPLEMENTATION_PROMPTS.md`: native Sockperf/iperf3 parsing, mode-specific validation, calibration selection and concise summaries using merged F01R only.

F08 lets an operator establish what a saved experiment actually measured before it is compared or reported. It retains usable evidence from incomplete attempts and explains missing or invalid data. It does not run measurements, provision resources, change raw files, schedule experiments or interpret thesis results. Plotting and multi-experiment reports belong to F12; lifecycle integration belongs to F09.

## Clarifications

### Session 2026-10-21

No critical ambiguities require human clarification. The authorized coordinator handoff resolves the engineering policies left to Spec Kit by the implementation notes:

- Loaded short-study, acceptance and calibration units require the full requested receiver TCP measured duration and active-probe measured-window coverage of at least 99% of the TCP measured interval. Idle requires the full requested probe interval. Warm-up and omitted intervals never count. These are engineering acceptance assumptions, not measured research findings.
- Actual provider measurement-start skew at most 60 seconds meets the target; greater than 60 through 300 seconds remains eligible for matched-time comparison with a degraded-target timing finding; greater than 300 is ineligible. Missing timestamps cannot establish eligibility. All other comparison prerequisites still apply.
- Missing mandatory evidence yields explicit unavailable/partial/invalid findings as appropriate; an optional F03-local sidecar is never a prerequisite. Existing F01R timing/command records and native artifacts carry evidence. Common N and exact UTC study windows remain evidence-dependent.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Recover trustworthy native metrics (Priority: P1)

An operator reads a saved provider bundle and obtains receiver TCP throughput, RTT statistics and the tool's separate diagnostic counts without changing the sources.

**Why this priority**: Every later validity decision and report depends on correct native parsing.
**Independent Test**: Known small native fixtures reproduce expected samples, interval bytes, elapsed time, quantiles and counts, with before/after source hashes identical.

**Acceptance Scenarios**:

1. **Given** short client and receiver output plus Sockperf samples, **When** metrics are computed, **Then** only measured receiver bytes/time and underlying valid RTT samples contribute; warm-up and omitted intervals do not.
2. **Given** 999, 1,000, 9,999 or 10,000 replies, **When** statistics are produced, **Then** P95 is available only at 1,000 and P99 only at 10,000; median and sample variability follow their stated minimum counts.
3. **Given** a truncated stream, corrupt row, missing counter or counter reset, **When** it is read, **Then** the precise issue and valid surviving intervals remain visible; missing values are never zero and sender throughput never substitutes for receiver evidence.

### User Story 2 - Decide validity and comparison eligibility (Priority: P1)

An operator validates a saved acceptance, short-study, calibration or diagnostic-day attempt and sees execution, data validity, comparison eligibility and cleanup evidence separately.

**Why this priority**: A command can finish while its data or timing is incomplete.
**Independent Test**: Mode fixtures and boundary mutations produce exact findings/statuses for artifacts, settings, identity, timing, generation, skew and cleanup without credentials.

**Acceptance Scenarios**:

1. **Given** a complete correctly identified bundle with matching commands/settings, **When** validation runs, **Then** materialized integrity and mode-specific intervals are checked against the executed configuration.
2. **Given** full requested TCP duration and 99% active measured-probe coverage, **When** a loaded unit is checked, **Then** coverage passes; one lesser boundary fails completeness while usable intervals remain available.
3. **Given** day TCP lasting 86,400 seconds with rotated probes, **When** coverage is checked, **Then** at least 99% RTT time coverage and no TCP restart are required for completeness; gaps/partial intervals remain reported.
4. **Given** starts with 60, 60.001, 300 or 300.001 seconds skew, **When** matched timing is evaluated, **Then** the first meets target, the middle two retain eligibility with a timing finding and the last is ineligible; missing starts yield unavailable eligibility.
5. **Given** slow observations, high CPU/steal or unusual interface bytes, **When** validation runs, **Then** the evidence is retained without automatic performance warnings or exclusion.

### User Story 3 - Select common N and hand off traceable summaries (Priority: P2)

An operator summarizes the twelve calibration trials per cloud and obtains a common stream-count recommendation only when all required evidence is present; later consumers can replay the same validation and metrics.

**Why this priority**: Frozen research settings must follow the specified cross-cloud rule.
**Independent Test**: Three-cloud fixture medians yield the smallest qualifying common N or the explicit N=8 no-common-plateau fallback; incomplete, duplicate or inconsistent evidence cannot freeze N.

**Acceptance Scenarios**:

1. **Given** three valid repetitions for 1/2/4/8 streams in each cloud, **When** selection runs, **Then** each cloud's count median is compared with 95% of its best tested median and the smallest shared qualifying N from 2/4/8 is recommended.
2. **Given** no qualifying common count, **When** complete calibration evidence is summarized, **Then** N=8 is recommended with `plateau_found=false`; missing or invalid trials instead leave the recommendation unavailable.
3. **Given** validation replay at the same supplied evaluation time, **When** machine-readable and text summaries are produced, **Then** results are deterministic, retain source identity/checksums and failure/cleanup details, and do not mutate configs or freeze N automatically.

### Edge Cases

Empty or unsupported native formats; nonfinite/negative RTT and byte/time values; malformed JSONL tail after valid events; missing receiver output; overlapping/duplicate/out-of-order receiver intervals; receiver restarts; absent generation counts; Sockperf-filtered/reordered samples; chunk overlaps/gaps; missing clock mappings or >100 ms clock error; altered/symlink-escaped artifacts; contradictory snapshots/IDs/commands; legacy FLENT bundles; missing provider starts; failed execution with usable data; unresolved cleanup; pending common N/schedule; absent or duplicate calibration trials; no common plateau.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Read native Sockperf CSV and separately reported invocation/chunk counts, iperf3 client JSON and receiver JSON/JSONL directly. Preserve native samples, receiver intervals, source references and parse findings; reject unsupported/legacy formats explicitly.
- **FR-002**: Compute receiver TCP throughput as `8 * received_bytes / measured_elapsed_seconds / 1e9` in decimal Gbit/s, excluding warm-up/omitted intervals. Never substitute sender bytes, interface counters or an average of interval rates; do not double-count stream/sum or overlapping receiver records.
- **FR-003**: Compute RTT in microseconds using the NumPy linear quantile definition over underlying valid measured samples: median with at least one sample; P95 with at least 1,000; P99 with at least 10,000; sample standard deviation with at least two; P95-minus-median only when P95 exists. Missing statistics have explicit reasons. Latency inflation is loaded median minus idle median only within the same valid three-phase deployment; unavailable for calibration/day or absent/invalid baselines.
- **FR-004**: Preserve reported dropped, duplicated and out-of-order reply counts separately with their invocation scope; absent counts remain unavailable. Never infer packet loss or missing-reply percentages from exported row counts. Generation must be at least 95% of the configured 1,000-mps target over the documented reporting interval, using actual generated-message evidence, not reply rows.
- **FR-005**: Validate required/optional/not-expected artifacts according to mode, supplied schedule and declared units; verify closed/retrieved materialized sizes/SHA-256, source snapshots, configuration/schedule/identity, actual versions/build/Git provenance and consistency of command/timing records with saved bytes. Missing/contradictory mandatory evidence must be reported. Derived validation outputs must not be mandatory input to their own validation.
- **FR-006**: Validate actual commands/settings/direction and unit order against the resolved protocol: 64-byte, 1,000-mps, UDP full RTT with monotonic timing; CUBIC, sender zerocopy, receiver A-to-B TCP and requested streams; five-second warm-up, two-second delay from probe test-start notification before TCP; phase/trial pauses, 60/10-second measured modes; continuous 86,400-second day and probe chunks at most 300 seconds without restarting TCP. Process-launch time plus sleep cannot establish active-probe coverage.
- **FR-007**: Establish actual measured intervals from tool timestamps and clock mappings plus F01R timing/command evidence. Exclude warm-up/omitted time and union clipped active intervals without double-counting. Loaded short/acceptance/calibration require full requested receiver TCP duration and at least 99% active-probe measured time over that TCP interval. Idle requires full requested probe interval. Day completeness requires full continuous 86,400-second receiver TCP and at least 99% RTT time coverage. Preserve valid partial intervals and gaps.
- **FR-008**: Preserve the input execution and cleanup outcomes separately from data validity and comparison eligibility. Use explicit machine-readable findings/reasons for unavailable evidence, invalid data and partial valid intervals. Failed execution does not erase metrics; cleanup failure remains visible and prevents acceptance of the bundle as complete. No cleanup side effects are performed here.
- **FR-009**: Evaluate matched-time eligibility only for compatible configuration, scenario, scheduled slot, phase and selected providers with valid data and actual measurement-start timestamps. Skew at most 60 seconds meets target; >60 through 300 seconds is timing-eligible with a degraded-target finding; >300 is ineligible. Missing evidence is unavailable, not assumed matched. Estimated clock error at most 100 ms is separately required for aligned one-second outputs; this does not invalidate RTT timed entirely on A.
- **FR-010**: Compute each provider's median receiver throughput from three valid trials per count 1/2/4/8, retaining the common fixed trial order and retained deployment identity. Recommend the smallest common N in 2/4/8 reaching at least 95% of each cloud's best tested median. Complete evidence with no qualifying N yields N=8 and explicit no-common-plateau evidence; incomplete/duplicate/invalid trials yield no recommendation. Check frozen-N consistency against the saved resolved configuration without inventing N or mutating the configuration.
- **FR-011**: Return machine-readable validation findings and concise readable run summaries with source IDs, hashes, units, modes, valid intervals/gaps, commands/versions/provenance, failures and cleanup. Preserve deterministic replay when inputs and evaluation time are fixed. Keep calibration/day as retained-pair repeated measurements, not fresh deployments, and exclude acceptance/calibration from the research dataset.
- **FR-012**: Keep raw input files immutable and offline validation credential-free. Preserve slow measurements and local diagnostic values/resets/gaps; do not introduce CPU/steal/byte-ratio performance warnings, compulsory Parquet, cloud calls, real traffic, plotting, scheduling, provider-wide conclusions or thesis interpretation. Focused offline fixture, integrity, contract and replay checks are required.

### Key Entities

- **Saved attempt**: F01R identity/mode/config/schedule, declared units/artifacts, execution/cleanup states and evidence for one provider deployment.
- **Native measurement**: Source-linked measured RTT samples, reported counters and receiver byte/time intervals; missing fields remain explicit.
- **Validation result**: Independent data/comparison statuses, stable findings and source-linked metrics; execution and cleanup remain attached evidence.
- **Calibration recommendation**: Three-cloud count medians, tested ratios, selected common N or unavailable reason, plateau outcome and retained source identities.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Known native fixtures reproduce 100% of stated bytes/time, quantile, count and unit expectations, including exact sample-count boundaries, with no source hash changes.
- **SC-002**: Complete fixtures for all four modes are distinguished from corrupt, truncated, missing, reset and partial variants with the expected findings and explicit reasons for every missing value.
- **SC-003**: Completeness and eligibility checks distinguish exact duration, 95% generation, 99% coverage, 60/300-second skew and 100-ms clock boundaries without excluding slow measurements.
- **SC-004**: Complete three-cloud calibration fixtures produce the specified smallest common N or explicit fallback; every incomplete/duplicate/invalid case leaves N unresolved.
- **SC-005**: Fixed-input replay produces identical machine-readable and text results with traceable sources; consumers can recompute bins from native samples/receiver intervals without averaging quantiles.

## Assumptions

- Only merged F01R at `cef838e42fc620f90b5943f5a8d1528ddc0b0724` is a prerequisite; no F03/F04/F11 implementation or local sidecar schema is imported. Required evidence can be unavailable and must be rejected honestly.
- The authorized clarification policies above are declared engineering choices within the source's delegated boundary, not new research results or formal advisor approval.
- Native format support is deliberately tied to recorded Sockperf/iperf3 build identity and documented fixture dialects; unsupported variants get findings rather than guessed fields. Parser grammar is frozen in the feature contract before implementation.
- F08 reads saved evidence and returns results; F09 owns manifest lifecycle/output placement and F12 owns reports/plots/comparisons. N, exact UTC windows and paid admission remain pending until evidence exists.
