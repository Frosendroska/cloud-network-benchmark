# EUR financial safeguards audit and repair

Authorized offline packet: Thesis `tracking/IMPLEMENTATION_PROMPTS.md`, 10 November 2026.
Baseline: c24d27ce7071c0b890d6304f75747a6ac9674db3. Branch: codex/eur-budget-cash-guard.
Policy: [Thesis financial rules](../../../Thesis/tracking/BUDGET_RULES.md).

## Baseline findings, before source changes

Real F11 `BudgetLedger.reserve` rejected absent paid_authorization, but accepted identity-only,
expired (2000), wrong-provider/config/count zero and explicit EUR gross/cash zero evidence. All used
14 October prices at 10 November with the refresh flag false. Source/envelope/support validation and USD ledger controls
already existed and are retained. Existing focused F11 tests: 59 passed.
Synthetic reproduction only: `/private/tmp/cnb-SYNTHETIC-budget-audit.py` and results JSON.
No live approval, ledger, cloud/API/resource or account changes occurred.

SCP counterexample: honest 12-byte two-VM inventory, four copies including retries wrote 2,097,164 bytes,
then returned complete=True. Integrity and one retry are not cumulative transfer enforcement.
Controller restart already restricts normal resume to cleanup/reconciliation; do not describe this
as unrestricted paid restart.

## Implemented and unresolved controls

| Control | Implemented behavior / limitation |
|---|---|
| Human approval | Strict source-card schema; wrapper fields cannot supply authority. Exact config/resolved/runtime/code/template/price/run/provider scope and two-VM resource bindings; unique child-run count; aware validity window through cleanup. Manual card ownership is the trust boundary, not cryptographic human authentication. |
| Price/FX | Source hashes re-read; quote age<=24h, future checks rejected; checked date agrees with each priced envelope. Canonical sourced USD-to-EUR FX capture must agree with card, including timestamp/rate/validity. Official URLs record the human review; offline code cannot establish that an online price was actually official/current. |
| EUR gross | Confirmed whole-thesis maximum EUR 300; explicit working/protected/provider/stage/run/fixed-batch caps, no recommendation defaults. External thesis liabilities, tax/uncertainty components, FX/card fee rate and fixed fees included; liabilities round up, caps down. Existing stricter USD 250/300/50 checks remain additional controls, not EUR labels. |
| Personal cash | Separate explicit ceiling and stored cash exposure. Full gross counts as cash; credit and reimbursement offsets are unsupported and rejected. This conservatively forgoes credit benefits and does not assume reimbursement was received. |
| Shared history | Existing atomic flock/fsync ledger; concurrent P0/P1 reservations share EUR/cash accounting. Original maximum exposures and external high-water marks remain after cleanup/reconciliation. Legacy unconverted history blocks new financial admission; it is not reset/migrated automatically. This can overcount final charges and intentionally block later work. |
| New stages | Approval, immutable source hashes, validity through cleanup and shared headroom are revalidated before non-cleanup stages/creation claims. Quote freshness is checked at reservation and again at each new provider creation claim; the claim time is stored in the ledger so a long DAY run retains launch-time proof instead of requiring a new quote mid-run. Generic old paid records cannot grant new creation. |
| Cleanup | No new quote/approval requirements in constructor/inspect/reconcile or shared provider settings. Existing original-scoped cleanup/recovery remains available after expiry, stale prices, missing card or exhausted launch allowance. |
| Cumulative transfer | Paid source envelopes now require a positive aggregate payload cap per two-VM provider run and a separate positive protocol/control overhead allowance and USD price within the priced GB-egress component. The ledger durably pre-debits each declared maximum before artifact inventory, including partial/cancelled attempts and retries, under its shared lock. A cap-crossing attempt is refused before payload and blocks further paid work; reaching the cap exactly permits no further payload for that provider. The paid copy uses SSH without a PTY, a remote byte-limited stream with one-byte excess probe and EOF proof, and a local byte-limited binary reader; published files still require exact size/hash and materialized verification. Workspace inventory and other control messages are priced by the explicit overhead input, but their billable wire bytes are not measured or hard-bounded. This is payload enforcement, not a wire-byte guarantee. |
| Preparation/presentation | Local preparation skips provider APIs and grants no authority; exact prepared identities need later review. Hero dated 10 November states EUR 300 all-in and proposed EUR 250/EUR 50 recommendations, with policy link. Existing user Markdown/renderer changes preserved. |

## Validation

| Scope | Revision | Recorded result |
|---|---|---|
| Halle full strict offline gate | `f5073d6add68ec0081b0ab0333930a5a26697180` | 20/20 checks passed; 1,699 pytest cases, zero failures/errors/skips; 112 required identities, no coverage gaps; source unchanged. 721.32 seconds. |
| Halle financial/runtime delta | `ae8c2a11b3f3cb97d5dd70187a601c03d93cf747` | 53 affected tests passed, zero failures/skips; schema, renderer and diff checks passed; source unchanged. |
| macOS full pytest | `ae8c2a1` code | 1,695 passed, five platform skips, one existing tarfile deprecation warning; 722.41 seconds. Two skips require Linux /proc and three require PowerShell. |
| Local focused financial/runtime tests | `ae8c2a1` | 53 passed in 1.01 seconds. |
| Local bounded-transfer and affected admission/retrieval/F11 tests | Bounded-transfer revision after `2804674`, including creation-time quote check | 267 passed in two affected groups (193 in 95.05 seconds; 74 in 0.84 seconds), zero failures/skips, including 18 new boundary cases. Python 3.13.7 on macOS; GNU coreutils `dd` 9.2 was used for exact, truncated and oversized remote-script cases. Contract schema `--check` and `git diff --check` passed. |
| Halle full strict offline gate after bounded transfer | `572bbe1af1ccc472e0efa2f8964ebbe47b109327` | **Failed:** 1,704 passed, 14 failed, zero skips/errors, one warning. All 14 failures were old F09 fake runners lacking `run_bounded_copy`; the new transfer boundary cases passed. The first failed pytest check stopped the gate, leaving its 19 other checks unrun. Original isolated source and evidence remain unchanged. |
| Halle affected native F09/transfer rerun | `0378ce9e9d9774aa01658fa8bae42c4dce9fad34` | **Passed:** 34 tests, zero failures/errors/skips, in 854.42 seconds. All 14 test IDs that failed in the original full gate passed. Isolated source was clean before and after the run; log and JUnit hashes verified. This is an affected rerun, not a passing full strict gate. |

