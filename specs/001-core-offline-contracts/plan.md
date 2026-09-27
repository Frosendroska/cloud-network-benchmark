# Implementation Plan: F01 Core Contracts and Offline Harness

**Branch**: `001-core-offline-contracts` | **Date**: 2026-09-27 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-core-offline-contracts/spec.md`

## Summary

Build the credential-free Python foundation that reads campaign YAML from the canonical experiment or smoke-test configuration directories, validates the entire parent campaign before any write or external action, resolves exactly one provider-specific child observation per selected provider, and initializes linked campaign/child manifests plus immutable child result paths. Publish explicit contracts for deployment, remote access, benchmark and diagnostic artifacts, validation inputs, lifecycle state, metadata evidence, and external command execution. Provide an offline CLI with validation, resolution, initialization, and dry-run commands; clocks, identifiers, Git provenance, and external actions are injected and covered by deterministic fakes. Each invocation handles one explicitly started campaign; recurrence, sharding, and automatic triggering remain external.

## Technical Context

**Language/Version**: Python 3.9+ (the repository host currently provides Python 3.9.2)

**Primary Dependencies**: Pydantic 2.x for typed validation and schema-compatible models; PyYAML 6.x for safe YAML loading; Python standard-library `argparse`, `pathlib`, `hashlib`, `json`, `subprocess`, `uuid`, and time utilities

**Storage**: Local YAML input and snapshots plus JSON manifests under `results/manifests/` and immutable child bundles under `results/raw/<run_id>/`; no database, object storage, or remote state

**Testing**: pytest 8.x with unit, contract, and credential-free integration tests; fakes for command execution, clock, ID generation, Git provenance, filesystem collisions, and persistence failures

**Target Platform**: Local macOS or Linux control host; generated contracts must remain usable by later Linux/cloud-facing features

**Project Type**: Installable Python library and CLI

**Performance Goals**: Validate and resolve a campaign with up to three selected providers in under one second on a development laptop; initialize all local records in under two seconds excluding filesystem faults; dry-run performs zero external commands

**Constraints**: No credentials, network access, cloud calls, Terraform apply, SSH, SCP, or FLENT execution; whole-campaign validation precedes ID reservation and writes; every assigned run ID has a durable manifest; provider semantics stay explicit; paths are collision-safe and never overwritten; missing provenance and metadata remain explicit; advisor-dependent values remain configurable

**Scale/Scope**: One scenario and one explicitly started campaign per command invocation; one parent campaign and 1-3 provider child runs; AWS, Azure, and GCP only; three sequential benchmark artifact slots; lightweight local manifests and fixtures; no recurring scheduler, sharding, or internal reconciler

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

### Pre-Design Gate

| Principle | Status | Evidence |
| --- | --- | --- |
| I. Thesis Traceability | PASS | Campaign and child records carry `experiment_id`, documented Thesis-side mapping, source path, byte-level config hash and snapshot reference, implementation/design Git provenance, IDs, manifests, and raw-result references. Git acquisition is injectable, and unavailable evidence is explicit rather than fabricated. Scenario vocabulary is preserved verbatim. |
| II. Deliberately Small Architecture | PASS | The design adds one local Python package and file-backed contracts only. It excludes object stores, remote state, agents, databases, queues, Packer, containers, and telemetry systems. |
| III. Immutable Results and Honest Provenance | PASS | IDs are assigned only after validation and by exclusive manifest reservation; every assigned child ID therefore has a manifest even when later persistence fails. Child bundles are never reused; VM metadata, provider metadata, tool versions, and unavailable provenance use honest evidence values; failures and cleanup status are separate fields. |
| IV. Provider-Explicit Implementation | PASS | AWS, Azure, and GCP configuration and deployment fields remain provider-specific. Shared models cover lifecycle vocabulary without flattening provider placement semantics. |
| V. Offline-First, Cleanup-Safe Validation | PASS | F01 never invokes a real external command. The command runner is injected, and scripted fakes cover success, failure, timeout, and interruption. Cleanup status is part of every child contract for later lifecycle features. |
| Repository boundary and workflow | PASS | Primary executable campaigns remain in `configs/experiments/`; reduced executable smoke-test campaigns remain in `configs/tests/`; both document Thesis-side mappings. Implementation evidence remains here and thesis analysis remains in `../Thesis`. No command is documented as available until F01 implements it. |

No constitutional exception or complexity waiver is required.

### Post-Design Gate

PASS. Phase 1 keeps the same boundaries: schemas are local files, result locations follow the constitution, provider-specific unions remain explicit, and every external operation crosses the fakeable execution contract. The campaign parent coordinates identity and schedule only; child runs retain independent provider, evidence, failure, and cleanup state. No forbidden infrastructure or paid behavior is introduced.

## Project Structure

### Documentation (this feature)

```text
specs/001-core-offline-contracts/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── README.md
│   ├── campaign-config.schema.json
│   ├── campaign-manifest.schema.json
│   ├── child-run-manifest.schema.json
│   ├── shared-contracts.md
│   └── cli.md
└── tasks.md                    # created later by $speckit-tasks
```

### Source Code (repository root)

```text
pyproject.toml
src/
└── cloud_network_benchmark/
    ├── __init__.py
    ├── __main__.py
    ├── cli.py
    ├── config.py
    ├── dry_run.py
    ├── errors.py
    ├── ids.py
    ├── lifecycle.py
    ├── manifests.py
    ├── paths.py
    └── contracts/
        ├── __init__.py
        ├── artifacts.py
        ├── campaign.py
        ├── common.py
        ├── deployment.py
        ├── execution.py
        └── validation.py
