# Implementation Plan: F09 V2 lifecycle integration

**Branch**: `codex/f09-v2-lifecycle` | **Date**: 2026-10-23 | **Spec**: [spec.md](spec.md)
**Input**: `specs/013-v2-lifecycle/spec.md`; verified merged base `527451d8697917c795d8dc9224cf8a17947c4311`.

## Summary

Add small controller functions and explicit provider/guest adapters around merged admission, provider, access, measurement, diagnostics, retrieval and validation components. A narrow VM-A guest entry calls the existing A-local runner; the existing standalone diagnostics guest runs on both VMs. Keep final source inventory/retrieval/deletion/validation as one bounded sequence. Preserve original failures and private recovery evidence, with one ledger governing cap/session/launch claims. No shared DTO/schema amendment is required.

## Technical Context

**Language/Version**: Existing Python ≥3.9 API; implementation checks use `/private/tmp/cnb-offline-venv/bin/python` (3.11.13). Ubuntu guest runs the same package revision in a per-deployment Python environment.
**Primary Dependencies**: Existing Pydantic 2/PyYAML; standard library concurrency/subprocess/files; OpenSSH/SCP; provider CLIs/Terraform behind existing CommandRunner. No new mandatory dependency.
**Storage**: Existing local immutable results/snapshots/manifests and private provider recovery/ledger files. Atomic validated updates and exclusive native destinations; no remote state service.
**Testing**: pytest unit/contract/integration with fake runner, guest process/clock and real temporary bytes. No real benchmark traffic.
**Target Platform**: Local controller; prepared Ubuntu A/B; exact provider differences retained. Offline development on macOS does not prove Ubuntu/native/cloud behavior.
**Project Type**: Existing Python library/CLI.
**Performance Goals**: At most three concurrent provider workflows; one retained pair per selected cloud; DAY streams/bounded previews rather than day-sized memory buffers. No arbitrary speed threshold.
**Constraints**: Full-workflow stage fit; immutable original cap; all-provider protection before creation; 60-second stabilization; close →final copy/verify →delete →validate; at most one copy retry; strict saved ValidatorInput.
**Scale/Scope**: One explicitly selected saved row/campaign per invocation; four modes; no auto scheduler/retry engine. Healthy independent P0/P1 invocations may share the ledger/session with separate namespaces.

## Constitution Check

| Principle | Before research | After design | Evidence |
| --- | --- | --- | --- |
| I Small reproducible instrument | Pass | Pass | Ordinary functions, no new platform/dependency; same package on A |
| II Frozen measurement protocol | Pass | Pass | Delegate F03/F04; mode matrix and original common-N proof retained |
| III Immutable/honest provenance | Pass | Pass | Actual closed bytes, supplemental declarations, strict input replay; separate states |
| IV Explicit providers/schedule | Pass | Pass | AWS/GCP drivers, Azure functions; same UTC slots and provider-specific expiry/cleanup |
| V Offline/cleanup safe | Pass | Pass | F11 reserve/claim; independent expiry barrier; failure matrix, unknown-liability block |
| Repository/workflow boundary | Pass | Pass | Only feature artifacts in planning; Thesis read-only; no paid actions; CLI ownership serialized |

No constitution exception or research change. Source-backed runtime/build identity remains evidence-dependent; software preparation does not create a software-freeze approval gate.

## Project Structure

### Documentation (this feature)

`specs/013-v2-lifecycle/{spec.md,plan.md,research.md,data-model.md,quickstart.md,tasks.md,planning-handoff.md,contracts/lifecycle.md,checklists/requirements.md,checklists/lifecycle.md}`.

### Source Code (repository root)

F09 implementation owns:

- `src/cloud_network_benchmark/orchestration.py`: explicit preview/run/cleanup composition and provider concurrency, mode/deadline/state flow.
- `src/cloud_network_benchmark/lifecycle_adapters.py`: narrow existing-provider and SSH/SCP guest preparation/start/stop/closed-inventory adapters; no generic backend registry.
- `src/cloud_network_benchmark/lifecycle_guest.py`: same-revision VM-A entry calling F03, durable result/outcome/control evidence, bounded cancellation; no alternative measurement implementation.
- `src/cloud_network_benchmark/lifecycle_bundle.py`: complete declarations, closed retrieval inventory/retry, post-retrieval acquisition binding, strict input/validation/summary/checksum persistence.
- `src/cloud_network_benchmark/lifecycle_cli.py`: isolated lifecycle arguments/dispatch and explicit execution scope; paid prerequisite failures stay enforced.
- `src/cloud_network_benchmark/manifests.py`: narrow validated atomic evidence update helper for existing fields (reservation/expiry, commands/timings/records, retrieval failure, validation status); retain identity, chronology and parent aggregation.
- New `tests/unit/test_f09_{lifecycle,guest,bundle,cli}.py`, `tests/contract/test_f09_interfaces.py`, `tests/integration/test_f09_lifecycle_{modes,failures,recovery}.py`, `tests/fixtures/f09/{helpers.py,cases.json,README.md}`.
- `docs/validation/f09-v2-lifecycle.md`, narrow user-facing lifecycle section in `README.md`.
- A final thin `src/cloud_network_benchmark/cli.py` hook only after coordinator integrates/releases F12 dispatch ownership; preserve existing/F12 commands. Do not edit analysis/report modules, existing F08 fixtures, DTOs/schemas, provider Terraform or Thesis.

**Structure Decision**: Five small integration modules correspond to controller flow, explicit external adapters, A-local entry, bundle persistence and CLI; use functions/dataclasses, not a new orchestration framework. `lifecycle.py` transition helpers remain reused unchanged. No speculative extension hooks.

## Phase 0: Decisions and interface evidence

[research.md](research.md) resolves remote runner placement, runtime staging, cap/copy policy, partial providers, source inventory, failure semantics and serialized CLI ownership from current code/sources. No unknown technology choice or external research is needed.

## Phase 1: Integration design

[data-model.md](data-model.md) defines local handoffs without new shared models; [contracts/lifecycle.md](contracts/lifecycle.md) defines callable/CLI/guest/bundle/state boundaries. [quickstart.md](quickstart.md) gives exact offline acceptance commands. Tasks are test-first for the requested risky lifecycle boundaries, then composition, then full integrated regression.

All selected providers register/read back expiry before any claim/create. Reserve/prepare fit includes staging/wait/stabilization/closure/copy/cleanup. Provision/prepare/measure healthy providers concurrently; one live controller session is shared explicitly. Failed providers enter their own finalization; joins collect every result, never abandon futures after the first exception.

Close guest clients/diagnostics and stop/flush exact prepared B listeners before inventory. Retrieve both VMs' files only at campaign end. One retry filters only failed items and never overwrites verified destinations. Record failures even when no valid closed inventory can be built; request normal deletion regardless when copy cannot finish within the original allowance. After cleanup, update truthful terminal execution/cleanup evidence, persist strict ValidatorInput, validate/evaluate eligible peers, update only validation status, and seal immutable indexes. Validation findings are data/eligibility evidence, not rewritten tool execution outcomes.

## Complexity Tracking

No violations. Runtime staging is necessary because F03 deliberately runs on A; it reuses the existing package and dependencies rather than a second measurement engine. Local atomic evidence helpers fill existing manifest fields without a schema amendment. Any newly discovered shared-contract issue must be returned to the coordinator for serialized ownership before implementation; never weaken helper/model validation.
