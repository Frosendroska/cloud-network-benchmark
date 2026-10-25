# Implementation Plan: F10 V2 offline readiness gate

**Branch**: `codex/f10-v2-offline-gate` | **Date**: 2026-10-25 | **Spec**: [spec.md](spec.md)
**Input**: `/private/tmp/cnb-f10/specs/014-v2-offline-gate/spec.md`

## Summary

Add one small local validation entry point around the existing full suite, canonical schema check and six Terraform roots. Extend the successful default three-provider F09 mode-test tail so its actual saved native bundles feed F12 and replay unchanged. Retain exact clean-revision evidence and a separate paid runbook. No application code or public-contract changes are planned.

## Technical Context

**Language/Version**: Existing Python >=3.9 compatibility; available validation interpreter Python 3.12.14 arm64. The script uses the standard library and the current interpreter.
**Primary Dependencies**: Existing pytest 8.4.2, Pydantic 2.14.1, PyYAML and Matplotlib 3.11.2/NumPy 2.5.4; installed Terraform 1.16.6 and PowerShell 7.5.11. Record observed versions rather than hardcoding claimed success.
**Storage**: Local evidence directory, logs, pytest JUnit XML and concise JSON summary. Existing temporary native fixtures and report directories only; no new runtime state store.
**Testing**: Full `tests/unit`, `tests/contract`, `tests/integration`; focused script guard/report tests; provider formatting/validation plus existing mocked plan suites.
**Target Platform**: Current Darwin/arm64 controller for offline fake/static proof; Linux guest process behavior remains explicitly separate.
**Project Type**: Small developer validation script and existing test integration; the benchmark CLI stays unchanged.
**Performance Goals**: Fake clocks simulate 86,400 seconds without waiting a day; no arbitrary timing target or new performance campaign.
**Constraints**: Clean final code revision; no cloud credentials/API/registry/traffic; filesystem-only providers, local tool prerequisites, secret-free metadata, explicit not-run evidence, no containers/VM workarounds.
**Scale/Scope**: Four modes × AWS/Azure/GCP; eleven failure categories; six Terraform roots; one complete local command. Do not duplicate existing coverage.

## Constitution Check

| Principle | Pre-design | Post-design |
| --- | --- | --- |
| I Traceability | Pass: source revisions, native/config/schedule/identity and analysis provenance required. | Pass: integrated saved F09 inputs feed F12; source hashes checked. |
| II Small architecture | Pass: validation only; no new platform/runtime. | Pass: one script, focused guard tests and one existing integration-test extension. |
| III Immutable/honest evidence | Pass: statuses, missing costs and raw immutability retained. | Pass: exact named macOS gaps remain not run; no synthetic research claims. |
| IV Provider explicitness | Pass: each provider/root and expiry semantics retained. | Pass: six-root inventory and actual mock suites; absent AWS mock suite not invented. |
| V Offline/cleanup safety | Pass: complete offline suite and separate paid handoff. | Pass: no init/download/apply/live plan/SSH/traffic during gate; actual fake failures and native limitations explicit. |

No violations, exemptions or research changes. Agent artifact checks replace routine human pauses under the authorized workflow.

## Project Structure

### Documentation (this feature)

`specs/014-v2-offline-gate/`: spec, plan, research, data model, quickstart, tasks, contracts/offline-gate.md, contracts/coverage.md and reviewed requirements/offline-boundary checklists.

### Owned implementation paths

- `scripts/validate_offline.py`: local developer command, plain functions, standard-library command execution/JUnit parsing/evidence output.
- `tests/unit/test_offline_gate.py`: meaningful injected preflight/command/report/failure tests for the gate; no recursive real full-suite run.
- `tests/integration/test_f09_multi_provider_factory.py`: only the successful four-mode tail plus a small local helper if needed, reusing all existing factories/responders/native sources.
- `docs/validation/f10-offline-gate.md`: final command, exact revision/environment/results/log references and limitations.
- `docs/paid-acceptance.md`: separate source-backed future acceptance runbook.
- `specs/014-v2-offline-gate/`: implementation task/evidence completion markers.

