# F01R research and engineering decisions

Source baseline: implementation `e7fc15ff1a7d6a4aa43dfebe5209592689a87fa7`, constitution 2.0.0.
Thesis protocol files were read at `d45655364ef21423cfe6cae177e1eec23b44acf9`; current tracker-only
revision `f76dfd230b7951fbd65994d8c7fa46bc6bb93291` records R00 delivery without research changes.
Read: design §§3–10, DECISIONS 17–19 October, full FRAMEWORK_IMPLEMENTATION_NOTES, experiment
index, M04, F01R prompt and roadmap dependencies. The coordinator's read-only
`/private/tmp/cnb-run-register-source.json` agrees on 20/30/1,460-minute caps, $300 total/$50
protected buffer and the $147.82 dated-input forecast. No paid admission or fresh price claim.

## Inspection findings

- Existing `contracts/campaign.py` and config resolution require version 1, fixed FLENT phase
  fields, a fabricated historical schedule in every YAML and optional S4 aggregate exclusion.
- `artifacts.py`, dry-run and validator inputs assume `.flent.gz` and per-phase diagnostic files.
  V2 requires native formats and mode-aware measurement units instead.
- Manifests already provide exclusive allocation, atomic persistence, failure recovery, parent
  links, evidence wrappers, linked-worktree/packed-ref commit provenance and a fakeable command
  boundary. Extend these mechanisms; do not replace them with a second framework.
- Published schemas currently live in historical specs/001. They must remain historical;
  current V2 schemas will live with this feature and tests must resolve the new location.
- F02 accepts shared deployment outputs; `scenario` is optional at the provider boundary but
  mandatory at remote handoff. Management host currently equals private benchmark address.
  Shared DTO changes and matching test fixture updates belong here; remote behavior stays F02R.
- Baseline test execution during planning is recorded in quickstart.md. These old assertions must
  be migrated to explicit V2 expectations, preserving rejection checks rather than marking xfail.

## Decisions

### D01 - One active version-2 boundary

Decision: retain existing public class/function names where they still serve their purpose;
replace active schema_version=1 contracts with literal 2 and reject legacy/missing versions with
`legacy_schema_unsupported`/`unsupported_schema_version` validation reasons. Preserve historical
specs and schemas byte-for-byte. Publish version-2 JSON schemas under this feature's contracts/.
Rationale: a second execution engine or automatic native conversion would hide a protocol change.
Alternatives: dual loaders or in-place rewriting old bundles add unsupported compatibility scope.

### D02 - Pending intent is valid planning, never launch authority

Decision: YAML scheduled_start may be null and research calibration state may be pending.
Validate/resolve/dry-run and local init expose typed blockers; they cannot grant admission.
No hardcoded date, research N=4 or false validation result. Benchmark mode settings are source
constants/defaults with exact per-mode constraints; changing them is not this migration's job.
Rationale: executable configs must exist before real calibration and diagnostic window selection.
Alternatives: invent N/date or reject every research config would either fabricate evidence or
prevent offline preparation. F11/F09 consume the pending state before actual launch.

### D03 - Preserve simple identity and immutable paths

Decision: campaign_id identifies one invocation across selected providers; run_id remains one
provider attempt, with `attempt_id` a documented semantic alias rather than duplicate storage.
Add observation_id stable across replacement attempts and deployment_id unique per created pair;
all mode units share their provider deployment. Initialize the first observation/deployment from
run_id deterministically; later explicit replacement context preserves observation/replaces_run_id.
Rationale: existing path/collision guarantees remain useful while independent deployment is clear.
Alternatives: a second run table/database or separate attempt_id value would introduce ambiguity.

### D04 - Typed handoffs, no downstream engines

