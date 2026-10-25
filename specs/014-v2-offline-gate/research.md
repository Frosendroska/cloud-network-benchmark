# F10 decisions and source findings

## Sources and base

Read implementation AGENTS.md, constitution 2.0.0 and `docs/autonomous/WORKFLOW.md`; actual specify/clarify/plan/tasks/analyze/checklist skills and resolved templates. Read canonical Thesis design, DECISIONS, FRAMEWORK_IMPLEMENTATION_NOTES, EXPERIMENTS, IMPLEMENTATION_PROMPTS, M04, roadmap and M05 at research-context revision `a06fe47f748b6918770505aac63d45eb5acac6d6`. Coordinator subsequently reported progress-only update `ce89a8549bafe95b7fe989784f3b37e8f65b9bbf`; research inputs unchanged. No source conflict found. Dated supersessions preserve concurrent 28-slot P0/P1, fixed protocol, deferred P2/P3 and paid separation.

Implementation base is verified merged F09/F12 `eb0be0b40d2b38607212cad3761f454295122f9b`. Coordinator allocated `/private/tmp/cnb-f10`, branch `codex/f10-v2-offline-gate`, spec 014. This is an independent clean clone after the original linked-checkout object read timed out; it preserves the exact merged base and single-writer isolation. No Git history rewrite or alternate feature is needed.

## D1 — Compose existing offline proof

**Decision**: One standard-library validation script runs full pytest, schema drift and actual Terraform checks; use existing fake/default factories and F12 functions.
**Rationale**: F09 already proves default AWS/Azure/GCP factories for all four modes plus interruption/protection/receipt recovery. F12 already proves native fixtures and deterministic report bytes. F10 needs reproducible integration/evidence, not another orchestrator.
**Alternatives considered**: Copying factories, a new test platform, a generic registry/plugin system, paid runs. Rejected as duplication or outside scope.

## D2 — Connect actual lifecycle outputs to analysis

**Decision**: Extend the existing successful four-mode test tail to feed its persisted F09 validator input/results into F12, preserving hashes and comparing two generated report trees.
**Rationale**: Existing F09 validates/seals actual produced bundles; existing F12 replay primarily uses F08 materializers. This narrow bridge closes the final end-to-end report gap without application changes.
**Alternatives considered**: Separate synthetic F10 fixture engine or only pointing to independent suites; the former duplicates code and the latter leaves the bridge unproved.

## D3 — Exact native-platform limitations

**Decision**: Allow only `test_f03_scoped_stop_reaps_owned_session_when_original_leader_has_exited` and `test_f03_scoped_stop_refuses_reused_pid_start_ticks` from `tests/unit/test_f09_guest.py` to remain not run on Darwin for unavailable Linux `/proc`; record exact identities/reasons in separate platform gaps. Any other required skip/missing scenario makes the gate incomplete/nonzero.
**Rationale**: Coordinator explicitly resolved this evidence boundary. Full fake/static/source-bound proof is required; macOS cannot establish Linux process identity consequences. Existing source-patch application/bootstrap tests remain required.
**Alternatives considered**: Relabeling skips passed, blanket skip allowance, containers/VM/cloud execution. Rejected; future authorized native Linux acceptance must execute these checks and instrumented source-built tools.

## D4 — Tools prepared separately, no network fallback

**Decision**: Require local tool/dependency/provider setup, initialized six roots and exact committed provider locks; configure Terraform with a filesystem-only mirror and checkpoints disabled. The gate never installs, initializes, downloads, calls a cloud CLI or runs a real plan/apply/destroy.
**Rationale**: Clean checkout concerns source state; ignored provider caches can exist. Terraform schema/validate processes are local static checks. Missing prerequisites are a recorded incomplete run rather than covert network access.
**Alternatives considered**: Automatic registry initialization, credential discovery and cloud tests. Rejected by offline boundary. Provider preparation may require separately authorized public registry/tool downloads outside the gate.

## D5 — Evidence is an ordinary local artifact

**Decision**: Save exact command argv/exits/durations/logs/hashes, safe versions/host/revisions, JUnit counts, required coverage and platform gaps in one caller-selected new local directory. No schema migration or shared coordinator-state writes.
**Rationale**: Enough provenance for reproduction without exporting secrets/environment or introducing another persistence framework. Refuse overwriting existing output and recheck sources after execution.
**Alternatives considered**: Hardcoded current counts, whole-environment dumps, replacing canonical manifests, tracking large raw results. Rejected as brittle, unsafe or unnecessary.

## D6 — Honest final revision

**Decision**: Full command runs only after code/test commit from a clean checkout; final integration requires exact final-code evidence. A later docs-only commit may cite the tested commit only with a path/tree comparison proving all executable/test/config/infrastructure content identical.
**Rationale**: Existing historical F09 result (1,445 passed, two Darwin gaps, one warning at code d035121) is dependency context and cannot substitute for F10.
**Alternatives considered**: Claiming the planning head or an earlier full suite tested later edits; rejected.

## D7 — Separate paid handoff

**Decision**: Runbook covers registered V-S1 and V-A/Z/G-FAIL, later V-S3/admitted V-S2/V-S4, twelve-trial CAL-S1, measured disk/export gate, DAY and evidence-dependent study windows. It records required refresh/permissions/cleanup and leaves no automatic paid next action.
**Rationale**: Research authority explicitly ends implementation at offline F10. N/windows/prices/real expiry/native-tool behavior remain evidence-dependent.
**Alternatives considered**: Inventing real N/windows/current prices or silently starting acceptance; rejected.

All design questions resolved within existing sources. No external research, new research choice or research-agent dispatch is necessary.
