# Focused validation record

## Source

- Isolated branch: `codex/week-slot-concurrency-20261120`
- Base commit: `eeac59498736bff46f1a27bbc63cf4fd4475145f`
- Base tree: `84a925de3e879de3056503c54208213cc4e3ca95`
- Runtime: macOS, Python 3.13, using the canonical checkout's existing `.venv` site-packages. No dependencies were installed.

## Results

- Campaign dispatch qualification: 8 passed. Covered 56 scheduled entries through two concurrent fake calls making progress through production admission and locked shared-ledger reservations; acceptance remained serial; malformed/duplicate pairs, changed approval evidence, missed slots, one-member failure, restart singleton dispatch, and the existing stage lock were rejected or stopped as required.
- Generated WEEK admission: 1 passed. Verified frozen source-hashed paired policy, 56 entries, pair metadata, and isolated child/config/runtime namespaces. Runtime: 181.95 s.
- Admission/lock focused checks: 4 passed. Covered unsupported concurrency cap, serialized acceptance, changed evidence, and second-driver lock contention.
- Existing DAY/native coverage: 5 passed. Confirmed one persistent fake TCP process with bounded local probe rotation, failed probe rotation, failed TCP partial output without restart, bounded-memory receiver stream replay, and synthetic diagnostic-day bundle replay. These are fake/native-file checks, not live traffic.
- Receipt-backed provider repair checks: 4 passed. Covered AWS registration/settle deadline boundaries, GCP subnet inventory command grammar, and preservation of typed AWS CLI failure/cleanup evidence.
- `compileall` for changed production and test modules: passed.
- `git diff --check`: passed.

The focused campaign selectors were `test_full_week_runs_56_observations_with_two_calls_per_slot_and_shared_budget`, `test_acceptance_stays_serial`, `test_missed_week_slot_blocks_later_slots`, `test_week_failure_blocks_later_slots_even_after_cleanup`, `test_week_rejects_incomplete_or_duplicate_slot_pairs`, `test_week_rejects_changed_concurrency_evidence`, `test_week_restart_does_not_launch_singleton_from_partial_pair`, and `test_second_campaign_driver_cannot_take_active_stage_lock`. The generated admission selector was `test_generated_week_freezes_the_bounded_source_hashed_pair_policy`. DAY/native selectors were `test_day_keeps_one_tcp_invocation_and_rotates_local_bounded_chunks`, `test_failed_day_probe_rotates_without_restarting_tcp`, `test_failed_day_tcp_preserves_partial_day_and_does_not_restart`, `test_receiver_stream_replay_keeps_memory_bounded_for_large_interval_logs`, and `test_diagnostic_day_materialized_bundle_replays_all_native_chunks`.

## Evidence boundary and pending work

All campaign calls used fakes/in-memory state; native DAY checks replayed synthetic/native-format files. No provider, credential, account, live ledger, paid launch, or real measurement was accessed. These results do not establish cloud acceptance or research DAY/WEEK results. The separately owned Azure host-trust parser repair was not available in the isolated source snapshot and remains T009; integrate it and rerun only its affected checks before the finite supervised provider validation procedure.
