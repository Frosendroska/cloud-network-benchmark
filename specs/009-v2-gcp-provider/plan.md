# Implementation Plan: F07 V2 GCP provider

**Branch**: `codex/f07-v2-gcp` | **Date**: 2026-10-22 | **Spec**: [spec.md](spec.md)
**Input**: `/private/tmp/cnb-f07/specs/009-v2-gcp-provider/spec.md`

## Summary

Build one GCP-specific driver and two local Terraform roots: protection creates the run-scoped network, firewall, optional compact policy and two immutable instance templates containing absolute automatic STOP; pair creates only two standalone VMs from those verified templates. Separate roots enforce pre-create registration without targeted applies, a scheduler, a MIG or an extra service. Existing V2 contracts carry all public evidence. F09 later composes admission, preparation, measurement and retrieval.

**Internal prerequisite**: coordinator-owned `F01R_GCP_EXPIRY`, allocated `specs/017-v2-gcp-expiry-contract/`, must migrate the existing GCP DELETE validators/configs/schemas/fixtures to governing STOP semantics and merge before F07 implementation. Current base does not accept STOP. This is implementation debt against existing governance, not a research decision. No F07 shared edits or silent DELETE-to-STOP override are allowed.

## Technical Context

**Language/Version**: Existing Python >=3.9; Terraform 1.16.6 available locally.
**Primary Dependencies**: Existing Pydantic, CommandRunner, Terraform Google provider and replaceable gcloud CLI commands. Select a stable Google provider supporting template termination_time/STOP and source_instance_template/self_link_unique, commit both lock files and verify actual schema during implementation; do not infer support from latest documentation alone. No controller SDK or dependency change.
**Storage**: Private local per-attempt protection/pair state, vars, redacted journal and host trust; no remote state.
**Testing**: pytest fixtures/fake commands/fake clocks; Terraform fmt, backend-disabled init, validate and provider-schema/static structure checks. No cloud plan/apply/destroy.
**Target Platform**: Local controller and stock Ubuntu 24.04 amd64 guests.
**Project Type**: Provider module within existing CLI framework; no new CLI command.
**Performance Goals**: Exactly two VMs per attempt; no performance thresholds or timing/billing guarantees. Whole workflow must fit the original cap and earlier stop trigger.
**Constraints**: Credential-free development; selected V2 topology; no platform/services/queues/agents, MIGs, object storage or shared-contract changes.
**Scale/Scope**: One selected provider/attempt; S1–S4; separate concurrent P0/P1 namespaces; two regions only for S4.

## Constitution Check

| Principle | Before research | After design |
| --- | --- | --- |
| I Traceability | Pass: existing identities/config/Git/evidence contracts | Pass: exact role/project/template/VM IDs and source/cost identities retained |
| II Small architecture | Pass: explicit Terraform/local commands | Pass: native two-template automatic STOP, no scheduler/MIG/service |
| III Honest immutable evidence | Pass: redacted exports/private state | Pass: no invented image, host identity, source-template response field, physical zone or stop success |
| IV Provider explicit | Pass: exact GCP shapes/S1–S4 | Pass in design: COLLOCATED/no max_distance; gVNIC/no Tier_1; GiB separate from throughput |
| V Offline and cleanup | Pass in design: original cap, pre-create expiry, full cleanup | Conditional runtime gate: STOP amendment must merge; static/fake checks precede convergence, paid proof remains unrun |

The design has no requested governance exception. The current executable DELETE mismatch is a concrete integration blocker tracked in the prerequisite, not a waived constitution rule.

## Project Structure

### Documentation (this feature)

Planner owns only `specs/009-v2-gcp-provider/`: spec, plan, research, data-model, quickstart, tasks, planning-handoff, `contracts/gcp-provider.md` and `checklists/{requirements,gcp-safety}.md`. Local `.specify/feature.json` is isolated ignored tooling state, not a shared artifact.

### Source Code (repository root)

