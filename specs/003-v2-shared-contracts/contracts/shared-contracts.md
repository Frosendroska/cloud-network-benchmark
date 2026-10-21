# F01R public shared contracts

Normative shapes and constraints: [data-model.md](../data-model.md). These are the shared interface
baseline to merge before dependent features. F01R implements DTOs, local intent/snapshot handling,
serialization and offline proof; downstream behavior is expressly excluded.

## Existing entry points and compatibility

Preserve package import names and the existing shell-free external CommandRunner protocol.
Extend these functions with keyword-only optional context, retaining existing callers:

```text
load_campaign(path, repository_root)
  -> (CampaignConfiguration, exact_source_bytes, ConfigProvenance, configuration_role)
resolve_campaign(path, repository_root, *, schedule_entry=None)
  -> (ResolvedCampaign, exact_source_bytes)
initialize_campaign(config_path, repository_root, results_root, clock=..., token_source=...,
                    implementation_git=None, design_git=None, fault=...,
                    *, schedule_entry=None, replacement_context=None)
  -> InitializedCampaign
build_dry_run(resolved) -> DryRunReport
artifact_inputs(layout, *, mode, measurement_units, schedule_supplied=False)
  -> dict[str, ArtifactInput]
ArtifactLayout.for_mode(mode, measurement_units) -> ArtifactLayout
RepositoryGitProvenance.commit(repository) -> StringEvidence
RepositoryGitProvenance.snapshot(repository) -> GitSnapshot
StaticGitProvenance.snapshot(repository) -> GitSnapshot
CommandRunner.run(CommandRequest) -> CommandResult
ScriptedCommandRunner.assert_exhausted() -> None
```

`replacement_context` is optional IdentityContext-compatible observation/replaces-run metadata,
validated against short_study/context=replacement and selected provider. First attempts need none.
No F01R retry launcher. Preserve `initialize_campaign` result identity/path fields, adding new
identity fields rather than changing results root ownership. Source bytes stay exact; resolved
JSON and optional original row get distinct hashes. `schedule_entry` is a supplied validated DTO;
F11 owns CSV file parsing and invocation, not this loader. Unknown/untrusted raw CSV is never
silently interpreted as a plan. Caller cannot override config mode or fixed settings through row.

`artifact_inputs` replaces the former `multi_flow_enabled` boolean: active V2 never omits the
aggregate phase in short/acceptance. It describes expected input, never parses a native payload.
Old keyword use is rejected with migration guidance rather than silently mapped.
`ARTIFACTS` may remain as a base layout constant, but has no FLENT slots; mode/unit paths require
for_mode. All new public DTOs/enums in data-model.md are exported through contracts/__init__.py.
Cross-record validator checks compare actual identity/hash/mode, not only independent schema shape.

## Mode intent and executable config matrix

| Config path | config_id / mode / context | Scenario / priority / provider scope |
| --- | --- | --- |
| configs/experiments/exp-001-multi-provider.yaml | exp-001-short / short_study / standard | same_zone/P0/all three |
| configs/experiments/exp-002-cross-zone.yaml | exp-002-short / short_study / standard | cross_zone/P2/all three; paid review required |
| configs/experiments/exp-003-placement-optimization.yaml | exp-003-short / short_study / standard | placement_optimization/P1/all three |
| configs/experiments/exp-004-inter-region.yaml | exp-004-short / short_study / standard | inter_region/P3/all three; paid review required; all three phases |
| configs/experiments/exp-001-calibration.yaml | exp-001-calibration / calibration / standard | same_zone/P0/all three retained pairs |
| configs/experiments/exp-001-diagnostic-day.yaml | exp-001-day / diagnostic_day / standard | same_zone/P0/all three retained pairs, separate from calibration |
| configs/tests/exp-900-single-provider.yaml | exp-900-acceptance / acceptance / failure_check | same_zone/P0/aws; registered V-A-FAIL context |
| configs/tests/exp-901-azure-single-provider.yaml | exp-901-acceptance / acceptance / failure_check | same_zone/P0/azure; registered V-Z-FAIL context |
| configs/tests/exp-902-gcp-single-provider.yaml | exp-902-acceptance / acceptance / failure_check | same_zone/P0/gcp; registered V-G-FAIL context |
| configs/tests/exp-903-three-provider-smoke.yaml | exp-903-acceptance / acceptance / standard | same_zone/P0/all three; V-S1 context |

