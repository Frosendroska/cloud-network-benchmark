# Azure Sweden component repair — 14 November 2026

Tested implementation: **`0b70033e8c49ade91637f0770adc6e7c76fd50ec`** on
`codex/azure-sweden-component`. Main implementation `266bd48`; native Terraform
correction `ad91123`; computed-identity correction `0b70033`. Starting baseline:
corrected saved-plan code `4da3cc9`, documentation `14a79dd`. Bounded feature artifact:
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

## Verified affected validation

| Checkpoint | Exact scope and result |
| --- | --- |
| Local 266bd48 | Python 3.13.7: 246 passed / three PowerShell skips, plus one passing Azure factory case |
| Native 266bd48 | Python 3.12.3: 249 affected cases + one Azure factory case = **250 passed, zero failures/errors/skips**; actual builtin-only same-plan/original-state proof passed |
| Native ad91123 | Both Azure roots fmt/init with readonly locks/validate; **eight Terraform mock test cases** (including one expected rejection) and **six scope-plan checks** passed; 34/34 driver checks, explicitly reusing the previous Python/builtin results |
| Local/native 0b70033 | **63/63 computed-ID/scope cases** passed on both hosts; six unchanged native scope artifacts replayed against final code; final native driver 11/11 |

There are **260 distinct affected Python case identities** across the native
checkpoints: the final 63 cases include 53 reruns and ten new cases. This is
313 case executions, not a new full baseline. The unchanged AWS/GCP baseline
was reused. Nine current executable hashes, all checkpoint source-clean
observations, CRC and **191 archive file hashes** were independently checked
on the Mac. The original private Sweden draft validates without modification.

The first native driver is preserved as **incomplete**, despite its 250 passing
Python cases: Terraform found a set-indexing error in the new test assertion,
implicit-default mock values, and a nested mock-identity shape error. The native
correction uses `one(admin_ssh_key)`, declares public/private IPv4 and disabled IP
forwarding explicitly, and fixes the test-only nested identity override. Scope
checks remained strict. Final convergence also found computed-ID references too
broad: the final code requires exact static ID attributes, per-role dynamic
references, one NIC attachment, and correct legacy PPG/peering targets. Ten new
cases cover those boundaries.

Native managed counts: expiry 4; Sweden pair 12; Frankfurt same-zone 12;
cross-zone 12; placement 13; inter-region 16. Native mock output omits normal
`terraform show`'s format/variables/configuration envelope. The scope adapter
supplies that envelope and explicit computed-ID mocks while preserving native
`resource_changes`. The unknown-reference branch is unit-tested; **actual Azure
provider plan/show integration remains unproved**. The builtin check applies
only a local `terraform_data` fixture, proving command/binary/state integration;
it grants no provider-apply evidence or paid authority.

Verified archive:
`/private/tmp/cnb-azure-sweden-final-native-evidence.zip` (6,524,857 bytes), SHA-256
`c19c8a585b067b4f88c208c180de6dea56077186f6cf373c42f910ab0d5c3fda`.
It is retained remotely under
`/u/halle/brek/home_at/cnb/saved-plan-native-wsJSS7/` alongside all three isolated
source/evidence checkpoints. Source, logs, JUnit and proof hashes are recorded in
[the machine-readable report](azure-sweden-component-evidence.json).
Final native summary SHA-256:
`e35cf50978e049bc862ce592e408bfd4430ba767f725b3bd084c8ddb31ec79cc`;
final 63-case JUnit:
`e9de9073ad87cb2546b41536c1b0ea479ee1d57589b340c5c0786331db09c373`.

No cloud environment/credentials were loaded, no real provider apply/expiry
registration, VM/IAM, ledger/reservation or paid authority was created. Thesis,
frozen AWS/GCP inputs/source and the four pre-existing user edits remain outside
this repair.

## Remaining handoff boundary

Native evidence is distinct from actual provider acceptance. Before a separate
supervised Azure component, complete disk-operation/Automation runtime liability
bounds, workflow/recovery/export allowances, fresh EUR FX/tax/card/reserves and
an exact source/identity/window/cash approval. No total or ceiling is invented.
The expired AWS/GCP EUR 1.50 authority does not cover Azure or this source.
Quota/image/provider-registration preparation does not prove capacity, execution,
expiry, guest tooling/SSH, controller loss or full billable-resource deletion.
