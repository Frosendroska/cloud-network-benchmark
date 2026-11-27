# WEEK archive progression repair

## Result

The WEEK driver can now finish an original slot whose lifecycle closed cleanup but returned a source-bound `archive_pending_validation` capsule. After both paired lifecycle futures join, the driver uses the existing replay and reconciliation paths while it owns the global lock. Public `reconcile_archive` still takes that lock exclusively.

Replay remains one-shot. The journal records the attempt before restore starts. Restart validates and reuses an immutable terminal receipt; an incomplete attempt without one stays pending and blocks new archive allocations. Both successful and terminal native-invalid replay allocations count against later peers' storage admission. Reconciliation checks the durable reservation, exact stage/ledger scope, source capsule, parent/child/result bindings, native validation and export integrity. Only proven closure clears the exact ledger reconciliation blocker. Transport integrity does not imply native validity.

The replay report is written outside the sealed export. The sealed export is verified after report generation, without changing `verify_export` or rewriting earlier exports. Replay capacity admission includes the extra raw copy retained by the driver's closed export. It requires hash-bound controller quota evidence at most 15 minutes old. The original repair used frozen evidence; the explicit opt-in refresh below supplies new observations without rewriting it. No pruning is provided.

The separate continuation after a settled failed or missed pair was already implemented and approved by the user (“I approve allowing later complete pairs after prior failures are settled.”). This change preserves that policy and its fixed schedules, pair gates, cleanup and finance requirements.

## Combined maintenance — 27 November

The published WEEK source `d8ec7588` and provider/financial candidate `0384206e` diverged at `bed9b988`. Merge `a0d5b6ce` retains the WEEK observer, paired continuation and owner-held replay repairs together with the provider candidate's strict real-capsule projection and financial gates. This offline integration does not change the running acceptance checkout or grant WEEK spending authority.

Three bounded corrections follow:

- An opt-in `archive_sink.replay_quota_refresh` freezes the expected user/UID, quota device/mount, actual filesystem paths and minimum available bytes. The fixed `/usr/bin/quota` command samples each entry before original-pair admission and again before replay allocation. Mode-0600 observations are append-only, hash/source/time/scope bound, and journal references survive lifecycle transitions. Pre-replay admission precedes the one-shot marker; post-allocation failure still cannot retry. Old settings retain frozen-evidence behavior.
- A genuine complete `replayed_invalid` receipt reaches canonical reconciliation under the owner-held lock. A settled native-invalid or partial result becomes permanently `failed`, retaining its actual validity, child evidence, exports and storage allocation. Only a later original complete S1/S3 pair may proceed. Corrupt, missing or untrusted replay evidence, unknown ownership, open cleanup and unresolved finance remain blocking. No uploaded bytes are promoted to native-valid without replay.
- F08/F12 retain an independently corroborated receiver prefix at a specifically recognised incomplete native/client/command tail. Generic source, identity, hash, clock or parse contradictions remain fatal. Raw receiver intervals join only at exact integer boundaries within the same source/session/boot/clock/stream/omission/mapping scope; UTC uncertainty is applied after that join. Positive raw gaps remain gaps. DAY uses the continuous receiver evidence once; RTT chunks do not duplicate it. Existing minute-boundary exclusion and primary-metric rules remain unchanged.

All write destinations, including `repository/results` replay exports, must share the pinned quota filesystem. Generated stage roots retain their separate path roles even when base roots alias. Mixed-filesystem placement is not covered by this simple option. Retained allocations are still conservatively subtracted from a fresh quota reading that already reflects retained files; this can require extra headroom. No receipt reserves capacity, and the real storage floor still needs measured CAL/DAY output rates and staging peaks.

The six-hour interruption fixture is generated before sealing with the original 86,400-second request, matched client/receiver/source prefixes, 72 RTT chunks, failed execution and separately closed cleanup. It remains invalid, ineligible and incomplete. Of 21,600 simulated receiver intervals, 21,598 meet source containment; minute boundaries exclude another 718. The existing descriptive whole-interval field retains 20,880 seconds and 2,610,000,000,000 **simulated traffic bytes**, not artifact-storage bytes or research measurements. Boundary-straddled primary minute metrics remain unavailable. This is logical engineering coverage, not six hours of elapsed execution or controller/disk-loss recovery.

Final qualified test candidate: `4047ad6bb29aa40e5917eda7b64bd7dfb3ffdc60`, tree `50dd81ca5bf88d5a3418dd118192b516d574ae8d`. Every production file is byte-identical to combined code `4392ebe16c8306bc5e5b92b4334d6c78dbd5aefe`; later changes are test fixtures only. Independent static reviews approved the merge, quota/invalid settlement, DAY correction and fixture integration.

**330 distinct native Linux cases passed across the recorded batches below, with no unresolved case or skip.** This is an affected-path selection, not the complete repository suite or elapsed-week endurance. The first combined batch had 328 passes and one failure. That test mixed wall-clock pricing and manifest initialization with its frozen historical lifecycle clock. Test-only corrections bind those synthetic inputs to the existing epoch. A later negative expected only one identity-error message, although the canonical schema correctly rejected the tampered first-provider run earlier; the helper now accepts either specific identity rejection and still requires the untouched receipt to reconcile successfully afterward. No production clock, financial guard, validator, timeout or receipt deadline was relaxed. Failed logs are retained.

