# WEEK archive progression repair

## Result

The WEEK driver can now finish an original slot whose lifecycle closed cleanup but returned a source-bound `archive_pending_validation` capsule. After both paired lifecycle futures join, the driver uses the existing replay and reconciliation paths while it owns the global lock. Public `reconcile_archive` still takes that lock exclusively.

Replay remains one-shot. The journal records the attempt before restore starts. Restart validates and reuses a successful immutable receipt; an incomplete attempt without one stays pending and blocks new archive allocations. Existing successful replay receipts count against later peers' fresh storage admission. Reconciliation checks the durable reservation, exact stage/ledger scope, source capsule, parent/child/result bindings, native validation and export integrity. Only proven closure clears the exact ledger reconciliation blocker. Transport integrity does not imply native validity.

The replay report is written outside the sealed export. The sealed export is verified after report generation, without changing `verify_export` or rewriting earlier exports. Replay capacity admission includes the extra raw copy retained by the driver's closed export. It requires the existing hash-bound, fresh (at most 15 minutes old) controller quota evidence; no refresh service or implicit pruning was added.

The separate continuation after a settled failed or missed pair was already implemented and approved by the user (“I approve allowing later complete pairs after prior failures are settled.”). This change preserves that policy and its fixed schedules, pair gates, cleanup and finance requirements.

## Baseline reproduction

On untouched main `95337b46d50b3f57e184d08b9fcbe607e1973604`, `_launch_lifecycle` reduced a closed lifecycle CLI result with `archive_pending_validation` and a capsule to `failed`, dropping the replay capsule. The positive projection test failed on that revision (`failed` instead of `archive_pending_validation`). The production projection now retains only a valid, closed, correctly scoped capsule; malformed digest, wrong run IDs, wrong capsule status and unresolved real reservation remain blocked.

A distinct controlled-driver characterization returns `archive_pending_validation` directly at the lifecycle boundary. The real driver preserves the pair and leaves the other 54 logical observations scheduled. This is not the same baseline failure as the lifecycle wrapper dropping the capsule. Historic bed9 behavior and the already-approved settled-failure continuation are documented separately; no new continuation policy was introduced.

## Qualification evidence

Candidate: branch `codex/week-archive-progression-20261125`, commit `e656fdc0c9605f658e8bf00cf00708b618e7040a`, tree `787c37a1e574ba9e73dde21735dee1ed914509af`. The two production files are pinned by SHA-256: `campaign.py` `b9ab83a8cec08747e5276bca0420035b1cb9f8cc3a2898c3c4d89dc92b03076d`; `archive_replay.py` `94d6e108b2f3e4bfbedeb0ca8ddf6537d8acc1593b579cee3e5ce2cec1b2d14e`.

Linux tests used Halle's existing Python 3.12.3 environment, `env -i`, and `unshare -Urn`; no provider, credentials, cloud calls, paid actions or live ledger operations occurred.

| Selection | Result | Scope |
|---|---:|---|
| Archive/guard/fixture/logical WEEK/lifecycle projection/continuation/F09 bundle/campaign driver; owner case excluded | 99 passed, 1 deselected in 34.33 s | Fresh Git checkout at the candidate commit; JUnit at `/var/tmp/cnb-week-archive-final-e656fdc/core.xml` |
| Genuine owner-held replay, receipt corruption and successful receipt restart | 1 passed in 14.68 s | Fresh generated WEEK stage/runtime; genuine AWS-only F09 pending capsule; the later original pair dispatched. Restore/backend/launch were forbidden on restart. |
| F09 invalid native result, missing archive part, mutated part, wrong generation | 4 passed, 17 deselected in 10.42 s | Existing fake-only lifecycle/archive factory fault cases |
| F09 bundle replay plus generated WEEK observer chain | 17 passed in 101.71 s | Fresh Git checkout at `5a9afdb`; production files and these test files are byte-identical to the candidate. No schedule/source change was used to revive a missed window. |

The full logical WEEK test ran all 28 frozen paired slots through `CampaignDriver`, the journal and `BudgetLedger`: 56 logical IDs and 168 child IDs remained bound; 54 lifecycle callbacks settled, one pair remained permanently missed, one measurement failure stayed terminal, and the existing verified P0-00 export retained its exact tree hash after restart. Lifecycle and provider results in this traversal are controlled test boundaries; it is not an elapsed seven-day run or native three-provider archive proof.

The owner-held positive test uses a real pending F09 AWS capsule and receipt source, the production runtime loader, replay, native validator, analysis, export verification and reconciliation. Its GCS backend and lifecycle source are fakes. The logical WEEK and observer suites also use synthetic lifecycle/provider boundaries. The evidence does not establish S3/Azure/GCP archive replay as a full WEEK, live quota availability for 28 slots, or WEEK launch authority.

The test harness once generated stage output outside its temporary root and allowed a fixed start date to expire during a long combined selection. The fixture now keeps generated outputs under pytest's temporary root and schedules its test-only stage two days ahead. A combined run made before that correction is incomplete and is not counted above. The observer selection passed separately before its setup window expired; no production timing or dispatch window was widened.

## Limits

Each automatic restore still requires a current, hash-bound quota snapshot with sufficient user quota for compressed input, four raw copies, metadata, other growth and reserve. A failed partial restore with no successful receipt conservatively blocks later replay allocation and remains pending across restart. No background quota-refresh process was added. This repair is not a live WEEK GO, financial approval, provider qualification, or all-cloud native archive acceptance.
