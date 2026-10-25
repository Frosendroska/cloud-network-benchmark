# Required offline coverage

The full suite runs once. This inventory identifies required evidence to evaluate from its JUnit report; it is not a substitute selected suite. Match exact function names and, where shown, exact parameter IDs. Parameterized rows without an explicit ID require their collected variants to pass. Missing coverage is incomplete/nonzero. Existing independent lower-level tests complement the actual default-factory four-mode path.

| Scope | Required existing test identity (repository relative) | Evidence boundary |
| --- | --- | --- |
| Acceptance | `tests/integration/test_f09_multi_provider_factory.py::test_default_three_provider_factory_reserves_and_enforces_global_claim_barrier[all-provider-acceptance]` | Actual default AWS/Azure/GCP factories, saved native/validation bundles, new F12 bridge. |
| Calibration | Same function `[all-provider-calibration]` | Twelve trials, retained pair/reservation, sourced test-only N recommendation; new F12 bridge preserves comparison limits. |
| Short study | Same function `[all-provider-short-study]` | Fresh three-phase 60-second inputs, source-bound synthetic common N, new F12 bridge. |
| Continuous DAY | Same function `[all-provider-diagnostic-day]` | Fake 86,400-second TCP, full native chunks/coverage, new F12 bridge; no idle/inflation. |
| Server/process loss | `tests/integration/test_measurement_runner.py::test_failed_short_unit_returns_primary_failure_and_no_later_units` and `test_failed_day_tcp_preserves_partial_day_and_does_not_restart` | Failed tool/server-shaped boundary and preserved partial evidence; actual cloud server kill remains paid acceptance. |
| Controller loss | F09 default-factory function above `[controller-interrupt-during-native-f03]` and `[controller-interrupt-unverified-stop]` | Actual production cancellation hooks, verified/unverified closure and partial-source projection; no physical controller crash claimed. |
| Readiness deadlines | `tests/unit/test_readiness.py::test_slow_checks_shrink_budgets_and_stop_at_deadline` and `test_late_success_at_original_deadline_is_not_ready` | One absolute deadline, late success refused. |
| Probe-start/overlap | `tests/integration/test_measurement_runner.py::test_split_delayed_notification_gates_tcp_on_notification_plus_monotonic_lead` and `test_late_notification_never_starts_tcp_and_keeps_partial_outputs`; `tests/unit/test_measurement_timing.py::test_acquisition_coverage_intersects_probe_with_receiver_inner_support_and_gaps` | Notification gating plus source-bound measured support; launch/sleep alone insufficient. |
| Chunk gaps/TCP continuity | `tests/integration/test_measurement_runner.py::test_day_keeps_one_tcp_invocation_and_rotates_local_bounded_chunks` and `test_failed_day_probe_rotates_without_restarting_tcp` | ≤300-second local chunks, gap/failure retention, uninterrupted TCP. |
| Receiver streaming | `tests/integration/test_acquisition_producer.py::test_truncated_diagnostic_client_retains_only_correlated_closed_receiver_prefix` and `test_iperf_replay_requires_native_receiver_corroboration` | Incremental receiver evidence and honest incomplete tails, not client throughput substitution. |
| Incomplete retrieval | `tests/unit/test_retrieval.py::test_required_remote_absence_is_failed_evidence_without_fake_integrity` and `test_one_vm_loss_keeps_previously_verified_native_artifacts` | Missing required files and retained peer evidence. |
| Corrupt bytes/retrieval | `tests/unit/test_retrieval.py::test_retry_materializes_only_verified_bytes_and_preserves_attempts`; `tests/integration/test_acquisition_producer.py::test_mutated_closed_source_bytes_are_rejected_before_replay` | Actual materialized verification/retry evidence and source-hash rejection. |
| Reservations/cap | `tests/unit/test_f11_budget.py::test_full_remaining_and_reserved_liabilities_obey_working_and_total_caps`; `tests/unit/test_f09_lifecycle.py::test_original_cap_or_earlier_provider_trigger_blocks_stages_but_still_cleans` | Working/total liabilities and original expiry; cleanup still attempted. |
| Expiry registration failure | F09 default-factory function above `[protection-failure]`; `tests/unit/test_f09_lifecycle.py::test_failed_protection_blocks_all_creation_and_cleans_safety_liabilities` | All-provider barrier before creation; fake provider semantics only. |
| Residual launch blocking | `tests/integration/test_f09_lifecycle_recovery.py::test_changed_session_performs_cleanup_only_and_keeps_unknown_liability_blocked` | Cleanup-only recovery, unknown liability retained. |
| Config/schedule/identity | `tests/unit/test_v2_admission_artifacts.py::test_schedule_entry_keeps_exact_row_and_hash_identity`; four integrated mode cases above | Executed row/hash, IDs/native source identities through final reports. |
| Git provenance/secrets | `tests/unit/test_git_provenance.py::test_snapshot_supports_real_linked_worktree`; `tests/unit/test_bootstrap.py::test_successful_build_observations_redact_credentials_and_secret_text`; `tests/integration/test_offline_boundaries.py::test_all_commands_remain_offline` | Actual Git readbacks, redaction and zero-IO CLI boundaries; no credentials. |
| Source-patch/static capability | `tests/unit/test_bootstrap.py::test_pinned_bootstrap_records_observed_patch_and_capability_identity`; `tests/integration/test_acquisition_producer.py::test_run_bootstrap_build_observation_materializes_and_replays_exact_multiline_identity` | Pinned patch/build/source-shaped evidence is required, but native instrumented Linux execution remains unproved. |
| Native mode replay | `tests/integration/test_analysis_replay.py::test_materialized_four_mode_inputs_are_revalidated_and_reported` | Existing F08→F12 mode/statistics checks complement new F09→F12 bridge. |
| Deterministic plots/summaries | `tests/integration/test_analysis_replay.py::test_materialized_cli_report_replays_all_files_for_reordered_selection`; four integrated mode cases above | Byte-identical generated file sets, immutable inputs; synthetic reports are not research results. |
| Azure runbook local behavior | `tests/integration/test_azure_provider_flow.py` full suite, including existing PowerShell harness cases | Local AST/fake execution; missing PowerShell skips are unexpected and fail F10. Hosted 5.1/Az.Accounts remains unproved. |

