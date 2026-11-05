# Implementation Plan: Remote campaign driver

**Branch**: `codex/campaign-driver` | **Date**: 2026-10-31 | **Spec**: [spec.md](spec.md)

## Summary

Add a bounded file-journal campaign dispatcher around existing initialization, schedule, lifecycle, F11 admission, collection and cleanup. Run it under one systemd service with two concurrent WEEK calls; keep local report analysis outside the runner. Generate private runtime/source-bound artifacts from a single settings file and existing configs. Extend exact cost-row selection minimally so all observations share one frozen all-stage cost source and ledger.

## Technical Context

**Language/Version**: Python 3.12 deployment; investigate advertised 3.9 import compatibility.  
**Primary Dependencies**: Existing pydantic/PyYAML, standard library subprocess/concurrency/fcntl; systemd, Terraform, provider CLIs, OpenSSH externally.  
**Storage**: Atomic JSON file journal and existing file-locked BudgetLedger, private recovery directories, immutable native results.  
**Testing**: pytest fake clocks/providers and real ledger locks; Linux service validation where available.  
**Target Platform**: Single Ubuntu 24.04 persistent runner, controller outside measured path.  
**Project Type**: Existing CLI extension, no new platform.  
**Performance Goals**: Dispatch exactly 56 observations, two at each slot; stream DAY exports/hashes without reading entire bundles into memory.  
**Constraints**: $300 total/$50 protected; caps 1200s short/acceptance, 1800s calibration, 87600s DAY; no resources or credentials in offline tests.  
**Scale/Scope**: One user, four explicit stages, 56 primary observations, at most two active lifecycle calls.

## Constitution Check

Before and after design: PASS by design. Retain existing provenance/expiry/admission/cleanup interfaces; no protocol changes or advisor-approval inference. Shared ledger protects both calls and stages. No cloud calls authorized. No object store, database, AI runtime, automatic outcome-based configuration or paid repeat. All native evidence preserved, private state excluded from export. Empirical acceptance remains a separate gate.

## Project Structure

### Documentation (this feature)

`spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/campaign.md`, `quickstart.md`, `tasks.md`, `checklists/requirements.md`.

### Source Code (repository root)

- `src/cloud_network_benchmark/campaign*.py`: dispatcher, input generation, journal, export/CLI with ordinary functions.
- `src/cloud_network_benchmark/cli.py`: route campaign command.
- `src/cloud_network_benchmark/admission.py`, `lifecycle_runtime.py`: compatible exact-run envelope selection, source-verified acceptance-scope mapping and configuration cost binding from a common all-stage cost source.
- `src/cloud_network_benchmark/providers/aws.py`, `providers/gcp.py`: external dated expiry price identity and private Terraform data directories; use PATH-safe runtime tool defaults.
- `plans/`, operator-settings example, `deploy/` service and short VM guide: stage entry points, real values pending.
- `tests/*/*campaign*.py`: scenario and real-contract regression tests.

**Structure Decision**: Reuse existing package, lifecycle CLI and contract models; avoid parallel scheduling/admission frameworks.

## Phases and ownership

1. Planner owns only `specs/017-campaign-driver/*`; reconciles current sources and publishes public interfaces.
2. Coordinator owns `campaign_inputs.py`, narrow shared admission/runtime compatibility fixes, plans/settings/service/developer/operator docs and packaging compatibility correction if necessary.
3. Implementer owns other `campaign*.py` plus root CLI wiring; consumes input generator contract. It must not edit planner artifacts or tests during independent test authoring without coordination.
4. Checker owns campaign tests and convergence findings. Application repairs return to the owning writer.
5. Coordinator integrates and validates exact final revision. No paid cloud validation or Thesis writes.

See [contracts/campaign.md](contracts/campaign.md) for function boundary and stable data fields. Implementer and coordinator must reconcile final callable shape before integration; no two writers per file.

## Complexity Tracking

No constitutional violations. The source envelope extension is required by existing single-source ledger identity, not an independent budget bypass. Journal and systemd are the explicitly requested minimal durable execution boundary.
