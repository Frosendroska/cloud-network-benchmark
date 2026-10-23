# Implementation Plan: Source-bound acquisition evidence

**Branch**: `codex/f01r-acquisition-evidence` | **Date**: 2026-10-22 | **Spec**: [spec.md](spec.md)

**Input**: `/private/tmp/cnb-acquisition-evidence/specs/016-acquisition-evidence/spec.md`

## Summary

Add narrowly scoped observational patches to the existing source builds, a small raw-source reference contract, and F03 production/replay of source-bound acquisition windows. Sockperf exports sender/receiver loop-enabled and native filter boundaries; iperf exports actual native statistics origins/endpoints. Keep native outputs and argv unchanged. Store observations in preserved stderr logs, close/copy/hash them after measurement, and derive intervals from verified bytes rather than declared DTO values. Missing proof remains unavailable. F08 integration follows this merged amendment separately.

## Technical Context

**Language/Version**: Existing Python >=3.9/Pydantic 2; minimal C/C++ patches against the two inspected upstream releases.

**Primary Dependencies**: Existing source-build/bootstrap, native tools, command/process boundaries and SHA-256; no new runtime package, service or benchmark.

**Storage**: Existing VM-local native/log files and post-measurement result bundle; additive prefixed observation lines in stderr, never modified native CSV/JSON/JSONL.

**Testing**: pytest contract/unit/integration checks with materialized fake logs, fake clocks and commands; source patch apply/build inspection without running traffic where the Linux toolchain is available.

**Target Platform**: Source-built Ubuntu 24.04 amd64 tools on A/B; credential-free controller development.

**Project Type**: Small local CLI/library instrument.

**Performance Goals**: Constant observation state per Sockperf chunk and streaming receiver processing. No per-probe disk writes or whole-day sample/window lists. Observation overhead is present and unquantified; do not claim equivalence.

**Constraints**: No research/command/workload change; no real benchmark traffic during offline checks; preserve native files and source/license notices. Unsupported versions/builds/clock modes remain unknown.

**Scale/Scope**: One serialized contract/build/F03 amendment, all four modes, one continuous receiver session/day, <=300-second Sockperf invocations. No new orchestrator or external tracing system.

## Constitution Check

| Principle | Before research | After design |
| --- | --- | --- |
| I Traceability | Require source/run/build binding | Pass: raw bytes, modified-build identity, boot/session/command linkage |
| II Small architecture/protocol | Retain existing builds/tools | Pass: additive observations only; unchanged algorithm/CLI/traffic |
| III Immutable/honest evidence | Do not replace evidence with assertions | Pass: native files unchanged, observations in stderr, unknowns retained |
| IV Provider explicit | No provider behavior changed | Pass: local evidence; provider identities remain distinct |
| V Offline first | No cloud/SSH/traffic authorization | Pass: fake boundaries/static source checks; paid acceptance separate |

The exact patches must be reviewed against research.md before implementation acceptance. Any required change to timing, probe cadence, omit, native statistics, networking or duration beyond observation is a stop condition and returns to the coordinator. No source prohibits additive source-build observations; source/build provenance is already required. This does not establish observational equivalence or advisor approval.

## Project Structure

```text
specs/016-acquisition-evidence/
  spec.md plan.md research.md data-model.md quickstart.md tasks.md
  checklists/requirements.md contracts/acquisition-evidence.md
src/cloud_network_benchmark/
  acquisition.py
  contracts/measurements.py contracts/artifacts.py contracts/validation.py
  contracts/__init__.py contracts/schema.py
  bootstrap.py measurement.py
  tool_patches/sockperf-3.10-acquisition.patch
  tool_patches/iperf-3.17.1-acquisition.patch
tests/
  fixtures/acquisition/
  contract/test_acquisition_contract.py
  unit/test_acquisition.py unit/test_bootstrap.py unit/test_measurement_timing.py
  integration/test_acquisition_producer.py
docs/validation/acquisition-evidence.md
```

