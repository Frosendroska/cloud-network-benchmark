# Implementation Plan: F06 V2 Azure provider and expiry

**Branch**: `codex/f06-v2-azure` | **Date**: 2026-10-22 | **Spec**: [spec.md](spec.md)

**Input**: `specs/008-v2-azure-provider/spec.md`; assigned base `7a70a3139c5ee9efa1f8ea13c3f6de40e9048121`.

## Summary

Add one Azure-specific Python module and two small explicit Terraform roots. Expiry prerequisites are prepared first; authenticated Automation REST draft/publication/schedule/binding readbacks establish the independent deallocation fallback for future deterministic A/B IDs. Only then may the separate pair root create two selected VMs. Return existing V2 deployment/expiry DTOs plus feature-local recovery/cleanup results. Preserve current F01R shared contracts and fail closed on absent admission, expiry or inventory evidence.

## Technical Context

**Language/Version**: Existing Python >=3.9, Pydantic 2; Terraform >=1.7 for provider mocks; PowerShell 5.1 runbook with Az.Accounts. No new controller SDK dependency.

**Primary Dependencies**: Merged `contracts.deployment`, `contracts.admission`, `contracts.common`, `contracts.execution`; `CommandRunner` for Terraform and `az rest`/Run Command. AzureRM 4.x provider with an exact selected lockfile per root; validate actual schema before relying on fields. The two roots use the same selected version. Do not add AzAPI, random-ID or local-file providers when ordinary Python/`file()` suffice.

**Storage**: Private local attempt workspace, separate pair/expiry Terraform state, write-before-action recovery JSON and authenticated known_hosts. Redacted durable evidence under the existing child result root. No remote state or object storage.

**Testing**: pytest contract/unit/integration with injected runner/filesystem/UTC+monotonic clocks; Terraform fmt/init/validate and mocked-provider plan tests for actual HCL. Static checks of the bounded deallocation-only runbook and its uploaded bytes/parameters; fakes do not execute Azure PowerShell or prove provider behavior.

**Target Platform**: Local controller; Azure public cloud Ubuntu 24.04 amd64 pair. No cloud access in the offline gate.

**Project Type**: Small provider driver inside the existing CLI instrument; F09 wires it into lifecycle later.

**Performance Goals**: Exactly two VMs; entire admitted mode, setup/registration, final-copy/cleanup before actual emergency trigger within original cap; no benchmark-performance target or expiry-dispatch SLA.

**Constraints**: Four explicit scenarios, shared DTOs unchanged, credential-free checks; preparation failures and unknown residuals block creation/cleanup respectively. No measurement, admission ledger, scheduler platform, monitoring, agent installation or research changes.

**Scale/Scope**: One pair/attempt; concurrent P0/P1 keep independent attempts even with equal private CIDRs. No cross-attempt peering.

## Constitution Check

| Gate | Pre-research check | Post-design check |
| --- | --- | --- |
| I Traceability | Pass: preserve request/attempt/source identities | Pass: output, expiry and private recovery inventory bound to same IDs |
| II Small architecture | Pass: provider Terraform plus local driver | Pass: narrow provider-only Automation fallback; no workers/platform; no benchmark changes |
| III Honest evidence | Pass: actual vs unavailable explicit | Pass: async/partial failures, secret exclusion, no fabricated real-cloud evidence |
| IV Provider explicit | Pass: Azure D4s_v5/StandardSSD/PPG/peering | Pass: logical zones, maximum network limit, deallocation billing and full deletion distinct |
| V Offline/cleanup | Pass: injected boundaries and static checks | Pass: pre-create expiry; original lifetime; residual blocking; separate paid acceptance |

No constitution amendment or shared contract amendment is needed. If real schema/permission/operation behavior makes this plan impossible, surface exact evidence to the coordinator before changing shared scope.

## Project Structure

### Documentation (this feature)

