# Bounded F05/F09 repair: failure before VM creation

14 November 2026. Baseline ccb8a7e (src/terraform identical to previously tested
0b70033). Assigned by the execution chat after a separately supervised AWS attempt.
Implementation and credential-free validation only; no launch, paid retry,
financial-cap/source reconciliation, active-state or Thesis changes.

## Specification and reviewed clarifications

The execution chat reports the original generated namespace has length **90**:
`cnb-20261114T125059Z-exp-900-91f75a46-aws-20261114T125059Z-exp-900-91f75a46-aws-deployment`.
Safety Terraform rejected its <=80 input before apply; empty-state rollback
succeeded. Provider protection failed, but orchestration left execution initialized;
finalization then tried assigning validation to an initialized manifest. CLI hid
real calls behind invalid_lifecycle_request/external_calls=0. Cleanup skipped
independent regional inventory and could not reconcile absence before VM creation.
These are actual reported failures, distinct from the offline reproductions here.

Required behavior:

- R1: deterministic AWS namespace <=80, preserving unchanged short names and adding
  a 24-hex identity digest for long names. Use the same function for provider
  inputs, F11 admission/financial namespace bindings and recovery paths. Preserve
  Terraform's limits; do not truncate run/deployment IDs or ownership tags.
- R2: original long namespace journals/requests/vars/state remain usable for
  cleanup. Never create a replacement namespace when recovering an existing one.
  Missing original journals and ambiguous old/new journals block recovery.
- R3: protection/claim failure or batch abort ends in failed execution, with the
  primary provider cause retained separately from cleanup and validation. Keep
  the initialized-manifest validation invariant. Persist strict failed bundle
  validation without fabricating guest commands or measurement timestamps.
- R4: CLI reports actual command/copy dispatch count on success, failure and
  cleanup; post-cleanup projection errors are secondary failed-run diagnostics,
  not invalid input or zero external calls. A clock injection seam mirrors the
  existing cleanup test seam; it is not a runtime option or authorization bypass.
- R5: cleanup authenticates the expected AWS account, independently inventories
  all 12 original run/deployment-tagged regional resource categories, and requires
  exact role NoSuchEntity/document InvalidDocument responses. Neither an empty
  Terraform state nor creation_may_have_started=false establishes absence.
- R6: when safety apply/output was partial/uncertain, destroy only the original
  persisted vars/state, then verify expected role/document lookup targets. Derive
  prospective names from original inputs and the existing Terraform naming rule;
  label them expected targets, not fabricated created resources. Lost expiry
  execution identity remains blocked and preserves support resources.
- R7: F11 reconciliation records these independent expected target ARNs after
  verified absence, closes cleanup only, and retains conservative reservation/
  billing liability. No zero, release, reset, new approval, credential or funding
  change. Cleanup does not require fresh launch quotes/approval or a plan receipt.

## Plan and artifact review

Read AGENTS, constitution 2.1.0, current design/DECISIONS/implementation notes,
implementation packet and BUDGET_RULES. One writer on codex/aws-pre-vm-failure-repair;
no delegation. Preserve PM/user edits and the original private execution source,
inputs, state, authority, recovery journal and ledger. Execution chat owns actual
cleanup/reconciliation and supplies its own independent closure evidence.

Owned code: providers/aws.py, lifecycle_runtime/cleanup/projection/cli,
orchestration and one private saved-plan result artifact; affected tests and implementation documentation. No Terraform
limit, research protocol, quota, financial control or cloud resource changes.

Reviewed checks: normal initialize_campaign IDs, collision/maximum-component
boundaries, old namespace recovery and ambiguity/missing originals; complete,
denied/truncated/wrong-account/present/uncertain safety inventories; real
orchestration through failed finalization and F11 absence reconciliation; CLI
secondary projection failure and actual dispatch counts; private failed-plan stdout/stderr; retained provider/
manifest/initialization regressions. Native checks use only affected Python cases
and both AWS roots with the new bounded and original rejected namespace inputs.

Constitution I/III: original identity and honest failure/provenance; II/IV: narrow
AWS/lifecycle paths; V: independent absence, original cleanup and retained liability.
No source conflict or research clarification is introduced.

## Tasks and convergence

- [x] PV1 Reproduce the three reported boundaries with normal generated IDs.
- [x] PV2 Centralize bounded naming and retain original recovery identity.
- [x] PV3 Finalize failed execution honestly and count actual CLI dispatches.
- [x] PV4 Require independently authenticated scope and support-resource absence.
- [x] PV5 Focused local regressions: 129 failure/provider cases + 76 reporting,
  manifest/init and selected factory cases, then 61 shared saved-plan regressions
  plus 17 new-case reruns after adding private plan-result retention: 266 distinct
  passing cases across local checkpoints, zero failures/errors/skips.
- [x] PV6 Credential-free native affected delta and unchanged Terraform limits.
- [x] PV7 Verify artifacts/hashes, converge and provide exact commit handoff.

No full unchanged baseline, cloud acceptance or paid readiness is claimed.

## Final convergence

Exact implementation eb91626507d0419003a739ecc314dbbf9549fc14 passed all 266
native affected Python cases and 15 driver checks, zero failures/errors/skips.
Both AWS roots preserve their constraints/locks, accept the normal generated
bounded namespace, and reject the original 90-character input: four mock test
cases, plus actual native exact-scope checks (safety 3 / pair 14 managed addresses).
Mac readback verified archive CRC, all 34 manifest hashes, JUnit/counts/logs,
clean native source and seven current executable hashes. Initial signed-merge
export identity mismatch stopped before tests and was corrected with original
commit metadata before exact-source validation; its diagnostic remains retained.

R1/R2: shared bounded helper reaches F11/provider/recovery, original long namespace
cleanup retains saved paths/bytes and blocks missing/ambiguous journals. R3/R4:
real initialized-ID orchestration reaches strict failed validation without guest
timestamps, preserves typed provider cause and private failed-plan output, reports
actual dispatches even when projection fails. R5/R6: authenticated 12-category
regional inventory, exact IAM/SSM absence, partial apply/output cleanup and unknown
execution blocking all covered. R7: original F11 cleanup reconciliation closes
absence only and retains liability; no production financial guard was changed.

No remaining bounded offline implementation finding. Existing live cleanup,
reconciliation and PM tracking remain with the execution chat. A future live
attempt requires separately reviewed new source bindings and exact authority;
this repair does not use the active private batch, rerun a cloud, release a
liability or establish real provider/guest/controller-failure acceptance.

See [validation and exact hashes](../../../docs/validation/pre-vm-failure-repair.md).
