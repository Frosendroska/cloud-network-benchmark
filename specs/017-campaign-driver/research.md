# Research and source reconciliation

Checked 2026-10-31 against live V2 and Runs/Budget saved by coordinator at `/private/tmp/cnb-campaign-live-sources.json`, current Thesis V2 snapshot, latest DECISIONS, EXECUTION_CHECKLIST, FRAMEWORK_IMPLEMENTATION_NOTES, EXPERIMENTS and implementation prompts. Live revision: `AHj4eMThPfnVdIjYc3raA3nq40Kuk9qQuA4dhSXUULpauhpvLax61ne9MvD-LhtlJhkhh2RagzvDL9csOBq4bro5C1pTlSoVjxL2Y71Rl5Q`. No current protocol conflict found; formal advisor approval remains unrecorded.

- Decision: Reuse initialize_campaign(schedule_entry=entry), load_schedule, lifecycle run/build_runtime/run_lifecycle and cleanup. Rationale: they already bind schedule snapshots, expiry, native measurement, post-copy validation and original recovery. Alternative: generic workflow platform rejected as outside scope.
- Decision: Persist all run IDs before dispatch and generate one immutable all-stage cost payload with config/provider envelope templates; bind only actual child run IDs compatibly. Rationale: current provider→row payload hardcodes one child run while ledger active_cost_source pins exact bytes. Different payloads for simultaneous P0/P1 reject source_mismatch; automatic source review per run would be false approval. Alternative: separate budgets rejected.
- Decision: Same stable controller session for cooperating simultaneous calls, one outer driver lock. Rationale: BudgetLedger intentionally allows active cleanup-open reservations for the same session, rejects different-session unresolved work and blocks cleanup failures. Restart still requires reconciliation. Alternative: relax ownership guard rejected.
- Decision: Per-entry private root. Rationale: build_runtime otherwise uses fixed lifecycle-recovery.json and provider private roots; collision-free IDs alone do not isolate top-level recovery.
- Decision: Portable analysis descriptors resolve copied paths, preserving native bytes and original descriptor/hash provenance. Rationale: existing analyze selection references descriptor files and ValidatorInput.child_result_root can contain runner absolute paths. Relative selection paths alone are insufficient.
- Decision: Start at measured pre-slot setup lead; lifecycle stabilization already waits for scheduled UTC start within lifetime. Rationale: launching at slot is too late for admission; launching hours early exceeds caps. Lead must derive from acceptance timing rather than invented readiness evidence.
- Decision: Registered V-S1, V-A-FAIL/V-Z-FAIL/V-G-FAIL and V-S3 only. Rationale: live run register lists these acceptance rows; physical failure remains supervised. Calibration and DAY each one separately retained experiment, caps 30/1460 minutes; short/acceptance 20 minutes. No extra pilot or automatic research repeat.
- Decision: Templates leave real account IDs, approval sources, refreshed prices, empirical N and exact windows pending. Rationale: dated Sheet forecasts ($147.82 working forecast) are planning estimates, not refreshed operation prices or authorization.

## Existing implementation blockers to resolve

1. Public init lacks schedule options although initialize_campaign accepts schedule_entry; driver must call the function with actual loaded row.
2. Cost payload only supports one call's run IDs; shared ledger requires one common exact cost source.
3. Runtime recovery filename requires per-entry isolation; Terraform/provider names/state plus fixed CIDRs/accounts need simultaneous-contract tests.
4. Portable local analyze needs descriptor relocation as well as relative selection paths.
5. Physical controller loss, live membership/deletion, expiry, actual credential renewal and empirical disk/lead measurements cannot pass offline; document them as pending acceptance evidence.

No package documentation web research was needed to alter the fixed protocol. Service and supported authentication instructions require current primary documentation verification by their owning writer before delivery.

## Deeper contract review — 31 October 2026

- Exact target config-ID acceptance matching could never admit legitimate calibration/DAY/research from their separate acceptance config IDs. Preserve legacy exact matching while verifying real completed acceptance campaign/child manifests, valid saved results and matching provider/scenario/VM/placement scope for new mappings. Human review links actual registered failure attempts; expected failed measurements remain failed while the recovery check may pass after explicit review. Arbitrary wrapper status fields cannot establish completion.
- AWS expiry input ID, path, digest and price were hardcoded to a dated tests/fixtures file. Permit an external actual source with exact saved hash/date/USD rates and recomputed cost while retaining two steps, 300-second discovery and 330-second action bounds. The funded expiry component must cover computed support; larger explicit reserves are valid.
- Generated configs must bind the new actual cost_input_id/date/source hash/estimate before manifest initialization; retaining old configured input ID makes every later reservation reject.
- AWS/GCP shared Terraform source roots need unique TF_DATA_DIR in each private attempt; Azure already has this isolation. Tool commands must use deployed PATH or validated operator paths, never development-machine absolute paths.
- A positive actual operator-input→prepare→runtime-loader→admission test is necessary; negative preflight tests and mocked launch boundaries alone cannot establish generated-contract usability.

These are engineering defects/compatibility boundaries, not changes to research protocol, budget or paid authorization.
