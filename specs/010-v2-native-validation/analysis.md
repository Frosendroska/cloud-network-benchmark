# Specification Analysis Report: F08

Date: 2026-10-21. Scope: spec.md, plan.md, tasks.md, related design/contracts, constitution 2.0.0. The analyze pass was read-only; this durable report and the subsequent fixes are recorded under the coordinator-authorized artifact workflow. No application code was changed. A later coordinator timing refinement was integrated and reanalyzed: coverage can use a conservative command/native-evidence bracket while exact UTC matching stays unavailable without actual starts.

## Findings and remediation

| ID | Category | Severity | Location | Initial finding | Resolution |
| --- | --- | --- | --- | --- | --- |
| I1 | Interface consistency | HIGH | Contract Signatures; tasks T016 | Comparison returned status only, losing required machine-readable degraded-skew findings. | Return copied existing ValidationResult values keyed by run ID, retaining metrics and adding comparison findings/status. No shared DTO change. |
| I2 | Task ordering | MEDIUM | tasks T010/T011; Parallel Opportunities | Validator materialization tests depend on new fixture inventory but both had parallel markers. | Remove both markers; declare fixture-before-test sequence. |
| I3 | Source traceability | MEDIUM | Contract parse_sockperf; data-model native source records | CSV and separate native log could share one source reference, losing log count provenance. | Add optional log_source; supplied log without its own source evidence cannot provide trusted log counters. |

Read-only reanalysis after explicit scoped remediation: zero outstanding CRITICAL/HIGH/MEDIUM/LOW findings. No duplication or ambiguous threshold remains. Required fixed protocol settings are cross-checked against saved BenchmarkPlan; no unmerged sibling/sidecar or new shared contract is required.

## Coverage Summary

| Requirement | Has task? | Task IDs |
| --- | --- | --- |
| FR-001 | Yes | T002–T008 |
| FR-002 | Yes | T006/T008/T009/T023 |
| FR-003 | Yes | T006/T009/T021/T023 |
| FR-004 | Yes | T005–T007/T014/T017 |
| FR-005 | Yes | T010–T012/T024 |
| FR-006 | Yes | T011/T013/T017 |
| FR-007 | Yes | T011/T014/T017/T023 |
| FR-008 | Yes | T011/T015/T019/T022 |
| FR-009 | Yes | T016/T017/T023 |
| FR-010 | Yes | T018/T020/T021 |
| FR-011 | Yes | T019/T022–T024 |
| FR-012 | Yes | T001/T006/T011/T015/T019/T023/T025/T026 |
| SC-001 | Yes | T005–T009/T023 |
| SC-002 | Yes | T010–T015/T023 |
| SC-003 | Yes | T014/T016/T017 |
| SC-004 | Yes | T018/T020/T021 |
| SC-005 | Yes | T019/T022–T025 |

Constitution alignment issues: none. Unmapped tasks: none; T003/T004 establish the parser/public model prerequisite, all other setup/final tasks enforce FR-012 or their mapped stories. Read-only source review found explicit supersessions rather than an unresolved research conflict. Calibration N and UTC windows remain pending by design.

Metrics: 12 functional requirements, 5 buildable success criteria, 26 tasks, 100% mapped requirement coverage, 0 outstanding ambiguities, 0 duplicated requirements, 0 critical issues. Three user stories have independent fixture checks; US2 consumes US1 primitives and the integration replay waits for all stories. No application tasks are checked complete.

## Artifact gates and actual commands

All commands ran in `/private/tmp/cnb-f08` with explicit `GIT_BRANCH_NAME=codex/f08-v2-validation SPECIFY_FEATURE_DIRECTORY=specs/010-v2-native-validation` on Spec Kit helpers.

| Command | Actual result |
| --- | --- |
| `.specify/scripts/bash/resolve-template.sh spec-template --json` | Exit 0, resolved installed template. |
| `.specify/scripts/bash/resolve-template.sh spec-template` redirected to spec.md before authoring | Exit 0, created starting structure. |
| `.specify/scripts/bash/check-prerequisites.sh --json --paths-only` | Exit 0, assigned feature paths. |
| `.specify/scripts/bash/setup-plan.sh --json` | Exit 0, plan template copied. |
| `.specify/scripts/bash/check-prerequisites.sh --json --template checklist-template` | Exit 0, checklist template/context resolved. |
| `.specify/scripts/bash/setup-tasks.sh --json` | Exit 0, tasks template/design artifacts resolved. |
| `.specify/scripts/bash/check-prerequisites.sh --json --require-spec --require-tasks --include-tasks` | Exit 0, all required feature artifacts found. |
| `/private/tmp/cnb-offline-venv/bin/python /private/tmp/cnb-f08/.codex/autonomous-evidence/f08-planning-check.py` | Exit 0: 26 sequential valid task IDs/paths, 17/17 coverage, links/fences/placeholders and quality checklists passed. |
| `git diff --check` | Exit 0 (planning changes initially untracked; staged check also required before commit). |
| `git add specs/010-v2-native-validation` | Initial sandbox call failed to create the linked-worktree index.lock (Operation not permitted); the targeted escalated retry exited 0. No artifact work was lost. |
| `git diff --cached --check` | Exit 0 after staging only assigned spec artifacts. |

Specify/clarify quality checklist: 16/16 passing, no regressions, zero questions. Custom checklist generation produced 16 unchecked requirements-quality items; separate authorized review subsequently passed 16/16. No extension file/hooks exist. Helpers label BRANCH from spec basename; actual `git branch --show-current` verified `codex/f08-v2-validation`. All six actual skills were read before execution. No unresolved technical research agents were needed.

Application tests, native execution, F08 implementation checks, Terraform/cloud checks, F10 suite and paid acceptance: **not run at this planning stage**. Existing base tests are not claimed as evidence of F08 behavior. Upstream parser source review establishes grammar requirements, not real runtime capability.

## Next action

Proceed to `$speckit-implement` for the 26 unchecked tasks in the assigned worktree, using the documented F01R-only public boundary. Then run focused/full offline checks and `$speckit-converge`. Coordinator owns PR/merge/integration scheduling. No routine approval or research decision is needed.
