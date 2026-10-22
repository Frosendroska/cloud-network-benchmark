# Implementation Plan: F04 V2 local diagnostics

**Branch**: `codex/f04-v2-diagnostics` | **Date**: 2026-10-22 | **Spec**: [spec.md](spec.md)
**Input**: `specs/006-v2-local-diagnostics/spec.md`
**Base**: `7a70a3139c5ee9efa1f8ea13c3f6de40e9048121` (merged F01R/F02R/F03)

## Summary

Add a small local Linux collector and a typed controller adapter. The guest module uses only Python's standard library and writes one diagnostic JSONL, one metadata JSON and one clock JSONL per VM. The adapter reuses existing `DiagnosticSample`, `VmMetadata`, `ClockMapping`, `ArtifactInput` and evidence models unchanged. Rooted Linux fixture trees and fake clocks prove arithmetic, metadata truthfulness, missing/reset/gap handling and bounded operation. No new runtime service or shared contract is needed.

## Technical Context

**Language/Version**: Existing Python >=3.9 contract; Ubuntu 24.04 guest Python 3.12. Guest script must run standalone without package/Pydantic installation.
**Primary Dependencies**: Guest standard library only; controller existing Pydantic and F01R contracts. No dependency changes.
**Storage**: Exclusive local UTF-8 JSON/JSONL files; no remote copying during collection.
**Testing**: Existing pytest/jsonschema; fixture files and injected file/command/clock observations.
**Target Platform**: Ubuntu Linux guests; credential-free fixture tests on controller macOS/Linux.
**Project Type**: Library adapter plus directly executable guest module, not a new controller CLI.
**Performance Goals**: One monotonic scheduled read/second, streaming writes and bounded retained memory for 86,400 seconds; no real-day waits in tests. No unmeasured probe-overhead claim.
**Constraints**: Both endpoints, explicit benchmark NIC, actual intervals, unknown evidence preserved, <=100 ms mapping-pair error rule; no shared schema edits or imports from unfinished siblings.
**Scale/Scope**: Two VMs/provider/deployment; same collector in all four modes, spanning their caller-supplied durations without owning mode orchestration.

## Constitution Check

| Principle | Before research | After design |
| --- | --- | --- |
| I Traceability | Pass: deployment/role retained; metadata seed uses actual provider/build evidence | Pass: canonical paths, unchanged typed wire records and explicit clock artifact IDs |
| II Small architecture | Pass: two local modules; no telemetry/agent/service | Pass: standalone guest process; F09 owns lifecycle/transport |
| III Honest immutable results | Pass: zero/missing/reset/gap distinct | Pass: exclusive files, per-read UTC/monotonic mappings, partial closure; no retrieval claim |
| IV Provider explicit | Pass: limits/image/acceleration have identified sources | Pass: no provider SDK/metadata endpoint; observed driver/VF truth, no equivalence assumption |
| V Offline/cleanup safe | Pass: rooted fixtures/fake time/commands | Pass: no traffic or cloud calls; caller retains cleanup responsibility after failures |

No violations or complexity exceptions. Actual guest/runtime acceptance remains separate from offline proof.

## Project Structure

### Documentation (this feature)

```text
specs/006-v2-local-diagnostics/
  spec.md  plan.md  research.md  data-model.md  quickstart.md  tasks.md
  contracts/local-diagnostics.md
  checklists/requirements.md  checklists/diagnostic-quality.md
```

Analysis is read-only and returned to the coordinator; no application changes occur in the planner stage.

### Source Code (repository root)

```text
src/cloud_network_benchmark/diagnostics_guest.py  # standalone stdlib collection/parsing
src/cloud_network_benchmark/diagnostics.py        # typed input/output adapter and eligibility
tests/fixtures/f04/                             # small Linux/chrony/provider/build fixtures
tests/unit/test_diagnostics.py
tests/unit/test_diagnostics_clock.py
tests/contract/test_diagnostics_contract.py
tests/integration/test_diagnostics_collection.py
docs/validation/f04-local-diagnostics.md
```

**Structure Decision**: Guest code owns parsing once; controller imports it and validates its wire outputs, avoiding duplicated arithmetic. Guest code has no relative/package imports, so a copied file is directly executable by `python3`. A direct execution smoke test must clear PYTHONPATH and run the copied file's `--help` outside the repository. Deployment/copy/start/stop composition is deferred to F09, rather than adding SSH or process orchestration here.

## Ownership and interfaces

Planner owns only this feature's specification artifacts. Luna owns exactly the two new source modules, four new test modules, `tests/fixtures/f04/`, this feature's task completion/evidence updates and `docs/validation/f04-local-diagnostics.md`. Other workers are active in other worktrees; do not revert their work. Do not edit shared contracts/schema exports, F02R bootstrap/access/retrieval, F03 measurement modules, existing configs, global CLI, Thesis or coordinator state. A required shared amendment must be coordinated before consumption.

Public interfaces and wire formats are frozen in [contracts/local-diagnostics.md](contracts/local-diagnostics.md); field constraints and arithmetic are in [data-model.md](data-model.md). No new provider API or mandatory guest package is introduced. Missing `chronyc` or unreadable VF/driver evidence yields explicit unavailable facts.

## Integration handoff

- F09 stages the standalone collector and validated nonsecret context per guest; chooses each benchmark NIC; starts both collectors before the executed mode; stops/awaits closure afterward within the original lifetime cap. Duration passed to the guest must fit the caller's remaining cap; host monotonic numbers must not be passed as guest deadlines.
- Canonical metadata/diagnostic declarations already exist in `ArtifactLayout`; reuse their IDs. F04 additionally declares `vm_a_clock` -> `diagnostics/vm_a-clock.jsonl` and `vm_b_clock` -> `diagnostics/vm_b-clock.jsonl`, producer matching role, kind `clock_mapping`, required for a started F04 collector. Shared `ArtifactLayout` is unchanged. Coordinator authorized these additive artifacts on 2026-10-22.
- F09 combines these references with the existing native inventory, handles unavailable files/closure, retrieves only after measurement, verifies size/hash and projects relevant clock mappings into timing evidence. F04 never marks files retrieved/verified.
- F08/F12 clock-sidecar consumption, interval coverage and plot integration belong to their owning integration follow-up. Mapping-pair clock eligibility is necessary evidence, not certification that an entire plot has coverage or absence of discontinuities.
- No collector closes benchmark/server files, changes measurement order, reserves resources, configures clocks or makes cleanup decisions.

## Delivery and acceptance

Implement setup/foundation, US1 sampling, US2 static identity, US3 clocks, then cross-cutting failure/schema/integration checks. Keep stream loops bounded in memory; retain no whole-day sample list. Focused tests must exercise actual writes/flush/closure and runtime/schema validation, not only fake call counts.

Exact commands and expected outcomes are in [quickstart.md](quickstart.md). Required evidence is focused tests, full offline regression, unchanged shared-schema export check, compile checks and whitespace diff check. Terraform checks are not relevant because no Terraform files are owned. No command claiming guest/cloud acceptance is authorized. Implementation then goes to the configured Sol Medium convergence stage; planner artifact checks do not establish implementation completion.
