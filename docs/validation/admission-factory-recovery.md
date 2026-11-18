# GCP zero support and Azure reserved-factory recovery repair

14 November 2026. Implementation **a46593d6abceeaee31a098e46cdd48a5b4480ff4**
on `codex/aws-pre-vm-failure-repair`; baseline implementation eb91626, documentation
a770fe0. [Requirements and convergence](../../specs/013-v2-lifecycle/repairs/admission-factory-recovery.md).

The coordinating chat reported GCP admission `support_cost_missing` before any
reservation/call, despite an explicitly priced zero native STOP-template support
line. Azure reserved, then rejected a slightly late UTC/monotonic cap mapping in
its workflow factory before recovery existed. The original private batch,
authority, inputs, provider state and shared ledger remain outside this repair.
Its cleanup and retained eventual-charge liability belong to the coordinating
chat. This source has no live execution authority.

## Changes

GCP may use an explicit zero support amount only when the exact saved complete
cost envelope has the same zero component and matching cost-source path/hash.
The real ledger still reserves the other workflow components and enforces all
financial checks. Other-provider zero, missing, boolean/null, negative,
mismatched, wrong-source and unpriced support remains ineligible.

Reservation activation samples monotonic before UTC, once, and derives every
monotonic time from that original anchor. Azure conservatively clamps its original
monotonic deadline against the original UTC expiry, using monotonic-before-UTC
sampling. The strict live validator is unchanged: no epsilon, cap extension or
fresh twenty minutes is introduced. Synthetic legacy skew is covered without
rewriting the original ledger times.

Before timing checks or workflow-factory execution, orchestration writes a private
selected-provider recovery journal with the original reservation and open cleanup
liability. Azure saves its original cleanup settings, prospective IDs, provider
journal and Terraform input bytes before live preflight. A factory exception now
persists its cause and returns failed provider execution with unknown cleanup,
reservation identity and zero actual calls. CLI finalization retains unavailable
guest commands/timestamps and failed data validity. Factory composition performs
no provider operation; it does not register expiry or claim creation.

If an earlier local-source failure prevents the settings snapshot from existing,
recovery remains durably cleanup-open and reports blocked cleanup. It does not
invent absence or release funding. When the original settings/inputs exist,
cleanup-only reconstruction remains usable after the launch cap/authority/quote
expires, and still requires independent inventory/VM/group/RBAC absence. Missing
journals, changed inputs, blank inventory and residual roles remain blocked.

The affected multi-provider regression also exposed a prior AWS receipt-replay
mismatch. The previous repair added independently verified prospective role and
document targets to cleanup receipts; replay omitted the same targets from its
expected set. Replay now includes the driver's saved `cleanup_safety_targets`;
altering the original target rejects replay. Already acknowledged absence receipts
need no extra provider calls. Provider absence rules and financial liabilities
remain in force.

## Verified local delta

Clean exact a46593d before and after the isolated driver: **246/246 passed**,
zero failures/errors/skips, native macOS x86_64 Python 3.13.7. This includes 21 new
source-shaped admission/factory cases with unpatched financial validation and the
real F11 reserve path, plus two new original-target receipt-replay cases.
Sequential simulated clocks and actual native monotonic reads cover Azure's
conservative cap; the actual CLI/manifest path covers a pre-provider validation
exception; restarted cleanup covers independent absence and retained full cash
liability after expiry.

[Exact source/log/JUnit hashes](admission-factory-recovery-evidence.json).
[Isolated affected-delta driver](check-admission-factory-repair.py).
The unchanged three PowerShell runbook execution cases are explicitly deselected;
no runbook/Terraform root/lock changed. A broader exploratory local invocation
also reached the separate analysis-plot stage, which requires the unavailable
optional Matplotlib dependency. That plotting path and unchanged research-mode
analysis are excluded from this Python repair delta. No passing full baseline or
provider acceptance is claimed.

Local archive `/private/tmp/cnb-admission-factory-a46593d-exact-local.zip`:
SHA-256 `6a2bb8354ca0db4c8a22ec270aa8d516adc92a9fb1598604ef471655e2451cd5`.
CRC and all **9** manifest member hashes independently verified; all five changed
executable source hashes match the current exact source.

- Summary: `4f8824df958a78c6900babf02f5e2c0baef26dd698b1542930d8904948695ebe`
- JUnit: `b9ae5e8d3cae0de462f3764400c22f9ace19bc1fcde5c821e49f1a128cbbe0a2`
- Pytest log: `5b94506c2a8dc293ae171213a2589d221208f830e3434e7d58bf6a2875cf5b55`

## Verified native Linux delta and handoff

Exact **a46593d** passed **246/246** affected cases on Halle Python 3.12.3,
zero failures/errors/skips, with all six driver checks passing and clean source
before/after. The downloaded archive's CRC, eight listed payload hashes, JUnit
counts and five executable hashes were independently verified on the Mac.
Native archive `/private/tmp/cnb-admission-factory-a46593d-native-evidence.zip`:
SHA-256 `656db277914deef0c7abfdd0737036b76a5029779e51d271dc8b4eb6c51f49a4`.

- Summary: `a1a8f89e7bc22c76df8f9ee0792c509d289fea4508da108fe155c0310e0c0f92`
- JUnit: `1646453e7be4bce5e4e2a99e95d592832a8b9dd3ecd29da19943a0933631242a`
- Pytest log: `7fb17bb0bcd137e785bca0e672c8bddce83711a34ba93198105370af459693cd`

A separate isolated copy of exact original **eb91626**, with only the new test
module overlaid, reproduces **six expected regression failures**, zero errors or
skips: GCP positive-cost rejection; sequential/native clock mapping; absent journal
before both factory failure variants; and CLI invalid-input reporting. Its tracked
executable source stayed unchanged. These are intentional baseline failures, not
repair failures. Downloaded archive CRC and all three payload hashes verified:
`/private/tmp/cnb-admission-factory-eb91626-regressions.zip`, SHA-256
`5659e01d003693c4de7d97ee54ffa6a99968526aa44583407eaa513615f2a237`.

Remote source and evidence are confined to the new private directory
`/u/halle/brek/home_at/cnb/saved-plan-native-wsJSS7/admission-factory-rFyY5Aqb`.
The source bundle hash was checked before importing original Git objects against
the separate prior offline clone. The driver used an empty HOME and a fresh
environment without cloud authentication variables; no original execution root,
input, state, ledger or authority was used or modified. The coordinating chat
retains ownership of the actual paid-batch cleanup/billing evidence.

Exact committed source archive:
`/private/tmp/cnb-admission-factory-a46593d-source.tar.gz`, SHA-256
`cae68c3b294efeccd969d05eb1d15cb8e588f5e841d40b2aee5552e50b0746b4`.
Original-object Git bundle:
`/private/tmp/cnb-admission-factory-a46593d.bundle`, SHA-256
`b47fe4b4977bd13f855905a3897cc539ee43ed68a4528cdcc0299ffcbe5f45d2`.
The verified bundle requires eb91626507d0419003a739ecc314dbbf9549fc14 and contains
ref `refs/heads/codex/aws-pre-vm-failure-repair` at exact a46593d. It preserves
original Git objects rather than rewriting signed ancestors.

The isolated affected delta is complete; no remaining bounded offline finding is
known. No unchanged full gate, Terraform/runbook/analysis baseline or live provider
acceptance was rerun. Changed source still needs fresh reviewed source bindings
and exact authority before any separately approved live attempt; the old card
does not authorize a retry or longer review wait.