Read/reuse `tests/f12_support.py`, `tests/integration/test_analysis_replay.py`, existing F09/F08/measurement/admission/provider tests, `src/cloud_network_benchmark/{analysis,analysis_report,analysis_plots,lifecycle_projection}.py`, all six Terraform roots and committed lock files. Their application behavior and shared schemas are not owned for change. Surface an actual uncovered application defect to the coordinator before expanding ownership. Never change Thesis or coordinator state. There is one writer at a time.

## Design and execution sequence

1. **Preflight/evidence**: Require a Git checkout with clean tracked and unignored untracked state; resolve HEAD and tree, record safe allowlisted environment/tool/source versions, refuse an existing output directory or an output location that could overwrite source. Local ignored evidence/cache directories are allowed. Require the interpreter packages, Terraform, PowerShell and initialized locked providers; no installer fallback. Recheck source status/HEAD/tree at the end even on failure where possible.
2. **Offline tool boundary**: Generate a private Terraform CLI configuration with only a caller-prepared filesystem provider mirror and checkpoints disabled, set `TF_CLI_CONFIG_FILE`, `CHECKPOINT_DISABLE=1` and `TF_IN_AUTOMATION=1`; prohibit direct network installation fallback. Validate installed providers against committed locks. No Terraform init is part of the command. Provider/tool preparation is documented separately. Run only fixed argv commands; do not evaluate shell command strings or dump environment/credentials.
3. **Required checks**: Run the entire pytest suite once with `--junitxml`, then canonical schema `--check`, `git diff --check`, six-root format/validate and the four existing Azure/GCP mock-plan files. Preserve raw local logs and every command exit/duration. No live Terraform plan/apply/destroy, no speculative AWS mock tests. Failures may stop dependent checks; remaining checks must be not run and overall incomplete. Capture missing/malformed/empty JUnit and failed command evidence.
4. **Coverage/skip evaluation**: Match required named tests/parameterized mode IDs in JUnit (normalize class/name to test identity). Missing/deselected categories, unexpected skips and report errors fail the gate even if pytest exits zero. Allow only the two exact `/proc` tests on Darwin with their actual unavailable-`/proc` reason; keep them as not run in `platform_gaps`, never as passed. Both must execute on a future Linux acceptance host. Known gaps do not excuse any fake/static/source-patch safeguard.
5. **Integrated replay**: In the existing successful default-factory four-mode branch, construct an F12 selection from the exact persisted `validation/input.json` and saved validation results after finalization. Analyze with a stable recorded analysis revision. Assert all three run/provider/source identities, correct mode/deployment context, calibration comparison limits, DAY metric rules and unknown billing. Write two report trees from identical/reordered selection and compare file sets/bytes, including CSV/JSON/HTML/PNG/SVG; hash all saved/native input files before/after. Reuse existing F12 functions and actual F09 outputs; do not regenerate replacement evidence.
6. **Final proof/handoff**: Commit implementation first, then run the one full command on clean sources at that commit. Coordinator integrates any intervening prerequisite changes and reruns at final integrated code HEAD. Document exact results/logs/revision and all gaps; docs-only follow-up must prove executable/test/config/Terraform content unchanged. Any code/test/config/infrastructure edit after a full run requires another full run. Convergence follows the implementation stage; no extra independent review stage.

## Acceptance checks

The contracts define evidence/exit behavior and named coverage. Focused guard tests inject dirty/revised trees, missing tools/caches/scenarios, command failures/interruption, malformed JUnit, exact allowed and unexpected skips, existing output and secret-like environment values. Integrated replay extends existing default factory tests. The full clean command includes both these checks and all historical relevant offline suites; no baseline count is hardcoded. `docs/paid-acceptance.md` must be checked against source registration IDs and actual CLI help without invoking a paid action.

## Complexity Tracking

No constitution violations. Keep the script and tests ordinary functions; no plugin system, scheduler, new service, generic check framework or new manifest version.