```text
specs/008-v2-azure-provider/
  spec.md
  plan.md
  research.md
  data-model.md
  quickstart.md
  contracts/azure-provider.md
  checklists/requirements.md
  checklists/azure-safety.md
  review.md
  tasks.md
```

### Source Code (repository root; implementation ownership)

```text
src/cloud_network_benchmark/azure_provider.py
terraform/azure/pair/{versions.tf,main.tf,variables.tf,outputs.tf,.terraform.lock.hcl}
terraform/azure/pair/tests/topologies.tftest.hcl
terraform/azure/expiry/{versions.tf,main.tf,variables.tf,outputs.tf,.terraform.lock.hcl}
terraform/azure/expiry/deallocate.ps1
terraform/azure/expiry/tests/prerequisites.tftest.hcl
tests/contract/test_azure_provider_contract.py
tests/unit/test_azure_provider.py
tests/integration/test_azure_provider_flow.py
tests/fixtures/f06/
docs/validation/f06-azure-provider.md
```

**Structure Decision**: One module avoids shared package initialization conflicts with unmerged AWS/GCP siblings. `terraform/azure/.gitkeep` may remain. No root CLI/config/manifest/schema/bootstrap/test-conftest/README edits. A provider lockfile is permitted, not Terraform state/plans or downloaded providers.

## Interfaces and implementation sequence

Exact signatures and evidence fields: [contracts/azure-provider.md](contracts/azure-provider.md), [data-model.md](data-model.md). Phase 0 research is complete in [research.md](research.md); no unresolved engineering placeholders. Phase 1 design fixes the account/runbook/schedule/role/publication contracts, exact target identity, lifetime bounds and cleanup order. Phase 2 tasks sequence foundation → pair mapping → expiry → provisioning/failure recovery → final offline checks. Pair mapping can be tested without calling provision; the public provision function is never allowed to bypass expiry.

Implementation owns only the listed paths. Planner owns the feature artifacts; Luna owns implementation and task checkbox updates, Sol convergence may append remediation tasks as its skill permits. Coordinator owns shared amendments, integration/PRs/merges and Thesis progress. No sibling implementation import/dependency is allowed.

## Acceptance checks

- Contract mapping and negative evidence tests for FR-001/005/007/008/009/011 using existing DTOs and feature-local models, never a modified shared schema.
- Static mocked Terraform plans for S1/S2/S3/S4: exactly two VMs, Regular purchase, image/disk/accelerated NIC/private routing, PPG and logical zones, narrow NSGs including default-rule override, static public IP zones, no forbidden infrastructure.
- Runner argv/payload tests: account prerequisite apply before runbook draft/upload/publish/readback, schedule/job/RBAC readback before pair apply, actual script content SHA-256, Azure deallocate rather than guest/ordinary power-off; no shell or fake response materialization shortcut.
- Independent fake expiry with controller gone, A/B partial existence, late VM appearance, async 202, denied/revoked permissions, late dispatch and finite retry horizon. Runtime PowerShell/cloud dispatch remain unproved offline.
- Cumulative original UTC/monotonic deadlines and complete-workflow bound, minimum 300-second schedule lead, earlier trigger fit, registration exhaustion, reservation mismatch and missing dated cost inputs. All failing cases have zero pair-creating actions.
- Apply/output/identity/cleanup interruption and failures retain primary/secondary evidence; inventory observes disk/IP/account/role leftovers and unknowns; expiry survives until VMs verified absent; same-CIDR concurrent attempt cannot overwrite/delete its neighbor. Secret sentinels absent from all durable outputs.
- Focused/full pytest, schema drift and Terraform fmt/validate/mock plans, then exact-head convergence. [quickstart.md](quickstart.md) distinguishes commands presently available from future implementation targets and paid checks.

## Complexity Tracking

No constitutional violations. Automation account, one fixed runbook and one schedule are justified solely by independent pre-create/date-specific Azure deallocation. Terraform expiry prerequisite root and pair root enforce ordering without targeted apply or an orchestration platform. Published runbook REST upload removes the need for remote source storage.