```text
src/cloud_network_benchmark/providers/gcp.py
terraform/gcp/protection/{versions,main,variables,outputs}.tf
terraform/gcp/pair/{versions,main,variables,outputs}.tf
terraform/gcp/{protection,pair}/.terraform.lock.hcl
tests/unit/test_gcp_provider.py
tests/unit/test_gcp_expiry.py
tests/contract/test_gcp_provider_contract.py
tests/integration/test_gcp_provider_sequences.py
tests/fixtures/gcp/{scenarios,templates,instances,hostkeys,failures,cost-inputs}/
docs/f07-gcp-provider.md
```

**Structure Decision**: Implementer owns these GCP paths and feature task/evidence updates only. Existing `providers/__init__.py`, AWS code, contracts, schemas, configs, CLI and other features stay read-only. They are not alone in the repository; preserve others' changes. Shared changes route through the coordinator. F05 is merged reference only; no dependency on unmerged F06 or F11.

## Interfaces and ownership

Freeze the ordinary provider-local boundary in [contracts/gcp-provider.md](contracts/gcp-provider.md): `GcpProviderDriver.register_expiry`, `provision`, `cleanup`; private `GcpRuntimeSettings`; local `GcpCleanupResult`. Reuse existing deployment/admission/common/execution models. There is no shared CleanupEvidence class. Local settings carry project, exact regional image identities, controller /32, authentication references, private state root, review decision and complete-workflow remaining-time evidence; do not extend strictly validated provider_options.

## Design and phases

1. Integrate verified merged F01R_GCP_EXPIRY at a clean checkpoint. Assert GCP STOP is actually accepted and DELETE rejected before implementation. Reconcile latest merged main and record SHA.
2. Add provider-local validation, isolation, bounded external commands and recovery journal. Reserve activation precedes the protection root; no code starts a fresh cap.
3. US1: protection root creates zero VMs/disks/addresses. Two role-specific global templates bind network/image/shape/boot settings/metadata/optional S3 policy and absolute STOP. Observe successful provider operations and authenticated template get readbacks. Register only after both match. Capture numeric IDs, unique self links and canonical properties hashes. Template defaults are not enough.
4. US2: immediately before pair apply, verify both template IDs/properties again, remaining time and original reservation. The pair root accepts only verified unique template references, role names/project/zones and bounded timeouts; it contains no scheduling, disk, NIC, metadata or placement overrides. It creates two `google_compute_instance_from_template` resources only. Provider GET verifies actual scheduling plus selected settings; actual create requests/config prove binding, without inventing a sourceTemplate response property.
5. Normalize raw Terraform plus authenticated GCP observations; fetch guest `hostkeys/` through the specific VM's authenticated API identity, reject reuse/mismatch and write strict known_hosts. Provide bootstrap handoff; F02R/F04 own guest preparation/diagnostic verification.
6. US3: pair destroy using original state, paginated known-ID/ownership inventory, then protection destroy and final full inventory. Retain inherited VM stop even if a template disappears; cancel registration only after no VM remains. Unknown cleanup blocks new attempts. Preserve partial/primary/secondary evidence.
7. Run focused/static/affected-contract checks, document exact revisions/results, then hand to converge. No actual GCP, credentials or real SSH/traffic.

## Acceptance checks

[quickstart.md](quickstart.md) lists exact planned commands and [tasks.md](tasks.md) maps requirement coverage. Credential-free fake traces must show reservation activation <= registration <= pair creation; every registration failure forbids pair apply. Tests must exercise the actual Terraform structure and actual gcloud request/response keys. `terraform validate` cannot establish cloud acceptance or runtime inheritance; fixtures/static binding checks cannot establish IAM or real stop latency.

## Complexity Tracking

No design exception. Two roots encode ordering and independent teardown. Precreated native templates reuse Compute Engine instead of adding a scheduling platform. Separate template per role prevents network/private-IP/metadata override during pair creation and avoids S3 resource-policy coexistence problems.
