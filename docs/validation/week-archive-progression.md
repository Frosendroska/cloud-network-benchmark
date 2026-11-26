# WEEK archive progression repair

## Result

The WEEK driver can now finish an original slot whose lifecycle closed cleanup but returned a source-bound `archive_pending_validation` capsule. After both paired lifecycle futures join, the driver uses the existing replay and reconciliation paths while it owns the global lock. Public `reconcile_archive` still takes that lock exclusively.

Replay remains one-shot. The journal records the attempt before restore starts. Restart validates and reuses a successful immutable receipt; an incomplete attempt without one stays pending and blocks new archive allocations. Existing successful replay receipts count against later peers' fresh storage admission. Reconciliation checks the durable reservation, exact stage/ledger scope, source capsule, parent/child/result bindings, native validation and export integrity. Only proven closure clears the exact ledger reconciliation blocker. Transport integrity does not imply native validity.

The replay report is written outside the sealed export. The sealed export is verified after report generation, without changing `verify_export` or rewriting earlier exports. Replay capacity admission includes the extra raw copy retained by the driver's closed export. It requires the existing hash-bound, fresh (at most 15 minutes old) controller quota evidence; no refresh service or implicit pruning was added.

The separate continuation after a settled failed or missed pair was already implemented and approved by the user (“I approve allowing later complete pairs after prior failures are settled.”). This change preserves that policy and its fixed schedules, pair gates, cleanup and finance requirements.

## Baseline reproduction

On untouched main `95337b46d50b3f57e184d08b9fcbe607e1973604`, `_launch_lifecycle` reduced a closed lifecycle CLI result with `archive_pending_validation` and a capsule to `failed`, dropping the replay capsule. The positive projection test failed on that revision (`failed` instead of `archive_pending_validation`). The production projection now retains only a valid, closed, correctly scoped capsule; malformed digest, wrong run IDs, wrong capsule status and unresolved real reservation remain blocked.

A distinct controlled-driver characterization returns `archive_pending_validation` directly at the lifecycle boundary. The real driver preserves the pair and leaves the other 54 logical observations scheduled. This is not the same baseline failure as the lifecycle wrapper dropping the capsule. At historical bed9, the run loop blocked all later WEEK slots after any failed or missed record, even when cleanup was closed. By current main `95337b4`, `_week_dispatch_blocked` allows later pairs after settled failures and missed slots while retaining blockers for open/unknown cleanup, uncertain launch or pending archive validation. The user explicitly approved that continuation policy; this repair did not introduce it.

## Qualification evidence

Qualified implementation/test candidate: branch `codex/week-archive-progression-20261125`, commit `e656fdc0c9605f658e8bf00cf00708b618e7040a`, tree `787c37a1e574ba9e73dde21735dee1ed914509af`. The final docs-only commits do not change that qualified code/test tree. Production files are pinned by SHA-256: `campaign.py` `b9ab83a8cec08747e5276bca0420035b1cb9f8cc3a2898c3c4d89dc92b03076d`; `archive_replay.py` `94d6e108b2f3e4bfbedeb0ca8ddf6537d8acc1593b579cee3e5ce2cec1b2d14e`.

Linux tests used Halle's existing Python 3.12.3 environment, `env -i`, and `unshare -Urn`; no provider, credentials, cloud calls, paid actions or operational/live ledger mutations occurred. The logical traversal uses a temporary synthetic `BudgetLedger`.

