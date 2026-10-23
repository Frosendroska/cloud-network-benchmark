# F11 schedule and budget admission

F11 adds local-only schedule inspection and a private JSON budget ledger. The library is an admission boundary for the later F09 lifecycle; it does not schedule workers, call clouds, retrieve results, delete resources or run benchmark traffic. A reservation is durable before the caller can register provider expiry. The cap begins at reservation activation and stays fixed.

## Offline entry points

Inspect existing configurations against saved CSV rows:

```sh
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis \
  /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark.admission --help
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis \
  /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark.admission preview \
  --schedule tests/fixtures/f11/schedule-pending.csv \
  --config-map tests/fixtures/f11/config-map.json \
  --cost-inputs tests/fixtures/f11/cost-inputs.json --now 2026-10-22T00:00:00Z
```

Preview emits deterministic JSON. Exit 0 means the local input can be inspected, including when study dates/N/prerequisites remain pending. It does not mean paid eligibility. Malformed input or an inspection with schedule errors/namespace collisions exits 2 and includes safe deterministic reason identities. Preview never creates a ledger or performs an external call.

The API lives in `cloud_network_benchmark.scheduling` (`load_schedule`, `inspect_schedule`, `assess_provider_skew`) and `cloud_network_benchmark.admission` (`load_cost_inputs`, `BudgetLedger`, `aws_expiry_cost_handoff`, `bounded_copy_handoff`). Callers supply configuration mappings, local ledger path, UTC and monotonic clocks, source-backed allowances, identities and gate evidence. `BudgetLedger.initialize` is explicit and exclusive; a missing, corrupt or wrong-scope initialized ledger fails closed. All cooperating calls use the same private local JSON path and controller session ID. POSIX `flock`, mode 0600, atomic replace and fsync serialize one local controller; this does not support distributed controllers.

## Admission policy

Primary schedule validation represents 28 P0/S1 and 28 P1/S3 short experiments as 28 matching paired slots across seven consecutive dates and four shared named windows. Each slot requires four VMs per cloud across the concurrent studies. The schedule records intent; it does not allocate resources or claim quota. Unknown dates and common N remain pending. A target missed before activation stays missed and does not cause catch-up work. Calibration and DAY retain one experiment row/reservation; their trials or probe chunks do not multiply launches.

Skew uses actual UTC phase starts. Up to and including 60 seconds meets the target; above 60 through 300 seconds remains comparison-eligible with `provider_skew_target_missed`; above 300 seconds is excluded; absent starts are unavailable. This assessment does not modify execution or data-validity status.

Money uses finite nonnegative decimal strings. Each candidate envelope names a dated one-run/two-VM per-provider cost source, exact selected regions, units and inclusion rules for compute, disks, addresses, traffic/export, tax, uncertainty and expiry/cleanup support. Its component totals must equal the request estimate and the funding bucket moves. Synthetic envelopes are offline test evidence only; paid requests require rows bound to the saved provider-price input. Reservation funding is moved from named remaining buckets into one complete-workflow ceiling; final actual cost plus active unspent ceiling is counted once. Working commitment cannot exceed $250, with the $50 protected buffer inside the $300 total. Unknown cleanup or charges remain liabilities. Stop, deallocation and expiry do not prove deletion. Same-session independent P0/P1 reservations may coexist; a changed controller session with unresolved work and any explicit failed/unknown cleanup block new launches. Recovery requires sourced reconciliation.

Caps are 1,200 seconds for acceptance/short studies, 1,800 seconds for calibration and 87,600 seconds for diagnostic day. A sourced allowance must include registration, provisioning, readiness, the fixed 60-second stabilization, mode minimum plus shutdown grace, retrieval and cleanup. The whole workflow and remaining slot wait must fit before the immutable monotonic cap and earliest provider trigger. Wall-clock UTC is retained as evidence and does not reset those bounds. Every still-required future stage allowance must remain present until its explicit completion. Durable reservation activation opens cleanup uncertainty before provider expiry registration; registration, creation and cleanup preserve that ordering. AWS's dated expiry-support component is $0.0139 per involved region ($0.0278 for two); `aws_expiry_cost_handoff` prepares the evidence consumed by AWS's `complete_workflow_cost_handoff` setting, binding the separate support fixture and complete reservation source. That component does not certify the full reservation. Later paid modes require source-linked completed acceptance for the same config/provider scope; acceptance itself remains a distinct mode. Paid requests also require refreshed prices and source-identified prerequisites/support policies for every provider.

