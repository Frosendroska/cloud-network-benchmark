# Implementation Plan: F03 V2 measurement runner

**Branch**: `codex/f03-v2-measurement` | **Date**: 2026-10-21 | **Spec**: [spec.md](spec.md)
**Input**: `specs/005-v2-measurement-runner/spec.md`
**Dependency/base**: merged F01R `cef838e42fc620f90b5943f5a8d1528ddc0b0724` only.

## Summary

Add a small A-local measurement module for the resolved V2 units, and a local concurrent subprocess adapter needed to observe Sockperf while TCP runs. Reuse `CommandRequest`, `CommandResult`, `CommandEvidence`, `MeasurementTiming`, `Evidence`, `IdentityContext` and `ArtifactLayout`. Do not extend the synchronous shared `CommandRunner` or introduce a second acquisition schema. Preserve original native files and unknown timing; validation/metrics remain F08's responsibility. F09 later invokes this module on A and composes the merged siblings.

## Technical Context

**Language/Version**: Python >=3.9, matching `pyproject.toml`; acceptance interpreter `/private/tmp/cnb-offline-venv/bin/python`.
**Primary Dependencies**: Standard library process/selectors/time/path/json tools, existing Pydantic v2 contracts; no new dependency required.
**Storage**: Exclusive local files below the caller's provider-attempt result root; native CSV/JSON/JSONL and existing command/timing evidence paths.
**Testing**: Existing pytest layout; scripted processes and monotonic/UTC clocks with real temporary files, no real traffic or external execution.
**Target Platform**: Ubuntu 24.04 amd64 VM A for execution; credential-free Python tests on controller/macOS. B listeners and streaming output are prepared externally.
**Project Type**: Small library component for the existing CLI/controller; no standalone service or new user command.
**Performance Goals**: Incremental local file writes, bounded in-memory tail for notification detection, one TCP process through simulated 86400 measured seconds, no real-time sleeps in tests.
**Constraints**: No SSH/clouds, sibling imports, server lifecycle, metrics/statistics, shared-contract mutation, data overwrite, inferred precision or credentials in durable evidence.
**Scale/Scope**: Three short units, twelve retained trials, or one continuous TCP unit plus locally rotated probes per supplied provider/deployment.

## Constitution Check

| Principle | Before research | After design | Evidence |
| --- | --- | --- | --- |
| I Traceability | Pass | Pass | Supplied identities, unit/deployment linkage, original config contract, durable commands/native files; F09 owns manifest composition |
| II Small architecture/protocol | Pass | Pass | Two modules, existing contracts, exact native settings; no service or extra campaign |
| III Immutable/honest results | Pass | Pass | Exclusive files, incremental writes, unavailable evidence, observed overlap/gaps; no validity claim |
| IV Provider explicit | Pass | Pass | Consume one resolved provider/private B endpoint; no provider policy hidden or substituted |
| V Offline/cleanup safe | Pass | Pass | Injectable process/clock boundary, failures retained, owned-client shutdown; caller retains cloud cleanup responsibility |

No constitution exception is required. The 99% short coverage choice fills the expressly delegated engineering policy and preserves the fixed day, generation and clock rules.

## Project Structure

### Documentation (this feature)

```text
specs/005-v2-measurement-runner/
  spec.md
  plan.md
  research.md
  data-model.md
  quickstart.md
  contracts/measurement-runner.md
  checklists/requirements.md
  checklists/measurement-quality.md
  tasks.md
```

### Source Code (repository root; implementer ownership)

```text
src/cloud_network_benchmark/measurement.py
src/cloud_network_benchmark/measurement_process.py     # local process mechanics only
tests/contract/test_measurement_contract.py
tests/unit/test_measurement_commands.py
tests/unit/test_measurement_process.py
tests/unit/test_measurement_timing.py
tests/unit/test_measurement_modes.py
tests/integration/test_measurement_runner.py
tests/fixtures/f03/                                  # synthetic/source-attributed small fixtures only
docs/validation/f03-v2-measurement.md
```

All listed source/test/validation files are F03-owned additions.

**Structure Decision**: `measurement.py` owns ordinary mode/argv/timing/evidence functions and result records; `measurement_process.py` owns start/read/poll/terminate/reap and a real local `Popen` adapter. One writer owns all F03 source files. No edits to `contracts/`, `config.py`, `cli.py`, bootstrap/readiness/retrieval, provider drivers, F08, sibling fixtures or historical specs. If a genuinely mandatory field proves unavailable in the shared contract, report its exact semantic gap to the coordinator before changing it.

## Design and implementation sequence

1. Validate the resolved observation and explicit runtime context, ensure identity agreement and safe unused destinations; freeze client argv and output routes with contract tests.
2. Build the minimal injected concurrent process/clock boundary and exclusive streaming files. Observe the current invocation's Sockperf `Starting test...` line even across partial reads, preserve it, then wait two monotonic seconds. A delayed/buffered line supplies a conservative gate, not an exact traffic-start timestamp.
3. Implement short/acceptance ordering and observed timing projection. Native timing extraction is limited to evidence needed for windows; no RTT quantiles, throughput calculations or N selection. If native fields cannot locate an exact measured boundary, leave it unavailable. Use the verified native receiver duration/omission and process lifetime to bracket every possible measured interval, then conservatively bound minimum probe coverage as specified in research.md. This yields an executable production path without treating process start or coarse connection time as the actual measurement start. Unknown uncertainty/semantics stays unavailable.
4. Implement calibration by consuming F01R's twelve canonical units without re-expanding or retuning their order. Reuse loaded-unit execution.
5. Implement one continuous day TCP client plus bounded local probe rotation, partial final chunks, gaps and failure handling. Stream output to disk; never accumulate the day in `CommandResult.stdout`.
6. Exercise materialized artifacts and failure/cancellation paths against all modes, then run F03 and existing regression checks. F08 independently evaluates native/timing evidence under the recorded thresholds; F09 later handles remote invocation, retrieval/cleanup and manifests.

## Acceptance checks

The exact intended commands, file lists and current execution status are in [quickstart.md](quickstart.md). Required groups: exact argv; event buffering/notification/deadline; warm-up/omission/coverage with unknowns; every mode/order; day single-process continuity; exclusive-file materialization/redaction; failure/cancellation/reap; existing full regression and `git diff --check`. No Terraform checks are relevant because F03 owns no Terraform changes. Planning is not evidence that these implementation tests passed.
