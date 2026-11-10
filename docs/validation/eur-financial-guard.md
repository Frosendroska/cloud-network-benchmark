# EUR financial safeguards audit and repair

Authorized offline packet: Thesis `tracking/IMPLEMENTATION_PROMPTS.md`, 10 November2026.
Baseline: c24d27ce7071c0b890d6304f75747a6ac9674db3. Branch: codex/eur-budget-cash-guard.
Policy: [Thesis financial rules](../../../Thesis/tracking/BUDGET_RULES.md).

## Baseline findings, before source changes

Real F11 `BudgetLedger.reserve` rejected absent paid_authorization, but accepted identity-only,
expired(2000), wrong-provider/config/count0 and explicit EUR gross/cash0 evidence. All used14Oct
prices at10Nov with refreshflagfalse. Source/envelope/support validation and USD ledger controls
already existed and are retained. Existing focused F11 tests:59passed.
Synthetic reproduction only: `/private/tmp/cnb-SYNTHETIC-budget-audit.py` and results JSON.
No live approval, ledger, cloud/API/resource or account changes occurred.

SCP counterexample: honest12byte two-VM inventory, four copies including retries wrote2097164bytes,
then returned complete=True. Integrity and one retry are not cumulative transfer enforcement.
Controller restart already restricts normal resume to cleanup/reconciliation; do not describe this
as unrestricted paid restart.

## Implemented and unresolved controls

| Control | Implemented behavior / limitation |
|---|---|
| Human approval | Strict source-card schema; wrapper fields cannot supply authority. Exact config/resolved/runtime/code/template/price/run/provider scope and two-VM resource bindings; unique child-run count; aware validity window through cleanup. Manual card ownership is the trust boundary, not cryptographic human authentication. |
| Price/FX | Source hashes re-read; quote age<=24h, future checks rejected; checked date agrees with each priced envelope. Canonical sourced USD-to-EUR FX capture must agree with card, including timestamp/rate/validity. Official URLs record the human review; offline code cannot establish that an online price was actually official/current. |
| EUR gross | Confirmed whole-thesis maximum300EUR; explicit working/protected/provider/stage/run/fixed-batch caps, no recommendation defaults. External thesis liabilities, tax/uncertainty components, FX/card fee rate and fixed fees included; liabilities round up, caps down. Existing stricter USD250/300/50 checks remain additional controls, not EUR labels. |
| Personal cash | Separate explicit ceiling and stored cash exposure. Full gross counts as cash; credit and reimbursement offsets are unsupported and rejected. This conservatively forgoes credit benefits and does not assume reimbursement was received. |
| Shared history | Existing atomic flock/fsync ledger; concurrent P0/P1 reservations share EUR/cash accounting. Original maximum exposures and external high-water marks remain after cleanup/reconciliation. Legacy unconverted history blocks new financial admission; it is not reset/migrated automatically. This can overcount final charges and intentionally block later work. |
| New stages | Approval/source/freshness and shared headroom revalidation before non-cleanup stage/creation claim. Generic old paid records cannot grant new creation. |
| Cleanup | No new quote/approval requirements in constructor/inspect/reconcile or shared provider settings. Existing original-scoped cleanup/recovery remains available after expiry, stale prices, missing card or exhausted launch allowance. |
| Cumulative transfer | **Unresolved, live blocker:** opaque SCP has no bounded byte stream, durable pre-debit for both VMs/attempts or sourced allowance for protocol/network overhead. `cumulative_transfer_guard_not_implemented` blocks real paid admission and provider identity preflight. No configuration/attestation flag enables it. Inventory/size/hash/time/retry checks are retained, not mislabelled as caps. |
| Preparation/presentation | Local preparation skips provider APIs and grants no authority; exact prepared identities need later review. Hero10Nov states EUR300 all-in and proposed EUR250/EUR50 recommendations, with policy link. Existing user Markdown/renderer changes preserved. |

## Validation

- New financial tests and affected campaign/F11/recovery tests:172passed in34.61s on macOS Python3.12.14.
- Broader fake provider run: one controller-interrupt timing case failed while concurrent work was in
  progress; focused rerun passed in4.79s. Native full validation must cover it before completion.
- Renderer regenerated; `python3 scripts/render_campaign_runbook.py --check` passed.
- New financial tests use actual guard and isolated synthetic temp files. Historical fake provider-stage
  tests explicitly mock the new financial boundary to continue testing their provider lifecycle subject;
  they do not establish financial readiness. Production contains no bypass flag.
- Native/full validation pending final committed source transfer. No cloud/provider acceptance claimed.

## Actual launch prerequisites

Do not create a paid approval or new live ledger from these fixtures. Cumulative transfer enforcement
must first be implemented/reviewed; removing the blocker alone is not a repair. Then supply actual
human-approved exact prepared entries/counts/resource scope, gross/working/protected/provider/stage/
run/batch and personal unreimbursed-cash caps, source-backed reconciled external liabilities, current
prices/FX/tax/fees and original cleanup/lifetime/expiry evidence. No numerical personal cash allowance
has been authorized. Old liabilities need explicit conversion/reconciliation without lost history.
Credits/reimbursement cannot lower this implementation's conservative cash total. Provider acceptance,
permissions, independent expiry and real billable-resource absence remain separate authorized stages.
No bank-charge guarantee follows from offline accounting or provider budget alerts.