The read-only forecast fixture records $147.82 from 14 October price inputs and separate $250/$50/$300 limits, plus the two supplied extract ranges/hashes. Those hashes identify bounded supplied bytes only; they are not a workbook revision/hash, complete workbook, live quote or actual bill. `tests/fixtures/f11/scenarios.json` marks generated dates, windows, N, mode lower bounds and prerequisite cases as synthetic test inputs. P2/P3 remain excluded until explicit sourced higher-priority execution, analysis and budget review. Offline simulation is never paid authorization.

The bounded copy handoff exposes a work deadline before cleanup allowance, permits at most one retry for each failed artifact, retains partial local evidence and requests normal cleanup. It does not execute a retry, add a timer or promise remote recovery. Reconciliation retains per-provider estimated/actual/final billing state, multiple failure evidence, event identity and cleanup evidence. Funding is released only after every selected provider has final sourced billing and verified absence; release evidence is terminal and reasoned. Only verified absence across every selected provider closes cleanup uncertainty.

The cap-start identifier in F11 evidence is `reservation-activation-before-provider-expiry`, matching the merged AWS contract. Ordering is reservation activation before provider expiry registration before creation. The planned placeholder identifier was superseded as an interface alignment only; no research policy changed. The Azure owning driver/spec had no corresponding identifier when checked, so F11 makes no Azure contract change. The 28 paired slots are also checked for exactly one P0 and one P1 entry at the same scheduled start.

Convergence T034–T038 closes the retained-liability, lifecycle and deadline edges. Nonfinal actual charges and conservative estimates count toward working commitment and budget blocks; provider billing events retain source-linked history and declare cumulative or delta semantics. Final cumulative billing supersedes provisional values, while later provisional events cannot overwrite final amounts. A completed all-provider cleanup marks the resource lifecycle terminal independently of financial release, preventing creation even while charges remain unresolved. Explicitly reported unknown cleanup invalidates that provider's earlier absence evidence; an event that omits cleanup status (for example, a billing-only update) does not silently revoke it. Creation compares complete mode-specific remaining work with immutable monotonic cap and trigger bounds, rejects a clock origin earlier than activation, and returns the earliest effective provider deadline on new and duplicate handoffs. A previously accepted exact expiry-registration set remains replayable after a wall-clock rollback; the observed UTC discrepancy is recorded in the ledger and never changes monotonic eligibility. Unaccepted or changed registration evidence still must satisfy the UTC ordering proof.

## Validation record

At code checkpoint `9295236c16aedb2e29d665dc40550b93f6610ce5` (based on integrated `origin/main` containing Azure PR14 commit `24a964505210b2e4e8593861bcabf8ce5ec2e9d6`), the initial offline checks were:

| Check | Command/result |
| --- | --- |
| Focused F11 tests | `PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/unit/test_f11_schedule.py tests/unit/test_f11_budget.py tests/contract/test_f11_admission.py tests/integration/test_f11_ledger.py --tb=short` — exit 0, 73 passed, no skips |
| Full suite | `PATH=/private/tmp/cnb-tools/powershell-7.5.11:$PATH PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q --tb=short` — exit 0, 924 passed, no skips, no warnings |
| Contract schema check | `PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts --check` — exit 0 |
| F11 module `--help` | `PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark.admission --help` — exit 0 |
| Existing CLI `--help` | `PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark --help` — exit 0 |
| Pending preview | `PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark.admission preview --schedule tests/fixtures/f11/schedule-pending.csv --config-map tests/fixtures/f11/config-map.json --cost-inputs tests/fixtures/f11/cost-inputs.json --now 2026-10-22T00:00:00Z` — exit 0, `status=inspectable`, `paid_eligible=false`, four VMs/cloud, `external_calls=0` |
| Duplicate retained preview | Integration subprocess with two calibration rows — exit 2, `status=invalid`, deterministic `retained_mode_requires_one_entry` reason |
| `git diff --check` | Exit 0 |

Commands ran in `/private/tmp/cnb-f11` with the offline virtualenv and `PYTHONPATH=src`; the full suite used the verified PowerShell runtime at `/private/tmp/cnb-tools/powershell-7.5.11/pwsh`, so no Azure tests were skipped. These checks establish offline behavior only. F09 composition, the F10 clean-checkout gate and separately authorized provider acceptance remain future stages. Provider billing, expiry timing, quota, cleanup behavior and real measurements are not established here.

After T034–T038, focused F11 tests passed (78 passed, exit 0) and the full suite passed (929 passed, no skips, exit 0) with the verified PowerShell runtime. Contract schema check, F11 and existing CLI help, pending schedule preview (`status=inspectable`, `paid_eligible=false`, four simultaneous VMs per provider, zero external calls), and `git diff --check` also passed. The F11 preview fixture remains synthetic; these checks do not establish paid eligibility or cloud behavior.
