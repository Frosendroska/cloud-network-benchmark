# Operator runbook: accounts → acceptance → DAY → WEEK

Updated 13 November 2026. **Financial authority is the user-confirmed €300 all-in thesis ceiling; see the mandatory gate below.** Run collection on one remote Ubuntu VM; analyse on your laptop. Driver PR #25 is merged and [offline-tested](validation/campaign-driver.md). The latest native halle strict offline gate passes at `e77fa72` (20/20 checks, 1,718 tests, zero failures/skips; evidence independently verified); northern d83d271 now passes its required affected native validation (22/22 checks, 281 Python cases, 13 mocked Terraform plans; zero failures/skips and unchanged source independently verified). Real cloud acceptance remains pending. Commands with `--execute` create billable resources: run them only after the named review/authorization. Replace `ACTUAL_*` and `YOUR_*` with real values. No AI session is needed during measurement.

Open the [visual HTML runbook](campaign-runner.html) for a saved checklist, setup controls, copyable commands and stage navigation. This Markdown file remains the operational source. After editing it, run `python3 scripts/render_campaign_runbook.py`; use `--check` to detect a stale HTML copy.

## Immediate execution plan — 13 November

**Approval received; launch blocked before resource creation:** in “Run supervised AWS and GCP lifecycle tests”, the user answered “yes i approve, run the testse” to the frozen-card question. Approved ceilings are €0.75 AWS + €0.75 GCP = €1.50 combined and full personal cash, with start window **13 November 21:15–22:30 UTC (22:15–23:30 Berlin)** and latest normal cleanup 22:50 UTC. The affirmative reply was treated as confirmation of the question's €0 other thesis commitments and independent console recovery access. The actual message, card bindings and interpretation are preserved privately at `~/cnb/private/north-launch-draft-lkt741w_/approvals/user-approval-receipt-20261113.json`; this receipt is not an executable FinancialAuthorization.

**Concrete source/procedure mismatch:** the approved execution handoff requires inspection of an actual saved provider plan before apply. Frozen d83d271 instead calls `terraform apply -auto-approve` directly for AWS safety/pair (`providers/aws.py`, lines 697/385) and GCP protection/pair (`providers/gcp.py`, line 821). The command runner has no saved-plan inspection hook. No paid command was issued; the authority source remains DRAFT_NOT_AUTHORIZATION and the shared ledger is absent. Preserve frozen identities/source. Resolve this narrow mismatch and review/rebind any changed executable source before launch; do not bypass admission or manually apply Terraform. A later launch remains constrained by the approved window.

Halle access and frozen config/runtime/cost/native-chain/financial-entry digests match. Prelaunch read-only CLI evidence at `~/cnb/private/north-launch-draft-lkt741w_/prelaunch-readonly` confirms the intended AWS account and empty AWS Stockholm instances/volumes/allocated addresses and GCP project instances/disks/addresses. No lifecycle, expiry, export or cleanup acceptance has run. Use Sol High or lower, no browser/web tools and no duplicate operator.

