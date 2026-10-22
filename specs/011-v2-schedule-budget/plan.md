# Implementation Plan: F11 V2 schedule and budget admission

**Branch**: `codex/f11-v2-schedule-budget` | **Date**: 2026-10-22 | **Spec**: [spec.md](spec.md)

**Input**: `/private/tmp/cnb-f11/specs/011-v2-schedule-budget/spec.md`

## Summary

Add a small offline scheduling/admission library over merged F01R value contracts. Two modules parse/inspect saved CSV entries and serialize complete-workflow reservations in one local JSON ledger. F09 later calls them before provider expiry registration and creation. No application code is changed by this planning stage; no provider implementation or shared DTO/schema is amended by F11.

## Technical Context

**Language/Version**: Existing Python >=3.9 project; validation interpreter Python 3.11 in `/private/tmp/cnb-offline-venv`.

**Primary Dependencies**: Existing Pydantic 2/PyYAML; standard-library csv, decimal, hashlib, datetime, json, pathlib, tempfile, os and fcntl. No new package/service dependency.

**Storage**: One explicit controller-local private ledger JSON file and adjacent advisory lock; caller supplies the shared path. Atomic replace/fsync under a kernel-released exclusive lock; no remote state/database.

**Testing**: Existing pytest/jsonschema, materialized small fixtures, fake clocks and subprocesses solely for concurrent local ledger tests.

**Target Platform**: Existing macOS/Linux local Python controller; POSIX local filesystem locking. No distributed/controller failover support.

**Project Type**: Internal library with an offline `python -m cloud_network_benchmark.admission` preview entry point; no mutation/launch CLI is added.

**Performance Goals**: Deterministic bounded processing of the registered 63 primary experiments, nine conditional replacement allowances and explicit deferred entries. Fake time covers DAY without sleeping. No external command or automatic wait loop.

**Constraints**: Frozen contracts, $250 working/$50 protected/$300 total, original mode caps, earliest provider trigger, source-backed inputs, no paid side effects.

**Scale/Scope**: 28 concurrent paired primary slots (56 fresh experiments); independent calibration/DAY reservations and scoped validation cases. Single shared local ledger across separate invocations.

## Constitution Check

| Gate | Pre-design | Post-design evidence |
| --- | --- | --- |
| I Traceability | Pass | Exact ScheduleEntry source/row bytes, config resolution and source-scope hashes; missing evidence unavailable. |
| II Small architecture | Pass | Two modules, local files, existing DTOs; no cloud command, service, database, queue or duplicated YAML plan. |
| III Honest evidence | Pass | Original rows/charge events retained; no invented workbook revision/prices or overwritten results. |
| IV Provider semantics | Pass | Per-provider supplied lead/cost/action policy and merged AWS support handoff; independent P0/P1 identities and four-VM demand. |
| V Offline cleanup safety | Pass | Reserve before registration/create; unchanged caps, earliest-trigger fit, durable creation claim and global cleanup block. |
| Workflow/source boundary | Pass | Required sources read at Thesis `d9d1051e2c908f0665ea957ab4b0db9047fb6fbf`; only assigned artifacts written. F09/F10/paid gates remain separate. |

No constitutional exception. Evidence-dependent N, UTC windows, refreshed prices and optional admission are explicit inputs, not unresolved implementation questions.

## Project Structure

### Documentation (this feature)

```text
specs/011-v2-schedule-budget/
  spec.md
  plan.md
  research.md
  data-model.md
  contracts/schedule-admission.md
  quickstart.md
  tasks.md
  checklists/requirements.md
  checklists/admission-safety.md
  planning-handoff.md
```

### Source Code (implementation ownership)

```text
src/cloud_network_benchmark/scheduling.py
src/cloud_network_benchmark/admission.py
tests/fixtures/f11/
tests/unit/test_f11_schedule.py
tests/unit/test_f11_budget.py
tests/contract/test_f11_admission.py
tests/integration/test_f11_ledger.py
docs/f11-schedule-budget.md
```

F11 owns exactly these new implementation/test/doc paths and its feature artifacts. `contracts/**`, `config.py`, `cli.py`, `lifecycle.py`, providers, Terraform, manifests, shared fixtures, Thesis and coordinator state are read-only. Use `resolve_campaign(..., schedule_entry=entry)` and `paths.atomic_write_json` where its actual durability is sufficient; add parent-directory fsync in the F11 module rather than changing the shared writer. Feature-local types stay in admission.py. Return genuinely required shared amendments to the coordinator before consumption.

## Design and Implementation Sequence

### Phase 0 — Research/clarification complete

[research.md](research.md) records source scope, monetary funding moves, activation/copy/skew policy, local locking/restart decisions and the merged AWS inclusion handoff. No unknown technology choice remains, so this single assigned planner does not dispatch technology research agents.

