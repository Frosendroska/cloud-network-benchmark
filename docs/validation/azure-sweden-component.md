# Azure Sweden component repair — 14 November 2026

Implementation baseline: corrected saved-plan code `4da3cc9`, documentation
`14a79dd`. Bounded feature artifact:
[Sweden repair](../../specs/008-v2-azure-provider/repairs/sweden-component.md).

The Sweden exception accepts only EXP-901 / Azure-only / same_zone / acceptance /
failure_check / zone 1. Existing Frankfurt/London scenarios remain supported.
The original private draft now validates unchanged: SHA-256
`eb0356fd74ec9da1032904ef143cb05c752d238e2ea63e7e531a1ea2991fa03a`.
It retains three ten-second phases and a 1,200-second cap. Exact candidate image:
`Canonical:ubuntu-24_04-lts:server:24.04.202610250`.

## Implementation

Selected region flows into pair variables, expiry group/account/runbook and
Marketplace image lookup. Authenticated account/runbook reads must match it;
actual VM reads must match ID, region, zone, shape, admin user and image version.
The admin user now follows the configured connection user (the previous
`azureuser` / hardcoded `ubuntu` mismatch would prevent SSH). Terraform's old
ubuntu default remains for direct-root inputs and old recovery journals.

Both roots use the corrected shared saved-plan boundary. Exact addresses,
fresh create-only actions, provider/subscription, identity references, permissions,
VM/image/disk/network/firewall scope and input variables are checked before the
operator point. Full private JSON/binary, source/input/state hashes and the
receipt remain under a new 0700 attempt-local review directory with 0600 files.
PowerShell source now participates in technical and financial source binding.
Apply retains the original explicit state and identical saved binary, without
replanning or var-file apply. No-op/update/delete/import/replacement/extra scope
is rejected. Computed relationship IDs require the planned exact resource
reference; known IDs must match the owned ARM namespace.

Azure runtime accepts optional integer `plan_review_timeout_seconds` in 0..600,
default 0 (no receipt means no apply). The command deadline and spare time before
the retained workflow bound both cap review/apply. Use the existing
[saved-plan operator recipe](saved-terraform-plan-review.md), separately for
expiry and pair. Technical inspection does not grant financial authority.

Cleanup uses original privately hashed inputs/state after restart and a bounded
recovery budget (`cleanup_timeout_seconds`, or the existing command timeout if
unspecified). It requires neither a new plan receipt nor current launch/quote
validity. It still verifies pair absence before removing expiry and blocks new
launches on residual/unknown cleanup. Legacy un-hashed journals use their original
strict comparison, allowing the new admin input to be absent in old files.

## Validation so far

Local Python 3.13.7 focused delta: **246 passed, three PowerShell cases skipped**
because macOS has no PowerShell. Separate affected Azure default-factory check
passed (one case), including runtime wait propagation and cleanup reconciliation.
The focused delta includes the 61 unchanged shared-boundary regressions and ten
Terraform command/cache checks; no broad AWS/GCP baseline was repeated.

Local JUnit SHA-256: `18b71b9485141012bda4e0775d50d033568f3f84e802caedbfd356040e60aa97`; factory JUnit: `2d899f89b8354cc0c4b3d8fef3bcf6360bf402c607556c196c1fdd9b88824fe7`.

Native Linux checks remain pending at this source checkpoint. No live provider
plan/apply, expiry registration, VM creation, real cloud failure test, resources,
ledger, reservation or new spending authority was created. Thesis and frozen
AWS/GCP launch inputs were not changed.

## Remaining handoff boundary

Native evidence is distinct from actual provider acceptance. Before a separate
supervised Azure component, complete disk-operation/Automation runtime liability
bounds, workflow/recovery/export allowances, fresh EUR FX/tax/card/reserves and
an exact source/identity/window/cash approval. No total or ceiling is invented.
The expired AWS/GCP EUR 1.50 authority does not cover Azure or this source.
Quota/image/provider-registration preparation does not prove capacity, execution,
expiry, guest tooling/SSH, controller loss or full billable-resource deletion.
