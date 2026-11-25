# Paired-WEEK failure semantics: proposed continuation policy

Status: REVIEW CANDIDATE ONLY. Author code/invariant review complete; user policy decision and integration review pending. No integration, publication, provider calls, spending, operational-ledger changes or launch.

## Exact audited source

The current detached-WEEK readiness handoff names:
- Halle: /var/tmp/cnb-v19-financial-qualification-owejkuZA/R2/candidate
- Commit: bed9b98893540e52a0433a3213f20e75b3dd0d1d
- Tree: 15eaae842d9db6d0df3c142d1c2965d31d883a0c

Both pins were verified live. This is the intended candidate named by that handoff, not a claim that a final approved WEEK source or launch record exists. The older published 1e87f31 source is not substituted. The independent WEEK observer repair is not included here.

The candidate is isolated at /var/tmp/cnb-week-failure-review-20261125/candidate on Halle. The canonical Mac checkout was not edited. A temporary local code clone was removed after transferring its changes to this isolated native clone because local writes began failing with ENOSPC. No other workspace was removed.

## Current behavior, reproduced before editing production code

The original specs/020-week-slot-dispatch/spec.md FR-004 explicitly requires a failed/missed member to block all later slots even after verified cleanup. This is intentional fail-stop policy, not an accidental missing cleanup check.

CampaignDriver.run in the audited source:
- Lines 731-732 latch blocked on any failed or missed WEEK entry after restart reconciliation.
- Lines 742-746 immediately mark subsequent scheduled entries missed when blocked, including future dates.
- Lines 750-753 latch blocked if the first expiry check removes the whole slot.
- Lines 808-818 reject a remaining singleton and latch blocked.
- Lines 909-916 latch blocked after a failed WEEK lifecycle, even with cleanup_open=False.
- Lines 1537-1547 can reconcile a failed entry to closed cleanup, but retain failed state. The first guard still blocks later dispatch. Already-missed future entries are never made scheduled again.

There is a precise exception: expiry detected after sleeping (lines 781-783), and certain final dispatch-time missed outcomes, do not consistently latch the same flag in the running process. A restarted process does latch on their missed records. Thus “every missed slot always prevents every later slot” is not universally true; ordinary early-miss, partial-pair and failed-lifecycle paths do, and restart does.

Native baseline cases:
- A first-pair measurement failure with cleanup closed: two lifecycle callbacks, later pair missed.
- Start one hour late: first pair missed, later pair also missed without dispatch.
- Partial first pair on restart: no singleton, remaining member and later pair missed.
- Open cleanup: no later dispatch.
- Oversleep: first expired pair is not backfilled. The new two-slot probe confirms a later pair can already run on this particular baseline path.

## Proposed behavior and smallest scoped change

One production module changes. WEEK derives the dispatch stop from unsettled work, rather than permanent failure/missed status:
1. Both earlier pair members must have settled. Every attempted member, including a complete one, requires literal cleanup_open=False. Missing/null/zero closure evidence blocks.
2. Open cleanup, launch uncertainty and archive_pending_validation still block. Future scheduled records remain scheduled with cleanup_pending and the driver returns awaiting_operator; it does not poll providers or automatically restart.
3. A later explicitly authorized invocation of the existing runner reconciles original evidence and rechecks unchanged UTC deadlines. Expired entries become missed; only a complete still-scheduled S1+S3 pair may dispatch.
4. Verified original failed-entry cleanup reconciliation clears resolved launch_uncertain only for WEEK. The failed state, original error, started/finished timestamps and other result evidence remain. Acceptance and other stage failure policies are unchanged.
5. A missed unstarted slot or settled failed slot does not poison subsequent pairs. The existing source-hashed pair policy, executor, locks, per-entry IDs, namespaces, runtime/config hashes, lifecycle and budget admission remain.

No new command, retry, backfill, replacement identity, schedule shift, cap, funding release or provider behavior is introduced. Existing terminal missed records, including future slots cancelled by old code, are never restored. An existing stage is not migrated by this proposal.

An open-cleanup stop needs separately authorized recovery/continuation; read-only status/resume remains observational. This patch does not provide a new operator continuation command or qualify a live recovery procedure.

## Author review and invariants

- Reviewed both initial/restart and same-process dispatch paths, all missed-slot checks, partial-pair rejection, journal reuse, reconciliation and final status/export handling.
- The future-pair hold is necessary: merely deleting the failed-state guard would still leave future slots permanently missed when a run returns cleanup_open=True.
- Explicit closure strengthens the old truthiness check: failed/complete entries with unknown closure cannot unlock later creation.
- The real executor settles both submitted callbacks before the next pair. Full 28-slot tests run with and without an initial failure, retain 56 unique calls/168 child IDs, a peak of two shared-ledger reservations and original six-hour fixture timestamps.
- No-singleton dispatch means both lifecycle entries are submitted as the frozen pair. It does not promise that both providers/scenarios succeed; their results remain independent and preserved.
- Original monetary controls, financial authorization, ledger code, lifetime/deadline code, price inputs, configurations and UTC plan files are byte-unchanged. Real synthetic ledger tests and EUR/retained-liability/cap regressions passed.
- Archive-pending validation remains a blocker. Synthetic lifecycle tests create no native result exports; export failures stay visible and are not represented as successful measurements.
- No independent reviewer or user policy approval is claimed. The specification amendment is explicitly proposed, not integrated. Existing historical validation records are retained.

