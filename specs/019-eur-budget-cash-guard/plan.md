# Implementation Plan: EUR budget and personal cash safeguards
**Branch**: `codex/eur-budget-cash-guard` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

## Summary
Add a local typed financial guard, exact source/run binding, conservative EUR/cash accounting inside
the existing locked ledger, and a hard paid-dispatch blocker for absent cumulative transfer control.
Preserve USD envelopes, expiry barriers, cleanup and user changes. No live approvals are generated.

## Technical Context
Python3.12+, existing Pydantic2/Decimal/pytest. Controller-local JSON, existing flock/fsync atomic writes.
macOS/Linux; small CLI framework, three providers and concurrent P0/P1 only. No service/dependency added.
Full-cash-only support: credits/reimbursements cannot lower exposure. Maximum reservation exposure
is retained even after final billing; this deliberately overcounts rather than inventing headroom.

## Constitution Check
Pre-research PASS: I provenance retained; II no new infrastructure/protocol; III immutable evidence;
IV explicit provider scope; V EUR/cash/cleanup/offline-only. Post-design PASS: no paid launch possible
until bounded transport exists; explicit recommendation values never become defaults. No exceptions.
Constitution2.1.0 minor expansion follows dated10Nov policy; ratification and all other principles retained.
Temporary sync report reviewed: V expanded, no principles removed, no unresolved placeholders.

## Project Structure
- `src/cloud_network_benchmark/financial_controls.py`: strict card/source/FX/window/cap validator,
  exact binding, conservative commitment/revalidation helpers, explicit unavailable transport capability.
- `admission.py`: optional financial binding on plan; validate before lock; aggregate under same lock;
  durable per-reservation financial snapshot; revalidate before non-cleanup stages and creation claims.
- `lifecycle_runtime.py`: retain runtime/source identity, bind actual code/config/provider resources;
  forbid paid ledger auto-initialization; retain lazy provider factory.
- `campaign_inputs.py`: local financial/transport block before provider identity calls; local-only
  preparation skips all provider APIs and grants no authority. Prepared identities need later approval.
- `tests/unit/test_financial_controls.py`, `tests/integration/test_financial_admission.py`: negative
  matrix, boundaries, shared ledger and recovery. Existing stage tests explicitly isolate new guard
  where their subject is legacy fake provider behavior; new integration tests use real guard.
- `scripts/render_campaign_runbook.py`, generated HTML: caption/date only atop existing user edits.
- `docs/validation/eur-financial-guard.md`: audit, implemented/unresolved matrix and native evidence.

## Implementation Phases
Typed source/card + negative tests; shared financial reservation; runtime/preflight/cleanup boundaries;
renderer correction; affected suite, full credential-free validation where warranted, native if connected;
convergence and selective commit. Research/data model/contracts/quickstart are adjacent.

## Complexity Tracking
None. Conservative blocked functionality replaces a transport redesign or credit reconciliation system.