**Structure Decision**: Reuse bootstrap and measurement; add one ordinary parsing module with streaming functions. The implementer owns the listed files, existing affected focused tests, package-data inclusion in pyproject.toml, and generated schemas under `specs/003-v2-shared-contracts/contracts/` only when models change. Shared contracts/artifacts.py and contracts/validation.py declare/validate the existing per-tool stderr sources, including dynamic day chunks, without changing F08 runtime. The planner owns only `specs/016-acquisition-evidence/`; local `.specify/feature.json` is ignored per-worktree tool state. Other worktrees and Thesis remain untouched.

## Phase 0 — Research and clarification

Read AGENTS routes and the actual specify/clarify/plan/tasks/analyze skills. Inspect merged F03 producers/F01R contracts and official pinned tool sources. research.md records control-flow support, immutable source IDs, alternatives and limits. Ordinary engineering choices are resolved; no research decision is invented. Existing F08 task text is read-only problem evidence, not a runtime dependency.

## Phase 1 — Design and serialization

1. Shared F01R amendment: optional raw acquisition evidence on MeasurementTiming, default unavailable for old bundles. Reuse ArtifactRecord and ClockMapping. Deterministic schema changes remain in the canonical export location.
2. F02R amendment: package reviewed patches, verify upstream/patch bytes before applying without fuzz, observe installed binary/compiler/configuration and explicit capability; invalidate old build caches on identity mismatch. Version strings alone do not establish semantics.
3. F03 amendment: parse preserved observation bytes through acquisition.py, bind invocation/session and boot/clock domain, expose a streaming source-window iterator. Derive conservative windows only with complete corroboration; keep unavailable evidence for uninstrumented builds.
4. Merge/converge this complete producer amendment before consumers update. Coordinator assigns minimal F08 follow-up: verify/read sources through the merged helper, use the same support for coverage and RTT selection, preserve thresholds, finish valid four-mode/eligible-peer replay. This feature does not edit native.py/validation.py/calibration.py/summaries.py or F08 tasks.

## Acceptance and stage gates

Require contract/schema round trips, patch applicability, source inspection, observed build/cache identity, unchanged commands, four-mode producer replay, deterministic immutable bytes and hostile substitutions. See quickstart.md for commands/not-run limits. Without a suitable offline Linux compiler, record build checking as not run and do not call source-built capability proved. Convergence must inspect actual hooks, not accept hand-authored intervals or fake-only capability claims. No real traffic is authorized to remedy a check gap.

## Serialized convergence ownership

The coordinator assigned T021–T025 to the same serialized implementer and authorized only the
small F02R/F03 plumbing required to materialize the exact `CNB_BUILD_V1` bytes captured from the
remote bootstrap sidecar as a closed, hashed build-identity `ArtifactRecord`. The per-attempt local
copy is tied to the original captured bytes; it does not synthesize provenance from a tool version
or an in-memory build object. F03 returns those build records alongside its measurement result so
the downstream bundle can retain the same references. Legacy builds without sidecar bytes remain
unknown.

The source-replay integration fixtures are owned by this feature's existing test harness. The
repair adds realistic client/server JSON receiver records and binds native artifacts to the exact
measurement-unit action and artifact path, preventing neighboring action-name collisions. The
fixtures do not claim a real patched binary or cloud run. The approved workload, CLI, warm-up,
native files, coverage denominator, and separate generation and alignment thresholds remain
unchanged. No F08 runtime consumer or other feature worktree is part of this amendment.

The serialized T026–T029 convergence also owns the bounded retrieval test-fixture repair: new
observation artifacts are matched by exact command artifact ID and path, avoiding prefix collisions
without changing production matching rules. Native iperf support consumes either the existing
pretty-printed client JSON artifact or the closed/retrieved B receiver JSONL artifact; it does not
alter measurement flags. Session-wide proof is preflighted before publishing windows, while only a
genuinely failed/truncated tail can preserve a correlated closed prefix. Day-scale replay and
coverage require chronological source iterators and retain bounded state. Corrupt native candidates
are isolated per unit so valid evidence from another unit remains available.

## Complexity Tracking

No constitution violations. Two additive upstream observation patches are necessary because current native sources expose filtered samples and relative/statistical spans rather than source-bound enabled windows. A DTO alone cannot close the gap. No additional agent/platform abstraction is justified.
