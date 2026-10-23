# F09 planning handoff

**Stage/model**: Artifact-only specify →clarify →plan →tasks →checklist →read-only analyze; Sol High.
**Worktree/branch**: `/private/tmp/cnb-f09`, `codex/f09-v2-lifecycle`.
**Verified base**: `527451d8697917c795d8dc9224cf8a17947c4311` (required F02R/F03–F08/F11 merged).
**Scope**: Only this013 feature directory changed; application code, checked-out constitution, Thesis and coordinator/runtime state untouched. Local ignored Spec Kit feature path is per-worktree tooling only.

## Artifact gates

| Gate | Actual result |
| --- | --- |
| Actual applicable skill/source reads | Completed; current AGENTS/constitution/WORKFLOW + canonical design/decisions/notes/experiments/F09 prompt/M04/dependency roadmap and merged interfaces |
| Specify template resolution/copy + specification review | Exit0;4 stories,19 functional requirements,6 success criteria; built-in checklist16/16 |
| Clarify prerequisite script `--json --paths-only` | Exit0; correct absolute feature paths;10 taxonomy categories Clear;0 questions;ordinary engineering resolutions recorded |
| Plan setup `setup-plan.sh --json` | Exit0; plan/research/data-model/contracts/quickstart generated; pre/post constitution gates pass |
| Tasks setup `setup-tasks.sh --json` | Exit0;29 sequential unchecked implementation tasks |
| Custom checklist prerequisite/template | Exit0;24 requirements-quality items generated unchecked, then separate authorized reviewer pass24/24 satisfied;100% referenced |
| Analyze prerequisites `--json --require-spec --require-tasks --include-tasks` | Exit0; all required artifacts available |
| Final read-only semantic/coverage audit | Exit0;25/25 buildable requirements covered,29/29 tasks mapped,0 actionable findings/ambiguities/duplications/constitution conflicts |
| Feature-local links/IDs/checkbox audit | Exit0; links resolve, unique continuous T001–T029, all implementation tasks unchecked |
| `git diff --check` | Exit0 before staging; initial staged check found Markdown hard-break trailing spaces, stripped before final staged recheck |

The first ad-hoc read-only coverage parser omitted slash shorthand (`FR-018/019`) and failed its assertion; corrected parser expands slash and ranges and passed without artifact changes. This was an audit-script issue, not a requirement gap.

## Coverage and task counts

All FR-001–019 and SC-001–006 have explicit task references; no unmapped task. Counts:setup1,foundational3,US1 seven,US2 five,US4 five,US3 three,final integration/documentation/gates five =29. [tasks.md](tasks.md) is the detailed requirement/task mapping. Execution sequence:setup/foundations →protected observation →verified bundle →failure/recovery →retained protocols →strict production replay/serialized CLI/full regression. The P1 failure story precedes the P2 retained story.

## Interfaces and ownership

Implementation owns only declared integration files in [plan.md](plan.md):orchestration.py, lifecycle_adapters.py, lifecycle_guest.py, lifecycle_bundle.py, lifecycle_cli.py; narrow manifests.py existing-field atomic helpers; F09-only tests/fixtures/docs/README. Shared DTOs/schemas, provider modules/Terraform, native helpers/F08 fixtures, F12 analysis/report modules and Thesis remain outside ownership. A final thin cli.py hook is explicitly serialized after coordinator confirms F12 CLI release/latest-main integration. No shared DTO/schema amendment is justified; any newly demonstrated shared issue must return to coordinator before edits.

Critical interfaces use actual components:BudgetLedger reserve/check_stage_fit/claim_creation/reconcile and bounded_copy_handoff; AWS/GCP drivers and Azure register/provision/destroy; existing access prepare/readiness/retrieve; A-local run_measurement and standalone diagnostics_guest on both roles; post-retrieval bind_acquisition_sources/iter_source_windows; strict saved ValidatorInput/validate_bundle/evaluate_comparison/select_common_n.

## Ordinary engineering assumptions resolved

- Reuse original F11 cap activation/all remaining-work fit, intermediate skew band and at-most-one failed-artifact retry minus cleanup allowance.
- Complete selected-cloud protection precedes any VM create. After protection, healthy peers can continue after a provision/readiness failure only if their full remainders fit.
- Stage the same package revision/digest with existing Python dependencies on A and run F03 there. Distinct controller/guest monotonic clocks must not be confused; unsupported timing remains unavailable.
- Bootstrap-captured original build sidecars may be controller-materialized once; distinguish those from real remote inventory and deduplicate identical A copies. Supplemental declarations identify exact unit logs/builds; no relaxed model or fabricated proof.
- Native/diagnostic/receiver outputs close before final both-VM copy/verify; normal deletion always attempted on reachable terminal failures. Abrupt-loss tests intentionally have no finalizer and do not prove real expiry execution.
- Execution/data/eligibility/cleanup remain separate; existing parent execution aggregate semantics stay intact, and overall lifecycle headline cannot claim success over required incomplete data or cleanup.
- Restart allows explicit cleanup/reconciliation only, never uncertain creation/measurement replay. No automatic campaign retry, disk recovery, periodic copies, new platform or research choices.

## Exact implementation gates and next stage

[quickstart.md](quickstart.md) lists exact focused pytest commands, full pytest, shared-schema `--check`, CLI help/dispatch and diff checks. The required production fixture path is ArtifactLayout + actual supplemental declarations →closed inventory →existing retrieval with fake transport →verified acquisition helper →matching terminal manifests →strict saved ValidatorInput roundtrip →F08 result/seal for every mode. Full provider×mode and stage/loss/cleanup/recovery matrix is required.

**Not run**: F09 application/pytest/guest installation/native Linux/Terraform/cloud/paid acceptance checks. Planning creates no executable readiness evidence. No dependency changes are needed. Coordinator integrates latest main before Luna implementation and serializes final CLI hook after F12; after implementation require current-head gates and separate Spec Kit convergence with zero actionable findings. F10 remains a later integrated offline gate; actual cloud/native/expiry acceptance is separate and unproved.