## Exact allowed native-platform gaps

Only on Darwin, with the actual Linux `/proc` unavailable reason:

- `tests/unit/test_f09_guest.py::test_f03_scoped_stop_reaps_owned_session_when_original_leader_has_exited`
- `tests/unit/test_f09_guest.py::test_f03_scoped_stop_refuses_reused_pid_start_ticks`

Record these individually as not run. Linux process/session consequences and actual instrumented Sockperf/iperf3 behavior must be proved on the authorized native Linux acceptance host before research. All other required skipped cases, absent mode parameter IDs or empty reports make the gate incomplete.

## Terraform inventory

| Root | Required local checks | Existing mock-plan input |
| --- | --- | --- |
| `terraform/aws/pair` | fmt, validate | None; Python static/fake provider suites apply. |
| `terraform/aws/safety` | fmt, validate | None; Python expiry/failure suites apply. |
| `terraform/azure/pair` | fmt, validate, mock test | `tests/topologies.tftest.hcl` (five runs) |
| `terraform/azure/expiry` | fmt, validate, mock test | `tests/prerequisites.tftest.hcl` (one run) |
| `terraform/gcp/pair` | fmt, validate, mock test | `tests/inheritance.tftest.hcl` (one run) |
| `terraform/gcp/protection` | fmt, validate, mock test | `tests/scenarios.tftest.hcl` (four runs) |

Check the actual sources before execution; new/unmocked or apply-mode test runs are not authorized by this contract. Locked providers are AWS 6.70.0, AzureRM 4.81.0 and Google 7.46.1; missing or changed locks require reconciliation rather than an implicit update. Mock-plan/schema success proves neither provider permissions nor real networking, expiry, deletion or billing.
