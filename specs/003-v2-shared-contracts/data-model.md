# F01R data model

This is the normative field contract for the implementer. Reuse the existing StrictModel and
Evidence wrappers; all models reject unknown fields and are immutable. Top-level serialized
configuration, resolved plan, manifest, provider handoff, validator/report and diagnostic records
carry `schema_version: 2`. Nested records inherit their enclosing version. No stored duplicate
`attempt_id`: it means `run_id` throughout. Names below are public imports from `contracts` unless
already defined in manifests.py/dry_run.py. Do not implement later-feature producers/consumers.

## Common values and invariants

| Type | Values / rule |
| --- | --- |
| ExperimentMode | `acceptance`, `calibration`, `short_study`, `diagnostic_day` |
| Provider / Scenario / VmRole / BenchmarkPhase | Preserve existing provider/scenario/role and idle_latency/single_flow/multi_flow enum values |
| DataValidity | `not_validated`, `valid`, `partial`, `invalid`, `unavailable` |
| ComparisonEligibility | `not_evaluated`, `eligible`, `ineligible`, `unavailable` |
| Existing execution/cleanup/campaign/command states | Preserve existing values and transition checks; do not reinterpret validation as execution |
| Identifier | Nonblank `^[A-Za-z0-9][A-Za-z0-9._-]{0,191}$`; no separators, traversal or whitespace |
| ConfigId | `^[a-z0-9][a-z0-9_-]{0,63}$`; distinct from design `EXP-[0-9]{3,}` |
| SHA-256 | 64 lowercase hexadecimal characters |
| Datetime | Timezone-aware, normalize to UTC; scheduled_start may be null for planning |
| Evidence[T] | Exactly one non-null `value` or nonblank `unavailable_reason`; never zero for missing |
| Relative artifact path | Normalized POSIX; no absolute path, `..`, `.`, backslash or symlink escape from child raw root |
| Measured values | Finite numbers; duration/size/delta nonnegative unless a documented reset is represented as unavailable |

## Configuration and resolved intent (US1)

Keep CampaignConfiguration, ResolvedCampaign, ResolvedObservation, BenchmarkPlan,
PhaseConfiguration, CampaignOptions, ProviderConfiguration, Pair, Placement and VmIntent names.
Remove FLENT test_name/upload_streams/disabled-phase semantics from active models.

| Record | Required fields and constraints |
| --- | --- |
| CampaignConfiguration | schema_version=2, config_id, experiment_id, mode, context=`standard`/`failure_check`/`replacement`, scheduled_start=aware datetime or null, selected_providers unique/nonempty, scenario, benchmark, provider_configs, options, admission |
| BenchmarkPlan | measured_seconds (acceptance 10, short/calibration 60, day 86400), warmup_seconds=5, pause_seconds=0 for diagnostic_day and 10 for every other mode in both input and resolution, stabilization_seconds=60, probe_lead_seconds=2, probe_bytes=64, probe_mps=1000, reply_every=1, full_rtt=true, no_rdtsc=true, tcp_congestion_control=`cubic`, tcp_zerocopy=true, tcp_buffer_bytes=131072, sample_interval_seconds=1, probe_chunk_seconds=300 for day/otherwise null, common_n: CommonNState |
| CommonNState | state=`pending`/`frozen`; common_n=null or integer 2/4/8, source_campaign_id=null or Identifier, source_sha256=null or SHA-256, plateau_found=null or bool. Pending requires all four null; frozen requires all observed and false plateau_found requires common_n=8 |
| PhaseConfiguration | unit_id Identifier, kind=`phase`/`calibration_trial`/`continuous_day`/`probe_chunk`, phase BenchmarkPhase, measured_seconds>0, streams=null or positive int, trial_index=null or int 1..12, parent_unit_id=null or Identifier. Idle streams=null; TCP streams=1 or common N/trial count; pending common N may be null only with launch blocker |
| CampaignOptions | Existing labels and timeout fields remain; timeouts are local stage limits, never additive promises that exceed overall cap. No script text, arbitrary command overrides or secret values |
| ResolvedCampaign | schema_version=2; original config identity/mode/context/scenario/providers/benchmark/options/admission; configuration_role derived from source root, config_source, resolved_sha256, scheduled_start, schedule_entry: Evidence[ScheduleEntry], measurement_units list[PhaseConfiguration], launch_blockers list[nonblank code], observations list[ResolvedObservation] |
| ResolvedObservation | Same config/mode/context/benchmark/units/admission/schedule linkage as parent; provider, scenario, vm_a/vm_b intent, measurement_direction=`vm_a_to_vm_b`, placement, provider_config, options and mode-derived ArtifactLayout |
| ConfigProvenance | Retain source_path, exact-byte sha256/byte_length, optional snapshot_path. Add resolved_sha256; implementation/design identity uses GitSnapshot evidence in manifests, not invented during pure config resolution |

