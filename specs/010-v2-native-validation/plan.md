# Implementation Plan: F08 V2 Native Result Validation

**Branch**: `codex/f08-v2-validation` | **Date**: 2026-10-21 | **Spec**: [spec.md](spec.md)
**Input**: `specs/010-v2-native-validation/spec.md`
**Base**: merged F01R `cef838e42fc620f90b5943f5a8d1528ddc0b0724`

## Summary

Add four small local modules for native parsing/statistics, saved-bundle validation, common-N selection and readable summaries. Reuse F01R `ValidatorInput`, `ValidationResult`, `ValidationFinding`, artifact path/hash helpers, evidence, command/timing, diagnostics and metadata models unchanged. Public function and return-value semantics are frozen in [contracts/native-validation.md](contracts/native-validation.md). F09 calls the validator and persists outputs; F12 consumes parsed samples/intervals and validation results. No sibling runner, collector, scheduler or sidecar imports are required.

## Technical Context

**Language/Version**: Existing Python >=3.9 project; run with the configured offline venv.
**Primary Dependencies**: Existing Pydantic/PyYAML; stdlib csv/json/hashlib/statistics/dataclasses/math. Implement exactly NumPy `method='linear'` semantics in a short local function; no new runtime dependency for interpolation.
**Storage**: Immutable local native CSV/JSON/JSONL/logs and F01R manifests/snapshots. Returned derived dictionaries use existing evidence wrappers.
**Testing**: Existing pytest with focused unit, contract and integration fixtures; no actual benchmarks.
**Target Platform**: Local Python controller on macOS/Linux reading Linux-produced saved data.
**Project Type**: Library functions integrated by later F09; no new CLI required in F08.
**Performance Goals**: Read receiver JSONL incrementally; parse Sockperf per artifact/chunk and retain sample-level access for F12. Do not concatenate a full day into one mandatory in-memory table or introduce a data platform.
**Constraints**: Offline, no secrets/credentials/traffic/cloud commands; raw immutability; deterministic ordering; native dialects declared, not guessed; unknown mandatory evidence fails honestly.
**Scale/Scope**: Four modes, two roles, AWS/Azure/GCP; one continuous day with <=300-second probe chunks or twelve calibration trials per provider. Keep N/windows pending until evidence exists.

## Constitution Check

Gate before Phase 0: PASS. Gate after Phase 1: PASS.

| Principle | Design evidence |
| --- | --- |
| I Traceability | Every metric/source retains experiment/run/observation/attempt/deployment/provider/unit IDs and source hashes; config/schedule/Git checks are explicit. |
| II Small architecture | Four ordinary modules; existing contracts; no services, intermediate format, cloud or runner implementation. Native receiver throughput and full RTT preserved. |
| III Immutable and honest results | Input hashes verified; no writes by parsers/validator; separate execution, data/comparison and cleanup; partial intervals/gaps/counts preserved. Thresholds explicitly declared. |
| IV Provider explicit | Provider identity/scenario and matched slots remain explicit; calibration uses each cloud's medians and one common N. No network equivalence claims. |
| V Offline first | Fixture-only parsing/validation/replay, corruption and failure injection. No paid acceptance, provisioning or real traffic. Cleanup evidence checked but no cleanup performed. |
| Workflow/boundaries | Only F08 artifacts changed in planning. Implementation ownership below is exclusive; shared contract changes require coordinator serialization. Thesis remains read-only. |

## Project Structure

### Documentation (this feature)

```text
specs/010-v2-native-validation/
  spec.md
  plan.md
  research.md
  data-model.md
  quickstart.md
  contracts/native-validation.md
  checklists/requirements.md
  checklists/native-validation.md
  analysis.md
  tasks.md
```

### Source Code (repository root)

```text
src/cloud_network_benchmark/
  native.py          # Native dialects, per-source sample/interval records, statistics
  validation.py      # F01R ValidatorInput -> ValidationResult, comparison timing
  calibration.py     # Validated three-cloud trial medians and recommendation
  summaries.py       # Deterministic readable summary, no raw writes

tests/
  fixtures/f08/      # Small non-secret native files, expected vectors, input factory data
  unit/test_native.py
  unit/test_validation.py
  unit/test_calibration.py
  unit/test_summaries.py
  contract/test_native_validation_interface.py
  integration/test_native_validation_replay.py
```

**Structure Decision**: Flat modules match the small existing package. F08 owns only these new source/test paths and its spec directory. No edits to `contracts/`, schemas, runner/bootstrap/diagnostics/admission/providers/CLI, historical specs or Thesis. F09 owns persistence/manifest lifecycle; F12 owns CSV/JSON analysis tables, binning/plots/HTML and deployment comparisons. Existing contract/helper code is read-only. Any actual shared mismatch is returned to the coordinator before an amendment or consumption.

## Phase 0: Research and engineering decisions

Read governing sources at canonical Thesis path and record their revision in [research.md](research.md). Resolve the delegated short-coverage/skew choices using the coordinator-authorized policy. Verify actual native output grammar from primary source/build examples. Local quantile interpolation matches the prescribed NumPy linear method with known vectors. All engineering unknowns resolved; no research choice altered, no extra research campaign or agent needed.

## Phase 1: Design and contracts

Define native source/sample/receiver interval records and metrics payload in [data-model.md](data-model.md), with public functions and supported grammar in [contracts/native-validation.md](contracts/native-validation.md). Define null/unavailable handling, deterministic findings/status reduction, source association, timing/coverage and comparison context. [quickstart.md](quickstart.md) separates current runnable planning checks from planned implementation validation. No shared DTO amendment is needed.

## Acceptance checks and handoff

Artifact review covers all FR/SC mappings, requirement checklists, paths, numerical boundaries and constitution compliance. Implementation must prove native fixtures with hand-calculated expected values, exact boundary cases, integrity/symlink rejection, per-mode validation and deterministic replay; then run existing full regression suite. Native source grammar fixtures must resemble actual outputs, including preamble, omitted markers, event sessions and truncated tails. Preserve private logs; return exact commands, exit results and head to coordinator. F10, paid acceptance and real source-build capability remain outside this stage.

## Complexity Tracking

No constitution violations or additional abstractions. Stdlib linear interpolation avoids a new package solely for quantiles; existing evidence/status contracts avoid a parallel schema framework.