configs/
├── experiments/
└── tests/
results/
├── manifests/
└── raw/
tests/
├── unit/
├── contract/
├── integration/
└── fixtures/
    ├── configs/
    ├── manifests/
    └── commands/
```

**Structure Decision**: Use a single `src`-layout Python package. Public cross-feature types live under `cloud_network_benchmark.contracts`; configuration resolution, ID/path allocation, manifests, lifecycle rules, and dry-run rendering remain small top-level modules. Tests are split by purpose so later features can import the public contracts while F01 retains focused ownership of foundation behavior. Existing provider Terraform directories remain untouched.

## Design Flow

1. Locate an explicitly named YAML file under `configs/experiments/` or `configs/tests/`, or accept an explicit path within one of those roots. The former holds primary campaigns; the latter holds executable, reduced-cost smoke-test campaigns. Both retain documented Thesis-side mappings.
2. Read bytes with safe YAML parsing, reject duplicate keys, and validate structural plus semantic rules without writing files or invoking commands.
3. Resolve shared campaign settings into one provider-specific child observation per selected provider. The selected-provider list and provider configuration keys must match exactly.
4. For validation and resolution, return models or structured errors only. For dry-run, render the resolved campaign, candidate identity/path pattern, artifacts, and planned external action classes without reserving IDs or paths.
5. For initialization, generate candidate campaign and child IDs through injected clock/ID sources and preflight every destination. Exclusively creating a child manifest is the atomic reservation that assigns its run ID; any later failure preserves or recovers all assigned child manifests with partial-initialization evidence.
6. Persist the original YAML snapshot and SHA-256 hash for each child bundle. Obtain the implementation commit through an injected Git provenance provider for both experiment and test campaigns; deterministic fakes avoid Git subprocesses in offline tests, and genuinely unavailable provenance is recorded explicitly.
7. Initialize child manifests with explicit unavailable VM metadata, cloud/provider metadata, and tool-version evidence, then permit later lifecycle features to replace those categories with observed structured values through atomic manifest updates.
8. Expose canonical state-transition helpers, manifest serialization, artifact locations, deployment/access/validation contracts, and real/fake command-runner interfaces for F02-F08. F01 performs no recurrence, sharding, or automatic campaign triggering.

## Test Strategy

- Unit tests cover YAML parsing, duplicate-key rejection, provider/scenario semantics, provisional benchmark values, campaign expansion, IDs, paths, canonical lifecycle transitions, aggregate parent status, artifact naming, manifest round trips, injected Git provenance, command outcomes, and dry-run redaction.
- Contract tests load every JSON schema, validate representative parent/child/config fixtures, and verify public Pydantic serialization uses the documented field names and enum values.
- Integration tests exercise `validate`, `resolve`, `init`, and `dry-run` with three-provider experiment and one-provider test fixtures in temporary result roots.
- Failure-injection tests cover every initialization persistence boundary and prove that every assigned run ID retains exactly one manifest, partial initialization is recorded rather than deleted, collisions never overwrite evidence, unavailable Git/metadata evidence stays explicit, fake command failures retain stdout/stderr/status, and cleanup failure never replaces the original failure.
- A network-denial guard fails tests if F01 attempts sockets or a non-fake subprocess during credential-free scenarios.
- A lightweight acceptance review checks that a human can understand the dry-run output for every provider/scenario fixture within five minutes; this is recorded validation evidence, not runtime behavior.

## Complexity Tracking

No constitution violations require justification.