Cross-field rules: provider_configs keys exactly equal selected_providers. Standard short study,
calibration and DAY require all three providers. Acceptance standard requires all three;
registered single-provider validation uses context=failure_check, replacement uses context=replacement
with short_study plus replacement metadata. A subset is not allowed simply because config is in
configs/tests. Calibration and DAY require same_zone. Scenario/placement constraints remain and
all short/acceptance modes have all three ordered phases, including S4. Calibration's derived units
are 12 multi_flow trials, streams 1/4/2/8 then 8/2/4/1 then 2/8/1/4; trial index is global 1..12.
DAY has one day unit, with later dynamic child probe chunks and no idle/single-flow unit.
Calibration ignores pending common N for its trial counts. Acceptance resolves aggregate N=4 if
pending, otherwise frozen N. Short study/DAY pending common N stays null with `calibration_pending`;
missing scheduled slot is `schedule_pending`. All previews/init also have `admission_not_evaluated`.

Nested provider-intent field names are `os`, `disk`, `network`, `security`, `acceleration`;
strict nested model class names may follow existing package style. They are not independently
versioned envelopes. Unknown fields and unexpected provider options are rejected before export.
Embedded secret-bearing keys/material (credential/token/password/private-key values and PEM
material) are rejected before writing source/row snapshots; configured redaction literals must
never appear in errors, manifests, argv or stdout/stderr. Runtime credential references are opaque
non-secret identifiers. Hashing source bytes does not permit exporting secret-bearing source.

### ProviderConfiguration and VmIntent

Retain explicit provider, regions/zones Pair, selected vm_shape/image/connection_user,
documented_network_limit, Placement and provider_options. Add structured intent (strict nested
models, exported through ProviderConfiguration) rather than an unconstrained options bag:

- `os`: distribution=`ubuntu`, release=`24.04`, architecture=`amd64`; image remains requested
  provider image reference, not actual immutable image identity.
- `disk`: size_gib=32, kind AWS `gp3` / Azure `StandardSSD_LRS_E4` / GCP `pd-balanced`.
- `network`: private_ipv4=true, public_ipv4_per_vm=1, mtu=1460, tcp_congestion_control=`cubic`,
  dscp=0, ecn=false, default_offloads=true; no NAT/bastion/managed monitoring/data disk.
- `network` also declares `address_spaces` and `subnets` by region: exact handoff ranges
  (AWS 10.60.0.0/16 and London 10.61.0.0/16 with Frankfurt /24 subnets .1/.2;
  Azure 10.70.0.0/16 and 10.71.0.0/16 with /24 subnets; GCP 10.80.1.0/24 and
  10.81.1.0/24). Choose the first /24 in Azure ranges as the ordinary subnet assumption.
  Config supports same ranges in isolated run-scoped networks; concurrent S1/S3 networks are not
  peered. Validate intra-run overlap, not a false global prohibition on isolated networks.
- `security`: controller_cidr Evidence[str] (pending before launch; observed must be IPv4 /32),
  sockperf_udp_port=11111, iperf3_tcp_port=5201, benchmark_source=`vm_a_private`,
  strict_host_key_checking=true, batch_mode=true. Runtime refs only, never private key material.