After resolving the mismatch within valid approval: one AWS Stockholm EXP-900 attempt → inspect/export/hash/size and independently verify full cleanup → one GCP Finland EXP-902 attempt. Each uses two selected VMs, three ten-second phases, a 1,200-second whole-workflow cap and a 1-GiB cumulative result-payload allowance. No paid retry or implicit DAY/WEEK progression. The [frozen card](../../Thesis/tracking/EXECUTION_CHECKLIST.md#frozen-two-provider-test-card--draft-not-authorized) retains original inputs and provenance; the approval receipt records the later actual decision.

Before DAY, finish remaining registered all-cloud same-zone/placement and failure/expiry/recovery acceptance, then retained-pair calibration/common N and measured storage/export review. Component tests do not establish those passes. Quote and approve DAY separately; no silent provider/geography or protocol change. [Concise plan and current blockers](../../Thesis/tracking/EXECUTION_CHECKLIST.md#immediate-test-plan-and-operating-constraints--13-november).

## Northern preparation and Sweden quota — 13 November

**Azure Sweden case 2611130050003915 is submitted and now Closed.** An authenticated support-case read confirms SWEDENCENTRAL DSv5 limit 16; a separate live quota read now shows **16 DSv5 and 26 total regional vCPUs, usage zero**. Germany West Central case 2611130050002591 remains separately recorded; its status was not refreshed. No Azure VM ran.

Current AWS Stockholm identity, 16-vCPU quota, empty instances/volumes/addresses, offerings/zones and pinned AMI reads pass. GCP Finland has N2 quota 200/0 and global CPU 32/0; instances/disks/addresses/resource policies are empty and selected shape/image reads pass. The user requests preparation of **EXP-900 AWS eu-north-1a m7i.xlarge** and **EXP-902 GCP europe-north1-a n2-standard-4**, sequential single supervised two-VM attempts: same-zone, no placement, three ten-second phases, 32-GiB disks and one public IPv4 each, 1,200-second whole-workflow cap. This is not main-study geography adoption, an Azure run or completed all-cloud acceptance.

The earlier regional-contract and multiline-HCL failures are resolved in committed d83d271. Passing native evidence is retained at `/u/halle/brek/home_at/cnb/evidence/north-native-final-AFBLgx29`; all 22 check logs and JUnit hashes were independently verified. The unchanged e77fa72 full baseline (20/20, 1,718 cases, 112 required identities) plus the affected northern delta are the required offline evidence; do not describe 281 cases as a new full suite. The original failed 4fe4f5f evidence is preserved. Neither provider has yet passed a real lifecycle.

Official-price cost draft: full-cap compute/disks/IP about $0.149 per provider; including proposed export/expiry/residual/tax/FX/card/contingency reserves, **€0.52 AWS / €0.54 GCP**. **€0.75 each (€1.50 combined) is only a proposed review ceiling**; no paid approval, live ledger or reservation exists. No credits are used to reduce conservative personal-cash exposure. The proposed aggregate 1-GiB payload plus 16-MiB separately priced overhead is not measured output or a bank/wire-byte guarantee. Exact reviewed source/config/runtime, current quote/usage/controller /32, liabilities and personal-cash/window approval must precede any launch. Scope changes require repricing. Evidence and all assumptions: [13 November preparation](../../Thesis/tracking/EXECUTION_CHECKLIST.md#northern-test-preparation-and-sweden-case-confirmed--13-november). No cloud resources or account settings changed. Earlier dated descriptions below retain prior snapshots.

## Native transfer validation and next review — 12 November

Committed transfer fix **572bbe1** was validated on halle: **1,704 Python cases passed, 14 failed, zero skipped**. All **18 new transfer-boundary cases passed**. The gate stopped at pytest; the other **19 canonical offline checks passed separately**, including Terraform validation/mocked plans, schema and formatting. Overall strict gate remains **failed**; no cloud/provider acceptance is established.

The 14 failures are older integration fake runners lacking the new `run_bounded_copy` method, rejected by `financial_transfer_transport_unavailable`. The existing **Finish bounded result transfer** chat has received the exact failures/evidence and is correcting those adapters while retaining production enforcement. This is a narrow continuation of the original repair, not a sudo/install-rights problem or an extra experiment. Original failed evidence is preserved; isolated source stayed unchanged and normal halle checkout remains c24d27c. [Native evidence and verified archive](../../Thesis/tracking/EXECUTION_CHECKLIST.md#requested-native-validation--12-november).

Next: validate the narrow repair, deploy/preview the reviewed source while preserving private/user state, refresh the existing GCP case's complete EUR cost and personal-cash ceiling, and obtain exact approval. Then supervise the selected two-VM short case through result/hash/size verification, deletion and independent absence checks. No cloud resources, account changes, live budget ledger or spending approval are part of this offline validation. M04 remains Active; M05 Not started. Earlier dated descriptions below preserve the preceding baseline.

## Committed EUR repair and next test — 11 November

The budget audit/repair is committed at **2804674** on `codex/eur-budget-cash-guard` (code through ae8c2a1). Full Linux gate at f5073d6: 20/20, 1,699 cases, zero failures/skips. Native arithmetic delta at ae8c2a1: 53/53 plus schema/renderer/diff, source unchanged. The delta summary/JUnit and archive SHA-256 were independently checked. [Committed evidence](validation/eur-financial-guard.md). This branch is not yet deployed; normal halle checkout is c24d27c.

**One remaining implementation blocker:** the cumulative-transfer capability check still deliberately rejects all real paid admission. Finish the smallest actual bounded result-transfer path; do not remove the check or use old code to bypass it. The [narrow follow-up](../../Thesis/tracking/IMPLEMENTATION_PROMPTS.md#remaining-bounded-result-transfer-safeguard--11-november) was dispatched on 11 November to the fresh user-requested **Finish bounded result transfer** chat (`01a1ccb6-c418-7573-8386-eefc3fa92029`). Active work is verified; repair and validation remain pending.

Then deploy/preview the reviewed revision without changing private/user state, refresh the existing small GCP case's full EUR quote and personal-cash ceiling, obtain explicit approval, and supervise creation → three ten-second phases → result verification/size → deletion/independent absence. No paid launch or fixed allowance is approved here. GCP preparation can proceed without chair IT contact or a new account deposit; AWS advanced activation and Azure Pay-As-You-Go/quota access are separate user financial decisions before all-cloud acceptance. Preserve the remaining registered acceptance, calibration, DAY and concurrent-week gates. [Shortest path and evidence](../../Thesis/tracking/EXECUTION_CHECKLIST.md#committed-eur-repair-and-immediate-next-steps--11-november).

## Cloud preparation without chair IT — 11 November

The user wants to retain halle for preparation and avoid chair IT contact. Halle has adequate hardware for setup and supervised work; do not turn its separate abrupt-power-off acceptance limitation into a blocker for all cloud preparation. Do not power off this shared host or claim an SSH disconnect proves physical loss. No controller purchase, failure-test waiver or unattended qualification is adopted.

**AWS:** Chrome confirms the $20 First Principles project pausing limit, $120 available credit and $0 reported charges. Frankfurt remains denied. Its opened advanced-features review explicitly says activation is irreversible and deletes spend limits. No verification code, root-email change or activation was submitted; user review is required for this consequential change.

**Azure — authorized request blocked:** the user explicitly approved sending the quota-only request. The quota-prefilled blade failed, so Codex used the standard Support + troubleshooting flow and selected the existing Students subscription and Compute-VM quota category. Azure explicitly reports that this subscription is ineligible for a quota increase until upgraded to Pay-As-You-Go. Stopped as instructed: no ticket ID, submitted support case, quota increase, upgrade or payment change. [Request/evidence](../../Thesis/tracking/EXECUTION_CHECKLIST.md#authorized-azure-request-blocked-by-subscription-eligibility--11-november). No exception or credit-retention outcome is promised. Any financial conversion is a separate user action.

**GCP/runner — reconnected and checked:** the user restored halle; local-only checks verify installed tools after today's reboot. GCP global CPU quota is 32/usage 0; disks and reserved addresses are empty; private ADC refresh passes without retaining/displaying a token. Earlier same-day regional quota and empty-instance checks remain preparation evidence. Evidence: `~/cnb/evidence/reconnection-readiness-kinzpfqv` and `gcp-pretest-readiness-58fyfych`. The saved native gate at isolated f5073d6 is independently verified: 20/20 checks, 1,699 Python cases, zero failures/errors/skips and matching recorded log hashes. The separate 53-test native arithmetic delta at ae8c2a1 now also passes; the documented transfer blocker remains unresolved. No new paid authorization, ledger, launch or cloud changes. [Evidence and next gates](../../Thesis/tracking/EXECUTION_CHECKLIST.md#authorized-azure-request-blocked-by-subscription-eligibility--11-november).


## Mandatory financial gate — EUR 300 for the entire thesis

**User-confirmed 10 November: the chair ceiling is €300 for everything across the entire thesis. The user advances personal money before reimbursement.** Historical USD 300/250/50 labels, command examples and forecasts below are not EUR spending authority. Do not relabel executable USD inputs or infer a new FX rate. Current rules: [Thesis BUDGET_RULES.md](../../Thesis/tracking/BUDGET_RULES.md), made mandatory by [Thesis AGENTS.md](../../Thesis/AGENTS.md#mandatory-financial-rules).

**Live creation remains blocked** until the exact approved scope/amount/cash exposure, existing liabilities, current EUR/FX quotes and necessary enforced-guard verification are complete. Proposed planning envelopes are €75 AWS + €75 Azure + €75 GCP + €25 shared = €250 working, with €50 protected; these recommendations are not approved expenditure or applied monthly console limits. No automatic launch, extra pilot, paid retry, resource/limit/plan increase or protected-reserve release. Validate cheaply, review separately before DAY and again before both concurrent weeks.

Before creation, show and save a real approval for the exact case/fixed batch, hashes, accounts/regions/SKUs/counts, start/expiry window, lifetime, expected charge versus conservative complete-workflow ceiling, dated currency conversion/tax/fees, credit treatment and personal-cash maximum. Compare incurred/unbilled/other-thesis amounts plus every active reservation and new liability against all applicable ceilings. Do not treat reimbursement expected as already received or credits as extra budget. Price full setup/copy/cleanup/residual support; verify official prices within 24 hours. Do not infer that the DAY export allowance or a proposed 1-GiB short allowance is measured or enforced. Use the canonical rules' approval card.

Keep exact allowlisted resources/lifetime/data scope, one shared ledger and expiry readback before provisioning. Have independent recovery access/credentials and adequate time/reserve for cleanup. On failure, uncertainty or approaching an allowance, stop new creation/measurement and promptly perform original scoped stop/deletion/recovery. **Never let an expired approval or exhausted budget strand existing resources billing.** STOP/deallocation is fallback, not complete cleanup: delete and independently inventory instances/disks/IPs/placement/support; open/unknown cleanup blocks future launches. Retain state and reconcile delayed invoices/credits/FX and reimbursement after the run. No automatic new deployment/experiment retry; same-run transfers must stay in an approved cumulative byte/time/cost scope.

**Historical baseline audit at c24d27c (superseded by ongoing repair; not a claim about its current code):** source at c24d27c has reservations, source checks, lifetime/expiry/cleanup guards and offline tests. Its cost payload is USD-only and its aggregate constants are US$250/300/50. Reviewed approval checks verify identity/source/hash but do not establish typed numeric/cash/expiry/resource-scope enforcement. The reviewed date parser does not alone enforce the new 24-hour rule, and size inventory is not a cumulative transfer cap. These need a dedicated offline audit/repair; no new software guard/test pass is claimed. The [implementation packet](../../Thesis/tracking/IMPLEMENTATION_PROMPTS.md#post-f10-eur-budget-and-personal-cash-guard-audit) was sent to the existing repair chat on 10 November after explicit user authorization. Active offline work is verified; repair and validation remain pending. No paid flags, live ledger or approval are generated by these rules.

**AWS setup readback — 11 November:** Chrome confirms the $20 project pausing limit, attached payment method, $120 available credit and $0 reported charges. The separate $10 notification budget is distinct. Frankfurt access is still denied; advanced activation remains unperformed and removes spend limits. Saved GCP/Azure alerts are notifications, not guaranteed VM/bank-charge caps; provider billing can lag. No bank-charge guarantee is made.

[Rules, current control matrix and approval card](../../Thesis/tracking/BUDGET_RULES.md) · [Confirmed chair decision](../../Thesis/DECISIONS.md#2026-11-10-confirm-eur-300-all-in-chair-ceiling-and-require-spending-guards). Official sources: [AWS project limits](https://docs.aws.amazon.com/accounts/latest/reference/create-spend-limit.html), [AWS advanced activation](https://docs.aws.amazon.com/accounts/latest/reference/activate-advanced-features.html), [GCP budgets](https://docs.cloud.google.com/billing/docs/how-to/budgets), [Azure alerts](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/cost-mgt-alerts-monitor-usage-spending).

## Current access and next account decision — 10 November evening

Codex verified the restored SSH connection and clean halle `c24d27c`. Google ADC and Azure tokens refresh successfully. The user renewed the expired AWS browser session; the actual account identity now passes. Fresh GCP billing/quota/inventory reads pass: no instances, disks or reserved addresses, and regional N2=200/CPU=200/IP=8/VM=24/SSD=500 GB with inspected usage zero. Global quota/credit balance are separate and were not refreshed by those regional reads. Evidence: `~/cnb/evidence/access-refresh-6agn437x`, `gcp-access-recheck-our3rzqi` and `machine-access-refresh-sgwr1uqb`.

**Still blocked for the selected machines:** AWS Frankfurt quota reads explicitly denied by organization SCP; Azure DSv5=0 and total regional vCPU=6 in Germany West Central and Sweden Central. One selected pair needs 8 vCPUs; overlapping weeks need 16. A successful login or cash deposit does not remove these blocks.

**Fresh small-test price revision:** official Frankfurt tables read on 10 November confirm n2-standard-4 $0.250248/VM-hour, pd-balanced $0.000164384/GiB-hour, IPv4 $0.005/IP-hour and Europe export $0.12/GiB. Non-executable `~/cnb/private/gcp-short-cost-revision-2026-11-10.json` totals **$0.504673**, including full-1,200-second compute/disks/IP $0.173673, proposed 1-GiB export $0.12, residual cleanup disks $0.011 and $0.10 each for tax reserve/contingency. One GiB is a generous planning allowance, not measured data or an enforced limit; the actual code inventories file sizes/hashes before retrieval. This is not an approved cap, actual bill, funding allocation or authorization for an extra normal pilot. No Billing API activation was needed. The old $5 proposal remains withdrawn.

**Failure-test controller qualification:** the implemented [registered procedure](campaign-developer.md#supervised-registered-failure-checks) requires normal V-S1/V-S3 first, then the planned server fault and abrupt runner power-off via independent hypervisor control. Shared halle cannot be powered off by this account and must not be used that way. Closing SSH or stopping a process is a different test. Qualify a controllable dedicated runner/ITO equivalent before failure acceptance, or obtain an explicit owning-project review of the requirement. No new host purchase, weaker assertion or protocol change is adopted. Normal test preparation can proceed separately.

**User now reviewing AWS Step 1/3:** the live form shows $20/month recommended and custom minimum $20, with $0 month-to-date. Recommend the minimum $20 for initial setup; it is a project pre-tax cost ceiling, not a deposit, predicted test cost or extra research budget. Recommendation only: Codex has not saved it and user completion is unverified. This project-pausing spend limit is separate from the saved $10 notification budget; future advanced activation removes project spend limits. Keep staged DAY/WEEK reviews. [Official rules](https://docs.aws.amazon.com/accounts/latest/reference/create-spend-limit.html).

**AWS next step is a user financial decision:** Codex opened the official `https://settings.aws.com/billing/upgrade` introduction. It states no upgrade fee, remaining credits retained, then spend-limit/cost-control and payment-method steps. Automatic approval review rejected clicking **Get started** because entering paid setup was not authorized. No upgrade, limit or payment method was saved; do not retry via a workaround. User must choose whether to enter and handle payment/final commitment privately. Frankfurt advanced activation is a separate irreversible step that removes project spend limits; do not conflate it with the introductory paid-plan limit. Azure paid/quota and student-credit treatment also need user review before a financial commitment. Do not set an arbitrary cash top-up.

[Detailed evidence and ownership](../../Thesis/tracking/EXECUTION_CHECKLIST.md#access-refresh-and-next-prerequisites--10-november-evening). Sources: [official compute/disk](https://cloud.google.com/products/compute/pricing/general-purpose), [network](https://cloud.google.com/vpc/pricing), [AWS plans](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html), [advanced activation](https://docs.aws.amazon.com/accounts/latest/reference/activate-advanced-features.html), [Azure quota eligibility](https://learn.microsoft.com/en-us/azure/quotas/quickstart-increase-quota-portal).

## Current plan: validate cheaply, then release funding by stage — 10 November

**User-selected direction:** do necessary cheap tests first; review any additional payment/funds immediately before DAY; review again after DAY for both parallel weekly studies. No stage is automatically authorized by this plan. Use eligible credits first; estimates, spending allowances, account credit and actual bank charges are distinct. The $4.2347/$5 GCP proposal remains withdrawn; its private files must not be run. No VM, reservation or new funding allocation has been created.

**Already passed:** native halle 1,595 Python cases/16 Terraform checks/20 strict-gate checks at e4f0f2d; 222 affected image-repair cases including 56 regressions at c24d27c; all three initial cloud identities and read-only Terraform plans; GCP local settings/source validation and preview. Counts overlap; do not add them as unique tests. These are setup/offline/read-only evidence, not real VM/SSH/expiry/export/cleanup acceptance or research results.

| Stage / checkpoint | What runs and what must pass | Dated USD forecast, all indicated clouds combined |
| --- | --- | --- |
| Short acceptance | V-S1 and V-S3: all-cloud same-zone/close-placement lifecycle, 10 measured seconds per phase, verify native outputs and full deletion/absence; 20-minute whole-pair cap. | $0.65 each |
| Isolated failure/recovery | V-A-FAIL / V-Z-FAIL / V-G-FAIL: actual planned failure/controller-loss/independent-expiry/recovery and cleanup checks in one provider each. Normal success alone does not pass them. | $0.22 / $0.21 / $0.22 |
| CAL-S1 | Separate retained pair/cloud, 1/2/4/8 streams × three 60-second trials; select/freeze common N and clean up. | $0.96; **acceptance + calibration total $2.91** |
| **Funding review before DAY** | Reconcile actual short-test outputs/size/lifetimes/cleanup and delayed charges. Confirm prices, credit eligibility/expiry, tax/support/export/recovery, disk and renewal/maintenance readiness; review existing alerts. Add payment/funds only if needed and obtain separate DAY allowance. | No predetermined cash top-up |
| DAY | One continuous 24-hour run on separate retained pairs across all clouds; check full TCP/≥99% RTT coverage, verified post-measurement retrieval and full cleanup. | **$61.09 total**, refresh before launch |
| **Funding review before both weeks** | Review DAY/remaining eligible credit and charges, choose UTC windows, refresh remaining costs and overlap quotas (four VMs/16 vCPUs per cloud), renewal and monthly alerts. Obtain separate weekly authorization. | No predetermined cash top-up |
| P0/S1 + P1/S3 WEEK | Two concurrent seven-day studies in the same week/windows, 28 observations each; fresh pairs/all three clouds, three 60-second phases, independent executions and shared budget. | **$18.20 + $18.20 = $36.40 total** |

All forecast numbers above use **14 October prices** and must be refreshed. Scheduled base $100.40 plus original shared replacements/tax/uncertainty = $147.82 working forecast, with $50 separately protected; $250 working/$300 total policy unchanged. Do not add shared reserves twice, preload these sums as cash or treat a credit-funded run as zero gross research cost. A credit balance is provider-specific and subject to eligibility/expiry; quotes and actual billing currency must be reconciled per account.

**Money and access:** GCP's last-verified trial has quota headroom; no cash deposit or paid conversion is currently justified for preparation. AWS selected-VM/Frankfurt and Azure selected-family/regional quota restrictions may need a user-approved plan/payment/quota decision **before their first short tests**, even if remaining credits could pay the small charges. Ordinary paid accounts charge uncovered use through their registered payment arrangement; alert budgets are notifications, not prepaid wallets or guaranteed limits. Check actual credit treatment before any conversion, especially Azure Students. No upgrade/payment change is made by this plan.

Do not add three normal single-provider smoke runs before the required all-cloud checks. Any GCP-first case must retain its actual planned assertions; an extra normal debugging attempt requires explicit justification, pricing and approval. After each authorized case, record result size/hashes, deletion/absence and later billing; fix/review failures before another launch. No automatic retry, calibration/DAY/WEEK start or P2/P3 funding is authorized.

**Connection restored — 10 November:** Codex verified the user-reopened Mac SSH master and clean halle source c24d27c. The old $5 proposal is now explicitly marked withdrawn remotely, with original cost/config artifacts preserved; no cloud APIs or launches occurred. **Next small action:** refresh identity/access and short-run output/cost bounds, then review the next necessary registered case. User enters any password/MFA privately. Before long runs resolve 11 November maintenance, Linger=no/durability and renewable credentials. Keep study dates unset until prerequisite evidence is available; the timetable remains at risk.

Canonical evidence/ownership: [staged checklist](../../Thesis/tracking/EXECUTION_CHECKLIST.md#staged-tests-and-funding--10-november), [decision](../../Thesis/DECISIONS.md#2026-11-10-release-experiment-funding-in-stages), [validation/run index](../../Thesis/tracking/experiments/RUNS.md). Dated forecasts: [run register](https://docs.google.com/spreadsheets/d/1902IXzZ3nJ9mToJLjGkOlg_LObUeVYR1rxbXbBs74VE/edit). Official financial/access guidance: [GCP trial](https://docs.cloud.google.com/free/docs/free-cloud-features), [AWS plans](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html), [AWS advanced activation](https://docs.aws.amazon.com/accounts/latest/reference/activate-advanced-features.html), [Azure quota eligibility](https://learn.microsoft.com/en-us/azure/quotas/quickstart-increase-quota-portal).

## Cheap acceptance checks: cost correction — 10 November

**No launch approval was given. The $5/$4.2347 GCP proposal is withdrawn for revision.** Codex incorrectly used DAY’s 20-GiB export allowance for a ten-second-per-phase check: $2.40 export plus $0.65 tax reserve and $1 contingency dominated the figure. The full twenty-minute compute/disk/IP amount is about $0.174 before tax and result export; it is not a full bill forecast. Same-zone private GCP benchmark traffic is free; exported text results are separate from the TCP payload and need a proportionate size bound. No measured artifact size or actual charge exists yet.

The agreed checks are **V-S1 normal same-zone in all three clouds, V-A/Z/G-FAIL isolated failure/recovery checks, V-S3 close-placement in all three clouds, then CAL-S1 calibration before DAY**. V-S3 is required before P1; it is not a new scenario. The register’s original 14 October estimates are $0.65 for each all-cloud normal check, $0.22/$0.21/$0.22 for isolated AWS/Azure/GCP checks and $0.96 for calibration: **$2.91 total**, requiring price refresh before launch. Three additional per-cloud normal smokes followed by repeating all three are not a required extra stage. The early GCP-only normal check was a suggested debugging step while AWS/Azure are blocked; if used, label and price it as an optional extra. It does not prove unperformed failure cases or all-cloud operation.

Next bound short-run outputs/export and reprice the complete workflow, aiming below $1 for a single GCP attempt if justified. This is a recommendation, not authorization, a guaranteed charge or a provider hard cap. Preserve the lifecycle cap, independent expiry and full deletion/absence checks. After an authorized first attempt, record output size and lifetimes and reconcile delayed billing before any repeat. The private review `gcp-acceptance-review-y2x4hbn8` is withdrawn in this runbook. Its remote files could not be marked because the SSH connection expired; they remain unmodified, lack paid authorization and must not be executed; no ledger/reservation was initialized, no $5 funding allocation applied and no VM launched. The successful c24d27c source/settings checks remain valid.

[Original plan and corrected breakdown](../../Thesis/tracking/EXECUTION_CHECKLIST.md#acceptance-cost-clarification--10-november). [Official GCP transfer pricing](https://cloud.google.com/vpc/pricing).

## GCP component test preparation — 9 November

**Historical preparation — cost proposal withdrawn on 10 November:** do not approve or launch using the envelope below; see the correction above. The image fix is verified at clean halle revision `c24d27c`: 222 affected tests passed, including 56 new regressions. Full actual provider settings, current-revision initialization and strict price/source-envelope checks now pass with zero external calls during validation. Proposed $5 ceiling awaits user approval; no ledger, reservation or VM was created. Eligible trial credit can fund this component check before AWS/Azure upgrades. It does not replace all-cloud acceptance or produce research data.

Codex verified halle’s public outbound IPv4 after specific user approval: **131.159.76.16/32** is saved in the private GCP draft; no firewall has been created. All 36 checked GCP IAM permissions passed. Private preparation is at `~/cnb/private/gcp-acceptance-draft/`: local manifests initialized, actual runtime/price drafts saved, lifecycle preview **external_calls=0 / paid_eligible=false**. Initial identities/read-only Terraform plans remain verified in all three clouds. The original preparation used clean e4f0f2d; current source is clean at c24d27c; no VMs, billing upgrades or optional Cloud Billing API activation occurred.

**Original code blocker — resolved at c24d27c:** Google readback confirms the pinned image `projects/ubuntu-os-cloud/global/images/ubuntu-2404-noble-amd64-v20261105` is READY / X86_64 / GVNIC. Provider settings and campaign checks currently require a different `ubuntu-2404-lts-amd64-...` name. Native settings validation rejected the actual image with zero cloud calls; see `provider-settings-check.json` and `image-readback.json` in the private draft folder. This is an implementation mismatch, not a sudo, credential or payment problem. After explicit user approval, the narrow repair handoff was sent to the existing implementation repair chat; 222 affected tests and the actual image check passed on halle; the original failure is preserved; do not work around the check with an invented image or arbitrary-string acceptance.

**Reviewable first attempt:** existing EXP-902 / exp-902-acceptance, two n2-standard-4 VMs in europe-west3-a, 4 vCPU/16 GiB each, 32-GiB pd-balanced each, one public IPv4 each, private benchmark traffic, ten measured seconds per phase and N=4 before calibration. Existing 1,200-second cap covers the entire workflow. Proposed stage bounds plus expiry lead total 1,155 seconds; `ens4` remains an expected interface to check on the real guest. These are preparation choices, not observed VM behavior.

Official Frankfurt prices were read in Chrome on 9 November: VM $0.250248/hour, balanced disk $0.000164384/GiB-hour, public IPv4 $0.005/hour, Premium Tier export to Europe $0.12/GiB; same-zone private IPv4 traffic has no transfer charge. Compute/disks/IP for the full cap total $0.173673 after upward rounding. The initial private draft adds a conservative 20-GiB export allowance ($2.40), $0.65 tax reserve and $1 contingency: $4.223673. Tax/currency reserves are not actual tax/exchange-rate claims, and 20 GiB is neither predicted file size nor an enforced traffic cap. The final sourced review adds $0.011 for 64 GiB of residual disks for one proposed additional cleanup hour. This is a cleanup allowance, not a template fee or guaranteed cap. The final F11 envelope totals $4.2347 after upward rounding. **Proposed $5 for one attempt remains unapproved and is not a hard cap.** Keep the existing alert budgets unchanged until their stage review.

Prepared private review: `~/cnb/private/gcp-acceptance-review-y2x4hbn8/summary.json`. Both cost/support references are now real, source-bound local files. The generated operational config is in the existing ignored `configs/tests/campaign-generated/` area; initialized campaign `20261109T222013Z-exp-902-d19bdc10` has not executed. Actual `paid_authorization` remains absent; the ledger is not initialized. Original drafts and failures remain unchanged. The 9 November live register readback is preserved privately. Proposed conservative additive $5 funding keeps all prior allocations: primary $103.75, replacements $1.95, tax $21.12, uncertainty $26 = $152.82 working commitment, with $97.18 working headroom and $50 separately protected under the $300 cap. Funding remains unapproved; no Sheet edit or later-stage allocation is implied.

Native templates have no additional service fee, but STOP can leave billable disks. Before VM creation, register/read back independent native STOP protection. Then prove authenticated SSH/tool builds, native outputs, post-measurement copy/checksum verification, complete resource deletion and an independent absence check. Price/model/source checks now pass; funding/spend approval and live protection remain launch gates; the inspectable preview is not admission. No automatic retry, calibration, DAY or WEEK start is authorized.

[Full evidence and next action](../../Thesis/tracking/EXECUTION_CHECKLIST.md#gcp-only-test-preparation--9-november). Price sources: [compute/disk](https://cloud.google.com/products/compute/pricing/general-purpose), [network/IP/export](https://cloud.google.com/vpc/pricing), [native template pricing](https://docs.cloud.google.com/compute/docs/instance-templates#pricing). Private drafts and credentials stay outside Git. The all-cloud sequence below still applies after the component check and remaining account access work.

## Plain-language path to the first real test — 9 November

The intended workflow is correct: **authenticate on halle → framework creates VMs → measures → retrieves/verifies results → deletes run-owned infrastructure**. Native offline checks pass at e4f0f2d; the first live acceptance must prove this lifecycle. No new experiment design or offline rerun is needed.

**Credit pays eligible charges; it does not unlock every VM or raise quota.** The saved $10/€10 budgets are alerts, not prepaid money or guaranteed caps. Paid access generally needs a payment method rather than a cash top-up; uncovered usage can be charged. AWS retains eligible remaining credits after conversion. Check actual Azure student-credit treatment before accepting an upgrade. GCP has no identified quota-based need for paid conversion.

| Order | Remaining work | Division of work |
| --- | --- | --- |
| 1 | Resolve AWS selected-VM/Frankfurt access and Azure DSv5=0/regional CPU=6. Need 8 vCPUs for one pair, 16 for study overlap. | Codex reviews/prepares; user handles financial commitment/payment details and required access confirmations. |
| 2 | Cloud CLIs and private credential/SSH locations are installed; finish home-directory operator/service settings. | Codex can prepare/perform through an accessible remote session; no sudo-backed /srv or /etc recipe unchanged. |
| 3 | Sign in per provider; check exact account/project, creation/deletion/expiry permissions and Terraform plan. | Codex runs checks; user completes MFA/sign-in and approves new access. Secrets stay outside chat/Git. |
| 4 | Verify basic controller/SSH/service operation, independent expiry, current full-workflow price and explicit test-spend allowance; review existing alerts. | Codex prepares; user approves the concrete live-test spend. |
| 5 | Run existing all-cloud V-S1/V-S3 acceptance: ten measured seconds per phase, retrieve/verify results, delete and independently verify all run-owned billable resources. | Codex supervises after authorization; no manual VM creation or new experiment required. |

CLI preparation and **GCP authentication/read-only checks can proceed now**, alongside AWS/Azure access resolution. The saved all-cloud acceptance still needs all three accounts ready. Successful live cleanup is not yet proved. VM power-off alone can leave billable disks/IPs; there is no separate “stop budget” action. Leave alerts in place.

Week-long credential renewal, maintenance/reboot resilience and measured long-run storage are later unattended-readiness requirements. They do not prevent CLI preparation or first sign-in. Existing acceptance/failure, calibration, DAY and N/window gates remain unchanged.

Sources checked 9 November: [AWS plans/credits](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html), [GCP trial](https://docs.cloud.google.com/free/docs/free-cloud-features), [Azure Students](https://azure.microsoft.com/en-us/pricing/offers/ms-azr-0170p/). AWS advanced activation is irreversible and [removes project spend limits](https://docs.aws.amazon.com/accounts/latest/reference/activate-advanced-features.html). The detailed account review below remains the evidence. This clarification performs no new cloud setup or launch.

**Setup execution — 9 November:** all three provider identities now pass the framework check on halle, and **AWS, Azure and GCP each passed an actual read-only Terraform plan** using their pinned providers and private credentials. No apply, resource launch, billing upgrade or new IAM grant was made. Cloud CLIs and the private benchmark SSH key are installed without sudo; framework source remains clean at `e4f0f2d`. This completes initial supervised credential setup, not unattended renewal or deployment/cleanup acceptance. Remaining machine-access blockers are AWS Free-plan/Frankfurt restrictions and Azure DSv5=0/regional CPU=6. GCP has inspected quota headroom. M04 Active; research experiments not started. [Live credential/access evidence](../../Thesis/tracking/EXECUTION_CHECKLIST.md#all-three-provider-identities-verified--9-november).

For the supervised halle setup, source `~/cnb/private/cloud-env.sh` before cloud commands. This shell file sets private configuration paths and is not a systemd `EnvironmentFile`. Google sign-in is complete; do not repeat `~/cnb/private/gcp-login.sh` unnecessarily. Codex verified identity/ADC with `~/cnb/private/gcp-readonly.sh` and passed a live read-only Terraform plan against the project and Ubuntu image. The expected N2 shape, READY GVNIC-capable image and sufficient reviewed quotas are readable; VM creation/placement/expiry/cleanup and long-lived renewal remain unproved. The original Azure device-code handoff failed with 530035. The replacement is `bash /private/tmp/cnb-azure-browser-login.sh` **on the Mac**, followed by `~/cnb/private/azure-readonly.sh` on halle after browser sign-in. The replacement uses Microsoft’s browser authorization-code flow through a tested loopback-only SSH callback tunnel; it keeps Security Defaults enabled. Azure browser login is now verified; the live read-only AzureRM 4.81.0 plan also passed with auto-registration disabled. Family/regional quota remains 0/6 in both reviewed regions. The device-code failure is retained as history. [Denial evidence and replacement](../../Thesis/tracking/EXECUTION_CHECKLIST.md#azure-device-code-denial-and-browser-replacement--9-november). Sign-in and inspection create no VMs. The private benchmark key is `~/cnb/private/ssh/benchmark_ed25519`; campaign settings and expiry/cleanup acceptance are still pending. Post-install quota: 21,758 MiB of 51,200 MiB used, about 28.8 GiB left; this is not a measured results-volume forecast. [Setup evidence and handoff](../../Thesis/tracking/EXECUTION_CHECKLIST.md#installed-tools-and-private-authentication-handoff--9-november).

AWS private sign-in is complete; do not repeat `~/cnb/private/aws-login.sh` unnecessarily. The `cnb` process profile and live pinned-provider read-only Terraform plan passed for account 238360820461, and the framework verified all three identities together. Stockholm Standard On-Demand quota is 16 with empty instance inventory; m7i.xlarge is offered but its FreeTierEligible flag is false. Frankfurt quota/EC2 reads are explicitly denied by organization SCP. [Verified credentials and remaining machine-access gates](../../Thesis/tracking/EXECUTION_CHECKLIST.md#all-three-provider-identities-verified--9-november).

## Current account and validation evidence

**Current next step — 9 November:** halle repair **passed** at `e4f0f2d3aefb404d96fdbaf173f4e8b99165cd47`: **16/16 targeted Terraform checks, 20/20 strict offline checks, 1,595 Python tests with zero failures/skips, all 112 required identities, source unchanged** (user/repair-chat report). The successful archive is already in Mac Downloads and independently verified: ZIP integrity, all 37 recorded log hashes, JUnit checksum/counts, revisions, clean unchanged source and required coverage agree. Archive SHA-256: `e8f2141a1edc4948ca853700b1b6c8a0dbc151032862fce43a647820fd0fc44f`. No further repair rerun is indicated. **All three private identities and actual read-only Terraform plans pass. First prepare the GCP-only component check described above; resolve AWS/Azure restrictions alongside it before all-cloud acceptance.** Chrome control recovered. AWS shows a concrete Region restriction: this simplified project is assigned to Stockholm; Frankfurt requires **Paid upgrade + irreversible advanced-features activation**. Activation removes project spend limits; it does not turn the saved alert budget into a cap. The reviewed upgrade wizard is at Step 1 of 3; no limit/upgrade/activation was saved. GCP trial project access is resolved; Codex enabled Compute Engine API and inspected europe-west3 N2/global CPU, VM/IP/disk quota with sufficient headroom. Azure Students is Active/Owner; after explicit confirmation Codex registered Microsoft.Compute and verified Registered. Actual DSv5 quota is 0 and total regional vCPU quota 6, usage zero, in Frankfurt and Sweden; even one selected two-VM pair is blocked. No quota request or upgrade was submitted. [Completed actions, evidence and exact blockers](../../Thesis/tracking/EXECUTION_CHECKLIST.md#cloud-console-retry-and-independent-evidence-review--8-november). Do not run the generic `/srv/cnb`, `/etc` or sudo/linger commands unchanged on halle. [Passing evidence, transfer and concrete next steps](../../Thesis/tracking/EXECUTION_CHECKLIST.md#native-offline-gate-passed--8-november). Earlier failure/setup records below are retained history, superseded by this native pass. No real cloud or research acceptance is claimed.

**Saved test alerts — review before real stages:** AWS **Thesis-initial-tests $10/month**, GCP **Thesis €10/month**, Azure **Thesis-initial-tests €10/month**, all actual 50/80/100%. AWS email verification is confirmed; actual threshold delivery remains untested. Review current monthly spend and fresh provider-specific whole-workflow costs before acceptance, CAL, DAY and WEEK, then explicitly increase these existing amounts as needed. Larger amounts are not chosen/applied yet. These are notification alerts, not hard caps. Keep **$250 working / $50 protected / $300 gross total**; promotional credits do not enlarge it. [Saved settings and payment decisions](../../Thesis/tracking/EXECUTION_CHECKLIST.md#saved-budgets-and-payment-decisions--8-november-2026).

**Machine access/payment review — 8 November:** AWS Console Home last showed **$120 credits / Free plan / $0 month-to-date**; source/expiry of extra $20 remains unreviewed. Free excludes m7i.xlarge. The Frankfurt console additionally states this project is assigned to Stockholm and needs **Paid, then advanced features** for other Regions. Codex reviewed both the advanced page and upgrade wizard without completing either. The latter proposes a $20 spend limit before payment entry; **advanced activation subsequently removes project spend limits and is irreversible**. Do not present that intermediate limit as lasting protection. The user handles financial commitment, payment details and new root credentials; material access/guardrail changes need action-time confirmation. [Official activation consequences](https://docs.aws.amazon.com/accounts/latest/reference/activate-advanced-features.html).

**GCP is no longer blocked on project visibility:** exact trial project `project-a70f8497-3463-4ef2-9cd` (number `296458484827`) matches billing `017D32-693E2D-BD122F`; current trial shows **€269.43 / 89 days**. Codex enabled `compute.googleapis.com` and verified Enabled. Inspected quotas below have zero usage and headroom for four n2-standard-4 / 16 vCPUs and four 32-GiB boot disks. Keep trial: no quota-based need to upgrade was found. Effective shape/zone capacity, placement/protection permissions and the full expiry workflow remain separate checks; quota is not a launch guarantee.

| Inspected GCP quota | Applied limit | Usage | Scope |
| --- | --- | --- | --- |
| N2 CPUs | 200 | 0 | europe-west3 |
| CPUs (all regions) | 32 | 0 | project/global |
| VM instances | 24 | 0 | europe-west3 |
| In-use / static external IPv4 | 8 each | 0 each | europe-west3 |
| Persistent Disk SSD / Standard | 500 / 4,096 GB | 0 GB | europe-west3 |

**Azure — registration and quotas verified:** the user explicitly approved registering Microsoft.Compute; Codex completed registration and verified **Registered**. Students remains Active/Owner. The quota table now shows **Standard DSv5 Family=0, Total Regional vCPU=6**, usage zero, in **both Germany West Central and Sweden Central**. This blocks even one 8-vCPU D4s_v5 pair; overlapping pairs need 16. Frankfurt DSv5 access guidance says “Unavailable in this region” for this subscription and offers Support/alternatives. It is not proof of global SKU unavailability. The regional request form was inspected with draft total 16 and then cancelled without submission; it would register Microsoft.Quota if submitted. No quota request, Microsoft.Quota registration, Support message or paid upgrade was made.

The [Students offer terms](https://azure.microsoft.com/en-us/pricing/offers/ms-azr-0170p/) require pay-as-you-go access to increase quotas/limits. Review actual upgrade/credit-retention terms and required DSv5 access before the user commits payment details; paid access does not guarantee quota/capacity. Do not assume the separate $200 offer exists or that unused Students credit carries automatically into a paid arrangement. Keep current alerts; review before real stages.

**Northern-region question — reviewed 9 November, no change adopted:** AWS Stockholm (eu-north-1), Azure Sweden Central (swedencentral, Gävle) and GCP Stockholm (europe-north2) exist. GCP's official Stockholm zone table does **not list N2**; Finland (europe-north1, Hamina) lists N2 in all three zones. Azure North Europe is Ireland. Staying north does not remove AWS m7i.xlarge's Paid-plan restriction or Azure's observed DSv5=0/regional=6. Recommend retaining the configured Frankfurt geography while resolving access; a Nordic alternative needs explicit region/shape/protocol review, fresh quota/placement/expiry checks and prices. No executable configuration or accepted region decision was changed. [Sources and tradeoffs](../../Thesis/tracking/EXECUTION_CHECKLIST.md#northern-region-alternative--reviewed-9-november-not-adopted).

No upfront top-up is needed for preparation. Paid plans meter usage: eligible credits offset charges, and uncovered usage is payable through the registered payment arrangement. No payment plan, budget amount or benchmark launch was changed; GCP API activation and authorized Azure Microsoft.Compute registration were completed. Private renewable credentials on halle, controller persistence, complete-workflow price/alert review and separately authorized supervised acceptance remain pending. [Full evidence and exact next actions](../../Thesis/tracking/EXECUTION_CHECKLIST.md#cloud-console-retry-and-independent-evidence-review--8-november).

## 1. Create the three accounts and obtain the controller

**Mentoring check-in — 7 November 2026:** use the Thesis chat to ask questions, report progress and review the next step. Share the current stage, what changed, blockers and redacted output/evidence paths; include stage/run IDs and source revision when available. Codex recommends a small next action and explains choices, then records accepted decisions and evidence in Thesis tracking. Keep this Markdown guide and its generated HTML aligned when guidance or recorded progress changes. Browser checklist marks remain personal notes; they do not synchronize repository records or establish completed runs. Start by confirming actual runner access and cloud-account/funding/quota readiness. No new execution progress is reported by this workflow agreement. See the [Thesis check-in workflow](../../Thesis/tracking/EXECUTION_CHECKLIST.md#mentoring-and-progress-updates--7-november-2026).

**Historical position — 7 November (superseded by the current update above):** the TUM runner has not been requested yet. Browser logins and promotional-credit displays were subsequently shown for all three clouds; execution identities, eligibility, coverage and quotas remain unverified. Later the same day, the user supplied the halle host inventory: shared hardware and Python are adequate in principle, but durable storage, persistence, tools/permissions and maintenance recovery remain unresolved. The user now authorizes a home-directory cloning/installation and offline-test trial before requesting more hosting or storage; use the [Halle setup recipe](../../Thesis/tracking/EXECUTION_CHECKLIST.md#halle-installation-trial--authorized-7-november-2026). LRZ chair-project access remains the alternative if this host cannot be qualified. Once access is known, reassess the study calendar; 9–15 November remains conditional. No request has been sent by Codex and no paid fallback has been selected.

**Cloud account progress — screenshots reviewed 7 November:** the user is logged into GCP, AWS and Azure in the browser. The screenshots show the following; Codex subsequently inspected live Chrome views read-only; findings below supersede the initial screenshot-only limits. Identities on halle remain unconfigured.

| Provider | Credit evidence | Pending item |
| --- | --- | --- |
| GCP | €269 remaining out of €269; 90 days, ends 6 February 2027; user's $300 welcome-offer description | Verify project/billing linkage, selected n2-standard-4 and four-VM/16-vCPU quota, placement/expiry access and full-workflow coverage |
| AWS | $100 USD visible; free-plan text, 182 days, displayed end 7 May 2027 | Extra up-to-$100 is conditional. Check m7i.xlarge access, quotas, placement/expiry services and covered costs |
| Azure | Azure for Students $100 out of $100; 365 days; active student product shown | Separate new-account $200 credit is not shown. Check actual offer/subscription, D4s_v5/regional quota and expiry-service eligibility |

**Offer restrictions:** [AWS's published Free-plan EC2 list](https://aws.amazon.com/free/compute/) does not include the protocol's m7i.xlarge. The direct catalog block confirms Paid-plan access is needed for this choice; the user handles that financial commitment. Do not substitute a different benchmark VM merely to fit the offer. [GCP non-billable trial accounts cannot request quota increases](https://docs.cloud.google.com/free/docs/free-cloud-features); check the actual quota first. [Azure Students](https://azure.microsoft.com/en-us/pricing/offers/ms-azr-0170p) ($100/12 months) and the [regular Free Trial](https://azure.microsoft.com/en-us/pricing/offers/ms-azr-0044p/) ($200/30 days, in billing currency) are separate offers and cannot be combined unless Microsoft permits it. Do not budget them as $300 or assume €200.

**Earlier Azure next step (superseded by the current review above):** search Subscriptions in the Azure portal, inspect the relevant directory/subscription filters and look for a separately activated Free Trial. Review its actual credit/expiry under Cost Management; the Education screenshot verifies only the student balance. Billing-account €0 amount due is not promotional credit. If a confirmed activation is missing, ask billing support to clarify eligibility/activation. [Full evidence and account next steps](../../Thesis/tracking/EXECUTION_CHECKLIST.md#cloud-logins-and-promotional-credits--screenshots-reviewed-7-november-2026).

Keep credits separate by provider, currency, account and offer. Preserve $300 total/$250 working/$50 protected limits and gross-workflow pricing/reservations. Browser access and visible credit do not prove selected-VM/quota/renewal readiness. No plan/subscription upgrade, price refresh, cloud launch or larger budget is approved by these screenshots. The initial 11 halle failures were repaired; the native pass above supersedes that attempt. Complete the strict offline gate before cloud acceptance.

1. Create or select your [AWS account](https://aws.amazon.com/), [Azure account/subscription](https://azure.microsoft.com/en-us/pricing/purchase-options/azure-account) and [GCP project with billing](https://docs.cloud.google.com/resource-manager/docs/creating-managing-projects). Enable MFA on human accounts. Record AWS account ID, Azure subscription/tenant IDs and GCP project ID. Check actual student/trial/university credit eligibility and service restrictions; do not assume credits cover these VMs. Keep the $250 working / $50 protected / $300 total budget; refresh gross workflow quotes, tax, export and expiry-support costs before launch.
2. Obtain an always-on **Ubuntu 24.04 x86-64 controller, at least 4 vCPU / 8 GB RAM, approximately 200 GB total persistent storage**, sudo, SSH and stable public outbound IPv4. For the LRZ candidate, first obtain chair-project access and funding as described below, then create the reviewed VM. Confirm the actual image, host/user, storage, network/egress and backup arrangements. Controller sizing remains provisional; a hosting or funding choice is not approved by this runbook. The next command starts after the VM exists.
3. Check quota/capacity for **4 benchmark VMs / 16 vCPUs per cloud**, disks, public IPs, placement and independent expiry permissions in the configured regions. The controller is separate. Selected benchmark VMs remain m7i.xlarge / Standard_D4s_v5 / n2-standard-4; controller sizing does not change them.

**Existing TUM host — screenshot reviewed 7 November:** CIT SSH to lxhalle.in.tum.de works; prompt halle. Banner reports Ubuntu 24.04.2 LTS x86-64, kernel 6.11.0-29-generic, shared 64 × 3.1 GHz / 768 GB RAM, and maintenance on 11 November morning. These are banner observations, not dedicated resources or verified runtime limits. [ITO documentation](https://wiki.ito.cit.tum.de/bin/view/CIT/ITO/Docs/Guides/AlleszumEinstiegmidenDienstenderITO/) confirms a shared SSH/remote-desktop and computation service; it asks for nice on long jobs. This login does not establish LRZ Compute Cloud access.

**Inventory supplied by the user — 7 November:** canonical hostname halle.cli.ito.cit.tum.de; Ubuntu 24.04.2, no virtualization detected, AMD EPYC 9554P (64 physical cores/128 threads), 755 GiB RAM with 682 GiB available at the sample. This is shared capacity. Python 3.12.3, git, tmux, screen and rsync are available; Terraform, aws, az, gcloud and pwsh were not found on the tested PATH. Group membership establishes no sudo entitlement.

**Storage and service gaps:** home is NFS; quota reports 50 GiB total and about 12.3 GiB used/~37.7 GiB remaining. Its NFS export label differs from the home mount; have ITO confirm applicability. The pool's 6.2 T free does not override personal quota. /var/tmp has about 798 G free, but scratch is not an approved durable result/private-state store. User systemd reports running; Linger=no means logout/boot persistence remains unproved. [Ubuntu loginctl documentation](https://manpages.ubuntu.com/manpages/noble/man1/loginctl.1.html) explains lingering; actual logout behavior also depends on host policy. tmux/screen do not survive reboot.

**Next action:** ask ITO whether unattended thesis-controller use is permitted and supported, with persistent services/linger, sufficient durable storage/backups, tools and approved setup paths, stable cloud SSH/API egress and recovery around the announced 11 November morning maintenance. Keep 200 GB provisional and measure raw-plus-export storage fit; the upper DAY export envelope can total 60 GiB across clouds before retained copies. Do not assume the current home quota is sufficient. Later installation commands assume sudo-backed /srv/cnb and /etc/cloud-network-benchmark setup, which is not established for this shared account. [Full inventory and request draft](../../Thesis/tracking/EXECUTION_CHECKLIST.md#user-reported-inventory--7-november-2026). Inventory is supplied; no qualified controller, native acceptance, hosting decision or paid research run is claimed.

**LRZ login first:** open [Compute Cloud](https://cc.lrz.de/) in a browser for the VM dashboard; no separate app is needed. Its sign-in form has User Name, Password and Domain. Use the activated LRZ project account/password and domain from your activation instructions. [Identity Management](https://idmportal2.lrz.de/) manages known personal LRZ accounts and passwords; use the account name rather than an email address. Identity-portal access does not establish Compute Cloud permission, and a failed cloud login alone does not establish that no account exists. If the project account is unknown, ask the chair's Master User or use [LRZ Service Desk Simple Submit](https://servicedesk.lrz.de/). No actual login/account outcome has been reported.

**LRZ option — checked 7 November:** the user's [linked access page](https://doku.lrz.de/zugriffsmoeglichkeiten-10614694.html) concerns webhosting. The [Compute Cloud FAQ](https://doku.lrz.de/faq-10745941.html) says students need a chair/research-group LRZ project account; ordinary TUM/LMU-managed accounts cannot be used directly. Ask the project's master user to provide an LRZ-managed account and enable Compute Cloud. Change the initial password in the LRZ identity portal, complete required policy acceptance, wait for activation/synchronization, then log into [cc.lrz.de](https://cc.lrz.de/) with the project account. Follow activation instructions for any domain field. No account or access is verified here.

**LRZ fit and funding:** lrz.large (4 vCPU/18 GiB) meets the provisional compute minimum. The [service description](https://www.lrz.de/service-center/alle-lrz-services/high-performance-computing/compute-cloud) lists 200 GB block-storage quota; count boot and data volumes together and confirm actual quota, Ubuntu 24.04 image and raw-plus-export storage fit. Public usage is normally paid: obtain actual prices and sponsorship, and have the master user set [project and user budgets](https://doku.lrz.de/budgeting-574291973.html) above zero with headroom for the full campaign and cleanup. Exhausting either applicable budget shelves the controller. LRZ offers best-effort availability and no automatic backups; retain verified off-machine results and private recovery state. This is a controller candidate, not a replacement for AWS/Azure/GCP benchmark pairs.

**After access and hosting review:** follow [LRZ Create a VM](https://doku.lrz.de/create-a-vm-10746085.html): Compute → Instances → Launch Instance; choose the verified Ubuntu image, flavor, persistent volume allocation, network, security group and SSH public key. Ubuntu's guest login is ubuntu with the corresponding private key. MWN plus laptop VPN access is preferable where sufficient; globally exposed inbound access is not required. Verify stable public egress and outbound cloud SSH/HTTPS, record the actual controller egress /32, and confirm expected result transfers are permitted under the [acceptable-use policy](https://doku.lrz.de/acceptable-use-policy-10745945.html). Laptop VPN connectivity must not be required for the controller's background execution. No VM or funded service was created by this review.

On your laptop:

```sh
ssh YOUR_USER@YOUR_RUNNER
```

**Ask Codex now:** review the real host/accounts, intended identity method, IAM/expiry permissions, quotas and funding before configuring paid execution. Share IDs and redacted settings, never keys/tokens. [Provider and failure details](campaign-developer.md) remain the reference for acceptance.

**Historical direct account review — 7 November (superseded by the current review above):** AWS Free plan has $100; EC2 catalog marks m7i.xlarge free-tier eligible=false and disables Launch with 'Instance type is not free tier eligible' (Europe/Stockholm catalog; Frankfurt quota/capacity not verified). GCP trial balance is €269.43/90 days, but its linked project differs from the currently selected project. The trial-linked project is absent from the current picker/search; selected-project Compute Engine API is disabled and compute quota filter returns no entries. Exact trial-project n2-standard-4/16-vCPU access therefore remains unverified. Azure screenshot lists only Azure for Students (Active/Owner) in the shown default directory/filter. No billing upgrade, API activation, quota request or resource creation was performed. Check GCP project IDs, not the duplicate project name; resolve visibility in the intended account before API activation and quota review. Review AWS Paid-plan terms before deciding on an upgrade. [Detailed findings and next diagnostic command](../../Thesis/tracking/EXECUTION_CHECKLIST.md#first-halle-test-result-and-direct-account-review--7-november-2026).

**Historical cloud preparation after terminal diagnostics — 7 November (repairs completed; see current update):** Terminal diagnostic received — 7 November: tested source f7c3d4cf569c00240af8bed33829834d82d2d127; sudo -n -l reports 'a password is required', so sudo entitlement remains unknown. Eleven failures are now backed by pasted JUnit traces: one /proc cleanup returns cancellation_unverified; four offline-gate tests fail (one explicitly on a nonexistent /Users default, with the other three requiring diagnosis); one replay peak is 3,149,008 bytes versus a 3,145,728-byte limit; five fake-provider projection tests report incomplete provenance/partial validity, with additional comparison-ineligible findings in the multi-provider cases. Missing Thesis context may contribute to provenance, but the cause and fix are not yet established. No code fix or test rerun has occurred. See the [exact cloud-account steps](../../Thesis/tracking/EXECUTION_CHECKLIST.md#cloud-preparation-after-terminal-diagnostics--7-november-2026): review AWS Paid-plan terms then Frankfurt standard quota; resolve GCP credit-bearing project access before API/N2/global quota checks, retaining trial unless blocked; retain Azure Students and inspect DSv5/regional quota in Germany West Central. These are recommendations, not performed upgrades or launches.

## 2. Install the controller and run all offline/native checks

**Halle trial — user-authorized 7 November:** start in your SSH session with bash, then use the home-directory recipe below. No admin approval is needed to try cloning, a private venv and credential-free tests; sudo is queried only to learn permissions. Downloads and writes stay under your home cnb directory. A failed sudo query does not block preparation. A pre-existing repository destination stops git clone rather than replacing it. Python's standard venv is tried first, with a PyPA zipapp fallback if ensurepip is absent. Keep the session open while this initial run executes.

**What this measures:** source revision, Python installation/test outcome, retained fixture/evidence size and quota. It is not a real guest result-size test. The previous saved macOS driver gate measured about 821 MB peak pytest fixtures; the first halle run retained 717 M evidence plus 18 M repository and 214 M venv; research volume remains unknown. Inspect all actual failures/skips: missing pwsh can skip the three Azure fake-runbook cases; missing real Thesis context can skip traceability. The two native /proc tests should run on Ubuntu. This first Python run is not the complete offline/static gate.

```bash
# Run inside the halle SSH session, in Bash.
set -euo pipefail
umask 077
export CNB_ROOT="$HOME/cnb"
mkdir -p "$CNB_ROOT"/{downloads,evidence,private,results}
CNB_EVIDENCE=$(mktemp -d "$CNB_ROOT/evidence/setup-XXXXXXXX")
export CNB_EVIDENCE
sudo -n -l > "$CNB_EVIDENCE/sudo-rights.txt" 2>&1 || true
cat "$CNB_EVIDENCE/sudo-rights.txt"

# A fresh clone: an existing destination makes git stop without replacing it.
git clone https://github.com/Frosendroska/cloud-network-benchmark.git "$CNB_ROOT/repository"
cd "$CNB_ROOT/repository"
git rev-parse HEAD | tee "$CNB_EVIDENCE/revision.txt"
git status --short

# Try the normal venv first; PyPA virtualenv is a user-space fallback.
if ! python3.12 -m venv "$CNB_ROOT/venv"; then
  python3.12 - <<'PYPA'
import hashlib, os, urllib.request
from pathlib import Path
p = Path(os.environ['CNB_ROOT']) / 'downloads/virtualenv.pyz'
with urllib.request.urlopen('https://bootstrap.pypa.io/virtualenv.pyz', timeout=60) as response:
    p.write_bytes(response.read())
print('Downloaded official PyPA zipapp, SHA256:', hashlib.sha256(p.read_bytes()).hexdigest())
PYPA
  python3.12 "$CNB_ROOT/downloads/virtualenv.pyz" "$CNB_ROOT/venv"
fi
. "$CNB_ROOT/venv/bin/activate"
python -m pip install --no-cache-dir -e '.[test,analysis]'
python -m pip check
python -m pip freeze > "$CNB_EVIDENCE/python-packages.txt"
python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts --check

# Run the full Python collection serially; retain failures and fixture files.
export MPLBACKEND=Agg
export MPLCONFIGDIR="$CNB_EVIDENCE/matplotlib"
set +e
nice -n 10 python -m pytest -q --basetemp="$CNB_EVIDENCE/pytest-work" \
  --junitxml="$CNB_EVIDENCE/pytest.xml" 2>&1 | tee "$CNB_EVIDENCE/pytest.log"
CNB_PYTEST_EXIT=${PIPESTATUS[0]}
set -e
printf '%s\n' "$CNB_PYTEST_EXIT" > "$CNB_EVIDENCE/pytest.exit"

# These sizes describe this installation and retained offline fixtures.
du -sh "$CNB_ROOT/repository" "$CNB_ROOT/venv" "$CNB_EVIDENCE"
quota -s
printf 'pytest exit: %s\nEvidence: %s\n' "$CNB_PYTEST_EXIT" "$CNB_EVIDENCE"
```

**Historical first result — screenshot reviewed 7 November (superseded by native pass above):** 1,559 passed, 11 failed, 4 skipped, one warning; pytest exit 1 after 16:06. Retained footprint totals 949 M; quota 13,584 M used of 51,200 M (~36.7 GiB remaining). Source hash f7c3d4c and JUnit assertions were subsequently supplied; see the diagnostic update above. Causes were subsequently repaired and native-tested at 3811619; retain this original failed attempt. Missing pwsh/Thesis explain the four displayed skips, not the failures. Keep the original evidence. Use the [read-only diagnostic recipe](../../Thesis/tracking/EXECUTION_CHECKLIST.md#first-halle-test-result-and-direct-account-review--7-november-2026); do not rerun the clone/bootstrap or claim native acceptance.

**After diagnosing/fixing the failures:** review the log and measured sizes, install verified user-space Terraform 1.16.6/PowerShell 7.5.11 matching the saved gate, rerun omitted cases and complete six locked Terraform roots/four mock suites. Provider CLIs follow before live acceptance: [AWS user-owned install paths](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html), [GCP archives](https://docs.cloud.google.com/sdk/docs/downloads-versioned-archives), and [Azure isolated-venv example](https://github.com/Azure/CloudShell/blob/master/docs/azcli-workaround.md). Verify actual halle installations rather than treating these methods as host acceptance. The [Thesis checklist](../../Thesis/tracking/EXECUTION_CHECKLIST.md#halle-installation-trial--authorized-7-november-2026) records phases and size boundaries. Keep RAM and disk separate: current RAM is ample; the 200 GB disk estimate remains provisional until real native growth and retained-copy headroom are measured. Do not add another paid pilot or reduce retained V2 outputs to fit storage.

**Linux repair versus installation rights — 7 November:** Linux failure qualification — 7 November: source inspection at the exact tested f7c3d4c confirms _stop_remote_f03 passes nine arguments (including timeout) while its embedded Python unpacks sys.argv[1:] into eight variables, causing an argument-unpacking error before the guarded stop logic. This is an implementation defect, not evidence of missing sudo. Earlier macOS validation skipped this Linux /proc case. The replay assertion exceeds its 3 MiB limit by 3,280 bytes; this does not indicate insufficient host RAM. Five provenance failures mean at least one of resolved config hash / implementation commit / design commit is absent; determine which, rather than assuming a package is missing or fabricating provenance. Four gate tests expose setup/portability assumptions, including the explicit nonexistent /Users path; diagnose all four. None has been fixed or rerun. Recommendation: use one focused repair agent in the dedicated cloud-network-benchmark project, with the pasted diagnostic report and tested hash. Reproduce and fix portable gate fixtures, complete genuine/fake-test provenance appropriately, repair scoped cleanup while preserving process ownership/PID-reuse protections, and investigate bounded-memory behavior without merely loosening the threshold. Require affected tests followed by the appropriate full/native suite and a no-sudo halle handoff. No paid cloud execution, protocol change, sudo/service modification, or weakening of validation is authorized; do not claim a native pass from macOS. No repair agent was spawned by this review. Missing PowerShell and real Thesis context explain four skips; Terraform/cloud CLI installation and persistent service support remain separate setup work. Use reviewed home-directory tool paths and qualify ITO-supported reboot/logout recovery. [Detailed repair handoff](../../Thesis/tracking/EXECUTION_CHECKLIST.md#linux-failures-versus-installation-rights--7-november-2026).

**Dedicated/sudo-backed controller route:** the installation and service commands below retain /srv/cnb and /etc paths. They do not apply unchanged to this halle home-directory trial. Persistent service/settings adaptation, linger/logout/reboot behavior and the 11 November maintenance remain required before unattended research; they do not block this first setup/test phase.

On the runner, as the normal user, install [Terraform](https://developer.hashicorp.com/terraform/install), [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html), [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli-linux?pivots=apt), [Google Cloud CLI](https://docs.cloud.google.com/sdk/docs/install-sdk) and [PowerShell 7](https://learn.microsoft.com/en-us/powershell/scripting/install/install-ubuntu) using their official Linux instructions. PowerShell is needed for Azure offline checks. Preserve committed Terraform lock files; do not run `init -upgrade`.

```sh
sudo apt-get update
sudo apt-get install -y python3.12-venv git rsync openssh-client ca-certificates
sudo install -d -o "$USER" -g "$(id -gn)" /srv/cnb
git clone https://github.com/Frosendroska/cloud-network-benchmark.git /srv/cnb/repository
cd /srv/cnb/repository
git rev-parse HEAD
python3.12 -m venv /srv/cnb/venv
. /srv/cnb/venv/bin/activate
python -m pip install -e '.[test,analysis]'
mkdir -p /srv/cnb/private /srv/cnb/results /srv/cnb/evidence /srv/cnb/credentials
chmod 700 /srv/cnb/private /srv/cnb/credentials /srv/cnb/evidence
```

Run before putting cloud credentials on this fresh VM. These commands download dependencies/providers but do not apply Terraform, create resources or send benchmark traffic. Use Bash, stop on any failure, and retain the evidence directory printed by `date`:

```bash
bash
set -euo pipefail
cd /srv/cnb/repository
. /srv/cnb/venv/bin/activate
CNB_EVIDENCE="/srv/cnb/evidence/native-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir "$CNB_EVIDENCE"
git rev-parse HEAD > "$CNB_EVIDENCE/revision.txt"
python -m pip freeze > "$CNB_EVIDENCE/python-packages.txt"
terraform version > "$CNB_EVIDENCE/terraform-version.txt"
pwsh -NoProfile -Command '$PSVersionTable.PSVersion.ToString()' > "$CNB_EVIDENCE/powershell-version.txt"
python -m pytest -q --junitxml="$CNB_EVIDENCE/pytest.xml" 2>&1 | tee "$CNB_EVIDENCE/pytest.log"
python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts --check
for root in aws/pair aws/safety azure/pair azure/expiry gcp/pair gcp/protection; do
  terraform -chdir="terraform/$root" init -backend=false -input=false -lockfile=readonly
  terraform -chdir="terraform/$root" fmt -check -recursive
  terraform -chdir="terraform/$root" validate -no-color
done 2>&1 | tee "$CNB_EVIDENCE/terraform-static.log"
for root in azure/pair azure/expiry gcp/pair gcp/protection; do
  terraform -chdir="terraform/$root" test -no-color
done 2>&1 | tee "$CNB_EVIDENCE/terraform-mocks.log"
git diff --check
git status --short
```

Inspect `pytest.xml`/log: on native Ubuntu the two `/proc` cases must run, not remain skipped. Investigate failures and unexpected skips; do not weaken tests. Native guest builds and real SSH/providers still need acceptance. The saved macOS gate was 1,572 passed / two declared Linux skips / 20 checks; [its full required-coverage/mirror procedure](validation/campaign-driver.md) is a developer audit, not a portable Ubuntu setup script (its mirror check currently expects `darwin_arm64`). Record actual Ubuntu results; do not copy the macOS count as proof. Exit this Bash shell when finished.

## 3. Store credentials, settings and the service

**Halle boundary:** this host has no sudo. Commands below using `/srv/cnb`, `/etc/cloud-network-benchmark` or `sudo loginctl` are generic administrator-supported examples, not ready-to-run halle instructions. Adapt reviewed private paths, service unit, environment and explicit settings selection to the actual home-directory workspace, and qualify ITO-supported logout/boot/maintenance recovery before unattended execution. Do not mark these requirements complete from the passing offline gate.

**Latest account/setup update — 8 November:** native strict gate passed at `e4f0f2d`; the successful summary ZIP is independently inspected. Resolve AWS quota-read denial and finish actual machine/quota/project access, private renewing identities and the no-sudo service setup before real cloud acceptance. All three test alerts remain saved; review their amounts/current spend and complete-workflow costs before real stages. No account upgrade, credential grant or launch was performed. [Current evidence/next actions](../../Thesis/tracking/EXECUTION_CHECKLIST.md#native-offline-gate-passed--8-november).

Use provider-supported identities with renewal through the last WEEK slot **and cleanup**: [AWS Roles Anywhere/profile credential process](https://docs.aws.amazon.com/rolesanywhere/latest/userguide/credential-helper.html), [Azure service-principal certificate](https://learn.microsoft.com/en-us/cli/azure/authenticate-azure-cli-service-principal), [GCP Workload Identity Federation](https://docs.cloud.google.com/iam/docs/workload-identity-federation). Have Codex adapt setup to your actual permitted identity provider; there is no universal account-creation/IAM command safe for every institution. Verify both CLI and Terraform credentials, not just a terminal login. Interactive SSO alone is insufficient for WEEK.

After approved identity setup, keep its stores/certificate keys outside Git/results. For example, AWS profile `cnb` uses its reviewed credential process; Azure's reviewed certificate contains its private key and certificate; the GCP file is the actual federation configuration:

```sh
umask 077
ssh-keygen -t ed25519 -f /srv/cnb/credentials/id_ed25519
# Use a dedicated key available non-interactively to the service; protect its directory.
export AWS_PROFILE=cnb
az login --service-principal --username ACTUAL_APP_ID --certificate /srv/cnb/credentials/azure.pem --tenant ACTUAL_TENANT_ID
az account set --subscription ACTUAL_SUBSCRIPTION_ID
gcloud auth login --cred-file=/srv/cnb/credentials/gcp-wif.json
gcloud config set project ACTUAL_PROJECT_ID
export GOOGLE_APPLICATION_CREDENTIALS=/srv/cnb/credentials/gcp-wif.json
aws sts get-caller-identity
az account show --query '{subscription:id,tenant:tenantId}' --output json
gcloud projects describe ACTUAL_PROJECT_ID --format='value(projectId)'
```

`authentication_reference` in settings is the absolute benchmark **SSH private-key path**, not a cloud token. Do not overwrite an existing key/settings file when resuming this runbook. Do not paste private keys into Codex.

```sh
sudo install -d -o "$USER" -g "$(id -gn)" /etc/cloud-network-benchmark
install -m 600 plans/operator-settings.example.yaml /etc/cloud-network-benchmark/operator.yaml
install -m 600 /dev/null /etc/cloud-network-benchmark/controller.env
editor /etc/cloud-network-benchmark/operator.yaml
editor /etc/cloud-network-benchmark/controller.env
mkdir -p ~/.config/systemd/user
install -m 644 deploy/cloud-network-benchmark-campaign.service ~/.config/systemd/user/
sudo loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user enable cloud-network-benchmark-campaign.service
```

Fill **one operator YAML** with actual account/image IDs, controller `/32`, SSH paths, measured interface names, quoted complete-workflow prices, funding, allowances and review-source paths. Nulls/false confirmations deliberately block launch. Use the [field/evidence table](campaign-developer.md#one-settings-file-real-evidence). Confirm an approval only after its named review exists. The driver generates internal JSON/configs; do not handcraft runtime manifests.

`controller.env` uses systemd `NAME=value` lines (no `export`), with actual absolute credential references, for example `AWS_PROFILE=cnb`, `GOOGLE_APPLICATION_CREDENTIALS=/srv/cnb/credentials/gcp-wif.json`, and `AZURE_CONFIG_DIR`/`CLOUDSDK_CONFIG` if custom. Terraform may need additional identity-specific variables; review those with Codex. The service does **not** inherit SSH-shell exports. Prove renewal beyond initial token lifetime in the service environment and retain evidence before setting `credential_refresh.confirmed`.

For persistent host logs, ask the controller administrator to enable bounded persistent journald storage (e.g. `Storage=persistent`, `SystemMaxUse=512M` in a journald drop-in; see [systemd settings](https://www.freedesktop.org/software/systemd/man/latest/journald.conf.html)). Preserve private journals and the shared ledger in an access-controlled off-machine backup, separately from public results; these contain recovery-sensitive paths/state. Never publish that backup. A results export is not a recovery-state backup.

## 4. Run acceptance, calibration and DAY with reviews

After every new SSH login:

```sh
export PATH="/srv/cnb/venv/bin:$PATH"
cd /srv/cnb/repository
cloud-network-benchmark campaign check --plan plans/acceptance.yaml
```

`check` creates no resources; it checks inputs, tools/storage and bounded read-only identities. It does not prove IAM/quota/expiry or readiness. **Ask Codex to review the check, native test logs and real authorization/price evidence before this first paid command:**

```sh
cloud-network-benchmark campaign start --plan plans/acceptance.yaml --execute
cloud-network-benchmark campaign status
cloud-network-benchmark campaign logs --follow
```

Acceptance runs V-S1/V-S3 at 10 seconds/phase, N=4, then waits for supervised failure entries. Verify actual AWS Cluster Placement Group, Azure **Standard** PPG and GCP **COLLOCATED** policy membership; inspect native data and full deletion. Follow the [physical failure/controller-loss procedure](campaign-developer.md#supervised-registered-failure-checks), including original expiry deadlines and independent absence evidence, before continuing each attempt:

```sh
cloud-network-benchmark campaign continue --stage-id ACTUAL_STAGE_ID --entry-id V-A-FAIL --confirm-supervision
# Repeat explicitly for V-Z-FAIL and V-G-FAIL after the previous attempt is reviewed/recovered.
```

During supervised acceptance, disconnect/reconnect SSH and verify the same session/journal continues; exercise supervisor restart under the physical-loss procedure. Do not improvise restart/power-loss tests during research. Normal success alone does not pass a failure check.

**Review acceptance/native builds/renewal/cleanup with Codex**, then prepare the future start and measured allowance in the operator file:

```sh
cloud-network-benchmark campaign check --plan plans/calibration.yaml
cloud-network-benchmark campaign start --plan plans/calibration.yaml --execute
```

Calibration uses one separate retained pair/cloud, 1/2/4/8 streams × three loaded 60-second trials in fixed order. Download the closed stage (step 6). On the **laptop**, use the downloaded calibration stage's final selection (`CNB_LOCAL` is its local directory):

```sh
python - "$CNB_LOCAL/export/selection.json" > calibration-results.json <<'PY'
import json, sys
from pathlib import Path
selection = Path(sys.argv[1])
rows = json.loads(selection.read_text())['runs']
if len(rows) != 3:
    raise SystemExit('Expected three cloud calibration results; review missing/extra runs')
results = [json.loads((selection.parent / row['validation_result']).read_text()) for row in rows]
print(json.dumps(results))
PY
cloud-network-benchmark lifecycle recommend-common-n --validation-results calibration-results.json --format json > common-n.json
```

**Ask Codex to review/freeze common N and storage/export evidence**; copy the reviewed recommendation back and set `common_n_recommendation`. Fill future DAY start/reviews and check before launch:

```sh
cloud-network-benchmark campaign check --plan plans/day.yaml
cloud-network-benchmark campaign start --plan plans/day.yaml --execute
```

DAY uses separate retained pairs, uninterrupted 86,400-second aggregate TCP, local RTT chunks ≤300 seconds and one-second diagnostics. Its raw files are retrieved **after measurement**. A live controller log does not prove complete guest data. Before launch, measure both guests' file growth/free space from acceptance/calibration, conservatively project all output to 24 hours plus overhead, verify 32 GiB guest disks and the 20 GiB/cloud export allowance, and demonstrate checksum-verified transfer within the cap. Controller free space must cover original data **and closed exports** plus headroom (at least twice projected raw bytes, and retained weekly data/software). 200 GB is provisional; enlarge before admission if needed. No automatic deletion or disk rescue is provided.

## 5. Start WEEK and observe it

**Review DAY with Codex:** full TCP duration, ≥99% RTT time coverage, missing data, sustained throughput, costs, exact four UTC windows and feasible seven-day dates. Incomplete DAY requires review; no automatic repeat. Set `first_day_utc`, four `windows_utc`, reviewed allowances/authorization; keep common N fixed.

```sh
cloud-network-benchmark campaign check --plan plans/week.yaml
cloud-network-benchmark campaign start --plan plans/week.yaml --execute
```

WEEK makes 28 P0/S1 + 28 P1/S3 independent all-cloud calls, concurrent in the same slots, with fresh resources and cleanup after each observation (168 child runs total). `start` hands off to systemd and returns. Leave SSH normally; the controller must stay available. Missed slots stay missed; uncertain launches/cleanup liabilities block new work. No automatic paid replacements or P2/P3.

Run these after the first slot and at least daily; inspect count changes after each expected slot:

```sh
cloud-network-benchmark campaign status
cloud-network-benchmark campaign logs --follow
systemctl --user status cloud-network-benchmark-campaign.service --no-pager
journalctl --user -u cloud-network-benchmark-campaign.service --since today --no-pager
# Add -f to journalctl for live service output; Ctrl-C only stops the viewer.
date -u
timedatectl status
df -h /srv/cnb/results /srv/cnb/private
du -sh /srv/cnb/results/*
```

| Evidence | Location / meaning |
| --- | --- |
| Controller events | `/srv/cnb/private/campaign.log`; rotates at 5 MiB, three backups |
| Service stdout/restarts | `journalctl --user -u cloud-network-benchmark-campaign.service`; retention depends on host journald |
| Detailed entry output | `/srv/cnb/private/STAGE_ID/ENTRY_ID.log` |
| Durable launch/cleanup state | `/srv/cnb/private/STAGE_ID/campaign-journal.json`, per-entry `lifecycle-recovery.json`; keep private |
| Original native results | `/srv/cnb/results/STAGE_ID/raw/RUN_ID/`; closed copies include native files, diagnostics, manifests, config/schedule, validation and checksums |
| Ready midweek | `/srv/cnb/results/STAGE_ID/closed/ENTRY_ID/export/selection.json` after entry `export_status: verified` |
| Final whole-stage selection | `/srv/cnb/results/STAGE_ID/export/selection.json`, only after stage settles |

Status reports expected/completed/failed/missed counts, current/next slot, disk space, cleanup liabilities and per-entry export/backup outcomes. Check missing/failed observations and actual UTC times, not only whether the service is running. Native files never rotate. Optional `backup_destination` copies closed exports; verify the mount is physically off-machine and test checksum/readback. `notification_hook` is opt-in, at-most-once and can miss delivery on a crash; test it and continue checking status. Neither option protects in-progress guest files. No CloudWatch or new telemetry service is required.

## 6. Download and analyse while WEEK continues

On the **laptop**, install the same framework revision in a Python 3.12 venv and `python -m pip install -e '.[analysis]'`. Work outside Git-managed datasets (the checkout's `results/processed/` is ignored). Get actual stage/entry IDs from status. For **one closed entry with verified export**:

```sh
CNB_STAGE=ACTUAL_STAGE_ID
CNB_ENTRY=ACTUAL_ENTRY_ID
CNB_LOCAL="$HOME/cnb-data/$CNB_STAGE"
mkdir -p "$CNB_LOCAL/closed/$CNB_ENTRY"
rsync -av --partial YOUR_USER@YOUR_RUNNER:/srv/cnb/results/$CNB_STAGE/closed/$CNB_ENTRY/export/ "$CNB_LOCAL/closed/$CNB_ENTRY/export/"
python - "$CNB_LOCAL/closed/$CNB_ENTRY/export" <<'PY'
import sys
from pathlib import Path
from cloud_network_benchmark.campaign_export import verify_export
if not verify_export(Path(sys.argv[1])):
    raise SystemExit('Copy integrity failed: recopy and investigate before analysis')
print('Export and native checksums verified')
PY
CNB_REPORT="results/processed/$CNB_STAGE-$CNB_ENTRY-$(date -u +%Y%m%dT%H%M%SZ)"
cloud-network-benchmark analyze --selection "$CNB_LOCAL/closed/$CNB_ENTRY/export/selection.json" --output "$CNB_REPORT"
open "$CNB_REPORT/index.html" # macOS; Linux: xdg-open
```

Repeat for other closed entries whenever wanted. Copying/checking these closed files does not touch active guests or stop WEEK. Do not copy active `raw/` as a validated snapshot. If an export contains only partial failure evidence, it may have no analysable selection: preserve it and review the failure rather than manufacturing one. Final stage copying can use `rsync -av --partial ...:/srv/cnb/results/$CNB_STAGE/ "$CNB_LOCAL/"`, then verify the final `export` and analyse its `selection.json`; keep `closed/` alongside it because the final selection references those bundles.

For a combined **interim** view after copying several closed entries, create a new local selection; this verifies every copied export and preserves source files:

```sh
CNB_SELECTION="$CNB_LOCAL/interim-$(date -u +%Y%m%dT%H%M%SZ).json"
python - "$CNB_LOCAL" "$CNB_SELECTION" <<'PY'
import json, os, sys
from pathlib import Path
from cloud_network_benchmark.campaign_export import verify_export
root, target = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
runs = []
derived = target.parent / (target.stem + '-inputs')
derived.mkdir()
for selection in sorted((root / 'closed').glob('*/export/selection.json')):
    if not verify_export(selection.parent):
        raise SystemExit(f'Incomplete/corrupt copy: {selection.parent}')
    for row in json.loads(selection.read_text())['runs']:
        source = selection.parent / row['validator_input']
        descriptor = json.loads(source.read_text())
        for key in ('campaign_manifest_path', 'child_manifest_path', 'child_result_root'):
            value = descriptor.get(key)
            if isinstance(value, str) and value and not Path(value).is_absolute():
                descriptor[key] = str((selection.parent / value).resolve())
        rebased = derived / (selection.parent.parent.name + '-' + source.name)
        rebased.write_text(json.dumps(descriptor, indent=2))
        runs.append({'validator_input': os.path.relpath(rebased, target.parent),
                     'validation_result': str((selection.parent / row['validation_result']).resolve())})
if not runs:
    raise SystemExit('No verified closed observations selected')
with target.open('x') as stream:
    json.dump({'format_version': 1, 'runs': runs}, stream, indent=2)
print(f'{len(runs)} child runs selected; keep cutoff and failed/missed counts with report')
PY
cloud-network-benchmark analyze --selection "$CNB_SELECTION" --output "results/processed/$CNB_STAGE-interim-$(date -u +%Y%m%dT%H%M%SZ)"
```

Reports contain `report.json`, seven normalized CSV tables, PNG/SVG plots and offline `index.html`; see [analysis semantics](native-analysis.md). Retain the cutoff, selected IDs, expected/completed/failed/missed counts, framework/analysis revision and validation findings. Interim selections above contain no cost-ledger snapshot: unknown costs stay unavailable; use the private ledger/provider billing for operational budget review. Start provisional figures and writing, check data quality early, and reconcile the entire expected sample before final conclusions. Do not adapt N/windows or stop early because of interim performance. Keep originals until verified laptop and second copies exist; never sync private credentials/state into public results or commit data.

## 7. If something fails; finish safely

Bring Codex the status, redacted relevant logs, entry IDs, source revision and local report; never credentials. Stop before further spending when a decision is needed: failed acceptance/renewal, inadequate storage, incomplete DAY, changed price evidence, cleanup liability, replacement request, or infeasible dates. Preserve the original ledger, journal and frozen settings; do not bypass admission or reset reservations.

For cleanup use the **actual saved** recovery path/session reported by private prepared state, following the [recovery procedure](campaign-developer.md):

```sh
cloud-network-benchmark lifecycle cleanup --recovery /srv/cnb/private/ACTUAL_STAGE_ID/ACTUAL_ENTRY_ID/lifecycle-recovery.json --controller-session ACTUAL_SAVED_SESSION --format json
```

This is live cleanup, not measurement. Verify all experiment VMs/disks/addresses/support resources are absent; compute expiry is only a fallback. Never target the controller or delete recovery state to unblock a stage. Finalize verified downloads, failure/missing-slot and cost reconciliation, full resource absence and final reports with Codex. Then update Thesis run references and canonical findings. P2/P3 and paid replacements require separate decisions. Controller and benchmark-disk loss before copying remain risks; local logs and offline tests cannot eliminate them.