Decision: add small admission.py and measurements.py contract modules only where existing files
lack an owner. ScheduleEntry, ReservationRequest/Evidence and ExpiryRequest/Evidence are immutable
records. F01R accepts supplied records and validates consistency; F11 reads CSV and decides
admission; F05–F07 implement expiry; F03/F04 produce measurement records; F08 parses/evaluates them.
Rationale: parallel features need concrete types without depending on unfinished sibling logic.
Alternatives: generic plugin/event/transport infrastructure would exceed the small framework.

### D05 - Provider distinction is intent plus observed evidence

Decision: retain ProviderConfiguration/Pair/Placement and explicit provider options; extend them
with the fixed network/disk/OS/acceleration intents in data-model.md. Remove Spot options from
accepted V2 values. AWS requested zone names are eu-central-1a/1b and eu-west-2a; exposed physical
zone IDs are observed metadata, not substitute requested names. Require scenario in every provider
output. RemoteConnectionData.host is the management IPv4, separately from private_ipv4; valid
connection records require authentication and strict host-key references. No SSH execution change.
Rationale: known scenario is never unavailable, and public management access is distinct from the
private measurement path. Provider drivers later prove mapping, routes and host identity.
Alternatives: preserve private-host equality or interchangeable zone IDs would contradict V2.

### D06 - Native evidence is mode-aware and honest

Decision: use mode-aware fixed artifact layout plus append-only observed ArtifactRecord entries
for runtime chunks. Plan finite phase/trial units; describe day probe chunks as dynamic without
allocating 288 fake measurements or claiming coverage. Every record carries source VM/unit,
closed/integrity evidence and timestamp scope. Native payload parsing and numerical statistics
remain F08. One-second diagnostics have typed values or reason/reset/gap states, no warnings.
Rationale: storage declarations must not fabricate files, receiver values or complete coverage.
Alternatives: universal FLENT slots, required Parquet or eager fake day files obscure the evidence.

### D07 - Git provenance uses ordinary local Git with injected boundary

Decision: preserve commit() compatibility and add snapshot() returning commit, dirty state and
content-diff SHA-256 evidence. Use a narrowly injected local Git reader, separate from external
cloud command runner. Hash tracked staged/unstaged binary diff plus sorted nonignored untracked
relative names/content with length delimiters; never persist diff or file contents. Missing Git,
unreadable content, permissions or races produce explicit unavailable evidence, never clean=false
claims from missing data. Real temporary repositories cover normal/detached/linked/packed refs.
Rationale: reading loose refs alone cannot report staged changes or untracked identity correctly.
Alternatives: handwritten Git-index parsing or hashing .git wholesale is larger and incorrect.
No network is used. Ordinary local subprocess Git is allowed by the offline gate; cloud/benchmark
commands remain denied in the relevant tests. Secret-bearing files are rejected/excluded by
existing source/export rules; hashes do not authorize exporting their content.

### D08 - Schemas and validation have an explicit boundary

Decision: current serialized public DTOs get Draft 2020-12 schemas generated from runtime models
and checked in under contracts/. Include structural/enumeration/mode conditionals; document
cross-record identity, hash and chronology predicates in shared-contracts.md and test them in
runtime validators. Schemas cannot inspect filesystem integrity or another record's contents.
Provide a documented schema export/check command, not hand-maintained competing model definitions.
Rationale: downstream readers need stable shapes without pretending JSON Schema performs I/O.
Alternatives: leave old schemas as current or add a schema registry/service violates traceability.

## Deferred by feature boundary, not unresolved F01R decisions

F03/F08 numerical short overlap; F08/F11 60–300-second skew eligibility; F11 cap-start and
reservation arithmetic; F02R/F09 failed-copy handling; F05–F07 concrete expiry mechanisms and
permissions/costs. Contracts carry policy IDs, timing and unavailable reasons so owners can
implement these without silently changing F01R identities. Existing fixed clock/day/coverage
constraints remain. No new calibration, probe-overhead experiment or software-freeze gate.

All F01R engineering unknowns are resolved above. No current-source research conflict was found.
Research agents were not spawned because this bounded assignment explicitly excludes delegation;
research consisted of the supplied authoritative sources and inspected local code.