### Phase 1 — Design complete

[data-model.md](data-model.md) defines local records/states; [contracts/schedule-admission.md](contracts/schedule-admission.md) declares APIs and the F09/provider handoff. [quickstart.md](quickstart.md) separates existing commands from future feature checks. Checklists review requirements quality; implementation task markers remain unchecked.

### Phase 2 — Implementation after handoff

1. Add scoped/synthetic fixtures and strict local input/accounting types.
2. Parse/inspect schedules over existing config resolution; retain pending N/time.
3. Reserve/reconcile under one locked durable transaction with explicit funding moves.
4. Add stage/creation, cleanup/replacement and actual-start skew guards.
5. Add offline preview, focused checks and concise docs. Actual provider registration/provisioning/retrieval integration remains F09.

## Safety and Accounting Rules

- Decimal internally, decimal strings in local JSON, finite/nonnegative/non-boolean; round calculated reservation liabilities upward to $0.0001. Preserve source rounded envelopes without claiming unrounded invoices.
- Initial source buckets: primary $100.40, replacements $1.95, tax $20.47, uncertainty $25.00 = $147.82. $50 remains separately protected. These are October 14-input forecasts. Per-run allocations move from remaining buckets into active ceilings, never on top of unchanged buckets.
- Working commitment = incurred/estimated liability + sum(active ceiling minus already allocated incurred liability, floored at zero) + all unallocated remaining buckets. Require ≤250; adding protected 50 must be ≤300. Record overruns truthfully and block admission, never clip costs.
- Provider expiry/cleanup support receives explicit sourced funding from uncertainty into the candidate ceiling; tax moves from its own pool. Already included ancillary/export amounts are not added twice. Missing support costs cannot be zero by default. A refreshed review updates remaining estimates while preserving historical sources/liabilities.
- One ledger covers the whole study/accounting scope across invocations. Source changes require reconciliation, not new initialization. Use locking, strict revalidation, private files and fsync/atomic replace. Persist exact input fingerprint and stable entry/campaign/provider creation claims before returning grants.
- Concurrent independent invocations share one explicit live controller_session_id. Healthy active work in that session is allowed and accounted; an unresolved earlier session blocks new launches as uncertain cleanup. A new controller recovery uses a new ID, with no automatic reactivation. The adjacent lock file also carries the durable initialized-scope marker: its existence does not mean the kernel lock is held, and neither a missing ledger nor missing marker may be silently recreated.
- Cap activates once at accepted durable reservation before expiry registration. Live enforcement uses monotonic time while recording actual UTC separately. Restart keeps liability and rejects automatic create/resume because the previous monotonic origin cannot be safely reconstructed. F09 cleanup/reconciliation remains possible; no repeat or new cap.
- Effective normal-work deadline = `min(cap_end, each provider_trigger)`. AWS trigger is `cap_end - 360s` from merged F05. Other providers supply identified policy inputs, not unfinished imports. All registration/setup/wait/measurement/retrieval/cleanup allowances must fit; re-check remaining work before every stage.
- Slot means measurement-start target. Fit `max(preparation_seconds, scheduled_start - activation_now, 0) + measurement_seconds + retrieval_seconds + cleanup_seconds` before the effective deadline; registration is included in preparation. Activation after target is missed without catch-up. Actual delayed starts of already activated attempts remain evidence, never rewritten slots.
- Supplied bounds must cover the fixed mode, including warm-up/notification buffers/pauses and 60-second stabilization. Reject bounds shorter than protocol minimums. F11 validates minimums from mode settings; it does not execute/synthesize commands.
- Creation guard additionally requires all selected providers' registered expiry evidence bound to exact child run/deployment, correct stop/deallocate action, exact trigger and registration-before-create. Reservation linkage comes from the matching ExpiryRequest/handoff because ExpiryEvidence has no reservation ID. One durable grant per provider prevents duplicate replay; uncertainty stays open for cleanup even if creation may not have happened.

## Acceptance Checks

Meaningful requested checks cover CSV bytes/secret rejection; 28+28 mapping/four-VM demand; pending N/windows/prices; 60/300-second skew; cost inclusion/no double count; exact budget/cap/earlier-trigger boundaries; missing/wrong expiry; concurrent commits/idempotency; interrupted/corrupt persistence/restart; replacement limits/missed slots; bounded-copy handoff and unresolved cleanup. Tests materialize local files and assert no external runner invocation.

Use explicit synthetic provider-policy inputs for all-cloud tests without depending on unfinished Azure/GCP modules. Read/hash the merged AWS cost fixture as actual repository data. Synthetic policies or successful reservations do not prove permissions, quota, timing, billing or cloud cleanup.

## Complexity Tracking

No violations/platforms. Local locking is required by simultaneous P0/P1 invocations and interruption-safe accounting; an in-memory counter would lose reservations.
