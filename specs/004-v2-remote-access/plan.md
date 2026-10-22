# Implementation Plan: F02R V2 remote preparation and native retrieval

**Branch**: `codex/f02r-v2-remote-access` | **Date**: 2026-10-21 | **Spec**: [spec.md](spec.md)

**Input**: `/private/tmp/cnb-f02r/specs/004-v2-remote-access/spec.md`

## Summary

Migrate the delivered F02 modules in place to source-verified Sockperf/iperf3 preparation, authenticated strict SSH/SCP, persistent private B listeners, one absolute readiness budget and closed native-file retrieval. Consume merged F01R contracts unchanged; expose small local Python facades for later F09 composition. [research.md](research.md) records source precedence and engineering decisions; [contracts/remote-access.md](contracts/remote-access.md) freezes the feature interfaces.

## Technical Context

**Language/Version**: Existing Python package (`pyproject.toml` requires >=3.9); use `/private/tmp/cnb-offline-venv/bin/python` for the provisioned test environment. Do not change interpreter/dependency policy in this feature.

**Primary Dependencies**: Existing Pydantic 2 and standard library; OpenSSH/SCP, Ubuntu cloud-init/systemd, tool-source build commands and native utilities remain replaceable external boundaries. No SSH library, cloud SDK, daemon or new dependency is needed.

**Storage**: Existing immutable child result paths plus contained local attempt staging; VM-local files during measurement. Small synthetic fixtures only in Git.

**Testing**: Existing pytest unit/contract/integration suites, exact command recording, fake UTC/monotonic clocks and temporary physical transfer bytes. No actual remote commands or traffic.

**Target Platform**: Local controller and exactly two Ubuntu 24.04 amd64 VMs per selected provider. Provider-specific identity, network and expiry are upstream contracts, not implemented here.

**Project Type**: Existing local CLI/library instrument; F02R adds no CLI experiment runner.

**Performance Goals**: All preparation/readiness actions fit one original readiness/overall budget; retrieval fits remaining cap minus caller cleanup allowance. Stream hashing rather than reading large diagnostic exports at once.

**Constraints**: Zero credentials/cloud contact/real SSH/SCP/benchmark traffic in offline checks; no shared contract changes, unfinished sibling imports, phase/calibration/day orchestration or new runtime services. All modes share the remote boundary.

**Scale/Scope**: One provider attempt, two VM roles per invocation; later F09 invokes these independently across selected providers. Prepared listeners live across phases/trials/chunks, until final producer closure.

## Constitution Check

| Gate | Pre-design | Post-design evidence |
| --- | --- | --- |
| I Traceability | Pass | Keep deployment/role/unit and F01R artifact IDs, actual source/compiler/binary/commands and missing evidence; data model/contract |
| II Small architecture | Pass | Existing four modules, OpenSSH/SCP and ordinary systemd-managed tool processes; no agent, service platform or duplicate tree |
| III Immutable native results | Pass | Measurement-end/closure gate; both-VM native files, materialized size/hash, immutable verified destinations and retained failed attempts |
| IV Provider-explicit behavior | Pass | Consume exact private addresses/authenticated identity/provider provenance; do not conceal provider security/expiry differences |
| V Offline and cleanup safe | Pass | One original cap/readiness deadline, bounded retries, existing cleanup allowance and returned cleanup-required evidence; fake commands/clocks/files |
| Workflow ownership | Pass | F01R base only; specs 001/002 untouched, no shared runtime state or Thesis edits; planner artifacts then Luna implementation then convergence |

No violations or complexity exception. These checks establish artifact consistency, not implemented behavior or cloud readiness.

## Project Structure

### Documentation (this feature)

```text
specs/004-v2-remote-access/
  spec.md
  plan.md
  research.md
  data-model.md
  contracts/remote-access.md
  quickstart.md
  tasks.md
  checklists/requirements.md
  checklists/remote-safety.md
  review.md
```

### Source Code (repository root)

```text
src/cloud_network_benchmark/
  access.py       # existing facades, identity/deadline/transport and redacted action evidence
  bootstrap.py    # existing preparation implementation; source build and B service setup
  readiness.py    # existing observation-only checks
  retrieval.py    # existing transfer implementation; closed inventory and integrity
  __init__.py     # only remote exports if needed; preserve unrelated exports

tests/
  unit/test_bootstrap.py
  unit/test_readiness.py
  unit/test_retrieval.py
  contract/test_remote_access_contract.py
  integration/test_remote_sequences.py
  integration/test_v2_remote_access.py
  fixtures/f02r/  # small synthetic tool help/build/inventory/transfer evidence and local fakes

docs/f02r-remote-access.md
```

**Structure Decision**: Preserve the existing module layout and test organization. Feature-local strict data models/functions are sufficient; reuse shared `StrictModel`, `IdentityContext`, `CommandRequest/Result/Runner`, `ProviderDeploymentOutput`, `ArtifactInput/Record/Layout`, path containment helpers and evidence wrappers. Do not edit `contracts/` or its JSON schemas. Tests can import `tests/fixtures/f02r` helper data through their local path; avoid changes to shared conftest.

## Phases and ownership

1. Phase 0 research: reconcile sources/legacy code; settle local transport, source verification, server management, deadlines and failed-copy policy. Complete in [research.md](research.md).
2. Phase 1 design: define [data-model.md](data-model.md), [contracts/remote-access.md](contracts/remote-access.md) and [quickstart.md](quickstart.md); rerun constitution/requirements checks. No application code in planning.
3. Phase 2 tasks: setup, shared local remote boundary, US1 preparation, US2 readiness, US3 retrieval, focused integration/documentation and full regression. Tests are explicitly required by FR-013.
4. Next worker: GPT-6 Luna Medium owns only the application/test/doc paths listed above plus task progress and implementation evidence in this feature. It is not alone in the repository and must preserve others' edits. Historical specs 001/002, shared F01R contracts/schemas/configs, sibling code, Thesis and coordinator state are read-only. Raise a justified shared-contract issue to the coordinator before consuming any amendment.
5. Next gate: Sol Medium runs actual `speckit-converge`; only zero actionable findings plus the exact tested head and passing relevant checks allow the coordinator to integrate/publish.

## Public interfaces and integration boundary

Keep `prepare`, `check_readiness`, `retrieve`, `ConnectionOutput` and result names at their existing module locations. V2 inputs become strict and require original deadline/closure/preparation evidence; legacy FLENT config fields are rejected rather than silently accepted. Signature/field details are in the contract. Export new remote-only types from their defining modules; root `__init__.py` edits are limited to existing remote imports/exports.

F09 supplies original cap context, completed producer closure and any primary failure; provider drivers supply complete V2 deployment/authenticated identity. F02R returns evidence and cleanup-required state. F02R never imports F03/F04/F05/F06/F07/F08/F11 implementations, does not perform cleanup, and does not fabricate their results. Native inventory consumes the frozen artifact vocabulary, including dynamic resolved probe chunks; it does not invent a sibling file format.

## Acceptance checks

Exact commands, expected cases and evidence rules are in [quickstart.md](quickstart.md). Run the focused remote suite, full existing pytest suite and `git diff --check` after implementation. Use `PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python`. Inspect actual recorded argv, physical local bytes and cumulative deadline observations; a permissive fake returning success is insufficient. No Terraform check is needed for F02R because no Terraform is owned/changed. Real build/permissions/SSH/UDP/streaming correctness and provider deletion stay unrun until separately authorized paid acceptance.