The later delta includes F11's upward USD rounding before EUR conversion and retains fees when
revaluing prior unbilled dollars at a higher reviewed FX rate. The earlier passing full gate was preserved;
only the affected 53 tests were run for the delta. No unchanged full suite was repeated for it.
Linux tools: Python 3.12.3, Terraform 1.16.6, PowerShell 7.5.11; genuine Thesis revision
`64de99536074095acbc75c0f57e24ce0cc5b9e4c`. Both verification checkouts were isolated. The normal
`~/cnb/repository` checkout remains `c24d27c`; this feature has not been deployed or pushed.

Evidence: `/u/halle/brek/home_at/cnb/evidence/eur-guard-final-TQg2m1KM`.
The full gate is under `gate/`; latest delta is `precision-summary.json`, `precision-pytest.xml`
and `precision-*.log`. Every full-gate log hash and JUnit hash was checked before packing
`financial-summary.zip` (SHA-256 `31457413665aaae85867564a300fc0a3f64327e4f7c73fc03e89d9a112f9d14d`).
The runner's `native_linux_verified` and `cloud_verified` flags remain false; the current repair
has an affected native rerun, not a passing strict full gate or live provider acceptance.

For this bounded-transfer revision, the old Halle control socket was initially absent and a fresh
batch SSH attempt was denied by authentication. The authorized connection was later restored.
The original failed full-gate evidence remains at
`/u/halle/brek/home_at/cnb/evidence/transfer-native-zuuz72jr/gate`; its `summary.json` records
the 19 unrun checks, and `pytest.xml` and `01-full-pytest.log` retain the 14 failures. Do not
describe this gate as passing. The separate repair run is at
`/u/halle/brek/home_at/cnb/evidence/transfer-native-fix-jcTFi8DX`; its `pytest.log` SHA-256 is
`e29befbd76bfbd49963cddce4edc0c752f4a34d4d4aa549762283e5f8b6fce30` and `pytest.xml`
SHA-256 is `7e6739e88dd8206f5f52c5eaf2e2d95aaf1c223db4827544042ddacebc6102ef`.
The test-only repair supplies bounded fake SSH copies and source-shaped synthetic transfer terms;
it does not alter the production transfer guard. No cloud/provider call was made for these tests.

An earlier full run at `5b9189b` had 1,697 passes and one incomplete synthetic runtime-provenance
fixture failure (zero skips); its original evidence is retained at
`/u/halle/brek/home_at/cnb/evidence/eur-guard-w3KSQ3p6/gate`. `f5073d6` completed that fixture and
added binding assertions without relaxing production checks. An earlier local controller-interrupt
timing failure passed its focused rerun and the later complete Linux and macOS suites.
The SSH connection briefly closed before the passing native summary was recovered; its successful
result was then read and hashes checked, rather than rerunning it because the connection had closed.

New financial tests exercise the real guard using isolated synthetic files. Historical fake provider
stage tests explicitly mock the new financial boundary so they can continue testing their lifecycle
subject; those mocks do not establish financial readiness. Production has no bypass flag.
The runbook was regenerated and its deterministic check passed both locally and in the committed
Halle delta. Pre-existing user Markdown, renderer and old validation-report edits remain unstaged.

## Actual launch prerequisites

Do not create a paid approval or new live ledger from these fixtures. Supply an approved source-bound
aggregate payload cap per provider run (both VMs and all attempts), a conservative separately priced
protocol/control allowance, and an egress component quantity/price covering both. Partial attempts
consume their full declared maximum without refund, so leave room for retries.
`pricing.result_transfer.<category>.<provider>` must provide `payload_limit_bytes`,
`protocol_overhead_allowance_bytes` and `protocol_overhead_priced_usd` for each prepared category
and provider; its `traffic_export` component uses `GB-egress`. The numeric inputs must come from
reviewed output-growth and price evidence; the synthetic values in tests are not approvals.
Then supply actual human-approved exact prepared entries/counts/resource scope,
gross/working/protected/provider/stage/run/batch and personal unreimbursed-cash caps,
source-backed reconciled external liabilities, current
prices/FX/tax/fees and original cleanup/lifetime/expiry evidence. No numerical personal cash allowance
has been authorized. Old liabilities need explicit conversion/reconciliation without lost history.
Credits/reimbursement cannot lower this implementation's conservative cash total. Provider acceptance,
permissions, independent expiry and real billable-resource absence remain separate authorized stages.
No bank-charge guarantee follows from offline accounting or provider budget alerts.