| Native batch | Source | Result / JUnit seconds | Log SHA-256 | JUnit SHA-256 |
| --- | --- | --- | --- | --- |
| Combined affected selection | `4392ebe` | 328 passed, 1 failed / 1081.652 | `a6466e008861c187872d3c96f359c71f4c7cd363f6b59b7b67fa873695445468` | `2fbe69535cbdbc8d95c24c72ce30c5c23cc1a98916021af9ebf6290f4462825b` |
| Price-date correction; initialization-clock mismatch exposed | `4933068` | 1 failed / 32.587 | `8c011618cf6933a208d17c9657c1e046e5c27ec12ca1f9c5bd0896d907d01dd0` | `581d2a0ab7cb108edaab99e86910eda890f096ac8a6659be60e9f515c1de4136` |
| Shared-parent export and DAY prefix; operator negative exposed | `3d8c74d` | 2 passed, 1 failed / 83.459 | `fa3ec5afd05337be764c0f82091371c84974d47493c03337781582468ab22773` | `05d3ff648203933a5eff73d1f819d3c9b5e656c8f31a7e160cbd8d863fb4f6b5` |
| Complete operator chain, including first-provider rejection and restored-receipt success | `4047ad6` | 1 passed / 47.268 | `b3184b26f4c1a8ed89d5821f510bda674b49f70d40f26b59730be271fa06e5ae` | `bafad1939b8959754ab38398a2e80e96764bd1a6fa270fe95b9d6bde8c18367a` |

The shared-parent export regression initializes one campaign and materializes its three actual selected children through the existing F08 helper. The logical entry stays failed for Azure's invalid native data; AWS/GCP remain valid in the closed and aggregate selections, with every run ID and artifact byte preserved. Parent execution remains truthfully succeeded where execution succeeded: native invalidity is not fabricated execution failure. This regression does not claim a live mixed-validity GCS acceptance batch.

Tests used Halle's existing Python environment, `nice -n 10`, `unshare -Urn`, `env -i`, an isolated HOME/TMPDIR, one numerical-library thread and the existing read-only Terraform mirror. Provider/auth/store boundaries and financial authority in this operator run are synthetic. No provider calls, paid actions, active-state reads or operational ledger writes occurred. The real sampler read below is a separate host-only quota read. Logs/JUnit files are retained at `/u/halle/brek/home_at/cnb/qualification/week-offline-combined-20261127/` and locally at `/private/tmp/week-offline-native-evidence-20261127/`.

The combined selector covers quota parsing/freshness/tampering and generated two-slot admission; storage admission; acquisition, validation and analysis contracts; owner-held replay and negative guards; the logical 28-slot traversal; settled failure/miss continuation; genuine capsule projection; native observer bindings; driver/restart/export behavior; the complete reduced operator chain; and strict native ingestion/UTC-selection/partial-prefix regressions. The three focused final selectors were `test_reduced_operator_chain_generates_and_activates_typed_draft`, `test_failed_three_provider_entry_keeps_valid_siblings_in_aggregate_export` and `test_failed_receiver_retains_verified_prefix_after_strict_replay`. Repeated cases are counted once in 330; historical component counts below are not added.

The production sampler separately passed a real read-only Halle quota read and receipt reload at `2026-11-27T00:06:50Z`: available bytes `32659091456`, receipt SHA-256 `67ea91ca019f5fca506e1217126dfc41cf895ba2f039212916176af75c76f765`. Its runtime/entry scope is explicitly synthetic and non-admitting. Sampler file SHA-256 `abe2951051acb4f9a589d6927c166f237e213a0d46a3a121045efc3b460dc61e` is unchanged in the combined candidate. This is a point-in-time observation, not reserved WEEK capacity.

## Earlier baseline reproduction — 25 November

On untouched main `95337b46d50b3f57e184d08b9fcbe607e1973604`, `_launch_lifecycle` reduced a closed lifecycle CLI result with `archive_pending_validation` and a capsule to `failed`, dropping the replay capsule. The positive projection test failed on that revision (`failed` instead of `archive_pending_validation`). The production projection now retains only a valid, closed, correctly scoped capsule; malformed digest, wrong run IDs, wrong capsule status and unresolved real reservation remain blocked.

A distinct controlled-driver characterization returns `archive_pending_validation` directly at the lifecycle boundary. The real driver preserves the pair and leaves the other 54 logical observations scheduled. This is not the same baseline failure as the lifecycle wrapper dropping the capsule. At historical bed9, the run loop blocked all later WEEK slots after any failed or missed record, even when cleanup was closed. By current main `95337b4`, `_week_dispatch_blocked` allows later pairs after settled failures and missed slots while retaining blockers for open/unknown cleanup, uncertain launch or pending archive validation. The user explicitly approved that continuation policy; this repair did not introduce it.

## Earlier qualification evidence — 25 November

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
