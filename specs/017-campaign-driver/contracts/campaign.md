# Campaign integration contract

These interfaces are the planning handoff; owning writers may refine callable shape together while preserving behavior.

## Public CLI

`campaign check --plan PATH [--settings PATH]`, `campaign start --plan PATH --execute [--settings PATH]`, `campaign status [--settings PATH]`, `campaign logs --follow [--settings PATH]`.

Default settings path is `/etc/cloud-network-benchmark/operator.yaml`. Settings use `stage_inputs` for per-stage values. Default settings resolution must make the user's exact commands work after one deployment setup. Check and start-without-execute make no resources/benchmarks/reservations. Missing inputs return nonzero and actionable key/path errors. A private service entry point may run the stage foreground under systemd; explicit operator start hands off once and survives terminal exit. Status/logs never launch.

## Writer boundary

`campaign_inputs.py` (coordinator) owns actual source/settings parsing and generation. A plan uses `stage: acceptance|calibration|day|week` and optional `configs` alias overrides. Other campaign modules (implementer) own journal, scheduling, command dispatch, monitoring/export and campaign CLI; `cli.py` routes to it. `admission.py`/`lifecycle_runtime.py` shared fixes belong only to coordinator. Tests belong to checker.

Agreed functions: `load_operator_settings(path) -> dict`; `validate_stage_inputs(plan, settings, root) -> list[str]` read-only; `prepare_stage(plan, settings, root, state_root, results_root, clock) -> dict` raises actionable ValueError/OSError for invalid/missing actual input. Input generation writes internal source-bound files and initializes all manifests; driver alone persists the overall journal and launches.

Prepared stage contains `stage_id`, `controller_session_id`, `ledger_path`, `scope_id`, `entries`. Every entry contains `entry_id`, `config_path`, `schedule_path`, `row_id`, `scheduled_start`, `launch_at`, `runtime_config_path`, `cost_inputs_path`, `parent_manifest_path`, `run_ids`, `private_root`. Paths become frozen absolute runner paths; schedule datetimes are UTC ISO strings. All entries use one cost input path/hash, budget scope/path and stable stage session. Each private_root is unique. Acceptance may omit schedule; retained CAL/DAY uses one actual scheduled row each.

Run command reuses existing `lifecycle run` with config, schedule/row, cost, runtime, results/private roots, shared session and `--execution-scope paid_request --format json`. Runtime load and admission keep their existing strict evidence checks. Persist starting before spawn. A crash in the launch window is ambiguous and requires original private journal/manifests/resource reconciliation, not paid retry. Cleanup uses existing `lifecycle cleanup --recovery ORIGINAL_PATH --controller-session CURRENT_SESSION`.

## Shared cost source compatibility

Preserve existing `provider_run_envelopes` provider→row shape. Add optional `provider_run_envelope_templates` config_id→provider→template row, exactly legacy envelope fields except run_id. One immutable all-stage source contains all planned configuration templates from real dated operator price components. build_admission_request binds actual initialized child run_id; _validate_cost_envelopes compares every other public envelope field against its exact template and enforces child identity, regions, components, dates and funding. This permits acceptance/calibration/DAY/WEEK and concurrent P0/P1 to share the same ledger source bytes without per-run human source review. Do not relax active_cost_source, synthetic-source rejection, ownership, cleanup or funding guards. An actual price/funding/source change still requires existing human source reconciliation.

## Portable selection

Existing selection format: `{"format_version":1,"runs":[{"validator_input":"relative/validator.json","validation_result":"relative/result.json"}]}`. Descriptor paths resolve relative to selection. ValidatorInput.child_result_root and artifact references must resolve inside the copied export; preserve original descriptor bytes or source hashes in export provenance if writing relocated descriptors. Do not mutate immutable native files. Prove with local analyze after moving the export and hiding original runner paths.