- `acceleration`: AWS `ena`, Azure `accelerated_networking`, GCP `gvnic`; no EFA/Express/Tier_1.
- Provider options: AWS instance_market_type=`on_demand`; Azure priority=`Regular`,
  eviction_policy=`Delete`, max_price=-1.0 (existing inert compatibility fields, not Spot support);
  GCP provisioning_model=`STANDARD`, instance_termination_action=`DELETE`.
- Placement: existing kinds; Azure standard PPG; GCP `collocated=true`, `max_distance=null`
  (do not permit max_distance for the selected protocol). Add these as explicit optional fields
  on Placement, with provider/scenario validation, not as generic Terraform overrides.

Shapes: AWS m7i.xlarge, Azure Standard_D4s_v5, GCP n2-standard-4. Requested AWS zone names use
`eu-central-1a/b`, B in `eu-west-2a` for S4. Observed physical zone IDs are separate metadata.
Regions/zones and provider placement must match the source scenario matrix. No silent substitutions.
VmIntent carries os/disk/network/security/acceleration intent from its ProviderConfiguration in
addition to existing role/function/locality/image/shape/user; it must equal its source configuration.

## Identity, manifests and provenance (US2)

| Record | Fields and rules |
| --- | --- |
| IdentityContext | experiment_id, config_id, campaign_id, observation_id, run_id, deployment_id; all IDs valid; run_id ends `-<provider>` when provider supplied. Optional replaces_run_id. attempt_id is documentation alias only |
| First attempt | Existing campaign candidate format retained; run_id=`<campaign_id>-<provider>`; observation_id=`<run_id>-observation`, deployment_id=`<run_id>-deployment`. Later replacement may explicitly supply observation_id/replaces_run_id; never reuse attempt path |
| Retained units | Each PhaseConfiguration or artifact/unit record links to same provider deployment; chunks/trials do not allocate result roots/reservations |
| GitSnapshot | commit: StringEvidence, dirty: Evidence[bool], diff_sha256: StringEvidence. Clean observed state has deterministic empty-state hash; unavailable dirty/hash has reason. Commit SHA 40/64 hex when observed; injected fake test commits must be valid |
| ManifestConfigProvenance | Existing source identity/snapshot path; resolved_sha256 and resolved_snapshot_path; implementation_git: GitSnapshot, design_git: GitSnapshot; source and resolved identities do not overwrite each other |
| CampaignManifest | Existing parent fields/type and chronology, schema_version=2, config_id/mode/context, optional scheduled_start, schedule_entry Evidence, admission requirements, reservation: Evidence[ReservationEvidence], child links and aggregate state; known config/run identity never unavailable |
| ChildRunManifest | Existing child type/parent/provider/scenario/lifecycle/failure/metadata/tool fields plus config_id/mode, observation_id/deployment_id/replaces_run_id, optional scheduled_start, schedule_entry Evidence, measurement_units, validation_status, commands/timings/artifact_records lists initially empty, reservation/expiry evidence initially unavailable |
| Failure fields | Keep failure and cleanup_failure; add retrieval_failure independently. FailureEvidence remains category/message/aware time/action/details after redaction; first primary failure preserved |
| ValidationStatus | data_validity default `not_validated`, data_reasons list[str], comparison_eligibility default `not_evaluated`, comparison_reasons list[str]; nonvalid/partial/ineligible/unavailable evaluations need nonempty reasons. Eligible requires valid data, not merely successful execution |

Init writes source `config.yaml`, resolved `config.resolved.json` and, only when supplied, original
`schedule.csv` row bytes into each immutable child root and records both hashes. The exact executed
row is unavailable for YAML-only init, not an empty fabricated CSV. A canonical resolved JSON hash
is over sorted-key compact UTF-8 serialization excluding source hash/provenance and its own hash;
include the resolved experiment intent and supplied schedule identity, not generated runtime IDs.
Preserve parent/child partial-allocation recovery and existing exclusive/atomic writers.

## Native evidence and validation handoffs (US3)