Retain existing EXP identifiers: retained S1 configs use EXP-001; config_id distinguishes them.
All committed scheduled_start values are null; exact study dates/windows are not yet known.
All common_n defaults are pending. Acceptance resolves pre-calibration N=4; calibration derives
its 12 stream counts. Research preview shows pending N; fixtures may use explicitly synthetic
frozen calibration evidence to test the contract, never presented as research output.
The matrix is 10 configs; fixture expectations must enumerate intent, not preserve old count=8.
S3 acceptance and replacements can be supplied as mode/context fixtures without extra committed
config copies. Registered labels link failures, but those configs do not run failure injection.
configs/README maps each config to an actual current Thesis table/section and design ID; traceability
checks must match real content (no dependency on an obsolete `### EXP-001` heading).

All three short phases use fixed order idle_latency/single_flow/multi_flow. Idle Sockperf durations
are 65 seconds (acceptance 15); loaded probe acquisitions 70 (acceptance 20); iperf3 uses measured
60 (acceptance 10) plus `-O 5`. Calibration loaded acquisitions use the same 60-second protocol.
DAY TCP is 86,400 measured seconds plus one omission; probe chunks ≤300 seconds record gaps and
do not restart TCP. F01R describes these settings/units; F03 builds/runs exact argv and timing.

## Native bundle layout and evidence

Keep `results/manifests/<campaign_id>.json`, child `results/manifests/<run_id>.json` and
`results/raw/<run_id>/`. All concrete file destinations are exclusive, normalized within the child
root and checked against symlink escape. Runtime records may append only new artifact IDs/paths;
closed raw bytes are immutable. Known templates are declarations, never evidence of existing files.

```text
config.yaml                         exact source bytes
config.resolved.json                resolved canonical intent
schedule.csv                        exact executed row + header only when supplied
terraform-outputs.redacted.json      provider output; no state/credentials
checksums.sha256                     final bundle checksums, produced later
metadata/{vm_a,vm_b}.json
diagnostics/{vm_a,vm_b}.jsonl
server-logs/sockperf.log
server-logs/iperf3-server.jsonl
server-logs/iperf3-server.err
measurements/<unit_id>/commands.json
measurements/<unit_id>/timing.json
measurements/<unit_id>/sockperf.csv
measurements/<unit_id>/stdout.log
measurements/<unit_id>/stderr.log
measurements/<unit_id>/iperf3.json    short/acceptance/each calibration TCP trial
measurements/day/iperf3-client.jsonl  day only
measurements/day/probes/<chunk_id>.csv
measurements/day/probes/<chunk_id>.log
validation/result.json
validation/summary.txt
```

Mode artifact expectations: idle has Sockperf+commands/timing/logs but no iperf3 client output;
loaded short/calibration units have both tools; DAY has one streaming client and dynamic probe
pattern, never idle/single-flow files. Receiver server JSONL, diagnostics and metadata are produced
on B/both roles in all modes; Sockperf/client output on A. Controller owns source/resolved/schedule,
provider output and later validation/checksum files. Validation output/checksums are optional at
initialization and required only at later sealing/validation stage; nonexistent outputs must not be
claimed verified. Schedule is not_expected when no row was supplied. Native counts retain tool
reporting scope, not inferred packet loss; exact parsing is F08. Retrieval happens only after files
close at experiment end; no remote checkpoints or automatic disk recovery.

## Provider and safety boundaries

ProviderDeploymentInput retains flat identity fields and adds the exact fields in data-model.md;
it carries schema version, source hashes, complete
VM intent and AdmissionRequirements plus reservation/expiry evidence. It may be previewed while
safety evidence is unavailable. Provider implementations MUST require registered expiry and
reserved lifetime before resource creation; F01R does not perform the side effect or grant it.
ProviderDeploymentOutput requires schema_version/run_id/deployment_id/mode/provider/scenario and
redacted canonical output path above. Known scenario is required on failed/partial outputs.
Each VM output preserves existing complete-or-explained-partial behavior plus observed public
management and private benchmark identities; host need not equal private IPv4.

