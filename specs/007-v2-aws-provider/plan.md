# Implementation Plan: F05 V2 AWS provider

**Branch**: `codex/f05-v2-aws` | **Date**: 2026-10-22 | **Spec**: [spec.md](spec.md)
**Input**: `/private/tmp/cnb-f05/specs/007-v2-aws-provider/spec.md`

## Summary

Implement one AWS-specific driver using merged F01R contracts, two explicit Terraform roots (safety and pair), authenticated console-derived host trust, and complete deletion inventory. Start independent SSM Automation before the pair root: absolute-time sleep then bounded tag discovery/compute stop. F05 proves ordering and failure semantics offline; F09 later composes the driver, F11 supplies admission. No sibling implementation or shared contract changes are required.

## Technical Context

**Language/Version**: Existing Python >=3.9 controller; inline AWS Automation Python 3.11; Terraform 1.16.6.
**Primary Dependencies**: Existing Pydantic/PyYAML, CommandRunner, AWS CLI boundary; HashiCorp AWS provider 6.x pinned by committed lock during implementation. No controller cloud SDK dependency required.
**Storage**: Private local per-attempt Terraform states, runtime inputs, host trust, command/recovery evidence; redacted exports only.
**Testing**: pytest, ScriptedCommandRunner or precise fake, fake clocks/EC2 client for inline expiry script; Terraform fmt/init-backend-false/validate and HCL assertions. No real plan/apply or credentials.
**Target Platform**: Local controller with OpenSSH/AWS CLI/Terraform for later authorized execution; Ubuntu 24.04 amd64 pair.
**Project Type**: Small Python library boundary with provider-specific Terraform.
**Performance Goals**: Exactly two VMs, bounded deadlines; no performance threshold or provider SLA.
**Constraints**: Fixed source settings, independent pre-create expiry, credential-free acceptance, no platform additions.
**Scale/Scope**: One AWS attempt, four scenarios; two independent overlapping P0/P1 attempts. S4 has two regional protections before either VM is created.

## Constitution Check

| Principle | Before research | After design |
| --- | --- | --- |
| I Traceability | Pass: frozen identities/source snapshots required | Pass: normalized outputs, private recovery and redacted durable evidence |
| II Small architecture | Pass: provider Terraform/local controller | Pass: only narrow mandatory AWS expiry document/role/executions; no guest agent/service platform |
| III Honest evidence | Pass: no invented availability/metadata | Pass: missing values explained; failures and unknown inventory preserved |
| IV Provider explicit | Pass: AWS shape/topology/billing retained | Pass: cluster vs peering, logical/physical zones and baseline/burst distinct |
| V Offline cleanup safety | Pass: no credentials/cloud actions | Pass: reserve -> safety register/readback -> pair; full cleanup and separate paid proof |

No constitution exception or research conflict. Engineering assumptions and official sources are in [research.md](research.md).

## Project Structure

### Documentation (this feature)

`specs/007-v2-aws-provider/{spec,plan,research,data-model,quickstart,tasks}.md`, `contracts/aws-provider.md`, `checklists/{requirements,aws-safety}.md`. Planner owns only this directory.

### Source Code (repository root)

```text
src/cloud_network_benchmark/providers/aws.py
terraform/aws/pair/{versions,main,variables,outputs}.tf
terraform/aws/safety/{versions,main,variables,outputs}.tf
terraform/aws/safety/expiry.py
terraform/aws/{pair,safety}/.terraform.lock.hcl
tests/unit/test_aws_provider.py
tests/unit/test_aws_expiry.py
tests/contract/test_aws_provider_contract.py
tests/integration/test_aws_provider_sequences.py
tests/fixtures/aws/{scenarios,automation,console,failures,cost-inputs}/
docs/f05-aws-provider.md
```

**Structure Decision**: `providers/aws.py` owns driver/local settings/private models and reuses existing contract modules read-only. Add only an empty package marker if packaging requires it. Safety Terraform contains IAM/document only; pair Terraform contains EC2/VPC/placement/peering/keypair only. Source `expiry.py` is inserted inline into document content, never uploaded to object storage. Each root has its own committed `.terraform.lock.hcl`; downloaded providers, `.terraform`, state, inputs and plans stay private/untracked. No shared CLI/config/controller edits.

## Interfaces and ownership

Implementer owns the above AWS paths plus this feature's task/evidence updates; other agents' edits must be preserved. Public entry points and exact frozen types: [contracts/aws-provider.md](contracts/aws-provider.md). Local settings carry management /32, selected regional images, private authentication paths and AWS credential reference; do not add fields to ProviderDeploymentInput or repurpose its strictly validated provider_options. ExpiryRequest/Evidence and StructuredEvidence carry existing aggregate/region information. No shared CleanupEvidence type exists: cleanup returns provider-owned AwsCleanupResult using existing CleanupState, FailureEvidence and StructuredEvidence, for later F09 mapping to manifest fields.

## Design and phases

1. Read merged base and inspect frozen contracts; record exact implementation source/lock selection. No unmerged timing fields.
2. Build private state isolation, strict input/admission/deadline checks and replaceable command boundary.
3. US1: safety-only Terraform, inline 300-second expiry discovery, registration/readback and partial-registration rollback; tests prove ordering before any pair command. Use absolute cap from supplied reservation and 360-second safety lead; no cap extension.
4. US2: exact S1–S4 pair Terraform, normalized outputs, authenticated console-key binding and provider metadata. Scenario admission is evidence supplied by the caller; preparation settings are handoff data, not F02R/F04 imports.
5. US3: pair destroy plus paginated owned-resource inventory in all regions, then terminal expiry cancellation and safety deletion. Preserve failures/unknowns and block subsequent launch.
6. Run focused tests, static checks and affected F01R contracts; update docs and validation evidence; then hand to convergence. Static/fake results cannot establish cloud permission/stop/deletion.

## Acceptance checks

Exact planned commands are in [quickstart.md](quickstart.md). Test obligation mappings are in tasks.md. Provider downloads may contact Terraform registry only, with normal tool permission; all pytest cases must prevent live cloud execution. Terraform `init -backend=false` and `validate` are static; neither `plan`, `apply`, nor `destroy` is run against AWS. Runtime apply/destroy requests are asserted solely via fakes.

## Complexity Tracking

No unjustified violations. Two roots enforce pre-create safety and teardown ordering more directly than targeted applies or a generic workflow layer. One bounded inline script removes the need for an additional scheduler, guest agent or cleanup service.