| Record | Fields and constraints |
| --- | --- |
| ArtifactInput | Existing expectation enum; artifact_id, path or declared path_template, kind, producer VmRole/controller, unit_id optional. Exactly one path/path_template; concrete paths normalized under child root. Templates are declarations, never claimed files |
| ArtifactRecord | artifact_id, path, kind, producer, unit_id, deployment_id, closed bool, size_bytes Evidence[int≥0], sha256 StringEvidence, retrieval_state=`not_retrieved`/`verified`/`failed`, failure optional. Verified requires closed=true and observed size/hash; no fake success before materialization |
| ArtifactLayout | Source/resolved/schedule, redacted provider outputs, metadata/diagnostics, server logs, validation and checksum paths, plus deterministic unit paths and dynamic probe template; no legacy FLENT fields |
| CommandRequest/Result | Preserve existing run(request)->result interface, shell=false, exact argv, action identity, outcomes/timeouts and sensitive stdin/env exclusion. Durable view redacts configured literal secrets in argv/stdout/stderr/error detail; request records environment key names/classification only |
| CommandEvidence | request durable view plus result durable view, producer role, deployment_id/unit_id, actual binary/version/source identity Evidence, monotonic start/finish Evidence; no shell content or credentials persisted |
| ClockMapping | vm_role, monotonic_seconds≥0, utc_timestamp, estimated_error_ms Evidence[float≥0], synchronization_source StringEvidence; cross-machine alignment cannot claim ≤100ms if unknown |
| MeasurementTiming | unit_id/deployment_id, probe_test_started_at Evidence[aware datetime], tcp_started_at Evidence, measured_start/end Evidence, monotonic_start/end Evidence, omitted_seconds≥0, overlap_seconds Evidence, gaps list[TimingGap], clock_mappings list; validate observed interval chronology, not later short-policy decisions |
| TimingGap | start/end aware timestamp, reason nonblank; end≥start; missing duration may use unavailable evidence rather than fictitious zero |
| DiagnosticSample | schema_version=2, deployment_id, vm_role, UTC/monotonic timing, interval_seconds>0, cpu_total_percent/cpu_max_core_percent/cpu_steal_percent each Evidence[0..100], tcp_retransmissions/interface_rx_drops/interface_tx_drops/interface_rx_errors/interface_tx_errors/rx_bytes/tx_bytes deltas Evidence[int≥0], reset_fields list[str], gap_reason optional. Reset makes affected delta unavailable with reason |
| VmMetadata | schema_version=2, deployment_id/vm_role/provider, cpu_model/nic_driver/acceleration/image/kernel/congestion_control/tool_versions/source_builds/documented_network_limit evidence, mtu Evidence[int>0], observed_region/zone/physical_zone_id and actual_shape evidence. Partial values are honest, never intended image presented as observed |
| ValidatorInput | schema_version=2, identity: IdentityContext, mode/context/provider/scenario, parent/child/raw/config/resolved/schedule paths+hashes/evidence; measurement_units, artifacts and observed records, commands/timings/diagnostics/metadata evidence, execution/cleanup/failure/retrieval_failure/cleanup_failure, ValidationStatus, GitSnapshot identities, reservation/expiry evidence |
| ValidationFinding | code/message nonblank, severity=`error`/`warning`/`info`, optional artifact_id/unit_id; codes extensible for F08/F11 policy without new enum amendment |
| ValidationResult | schema_version=2, run_id/mode, validation_status, findings list, metrics StructuredEvidence (F08 owns metric-specific structures), evaluated_at aware time; no parser implementation here |

Metadata evidence may use existing StructuredEvidence for tool/source version maps and the documented
network-limit record. Diagnostic keys/units above are fixed public names; collector parsing and
statistical interpretation are later work. Pending status does not require invented measurements.

## Provider handoff field shape (US3)

Retain the existing flat fields in ProviderDeploymentInput: schema_version=2, campaign_id, run_id,
experiment_id, provider, scenario, scheduled_start (nullable planning time), vm_a, vm_b, placement,
provider_options, campaign_options, bootstrap_template, bootstrap_inputs, terraform_directory,
child_result_path, provisioning_timeout_seconds/readiness_timeout_seconds/cleanup_timeout_seconds,
config_sha256, implementation_git_commit and design_git_commit. Add flat config_id, mode,
observation_id, deployment_id, replaces_run_id optional, resolved_sha256, admission:
AdmissionRequirements, reservation: Evidence[ReservationEvidence], expiry: Evidence[ExpiryEvidence].
Keep existing commit evidence fields for compatibility; full GitSnapshot belongs in manifests.
Do not also store a second nested identity field. from_observation accepts missing safety evidence
as explicit unavailable during preview, not as successful registration or launch permission.