## Native no-call evidence

Halle Linux, existing Python 3.12.3. No dependencies installed. Fake lifecycle outcomes and synthetic temporary state; real scheduler, journal, locks and relevant shared-ledger code. No operational settings or state were used.

The runner uses an empty process environment, explicit PATH/PYTHONPATH, disabled pytest plugin autoload and bytecode/cache writes disabled. A Python audit hook denies actual socket connection/DNS and subprocess/exec/spawn/system dispatch. Every recorded run had denied_actual_dispatches=0. SSH/SCP were only host transport outside this guarded process.

Evidence root: /var/tmp/cnb-week-failure-review-20261125/evidence

| Evidence | Result | SHA-256 |
| --- | --- | --- |
| baseline.xml | 5 passed, 55 deselected: existing fail-stop tests | ea1f61e8d3e4a533b58d27143cbce48470d04937ecfb81ad40949b3e8a45fad2 |
| before-final-tests.xml | 12 expected failures, 1 pass on unchanged production source | cc07cd805daedb72734173fd109ffc92a4dfb4a2667bb6e02aed1b7f289013b6 |
| final-combined.xml | 125 passed, 0 failures/errors/skips on final changed production/test bytes | 9f97f72392e7051e16eac348e77e7b41549dd2d2aec0763316faa9378715dc0b |

Final selection: all test_week_failure_continuation.py (13), test_campaign_driver.py (61), test_campaign_service.py (19); all test_financial_controls.py plus six focused F11 ledger selectors (32). Total 125, not additional counts for earlier overlapping runs. The intermediate after.xml records 2 failed/91 passed from test expectations about synthetic exports and future archive-pending work; corrected without weakening production gates.

Changed runtime SHA-256: 3368bf76e852da80337bd0ce548c252fade33037a0a2f0e4fac15aee3c92d6c9
Driver tests SHA-256: 8c8daceec2dd7bd1b97c8f81fe0540694400e220722581f0191f1d7f9f1be572
New regressions SHA-256: 27eb9888bd538a4eb4b4cc81f90551135f6663b72576769e6c187d763fcdf41f
No-call runner SHA-256: dc5ccd03f3df23c800b13d2ea6793974cbef462a9cbd82a819e2bcb23f34cea3

git diff --check passed. These are focused native Linux no-call tests, not a full suite, elapsed-week endurance, real cleanup proof, provider acceptance or research measurements.

## Methodology impact and decision

Proposed decision: permit later scheduled complete pairs once prior work is settled, while retaining every failed/missed observation and every existing safety/admission gate.

This changes stopping/missingness policy, not workload, N, VM topology, seven-day duration, sampling windows, measurement criteria or the planned 28 pairs/56 entries. Fail-stop truncates the sample at the first failure; continuation can retain later times of day but does not make missingness random. Capacity, infrastructure and controller failures may correlate with provider, scenario and time.

Report expected, attempted, failed, missed, valid and matched counts with reasons per scheduled slot/provider/scenario. A successful sibling remains evidence but cannot supply the missing side of a matched S1/S3 contrast. Apply existing validity and timing criteria to the actual matched intersection. Do not impute measurements, count packets as independent deployments, shift windows, fill gaps, or claim 28 successful pairs. A week with failures remains incomplete/failed in execution reporting even if later pairs succeed.

The current V2 design/handoff require preserved failures, missed slots and cleanup closure. The narrower implementation FR-004 imposes stronger fail-stop semantics; changing it is a reviewable policy amendment and must be declared before research. Historical conditional replacement entries do not override this task's explicit no-retry/no-backfill constraint.

Before any integration: accept or reject this policy amendment; review this exact delta; compose with the separately owned observer/final source changes and requalify affected integration; bind a fresh exact WEEK source and financial approval. No such decision, integration or launch is performed here.

Sources consulted through current connected GitHub because local routed reads failed:
- advisor/docs/design_document.md blob 04e400edf64076b010d2d0077f8576a93bbc9bad
- DECISIONS.md blob c781a9021e2a9c6e333f65be049ad416462e7ea1
- experiments/FRAMEWORK_IMPLEMENTATION_NOTES.md blob 4cac832ce22a39a00673debb076756478c986724
- experiments/EXPERIMENTS.md blob 88cb55a3b9ec1d58110c385f667ecb021bbe0c21
- tracking/execution/IMPLEMENTATION_PROMPTS.md blob 10d6adceda1a636a74b3654f890067690d7a3a8f
The old tracking/IMPLEMENTATION_PROMPTS.md route moved; tracking/README.md resolves it.