RemoteConnectionData.host is a syntactically valid management IPv4 (public in real design; distinct
private test addresses remain permissible fixtures), port 22, role/user, nonblank runtime
`authentication_reference` and `host_key_reference`. References are opaque identifiers, not
private-key contents; providers/F02R resolve and authenticate them. Invalid/partial connection
must remain absent with unavailable evidence, not a usable placeholder. SSH key validation and
listener/readiness behavior remain F02R; no broadened insecure SSH flags.

ReservationRequest/Evidence and ExpiryRequest/Evidence fields are defined in data-model.md.
Providers must never treat compute-stop/deallocation as proof that billable disks/addresses are
gone. Evidence timestamps allow registration-before-create ordering; unknown policy times remain
unavailable. F11 chooses cap-start and admission; F05–F07 choose minimal supported provider
mechanisms; F09 enforces sequence and cleanup blocking. No generic implementation is added here.

## Versioning, schemas and local CLI

Existing commands remain `validate`, `resolve`, `dry-run`, `init` with --config and --format.
`init` also takes --results-root. No `run`/cloud command or CSV schedule executor is introduced.
Human/machine output must show mode, units, pending state, scope, admission blockers and native
paths, rather than FLENT terminology. Dry-run does not allocate IDs or create any files; init
allocates local evidence and labels all measurements/validation/reservations unavailable/pending.
Invalid config/version returns the existing validation exit-code convention and redacted error
with field path; no secret input value is echoed.

The implementer adds a small `contracts/schema.py` module with an explicit export map and
`--output <directory>` / `--check` module entry point. It exports Draft 2020-12 from runtime models
and checks byte-deterministic canonical JSON. This is a planned command, not available at planning:

```text
python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts
python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts --check
```

Publish separate schemas for CampaignConfiguration, ResolvedCampaign, CampaignManifest,
ChildRunManifest, ProviderDeploymentInput, ProviderDeploymentOutput, ValidatorInput,
ValidationResult, ScheduleEntry, ReservationRequest, ReservationEvidence, ExpiryRequest,
ExpiryEvidence, DiagnosticSample and VmMetadata using kebab-case filenames (`campaign-config`,
`resolved-campaign`, `campaign-manifest`, `child-run-manifest`, `provider-deployment-input`,
`provider-deployment-output`, `validator-input`, `validation-result`, `schedule-entry`,
`reservation-request`, `reservation-evidence`, `expiry-request`, `expiry-evidence`,
`diagnostic-sample`, `vm-metadata`) plus `.schema.json`.
Schemas validate structural/mode constraints and serialized examples; runtime validators additionally
check cross-record equality, chronology and filesystem integrity as documented here. Required fields,
unknown-field rejection, evidence exclusivity, provider/mode literals and finite numeric bounds
must agree in schema and runtime. Do not edit historic schemas or claim schemas perform file I/O.

## File ownership after F01R merge

| Feature | Consumes | Owns later behavior/files |
| --- | --- | --- |
| F02R | deployment/connection/artifact/command records | access.py/bootstrap.py/readiness.py/retrieval.py and remote tests; native build/listener/copy logic |
| F03 | mode/measurement-unit/timing/artifact/command records | new measurement runner module and process/clock tests; exact tool argv/overlap/chunking |
| F04 | DiagnosticSample/VmMetadata/ClockMapping | new local collectors and Linux fixtures; actual counters, reset/gap/clock capture |
| F05/F06/F07 | ProviderDeploymentInput/Output and expiry records | explicit provider modules/Terraform, registration/cleanup; provider-owned tests |
| F08 | ValidatorInput/ValidationResult/native records | parsers/statistics/eligibility in new validation modules; native fixtures |
| F11 | ScheduleEntry/AdmissionRequirements/reservation records | CSV scheduling/admission/budget/cap-start and replacement policy modules |
| F09 | all merged interfaces | lifecycle integration/CLI launch; no F01R runtime readiness claim |
| F12 | F08 outputs and native evidence | deterministic report/analysis driver; thesis interpretation stays Thesis |

F01R implementer owns the source files declared in plan.md, these schemas and matching tests/configs.
Downstream features consume merged public contracts; changes to them require a serialized,
justified amendment coordinated before parallel consumers proceed. No unfinished-sibling imports.