ProviderDeploymentOutput retains run_id/provider/scenario/apply_action_id/started_at/finished_at,
vm_a/vm_b/provider_metadata/documented_network_limit/output_artifact_path. Add schema_version=2,
deployment_id, mode and expiry: Evidence[ExpiryEvidence]; scenario is required, not Optional.
output_artifact_path is exactly `terraform-outputs.redacted.json`. ProviderVmOutput retains existing
fields and adds public_ipv4: Evidence[str] and physical_zone_id: StringEvidence; its existing
private_ipv4 is observed benchmark address, never equated with management connection.host.
Partial output must provide unavailable evidence; missing known identity/scenario is invalid.
RemoteConnectionData retains role/host/port/user/authentication_reference/host_key_reference,
readiness_expectations and failure. host_key_reference is now required nonblank, port remains
1..65535 with default22, host must be valid non-unspecified/non-multicast IPv4. Provider fixtures
may use private management hosts; real intent requests public IPv4. A connection may be absent
on partial output; consumers never substitute private benchmark address for an absent host.

## Schedule, reservation and expiry DTOs (US3)

Use strict immutable contracts/admission.py records, not scheduler/provider implementations.

| Record | Fields and rules |
| --- | --- |
| ScheduleEntry | schema_version=2, entry_id Identifier, config_id, experiment_id, scheduled_start aware or null, window_label null/night/morning/afternoon/evening, source_path/source_sha256/row_sha256 (hash exact row bytes), original_header str, original_row str, priority P0/P1/P2/P3, replaces_run_id optional. Preserve exact raw header and row bytes as strings plus ordered `columns: list[str]`; snapshot joins those preserved strings, never a regenerated CSV representation |
| AdmissionRequirements | priority P0/P1/P2/P3 (scenario-derived), lifetime_cap_seconds (acceptance/short=1200, calibration=1800, day=87600), hard_budget_usd=300, protected_buffer_usd=50, cost_input_id nonblank, prices_checked_on date, cost_input_sha256 StringEvidence, estimated_cost_usd Evidence[finite≥0], requires_common_n/requires_schedule/requires_primary_review/requires_expiry booleans derived by mode/scenario; no admission decision |
| ReservationRequest | schema_version=2, campaign_id/config_id/schedule_entry_id Evidence, selected_providers, mode/priority, lifetime_cap_seconds, estimated_cost_usd Evidence, input source identity, incurred_cost_usd Evidence, active_reserved_usd Evidence, outstanding_cleanup bool; one request per entire retained campaign |
| ReservationEvidence | schema_version=2, campaign_id, reservation_id Evidence, state=`not_evaluated`/`reserved`/`rejected`/`released`, reserved_cost_usd Evidence, cap_started_at/expires_at Evidence[aware datetime], cap_start_policy_id StringEvidence, source identity, reasons list. Reserved requires observed ID/cost/cap times/policy and expiry later than start; F11 computes/creates it |
| ExpiryRequest | schema_version=2, run_id/deployment_id/provider, action=`stop` for aws/gcp or `deallocate` for azure, expires_at Evidence, reservation_id Evidence; contains resource namespace/target_selector nonblank so registration may precede VM IDs |
| ExpiryEvidence | schema_version=2, run_id/deployment_id/provider, action, state=`not_registered`/`registered`/`failed`/`cancelled`/`triggered`, registration_id Evidence, registered_at/expires_at Evidence, target_selector, provider_metadata StructuredEvidence, failure optional; registered requires observed ID/times; failure requires FailureEvidence |

No ExpiryEvidence alone asserts complete cleanup. `outstanding_cleanup=true` remains a blocker.
Actual registration-before-create enforcement is F05–F07/F09; F01R validates representable ordering
where supplied evidence exists and cannot mark an initialized bundle as registered/reserved.
