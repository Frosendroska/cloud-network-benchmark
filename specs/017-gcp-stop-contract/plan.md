# Implementation Plan: GCP STOP Contract Migration

**Branch**: `codex/f01r-gcp-stop` | **Date**: 2026-10-23 | **Spec**: [spec.md](spec.md)

**Input**: `specs/017-gcp-stop-contract/spec.md`

## Summary

Replace contradictory GCP DELETE with explicit STOP in the two existing validators and eight active YAML examples. Add narrow GCP schema constraints to the owning models and regenerate canonical schemas after the serialized acquisition-contract merge. Prove runtime/schema parity and config/CLI propagation offline. No new model, mode, dependency, compatibility layer or provider implementation.

## Technical Context

**Language/Version**: Existing Python >=3.9 package; `/private/tmp/cnb-offline-venv/bin/python`.
**Primary Dependencies**: Existing Pydantic 2, PyYAML, pytest and jsonschema. No additions.
**Storage**: Existing YAML examples and canonical JSON schemas; no new runtime storage.
**Testing**: Focused option/schema/config/CLI regressions, full existing offline pytest suite and deterministic schema export/check.
**Target Platform**: Local credential-free development; no guest/cloud execution.
**Project Type**: Existing small CLI/library.
**Performance Goals**: No new workload; retain existing local validation behavior.
**Constraints**: STANDARD On-Demand, explicit STOP, schema version 2, no coercion, historical specs 001/002 preserved, no cloud/SSH/benchmark traffic.
**Scale/Scope**: Two independent runtime boundaries, corresponding schema constraints and eight current GCP examples. Rescan for relevant newly merged examples.

## Constitution Check

| Gate | Before research | After design |
| --- | --- | --- |
| I/III: identities, actual config hashes and historical evidence | Pass | Pass: hashes change from actual YAML bytes; saved results untouched |
| II: deliberately small architecture | Pass | Pass: existing models/functions, no service/dependency |
| IV: GCP STOP, selected On-Demand settings | Pass | Pass: corrects executable debt under existing governance |
| V: offline validation and complete cleanup distinction | Pass | Pass: tests/CLI only; STOP never closes full cleanup |
| Workflow: bounded ownership, serialized shared exports | Pass | Pass: planning only; implementation/export waits for merged acquisition |

No constitution amendment, complexity exception or advisor-approval claim is needed.

## Project Structure

### Documentation (this feature)

```text
specs/017-gcp-stop-contract/
  spec.md, plan.md, research.md, data-model.md, quickstart.md, tasks.md
  contracts/gcp-stop.md
  checklists/requirements.md
  checklists/contracts.md
```

### Source Code (implementation ownership after coordinator release)

```text
src/cloud_network_benchmark/contracts/{campaign,deployment}.py
configs/experiments/exp-{001-multi-provider,001-calibration,001-diagnostic-day,
                         002-cross-zone,003-placement-optimization,004-inter-region}.yaml
configs/tests/exp-{902-gcp-single-provider,903-three-provider-smoke}.yaml
specs/003-v2-shared-contracts/contracts/*.schema.json
tests/contract/{test_campaign_config_contract,test_deployment_contract,
                test_v2_schema_export,test_gcp_stop_contract}.py
tests/integration/test_dry_run_cli.py
```

**Structure Decision**: Amend the existing validators and `__get_pydantic_json_schema__` hooks (add hooks where absent). For `provider=gcp`, require exactly the two existing option keys with constants STANDARD and STOP. Embedded ProviderConfiguration schemas propagate into config/resolved consumers; direct ProviderDeploymentInput needs its own constraint. Export via `contracts.schema`; never hand-edit generated files or copy pre-acquisition exports. Use one small new `test_gcp_stop_contract.py` corpus and existing test files. Existing `config.py`, `dry_run.py`, `cli.py` and `contracts/schema.py` stay read-only unless a demonstrated propagation defect requires coordinator reassignment.

## Interfaces, ownership and dependencies

- The existing `provider_options` mapping remains the interface; [gcp-stop.md](contracts/gcp-stop.md) and [data-model.md](data-model.md) define requiredness and cases.
- Planner owns only this feature directory. After release, implementer owns the exact paths above and this feature's `validation.md`. Minimal affected fixture assertions may be corrected after the required scan; historical specs 001/002 and other worktrees remain outside ownership.
- The acquisition follow-up in `/private/tmp/cnb-acquisition-evidence` must be verified merged by the coordinator before shared edits/export. Coordinator supplies its actual merged SHA/artifact path; no proposed acquisition API is consumed here.
- F07 `specs/009-v2-gcp-provider/` owns immutable-template registration, absolute expiry, inheritance checks, operations and full cleanup. Its planning is context, not an executable dependency of this feature. F07 implementation waits for this migration's verified merge.

## Implementation sequence and acceptance

1. Record coordinator release, merged acquisition SHA and integrated base; recheck artifact applicability before editing shared code.
2. Add independent runtime/schema cases, then amend both validators, GCP schema constraints and active examples. Preserve AWS/Azure options and unrelated settings.
3. Regenerate current canonical schemas; prove options at every resolved/deployment occurrence, all active examples and human/JSON CLI previews.
4. Run [quickstart.md](quickstart.md), record exact source/head/base and command results in `validation.md`, complete tasks and hand off to Spec Kit convergence. Zero actionable findings and passing current-head checks precede coordinator integration/PR/merge; no extra independent review stage.

Current probes establish debt, not STOP support. Cloud IAM, actual expiry, billing and cleanup remain unproved.