| Selection | Result | JUnit SHA-256 | Log SHA-256 |
|---|---:|---|---|
| Archive/guard/fixture/logical WEEK/lifecycle projection/continuation/F09 bundle/campaign driver; owner case excluded | 99 passed, 1 deselected in 34.33 s | `2d8f5841d86f4cd66276ca131de841dc9f20f8030e14ce9997d4b1426dc2f772` | `95b64c93562224d79184769ad6ff693b285c04eac629e8bbfdde55c6993baa73` |
| Genuine owner-held replay, receipt corruption and successful receipt restart | 1 passed in 14.84 s | `2b82d4a237b06f350cd6548fc3272777a40fd05b3a6d5c1fe2f4b5fbfdca1fe7` | `f1741882850bd5e7e3d511fc685d0dd95039397d9cead1a32c101d7b0e2f61ee` |
| F09 invalid native result, missing archive part, mutated part, wrong generation | 4 passed, 17 deselected in 9.87 s | `b6dfc703f3117802994165ee7a201c884fd4c0f1b0c2c573e236ee19da860a88` | `19119df66cd9d6997494bfc32cbd2462c228bbb581abd7d198f8e51cd53b9f67` |
| F09 bundle replay plus generated WEEK observer chain | 17 passed in 101.71 s | No JUnit was emitted | `560ceb182cc8b589c8e3ce5f0fbf5fb967819da881fe206b9cba5fa93bdf41e5` |

The commands below ran in a fresh Git checkout. Each command was prefixed with `env -i PATH=/usr/bin:/bin PYTHONPATH=src:. unshare -Urn /u/halle/brek/home_at/cnb/venv/bin/python -m pytest`:

- Core: `-q --junitxml=core.xml -k "not test_campaign_driver_replays_and_reconciles_genuine_pending_capsule_under_owned_lock" tests/integration/test_week_archive_progression.py tests/integration/test_week_archive_guard_negatives.py tests/integration/test_week_archive_fixture.py tests/integration/test_week_archive_baseline.py tests/integration/test_week_archive_logical_run.py tests/integration/test_week_lifecycle_pending_projection.py tests/integration/test_week_failure_continuation.py tests/integration/test_f09_bundle_replay.py tests/integration/test_campaign_driver.py`
- Owner: `-q --junitxml=owner.xml tests/integration/test_week_archive_progression.py::test_campaign_driver_replays_and_reconciles_genuine_pending_capsule_under_owned_lock`
- Invalid archive/native: `-q --junitxml=native-invalid.xml -k "invalid_native or missing_part or mutated_part or wrong_generation" tests/integration/test_f09_aws_runtime_factory.py`
- Observer/bundle: `-vv -o faulthandler_timeout=30 tests/integration/test_f09_bundle_replay.py tests/integration/test_week_observer_chain.py`

The core batch's four F09 bundle cases overlap four tests in the observer/bundle batch. Across the four passing batches, there are 117 distinct passing node IDs; raw batch totals include those four repeated cases.

Logs and JUnit files are retained at `/u/halle/brek/home_at/cnb/qualification/week-archive-progression-e656fdc/`; a verified local copy is in `/private/tmp/week-archive-progression-evidence/`.

The full logical WEEK test ran all 28 frozen paired slots through `CampaignDriver`, the journal and `BudgetLedger`: 56 logical IDs and 168 child IDs remained bound; 54 lifecycle callbacks settled, one pair remained permanently missed, one measurement failure stayed terminal, and the existing verified P0-00 export retained its exact tree hash after restart. Lifecycle and provider results in this traversal are controlled test boundaries; it is not an elapsed seven-day run or native three-provider archive proof.

The owner-held positive test uses a real pending F09 AWS capsule and receipt source, the production runtime loader, replay, native validator, analysis, export verification and reconciliation. Its GCS backend and lifecycle source are fakes. The logical WEEK and observer suites also use synthetic lifecycle/provider boundaries. The evidence does not establish S3/Azure/GCP archive replay as a full WEEK, live quota availability for 28 slots, or WEEK launch authority.

The test harness initially generated stage output outside its temporary root and allowed a fixed start date to expire during a long combined selection. The fixture now keeps generated outputs under pytest's temporary root and schedules its test-only stage two days ahead. Those incomplete mixed runs are not counted above. The observer selection passed separately before its setup window expired; no production timing or dispatch window was widened.

## Limits

Each automatic restore still requires a current, hash-bound quota snapshot with sufficient user quota for compressed input, four raw copies, metadata, other growth and reserve. A failed partial restore with no successful receipt conservatively blocks later replay allocation and remains pending across restart. No background quota-refresh process was added. This repair is not a live WEEK GO, financial approval, provider qualification, or all-cloud native archive acceptance.
